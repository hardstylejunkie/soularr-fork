import configparser
import json
import os
import time

from soularr_fork import policy
from soularr_fork.orphans import OrphanSweep


class RecordingLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def error(self, message):
        self.errors.append(message)


class StubLidarr:
    def __init__(self, artists=None, albums_by_artist=None):
        self.artists = artists or []
        self.albums_by_artist = albums_by_artist or {}
        self.get_artist_calls = 0
        self.get_album_calls = []

    def get_artist(self):
        self.get_artist_calls += 1
        return [dict(artist) for artist in self.artists]

    def get_album(self, artistId=None, **kwargs):
        self.get_album_calls.append(artistId)
        return [dict(album) for album in self.albums_by_artist.get(artistId, [])]


class StubPromoter:
    def __init__(self, result=(True, "/music/dest")):
        self.calls = []
        self.result = result

    def promote(self, album, folder, name):
        self.calls.append((album, folder, name))
        return self.result


def make_policy(text=None):
    config = configparser.ConfigParser()
    config.read_string(text or "[Search Settings]\nallowed_filetypes = flac,mp3\n")
    return policy.TypePolicy(config)


PROOF_CONFIG = """
[Search Settings]
allowed_filetypes = flac
require_proof_album_types = Album
proof_files = log,cue
"""


def make_album(album_id=11, artist_id=3, artist_name="Adele", title="21",
               album_type="Album", track_count=1, release_date="2011-01-24T00:00:00Z"):
    return {
        "id": album_id,
        "artistId": artist_id,
        "title": title,
        "albumType": album_type,
        "releaseDate": release_date,
        "statistics": {"trackCount": track_count},
        "artist": {"artistName": artist_name, "path": f"/music/{artist_name}"},
    }


def adele_lidarr(track_count=1, album_type="Album"):
    album = make_album(track_count=track_count, album_type=album_type)
    return StubLidarr(artists=[{"id": 3, "artistName": "Adele"}], albums_by_artist={3: [album]})


def make_folder(staging, name, files=("01.flac",), age_minutes=60):
    folder = staging / name
    folder.mkdir(parents=True)
    for rel in files:
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    stamp = time.time() - age_minutes * 60
    os.utime(folder, (stamp, stamp))
    return folder


# Minimal real FLAC so flac_bit_depth reads a depth for composed-name tests.
def flac_bytes(depth):
    info = bytearray(34)
    bits = depth - 1
    info[12] = (bits >> 4) & 1
    info[13] = (bits & 0xF) << 4
    return b"fLaC" + bytes([0x80, 0x00, 0x00, 34]) + bytes(info)


def make_sweep(tmp_path, lidarr=None, promoter=None, type_policy=None, tag_reader=None,
               active=frozenset(), gate_proof=True, min_age_minutes=0, max_attempts=4, logger=None):
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    return OrphanSweep(
        lidarr=lidarr if lidarr is not None else StubLidarr(),
        logger=logger or RecordingLogger(),
        staging_dir=str(staging),
        promoter=promoter if promoter is not None else StubPromoter(),
        type_policy=type_policy or make_policy(),
        tag_reader=tag_reader or (lambda path: None),
        active_folders_fn=lambda: set(active),
        state_path=str(tmp_path / "orphan_sweep.json"),
        gate_proof=gate_proof,
        min_age_minutes=min_age_minutes,
        max_attempts=max_attempts,
    )


def load_state(tmp_path):
    path = tmp_path / "orphan_sweep.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# --- identification ---------------------------------------------------------

def test_tag_reader_wins_over_unparseable_folder_name(tmp_path):
    make_folder(tmp_path / "staging", "some random slskd folder")
    promoter = StubPromoter()
    reads = []

    def tag_reader(path):
        reads.append(path)
        return {"artist": "Adele", "album": "21"}

    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter, tag_reader=tag_reader)
    results = sweep.sweep()

    assert results == [("some random slskd folder", "promoted")]
    assert len(promoter.calls) == 1
    assert reads and reads[0].endswith("01.flac")


def test_nameparse_fallback_when_tags_unreadable(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)")
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter, tag_reader=lambda path: None)

    results = sweep.sweep()

    assert results == [("Adele - 21 (2011)", "promoted")]
    assert len(promoter.calls) == 1


def test_unidentified_counts_an_attempt(tmp_path):
    make_folder(tmp_path / "staging", "randomfolder")
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, promoter=promoter, tag_reader=lambda path: None)

    results = sweep.sweep()

    assert results == [("randomfolder", "unidentified")]
    assert promoter.calls == []
    state = load_state(tmp_path)
    assert state["randomfolder"]["attempts"] == 1
    assert state["randomfolder"]["last_outcome"] == "unidentified"


def test_feat_clause_stripped_for_artist_match(tmp_path):
    album = make_album(artist_id=9, artist_name="2Pac", title="Me Against the World",
                       release_date="1995-03-14T00:00:00Z")
    lidarr = StubLidarr(artists=[{"id": 9, "artistName": "2Pac"}], albums_by_artist={9: [album]})
    make_folder(tmp_path / "staging", "2Pac - Me Against the World (1995)")
    promoter = StubPromoter()
    sweep = make_sweep(
        tmp_path, lidarr=lidarr, promoter=promoter,
        tag_reader=lambda path: {"artist": "2Pac feat. Nate Dogg", "album": "Me Against the World"},
    )

    results = sweep.sweep()

    assert results == [("2Pac - Me Against the World (1995)", "promoted")]
    assert promoter.calls[0][0]["artistId"] == 9


# --- skips ------------------------------------------------------------------

def test_active_download_folder_skipped(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)")
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter,
                       active={"Adele - 21 (2011)"})

    results = sweep.sweep()

    assert results == [("Adele - 21 (2011)", "skipped (active download)")]
    assert promoter.calls == []
    assert load_state(tmp_path) == {}  # skips never count attempts


def test_active_nested_disc_folder_skips_parent(tmp_path):
    make_folder(tmp_path / "staging", "Boxset", files=("CD1/01.flac", "CD2/01.flac"))
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, promoter=promoter, active={"CD2"})

    results = sweep.sweep()

    assert results == [("Boxset", "skipped (active download)")]
    assert promoter.calls == []


def test_min_age_skip(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)", age_minutes=1)
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter, min_age_minutes=15)

    results = sweep.sweep()

    assert len(results) == 1
    assert results[0][0] == "Adele - 21 (2011)"
    assert results[0][1].startswith("skipped (age")
    assert promoter.calls == []
    assert load_state(tmp_path) == {}


def test_dot_folders_ignored_entirely(tmp_path):
    staging = tmp_path / "staging"
    make_folder(staging, ".partial")
    (staging / "loose-file.txt").write_text("not a folder")
    sweep = make_sweep(tmp_path)

    assert sweep.sweep() == []


# --- attempt cap + state persistence ----------------------------------------

def test_attempt_cap_honored_and_state_persists_across_instances(tmp_path):
    make_folder(tmp_path / "staging", "randomfolder")

    for expected in (1, 2):
        results = make_sweep(tmp_path, max_attempts=2).sweep()
        assert results == [("randomfolder", "unidentified")]
        assert load_state(tmp_path)["randomfolder"]["attempts"] == expected

    # Third instance: cap reached, no processing, attempts unchanged.
    promoter = StubPromoter()
    results = make_sweep(tmp_path, promoter=promoter, max_attempts=2).sweep()
    assert results == [("randomfolder", "skipped (attempt cap 2/2)")]
    assert promoter.calls == []
    assert load_state(tmp_path)["randomfolder"]["attempts"] == 2


def test_state_pruned_for_vanished_folders(tmp_path):
    (tmp_path / "orphan_sweep.json").write_text(json.dumps({"gone folder": {"attempts": 3}}))
    make_folder(tmp_path / "staging", "randomfolder")

    make_sweep(tmp_path).sweep()

    state = load_state(tmp_path)
    assert "gone folder" not in state
    assert state["randomfolder"]["attempts"] == 1


# --- completeness -----------------------------------------------------------

def test_completeness_mismatch(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)", files=("01.flac", "02.flac"))
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(track_count=12), promoter=promoter)

    results = sweep.sweep()

    assert results == [("Adele - 21 (2011)", "incomplete (2/12)")]
    assert promoter.calls == []
    assert load_state(tmp_path)["Adele - 21 (2011)"]["attempts"] == 1


def test_unmatched_album_title(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 19 (2008)")
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter)

    results = sweep.sweep()

    assert results[0][1].startswith("unmatched")
    assert promoter.calls == []


def test_multiple_title_hits_prefer_matching_track_count(tmp_path):
    albums = [
        make_album(album_id=1, track_count=12),
        make_album(album_id=2, track_count=3),
    ]
    lidarr = StubLidarr(artists=[{"id": 3, "artistName": "Adele"}], albums_by_artist={3: albums})
    make_folder(tmp_path / "staging", "Adele - 21 (2011)", files=("01.flac", "02.flac", "03.flac"))
    promoter = StubPromoter()

    make_sweep(tmp_path, lidarr=lidarr, promoter=promoter).sweep()

    assert len(promoter.calls) == 1
    assert promoter.calls[0][0]["id"] == 2


# --- proof gate -------------------------------------------------------------

def test_gate_failed_without_proof_or_scene_name(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)")  # no log/cue, no FLAC token in name
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter,
                       type_policy=make_policy(PROOF_CONFIG))

    results = sweep.sweep()

    assert results == [("Adele - 21 (2011)", "gate-failed")]
    assert promoter.calls == []
    assert load_state(tmp_path)["Adele - 21 (2011)"]["attempts"] == 1


def test_scene_named_folder_passes_gate_without_proof(tmp_path):
    make_folder(tmp_path / "staging", "Adele-21-WEB-FLAC-2011-GRP")
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter,
                       type_policy=make_policy(PROOF_CONFIG), tag_reader=lambda path: None)

    results = sweep.sweep()

    assert results == [("Adele-21-WEB-FLAC-2011-GRP", "promoted")]
    _, _, name = promoter.calls[0]
    assert name == "Adele - 21 (2011) [WEB][FLAC]-GRP"


def test_gate_skipped_for_non_proof_album_type(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)")
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(album_type="EP"), promoter=promoter,
                       type_policy=make_policy(PROOF_CONFIG))

    assert sweep.sweep() == [("Adele - 21 (2011)", "promoted")]
    assert len(promoter.calls) == 1


# --- promote path -----------------------------------------------------------

def test_successful_promote_composes_name_and_clears_state(tmp_path):
    folder = make_folder(tmp_path / "staging", "Adele - 21 (2011)", files=("rip.log", "rip.cue"))
    (folder / "01.flac").write_bytes(flac_bytes(16))
    stamp = time.time() - 3600
    os.utime(folder, (stamp, stamp))
    (tmp_path / "orphan_sweep.json").write_text(json.dumps({"Adele - 21 (2011)": {"attempts": 2}}))
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter,
                       type_policy=make_policy(PROOF_CONFIG))

    results = sweep.sweep()

    assert results == [("Adele - 21 (2011)", "promoted")]
    album, promoted_folder, name = promoter.calls[0]
    assert album["id"] == 11
    assert promoted_folder == str(folder)
    # log+cue proof => [CD] + [LOG+CUE]; depth from the real FLAC's STREAMINFO
    assert name == "Adele - 21 (2011) [CD][FLAC 16bit][LOG+CUE]"
    assert load_state(tmp_path) == {}


def test_failed_promote_counts_attempt(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)")
    promoter = StubPromoter(result=(False, "destination exists"))
    sweep = make_sweep(tmp_path, lidarr=adele_lidarr(), promoter=promoter)

    results = sweep.sweep()

    assert results == [("Adele - 21 (2011)", "promote failed: destination exists")]
    assert load_state(tmp_path)["Adele - 21 (2011)"]["attempts"] == 1


# --- multi-disc -------------------------------------------------------------

def test_multi_disc_audio_counted_recursively(tmp_path):
    make_folder(
        tmp_path / "staging",
        "Bob Marley - Songs of Freedom (2001)",
        files=("CD1/01.flac", "CD1/02.flac", "CD2/01.flac", "CD2/02.flac", "cover.jpg"),
    )
    album = make_album(album_id=77, artist_id=8, artist_name="Bob Marley",
                       title="Songs of Freedom", track_count=4,
                       release_date="2001-05-01T00:00:00Z")
    lidarr = StubLidarr(artists=[{"id": 8, "artistName": "Bob Marley"}], albums_by_artist={8: [album]})
    promoter = StubPromoter()
    sweep = make_sweep(tmp_path, lidarr=lidarr, promoter=promoter)

    results = sweep.sweep()

    assert results == [("Bob Marley - Songs of Freedom (2001)", "promoted")]
    _, _, name = promoter.calls[0]
    # No proof, no readable depth at the folder root: WEB + bare FLAC token
    assert name == "Bob Marley - Songs of Freedom (2001) [WEB][FLAC]"


# --- fail-open --------------------------------------------------------------

def test_one_folder_raising_does_not_stop_the_sweep(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)")
    make_folder(tmp_path / "staging", "Boom - Bang (2020)")

    class ExplodingPromoter(StubPromoter):
        def promote(self, album, folder, name):
            super().promote(album, folder, name)
            if "Boom" in folder:
                raise RuntimeError("promoter exploded")
            return (True, folder)

    albums = {
        3: [make_album()],
        4: [make_album(album_id=12, artist_id=4, artist_name="Boom", title="Bang",
                       release_date="2020-01-01T00:00:00Z")],
    }
    lidarr = StubLidarr(
        artists=[{"id": 3, "artistName": "Adele"}, {"id": 4, "artistName": "Boom"}],
        albums_by_artist=albums,
    )
    logger = RecordingLogger()
    promoter = ExplodingPromoter()
    sweep = make_sweep(tmp_path, lidarr=lidarr, promoter=promoter, logger=logger)

    results = dict(sweep.sweep())

    assert results["Adele - 21 (2011)"] == "promoted"
    assert results["Boom - Bang (2020)"].startswith("error:")
    assert any("Boom" in warning for warning in logger.warnings)
    assert load_state(tmp_path)["Boom - Bang (2020)"]["attempts"] == 1


def test_artist_list_fetched_once_per_sweep(tmp_path):
    make_folder(tmp_path / "staging", "Adele - 21 (2011)")
    make_folder(tmp_path / "staging", "Adele - 19 (2008)")
    lidarr = adele_lidarr()
    sweep = make_sweep(tmp_path, lidarr=lidarr)

    sweep.sweep()

    assert lidarr.get_artist_calls == 1
    assert lidarr.get_album_calls == [3]  # album list cached per artist too
