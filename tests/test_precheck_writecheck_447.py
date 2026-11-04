"""‏#447 (ברזל 25/10 ו-03/11) — כונן שנפסל כאיטי בבדיקת הכתיבה **מקבל אזהרה לפני "התחל"**
בסבב או בגל הבאים שלו.

על ברזל: ה-TOSHIBA DT01ACA0 נפסל ב-room_2c946f3b ("איטי — 33 MB/s"), ובסבב שאחריו
(‏room_1702b788) ה-`precheck` היה `[]`. השורש: הבדיקה המקדימה לא קראה את מדידת בדיקת
הכתיבה כלל. המדידה **נשמרת** (‏`model_speeds`, ‏#1845, בסוף כל גל — גם של דיסק שנפסל),
אבל נשפטה רק מול חציון הדגם, ובלי שלושה דיסקים אחרים מאותו דגם — ‏`slow: None` ושתיקה.

הכלל: המדידה האחרונה של הסידורי מתחת לסף שבתוקף (‏`writefloor.mbps`, ההגדרה
‏`writecheck_min_mbps` של #2338 — אותו סף שהמניפסט מוסר לסוכן) — אזהרה כתומה.
**אזהרה בלבד**: לא פוסל, לא מדלג (הכרעת נדב 19/10).
"""

from __future__ import annotations

import pytest

from server import db
from server.disks import writefloor
from server.learn import models as learn_models
from server.rounds import room_precheck

from test_server_room import (CLONER1, CLONER2, cloner_hello, heartbeat, room,  # noqa: F401
                              room_body, room_server)

MAC = "78:ac:c0:9b:11:c2"


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


def test_a_disk_failed_slow_in_round_one_warns_before_start_in_round_two(room_server):
    """התרחיש מהברזל: סבב 1 — X נפסל איטי; סבב 2 עם X — ‏`precheck` אינו ריק לפני "התחל"."""
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["X", "Y"]})).status_code == 200
    _wave(room_server, ["X", "Y"], [SLOW_X, DONE_Y])
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "Office 2024 Standard"}).json()["ok"] is True
    heartbeat(room_server)
    assert room(deploy)["round"] is None

    # שלושה חריצים נבחרו ושניים הגיעו — הגל נשאר פתוח, לפני "התחל"
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 3, {CLONER1: ["X", "Z", "Q"]})).status_code == 200
    cloner_hello(room_server["anon"], CLONER1, ["X", "Z"])
    r = room(deploy)["round"]
    assert r["wave_state"] == "open"
    warn = r["precheck"]
    assert [w["serial"] for w in warn] == ["X"], warn        # Z לא נמדד מעולם — אין אזהרה
    (w,) = warn
    assert w["write_slow"] is True
    assert "בבדיקת הכתיבה הקודמת נמדד איטי — 33 MB/s, מתחת לסף 50 MB/s" in w["text_he"], w
    by_serial = {d["serial"]: d for d in room(deploy)["machines"][0]["drawer_list"]}
    assert by_serial["X"]["last_write_check"]["slow"] is True
    assert by_serial["X"]["last_write_check"]["mbps"] == 33
    assert by_serial["Z"]["last_write_check"]["slow"] is None   # לא נמדד — לא "תקין"

    # אזהרה, לא חסימה: "התחל" מתקבל, והיומן זוכר שהאזהרה הייתה שם
    started = deploy.post("/api/console/room/start")
    assert started.status_code == 200 and started.json()["warned"] == 1, started.text
    (detail,) = _journal(ctx, "room_precheck_warned")
    assert f"{CLONER1}:1 learned=slow_write" in detail, detail


def test_the_next_wave_of_the_same_round_warns_too(room_server):
    """"בסבב/גל קודם": כונן שנפסל נשאר טרי לגל הבא של אותו סבב — והגל ההוא מזהיר עליו.
    ‏shich-2 נבחר ואינו עונה, ולכן כל גל נשאר פתוח עד "התחל"."""
    deploy = room_server["deploy"]
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 3, {CLONER1: ["X", "Y"], CLONER2: ["V"]}))
    assert opened.status_code == 200, opened.text
    _wave(room_server, ["X", "Y"], [SLOW_X, DONE_Y])
    cloner_hello(room_server["anon"], CLONER1, ["X", "Y"])      # Y כבר נכתב — רק X טרי
    r = room(deploy)["round"]
    assert r["wave_state"] == "open", r
    assert [(w["serial"], w["write_slow"]) for w in r["precheck"]] == [("X", True)], r["precheck"]


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


# --- היחידה: מה נחשב "איטי בפעם הקודמת" ---------------------------------------


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _m(conn, session, serial, mbps, at):
    conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at)"
                 " VALUES (?,?,?,?,?,?)", (session, serial, "TOSHIBA DT01ACA050", MAC, mbps, at))
    conn.commit()


def test_the_last_measurement_decides_not_an_old_one(conn):
    _m(conn, "ses_1", "X", 33, "2026-11-01T10:00:00+00:00")
    _m(conn, "ses_2", "X", 140, "2026-11-02T10:00:00+00:00")   # מאז נמדד תקין
    got = learn_models.last_check(conn, "X")
    assert got == {"mbps": 140, "slow": False, "text_he": None}


def test_at_the_floor_is_not_slow_and_below_it_is(conn):
    floor = writefloor.mbps(conn)
    assert floor == writefloor.DEFAULT                              # בלי הגדרה — 50
    _m(conn, "ses_1", "AT", floor, "2026-11-01T10:00:00+00:00")
    _m(conn, "ses_1", "UNDER", floor - 1, "2026-11-01T10:00:00+00:00")
    assert learn_models.last_check(conn, "AT")["slow"] is False      # הסוכן: ‏-lt
    assert learn_models.last_check(conn, "UNDER")["slow"] is True


def test_never_measured_is_unknown_not_fine(conn):
    assert learn_models.last_check(conn, "NEW") == {"mbps": None, "slow": None, "text_he": None}
    assert learn_models.last_check(conn, None)["slow"] is None


def test_the_warning_names_the_slot_and_stays_a_warning(conn):
    _m(conn, "ses_1", "X", 21, "2026-11-01T10:00:00+00:00")
    disk = {"dev": "sda", "serial": "X", "port": 1, "model": "TOSHIBA DT01ACA050"}
    d = {**disk, **room_precheck.drawer_fields(conn, disk, MAC), "fresh": True}
    (w,) = room_precheck.warnings([{"mac": MAC, "name": "01", "joined": True,
                                    "drawer_list": [d]}], selection=False)
    assert w["write_slow"] is True
    assert w["text_he"] == "01 · דיסק 1: בבדיקת הכתיבה הקודמת נמדד איטי — 21 MB/s, מתחת לסף 50 MB/s"


def test_the_floor_in_effect_is_the_setting_and_the_text_names_it(conn):
    """‏#2338: הסף הוא ההגדרה `writecheck_min_mbps` — 45 עובר סף 40, ו-35 נכשל מולו."""
    db.set_settings(conn, {writefloor.SETTING: "40"})
    _m(conn, "ses_1", "OVER", 45, "2026-11-01T10:00:00+00:00")
    _m(conn, "ses_1", "UNDER", 35, "2026-11-01T10:00:00+00:00")
    assert learn_models.last_check(conn, "OVER")["slow"] is False    # מתחת ל-50, מעל 40
    got = learn_models.last_check(conn, "UNDER")
    assert got["slow"] is True
    assert got["text_he"] == "בבדיקת הכתיבה הקודמת נמדד איטי — 35 MB/s, מתחת לסף 40 MB/s"


def test_the_precheck_and_the_manifest_ask_the_same_function(room_server, monkeypatch):
    """הסף של האזהרה הוא הסף שהסוכן יקבל במניפסט — אותה פונקציה, `writefloor.mbps`.
    שני מקורות היו יכולים לזוז זה מזה, והאזהרה הייתה משקרת."""
    from server.disks import writefloor as wf                   # noqa: PLC0415
    monkeypatch.setattr(wf, "mbps", lambda _conn: 77)
    anon, ctx = room_server["anon"], room_server["ctx"]
    assert anon.get("/api/v1/images/img_7f3a91/manifest").json()[wf.SETTING] == 77
    _m(ctx.conn, "ses_1", "X", 60, "2026-11-01T10:00:00+00:00")
    got = learn_models.last_check(ctx.conn, "X")
    assert got["slow"] is True and "מתחת לסף 77 MB/s" in got["text_he"], got
