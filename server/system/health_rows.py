"""שורת בדיקה של `/health` — מודול עלה בלי תלויות.

`check` ו-`last_seen` ישבו ב-`health.py`, אבל כל מודול שמוסיף שורות
לבריאות (`learn/*`, `storage/*`, `agent/loops`...) צריך אותם ו-`health`
מייבא אותו — ולכן ייבאו אותם בגוף הפונקציה וקיבלו מעגל ייבוא (CodeQL
py/cyclic-import). כאן הם בלי אף ייבוא פנימי, ו-`health` מייצא אותם מחדש
(`health.check` נשאר ה-API הישן)."""
from __future__ import annotations


def check(check_id: str, label: str, state: str, detail: str) -> dict:
    return {"id": check_id, "label": label, "state": state, "detail": detail}


def last_seen(seconds: int) -> str:
    if seconds < 60:
        return "נראה לאחרונה לפני פחות מדקה"
    return f"נראה לאחרונה לפני {seconds // 60} דק'"
