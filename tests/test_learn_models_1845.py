"""‏#1845 — מהירות לכל דגם דיסק: כתום "איטי מהרגיל לדגם" — תוספת לסף המוחלט.

הבסיס = חציון המדידות האחרונות של **דיסקים אחרים** מאותו דגם (‏`write_preflight`
של #1366). בלי בסיס — רק הסף המוחלט (‏`slow: None`). לא חוסם דבר.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db, housekeeping, learn_models, room_precheck

from test_server_room import (CLONER1, cloner_hello, heartbeat, room,  # noqa: F401
                              room_body, room_server)

MAC = "78:ac:c0:9b:11:c2"
EVO = "Samsung SSD 870 EVO 500GB"
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagectl.db")


def _m(conn, serial, mbps, model=EVO, at=None):
    conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at)"
                 " VALUES (?,?,?,?,?,?)",
                 (f"ses_m{next(_seq):05d}", serial, model, MAC, mbps, at or db.now_iso()))
    conn.commit()


def _base(conn):
    for serial, mbps in (("A", 400), ("B", 420), ("C", 380)):
        _m(conn, serial, mbps)


def test_a_disk_well_under_its_model_is_orange(conn):
    _base(conn)                                     # חציון 400
    got = learn_models.judge(conn, EVO, "X", 250)   # 250 < 0.7 × 400 = 280
    assert got["slow"] is True and got["median"] == 400 and got["samples"] == 3
    assert got["text_he"] == f"איטי מהרגיל לדגם — 250 MB/s מול חציון 400 MB/s ל-{EVO} (3 מדידות)"


def test_a_disk_near_its_model_is_not(conn):
    _base(conn)
    got = learn_models.judge(conn, EVO, "X", 300)   # 300 ≥ 280
    assert got["slow"] is False and got["text_he"] is None


def test_a_fast_absolute_number_can_still_be_slow_for_its_model(conn):
    """60MB/s עובר את הסף המוחלט (50), וכתום מול דגם שכותב 400."""
    _base(conn)
    assert learn_models.judge(conn, EVO, "X", 60)["slow"] is True


def test_no_baseline_means_only_the_absolute_threshold(conn):
    _m(conn, "A", 400)
    _m(conn, "B", 400)                              # שתי מדידות — פחות מ-MIN_SAMPLES
    got = learn_models.judge(conn, EVO, "X", 50)
    assert got["slow"] is None and got["median"] is None and got["samples"] == 2


def test_one_disk_measured_many_times_is_not_a_model_baseline(conn):
    for _ in range(5):
        _m(conn, "A", 400)
    assert learn_models.judge(conn, EVO, "X", 50)["slow"] is None


def test_a_disk_is_not_its_own_baseline(conn):
    """הדיסק האיטי עצמו, שנמדד הרבה, אינו מוריד את בסיס הדגם שלו."""
    _base(conn)
    for _ in range(6):
        _m(conn, "SLOW", 100)
    got = learn_models.judge(conn, EVO, "SLOW", None)   # המדידה האחרונה שלו — 100
    assert got["mbps"] == 100 and got["median"] == 400 and got["slow"] is True


def test_another_model_is_not_a_baseline(conn):
    for serial in ("A", "B", "C"):
        _m(conn, serial, 500, model="Other")
    assert learn_models.judge(conn, EVO, "X", 100)["slow"] is None


def test_a_disk_never_measured_is_unknown_not_fine(conn):
    _base(conn)
    got = learn_models.judge(conn, EVO, "NEW", None)
    assert got["mbps"] is None and got["slow"] is None and got["median"] == 400


def test_record_wave_takes_finished_write_checks_only(conn):
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)", (MAC, json.dumps([
        {"dev": "sda", "serial": "S1", "model": EVO}, {"dev": "sdb", "serial": "S2", "model": EVO},
        {"dev": "sdc", "serial": "S3", "model": EVO}, {"dev": "sdd", "serial": "S4"}])))
    conn.commit()
    member = {"mac": MAC, "targets_json": json.dumps([
        {"dev": "sda", "state": "done", "serial": "S1",
         "write_preflight": {"state": "ok", "mbps": 410}},
        {"dev": "sdb", "state": "failed", "write_preflight": {"state": "slow", "mbps": 29}},
        {"dev": "sdc", "state": "failed",              # נקרא בחזרה שונה — אינו קצב
         "write_preflight": {"state": "failed", "mbps": 12}},
        {"dev": "sdd", "state": "done", "write_preflight": {"state": "ok", "mbps": 300}},  # בלי דגם
    ])}
    assert learn_models.record_wave(conn, "ses_w", [member]) == 2
    got = {r["serial"]: r["mbps"] for r in conn.execute("SELECT * FROM model_speeds")}
    assert got == {"S1": 410, "S2": 29}


def test_the_precheck_warning_says_slow_for_its_model(conn):
    _base(conn)
    _m(conn, "S9", 200)                              # המדידה הקודמת של הדיסק
    disk = {"dev": "sda", "serial": "S9", "port": 1, "model": EVO}
    d = {**disk, **room_precheck.drawer_fields(conn, disk, MAC), "fresh": True,
         "model_speed": learn_models.judge(conn, EVO, "S9", None)}
    (w,) = room_precheck.warnings([{"mac": MAC, "name": "01", "joined": True,
                                    "drawer_list": [d]}], selection=False)
    assert "איטי מהרגיל לדגם — 200 MB/s" in w["text_he"] and w["model_slow"] is True


def test_housekeeping_keeps_the_baseline_and_each_disk_last(conn):
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(timespec="seconds")
    for k in range(learn_models.WINDOW + 5):
        _m(conn, "A", 400, at=old)                  # דיסק אחד, הרבה מדידות ישנות
    for k in range(learn_models.WINDOW + 5):
        _m(conn, f"N{k}", 400, model="Old", at=old)
    housekeeping.purge_tables(conn)
    n = lambda q: conn.execute(q).fetchone()[0]     # noqa: E731
    # כל דיסק של 'Old' נמדד פעם אחת — זו האחרונה שלו, ולכן נשמרת
    assert n("SELECT COUNT(*) FROM model_speeds WHERE model = 'Old'") == learn_models.WINDOW + 5
    assert n("SELECT COUNT(*) FROM model_speeds WHERE serial = 'A'") == learn_models.WINDOW


def test_the_room_learns_the_model_from_finished_waves(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 8, {CLONER1: ["S1", "S2"]})).status_code == 200
    for (first, second), speeds in ((("S1", "S2"), (400, 410)), (("S3", "S4"), (390, 120))):
        wave = cloner_hello(anon, CLONER1, [first, second])["session"]["id"]
        heartbeat(room_server)
        if room(deploy)["round"]["wave_state"] == "open":
            assert deploy.post("/api/console/room/start").status_code == 200
        assert anon.post("/api/v1/agent/progress", json={
            "session_id": wave, "mac": CLONER1, "state": "done",
            "targets": [
                {"dev": dev, "bytes_written": 100, "bytes_total": 100, "state": "done",
                 "write_preflight": {"state": "ok", "reason": "", "mbps": mbps}}
                for dev, mbps in zip(("sda", "sdb"), speeds)]}).status_code == 200
        heartbeat(room_server)
    got = {r["serial"]: r["mbps"] for r in ctx.conn.execute("SELECT * FROM model_speeds")}
    assert got == {"S1": 400, "S2": 410, "S3": 390, "S4": 120}, got
    # S4 נכנס שוב: מולו S1/S2/S3 (חציון 400) — 120 < 280 → כתום, עם המדידה הקודמת שלו
    cloner_hello(anon, CLONER1, ["S4", "S5"])
    view = room(deploy)
    by_serial = {d["serial"]: d for d in view["machines"][0]["drawer_list"]}
    assert by_serial["S4"]["model_speed"]["slow"] is True
    assert by_serial["S5"]["model_speed"]["slow"] is None      # לא נמדד מעולם
    # (האזהרה שלפני "התחל" — רק למגירה טרייה; היא נבדקת ב-test_the_precheck_warning_…)


def test_the_console_paints_the_slow_model_orange_with_the_server_text():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "server" / "static" / "console.js").read_text(
        encoding="utf-8")
    assert "(d.model_speed && d.model_speed.slow === true)) return \"warn\"" in js
    assert "if (d.model_speed && d.model_speed.text_he) parts.push(d.model_speed.text_he);" in js
