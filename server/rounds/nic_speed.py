"""מהירות כרטיס הרשת של מחשב שיכפול — מה שחוסם את הגל מהצד של המקבלים.

הכרעת נדב 15/10: תקרת הגל = הנמוכה מבין כרטיס ההפצה של השרת
(`drive_rates.read_link`) לבין הכרטיס האיטי מבין מחשבי השיכפול בגל. לכל
מכונה, לפי הסדר:

1. **ידני** — מה שהמנהל קבע במגירת המכונה (‏1/2.5/10Gb/s). גובר.
2. **מדווח** — ‏`speed_mbps` של כרטיס ההפצה מבדיקת המכונה שהסוכן שולח ב-hello
   (‏`probe.nic`, ‏`agent/lib/hw/probe.sh`). כרטיס ההפצה = הכרטיס היחיד עם
   קישור; ואם יש כמה — היחיד שעליו נבדקה התנגשות כתובת (‏`ip_conflict`,
   הסוכן בודק רק את הכרטיס שלו). שניים או אפס — לא יודעים איזה.
3. **הנחה** — אין ידני ואין דיווח תקין: **מניחים 1Gb/s** (הכרעת נדב,
   שמרני). ההנחה **מוגבלת** והמסך אומר שהיא הנחה, לא מדידה (‏`source:
   "assumed"` + ‏`why`) — "לא ידוע" אינו מוצג כ"נמדד".
"""

from __future__ import annotations

import sqlite3

from server.agent import probe
from server.db import get_setting, set_setting, set_settings

#: מפתח ההגדרה הידנית לכל MAC (טבלת `settings`, כמו `nicdesc:` של #1263).
PREFIX = "nicspeed:"
#: מה שהמנהל יכול לבחור, ב-Mb/s. "אוטומטי" = אין מפתח.
CHOICES = (1000, 2500, 10000)
#: מה שמניחים על מכונה שמהירותה לא ידועה (הכרעת נדב 15/10).
ASSUMED_MBPS = 1000


def manual(conn: sqlite3.Connection, mac: str) -> int | None:
    raw = get_setting(conn, PREFIX + mac)
    try:
        value = int(raw) if raw is not None else None
    except ValueError:
        return None
    return value if value in CHOICES else None


def set_manual(conn: sqlite3.Connection, mac: str, mbps: int | None) -> None:
    """‏`None` = חזרה לאוטומטי (המפתח נמחק)."""
    if mbps is None:
        set_settings(conn, {}, remove=[PREFIX + mac])
    elif mbps in CHOICES:
        set_setting(conn, PREFIX + mac, str(mbps))
    else:
        raise ValueError(f"מהירות לא נתמכת: {mbps}")


def reported(sample: dict | None) -> tuple[int | None, str | None]:
    """(‏Mb/s, ‏None) מהדגימה האחרונה, או (‏None, למה): ‏`no_probe` · ‏`no_nic`
    (הסוכן לא דיווח כרטיס עם קישור) · ‏`ambiguous` (כמה כרטיסים ואין דרך
    לדעת איזה מהם להפצה) · ‏`unknown` (‏null/‏-1/לא מספר)."""
    nics = (sample or {}).get("nic") if isinstance(sample, dict) else None
    if sample is None:
        return None, "no_probe"
    if not isinstance(nics, list):
        return None, "no_nic"
    nics = [n for n in nics if isinstance(n, dict)]
    if len(nics) > 1:
        nics = [n for n in nics if n.get("ip_conflict") is not None]
        if len(nics) != 1:
            return None, "ambiguous"
    if not nics:
        return None, "no_nic"
    mbps = nics[0].get("speed_mbps")
    if not isinstance(mbps, int) or isinstance(mbps, bool) or mbps <= 0:
        return None, "unknown"
    return mbps, None


def view(conn: sqlite3.Connection, mac: str, sample: dict | None = None,
         sampled: bool = False) -> dict:
    """‏`{"speed_mbps", "source", "why"}` — ‏`source`: ‏`manual` · ‏`reported` ·
    ‏`assumed` (‏`speed_mbps` = ‏`ASSUMED_MBPS`, ‏`why` = למה הדיווח לא שימש).
    ‏`sample` — הדגימה, כשהקורא כבר קרא אותה (`sampled=True`)."""
    chosen = manual(conn, mac)
    if chosen is not None:
        return {"speed_mbps": chosen, "source": "manual", "why": None}
    if not sampled:
        latest = probe.latest(conn, mac)
        sample = latest["probe"] if latest else None
    mbps, why = reported(sample)
    if mbps is not None:
        return {"speed_mbps": mbps, "source": "reported", "why": None}
    return {"speed_mbps": ASSUMED_MBPS, "source": "assumed", "why": why}


def slowest(conn: sqlite3.Connection, macs) -> dict | None:
    """המכונה האיטית בגל: ‏`{"mac", "speed_mbps", "source", "why"}`, ו-`assumed`
    — כל המכונות שמהירותן הונחה. ‏None — אין מכונות."""
    seen = [{"mac": mac, **view(conn, mac)} for mac in sorted({m for m in macs if m})]
    if not seen:
        return None
    # בשוויון — המונחת: השורה במסך אומרת שהתקרה נשענת על הנחה.
    low = min(seen, key=lambda m: (m["speed_mbps"], m["source"] != "assumed"))
    return {**low, "assumed": [m["mac"] for m in seen if m["source"] == "assumed"]}
