"""#2543: טקסט חריגה גולמי לא יוצא למפעיל — גם דרך ``HTTPException``, WebSocket,
``ProbeResult.reason``, ``TargetError``, ``last-backup.json`` ויומן האירועים.

ההכרעה (נדב 07/11, #595; SECURITY.md, "מה הלקוח רואה כשפעולה נכשלת"): המפעיל רואה
רק הודעה שלנו, ברורה ובעברית. הטקסט הטכני (נתיב, strerror, sqlite, OpenSSL, פלט
פקודה) הולך ליומן השרת בלבד — "יומן השרת" הוא journald, לא טבלת ``journal`` שהקונסולה
מציגה. כל טסט בודק שלושה דברים: הטקסט הגולמי **לא** בתשובה, הוא **כן** ב-log, והכישלון
עצמו נשאר גלוי (קוד שגיאה / "נכשל" / "לא נבדק" — עיקרון 5).

כל חריגה מזויפת נושאת את ``SECRET`` ואת ``fake-strerror``.
"""

from __future__ import annotations

import errno
import json
import socket
import sqlite3
import subprocess
import types
from pathlib import Path

import pytest

from server.db import connect

SECRET = "/var/lib/imagesrv/fake-secret-path"
TAIL = "הפרטים ביומן השרת"
NODE = "node1"
MAC = "aa:bb:cc:dd:ee:01"


def os_error(code: int = errno.EACCES) -> OSError:
    return OSError(code, "fake-strerror", SECRET)


def logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def clean(text) -> bool:
    text = json.dumps(text, ensure_ascii=False) if not isinstance(text, str) else text
    return SECRET not in text and "fake-strerror" not in text


def journal_details(conn, event: str) -> list[str]:
    return [r["detail"] or "" for r in conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


# --- console/storage.py: הקונסולה של המשני דרך הראשי ----------------------------------

@pytest.fixture()
def node(server, monkeypatch):
    """משני רשום אחד; ``node_client`` מוחלף — אין רשת בטסט."""
    from server.db import now_iso, set_setting
    from server.interserver import storage_nodes
    conn = server["ctx"].conn
    set_setting(conn, storage_nodes.ROLE_KEY, storage_nodes.ROLE_STANDALONE)
    conn.execute(
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (NODE, "haifa", "https://node1.example:8443/api/interserver/v1", "cred:missing", now_iso()))
    conn.commit()
    return server


def _refusing_client(monkeypatch, exc: Exception):
    from server.interserver import storage_nodes

    def node_client(conn, data_dir, node, *, timeout=None):
        raise exc
    monkeypatch.setattr(storage_nodes, "node_client", node_client)


def test_a_relayed_get_that_did_not_connect_is_our_sentence(node, monkeypatch, caplog):
    caplog.set_level("WARNING", logger="imagesrv.console_storage")
    _refusing_client(monkeypatch, os_error(errno.ECONNREFUSED))
    r = node["admin"].get(f"/api/console/storage-nodes/{NODE}/console/overview")
    assert r.status_code == 502 and "לא ענה" in r.json()["detail"]
    assert clean(r.json()["detail"]), r.text
    assert "fake-strerror" in logged(caplog)
    row = node["ctx"].conn.execute(
        "SELECT last_error FROM storage_nodes WHERE id = ?", (NODE,)).fetchone()
    assert row["last_error"] and clean(row["last_error"])


def test_a_relayed_write_that_was_not_sent_is_our_sentence(node, monkeypatch, caplog):
    caplog.set_level("WARNING", logger="imagesrv.console_storage")
    _refusing_client(monkeypatch, KeyError(SECRET))
    r = node["admin"].post(f"/api/console/storage-nodes/{NODE}/console/users",
                           json={"username": "x"})
    assert r.status_code == 502 and "לא נשלחה" in r.json()["detail"]
    assert clean(r.json()["detail"]), r.text
    assert SECRET in logged(caplog)
    assert all(clean(d) for d in journal_details(node["ctx"].conn, "peer_write"))


def test_a_relayed_write_that_dropped_after_sending_is_unknown_not_raw(node, monkeypatch, caplog):
    from server.interserver import storage_nodes
    caplog.set_level("WARNING", logger="imagesrv.console_storage")

    class Dropped:
        request_timeout = 10.0

        def open(self):
            pass

        def close(self):
            pass

        def send_answer(self, *a, **kw):
            raise os_error(errno.ECONNRESET)

    monkeypatch.setattr(storage_nodes, "node_client", lambda *a, **kw: (Dropped(), "tok"))
    r = node["admin"].post(f"/api/console/storage-nodes/{NODE}/console/users",
                           json={"username": "x"})
    detail = r.json()["detail"]
    assert r.status_code == 502 and "לא ידוע אם" in detail and TAIL in detail
    assert clean(detail), detail
    assert "fake-strerror" in logged(caplog)
    assert all(clean(d) for d in journal_details(node["ctx"].conn, "peer_write"))


def test_remote_power_on_an_unreachable_secondary_is_our_sentence(node, monkeypatch, caplog):
    caplog.set_level("WARNING", logger="imagesrv.console_storage")
    _refusing_client(monkeypatch, os_error(errno.EHOSTUNREACH))
    r = node["admin"].post(f"/api/console/storage-nodes/{NODE}/monitor/{MAC}/power",
                           json={"action": "reboot"})
    assert r.status_code == 502 and r.json()["detail"].startswith("שרת המשנה אינו זמין")
    assert clean(r.json()["detail"]), r.text
    assert "fake-strerror" in logged(caplog)


def _ws_reason(client, path) -> tuple[int, str]:
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(path) as ws:
            ws.receive_bytes()
    return caught.value.code, caught.value.reason


def test_the_remote_monitor_close_reason_carries_no_raw_text(node, monkeypatch, caplog):
    from server.interserver import auth as interserver_auth
    caplog.set_level("WARNING", logger="imagesrv.console_storage")

    def unreadable(_ref):
        raise os_error()
    monkeypatch.setattr(interserver_auth, "load_credential", unreadable)
    code, reason = _ws_reason(node["admin"], f"/api/console/storage-nodes/{NODE}/monitor/{MAC}")
    assert code == 4500 and reason and clean(reason), reason
    assert "fake-strerror" in logged(caplog)


def test_a_tunnel_that_did_not_open_closes_with_our_sentence(node, monkeypatch, caplog):
    from server.interserver import auth as interserver_auth, storage_client, storage_nodes
    caplog.set_level("WARNING", logger="imagesrv.console_storage")
    monkeypatch.setattr(interserver_auth, "load_credential", lambda ref: "tok")
    monkeypatch.setattr(storage_nodes, "identity",
                        lambda conn, data_dir=None: {"server_cert_ref": "c", "server_key_ref": "k"})

    async def no_tunnel(*a, **kw):
        raise os_error(errno.ECONNREFUSED)
    monkeypatch.setattr(storage_client, "open_tunnel", no_tunnel)
    code, reason = _ws_reason(node["admin"], f"/api/console/storage-nodes/{NODE}/monitor/{MAC}")
    assert code == 4502 and reason.startswith("השרת המשני אינו זמין") and clean(reason), reason
    assert "fake-strerror" in logged(caplog)


def test_an_enrollment_row_that_failed_in_sqlite_is_our_sentence(node, monkeypatch, caplog):
    from server.interserver import auth as interserver_auth, storage_client, storage_nodes
    caplog.set_level("WARNING", logger="imagesrv.console_storage")
    spki = "ab" * 32
    monkeypatch.setattr(storage_client, "pair_secondary", lambda *a, **kw: {"token": "t" * 40})

    def integrity(*a, **kw):
        raise sqlite3.IntegrityError(f"UNIQUE constraint failed: {SECRET}")
    monkeypatch.setattr(storage_nodes, "enroll_node", integrity)
    r = node["admin"].post("/api/console/storage-nodes/enroll", json={
        "url": "https://10.0.0.9:8443/api/interserver/v1", "label": "branch",
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
        "expected_secondary_spki": spki, "code": "123456"})
    assert r.status_code == 409 and "רישום המשני נכשל" in r.json()["detail"]
    assert clean(r.json()["detail"]) and "UNIQUE" not in r.json()["detail"], r.text
    assert SECRET in logged(caplog)


# --- interserver/storage_client.py: הודעות "שלנו" שעטפו את {exc} ---------------------

def test_a_request_that_was_not_sent_says_ours_and_logs_the_os_text(caplog):
    from server.interserver import storage_client
    caplog.set_level("WARNING", logger="imagesrv.storage_client")

    class Broken:
        def limit(self, *a, **kw):
            pass

        def sendall(self, data):
            raise os_error(errno.EPIPE)

    client = storage_client.PinnedMTLSClient("10.0.0.9", 8443, expected_secondary_spki="00" * 32,
                                             cert_pem=b"", key_pem=b"")
    client._conn = Broken()
    with pytest.raises(storage_client.RequestNotSent) as caught:
        client.send_answer("POST", "/console/users", "tok", {})
    assert clean(str(caught.value)) and TAIL in str(caught.value)
    assert "fake-strerror" in logged(caplog)


def test_a_failed_handshake_says_ours_and_logs_the_openssl_text(caplog, monkeypatch):
    pytest.importorskip("OpenSSL")
    from OpenSSL import SSL
    from server.interserver import auth as interserver_auth, storage_client
    caplog.set_level("WARNING", logger="imagesrv.storage_client")
    monkeypatch.setattr(interserver_auth, "build_pinned_client_context",
                        lambda *a: SSL.Context(SSL.TLS_CLIENT_METHOD))

    def refuse(self):
        raise SSL.Error([("fake-strerror", "", SECRET)])
    monkeypatch.setattr(storage_client._TimedConn, "do_handshake", refuse)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        client = storage_client.PinnedMTLSClient(
            "127.0.0.1", listener.getsockname()[1], expected_secondary_spki="00" * 32,
            cert_pem=b"", key_pem=b"")
        with pytest.raises(storage_client.InterserverClientError) as caught:
            client.open()
    finally:
        listener.close()
    assert "handshake mTLS נכשל" in str(caught.value) and clean(str(caught.value))
    assert SECRET in logged(caplog)


# --- net/dhcp_host.py: ProbeResult.reason ו-set_link --------------------------------

class _Tap:
    def __init__(self, frames, stats_error=None):
        self.frames = list(frames)
        self.stats_error = stats_error

    def settimeout(self, _t):
        pass

    def recvfrom(self, _n):
        if not self.frames:
            raise socket.timeout()
        item = self.frames.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def getsockopt(self, *a):
        raise self.stats_error

    def close(self):
        pass


def test_a_listen_that_broke_off_is_unchecked_with_our_reason(caplog):
    from server.net import dhcp_host
    caplog.set_level("WARNING", logger="server.net.dhcp_host")
    found = dhcp_host._listen(_Tap([os_error(errno.ENETDOWN)]), "eth1", b"xid1", 0.5)
    assert found.checked is False and found.servers == ()
    assert "eth1" in found.reason and TAIL in found.reason and clean(found.reason), found
    assert "fake-strerror" in logged(caplog)


def test_unreadable_listen_counters_are_unchecked_with_our_reason(monkeypatch, caplog):
    from server.net import dhcp_host
    caplog.set_level("WARNING", logger="server.net.dhcp_host")
    monkeypatch.setattr(dhcp_host, "_dhcp_frame", lambda frame, xid: ("request", "0.0.0.0"))
    tap = _Tap([(b"frame", ("eth1", 0, dhcp_host.PACKET_OUTGOING))], stats_error=os_error())
    found = dhcp_host._listen(tap, "eth1", b"xid1", 0.5)
    assert found.checked is False and TAIL in found.reason and clean(found.reason), found
    assert "fake-strerror" in logged(caplog)


def test_ip_link_that_did_not_run_or_failed_is_our_sentence(monkeypatch, caplog):
    from server.net import dhcp_host
    caplog.set_level("WARNING", logger="server.net.dhcp_host")

    def cannot(*a, **kw):
        raise os_error(errno.ENOENT)
    monkeypatch.setattr(dhcp_host.subprocess, "run", cannot)
    error = dhcp_host.set_link("eth1", True)
    assert error and TAIL in error and clean(error), error
    monkeypatch.setattr(dhcp_host.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 2, "", f"RTNETLINK answers: fake-strerror {SECRET}"))
    error = dhcp_host.set_link("eth1", True)
    assert error and "2" in error and clean(error), error
    assert "RTNETLINK" in logged(caplog) and "fake-strerror" in logged(caplog)


def test_the_whole_probe_hands_only_our_reason(monkeypatch):
    """דרך ``probe_existing_dhcp`` כולה — מה ש-``GET /interfaces/{name}/probe`` מחזיר."""
    from server.net import dhcp_host
    monkeypatch.setattr(dhcp_host, "_open_tap", lambda iface: _Tap([os_error(errno.ENETDOWN)]))

    class Sender:
        def bind(self, addr):
            pass

        def getsockname(self):
            return ("eth1", 0, 0, 0, b"\xaa\xbb\xcc\xdd\xee\x01")

        def sendto(self, frame, addr):
            return len(frame)

        def close(self):
            pass
    monkeypatch.setattr(dhcp_host.socket, "socket", lambda *a: Sender())
    found = dhcp_host.probe_existing_dhcp("eth1", timeout=0.2)
    assert found.checked is False and clean(found.reason) and TAIL in found.reason, found


# --- system/backup_target.py: write_probe ו-TargetError ------------------------------

def test_a_probe_that_could_not_be_written_is_409_with_our_sentence(tmp_path, monkeypatch, caplog):
    from server.system import backup_target
    caplog.set_level("WARNING", logger="imagesrv.backup_target")

    real = backup_target.os.open

    def refuse(path, *a, **kw):
        if ".imagesrv-backup-test-" in str(path):
            raise os_error(errno.EROFS)
        return real(path, *a, **kw)
    monkeypatch.setattr(backup_target.os, "open", refuse)
    with pytest.raises(backup_target.TargetError) as caught:
        backup_target.write_probe(tmp_path)
    assert caught.value.status == 409 and str(tmp_path) in caught.value.reason
    assert clean(caught.value.reason) and TAIL in caught.value.reason
    assert "fake-strerror" in logged(caplog)


def test_a_probe_that_did_not_read_back_is_409_with_our_sentence(tmp_path, monkeypatch, caplog):
    from server.system import backup_target
    caplog.set_level("WARNING", logger="imagesrv.backup_target")

    def unreadable(path):
        raise os_error(errno.EIO)
    monkeypatch.setattr(backup_target, "_read_back", unreadable)
    with pytest.raises(backup_target.TargetError) as caught:
        backup_target.write_probe(tmp_path)
    assert caught.value.status == 409 and "לא נקרא בחזרה" in caught.value.reason
    assert clean(caught.value.reason)
    assert "fake-strerror" in logged(caplog)
    assert not list(tmp_path.iterdir()), "the probe file is removed even when the read failed"


def test_a_probe_that_was_not_removed_is_409_with_our_sentence(tmp_path, monkeypatch, caplog):
    from server.system import backup_target
    caplog.set_level("WARNING", logger="imagesrv.backup_target")
    real = Path.unlink

    def stuck(self, *a, **kw):
        if self.name.startswith(".imagesrv-backup-test-"):
            raise os_error(errno.EBUSY)
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", stuck)
    with pytest.raises(backup_target.TargetError) as caught:
        backup_target.write_probe(tmp_path)
    assert caught.value.status == 409 and "לא נמחק" in caught.value.reason
    assert clean(caught.value.reason)
    assert "fake-strerror" in logged(caplog)


def test_a_default_folder_that_was_not_created_is_409_with_our_sentence(tmp_path, monkeypatch,
                                                                        caplog):
    from server.system import backup_target
    caplog.set_level("WARNING", logger="imagesrv.backup_target")
    conn = connect(tmp_path / "imagesrv.db")
    real = Path.mkdir

    def refuse(self, *a, **kw):
        if self.name == "backups":
            raise os_error(errno.ENOSPC)
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "mkdir", refuse)
    with pytest.raises(backup_target.TargetError) as caught:
        backup_target.configure(conn, tmp_path, {"type": "default"}, {}, "noc")
    assert caught.value.status == 409 and "לא נוצרה" in caught.value.reason
    assert clean(caught.value.reason)
    assert "fake-strerror" in logged(caplog)


# --- system/backup_job.py: השדה error ב-last-backup.json -----------------------------

def test_a_foreign_backup_failure_writes_our_sentence_to_the_status_file(tmp_path, monkeypatch,
                                                                         caplog):
    from server.system import backup_job
    caplog.set_level("WARNING", logger="imagesrv.backup")

    def broken(*a, **kw):
        raise os_error(errno.EIO)
    monkeypatch.setattr(backup_job, "backup_once", broken)
    status = backup_job.run(tmp_path, str(tmp_path / "dest"))
    assert status["ok"] is False and status["error"] == f"הגיבוי נכשל — {TAIL}"
    saved = json.loads((tmp_path / backup_job.STATUS_NAME).read_text(encoding="utf-8"))
    assert saved["ok"] is False and saved["error"] == status["error"] and clean(saved)
    assert "fake-strerror" in logged(caplog)


def test_our_own_backup_refusal_still_reaches_the_status_file_word_for_word(tmp_path):
    """השומר מפני תיקון-יתר: סירוב שנוסח בשמו (#2574, יעד שאינו מעוגן) עובר כמו שהוא."""
    from server.system import backup_job
    dest = tmp_path / "not-mounted"
    status = backup_job.run(tmp_path, str(dest), other_disk=True)
    assert status["ok"] is False
    assert status["error"] == f"התיקייה {dest} אינה קיימת (הכונן לא מעוגן?) — הגיבוי לא נכתב"


def test_an_unreadable_database_writes_our_sentence_to_the_status_file(tmp_path, monkeypatch,
                                                                       capsys):
    from server.system import backup_job

    def locked(_data_dir):
        raise sqlite3.OperationalError(f"fake-strerror {SECRET}")
    monkeypatch.setattr(backup_job, "_config", locked)
    assert backup_job.main(["--data-dir", str(tmp_path)]) == 1
    saved = json.loads((tmp_path / backup_job.STATUS_NAME).read_text(encoding="utf-8"))
    assert saved["ok"] is False and "המסד לא נקרא" in saved["error"] and clean(saved)
    assert "fake-strerror" in capsys.readouterr().err       # stderr = journald של היחידה


# --- יומן האירועים: dhcp_apply_failed, ups_config_failed ----------------------------

def test_a_dhcp_render_failure_journals_our_sentence(tmp_path, monkeypatch, caplog):
    from server.console import dhcp as console_dhcp
    caplog.set_level("WARNING", logger="imagesrv.console_dhcp")

    def bad_render(ctx, deploy):
        raise ValueError(f"fake-render {SECRET}")
    monkeypatch.setattr(console_dhcp, "render_dnsmasq", bad_render)
    ctx = types.SimpleNamespace(conn=connect(tmp_path / "imagesrv.db"))
    error = console_dhcp.apply_dnsmasq(ctx, {}, None, "eth1", "noc")
    assert error and TAIL in error and clean(error)
    deploy = types.SimpleNamespace(state=types.SimpleNamespace(configured=True))
    error = console_dhcp.sync_main_conf(ctx, {}, deploy)
    assert error and TAIL in error and clean(error)
    details = journal_details(ctx.conn, "dhcp_apply_failed")
    assert len(details) == 2 and all(clean(d) and TAIL in d for d in details), details
    assert logged(caplog).count("fake-render") == 2


def test_a_ups_config_failure_journals_our_sentence(server, caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.console import ups as console_ups
    caplog.set_level("WARNING", logger="imagesrv.console_ups")

    def write(_files):
        raise os_error()
    hooks = {"write": write, "start_usb": lambda: None, "stop_usb": lambda: None,
             "read": lambda t: {}, "sleep": lambda s: None}
    app = FastAPI()
    app.include_router(console_ups.create_ups_router(server["ctx"], hooks))
    client = TestClient(app)
    client.cookies = server["admin"].cookies
    assert client.post("/api/console/ups", json={"mode": "usb"}).status_code == 500
    details = journal_details(server["ctx"].conn, "ups_config_failed")
    assert details == [f"הגדרת ה-UPS נכשלה ולא נשמרה — {TAIL}"], details
    assert SECRET in logged(caplog)


# --- עוד אתרים מאותו דפוס ב-server/console (#2543, "אתרים נוספים") -------------------

def test_an_unmeasured_free_space_is_507_with_our_sentence(server, monkeypatch, caplog):
    from server.console import library as console_library
    caplog.set_level("WARNING", logger="imagesrv.console_library")

    def unmeasured(_root):
        raise os_error(errno.EIO)
    monkeypatch.setattr(console_library, "_library_free", unmeasured)
    r = server["admin"].post("/api/console/images/upload", content=b"x" * 64,
                             headers={"content-length": "64"})
    assert r.status_code == 507 and clean(r.json()["detail"]) and TAIL in r.json()["detail"]
    assert "fake-strerror" in logged(caplog)


def test_an_unreadable_grub_password_is_503_and_unknown_with_our_sentence(server, tmp_path, caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.console import grubpw
    caplog.set_level("WARNING", logger="imagesrv.console_grubpw")
    missing = tmp_path / "no-such" / "grub.pw"
    app = FastAPI()
    app.include_router(grubpw.create_grub_password_router(server["ctx"], missing))
    client = TestClient(app)
    client.cookies = server["admin"].cookies
    for path in ("/api/console/grub-password", "/api/console/grub-password/saved"):
        r = client.post(path)
        assert r.status_code == 503 and r.json()["detail"] == f"סיסמת ה-GRUB לא נקראה — {TAIL}", r.text
    state, detail = grubpw.reminder(server["ctx"].conn, missing)
    assert state == "unknown" and TAIL in detail
    assert "Errno" not in detail and "WinError" not in detail, detail
    assert "Errno" in logged(caplog) or "WinError" in logged(caplog)


def test_hostnamectl_that_did_not_run_or_failed_is_our_sentence(monkeypatch, caplog):
    from server.console import hostcfg
    caplog.set_level("WARNING", logger="imagesrv.console_hostcfg")

    def cannot(*a, **kw):
        raise os_error(errno.ENOENT)
    monkeypatch.setattr(hostcfg.subprocess, "run", cannot)
    error = hostcfg._hostnamectl("srv1")
    assert error and TAIL in error and clean(error)
    monkeypatch.setattr(hostcfg.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 1, "", f"Could not set hostname: fake-strerror {SECRET}"))
    error = hostcfg._hostnamectl("srv1")
    assert error and "1" in error and clean(error)
    assert "Could not set hostname" in logged(caplog)


def test_an_unreadable_zone_list_is_500_with_our_sentence(server, monkeypatch, caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.console import timezone as console_timezone
    from server.system import timezone
    caplog.set_level("WARNING", logger="imagesrv.console_timezone")

    def unreadable(root=None):
        raise os_error(errno.ENOENT)
    monkeypatch.setattr(timezone, "zones", unreadable)
    app = FastAPI()
    app.include_router(console_timezone.create_timezone_router(
        server["ctx"], {"timezone": lambda: "UTC", "set_timezone": lambda z: None}))
    client = TestClient(app)
    client.cookies = server["admin"].cookies
    r = client.get("/api/console/timezone")
    assert r.status_code == 500 and clean(r.json()["detail"]) and TAIL in r.json()["detail"]
    assert "fake-strerror" in logged(caplog)


def test_a_time_command_that_failed_is_our_sentence(monkeypatch, caplog):
    from server.system import timesync
    caplog.set_level("WARNING", logger="imagesrv.timesync")

    def cannot(command, timeout=10):
        raise os_error(errno.ENOENT)
    monkeypatch.setattr(timesync, "_run", cannot)
    with pytest.raises(RuntimeError) as caught:
        timesync._read(["chronyc", "-n", "tracking"])
    assert "chronyc" in str(caught.value) and clean(str(caught.value))
    monkeypatch.setattr(timesync, "_run", lambda command, timeout=10: subprocess.CompletedProcess(
        command, 1, "", f"506 Cannot talk to daemon fake-strerror {SECRET}"))
    with pytest.raises(RuntimeError) as caught:
        timesync._read(["systemctl", "restart", "chrony"])
    assert clean(str(caught.value)) and TAIL in str(caught.value)
    assert "Cannot talk to daemon" in logged(caplog)


def test_a_time_source_that_could_not_be_written_is_our_sentence(caplog):
    from server.system import timesync
    caplog.set_level("WARNING", logger="imagesrv.timesync")

    def unwritable(_value):
        raise os_error(errno.EROFS)
    hooks = {**timesync.default_hooks(), "ensure_confdir": lambda: None, "write": unwritable,
             "restart": lambda: None}
    with pytest.raises(RuntimeError) as caught:
        timesync.apply("", hooks)
    assert clean(str(caught.value)) and TAIL in str(caught.value)
    assert "fake-strerror" in logged(caplog)


def test_a_time_sentence_of_ours_still_reaches_the_operator_word_for_word():
    """השומר מפני תיקון-יתר: RuntimeError של המודול (כאן מ-hook) עובר כמו שהוא."""
    from server.system import timesync

    def refused():
        raise RuntimeError("השעה לא נקבעה: השעון נקרא בחזרה בערך אחר")
    hooks = {**timesync.default_hooks(), "ensure_confdir": lambda: None, "write": lambda v: None,
             "restart": refused}
    with pytest.raises(RuntimeError) as caught:
        timesync.apply("", hooks)
    assert str(caught.value) == "השעה לא נקבעה: השעון נקרא בחזרה בערך אחר"


def test_a_tls_pair_that_does_not_load_is_refused_with_our_sentence(tmp_path, caplog):
    from server.console import tls as console_tls
    caplog.set_level("WARNING", logger="imagesrv.console_tls")
    error = console_tls._ssl_loads(b"not a certificate", b"not a key", tmp_path / "tls")
    assert error == TAIL
    assert logged(caplog)


def test_an_unreadable_usb_reports_folder_is_our_sentence(tmp_path, monkeypatch, caplog):
    from server.usbstick import records
    caplog.set_level("WARNING", logger="imagesrv.usbstick")
    (tmp_path / "reports").mkdir()

    real = records.os.scandir

    def unreadable(path):
        if Path(path).name == records.REPORTS_DIR:
            raise os_error(errno.EIO)
        return real(path)
    monkeypatch.setattr(records.os, "scandir", unreadable)
    with pytest.raises(records.ReadError) as caught:
        records.read_reports(tmp_path)
    assert clean(str(caught.value)) and TAIL in str(caught.value)
    assert "fake-strerror" in logged(caplog)
