import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soularr_fork.nameparse import ParsedFolder, parse_folder_name, sanitize_search_query


def check(name, expected):
    parsed = parse_folder_name(name)
    assert parsed is not None, name
    for field, value in expected.items():
        assert getattr(parsed, field) == value, f"{name}: {field}"
    return parsed


class TestRequiredCases:
    def test_scene_cd_flac(self):
        check("Styx-20th_Century_Masters_The_Millennium_Collection-CD-FLAC-2002-PERFECT", {
            "artist": "Styx",
            "album": "20th Century Masters The Millennium Collection",
            "source": "CD",
            "year": 2002,
            "codec": "FLAC",
            "release_group": "PERFECT",
        })

    def test_group_containing_codec_token_returns_none(self):
        # GRMFLAC ends in FLAC: the lookbehind guard drops it, same as upstream.
        check("Adele-21-CD-FLAC-2011-GRMFLAC", {
            "artist": "Adele",
            "album": "21",
            "year": 2011,
            "codec": "FLAC",
            "release_group": None,
        })

    def test_ep_token_excluded_from_album(self):
        # The EP token is captured as the scene version and dropped; album
        # excludes it (ParsedFolder has no version field).
        check("Little_Big_Town-From_LBT_With_Love-EP-WEB-FLAC-2026-GROUP", {
            "artist": "Little Big Town",
            "album": "From LBT With Love",
            "source": "WEB",
            "year": 2026,
            "codec": "FLAC",
            "release_group": "GROUP",
        })

    def test_brace_tag_feeds_sample_size_then_strips(self):
        check("Fleetwood Mac - Rumours (1977) [FLAC] {24-96}", {
            "artist": "Fleetwood Mac",
            "album": "Rumours",
            "year": 1977,
            "codec": "FLAC",
            "bit_depth": 24,
            "release_group": None,
        })

    def test_cd_sample_rate_is_not_bit_depth(self):
        check("Mariah Carey - #1 to Infinity (2015) FLAC 16-44.1", {
            "artist": "Mariah Carey",
            "album": "#1 to Infinity",
            "year": 2015,
            "codec": "FLAC",
            "bit_depth": None,
        })

    def test_leading_year_prefix_captured(self):
        check("(2001) Nelly - Training Day [EAC-FLAC]", {
            "artist": "Nelly",
            "album": "Training Day",
            "year": 2001,
            "codec": "FLAC",
        })


# Rows cribbed from Lidarr's ParserFixture / QualityParserFixture /
# ReleaseGroupParserFixture (music cases our folder-name port covers).
LIDARR_CASES = [
    ("VA - The Best 101 Love Ballads (2017) MP3 [192 kbps]",
     {"artist": "VA", "album": "The Best 101 Love Ballads", "year": 2017,
      "codec": "MP3", "bitrate": "192"}),
    ("ATCQ - The Love Movement 1998 2CD 192kbps  RIP",
     {"artist": "ATCQ", "album": "The Love Movement", "year": 1998}),
    ("Maula - Jism 2 [2012] Mp3 - 192Kbps [Extended]- TK",
     {"artist": "Maula", "album": "Jism 2", "year": 2012,
      "codec": "MP3", "bitrate": "192"}),
    ("VA - Complete Clubland - The Ultimate Ride Of Your Lfe [2014][MP3][192 kbps]",
     {"artist": "VA", "album": "Complete Clubland - The Ultimate Ride Of Your Lfe",
      "year": 2014, "codec": "MP3"}),
    ("Complete Clubland - The Ultimate Ride Of Your Lfe [2014][MP3](192kbps)",
     {"artist": "Complete Clubland", "album": "The Ultimate Ride Of Your Lfe",
      "year": 2014, "codec": "MP3"}),
    ("Gary Clark Jr - Live North America 2016 (2017) MP3 192kbps",
     {"artist": "Gary Clark Jr", "album": "Live North America 2016", "year": 2017,
      "codec": "MP3", "bitrate": "192"}),
    ("Childish Gambino - Awaken, My Love Album 2016 mp3 320 Kbps",
     {"artist": "Childish Gambino", "album": "Awaken, My Love Album", "year": 2016,
      "codec": "MP3", "bitrate": "320"}),
    ("Ricardo Arjona - APNEA (Single 2014) (320 kbps)",
     {"artist": "Ricardo Arjona", "album": "APNEA", "year": 2014}),
    ("Kehlani - SweetSexySavage (Deluxe Edition) (2017) 320",
     {"artist": "Kehlani", "album": "SweetSexySavage", "year": 2017, "codec": None}),
    ("Anderson Paak - Malibu (320)(2016)",
     {"artist": "Anderson Paak", "album": "Malibu", "year": 2016}),
    ("Caetano Veloso Discografia Completa MP3 @256",
     {"artist": "Caetano Veloso", "album": "Discography",
      "codec": "MP3", "bitrate": "256"}),
    ("Little Mix - Salute [Deluxe Edition] [2013] [M4A-256]-V3nom [GLT]",
     # Last match wins, so the trailing [GLT] tag beats -V3nom (same as .NET).
     {"artist": "Little Mix", "album": "Salute", "year": 2013,
      "codec": "AAC", "bitrate": "256", "release_group": "GLT"}),
    ("Ricky Martin - A Quien Quiera Escuchar (2015) 256 kbps [GloDLS]",
     {"artist": "Ricky Martin", "album": "A Quien Quiera Escuchar", "year": 2015}),
    ("Jake Bugg - Jake Bugg (Album) [2012] {MP3 256 kbps}",
     {"artist": "Jake Bugg", "album": "Jake Bugg", "year": 2012,
      "codec": "MP3", "bitrate": "256"}),
    ("Milky Chance - Sadnecessary [256 Kbps] [M4A]",
     {"artist": "Milky Chance", "album": "Sadnecessary", "year": None,
      "codec": "AAC", "bitrate": "256"}),
    ("Clean Bandit - New Eyes [2014] [Mp3-256]-V3nom [GLT]",
     {"artist": "Clean Bandit", "album": "New Eyes", "year": 2014,
      "codec": "MP3", "bitrate": "256"}),
    ("Armin van Buuren - A State Of Trance 810 (20.04.2017) 256 kbps",
     {"artist": "Armin van Buuren", "album": "A State Of Trance 810", "year": 2017}),
    ("PJ Harvey - Let England Shake [mp3-256-2011][trfkad]",
     {"artist": "PJ Harvey", "album": "Let England Shake", "year": 2011,
      "codec": "MP3", "bitrate": "256"}),
    ("Kendrick Lamar - DAMN (2017) FLAC",
     {"artist": "Kendrick Lamar", "album": "DAMN", "year": 2017, "codec": "FLAC"}),
    ("Alicia Keys - Vault Playlist Vol. 1 (2017) [FLAC CD]",
     {"artist": "Alicia Keys", "album": "Vault Playlist Vol  1", "year": 2017,
      "codec": "FLAC"}),
    ("Gorillaz - Humanz (Deluxe) - lossless FLAC Tracks - 2017 - CDrip",
     {"artist": "Gorillaz", "album": "Humanz", "year": 2017, "codec": "FLAC"}),
    ("David Bowie - Blackstar (2016) [FLAC]",
     {"artist": "David Bowie", "album": "Blackstar", "year": 2016,
      "codec": "FLAC", "release_group": None}),
    ("The Cure - Greatest Hits (2001) FLAC Soup",
     {"artist": "The Cure", "album": "Greatest Hits", "year": 2001, "codec": "FLAC"}),
    ("Slowdive - Souvlaki (FLAC)",
     {"artist": "Slowdive", "album": "Souvlaki", "year": None, "codec": "FLAC"}),
    ("John Coltrane - Kulu Se Mama (1965) [EAC-FLAC]",
     {"artist": "John Coltrane", "album": "Kulu Se Mama", "year": 1965,
      "codec": "FLAC"}),
    ("The Rolling Stones - The Very Best Of '75-'94 (1995) {FLAC}",
     {"artist": "The Rolling Stones", "album": "The Very Best Of '75-'94",
      "year": 1995, "codec": "FLAC"}),
    ("Migos-No_Label_II-CD-FLAC-2014-FORSAKEN",
     {"artist": "Migos", "album": "No Label II", "source": "CD", "year": 2014,
      "codec": "FLAC", "release_group": "FORSAKEN"}),
    ("A.I. - Sex & Robots [2007/MP3/V0(VBR)]",
     {"artist": "A I", "album": "Sex & Robots", "year": 2007,
      "codec": "MP3", "bitrate": "V0"}),
    ("Jay-Z - 4:44 (Deluxe Edition) (2017) 320",
     {"artist": "Jay-Z", "album": "4:44", "year": 2017}),
    ("[scnzbefnet][509103] Jay-Z - 4:44 (Deluxe Edition) (2017) 320",
     {"artist": "Jay-Z", "album": "4:44", "year": 2017}),
    ("VA - NOW Thats What I Call Music 96 (2017) [Mp3~Kbps]",
     {"artist": "VA", "album": "NOW Thats What I Call Music 96", "year": 2017,
      "codec": "MP3"}),
    ("Queen - The Ultimate Best Of Queen(2011)[mp3]",
     {"artist": "Queen", "album": "The Ultimate Best Of Queen", "year": 2011,
      "codec": "MP3"}),
    ("Barış Manço - Ben Bilirim [1993/FLAC/Lossless/Log]",
     {"artist": "Barış Manço", "album": "Ben Bilirim", "year": 1993,
      "codec": "FLAC"}),
    ("Imagine Dragons-Smoke And Mirrors-Deluxe Edition-2CD-FLAC-2015-JLM",
     {"artist": "Imagine Dragons", "album": "Smoke And Mirrors", "source": "2CD",
      "year": 2015, "codec": "FLAC", "release_group": "JLM"}),
    ("Dani_Sbert-Togheter-WEB-2017-FURY",
     {"artist": "Dani Sbert", "album": "Togheter", "source": "WEB", "year": 2017,
      "release_group": "FURY"}),
    ("New.Edition-One.Love-CD-FLAC-2017-MrFlac",
     {"artist": "New Edition", "album": "One Love", "source": "CD", "year": 2017,
      "codec": "FLAC", "release_group": None}),
    ("Shinedown-Us and Them-NMR-2005-NMR",
     {"artist": "Shinedown", "album": "Us and Them", "year": 2005,
      "release_group": "NMR"}),
    ("Led Zeppelin - Studio Discography 1969-1982 (10 albums)(flac)",
     {"artist": "Led Zeppelin", "album": "Discography", "year": 1982,
      "codec": "FLAC"}),
    ("Minor Threat - Complete Discography [1989] [Anthology]",
     {"artist": "Minor Threat", "album": "Discography", "year": 1989}),
    ("Captain-Discography_1998_-_2001-CD-FLAC-2007-UTP",
     {"artist": "Captain", "album": "Discography", "year": 2001,
      "codec": "FLAC", "release_group": "UTP"}),
    ("Coolio - Gangsta's Paradise (1995) (FLAC Lossless)",
     {"artist": "Coolio", "album": "Gangsta's Paradise", "year": 1995,
      "codec": "FLAC"}),
    ("Brother Ali-2007-The Undisputed Truth-FTD",
     {"artist": "Brother Ali", "album": "The Undisputed Truth", "year": 2007}),
    ("Brother Ali-The Undisputed Truth-2007-FTD",
     {"artist": "Brother Ali", "album": "The Undisputed Truth", "year": 2007,
      "release_group": "FTD"}),
    ("(Eclectic Progressive Rock) [CD] Peter Hammill - From The Trees - 2017, FLAC (tracks + .cue), lossless",
     {"artist": "Peter Hammill", "album": "From The Trees", "source": "CD",
      "year": 2017, "codec": "FLAC"}),
    ("(Folk Rock / Pop) Aztec Two-Step - Naked - 2017, MP3, 320 kbps",
     {"artist": "Aztec Two-Step", "album": "Naked", "year": 2017,
      "codec": "MP3", "bitrate": "320"}),
    ("Olafur.Arnalds-Remember-WEB-2018-ENTiTLED",
     {"artist": "Olafur Arnalds", "album": "Remember", "source": "WEB",
      "year": 2018, "release_group": "ENTiTLED"}),
    ("Olafur.Arnalds-Remember-WEB-2018-ENTiTLED-Pre",
     {"release_group": "ENTiTLED"}),
    ("Kid_Cudi-Entergalactic-WEBFLAC-2022-NACHOS",
     {"artist": "Kid Cudi", "album": "Entergalactic", "source": "WEB",
      "year": 2022, "codec": "FLAC", "release_group": "NACHOS"}),
    ("Foghat-Foghat_Live-24-192-WEB-FLAC-REMASTERED-2016-OBZEN",
     {"artist": "Foghat", "album": "Foghat Live", "source": "WEB", "year": 2016,
      "codec": "FLAC", "bit_depth": 24, "release_group": "OBZEN"}),
    ("Linkin Park - Studio Collection 2000-2012 (2013) [WEB FLAC24-44.1]",
     {"artist": "Linkin Park", "album": "Studio Collection 2000-2012",
      "year": 2013, "codec": "FLAC", "bit_depth": 24}),
    ("Green_Day-Father_Of_All-24-44-WEB-FLAC-2020-OBZEN",
     {"artist": "Green Day", "album": "Father Of All", "source": "WEB",
      "year": 2020, "codec": "FLAC", "bit_depth": 24}),
    ("Sia - This Is Acting (Standard Edition) [2016-Web-MP3-V0(VBR)]",
     {"artist": "Sia", "album": "This Is Acting", "year": 2016,
      "codec": "MP3", "bitrate": "V0"}),
    ("Mount Eerie - A Crow Looked at Me (2017) [MP3 V0 VBR)]",
     {"artist": "Mount Eerie", "album": "A Crow Looked at Me", "year": 2017,
      "codec": "MP3", "bitrate": "V0"}),
]


@pytest.mark.parametrize("name,expected", LIDARR_CASES, ids=[c[0] for c in LIDARR_CASES])
def test_lidarr_rows(name, expected):
    check(name, expected)


@pytest.mark.parametrize("name", [
    "",
    "   ",
    "Bad Format",
    "02 Unchained.flac",
    "Fall Out Boy - 02 - Title.wav",
])
def test_unparseable_names_return_none(name):
    assert parse_folder_name(name) is None


class TestYearSanity:
    def test_out_of_range_year_dropped(self):
        parsed = check("Some Artist - Some Album 0512", {"album": "Some Album"})
        assert parsed.year is None

    def test_out_of_range_prefix_year_not_captured(self):
        parsed = parse_folder_name("(0512) Nelly - Training Day [EAC-FLAC]")
        assert parsed is None or parsed.year is None

    def test_genre_parens_not_treated_as_year_prefix(self):
        # ruTracker '(Genre)' prefixes must survive for the ruTracker patterns.
        check("(Eclectic Progressive Rock) [CD] Peter Hammill - From The Trees - 2017, FLAC (tracks + .cue), lossless",
              {"artist": "Peter Hammill", "source": "CD"})


class TestParsedFolderShape:
    def test_pattern_always_set(self):
        parsed = parse_folder_name("Adele-21-CD-FLAC-2011-GRMFLAC")
        assert isinstance(parsed.pattern, str) and parsed.pattern

    def test_defaults(self):
        empty = ParsedFolder()
        assert empty.artist is None
        assert empty.release_group is None
        assert empty.pattern == ""


class TestSanitizeSearchQuery:
    @pytest.mark.parametrize("query,expected", [
        ("#1's", "1 s"),
        ("S/C/A/R/E/C/R/O/W", "S C A R E C R O W"),
        ("AC/DC Back in Black", "AC DC Back in Black"),
        ("Panic! At The Disco", "Panic At The Disco"),
        ("Twenty-One Pilots", "Twenty-One Pilots"),
        ('"Weird Al" Yankovic', "Weird Al Yankovic"),
        ("Mr. & Mrs. Smith - Theme", "Mr Mrs Smith Theme"),
        ("Dani_Sbert Togheter", "Dani Sbert Togheter"),
        # Curly punctuation (MusicBrainz's preferred forms) folds like straight
        ("Don’t Smile", "Don t Smile"),
        ("“Heroes”", "Heroes"),
        ("Beyoncé ‐ Halo", "Beyonce Halo"),
        ("  spaced   out  ", "spaced out"),
        ("...", ""),
        ("", ""),
    ])
    def test_queries(self, query, expected):
        assert sanitize_search_query(query) == expected
