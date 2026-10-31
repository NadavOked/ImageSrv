"""‏#2001: מיפוי החיבורים לפי דגם — "כל מחשבי הדגם" בחלון "מיפוי חיבורים".

**הדגם** הוא ``dmi.product_name`` מהמלאי האחרון שהמכונה שלחה ב-hello
(‏`server/agent/inventory.py`) — אותו מפתח שה-USB שומר לפיו (#1962). מכונה בלי
מלאי, או עם ``product_name`` ריק, אין לה דגם: רק "המחשב הזה".

**מיפוי** = החיבורים הפעילים בסדר החיבורים (``drawer_slots.order``) ושם
לכל אחד (‏``{key: name}``; בלי שם — "דיסק N" לפי הסדר).

**העדיפות: מכונה ← דגם ← ברירת מחדל.** למכונה יש מיפוי משלה כשהעמודה
``machines.drawer_slots`` אינה ``NULL``; אז השמות הם רק שלה. בלעדיו — מיפוי
הדגם, והשמות של הדגם עם שמות ישנים של המכונה מעליהם (#704, שנשמרו לפני
החלון). בלי שניהם — "מגירה k = ‏sata:k" כמו קודם.

שמירה "לכל מחשבי הדגם" מנקה את המיפוי האישי של כל מחשב רשום מהדגם —
המתג אומר "חל על N מחשבים", ומחשב שמיפוי ישן שלו גובר היה הופך את המשפט
לשקר.
"""

from __future__ import annotations

import json
import sqlite3

from server.disks import drawer_slots
from server.db import _write_lock, now_iso, writing


def _product(inventory_json: str | None) -> str | None:
    try:
        inv = json.loads(inventory_json) if inventory_json else None
    except ValueError:
        return None
    dmi = inv.get("dmi") if isinstance(inv, dict) else None
    name = dmi.get("product_name") if isinstance(dmi, dict) else None
    return (name.strip() or None) if isinstance(name, str) else None


def model_of(conn: sqlite3.Connection, mac: str) -> str | None:
    row = conn.execute("SELECT inventory_json FROM machine_inventory WHERE mac = ?"
                       " ORDER BY id DESC LIMIT 1", (mac,)).fetchone()
    return _product(row["inventory_json"]) if row else None


def models_by_mac(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT mac, inventory_json FROM machine_inventory"
        " WHERE id IN (SELECT MAX(id) FROM machine_inventory GROUP BY mac)").fetchall()
    return {r["mac"]: m for r in rows if (m := _product(r["inventory_json"]))}


def _map(row: sqlite3.Row) -> dict:
    return {"slots": json.loads(row["slots_json"]), "names": json.loads(row["names_json"]),
            "set_at": row["set_at"], "set_by": row["set_by"]}


def get(conn: sqlite3.Connection, model: str | None) -> dict | None:
    if not model:
        return None
    row = conn.execute("SELECT * FROM model_slot_maps WHERE model = ?", (model,)).fetchone()
    return _map(row) if row else None


def all_maps(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["model"]: _map(r) for r in conn.execute("SELECT * FROM model_slot_maps")}


def for_mac(conn: sqlite3.Connection, mac: str) -> dict | None:
    """מיפוי הדגם שחל על המכונה — ``None`` כשיש לה מיפוי משלה, כשאין לה דגם,
    או כשלדגם אין מיפוי."""
    row = conn.execute("SELECT drawer_slots FROM machines WHERE mac = ?", (mac,)).fetchone()
    if row is None or row["drawer_slots"] is not None:
        return None
    if conn.execute("SELECT 1 FROM model_slot_maps LIMIT 1").fetchone() is None:
        return None
    return get(conn, model_of(conn, mac))


def by_mac(conn: sqlite3.Connection) -> dict[str, dict]:
    """``{mac: מיפוי הדגם}`` לכל מכונה רשומה שהוא חל עליה — שאילתה לכל תשובת API."""
    maps = all_maps(conn)
    if not maps:
        return {}
    models = models_by_mac(conn)
    out = {}
    for r in conn.execute("SELECT mac FROM machines WHERE drawer_slots IS NULL"):
        model = models.get(r["mac"])
        if model in maps:
            out[r["mac"]] = maps[model]
    return out


def machines_of(conn: sqlite3.Connection, model: str) -> list[str]:
    models = models_by_mac(conn)
    return [r["mac"] for r in conn.execute("SELECT mac FROM machines ORDER BY mac")
            if models.get(r["mac"]) == model]


def clean(slots_raw, names_raw) -> tuple[list[str], dict[str, str]]:
    """מאמת מיפוי מהחלון. ‏ValueError = 400, בעברית שהמפעיל קורא."""
    from server.disks import slot_names   # slot_names מייבא את המודול הזה
    slots = sorted(drawer_slots.parse(slots_raw), key=drawer_slots.order)
    if names_raw is None:
        names_raw = {}
    if not isinstance(names_raw, dict):
        raise ValueError("השמות חייבים להיות {חיבור: שם}")
    names = {}
    for key, raw in names_raw.items():
        if key not in slots:
            raise ValueError(f"שם לחיבור שאינו פעיל: {key}")
        if raw is not None:
            names[key] = slot_names.clean_name(raw)
    seen: dict[str, str] = {}
    for i, key in enumerate(slots):
        label = names.get(key) or f"דיסק {i + 1}"
        if label.casefold() in seen:
            raise ValueError(f'שתי שורות עם אותו שם ("{label}") — כל שם צריך להיות שונה,'
                             " אחרת לא יודעים לאיזה דיסק התכוונת בסבב")
        seen[label.casefold()] = key
    return slots, names


class MachineMaps(Exception):
    """‏שמירה לדגם שהייתה מוחקת מיפוי אישי של מחשבים מהדגם — נדב 22/10: שואלים
    קודם. ``macs`` = המחשבים שיש להם מיפוי משלהם."""

    def __init__(self, macs: list[str]):
        super().__init__(f"ל-{len(macs)} מחשבים מהדגם יש מיפוי משלהם — הוא יוחלף")
        self.macs = macs


def _replace_machine(conn, mac, slots, names, user, now) -> None:
    conn.execute("UPDATE machines SET drawer_slots = ? WHERE mac = ?",
                 (json.dumps(slots) if slots is not None else None, mac))
    if slots is not None:
        conn.execute("UPDATE machines SET drawer_count = ? WHERE mac = ?", (len(slots), mac))
    conn.execute("DELETE FROM machine_slot_names WHERE mac = ?", (mac,))
    for key, name in (names or {}).items():
        conn.execute("INSERT INTO machine_slot_names (mac, slot, name, set_at, set_by)"
                     " VALUES (?, ?, ?, ?, ?)", (mac, key, name, now, user))


def save(conn: sqlite3.Connection, mac: str, scope: str, slots: list[str] | None,
         names: dict[str, str] | None, user: str, replace_machine_maps: bool = False) -> dict:
    """שומר (או, ``slots=None``, מחזיר לברירת המחדל) את המיפוי בהיקף שנבחר.
    ‏LookupError = המכונה לא רשומה; ValueError = אין לה דגם (היקף ``model``);
    ‏MachineMaps = שמירה לדגם תמחק מיפוי אישי, ולא נשלח ``replace_machine_maps``."""
    if conn.execute("SELECT 1 FROM machines WHERE mac = ?", (mac,)).fetchone() is None:
        raise LookupError("מכונה לא רשומה")
    now = now_iso()
    if scope == "machine":
        with _write_lock, writing(conn):
            _replace_machine(conn, mac, slots, names, user, now)
        return {"scope": "machine", "model": None, "machines": [mac]}
    model = model_of(conn, mac)
    if model is None:
        raise ValueError("המחשב לא דיווח דגם (DMI) — אפשר לשמור רק למחשב הזה")
    macs = machines_of(conn, model)
    if slots is not None and not replace_machine_maps:
        own = [m for m in macs if conn.execute(
            "SELECT 1 FROM machines WHERE mac = ? AND drawer_slots IS NOT NULL", (m,)).fetchone()]
        if own:   # שום דבר לא נמחק בשקט — הקונסולה שואלת ושולחת שוב עם האישור
            raise MachineMaps(own)
    with _write_lock, writing(conn):
        if slots is None:
            conn.execute("DELETE FROM model_slot_maps WHERE model = ?", (model,))
        else:
            conn.execute(
                "INSERT INTO model_slot_maps (model, slots_json, names_json, set_at, set_by)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(model) DO UPDATE SET"
                " slots_json = excluded.slots_json, names_json = excluded.names_json,"
                " set_at = excluded.set_at, set_by = excluded.set_by",
                (model, json.dumps(slots), json.dumps(names or {}, ensure_ascii=False), now, user))
            for other in macs:   # "חל על N מחשבים" — מיפוי אישי ישן אינו גובר עליו
                _replace_machine(conn, other, None, None, user, now)
                conn.execute("UPDATE machines SET drawer_count = ? WHERE mac = ?",
                             (len(slots), other))
    return {"scope": "model", "model": model, "machines": macs}


def _complete(slots, names, disks):
    done = drawer_slots.resolve_ordinals(slots, disks)
    if done == slots:
        return None
    moved = dict(zip(slots, done))
    return done, {moved.get(k, k): v for k, v in names.items()}


def complete_ordinals(conn: sqlite3.Connection, mac: str, disks) -> bool:
    """‏hello: ‏"NVMe n" שנוסף ידנית מקבל את כתובת ה-PCI כשהמכונה מדווחת NVMe
    n-י (נדב 22/10: "הכתובת נשלמת אוטומטית"). כתיבה רק כשיש מה להשלים."""
    changed = False
    row = conn.execute("SELECT drawer_slots FROM machines WHERE mac = ?", (mac,)).fetchone()
    if row is not None and row["drawer_slots"] and "nvme#" in row["drawer_slots"]:
        slots = json.loads(row["drawer_slots"])
        own = {r["slot"]: r["name"] for r in conn.execute(
            "SELECT slot, name FROM machine_slot_names WHERE mac = ?", (mac,))}
        got = _complete(slots, own, disks)
        if got:
            with _write_lock, writing(conn):
                _replace_machine(conn, mac, got[0], got[1], "hello", now_iso())
            changed = True
    elif conn.execute("SELECT 1 FROM model_slot_maps WHERE slots_json LIKE '%nvme#%'"
                      " LIMIT 1").fetchone() is not None:
        model = model_of(conn, mac)
        current = get(conn, model)
        got = _complete(current["slots"], current["names"], disks) if current else None
        if got:
            with _write_lock, writing(conn):
                conn.execute("UPDATE model_slot_maps SET slots_json = ?, names_json = ?"
                             " WHERE model = ?",
                             (json.dumps(got[0]), json.dumps(got[1], ensure_ascii=False), model))
            changed = True
    return changed
