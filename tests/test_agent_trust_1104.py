"""השרת סומך פחות על מה שמגיע מרשת ההפצה — סריקת האבטחה של 04/11.

כל פעולה נקשרת לזהות שכבר יש לשרת: ה-MAC הרשום, הכתובת שממנה הבקשה באה
בפועל (מול חכירת ה-DHCP, ‏#855), וה-session של הסבב. מה שדורש מפתח לכל
מכונה או TLS הוא #1076, ואינו כאן.

* **#2410** — גוף JSON מהסוכן נקרא עם תקרה (‏`server/agent/body.py`): 413 לפני
  הפרסור, גם בלי `Content-Length`.
* **#2416** — JSON מקונן עמוק ו-surrogate בודד: ‏400/נרמול, לא 500.
* **#2411** — ‏`GET /boot/menu?mac=` ממכונה אחרת אינו נספר בתקציב האתחולים של
  התחנה (#75), אינו רושם אותה, ואינו שולח את הפונה לסוכן.
* **#2413** — רשומת כיווץ נסגרת (ומקבלת הערה) רק בידי המכונה שפתחה אותה.
* **#2421** — חדר המשכפלים: מגירה נסגרת ("נכתב"/"בוטל"/"דולג") לפי הסידורי
  שבמלאי של המכונה המדווחת, ולא כשמכונה אחרת מחזיקה אותו סידורי.
* **#2432** — הכתובת שנרשמת למכונה (וממנה המוניטור מתחבר) היא ה-peer, לא
  ה-`ip` שבגוף ה-hello.

בקרה שלילית: כל טסט כאן נופל כשקובצי הייצור (`server/agent/api.py`,
‏`server/agent/body.py`, ‏`server/agent/hello.py`, ‏`server/disks/shrink_records.py`,
‏`server/rounds/room.py`) חוזרים ל-main ‏a000bec7 — חוץ מהבקרות לכיוון השני,
שמסומנות "בקרה".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import hello_body, setup_classroom
from server import db
from server.agent.bootguard import ATTEMPT_LIMIT
from test_machine_identity import (BUILD, MAC1, STATION_IP, STRANGER_IP,  # noqa: F401
                                   identity_server, journal_details)
from test_shrink_records import SERIAL, layout

#: 1MiB ועוד — מעל התקרה של `body.MAX_BYTES`, ומתחת לכל מה שהשרת חשש ממנו.
OVER_CAP = 1024 * 1024 + 1

AGENT_JSON_PATHS = [
    "/api/v1/agent/hello", "/api/v1/agent/login", "/api/v1/agent/pulls",
    "/api/v1/agent/progress", "/api/v1/agent/disk-event", "/api/v1/agent/local-clone",
    "/api/v1/agent/shrink-open", "/api/v1/agent/shrink-close", "/api/v1/agent/shrink-note",
]


def _lenient(server) -> TestClient:
    """לקוח שמחזיר 500 כתשובה ולא כחריגה — כך נראה הכשל בבקרה השלילית."""
    return TestClient(server["app"], raise_server_exceptions=False)


def _devices(server) -> int:
    return server["ctx"].conn.execute("SELECT COUNT(*) AS n FROM net_devices").fetchone()["n"]


# --- #2410: תקרת גודל לגוף ---------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/agent/hello", "/api/v1/agent/progress"])
def test_a_body_over_the_cap_is_413_and_records_nothing(server, path):
    """שדה לא מוכר (`padding`) — מחוץ לתקרות השדות של #1127, ולכן עד כאן הגוף
    נקרא ונפרס בשלמותו."""
    body = {**hello_body("aa:bb:cc:11:22:33"), "padding": "x" * OVER_CAP}
    response = _lenient(server).post(path, json=body)
    assert response.status_code == 413, response.text
    assert response.json() == {"ok": False, "error": "request body exceeds the size cap",
                               "code": "payload_too_large"}
    assert _devices(server) == 0


def test_a_chunked_body_over_the_cap_is_413_too(server):
    """בלי `Content-Length` (chunked): המונה בזמן הקריאה, לא הכותרת, הוא השער."""
    raw = json.dumps({**hello_body("aa:bb:cc:11:22:33"), "padding": "x" * OVER_CAP}).encode()

    def chunks():
        for i in range(0, len(raw), 64 * 1024):
            yield raw[i:i + 64 * 1024]

    response = _lenient(server).post("/api/v1/agent/hello", content=chunks(),
                                     headers={"Content-Type": "application/json"})
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 413, response.text
    assert _devices(server) == 0


def test_a_normal_hello_is_untouched_by_the_cap(server):
    """בקרה: hello אמיתי — קילובייטים בודדים — עובר ונרשם כמו תמיד."""
    response = server["anon"].post("/api/v1/agent/hello", json=hello_body("aa:bb:cc:11:22:33"))
    assert response.status_code == 200 and response.json()["known"] is False
    assert _devices(server) == 1


# --- #2416: עומק ו-surrogate -------------------------------------------------


@pytest.mark.parametrize("path", AGENT_JSON_PATHS)
def test_json_nested_ten_thousand_deep_is_400_not_500(server, path):
    response = _lenient(server).post(path, content=b"[" * 10_000 + b"]" * 10_000,
                                     headers={"Content-Type": "application/json"})
    assert response.status_code == 400, (path, response.status_code, response.text[:200])
    assert response.json()["code"] == "bad_json"


def test_json_deeper_than_the_cap_but_parseable_is_400(server):
    """עומק 40 נפרס בלי RecursionError — ועדיין נדחה, לפני שקוד רקורסיבי בהמשך
    (‏`hello._json_depth`) מגיע אליו."""
    deep: object = "x"
    for _ in range(40):
        deep = [deep]
    body = {**hello_body("aa:bb:cc:11:22:33"), "inventory": deep}
    response = _lenient(server).post("/api/v1/agent/hello", json=body)
    assert response.status_code == 400 and response.json()["code"] == "bad_json"


def test_a_lone_surrogate_in_the_prompt_is_stored_replaced_not_a_500(server):
    """‏`"\\ud800bad"` — אין לו קידוד UTF-8, ו-sqlite נפל עליו. עכשיו: 200, והשאלה
    נשמרת עם `?` במקום התו (‏`errors="replace"`)."""
    mac = "aa:bb:cc:11:22:33"
    body = {**hello_body(mac), "waiting_for": "operator", "prompt": "\ud800bad"}
    raw = json.dumps(body).encode("ascii")            # ‏`\ud800` כ-escape, כמו על הכבל
    assert b"\\ud800bad" in raw
    response = _lenient(server).post("/api/v1/agent/hello", content=raw,
                                     headers={"Content-Type": "application/json"})
    assert response.status_code == 200, response.text
    row = server["ctx"].conn.execute("SELECT prompt FROM net_devices WHERE mac = ?",
                                     (mac,)).fetchone()
    assert row["prompt"] == "?bad"


# --- #2411: תפריט האתחול ממכונה אחרת -----------------------------------------


def _open_round(server) -> dict:
    ids = setup_classroom(server)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2})
    assert response.status_code == 200, response.text
    assert ids["mac1"] == MAC1
    return ids


def _attempts(server, mac: str) -> int:
    row = server["ctx"].conn.execute(
        "SELECT attempts FROM boot_attempts WHERE mac = ?", (mac,)).fetchone()
    return int(row["attempts"]) if row else 0


def test_a_stranger_cannot_spend_a_stations_boot_budget(identity_server):
    """המעבדה, 04/11: ‏identity_refused נרשם, ובכל זאת attempts הגיע ל-5. עכשיו
    הבקשות של הזר אינן נספרות, והתחנה מקבלת את הסבב באתחול שלה."""
    _open_round(identity_server)
    for _ in range(ATTEMPT_LIMIT + 2):
        text = identity_server["stranger"].get(f"/boot/menu?mac={MAC1}").text
        assert "set default=local" in text            # הזר אינו נשלח לסוכן
    assert _attempts(identity_server, MAC1) == 0
    assert journal_details(identity_server, "boot_loop_local") == []
    own = identity_server["station"].get(f"/boot/menu?mac={MAC1}").text
    assert "set default=imagesrv" in own
    assert _attempts(identity_server, MAC1) == 1


def test_a_stranger_menu_does_not_record_the_station(identity_server):
    _open_round(identity_server)
    identity_server["stranger"].get(f"/boot/menu?mac={MAC1}")
    row = identity_server["ctx"].conn.execute(
        "SELECT ip FROM net_devices WHERE mac = ?", (MAC1,)).fetchone()
    assert row is None


def test_an_unverifiable_station_is_not_sent_to_the_agent_without_a_count(identity_server):
    """עיקרון 1: ‏MAC2 אין לו חכירה (‏`identity_unverifiable`). בלי הסימון, תפריט
    שאינו נספר היה שולח אותו לסוכן שוב ושוב — ‏hello נדחה, אתחול, וחוזר חלילה."""
    ids = _open_round(identity_server)
    for _ in range(ATTEMPT_LIMIT + 2):
        assert "set default=local" in identity_server["station"].get(
            f"/boot/menu?mac={ids['mac2']}").text
    assert _attempts(identity_server, ids["mac2"]) == 0


def test_the_station_itself_is_still_counted(identity_server):
    """בקרה: השומר של #75 עדיין סופר את התחנה עצמה, ויורד לדיסק אחרי התקציב."""
    _open_round(identity_server)
    for attempt in range(1, ATTEMPT_LIMIT + 1):
        assert "set default=imagesrv" in identity_server["station"].get(
            f"/boot/menu?mac={MAC1}").text
        assert _attempts(identity_server, MAC1) == attempt
    assert "set default=local" in identity_server["station"].get(f"/boot/menu?mac={MAC1}").text


# --- #2413: רשומת כיווץ שייכת למכונה שפתחה אותה -------------------------------


def test_another_machine_cannot_close_or_note_a_shrink_record(identity_server):
    """‏MAC1 ו-BUILD חכורים שניהם ל-STATION_IP — שתיהן עוברות את שער הזהות.
    המעבדה, 04/11: תחנה 2 סגרה את הרשומה של תחנה 1 וקיבלה 200."""
    station = identity_server["station"]
    opened = station.post("/api/v1/agent/shrink-open", json=layout(mac=BUILD))
    assert opened.status_code == 200, opened.text
    rid = opened.json()["id"]

    close = station.post("/api/v1/agent/shrink-close",
                         json={"mac": MAC1, "serial": SERIAL, "id": rid})
    assert close.status_code == 403 and close.json()["code"] == "not_owner", close.text
    note = station.post("/api/v1/agent/shrink-note",
                        json={"mac": MAC1, "serial": SERIAL, "id": rid, "note": "x"})
    assert note.status_code == 403 and note.json()["code"] == "not_owner", note.text
    row = identity_server["ctx"].conn.execute(
        "SELECT closed_at, note FROM shrink_records WHERE id = ?", (rid,)).fetchone()
    assert row["closed_at"] is None and row["note"] is None
    assert len(journal_details(identity_server, "shrink_refused")) == 2


def test_the_machine_that_opened_it_still_closes_it(identity_server):
    """בקרה: הבעלים סוגר כמו תמיד, וסגירה שנייה היא 404 (עיקרון 5)."""
    station = identity_server["station"]
    rid = station.post("/api/v1/agent/shrink-open", json=layout(mac=BUILD)).json()["id"]
    assert station.post("/api/v1/agent/shrink-note", json={
        "mac": BUILD, "serial": SERIAL, "id": rid, "note": "ntfsresize"}).status_code == 200
    assert station.post("/api/v1/agent/shrink-close", json={
        "mac": BUILD, "serial": SERIAL, "id": rid}).status_code == 200
    again = station.post("/api/v1/agent/shrink-close", json={"mac": BUILD, "serial": SERIAL})
    assert again.status_code == 404 and again.json()["code"] == "not_open"


# --- #2432: הכתובת שנרשמת היא ה-peer ----------------------------------------


def test_the_monitor_target_is_the_peer_not_the_ip_in_the_hello(identity_server):
    from server.console import monitor

    assert identity_server["admin"].post("/api/console/machines", json={
        "mac": BUILD, "name": "build-01", "group_id": "grp_BUILD"}).status_code == 200
    body = {**hello_body(BUILD), "ip": "10.66.6.6",
            "monitor_secret": "00112233445566778899aabbccddeeff", "monitor_auth": "hmac"}
    assert identity_server["station"].post("/api/v1/agent/hello", json=body).status_code == 200
    ip, role, _secret, _auth = monitor._target(identity_server["ctx"], BUILD)
    assert (ip, role) == (STATION_IP, "build")


# --- #2421: חדר המשכפלים — מגירה לפי המלאי של המדווח --------------------------

A, B = "aa:aa:aa:aa:aa:01", "bb:bb:bb:bb:bb:02"
S_A, S_B = "S3N-A-0001", "S3N-B-0002"


def _iso(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat(timespec="seconds")


@pytest.fixture()
def room_db(tmp_path: Path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at)"
        " VALUES ('w1', 'grp_CLONERS', 'img_7f3a91', 'ROOM', 1, 60, 'running', 't', ?, 0, ?)",
        (_iso(-600), _iso(-600)))
    conn.commit()
    return conn


def _inventory(conn, mac: str, serial: str, port: int, last_seen: str | None) -> None:
    conn.execute("INSERT OR REPLACE INTO net_devices (mac, last_seen, disks_json) VALUES (?,?,?)",
                 (mac, last_seen, json.dumps([{"dev": "sda", "port": port, "serial": serial,
                                               "smart": "ok"}])))


def _tally(conn, targets: list[dict], b_selected: bool = True):
    from server.rounds import room

    slots = [{"mac": A, "ports": [1]}] + ([{"mac": B, "ports": [2]}] if b_selected else [])
    conn.execute("DELETE FROM room_rounds")
    conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json, state,"
        " wave_session_id, opened_by, created_at) VALUES ('room_1', 'img_7f3a91', 2, ?,"
        " 'active', 'w1', 't', ?)", (json.dumps(slots), _iso(-700)))
    conn.execute("DELETE FROM session_members")
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES ('w1', ?, 'done', ?, ?)", (A, _iso(), json.dumps(targets)))
    conn.commit()
    round_row = conn.execute("SELECT * FROM room_rounds").fetchone()
    members = conn.execute("SELECT * FROM session_members").fetchall()
    return room._tally(conn, round_row, members)


def _conflicts(conn) -> list[str]:
    return [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_drawer_serial_conflict'")]


@pytest.mark.parametrize("b_seen", [_iso(-10), None], ids=["b-awake", "b-asleep-but-selected"])
def test_done_on_a_serial_another_machine_holds_is_not_counted(room_db, b_seen):
    """ה-PoC של 04/11: תחנה A מכריזה ב-hello שלה על הסידורי של המגירה של B
    (או שני כוננים עם סידורי זהה מהמפעל), ומדווחת `done`. המגירה של B לא נכתבה."""
    _inventory(room_db, A, S_B, 1, _iso(-5))
    _inventory(room_db, B, S_B, 2, b_seen)
    total, written, _skipped, _cancelled = _tally(
        room_db, [{"dev": "sda", "state": "done", "serial": S_B}])
    assert (total, written) == (0, set())
    assert _conflicts(room_db) == [f"room_1 {A} sda serial={S_B} also on {B}"]


def test_a_drawer_that_moved_from_a_machine_that_is_off_and_not_chosen_still_counts(room_db):
    """בקרה: המלאי של B ישן (מלפני הגל) ו-B אינו בסבב — המגירה עברה ל-A ונכתבה שם."""
    _inventory(room_db, A, S_B, 1, _iso(-5))
    _inventory(room_db, B, S_B, 2, _iso(-3600))
    total, written, _s, _c = _tally(room_db, [{"dev": "sda", "state": "done", "serial": S_B}],
                                    b_selected=False)
    assert (total, written) == (1, {S_B})
    assert _conflicts(room_db) == []


def test_a_normal_done_counts(room_db):
    """בקרה: כל מכונה עם המגירה שלה — נספר."""
    _inventory(room_db, A, S_A, 1, _iso(-5))
    _inventory(room_db, B, S_B, 2, _iso(-5))
    total, written, _s, _c = _tally(room_db, [{"dev": "sda", "state": "done", "serial": S_A}])
    assert (total, written) == (1, {S_A})


def test_cancelled_with_another_machines_serial_closes_nothing(room_db):
    """‏`cancelled` נשא סידורי שהתחנה בחרה. עכשיו הסידורי הוא זה שבמלאי של המדווחת."""
    _inventory(room_db, A, S_A, 1, _iso(-5))
    _inventory(room_db, B, S_B, 2, _iso(-5))
    _total, _w, _s, cancelled = _tally(
        room_db, [{"dev": "sda", "state": "cancelled", "serial": S_B}])
    assert cancelled == set()


def test_cancelled_on_its_own_drawer_still_closes_it(room_db):
    """בקרה: ביטול ממסך המשכפל עצמו (#1386, בלי בקשה בשרת) — המגירה שלו נסגרת."""
    _inventory(room_db, A, S_A, 1, _iso(-5))
    _inventory(room_db, B, S_B, 2, _iso(-5))
    _total, _w, _s, cancelled = _tally(room_db, [{"dev": "sda", "state": "cancelled"}])
    assert cancelled == {S_A}
