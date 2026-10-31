"""‏#177 — שני דופקים בו-זמנית בסוף גל בחדר השיכפולים.

מחשבי השיכפול דוגמים כל הזמן, ו-`hello` שלהם הוא שמקדם את הסבב. בסוף
גל שכולם דיווחו בו, שני hello שנכנסו יחד הם **שני `room.tick`** — וזה
בדיוק מה שהחדר קיים בשבילו: כמה מכונות מסיימות באותו רגע.

הקוד שלפני התיקון עשה `close` ואז `open` כשתי כתיבות נפרדות. בין
השתיים חריץ המולטיקאסט פנוי, ולכן שניהם סגרו ושניהם ניסו לפתוח: השני
נדחה על האינדקס הייחודי (#103), והחריגה ברחה מ-`tick` דרך מסלול ה-hello
עד 500 — כלומר "השרת מת", שקר.

**דיוק שנמדד ולא הונח:** בגוף #177 נכתב שגם "הגל הבא לא נפתח". במסלול
"היעד לא הושג" זה **אינו** מה שקורה, ונמדד כאן: הפותח שניצח פותח את
הגל, ו-`wave_number` עולה ב-1 בדיוק — גם על הקוד שלפני התיקון. לכן
האסרשנים על מספר הגל ועל השידור הפעיל הם **שומרי רגרסיה** ולא הבקרה
השלילית; הבקרה היא ה-500. מה שכן נשבר בקוד הישן מעבר ל-500 הוא מסלול
**"היעד הושג"**: שני דופקים סוגרים את הסבב פעמיים וכותבים `room_done`
כפול, וזה הטסט השני כאן.

החלון נפתח כאן ביד ולא בתקווה למתזמן: התהליכון הראשון נעצר בתוך
`SessionStore.members` — אחרי שקרא את הסבב ואת חבריו ולפני שהוא מסיק
מהם שהגל נגמר — והשני מספיק לסיים את הגל ולפתוח את הבא. אז הראשון
משתחרר וממשיך עם בדיוק אותה תמונה ישנה שהייתה לו.

הטסטים כאן מפרידים בין שתי הדרישות של ה-Issue: שני הראשונים על
האטומיות (שני מסלולי `_finish_wave`), והשאר על כך ש-`pulse` אינו מפיל
את ה-hello **ואינו בולע** — עיקרון 5.
"""

from __future__ import annotations

import pytest
from conftest import hello_body

from server.rounds import room
from server.rounds.sessions import SessionStore

from test_server_room import (          # noqa: F401 — ‏room_server הוא fixture
    CLONER1, CLONER2, cloner_hello, room_body, report, room_server,
)
from test_session_claim_race import Gate, _both

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


def _cloner_body(mac: str, serials: list[str]) -> dict:
    """אותו גוף בדיוק כמו `test_server_room.cloner_hello`, בלי ה-assert:
    בתוך תהליכון צריך להחזיר את התשובה, גם כשהיא 500."""
    body = hello_body(mac)
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304,
         "model": "Drawer SSD", "serial": serial, "removable": False,
         "scheme": "gpt", "has_data": False, "port": i + 1}
        for i, serial in enumerate(serials)
    ]
    return body


def _gate_the_wave(monkeypatch, gate: Gate, wave_id: str) -> None:
    """עוצר את הקורא הראשון בתוך `SessionStore.members` על הגל הזה.

    זו הנקודה שבה `tick` כבר קרא את הסבב ואת מצב הגל, ועוד לא הכריע
    שהגל נגמר — כלומר בדיוק החלון שבו שני הדופקים חופפים.
    """
    real = SessionStore.members

    def gated(self, session_id: str):
        rows = real(self, session_id)
        if session_id == wave_id:
            gate.trip()
        return rows

    monkeypatch.setattr(SessionStore, "members", gated)


def _wave_ready_to_finish(room_server, target: int = 6) -> str:
    """סבב חדר וגל שכל חבריו דיווחו סיום — הדופק הבא הוא שמסיים אותו.

    ‏4 מגירות נכתבות. ‏`target=6` הוא מסלול "היעד לא הושג" (נפתח גל
    שני), ו-`target=4` הוא מסלול "היעד הושג" (הסבב נסגר). שני
    המסלולים נכנסים ל-`_finish_wave`, ולכל אחד מהם מרוץ משלו.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]
    # ‏`assert` אינו המקום לפעולה: עם `-O` הוא נעלם, ואיתו הבקשה עצמה
    # (‏`py/side-effect-in-assert`). קודם עושים, ואז מאמתים.
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, target, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    assert opened.status_code == 200, opened.text
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    # ‏target=4: המוכנות כיסתה את היתרה וה-tick כבר הוציא את הגל.
    # ‏target=6: היא לא כיסתה, ולכן צריך "התחל עכשיו".
    started = ctx_wave_state(room_server) == "running"
    if not started:
        started_now = deploy.post("/api/console/room/start")
        assert started_now.status_code == 200, started_now.text
    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done"})
    report(anon, wave1, CLONER2, {"sda": "done", "sdb": "done"})
    return wave1


def ctx_wave_state(room_server) -> str | None:
    """Inspect the wave directly; GET is also read-only now (#446)."""
    row = room_server["ctx"].conn.execute(
        "SELECT state FROM sessions WHERE kind = 'multicast'"
        " AND state IN ('open', 'running')").fetchone()
    return row["state"] if row else None


def _active_broadcasts(conn) -> list:
    return conn.execute(
        "SELECT id FROM sessions WHERE kind = 'multicast'"
        " AND state IN ('open', 'running')"
    ).fetchall()


def _count(conn, event: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = ?", (event,)
    ).fetchone()["n"]


def _race_two_pulses(room_server, monkeypatch, wave1: str) -> dict:
    """שני hello של מחשבי שיכפול, חופפים על אותו גל גמור.

    לקוח משלו לכל דופק: כל בקשה רצה בתהליכון אחר ולכן על חיבור sqlite
    אחר — בדיוק המצב שבו הבאג חי (#54).
    """
    gate = Gate()
    _gate_the_wave(monkeypatch, gate, wave1)
    results: dict[str, object] = {}

    def pulse_of(name: str, mac: str, serials: list[str]):
        client = TestClient(room_server["app"])

        def run() -> None:
            try:
                results[name] = client.post("/api/v1/agent/hello",
                                            json=_cloner_body(mac, serials))
            except Exception as exc:          # noqa: BLE001 — לא נבלע בתהליכון
                results[name] = exc
        return run

    _both(pulse_of("slow", CLONER1, ["S1", "S2"]),
          pulse_of("fast", CLONER2, ["S3", "S4"]), gate)
    return results


def _both_answered(results: dict) -> None:
    for name in ("fast", "slow"):
        outcome = results.get(name)
        assert outcome is not None, f"הדופק {name} לא החזיר דבר"
        assert not isinstance(outcome, Exception), \
            f"hello של {name} נפל: {outcome!r}"
        assert outcome.status_code == 200, \
            f"hello של {name} החזיר {outcome.status_code}: {outcome.text}"


def test_two_cloners_finishing_together_open_exactly_one_next_wave(
        room_server, monkeypatch):
    """שני hello בו-זמנית בסוף גל: **שניהם נענים**, ונפתח גל אחד.

    לפני התיקון המפסיד קיבל `SessionError` שברחה מ-`tick`, והבקשה
    הסתיימה ב-500. ‏`wave_number` ו-`room_wave` נבדקים כאן כשומרי
    רגרסיה — הם עברו גם על הקוד הישן (ראו ה-docstring של הקובץ).
    """
    if TestClient is None:
        pytest.skip("fastapi is required")
    ctx = room_server["ctx"]
    wave1 = _wave_ready_to_finish(room_server, target=6)

    _both_answered(_race_two_pulses(room_server, monkeypatch, wave1))

    # ראיה חיובית שהגל הבא באמת נפתח, ושהוא אחד.
    active = _active_broadcasts(ctx.conn)
    assert len(active) == 1, f"{len(active)} שידורים פעילים אחרי הגל"
    round_row = ctx.conn.execute(
        "SELECT * FROM room_rounds WHERE state = 'active'").fetchone()
    assert round_row is not None, "הסבב נסגר במקום להמשיך ליעד"
    assert round_row["written_drives"] == 4
    assert round_row["wave_number"] == 2, "מספר הגל התקדם פעמיים או בכלל לא"
    assert round_row["wave_session_id"] == active[0]["id"] != wave1
    assert _count(ctx.conn, "room_wave") == 1, "הגל הבא נרשם יותר מפעם אחת"
    assert _count(ctx.conn, "room_tick_failed") == 0


def test_two_cloners_reaching_the_target_together_close_the_round_once(
        room_server, monkeypatch):
    """המסלול השני של `_finish_wave`: היעד הושג.

    כאן אין `TAKEN` ואין 500 — שני הדופקים פשוט סגרו את אותו סבב
    ושניהם כתבו את השורה התחתונה. ‏`room_done` כפול ביומן הוא סבב
    שנגמר פעמיים במסך של המפעיל, ובראיה החיובית של `close` יש בדיוק
    סוגר אחד.
    """
    if TestClient is None:
        pytest.skip("fastapi is required")
    ctx = room_server["ctx"]
    wave1 = _wave_ready_to_finish(room_server, target=4)

    _both_answered(_race_two_pulses(room_server, monkeypatch, wave1))

    assert not _active_broadcasts(ctx.conn), "נשאר שידור פעיל אחרי סיום הסבב"
    round_row = ctx.conn.execute(
        "SELECT * FROM room_rounds").fetchone()
    assert round_row["state"] == "closed" and round_row["written_drives"] == 4
    assert _count(ctx.conn, "room_done") == 1, "הסבב נסגר פעמיים ביומן"
    assert _count(ctx.conn, "room_wave") == 0, "נפתח גל אחרי שהיעד הושג"
    assert _count(ctx.conn, "room_tick_failed") == 0


def test_a_tick_that_cannot_run_is_journaled_and_hello_still_answers(
        room_server, monkeypatch):
    """הדרישה השנייה, בפני עצמה: קידום הסבב לא מפיל את ה-hello.

    התקלה כאן מומצאת בכוונה ואינה המרוץ — מה שנבדק הוא הכלל: מחשב
    שיכפול מקבל תשובה תקינה, **ולא בשקט** — הכישלון ביומן, כדי
    שהמפעיל יראה שהסבב אינו מתקדם (עיקרון 5, ולא `except: pass`).
    """
    anon, ctx = room_server["anon"], room_server["ctx"]
    _wave_ready_to_finish(room_server)

    def boom(_conn, _store):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(room, "tick", boom)

    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])     # ‏200, לא 500
    assert answer["known"] is True

    rows = ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_tick_failed'").fetchall()
    assert rows, "כישלון בקידום הסבב נבלע — המפעיל לא רואה אותו"
    assert CLONER1 in rows[-1]["detail"] and "database is locked" in rows[-1]["detail"]


def test_the_pulse_reports_whether_it_ran(room_server, monkeypatch):
    """‏`pulse` מחזיר ראיה חיובית ולא "לא נזרקה חריגה"."""
    ctx = room_server["ctx"]
    ran = room.pulse(ctx.conn, ctx.store, CLONER1)
    assert ran is True

    def boom(_conn, _store):
        raise RuntimeError("boom")

    monkeypatch.setattr(room, "tick", boom)
    broke = room.pulse(ctx.conn, ctx.store, CLONER1)
    assert broke is False


def test_a_wave_is_closed_by_exactly_one_caller(room_server):
    """אותה ראיה, ברמת `SessionStore`: הסגירה השנייה מודה שהיא לא סגרה."""
    store = room_server["ctx"].store
    wave1 = _wave_ready_to_finish(room_server)
    first = store.close(wave1, "noc")
    second = store.close(wave1, "noc")
    assert first is True, "הסוגר הראשון לא הודה שהוא סגר"
    assert second is False, "הסגירה השנייה מדווחת שהיא סגרה סבב שכבר סגור"


def test_an_exception_message_cannot_forge_a_line_in_the_systemd_journal(
        room_server, monkeypatch, caplog):
    """שורה חדשה בהודעת חריגה לא תיראה כרשומה נפרדת ביומן ה-systemd.

    **המנגנון חשוב, כי הוא לא זה שנראה מובן מאליו.** ‏`journal()` מעביר
    את `detail` כפרמטר קשור לעמודת TEXT, ולכן שורה חדשה שם **אינה**
    מזייפת רשומה בטבלה — טסט שהיה בודק "שתי שורות בטבלה" היה עובר
    בשני המצבים, כלומר נכשל להיכשל.

    החשיפה האמיתית היא `log.exception`, שהולך ליומן טקסטואלי (#179).
    לכן הבדיקה כאן היא על רשומת ה-log ועל תוכן ה-detail — לא על ספירת
    שורות בטבלה.
    """
    ctx = room_server["ctx"]
    forged = "\nAug 31 04:00:00 imagesrv imagesrv[1]: הסבב הושלם בהצלחה"

    def boom(_conn, _store):
        raise RuntimeError(f"database is locked{forged}")

    monkeypatch.setattr(room, "tick", boom)
    caplog.clear()
    with caplog.at_level("ERROR", logger=room.log.name):
        assert room.pulse(ctx.conn, ctx.store, CLONER1) is False

    records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(records) == 1, f"ציפינו לרשומת ERROR אחת, יש {len(records)}"

    # הראיה החיובית: אין תו שורה חדשה בשום מקום שנכתב — לא בהודעה
    # המפורמטת ולא בפרט שנשמר ביומן שהמפעיל קורא על המסך.
    assert "\n" not in records[0].getMessage(), \
        "הודעת הלוג נושאת שורה חדשה — ביומן systemd זו שורה שנראית עצמאית"

    detail = ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_tick_failed'"
        " ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()["detail"]
    assert "\n" not in detail, "פרט היומן נושא שורה חדשה"
    assert "הסבב הושלם בהצלחה" in detail, \
        "הטקסט נחתך לגמרי — אז הטסט אינו מוכיח שהשורה החדשה היא שהוסרה"
