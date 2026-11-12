"""#2023: inline full read-back on real loop devices (root, Linux Testrunner).

The engine fixtures create their own disks; never use a host disk. Faults are
injected into the real C writer/reader with LD_PRELOAD, restricted to one exact
fixture partition. In particular corruption happens at fsync, AFTER the writes,
and an open failure affects only O_DIRECT readers, not the copy. No source-text
assertions. Compile with the production warning policy, including -Werror.

Repair before failing (Nadav, 22/10): a chunk read back wrong is rewritten from
the source and read again (twice at most). A one-time loss ends "repaired" --
done, orange, the files right; a sector that keeps giving back the wrong byte
ends red by partition, the copy to that drawer stopped before its end, and the
other drawer verified.

Run on Linux (the Testrunner): pytest tests/test_inline_readback_2023.py -q
Negative control: docs/negative-controls/2023-inline-readback.json (tools/bakara-shlilit.py).
"""
from __future__ import annotations

from pathlib import Path
import re
import shlex
import subprocess
import time

import pytest

import pcl_image as P
import test_local_clone_engine as E

pytestmark = E.pytestmark
disks = E.disks
CH = 4194304

# Only loaded by the --inline-write invocation, with an exact fixture node.
# The real syscalls still perform all disk I/O. A logged injection is required
# by the assertions, so a hook which didn't fire cannot pass a fault test.
FAULT_C = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static int writer = -1, readers[8], nreaders;
static int is_action(const char *s) {
    const char *a = getenv("INLINE_TEST_ACTION");
    return a && strcmp(a, s) == 0;
}
static int is_reader(int fd) {
    for (int i = 0; i < nreaders; i++) if (readers[i] == fd) return 1;
    return 0;
}
int open(const char *path, int flags, ...) {
    int (*real_open)(const char *, int, ...) = dlsym(RTLD_NEXT, "open");
    const char *node = getenv("INLINE_TEST_NODE");
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, int); va_end(ap); }
    int match = node && strcmp(path, node) == 0;
    if (match && (flags & O_DIRECT) && is_action("open")) {
        fprintf(stderr, "INLINE_FAULT reader-open %s\n", path);
        errno = EACCES; return -1;
    }
    int fd = real_open(path, flags, mode);
    if (match && fd >= 0 && (flags & O_ACCMODE) == O_WRONLY) writer = fd;
    if (match && fd >= 0 && (flags & O_DIRECT) && nreaders < 8) readers[nreaders++] = fd;
    return fd;
}
ssize_t pwrite(int fd, const void *buf, size_t n, off_t off) {
    ssize_t (*real_pwrite)(int, const void *, size_t, off_t) = dlsym(RTLD_NEXT, "pwrite");
    ssize_t r = real_pwrite(fd, buf, n, off);
    const char *us = getenv("INLINE_TEST_SLOW_US");
    if (fd == writer && is_action("slow")) usleep(30000);
    if (fd == writer && is_action("bad") && us) usleep((useconds_t)atoi(us));
    return r;
}
ssize_t pread(int fd, void *buf, size_t n, off_t off) {
    ssize_t (*real_pread)(int, void *, size_t, off_t) = dlsym(RTLD_NEXT, "pread");
    if (is_action("trace")) fprintf(stderr, "INLINE_READ %lld %zu\n", (long long)off, n);
    ssize_t r = real_pread(fd, buf, n, off);
    /* a sector that never gives back what was written: the same wrong byte on every read */
    if (is_reader(fd) && is_action("bad") && r > 0 && off == 0) {
        ((unsigned char *)buf)[0] ^= 0x80;
        fprintf(stderr, "INLINE_FAULT bad-read %s\n", getenv("INLINE_TEST_NODE"));
    }
    return r;
}
int fdatasync(int fd) {
    int (*real_fdatasync)(int) = dlsym(RTLD_NEXT, "fdatasync");
    if (is_action("trace")) fprintf(stderr, "INLINE_FLUSH\n");
    return real_fdatasync(fd);
}
int fsync(int fd) {
    int (*real_fsync)(int) = dlsym(RTLD_NEXT, "fsync");
    int (*real_open)(const char *, int, ...) = dlsym(RTLD_NEXT, "open");
    ssize_t (*real_pwrite)(int, const void *, size_t, off_t) = dlsym(RTLD_NEXT, "pwrite");
    ssize_t (*real_pread)(int, void *, size_t, off_t) = dlsym(RTLD_NEXT, "pread");
    if (fd == writer && is_action("corrupt")) {
        /* once, after the writes and before the tail is released: the disk lost a bit */
        const char *node = getenv("INLINE_TEST_NODE");
        int rw = real_open(node, O_RDWR);
        unsigned char byte;
        if (rw < 0 || real_pread(rw, &byte, 1, 0) != 1) _exit(90);
        byte ^= 0x80;
        if (real_pwrite(rw, &byte, 1, 0) != 1 || real_fsync(rw)) _exit(91);
        close(rw);
        fprintf(stderr, "INLINE_FAULT corrupt-after-write %s\n", node);
    }
    if (fd == writer && is_action("flush")) {
        fprintf(stderr, "INLINE_FAULT flush\n"); errno = EIO; return -1;
    }
    return real_fsync(fd);
}
'''


@pytest.fixture(scope="module")
def binaries(tmp_path_factory):
    root = tmp_path_factory.mktemp("inline-bin")
    hook = root / "fault.c"
    hook.write_text(FAULT_C, encoding="utf-8", newline="\n")
    for cmd in (
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(root / "fanout"), str(E.REPO / "agent/fanout.c")],
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o", str(root / "fault.so"), str(hook), "-ldl"],
    ):
        p = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert p.returncode == 0, p.stdout + p.stderr
    return root


def _clone(tmp_path, monkeypatch, disks, binaries, *, action="", rotation="", lag=512 << 20, part=1, slow_us=0):
    fx, devs = disks
    # Mock only the rotational property: loop devices report rotational on
    # some kernels. Every read/write/map/flush uses the real loop partitions.
    # fx is root's (the fixture runs under sudo): the test's own files live in tmp_path.
    rot = tmp_path / "rotation"
    rot.mkdir()
    for name, dev in devs.items():
        (rot / Path(dev).name).write_text("1\n" if name == rotation else "0\n", newline="\n")
    # readbackinline.sh is loaded here, not in the engine's own list: without it (the
    # base, the negative control) the copy still runs, and what the drawers end as
    # is what every test below fails on -- never a driver that could not start.
    extra = r'''
[ ! -f "$LIB_DIR/readbackinline.sh" ] || . "$LIB_DIR/readbackinline.sh"
printf '{"readback_mode":"inline"}\n' > "$RUN_DIR/response.json"
rb_inline_rotational() { cat "$INLINE_TEST_ROT/$1"; }
'''
    anchor = 'mkdir -p "$LC"\nlc_inventory'
    assert anchor in E.DRIVER
    monkeypatch.setattr(E, "DRIVER", E.DRIVER.replace(anchor, extra + "\n" + anchor))
    wrapper = tmp_path / "bin"
    wrapper.mkdir()
    calls = fx / "fanout.calls"
    # The fault is only injected in one partition of the first target. The other
    # target is a positive control for per-drawer isolation.
    node = fx / "dev" / (Path(devs["same"]).name + f"p{part}")
    script = (
        '#!/bin/sh\n'
        f'printf "%s\\n" "$*" >> {shlex.quote(str(calls))}\n'
        'if [ "$1" = --inline-write ]; then\n'
        f'  export LD_PRELOAD={shlex.quote(str(binaries / "fault.so"))}\n'
        'fi\n'
        f'exec {shlex.quote(str(binaries / "fanout"))} "$@"\n'
    )
    (wrapper / "fanout").write_text(script, encoding="utf-8", newline="\n")
    (wrapper / "fanout").chmod(0o755)
    out = E._engine(tmp_path, fx, wrapper, "same big", 0,
                    env=(f"LC_INLINE_LAG_BYTES={lag}", f"INLINE_TEST_NODE={node}", f"INLINE_TEST_ACTION={action}",
                         f"INLINE_TEST_ROT={rot}", f"INLINE_TEST_SLOW_US={slow_us}"))
    out["log"] = (fx / "run/agent.log").read_text(encoding="utf-8")
    out["calls"] = [shlex.split(line) for line in calls.read_text().splitlines()]
    out["rb"] = {}
    for name in ("same", "big"):
        target = fx / "run/targets" / Path(devs[name]).name / "readback"
        out["rb"][name] = target.read_text().strip() if target.exists() else None
    return out


def _inline_files(fx):
    # LC is defined by lcinv.sh; discover its run directory from the positive
    # result record, not from a duplicated assumption about LC's default.
    found = list((fx / "run").glob("**/inline/mode"))
    assert len(found) == 1, found
    return found[0].parent


def test_clean_inline_clone_verified_without_a_second_source_read(tmp_path, monkeypatch, disks, binaries):
    fx, devs = disks
    source_files = E._files(devs["src"], fx)
    out = _clone(tmp_path, monkeypatch, disks, binaries, action="slow", lag=CH)
    assert out["rb"] == {"same": "verified", "big": "verified"}, out["raw"]
    assert all(t["state"] == "done" for t in out["target"].values()), out["raw"]
    assert E._files(devs["same"], fx) == E._files(devs["big"], fx) == source_files
    assert E._files(devs["src"], fx) == source_files
    # Prove concurrency, not just a successful after-copy check labelled inline.
    counts = re.findall(r": (\d+) chunks checked during copy, 0 rewritten, reader rc=0", out["log"])
    assert counts and max(map(int, counts)) > 0, out["log"]
    inline = _inline_files(fx)
    assert sorted(p.name for p in inline.glob("selected.*")) == ["selected.1", "selected.3", "selected.4", "selected.5"]
    # p2 is raw on this base: it really runs the existing full read-back.
    assert "partition 2: falling back to after-copy full readback: unsupported partition type" in out["log"]
    reads = [args for args in out["calls"] if args[0] == "--readback"]
    assert len(reads) == 3 and all(args[1] == "-x" and args[2].endswith("p2") for args in reads), reads
    # Independently reread the source ESP and compare the identical c/n format.
    expected = E._root("sh", "-c", '"$1" -c -s "$2" -L "$3" | "$4" --readback -x "$2"', "sh",
                       E._tool("partclone.fat"), devs["src"] + "p1", str(fx / "expected.log"), str(binaries / "fanout"))
    assert expected.returncode == 0, expected.stderr
    for name in ("same", "big"):
        assert (inline / f"{Path(devs[name]).name}.1.source").read_text() == expected.stdout


def test_a_one_time_loss_is_rewritten_from_the_source_and_ends_orange(tmp_path, monkeypatch, disks, binaries):
    """The disk loses a bit of partition 1 after it was written (once): the reader
    sees it, rewrites the chunk from the source, reads it back right -- the drawer
    is done, "repaired" (never "verified") with the owner's words, and its files
    really are the source's again."""
    fx, devs = disks
    source_files = E._files(devs["src"], fx)
    out = _clone(tmp_path, monkeypatch, disks, binaries, action="corrupt")
    assert "INLINE_FAULT corrupt-after-write" in out["log"], out["log"]
    assert re.search(r"chunk 0 rewritten from \S+p1 \(try 1 of 2\): matches now", out["log"]), out["log"]
    target = out["target"]["same"]
    assert target["state"] == "done", out["raw"]
    assert out["rb"]["same"] == "repaired", out["raw"]
    assert "1 חתיכות נכתבו מחדש ותוקנו — מומלץ לבדוק את הדיסק (מחיצה 1: 1)" in target["error"], target
    assert E._files(devs["same"], fx) == source_files
    assert out["rb"]["big"] == "verified" and out["target"]["big"]["state"] == "done", out["raw"]
    assert out["target"]["big"]["error"] == "", out["target"]["big"]


def test_a_chunk_still_wrong_after_two_rewrites_stops_that_drawer_mid_copy_red(tmp_path, monkeypatch, disks, binaries):
    """Partition 3 of one drawer gives back a wrong byte on every read: two rewrites
    from the source, then red by partition -- and its writer stopped before the end
    of the partition, while the other drawer finished and verified."""
    fx, devs = disks
    source_files = E._files(devs["src"], fx)
    out = _clone(tmp_path, monkeypatch, disks, binaries, action="bad", part=3, lag=CH, slow_us=100000)
    assert "INLINE_FAULT bad-read" in out["log"], out["log"]
    for t in (1, 2):
        assert re.search(rf"chunk 0 rewritten from \S+p3 \(try {t} of 2\): read back wrong again", out["log"]), out["log"]
    stopped = re.search(r"inline: \S+p3: stopped after (\d+) of (\d+) bytes: chunk 0 stayed wrong", out["log"])
    assert stopped, out["log"]
    at, total = int(stopped[1]), int(stopped[2])
    assert total >= 5 * CH and at < total, (at, total)   # a partition long enough to stop in, and stopped in it
    target = out["target"]["same"]
    assert target["state"] == "failed" and target["ok"] == ["1", "2"], out["raw"]
    assert target["error"].startswith("מחיצה 3: האימות אחרי הכתיבה נכשל"), target
    assert "לא תאמה למקור גם אחרי 2 כתיבות מחדש" in target["error"], target
    assert out["rb"]["same"] is None
    assert out["target"]["big"]["state"] == "done" and out["rb"]["big"] == "verified", out["raw"]
    assert E._files(devs["big"], fx) == source_files


def test_reader_open_failure_is_unchecked_never_verified(tmp_path, monkeypatch, disks, binaries):
    out = _clone(tmp_path, monkeypatch, disks, binaries, action="open")
    assert "INLINE_FAULT reader-open" in out["log"], out["log"]
    target = out["target"]["same"]
    assert target["state"] == "failed" and "מחיצה 1" in target["error"], out["raw"]
    assert "האימות אחרי הכתיבה לא רץ" in target["error"] and out["rb"]["same"] is None
    assert "reader unavailable" in target["error"], target
    assert out["rb"]["big"] == "verified"


@pytest.mark.parametrize("rotation", ["src", "same"])
def test_rotational_source_or_target_uses_after_copy_full(tmp_path, monkeypatch, disks, binaries, rotation):
    out = _clone(tmp_path, monkeypatch, disks, binaries, rotation=rotation)
    assert out["rb"] == {"same": "verified", "big": "verified"}, out["raw"]
    assert "falling back to after-copy full readback: rotational=1 on " in out["log"]
    assert not any(args[0] == "--inline-write" for args in out["calls"])
    reads = [args for args in out["calls"] if args[0] == "--readback"]
    assert len(reads) == 15 and all(args[1] == "-x" and "-s" not in args for args in reads), reads


@pytest.fixture
def loop(tmp_path):
    backing = tmp_path / "disk.img"
    with backing.open("wb") as f:
        f.truncate(32 << 20)
    up = E._root("losetup", "--find", "--show", str(backing))
    assert up.returncode == 0, up.stdout + up.stderr
    dev = up.stdout.strip()
    try:
        yield dev
    finally:
        down = E._root("losetup", "--detach", dev)
        assert down.returncode == 0, down.stdout + down.stderr


def _write(tmp_path, binaries, loop, data, action="", lag=CH):
    image = tmp_path / "image.pcl"
    result = tmp_path / "read.result"
    image.write_bytes(data)
    out = E._root(f"LD_PRELOAD={binaries / 'fault.so'}", f"INLINE_TEST_NODE={loop}", f"INLINE_TEST_ACTION={action}",
                  "sh", "-c", 'exec "$1" --inline-write "$2" "$3" "$4" "$5" 30 < "$6"', "sh",
                  str(binaries / "fanout"), loop, str(tmp_path / "no-source"), str(result), str(lag), str(image))
    return out, result.read_text() if result.exists() else ""


@pytest.mark.parametrize("bs", [512, 4096])
def test_sparse_stream_digests_and_written_blocks_match_independent_reference(tmp_path, binaries, loop, bs):
    used = list(range(0, 2100)) + [2200, 2400, 2600] + list(range(2800, 3005))
    data = P.image(3005, used, bs)
    out, result = _write(tmp_path, binaries, loop, data)
    assert out.returncode == 0 and result == "ok\n", out.stdout + out.stderr + result
    expected, nbytes = P.expected_list(P.stream(used, bs))
    lines = expected.splitlines()
    assert out.stdout == "".join(f"c {i} {h}\n" for i, h in enumerate(lines)) + f"n {len(lines)} {nbytes}\n"
    image = tmp_path / "image.pcl"
    rb = E._root("sh", "-c", 'exec "$1" --readback -x "$2" < "$3"', "sh",
                 str(binaries / "fanout"), loop, str(image))
    assert rb.returncode == 0 and rb.stdout == out.stdout, rb.stdout + rb.stderr


@pytest.mark.parametrize("damage", ["short", "extra", "header", "bitmap", "checksums"])
def test_invalid_stream_cannot_leave_success(tmp_path, binaries, loop, damage):
    data = P.image(64, [0, 1, 40], 4096, checksum_size=4 if damage == "checksums" else 0, per_checksum=1)
    if damage == "short":
        data = data[:-1]
    elif damage == "extra":
        data += b"x"
    elif damage in ("header", "bitmap"):
        at = 8 if damage == "header" else 110
        data = data[:at] + bytes([data[at] ^ 1]) + data[at + 1:]
    out, result = _write(tmp_path, binaries, loop, data)
    assert out.returncode != 0 and result.startswith("unchecked|"), out.stderr + result
    assert "\nn " not in out.stdout


def test_failed_tail_flush_is_not_verified(tmp_path, binaries, loop):
    out, result = _write(tmp_path, binaries, loop, P.image(64, [0, 1, 40], 4096), action="flush")
    assert "INLINE_FAULT flush" in out.stderr
    assert out.returncode != 0 and result.startswith("unchecked|flush"), out.stderr + result


def test_empty_bitmap_requires_clean_eof_and_flush(tmp_path, binaries, loop):
    out, result = _write(tmp_path, binaries, loop, P.image(64, [], 4096))
    assert out.returncode == 0 and out.stdout == "n 0 0\n" and result == "ok\n", out.stderr + result


def test_reader_obeys_written_watermark_and_lag_before_eof(tmp_path, binaries, loop):
    """Hold stdin open: with 8MiB written and lag=4MiB only chunk 0 is eligible.
    No timing inference from throughput: the writer cannot receive chunk 2
    until the test has observed chunk 0's real pread and checked the boundary.
    And no read before a flush covers it: an O_DIRECT read of dirty pages would
    write them back itself and get them from the drive's cache.
    """
    data = P.image(4096, list(range(4096)), 4096)
    start = 110 + 4096 // 8 + 4
    result, log = tmp_path / "result", tmp_path / "trace"
    with (tmp_path / "digests").open("wb") as output, log.open("wb") as err:
        proc = subprocess.Popen(
            ["sudo", "-n", "env", f"LD_PRELOAD={binaries / 'fault.so'}", "INLINE_TEST_ACTION=trace",
             str(binaries / "fanout"), "--inline-write", loop, str(tmp_path / "no-source"), str(result), str(CH), "10"],
            stdin=subprocess.PIPE, stdout=output, stderr=err,
        )
        try:
            proc.stdin.write(data[:start + 2 * CH]); proc.stdin.flush()
            deadline = time.monotonic() + 10
            while "INLINE_READ 0 " not in log.read_text() and time.monotonic() < deadline:
                assert proc.poll() is None, log.read_text()
                time.sleep(0.02)
            time.sleep(0.1)   # the next chunk remains ineligible, even with time to read it
            reads = re.findall(r"INLINE_READ (\d+) (\d+)", log.read_text())
            assert reads == [("0", str(CH))], log.read_text()
            trace = log.read_text()
            assert -1 < trace.find("INLINE_FLUSH") < trace.find("INLINE_READ 0 "), trace
            proc.stdin.write(data[start + 2 * CH:]); proc.stdin.close()
            assert proc.wait(timeout=30) == 0, log.read_text()
        finally:
            if not proc.stdin.closed:
                proc.stdin.close()
            if proc.poll() is None:
                # sudo -n may own the supervisor; use the same root helper to
                # terminate it. The C child's parent-death signal stops readers.
                E._root("kill", "-KILL", str(proc.pid))
                proc.wait(timeout=15)
    assert result.read_text() == "ok\n", log.read_text()
    assert re.findall(r"INLINE_READ (\d+) (\d+)", log.read_text()) == [
        (str(i * CH), str(CH)) for i in range(4)
    ]
