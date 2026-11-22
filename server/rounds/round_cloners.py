"""#3898 (שלב 1): משכפל נמצא בסבב אחד בלבד -- מי תפוס, ובאיזה סבב.

אין טבלה: התפיסה היא `target_slots_json` של הסבבים הפעילים עצמם, ולכן אין מצב שנשכח לשחרר (סבב שנסגר משחרר לבד).
האטומיות -- שני מפעילים שפותחים יחד סבב על אותו משכפל -- היא ה-`_OPEN_LOCK` של `room.open_round`: הבדיקה והכתיבה של
הסבב החדש בתוכו, ולכן הבדיקה של השני רואה את הסבב של הראשון. כל פותחי הסבבים הם תהליכונים של תהליך השרת הזה.
(טבלה עם PRIMARY KEY הייתה אכיפה חזקה יותר, והיא דורשת bump של `SCHEMA_VERSION` ועשרות טסטים שמקבעים אותו; ראו
`handoffs/design-3898-parallel-rounds.md` §4 ושאלה 6.)
"""

from __future__ import annotations

import json
import sqlite3

from server.agent import registry
from server.core import machine_names
from server.rounds.room_state import active_rounds


def busy(conn: sqlite3.Connection, macs) -> dict[str, sqlite3.Row]:
    """mac -> שורת הסבב הפעיל שהמשכפל בו, לאלה מ-`macs` שתפוסים. קריאה בלבד."""
    found: dict[str, sqlite3.Row] = {}
    for row in active_rounds(conn):
        for item in json.loads(row["target_slots_json"] or "[]"):
            if item.get("mac") in macs:
                found.setdefault(item["mac"], row)
    return found


def label(conn: sqlite3.Connection, mac: str) -> str:
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    return machine_names.label("cloner", row["suffix"]) if row else (registry.normalize_mac(mac) or mac)
