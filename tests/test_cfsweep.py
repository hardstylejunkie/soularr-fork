from soularr_fork.cfsweep import CfSweep


class RecordingLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)


class StubLidarr:
    def __init__(self, artists=None, track_files=None, albums=None, fail_artist_ids=()):
        self.artists = artists or []
        self.track_files = track_files or {}  # artistId -> list of trackfile dicts
        self.albums = albums or {}  # artistId -> list of AlbumResource dicts
        self.fail_artist_ids = set(fail_artist_ids)
        self.track_file_calls = []

    def get_artist(self, id_=None):
        return [dict(artist) for artist in self.artists]

    def get_track_file(self, artistId=None, albumId=None, trackFileIds=None, unmapped=None):
        self.track_file_calls.append(artistId)
        if artistId in self.fail_artist_ids:
            raise RuntimeError("boom")
        return [dict(file) for file in self.track_files.get(artistId, [])]

    def get_album(self, albumIds=None, artistId=None, **kwargs):
        return [dict(album) for album in self.albums.get(artistId, [])]


def make_artist(artist_id, name):
    return {"id": artist_id, "artistName": name, "sortName": name.casefold()}


def make_album(album_id, title, album_type="Album", monitored=True, artist_name="Artist"):
    return {
        "id": album_id,
        "title": title,
        "albumType": album_type,
        "monitored": monitored,
        "artist": {"artistName": artist_name},
        "statistics": {"trackFileCount": 10},
        "releases": [{"albumId": album_id}],
    }


def tf(album_id, score):
    return {"albumId": album_id, "customFormatScore": score}


def sweep(lidarr, cursor_path=None, **kwargs):
    return CfSweep(lidarr, RecordingLogger(), cursor_path=cursor_path, **kwargs)


def album_ids(batch):
    return sorted(album["id"] for album in batch)


def test_threshold_is_strictly_less_than():
    lidarr = StubLidarr(
        artists=[make_artist(1, "Joy Division")],
        track_files={1: [tf(11, -6), tf(12, 0), tf(13, 2)]},
        albums={1: [make_album(11, "Bad Copy"), make_album(12, "At Threshold"), make_album(13, "Fine Copy")]},
    )
    batch = sweep(lidarr).next_batch()
    assert album_ids(batch) == [11]


def test_min_aggregation_partially_bad_album_qualifies():
    # Modeled on the real 'Division' case: min=-10, max=6 across the album's files
    lidarr = StubLidarr(
        artists=[make_artist(1, "Moby")],
        track_files={1: [tf(11, 6), tf(11, -10), tf(11, 3)]},
        albums={1: [make_album(11, "Division", artist_name="Moby")]},
    )
    logger = RecordingLogger()
    batch = CfSweep(lidarr, logger).next_batch()
    assert album_ids(batch) == [11]
    assert batch[0]["releases"] == [{"albumId": 11}]  # full AlbumResource returned
    assert "CF sweep: Moby - Division file score -10 < 0" in logger.infos


def test_none_scores_ignored_and_all_none_album_skipped():
    lidarr = StubLidarr(
        artists=[make_artist(1, "Artist")],
        track_files={
            1: [
                {"albumId": 11, "customFormatScore": None},
                {"albumId": 11},  # field missing entirely
                tf(11, -5),
                {"albumId": 12, "customFormatScore": None},
                {"albumId": 12},
            ]
        },
        albums={1: [make_album(11, "Mixed Data"), make_album(12, "No Data")]},
    )
    batch = sweep(lidarr).next_batch()
    # 11: None files ignored, min of scored files = -5 -> qualifies.
    # 12: no scored file at all -> never treated as bad.
    assert album_ids(batch) == [11]


def test_album_type_filter():
    lidarr = StubLidarr(
        artists=[make_artist(1, "Artist")],
        track_files={1: [tf(11, -10), tf(12, -10)]},
        albums={1: [make_album(11, "The EP", album_type="EP"), make_album(12, "The Album")]},
    )
    assert album_ids(sweep(lidarr).next_batch()) == [12]
    assert album_ids(sweep(lidarr, album_types=("album", "ep")).next_batch()) == [11, 12]


def test_unmonitored_album_excluded():
    lidarr = StubLidarr(
        artists=[make_artist(1, "Artist")],
        track_files={1: [tf(11, -10)]},
        albums={1: [make_album(11, "Unwatched", monitored=False)]},
    )
    assert sweep(lidarr).next_batch() == []


def make_five_artist_stub():
    # Supplied out of order to prove the sweep sorts by sortName (casefold)
    return StubLidarr(artists=[make_artist(3, "Cc"), make_artist(1, "aa"), make_artist(5, "Ee"), make_artist(2, "Bb"), make_artist(4, "dd")])


def test_cursor_batches_then_wraps(tmp_path):
    cursor_path = str(tmp_path / ".cf_artist_cursor.txt")
    lidarr = make_five_artist_stub()
    cf = sweep(lidarr, cursor_path=cursor_path, artists_per_run=2)
    cf.next_batch()
    assert lidarr.track_file_calls == [1, 2]
    cf.next_batch()
    assert lidarr.track_file_calls == [1, 2, 3, 4]
    cf.next_batch()  # wraps past the end
    assert lidarr.track_file_calls == [1, 2, 3, 4, 5, 1]


def test_cursor_survives_fresh_instance(tmp_path):
    cursor_path = str(tmp_path / ".cf_artist_cursor.txt")
    first = make_five_artist_stub()
    sweep(first, cursor_path=cursor_path, artists_per_run=2).next_batch()
    assert first.track_file_calls == [1, 2]
    second = make_five_artist_stub()
    sweep(second, cursor_path=cursor_path, artists_per_run=2).next_batch()
    assert second.track_file_calls == [3, 4]


def test_cursor_persisted_even_when_nothing_qualifies(tmp_path):
    cursor_path = str(tmp_path / ".cf_artist_cursor.txt")
    lidarr = make_five_artist_stub()  # no track files anywhere -> nothing qualifies
    assert sweep(lidarr, cursor_path=cursor_path, artists_per_run=2).next_batch() == []
    with open(cursor_path) as file:
        assert file.read().splitlines() == ["2", "bb"]


def test_vanished_cursor_artist_resumes_at_following_sortname(tmp_path):
    cursor_path = str(tmp_path / ".cf_artist_cursor.txt")
    sweep(make_five_artist_stub(), cursor_path=cursor_path, artists_per_run=2).next_batch()  # cursor at artist 2 ("bb")
    shrunk = StubLidarr(artists=[make_artist(1, "aa"), make_artist(3, "Cc"), make_artist(4, "dd"), make_artist(5, "Ee")])
    sweep(shrunk, cursor_path=cursor_path, artists_per_run=2).next_batch()
    assert shrunk.track_file_calls == [3, 4]


def test_missing_cursor_file_starts_at_top(tmp_path):
    lidarr = make_five_artist_stub()
    sweep(lidarr, cursor_path=str(tmp_path / "never_written.txt"), artists_per_run=1).next_batch()
    assert lidarr.track_file_calls == [1]


def test_exception_fails_open_and_cursor_stops_before_errored_artist(tmp_path):
    cursor_path = str(tmp_path / ".cf_artist_cursor.txt")
    lidarr = StubLidarr(
        artists=[make_artist(1, "aa"), make_artist(2, "bb"), make_artist(3, "cc")],
        track_files={1: [tf(11, -6)], 3: [tf(31, -6)]},
        albums={1: [make_album(11, "Bad One")], 3: [make_album(31, "Bad Three")]},
        fail_artist_ids={2},
    )
    logger = RecordingLogger()
    batch = CfSweep(lidarr, logger, cursor_path=cursor_path, artists_per_run=3).next_batch()
    # Fail-open: what was gathered before the error still comes back
    assert album_ids(batch) == [11]
    assert len(logger.warnings) == 1
    # Cursor stayed at artist 1, so the errored artist is retried next run
    # (the retry run wraps 2 -> 3 -> 1, re-qualifying album 11 along the way)
    lidarr.fail_artist_ids = set()
    retry = CfSweep(lidarr, RecordingLogger(), cursor_path=cursor_path, artists_per_run=3)
    assert album_ids(retry.next_batch()) == [11, 31]
    assert lidarr.track_file_calls == [1, 2, 2, 3, 1]


def test_exception_fetching_artists_returns_empty():
    class ExplodingLidarr:
        def get_artist(self, id_=None):
            raise RuntimeError("boom")

    logger = RecordingLogger()
    assert CfSweep(ExplodingLidarr(), logger).next_batch() == []
    assert len(logger.warnings) == 1


def test_duplicate_albums_deduped_by_id():
    lidarr = StubLidarr(
        artists=[make_artist(1, "Artist")],
        track_files={1: [tf(11, -6), tf(11, -3)]},
        albums={1: [make_album(11, "Bad Copy")]},
    )
    batch = sweep(lidarr).next_batch()
    assert [album["id"] for album in batch] == [11]
