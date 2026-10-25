"""‏#1847 — מכונה שלא מתעוררת מ-WoL: זיהוי, שליחה חוזרת מוקדמת, התראה.

"נשלח" אינו "התעורר" (#74, #1721). הראיה — המכונה דיברה עם השרת: פירור האתחול
הראשון או hello. מכונה ערה בזמן השליחה אינה מדידה. ו-`wol_arm_failed` פעם אחת
לכל אתחול, לא בכל hello.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from server import boottrace, db, learn_wol, wol

from test_server_room import CLONER1, room_server  # noqa: F401
from conftest import hello_body

MAC = "78:ac:c0:9b:11:c2"
T0 = 1_760_000_000.0


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagectl.db")
    c.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02',"
              " 'grp_CLONERS', 'x')", (MAC,))
    c.commit()
    return c


def _cycle(conn, seconds: float | None, at: float) -> float:
    """הערה אחת שנשלחה ב-`at`, ונענתה אחרי `seconds` (None = לא ענתה)."""
    learn_wol.sent(conn, MAC, now=at)
    if seconds is not None:
        learn_wol.answered(conn, MAC, now=at + seconds)
    return at + learn_wol.ANSWER_S + 10


def test_a_wake_is_measured_from_send_to_the_first_sign_of_life(conn):
    learn_wol.sent(conn, MAC, now=T0)
    learn_wol.answered(conn, MAC, now=T0 + 42)
    learn_wol.answered(conn, MAC, now=T0 + 90)          # ה-hello שאחרי — לא מזיז
    row = conn.execute("SELECT * FROM wol_wakes").fetchone()
    assert row["seconds"] == 42 and row["answered_at"]


def test_a_machine_that_was_awake_is_not_a_measurement(conn):
    conn.execute("INSERT INTO net_devices (mac, last_seen) VALUES (?, ?)",
                 (MAC, datetime.fromtimestamp(T0 - 5, timezone.utc).isoformat()))
    conn.commit()
    learn_wol.sent(conn, MAC, now=T0)
    assert conn.execute("SELECT COUNT(*) FROM wol_wakes").fetchone()[0] == 0


def test_a_resend_while_open_is_the_same_wake(conn):
    learn_wol.sent(conn, MAC, now=T0)
    learn_wol.sent(conn, MAC, now=T0 + 60)              # room_rewake
    learn_wol.answered(conn, MAC, now=T0 + 100)
    (row,) = conn.execute("SELECT * FROM wol_wakes").fetchall()
    assert row["resent"] == 1 and row["seconds"] == 100


def test_stats_rate_and_typical_time(conn):
    at = T0
    for seconds in (40, 50, 60, None):
        at = _cycle(conn, seconds, at)
    got = learn_wol.stats(conn, MAC, now=at)
    assert got["decided"] == 4 and got["answered"] == 3 and got["rate"] == 0.75
    assert got["typical_s"] == 50 and got["streak"] == 1


def test_no_typical_time_without_enough_successes(conn):
    at = _cycle(conn, 40, T0)
    at = _cycle(conn, 50, at)
    assert learn_wol.stats(conn, MAC, now=at)["typical_s"] is None


def test_an_open_wake_is_not_counted_as_failed_yet(conn):
    learn_wol.sent(conn, MAC, now=T0)
    assert learn_wol.stats(conn, MAC, now=T0 + 100)["decided"] == 0


def test_early_resend_after_twice_the_typical_time_once(conn):
    at = T0
    for seconds in (40, 50, 60):
        at = _cycle(conn, seconds, at)                  # טיפוסי 50ש'
    packets: list[bytes] = []
    learn_wol.sent(conn, MAC, now=at)
    learn_wol.sweep(conn, packets.append, now=at + 90)  # 90 < 100 — עוד לא
    assert packets == []
    learn_wol.sweep(conn, packets.append, now=at + 101)
    assert packets == [wol.magic_packet(MAC)]
    learn_wol.sweep(conn, packets.append, now=at + 200)  # פעם אחת בלבד
    assert len(packets) == 1
    line = conn.execute("SELECT detail FROM journal WHERE event = 'wol_resent'").fetchone()
    assert line["detail"].startswith(f"{MAC} after=101s typical=50s")


def test_no_early_resend_without_a_typical_time(conn):
    packets: list[bytes] = []
    learn_wol.sent(conn, MAC, now=T0)
    learn_wol.sweep(conn, packets.append, now=T0 + 500)
    assert packets == []


def test_a_streak_of_unanswered_wakes_alerts_once(conn):
    at = _cycle(conn, 40, T0)
    for _ in range(learn_wol.FAIL_STREAK - 1):
        at = _cycle(conn, None, at)
    learn_wol.sweep(conn, lambda p: None, now=at)
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'wol_no_wake'").fetchone() is None
    at = _cycle(conn, None, at)                         # השלישית ברצף
    learn_wol.sweep(conn, lambda p: None, now=at)
    learn_wol.sweep(conn, lambda p: None, now=at + 60)
    lines = conn.execute("SELECT detail FROM journal WHERE event = 'wol_no_wake'").fetchall()
    assert len(lines) == 1 and "לא מתעורר מ-WoL — BIOS/כבל" in lines[0]["detail"]
    (row,) = learn_wol.health_checks(conn, now=at)
    assert row["id"] == f"wol:{MAC}" and row["state"] == "warn" and row["label"] == "02"


def test_an_answer_ends_the_streak(conn):
    at = T0
    for _ in range(learn_wol.FAIL_STREAK):
        at = _cycle(conn, None, at)
    at = _cycle(conn, 45, at)
    (row,) = learn_wol.health_checks(conn, now=at)
    assert row["id"] == "wol" and row["state"] == "ok"


def test_no_wakes_no_health_row(conn):
    assert learn_wol.health_checks(conn) == []


def test_wake_machine_and_wake_group_record_the_wake(conn):
    wol.wake_machine(conn, MAC, send=lambda p: None)
    assert conn.execute("SELECT COUNT(*) FROM wol_wakes").fetchone()[0] == 1
    conn.execute("DELETE FROM wol_wakes")
    conn.commit()
    wol.wake_group(conn, "grp_CLONERS", send=lambda p: None)
    assert conn.execute("SELECT mac FROM wol_wakes").fetchone()["mac"] == MAC


def test_a_failed_send_is_not_a_wake(conn):
    def broken(_p):
        raise OSError("no carrier")
    wol.wake_machine(conn, MAC, send=broken)
    assert conn.execute("SELECT COUNT(*) FROM wol_wakes").fetchone()[0] == 0


def test_the_first_boot_breadcrumb_answers(conn):
    learn_wol.sent(conn, MAC, now=time.time() - 30)
    assert boottrace.record(conn, MAC, "menu")
    row = conn.execute("SELECT * FROM wol_wakes").fetchone()
    assert row["answered_at"] and 25 <= row["seconds"] <= 60


def test_the_hello_answers_and_arm_failure_is_journaled_once_per_boot(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    learn_wol.sent(ctx.conn, CLONER1, now=time.time() - 30)
    body = {**hello_body(CLONER1), "wol_armed": False}
    for _ in range(3):
        assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    assert ctx.conn.execute("SELECT answered_at FROM wol_wakes").fetchone()["answered_at"]
    n = lambda: ctx.conn.execute(                       # noqa: E731
        "SELECT COUNT(*) FROM journal WHERE event = 'wol_arm_failed'").fetchone()[0]
    assert n() == 1
    # אתחול חדש: פירור `menu` מאפס את first_at — שורה אחת נוספת, לא יותר
    time.sleep(1.1)
    boottrace.record(ctx.conn, CLONER1, "menu")
    for _ in range(2):
        anon.post("/api/v1/agent/hello", json=body)
    assert n() == 2


def test_arm_failure_dedupe_without_a_boot_trail_is_daily(conn):
    assert learn_wol.arm_failure_is_new(conn, MAC)
    from server.db import journal
    journal(conn, "wol_arm_failed", MAC)
    assert not learn_wol.arm_failure_is_new(conn, MAC)
    assert learn_wol.arm_failure_is_new(conn, MAC, now=time.time() + 86400 + 60)
