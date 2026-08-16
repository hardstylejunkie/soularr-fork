import os
from bisect import bisect_right


class CfSweep:
    """Rolling sweep for albums whose existing files carry a low custom-format
    score (LQ/vinyl-rip CF flags): each run inspects the next slice of artists
    and returns the albums whose worst-scoring file sits strictly below the
    threshold, so Soularr can hunt replacement copies. Lidarr's own RSS/upgrade
    machinery is untouched — this only feeds Soularr's search list.

    The cursor file stores "id\\nsortName" of the last-processed artist; runs
    resume after it and wrap at the end of the library. Fail-open: any error
    logs a warning, returns what was gathered, and never advances the cursor
    past an artist that errored.
    """

    def __init__(self, lidarr, logger, threshold=0, artists_per_run=10, cursor_path=None, album_types=("album",)):
        self.lidarr = lidarr
        self.logger = logger
        self.threshold = threshold
        self.artists_per_run = artists_per_run
        self.cursor_path = cursor_path
        self.album_types = {str(entry).strip().casefold() for entry in album_types}

    def next_batch(self):
        try:
            artists = sorted(self.lidarr.get_artist(), key=self._sort_key)
        except Exception as error:
            self.logger.warning(f"CF sweep: failed to fetch artists: {error}")
            return []
        if not artists:
            return []
        start = self._resume_index(artists)
        qualifying = {}
        cursor_artist = None
        for offset in range(min(self.artists_per_run, len(artists))):
            artist = artists[(start + offset) % len(artists)]
            try:
                for album in self._low_score_albums(artist):
                    qualifying.setdefault(album["id"], album)
            except Exception as error:
                self.logger.warning(
                    f"CF sweep: check failed for artist {artist.get('artistName')}: {error}; will resume from this artist next run"
                )
                break
            cursor_artist = artist
        if cursor_artist is not None:
            self._save_cursor(cursor_artist)
        return list(qualifying.values())

    def _low_score_albums(self, artist):
        # Album score = min of the files' customFormatScores. Files without a
        # score are ignored; an album with no scored file at all is skipped —
        # missing data is never treated as bad.
        scores = {}
        for file in self.lidarr.get_track_file(artistId=artist["id"]):
            score = file.get("customFormatScore")
            album_id = file.get("albumId")
            if score is None or album_id is None:
                continue
            if album_id not in scores or score < scores[album_id]:
                scores[album_id] = score
        if not any(score < self.threshold for score in scores.values()):
            return []
        albums = {album["id"]: album for album in self.lidarr.get_album(artistId=artist["id"])}
        results = []
        for album_id, score in scores.items():
            if score >= self.threshold:
                continue
            album = albums.get(album_id)
            if album is None:
                continue
            if (album.get("albumType") or "").strip().casefold() not in self.album_types:
                continue
            if album.get("monitored") is not True:
                continue
            # Having grouped track files above already proves the album has files.
            artist_name = (album.get("artist") or {}).get("artistName") or artist.get("artistName", "")
            self.logger.info(f"CF sweep: {artist_name} - {album.get('title')} file score {score} < {self.threshold}")
            results.append(album)
        return results

    @staticmethod
    def _sort_key(artist):
        return ((artist.get("sortName") or "").casefold(), artist.get("id") or 0)

    def _resume_index(self, artists):
        cursor = self._load_cursor()
        if cursor is None:
            return 0
        artist_id, sort_name = cursor
        for position, artist in enumerate(artists):
            if artist.get("id") == artist_id:
                return (position + 1) % len(artists)
        # Cursor artist vanished (removed/renamed): resume at the position
        # right after where its sortName would have sat.
        keys = [self._sort_key(artist) for artist in artists]
        return bisect_right(keys, (sort_name.casefold(), artist_id)) % len(artists)

    def _load_cursor(self):
        if not self.cursor_path or not os.path.exists(self.cursor_path):
            return None
        try:
            with open(self.cursor_path, "r") as file:
                lines = file.read().splitlines()
        except OSError as error:
            self.logger.warning(f"CF sweep: failed to read cursor file {self.cursor_path}: {error}")
            return None
        if not lines:
            return None
        try:
            artist_id = int(lines[0])
        except ValueError:
            self.logger.warning(f"CF sweep: malformed cursor file {self.cursor_path}; restarting from the top")
            return None
        return artist_id, lines[1] if len(lines) > 1 else ""

    def _save_cursor(self, artist):
        if not self.cursor_path:
            return
        try:
            with open(self.cursor_path, "w") as file:
                file.write(f"{artist.get('id')}\n{artist.get('sortName') or ''}\n")
        except OSError as error:
            self.logger.warning(f"CF sweep: failed to persist cursor file {self.cursor_path}: {error}")
