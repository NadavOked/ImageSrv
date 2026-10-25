"""‏#1888 — היסטוריית רשת ו-IP לכל מכונה: קישור, מוני שגיאות/זריקות לאורך זמן, ושינויי IP.

**מה כבר נאסף, ולא שוכפל:** ‏`wave_rates.drops` (‏#1758/#1842) שומר **לכל גל** את ‏`rcvbuf`/`inerrors` של כל
מכונה (‏`udp_drops` מהדיווח) — זו ההיסטוריה לכל גל; כאן רק נקראת (‏`wave_drops`).

**מה חסר, ונוסף:**

* **‏`ip_history`** — שורה כשה-IP של MAC **משתנה** (או נראה בפעם הראשונה), מתוך `db.net_seen` — אותה כתיבה,
  לא hello נוסף. ‏`ip`, ‏`at`, ‏`previous`. שינוי (לא ראשון) — יומן **`ip_changed`** (‏`<mac> <ישן> → <חדש>`):
  שימושי לחכירה כפולה ול-DHCP זר. ‏`KEEP_IPS` אחרונות למכונה (בכל הכנסה) + 365 יום.
* **‏`nic_history`** — המונים של בדיקת המכונה (‏`probe.nic[].stats`: ‏`rx_crc_errors`, ‏`rx_dropped`) ומהירות
  הקישור, ש-`probe.py` **דורס** בשורה האחרונה (שדות נדיפים). דגימה נשמרת כשהמהירות השתנתה, כשהמונה ירד
  (אתחול — סדרה חדשה), או כשעברה `NIC_GAP_S` מהדגימה הקודמת של אותו כרטיס — לא בכל רענון של 10 דק'.
  ‏`KEEP_NIC` למכונה (בכל הכנסה) + 90 יום.

ערך שלא נמדד נשמר ‏NULL — לא 0. חומרה ורשת בלבד (#1924). לעולם אינו זורק.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from .db import _settle, _write_lock, now_iso, writing

log = logging.getLogger("imagectl.net_history")

KEEP_IPS = 50
KEEP_NIC = 500
NIC_GAP_S = 3600


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def record_nic(conn: sqlite3.Connection, mac: str, probe: dict) -> int:
    """מ-`probe.record`, אחרי שהבדיקה נשמרה. מחזיר כמה דגימות נשמרו."""
    try:
        nics = probe.get("nic") if isinstance(probe, dict) else None
        if not isinstance(nics, list):
            return 0
        now = datetime.now(timezone.utc)
        rows = []
        for nic in nics:
            if not isinstance(nic, dict) or not isinstance(nic.get("name"), str):
                continue
            stats = nic.get("stats") if isinstance(nic.get("stats"), dict) else {}
            got = (_int(nic.get("speed_mbps")), _int(stats.get("rx_crc_errors")),
                   _int(stats.get("rx_dropped")))
            last = conn.execute("SELECT at, speed_mbps, rx_crc_errors, rx_dropped FROM nic_history"
                                " WHERE mac = ? AND iface = ? ORDER BY rowid DESC LIMIT 1",
                                (mac, nic["name"])).fetchone()
            if last is not None:
                try:
                    age = (now - datetime.fromisoformat(last["at"])).total_seconds()
                except (TypeError, ValueError):
                    age = NIC_GAP_S
                fell = any(a is not None and b is not None and b < a
                           for a, b in ((last["rx_crc_errors"], got[1]), (last["rx_dropped"], got[2])))
                if age < NIC_GAP_S and last["speed_mbps"] == got[0] and not fell:
                    continue
            rows.append((mac, nic["name"][:32], now_iso(), *got))
        if not rows:
            return 0
        _settle(conn)
        with _write_lock, writing(conn):
            conn.executemany("INSERT INTO nic_history (mac, iface, at, speed_mbps, rx_crc_errors,"
                             " rx_dropped) VALUES (?,?,?,?,?,?)", rows)
            conn.execute("DELETE FROM nic_history WHERE mac = ? AND rowid NOT IN (SELECT rowid FROM"
                         " nic_history WHERE mac = ? ORDER BY rowid DESC LIMIT ?)", (mac, mac, KEEP_NIC))
        return len(rows)
    except Exception:             # noqa: BLE001 — היסטוריה אינה מפילה hello
        log.exception("nic history of %s failed", mac)
        return 0


def ip_history(conn: sqlite3.Connection, mac: str, limit: int = 10) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT ip, previous, at FROM ip_history WHERE mac = ? ORDER BY rowid DESC LIMIT ?", (mac, limit))]


def nic_history(conn: sqlite3.Connection, mac: str, limit: int = 20) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT iface, at, speed_mbps, rx_crc_errors, rx_dropped FROM nic_history WHERE mac = ?"
        " ORDER BY rowid DESC LIMIT ?", (mac, limit))]


def wave_drops(conn: sqlite3.Connection, mac: str, limit: int = 10) -> list[dict]:
    """‏`wave_rates.drops[mac]` של הגלים האחרונים שהמכונה הייתה בהם — ‏`rcvbuf`/`inerrors` (‏null = לא נקרא)."""
    out = []
    for row in conn.execute("SELECT session_id, at, drops FROM wave_rates WHERE drops IS NOT NULL"
                            " ORDER BY rowid DESC LIMIT 200"):
        try:
            got = json.loads(row["drops"]).get(mac)
        except (TypeError, ValueError, AttributeError):
            continue
        if mac not in json.loads(row["drops"]):
            continue
        got = got if isinstance(got, dict) else {}
        out.append({"session_id": row["session_id"], "at": row["at"],
                    "rcvbuf": _int(got.get("rcvbuf")), "inerrors": _int(got.get("inerrors"))})
        if len(out) >= limit:
            break
    return out
