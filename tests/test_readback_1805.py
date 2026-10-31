"""#1805 -- a drawer is `done` by what the DISK holds, not by what the stream said.

Until #1805 every "done" stood on evidence from the stream: the sha256 of the
compressed bytes before zstd and partclone, partclone's exit code (CLR-01/03,
research-failures-cloner.md). RAM without ECC, a zstd/partclone bug, or SSD
firmware that acknowledges a write it never made all ended "done".

Nadav, 20/10 (#1864, a full sha256 cost ~8 minutes a round of 4 drawers): the
data stream -- the used blocks in order -- is cut into 4MiB chunks, an XXH64
each, and the list is taken twice, independently:

* the server's, from the image in the library (server/agent/readbackhash.py), kept
  beside the partition file and named in the manifest (`readback_xxh64` is the
  XXH64 of the list itself);
* the agent's, from the written partition (`fanout --readback -x`, fanout.c):
  every chunk (`readback_full` on), or a seeded sample of ~5% (the default).

The images here are built byte for byte by ``pcl_image``, and the expected list
is the public XXH64 reference (test_fanout.py) over blocks the test chose --
neither side can vouch for itself.
"""

from __future__ import annotations

import errno
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from agentlib import lib_files

import pytest
from conftest import MANIFEST_256, write_image

import pcl_image as P
from native import requires_native
from server.agent import readbackhash as RB
from server.library.images import ImageLibrary
from test_fanout import xxh64_reference

REPO = Path(__file__).resolve().parent.parent
FANOUT_C = REPO / "agent" / "fanout.c"

#: a "decompressor" that is cat: the server's own zstd path, without zstd.
CAT = (sys.executable, "-c", "import sys,shutil;shutil.copyfileobj(open(sys.argv[1],'rb'),sys.stdout.buffer)")
FAIL = (sys.executable, "-c", "import sys;sys.stdout.buffer.write(open(sys.argv[1],'rb').read());sys.exit(3)")
PY = RB.PyXxh64          # the engine of these tests: no kernel on a Windows workstation

# A map with every shape the reader meets: a run longer than its 8MiB window
# (2500 x 4KiB), short gaps it reads through, a long gap it skips, single
# blocks, and a used last block whose byte is not full (total % 8 != 0).
# 2514 blocks x 4KiB = 3 chunks of the data stream, the last one short.
TOTAL = 3005
USED = (list(range(0, 2500)) + [2510, 2511, 2600] + list(range(2700, 2710)) + [3004])
_WANT = {}


def want(bs: int = 4096) -> tuple[str, int]:
    if bs not in _WANT:
        _WANT[bs] = P.expected_list(P.stream(USED, bs))
    return _WANT[bs]


# --- the server's list -------------------------------------------------------------


@pytest.mark.parametrize("cs, per", [(0, 0), (4, 1), (4, 3), (4, 256), (4, 5000)])
def test_the_server_list_is_the_used_blocks_in_4mib_chunks_checksums_stripped(cs, per):
    """-a0 (no checksums, the capture since #1472), CRC32 after every block, after
    every 3 (a remainder), after 256 (the default buffer) and after more blocks
    than there are (only the final one): one list, the blocks'."""
    img = P.image(TOTAL, USED, 4096, checksum_size=cs, per_checksum=per)
    assert RB.digest_partclone(io.BytesIO(img), None, PY) == want()


def test_small_blocks_one_short_chunk():
    img = P.image(TOTAL, USED, 512)
    text, count = RB.digest_partclone(io.BytesIO(img), None, PY)
    assert (text, count) == want(512) and text.count("\n") == 1


def test_an_empty_filesystem_is_an_empty_list():
    assert RB.digest_partclone(io.BytesIO(P.image(64, [], 4096)), None, PY) == ("", 0)


def _corrupt(img: bytes, at: int) -> bytes:
    return img[:at] + bytes([img[at] ^ 0xFF]) + img[at + 1:]


BM_AT = 110


@pytest.mark.parametrize("damage, why", [
    (lambda img: _corrupt(img, BM_AT + 3), "bit map failed its CRC"),
    (lambda img: _corrupt(img, 5), "no valid partclone v2 header"),
    (lambda img: img[:-1], "ended 1 bytes early"),
    (lambda img: img + b"\0", "bytes after the last block"),
    (lambda img: img[:BM_AT + 10], "ended"),
])
def test_anything_short_of_a_provably_whole_image_has_no_list(damage, why):
    img = P.image(TOTAL, USED, 4096, checksum_size=4, per_checksum=7)
    with pytest.raises(RB.Underived, match=why):
        RB.digest_partclone(io.BytesIO(damage(img)), None, PY)


def test_a_map_that_disagrees_with_its_header_has_no_list():
    bm = P.bitmap(64, [1, 2, 3])
    img = P.header(64, 4, 4096) + bm + P.crc(bm).to_bytes(4, "little") + b"\0" * 4 * 4096
    with pytest.raises(RB.Underived, match="marks 3 blocks, the header 4"):
        RB.digest_partclone(io.BytesIO(img), None, PY)


def test_a_byte_map_image_is_not_guessed():
    with pytest.raises(RB.Underived, match="not a v2 bit map"):
        RB.digest_partclone(io.BytesIO(P.image(64, [1], 4096, bitmap_mode=8)), None, PY)


def test_a_dd_capture_is_the_whole_stream():
    data = os.urandom(5 * 1024 * 1024 + 17)
    assert RB.digest_raw(io.BytesIO(data), None, PY) == P.expected_list(data)


def test_the_list_digest_is_what_fanout_xxh64_prints_of_the_list_file():
    text, _ = want()
    assert RB.list_digest(text, PY) == xxh64_reference(text.encode("ascii"))


def test_the_file_path_runs_the_decompressor_and_reads_its_exit_code(tmp_path):
    f = tmp_path / "p3.win.pcl.zst"
    f.write_bytes(P.image(TOTAL, USED, 4096))
    assert RB.digest_file(f, False, cmd=CAT, new=PY) == want()
    # every byte arrived, the list is right -- and the decompressor said it failed:
    # zstd's own frame check is part of the evidence, so no list (principle 5).
    with pytest.raises(RB.Underived, match="exited 3"):
        RB.digest_file(f, False, cmd=FAIL, new=PY)
    with pytest.raises(RB.Underived, match="cannot run"):
        RB.digest_file(f, False, cmd=("no-such-decompressor-1805",), new=PY)


@requires_native("zstd", why="the server's real decompressor")
def test_a_real_zstd_stream(tmp_path):
    img = P.image(TOTAL, USED, 4096, checksum_size=4, per_checksum=256)
    packed = subprocess.run(["zstd", "-q", "-c"], input=img, capture_output=True, check=True).stdout
    f = tmp_path / "p3.win.pcl.zst"
    f.write_bytes(packed)
    assert RB.digest_file(f, False, new=PY) == want()


def _kernel_xxh() -> bool:
    import socket
    try:
        s = socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0)
        s.bind(("hash", "xxhash64"))
        s.close()
        return True
    except (AttributeError, OSError):
        return False


@requires_native(("AF_ALG xxhash64", _kernel_xxh()), posix=True, why="the server's engine is the kernel's")
def test_the_kernels_xxhash64_is_the_same_numbers():
    """The server uses the kernel's XXH64; the agent uses fanout's own C. Same
    seed (0), same bytes, the same 16 digits -- byte order included."""
    data = os.urandom(3 * 1024 * 1024 + 5)
    new = RB.xxh64_factory()
    assert new is not RB.PyXxh64
    lister = RB.ChunkLister(new)
    lister.update(data)
    assert lister.finish() == P.expected_list(data)


# --- the library: the list beside the file, the fields in the manifest -------------


def _image(root: Path, image_id: str = "img_1805aa") -> dict:
    manifest = {**MANIFEST_256, "id": image_id}
    write_image(root, manifest)
    return manifest


def _on_disk(root: Path, image_id: str) -> dict:
    return json.loads((root / image_id / "manifest.json").read_text(encoding="utf-8"))


LIST_A = "".join(f"{i:016x}\n" for i in range(3))      # 3 chunks: 8MiB < bytes <= 12MiB
BYTES_A = 9 * 1024 * 1024
LIST_B = "00000000000000aa\n"
BYTES_B = 1000


def _digests(by_name: dict[str, object]):
    def digest(path, raw, limit, stop=None):
        assert limit == {"p1.esp.pcl.zst": 104857600, "p3.win.pcl.zst": 254803968000}[Path(path).name]
        value = by_name[Path(path).name]
        if isinstance(value, Exception):
            raise value
        return value
    return digest


def test_fill_writes_the_list_beside_the_file_and_the_fields_once(tmp_path):
    _image(tmp_path)
    lib = ImageLibrary(tmp_path)
    results = RB.fill(lib, digest=_digests({"p1.esp.pcl.zst": (LIST_B, BYTES_B),
                                            "p3.win.pcl.zst": (LIST_A, BYTES_A)}))
    assert [r["problems"] for r in results] == [[]] and sorted(results[0]["written"]) == [1, 3]
    esp, win = _on_disk(tmp_path, "img_1805aa")["partitions"]
    assert (win["readback_chunks"], win["readback_bytes"], win["readback_chunk_bytes"]) == (3, BYTES_A, 4194304)
    assert win["readback_xxh64"] == xxh64_reference(LIST_A.encode("ascii"))       # the list file's own XXH64
    assert (tmp_path / "img_1805aa" / "p3.win.pcl.zst.readback").read_text(encoding="ascii") == LIST_A
    assert esp["readback_chunks"] == 1 and "readback_sha256" not in win           # sha256 is no longer made
    assert lib.get("img_1805aa") is not None                                     # still a valid image
    assert RB.fill(lib, digest=_digests({})) == []                                # on the disk: not read again


def test_a_manifest_that_came_without_its_list_is_made_again(tmp_path):
    """A transfer or an import copies manifest.json and the partition files --
    not the list. The fields alone are not a list: it is made again."""
    _image(tmp_path)
    lib = ImageLibrary(tmp_path)
    RB.fill(lib, digest=_digests({"p1.esp.pcl.zst": (LIST_B, BYTES_B), "p3.win.pcl.zst": (LIST_A, BYTES_A)}))
    (tmp_path / "img_1805aa" / "p3.win.pcl.zst.readback").unlink()
    again = RB.fill(lib, digest=_digests({"p3.win.pcl.zst": (LIST_A, BYTES_A)}))
    assert again[0]["written"] == {3: (LIST_A, BYTES_A, xxh64_reference(LIST_A.encode("ascii")))}


def test_an_old_manifest_with_only_sha256_gets_a_list(tmp_path):
    m = {**MANIFEST_256, "id": "img_1805aa"}
    m["partitions"] = [{**p, "readback_sha256": "ab" * 32, "readback_bytes": 1} for p in m["partitions"]]
    write_image(tmp_path, m)
    RB.fill(ImageLibrary(tmp_path), digest=_digests({"p1.esp.pcl.zst": (LIST_B, BYTES_B),
                                                    "p3.win.pcl.zst": (LIST_A, BYTES_A)}))
    win = _on_disk(tmp_path, "img_1805aa")["partitions"][1]
    assert win["readback_sha256"] == "ab" * 32 and win["readback_chunks"] == 3        # the old one stays


@pytest.mark.parametrize("value", [
    ("0000000000000001\n", 5 * 1024 * 1024, "a" * 16),          # 5MiB is two chunks, one line
    ("XYZ\n", 10, "a" * 16),                                     # not hex
    (LIST_B, 10 ** 15, "a" * 16),                                # more than the partition
    (LIST_B, BYTES_B, "A" * 16),                                 # not the agent's lowercase
    ("0000000000000001", BYTES_B, "a" * 16),                     # no newline: not a list file
])
def test_record_refuses_a_list_the_agent_would_fail_good_drawers_on(tmp_path, value):
    _image(tmp_path)
    assert ImageLibrary(tmp_path).record_readback("img_1805aa", {1: value}) == []
    assert "readback_xxh64" not in _on_disk(tmp_path, "img_1805aa")["partitions"][0]
    assert not (tmp_path / "img_1805aa" / "p1.esp.pcl.zst.readback").exists()


def test_what_cannot_be_derived_is_reported_left_absent_and_retried_when_the_files_change(tmp_path):
    _image(tmp_path)
    lib = ImageLibrary(tmp_path)
    digest = _digests({"p1.esp.pcl.zst": (LIST_B, BYTES_B), "p3.win.pcl.zst": RB.Underived("the bit map failed its CRC")})
    tried, seen = {}, []
    RB.fill(lib, digest=digest, report=seen.append, tried=tried)
    assert seen[0]["problems"] == ["p3: the bit map failed its CRC"] and sorted(seen[0]["written"]) == [1]
    assert "readback_xxh64" not in _on_disk(tmp_path, "img_1805aa")["partitions"][1]   # never a guessed value
    assert RB.fill(lib, digest=_digests({}), tried=tried) == []                       # not every pass
    (tmp_path / "img_1805aa" / "p3.win.pcl.zst").write_bytes(b"new bytes")
    again = RB.fill(lib, digest=_digests({"p3.win.pcl.zst": (LIST_A, BYTES_A)}), tried=tried)
    assert sorted(again[0]["written"]) == [3]


# --- the header is input: bounded before it is believed (security review, #1805) -----


class _Spy(io.BytesIO):
    """A stream that refuses any read larger than one CHUNK: a header that makes
    the reader ask for its own size in one piece IS the memory exhaustion."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.reads: list[int] = []

    def read(self, n=-1):
        self.reads.append(n)
        if n is None or n < 0 or n > RB.CHUNK:
            raise MemoryError(f"asked for {n} bytes in one read")
        return super().read(n)


def test_a_header_claiming_a_huge_map_is_refused_before_reading_it():
    with pytest.raises(RB.Underived, match="implausible header"):
        RB.digest_partclone(_Spy(P.header(2 ** 40, 0, 4096) + b"\0" * 64), None, PY)


def test_a_header_larger_than_its_partition_is_refused_before_its_map():
    spy = _Spy(P.image(TOTAL, USED, 4096))
    with pytest.raises(RB.Underived, match="more than the partition"):
        RB.digest_partclone(spy, 1 << 20, PY)
    assert spy.reads == [110]


def test_the_map_is_read_in_bounded_pieces():
    total = RB.CHUNK * 8 * 3 + 5
    used = [0, total // 2, total - 1]
    data = P.stream(used, 512)
    assert RB.digest_partclone(_Spy(P.image(total, used, 512)), None, PY) == P.expected_list(data)


def test_one_image_that_breaks_the_reader_does_not_stop_the_pass(tmp_path):
    _image(tmp_path, "img_1805aa")
    _image(tmp_path, "img_1805bb")

    def digest(path, raw, limit=None, stop=None):
        if "img_1805aa" in str(path):
            raise MemoryError("a header that asked for everything")
        return (LIST_B, BYTES_B)
    tried: dict = {}
    results = {r["image_id"]: r for r in RB.fill(ImageLibrary(tmp_path), digest=digest, tried=tried)}
    assert results["img_1805aa"]["written"] == {} and results["img_1805aa"]["problems"][0].startswith("MemoryError")
    assert "img_1805aa" in tried and sorted(results["img_1805bb"]["written"]) == [1, 3]


# --- #1864 (metal 20/10): the digest yields to the work, and the round's image goes first ---


@pytest.fixture
def fresh_priority(monkeypatch):
    monkeypatch.setattr(RB, "_PRIORITY", {})
    return RB._PRIORITY


def test_a_busy_server_is_not_read_at_all_and_nothing_is_failed(tmp_path, fresh_priority):
    _image(tmp_path)
    calls, tried, status = [], {}, {}
    RB.fill(ImageLibrary(tmp_path), digest=lambda *a: calls.append(a), tried=tried,
            busy=lambda image_id: "שידור פעיל", status=status)
    assert calls == [] and tried == {} and status["paused"] == "שידור פעיל"


def test_work_that_starts_mid_read_stops_the_decompressor(tmp_path, monkeypatch):
    monkeypatch.setattr(RB, "STOP_EVERY_S", 0.0)
    f = tmp_path / "p3.win.pcl.zst"
    f.write_bytes(P.image(TOTAL, USED, 4096))
    asked = []

    def busy():
        asked.append(1)
        return "שידור פעיל" if len(asked) > 2 else None
    with pytest.raises(RB.Paused, match="שידור פעיל"):
        RB.digest_file(f, False, None, busy, cmd=CAT, new=PY)


def test_a_paused_image_keeps_what_it_finished_and_its_place(tmp_path, fresh_priority):
    _image(tmp_path)
    RB.kick("img_1805aa")

    def digest(path, raw, limit, stop=None):
        if Path(path).name == "p3.win.pcl.zst":
            raise RB.Paused("שידור פעיל")
        return (LIST_B, BYTES_B)
    tried, status = {}, {}
    results = RB.fill(ImageLibrary(tmp_path), digest=digest, tried=tried, busy=lambda i: None, status=status)
    assert sorted(results[0]["written"]) == [1] and results[0]["problems"] == []
    assert status["paused"] == "שידור פעיל" and tried == {} and "img_1805aa" in fresh_priority


def test_the_rounds_image_first_then_last_used_then_the_big_idle_ones(tmp_path, fresh_priority):
    for i in ("img_00000a", "img_00000b", "img_00000c", "img_00000d", "img_00000e"):
        _image(tmp_path, i)
    for image_id, size in {"img_00000c": 10, "img_00000d": 10 ** 11}.items():
        path = tmp_path / image_id / "manifest.json"
        m = json.loads(path.read_text(encoding="utf-8"))
        m["total_compressed_bytes"] = size
        path.write_text(json.dumps(m), encoding="utf-8")
    RB.kick("img_00000e")
    order = []
    RB.fill(ImageLibrary(tmp_path),
            digest=lambda path, raw, limit, stop=None: order.append(Path(path).parent.name) or (LIST_B, BYTES_B),
            busy=lambda i: None,
            rank=lambda: {"img_00000a": "2026-10-01T10:00:00", "img_00000b": "2026-10-19T10:00:00",
                          "img_00000e": "2026-09-01T00:00:00"})
    assert list(dict.fromkeys(order)) == ["img_00000e", "img_00000b", "img_00000a", "img_00000c", "img_00000d"]
    assert "img_00000e" not in fresh_priority


def _busy_db(tmp_path, *, round_image=None, task_state=None, transfer_state=None):
    from server import db
    conn = db.connect(tmp_path / "rb.db")
    if round_image:
        conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
                     " VALUES ('r1', ?, 3, 'active', 'u', '2026-10-20T01:42:00')", (round_image,))
    if task_state:
        conn.execute("INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by, created_at,"
                     " updated_at) VALUES ('t1', 'aa', 'capture', 'sda', 'img_x', 'n', ?, 'u', 'x', 'x')",
                     (task_state,))
    if transfer_state:
        conn.execute("INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
                     " VALUES ('n1', 'n', 'https://n', 'c', 'x')")
        conn.execute("INSERT INTO storage_transfers (id, node_id, image_id, image_name, state, started_by,"
                     " created_at, updated_at) VALUES ('s1', 'n1', 'img_x', 'n', ?, 'u', 'x', 'x')",
                     (transfer_state,))
    conn.commit()
    return conn


class _Sender:
    def __init__(self, state):
        self.state = state

    def status(self):
        return None if self.state is None else {"state": self.state}


@pytest.mark.parametrize("kw, sender, image, why", [
    ({}, None, "img_a", None),
    ({"round_image": "img_a"}, "starting", "img_a", None),
    ({"round_image": "img_a"}, "sending", "img_a", "שידור פעיל"),
    ({"round_image": "img_a"}, None, "img_b", "סבב פתוח לאימג' אחר"),
    ({"task_state": "running"}, None, "img_a", "קליטה או שליחה ישירה פעילה"),
    ({"task_state": "verifying"}, None, "img_a", "קליטה או שליחה ישירה פעילה"),
    ({"task_state": "done"}, None, "img_a", None),
    ({"transfer_state": "sending"}, None, "img_a", "העברה בין אתרים פעילה"),
])
def test_the_server_says_when_it_needs_its_disk(tmp_path, kw, sender, image, why):
    assert RB.server_busy(_busy_db(tmp_path, **kw), _Sender(sender) if sender else None)(image) == why


def test_last_use_is_the_newest_round_or_session(tmp_path):
    conn = _busy_db(tmp_path, round_image="img_a")
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'g', 'cloner')")
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES ('s', 'g', 'img_a', 'p', 1, 1, 'closed', 'u',"
                 " '2026-10-21T00:00:00', 0)")
    conn.commit()
    assert RB.last_use(conn)() == {"img_a": "2026-10-21T00:00:00"}


@pytest.mark.parametrize("found, want_cmd", [(True, RB.IDLE_PRIORITY + RB.DECOMPRESS), (False, RB.DECOMPRESS)])
def test_zstd_runs_at_idle_cpu_and_disk_priority_when_it_can(found, want_cmd):
    assert RB.decompress_cmd(which=lambda name: f"/usr/bin/{name}" if found else None) == want_cmd


def test_a_round_kicks_its_image_and_the_thread_is_given_the_busy_and_rank():
    import inspect
    from server import app
    from server.rounds import room, sessions
    assert "readbackhash.kick(image_id)" in inspect.getsource(room.open_round)
    assert "readbackhash.kick(image_id)" in inspect.getsource(sessions.SessionStore.open)
    src = inspect.getsource(app.create_runtime)
    assert '"busy": readbackhash.server_busy(conn, sender)' in src and '"rank": readbackhash.last_use(conn)' in src
    assert src.index("sender = SenderEngine(") < src.index("readbackhash.run_forever")


def test_the_capture_kicks_the_pass_and_main_starts_it():
    import inspect
    from server import app, main
    from server.library import capture
    assert "readbackhash.kick()" in inspect.getsource(capture._verify)
    assert inspect.signature(app.create_runtime).parameters["backfill_readback"].default is False
    assert "backfill_readback=True" in inspect.getsource(main)


def test_the_room_passes_only_the_three_known_values():
    import inspect
    from server.rounds import room
    assert 'in ("verified", "sampled", "unverified") else None' in inspect.getsource(room)


# --- #1864: the switch, the mode in the manifest, the list on the wire --------------


def test_the_switch_is_validated_by_the_server_and_off_by_default(server):
    """Nadav 20/10: a setting like the others (#1794: validated server-side)."""
    admin, ctx = server["admin"], server["ctx"]
    assert admin.get("/api/console/settings").json()["readback_full"] == "false"
    for bad in ("yes", "1", "", 5, None):
        r = admin.post("/api/console/settings", json={"readback_full": bad})
        assert r.status_code == 400 and "true או false" in r.json()["detail"], (bad, r.text)
    assert RB.mode(ctx.conn) == "sample"
    assert admin.post("/api/console/settings", json={"readback_full": True}).status_code == 200
    assert admin.get("/api/console/settings").json()["readback_full"] == "true" and RB.mode(ctx.conn) == "full"
    assert server["deploy"].post("/api/console/settings", json={"readback_full": False}).status_code == 403


def test_the_manifest_the_agent_reads_carries_the_mode(server):
    anon, admin = server["anon"], server["admin"]
    assert anon.get("/api/v1/images/img_7f3a91/manifest").json()["readback_mode"] == "sample"
    admin.post("/api/console/settings", json={"readback_full": "true"})
    assert anon.get("/api/v1/images/img_7f3a91/manifest").json()["readback_mode"] == "full"


def test_the_list_is_served_only_when_it_is_whole(server):
    anon, ctx = server["anon"], server["ctx"]
    assert anon.get("/api/v1/images/img_7f3a91/readback/3").status_code == 404     # not made yet
    xxh = xxh64_reference(LIST_A.encode("ascii"))
    assert ctx.library.record_readback("img_7f3a91", {3: (LIST_A, BYTES_A, xxh)}) == [3]
    r = anon.get("/api/v1/images/img_7f3a91/readback/3")
    assert r.status_code == 200 and r.text == LIST_A
    assert anon.get("/api/v1/images/img_7f3a91/readback/1").status_code == 404     # p1 has none
    assert anon.get("/api/v1/images/img_7f3a91/readback/9").status_code == 404     # no such partition


def test_the_hello_carries_the_mode_for_the_local_clone():
    import inspect
    from server.agent import hello
    assert '"readback_mode": readbackhash.mode(conn)' in inspect.getsource(hello)


# --- the C reader: fanout --readback -x [-s seed permille] -------------------------

needs_gcc = requires_native("gcc", posix=True, why="fanout --readback is C, read with O_DIRECT")


@pytest.fixture(scope="module")
def fanout(tmp_path_factory):
    binary = tmp_path_factory.mktemp("rbbuild") / "fanout"
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(binary), str(FANOUT_C)],
                   check=True, stdin=subprocess.DEVNULL)
    return binary


def _direct_env(path: Path) -> dict:
    """O_DIRECT where the temp dir allows it; a tmpfs refuses it and the test
    says so -- the escape hatch is FANOUT_READBACK_CACHED, never set by the agent."""
    env = dict(os.environ)
    try:
        os.close(os.open(path, os.O_RDONLY | os.O_DIRECT))
    except OSError as exc:
        if exc.errno != errno.EINVAL:
            raise
        env["FANOUT_READBACK_CACHED"] = "1"
    return env


def _readback(fanout: Path, dev: Path, stdin: bytes | None, *args: str, env: dict | None = None):
    feed = dev.parent / f"{dev.name}.stdin"
    feed.write_bytes(stdin or b"")
    with feed.open("rb") as fh:
        res = subprocess.run([str(fanout), "--readback", *args], stdin=fh,
                             capture_output=True, timeout=120, env=env or _direct_env(dev))
    return res.returncode, res.stdout.decode(), res.stderr.decode()


def _lines(out: str) -> tuple[dict[int, str], str]:
    chunks, last = {}, ""
    for line in out.splitlines():
        kind, *rest = line.split()
        if kind == "c":
            chunks[int(rest[0])] = rest[1]
        else:
            last = line
    return chunks, last


@needs_gcc
@pytest.mark.parametrize("bs", [512, 4096])
def test_every_chunk_is_the_servers_list(fanout, tmp_path, bs):
    img = P.image(TOTAL, USED, bs, checksum_size=4, per_checksum=256)
    dev = tmp_path / "sdb3"
    dev.write_bytes(P.device(TOTAL, USED, bs))
    rc, out, err = _readback(fanout, dev, img, "-x", str(dev))
    text, count = want(bs)
    assert rc == 0, err
    chunks, last = _lines(out)
    assert "".join(chunks[k] + "\n" for k in sorted(chunks)) == text
    assert last == f"n {text.count(chr(10))} {count}"
    assert RB.digest_partclone(io.BytesIO(img), None, PY) == (text, count)          # the server's: the same


@needs_gcc
def test_a_sample_reads_only_its_chunks_and_the_seed_draws_them_again(fanout, tmp_path):
    """20 chunks: the first, the middle, the last, and the drawn ones; the same seed
    draws the same chunks -- and a chunk not drawn is not read at all."""
    bs, per = 4096, 1024                       # 1024 blocks of 4KiB = one chunk
    n = 20 * per
    used = list(range(n))
    img = P.image(n, used, bs)
    data = P.stream(used, bs)
    ref = lambda k: xxh64_reference(data[k * P.CHUNK_BYTES:(k + 1) * P.CHUNK_BYTES])   # noqa: E731
    dev = tmp_path / "sdb3"
    dev.write_bytes(P.device(n, used, bs))
    rc, out, err = _readback(fanout, dev, img, "-x", "-s", "1805", "50", str(dev))
    assert rc == 0, err
    picked, last = _lines(out)
    assert last == f"n 20 {len(data)}"
    assert set(picked) == {0, 10, 19}                     # 5% of 20 is one: never fewer than the fixed three
    assert all(picked[k] == ref(k) for k in picked)
    more, _ = _lines(_readback(fanout, dev, img, "-x", "-s", "1805", "500", str(dev))[1])
    assert len(more) == 10 and {0, 10, 19} <= set(more) and all(more[k] == ref(k) for k in more)
    assert _lines(_readback(fanout, dev, img, "-x", "-s", "1805", "500", str(dev))[1])[0] == more   # same seed
    assert set(_lines(_readback(fanout, dev, img, "-x", "-s", "7", "500", str(dev))[1])[0]) != set(more)
    # a chunk nobody drew is never read: zero every block outside the drawn ones
    raw = bytearray(P.device(n, used, bs))
    for k in range(20):
        if k not in more:
            raw[k * P.CHUNK_BYTES:(k + 1) * P.CHUNK_BYTES] = bytes(P.CHUNK_BYTES)
    dev.write_bytes(bytes(raw))
    rc, out3, err = _readback(fanout, dev, img, "-x", "-s", "1805", "500", str(dev))
    assert rc == 0 and _lines(out3)[0] == more, err


@needs_gcc
def test_one_flipped_byte_changes_its_chunk_and_free_space_is_not_read(fanout, tmp_path):
    bs = 4096
    img = P.image(TOTAL, USED, bs)
    base = P.device(TOTAL, USED, bs)
    good = want(bs)[0].splitlines()
    dev = tmp_path / "sdb3"
    for block, chunk in [(0, 0), (1500, 1), (3004, 2), (2601, None), (2550, None)]:
        at = block * bs + 1234
        dev.write_bytes(base[:at] + bytes([base[at] ^ 0x01]) + base[at + 1:])
        rc, out, err = _readback(fanout, dev, P.map_only(img, TOTAL), "-x", str(dev))
        assert rc == 0, err
        got = [_lines(out)[0][k] for k in range(3)]
        assert [k for k in range(3) if got[k] != good[k]] == ([] if chunk is None else [chunk]), block


@needs_gcc
@pytest.mark.parametrize("damage, why", [
    (lambda m: m[:100], "header did not arrive whole"),
    (lambda m: m[:-2], "bit map did not arrive whole"),
    (lambda m: _corrupt(m, 20), "header failed its CRC"),
    (lambda m: _corrupt(m, 120), "bit map failed its CRC"),
    (lambda m: m[:30] + b"0001" + m[34:], "not a partclone v2 image"),
])
def test_a_map_that_is_not_provably_whole_is_exit_3_and_no_list(fanout, tmp_path, damage, why):
    img = P.image(TOTAL, USED, 4096)
    dev = tmp_path / "sdb3"
    dev.write_bytes(P.device(TOTAL, USED, 4096))
    rc, out, err = _readback(fanout, dev, damage(P.map_only(img, TOTAL)), "-x", str(dev))
    assert (rc, out) == (3, ""), err
    assert why in err


@needs_gcc
def test_a_device_shorter_than_its_map_is_exit_4(fanout, tmp_path):
    img = P.image(TOTAL, USED, 4096)
    dev = tmp_path / "sdb3"
    dev.write_bytes(P.device(TOTAL, USED, 4096)[:3000 * 4096])
    rc, out, err = _readback(fanout, dev, P.map_only(img, TOTAL), "-x", str(dev))
    assert rc == 4 and "c " not in out.splitlines()[-1:] and "ended at byte" in err


@needs_gcc
def test_raw_mode_lists_the_first_n_bytes(fanout, tmp_path):
    data = os.urandom(9 * 1024 * 1024 + 4096)
    dev = tmp_path / "sdb2"
    dev.write_bytes(data + os.urandom(8192))
    rc, out, err = _readback(fanout, dev, None, "-x", str(dev), str(len(data)))
    assert rc == 0, err
    chunks, last = _lines(out)
    text, count = P.expected_list(data)
    assert "".join(chunks[k] + "\n" for k in sorted(chunks)) == text and last == f"n 3 {count}"
    assert _readback(fanout, dev, None, "-x", str(dev), str(len(data) + (1 << 20)))[0] == 4
    assert _readback(fanout, dev, None, "-x", "-s", "1", "0", str(dev), "10")[0] == 2      # permille 1..1000


@needs_gcc
def test_the_list_file_hashes_the_same_in_fanout_and_on_the_server(fanout, tmp_path):
    text, _ = want()
    f = tmp_path / "list"
    f.write_bytes(text.encode("ascii"))
    with f.open("rb") as fh:
        got = subprocess.run([str(fanout), "--xxh64"], stdin=fh, capture_output=True, check=True).stdout.decode()
    assert got.strip() == RB.list_digest(text, PY)


@needs_gcc
def test_the_old_sha256_form_still_reads_for_manifests_from_before_1864(fanout, tmp_path):
    import hashlib
    img = P.image(TOTAL, USED, 4096)
    dev = tmp_path / "sdb3"
    dev.write_bytes(P.device(TOTAL, USED, 4096))
    rc, out, err = _readback(fanout, dev, P.map_only(img, TOTAL), str(dev))
    data = P.stream(USED, 4096)
    assert rc == 0 and out == f"{hashlib.sha256(data).hexdigest()} {len(data)}\n", err


def test_the_agent_never_reads_through_the_cache():
    for f in [*lib_files(REPO / "agent" / "lib"), REPO / "agent" / "imagesrv-agent", REPO / "agent" / "init"]:
        assert "FANOUT_READBACK_CACHED" not in f.read_text(encoding="utf-8"), f


# --- the real thing: mkfs.vfat, partclone.fat -c / -r, no root ----------------------

real = requires_native("gcc", "mkfs.vfat", "partclone.fat", posix=True,
                       why="a real FAT filesystem through real partclone: the CI (#1356) and the Testrunner")


def _sbin_env() -> dict:
    from native import with_sbin
    return {**os.environ, "PATH": with_sbin(os.environ.get("PATH", ""))}


def _run(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, stdin=subprocess.DEVNULL, timeout=300,
                          env=_sbin_env(), **kw)


@real
def test_real_partclone_restore_reads_back_to_the_servers_list(fanout, tmp_path):
    src, dst = tmp_path / "src.img", tmp_path / "dst.img"
    src.write_bytes(b"\0" * (64 << 20))
    made = _run("mkfs.vfat", "-n", "RB1805", str(src))
    assert made.returncode == 0, made.stderr
    lists = {}
    for flags in ([], ["-a0"]):
        out = tmp_path / f"img{len(flags)}.pcl"
        cap = _run("partclone.fat", "-c", *flags, "-s", str(src), "-o", str(out), "-L", str(tmp_path / "c.log"))
        assert cap.returncode == 0, cap.stderr
        lists[len(flags)] = RB.digest_partclone(io.BytesIO(out.read_bytes()), None, RB.xxh64_factory())
    assert lists[0] == lists[1], lists                 # the checksums are not part of the list
    text, count = lists[0]
    dst.write_bytes(b"\xEE" * (64 << 20))
    rest = _run("partclone.fat", "-r", "-s", str(tmp_path / "img0.pcl"), "-O", str(dst), "-L", str(tmp_path / "r.log"))
    assert rest.returncode == 0, rest.stderr

    def readback(*flags) -> tuple[int, str, str]:
        pcl = subprocess.Popen(["partclone.fat", "-c", "-s", str(dst), "-L", str(tmp_path / "rb.log")],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=_sbin_env())
        res = subprocess.run([str(fanout), "--readback", *flags, str(dst)], stdin=pcl.stdout,
                             capture_output=True, timeout=120, env={**_sbin_env(), **_direct_env(dst)})
        pcl.stdout.close()
        pcl.wait(timeout=60)
        return res.returncode, res.stdout.decode(), res.stderr.decode()

    rc, out, err = readback("-x")
    chunks, last = _lines(out)
    assert rc == 0 and "".join(chunks[k] + "\n" for k in sorted(chunks)) == text, err
    assert last == f"n {text.count(chr(10))} {count}"
    # one byte of a used block changed after the write (the SSD that did not keep it)
    raw = (tmp_path / "img0.pcl").read_bytes()
    bs, total = int.from_bytes(raw[84:88], "little"), int.from_bytes(raw[60:68], "little")
    bm = raw[110:110 + (total + 7) // 8]
    last_used = max(i for i in range(total) if bm[i >> 3] >> (i & 7) & 1)
    data = bytearray(dst.read_bytes())
    data[last_used * bs + 7] ^= 0x5A
    dst.write_bytes(bytes(data))
    rc, out, err = readback("-x")
    assert rc in (0, 3) and _lines(out)[0] != chunks, err
