"""‏#1883 — ציר זמן לשלבי השחזור לכל מכונה ודיסק.

הסוכן מסמן "השלב הזה מתחיל עכשיו" (‏uptime) לכל מגירה; היעד הסופי נושא את הסימנים, והשרת
הופך אותם למשכים, שומר אותם ב-`targets_json` (מסך הסבב) וב-`restore_stages` (‏#1843 לומד מהם).
סימנים פגומים — השדה נזנח כולו ("לא נמדד"), לא ציר זמן חלקי שנראה שלם.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.agent import reports, restore_stages
from server.rounds import room
from server.learn import learn_eta
from agentlib import flat_lib

MAC = "78:ac:c0:9b:18:83"
MARKS = [["gpt", 100.0], ["expand", 103.5], ["p1", 104.0], ["p2", 110.0],
         ["readback", 600.0], ["grow", 660.0], ["end", 664.0]]


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


# --- הסימנים → משכים -----------------------------------------------------------


def test_marks_become_durations_each_until_the_next():
    assert restore_stages.clean(MARKS) == [
        {"stage": "gpt", "seconds": 3.5}, {"stage": "expand", "seconds": 0.5},
        {"stage": "p1", "seconds": 6.0}, {"stage": "p2", "seconds": 490.0},
        {"stage": "readback", "seconds": 60.0}, {"stage": "grow", "seconds": 4.0}]


@pytest.mark.parametrize("bad", [
    MARKS[:-1],                                         # בלי end — לשלב האחרון אין משך
    [["gpt", 10], ["p1", 5], ["end", 20]],              # שעון שחזר אחורה
    [["gpt", 10], ["format_c", 12], ["end", 20]],       # שם שאינו שלב
    [["gpt", "10"], ["end", 20]],                       # זמן שאינו מספר
    [["gpt", True], ["end", 20]],
    [["gpt", 10], ["end", 12], ["p1", 13], ["end", 20]],   # end באמצע
    [["end", 1]], "gpt 10", None, [["gpt", float("nan")], ["end", 2]],
])
def test_malformed_marks_are_not_a_partial_timeline(bad):
    assert restore_stages.clean(bad) is None


# --- הדיווח ------------------------------------------------------------------


def _session(conn, sid: str) -> None:
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at)"
        " VALUES (?, 'grp_CLONERS', 'img_x', 'p', 1, 300, 'running', 't', ?, 0, ?)",
        (sid, db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES (?, ?, 'writing', ?)", (sid, MAC, db.now_iso()))
    conn.commit()
    db.net_seen(conn, MAC, "10.0.0.7", disks_json=json.dumps(
        [{"dev": "sda", "serial": "SER-A", "port": 1}, {"dev": "sdb", "serial": "SER-B", "port": 2}]))


def _report(conn, sid: str, state: str, targets: list) -> dict:
    return reports.ingest(conn, {"session_id": sid, "mac": MAC, "state": state, "targets": targets})


def _t(dev: str, state: str, stages=None) -> dict:
    t = {"dev": dev, "state": state, "bytes_written": 1, "bytes_total": 1}
    if stages is not None:
        t["stages"] = stages
    return t


def test_the_final_report_stores_durations_and_one_row_per_stage(conn):
    _session(conn, "ses_s1")
    bad = [["gpt", 10], ["p1", 5], ["end", 20]]
    assert _report(conn, "ses_s1", "writing", [_t("sda", "writing"), _t("sdb", "failed", bad)])["ok"]
    assert conn.execute("SELECT COUNT(*) FROM restore_stages").fetchone()[0] == 0   # לא טרמינלי
    assert _report(conn, "ses_s1", "done", [_t("sda", "done", MARKS), _t("sdb", "failed", bad)])["ok"]
    stored = {t["dev"]: t for t in json.loads(conn.execute(
        "SELECT targets_json FROM session_members").fetchone()[0])}
    assert stored["sda"]["stages"][3] == {"stage": "p2", "seconds": 490.0}
    assert "stages" not in stored["sdb"]                   # פגום — נזנח, לא חלקי
    rows = conn.execute("SELECT dev, serial, stage, seconds FROM restore_stages"
                        " ORDER BY rowid").fetchall()
    assert [tuple(r) for r in rows][:2] == [("sda", "SER-A", "gpt", 3.5), ("sda", "SER-A", "expand", 0.5)]
    assert len(rows) == 6 and {r["dev"] for r in rows} == {"sda"}


def test_the_room_screen_carries_the_stages_of_each_drawer(conn):
    _session(conn, "ses_s2")
    _report(conn, "ses_s2", "done", [_t("sda", "done", MARKS), _t("sdb", "done")])
    member = conn.execute("SELECT * FROM session_members WHERE session_id = 'ses_s2'").fetchone()
    drawers = {d["dev"]: d for d in room.drawer_list(conn, MAC, set(), member)}
    assert drawers["sda"]["stages"][0] == {"stage": "gpt", "seconds": 3.5}
    assert drawers["sdb"]["stages"] is None                  # לא נמדד — לא []


# --- #1843 -------------------------------------------------------------------


def test_medians_take_the_slowest_drawer_of_each_wave(conn):
    for sid, rb_a, rb_b in (("ses_m1", 60, 90), ("ses_m2", 100, 40), ("ses_m3", 50, 50)):
        for dev, rb in (("sda", rb_a), ("sdb", rb_b)):
            restore_stages.record(conn, sid, MAC, {"dev": dev, "state": "done", "stages": [
                {"stage": "gpt", "seconds": 3.0}, {"stage": "p1", "seconds": 10.0},
                {"stage": "p2", "seconds": 400.0}, {"stage": "readback", "seconds": rb}]}, None)
    got = restore_stages.medians(conn, MAC, ["ses_m1", "ses_m2", "ses_m3"])
    assert got == {"gpt": 3.0, "partitions": 410.0, "readback": 90.0}   # max: 90, 100, 50
    assert restore_stages.medians(conn, MAC, ["ses_none"]) is None


def test_the_wave_forecast_names_the_measured_stages(conn):
    from test_learn_eta_1843 import _drives, _wave
    sids = [_wave(conn, {MAC: {"drawers": 1, "done": 1, "written_bytes": 20 * 10 ** 9}},
                  wall=400, total=520) for _ in range(2)]
    for sid in sids:
        restore_stages.record(conn, sid, MAC, {"dev": "sda", "state": "done", "stages": [
            {"stage": "readback", "seconds": 70.0}]}, None)
    [machine] = learn_eta.wave(conn, _drives(MAC, 1), 18 * 10 ** 9)["machines"]
    assert machine["stages_s"] == {"readback": 70.0}


def test_a_failure_while_recording_is_zero_rows_not_a_crash(conn, monkeypatch):
    monkeypatch.setattr(restore_stages, "_settle", lambda c: (_ for _ in ()).throw(OSError("x")))
    assert restore_stages.record(conn, "s", MAC, {"dev": "sda", "state": "done",
                                                  "stages": [{"stage": "gpt", "seconds": 1}]}, None) == 0


# --- הסוכן ------------------------------------------------------------------

from native import requires_native                       # noqa: E402
from test_agent import AGENT, BASH, posix, sh            # noqa: E402

LIB = flat_lib()
def _agent(tmp_path: Path, script: str) -> dict:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    proc = tmp_path / "sys" / "proc"
    proc.mkdir(parents=True)
    up = proc / "uptime"
    return json.loads(sh(
        f'export RUN_DIR={posix(run)!r} SYSROOT={posix(tmp_path / "sys")!r}; . {posix(LIB)}/common.sh;'
        f' . {posix(LIB)}/progress.sh; . {posix(LIB)}/datareport.sh;'
        f' t() {{ echo "$1 0.00" > {posix(up)!r}; }}; ' + script))


@requires_native(("bash", BASH))
def test_the_agent_reports_the_marks_only_once_a_target_is_final(tmp_path):
    got = _agent(tmp_path,
                 'target_init sda 0; target_init sdb 0; echo writing > "$RUN_DIR/state";'
                 ' t 10.5; stage_at "sda sdb" gpt; t 12; stage_at "sda sdb" p1;'
                 ' target_set sdb failed boom; t 13; stage_at sdb end;'
                 ' t 14; stage_at "sda sdb" p2;'           # sdb ended — takes no more marks
                 ' build_progress ses_1 aa:bb:cc:dd:ee:01')
    by = {t["dev"]: t for t in got["targets"]}
    assert "stages" not in by["sda"]                        # still writing
    assert by["sdb"]["stages"] == [["gpt", 10.5], ["p1", 12], ["end", 13]]


@requires_native(("bash", BASH))
def test_a_final_machine_closes_an_open_timeline(tmp_path):
    got = _agent(tmp_path,
                 'target_init sda 0; t 1; stage_at sda gpt; target_set sda cancelled;'
                 ' echo partial > "$RUN_DIR/state"; t 9; build_progress ses_1 aa:bb:cc:dd:ee:01')
    assert got["targets"][0]["stages"] == [["gpt", 1], ["end", 9]]


@requires_native(("bash", BASH))
def test_no_uptime_is_no_mark_and_a_new_round_starts_clean(tmp_path):
    got = _agent(tmp_path,
                 'target_init sda 0; t 1; stage_at sda gpt; t 2; stage_at sda end; target_set sda done;'
                 ' target_init sda 0; rm -f "$SYSROOT/proc/uptime"; stage_at sda gpt; target_set sda done;'
                 ' build_progress ses_1 aa:bb:cc:dd:ee:01')
    assert "stages" not in got["targets"][0]


def test_the_drawer_flow_marks_every_stage_in_order():
    src = (LIB / "drawers.sh").read_text(encoding="utf-8")
    flow = src[src.index("run_restore_drawers() {"):]
    order = ['stage_at "$_d" gpt', 'stage_at "$_d" expand', 'stage_at "$_disks" "p$_i"',
             'stage_at "$_disks" readback', 'stage_at "$_d" grow']
    at = [flow.index(m) for m in order] + [flow.rindex('stage_at "$_disks" end')]
    assert flow.count('stage_at "$_disks" end') == 2      # the short write and the normal end
    assert at == sorted(at), at
    assert 'stage_at "$1" end' in (LIB / "failmark.sh").read_text(encoding="utf-8")


def test_the_interface_documents_the_stages():
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "restore_stages" in doc and "stages_s" in doc
