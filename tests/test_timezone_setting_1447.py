"""#1447 (Nadav 17/10): the server's timezone -- a console setting (admin
only) that is the OS zone too, so the DCUI shows local time.

* ``GET/POST /api/console/timezone``: POST refuses a zone outside tzdata's
  list before it touches the machine, runs ``timedatectl set-timezone``,
  reads the zone back, and only then writes the setting -- the setting that
  ``hello``'s ``tz`` is derived from.
* ``timezone.apply_on_start``: a server installed with Debian's Etc/UTC
  (before #1447) is brought to the setting (default Asia/Jerusalem) when the
  server starts -- ``main`` only (``apply_timezone=True``), never in tests.
* the DCUI re-reads the zone for every frame (``gui.local_now``).

The machine is never touched here: ``host_hooks`` replace ``timedatectl``.
The DB keeps UTC; only the display is local.
"""

from __future__ import annotations

import inspect
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from conftest import complete_console_login, hello_body
from server.system import timezone
from server.console import users
from server.db import connect, get_setting, set_setting

ZONE_TAB = ("# tzdata zone.tab (excerpt)\n"
            "IL\t+314650+0351326\tAsia/Jerusalem\n"
            "GB\t+513030-0000731\tEurope/London\n"
            "US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)\n")
FOOTERS = {"Asia/Jerusalem": b"IST-2IDT,M3.4.4/26,M10.5.0", "Europe/London": b"GMT0BST,M3.5.0/1,M10.5.0",
           "America/New_York": b"EST5EDT,M3.2.0,M11.1.0", "UTC": b"UTC0"}


def _zoneinfo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "zone.tab").write_text(ZONE_TAB, encoding="utf-8")
    for name, footer in FOOTERS.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif2" + b"\0" * 40 + b"\n" + footer + b"\n")
    return root


class Machine:
    """timedatectl, faked: the zone it reports and what it was asked."""

    def __init__(self, zone: str | None = "Etc/UTC"):
        self.zone, self.calls, self.set_error, self.sticky = zone, [], None, False

    def read(self):
        return self.zone

    def set(self, zone):
        self.calls.append(zone)
        if self.set_error:
            return self.set_error
        if not self.sticky:          # sticky: "succeeds" and changes nothing
            self.zone = zone
        return None

    def hooks(self):
        return {"timezone": self.read, "set_timezone": self.set}


@pytest.fixture()
def tz(tmp_path, images_root, clock, monkeypatch):
    from server.app import create_app
    from test_sender import Recorder

    monkeypatch.setattr(timezone, "ZONEINFO", _zoneinfo(tmp_path / "zoneinfo"))
    # set_system_zone re-reads the zone (time.tzset) on success; the fake
    # machine changed nothing, and this process's zone must stay as it is.
    monkeypatch.setattr(time, "tzset", lambda: None, raising=False)
    machine = Machine()
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), host_hooks=machine.hooks())
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")
    assert deploy.post("/api/console/login",
                       json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "deploy": deploy, "anon": TestClient(app), "machine": machine}
    ctx.sender.stop()


def events(conn, name):
    return [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (name,))]


# --- the console ----------------------------------------------------------------


def test_status_names_the_zone_in_effect_the_os_zone_and_the_choices(tz):
    r = tz["admin"].get("/api/console/timezone")
    assert r.status_code == 200, r.text
    assert r.json() == {"zone": "Asia/Jerusalem", "system": "Etc/UTC",
                        "zones": ["America/New_York", "Asia/Jerusalem", "Europe/London", "UTC"]}


def test_a_change_sets_the_os_reads_it_back_writes_the_setting_and_reaches_hello(tz):
    r = tz["admin"].post("/api/console/timezone", json={"zone": "Europe/London"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "zone": "Europe/London"}
    assert tz["machine"].calls == ["Europe/London"] and tz["machine"].zone == "Europe/London"
    assert get_setting(tz["ctx"].conn, "timezone") == "Europe/London"
    assert events(tz["ctx"].conn, "timezone_changed") == ["Asia/Jerusalem → Europe/London"]
    hello = tz["anon"].post("/api/v1/agent/hello", json=hello_body("b4:2e:99:07:1a:fe")).json()
    assert hello["tz"] == "GMT0BST,M3.5.0/1,M10.5.0", "the stations follow the setting"
    assert tz["admin"].get("/api/console/timezone").json()["zone"] == "Europe/London"


@pytest.mark.parametrize("zone", ["Asia/Jerusalm", "../../etc/passwd", "", None, 3, "Etc/GMT+2"])
def test_a_zone_outside_the_list_is_refused_before_the_machine_is_touched(tz, zone):
    r = tz["admin"].post("/api/console/timezone", json={"zone": zone})
    assert r.status_code == 400
    assert "אזור זמן לא מוכר" in r.json()["detail"]
    assert tz["machine"].calls == []
    assert get_setting(tz["ctx"].conn, "timezone") is None


def test_a_failed_timedatectl_changes_nothing_and_is_said(tz):
    tz["machine"].set_error = "timedatectl החזיר 1: Access denied"
    r = tz["admin"].post("/api/console/timezone", json={"zone": "Europe/London"})
    assert r.status_code == 500
    assert "Access denied" in r.json()["detail"]
    assert get_setting(tz["ctx"].conn, "timezone") is None, "no setting the OS does not have"
    assert events(tz["ctx"].conn, "timezone_change_failed")
    assert not events(tz["ctx"].conn, "timezone_changed")


def test_success_is_the_read_back_not_the_exit_code(tz):
    tz["machine"].sticky = True
    r = tz["admin"].post("/api/console/timezone", json={"zone": "Europe/London"})
    assert r.status_code == 500
    assert "לא אומת" in r.json()["detail"]
    assert get_setting(tz["ctx"].conn, "timezone") is None


@pytest.mark.parametrize("method", ["get", "post"])
def test_deploy_user_gets_403(tz, method):
    r = getattr(tz["deploy"], method)("/api/console/timezone", **({"json": {"zone": "UTC"}} if method == "post" else {}))
    assert r.status_code == 403
    assert tz["machine"].calls == []


def test_the_generic_settings_route_cannot_bypass_the_os(tz):
    r = tz["admin"].post("/api/console/settings", json={"timezone": "Europe/London"})
    assert r.status_code == 400
    assert get_setting(tz["ctx"].conn, "timezone") is None


def test_an_unreadable_zone_list_is_an_error_not_an_empty_list(tz, tmp_path, monkeypatch):
    monkeypatch.setattr(timezone, "ZONEINFO", tmp_path / "missing")
    assert tz["admin"].get("/api/console/timezone").status_code == 500
    assert tz["admin"].post("/api/console/timezone", json={"zone": "UTC"}).status_code == 500
    assert tz["machine"].calls == []


# --- the server's start ---------------------------------------------------------


def _conn(tmp_path, zone=None):
    conn = connect(tmp_path / "start.db")
    if zone:
        set_setting(conn, "timezone", zone)
    return conn


def _apply(conn, machine, monkeypatch):
    monkeypatch.setattr(time, "tzset", lambda: None, raising=False)
    timezone.apply_on_start(conn, machine.hooks())


def test_a_server_installed_with_utc_is_brought_to_the_default_at_start(tmp_path, monkeypatch):
    conn, machine = _conn(tmp_path), Machine("Etc/UTC")
    _apply(conn, machine, monkeypatch)
    assert machine.calls == ["Asia/Jerusalem"]
    assert events(conn, "timezone_applied") == ["Etc/UTC → Asia/Jerusalem"]


def test_the_setting_wins_over_the_default_at_start(tmp_path, monkeypatch):
    conn, machine = _conn(tmp_path, "Europe/London"), Machine("Asia/Jerusalem")
    _apply(conn, machine, monkeypatch)
    assert machine.calls == ["Europe/London"]


def test_an_equal_zone_is_left_alone_and_unwritten(tmp_path, monkeypatch):
    conn, machine = _conn(tmp_path), Machine("Asia/Jerusalem")
    _apply(conn, machine, monkeypatch)
    assert machine.calls == []
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event LIKE 'timezone%'").fetchone()[0] == 0


@pytest.mark.parametrize("zone,error", [(None, None), ("Etc/UTC", "timedatectl לא רץ: no bus")])
def test_an_unread_or_failed_start_is_a_journal_line(tmp_path, monkeypatch, zone, error):
    conn, machine = _conn(tmp_path), Machine(zone)
    machine.set_error = error
    _apply(conn, machine, monkeypatch)
    assert machine.calls == ([] if zone is None else ["Asia/Jerusalem"])
    assert events(conn, "timezone_apply_failed") and not events(conn, "timezone_applied")


def test_create_runtime_applies_only_when_asked(tmp_path, images_root):
    from server.app import create_runtime
    off, on = Machine("Etc/UTC"), Machine("Etc/UTC")
    for machine, flag, sub in ((off, False, "a"), (on, True, "b")):
        rt = create_runtime(tmp_path / sub, images_root, "http://10.44.12.10:8080",
                            host_hooks=machine.hooks(), apply_timezone=flag)
        rt.ctx.sender.stop()
    assert off.calls == [], "tests (the default) never touch the zone"
    assert on.calls == ["Asia/Jerusalem"]


def test_main_turns_it_on():
    from server import main
    assert "apply_timezone=True" in inspect.getsource(main.main)


# --- the DCUI -------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset is POSIX-only; runs on Linux/CI")
def test_the_dcui_clock_follows_a_zone_changed_under_it(monkeypatch):
    from server.dcui import gui, samples
    monkeypatch.setenv("TZ", "UTC0")
    time.tzset()
    try:
        gui.frame_now(samples._runtime(samples.PRIMARY, samples.PRIMARY_EXTRAS))
        # timedatectl changed /etc/localtime; this process was not restarted.
        monkeypatch.setenv("TZ", "XYZ-3")
        frame = gui.frame_now(samples._runtime(samples.PRIMARY, samples.PRIMARY_EXTRAS))
        clock = next(line.split("\t")[5] for line in frame.split("\n") if line.startswith("hdr\t"))
        utc = datetime.now(dt_timezone.utc)
        local = {(utc + timedelta(hours=3, minutes=d)).strftime("%H:%M") for d in (-1, 0)}
        assert clock in local, f"the DCUI shows {clock}; UTC+3 is {sorted(local)}"
    finally:
        monkeypatch.undo()
        time.tzset()
