"""#2349 -- applying a DHCP config read the address back at once, before the
lease arrived, and said "no address". ``dhcpcd -b`` returns immediately; the
lease lands seconds later (#2094 measured ~7 s on ESXi).

The read-back now polls every ``DHCP_POLL_S`` up to ``DHCP_LEASE_WAIT_S``:
in the console with ``await`` (the agents, GRUB and the console share that
event loop), on the DCUI screen synchronously (its own process). Running out
of time is not success and not an instant "no address": the gap names the
seconds waited (principle 5).

No test touches a real network: write/ifup/``ip addr`` are injected, like
``dhcp_hooks`` (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from conftest import hello_body  # noqa: E402
from server.dcui import actions  # noqa: E402
from server.net import netcfg, netcfg_host  # noqa: E402

LEASE = "10.44.9.77/24"
CONF = "/api/console/net/config"
DHCP_BODY = {"mode": "dhcp", "address": "", "gateway": "", "dns": [], "confirm": "eth1"}


class Machine:
    """eth1 with no address until ``lease_after`` reads (None = never)."""

    def __init__(self, lease_after: int | None) -> None:
        self.lease_after = lease_after
        self.reads = 0
        self.now = 0.0                  # the injected monotonic clock
        self.sleeps: list[float] = []
        self.first_read = threading.Event()
        self.lease_now = threading.Event()

    def state(self) -> netcfg_host.LiveState:
        self.reads += 1
        self.first_read.set()
        leased = (self.lease_now.is_set() or
                  (self.lease_after is not None and self.reads > self.lease_after))
        return netcfg_host.LiveState(True, {"eth0": ["10.10.10.8/24"],
                                            "eth1": [LEASE] if leased else []},
                                     ["10.10.10.0/24 dev eth0"], [])

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _app(tmp_path: Path, images_root: Path, clock, machine: Machine, *, fake_time: bool):
    from server.app import create_app
    from server.console import users

    nics = [{"name": "eth0", "state": "up", "mac": "aa:00", "addresses": ["10.10.10.8/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:01", "addresses": []}]
    hooks = {
        "interfaces": lambda: [dict(n) for n in nics],
        "netcfg_read_conf": lambda name, root=None: None,
        "netcfg_write_conf": lambda name, text, root=None: None,
        "netcfg_write_resolv": lambda text, path=None: None,
        "netcfg_apply": lambda name: None,
        "netcfg_state": machine.state,
        "netcfg_sourced": lambda *a: True,
        "netcfg_timer_active": lambda *a: (True, "active"),
        "netcfg_now": clock,
        "netcfg_boot_id": lambda *a: "boot-A",
        "netcfg_local_address": lambda request: "10.10.10.8",   # the console is on eth0
        "netcfg_management": lambda: "",
        "netcfg_foreign_manager": lambda: None,
    }
    if fake_time:
        hooks.update(netcfg_sleep=machine.sleep, netcfg_monotonic=lambda: machine.now)
    dhcp_hooks = {"interfaces": hooks["interfaces"], "netcfg_management": lambda: ""}
    app = create_app(tmp_path / "data", images_root, "http://10.10.10.8:8080",
                     now_fn=clock, netcfg_hooks=hooks, dhcp_hooks=dhcp_hooks,
                     netcfg_state_dir=tmp_path / "netstate")
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    admin = TestClient(app)
    assert admin.post("/api/console/login", json={"username": "noc",
                                                  "password": "admin-pass-123"}).status_code == 200
    return app, admin


# --- the console (async) ------------------------------------------------------


def test_a_lease_that_arrives_after_a_few_reads_is_verified(tmp_path, images_root, clock):
    machine = Machine(lease_after=6)          # three seconds of empty reads
    _, admin = _app(tmp_path, images_root, clock, machine, fake_time=True)
    result = admin.put(f"{CONF}/eth1", json=DHCP_BODY).json()
    assert result["ok"] is True and result["verified"] is True, result
    assert result["mismatches"] == []
    assert result["interface"]["live_addresses"] == [LEASE]
    assert machine.reads == 7
    assert machine.sleeps == [netcfg_host.DHCP_POLL_S] * 6
    assert netcfg_host.DHCP_POLL_S >= 0.1     # CLAUDE.md: no tight polling


def test_no_lease_by_the_cap_fails_and_names_the_seconds(tmp_path, images_root, clock):
    machine = Machine(lease_after=None)
    app, admin = _app(tmp_path, images_root, clock, machine, fake_time=True)
    result = admin.put(f"{CONF}/eth1", json=DHCP_BODY).json()
    assert result["ok"] is False and result["verified"] is False
    assert result["mismatches"] == [
        f"‏eth1: לא התקבלה כתובת מ-DHCP אחרי {netcfg_host.DHCP_LEASE_WAIT_S} שניות"]
    assert machine.now >= netcfg_host.DHCP_LEASE_WAIT_S        # it did wait
    assert machine.reads > 1
    events = [r["event"] for r in app.state.ctx.conn.execute(
        "SELECT event FROM journal ORDER BY rowid")]
    assert "net_config_unverified" in events


def test_a_static_address_is_read_once_and_never_waits(tmp_path, images_root, clock):
    machine = Machine(lease_after=None)
    _, admin = _app(tmp_path, images_root, clock, machine, fake_time=True)
    body = {"mode": "static", "address": "10.44.9.11", "netmask": "255.255.255.0",
            "confirm": "eth1"}
    result = admin.put(f"{CONF}/eth1", json=body).json()
    assert result["ok"] is False                             # the fake never moves it
    # #2418: one read before the change (live-network overlap), one read-back.
    assert machine.reads == 2 and machine.sleeps == []


async def _during_the_wait(app, machine: Machine, cookies, second):
    """PUT a DHCP config; once its read-back is waiting, send ``second`` on the
    same event loop (httpx ASGITransport, as in test_async_io_1811.py). The
    lease is let in only after ``second`` is answered."""
    order: list[str] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                 cookies=cookies) as client:
        async def put():
            response = await client.put(f"{CONF}/eth1", json=DHCP_BODY)
            order.append("put")
            return response
        held = asyncio.create_task(put())
        assert await asyncio.to_thread(machine.first_read.wait, 10), "no read-back"
        answer = await asyncio.wait_for(second(client), 15)
        order.append("second")
        machine.lease_now.set()
        return order, answer, await held


def test_a_hello_is_answered_while_the_console_waits_for_a_lease(
        tmp_path, images_root, clock, monkeypatch):
    """Real ``asyncio.sleep`` and clock. A cap of 3 s bounds the test if the
    wait ever blocks the loop again: the hello then comes after the PUT."""
    monkeypatch.setattr(netcfg_host, "DHCP_LEASE_WAIT_S", 3)
    machine = Machine(lease_after=None)
    app, admin = _app(tmp_path, images_root, clock, machine, fake_time=False)
    order, hello, put = asyncio.run(_during_the_wait(
        app, machine, dict(admin.cookies),
        lambda c: c.post("/api/v1/agent/hello", json=hello_body("aa:bb:cc:00:23:49"))))
    assert order == ["second", "put"], (order, "the lease wait held the event loop")
    assert hello.status_code == 200, hello.text
    assert put.json()["ok"] is True, put.text


def test_a_second_apply_during_the_wait_is_refused_by_name(
        tmp_path, images_root, clock, monkeypatch):
    monkeypatch.setattr(netcfg_host, "DHCP_LEASE_WAIT_S", 3)
    machine = Machine(lease_after=None)
    app, admin = _app(tmp_path, images_root, clock, machine, fake_time=False)
    order, second, first = asyncio.run(_during_the_wait(
        app, machine, dict(admin.cookies),
        lambda c: c.put(f"{CONF}/eth1", json=DHCP_BODY)))
    assert second.status_code == 409, second.text
    assert "ממתינה לכתובת מ-DHCP" in second.json()["detail"]
    assert first.json()["ok"] is True
    # and once it is over, the next apply goes through
    assert admin.put(f"{CONF}/eth1", json=DHCP_BODY).json()["ok"] is True


# --- the DCUI (sync, its own process) ----------------------------------------


def _dcui_apply(tmp_path, machine: Machine):
    from server.db import connect

    conn = connect(tmp_path / "imagesrv.db")
    clock = {"now": 0.0}
    hooks = {
        "netcfg_timer_active": lambda: (True, "active"), "now": lambda: 100.0,
        "boot_id": lambda: "boot-A", "netcfg_read_conf": lambda name, root: "",
        "read_resolv": lambda: "", "netcfg_write_conf": lambda name, text, root: None,
        "netcfg_write_resolv": lambda text, path: None, "netcfg_apply": lambda name: None,
        "netcfg_state": machine.state, "hostname": lambda: "imagesrv-test",
        "sleep": lambda s: clock.__setitem__("now", clock["now"] + s),
        "monotonic": lambda: clock["now"],
    }
    form = {"interface": "eth1", "mode": "dhcp", "address": "",
            "netmask": "255.255.255.0", "gateway": "", "dns": ""}
    return actions.apply_network(conn, form, tmp_path, {"eth1"}, hooks), clock["now"]


def test_the_dcui_apply_waits_for_the_lease(tmp_path):
    machine = Machine(lease_after=4)
    result, waited = _dcui_apply(tmp_path, machine)
    assert result.ok, result
    assert machine.reads == 5 and waited == 4


def test_the_dcui_apply_without_a_lease_says_how_long_it_waited(tmp_path):
    from server.dcui.view import he

    result, waited = _dcui_apply(tmp_path, Machine(lease_after=None))
    assert not result.ok
    assert waited == netcfg_host.DHCP_LEASE_WAIT_S
    line = f"Still no match after waiting {netcfg_host.DHCP_LEASE_WAIT_S} seconds for DHCP."
    assert line in result.details
    assert he(line) != line and str(netcfg_host.DHCP_LEASE_WAIT_S) in he(line)


def test_mismatches_without_a_wait_keeps_the_old_words():
    cfg = netcfg.NetConfig("eth1", mode=netcfg.MODE_DHCP)
    state = netcfg_host.LiveState(True, {"eth1": []})
    assert netcfg_host.mismatches(cfg, state) == ["‏eth1 לא קיבל שום כתובת מ-DHCP"]
