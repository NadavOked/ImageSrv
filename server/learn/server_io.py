"""#1884 — דיסק הספרייה בשרת בזמן גל וקליטה: קצב, ו-I/O pressure. שהלמידה לא תאשים את הרשת.

**הראיה (20/10):** אימג' של 146GB (גדול מה-RAM) — השידור ירד ל-15–50MB/s כשה-NVMe המשותף ב-ESXi היה
עמוס (psi full 48%), ועלה ל-98 כשנרגע. המשכפל היה 95% idle. בלי המדידה הזו #1842 היה לומד קצב נמוך,
ו-#1846 היה מתריע "אבדן קפץ — סוויץ'/כבל".

**מה נמדד** — צילום בתחילת העבודה (`begin`) ובסופה (`end`), מהקרנל של השרת, קריאה בלבד:

* `/proc/diskstats` של **ההתקן שעליו יושבת הספרייה** (`st_dev` של השורש → major:minor): סקטורים
  שנקראו/נכתבו, `io_ticks` (מ"ש שבהן ההתקן היה עסוק). ספרייה על NFS/SMB — אין התקן: `device: null`.
* `/proc/pressure/io` (PSI): `some`/`full` `total=` במיקרו-שניות. `full` = הזמן שבו **כל** המשימות
  הרצות חיכו ל-I/O — "השרת ממתין לדיסק".

**התוצאה** (`server_io`, שורה לכל (סוג, מזהה)): `read_mbps`, `write_mbps`, `util` (io_ticks לשעון הקיר),
`psi_some`/`psi_full` (שבר מזמן הזרם — `seconds`, `wall_s` של הגל; בלעדיו — השעון בין הצילומים),
`seconds`, `device`. מונה שלא נקרא באחד הצילומים — **null**, לא 0 (עיקרון 5ב).

**`basis` (#3365):** נקבע לפי `diskstats` של **ההתקן של הספרייה** בלבד, לא לפי PSI. ה-PSI הוא של כל המערכת: ב-LabRoot
(15/11) דיסק השורש (journald, jbd2) הדליק אותו ב-5 גלים מתוך 6, ודיסק הספרייה היה ב-util של 0.3%. `server_disk` —
`util` ≥ `SERVER_DISK_UTIL` **וגם** await (זמן ממוצע לבקשה שהסתיימה בחלון) ≥ `SERVER_DISK_AWAIT_MS`. ⚠️ שני הספים
הם ניחוש מנומק: בגלים של 15/11 דיסק הספרייה היה ב-util של 27-41% ו-await של 10.4-12.9ms, והשולח, שקורא `pread`
סינכרוני, איבד 9.3-13.9% מהמעבר הראשון; אין עדיין גל שבו הדיסק בריא ונמדד. `ok` — נמדד ומתחת (גם בלי בקשות
בחלון). `null` — ההתקן לא זוהה (ספרייה ברשת) או מונה שלא נקרא באחד הצילומים: "לא נמדד" (עיקרון 5), לא "תקין".
ה-PSI נשאר ב-`result_json` כמידע בלבד. גל `server_disk`: `drive_rates` (#1842) לא לומד ממנו קצב, ו-`learn_netloss`
(#1846) לא מתריע עליו — ראו `bound`. יומן **`server_disk_slow`**. לעולם אינו זורק ואינו חוסם: כשל הוא שורה בלי מספרים.

**הראיה שהשולח מחכה (#3508):** `util` ו-`await` מודדים "הדיסק עסוק" על חלון הגל כולו, לא "השולח מחכה לו". ברזל 15/11
(`lab/metal-1115-compare`): גלי המנוע החדש עברו את שני הספים (util 36%, await 15.6-15.9ms), אבל `chunkcast send` ישן 74% מהזמן
ל-`--kbps`/`--write-kbps` (`hrtimer_nanosleep`) ורק 16% ממתין לקריאה, ודיסק הספרייה היה פנוי 64% מהזרם. בגל חתיכות
(`end(..., sender=..., need_sender=True)`) `server_disk` נקבע רק כש**גם** השולח לא ישן לקצב שלו יותר מ-`SENDER_ASLEEP_MAX` מהמעבר
הראשון (`paced_ms`, `first_pass_s` של `ChunkEngine.rate_outcome`). אחרת `basis` ריק -- **"לא נקבע"**, לא `server_disk` ולא `ok` --
ו-`undecided` ב-`result_json` אומר למה: `sender_not_measured` או `sender_paced`; מה שהספים אמרו נשאר ב-`looked`. הקליטה: בלי שינוי.

**#3553 -- הראיה היא ההמתנה לקריאה, לא השינה.** "לא ישן לקצב שלו" אינה מה שהשער שואל: המנוע ישן לתקרות (`--kbps`, `--write-kbps`) 57-69% מהמעבר
הראשון **במכוון**, ולכן 22 מ-24 גלי חתיכות של ברזל 16/11 יצאו "לא נקבע" (`sender_paced`) גם כשדיסק הספרייה היה פנוי, ותקרת השרת ותקרת הרשת
לא יכלו ללמוד מהם לעולם. מה ששואלים: כמה מהמעבר הראשון השולח **חיכה לקובץ הספרייה** (`library_wait`), לפי `sendstats` של `chunkcast send`
(`rate_outcome["sendstats"]`): בקורא `inline` -- `read_ms`; בתהליכון קורא -- `readq_empty_ms` (`read_ms` שלו הוא זמן התהליכון, במקביל לשליחה: ברזל
r16, `RT-1`: `read_ms` 110.6 שניות מתוך 321, `readq_empty_ms` 3.6). הספים אמרו עסוק, והשולח חיכה יותר מ-`LIBRARY_WAIT_MAX` מהמעבר -- `server_disk`;
חיכה פחות -- `ok` (הדיסק עסוק ואיש לא חיכה לו; `looked` אומר מה הספים ראו). `sendstats` חסר או קורא לא ידוע -- "לא נקבע" (`sender_not_measured`),
לא `ok`. השינה לתקרות ממשיכה לשמש את `netcap._pushed` ואת K8, ב-`sender_asleep`.

**התקרה (#3464):** `server_cap.learn` לומד מהגלים האלה את תקרת הקריאה של ההתקן (חציון `read_mbps` של גלי `server_disk`, ולפחות
שניים), ושורת הבריאות אומרת אותה. אותו קובץ גם נותן ל-K8 החלטת צל (`chunk_rate._server_cap`).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from server.db import _settle, _write_lock, journal, now_iso, writing
from server.core.logtext import one_line
from server.learn import server_cap
from server.system.health_rows import check

log = logging.getLogger("imagesrv.server_io")

PROC = Path("/proc")
#: #3365: איזה שבר מהזמן דיסק הספרייה היה עסוק (`io_ticks`), וכמה זמן ארכה בקשה אחת (await, מ"ש) -- שניהם יחד
#: "דיסק הספרייה הוא צוואר הבקבוק". ניחוש מנומק, ראו את ראש המודול.
SERVER_DISK_UTIL = 0.20
SERVER_DISK_AWAIT_MS = 10.0
#: #3508: השבר מהמעבר הראשון ש-`chunkcast send` רשאי לישון לקצב שלו (`--kbps` ו-`--write-kbps`, שורת `paced`) ועדיין להיחשב
#: "מחכה לקריאה". זה הסף של K8 (`chunk_rate.server_release`: `100 - CUT_PERCENT` אחוז), ו-`tests/test_server_io_sender_3508.py`
#: בודק שהם אותו מספר. ניחוש מנומק, לא נמדד על ברזל.
SENDER_ASLEEP_MAX = 0.25
#: #3553: השבר מהמעבר הראשון שהשולח רשאי לחכות לקובץ הספרייה (`library_wait`) ועדיין להיחשב "לא ממתין לדיסק הספרייה". ניחוש מנומק: ברזל 16/11
#: (`lab/metal-1116-r16`, ברירת המחדל של המנוע, קורא `inline`) מדד 18.3-22.9% בארבע המחיצות יחד; בתהליכון קורא 0.9-2.0%. השוליים בקורא `inline`
#: קטנים (עד 25%), ולכן גל כזה על דיסק איטי יותר ייקרא `server_disk`, וזה נכון: רבע מזמנו של השולח הלך על הקריאה.
LIBRARY_WAIT_MAX = 0.25
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
        pass  # לא נקרא — החזרה None = "לא ידוע", לא "אפס"
    return None


def _diskstats(proc: Path, device: str | None) -> dict:
    out = {"read_sectors": None, "write_sectors": None, "io_ms": None,
           "read_ios": None, "write_ios": None, "ios_ms": None}
    if device is None:
        return out
    try:
        for line in (proc / "diskstats").read_text(encoding="ascii").splitlines():
            f = line.split()
            if len(f) >= 13 and f[2] == device:
                out = {"read_sectors": int(f[5]), "write_sectors": int(f[9]), "io_ms": int(f[12]),
                       "read_ios": int(f[3]), "write_ios": int(f[7]), "ios_ms": int(f[6]) + int(f[10])}
                break
    except (OSError, ValueError):
        pass  # לא נקרא — out נשאר ריק = "לא ידוע", לא "אפס"
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
        pass  # לא נקרא — השדות חסרים = "לא ידוע", לא "אפס"
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
        log.exception("server I/O snapshot of %s %s failed", one_line(kind), one_line(ref))


def _delta(a: dict, b: dict, key: str) -> int | None:
    x, y = a.get(key), b.get(key)
    if not isinstance(x, int) or not isinstance(y, int) or y < x:
        return None
    return y - x


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _seconds(sender: object) -> float | None:
    got = sender.get("first_pass_s") if isinstance(sender, dict) else None
    return got if isinstance(got, (int, float)) and not isinstance(got, bool) and got > 0 else None


def sender_asleep(sender: dict | None) -> str | None:
    """#3508: None = יש ראיה שהשולח לא ישן לקצב שלו (ולכן הזמן שלו הלך על הקריאה והשליחה), אחרת למה אין: `sender_not_measured`
    (`paced_ms` או משך המעבר הראשון חסרים -- לא אפס) או `sender_paced` (ישן יותר מ-`SENDER_ASLEEP_MAX`). `sender` = `rate_outcome`
    או הדגימה שלו (`chunk_rate.sample_of`). #3553: זו שאלה על **השולח** (האם הוא הגבול של הגל, `netcap._pushed`), לא על דיסק הספרייה --
    לזה `library_wait`."""
    paced = sender.get("paced_ms") if isinstance(sender, dict) else None
    seconds = _seconds(sender)
    if not (isinstance(paced, dict) and all(_count(paced.get(k)) for k in ("wire", "disk")) and seconds is not None):
        return "sender_not_measured"
    return "sender_paced" if (paced["wire"] + paced["disk"]) > SENDER_ASLEEP_MAX * seconds * 1000 else None


def library_wait(sender: dict | None) -> float | None:
    """#3553: השבר מהמעבר הראשון שהשולח חיכה לקובץ הספרייה, לפי `sendstats` שלו (`rate_outcome`); None = לא נמדד (לא אפס): `sendstats` חסר
    (לא כל מחיצה נקראה, או שולח שלא הדפיס), הקורא לא ידוע, או משך המעבר חסר. בקורא `inline` השולח נחסם ב-`read_ms`; בתהליכון קורא הוא נחסם
    רק ב-`readq_empty_ms` (התור ריק) -- `read_ms` של התהליכון רץ במקביל לשליחה ואינו המתנה של השולח."""
    stats, seconds = (sender.get("sendstats") if isinstance(sender, dict) else None), _seconds(sender)
    reader = stats.get("reader") if isinstance(stats, dict) else None
    waited = stats.get({"inline": "read_ms", "thread": "readq_empty_ms"}.get(reader)) if isinstance(stats, dict) else None
    return waited / (seconds * 1000) if _count(waited) and seconds is not None else None


def measure(start: dict, end: dict, seconds: float | None = None, sender: dict | None = None,
            need_sender: bool = False) -> dict:
    """שני צילומים → התוצאה (ראו את ראש המודול). `seconds` — משך הזרם; בלעדיו — השעון. `need_sender` (#3508, גל חתיכות):
    `server_disk` רק עם ראיה מהשולח (`sender` = `rate_outcome`, `library_wait`, #3553: חיכה לקובץ יותר מ-`LIBRARY_WAIT_MAX`); בלעדיה --
    "לא נקבע". התוצאה נושאת גם את `library_wait` (None = לא נמדד)."""
    wall = end["at"] - start["at"]
    span = seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) \
        and seconds > 0 else wall
    same = start.get("device") is not None and start.get("device") == end.get("device")
    reads = _delta(start, end, "read_sectors") if same else None
    writes = _delta(start, end, "write_sectors") if same else None
    busy = _delta(start, end, "io_ms") if same else None
    some, full = _delta(start, end, "psi_some_us"), _delta(start, end, "psi_full_us")
    done = [_delta(start, end, k) if same else None for k in ("read_ios", "write_ios")]
    ios = None if None in done else sum(done)        # בקשות שהסתיימו בחלון
    ios_ms = _delta(start, end, "ios_ms") if same else None
    ok = span > 0                      # util לשעון הקיר; השאר למשך הזרם
    out = {
        "device": start.get("device") if same else None,
        "seconds": round(span, 1) if span > 0 else None,
        "read_mbps": round(reads * SECTOR / span / 1e6, 1) if ok and reads is not None else None,
        "write_mbps": round(writes * SECTOR / span / 1e6, 1) if ok and writes is not None else None,
        "util": round(min(1.0, busy / 1000 / wall), 3) if wall > 0 and busy is not None else None,
        "await_ms": round(ios_ms / ios, 1) if ios and ios_ms is not None else None,
        "psi_some": round(min(1.0, some / 1e6 / span), 3) if ok and some is not None else None,
        "psi_full": round(min(1.0, full / 1e6 / span), 3) if ok and full is not None else None,
    }
    # #3365: ראיה חיובית מההתקן של הספרייה בלבד. אין התקן / מונה שלא נקרא = None, לא "ok"
    seen = out["util"] is not None and ios is not None and ios_ms is not None
    out["basis"] = (None if not seen
                    else "server_disk" if out["util"] >= SERVER_DISK_UTIL and out["await_ms"] is not None
                    and out["await_ms"] >= SERVER_DISK_AWAIT_MS else "ok")
    if need_sender and out["basis"] is not None:
        wait = library_wait(sender)                                       # #3553: the sender's own count of its wait for the library file
        out["library_wait"] = None if wait is None else round(wait, 3)
        if out["basis"] == "server_disk" and wait is None:
            out.update(basis=None, looked="server_disk", undecided="sender_not_measured")   # #3508: busy by the thresholds; nobody says it was waited for
        elif out["basis"] == "server_disk" and wait <= LIBRARY_WAIT_MAX:
            out.update(basis="ok", looked="server_disk")                  # #3553: busy by the thresholds, and the sender did not wait for it
    return out


def end(conn: sqlite3.Connection, kind: str, ref: str, root: str | Path,
        seconds: float | None = None, proc: Path = PROC, sender: dict | None = None,
        need_sender: bool = False) -> dict | None:
    """הצילום בסוף, והתוצאה. None — אין צילום פתיחה (גל שלא נבחר לו קצב). `sender`, `need_sender` -- ראו `measure` (#3508).
    לעולם אינו זורק."""
    try:
        row = conn.execute("SELECT start_json FROM server_io WHERE kind = ? AND ref = ?",
                           (kind, ref)).fetchone()
        if row is None:
            return None
        result = measure(json.loads(row["start_json"]), snapshot(root, proc), seconds, sender, need_sender)
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute("UPDATE server_io SET ended_at = ?, result_json = ?, basis = ?"
                         " WHERE kind = ? AND ref = ?",
                         (now_iso(), json.dumps(result), result["basis"], kind, ref))
        if result["basis"] == "server_disk":
            journal(conn, "server_disk_slow", f"{kind} {ref} {_numbers(result)}",
                    round_id=ref if kind == "wave" else None)
        elif result.get("undecided"):
            journal(conn, "server_disk_undecided", f"{kind} {ref} {_numbers(result)} why={result['undecided']}",
                    round_id=ref if kind == "wave" else None)
        return result
    except Exception:             # noqa: BLE001
        log.exception("server I/O result of %s %s failed", one_line(kind), one_line(ref))
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


def result_of(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """#3269: התוצאה של הגל (`measure`), או None — לא נמדד, עוד לא הסתיים, או לא נקרא."""
    try:
        row = conn.execute("SELECT result_json FROM server_io WHERE kind = 'wave' AND ref = ?",
                           (session_id,)).fetchone()
        got = json.loads(row["result_json"]) if row is not None and row["result_json"] else None
        return got if isinstance(got, dict) else None
    except Exception:             # noqa: BLE001
        log.exception("server I/O result of %s unreadable", session_id)
        return None


UNDECIDED_HE = {"sender_not_measured": "השולח לא דיווח כמה חיכה לקובץ הספרייה",
                "sender_paced": "השולח ישן לקצב שלו רוב הזמן"}        # sender_paced: שורות שנכתבו לפני #3553


def _numbers(r: dict) -> str:
    def f(v, fmt):
        return "לא נמדד" if v is None else fmt.format(v)
    return (f"read={f(r['read_mbps'], '{:.1f}')}MB/s write={f(r['write_mbps'], '{:.1f}')}MB/s"
            f" util={f(None if r.get('util') is None else 100 * r['util'], '{:.0f}')}%"
            f" await={f(r.get('await_ms'), '{:.1f}')}ms"
            f" device={r['device'] or 'לא נקרא'}"
            + ("" if "library_wait" not in r else f" libwait={f(None if r['library_wait'] is None else 100 * r['library_wait'], '{:.0f}')}%"))


def health_checks(conn: sqlite3.Connection) -> list[dict]:
    """שורה אחת — לפי הגל האחרון שנמדד. בלי גל שנמדד — אין שורה."""
    try:
        row = conn.execute("SELECT ref, result_json FROM server_io WHERE kind = 'wave'"
                           " AND result_json IS NOT NULL ORDER BY ended_at DESC, rowid DESC"
                           " LIMIT 1").fetchone()
        if row is None:
            return []
        r = json.loads(row["result_json"])
        mbps = "לא נמדד" if r.get("read_mbps") is None else f"{r['read_mbps']:.0f} MB/s"
        util = None if r.get("util") is None else round(100 * r["util"])
        wait = "לא נמדד" if r.get("await_ms") is None else f"{r['await_ms']:.1f}ms"
        cap = server_cap.learn(conn)          # #3464: התקרה שנלמדה מגלים שהדיסק הגביל. None = עוד לא נלמדה
        learned = "" if cap is None else f". {cap.text_he()}"
        if r.get("basis") == "server_disk":
            return [check("server_disk", "דיסק הספרייה", "warn",
                          f"דיסק הספרייה איטי — {mbps} בגל האחרון; עסוק {util}% מהזמן, {wait} לבקשה"
                          f" ({row['ref']}). הגל הזה לא נכנס ללמידת הקצב{learned}")]
        if r.get("basis") == "ok":
            idle = ("" if r.get("looked") != "server_disk" or r.get("library_wait") is None else
                    f" -- הדיסק נראה עסוק, אבל השולח חיכה לקובץ רק {round(100 * r['library_wait'])}% מהמעבר הראשון")   # #3553
            return [check("server_disk", "דיסק הספרייה", "ok",
                          f"{mbps} בגל האחרון; עסוק {util}% מהזמן, {wait} לבקשה{idle}{learned}")]
        if r.get("undecided"):            # #3508: busy by the thresholds, with no evidence that the sender waited for it
            return [check("server_disk", "דיסק הספרייה", "off",
                          f"לא נקבע — הדיסק נראה עסוק ({mbps}; {util}% מהזמן, {wait} לבקשה) אבל אין ראיה שהשולח חיכה לו"
                          f" ({UNDECIDED_HE.get(r['undecided'], r['undecided'])}){learned}")]
        return [check("server_disk", "דיסק הספרייה", "off",
                      f"לא נבדק — דיסק הספרייה לא נמדד (ההתקן לא זוהה, או ש-/proc/diskstats לא נקרא); קריאה {mbps}")]
    except Exception:             # noqa: BLE001
        log.exception("server I/O health failed")
        return [check("server_disk", "דיסק הספרייה", "off", "לא נבדק — הקריאה נכשלה")]
