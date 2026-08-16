"""Title normalization for cross-release track matching.

Known trade-off: 'ft.' as a trailing credit marker means a title that
legitimately ends in 'ft. <something>' ("Live from Ft. Worth") still gets
truncated. Bare 'with' is deliberately NOT a trailing credit marker (only the
parenthetical '(with X)' form is) so titles like 'Home with You' survive.
"""

import re
import unicodedata

# MusicBrainz mixes curly/straight punctuation between releases of the same
# recording (single vs album), so both sides must fold to one form.
_PUNCT_FOLD = {
    0x2018: "'",
    0x2019: "'",
    0x201B: "'",
    0x201C: '"',
    0x201D: '"',
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",
    0x2014: "-",
    0x2015: "-",
    0x2212: "-",
}

_FEAT_PAREN = re.compile(
    r"\s*[(\[](?:feat\.?|featuring|ft\.?|with)\s+[^()\[\]]*[)\]]", re.IGNORECASE
)
# Remainder must not contain parens so a "feat." inside a parenthetical
# ("(Live feat. the Orchestra)") never truncates the title mid-parenthesis.
# Bare 'with' is excluded here (see module docstring): it appears in ordinary
# titles ("Home with You") far more often than as an uncredited feature.
_FEAT_TRAILING = re.compile(
    r"\s+(?:feat\.?|featuring|ft\.?)\s+[^()\[\]]*$", re.IGNORECASE
)

_TRAILING_PAREN = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]\s*$")

# Only these parenthetical suffixes are packaging noise. Anything else —
# "(2024 mix)", "(live at ...)", "(Chromeo remix)", "(Extended Version)",
# "(DONK)" — can denote an exclusive recording; stripping it causes wrongful
# skips.
_SUFFIX_WHITELIST = re.compile(
    r"^(?:"
    r"(?:19|20)\d\d remaster(?:ed)?"
    r"|remaster(?:ed)?(?: (?:19|20)\d\d)?"
    r"|mono"
    r"|stereo"
    r"|single version"
    r"|album version"
    r"|radio edit"
    r"|explicit"
    r"|clean"
    r"|deluxe(?: edition)?"
    r"|bonus track"
    r")$",
    re.IGNORECASE,
)


def fold_unicode(text):
    text = text.translate(_PUNCT_FOLD)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def strip_release_suffixes(title):
    # Suffixes stack ("(Extended Version) (2026 Remaster)"), so peel trailing
    # whitelisted layers until the last parenthetical is not whitelisted.
    while True:
        match = _TRAILING_PAREN.search(title)
        if not match or match.start() == 0:
            return title
        if not _SUFFIX_WHITELIST.match(match.group(1).strip()):
            return title
        title = title[: match.start()].rstrip()


def remove_feat(title):
    title = _FEAT_PAREN.sub("", title)
    title = _FEAT_TRAILING.sub("", title)
    return title.strip()


def normalize_title(title):
    # Suffixes strip before feat removal: a trailing whitelisted parenthetical
    # ("Song feat. Artist (2011 Remaster)") would otherwise block
    # _FEAT_TRAILING's paren-free-remainder guard.
    text = fold_unicode(title).casefold()
    text = strip_release_suffixes(text)
    text = remove_feat(text)
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())
