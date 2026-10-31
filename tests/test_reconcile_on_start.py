"""‏#411 — פיוס בעליית השרת: מה שהיה `running` כשהשרת נפל אינו נשאר תלוי.

הכרעת נדב (06/09, אפשרות 1): סבב `running` יתום הופך לכישלון בעליית
השרת — לא ממשיך, לא נשאר תלוי. עד כאן איש לא רץ בעלייה על הטבלאות:
`sweep_work_areas` משאיר `running` במכוון, `SessionStore.__init__` אינו
סורק, ו-`room._resume` רץ בדופק. התוצאה שנמדדה: מכונה שחברה ולא סיימה
קיבלה את הסבב שוב ב-hello והמתינה למולטיקאסט שלעולם לא יצא; משימות
קליטה נשארו `running` לנצח.

הבדיקות מריצות שרת, מביאות אותו למצב, ובונות שרת **שני** על אותו
`--data-dir` — זו "עלייה מחדש". ההבחנה שנבדקת: כישלון **בשם**, לא
מחיקה; חבר שכבר `done` נשאר `done`; משימה שהאימג' שלה כבר בספרייה
(‏`rename` קרה, ה-`UPDATE` אבד) היא `done`, לא כישלון (עיקרון 3/5).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import Clock, hello_body, write_image, MANIFEST_256

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from test_server_room import CLONER1, CLONER2, cloner_hello, report, room  # noqa: E402,F401

try:
    from server.rounds.reconcile import RESTARTED_SESSION, RESTARTED_TASK
except ImportError:   # בקרה שלילית: בלי המודול הטסטים רצים ונופלים על ההתנהגות, לא על ImportError
    RESTARTED_SESSION = RESTARTED_TASK = "השרת עלה מחדש"


def _boot(tmp_path: Path, monkeypatch, *, seed: bool, **runtime_options):
    """שרת על `tmp_path/data`. ‏`seed=True` בפעם הראשונה: משתמשים ומכונות."""
    from server.rounds import sender as sender_module
    from server.console import users
    from server.app import create_app
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    images = tmp_path / "images"
    if seed:
        write_image(images, MANIFEST_256)
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080",
                     now_fn=Clock(), sender_runner=Recorder(block=True),
                     wol_send=lambda frame: None, **runtime_options)
    ctx = app.state.ctx
    if seed:
        users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                     is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    if seed:
        for mac, name in ((CLONER1, "shich-1"), (CLONER2, "shich-2")):
            assert admin.post("/api/console/machines", json={
                "mac": mac, "name": name, "group_id": "grp_CLONERS"}).status_code == 200
    return {"app": app, "ctx": ctx, "admin": admin, "anon": TestClient(app)}


def _events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]


def _running_wave(first) -> str:
    """סבב חדר עם גל `running`: מכונה 1 כותבת, מכונה 2 כבר סיימה."""
    admin, anon = first["admin"], first["anon"]
    # ‏#1410: בחירת המגירות מפורשת וחובה, והיא נבדקת מול מגירות מחוברות —
    # ולכן hello ראשון לפני הפתיחה, והשני מצרף לגל.
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert admin.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 4,
        "target_slots": [{"mac": CLONER1, "ports": [1, 2]},
                         {"mac": CLONER2, "ports": [1, 2]}]}).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])   # #1703: covers the target -- the wave starts here
    wave = first["ctx"].conn.execute("SELECT wave_session_id FROM room_rounds").fetchone()[0]
    assert first["ctx"].conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (wave,)).fetchone()["state"] == "running"
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 40, "bytes_total": 100, "state": "writing"},
                    {"dev": "sdb", "bytes_written": 40, "bytes_total": 100, "state": "writing"}],
    }).status_code == 200
    report(anon, wave, CLONER2, {"sda": "done", "sdb": "done"})
    return wave


def _insert_task(ctx, task_id: str, image_id: str, state: str = "running") -> None:
    ctx.conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, created_by,"
        " created_at, updated_at, state, token)"
        " VALUES (?, ?, 'capture', 'sda', ?, 'קליטה', 'noc', 'x', 'x', ?, 'tok')",
        (task_id, CLONER1, image_id, state))
    ctx.conn.commit()


def _task(ctx, task_id: str):
    return ctx.conn.execute("SELECT state, error FROM tasks WHERE id = ?", (task_id,)).fetchone()


def test_a_running_wave_is_closed_as_a_failure_by_name_and_the_writer_does_not_get_it_again(tmp_path, monkeypatch):
    first = _boot(tmp_path, monkeypatch, seed=True)
    wave = _running_wave(first)
    first["ctx"].sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    ctx = second["ctx"]
    assert ctx.conn.execute("SELECT state FROM sessions WHERE id = ?", (wave,)).fetchone()["state"] == "closed"
    members = {m["mac"]: m for m in ctx.store.members(wave)}
    assert members[CLONER1]["state"] == "failed"
    assert members[CLONER1]["error"] == RESTARTED_SESSION
    assert members[CLONER2]["state"] == "done" and members[CLONER2]["done"], "מי שסיים — סיים"
    assert "session_orphaned" in _events(ctx)
    # מכונה 1 עולה מחדש ופונה: היא אינה מקבלת את הגל המת שוב.
    answer = cloner_hello(second["anon"], CLONER1, ["S1", "S2"])
    assert not (answer.get("session") and answer["session"].get("id") == wave)
    # לא נמחק דבר: הסבב, החברים והמגירות שנכתבו נשארים לקריאה.
    assert ctx.conn.execute("SELECT COUNT(*) FROM session_members WHERE session_id = ?", (wave,)).fetchone()[0] == 2
    second["ctx"].sender.stop()


def test_the_room_round_survives_and_the_next_pulse_opens_a_fresh_wave(tmp_path, monkeypatch):
    """הסבב המצטבר (`room_rounds`) אינו הגל: הוא נשאר `active`, והדופק הבא
    סופר את המגירות שכן נכתבו (מכונה 2) ופותח גל חדש — כמו אחרי
    `_mark_lost`. הגל שנפל נכשל; לא נמשך."""
    first = _boot(tmp_path, monkeypatch, seed=True)
    wave = _running_wave(first)
    first["ctx"].sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    view = room(second["admin"])
    assert view["round"] is not None and view["round"]["wave_state"] == "closed"
    # דופק לא-מצטרף: ‏hello מצטרף של מכונה 1 גם היה מצרף אותה לגל החדש
    # ומוציא אותו לדרך באותה תשובה (#1703) — כאן נבדק רק שהגל נפתח.
    cloner_hello(second["anon"], CLONER1, ["S1", "S2"], joining=False)   # דופק
    view = room(second["admin"])
    assert view["round"]["written_drives"] == 2, "מה שמכונה 2 כתבה נספר, לא אבד"
    assert view["round"]["wave_state"] == "open"
    assert "room_wave_lost" in _events(second["ctx"])
    assert second["ctx"].conn.execute(
        "SELECT wave_session_id FROM room_rounds").fetchone()[0] != wave
    second["ctx"].sender.stop()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="TZ/tzset is POSIX-only")
def test_startup_timezone_reconcile_and_next_wave_store_real_utc(tmp_path, monkeypatch):
    """#1771: startup applies Asia/Jerusalem, orphans the old wave, then
    opens its replacement; every persisted ISO value remains real UTC."""
    first = _boot(tmp_path, monkeypatch, seed=True)
    old_wave = _running_wave(first)
    first["ctx"].sender.stop()
    journal_cut = first["ctx"].conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM journal").fetchone()[0]

    current_zone = "Etc/UTC"

    def read_zone():
        return current_zone

    def set_zone(zone):
        nonlocal current_zone
        current_zone = zone
        os.environ["TZ"] = zone
        time.tzset()
        return None

    before = datetime.now(timezone.utc)
    try:
        second = _boot(
            tmp_path, monkeypatch, seed=False, apply_timezone=True,
            host_hooks={"timezone": read_zone, "set_timezone": set_zone},
        )
        cloner_hello(second["anon"], CLONER1, ["S1", "S2"], joining=False)
        rows = second["ctx"].conn.execute(
            "SELECT ts FROM journal WHERE event IN"
            " ('timezone_applied', 'session_orphaned', 'room_wave_lost', 'session_open')"
            " AND rowid > ?", (journal_cut,)).fetchall()
        new_wave = second["ctx"].conn.execute(
            "SELECT created_at FROM sessions WHERE id != ? ORDER BY rowid DESC LIMIT 1",
            (old_wave,),
        ).fetchone()
        stamps = [datetime.fromisoformat(row["ts"]) for row in rows]
        stamps.append(datetime.fromisoformat(new_wave["created_at"]))
        after = datetime.now(timezone.utc)
        assert stamps, "startup did not write the events under test"
        assert all(stamp.utcoffset().total_seconds() == 0 for stamp in stamps)
        assert all(before - timedelta(seconds=2) <= stamp <= after
                   for stamp in stamps), stamps
        second["ctx"].sender.stop()
    finally:
        monkeypatch.undo()
        time.tzset()


def test_an_open_session_waiting_for_joiners_is_not_touched(tmp_path, monkeypatch):
    first = _boot(tmp_path, monkeypatch, seed=True)
    cloner_hello(first["anon"], CLONER1, ["S1"])       # ‏#1410: המגירה מחוברת לפני הבחירה
    assert first["admin"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 4,
        "target_slots": [{"mac": CLONER1, "ports": [1]}]}).status_code == 200
    cloner_hello(first["anon"], CLONER1, ["S1"])       # מוכנות 1 מול יעד 4 — הגל נשאר open
    wave = first["ctx"].conn.execute("SELECT wave_session_id FROM room_rounds").fetchone()[0]
    assert first["ctx"].conn.execute("SELECT state FROM sessions WHERE id = ?", (wave,)).fetchone()["state"] == "open"
    first["ctx"].sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    assert second["ctx"].conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (wave,)).fetchone()["state"] == "open"
    assert "session_orphaned" not in _events(second["ctx"])
    second["ctx"].sender.stop()


def test_a_running_capture_whose_upload_died_with_the_server_fails_by_name(tmp_path, monkeypatch):
    first = _boot(tmp_path, monkeypatch, seed=True)
    _insert_task(first["ctx"], "tsk_dead", "img_dead")
    _insert_task(first["ctx"], "tsk_wait", "img_wait", state="pending")
    first["ctx"].sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    dead = _task(second["ctx"], "tsk_dead")
    assert dead["state"] == "failed"
    assert dead["error"] == RESTARTED_TASK
    assert _task(second["ctx"], "tsk_wait")["state"] == "pending", "משימה שטרם התחילה ממתינה למכונה"
    assert "capture_failed" in _events(second["ctx"])
    second["ctx"].sender.stop()


def test_a_capture_whose_image_already_landed_in_the_library_is_done_not_failed(tmp_path, monkeypatch):
    """‏`capture.py`: ‏`rename` ואז `UPDATE 'done'` — נפילה ביניהם משאירה
    `running` עם אימג' שלם בספרייה. הדיסק הוא מקור האמת (עיקרון 3)."""
    first = _boot(tmp_path, monkeypatch, seed=True)
    _insert_task(first["ctx"], "tsk_land", "img_7f3a91")      # האימג' הזה כבר בספרייה
    first["ctx"].sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    landed = _task(second["ctx"], "tsk_land")
    assert landed["state"] == "done" and landed["error"] is None
    assert "capture_done" in _events(second["ctx"])
    assert "capture_failed" not in _events(second["ctx"])
    second["ctx"].sender.stop()
