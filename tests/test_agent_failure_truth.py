"""Regression tests for #1124: a failed check must not become clean `done`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_captured_stdout import sh, stub

pytestmark = requires_native(("bash", BASH))


def grow_box(tmp_path: Path, stubs: dict[str, str]) -> tuple[str, Path]:
    run = tmp_path / "run"
    target = run / "targets/sda"
    target.mkdir(parents=True)
    (target / "expanded").write_text("1|ntfs\n", newline="\n")
    bindir = tmp_path / "bin"; bindir.mkdir()
    # #1798: finish_grow reads the table for the incomplete-write mark -- an empty table here
    stubs = {"sgdisk": "exit 0", "ntfs-3g.probe": "exit 0", **stubs}   # #2009: the grow reads hibernation first -- clean
    setup = "".join(stub(bindir / name, body) for name, body in stubs.items())
    setup += (
        f'export PATH="$(cd {posix(bindir)!r} && pwd):/usr/bin" '
        f'RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "agent.log")!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/progress.sh; '
        f'. {posix(AGENT)}/lib/restore/grow.sh; . {posix(AGENT)}/lib/restore/ntfsunhib.sh; . {posix(AGENT)}/lib/restore/expandshare.sh; . {posix(AGENT)}/lib/restore/writemark.sh; '
        'partition_node() { printf "/dev/%s%s\\n" "$1" "$2"; }; '
    )
    return setup, run


def test_final_ntfsfix_failure_is_done_with_the_dirty_flag_warning(tmp_path):
    """Old behavior returned 0 after the final ntfsfix failed: clean done."""
    setup, run = grow_box(tmp_path, {
        "ntfsresize": "exit 0",
        "ntfsfix": 'if [ "$1" = -d ]; then exit 9; fi\nexit 0',
    })
    out = sh(setup + 'finish_grow sda; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("rc=0")
    assert (run / "targets/sda/state").read_text().strip() == "done"
    warning = (run / "targets/sda/error").read_text(encoding="utf-8")
    assert "מערכת הקבצים הורחבה" in warning and "דגל ה-dirty" in warning
    assert "rc=9" in warning


@pytest.mark.parametrize(("fs", "grower"), [("btrfs", "btrfs"), ("xfs", "xfs_growfs")])
def test_mounted_grow_is_not_success_when_unmount_fails(tmp_path, fs, grower):
    """Old behavior checked only the resize rc and hid umount stderr."""
    setup, run = grow_box(tmp_path, {
        "mount": "exit 0",
        "umount": 'echo "umount refused" >&2\nexit 32',
        grower: "exit 0",
    })
    out = sh(setup + f'grow_filesystem {fs} /dev/sda1; r=$?; echo "rc=$r error=$GROW_ERROR"')
    assert out.returncode == 0, out.stderr
    assert "rc=1" in out.stdout and "נשאר מעוגן" in out.stdout
    assert "umount refused" in (run / "agent.log").read_text()


def test_hostname_manifest_failure_is_named_and_never_selects_linux(tmp_path):
    """Old pipeline folded manifest_plan rc=7 into the non-Windows path."""
    run = tmp_path / "run"; run.mkdir()
    script = (
        f'RUN_DIR={posix(run)!r}; LOG_FILE={posix(run / "agent.log")!r}; '
        f'. {posix(AGENT)}/lib/postdeploy/hostname.sh; . {posix(AGENT)}/lib/postdeploy/unattend.sh; '
        'manifest_plan() { printf "1|guid|windows|ntfs\\n"; return 7; }; '
        '_write_hostname_linux() { echo LINUX_CALLED; return 0; }; '
        'write_hostname sda /manifest LAB-01; r=$?; echo "rc=$r"'
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    result = json.loads(lines[0])
    assert result["code"] == "manifest_plan_failed"
    assert lines[-1] == "rc=1" and "LINUX_CALLED" not in out.stdout


def test_restore_plan_count_failure_is_named_before_any_partition_stream(tmp_path):
    """Old code used an unchecked awk result in a numeric comparison."""
    run = tmp_path / "run"; (run / "targets/sda").mkdir(parents=True)
    script = (
        f'RUN_DIR={posix(run)!r}; LOG_FILE={posix(run / "agent.log")!r}; '
        f'. {posix(AGENT)}/lib/restore/restore.sh; '
        'target_set() { :; }; disk_fits() { return 0; }; boot_ca_preflight() { return 0; }; apply_gpt() { return 0; }; '
        'write_check() { return 0; }; write_speed_verdict() { return 0; }; readback_drawers() { :; }; '
        'expand_last() { return 0; }; manifest_plan() { echo "1|g|linux|ext4|1|512|p|s"; }; '
        'awk() { return 7; }; restore_partition() { echo STREAMED; return 0; }; '
        'fail_written_target() { echo "$2" > "$RUN_DIR/failure"; }; '
        'run_restore unicast sda server image manifest; echo "rc=$?"'
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "rc=1" in out.stdout and "STREAMED" not in out.stdout
    assert (run / "failure").read_text(encoding="utf-8").strip() == "לא הצלחנו לספור את המחיצות בתוכנית השחזור"

