"""‏#447 → ‏#2848 (נדב 11/11): דיסק שנפסל כאיטי בבדיקת הכתיבה **אינו** מוזהר בסבב הבא.

‏#447 (ברזל 03/11) הוסיף אזהרה לפני "התחל" על דיסק שבדיקת הכתיבה **האחרונה** שלו הייתה איטית
(‏`last_write_check`). נדב 11/11 הסיר אותה: "איטי" נקבע רק לפי הסבב הנוכחי — הסבב הבא מודד
מחדש, ודיסק שאינו איטי עוד חוזר לצבע של ה-SMART שלו, בלי שום סימון מהעבר. מה שנשאר מכאן:
המדידה עצמה עדיין נשמרת ב-`model_speeds` (‏#1845, "איטי מהרגיל לדגם"), גם בגל שנסגר מבחוץ.
"""

from __future__ import annotations

from test_server_room import (CLONER1, CLONER2, cloner_hello, heartbeat, room,  # noqa: F401
                              room_body, room_server)



def _journal(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ?", (event,))]


def _wave(room_server, serials: list[str], targets: list[dict]) -> None:
    """גל אחד בסבב הפתוח: hello, "התחל", ודוח סיום של הסוכן."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    wave = cloner_hello(anon, CLONER1, serials)["session"]["id"]
    heartbeat(room_server)
    if room(deploy)["round"]["wave_state"] == "open":
        assert deploy.post("/api/console/room/start").status_code == 200
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "partial",
        "targets": targets}).status_code == 200
    heartbeat(room_server)


SLOW_X = {"dev": "sda", "bytes_written": 0, "bytes_total": 100, "state": "failed",
          "error": "איטי — 33 MB/s, מתחת לסף 50 MB/s",
          "write_preflight": {"state": "slow", "reason": "איטי — 33 MB/s, מתחת לסף 50 MB/s",
                              "mbps": 33}}
DONE_Y = {"dev": "sdb", "bytes_written": 100, "bytes_total": 100, "state": "done",
          "write_preflight": {"state": "ok", "reason": "", "mbps": 180}}


def test_a_disk_failed_slow_in_round_one_is_not_warned_in_round_two(room_server):
    """סבב 1 — X נפסל איטי; סבב 2 עם X — אין אזהרה לפני "התחל", ואין שדה מהסבב הקודם."""
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["X", "Y"]})).status_code == 200
    _wave(room_server, ["X", "Y"], [SLOW_X, DONE_Y])
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "Office 2024 Standard"}).json()["ok"] is True
    heartbeat(room_server)
    assert room(deploy)["round"] is None
    assert ctx.conn.execute("SELECT mbps FROM model_speeds WHERE serial = 'X'").fetchone()["mbps"] == 33   # measured

    assert deploy.post("/api/console/room", json=room_body(
        room_server, 3, {CLONER1: ["X", "Z", "Q"]})).status_code == 200
    cloner_hello(room_server["anon"], CLONER1, ["X", "Z"])
    r = room(deploy)["round"]
    assert r["wave_state"] == "open"
    assert r["precheck"] == [], r["precheck"]
    x = {d["serial"]: d for d in room(deploy)["machines"][0]["drawer_list"]}["X"]
    assert "last_write_check" not in x and x["write_slow"] is False, x
    started = deploy.post("/api/console/room/start")
    assert started.status_code == 200 and started.json()["warned"] == 0, started.text
    assert _journal(ctx, "room_precheck_warned") == []


def test_the_next_wave_of_the_same_round_is_not_warned_either(room_server):
    """גל חדש באותו סבב מודד מחדש — X טרי, ואין עליו אזהרה מהגל הקודם."""
    deploy = room_server["deploy"]
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 3, {CLONER1: ["X", "Y"], CLONER2: ["V"]}))
    assert opened.status_code == 200, opened.text
    _wave(room_server, ["X", "Y"], [SLOW_X, DONE_Y])
    cloner_hello(room_server["anon"], CLONER1, ["X", "Y"])      # Y כבר נכתב — רק X טרי
    r = room(deploy)["round"]
    assert r["wave_state"] == "open", r
    assert r["precheck"] == [], r["precheck"]


def test_a_wave_closed_from_the_console_still_learns_the_write_check(room_server):
    """‏#2345: גל שנסגר מבחוץ (`/api/console/sessions/{id}/close`) מסתיים ב-`room._resume`,
    לא ב-`_finish_wave` — והמדידה של המגירה שנפסלה חייבת להגיע ל-`model_speeds` גם משם."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 3, {CLONER1: ["X", "Y"], CLONER2: ["V"]})).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["X", "Y"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    assert anon.post("/api/v1/agent/progress", json={      # עוד כותב — הגל לא הסתיים מעצמו
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [SLOW_X, {**DONE_Y, "state": "writing", "bytes_written": 40}]}).status_code == 200
    closed = deploy.post(f"/api/console/sessions/{wave}/close",
                         json={"confirm_name": "Office 2024 Standard"})
    assert closed.status_code == 200, closed.text
    cloner_hello(anon, CLONER1, ["X", "Y"])                # הדופק — ‏_resume
    assert room(deploy)["round"]["wave_session_id"] != wave
    got = {r["serial"]: r["mbps"] for r in ctx.conn.execute(
        "SELECT serial, mbps FROM model_speeds WHERE session_id = ?", (wave,))}
    assert got == {"X": 33, "Y": 180}, got      # גם המדידה התקינה של Y — כמו ב-_finish_wave
