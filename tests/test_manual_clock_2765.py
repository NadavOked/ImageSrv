"""#2765 (Nadav 09/11): "ידני — בלי שרת זמן" -- a server with no internet and a time its operator set.

"It must not show a problem if I set the time by hand and I have no internet."

The chain, each link tested here:

* ``timesync`` -- the third source: chrony without sources (byte for byte what the installer
  writes), the system clock set and *read back*, the flag and the moment saved in one transaction;
* the console route -- the same value read and changed (settings -> time source), a refused time
  changes nothing, a time server chosen afterwards takes the flag away;
* ``/health`` -- the clock row is **information** when manual (not counted in "needs attention"),
  says when it was set, and says nothing is measured; a time server that was CHOSEN and does not
  answer is still a problem (the decision of 09/11: not weakened);
* the server screen (DCUI) -- no orange tag and no problem card for a manual clock; an unsynced
  chosen server still gets both;
* MFA -- the skew check is not switched off by the manual source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.console import mfa, users
from server.db import connect, get_setting
from server.dcui import view
from server.dcui import data as dcui_data   # attribute access, so a build without #2765 fails each test on its own
from server.dcui import samples
from server.dcui.data import CLOCK_SYNCED, CLOCK_UNSYNCED, CommandResult, clock_state
from server.system import health, timesync

REPO = Path(__file__).resolve().parent.parent
ZONE = "Asia/Jerusalem"
SET_AT = "2026-11-09T12:32:00+00:00"          # 14:32 in Jerusalem (UTC+2 in November)
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


class ClockMachine:
    """chrony and the clock as the server sees them: the calls it makes, and the measurement it gets."""

    def __init__(self):
        self.written, self.manual_written, self.set_to, self.restarts, self.order = [], 0, [], 0, []
        self.rtc, self.set_error, self.override = True, None, None   # override: what the health hook answers instead of chrony
        self.tracking =("Reference ID : 1.2.3.4\nSystem time : 0.001250 seconds slow of NTP time\n"
                         "Leap status : Normal\n")
        self.sources = ("MS Name/IP address Stratum Poll Reach LastRx Last sample\n"
                        "^* 1.2.3.4 2 6 377 10 -10us[ -20us] +/- 1ms\n")

    def hooks(self):
        return {"write": self._write, "write_manual": self._write_manual, "set_time": self._set_time,
                "write_server": lambda _: None, "ensure_confdir": lambda: None, "restart": self._restart,
                "tracking": lambda: self.tracking, "sources": lambda: self.sources,
                "sleep": lambda _: None, "now": lambda: "2026-11-09T12:34:00+00:00"}

    def _write(self, value): self.order.append("write"); self.written.append(value)
    def _write_manual(self): self.order.append("write_manual"); self.manual_written += 1
    def _restart(self): self.order.append("restart"); self.restarts += 1
    def _set_time(self, epoch):
        self.order.append("set_time")
        if self.set_error: raise RuntimeError(self.set_error)
        self.set_to.append(epoch)
        return self.rtc

    def go_unsynced(self):
        self.tracking = self.tracking.replace("Normal", "Not synchronised")
        self.sources = self.sources.replace("^*", "^?")


@pytest.fixture()
def api(tmp_path, images_root, clock):
    from server.app import create_app
    from test_sender import Recorder
    machine = ClockMachine()
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), host_hooks=machine.hooks(),
                     health_hooks={"interfaces": lambda: [],
                                   "timesync_status": lambda: (machine.override if machine.override is not None
                                                               else timesync.status(machine.hooks()))})
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    admin = TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")
    yield admin, machine, ctx
    ctx.sender.stop()


def _row(admin) -> dict:
    return next(c for c in admin.get("/api/console/health").json() if c["id"] == "time_sync")


# --- timesync ---------------------------------------------------------------------

def test_parse_local_reads_the_operators_wall_clock_in_the_servers_zone():
    assert timesync.parse_local("2026-11-09 14:32", ZONE) == int(SET_AT_EPOCH())
    assert timesync.parse_local("2026-11-09T14:32", ZONE) == int(SET_AT_EPOCH()), "datetime-local's form"
    assert timesync.parse_local("2026-11-09 14:32:30", ZONE) == int(SET_AT_EPOCH()) + 30
    assert timesync.parse_local("2026-07-01 12:00", ZONE) == int(datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).timestamp()), "summer: UTC+3"


def SET_AT_EPOCH() -> float:
    return datetime.fromisoformat(SET_AT).timestamp()


@pytest.mark.parametrize("value", ["", None, "soon", "2026-02-31 10:00", "2026-13-01 10:00", "2026-11-09 24:00",
                                   "2026-11-09 14:60", "1999-12-31 23:59", "2100-01-01 00:00", "09/11/2026 14:32",
                                   "2026-11-09 14:32 x"])
def test_parse_local_refuses_what_is_not_a_real_time_and_never_rounds_it_into_one(value):
    with pytest.raises(ValueError):
        timesync.parse_local(value, ZONE)


def test_manual_chrony_file_has_no_source_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "CONF_DIR", tmp_path)
    monkeypatch.setattr(timesync, "CLIENT_CONF", tmp_path / "imagesrv.conf")
    timesync._write_manual()
    text = (tmp_path / "imagesrv.conf").read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    assert text == "# Managed by ImageSrv — time source (#2765): manual, no time server\n"
    assert not any(line.split(" ")[0] in ("server", "pool", "peer", "makestep") for line in text.splitlines())


def test_apply_manual_rewrites_chrony_and_restarts_BEFORE_it_sets_the_time():
    """A chrony that still had a source could step the clock back after it was set."""
    machine = ClockMachine()
    result = timesync.apply_manual(1_700_000_000, machine.hooks())
    assert machine.order == ["write_manual", "restart", "set_time"]
    assert machine.set_to == [1_700_000_000] and result["rtc_written"] is True
    assert result["set_at"] == "2023-11-14T22:13:20+00:00"


def test_apply_manual_says_when_only_the_running_clock_was_set():
    machine = ClockMachine(); machine.rtc = False
    assert timesync.apply_manual(1_700_000_000, machine.hooks())["rtc_written"] is False


def test_set_time_proves_the_clock_by_reading_it_back(monkeypatch):
    """`date` that exits 0 and changes nothing is not "set" (principle 5)."""
    calls = []
    monkeypatch.setattr(timesync, "_read", lambda command: calls.append(command) or "")
    with pytest.raises(RuntimeError, match="נקרא בחזרה"):
        timesync._set_time(1_700_000_000)          # the real clock is nowhere near 2023
    assert calls == [["date", "-u", "-s", "@1700000000"]], "and the hardware clock is not written after a failed set"


def test_set_time_reports_a_missing_hardware_clock_instead_of_hiding_it(monkeypatch):
    import time
    target = int(time.time())

    def read(command):
        if command[0] == "hwclock":
            raise RuntimeError("no rtc")
        return ""
    monkeypatch.setattr(timesync, "_read", read)
    assert timesync._set_time(target) is False
    monkeypatch.setattr(timesync, "_read", lambda command: "")
    assert timesync._set_time(target) is True


def test_save_is_one_flag_with_its_moment_and_a_time_server_takes_it_away(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert timesync.mode(conn) == "auto" and not timesync.is_manual(conn)
    timesync.save(conn, "", SET_AT)
    assert (timesync.mode(conn), timesync.manual_at(conn), timesync.configured(conn)) == ("manual", SET_AT, "")
    timesync.save(conn, "10.44.0.250")
    assert (timesync.mode(conn), timesync.manual_at(conn)) == ("org", "")
    assert get_setting(conn, timesync.MANUAL_SETTING) is None, "the flag is gone, not 'false'"
    timesync.save(conn, "")
    assert timesync.mode(conn) == "auto"


def test_start_up_keeps_a_manual_clock_manual_and_does_not_set_the_time_again(tmp_path):
    conn = connect(tmp_path / "t.db")
    timesync.save(conn, "", SET_AT)
    machine = ClockMachine()
    timesync.apply_on_start(conn, machine.hooks())
    assert machine.order == ["write_manual", "restart"] and machine.written == [], "never 'automatic' behind the operator's back"
    assert not machine.set_to
    assert conn.execute("SELECT detail FROM journal WHERE event='timesync_applied'").fetchone()[0].startswith("ידני")


# --- the console route ------------------------------------------------------------

def test_the_console_reads_the_mode_and_the_moment(api):
    admin, machine, ctx = api
    got = admin.get("/api/console/timesync").json()
    assert got["mode"] == "auto" and got["manual_at"] == "" and got["ntp_servers"] == ""
    changed = admin.post("/api/console/timesync", json={"mode": "manual", "time": "2026-11-09 14:32"})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["mode"] == "manual" and body["manual_at"] == SET_AT and body["rtc_written"] is True
    assert machine.set_to == [int(SET_AT_EPOCH())] and machine.manual_written == 1 and machine.written == []
    assert admin.get("/api/console/timesync").json()["mode"] == "manual"
    assert get_setting(ctx.conn, "ntp_servers") == "", "'automatic' and 'manual' are different choices"
    row = ctx.conn.execute("SELECT detail FROM journal WHERE event='timesync_changed'").fetchone()[0]
    assert "ידני" in row and "14:32" in row, row


def test_a_refused_time_changes_nothing(api):
    admin, machine, ctx = api
    for bad in ("", "soon", "2026-02-31 10:00"):
        assert admin.post("/api/console/timesync", json={"mode": "manual", "time": bad}).status_code == 400
    assert machine.order == [] and not machine.set_to
    assert get_setting(ctx.conn, "ntp_manual") is None


def test_a_failed_set_is_an_error_and_not_saved_as_manual(api):
    admin, machine, ctx = api
    machine.set_error = "date: cannot set date"
    response = admin.post("/api/console/timesync", json={"mode": "manual", "time": "2026-11-09 14:32"})
    assert response.status_code == 500 and "cannot set date" in response.json()["detail"]
    assert get_setting(ctx.conn, "ntp_manual") is None, "the flag is saved only after the clock was set"
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event='timesync_change_failed'").fetchone()[0] == 1


def test_choosing_a_time_server_afterwards_clears_manual(api):
    admin, machine, ctx = api
    admin.post("/api/console/timesync", json={"mode": "manual", "time": "2026-11-09 14:32"})
    changed = admin.post("/api/console/timesync", json={"ntp_servers": "10.44.0.250"})
    assert changed.status_code == 200 and changed.json()["mode"] == "org"
    assert get_setting(ctx.conn, "ntp_manual") is None and get_setting(ctx.conn, "ntp_manual_at") is None
    assert machine.written == ["10.44.0.250"]


def test_the_old_form_of_the_request_is_unchanged(api):
    admin, machine, _ = api
    changed = admin.post("/api/console/timesync", json={"ntp_servers": "ntp.example.org"})
    assert changed.status_code == 200 and changed.json()["mode"] == "org" and machine.written == ["ntp.example.org"]


# --- /health: information, not a problem -----------------------------------------

def test_a_manual_clock_is_information_that_says_when_and_what_is_not_measured():
    row = health.manual_time_check(SET_AT, ZONE, NOW)
    assert row["state"] == "info" and row["id"] == "time_sync" and row["label"] == "שעון"
    assert "שעה ידנית" in row["detail"] and "09/11/2026 14:32" in row["detail"], row["detail"]
    assert "MFA" in row["detail"] and "TLS" in row["detail"], "the two things that lean on this clock"
    assert "עברו" not in row["detail"], "a day old is not stale"


def test_a_long_old_manual_time_says_how_old_and_is_still_information():
    row = health.manual_time_check(SET_AT, ZONE, datetime(2027, 1, 20, tzinfo=timezone.utc))
    assert row["state"] == "info" and "עברו 71 ימים" in row["detail"]


def test_an_unrecorded_moment_is_said_to_be_unknown_and_not_guessed():
    row = health.manual_time_check("", ZONE, NOW)
    assert row["state"] == "info" and "מועד ההגדרה לא ידוע" in row["detail"]


def test_the_health_page_shows_manual_as_information_even_though_chrony_is_unsynchronized(api):
    admin, machine, ctx = api
    machine.go_unsynced()                               # no time server -- chrony is "Not synchronised"
    timesync.save(ctx.conn, "", SET_AT)
    row = _row(admin)
    assert row["state"] == "info", row
    states = {c["state"] for c in admin.get("/api/console/health").json() if c["id"] == "time_sync"}
    assert not states & {"warn", "bad"}, "not counted in 'needs attention' (the console counts warn and bad)"


def test_a_time_server_that_was_chosen_and_does_not_answer_is_still_a_problem(api):
    """The decision of 09/11, not weakened: only the *manual* source is information."""
    admin, machine, ctx = api
    timesync.save(ctx.conn, "10.44.0.250")
    machine.go_unsynced()
    row = _row(admin)
    assert row["state"] == "warn" and "לא מסונכרן" in row["detail"], row
    timesync.save(ctx.conn, "")                         # automatic: same
    assert _row(admin)["state"] == "warn"


def test_a_clock_that_chrony_cannot_read_is_still_not_ok_when_a_time_server_is_chosen(api):
    admin, machine, ctx = api
    timesync.save(ctx.conn, "10.44.0.250")
    machine.override = {"synchronized": None}
    assert _row(admin)["state"] == "off", "not checked is not 'fine'"


def test_clearing_manual_brings_the_measurement_back(api):
    admin, machine, ctx = api
    machine.go_unsynced()
    timesync.save(ctx.conn, "", SET_AT)
    assert _row(admin)["state"] == "info"
    timesync.save(ctx.conn, "")
    assert _row(admin)["state"] == "warn"


# --- the server screen (DCUI) -----------------------------------------------------

TRACKING_NONE = ("Reference ID    : 00000000 ()\nStratum         : 0\n"
                 "System time     : 0.000000000 seconds fast of NTP time\nLeap status     : Not synchronised\n")
SOURCES_NONE = ("MS Name/IP address         Stratum Poll Reach LastRx Last sample\n"
                "===============================================================\n")


def test_dcui_reads_manual_without_asking_chrony_and_other_states_as_before(tmp_path):
    conn = connect(tmp_path / "t.db")
    asked = []

    def runner(argv):
        asked.append(argv)
        return CommandResult(0, TRACKING_NONE if argv[2] == "tracking" else SOURCES_NONE, "")
    assert clock_state(runner, conn)[0] == CLOCK_UNSYNCED, "no manual flag: an unanswered source is unsynced"
    timesync.save(conn, "", SET_AT)
    asked.clear()
    assert clock_state(runner, conn) == (dcui_data.CLOCK_MANUAL, "") and asked == [], "manual is the operator's word, not a measurement"
    timesync.save(conn, "10.44.0.250")
    assert clock_state(runner, conn) == (CLOCK_UNSYNCED, "10.44.0.250")


def test_dcui_an_unreadable_flag_is_not_manual(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")

    def broken(_conn):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(timesync, "is_manual", broken)
    runner = lambda argv: CommandResult(0, TRACKING_NONE if argv[2] == "tracking" else SOURCES_NONE, "")  # noqa: E731
    assert clock_state(runner, conn)[0] == CLOCK_UNSYNCED, "the measurement decides; a problem is not hidden"


def _screen(clock: str, uptime: int = 900):
    from dataclasses import replace
    snapshot = replace(samples.PRIMARY, clock=clock, clock_server="10.44.0.250")
    extras = view.Extras(uptime_seconds=uptime)
    return snapshot, extras, datetime(2026, 11, 10)


def test_dcui_a_manual_clock_has_no_tag_and_no_problem_card_but_an_unsynced_server_has_both():
    snapshot, extras, today = _screen("manual")
    assert view.clock_chip(snapshot, extras) == "" and view.clock_problem(snapshot, extras) is None
    assert not [p for p in view.problems(snapshot, extras, today) if "שעון" in p.title]
    snapshot, extras, today = _screen(CLOCK_UNSYNCED)
    assert view.clock_chip(snapshot, extras) == "השעון לא מסונכרן"
    assert [p for p in view.problems(snapshot, extras, today) if p.title == "השעון של השרת לא מסונכרן"]
    snapshot, _, _ = _screen(CLOCK_SYNCED)
    assert view.clock_problem(snapshot, extras) is None


# --- MFA: the skew check stays ----------------------------------------------------

def test_the_skew_check_is_not_switched_off_by_the_manual_source():
    """The status a manual clock reports (chrony has no source: not synchronized, offset unknown) still
    makes a rejected code say so; and a measured offset past 30 s is still named."""
    assert "not synchronized" in mfa.clock_problem(lambda: {"synchronized": False, "offset_ms": None})
    assert "off by 45s" in mfa.clock_problem(lambda: {"synchronized": True, "offset_ms": 45_000})
    assert mfa.clock_problem(lambda: {"synchronized": True, "offset_ms": 3.0}) is None


# --- first boot: the installer's choice reaches the server's setting ------------------

def test_firstboot_hands_the_manual_moment_to_setup_and_setup_writes_both_settings():
    firstboot = (REPO / "tools/iso/firstboot-answers.sh").read_text(encoding="utf-8")
    assert ('if [[ -n "$(answers_get ntp_manual_at)" ]]; then argv+=(--ntp-manual-at '
            '"$(answers_get ntp_manual_at)"); fi') in firstboot
    setup = (REPO / "deploy/setup-boot-server.sh").read_text(encoding="utf-8")
    assert '--ntp-manual-at) NTP_MANUAL_AT="${2:?}"; shift 2 ;;' in setup
    assert 'timesync.save(conn, "", set_at)' in setup
    assert '[[ -z "$NTP_SERVERS" || -z "$NTP_MANUAL_AT" ]] || die' in setup, "a time server and manual are alternatives"
