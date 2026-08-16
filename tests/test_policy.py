import configparser

from soularr_fork import policy


def make_config(text):
    config = configparser.ConfigParser()
    config.read_string(text)
    return config


STOCK_CONFIG = """
[Search Settings]
allowed_filetypes = flac,mp3
"""

FORK_CONFIG = """
[Search Settings]
allowed_filetypes = flac,mp3
processed_album_types = Album,EP,Single
album_allowed_filetypes = flac 16/44.1,flac
ep_allowed_filetypes = flac,mp3 320
single_allowed_filetypes = mp3 320,mp3
require_proof_album_types = Album
proof_files = log,cue
max_directory_probes = 12
"""


class TestTypePolicyStock:
    def setup_method(self):
        self.policy = policy.TypePolicy(make_config(STOCK_CONFIG))

    def test_processes_everything(self):
        for album_type in ["Album", "EP", "Single", "Broadcast", "Other", "", None]:
            assert self.policy.processes(album_type)

    def test_single_ladder_everywhere(self):
        assert self.policy.ladder_for("Album") == ["flac", "mp3"]
        assert self.policy.ladder_for("Single") == ["flac", "mp3"]
        assert self.policy.ladder_for("Broadcast") == ["flac", "mp3"]

    def test_union_matches_upstream_order(self):
        assert self.policy.union_filetypes() == ["flac", "mp3"]

    def test_no_proof_required(self):
        assert not self.policy.requires_proof("Album")
        assert not self.policy.requires_proof("Single")

    def test_defaults(self):
        assert self.policy.proof_exts == {"log", "cue"}
        assert self.policy.max_probes == 30

    def test_empty_config_falls_back(self):
        empty = policy.TypePolicy(make_config(""))
        assert empty.union_filetypes() == ["flac", "mp3"]
        assert empty.processes("Broadcast")

    def test_explicit_base_ladder_wins(self):
        explicit = policy.TypePolicy(make_config(STOCK_CONFIG), ["flac 24/192", "flac"])
        assert explicit.ladder_for("Album") == ["flac 24/192", "flac"]
        assert explicit.union_filetypes() == ["flac 24/192", "flac"]


class TestTypePolicyFork:
    def setup_method(self):
        self.policy = policy.TypePolicy(make_config(FORK_CONFIG))

    def test_processed_types(self):
        assert self.policy.processes("Album")
        assert self.policy.processes("EP")
        assert self.policy.processes("single")  # case-insensitive
        assert not self.policy.processes("Broadcast")
        assert not self.policy.processes(None)

    def test_per_type_ladders(self):
        assert self.policy.ladder_for("Album") == ["flac 16/44.1", "flac"]
        assert self.policy.ladder_for("EP") == ["flac", "mp3 320"]
        assert self.policy.ladder_for("Single") == ["mp3 320", "mp3"]
        # Unknown types inherit the base ladder
        assert self.policy.ladder_for("Other") == ["flac", "mp3"]

    def test_union_preserves_ladder_order(self):
        assert self.policy.union_filetypes() == ["flac", "mp3", "flac 16/44.1", "mp3 320"]

    def test_proof_on_album_only(self):
        assert self.policy.requires_proof("Album")
        assert self.policy.requires_proof("album")
        assert not self.policy.requires_proof("EP")
        assert not self.policy.requires_proof("Single")

    def test_probe_cap(self):
        assert self.policy.max_probes == 12


PROOF_FOLDER = [
    {"filename": "01 - Intro.flac", "extension": ""},
    {"filename": "02 - Outro.FLAC", "extension": ""},
    {"filename": "rip.LOG", "extension": ""},
    {"filename": "album.Cue"},
    {"filename": "cover.jpg", "extension": ""},
    {"filename": "README"},
]


class TestFolderProofOk:
    def test_log_and_cue_present(self):
        assert policy.folder_proof_ok(PROOF_FOLDER, {"log", "cue"})

    def test_missing_cue_fails(self):
        folder = [f for f in PROOF_FOLDER if not f["filename"].lower().endswith(".cue")]
        assert not policy.folder_proof_ok(folder, {"log", "cue"})

    def test_case_insensitive(self):
        folder = [{"filename": "rip.Log"}, {"filename": "disc.CUE"}]
        assert policy.folder_proof_ok(folder, {"log", "cue"})

    def test_empty_extension_field_ignored(self):
        # slskd's 'extension' field is unreliable; only the filename counts
        folder = [{"filename": "rip.log", "extension": ""}, {"filename": "disc.cue", "extension": "flac"}]
        assert policy.folder_proof_ok(folder, {"log", "cue"})

    def test_empty_folder_fails(self):
        assert not policy.folder_proof_ok([], {"log", "cue"})


class TestFolderAudioClean:
    def test_flac_only_folder_is_clean(self):
        assert policy.folder_audio_clean(PROOF_FOLDER)

    def test_stray_mp3_fails(self):
        assert not policy.folder_audio_clean(PROOF_FOLDER + [{"filename": "bonus.mp3", "extension": ""}])

    def test_stray_audio_case_insensitive(self):
        assert not policy.folder_audio_clean([{"filename": "01.flac"}, {"filename": "02.M4A"}])

    def test_misleading_extension_field_ignored(self):
        # Filename says txt even though the metadata claims mp3
        assert policy.folder_audio_clean([{"filename": "notes.txt", "extension": "mp3"}])

    def test_non_audio_files_allowed(self):
        folder = [{"filename": "cover.jpg"}, {"filename": "rip.log"}, {"filename": "folder.nfo"}]
        assert policy.folder_audio_clean(folder)

    def test_wav_and_ape_fail(self):
        assert not policy.folder_audio_clean([{"filename": "01.wav"}])
        assert not policy.folder_audio_clean([{"filename": "01.ape"}])

    def test_extended_audio_extensions_fail(self):
        for ext in ("dff", "mpc", "mp2", "m4b", "shn", "tta"):
            assert not policy.folder_audio_clean([{"filename": f"01.{ext}"}]), ext


class TestFileExt:
    def test_full_path(self):
        assert policy.file_ext("dir\\subdir\\01 - Track.FLAC") == "flac"

    def test_basename(self):
        assert policy.file_ext("rip.log") == "log"

    def test_no_extension(self):
        assert policy.file_ext("README") == ""
        assert policy.file_ext("dir\\README") == ""


class TestStagedStore:
    def test_record_and_check(self, tmp_path):
        store = str(tmp_path / "staged_albums.json")
        policy.record_staged(store, 42, "Test Album")
        assert policy.is_staged(store, 42, 7)
        assert not policy.is_staged(store, 99, 7)

    def test_clear(self, tmp_path):
        store = str(tmp_path / "staged_albums.json")
        policy.record_staged(store, 42, "Test Album")
        policy.clear_staged(store, 42)
        assert not policy.is_staged(store, 42, 7)

    def test_stale_entry_expires(self, tmp_path):
        store = str(tmp_path / "staged_albums.json")
        policy.save_staged(store, {"42": {"album_id": 42, "title": "Old", "staged_at": "2020-01-01T00:00:00"}})
        assert not policy.is_staged(store, 42, 7)

    def test_missing_file(self, tmp_path):
        store = str(tmp_path / "missing.json")
        assert not policy.is_staged(store, 42, 7)
        policy.clear_staged(store, 42)  # must not create the file
        assert not (tmp_path / "missing.json").exists()

    def test_list_shaped_file_resets(self, tmp_path):
        # Valid JSON of the wrong shape must not crash is_staged/record_staged
        store = tmp_path / "staged_albums.json"
        store.write_text("[]")
        assert policy.load_staged(str(store)) == {}
        assert not policy.is_staged(str(store), 42, 7)
        policy.record_staged(str(store), 42, "Test Album")
        assert policy.is_staged(str(store), 42, 7)

    def test_garbage_bytes_reset(self, tmp_path):
        store = tmp_path / "staged_albums.json"
        store.write_bytes(b"\xff\xfe\x00garbage\x9c")
        assert policy.load_staged(str(store)) == {}
        assert not policy.is_staged(str(store), 42, 7)

    def test_non_dict_entry_dropped(self, tmp_path):
        store = tmp_path / "staged_albums.json"
        store.write_text('{"42": "old", "7": {"album_id": 7, "title": "Keeper", "staged_at": "2020-01-01T00:00:00"}}')
        assert policy.load_staged(str(store)) == {"7": {"album_id": 7, "title": "Keeper", "staged_at": "2020-01-01T00:00:00"}}
        assert not policy.is_staged(str(store), 42, 7)
        policy.record_staged(str(store), 42, "Test Album")
        assert policy.is_staged(str(store), 42, 7)


class RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


class TestValidate:
    def test_lossy_rung_on_proof_type_warns(self):
        config = make_config("""
[Search Settings]
allowed_filetypes = flac,mp3
require_proof_album_types = Album
album_allowed_filetypes = flac,mp3 320
""")
        log = RecordingLogger()
        policy.TypePolicy(config).validate(log)
        assert len(log.warnings) == 1
        assert "album" in log.warnings[0]
        assert "mp3 320" in log.warnings[0]

    def test_proof_type_inheriting_lossy_base_ladder_warns(self):
        config = make_config("""
[Search Settings]
allowed_filetypes = flac,mp3
require_proof_album_types = Album
""")
        log = RecordingLogger()
        policy.TypePolicy(config).validate(log)
        assert len(log.warnings) == 1
        assert "mp3" in log.warnings[0]

    def test_lossless_proof_ladder_no_warning(self):
        # FORK_CONFIG: Album is proof-gated with a flac-only ladder; the lossy
        # EP/Single rungs are not proof-gated so they must not warn.
        log = RecordingLogger()
        policy.TypePolicy(make_config(FORK_CONFIG)).validate(log)
        assert log.warnings == []

    def test_no_proof_types_no_warning(self):
        log = RecordingLogger()
        policy.TypePolicy(make_config(STOCK_CONFIG)).validate(log)
        assert log.warnings == []
