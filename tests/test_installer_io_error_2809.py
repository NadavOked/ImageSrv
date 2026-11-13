"""#2809: a disk I/O error while formatting showed as a general installer
failure, and the line that tells them apart was cut off.

On metal (#2775) the root partition's mke2fs ended with ``Input/output error
while writing out and closing file system`` -- the drive, its cable or its
port, not the installer. The screen said ``format-root: ... failed on
/dev/sda2``, the technical details were the engine log's last 20 lines (the
mke2fs line was above them), and mke2fs's backspace progress drew as boxes.

The bridge (``do_progress``) now says the Hebrew sentence as the failure when
a failed install's log has the I/O error, keeps that line in the details even
when the tail does not reach it, and drops control characters. sh + jq, like
the bridge's other tests (Linux/testrunner).
"""
from __future__ import annotations

from test_installer_gui_bridge import _machine, bridge, pytestmark  # noqa: F401  (sh + jq only)

TOOL = "mke2fs"
IO_LINE = f"{TOOL}: Input/output error while writing out and closing file system"
HE = "שגיאת כתיבה לדיסק. ייתכן שהדיסק, הכבל או השקע תקולים. נסו כבל, שקע או דיסק אחר."
ENGINE_ERROR = "format-root: ext4 format failed on /dev/sda2"
FAILED = f"state=failed\npct=12\ntitle=Installation failed\nlog=\nerror={ENGINE_ERROR}\n"


def _log(m, *lines: str) -> None:
    (m["run"] / "install.log").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def test_an_io_error_is_said_as_the_disk_and_its_line_survives_the_tail(tmp_path):
    m = _machine(tmp_path)
    (m["run"] / "install.state").write_text(FAILED, encoding="utf-8")
    after = [f"umount: /target/proc: not mounted ({i})" for i in range(30)]   # pushes it out of the tail
    _log(m, "Writing superblocks and filesystem accounting information: 0/8\b\b\b1/8\b\b\b",
         IO_LINE, *after, f"installer: [{ENGINE_ERROR}]")
    rc, out, err = bridge(m, "progress")
    assert rc == 0, err
    assert out["error"][-1] == HE, out["error"]            # the last error= is what the GUI keeps
    assert IO_LINE in out["output"], out["output"][:3]
    assert len(out["output"]) == 21
    assert not any(ch in "".join(out["output"]) for ch in "\b\x1b\x7f")


def test_a_failure_without_an_io_error_keeps_the_engines_message(tmp_path):
    m = _machine(tmp_path)
    (m["run"] / "install.state").write_text(FAILED, encoding="utf-8")
    _log(m, f"{TOOL}: Device or resource busy while setting up superblock", f"installer: [{ENGINE_ERROR}]")
    rc, out, err = bridge(m, "progress")
    assert rc == 0, err
    assert out["error"] == [ENGINE_ERROR]


def test_an_io_error_line_in_a_running_install_is_not_a_failure(tmp_path):
    """Only a failed install is headlined; a retried read that recovered is a log line."""
    m = _machine(tmp_path)
    (m["run"] / "install.state").write_text("state=packages\npct=60\ntitle=packages\n", encoding="utf-8")
    _log(m, IO_LINE, "Setting up imagesrv-server")
    rc, out, err = bridge(m, "progress")
    assert rc == 0, err
    assert "error" not in out and IO_LINE in out["output"]


def test_the_backspace_progress_is_not_drawn_as_boxes(tmp_path):
    m = _machine(tmp_path)
    (m["run"] / "install.state").write_text("state=partitioning\npct=5\ntitle=partitioning\n", encoding="utf-8")
    _log(m, "Allocating group tables: 0/8\b\b\b   \b\b\b1/8\b\b\bdone", "\x1b[1mbold\x1b[0m\ttab")
    rc, out, err = bridge(m, "progress")
    assert rc == 0, err
    assert out["output"][0].startswith("Allocating group tables: 0/8") and "\b" not in out["output"][0]
    assert out["output"][1] == "bold\ttab"
