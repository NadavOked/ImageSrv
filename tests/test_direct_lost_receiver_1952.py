"""‏#1952 — הפער של #1950 בהפצה ישירה ממחשב בנייה.

‏`task_block` קובע `min_receivers = live_count`, ו-`live_count` (‏#1430/#1432)
מוציא חברים `lost`. ‏`lost` הוא הסקה של השרת ("לא דיווח"), לא דיווח של
המכונה: חבר שה-udp-receiver שלו עוד חי מגיע ראשון למחיצה הבאה, ממלא את
‏`--min-receivers`, ו-udp-sender **במחשב הבנייה** יוצא בלי החבר החי —
שמצטרף באמצע ומקבל זרם ריק (R2 של #1950, במסלול הספרייה: 0/2).

בשרת אין כאן את הלוג של udp-sender — הוא רץ במחשב הבנייה. לכן מחשב הבנייה
מדווח ב-hello שלפני כל מחיצה (מהשנייה) את הכתובות שקיבלו את הקודמת עד
סופה (`direct_finished`), והשרת סופר לפיהן — אותו כלל כמו #1950.
"""

from __future__ import annotations

import pytest

from conftest import hello_body
from test_direct_live_receivers import CLONER3, CLONERS, _running_three
from test_direct_server import BUILD
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401

pytest.importorskip("fastapi")

IPS = {CLONER1: "10.44.0.59", CLONER2: "10.44.0.60", CLONER3: "10.44.0.161"}
LOST = CLONER3


def _lose(room_server, opened, lost_ip: str | None = IPS[LOST]):
    """מה ש-`room._mark_lost` כותב: החבר לא דיווח מעל LOST_SECONDS."""
    conn = room_server["ctx"].conn
    for mac in CLONERS:
        conn.execute("UPDATE net_devices SET ip = ? WHERE mac = ?",
                     (lost_ip if mac == LOST else IPS[mac], mac))
    conn.execute("UPDATE session_members SET state = 'lost' WHERE session_id = ? AND mac = ?",
                 (opened["wave_session_id"], LOST))
    conn.commit()


def _source_hello(room_server, **extra) -> dict:
    """ה-hello של מחשב הבנייה לפני מחיצה — `build_hello` עם (או בלי) השדה."""
    body = hello_body(BUILD)
    body["disks"] = [{"dev": "sda", "size_bytes": 256060514304, "model": "Build SSD",
                      "serial": "B-sda", "removable": False, "scheme": "gpt", "has_data": True}]
    body.update(extra)
    r = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()["task"]["direct"]


def _counted_lost(room_server) -> list[str]:
    return [row["detail"] for row in room_server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'send_counts_lost' ORDER BY id")]


def test_a_lost_member_that_finished_the_last_partition_is_still_waited_for(room_server):
    """הוא יתחבר גם למחיצה הבאה — ובלי לספור אותו השולח יוצא בלי החבר החי."""
    opened = _running_three(room_server)
    _lose(room_server, opened)
    direct = _source_hello(room_server, direct_finished=sorted(IPS.values()))
    assert direct["multicast"]["min_receivers"] == 3
    assert direct["receivers"] == 2           # החיים — מה שמסך ההמתנה מציג
    assert _counted_lost(room_server) == [f'{opened["wave_session_id"]} {IPS[LOST]}']


def test_a_lost_member_the_sender_dropped_is_not_waited_for(room_server):
    """#1432 נשאר: מי שלא קיבל את הקודמת עד סופה — לא נספר."""
    opened = _running_three(room_server)
    _lose(room_server, opened)
    direct = _source_hello(room_server, direct_finished=[IPS[CLONER1], IPS[CLONER2]])
    assert direct["multicast"]["min_receivers"] == 2
    assert _counted_lost(room_server) == []


@pytest.mark.parametrize("finished", [None, "10.44.0.161", ["not-an-ip"], [7]])
def test_a_log_the_source_could_not_read_counts_the_lost_member(room_server, finished):
    """"לא נבדק" אינו "אף אחד" (עיקרון 5): המחיר הוא המתנה של `--max-wait`,
    לא מגירה חיה שמקבלת זרם ריק."""
    opened = _running_three(room_server)
    _lose(room_server, opened)
    assert _source_hello(room_server, direct_finished=finished)["multicast"]["min_receivers"] == 3


def test_a_lost_member_whose_address_is_unknown_is_counted(room_server):
    opened = _running_three(room_server)
    _lose(room_server, opened, lost_ip=None)
    direct = _source_hello(room_server, direct_finished=[IPS[CLONER1], IPS[CLONER2]])
    assert direct["multicast"]["min_receivers"] == 3
    assert _counted_lost(room_server) == [f'{opened["wave_session_id"]} ?']


def test_without_the_field_the_count_is_the_live_members(room_server):
    """המחיצה הראשונה (אין "קודמת"), או סוכן ישן: ההתנהגות של #1432."""
    opened = _running_three(room_server)
    _lose(room_server, opened)
    assert _source_hello(room_server)["multicast"]["min_receivers"] == 2
    assert _counted_lost(room_server) == []


def test_without_a_lost_member_the_report_changes_nothing(room_server):
    _running_three(room_server)
    direct = _source_hello(room_server, direct_finished=sorted(IPS.values()))
    assert direct["multicast"]["min_receivers"] == 3
    assert _counted_lost(room_server) == []
