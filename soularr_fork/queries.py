"""Album search queries: artist + title first, bare title as the fallback.

A Soulseek search matches when every query word appears somewhere in a
peer's file path, and slskd closes the search at `responseLimit` PEERS, first
come first served. A bare album title ("Icon", "Gravity", "Greatest Hits")
fills that cap with other artists' folders: measured on 2026-09-28, the
median capped search had 5 of 100 responders holding the wanted artist at
all, while artist + title found FLAC sources for 12 of 12 albums the bare
title had missed.

The ladder therefore leads with artist + title and only widens to the bare
title when the narrower query finds nobody (artist spelled differently in
shares, phrases the Soulseek server refuses to search).
"""

import re
from dataclasses import dataclass

from .nameparse import sanitize_search_query
from .normalize import normalize_title

VARIOUS_ARTISTS_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"
_VARIOUS_NAMES = {"various artists", "various", "va"}

SCOPE_ARTIST = "artist+title"
SCOPE_TITLE = "title"


@dataclass(frozen=True)
class Query:
    text: str  # what is sent to slskd
    scope: str  # SCOPE_ARTIST or SCOPE_TITLE
    sanitized: bool  # True when text is the punctuation-stripped form of raw
    raw: str  # blacklist-filtered query before sanitizing
    source: str = ""  # query before the search_blacklist was applied


def is_various_artists(artist):
    """True for Lidarr's Various Artists entry (an artist dict or a bare name)."""
    if isinstance(artist, dict):
        if artist.get("foreignArtistId") == VARIOUS_ARTISTS_MBID:
            return True
        artist = artist.get("artistName")
    return (artist or "").strip().casefold() in _VARIOUS_NAMES


def title_has_artist(artist_name, album_title):
    """True when the title already spells the artist out ("The Best of Toby Keith")."""
    artist = normalize_title(artist_name or "")
    if not artist:
        return False
    return f" {artist} " in f" {normalize_title(album_title or '')} "


def apply_blacklist(query, blacklist):
    for word in blacklist or ():
        if word:
            query = re.compile(re.escape(word), re.IGNORECASE).sub("", query)
    return " ".join(query.split())


def build_query_ladder(artist_name, album_title, *, prepend_artist=False, title_fallback=False, sanitize=False, blacklist=(), various=False):
    """
    Ordered, de-duplicated queries to try until one returns responses.

    With every option off this is upstream's single bare-title query (artist
    + title for one-character titles). Each scope contributes its sanitized
    form first and its raw form second when `sanitize` is on.
    """
    artist_name = (artist_name or "").strip()
    album_title = (album_title or "").strip()
    one_char = len(album_title) == 1
    # Naming the artist twice, or naming "Various Artists", only narrows the match
    artist_helps = bool(artist_name) and not various and not title_has_artist(artist_name, album_title)

    scoped = []
    if one_char and artist_name:
        scoped.append((SCOPE_ARTIST, artist_name + " " + album_title))
    elif prepend_artist and artist_helps:
        scoped.append((SCOPE_ARTIST, artist_name + " " + album_title))
        if title_fallback:
            scoped.append((SCOPE_TITLE, album_title))
    else:
        scoped.append((SCOPE_TITLE, album_title))

    ladder = []
    seen = set()
    for scope, text in scoped:
        raw = apply_blacklist(text, blacklist)
        forms = []
        if sanitize:
            forms.append((sanitize_search_query(raw), True))
        forms.append((raw, False))
        for form, sanitized in forms:
            if not form or form.casefold() in seen:
                continue
            seen.add(form.casefold())
            ladder.append(Query(text=form, scope=scope, sanitized=sanitized and form != raw, raw=raw, source=text))
    return ladder


def run_ladder(ladder, search, *, pause=None, log=None):
    """
    Runs `search(text)` down the ladder until one query returns responses.

    `search` returns a list of responses, or None when the search failed.
    `pause()` is called between attempts (search rate limiting). Returns
    (responses, query) for the first hit, or (None, None) when every rung
    came back empty or failed.
    """
    previous = None
    for query in ladder:
        if previous is None:
            if log:
                if query.source and query.raw != query.source:
                    log(f"Filtered search query: '{query.source}' -> '{query.raw}'")
                if query.text != query.raw:
                    log(f"Sanitized search query: '{query.raw}' -> '{query.text}'")
                log(f"Searching for album: {query.text}")
        else:
            if log:
                log(f"No results for '{previous.text}'. Retrying with {describe(query)}: {query.text}")
            if pause:
                pause()
        responses = search(query.text)
        if responses:
            return responses, query
        previous = query
    return None, None


def describe(query):
    form = "sanitized" if query.sanitized else "raw"
    scope = "artist + title" if query.scope == SCOPE_ARTIST else "title-only"
    return f"{form} {scope} query"


def positive_limit(raw, default, key, logger=None):
    """A search cap from config.ini: blank means the default, a non-positive or non-numeric value warns and falls back."""
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        if logger:
            logger.warning(f"[Search Settings] {key} '{raw}' is not a positive integer - using {default}")
        return default
    return value


def poll_deadline(search_timeout_ms, grace=60):
    """
    Seconds to keep polling slskd for a search to close. search_timeout is
    slskd's idle timer in milliseconds (it restarts on every response), so a
    live search can outlast it; past the grace period slskd has lost the search.
    """
    return max(search_timeout_ms, 0) / 1000 + grace
