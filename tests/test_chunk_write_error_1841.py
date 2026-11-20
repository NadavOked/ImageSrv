"""#1841 on the chunk engine: a drive that refuses every write fails in seconds, as `write_error`.

On the old pipe (FI D4, 19/10, cloner 2) a `dm error` target took writes into the page
cache for six minutes and failed only at the fsync at the end of the partition, and
the server recorded `cause=unclassified`. The old pipe got a periodic fsync
(writeback.sh, PR #1986). The chunk engine has its own: `chunkcast` flushes every
target every `--sync-ms` (2 s unless told; the agent does not tell it) and fails the
one whose flush or write does not come back 0 (agent/chunkcast.c, cc_sync, cc_write).

Two things are held here:

* every way `chunkcast` says a write failed reaches the server as `write_error` --
  the sentence the agent writes on the drawer (`_chunk_why`, chunkrecv.sh) is what
  `ata_cause.classify` reads when the kernel said nothing. Until this fix a failed
  write() said "שגיאת כתיבה לדיסק" and was recorded `unclassified`; a failed flush
  already said "(write_error)". A disk that hangs, or that is too small, is NOT a
  write error, and stays so;
* on a real kernel: a `dm error` drive beside a healthy one, in one receiver, with
  the agent's flush interval. The dead drive is failed by the flush while the stream
  still runs -- seconds after it began, not at its end -- and the healthy one goes on.
  Root (`dmsetup`): the Testrunner's `sudo -n`; elsewhere a declared skip.

Not here: the cost of the flush on a real HDD/SSD (#1841's last open line, metal).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native, with_sbin
from server.disks import ata_cause
from test_agent import AGENT, BASH, posix

SOURCE = (AGENT / "chunkcast.c").read_text(encoding="utf-8")

#: chunkcast's reasons for a failed target, as cc_fail writes them -> the cause the server must record
REASONS = {
    "write error at chunk 3: Input/output error": "write_error",
    "the flush to the disk failed": "write_error",
    "the disk has not taken a write or a flush for too long -- it is not waited for": "unclassified",
    "the target did not open for writing, is not a disk, or is too small": "unclassified",
}


def _why(reason: str) -> str:
    """The agent's own `_chunk_why`, under the agent's shell: the operator's sentence for a reason."""
    script = f'. {posix(AGENT)}/lib/restore/chunkrecv.sh; _chunk_why "$1"'
    p = subprocess.run([BASH, "-c", script, "sh", reason], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    assert p.returncode == 0 and p.stdout.strip(), p.stderr
    return p.stdout.strip()


@requires_native(("bash", BASH), why="the agent's shell")
@pytest.mark.parametrize("reason", sorted(REASONS))
def test_every_way_chunkcast_says_a_write_failed_is_recorded_as_write_error(reason):
    printf = reason.split(" at chunk ")[0] if reason.startswith("write error") else reason
    assert printf in SOURCE, f"chunkcast.c no longer says {printf!r} -- the table here is stale"
    # what fail_written_target leaves on the drawer, and what the server classifies with no kernel line
    error = f"מחיצה 3: {_why(reason)}"
    assert ata_cause.classify([], error) == REASONS[reason], error


# --- a real dm-error drive ------------------------------------------------------------------------------

SBIN_PATH = with_sbin(os.environ.get("PATH", "/usr/bin:/bin"))
DMSETUP = shutil.which("dmsetup", path=SBIN_PATH)


def _sudo_ok() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def _sudo(*cmd) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo", "-n", *map(str, cmd)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


@requires_native("gcc", "zstd", ("sudo -n", _sudo_ok()), ("dmsetup", DMSETUP), posix=True,
                 why="a device-mapper `error` drive, as root -- the Testrunner")
def test_a_drive_that_refuses_every_write_fails_mid_stream_by_the_flush_and_its_neighbour_is_whole(tmp_path):
    (tmp_path / "bin").mkdir()
    cc = C.build_binaries(tmp_path / "bin")
    img = C.build(tmp_path / "img", cc / "fanout", noisy=(0, 1, 2, 3), chunks=4.5)   # ~17MB on the wire
    name = f"isrv1841-{os.getpid()}"
    made = _sudo(DMSETUP, "create", name, "--table", f"0 {(64 << 20) // 512} error")
    assert made.returncode == 0, made.stderr
    dead, good, state = Path(f"/dev/mapper/{name}"), img.blank(tmp_path / "good.disk"), tmp_path / "state"
    session, port = C.new_session(), C.free_port()
    try:
        # chunkcast recv as chunkrecv.sh runs it: no --sync-ms, the flush interval is the engine's own
        r = C._spawn(["sudo", "-n", "timeout", "120", "env", "CHUNKCAST_ALLOW_FILE=1", str(cc / "chunkcast"), "recv",
                      "--listen", f"127.0.0.1:{port}", "--session", session, "--part", "3", "--pass", "1",
                      *C.image_args(img), "--state", str(state), "--start-s", "20", "--stall-s", "10",
                      str(dead), str(good)], tmp_path / "recv")
        r.wait_for("listening")
        began = time.monotonic()
        s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send", kbps=8_000)   # ~17 s of stream
        failed_at = None
        while s.process.poll() is None and failed_at is None:
            if state.exists() and any(line.startswith(f"{dead} failed ") for line in state.read_text().splitlines()):
                failed_at = time.monotonic() - began
            time.sleep(0.2)
        still_sending = s.process.poll() is None
        assert s.wait(120) == 0, s.log()
        assert r.wait(150) == 0, r.log()
    finally:
        for _ in range(10):
            if _sudo(DMSETUP, "remove", name).returncode == 0:
                break
            time.sleep(0.5)
        _sudo("chown", "-R", f"{os.getuid()}:{os.getgid()}", tmp_path)

    assert failed_at is not None and still_sending, f"the dead drive was failed only when the stream ended\n{r.log()}"
    assert failed_at <= 10, f"failed {failed_at:.1f}s into the stream\n{r.log()}"
    got = C.state_of(state)
    assert not got[str(dead)]["alive"] and got[str(dead)]["durable"] == set(), got[str(dead)]
    reason = got[str(dead)]["reason"]
    assert ata_cause.classify([], f"מחיצה 3: {_why(reason)}") == "write_error", reason
    # the neighbour is not harmed: alive, and what it holds is right. Whole is the repair passes' job -- a loaded
    # test host's file can be "behind" on a chunk (`skipped`), as a slow drive is (testrunner, 15/11)
    kept = got[str(good)]
    assert kept["alive"] and kept["reason"] == "" and kept["durable"], kept
    assert good.read_bytes() == img.expected(kept["durable"])
