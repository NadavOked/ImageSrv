"""‏#1296 — יעד מבני לשורות היומן: ‏`journal.machine`, ‏`round_id`, ‏`class_id`.

עד כאן שורת יומן הייתה ``ts, user, event, detail`` בלבד, והסינון לפי מכונה
חיפש את ה-MAC **בטקסט**. עכשיו לכל שורה שכותבה יודע את היעד — עמודות nullable:

* ‏`machine` — ה-MAC הקנוני (כמו `machines.mac`);
* ‏`round_id` — ‏`sessions.id` (סבב, גל, משיכה) או `room_rounds.id` (סבב חדר);
* ‏`class_id` — ‏`groups.id`; **ריקה עד v2** (מיון 06/11).

‏NULL = "לא נרשם" (שורה מלפני המיגרציה, או אירוע בלי יעד כזה), לא "אף מכונה".
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from server import db
from server.agent import hello, registry, reports
from server.db import connect, journal

pytest.importorskip("fastapi")

from conftest import setup_classroom  # noqa: E402
from test_server_room import CLONER1, CLONER2, room_body, room_server  # noqa: E402,F401 (fixture)

MAC = "78:ac:c0:9b:12:96"
TARGET = ("machine", "round_id", "class_id")


def _rows(conn, event: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT detail, machine, round_id, class_id FROM journal WHERE event = ? ORDER BY id",
        (event,)).fetchall()


# --- 1. הסכמה והמיגרציה ----------------------------------------------------------


def test_a_fresh_database_has_the_three_nullable_target_columns(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    info = {r["name"]: r for r in conn.execute("PRAGMA table_info(journal)")}
    for column in TARGET:
        assert column in info, column
        assert info[column]["notnull"] == 0 and info[column]["dflt_value"] is None, column


def test_a_database_on_10_gains_the_target_and_keeps_its_rows(tmp_path):
    """שרת בשטח על סכמה 10: טבלת `journal` בלי העמודות, עם שורות. העלייה מוסיפה
    את שלושתן, השורות הישנות נשארות כפי שהיו — עם NULL, לא ערך מומצא — והוא
    נחתם 11. שורה חדשה אחרי המיגרציה נושאת יעד."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    for column in TARGET:
        raw.execute(f"ALTER TABLE journal DROP COLUMN {column}")
    raw.execute("INSERT INTO journal (ts, user, event, detail)"
                " VALUES ('2026-10-01T00:00:00+00:00', 'noc', 'machine_add', ?)", (f"{MAC} name=05",))
    raw.execute("PRAGMA user_version = 10")
    raw.commit()
    assert [r[1] for r in raw.execute("PRAGMA table_info(journal)")] == [
        "id", "ts", "user", "event", "detail"]          # באמת הסכמה הקודמת
    raw.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15   # 13: usb_records/usb_syncs (#1920), 14: disk_f1_marks (#1858), 15: net_devices.monitor_on (#2850)
    [old] = conn.execute("SELECT user, detail, machine, round_id, class_id FROM journal").fetchall()
    assert tuple(old) == ("noc", f"{MAC} name=05", None, None, None)
    journal(conn, "agent_loop", MAC, machine=MAC)
    assert tuple(_rows(conn, "agent_loop")[0]) == (MAC, MAC, None, None)


def test_the_migration_runs_once_and_twice_is_harmless(tmp_path):
    """פתיחה חוזרת של DB שכבר מוגר אינה מוסיפה עמודה שנייה ואינה נכשלת."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    conn = connect(path)
    names = [r["name"] for r in conn.execute("PRAGMA table_info(journal)")]
    assert [n for n in names if n in TARGET] == list(TARGET)


# --- 2. ‏`journal()` --------------------------------------------------------------


def test_journal_stores_the_target_and_an_empty_one_is_null(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    journal(conn, "client_done", f"{MAC} in ses_1", machine=MAC, round_id="ses_1",
            class_id="grp_LAB1")
    journal(conn, "setting_change", "x=1", "noc")
    journal(conn, "agent_loop", MAC, machine="", round_id="")
    assert tuple(_rows(conn, "client_done")[0]) == (f"{MAC} in ses_1", MAC, "ses_1", "grp_LAB1")
    assert tuple(_rows(conn, "setting_change")[0])[1:] == (None, None, None)
    assert tuple(_rows(conn, "agent_loop")[0])[1:] == (None, None, None)


def test_the_target_is_keyword_only():
    """‏`journal(conn, event, detail, user, MAC)` היה נכנס בשקט כעמודה הלא נכונה."""
    with pytest.raises(TypeError):
        journal(None, "x", "", "", MAC)  # type: ignore[misc]


def test_two_threads_each_on_its_own_connection_write_their_targets(tmp_path):
    """שני תהליכונים כותבים יומן עם יעד במקביל דרך `db.Database` — כל אחד על
    החיבור שלו (#54), ואף שורה אינה נופלת או מקבלת יעד של השני."""
    database = connect(tmp_path / "imagesrv.db")
    macs = ("aa:bb:cc:00:12:01", "aa:bb:cc:00:12:02")
    seen: dict[str, int] = {}
    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def write(mac: str) -> None:
        try:
            start.wait()
            seen[mac] = id(database.connection)
            for i in range(60):
                journal(database, "agent_loop", f"{mac} #{i}", machine=mac, round_id=f"ses_{mac[-2:]}")
        except BaseException as exc:      # noqa: BLE001 — נבדק למטה
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(m,)) for m in macs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    assert len(set(seen.values())) == 2, "שני התהליכונים חלקו חיבור"
    for mac in macs:
        rows = database.execute(
            "SELECT detail, round_id FROM journal WHERE machine = ?", (mac,)).fetchall()
        assert len(rows) == 60
        assert all(r["detail"].startswith(mac) and r["round_id"] == f"ses_{mac[-2:]}" for r in rows)


# --- 3. הכותבים ממלאים את היעד ----------------------------------------------------


def test_registering_a_machine_journals_its_mac(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    registry.add_machine(conn, MAC.upper(), "1", "grp_CLONERS", "noc")      # קנוני ביומן
    assert tuple(_rows(conn, "machine_add")[0])[1:] == (MAC, None, None)


def test_an_unknown_mac_line_names_the_machine(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    hello.journal_unknown(conn, MAC, "10.44.12.50")
    assert _rows(conn, "unknown_mac")[0]["machine"] == MAC


def test_a_member_report_carries_the_machine_and_the_round(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at, started_at) VALUES ('ses_1296',"
                 " 'grp_CLONERS', 'img', 'p', 1, 300, 'running', 't', ?, 0, ?)",
                 (db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES ('ses_1296', ?, 'writing', ?)", (MAC, db.now_iso()))
    conn.commit()
    assert reports.ingest(conn, {"session_id": "ses_1296", "mac": MAC, "state": "done",
                                 "targets": []})["ok"]
    assert tuple(_rows(conn, "client_done")[0])[1:] == (MAC, "ses_1296", None)
    # דיווח על סבב שאינו קיים — המכונה והסבב שהיא טענה, כמו בטקסט.
    reports.ingest(conn, {"session_id": "ses_gone", "mac": MAC, "state": "writing", "targets": []})
    assert tuple(_rows(conn, "report_on_closed_session")[0])[1:] == (MAC, "ses_gone", None)


def test_a_pull_open_and_close_carry_the_round(server):
    ctx = server["ctx"]
    info = setup_classroom(server)
    sid = ctx.store.open(group_id=info["group"], image_id="img_7f3a91", prefix="PULL",
                         expected_clients=1, opened_by="noc", roster=[info["mac1"]], kind="unicast")
    assert tuple(_rows(ctx.conn, "pull_open")[0])[1:] == (info["mac1"], sid, None)
    ctx.store.close(sid, "noc")
    closing = ctx.conn.execute(
        "SELECT round_id FROM journal WHERE detail = ? AND event != 'pull_open'", (sid,)).fetchall()
    assert closing and all(r["round_id"] == sid for r in closing)


def test_deleting_a_machine_journals_its_mac(server):
    admin, conn = server["admin"], server["ctx"].conn
    info = setup_classroom(server)
    assert admin.delete(f"/api/console/machines/{info['mac1']}").status_code == 200
    assert tuple(_rows(conn, "machine_delete")[0])[1:] == (info["mac1"], None, None)


def test_a_room_round_and_its_wave_carry_their_own_ids(room_server):
    opened = room_server["deploy"].post(
        "/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1"], CLONER2: ["S2"]}))
    assert opened.status_code == 200, opened.text
    round_id, wave_id = opened.json()["id"], opened.json()["wave_session_id"]
    conn = room_server["ctx"].conn
    assert round_id.startswith("room_") and wave_id.startswith("ses_")
    assert _rows(conn, "room_open")[0]["round_id"] == round_id
    assert _rows(conn, "session_open")[0]["round_id"] == wave_id


# --- 4. ה-API: ‏`GET /api/console/journal` -----------------------------------------


def test_journal_rows_expose_the_target_and_null_when_unrecorded(server):
    conn = server["ctx"].conn
    journal(conn, "agent_loop", MAC, machine=MAC, round_id="ses_1296")
    journal(conn, "setting_change", "x=1", "noc")
    r = server["admin"].get("/api/console/journal")
    assert r.status_code == 200, r.text
    by_event = {row["event"]: row for row in r.json()}
    loop, setting = by_event["agent_loop"], by_event["setting_change"]
    assert (loop["machine"], loop["round_id"], loop["class_id"]) == (MAC, "ses_1296", None)
    assert (setting["machine"], setting["round_id"], setting["class_id"]) == (None, None, None)


def test_the_machine_filter_finds_a_line_by_its_target_not_only_its_text(server):
    """שורה שהטקסט שלה אינו נושא את ה-MAC (כאן: רק שם התחנה) — נמצאת לפי היעד.
    שורה ישנה בלי יעד שה-MAC בטקסט שלה — נמצאת כמו קודם. שורה של מכונה אחרת —
    לא."""
    conn = server["ctx"].conn
    journal(conn, "gui_failed", "במחשב 05 — המסך לא עלה", machine=MAC)
    journal(conn, "agent_loop", MAC)                                  # ישנה: בלי יעד
    journal(conn, "agent_loop", "aa:bb:cc:dd:ee:ff", machine="aa:bb:cc:dd:ee:ff")
    r = server["admin"].get("/api/console/journal", params={"machine": MAC.upper()})
    assert r.status_code == 200, r.text
    got = sorted((row["event"], row["machine"]) for row in r.json())
    assert got == [("agent_loop", None), ("gui_failed", MAC)]
