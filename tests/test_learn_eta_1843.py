"""‏#1843 — זמן סיום צפוי לגל, למשיכה ולקליטה, לפי ההיסטוריה של השרת.

הכלל (נדב 19/10): השרת לומד רק ממדידה שלו, והלמידה לעולם אינה חוסמת.
בלי היסטוריה — "אין עדיין הערכה" (‏`seconds: None` + ‏`why`), לא מספר מומצא
(עיקרון 5ב). הטסטים בודקים את המספר עצמו מול חישוב יד, ואת "אין היסטוריה"
כמצב נפרד מ"נמדד".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db, learn_eta

from test_server_room import CLONER1, cloner_hello, room, room_body, room_server  # noqa: F401

GB = 10 ** 9
MAC = "78:ac:c0:9b:11:c2"
OTHER = "aa:bb:cc:00:00:32"
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagectl.db")


def _wave(conn, delivered: dict, *, wall: float, total: float, ended: str = "send_done",
          state: str = "closed", direct: bool = False) -> str:
    """גל שהסתיים: ‏`sessions` (‏started_at → ‏closed_at = ‏`total` שניות) ושורת
    ‏`wave_rates` עם `stream.wall_s` ו-`delivered` — הצורה ש-`drive_rates`
    כותב (‏`record_stream` / ‏`record_wave`)."""
    sid = f"ses_eta{next(_seq):04d}"
    start = 1_700_000_000
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at, closed_at)"
        " VALUES (?, 'grp_CLONERS', 'img_x', 'p', 1, 300, ?, 't', ?, 0, ?, ?)",
        (sid, state, iso(start), iso(start), iso(start + total)))
    roster = [{"mac": mac, "serial": f"{sid}-{mac}-{k}", "model": "M"}
              for mac, got in delivered.items() for k in range(got["drawers"])]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, delivered)"
                 " VALUES (?,?,?,?,?)",
                 (sid, json.dumps({"max_bitrate": "400m", "roster": roster,
                                   **({"direct": True} if direct else {})}),
                  iso(start), json.dumps({"wall_s": wall, "ended": ended}),
                  json.dumps(delivered)))
    conn.commit()
    return sid


def _drives(mac: str, n: int) -> list[dict]:
    return [{"mac": mac, "serial": f"new-{k}"} for k in range(n)]


# --- גל ----------------------------------------------------------------------


def test_no_history_is_no_estimate_not_a_number(conn):
    got = learn_eta.wave(conn, _drives(MAC, 2), 20 * GB)
    assert got["seconds"] is None and got["why"] == "no_history"
    assert got["missing"] == [MAC]


def test_the_estimate_is_fixed_stages_plus_size_over_goodput(conn):
    """שני גלים קודמים של 2 מגירות: ‏goodput ‏50MB/s ו-40MB/s למגירה, שלבים
    קבועים 120ש' ו-100ש'. חציון של שניים = ממוצע: ‏45MB/s, ‏110ש'.
    אימג' של 18GB → ‏110 + 18e9 / 45e6 = ‏510ש'."""
    _wave(conn, {MAC: {"drawers": 2, "done": 2, "written_bytes": 2 * 20 * GB}},
          wall=400, total=520)                         # 50MB/s, 120s
    _wave(conn, {MAC: {"drawers": 2, "done": 2, "written_bytes": 2 * 20 * GB}},
          wall=500, total=600)                         # 40MB/s, 100s
    got = learn_eta.wave(conn, _drives(MAC, 2), 18 * GB, "2026-10-21T10:00:00+00:00")
    assert got["why"] is None and got["seconds"] == 510, got
    assert got["ends_at"] == "2026-10-21T10:08:30+00:00"
    (machine,) = got["machines"]
    assert machine["waves"] == 2 and machine["drawers"] == 2
    assert machine["goodput_bps"] == 45_000_000 and machine["fixed_s"] == 110


def test_another_drawer_count_is_not_used(conn):
    """קצב של 2 מגירות אינו זמן של 4 (#1842) — אין גזירה."""
    _wave(conn, {MAC: {"drawers": 2, "done": 2, "written_bytes": 2 * 20 * GB}},
          wall=400, total=520)
    got = learn_eta.wave(conn, _drives(MAC, 4), 18 * GB)
    assert got["seconds"] is None and got["why"] == "no_history"


def test_the_slowest_machine_sets_the_wave(conn):
    _wave(conn, {MAC: {"drawers": 1, "done": 1, "written_bytes": 20 * GB},
                 OTHER: {"drawers": 1, "done": 1, "written_bytes": 20 * GB}},
          wall=400, total=400)
    _wave(conn, {OTHER: {"drawers": 1, "done": 1, "written_bytes": 20 * GB}},
          wall=1000, total=1000)
    got = learn_eta.wave(conn, _drives(MAC, 1) + _drives(OTHER, 1), 20 * GB)
    # ‏MAC: ‏50MB/s → 400ש'. ‏OTHER: חציון של 50 ו-20MB/s = 35MB/s → 571ש'.
    assert got["seconds"] == 571, got


def test_one_machine_without_history_means_no_estimate_for_the_wave(conn):
    _wave(conn, {MAC: {"drawers": 1, "done": 1, "written_bytes": 20 * GB}},
          wall=400, total=400)
    got = learn_eta.wave(conn, _drives(MAC, 1) + _drives(OTHER, 1), 20 * GB)
    assert got["seconds"] is None and got["missing"] == [OTHER]


@pytest.mark.parametrize("kw", [
    {"ended": "send_failed"},
    {"state": "failed"},
    {"direct": True},
])
def test_a_wave_that_did_not_finish_whole_is_not_learned(conn, kw):
    _wave(conn, {MAC: {"drawers": 1, "done": 1, "written_bytes": 20 * GB}},
          wall=400, total=400, **kw)
    assert learn_eta.wave(conn, _drives(MAC, 1), 20 * GB)["why"] == "no_history"


def test_a_lost_drawer_is_not_learned(conn):
    _wave(conn, {MAC: {"drawers": 2, "done": 1, "written_bytes": 20 * GB}},
          wall=400, total=400)
    assert learn_eta.wave(conn, _drives(MAC, 2), 20 * GB)["why"] == "no_history"


def test_unknown_image_size_is_said(conn):
    assert learn_eta.wave(conn, _drives(MAC, 1), None)["why"] == "no_image_size"


def test_a_broken_query_is_an_error_not_an_estimate():
    class Broken:
        def execute(self, *a):
            raise RuntimeError("db gone")
    got = learn_eta.wave(Broken(), _drives(MAC, 1), 20 * GB)
    assert got == {"seconds": None, "ends_at": None, "why": "error"}


# --- משיכה -------------------------------------------------------------------


def _pull(conn, mac, *, seconds: int, total: int, done: int = 1, state: str = "closed") -> str:
    from datetime import datetime, timedelta, timezone
    sid = f"pull_eta{next(_seq):04d}"
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at, closed_at, kind)"
        " VALUES (?, 'grp_BUILD', 'img_x', 'p', 1, 0, ?, 't', ?, 0, ?, ?, 'unicast')",
        (sid, state, start.isoformat(), start.isoformat(),
         (start + timedelta(seconds=seconds)).isoformat()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, done, bytes_written,"
                 " bytes_total, updated_at) VALUES (?, ?, 'done', ?, ?, ?, ?)",
                 (sid, mac, done, total, total, start.isoformat()))
    conn.commit()
    return sid


def test_pull_estimate_from_the_same_machine(conn):
    _pull(conn, MAC, seconds=200, total=20 * GB)       # 100MB/s
    _pull(conn, OTHER, seconds=2000, total=20 * GB)    # אחרת — לא נחשבת
    got = learn_eta.pull(conn, MAC, 30 * GB)
    assert got["seconds"] == 300 and got["pulls"] == 1, got


def test_pull_without_history_or_not_finished(conn):
    _pull(conn, MAC, seconds=200, total=20 * GB, done=0)
    _pull(conn, MAC, seconds=200, total=20 * GB, state="failed")
    assert learn_eta.pull(conn, MAC, 30 * GB)["why"] == "no_history"


# --- קליטה -------------------------------------------------------------------


class _Library:
    def __init__(self, manifests: dict):
        self.manifests = manifests

    def get(self, image_id):
        return self.manifests.get(image_id)


def _capture(conn, mac, image_id, *, seconds: int, state: str = "done"):
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    tid = f"task_eta{next(_seq):04d}"
    conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by, created_at,"
        " updated_at, started_at) VALUES (?, ?, 'capture', 'sda', ?, 'n', ?, 't', ?, ?, ?)",
        (tid, mac, image_id, state, start.isoformat(),
         (start + timedelta(seconds=seconds)).isoformat(), start.isoformat()))
    conn.commit()
    return tid


def test_capture_estimate_uses_the_used_bytes_of_the_image_it_made(conn):
    lib = _Library({"img_a": {"partitions": [{"used_bytes": 30 * GB}, {"used_bytes": 10 * GB}]}})
    _capture(conn, MAC, "img_a", seconds=800)           # 40GB / 800s = 50MB/s
    got = learn_eta.capture(conn, lib, MAC, 25 * GB, "2026-10-21T10:00:00+00:00")
    assert got["seconds"] == 500 and got["captures"] == 1, got
    assert got["ends_at"] == "2026-10-21T10:08:20+00:00"


def test_capture_with_an_unmeasured_partition_is_not_learned(conn):
    lib = _Library({"img_a": {"partitions": [{"used_bytes": 30 * GB}, {"used_bytes": None}]}})
    _capture(conn, MAC, "img_a", seconds=800)
    assert learn_eta.capture(conn, lib, MAC, 25 * GB)["why"] == "no_history"


def test_capture_before_the_partitions_are_measured(conn):
    assert learn_eta.capture(conn, _Library({}), MAC, None)["why"] == "size_unknown"


def test_the_first_report_stamps_started_at_once(tmp_path):
    """‏`tasks.started_at` — הדיווח הראשון, ולא זז בדיווחים הבאים."""
    from server import reports
    conn = db.connect(tmp_path / "x.db")
    conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES"
                 " (?, '01', 'grp_BUILD', 'x')", (MAC,))
    conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by, created_at,"
        " updated_at, token) VALUES ('t1', ?, 'capture', 'sda', 'img_a', 'n', 'pending', 't',"
        " 'x', 'x', 'tok')", (MAC,))
    conn.commit()
    token = "tok"
    row = conn.execute("SELECT * FROM tasks WHERE id = 't1'").fetchone()
    assert row["started_at"] is None
    body = {"task_id": "t1", "mac": MAC, "state": "writing",
            "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 10, "state": "writing"}]}
    first = reports.ingest(conn, body, token=token)
    assert first.get("ok"), first
    stamped = conn.execute("SELECT started_at FROM tasks WHERE id = 't1'").fetchone()["started_at"]
    assert stamped
    conn.execute("UPDATE tasks SET started_at = '2026-01-01T00:00:00+00:00' WHERE id = 't1'")
    conn.commit()
    reports.ingest(conn, body, token=token)
    assert conn.execute("SELECT started_at FROM tasks WHERE id = 't1'").fetchone()[
        "started_at"] == "2026-01-01T00:00:00+00:00"


# --- מסך החדר ----------------------------------------------------------------


def test_the_room_screen_says_no_estimate_then_the_learned_one(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1", "S2"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    fc = room(deploy)["round"]["forecast"]
    assert fc["seconds"] is None and fc["why"] == "no_history" and fc["missing"] == [CLONER1]
    # גל קודם של אותו משכפל עם 2 מגירות: 50MB/s, ‏100ש' קבועים
    _wave(ctx.conn, {CLONER1: {"drawers": 2, "done": 2, "written_bytes": 2 * 20 * GB}},
          wall=400, total=500)
    fc = room(deploy)["round"]["forecast"]
    # MANIFEST_256: ‏total_compressed_bytes = 57,982,058,496 → ‏100 + 1159.6 = 1259ש'
    assert fc["why"] is None and fc["seconds"] == 1259, fc
    assert fc["ends_at"] is None                    # הגל פתוח — עוד לא יצא


def test_the_console_shows_the_forecast_from_the_server():
    """הקונסולה מציגה את ההערכה מהשרת — ולא מחשבת אחת משלה — ו"אין עדיין הערכה"
    כשאין. בגל רץ, ‏"נותרו ~X" של #410 (הקצב שנמדד עכשיו) גובר: ההערכה מההיסטוריה
    מוצגת רק עד שהוא נמדד — כך היא מתעדכנת לפי הקצב בפועל, והשורה נשארת זהה לגואי."""
    static = Path(__file__).resolve().parents[1] / "server" / "static"
    js = (static / "console.js").read_text(encoding="utf-8")
    assert "function forecastText(f, liveEta)" in js
    assert "אין עדיין הערכת סיום" in js
    assert "forecastText(r.forecast, null)" in js                      # גל פתוח
    assert "parts.push(forecastText(r.forecast, null));   // #1843: עד שהקצב נמדד" in js  # גל רץ
    assert "forecastText(p.forecast, null)" in js                      # משיכה
    cap = (static / "capture_detail.js").read_text(encoding="utf-8")
    assert "forecastText(t.forecast, null)" in cap                     # קליטה


def test_the_overview_gives_each_running_pull_its_forecast(room_server):
    """‏`/overview.pulls[].forecast` — משיכה שרצה מקבלת את ההערכה מהמשיכות הקודמות
    של אותה מכונה; בלי היסטוריה — ‏`no_history`, לא מספר."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ctx.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at, roster_json, kind)"
        " VALUES ('pull_live', 'grp_CLONERS', 'img_7f3a91', 'p', 1, 0, 'running', 't', ?, 0, ?, ?,"
        " 'unicast')", (now, now, json.dumps([CLONER1])))
    ctx.conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                     " VALUES ('pull_live', ?, 'writing', ?)", (CLONER1, now))
    ctx.conn.commit()
    (p,) = admin.get("/api/console/overview").json()["pulls"]
    assert p["forecast"]["seconds"] is None and p["forecast"]["why"] == "no_history"
    _pull(ctx.conn, CLONER1, seconds=600, total=30 * GB)          # 50MB/s
    (p,) = admin.get("/api/console/overview").json()["pulls"]
    # MANIFEST_256: 57,982,058,496 / 50e6 = 1159ש'
    assert p["forecast"]["seconds"] == 1159, p["forecast"]
    assert p["forecast"]["ends_at"] is not None


def test_the_task_list_gives_a_running_capture_its_forecast(room_server):
    """‏`/api/console/tasks[].forecast` — קליטה שרצה; לפני שהסוכן דיווח את המחיצות
    — ‏`size_unknown`. משימה שאינה קליטה רצה — ‏None."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    for tid, state in (("t_run", "running"), ("t_done", "done")):
        ctx.conn.execute(
            "INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by,"
            " created_at, updated_at) VALUES (?, ?, 'capture', 'sda', 'img_new', 'n', ?, 't',"
            " '2026-10-21T10:00:00+00:00', '2026-10-21T10:00:00+00:00')", (tid, CLONER1, state))
    ctx.conn.commit()
    got = {t["id"]: t for t in admin.get("/api/console/tasks").json()}
    assert got["t_run"]["forecast"]["why"] == "size_unknown"
    assert got["t_done"]["forecast"] is None
