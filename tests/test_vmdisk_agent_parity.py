"""‏#1334 — השרת מריץ את פונקציות הסוכן, וההעתקים הקטנים שלו מסכימים איתן.

שני דברים נבדקים כאן:

1. **הגשר** (`server/vm/agent_shell.py`) — טוען את כל `agent/lib` בלי תופעות
   לוואי, מחזיר את הסיבה שהסוכן נתן (‏`PLAN_ERROR` וחבריו), ו-`log` אינו
   מלכלך את ה-stdout שפונקציה מחזירה בו ערך.
2. **ארבעת ההעתקים** ב-`server/vm/vmdisk.py` (‏`partclone_for_fs`,
   ‏`partclone_mode`, ‏`partition_role`, ‏`mark_expandable`) — כל אחד מורץ
   מול המקור בסוכן על אותו קלט. סטייה = אדום.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from native import requires_native
from server.vm import agent_shell, vmdisk
from test_agent import BASH, posix

pytestmark = requires_native(("bash", BASH))

SHELL = shutil.which("sh") if os.name != "nt" else BASH

SGDISK_INFO = """\
Partition GUID code: EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 (Microsoft basic data)
Partition unique GUID: 6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D4
First sector: 1085440 (at 530.0 MiB)
Last sector: 498069503 (at 237.5 GiB)
Partition size: 496984064 sectors (237.0 GiB)
Attribute flags: 8000000000000001
Partition name: 'Basic data partition'
"""


def call(tmp_path, func, *args, stubs: dict[str, str] | None = None,
         extra_env: dict[str, str] | None = None):
    # ‏bash.exe שמורץ ישירות בווינדוס אינו רואה את /usr/bin (awk, ‏cut) — כמו
    # ב-`test_agent.sh`. בלינוקס ה-PATH של התהליך נשאר.
    env = {"PATH": "/usr/bin:/bin"} if os.name == "nt" else {}
    env.update(extra_env or {})
    if stubs:
        bin_dir = tmp_path / "stubs"
        bin_dir.mkdir(exist_ok=True)
        for name, body in stubs.items():
            path = bin_dir / name
            path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        env["PATH"] = f"{posix(bin_dir)}:/usr/bin:/bin"
    return agent_shell.call(func, *args, run_dir=tmp_path / "run", env=env or None, shell=SHELL)


# --- הגשר -------------------------------------------------------------------------


def test_the_bridge_loads_every_agent_lib_and_runs_a_function(tmp_path):
    got = call(tmp_path, "partclone_for_fs", "ntfs")
    assert got.ok, got.err
    assert got.out == "partclone.ntfs\n"     # רק הערך — אין שורת log על ה-stdout
    assert all(agent_shell.lib_path(lib).is_file() for lib in agent_shell.LIBS)


def test_the_agents_reason_comes_back_by_name(tmp_path):
    """‏apply_gpt מול יעד שגודל הסקטור שלו לא נקרא — ה-PLAN_ERROR של הסוכן
    עצמו חוזר, לא "rc=1"."""
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"sector_size": 512, "scheme": "gpt"}), encoding="utf-8")
    got = call(tmp_path, "apply_gpt", "loop9", posix(manifest),
               stubs={"blockdev": "exit 1\n", "sgdisk": "echo SGDISK-CALLED >&2; exit 0\n"})
    assert got.rc == 1
    assert got.errors["PLAN_ERROR"] == "לא ניתן לקרוא את גודל הסקטור של הדיסק — לא נכתב בייט"
    assert "SGDISK-CALLED" not in got.err     # נעצר לפני ה---zap-all
    assert got.reason("fallback") == got.errors["PLAN_ERROR"]


def test_log_lines_go_to_the_log_not_into_a_returned_value(tmp_path):
    """‏boot_ca_json מחזיר JSON ב-stdout ו-log בדרך — ב-common.sh ה-log היה
    מודפס גם ל-stdout ושובר את הפרסור."""
    got = call(tmp_path, "boot_ca_json", "/dev/does-not-exist")
    fragment = json.loads("{" + got.out.strip() + "}")
    assert fragment["boot_ca"] is None and fragment["boot_ca_error"]
    assert "boot_ca:" in (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")


# --- ההעתקים מול המקור ---------------------------------------------------------------


FS_VALUES = ["ntfs", "vfat", "fat", "fat32", "fat16", "ext4", "ext3", "ext2", "btrfs",
             "xfs", "swap", "unknown", ""]


@pytest.mark.parametrize("fs", FS_VALUES)
def test_partclone_for_fs_matches_the_agent(tmp_path, fs):
    assert call(tmp_path, "partclone_for_fs", fs).out.strip() == vmdisk.partclone_for_fs(fs)


@pytest.mark.parametrize("tool", ["partclone.ntfs", "partclone.fat", "partclone.dd"])
@pytest.mark.parametrize("flag", ["-c", "-r"])
def test_partclone_mode_matches_the_agent(tmp_path, tool, flag):
    assert call(tmp_path, "partclone_mode", tool, flag).out.split() == \
        vmdisk.partclone_mode(tool, flag)


@pytest.mark.parametrize("guid", [
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B", "e3c9e316-0b5c-4db8-817d-f92df00215ae",
    "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC", "0FC63DAF-8483-4772-8E79-3D69D8477DE4",
    "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F", "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
    "21686148-6449-6E6F-744E-656564454649", ""])
def test_partition_role_matches_the_agent(tmp_path, guid):
    assert call(tmp_path, "_partition_role", guid).out.strip() == vmdisk.partition_role(guid)


def _part(index, role, start):
    return {"index": index, "type_guid": "x", "role": role, "fs": "ntfs",
            "start_sector": start, "size_bytes": 1, "file": f"p{index}.{role}.pcl.zst",
            "sha256": "0" * 64, "expandable": False}


@pytest.mark.parametrize("layout", [
    [("esp", 2048), ("msr", 206848), ("windows", 239616), ("recovery", 9000000)],
    [("linux", 9000000), ("esp", 2048)],                       # ענן: השורש ראשון ואחרון
    [("esp", 2048), ("windows", 239616), ("windows", 8000000)],  # ‏D: אחרי C:
    [("esp", 2048), ("recovery", 4096)],                       # אין מועמד
])
def test_mark_expandable_matches_the_agent(tmp_path, layout):
    parts = [_part(i + 1, role, start) for i, (role, start) in enumerate(layout)]
    compact = ",".join(json.dumps(p, separators=(",", ":")) for p in parts)
    # דרך משתנה סביבה ולא כארגומנט: בווינדוס ה-runtime של MSYS מפרק `{"..."}`
    # בשורת הפקודה (brace/quote), ובלינוקס execve מעביר אותו כמות שהוא.
    agent = json.loads("[" + call(tmp_path, "eval", '_mark_expandable "$PARTS"',
                                  extra_env={"PARTS": compact}).out + "]")
    mine = json.loads(json.dumps(parts))
    vmdisk.mark_expandable(mine)
    assert [p["expandable"] for p in mine] == [p["expandable"] for p in agent]


@pytest.mark.parametrize("minimum,current", [(10 * 2**30, 100 * 2**30), (1, 3 * 2**30),
                                             (50 * 2**30, 52 * 2**30)])
def test_the_shrink_target_is_the_agents_own(tmp_path, minimum, current):
    """היעד בשמירה מ-VM הוא `_shrink_target` של הסוכן — אותה נוסחה (מינימום
    + 10%, לפחות 2GiB, מגה) — ולא עותק. נבדק כאן רק שהקריאה מחזירה מספר."""
    got = call(tmp_path, "_shrink_target", str(minimum), str(current)).out.strip()
    assert got.isdigit() and int(got) <= current and int(got) % (1 << 20) == current % (1 << 20)


def test_the_vm_save_tail_rule_is_the_agents_own(tmp_path):
    mib = 1 << 20
    measured = tmp_path / "measured"
    measured.write_text(
        f"3|windows|guid|2048|999999|{10 * mib}|{10 * mib}|{8 * mib}\n"
        f"4|windows|guid|999999|1000|{300 * mib}|{290 * mib}|{100 * mib}\n",
        encoding="utf-8")
    got = call(tmp_path, "_shrink_tail", posix(measured), "0")
    rows = [line.split("|") for line in got.out.splitlines()]
    assert got.ok
    assert rows[-1][0] == "4"
    assert int(rows[-1][6]) == 236 * mib


def test_the_boot_paths_come_from_bootca_sh(tmp_path):
    """הרשימה שמורידים מה-ESP היא `BOOTCA_PATHS` של הסוכן, לא עותק."""
    class Host:
        def agent(self, func, *args, run_dir, **_kw):
            return call(tmp_path, func, *args)

    paths = vmdisk._boot_paths(Host(), tmp_path / "run")
    assert "/EFI/Microsoft/Boot/bootmgfw.efi" in paths and "/EFI/BOOT/bootx64.efi" in paths


# --- ‏#1491: תקציב העותק של הכיווץ — `_lc_relocs` של הסוכן, דרך הגשר -------------

#: מה ש-ntfsresize v2022.10.3 הדפיס ב-Testrunner (17/10) — ‏-n על C: של
#: ‏`test_vmupdate_integration`, ליעד 2.16GB ול-1GB.
RELOCS_NONE = "Collecting resizing constraints ...\nNeeded relocations : 0 (0 MB)\n"
RELOCS_SOME = "Collecting resizing constraints ...\nNeeded relocations : 3277 (14 MB)\n"


@pytest.mark.parametrize("said,mb", [(RELOCS_NONE, "0"), (RELOCS_SOME, "14"), ("", "")])
def test_the_bridge_reads_the_relocations_the_way_the_agent_does(tmp_path, said, mb):
    """‏`view_budget` קורא ל-`_lc_relocs` דרך הגשר — הוא חייב להיטען שם (‏lcsnap.sh),
    ו-0 הוא מספר שנמדד, לא "לא נמדד". פלט בלי השורה = ריק (‏view_budget נכשל)."""
    out = tmp_path / "relocs.txt"
    out.write_text(said, encoding="utf-8", newline="\n")
    got = call(tmp_path, "_lc_relocs", posix(out))
    assert got.out.strip() == mb, got
