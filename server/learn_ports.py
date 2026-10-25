"""‏#1844 — מגמת CRC וקישור לכל חיבור SATA: "כנראה כבל" או "הדיסק".

הכרעת נדב 19/10: השרת לומד את מחשבי השיכפול **ממדידה שלו**, ושום דבר אינו
נחסם בגלל הלמידה — זו המלצה כתומה, כמו הכתום של #652.

‏#432 ו-#1366 מסתכלים על דיסק **אחד** בסבב אחד: מונה ה-CRC עלה, הקישור איטי.
מה שהם אינם יכולים לומר הוא **מי אשם** — הכבל/המגירה או הדיסק. ההבחנה
דורשת היסטוריה: אותו חיבור שבעייתי עם דיסקים שונים הוא החיבור; דיסק שבעייתי
בכל חיבור שהוא נכנס אליו הוא הדיסק.

**מה נרשם** (‏`port_history`, שורה לכל (גל, מכונה, חיבור), בסוף כל גל של החדר —
‏`room._finish_wave`):

    ‏crc_delta   — בכמה עלה 199 בגל הזה (דוח הסוכן, #872); ‏None = לא נמדד
    ‏link_gbps / ‏link_max_gbps — קישור ה-SATA מה-hello של המכונה בסוף הגל (#432);
                  ‏None = לא נקרא
    ‏cable_fail  — הכתיבה בחיבור הזה נכשלה בגל עם סיבה `cable` (‏`disk_failures`,
                  ‏`ata_cause`: ‏ICRC, ‏"limiting SATA link speed", ‏interface fatal)

תצפית **בעייתית** = ‏`crc_delta > 0`, או קישור שנקרא מתחת למקסימום של הפורט,
או כשל כתיבה בסיבת כבל. "לא נמדד" אינו "תקין" ואינו "בעייתי" — הוא פשוט
אינו ראיה (עיקרון 5).

**ההכרעה** (‏`verdict`), על `WINDOW` התצפיות האחרונות:

    ‏port — באותו (מכונה, חיבור) היו בעייתיים ≥ `MIN_DISKS` דיסקים **שונים** (סידורי)
            → "כנראה כבל/מחבר, לא הדיסק"
    ‏disk — אותו סידורי היה בעייתי ב-≥ `MIN_DISKS` חיבורים **שונים** (מכונה+חיבור)
            → "הדיסק עצמו"

תצפית אחת אינה מכריעה דבר — כבל שזז פעם אחת, דיסק חם. "‏≥2 דיסקים שונים"
הוא ההגדרה ב-Issue עצמו, לא כיול.

**נקודת חיבור ל-#1921 (‏PR #1930, טבלת החומרה ‏`hw-issues.tsv`):** הסוכן שם
מסווג לכל מגירה מילים כמו `link_reset` / ‏`drawer_gone` מ-dmesg. כשזה ישולב,
המילים האלה הן עוד ראיה ל"תצפית בעייתית" — ב-`_problem` ובעמודה חדשה כאן, לא
בטבלה מקבילה. עד אז המודול נשען רק על מה שכבר קיים ב-rc7.

מה שנשמר: חומרה (MAC, חיבור, סידורי) ומדידות בלבד (#1924). לעולם אינו זורק.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from .db import _write_lock, now_iso, writing

log = logging.getLogger("imagectl.learn_ports")

#: כמה תצפיות אחרונות לכל חיבור / לכל דיסק נחשבות. ‏20 גלים הם כשבועיים של
#: עבודה בחדר בקצב של 2-3 גלים ביום: מספיק כדי שאותו חיבור יראה כמה דיסקים,
#: וקצר מספיק כדי שכבל שהוחלף יפסיק לצבוע תוך כמה שבועות. ⚠️ ניחוש מנומק.
WINDOW = 20
#: כמה דיסקים שונים (או חיבורים שונים) צריכים להיות בעייתיים — "≥2" מה-Issue.
MIN_DISKS = 2

PORT_HE = "כנראה כבל/מחבר, לא הדיסק — {n} דיסקים שונים עם CRC/קישור בחיבור הזה"
DISK_HE = "הדיסק עצמו — בעייתי ב-{n} חיבורים שונים"


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _gbps(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _problem(crc_delta, link_gbps, link_max_gbps, cable_fail) -> bool:
    """תצפית בעייתית — ראיה חיובית בלבד; ‏None (לא נמדד) אינו בעיה."""
    slow = link_gbps is not None and link_max_gbps is not None and link_gbps < link_max_gbps
    return bool((crc_delta or 0) > 0 or slow or cable_fail)


def record_wave(conn: sqlite3.Connection, session_id: str, members) -> int:
    """נקרא כשגל החדר הסתיים (‏`room._finish_wave`). שורה לכל מגירה עם סידורי
    וחיבור שהיא ב-hello של המכונה. מחזיר כמה נרשמו; לעולם אינו זורק."""
    try:
        rows = []
        for member in members:
            mac = member["mac"]
            got = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?",
                               (mac,)).fetchone()
            disks = json.loads(got["disks_json"] or "[]") if got else []
            by_dev = {d.get("dev"): d for d in disks if isinstance(d, dict)}
            failed = {r["port"] for r in conn.execute(
                "SELECT port FROM disk_failures WHERE session_id = ? AND mac = ?"
                " AND cause = 'cable'", (session_id, mac))}
            for t in json.loads(member["targets_json"] or "[]"):
                if not isinstance(t, dict):
                    continue
                disk = by_dev.get(t.get("dev")) or {}
                serial = t.get("serial") or disk.get("serial")
                port = _int(disk.get("port"))
                if not serial or port is None or disk.get("serial") != serial:
                    continue          # בלי זהות ומקום אין מה ללמוד
                rows.append((session_id, mac, port, serial, _int(t.get("crc_delta")),
                             _gbps(disk.get("link_gbps")), _gbps(disk.get("link_max_gbps")),
                             int(port in failed), now_iso()))
        if not rows:
            return 0
        with _write_lock, writing(conn):
            return sum(conn.execute(
                "INSERT OR REPLACE INTO port_history (session_id, mac, port, serial, crc_delta,"
                " link_gbps, link_max_gbps, cable_fail, at) VALUES (?,?,?,?,?,?,?,?,?)",
                row).rowcount for row in rows)
    except Exception:             # noqa: BLE001 — הלמידה אינה עוצרת סיום גל
        log.exception("recording port history of %s failed", session_id)
        return 0


def _bad(rows) -> list[sqlite3.Row]:
    return [r for r in rows
            if _problem(r["crc_delta"], r["link_gbps"], r["link_max_gbps"], r["cable_fail"])]


def verdict(conn: sqlite3.Connection, mac: str | None, port: object,
            serial: str | None) -> dict:
    """מה שנלמד על החיבור ועל הדיסק: ‏`{"port_suspect", "port_disks", "disk_suspect",
    "disk_ports", "text_he"}`. ‏`port_suspect`/‏`disk_suspect` — ‏None כשאין על מה
    לשאול (אין MAC/חיבור/סידורי) או שהשאילתה נכשלה; ‏False — נבדק, לא עבר את הסף."""
    out = {"port_suspect": None, "port_disks": None, "disk_suspect": None,
           "disk_ports": None, "text_he": None}
    try:
        port = _int(port)
        if mac and port is not None:
            rows = conn.execute(
                "SELECT * FROM port_history WHERE mac = ? AND port = ? ORDER BY at DESC, rowid DESC"
                " LIMIT ?", (mac, port, WINDOW)).fetchall()
            n = len({r["serial"] for r in _bad(rows)})
            out["port_disks"], out["port_suspect"] = n, n >= MIN_DISKS
        if serial:
            rows = conn.execute(
                "SELECT * FROM port_history WHERE serial = ? ORDER BY at DESC, rowid DESC LIMIT ?",
                (serial, WINDOW)).fetchall()
            n = len({(r["mac"], r["port"]) for r in _bad(rows)})
            out["disk_ports"], out["disk_suspect"] = n, n >= MIN_DISKS
    except Exception:             # noqa: BLE001 — אין הכרעה, לא "תקין"
        log.exception("port verdict for %s:%s failed", mac, port)
        return {**out, "port_suspect": None, "disk_suspect": None}
    texts = []
    if out["disk_suspect"]:
        texts.append(DISK_HE.format(n=out["disk_ports"]))
    if out["port_suspect"]:
        texts.append(PORT_HE.format(n=out["port_disks"]))
    out["text_he"] = " · ".join(texts) or None
    return out
