"""מה שנשאר ממדיניות קצב הגל של 17/10 (#447/#1471, #1571) אחרי מחיקת udpcast (#3173, נדב 21/11).

המדיניות עצמה — עלייה כשהשידורים החוזרים של udp-sender ו-`RcvbufErrors` של המקלטים בתוך 0.1% מהבלוקים, ו-`basis=up(loss=…,rcvbuf=…)`
ביומן — נמחקה עם udp-sender: אין לה מקביל במנוע החתיכות, והקצב הנלמד שלו הוא של `chunk_rate` (`test_chunk_rate_3163`).
הזיכרון לפי מספר המגירות (#1842) נבדק על גלי חתיכות ב-`test_learn_no_udpcast_3173`.

נשאר כאן מה שאינו udpcast: הדיווח של `udp_drops` מהמקלט אל השרת, ו-`wave_rates.drops` בסוף הגל (`netloss` ו-`capability`
קוראים אותו בגלי חתיכות). ⚠️ טסטי הסוכן למטה מריצים את `udp_drops_add` ואת מסלול ה-`multicast` של המגירות — אם הכותב
של הליבה (A) מוחק אותם, הטסטים האלה יוצאים איתו.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.rounds import drive_rates
from server.agent import reports

MAC_A = "aa:bb:cc:00:00:31"           # מחשב שיכפול 2 (i5-650)
MAC_B = "aa:bb:cc:00:00:32"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives(session: str, drawers: dict[str, int]) -> list[dict]:
    """מגירות טריות בכל גל (בחדר מחליפים מגירות) — `drawers`: mac → כמה."""
    return [{"serial": f"{session}-{mac[-2:]}-{k}", "model": "EVO 870", "mac": mac,
             "port": k + 1}
            for mac, count in drawers.items() for k in range(count)]


# --- השרת: מהדיווח אל הזיכרון ------------------------------------------------


def _member(conn, session="ses_r", mac=MAC_A):
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES (?, 'grp_CLONERS', 'img', 'ROOM', 1, 300, 'running', 'nadav', 't',"
        " 0.0, 'multicast')", (session,))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES (?, ?, 'writing', 't')", (session, mac))
    conn.commit()


def _report(conn, **extra):
    return reports.ingest(conn, {"session_id": "ses_r", "mac": MAC_A,
                                 "state": "writing", "targets": [], **extra})


def _stored(conn):
    raw = conn.execute("SELECT udp_drops_json FROM session_members WHERE mac = ?",
                       (MAC_A,)).fetchone()["udp_drops_json"]
    return json.loads(raw) if raw else None


def test_the_report_keeps_the_receivers_drops(conn):
    _member(conn)
    assert _report(conn)["ok"] is True
    assert _stored(conn) is None                         # עוד לא שודרה מחיצה
    _report(conn, udp_drops={"rcvbuf": 0, "inerrors": 3})
    assert _stored(conn) == {"rcvbuf": 0, "inerrors": 3}
    _report(conn)                                        # דיווח בלי השדה — נשאר
    assert _stored(conn) == {"rcvbuf": 0, "inerrors": 3}
    _report(conn, udp_drops={"rcvbuf": None, "inerrors": None})
    assert _stored(conn) == {"rcvbuf": None, "inerrors": None}


@pytest.mark.parametrize("bad", [{"rcvbuf": -1, "inerrors": 0}, {"rcvbuf": "0"},
                                 {"rcvbuf": True}, "0 0", [0, 0]])
def test_a_malformed_value_is_not_read_and_does_not_leave_an_old_zero(conn, bad):
    """דיווח סופי פגום אחרי מחיצה נקייה: האפס הקודם **לא** נשאר — "לא נקרא"."""
    _member(conn)
    _report(conn, udp_drops={"rcvbuf": 0, "inerrors": 0})
    _report(conn, udp_drops=bad)
    assert _stored(conn) == {"rcvbuf": None, "inerrors": None}


def test_the_end_of_the_wave_copies_each_receivers_drops(conn):
    drive_rates.decide(conn, "ses_1", lambda: _drives("ses_1", {MAC_A: 1, MAC_B: 2}),
                       None)
    drive_rates.record_wave(conn, "ses_1", [
        {"mac": MAC_A, "targets_json": "[]",
         "udp_drops_json": json.dumps({"rcvbuf": 4, "inerrors": 0})},
        {"mac": "ff:ff:ff:ff:ff:ff", "targets_json": "[]",       # לא ברשימת הגל
         "udp_drops_json": json.dumps({"rcvbuf": 0, "inerrors": 0})}])
    row = conn.execute("SELECT drops FROM wave_rates WHERE session_id = 'ses_1'").fetchone()
    assert json.loads(row["drops"]) == {
        MAC_A: {"rcvbuf": 4, "inerrors": 0, "failed_drawers": 0}, MAC_B: None}


# #3173: the agent's side -- a sum per partition of the old receiver (udp_drops_add) -- left with it. A chunk
# pass writes udp_drops itself (chunkloss.sh): test_chunk_loss_3064.py, test_chunk_loss_g7_3064.py.
