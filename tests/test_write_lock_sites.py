"""‏#313 — שלושת רצפי הכתיבות שנשארו, ועוד `db.set_setting` עצמו.

‏#272 מצא את שני הבאגים ותיקן אותם ב-`net_seen`; ‏#356 החיל אותם על
מסלול ה-hello (`agent_loops.note`, ‏`bootguard.guard`). מה שנשאר הוא
מסלול ה**קונסולה**, ואותה צורה בדיוק — ``conn.execute(...)`` ואחריו
``conn.commit()``, בלי ``_write_lock`` ובלי ``writing``:

1. **כתיבה שנכשלה מרעילה את החיבור.** ‏pysqlite מוציא ``BEGIN`` לפני
   כל DML. אם ה-DML נכשל ב-``database is locked`` ואיש אינו עושה
   ``rollback``, החיבור נשאר **בתוך טרנזאקציה** — ומשם sqlite אינו
   מפעיל את ה-busy handler לשדרוג נעילה, וכל כתיבה נכשלת **מיד**, גם
   כשהמחזיק שחרר חצי שנייה אחר כך. תהליכון של uvicorn ממוחזר, ולכן
   אירוע עומס **חולף אחד** משבית את הכתיבה של אותו תהליכון עד אתחול
   השרת. באתרים שכאן זה אומר קונסולה שמפסיקה לשמור: המפעיל לוחץ
   "שמור", מקבל שגיאה, מנסה שוב — וזה נכשל שוב, מיד, לתמיד.
2. **כותבים בתוך התהליך מרעיבים זה את זה.** ‏sqlite אינו מבטיח
   הוגנות: הממתין ישן ב-busy handler ובזמן השינה האחרים כותבים שוב.
   ``_write_lock`` הוא התור ההוגן בתוך התהליך.

ארבעת האתרים נבדקים כאן בנפרד — ‏`users`, ‏`console_api`, ‏`registry`,
ו-`db.set_setting` — כי כל אחד מהם רץ ממסלול אחר וכשל באחד אינו מעיד
על השאר.

הכשל נבנה **דטרמיניסטית** ולא נתלה בדיסק איטי: מחזיק חיצוני תופס את
נעילת הכתיבה, ו-``busy_timeout`` של הקורבן מוקטן. הכלים לכך נבנו
ב-#356 ומיובאים משם כמות שהם — שני עותקים של אותו פיגום תחרות היו
נסחפים זה מזה, וזה בדיוק סוג הפיגום שסחיפה בו אינה נראית.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from server.agent import registry
from server.console import users
from server.console.console_api import create_console_router
from server.db import _open, connect, get_setting, set_setting

from test_hello_write_lock import _hold_briefly, _poison, _starve

#: המשתמש שה-endpoint מקבל מ-`admin_only`. הבדיקות כאן עוקפות את ה-DI
#: של FastAPI במכוון: הנבדק הוא רצף הכתיבות, לא ההרשאות.
ADMIN = ("noc", "admin")

#: קבוצה שנוצרת עם הסכימה (`db.FIXED_GROUPS`) — לא צריך לייצר אותה.
GROUP = "grp_CLONERS"


class _Body:
    """‏Request מזויף ל-endpoint שקורא ``await request.json()``."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


def _console(conn) -> dict:
    """ה-endpoints של הקונסולה, לפי שם, מעל חיבור נתון."""
    router = create_console_router(SimpleNamespace(conn=conn))
    return {route.endpoint.__name__: route.endpoint for route in router.routes}


# --- users -------------------------------------------------------------------


def test_a_failed_user_create_leaves_the_connection_usable(tmp_path):
    """יצירת משתמש שנכשלה אינה רשאית לנעול את ניהול המשתמשים לתמיד.

    הראיה חיובית ולא היעדר-חריגה: אחרי כשל אחד, **אותו חיבור** יוצר
    משתמש תחת עומס חולף, והרשומה נקראת בחזרה.
    """
    path = tmp_path / "t.db"
    connect(path)
    victim, holder = _poison(path)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        users.create(victim, "first", "password-12", "admin", by="test", is_builtin=True, check_policy=False)
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        users.create(victim, "second", "password-12", "deploy", by="test", check_policy=False)
    finally:
        brief.join(timeout=30)

    row = victim.execute(
        "SELECT role FROM users WHERE username = ?", ("second",)).fetchone()
    assert row is not None and row["role"] == "deploy"
    victim.close()


def test_user_writes_do_not_starve_each_other(tmp_path):
    """כמה מסכי ניהול פתוחים יחד — וכל משתמש שנוצר באמת נוצר.

    הראיה חיובית: כל תשע הרשומות נמצאות. ‏`create` אינו בולע חריגות,
    אבל מה שנספר הוא מה שנכתב ולא מה שלא נזרק.
    """
    db = connect(tmp_path / "t.db")
    failures = _starve(
        db, lambda conn, i, n: users.create(
            conn, f"u{i}{n}", "password-12", "deploy", by="test",
            check_policy=False))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    written = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    assert written == 9


# --- console_api -------------------------------------------------------------


def test_a_failed_group_reorder_leaves_the_connection_usable(tmp_path):
    """סדר הקבוצות הוא **רצף** כתיבות — כשל באמצע משאיר נעילה יתומה.

    הגרירה בקונסולה כותבת שורה לכל קבוצה. אם השנייה נכשלת ואיש אינו
    עושה ``rollback``, הנעילה של הראשונה נשארת, והחיבור מורעל.
    """
    path = tmp_path / "t.db"
    connect(path)
    victim, holder = _poison(path)
    reorder = _console(victim)["reorder_groups"]
    ids = ["grp_BUILD", GROUP]

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        asyncio.run(reorder(_Body({"ids": ids}), user=ADMIN))
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        assert asyncio.run(reorder(_Body({"ids": ids}), user=ADMIN)) == {"ok": True}
    finally:
        brief.join(timeout=30)

    order = [r["id"] for r in victim.execute(
        "SELECT id FROM groups ORDER BY sort")]
    assert order == ids
    victim.close()


def test_console_deletes_do_not_starve_each_other(tmp_path):
    """מחיקת מכונות מכמה מסכים יחד — וכל מחיקה שהוחזר עליה ok באמת קרתה."""
    db = connect(tmp_path / "t.db")
    macs = [f"aa:bb:cc:{i:02x}:00:{n:02x}" for i in range(3) for n in range(3)]
    for index, mac in enumerate(macs):
        db.execute(
            "INSERT INTO machines (mac, suffix, group_id, added_at)"
            " VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00')",
            (mac, f"pc{index}", GROUP),
        )
    db.commit()
    delete = _console(db)["del_machine"]

    failures = _starve(
        db, lambda conn, i, n: delete(f"aa:bb:cc:{i:02x}:00:{n:02x}", user=ADMIN))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    left = db.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"]
    assert left == 0


# --- registry ----------------------------------------------------------------


def test_a_failed_mac_import_leaves_the_connection_usable(tmp_path):
    """הייבוא כותב שורה למכונה — רצף באורך הרשימה, ובדיוק אותו דפוס."""
    path = tmp_path / "t.db"
    connect(path)
    victim, holder = _poison(path)
    lines = registry.parse_paste(
        "aa:bb:cc:dd:ee:01 pc-one\naa:bb:cc:dd:ee:02 pc-two\n", "cloner")
    assert [item.error for item in lines] == [None, None]

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        registry.import_lines(victim, GROUP, lines, "test")
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        saved, rejected = registry.import_lines(victim, GROUP, lines, "test")
    finally:
        brief.join(timeout=30)

    assert (saved, rejected) == (2, [])
    written = victim.execute(
        "SELECT COUNT(*) AS n FROM machines").fetchone()["n"]
    assert written == 2
    victim.close()


def test_machine_writes_do_not_starve_each_other(tmp_path):
    """כיתה שלמה נרשמת ידנית משני מסכים — וכל מכונה נכנסת לטבלה."""
    db = connect(tmp_path / "t.db")
    failures = _starve(
        db, lambda conn, i, n: registry.add_machine(
            conn, f"aa:bb:cc:{i:02x}:00:{n:02x}", f"pc-{i}-{n}", GROUP, "test"))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    written = db.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"]
    assert written == 9


# --- db.set_setting ----------------------------------------------------------


def test_a_failed_set_setting_leaves_the_connection_usable(tmp_path):
    """‏`set_setting` הוא מסלול הכתיבה של **כל** מסכי ההגדרות.

    ‏DHCP, כתובות, מתגים ורשימת התיקיות עוברים כאן. חיבור מורעל
    כאן הוא קונסולה שהפסיקה לשמור — ולפי עיקרון 5, "שמירה שלא הצליחה
    להיבדק" אינה "נשמר".
    """
    path = tmp_path / "t.db"
    connect(path)
    victim, holder = _poison(path)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        set_setting(victim, "console_idle_seconds", "111")
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        set_setting(victim, "console_idle_seconds", "222")
    finally:
        brief.join(timeout=30)

    assert get_setting(victim, "console_idle_seconds") == "222"
    victim.close()


def test_settings_writes_do_not_starve_each_other(tmp_path):
    """מסך הגדרות שומר כמה מפתחות, ומסך אחר שומר במקביל — הכול נשמר."""
    db = connect(tmp_path / "t.db")
    failures = _starve(
        db, lambda conn, i, n: set_setting(conn, f"k{i}{n}", f"v{i}{n}"))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    missing = [f"k{i}{n}" for i in range(3) for n in range(3)
               if get_setting(db, f"k{i}{n}") != f"v{i}{n}"]
    assert not missing, f"הגדרות שלא נשמרו: {missing}"


def test_the_write_lock_is_not_reentrant(tmp_path):
    """שומר על ההנחה שכל התיקון הזה נשען עליה.

    ‏`_write_lock` הוא ``Lock`` ולא ``RLock``, ולכן כל קינון — למשל
    ‏`journal` שייקרא **בתוך** בלוק כתיבה במקום אחריו — נתקע לנצח ולא
    נכשל. תקיעה אינה נראית ככשל, והיא נראית כמו שרת עמוס.
    """
    from server.db import _write_lock

    with _write_lock:
        # משחררים **בתוך** הבלוק: אם זה כן RLock, טענה שנכשלת אחרי
        # רכישה כפולה הייתה משאירה את הנעילה תפוסה ותוקעת את כל החבילה.
        reentered = _write_lock.acquire(timeout=0.05)
        if reentered:
            _write_lock.release()
    assert reentered is False, (
        "‏_write_lock הפך ל-RLock — קינון כבר לא ייתקע, "
        "וההנחה שהתיקון נשען עליה אינה נכונה יותר")
