"""#2840: one save of the deploy network = one apply.

Work server, v0.91.10, 11/11 (journal + DB copied read-only to the testrunner,
``/work/ws1111``): one save from the console window became 18
``PUT /net/config/enp2s0/deploy`` from the browser (six keep-alive ports of
10.0.0.3) between 11:05:11 and 11:05:23, 15 of them answered 200, and each 200
restarted dnsmasq -- 15 restarts in four seconds, then ``dhcp_apply_failed``.

Three things let that through, and each has a test here:

1. the first save armed the 60-second rollback and answered "awaits
   confirmation -- the DHCP was not changed". Its address was already stored,
   so for every later save ``netcfg.changed`` was empty, the guard in
   ``_configure`` never ran, and the DHCP went on over an address that the
   rollback removed a minute later (``net_rollback reason=expired``);
2. nothing on the server refused a save while another one was running;
3. a failed ``ifup`` said "(code 1):" and nothing else -- the reason
   ("ifup: unknown interface enp2s0") was only in the unit's journal.

(The browser side -- the window's button -- is in windows_frame.test.cjs.)
Nothing here touches the machine: every hook is injected (CLAUDE.md).
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")

from server.net import netcfg_host  # noqa: E402

from test_server_netcfg import (  # noqa: E402,F401 -- net_server is a fixture
    confirm, dhcp_setting, journal_events, net_server, put_deploy)


def _saved(server) -> int:
    return sum(1 for event, _ in journal_events(server) if event == "net_deploy_saved")


def test_a_save_while_the_address_awaits_confirmation_does_not_turn_the_dhcp_on(net_server):
    """The incident's order: the first save arms the rollback; every save after it,
    before anyone confirmed, is refused -- the DHCP is not committed and dnsmasq
    is not touched. After the confirmation one save = one apply."""
    fake = net_server["fake"]
    fake["local"] = ""              # the console's card is unknown -> the change arms #56
    first = put_deploy(net_server)
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["dhcp"] is None and out["address"]["rollback"]["pending"] is True, out
    assert "ממתינה לאישור" in out["apply_error"]

    for _ in range(17):             # the 17 PUTs that followed it on the work server
        again = put_deploy(net_server)
        assert again.status_code == 409, again.text
        assert "ממתין לאישור" in again.json()["detail"]
    assert fake["dnsmasq"] == [], "dnsmasq was rewritten/restarted for a refused save"
    assert dhcp_setting(net_server).enabled is False
    assert _saved(net_server) == 0

    assert confirm(net_server).status_code == 200
    done = put_deploy(net_server)
    assert done.status_code == 200, done.text
    assert done.json()["dhcp"]["ok"] is True
    assert len(fake["dnsmasq"]) == 1 and _saved(net_server) == 1


def test_an_expired_rollback_after_the_storm_leaves_nothing_of_the_deploy_side_behind(net_server):
    """The work server after 11:06:12: the rollback put enp2s0 back (no file, manual),
    but `dhcp:enp2s0` and `deploy:url` stayed on 20.0.0.1 -- a deploy network that a
    reboot would lose. With the saves refused while the change awaits confirmation,
    the rollback has nothing on the deploy side to undo: all of it, or none of it."""
    from server.db import get_setting
    from server.net import netcfg, netcfg_rollback

    fake, conn = net_server["fake"], net_server["ctx"].conn
    fake["local"] = ""
    assert put_deploy(net_server).json()["address"]["rollback"]["pending"] is True
    for _ in range(17):
        assert put_deploy(net_server).status_code == 409
    reason = netcfg_rollback.run_once(net_server["state_dir"], net_server["hooks"],
                                      now=net_server["clock"]() + 61, boot="boot-A")
    assert reason == "expired"
    assert net_server["admin"].get("/api/console/net/config").status_code == 200  # drains the crumb
    assert "eth1" not in fake["confs"], "the card's file is back to none"
    stored = netcfg.NetConfig.from_json("eth1", get_setting(conn, netcfg.SETTING_PREFIX + "eth1"))
    assert stored.mode == netcfg.MODE_MANUAL and not stored.address
    # and the deploy side never moved: nothing for a reboot to lose
    assert dhcp_setting(net_server).enabled is False
    assert fake["dnsmasq"] == [] and _saved(net_server) == 0
    assert not get_setting(conn, "deploy:url") and not get_setting(conn, "deploy:interface")


def test_a_second_save_while_the_first_is_still_running_is_refused_not_queued(net_server):
    """Two saves at once (two tabs, or a request the browser sent while the first
    was running): the second gets 409 by name and applies nothing. Each request runs
    on its own event loop thread, so the first really is in the middle of its apply
    when the second arrives."""
    fake = net_server["fake"]
    inside, release = threading.Event(), threading.Event()

    class SlowDnsmasq(list):
        """The fixture's dnsmasq apply hook appends here: the first apply waits."""
        def append(self, text):
            super().append(text)
            if len(self) == 1:
                inside.set()
                release.wait(10)

    fake["dnsmasq"] = SlowDnsmasq()
    first: dict = {}
    worker = threading.Thread(target=lambda: first.setdefault("r", put_deploy(net_server)))
    worker.start()
    try:
        assert inside.wait(10), "the first save never reached the dnsmasq apply"
        second = put_deploy(net_server)
    finally:
        release.set()
        worker.join(10)
    assert second.status_code == 409, second.text
    assert "שמירה קודמת של רשת ההפצה" in second.json()["detail"]
    assert first["r"].status_code == 200, first["r"].text
    assert len(fake["dnsmasq"]) == 1, f"one save = one apply of dnsmasq, got {len(fake['dnsmasq'])}"
    assert _saved(net_server) == 1
    # and the lock is released: the next deliberate save goes through
    assert put_deploy(net_server, range_end="10.44.20.150").status_code == 200


# --- 3: a failed ifup says why ---------------------------------------------------


def _host(monkeypatch, results: dict):
    calls: list[list[str]] = []

    def run(cmd, timeout=40):
        calls.append(list(cmd))
        key = "systemctl stop" if cmd[:2] == ["systemctl", "stop"] else cmd[0]
        answer = results.get(key, (0, ""))
        return answer.pop(0) if isinstance(answer, list) else answer

    monkeypatch.setattr(netcfg_host, "_run", run)
    monkeypatch.setattr(netcfg_host, "JOURNAL_LAG_S", 0, raising=False)   # absent before #2840 -- the control must fail on behaviour
    return calls


def test_a_failed_ifup_carries_what_ifup_itself_wrote(monkeypatch):
    """The work server's rollback: "(קוד 1):  —" and nothing else. Now the unit's
    own output is in the message that reaches the console journal / the window."""
    calls = _host(monkeypatch, {
        "systemd-run": (1, ""),
        "journalctl": (0, "ifup: unknown interface enp2s0\n"),
    })
    error = netcfg_host.apply_interface("enp2s0")
    assert error is not None
    assert "imagesrv-ifup-enp2s0.service" in error and "(קוד 1)" in error
    assert "ifup: unknown interface enp2s0" in error, error
    assert "journalctl -u imagesrv-ifup-enp2s0.service" in error
    journal = next(c for c in calls if c[0] == "journalctl")
    # only what the process wrote, not systemd's Starting/Failed lines about the unit
    assert "_SYSTEMD_UNIT=imagesrv-ifup-enp2s0.service" in journal and "-u" not in journal


def test_an_ifup_output_that_could_not_be_read_is_not_an_empty_output(monkeypatch):
    _host(monkeypatch, {"systemd-run": (1, ""), "journalctl": (1, "Failed to open journal")})
    error = netcfg_host.apply_interface("enp2s0")
    assert "לא נקרא" in error and "Failed to open journal" in error


def test_an_empty_journal_is_read_again_once_and_then_said_by_name(monkeypatch):
    calls = _host(monkeypatch, {"systemd-run": (1, ""), "journalctl": [(0, ""), (0, "")]})
    error = netcfg_host.apply_interface("enp2s0")
    assert sum(1 for c in calls if c[0] == "journalctl") == 2
    assert "אין (עדיין) פלט" in error


def test_a_successful_ifup_reads_no_journal(monkeypatch):
    calls = _host(monkeypatch, {})
    assert netcfg_host.apply_interface("enp2s0") is None
    assert not [c for c in calls if c[0] == "journalctl"]


# --- #2853: known_macs on a dnsmasq that is off on purpose -----------------------


class _Done:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _systemctl(monkeypatch, answers: dict):
    """``answers[action]`` = a _Done or an exception; every command is recorded."""
    from server.net import dhcp_host
    calls: list[list[str]] = []

    def run(cmd, *a, **k):
        calls.append(list(cmd))
        answer = answers.get(cmd[1], _Done(0))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(dhcp_host.subprocess, "run", run)
    return calls


NOT_ACTIVE = _Done(1, err="dnsmasq.service is not active, cannot reload.")


def test_known_macs_on_a_dnsmasq_that_is_off_on_purpose_is_written_and_not_a_failure(
        tmp_path, monkeypatch):
    """Work server 11:01:36: dnsmasq stopped by the installer until the deploy network
    exists (#1088), `reload` rc=1, known_macs_apply_failed twice, the new file reverted."""
    from server.net import dhcp_host
    conf = tmp_path / "known-macs"
    conf.write_text("aa:bb:cc:dd:ee:01,set:known\n", encoding="utf-8")
    calls = _systemctl(monkeypatch, {"reload": NOT_ACTIVE, "is-active": _Done(3, "inactive\n")})
    assert dhcp_host.apply_known_macs("aa:bb:cc:dd:ee:02,set:known\n", conf) is None
    assert conf.read_text(encoding="utf-8") == "aa:bb:cc:dd:ee:02,set:known\n", \
        "the file dnsmasq reads when it starts is the new one"
    assert [c[1] for c in calls] == ["reload", "is-active"], calls


@pytest.mark.parametrize("state", [
    _Done(0, "active\n"),          # running and the reload failed: a real failure
    _Done(3, "failed\n"),          # crashed: not "off on purpose"
    _Done(1, ""),                  # no answer read
    OSError("systemctl missing"),  # the check itself did not run
])
def test_known_macs_reload_that_failed_on_anything_but_inactive_is_still_a_failure(
        tmp_path, monkeypatch, state):
    from server.net import dhcp_host
    conf = tmp_path / "known-macs"
    conf.write_text("aa:bb:cc:dd:ee:01,set:known\n", encoding="utf-8")
    _systemctl(monkeypatch, {"reload": NOT_ACTIVE, "is-active": state})
    assert dhcp_host.apply_known_macs("aa:bb:cc:dd:ee:02,set:known\n", conf) is not None
    assert conf.read_text(encoding="utf-8") == "aa:bb:cc:dd:ee:01,set:known\n"


# --- the console answers while a save runs ---------------------------------------
#
# Work server 11:05:16-19: "who already answers" and the install completion ran
# synchronously inside the `async def` endpoint and held the event loop -- the
# whole console (and the agents, and GRUB) stopped answering, and the browser
# queued the clicks. TestClient gives every request its own loop and cannot show
# that, so these drive one loop through httpx's ASGI transport.


def _answers_while_saving(server, hold: str) -> list[str]:
    """Start a deploy save that blocks inside ``hold`` ("links": the link raise in the
    gates, "dnsmasq": the apply in the commit), ask /health/live meanwhile, return the
    order in which the two finished."""
    import asyncio
    import time

    import httpx

    fake = server["fake"]
    inside, release, order = threading.Event(), threading.Event(), []

    class Held(list):
        def append(self, item):
            super().append(item)
            if len(self) == 1:
                inside.set()
                release.wait(3)          # never forever: the old code would hang the loop here
                order.append("save-step-done")

    fake[hold] = Held()

    async def main():
        transport = httpx.ASGITransport(app=server["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                     cookies=server["admin"].cookies) as client:
            save = asyncio.create_task(client.put(
                "/api/console/net/config/eth1/deploy",
                json={"network": "10.44.20.0", "netmask": "255.255.255.0",
                      "range_start": "10.44.20.50", "range_end": "10.44.20.200",
                      "lease": "12h", "confirm": "eth1"}))
            deadline = time.monotonic() + 5
            while not inside.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert inside.is_set(), "the save never reached the held step"
            live = await client.get("/api/console/health/live")
            order.append(f"health-{live.status_code}")
            release.set()
            done = await save
            assert done.status_code == 200, done.text

    asyncio.run(main())
    return order


def test_the_console_answers_while_the_who_answers_check_runs(net_server):
    nic = next(n for n in net_server["fake"]["interfaces"] if n["name"] == "eth1")
    nic.update(state="down", admin_up=False, link_up=None, addresses=[])   # #2805: gates raise it
    order = _answers_while_saving(net_server, "links")
    assert order[:2] == ["health-200", "save-step-done"], \
        f"/health/live waited for the gates: {order}"


def test_the_console_answers_while_dnsmasq_is_applied(net_server):
    order = _answers_while_saving(net_server, "dnsmasq")
    assert order[:2] == ["health-200", "save-step-done"], \
        f"/health/live waited for the commit: {order}"
