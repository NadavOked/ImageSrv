"""#3021 + #3091: טקסט חריגה גולמי לא מגיע למפעיל ולא ליומן האירועים -- ההמשך של #2543.

אותה הכרעה (נדב 07/11, #595): המפעיל ויומן האירועים מקבלים משפט שלנו שמסתיים
ב"הפרטים ביומן השרת"; הטקסט הגולמי (strerror, stderr, נתיב) הולך ל-``log.warning``.
כל טסט בודק שלושה דברים: הגולמי **לא** בתשובה/ביומן, הוא **כן** ב-log, והכישלון עצמו
נשאר גלוי (עיקרון 5).

**#3084** נמצא בקובץ נפרד (test_interserver_cap_3084.py).
"""

from __future__ import annotations

import errno
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.db import connect

SECRET = "/var/lib/imagesrv/fake-secret-path"
RAW = "fake-strerror"
TAIL = "הפרטים ביומן השרת"


def os_error(code: int = errno.EACCES) -> OSError:
    return OSError(code, RAW, SECRET)


def logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def clean(text) -> bool:
    text = json.dumps(text, ensure_ascii=False) if not isinstance(text, str) else text
    return SECRET not in text and RAW not in text


def journal_details(conn, event: str) -> list[str]:
    return [r["detail"] or "" for r in conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


# --- #3021: server/app.py -- NTP של הכרטיס בעלייה ---------------------------------------

def test_a_timesync_step_that_failed_at_start_journals_our_sentence(tmp_path, caplog):
    from server.app import _nic_services
    caplog.set_level("WARNING", logger="imagesrv.app")

    def failing(_nic):
        raise os_error(errno.ENOENT)

    ctx = SimpleNamespace(conn=connect(tmp_path / "imagesrv.db"))
    service = _nic_services(ctx, None, None, None, False,
                            {"interfaces": lambda: [], "timesync": failing}, apply_timesync=True)
    assert service is not None
    rows = journal_details(ctx.conn, "timesync_apply_failed")
    assert len(rows) == 1 and TAIL in rows[0] and clean(rows[0]), rows       # הכישלון גלוי
    assert RAW in logged(caplog) and SECRET in logged(caplog)


# --- #3021: readbackhash -- `problems` של הגיבוב -----------------------------------------

def test_a_decompressor_that_cannot_run_is_our_sentence(tmp_path, caplog):
    from server.agent import readbackhash as RB
    caplog.set_level("WARNING", logger="imagesrv.readbackhash")
    part = tmp_path / "p1.zst"
    part.write_bytes(b"x")
    with pytest.raises(RB.Underived) as caught:
        RB.digest_file(part, True, cmd=(SECRET + "/no-such-zstd",))
    assert clean(str(caught.value)) and TAIL in str(caught.value)
    assert SECRET in logged(caplog)


def test_an_image_whose_hashing_raised_is_reported_with_our_sentence(tmp_path, caplog):
    from server.agent import readbackhash as RB
    from test_chunk_format_2533 import _image
    caplog.set_level("WARNING", logger="imagesrv.readbackhash")

    def boom(*_a, **_k):
        raise RuntimeError(f"{RAW} {SECRET}")
    results = RB.fill(_image(tmp_path), digest=boom)
    assert len(results) == 1 and results[0]["problems"], results
    text = "; ".join(results[0]["problems"])
    assert clean(text) and TAIL in text
    assert RAW in logged(caplog)


# --- #3021: deploy_net -- מחולל חומת האש; #3091: תזמון אתחול --------------------------------

def test_a_firewall_generator_that_failed_is_our_sentence(tmp_path, monkeypatch, caplog):
    from server.net import deploy_net
    caplog.set_level("WARNING", logger=deploy_net.log.name)
    monkeypatch.setattr(deploy_net, "firewall_installed", lambda conf=None: True)
    monkeypatch.setattr(deploy_net, "_run", lambda *a, **kw: (False, "", f"nft: {RAW} {SECRET}"))
    error = deploy_net.regenerate_firewall(tmp_path, deploy_if="eth1", servers_if="eth0",
                                           conf=tmp_path / "nftables.conf")
    assert error and "נכשל" in error and TAIL in error and clean(error)
    assert RAW in logged(caplog)


def test_a_server_restart_that_could_not_be_scheduled_is_our_sentence(monkeypatch, caplog):
    from server.net import deploy_net
    caplog.set_level("WARNING", logger=deploy_net.log.name)
    monkeypatch.setattr(deploy_net, "_run", lambda *a, **kw: (False, "", f"Failed: {RAW} {SECRET}"))
    error = deploy_net.restart_server_later()
    assert error and TAIL in error and clean(error)
    assert RAW in logged(caplog)


def test_a_power_action_that_could_not_be_scheduled_is_our_sentence(monkeypatch, caplog):
    from server.console import server_power
    caplog.set_level("WARNING", logger="imagesrv.server_power")
    monkeypatch.setattr(server_power.deploy_net, "_run",
                        lambda *a, **kw: (False, f"out {SECRET}", f"Failed: {RAW}"))
    error = server_power.schedule_later("reboot")
    assert error and "systemd-run" in error and TAIL in error and clean(error)
    assert RAW in logged(caplog)


# --- #3091: אזור הזמן ----------------------------------------------------------------------

def test_a_timezone_command_that_did_not_run_or_failed_is_our_sentence(monkeypatch, caplog):
    from server.system import timezone
    caplog.set_level("WARNING", logger=timezone.log.name)

    def cannot(*a, **kw):
        raise os_error(errno.ENOENT)
    monkeypatch.setattr(timezone.subprocess, "run", cannot)
    error = timezone._timedatectl_set("Asia/Jerusalem")
    assert error and TAIL in error and clean(error)
    monkeypatch.setattr(timezone.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 1, "", f"Failed to set time zone: {RAW} {SECRET}"))
    error = timezone._timedatectl_set("Asia/Jerusalem")
    assert error and "1" in error and TAIL in error and clean(error)       # קוד החזרה נשאר גלוי
    assert "Failed to set time zone" in logged(caplog)


# --- #3021: usbstick/device.py -- עיגון ----------------------------------------------------

def _stick_calls(mount_ok=True, umount_ok=True):
    from server.storage import locations as sl
    from test_usb_stick_device_1920 import connected
    log: list = []

    def scan():
        return connected()

    def mount(fstype, source, target, opts=""):
        return sl.HookResult(sl.OK if mount_ok else sl.FAILED, "" if mount_ok else f"{RAW} {SECRET}")

    def umount(target):
        return sl.HookResult(sl.OK if umount_ok else sl.FAILED, "" if umount_ok else f"busy {RAW} {SECRET}")
    return connected().stick, scan, mount, umount, log


def test_a_stick_that_did_not_mount_is_our_sentence(monkeypatch, caplog):
    from server.usbstick import device
    caplog.set_level("WARNING", logger="imagesrv.usbstick")
    monkeypatch.setattr(device.sl, "is_mounted", lambda target: False)
    stick, scan, mount, umount, _ = _stick_calls(mount_ok=False)
    with pytest.raises(device.StickError) as caught:
        with device.open_stick(stick, scanner=scan, mount=mount, umount=umount):
            pytest.fail("the stick must not open")
    assert "העיגון נכשל" in str(caught.value) and TAIL in str(caught.value) and clean(str(caught.value))
    assert RAW in logged(caplog)


def test_a_stick_that_stayed_mounted_is_our_sentence(monkeypatch, caplog):
    from server.usbstick import device
    caplog.set_level("WARNING", logger="imagesrv.usbstick")
    monkeypatch.setattr(device.sl, "is_mounted", lambda target: False)
    stick, scan, mount, umount, _ = _stick_calls(umount_ok=False)
    with pytest.raises(device.StickError) as caught:
        with device.open_stick(stick, scanner=scan, mount=mount, umount=umount):
            pass
    assert "נשאר מעוגן" in str(caught.value) and TAIL in str(caught.value) and clean(str(caught.value))
    assert RAW in logged(caplog)


# --- #3021: storage_client -- ניתוק באמצע העברה בין אתרים --------------------------------

class _Conn:
    """חיבור מזויף: תשובה מוכנה, ואז חריגה גולמית בקריאה/בכתיבה."""

    def __init__(self, replies: list[bytes], *, fail_after_sends: int | None = None):
        self._replies = replies
        self._sends = 0
        self._fail_after = fail_after_sends

    def limit(self, *a, **kw):
        pass

    def sendall(self, data):
        self._sends += 1
        if self._fail_after is not None and self._sends > self._fail_after:
            raise os_error(errno.ECONNRESET)

    def recv(self, n):
        if self._replies:
            return self._replies.pop(0)
        raise os_error(errno.ECONNRESET)


def _client(conn):
    from server.interserver import storage_client
    client = object.__new__(storage_client.PinnedMTLSClient)
    client._conn = conn
    client.host = "node1.example"
    client.path_prefix = ""
    return client


def test_a_download_dropped_midway_is_our_sentence(tmp_path, caplog):
    from server.interserver import storage_client
    caplog.set_level("WARNING", logger="imagesrv.storage_client")
    conn = _Conn([b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n"])
    with pytest.raises(storage_client.InterserverClientError) as caught:
        _client(conn).get_stream("/x", "tok", tmp_path / "x.bin")
    assert "נותק אחרי" in str(caught.value) and TAIL in str(caught.value) and clean(str(caught.value))
    assert RAW in logged(caplog)


def test_an_upload_dropped_midway_is_our_sentence(caplog):
    from server.interserver import storage_client
    caplog.set_level("WARNING", logger="imagesrv.storage_client")
    conn = _Conn([b"HTTP/1.1 100 Continue\r\nContent-Length: 0\r\n\r\n"], fail_after_sends=1)
    with pytest.raises(storage_client.InterserverClientError) as caught:
        _client(conn).put_stream("/x", "tok", iter([b"abcd"]), 4)
    assert "נותק אחרי" in str(caught.value) and TAIL in str(caught.value) and clean(str(caught.value))
    assert RAW in logged(caplog)


# --- #3021: netcfg -- שגיאות ההחזרה -------------------------------------------------------

def test_rollback_errors_are_counted_in_the_journal_and_named_in_the_log(tmp_path, monkeypatch, caplog):
    from server.console import netcfg
    caplog.set_level("WARNING", logger="imagesrv.console_netcfg")
    crumb = {"at": "2026-11-15T10:00:00", "interface": "eth1", "reason": "timeout",
             "errors": [f"nmcli: {RAW} {SECRET}", "ifdown failed"]}
    monkeypatch.setattr(netcfg.netcfg_rollback, "read_crumbs", lambda state_dir: [crumb])
    monkeypatch.setattr(netcfg.netcfg_rollback, "clear_crumbs", lambda state_dir: None)
    ctx = SimpleNamespace(conn=connect(tmp_path / "imagesrv.db"))
    assert netcfg.drain_crumbs(ctx, tmp_path) == 1
    rows = journal_details(ctx.conn, "net_rollback")
    assert len(rows) == 1 and "errors=2" in rows[0] and TAIL in rows[0] and clean(rows[0]), rows
    assert RAW in logged(caplog)
    from server.console.journal_he import JournalTranslator
    _label, said = JournalTranslator(ctx.conn, SimpleNamespace(scan=lambda: {})).translate(
        "net_rollback", rows[0])
    assert "ההחזרה עצמה דיווחה שגיאות" in said and TAIL in said and clean(said), said      # נשאר גלוי
