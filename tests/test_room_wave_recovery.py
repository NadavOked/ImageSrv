"""‏#217 — הסבב אינו נשאר תלוי כשהגל שלו נעלם מתחתיו.

‏`tick` הכיר בשני מצבי גל בלבד, `open` ו-`running`, וכל מסלול נסיגה
ב-`_finish_wave` יצא **בלי לגעת ב-`room_rounds`**. מכאן מצב יציב אחד
שאין ממנו יציאה: הסבב `active`, ‏`wave_session_id` מצביע על סשן
**סגור**, וכל דופק עתידי נוסג באותה נקודה בדיוק. החדר נראה פתוח, אף גל
לא נפתח, ‏`open_round` מסרב ("כבר יש סבב חדר פעיל"), והדרך היחידה החוצה
היא שהמפעיל יסגור את הסבב ביד — אם יבין שזה מה שקרה.

**שלוש דרכים נמדדות להגיע לשם**, וכל אחת היא טסט כאן:

1. **בלי שום תקלה** — סגירת הגל מ-endpoint הסבבים הכללי של הקונסולה.
   ‏`close_session` אינו יודע שהסשן הזה הוא גל של חדר, והסבב נשאר פעיל.
2. **כשל בין סגירת הגל לכתיבת הסבב** (מסלול "היעד הושג"): ‏`store.close`
   כבר בוצע לו commit, וה-UPDATE של `room_rounds` נופל. זה בדיוק
   "המנצח מת אחרי ה-commit של sessions ולפני כתיבת הסבב" מגוף ה-Issue,
   ו-`pulse` מבטיח שהשרת ימשיך לרוץ אחריו.
3. **יורש יתום** (מסלול "היעד לא הושג"): הגל הבא כבר נפתח ותפס את
   החריץ, והכתיבה שמצביעה עליו נופלת. כאן אסור לפתוח גל **שלישי** —
   הריפוי הוא לאמץ את הקיים.

טסט רביעי שומר על ההכרעה שהריפוי דורש: כשחריץ המולטיקאסט תפוס בידי
סבב אחר, החדר **ממתין** — ‏`room_wave_lost` נרשם פעם אחת ולא בכל
‏hello, ואין `room_tick_failed` בכלל. שקט מוחלט היה מסתיר את המצב;
שורה לכל דופק הייתה מכסה עליו באותה מידה.

שאר הטסטים בקובץ הם שני הממצאים האחרים של #217, ששניהם **הופרכו**
במדידה ולא בתיקון — ולכן הם נשארים כאן כשומרי רגרסיה על מה שנמדד.
"""

from __future__ import annotations

import sqlite3
import sys

import pytest

from server.rounds import room, sessions

from conftest import room_slots
from test_server_room import (          # noqa: F401 — ‏room_server הוא fixture
    CLONER1, CLONER2, cloner_hello, report, room_server,
)

pytest.importorskip("fastapi")

IMAGE = "img_7f3a91"
#: האישור בהקלדה שסגירת סבב דורשת (#581) — שם האימג' שהגל משדר.
CONFIRM = {"confirm_name": "Office 2024 Standard"}


# --- עזרים -------------------------------------------------------------------


def _round(ctx) -> sqlite3.Row | None:
    return ctx.conn.execute("SELECT * FROM room_rounds").fetchone()


def _actives(ctx) -> list[sqlite3.Row]:
    return ctx.conn.execute(
        "SELECT id, state FROM sessions WHERE kind = 'multicast'"
        " AND state IN ('open', 'running')").fetchall()


def _events(ctx, event: str) -> int:
    return ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = ?", (event,)
    ).fetchone()["n"]


def _open_round(room_server, target: int, drawers=None) -> None:
    """‏#1410: הסבב נפתח על בחירה מפורשת — כאן כל מגירות שני המשכפלים."""
    drawers = drawers or {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}
    opened = room_server["deploy"].post("/api/console/room", json={
        "image_id": IMAGE, "target_drives": target,
        "target_slots": room_slots(room_server["ctx"].conn, drawers)})
    assert opened.status_code == 200, opened.text


def _wave_done(room_server, target: int) -> str:
    """גל שכל חבריו דיווחו סיום על ארבע מגירות — הדופק הבא מסיים אותו."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    _open_round(room_server, target)
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    if room_server["ctx"].conn.execute(
            "SELECT state FROM sessions WHERE id = ?", (wave,)
    ).fetchone()["state"] == "open":
        started = deploy.post("/api/console/room/start")
        assert started.status_code == 200, started.text
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})
    report(anon, wave, CLONER2, {"sda": "done", "sdb": "done"})
    return wave


class _Boom:
    """מפיל משפט מסומן פעם אחת — התקלה שמפרידה בין שתי הכתיבות.

    ‏`ctx.conn` הוא `db.Database`, שאותו חולקים כל התהליכונים של
    ‏`TestClient`; החלפת ה-`execute` שלו היא הדרך היחידה להגיע לכתיבה
    שקורית **בתוך** בקשה.
    """

    def __init__(self, conn, marker: str):
        # המתודה **הכרוכה**, ולא האובייקט: אחרי ה-monkeypatch חיפוש של
        # `conn.execute` היה מחזיר את העוטף הזה עצמו — רקורסיה אינסופית.
        self._execute = conn.execute
        self._marker = marker
        self.armed = True
        self.fired = 0

    def __call__(self, sql: str, parameters=()):
        if self.armed and self._marker in " ".join(sql.split()):
            self.fired += 1
            self.armed = False
            raise sqlite3.OperationalError("database is locked")
        return self._execute(sql, parameters)


# --- ‏1: הגל נסגר מבחוץ -------------------------------------------------------


def test_a_wave_closed_from_the_console_does_not_strand_the_round(room_server):
    """בלי שום תקלה מוזרקת: סגירת הגל דרך endpoint הסבבים הכללי.

    ‏`close_session` בקונסולה מקבל **כל** מזהה סבב, וגל חדר הוא סבב
    רגיל לכל דבר. אחריו הסבב פעיל, הגל סגור, ואף דופק אינו מתקדם.
    """
    anon, ctx = room_server["anon"], room_server["ctx"]
    _open_round(room_server, target=6)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]

    closed = room_server["deploy"].post(f"/api/console/sessions/{wave1}/close", json=CONFIRM)
    assert closed.status_code == 200, closed.text
    assert not _actives(ctx), "הגל לא נסגר — הטסט אינו בודק את מה שהוא מתאר"

    cloner_hello(anon, CLONER1, ["S1", "S2"])          # דופק אחד

    round_row = _round(ctx)
    assert round_row["state"] == "active", "הסבב נסגר מעצמו — לא זה מה שסוכם"
    active = _actives(ctx)
    assert len(active) == 1, \
        f"החדר נשאר עם {len(active)} גלים — כל tick נוסג באותה נקודה"
    assert round_row["wave_session_id"] == active[0]["id"] != wave1
    assert round_row["wave_number"] == 2
    assert _events(ctx, "room_tick_failed") == 0


# --- ‏2: כשל בין סגירת הגל לכתיבת הסבב ----------------------------------------


def test_a_failure_after_closing_the_wave_is_recovered_by_the_next_pulse(
        room_server, monkeypatch):
    """מסלול "היעד הושג": הסבב חייב להיסגר, גם אם הכתיבה נפלה פעם אחת.

    ‏`pulse` מבטיח ש-hello ימשיך לענות 200 והכישלון יירשם ביומן — ומה
    ש-#217 מוסיף הוא שהדופק **הבא** מסיים את מה שנקטע.
    """
    anon, ctx = room_server["anon"], room_server["ctx"]
    boom = _Boom(ctx.conn, "UPDATE room_rounds SET written_drives")
    wave1 = _wave_done(room_server, target=4)
    monkeypatch.setattr(ctx.conn, "execute", boom)

    cloner_hello(anon, CLONER1, ["S1", "S2"])          # הדופק שנקטע
    assert boom.fired == 1, "התקלה לא נורתה — הטסט לא בדק דבר"
    assert _events(ctx, "room_tick_failed") == 1, "הכישלון נבלע"
    assert ctx.conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (wave1,)
    ).fetchone()["state"] == "closed", "הגל לא נסגר — אין מה לשחזר"

    cloner_hello(anon, CLONER2, ["S3", "S4"])          # הדופק שמשחזר

    round_row = _round(ctx)
    assert round_row["state"] == "closed", "הסבב נשאר תלוי על גל סגור"
    assert round_row["written_drives"] == 4
    assert not _actives(ctx), "נפתח גל אף שהיעד הושג"
    assert _events(ctx, "room_done") == 1, "הסבב נסגר ביומן פעמיים או בכלל לא"


# --- ‏3: יורש יתום ------------------------------------------------------------


def test_a_successor_wave_is_adopted_and_not_duplicated(room_server, monkeypatch):
    """מסלול "היעד לא הושג": הגל הבא נפתח, והכתיבה שמצביעה עליו נפלה.

    היורש כבר מחזיק את חריץ המולטיקאסט היחיד. פתיחת גל שלישי הייתה
    נכשלת ב-`TAKEN` בכל דופק מכאן והלאה, ולכן הריפוי הוא **אימוץ**.
    """
    anon, ctx = room_server["anon"], room_server["ctx"]
    boom = _Boom(ctx.conn, "UPDATE room_rounds SET written_drives")
    wave1 = _wave_done(room_server, target=6)
    monkeypatch.setattr(ctx.conn, "execute", boom)

    # דופק לא-מצטרף: ‏hello **מצטרף** מריץ דופק שני אחרי ההצטרפות (#1703),
    # והוא כבר היה מאמץ את היורש באותה בקשה — כאן נבדק הדופק הבא בנפרד.
    cloner_hello(anon, CLONER1, ["S1", "S2"], joining=False)   # הדופק שנקטע
    assert boom.fired == 1, "התקלה לא נורתה — הטסט לא בדק דבר"
    orphan = _actives(ctx)
    assert len(orphan) == 1 and orphan[0]["id"] != wave1, \
        "היורש לא נפתח — זה אינו התרחיש שהטסט מתאר"
    assert _round(ctx)["wave_session_id"] == wave1, "הסבב כבר עודכן"

    cloner_hello(anon, CLONER2, ["S3", "S4"])          # הדופק שמשחזר

    active = _actives(ctx)
    assert len(active) == 1, f"נפתחו {len(active)} גלים — היורש לא אומץ"
    assert active[0]["id"] == orphan[0]["id"], "נפתח גל חדש במקום היורש הקיים"
    round_row = _round(ctx)
    assert round_row["state"] == "active"
    assert round_row["wave_session_id"] == orphan[0]["id"]
    assert round_row["written_drives"] == 4
    assert round_row["wave_number"] == 2, "מספר הגל קפץ פעמיים"


# --- כשהחריץ תפוס: לא נפתח גל, ולא נוצר רעש ----------------------------------


def test_a_taken_slot_leaves_the_round_waiting_without_flooding_the_journal(
        room_server):
    """אין חריץ פנוי לגל חדש — החדר ממתין, ולא צועק בכל hello.

    זו ההכרעה שהריפוי דורש: כישלון פתיחה **אינו** חריגה שתגיע לכל
    ‏hello כ-`room_tick_failed` (רעש שמכסה על השורה שכן אומרת משהו),
    אבל גם אינו שקט — ‏`room_wave_lost` נרשם **פעם אחת**, כי התביעה
    שמאפסת את המצביע מצליחה פעם אחת. וכשהחריץ מתפנה, הדופק הבא פותח.
    """
    anon, ctx, deploy = room_server["anon"], room_server["ctx"], room_server["deploy"]
    _open_round(room_server, target=6)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    closed = deploy.post(f"/api/console/sessions/{wave1}/close", json=CONFIRM)
    assert closed.status_code == 200, closed.text

    # מישהו אחר תפס את חריץ המולטיקאסט היחיד בין לבין.
    other = ctx.store.open("grp_BUILD", IMAGE, "BUILD", 1, "noc")

    for _ in range(3):
        cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert _events(ctx, "room_tick_failed") == 0, "כשל הפתיחה הפך לרעש בכל דופק"
    assert _events(ctx, "room_wave_lost") == 1, \
        "אבדן הגל לא נרשם פעם אחת בדיוק — שקט מוחלט או שורה לכל hello"
    assert _round(ctx)["wave_session_id"] is None
    view = deploy.get("/api/console/room")
    assert view.status_code == 200, view.text
    assert view.json()["round"]["wave_state"] == "closed"

    ctx.store.close(other, "noc")                  # החריץ התפנה
    cloner_hello(anon, CLONER1, ["S1", "S2"])

    active = _actives(ctx)
    assert len(active) == 1, "הגל לא נפתח אחרי שהחריץ התפנה"
    assert _round(ctx)["wave_session_id"] == active[0]["id"]
    assert _round(ctx)["wave_number"] == 2


# --- כשההצמדה מפסידה: שני הכיוונים -------------------------------------------


def _lose_the_attach(monkeypatch, ctx, meddle) -> dict:
    """מפיל את התביעה של `_attach_wave` — ‏`meddle` הוא "התהליכון האחר".

    ‏`meddle(conn, wave_id, round_id)` רץ **אחרי** שהגל כבר נפתח ולפני
    ה-UPDATE המותנה, ולכן ה-UPDATE אינו תואם שורה. ככה נפתח החלון ביד,
    ולא בתקווה למתזמן.
    """
    real = room.update_one
    seen: dict = {}

    def spy(conn, sql: str, parameters=()):
        if "wave_number = wave_number + 1" in " ".join(sql.split()):
            seen["wave_id"] = parameters[0]
            meddle(conn, parameters[0], parameters[1])
        return real(conn, sql, parameters)

    monkeypatch.setattr(room, "update_one", spy)
    return seen


def test_a_wave_adopted_by_the_winner_is_not_closed_by_the_loser(
        room_server, monkeypatch):
    """המפסיד לא סוגר את הגל שהמנצח **אימץ** — וזה בדיוק הגל שהוא פתח.

    יש חריץ מולטיקאסט אחד בלבד (אינדקס ייחודי), ולכן תהליכון שמגיע
    ל-`_attach_wave` בזמן שהגל שלנו כבר פתוח **אינו יכול** לפתוח אחר:
    הוא מאמץ את שלנו. סגירה עיוורת של "מה שאנחנו פתחנו" הייתה מחזירה
    את החדר בדיוק למצב שממנו באנו.
    """
    anon, ctx, deploy = room_server["anon"], room_server["ctx"], room_server["deploy"]
    _open_round(room_server, target=6)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    closed = deploy.post(f"/api/console/sessions/{wave1}/close", json=CONFIRM)
    assert closed.status_code == 200, closed.text

    def adopt(conn, wave_id, round_id):
        conn.execute("UPDATE room_rounds SET wave_session_id = ? WHERE id = ?",
                     (wave_id, round_id))
        conn.commit()

    seen = _lose_the_attach(monkeypatch, ctx, adopt)
    cloner_hello(anon, CLONER1, ["S1", "S2"])

    assert seen.get("wave_id"), "ההצמדה לא רצה — הטסט לא בדק דבר"
    active = _actives(ctx)
    assert len(active) == 1 and active[0]["id"] == seen["wave_id"], \
        "הגל שהמנצח אימץ נסגר על ידי המפסיד — החדר חזר להיות בלי גל"
    assert _round(ctx)["wave_session_id"] == seen["wave_id"]


def test_a_wave_nobody_points_at_is_not_left_holding_the_slot(
        room_server, monkeypatch):
    """הכיוון השני: הסבב נסגר בינתיים, ואת הגל שפתחנו כן צריך לסגור.

    גל פתוח שאיש אינו מצביע עליו מחזיק את החריץ היחיד לנצח, וכל סבב
    כיתה הבא היה נדחה ב-`TAKEN` בלי שיש מה לראות על המסך.
    """
    anon, ctx, deploy = room_server["anon"], room_server["ctx"], room_server["deploy"]
    _open_round(room_server, target=6)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    closed = deploy.post(f"/api/console/sessions/{wave1}/close", json=CONFIRM)
    assert closed.status_code == 200, closed.text

    def close_the_round(conn, _wave_id, round_id):
        conn.execute("UPDATE room_rounds SET state = 'closed', closed_at = ?"
                     " WHERE id = ?", ("2026-09-02T00:00:00+00:00", round_id))
        conn.commit()

    seen = _lose_the_attach(monkeypatch, ctx, close_the_round)
    cloner_hello(anon, CLONER1, ["S1", "S2"])

    assert seen.get("wave_id"), "ההצמדה לא רצה — הטסט לא בדק דבר"
    assert not _actives(ctx), "הגל שאיש אינו מצביע עליו נשאר מחזיק את החריץ"


# --- ממצא ‏2 ב-#217: `on_closed` והיורש (הופרך) --------------------------------


def test_the_successor_wave_is_never_broadcasting_when_on_closed_fires(
        room_server, monkeypatch):
    """‏`SenderEngine.stop` מתעלם ממזהה הסבב — ולכן נמדד **מי** יכול
    להיות משדר ברגע ש-`on_closed(replaces)` רץ.

    הממצא היה שהקריאה רצה אחרי שהיורש כבר קיים, ולכן היא עלולה לעצור
    את שולח הגל החדש. מה שנמדד: היורש נולד `open`, והמסלול היחיד
    שמעביר גל חדר ל-`running` הוא `room.tick` — שקורא את מצביע הגל
    מ-`room_rounds`, והוא עדיין מצביע על **הישן** באותו רגע.
    ‏`maybe_start` (מסלול ה-hello) מחריג קבוצת `cloner` במפורש ולעולם
    אינו מתניע גל חדר. אין רגע שבו היורש משדר לפני ה-`stop`.

    זה שומר רגרסיה, לא תיקון: ברגע שמישהו יתניע גל חדר ממסלול אחר,
    הטסט הזה ייפול.
    """
    ctx = room_server["ctx"]
    seen: list[dict] = []
    real = ctx.store.on_closed

    def spy(session_id: str) -> None:
        seen.append({
            "closing": session_id,
            "actives": [(r["id"], r["state"]) for r in _actives(ctx)],
            "sender": ctx.sender.status(),
        })
        real(session_id)

    monkeypatch.setattr(ctx.store, "on_closed", spy)
    wave1 = _wave_done(room_server, target=6)
    cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])   # מסיים ופותח

    assert len(seen) == 1, f"`on_closed` רץ {len(seen)} פעמים"
    moment = seen[0]
    assert moment["closing"] == wave1
    assert len(moment["actives"]) == 1, "יותר משידור פעיל אחד ברגע הסגירה"
    successor, state = moment["actives"][0]
    assert successor != wave1
    assert state == "open", \
        f"היורש כבר {state!r} כשה-stop נורה — הוא עלול להיעצר"
    assert moment["sender"] is None or moment["sender"]["session_id"] == wave1, \
        "המשדר שנעצר אינו של הגל הישן"


def test_hello_never_starts_a_room_wave(room_server):
    """הצלע השנייה של אותה מדידה: מסלול ה-hello אינו מתניע גל חדר.

    ‏`maybe_start` הוא מה ש-hello, מסך התחנה ומבט-העל של הקונסולה
    מריצים על הסבב הפעיל, והוא **חוזר כמו שהוא** על קבוצת `cloner` —
    גם כשהמצטרפים מכסים את המספר שהוצהר וגם כשהטיימר פקע.
    """
    ctx = room_server["ctx"]
    # ‏#1703: hello שמכסה את היעד מוציא את הגל בעצמו (`room.tick`, לא
    # `maybe_start`) — לכן כאן CLONER2 עוד בדרך, והגל נשאר פתוח.
    _open_round(room_server, target=4)
    cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    wave = ctx.store.active()
    assert wave is not None and wave["state"] == "open"

    ctx.conn.execute("UPDATE sessions SET expected_clients = 1, last_join_at = 0"
                     " WHERE id = ?", (wave["id"],))
    ctx.conn.commit()
    wave = ctx.conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (wave["id"],)).fetchone()
    assert ctx.store.starts_in_seconds(wave) == 0, "הטיימר לא פקע — אין מה לבדוק"
    assert ctx.store.joined_count(wave["id"]) >= wave["expected_clients"]

    after = ctx.store.maybe_start(wave)
    assert after["state"] == "open", "מסלול ה-hello התניע גל של חדר השיכפולים"


# --- ממצא ‏3 ב-#217: ה-rollback של `update_one` (הופרך) ------------------------


def _in_transaction(conn) -> bool:
    """‏`db.Database` מתחזה לחיבור; הטרנזאקציה יושבת על החיבור שמתחתיו."""
    return getattr(conn, "connection", conn).in_transaction


def test_no_rollback_in_the_room_path_can_discard_an_earlier_write(
        room_server, monkeypatch):
    """‏`update_one` עושה `rollback` כשה-UPDATE לא תאם שורה — והשאלה
    היחידה היא **מה עוד** יושב באותה טרנזאקציה.

    התשובה נמדדת ולא מונחת: כל קריאה ל-`update_one` שמגיעה ממסלולי
    ה-hello, ה-tick והקונסולה נכנסת כשהחיבור ב-**autocommit** — כלומר
    אין שום כתיבה קודמת להפסיד. היוצא היחיד הוא הקריאה מתוך
    ‏`SessionStore.open`, ושם הטרנזאקציה נפתחה במשפט שלפניה
    (`BEGIN IMMEDIATE`) ואין בה עוד דבר — נסיגה מלאה שלה היא בדיוק
    ההתנהגות הרצויה, והיא מה ש-#200 מודד.

    שומר הרגרסיה: ברגע שמישהו יוסיף כתיבה לא-מקומטת לפני `close`,
    הטסט הזה ייפול.
    """
    seen: list[tuple[str, bool]] = []
    real = sessions.update_one

    def spy(conn, sql: str, parameters=()):
        seen.append((sys._getframe(1).f_code.co_name, _in_transaction(conn)))
        return real(conn, sql, parameters)

    monkeypatch.setattr(sessions, "update_one", spy)
    monkeypatch.setattr(room, "update_one", spy, raising=False)

    anon, ctx = room_server["anon"], room_server["ctx"]
    wave1 = _wave_done(room_server, target=6)
    cloner_hello(anon, CLONER1, ["S1", "S2"])      # סיום גל ופתיחת הבא
    # קודם עושים, ואז מאמתים: עם `-O` ה-assert נעלם ואיתו הפעולה עצמה
    # (‏`py/side-effect-in-assert`), והסגירה שלא תאמה שורה היא **הקריאה**
    # שהטסט הזה קיים כדי למדוד.
    closed_again = ctx.store.close(wave1, "noc")
    assert closed_again is False

    callers = {name for name, _ in seen}
    assert "close" in callers and "open" in callers, \
        f"המסלולים שנמדדים לא נכנסו לתמונה: {callers}"
    inside = {name for name, in_tx in seen if in_tx}
    assert inside <= {"open"}, \
        f"קריאה ל-update_one בתוך טרנזאקציה פתוחה שאינה של open: {inside}"
