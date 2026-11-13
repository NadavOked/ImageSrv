"""#2267 — שרת פיתוח בווינדוס יצר את `C:\\etc\\imagesrv` ואת `C:\\etc\\dnsmasq.d`.

`server.main` מעביר ל-`create_runtime` את `known_macs_hooks` (#141) ואת
`sync_dnsmasq=True` (#1013) תמיד, ו-`dhcp_host._write` עושה
`path.parent.mkdir(parents=True)` על `/etc/imagesrv/known-macs` ועל
`/etc/dnsmasq.d/imagesrv-dhcp.conf` — בווינדוס זה `C:\\etc\\…`. systemctl נכשל
והקובץ נמחק, אבל התיקיות נשארות.

התיקון: שני הכותבים האלה מודלקים רק כשהשרת רץ על לינוקס — מערכת ההפעלה של
השרת (דביאן 13). על לינוקס הכול **בדיוק** כמו קודם, כולל כשל גלוי ביומן
(עיקרון 5); הטסטים כאן מוכיחים את שני הצדדים.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import server.app
import server.main
from server.net import dhcp_host

#: the real one, before any test replaces it with a recorder
REAL_CREATE_RUNTIME = server.app.create_runtime


def _run_main(tmp_path: Path, monkeypatch, platform: str) -> dict:
    import asyncio
    import uvicorn

    seen: dict = {}

    def fake_create_runtime(*args, **kwargs):
        seen.update(kwargs)
        return "rt"

    # only server.main sees the other OS -- not uvicorn, ssl or pathlib under it
    fake_sys = types.ModuleType("sys")
    fake_sys.__dict__.update(vars(sys))
    fake_sys.platform = platform
    monkeypatch.setattr(server.main, "sys", fake_sys)
    monkeypatch.setattr(server.app, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: None)
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://127.0.0.1:8199",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
    ])
    server.main.main()
    return seen


def test_main_on_linux_hands_create_runtime_both_writers(tmp_path, monkeypatch):
    """The production side: the same two arguments as before #2267."""
    seen = _run_main(tmp_path, monkeypatch, "linux")
    assert seen["known_macs_hooks"] == {"apply": dhcp_host.apply_known_macs}
    assert seen["sync_dnsmasq"] is True


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_main_on_a_dev_station_hands_it_neither(tmp_path, monkeypatch, capsys, platform):
    seen = _run_main(tmp_path, monkeypatch, platform)
    assert seen["known_macs_hooks"] is None
    assert seen["sync_dnsmasq"] is False
    # said, not silent: whoever runs the dev server sees why /etc is untouched
    assert "#2267" in capsys.readouterr().out


# --- create_runtime itself, with what main hands it ---------------------------------


@pytest.fixture()
def writes(monkeypatch) -> list[Path]:
    """Every file dhcp_host would write (and nothing written, no systemctl)."""
    written: list[Path] = []
    monkeypatch.setattr(dhcp_host, "_write", lambda text, path: written.append(Path(path)))
    monkeypatch.setattr(dhcp_host, "_systemctl", lambda action, unit: None)
    return written


def _runtime(tmp_path: Path, monkeypatch, platform: str):
    """The real create_runtime, with the host writers main hands it on <platform>."""
    seen = _run_main(tmp_path / "main", monkeypatch, platform)
    return REAL_CREATE_RUNTIME(tmp_path / "data", tmp_path / "img", "http://127.0.0.1:8199",
                               known_macs_hooks=seen["known_macs_hooks"],
                               sync_dnsmasq=seen["sync_dnsmasq"])


def test_create_runtime_on_a_dev_station_writes_nothing_outside_tmp(tmp_path, monkeypatch, writes):
    rt = _runtime(tmp_path, monkeypatch, "win32")
    try:
        assert writes == []
    finally:
        rt.conn.close()


def test_create_runtime_on_linux_still_writes_known_macs(tmp_path, monkeypatch, writes):
    """Principle 5: the real server keeps writing /etc/imagesrv/known-macs at start."""
    rt = _runtime(tmp_path, monkeypatch, "linux")
    try:
        assert Path(dhcp_host.KNOWN_MACS_CONF) in writes, writes
    finally:
        rt.conn.close()
