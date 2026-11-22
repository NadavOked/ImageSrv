"""#2028 — למידה אוטומטית והמלצת חיווט (מוקאפ 2028-learning, אושר 06/11).

מה שמוגן כאן: כבוי כברירת מחדל · למידה מ-2 השיכפולים הבאים בלי שום כתיבה למדידה · לא
הגיעה למספר = לא משתנה כלום ("לא נלמד") · כל שינוי עם קודם/חדש ומקור, וחוזר · שינוי ידני
גובר · המלצת חיווט מסף 2 מחשבים ו-5 שיכפולים, "חשוד" על חיבור בודד גם בלי סף · המלצה
אינה משנה מיפוי · מנהל בלבד.
"""

from __future__ import annotations

import json

import pytest

from fastapi import APIRouter

from conftest import hello_body
from fakesend import wait_for
from server import db
from server.console import journal_he, learning as console_learning
from server.brain import floorrule
from server.disks import writefloor
from server.learn import tuning, wiring
from server.rounds import chunk_rate, drive_rates, nic_live

from test_server_room import (CLONER1, CLONER2, cloner_hello, heartbeat, room,  # noqa: F401
                              room_body, room_server)

HP = "HP Compaq 8000 Elite"
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "imagesrv.db")
    connection.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'משכפלים', 'cloner')")
    connection.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '01', 'g', ?)",
                       (CLONER1, db.now_iso()))
    connection.commit()
    return connection


def _on(conn):
    db.set_setting(conn, tuning.SETTING, "true")
    tuning.settings_written(conn, {tuning.SETTING: "true"}, False, "noc")


def _wave(conn, *, rate="700m", speeds=(140, 150), ended="send_done", bps=700_000_000 // 8, direct=False,
          done=1, wall=600, sent=20 * 10 ** 9, mac=CLONER1, roster=None, finished=None, **sample):
    """גל שהסתיים, כפי שהשרת משאיר אותו: `wave_rates` (ב-`stream` רק ה-sample של מנוע החתיכות, `chunk_rate.record`),
    `model_speeds`, ושורת `session_members` של המגירות (`tuning._finished_drives`).

    #3173: udpcast נמחק, ואיתו המונים של udp-sender ברמה העליונה של `stream`. המקבילים ב-sample (`tuning._counters`):
    `bps` — מה שהשרת שידר, בבייטים לשנייה (`sent_bps` / 8); `wall` — משך המעבר הראשון (`first_pass_s`); `sent` — הבייטים
    הדחוסים ששודרו (`stream_zbytes`). `finished` — הסידוריים שהמגירה שלהם `done` עם `bytes_written` (ברירת מחדל: כולם);
    השאר נכשלו. `sample` — שדות של ה-sample עצמו (`loss_permille`, `skip`...)."""
    session = f"ses_{next(_seq):05d}"
    serials = [f"D{i}" for i in range(len(speeds))]
    finished = serials if finished is None else finished
    decision = {"max_bitrate": rate, "basis": "unmeasured", "engine": "chunk", "link": {"interface": "ens224"},
                "roster": [{"mac": mac, "serial": s, "model": "SSD"} for s in serials]
                if roster is None else roster, **({"direct": True} if direct else {})}
    stream = {tuning.CHUNK_KEY: {"asked_bps": drive_rates.parse_bitrate(rate),
                                 "sent_bps": None if bps is None else bps * 8, "stream_zbytes": sent,
                                 "first_pass_s": wall, "loss_permille": 0.0, "ended": ended, "skip": None,
                                 "teaches": True, **sample}}
    delivered = {mac: {"drawers": 2, "done": done, "written_bytes": sent}}
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, delivered)"
                 " VALUES (?,?,?,?,?)", (session, json.dumps(decision), db.now_iso(),
                                         json.dumps(stream), json.dumps(delivered)))
    for i, mbps in enumerate(speeds):
        conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at)"
                     " VALUES (?,?,?,?,?,?)", (session, f"D{i}", "SSD", mac, mbps, db.now_iso()))
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES (?, 'grp_CLONERS', 'img', 'P', 1, 60, 'closed', 'noc', ?, 0)",
                 (session, db.now_iso()))
    targets = [{"serial": s, "dev": f"sd{i}", "state": "done" if s in finished else "failed",
                "bytes_written": 18 * 10 ** 9 if s in finished else 0} for i, s in enumerate(serials)]
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, 'done', ?, ?)", (session, mac, db.now_iso(), json.dumps(targets)))
    conn.commit()
    return session


def _settings(conn):
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def test_a_database_on_11_gains_the_learning_tables_and_keeps_its_rows(tmp_path):
    """שרת קיים: הטבלאות נוספות, המתג כבוי, וההגדרות שלו לא זזות."""
    path = tmp_path / "imagesrv.db"
    conn = db.connect(path)
    db.set_setting(conn, writefloor.SETTING, "70")
    for table in ("learning_changes", "learning_waves", "learning_machines", "learning_runs"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("DELETE FROM settings WHERE key = ?", (tuning.SETTING,))
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION   # stamped to the current one (14 since round/1107c: #1920 = 13, #1858 = 14)
    assert writefloor.mbps(conn) == 70
    assert db.get_setting(conn, tuning.SETTING) == "false"
    assert tuning.status(conn)["run"] is None


# ---------- המתג ----------

def test_the_switch_is_off_by_default_and_a_wave_changes_nothing(conn):
    assert db.get_setting(conn, tuning.SETTING) == "false"
    before = _settings(conn)
    tuning.record_wave(conn, _wave(conn))
    tuning.record_wave(conn, _wave(conn))
    assert _settings(conn) == before
    assert conn.execute("SELECT COUNT(*) AS n FROM learning_waves").fetchone()["n"] == 0
    assert tuning.status(conn)["run"] is None


def test_turning_the_switch_on_opens_a_run_and_schedules_no_measurement(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    count = lambda table: ctx.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: E731
    before = count("sessions"), count("room_rounds")
    assert admin.post("/api/console/settings", json={tuning.SETTING: "true"}).status_code == 200
    run = admin.get("/api/console/learning").json()["run"]
    assert (run["state"], run["required"], run["attempts"]) == ("collecting", 2, 0)
    assert (count("sessions"), count("room_rounds")) == before, "הדלקה אינה פותחת סבב ואינה מריצה מדידה"
    # שמירה חוזרת של אותו ערך אינה מאפסת סבב שרץ
    tuning.record_wave(ctx.conn, _wave(ctx.conn))
    assert admin.post("/api/console/settings", json={tuning.SETTING: "true"}).status_code == 200
    assert admin.get("/api/console/learning").json()["run"]["attempts"] == 1


def test_turning_the_switch_off_cancels_the_run_and_leaves_the_settings(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    admin.post("/api/console/settings", json={tuning.SETTING: "true"})
    for _ in range(2):
        tuning.record_wave(ctx.conn, _wave(ctx.conn))
    learned = writefloor.mbps(ctx.conn)
    assert learned > floorrule.DEFAULT
    admin.post("/api/console/settings", json={tuning.SETTING: "true"})
    assert admin.post("/api/console/settings", json={tuning.SETTING: "false"}).status_code == 200
    tuning.record_wave(ctx.conn, _wave(ctx.conn, speeds=(400, 410)))
    assert writefloor.mbps(ctx.conn) == learned, "כיבוי: ההגדרות נשארות כפי שהן"
    assert tuning._collecting(ctx.conn) is None


# ---------- סבב למידה ----------

def test_two_clean_waves_adjust_with_previous_new_and_the_number_behind_it(conn):
    _on(conn)
    tuning.record_wave(conn, _wave(conn, speeds=(140, 150, 200)))
    assert tuning.status(conn)["run"]["state"] == "collecting"
    assert writefloor.mbps(conn) == floorrule.DEFAULT, "אחרי גל אחד לא משתנה דבר"
    tuning.record_wave(conn, _wave(conn, speeds=(140, 150, 200)))
    state = tuning.status(conn)
    assert state["run"]["state"] == "done" and state["run"]["learned"] == 2
    # חציון 150 ← 75 (חצי, הכלל של הסוכן); האיטי 140 × 0.9 = 126 גבוה יותר
    assert writefloor.mbps(conn) == 75
    assert drive_rates.learned_start(conn) == 700_000_000
    rows = {c["key"]: c for c in state["changes"]}
    floor = rows[writefloor.SETTING]
    assert (floor["previous_he"], floor["value_he"], floor["state"]) == ("50 MB/s", "75 MB/s", "applied")
    assert "150 MB/s" in floor["why_he"] and "140 MB/s" in floor["why_he"], "מספר נוקב במקורו (5ב)"
    start = rows[drive_rates.START_SETTING]
    assert (start["previous_he"], start["value_he"]) == ("600 Mb/s", "700 Mb/s")
    assert "700 Mb/s" in start["why_he"] and "2 שיכפולים" in start["why_he"]
    assert sorted(state["adjusted_keys"]) == sorted(rows)
    assert state["run"]["unchanged"] == []


def test_a_wave_that_failed_means_not_learned_and_nothing_changes(conn):
    _on(conn)
    tuning.record_wave(conn, _wave(conn))
    before = _settings(conn)
    tuning.record_wave(conn, _wave(conn, ended="send_failed"))
    state = tuning.status(conn)
    assert state["run"]["state"] == "not_learned"
    assert (state["run"]["learned"], state["run"]["required"]) == (1, 2)
    assert state["run"]["waves"][1] == {"at": state["run"]["waves"][1]["at"], "ok": False,
                                        "why_he": "השולח לא סיים את הזרם"}
    assert _settings(conn) == before, "בלי ראיה אין שינוי"
    assert state["changes"] == []
    # גל נוסף אחרי שהסבב נסגר אינו מלמד
    tuning.record_wave(conn, _wave(conn))
    assert _settings(conn) == before


def test_the_room_may_close_the_wave_before_the_sender_ended_or_after(conn):
    """הסדר אינו מובטח. מי שמגיע שני סופר, וגל שעוד לא נסגר אינו כישלון."""
    _on(conn)
    session = _wave(conn)
    stream = conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (session,)).fetchone()[0]
    conn.execute("UPDATE wave_rates SET stream = NULL WHERE session_id = ?", (session,))
    conn.commit()
    tuning.record_wave(conn, session)                 # החדר ראשון
    assert tuning.status(conn)["run"]["attempts"] == 0
    conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = ?", (stream, session))
    conn.commit()
    tuning.record_wave(conn, session)                 # השולח שני
    assert tuning.status(conn)["run"]["waves"][0]["ok"] is True
    other = _wave(conn)
    delivered = conn.execute("SELECT delivered FROM wave_rates WHERE session_id = ?", (other,)).fetchone()[0]
    conn.execute("UPDATE wave_rates SET delivered = NULL WHERE session_id = ?", (other,))
    conn.commit()
    tuning.record_wave(conn, other)                   # השולח ראשון
    assert tuning.status(conn)["run"]["attempts"] == 1
    conn.execute("UPDATE wave_rates SET delivered = ? WHERE session_id = ?", (delivered, other))
    conn.commit()
    tuning.record_wave(conn, other)                   # החדר שני
    assert tuning.status(conn)["run"]["state"] == "done"


def test_what_the_room_writes_into_stream_is_not_the_senders_end(conn):
    """#3173: the room closes first, and `drive_rates.record_wave` has already written `drives_unmeasured` into `stream`
    -- that is not the sender's end. The wave is not counted on it as "the sender did not finish the stream"; the
    sender's end (`chunk_rate.record`, then `record_wave` again) counts it, by its own evidence.
    **Negative control:** on the code before the fix the room's call records it, not ok, "השולח לא סיים את הזרם"."""
    _on(conn)
    session = _wave(conn)
    stream = conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (session,)).fetchone()[0]
    conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = ?",
                 (json.dumps({"drives_unmeasured": ["D0"]}), session))
    conn.commit()
    tuning.record_wave(conn, session)                 # the room, first
    assert tuning.status(conn)["run"]["attempts"] == 0
    conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = ?",
                 (json.dumps({"drives_unmeasured": ["D0"], **json.loads(stream)}), session))
    conn.commit()
    tuning.record_wave(conn, session)                 # the sender, second
    assert tuning.status(conn)["run"]["waves"][0]["ok"] is True


def test_a_sender_that_ended_after_the_room_closed_reaches_the_run(room_server):
    """#2028 + #3173: the wiring in `app.on_chunk_event`. The room closed the wave before the chunk engine ended it: the
    room's count found no sender end and waited. The engine's end (`send_done`) writes it (`chunk_rate.record`) and
    counts the wave. (The udpcast twin left with `on_sender_event`.)
    **Negative control:** on the code before the fix nothing counts it, and the run stays at 0 attempts."""
    admin, ctx = room_server["admin"], room_server["ctx"]
    wave = _open_wave(room_server)
    assert wait_for(lambda: drive_rates.decision(ctx.conn, wave) is not None)
    # the engine's own thread is stopped and gone first: its `send_stopped` must not be the end this test counts
    ctx.chunk.stop(wave)
    assert wait_for(lambda: ctx.chunk._thread is None or not ctx.chunk._thread.is_alive())
    ctx.conn.execute("UPDATE wave_rates SET stream = NULL WHERE session_id = ?", (wave,))
    ctx.conn.commit()
    _close_wave(room_server, wave)
    assert admin.get("/api/console/learning").json()["run"]["attempts"] == 0
    ctx.chunk.on_event("send_done", f"{wave} {{}}", wave)
    run = admin.get("/api/console/learning").json()["run"]
    assert run["attempts"] == 1, run
    assert run["waves"][0]["why_he"] != "השולח לא סיים את הזרם", run["waves"][0]


def test_a_session_that_is_not_a_wave_of_the_room_is_not_counted(conn):
    _on(conn)
    tuning.record_wave(conn, "ses_no_rate_row")
    session = _wave(conn)
    conn.execute("UPDATE wave_rates SET delivered = NULL WHERE session_id = ?", (session,))
    conn.commit()
    tuning.record_wave(conn, session)
    assert tuning.status(conn)["run"]["attempts"] == 0


def test_a_direct_wave_is_not_counted_and_a_repeat_call_counts_once(conn):
    _on(conn)
    tuning.record_wave(conn, _wave(conn, direct=True))
    assert tuning.status(conn)["run"]["attempts"] == 0
    session = _wave(conn)
    tuning.record_wave(conn, session)
    tuning.record_wave(conn, session)
    assert tuning.status(conn)["run"]["attempts"] == 1


def test_the_floor_never_disqualifies_a_disk_that_was_written_in_the_run(conn):
    """חדר מעורב: חציון 400 ודיסק של 100. חצי מהחציון היה פוסל אותו."""
    db.set_setting(conn, writefloor.SETTING, "80")
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, rate="900m", bps=112_000_000, speeds=(100, 400, 410, 420)))
    assert writefloor.mbps(conn) == 90


def test_too_few_disk_measurements_leave_the_floor(conn):
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, speeds=(300,)))
    assert writefloor.mbps(conn) == floorrule.DEFAULT
    row = next(u for u in tuning.status(conn)["run"]["unchanged"] if u["key"] == writefloor.SETTING)
    assert row["why_he"] == "אין מספיק מדידות: 2 מ-1 דיסקים"


def test_loss_above_the_limit_leaves_the_start_rate(conn):
    _on(conn)
    tuning.record_wave(conn, _wave(conn))
    tuning.record_wave(conn, _wave(conn, loss_permille=50.0))          # 5% מהחתיכות לא הגיעו
    assert drive_rates.learned_start(conn) is None
    row = next(u for u in tuning.status(conn)["run"]["unchanged"]
               if u["key"] == drive_rates.START_SETTING)
    assert "אבדן" in row["why_he"]


def test_the_start_rate_is_what_every_wave_held_not_the_cap_it_was_given(conn):
    _on(conn)
    tuning.record_wave(conn, _wave(conn, rate="800m", bps=800_000_000 // 8))
    tuning.record_wave(conn, _wave(conn, rate="800m", bps=400_000_000 // 8))   # הכוננים עיכבו
    assert drive_rates.learned_start(conn) is None, "400m לא הוכיח יותר מ-600m"
    _on(conn)
    tuning.record_wave(conn, _wave(conn, rate="650m", bps=620_000_000 // 8))
    tuning.record_wave(conn, _wave(conn, rate="900m", bps=880_000_000 // 8))
    assert drive_rates.learned_start(conn) == 650_000_000


# ---------- דיווחים שקריים (סקירת אבטחה 06/11) ----------

def _rejected(conn):
    return [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'learning_rejected'")]


def _liar(conn, *, speeds, count=2, **wave):
    """סבב מלא, והמכונה מדווחת `speeds`."""
    db.set_setting(conn, writefloor.SETTING, "100")
    _on(conn)
    for _ in range(count):
        tuning.record_wave(conn, _wave(conn, rate="900m", bps=112_000_000, **wave, speeds=speeds))


def test_a_registered_machine_that_lies_high_stops_at_what_the_server_measured(conn):
    _liar(conn, speeds=(500, 520, 540))
    assert writefloor.mbps(conn) == 112, "הזרם בשרת, לא 250 שהמכונה ביקשה"


def test_the_step_is_bounded_whatever_was_measured(conn):
    db.set_setting(conn, writefloor.SETTING, "50")
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, rate="900m", bps=500_000_000, speeds=(500, 520, 540)))
    assert writefloor.mbps(conn) == 75, "×1.5 בסבב אחד"
    assert drive_rates.learned_start(conn) == 900_000_000, "600 × 1.5, הקצב המוכח"


def test_the_learned_start_cannot_jump_past_the_step(conn):
    db.set_setting(conn, drive_rates.START_SETTING, "700")
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, rate="4000m", bps=500_000_000))
    assert drive_rates.learned_start(conn) == 1_050_000_000


def test_a_stream_that_was_not_measured_moves_no_floor(conn):
    """#3173: במנוע החתיכות זרם שלא נמדד (`sent_bps` None) אינו ראיה לגל כולו (`tuning._chunk_why`), ולכן הסבב
    אינו לומד ואינו מגיע לשאלת הסף. ב-udpcast הגל נספר והסף נשאר ("קצב הזרם לא נמדד בכל השיכפולים")."""
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, bps=None, speeds=(500, 520, 540)))
    assert writefloor.mbps(conn) == floorrule.DEFAULT
    run = tuning.status(conn)["run"]
    assert run["state"] == "not_learned" and tuning.status(conn)["changes"] == []
    assert [w["why_he"] for w in run["waves"]] == ["מנוע החתיכות: מוני הזרם לא נמדדו"] * 2


def test_a_machine_that_is_not_registered_is_not_counted(conn):
    _liar(conn, speeds=(500, 520, 540), mac="de:ad:be:ef:00:01")
    assert writefloor.mbps(conn) == 100
    assert len(_rejected(conn)) == 1 and "de:ad:be:ef:00:01" in _rejected(conn)[0]


def test_a_disk_outside_the_wave_roster_is_not_counted(conn):
    _liar(conn, speeds=(500, 520, 540), roster=[])
    assert writefloor.mbps(conn) == 100
    assert tuning.status(conn)["run"]["levels"]["disks"]["count"] == 0, "גם לא מוצג כנמדד"


def test_a_machine_with_no_finished_drawer_is_not_counted_even_in_a_good_wave(conn):
    conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02', 'g', ?)",
                 (CLONER2, db.now_iso()))
    db.set_setting(conn, writefloor.SETTING, "100")
    _on(conn)
    for _ in range(2):
        session = _wave(conn, rate="900m", bps=112_000_000, speeds=(130, 140, 150))
        conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES"
                     " (?, 'LIAR', 'SSD', ?, 5000, ?)", (session, CLONER2, db.now_iso()))
        row = conn.execute("SELECT decision, delivered FROM wave_rates WHERE session_id = ?",
                           (session,)).fetchone()
        decision, delivered = json.loads(row["decision"]), json.loads(row["delivered"])
        decision["roster"].append({"mac": CLONER2, "serial": "LIAR", "model": "SSD"})
        delivered[CLONER2] = {"drawers": 1, "done": 0, "written_bytes": 0}   # ברשימה, ולא סיימה
        conn.execute("UPDATE wave_rates SET decision = ?, delivered = ? WHERE session_id = ?",
                     (json.dumps(decision), json.dumps(delivered), session))
        conn.commit()
        tuning.record_wave(conn, session)
    # 130 × 0.9 = 117 גבוה מהזרם; החציון של הכנים: 140 ← 70, לא משתנה
    assert writefloor.mbps(conn) == 100
    assert "5000MB/s" in _rejected(conn)[0]


def test_a_number_above_what_the_link_can_carry_is_rejected(conn):
    db.set_setting(conn, writefloor.SETTING, "100")
    _on(conn)
    for _ in range(2):
        session = _wave(conn, rate="900m", bps=112_000_000, speeds=(700, 710, 720))
        for i in range(3):
            _obs(conn, CLONER1, i + 1, f"D{i}", session=session)      # 6Gb/s = 600MB/s
        tuning.record_wave(conn, session)
    assert writefloor.mbps(conn) == 100
    assert _rejected(conn)


def test_a_number_above_any_disk_is_rejected_without_a_known_link(conn):
    _liar(conn, speeds=(9500, 9600, 9700))
    assert writefloor.mbps(conn) == 100 and _rejected(conn)


def test_a_number_far_above_the_model_is_rejected(conn):
    for i in range(4):                                                # היסטוריה מחוץ לסבב
        conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES"
                     " (?, ?, 'SSD', ?, 100, ?)", (f"old{i}", f"OLD{i}", CLONER1, db.now_iso()))
    conn.commit()
    _liar(conn, speeds=(500, 520, 540))
    assert writefloor.mbps(conn) == 100 and _rejected(conn)


def test_a_low_number_only_holds_the_floor_back(conn):
    """מדידה נמוכה אינה נדחית: דיסק גוסס אמיתי הוא מי שהסף חייב להתחשב בו."""
    _liar(conn, speeds=(1, 300, 310, 320))
    assert writefloor.mbps(conn) == 100 and not _rejected(conn)


def test_reports_of_the_agent_cannot_raise_the_start_rate(conn):
    _on(conn)
    for _ in range(2):
        session = _wave(conn, rate="600m", bps=75_000_000, speeds=(9500, 9500, 9500), sent=10 ** 12)
        tuning.record_wave(conn, session)
    assert drive_rates.learned_start(conn) is None


def test_a_change_made_on_lies_is_recorded_and_can_be_reverted(conn):
    _liar(conn, speeds=(500, 520, 540))
    change = next(c for c in tuning.status(conn)["changes"] if c["key"] == writefloor.SETTING)
    assert (change["previous_he"], change["value_he"]) == ("100 MB/s", "112 MB/s")
    assert "הזרם בשרת: 112 MB/s" in change["why_he"]
    assert tuning.revert(conn, change["id"], "noc") == 1
    assert writefloor.mbps(conn) == 100


def test_learning_never_lowers_a_setting(conn):
    db.set_setting(conn, writefloor.SETTING, "200")
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, rate="400m", bps=400_000_000 // 8, speeds=(140, 150, 160)))
    assert writefloor.mbps(conn) == 200 and drive_rates.learned_start(conn) is None
    assert tuning.status(conn)["changes"] == []


def test_a_failure_inside_learning_does_not_stop_the_wave(conn, monkeypatch):
    _on(conn)
    monkeypatch.setattr(tuning, "_evidence", lambda row: 1 / 0)
    tuning.record_wave(conn, _wave(conn))     # לא זורק


def _open_wave(room_server):
    admin, deploy, anon = room_server["admin"], room_server["deploy"], room_server["anon"]
    admin.post("/api/console/settings", json={tuning.SETTING: "true"})
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    if room(deploy)["round"]["wave_state"] == "open":
        assert deploy.post("/api/console/room/start").status_code == 200
    return wave


def _close_wave(room_server, wave):
    assert room_server["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "done", "targets": [
            {"dev": "sda", "bytes_written": 100, "bytes_total": 100, "state": "done"},
            {"dev": "sdb", "bytes_written": 100, "bytes_total": 100, "state": "done"}]}).status_code == 200
    heartbeat(room_server)


def test_a_real_wave_of_the_room_reaches_the_run(room_server):
    """החיבור ל-`room._finish_wave`: השולח סיים קודם, והחדר סוגר. בלי מונים הגל נספר ואינו ראיה.
    #3173: "השולח סיים" הוא `chunk_rate.record` (מה שמנוע החתיכות כותב בסוף הגל), לא `record_stream` של udp-sender."""
    admin, ctx = room_server["admin"], room_server["ctx"]
    before = _settings(ctx.conn)
    wave = _open_wave(room_server)
    # מנוע החתיכות בוחר את הקצב (`drive_rates.decide`, שורת `wave_rates`) בתהליכון שלו, לפני שמשכפל מתייצב
    assert wait_for(lambda: drive_rates.decision(ctx.conn, wave) is not None)
    chunk_rate.record(ctx.conn, wave, {"ended": "send_done"})
    stream = ctx.conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (wave,)).fetchone()["stream"]
    assert tuning._chunk_sample(json.loads(stream))["ended"] == "send_done", stream
    _close_wave(room_server, wave)
    run = admin.get("/api/console/learning").json()["run"]
    assert run["attempts"] == 1 and run["learned"] == 0
    assert run["waves"][0]["ok"] is False and run["waves"][0]["why_he"]
    now = _settings(ctx.conn)
    now.pop(tuning.SETTING), before.pop(tuning.SETTING)
    assert now == before


# ---------- קצב הפתיחה ב-`choose` ----------

def _choose(conn, link=None):
    return drive_rates.choose(conn, [{"serial": "S", "model": "M", "mac": CLONER1}], None,
                              "ses_new", link=link)


def test_the_learned_start_opens_the_wave_and_the_ceiling_still_holds(conn):
    assert _choose(conn)["start"] == "600m" and "start_learned" not in _choose(conn)
    db.set_setting(conn, drive_rates.START_SETTING, "700")
    got = _choose(conn)
    assert got["start"] == "700m" and got["start_learned"] is True and got["max_bitrate"] == "700m"
    db.set_setting(conn, drive_rates.START_SETTING, "5000")
    assert _choose(conn)["start"] == "900m", "הגג נשאר גג"


def test_a_link_slower_than_1g_keeps_its_own_start(conn):
    db.set_setting(conn, drive_rates.START_SETTING, "700")
    link = {"interface": "eth1", "speed_mbps": 100, "why": None}
    assert _choose(conn, link)["start"] == "60m"


@pytest.mark.parametrize("bad", ["abc", "0", "600", "-700", "٧٠٠"])
def test_a_broken_learned_start_falls_back_to_the_start_rate(conn, bad):
    db.set_setting(conn, drive_rates.START_SETTING, bad)
    assert drive_rates.learned_start(conn) is None
    assert _choose(conn)["start"] == "600m"


# ---------- החזרה ושינוי ידני ----------

def _learned(conn):
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn))
    return {c["key"]: c["id"] for c in tuning.status(conn)["changes"]}


def test_revert_restores_the_previous_row_exactly(conn):
    before = _settings(conn)
    ids = _learned(conn)
    assert tuning.revert(conn, ids[drive_rates.START_SETTING], "noc") == 1
    assert drive_rates.learned_start(conn) is None
    assert drive_rates.START_SETTING not in _settings(conn), "לא הייתה שורה — אין שורה"
    assert _choose(conn)["start"] == "600m"
    assert tuning.revert(conn, None, "noc") == 1
    now = _settings(conn)
    now.pop(tuning.SETTING), before.pop(tuning.SETTING)
    assert now == before
    state = tuning.status(conn)
    assert state["adjusted_keys"] == [] and {c["state"] for c in state["changes"]} == {"reverted"}


def test_revert_of_a_value_that_moved_refuses_loudly(conn):
    ids = _learned(conn)
    db.set_setting(conn, writefloor.SETTING, "123")     # כותב שאינו `POST /settings`
    with pytest.raises(tuning.Refused) as caught:
        tuning.revert(conn, ids[writefloor.SETTING], "noc")
    assert caught.value.status == 409
    assert writefloor.mbps(conn) == 123


def test_the_write_floor_is_the_learnings_alone_and_the_operator_cannot_write_it(room_server):
    """נדב 14/11 בערב: `writecheck_min_mbps` יצא מההגדרות. הכתיבה אליו נדחית, והשינוי הנלמד נשאר ואפשר לבטל אותו."""
    admin, ctx = room_server["admin"], room_server["ctx"]
    ids = _learned(ctx.conn)
    learned = writefloor.mbps(ctx.conn)
    r = admin.post("/api/console/settings", json={writefloor.SETTING: "77"})
    assert r.status_code == 400 and r.json()["detail"] == f"הגדרה לא מוכרת: {writefloor.SETTING}"
    assert writefloor.SETTING not in admin.get("/api/console/settings").json()
    state = admin.get("/api/console/learning").json()
    assert writefloor.SETTING in state["adjusted_keys"] and drive_rates.START_SETTING in state["adjusted_keys"]
    assert writefloor.mbps(ctx.conn) == learned != 77
    url = f"/api/console/learning/changes/{ids[writefloor.SETTING]}/revert"
    assert admin.post(url).status_code == 200
    assert writefloor.mbps(ctx.conn) != learned


def test_a_later_run_replaces_and_revert_goes_back_to_the_value_before_it(conn):
    _learned(conn)
    first = writefloor.mbps(conn)
    db.set_setting(conn, tuning.SETTING, "false")
    tuning.settings_written(conn, {tuning.SETTING: "false"}, True, "noc")
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, speeds=(300, 320, 340)))
    assert writefloor.mbps(conn) == 87, "הזרם בשרת הוא התקרה"
    assert tuning.revert(conn, None, "noc") == 2
    assert writefloor.mbps(conn) == first


def test_every_learning_event_has_a_hebrew_label(conn):
    ids = _learned(conn)
    tuning.revert(conn, ids[writefloor.SETTING], "noc")
    events = {r["event"] for r in conn.execute("SELECT event FROM journal WHERE event LIKE 'learning%'")}
    assert {"learning_wave", "learning_done", "learning_changed", "learning_reverted"} <= events
    assert events <= set(journal_he.EVENTS_HE)


# ---------- הרשאות ----------

def test_learning_is_admin_only(room_server):
    deploy, admin = room_server["deploy"], room_server["admin"]
    for method, url in (("get", "/api/console/learning"),
                        ("post", "/api/console/learning/revert-all"),
                        ("post", "/api/console/learning/changes/1/revert"),
                        ("get", f"/api/console/machines/{CLONER1}/wiring")):
        assert getattr(deploy, method)(url).status_code == 403, url
    assert deploy.post(f"/api/console/machines/{CLONER1}/learning",
                       json={"action": "start"}).status_code == 403
    assert deploy.post("/api/console/settings", json={tuning.SETTING: "true"}).status_code == 403
    assert deploy.get("/api/console/machines").status_code == 403
    row = next(m for m in admin.get("/api/console/machines").json() if m["mac"] == CLONER1)
    assert row["learning"]["state"] == "offer"


def test_every_learning_route_itself_requires_an_admin():
    """בקונסולה משתמש הפצה נחסם גם לפני הנתיב, ולכן 403 לבדו אינו מוכיח את השומר של הנתיב."""
    def admin_only():
        return "noc", "admin"

    router = APIRouter()
    console_learning.register(router, object(), admin_only)
    assert len(router.routes) == 5
    for route in router.routes:
        assert admin_only in [d.call for d in route.dependant.dependencies], route.path


# ---------- מחשב חדש ----------

def _obs(conn, mac, port, serial="S", *, crc=0, link=(6.0, 6.0), cable=0, session=None):
    session = session or f"ses_o{next(_seq):05d}"
    conn.execute(
        "INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps,"
        " link_max_gbps, cable_fail, at) VALUES (?,?,?,?,?,?,?,?,?)",
        (session, mac, port, serial, crc, *(link or (None, None)), cable, db.now_iso()))
    conn.commit()
    return session


def _learning(client, mac):
    return next(m for m in client.get("/api/console/machines").json() if m["mac"] == mac)["learning"]


def test_the_offer_is_gone_once_the_connections_were_measured_twice(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    assert _learning(admin, CLONER1) == {"state": "offer", "waves": 0, "ports": 0, "required": 2}
    first = _obs(ctx.conn, CLONER1, 1)
    _obs(ctx.conn, CLONER1, 2, session=first)
    assert _learning(admin, CLONER1)["state"] == "offer"
    _obs(ctx.conn, CLONER1, 1)
    assert _learning(admin, CLONER1) == {"state": "learned", "waves": 2, "ports": 2, "required": 2}


def test_a_row_where_nothing_was_measured_does_not_teach(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    for _ in range(3):
        _obs(ctx.conn, CLONER1, 1, crc=None, link=None)
    assert _learning(admin, CLONER1)["state"] == "offer"


def test_answering_the_offer_is_recorded_and_start_turns_the_switch_on(room_server):
    """#2028, הכרעת נדב 12/11: 'הפעל למידה' מדליק את המתג הכללי ופותח סבב; 'לא עכשיו' לא נוגע בו."""
    admin, ctx = room_server["admin"], room_server["ctx"]
    url = f"/api/console/machines/{CLONER1}/learning"
    assert admin.post(url, json={"action": "dismiss"}).status_code == 200
    assert db.get_setting(ctx.conn, tuning.SETTING) == "false", "'לא עכשיו' אינו מדליק את המתג"
    assert admin.post(url, json={"action": "start"}).status_code == 200
    assert _learning(admin, CLONER1)["state"] == "started"
    assert db.get_setting(ctx.conn, tuning.SETTING) == "true", "'הפעל למידה' לא הדליק את המתג"
    assert admin.get("/api/console/learning").json()["run"]["state"] == "collecting", "המתג נדלק בלי סבב למידה"
    assert ctx.conn.execute("SELECT detail FROM journal WHERE event = 'setting_change' AND detail = ?",
                            (f"{tuning.SETTING}=true",)).fetchone() is not None
    assert admin.post(url, json={"action": "start"}).status_code == 200      # again: no second run
    assert ctx.conn.execute("SELECT COUNT(*) FROM learning_runs WHERE state = 'collecting'").fetchone()[0] == 1
    assert admin.post(url, json={"action": "dismiss"}).status_code == 200
    assert _learning(admin, CLONER1)["state"] == "dismissed"
    assert admin.post(url, json={"action": "explode"}).status_code == 400
    assert admin.post("/api/console/machines/00:00:00:00:00:99/learning",
                      json={"action": "start"}).status_code == 404
    for _ in range(2):
        _obs(ctx.conn, CLONER1, 1)
    assert _learning(admin, CLONER1)["state"] == "learned"


# ---------- המלצת חיווט ----------

def _hello(client, mac, ports, model=HP, link=(6.0, 6.0)):
    body = hello_body(mac)
    body["disks"] = [{"dev": f"sd{chr(96 + p)}", "size_bytes": 500 * 10 ** 9, "model": "SSD",
                      "serial": f"{mac[-2:]}-{p}", "removable": False, "scheme": "gpt",
                      "has_data": False, "port": p, "link_gbps": link[0], "link_max_gbps": link[1]}
                     for p in ports]
    body["schema"] = 2
    body["inventory"] = {"dmi": {"sys_vendor": "HP", "product_name": model, "product_version": None,
                                 "board_name": None}, "pci": [], "tpm": None}
    assert client.post("/api/v1/agent/hello", json=body).status_code == 200


def _wiring(client, mac):
    r = client.get(f"/api/console/machines/{mac}/wiring")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def wire_net(room_server):
    """#3600: the network as the brain measured it -- the cloner's card (`nic_live`, its hello) and the server's card (the last wave's
    decision). `put(mac, cloner_mbps, server_mbps)`; `None` = that card was not measured. Call it AFTER `_hello`: a hello without
    `nic_link` makes the cloner's speed "unknown" again."""
    conn = room_server["ctx"].conn

    def put(mac, cloner_mbps, server_mbps):
        if cloner_mbps is not None:
            nic_live.note(mac, {"iface": "eth0", "speed_mbps": cloner_mbps}, "boot-a")
        if server_mbps is not None:
            decision = {"max_bitrate": "900m", "link": {"interface": "eth1", "speed_mbps": server_mbps, "why": None, "applied": True}}
            conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                         (f"ses_net{next(_seq):05d}", json.dumps(decision), db.now_iso()))
            conn.commit()
    yield put
    nic_live.forget()


def _fleet(room_server, *, drawers=4):
    """שני משכפלים מאותו דגם, 6 גלים: SATA 0-2 נקיים בשניהם, SATA 3 רע בשניהם, SATA 4 נקי ב-CLONER2."""
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = ?", (drawers,))
    conn.commit()
    _hello(anon, CLONER1, [1, 2, 3, 4])
    _hello(anon, CLONER2, [1, 2, 3, 4, 5])
    for i in range(3):
        for mac, ports in ((CLONER1, (1, 2, 3)), (CLONER2, (1, 2, 3, 5))):
            session = f"ses_w{mac[-2:]}{i}"
            for port in ports:
                _obs(conn, mac, port, f"{mac}-{port}", session=session)
            _obs(conn, mac, 4, f"{mac}-4", crc=100, link=(3.0, 6.0), session=session)
    return admin


def test_no_recommendation_below_the_threshold_but_the_numbers_are_named(room_server):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    _hello(anon, CLONER1, [1, 2])
    _hello(anon, CLONER2, [1, 2])
    for _ in range(9):
        _obs(conn, CLONER1, 1)                 # הרבה שיכפולים, מחשב אחד
    got = _wiring(admin, CLONER1)
    assert (got["enough"], got["machines"], got["waves"]) == (False, 1, 9)
    assert (got["required_machines"], got["required_waves"]) == (2, 5)
    assert got["text_he"] is None and got["recommended"] == [] and got["moves"] == []
    for port in got["ports"].values():
        assert port["advice"] is None


def test_two_machines_with_four_waves_are_still_below_it(room_server):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    _hello(anon, CLONER1, [1])
    _hello(anon, CLONER2, [1])
    for _ in range(2):
        _obs(conn, CLONER1, 1)
        _obs(conn, CLONER2, 1)
    got = _wiring(admin, CLONER1)
    assert (got["enough"], got["machines"], got["waves"]) == (False, 2, 4)
    _obs(conn, CLONER2, 1)
    got = _wiring(admin, CLONER1)
    assert (got["enough"], got["waves"]) == (True, 5)


def test_a_suspect_connection_shows_on_one_machine_without_the_threshold(room_server):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    _hello(anon, CLONER1, [1, 2])
    _obs(conn, CLONER1, 2, crc=12, link=(3.0, 6.0))
    _obs(conn, CLONER1, 1)
    got = _wiring(admin, CLONER1)
    assert got["enough"] is False and got["text_he"] is None
    assert got["ports"]["2"]["advice"] == "avoid"
    assert (got["ports"]["2"]["crc"], got["ports"]["2"]["crc_waves"]) == (12, 1)
    assert got["ports"]["1"]["advice"] is None


def test_not_measured_is_not_clean_and_not_zero(room_server):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    _hello(anon, CLONER1, [1, 2])
    _obs(conn, CLONER1, 1, crc=None)
    got = _wiring(admin, CLONER1)
    assert got["ports"]["1"]["crc"] is None, "לא נמדד אינו 0"
    assert got["ports"]["2"]["crc"] is None and got["ports"]["2"]["observations"] == 0


def test_the_recommendation_names_ports_reason_and_its_evidence(room_server):
    """#3647: no network was measured, so no speed recommendation ("חברו את N המגירות ל-SATA ..."): only the cable's -- what to leave free, and the move."""
    admin = _fleet(room_server)
    got = _wiring(admin, CLONER1)
    assert got["enough"] is True and (got["machines"], got["waves"]) == (2, 6)
    assert got["recommended"] == [5] and 4 in got["avoid"] and got["speed"] is False, "only the target of the cable's move"
    assert got["head_he"] == f"המלצת חיווט לדגם {HP}:"
    assert got["text_he"] == ("SATA 3 יש עליו היסטוריית CRC, ולכן מומלץ להשאיר אותו פנוי."
                              " מבוסס על 2 מחשבים מהדגם, 6 שיכפולים.")
    assert got["ports"]["4"]["advice"] == "avoid"
    assert (got["ports"]["4"]["crc"], got["ports"]["4"]["crc_waves"]) == (300, 3)
    # SATA 4 ריק במחשב הזה: לא נמדד בו, והקישור בא מהדגם ונאמר כך
    assert got["ports"]["5"]["link_gbps"] is None and got["ports"]["5"]["model_link_gbps"] == 6.0
    assert got["ports"]["5"]["advice"] == "use" and got["ports"]["5"]["instead_of"] == 4
    assert got["moves"] == [{"from": 4, "to": 5, "drawer": 4}]
    assert got["move_notes_he"] == ["המגירה הרביעית מחוברת היום ל-SATA 3. מומלץ להעביר אותה ל-SATA 4."]


def test_a_port_never_measured_in_the_model_is_not_recommended(room_server):
    admin, anon = _fleet(room_server, drawers=5), room_server["anon"]
    _hello(anon, CLONER1, [1, 2, 3, 4, 6])     # SATA 5: דיסק מחובר, בלי שום תצפית
    got = _wiring(admin, CLONER1)
    assert 6 not in got["recommended"] and got["ports"]["6"]["advice"] is None
    assert "חברו" not in got["text_he"] and "נמדדו נקיים" not in got["text_he"], "#3647: no speed recommendation without a measured network"


def test_a_bad_port_on_one_machine_is_that_machine_only(room_server):
    admin, conn = _fleet(room_server), room_server["ctx"].conn
    _obs(conn, CLONER1, 2, crc=9)
    mine, other = _wiring(admin, CLONER1), _wiring(admin, CLONER2)
    assert mine["ports"]["2"]["advice"] == "avoid"
    assert other["ports"]["2"]["advice"] is None and other["avoid"] != mine["avoid"], "#3647: the other machine's port 2 is no cable's target"


def test_a_slower_port_is_not_avoided_it_gets_a_better_if_free_note_when_it_is_the_bottleneck(room_server, wire_net):
    """#3600 (Nadav 17/11, 18/11): a port that is always 3.0 is the board's, not "suspect, do not connect"; a faster FREE port is "better,
    if free" -- and only when the network that was measured (10G on both cards) is faster than the port."""
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = 2")
    conn.commit()
    _hello(anon, CLONER1, [1, 3])
    _hello(anon, CLONER2, [1, 2])
    for i in range(3):
        for mac in (CLONER1, CLONER2):
            _obs(conn, mac, 1, session=f"s{mac}{i}")
            _obs(conn, mac, 2, session=f"s{mac}{i}")
            _obs(conn, mac, 3, link=(3.0, 3.0), session=f"s{mac}{i}")   # תקרת הפורט: לא תקלה
    wire_net(CLONER1, 10000, 10000)
    got = _wiring(admin, CLONER1)
    assert got["recommended"] == [1, 2] and got["avoid"] == [], "a speed is no evidence of a cable"
    assert "איטי" not in got["text_he"] and "להשאיר אותו פנוי" not in got["text_he"], got["text_he"]
    assert got["moves"] == [], "a move is for a cable only"
    assert got["upgrades"] == [{"from": 3, "to": 2, "drawer": 2, "from_gbps": 3.0, "to_gbps": 6.0, "from_mbs": 280, "net_mbs": 1125}]
    assert got["upgrade_notes_he"] == ["המגירה השנייה מחוברת היום ל-SATA 2 (3 Gb/s), והיא מגבילה את הקצב: עד 280 MB/s מול כ-1125 MB/s שהרשת נותנת."
                                       " עדיף SATA 1 (6 Gb/s) אם הוא פנוי — זו המלצה, לא תקלה."]
    assert got["network"] == {"mbs": 1125.0, "mbps": 10000, "by": "cloner", "cloner_mbps": 10000, "server_mbps": 10000, "why": None, "margin": 1.1}
    assert got["ports"]["3"]["advice"] is None and (got["ports"]["3"]["better_port"], got["ports"]["3"]["better_gbps"]) == (2, 6.0)
    assert got["ports"]["2"]["advice"] == "use" and got["ports"]["2"]["better_port"] is None


def test_the_advice_changes_no_mapping(room_server):
    admin, conn = _fleet(room_server), room_server["ctx"].conn
    assert admin.put(f"/api/console/machines/{CLONER1}/slot-map", json={
        "scope": "machine", "slots": ["sata:1", "sata:2", "sata:3", "sata:4"]}).status_code == 200
    tables = ("machines", "model_slot_maps", "settings", "port_history")
    snapshot = lambda: {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}  # noqa: E731
    before = snapshot()
    got = _wiring(admin, CLONER1)
    assert got["moves"], "יש המלצה להעביר"
    assert snapshot() == before, "המלצה אינה נוגעת במיפוי"
    row = next(m for m in admin.get("/api/console/machines").json() if m["mac"] == CLONER1)
    assert row["drawer_slots"] == ["sata:1", "sata:2", "sata:3", "sata:4"]


def test_a_machine_without_a_model_gets_no_model_advice_but_keeps_its_suspect(room_server):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    body = hello_body(CLONER1)
    body["disks"] = []
    anon.post("/api/v1/agent/hello", json=body)
    for _ in range(6):
        _obs(conn, CLONER1, 1, crc=2)
    got = _wiring(admin, CLONER1)
    assert got["model"] is None and got["enough"] is False
    assert got["ports"]["1"]["advice"] == "avoid"


def test_wiring_of_an_unknown_machine_is_404(room_server):
    assert room_server["admin"].get("/api/console/machines/00:00:00:00:00:99/wiring").status_code == 404
    assert wiring.MIN_MACHINES == 2 and wiring.MIN_WAVES == 5


# ---------- #3443: גל חתיכות -- לומד, ורק לפי ראיה חיובית שלו ----------

#: ה-sample של גל חתיכות שלימד (`chunk_rate.sample_of` + `skip`/`teaches`): 700Mb/s שודרו 10 דקות, בלי אבדן.
CHUNK_OK = {"asked_bps": 700_000_000, "sent_bps": 700_000_000, "stream_zbytes": 52_500_000_000,
            "first_pass_s": 600, "loss_permille": 0.0, "ended": "send_done", "skip": None, "teaches": True}


def _chunk_wave(conn, *, finished=("D0", "D1"), speeds=(140, 150), done=1, **sample):
    """גל של מנוע החתיכות כפי ש-`chunk_rate.record` משאיר אותו (`_wave`), עם ה-sample `CHUNK_OK` כבסיס.
    `finished`: הסידוריים שהמגירה שלהם `done` עם `bytes_written` (`session_members.targets_json`); השאר נכשלו."""
    return _wave(conn, speeds=speeds, done=done, finished=finished, **{**CHUNK_OK, **sample})


def test_the_chunk_stream_key_is_the_one_chunk_rate_writes():
    from server.rounds import chunk_rate
    assert tuning.CHUNK_KEY == chunk_rate.STREAM_KEY


def test_two_chunk_waves_learn_like_two_udpcast_waves(conn):
    """ברזל 15/11: בשלושה סבבים ה-Samsung סיים, ו-`learning_runs` נסגרה `not_learned` כי `ok` היה 0 לכל גל חתיכות."""
    _on(conn)
    tuning.record_wave(conn, _chunk_wave(conn, speeds=(140, 150, 200), finished=("D0", "D1", "D2")))
    tuning.record_wave(conn, _chunk_wave(conn, speeds=(140, 150, 200), finished=("D0", "D1", "D2")))
    state = tuning.status(conn)
    assert (state["run"]["state"], state["run"]["learned"]) == ("done", 2)
    assert [w["ok"] for w in state["run"]["waves"]] == [True, True]
    assert writefloor.mbps(conn) == 75                          # אותו חציון כמו בגל udpcast: 150 ← 75
    assert drive_rates.learned_start(conn) == 700_000_000       # הזרם עמד ב-700m, בלי אבדן
    rows = {c["key"]: c for c in state["changes"]}
    assert "2 שיכפולים" in rows[drive_rates.START_SETTING]["why_he"]


def test_a_chunk_wave_held_back_by_the_library_disk_still_counts_and_raises_nothing(conn):
    """ברזל 15/11: `skip=server_disk` בכל שלושת הסבבים. הזרם שנמדד (263Mb/s) הוא מה שהשרת שידר: הוא התקרה, ולא מעלה."""
    _on(conn)
    db.set_setting(conn, writefloor.SETTING, "74")
    held = dict(skip="server_disk", sent_bps=263_000_000, asked_bps=892_000_000, stream_zbytes=19_725_000_000)
    for _ in range(2):
        tuning.record_wave(conn, _chunk_wave(conn, **held))
    state = tuning.status(conn)["run"]
    assert (state["state"], state["learned"]) == ("done", 2)
    assert writefloor.mbps(conn) == 74, "הזרם 32MB/s: מתחת לסף, התקרה אינה מעלה"
    assert drive_rates.learned_start(conn) is None


def test_a_wave_where_only_some_drives_finished_learns_from_those_alone(conn):
    """D1 נכשל באותה מכונה שבה D0 סיים: ה-`delivered` הוא לכל מכונה, והמדידה של D1 (999) אינה נספרת."""
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _chunk_wave(conn, speeds=(140, 999), finished=("D0",)))
    assert writefloor.mbps(conn) == floorrule.DEFAULT, "דיסק אחד שסיים: אין מספיק מדידות"
    row = next(u for u in tuning.status(conn)["run"]["unchanged"] if u["key"] == writefloor.SETTING)
    assert row["why_he"] == "אין מספיק מדידות: 2 מ-1 דיסקים"
    levels = tuning.status(conn)["run"]["levels"]["disks"]
    assert (levels["count"], levels["max_mbps"]) == (1, 140), "999 של הדיסק שנכשל אינו בשום מספר"


@pytest.mark.parametrize("sample,why", [
    ({"skip": "chunk_net_gap"}, "מנוע החתיכות: מחשב אבד באמצע המעבר הראשון"),
    ({"skip": "too_short"}, "מנוע החתיכות: הזרם קצר מדי למדידה"),
    ({"skip": "no_first_pass"}, "מנוע החתיכות: אין מעבר ראשון שנמדד"),
    ({"skip": "no_decision"}, "מנוע החתיכות: לא נבחר קצב לגל"),
    ({"skip": "something_new"}, "מנוע החתיכות: something_new"),                       # קוד לא מוכר: כמות שהוא
    ({"ended": "send_failed", "skip": "ended=send_failed"}, "השולח לא סיים את הזרם"),  # אמת: הוא באמת לא סיים
    ({"ended": "send_stopped"}, "השולח לא סיים את הזרם"),
    ({"ended": None}, "מנוע החתיכות: לא נקרא איך הזרם הסתיים"),
    ({"stream_zbytes": None}, "מנוע החתיכות: מוני הזרם לא נמדדו"),
    ({"first_pass_s": None}, "מנוע החתיכות: מוני הזרם לא נמדדו"),
    ({"sent_bps": None}, "מנוע החתיכות: מוני הזרם לא נמדדו"),
    ({"loss_permille": None}, "מנוע החתיכות: מוני הזרם לא נמדדו"),               # "לא נמדד" אינו "אפס אבדן"
    ({"first_pass_s": 10}, "הזרם קצר מדי למדידה"),
    ({"stream_zbytes": 1000}, "הזרם קצר מדי למדידה"),
])
def test_a_chunk_wave_without_positive_evidence_is_not_counted_and_says_why(conn, sample, why):
    """(neg) אין מונה = הגל לא לומד והסיבה נרשמת; שום ערך אינו מומצא."""
    _on(conn)
    tuning.record_wave(conn, _chunk_wave(conn, **sample))
    wave = tuning.status(conn)["run"]["waves"][0]
    assert wave["ok"] is False and wave["why_he"] == why


def test_a_chunk_wave_with_an_empty_sample_is_not_counted(conn):
    _on(conn)
    tuning.record_wave(conn, _chunk_wave(conn, **{k: None for k in CHUNK_OK}))
    assert tuning.status(conn)["run"]["waves"][0]["why_he"] == "מנוע החתיכות: לא נקרא איך הזרם הסתיים"


def test_a_chunk_wave_with_no_drive_that_finished_is_not_counted(conn):
    _on(conn)
    tuning.record_wave(conn, _chunk_wave(conn, finished=(), done=0))
    session = _chunk_wave(conn)
    conn.execute("UPDATE wave_rates SET delivered = ? WHERE session_id = ?",
                 (json.dumps({CLONER1: {"drawers": 2, "done": 1, "written_bytes": 0}}), session))
    conn.commit()
    tuning.record_wave(conn, session)
    assert [w["why_he"] for w in tuning.status(conn)["run"]["waves"]] == [
        "אף מגירה לא סיימה", "אף מגירה לא סיימה עם בייטים שנכתבו"]


def test_a_chunk_wave_that_lost_a_machine_teaches_no_rate_even_with_a_finished_drive(conn):
    _on(conn)
    before = _settings(conn)
    for _ in range(2):
        tuning.record_wave(conn, _chunk_wave(conn, skip="chunk_net_gap"))
    assert tuning.status(conn)["run"]["state"] == "not_learned" and _settings(conn) == before


def test_chunk_loss_above_the_limit_leaves_the_start_rate(conn):
    _on(conn)
    tuning.record_wave(conn, _chunk_wave(conn))
    tuning.record_wave(conn, _chunk_wave(conn, loss_permille=5.0))      # 0.5% > 0.1%
    assert drive_rates.learned_start(conn) is None
    row = next(u for u in tuning.status(conn)["run"]["unchanged"] if u["key"] == drive_rates.START_SETTING)
    assert "אבדן 0.50%" in row["why_he"]


def test_a_chunk_wave_with_a_finished_drive_but_no_member_rows_changes_no_floor(conn):
    """הראיה לכל דיסק היא `session_members`; בלעדיה `delivered` לבדו אינו מכניס מדידה."""
    _on(conn)
    for _ in range(2):
        session = _chunk_wave(conn)
        conn.execute("DELETE FROM session_members WHERE session_id = ?", (session,))
        conn.commit()
        tuning.record_wave(conn, session)
    assert writefloor.mbps(conn) == floorrule.DEFAULT
    assert tuning.status(conn)["run"]["levels"]["disks"]["count"] == 0
