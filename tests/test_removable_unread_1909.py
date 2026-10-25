"""#1909 (QA-G48) -- one answer to "is this disk internal?" when sysfs does not say.

The gap scan of 20/10 found two defaults for the same question when
``/sys/block/<d>/removable`` does not read (a mount namespace, a sysfs without it):
``cat ... || echo 0`` in the hello ("internal"), ``|| echo 1`` in the picker and the
drawers ("not internal"). #1807/#1832 then put every reader of the bit behind one
test, ``disk_internal`` (sysinfo.sh) -- but no test ever took the file away, and the
failure screen (``fail_disk_he``, failscreen.sh) still said "פנימי" for anything that
was not ``1``: a removable that did not read, and a USB enclosure that says 0.

Here the file is not there at all, beside a disk that is internal, and every reader
gives the same, safe answer: not internal -- never a target, never "פנימי".
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_internal_disk_1807_1832 import _entry, _machine, _pick, _sh

pytestmark = requires_native(("bash", BASH))

FAILSCREEN = f". {posix(AGENT)}/lib/failscreen.sh; "


def _unread(box: Path, name: str) -> None:
    """The disk is there, on a SATA port -- only its removable bit does not read."""
    (box / "sys" / "block" / name / "removable").unlink()


def test_the_hello_does_not_call_a_disk_whose_removable_did_not_read_internal(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "sata")})
    _unread(box, "sda")
    assert _entry(box, "sda")["removable"] is True


def test_the_picker_does_not_take_a_disk_whose_removable_did_not_read(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "sata"), "nvme0n1": ("0", "nvme")})
    _unread(box, "sda")
    assert _pick(box).splitlines() == ["nvme0n1", "rc=0"]


def test_a_disk_whose_removable_did_not_read_is_not_a_drawer(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "sata"), "sdb": ("0", "sata")})
    _unread(box, "sda")
    out = _sh(box, "list_drawers", f". {posix(AGENT)}/lib/drawers.sh; ").split()
    assert out == ["sdb"], out


def test_the_failure_screen_does_not_call_it_internal_either(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "sata")})
    _unread(box, "sda")
    out = _sh(box, "fail_disk_he sda", FAILSCREEN).strip()
    assert "פנימי" not in out, out
    assert out.startswith("חיצוני"), out


def test_the_failure_screen_calls_a_usb_enclosure_that_says_0_external(tmp_path):
    """#1832 on the same screen: removable=0 is the media's bit, not the bus's."""
    box = _machine(tmp_path, {"sda": ("0", "usb")})
    assert _sh(box, "fail_disk_he sda", FAILSCREEN).strip().startswith("חיצוני")


# --- the guards: what was right stays right ------------------------------------------


def test_the_failure_screen_still_calls_the_internal_disk_internal(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "sata")})
    (box / "sys" / "block" / "sda" / "size").write_text("976773168\n", newline="\n")   # 500GB
    assert _sh(box, "fail_disk_he sda", FAILSCREEN).strip() == "פנימי · 500GB"


def test_the_internal_disk_beside_it_is_still_internal_in_the_hello(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "sata"), "sdb": ("0", "sata")})
    _unread(box, "sda")
    assert _entry(box, "sdb")["removable"] is False
