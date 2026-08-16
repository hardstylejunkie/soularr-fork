from soularr_fork.normalize import normalize_title


class SingleDedup:
    """Skips wanted Singles whose every track already exists on one of the
    artist's studio albums."""

    def __init__(self, lidarr, logger, enabled=True, secondary_excludes={'Compilation', 'Live', 'Remix'}, require_album_presence=True):
        self.lidarr = lidarr
        self.logger = logger
        self.enabled = enabled
        self.secondary_excludes = set(secondary_excludes)
        # Only count albums the user monitors or already owns files for;
        # metadata-only albums Lidarr merely knows about must not cause skips.
        self.require_album_presence = require_album_presence
        self._pool_cache = {}
        self._pool_albums = {}

    def should_skip(self, record):
        if not self.enabled or record.get('albumType') != 'Single':
            return False, ''
        try:
            tracks = self.lidarr.get_tracks(albumId=record['id'])
            if not tracks:
                return False, 'single has no tracks'
            pool = self._artist_pool(record['artistId'])
            if not pool:
                self.logger.warning(f"Empty studio album pool for artistId {record['artistId']}; keeping single: {record.get('title')}")
                return False, 'no studio album pool for artist'
            for track in tracks:
                if normalize_title(track['title']) not in pool:
                    return False, f"exclusive track: {track['title']}"
            title_albums = self._pool_albums[record['artistId']]
            matched = dict.fromkeys(title_albums[normalize_title(track['title'])] for track in tracks)
            return True, f"all {len(tracks)} tracks on studio albums ({', '.join(matched)})"
        except Exception as error:
            self.logger.warning(f"Single dedup check failed for {record.get('title')}: {error}")
            return False, f"dedup check failed: {error}"

    def _artist_pool(self, artist_id):
        if artist_id not in self._pool_cache:
            title_albums = {}
            for album in self.lidarr.get_album(artistId=artist_id):
                if album.get('albumType') != 'Album':
                    continue
                if self.require_album_presence and not self._album_present(album):
                    continue
                secondaries = {self._secondary_name(entry) for entry in album.get('secondaryTypes') or []}
                if secondaries & self.secondary_excludes:
                    continue
                for track in self.lidarr.get_tracks(albumId=album['id']):
                    title = normalize_title(track['title'])
                    if title and title not in title_albums:
                        title_albums[title] = album.get('title', '')
            self._pool_cache[artist_id] = frozenset(title_albums)
            self._pool_albums[artist_id] = title_albums
        return self._pool_cache[artist_id]

    @staticmethod
    def _album_present(album):
        # Monitored (Lidarr will fetch it) or already has files on disk.
        # Anything else is metadata only; excluding it errs toward KEEP.
        return album.get('monitored') is True or (album.get('statistics') or {}).get('trackFileCount', 0) > 0

    @staticmethod
    def _secondary_name(entry):
        # secondaryTypes arrives as strings or {id,name} dicts depending on Lidarr build
        return entry.get('name', '') if isinstance(entry, dict) else entry
