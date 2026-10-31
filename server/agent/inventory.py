"""המלאי החומרתי שהמכונה מדווחת ב-hello (schema 2, ‏#720) — DMI, PCI, TPM.

זה המפתח להתאמת חבילות דרייברים (`server/library/drivers.py`): לא שם-דגם
כמו ב-FOG, שכל יצרן שם בשדה אחר, אלא ‏vendor:device של הבקרים עצמם —
מה שה-INF של הדרייבר מתאים עליו.

**נשמר מגורסת** בטבלת ``machine_inventory``: שורה חדשה נכתבת רק כשהמלאי
הקנוני השתנה, ו-``seen_at`` הוא מתי הגרסה הזו נראתה לראשונה. השורה
האחרונה לכל MAC היא המלאי הנוכחי; מה שלפניה הוא ההיסטוריה (כרטיס
רשת שהוחלף, דיסק שעבר לבקר אחר). ‏hello חוזר עם אותו מלאי אינו כותב
דבר — הבדיקה היא ``SELECT``, כמו החניקה של ``net_seen`` (#136).

מלאי פגום נזנח כמו שדה לא ידוע (מוסכמות רוחביות, ממשק 6): הוא **אינו**
דורס גרסה תקינה קודמת, ואינו נרשם כ"אין חומרה".
"""

from __future__ import annotations

import json
import re
import sqlite3

from server.db import _settle, _write_lock, now_iso, writing

#: ‏vendor:device:class — ארבע, ארבע ושש ספרות hex קטנות, כפי ש-sysfs
#: מדווח אותן בלי ``0x`` (‏`agent/lib/hw/inventory.sh`).
PCI_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}:[0-9a-f]{6}$")
DMI_FIELDS = ("sys_vendor", "product_name", "product_version", "board_name")
#: תקרה למספר ההתקנים ולאורך שדה DMI — ‏hello אינו מאומת, ומלאי בן
#: מגה-בייט אינו מלאי.
PCI_LIMIT = 64
DMI_LIMIT = 200


def well_formed(value: object) -> dict | None:
    """המלאי בצורתו הקנונית, או ``None`` כשאינו בצורה שהממשק מגדיר.

    קנוני = אותו קלט תמיד נותן אותו JSON: ‏PCI ממוין וללא כפילויות,
    שדות DMI מקוצצים, ‏``None`` לשדה ריק. ההשוואה "האם השתנה" נעשית על
    המחרוזת הזו.
    """
    if not isinstance(value, dict):
        return None
    dmi, pci, tpm = value.get("dmi"), value.get("pci"), value.get("tpm")
    if not isinstance(dmi, dict) or not isinstance(pci, list):
        return None
    out_dmi: dict[str, str | None] = {}
    for field in DMI_FIELDS:
        raw = dmi.get(field)
        if raw is not None and not isinstance(raw, str):
            return None
        text = raw.strip()[:DMI_LIMIT] if isinstance(raw, str) else ""
        out_dmi[field] = text or None
    if len(pci) > PCI_LIMIT:
        return None
    for item in pci:
        if not isinstance(item, str) or not PCI_RE.match(item):
            return None
    out_tpm: dict | None = None
    if tpm is not None:
        if not isinstance(tpm, dict) or not isinstance(tpm.get("present"), bool):
            return None
        version = tpm.get("version")
        if version is not None and not isinstance(version, str):
            return None
        out_tpm = {"present": tpm["present"],
                   "version": version[:16] if tpm["present"] and version else None}
    return {"dmi": out_dmi, "pci": sorted(set(pci)), "tpm": out_tpm}


def _text(inventory: dict) -> str:
    return json.dumps(inventory, sort_keys=True, ensure_ascii=False)


def record(conn: sqlite3.Connection, mac: str, inventory: dict) -> bool:
    """שומר גרסה חדשה כשהמלאי השתנה. מחזיר האם נכתבה שורה.

    ‏``inventory`` חייב להיות מה ש-`well_formed` החזיר — הקנוניות היא מה
    שהופך "לא השתנה" להשוואת מחרוזות. אותו מסלול נעילות כמו ``net_seen``
    (‏`_settle` → ‏`_write_lock` → ‏`writing`, ‏#457/#272): זו כתיבה שכיתה
    שלמה דורכת עליה באתחול הראשון אחרי פריסה.
    """
    text = _text(inventory)
    row = conn.execute(
        "SELECT inventory_json FROM machine_inventory WHERE mac = ?"
        " ORDER BY id DESC LIMIT 1", (mac,)
    ).fetchone()
    if row is not None and row["inventory_json"] == text:
        return False
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO machine_inventory (mac, seen_at, inventory_json)"
            " VALUES (?, ?, ?)", (mac, now_iso(), text))
    return True


def _row(row: sqlite3.Row) -> dict:
    return {"inventory": json.loads(row["inventory_json"]), "seen_at": row["seen_at"]}


def latest(conn: sqlite3.Connection, mac: str) -> dict | None:
    """המלאי הנוכחי של מכונה: ``{"inventory": …, "seen_at": …}`` או ``None``
    כשמעולם לא דיווחה (schema 1, או סוכן ישן)."""
    row = conn.execute(
        "SELECT inventory_json, seen_at FROM machine_inventory WHERE mac = ?"
        " ORDER BY id DESC LIMIT 1", (mac,)
    ).fetchone()
    return _row(row) if row is not None else None


def latest_all(conn: sqlite3.Connection) -> dict[str, dict]:
    """המלאי הנוכחי של כל מכונה שדיווחה, לפי MAC — ל"מתאים ל-N מכונות"
    בדף הדרייברים ולכרטיסי המכונות."""
    rows = conn.execute(
        "SELECT mac, inventory_json, seen_at FROM machine_inventory"
        " WHERE id IN (SELECT MAX(id) FROM machine_inventory GROUP BY mac)"
    ).fetchall()
    return {row["mac"]: _row(row) for row in rows}


def history(conn: sqlite3.Connection, mac: str) -> list[dict]:
    """כל הגרסאות של מכונה, מהחדשה לישנה."""
    rows = conn.execute(
        "SELECT inventory_json, seen_at FROM machine_inventory WHERE mac = ?"
        " ORDER BY id DESC", (mac,)
    ).fetchall()
    return [_row(row) for row in rows]
