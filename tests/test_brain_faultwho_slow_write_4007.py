"""#4007 -- B50 (`server/brain/faultwho.py`) אמר ``UNDECIDED`` "אין ראיה שמבדילה" על דיסק שנפסל כאיטי בבדיקת הכתיבה שלפני הזרם
(15 MB/s מול 157 של הדיסק השני באותה מכונה, בלי אירועי קו): ``brain_decisions`` id 17028, משכפל 1, מגירה 3, ברזל 22/11.
הראיה (`targets[].write_preflight`, `state="slow"`) קיימת ב-`targets_json`, והכלל לא קרא אותה.

מה נבדק: הפסק נשאר ``UNDECIDED`` (מדידה אחת אינה היסטוריה, והמוח מציע ולא פוסל), אבל הנימוק אומר מה נמדד ואינו אומר "אין ראיה",
והראיות נושאות ``write=slow/15MBps``. ``ok`` או דיווח חסר אינם משנים את הנוסח; היסטוריה עדיין גוברת.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import faultwho as F, memory, veto

M = "aa:bb:cc:00:00:50"
SLOW, FAST = "naa.50025385503b5985", "naa.other"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "faultwho_4007.db")
    yield c
    veto.forget()


def wave(conn, serial_failed=SLOW):
    for port, serial, failed in ((3, serial_failed, 1), (1, FAST, 0)):
        conn.execute("INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps, link_max_gbps, cable_fail, at, failed)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?)", ("w1", M, port, serial, 0, 3.0, 3.0, 0, f"2026-11-22T19:0{port}:00+00:00", failed))
    conn.commit()


def member(pre):
    t = {"dev": "sdc", "serial": SLOW, "state": "failed"}
    if pre is not None:
        t["write_preflight"] = pre
    return {"mac": M, "targets_json": json.dumps([t])}


def rows(conn):
    return memory.decisions(conn, rule=F.RULE, limit=100)


def test_a_disk_failed_as_slow_in_the_write_check_is_said_and_not_called_no_evidence(conn):
    wave(conn)
    assert F.after_wave(conn, "w1", "room_x", [member({"state": "slow", "mbps": 15})]) == 1
    (got,) = rows(conn)
    assert got["verdict"] == F.UNDECIDED and "write=slow/15MBps" in got["evidence"] and "link_events=null" in got["evidence"]
    assert "15 MB/s" in got["reason"] and "איטי" in got["reason"] and "אין ראיה" not in got["reason"], got["reason"]


@pytest.mark.parametrize("pre", [None, {"state": "ok", "mbps": 157}, {"state": "slow"}, "junk"])
def test_other_or_missing_write_checks_keep_what_they_were(conn, pre):
    wave(conn)
    F.after_wave(conn, "w1", "room_x", [member(pre)])
    (got,) = rows(conn)
    if isinstance(pre, dict) and pre["state"] == "slow":
        assert "איטי" in got["reason"] and "MB/s" not in got["reason"], "no number invented"
    else:
        assert "אין ראיה שמבדילה" in got["reason"] and "איטי" not in got["reason"]


def test_judge_history_still_wins_over_a_slow_write_check():
    assert F.judge(F.Seen(failed=True, port_suspect=True, port_disks=2, write="slow", write_mbps=15))[0] == F.SLOT
    assert F.judge(F.Seen(failed=True, port_suspect=False, disk_suspect=True, disk_ports=2, write="slow"))[0] == F.DISK
