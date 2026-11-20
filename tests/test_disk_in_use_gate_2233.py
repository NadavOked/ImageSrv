"""#2233 -- no destructive disk tool touches a disk that is in use.

Before the typed machine name and before any command, the wipe tools
(`tools_disk.sh`: blkdiscard, hdparm erase, nvme format, dd, wipefs) and the
catalog erasers (`tools_erase.sh`: nvme-sanitize, zap-partition-table, nwipe) read
three sources: `/proc/mounts`, `/proc/swaps` and the holders under `/sys/block`
(md / dm / LVM, on the disk and on each of its partitions). Any one of them naming
the disk = rc 2, nothing runs. A source that could not be read = rc 2 too, and says
"not checked" -- never "free" (principle 5). A read-only tool (`nvme-sanitize-status`)
is not gated.

Negative control: `tools_disk.sh` and `tools_erase.sh` back to main, tests kept --
every refusal below runs the tool instead (see the PR body).
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable

import pytest

from native import requires_native
from test_agent import BASH
from test_agent_hardening_2431 import tools, touched
from test_tools_additions import DESTROY, READ, calls, output, room, run as erase_run
from test_tools_disk import out_text

pytestmark = requires_native(("bash", BASH))

DISK_TOOLS = [
    ("disk-blkdiscard", "/dev/sda"),
    ("disk-hdparm-erase", "/dev/sda"),
    ("disk-nvme-format", "/dev/nvme0n1"),
    ("disk-dd-zero", "/dev/sda"),
    ("disk-wipefs-all", "/dev/sda"),
]
IN_USE = "הדיסק בשימוש"
NOT_CHECKED = "לא נבדק אם הדיסק בשימוש"


# --- sysroot edits, as shell run after the fake sysroot exists -----------------------------


def disk_of(arg: str) -> str:
    return arg.removeprefix("/dev/")


def part_of(arg: str) -> str:
    d = disk_of(arg)
    return d + ("p1" if d[-1].isdigit() else "1")


def mkpart(d: str, p: str) -> str:
    return f'mkdir -p "$SYSROOT/sys/block/{d}/{p}"'


def mounts(*lines: str) -> str:
    return "printf '%s\\n' " + " ".join(shlex.quote(ln) for ln in lines) + ' > "$SYSROOT/proc/mounts"'


def swaps(*lines: str) -> str:
    head = "Filename Type Size Used Priority"
    return "printf '%s\\n' " + " ".join(shlex.quote(ln) for ln in (head, *lines)) + ' > "$SYSROOT/proc/swaps"'


def shell(cmds: list[str]) -> str:
    return "".join(c + "; " for c in cmds)


# scenario -> (what to do to the sysroot, what the refusal must say)
IN_USE_SCENARIOS: dict[str, Callable[[str, str], tuple[list[str], str]]] = {
    "a partition is mounted": lambda d, p: (
        [mkpart(d, p), mounts(f"/dev/{p} /mnt ext4 rw 0 0")], f"/dev/{p} מעוגן"),
    "the whole disk is mounted": lambda d, p: (
        [mounts(f"/dev/{d} /mnt ext4 rw 0 0")], f"/dev/{d} מעוגן"),
    "a partition is active swap": lambda d, p: (
        [mkpart(d, p), swaps(f"/dev/{p} partition 1000 0 -2")], f"/dev/{p} הוא swap פעיל"),
    "a partition has a dm holder": lambda d, p: (
        [mkpart(d, p), f'mkdir -p "$SYSROOT/sys/block/{d}/{p}/holders"',
         f': > "$SYSROOT/sys/block/{d}/{p}/holders/dm-0"'], "מוחזק על ידי dm-0"),
    "the disk itself is held by md": lambda d, p: (
        [f': > "$SYSROOT/sys/block/{d}/holders/md0"'], "מוחזק על ידי md0"),
}


# --- tools_disk.sh -------------------------------------------------------------------------


@pytest.mark.parametrize("tool_id,arg", DISK_TOOLS)
@pytest.mark.parametrize("scenario", list(IN_USE_SCENARIOS))
def test_a_disk_in_use_is_refused_and_nothing_runs(tmp_path, tool_id, arg, scenario):
    cmds, says = IN_USE_SCENARIOS[scenario](disk_of(arg), part_of(arg))
    r = tools(tmp_path, f"tools_disk_run {tool_id} {arg} lab-pc", pre=shell(cmds))
    body = out_text(tmp_path, tool_id)
    assert r.returncode == 2, (r.returncode, body)
    assert IN_USE in body and says in body, body
    assert touched(tmp_path) == [], touched(tmp_path)


@pytest.mark.parametrize("tool_id,arg", DISK_TOOLS)
@pytest.mark.parametrize("what,cmd,named", [
    ("mounts", 'rm "$SYSROOT/proc/mounts"', "/proc/mounts"),
    ("swaps", 'rm "$SYSROOT/proc/swaps"', "/proc/swaps"),
    ("holders", None, "holders"),
])
def test_a_source_that_could_not_be_read_is_not_free(tmp_path, tool_id, arg, what, cmd, named):
    cmd = cmd or f'rmdir "$SYSROOT/sys/block/{disk_of(arg)}/holders"'
    r = tools(tmp_path, f"tools_disk_run {tool_id} {arg} lab-pc", pre=shell([cmd]))
    body = out_text(tmp_path, tool_id)
    assert r.returncode == 2, (r.returncode, body)
    assert NOT_CHECKED in body and named in body and "לא נקרא" in body, body
    assert IN_USE not in body.replace(NOT_CHECKED, ""), body    # two different answers, never folded
    assert touched(tmp_path) == [], touched(tmp_path)


@pytest.mark.parametrize("tool_id,arg", DISK_TOOLS)
def test_a_free_disk_runs_even_when_other_disks_and_lookalikes_are_in_use(tmp_path, tool_id, arg):
    """Control for the refusals: the same tools run when nothing names *this* disk -- another
    disk's mount and swap, tmpfs lines, and /dev/<disk>10 when only <disk>1 exists (a prefix
    is not a partition)."""
    d = disk_of(arg)
    cmds = [mounts("tmpfs /run tmpfs rw 0 0", "/dev/sdb1 /data ext4 rw 0 0", f"/dev/{d}10 /x ext4 rw 0 0"),
            swaps("/dev/sdb2 partition 1000 0 -2")]
    r = tools(tmp_path, f"tools_disk_run {tool_id} {arg} lab-pc", pre=shell(cmds))
    assert r.returncode == 0, (r.returncode, out_text(tmp_path, tool_id))
    assert touched(tmp_path), "the tool ran -- positive evidence"


@pytest.mark.skipif(os.name == "nt", reason="needs real symlinks; the testrunner is Linux")
def test_a_mount_by_uuid_is_resolved_to_its_disk(tmp_path):
    cmds = [mkpart("sda", "sda1"),
            'mkdir -p "$SYSROOT/dev/disk/by-uuid"', ': > "$SYSROOT/dev/sda1"',
            'ln -s ../../sda1 "$SYSROOT/dev/disk/by-uuid/abcd-1234"',
            '[ -L "$SYSROOT/dev/disk/by-uuid/abcd-1234" ]',
            mounts("/dev/disk/by-uuid/abcd-1234 /mnt ext4 rw 0 0")]
    r = tools(tmp_path, "tools_disk_run disk-wipefs-all /dev/sda lab-pc", pre=shell(cmds))
    body = out_text(tmp_path, "disk-wipefs-all")
    assert r.returncode == 2, (r.returncode, r.stderr, body)
    assert IN_USE in body and "by-uuid/abcd-1234 מעוגן" in body, body
    assert touched(tmp_path) == []


# --- tools_erase.sh ------------------------------------------------------------------------


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("tool,arg", DESTROY)
@pytest.mark.parametrize("scenario", list(IN_USE_SCENARIOS))
def test_the_catalog_erasers_refuse_a_disk_in_use(tmp_path, tool, arg, scenario, direct):
    room(tmp_path)
    cmds, says = IN_USE_SCENARIOS[scenario](disk_of(arg), part_of(arg))
    r = erase_run(tmp_path, tool, arg, direct=direct, extra=shell(cmds))
    assert r.returncode == 2, (r.returncode, r.stderr, output(tmp_path, tool))
    body = output(tmp_path, tool)
    assert IN_USE in body and says in body, body
    assert calls(tmp_path) == "", calls(tmp_path)


@pytest.mark.parametrize("tool,arg", DESTROY)
def test_the_catalog_erasers_do_not_take_a_missing_source_for_free(tmp_path, tool, arg):
    room(tmp_path)
    r = erase_run(tmp_path, tool, arg, direct=True, extra=shell(['rm "$SYSROOT/proc/swaps"']))
    body = output(tmp_path, tool)
    assert r.returncode == 2, (r.returncode, r.stderr, body)
    assert NOT_CHECKED in body and "/proc/swaps" in body, body
    assert calls(tmp_path) == ""


@pytest.mark.parametrize("tool,arg", DESTROY)
def test_a_free_disk_runs_the_catalog_erasers(tmp_path, tool, arg):
    room(tmp_path)
    d = disk_of(arg)
    r = erase_run(tmp_path, tool, arg, direct=True,
                  extra=shell([mounts("/dev/sdb1 /data ext4 rw 0 0", f"/dev/{d}10 /x ext4 rw 0 0")]))
    assert r.returncode == 0, (r.returncode, r.stderr, output(tmp_path, tool))
    assert calls(tmp_path) != ""


def test_the_read_only_status_tool_is_not_gated(tmp_path):
    """Asking a disk how its sanitize is going must work while the disk is mounted -- the gate is for writers."""
    room(tmp_path)
    tool, arg = next(t for t in READ if t[0] == "nvme-sanitize-status")
    cmds = [mkpart("nvme0n1", "nvme0n1p1"), mounts("/dev/nvme0n1p1 /mnt ext4 rw 0 0")]
    r = erase_run(tmp_path, tool, arg, direct=True, extra=shell(cmds))
    assert r.returncode == 0, (r.returncode, r.stderr, output(tmp_path, tool))
    assert "sanitize-log" in calls(tmp_path)
    assert IN_USE not in output(tmp_path, tool)
