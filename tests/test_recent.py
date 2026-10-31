import json
from datetime import datetime, timedelta, timezone

from soularr_fork.recent import RecentSweep, parse_release_date

NOW = datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)


class RecordingLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)


class StubLidarr:
    """wanted/missing sorted by releaseDate descending, paged like Lidarr."""

    def __init__(self, records, fail_pages=()):
        self.records = sorted(records, key=lambda r: r.get("releaseDate") or "", reverse=True)
        self.fail_pages = set(fail_pages)
        self.calls = []

    def get_wanted(self, page=1, page_size=10, sort_key=None, sort_dir=None, missing=True):
        self.calls.append({"page": page, "page_size": page_size, "sort_key": sort_key, "sort_dir": sort_dir, "missing": missing})
        if page in self.fail_pages:
            raise ConnectionError("boom")
        start = (page - 1) * page_size
        return {"totalRecords": len(self.records), "records": [dict(r) for r in self.records[start : start + page_size]]}


def rec(album_id, days_ago, album_type="Single", title=None, artist="HARDY"):
    released = (NOW - timedelta(days=days_ago)).replace(hour=0, minute=0)
    return {
        "id": album_id,
        "title": title or f"Title {album_id}",
        "albumType": album_type,
        "releaseDate": released.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artist": {"artistName": artist},
        "releases": [{"albumId": album_id}],
    }


def sweep(lidarr, state_path=None, now=NOW, **kwargs):
    logger = RecordingLogger()
    return RecentSweep(lidarr, logger, state_path=state_path, now=lambda: now, **kwargs), logger


def ids(albums):
    return [album["id"] for album in albums]


def test_parse_release_date():
    assert parse_release_date("2026-10-03T00:00:00Z") == datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert parse_release_date("2026-10-03T00:00:00") == datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert parse_release_date(None) is None
    assert parse_release_date("") is None
    assert parse_release_date("not a date") is None


def test_queries_missing_sorted_by_release_date_descending():
    lidarr = StubLidarr([rec(1, 3)])
    source, _ = sweep(lidarr)
    source.next_batch()
    call = lidarr.calls[0]
    assert call["sort_key"] == "releaseDate"
    assert call["sort_dir"] == "descending"
    assert call["missing"] is True


def test_window_and_type_filter_newest_first():
    lidarr = StubLidarr([
        rec(1, 2),
        rec(2, 10, "EP"),
        rec(3, 5, "Album"),
        rec(4, 59),
        rec(5, 61),  # outside the 60-day window
        rec(6, 1, "Broadcast"),
    ])
    source, _ = sweep(lidarr, days=60, per_run=10)
    assert ids(source.next_batch()) == [1, 2, 4]


def test_empty_album_types_means_every_type():
    lidarr = StubLidarr([rec(1, 2, "Album"), rec(2, 3, "Single")])
    source, _ = sweep(lidarr, album_types=(), per_run=10)
    assert ids(source.next_batch()) == [1, 2]


def test_future_and_undated_records_are_skipped():
    future = rec(1, 0)
    future["releaseDate"] = (NOW + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    undated = rec(2, 0)
    undated["releaseDate"] = None
    lidarr = StubLidarr([future, undated, rec(3, 4)])
    source, _ = sweep(lidarr, per_run=10)
    assert ids(source.next_batch()) == [3]


def test_per_run_cap():
    lidarr = StubLidarr([rec(i, i) for i in range(1, 9)])
    source, _ = sweep(lidarr, per_run=3)
    assert ids(source.next_batch()) == [1, 2, 3]


def test_paging_stops_at_window_edge():
    lidarr = StubLidarr([rec(i, i) for i in range(1, 40)])
    source, _ = sweep(lidarr, days=12, per_run=50, page_size=5)
    assert ids(source.next_batch()) == list(range(1, 13))
    # pages 1-3 hold days 1-15; the first out-of-window record ends paging
    assert [call["page"] for call in lidarr.calls] == [1, 2, 3]


def test_paging_stops_at_end_of_list():
    lidarr = StubLidarr([rec(i, i) for i in range(1, 8)])
    source, _ = sweep(lidarr, days=60, per_run=50, page_size=5)
    assert ids(source.next_batch()) == list(range(1, 8))
    assert [call["page"] for call in lidarr.calls] == [1, 2]


def test_retry_window_rotates_through_candidates(tmp_path):
    state = str(tmp_path / "recent.json")
    lidarr = StubLidarr([rec(i, i) for i in range(1, 6)])
    first, _ = sweep(lidarr, state_path=state, per_run=2, retry_hours=24)
    assert ids(first.next_batch()) == [1, 2]
    later, _ = sweep(lidarr, state_path=state, now=NOW + timedelta(hours=1), per_run=2, retry_hours=24)
    assert ids(later.next_batch()) == [3, 4]
    later2, _ = sweep(lidarr, state_path=state, now=NOW + timedelta(hours=2), per_run=2, retry_hours=24)
    assert ids(later2.next_batch()) == [5]
    # nothing due until the retry window has passed
    idle, logger = sweep(lidarr, state_path=state, now=NOW + timedelta(hours=3), per_run=2, retry_hours=24)
    assert idle.next_batch() == []
    assert "0 due" in logger.infos[0]
    # after 24 h the longest-waiting come back first
    again, _ = sweep(lidarr, state_path=state, now=NOW + timedelta(hours=24, minutes=30), per_run=2, retry_hours=24)
    assert ids(again.next_batch()) == [1, 2]


def test_new_release_jumps_ahead_of_retries(tmp_path):
    state = str(tmp_path / "recent.json")
    records = [rec(1, 5), rec(2, 6)]
    first, _ = sweep(StubLidarr(records), state_path=state, per_run=5, retry_hours=1)
    assert ids(first.next_batch()) == [1, 2]
    records.append(rec(3, 9))  # learned by Lidarr later, never searched
    later, _ = sweep(StubLidarr(records), state_path=state, now=NOW + timedelta(hours=2), per_run=1, retry_hours=1)
    assert ids(later.next_batch()) == [3]


def test_state_drops_albums_no_longer_candidates(tmp_path):
    state = str(tmp_path / "recent.json")
    first, _ = sweep(StubLidarr([rec(1, 2), rec(2, 3)]), state_path=state, per_run=5)
    first.next_batch()
    # album 2 got imported (no longer missing)
    later, _ = sweep(StubLidarr([rec(1, 2)]), state_path=state, now=NOW + timedelta(hours=1), per_run=5)
    later.next_batch()
    with open(state) as file:
        assert set(json.load(file)) == {"1"}


def test_unreadable_state_starts_fresh(tmp_path):
    state = tmp_path / "recent.json"
    state.write_text("{not json")
    source, logger = sweep(StubLidarr([rec(1, 2)]), state_path=str(state), per_run=5)
    assert ids(source.next_batch()) == [1]
    assert logger.warnings
    assert json.loads(state.read_text()) == {"1": NOW.isoformat()}


def test_lidarr_error_fails_open():
    lidarr = StubLidarr([rec(1, 2)], fail_pages={1})
    source, logger = sweep(lidarr)
    assert source.next_batch() == []
    assert logger.warnings


def test_lidarr_error_on_later_page_keeps_earlier_pages():
    lidarr = StubLidarr([rec(i, i) for i in range(1, 9)], fail_pages={2})
    source, logger = sweep(lidarr, per_run=10, page_size=5)
    assert ids(source.next_batch()) == [1, 2, 3, 4, 5]
    assert logger.warnings


def test_disabled_by_zero_per_run_or_days():
    lidarr = StubLidarr([rec(1, 2)])
    source, _ = sweep(lidarr, per_run=0)
    assert source.next_batch() == []
    source, _ = sweep(lidarr, days=0)
    assert source.next_batch() == []
    assert lidarr.calls == []


def test_logs_each_pick():
    source, logger = sweep(StubLidarr([rec(70241, 28, title="Man In The Box")]))
    source.next_batch()
    assert any("HARDY - Man In The Box (Single, 2026-10-03)" in line for line in logger.infos)
