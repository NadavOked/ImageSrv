"""#1104: non-interactive runners, fake binaries only; no real disk access."""

import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native

ROOT = Path(__file__).resolve().parents[1]
LIB = flat_lib()
BASH = shutil.which("bash")
pytestmark = requires_native(("bash", BASH))

DESTROY = [("nvme-sanitize", "/dev/nvme0n1"),
           ("zap-partition-table", "/dev/sda"), ("nwipe", "/dev/sda")]
READ = [("nvme-sanitize-status", "/dev/nvme0n1"),
        ("windows-ntfs-cat", "/dev/sda1:/Windows/win.ini"),
        ("ntfs-recover-scan", "/dev/sda1"),
        ("unsquashfs", "@archive"), ("bsdtar", "@archive")]


def fake(box, name, body):
    target = box / name
    target.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    subprocess.run([BASH, "-c", f"chmod +x {shlex.quote(target.as_posix())}"], check=True,
                   stdin=subprocess.DEVNULL)   # #14: WinError 50 in a multi-file run


def room(tmp_path):
    for name in ("bin", "run"):
        (tmp_path / name).mkdir()
    for name in ("sda", "nvme0n1"):
        disk = tmp_path / "sysroot/sys/block" / name
        (disk / "device").mkdir(parents=True)
        (disk / "size").write_text("1024\n")
        for field in ("model", "serial"):
            (disk / "device" / field).write_text("test\n")
    (tmp_path / "sysroot/sys/class/block/sda1").mkdir(parents=True)
    (tmp_path / "sysroot/sys/class/nvme/nvme0/nvme0n1").mkdir(parents=True)
    (tmp_path / "sysroot/proc").mkdir(parents=True)
    (tmp_path / "sysroot/proc/meminfo").write_text("MemAvailable:    1048576 kB\n")
    ctrl = bytearray(4096)
    ctrl[516] = 1
    (tmp_path / "controller").write_bytes(ctrl)
    status = bytearray(512)
    status[:4] = bytes([255, 255, 1, 1])
    (tmp_path / "status").write_bytes(status)
    (tmp_path / "archive").write_bytes(b"fixture")
    (tmp_path / "called").write_text("")
    (tmp_path / "selection.json").write_text(json.dumps({
        "schema": 1, "build": [t for t, _ in DESTROY + READ], "student": []}))
    fake(tmp_path / "bin", "nvme", r'''
printf 'nvme %s\n' "$*" >> "$CASE/called"
[ "${FAIL_COMMAND:-}" != "$1" ] || { echo 'injected nvme failure' >&2; exit 7; }
case "$1" in
    id-ctrl) cat "$CASE/controller" ;;
    sanitize-log) cat "$CASE/status" ;;
    sanitize) echo accepted ;;
    *) exit 9 ;;
esac
''')
    fake(tmp_path / "bin", "nwipe", r'''
printf 'nwipe %s\n' "$*" >> "$CASE/called"
[ "${FAIL_COMMAND:-}" != nwipe ] || { echo 'injected nwipe failure' >&2; exit 7; }
for opt in "$@"; do
    case "$opt" in --logfile=*) logfile=${opt#--logfile=} ;; esac
done
[ "${NO_NWIPE_REPORT:-0}" = 0 ] || exit 0
printf 'Nwipe successfully completed. See summary table for details.\n' > "$logfile"
''')
    for name in ("sgdisk", "ntfscat", "ntfsundelete", "unsquashfs", "bsdtar"):
        fake(tmp_path / "bin", name, f'''
printf '{name} %s\\n' "$*" >> "$CASE/called"
[ "${{FAIL_COMMAND:-}}" != {name} ] || {{ echo 'injected {name} failure' >&2; exit 7; }}
printf 'read-result\\n'
''')


def run(tmp_path, tool, arg, confirm="lab-pc", *, direct=False, extra="", erase=None):
    erase = erase or LIB / "tools_erase.sh"
    provider = "erase" if tool in dict(DESTROY) or tool == "nvme-sanitize-status" else "recover"
    command = f"tools_{provider}_run" if direct else "tools_run"
    script = f'''
export CASE=$(cd {shlex.quote(tmp_path.as_posix())} && pwd)
export PATH="$CASE/bin:/usr/bin:/bin" RUN_DIR="$CASE/run" SYSROOT="$CASE/sysroot"
export MACHINE_NAME=lab-pc LIB_DIR={shlex.quote(LIB.as_posix())} TOOLS_SELECTION="$CASE/selection.json"
. "$LIB_DIR/common.sh"
. "$LIB_DIR/waits.sh"
. "$LIB_DIR/tools.sh"
tools_load
. {shlex.quote(erase.as_posix())}
if [ "$2" = @archive ]; then set -- "$1" "$CASE/archive" "$3"; fi
{extra}
{command} "$1" "$2" "$3"
'''
    return subprocess.run([BASH, "-c", script, "tools-test", tool, arg, confirm],
                          text=True, encoding="utf-8", errors="replace", capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=20, cwd=ROOT)


def calls(tmp_path):
    return (tmp_path / "called").read_text()


def output(tmp_path, tool):
    return (tmp_path / "run" / f"tool-{tool}.out").read_text(encoding="utf-8")


@pytest.mark.parametrize("tool,arg", DESTROY + READ)
def test_selected_tool_runs_the_exact_noninteractive_command(tmp_path, tool, arg):
    room(tmp_path)
    result = run(tmp_path, tool, arg)
    assert result.returncode == 0, result.stderr + output(tmp_path, tool)
    expected = {
        "nvme-sanitize": "nvme sanitize /dev/nvme0 --sanact=2",
        "nvme-sanitize-status": "nvme sanitize-log /dev/nvme0 --raw-binary",
        "zap-partition-table": "sgdisk --zap-all /dev/sda",
        "nwipe": "nwipe --autonuke --nogui --nowait --method=dodshort --rounds=1 --verify=all",
        "windows-ntfs-cat": "ntfscat /dev/sda1 /Windows/win.ini",
        "ntfs-recover-scan": "ntfsundelete --scan /dev/sda1",
        "unsquashfs": "unsquashfs -s ", "bsdtar": "bsdtar -tf ",
    }
    assert expected[tool] in calls(tmp_path)
    if tool == "nwipe":
        assert calls(tmp_path).strip().endswith(" /dev/sda")
    assert "מה זה אומר" in output(tmp_path, tool)


@pytest.mark.parametrize("tool,arg", DESTROY)
@pytest.mark.parametrize("confirm", ["", "wrong-name"])
@pytest.mark.parametrize("direct", [False, True])
def test_named_confirmation_is_required_at_both_entry_points(tmp_path, tool, arg, confirm, direct):
    room(tmp_path)
    result = run(tmp_path, tool, arg, confirm, direct=direct)
    assert result.returncode == 3, result.stderr
    assert calls(tmp_path) == ""


@pytest.mark.parametrize("tool,arg", DESTROY)
def test_confirmation_test_detects_removed_module_gate(tmp_path, tool, arg):
    room(tmp_path)
    source = (LIB / "tools_erase.sh").read_text(encoding="utf-8")
    gate = '_tools_disk_destroy_gate "$_out" "$_id on $_arg" "$_confirm" || return 3'
    assert source.count(gate) == 1
    mutant = tmp_path / "erase-mutant.sh"
    mutant.write_text(source.replace(gate, ":"), encoding="utf-8", newline="\n")
    result = run(tmp_path, tool, arg, "wrong-name", direct=True, erase=mutant)
    assert result.returncode == 0, result.stderr + output(tmp_path, tool)
    assert calls(tmp_path), "The mutation must reach the operation, not fail at setup"


@pytest.mark.parametrize("tool,arg", DESTROY + READ)
def test_no_default_target(tmp_path, tool, arg):
    room(tmp_path)
    result = run(tmp_path, tool, "")
    assert result.returncode == 2, result.stderr
    assert calls(tmp_path) == ""


@pytest.mark.parametrize("tool,arg", DESTROY + READ)
def test_unselected_tool_never_runs(tmp_path, tool, arg):
    room(tmp_path)
    (tmp_path / "selection.json").write_text('{"build": [], "student": []}')
    result = run(tmp_path, tool, arg)
    assert result.returncode == 4
    assert calls(tmp_path) == ""


@pytest.mark.parametrize("tool,arg,binary", [
    ("nvme-sanitize", "/dev/nvme0n1", "sanitize"),
    ("nvme-sanitize-status", "/dev/nvme0n1", "sanitize-log"),
    ("zap-partition-table", "/dev/sda", "sgdisk"), ("nwipe", "/dev/sda", "nwipe"),
    ("windows-ntfs-cat", "/dev/sda1:/x", "ntfscat"),
    ("ntfs-recover-scan", "/dev/sda1", "ntfsundelete"),
    ("unsquashfs", "@archive", "unsquashfs"), ("bsdtar", "@archive", "bsdtar")])
def test_operation_failure_is_not_success_and_keeps_stderr(tmp_path, tool, arg, binary):
    room(tmp_path)
    result = run(tmp_path, tool, arg, extra=f"export FAIL_COMMAND={binary}")
    assert result.returncode in (1, 2), result.stderr
    assert "injected" in output(tmp_path, tool)
    assert calls(tmp_path)


@pytest.mark.parametrize("status,rc", [(0, 2), (1, 0), (2, 2), (3, 1), (4, 0), (7, 2), (257, 0)])
@pytest.mark.parametrize("tool", ["nvme-sanitize", "nvme-sanitize-status"])
def test_sanitize_requires_positive_completion(tmp_path, status, rc, tool):
    room(tmp_path)
    log = bytearray(512)
    log[2:4] = status.to_bytes(2, "little")
    (tmp_path / "status").write_bytes(log)
    result = run(tmp_path, tool, "/dev/nvme0n1")
    assert result.returncode == rc, result.stderr + output(tmp_path, tool)


@pytest.mark.parametrize("field,value", [(516, 2), (76, 1), (76, 2)])
def test_sanitize_refuses_other_namespaces_and_shared_controllers(tmp_path, field, value):
    room(tmp_path)
    ctrl = bytearray((tmp_path / "controller").read_bytes())
    ctrl[field] = value
    (tmp_path / "controller").write_bytes(ctrl)
    result = run(tmp_path, "nvme-sanitize", "/dev/nvme0n1")
    assert result.returncode == 2
    assert "nvme sanitize " not in calls(tmp_path)


@pytest.mark.parametrize("file", ["controller", "status"])
def test_truncated_readback_is_not_success(tmp_path, file):
    room(tmp_path)
    (tmp_path / file).write_bytes(b"\0\0\1\0")
    result = run(tmp_path, "nvme-sanitize", "/dev/nvme0n1")
    assert result.returncode == 2
    if file == "controller":
        assert "nvme sanitize " not in calls(tmp_path)


def test_nwipe_zero_without_completion_report_is_not_success(tmp_path):
    room(tmp_path)
    result = run(tmp_path, "nwipe", "/dev/sda", extra="export NO_NWIPE_REPORT=1")
    assert result.returncode == 2
    assert "No successful nwipe completion report" in output(tmp_path, "nwipe")


def test_failed_identity_read_never_starts_sanitize(tmp_path):
    room(tmp_path)
    result = run(tmp_path, "nvme-sanitize", "/dev/nvme0n1", extra="export FAIL_COMMAND=id-ctrl")
    assert result.returncode == 2
    assert "nvme sanitize " not in calls(tmp_path)
    assert "injected" in output(tmp_path, "nvme-sanitize")


@pytest.mark.parametrize("tool,arg", [
    ("nvme-sanitize-status", "/dev/sda"),
    ("windows-ntfs-cat", "/dev/../sda1:/x"),
    ("windows-ntfs-cat", "/dev/sda1"),
    ("windows-ntfs-cat", "/dev/sda1:--help"),
    ("ntfs-recover-scan", "/dev/absent"),
    ("unsquashfs", "--help"), ("bsdtar", "-")])
def test_readers_reject_missing_or_ambiguous_targets(tmp_path, tool, arg):
    room(tmp_path)
    result = run(tmp_path, tool, arg)
    assert result.returncode == 2
    assert calls(tmp_path) == ""


@pytest.mark.parametrize("tool,arg", DESTROY)
def test_clone_media_is_never_erased(tmp_path, tool, arg):
    room(tmp_path)
    (tmp_path / "run/usb-media.disks").write_text(arg.removeprefix("/dev/") + "\n")
    result = run(tmp_path, tool, arg)
    assert result.returncode == 2
    assert calls(tmp_path) == ""


@pytest.mark.parametrize("arg", ["/dev/../sda", "/dev/sda1", "/dev/absent", "--all"])
@pytest.mark.parametrize("tool,unused", DESTROY)
def test_bad_disk_never_reaches_an_eraser(tmp_path, arg, tool, unused):
    room(tmp_path)
    result = run(tmp_path, tool, arg)
    assert result.returncode == 2
    assert calls(tmp_path) == ""

CAT = ("windows-ntfs-cat", "/dev/sda1:/Windows/win.ini")
CEILING = "export TOOLS_CAT_MAX=4096 TOOLS_CAT_SECS=2"
BYTES_OF_X = r"head -c {n} /dev/zero | tr '\0' x"


def cat_with(tmp_path, body, extra=CEILING):
    room(tmp_path)
    fake(tmp_path / "bin", "ntfscat", body)
    return run(tmp_path, *CAT, extra=extra)


def leftovers(tmp_path):
    run_dir = tmp_path / "run"
    return list(run_dir.glob("*.data")) + list(run_dir.glob("*.err"))


def test_ntfs_cat_shows_the_file_under_the_ceiling(tmp_path):
    result = cat_with(tmp_path, BYTES_OF_X.format(n=4000))
    assert result.returncode == 0, result.stderr + output(tmp_path, CAT[0])
    assert "x" * 4000 in output(tmp_path, CAT[0])
    assert not leftovers(tmp_path)


@pytest.mark.parametrize("size", [4096, 10000])
def test_ntfs_cat_at_or_over_the_ceiling_is_a_failure_not_a_shortened_file(tmp_path, size):
    result = cat_with(tmp_path, BYTES_OF_X.format(n=size))
    text = output(tmp_path, CAT[0])
    assert result.returncode == 2
    assert "גדול מהתקרה" in text and "כישלון" in text
    assert "xxxx" not in text
    assert not leftovers(tmp_path)


def test_ntfs_cat_writer_is_stopped_at_the_ceiling_not_after_it(tmp_path):
    body = 'head -c 20000 /dev/zero || exit 3\necho wrote-all >> "$CASE/called"'
    result = cat_with(tmp_path, body)
    assert result.returncode == 2
    assert "wrote-all" not in calls(tmp_path)


def test_ntfs_cat_times_out_visibly(tmp_path):
    result = cat_with(tmp_path, "exec sleep 30", extra="export TOOLS_CAT_MAX=4096 TOOLS_CAT_SECS=1")
    assert result.returncode == 2
    assert "לא הסתיימה תוך 1 שניות" in output(tmp_path, CAT[0])
    assert not leftovers(tmp_path)


@pytest.mark.parametrize("meminfo", [
    "MemAvailable:       8 kB\n", "MemFree: 1048576 kB\n", "MemAvailable: lots kB\n"])
def test_ntfs_cat_refuses_before_reading_when_memory_is_short_or_unknown(tmp_path, meminfo):
    room(tmp_path)
    (tmp_path / "sysroot/proc/meminfo").write_text(meminfo)
    result = run(tmp_path, *CAT, extra=CEILING)
    assert result.returncode == 2
    assert calls(tmp_path) == ""
    assert "הקובץ לא נקרא" in output(tmp_path, CAT[0])
