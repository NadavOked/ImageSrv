"""#2533 / #2528 stage 2 -- the agent's side of the chunk format: chunkcut, the capture
that calls it, and the ledger of durable chunks.

`agent/chunkcut.c` stands where `zstd` stood in the capture's pipe: it reads
partclone's stream and writes the same bytes as zstd frames that stand alone --
the head, then one frame for every 4MiB of the data stream -- and the index of
those frames. What these tests hold, with the real tools (gcc, the libzstd every
Debian carries, zstd, partclone):

* `zstd -dc` of what it writes is the stream it read, byte for byte -- so the
  pipe of today (udp-sender -> zstd -dc -> partclone -r) restores it unchanged;
* the index it writes is the one the SERVER derives from the file by itself
  (server/library/chunkformat.py) -- the two were written apart, and meet;
* the output does not depend on the number of threads (a direct send reads the
  disk twice and its receivers hash pass 2 against pass 1);
* a stream that is not whole has no index -- never a guessed one;
* a real FAT filesystem goes capture -> chunkcut -> zstd -dc -> partclone -r and
  comes back with every file;
* the ledger of durable chunks names a chunk only after an fsync that came back 0.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import pcl_image as P
from agentlib import agent_sources
from native import requires_native, with_sbin
from server.agent import readbackhash as RB
from server.library import chunkformat as F
from test_agent import AGENT, BASH, posix, sh
from test_fanout import xxh64_reference

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "chunkcut.c"
CAPTURE = AGENT / "lib" / "capture" / "capture.sh"
SINK = AGENT / "lib" / "capture" / "capturesink.sh"
DIRECT = AGENT / "lib" / "capture" / "directsend.sh"
PY = RB.PyXxh64

needs_cut = requires_native("gcc", "zstd", posix=True, why="chunkcut is C, linked to the system's libzstd")
needs_sh = requires_native(("bash", BASH), posix=True, why="the agent's shell")

TOTAL, USED = 1030, list(range(0, 1000)) + list(range(1003, 1027)) + [1029]     # 4MiB + 4KiB of data
STREAM = P.image(TOTAL, USED, 4096)
HEAD_BYTES = 110 + (TOTAL + 7) // 8 + 4


@pytest.fixture(scope="module")
def chunkcut(tmp_path_factory):
    """Built as tools/build_initramfs.sh builds it: no libzstd-dev, the library by its file name."""
    binary = tmp_path_factory.mktemp("cutbuild") / "chunkcut"
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-o", str(binary),
                    str(SOURCE), "-l:libzstd.so.1"], check=True, stdin=subprocess.DEVNULL)
    return binary


def cut(binary: Path, data: bytes, where: Path, *flags: str) -> tuple[int, bytes, str, Path]:
    index = where / "idx"
    done = subprocess.run([str(binary), *flags, "-i", str(index)], input=data, capture_output=True)
    return done.returncode, done.stdout, done.stderr.decode("utf-8", "replace"), index


def unzstd(blob: bytes) -> bytes:
    done = subprocess.run(["zstd", "-dc"], input=blob, capture_output=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


# --- what chunkcut writes -----------------------------------------------------------


@needs_cut
def test_zstd_dc_of_the_frames_is_the_stream_that_was_read(chunkcut, tmp_path):
    rc, out, err, index = cut(chunkcut, STREAM, tmp_path)
    assert rc == 0, err
    assert unzstd(out) == STREAM                               # the pipe of today reads it unchanged
    parsed = F.parse_index(index.read_text(encoding="ascii"))
    assert (parsed.kind, parsed.chunks, parsed.data_bytes, parsed.head_bytes) == \
        ("partclone", 2, 1025 * 4096, HEAD_BYTES)
    assert parsed.file_zbytes == len(out)


@needs_cut
def test_the_index_is_the_one_the_server_derives_by_itself(chunkcut, tmp_path):
    """Two derivations, written apart: the agent's from the bytes it read before it
    compressed them, the server's from the frames of the file and what they
    decompress to. They are the same file -- and its XXH64 is the manifest's."""
    rc, out, err, index = cut(chunkcut, STREAM, tmp_path)
    assert rc == 0, err
    path = tmp_path / "p3.win.pcl.zst"
    path.write_bytes(out)
    text = index.read_text(encoding="ascii")
    derived = F.derive_index(path, False, TOTAL * 4096, new=PY)
    assert derived.render() == text
    assert (derived.readback_list(), derived.data_bytes) == P.expected_list(P.stream(USED, 4096))
    assert Path(str(index) + ".xxh64").read_text(encoding="ascii") == xxh64_reference(text.encode("ascii")) + "\n"


@needs_cut
def test_every_chunk_and_the_head_stand_alone(chunkcut, tmp_path):
    rc, out, err, index = cut(chunkcut, STREAM, tmp_path)
    assert rc == 0, err
    parsed = F.parse_index(index.read_text(encoding="ascii"))
    data = P.stream(USED, 4096)
    assert unzstd(out[:parsed.head_zbytes]) == STREAM[:HEAD_BYTES]
    assert xxh64_reference(STREAM[:HEAD_BYTES]) == parsed.head_xxh64
    for k, (offset, zbytes, xxh) in enumerate(parsed.rows):
        piece = unzstd(out[offset:offset + zbytes])
        assert piece == data[k * 4194304:(k + 1) * 4194304] and xxh64_reference(piece) == xxh
        assert F.chunk_problem(parsed, k, out[offset:offset + zbytes], new=PY) is None


@needs_cut
@pytest.mark.parametrize("level", ["3", "19"])
def test_every_frame_says_its_size_carries_a_checksum_and_needs_a_small_window(chunkcut, tmp_path, level):
    """What a receiver may count on (interfaces.md section 7): the content size in the
    frame header, zstd's checksum at its end, and a window of 4MiB at most for a chunk."""
    rc, out, err, _ = cut(chunkcut, STREAM, tmp_path, "-l", level)
    assert rc == 0, err
    path = tmp_path / "f.zst"
    path.write_bytes(out)
    frames = list(F.walk_frames(path))
    assert [f[2] for f in frames] == [HEAD_BYTES, 4194304, 4096] and all(f[3] for f in frames)
    for offset, zbytes, _, _ in frames[1:]:
        fhd = out[offset + 4]
        if not fhd & 0x20:                                     # not single-segment: a window descriptor
            wd = out[offset + 5]
            log, mantissa = 10 + (wd >> 3), wd & 7
            assert (1 << log) + ((1 << log) >> 3) * mantissa <= 4194304


@needs_cut
def test_the_bytes_do_not_depend_on_the_number_of_threads(chunkcut, tmp_path):
    """A direct send reads the disk twice, and pass 2 must hash to pass 1 (#1574)."""
    outs = set()
    for threads in ("1", "2", "7", "0"):
        where = tmp_path / threads
        where.mkdir()
        rc, out, err, index = cut(chunkcut, STREAM, where, "-T", threads)
        assert rc == 0, err
        outs.add((hashlib.sha256(out).hexdigest(), index.read_text(encoding="ascii")))
    assert len(outs) == 1


@needs_cut
def test_a_raw_partition_is_cut_whole_and_has_no_head(chunkcut, tmp_path):
    data = os.urandom(4194304 + 777)
    rc, out, err, index = cut(chunkcut, data, tmp_path, "--raw")
    assert rc == 0, err
    assert unzstd(out) == data
    parsed = F.parse_index(index.read_text(encoding="ascii"))
    assert (parsed.kind, parsed.chunks, parsed.head_zbytes, parsed.head_xxh64) == ("raw", 2, 0, None)
    path = tmp_path / "p2.msr.pcl.zst"
    path.write_bytes(out)
    assert F.derive_index(path, True, len(data), new=PY) == parsed


@needs_cut
def test_an_empty_filesystem_is_a_head_and_no_chunk(chunkcut, tmp_path):
    stream = P.image(64, [], 4096)
    rc, out, err, index = cut(chunkcut, stream, tmp_path)
    assert rc == 0, err
    assert unzstd(out) == stream
    assert index.read_text(encoding="ascii").count("\n") == 1


BROKEN = {
    "checksums inside": (lambda: P.image(TOTAL, USED, 4096, checksum_size=4, per_checksum=256), (), "checksums"),
    "a byte short": (lambda: STREAM[:-1], (), "ended 1 bytes early"),
    "a byte long": (lambda: STREAM + b"\0", (), "bytes after the last block"),
    "cut in the map": (lambda: STREAM[:150], (), "inside the bit map"),
    "cut in the header": (lambda: STREAM[:50], (), "inside the header"),
    "not partclone": (lambda: b"\0" * 4096, (), "no partclone v2 header"),
    "a map that disagrees": (lambda: P.header(64, 4, 4096) + P.bitmap(64, [1, 2, 3]) + b"\0" * 4 + b"\0" * 4 * 4096, (),
                             "marks 3 blocks, the header 4"),
    "empty raw": (lambda: b"", ("--raw",), "an empty stream"),
}


@needs_cut
@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_stream_that_is_not_whole_has_no_index(chunkcut, tmp_path, case):
    """An index is the evidence that the stream was cut whole. An index left by an
    earlier cut is not this one's evidence, and goes first."""
    stale = tmp_path / "idx"
    stale.write_text("IMAGESRV-IDX 2 raw 4194304 0 0 - 1 1 9\n0 9 0000000000000000\n")
    Path(str(stale) + ".xxh64").write_text("0" * 16 + "\n")
    make, flags, why = BROKEN[case]
    rc, _, err, index = cut(chunkcut, make(), tmp_path, *flags)
    assert rc == 1 and why in err, (rc, err)
    assert not index.exists() and not Path(str(index) + ".xxh64").exists()


@needs_cut
@pytest.mark.parametrize("flags", [("-l", "0"), ("-l", "20"), ("-T", "-1"), ("--what",), ()])
def test_a_wrong_command_line_is_refused(chunkcut, tmp_path, flags):
    args = [str(chunkcut), *flags] + (["-i", str(tmp_path / "idx")] if flags else [])
    done = subprocess.run(args, input=STREAM, capture_output=True)
    assert done.returncode == 2 and b"usage" in done.stderr


@needs_cut
def test_a_reader_that_leaves_is_a_failure_not_a_silent_death(chunkcut, tmp_path):
    """stdout that goes away (curl died, the fifo closed) is exit 1 with a reason --
    the shell reads this stage's rc by name (capture.sh)."""
    data = os.urandom(3 * 4194304)
    proc = subprocess.Popen([str(chunkcut), "--raw", "-i", str(tmp_path / "idx")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdout.close()
    try:
        proc.stdin.write(data)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    assert proc.wait(timeout=30) == 1
    assert b"writing the" in proc.stderr.read()
    assert not (tmp_path / "idx").exists()


# --- a real filesystem: capture -> chunkcut -> zstd -dc -> partclone -r ---------------


def _tool(name: str):
    return shutil.which(name, path=with_sbin(os.environ.get("PATH", "")))


@needs_cut
@requires_native(("partclone.fat", _tool("partclone.fat")), ("mformat", _tool("mformat")),
                 ("mcopy", _tool("mcopy")), why="a real FAT filesystem, read and written by partclone")
def test_a_real_fat_partition_is_captured_cut_restored_and_indexed(chunkcut, tmp_path):
    """The whole road with the real tools, on files (no root): a FAT32 filesystem
    with files in it is read by `partclone.fat -c -a0` (the capture's own flags),
    cut by chunkcut, decompressed by `zstd -dc` and written by `partclone.fat -r`
    -- the restore's own pipe. Every file comes back, and the server derives the
    index chunkcut wrote."""
    pcl, mformat, mcopy = _tool("partclone.fat"), _tool("mformat"), _tool("mcopy")
    src, dst = tmp_path / "src.img", tmp_path / "dst.img"
    size = 96 * 1024 * 1024
    for image in (src, dst):
        with image.open("wb") as fh:
            fh.truncate(size)
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE}   # stdin= is spelled out at each call (#541 scanner)
    subprocess.run([mformat, "-i", str(src), "-F", "-v", "EFI", "::"], check=True, stdin=subprocess.DEVNULL, **quiet)
    files = {"big.bin": os.urandom(9 * 1024 * 1024 + 123), "zeros.bin": b"\0" * (5 * 1024 * 1024),
             "hello.txt": b"image_format 2\n"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        subprocess.run([mcopy, "-i", str(src), str(tmp_path / name), f"::{name}"], check=True, stdin=subprocess.DEVNULL, **quiet)

    read = subprocess.Popen([pcl, "-c", "-a0", "-z", "524288", "-s", str(src), "-L", str(tmp_path / "c.log")],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    index = tmp_path / "idx"
    packed = tmp_path / "p1.esp.pcl.zst"
    with packed.open("wb") as fh:
        cutter = subprocess.run([str(chunkcut), "-l", "3", "-T", "0", "-i", str(index)],
                                stdin=read.stdout, stdout=fh, stderr=subprocess.PIPE)
    read.stdout.close()
    assert read.wait(timeout=120) == 0 and cutter.returncode == 0, cutter.stderr

    plain = subprocess.Popen(["zstd", "-dc", str(packed)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    write = subprocess.run([pcl, "-r", "-s", "-", "-O", str(dst), "-L", str(tmp_path / "r.log")],
                           stdin=plain.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    plain.stdout.close()
    assert plain.wait(timeout=120) == 0 and write.returncode == 0, write.stderr

    for name, data in files.items():
        back = tmp_path / f"back.{name}"
        subprocess.run([mcopy, "-i", str(dst), f"::{name}", str(back)], check=True, stdin=subprocess.DEVNULL, **quiet)
        assert back.read_bytes() == data, name

    text = index.read_text(encoding="ascii")
    derived = F.derive_index(packed, False, size, new=PY)
    assert derived.render() == text and derived.chunks >= 3
    assert RB.list_digest(text, PY) == Path(str(index) + ".xxh64").read_text(encoding="ascii").strip()


# --- the capture calls it, in both of its pipes ----------------------------------------


def test_both_reads_of_a_direct_send_and_the_capture_cut_through_one_function():
    """One place decides how a partition is cut -- like capture_pcl_flags (#1574):
    pass 2 of a direct send must give the bytes pass 1 hashed."""
    capture, direct, sink = (p.read_text(encoding="utf-8") for p in (CAPTURE, DIRECT, SINK))
    assert '| { capture_cut "$_pcl" "$RUN_DIR/idx.$_idx" 2>> "$LOG_FILE"' in capture
    assert '| { capture_cut "$_do_pcl" "$RUN_DIR/didx.$2" 2>> "$LOG_FILE"' in direct
    assert 'chunkcut $_cc_raw -l "$CAPTURE_LEVEL" -T "$CAPTURE_THREADS" -i "$2"' in sink
    for source in (capture, direct):
        assert "zstd -" not in source.replace("zstd -dc", ""), "a second compressor beside capture_cut"


def test_the_manifest_says_image_format_2_and_carries_the_index_digest():
    capture = CAPTURE.read_text(encoding="utf-8")
    assert "printf '{\"schema\":1,\"image_format\":2,\"family\":%s," in capture
    assert '\\"sha256\\":\\"$_sha\\"$_ixj,' in capture
    # no digest of the index = the partition fails there, before the next one is read
    assert '_ixj=$(capture_index_json "$RUN_DIR/idx.$_idx"); [ -n "$_ixj" ] || { _capture_failed' in capture


def test_the_boot_image_builds_chunkcut_without_a_new_package():
    """libzstd-dev is not on a server in the field, and its update rebuilds the boot
    image offline: the library is linked by its file name and rides along through ldd."""
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert ('gcc -O2 -Wall -Wextra -pthread -o "$ROOT/usr/bin/chunkcut" "$AGENT_DIR/chunkcut.c" -l:libzstd.so.1'
            in build)
    assert 'copy_libs "$ROOT/usr/bin/chunkcut"' in build
    assert "libzstd-dev" not in build.replace("no libzstd-dev", "")
    assert "#include <zstd.h>" not in SOURCE.read_text(encoding="utf-8")


def _sink(script: str, tmp_path: Path) -> str:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "chunkcut").write_text('#!/bin/sh\necho "chunkcut $*" >> "$BOX/calls"\ncat\n', newline="\n")
    (stub / "chunkcut").chmod(0o755)
    return sh(f'export PATH={posix(stub)!r}:"$PATH" BOX={posix(tmp_path)!r} CAPTURE_LEVEL=3 CAPTURE_THREADS=0; '
              f'. {posix(SINK)}; ' + script)


@needs_sh
def test_capture_cut_marks_only_a_dd_partition_raw(tmp_path):
    out = _sink('printf abc | capture_cut partclone.ntfs "$BOX/i1"; printf def | capture_cut partclone.dd "$BOX/i2"',
                tmp_path)
    assert out == "abcdef"
    assert (tmp_path / "calls").read_text().splitlines() == [
        f"chunkcut -l 3 -T 0 -i {posix(tmp_path)}/i1", f"chunkcut --raw -l 3 -T 0 -i {posix(tmp_path)}/i2"]


@needs_sh
@pytest.mark.parametrize("index, digest, want", [
    ("IMAGESRV-IDX 2 raw 4194304 0 0 - 0 0 0\n", "0123456789abcdef\n", ',"index_xxh64":"0123456789abcdef"'),
    ("IMAGESRV-IDX 2 raw 4194304 0 0 - 0 0 0\n", None, ""),             # no digest beside it
    ("IMAGESRV-IDX 2 raw 4194304 0 0 - 0 0 0\n", "0123456789ABCDEF\n", ""),   # not the lowercase the server reads
    ("IMAGESRV-IDX 2 raw 4194304 0 0 - 0 0 0\n", "0123\n", ""),
    ("", "0123456789abcdef\n", ""),                                      # an empty index is no index
    (None, "0123456789abcdef\n", ""),                                    # chunkcut did not end clean
])
def test_the_manifest_field_is_the_digest_or_nothing(tmp_path, index, digest, want):
    """Never a guessed value: capture_disk fails the partition on an empty answer."""
    if index is not None:
        (tmp_path / "idx").write_text(index, newline="\n")
    if digest is not None:
        (tmp_path / "idx.xxh64").write_text(digest, newline="\n")
    assert _sink('capture_index_json "$BOX/idx"', tmp_path) == want


# --- the ledger: a chunk is durable only after an fsync that came back 0 ----------------


DD = ('#!/bin/sh\necho "dd $*" >> "$BOX/dd.calls"\n'
      # a chunk the writer finishes WHILE the fsync runs: it was not written before it started
      '[ ! -e "$BOX/late" ] || { cat "$BOX/late" > "$T/ledger/written.3.new" && mv "$T/ledger/written.3.new" "$T/ledger/written.3"; }\n'
      'if [ -e "$BOX/eio" ]; then echo "dd: fsync failed: Input/output error" >&2; exit 1; fi\nexit 0\n')


def _ledger(tmp_path: Path, script: str) -> str:
    box = tmp_path / "box"
    (box / "bin").mkdir(parents=True, exist_ok=True)
    (box / "bin" / "dd").write_text(DD, encoding="utf-8", newline="\n")
    (box / "bin" / "dd").chmod(0o755)
    run = box / "run"
    (run / "targets" / "sdb" / "ledger").mkdir(parents=True, exist_ok=True)
    return sh(f'export PATH={posix(box / "bin")!r}:"$PATH" BOX={posix(box)!r} RUN_DIR={posix(run)!r} '
              f'LOG_FILE={posix(box / "log")!r}; T="$RUN_DIR/targets/sdb"; export T; '
              + agent_sources("common", "chunkledger") + script)


@needs_sh
def test_nothing_is_durable_before_an_fsync(tmp_path):
    out = _ledger(tmp_path, 'printf "0-41\\n" > "$T/ledger/written.3"; ledger_json sdb; echo "[$?]"')
    assert out.strip() == "[0]"                                # written is not durable: no field at all


@needs_sh
def test_what_was_written_before_a_good_fsync_is_durable_after_it(tmp_path):
    out = _ledger(tmp_path, 'printf "0-41\\n44-50\\n" > "$T/ledger/written.3"; printf "0-7\\n" > "$T/ledger/written.1"; '
                            'ledger_sync sdb /dev/sdb3; echo "rc=$?"; ledger_json sdb')
    assert out.splitlines()[0] == "rc=0"
    assert out.splitlines()[1] == ',"durable":[{"part":1,"ranges":[[0,7]]},{"part":3,"ranges":[[0,41],[44,50]]}]'
    assert "conv=notrunc,fsync" in (tmp_path / "box" / "dd.calls").read_text()
    assert "of=/dev/sdb3" in (tmp_path / "box" / "dd.calls").read_text()


@needs_sh
def test_a_chunk_written_while_the_fsync_ran_waits_for_the_next_one(tmp_path):
    """The snapshot is taken BEFORE the fsync starts. A chunk that lands during it was
    not covered by it -- the ledger may lag behind the disk, never lead it."""
    (tmp_path / "box").mkdir()
    (tmp_path / "box" / "late").write_text("0-99\n", newline="\n")
    out = _ledger(tmp_path, 'printf "0-41\\n" > "$T/ledger/written.3"; ledger_sync sdb /dev/sdb3; ledger_json sdb; echo; '
                            'rm "$BOX/late"; ledger_sync sdb /dev/sdb3; ledger_json sdb')
    first, second = out.splitlines()
    assert first == ',"durable":[{"part":3,"ranges":[[0,41]]}]'       # not 0-99: that came during the fsync
    assert second == ',"durable":[{"part":3,"ranges":[[0,99]]}]'      # the next fsync vouches for it


@needs_sh
def test_a_refused_fsync_makes_nothing_durable(tmp_path):
    out = _ledger(tmp_path, 'printf "0-41\\n" > "$T/ledger/written.3"; ledger_sync sdb /dev/sdb3; '
                            'printf "0-80\\n" > "$T/ledger/written.3"; : > "$BOX/eio"; '
                            'ledger_sync sdb /dev/sdb3; echo "rc=$?"; ledger_json sdb')
    assert out.splitlines()[-2:] == ["rc=1", ',"durable":[{"part":3,"ranges":[[0,41]]}]']   # what the good one vouched for, no more
    log = tmp_path / "box" / "log"                        # log() says it on stdout, and in LOG_FILE where there is one
    assert "fsync failed -- nothing more is reported durable on sdb" in out + (log.read_text() if log.exists() else "")


@needs_sh
@pytest.mark.parametrize("bad", ["0-41 x", "41-0", "0-41-50", "0", "-5"])
def test_a_ledger_that_cannot_be_read_is_not_reported_in_part(tmp_path, bad):
    out = _ledger(tmp_path, f'printf "0-7\\n" > "$T/ledger/durable.1"; '
                            f"printf '%s\\n' {' '.join(repr(line) for line in bad.split())} > \"$T/ledger/durable.3\"; "
                            'grep -c . "$T/ledger/durable.3"; ledger_json sdb; echo "[$?]"')
    assert out.split() == [str(len(bad.split())), "[0]"]         # the bad file is there, and the field is not


def test_the_report_carries_the_field_and_the_agent_loads_the_library():
    progress = (AGENT / "lib" / "core" / "progress.sh").read_text(encoding="utf-8")
    # #3181 appended drawer_back_json to the same line: the anchor is the line's start and its close, not its whole
    line = [ln.strip() for ln in progress.splitlines() if '$(ledger_json "$_dev" 2>/dev/null)' in ln]
    assert len(line) == 1, line
    assert line[0].startswith('_entry="$_entry$(ledger_json "$_dev" 2>/dev/null)$(target_extra_json "$_dev" 2>/dev/null)')
    assert line[0].split("   #")[0].endswith('}"'), line[0]
    assert '. "$LIB_DIR/chunkledger.sh"' in (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
