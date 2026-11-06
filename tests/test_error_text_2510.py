"""‏#2510: טקסט של ``OSError``/``Exception`` אינו יוצא ללקוח — בכל האתרים ש-#595 לא כיסה.

הכלל (‏SECURITY.md, "מה הלקוח רואה כשפעולה נכשלת", #595): ללקוח עוברת הודעה רק
מחריגה שאנחנו כותבים. הטקסט הגולמי (נתיב, strerror) נכתב ליומן השרת, והלקוח מקבל
משפט קבוע — והכישלון עצמו נשאר גלוי (עיקרון 5): כל טסט כאן בודק גם שהמצב נשאר
"נכשל"/"לא נבדק", ולא רק שהטקסט נעלם.

כל חריגה מזויפת נושאת את ``SECRET`` — נתיב ו-strerror שאסור שיגיעו לתשובה.
"""

from __future__ import annotations

import asyncio
import errno
import types
from pathlib import Path

import pytest

from server.db import connect, set_setting

SECRET = "/var/lib/imagesrv/fake-secret-path"
TAIL = "הפרטים ביומן השרת"


def os_error(code: int = errno.EACCES) -> OSError:
    return OSError(code, "fake-strerror", SECRET)


def logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


# --- WoL: הסיבה על המסך מרשימה סגורה (#74 + #2510) ------------------------------

def test_a_socket_error_becomes_a_sentence_from_the_closed_list(caplog):
    from server.net import wol
    caplog.set_level("ERROR", logger="imagesrv.wol")

    def unreachable(_packet):
        raise os_error(errno.ENETUNREACH)

    reason = wol._try_send("aa:bb:cc:dd:ee:01", unreachable)
    assert "fake-strerror" not in reason and SECRET not in reason
    assert reason == wol.ERRNO_REASONS[errno.ENETUNREACH]
    assert "fake-strerror" in logged(caplog)


def test_an_unknown_errno_is_the_fixed_send_failed_sentence(caplog):
    from server.net import wol
    caplog.set_level("ERROR", logger="imagesrv.wol")

    def odd(_packet):
        raise os_error(errno.EIO)

    reason = wol._try_send("aa:bb:cc:dd:ee:01", odd)
    assert SECRET not in reason
    assert reason == wol.SEND_FAILED
    assert SECRET in logged(caplog)


def test_our_own_refusal_still_reaches_the_screen_word_for_word():
    """‏#74: "אין carrier על eth0" הוא משפט שלנו — הוא נשאר על המסך."""
    from server.net import wol

    def no_carrier(_packet):
        raise wol.WakeRefused("no-carrier: אין carrier על eth0")

    assert wol._try_send("aa:bb:cc:dd:ee:01", no_carrier) == "no-carrier: אין carrier על eth0"


def test_the_unpinned_sender_is_one_of_our_refusals():
    from server.net import wol
    assert wol._try_send("aa:bb:cc:dd:ee:01", wol.unpinned_sender).startswith(
        "רשת ההפצה לא הוגדרה")


def test_a_bad_mac_is_the_fixed_sentence_not_the_repr(caplog):
    from server.net import wol
    caplog.set_level("ERROR", logger="imagesrv.wol")
    reason = wol._try_send("zz:" + SECRET, lambda p: None)
    assert SECRET not in reason
    assert reason == wol.BAD_MAC


def test_the_wake_endpoint_hands_the_closed_list_sentence_to_the_console(tmp_path):
    from server.net import wol
    conn = connect(tmp_path / "imagesrv.db")
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('grp_B', 'B', 'build')")
    conn.execute("INSERT INTO machines (mac, group_id, suffix, added_at) VALUES "
                 "('aa:bb:cc:dd:ee:01', 'grp_B', '01', '2026-11-06T00:00:00+00:00')")
    conn.commit()

    def down(_packet):
        raise os_error(errno.ENETDOWN)

    result = wol.wake_group(conn, "grp_B", send=down)
    assert result == 0 and len(result.reasons) == 1
    assert SECRET not in result.reasons[0]
    assert result.reasons == [wol.ERRNO_REASONS[errno.ENETDOWN]]
    detail = conn.execute(
        "SELECT detail FROM journal WHERE event = 'wol_failed'").fetchone()["detail"]
    assert "fake-strerror" not in detail and SECRET not in detail


# --- USB ---------------------------------------------------------------------------

def test_an_unreadable_usb_sha_file_is_unavailable_with_a_fixed_reason(tmp_path, caplog):
    from server.console import usb
    caplog.set_level("WARNING", logger="imagesrv.console_usb")
    base = tmp_path / usb.USB_DIR
    base.mkdir()
    (base / usb.IMG).write_bytes(b"img")
    (base / usb.SHA).mkdir()            # קריאה של תיקייה — OSError עם הנתיב
    st = usb.usb_status(tmp_path)
    assert st["available"] is False
    assert st["reason"] == f"קובץ ה-SHA256 לא נקרא — {TAIL}"
    assert usb.SHA in logged(caplog)


# --- ספרייה: scrub -------------------------------------------------------------------

def test_an_unreadable_image_file_is_unreadable_with_a_fixed_error(tmp_path, monkeypatch, caplog):
    import server.library.library_scrub as library_scrub
    from server.library.images import ImageLibrary
    from test_library_scrub import _write_image
    caplog.set_level("WARNING", logger="imagesrv.library_scrub")
    _write_image(tmp_path)

    def cannot_read(path):
        raise os_error(errno.EIO)

    monkeypatch.setattr(library_scrub, "_sha256", cannot_read)
    image = library_scrub.scrub_library(ImageLibrary(tmp_path))["images"][0]
    assert [f["state"] for f in image["files"]] == ["unreadable", "unreadable"]
    assert not [f for f in image["files"] if SECRET in f["error"]]
    assert {f["error"] for f in image["files"]} == {library_scrub.UNREADABLE}
    assert SECRET in logged(caplog)


# --- UPS -----------------------------------------------------------------------------

def test_a_refused_upsd_connection_names_the_target_not_the_strerror(caplog):
    from server.system import ups
    caplog.set_level("WARNING", logger="imagesrv.ups")

    def refuse(address, timeout):
        raise os_error(errno.ECONNREFUSED)

    with pytest.raises(ups.UpsError) as caught:
        ups.read_vars(ups.Target("rack", "10.0.0.5"), connect=refuse)
    assert str(caught.value) == "אין חיבור ל-10.0.0.5:3493"
    assert "fake-strerror" in logged(caplog)


def test_a_foreign_read_failure_and_a_failed_poweroff_are_fixed_sentences(tmp_path, caplog):
    from server.system import ups
    caplog.set_level("WARNING", logger="imagesrv.ups")
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, ups.MODE, "net")
    set_setting(conn, ups.TARGET, "rack@10.0.0.5")

    def read(_target):
        raise os_error()

    m = ups.UpsMonitor(conn, stop_work=lambda why: [], poweroff=lambda: None, read=read)
    m.tick()
    state, detail = m.health()
    assert state == "bad" and SECRET not in detail
    assert ups.READ_FAILED in detail and m.snapshot()["error"] == ups.READ_FAILED

    low = {"ups.status": "OB DISCHRG LB", "battery.charge": "9"}

    def poweroff():
        raise RuntimeError(f"systemctl: fake-denied {SECRET}")

    m = ups.UpsMonitor(conn, stop_work=lambda why: [], poweroff=poweroff, read=lambda t: low)
    m.tick()
    state, detail = m.health()
    assert state == "bad" and SECRET not in detail and ups.SHUTDOWN_FAILED in detail
    assert "fake-denied" in logged(caplog)


def test_apply_returns_the_fixed_sentence_for_a_foreign_failure(tmp_path):
    from server.system import ups
    conn = connect(tmp_path / "imagesrv.db")

    def read(_target):
        raise os_error()

    hooks = {"write": lambda f: None, "start_usb": lambda: None, "stop_usb": lambda: None,
             "read": read, "sleep": lambda s: None}
    result = ups.apply(conn, "net", "rack@10.0.0.5", hooks, "noc")
    assert result["read"] is None and SECRET not in result["error"]
    assert result["error"] == ups.READ_FAILED


def test_the_ups_console_500_is_a_fixed_sentence(server, caplog):
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
    r = client.post("/api/console/ups", json={"mode": "usb"})
    assert r.status_code == 500
    assert r.json()["detail"] == f"הגדרת ה-UPS נכשלה ולא נשמרה — {TAIL}"
    assert SECRET in logged(caplog)


# --- בריאות: TFTP, ‏RAID, גיבוי ------------------------------------------------------

def test_an_unchecked_tftp_file_names_our_path_not_the_strerror(tmp_path, monkeypatch, caplog):
    from server.system import health
    caplog.set_level("WARNING", logger="imagesrv.health")

    def broken_resolve(self, strict=False):
        raise os_error(errno.ELOOP)

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    problems = health.tftp_file_problems(tmp_path)
    assert problems, "בדיקה שנכשלה היא בעיה גלויה, לא שקט"
    assert all("fake-strerror" not in p and SECRET not in p for p in problems), problems
    assert any(TAIL in p for p in problems)
    assert "fake-strerror" in logged(caplog)


def test_an_unreadable_raid_source_is_a_fixed_sentence(caplog):
    from server.system import raid_status
    caplog.set_level("WARNING", logger="imagesrv.raid")

    def unreadable():
        raise os_error()

    rows = raid_status.health_checks(unreadable, lambda: "level=raid1\n")
    assert rows[0]["state"] == "bad" and SECRET not in rows[0]["detail"]
    rows = raid_status.health_checks(lambda: "", unreadable)
    assert rows[0]["state"] == "unknown" and SECRET not in rows[0]["detail"]
    assert SECRET in logged(caplog)


def test_an_unreadable_backup_status_is_bad_with_a_fixed_sentence(tmp_path, caplog):
    from server.system import backup_job
    caplog.set_level("WARNING", logger="imagesrv.backup")
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, backup_job.SETTING, "/mnt/backup")
    (tmp_path / backup_job.STATUS_NAME).mkdir()   # קריאה של תיקייה — OSError
    row = backup_job.health_check(conn, tmp_path, "active")
    assert row["state"] == "bad"
    assert row["detail"] == f"קובץ הסטטוס של הגיבוי לא נקרא — {TAIL}"
    assert backup_job.STATUS_NAME in logged(caplog)


# --- פורטים --------------------------------------------------------------------------

def test_a_foreign_toggle_failure_is_the_fixed_detail(tmp_path, images_root, clock, caplog):
    pytest.importorskip("fastapi")
    from server.net import ports
    from test_port_toggles import FakeListeners, _build
    caplog.set_level("WARNING", logger="imagesrv.ports")

    class Stuck(FakeListeners):
        def request(self, port_id, want, timeout=None):
            raise TimeoutError(f"fake-timeout {SECRET}")

    t = _build(tmp_path, images_root, clock, Stuck())
    body = t["admin"].put("/api/console/ports/interserver", json={"enabled": False}).json()
    assert body["ok"] is False and body["applied"] is False
    assert SECRET not in body["detail"]
    assert body["detail"] == ports.TOGGLE_FAILED
    assert "fake-timeout" in logged(caplog)


def test_a_listener_that_cannot_start_raises_our_sentence(caplog):
    from server.net import ports
    caplog.set_level("WARNING", logger="imagesrv.ports")

    def start():
        raise os_error(errno.EADDRINUSE)

    mgr = ports.Listeners()
    mgr.add_threaded("interserver", start=start, stop=lambda obj: None, port=8443, hosts=[])
    with pytest.raises(ports.ListenerError) as caught:
        asyncio.run(mgr._open("interserver"))
    assert str(caught.value) == f"interserver: המאזין לא עלה — {TAIL}"
    assert "fake-strerror" in logged(caplog)


# --- DHCP / רשת ההפצה ----------------------------------------------------------------

def test_a_stored_dhcp_record_that_does_not_render_is_a_fixed_sentence(tmp_path, monkeypatch,
                                                                         caplog):
    from server.console import dhcp as console_dhcp
    caplog.set_level("WARNING", logger="imagesrv.console_dhcp")

    def bad_render(ctx, deploy):
        raise ValueError(f"fake-render {SECRET}")

    monkeypatch.setattr(console_dhcp, "render_dnsmasq", bad_render)
    ctx = types.SimpleNamespace(conn=connect(tmp_path / "imagesrv.db"))
    error = console_dhcp.apply_dnsmasq(ctx, {}, None, "test", "noc")
    assert error and TAIL in error and SECRET not in error
    assert "fake-render" in logged(caplog)


def test_an_unresolved_primary_names_the_host_not_the_resolver_text(caplog):
    from server.net import deploy_net
    caplog.set_level("WARNING", logger="server.net.deploy_net")

    def resolve(*_a):
        raise OSError(-2, f"fake-gaierror {SECRET}")

    ip, error = deploy_net.primary_ip_of("https://primary.lab:8443", resolve=resolve)
    assert ip is None and "primary.lab" in error and SECRET not in error
    assert "fake-gaierror" in logged(caplog)


def test_an_unwritable_grub_cfg_names_our_path_only(tmp_path, caplog):
    from server.net import deploy_net
    caplog.set_level("WARNING", logger="server.net.deploy_net")
    tftp = tmp_path / "tftp"
    tftp.write_text("not a folder")          # mkdir של grub/ נכשל ב-OSError
    error = deploy_net.write_grub_cfg("http://10.44.0.1:8080", tftp)
    assert error and error.endswith(TAIL)
    assert logged(caplog)


def test_netcfg_host_returns_our_sentence_and_logs_the_strerror(tmp_path, caplog):
    from server.net import netcfg_host
    caplog.set_level("WARNING", logger="imagesrv.netcfg_host")
    blocker = tmp_path / "interfaces.d"
    blocker.write_text("a file where the folder should be")
    error = netcfg_host.write_conf("eth9", "iface eth9 inet dhcp\n", blocker)
    assert error and error.startswith("לא ניתן לכתוב את") and error.endswith(TAIL)
    assert logged(caplog)
    state = netcfg_host.read_bond("bond9", tmp_path / "no-such-dir")
    assert state.checked is False and state.reason.endswith(TAIL)
