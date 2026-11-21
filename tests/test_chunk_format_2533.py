"""#2533 / #2528 stage 2 -- the chunk format (image_format 2) on the server's side.

A partition file is no longer one zstd stream: it is the head (partclone's header
and bit map) and then one independent zstd frame for every 4MiB of the data
stream, with an index beside it (offset, frame length, XXH64 of the chunk). The
capturing agent builds the index and signs it in the manifest (`index_xxh64`); the
server derives the same index from the file and writes it beside the file only
when the two meet.

What these tests hold:

* the server's derived index is the one the file was built with -- offsets from the
  frame headers, hashes from the decompressed bytes -- and anything short of a
  provably whole image_format 2 file has no index (principle 5);
* one chunk cut out of the file by its index line stands alone, and a chunk whose
  bytes are not the ones the index names is caught by its hash;
* an image in the old format is labelled, is not offered to a machine, and every
  way of distributing it is refused by name -- never opened to fail later;
* a capture that is not image_format 2 does not enter the library.

The files are built by chunk_image.py from RFC 8878 (stored frames) and the
public XXH64 reference -- not by the code under test, and not by libzstd.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import MANIFEST_256, write_image

import chunk_image as C
import pcl_image as P
from native import requires_native
from server.agent import readbackhash as RB
from server.library import chunkformat as F
from server.library import images
from server.library.images import ImageLibrary
from test_capture_mac_binding import ADMIN
from test_fanout import xxh64_reference
from test_server_room import CLONER1, room_body, room_server      # noqa: F401 -- room_server is a fixture

PY = RB.PyXxh64                     # the engine here: no kernel xxhash64 on a workstation
DC = {"cmd": C.STORED_DC, "frame_cmd": C.STORED_DC, "new": PY}

# 1025 used blocks of 4KiB = 4MiB + 4KiB of data: two chunks, the last one short;
# a used last block in a byte that is not full (1030 % 8 != 0).
TOTAL, USED = 1030, list(range(0, 1000)) + list(range(1003, 1027)) + [1029]
STREAM = P.image(TOTAL, USED, 4096)
_BUILT: dict = {}


def built(raw: bool = False) -> tuple[bytes, str]:
    if raw not in _BUILT:
        _BUILT[raw] = C.build(STREAM, raw)
    return _BUILT[raw]


def put(tmp_path: Path, blob: bytes, name: str = "p3.win.pcl.zst") -> Path:
    path = tmp_path / name
    path.write_bytes(blob)
    return path


# --- the index the server derives ---------------------------------------------------


def test_the_file_decompresses_to_partclones_own_stream():
    """What keeps the pipe of today alive: the frames, one after another, are the stream."""
    blob, _ = built()
    assert C.decode_stored(blob) == STREAM


def test_the_derived_index_is_the_one_the_file_was_cut_with(tmp_path):
    blob, index = built()
    got = F.derive_index(put(tmp_path, blob), False, TOTAL * 4096, **DC)
    assert got.render() == index
    assert (got.kind, got.chunks, got.data_bytes) == ("partclone", 2, 1025 * 4096)
    assert got.chunk_bytes(0) == 4 * 1024 * 1024 and got.chunk_bytes(1) == 4096
    # the third column is the read-back list of #1864: nothing else is kept beside the file
    assert (got.readback_list(), got.data_bytes) == P.expected_list(P.stream(USED, 4096))
    assert F.parse_index(index) == got


def test_a_raw_partition_has_no_head_and_its_chunks_are_its_bytes(tmp_path):
    blob, index = built(raw=True)
    got = F.derive_index(put(tmp_path, blob), True, len(STREAM), **DC)
    assert got.render() == index and got.kind == "raw"
    assert (got.head_bytes, got.head_zbytes, got.head_xxh64) == (0, 0, None)
    assert index.splitlines()[0].split(" ")[4:7] == ["0", "0", "-"]
    assert (got.readback_list(), got.data_bytes) == P.expected_list(STREAM)


def test_an_empty_filesystem_is_a_head_and_no_chunk(tmp_path):
    blob, index = C.build(P.image(64, [], 4096))
    got = F.derive_index(put(tmp_path, blob), False, 64 * 4096, **DC)
    assert got.render() == index and got.chunks == 0 and index.count("\n") == 1


def test_data_that_is_a_whole_number_of_chunks_has_no_short_one(tmp_path):
    stream = P.image(1024, list(range(1024)), 4096)                 # exactly 4MiB
    blob, index = C.build(stream)
    got = F.derive_index(put(tmp_path, blob), False, 1024 * 4096, **DC)
    assert got.render() == index and got.chunks == 1 and got.chunk_bytes(0) == 4 * 1024 * 1024


def test_one_zstd_stream_is_not_image_format_2(tmp_path):
    """The old format: the whole partclone stream in one frame. No index, by name."""
    old = put(tmp_path, C.stored_frame(STREAM))
    with pytest.raises(RB.Underived, match="the first frame holds"):
        F.derive_index(old, False, TOTAL * 4096, **DC)


@pytest.mark.parametrize("damage, why", [
    (lambda blob: blob + b"\0", "no zstd frame starts here"),                      # a byte that is no frame's
    (lambda blob: blob[:-3], "cut|past the end"),                                   # the last frame is cut
    (lambda blob: C.stored_frame(C.split(STREAM, False)[0], checksum=False) + blob[len(C.stored_frame(C.split(STREAM, False)[0])):],
     "does not declare its content size and checksum"),
    (lambda blob: C.stored_frame(C.split(STREAM, False)[0], content_size=False) + blob[len(C.stored_frame(C.split(STREAM, False)[0])):],
     "does not declare its content size and checksum"),
    (lambda blob: b"", "no frame in the file"),
])
def test_anything_short_of_whole_frames_has_no_index(tmp_path, damage, why):
    blob, _ = built()
    with pytest.raises(RB.Underived, match=why):
        F.derive_index(put(tmp_path, damage(blob)), False, TOTAL * 4096, **DC)


def test_a_stream_with_partclone_checksums_is_refused(tmp_path):
    """image_format 2 is read with -a0: a chunk boundary is a byte of the data stream."""
    blob, _ = C.build(P.image(TOTAL, USED, 4096, checksum_size=4, per_checksum=256))
    with pytest.raises(RB.Underived, match="partclone checksums"):
        F.derive_index(put(tmp_path, blob), False, TOTAL * 4096, **DC)


def test_two_chunks_in_one_frame_are_not_a_chunk_frame(tmp_path):
    head, data = C.split(STREAM, False)
    blob = C.stored_frame(head) + C.stored_frame(data)               # 4MiB + 4KiB in ONE frame
    with pytest.raises(RB.Underived, match="chunk frames for 2 chunks"):
        F.derive_index(put(tmp_path, blob), False, TOTAL * 4096, **DC)


def test_frames_cut_in_the_wrong_place_are_not_chunks(tmp_path):
    """The right number of frames, the right bytes when decompressed in a row -- and
    a frame that is not a 4MiB chunk. Chunk k must be frame k, alone."""
    head, data = C.split(STREAM, False)
    blob = C.stored_frame(head) + C.stored_frame(data[:5000]) + C.stored_frame(data[5000:])
    assert C.decode_stored(blob) == STREAM
    with pytest.raises(RB.Underived, match="the frame of chunk 0 holds 5000 bytes, not 4194304"):
        F.derive_index(put(tmp_path, blob), False, TOTAL * 4096, **DC)


@requires_native("zstd", why="the server's real decompressor reads the frames")
def test_real_zstd_reads_the_same_frames(tmp_path):
    """The stored frames are zstd's format, not a private one: the real `zstd -dc`
    gives the stream, and the server's default path derives the same index."""
    blob, index = built()
    path = put(tmp_path, blob)
    plain = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True,
                           stdin=subprocess.DEVNULL).stdout
    assert plain == STREAM
    assert F.derive_index(path, False, TOTAL * 4096, new=PY).render() == index


# --- the index file: consistent with itself, or refused ------------------------------


def _lines(index: str) -> list[str]:
    return index.splitlines()


@pytest.mark.parametrize("change, why", [
    (lambda ls: [ls[0].replace("IMAGESRV-IDX 2", "IMAGESRV-IDX 3"), *ls[1:]], "index version"),
    (lambda ls: [ls[0].replace(" 4194304 ", " 1048576 "), *ls[1:]], "chunk_bytes"),
    (lambda ls: [ls[0], ls[2], ls[1]], "starts at"),                                 # frames out of order
    (lambda ls: ls[:-1], "chunk lines"),                                             # a line short
    (lambda ls: [ls[0], ls[1], ls[2].replace(ls[2].split()[2], "XYZ")], "16 lowercase hex"),
    (lambda ls: [ls[0], ls[1], ls[2].replace(ls[2].split()[2], ls[2].split()[2].upper() if any(c in "abcdef" for c in ls[2].split()[2]) else "A" * 16)],
     "16 lowercase hex"),
    (lambda ls: [" ".join(ls[0].split()[:-1] + ["1"]), *ls[1:]], "the header says the file is"),
    (lambda ls: [ls[0].replace(" partclone ", " tar "), *ls[1:]], "unknown kind"),
    (lambda ls: [ls[0].replace(" partclone ", " raw "), *ls[1:]], "a raw partition has no head"),
    (lambda ls: [ls[0], "0" + ls[1], ls[2]], "plain decimal"),                       # a leading zero
    (lambda ls: [ls[0], ls[1] + " extra", ls[2]], "not three fields"),
])
def test_an_index_that_disagrees_with_itself_is_refused(change, why):
    _, index = built()
    text = "\n".join(change(_lines(index))) + "\n"
    with pytest.raises(F.IndexProblem, match=why):
        F.parse_index(text)


def test_an_index_without_its_last_newline_is_not_a_whole_file():
    _, index = built()
    with pytest.raises(F.IndexProblem, match="newline"):
        F.parse_index(index[:-1])


def test_the_index_beside_a_file_of_another_length_is_not_read(tmp_path):
    blob, index = built()
    path = put(tmp_path, blob)
    F.index_path(path).write_text(index, encoding="ascii", newline="\n")
    assert F.read_index(path) == F.parse_index(index)
    path.write_bytes(blob + b"\0")                                   # not the file the index describes
    assert F.read_index(path) is None


# --- one chunk, cut out by its index line ---------------------------------------------


def _frame(blob: bytes, index: F.Index, k: int) -> bytes:
    offset, zbytes, _ = index.rows[k]
    return blob[offset:offset + zbytes]


def test_a_chunk_cut_out_by_the_index_stands_alone():
    blob, text = built()
    index = F.parse_index(text)
    for k in range(index.chunks):
        assert F.chunk_problem(index, k, _frame(blob, index, k), cmd=C.STORED_DC, new=PY) is None


def test_a_damaged_chunk_is_caught_by_its_hash():
    """The frame is a good zstd frame -- its own checksum is right -- and holds the
    wrong bytes. Only the index's XXH64 says so; such a chunk is never written."""
    blob, text = built()
    index = F.parse_index(text)
    wrong = bytearray(C.split(STREAM, False)[1][:4 * 1024 * 1024])
    wrong[123456] ^= 0x01
    problem = F.chunk_problem(index, 0, C.stored_frame(bytes(wrong)), cmd=C.STORED_DC, new=PY)
    assert problem is not None and "xxh64" in problem and index.rows[0][2] in problem


def test_the_frame_of_another_chunk_is_not_this_chunk():
    blob, text = built()
    index = F.parse_index(text)
    wrong_length = F.chunk_problem(index, 0, _frame(blob, index, 1), cmd=C.STORED_DC, new=PY)
    assert wrong_length is not None and "bytes" in wrong_length
    short = C.stored_frame(C.split(STREAM, False)[1][:4096])        # the right length for chunk 1, other bytes
    assert len(short) == index.rows[1][1]
    wrong_bytes = F.chunk_problem(index, 1, short, cmd=C.STORED_DC, new=PY)
    assert wrong_bytes is not None and "xxh64" in wrong_bytes


def test_a_frame_the_decompressor_refuses_is_a_problem_not_a_pass():
    blob, text = built()
    index = F.parse_index(text)
    torn = bytearray(_frame(blob, index, 1))
    torn[-1] ^= 0xFF                                                 # zstd's own checksum
    refused = F.chunk_problem(index, 1, bytes(torn), cmd=C.STORED_DC, new=PY)
    assert refused is not None and "did not decompress" in refused
    no_such = F.chunk_problem(index, 2, _frame(blob, index, 1), cmd=C.STORED_DC, new=PY)
    assert no_such is not None and "is not one of" in no_such


# --- the library: the index beside the file, only when the two derivations meet --------


def _image(root: Path, *, sign: str | None = None, blob: bytes | None = None,
           image_id: str = "img_2533aa") -> ImageLibrary:
    """An image whose p3 is a real image_format 2 file; `sign` is the index the
    capture signed (the manifest's index_xxh64) -- the file's own unless given."""
    file_blob, index = built()
    manifest = C.signed({**MANIFEST_256, "id": image_id}, {3: sign or index})
    write_image(root, manifest)
    (root / image_id / "p3.win.pcl.zst").write_bytes(blob or file_blob)
    return ImageLibrary(root)


def _on_disk(root: Path, image_id: str = "img_2533aa") -> dict:
    return json.loads((root / image_id / "manifest.json").read_text(encoding="utf-8"))


def _derive(path, raw, limit, stop=None):
    return F.derive_index(path, raw, None, stop, **DC)      # the fixture's size_bytes is a real disk's


def test_the_index_is_written_beside_the_file_when_it_is_the_one_the_capture_signed(tmp_path):
    lib = _image(tmp_path)
    _, index = built()
    results = RB.fill(lib, digest=_derive)
    assert [r["problems"] for r in results] == [[]] and sorted(results[0]["written"]) == [3]
    assert (tmp_path / "img_2533aa" / "p3.win.pcl.zst.idx").read_text(encoding="ascii") == index
    assert not (tmp_path / "img_2533aa" / "p3.win.pcl.zst.readback").exists()       # the index replaced it
    win = _on_disk(tmp_path)["partitions"][1]
    listing = F.parse_index(index).readback_list()
    assert (win["readback_chunks"], win["readback_bytes"], win["readback_chunk_bytes"]) == (2, 1025 * 4096, 4194304)
    assert win["readback_xxh64"] == xxh64_reference(listing.encode("ascii"))        # of the LIST, as the agent checks it
    assert "index_error" not in win and images.format_problem(lib.get("img_2533aa")) is None
    assert RB.fill(lib, digest=_derive) == []                                        # verified: not read again


def test_a_partition_without_a_signed_index_is_not_derived(tmp_path):
    """p1 of the fixture carries no index_xxh64: nothing to meet, nothing written."""
    lib = _image(tmp_path)
    RB.fill(lib, digest=_derive)
    assert "readback_xxh64" not in _on_disk(tmp_path)["partitions"][0]
    assert not (tmp_path / "img_2533aa" / "p1.esp.pcl.zst.idx").exists()


def test_a_damaged_chunk_in_the_library_is_caught_and_the_image_is_not_distributed(tmp_path):
    """The capture signed the index of what it read. The file in the library holds a
    chunk with other bytes -- every frame a good zstd frame. The derived index is
    not the signed one: no index file, `index_error` in the manifest, refused by name."""
    damaged, _ = C.build(STREAM, damage=lambda k, chunk: chunk[:-1] + bytes([chunk[-1] ^ 1]) if k == 1 else chunk)
    lib = _image(tmp_path, blob=damaged)
    seen = []
    RB.fill(lib, digest=_derive, report=seen.append)
    assert len(seen) == 1 and len(seen[0]["problems"]) == 1, seen
    assert "is not the one the capture signed" in seen[0]["problems"][0]
    win = _on_disk(tmp_path)["partitions"][1]
    assert "is not the one the capture" in win["index_error"] and "readback_xxh64" not in win
    assert not (tmp_path / "img_2533aa" / "p3.win.pcl.zst.idx").exists()
    code, reason = images.format_problem(lib.get("img_2533aa"))
    assert code == "index_mismatch" and "מחיצה 3" in reason
    assert images.restore_refusal(lib.get("img_2533aa"), None) == reason
    assert RB.fill(lib, digest=_derive) == []                                        # judged: not read every pass


def test_a_file_the_check_found_bad_is_marked_and_not_distributed(tmp_path):
    """The review of 12/11 (2-ד): a derivation that RAN and found the file is not zstd
    frames at all was folded into "not checked", and the image looked fine. It is
    positive evidence of a bad file: `index_error`, refused by name, not read again."""
    lib = _image(tmp_path, blob=b"not zstd frames at all")
    seen = []
    RB.fill(lib, digest=_derive, report=seen.append)
    assert len(seen) == 1 and len(seen[0]["problems"]) == 1 and sorted(seen[0]["written"]) == [3], seen
    assert "no zstd frame starts here" in seen[0]["problems"][0]
    win = _on_disk(tmp_path)["partitions"][1]
    assert "no zstd frame starts here" in win["index_error"] and "readback_xxh64" not in win
    code, reason = images.format_problem(lib.get("img_2533aa"))
    assert code == "index_mismatch" and "מחיצה 3" in reason and "פגום" in reason
    assert RB.fill(lib, digest=_derive) == []


def test_a_check_that_did_not_run_is_not_a_verdict(tmp_path):
    """"We could not check" is not "we checked, it is bad" (principle 5): the
    decompressor is not there, or was killed on a stalled read. No index and no
    index_error -- the image is distributed over the pipe of today, its read-back says
    NOT VERIFIED, and the next pass tries again when the files change."""
    lib = _image(tmp_path)

    def no_tool(path, raw, limit, stop=None):
        return F.derive_index(path, raw, None, stop, cmd=("no-such-decompressor-2533",),
                              frame_cmd=("no-such-decompressor-2533",), new=PY)
    seen = []
    RB.fill(lib, digest=no_tool, report=seen.append)
    assert len(seen) == 1 and "cannot run no-such-decompressor-2533" in seen[0]["problems"][0]
    assert seen[0]["written"] == {}
    win = _on_disk(tmp_path)["partitions"][1]
    assert "index_error" not in win and "readback_xxh64" not in win
    assert images.format_problem(lib.get("img_2533aa")) is None


def test_which_failures_are_evidence_about_the_file(tmp_path):
    blob, _ = built()
    path = put(tmp_path, blob)
    killed = (sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)")         if os.name != "nt" else None
    refused = (sys.executable, "-c", "import sys; sys.exit(1)")
    with pytest.raises(RB.BadImage, match="exited 1"):
        F.decode_frame(blob[:10], cmd=refused)                       # the tool ran and said no
    with pytest.raises(RB.Underived) as missing:
        F.decode_frame(blob[:10], cmd=("no-such-decompressor-2533",))
    assert not isinstance(missing.value, RB.BadImage)                # the tool did not run
    if killed is not None:
        with pytest.raises(RB.Underived) as dead:
            F.decode_frame(blob[:10], cmd=killed)
        assert not isinstance(dead.value, RB.BadImage)               # the tool was killed
    with pytest.raises(RB.BadImage):
        F.derive_index(put(tmp_path, blob + b"x", "other.zst"), False, TOTAL * 4096, **DC)
    assert path.exists()


def test_an_image_that_came_without_its_index_gets_it_back(tmp_path):
    """A transfer or an import copies manifest.json and the partition files -- not the
    index. It is derived again, and must again be the one the manifest signs."""
    lib = _image(tmp_path)
    RB.fill(lib, digest=_derive)
    (tmp_path / "img_2533aa" / "p3.win.pcl.zst.idx").unlink()
    assert RB.has_list(str(tmp_path / "img_2533aa"), _on_disk(tmp_path)["partitions"][1]) is False
    again = RB.fill(lib, digest=_derive)
    assert sorted(again[0]["written"]) == [3]
    assert (tmp_path / "img_2533aa" / "p3.win.pcl.zst.idx").read_text(encoding="ascii") == built()[1]


def test_record_refuses_a_list_the_agent_would_fail_good_drawers_on(tmp_path):
    lib = _image(tmp_path)
    _, index = built()
    good = F.parse_index(index)
    listing = good.readback_list()
    xxh = xxh64_reference(listing.encode("ascii"))
    for bad in ({"index": index, "list": listing, "list_xxh64": xxh, "bytes": 10 ** 15},          # more than the partition
                {"index": index, "list": listing[:17], "list_xxh64": xxh, "bytes": good.data_bytes},  # a line short
                {"index": index, "list": listing, "list_xxh64": xxh.upper(), "bytes": good.data_bytes},
                {"index": None, "list": listing, "list_xxh64": xxh, "bytes": good.data_bytes},
                (listing, good.data_bytes, xxh)):                                                   # the shape before #2533
        assert lib.record_index("img_2533aa", {3: bad}) == [], bad
    assert "readback_xxh64" not in _on_disk(tmp_path)["partitions"][1]
    assert not (tmp_path / "img_2533aa" / "p3.win.pcl.zst.idx").exists()


# --- the old format: labelled, not offered, refused by name ----------------------------


LEGACY = {k: v for k, v in MANIFEST_256.items() if k != "image_format"}


def test_a_manifest_without_the_mark_is_the_old_format():
    assert images.format_problem(MANIFEST_256) is None
    for manifest in (LEGACY, {**MANIFEST_256, "image_format": 1}, {**MANIFEST_256, "image_format": "2"},
                     {**MANIFEST_256, "image_format": True}, {**MANIFEST_256, "image_format": 3}):
        assert images.format_problem(manifest) == ("legacy_format", "האימג' בפורמט ישן — יש לקלוט אותו מחדש")
        assert images.restore_refusal(manifest, None) == "האימג' בפורמט ישן — יש לקלוט אותו מחדש"
        assert images.restore_refusal(manifest, ["b4:2e:99:07:1a:c4"]) is not None      # to one machine too


def _legacy_library(root: Path) -> ImageLibrary:
    write_image(root, {**LEGACY, "id": "img_01d000", "name": "Old Format"})
    write_image(root, {**MANIFEST_256, "id": "img_2e3000", "name": "New Format"})
    return ImageLibrary(root)


def test_the_library_labels_the_old_image_and_does_not_offer_it_to_a_machine(tmp_path):
    lib = _legacy_library(tmp_path)
    shown = {item["id"]: item for item in lib.public_list()}
    assert (shown["img_01d000"]["format_problem"], shown["img_01d000"]["format_reason"]) == \
        ("legacy_format", "פורמט ישן — יש לקלוט מחדש")
    assert (shown["img_2e3000"]["format_problem"], shown["img_2e3000"]["format_reason"]) == (None, None)
    assert lib.allowed_for_disks(None) == ["img_2e3000"]
    assert lib.allowed_for_disks([{"dev": "sda", "size_bytes": 500107862016, "removable": False}]) == ["img_2e3000"]


def test_an_old_image_is_not_digested(tmp_path):
    """Decompressing 100GB for an image nobody may distribute is not done."""
    lib = _legacy_library(tmp_path)

    def never(*args, **kwargs):
        raise AssertionError("an old-format image was read")
    assert RB.fill(lib, digest=never) == []


def _make_legacy(images_root: Path, image_id: str = "img_7f3a91") -> None:
    path = images_root / image_id / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("image_format")
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def test_a_cloner_room_round_of_an_old_image_is_refused_by_name(room_server):
    """Not opened to fail at its first wave: refused when asked, with the reason."""
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    _make_legacy(Path(ctx.library.root))
    answer = deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert answer.status_code == 400, answer.text
    assert "פורמט ישן" in answer.json()["detail"] and "לקלוט" in answer.json()["detail"]
    assert ctx.conn.execute("SELECT COUNT(*) FROM room_rounds").fetchone()[0] == 0
    assert ctx.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_a_class_round_and_a_pull_of_an_old_image_are_refused_by_name(server, images_root):
    from conftest import setup_classroom
    ids = setup_classroom(server)
    _make_legacy(images_root)
    round_ = server["deploy"].post("/api/console/sessions",
                                   json={"group_id": ids["group"], "image_id": "img_7f3a91"})
    assert round_.status_code == 400 and "פורמט ישן" in round_.json()["detail"], round_.text
    pull = server["anon"].post("/api/v1/agent/pulls", json={
        "mac": ids["mac1"], "image_id": "img_7f3a91", **ADMIN})
    assert pull.status_code in (400, 403), pull.text
    assert "פורמט ישן" in pull.json()["error"], pull.text
    assert server["ctx"].conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_the_console_list_carries_the_label(server, images_root):
    _make_legacy(images_root)
    shown = {item["id"]: item for item in server["admin"].get("/api/console/images").json()}
    assert shown["img_7f3a91"]["format_problem"] == "legacy_format"
    assert shown["img_7f3a91"]["format_reason"] == "פורמט ישן — יש לקלוט מחדש"
    assert shown["img_2c8e04"]["format_problem"] is None


# --- the wire: the index, the list, and one chunk by Range -----------------------------


def _served(server, images_root: Path) -> tuple[bytes, str]:
    """img_7f3a91 of the server fixture, its p3 a real image_format 2 file with a verified index."""
    blob, index = built()
    path = images_root / "img_7f3a91" / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    signed = C.signed(raw, {3: index})
    import hashlib
    for part in signed["partitions"]:
        if part["index"] == 3:
            part["sha256"] = hashlib.sha256(blob).hexdigest()
    path.write_text(json.dumps(signed, ensure_ascii=False), encoding="utf-8")
    (images_root / "img_7f3a91" / "p3.win.pcl.zst").write_bytes(blob)
    return blob, index


def test_the_index_and_the_list_are_served_only_once_verified(server, images_root):
    anon, ctx = server["anon"], server["ctx"]
    blob, index = _served(server, images_root)
    assert anon.get("/api/v1/images/img_7f3a91/index/3").status_code == 404        # not derived yet
    assert anon.get("/api/v1/images/img_7f3a91/readback/3").status_code == 404
    assert RB.fill(ctx.library, digest=_derive)[0]["problems"] == []
    got = anon.get("/api/v1/images/img_7f3a91/index/3")
    assert got.status_code == 200 and got.text == index
    assert xxh64_reference(got.content) == ctx.library.get("img_7f3a91")["partitions"][1]["index_xxh64"]
    listing = anon.get("/api/v1/images/img_7f3a91/readback/3")
    assert listing.status_code == 200 and listing.text == F.parse_index(index).readback_list()
    assert anon.get("/api/v1/images/img_7f3a91/index/1").json()["code"] == "no_index"   # p1 signs none
    assert anon.get("/api/v1/images/img_7f3a91/index/9").status_code == 404


def test_one_chunk_is_fetched_by_range_and_stands_alone(server, images_root):
    """What a machine that missed a chunk asks for (#2533): the frame, by the index
    line, over the route that already serves the partition file."""
    anon = server["anon"]
    blob, text = _served(server, images_root)
    index = F.parse_index(text)
    for k, (offset, zbytes, _) in enumerate(index.rows):
        got = anon.get("/api/v1/images/img_7f3a91/files/p3.win.pcl.zst",
                       headers={"Range": f"bytes={offset}-{offset + zbytes - 1}"})
        assert got.status_code == 206, got.status_code
        assert got.content == blob[offset:offset + zbytes]
        assert F.chunk_problem(index, k, got.content, cmd=C.STORED_DC, new=PY) is None
    head = anon.get("/api/v1/images/img_7f3a91/files/p3.win.pcl.zst",
                    headers={"Range": f"bytes=0-{index.head_zbytes - 1}"})
    assert head.status_code == 206 and xxh64_reference(C.decode_stored(head.content)) == index.head_xxh64


# --- capture: what is not image_format 2 does not enter the library --------------------


def test_a_capture_in_the_old_format_is_refused_at_the_manifest(server):
    from test_capture import do_capture, make_task, manifest_for, setup_build_machine
    mac = setup_build_machine(server)
    for change, why in ((lambda m: m.pop("image_format"), "not image_format 2"),
                        (lambda m: m["partitions"][1].pop("index_xxh64"), "partition 3 carries no index_xxh64"),
                        (lambda m: m["partitions"][0].update(index_xxh64="XYZ"), "partition 1 carries no index_xxh64")):
        task = make_task(server, mac).json()["id"]
        manifest = manifest_for()
        change(manifest)
        answer = do_capture(server, task, manifest)
        assert answer.status_code == 400 and why in answer.json()["detail"], answer.text
        row = server["ctx"].conn.execute("SELECT state, error, image_id FROM tasks WHERE id = ?", (task,)).fetchone()
        assert row["state"] == "failed" and why in row["error"]
        assert server["ctx"].library.get(row["image_id"]) is None


def test_a_capture_in_the_new_format_enters_the_library(server):
    from test_capture import do_capture, make_task, manifest_for, setup_build_machine
    mac = setup_build_machine(server)
    task = make_task(server, mac).json()["id"]
    answer = do_capture(server, task)
    assert answer.status_code == 202, answer.text
    manifest = server["ctx"].library.get(answer.json()["image_id"])
    assert manifest["image_format"] == 2 and images.format_problem(manifest) is None
    assert manifest["partitions"][1]["index_xxh64"] == manifest_for()["partitions"][1]["index_xxh64"]


def test_every_door_that_opens_a_restore_asks_the_format_gate():
    """The gate is one function, restore_refusal, and it asks format_problem first."""
    import inspect
    source = inspect.getsource(images.restore_refusal)
    assert source.index("format_problem(manifest)") < source.index("bound_mac(manifest)")


# --- the server cuts too: a version saved from a VM (#1334) ----------------------------


def test_the_servers_cut_is_the_same_file_and_index(tmp_path):
    """vmdisk saves a version from a VM disk on the server: no agent, no chunkcut. The
    cut is the same one -- the head, then a frame for every 4MiB -- and its index is
    the one the derivation gives back."""
    out = io.BytesIO()
    text, sha, total = F.cut(io.BytesIO(STREAM), out, False, compress=C.stored_frame, new=PY)
    blob, index = built()
    assert (out.getvalue(), text, total) == (blob, index, len(blob))
    import hashlib
    assert sha == hashlib.sha256(blob).hexdigest()
    raw_out = io.BytesIO()
    raw_text, _, _ = F.cut(io.BytesIO(STREAM), raw_out, True, compress=C.stored_frame, new=PY)
    assert (raw_out.getvalue(), raw_text) == built(raw=True)


@pytest.mark.parametrize("name, stream, raw, why", [
    ("checksums", lambda: P.image(TOTAL, USED, 4096, checksum_size=4, per_checksum=256), False, "with -a0"),
    ("a byte short", lambda: STREAM[:-1], False, "ended 1 bytes early"),
    ("a byte long", lambda: STREAM + b"x", False, "bytes after the last block"),
    ("cut in the map", lambda: STREAM[:150], False, "inside the bit map"),
    ("not partclone", lambda: bytes(4096), False, "no valid partclone v2 header"),
    ("empty raw", lambda: b"", True, "an empty stream"),
], ids=lambda v: v if isinstance(v, str) and len(v) < 20 else None)
def test_the_servers_cut_refuses_a_stream_that_is_not_whole(name, stream, raw, why):
    with pytest.raises(F.CutProblem, match=why):
        F.cut(io.BytesIO(stream()), io.BytesIO(), raw, compress=C.stored_frame, new=PY)


def test_the_servers_cut_stops_a_file_that_outgrows_its_partition():
    with pytest.raises(F.CutProblem, match="grew past 100 bytes"):
        F.cut(io.BytesIO(STREAM), io.BytesIO(), False, compress=C.stored_frame, new=PY, limit=100)


@requires_native("zstd", why="the cut's own compressor is the system's zstd")
def test_the_servers_cut_with_real_zstd_derives_back(tmp_path):
    path = tmp_path / "p3.win.pcl.zst"
    with path.open("wb") as fh:
        text, sha, total = F.cut(io.BytesIO(STREAM), fh, False, new=PY)
    assert total == path.stat().st_size
    assert subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True,
                          stdin=subprocess.DEVNULL).stdout == STREAM
    assert F.derive_index(path, False, TOTAL * 4096, new=PY).render() == text
