"""#1969 -- TRIM (blkdiscard) of an SSD target before the restore writes its table.

Nadav 22/10: only a disk that says it takes discard; a TRIM that fails is logged
and the restore goes on. Verified 22/10 (PR to lab/int-1022-prs): never a
rotating disk (DM-SMR advertises discard), never a drawer already failed by a
gate, always BEFORE that drawer's table, one job per drawer (the first version
ran them one after another with no ceiling), and a TRIM that does not END fails
that drawer by name -- discards may still be in flight under the next write.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_drawer_prep_waits_1704_1705 import _error, _room, _state
from test_timeouts import run_sh

pytestmark = requires_native(("bash", BASH))


def _disks(root: Path, **disks: tuple[str, str]) -> None:
    """name=(rotational, discard_max_bytes); "" = the sysfs file is missing."""
    for name, (rot, dmax) in disks.items():
        q = root / "sys/block" / name / "queue"
        q.mkdir(parents=True, exist_ok=True)
        for f, v in (("rotational", rot), ("discard_max_bytes", dmax)):
            if v != "":
                (q / f).write_text(v + "\n", encoding="utf-8", newline="\n")


def _blkdiscard(tmp_path: Path, body: str = "exit 0") -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    calls = tmp_path / "trim.calls"
    tool = bindir / "blkdiscard"
    tool.write_text(f'#!/bin/sh\necho "trim ${{1##*/}}" >> {posix(calls)!r}\n{body}\n',
                    encoding="utf-8", newline="\n")
    run_sh(f"chmod +x {posix(tool)!r}")
    return bindir


def _one(tmp_path: Path, disk: str, body: str = "exit 0", **env: str) -> tuple[str, list[str], str]:
    root = tmp_path / "root"
    bindir = _blkdiscard(tmp_path, body)
    log = tmp_path / "agent.log"
    calls = tmp_path / "trim.calls"
    calls.unlink(missing_ok=True)
    exports = " ".join(f"{k}={v}" for k, v in env.items())
    out = run_sh(
        f"export PATH={posix(bindir)!r}:$PATH SYSROOT={posix(root)!r} DEVROOT={posix(tmp_path / 'dev')!r} "
        f"RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(log)!r} {exports}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; . {posix(AGENT)}/lib/restore/discard.sh; "
        f'discard_before_restore {disk}; echo "rc=$?"')
    return (out.strip().splitlines()[-1],
            calls.read_text(encoding="utf-8").splitlines() if calls.exists() else [],
            log.read_text(encoding="utf-8") if log.exists() else "")


def test_ssd_with_discard_is_trimmed_and_the_seconds_are_logged(tmp_path):
    _disks(tmp_path / "root", sda=("0", "2147450880"))
    rc, calls, log = _one(tmp_path, "sda")
    assert rc == "rc=0" and calls == ["trim sda"], (rc, calls)
    assert "sda: TRIM done in " in log, log


def test_a_trim_that_fails_is_logged_and_the_restore_goes_on(tmp_path):
    _disks(tmp_path / "root", sda=("0", "2147450880"))
    rc, calls, log = _one(tmp_path, "sda", "exit 9")
    assert rc == "rc=0" and calls == ["trim sda"], (rc, calls)
    assert "TRIM failed (rc=9) -- the restore goes on" in log, log


def test_a_rotating_disk_is_never_trimmed_even_when_it_advertises_discard(tmp_path):
    """DM-SMR hard disks advertise discard. Nadav: TRIM is for an SSD."""
    _disks(tmp_path / "root", sda=("1", "2147450880"))
    rc, calls, log = _one(tmp_path, "sda")
    assert rc == "rc=0" and calls == [], calls
    assert "not trimmed -- rotational=1" in log, log


def test_unreadable_rotational_or_no_discard_is_not_trimmed(tmp_path):
    _disks(tmp_path / "root", sda=("", "2147450880"), sdb=("0", "0"), sdc=("0", ""))
    for disk in ("sda", "sdb", "sdc"):
        rc, calls, _ = _one(tmp_path, disk)
        assert rc == "rc=0" and calls == [], (disk, calls)


def test_a_trim_that_does_not_end_is_rc_2_by_name(tmp_path):
    _disks(tmp_path / "root", sda=("0", "2147450880"))
    rc, _, log = _one(tmp_path, "sda", "sleep 30", DISCARD_MAX_S="1")
    assert rc == "rc=2", rc
    assert "TRIM did not end within 1s -- this disk fails" in log, log


# --- inside the round: run_restore_drawers ---------------------------------------


def _round(tmp_path: Path, blk_body: str, **env: str):
    root = tmp_path / "root"
    _disks(root, sda=("0", "2147450880"), sdb=("1", "2147450880"), sdc=("0", "2147450880"), sdd=("0", "4096"))
    bindir = _blkdiscard(tmp_path, blk_body)
    calls = posix(tmp_path / "trim.calls")
    run, prelude = _room(tmp_path, f'echo "gpt $1" >> {calls!r}; return 0;',
                         drawers=("sda", "sdb", "sdc", "sdd"), **env)
    prelude = (prelude + f"export PATH={posix(bindir)!r}:$PATH SYSROOT={posix(root)!r}; "
               f". {posix(AGENT)}/lib/restore/discard.sh; "
               # a gate failed sdc before the tables (the write check, as #1366 does)
               'write_check() { [ "$1" != sdc ] || { target_set sdc failed "gate"; return 1; }; }; ')
    out = run_sh(prelude + 'run_restore_drawers multicast http://s img m.json sda sdb sdc sdd; echo "rc=$?"')
    lines = (tmp_path / "trim.calls").read_text(encoding="utf-8").splitlines()
    return run, out, lines


def test_round_trims_only_live_ssds_and_each_before_its_own_table(tmp_path):
    run, out, lines = _round(tmp_path, "exit 0")
    assert out.strip().endswith("rc=0"), out
    trims = sorted(x for x in lines if x.startswith("trim"))
    assert trims == ["trim sda", "trim sdd"], lines          # sdb rotates, sdc failed a gate
    for d in ("sda", "sdd"):
        assert lines.index(f"trim {d}") < lines.index(f"gpt {d}"), lines
    assert "gpt sdc" not in lines
    assert [_state(run, d) for d in ("sda", "sdb", "sdd")] == ["done"] * 3


def test_round_a_hung_trim_fails_only_its_drawer_by_name(tmp_path):
    run, out, lines = _round(tmp_path, '[ "${1##*/}" = sdd ] && sleep 30; exit 0', DISCARD_MAX_S="1")
    assert out.strip().endswith("rc=0"), out
    assert _state(run, "sdd") == "failed" and "TRIM" in _error(run, "sdd"), _error(run, "sdd")
    assert "gpt sdd" not in lines, "a table was written over a TRIM that never ended"
    assert _state(run, "sda") == "done" and _state(run, "sdb") == "done"


def test_round_a_failed_trim_fails_nothing(tmp_path):
    run, out, _ = _round(tmp_path, "exit 1")
    assert out.strip().endswith("rc=0"), out
    assert [_state(run, d) for d in ("sda", "sdb", "sdd")] == ["done"] * 3
