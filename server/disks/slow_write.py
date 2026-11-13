"""#2956: דיסק שנפסל כאיטי בבדיקת הכתיבה שלפני הזרם נרשם ביומן, בשמו.

11/11 בעבודה: דיסק אחד (96MB/s מול 209 של האחרים) נפסל ולא נכתב, והפסילה הייתה קיימת **רק** ב-
`session_members.targets_json` (`write_preflight.state = "slow"`). ביומן הופיעה רק אזהרת ה-CRC
(`room_precheck_warned`), והמפעיל לא ראה למה המגירה לא נכתבה.

הסוכן מכריע (`write_speed_verdict`, writecheck.sh) ומדווח את ההכרעה ב-`targets[].write_preflight`;
השרת רק מעתיק אותה ליומן — **פעם אחת** לדיסק, במעבר: הדיווח חוזר כל 2 שניות, ולכן שורה נרשמת
רק כשהדיווח הקודם של אותו `dev` עוד לא נשא `slow`. הסף והיחס נשארים בנוסח של הסוכן (`reason`) ולא
מחושבים כאן מחדש: הסוכן יודע סוג דיסק (מוצק/מכני), הסף שבתוקף במניפסט וכמה נמדדו — השרת לא.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from server.db import journal
from server.disks import drawer_slots, model_slots, slot_names

log = logging.getLogger("imagesrv.slow_write")

EVENT = "disk_write_slow"
_MAX_MODEL = 64


def _is_mbps(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _pre(target: object) -> dict | None:
    pre = target.get("write_preflight") if isinstance(target, dict) else None
    return pre if isinstance(pre, dict) else None


def _slow_devs(raw: str | None) -> set[str]:
    """ה-`dev`-ים שהדיווח הקודם כבר נשא להם `slow`. `targets_json` פגום = קבוצה ריקה: עדיף שורה
    כפולה על שורה שלא נרשמה (עיקרון 5)."""
    try:
        targets = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return set()
    return {t["dev"] for t in targets if isinstance(t, dict) and isinstance(t.get("dev"), str)
            and (_pre(t) or {}).get("state") == "slow"}


def _hello_disk(conn: sqlite3.Connection, mac: str, dev: str) -> dict:
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    try:
        disks = json.loads(row["disks_json"]) if row is not None and row["disks_json"] else []
    except (TypeError, ValueError):
        return {}
    return next((d for d in disks if isinstance(d, dict) and d.get("dev") == dev), {})


def _drawer(conn: sqlite3.Connection, mac: str, disk: dict) -> int | None:
    """מספר המגירה כפי שהמסך מציג (#2677/#2945): אותו מיפוי כמו `room.py` -- `drawer_slots.effective`
    (המכונה, אחריה הדגם, אחריה "מגירה k = sata:k") ו-`drawer_of`. **לא** ה-`port`: חריץ ה-ATA אינו
    מספר המגירה. None = אין מיפוי (מכונה לא רשומה, או החריץ אינו בין המגירות) -- אין מספר מומצא."""
    row = conn.execute("SELECT drawer_count, drawer_slots FROM machines WHERE mac = ?", (mac,)).fetchone()
    if row is None:
        return None
    try:
        mapping = model_slots.for_mac(conn, mac)
        slots = drawer_slots.effective(row["drawer_count"], row["drawer_slots"],
                                       mapping["slots"] if mapping else None)
        return drawer_slots.drawer_of(slots, disk)
    except (TypeError, ValueError, KeyError):
        return None


def _word(value: object, limit: int = 64) -> str:
    """טקסט חיצוני (דגם/סידורי מה-hello) לשורת יומן אחת. `?` = לא דווח."""
    text = " ".join(value.split())[:limit] if isinstance(value, str) else ""
    return text or "?"


def journal_new(conn: sqlite3.Connection, session_id: str, mac: str,
                previous_json: str | None, targets: list) -> None:
    """שורת יומן לכל יעד שבדיווח הזה **נפסל לראשונה** כאיטי. לעולם אינו זורק: כשל ברישום אינו
    עוצר דיווח התקדמות של סבב (`log.exception`, לא שתיקה)."""
    try:
        already = _slow_devs(previous_json)
        for t in targets:
            pre = _pre(t)
            dev = t.get("dev") if isinstance(t, dict) else None
            if pre is None or pre.get("state") != "slow" or not isinstance(dev, str) or dev in already:
                continue
            others = [p["mbps"] for o in targets if o is not t and (p := _pre(o)) is not None
                      and p.get("state") == "ok" and _is_mbps(p.get("mbps"))]
            disk = _hello_disk(conn, mac, dev)
            serial = t.get("serial") if isinstance(t.get("serial"), str) and t.get("serial") else disk.get("serial")
            port = disk.get("port")
            reason = " ".join(str(pre.get("reason") or "").split())
            detail = (f"{mac} {dev} serial={''.join(_word(serial).split())}"
                      f" port={port if isinstance(port, int) and not isinstance(port, bool) else '?'}"
                      f" drawer={_drawer(conn, mac, disk) or '?'}"
                      f"{slot_names.journal_suffix(slot_names.label(conn, mac, dev=dev))}"
                      f" mbps={pre['mbps'] if _is_mbps(pre.get('mbps')) else '?'}"
                      f" others={','.join(map(str, others)) or '-'}"
                      f" model={_word(disk.get('model'), _MAX_MODEL)} — {reason}")
            journal(conn, EVENT, detail, machine=mac, round_id=session_id)
    except Exception:   # noqa: BLE001 — הרישום ליומן אינו עוצר דיווח התקדמות
        log.exception("journaling a slow write check of %s in %s failed", mac, session_id)
