from datetime import datetime

from soularr_fork.promote import Promoter, compose_folder_name, flac_bit_depth, medium_from_release


class RecordingLogger:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)


class StubLidarr:
    """get_track_file returns successive batches (snapshot, then verify);
    the last batch repeats if called again."""

    def __init__(self, trackfile_batches=None, post_command_error=None, command_status="completed"):
        self.trackfile_batches = [list(batch) for batch in (trackfile_batches or [])]
        self.post_command_error = post_command_error
        self.command_status = command_status
        self.commands = []
        self.track_file_calls = []
        self.deleted_track_files = []

    def get_track_file(self, albumId=None, **kwargs):
        self.track_file_calls.append(albumId)
        if not self.trackfile_batches:
            return []
        batch = self.trackfile_batches[0] if len(self.trackfile_batches) == 1 else self.trackfile_batches.pop(0)
        return [dict(file) for file in batch]

    def delete_track_file(self, ids_):
        self.deleted_track_files.append(list(ids_) if isinstance(ids_, list) else [ids_])
        return {}

    def post_command(self, name=None, **kwargs):
        self.commands.append((name, kwargs))
        if self.post_command_error is not None:
            raise self.post_command_error
        return {"id": 7}

    def get_command(self, command_id):
        return {"status": self.command_status}


def make_album(artist_path, album_id=11, artist_id=3, artist_name="Adele", title="21"):
    return {
        "id": album_id,
        "artistId": artist_id,
        "title": title,
        "artist": {"artistName": artist_name, "path": str(artist_path)},
    }


# --- compose_folder_name ----------------------------------------------------

def test_compose_proof_disambiguation_example():
    assert (
        compose_folder_name("10 Years", "The Autumn Effect", 2005, "BMG Club Edition", "CD", 16, True, None)
        == "10 Years - The Autumn Effect (2005) [BMG Club Edition] [CD][FLAC 16bit][LOG+CUE]"
    )


def test_compose_release_group_example():
    assert compose_folder_name("Adele", "21", 2011, None, "CD", 16, False, "GRMFLAC") == "Adele - 21 (2011) [CD][FLAC 16bit]-GRMFLAC"


def test_compose_web_24bit_example():
    assert compose_folder_name("Styx", "Paradise Theatre", 1981, None, "WEB", 24, False, None) == "Styx - Paradise Theatre (1981) [WEB][FLAC 24bit]"


def test_compose_sanitizes_invalid_characters():
    assert compose_folder_name("AC/DC", 'Who Made Who?', 1986, None, "CD", 16, False, None) == "ACDC - Who Made Who (1986) [CD][FLAC 16bit]"


def test_compose_disambiguation_gets_brackets_and_sanitation():
    assert (
        compose_folder_name("Nirvana", "Nevermind", 1991, 'Deluxe "Fan" Edition', "CD", 16, True, "PERFECT")
        == "Nirvana - Nevermind (1991) [Deluxe Fan Edition] [CD][FLAC 16bit][LOG+CUE]-PERFECT"
    )


def test_compose_unknown_depth_emits_bare_flac_token():
    assert compose_folder_name("Adele", "21", 2011, None, "CD", None, False, None) == "Adele - 21 (2011) [CD][FLAC]"


# --- flac_bit_depth ---------------------------------------------------------

def streaminfo_bytes(depth):
    info = bytearray(34)
    bits = depth - 1
    info[12] = (bits >> 4) & 1
    info[13] = (bits & 0xF) << 4
    return bytes(info)


def flac_bytes(depth):
    # fLaC magic + last-block STREAMINFO header (type 0, length 34) + block
    return b"fLaC" + bytes([0x80, 0x00, 0x00, 34]) + streaminfo_bytes(depth)


def id3_wrapped(payload, tag_size=20):
    header = b"ID3" + bytes([4, 0, 0]) + bytes([0, 0, 0, tag_size])
    return header + b"\x00" * tag_size + payload


def test_flac_bit_depth_16(tmp_path):
    (tmp_path / "01 track.flac").write_bytes(flac_bytes(16))
    assert flac_bit_depth(str(tmp_path)) == 16


def test_flac_bit_depth_24(tmp_path):
    (tmp_path / "01 track.flac").write_bytes(flac_bytes(24))
    assert flac_bit_depth(str(tmp_path)) == 24


def test_flac_bit_depth_id3_wrapped(tmp_path):
    (tmp_path / "01 track.FLAC").write_bytes(id3_wrapped(flac_bytes(16)))
    assert flac_bit_depth(str(tmp_path)) == 16


def test_flac_bit_depth_garbage_returns_none(tmp_path):
    (tmp_path / "01 track.flac").write_bytes(b"not a flac file at all")
    assert flac_bit_depth(str(tmp_path)) is None


def test_flac_bit_depth_no_flac_files_returns_none(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(b"jpg")
    assert flac_bit_depth(str(tmp_path)) is None


def test_flac_bit_depth_missing_folder_returns_none(tmp_path):
    assert flac_bit_depth(str(tmp_path / "nope")) is None


# --- medium_from_release ----------------------------------------------------

def test_medium_matrix():
    assert medium_from_release({"format": "CD"}, None) == "CD"
    assert medium_from_release({"format": "2xCD"}, None) == "CD"
    assert medium_from_release({"format": "Digital Media"}, None) == "WEB"
    assert medium_from_release({"format": "Vinyl"}, None) == "Vinyl"
    assert medium_from_release({"format": '12" Vinyl'}, None) == "Vinyl"
    # Release format wins over the parsed source
    assert medium_from_release({"format": "CD"}, "WEB") == "CD"
    # No usable format: parsed source from the original slskd folder name
    assert medium_from_release(None, "CD") == "CD"
    assert medium_from_release(None, "2CD") == "CD"
    assert medium_from_release(None, "WEB") == "WEB"
    assert medium_from_release({"format": "Cassette"}, "WEB") == "WEB"
    # Nothing at all: proof implies a CD rip, otherwise WEB
    assert medium_from_release(None, None, has_proof=True) == "CD"
    assert medium_from_release(None, None, has_proof=False) == "WEB"
    assert medium_from_release({}, None) == "WEB"


# --- Promoter ---------------------------------------------------------------

def make_staged(tmp_path, name="Adele - 21 (2011)"):
    staged = tmp_path / "staging" / name
    staged.mkdir(parents=True)
    (staged / "01.flac").write_bytes(b"new audio")
    return staged


def test_promote_success_moves_and_recycles_old_folder(tmp_path):
    artist_dir = tmp_path / "music" / "Adele"
    artist_dir.mkdir(parents=True)
    old_dir = artist_dir / "Adele - 21 (2011) [WEB][FLAC]"
    old_dir.mkdir()
    (old_dir / "01.flac").write_bytes(b"old audio")
    staged = make_staged(tmp_path)
    name = "Adele - 21 (2011) [CD][FLAC 16bit]-GRMFLAC"
    new_dir = artist_dir / name
    recycle = tmp_path / "recycle"
    lidarr = StubLidarr(
        trackfile_batches=[
            [{"path": str(old_dir / "01.flac")}],
            [{"path": str(new_dir / "01.flac")}],
        ]
    )
    promoter = Promoter(lidarr, RecordingLogger(), str(recycle), verify_timeout=0)

    ok, detail = promoter.promote(make_album(artist_dir), str(staged), name)

    assert ok is True
    assert detail == str(new_dir)
    assert (new_dir / "01.flac").read_bytes() == b"new audio"
    assert not staged.exists()
    assert lidarr.commands == [("RefreshArtist", {"artistId": 3})]
    day_dir = recycle / ("replaced-" + datetime.now().strftime("%Y%m%d"))
    assert (day_dir / "Adele - 21 (2011) [WEB][FLAC]" / "01.flac").read_bytes() == b"old audio"
    assert not old_dir.exists()


def test_promote_recycle_collision_prefixes_artist(tmp_path):
    artist_dir = tmp_path / "music" / "Adele"
    artist_dir.mkdir(parents=True)
    old_dir = artist_dir / "21 [WEB][FLAC]"
    old_dir.mkdir()
    (old_dir / "01.flac").write_bytes(b"old audio")
    staged = make_staged(tmp_path)
    name = "Adele - 21 (2011) [CD][FLAC 16bit]"
    new_dir = artist_dir / name
    recycle = tmp_path / "recycle"
    day_dir = recycle / ("replaced-" + datetime.now().strftime("%Y%m%d"))
    (day_dir / "21 [WEB][FLAC]").mkdir(parents=True)  # someone already recycled that name today
    lidarr = StubLidarr(
        trackfile_batches=[
            [{"path": str(old_dir / "01.flac")}],
            [{"path": str(new_dir / "01.flac")}],
        ]
    )
    promoter = Promoter(lidarr, RecordingLogger(), str(recycle), verify_timeout=0)

    ok, _ = promoter.promote(make_album(artist_dir), str(staged), name)

    assert ok is True
    assert (day_dir / "Adele - 21 [WEB][FLAC]" / "01.flac").read_bytes() == b"old audio"


def test_promote_verify_failure_leaves_folder_and_recycles_nothing(tmp_path):
    artist_dir = tmp_path / "music" / "Adele"
    artist_dir.mkdir(parents=True)
    old_dir = artist_dir / "Adele - 21 (2011) [WEB][FLAC]"
    old_dir.mkdir()
    (old_dir / "01.flac").write_bytes(b"old audio")
    staged = make_staged(tmp_path)
    name = "Adele - 21 (2011) [CD][FLAC 16bit]"
    new_dir = artist_dir / name
    recycle = tmp_path / "recycle"
    # After refresh Lidarr still only knows the old folder: verify must fail
    lidarr = StubLidarr(trackfile_batches=[[{"path": str(old_dir / "01.flac")}]])
    logger = RecordingLogger()
    promoter = Promoter(lidarr, logger, str(recycle), verify_timeout=0)

    ok, reason = promoter.promote(make_album(artist_dir), str(staged), name)

    assert ok is False
    assert "no trackfiles" in reason
    assert (new_dir / "01.flac").exists()  # moved folder stays put for the next rescan
    assert not staged.exists()
    # old copy was retired BEFORE the refresh; restorable from the recycle bin
    assert not old_dir.exists()
    recycled = list(recycle.rglob("01.flac"))
    assert len(recycled) == 1
    assert logger.warnings


def test_promote_retires_old_copy_before_refresh(tmp_path):
    artist_dir = tmp_path / "music" / "Adele"
    artist_dir.mkdir(parents=True)
    old_dir = artist_dir / "Adele - 21 (2011) [WEB][FLAC]"
    old_dir.mkdir()
    (old_dir / "01.flac").write_bytes(b"old audio")
    staged = make_staged(tmp_path)
    name = "Adele - 21 (2011) [CD][FLAC 16bit]"
    new_dir = artist_dir / name
    recycle = tmp_path / "recycle"

    class OrderCheckingStub(StubLidarr):
        def post_command(self, name=None, **kwargs):
            # by refresh time the old copy must already be retired
            assert not old_dir.exists()
            assert self.deleted_track_files == [[41]]
            return super().post_command(name=name, **kwargs)

    lidarr = OrderCheckingStub(
        trackfile_batches=[
            [{"id": 41, "path": str(old_dir / "01.flac")}],
            [{"id": 42, "path": str(new_dir / "01.flac")}],
        ]
    )
    promoter = Promoter(lidarr, RecordingLogger(), str(recycle), verify_timeout=0)

    ok, _ = promoter.promote(make_album(artist_dir), str(staged), name)

    assert ok is True
    assert not old_dir.exists()
    assert len(list(recycle.rglob("01.flac"))) == 1
    assert (new_dir / "01.flac").exists()


def test_promote_creates_missing_artist_directory(tmp_path):
    artist_dir = tmp_path / "music" / "Brand New Artist"
    staged = make_staged(tmp_path, "Brand New Artist - Debut (2026)")
    name = "Brand New Artist - Debut (2026) [WEB][FLAC 16bit]"
    new_dir = artist_dir / name
    lidarr = StubLidarr(trackfile_batches=[[], [{"path": str(new_dir / "01.flac")}]])
    promoter = Promoter(lidarr, RecordingLogger(), str(tmp_path / "recycle"))

    ok, detail = promoter.promote(make_album(artist_dir, artist_name="Brand New Artist"), str(staged), name)

    assert ok is True
    assert detail == str(new_dir)
    assert (new_dir / "01.flac").exists()


def test_promote_refresh_exception_leaves_folder_at_destination(tmp_path):
    artist_dir = tmp_path / "music" / "Adele"
    artist_dir.mkdir(parents=True)
    staged = make_staged(tmp_path)
    name = "Adele - 21 (2011) [CD][FLAC 16bit]"
    new_dir = artist_dir / name
    lidarr = StubLidarr(trackfile_batches=[[]], post_command_error=RuntimeError("lidarr down"))
    logger = RecordingLogger()
    promoter = Promoter(lidarr, logger, str(tmp_path / "recycle"))

    ok, reason = promoter.promote(make_album(artist_dir), str(staged), name)

    assert ok is False
    assert "refresh failed" in reason
    # The move already happened: folder stays at the destination with a warning
    assert (new_dir / "01.flac").exists()
    assert not staged.exists()
    assert any("folder left in place" in warning for warning in logger.warnings)


def test_promote_existing_destination_leaves_staged_folder(tmp_path):
    artist_dir = tmp_path / "music" / "Adele"
    artist_dir.mkdir(parents=True)
    name = "Adele - 21 (2011) [CD][FLAC 16bit]"
    (artist_dir / name).mkdir()
    staged = make_staged(tmp_path)
    lidarr = StubLidarr(trackfile_batches=[[]])
    promoter = Promoter(lidarr, RecordingLogger(), str(tmp_path / "recycle"))

    ok, reason = promoter.promote(make_album(artist_dir), str(staged), name)

    assert ok is False
    assert "destination exists" in reason
    assert (staged / "01.flac").exists()  # nothing was moved
    assert lidarr.commands == []


# --- audio_format_token / compose with non-FLAC payloads (2026-09-24) -------

from soularr_fork.promote import audio_format_token, mp3_format_token


def mp3_frame(bitrate_index=14, version_bits=3, mono=False, xing=False, id3=False):
    """One MPEG Layer III frame header (+ side info + optional Xing tag)."""
    b2 = 0xE0 | (version_bits << 3) | (1 << 1)          # sync tail, version, layer III
    b3 = (bitrate_index << 4) | (0 << 2)                  # bitrate index, 44.1 kHz
    b4 = (0x3 << 6) if mono else (0x0 << 6)               # channel mode
    side = (17 if mono else 32) if version_bits == 3 else (9 if mono else 17)
    frame = bytes([0xFF, b2, b3, b4]) + b"\x00" * side + (b"Xing" if xing else b"Info") + b"\x00" * 64
    if id3:
        frame = b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + frame
    return frame


def test_mp3_format_token_cbr_320(tmp_path):
    path = tmp_path / "01 - Song.mp3"
    path.write_bytes(mp3_frame())
    assert mp3_format_token(str(path)) == "MP3-320"


def test_mp3_format_token_skips_id3_and_reads_mpeg2_table(tmp_path):
    path = tmp_path / "01 - Song.mp3"
    path.write_bytes(mp3_frame(bitrate_index=13, version_bits=2, mono=True, id3=True))
    assert mp3_format_token(str(path)) == "MP3-144"


def test_mp3_format_token_xing_means_vbr(tmp_path):
    path = tmp_path / "01 - Song.mp3"
    path.write_bytes(mp3_frame(bitrate_index=9, xing=True))
    assert mp3_format_token(str(path)) == "MP3-VBR"


def test_mp3_format_token_unreadable_is_bare_mp3(tmp_path):
    path = tmp_path / "01 - Song.mp3"
    path.write_bytes(b"\x00" * 100)
    assert mp3_format_token(str(path)) == "MP3"


def test_audio_format_token_prefers_flac(tmp_path):
    (tmp_path / "01.flac").write_bytes(b"fLaC")
    (tmp_path / "02.mp3").write_bytes(mp3_frame())
    assert audio_format_token(str(tmp_path)) == "FLAC"


def test_audio_format_token_mp3_folder(tmp_path):
    (tmp_path / "Lifehouse_All In_01_All In.mp3").write_bytes(mp3_frame())
    (tmp_path / "folder.jpg").write_bytes(b"jpg")
    assert audio_format_token(str(tmp_path)) == "MP3-320"


def test_audio_format_token_other_lossy_uses_extension(tmp_path):
    (tmp_path / "01 - Song.m4a").write_bytes(b"\x00" * 16)
    assert audio_format_token(str(tmp_path)) == "M4A"


def test_audio_format_token_no_audio_keeps_legacy_flac(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(b"jpg")
    assert audio_format_token(str(tmp_path)) == "FLAC"
    assert audio_format_token(str(tmp_path / "missing")) == "FLAC"


def test_compose_mp3_payload_never_says_flac():
    # Lifehouse - All In (2012) was an MP3 single promoted as [CD][FLAC]; the
    # folder-parse custom formats then scored it Lossless.
    assert (
        compose_folder_name("Lifehouse", "All In", 2012, None, "CD", None, False, None, audio_format="MP3-320")
        == "Lifehouse - All In (2012) [CD][MP3-320]"
    )


def test_compose_non_flac_ignores_depth_and_keeps_proof_and_group():
    assert (
        compose_folder_name("Adele", "21", 2011, None, "WEB", 16, True, "GROUP", audio_format="MP3-VBR")
        == "Adele - 21 (2011) [WEB][MP3-VBR][LOG+CUE]-GROUP"
    )


def test_compose_flac_default_unchanged():
    assert compose_folder_name("Adele", "21", 2011, None, "CD", 16, False, None, audio_format="FLAC") == "Adele - 21 (2011) [CD][FLAC 16bit]"


def test_audio_format_token_looks_one_level_down_for_multi_disc(tmp_path):
    (tmp_path / "CD1").mkdir()
    (tmp_path / "CD1" / "01 - Song.mp3").write_bytes(mp3_frame())
    (tmp_path / "cover.jpg").write_bytes(b"jpg")
    assert audio_format_token(str(tmp_path)) == "MP3-320"
