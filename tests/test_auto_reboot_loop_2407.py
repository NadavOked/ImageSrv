"""#2407: הפעלה מחדש מתוזמנת אינה מבוצעת פעמיים על אותו מופע.

נמדד על ה-TA ב-04/11 (v0.81.3, daily 10:21): השרת עלה תוך ~20 שניות, ובעלייה
המתזמן חישב את ``due`` **כולל הדקה הנוכחית** — ולכן הפעיל מחדש שוב, כשש פעמים,
עד שהדקה עברה. הסימן ``scheduled-reboot`` כבר נשא את זמן הביצוע, אבל
``consume_marker`` מחק אותו בלי למסור את הזמן למתזמן.

מה נבדק: עלייה עם סימן מאותה דקה אינה מפעילה מחדש ו-``due`` הוא המופע הבא;
עלייה בלי סימן בתוך הדקה כן מפעילה (ההתנהגות הקיימת); סימן פגום נחשב
"כבר בוצע" ונרשם (עיקרון 5); סימן ישן אינו מפעיל מופע שכבר עבר; והחיווט
מ-``create_runtime`` אל ה-runtime שהמתזמן קורא.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server import auto_reboot
from server.db import set_setting

SCHEDULED = datetime(2026, 11, 4, 10, 21)
TOMORROW = datetime(2026, 11, 5, 10, 21)


def journal_details(server):
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'auto_reboot' ORDER BY id")]


@pytest.fixture()
def daily(server, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    set_setting(server["ctx"].conn, "auto_reboot_time", "10:21")
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: None)
    return server


def make(server, tmp_path, clock, calls, last_done=None):
    """המתזמן כפי שהוא עולה: הזמן מהסימן מגיע דרך ה-runtime (``auto_reboot_last``)."""
    rt = SimpleNamespace(conn=server["ctx"].conn, data_dir=tmp_path, vmupdate=None,
                         auto_reboot_last=last_done)
    return auto_reboot.Scheduler(rt, lambda: calls.append(clock[0]), lambda: clock[0])


def test_fast_boot_in_the_scheduled_minute_does_not_reboot_again(daily, tmp_path):
    clock, calls = [SCHEDULED], []
    assert make(daily, tmp_path, clock, calls).tick() == "done"
    # העלייה: ~20 שניות אחרי, ואותם מרווחי ~11 שניות שנמדדו ב-TA.
    clock[0] = SCHEDULED.replace(second=20)
    last = auto_reboot.consume_marker(daily["ctx"].conn, tmp_path)
    assert last == SCHEDULED
    booted = make(daily, tmp_path, clock, calls, last_done=last)
    for second in (21, 32, 43, 55):
        clock[0] = SCHEDULED.replace(second=second)
        assert booted.tick() is None
    assert calls == [SCHEDULED]
    assert booted.due == TOMORROW
    assert not (tmp_path / auto_reboot.MARKER).exists()


def test_scheduler_skips_the_occurrence_this_boot_came_from(daily, tmp_path):
    # המתזמן לבדו, בלי consume_marker: זו הלולאה עצמה מה-TA.
    clock, calls = [SCHEDULED.replace(second=20)], []
    job = make(daily, tmp_path, clock, calls, last_done=SCHEDULED)
    assert job.tick() is None
    assert calls == [] and job.due == TOMORROW


def test_boot_without_marker_inside_the_scheduled_minute_still_reboots(daily, tmp_path):
    clock, calls = [SCHEDULED.replace(second=20)], []
    assert not auto_reboot.consume_marker(daily["ctx"].conn, tmp_path)
    job = make(daily, tmp_path, clock, calls, last_done=None)
    assert job.tick() == "done"
    assert calls == [clock[0]]


@pytest.mark.parametrize("content", [
    b"scheduled", b"", b"2026-11-04T10:21:00+00:00", b"\xff\xfe10:21",
])
def test_unreadable_marker_counts_as_already_done_and_is_journalled(daily, tmp_path, content):
    # שעון אמיתי: סימן שלא נקרא נחשב "בוצע עכשיו", ו"עכשיו" הוא השעון של השרת.
    minute = datetime.now().replace(second=0, microsecond=0)
    set_setting(daily["ctx"].conn, "auto_reboot_time", minute.strftime("%H:%M"))
    (tmp_path / auto_reboot.MARKER).write_bytes(content)
    last = auto_reboot.consume_marker(daily["ctx"].conn, tmp_path)
    assert isinstance(last, datetime) and minute <= last <= datetime.now()
    assert not (tmp_path / auto_reboot.MARKER).exists()
    line = journal_details(daily)[-1]
    # "השרת אותחל" בראש — ההתראה במייל (email_alerts) נשארת; והספק נאמר בגלוי.
    assert line.startswith("השרת אותחל לפי לוח הזמנים") and "לא נקרא" in line
    assert "נחשב כבוצע" in line
    clock, calls = [minute], []
    job = make(daily, tmp_path, clock, calls, last_done=last)
    assert job.tick() is None
    assert calls == [] and job.due == minute + timedelta(days=1)


def test_old_marker_does_not_fire_an_occurrence_that_already_passed(daily, tmp_path):
    # הסימן מלפני יומיים (השרת היה כבוי מאז), והעלייה אחרי השעה של היום.
    clock, calls = [datetime(2026, 11, 4, 15, 0, 20)], []
    job = make(daily, tmp_path, clock, calls, last_done=datetime(2026, 11, 2, 10, 21))
    assert job.tick() is None
    assert calls == [] and job.due == TOMORROW


def test_runtime_carries_the_marker_time_for_the_scheduler(tmp_path, images_root):
    from server.app import create_runtime                  # noqa: PLC0415
    from test_sender import Recorder                       # noqa: PLC0415
    data = tmp_path / "data"
    data.mkdir()
    (data / auto_reboot.MARKER).write_text(SCHEDULED.isoformat(), encoding="ascii")
    rt = create_runtime(data, images_root, "http://10.44.12.10:8080",
                        sender_runner=Recorder(block=True))
    try:
        assert getattr(rt, "auto_reboot_last", None) == SCHEDULED
        assert not (data / auto_reboot.MARKER).exists()
    finally:
        rt.ctx.sender.stop()
