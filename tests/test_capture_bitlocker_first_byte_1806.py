"""#1806: שער ה-BitLocker רץ **לפני שבייט אחד עוזב את המחשב**.

עד #1806 הבדיקה רצה בתוך לולאת הזרם, מחיצה אחר מחיצה. דיסק Windows 11
24H2 טיפוסי — ESP, MSR, C: מוצפן — העלה את מחיצות 1 ו-2 לשרת, ורק
במחיצה 3 הגיעה ההכרעה. כאן נמדד מה **יצא**: כל קריאת curl נרשמת.

#1840 (הכרעת נדב 07/11) הפך את ההכרעה עצמה: כרך מוצפן נקלט גולמי
(`tests/test_bitlocker_raw_1840.py`), והנוסח של סירוב ה-BitLocker הוסר. מה
שנשאר מ-#1806 הוא המקום: השער (`capture_bitlocker_gate`) יושב ליד שער המהובר,
לפני בדיקת המקום, הכיווץ והזרם — ובדיקה **שלא רצה** עדיין מסרבת שם, לפני
שבייט יצא.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
import test_capture_refusals
from test_capture_refusals import capture_run, refusal_reason

pytestmark = requires_native(("bash", BASH))

#: הדיסק של Windows 11 24H2 כפי ש-sgdisk מדפיס אותו: ESP, MSR ו-C:.
THREE_PARTITIONS = (
    '#!/bin/sh\n'
    'if [ "$1" = "-i" ]; then\n'
    '  case "$2" in\n'
    '    1) code=C12A7328-F81F-11D2-BA4B-00A0C93EC93B; first=2048; size=204800 ;;\n'
    '    2) code=E3C9E316-0B5C-4DB8-817D-F92DF00215AE; first=206848; size=32768 ;;\n'
    '    *) code=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7; first=239616; size=4194304 ;;\n'
    '  esac\n'
    '  echo "Partition GUID code: $code (x)"\n'
    '  echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-00000000000$2"\n'
    '  echo "First sector: $first (at 1 MiB)"\n'
    '  echo "Partition size: $size sectors (1 MiB)"\n'
    '  echo "Attribute flags: 0000000000000000"\n'
    '  exit 0\n'
    'fi\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
    'echo "   1            2048          206847   100.0 MiB   EF00  EFI system"\n'
    'echo "   2          206848          239615   16.0 MiB    0C01  MSR"\n'
    'echo "   3          239616         4433919   2.0 GiB     0700  Basic data"\n'
)

#: curl שרושם **כל** קריאה ל-`$RUN_DIR/curl.log` ומרוקן `-T` כמו CURL_SINK.
#: ההעלאה נמדדת במה שיצא, לא במה שהקוד מתכוון לעשות.
CURL_RECORDER = (
    '#!/bin/sh\n'
    'echo "$*" >> "$RUN_DIR/curl.log"\n'
    'case "$*" in */preflight*) echo \'{"status":"ok","reason":"space checked"}\'; exit 0 ;; esac\n'
    'f=""\n'
    'while [ $# -gt 0 ]; do\n'
    '  [ "$1" = "-T" ] && { f="$2"; shift; }\n'
    '  shift\n'
    'done\n'
    '[ -n "$f" ] && cat "$f" > /dev/null\n'
    'exit 0\n'
)

#: partclone שרושם שהופעל — הוא הקורא של כל בייט שיוצא.
PARTCLONE_RECORDER = (
    '#!/bin/sh\n'
    'echo "$*" >> "$RUN_DIR/partclone.ran"\n'
    'head -c 4096 /dev/zero\n'
    'exit 0\n'
)


def _unchecked_third(tmp_path: Path, monkeypatch):
    # dd שנכשל על כותרת מחיצה 3 בלבד — "לא הצלחנו לבדוק", לא "מוצפן" ולא "נקי".
    monkeypatch.setattr(test_capture_refusals, "CAPTURE_TIMEOUT", 400)
    real_dd = shutil.which("dd") or "/usr/bin/dd"
    dd = ('#!/bin/sh\ncase "$*" in *sda3*bs=512*) exit 5 ;; esac\n'
          f'exec {posix(Path(real_dd))!r} "$@"\n')
    return capture_run(
        tmp_path,
        stubs={"sgdisk": THREE_PARTITIONS, "curl": CURL_RECORDER, "dd": dd,
               "blkid": "#!/bin/sh\nexit 2\n",
               "partclone.dd": PARTCLONE_RECORDER,
               "partclone.vfat": PARTCLONE_RECORDER},
    )


def test_a_third_partition_that_could_not_be_checked_is_refused_before_a_byte_leaves(tmp_path, monkeypatch):
    """מחיצה 3 שכותרתה לא נקראה ← סירוב בשמה, ואף קובץ (`p1`, `p2`) לא נפתח
    בשרת, partclone לא רץ, והשלב `stream` לא נפתח (עיקרון 5)."""
    box, run, out = _unchecked_third(tmp_path, monkeypatch)
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לבדוק BitLocker במחיצה 3" in reason and "dd rc=5" in reason, reason

    sent = (run / "curl.log").read_text(encoding="utf-8") \
        if (run / "curl.log").exists() else ""
    uploads = [line for line in sent.splitlines() if "/files/" in line]
    assert uploads == [], f"בייטים יצאו לשרת לפני הסירוב: {uploads}"
    assert not (run / "partclone.ran").exists(), \
        (run / "partclone.ran").read_text(encoding="utf-8")
    assert not list(run.glob("upload.*")), "נפתח fifo העלאה לפני הסירוב"
    stage = (run / "stage").read_text(encoding="utf-8").strip()
    assert stage == "ntfs_check", f"הקליטה הגיעה לשלב {stage} לפני הסירוב"


def test_the_refusal_comes_before_the_space_check_and_the_shrink(tmp_path, monkeypatch):
    """אותו "לפני בייט": גם בדיקת המקום מול השרת (`/preflight`) והכיווץ
    הם עבודה על דיסק שבכל מקרה יסורב. השער יושב ליד שער המהובר — בלולאה
    הקודמת לכל אלה."""
    _box, run, _out = _unchecked_third(tmp_path, monkeypatch)
    sent = (run / "curl.log").read_text(encoding="utf-8") \
        if (run / "curl.log").exists() else ""
    assert sent == "", f"השרת שמע מהקליטה לפני הסירוב: {sent}"


def test_the_hibernation_header_promise_is_kept_in_capture():
    """`hibernation.sh:2-3` מבטיח "Both run before a byte moves". הטסט
    הזה קורא את capture.sh: הקריאה ל-`capture_bitlocker_gate` יושבת לפני
    `capture_stage stream` ואינה בתוך לולאת הזרם."""
    src = (AGENT / "lib" / "capture" / "capture.sh").read_text(encoding="utf-8")
    gate = src.index("capture_bitlocker_gate")
    assert gate < src.index("capture_stage shrink"), "השער אחרי הכיווץ"
    assert src.count("capture_bitlocker_gate") == 1, "השער נשאר גם בלולאת הזרם"
    header = (AGENT / "lib" / "capture" / "hibernation.sh").read_text(encoding="utf-8")
    assert "Both run before a byte moves" in header
