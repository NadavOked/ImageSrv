"""‏#1844 — מגמת CRC/קישור לכל חיבור SATA: "כנראה כבל" מול "הדיסק".

אותו חיבור שבעייתי עם ≥2 דיסקים שונים — החיבור. אותו דיסק שבעייתי ב-≥2
חיבורים — הדיסק. "לא נמדד" אינו בעיה ואינו "תקין". הלמידה אינה חוסמת —
המלצה כתומה בלבד.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.rounds import room_precheck
from server.system import housekeeping
from server.learn import learn_ports

from test_server_room import (CLONER1, CLONER2, cloner_hello, heartbeat, room,  # noqa: F401
                              room_body, room_server)

MAC = "78:ac:c0:9b:11:c2"
OTHER = "aa:bb:cc:00:00:32"
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _obs(conn, mac, port, serial, *, crc=None, link=None, cable=0, at=None):
    """תצפית אחת, כפי ש-`record_wave` כותב אותה."""
    conn.execute(
        "INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps,"
        " link_max_gbps, cable_fail, at) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"ses_p{next(_seq):05d}", mac, port, serial, crc, *(link or (None, None)), cable,
         at or db.now_iso()))
    conn.commit()


def test_one_connection_with_two_different_bad_disks_is_the_cable(conn):
    _obs(conn, MAC, 1, "S1", crc=4)
    _obs(conn, MAC, 1, "S2", link=(1.5, 6.0))
    got = learn_ports.verdict(conn, MAC, 1, "S9")
    assert got["port_suspect"] is True and got["port_disks"] == 2
    assert got["disk_suspect"] is False
    assert got["text_he"] == learn_ports.PORT_HE.format(n=2)
    assert "כנראה כבל/מחבר, לא הדיסק" in got["text_he"]


def test_the_same_disk_twice_on_one_connection_is_not_the_cable(conn):
    _obs(conn, MAC, 1, "S1", crc=4)
    _obs(conn, MAC, 1, "S1", crc=2)
    got = learn_ports.verdict(conn, MAC, 1, "S1")
    assert got["port_suspect"] is False and got["port_disks"] == 1
    assert got["text_he"] is None


def test_one_disk_bad_in_two_connections_is_the_disk(conn):
    _obs(conn, MAC, 1, "S1", crc=3)
    _obs(conn, OTHER, 2, "S1", cable=1)
    got = learn_ports.verdict(conn, MAC, 3, "S1")
    assert got["disk_suspect"] is True and got["disk_ports"] == 2
    assert got["port_suspect"] is False
    assert got["text_he"] == learn_ports.DISK_HE.format(n=2)


def test_not_measured_is_not_a_problem(conn):
    """‏crc_delta ‏None וקישור שלא נקרא — אינם ראיה לבעיה (עיקרון 5)."""
    _obs(conn, MAC, 1, "S1", crc=None, link=(None, 6.0))
    _obs(conn, MAC, 1, "S2", crc=0, link=(6.0, 6.0))
    got = learn_ports.verdict(conn, MAC, 1, None)
    assert got["port_suspect"] is False and got["port_disks"] == 0
    assert got["disk_suspect"] is None             # לא נשאל על דיסק


def test_only_the_last_window_counts(conn):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    _obs(conn, MAC, 1, "S1", crc=4, at=old)
    for k in range(learn_ports.WINDOW):
        _obs(conn, MAC, 1, f"ok{k}", crc=0)
    _obs(conn, MAC, 1, "S2", crc=4)
    got = learn_ports.verdict(conn, MAC, 1, None)
    assert got["port_disks"] == 1 and got["port_suspect"] is False


def test_a_broken_query_is_unknown_not_clean():
    class Broken:
        def execute(self, *a):
            raise RuntimeError("db gone")
    got = learn_ports.verdict(Broken(), MAC, 1, "S1")
    assert got["port_suspect"] is None and got["disk_suspect"] is None


def test_record_wave_reads_crc_delta_link_and_cable_failures(conn):
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)", (MAC, json.dumps([
        {"dev": "sda", "serial": "S1", "port": 1, "link_gbps": 3.0, "link_max_gbps": 6.0},
        {"dev": "sdb", "serial": "S2", "port": 2},
        {"dev": "sdc", "serial": "S3"},                      # בלי חיבור — לא נלמד
    ])))
    conn.execute("INSERT INTO disk_failures (session_id, mac, dev, serial, port, at, cause)"
                 " VALUES ('ses_w', ?, 'sdb', 'S2', 2, 'x', 'cable')", (MAC,))
    conn.commit()
    member = {"mac": MAC, "targets_json": json.dumps([
        {"dev": "sda", "state": "done", "serial": "S1", "crc_delta": 2},
        {"dev": "sdb", "state": "failed"},
        {"dev": "sdc", "state": "done", "serial": "S3", "crc_delta": 1},
    ])}
    assert learn_ports.record_wave(conn, "ses_w", [member]) == 2
    rows = {r["port"]: dict(r) for r in conn.execute("SELECT * FROM port_history")}
    assert rows[1]["serial"] == "S1" and rows[1]["crc_delta"] == 2
    assert rows[1]["link_gbps"] == 3.0 and rows[1]["link_max_gbps"] == 6.0
    assert rows[1]["cable_fail"] == 0
    assert rows[2]["serial"] == "S2" and rows[2]["crc_delta"] is None
    assert rows[2]["link_gbps"] is None and rows[2]["cable_fail"] == 1


def test_a_disk_swapped_since_the_wave_is_not_learned(conn):
    """הסידורי שדווח ב-done אינו מה שב-hello עכשיו — אין ייחוס לחיבור."""
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)",
                 (MAC, json.dumps([{"dev": "sda", "serial": "NEW", "port": 1}])))
    conn.commit()
    member = {"mac": MAC, "targets_json": json.dumps(
        [{"dev": "sda", "state": "done", "serial": "OLD", "crc_delta": 2}])}
    assert learn_ports.record_wave(conn, "ses_x", [member]) == 0


def test_the_precheck_warning_says_cable_in_hebrew(conn):
    _obs(conn, MAC, 1, "S1", crc=4)
    _obs(conn, MAC, 1, "S2", crc=1)
    disk = {"dev": "sda", "serial": "S9", "port": 1, "smart": "ok"}
    fields = room_precheck.drawer_fields(conn, disk, MAC)
    assert fields["port_suspect"] is True and fields["disk_suspect"] is False
    machines = [{"mac": MAC, "name": "01", "joined": True,
                 "drawer_list": [{**disk, **fields, "fresh": True, "selected": True}]}]
    (w,) = room_precheck.warnings(machines, selection=False)
    assert "כנראה כבל/מחבר, לא הדיסק" in w["text_he"] and w["port_suspect"] is True


def test_housekeeping_keeps_the_window(conn):
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(timespec="seconds")
    for k in range(learn_ports.WINDOW + 5):
        _obs(conn, MAC, 1, f"S{k}", crc=1, at=old)
    housekeeping.purge_tables(conn)
    left = conn.execute("SELECT COUNT(*) AS n FROM port_history").fetchone()["n"]
    # כל סידורי הוא תצפית אחת שלו — ולכן נשמר לפי הכלל "WINDOW לכל דיסק".
    assert left == learn_ports.WINDOW + 5
    for k in range(learn_ports.WINDOW + 5):
        _obs(conn, OTHER, 2, "SAME", crc=1, at=old)
    housekeeping.purge_tables(conn)
    same = conn.execute("SELECT COUNT(*) AS n FROM port_history WHERE serial = 'SAME'"
                        ).fetchone()["n"]
    assert same == learn_ports.WINDOW


def test_the_room_learns_from_finished_waves_and_tells_the_next_one(room_server):
    """מקצה לקצה: שני גלים, בכל אחד דיסק אחר בחיבור 1 עם CRC שעלה. בגל השלישי
    הדיסק החדש בחיבור 1 מקבל "כנראה כבל/מחבר" — ב-`drawer_list` ובאזהרה."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 8, {CLONER1: ["S1", "S2"]})).status_code == 200
    for first, second in (("S1", "S2"), ("S3", "S4")):
        wave = cloner_hello(anon, CLONER1, [first, second])["session"]["id"]
        heartbeat(room_server)
        if room(deploy)["round"]["wave_state"] == "open":
            assert deploy.post("/api/console/room/start").status_code == 200
        assert anon.post("/api/v1/agent/progress", json={
            "session_id": wave, "mac": CLONER1, "state": "done",
            "targets": [
                {"dev": "sda", "bytes_written": 100, "bytes_total": 100, "state": "done",
                 "crc_delta": 7},
                {"dev": "sdb", "bytes_written": 100, "bytes_total": 100, "state": "done",
                 "crc_delta": 0},
            ]}).status_code == 200
        heartbeat(room_server)
    got = {(r["port"], r["serial"]): r["crc_delta"]
           for r in ctx.conn.execute("SELECT * FROM port_history")}
    assert got == {(1, "S1"): 7, (2, "S2"): 0, (1, "S3"): 7, (2, "S4"): 0}, got
    cloner_hello(anon, CLONER1, ["S5", "S6"])
    view = room(deploy)
    assert view["round"]["wave_state"] == "open"    # 2 מוכנות מול 4 שנותרו — ממתין
    by_port = {d["port"]: d for d in view["machines"][0]["drawer_list"]}
    assert by_port[1]["port_suspect"] is True and by_port[2]["port_suspect"] is False
    texts = [w["text_he"] for w in view["round"]["precheck"]]
    assert any("כנראה כבל/מחבר, לא הדיסק" in t for t in texts), texts


def test_the_console_paints_the_learned_connection_orange_with_the_server_text():
    """כתום (אזהרה, לא אדום) ובטקסט מהשרת — הקונסולה אינה מחשבת הכרעה משלה."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "server" / "static" / "console.js").read_text(
        encoding="utf-8")
    line = next(x for x in js.splitlines() if "function slotClass" not in x
                and "d.port_suspect === true || d.disk_suspect === true" in x)
    assert 'return "warn"' in line
    assert "if (d.learned_he) parts.push(d.learned_he);" in js
