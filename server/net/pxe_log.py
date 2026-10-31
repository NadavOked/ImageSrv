"""‏#1887 — מה ש-dnsmasq ראה לפני GRUB: להבחין בין "נעצר במסך F1/כבוי" לבין כשל PXE/TFTP.

**הבעיה.** משכפל שנעצר על F1 לא מגיע ל-PXE בכלל; משכפל עם בעיית רשת שולח DISCOVER ולא ממשיך. היום שניהם
נראים כמו "WoL ← שתיקה" (#1847, ‏#1858) — הסקה משתיקה, לא ראיה (עיקרון 5א).

**קריאה בלבד.** ⚠️ שום דבר כאן אינו נוגע בהגדרות dnsmasq (‏#53: שינוי DHCP דורש הכרעה). dnsmasq רושם
ל-journal כברירת מחדל (בלי `quiet-dhcp`/`quiet-tftp` — ‏`server/net/dhcp.py` אינו כותב אותם) את ‏DHCPDISCOVER/
OFFER/REQUEST/ACK עם ה-MAC, ואת ה-TFTP (‏`sent <קובץ> to <IP>`) עם ה-IP בלבד — ולכן TFTP נקשר ל-MAC
לפי ה-OFFER/ACK האחרון לאותו IP. הקורא (‏`journal_reader`) מריץ ‏`journalctl -u dnsmasq -o short-iso
--show-cursor` מהסמן האחרון (‏`settings.pxe_log_cursor`); בלי סמן — `PXE_FIRST_SINCE` אחורה.

**נשמר** (‏`pxe_events`): ‏`mac`, ‏`kind` (‏`discover`/`offer`/`request`/`ack`/`tftp`/`tftp_error`), ‏`at`
(השעה ש-journald רשם), ‏`ip`, ‏`detail` (שם הקובץ שנשלח / השגיאה — שמות קבצי האתחול שלנו, לא תוכן דיסק).
‏`sweep` רץ משעון החדר כל `EVERY_S`; קריאה שנכשלה נרשמת (‏`settings.pxe_log_state`) ומחזירה "לא נבדק".

**ההכרעה** (‏`verdict`), למכונה מאז רגע (ההערה האחרונה / חצי שעה):
‏`no_dhcp` — "לא שלח DHCP (כנראה BIOS/F1/כבוי)"; ‏`no_tftp` — "קיבל DHCP ולא טען את קובץ האתחול";
‏`no_hello` — "טען את קובץ האתחול ולא הגיע לסוכן (GRUB/קרנל)"; ‏`hello` — הגיע; ‏`unchecked` — הלוג לא
נקרא (לא "לא שלח"). מידע בלבד — שום דבר אינו נחסם או מוחלט אוטומטית.

תקרות: ‏`KEEP_ROWS` בכל הכנסה, ‏`housekeeping` — ‏`KEEP_DAYS`. לעולם אינו זורק.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Callable

from server.db import _settle, _write_lock, get_setting, now_iso, set_setting, writing

log = logging.getLogger("imagesrv.pxe_log")

EVERY_S = 60
KEEP_DAYS = 14
KEEP_ROWS = 20000
PXE_FIRST_SINCE = "-15min"
CURSOR_KEY = "pxe_log_cursor"
STATE_KEY = "pxe_log_state"
#: חלון ברירת המחדל לשאלה "מה קרה בעלייה האחרונה" כשאין הערה ידועה.
DEFAULT_WINDOW_S = 1800

_TS = r"(?P<at>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[.,]\d+)?(?:[+-]\d\d:?\d\d|Z))"
_DHCP = re.compile(_TS + r"\s+\S+\s+dnsmasq(?:-dhcp)?\[\d+\]:\s+(?:\d+\s+)?"
                   r"DHCP(?P<kind>DISCOVER|OFFER|REQUEST|ACK)\([^)]*\)\s+"
                   r"(?:(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+)?(?P<mac>[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
_TFTP_SENT = re.compile(_TS + r"\s+\S+\s+dnsmasq(?:-tftp)?\[\d+\]:\s+sent\s+(?P<file>\S+)\s+to\s+"
                        r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})")
_TFTP_ERR = re.compile(_TS + r"\s+\S+\s+dnsmasq(?:-tftp)?\[\d+\]:\s+(?P<err>(?:error|file)\b.*?)"
                       r"(?:to|from|for)\s+(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s*$")
_CURSOR = re.compile(r"^-- cursor:\s*(\S+)")

STATES_HE = {
    "no_dhcp": "לא שלח DHCP — כנראה עצר במסך ה-BIOS (F1), כבוי, או בלי קישור",
    "no_tftp": "קיבל DHCP ולא טען את קובץ האתחול — PXE/TFTP/Secure Boot",
    "no_hello": "טען את קובץ האתחול ולא הגיע לסוכן — GRUB/קרנל",
    "hello": "הגיע לסוכן",
    "unchecked": "לא נבדק — לוג ה-DHCP לא נקרא",
}


def _iso(stamp: str) -> str | None:
    try:
        got = datetime.fromisoformat(stamp.replace(",", "."))
    except ValueError:
        return None
    return got.astimezone(timezone.utc).isoformat(timespec="seconds") if got.tzinfo else None


def parse(lines: list[str]) -> list[dict]:
    """שורות journal → אירועים. ‏TFTP — ‏`mac: None` עד שנקשר (‏`_bind`)."""
    out = []
    for line in lines:
        m = _DHCP.search(line)
        if m:
            at = _iso(m["at"])
            if at:
                out.append({"mac": m["mac"].lower(), "kind": m["kind"].lower(), "at": at,
                            "ip": m["ip"], "detail": None})
            continue
        m = _TFTP_SENT.search(line)
        if m:
            at = _iso(m["at"])
            if at:
                out.append({"mac": None, "kind": "tftp", "at": at, "ip": m["ip"],
                            "detail": m["file"].rsplit("/", 1)[-1][:80]})
            continue
        m = _TFTP_ERR.search(line)
        if m:
            at = _iso(m["at"])
            if at:
                out.append({"mac": None, "kind": "tftp_error", "at": at, "ip": m["ip"],
                            "detail": m["err"].strip()[:120]})
    return out


def _bind(conn: sqlite3.Connection, events: list[dict]) -> list[dict]:
    """TFTP → ה-MAC שה-IP הזה הוצע/אושר לו לאחרונה (באצווה, ואחרת בטבלה). לא נקשר — נזנח."""
    last: dict[str, str] = {}
    out = []
    for e in events:
        if e["kind"] in ("offer", "ack") and e["ip"]:
            last[e["ip"]] = e["mac"]
        if e["mac"] is None:
            mac = last.get(e["ip"])
            if mac is None:
                row = conn.execute("SELECT mac FROM pxe_events WHERE ip = ? AND kind IN ('offer','ack')"
                                   " ORDER BY at DESC, rowid DESC LIMIT 1", (e["ip"],)).fetchone()
                mac = row["mac"] if row else None
            if mac is None:
                continue
            e = {**e, "mac": mac}
        out.append(e)
    return out


def journal_reader(cursor: str | None) -> tuple[list[str], str | None]:
    """‏journalctl, קריאה בלבד. זורק על כשל (‏`sweep` תופס ורושם "לא נבדק")."""
    cmd = ["journalctl", "-u", "dnsmasq", "-o", "short-iso", "--no-pager", "--show-cursor"]
    cmd += [f"--after-cursor={cursor}"] if cursor else [f"--since={PXE_FIRST_SINCE}"]
    got = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                         stdin=subprocess.DEVNULL, check=False)
    if got.returncode != 0:
        raise RuntimeError(f"journalctl rc={got.returncode}: {got.stderr.strip()[:200]}")
    lines = got.stdout.splitlines()
    new_cursor = next((m.group(1) for ln in reversed(lines) if (m := _CURSOR.match(ln))), cursor)
    return lines, new_cursor


def sweep(conn: sqlite3.Connection, reader: Callable = journal_reader) -> int:
    """קורא מה שנוסף מאז הסמן, ושומר. מחזיר כמה אירועים נשמרו; ‏-1 — הקריאה נכשלה."""
    try:
        lines, cursor = reader(get_setting(conn, CURSOR_KEY))
        events = _bind(conn, parse(lines))
        _settle(conn)
        with _write_lock, writing(conn):
            conn.executemany("INSERT INTO pxe_events (mac, kind, at, ip, detail) VALUES (?,?,?,?,?)",
                             [(e["mac"], e["kind"], e["at"], e["ip"], e["detail"]) for e in events])
            conn.execute("DELETE FROM pxe_events WHERE rowid NOT IN"
                         " (SELECT rowid FROM pxe_events ORDER BY rowid DESC LIMIT ?)", (KEEP_ROWS,))
        if cursor:
            set_setting(conn, CURSOR_KEY, cursor)
        set_setting(conn, STATE_KEY, "ok " + now_iso())
        return len(events)
    except Exception as exc:      # noqa: BLE001 — קריאת לוג אינה מפילה את שעון החדר
        log.warning("dnsmasq log not read: %s", exc)
        try:
            set_setting(conn, STATE_KEY, f"failed {now_iso()} {str(exc)[:200]}")
        except Exception:         # noqa: BLE001
            log.exception("pxe log state not saved")
        return -1


def readable(conn: sqlite3.Connection) -> bool:
    return (get_setting(conn, STATE_KEY) or "").startswith("ok ")


def verdict(conn: sqlite3.Connection, mac: str, since: str | None = None) -> dict:
    """מה קרה למכונה מאז `since` (ISO; בלעדיו — `DEFAULT_WINDOW_S` אחורה). ראו את ראש המודול."""
    try:
        if since is None:
            since = (datetime.now(timezone.utc) - timedelta(seconds=DEFAULT_WINDOW_S)).isoformat(
                timespec="seconds")
        events = [dict(r) for r in conn.execute(
            "SELECT kind, at, ip, detail FROM pxe_events WHERE mac = ? AND julianday(at) >= julianday(?)"
            " ORDER BY at, rowid", (mac, since))]
        # הפירור האחרון של שביל האתחול (#400) — ראיה חיובית שהמכונה כבר אחרי ה-TFTP.
        crumb = conn.execute("SELECT step FROM boot_steps WHERE mac = ? AND julianday(at) >= julianday(?)",
                             (mac, since)).fetchone()
        kinds = {e["kind"] for e in events}
        if crumb is not None and crumb["step"].startswith("agent-"):
            state = "hello"
        elif crumb is not None or "tftp" in kinds:
            state = "no_hello"
        elif not readable(conn):
            state = "unchecked"
        elif not kinds & {"discover", "request", "offer", "ack"}:
            state = "no_dhcp"
        else:
            state = "no_tftp"
        return {"state": state, "he": STATES_HE[state], "since": since, "events": events[-20:]}
    except Exception:             # noqa: BLE001
        log.exception("pxe verdict of %s failed", mac)
        return {"state": "unchecked", "he": STATES_HE["unchecked"], "since": since, "events": []}


def since_last_wake(conn: sqlite3.Connection, mac: str) -> str | None:
    """רגע ההערה האחרונה (‏`wol_wakes`, #1847), או None."""
    row = conn.execute("SELECT sent_at FROM wol_wakes WHERE mac = ? ORDER BY sent_epoch DESC LIMIT 1",
                       (mac,)).fetchone()
    return row["sent_at"] if row else None
