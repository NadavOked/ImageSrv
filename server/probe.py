"""בדיקת המכונה שהסוכן נושא ב-hello (‏#1049 שלב ב') — שמירה, והשערים.

שלב א' (‏`agent/lib/probe.sh`) אוסף: חשמל, שעון חומרה, מעבד, זיכרון,
טמפרטורות, רשת, PCI בלי דרייבר, קרנל, pstore, מפתח OEM, NVMe/SMART,
הצפנה. ‏#1048 מוסיף ``netprobe`` (כבל + LLDP) מ-hello כשדה אח, נשמר
באותה שורה. כל שדה הוא אחד משלושה מצבים (עיקרון 5): **נמדד** · **``None``** =
לא בדקנו · **``{"error": …}``** = ניסינו ונכשל. השרת שומר את שלושתם כמו
שהם — "לא נבדק" אינו "תקין", וגם לא "נכשל".

**נשמר מגורסת** בטבלת ``machine_probe`` כמו ``machine_inventory`` (#720):
שורה חדשה רק כשהתוכן **היציב** השתנה. חלק מהשדות משתנים בכל דגימה —
``probe_seconds``, שלושת מספרי ``rtc``, ``temp_c`` של כל אזור תרמי ומוני
``stats`` של כל כרטיס — והם **אינם** נכללים בהשוואה, אחרת כל רענון של
מטמון הסוכן (‏``PROBE_TTL``, 10 דק') היה "גרסה". הם כן **נשמרים**:
כשרק הם השתנו, השורה האחרונה מתעדכנת במקום (‏``sampled_at`` זז,
``seen_at`` נשאר), כי השערים — סטיית השעון, CRC — נמדדים מהדגימה
האחרונה ולא מהראשונה. ‏hello עם אותו JSON בדיוק (בתוך ה-TTL) אינו
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

from . import net_history
from .db import _settle, _write_lock, now_iso, writing

#: המפתחות שהממשק מגדיר (‏docs/interfaces.md, hello → probe). מפתח אחר
#: ברמה העליונה נזנח; מפתח חסר נשמר כ-``None`` = לא דווח.
KNOWN_KEYS = (
    "power", "rtc", "cpu", "memory", "thermal", "nic", "pci_without_driver",
    "kernel", "pstore", "oem_key", "disks", "encryption", "probe_seconds",
    "netprobe",  # #1048: cable + LLDP, sibling in hello, stored in this row
    "ntfs",  # #980 §6: hibernated/dirty per NTFS partition (ntfs-3g.probe), before a capture
)
#: תקרות — ‏hello אינו מאומת, ו-probe בן מגה-בייט אינו probe.
STR_LIMIT = 200
LIST_LIMIT = 64
KEYS_LIMIT = 64
DEPTH_LIMIT = 6

#: סטיית שעון שמעליה "סוללת BIOS חשודה" (הכרעת נדב, #1049: > 5 דק').
RTC_SKEW_SECONDS = 300
#: בלאי NVMe שמעליו אזהרה (‏percentage_used הוא אחוז מהחיים המובטחים).
NVME_USED_WARN = 90


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


def record(conn: sqlite3.Connection, mac: str, probe: dict) -> bool:
    """שומר. מחזיר האם נפתחה **גרסה** חדשה (שורה חדשה).

    ‏``probe`` חייב להיות מה ש-`well_formed` החזיר. אותו JSON → כלום;
    רק שדות נדיפים השתנו → עדכון השורה האחרונה במקום (``sampled_at``);
    תוכן יציב השתנה → שורה חדשה. אותו מסלול נעילות כמו ``net_seen``
    (‏`_settle` → ‏`_write_lock` → ‏`writing`).
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
        if row is not None and _text(stable(json.loads(row["probe_json"]))) == key:
            conn.execute(
                "UPDATE machine_probe SET probe_json = ?, sampled_at = ? WHERE id = ?",
                (text, now, row["id"]))
            new = False
        else:
            conn.execute(
                "INSERT INTO machine_probe (mac, seen_at, sampled_at, probe_json)"
                " VALUES (?, ?, ?, ?)", (mac, now, now, text))
            new = True
    # ‏#1888: מוני הכרטיס כאן נדרסים — ההיסטוריה שלהם נשמרת בנפרד (אחרי הנעילה; אינו זורק).
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


# --- השערים -----------------------------------------------------------------

def _measured(value: object) -> dict | None:
    """אובייקט **שנמדד**: לא ``None`` (לא נבדק) ולא ``{"error"}`` (נכשל).
    שני אלה אינם שער — "לא נבדק" ≠ "תקין", אבל גם ≠ "רע"."""
    if isinstance(value, dict) and "error" not in value:
        return value
    return None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _skew_text(seconds: int) -> str:
    s = abs(seconds)
    if s >= 86400:
        return f"{s // 86400} ימים"
    if s >= 3600:
        return f"{s // 3600} שעות"
    return f"{s // 60} דק'"


def verdicts(probe: dict | None) -> list[dict]:
    """מה שדורש עין ליד המכונה, לפי הדגימה האחרונה.

    כל פריט: ``{"key", "level": "warn"|"err", "text_he"}``. שדה שלא נמדד
    (``None``/``error``) אינו מייצר פריט — ולכן רשימה ריקה אומרת "לא
    נמצאה בעיה במה שנמדד", לא "הכול תקין".
    """
    out: list[dict] = []
    if not isinstance(probe, dict):
        return out

    power = _measured(probe.get("power"))
    if power is not None and power.get("on_battery") is True:
        out.append({"key": "battery", "level": "err",
                    "text_he": "המחשב על סוללה — שיכפול/קליטה עלולים להיקטע"})

    rtc = _measured(probe.get("rtc"))
    skew = _int(rtc.get("skew_seconds")) if rtc is not None else None
    if skew is not None and abs(skew) > RTC_SKEW_SECONDS:
        out.append({"key": "rtc", "level": "warn",
                    "text_he": f"שעון החומרה סוטה ב-{_skew_text(skew)} — סוללת BIOS חשודה"})

    for disk in probe.get("disks") or []:
        if not isinstance(disk, dict):
            continue
        smart = _measured(disk.get("nvme_smart"))
        if smart is None:
            continue
        name = str(disk.get("name") or "?")
        crit, media, used = (_int(smart.get("critical_warning")),
                             _int(smart.get("media_errors")), _int(smart.get("percentage_used")))
        if (crit is not None and crit != 0) or (media is not None and media > 0):
            out.append({"key": f"nvme:{name}", "level": "err",
                        "text_he": f"NVMe {name}: " + " · ".join(
                            p for p in (f"critical_warning={crit}" if crit else "",
                                        f"{media} שגיאות מדיה" if media else "") if p)})
        elif used is not None and used >= NVME_USED_WARN:
            out.append({"key": f"nvme:{name}", "level": "warn",
                        "text_he": f"NVMe {name}: {used}% מהבלאי המובטח נוצל"})

    pstore = _measured(probe.get("pstore"))
    if pstore is not None and pstore.get("crashed") is True:
        out.append({"key": "pstore", "level": "warn",
                    "text_he": "המחשב קרס לפני האתחול הזה"})

    for nic in probe.get("nic") or []:
        if not isinstance(nic, dict):
            continue
        name = str(nic.get("name") or "?")
        conflict = _measured(nic.get("ip_conflict"))
        if conflict is not None and conflict.get("duplicate") is True:
            out.append({"key": f"ip_conflict:{name}", "level": "err",
                        "text_he": f"כפילות IP על {name} — מחשב אחר ברשת עונה על הכתובת"})
        stats = _measured(nic.get("stats"))
        crc = _int(stats.get("rx_crc_errors")) if stats is not None else None
        if crc is not None and crc > 0:
            out.append({"key": f"crc:{name}", "level": "warn",
                        "text_he": f"{crc} שגיאות CRC על {name} — כבל או פורט חשודים"})

    # #1048: כבל פגום הוא שער. מתג שלא משדר (lldp.unheard) אינו תקלה.
    netprobe = _measured(probe.get("netprobe"))
    cable = _measured(netprobe.get("cable")) if netprobe is not None else None
    if cable is not None and cable.get("status") in ("open", "short"):
        pair, he, length = "?", ("פתוח" if cable["status"] == "open" else "קצר"), None
        for item in cable.get("pairs") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").lower()
            if code in ("open", "short"):
                pair = str(item.get("pair") or "?")
                he = "פתוח" if code == "open" else "קצר"
                length = item.get("length_m")
                break
        text = f"כבל פגום: זוג {pair} {he}"
        if isinstance(length, int) and not isinstance(length, bool):
            text += f" ב-{length} מטר"
        elif isinstance(length, float) and math.isfinite(length):
            text += f" ב-{int(length)} מטר"
        out.append({"key": "cable", "level": "err", "text_he": text})
    return out
