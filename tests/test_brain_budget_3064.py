"""#3064, שלב 3 (K7) -- **תקציב הרשת האוטומטי** של ההגשה ב-HTTP (`server/brain/budget.py`, `netsense.py`).

הכרעת נדב 14/11: אין אחוזים קבועים; המוח מודד ומתאים בתוך גבול עליון אחד (`brain_net_ceiling_mbps`).

- **התקציב לא חורג מהגבול**, והשידור קודם: מה שנשאר אחרי הקצב שהזרם ביקש, ואף פעם לא יותר (מעל הרצפה).
- **השידור לא נחנק:** אין שידור = כל הגבול; שידור שתופס את הגבול = רצפה, לא אפס ולא שלילי; קצב שלא נקרא = רצפה.
- **AIMD מתכנס:** אובדן/עומס = מחצית; שקט שנמדד = צעד; אין מדידה = לא זז ("אין מדידה", לא "תקין").
- **round-robin הוגן:** משכפל עם שלושה חיבורים מקבל כמו משכפל עם חיבור אחד; משכפל שאינו מבקש אינו מחזיק את האחרים.
- **shadow אינו משנה התנהגות:** התקציב מחושב ונרשם, ושום הורדה לא מושהית. `enforce` מאט בפועל.
- כל החלטה נרשמת ב-`imagesrv.brain` עם הסיבה.

**בקרה שלילית:** ראו את גוף ה-PR (המוטציות ותוצאותיהן).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import Counter

import pytest

pytest.importorskip("fastapi")

from conftest import PARTITION_BYTES
from server import db
from server.brain import budget as B, netsense
from server.brain.served import Served
from server.rounds import chunksend

import chunkcast_rig as C
from test_chunksend_2533 import SID, rig, wait_for   # noqa: F401 — rig is a fixture

MBPS = 1_000_000
CEIL = 900 * MBPS
CFG = B.Config(ceiling_bps=CEIL)
CHUNK = 64 * 1024


def sample(broadcast=0, *, rx=None, tx=None, io=None, served=0.0):
    return B.Sample(broadcast, served, rx, tx, io)


def quiet(broadcast):
    """הכול נמדד ונקי: אף חוש לא נקרא כ"אין מדידה"."""
    return sample(broadcast, rx=0, tx=0, io=0.0)


# --- המחשבון: שארית הגבול, והשידור קודם -------------------------------------------------------------------------------


def test_the_budget_is_what_the_stream_leaves_of_the_ceiling():
    d = B.step(None, CFG, quiet(700 * MBPS))
    assert d.limit_bps == 200 * MBPS and d.rate_bps == 200 * MBPS and d.broadcast_bps == 700 * MBPS


def test_no_stream_means_the_whole_ceiling_to_downloads():
    d = B.step(120 * MBPS, CFG, sample(0))
    assert (d.action, d.rate_bps, d.limit_bps) == (B.OPEN, CEIL, CEIL), "AIMD state is dropped: nobody to protect"
    assert "אין שידור" in d.reason


def test_a_stream_that_takes_the_whole_ceiling_leaves_the_floor_not_zero_and_not_negative():
    for broadcast in (CEIL - 4 * MBPS, CEIL, CEIL + 300 * MBPS):
        d = B.step(None, CFG, quiet(broadcast))
        assert d.rate_bps == B.FLOOR_BPS == d.limit_bps, broadcast
        assert "רצפה" in d.reason


def test_a_stream_rate_that_was_not_read_is_the_floor_not_the_whole_ceiling():
    """"לא הצלחנו לשאול" אינו "אין שידור" (עיקרון 5): לא מחלקים את כל הגבול על ניחוש."""
    d = B.step(None, CFG, B.Sample(None, 0.0, 0, 0, 0.0))
    assert d.rate_bps == B.FLOOR_BPS and "לא נקרא" in d.reason and d.broadcast_bps is None


def test_the_floor_is_never_above_a_small_ceiling():
    tiny = B.Config(ceiling_bps=4 * MBPS)
    assert B.step(None, tiny, quiet(0)).rate_bps == 4 * MBPS
    assert B.step(None, tiny, quiet(3 * MBPS)).rate_bps == 4 * MBPS and tiny.floor == 4 * MBPS


def test_the_budget_never_passes_the_ceiling_and_never_takes_the_streams_share():
    """אינווריאנטים 12 ו-13: מאה אלף דגימות אקראיות, כל אחת מול כל מצב קודם."""
    rnd = random.Random(3064)
    rate = None
    for _ in range(20_000):
        ceiling = rnd.choice([100, 500, 900, 2500, 9_000]) * MBPS   # Mbps, not a port
        cfg = B.Config(ceiling_bps=ceiling)
        broadcast = rnd.choice([None, 0, rnd.randint(0, int(ceiling * 1.2))])
        s = B.Sample(broadcast, rnd.random() * ceiling, rnd.choice([None, 0, 3]), rnd.choice([None, 0, 2]),
                     rnd.choice([None, 0.0, 0.05, 0.5]))
        d = B.step(rate if rnd.random() < 0.8 else None, cfg, s)
        rate = d.rate_bps
        assert cfg.floor <= d.rate_bps <= ceiling, (d, ceiling)
        if broadcast and ceiling - broadcast >= cfg.floor:
            assert d.rate_bps + broadcast <= ceiling, (d, broadcast, ceiling)         # the live stream keeps its rate
        if broadcast is None:
            assert d.rate_bps == cfg.floor


# --- AIMD ------------------------------------------------------------------------------------------------------------


def test_loss_in_the_receivers_halves_quiet_adds_one_step_and_nothing_measured_does_not_move():
    start = 200 * MBPS
    cut = B.step(start, CFG, sample(500 * MBPS, rx=12))
    assert (cut.action, cut.rate_bps) == (B.CUT_, 100 * MBPS) and "12" in cut.reason and "בחצי" in cut.reason
    up = B.step(100 * MBPS, CFG, quiet(500 * MBPS))
    assert (up.action, up.rate_bps) == (B.RAISE, 100 * MBPS + B.STEP_BPS) and "שקט" in up.reason
    held = B.step(100 * MBPS, CFG, sample(500 * MBPS))
    assert (held.action, held.rate_bps) == (B.HOLD, 100 * MBPS)
    assert "אין מדידה" in held.reason and "unmeasured" in " ".join(held.evidence), \
        "no measurement is logged as no measurement, never as clean"


@pytest.mark.parametrize("signal,expect", [({"rx": 5}, "המקלטים"), ({"tx": 9}, "כרטיס השרת")])
def test_each_kind_of_pressure_cuts_the_budget_in_half_and_names_itself(signal, expect):
    # #3463: דיסק הספרייה אינו כאן עוד -- לחץ דיסק אינו ראיה על הרשת (tests/test_brain_budget_disk_3463.py)
    s = sample(500 * MBPS, **signal)
    d = B.step(300 * MBPS, CFG, s)
    assert d.action == B.CUT_ and d.rate_bps == 150 * MBPS and expect in d.reason


def test_pressure_below_the_servers_threshold_is_quiet_not_a_cut():
    # #3463: הדיסק נספר רק כשהוא מקצץ (`disk_cuts_budget`), ואז הסף הוא `PSI_FULL_MAX`
    d = B.step(100 * MBPS, B.Config(ceiling_bps=CEIL, disk_cuts_budget=True), sample(500 * MBPS, io=B.PSI_FULL_MAX))
    assert d.action == B.RAISE


def test_a_cut_never_goes_below_the_floor():
    rate = 100 * MBPS
    for _ in range(40):
        rate = B.step(rate, CFG, sample(500 * MBPS, rx=1)).rate_bps
    assert rate == B.FLOOR_BPS


def test_the_budget_is_held_down_when_the_stream_takes_more_than_before():
    d = B.step(300 * MBPS, CFG, quiet(800 * MBPS))
    assert (d.action, d.rate_bps) == (B.CLAMP, 100 * MBPS) and "השידור תופס יותר" in d.reason


def test_at_the_limit_a_quiet_tick_holds_and_says_so():
    d = B.step(200 * MBPS, CFG, quiet(700 * MBPS))
    assert (d.action, d.rate_bps) == (B.HOLD, 200 * MBPS) and "בגבול" in d.reason


def _plant(capacity, broadcast, ticks, *, start=None, cfg=CFG):
    """רשת עם קיבולת אמיתית שהמוח לא יודע. עומס = הידוק מעבר לקיבולת מפיל חבילות במקלטים."""
    rate, history = start, []
    limit = cfg.ceiling_bps - broadcast
    for _ in range(ticks):
        in_effect = limit if rate is None else rate
        lost = 7 if broadcast + in_effect > capacity else 0
        rate = B.step(rate, cfg, sample(broadcast, rx=lost, tx=0, io=0.0)).rate_bps
        history.append(rate)
    return history


def test_aimd_converges_under_the_real_capacity_the_brain_does_not_know():
    """קיבולת אמיתית 800, שידור 500: ל-HTTP מגיעים 300, אבל הגבול העליון משאיר 400. אחרי ההתכנסות התקציב נע
    בין חצי מהקיבולת לקיבולת ועוד צעד, ואינו מתפזר."""
    hist = _plant(800 * MBPS, 500 * MBPS, 400)
    room = 300 * MBPS
    tail = hist[-150:]
    assert min(tail) >= room / 2 - B.STEP_BPS and max(tail) <= room + B.STEP_BPS, (min(tail), max(tail))
    assert max(hist) <= CEIL - 500 * MBPS


def test_aimd_converges_to_the_limit_and_stays_there_when_the_network_is_clean():
    hist = _plant(10_000 * MBPS, 500 * MBPS, 400, start=B.FLOOR_BPS)
    assert hist[-1] == CEIL - 500 * MBPS and set(hist[-20:]) == {hist[-1]}
    assert hist == sorted(hist), "it rises slowly and monotonically: quiet adds a step, never more"
    assert hist[1] - hist[0] == B.STEP_BPS


def test_aimd_recovers_from_a_burst_of_loss_and_does_not_collapse():
    hist = [B.step(200 * MBPS, CFG, sample(500 * MBPS, rx=3)).rate_bps]
    for _ in range(60):
        hist.append(B.step(hist[-1], CFG, quiet(500 * MBPS)).rate_bps)
    assert hist[0] == 100 * MBPS and hist[-1] > hist[0] and hist[-1] <= 400 * MBPS


# --- המדידה ----------------------------------------------------------------------------------------------------------


def reading(at, *, served=0, broadcast=0, nic_tx=None, drop=None, rx=None, psi=None):
    return B.Reading(at, served, broadcast, nic_tx, drop, rx, psi)


def test_a_counter_that_went_backwards_is_unmeasured_not_loss_and_not_quiet():
    got = B.derive(reading(0, nic_tx=10_000, drop=50, rx=40, psi=9_000_000),
                   reading(10, nic_tx=500, drop=3, rx=0, psi=100, broadcast=300 * MBPS))
    assert (got.rx_dropped, got.tx_dropped, got.io_full) == (None, None, None)
    d = B.step(None, CFG, got)
    assert d.action == B.HOLD


def test_counters_that_grew_give_the_deltas_and_the_io_pressure_as_a_share_of_time():
    got = B.derive(reading(0, drop=1, rx=10, psi=1_000_000), reading(10, drop=4, rx=15, psi=3_000_000, broadcast=1))
    assert (got.rx_dropped, got.tx_dropped) == (5, 3) and got.io_full == pytest.approx(0.2)


def test_served_rate_comes_from_the_counter_and_the_first_cycle_has_no_deltas():
    first = B.derive(None, reading(5, served=7_000_000, broadcast=300 * MBPS))
    assert first.served_bps == 0.0 and (first.rx_dropped, first.tx_dropped, first.io_full) == (None, None, None)
    assert first.broadcast_bps == 300 * MBPS, "the stream's asked rate is known from the first cycle"
    got = B.derive(reading(0, served=0, broadcast=300 * MBPS), reading(10, served=125_000_000, broadcast=300 * MBPS))
    assert got.served_bps == pytest.approx(100 * MBPS)


def test_a_card_that_carries_more_than_the_stream_asked_keeps_room_for_it_but_our_own_files_are_not_counted():
    own = reading(0, served=0, broadcast=300 * MBPS, nic_tx=0)
    # 10 s: the card sent 1,500 MB = 1,200 Mbps; 100 Mbps of that is our files; 1,100 Mbps is something else
    more = B.derive(own, reading(10, served=125_000_000, broadcast=300 * MBPS, nic_tx=1_500_000_000))
    assert more.broadcast_bps == pytest.approx(1_100 * MBPS)
    assert any("more than asked" in n for n in more.notes)
    # the card carries exactly what we asked plus our files: the asked rate stands
    same = B.derive(own, reading(10, served=125_000_000, broadcast=300 * MBPS, nic_tx=(400 * MBPS * 10) // 8))
    assert same.broadcast_bps == 300 * MBPS


def test_no_stream_means_no_card_arithmetic():
    got = B.derive(reading(0, nic_tx=0), reading(10, broadcast=0, nic_tx=10**9))
    assert got.broadcast_bps == 0


# --- התור: הגינות וקצב -----------------------------------------------------------------------------------------------


def _drain(fair, rate_bps, conns, seconds):
    """כל חיבור מבקש חתיכה מיד אחרי שהקודמת הוגשה. מחזיר כמה בייטים קיבל כל משכפל. זמן וירטואלי, בלי שעון."""
    got, now = Counter(), 0.0
    for i, (ip, _) in enumerate(conns):
        fair.add(ip, CHUNK, i)
    while now < seconds:
        for i in fair.grant(now, rate_bps / 8):
            got[conns[i][0]] += CHUNK
            fair.add(conns[i][0], CHUNK, i)
        due = fair.next_due(now)
        assert due is not None, "every connection asks again at once: the line is never empty here"
        now += max(due, 1e-9)
    return got


def test_round_robin_gives_a_machine_with_three_connections_the_share_of_a_machine_with_one():
    fair = B.Fair()
    got = _drain(fair, 80 * MBPS, [("a", 0), ("a", 1), ("a", 2), ("b", 0), ("c", 0)], 30)
    assert set(got) == {"a", "b", "c"}
    assert max(got.values()) - min(got.values()) <= 3 * CHUNK, got


def test_the_total_never_passes_the_rate_and_is_not_starved_below_it():
    fair, rate, seconds = B.Fair(), 80 * MBPS, 30
    got = _drain(fair, rate, [("a", 0), ("b", 0), ("c", 0), ("d", 0)], seconds)
    total = sum(got.values())
    assert total <= rate / 8 * (seconds + B.BURST_S) + 2 * CHUNK, total
    assert total >= rate / 8 * seconds * 0.97, total


def test_a_machine_that_does_not_ask_leaves_its_share_to_the_others():
    fair = B.Fair()
    got = _drain(fair, 80 * MBPS, [("a", 0), ("a", 1)], 20)
    assert set(got) == {"a"} and got["a"] >= 80 * MBPS / 8 * 20 * 0.97


def test_a_machine_that_was_idle_comes_back_with_no_credit_to_spend_over_the_others():
    fair, rate = B.Fair(), 80 * MBPS
    first = _drain(fair, rate, [("a", 0), ("b", 0)], 20)
    assert abs(first["a"] - first["b"]) <= 2 * CHUNK
    assert fair.finish == {} or all(f > fair.v for f in fair.finish.values())


def test_a_request_nobody_waits_for_any_more_does_not_hold_the_line():
    fair, dead = B.Fair(), {1}
    fair.add("gone", CHUNK, 1)
    fair.add("here", CHUNK, 2)
    out = fair.grant(0.0, 1.0, alive=lambda t: t not in dead)
    assert out == [2], "the cancelled request is forgotten and the line goes to the next one"
    assert fair.next_due(0.0) is None


def test_unlimited_serves_everyone_at_once_and_takes_no_time_on_the_line():
    fair = B.Fair()
    for i in range(50):
        fair.add(f"m{i}", CHUNK, i)
    assert fair.grant(0.0, 1.0, unlimited=True) == list(range(50))
    assert len(fair) == 0 and fair.next_free == 0.0


# --- ההגדרה ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expect", [("0", 0), (0, 0), ("10", 10), (10, 10), ("900", 900), (100_000, 100_000),
                                       ("100000", 100_000), ("9", None), ("100001", None), ("-5", None),
                                       ("", None), ("010", None), ("00", None), (" 40", None), ("1e3", None),
                                       ("12.5", None), (12.5, None), (True, None), (False, None), (None, None),
                                       ("٣٠٠", None)])
def test_the_ceiling_is_zero_or_a_whole_number_in_range(raw, expect):
    assert B.parse_ceiling(raw) == expect


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "budget.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def test_the_ceiling_comes_from_the_setting_then_from_the_card_then_from_the_default(conn):
    link = {"interface": "eth1", "speed_mbps": 1000, "why": None}
    assert B.ceiling_bps(conn, link) == (900 * MBPS, "הכרטיס"), "90% of the link, like the wave's ceiling"
    assert B.ceiling_bps(conn, {"speed_mbps": 10_000})[0] == 9_000 * MBPS
    for unreadable in ({"speed_mbps": None, "why": "virtual"}, {}, None, {"speed_mbps": True}, {"speed_mbps": -1}):
        assert B.ceiling_bps(conn, unreadable) == (B.DEFAULT_CEILING_MBPS * MBPS, "ברירת מחדל")
    assert B.DEFAULT_CEILING_MBPS == 900
    db.set_setting(conn, B.SETTING, "300")
    assert B.ceiling_bps(conn, link) == (300 * MBPS, "הגדרה")
    db.set_setting(conn, B.SETTING, "0")
    assert B.ceiling_bps(conn, link) == (900 * MBPS, "הכרטיס")


def test_a_stored_value_the_brain_cannot_use_is_automatic_not_garbage(conn):
    for bad in ("abc", "5", "999999999", "-1", ""):
        db.set_setting(conn, B.SETTING, bad)
        assert B.stored(conn) == 0 and B.effective(conn) == "0"
    assert B.effective(db.connect(":memory:")) == "0"


def test_the_ceiling_setting_is_in_the_console_with_its_range_a_default_and_a_hebrew_journal(server):
    admin, ctx = server["admin"], server["ctx"]
    assert admin.get("/api/console/settings").json()[B.SETTING] == "0", "unset = measured from the card, shown as 0"
    for ok in ("0", "10", "250", "100000"):
        assert admin.post("/api/console/settings", json={B.SETTING: ok}).status_code == 200, ok
        assert admin.get("/api/console/settings").json()[B.SETTING] == ok
    assert admin.post("/api/console/settings", json={B.SETTING: "250"}).status_code == 200
    assert B.stored(ctx.conn) == 250
    for bad in ("9", "100001", "-1", "abc", "", "0250", "12.5", 12.5, True, None):
        r = admin.post("/api/console/settings", json={B.SETTING: bad})
        assert r.status_code == 400, bad
        assert "Mbps" in r.json()["detail"] and "10–100000" in r.json()["detail"]
    assert admin.get("/api/console/settings").json()[B.SETTING] == "250"
    rows = admin.get("/api/console/journal", params={"event": "setting_change"}).json()
    assert f"{B.LABEL_HE}: 250" in [r["text"] for r in rows], [r["text"] for r in rows]


def test_deploy_cannot_write_the_ceiling(server):
    assert server["deploy"].post("/api/console/settings", json={B.SETTING: "300"}).status_code == 403
    assert server["admin"].get("/api/console/settings").json()[B.SETTING] == "0"


# --- המחזור: מצבים, יומן, ואכיפה -------------------------------------------------------------------------------------


class Harness:
    """תקציב עם מקורות מזויפים: מצב, גבול, וקריאות שהטסט מזיז."""

    def __init__(self, mode=B.SHADOW, ceiling=100 * MBPS, **kw):
        self.mode, self.ceiling, self.t = mode, ceiling, 1000.0
        self.reading = {"served_bytes": 0, "broadcast_bps": 0}
        self.wall = 1_800_000_000.0
        self.budget = B.Budget(mode=lambda: self.mode, ceiling=lambda: (self.ceiling, "הגדרה"),
                               read=lambda: B.Reading(self.t, **self.reading), wall=lambda: self.wall, **kw)

    def tick(self, advance=10.0, *, broadcast=None, served=None, **reading):
        self.t += advance
        if broadcast is not None:
            self.reading["broadcast_bps"] = broadcast
        if served is not None:
            self.reading["served_bytes"] = served
        self.reading.update(reading)
        return self.budget.tick()


def brain_lines(caplog, rule="K7"):
    return [json.loads(r.getMessage()[6:]) for r in caplog.records
            if r.name == "imagesrv.brain" and r.getMessage().startswith("brain {") and f'"rule": "{rule}"' in r.getMessage()]


def test_every_decision_is_logged_with_its_reason_and_the_evidence(caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    h = Harness(mode=B.ENFORCE, ceiling=900 * MBPS)
    h.tick(broadcast=700 * MBPS, rx_dropped=0)
    h.tick(broadcast=700 * MBPS, rx_dropped=9)
    lines = brain_lines(caplog)
    assert [x["action"] for x in lines] == ["HOLD", "CUT"], lines
    assert all(x["rule"] == "K7" and x["reason"] and x["mode"] == "enforce" for x in lines)
    cut = lines[-1]
    assert cut["budget_mbps"] == 100.0 and cut["limit_mbps"] == 200.0 and cut["ceiling_mbps"] == 900.0
    assert cut["broadcast_mbps"] == 700.0 and "9" in cut["reason"] and cut["ceiling_from"] == "הגדרה"
    assert any(e.startswith("rcvbuf=") for e in cut["evidence"])
    assert [r.levelno for r in caplog.records if r.getMessage().startswith("brain {")][-1] == logging.WARNING


def test_a_steady_idle_server_does_not_repeat_the_same_line_every_cycle(caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    h = Harness(ceiling=900 * MBPS)
    for _ in range(6):
        h.tick(broadcast=0)
    assert [x["action"] for x in brain_lines(caplog)] == ["OPEN"]
    h.tick(broadcast=300 * MBPS)
    assert len(brain_lines(caplog)) == 2


def test_off_computes_nothing_logs_nothing_and_forgets_the_budget(caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    h = Harness(mode=B.ENFORCE)
    h.tick(broadcast=30 * MBPS)
    assert h.budget.rate_bps is not None
    caplog.clear()
    h.mode = B.OFF
    assert h.tick(broadcast=30 * MBPS) is None
    assert h.budget.rate_bps is None and brain_lines(caplog) == []
    h.mode = "garbage"                                   # an unknown mode is shadow, like the veto's switch
    assert h.tick(broadcast=30 * MBPS) is not None and h.budget.mode == B.SHADOW


def test_shadow_computes_and_logs_but_never_delays_a_download(caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    h = Harness(mode=B.SHADOW, ceiling=100 * MBPS)
    d = h.tick(broadcast=100 * MBPS)                     # the stream takes everything: the budget is the floor
    assert d.rate_bps == B.FLOOR_BPS
    line = brain_lines(caplog)[-1]
    assert line["enforced"] is False and "לא מושהית" in line["say"] and "delayed_chunks" not in line

    async def hammer():
        began = time.monotonic()
        for _ in range(200):
            await h.budget.acquire("10.0.0.5", CHUNK)    # 12.8MB at a 1MB/s floor would take 12 s if enforced
        return time.monotonic() - began

    assert asyncio.run(hammer()) < 1.0
    h.tick(broadcast=100 * MBPS)
    assert "delayed_chunks" not in brain_lines(caplog)[-1]


def test_enforce_really_slows_a_download_down_to_the_budget_and_says_how_much(caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    h = Harness(mode=B.ENFORCE, ceiling=10 * MBPS)           # no stream: all of 10 Mbps = 1.25MB/s
    assert h.tick(broadcast=0).rate_bps == 10 * MBPS

    async def download(n):
        began = time.monotonic()
        for _ in range(n):
            await h.budget.acquire("10.0.0.5", CHUNK)
        return time.monotonic() - began

    took = asyncio.run(download(20))                       # 1.31MB: a 0.25 s burst, then 1.25MB/s
    assert 0.5 <= took <= 3.0, took
    h.tick(broadcast=0, served=1_310_720)
    line = brain_lines(caplog)[-1]
    assert line["enforced"] is True and line["delayed_chunks"] >= 5 and line["delayed_s"] > 0.3


def test_enforce_before_the_first_decision_delays_nothing():
    h = Harness(mode=B.ENFORCE)
    assert h.budget.rate_bps is None

    async def go():
        began = time.monotonic()
        for _ in range(50):
            await h.budget.acquire("x", CHUNK)
        return time.monotonic() - began

    assert asyncio.run(go()) < 0.5


def test_two_machines_share_the_budget_in_turns_in_a_real_loop():
    """אחרי הפרץ שמותר אחרי שקט (`BURST_S`, שבו מי שיש לו יותר בקשות בתור לוקח יותר) החלוקה שווה לפי משכפל."""
    h = Harness(mode=B.ENFORCE, ceiling=32 * MBPS)           # 4MB/s
    h.tick(broadcast=0)

    async def go():
        got, began = Counter(), time.monotonic()
        measured_from, until = began + 0.5, began + 1.5

        async def machine(ip, conns):
            async def one():
                while time.monotonic() < until:
                    await h.budget.acquire(ip, CHUNK)
                    if time.monotonic() >= measured_from:
                        got[ip] += CHUNK
            await asyncio.gather(*(one() for _ in range(conns)))
        await asyncio.gather(machine("a", 3), machine("b", 1))
        return got

    got = asyncio.run(go())
    assert got["b"] > 1_500_000, got
    assert abs(got["a"] - got["b"]) <= 4 * CHUNK, got
    assert got["a"] + got["b"] <= 4_000_000 * 1.0 + 8 * CHUNK


def test_switching_from_enforce_to_shadow_releases_whoever_is_waiting():
    h = Harness(mode=B.ENFORCE, ceiling=B.FLOOR_BPS)         # 1MB/s
    h.tick(broadcast=0)

    async def go():
        for _ in range(20):                                # drain the burst so the next ones queue
            await h.budget.acquire("x", CHUNK)
        waiting = [asyncio.create_task(h.budget.acquire("y", CHUNK * 8)) for _ in range(6)]
        await asyncio.sleep(0.05)
        assert not all(w.done() for w in waiting), "they queue behind the budget"
        h.mode = B.SHADOW
        h.tick(broadcast=0)
        began = time.monotonic()
        await asyncio.wait_for(asyncio.gather(*waiting), 2.0)
        return time.monotonic() - began

    assert asyncio.run(go()) < 1.5


def test_a_cycle_that_fails_is_a_log_line_and_the_downloads_go_on(caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    h = Harness(mode=B.ENFORCE)
    h.tick(broadcast=0)
    h.budget._read_fn = lambda: 1 / 0
    assert h.budget.tick() is None
    assert any("cycle failed" in r.getMessage() for r in caplog.records)
    assert asyncio.run(h.budget.acquire("x", CHUNK)) is None


# --- החושים ----------------------------------------------------------------------------------------------------------


def test_the_cards_counters_are_read_from_sysfs_and_a_missing_card_is_unread_not_zero(tmp_path):
    stats = tmp_path / "eth1" / "statistics"
    stats.mkdir(parents=True)
    (stats / "tx_bytes").write_text("123456789\n")
    (stats / "tx_dropped").write_text("7\n")
    assert netsense.nic_counters("eth1", tmp_path) == {"tx_bytes": 123456789, "tx_dropped": 7}
    (stats / "tx_dropped").write_text("garbage\n")
    assert netsense.nic_counters("eth1", tmp_path) == {"tx_bytes": 123456789, "tx_dropped": None}
    for name in ("eth9", None, "", "../eth1", "eth1/statistics"):
        assert netsense.nic_counters(name, tmp_path) == {"tx_bytes": None, "tx_dropped": None}, name


def test_io_pressure_is_the_full_line_of_proc_pressure_and_a_missing_file_is_unread(tmp_path):
    (tmp_path / "pressure").mkdir()
    (tmp_path / "pressure" / "io").write_text(
        "some avg10=0.50 avg60=0.40 avg300=0.10 total=1234567\nfull avg10=0.18 avg60=0.10 avg300=0.05 total=9876543\n")
    assert netsense.psi_io_full_us(tmp_path) == 9876543
    (tmp_path / "pressure" / "io").write_text("some avg10=0.50 total=5\n")
    assert netsense.psi_io_full_us(tmp_path) is None
    assert netsense.psi_io_full_us(tmp_path / "nowhere") is None


def _running_wave(conn, members: dict[str, object]):
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES ('W-1', 'g', 'img', 'ROOM', 1, 1, 'running', 'x', 'now', 0)")
    for mac, drops in members.items():
        conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, udp_drops_json)"
                     " VALUES ('W-1', ?, 'writing', 'now', ?)", (mac, drops if drops is None else json.dumps(drops)))
    conn.commit()


def test_the_receivers_drops_are_summed_only_when_every_machine_of_the_wave_reported(conn):
    assert netsense.receivers_rcvbuf(conn) is None, "no running wave: nothing to report"
    _running_wave(conn, {"aa": {"rcvbuf": 4, "inerrors": 0}, "bb": {"rcvbuf": 11, "inerrors": 0}})
    assert netsense.receivers_rcvbuf(conn) == 15


@pytest.mark.parametrize("drops", [None, {"rcvbuf": None, "inerrors": None}, {"inerrors": 0}, {"rcvbuf": -1},
                                   {"rcvbuf": True}, "x"])
def test_one_machine_that_did_not_report_makes_the_whole_wave_unread_not_clean(conn, drops):
    """אותו כלל של `drive_rates._rcvbuf`: סכום חלקי היה נקרא כ"אפס זריקות". וגלי חתיכות אינם מדווחים את השדה כלל."""
    _running_wave(conn, {"aa": {"rcvbuf": 0, "inerrors": 0}, "bb": drops})
    assert netsense.receivers_rcvbuf(conn) is None


# --- החיבור לשרת -----------------------------------------------------------------------------------------------------


class FakeChunk:
    interface = "eth1"

    def __init__(self, bps=0):
        self.bps = bps

    def sending_bps(self):
        if isinstance(self.bps, Exception):
            raise self.bps
        return self.bps


class FakeSender:
    interface = None

    def __init__(self, status=None, link=None):
        self._status, self._link = status, link or {"speed_mbps": 1000}

    def status(self):
        return self._status

    def link(self):
        return self._link


def test_the_stream_rate_is_the_chunk_engines_then_udpcasts_and_a_question_that_failed_is_unknown(conn, monkeypatch):
    sending = {"session_id": "W-1", "state": "sending"}
    assert B._broadcast_bps(conn, FakeChunk(700 * MBPS), FakeSender()) == 700 * MBPS
    assert B._broadcast_bps(conn, FakeChunk(0), FakeSender()) == 0
    monkeypatch.setattr(B.drive_rates, "decision", lambda c, s: {"max_bitrate": "600m"})
    assert B._broadcast_bps(conn, FakeChunk(0), FakeSender(sending)) == 600 * MBPS
    assert B._broadcast_bps(conn, FakeChunk(0), FakeSender({**sending, "state": "starting"})) == 600 * MBPS
    assert B._broadcast_bps(conn, FakeChunk(0), FakeSender({**sending, "state": "done"})) == 0
    monkeypatch.setattr(B.drive_rates, "decision", lambda c, s: None)
    assert B._broadcast_bps(conn, FakeChunk(0), FakeSender(sending)) is None, "running at an unknown rate is not 'no stream'"
    assert B._broadcast_bps(conn, FakeChunk(RuntimeError("boom")), FakeSender()) is None


def test_the_production_budget_reads_the_engines_the_served_counter_and_the_setting(conn, tmp_path):
    stats = tmp_path / "eth1" / "statistics"
    stats.mkdir(parents=True)
    (stats / "tx_bytes").write_text("0")
    (stats / "tx_dropped").write_text("0")
    served = Served()
    budget = B.production(conn, served=served, chunk=FakeChunk(300 * MBPS), sender=FakeSender(),
                          sys_net=tmp_path, proc=tmp_path / "proc")
    first = budget.tick()
    assert first.broadcast_bps == 300 * MBPS and first.rate_bps == 600 * MBPS and budget.mode == B.SHADOW
    db.set_setting(conn, B.SETTING, "500")
    db.set_setting(conn, "brain_veto_mode", "enforce")
    second = budget.tick()
    assert budget.mode == B.ENFORCE and second.limit_bps == 200 * MBPS, "the setting and the switch are read each cycle"


def test_the_served_counter_adds_up_every_byte_and_never_goes_down_when_a_source_is_forgotten():
    t = [1000.0]
    served = Served(clock=lambda: t[0], keep_s=10)
    assert served.total_all() == 0
    served.note("10.0.0.5", 1000)
    served.note("10.0.0.6", 500)
    served.note("10.0.0.5", 0)
    assert served.total_all() == 1500
    t[0] += 100
    served.note("10.0.0.7", 1)                              # the older sources are pruned past keep_s...
    assert served.total("10.0.0.5") == 1000 and served.total_all() == 1501


def test_the_production_budget_sees_the_files_served_between_two_cycles(conn, tmp_path):
    served = Served()
    budget = B.production(conn, served=served, chunk=FakeChunk(300 * MBPS), sender=FakeSender(),
                          sys_net=tmp_path, proc=tmp_path / "proc")
    budget.tick()
    served.note("10.0.0.5", 5_000_000)
    time.sleep(0.05)
    second = budget.tick()
    assert any(e.startswith("served ") for e in second.evidence), second.evidence


def test_the_production_budget_cuts_when_the_receivers_of_a_running_wave_report_drops(conn, tmp_path):
    _running_wave(conn, {"aa": {"rcvbuf": 0, "inerrors": 0}, "bb": {"rcvbuf": 0, "inerrors": 0}})
    budget = B.production(conn, served=Served(), chunk=FakeChunk(300 * MBPS), sender=FakeSender(),
                          sys_net=tmp_path, proc=tmp_path / "proc")
    assert budget.tick().action == B.HOLD, "the first cycle has no deltas: nothing measured"
    time.sleep(0.05)
    conn.execute("UPDATE session_members SET udp_drops_json = ? WHERE mac = 'bb'", (json.dumps({"rcvbuf": 25, "inerrors": 0}),))
    conn.commit()
    cut = budget.tick()
    assert cut.action == B.CUT_ and "25" in cut.reason, cut


def test_the_production_budget_cuts_on_drops_of_the_servers_own_card(conn, tmp_path):
    stats = tmp_path / "eth1" / "statistics"
    stats.mkdir(parents=True)
    (stats / "tx_bytes").write_text("0")
    (stats / "tx_dropped").write_text("0")
    budget = B.production(conn, served=Served(), chunk=FakeChunk(300 * MBPS), sender=FakeSender(),
                          sys_net=tmp_path, proc=tmp_path / "proc")
    budget.tick()
    time.sleep(0.05)
    (stats / "tx_dropped").write_text("12")
    cut = budget.tick()
    assert cut.action == B.CUT_ and "כרטיס השרת" in cut.reason


# --- הצנרת: ההגשה, המנוע, והשעון --------------------------------------------------------------------------------------


def _file_url():
    from test_brain_served_3064 import _partition_file
    return f"/api/v1/images/img_7f3a91/files/{_partition_file()}"


class Recorder:
    def __init__(self, wait=0.0):
        self.calls, self.wait = [], wait

    async def acquire(self, ip, nbytes):
        self.calls.append((ip, nbytes))
        if self.wait:
            await asyncio.sleep(self.wait)


def test_the_app_builds_the_budget_and_the_download_route_asks_it_for_every_byte_it_sends(server):
    ctx, anon = server["ctx"], server["anon"]
    assert isinstance(ctx.budget, B.Budget) and ctx.budget.mode == B.SHADOW
    ctx.budget = rec = Recorder()
    got = anon.get(_file_url())
    assert got.status_code == 200 and got.content == PARTITION_BYTES
    assert rec.calls and {ip for ip, _ in rec.calls} == {"testclient"}
    assert sum(n for _ip, n in rec.calls) == len(PARTITION_BYTES)
    rec.calls.clear()
    part = anon.get(_file_url(), headers={"Range": "bytes=3-9"})
    assert part.status_code == 206 and part.content == PARTITION_BYTES[3:10]
    assert sum(n for _ip, n in rec.calls) == 7


def test_a_download_waits_for_its_turn_and_still_arrives_whole(server):
    ctx, anon = server["ctx"], server["anon"]
    ctx.budget = rec = Recorder(wait=0.2)
    began = time.monotonic()
    got = anon.get(_file_url())
    assert got.status_code == 200 and got.content == PARTITION_BYTES
    assert time.monotonic() - began >= 0.2 * len(rec.calls) * 0.9


def test_a_refused_or_missing_file_never_reaches_the_budget(server):
    ctx, anon = server["ctx"], server["anon"]
    ctx.budget = rec = Recorder()
    assert anon.get("/api/v1/images/img_7f3a91/files/not-in-the-manifest").status_code == 404
    assert rec.calls == []


def test_the_clock_ticks_the_budget_off_the_event_loop_until_it_is_cancelled():
    from server.app import _brain_budget_clock

    class Counting:
        n = 0

        def tick(self):
            Counting.n += 1

    async def go():
        task = asyncio.create_task(_brain_budget_clock(Counting(), 0.02))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert Counting.n >= 4


def test_the_apps_lifespan_runs_the_budget_clock(server, monkeypatch):
    from fastapi.testclient import TestClient
    ticks = []

    class Counting:
        def tick(self):
            ticks.append(1)

    monkeypatch.setattr(B, "TICK_S", 0.02)
    server["ctx"].budget = Counting()
    with TestClient(server["app"]):
        time.sleep(0.3)
    assert len(ticks) >= 3


def test_a_chunk_engine_with_no_wave_asked_nothing_of_the_stream():
    engine = chunksend.ChunkEngine(None, live_members=lambda sid: [], lost_wait=lambda: 1, write_floor=lambda: 50,
                                   ledger=C.MemLedger())
    assert engine.sending_bps() == 0


def test_the_chunk_engine_reports_the_rate_it_asked_of_the_stream_while_a_wave_is_active_and_zero_after(rig):
    engine = rig.make()                                      # wave_bitrate is "700m"
    assert wait_for(lambda: engine.sending_bps() == 700 * MBPS), engine.sending_bps()
    engine.stop(SID)
    assert wait_for(lambda: engine.sending_bps() == 0), "a stopped wave leaves the stream's room to the downloads"


def test_a_wave_that_failed_asks_nothing_of_the_stream(rig):
    rig.members = []                                         # nobody to send to: the wave fails by name
    engine = rig.make()
    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert engine.sending_bps() == 0
