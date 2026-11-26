"""#2848 -- the same disk card said "SMART passed" and, under it, "unchecked: SMART not read".

Two callers probe the SAME drawer at boot: the main loop's smart_probe_idle (the hello needs the
health) and the waiting-screen write check's guard (idlecheck.sh, a background subshell per disk).
smart_probe keeps its working files per disk, not per call -- ``smart/<d>.out`` (smartctl's stdout),
``.find``/``.find.next``, ``.v``, ``.serial`` -- and had no lock. The second call opens ``.out`` with
``>`` (truncate) while the first smartctl is still printing: smartctl prints its sections as it reads
each log from the disk, so the first run's next write lands after the offset it already reached,
over a file the second run emptied -- a hole of NUL bytes where the attribute table was. That run
reads a file without a table, and says "unchecked" (the write check's "SMART לא נקרא"), while the
other run finishes, stores ``ok``, and the card shows passed. Not proved on metal; this test
reproduces the mechanism with a smartctl that prints in three parts.

The fix: one probe per disk at a time -- the second waits for the first and takes its cached answer.
"""

from __future__ import annotations

import subprocess

from test_smart import AGENT, BASH, PRELUDE, attrs, env, posix, sh  # noqa: F401 -- the same bash, harness and prelude

PART = 1.2   # seconds between the parts smartctl prints (a real `smartctl -x` takes seconds)
SECOND_CALL_AT = 1.8   # after part 2, before part 3 of the first run


def _smartctl(tmp_path, part=PART):
    """A smartctl that prints in three parts, a pause between them -- one write() each, as a
    libc flushing a big `-x` report does."""
    body = attrs().replace("'", "'\\''")
    p = tmp_path / "smartctl"
    p.write_text(
        "#!/bin/sh\n"
        "echo 'SMART overall-health self-assessment test result: PASSED'; echo\n"
        f"sleep {part}\n"
        f"printf '%s' '{body}'\n"
        f"sleep {part}\n"
        "echo 'SMART Error Log Version: 1'\n"
        "exit 0\n", newline="\n")
    subprocess.run([BASH, "-c", f"chmod +x {posix(p)!r}"], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return posix(p)


def _lab(tmp_path, part=PART):
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sdb").write_bytes(b"\x00")
    blk = tmp_path / "sysro/sys/block/sdb"; (blk / "device").mkdir(parents=True)
    (blk / "ro").write_text("0\n", newline="\n")
    (blk / "device/serial").write_text("SERIAL-A\n", newline="\n")
    return run, env(run, dev, sysroot=tmp_path / "sysro", smartctl=_smartctl(tmp_path, part)) + PRELUDE


def test_two_probes_of_one_disk_at_once_both_say_ok(tmp_path):
    run, prefix = _lab(tmp_path)
    r = sh(
        prefix +
        f'( smart_probe sdb > {posix(tmp_path)}/a.txt ) & _a=$!; '
        f'sleep {SECOND_CALL_AT}; smart_probe sdb > {posix(tmp_path)}/b.txt; wait $_a; '
        f'echo "A=$(cat {posix(tmp_path)}/a.txt) B=$(cat {posix(tmp_path)}/b.txt) V=$(smart_field sdb 1)"')
    assert r.stdout.strip() == "A=ok B=ok V=ok", (r.stdout, r.stderr)


def test_a_probe_after_another_has_finished_is_cached_and_runs_no_smartctl(tmp_path):
    # the guard against an over-broad fix: serial callers are untouched, and the second one is the cache
    run, prefix = _lab(tmp_path)
    r = sh(prefix + 'smart_probe sdb; SMARTCTL=/nonexistent smart_probe sdb; echo "V=$(smart_field sdb 1)"')
    assert r.stdout.split() == ["ok", "ok", "V=ok"], (r.stdout, r.stderr)


def test_a_lock_left_by_a_killed_probe_does_not_stop_the_next_one(tmp_path):
    # a holder killed with -9 leaves its lock; the next probe waits its ceiling at most, then reads the disk
    run, prefix = _lab(tmp_path, part=0)
    (run / "smart/sdb.lk").mkdir(parents=True)
    r = sh(prefix + 'SMARTCTL_MAX_S=1; smart_probe sdb')
    assert r.stdout.strip() == "ok", (r.stdout, r.stderr)
    assert not (run / "smart/sdb.lk").exists()


def test_a_probe_that_calls_itself_is_not_locked_out_by_its_own_lock(tmp_path):
    # disk_f1_red (f1mark.sh), called by smart_ro_check inside the probe, probes the same disk again.
    # Waiting on the lock its own shell holds would cost the whole ceiling, then take it over.
    run, prefix = _lab(tmp_path, part=0)
    (run / "disk_f1").write_text("ok\nSERIAL-A|2026-11-05|manual\n", newline="\n")
    r = sh(prefix + f'. {posix(AGENT)}/lib/smart/f1mark.sh; SMARTCTL_MAX_S=1; '
           'rm() { case "$*" in *.lk*) echo TAKEN-OVER >> "$RUN_DIR/taken";; esac; command rm "$@"; }; smart_probe sdb')
    assert r.stdout.strip() == "fail", (r.stdout, r.stderr)   # fail f1_observed: the mark, as before
    assert (run / "smart/sdb.under").read_text().strip() == "SERIAL-A|ok"
    assert not (run / "taken").exists(), "the inner probe waited out the lock and took it over"
