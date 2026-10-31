"""‏#2160 — ממצאי CodeQL שאומתו כאמיתיים (מהריפו הציבורי), כל אחד עם הנתיב שלו.

1. ‏538: גיבוי ``/etc/network/interfaces`` של ``adopt_interface`` וסמן ההחזרה של
   ``netcfg_rollback`` נכתבו 0644 (ברירת המחדל של ``write_atomic`` לקובץ חדש) —
   עותק קריא לכולם של קובץ שהמנהל אולי נעל.
2. ‏532: ‏``/api/v1/agent/log`` (פורט הסוכן, בלי כניסה) החזיר את טקסט החריגה של zlib.
3. ‏267: ‏``--primary-url`` עם שם מארח השאיר ``primary_ip=None`` בשקט, וכל חילול של
   חומת האש מהשרת פתח את 8443 מכל הכרטיס, בלי ``saddr``.
"""

from __future__ import annotations

import gzip
import logging
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.agent import agent_logs, registry
from server import db
from server.net import deploy_net, netcfg, netcfg_host, netcfg_rollback

posix_modes = pytest.mark.skipif(os.name != "posix", reason="הרשאות POSIX — נבדק על ה-testrunner")

MAC = "aa:bb:cc:dd:21:60"
PRIMARY_NAME = "primary.lab.test"
PRIMARY_IP = "10.30.0.5"


# --- 538: עותקי הגדרות הרשת ------------------------------------------------------


@posix_modes
def test_the_backup_of_a_locked_interfaces_file_is_not_world_readable(tmp_path):
    main = tmp_path / "interfaces"
    main.write_text("auto eth0\niface eth0 inet dhcp\n    wpa-psk secret\n", encoding="utf-8")
    os.chmod(main, 0o600)
    text = netcfg.render(netcfg.NetConfig("eth0", mode="dhcp"), hostname="imagesrv")
    _notes, error = netcfg_host.adopt_interface("eth0", text, main=main,
                                                root=tmp_path / "interfaces.d", backup_suffix="t")
    assert error is None
    backup = tmp_path / "interfaces.pre-imagesrv.t"
    assert "wpa-psk secret" in backup.read_text(encoding="utf-8")
    assert oct(backup.stat().st_mode & 0o777) == oct(0o600)


@posix_modes
def test_the_rollback_marker_copy_of_the_network_config_is_0600(tmp_path):
    marker = netcfg_rollback.Pending(interface="eth1", deadline=1e12, armed_at="2026-10-25T10:00:00+00:00",
                                     boot="b1", files=[{"name": "eth1", "text": "iface eth1\n"}],
                                     resolv="nameserver 10.0.0.1\n")
    assert netcfg_rollback.write_pending(tmp_path, marker) is None
    path = netcfg_rollback.pending_path(tmp_path)
    assert oct(path.stat().st_mode & 0o777) == oct(0o600)
    assert netcfg_rollback.read_pending(tmp_path).files == marker.files   # עדיין נקרא


# --- 532: לוג סוכן שאינו gzip ----------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    registry.add_machine(c, MAC, "cloner-21", "grp_CLONERS", "test")
    return c


def test_a_non_gzip_agent_log_gets_a_fixed_refusal_and_the_detail_goes_to_the_log(conn, caplog):
    with caplog.at_level(logging.WARNING, logger="imagesrv.agent_logs"):
        got = agent_logs.receive(conn, MAC, None, "failed", b"not gzip at all", known=True)
    assert got == {"ok": False, "code": "bad_log", "error": "not gzip"}
    assert any("not gzip" in r.getMessage() and "header" in r.getMessage() for r in caplog.records)


def test_a_real_gzip_log_is_still_stored(conn):
    got = agent_logs.receive(conn, MAC, None, "failed", gzip.compress(b"[10:00:00] hi\n"), known=True)
    assert got["ok"] is True and got["lines"] == 1


# --- 267: כתובת הראשי בחומת האש של משני ------------------------------------------


def _main_deploy_context(tmp_path, monkeypatch, primary_url: str, resolve) -> dict:
    """מריץ את ``main`` (‏`test_console_bind.run_main`) כמשני, ומחזיר את מה שנבנה
    ל-``DeployContext`` — המקום שממנו כל חילול של חומת האש לוקח את ``primary_ip``."""
    from test_console_bind import run_main

    seen: dict = {}
    real = deploy_net.DeployContext

    def record(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    real_resolve = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == PRIMARY_NAME:
            return resolve()
        return real_resolve(host, *args, **kwargs)

    monkeypatch.setattr(deploy_net, "DeployContext", record)
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    run_main(tmp_path, monkeypatch, "--storage-role", "secondary", "--primary-url", primary_url)
    return seen


def test_a_primary_given_by_name_pins_the_firewall_to_its_resolved_address(tmp_path, monkeypatch):
    seen = _main_deploy_context(
        tmp_path, monkeypatch, f"https://{PRIMARY_NAME}:8443",
        lambda: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PRIMARY_IP, 0))])
    assert seen["primary_ip"] == PRIMARY_IP


def test_a_primary_name_that_does_not_resolve_is_a_named_reason_not_a_silent_none(tmp_path, monkeypatch):
    def fail():
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    seen = _main_deploy_context(tmp_path, monkeypatch, f"https://{PRIMARY_NAME}:8443", fail)
    assert seen["primary_ip"] is None
    assert PRIMARY_NAME in (seen.get("primary_ip_error") or "")


def test_a_primary_given_by_address_is_used_as_is(tmp_path, monkeypatch):
    seen = _main_deploy_context(tmp_path, monkeypatch, f"https://{PRIMARY_IP}:8443",
                                lambda: pytest.fail("an address is not resolved"))
    assert seen["primary_ip"] == PRIMARY_IP and not seen.get("primary_ip_error")


def _unresolved_ctx(tmp_path, hooks) -> deploy_net.DeployContext:
    ctx = deploy_net.DeployContext(
        state=deploy_net.DeployState("none", None, None), agent_port=8080,
        tftp_root=tmp_path / "tftp", repo_dir=tmp_path, servers_interface="eth0",
        primary_ip=None, hooks=hooks)
    ctx.primary_ip_error = f"השם {PRIMARY_NAME} של השרת הראשי לא נפתר לכתובת"
    return ctx


def test_deploy_completion_refuses_a_firewall_with_8443_open_to_the_whole_nic(tmp_path):
    calls: list = []
    hooks = {"write_grub_cfg": lambda url, root: None, "enable_dnsmasq": lambda: None,
             "firewall": lambda *a, **k: calls.append(("fw", k)) or None,
             "restart_server": lambda: calls.append(("restart",)) or None}
    result = deploy_net.complete(_unresolved_ctx(tmp_path, hooks), None,
                                 interface="eth1", server_ip="10.44.0.1")
    assert calls == [], "neither the firewall nor a restart may run"
    assert result["ok"] is False and result["errors"][0].startswith("חומת אש")
    assert PRIMARY_NAME in result["errors"][0]


def test_per_nic_firewall_regeneration_refuses_too(tmp_path):
    from server.app import _nic_services

    calls: list = []
    ctx_db = SimpleNamespace(conn=db.connect(tmp_path / "imagesrv.db"))
    deploy = _unresolved_ctx(tmp_path, {"firewall": lambda *a, **k: calls.append(k) or None})
    nic = _nic_services(ctx_db, "eth1", deploy, None, False, {"interfaces": lambda: []})
    error = nic.hooks["firewall"](nic)
    assert calls == [], "the generator would have been run without --primary-ip"
    assert PRIMARY_NAME in (error or "")
