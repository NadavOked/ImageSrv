"""#1821, #3167: שם המחשב בכוורת SYSTEM שלא סונכרנה.

כוורת regf "מלוכלכת" — רצף ראשי (offset 4) שונה מהמשני (offset 8) — היא
כוורת ש-Windows התחיל לכתוב אליה ולא סיים. בעלייה הבאה הוא עלול לשחזר אותה
מ-SYSTEM.LOG1/LOG2, מעל מה שנכתב.

עד #3167 זה היה סירוב: `hive_dirty`, ולא נכתב דבר. **הכרעת נדב (14/11, אפשרות א'),
כמו FOG ו-Clonezilla:** השם נכתב בכל מקרה ונקרא בחזרה כמו בכוורת נקייה, והתוצאה
היא הצלחה עם הערה (`note`, `note_code: hive_dirty`) — לא כשל. כתיבה או קריאה חוזרת
שנכשלו נשארות כשל. כותרת שלא נקראה (`hive_unchecked`) נשארת סירוב.

לוגים לא ריקים ליד כוורת **נקייה** אינם הערה: לפי מפרט regf של Suhanov, Windows
מתעלם מרשומות לוג כשהכוורת אינה מלוכלכת — ולוג "ישן" הוא המצב הרגיל.

בקרה שלילית: `agent/lib/postdeploy/hostname.sh` של הבסיס — הכוורת המלוכלכת
מסורבת (ok=false, `hive_dirty`, אפס כתיבות), ושלושת הטסטים של הכוורת המלוכלכת
נופלים על ההתנהגות.
"""
from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_captured_stdout import HIVEWRITE_OK, only_json, sh, windows_box
from test_smart import BASH

pytestmark = requires_native(("bash", BASH))

#: hivewrite שרושם כל קריאה — "נכתב" נמדד ברשימה, לא בהיעדר שגיאה.
HIVEWRITE_LOGGED = 'echo "$*" >> "$RUN_DIR/hivewrites"\n' + HIVEWRITE_OK
#: כתיבה שנכשלת (יציאה 1) — הקריאה החוזרת כמו ב-HIVEWRITE_OK.
HIVEWRITE_FAILS = ('echo "$*" >> "$RUN_DIR/hivewrites"\n'
                   'if [ "$1" = "-g" ]; then case "$*" in *Current*) echo 1;; *) echo LAB1-05;; esac; exit 0; fi\n'
                   'exit 1')
#: הכתיבה "מצליחה" והקריאה החוזרת מחזירה את השם הישן — מה שנקרא מהדיסק קובע.
HIVEWRITE_OLD_BACK = ('echo "$*" >> "$RUN_DIR/hivewrites"\n'
                      'if [ "$1" = "-g" ]; then case "$*" in *Current*) echo 1;; *) echo OLD-NAME;; esac; exit 0; fi\n'
                      'exit 0')

NOTE = "האימג' לא כובה נקי, ייתכן ש-Windows יחזיר את השם הישן."


def _hive(seq1: int, seq2: int) -> bytes:
    return b"regf" + seq1.to_bytes(4, "little") + seq2.to_bytes(4, "little") + bytes(500)


def _name(tmp_path: Path, hive: bytes, logs: bytes = b"", hivewrite: str = HIVEWRITE_LOGGED
          ) -> tuple[dict, list[str], list[str]]:
    """(התוצאה, הכתיבות, הקריאות החוזרות של השם -- בלי Select\\Current)."""
    script, run = windows_box(tmp_path, hivewrite=hivewrite)
    config = run / "win" / "Windows" / "System32" / "config"
    (config / "SYSTEM").write_bytes(hive)
    if logs:
        (config / "SYSTEM.LOG1").write_bytes(logs)
        (config / "SYSTEM.LOG2").write_bytes(logs)
    result = only_json(sh(script + "write_hostname sda /dev/null LAB1-05"))
    calls = run / "hivewrites"
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return (result, [line for line in lines if not line.startswith("-g ")],
            [line for line in lines if line.startswith("-g ") and "Current" not in line])


def test_a_dirty_hive_is_named_with_a_note_not_refused(tmp_path):
    result, writes, reads = _name(tmp_path, _hive(41, 40))
    assert result == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry",
                      "note": NOTE, "note_code": "hive_dirty"}, result
    assert len(writes) == 2, f"השם לא נכתב לכוורת שלא סונכרנה: {writes}"
    assert sum("ComputerName" in r or "Tcpip" in r for r in reads) == 3, f"השם לא נקרא בחזרה כמו בכוורת נקייה: {reads}"


def test_a_dirty_hive_whose_write_fails_is_still_a_failure(tmp_path):
    result, writes, _ = _name(tmp_path, _hive(41, 40), hivewrite=HIVEWRITE_FAILS)
    assert (result["ok"], result.get("code")) == (False, "hive_write_failed"), result
    assert "note" not in result, result
    assert writes, "הכתיבה נוסתה -- והכשל שלה הוא הכשל, לא סירוב מראש"


def test_a_dirty_hive_whose_name_does_not_read_back_is_still_a_failure(tmp_path):
    result, _, reads = _name(tmp_path, _hive(41, 40), hivewrite=HIVEWRITE_OLD_BACK)
    assert (result["ok"], result.get("code")) == (False, "hive_write_failed"), result
    assert "note" not in result and reads, result


def test_a_hive_header_that_does_not_read_is_unchecked_not_clean(tmp_path):
    """עיקרון 5: כותרת קצרה (אין שמונה בייטים אחרי `regf`) — סירוב, לא כתיבה (לא השתנה ב-#3167)."""
    result, writes, _ = _name(tmp_path, b"regf")
    assert (result["ok"], result.get("code")) == (False, "hive_unchecked"), result
    assert writes == [], writes


def test_a_clean_hive_is_named_with_no_note(tmp_path):
    result, writes, _ = _name(tmp_path, _hive(40, 40))
    assert result == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"}, result
    assert len(writes) == 2, writes


def test_logs_beside_a_clean_hive_do_not_add_a_note(tmp_path):
    result, _, _ = _name(tmp_path, _hive(40, 40), logs=b"regf" + bytes(4092))
    assert result == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"}, result
