"""#3298, #3299, #3300 (N1-N3 of the security review of #3271): the agent side of the write-check copy.

* **N1** -- `idle_wc_adopt` looks for a dd of the check that is still writing the disk, as the local copy
  does (`idle_wc_ready dst`). A live dd -> the drawer fails by name, nothing is taken from the waiting screen.
* **N2** -- past the ceiling, `_ic_wait` looks at /proc twice: a dd killed between fork and exec still reads
  as `sh`, and one look finds nothing. The second look (after 0.5 s) finds it -> "a dd may still write" (2).
* **N3** -- two open copies for one serial (twin disks in two cloners): `idle_wc_recover` says "not known"
  (1), puts nothing back, drops nothing, and writes no `settled`.

Negative control: `idlecheck.sh` and `idlerecover.sh` back at the base of this PR -- every test here fails
except the controls.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH
from test_idlecheck_3268 import _copies, _cut, _make, _res, _run, _seed, pytestmark  # noqa: F401


def _orphan_dd(tmp_path: Path, node: Path) -> subprocess.Popen:
    """A process that reads as a `dd` of `of=<node>` in /proc (a dd script under an interpreter, as the late dd of H4)."""
    script = tmp_path / "orphan" / "dd"
    script.parent.mkdir(exist_ok=True)
    script.write_text("#!/bin/bash\nsleep 30\n", newline="\n")
    script.chmod(0o755)
    return subprocess.Popen([BASH, str(script), f"of={node}"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _in_proc(proc: subprocess.Popen) -> None:
    """The orphan is in /proc with its arguments before the test relies on it."""
    deadline = time.monotonic() + 5
    cmdline = Path(f"/proc/{proc.pid}/cmdline")
    while time.monotonic() < deadline:
        try:
            if b"of=" in cmdline.read_bytes():
                return
        except OSError:
            pass
        time.sleep(0.05)
    pytest.fail("the orphan dd never showed in /proc")


# --- N1 (#3298) ---------------------------------------------------------------------------

@requires_native("setsid", posix=True)
def test_n1_a_round_does_not_take_the_result_of_a_disk_a_dd_of_the_check_still_writes(tmp_path):
    _make(tmp_path)
    _seed(tmp_path, "sdx", "ok", mbps="120", reason="done")
    proc = _orphan_dd(tmp_path, tmp_path / "dev" / "sdx")
    try:
        _in_proc(proc)
        out = _run(tmp_path, "idle_wc_adopt sdx; echo RC=$?")
    finally:
        proc.kill()
        proc.wait()
    assert "RC=2" in out.stdout, (out.stdout, out.stderr)
    targets = tmp_path / "run" / "targets" / "sdx"
    assert not (targets / "write_check_state").exists(), "the result was taken from the waiting screen"


def test_n1_control_no_dd_left_and_the_result_is_taken_as_before(tmp_path):
    _make(tmp_path)
    _seed(tmp_path, "sdx", "ok", mbps="120", reason="done")
    out = _run(tmp_path, "idle_wc_adopt sdx; echo RC=$?")
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    assert (tmp_path / "run" / "targets" / "sdx" / "write_check_state").read_text() == "ok"


# --- N2 (#3299) ---------------------------------------------------------------------------

def test_n2_a_dd_that_shows_only_in_the_second_look_is_still_a_writer(tmp_path):
    """The first look at /proc finds none (the process is still `sh`, between fork and exec); the second, 0.5 s
    later, finds a dd of the node: `_ic_wait` says "a dd may still write it" (2), not "none" (1)."""
    _make(tmp_path)
    counter = tmp_path / "looks"
    script = (
        "WAIT_WRITECHECK_S=1; IDLE_WC_KILL_S=1; "
        f'_ic_writers() {{ n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n + 1)) > "{counter}"; '
        '[ "$n" -ge 1 ] && echo 99999999; return 0; }; '
        '( exec sleep 30 ) & _ic_wait "$!" "$DEVROOT/sdx"; echo RC=$?'
    )
    out = _run(tmp_path, script)
    assert "RC=2" in out.stdout, (out.stdout, out.stderr)
    assert int(counter.read_text()) >= 2, "only one look at /proc"


def test_n2_control_no_dd_in_either_look_is_the_ceiling_with_nothing_writing(tmp_path):
    _make(tmp_path)
    script = ('WAIT_WRITECHECK_S=1; IDLE_WC_KILL_S=1; _ic_writers() { return 0; }; '
              '( exec sleep 30 ) & _ic_wait "$!" "$DEVROOT/sdx"; echo RC=$?')
    out = _run(tmp_path, script)
    assert "RC=1" in out.stdout, (out.stdout, out.stderr)


# --- N3 (#3300) ---------------------------------------------------------------------------

#: the listing answers a second open copy of the same serial (the other one's id); everything else is the fake server's
DOUBLE = (
    'curl() { _o=$(command curl "$@"); case "$*" in *-X*|*/blob*|*" -o "*) printf "%s" "$_o" ;; '
    '*) printf "%s" "$_o" | jq -c \'.backups += [.backups[0] | .id = "ffffffffffffffffffffffffffffffff"]\' ;; esac; }; '
)


def test_n3_two_open_copies_of_one_serial_are_not_known_which_is_this_disks(tmp_path):
    img, _original = _cut(tmp_path)
    cut = img.read_bytes()
    out = _run(tmp_path, DOUBLE + "idle_wc_recover sdx; echo RC=$?")
    assert "RC=1" in out.stdout, (out.stdout, out.stderr)
    assert img.read_bytes() == cut, "a copy was put back although it is not known whose it is"
    assert _copies(tmp_path), "a copy was dropped"
    assert not (tmp_path / "run" / "idlecheck" / "settled.sdx").exists(), "settled was written for a disk not known clean"
    assert "open copies of serial" in (tmp_path / "run" / "agent.log").read_text()


def test_n3_the_disk_is_then_not_checked_and_the_check_writes_nothing(tmp_path):
    img, _original = _cut(tmp_path)
    cut, writes_before = img.read_bytes(), len((tmp_path / "writes").read_text().split())
    res = tmp_path / "run" / "idlecheck" / "res.sdx"
    for f in res.iterdir():                     # a fresh insertion: no result yet
        f.unlink()
    res.rmdir()
    _run(tmp_path, DOUBLE + "idle_wc_disk sdx")
    assert img.read_bytes() == cut
    assert len((tmp_path / "writes").read_text().split()) == writes_before, "the check wrote with two open copies"
    assert _res(tmp_path)["state"] == "unchecked"


def test_n3_control_one_open_copy_is_put_back_as_before(tmp_path):
    img, original = _cut(tmp_path)
    out = _run(tmp_path, "idle_wc_recover sdx; echo RC=$?")
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    assert img.read_bytes() == original
