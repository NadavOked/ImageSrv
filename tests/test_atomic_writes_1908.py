"""‏#1908 — מניפסט וקובצי תצורה נכתבים אטומית: כתיבה שנקטעה משאירה את הישן.

הכשל מוזרק ב-``io.open``: כל פתיחה לכתיבה בזמן ההזרקה מחזירה קובץ שכותב
**חצי** מהבייטים וזורק ``ENOSPC`` — כמו דיסק שהתמלא באמצע. גם
``Path.write_text`` וגם ``os.fdopen`` עוברים דרך ``io.open``, ולכן ההזרקה
זהה לקוד שלפני התיקון ולקוד שאחריו; ההבדל הוא רק **לאן** נכתב החצי.
"""

from __future__ import annotations

import errno
import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from server.net import deploy_net, dhcp_host, netcfg_host

OLD = "# the previous, good config\ninterface=eth1\n"
NEW = "# the new config\n" + "dhcp-range=10.0.0.10,10.0.0.200\n" * 50


class _HalfWriter:
    def __init__(self, inner):
        self._inner = inner

    def write(self, data):
        self._inner.write(data[: len(data) // 2])
        self._inner.flush()
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._inner.close()
        return False


@contextmanager
def disk_fills_mid_write(monkeypatch):
    real_open = io.open

    def faulty(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return _HalfWriter(handle) if any(c in mode for c in "wxa") else handle

    monkeypatch.setattr(io, "open", faulty)
    try:
        yield
    finally:
        monkeypatch.setattr(io, "open", real_open)


def _only_target(directory: Path, name: str) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name != name)


def test_dnsmasq_conf_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    conf = tmp_path / "imagesrv-dhcp.conf"
    conf.write_text(OLD, encoding="utf-8")
    with disk_fills_mid_write(monkeypatch):
        error = dhcp_host._write(NEW, conf)
    assert error is not None                                 # הכשל נאמר
    assert conf.read_text(encoding="utf-8") == OLD           # והישן שלם
    assert _only_target(tmp_path, conf.name) == []           # בלי שאריות tmp


def test_grub_cfg_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    cfg = tmp_path / "grub" / "grub.cfg"
    cfg.parent.mkdir()
    cfg.write_text("set default=local\n", encoding="ascii")
    with disk_fills_mid_write(monkeypatch):
        error = deploy_net.write_grub_cfg("http://10.0.0.1:8080", tmp_path)
    assert error is not None
    assert cfg.read_text(encoding="ascii") == "set default=local\n"


def test_interfaces_conf_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    path = netcfg_host.conf_path("eth1", tmp_path)
    path.write_text(OLD, encoding="utf-8")
    with disk_fills_mid_write(monkeypatch):
        error = netcfg_host.write_conf("eth1", NEW, tmp_path)
    assert error is not None
    assert path.read_text(encoding="utf-8") == OLD


def test_resolv_conf_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    path = tmp_path / "resolv.conf"
    path.write_text("nameserver 10.0.0.1\n", encoding="utf-8")
    with disk_fills_mid_write(monkeypatch):
        error = netcfg_host.write_resolv("nameserver 10.9.9.9\n" * 40, path)
    assert error is not None
    assert path.read_text(encoding="utf-8") == "nameserver 10.0.0.1\n"


def test_a_normal_write_lands_whole(tmp_path):
    conf = tmp_path / "imagesrv-dhcp.conf"
    conf.write_text(OLD, encoding="utf-8")
    assert dhcp_host._write(NEW, conf) is None
    assert conf.read_text(encoding="utf-8") == NEW
    assert _only_target(tmp_path, conf.name) == []


# --- המניפסט של קליטה ---------------------------------------------------------

pytest.importorskip("fastapi")


def test_a_capture_manifest_cut_halfway_never_appears_as_manifest_json(server, monkeypatch):
    from fastapi.testclient import TestClient

    from server.library.capture import staging_dir
    from test_capture import (PART_A, PART_B, make_task, manifest_for,
                              setup_build_machine, task_token)
    from server.core.tasks import TOKEN_HEADER

    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    head = {TOKEN_HEADER: task_token(server, created["id"])}
    client = TestClient(server["app"], raise_server_exceptions=False)
    for name, data in {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B}.items():
        assert client.put(f"/api/v1/capture/{created['id']}/files/{name}",
                          content=data, headers=head).status_code == 200
    body = json.dumps(manifest_for()).encode()
    with disk_fills_mid_write(monkeypatch):
        response = client.put(f"/api/v1/capture/{created['id']}/manifest",
                              content=body, headers=head)
    assert response.status_code >= 400
    folder = staging_dir(server["ctx"].library.root, created["id"])
    # מניפסט חתוך בשם manifest.json הוא "כאן אימג'" שקר — עיקרון 3.
    assert not (folder / "manifest.json").exists()
