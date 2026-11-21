"""#3769 (המשך #3752): קפיצת שעון קיר בין `first_moved_at` ל-`moved_at` אינה משבשת את הקצב שנלמד לכונן.

השרת רושם ליד כל חותמת קיר גם ערך monotonic (`reports.stamp_movement`), ו-`drive_rates` מודד איתו כששני
הצדדים נכתבו באותו תהליך. דיווח ישן, או אתחול באמצע הגל, נשארים על הקיר (עם ההגנה על ערך שלילי)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server.agent import reports
from server.rounds import drive_rates
from server.system import steadyclock

MB = 1_000_000
T0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)


class Clocks:
    """קיר ו-monotonic מזויפים; `jump` מזיז רק את הקיר."""
    def __init__(self, monkeypatch):
        self.wall, self.mono = T0, 1000.0
        monkeypatch.setattr(reports, "now_iso", lambda: self.wall.isoformat())
        monkeypatch.setattr(steadyclock, "_mono", lambda: self.mono)

    def advance(self, s):
        self.wall += timedelta(seconds=s)
        self.mono += s

    def jump(self, s):
        self.wall += timedelta(seconds=s)


def _run(clocks, jump_s):
    """דיווח ראשון עם בייטים, 1000 שניות אמיתיות (וקפיצה באמצע), דיווח שני שבו המונה עדיין גדל."""
    first = reports.stamp_movement(None, [{"dev": "sda", "bytes_written": 10}])
    clocks.advance(500)
    clocks.jump(jump_s)
    clocks.advance(500)
    return reports.stamp_movement(json.dumps(first), [{"dev": "sda", "bytes_written": 99}])[0]


@pytest.mark.parametrize("jump", [3 * 3600, -3 * 3600, -900])
def test_a_wall_clock_jump_between_the_stamps_does_not_change_the_measured_seconds(monkeypatch, jump):
    t = _run(Clocks(monkeypatch), jump)
    assert drive_rates._moved_seconds(t) == 1000
    wall = drive_rates._seconds(t["first_moved_at"], t["moved_at"])
    assert wall != 1000 or jump == 0          # הקיר לבדו אכן טועה כאן: זה מה שהטסט תופס


def test_the_learned_rate_survives_a_forward_jump(monkeypatch, tmp_path):
    t = {**_run(Clocks(monkeypatch), 3 * 3600), "state": "done", "serial": "S1", "bytes_written": 15_000 * MB}
    assert drive_rates._moved_seconds(t) == 1000
    assert int(t["bytes_written"] / drive_rates._moved_seconds(t)) == 15 * MB


def test_an_old_report_without_monotonic_stays_on_the_wall_clock():
    t = {"first_moved_at": "2026-10-11T10:00:00+00:00", "moved_at": "2026-10-11T10:16:40+00:00"}
    assert drive_rates._moved_seconds(t) == 1000
    back = {"first_moved_at": "2026-10-11T10:16:40+00:00", "moved_at": "2026-10-11T10:00:00+00:00"}
    assert drive_rates._moved_seconds(back) < drive_rates.MIN_SAMPLE_S   # נדחה כמו היום


def test_a_server_restart_between_the_stamps_falls_back_to_the_wall_clock(monkeypatch):
    clocks = Clocks(monkeypatch)
    first = reports.stamp_movement(None, [{"dev": "sda", "bytes_written": 10}])
    clocks.advance(1000)
    monkeypatch.setattr(reports, "BOOT_ID", "another-process")      # השרת עלה מחדש
    clocks.mono = 5.0                                               # ה-monotonic התחיל מחדש
    t = reports.stamp_movement(json.dumps(first), [{"dev": "sda", "bytes_written": 99}])[0]
    assert t["first_moved_boot"] != t["moved_boot"]
    assert drive_rates._moved_seconds(t) == 1000                    # הקיר, לא 5 - 1000


def test_an_agent_sent_mono_field_is_dropped(monkeypatch):
    Clocks(monkeypatch)
    t = reports.stamp_movement(None, [{"dev": "sda", "bytes_written": 10, "moved_mono": 1.0,
                                       "first_moved_mono": 1.0, "moved_boot": "x", "first_moved_boot": "x"}])[0]
    assert t["moved_boot"] == reports.BOOT_ID == t["first_moved_boot"] and t["moved_mono"] == 1000.0
