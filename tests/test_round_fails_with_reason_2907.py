"""‏#2907 — סבב שנגמר בלי אף הצלחה, בגלל אירוע שאינו "המפעיל עצר", נכשל **בשם**.

"נסגר 0/N בלי `failed_reason`" (הצורה של #2841) נוצר בארבעה מסלולים ש-PR #2859
לא כיסה (הוא כיסה מסלול אחד: משכפל שאבד באמצע `_finish_wave`):

  1. עלייה מחדש של השרת באמצע שידור כיתה: ‏`reconcile` סגר `closed` בלי סיבה.
  2. אותו דבר בסבב ישיר: ‏`_resume` סגר `closed` 0/N ‏(`room_done`).
  3. ביטול באתחול של משימת גואי שעוד pending — ראו `test_gui_task_boot_cancel_1576.py`
     (הסבב והגל `failed`, והמשכפל שהצטרף `failed` ולא `waiting`).
  4. כל המגירות דיווחו `failed` (בלי lost): ‏`_finish_wave` סגר `closed` 0/N.

והתצוגה בגואי (סעיף 5 ב-Issue): סבב `failed` הוא מה שהגואי מציג כ"הסבב נכשל"
(‏`guistate.sh`: ‏`room_failed=`); סבב `closed` אינו נראה כלל (`visible_round`).

אין כאן הפיכה של עצירת מפעיל לכשל: ‏`test_an_operator_stop_is_still_closed_not_failed`.
"""

from __future__ import annotations

from test_direct_server import _put_manifest, _setup
from test_reconcile_on_start import _boot
from test_server_room import (           # noqa: F401 — ‏room_server הוא fixture
    CLONER1, cloner_hello, heartbeat, report, room, room_server,
)

CLONERS = ["S1", "S2"]


def _row(ctx, sql, *args):
    return ctx.conn.execute(sql, args).fetchone()


def _events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]


def _running_direct_wave(room_server) -> dict:
    """סבב ישיר, המשכפל הצטרף, המניפסט הגיע, והגל `running`."""
    opened = _setup(room_server)
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, CLONERS)
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    cloner_hello(anon, CLONER1, CLONERS)
    assert room(room_server["admin"])["round"]["wave_state"] == "running"
    return opened


# --- 4. כל המגירות נכשלו ----------------------------------------------------------


def test_every_drawer_failed_fails_the_direct_round_by_name(room_server):
    """היום: `room_done written=0/2`, ‏`closed`, בלי סיבה — והסבב נעלם מהמסך."""
    opened = _running_direct_wave(room_server)
    ctx = room_server["ctx"]
    report(room_server["anon"], opened["wave_session_id"], CLONER1,
           {"sda": "failed", "sdb": "failed"})
    heartbeat(room_server)
    rnd = _row(ctx, "SELECT state, failed_reason FROM room_rounds WHERE id = ?", opened["id"])
    assert rnd["state"] == "failed", "0/2 כשכל המגירות נכשלו נסגר כאילו הסתיים"
    assert "כל המגירות נכשלו" in rnd["failed_reason"] and "0/2" in rnd["failed_reason"]
    assert "room_failed" in _events(ctx) and "room_done" not in _events(ctx)


def test_one_drawer_written_is_still_a_done_round_not_a_failure(room_server):
    """שומר נגד תיקון-יתר: מגירה אחת נכתבה והשנייה נכשלה — `closed`, כמו קודם."""
    opened = _running_direct_wave(room_server)
    ctx = room_server["ctx"]
    report(room_server["anon"], opened["wave_session_id"], CLONER1,
           {"sda": "done", "sdb": "failed"}, top="partial")
    heartbeat(room_server)
    rnd = _row(ctx, "SELECT state, failed_reason, written_drives FROM room_rounds WHERE id = ?",
               opened["id"])
    assert rnd["state"] == "closed" and rnd["failed_reason"] is None and rnd["written_drives"] == 1
    assert "room_done" in _events(ctx)


def test_an_operator_stop_is_still_closed_not_failed(room_server):
    """"עצור סבב" הוא החלטה של מפעיל — לא כשל (אסור בתיקון)."""
    opened = _running_direct_wave(room_server)
    ctx = room_server["ctx"]
    assert room_server["admin"].post(
        "/api/console/room/close", json={"confirm_name": "build-01:sda"}).status_code == 200
    heartbeat(room_server)
    rnd = _row(ctx, "SELECT state, failed_reason FROM room_rounds WHERE id = ?", opened["id"])
    assert rnd["state"] == "closed" and rnd["failed_reason"] is None


# --- 2. עלייה מחדש באמצע סבב ישיר ----------------------------------------------------


def test_a_restart_mid_direct_wave_fails_the_round_by_name(room_server, tmp_path):
    """היום: ‏`reconcile` סוגר את הגל והחברים `failed`, ו-`_resume` סוגר את הסבב
    `closed` 0/N ‏(`room_done`) — אותה צורה של #2841, בלי סיבה."""
    from server.rounds.reconcile import reconcile_on_start

    opened = _running_direct_wave(room_server)
    ctx = room_server["ctx"]
    out = reconcile_on_start(ctx.conn, tmp_path)
    assert opened["wave_session_id"] in out["sessions_closed"]
    heartbeat(room_server)                      # הדופק הבא: `_resume`
    rnd = _row(ctx, "SELECT state, failed_reason FROM room_rounds WHERE id = ?", opened["id"])
    assert rnd["state"] == "failed", "עלייה מחדש באמצע סבב ישיר סגרה אותו 0/N בלי סיבה"
    assert "השרת עלה מחדש" in rnd["failed_reason"]
    assert "room_failed" in _events(ctx) and "room_done" not in _events(ctx)
    view = room(room_server["admin"])["round"]
    assert view is not None and view["state"] == "failed"          # נשאר על המסך עם הסיבה
    assert view["failed_reason"] == rnd["failed_reason"]


def test_a_restart_mid_library_wave_still_opens_the_next_wave(room_server, tmp_path):
    """שומר נגד תיקון-יתר: סבב מספרייה ממשיך לגל הבא אחרי עלייה מחדש (כמו #411)."""
    from server.rounds.reconcile import reconcile_on_start

    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    cloner_hello(anon, CLONER1, CLONERS)
    assert admin.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": [{"mac": CLONER1, "ports": [1, 2]}]}).status_code == 200
    cloner_hello(anon, CLONER1, CLONERS)
    cloner_hello(anon, CLONER1, CLONERS)
    wave = _row(ctx, "SELECT wave_session_id FROM room_rounds")[0]
    assert _row(ctx, "SELECT state FROM sessions WHERE id = ?", wave)["state"] == "running"
    reconcile_on_start(ctx.conn, tmp_path)
    cloner_hello(anon, CLONER1, CLONERS, joining=False)
    rnd = _row(ctx, "SELECT state, wave_session_id FROM room_rounds")
    assert rnd["state"] == "active" and rnd["wave_session_id"] != wave


# --- 1. עלייה מחדש באמצע שידור כיתה ---------------------------------------------------


def test_a_classroom_broadcast_orphaned_by_a_restart_is_failed_with_a_reason(
        tmp_path, monkeypatch, classrooms_on):
    """היום: ‏`closed`, ‏`failed_reason` NULL — ובסקירה (`latest_failed_broadcast`) אין כשל."""
    first = _boot(tmp_path, monkeypatch, seed=True)
    ctx, admin = first["ctx"], first["admin"]
    mac = "b4:2e:99:00:00:09"
    assert admin.post("/api/console/groups", json={
        "id": "grp_LAB9", "label": "כיתה 9", "role": "classroom"}).status_code == 200
    assert admin.post("/api/console/machines", json={
        "mac": mac, "name": "05", "group_id": "grp_LAB9"}).status_code == 200
    sid = ctx.store.open("grp_LAB9", "img_7f3a91", prefix="LAB9", expected_clients=1,
                         opened_by="noc")
    ctx.conn.execute("UPDATE sessions SET state = 'running' WHERE id = ?", (sid,))
    ctx.conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                     " VALUES (?, ?, 'writing', 'x')", (sid, mac))
    ctx.conn.commit()
    ctx.sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    ctx = second["ctx"]
    row = _row(ctx, "SELECT state, failed_reason FROM sessions WHERE id = ?", sid)
    assert row["state"] == "failed" and "השרת עלה מחדש" in row["failed_reason"]
    assert _row(ctx, "SELECT state FROM session_members WHERE session_id = ?", sid)["state"] == "failed"
    shown = ctx.store.latest_failed_broadcast()
    assert shown is not None and shown["id"] == sid
    ctx.sender.stop()
