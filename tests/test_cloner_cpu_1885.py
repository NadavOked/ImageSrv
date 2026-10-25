"""‏#1885 — מעבד, throttle וטמפרטורה במשכפל בזמן גל. גל עם throttle שעלה לא נכנס ללמידה.

הסוכן שולח `cpu` פעם אחת — בדיווח הסופי — ובינתיים רק קורא את החום מקומית. מקור שאינו קיים = ‏null,
לא 0 ("אין throttle" ≠ "לא נקרא"). בשרת: שמירה, יומן, ודילוג בלמידת הקצב (#1842) על הגל הזה למכונה הזו.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import cloner_cpu, db, drive_rates, reports

MAC = "78:ac:c0:9b:18:85"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagectl.db")


def test_the_field_is_kept_only_as_measured_numbers():
    assert cloner_cpu.well_formed(None) is None
    got = cloner_cpu.well_formed({"busy_pct": 87, "iowait_pct": 101, "throttle_delta": True,
                                  "temp_max_c": "80", "seconds": 400, "extra": 1})
    assert got == {"busy_pct": 87, "iowait_pct": None, "throttle_delta": None, "temp_max_c": None,
                   "seconds": 400}
    assert cloner_cpu.throttled({"throttle_delta": 3}) and not cloner_cpu.throttled({"throttle_delta": 0})
    assert not cloner_cpu.throttled({"throttle_delta": None}) and not cloner_cpu.throttled(None)


def _session(conn, sid):
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at, started_at) VALUES (?, 'grp_CLONERS',"
                 " 'img', 'p', 1, 300, 'running', 't', ?, 0, ?)", (sid, db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES (?, ?,"
                 " 'writing', ?)", (sid, MAC, db.now_iso()))
    conn.commit()


def test_the_final_report_stores_it_and_a_throttle_is_journaled(conn):
    _session(conn, "ses_c1")
    reports.ingest(conn, {"session_id": "ses_c1", "mac": MAC, "state": "writing", "targets": []})
    cpu = {"busy_pct": 97, "iowait_pct": 2, "throttle_delta": 41, "temp_max_c": 96, "seconds": 410}
    assert reports.ingest(conn, {"session_id": "ses_c1", "mac": MAC, "state": "done",
                                 "targets": [], "cpu": cpu})["ok"]
    row = conn.execute("SELECT cpu_json FROM session_members").fetchone()
    assert json.loads(row["cpu_json"]) == cpu
    [line] = conn.execute("SELECT detail FROM journal WHERE event = 'cloner_throttled'").fetchall()
    assert line["detail"].startswith(f"{MAC} in ses_c1: throttle +41, max 96°C") and "אוורור" in line["detail"]
    assert cloner_cpu.history(conn, MAC)[0]["throttled"] is True


def test_no_throttle_and_an_unread_throttle_are_silent(conn):
    for sid, delta in (("ses_c2", 0), ("ses_c3", None)):
        _session(conn, sid)
        reports.ingest(conn, {"session_id": sid, "mac": MAC, "state": "done", "targets": [],
                              "cpu": {"throttle_delta": delta}})
        conn.execute("UPDATE sessions SET state = 'closed' WHERE id = ?", (sid,))
        conn.commit()
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'cloner_throttled'").fetchone() is None


def _wave(conn, sid, cpu, rate="400m"):
    roster = [{"mac": MAC, "serial": f"{sid}-1", "model": "M"}]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, delivered) VALUES (?,?,?,?,?)",
                 (sid, json.dumps({"max_bitrate": rate, "roster": roster}), db.now_iso(),
                  json.dumps({"sent_bytes": 10 ** 10, "loss_blocks": 0, "wall_s": 400,
                              "ended": "send_done"}),
                  json.dumps({MAC: {"drawers": 1, "done": 1, "written_bytes": 0, "cpu": cpu}})))
    conn.commit()


def test_the_rate_learning_skips_a_wave_where_this_cloner_throttled(conn):
    drives = [{"mac": MAC, "serial": "n1"}]
    _wave(conn, "ses_cool", {"throttle_delta": 0})
    _wave(conn, "ses_hot", {"throttle_delta": 12}, rate="250m")
    assert drive_rates._from_previous(conn, drives, 600_000_000, None)[2]["session_id"] == "ses_cool"
    _wave(conn, "ses_unread", {"throttle_delta": None}, rate="300m")    # לא נקרא — נלמד כרגיל
    assert drive_rates._from_previous(conn, drives, 600_000_000, None)[2]["session_id"] == "ses_unread"


def test_record_wave_carries_each_machines_cpu(conn):
    _session(conn, "ses_w")
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES ('ses_w', ?, ?)",
                 (json.dumps({"max_bitrate": "400m", "roster": [{"mac": MAC, "serial": "S", "model": "M"}]}),
                  db.now_iso()))
    conn.execute("UPDATE session_members SET cpu_json = ?, udp_drops_json = ?",
                 (json.dumps({"throttle_delta": 5}), json.dumps({"rcvbuf": 0, "inerrors": 0})))
    conn.commit()
    drive_rates.record_wave(conn, "ses_w", conn.execute("SELECT * FROM session_members").fetchall())
    delivered = json.loads(conn.execute("SELECT delivered FROM wave_rates").fetchone()[0])
    assert delivered[MAC]["cpu"]["throttle_delta"] == 5


# --- הסוכן ------------------------------------------------------------------

from native import requires_native                       # noqa: E402
from test_agent import AGENT, BASH, posix, sh            # noqa: E402

LIB = AGENT / "lib"


def _sys(tmp_path: Path, stat: str, throttle: list[int] | None, temps: list[int], uptime: str) -> Path:
    root = tmp_path / "sys"
    (root / "proc").mkdir(parents=True, exist_ok=True)
    (root / "proc" / "stat").write_text(f"cpu  {stat}\ncpu0 1 2 3 4 5\n", encoding="ascii", newline="\n")
    (root / "proc" / "uptime").write_text(f"{uptime} 0.00\n", encoding="ascii", newline="\n")
    for i, n in enumerate(throttle or []):
        d = root / "sys" / "devices" / "system" / "cpu" / f"cpu{i}" / "thermal_throttle"
        d.mkdir(parents=True, exist_ok=True)
        (d / "core_throttle_count").write_text(f"{n}\n", encoding="ascii", newline="\n")
    for i, t in enumerate(temps):
        d = root / "sys" / "class" / "thermal" / f"thermal_zone{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "temp").write_text(f"{t}\n", encoding="ascii", newline="\n")
    return root


def _report(tmp_path, root, state, task="") -> dict:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "state").write_text(state + "\n", encoding="ascii", newline="\n")
    return json.loads(sh(f'export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r}; . {posix(LIB)}/common.sh;'
                         f' . {posix(LIB)}/progress.sh; . {posix(LIB)}/datareport.sh;'
                         f' build_progress ses_1 aa:bb:cc:dd:ee:01 {task}'))


@requires_native(("bash", BASH))
def test_the_agent_sends_cpu_once_final_with_deltas_and_the_peak(tmp_path):
    # user nice system idle iowait irq softirq — total 1000, idle 600, iowait 100
    root = _sys(tmp_path, "200 0 100 600 100 0 0", [3, 4], [55000], "100.0")
    assert "cpu" not in _report(tmp_path, root, "writing")              # הצילום — בלי רשת
    _sys(tmp_path, "200 0 100 600 100 0 0", [3, 4], [91000, 40000], "200.0")
    assert "cpu" not in _report(tmp_path, root, "writing")              # רק החום המרבי נשמר
    # +1000: idle +100, iowait +100 → עסוק 80%, ‏iowait 10%; throttle 7 → 10
    root = _sys(tmp_path, "900 0 200 700 200 0 0", [5, 5], [60000], "500.5")
    got = _report(tmp_path, root, "done")["cpu"]
    assert got == {"busy_pct": 80, "iowait_pct": 10, "throttle_delta": 3, "temp_max_c": 91, "seconds": 400}


@requires_native(("bash", BASH))
def test_no_source_is_null_never_zero(tmp_path):
    root = _sys(tmp_path, "200 0 100 600 100 0 0", None, [], "100")
    _report(tmp_path, root, "writing")
    got = _report(tmp_path, root, "failed")["cpu"]
    assert got["throttle_delta"] is None and got["temp_max_c"] is None
    assert got["busy_pct"] is None                                      # אותו /proc/stat — אין דלתא


@requires_native(("bash", BASH))
def test_a_capture_report_never_carries_cpu(tmp_path):
    root = _sys(tmp_path, "1 0 1 1 1 0 0", [1], [50000], "1")
    assert "cpu" not in _report(tmp_path, root, "writing", task="tsk_1")
    assert "cpu" not in _report(tmp_path, root, "done", task="tsk_1")


def test_the_interface_documents_it():
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "cpu_json" in doc and "throttle_delta" in doc and "cloner_throttled" in doc
