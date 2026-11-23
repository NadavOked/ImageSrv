"""בדיקת המכונה שהסוכן נושא ב-hello (#1049 שלב ב') — שמירה, והשערים.

שלב א' (`agent/lib/hw/probe.sh`) אוסף: חשמל, שעון חומרה, מעבד, זיכרון,
טמפרטורות, רשת, PCI בלי דרייבר, קרנל, pstore, מפתח OEM, NVMe/SMART,
הצפנה. #1048 מוסיף ``netprobe`` (כבל + LLDP) מ-hello כשדה אח, נשמר
באותה שורה. כל שדה הוא אחד משלושה מצבים (עיקרון 5): **נמדד** · **``None``** =
לא בדקנו · **``{"error": …}``** = ניסינו ונכשל. השרת שומר את שלושתם כמו
שהם — "לא נבדק" אינו "תקין", וגם לא "נכשל".

**נשמר מגורסת** בטבלת ``machine_probe`` כמו ``machine_inventory`` (#720):
שורה חדשה רק כשהתוכן **היציב** השתנה. חלק מהשדות משתנים בכל דגימה —
``probe_seconds``, שלושת מספרי ``rtc``, ``temp_c`` של כל אזור תרמי ומוני
``stats`` של כל כרטיס — והם **אינם** נכללים בהשוואה, אחרת כל רענון של
מטמון הסוכן (``PROBE_TTL``, 10 דק') היה "גרסה". הם כן **נשמרים**:
כשרק הם השתנו, השורה האחרונה מתעדכנת במקום (``sampled_at`` זז,
``seen_at`` נשאר), כי השערים — סטיית השעון, CRC — נמדדים מהדגימה
האחרונה ולא מהראשונה. hello עם אותו JSON בדיוק (בתוך ה-TTL) אינו
כותב דבר — ``SELECT`` בלבד, כמו #136.

probe **חסר או פגום** (לא אובייקט) נזנח כשדה לא ידוע ואינו דורס גרסה
תקינה קודמת. השערים (``verdicts``) **מוצגים** — כרטיס המכונה והתראה
בעמוד הבית — ואינם עוצרים סבב: עצירה היא הכרעה נפרדת של נדב.
"""

from __future__ import annotations

import copy
import json
import math
import sqlite3

from server.brain import probejudge
from server.learn import net_history
from server.db import _settle, _write_lock, now_iso, writing

#: המפתחות שהממשק מגדיר (docs/architecture/interfaces.md, hello → probe). מפתח אחר
#: ברמה העליונה נזנח; מפתח חסר נשמר כ-``None`` = לא דווח.
KNOWN_KEYS = (
    "power", "rtc", "cpu", "memory", "thermal", "nic", "pci_without_driver",
    "kernel", "pstore", "oem_key", "disks", "encryption", "probe_seconds",
    "netprobe",  # #1048: cable + LLDP, sibling in hello, stored in this row
    "netwatch",  # #3064: what the distribution port hears (IGMP queries, foreign multicast), sibling in hello; volatile
    "ntfs",  # #980 §6: hibernated/dirty per NTFS partition (ntfs-3g.probe), before a capture
    "display",  # #2849: fb0 size + DRM connectors (EDID bytes, first mode) -- the KVM switch case
)
#: תקרות — hello אינו מאומת, ו-probe בן מגה-בייט אינו probe.
STR_LIMIT = 200
LIST_LIMIT = 64
KEYS_LIMIT = 64
DEPTH_LIMIT = 6


def _clean(value: object, depth: int = 0) -> object:
    """עותק קנוני ומקוצץ: מחרוזות ≤200, רשימות ≤64, אובייקטים ≤64
    מפתחות ממוינים, עומק ≤6. טיפוס שאינו JSON — ``None``."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:STR_LIMIT]
    if depth >= DEPTH_LIMIT:
        return None
    if isinstance(value, list):
        return [_clean(v, depth + 1) for v in value[:LIST_LIMIT]]
    if isinstance(value, dict):
        items = sorted((str(k)[:STR_LIMIT], v) for k, v in value.items())[:KEYS_LIMIT]
        return {k: _clean(v, depth + 1) for k, v in items}
    return None


def well_formed(value: object) -> dict | None:
    """ה-probe בצורתו הקנונית, או ``None`` כשאינו אובייקט כלל.

    שדה חסר → ``None`` (לא דווח); שדה שאינו בממשק נזנח. השדות עצמם
    אינם מאומתים לעומק — שלושת המצבים (ערך / ``None`` / ``{"error"}``)
    הם של הסוכן, והשרת מציג אותם כפי שהם.
    """
    if not isinstance(value, dict):
        return None
    return {key: _clean(value.get(key)) for key in KNOWN_KEYS}


def _text(probe: dict) -> str:
    return json.dumps(probe, sort_keys=True, ensure_ascii=False)


def stable(probe: dict) -> dict:
    """ה-probe בלי מה שמשתנה בכל דגימה — זה מה שמושווה בין גרסאות."""
    out = copy.deepcopy(probe)
    out.pop("probe_seconds", None)
    out.pop("netwatch", None)   # #3064: counters of one listening window -- new every time, never a "version"
    rtc = out.get("rtc")
    if isinstance(rtc, dict):
        for k in ("hwclock_epoch", "system_epoch", "skew_seconds"):
            rtc.pop(k, None)
    if isinstance(out.get("thermal"), list):
        for zone in out["thermal"]:
            if isinstance(zone, dict):
                zone.pop("temp_c", None)
    if isinstance(out.get("nic"), list):
        for nic in out["nic"]:
            if isinstance(nic, dict):
                nic.pop("stats", None)
    return out


def record(conn: sqlite3.Connection, mac: str, probe: dict, history: bool = True) -> bool:
    """שומר. מחזיר האם נפתחה **גרסה** חדשה (שורה חדשה).

    #2487: ``history=False`` — מכונה לא רשומה: גם תוכן יציב שהשתנה מעדכן את
    השורה הקיימת במקום (שורה אחת ל-MAC), ולא פותח גרסה.

    ``probe`` חייב להיות מה ש-`well_formed` החזיר. אותו JSON → כלום;
    רק שדות נדיפים השתנו → עדכון השורה האחרונה במקום (``sampled_at``);
    תוכן יציב השתנה → שורה חדשה. אותו מסלול נעילות כמו ``net_seen``
    (`_settle` → `_write_lock` → `writing`).
    """
    text, key = _text(probe), _text(stable(probe))
    row = conn.execute(
        "SELECT id, probe_json FROM machine_probe WHERE mac = ?"
        " ORDER BY id DESC LIMIT 1", (mac,)
    ).fetchone()
    if row is not None and row["probe_json"] == text:
        return False
    now = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        if row is not None and (not history or _text(stable(json.loads(row["probe_json"]))) == key):
            conn.execute(
                "UPDATE machine_probe SET probe_json = ?, sampled_at = ? WHERE id = ?",
                (text, now, row["id"]))
            new = False
        else:
            conn.execute(
                "INSERT INTO machine_probe (mac, seen_at, sampled_at, probe_json)"
                " VALUES (?, ?, ?, ?)", (mac, now, now, text))
            new = True
    # #1888: מוני הכרטיס כאן נדרסים — ההיסטוריה שלהם נשמרת בנפרד (אחרי הנעילה; אינו זורק).
    net_history.record_nic(conn, mac, probe)
    return new


def _row(row: sqlite3.Row) -> dict:
    return {"probe": json.loads(row["probe_json"]), "seen_at": row["seen_at"],
            "sampled_at": row["sampled_at"]}


def latest(conn: sqlite3.Connection, mac: str) -> dict | None:
    """הדגימה הנוכחית: ``{"probe", "seen_at", "sampled_at"}`` או ``None``
    כשמעולם לא דיווחה (סוכן ישן)."""
    row = conn.execute(
        "SELECT probe_json, seen_at, sampled_at FROM machine_probe WHERE mac = ?"
        " ORDER BY id DESC LIMIT 1", (mac,)
    ).fetchone()
    return _row(row) if row is not None else None


def latest_all(conn: sqlite3.Connection) -> dict[str, dict]:
    """הדגימה הנוכחית של כל מכונה שדיווחה, לפי MAC — לכרטיסי המכונות."""
    rows = conn.execute(
        "SELECT mac, probe_json, seen_at, sampled_at FROM machine_probe"
        " WHERE id IN (SELECT MAX(id) FROM machine_probe GROUP BY mac)"
    ).fetchall()
    return {row["mac"]: _row(row) for row in rows}


def history(conn: sqlite3.Connection, mac: str) -> list[dict]:
    """כל הגרסאות של מכונה, מהחדשה לישנה."""
    rows = conn.execute(
        "SELECT probe_json, seen_at, sampled_at FROM machine_probe WHERE mac = ?"
        " ORDER BY id DESC", (mac,)
    ).fetchall()
    return [_row(row) for row in rows]


def memory_bytes(probe: dict | None) -> int | None:
    """#977 §7: סך הזיכרון שהמכונה מדדה (MemTotal), לשדה `memory_bytes` ב-`GET /machines`.

    ``None`` = לא ידוע: מעולם לא דיווחה, `memory` לא נבדק או `{"error"}`, או ערך שאינו מספר חיובי.
    אסור להפוך "לא ידוע" ל-0 (עיקרון 5): 0 היה נקרא "אין זיכרון"."""
    mem = probe.get("memory") if isinstance(probe, dict) else None
    total = mem.get("total_bytes") if isinstance(mem, dict) else None
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        return None
    return total


# --- השערים -----------------------------------------------------------------

def verdicts(probe: dict | None) -> list[dict]:
    """מה שדורש עין ליד המכונה, לפי הדגימה האחרונה: ``{"key", "level": "warn"|"err", "text_he"}``.

    #3662 (S41, B71): **ההכרעה היא של המוח** (`server/brain/probejudge.py`: מה נחשב סוללה, סטיית שעון, בלאי NVMe, קריסה, כפילות IP,
    CRC וכבל פגום, באיזו חומרה ובאילו ספים). כאן רק המעטפת שהקונסולה והשערים כבר קוראים: אותו שם, אותו פלט."""
    return probejudge.verdicts(probe)
