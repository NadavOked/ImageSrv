"""קליטה מבר-מתכת 13/10 (img_5790e4, ‏tsk_749e): שלושה שדות שהמניפסט והמשימה
מסרו בלי ראיה.

- **#1368** — ‏`used_bytes=null` בכל מחיצת NTFS. ‏`_used_bytes` עיגן ב-`mount -t
  ntfs`, ובקרנל דביאן 13 אין סוג כזה (‏CONFIG_NTFS_FS כבוי, ‏ntfs3 לא נארז);
  ‏mount של busybox אינו מפעיל mount.ntfs. נמדד בתוך ה-initramfs עצמו
  (‏chroot על ה-Testrunner): ‏"No such device". המדידה עוברת ל-ntfs-3g לקריאה
  בלבד — אותה ספרייה שכבר כותבת את שם המחשב — ו-FUSE, שעליו הוא רץ, מוצהר
  בבנאי (‏tests/test_initramfs_modules.py).
- **#1369** — שתי מחיצות `role=windows`: ה-GUID של Basic Data משותף לכל
  מחיצת נתונים/OEM. ‏windows ניתן רק למחיצה ש-Windows\\System32 יושבת בה.
  מניפסט ישן עם שתיים ממשיך לעבוד: כתיבת השם בוחרת את זו שיש בה מערכת.
- **#1370** — ‏`bytes_total=0` על משימה שהסתיימה: השרת ממלא אותו
  מ-`total_compressed_bytes` כשהמניפסט מתקבל.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, _stub, posix, sh
from test_capture import (WINDOWS_GUID, do_capture, make_task, manifest_for,
                          setup_build_machine)
from test_capture_refusals import CURL_SINK, capture_run, refusal_reason

BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"

#: ‏mount של ה-initramfs מול NTFS: אין סוג כזה בקרנל (‏"No such device").
MOUNT_WITHOUT_NTFS = '#!/bin/sh\necho "mount: No such device" >&2\nexit 32\n'

#: ‏ntfs-3g שמעגן "באמת": רושם את הקריאה, ובמחיצה 2 בלבד חושף מערכת.
#: הארגומנט האחרון הוא נקודת העיגון, זה שלפניו — הצומת.
NTFS3G = (
    '#!/bin/sh\n'
    'echo "$*" >> "$RUN_DIR/ntfs-3g.calls"\n'
    'eval "mnt=\\${$#}"; eval "node=\\${$(($# - 1))}"\n'
    'case "$node" in *2) mkdir -p "$mnt/Windows/System32/config"\n'
    '                    echo hive > "$mnt/Windows/System32/config/SYSTEM" ;; esac\n'
    'exit 0\n'
)
#: ניתוק שמרוקן את נקודת העיגון — אחרת מחיצה אחת "רואה" את קודמתה.
UMOUNT_CLEAN = (
    '#!/bin/sh\n'
    'case "$1" in */run/*) rm -rf "$1/Windows" ;; esac\n'
    'exit 0\n'
)
def _text(path: Path) -> str:
    """תוכן קובץ, או סימן שאינו קיים — הודעת כשל אינה נופלת בעצמה."""
    return path.read_text(encoding="utf-8") if path.exists() else f"<{path.name}: absent>"


DF_ONE_MIB = ('#!/bin/sh\n'
              'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
              'echo "/dev/x 1000000 1024 1 1% /mnt"\n')


# --- #1368: ‏used_bytes של NTFS נמדד דרך ntfs-3g, לקריאה בלבד ----------------


def _probe(tmp_path: Path, call: str, ntfs3g: str = NTFS3G) -> tuple[str, Path]:
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    run = box / "run"
    stub_dir.mkdir(parents=True)
    run.mkdir(parents=True)
    stubs = (_stub(stub_dir / "mount", MOUNT_WITHOUT_NTFS.split("\n", 1)[1])
             + _stub(stub_dir / "ntfs-3g", ntfs3g.split("\n", 1)[1])
             + _stub(stub_dir / "umount", UMOUNT_CLEAN.split("\n", 1)[1])
             + _stub(stub_dir / "df", DF_ONE_MIB.split("\n", 1)[1]))
    out = sh(stubs
             + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
             f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "log")!r}; '
             f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
             + call)
    return out.strip(), run


@requires_native(("bash", BASH))
def test_an_ntfs_partition_is_measured_and_not_null(tmp_path):
    """הבאג עצמו: ‏mount -t ntfs נכשל ב-initramfs, והמדידה חייבת לעבור."""
    out, run = _probe(tmp_path, "_used_bytes /dev/sda3 ntfs")
    assert out == str(1024 * 1024), (out, _text(run / "log"))


@requires_native(("bash", BASH))
def test_the_ntfs_measurement_mounts_read_only(tmp_path):
    """מדידה בלבד: ‏`-o ro` — גם על כרך מלוכלך או מהובר אין כתיבה."""
    _probe(tmp_path, "_used_bytes /dev/sda3 ntfs")
    calls = _text(tmp_path / "box" / "run" / "ntfs-3g.calls")
    assert calls.startswith("-o ro /dev/sda3 "), calls


@requires_native(("bash", BASH))
def test_an_ntfs_partition_that_will_not_mount_is_still_null(tmp_path):
    """הצד השני נשמר: עיגון שנכשל הוא "לא נמדד", לא 0 (#298)."""
    out, _ = _probe(tmp_path, "_used_bytes /dev/sda3 ntfs",
                    ntfs3g='#!/bin/sh\nexit 14\n')
    assert out == "null"


# --- #1369: ‏windows רק על ראיה ------------------------------------------------


@requires_native(("bash", BASH))
def test_a_partition_with_windows_system32_is_the_system(tmp_path):
    out, _ = _probe(tmp_path, "_system_role 2 /dev/sda2 ntfs")
    assert out == "windows"


@requires_native(("bash", BASH))
def test_a_basic_data_partition_without_a_system_is_data(tmp_path):
    out, run = _probe(tmp_path, "_system_role 1 /dev/sda1 ntfs")
    assert out == "data"
    assert _text(run / "ntfs-3g.calls").startswith("-o ro /dev/sda1 ")


@requires_native(("bash", BASH))
def test_a_system_check_that_could_not_mount_is_a_reason_not_a_role(tmp_path):
    """עיקרון 5: "לא הצלחנו לבדוק" אינו data ואינו windows."""
    out, _ = _probe(tmp_path, "_system_role 2 /dev/sda2 ntfs || echo rc=$?",
                    ntfs3g='#!/bin/sh\nexit 1\n')
    assert out.endswith("rc=1"), out
    assert "מחיצה 2" in out and "data" not in out and "windows" not in out


@requires_native(("bash", BASH))
def test_a_non_ntfs_basic_data_partition_is_data_without_mounting(tmp_path):
    """‏Windows עולה רק מ-NTFS: ‏FAT/exFAT בעלי GUID של Basic Data הם נתונים."""
    out, run = _probe(tmp_path, "_system_role 4 /dev/sda4 vfat")
    assert out == "data"
    assert not (run / "ntfs-3g.calls").exists()


# --- הקליטה כולה: המניפסט ש-img_5790e4 היה צריך לקבל -------------------------

#: ‏p1 = מחיצת OEM/נתונים בתחילת הדיסק, ‏p2 = ‏C:. שתיהן Basic Data.
#: ‏p3 = ה-ESP: ‏Windows בדיסק בלי ESP נדחה מאז #1822, וזה לא הנושא כאן.
TWO_BASIC_DATA = (
    '#!/bin/sh\n'
    'if [ "$1" = "-i" ]; then\n'
    '  if [ "$2" = 3 ]; then echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI)"\n'
    '  else echo "Partition GUID code: ' + BASIC_DATA + ' (Microsoft basic data)"; fi\n'
    '  echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-00000000000$2"\n'
    '  case "$2" in\n'
    '    1) echo "First sector: 2048 (at 1024 KiB)" ;;\n'
    '    3) echo "First sector: 411648 (at 201 MiB)" ;;\n'
    '    *) echo "First sector: 206848 (at 101 MiB)" ;;\n'
    '  esac\n'
    '  echo "Partition size: 204800 sectors (100.0 MiB)"\n'
    '  echo "Attribute flags: 0000000000000000"\n'
    '  echo "Partition name: \'Basic data partition\'"\n'
    '  exit 0\n'
    'fi\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
    'echo "   1            2048          206847   100.0 MiB   0700  Basic data"\n'
    'echo "   2          206848          411647   100.0 MiB   0700  Basic data"\n'
    'echo "   3          411648          616447   100.0 MiB   EF00  EFI system"\n'
)

CAPTURE_STUBS = {
    "sgdisk": TWO_BASIC_DATA, "curl": CURL_SINK,
    "blkid": '#!/bin/sh\ncase "$*" in *TYPE*) echo ntfs ;; *UUID*) echo 0A0B ;; esac\n',
    "ntfs-3g.probe": '#!/bin/sh\nexit 0\n',
    "partclone.ntfs": '#!/bin/sh\nhead -c 4096 /dev/zero\n',
    "mount": MOUNT_WITHOUT_NTFS, "ntfs-3g": NTFS3G,
    "umount": UMOUNT_CLEAN, "df": DF_ONE_MIB,
}
#: הכיווץ אינו הנושא כאן, ויש לו טסטים משלו (‏test_shrink_on_capture).
NO_SHRINK = "shrink_before_capture() { return 0; }; "


def _captured(tmp_path, **stubs) -> dict:
    box, run, out = capture_run(tmp_path, stubs={**CAPTURE_STUBS, **stubs},
                                shell_pre=NO_SHRINK)
    assert out.strip().endswith("rc=0"), \
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    return json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))


@requires_native(("bash", BASH))
def test_the_oem_partition_is_data_and_only_the_system_is_windows(tmp_path):
    """img_5790e4: ‏p1 ‏(3GB, ‏sector 2048) ו-‏p2 ‏(C:) שתיהן היו windows."""
    written = _captured(tmp_path)
    roles = {p["index"]: p["role"] for p in written["partitions"]}
    assert roles == {1: "data", 2: "windows", 3: "esp"}, written["partitions"]
    assert written["os"] == "windows"
    expandable = [p["index"] for p in written["partitions"] if p["expandable"]]
    assert expandable == [2]


@requires_native(("bash", BASH))
def test_every_ntfs_partition_in_the_manifest_is_measured(tmp_path):
    written = _captured(tmp_path)
    used = {p["index"]: p["used_bytes"] for p in written["partitions"]}
    assert used == {1: 1024 * 1024, 2: 1024 * 1024, 3: 1024 * 1024}, written["partitions"]


@requires_native(("bash", BASH))
def test_a_capture_whose_system_check_could_not_run_stops_with_the_reason(tmp_path):
    """לא "windows" מתוך ה-GUID ולא "data" מתוך השתיקה — עצירה, בשם."""
    box, run, out = capture_run(
        tmp_path, stubs={**CAPTURE_STUBS, "ntfs-3g": '#!/bin/sh\nexit 1\n'},
        shell_pre=NO_SHRINK)
    reason = refusal_reason(box, run, out)
    assert "מחיצת המערכת" in reason, reason
    assert not (run / "new-manifest.json").exists()


# --- כתיבת השם: מניפסט ישן עם שתי מחיצות windows ---------------------------


def _name_old_image(tmp_path, plan: list[str]) -> tuple[dict, Path]:
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    run = box / "run"
    stub_dir.mkdir(parents=True)
    run.mkdir(parents=True)
    hivewrite = ('if [ "$1" = "-g" ]; then\n'
                 '  case "$*" in *Current*) echo 1;; *) echo LAB1-05;; esac\n'
                 '  exit 0\nfi\nexit 0')
    stubs = (_stub(stub_dir / "ntfs-3g", NTFS3G.split("\n", 1)[1])
             + _stub(stub_dir / "umount", UMOUNT_CLEAN.split("\n", 1)[1])
             + _stub(stub_dir / "hivewrite", hivewrite))
    lines = "\\n".join(plan)
    out = sh(stubs
             + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
             f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
             f'LOG_FILE={posix(run / "log")!r}; '
             f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
             f'. {posix(AGENT)}/lib/hostname.sh; . {posix(AGENT)}/lib/unattend.sh; '
             f"manifest_plan() {{ printf '{lines}\\n'; }}; "
             'partition_node() { echo /dev/fake$2; }; '
             'write_hostname sda /dev/null LAB1-05 || true')
    return json.loads(out.strip().splitlines()[-1]), run


OEM = f"1|{WINDOWS_GUID}|windows|ntfs|4096|1|p1|y|false|u"
SYSTEM = f"2|{WINDOWS_GUID}|windows|ntfs|4096|1|p2|y|true|u"


@requires_native(("bash", BASH))
def test_an_old_manifest_with_two_windows_partitions_is_named_on_the_system(tmp_path):
    """תאימות לאחור: הראשונה במניפסט הישן היא מחיצת ה-OEM. השם נכתב ל-C:."""
    result, run = _name_old_image(tmp_path, [OEM, SYSTEM])
    assert result["ok"] is True, (result, _text(run / "log"))
    mounts = _text(run / "ntfs-3g.calls").split("\n")
    assert "/dev/fake1 " in mounts[0] and "/dev/fake2 " in mounts[1], mounts


@requires_native(("bash", BASH))
def test_no_windows_partition_with_a_system_still_says_no_hive(tmp_path):
    """אף מועמדת בלי מערכת: כמו קודם — הקורא אומר מה חסר, ולא "mount_failed"."""
    result, _ = _name_old_image(tmp_path, [OEM])
    assert result["ok"] is False and result["code"] == "no_hive", result


# --- #1370: ‏bytes_total של משימה שהסתיימה -----------------------------------


def _task_row(server, task_id):
    return server["ctx"].conn.execute(
        "SELECT state, bytes_total FROM tasks WHERE id = ?", (task_id,)).fetchone()


def test_a_finished_capture_carries_its_compressed_total(server):
    created = make_task(server, setup_build_machine(server)).json()
    manifest = manifest_for()
    assert do_capture(server, created["id"], manifest).status_code == 202
    row = _task_row(server, created["id"])
    assert row["state"] == "done"
    assert row["bytes_total"] == manifest["total_compressed_bytes"] > 0


def test_a_total_that_is_not_a_byte_count_is_not_guessed(server):
    """‏"12" (מחרוזת) או True אינם מספר בייטים — המכנה נשאר "לא ידוע".

    מאז #1407 ‏`_validate` דוחה מניפסט כזה בקליטה, בשמו (עיקרון 6), ולכן
    הוא אינו מגיע לחישוב המכנה כלל — ושום מספר אינו מנוחש בדרך."""
    created = make_task(server, setup_build_machine(server)).json()
    manifest = manifest_for()
    manifest["total_compressed_bytes"] = "12"
    response = do_capture(server, created["id"], manifest)
    assert response.status_code == 400
    assert "total_compressed_bytes" in response.json()["detail"]
    assert _task_row(server, created["id"])["bytes_total"] == 0
