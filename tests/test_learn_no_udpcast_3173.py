"""#3173 (D, הלמידה): udpcast נמחק, ושורות `wave_rates` ישנות שלו ב-DB משודרג נשארות בטבלה ואינן נקראות להחלטה.

הכרעת נדב 21/11: מוחקים את udpcast לפני ההקפאה. המונים שרק udp-sender ייצר (שידורים חוזרים, `Timeout notAnswered`,
`Dropping client`, ה-goodput של #1758, `stream_bps`/`wall_s` ברמה העליונה של `stream`) יצאו עם הקוד שקרא אותם, ואין
להם מקביל במנוע החתיכות. ב-DB של שרת ששודרג יש עדיין שורות כאלה. ההכרעה (הכי פחות הרסני): הן נשארות כמות שהן,
ואף קורא אינו לוקח מהן החלטה:

- הקצב לגל (`drive_rates.choose`, K8): גל udpcast אינו "הגל הקודם" של אף מכונה (`_chunk_wave`), ו-`unsafe_rates`
  אינה נקראת. מכונה שיש לה רק שורה כזו יוצאת בפתיחה, כמו מכונה חדשה; מכונה שיש לה גם גל חתיכות — ממנו.
- האבדן ברשת (`learn_netloss`, LR16): שורה כזו אינה מדידה ואינה בסיס, ואין לה שורה במסך הבריאות.
- סבב הלמידה (`learn.tuning`, #2028): אינה ראיה לסבב, ואומרת למה.
- חיזוי זמן הגל (`learn.eta`) ויכולת הקבלה (`brain.capability`, LR24): אין לה משך זרם ואין לה קצב שהושג.

**בקרה שלילית** (`bakara-shlilit`): כל חמשת קובצי הייצור של D מוחזרים לבסיס `7ba2f2f03` יחד —
`server/rounds/drive_rates.py`, `server/learn/netloss.py`, `server/learn/tuning.py`, `server/learn/eta.py`,
`server/brain/capability.py` — ושבעה טסטים כאן נופלים. שלושה הם שומרים שעוברים בשני הצדדים, כי ההתנהגות שלהם נשמרה ולא שונתה:
`test_the_old_rows_stay_in_the_tables` (הבסיס לא מחק שורות גם הוא), #1842 ו-#1884/#1885 על שורות חתיכות.
נמדד ב-21/11 בתחנה (Python 3.12, 7 נכשלו ו-3 עברו): בבסיס הקצב הוא `held` מהשורה
הישנה (`previous` = `ses_udp_old`, ו-`unsafe_rates` 500m חוסם) במקום `start`; 600m `held` במקום 450m `unmeasured` מגל
החתיכות; 450m `drawers` במקום 337m; `assess` מחזיר `kind=udpcast`; `_evidence` מחזיר None (ראיה); `_stream_clock`
מחזיר `('send_done', 300)`; ו-`record_stream` קיים. לא ישן ואין רשת.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import capability
from server.learn import eta, netloss, tuning
from server.rounds import drive_rates

MAC = "aa:bb:cc:31:73:01"
SERIAL, MODEL = "S3173-1", "M3173"
GB = 10 ** 9
DRIVES = [{"serial": SERIAL, "model": MODEL, "mac": MAC, "port": 1}]
ROSTER = [{"mac": MAC, "serial": SERIAL, "model": MODEL}]
LINK = {"interface": "eth1", "speed_mbps": 1000, "why": None, "applied": True}

#: גל udpcast נקי ב-600m, כפי ש-`record_stream` ו-`record_wave` השאירו אותו לפני #3173: 20GB ב-300 ש' (goodput
#: 66.7MB/s, 89% מהקצב), אפס שידורים חוזרים, אפס ניתוקים, אפס פקיעות; `unsafe_rates` שלו ב-500m.
UDPCAST_STREAM = {"sent_bytes": 20 * GB, "loss_blocks": 0, "stream_bps": 70_000_000, "stream_s": 300,
                  "wall_s": 300, "ended": "send_done", "dropped_clients": 0, "timeouts": 0}
DELIVERED = {MAC: {"drawers": 1, "done": 1, "disk_failed": 0, "stream_failed": 0, "unknown_failed": 0,
                   "written_bytes": 20 * GB}}


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _row(conn, sid: str, decision: dict, stream: dict | None) -> None:
    with db._write_lock, db.writing(conn):
        conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, drops, delivered)"
                     " VALUES (?, ?, ?, ?, ?, ?)",
                     (sid, json.dumps(decision), db.now_iso(), json.dumps(stream) if stream is not None else None,
                      json.dumps({MAC: {"rcvbuf": 0, "failed_drawers": 0}}), json.dumps(DELIVERED)))


def _old_udpcast_wave(conn, sid: str = "ses_udp_old") -> None:
    """שורה של גל udpcast מ-DB משודרג: בלי `engine`, בלי `stream.chunk`, עם המונים של udp-sender."""
    _row(conn, sid, {"max_bitrate": "600m", "roster": ROSTER, "link": LINK}, UDPCAST_STREAM)
    with db._write_lock, db.writing(conn):
        conn.execute("INSERT INTO unsafe_rates (mac, drawers, rate_bps, session_id, at) VALUES (?,?,?,?,?)",
                     (MAC, 1, 500_000_000, sid, db.now_iso()))


def _chunk_wave(conn, sid: str, rate: str) -> None:
    """גל חתיכות קודם של אותה מכונה (`decide` עם `adjust` רושם `engine`), שלא לימד."""
    _row(conn, sid, {"max_bitrate": rate, "roster": ROSTER, "link": LINK, "engine": "chunk"},
         {"chunk": {"asked_bps": drive_rates.parse_bitrate(rate), "teaches": False, "skip": "too_short",
                    "ended": "send_done"}})


def test_a_machine_with_only_an_old_udpcast_wave_starts_like_a_new_one(conn):
    _old_udpcast_wave(conn)
    chosen = drive_rates.choose(conn, DRIVES, None, "ses_now")
    assert (chosen["max_bitrate"], chosen["basis"], chosen["previous"]) == ("600m", "start", None), chosen


def test_the_last_chunk_wave_counts_and_the_newer_udpcast_row_does_not(conn):
    _chunk_wave(conn, "ses_chunk_before", "450m")
    _old_udpcast_wave(conn, "ses_udp_after")          # newer rowid: the old code read it first
    chosen = drive_rates.choose(conn, DRIVES, None, "ses_now")
    assert (chosen["max_bitrate"], chosen["basis"]) == ("450m", "unmeasured"), chosen
    assert (chosen["previous"]["session_id"], chosen["previous"]["mac"]) == ("ses_chunk_before", MAC)


def test_an_old_unsafe_rate_does_not_wall_a_chunk_wave(conn):
    """`unsafe_rates` (udp-sender `Dropping client`) אינה נקראת: 500m שנרשם שם אינו מוריד גל של 2 מגירות מ-450m."""
    _old_udpcast_wave(conn)
    _chunk_wave(conn, "ses_chunk", "450m")
    two = DRIVES + [{"serial": "S3173-2", "model": MODEL, "mac": MAC, "port": 2}]
    chosen = drive_rates.choose(conn, two, None, "ses_now")
    # #1842: 1 → 2 מגירות = ×0.75 מ-450m. בבסיס: 450m (השורה הישנה והקיר של ה-unsafe הישן קבעו).
    assert (chosen["max_bitrate"], chosen["basis"]) == ("337m", "drawers"), chosen


def test_the_old_rows_stay_in_the_tables(conn):
    _old_udpcast_wave(conn)
    before = conn.execute("SELECT decision, stream, drops, delivered FROM wave_rates WHERE session_id = 'ses_udp_old'"
                          ).fetchone()
    drive_rates.decide(conn, "ses_now", lambda: DRIVES, None, adjust=lambda chosen, wave: chosen)
    drive_rates.record_wave(conn, "ses_now", [{"mac": MAC, "targets_json": json.dumps(
        [{"dev": "sda", "state": "done", "serial": SERIAL, "bytes_written": 20 * GB}])}])
    after = conn.execute("SELECT decision, stream, drops, delivered FROM wave_rates WHERE session_id = 'ses_udp_old'"
                         ).fetchone()
    assert tuple(after) == tuple(before)
    assert conn.execute("SELECT COUNT(*) FROM unsafe_rates").fetchone()[0] == 1


def test_the_net_loss_does_not_read_an_old_udpcast_wave(conn):
    _old_udpcast_wave(conn)
    assert netloss.assess(conn, "ses_udp_old") is None
    assert netloss.health_checks(conn) == []


def test_the_learning_round_does_not_count_an_old_udpcast_wave(conn):
    _old_udpcast_wave(conn)
    row = conn.execute("SELECT decision, stream, delivered FROM wave_rates WHERE session_id = 'ses_udp_old'").fetchone()
    assert tuning._evidence(row) == "גל ישן של המנוע הקודם: אינו נספר"


def test_the_forecast_and_the_capability_do_not_read_udp_sender_counters():
    assert eta._stream_clock(dict(UDPCAST_STREAM)) == (None, None)
    assert capability._achieved(json.dumps(UDPCAST_STREAM)) is None


def test_the_udpcast_learning_entry_points_are_gone():
    assert not hasattr(drive_rates, "record_stream")
    assert not hasattr(netloss, "evaluate")
    assert not hasattr(drive_rates, "UDPCAST_BLOCK")


# --- מה שנשאר משותף, על שורות של מנוע החתיכות (מה ש-`test_rate_per_drawers_1842` ו-`test_cloner_cpu_1885` בדקו
# על שורות udpcast) ---------------------------------------------------------------------------------------------


def test_fewer_drawers_scale_down_per_doubling_1842(conn):
    """#1842 על גל חתיכות: 432m של 2 מגירות → 324m ל-4 (×0.75 להכפלה אחת), `previous.drawers` = 2."""
    _row(conn, "ses_two", {"max_bitrate": "432m", "engine": "chunk", "link": LINK,
                           "roster": [{"mac": MAC, "serial": f"S{i}", "model": MODEL} for i in range(2)]}, {})
    four = [{"serial": f"S{i}", "model": MODEL, "mac": MAC, "port": i} for i in range(4)]
    chosen = drive_rates.choose(conn, four, None, "ses_now")
    assert (chosen["max_bitrate"], chosen["basis"], chosen["previous"]["drawers"]) == ("324m", "drawers", 2), chosen


def test_a_throttled_or_server_bound_chunk_wave_is_not_the_previous_one(conn):
    """#1885 ו-#1884 על גל חתיכות: גל שבו המעבד של המכונה הואט, וגל שדיסק הספרייה עיכב, אינם הגל הקודם שלה."""
    _chunk_wave(conn, "ses_good", "450m")
    _chunk_wave(conn, "ses_hot", "700m")
    _chunk_wave(conn, "ses_disk", "800m")
    with db._write_lock, db.writing(conn):
        conn.execute("UPDATE wave_rates SET delivered = ? WHERE session_id = 'ses_hot'",
                     (json.dumps({MAC: {**DELIVERED[MAC], "cpu": {"throttle_delta": 3}}}),))
        conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, basis) VALUES"
                     " ('wave', 'ses_disk', ?, '{}', 'server_disk')", (db.now_iso(),))
    chosen = drive_rates.choose(conn, DRIVES, None, "ses_now")
    assert (chosen["max_bitrate"], chosen["previous"]["session_id"]) == ("450m", "ses_good"), chosen
