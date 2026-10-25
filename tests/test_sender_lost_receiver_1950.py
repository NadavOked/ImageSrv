"""‏#1950 — חבר `lost` שעדיין מקבל את הזרם "תופס" את `--min-receivers`.

‏R2 (מעבדה, ‏22/10): ‏VCLONE-2 סומן `client_lost` (הדיווחים שלו נדחו —
‏#1949), אבל ה-udp-receiver שלו המשיך. ‏#1430 מוציא `lost` מהספירה, ולכן
מחיצה 4 יצאה עם `--min-receivers 1`; ‏VCLONE-2 הגיע אליה ראשון, השולח
התחיל, ומשכפל 2 — החבר החי היחיד — הצטרף 4 שניות אחריו וקיבל זרם ריק
(‏`fanout … took 0 bytes`). ‏udpcast אינו מצרף מקלט באמצע מחיצה
(‏`agent/lib/waits.sh`: מי שהצטרף באמצע ממתין לשידור הבא).

התיקון: חבר `lost` שקיבל את המחיצה הקודמת **עד סופה** (התחבר, לא נזרק,
לפי הלוג של udp-sender) נספר שוב. חבר שנזרק (‏`Dropping client`) — לא, כמו
ב-#1430.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import sender as sender_module
from server.sender import SenderEngine

from conftest import hello_body, setup_classroom
from test_sender import Recorder, free_ports, library, wait_for  # noqa: F401

LIVE_IP = "10.44.0.59"
LOST_IP = "10.44.0.161"


def _p1_log(dropped_lost: bool) -> str:
    """לוג של מחיצה 1 בצורה שנמדדה מול udpcast 20120424 (testrunner, ‏22/10)."""
    lines = ["Udp-sender 20120424", "Using mcast address 234.44.0.1",
             f"New connection from {LIVE_IP}  (#0) 00000009",
             f"New connection from {LOST_IP}  (#1) 00000009",
             "Starting transfer: 00000009"]
    if dropped_lost:
        lines.append("Dropping client #1 because of timeout")
    lines += ["Transfer complete.", "Disconnecting #0 (10.44.0.59)"]
    if not dropped_lost:
        lines.append(f"Disconnecting #1 ({LOST_IP})")
    return "\n".join(lines) + "\n"


def _second_partition_receivers(server, recorder, dropped_lost: bool) -> str:
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2},
    ).json()["id"]
    recorder.block = True
    recorder.log_text = _p1_log(dropped_lost)
    for mac in (ids["mac1"], ids["mac2"], ids["mac1"]):
        server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert recorder.spawned.wait(timeout=5)
    first = recorder.commands[0]
    assert first[first.index("--min-receivers") + 1] == "2"

    conn = server["ctx"].conn
    for mac, ip in ((ids["mac1"], LIVE_IP), (ids["mac2"], LOST_IP)):
        conn.execute("UPDATE net_devices SET ip = ? WHERE mac = ?", (ip, mac))
    # מה ש-`room._mark_lost` כותב: החבר לא דיווח מעל LOST_SECONDS.
    conn.execute("UPDATE session_members SET state = 'lost' WHERE session_id = ? AND mac = ?",
                 (session, ids["mac2"]))
    conn.commit()
    recorder.processes[0].release()

    assert wait_for(lambda: len(recorder.commands) >= 2)
    second = recorder.commands[1]
    recorder.processes[1].release()
    return second[second.index("--min-receivers") + 1]


def test_a_lost_member_that_finished_the_last_partition_is_still_waited_for(
        server_with_sender, free_ports):  # noqa: F811
    """הוא יתחבר גם למחיצה הבאה — ובלי לספור אותו השולח יוצא בלי החבר החי."""
    server, recorder = server_with_sender
    assert _second_partition_receivers(server, recorder, dropped_lost=False) == "2"
    events = [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'send_counts_lost'")]
    assert events and LOST_IP in events[0], events


def test_a_lost_member_the_sender_dropped_is_not_waited_for(
        server_with_sender, free_ports):  # noqa: F811
    """שומר #1430: מכונה שנפלה באמצע המחיצה (udp-sender זרק אותה) אינה
    מתחברת שוב — ‏600 שניות של `--max-wait` עליה הן בדיוק מה ש-#1430 סגר."""
    server, recorder = server_with_sender
    assert _second_partition_receivers(server, recorder, dropped_lost=True) == "1"


def test_finished_receivers_reads_the_log_in_order(tmp_path):
    """‏#N ממוחזר אחרי ניתוק; ‏`Disconnecting` אחרי `Transfer complete` הוא
    הסיום הרגיל; ולוג שלא נקרא הוא None — "לא נבדק", לא "אף אחד"."""
    log = tmp_path / "p.log"
    log.write_text(
        "New connection from 10.0.0.1  (#0) 00000009\n"
        "New connection from 10.0.0.2  (#1) 00000009\n"
        "Disconnecting #1 (10.0.0.2)\n"
        "New connection from 10.0.0.3  (#1) 00000009\n"
        "Starting transfer: 00000009\n"
        "bytes= 1 000 000  re-xmits=0000000 (  0.0%) slice=0112 -   0\r"
        "Dropping client #0 because of timeout\n"
        "Transfer complete.\n"
        "Disconnecting #1 (10.0.0.3)\n", encoding="utf-8")
    assert sender_module.finished_receivers(log) == {"10.0.0.3"}
    assert sender_module.finished_receivers(tmp_path / "missing.log") is None


def test_an_unreadable_log_or_unknown_address_counts_the_lost_member(library, tmp_path):  # noqa: F811
    """עיקרון 5: "לא הצלחנו לבדוק אם הוא עוד מקבל" נספר — המחיר הוא
    המתנה של `--max-wait`, ולא מגירה חיה שמקבלת זרם ריק. ושומר: חבר `lost`
    בלי ראיה — לא נספר; ובמחיצה הראשונה (אין קודמת) — כמו #1430."""
    seen = tmp_path / "p1.log"
    seen.write_text(_p1_log(dropped_lost=False), encoding="utf-8")
    engine = SenderEngine(library, runner=Recorder(), live_receivers=lambda _s: 1,
                          lost_receivers=lambda _s: [LOST_IP])
    assert engine._receivers("ses_1", 2, tmp_path / "missing.log") == 2
    assert engine._receivers("ses_1", 2, seen) == 2
    assert engine._receivers("ses_1", 2, None) == 1
    engine.lost_receivers = lambda _s: ["10.44.0.200"]
    assert engine._receivers("ses_1", 2, seen) == 1
    engine.lost_receivers = lambda _s: [None]
    assert engine._receivers("ses_1", 2, seen) == 2
