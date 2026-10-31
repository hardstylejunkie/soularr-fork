import json
import os
from datetime import datetime, timedelta, timezone


def parse_release_date(raw):
    """Lidarr sends releaseDate as ISO-8601 UTC ("2026-10-03T00:00:00Z").
    Returns an aware datetime, or None for a missing/unparseable value."""
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


class RecentSweep:
    """Search source for new releases: the missing albums released within the
    last `days` days (counted in whole days), newest first, limited to `album_types` (empty = every
    type) and to `per_run` albums per run.

    New singles/EPs reach Soulseek over days and Lidarr's indexers rarely carry
    them, so one search is not enough — but neither is every 20-minute cycle.
    Each pick is remembered in a JSON state file and an album is searched again
    only once `retry_hours` have passed. Never-searched albums go first (newest
    release first), then the ones that have waited longest. Albums that leave
    the window or stop being missing drop out of the state file.

    Fail-open: a Lidarr error logs a warning and returns what was gathered.
    """

    def __init__(self, lidarr, logger, days=60, album_types=("single", "ep"), per_run=5, retry_hours=24, state_path=None, page_size=250, now=None):
        self.lidarr = lidarr
        self.logger = logger
        self.days = days
        self.album_types = {str(entry).strip().casefold() for entry in album_types if str(entry).strip()}
        self.per_run = per_run
        self.retry = timedelta(hours=retry_hours)
        self.state_path = state_path
        self.page_size = page_size
        self._now = now

    def now(self):
        return self._now() if self._now is not None else datetime.now(timezone.utc)

    def next_batch(self):
        if self.per_run <= 0 or self.days <= 0:
            return []
        now = self.now()
        # Day-granular: Lidarr release dates are midnight UTC, so "60 days"
        # includes everything released on the day 60 days ago.
        cutoff = (now - timedelta(days=self.days)).replace(hour=0, minute=0, second=0, microsecond=0)
        candidates = self._candidates(now, cutoff)
        state = self._load_state()
        # Forget albums that left the window or are no longer missing.
        state = {key: value for key, value in state.items() if key in candidates}

        due = []
        for key, album in candidates.items():
            last = parse_release_date(state.get(key))
            if last is None or now - last >= self.retry:
                due.append((last, album))
        # Never searched first; then longest-waiting; newest release breaks ties.
        due.sort(key=lambda item: (
            item[0] is not None,
            item[0] or now,
            -parse_release_date(item[1].get("releaseDate")).timestamp(),
        ))
        picked = [album for _, album in due[: self.per_run]]

        stamp = now.isoformat()
        for album in picked:
            state[str(album["id"])] = stamp
        self._save_state(state)

        types = ",".join(sorted(self.album_types)) if self.album_types else "all types"
        self.logger.info(
            f"Recent releases: {len(candidates)} missing ({types}) released since {cutoff:%Y-%m-%d}, "
            f"{len(due)} due for a search, taking {len(picked)}"
        )
        for album in picked:
            artist_name = (album.get("artist") or {}).get("artistName", "")
            self.logger.info(
                f"Recent release: {artist_name} - {album.get('title')} ({album.get('albumType')}, "
                f"{(album.get('releaseDate') or '')[:10]})"
            )
        return picked

    def _candidates(self, now, cutoff):
        # Wanted/missing sorted by release date, newest first; stop paging at
        # the first record older than the window.
        candidates = {}
        page = 1
        while True:
            try:
                data = self.lidarr.get_wanted(page=page, page_size=self.page_size, sort_key="releaseDate", sort_dir="descending", missing=True)
            except Exception as error:
                self.logger.warning(f"Recent releases: failed to fetch wanted/missing page {page}: {error}")
                break
            records = data.get("records") or []
            reached_cutoff = False
            for album in records:
                released = parse_release_date(album.get("releaseDate"))
                if released is None or released > now:
                    continue
                if released < cutoff:
                    reached_cutoff = True
                    break
                if self.album_types and (album.get("albumType") or "").strip().casefold() not in self.album_types:
                    continue
                candidates.setdefault(str(album["id"]), album)
            total = data.get("totalRecords") or 0
            if reached_cutoff or not records or page * self.page_size >= total:
                break
            page += 1
        return candidates

    def _load_state(self):
        if not self.state_path or not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r") as file:
                data = json.load(file)
        except (OSError, ValueError) as error:
            self.logger.warning(f"Recent releases: unreadable state file {self.state_path} ({error}); starting fresh")
            return {}
        return {str(key): value for key, value in data.items()} if isinstance(data, dict) else {}

    def _save_state(self, state):
        if not self.state_path:
            return
        temp_path = self.state_path + ".tmp"
        try:
            with open(temp_path, "w") as file:
                json.dump(state, file, indent=1, sort_keys=True)
            os.replace(temp_path, self.state_path)
        except OSError as error:
            self.logger.warning(f"Recent releases: failed to persist state file {self.state_path}: {error}")
