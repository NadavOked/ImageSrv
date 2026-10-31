"""‏#1899 — לינוקס עם /boot נפרד: מחיצת המערכת היא השורש, לא /boot.

ברירת המחדל של Fedora (ונפוץ באובונטו עם LVM): ‏ESP, ‏/boot של 1GB, שורש
‏btrfs — שתיים עם GUID של linux. "הראשונה על הדיסק" (min-free, ‏`diskfit.sh`
ו-`server/library/minfree.py`) ו"הראשונה באינדקס" (שם המחשב, ‏`hostname_linux.sh`)
בחרו את /boot: האימג' נדחה בכל דיסק ("יישארו 0.8GB") ושם המחשב נכשל ב-no_etc.

מעכשיו הקליטה מסמנת את השורש על ראיה (‏etc/os-release או etc/fstab בעיגון
לקריאה בלבד; ב-btrfs גם רמה אחת מטה) — ‏`"system": true`, כמו #1875 ל-Windows
— ושלושת הקוראים משתמשים בסימון. מניפסט ישן בלי סימון עם יותר ממחיצת linux
אחת: "לא נבדק", לא `short` שגוי.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from server.library import minfree
from server.library.images import required_bytes

from test_agent import AGENT, BASH, posix, sh
from agentlib import flat_lib

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

GIB = 1 << 30
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
LNX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
LINUX_WHY = "לא ידוע איזו מחיצת לינוקס היא השורש (אין סימון מהקליטה, ויש יותר מאחת)"


def fedora(marked: bool = True) -> dict:
    parts = [
        dict(index=1, type_guid=ESP, role="esp", fs="vfat", start_sector=2048,
             size_bytes=600 << 20, used_bytes=20 << 20),
        dict(index=2, type_guid=LNX, role="linux", fs="ext4", start_sector=1230848,
             size_bytes=1 * GIB, used_bytes=200 << 20),
        dict(index=3, type_guid=LNX, role="linux", fs="btrfs", start_sector=3328000,
             size_bytes=60 * GIB, used_bytes=12 * GIB),
    ]
    if marked:
        parts[2]["system"] = True
    for p in parts:
        p.update(file=f"p{p['index']}.x.pcl.zst", sha256="aa" * 32, expandable=False)
    m = {"schema": 1, "id": "img_1899", "name": "t", "family": 256, "scheme": "gpt",
         "sector_size": 512, "disk_guid": None, "source_disk_bytes": None, "partitions": parts}
    m["min_target_bytes"] = required_bytes(m)
    return m


# --- הקליטה: הסימון על ראיה -----------------------------------------------------


def _probe(tmp_path: Path, fs: str, layout: str, mount_rc: int = 0) -> tuple[str, bool]:
    """`_linux_system` האמיתי; העיגון מזויף — יוצר את `layout` תחת נקודת העיגון."""
    run = tmp_path / "run"
    run.mkdir(parents=True)
    out = sh(f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/log; "
             f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/capture/manifest.sh; "
             f'_probe_mount() {{ [ {mount_rc} = 0 ] || return 1; for f in {layout}; do '
             'mkdir -p "$4/$(dirname "$f")"; : > "$4/$f"; done; }; '
             'umount() { rm -rf "$1"/*; }; '
             f'why=$(_linux_system 3 /dev/sda3 {fs}); echo "rc=$? why=$why"')
    return out.strip().splitlines()[-1], (run / "system.3").exists()


@pytest.mark.skipif(BASH is None, reason="bash is required")
@pytest.mark.parametrize("fs,layout,marked", [
    ("ext4", "etc/os-release", True),
    ("btrfs", "root/etc/os-release", True),          # ‏Fedora: השורש ב-subvolume "root"
    ("btrfs", "@/etc/fstab", True),                   # ‏Ubuntu: "@"
    ("ext4", "grub2/grub.cfg", False),                # ‏/boot — אין /etc
])
def test_the_root_is_marked_on_evidence_and_boot_is_not(tmp_path, fs, layout, marked):
    rc, is_marked = _probe(tmp_path, fs, layout)
    assert rc == "rc=0 why="
    assert is_marked is marked


@pytest.mark.skipif(BASH is None, reason="bash is required")
def test_a_root_that_could_not_be_checked_refuses_by_name(tmp_path):
    rc, is_marked = _probe(tmp_path, "ext4", "etc/os-release", mount_rc=1)
    assert rc.startswith("rc=1 why=מחיצה 3: לא הצלחנו לעגן לקריאה ולבדוק אם היא שורש הלינוקס"), rc
    assert not is_marked


@pytest.mark.skipif(BASH is None, reason="bash is required")
def test_a_filesystem_we_do_not_capture_is_not_mounted(tmp_path):
    rc, is_marked = _probe(tmp_path, "unknown", "etc/os-release", mount_rc=1)
    assert rc == "rc=0 why=" and not is_marked


# --- min-free: השורש ולא /boot, בסוכן ובשרת ----------------------------------


def _agent(tmp_path: Path, m: dict, disk: int) -> str:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    (tmp_path / "min_free_gb").write_text("20\n")
    out = sh(f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in
                       ("common", "jsonq", "progress", "restore", "expand", "grow", "ntfsunhib", "expandshare", "diskfit"))
             + f'min_free_check {posix(path)!r} {disk} {required_bytes(m)}')
    return out.strip().splitlines()[-1]


@needs_shell
def test_a_fedora_image_is_measured_on_its_root_not_on_boot(tmp_path):
    m = fedora()
    assert minfree.system_partition(m)["index"] == 3
    disk = 256060514304
    assert minfree.refusal(m, disk, 20) is None
    assert _agent(tmp_path, m, disk) == "ok"


@needs_shell
def test_an_old_image_without_the_mark_is_unchecked_not_a_wrong_short(tmp_path):
    m = fedora(marked=False)
    want = f"לא נבדק אם יישארו 20GB פנויים במחיצת הלינוקס הראשית — {LINUX_WHY}"
    assert minfree.refusal(m, 256060514304, 20) == want
    assert minfree.unchecked(m, 20) == want
    assert _agent(tmp_path, m, 256060514304) == "unchecked|" + want


# --- שם המחשב: השורש המסומן --------------------------------------------------


@pytest.mark.skipif(BASH is None or shutil.which("jq") is None, reason="bash and jq are required")
def test_the_hostname_mounts_the_marked_root(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    path = tmp_path / "m.json"
    path.write_text(json.dumps(fedora()), encoding="utf-8")
    plan = "\n".join(f"{p['index']}|{p['type_guid']}|{p['role']}|{p['fs']}|{p['start_sector']}"
                     f"|{p['size_bytes']}|{p['file']}|{p['sha256']}|false|||"
                     for p in fedora()["partitions"])
    (tmp_path / "plan").write_text(plan + "\n")
    out = sh(f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
             f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
             f". {posix(AGENT)}/lib/postdeploy/hostname_linux.sh; "
             f'mount() {{ echo "$*" >> {posix(run)!r}/mounts; mkdir -p "$RUN_DIR/linux/etc"; }}; '
             f'_mount_linux sda {posix(path)!r} {posix(tmp_path / "plan")!r}')
    assert out.strip().splitlines()[-1].endswith("/linux")
    assert "/dev/sda3" in (run / "mounts").read_text(), "עוגנה /boot (‏sda2) ולא השורש"
