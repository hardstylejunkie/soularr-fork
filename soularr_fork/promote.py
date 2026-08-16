"""Fork auto-import ("promote"): move a completed staging folder into the
artist tree under the library's token naming convention, then let Lidarr map
the files IN PLACE via RefreshArtist.

The library's custom-format scores are driven by folder-name tokens
([LOG+CUE], [CD]/[WEB], FLAC, trailing -GROUP), so Lidarr's RenameFiles is
never used — it would strip them. Promote composes the final folder name
itself, moves the folder, refreshes the artist, verifies Lidarr picked the
files up, and only then retires any replaced old folder into a recycle bin.

Everything here is fail-open: any error logs a warning and returns; nothing
outside the recycle-bin move is ever deleted. Requires the download staging
dir to live OUTSIDE the Lidarr root folder (RefreshArtist rescans the root).
Stdlib only.
"""

import os
import re
import shutil
import time
from datetime import datetime

# Same character set soularr.py's sanitize_folder_name strips.
_INVALID_CHARS = re.compile(r'[<>:."/\\|?*]')

_REFRESH_POLL_SECONDS = 2


def compose_folder_name(artist_name, album_title, year, disambiguation, medium, depth, has_proof, release_group):
    """Emit the library folder convention:

    {artist} - {title} ({year})[ [disambiguation]] [{medium}][FLAC {depth}bit][LOG+CUE][-GROUP]

    depth None/0 emits a bare [FLAC] token (real library folders without a
    readable depth use that form). The finished name gets the same character
    sanitation as soularr.py's sanitize_folder_name.
    """
    name = f"{artist_name} - {album_title} ({year})"
    if disambiguation:
        name += f" [{disambiguation}]"
    if depth:
        name += f" [{medium}][FLAC {depth}bit]"
    else:
        name += f" [{medium}][FLAC]"
    if has_proof:
        name += "[LOG+CUE]"
    if release_group:
        name += f"-{release_group}"
    return _INVALID_CHARS.sub("", name).strip()


def _skip_id3(handle):
    """Position the handle past an ID3v2 tag if one prefixes the file."""
    header = handle.read(10)
    if len(header) == 10 and header[:3] == b"ID3":
        size = (header[6] << 21) | (header[7] << 14) | (header[8] << 7) | header[9]
        if header[5] & 0x10:  # footer-present flag adds another 10 bytes
            size += 10
        handle.seek(10 + size)
    else:
        handle.seek(0)


def flac_bit_depth(folder_path):
    """Bit depth from the first .flac file's STREAMINFO block, or None."""
    try:
        flacs = sorted(entry for entry in os.listdir(folder_path) if entry.lower().endswith(".flac"))
        if not flacs:
            return None
        with open(os.path.join(folder_path, flacs[0]), "rb") as handle:
            _skip_id3(handle)
            if handle.read(4) != b"fLaC":
                return None
            block_header = handle.read(4)
            if len(block_header) != 4 or (block_header[0] & 0x7F) != 0:
                return None  # first metadata block must be STREAMINFO
            info = handle.read(34)
            if len(info) < 14:
                return None
            return (((info[12] & 1) << 4) | (info[13] >> 4)) + 1
    except Exception:
        return None


def medium_from_release(release, parsed_source, has_proof=False):
    """Medium token for the folder name.

    Lidarr release['format'] wins ('2xCD' counts as CD); then the source
    parsed from the original slskd folder name; then CD/WEB by proof presence.
    """
    release_format = (release or {}).get("format") or ""
    if "CD" in release_format:
        return "CD"
    if "Digital Media" in release_format:
        return "WEB"
    if "Vinyl" in release_format:
        return "Vinyl"
    source = (parsed_source or "").upper()
    if "CD" in source:
        return "CD"
    if "WEB" in source:
        return "WEB"
    return "CD" if has_proof else "WEB"


def _inside(path, folder):
    path = os.path.normpath(path)
    folder = os.path.normpath(folder)
    return path == folder or path.startswith(folder + os.sep)


class Promoter:
    """Move a completed staging folder into the artist tree and have Lidarr
    map it in place (RefreshArtist), verifying before retiring old folders.

    Old album folders left without any trackfile reference after a verified
    promote are moved (never deleted) into recycle_bin/replaced-<YYYYMMDD>/.
    """

    def __init__(self, lidarr, logger, recycle_bin, refresh_timeout=300):
        self.lidarr = lidarr
        self.logger = logger
        self.recycle_bin = recycle_bin
        self.refresh_timeout = refresh_timeout

    def promote(self, album_record, staged_dir, name):
        try:
            artist_path = album_record["artist"]["path"]
            album_id = album_record["id"]
            os.makedirs(artist_path, exist_ok=True)
            old_folders = self._trackfile_folders(album_id)
            new_path = os.path.join(artist_path, name)
            if os.path.exists(new_path):
                self.logger.warning(f"Promote target already exists, leaving staged folder alone: {new_path}")
                return False, f"destination exists: {new_path}"
            shutil.move(staged_dir, new_path)
        except Exception as error:
            self.logger.warning(f"Promote failed before/at move for {staged_dir}: {error}")
            return False, f"move failed: {error}"

        # From here on the folder lives at new_path; every failure leaves it
        # there (root-folder rescans will retry the mapping).
        try:
            command = self.lidarr.post_command(name="RefreshArtist", artistId=album_record["artistId"])
            self._wait_for_command(command["id"])
        except Exception as error:
            self.logger.warning(f"RefreshArtist failed after moving {new_path}; folder left in place: {error}")
            return False, f"refresh failed: {error}"

        try:
            current = self.lidarr.get_track_file(albumId=album_id)
        except Exception as error:
            self.logger.warning(f"Could not verify promote of {new_path}; folder left in place: {error}")
            return False, f"verify failed: {error}"

        current_paths = [file.get("path") for file in current if file.get("path")]
        if not any(_inside(path, new_path) for path in current_paths):
            self.logger.warning(f"Promote not verified: no trackfiles inside {new_path}; folder left in place for rescan")
            return False, f"no trackfiles mapped inside {new_path}"

        self._recycle_old_folders(album_record, old_folders, new_path, current_paths)
        return True, new_path

    def _trackfile_folders(self, album_id):
        """Snapshot of the album's current trackfile folders; empty on error
        (promote proceeds, only old-folder recycling is skipped)."""
        try:
            files = self.lidarr.get_track_file(albumId=album_id)
            return {os.path.normpath(os.path.dirname(file["path"])) for file in files if file.get("path")}
        except Exception as error:
            self.logger.warning(f"Could not snapshot existing trackfiles for album {album_id}: {error}")
            return set()

    def _wait_for_command(self, command_id):
        deadline = time.time() + self.refresh_timeout
        while True:
            task = self.lidarr.get_command(command_id)
            if (task or {}).get("status") in ("completed", "failed"):
                return
            if time.time() >= deadline:
                self.logger.warning(f"Timed out after {self.refresh_timeout}s waiting for RefreshArtist command {command_id}")
                return
            time.sleep(_REFRESH_POLL_SECONDS)

    def _recycle_old_folders(self, album_record, old_folders, new_path, current_paths):
        artist_name = (album_record.get("artist") or {}).get("artistName") or "Unknown Artist"
        for old_folder in old_folders:
            try:
                if _inside(old_folder, new_path) or _inside(new_path, old_folder):
                    continue
                if any(_inside(path, old_folder) for path in current_paths):
                    continue  # still referenced: not replaced, leave it
                if not os.path.isdir(old_folder):
                    continue
                day_dir = os.path.join(self.recycle_bin, "replaced-" + datetime.now().strftime("%Y%m%d"))
                os.makedirs(day_dir, exist_ok=True)
                base = os.path.basename(old_folder)
                target = os.path.join(day_dir, base)
                if os.path.exists(target):
                    target = os.path.join(day_dir, f"{artist_name} - {base}")
                counter = 2
                while os.path.exists(target):
                    target = os.path.join(day_dir, f"{artist_name} - {base} ({counter})")
                    counter += 1
                shutil.move(old_folder, target)
                self.logger.info(f"Recycled replaced folder: {old_folder} -> {target}")
            except Exception as error:
                self.logger.warning(f"Failed to recycle replaced folder {old_folder}: {error}")
