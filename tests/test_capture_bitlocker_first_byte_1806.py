"""#1806: שער ה-BitLocker מסרב **לפני שבייט אחד עוזב את המחשב**.

עד התיקון הבדיקה (`_bitlocker_reason`) רצה בתוך לולאת הזרם, מחיצה אחר
מחיצה. דיסק Windows 11 24H2 טיפוסי — ESP, ‏MSR, ‏C: מוצפן — העלה את
מחיצות 1 ו-2 לשרת, ורק במחיצה 3 הגיע הסירוב. כאן נמדד מה **יצא**: כל
קריאת curl נרשמת, ובקליטה שסורבה אין אף העלאה (`/files/`), אף partclone
שרץ, והשלב `stream` לא נפתח.

ובנוסף: הנוסח. "הצפנת מכשיר" ב-Home, הצפנה שממתינה להפעלה, הצפנה
מושהית ופענוח שלא הסתיים — כולם נושאים את אותה חתימת `-FVE-FS-` על
הדיסק, והמפעיל שבטוח ש"BitLocker כבוי" צריך לקרוא את המצב שלו בהודעה.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
import test_capture_refusals
from test_capture_refusals import FVE_SECTOR, capture_run, refusal_reason
from test_timeouts import make_stubs

pytestmark = requires_native(("bash", BASH))

#: הדיסק של Windows 11 24H2 כפי ש-sgdisk מדפיס אותו: ESP, ‏MSR ו-C:.
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

#: ‏curl שרושם **כל** קריאה ל-`$RUN_DIR/curl.log` ומרוקן `-T` כמו CURL_SINK.
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

#: ‏partclone שרושם שהופעל — הוא הקורא של כל בייט שיוצא.
PARTCLONE_RECORDER = (
    '#!/bin/sh\n'
    'echo "$*" >> "$RUN_DIR/partclone.ran"\n'
    'head -c 4096 /dev/zero\n'
    'exit 0\n'
)


def _encrypted_third(tmp_path: Path, monkeypatch):
    # הקוד הישן מזרים שתי מחיצות לפני הסירוב, ובווינדוס זה יותר מתקרת
    # הקליטה של 90 שניות. התקרה מורחבת כאן כדי שהבקרה השלילית תיכשל על
    # **מה שיצא** ולא על פקיעת זמן; הקוד המתוקן מסרב לפני הזרם.
    monkeypatch.setattr(test_capture_refusals, "CAPTURE_TIMEOUT", 400)
    fve = tmp_path / "fve.bin"
    fve.write_bytes(FVE_SECTOR)
    part = tmp_path / "box" / "dev" / "sda3"
    return capture_run(
        tmp_path,
        stubs={"sgdisk": THREE_PARTITIONS, "curl": CURL_RECORDER,
               "partclone.dd": PARTCLONE_RECORDER,
               "partclone.vfat": PARTCLONE_RECORDER},
        shell_pre=f"cp {posix(fve)!r} {posix(part)!r}; ",
    )


def test_an_encrypted_third_partition_is_refused_before_a_byte_leaves(tmp_path, monkeypatch):
    """הגדרת ה"גמור" של #1806, סעיף 2: מחיצה 3 נושאת `-FVE-FS-` ← סירוב,
    ואף קובץ (`p1`, ‏`p2`) לא נפתח בשרת.

    בקרה שלילית: על `capture.sh` + `hibernation.sh` של לפני התיקון
    ‏`curl.log` מכיל את `files/p1.esp...` ואת `files/p2.msr...` —
    הסירוב מגיע רק במחיצה 3.
    """
    box, run, out = _encrypted_third(tmp_path, monkeypatch)
    reason = refusal_reason(box, run, out)
    assert "BitLocker" in reason and "מחיצה 3" in reason, reason

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
    _box, run, _out = _encrypted_third(tmp_path, monkeypatch)
    sent = (run / "curl.log").read_text(encoding="utf-8") \
        if (run / "curl.log").exists() else ""
    assert sent == "", f"השרת שמע מהקליטה לפני הסירוב: {sent}"


def _bitlocker_reason(tmp_path: Path, fs: str = "ntfs") -> str:
    run = tmp_path / "run"
    run.mkdir(parents=True)
    script = (
        make_stubs(tmp_path / "stubs",
                   {"dd": "#!/bin/sh\nprintf -- '\\353R\\220-FVE-FS-'\n"})
        + f'RUN_DIR={posix(run)!r}; LOG_FILE={posix(run / "agent.log")!r}; '
        + f'. {posix(AGENT)}/lib/hibernation.sh; '
        + f'_bitlocker_reason 3 {fs} /dev/fake'
    )
    return sh(script).strip()


def test_the_refusal_names_the_states_that_look_off_but_are_encrypted(tmp_path):
    """הדרישה של #1806: הודעה ברורה גם למי ש"כיבה" — הצפנת מכשיר ב-Home
    (אין שם תפריט BitLocker), הצפנה שממתינה להפעלה, הצפנה מושהית ופענוח
    שעוד לא הגיע ל-100%. על הדיסק כולם אותה חתימה, ולכן אותו סירוב —
    והנוסח הקיים (#671) נשאר בראשו, מילה במילה."""
    for reason in (_bitlocker_reason(tmp_path / "sig"),
                   _bitlocker_reason(tmp_path / "blkid", fs="BitLocker")):
        assert reason.startswith(
            "מחיצה 3 מוצפנת ב-BitLocker — כבו את BitLocker לפני הקליטה"), reason
        for state in ("הצפנת מכשיר", "ממתינה להפעלה", "מושהית", "100%"):
            assert state in reason, f"{state!r} חסר: {reason}"
        assert "manage-bde -off" in reason, reason


def test_the_hibernation_header_promise_is_kept_in_capture():
    """‏`hibernation.sh:2-3` מבטיח "Both run before a byte moves". הטסט
    הזה קורא את capture.sh: הקריאה ל-`_bitlocker_reason` יושבת לפני
    ‏`capture_stage stream` ואינה בתוך לולאת הזרם."""
    src = (AGENT / "lib" / "capture.sh").read_text(encoding="utf-8")
    gate = src.index("_bitlocker_reason")
    assert gate < src.index("capture_stage shrink"), "השער אחרי הכיווץ"
    assert src.count("_bitlocker_reason") == 1, "השער נשאר גם בלולאת הזרם"
    header = (AGENT / "lib" / "hibernation.sh").read_text(encoding="utf-8")
    assert "Both run before a byte moves" in header
