"""#2155 section 2 (rc5, 25/10): the first frames of first boot are not red.

setup-boot-server starts the DCUI before the management NIC is brought up
(firstboot-answers restarts networking.service after it) and before the
firewall (install_firewall is its last step). For about one refresh the DCUI
showed "management network cable disconnected" and the firewall "not
running" in red -- Hyper-V 01:09:06 (`12b`), ESXi 01:09:47 (`12b`); five
seconds later both were green (`12c`), first boot still running.

The contract:

* while firstboot.status says ``state=installing`` -- and only for
  ``FIRST_BOOT_GRACE_S`` from the first time the DCUI saw it -- a NIC without
  link/address and a service that is not active are "starting" (neutral),
  not a red problem, and the server is not called "fine" either;
* ``failed`` is still red, and "could not check" is still "not checked"
  (principle 5): only the states first boot is known to pass through;
* past the cap, or once first boot ended (done, or a named failure), the
  same machine state is red again.

The snapshot comes from ``collect_snapshot`` over files and a fake runner,
and the screen from ``_main_body``, so the same test runs on the code before
the fix (the negative control) and fails there on what it shows.
"""

from __future__ import annotations

import dataclasses
import json
import socket
from datetime import datetime
from pathlib import Path

from server.dcui import data, view
from server.dcui.data import CommandResult, collect_snapshot
from server.dcui.view import Extras

TODAY = datetime(2026, 10, 25)
RC5 = {"imagesrv-server": "active", "dnsmasq": "inactive", "nftables": "inactive"}


def _clock(monkeypatch, start: float = 1000.0) -> dict:
    clock = {"now": start}
    monkeypatch.setattr(data, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(data, "_installing_since", [], raising=False)
    monkeypatch.setattr(data, "_cert_unread_since", [])
    return clock


def _closed_port() -> tuple[str, int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()


def _snapshot(tmp_path: Path, monkeypatch, state: str, services: dict | None = None,
              link: str = "down", ip_fails: bool = False):
    """The rc5 first frame (Hyper-V `12b`) as the DCUI collects it: ens33 with no
    link and no address, the firewall not started yet."""
    base = tmp_path / f"{state}-{len(list(tmp_path.iterdir())) if tmp_path.exists() else 0}"
    base.mkdir(parents=True)
    status = base / "firstboot.status"
    status.write_text(f"state={state}\nservers_if=ens33\n", encoding="utf-8")
    (base / "data").mkdir()
    nic = base / "net" / "ens33"
    nic.mkdir(parents=True)
    for name, value in (("address", "00:15:5d:00:00:01"), ("operstate", link), ("speed", "-1")):
        (nic / name).write_text(value, encoding="ascii")
    states = {**RC5, **(services or {})}

    def runner(argv):
        if argv[:4] == ["ip", "-j", "address", "show"]:
            if ip_fails:
                return CommandResult(1, "", "ip: failed")
            return CommandResult(0, json.dumps([{"ifname": "ens33", "operstate": link.upper(),
                                                 "addr_info": []}]))
        if argv[:2] == ["systemctl", "is-active"]:
            return CommandResult(0, states[argv[2]] + "\n")
        if argv[:2] == ["systemctl", "is-enabled"]:
            return CommandResult(0, "enabled\n")
        return CommandResult(1, "", "not here")

    monkeypatch.setattr(data, "CONSOLE_LOCAL", _closed_port())
    return collect_snapshot(base / "data", runner=runner, sys_net=base / "net",
                            status_file=status, override_file=base / "none.conf",
                            repo_dir=base / "repo")


def _screen(snap):
    frame = view.Frame("main")
    view._main_body(frame, snap, Extras(), TODAY)
    return frame.lines


def _row(lines, label: str) -> tuple[str, str]:
    """(pill kind, pill) of the row named ``label``."""
    line = next(line for line in lines if line[0] == "row" and line[1] == label)
    return line[6], line[7]


def _titles(snap) -> list[str]:
    return [p.title for p in view.problems(snap, Extras(), TODAY)]


# --- first boot: starting, not red -------------------------------------------------

def test_first_boot_nic_without_link_is_starting_not_a_red_cable_problem(tmp_path, monkeypatch):
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "installing")
    assert _row(_screen(snap), "רשת ניהול") == ("info", "עולה")
    assert not any("רשת הניהול" in title for title in _titles(snap)), _titles(snap)


def test_first_boot_firewall_not_yet_started_is_starting_not_red(tmp_path, monkeypatch):
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "installing")
    assert _row(_screen(snap), "nftables") == ("info", "עולה")


def test_first_boot_server_between_restarts_is_not_a_red_problem(tmp_path, monkeypatch):
    """ESXi rc5: imagesrv-server "deactivating" -- firstboot-answers restarts
    it once the NIC has its address (#2095)."""
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "installing",
                     services={"imagesrv-server": "deactivating"})
    assert _row(_screen(snap), "imagesrv-server")[0] != "bad"
    assert not any("imagesrv-server" in title for title in _titles(snap)), _titles(snap)


def test_first_boot_with_nothing_else_wrong_does_not_claim_the_server_is_fine(tmp_path, monkeypatch):
    """No problem left (an unpaired secondary needs no deploy network), and
    still not "השרת תקין": starting is a third state."""
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "installing")
    snap = dataclasses.replace(snap, storage_role="secondary")
    assert _titles(snap) == []
    assert view.main_state(snap, Extras(), TODAY) == ("info", "התקנה בתהליך")
    hero = next(line for line in _screen(snap) if line[0] == "hero")
    assert (hero[1], hero[2]) == ("wait", "ההתקנה עדיין לא הסתיימה"), hero


# --- the guards: what stays red ----------------------------------------------------

def test_after_first_boot_the_same_machine_is_red(tmp_path, monkeypatch):
    """The guard against over-fixing: the rc5 frame once first boot is done."""
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "done")
    lines = _screen(snap)
    assert _row(lines, "רשת ניהול") == ("bad", "מנותק")
    assert _row(lines, "nftables") == ("bad", "לא פועל")
    assert any("רשת הניהול" in title for title in _titles(snap)), _titles(snap)


def test_a_named_first_boot_failure_is_red(tmp_path, monkeypatch):
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "installer-failed")
    assert _row(_screen(snap), "nftables")[0] == "bad"


def test_past_the_cap_installing_is_red_again(tmp_path, monkeypatch):
    """A first boot that hangs (systemd killed it: no `fail`, the status stays
    installing) must not stay neutral forever."""
    clock = _clock(monkeypatch)
    _snapshot(tmp_path, monkeypatch, "installing")
    clock["now"] += getattr(data, "FIRST_BOOT_GRACE_S", 0)
    late = _snapshot(tmp_path, monkeypatch, "installing")
    assert _row(_screen(late), "רשת ניהול") == ("bad", "מנותק")
    assert _row(_screen(late), "nftables") == ("bad", "לא פועל")
    clock["now"] += 5
    again = _snapshot(tmp_path, monkeypatch, "installing")
    assert _row(_screen(again), "nftables")[0] == "bad", "the cap was measured again"


def test_within_the_cap_installing_is_still_starting(tmp_path, monkeypatch):
    clock = _clock(monkeypatch)
    _snapshot(tmp_path, monkeypatch, "installing")
    clock["now"] += getattr(data, "FIRST_BOOT_GRACE_S", 1) - 1
    snap = _snapshot(tmp_path, monkeypatch, "installing")
    assert _row(_screen(snap), "nftables") == ("info", "עולה")


def test_a_failed_service_is_red_even_during_first_boot(tmp_path, monkeypatch):
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "installing",
                     services={"nftables": "failed", "imagesrv-server": "failed"})
    assert _row(_screen(snap), "nftables") == ("bad", "נכשל")
    assert any("imagesrv-server" in title for title in _titles(snap)), _titles(snap)


def test_unreadable_addresses_stay_not_checked_during_first_boot(tmp_path, monkeypatch):
    _clock(monkeypatch)
    snap = _snapshot(tmp_path, monkeypatch, "installing", ip_fails=True)
    assert _row(_screen(snap), "רשת ניהול") == ("warn", "לא נבדק")
    assert "לא הצלחנו לקרוא את הכתובות של כרטיסי הרשת" in _titles(snap)
