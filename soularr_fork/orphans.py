"""Startup orphan sweep: promote completed downloads stranded in staging.

The promote flow only fires within the cycle that grabbed the album, so a
container restart orphans completed downloads in the staging dir forever.
Once per script run this sweep walks the staging dir's immediate subfolders:
each settled folder is identified (audio tags first, then Lidarr-style
folder-name parsing of the leaf), matched to a Lidarr album, checked for
completeness against the album's track count, run through the same proof
gate as the live grab path, and finally promoted via the regular Promoter
under the library's composed token folder name.

Everything is fail-open per folder — one bad folder never aborts the sweep —
and every folder gets one INFO line with its outcome. A JSON state file
(policy.py store style) caps attempts per folder so a permanently broken
orphan is not re-chewed on every restart. Stdlib only: tag reading and the
set of folders slskd is still downloading come in as injected callables.
"""

import json
import os
import time
from datetime import datetime

from . import policy as fork_policy
from . import promote as fork_promote
from .nameparse import parse_folder_name
from .normalize import remove_feat

# Identification only needs the common cases; policy.AUDIO_EXTS stays the
# authority for the gate itself (folder_audio_clean).
AUDIO_EXTS = {"flac", "mp3", "m4a", "ogg", "opus", "ape", "wav"}


class OrphanSweep:
    def __init__(self, lidarr, logger, staging_dir, promoter, type_policy, tag_reader,
                 active_folders_fn, state_path, gate_proof=True, min_age_minutes=15, max_attempts=4):
        self.lidarr = lidarr
        self.logger = logger
        self.staging_dir = staging_dir
        self.promoter = promoter
        self.type_policy = type_policy
        self.tag_reader = tag_reader
        self.active_folders_fn = active_folders_fn
        self.state_path = state_path
        self.gate_proof = gate_proof
        self.min_age_minutes = min_age_minutes
        self.max_attempts = max_attempts
        self._artists = None
        self._albums_by_artist = {}

    def sweep(self):
        """Process every immediate staging subfolder once; returns
        [(folder_name, outcome), ...] for everything examined."""
        results = []
        state = self._load_state()
        self._artists = None
        self._albums_by_artist = {}
        try:
            active = set(self.active_folders_fn() or ())
        except Exception as error:
            self.logger.warning(f"Orphan sweep: could not list active downloads ({error}); assuming none")
            active = set()
        try:
            entries = sorted(os.listdir(self.staging_dir))
        except OSError as error:
            self.logger.warning(f"Orphan sweep: cannot list staging dir {self.staging_dir}: {error}")
            return results

        present = set()
        for entry in entries:
            folder = os.path.join(self.staging_dir, entry)
            if entry.startswith(".") or not os.path.isdir(folder):
                continue
            present.add(entry)
            outcome, attempted = self._process(entry, folder, active, state)
            self.logger.info(f"Orphan sweep: {entry} -> {outcome}")
            results.append((entry, outcome))
            if attempted:
                if outcome == "promoted":
                    state.pop(entry, None)
                else:
                    record = state.setdefault(entry, {"attempts": 0})
                    record["attempts"] = int(record.get("attempts", 0)) + 1
                    record["last_outcome"] = outcome
                    record["last_attempt"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # Folders no longer in staging (promoted, moved, cleaned up) don't
        # need attempt history anymore.
        for name in list(state):
            if name not in present:
                del state[name]
        self._save_state(state)
        return results

    # --- per-folder pipeline -------------------------------------------------

    def _process(self, leaf, folder, active, state):
        """Returns (outcome, attempted). attempted=False means a skip that
        must not count against the folder's attempt cap."""
        try:
            age_minutes = (time.time() - os.path.getmtime(folder)) / 60
            if age_minutes < self.min_age_minutes:
                return f"skipped (age {age_minutes:.0f}m < {self.min_age_minutes}m)", False
            if self._folder_names(folder, leaf) & active:
                return "skipped (active download)", False
            attempts = int((state.get(leaf) or {}).get("attempts", 0))
            if attempts >= self.max_attempts:
                return f"skipped (attempt cap {attempts}/{self.max_attempts})", False

            audio_files = self._audio_files(folder)
            artist_hint, album_hint = self._identify(leaf, audio_files)
            if not artist_hint or not album_hint:
                return "unidentified", True

            album = self._match(artist_hint, album_hint, len(audio_files))
            if album is None:
                return f"unmatched ({artist_hint} - {album_hint})", True

            track_count = ((album.get("statistics") or {}).get("trackCount")) or 0
            if len(audio_files) != track_count:
                return f"incomplete ({len(audio_files)}/{track_count})", True

            artist_name = (album.get("artist") or {}).get("artistName") or artist_hint
            title = album.get("title") or album_hint
            if self.gate_proof and self.type_policy is not None and self.type_policy.requires_proof(album.get("albumType")):
                if not self._gate_ok(leaf, folder, artist_name, title):
                    return "gate-failed", True

            parsed = parse_folder_name(leaf)
            has_proof = self._has_proof(folder)
            year = (album.get("releaseDate") or "")[0:4] or (parsed.year if parsed else None) or ""
            name = fork_promote.compose_folder_name(
                artist_name=artist_name,
                album_title=title,
                year=year,
                disambiguation=None,
                medium=fork_promote.medium_from_release(None, parsed.source if parsed else None, has_proof),
                depth=fork_promote.flac_bit_depth(folder),
                has_proof=has_proof,
                release_group=parsed.release_group if parsed else None,
            )
            promoted, detail = self.promoter.promote(album, folder, name)
            if promoted:
                return "promoted", True
            return f"promote failed: {detail}", True
        except Exception as error:
            self.logger.warning(f"Orphan sweep error on {leaf}: {error}")
            return f"error: {error}", True

    # --- identification ------------------------------------------------------

    def _identify(self, leaf, audio_files):
        """(artist, album) hints: tags from the first audio file win, the
        parsed leaf folder name fills whatever the tags didn't."""
        artist = album = None
        if audio_files:
            try:
                tags = self.tag_reader(audio_files[0])
            except Exception:
                tags = None
            if tags:
                artist = (tags.get("artist") or "").strip() or None
                album = (tags.get("album") or "").strip() or None
        if not artist or not album:
            parsed = parse_folder_name(leaf)
            if parsed is not None:
                artist = artist or parsed.artist
                album = album or parsed.album
        return artist, album

    # --- Lidarr matching -----------------------------------------------------

    def _match(self, artist_hint, album_hint, audio_count):
        artist = self._match_artist(artist_hint)
        if artist is None:
            return None
        artist_id = artist.get("id")
        if artist_id not in self._albums_by_artist:
            self._albums_by_artist[artist_id] = self.lidarr.get_album(artistId=artist_id) or []
        hits = [album for album in self._albums_by_artist[artist_id]
                if (album.get("title") or "").casefold() == album_hint.casefold()]
        if not hits:
            return None
        if self.type_policy is not None:
            processed = [album for album in hits if self.type_policy.processes(album.get("albumType"))]
            if processed:
                hits = processed
        if len(hits) > 1:
            exact = [album for album in hits
                     if ((album.get("statistics") or {}).get("trackCount")) == audio_count]
            hits = exact or sorted(hits, key=lambda album: album.get("releaseDate") or "9999")
        return hits[0]

    def _match_artist(self, artist_hint):
        if self._artists is None:
            self._artists = self.lidarr.get_artist() or []
        for hint in self._artist_hints(artist_hint):
            for artist in self._artists:
                if (artist.get("artistName") or "").casefold() == hint:
                    return artist
        return None

    @staticmethod
    def _artist_hints(artist_hint):
        """Exact casefold first; tag artists often carry a featuring clause
        ('2Pac feat. Nate Dogg') absent from Lidarr's artistName, so retry
        with the feat clause stripped."""
        hints = [artist_hint.casefold()]
        stripped = remove_feat(artist_hint).casefold()
        if stripped and stripped not in hints:
            hints.append(stripped)
        return hints

    # --- gate / proof --------------------------------------------------------

    def _gate_ok(self, leaf, folder, artist_name, title):
        """Same rule as the live grab gate: lossless-clean audio AND (proof
        files present OR a matching scene name when the policy accepts them)."""
        listing = self._file_listing(folder)
        if not fork_policy.folder_audio_clean(listing):
            return False
        if fork_policy.folder_proof_ok(listing, self.type_policy.proof_exts):
            return True
        return bool(self.type_policy.scene_accept) and fork_policy.scene_release_qualifies(leaf, artist_name, title)

    def _has_proof(self, folder):
        exts = {fork_policy.file_ext(entry["filename"]) for entry in self._file_listing(folder)}
        return "log" in exts and "cue" in exts

    # --- filesystem helpers --------------------------------------------------

    @staticmethod
    def _folder_names(folder, leaf):
        """The folder's own leaf name plus every nested directory name —
        multi-disc downloads land as Album/CD1, and slskd's active transfer
        list carries the remote leaf (which can be the disc subfolder)."""
        names = {leaf}
        for _, dirnames, _ in os.walk(folder):
            names.update(dirnames)
        return names

    @staticmethod
    def _audio_files(folder):
        found = []
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames.sort()
            for filename in sorted(filenames):
                if fork_policy.file_ext(filename) in AUDIO_EXTS:
                    found.append(os.path.join(dirpath, filename))
        return found

    @staticmethod
    def _file_listing(folder):
        """Recursive {'filename': basename} dicts, the shape the policy gate
        helpers expect from a slskd directory listing."""
        return [{"filename": filename}
                for _, _, filenames in os.walk(folder)
                for filename in filenames]

    # --- state (policy.py JSON store style) ----------------------------------

    def _load_state(self):
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, "r") as file:
                state = json.load(file)
        except (ValueError, OSError, UnicodeDecodeError) as error:
            self.logger.warning(f"Error loading orphan sweep state: {error}. Starting fresh.")
            return {}
        if not isinstance(state, dict):
            self.logger.warning(f"Orphan sweep state has unexpected shape ({type(state).__name__}). Starting fresh.")
            return {}
        return {name: entry for name, entry in state.items() if isinstance(entry, dict)}

    def _save_state(self, state):
        try:
            with open(self.state_path, "w") as file:
                json.dump(state, file, indent=2)
        except IOError as error:
            self.logger.error(f"Error saving orphan sweep state: {error}")
