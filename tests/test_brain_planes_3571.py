"""#3571: שלושה מישורי החלטה -- רשת, אחסון לכל דיסק, הפצה -- והשולח מציית למחמיר. בצל.

ההחלטה נרשמת ב-`brain_decisions` ואינה משנה את פקודת `chunkcast send`. חסר נמדד בשמו
(`NOT_MEASURED` / `UNDECIDED` / `STALE`), ולעולם לא כ"הכול תקין". מתג הביצוע `brain_planes`
כבוי כברירת מחדל, וגם כשהוא דלוק יחד עם `enforce` אין כאן מוציא לפועל.

**בקרה שלילית:** `judge` שמחזיר `CLEAR` בלי לקרוא את המישורים מפיל את הטסטים על הפסק, לא על import.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import forecast, say

try:
    from server.brain import planes
except ImportError:  # pragma: no cover - הבקרה השלילית מוחקת את המודול רק אחרי שיש טסטים
    planes = None

from test_chunksend_2533 import SID, rig, wait_for  # noqa: F401

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _need():
    assert planes is not None, "המישורים לא החליטו: אין פסק, לא שגיאת ייבוא"


def _disk(serial="FAST", durable_bps=100_000_000, backlog_bytes=1_000_000, slope_bps=0, window_s=3.0,
          behind_ticks=0):
    return planes.Disk(serial, durable_bps, backlog_bytes, slope_bps, window_s, behind_ticks)


def _obs(*, net=None, store=None, dist=None, current_kbps=400_000):
    """שלושה מישורים שנמדדו, אלא אם מחליפים אחד. אחסון: 100MB/s עמיד, תור קצר, ספרייה מהירה יותר."""
    _need()
    net = planes.Net(0, 20.0, 0, 100, False) if net is None else net
    if store is None:
        store = planes.Store((_disk(),), 200_000_000, 100, False, current_kbps)
    dist = planes.Dist(0, 2, 2, 500_000, False, 100, False) if dist is None else dist
    return planes.Observe(net, store, dist, NOW)


def _kbps(bps):
    return int(bps * 8 / 1000)


def _tfrc(loss_permille, rtt_ms):
    """RFC 5348 משוואה 3, אותו חישוב שהמוח חייב להחזיר. אורקל בטסט, לא עותק מהמודול."""
    p, rtt_s = loss_permille / 1000, rtt_ms / 1000
    denom = math.sqrt(2 * p / 3) + 12 * math.sqrt(3 * p / 8) * p * (1 + 32 * p * p)
    return int(planes.NET_PACKET_B / (rtt_s * denom) * 8 / 1000)


def test_the_packet_size_is_the_chunkcast_frame():
    _need()
    assert planes.NET_PACKET_B == 16 + 16 + 1440


def test_a_measured_loss_caps_the_network_and_does_not_blame_the_disk():
    """#3141: ניתוק רשת אינו "הדיסק לא עמד". האחסון נשאר קצב, והתקרה המחמירה היא הרשת."""
    _need()
    found = planes.judge(_obs(net=planes.Net(50, 30.0, 4, 100, False)))
    assert found.net.verdict == "CAPPED"
    assert found.net.ceiling_kbps == _tfrc(50, 30.0)
    assert found.store.verdict == "PACE"
    assert "אבדן" not in found.store.reason
    assert found.verdict == "NET"
    assert found.ceiling_kbps == found.net.ceiling_kbps
    assert found.ceiling_kbps < found.store.ceiling_kbps


def test_zero_loss_is_clear_only_when_retransmits_were_measured_at_zero():
    _need()
    clear = planes.judge(_obs())
    assert clear.net.verdict == "CLEAR" and clear.net.ceiling_kbps is None and clear.net.counts
    clash = planes.judge(_obs(net=planes.Net(0, 20.0, 3, 100, False)))
    assert clash.net.verdict == "UNDECIDED" and clash.net.ceiling_kbps is None and not clash.net.counts
    missing = planes.judge(_obs(net=planes.Net(0, 20.0, None, 100, False)))
    assert missing.net.verdict == "NOT_MEASURED" and missing.net.counts is False


def test_a_stale_report_expires_and_is_not_a_ceiling():
    _need()
    found = planes.judge(_obs(net=planes.Net(50, 30.0, 4, planes.REPORT_STALE_MS + 1, False)))
    assert found.net.verdict == "STALE" and found.net.ceiling_kbps is None and not found.net.counts
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None


def test_an_identical_report_is_suppressed_and_does_not_step_the_rate():
    _need()
    high = _disk(backlog_bytes=80_000_000)  # 0.8s, מעל STORE_HIGH_S
    store = planes.Store((high,), 200_000_000, 100, True, 10_000)
    found = planes.judge(_obs(store=store))
    assert found.store.verdict == "SUPPRESSED"
    assert found.store.step == "hold" and found.store.proposed_kbps == 10_000
    assert found.store.counts and found.store.ceiling_kbps == _kbps(100_000_000)


def test_a_fresh_high_backlog_steps_down_by_at_most_one_tick():
    _need()
    high = _disk(backlog_bytes=80_000_000)
    store = planes.Store((high,), 200_000_000, 100, False, 10_000)
    found = planes.judge(_obs(store=store))
    assert found.store.step == "down"
    assert found.store.proposed_kbps == 10_000 - 10_000 * planes.STORE_STEP_PCT // 100
    assert found.store.proposed_kbps != found.store.ceiling_kbps


def test_the_deadband_holds_a_step_smaller_than_the_band():
    _need()
    # התור קצר, אז הכיוון למעלה, אבל התקרה קרובה מה-deadband
    store = planes.Store((_disk(),), 200_000_000, 100, False, 1000)
    cap = 1000 + (1000 * planes.STORE_DEADBAND_PCT // 100) - 1
    # התקרה של הדיסק גדולה בהרבה; מצמצמים אותה דרך הספרייה
    store = planes.Store((_disk(),), cap * 1000 // 8, 100, False, 1000)
    found = planes.judge(_obs(store=store))
    assert found.store.ceiling_kbps == cap
    assert found.store.step == "hold" and found.store.proposed_kbps == 1000


def test_the_band_between_low_and_high_holds():
    _need()
    mid = _disk(backlog_bytes=40_000_000)  # 0.4s, בין 0.2 ל-0.6
    store = planes.Store((mid,), 200_000_000, 100, False, 10_000)
    found = planes.judge(_obs(store=store))
    assert found.store.step == "hold" and found.store.proposed_kbps == 10_000


def test_a_short_window_is_not_a_rate():
    _need()
    disk = _disk(window_s=planes.STORE_WINDOW_S - 0.1)
    found = planes.judge(_obs(store=planes.Store((disk,), 200_000_000, 100, False, 10_000)))
    assert found.store.verdict == "UNDECIDED" and found.store.ceiling_kbps is None and not found.store.counts
    assert found.verdict == "UNDECIDED"


def test_one_unmeasured_disk_does_not_let_the_others_set_the_pace():
    _need()
    bad = _disk("SLOW", backlog_bytes=None)
    store = planes.Store((_disk(), bad), 200_000_000, 100, False, 10_000)
    found = planes.judge(_obs(store=store))
    assert found.store.verdict == "UNDECIDED" and found.store.ceiling_kbps is None
    assert found.ceiling_kbps is None


def test_a_consistently_slow_disk_stays_behind_and_a_new_one_does_not():
    _need()
    # התור של האיטי ארוך; של המהיר קצר. האיטי לא מושך את הצעד.
    slow = planes.Disk("SLOW", 40_000_000, 80_000_000, 1_000_000, 3.0, planes.STORE_BEHIND_MAX)
    fast = planes.Disk("FAST", 100_000_000, 1_000_000, 0, 3.0, 0)
    store = planes.Store((fast, slow), 200_000_000, 100, False, 400_000)
    found = planes.judge(_obs(store=store))
    assert found.store.verdict == "PACE"
    assert found.store.ceiling_kbps == _kbps(100_000_000)
    assert any(e == "behind=SLOW" for e in found.store.evidence)
    fresh = planes.Disk("SLOW", 40_000_000, 80_000_000, 1_000_000, 3.0, planes.STORE_BEHIND_MAX - 1)
    early = planes.judge(_obs(store=planes.Store((fast, fresh), 200_000_000, 100, False, 400_000)))
    assert early.store.ceiling_kbps == _kbps(40_000_000)
    assert not any(e.startswith("behind=") for e in early.store.evidence)


def test_the_only_disk_is_not_left_behind():
    _need()
    only = planes.Disk("ONLY", 40_000_000, 80_000_000, 1_000_000, 3.0, planes.STORE_BEHIND_MAX)
    found = planes.judge(_obs(store=planes.Store((only,), 200_000_000, 100, False, 400_000)))
    assert found.store.ceiling_kbps == _kbps(40_000_000)
    assert not any(e.startswith("behind=") for e in found.store.evidence)


def test_a_missing_library_read_is_not_the_disk_pace():
    _need()
    found = planes.judge(_obs(store=planes.Store((_disk(),), None, 100, False, 400_000)))
    assert found.store.verdict == "UNDECIDED" and found.store.ceiling_kbps is None
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None
    assert any(e.startswith("partial_kbps=") for e in found.store.evidence)


def test_distribution_missing_does_not_become_the_min_of_the_other_two():
    _need()
    found = planes.judge(_obs(dist=planes.Dist(None, None, None, None, None, None, None)))
    assert found.dist.verdict == "NOT_MEASURED" and not found.dist.counts
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None
    assert found.store.ceiling_kbps is not None  # נמדד, אבל לא הופך לתקרה של השולח לבד


def test_no_peer_is_a_measured_zero_and_nobody_seen_is_not():
    _need()
    none = planes.judge(_obs(dist=planes.Dist(4, 0, 0, 1000, False, 100, False)))
    assert none.dist.verdict == "NOT_MEASURED" and none.dist.ceiling_kbps is None
    blocked = planes.judge(_obs(dist=planes.Dist(4, 0, 2, 1000, False, 100, False)))
    assert blocked.dist.verdict == "NO_PEER" and blocked.dist.ceiling_kbps == 0 and blocked.dist.counts
    assert blocked.verdict == "DIST" and blocked.ceiling_kbps == 0
    fallback = planes.judge(_obs(dist=planes.Dist(4, 0, 2, 3000, True, 100, False)))
    assert fallback.dist.verdict == "CAPPED" and fallback.dist.ceiling_kbps == 3000
    assert "שרת" in fallback.dist.reason


def test_the_strictest_of_three_measured_planes_wins():
    _need()
    # רשת לא מגבילה (אבדן 0), אחסון 800000, הפצה 3000
    found = planes.judge(_obs(dist=planes.Dist(4, 1, 2, 3000, False, 100, False)))
    assert found.net.verdict == "CLEAR" and found.store.counts and found.dist.counts
    assert found.verdict == "DIST" and found.ceiling_kbps == 3000


def test_a_bool_is_not_a_measurement():
    _need()
    found = planes.judge(_obs(net=planes.Net(True, 20.0, 0, 100, False)))
    assert found.net.verdict == "NOT_MEASURED"


def test_obey_stays_off_unless_enforce_and_the_setting_and_a_real_ceiling():
    _need()
    found = planes.judge(_obs(dist=planes.Dist(4, 1, 2, 3000, False, 100, False)))
    assert found.ceiling_kbps == 3000
    assert planes.obey("shadow", "on", found) is None
    assert planes.obey("enforce", None, found) is None
    assert planes.obey("enforce", "off", found) is None
    assert planes.obey("enforce", "on", found) == 3000
    empty = planes.judge(planes.Observe())
    assert empty.verdict == "UNDECIDED" and planes.obey("enforce", "on", empty) is None
    assert planes.SETTING == "brain_planes"


def test_decide_records_each_plane_and_does_not_apply(conn):
    _need()
    found = planes.decide(conn, _obs(dist=planes.Dist(4, 1, 2, 3000, False, 100, False)), round_id="w-1")
    assert found.verdict == "DIST" and found.ceiling_kbps == 3000
    rows = [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = ? ORDER BY id", (planes.RULE,))]
    assert [r["action"] for r in rows] == ["PLANE_NET", "PLANE_STORE", "PLANE_DIST", "PLANE_BIND"]
    assert all(r["mode"] == "shadow" for r in rows)
    assert all("לא שונה" in r["outcome"] for r in rows)
    bind = rows[-1]
    assert bind["verdict"] == "DIST" and bind["entity_kind"] == "round" and bind["entity_key"] == "w-1"
    from server.brain import memory
    shown = memory.decisions(conn, rule=planes.RULE)
    assert shown[0]["action"] == "PLANE_BIND" and "הפצה" in shown[0]["line_he"] and "רק נרשם" in shown[0]["line_he"]
    assert shown[0]["snapshot"] and shown[0]["snapshot"]["id"].startswith("s1-")
    assert planes.RULE not in say.EXECUTED
    assert forecast.predicts(planes.RULE)[0] is None


def test_off_decides_and_does_not_record(conn):
    _need()
    db.set_setting(conn, "brain_veto_mode", "off")
    found = planes.decide(conn, _obs(), round_id="w-off")
    assert found.verdict == "STORE" and found.ceiling_kbps == _kbps(100_000_000)
    assert conn.execute("SELECT COUNT(*) AS n FROM brain_decisions").fetchone()["n"] == 0


def test_note_send_records_unmeasured_once_and_the_command_is_the_old_one(rig, conn):
    """השולח עדיין לא מדווח דיסק-דיסק, RTT, או מלאי. השורה אומרת שלא נמדד, והדגלים לא זזים."""
    _need()
    engine = rig.make()
    engine.brain_conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=120)
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    assert cmd[cmd.index("--kbps") + 1] == "700000"
    assert cmd[cmd.index("--write-kbps") + 1] == str(int(120 * 0.9) * 8000)
    rows = [dict(r) for r in conn.execute(
        "SELECT action, verdict FROM brain_decisions WHERE rule = ? ORDER BY id", (planes.RULE,))]
    assert [r["action"] for r in rows] == ["PLANE_NET", "PLANE_STORE", "PLANE_DIST", "PLANE_BIND"]
    assert all(r["verdict"] == "NOT_MEASURED" for r in rows[:3])
    assert rows[3]["verdict"] == "UNDECIDED"
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=120)
    assert wait_for(lambda: len(rig.runner.commands) >= 1)
    again = conn.execute("SELECT COUNT(*) AS n FROM brain_decisions WHERE rule = ?", (planes.RULE,)).fetchone()["n"]
    assert again == 4


def test_the_labels_cover_the_actions_and_the_verdicts():
    _need()
    for action in (planes.ACTION_NET, planes.ACTION_STORE, planes.ACTION_DIST, planes.ACTION_BIND):
        assert action in say.ACTION_HE
    for verdict in ("STALE", "SUPPRESSED", "CLEAR", "CAPPED", "NO_PEER", "NET", "STORE", "DIST"):
        assert verdict in say.VERDICT_HE
