"""#3185 (סעיף 8): **סיכום סבב לתמיד** -- שורה לכל סבב חדר ב-`memory.SOURCES`.

מה נבדק:
- סבב שנסגר לפני יותר מ-`ROUND_DAYS` מסוכם: מתי, אימג', כמה דיסקים, כמה הצליחו, משך, קצב, איבוד וסיבת כשל.
  `knowledge("round", id)` מחזיר אותו.
- **הטבלה נשארת:** `room_rounds` היא טבלת פעולות שההיסטוריה בקונסולה קוראת -- הסיכום אינו מוחק דבר.
- **פעם אחת:** ניקוי נוסף אינו מסכם שוב (אחרת הסכומים היו מוכפלים בכל יום).
- **הקצב והאיבוד שורדים את הדגימות:** הסיכום נכתב לפני ש-`wave_rates` ו-`drive_rates` מוחקים אותן, והוא נשאר אחריהן.
- **ערך שלא נמדד אינו אפס:** סבב בלי אף גל מדווח אינו נרשם עם איבוד 0.
- סבב שעוד פתוח, או שנסגר לאחרונה, אינו מסוכם.
"""

from __future__ import annotations

import json
from datetime import timedelta

from server.brain import memory
from server.system import housekeeping
from test_brain_memory_3185 import NOW, ago, conn, count, summary   # noqa: F401 -- conn is a fixture


def close_round(conn, round_id, days, *, image="img_a", target=8, written=8, state="closed", reason=None,
                minutes=40):
    """A room round that closed `days` ago and ran `minutes`."""
    conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, written_drives, state, opened_by,"
                 " created_at, closed_at, failed_reason) VALUES (?,?,?,?,?,?,?,?,?)",
                 (round_id, image, target, written, state, "noc",
                  (NOW - timedelta(days=days, minutes=minutes)).isoformat(timespec="seconds"), ago(days), reason))


def wave_in(conn, session, days, *, loss=None, sent=None, bps=None):
    """A wave `days` ago (inside the round that closed after it), with the stream the engine reported."""
    stream = None if loss is None and sent is None else json.dumps({"loss_blocks": loss, "sent_bytes": sent})
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?,?,?,?)",
                 (session, json.dumps({"max_bitrate": "600m"}), ago(days), stream))
    for n, rate_bps in enumerate(bps or ()):
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?,?,?,?,?,?,?,?)", (f"S{n}", "M", session, rate_bps, 100_000_000, 2_000_000_000,
                                                    100, ago(days)))


def summed(conn, round_id):
    return summary(conn, "round", round_id, "room_rounds")


def test_the_round_is_a_source_that_keeps_its_table():
    source = memory.SOURCES["room_rounds"]
    assert source.table == "room_rounds" and source.keep is True


def test_a_round_past_the_window_is_summed_with_what_the_operator_reads_in_the_history(conn):
    close_round(conn, "rnd_old", 85, image="img_win11", target=8, written=7, state="failed",
                reason="מגירה 3 איבדה את הכבל", minutes=40)
    wave_in(conn, "w1", 85.02, loss=5, sent=4_000_000_000, bps=[100, 300])
    wave_in(conn, "w2", 85.01, loss=7, sent=1_000_000_000, bps=[200, 400])
    conn.commit()
    result = housekeeping.purge_tables(conn, NOW)
    assert result["failed"] == {}
    got = summed(conn, "rnd_old")
    assert got["rows"] == 1 and got["last_at"] == ago(85)
    num = {k: v["last"] for k, v in got["num"].items()}
    assert num == {"disks": 8, "ok": 7, "seconds": 2400, "rate_bps": 250, "loss_blocks": 12,
                   "sent_bytes": 5_000_000_000, "waves": 2}, num
    text = {k: v["value"] for k, v in got["text"].items()}
    assert text == {"when": (NOW - timedelta(days=85, minutes=40)).isoformat(timespec="seconds"),
                    "image_id": "img_win11", "state": "failed", "reason": "מגירה 3 איבדה את הכבל"}, text


def test_the_console_and_the_brain_read_it_through_knowledge(conn):
    close_round(conn, "rnd_old", 85)
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    known = memory.knowledge(conn, "round", "rnd_old")
    assert known["summary"]["room_rounds"]["num"]["ok"]["last"] == 8
    assert [r["id"] for r in known["recent"]["room_rounds"]] == ["rnd_old"]     # the table, as it was
    assert [e["key"] for e in memory.entities(conn, "round")] == ["rnd_old"]


def test_the_table_is_not_touched_and_the_journal_says_summed_not_deleted(conn):
    close_round(conn, "rnd_old", 85)
    close_round(conn, "rnd_failed", 86, state="failed", written=0, reason="x")
    conn.commit()
    result = housekeeping.purge_tables(conn, NOW)
    assert count(conn, "room_rounds") == 2
    assert "room_rounds" not in result["deleted"], result
    line = conn.execute("SELECT detail FROM journal WHERE event = 'retention_summed'").fetchone()[0]
    assert "room_rounds=2" in line
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'retention_purged'").fetchone()[0] == 0


def test_a_second_pass_does_not_sum_the_round_again(conn):
    close_round(conn, "rnd_old", 85)
    wave_in(conn, "w1", 85.01, loss=5, sent=1, bps=[100])
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    first = json.dumps(memory.summary(conn, "round", "rnd_old"), sort_keys=True)
    housekeeping.purge_tables(conn, NOW + timedelta(days=1))
    housekeeping.purge_tables(conn, NOW + timedelta(days=2))
    assert json.dumps(memory.summary(conn, "round", "rnd_old"), sort_keys=True) == first
    assert summed(conn, "rnd_old")["rows"] == 1 and summed(conn, "rnd_old")["num"]["loss_blocks"]["n"] == 1


def test_the_rate_and_the_loss_outlive_the_samples_they_came_from(conn):
    """The waves are older than the 90 days a week after the round was summed: they go, and the round keeps
    what they said -- the whole reason the round is summed first."""
    close_round(conn, "rnd_old", 80, minutes=180)
    wave_in(conn, "first", 80.1, loss=3, sent=10, bps=[100])             # the first wave of a long round
    wave_in(conn, "last", 80.01, loss=4, sent=20, bps=[300])
    conn.commit()
    housekeeping.purge_tables(conn, NOW)                                  # 80 days old: inside the window
    assert summed(conn, "rnd_old")["rows"] == 0, "a round newer than ROUND_DAYS was summed"
    housekeeping.purge_tables(conn, NOW + timedelta(days=4))              # 84 days: summed, the samples still there
    assert summed(conn, "rnd_old")["rows"] == 1 and count(conn, "wave_rates", "session_id = 'first'") == 1
    # the newest MEMORY_SCAN waves are never purged (the automatic rate reads them): make them many more
    for n in range(housekeeping.drive_rates.MEMORY_SCAN + 5):
        wave_in(conn, f"newer{n}", 1)
    conn.commit()
    housekeeping.purge_tables(conn, NOW + timedelta(days=12))             # 92 days: the samples leave
    assert count(conn, "wave_rates", "session_id IN ('first', 'last')") == 0
    housekeeping.purge_tables(conn, NOW + timedelta(days=200))            # and a year on, the round is still known
    num = {k: v["last"] for k, v in summed(conn, "rnd_old")["num"].items()}
    assert num["loss_blocks"] == 7 and num["rate_bps"] == 200 and num["waves"] == 2, num


def test_a_round_with_no_wave_reporting_is_not_a_round_with_no_loss(conn):
    close_round(conn, "rnd_old", 85)
    wave_in(conn, "silent", 85.01)                                        # a wave whose stream was never read
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    got = summed(conn, "rnd_old")
    assert "loss_blocks" not in got["num"] and "sent_bytes" not in got["num"] and "rate_bps" not in got["num"], got
    assert got["num"]["waves"]["last"] == 1 and got["num"]["ok"]["last"] == 8


def test_a_stream_that_is_not_json_does_not_stop_the_other_rounds_from_being_summed(conn):
    close_round(conn, "rnd_a", 86)
    close_round(conn, "rnd_b", 85)
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES ('bad', '{}', ?, 'not json')",
                 (ago(86.01),))
    wave_in(conn, "good", 85.01, loss=2, sent=9, bps=[50])
    conn.commit()
    result = housekeeping.purge_tables(conn, NOW)
    assert result["failed"] == {}
    assert summed(conn, "rnd_a")["rows"] == 1 and summed(conn, "rnd_b")["num"]["loss_blocks"]["last"] == 2
    assert "loss_blocks" not in summed(conn, "rnd_a")["num"]


def test_a_round_that_is_open_or_recent_is_not_summed(conn):
    close_round(conn, "rnd_recent", 10)
    conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
                 " VALUES ('rnd_open', 'img_a', 4, 'active', 'noc', ?)", (ago(200),))
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    assert memory.entities(conn, "round") == []
