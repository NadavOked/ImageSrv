"""#3469 (K16, #3064): המוח חוזה את זמן הגל מההיסטוריה של השרת, בצל, ורושם אותו מול מה שקרה.

מה נבדק: המספר החזוי מול חישוב יד (אותו חישוב של `learn_eta.wave`); ש"לא נמדד" אינו 0 בכל אחד מהחושים (WoL, אתחול,
מוכן-אחרי, שלבי שחזור); ש"אין היסטוריה" הוא `UNPREDICTED` עם הסיבה, לא מספר מומצא; שהסגירה נושאת `actual`, את ההמתנה לאדם
(`waits.wave_hold`, שני זוגות האירועים) ואת חיזוי המנוע (`K3/WAVE_ETA`); ושבשרת אמיתי הגל רושם ונסגר, והמתג `off` משתיק.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import memory, veto
from server.db import set_setting
from server.learn import eta as learn_eta, waits as learn_waits
from server.rounds import room as room_mod

from test_server_room import (  # noqa: F401 -- room_server הוא fixture
    CLONER1, cloner_hello, report, room, room_body, room_server,
)

try:                                     # בקרה שלילית: בבסיס המודול אינו קיים
    from server.brain import timeforecast
except ImportError:                      # pragma: no cover
    timeforecast = None

pure = pytest.mark.skipif(timeforecast is None, reason="negative control: the new brain module is absent")

GB = 10 ** 9
MAC = "78:ac:c0:9b:11:c2"
T0 = 1_700_000_000.0
_seq = iter(range(10 ** 6))


@pytest.fixture(autouse=True)
def _fresh_brain():
    veto.forget()
    yield
    veto.forget()


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _wave(conn, delivered: dict, *, wall: float, total: float) -> str:
    """גל שהסתיים (כמו ב-`test_learn_eta_1843`): `sessions` ו-`wave_rates` עם `stream.wall_s` ו-`delivered`."""
    sid = f"ses_tf{next(_seq):04d}"
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at, closed_at)"
        " VALUES (?, 'grp_CLONERS', 'img_x', 'p', 1, 300, 'closed', 't', ?, 0, ?, ?)",
        (sid, _iso(T0), _iso(T0), _iso(T0 + total)))
    roster = [{"mac": mac, "serial": f"{sid}-{mac}-{k}", "model": "M"}
              for mac, got in delivered.items() for k in range(got["drawers"])]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, delivered) VALUES (?,?,?,?,?)",
                 (sid, json.dumps({"max_bitrate": "400m", "roster": roster}), _iso(T0),
                  json.dumps({"wall_s": wall, "ended": "send_done"}), json.dumps(delivered)))
    conn.commit()
    return sid


def _history(conn) -> list[str]:
    """שני גלים של שתי מגירות: goodput 50 ו-40MB/s, שלבים קבועים 120 ו-100ש'. חציון: 45MB/s ו-110ש'."""
    got = {MAC: {"drawers": 2, "done": 2, "written_bytes": 2 * 20 * GB}}
    return [_wave(conn, got, wall=400, total=520), _wave(conn, got, wall=500, total=600)]


def _drives(n: int = 2) -> list[dict]:
    return [{"mac": MAC, "serial": f"new-{k}"} for k in range(n)]


def k16(conn) -> list[dict]:
    return memory.decisions(conn, rule="K16", limit=50)


# --- ההחלטה בתחילת הגל ----------------------------------------------------------------------------------------------


@pure
def test_the_forecast_is_the_history_estimate_as_a_number_with_its_unit(conn):
    """אימג' של 18GB: 110 + 18e9 / 45e6 = 510ש'. המספר נשמר ב-`predicted`, לא רק בטקסט."""
    _history(conn)
    timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0)
    [d] = k16(conn)
    assert (d["verdict"], d["action"], d["entity_kind"], d["entity_key"], d["mode"]) == \
        ("PREDICT", "TIME_FORECAST", "round", "ses_new", "shadow")
    assert (d["predicted"], d["unit"], d["actual"], d["matched"]) == (510.0, "s", None, None)
    assert "eta_s=510" in d["evidence"] and "eta_why=history" in d["evidence"]
    assert f"{MAC}: wake_s=unmeasured boot_s=unmeasured ready_s=unmeasured wave_s=510 goodput_bps=45000000" \
           " fixed_s=110 stages_s=unmeasured" in d["evidence"]


@pure
def test_no_history_is_unpredicted_with_the_reason_and_no_number(conn):
    timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0)
    [d] = k16(conn)
    assert d["verdict"] == "UNPREDICTED" and d["predicted"] is None and d["unit"] is None
    assert "אין חיזוי" in d["reason"] and MAC in d["reason"]
    assert "eta_s=unmeasured" in d["evidence"] and "eta_why=no_history" in d["evidence"]


@pure
def test_an_unknown_image_size_is_unpredicted_not_zero(conn):
    _history(conn)
    timeforecast.started(conn, "ses_new", _drives(), None, now=T0)
    [d] = k16(conn)
    assert d["verdict"] == "UNPREDICTED" and d["predicted"] is None
    assert "image_bytes=unmeasured" in d["evidence"] and "גודל האימג'" in d["reason"]


@pure
def test_one_row_per_wave(conn):
    _history(conn)
    timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0)
    assert timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0) is None
    assert len(k16(conn)) == 1


@pure
def test_off_computes_and_records_nothing(conn):
    _history(conn)
    set_setting(conn, veto.SETTING, "off")
    assert timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0) is None
    assert k16(conn) == []


@pure
def test_a_broken_connection_does_not_raise():
    assert timeforecast.started(object(), "ses_x", _drives(), GB) is None
    timeforecast.finished(object(), "ses_x", None)


# --- מה שהשרת למד על מכונה: "לא נמדד" אינו 0 --------------------------------------------------------------------------


@pure
def test_nothing_measured_is_none_in_every_sense(conn):
    t = timeforecast.machine_times(conn, MAC, T0)
    assert (t["wake_s"], t["boot_s"], t["ready_s"]) == (None, None, None)


@pure
def test_a_wake_is_the_median_of_answered_wakes_and_needs_three(conn):
    def wake(seconds, at):
        conn.execute("INSERT INTO wol_wakes (mac, sent_at, sent_epoch, answered_at, seconds) VALUES (?,?,?,?,?)",
                     (MAC, _iso(at), at, _iso(at + seconds), seconds))
    wake(30, T0)
    wake(50, T0 + 100)
    assert timeforecast.machine_times(conn, MAC, T0 + 1000)["wake_s"] is None, "שתי הערות אינן בסיס"
    wake(40, T0 + 200)
    assert timeforecast.machine_times(conn, MAC, T0 + 1000)["wake_s"] == 40.0


@pure
def test_boot_is_the_sum_of_the_step_medians_and_a_missing_step_makes_it_unmeasured(conn):
    steps = timeforecast.BOOT_STEPS
    assert "menu" not in steps and "entry" not in steps and "agent-hello" in steps

    def measure(step, values):
        for v in values:
            conn.execute("INSERT INTO boot_step_times (mac, step, seconds, at) VALUES (?,?,?,?)",
                         (MAC, step, v, _iso(T0)))
    for step in steps:
        if step != "pre-boot":
            measure(step, (1.0, 2.0, 9.0))               # חציון 2
    t = timeforecast.machine_times(conn, MAC, T0)
    assert t["boot_s"] is None and t["boot_missing"] == ["pre-boot"], "סכום חלקי אינו זמן אתחול"
    measure("pre-boot", (4.0, 4.0))                       # שתי מדידות: עדיין פחות מ-MIN_SAMPLES
    assert timeforecast.machine_times(conn, MAC, T0)["boot_s"] is None
    measure("pre-boot", (4.0,))
    t = timeforecast.machine_times(conn, MAC, T0)
    assert t["boot_s"] == 2.0 * (len(steps) - 1) + 4.0 and t["boot_missing"] == []


@pure
def test_the_evidence_names_the_missing_boot_steps_only_when_some_were_measured(conn):
    _history(conn)
    timeforecast.started(conn, "ses_none", _drives(), 18 * GB, now=T0)
    [none] = k16(conn)
    assert not any("boot_missing" in line for line in none["evidence"]), "אף שלב לא נמדד: boot_s=unmeasured אומר הכול"
    for step in timeforecast.BOOT_STEPS:
        if step != "pre-boot":
            for v in (1.0, 2.0, 3.0):
                conn.execute("INSERT INTO boot_step_times (mac, step, seconds, at) VALUES (?,?,?,?)",
                             (MAC, step, v, _iso(T0)))
    conn.commit()
    timeforecast.started(conn, "ses_part", _drives(), 18 * GB, now=T0)
    part = [d for d in k16(conn) if d["entity_key"] == "ses_part"][0]
    assert any(line.startswith(f"{MAC}: wake_s=unmeasured boot_s=unmeasured boot_missing=pre-boot ")
               for line in part["evidence"]), part["evidence"]


@pure
def test_ready_is_the_median_over_waves_of_the_slowest_drawer(conn):
    for slow in (30, 50, 40):
        sid = _wave(conn, {MAC: {"drawers": 2, "done": 2, "written_bytes": 2 * 20 * GB}}, wall=400, total=500)
        targets = [{"dev": "sda", "senses": {"ready_s": 5}}, {"dev": "sdb", "senses": {"ready_s": slow}},
                   {"dev": "sdc"}]                        # בלי senses: סוכן ישן, אינו 0
        conn.execute("INSERT INTO session_members (session_id, mac, updated_at, targets_json) VALUES (?,?,?,?)",
                     (sid, MAC, _iso(T0), json.dumps(targets)))
    conn.commit()
    assert timeforecast.machine_times(conn, MAC, T0)["ready_s"] == 40.0


# --- ההמתנה לאדם בגל (`waits.wave_hold`) --------------------------------------------------------------------------------


def test_the_wait_for_a_person_counts_both_event_pairs_and_only_this_wave(conn):
    db.journal(conn, "send_held_prompt", "ses_a aa:bb")
    db.journal(conn, "send_prompt_released", "ses_a 90s")
    db.journal(conn, "send_held_smart", "ses_a aa:bb")                # הזוג שלפני #2044
    db.journal(conn, "send_hold_released", "ses_a 30s")
    db.journal(conn, "send_held_prompt", "ses_b aa:bb")
    db.journal(conn, "send_prompt_released", "ses_b 500s")
    assert learn_waits.wave_hold(conn, "ses_a") == 120
    assert learn_waits.wave_hold(conn, "ses_b") == 500


def test_no_release_in_the_journal_is_not_measured_and_not_zero(conn):
    assert learn_waits.wave_hold(conn, "ses_none") is None, "מנוע החתיכות מחזיק בלי אירוע: אין אירוע אינו 0"
    db.journal(conn, "send_held_prompt", "ses_open aa:bb")
    assert learn_waits.wave_hold(conn, "ses_open") is None, "החזקה שלא שוחררה: לא ידוע כמה ארכה"


# --- סוף הגל -------------------------------------------------------------------------------------------------------


@pure
def test_finish_closes_with_actual_the_wait_for_a_person_and_the_engines_forecast(conn):
    _history(conn)
    timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0)
    memory.record(conn, mode="shadow", rule="K3", action="WAVE_ETA", verdict="PREDICT", reason="r",
                  evidence=("eta_s=600", "t1_s=300"), round_id="ses_new", entity=("round", "ses_new"))
    db.journal(conn, "send_held_prompt", "ses_new aa:bb")
    db.journal(conn, "send_prompt_released", "ses_new 90s")
    began = datetime.fromtimestamp(T0, timezone.utc)
    timeforecast.finished(conn, "ses_new", began.isoformat(), now=began + timedelta(seconds=540))
    [d] = k16(conn)
    assert (d["actual"], d["matched"], d["outcome_state"]) == (540.0, None, "closed"), "ל-K16 אין הגדרת התאמה (#3321)"
    assert "בפועל/חזוי 1.06" in d["outcome"]
    assert "90 שניות" in d["outcome"] and "10 דק'" in d["outcome"], d["outcome"]


@pure
def test_finish_says_not_measured_when_there_is_no_wait_no_peer_and_no_start_time(conn):
    _history(conn)
    timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0)
    timeforecast.finished(conn, "ses_new", None)
    [d] = k16(conn)
    assert d["actual"] is None and d["matched"] is None
    assert "בפועל לא נמדד" in d["outcome"] and "המתנה לאדם בגל: לא נמדד" in d["outcome"]
    assert "חיזוי המנוע (K3/WAVE_ETA): לא נרשם" in d["outcome"]


@pure
def test_a_failed_wave_has_no_actual(conn):
    _history(conn)
    timeforecast.started(conn, "ses_bad", _drives(), 18 * GB, now=T0)
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES ('ses_bad', 'grp_CLONERS', 'img_x', 'p', 1, 300,"
                 " 'failed', 't', ?, 0)", (_iso(T0),))
    conn.commit()
    began = datetime.fromtimestamp(T0, timezone.utc)
    timeforecast.finished(conn, "ses_bad", began.isoformat(), now=began + timedelta(seconds=60))
    [d] = k16(conn)
    assert d["actual"] is None and "הגל נכשל" in d["outcome"]


@pure
def test_an_unpredicted_wave_still_gets_its_actual(conn):
    """אין חיזוי -- אבל הזמן בפועל נמדד, והוא הבסיס לגל הבא."""
    timeforecast.started(conn, "ses_new", _drives(), 18 * GB, now=T0)
    began = datetime.fromtimestamp(T0, timezone.utc)
    timeforecast.finished(conn, "ses_new", began.isoformat(), now=began + timedelta(seconds=300))
    [d] = k16(conn)
    assert d["verdict"] == "UNPREDICTED" and d["actual"] == 300.0 and "לא נחזה" in d["outcome"]


# --- בשרת אמיתי -----------------------------------------------------------------------------------------------------


def _run_wave(room_server, drawers=("S1", "S2")):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room", json=room_body(room_server, len(drawers), {CLONER1: list(drawers)}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, list(drawers))["session"]["id"]
    room_mod.sweep(ctx.conn, ctx.store)
    assert room(deploy)["round"]["wave_state"] == "running"
    return wave


def test_the_room_records_the_forecast_when_the_wave_leaves_and_closes_it_when_it_ends(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = _run_wave(room_server)
    [d] = k16(ctx.conn)
    assert (d["round_id"], d["entity_key"], d["mode"], d["verdict"]) == (wave, wave, "shadow", "UNPREDICTED"), d
    assert CLONER1 in d["reason"], "אין היסטוריה: הסיבה אומרת של מי"
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})
    room_mod.sweep(ctx.conn, ctx.store)
    [d] = k16(ctx.conn)
    assert d["outcome_state"] == "closed" and d["actual"] is not None and "המתנה לאדם בגל" in d["outcome"], d


def test_off_the_room_records_no_forecast(room_server):
    ctx = room_server["ctx"]
    set_setting(ctx.conn, veto.SETTING, "off")
    _run_wave(room_server)
    assert k16(ctx.conn) == []


def test_a_brain_that_throws_does_not_stop_the_wave(room_server, monkeypatch):
    ctx = room_server["ctx"]
    monkeypatch.setattr(timeforecast, "started", lambda *a, **kw: 1 / 0)
    _run_wave(room_server)                                           # הגל יוצא ורץ גם כשהמוח זורק
    assert k16(ctx.conn) == []
