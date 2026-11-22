"""#3771: קפיצת שעון קדימה בתוך חלון האישור של שינוי רשת אינה מפקיעה אותו.

הסמן נכתב בקונסולה ונקרא בתהליך ההחזרה הנפרד (בלי DB), ולכן העוגן המשותף הוא `CLOCK_MONOTONIC` של
האתחול, ששניהם רואים. הסמן נושא `deadline_mono` רק לצד `boot`, ובאתחול אחר חוזרים לשעון הקיר.

הזרוע רצה כאן כמו ב-systemd: `run_once` בלי `now` ובלי `mono`, והשעונים שלה (`time.time`,
`time.monotonic`) מוחלפים בשני שעונים מדומים. הקונסולה מקבלת את אותם שני שעונים דרך ה-hooks.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server.net import netcfg_rollback

from test_netcfg_rollback import marker  # noqa: F401 -- ה-helper של הקובץ הסמוך
from test_server_netcfg import CONF, confirm, net_server, put  # noqa: F401 -- הפיקסטורה בשמה

HOURS3 = 3 * 3600


@pytest.fixture()
def arm_clocks(net_server, monkeypatch):
    """הזרוע רואה את אותם שני שעונים כמו הקונסולה: קיר (`clock`) ו-monotonic (קיר פחות הקפיצות)."""
    clock, skew = net_server["clock"], net_server["skew"]
    monkeypatch.setattr(netcfg_rollback, "time", SimpleNamespace(
        time=lambda: clock(), monotonic=lambda: clock() - skew["s"]))
    return net_server


def jump_forward(server, seconds: float) -> None:
    """שעון הקיר קופץ; ה-monotonic לא."""
    server["clock"].advance(seconds)
    server["skew"]["s"] += seconds


def tick(server, seconds: float) -> None:
    """זמן אמיתי שעובר: שניהם זזים."""
    server["clock"].advance(seconds)


def arm(server) -> None:
    # eth0 מחמש את ההחזרה (ראו test_netcfg_rollback)
    assert put(server, name="eth0", address="10.10.10.9").status_code == 200


def arm_runs(server) -> str | None:
    return netcfg_rollback.run_once(server["state_dir"], server["hooks"], boot="boot-A")


def test_a_forward_clock_jump_does_not_close_the_window_before_the_operator_confirms(arm_clocks):
    arm(arm_clocks)
    jump_forward(arm_clocks, HOURS3)
    tick(arm_clocks, 10)
    assert arm_runs(arm_clocks) is None
    response = confirm(arm_clocks, "eth0")
    assert response.status_code == 200, response.text
    assert netcfg_rollback.read_pending(arm_clocks["state_dir"]) is None


def test_the_window_still_closes_after_sixty_real_seconds_whatever_the_wall_clock_says(arm_clocks):
    arm(arm_clocks)
    jump_forward(arm_clocks, HOURS3)
    tick(arm_clocks, 61)
    assert arm_runs(arm_clocks) == "expired"
    assert "10.10.10.9" not in (arm_clocks["fake"]["confs"].get("eth0") or "")


def test_the_view_counts_seconds_left_on_the_monotonic_clock(arm_clocks):
    arm(arm_clocks)
    jump_forward(arm_clocks, HOURS3)
    tick(arm_clocks, 10)
    view = arm_clocks["admin"].get(CONF).json()["rollback"]
    assert view["pending"] is True and view["expired"] is False
    assert 49 <= view["seconds_left"] <= 50


def test_a_backward_jump_is_not_a_clock_rollback_when_the_monotonic_clock_vouches(arm_clocks):
    arm(arm_clocks)
    jump_forward(arm_clocks, -HOURS3)
    tick(arm_clocks, 10)
    assert arm_runs(arm_clocks) is None


# --- הגבול: מתי ה-monotonic אינו עוגן ---------------------------------------


def write_marker(tmp_path, **over) -> None:
    data = {"schema": 1, **marker().__dict__, **over}
    (tmp_path / "pending.json").write_text(json.dumps(data), encoding="utf-8")


def run_at(tmp_path, monkeypatch, wall: float, mono: float, boot: str) -> str | None:
    monkeypatch.setattr(netcfg_rollback, "time", SimpleNamespace(time=lambda: wall, monotonic=lambda: mono))
    hooks = {"netcfg_write_conf": lambda *a: None, "netcfg_write_resolv": lambda *a: None,
             "netcfg_apply": lambda name: None, "netcfg_down": lambda name: None,
             "netcfg_main_defines": lambda name: True}
    return netcfg_rollback.run_once(tmp_path, hooks, boot=boot)


def test_the_monotonic_deadline_counts_only_on_the_boot_that_wrote_it(tmp_path, monkeypatch):
    """ה-monotonic מתאפס באתחול. בלי `boot` ידוע ב-/proc הקיר קובע, עם הגדר של #3752."""
    write_marker(tmp_path, deadline=1_000_060.0, deadline_mono=5000.0)
    # אותו אתחול: ה-monotonic אומר שנשארו 4 שניות, ושעון הקיר קפץ 3 שעות -- לא מחזירים.
    assert run_at(tmp_path, monkeypatch, 1_000_000.0 + HOURS3, 4996.0, "boot-A") is None
    assert (tmp_path / "pending.json").exists()
    # בלי boot ידוע: ה-monotonic אינו עוגן, והקיר שקפץ פוקע (הכיוון הבטוח).
    assert run_at(tmp_path, monkeypatch, 1_000_000.0 + HOURS3, 4996.0, "") == "expired"


def test_a_marker_without_a_monotonic_deadline_keeps_the_wall_clock_rule(tmp_path, monkeypatch):
    write_marker(tmp_path)
    assert run_at(tmp_path, monkeypatch, 1_000_000.0 + 59, 1.0, "boot-A") is None
    assert run_at(tmp_path, monkeypatch, 1_000_000.0 + 61, 1.0, "boot-A") == "expired"
