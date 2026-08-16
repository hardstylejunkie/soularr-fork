"""Album folder-name parsing ported from Lidarr (stdlib re only).

Sources: NzbDrone.Core/Parser/Parser.cs (ReportAlbumTitleRegex, ParseAlbumTitle,
ParseAlbumMatchCollection, ParseReleaseGroup) and QualityParser.cs (CodecRegex,
BitRateRegex, SampleSizeRegex). Pattern order is first-match-wins and mirrors
Lidarr exactly -- do not reorder.

Two pre-normalization rules beyond Lidarr (the stock patterns stay verbatim):
a leading '(YYYY) ' prefix is stripped and captured as the year, and {...}
brace tags are stripped before pattern matching (quality regexes still see the
raw name, so '{24-96}' feeds SampleSizeRegex).

A version token captured by the scene A-B-Ver-Src-Y pattern (e.g. 'EP',
'Deluxe Edition') is excluded from `album` and dropped: ParsedFolder carries
no version field.
"""

import re
from dataclasses import dataclass
from typing import Optional

from .normalize import fold_unicode


@dataclass
class ParsedFolder:
    artist: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    source: Optional[str] = None
    codec: Optional[str] = None
    bit_depth: Optional[int] = None
    bitrate: Optional[str] = None
    release_group: Optional[str] = None
    pattern: str = ""


# --- Parser.cs cleanup regexes ---------------------------------------------
_SIMPLE_TITLE = re.compile(
    r"(?:(480|720|1080|2160|320)[ip]|[xh][\W_]?26[45]|DD\W?5\W1|[<>*|]|848x480|"
    r"1280x720|1920x1080|3840x2160|4096x2160|(8|10)b(it)?)\s*", re.IGNORECASE)
_WEBSITE_PREFIX = re.compile(
    r"^(?:\[\s*)?(?:www\.)?[-a-z0-9-]{1,256}\.(?:[a-z]{2,6}\.[a-z]{2,6}|"
    r"xn--[a-z0-9-]{4,}|[a-z]{2,})\b(?:\s*\]|[ -]{2,})[ -]*", re.IGNORECASE)
_WEBSITE_POSTFIX = re.compile(
    r"(?:\[\s*)?(?:www\.)?[-a-z0-9-]{1,256}\.(?:xn--[a-z0-9-]{4,}|[a-z]{2,6})\b(?:\s*\])$",
    re.IGNORECASE)
_CLEAN_TORRENT_SUFFIX = re.compile(r"\[(?:ettv|rartv|rarbg|cttv)\]$", re.IGNORECASE)
_REQUEST_INFO = re.compile(r"\[.+?\]")

# Fork pre-normalization (see module docstring).
_LEADING_YEAR_PREFIX = re.compile(r"^\((?P<year>\d{4})\)\s*")
_BRACE_TAG = re.compile(r"\{[^{}]*\}")

# --- ReportAlbumTitleRegex, Parser.cs order (first-match-wins) --------------
_ALBUM_PATTERNS = [
    # ruTracker - (Genre) [Source]? Artist - Discography
    (r"^(?:\(.+?\))(?:\W*(?:\[(?P<source>.+?)\]))?\W*(?P<artist>.+?)(?: - )(?P<discography>Discography|Discografia).+?(?P<startyear>\d{4}).+?(?P<endyear>\d{4})",
     "ruTracker discography"),
    # Artist - Discography with two years
    (r"^(?P<artist>.+?)(?: - )(?:.+?)?(?P<discography>Discography|Discografia).+?(?P<startyear>\d{4}).+?(?P<endyear>\d{4})",
     "discography 2yr"),
    # Artist - Discography with end year
    (r"^(?P<artist>.+?)(?: - )(?:.+?)?(?P<discography>Discography|Discografia).+?(?P<endyear>\d{4})",
     "discography endyr"),
    # Artist Discography with two years
    (r"^(?P<artist>.+?)\W*(?P<discography>Discography|Discografia).+?(?P<startyear>\d{4}).+?(?P<endyear>\d{4})",
     "discography nospace 2yr"),
    # Artist Discography with end year
    (r"^(?P<artist>.+?)\W*(?P<discography>Discography|Discografia).+?(?P<endyear>\d{4})",
     "discography nospace endyr"),
    # Artist Discography
    (r"^(?P<artist>.+?)\W*(?P<discography>Discography|Discografia)",
     "discography bare"),
    # ruTracker - (Genre) [Source]? Artist - Album - Year
    (r"^(?:\(.+?\))(?:\W*(?:\[(?P<source>.+?)\]))?\W*(?P<artist>.+?)(?: - )(?P<album>.+?)(?: - )(?P<releaseyear>\d{4})",
     "ruTracker album"),
    # Artist-Album-Version-Source-Year (Imagine Dragons-Smoke And Mirrors-Deluxe Edition-2CD-FLAC-2015-JLM)
    (r"^(?P<artist>.+?)[-](?P<album>.+?)[-](?:[\(|\[]?)(?P<version>.+?(?:Edition)?)(?:[\)|\]]?)[-](?P<source>\d?CD|WEB).+?(?P<releaseyear>\d{4})",
     "scene A-B-Ver-Src-Y"),
    # Artist-Album-Source-Year (Dani_Sbert-Togheter-WEB-2017-FURY)
    (r"^(?P<artist>.+?)[-](?P<album>.+?)[-](?P<source>\d?CD|WEB).+?(?P<releaseyear>\d{4})",
     "scene A-B-Src-Y"),
    # Artist - Album (Year) Strict
    (r"^(?:(?P<artist>.+?)(?: - )+)(?P<album>.+?)\W*(?:\(|\[).+?(?P<releaseyear>\d{4})",
     "A - B (Y) strict"),
    # Artist - Album (Year)
    (r"^(?:(?P<artist>.+?)(?: - )+)(?P<album>.+?)\W*(?:\(|\[)(?P<releaseyear>\d{4})",
     "A - B (Y)"),
    # Artist - Album - Year [something]
    (r"^(?:(?P<artist>.+?)(?: - )+)(?P<album>.+?)\W*(?: - )(?P<releaseyear>\d{4})\W*(?:\(|\[)",
     "A - B - Y [x]"),
    # Artist - Album [something] or Artist - Album (something)
    (r"^(?:(?P<artist>.+?)(?: - )+)(?P<album>.+?)\W*(?:\(|\[)",
     "A - B [x]"),
    # Artist - Album Year
    (r"^(?:(?P<artist>.+?)(?: - )+)(?P<album>.+?)\W*(?P<releaseyear>\d{4})",
     "A - B Y"),
    # Artist-Album (Year) Strict
    (r"^(?:(?P<artist>.+?)(?:-)+)(?P<album>.+?)\W*(?:\(|\[).+?(?P<releaseyear>\d{4})",
     "A-B (Y) strict"),
    # Artist-Album (Year)
    (r"^(?:(?P<artist>.+?)(?:-)+)(?P<album>.+?)\W*(?:\(|\[)(?P<releaseyear>\d{4})",
     "A-B (Y)"),
    # Artist-Album [something] or Artist-Album (something)
    (r"^(?:(?P<artist>.+?)(?:-)+)(?P<album>.+?)\W*(?:\(|\[)",
     "A-B [x]"),
    # Artist-Album-something-Year
    (r"^(?:(?P<artist>.+?)(?:-)+)(?P<album>.+?)(?:-.+?)(?P<releaseyear>\d{4})",
     "A-B-x-Y"),
    # Artist-Album Year
    (r"^(?:(?P<artist>.+?)(?:-)+)(?:(?P<album>.+?)(?:-)+)(?P<releaseyear>\d{4})",
     "A-B Y"),
    # Artist - Year - Album
    (r"^(?:(?P<artist>.+?)(?:-))(?P<releaseyear>\d{4})(?:-)(?P<album>[^-]+)",
     "A-Y-B"),
]
_ALBUM_PATTERNS = [(re.compile(pattern, re.IGNORECASE), label)
                   for pattern, label in _ALBUM_PATTERNS]

_SCENE_SOURCE = re.compile(r"\d?CD|WEB", re.IGNORECASE)

# --- QualityParser.cs -------------------------------------------------------
_CODEC = re.compile(
    r"\b(?:(?P<MP1>MPEG Version \d(?:.5)? Audio, Layer 1|MP1)|"
    r"(?P<MP2>MPEG Version \d(?:.5)? Audio, Layer 2|MP2)|"
    r"(?P<MP3VBR>MP3.*VBR|MPEG Version \d(?:.5)? Audio, Layer 3 vbr)|"
    r"(?P<MP3CBR>MP3|MPEG Version \d(?:.5)? Audio, Layer 3)|"
    r"(?P<FLAC>(?:web)?flac(?:24(?:[-._ ]?bit)?)?|TR24)|"
    r"(?P<WAVPACK>wavpack|wv)|(?P<ALAC>alac)|(?P<WMA>WMA\d?)|(?P<WAV>WAV|PCM)|"
    r"(?P<AAC>M4A|M4P|M4B|AAC|mp4a|MPEG-4 Audio(?!.*alac))|"
    r"(?P<OGG>OGG|OGA|Vorbis))\b"
    r"|(?P<APE>monkey's audio|[\[|\(].*\bape\b.*[\]|\)])"
    r"|(?P<OPUS>Opus Version \d(?:.5)? Audio|[\[|\(].*\bopus\b.*[\]|\)])",
    re.IGNORECASE)

_CODEC_NAMES = {
    "MP1": "MP1", "MP2": "MP2", "MP3VBR": "MP3", "MP3CBR": "MP3",
    "FLAC": "FLAC", "WAVPACK": "WAVPACK", "ALAC": "ALAC", "WMA": "WMA",
    "WAV": "WAV", "AAC": "AAC", "OGG": "OGG", "APE": "APE", "OPUS": "OPUS",
}

# Bitrate junk ("320" year fragments, "{24-96}" sample rates) only means
# anything on lossy codecs, so lossless names skip the bitrate lookup.
_LOSSY_CODECS = {"MP1", "MP2", "MP3", "AAC", "OGG", "WMA", "OPUS"}

# re.VERBOSE mirrors RegexOptions.IgnorePatternWhitespace on the .NET original.
_BIT_RATE = re.compile(
    r"""\b(?:(?P<B096>96[ ]?kbps|96|[\[\(].*96.*[\]\)])|
            (?P<B128>128[ ]?kbps|128|[\[\(].*128.*[\]\)])|
            (?P<B160>160[ ]?kbps|160|[\[\(].*160.*[\]\)]|q5)|
            (?P<B192>192[ ]?kbps|192|[\[\(].*192.*[\]\)]|q6)|
            (?P<B224>224[ ]?kbps|224|[\[\(].*224.*[\]\)]|q7)|
            (?P<B256>256[ ]?kbps|256|itunes\splus|[\[\(].*256.*[\]\)]|q8)|
            (?P<B320>320[ ]?kbps|320|[\[\(].*320.*[\]\)]|q9)|
            (?P<B500>500[ ]?kbps|500|[\[\(].*500.*[\]\)]|q10)|
            (?P<VBRV0>V0[ ]?kbps|V0|[\[\(].*V0.*[\]\)])|
            (?P<VBRV2>V2[ ]?kbps|V2|[\[\(].*V2.*[\]\)]))\b""",
    re.IGNORECASE | re.VERBOSE)

# Case-sensitive upstream; only ever run against the lowercased name.
_SAMPLE_SIZE = re.compile(
    r"\b(?:(?P<S24>24[-._ ]?bit|flac24(?:[-._ ]?bit)?|tr24|24-(?:44|48|96|192)|[\[\(].*24bit.*[\]\)]))\b")

# --- ParseReleaseGroup ------------------------------------------------------
_CLEAN_RELEASE_GROUP = re.compile(
    r"^(.*?[-._ ])|(-(RP|1|NZBGeek|Obfuscated|Scrambled|sample|Pre|postbot|xpost|"
    r"Rakuv[a-z0-9]*|WhiteRev|BUYMORE|AsRequested|AlternativeToRequested|GEROV|"
    r"Z0iDS3N|Chamele0n|4P|4Planet|AlteZachen|RePACKPOST))+$", re.IGNORECASE)

# .NET original repeats the 'releasegroup' name across alternatives and uses a
# variable-length lookbehind (?<!.*?MP3|ALAC|FLAC|WEB); Python needs the group
# renamed and the lookbehind emulated with a prefix endswith check.
_RELEASE_GROUP = re.compile(
    r"-(?P<releasegroup>[a-z0-9]+(?!.+?(?:MP3|ALAC|FLAC|WEB)))(?:\b|[-._ ]|$)"
    r"|[-._ ]\[(?P<releasegroup2>[a-z0-9]+)\]$", re.IGNORECASE)

_RELEASE_GROUP_STOP_TOKENS = ("MP3", "ALAC", "FLAC", "WEB")


def _parse_release_group(title):
    text = _CLEAN_RELEASE_GROUP.sub("", title.strip())
    last = None
    for match in _RELEASE_GROUP.finditer(text):
        if match.group("releasegroup"):
            group = match.group("releasegroup")
            end = match.end("releasegroup")
        else:
            group = match.group("releasegroup2")
            end = match.end("releasegroup2")
        if text[:end].upper().endswith(_RELEASE_GROUP_STOP_TOKENS):
            continue
        last = group
    # Parser.cs rejects a purely numeric final match (year/bitrate fragments).
    if last is None or last.isdigit():
        return None
    return last


def _parse_quality(name):
    normalized = name.replace("_", " ").strip().lower()
    codec = None
    match = _CODEC.search(normalized)
    if match:
        codec = _CODEC_NAMES[next(k for k, v in match.groupdict().items() if v)]
    bit_depth = 24 if _SAMPLE_SIZE.search(normalized) else None
    bitrate = None
    if codec in _LOSSY_CODECS:
        match = _BIT_RATE.search(normalized)
        if match:
            group = next(k for k, v in match.groupdict().items() if v)
            bitrate = group[1:].lstrip("0") if group.startswith("B") else group[3:]
    return codec, bit_depth, bitrate


def _clean_captured(value):
    # ParseAlbumMatchCollection: cleanup happens AFTER matching, not in the
    # patterns ('.'/'_' -> space, strip [..] tags, trim).
    if not value:
        return None
    value = value.replace(".", " ").replace("_", " ")
    value = _REQUEST_INFO.sub("", value).strip(" ")
    return value or None


def _valid_year(text):
    if not text:
        return None
    year = int(text)
    return year if 1900 <= year <= 2100 else None


def parse_folder_name(name):
    """Parse a leaf album-folder name; returns None when nothing sensible matched."""
    if not name or not name.strip():
        return None
    name = name.strip()

    prefix_year = None
    match = _LEADING_YEAR_PREFIX.match(name)
    if match and _valid_year(match.group("year")):
        prefix_year = int(match.group("year"))
        working = name[match.end():]
    else:
        working = name
    working = _BRACE_TAG.sub(" ", working).strip()

    simple = _SIMPLE_TITLE.sub("", working)
    simple = _WEBSITE_PREFIX.sub("", simple)
    simple = _WEBSITE_POSTFIX.sub("", simple)
    simple = _CLEAN_TORRENT_SUFFIX.sub("", simple)

    for regex, label in _ALBUM_PATTERNS:
        match = regex.search(simple)
        if not match:
            continue
        groups = match.groupdict()

        artist = _clean_captured(groups.get("artist"))
        if groups.get("discography"):
            album = "Discography"
            year = _valid_year(groups.get("endyear"))
        else:
            album = _clean_captured(groups.get("album"))
            year = _valid_year(groups.get("releaseyear"))
        if not artist or not album:
            return None
        if year is None:
            year = prefix_year

        source = _clean_captured(groups.get("source"))
        if source and _SCENE_SOURCE.fullmatch(source):
            source = source.upper()

        codec, bit_depth, bitrate = _parse_quality(name)

        return ParsedFolder(
            artist=artist,
            album=album,
            year=year,
            source=source,
            codec=codec,
            bit_depth=bit_depth,
            bitrate=bitrate,
            # Advisory only: group extraction may return None on a fine parse
            # (e.g. GRMFLAC-style names), matching upstream Lidarr.
            release_group=_parse_release_group(name),
            pattern=label,
        )
    return None


# Soulseek chokes on most punctuation; hyphens inside words are meaningful.
_QUERY_PUNCT_TABLE = str.maketrans({c: " " for c in "#'\"!?.,:;()[]&/+*_"})


def sanitize_search_query(q):
    """Make a Soulseek-safe search query from a title/artist string."""
    if not q:
        return ""
    # MusicBrainz favors curly quotes/dashes; fold them (and accents) first so
    # the punctuation table actually catches them.
    tokens = fold_unicode(q).translate(_QUERY_PUNCT_TABLE).split()
    return " ".join(t for t in tokens if any(ch.isalnum() for ch in t))
