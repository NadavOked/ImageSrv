"""‏#1943 — שאריות #1908: כל קובץ שהשרת מחליף נכתב ב-``write_atomic``.

שתי שאלות לכל אתר, כל אחת בטסט משלה:

* **כתיבה שנקטעה** (קבוצה א' ב-#1943 — כתיבה במקום): ‏``io.open`` מוזרק
  כך שהכתיבה לקובץ **הזה בלבד** כותבת חצי וזורקת ``ENOSPC``. הישן נשאר
  שלם, ואין קובץ זמני יתום לידו.
* **עמידות** (קבוצה ב' — tmp + ‏``os.replace`` בלי ``fsync``): ‏``os.fsync``
  נרשם; הבייטים שעל הדיסק בנתיב אחרי הכתיבה הם inode שעבר ``fsync``, ובפוסיקס
  גם התיקייה שלו (ה-rename). ‏tmp+replace בלי fsync "אטומי" מול קריסה, אבל
  נפילת חשמל שניות אחריו יכולה להשאיר קובץ ריק (ext4 delayed allocation).

ההזרקה **סלקטיבית לפי שם** כי בחלק מהאתרים יש לפני הכתיבה הזו כתיבה אחרת
(מפתח לפני תעודה, ‏state.json לפני המניפסט). ‏``write_atomic`` פותח את הקובץ
הזמני דרך ``tempfile.mkstemp`` + ‏``os.fdopen`` — ה-fd ממופה חזרה לשם דרך
``mkstemp`` עטוף.
"""

from __future__ import annotations

import io
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import auto_reboot
from server.library import images
from server.net import netcfg_rollback
from server.storage import storage_locations
from server.interserver import storage_nodes
from server.vm import vmupdate
from server.db import connect
from server.wizard.core import WizardState
from test_atomic_writes_1908 import _HalfWriter

OLD = "the previous, good content\n"


@contextmanager
def torn_write(monkeypatch, name: str):
    """כתיבה לקובץ ששמו מכיל ``name`` (או לקובץ זמני של ``mkstemp`` ששמו
    מכיל אותו) כותבת חצי וזורקת ENOSPC. כל השאר — כרגיל."""
    real_open, real_mkstemp = io.open, tempfile.mkstemp
    names: dict[int, str] = {}

    def mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        names[fd] = path
        return fd, path

    def faulty(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        path = names.get(file, "") if isinstance(file, int) else os.fspath(file)
        if any(c in mode for c in "wxa") and name in os.path.basename(str(path)):
            return _HalfWriter(handle)
        return handle

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(io, "open", faulty)
    try:
        yield
    finally:
        monkeypatch.setattr(io, "open", real_open)
        monkeypatch.setattr(tempfile, "mkstemp", real_mkstemp)


@pytest.fixture()
def fsynced(monkeypatch):
    """(st_dev, st_ino) של כל fd שעבר ``os.fsync``."""
    real = os.fsync
    seen: set[tuple[int, int]] = set()

    def spy(fd):
        st = os.fstat(fd)
        seen.add((st.st_dev, st.st_ino))
        return real(fd)

    monkeypatch.setattr(os, "fsync", spy)
    return seen


def assert_durable(path: Path, seen: set) -> None:
    st = os.stat(path)
    assert (st.st_dev, st.st_ino) in seen, f"{path.name}: the bytes on disk were never fsynced"
    if os.name == "posix":
        d = os.stat(path.parent)
        assert (d.st_dev, d.st_ino) in seen, f"{path.name}: the rename was never fsynced (directory)"


def leftovers(directory: Path, *keep: str) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name not in keep)


# --- storage_nodes: ‏server.crt של זהות השרת בין אתרים -------------------------

def test_interserver_cert_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    conn = connect(tmp_path / "imagesrv.db")
    cert = tmp_path / storage_nodes.INTERSERVER_DIRNAME / "server.crt"
    cert.parent.mkdir()
    cert.write_text(OLD, encoding="utf-8")
    with torn_write(monkeypatch, "server.crt"), pytest.raises(OSError):
        storage_nodes.ensure_identity(conn, tmp_path, host_sans=("10.0.0.1",))
    assert cert.read_text(encoding="utf-8") == OLD
    assert leftovers(cert.parent, "server.crt") == []


def test_interserver_cert_is_durable(tmp_path, fsynced):
    conn = connect(tmp_path / "imagesrv.db")
    storage_nodes.ensure_identity(conn, tmp_path, host_sans=("10.0.0.1",))
    assert_durable(tmp_path / storage_nodes.INTERSERVER_DIRNAME / "server.crt", fsynced)


# --- console_tls: תעודת הקונסולה וסמן טביעת האצבע ------------------------------

def test_console_cert_and_marker_are_durable_on_first_issue(tmp_path, fsynced):
    from server.console import console_tls
    console_tls.ensure_console_cert(tmp_path, "10.0.0.1", hostname="imgsrv")
    directory = tmp_path / console_tls.CONSOLE_TLS_DIRNAME
    assert_durable(directory / console_tls.CERT_NAME, fsynced)
    assert_durable(directory / console_tls.GENERATED_MARKER, fsynced)


def test_console_cert_reissue_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    from server.console import console_tls
    console_tls.ensure_console_cert(tmp_path, "10.0.0.1", hostname="imgsrv")
    directory = tmp_path / console_tls.CONSOLE_TLS_DIRNAME
    before = (directory / console_tls.CERT_NAME).read_bytes()
    with torn_write(monkeypatch, console_tls.CERT_NAME + "."), pytest.raises(OSError):
        console_tls.reissue_self_signed(tmp_path, "imgsrv2")
    assert (directory / console_tls.CERT_NAME).read_bytes() == before
    assert not [n for n in os.listdir(directory) if n.endswith(".tmp")], os.listdir(directory)


def test_console_cert_reissue_is_durable(tmp_path, fsynced):
    from server.console import console_tls
    console_tls.ensure_console_cert(tmp_path, "10.0.0.1", hostname="imgsrv")
    fsynced.clear()
    console_tls.reissue_self_signed(tmp_path, "imgsrv2")
    directory = tmp_path / console_tls.CONSOLE_TLS_DIRNAME
    assert_durable(directory / console_tls.CERT_NAME, fsynced)
    assert_durable(directory / console_tls.GENERATED_MARKER, fsynced)


# --- netcfg_rollback: סמן ההחזרה של שינוי רשת, והפירור --------------------------

def _pending() -> netcfg_rollback.Pending:
    return netcfg_rollback.Pending(interface="eth1", deadline=1e12, armed_at="2026-10-24T10:00:00+00:00",
                                   boot="b1", files=[{"name": "eth1", "text": "iface eth1\n"}],
                                   resolv=None)


def test_netcfg_pending_marker_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    path = netcfg_rollback.pending_path(tmp_path)
    path.write_text(OLD, encoding="utf-8")
    with torn_write(monkeypatch, netcfg_rollback.PENDING_NAME):
        error = netcfg_rollback.write_pending(tmp_path, _pending())
    assert error is not None
    assert path.read_text(encoding="utf-8") == OLD
    assert leftovers(tmp_path, path.name) == []


def test_netcfg_pending_marker_is_durable(tmp_path, fsynced):
    assert netcfg_rollback.write_pending(tmp_path, _pending()) is None
    assert_durable(netcfg_rollback.pending_path(tmp_path), fsynced)


def test_a_rollback_crumb_cut_halfway_leaves_no_torn_crumb(tmp_path, monkeypatch):
    with torn_write(monkeypatch, netcfg_rollback.CRUMB_SUFFIX):
        netcfg_rollback.write_crumb(tmp_path, "eth1", "expired", "2026-10-24T10:00:00+00:00", [])
    assert leftovers(tmp_path) == []          # פירור חתוך היה נקרא "פגום" בהפעלה הבאה


# --- storage_locations: ‏/etc/fstab וקובץ אישורי CIFS -----------------------------

def test_fstab_is_durable(tmp_path, monkeypatch, fsynced):
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=1 / ext4 defaults 0 1\n", encoding="utf-8")
    monkeypatch.setattr(storage_locations, "FSTAB_PATH", fstab)
    monkeypatch.setattr(storage_locations, "daemon_reload",   # ‏#2122: לא PID 1 של המריץ
                        lambda: storage_locations.HookResult(storage_locations.OK), raising=False)
    entries = [{"source": "//nas/images", "target": "/srv/images-2", "fstype": "cifs"}]
    assert storage_locations.fstab_write(entries).status == storage_locations.OK
    assert "UUID=1 / ext4" in fstab.read_text(encoding="utf-8") and "//nas/images" in fstab.read_text(encoding="utf-8")
    assert_durable(fstab, fsynced)


def test_fstab_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=1 / ext4 defaults 0 1\n", encoding="utf-8")
    monkeypatch.setattr(storage_locations, "FSTAB_PATH", fstab)
    with torn_write(monkeypatch, "fstab"):
        result = storage_locations.fstab_write([{"source": "//nas/x", "target": "/srv/x"}])
    assert result.status != storage_locations.OK
    assert fstab.read_text(encoding="utf-8") == "UUID=1 / ext4 defaults 0 1\n"
    assert leftovers(tmp_path, "fstab") == []


def test_cifs_credentials_are_durable_and_0600(tmp_path, fsynced):
    path = tmp_path / "creds" / "loc_1"
    result = storage_locations.creds_write(str(path), {"username": "u", "password": "p"})
    assert result.status == storage_locations.OK
    assert path.read_text(encoding="utf-8") == "username=u\npassword=p\n"
    assert_durable(path, fsynced)
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


# --- images: ‏manifest.json בעריכה, ורשימת הקריאה החוזרת --------------------------

def test_library_manifest_edit_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text('{"id": "img_old"}', encoding="utf-8")
    with torn_write(monkeypatch, "manifest.json"), pytest.raises(OSError):
        images._replace_json(path, {"id": "img_old", "name": "x" * 4000})
    assert path.read_text(encoding="utf-8") == '{"id": "img_old"}'
    assert leftovers(tmp_path, "manifest.json") == []


def test_library_manifest_edit_is_durable(tmp_path, fsynced):
    path = tmp_path / "manifest.json"
    path.write_text('{"id": "img_old"}', encoding="utf-8")
    images._replace_json(path, {"id": "img_old", "name": "new"})
    assert_durable(path, fsynced)


def _readback_image(tmp_path):
    from test_readback_1805 import BYTES_B, LIST_B, _image
    from test_fanout import xxh64_reference
    _image(tmp_path)
    return images.ImageLibrary(tmp_path), {1: (LIST_B, BYTES_B, xxh64_reference(LIST_B.encode("ascii")))}


def test_readback_list_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    library, values = _readback_image(tmp_path)
    folder = tmp_path / "img_1805aa"
    before = sorted(os.listdir(folder))
    with torn_write(monkeypatch, ".readback"), pytest.raises(OSError):
        library.record_readback("img_1805aa", values)
    assert sorted(os.listdir(folder)) == before        # no torn list, no tmp


def test_readback_list_is_durable(tmp_path, fsynced):
    library, values = _readback_image(tmp_path)
    assert library.record_readback("img_1805aa", values) == [1]
    assert_durable(tmp_path / "img_1805aa" / "p1.esp.pcl.zst.readback", fsynced)


# --- wizard: ‏firstboot.status (כישלון והצלחה) -----------------------------------

def _wizard(tmp_path):
    status = tmp_path / "firstboot.status"
    self = SimpleNamespace(paths=SimpleNamespace(status_file=status), _set=lambda *a, **k: None)
    return self, status, SimpleNamespace(interface="eth0", role="primary")


def test_wizard_failure_status_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    self, status, config = _wizard(tmp_path)
    status.write_text(OLD, encoding="utf-8")
    with torn_write(monkeypatch, "firstboot"), pytest.raises(OSError):
        WizardState._fail(self, "failed", "boom", config)
    assert status.read_text(encoding="utf-8") == OLD
    assert leftovers(tmp_path, status.name) == []


def test_wizard_done_status_is_durable(tmp_path, fsynced):
    self, status, config = _wizard(tmp_path)
    assert WizardState._status_done(self, config) is True
    assert_durable(status, fsynced)


def test_wizard_done_status_cut_halfway_leaves_no_tmp(tmp_path, monkeypatch):
    self, status, config = _wizard(tmp_path)
    status.write_text(OLD, encoding="utf-8")
    with torn_write(monkeypatch, "firstboot"), pytest.raises(OSError):
        WizardState._status_done(self, config)
    assert status.read_text(encoding="utf-8") == OLD
    assert leftovers(tmp_path, status.name) == []


# --- vmupdate: ‏state.json ומניפסט הגרסה החדשה -----------------------------------

def test_vm_update_state_survives_a_write_that_dies_halfway(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text(OLD, encoding="utf-8")
    with torn_write(monkeypatch, "state.json"), pytest.raises(OSError):
        vmupdate.VmUpdate._save_state(SimpleNamespace(workdir=tmp_path), {"state": "running"})
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == OLD
    assert leftovers(tmp_path, "state.json") == []


def test_vm_update_state_is_durable(tmp_path, fsynced):
    vmupdate.VmUpdate._save_state(SimpleNamespace(workdir=tmp_path), {"state": "running"})
    assert_durable(tmp_path / "state.json", fsynced)


# --- auto_reboot: סמן ההפעלה מחדש המתוזמנת ---------------------------------------

def _reboot_job(server, data_dir, monkeypatch):
    from datetime import datetime

    from server.db import set_setting
    from test_auto_reboot_1406 import scheduler
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    set_setting(server["ctx"].conn, "auto_reboot_time", "03:00")
    monkeypatch.setattr(auto_reboot, "busy_reason", lambda rt: None)
    data_dir.mkdir()
    return scheduler(server, data_dir, [datetime(2026, 10, 14, 3)], lambda: None)


def test_scheduled_reboot_marker_survives_a_write_that_dies_halfway(server, tmp_path, monkeypatch):
    job = _reboot_job(server, tmp_path / "rb", monkeypatch)
    marker = tmp_path / "rb" / auto_reboot.MARKER
    marker.write_text(OLD, encoding="ascii")
    with torn_write(monkeypatch, auto_reboot.MARKER), pytest.raises(OSError):
        job.tick()
    assert marker.read_text(encoding="ascii") == OLD
    assert leftovers(marker.parent, marker.name) == []


def test_scheduled_reboot_marker_is_durable(server, tmp_path, monkeypatch, fsynced):
    job = _reboot_job(server, tmp_path / "rb", monkeypatch)
    assert job.tick() == "done"
    assert_durable(tmp_path / "rb" / auto_reboot.MARKER, fsynced)


# vmupdate.py:1241 — המניפסט של הגרסה החדשה נכתב ל-out/ ואז out נכנס לספרייה
# ב-rename. בלי fsync, נפילת חשמל אחרי ה-rename משאירה אימג' בספרייה עם
# manifest.json ריק (עיקרון 3: הדיסק הוא מקור האמת).
from test_vmupdate import lab, run_to_stopped, wait_for  # noqa: E402,F401 -- lab is a fixture


def test_a_saved_vm_version_lands_with_a_durable_manifest(lab, fsynced):
    engine = run_to_stopped(lab)
    engine.save("noc", "Office 2024 v2")
    new_id = wait_for(engine, "saved")["result"]["image_id"]
    assert_durable(lab["root"] / new_id / "manifest.json", fsynced)
