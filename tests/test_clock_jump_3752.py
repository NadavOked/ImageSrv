"""#3752 (המשך #1947): קפיצת שעון קיר באתרים שמודדים משך, מחוץ לנתיב הסבב.

כל הבדיקות קופצות שעון **קדימה ואחורה ב-3 שעות** על שעון מוזרק, ובודקות את
מה שהמפעיל רואה (נעילה, עוגייה, חלון אישור), לא קוד פנימי.

* `steadyclock` — "עכשיו" שאינו קופץ; סחיפה קטנה נבלעת.
* `login_guard` — נעילת כניסה: קפיצה באמצע הנעילה (שעון monotonic), וקפיצה אחורה בין אתחולים.
* `auth` / `mfa` / pairing — תפוגה רחוקה מהמשך האפשרי = שעון חזר אחורה = נדחה.
* `netcfg_rollback` / `nic_services` — חלון אישור: אחורה מחזיר עכשיו, ולא אחרי שעות.
* `vmupdate` — ברירת המחדל של השעון; `auto_reboot` — עדכון חי אחרי קפיצה קדימה אינו "תקוע".
"""

from __future__ import annotations

import inspect
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from server.console import auth, login_guard, mfa
from server.db import connect
from server.interserver import auth as interserver_auth
from server.net import netcfg_rollback
from server.system import steadyclock

from test_nic_services import handoff, rig  # noqa: F401  (fixture + helper)

H3 = 3 * 3600


class FakeClocks:
    """קיר ו-monotonic מוזרקים ל-`steadyclock`. `jump` זז רק בקיר; `advance` בשניהם."""

    def __init__(self, monkeypatch):
        self.wall = 1_800_000_000.0
        self.mono = 5_000.0
        monkeypatch.setattr(steadyclock, "_wall", lambda: self.wall)
        monkeypatch.setattr(steadyclock, "_mono", lambda: self.mono)
        monkeypatch.setattr(steadyclock, "_anchor", (self.wall, self.mono))

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds

    def jump(self, seconds: float) -> None:
        self.wall += seconds

    def restart(self) -> None:
        """תהליך חדש: העוגן נלקח מחדש משעון הקיר הנוכחי."""
        steadyclock._anchor = (self.wall, self.mono)


@pytest.fixture()
def clocks(monkeypatch):
    return FakeClocks(monkeypatch)


@pytest.fixture()
def conn(tmp_path):
    from server.console import users

    db = connect(tmp_path / "imagesrv.db")
    users.create(db, "noc", "Aa12345!pw", "admin", by="test", check_policy=False)
    return db


# ---------- steadyclock ----------


@pytest.mark.parametrize("jump", [H3, -H3])
def test_now_does_not_follow_a_wall_jump(clocks, jump):
    start = steadyclock.now()
    clocks.advance(10)
    clocks.jump(jump)
    assert steadyclock.now() == pytest.approx(start + 10)
    assert steadyclock.skew() == pytest.approx(jump)


def test_a_small_drift_is_followed_not_ignored(clocks):
    steadyclock.now()
    clocks.advance(100)
    clocks.jump(20)                                     # NTP slew: מתחת ל-JUMP_SECONDS
    assert steadyclock.now() == pytest.approx(clocks.wall)
    assert steadyclock.skew() == pytest.approx(0)


# ---------- login_guard ----------


def _lock_out(conn, user="noc", ip="10.1.1.1"):
    for _ in range(login_guard.LOCK_AFTER):
        login_guard.record_failure(conn, user, ip)


def _retry_after(conn, user="noc", ip="10.1.1.1"):
    try:
        login_guard.check(conn, user, ip)
    except login_guard.LoginBlocked as exc:
        return exc.retry_after
    return None


@pytest.mark.parametrize("jump", [H3, -H3])
def test_a_lockout_lasts_fifteen_minutes_through_a_wall_jump(clocks, conn, jump):
    _lock_out(conn)
    clocks.advance(60)
    clocks.jump(jump)
    left = _retry_after(conn)
    assert left is not None, "the jump must not unlock the account"
    assert 14 * 60 <= left <= 15 * 60, f"the jump must not stretch the lock: {left}s"
    clocks.advance(login_guard.LOCK_SECONDS)            # הזמן שעבר באמת
    assert _retry_after(conn) is None


def test_a_lockout_written_before_a_backward_jump_is_cut_to_fifteen_minutes(clocks, conn):
    """אתחול שרת אחרי שהשעון חזר 3 שעות: הנעילה השמורה נשארת בעתיד, ונחתכת."""
    _lock_out(conn)
    clocks.jump(-H3)
    clocks.restart()
    left = _retry_after(conn)
    assert left is not None and left <= login_guard.LOCK_SECONDS
    clocks.advance(login_guard.LOCK_SECONDS + 1)
    assert _retry_after(conn) is None


def test_a_delay_written_before_a_backward_jump_is_not_hours(clocks, conn):
    for _ in range(7):                                  # 7 כשלים: השהיה של 4 שניות
        login_guard.record_failure(conn, "noc", "10.1.1.1")
    clocks.jump(-H3)
    clocks.restart()
    left = _retry_after(conn)
    assert left is not None and left <= 4
    clocks.advance(5)
    assert _retry_after(conn) is None


def test_the_account_cap_written_before_a_backward_jump_is_cut_too(clocks, conn):
    for n in range(login_guard.ACCOUNT_LIMIT):
        login_guard.record_failure(conn, "noc", f"10.2.{n // 250}.{n % 250 + 1}")
    clocks.jump(-H3)
    clocks.restart()
    left = _retry_after(conn, ip="10.3.3.3")
    assert left is not None and left <= login_guard.ACCOUNT_LOCK_SECONDS
    clocks.advance(login_guard.ACCOUNT_LOCK_SECONDS + 1)
    assert _retry_after(conn, ip="10.3.3.3") is None


# ---------- עוגיית הקונסולה ----------


def _cookie_server(conn, monkeypatch):
    t = {"s": 0.0}
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: time.time() + t["s"]))
    return t


def _read(conn, token):
    return auth.read_session(conn, token, idle_exempt=True, no_touch=True)


def test_a_cookie_is_read_within_its_ttl(conn, monkeypatch):
    t = _cookie_server(conn, monkeypatch)
    token = auth.issue(conn, "noc", "admin")
    t["s"] = auth.TTL_SECONDS - 60
    assert _read(conn, token) is not None
    t["s"] = auth.TTL_SECONDS + 60
    assert _read(conn, token) is None


def test_a_cookie_does_not_live_three_hours_longer_after_a_backward_jump(conn, monkeypatch):
    t = _cookie_server(conn, monkeypatch)
    t["s"] = H3                                          # הונפקה כשהשעון היה 3 שעות קדימה
    token = auth.issue(conn, "noc", "admin")
    t["s"] = 0.0                                         # השעון חזר: התפוגה רחוקה ב-15 שעות
    assert _read(conn, token) is None


# ---------- אתגר MFA ----------


def test_an_mfa_challenge_lives_its_ttl_and_not_hours_after_a_backward_jump(conn, monkeypatch):
    t = {"s": H3}
    monkeypatch.setattr(mfa, "time", SimpleNamespace(time=lambda: time.time() + t["s"]))
    raw = mfa.make_challenge(conn, "noc")
    t["s"] = H3 + 60
    assert mfa._challenge_user(conn, raw) == "noc"
    t["s"] = 0.0
    assert mfa._challenge_user(conn, raw) is None
    t["s"] = H3 + mfa.CHALLENGE_TTL + 1
    assert mfa._challenge_user(conn, raw) is None


def test_the_pending_mfa_secret_clock_is_the_steady_one():
    assert mfa._now is steadyclock.now


# ---------- חלון pairing בין-שרתי ----------


def test_a_pairing_window_closes_after_a_backward_jump(conn):
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    code = interserver_auth.open_pairing_window(conn, ttl_seconds=180, now=now)
    soon = now + timedelta(seconds=30)
    assert interserver_auth.pairing_window_status(conn, now=soon)["open"] is True
    back = now - timedelta(hours=3)
    assert interserver_auth.pairing_window_status(conn, now=back)["open"] is False
    assert interserver_auth.verify_and_consume_code(conn, code, now=back) is False
    assert interserver_auth.verify_and_consume_code(conn, code, now=soon) is False   # נמחק, לא רק נדחה


def test_a_pairing_window_still_opens_and_consumes_normally(conn):
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    code = interserver_auth.open_pairing_window(conn, ttl_seconds=600, now=now)
    assert interserver_auth.verify_and_consume_code(
        conn, code, now=now + timedelta(seconds=599)) is True


def test_a_pairing_handle_is_dropped_after_a_backward_jump():
    from server.interserver import api as interserver_api

    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    handles = {"h": {"expires_at": now + timedelta(seconds=interserver_api.HANDLE_TTL_SECONDS)}}
    interserver_api._sweep_handles(handles, now + timedelta(seconds=10))
    assert "h" in handles
    interserver_api._sweep_handles(handles, now - timedelta(hours=3))
    assert handles == {}


# ---------- חלון אישור רשת ----------


def _marker(deadline):
    return SimpleNamespace(boot="b", deadline=deadline)


def test_netcfg_decide_reverts_now_on_a_backward_jump():
    armed = 10_000.0
    marker = _marker(armed + netcfg_rollback.WINDOW_SECONDS)
    assert netcfg_rollback.decide(marker, armed + 10, "b") is None
    assert netcfg_rollback.expired(marker, armed + 10) is False
    assert netcfg_rollback.decide(marker, armed - H3, "b") == "clock"
    assert netcfg_rollback.expired(marker, armed - H3) is True
    assert netcfg_rollback.decide(marker, armed + H3, "b") == netcfg_rollback.REASON_EXPIRED
    assert netcfg_rollback.REASON_HE.get("clock")


def test_nic_services_reverts_on_the_tick_after_a_backward_jump(rig):  # noqa: F811
    rig["clock"].t = 1_800_000_000.0                     # תאריך אמיתי: קפיצה אחורה לא יורדת מתחת ל-epoch 0
    svc, _out = handoff(rig)
    rig["scheduled"][0][1]()
    rig["clock"].t -= H3
    assert svc.pending() is not None
    assert svc.tick() == "clock"
    assert svc.pending() is None


def test_nic_services_default_clock_is_the_steady_one():
    from server.net import nic_services

    assert "\"now\": steadyclock.now" in inspect.getsource(nic_services)


# ---------- VM ----------


def test_vmupdate_default_clock_is_the_steady_one():
    from server.vm import vmupdate

    assert inspect.signature(vmupdate.VmUpdate.__init__).parameters["now"].default is steadyclock.now


# ---------- אתחול מתוזמן באמצע עדכון ----------


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc)


def _update_rt(server, tmp_path, started_at: datetime):
    import json

    from server import update
    from server.db import set_setting

    set_setting(server["ctx"].conn, update.STATUS_KEY, json.dumps(
        {"state": "running", "tag": "v0.73.2",
         "started_at": started_at.isoformat(timespec="seconds")}))
    hooks = {"describe": lambda _repo: "v0.73.1", "head": lambda _repo: None}   # גרסה אחרת: לא "כבר רץ"
    return SimpleNamespace(conn=server["ctx"].conn, data_dir=tmp_path, vmupdate=None,
                           repo_dir=tmp_path, update_hooks=hooks)


def test_a_live_update_is_not_stale_after_a_forward_jump(server, tmp_path, clocks, monkeypatch):
    from server import auto_reboot

    monkeypatch.setattr(auto_reboot, "_progress_seen", None, raising=False)
    rt = _update_rt(server, tmp_path, _utc(clocks.wall) - timedelta(seconds=30))
    assert auto_reboot.update_verdict(rt, _utc(clocks.wall))[0] is True
    clocks.jump(H3)                                       # NTP: השעון קופץ 3 שעות קדימה
    assert auto_reboot.update_verdict(rt, _utc(clocks.wall))[0] is True,         "the wall says 3h old; the server saw that stamp 0s ago"
    clocks.advance(auto_reboot.STALE_UPDATE_MINUTES * 60 + 1)
    assert auto_reboot.update_verdict(rt, _utc(clocks.wall))[0] is False   # באמת לא זז שעה


def test_a_stale_update_is_not_busy_at_once_when_the_clock_never_jumped(server, tmp_path, clocks, monkeypatch):
    from server import auto_reboot

    monkeypatch.setattr(auto_reboot, "_progress_seen", None, raising=False)
    old = _update_rt(server, tmp_path, _utc(clocks.wall) - timedelta(hours=2))
    assert auto_reboot.update_verdict(old, _utc(clocks.wall))[0] is False
    fresh = _update_rt(server, tmp_path, _utc(clocks.wall) - timedelta(minutes=5))
    assert auto_reboot.update_verdict(fresh, _utc(clocks.wall))[0] is True
