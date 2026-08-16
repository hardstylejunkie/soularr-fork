import logging

from soularr_fork.dedup import SingleDedup

RSQ = "’"  # curly apostrophe (U+2019)

logger = logging.getLogger("test_dedup")


class StubLidarr:
    def __init__(self, albums_by_artist=None, tracks_by_album=None):
        self.albums_by_artist = albums_by_artist or {}
        self.tracks_by_album = tracks_by_album or {}
        self.get_album_calls = 0

    def get_album(self, albumIds=None, artistId=None, **kwargs):
        self.get_album_calls += 1
        return self.albums_by_artist.get(artistId, [])

    def get_tracks(self, artistId=None, albumId=None, albumReleaseId=None, trackIds=None):
        return [{"title": title} for title in self.tracks_by_album.get(albumId, [])]


class ExplodingLidarr:
    def get_album(self, **kwargs):
        raise RuntimeError("boom")

    def get_tracks(self, **kwargs):
        raise RuntimeError("boom")


def make_album(album_id, title, album_type="Album", secondary_types=(), monitored=True, track_file_count=0):
    return {
        "id": album_id,
        "title": title,
        "albumType": album_type,
        "secondaryTypes": list(secondary_types),
        "monitored": monitored,
        "statistics": {"trackFileCount": track_file_count},
    }


def single(record_id, artist_id, title):
    return {"id": record_id, "artistId": artist_id, "albumType": "Single", "title": title}


def test_blink182_curly_vs_straight_apostrophe_skips():
    lidarr = StubLidarr(
        albums_by_artist={1: [make_album(201, "Enema of the State")]},
        tracks_by_album={
            101: ["All the Small Things", "What's My Age Again?"],
            201: [f"What{RSQ}s My Age Again?", "All the Small Things", f"Adam{RSQ}s Song"],
        },
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(
        single(101, 1, "All the Small Things / What's My Age Again?")
    )
    assert skip
    assert "all 2 tracks" in reason
    assert "Enema of the State" in reason


def test_aerosmith_live_and_mix_parentheticals_keep():
    lidarr = StubLidarr(
        albums_by_artist={2: [make_album(202, "Aerosmith")]},
        tracks_by_album={
            102: ["Walkin' the Dog (live at Paul's Mall, 1973)", "Mama Kin (2024 mix)"],
            202: ["Walkin' the Dog", "Mama Kin"],
        },
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(
        single(102, 2, "Walkin' The Dog / Mama Kin")
    )
    assert not skip
    assert "exclusive track" in reason


def test_mcr_three_tracks_curly_both_sides_skips():
    titles = [
        "S/C/A/R/E/C/R/O/W",
        f"Save Yourself, I{RSQ}ll Hold Them Back",
        "Planetary (GO!)",
    ]
    lidarr = StubLidarr(
        albums_by_artist={3: [make_album(203, "Danger Days")]},
        tracks_by_album={103: titles, 203: titles + ["Party Poison"]},
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(
        single(103, 3, f"Save Yourself, I{RSQ}ll Hold Them Back")
    )
    assert skip
    assert "all 3 tracks" in reason


def test_madonna_afterhours_mixes_keep():
    lidarr = StubLidarr(
        albums_by_artist={4: [make_album(204, "Veronica Electronica")]},
        tracks_by_album={
            104: [
                "Love Sensation",
                "Love Sensation (Afterhours mix)",
                "Love Sensation (Extended Afterhours mix)",
            ],
            204: ["Love Sensation"],
        },
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(single(104, 4, "Love Sensation"))
    assert not skip
    assert reason == "exclusive track: Love Sensation (Afterhours mix)"


def test_kesha_chromeo_remix_keep():
    lidarr = StubLidarr(
        albums_by_artist={5: [make_album(205, "PERIOD")]},
        tracks_by_album={
            105: ["RED FLAG. (Chromeo remix)"],
            205: ["RED FLAG.", "JOYRIDE."],
        },
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(
        single(105, 5, "RED FLAG. (Chromeo remix)")
    )
    assert not skip
    assert "exclusive track" in reason


def test_beyonce_donk_on_no_album_keep():
    lidarr = StubLidarr(
        albums_by_artist={6: [make_album(206, "COWBOY CARTER")]},
        tracks_by_album={
            106: ["MORNING DEW (DONK)"],
            206: ["AMERIICAN REQUIEM", "TEXAS HOLD 'EM"],
        },
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(
        single(106, 6, "MORNING DEW (DONK)")
    )
    assert not skip
    assert "exclusive track" in reason


def test_van_halen_remaster_strips_but_versions_keep():
    lidarr = StubLidarr(
        albums_by_artist={7: [make_album(207, "5150")]},
        tracks_by_album={
            107: [
                "Why Can't This Be Love (Extended Version) (2026 Remaster)",
                "Why Can't This Be Love (Live at the Whisky) (2026 Remaster)",
            ],
            207: ["Why Can't This Be Love", "Get Up"],
        },
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(
        single(107, 7, "Why Can't This Be Love")
    )
    assert not skip
    assert "exclusive track" in reason


def test_secondary_types_string_and_dict_shapes_same_verdicts():
    def build(shape):
        def sec(name):
            return name if shape == "str" else {"id": 1, "name": name}

        albums = [
            make_album(301, "Greatest Hits", secondary_types=[sec("Compilation")]),
            make_album(302, "Studio Album"),
        ]
        return StubLidarr(
            albums_by_artist={8: albums},
            tracks_by_album={
                301: ["Comp Only Track", "Studio Track"],
                302: ["Studio Track"],
                108: ["Comp Only Track"],
                109: ["Studio Track"],
            },
        )

    verdicts = {}
    for shape in ("str", "dict"):
        dedup = SingleDedup(build(shape), logger)
        verdicts[shape] = (
            dedup.should_skip(single(108, 8, "Comp Only Track")),
            dedup.should_skip(single(109, 8, "Studio Track")),
        )
    assert verdicts["str"] == verdicts["dict"]
    comp_verdict, studio_verdict = verdicts["str"]
    assert comp_verdict[0] is False  # compilation excluded from pool
    assert studio_verdict[0] is True


def test_unmonitored_fileless_album_excluded_from_pool_keeps():
    # Metadata-only album (unmonitored, no files) must not cause a skip
    lidarr = StubLidarr(
        albums_by_artist={10: [make_album(210, "Metadata Only", monitored=False)]},
        tracks_by_album={210: ["Hit Song"], 112: ["Hit Song"]},
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(single(112, 10, "Hit Song"))
    assert not skip


def test_monitored_fileless_album_still_skips():
    lidarr = StubLidarr(
        albums_by_artist={11: [make_album(211, "Wanted Album", monitored=True)]},
        tracks_by_album={211: ["Hit Song"], 113: ["Hit Song"]},
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(single(113, 11, "Hit Song"))
    assert skip


def test_unmonitored_album_with_files_still_skips():
    lidarr = StubLidarr(
        albums_by_artist={12: [make_album(212, "Owned Album", monitored=False, track_file_count=10)]},
        tracks_by_album={212: ["Hit Song"], 114: ["Hit Song"]},
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(single(114, 12, "Hit Song"))
    assert skip


def test_home_with_you_not_confused_with_home():
    # Regression: bare-'with' feat stripping normalized 'Home with You' to 'home'
    lidarr = StubLidarr(
        albums_by_artist={13: [make_album(213, "Some Album")]},
        tracks_by_album={213: ["Home"], 115: ["Home with You"]},
    )
    skip, reason = SingleDedup(lidarr, logger).should_skip(single(115, 13, "Home with You"))
    assert not skip
    assert "exclusive track" in reason


def test_empty_pool_keeps():
    lidarr = StubLidarr(albums_by_artist={9: []}, tracks_by_album={110: ["Anything"]})
    skip, reason = SingleDedup(lidarr, logger).should_skip(single(110, 9, "Anything"))
    assert not skip
    assert reason


def test_non_single_ignored():
    dedup = SingleDedup(StubLidarr(), logger)
    record = {"id": 1, "artistId": 1, "albumType": "Album", "title": "X"}
    assert dedup.should_skip(record) == (False, "")


def test_disabled_ignored():
    dedup = SingleDedup(StubLidarr(), logger, enabled=False)
    assert dedup.should_skip(single(1, 1, "X")) == (False, "")


def test_lidarr_exception_keeps():
    skip, reason = SingleDedup(ExplodingLidarr(), logger).should_skip(single(1, 1, "X"))
    assert not skip
    assert "boom" in reason


def test_artist_pool_cached_across_calls():
    lidarr = StubLidarr(
        albums_by_artist={1: [make_album(201, "Enema of the State")]},
        tracks_by_album={
            101: ["All the Small Things"],
            111: ["Adam's Song"],
            201: ["All the Small Things", "Adam's Song"],
        },
    )
    dedup = SingleDedup(lidarr, logger)
    assert dedup.should_skip(single(101, 1, "All the Small Things"))[0]
    assert dedup.should_skip(single(111, 1, "Adam's Song"))[0]
    assert lidarr.get_album_calls == 1
