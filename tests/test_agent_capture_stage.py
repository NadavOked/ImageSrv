"""‏#980 §4, §6 — הצד של הסוכן: שלב הקליטה בדיווח, ומצב ה-NTFS בבדיקת המכונה.

§4: ‏`capture_stage` כותב את השלב ל-`$RUN_DIR/stage`, ו-`build_progress` נושא
אותו (‏`"stage"`) **רק בדיווח של משימה** — סבב כיתה/חדר אינו קליטה. קובץ חסר
= השדה חסר (השרת משאיר את השלב הקודם), לא "שלב ראשון".

§6: ‏`probe_ntfs` (ב-`hibernation.sh`, ליד שער המהובר של הקליטה) — אותו
‏`ntfs-3g.probe --readwrite`, קריאה בלבד, לכל מחיצת NTFS ש-blkid רואה:
‏`clean` (0) / ‏`hibernated` (14) / ‏`dirty` (15) / ‏`error` עם ה-rc. כלי שאינו
ארוז = ‏`null`, לא רשימה ריקה שאומרת "בדקנו" (עיקרון 5).

רץ תחת bash (Git Bash בווינדוס), כמו `test_agent_idle.py` — ‏busybox אינו
מותקן על תחנת הפיתוח, והמסלולים כאן אינם תלויים ב-awk של busybox.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

LIB = flat_lib()
def _progress(tmp_path: Path, stage: str | None, who: str = "task") -> dict:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    if stage is not None:
        (run / "stage").write_text(stage + "\n", encoding="utf-8", newline="\n")
    ids = '"" b4:2e:99:07:1a:c4 tsk_4e10' if who == "task" else "ses_1 b4:2e:99:07:1a:c4"
    return json.loads(sh(
        f'export RUN_DIR={posix(run)!r}; . {posix(LIB)}/common.sh; . {posix(LIB)}/progress.sh; '
        f'target_init sda 0; build_progress {ids}'))


def test_a_capture_report_carries_the_stage_the_agent_is_in(tmp_path):
    assert _progress(tmp_path, "shrink")["stage"] == "shrink"


def test_no_stage_file_means_no_field_not_a_first_stage(tmp_path):
    assert "stage" not in _progress(tmp_path, None)


def test_a_round_report_never_carries_a_stage(tmp_path):
    assert "stage" not in _progress(tmp_path, "stream", who="session")


def test_capture_stage_writes_the_file_the_report_reads(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    sh(f'export RUN_DIR={posix(run)!r}; . {posix(LIB)}/capturesink.sh; capture_stage restore_size')
    assert (run / "stage").read_text(encoding="utf-8") == "restore_size\n"


def test_the_capture_flow_marks_every_stage_in_order():
    """הזרימה עצמה (partclone/sgdisk) דורשת דיסק; כאן נבדק שכל שלב מסומן
    במקומו, לפי הסדר, ושהאימות מסומן כשהמניפסט נשלח (השרת מאמת sha256)."""
    capture = (LIB / "capture.sh").read_text(encoding="utf-8")
    marks = re.findall(r"capture_stage (\w+)", capture)
    assert marks == ["ntfs_check", "shrink", "stream", "restore_size"], marks
    order = [capture.index(s) for s in (
        "capture_stage ntfs_check", "capture_ntfs_hibernation_reason \"$_node\"",
        "capture_stage shrink", "capture_space_preflight \"$_task\"",
        "capture_stage stream", "capture_stage restore_size", "shrink_restore_source \"$_disk\"")]
    assert order == sorted(order)
    sink = (LIB / "capturesink.sh").read_text(encoding="utf-8")
    upload = sink[sink.index("upload_manifest() {"):]
    assert "capture_stage verify" in upload.split("curl", 1)[0], "האימות מסומן לפני ה-PUT, לא אחריו"


# --- §6: probe_ntfs ----------------------------------------------------------


def _chmod_x(path: Path) -> None:
    subprocess.run([BASH, "-c", f"chmod +x {posix(path)!r}"], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _stub(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    _chmod_x(p)
    return p


BLKID = r"""echo '/dev/sda1: UUID="A" TYPE="vfat"
/dev/sda3: UUID="B" TYPE="ntfs"
/dev/sda4: UUID="C" TYPE="ntfs"
/dev/sda5: TYPE="ntfs"
/dev/sda6: TYPE="ntfs"
/dev/sdb1: TYPE="ext4"'
exit 0"""
NTFSPROBE = r"""[ "$1" = --readwrite ] || exit 99
case "$2" in /dev/sda3) exit 0 ;; /dev/sda4) exit 14 ;; /dev/sda5) exit 15 ;; *) exit 21 ;; esac"""


def _probe_ntfs(tmp_path: Path, blkid: str | None = BLKID, ntfsprobe: str | None = NTFSPROBE):
    stubs = tmp_path / "stubs"; stubs.mkdir(exist_ok=True)
    env = f"export SYSROOT={posix(tmp_path)!r} PROBE_STUBS=1; "
    if blkid is not None:
        env += f"export BLKID={posix(_stub(stubs, 'blkid', blkid))!r}; "
    if ntfsprobe is not None:
        env += f"export NTFSPROBE={posix(_stub(stubs, 'ntfs-3g.probe', ntfsprobe))!r}; "
    return json.loads(sh(env + f'. {posix(LIB)}/common.sh; . {posix(LIB)}/probe.sh; '
                         f'. {posix(LIB)}/hibernation.sh; probe_ntfs'))


def test_every_ntfs_partition_gets_its_state_from_ntfs_3g_probe(tmp_path):
    assert _probe_ntfs(tmp_path) == [
        {"node": "/dev/sda3", "state": "clean"},
        {"node": "/dev/sda4", "state": "hibernated"},
        {"node": "/dev/sda5", "state": "dirty"},
        {"node": "/dev/sda6", "error": "ntfs-3g.probe rc 21"},
    ]


def test_no_ntfs_partition_is_an_empty_list(tmp_path):
    assert _probe_ntfs(tmp_path, blkid="echo '/dev/sda1: TYPE=\"ext4\"'; exit 0") == []


def test_a_missing_tool_is_null_not_an_empty_list(tmp_path):
    assert _probe_ntfs(tmp_path, blkid=None) is None
    assert _probe_ntfs(tmp_path, ntfsprobe=None) is None


def test_a_failed_blkid_is_an_error_not_an_empty_list(tmp_path):
    assert _probe_ntfs(tmp_path, blkid="exit 4")["error"] == "blkid rc 4"


def test_the_probe_carries_ntfs_and_stays_null_without_hibernation_sh(tmp_path):
    """‏`probe_json` נושא `ntfs`; ‏probe.sh לבדו (כמו בטסטים שלו) = ‏null."""
    out = sh(f'export SYSROOT={posix(tmp_path)!r}; . {posix(LIB)}/common.sh; . {posix(LIB)}/probe.sh; '
             r'printf "{\"x\":0%s}" "$(probe_json)"')
    assert json.loads(out)["probe"]["ntfs"] is None


def test_the_agent_loads_hibernation_sh():
    text = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/hibernation.sh"' in text
