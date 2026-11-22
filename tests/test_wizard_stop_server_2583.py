"""#2583 -- the explicit rerun of the wizard stops the server for the setup, and brings it back.

Nadav, 12/11: no round / ingest / direct session -> the server stops, and comes back at the end or
on failure, also when the wizard is interrupted; work running -> refuse, naming it; positive
evidence that the server really stopped (the console port free, DB not open) before the wizard goes on;
"could not check for work" is a refusal.
"""
from __future__ import annotations

import dataclasses
import signal
import sqlite3
from pathlib import Path

import pytest

from server.wizard import __main__ as wizard_main
from server.wizard import serverguard
from server.wizard.serverguard import Hooks, Refused, stop_for_setup
from server.core import ports

ROOT = Path(__file__).resolve().parent.parent
DB = Path("/var/lib/imagesrv/imagesrv.db")


class Machine:
    """A fake host: the calls made, and what the checks answer."""

    def __init__(self, *, running=True, work=(), stop_rc=0, port_free=True, holders=(), start_rc=0,
                 dcui=False, dcui_stop_rc=0, dcui_start_rc=0):
        self.calls: list[str] = []
        self.running, self.work, self.stop_rc = running, list(work), stop_rc
        self.free, self.holders, self.start_rc = port_free, list(holders), start_rc
        self.dcui, self.dcui_stop_rc, self.dcui_start_rc = dcui, dcui_stop_rc, dcui_start_rc

    def hooks(self) -> Hooks:
        def stop():
            self.calls.append("stop")
            return self.stop_rc

        def start():
            self.calls.append("start")
            return self.start_rc

        def dcui_stop():
            self.calls.append("dcui-stop")
            return self.dcui_stop_rc

        def dcui_start():
            self.calls.append("dcui-start")
            return self.dcui_start_rc

        # the DCUI hooks only where Hooks has them: on a guard without them (the negative control of #3384) the tests
        # below fail on what happened to the DCUI, not on a keyword the old Hooks does not know
        dcui_hooks = {"dcui_active": lambda: self.dcui, "dcui_stop": dcui_stop, "dcui_start": dcui_start}
        known = {f.name for f in dataclasses.fields(Hooks)}
        return Hooks(unit_active=lambda: self.running, stop=stop, start=start,
                     port_free=lambda: self.free, active_work=lambda db: self.work,
                     db_holders=lambda db: self.holders, **{k: v for k, v in dcui_hooks.items() if k in known})


def test_no_work_stops_the_server_says_so_and_brings_it_back():
    machine, said = Machine(), []
    stopped = stop_for_setup(DB, machine.hooks(), said.append)
    assert machine.calls == ["stop"] and said == [serverguard.NOTICE_HE]
    assert stopped.restore() is True
    assert machine.calls == ["stop", "start"]
    assert stopped.restore() is True and machine.calls == ["stop", "start"], "restored twice"


def test_a_round_that_runs_refuses_by_name_and_stops_nothing():
    machine = Machine(work=["סבב פעיל בחדר המשכפלים (1)"])
    with pytest.raises(Refused) as caught:
        stop_for_setup(DB, machine.hooks(), lambda _t: None)
    assert "סבב פעיל בחדר המשכפלים" in str(caught.value)
    assert machine.calls == [], "the server was stopped under a running round"


def test_a_check_that_could_not_run_refuses_and_stops_nothing():
    machine = Machine()
    hooks = machine.hooks()
    hooks.active_work = lambda db: None
    with pytest.raises(Refused, match="לא הצלחנו לבדוק"):
        stop_for_setup(DB, hooks, lambda _t: None)
    hooks = machine.hooks()
    hooks.unit_active = lambda: None
    with pytest.raises(Refused, match="לא הצלחנו לבדוק"):
        stop_for_setup(DB, hooks, lambda _t: None)
    assert machine.calls == []


def test_a_server_that_was_already_off_is_not_started_by_the_wizard():
    machine = Machine(running=False)
    stopped = stop_for_setup(DB, machine.hooks(), lambda _t: None)
    assert stopped.restore() is True and machine.calls == []


def test_a_stop_that_failed_restores_and_refuses():
    machine = Machine(stop_rc=1)
    with pytest.raises(Refused, match="עצירת השרת נכשלה"):
        stop_for_setup(DB, machine.hooks(), lambda _t: None)
    assert machine.calls == ["stop", "start"]


@pytest.mark.parametrize("kwargs, word", [({"port_free": False}, f"פורט {serverguard.PORT} "), ({"port_free": None}, f"פורט {serverguard.PORT} "),
                                          ({"holders": [4242]}, "4242")])
def test_the_server_must_really_be_gone_before_the_wizard_goes_on(kwargs, word):
    machine = Machine(**kwargs)
    with pytest.raises(Refused, match=word):
        stop_for_setup(DB, machine.hooks(), lambda _t: None)
    assert machine.calls == ["stop", "start"], "refused but left the server stopped"


def test_a_db_that_could_not_be_checked_is_not_closed():
    machine = Machine()
    hooks = machine.hooks()
    hooks.db_holders = lambda db: None
    with pytest.raises(Refused, match="לא הצלחנו לבדוק שהמסד סגור"):
        stop_for_setup(DB, hooks, lambda _t: None)
    assert machine.calls == ["stop", "start"]


def test_a_server_that_would_not_start_again_is_said_and_returned_false():
    machine, said = Machine(start_rc=1), []
    stopped = stop_for_setup(DB, machine.hooks(), said.append)
    assert stopped.restore() is False
    assert any("systemctl start" in line for line in said), said


# --- #3384: the server's own screen (DCUI) holds imagesrv.db; a server installed from the ISO runs it ----------


def test_a_running_dcui_is_stopped_before_the_server_and_started_after_it():
    machine, said = Machine(dcui=True), []
    stopped = stop_for_setup(DB, machine.hooks(), said.append)
    assert machine.calls == ["dcui-stop", "stop"] and said == [serverguard.NOTICE_HE, serverguard.DCUI_NOTICE_HE]
    assert stopped.restore() is True
    assert machine.calls == ["dcui-stop", "stop", "start", "dcui-start"]
    assert stopped.restore() is True and len(machine.calls) == 4, "restored twice"


def test_a_dcui_that_was_not_running_is_not_started_by_the_wizard():
    machine = Machine(dcui=False)
    stopped = stop_for_setup(DB, machine.hooks(), lambda _t: None)
    assert stopped.restore() is True and machine.calls == ["stop", "start"]


def test_a_dcui_that_would_not_stop_restores_and_refuses():
    machine = Machine(dcui=True, dcui_stop_rc=1)
    with pytest.raises(Refused, match="עצירת מסך השרת"):
        stop_for_setup(DB, machine.hooks(), lambda _t: None)
    assert machine.calls == ["dcui-stop", "start", "dcui-start"], "refused but left something stopped"


def test_a_dcui_state_that_could_not_be_read_refuses_and_stops_nothing():
    machine = Machine()
    hooks = machine.hooks()
    hooks.dcui_active = lambda: None
    with pytest.raises(Refused, match="לא הצלחנו לבדוק אם מסך השרת"):
        stop_for_setup(DB, hooks, lambda _t: None)
    assert machine.calls == []


def test_a_server_stop_that_failed_after_the_dcui_was_stopped_brings_both_back():
    machine = Machine(dcui=True, stop_rc=1)
    with pytest.raises(Refused, match="עצירת השרת נכשלה"):
        stop_for_setup(DB, machine.hooks(), lambda _t: None)
    assert machine.calls == ["dcui-stop", "stop", "start", "dcui-start"]


def test_a_dcui_that_would_not_start_again_is_said_by_name_and_returned_false():
    machine, said = Machine(dcui=True, dcui_start_rc=1), []
    stopped = stop_for_setup(DB, machine.hooks(), said.append)
    assert stopped.restore() is False
    assert any("systemctl start imagesrv-dcui.service" in line for line in said), said
    assert machine.calls == ["dcui-stop", "stop", "start", "dcui-start"], "the server still came back"


def test_a_wizard_that_finished_brings_the_dcui_back_too(tmp_path, monkeypatch, sigterm_kept):
    machine = Machine(dcui=True)
    assert _run_main(tmp_path, monkeypatch, machine, lambda *_a: None) == 0
    assert machine.calls == ["dcui-stop", "stop", "start", "dcui-start"]


def _db(path: Path, **rows) -> Path:
    conn = sqlite3.connect(path)
    for table in ("room_rounds", "sessions", "tasks", "storage_transfers"):
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, state TEXT)")
    for table, state in rows.items():
        conn.execute(f"INSERT INTO {table} (state) VALUES (?)", (state,))
    conn.commit()
    conn.close()
    return path


def test_the_work_check_reads_the_four_places_work_lives_in(tmp_path):
    assert serverguard.active_work(_db(tmp_path / "idle.db", room_rounds="done", tasks="done")) == []
    busy = serverguard.active_work(_db(tmp_path / "busy.db", room_rounds="active", sessions="running",
                                       tasks="verifying", storage_transfers="sending"))
    assert [text.rsplit(" (", 1)[0] for text in busy] == [
        "סבב פעיל בחדר המשכפלים", "הפצה ישירה פתוחה או רצה",
        "קליטה או שחזור רצים (משימה פתוחה)", "העברת אימג' בין שרתים רצה"], busy


def test_a_db_that_cannot_be_read_is_not_idle(tmp_path):
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a database" * 200)
    assert serverguard.active_work(broken) is None
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()                 # a DB without the tables: the question was not answered
    assert serverguard.active_work(empty) is None
    assert serverguard.active_work(tmp_path / "missing.db") == []     # never created = no work


@pytest.fixture
def sigterm_kept():
    old = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, old)


def _run_main(tmp_path, monkeypatch, machine, serve):
    monkeypatch.setattr(serverguard, "default_hooks", machine.hooks)
    monkeypatch.setattr(wizard_main, "serve", serve)
    return wizard_main.main(["--rerun", "--data-dir", str(tmp_path / "data"),
                             "--installer", str(tmp_path / "i.sh"), "--verify", str(tmp_path / "v.sh")])


def test_a_wizard_that_is_interrupted_brings_the_server_back(tmp_path, monkeypatch, sigterm_kept):
    machine = Machine()

    def serve(*_a):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run_main(tmp_path, monkeypatch, machine, serve)
    assert machine.calls == ["stop", "start"]


def test_a_wizard_that_finished_brings_the_server_back_and_exits_0(tmp_path, monkeypatch, sigterm_kept):
    machine = Machine()
    assert _run_main(tmp_path, monkeypatch, machine, lambda *_a: None) == 0
    assert machine.calls == ["stop", "start"]


def test_a_refused_rerun_exits_3_and_never_serves(tmp_path, monkeypatch, sigterm_kept, capsys):
    machine, served = Machine(work=["סבב פעיל בחדר המשכפלים (1)"]), []
    assert _run_main(tmp_path, monkeypatch, machine, lambda *_a: served.append(1)) == 3
    assert served == [] and machine.calls == []
    assert "סבב פעיל בחדר המשכפלים" in capsys.readouterr().err


def test_the_first_boot_wizard_and_dev_mode_do_not_touch_the_server(tmp_path, monkeypatch, sigterm_kept):
    machine, served = Machine(), []
    monkeypatch.setattr(serverguard, "default_hooks", machine.hooks)
    monkeypatch.setattr(wizard_main, "serve", lambda *_a: served.append(1))
    args = ["--data-dir", str(tmp_path / "d1"), "--installer", "i", "--verify", "v"]
    assert wizard_main.main(args) == 0                                  # no --rerun
    assert wizard_main.main(["--rerun", "--dev", *args[:2]]) == 0       # dev
    assert served == [1, 1] and machine.calls == []


def test_the_rerun_unit_brings_the_server_back_even_when_the_wizard_was_killed():
    unit = (ROOT / "deploy" / "imagesrv-wizard-rerun.service").read_text(encoding="utf-8")
    assert "ExecStopPost=/usr/bin/systemctl start imagesrv-server.service" in unit
    assert "systemctl is-enabled --quiet imagesrv-dcui.service && systemctl start imagesrv-dcui.service" in unit
    first_boot = (ROOT / "deploy" / "imagesrv-wizard.service").read_text(encoding="utf-8")
    assert "ExecStopPost" not in first_boot


def test_the_guard_waits_for_the_console_port_of_the_table():
    """#3871: the port the guard waits on is the one the wizard and the server take, from the table, not a copy."""
    assert serverguard.PORT == ports.CONSOLE
    assert wizard_main.parser().parse_args([]).port == serverguard.PORT
