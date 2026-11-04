from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server import auto_reboot
from server.db import set_setting


def journal_details(server, event="auto_reboot"):
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


@pytest.mark.parametrize("body", [
    {"auto_reboot": "sometimes"}, {"auto_reboot_time": "3:00"},
    {"auto_reboot_time": "24:00"}, {"auto_reboot_weekday": -1},
    {"auto_reboot_weekday": 7}, {"auto_reboot_weekday": True},
])
def test_invalid_schedule_is_rejected_before_any_setting_is_written(server, body):
    body["session_wait_seconds"] = "77"
    response = server["admin"].post("/api/console/settings", json=body)
    assert response.status_code == 400
    assert server["admin"].get("/api/console/settings").json()["session_wait_seconds"] != "77"
    assert journal_details(server, "setting_change") == []


def test_unknown_setting_rejects_the_whole_request(server):
    before = server["admin"].get("/api/console/settings").json()["auto_reboot"]
    response = server["admin"].post("/api/console/settings", json={
        "auto_reboot": "daily", "typo": 1})
    assert response.status_code == 400
    assert server["admin"].get("/api/console/settings").json()["auto_reboot"] == before
    assert journal_details(server, "setting_change") == []


def test_settings_body_must_be_a_json_object(server):
    response = server["admin"].post("/api/console/settings", json=[])
    assert response.status_code == 400


def test_schedule_is_admin_only_and_each_change_is_journalled(server):
    body = {"auto_reboot": "weekly", "auto_reboot_time": "04:25",
            "auto_reboot_weekday": 4}
    assert server["deploy"].post("/api/console/settings", json=body).status_code == 403
    assert server["admin"].post("/api/console/settings", json=body).status_code == 200
    values = server["admin"].get("/api/console/settings").json()
    assert {key: values[key] for key in body} == {**body, "auto_reboot_weekday": "4"}
    details = journal_details(server, "setting_change")
    assert all(f"{key}={values[key]}" in details for key in body)


def test_defaults_are_off_three_oclock_and_monday(server):
    values = server["admin"].get("/api/console/settings").json()
    assert (values["auto_reboot"], values["auto_reboot_time"],
            values["auto_reboot_weekday"]) == ("off", "03:00", "0")


def test_next_daily_occurrence_uses_naive_server_local_time():
    now = datetime(2026, 10, 14, 2, 59, 45)
    assert auto_reboot.next_occurrence("daily", "03:00", 0, now) == datetime(2026, 10, 14, 3)
    assert auto_reboot.next_occurrence("daily", "03:00", 0, now.replace(hour=3)) == datetime(2026, 10, 15, 3)


def test_next_weekly_occurrence_uses_python_weekday_zero_to_six():
    # 2026-10-14 is Wednesday (2); Friday is 4.
    assert auto_reboot.next_occurrence("weekly", "01:30", 4,
                                       datetime(2026, 10, 14, 12)) == datetime(2026, 10, 16, 1, 30)


def scheduler(server, tmp_path, clock, hook):
    rt = SimpleNamespace(conn=server["ctx"].conn, data_dir=tmp_path, vmupdate=None)
    return auto_reboot.Scheduler(rt, hook, lambda: clock[0])


def test_busy_defers_every_ten_minutes_then_gives_up_after_two_hours(server, tmp_path, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    set_setting(server["ctx"].conn, "auto_reboot_time", "03:00")
    clock = [datetime(2026, 10, 14, 3)]
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: "קליטת אימג' רצה")
    calls = []
    job = scheduler(server, tmp_path, clock, lambda: calls.append(1))
    assert job.tick() == "deferred"
    for minutes in range(10, 120, 10):
        clock[0] = datetime(2026, 10, 14, 3) + timedelta(minutes=minutes)
        assert job.tick() == "deferred"
    clock[0] += timedelta(minutes=10)
    assert job.tick() == "skipped"
    assert calls == []
    assert len([d for d in journal_details(server) if d.startswith("נדחה")]) == 12
    assert journal_details(server)[-1].startswith("דולג")


def test_idle_schedule_calls_injected_hook_exactly_once_and_writes_marker(server, tmp_path, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    set_setting(server["ctx"].conn, "auto_reboot_time", "03:00")
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: None)
    clock = [datetime(2026, 10, 14, 3)]
    calls = []
    job = scheduler(server, tmp_path, clock, lambda: calls.append("reboot"))
    assert job.tick() == "done"
    assert job.tick() is None
    assert calls == ["reboot"]
    assert (tmp_path / auto_reboot.MARKER).exists()


def test_failed_reboot_has_no_marker(server, tmp_path, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    set_setting(server["ctx"].conn, "auto_reboot_time", "03:00")
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: None)
    job = scheduler(server, tmp_path, [datetime(2026, 10, 14, 3)],
                    lambda: (_ for _ in ()).throw(RuntimeError("reboot failed")))
    assert job.tick() == "failed"
    assert not (tmp_path / auto_reboot.MARKER).exists()
    assert journal_details(server) == ["נכשל — RuntimeError: reboot failed"]


def test_failed_reboot_waits_until_retry_time(server, tmp_path, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: None)
    clock = [datetime(2026, 10, 14, 3)]
    calls = []

    def reboot():
        calls.append(clock[0])
        raise subprocess.CalledProcessError(1, ["systemctl", "reboot"])

    job = scheduler(server, tmp_path, clock, reboot)
    assert job.tick() == "failed"
    for _ in range(1, auto_reboot.RETRY_MINUTES):
        clock[0] += timedelta(minutes=1)
        assert job.tick() is None
    assert len(calls) == 1
    clock[0] += timedelta(minutes=1)
    assert job.tick() == "failed"
    assert calls[1] - calls[0] == timedelta(minutes=auto_reboot.RETRY_MINUTES)
    assert not (tmp_path / auto_reboot.MARKER).exists()


def test_failed_reboot_gives_up_within_shared_window(server, tmp_path, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: None)
    start = datetime(2026, 10, 14, 3)
    clock = [start]
    calls = []

    def reboot():
        calls.append(clock[0])
        raise RuntimeError("reboot failed")

    job = scheduler(server, tmp_path, clock, reboot)
    results = []
    for minute in range(auto_reboot.GIVE_UP_MINUTES + auto_reboot.RETRY_MINUTES + 1):
        clock[0] = start + timedelta(minutes=minute)
        results.append(job.tick())
    assert results[0] == "failed"
    assert results[auto_reboot.GIVE_UP_MINUTES] == "skipped"
    assert results.count("skipped") == 1
    assert 1 < len(calls) <= auto_reboot.GIVE_UP_MINUTES // auto_reboot.RETRY_MINUTES + 1
    assert all(at - start <= timedelta(minutes=auto_reboot.GIVE_UP_MINUTES) for at in calls)
    assert all(b - a >= timedelta(minutes=auto_reboot.RETRY_MINUTES)
               for a, b in zip(calls, calls[1:]))
    assert job.due == start + timedelta(days=1)
    assert job.first_deferred is None and job.retry_at is None
    lines = journal_details(server)
    assert lines[-1].startswith("דולג") and "כשל" in lines[-1]
    assert not any(line.startswith("בוצע") for line in lines)
    assert not (tmp_path / auto_reboot.MARKER).exists()


def test_failed_reboot_can_succeed_within_window(server, tmp_path, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: None)
    clock = [datetime(2026, 10, 14, 3)]
    calls = []

    def reboot():
        calls.append(clock[0])
        if len(calls) == 1:
            raise RuntimeError("reboot failed")

    job = scheduler(server, tmp_path, clock, reboot)
    assert job.tick() == "failed"
    clock[0] += timedelta(minutes=auto_reboot.RETRY_MINUTES)
    assert job.tick() == "done"
    assert len(calls) == 2
    assert (tmp_path / auto_reboot.MARKER).read_text(encoding="ascii") == clock[0].isoformat()
    assert journal_details(server)[-1].startswith("בוצע")
    assert job.due == datetime(2026, 10, 15, 3)
    assert job.first_deferred is None and job.retry_at is None
    assert job.tick() is None
    assert len(calls) == 2


def test_failed_busy_check_defers_instead_of_rebooting(server, tmp_path, monkeypatch):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    set_setting(server["ctx"].conn, "auto_reboot_time", "03:00")
    monkeypatch.setattr(auto_reboot, "busy_reason",
                        lambda rt: (_ for _ in ()).throw(RuntimeError("locked")))
    calls = []
    job = scheduler(server, tmp_path, [datetime(2026, 10, 14, 3)],
                    lambda: calls.append(1))
    assert job.tick() == "deferred"
    assert calls == []
    assert journal_details(server)[-1] == "נדחה — לא ניתן לבדוק אם השרת עסוק"


def test_run_journals_an_exception_and_keeps_scheduling(server, tmp_path, monkeypatch):
    rt = SimpleNamespace(conn=server["ctx"].conn, data_dir=tmp_path, vmupdate=None)
    calls = []

    def tick(_self):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        raise asyncio.CancelledError

    monkeypatch.setattr(auto_reboot.Scheduler, "tick", tick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(auto_reboot.run(rt, interval=0))
    assert len(calls) == 2
    assert journal_details(server)[-1] == "תקלה: RuntimeError: database is locked"


def test_marker_is_consumed_and_post_boot_event_is_journalled(server, tmp_path):
    marker = tmp_path / auto_reboot.MARKER
    # ‏#2407: הסימן נושא את זמן הביצוע, ו-consume_marker מחזיר אותו (סימן פגום —
    # test_auto_reboot_loop_2407).
    marker.write_text("2026-10-14T03:00:00", encoding="ascii")
    assert auto_reboot.consume_marker(server["ctx"].conn, tmp_path) == datetime(2026, 10, 14, 3)
    assert not marker.exists()
    assert journal_details(server)[-1] == "השרת אותחל לפי לוח הזמנים"


def test_health_reports_off_or_the_next_occurrence(server):
    rows = server["admin"].get("/api/console/health").json()
    row = next(r for r in rows if r["id"] == "auto_reboot")
    assert row["label"] == "הפעלה מחדש הבאה" and row["detail"] == "כבוי"


def test_health_reports_a_stopped_scheduler(server):
    server["ctx"].auto_reboot_task = SimpleNamespace(
        done=lambda: True, cancelled=lambda: False)
    rows = server["admin"].get("/api/console/health").json()
    row = next(r for r in rows if r["id"] == "auto_reboot")
    assert row["detail"] == "המתזמן אינו רץ" and row["state"] == "bad"
