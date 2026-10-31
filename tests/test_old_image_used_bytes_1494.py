"""#1494 -- an image captured before #1368 (``used_bytes: null`` on its NTFS
partitions) failed a room round on EVERY drawer: the agent's min-free check
(#1354) refuses "not checked", correctly (principle 5). Metal, 16/10.

Two halves:

1. The measurement exists on disk: each partition file is ``partclone -c |
   zstd``, and partclone's v2 image header (110 bytes, ``src/partclone.h``)
   carries ``usedblocks`` and ``block_size``. ``server.library.usedbackfill`` reads
   it and records ``used_bytes`` + ``used_bytes_source`` in manifest.json --
   derived, never invented: a header that is not provably valid leaves null.
2. An image whose min-free check cannot be evaluated is not deployable: the
   room refuses it (409, the agent's string) and the console disables it
   with that reason, instead of "size unknown" and a round that fails.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest
from conftest import Clock, MANIFEST_256, write_image

from server.library.images import ImageLibrary

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

UNCHECKED = "לא נבדק אם יישארו 20GB פנויים ב-C: — לא נמדד כמה תפוס בה (used_bytes חסר)"
WIN_SIZE = MANIFEST_256["partitions"][1]["size_bytes"]          # 254803968000


# --- the header, built independently of the production parser ---------------

def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
        table.append(c)
    return table


_TABLE = _crc_table()


def pc_crc32(data: bytes) -> int:
    """partclone's checksum.c: reflected table, seed 0xFFFFFFFF, no final XOR."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc


def header(*, used: int = 10_000_000, total: int = 62_207_999, block: int = 4096,
           magic: bytes = b"partclone-image", version: bytes = b"0002",
           endian: int = 0xC0DE, fs: bytes = b"NTFS", crc: int | None = None) -> bytes:
    # superBlockUsedBlocks (offset 68) differs from usedblocks (76) on purpose:
    # the bitmap count -- what partclone copied -- is the one that is read.
    body = struct.pack("<15sx14s4sH16sQQQQI18x", magic, b"0.3.33", version, endian,
                       fs, total * block, total, max(used - 1, 0), used, block)
    assert len(body) == 106
    return body + struct.pack("<I", pc_crc32(body) if crc is None else crc)


def test_partclones_crc_is_zlibs_without_the_final_inversion():
    for sample in (b"", b"a", b"partclone-image", bytes(range(256)) * 3):
        assert pc_crc32(sample) == zlib.crc32(sample) ^ 0xFFFFFFFF


# --- parse_header -------------------------------------------------------------

def test_a_valid_header_gives_used_blocks_and_block_size():
    from server.library.usedbackfill import parse_header
    got = parse_header(header(used=12345, total=99999, block=4096) + b"\xff" * 64)
    assert got == {"fs": "NTFS", "device_size": 99999 * 4096, "total_blocks": 99999,
                   "used_blocks": 12345, "block_size": 4096}


def _flip(raw: bytes, at: int) -> bytes:
    return raw[:at] + bytes([raw[at] ^ 0x01]) + raw[at + 1:]


@pytest.mark.parametrize("raw", [
    None, b"", header()[:109],
    header(magic=b"partclone-imagf"),
    header(version=b"0001"),
    header(endian=0xDEC0),
    _flip(header(), 80),                     # a used-blocks byte: CRC no longer matches
    _flip(header(), 106),                    # the CRC itself
    header(block=0),
    header(used=10, total=9),
], ids=["none", "empty", "short", "magic", "v1", "endian", "body-crc", "crc", "block0", "used>total"])
def test_anything_short_of_a_provably_valid_v2_header_is_none(raw):
    from server.library.usedbackfill import parse_header
    assert parse_header(raw) is None


# --- read_head ----------------------------------------------------------------

DRIP = ("import sys,time\n"
        "data=open(sys.argv[1],'rb').read()\n"
        "for i in range(0,len(data),7):\n"
        "    sys.stdout.buffer.write(data[i:i+7]); sys.stdout.buffer.flush()\n")
HANG = ("import sys,time\n"
        "sys.stdout.buffer.write(b'x'*10); sys.stdout.buffer.flush(); time.sleep(60)\n")


def test_read_head_collects_110_bytes_across_short_reads(tmp_path):
    from server.library.usedbackfill import read_head
    f = tmp_path / "stream"
    f.write_bytes(header() + b"\x00" * 5000)
    assert read_head(f, cmd=(sys.executable, "-c", DRIP)) == header()


def test_read_head_without_the_decompressor_is_none(tmp_path):
    from server.library.usedbackfill import read_head
    assert read_head(tmp_path / "x", cmd=(str(tmp_path / "no-such-zstd"),)) is None


def test_read_head_is_bounded_by_its_timeout(tmp_path):
    from server.library.usedbackfill import read_head
    start = time.monotonic()
    got = read_head(tmp_path / "x", cmd=(sys.executable, "-c", HANG), timeout=1)
    assert time.monotonic() - start < 15
    assert got == b"x" * 10                  # short -- parse_header rejects it


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd binary not installed")
def test_a_real_zstd_stream_round_trips(tmp_path):
    from server.library.usedbackfill import parse_header, read_head
    plain = header(used=777, total=1000, block=4096) + bytes(range(256)) * 4096
    packed = subprocess.run(["zstd", "-q", "-c"], input=plain, capture_output=True,
                            check=True).stdout
    f = tmp_path / "p3.win.pcl.zst"
    f.write_bytes(packed)
    assert parse_header(read_head(f))["used_blocks"] == 777


# --- the library: derive, record, backfill ------------------------------------

def old_image(image_id: str = "img_5790e4") -> dict:
    """MANIFEST_256 as captured before #1368: C: not measured."""
    esp, win = MANIFEST_256["partitions"]
    return {**MANIFEST_256, "id": image_id,
            "partitions": [esp, {**win, "used_bytes": None}]}


def on_disk(root: Path, image_id: str) -> dict:
    return json.loads((root / image_id / "manifest.json").read_text(encoding="utf-8"))


def reader(by_name: dict[str, bytes | None]):
    return lambda path: by_name.get(Path(path).name)


def test_backfill_writes_the_derived_value_and_its_source_to_the_manifest(tmp_path):
    from server.library.usedbackfill import SOURCE, backfill
    write_image(tmp_path, old_image())
    lib = ImageLibrary(tmp_path)
    used_blocks = 20_000_000

    results = backfill(lib, read=reader({"p3.win.pcl.zst": header(used=used_blocks)}))

    want = used_blocks * 4096
    assert results == [{"image_id": "img_5790e4", "written": {3: want}, "problems": []}]
    esp, win = on_disk(tmp_path, "img_5790e4")["partitions"]
    assert win["used_bytes"] == want and win["used_bytes_source"] == SOURCE == "partclone_header"
    assert esp["used_bytes"] == 31457280 and "used_bytes_source" not in esp   # measured: untouched
    assert lib.get("img_5790e4")["partitions"][1]["used_bytes"] == want      # still in the library


def test_a_measured_image_is_not_read_at_all(tmp_path):
    from server.library.usedbackfill import backfill
    write_image(tmp_path, MANIFEST_256)
    calls = []
    assert backfill(ImageLibrary(tmp_path), read=lambda p: calls.append(p)) == []
    assert calls == []


@pytest.mark.parametrize("raw,problem", [
    (b"not a partclone stream", "p3: no valid partclone v2 header"),
    (None, "p3: could not run zstd"),
    (header(used=WIN_SIZE // 4096 + 1, total=WIN_SIZE // 4096 + 10),
     f"p3: header says {(WIN_SIZE // 4096 + 1) * 4096} bytes used, more than the partition ({WIN_SIZE})"),
], ids=["garbage", "no-zstd", "bigger-than-partition"])
def test_what_cannot_be_derived_stays_null_and_is_reported(tmp_path, raw, problem):
    from server.library.usedbackfill import backfill
    write_image(tmp_path, old_image())
    before = (tmp_path / "img_5790e4" / "manifest.json").read_bytes()
    reported = []

    results = backfill(ImageLibrary(tmp_path), read=reader({"p3.win.pcl.zst": raw}),
                       report=reported.append)

    assert results == reported == [{"image_id": "img_5790e4", "written": {}, "problems": [problem]}]
    assert (tmp_path / "img_5790e4" / "manifest.json").read_bytes() == before


def test_a_missing_partition_file_is_reported_not_guessed(tmp_path):
    from server.library.usedbackfill import backfill
    write_image(tmp_path, old_image())
    (tmp_path / "img_5790e4" / "p3.win.pcl.zst").unlink()
    results = backfill(ImageLibrary(tmp_path), read=lambda p: header())
    assert results[0]["problems"] == ["p3: file missing or unsafe"]
    assert on_disk(tmp_path, "img_5790e4")["partitions"][1]["used_bytes"] is None


def test_one_bad_image_does_not_stop_the_next(tmp_path):
    from server.library.usedbackfill import backfill
    write_image(tmp_path, old_image("img_aaaaaa"))
    write_image(tmp_path, old_image("img_bbbbbb"))
    good = header(used=1000)

    def read(path):
        if "img_aaaaaa" in str(path):
            raise OSError("I/O error")
        return good

    results = {r["image_id"]: r for r in backfill(ImageLibrary(tmp_path), read=read)}
    assert results["img_aaaaaa"]["written"] == {} and "I/O error" in results["img_aaaaaa"]["problems"][0]
    assert results["img_bbbbbb"]["written"] == {3: 1000 * 4096}


def test_record_never_overwrites_a_measured_value(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    lib = ImageLibrary(tmp_path)
    assert lib.record_used_bytes(MANIFEST_256["id"], {1: 5, 3: 5}, "partclone_header") == []
    assert on_disk(tmp_path, MANIFEST_256["id"])["partitions"][1]["used_bytes"] == 84509376512


def test_record_refuses_a_value_that_would_make_the_manifest_invalid(tmp_path):
    """used_bytes > size_bytes is a manifest the library rejects -- writing it
    would make the image vanish. The recorder validates before replacing."""
    write_image(tmp_path, old_image())
    lib = ImageLibrary(tmp_path)
    assert lib.record_used_bytes("img_5790e4", {3: WIN_SIZE + 1}, "partclone_header") == []
    assert lib.get("img_5790e4") is not None
    assert on_disk(tmp_path, "img_5790e4")["partitions"][1]["used_bytes"] is None


def test_record_takes_only_a_known_source(tmp_path):
    write_image(tmp_path, old_image())
    with pytest.raises(ValueError):
        ImageLibrary(tmp_path).record_used_bytes("img_5790e4", {3: 1}, "guess")


def test_the_dry_run_prints_the_header_and_writes_nothing(tmp_path, capsys, monkeypatch):
    from server.library import usedbackfill
    write_image(tmp_path, old_image())
    folder = tmp_path / "img_5790e4"
    (folder / "p3.win.pcl.zst").write_bytes(header(used=5, total=9))
    (folder / "p1.esp.pcl.zst").write_bytes(b"garbage")
    before = (folder / "manifest.json").read_bytes()
    monkeypatch.setattr(usedbackfill, "read_head", lambda path: Path(path).read_bytes()[:110])

    assert usedbackfill.main([str(folder)]) == 1        # the ESP has no valid header
    out = capsys.readouterr().out.splitlines()
    assert out == ["p1 p1.esp.pcl.zst NO VALID HEADER",
                   f"p3 p3.win.pcl.zst fs=NTFS block_size=4096 used_blocks=5 total_blocks=9"
                   f" used_bytes=20480 manifest_used_bytes=null size_bytes={WIN_SIZE}"]
    assert (folder / "manifest.json").read_bytes() == before
    assert usedbackfill.main([]) == 2


# --- the server: min_free_unchecked, the room refusal, the startup pass -------

def test_unchecked_is_the_agents_string_and_only_for_an_unmeasured_system_partition():
    from server.library import minfree
    assert minfree.unchecked(old_image(), 20) == UNCHECKED
    assert minfree.unchecked(old_image(), 0) is None                    # rule off
    assert minfree.unchecked(MANIFEST_256, 20) is None                  # measured
    assert minfree.unchecked(old_image(), 20) == minfree.refusal(old_image(), 10**15, 20)


@pytest.fixture()
def room_server(tmp_path: Path):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_room_image_fit import CLONER1, DISK_500, hello_with_disks, open_round
    from test_sender import Recorder
    from server.console import users
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, old_image())
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080",
                     now_fn=Clock(), sender_runner=Recorder(), wol_send=lambda _b: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True,
                 check_policy=False)
    admin, anon = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert admin.post("/api/console/machines", json={
        "mac": CLONER1, "name": "1", "group_id": "grp_CLONERS"}).status_code == 200
    yield {"admin": admin, "anon": anon, "ctx": ctx, "images": images,
           "hello": lambda: hello_with_disks(anon, CLONER1, [DISK_500, DISK_500]),
           "open": lambda: open_round(admin, "img_5790e4")}
    ctx.sender.stop()


def test_the_console_list_carries_the_reason(room_server):
    listed = {i["id"]: i for i in room_server["admin"].get("/api/console/images").json()}
    assert listed["img_5790e4"]["min_free_unchecked"] == UNCHECKED
    assert listed["img_5790e4"]["min_target_bytes"] is None


def test_the_room_refuses_a_round_that_would_fail_on_every_drawer(room_server):
    room_server["hello"]()
    r = room_server["open"]()
    assert r.status_code == 409
    assert r.json()["detail"] == UNCHECKED
    assert room_server["admin"].get("/api/console/room").json()["round"] is None


def test_the_room_refuses_it_even_when_no_drawer_size_is_known(room_server):
    """No floor known is "the agent decides" for SIZE (#953) -- but this
    refusal does not depend on the drawer: every drawer refuses it."""
    from test_room_image_fit import CLONER1, hello_with_disks
    from server.rounds import room
    hello_with_disks(room_server["anon"], CLONER1, [0, 0])    # size not measured
    assert room.disk_floor(room_server["ctx"].conn) is None
    r = room_server["open"]()
    assert r.status_code == 409 and r.json()["detail"] == UNCHECKED


def test_after_the_backfill_the_same_image_opens(room_server):
    from server.library.usedbackfill import backfill
    lib = room_server["ctx"].library
    backfill(lib, read=reader({"p3.win.pcl.zst": header(used=20_000_000)}))
    room_server["hello"]()
    r = room_server["open"]()
    assert r.status_code == 200, r.text
    listed = {i["id"]: i for i in room_server["admin"].get("/api/console/images").json()}
    assert listed["img_5790e4"]["min_free_unchecked"] is None


def test_the_startup_pass_journals_what_it_wrote_and_what_it_could_not(tmp_path, monkeypatch):
    import functools

    import server.library.usedbackfill as ub
    from server.app import _backfill_used_bytes
    from server.db import connect
    write_image(tmp_path / "images", old_image("img_aaaaaa"))
    write_image(tmp_path / "images", old_image("img_bbbbbb"))
    (tmp_path / "images" / "img_aaaaaa" / "p3.win.pcl.zst").write_bytes(header(used=3))
    conn = connect(tmp_path / "db.sqlite")
    lib = ImageLibrary(tmp_path / "images")
    # the files are the decompressed stream here: no zstd binary needed
    monkeypatch.setattr(ub, "backfill", functools.partial(
        ub.backfill, read=lambda path: Path(path).read_bytes()[:110]))

    _backfill_used_bytes(conn, lib)

    rows = [(r["event"], r["detail"]) for r in conn.execute(
        "SELECT event, detail FROM journal ORDER BY id")]
    assert ("image_used_bytes_derived",
            "img_aaaaaa: used_bytes מכותרת partclone — p3=12288") in rows
    assert any(e == "image_used_bytes_underived" and d.startswith("img_bbbbbb: לא הושלם — ")
               for e, d in rows)


def test_only_main_turns_the_startup_pass_on():
    """A test runtime must not start a thread that runs zstd and journals at
    an unpredictable time; production must."""
    import inspect

    from server import app, main
    assert inspect.signature(app.create_runtime).parameters["backfill_used_bytes"].default is False
    assert "backfill_used_bytes=True" in inspect.getsource(main)


# --- #1641: a raw (dd) capture has no partclone header by format -------------

def windows_with_msr(image_id: str = "img_8da804") -> dict:
    """ESP + MSR + NTFS as the agent writes them: the MSR has ``fs: ""`` and
    ``used_bytes: null`` on purpose, and its file is a ``partclone.dd`` stream."""
    esp, win = MANIFEST_256["partitions"]
    msr = {"index": 2, "type_guid": "E3C9E316-0B5C-4DB8-817D-F92DF00215AE", "role": "msr",
           "fs": "", "start_sector": 206848, "size_bytes": 16777216, "used_bytes": None,
           "file": "p2.msr.pcl.zst", "sha256": "cc" * 32, "expandable": False}
    return {**MANIFEST_256, "id": image_id,
            "partitions": [{**esp, "used_bytes": None}, msr, {**win, "used_bytes": None}]}


def test_a_raw_capture_is_not_read_and_is_not_a_problem(tmp_path):
    """Only the NTFS partition -- which has no header although partclone wrote
    it -- is reported. The MSR is never even opened (#1641), and the ESP, whose
    header is valid, is still measured."""
    from server.library.usedbackfill import derive
    write_image(tmp_path, windows_with_msr())
    manifest = ImageLibrary(tmp_path).scan()["img_8da804"]
    read_names = []

    def read(path):
        read_names.append(Path(path).name)
        return header(used=7, fs=b"FAT32") if path.name.startswith("p1") else b"\0" * 110

    values, problems = derive(manifest, read=read)

    assert values == {1: 7 * 4096}
    assert problems == ["p3: no valid partclone v2 header"]
    assert "p2.msr.pcl.zst" not in read_names


def test_the_startup_pass_is_silent_for_a_windows_image_whose_only_gap_is_the_msr(tmp_path):
    from server.library.usedbackfill import backfill
    esp, msr, win = windows_with_msr()["partitions"]
    write_image(tmp_path, {**windows_with_msr(), "partitions": [{**esp, "used_bytes": 1}, msr,
                                                                {**win, "used_bytes": 1}]})
    assert backfill(ImageLibrary(tmp_path), read=lambda p: b"\0" * 110) == []


def test_every_filesystem_the_agent_streams_with_dd_is_skipped_by_its_own_mapping(tmp_path):
    """The rule is the agent's ``partclone_for_fs`` (``server.vm.vmdisk`` keeps a
    parity-tested copy), not a file name -- the file is called ``.pcl.zst`` in
    both cases. (``xfs`` is not a manifest filesystem today, so it cannot be
    tried here; the mapping would send it to dd too.)"""
    from server.library.usedbackfill import derive
    msr = windows_with_msr()["partitions"][1]
    for n, fs in enumerate(("", "unknown")):
        image = {**windows_with_msr(f"img_00000{n}"),
                 "partitions": [{**msr, "fs": fs}]}
        write_image(tmp_path, image)
        manifest = ImageLibrary(tmp_path).scan()[image["id"]]
        assert derive(manifest, read=lambda p: pytest.fail("a dd stream was read")) == ({}, [])


def test_a_partclone_filesystem_without_a_header_still_fails_loudly(tmp_path):
    from server.library.usedbackfill import derive
    esp, _msr, win = windows_with_msr()["partitions"]
    write_image(tmp_path, {**windows_with_msr(), "partitions": [esp, win]})
    manifest = ImageLibrary(tmp_path).scan()["img_8da804"]
    _, problems = derive(manifest, read=lambda p: b"\0" * 110)
    assert problems == ["p1: no valid partclone v2 header", "p3: no valid partclone v2 header"]


def test_the_dry_run_does_not_call_a_raw_capture_a_failure(tmp_path, capsys, monkeypatch):
    from server.library import usedbackfill
    esp, msr, win = windows_with_msr()["partitions"]
    write_image(tmp_path, {**windows_with_msr(), "partitions": [esp, msr, win]})
    folder = tmp_path / "img_8da804"
    (folder / "p1.esp.pcl.zst").write_bytes(header(used=7, fs=b"FAT32"))
    (folder / "p2.msr.pcl.zst").write_bytes(b"\0" * 110)
    (folder / "p3.win.pcl.zst").write_bytes(header(used=5, total=9))
    monkeypatch.setattr(usedbackfill, "read_head", lambda path: Path(path).read_bytes()[:110])

    assert usedbackfill.main([str(folder)]) == 0
    out = capsys.readouterr().out
    assert "NO VALID HEADER" not in out
    assert "p2 p2.msr.pcl.zst RAW DD CAPTURE (no partclone header)" in out.splitlines()
