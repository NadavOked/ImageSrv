"""#1537: "Restart Management Network" (F2 -> password -> Down -> Enter) changes
no setting, so it arms no rollback and asks for no confirmation.  Before the
fix it went through apply_network: the 60 s marker was armed, the result
screen said "confirm within 60 seconds" with only a "back" button, Configure
was refused for the next minute, and the timer restarted the network again.

These run the real restart_management_network (not the stub of
test_dcui.py) through the real keys; only the host boundary -- ifdown/ifup,
the live read-back, the files, the timer -- is faked, through
actions.default_network_hooks, which is where production gets them."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from server.net import netcfg, netcfg_host, netcfg_rollback
from server.console import users
from server.db import connect, set_setting
from server.dcui import actions, view
from server.dcui.app import Runtime, _current_lines, _handle_key
from server.dcui.data import demo_snapshot

KEYS = SimpleNamespace(KEY_ENTER=343, KEY_F2=266, KEY_F4=268, KEY_F5=269, KEY_F9=273,
                       KEY_F12=276, KEY_BACKSPACE=263, KEY_UP=259, KEY_DOWN=258,
                       KEY_LEFT=260, KEY_RIGHT=261, KEY_BTAB=353)
NIC = "ens18"                       # demo_snapshot's management NIC
SAVED = netcfg.NetConfig(NIC, mode="static", address="10.44.9.12",
                         netmask="255.255.255.0", gateway="10.44.9.254")


class FakeHost:
    """The host side of the network path, recorded."""

    def __init__(self, addresses=("10.44.9.12/24",), apply_error=None):
        self.calls: list[tuple] = []
        self.addresses = list(addresses)
        self.apply_error = apply_error

    def hooks(self) -> dict:
        def record(name, result=None):
            def hook(*args):
                self.calls.append((name, *args))
                return result
            return hook
        return {
            "netcfg_timer_active": lambda: self.calls.append(("timer",)) or (True, "active"),
            "now": time.time,
            "boot_id": lambda: "boot-1537",
            "netcfg_read_conf": lambda name, root: "previous\n",
            "read_resolv": lambda: "nameserver 10.44.9.2\n",
            "hostname": lambda: "imagesrv-1537",
            "netcfg_write_conf": record("write_conf"),
            "netcfg_write_resolv": record("write_resolv"),
            "netcfg_apply": lambda name: self.calls.append(("apply", name)) or self.apply_error,
            "netcfg_state": lambda: netcfg_host.LiveState(
                True, addresses={NIC: list(self.addresses)} if self.addresses else {},
                routes=["default via 10.44.9.254"]),
        }

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def host(monkeypatch):
    fake = FakeHost()
    monkeypatch.setattr(actions, "default_network_hooks", fake.hooks)
    return fake


def _runtime(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    users.create(conn, "admin", "before", "admin", by="test",
                 is_builtin=True, check_policy=False)
    set_setting(conn, netcfg.SETTING_PREFIX + NIC, SAVED.to_json())
    snapshot = demo_snapshot()
    runtime = Runtime(tmp_path, conn, snapshot, last_key_at=time.monotonic())
    runtime.interfaces = list(snapshot.interfaces)
    return runtime


def _press(runtime, *keys):
    for key in keys:
        _handle_key(runtime, key, KEYS)


def _restart_from_the_keyboard(runtime):
    """F2 -> admin password -> Down -> Enter: the reproduction in #1537."""
    _press(runtime, KEYS.KEY_F2, *"before", "\n")
    assert runtime.screen == "menu", runtime.message
    _press(runtime, KEYS.KEY_DOWN, "\n")
    assert runtime.screen == "result"


def _pending(runtime):
    return netcfg_rollback.read_pending(runtime.data_dir / "netcfg")


def _journal(runtime, event):
    return [row["detail"] for row in runtime.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def test_restart_arms_no_rollback_and_configure_works_right_after(tmp_path, host):
    runtime = _runtime(tmp_path)
    _restart_from_the_keyboard(runtime)
    assert runtime.result.ok, runtime.result
    assert _pending(runtime) is None
    # Nothing is changed: no file is written and the rollback timer is not
    # needed -- only ifdown/ifup on the NIC and the live read-back.
    assert host.names() == ["apply"] and host.calls[0] == ("apply", NIC)
    assert _journal(runtime, "net_rollback_armed") == []
    assert _journal(runtime, "dcui_management_network_restart") == [f"{NIC} ok=true"]
    # Configure, right away: the form opens and its apply arms the window.
    _press(runtime, "\n", KEYS.KEY_UP, "\n")
    assert runtime.screen == "network"
    _press(runtime, "\n")
    assert runtime.screen == "network_confirm", runtime.message
    assert runtime.result.ok and _pending(runtime) is not None


def test_the_result_promises_no_confirmation_on_either_screen(tmp_path, host):
    runtime = _runtime(tmp_path)
    _restart_from_the_keyboard(runtime)
    text = "\n".join(_current_lines(runtime, time.monotonic()))
    assert "confirm" not in text.lower() and "60" not in text, text
    frame = view.render(runtime, time.monotonic(), time.time(), "12:00", datetime(2026, 10, 17))
    assert "הפעולה הושלמה" in frame
    assert "מאשרים" not in frame and "60 שניות" not in frame, frame


def test_restart_fails_when_the_address_does_not_read_back(tmp_path, host):
    host.addresses = []
    runtime = _runtime(tmp_path)
    _restart_from_the_keyboard(runtime)
    assert not runtime.result.ok
    assert _pending(runtime) is None
    assert view.he(runtime.result.message) != runtime.result.message
    assert _journal(runtime, "dcui_management_network_restart") == [
        f"{NIC} ok=false live state mismatch"]


def test_restart_fails_when_ifup_fails(tmp_path, host):
    host.apply_error = f"{NIC}: ifup returned 1"
    runtime = _runtime(tmp_path)
    _restart_from_the_keyboard(runtime)
    assert not runtime.result.ok
    assert _pending(runtime) is None
    assert _journal(runtime, "dcui_management_network_restart") == [
        f"{NIC} ok=false apply failed"]


def test_restart_during_a_pending_change_touches_nothing(tmp_path, host):
    """A Configure still in its window keeps its marker: the restart is
    refused before the NIC is touched (as before #1537)."""
    runtime = _runtime(tmp_path)
    form = {"interface": NIC, "mode": "dhcp", "address": "",
            "netmask": "255.255.255.0", "gateway": "", "dns": ""}
    assert actions.apply_network(runtime.conn, form, tmp_path, {NIC}).ok
    marker = _pending(runtime)
    host.calls.clear()
    _restart_from_the_keyboard(runtime)
    assert not runtime.result.ok
    assert runtime.result.message == "A previous network change still awaits confirmation."
    assert host.calls == [] and _pending(runtime) == marker
