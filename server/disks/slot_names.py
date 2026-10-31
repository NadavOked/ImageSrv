"""‏#704: השם שהמפעיל נותן לכל חיבור של כונן במכונה ("דיסק 1", "דיסק מיוחד").

**המפתח הוא החיבור, לא הדיסק** — דיסק שהוחלף באותה מגירה שומר את השם:

- יש לכונן `port` (‏SATA, ה-``ataN`` של ממשק 2) → ‏``sata:<port>``.
- אין `port` (‏NVMe, ‏USB, בקר SCSI) ויש `serial` → ‏``serial:<serial>``.
  הסוכן אינו מדווח היום מזהה חיבור יציב ל-NVMe (אין כתובת PCI ב-hello),
  ולכן כאן השם צמוד **לכונן**, והקונסולה אומרת זאת ליד השדה.
- בלי שניהם — אין מפתח ואין שם. לא ממציאים מזהה מ-``dev`` (‏``sda``
  מתחלף בין אתחולים).

לכל כונן מפתח **אחד**: כונן עם `port` אינו נקרא לפי הסידורי שלו, כי אז
השם היה נודד עם הדיסק — בדיוק מה שהבקשה פוסלת.

**השם הוא תווית בלבד.** הבחירה, ‏``target_slots`` והמספור נשארים לפי
`port`; בלי שם כל המסכים מציגים "דיסק N" כמו היום (‏SATA 0 → דיסק 1).
"""

from __future__ import annotations

import json
import re
import sqlite3

from server.disks import model_slots
from server.db import _write_lock, now_iso, writing

#: תקרת אורך בתווים — נכנס לתגית בגריד ולכותרת כרטיס בגואי הנייטיב.
NAME_MAX = 24
#: ‏``ataN`` על בקר שני יכול לעבור את 8 (‏``drawer_count``); 32 מספיק לכל לוח.
PORT_MAX = 32

_SATA = re.compile(r"sata:([1-9][0-9]?)\Z")
#: סידורי: ASCII מודפס בלי רווח ובלי ``|`` (המפריד של רשומות הגואי).
_SERIAL = re.compile(r"serial:([!-{}~]{1,80})\Z")
#: אותיות וספרות (כל שפה, ‏``\w``), רווח, ו- ``- . # ( ) /``. בלי מירכאות,
#: ``< > & ; \\ |`` ותווי בקרה: השם נכנס ל-HTML, ליומן ולקובץ המצב של הגואי.
_NAME = re.compile(r"[\w \-.#()/]+\Z")


def key_for(port, serial) -> str | None:
    """המפתח של כונן כפי שדווח ב-hello, או ``None`` כשאין לו מזהה יציב."""
    if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= PORT_MAX:
        return f"sata:{port}"
    if isinstance(serial, str) and _SERIAL.match(f"serial:{serial}"):
        return f"serial:{serial}"
    return None


def parse_key(raw) -> str:
    """מאמת מפתח שהגיע מהקונסולה. ‏ValueError = 400."""
    if isinstance(raw, str):
        m = _SATA.match(raw)
        if m and 1 <= int(m.group(1)) <= PORT_MAX:
            return raw
        if _SERIAL.match(raw):
            return raw
    raise ValueError(f"חיבור לא תקין — sata:1..{PORT_MAX} או serial:<מספר סידורי>")


def clean_name(raw) -> str:
    """מאמת שם: מחרוזת, לא ריקה אחרי חיתוך רווחים, עד ``NAME_MAX`` תווים,
    מהתווים המותרים בלבד. ‏ValueError = 400."""
    if not isinstance(raw, str):
        raise ValueError("השם חייב להיות טקסט")
    name = raw.strip()
    if not name:
        raise ValueError("השם ריק — כדי לחזור לברירת המחדל שלחו null")
    if len(name) > NAME_MAX:
        raise ValueError(f"השם ארוך מדי ({len(name)} תווים, המקסימום {NAME_MAX})")
    if not _NAME.match(name):
        raise ValueError("השם יכול להכיל אותיות, ספרות, רווח ו- - . # ( ) / בלבד")
    return name


def all_by_mac(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """כל השמות **האפקטיביים**, ``{mac: {slot: name}}`` — ‏#2001: שמות הדגם
    (‏`model_slots`) למכונה בלי מיפוי משלה, ושמות המכונה מעליהם."""
    out: dict[str, dict[str, str]] = {}
    for r in conn.execute("SELECT mac, slot, name FROM machine_slot_names ORDER BY mac, slot"):
        out.setdefault(r["mac"], {})[r["slot"]] = r["name"]
    for mac, mapping in model_slots.by_mac(conn).items():
        out[mac] = {**mapping["names"], **out.get(mac, {})}
    return out


def for_mac(conn: sqlite3.Connection, mac: str) -> dict[str, str]:
    own = {r["slot"]: r["name"] for r in conn.execute(
        "SELECT slot, name FROM machine_slot_names WHERE mac = ? ORDER BY slot", (mac,))}
    mapping = model_slots.for_mac(conn, mac)
    return {**mapping["names"], **own} if mapping else own


def resolve(names: dict[str, str], port, serial, pci=None) -> str | None:
    """השם של כונן אחד מתוך השמות של המכונה שלו, או ``None``. ‏#2001: ל-NVMe
    קודם לפי החיבור (‏``nvme:<PCI>``, החלון), ואחר כך לפי הסידורי (#704)."""
    if isinstance(pci, str) and port is None and f"nvme:{pci}" in names:
        return names[f"nvme:{pci}"]
    key = key_for(port, serial)
    return names.get(key) if key else None


def label(conn: sqlite3.Connection, mac: str, *, port=None, serial=None,
          dev: str | None = None) -> str | None:
    """השם לשורת יומן. כשהדיווח נושא רק ``dev`` (‏דיווח התקדמות של מגירה),
    החריץ והסידורי נלקחים מהדיסקים שהמכונה דיווחה ב-hello האחרון —
    ‏``sdb`` אינו "דיסק 2" לפי האות."""
    names = for_mac(conn, mac)
    if not names:
        return None
    pci = None
    if port is None and serial is None and dev:
        row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
        try:
            disks = json.loads(row["disks_json"]) if row and row["disks_json"] else []
        except ValueError:
            disks = []
        disk = next((d for d in disks if isinstance(d, dict) and d.get("dev") == dev), None)
        if disk is None:
            return None
        port, serial, pci = disk.get("port"), disk.get("serial"), disk.get("pci")
    return resolve(names, port, serial, pci)


def journal_suffix(name: str | None) -> str:
    """‏`` name="…"`` לסוף שורת יומן, או כלום — בלי שם השורה כמו היום."""
    return f' name="{name}"' if name else ""


def set_name(conn: sqlite3.Connection, mac: str, slot: str, name: str | None,
             user: str) -> None:
    """שומר (או מוחק, ``name=None``) שם. ‏LookupError = המכונה לא רשומה."""
    with _write_lock, writing(conn):
        if conn.execute("SELECT 1 FROM machines WHERE mac = ?", (mac,)).fetchone() is None:
            raise LookupError("מכונה לא רשומה")
        if name is None:
            conn.execute("DELETE FROM machine_slot_names WHERE mac = ? AND slot = ?", (mac, slot))
        else:
            conn.execute(
                "INSERT INTO machine_slot_names (mac, slot, name, set_at, set_by)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(mac, slot) DO UPDATE SET name = excluded.name,"
                " set_at = excluded.set_at, set_by = excluded.set_by",
                (mac, slot, name, now_iso(), user))
