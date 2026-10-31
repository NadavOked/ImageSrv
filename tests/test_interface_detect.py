"""‏#514 — זיהוי ממשק השידור מבחין בין תחנת פיתוח (אין `ip`) לבין שרת
לינוקס אמיתי שאי אפשר לזהות לו ממשק. הראשון מחזיר None (השידור מזויף
ממילא); השני נכשל בגלוי, כדי ש-udp-sender לא ישדר לכרטיס הלא נכון."""

from __future__ import annotations

import json
import subprocess

import pytest

from server.main import (InterfaceDetectionError, _address_for_interface,
                         _interface_for)

_IP_JSON = json.dumps([
    {"ifname": "lo", "addr_info": [{"local": "127.0.0.1"}]},
    {"ifname": "eth0", "addr_info": [{"local": "10.44.0.1"}]},
    {"ifname": "eth1", "addr_info": [{"local": "192.168.1.5"}]},
])


def _fake_run(stdout):
    def run(*a, **k):
        return subprocess.CompletedProcess(a[0], 0, stdout=stdout, stderr="")
    return run


def test_matching_address_returns_its_interface(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_IP_JSON))
    assert _interface_for("http://10.44.0.1:8080") == "eth0"


def test_no_ip_command_is_a_dev_station_none(monkeypatch):
    # FileNotFoundError = אין בינארי `ip` — לא לינוקס. None לגיטימי.
    def boom(*a, **k):
        raise FileNotFoundError("ip")
    monkeypatch.setattr(subprocess, "run", boom)
    assert _interface_for("http://10.44.0.1:8080") is None


def test_ip_ran_but_no_nic_matches_fails_loudly(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_IP_JSON))
    with pytest.raises(InterfaceDetectionError):
        _interface_for("http://10.99.99.99:8080")


def test_ip_present_but_failed_fails_loudly(monkeypatch):
    # ‏CalledProcessError = `ip` קיים ורץ ונכשל — שרת אמיתי, לא None שקט.
    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "ip")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(InterfaceDetectionError):
        _interface_for("http://10.44.0.1:8080")


def test_hostname_url_is_resolved_before_matching(monkeypatch):
    import socket as _socket
    monkeypatch.setattr(subprocess, "run", _fake_run(_IP_JSON))
    monkeypatch.setattr(
        _socket, "getaddrinfo",
        lambda host, port: [(None, None, None, "", ("10.44.0.1", 0))],
    )
    assert _interface_for("http://imagesrv.lab:8080") == "eth0"


def test_ip_present_but_malformed_json_fails_loudly(monkeypatch):
    # ‏`ip` יצא 0 אבל הפלט אינו JSON — שרת אמיתי, לא None שקט (Codex #2).
    monkeypatch.setattr(subprocess, "run", _fake_run('{"truncated'))
    with pytest.raises(InterfaceDetectionError):
        _interface_for("http://10.44.0.1:8080")


def test_address_for_interface_returns_ipv4(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_IP_JSON))
    assert _address_for_interface("eth0") == "10.44.0.1"


def test_address_for_missing_interface_fails_loudly(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(_IP_JSON))
    with pytest.raises(InterfaceDetectionError):
        _address_for_interface("eth9")


def test_address_for_interface_without_ip_cmd_is_none(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ip")
    monkeypatch.setattr(subprocess, "run", boom)
    assert _address_for_interface("eth0") is None
