"""‏#1887 — לוג ה-DHCP/PXE של dnsmasq לכל מכונה: "לא שלח DHCP" ≠ "קיבל DHCP ולא טען" ≠ "טען ולא הגיע".

קריאה בלבד: הגדרות dnsmasq אינן נוגעות (#53). לוג שלא נקרא — "לא נבדק", לעולם לא "לא שלח DHCP".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db, pxe_log

MAC = "78:ac:c0:9b:18:87"
OTHER = "aa:bb:cc:00:18:87"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _ts(sec_ago: int) -> str:
    # ‏השעון נקרא כשהטסט רץ, לא כשהמודול נאסף: בחבילה המלאה (93 דק' ב-Testrunner,
    # ‏21/10) "לפני 50 שניות" מזמן האיסוף היה כבר מחוץ לחלון `DEFAULT_WINDOW_S`.
    return (_now() - timedelta(seconds=sec_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _lines() -> list[str]:
    return [
        f"{_ts(50)} imagesrv dnsmasq-dhcp[812]: 2219812 DHCPDISCOVER(eth1) {MAC.upper()}",
        f"{_ts(49)} imagesrv dnsmasq-dhcp[812]: 2219812 DHCPOFFER(eth1) 10.44.12.31 {MAC}",
        f"{_ts(49)} imagesrv dnsmasq-dhcp[812]: DHCPREQUEST(eth1) 10.44.12.31 {MAC}",
        f"{_ts(48)} imagesrv dnsmasq-dhcp[812]: DHCPACK(eth1) 10.44.12.31 {MAC} cloner-2",
        f"{_ts(47)} imagesrv dnsmasq-tftp[812]: sent /srv/imagectl/tftp/bootx64.efi to 10.44.12.31",
        f"{_ts(46)} imagesrv dnsmasq-tftp[812]: error 0 Early terminate received from 10.44.12.99",
        f"{_ts(45)} imagesrv dnsmasq-dhcp[812]: DHCPDISCOVER(eth1) {OTHER}",
        "-- cursor: s=abc;i=42",
    ]


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagectl.db")


def test_the_journal_lines_become_events():
    got = pxe_log.parse(_lines())
    assert [(e["mac"], e["kind"], e["ip"]) for e in got] == [
        (MAC, "discover", None), (MAC, "offer", "10.44.12.31"), (MAC, "request", "10.44.12.31"),
        (MAC, "ack", "10.44.12.31"), (None, "tftp", "10.44.12.31"), (None, "tftp_error", "10.44.12.99"),
        (OTHER, "discover", None)]
    assert got[4]["detail"] == "bootx64.efi" and got[0]["at"].endswith("+00:00")


def test_a_sweep_binds_tftp_to_the_mac_by_ip_and_keeps_the_cursor(conn):
    seen = []

    def reader(cursor):
        seen.append(cursor)
        return (_lines(), "s=abc;i=42") if len(seen) == 1 else ([], "s=abc;i=42")
    assert pxe_log.sweep(conn, reader) == 6                  # שגיאת TFTP ל-IP לא מוכר — נזנחה
    assert pxe_log.sweep(conn, reader) == 0
    assert seen == [None, "s=abc;i=42"]
    tftp = conn.execute("SELECT mac, detail FROM pxe_events WHERE kind = 'tftp'").fetchone()
    assert tftp["mac"] == MAC and tftp["detail"] == "bootx64.efi"


def test_the_verdict_tells_the_three_silences_apart(conn):
    now = _now()
    since = (now - timedelta(minutes=5)).isoformat()
    pxe_log.sweep(conn, lambda c: (_lines(), "x"))
    assert pxe_log.verdict(conn, MAC, since)["state"] == "no_hello"       # TFTP, בלי פירור סוכן
    assert pxe_log.verdict(conn, OTHER, since)["state"] == "no_tftp"      # DHCP בלבד
    assert pxe_log.verdict(conn, "de:ad:be:ef:00:01", since)["state"] == "no_dhcp"
    conn.execute("INSERT INTO boot_steps (mac, step, idx, at, first_at) VALUES (?, 'agent-hello', 8, ?, ?)",
                 (MAC, now.isoformat(), now.isoformat()))
    conn.execute("INSERT INTO boot_steps (mac, step, idx, at, first_at) VALUES (?, 'menu', 0, ?, ?)",
                 (OTHER, now.isoformat(), now.isoformat()))
    conn.commit()
    assert pxe_log.verdict(conn, MAC, since)["state"] == "hello"
    assert pxe_log.verdict(conn, OTHER, since)["state"] == "no_hello"     # GRUB נטען — פירור


def test_an_unread_log_is_unchecked_never_no_dhcp(conn):
    def broken(cursor):
        raise RuntimeError("journalctl rc=1: No journal files were opened due to insufficient permissions")
    assert pxe_log.sweep(conn, broken) == -1
    got = pxe_log.verdict(conn, MAC)
    assert got["state"] == "unchecked" and got["he"].startswith("לא נבדק")
    assert "insufficient permissions" in db.get_setting(conn, pxe_log.STATE_KEY)


def test_rows_are_capped_on_every_insert(conn, monkeypatch):
    monkeypatch.setattr(pxe_log, "KEEP_ROWS", 3)
    pxe_log.sweep(conn, lambda c: (_lines(), None))
    assert conn.execute("SELECT COUNT(*) FROM pxe_events").fetchone()[0] == 3


def test_only_the_journal_is_read_never_the_dhcp_settings():
    src = (Path(__file__).resolve().parent.parent / "server" / "pxe_log.py").read_text(encoding="utf-8")
    assert '["journalctl", "-u", "dnsmasq"' in src
    for forbidden in ("dnsmasq.d", "write_text", "systemctl", ".conf"):
        assert forbidden not in src, forbidden


def test_the_room_clock_reads_it_and_the_console_shows_it(server):
    app = (Path(__file__).resolve().parent.parent / "server" / "app.py").read_text(encoding="utf-8")
    assert "pxe_log.sweep" in app
    from server import registry
    ctx = server["ctx"]
    registry.add_machine(ctx.conn, MAC, "cloner-2", "grp_CLONERS", "test")
    pxe_log.sweep(ctx.conn, lambda c: (_lines(), "x"))
    got = server["admin"].get(f"/api/console/machines/{MAC}/data").json()["pxe"]
    assert got["state"] == "no_hello" and len(got["events"]) == 5


def test_a_machine_missing_from_the_wave_says_what_dhcp_saw(conn):
    from server import room
    pxe_log.sweep(conn, lambda c: (_lines(), "x"))
    got = room._with_pxe(conn, [{"mac": OTHER, "reason": "off"}, {"mac": MAC, "reason": "drawers"}])
    assert got[0]["pxe"] == {"state": "no_tftp", "he": pxe_log.STATES_HE["no_tftp"]}
    assert "pxe" not in got[1]                       # הצטרף — אין שאלת PXE
    js = (Path(__file__).resolve().parent.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert 'x.pxe && x.pxe.state !== "hello"' in js
