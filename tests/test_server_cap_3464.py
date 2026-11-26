"""#3464: תקרת הקריאה של דיסק הספרייה נלמדת מראיה בלבד, ונכנסת ל-K8 בצל.

ברזל 15/11 (LabRoot, `lab/metal-1115-brain3`): שלושה סבבים, `read=27.5-28.9MB/s device=sdb`, והשידור 263-293Mb/s; בכל
שלושת הגלים `UNLEARNED server_disk`, והקצב הנתבע (892m) לא זז. לפני התיקון `server_io` רק זיהה את הגל והוציא אותו מהלמידה:
המוח לא ידע מה התקרה. כאן:

1. **הלמידה** (`server_cap.learn`): חציון `read_mbps` של גלי `server_disk` של ההתקן הנוכחי, ולפחות שניים; גל בלי מונה
   אינו נכנס; גל `ok` אינו נכנס, אבל `ok` אחרון שקרא יותר מהתקרה מבטל אותה; התקן אחר אינו מתערבב.
2. **K8 בצל** (`chunk_rate.start`): `WAVE_SERVER_CAP` נרשם, והקצב החי אינו משתנה; רק `brain_server_cap` דלוק מקצץ,
   ולא מתחת לרצפה. `brain_veto_mode=off` -- לא נרשם דבר.
3. **ההצגה**: שורת הבריאות `server_disk` אומרת "דיסק הספרייה מוגבל ל-X MB/s — זה הגבול של הסבב".

**בקרה שלילית:** `server/learn/server_cap.py` (חדש), `server/learn/server_io.py`, `server/rounds/chunk_rate.py`,
`server/console/api.py` ו-`server/console/journal_he.py` חזרו לבסיס -- כל הטסטים כאן נופלים (`ImportError` על
`server_cap` נספר כאפס; מה שנספר: שורת הבריאות בלי המשפט, והקצב שעוד "שמאל" ב-`CAP` כשהמתג דלוק).
אין כאן `sleep`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.learn import server_cap, server_io
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6
MAC = "aa:bb:cc:00:00:21"
BASE = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _io(conn, read, *, basis="server_disk", device="sdb", at=None, util=0.95) -> str:
    """גל שנמדד: `read` = `read_mbps` שהמדידה כתבה (None = המונה לא נקרא), `basis` כמו `server_io.measure`. #3525: הגל רווי (`util` 0.95) --
    תקרה נלמדת רק מראיה לרוויה (`server_cap.SATURATED_UTIL`); ב-15/11 `util` היה 0.34, וזה אינו ראיה (ראו `test_server_cap_saturated_3525.py`)."""
    n = next(_seq)
    ref = f"ses_io{n:05d}"
    stamp = (BASE + timedelta(minutes=n)).isoformat(timespec="seconds") if at is None else at
    result = {"device": device, "seconds": 640.0, "read_mbps": read, "write_mbps": 0.0, "util": util,
              "await_ms": 11.0, "psi_some": 0.2, "psi_full": 0.1, "basis": basis}
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis)"
                 " VALUES ('wave', ?, ?, '{}', ?, ?, ?)", (ref, stamp, stamp, json.dumps(result), basis))
    conn.commit()
    return ref


def _brain(conn):
    return conn.execute("SELECT * FROM brain_decisions WHERE action = 'WAVE_SERVER_CAP' ORDER BY id").fetchall()


# --- 1. הלמידה ----------------------------------------------------------------------------------------------------


def test_the_three_waves_of_15_11_teach_the_ceiling_of_sdb(conn):
    """27.5, 28.2, 28.9MB/s על sdb (ברזל 15/11): התקרה היא החציון, וביחידה של החוט."""
    for read in (27.5, 28.9, 28.2):
        _io(conn, read)
    cap = server_cap.learn(conn)
    assert cap is not None and cap.device == "sdb" and cap.read_mbps == 28.2 and len(cap.waves) == 3
    assert cap.bps == int(28.2 * 8 * MB)
    assert cap.text_he().startswith("דיסק הספרייה מוגבל ל-28 MB/s — זה הגבול של הסבב")


def test_no_evidence_is_no_ceiling_not_zero(conn):
    assert server_cap.learn(conn) is None                       # אין גלים
    _io(conn, 28.0)
    assert server_cap.learn(conn) is None                       # גל אחד: פחות מ-MIN_WAVES
    _io(conn, 28.0, basis="ok")
    _io(conn, 28.0, basis=None)                                 # `basis` None = לא נמדד, לא ראיה
    assert server_cap.learn(conn) is None


def test_a_wave_without_a_counter_does_not_enter(conn):
    """הגל השני נאמר `server_disk` אבל `read_mbps` שלו חסר (המונה לא נקרא): אינו נספר, ואינו 0."""
    _io(conn, 28.0)
    _io(conn, None)
    assert server_cap.learn(conn) is None
    _io(conn, 30.0)
    cap = server_cap.learn(conn)
    assert cap is not None and cap.read_mbps == 29.0 and len(cap.waves) == 2


def test_a_wave_the_disk_did_not_limit_does_not_move_the_ceiling(conn):
    for read in (28.0, 30.0):
        _io(conn, read)
    _io(conn, 12.0, basis="ok")                                 # גל קצר ושקט: לא מוריד
    assert server_cap.learn(conn).read_mbps == 29.0


def test_a_later_ok_wave_that_read_more_cancels_the_ceiling(conn):
    for read in (28.0, 30.0):
        _io(conn, read)
    _io(conn, 120.0, basis="ok")                                # הדיסק הוחלף: קרא פי ארבעה בלי הגבלה
    assert server_cap.learn(conn) is None


def test_another_device_is_not_mixed_in(conn):
    _io(conn, 28.0, device="sdb")
    _io(conn, 28.0, device="sdb")
    _io(conn, 900.0, device="nvme0n1", basis="ok")              # הספרייה עברה להתקן אחר: sdb אינו שלה
    assert server_cap.learn(conn) is None
    _io(conn, 400.0, device="nvme0n1")
    _io(conn, 420.0, device="nvme0n1")
    cap = server_cap.learn(conn)
    assert cap is not None and cap.device == "nvme0n1" and cap.read_mbps == 410.0


def test_only_the_last_window_counts(conn):
    for read in (10.0, 10.0, 10.0, 30.0, 30.0, 30.0, 30.0, 30.0):
        _io(conn, read)
    assert server_cap.learn(conn).read_mbps == 30.0 and len(server_cap.learn(conn).waves) == server_cap.WINDOW


def test_a_broken_row_or_table_is_none_and_never_raises(conn):
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis)"
                 " VALUES ('wave', 'ses_bad', '2026-11-15T10:00:00+00:00', '{}', '2026-11-15T10:00:00+00:00', '{not json', 'server_disk')")
    conn.commit()
    assert server_cap.learn(conn) is None
    conn.execute("DROP TABLE server_io")
    assert server_cap.learn(conn) is None


# --- 2. K8: החלטת צל, והקצב החי ---------------------------------------------------------------------------------


def _run(conn, bits=None) -> tuple[int, int]:
    """(הקצב שהגל נבחר בו, הקצב שיצא ממנו `start`). `bits` = קצב שהגל כבר שונה לפני ה-`start`."""
    drives = [{"serial": "D1", "model": "APS", "mac": MAC, "port": 1}]
    asked = drive_rates.parse_bitrate(drive_rates.decide(conn, "ses_now", lambda: drives, None))
    assert asked > 400 * MB                                     # הבדיקה אומרת משהו: הקצב מעל כל התקרות שכאן
    return asked, chunk_rate.start(conn, "ses_now", asked if bits is None else bits, {(MAC, "D1"): 209})


def _teach(conn, reads=(27.5, 28.2, 28.9)):
    for read in reads:
        _io(conn, read)


def test_the_shadow_decision_is_recorded_and_the_live_rate_does_not_move(conn):
    _teach(conn)
    asked, got = _run(conn)
    assert got == asked                                         # המתג כבוי: הקצב החי כמו שהוא
    (row,) = _brain(conn)
    assert (row["rule"], row["mode"], row["verdict"], row["round_id"]) == ("K8", "shadow", "CAP", "ses_now")
    assert row["outcome"] == "shadow" and "צל, הקצב לא שונה" in row["reason"]
    ev = json.loads(row["evidence_json"])[0]
    assert ev["asked_bps"] == asked and ev["held_bps"] == asked and ev["enabled"] is False
    assert ev["read_mbps"] == 28.2 and ev["cap_bps"] == int(28.2 * 8 * MB) and ev["device"] == "sdb"
    assert drive_rates.decision(conn, "ses_now")["basis"] != "server_cap"
    assert not conn.execute("SELECT 1 FROM journal WHERE detail LIKE '%basis=server_cap%'").fetchall()


def test_no_ceiling_is_no_decision_not_fits(conn):
    """אין עדיין תקרה: "לא נלמד" אינו "נכנס". לא נרשמת שורה ל-K8 (שורה ריקה לכל גל רק מנפחת את הספירה), והקצב כמו שהוא."""
    asked, got = _run(conn)
    assert got == asked and _brain(conn) == []
    _io(conn, 28.0)                                             # גל אחד אינו תקרה
    assert _run(conn)[1] == asked and _brain(conn) == []


def test_a_rate_inside_the_ceiling_fits(conn):
    _teach(conn)
    _asked, got = _run(conn, 200 * MB)
    assert got == 200 * MB                                      # 200m < 225m
    (row,) = _brain(conn)
    assert row["verdict"] == "FITS" and "בתוכה" in row["reason"]


def test_the_switch_on_cuts_the_live_rate_to_the_ceiling(conn):
    _teach(conn)
    db.set_setting(conn, server_cap.SETTING, "true")
    cap = server_cap.learn(conn)
    asked, got = _run(conn)
    assert got == cap.bps and got < asked
    decision = drive_rates.decision(conn, "ses_now")
    assert decision["basis"] == "server_cap" and decision["max_bitrate"] == chunk_rate._fmt(got)
    (row,) = _brain(conn)
    assert row["verdict"] == "CAP" and row["outcome"] == "applied" and "הוגבל ל-" in row["reason"]
    (line,) = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                                                 " AND detail LIKE '%basis=server_cap%'")]
    assert f"was={chunk_rate._fmt(asked)}" in line and "device=sdb" in line


def test_the_cut_never_goes_under_the_floor(conn):
    _teach(conn, (8.0, 8.0))                                    # 64Mb/s: מתחת לרצפה של choose (150m)
    db.set_setting(conn, server_cap.SETTING, "true")
    asked, got = _run(conn)
    assert got == 150 * MB < asked
    (row,) = _brain(conn)
    assert row["verdict"] == "CAP" and json.loads(row["evidence_json"])[0]["cap_bps"] == 64 * MB


def test_brain_off_records_nothing_and_changes_nothing(conn):
    _teach(conn)
    db.set_setting(conn, server_cap.SETTING, "true")
    db.set_setting(conn, "brain_veto_mode", "off")
    asked, got = _run(conn)
    assert got == asked and _brain(conn) == []


def test_a_failure_of_the_cap_keeps_the_rate(conn, monkeypatch):
    monkeypatch.setattr(server_cap, "learn", lambda c: 1 / 0)
    asked, got = _run(conn)
    assert got == asked
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'chunk_rate_failed'").fetchone() is not None


# --- 3. ההצגה ---------------------------------------------------------------------------------------------------


def test_the_health_line_says_the_ceiling_of_the_round(conn):
    _io(conn, 28.0)
    [line] = server_io.health_checks(conn)
    assert "מוגבל ל-" not in line["detail"]                    # גל אחד אינו תקרה
    _io(conn, 28.4)
    [line] = server_io.health_checks(conn)
    assert line["state"] == "warn"
    assert "דיסק הספרייה מוגבל ל-28 MB/s — זה הגבול של הסבב" in line["detail"] and "(נלמד מ-2 גלים, sdb)" in line["detail"]
    _io(conn, 26.0, basis="ok")
    [line] = server_io.health_checks(conn)
    assert line["state"] == "ok" and "דיסק הספרייה מוגבל ל-28 MB/s" in line["detail"]


def test_the_switch_is_a_boolean_setting_off_by_default(conn):
    from server.console import api
    assert server_cap.SETTING in api.WRITE_SETTINGS and server_cap.SETTING in api.BOOLEAN_SETTINGS
    assert server_cap.enabled(conn) is False
    db.set_setting(conn, server_cap.SETTING, "true")
    assert server_cap.enabled(conn) is True
    db.set_setting(conn, server_cap.SETTING, "yes")
    assert server_cap.enabled(conn) is False                    # רק "true" בדיוק


def test_server_cap_wave_starts_without_another_tests_live_link(conn):
    asked, _ = _run(conn)
    assert asked == drive_rates.START_BITRATE
