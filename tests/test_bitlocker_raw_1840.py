"""#1840 (הכרעת נדב 07/11): כרך BitLocker נקלט ומשוחזר **סקטור-אחר-סקטור**.

כמו FOG, Clonezilla ו-Ghost: בלי כיווץ, רק ליעד בגודל המקור או גדול ממנו,
והמפתח נשאר ב-TPM של המקור — במחשב אחר העותק יבקש מפתח שחזור. **זה חייב
להיות גלוי למפעיל** לפני הקליטה ובשחזור. עד #1840 הכרך סורב (#671, #1806).

מה נבדק כאן, על הקוד האמיתי של הסוכן:

* קליטה: דיסק Windows 11 24H2 (ESP, MSR, C: מוצפן) נקלט; מחיצה 3 נקראת
  ב-`partclone.dd` (המסלול הקיים של מחיצה בלי מערכת קבצים מוכרת), נרשמת
  `fs: "BitLocker"`, אינה `expandable`, אינה מעוגנת, ונושאת sha256 כמו כל
  מחיצה. האזהרה כבר בשדה `error` של היעד **כשהבייט הראשון נקרא**, ונשארת
  עד הסוף. גם כש-blkid של busybox אומר "unknown" (החתימה `-FVE-FS-` היא הראיה).
* בדיקה שלא רצה — עדיין סירוב לפני בייט (עיקרון 5;
  `test_capture_bitlocker_first_byte_1806.py`).
* שחזור: יעד קטן מהפריסה — סירוב לפני כתיבה, וההודעה אומרת שהכרך מוצפן
  ולא מתכווץ; שם המחשב והדרייברים לא נוגעים בכרך ומדווחים שדולגו בגלל
  ההצפנה; ה"הושלם" כתום עם מפתח השחזור.
* השרת מקבל את המניפסט, ו-`GET /api/console/images` מעביר את המחיצות.

ה-sha256 בשחזור אינו חדש: `restore_partition` בודק אותו על כל זרם, בלי
תלות ב-fs (restore.sh) — מה שנבדק כאן הוא שהכרך נכנס לאותו מסלול.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from console_src import console_js
from native import requires_native
from test_agent import AGENT, BASH, posix, sh
import test_capture_refusals
from test_capture_refusals import FVE_SECTOR, capture_run
from test_capture_bitlocker_first_byte_1806 import CURL_RECORDER, THREE_PARTITIONS
from test_timeouts import make_stubs

pytestmark = requires_native(("bash", BASH))

NOTE = "מוצפן ב-BitLocker: יועתק כמו שהוא, בלי כיווץ; במחשב אחר יידרש מפתח שחזור"
RESTORE_NOTE = "מוצפן ב-BitLocker: הועתק כמו שהוא, בלי כיווץ; במחשב אחר יידרש מפתח שחזור"

#: partclone.dd שרושם את הקריאה, ואת שדה `error` של היעד **ברגע** הקריאה — מה
#: שהקונסולה והגואי מראים כשהבייט הראשון יוצא.
PARTCLONE_DD = (
    '#!/bin/sh\n'
    'n=$(cat "$RUN_DIR/pcl.n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$RUN_DIR/pcl.n"\n'
    'echo "partclone.dd $*" >> "$RUN_DIR/partclone.ran"\n'
    'cat "$RUN_DIR/targets/sda/error" > "$RUN_DIR/error.at.$n" 2>/dev/null || : > "$RUN_DIR/error.at.$n"\n'
    'head -c 4096 /dev/zero\n'
    'exit 0\n'
)
#: כל כלי partclone אחר — אסור שירוץ כאן (אין מערכת קבצים מוכרת בקופסה).
PARTCLONE_OTHER = '#!/bin/sh\necho "OTHER $0 $*" >> "$RUN_DIR/partclone.ran"\nexit 9\n'


def _blkid(third: str) -> str:
    """blkid: מחיצה 3 = `third` ("" = בלי TYPE, rc 2, כמו busybox); השאר — בלי TYPE."""
    say = f'echo {third}; exit 0' if third else 'exit 2'
    return ('#!/bin/sh\nfor node; do :; done\n'
            f'case "$node" in *sda3) {say} ;; esac\nexit 2\n')


def _capture(tmp_path: Path, monkeypatch, third: str):
    monkeypatch.setattr(test_capture_refusals, "CAPTURE_TIMEOUT", 400)
    fve = tmp_path / "fve.bin"
    fve.write_bytes(FVE_SECTOR)
    part = tmp_path / "box" / "dev" / "sda3"
    return capture_run(
        tmp_path,
        stubs={"sgdisk": THREE_PARTITIONS, "curl": CURL_RECORDER, "blkid": _blkid(third),
               "partclone.dd": PARTCLONE_DD, "partclone.ntfs": PARTCLONE_OTHER,
               "partclone.fat": PARTCLONE_OTHER},
        shell_pre=f"cp {posix(fve)!r} {posix(part)!r}; ",
    )


@pytest.mark.parametrize("third", ["", "BitLocker"], ids=["busybox-unknown", "blkid-bitlocker"])
def test_an_encrypted_c_is_captured_raw_not_refused(tmp_path, monkeypatch, third):
    """ההכרעה: נקלט, לא מסורב. partclone.dd על מחיצה 3, fs=BitLocker במניפסט,
    לא expandable, לא נמדד (null — לא עוגן), ו-sha256 כמו כל מחיצה.

    בקרה שלילית: על הקוד שלפני #1840 — rc=1 והסירוב של #1806.
    """
    box, run, out = _capture(tmp_path, monkeypatch, third)
    details = (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    assert out.strip().endswith("rc=0"), out + details
    manifest = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    p3 = next(p for p in manifest["partitions"] if p["index"] == 3)
    assert p3["fs"] == "BitLocker", p3
    assert p3["role"] == "data", p3                       # never the system, never mounted
    assert p3["expandable"] is False, p3
    assert p3["used_bytes"] is None, p3
    assert "shrunk_from_bytes" not in p3, p3
    assert p3["size_bytes"] == 4194304 * 512, p3          # the whole volume, as on the source
    assert re.fullmatch(r"[0-9a-f]{64}", p3["sha256"]), p3
    assert not any(p["expandable"] for p in manifest["partitions"]), manifest["partitions"]
    ran = (run / "partclone.ran").read_text(encoding="utf-8")
    assert re.search(r"^partclone\.dd .*-s \S*/sda3 ", ran, re.M), ran
    assert "OTHER" not in ran, ran
    sent = (run / "curl.log").read_text(encoding="utf-8")
    assert "/files/p3.data.pcl.zst" in sent, sent
    # the space check counts every byte of the volume: ciphertext does not compress
    space = json.loads((run / "capture-space.json").read_text(encoding="utf-8"))
    assert {"index": 3, "used_bytes": 4194304 * 512} in space["partitions"], space


def test_the_operator_sees_the_note_before_the_first_byte_and_on_done(tmp_path, monkeypatch):
    """"חובה שזה יהיה גלוי למפעיל, לפני הקליטה": שדה `error` של היעד — מה
    שהקונסולה והגואי מציגים — נושא את המשפט כבר בקריאה הראשונה (מחיצה 1),
    ובדיווח האחרון. אותו נוסח בקונסולה (BL_NOTE, console.js)."""
    box, run, out = _capture(tmp_path, monkeypatch, "")
    assert out.strip().endswith("rc=0"), out
    first = (run / "error.at.1").read_text(encoding="utf-8").strip()
    assert first == f"מחיצה 3: {NOTE}", first
    report = json.loads((box / "progress.json").read_text(encoding="utf-8"))
    assert report["targets"][0]["error"] == f"מחיצה 3: {NOTE}", report
    console = console_js()
    assert f'const BL_NOTE = "{NOTE}";' in console


def _state(tmp_path: Path, fs: str, head: bytes, dd_body: str | None = None) -> str:
    run = tmp_path / "run"
    run.mkdir(parents=True)
    node = tmp_path / "part.bin"
    node.write_bytes(head + bytes(512 - len(head)))
    stubs = make_stubs(tmp_path / "s", {} if dd_body is None else {"dd": f"#!/bin/sh\n{dd_body}\n"})
    return sh(f'. {posix(AGENT)}/lib/core/common.sh; ' + stubs
              + f'RUN_DIR={posix(run)!r}; LOG_FILE={posix(run / "log")!r}; '
              f'. {posix(AGENT)}/lib/capture/hibernation.sh; '
              f'bitlocker_state 3 {fs} {posix(node)!r}').strip()


def test_the_state_has_three_answers_never_two(tmp_path):
    assert _state(tmp_path / "a", "unknown", FVE_SECTOR) == "encrypted"
    assert _state(tmp_path / "b", "BitLocker", b"") == "encrypted"
    assert _state(tmp_path / "c", "unknown", b"\xebR\x90NTFS    ") == "clear"
    unable = _state(tmp_path / "d", "unknown", b"", dd_body="exit 7")
    assert "לא הצלחנו לבדוק BitLocker במחיצה 3" in unable and "dd rc=7" in unable, unable


# --- השחזור -------------------------------------------------------------------

def _bl_manifest(size: int) -> dict:
    esp = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
    win = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
    return {"schema": 1, "id": "img_184001", "name": "bl", "family": 256, "scheme": "gpt",
            "sector_size": 512, "source_disk_bytes": size, "min_target_bytes": size,
            "partitions": [
                {"index": 1, "type_guid": esp, "role": "esp", "fs": "unknown", "start_sector": 2048,
                 "size_bytes": 104857600, "used_bytes": None, "file": "p1.esp.pcl.zst",
                 "sha256": "aa" * 32, "expandable": False},
                {"index": 3, "type_guid": win, "role": "data", "fs": "BitLocker", "start_sector": 239616,
                 "size_bytes": size - 239616 * 512 - 1048576, "used_bytes": None,
                 "file": "p3.data.pcl.zst", "sha256": "bb" * 32, "expandable": False}]}


needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required (the lab has it)")


@needs_jq
def test_a_smaller_target_is_refused_before_a_write_and_says_why(tmp_path):
    """"רק ליעד בגודל המקור או גדול ממנו": בדיקה 2.7 מסרבת לפני הטבלה, וההודעה
    אומרת שהכרך מוצפן ומועתק כמו שהוא — לא "קטן מדי" סתמי."""
    from test_image_sizing import fits_box
    source = 256060514304
    run, path, prelude = fits_box(tmp_path, f"echo {source - 1048576 * 4}", image=_bl_manifest(source))
    out = sh(prelude + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    error = (run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert error.startswith("הדיסק קטן מדי"), error
    assert "מחיצה 3 מוצפנת ב-BitLocker ומועתקת כמו שהיא, בלי כיווץ" in error, error


@needs_jq
def test_a_target_of_the_source_size_passes(tmp_path):
    from test_image_sizing import fits_box
    source = 256060514304
    run, path, prelude = fits_box(tmp_path, f"echo {source}", image=_bl_manifest(source))
    out = sh(prelude + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    assert not (run / "targets" / "sda" / "error").exists()


BL_PLAN = "3|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|data|BitLocker|239616|2147483648|p3.data.pcl.zst|y|false|u"
MOUNT_LOG = 'echo "$0 $*" >> "$FAKE_SERVER/mount.calls"; exit 0'


def _box(tmp_path: Path):
    from test_postdeploy import Box
    box = Box(tmp_path, plan=BL_PLAN, stubs={"ntfs-3g": MOUNT_LOG, "mount": MOUNT_LOG})
    (box.run / "targets" / "sda").mkdir(parents=True)
    return box


def test_the_drivers_never_touch_the_volume_and_say_why(tmp_path):
    """postdeploy: skipped/bitlocker — לא not_windows, ואף עיגון או כתיבה
    לכוורת. וה"הושלם" כתום: הועתק כמו שהוא, ובמחשב אחר — מפתח שחזור."""
    box = _box(tmp_path)
    _, result = box.stage()
    assert result == {"state": "skipped", "packages": [], "reason": "bitlocker"}, result
    assert box.calls() == []
    assert not (box.server / "mount.calls").exists(), (box.server / "mount.calls").read_text()
    error = (box.run / "targets" / "sda" / "error").read_text(encoding="utf-8").strip()
    assert error == f"מחיצה 3 — {RESTORE_NOTE}", error


def test_the_boot_entry_is_still_written_it_sits_on_the_esp(tmp_path):
    """#433: רשומת Boot#### היא על ה-ESP ולא בכרך — כרך BitLocker הוא Windows,
    ולכן הוא אינו "not_windows". כאן אין efivars, והתשובה היא no_efivars: עברנו את השער."""
    box = _box(tmp_path)
    sh(box.script + f'. {posix(AGENT)}/lib/postdeploy/bootentry.sh; ensure_boot_entry sda /dev/null')
    entry = json.loads((box.run / "bootentry.json").read_text(encoding="utf-8"))
    assert entry == {"state": "skipped", "entry": "", "reason": "no_efivars"}, entry


def test_the_note_is_written_once(tmp_path):
    box = _box(tmp_path)
    box.stage()
    box.stage()
    error = (box.run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert error.count("מפתח שחזור") == 1, error


def test_the_hostname_is_skipped_because_of_the_encryption(tmp_path):
    """שם המחשב: לא "אין מחיצת לינוקס" ולא ניסיון עיגון — code=bitlocker,
    והסיבה בשמה."""
    box = _box(tmp_path)
    out = sh(box.script + 'write_hostname sda /dev/null PC-01; echo "rc=$?"')
    line, rc = out.strip().splitlines()[-2:]
    assert rc == "rc=1", out
    result = json.loads(line)
    assert result["code"] == "bitlocker", result
    assert "מחיצה 3 מוצפנת ב-BitLocker — דולג בגלל ההצפנה" in result["error"], result
    assert not (box.server / "mount.calls").exists()


# --- השרת והקונסולה ------------------------------------------------------------

def test_the_server_accepts_a_bitlocker_partition():
    """בלי זה הקליטה הייתה רצה עד הסוף ונדחית ב-finish_capture."""
    from server.library.images import ImageLibrary
    m = _bl_manifest(256060514304)
    assert ImageLibrary._validate(m, require_library_fields=False) is None

