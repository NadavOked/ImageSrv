"""‏#2427 — מרוצים במשדר המולטיקאסט. כל טסט כאן הוא ה-PoC של הסריקה
(‏lab/security-scan-1104/scan6/poc_sender_races.py) **הפוך**: שם הוא עבר כשהבאג
קיים, כאן הוא נכשל כשהבאג קיים.

1א. סגירה בחלון שבין ה-commit ל-running לבין ``on_running`` — ‏udp-sender לא עולה.
1ב. ‏``stop`` בין מחיצות — המחיצה הבאה לא מופעלת אחרי ש-``stop`` אמר "איננו".
2.  ‏``start`` שסורב מכשיל את הסבב בשמו, ולא נבלע ב-log; וסגירת סבב כיתה
    אינה "ok" כשהמשדר שרד SIGKILL (כמו ``close_round``, ‏#439).
3.  ‏``close_round`` מול ``_finish_wave`` — הגל שהוחלף אינו נשאר ``open``.
4.  חריגה בתהליכון השולח — ‏``failed`` בשם, לא ``starting`` לתמיד.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

pytest.importorskip("fastapi")

from server.db import connect, now_iso
from server.library.images import ImageLibrary
from server.rounds import sender as sender_module
from server.rounds.sender import SenderEngine
from server.rounds.sessions import SessionStore

from conftest import MANIFEST_256, hello_body, setup_classroom, write_image
from test_sender import FakeProcess, Recorder, wait_for

GROUP = "grp_LAB1"
MAC = "b4:2e:99:07:1a:c4"
IMAGE = "img_7f3a91"


@pytest.fixture()
def library(tmp_path):
    write_image(tmp_path / "images", MANIFEST_256)
    return ImageLibrary(tmp_path / "images")


@pytest.fixture(autouse=True)
def free_ports(monkeypatch):
    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])


def _db(tmp_path):
    db = connect(tmp_path / "races.db")
    db.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, 'classroom')",
               (GROUP, "כיתה"))
    db.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, ?, ?, ?)",
               (MAC, "05", GROUP, now_iso()))
    db.commit()
    return db


def _state(db, sid):
    return db.execute("SELECT state FROM sessions WHERE id = ?", (sid,)).fetchone()["state"]


# --- 1א ------------------------------------------------------------------------


def test_a_close_inside_the_start_window_never_launches_udp_sender(tmp_path, library):
    db = _db(tmp_path)
    recorder = Recorder(block=True)
    engine = SenderEngine(library, runner=recorder)
    inside, release = threading.Event(), threading.Event()
    refused: list = []

    def on_running(session):
        if session["id"] == first_id:            # רק הסבב הראשון נעצר בחלון
            inside.set()
            assert release.wait(10)
        refused.append(engine.start(session))

    def on_closed(session_id):
        engine.stop(session_id)

    store = SessionStore(db, on_running=on_running, on_closed=on_closed)
    first_id = store.open(GROUP, IMAGE, "LAB1", 2, "noc", wait_seconds=300)
    store.record_hello(db.execute("SELECT * FROM sessions WHERE id = ?",
                                  (first_id,)).fetchone(), MAC)
    hello = threading.Thread(target=lambda: SessionStore(
        db, on_running=on_running, on_closed=on_closed).start_now(first_id, "noc"))
    hello.start()
    assert inside.wait(10), "start_now never reached the window"
    console = threading.Thread(target=lambda: SessionStore(
        db, on_running=on_running, on_closed=on_closed).close(first_id, "noc"))
    console.start()
    console.join(10)
    release.set()
    hello.join(10)

    assert _state(db, first_id) == "closed"
    assert not recorder.spawned.wait(1.0), "udp-sender was launched for a closed round"
    assert refused and refused[0] and "נסגר" in refused[0]

    # החריץ פנוי, והסבב הבא משדר כרגיל.
    second_id = store.open(GROUP, IMAGE, "LAB1", 1, "noc", wait_seconds=300)
    store.record_hello(db.execute("SELECT * FROM sessions WHERE id = ?",
                                  (second_id,)).fetchone(), MAC)
    store.start_now(second_id, "noc")
    assert recorder.spawned.wait(5)
    assert refused[-1] is None
    assert engine.status()["session_id"] == second_id
    recorder.processes[0].release()


# --- 1ב ------------------------------------------------------------------------


class TwoPhaseRunner(Recorder):
    """מחיצה 1 מסתיימת מיד; מחיצה 2 נחסמת כמו udp-sender שממתין."""

    def __call__(self, cmd):
        self.block = len(self.processes) >= 1
        return super().__call__(cmd)


def test_a_stop_between_partitions_launches_nothing_after_it(library):
    runner = TwoPhaseRunner()
    calls: list[int] = []
    stop_ret: list = []

    def live(session_id):
        calls.append(1)
        if len(calls) == 2:                 # בתוך `_receivers` של מחיצה 2 — לפני runner
            stop_ret.append(engine.stop(session_id))
        return 1

    engine = SenderEngine(library, runner=runner, live_receivers=live)
    engine.start({"id": "ses_A", "image_id": IMAGE, "joined": 1})
    assert wait_for(lambda: stop_ret)
    assert wait_for(lambda: not engine._thread.is_alive())
    assert stop_ret == [None]
    assert len(runner.processes) == 1, "partition 2 was launched after stop() said it is gone"
    assert engine.status()["state"] == "stopped"

    # והסבב הבא אינו נבלע: התהליכון הקודם כבר סיים.
    assert engine.start({"id": "ses_B", "image_id": IMAGE, "joined": 1}) is None
    assert wait_for(lambda: len(runner.processes) == 2)
    assert engine.status()["session_id"] == "ses_B"
    runner.processes[-1].release()


# --- 2 -------------------------------------------------------------------------


def test_start_while_another_sender_is_alive_returns_the_reason(library):
    recorder = Recorder(block=True)
    engine = SenderEngine(library, runner=recorder)
    assert engine.start({"id": "ses_A", "image_id": IMAGE, "joined": 1}) is None
    assert recorder.spawned.wait(5)
    refused = engine.start({"id": "ses_B", "image_id": IMAGE, "joined": 1})
    assert refused and "ses_A" in refused and "ses_B" in refused
    assert engine.status()["session_id"] == "ses_A"
    recorder.processes[0].release()


def test_a_refused_start_fails_the_round_by_name(server_with_sender):
    server, recorder = server_with_sender
    recorder.block = True
    ids = setup_classroom(server)
    first = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": IMAGE, "prefix": "LAB1",
              "expected_clients": 1}).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(5)
    ctx = server["ctx"]
    # הסבב נסגר ב-DB בלי שהמשדר שלו נעצר — החריץ פנוי, והתהליכון עדיין חי.
    ctx.conn.execute("UPDATE sessions SET state = 'closed' WHERE id = ?", (first,))
    ctx.conn.commit()
    second = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": IMAGE, "prefix": "LAB1",
              "expected_clients": 1}).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac2"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac2"]))
    row = ctx.conn.execute("SELECT state, failed_reason FROM sessions WHERE id = ?",
                           (second,)).fetchone()
    assert row["state"] == "failed", dict(row)
    assert "עדיין רץ" in row["failed_reason"]
    assert len(recorder.processes) == 1
    recorder.processes[0].release()


def test_closing_a_classroom_round_is_not_ok_while_udp_sender_survives(
        server_with_sender, monkeypatch):
    monkeypatch.setattr(sender_module, "STOP_TERM_WAIT", 0.05)
    monkeypatch.setattr(sender_module, "STOP_KILL_WAIT", 0.05)
    server, recorder = server_with_sender
    recorder.block, recorder.ignore_term, recorder.ignore_kill = True, True, True
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": IMAGE, "prefix": "LAB1",
              "expected_clients": 1}).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(5)
    assert wait_for(lambda: server["ctx"].sender._process is not None)
    r = server["admin"].post(f"/api/console/sessions/{session}/close",
                             json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 500, r.text
    assert "udp-sender עדיין רץ" in r.json()["detail"]
    assert str(recorder.processes[0].pid) in r.json()["detail"]
    recorder.processes[0].release()


def test_closing_a_classroom_round_whose_sender_died_is_ok(server_with_sender):
    """השומר מהצד השני: סגירה רגילה נשארת ``ok``."""
    server, recorder = server_with_sender
    recorder.block = True
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": IMAGE, "prefix": "LAB1",
              "expected_clients": 1}).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(5)
    r = server["admin"].post(f"/api/console/sessions/{session}/close",
                             json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 200, r.text
    assert recorder.processes[0].terminated


# --- 3 -------------------------------------------------------------------------


def test_close_round_racing_the_next_wave_leaves_no_open_wave(tmp_path, library, monkeypatch):
    """T1 = close_round קורא את round_row (גל W1). ‏T2 = ‏_finish_wave פותח את W2 במקום
    W1 ומעדכן את room_rounds — בין הקריאה של T1 ל-CAS שלו ל-'closing'."""
    from types import SimpleNamespace
    from server.rounds import room
    from server.agent.registry import CLONERS_GROUP

    db = connect(tmp_path / "room.db")
    db.execute("INSERT OR IGNORE INTO groups (id, label, role) VALUES (?, ?, 'cloner')",
               (CLONERS_GROUP, "שיכפול"))
    db.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, 'classroom')",
               (GROUP, "כיתה"))
    db.commit()
    engine = SenderEngine(library, runner=Recorder(block=True))
    store = SessionStore(db, on_running=engine.start,
                         on_closed=lambda sid: engine.stop(sid))
    w1 = store.open(CLONERS_GROUP, IMAGE, "ROOM", 1, "", wait_seconds=300)
    db.execute("INSERT INTO room_rounds (id, image_id, target_drives, state,"
               " wave_session_id, opened_by, created_at) VALUES"
               " ('room_2427', ?, 2, 'active', ?, 'noc', ?)", (IMAGE, w1, now_iso()))
    db.commit()

    real_visible = room.visible_round
    box: dict = {}

    def visible_then_next_wave(conn):
        row = real_visible(conn)
        w2 = store.open(CLONERS_GROUP, IMAGE, "ROOM", 1, "", wait_seconds=300, replaces=w1)
        assert room.update_one(conn, "UPDATE room_rounds SET wave_session_id = ?,"
                               " wave_number = wave_number + 1"
                               " WHERE id = 'room_2427' AND state = 'active'"
                               " AND wave_session_id = ?", (w2, w1))
        conn.commit()
        box["w2"] = w2
        return row

    monkeypatch.setattr(room, "visible_round", visible_then_next_wave)
    ctx = SimpleNamespace(conn=db, store=store, sender=engine, library=library)
    room.close_round(ctx, "noc", MANIFEST_256["name"])

    assert db.execute("SELECT state FROM room_rounds WHERE id = 'room_2427'"
                      ).fetchone()["state"] == "closed"
    assert _state(db, box["w2"]) == "closed"
    # החריץ פנוי: שידור חדש נפתח.
    assert store.open(GROUP, IMAGE, "LAB1", 1, "noc", wait_seconds=300)


# --- 4 -------------------------------------------------------------------------


def test_an_exception_in_the_sender_thread_is_a_named_failure(library):
    events: list[str] = []

    def on_event(event, detail):
        if event == "send_start":
            raise sqlite3.OperationalError("database is locked")
        events.append(event)

    runner = Recorder(block=True)
    engine = SenderEngine(library, runner=runner, on_event=on_event)
    engine.start({"id": "ses_X", "image_id": IMAGE, "joined": 1})
    assert wait_for(lambda: not engine._thread.is_alive())
    status = engine.status()
    assert status["state"] == "failed", status
    assert "database is locked" in (status["error"] or "")
    assert "send_failed" in events
    assert runner.processes == []


def test_a_crash_after_udp_sender_started_reaps_it(library):
    """חריגה אחרי שהתהליך עלה אינה משאירה udp-sender יתום."""
    runner = Recorder(block=True)
    engine = SenderEngine(library, runner=runner)

    def boom(*_args, **_kwargs):
        raise RuntimeError("sampling broke")

    engine._wait_sampling = boom
    engine.start({"id": "ses_Y", "image_id": IMAGE, "joined": 1})
    assert wait_for(lambda: not engine._thread.is_alive())
    assert engine.status()["state"] == "failed"
    process: FakeProcess = runner.processes[0]
    assert process.terminated
    assert engine._process is None
