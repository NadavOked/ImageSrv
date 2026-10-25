"""‏#525 — הכלל במקום הרשימה.

``test_write_lock_sites.py`` בודק **ארבעה אתרים**, והוא אומר זאת
בעצמו: *"ארבעת האתרים נבדקים כאן בנפרד — ``users``, ``console_api``,
``registry``, ו-``db.set_setting``"*. **זו רשימה ידנית**, ולכן אתר
חמישי נולד מחוץ להגנה ואיש אינו מבחין.

וכך זה קרה שלוש פעמים: ‏#272 מצא שניים, ‏#356 הרחיב לשניים, ‏#313
הוסיף ארבעה. **כל פעם ביד, וכל פעם מחדש.** ב-07/09 סריקת AST מצאה
**26** ``.commit()`` מחוץ ל-``writing()`` — עשרים ואחד מהם במסלול
בקשה, כולל ``room.open_round``, ‏``sessions.open/close/_transition``
וכל זרימת ``capture``.

הטסט כאן אינו מוסיף אתר חמישי לרשימה. הוא **סורק את הקוד**, ולכן
אתר עשרים ושניים נופל בלי שאיש יזכור לעדכן טסט.

**‏``MUST_BE_WRAPPED`` הוא מה שהופך אותו לראיה.** סורק שמחזיר אפס
ממצאים יכול להיות "הכול נקי" או "הסורק שבור", ואלה שני מצבים
שונים (עיקרון 5). הקבוצה הזו מכריחה אותו להוכיח שהוא רץ.

**‏``STILL_BARE`` רשאית רק להתכווץ.** הוספת שם אליה היא בדיוק הבאג
שהקובץ הזה קיים כדי למנוע — ולכן יש טסט שאוסר את זה.
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server"

#: אתחול, ה-contextmanager עצמו, והפרוקסי — לא מסלול בקשה.
BOOTSTRAP = frozenset({
    "db.py:writing",
    "db.py:commit",
    "db.py:_initialize",
    "db.py:_create_unique_indexes",
    "db.py:_close_duplicate_actives",
    # ‏`_settle` הוא **אכיפת סדר הנעילות עצמו** (#457): הוא נקרא לפני
    # כל נטילה של `_write_lock` וסוגר טרנזאקציה פתוחה. עטיפתו ב-
    # ‏`writing()` הייתה רקורסיה.
    "db.py:_settle",
    # ‏#1126: מיגרציית CHECK בעלייה — חייבת `commit` לפני `PRAGMA foreign_keys`
    # (אסור לשנותו בתוך טרנזאקציה), ולא במסלול בקשה.
    "db.py:_rebuild_state_tables",
})

#: חוב ידוע, ‏07/09. **הרשימה הזו רשאית רק להתכווץ.**
#: מי שמוסיף אליה שם — עוקף את הכלל במקום לתקן, וזה בדיוק מה
#: שהקובץ הזה קיים כדי למנוע. ‏`test_the_debt_list_only_shrinks`
#: אוכף את זה במספר.
STILL_BARE = frozenset({
    "capture.py:_fail",
    "capture.py:cancel",
    # ‏create_capture יצא מהחוב ב-#1934: הגוף עבר ל-order_capture (#1786)
    # וה-INSERT שם עטוף ב-writing().
    # ‏finish_capture יצא מהחוב ב-#1470: ה-UPDATE ל-verifying בתוך writing().
    "capture.py:upload_partition",
    "console_dhcp.py:forget",
    "console_net.py:add_device",
    "console_net.py:describe_device",
    "console_net.py:forget_device",
    "pulls.py:open_pull",
    "reports.py:_ingest_task",
    "reports.py:ingest",
    "room.py:_attach_wave",
    "room.py:_finish_wave",
    "room.py:_resume",
    "room.py:close_round",
    "room.py:open_round",
    "sessions.py:_transition",
    "sessions.py:close",
    "sessions.py:open",
})

#: אתרים שהסורק **חייב** למצוא כחשופים.
#:
#: בלי זה, סורק שנשבר — ‏`ast` שמפספס, נתיב שהשתנה, ייחוס שגוי —
#: מחזיר "אין הפרות" ונראה כמו הצלחה. ‏**זו הראיה החיובית שהוא רץ.**
#:
#: ⚠️ שים לב שהראיה היא על **חשופים** ולא על עטופים: ‏``writing()``
#: הוא ``contextmanager`` שעושה את ה-``commit`` בעצמו, ולכן פונקציה
#: עטופה נכון **אינה קוראת ל-``.commit()`` כלל**. סורק שמחפש
#: ``.commit()`` בתוך ``with`` מחפש דבר שאינו קיים — וזו הייתה
#: הגרסה הראשונה של הקובץ הזה.
MUST_BE_FOUND_BARE = frozenset({
    "console_dhcp.py:forget",
    "room.py:open_round",
    "sessions.py:_transition",
})


def _scan() -> tuple[set[str], set[str]]:
    """מחזיר (חשופים, עטופים) — ‏``קובץ:פונקציה`` לכל ``.commit()``."""
    bare: set[str] = set()
    wrapped: set[str] = set()
    for path in sorted(SERVER.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # כל צומת שיושב בתוך ``with ... writing(...)``
        inside: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and any(
                    "writing" in ast.dump(item) for item in node.items):
                inside.update(id(d) for d in ast.walk(node))
        # מיפוי צומת → הפונקציה שמכילה אותו.
        #
        # ‏``ast.walk`` הוא BFS: הפונקציה החיצונית מגיעה ראשונה. עם
        # ‏``setdefault`` היא הייתה תופסת את כל הצמתים, ו-`add_device`
        # היה מדווח כ-`create_net_router` — כלומר השם שמופיע בכשל אינו
        # המקום שצריך לתקן. **דריסה** נותנת לפנימית לנצח.
        owner: dict[int, str] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in ast.walk(fn):
                    owner[id(d)] = fn.name
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "commit"):
                site = f"{path.name}:{owner.get(id(node), '<module>')}"
                (wrapped if id(node) in inside else bare).add(site)
    return bare, wrapped


def test_the_scanner_actually_runs():
    """עיקרון 5 על הסורק עצמו.

    אם הוא אינו מוצא את האתרים שאנחנו **יודעים** שחשופים, הוא שבור —
    ואז "אין הפרות" אינו ראיה לכלום.
    """
    bare, _ = _scan()
    missing = MUST_BE_FOUND_BARE - bare
    assert not missing, (
        f"הסורק אינו מוצא אתרים חשופים ידועים: {sorted(missing)} — "
        "כלומר הוא אינו רץ, ו'אין הפרות' חסר משמעות")


def test_no_new_bare_commit_appears_in_the_server():
    """‏``.commit()`` חדש מחוץ ל-``writing()`` נופל כאן, אוטומטית.

    זה מה שמחליף את רשימת ארבעת האתרים: אתר חדש אינו דורש שמישהו
    יזכור לעדכן טסט.
    """
    bare, _ = _scan()
    unknown = bare - BOOTSTRAP - STILL_BARE
    assert not unknown, (
        "‏.commit() חדש מחוץ ל-`with _write_lock, writing(conn)`:\n  "
        + "\n  ".join(sorted(unknown))
        + "\n\nכתיבה שנכשלה בלי rollback משאירה נעילה יתומה, וכל כתיבה"
          "\nמאותו תהליכון נכשלת מיד עד אתחול השרת (#54)."
          "\n\n**אל תוסיף את השם ל-STILL_BARE** — זו הרשימה שרק מתכווצת.")


def test_the_debt_list_only_shrinks():
    """‏``STILL_BARE`` אינה רשאית לגדול, ואינה רשאית להחזיק שמות מתים.

    שם שכבר עטוף ונשאר ברשימה מסתיר את ההתקדמות; שם שנעלם מהקוד
    מסתיר קובץ שנמחק. שניהם הופכים את הרשימה למספר שאינו נמדד.
    """
    bare, _ = _scan()
    stale = STILL_BARE - bare
    assert not stale, (
        f"‏STILL_BARE מחזיקה שמות שאינם חשופים יותר: {sorted(stale)} — "
        "להסיר אותם, כדי שהמספר ימשיך למדוד משהו")
    assert len(STILL_BARE) <= 21, (
        f"‏STILL_BARE גדלה ל-{len(STILL_BARE)} — היא נמדדה כ-21 ב-07/09 "
        "והיא רשאית רק להתכווץ")
