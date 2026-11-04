"""#2174 -- dnsmasq's late-NIC retry (#1816) is reconciled on every server start.

The installer writes dnsmasq.service.d/imagesrv-restart.conf and
server-upgrade.sh writes it on an update -- but the button runs the *old*
tag's server-upgrade.sh (#2350), so a server updating from a version without
that step never got it. The code under /opt/imagesrv is what every update
replaces, so the server itself now brings the drop-in to the tree's copy at
start: equal and loaded by systemd = nothing; missing / different / not
loaded = atomic write + daemon-reload, and success is systemd reporting the
file in dnsmasq's DropInPaths (rule 5), not the absence of an error.

systemd is a fake here (hooks); the files are real, under IMAGESRV_UNIT_DIR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.db import connect
from server.system import dnsmasq_dropin

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "deploy" / "dnsmasq-imagesrv-restart.conf"


class FakeSystemd:
    """dnsmasq's DropInPaths as systemd would report them: a reload loads what is on disk."""

    def __init__(self, target: Path, loaded: bool = False, reload_error: str | None = None,
                 show_error: str | None = None, reload_loads: bool = True):
        self.target = target
        self.loaded = loaded
        self.reload_error = reload_error
        self.show_error = show_error
        self.reload_loads = reload_loads
        self.reloads = 0

    def dropins(self):
        if self.show_error:
            return self.show_error
        return ["/usr/lib/systemd/system/dnsmasq.service.d/vendor.conf"] + (
            [str(self.target)] if self.loaded else [])

    def daemon_reload(self):
        self.reloads += 1
        if self.reload_error:
            return self.reload_error
        self.loaded = self.reload_loads and self.target.is_file()
        return None

    def hooks(self):
        return {"dropins": self.dropins, "daemon_reload": self.daemon_reload}


@pytest.fixture()
def units(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "units"
    monkeypatch.setenv("IMAGESRV_UNIT_DIR", str(path))
    return path


def _target(units: Path) -> Path:
    return units / "dnsmasq.service.d" / "imagesrv-restart.conf"


def _events(conn) -> list[tuple[str, str]]:
    return [(r["event"], r["detail"]) for r in conn.execute(
        "SELECT event, detail FROM journal WHERE event LIKE 'dnsmasq_dropin%' ORDER BY id")]


def _start(tmp_path, systemd) -> list[tuple[str, str]]:
    conn = connect(tmp_path / "start.db")
    try:
        dnsmasq_dropin.apply_on_start(conn, systemd.hooks())
        return _events(conn)
    finally:
        conn.close()


# --- the reconciliation -----------------------------------------------------------


def test_a_server_without_the_retry_gets_it_at_start_and_systemd_loads_it(tmp_path, units):
    """The #2350 server: updated by an old script, the drop-in was never written."""
    target = _target(units)
    systemd = FakeSystemd(target)

    events = _start(tmp_path, systemd)

    assert target.read_bytes() == SOURCE.read_bytes()
    assert systemd.reloads == 1 and systemd.loaded
    assert events == [("dnsmasq_dropin_applied", f"{target} נוסף ונטען ל-dnsmasq.service")]


def test_an_equal_loaded_drop_in_is_left_alone(tmp_path, units):
    """Idempotent: every later start writes nothing, reloads nothing, journals nothing."""
    target = _target(units)
    target.parent.mkdir(parents=True)
    target.write_bytes(SOURCE.read_bytes())
    before = target.stat().st_mtime_ns
    systemd = FakeSystemd(target, loaded=True)

    events = _start(tmp_path, systemd)

    assert events == []
    assert systemd.reloads == 0
    assert target.stat().st_mtime_ns == before


def test_a_different_drop_in_is_brought_to_the_tree_copy(tmp_path, units):
    """An older version of ours (or an edit to our file): replaced, as the installer does."""
    target = _target(units)
    target.parent.mkdir(parents=True)
    target.write_text("[Service]\nRestart=no\n", encoding="utf-8", newline="\n")
    systemd = FakeSystemd(target, loaded=True)

    events = _start(tmp_path, systemd)

    assert target.read_bytes() == SOURCE.read_bytes()
    assert systemd.reloads == 1
    assert events == [("dnsmasq_dropin_applied", f"{target} עודכן ונטען ל-dnsmasq.service")]


def test_an_equal_file_systemd_never_loaded_is_reloaded_not_called_equal(tmp_path, units):
    """A reload that failed on an earlier start: the file is right, systemd does not know it."""
    target = _target(units)
    target.parent.mkdir(parents=True)
    target.write_bytes(SOURCE.read_bytes())
    systemd = FakeSystemd(target, loaded=False)

    events = _start(tmp_path, systemd)

    assert systemd.reloads == 1 and systemd.loaded
    assert events == [("dnsmasq_dropin_applied",
                       f"{target} היה על הדיסק ולא נטען; נטען עכשיו ל-dnsmasq.service")]


def test_a_failed_reload_is_a_named_failure(tmp_path, units):
    target = _target(units)
    systemd = FakeSystemd(target, reload_error="systemctl daemon-reload נכשל (קוד 1): Access denied")

    events = _start(tmp_path, systemd)

    assert target.is_file()          # on disk -- systemd reads it at the next boot
    assert [e for e, _ in events] == ["dnsmasq_dropin_failed"]
    assert "Access denied" in events[0][1] and str(target) in events[0][1]


def test_a_reload_that_does_not_load_it_is_a_failure_not_success(tmp_path, units):
    """Exit 0 from daemon-reload is not the evidence; DropInPaths is (rule 5)."""
    target = _target(units)
    systemd = FakeSystemd(target, reload_loads=False)

    events = _start(tmp_path, systemd)

    assert [e for e, _ in events] == ["dnsmasq_dropin_failed"]
    assert "DropInPaths" in events[0][1]


def test_a_systemd_that_cannot_be_asked_is_not_up_to_date(tmp_path, units):
    """An equal file and an unanswered question are not "nothing to do"."""
    target = _target(units)
    target.parent.mkdir(parents=True)
    target.write_bytes(SOURCE.read_bytes())
    systemd = FakeSystemd(target, loaded=True,
                          show_error="systemctl show dnsmasq.service לא רץ: No such file")

    events = _start(tmp_path, systemd)

    assert [e for e, _ in events] == ["dnsmasq_dropin_failed"]
    assert "No such file" in events[0][1]


def test_a_drop_in_directory_that_cannot_be_written_is_a_named_failure(tmp_path, units):
    units.mkdir()
    (units / "dnsmasq.service.d").write_text("a file, not a directory\n", encoding="utf-8")
    systemd = FakeSystemd(_target(units))

    events = _start(tmp_path, systemd)

    assert [e for e, _ in events] == ["dnsmasq_dropin_failed"]
    assert "לא נקרא" in events[0][1] or "לא נכתב" in events[0][1], events
    assert systemd.reloads == 0


# --- the wiring: main lights it on Linux only, create_runtime runs it --------------


def test_main_lights_it_on_linux_and_not_on_a_dev_station(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from test_dev_station_no_etc_2267 import _run_main
    assert _run_main(tmp_path / "linux", monkeypatch, "linux")["dnsmasq_dropin"] is True
    assert _run_main(tmp_path / "win", monkeypatch, "win32")["dnsmasq_dropin"] is False


def test_create_runtime_reconciles_at_start_when_lit(tmp_path, units):
    pytest.importorskip("fastapi")
    from server.app import create_runtime
    target = _target(units)
    systemd = FakeSystemd(target)
    rt = create_runtime(tmp_path / "data", tmp_path / "img", "http://127.0.0.1:8199",
                        dnsmasq_dropin=True, host_hooks=systemd.hooks())
    try:
        assert target.read_bytes() == SOURCE.read_bytes()
        assert [e for e, _ in _events(rt.conn)] == ["dnsmasq_dropin_applied"]
    finally:
        rt.ctx.sender.stop()
        rt.conn.close()


def test_create_runtime_by_default_does_not_touch_the_unit_dir(tmp_path, units):
    """Tests (and every other caller) never reach the machine's systemd."""
    pytest.importorskip("fastapi")
    from server.app import create_runtime
    rt = create_runtime(tmp_path / "data", tmp_path / "img", "http://127.0.0.1:8199")
    try:
        assert not units.exists()
        assert _events(rt.conn) == []
    finally:
        rt.ctx.sender.stop()
        rt.conn.close()
