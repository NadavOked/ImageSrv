"""#379 — `db.journal` נטל את נעילת הכתיבה בלי `writing`.

זה האתר האחרון במשפחה של #272 / #356 / #313, והחמור שבהם מבחינת
התפוצה: `journal` נקרא כמעט מכל מסלול בשרת — כניסה, יצירת קבוצה,
פתיחת סבב, שומר האתחול, תהליכון הרקע של השידור.

`journal` **כן** היה מאחורי ``_write_lock`` מאז ומתמיד (הוא היחיד
שמעולם לא סבל מהרעבה), אבל הוא עשה ``conn.execute(...)`` ואחריו
``conn.commit()`` בלי ``writing``. pysqlite מוציא ``BEGIN`` לפני כל
DML, ולכן כתיבה שנכשלה ב-``database is locked`` משאירה את החיבור
**בתוך טרנזאקציה**; משם sqlite אינו מפעיל את ה-busy handler לשדרוג
נעילה, וכל כתיבה על אותו חיבור נכשלת **מיד**. תהליכון של uvicorn חי
לאורך זמן וממוחזר, ולכן אירוע עומס חולף אחד היה מרעיל אותו עד אתחול
השרת — ומכיוון ש-`journal` נמצא על כל מסלול, הנזק לא היה נעצר ביומן.

הנעילה אינה מגנה מפני כותב **חיצוני לתהליך** — `sqlite3` ידני על
השרת, גיבוי, או מישהו במעבדה. לכן היא לבדה לא מספיקה.

הכשל נבנה **דטרמיניסטית** ולא נתלה בדיסק איטי: מחזיק חיצוני תופס את
נעילת הכתיבה, ו-``busy_timeout`` של הקורבן מוקטן. הפיגום מיובא
מ-`test_hello_write_lock` כמות שהוא — שני עותקים של אותו פיגום תחרות
היו נסחפים זה מזה, וזה בדיוק סוג הפיגום שסחיפה בו אינה נראית.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from server import db
from server.db import connect, journal

from test_hello_write_lock import _hold_briefly, _poison, _starve


def test_a_failed_journal_write_leaves_the_connection_usable(tmp_path):
    """שורת יומן שנכשלה אינה רשאית להרעיל את החיבור שכל המסלולים חולקים.

    הראיה חיובית ולא היעדר-חריגה: ``in_transaction`` נבדק מיד אחרי
    הכשל, ואחר כך **אותו חיבור** כותב שורת יומן תחת עומס חולף והשורה
    נקראת בחזרה. בלי ה-``rollback`` הכתיבה השנייה נכשלת מיד, גם אחרי
    שהמחזיק שחרר.
    """
    path = tmp_path / "t.db"
    connect(path)                                   # סכימה
    victim, holder = _poison(path)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        journal(victim, "login_failed", "noc")
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        journal(victim, "login", "", "noc")
    finally:
        brief.join(timeout=30)

    row = victim.execute(
        "SELECT event, user FROM journal ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and (row["event"], row["user"]) == ("login", "noc")
    victim.close()


def test_a_failed_journal_write_still_reaches_its_caller(tmp_path):
    """`journal` אינו בולע חריגות — לא לפני התיקון ולא אחריו.

    #379 תיאר את `journal` כמי שבולע חריגות במכוון, וביקש טסט שיוודא
    שהבליעה נשמרה. **אין בליעה בקוד** — לא ב-`journal` ולא בגרסה שלפני
    התיקון (אין שם ``try``/``except``), ולכן אין מה לשמר; מה שיש לשמר
    הוא ההפך, וזה מה שנבדק כאן. ``writing`` דואג לכך במפורש: הוא עושה
    ``rollback`` ו-``raise`` מחדש, ולא ``return``.

    זה גם השומר מפני תיקון-יתר: אילו התיקון היה מוסיף בליעה "כדי
    שכתיבת יומן לא תפיל מסלול", שורת יומן שאבדה הייתה נראית בדיוק כמו
    שורה שנכתבה — עיקרון 5.
    """
    path = tmp_path / "t.db"
    connect(path)
    victim, holder = _poison(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            journal(victim, "capture_failed", "t1 disk full")
    finally:
        holder.rollback()
        holder.close()
        victim.close()


def test_journal_writes_do_not_starve_each_other(tmp_path):
    """תשע שורות יומן משלושה מסלולים במקביל — וכולן באמת בטבלה.

    `journal` היה מאחורי ``_write_lock`` גם לפני התיקון, ולכן הטסט
    הזה עובר בשני המצבים. זה בדיוק תפקידו: הוא השומר שמראה שהתיקון לא
    שבר את ההוגנות שכבר הייתה.
    """
    db_ = connect(tmp_path / "t.db")
    failures = _starve(
        db_, lambda conn, i, n: journal(conn, f"e{i}{n}", f"d{i}{n}"))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    written = db_.execute("SELECT COUNT(*) AS n FROM journal").fetchone()["n"]
    assert written == 9


# --- השומר שמונע את החזרה הרביעית -------------------------------------------


#: האתרים ב-`server/db.py` שנוטלים את ``_write_lock``, נכון ל-#379.
#: הרשימה קיימת כדי שהסורק לא יעבור בשקט אם ה-AST יפסיק למצוא כלום —
#: בדיקה שמצאה **אפס** אתרים אינה "כל האתרים תקינים" (עיקרון 5).
KNOWN_WRITE_LOCK_SITES = {"net_seen", "set_setting", "journal"}


def _takes_write_lock(node: ast.With) -> bool:
    return any(
        isinstance(name, ast.Name) and name.id == "_write_lock"
        for item in node.items
        for name in ast.walk(item.context_expr)
    )


def _wraps_in_writing(node: ast.With) -> bool:
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Name)
        and item.context_expr.func.id == "writing"
        for item in node.items
    )


def _write_lock_sites() -> list[tuple[str, int, bool]]:
    """כל ``with _write_lock`` ב-`server/db.py`: פונקציה, שורה, ועטוף?"""
    tree = ast.parse(Path(db.__file__).read_text(encoding="utf-8"))
    sites: dict[int, tuple[str, int, bool]] = {}
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.With) and _takes_write_lock(node):
                sites[node.lineno] = (
                    func.name, node.lineno, _wraps_in_writing(node))
    return sorted(sites.values(), key=lambda site: site[1])


def test_every_write_lock_site_in_db_is_wrapped_in_writing():
    """נעילה בלי ``writing`` היא בדיוק הבאג של #272/#356/#313/#379.

    `_write_lock` מסדר את **הכותבים שלנו** בתור הוגן; הוא אינו עושה
    ``rollback`` ואינו מגן מפני כותב חיצוני לתהליך. אתר שנוטל אותו
    לבדו נראה מוגן והוא בדיוק זה שמרעיל את החיבור — ולכן הבדיקה כאן
    היא על **הצורה**, ולא על אתר אחד שמישהו זכר.

    הסורק מאמת קודם את עצמו מול האתרים הידועים: בדיקה שלא מצאה אתרים
    היא בדיקה שלא רצה, לא בדיקה שעברה.
    """
    sites = _write_lock_sites()
    found = {name for name, _, _ in sites}
    assert KNOWN_WRITE_LOCK_SITES <= found, (
        f"הסורק לא מצא את {sorted(KNOWN_WRITE_LOCK_SITES - found)} — "
        "הוא כנראה שבור, ובדיקה שלא רצה אינה בדיקה שעברה")

    unwrapped = [f"{name} (שורה {line})" for name, line, ok in sites if not ok]
    assert not unwrapped, (
        "\u200f_write_lock נלקח בלי writing ב-server/db.py: "
        + ", ".join(unwrapped)
        + " — כתיבה שתיכשל שם תשאיר את החיבור בטרנזאקציה, ומשם כל "
          "כתיבה על אותו תהליכון נכשלת מיד עד אתחול השרת (#272)")
