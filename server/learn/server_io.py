"""‏#1884 — דיסק הספרייה בשרת בזמן גל וקליטה: קצב, ו-I/O pressure. שהלמידה לא תאשים את הרשת.

**הראיה (20/10):** אימג' של 146GB (גדול מה-RAM) — השידור ירד ל-15–50MB/s כשה-NVMe המשותף ב-ESXi היה
עמוס (‏psi full 48%), ועלה ל-98 כשנרגע. המשכפל היה 95% idle. בלי המדידה הזו #1842 היה לומד קצב נמוך,
ו-#1846 היה מתריע "אבדן קפץ — סוויץ'/כבל".

**מה נמדד** — צילום בתחילת העבודה (‏`begin`) ובסופה (‏`end`), מהקרנל של השרת, קריאה בלבד:

* ‏`/proc/diskstats` של **ההתקן שעליו יושבת הספרייה** (‏`st_dev` של השורש → ‏major:minor): סקטורים
  שנקראו/נכתבו, ‏`io_ticks` (מ"ש שבהן ההתקן היה עסוק). ספרייה על NFS/SMB — אין התקן: ‏`device: null`.
* ‏`/proc/pressure/io` (‏PSI): ‏`some`/`full` ‏`total=` במיקרו-שניות. ‏`full` = הזמן שבו **כל** המשימות
  הרצות חיכו ל-I/O — "השרת ממתין לדיסק".

**התוצאה** (‏`server_io`, שורה לכל (סוג, מזהה)): ‏`read_mbps`, ‏`write_mbps`, ‏`util` (‏io_ticks לשעון הקיר),
‏`psi_some`/`psi_full` (שבר מזמן הזרם — ‏`seconds`, ‏`wall_s` של הגל; בלעדיו — השעון בין הצילומים),
‏`seconds`, ‏`device`. מונה שלא נקרא באחד הצילומים — **‏null**, לא 0 (עיקרון 5ב).

**‏`basis`:** ‏`server_disk` — ‏`psi_full` ≥ `SERVER_DISK_FULL` (‏⚠️ ניחוש מנומק: 10% מזמן הזרם שבו השרת כולו
חיכה לדיסק; הראיה היא 48%, ובשרת רגוע זה ≈0). ‏`ok` — נמדד ומתחת. ‏`null` — PSI לא נקרא ("לא נבדק").
גל `server_disk`: ‏`drive_rates` (‏#1842) לא לומד ממנו קצב, ו-`learn_netloss` (‏#1846) לא מתריע עליו — ראו
`bound`. יומן **`server_disk_slow`**. ‏לעולם אינו זורק ואינו חוסם: כשל הוא שורה בלי מספרים.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from server.db import _settle, _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.server_io")

PROC = Path("/proc")
#: שבר מזמן הזרם שבו כל המשימות חיכו ל-I/O — מעליו "השרת הוא צוואר הבקבוק". ראו את ראש המודול.
SERVER_DISK_FULL = 0.10
KINDS = frozenset({"wave", "capture"})
SECTOR = 512


def _device_of(root: str | Path, proc: Path) -> str | None:
    try:
        dev = os.stat(root).st_dev
        major, minor = os.major(dev), os.minor(dev)
        for line in (proc / "diskstats").read_text(encoding="ascii").splitlines():
            f = line.split()
            if len(f) >= 3 and f[0] == str(major) and f[1] == str(minor):
                return f[2]
    except (OSError, AttributeError, ValueError):
        pass
    return None


def _diskstats(proc: Path, device: str | None) -> dict:
    out = {"read_sectors": None, "write_sectors": None, "io_ms": None}
    if device is None:
        return out
    try:
        for line in (proc / "diskstats").read_text(encoding="ascii").splitlines():
            f = line.split()
            if len(f) >= 13 and f[2] == device:
                out = {"read_sectors": int(f[5]), "write_sectors": int(f[9]), "io_ms": int(f[12])}
                break
    except (OSError, ValueError):
        pass
    return out


def _psi(proc: Path) -> dict:
    out = {"psi_some_us": None, "psi_full_us": None}
    try:
        for line in (proc / "pressure" / "io").read_text(encoding="ascii").splitlines():
            kind, *fields = line.split()
            total = next((x.split("=", 1)[1] for x in fields if x.startswith("total=")), None)
            if kind in ("some", "full") and total is not None:
                out[f"psi_{kind}_us"] = int(total)
    except (OSError, ValueError):
        pass
    return out


def snapshot(root: str | Path, proc: Path = PROC) -> dict:
    device = _device_of(root, proc)
    return {"at": time.time(), "device": device, **_diskstats(proc, device), **_psi(proc)}


def begin(conn: sqlite3.Connection, kind: str, ref: str, root: str | Path,
          proc: Path = PROC) -> None:
    """הצילום בתחילת הגל/הקליטה. לעולם אינו זורק."""
    try:
        snap = snapshot(root, proc)
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json)"
                         " VALUES (?,?,?,?)", (kind, ref, now_iso(), json.dumps(snap)))
    except Exception:             # noqa: BLE001 — מדידה אינה מפילה גל
        log.exception("server I/O snapshot of %s %s failed", kind, ref)


def _delta(a: dict, b: dict, key: str) -> int | None:
    x, y = a.get(key), b.get(key)
    if not isinstance(x, int) or not isinstance(y, int) or y < x:
        return None
    return y - x


def measure(start: dict, end: dict, seconds: float | None = None) -> dict:
    """שני צילומים → התוצאה (ראו את ראש המודול). ‏`seconds` — משך הזרם; בלעדיו — השעון."""
    wall = end["at"] - start["at"]
    span = seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) \
        and seconds > 0 else wall
    same = start.get("device") is not None and start.get("device") == end.get("device")
    reads = _delta(start, end, "read_sectors") if same else None
    writes = _delta(start, end, "write_sectors") if same else None
    busy = _delta(start, end, "io_ms") if same else None
    some, full = _delta(start, end, "psi_some_us"), _delta(start, end, "psi_full_us")
    ok = span > 0                      # ‏util לשעון הקיר; השאר למשך הזרם
    out = {
        "device": start.get("device") if same else None,
        "seconds": round(span, 1) if span > 0 else None,
        "read_mbps": round(reads * SECTOR / span / 1e6, 1) if ok and reads is not None else None,
        "write_mbps": round(writes * SECTOR / span / 1e6, 1) if ok and writes is not None else None,
        "util": round(min(1.0, busy / 1000 / wall), 3) if wall > 0 and busy is not None else None,
        "psi_some": round(min(1.0, some / 1e6 / span), 3) if ok and some is not None else None,
        "psi_full": round(min(1.0, full / 1e6 / span), 3) if ok and full is not None else None,
    }
    out["basis"] = (None if out["psi_full"] is None
                    else "server_disk" if out["psi_full"] >= SERVER_DISK_FULL else "ok")
    return out


def end(conn: sqlite3.Connection, kind: str, ref: str, root: str | Path,
        seconds: float | None = None, proc: Path = PROC) -> dict | None:
    """הצילום בסוף, והתוצאה. ‏None — אין צילום פתיחה (גל שלא נבחר לו קצב). לעולם אינו זורק."""
    try:
        row = conn.execute("SELECT start_json FROM server_io WHERE kind = ? AND ref = ?",
                           (kind, ref)).fetchone()
        if row is None:
            return None
        result = measure(json.loads(row["start_json"]), snapshot(root, proc), seconds)
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute("UPDATE server_io SET ended_at = ?, result_json = ?, basis = ?"
                         " WHERE kind = ? AND ref = ?",
                         (now_iso(), json.dumps(result), result["basis"], kind, ref))
        if result["basis"] == "server_disk":
            journal(conn, "server_disk_slow", f"{kind} {ref} {_numbers(result)}")
        return result
    except Exception:             # noqa: BLE001
        log.exception("server I/O result of %s %s failed", kind, ref)
        return None


def bound(conn: sqlite3.Connection, session_id: str) -> bool:
    """האם הגל היה מוגבל בדיסק הספרייה — ראיה חיובית בלבד; לא נמדד = False."""
    try:
        row = conn.execute("SELECT basis FROM server_io WHERE kind = 'wave' AND ref = ?",
                           (session_id,)).fetchone()
        return row is not None and row["basis"] == "server_disk"
    except Exception:             # noqa: BLE001
        log.exception("server I/O basis of %s unreadable", session_id)
        return False


def _numbers(r: dict) -> str:
    def f(v, fmt):
        return "לא נמדד" if v is None else fmt.format(v)
    return (f"read={f(r['read_mbps'], '{:.1f}')}MB/s write={f(r['write_mbps'], '{:.1f}')}MB/s"
            f" psi_full={f(None if r['psi_full'] is None else 100 * r['psi_full'], '{:.0f}')}%"
            f" device={r['device'] or 'לא נקרא'}")


def health_checks(conn: sqlite3.Connection) -> list[dict]:
    """שורה אחת — לפי הגל האחרון שנמדד. בלי גל שנמדד — אין שורה."""
    from server.system.health import check                # noqa: PLC0415 — health מייבא אותנו
    try:
        row = conn.execute("SELECT ref, result_json FROM server_io WHERE kind = 'wave'"
                           " AND result_json IS NOT NULL ORDER BY ended_at DESC, rowid DESC"
                           " LIMIT 1").fetchone()
        if row is None:
            return []
        r = json.loads(row["result_json"])
        mbps = "לא נמדד" if r.get("read_mbps") is None else f"{r['read_mbps']:.0f} MB/s"
        full = None if r.get("psi_full") is None else round(100 * r["psi_full"])
        if r.get("basis") == "server_disk":
            return [check("server_disk", "דיסק הספרייה", "warn",
                          f"דיסק הספרייה איטי — {mbps} בגל האחרון; השרת חיכה לדיסק {full}% מזמן הזרם"
                          f" ({row['ref']}). הגל הזה לא נכנס ללמידת הקצב")]
        if r.get("basis") == "ok":
            return [check("server_disk", "דיסק הספרייה", "ok",
                          f"{mbps} בגל האחרון; המתנה לדיסק {full}% מזמן הזרם")]
        return [check("server_disk", "דיסק הספרייה", "off",
                      f"לא נבדק — לחץ ה-I/O (‏/proc/pressure/io) לא נקרא; קריאה {mbps}")]
    except Exception:             # noqa: BLE001
        log.exception("server I/O health failed")
        return [check("server_disk", "דיסק הספרייה", "off", "לא נבדק — הקריאה נכשלה")]
