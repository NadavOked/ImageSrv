"""#1821: שם לא נכתב לכוורת SYSTEM שלא סונכרנה.

כוורת regf "מלוכלכת" — רצף ראשי (offset 4) שונה מהמשני (offset 8) — היא
כוורת ש-Windows התחיל לכתוב אליה ולא סיים. בעלייה הבאה הוא משחזר אותה
מ-SYSTEM.LOG1/LOG2, מעל מה שנכתב. hivewrite כותב לקובץ הראשי בלבד, ו-commit
של hivex מציב את שני המספרים שווים (`lib/handle.c`) — כך שהסימן נמחק,
והקריאה החוזרת (`hivewrite -g`, מאותו קובץ) "מאשרת" שם שאולי לא ייכנס לתוקף.

לוגים לא ריקים ליד כוורת **נקייה** אינם סיבה לסרב: לפי מפרט regf של
Suhanov, Windows מתעלם מרשומות לוג כשהכוורת אינה מלוכלכת — ולוג "ישן" הוא
המצב הרגיל. `test_logs_beside_a_clean_hive_do_not_block_the_name` שומר על זה.
"""
from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_captured_stdout import HIVEWRITE_OK, only_json, sh, windows_box
from test_smart import BASH

pytestmark = requires_native(("bash", BASH))

#: hivewrite שרושם כל קריאה — "לא נכתב" נמדד ברשימה, לא בהיעדר שגיאה.
HIVEWRITE_LOGGED = 'echo "$*" >> "$RUN_DIR/hivewrites"\n' + HIVEWRITE_OK


def _hive(seq1: int, seq2: int) -> bytes:
    return b"regf" + seq1.to_bytes(4, "little") + seq2.to_bytes(4, "little") + bytes(500)


def _name(tmp_path: Path, hive: bytes, logs: bytes = b"") -> tuple[dict, list[str]]:
    script, run = windows_box(tmp_path, hivewrite=HIVEWRITE_LOGGED)
    config = run / "win" / "Windows" / "System32" / "config"
    (config / "SYSTEM").write_bytes(hive)
    if logs:
        (config / "SYSTEM.LOG1").write_bytes(logs)
        (config / "SYSTEM.LOG2").write_bytes(logs)
    result = only_json(sh(script + "write_hostname sda /dev/null LAB1-05"))
    calls = run / "hivewrites"
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, [line for line in lines if not line.startswith("-g ")]


def test_a_dirty_hive_is_refused_by_name_and_nothing_is_written(tmp_path):
    result, writes = _name(tmp_path, _hive(41, 40))
    assert result["ok"] is False, result
    assert result.get("code") == "hive_dirty", result
    assert "לא סונכרנה" in result["error"] and "41/40" in result["error"], result
    assert writes == [], f"נכתב לכוורת מלוכלכת: {writes}"


def test_a_hive_header_that_does_not_read_is_unchecked_not_clean(tmp_path):
    """עיקרון 5: כותרת קצרה (אין שמונה בייטים אחרי `regf`) — סירוב, לא כתיבה."""
    result, writes = _name(tmp_path, b"regf")
    assert (result["ok"], result.get("code")) == (False, "hive_unchecked"), result
    assert writes == [], writes


def test_a_clean_hive_is_named(tmp_path):
    result, writes = _name(tmp_path, _hive(40, 40))
    assert result == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"}, result
    assert len(writes) == 2, writes


def test_logs_beside_a_clean_hive_do_not_block_the_name(tmp_path):
    result, _ = _name(tmp_path, _hive(40, 40), logs=b"regf" + bytes(4092))
    assert result["ok"] is True, result
