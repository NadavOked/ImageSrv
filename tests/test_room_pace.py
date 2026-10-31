"""‏#410 — זמן שעבר, קצב והערכת סיום על מסך החדר.

הנתונים היו בשרת כל הזמן: `started_at` של הגל, ו-`bytes_written`/`moved_at`
לכל מגירה (#552). מה שלא היה הוא הגזירה — ולכן המסך הגרפי אמר בהערה
`/* bytes, no rate in the state */`. ‏ETA = היתרה חלקי הקצב.

‏17/10 (ברזל): הקצב היה ממוצע מאז שהגל יצא (‏`bytes_written` חלקי
‏`moved_at − started_at`), והמכנה כלל את ההמתנה לפני הבייט הראשון ובין
המחיצות. מאז הוא הקצב **העדכני**: הבייטים בחלון האחרון של 10 שניות לפחות,
מחותמות שהשרת עצמו שם על הדיווחים (‏`reports._stamp_window`).

⚠️ ‏`None` ≠ `0` (עיקרון 5): מגירה שלא נמדדה אינה "עצרה". כל דרך שבה
המדידה עצמה נכשלת — אין חותמת, אין בייטים, אפס שניות, לא כותבת — חייבת
לצאת `None`, ולא מספר שנראה כמו מדידה.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server.agent import reports  # noqa: E402
from server.rounds import room as room_mod  # noqa: E402
from test_server_room import (  # noqa: E402,F401
    CLONER1, CLONER2, cloner_hello, room, room_body, room_server,
)

T0 = "2026-09-19T10:00:00+00:00"
T100 = "2026-09-19T10:01:40+00:00"          # 100 שניות אחרי T0
GB = 1_000_000_000


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _pace(**target):
    base = {"state": "writing", "bytes_written": 5 * GB, "bytes_total": 15 * GB,
            "moved_at": T100, "recent_bps": 50_000_000, "rate_at": _ago(4)}
    return room_mod._pace({**base, **target}, T0)


def test_rate_is_the_recent_window_not_the_average_since_the_wave_started():
    """17/10 (ברזל): ‏5GB אחרי 100 שניות מאז שהגל יצא הם "50MB/s" בממוצע; אבל
    הקצב העדכני (החלון האחרון) הוא מה שהמגירה עושה עכשיו."""
    pace = _pace(recent_bps=75_000_000)
    assert pace["rate_bps"] == 75_000_000
    assert pace["eta_s"] == 133                    # 10GB נשארו / 75MB/s


def test_eta_is_unknown_without_a_total_but_the_rate_still_reads():
    pace = _pace(bytes_total=0)
    assert pace["rate_bps"] == 50_000_000
    assert pace["eta_s"] is None


@pytest.mark.parametrize("broken", [
    {"recent_bps": None}, {"recent_bps": 0}, {"recent_bps": True}, {"recent_bps": "75"},
    {"rate_at": None}, {"rate_at": "yesterday"}, {"rate_at": _ago(31)},
    {"state": "done"}, {"state": "verifying"},
])
def test_a_measurement_that_cannot_be_made_is_none_not_zero(broken):
    """חלון שטרם נסגר / חלון שלא זז / חותמת פגומה / חלון ישן (הדיווחים פסקו) /
    לא כותבת — כולם "לא נמדד". ‏0 היה אומר "נמדד: עצרה"."""
    pace = _pace(**broken)
    assert pace == {"rate_bps": None, "eta_s": None}


def test_no_wave_start_means_no_rate_at_all():
    assert room_mod._pace({"state": "writing", "bytes_written": GB, "bytes_total": 2 * GB,
                           "recent_bps": GB, "rate_at": _ago(1)}, None) ==         {"rate_bps": None, "eta_s": None}


# --- החלון עצמו: reports._stamp_window -------------------------------------------

def _stamp(before, written, now, monkeypatch, **sent):
    monkeypatch.setattr(reports, "now_iso", lambda: now)
    return reports._stamp_window(before, {"dev": "sda", "bytes_written": written, **sent})


def test_the_first_report_opens_a_window_and_measures_nothing(monkeypatch):
    t = _stamp(None, 3 * GB, T0, monkeypatch)
    assert (t["rate_at"], t["rate_bytes"]) == (T0, 3 * GB) and "recent_bps" not in t


def test_inside_the_window_the_start_and_the_last_rate_stay(monkeypatch):
    before = {"rate_at": T0, "rate_bytes": GB, "recent_bps": 40_000_000}
    t = _stamp(before, 2 * GB, "2026-09-19T10:00:09+00:00", monkeypatch)
    assert (t["rate_at"], t["rate_bytes"], t["recent_bps"]) == (T0, GB, 40_000_000)


def test_a_full_window_measures_the_bytes_in_it_and_starts_again(monkeypatch):
    before = {"rate_at": T0, "rate_bytes": GB, "recent_bps": 40_000_000}
    now = "2026-09-19T10:00:12+00:00"
    t = _stamp(before, GB + 900_000_000, now, monkeypatch)
    assert t["recent_bps"] == 75_000_000            # 900MB / 12s
    assert (t["rate_at"], t["rate_bytes"]) == (now, GB + 900_000_000)


@pytest.mark.parametrize("before", [
    {"rate_at": T0, "rate_bytes": 5 * GB, "recent_bps": 1},       # המונה ירד: התחלה מחדש
    {"rate_at": "yesterday", "rate_bytes": 0, "recent_bps": 1},   # חותמת פגומה
])
def test_a_counter_that_went_back_or_a_broken_stamp_restarts_without_a_rate(before, monkeypatch):
    t = _stamp(before, GB, T100, monkeypatch)
    assert (t["rate_at"], t["rate_bytes"]) == (T100, GB) and "recent_bps" not in t


def test_the_window_is_the_servers_own_stamp_never_the_agents(monkeypatch):
    t = _stamp(None, GB, T0, monkeypatch, recent_bps=10**12, rate_at=T100, rate_bytes=0)
    assert (t["rate_at"], t["rate_bytes"]) == (T0, GB) and "recent_bps" not in t


def _machines(*drawers):
    return [{"drawer_list": list(drawers)}]


def test_the_wave_rate_is_the_slowest_drawer_and_the_eta_the_longest():
    """הגל נגמר כשהמגירה האחרונה נגמרת; ממוצע היה מסתיר מגירה מפגרת."""
    pace = room_mod._wave_pace(
        (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="seconds"),
        _machines({"state": "writing", "rate_bps": 80_000_000, "eta_s": 100},
                  {"state": "writing", "rate_bps": 20_000_000, "eta_s": 400},
                  {"state": "done", "rate_bps": None, "eta_s": None}))
    assert pace["rate_bps"] == 20_000_000
    assert pace["eta_s"] == 400
    assert 89 <= pace["elapsed_s"] <= 95


def test_a_wave_that_did_not_start_has_no_elapsed_and_an_unmeasured_drawer_does_not_vote():
    pace = room_mod._wave_pace(
        None, _machines({"state": "writing", "rate_bps": None, "eta_s": None}))
    assert pace == {"started_at": None, "elapsed_s": None,
                    "rate_bps": None, "eta_s": None}


def test_the_numbers_reach_the_room_screen_from_a_real_wave(room_server, monkeypatch):
    """קצה-לקצה: גל רץ 100 שניות, אבל המגירה החלה לכתוב רק לפני 12 — ההמתנה
    למקבלים ולשאלת SMART. שני דיווחים 12 שניות זה מזה, ‏900MB ביניהם: ‏GET /room
    נושא 75MB/s — מה שהמגירה עושה עכשיו — ולא ‏~10MB/s, הממוצע מאז שהגל יצא
    שהמסך הציג עד 17/10. השעון של הדיווחים מוזז דרך `reports.now_iso`."""
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    assert admin.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1", "S2"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert admin.post("/api/console/room/start").status_code == 200
    wave = ctx.conn.execute("SELECT wave_session_id FROM room_rounds").fetchone()[0]
    ago = _ago(100)
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (ago, wave))
    ctx.conn.commit()

    def report(at: str, written: int) -> None:
        monkeypatch.setattr(reports, "now_iso", lambda: at)
        assert anon.post("/api/v1/agent/progress", json={
            "session_id": wave, "mac": CLONER1, "state": "writing",
            "targets": [{"dev": "sda", "bytes_written": written, "bytes_total": 15 * GB,
                         "state": "writing"}],
        }).status_code == 200

    report(_ago(12), 100_000_000)
    report(_ago(0), 1_000_000_000)
    monkeypatch.undo()

    view = room(admin)
    rnd = view["round"]
    assert rnd["started_at"] == ago
    assert 99 <= rnd["elapsed_s"] <= 105
    assert rnd["rate_bps"] == 75_000_000, rnd["rate_bps"]
    assert rnd["eta_s"] == (15 * GB - GB) // 75_000_000
    m1 = next(m for m in view["machines"] if m["mac"] == CLONER1)
    sda = next(d for d in m1["drawer_list"] if d["dev"] == "sda")
    assert sda["rate_bps"] == rnd["rate_bps"] and sda["eta_s"] == rnd["eta_s"]
    sdb = next(d for d in m1["drawer_list"] if d["dev"] == "sdb")
    assert sdb["rate_bps"] is None and sdb["eta_s"] is None, "מגירה שלא דיווחה — לא נמדדה"
    m2 = next(m for m in view["machines"] if m["mac"] == CLONER2)
    assert all(d["rate_bps"] is None for d in m2["drawer_list"])


def test_one_report_is_not_a_rate(room_server):
    """דיווח יחיד פותח חלון ואינו קצב — "לא נמדד", לא ממוצע מאז שהגל יצא."""
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    assert admin.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["S1"])
    assert admin.post("/api/console/room/start").status_code == 200
    wave = ctx.conn.execute("SELECT wave_session_id FROM room_rounds").fetchone()[0]
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (_ago(100), wave))
    ctx.conn.commit()
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 5 * GB, "bytes_total": 15 * GB,
                     "state": "writing"}]}).status_code == 200
    rnd = room(admin)["round"]
    assert rnd["rate_bps"] is None and rnd["eta_s"] is None


def test_before_the_wave_runs_nothing_is_measured(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["S1"])
    rnd = room(admin)["round"]
    assert rnd["wave_state"] == "open"
    assert rnd["started_at"] is None and rnd["elapsed_s"] is None
    assert rnd["rate_bps"] is None and rnd["eta_s"] is None
