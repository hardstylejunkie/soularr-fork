import json
import logging
import os
from datetime import datetime, timedelta

from .nameparse import parse_folder_name
from .normalize import normalize_title

logger = logging.getLogger("soularr")

AUDIO_EXTS = {"flac", "mp3", "m4a", "aac", "ogg", "opus", "wma", "ape", "wav", "aiff", "aif", "alac", "wv", "dsf", "dff", "mpc", "mp2", "m4b", "shn", "tta"}

# Proof-gated folders must be lossless-only; any other audio ext disqualifies.
CLEAN_AUDIO_EXTS = {"flac"}

# Lossy formats can never satisfy a proof gate (folder_audio_clean only
# accepts flac), so a lossy rung on a proof-required type is a dead rung.
LOSSY_AUDIO_EXTS = {"mp3", "aac", "ogg", "opus", "wma"}

TYPE_LADDER_KEYS = {
    "album": "album_allowed_filetypes",
    "ep": "ep_allowed_filetypes",
    "single": "single_allowed_filetypes",
}


def file_ext(filename):
    # slskd directory listings return basenames but search responses return full
    # "dir\file" paths, and the 'extension' field is often empty — always derive
    # the extension from the filename itself.
    name = filename.rsplit("\\", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def parse_ladder(raw):
    # Same parse as upstream allowed_filetypes: bare split, no stripping, so
    # attribute entries like "flac 16/44.1" survive intact.
    if raw is None or not raw.strip():
        return None
    return raw.split(",") if "," in raw else [raw]


def parse_name_set(raw):
    return {name.strip().casefold() for name in raw.split(",") if name.strip()}


def parse_search_sources(raw):
    # search_source accepts a single source or a comma-separated list
    # (missing | cutoff_unmet | cf_below); 'all' keeps its upstream meaning
    # of missing+cutoff_unmet. Order is preserved, duplicates dropped.
    sources = []
    for entry in (raw or "").split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        for source in ["missing", "cutoff_unmet"] if entry == "all" else [entry]:
            if source not in sources:
                sources.append(source)
    return sources or ["missing"]


class TypePolicy:
    def __init__(self, config, base_ladder=None):
        section = "Search Settings"
        if base_ladder is None:
            base_ladder = parse_ladder(config.get(section, "allowed_filetypes", fallback="flac,mp3"))
        self.base_ladder = list(base_ladder)
        # Empty/absent = process every album type (upstream behavior)
        self.processed = parse_name_set(config.get(section, "processed_album_types", fallback="")) or None
        self.ladders = {}
        for type_name, key in TYPE_LADDER_KEYS.items():
            ladder = parse_ladder(config.get(section, key, fallback=""))
            self.ladders[type_name] = ladder if ladder is not None else self.base_ladder
        self.proof_types = parse_name_set(config.get(section, "require_proof_album_types", fallback=""))
        self.proof_exts = {ext.strip().lstrip(".").lower() for ext in config.get(section, "proof_files", fallback="log,cue").split(",") if ext.strip()}
        self.max_probes = config.getint(section, "max_directory_probes", fallback=30)
        self.scene_accept = config.getboolean(section, "proof_accept_scene_names", fallback=True)

    def processes(self, album_type):
        if self.processed is None:
            return True
        return (album_type or "").strip().casefold() in self.processed

    def ladder_for(self, album_type):
        return self.ladders.get((album_type or "").strip().casefold(), self.base_ladder)

    def requires_proof(self, album_type):
        return (album_type or "").strip().casefold() in self.proof_types

    def validate(self, logger):
        # Surface config conflicts once at startup instead of silently never
        # matching: a proof-required type with a lossy ladder rung can't grab
        # through that rung (proof-gated folders must be flac-only).
        for album_type in sorted(self.proof_types):
            lossy = [entry for entry in self.ladder_for(album_type) if entry.strip().split(" ")[0].lower() in LOSSY_AUDIO_EXTS]
            if lossy:
                logger.warning(
                    f"Config conflict: album type '{album_type}' requires proof files but its quality ladder "
                    f"contains lossy entries {lossy} — proof-gated folders only accept flac audio, so these rungs can never match."
                )

    def union_filetypes(self):
        # The global allowed_filetypes must cover every ladder or per-type
        # entries never make it into the search cache. Base first preserves
        # upstream order when no per-type ladders are configured.
        union = []
        for ladder in [self.base_ladder, self.ladders["album"], self.ladders["ep"], self.ladders["single"]]:
            for entry in ladder:
                if entry not in union:
                    union.append(entry)
        return union


def folder_proof_ok(files, proof_exts):
    exts = {file_ext(file.get("filename", "")) for file in files}
    return set(proof_exts) <= exts


def folder_audio_clean(files):
    for file in files:
        ext = file_ext(file.get("filename", ""))
        if ext in AUDIO_EXTS and ext not in CLEAN_AUDIO_EXTS:
            return False
    return True




def _names_consistent(parsed_name, lidarr_name):
    parsed_fold = parsed_name.casefold()
    lidarr_fold = lidarr_name.casefold()
    if parsed_fold in lidarr_fold or lidarr_fold in parsed_fold:
        return True
    return normalize_title(parsed_name) == normalize_title(lidarr_name)


def scene_release_qualifies(leaf_name, artist, title):
    """
    Alternative proof: the folder's leaf name parses (Lidarr-style) to THIS
    album with an explicit FLAC token. WEB/scene FLAC releases carry no
    log/cue by nature; a correctly named release with a FLAC tag is accepted
    when proof_accept_scene_names is on. Folder contents must still pass
    folder_audio_clean.
    """
    parsed = parse_folder_name(leaf_name)
    if parsed is None or not parsed.artist or not parsed.album or parsed.codec != "FLAC":
        return False
    return _names_consistent(parsed.artist, artist) and _names_consistent(parsed.album, title)
def load_staged(file_path):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r") as file:
            staged = json.load(file)
    except (ValueError, OSError, UnicodeDecodeError) as ex:
        logger.warning(f"Error loading staged albums file: {ex}. Starting with empty list.")
        return {}
    if not isinstance(staged, dict):
        logger.warning(f"Staged albums file has unexpected shape ({type(staged).__name__}). Starting with empty list.")
        return {}
    # Entries must be dicts (is_staged/record_staged assume .get()); drop anything else.
    return {album_id: entry for album_id, entry in staged.items() if isinstance(entry, dict)}


def save_staged(file_path, staged):
    try:
        with open(file_path, "w") as file:
            json.dump(staged, file, indent=2)
    except IOError as ex:
        logger.error(f"Error saving staged albums file: {ex}")


def record_staged(file_path, album_id, title):
    staged = load_staged(file_path)
    staged[str(album_id)] = {
        "album_id": album_id,
        "title": title,
        "staged_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_staged(file_path, staged)


def clear_staged(file_path, album_id):
    staged = load_staged(file_path)
    if str(album_id) in staged:
        del staged[str(album_id)]
        save_staged(file_path, staged)


def is_staged(file_path, album_id, max_age_days):
    entry = load_staged(file_path).get(str(album_id))
    if not entry:
        return False
    try:
        staged_at = datetime.strptime(entry.get("staged_at", ""), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return datetime.now() - staged_at <= timedelta(days=max_age_days)
