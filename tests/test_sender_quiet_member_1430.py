"""#1430 — לא לחכות 10 דקות למכונה שמתה בשקט.

מצב לפני התיקון: חבר שמת **בלי** דיווח `failed` נספר ב-`--min-receivers`
עד ש-`client_lost` נרשם (`room.LOST_SECONDS`, 180ש' או ההגדרה בקונסולה).
מחיצה 2+ שהשולח פתח לפני כן ממתינה לו עד `LATER_MAX_WAIT` (600ש'), כי
השולח כבר רץ עם המספר הקודם.

הכרעת נדב (02/11): חבר שלא שלח hello/progress בדקה האחרונה **אינו נספר**
ב-`--min-receivers` של המחיצה הבאה — השולח יוצא ברגע שכל המדווחים
מחוברים. `--max-wait` נשאר 600 (#957): מגירה איטית ותקינה מדווחת progress
כל ~2 שניות (#449), ולכן נספרת ומחכים לה. חבר שחזר לדווח — נספר שוב.

הכרעת המתאם (02/11), כמו `lost` ב-#1950: חבר שקט שקיבל את המחיצה הקודמת
עד סופה (לוג udp-sender) — המקלט שלו חי — **נספר**. אחרת הוא מתחבר ראשון,
ממלא את הספירה, והמגירה האיטית והתקינה מפספסת את המחיצה.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server.rounds.sender import SenderEngine

from conftest import HANG_S, hello_body, setup_classroom
from test_sender import Recorder, free_ports, library, wait_for  # noqa: F401

#: כתובות החברים A, B, C — לפי הסדר.
IPS = ("10.44.0.11", "10.44.0.12", "10.44.0.13")
MAC3 = "b4:2e:99:07:1a:c6"


def _ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(
        timespec="seconds")


def _writing(server, session: str, mac: str) -> None:
    assert server["anon"].post("/api/v1/agent/progress", json={
        "session_id": session, "mac": mac, "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2,
                     "state": "writing"}],
    }).json()["ok"]


def _arg(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def _p1_log(finished: list[str], dropped: list[str] = ()) -> str:
    """לוג של udp-sender בצורה שנמדדה מול udpcast 20120424 (#1950)."""
    joined = list(finished) + list(dropped)
    lines = ["Udp-sender 20120424", "Using mcast address 234.44.0.1"]
    lines += [f"New connection from {ip}  (#{n}) 00000009" for n, ip in enumerate(joined)]
    lines.append("Starting transfer: 00000009")
    lines += [f"Dropping client #{joined.index(ip)} because of timeout" for ip in dropped]
    lines.append("Transfer complete.")
    return "\n".join(lines) + "\n"


def _second_partition(server, recorder, quiet: dict[int, int], members: int = 2,
                      finished: list[int] | None = None, dropped: list[int] = (),
                      reports_again: list[int] = ()) -> list[str]:
    """`members` חברים כותבים; ל-`quiet[i]` הדיווח האחרון של חבר i לפני כך
    וכך שניות. `finished` — מי קיבל את מחיצה 1 עד סופה לפי הלוג (ברירת
    מחדל: מי שאינו שקט — מכונה שמתה); `dropped` — מי ש-udp-sender זרק;
    `reports_again` — מי ששולח progress אחרי השקט. מחזיר את פקודת מחיצה 2."""
    ids = setup_classroom(server)
    macs = [ids["mac1"], ids["mac2"]]
    if members == 3:
        assert server["admin"].post("/api/console/machines/import", json={
            "group_id": ids["group"], "text": f"{MAC3} 07\n"}).json()["saved"] == 1
        macs.append(MAC3)
    if finished is None:
        finished = [i for i in range(members) if i not in quiet and i not in dropped]
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": members},
    ).json()["id"]
    recorder.block = True
    recorder.log_text = _p1_log([IPS[i] for i in finished], [IPS[i] for i in dropped])
    for mac in macs + macs[:1]:
        server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert recorder.spawned.wait(timeout=HANG_S)
    first = recorder.commands[0]
    assert _arg(first, "--min-receivers") == str(members)
    assert _arg(first, "--max-wait") == "120"

    for mac in macs:
        _writing(server, session, mac)
    conn = server["ctx"].conn
    for mac, ip in zip(macs, IPS):
        conn.execute("UPDATE net_devices SET ip = ? WHERE mac = ?", (ip, mac))
    # הגל רץ כבר חמש דקות — הגרייס של תחילת הגל (`started_at`) מאחורינו.
    conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (_ago(300), session))
    for i, seconds in quiet.items():
        conn.execute("UPDATE session_members SET updated_at = ?"
                     " WHERE session_id = ? AND mac = ?",
                     (_ago(seconds), session, macs[i]))
    conn.commit()
    for i in reports_again:
        _writing(server, session, macs[i])
    recorder.processes[0].release()

    assert wait_for(lambda: len(recorder.commands) >= 2)
    second = recorder.commands[1]
    recorder.processes[1].release()
    return second


def _quiet_events(server) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'send_quiet_wait'")]


def test_a_quiet_member_does_not_hold_the_next_partition(
        server_with_sender, free_ports):  # noqa: F811
    """B לא דיווח 90 שניות (עוד לא `lost`, שקורה ב-180) ולא קיבל את מחיצה 1
    עד סופה: מחיצה 2 אינה סופרת אותו — udp-sender יוצא ברגע ש-A מחובר.
    וההמתנה **אינה** מתקצרת: אין עוד מסלול של 120 למחיצה 2+.

    **בקרה שלילית:** על הקוד שלפני #1430 — `--min-receivers 2`; על
    הגרסה הראשונה של ה-PR — `--min-receivers 2` ו-`--max-wait 120`."""
    server, recorder = server_with_sender
    second = _second_partition(server, recorder, quiet={1: 90})
    assert _arg(second, "--min-receivers") == "1", second
    assert _arg(second, "--max-wait") == "600", second
    assert _arg(second, "--start-timeout") == "780", second
    events = _quiet_events(server)
    assert len(events) == 1 and events[0].endswith(f" {IPS[1]} min-receivers 1"), events


def test_a_quiet_member_that_finished_the_last_partition_is_still_counted(
        server_with_sender, free_ports):  # noqa: F811
    """שלושה חברים: A שקט, אבל ה-udp-receiver שלו קיבל את מחיצה 1 עד סופה —
    הוא חי מבחינת הזרם ויתחבר גם לבאה; B איטי ומדווח; C תקין. אם A לא נספר
    הוא ממלא את הספירה ו-B מפספס את המחיצה. `--min-receivers 3`, 600.

    **בקרה שלילית:** על הגרסה בלי ההצלבה — `--min-receivers 2`."""
    server, recorder = server_with_sender
    second = _second_partition(server, recorder, quiet={0: 90}, members=3,
                               finished=[0, 1, 2])
    assert _arg(second, "--min-receivers") == "3", second
    assert _arg(second, "--max-wait") == "600", second
    assert _quiet_events(server) == []


def test_a_quiet_member_that_did_not_finish_the_last_partition_is_not_counted(
        server_with_sender, free_ports):  # noqa: F811
    """שלושה חברים: A שקט, ו-udp-sender זרק אותו באמצע מחיצה 1 — הוא לא
    יתחבר שוב. B ו-C נספרים: `--min-receivers 2`, 600."""
    server, recorder = server_with_sender
    second = _second_partition(server, recorder, quiet={0: 90}, members=3,
                               finished=[1, 2], dropped=[0])
    assert _arg(second, "--min-receivers") == "2", second
    assert _arg(second, "--max-wait") == "600", second
    events = _quiet_events(server)
    assert len(events) == 1 and events[0].endswith(f" {IPS[0]} min-receivers 2"), events


def test_a_member_that_reports_is_counted_and_waited_for_600s(
        server_with_sender, free_ports):  # noqa: F811
    """#957/#449: מגירה איטית ותקינה מדווחת progress כל ~2 שניות — היא אינה
    שקטה, נספרת, ומחיצה 2 ממתינה לה עד 600 שניות — גם כשהיא עוד לא סיימה
    לקבל את הקודמת לפי הלוג."""
    server, recorder = server_with_sender
    second = _second_partition(server, recorder, quiet={}, finished=[0])
    assert _arg(second, "--min-receivers") == "2", second
    assert _arg(second, "--max-wait") == "600", second
    assert _quiet_events(server) == []


def test_half_a_minute_of_silence_is_not_quiet(server_with_sender, free_ports):  # noqa: F811
    server, recorder = server_with_sender
    second = _second_partition(server, recorder, quiet={1: 30})
    assert _arg(second, "--min-receivers") == "2", second
    assert _arg(second, "--max-wait") == "600", second


def test_a_quiet_member_that_reports_again_is_counted_again(
        server_with_sender, free_ports):  # noqa: F811
    """B שתק 90 שניות ואז שלח progress — הוא נספר שוב במחיצה הבאה."""
    server, recorder = server_with_sender
    second = _second_partition(server, recorder, quiet={1: 90}, reports_again=[1])
    assert _arg(second, "--min-receivers") == "2", second
    assert _arg(second, "--max-wait") == "600", second
    assert _quiet_events(server) == []


def test_the_count_is_taken_again_before_every_partition(library, tmp_path):  # noqa: F811
    """הספירה אינה "נדבקת": חבר שהיה שקט ודיווח שוב נספר במחיצה שאחריה.
    ובמחיצה הראשונה (אין `previous_log`) השאלה אינה נשאלת בכלל."""
    answers = [[IPS[1]], []]
    asked = []

    def quiet(session_id):
        asked.append(session_id)
        return answers.pop(0)

    engine = SenderEngine(library, runner=Recorder(), live_receivers=lambda _s: 2,
                          quiet_receivers=quiet)
    previous = tmp_path / "p1.log"
    previous.write_text(_p1_log([IPS[0]]), encoding="utf-8")
    assert engine._receivers("ses_1", 2, None) == 2
    assert asked == []
    assert engine._receivers("ses_1", 2, previous) == 1
    assert engine._receivers("ses_1", 2, previous) == 2
    assert asked == ["ses_1", "ses_1"]


def test_an_unreadable_log_or_unknown_address_counts_the_quiet_member(library, tmp_path):  # noqa: F811
    """עיקרון 5: "לא הצלחנו לבדוק אם הוא עוד מקבל" נספר — כמו `lost` ב-#1950.
    המחיר הוא המתנה של `--max-wait`, לא מגירה חיה שמפספסת מחיצה."""
    seen = tmp_path / "p1.log"
    seen.write_text(_p1_log([IPS[0]]), encoding="utf-8")
    engine = SenderEngine(library, runner=Recorder(), live_receivers=lambda _s: 2,
                          quiet_receivers=lambda _s: [IPS[1]])
    assert engine._receivers("ses_1", 2, tmp_path / "missing.log") == 2
    assert engine._receivers("ses_1", 2, seen) == 1
    engine.quiet_receivers = lambda _s: [None]
    assert engine._receivers("ses_1", 2, seen) == 2


def test_the_first_partition_is_never_affected(library, free_ports):  # noqa: F811
    """מחיצה 1 סופרת את כולם ו-`--max-wait` שלה 120 כמו תמיד. השאלה נשאלת
    רק ל-2+, ושם `--max-wait` נשאר 600 גם כשיש חבר שקט."""
    recorder = Recorder()
    asked = []
    engine = SenderEngine(library, runner=recorder, live_receivers=lambda _s: 2,
                          quiet_receivers=lambda s: asked.append(s) or [IPS[1]])
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "done")
    first, second = recorder.commands
    assert (_arg(first, "--min-receivers"), _arg(first, "--max-wait")) == ("2", "120")
    assert (_arg(second, "--min-receivers"), _arg(second, "--max-wait")) == ("1", "600")
    assert asked == ["ses_1"]          # פעם אחת — למחיצה 2 בלבד


def test_an_unreadable_quiet_count_counts_everyone(library, free_ports):  # noqa: F811
    """עיקרון 5: "לא הצלחנו לבדוק" אינו "יש חבר מת". הכיוון הבטוח הוא לספור
    את כולם ולהמתין 600 — המחיר הוא זמן, לא מגירה חיה שמפספסת מחיצה."""
    recorder = Recorder()

    def broken(_session_id):
        raise RuntimeError("database is locked")

    engine = SenderEngine(library, runner=recorder, live_receivers=lambda _s: 2,
                          quiet_receivers=broken)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "done")
    second = recorder.commands[1]
    assert _arg(second, "--min-receivers") == "2"
    assert _arg(second, "--max-wait") == "600"
