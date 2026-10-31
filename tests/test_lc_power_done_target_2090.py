"""#2090: confirmed console power-off must preserve terminal clone targets.

Use the local-clone harness and real lc_power_commit/fail_written_target,
including the serial-guarded wipe and ATA/progress code. Only device access,
the parsed console action, reporting transport and power-off are faked.
"""

import json
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_local_clone_agent import run

pytestmark = requires_native(("bash", BASH))

POWER_ERROR = "המחשב כובה מהקונסולה באמצע שיכפול מקומי — הדיסק שבור ואינו שמיש"

SETUP = r'''
export SYSROOT="$RUN_DIR/sys" DEVROOT="$RUN_DIR/dev"
LC="$RUN_DIR/localclone"
mkdir -p "$SYSROOT/proc" "$DEVROOT" "$LC/run"
echo '300.00 0.00' > "$SYSROOT/proc/uptime"
echo verifying > "$LC/phase"
echo sdc > "$LC/run/src"
disk_serial() { printf 'serial-%s' "$1"; }
disk_port() { case "$1" in sda) echo 1 ;; sdb) echo 2 ;; esac; }
ata_port_of() { disk_port "$1"; }
sgdisk() { printf '%s\n' "$*" >> "$RUN_DIR/sgdisk.calls"; }
blockdev() { printf '%s\n' "$*" >> "$RUN_DIR/blockdev.calls"; }
dmesg() {
    echo called >> "$RUN_DIR/dmesg.calls"
    echo '[ 301.00] ata1.00: exception Emask 0x50'
    echo '[ 301.00] ata2.00: exception Emask 0x50'
}
stage_at() { printf '%s\n' "$*" >> "$RUN_DIR/stage.calls"; }
target_extra_json() { :; }
machine_extra_json() { :; }
lc_report() {
    build_progress local-clone "$MAC" > "$RUN_DIR/power-progress.json"
}
json_get() {
    [ "$1" = "$RUN_DIR/power.taken" ] && [ "$2" = .power_action ] || return 1
    echo poweroff
}
safe_poweroff() {
    # The real power path invokes this callback only after arming WoL.
    "$2" || return 1
    echo powered-off > "$RUN_DIR/power.called"
}
'''


def _power(tmp_path: Path, targets, *, before_power=""):
    script = SETUP
    for dev, state in targets:
        script += f'''
target_init {dev} 100
target_write_started {dev}
target_set {dev} {state} 'existing reason for {dev}'
'''
    names = " ".join(dev for dev, _ in targets)
    script += f'printf "%s\\n" "{names}" > "$LC/run/targets"\n'
    script += before_power
    script += '''
printf '%s\n' '{"power_action":"poweroff","power_confirmed":true}' > "$RUN_DIR/power.resp"
lc_copy_busy || exit 91
lc_power_during_copy
'''
    out = run(tmp_path, script)
    assert out.returncode == 0, out.stdout + out.stderr
    root = tmp_path / "run"
    assert (root / "power.called").read_text().strip() == "powered-off"
    assert (root / "localclone/run/stopped").read_text().strip() == "1"
    assert int((root / "localclone/run/ended").read_text()) > 0
    return root


def _calls(root, name):
    path = root / f"{name}.calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _no_ata(root, dev):
    target = root / "targets" / dev
    for name in ("ata_log", "ata_log.json", "ident"):
        assert not (target / name).exists(), (dev, name)
    progress = json.loads((root / "power-progress.json").read_text(encoding="utf-8"))
    entry = next(t for t in progress["targets"] if t["dev"] == dev)
    assert "ata_log" not in entry, entry
    assert "ata_port" not in entry, entry


@pytest.mark.parametrize("state", ["done", "failed", "cancelled"])
def test_terminal_target_is_untouched(tmp_path, state):
    root = _power(tmp_path, [("sda", state)])
    target = root / "targets/sda"
    assert _calls(root, "sgdisk") == []
    assert _calls(root, "blockdev") == []
    assert (target / "state").read_text().strip() == state
    assert (target / "error").read_text().strip() == "existing reason for sda"
    assert _calls(root, "stage") == []
    assert _calls(root, "dmesg") == []
    _no_ata(root, "sda")


@pytest.mark.parametrize("targets", [
    [("sda", "done"), ("sdb", "writing")],
    [("sdb", "writing"), ("sda", "done")],
])
def test_only_the_unfinished_target_is_failed_and_wiped(tmp_path, targets):
    root = _power(tmp_path, targets)
    assert _calls(root, "sgdisk") == [f"--zap-all {posix(root / 'dev/sdb')}"]
    assert _calls(root, "blockdev") == [f"--rereadpt {posix(root / 'dev/sdb')}"]
    assert (root / "targets/sda/state").read_text().strip() == "done"
    assert (root / "targets/sda/error").read_text().strip() == "existing reason for sda"
    assert (root / "targets/sdb/state").read_text().strip() == "failed"
    assert (root / "targets/sdb/error").read_text(encoding="utf-8").strip() == POWER_ERROR
    assert _calls(root, "stage") == ["sdb end"]
    _no_ata(root, "sda")
    _no_ata(root, "sdb")


def test_poweroff_failure_has_no_drive_failure_evidence(tmp_path):
    root = _power(tmp_path, [("sda", "writing")])
    assert (root / "targets/sda/state").read_text().strip() == "failed"
    assert (root / "targets/sda/error").read_text(encoding="utf-8").strip() == POWER_ERROR
    assert _calls(root, "sgdisk") == [f"--zap-all {posix(root / 'dev/sda')}"]
    assert _calls(root, "dmesg") == []
    _no_ata(root, "sda")


@pytest.mark.parametrize("unreadable", ["missing", "empty", "read-error", "partial-read"])
def test_unreadable_state_is_failed_and_wiped_aloud(tmp_path, unreadable):
    if unreadable == "missing":
        before = 'rm "$RUN_DIR/targets/sda/state"\n'
    elif unreadable == "empty":
        before = ': > "$RUN_DIR/targets/sda/state"\n'
    else:
        # chmod 000 is still readable by the root VM runner. Inject a read
        # failure while leaving the file writable for the real target_set.
        partial = "printf done; " if unreadable == "partial-read" else ""
        before = f'''
cat() {{
    if [ "$1" = "$RUN_DIR/targets/sda/state" ]; then {partial}return 1; fi
    command cat "$@"
}}
'''
    root = _power(tmp_path, [("sda", "done")], before_power=before)
    assert (root / "targets/sda/state").read_text().strip() == "failed"
    assert _calls(root, "sgdisk") == [f"--zap-all {posix(root / 'dev/sda')}"]
    assert "sda: target state could not be read -- treating it as unfinished" in (
        root / "agent.log"
    ).read_text(encoding="utf-8")
    _no_ata(root, "sda")
