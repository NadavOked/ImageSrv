"""‏#1846 — אבדן חבילות ברשת ההפצה מול הבסיס שלה.

הבסיס = חציון הגלים הקודמים **על אותו כרטיס, באותו מספר מגירות ובאותו קצב**.
קפיצה = פי 3 מהבסיס וגם מעל 0.1%. בלי בסיס — "לא נבדק", לא "תקין".
"""

from __future__ import annotations

import json

import pytest

from server import db, drive_rates, health, learn_netloss

SENT = 19_500_000_000
BLOCKS = -(-SENT // drive_rates.UDPCAST_BLOCK)
MAC = "78:ac:c0:9b:11:c2"
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagectl.db")


def _wave(conn, loss: float | None, *, rate="400m", drawers=2, interface="eth1",
          direct=False) -> str:
    """גל שהסתיים — ההחלטה (עם כרטיס ההפצה) וסוף הזרם דרך `record_stream`."""
    sid = f"ses_n{next(_seq):05d}"
    roster = [{"mac": MAC, "serial": f"{sid}-{k}", "model": "M"} for k in range(drawers)]
    decision = {"max_bitrate": rate, "roster": roster,
                "link": {"interface": interface, "speed_mbps": 1000, "why": None,
                         "applied": True}}
    if direct:
        decision["direct"] = True
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                 (sid, json.dumps(decision), db.now_iso()))
    conn.commit()
    drive_rates.record_stream(conn, sid, {
        "sent_bytes": SENT, "loss_blocks": None if loss is None else round(BLOCKS * loss),
        "wall_s": 400, "ended": "send_done", "dropped_clients": 0, "timeouts": 10})
    return sid


def _base(conn, **kw):
    for loss in (0.004, 0.005, 0.006):
        _wave(conn, loss, **kw)


def test_a_jump_in_the_same_conditions_is_journaled(conn):
    _base(conn)
    sid = _wave(conn, 0.02)                         # 2% מול חציון 0.5% — פי 4
    got = learn_netloss.evaluate(conn, sid)
    assert got["state"] == "jump" and got["waves"] == 3
    assert got["baseline"] == pytest.approx(0.005, rel=0.01)
    line = conn.execute("SELECT detail FROM journal WHERE event = 'net_loss_jump'").fetchone()
    assert line and line["detail"].startswith(f"{sid} if=eth1 drawers=2 rate=400m loss=2.00%")
    assert "סוויץ'/כבל/IGMP" in line["detail"]


def test_within_the_baseline_is_ok_and_silent(conn):
    _base(conn)
    sid = _wave(conn, 0.012)                        # פי 2.4 — לא קפיצה
    assert learn_netloss.evaluate(conn, sid)["state"] == "ok"
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'net_loss_jump'").fetchone() is None


def test_another_rate_or_drawer_count_is_not_a_baseline(conn):
    """קפיצה שבאה עם קצב אחר או מספר מגירות אחר — חיפוש השיא, לא הרשת."""
    _base(conn)
    assert learn_netloss.evaluate(conn, _wave(conn, 0.02, rate="500m"))["state"] == "unchecked"
    assert learn_netloss.evaluate(conn, _wave(conn, 0.02, drawers=4))["state"] == "unchecked"
    assert learn_netloss.evaluate(conn, _wave(conn, 0.02, interface="eth2"))["state"] == "unchecked"


def test_fewer_than_n_waves_is_unchecked_not_fine(conn):
    _wave(conn, 0.004)
    _wave(conn, 0.005)
    got = learn_netloss.evaluate(conn, _wave(conn, 0.05))
    assert got["state"] == "unchecked" and got["waves"] == 2 and got["baseline"] is None


def test_a_zero_baseline_does_not_turn_every_block_into_a_jump(conn):
    """מתחת ל-0.1% udp-sender עצמו מדפיס 0.0% — אין שם קפיצה."""
    _base(conn, )
    for _ in range(3):
        _wave(conn, 0.0, rate="300m")
    assert learn_netloss.evaluate(conn, _wave(conn, 0.0008, rate="300m"))["state"] == "ok"
    assert learn_netloss.evaluate(conn, _wave(conn, 0.002, rate="300m"))["state"] == "jump"


def test_an_unread_counter_and_a_direct_wave_are_not_measurements(conn):
    _base(conn)
    assert learn_netloss.evaluate(conn, _wave(conn, None)) is None
    assert learn_netloss.evaluate(conn, _wave(conn, 0.5, direct=True)) is None
    # ולא נכנסים לבסיס של הבאים
    assert learn_netloss.evaluate(conn, _wave(conn, 0.02))["waves"] == 3


def test_later_waves_are_not_the_baseline_of_an_earlier_one(conn):
    _base(conn)
    first = _wave(conn, 0.02)
    for _ in range(5):
        _wave(conn, 0.02)
    assert learn_netloss.assess(conn, first)["state"] == "jump"


def test_the_health_screen_row(conn):
    assert learn_netloss.health_checks(conn) == []          # אף גל — אין שורה
    _wave(conn, 0.004)
    (row,) = learn_netloss.health_checks(conn)
    assert row["state"] == "off" and row["detail"].startswith("לא נבדק — 0 מתוך 3")
    _base(conn)
    _wave(conn, 0.02)
    (row,) = learn_netloss.health_checks(conn)
    assert row["id"] == "net_loss:eth1" and row["state"] == "warn"
    assert row["detail"].startswith("האבדן ברשת ההפצה קפץ — סוויץ'/כבל/IGMP: 2.00% מול בסיס")


def test_the_health_screen_carries_it():
    """‏`health.collect` מוסיף את השורות — נבדק על המקור, כי collect דורש שרת שלם."""
    import inspect
    assert "_learn_netloss.health_checks(ctx.conn)" in inspect.getsource(health.collect)


def test_the_sender_end_evaluates_the_wave():
    import inspect
    from server import app
    src = inspect.getsource(app)
    assert "learn_netloss.evaluate(conn, session_id)" in src


def test_a_broken_database_is_a_named_unchecked_row():
    class Broken:
        def execute(self, *a):
            raise RuntimeError("db gone")
    (row,) = learn_netloss.health_checks(Broken())
    assert row["state"] == "off" and "לא נבדק" in row["detail"]
    assert learn_netloss.evaluate(Broken(), "x") is None
