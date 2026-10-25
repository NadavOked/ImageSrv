"""‏#1882 — הלוג של המשכפל נשמר בשרת: קטע אחרון דחוס בכל סוף סבב, וזיהוי כשלים מוכרים.

**למה.** ‏`/run/imagectl/agent.log` יושב ב-tmpfs של המכונה ונמחק באתחול, ובייצור אין SSH
(‏#702). עד כאן לא הייתה שום דרך לדעת **למה** דיסק נכשל בכיתה — רק שורת ה-`error` של היעד.

**מה מגיע.** הסוכן (‏`agent/lib/datareport.sh: agent_log_send`) שולח פעם אחת בסוף כל סבב —
בהצלחה (‏`round_end`) ובכשל (‏`failed`: מגירה אחת לפחות נכשלה, או אף מגירה לא הושלמה) — את
‏`AGENT_LOG_LINES` השורות האחרונות של הלוג, חתוכות ל-`AGENT_LOG_BYTES`, דחוסות ב-gzip.
בקשה אחת לסבב, אחרי הדיווח הסופי — לא בכל דיווח של 2 שניות (רשת ההפצה, #1882).

**מה נשמר** (‏`agent_logs`): ‏`mac`, ‏`session_id`, ‏`reason`, ‏`at` (שעון השרת), ‏`lines`,
‏`bytes`, ‏`truncated`, ‏`signatures_json` והטקסט **אחרי סינון**, דחוס (‏`text_gz`).

**פרטיות (#1924) וסודות.** לפני שנשמר בייט, `redact` מחליף: מפתחות/עוגיות/סיסמאות/אסימונים
(כותרות HTTP, ‏`token=`/‏`password=`…, מפתח פרטי), GUIDs, ‏UUID של מערכות קבצים ומספרים סידוריים
של NTFS, תוויות (‏`LABEL=`/‏`PARTLABEL=`), ושמות מחשב ומשתמשים **שנקראו מהדיסק** (‏`computer='…'`,
‏`hostname='…'`, ‏`raw='…'`, ‏`\\Users\\<שם>`). מה שהשרת עצמו נתן (שם המחשב שנכתב) אינו מידע מהדיסק,
אבל גם הוא עובר את אותו סינון — אין כאן הבחנה עדינה שעלולה לטעות לכיוון הדליפה.

**חתימות** (‏`SIGNATURES`) — כמו `ata_cause.py`: ביטוי → הסבר בעברית. מידע בלבד: שום דבר כאן
אינו צובע דיסק, אינו מדלג ואינו חוסם (הכרעת נדב 20/10). מה שלא תאם — בלי חתימה, לא ניחוש.

**תקרות** (הדיסק של השרת לא מתמלא): גוף ≤ `MAX_BODY` דחוס, ≤ `MAX_INFLATE` פרוס (פצצת דחיסה
נדחית), טקסט שמור ≤ `MAX_TEXT` (הזנב); ‏`KEEP_PER_MAC` רשומות לכל מכונה ו-`KEEP_ROWS` בסך הכול —
נאכף **בכל הכנסה**, ו-`housekeeping` מוחק אחרי 90 יום.

לעולם אינו זורק: כשל הוא תשובה `{"ok": false}` ושורה ב-log של השרת — הסוכן ממשיך כרגיל.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import sqlite3
import zlib

from .db import _settle, _write_lock, journal, now_iso, writing

log = logging.getLogger("imagectl.agent_logs")

#: הגוף הדחוס שמתקבל — מעליו 413. הסוכן שולח עד 64KB פרוסים, ‏gzip מוריד פי 5–10.
MAX_BODY = 128 * 1024
#: כמה בייטים פרוסים לכל היותר — מעבר לזה פצצת דחיסה, ונדחה.
MAX_INFLATE = 512 * 1024
#: כמה טקסט נשמר — הזנב (שם הכשל), מתחילת שורה.
MAX_TEXT = 64 * 1024
#: רשומות לכל מכונה, ובסך הכול — נאכף בכל הכנסה (`_trim`).
KEEP_PER_MAC = 30
KEEP_ROWS = 1000
#: למה נשלח. ‏`failed` = מגירה נכשלה או אף אחת לא הושלמה; ‏`round_end` = הסבב הסתיים.
REASONS = frozenset({"failed", "round_end"})

_SESSION = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

#: (ביטוי, החלפה) — לפי הסדר. ראו את ראש המודול.
REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "<private-key>"),
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie|x-imagectl-[a-z-]*token)(\s*:\s*)\S.*"),
     r"\1\2<redacted>"),
    (re.compile(r"(?i)\b(token|password|passwd|pass|secret|key|cookie|auth|session)=([^\s&'\"]+)"),
     r"\1=<redacted>"),
    (re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"),
     "<guid>"),
    (re.compile(r"\b[0-9A-Fa-f]{16}\b"), "<serial>"),
    (re.compile(r"\b[0-9A-F]{4}-[0-9A-F]{4}\b"), "<uuid>"),
    (re.compile(r"\b(LABEL|PARTLABEL|label)=(\"[^\"]*\"|'[^']*'|\S+)"), r"\1=<label>"),
    (re.compile(r"(?i)\b(computer|hostname|host|nv|raw|name|user)=('[^']*'|\"[^\"]*\")"),
     r"\1='<redacted>'"),
    (re.compile(r"(?i)(\\Users\\|/Users/|/home/)[^\\/\s'\"]+"), r"\1<user>"),
]

#: (מפתח, ביטוי, הסבר בעברית). מידע בלבד — ראו את ראש המודול.
SIGNATURES: list[tuple[str, re.Pattern, str]] = [
    ("crc", re.compile(r"BadCRC|ICRC"),
     "שגיאות CRC בקו ה-SATA — כנראה הכבל או המגירה, לא הדיסק"),
    ("link_reset", re.compile(r"(?i)hard resetting link|SATA link down|limiting SATA link speed|link reset"),
     "קישור ה-SATA התאפס או הואט — חיבור רופף, כבל או ספק כוח"),
    ("offline", re.compile(r"(?i)rejecting I/O to offline|device offline"),
     "הדיסק יצא מהקו באמצע הכתיבה — נשלף, או שהבקר ויתר עליו"),
    ("io_error", re.compile(r"(?i)Input/output error|\bEIO\b|fsync.*fail"),
     "שגיאת קלט/פלט בכתיבה לדיסק — הדיסק, הכבל או המגירה"),
    ("no_space", re.compile(r"(?i)No space left on device"),
     "אזל המקום ביעד"),
    ("zstd", re.compile(r"(?i)zstd.*(corrupt|error)"),
     "הזרם הגיע פגום לפריסה (zstd) — רשת או מקור, לא הדיסק"),
    ("sha", re.compile(r"sha256 mismatch|ה-sha256 לא חושב"),
     "ה-sha256 של הזרם לא תאם — הזרם לא הגיע שלם (לא הדיסק)"),
    ("stream_timeout", re.compile(r"(?i)receive-timeout|udp-receiver.*timed? ?out|פג הזמן"),
     "הזרם נעצר — פקיעת המתנה לשרת או לרשת"),
    ("hivex", re.compile(r"(?i)key not found"),
     "קריאת הרישום של Windows נכשלה (hivex) — כנראה מודולי gconv חסרים (#33)"),
    ("partition_write", re.compile(r"partition \d+ .*write failed \(rc="),
     "כתיבת מחיצה נכשלה (partclone/הזרם) — השורות שלפניה אומרות למה"),
]


class BadLog(ValueError):
    """גוף שאינו לוג שאפשר לשמור — הסיבה בשם."""


def redact(text: str) -> str:
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def classify(text: str) -> list[dict]:
    """לכל חתימה שתאמה — ‏`{"key", "he", "line"}` (השורה **האחרונה** שתאמה, מסוננת)."""
    found = []
    lines = text.splitlines()
    for key, pattern, he in SIGNATURES:
        hit = next((ln for ln in reversed(lines) if pattern.search(ln)), None)
        if hit is not None:
            found.append({"key": key, "he": he, "line": hit[:300]})
    return found


def inflate(body: bytes) -> str:
    """גוף gzip → טקסט. ‏`BadLog` על גוף ריק, גדול מדי, לא-gzip, או פצצת דחיסה."""
    if not body:
        raise BadLog("empty body")
    if len(body) > MAX_BODY:
        raise BadLog("body too large")
    try:
        unpack = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = unpack.decompress(body, MAX_INFLATE)
        if unpack.unconsumed_tail:
            raise BadLog("inflated log too large")
    except zlib.error as exc:
        # ‏#2160: הודעה קבועה לרשת (פורט הסוכן, בלי כניסה); הפירוט של zlib — ליומן השרת.
        log.warning("agent log upload is not gzip: %s", exc)
        raise BadLog("not gzip") from exc
    text = raw.decode("utf-8", errors="replace")
    return "".join(ch for ch in text if ch in "\n\t" or ch >= " ")


def _tail(text: str) -> tuple[str, bool]:
    if len(text.encode("utf-8")) <= MAX_TEXT:
        return text, False
    cut = text.encode("utf-8")[-MAX_TEXT:].decode("utf-8", errors="ignore")
    nl = cut.find("\n")
    return (cut[nl + 1:] if nl >= 0 else cut), True


def receive(conn: sqlite3.Connection, mac: str, session_id: object, reason: object,
            body: bytes, known: bool) -> dict:
    """‏`POST /api/v1/agent/log` אחרי שער הזהות. ‏`known` — המכונה רשומה."""
    try:
        if not known:
            return {"ok": False, "code": "unknown_machine", "error": "not a registered machine"}
        if reason not in REASONS:
            return {"ok": False, "code": "bad_reason", "error": f"reason must be one of {sorted(REASONS)}"}
        if session_id is not None and (not isinstance(session_id, str)
                                       or not _SESSION.match(session_id)):
            return {"ok": False, "code": "bad_session", "error": "malformed session_id"}
        try:
            text = inflate(body)
        except BadLog as exc:
            return {"ok": False, "code": "bad_log", "error": str(exc)}
        text, truncated = _tail(redact(text))
        signatures = classify(text)
        lines = len(text.splitlines())
        log_id = _store(conn, mac, session_id or None, reason, text, lines, truncated, signatures)
        journal(conn, "agent_log", f"{mac} {reason} {session_id or '-'} lines={lines}"
                + (" — " + "; ".join(s["he"] for s in signatures) if signatures else ""))
        return {"ok": True, "id": log_id, "lines": lines, "signatures": [s["key"] for s in signatures]}
    except Exception:             # noqa: BLE001 — איסוף אינו מפיל את השרת
        log.exception("storing the agent log of %s failed", mac)
        return {"ok": False, "code": "error", "error": "not stored"}


def _store(conn: sqlite3.Connection, mac: str, session_id: str | None, reason: str,
           text: str, lines: int, truncated: bool, signatures: list[dict]) -> int:
    blob = gzip.compress(text.encode("utf-8"))
    _settle(conn)
    with _write_lock, writing(conn):
        cur = conn.execute(
            "INSERT INTO agent_logs (mac, session_id, reason, at, lines, bytes, truncated,"
            " signatures_json, text_gz) VALUES (?,?,?,?,?,?,?,?,?)",
            (mac, session_id, reason, now_iso(), lines, len(text.encode("utf-8")),
             int(truncated), json.dumps(signatures, ensure_ascii=False), blob))
        _trim(conn, mac)
        return int(cur.lastrowid)


def _trim(conn: sqlite3.Connection, mac: str) -> None:
    """התקרה בכל הכנסה — `KEEP_PER_MAC` למכונה, `KEEP_ROWS` בסך הכול."""
    conn.execute("DELETE FROM agent_logs WHERE mac = ? AND id NOT IN"
                 " (SELECT id FROM agent_logs WHERE mac = ? ORDER BY id DESC LIMIT ?)",
                 (mac, mac, KEEP_PER_MAC))
    conn.execute("DELETE FROM agent_logs WHERE id NOT IN"
                 " (SELECT id FROM agent_logs ORDER BY id DESC LIMIT ?)", (KEEP_ROWS,))


def list_for(conn: sqlite3.Connection, mac: str, limit: int = 10) -> list[dict]:
    """הרשומות האחרונות של המכונה, בלי הטקסט."""
    out = []
    for row in conn.execute(
            "SELECT id, session_id, reason, at, lines, bytes, truncated, signatures_json"
            " FROM agent_logs WHERE mac = ? ORDER BY id DESC LIMIT ?", (mac, limit)):
        try:
            signatures = json.loads(row["signatures_json"] or "[]")
        except (TypeError, ValueError):
            signatures = []
        out.append({"id": row["id"], "session_id": row["session_id"], "reason": row["reason"],
                     "at": row["at"], "lines": row["lines"], "bytes": row["bytes"],
                     "truncated": bool(row["truncated"]), "signatures": signatures})
    return out


def text_of(conn: sqlite3.Connection, log_id: int) -> dict | None:
    row = conn.execute("SELECT id, mac, session_id, reason, at, truncated, text_gz"
                       " FROM agent_logs WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        return None
    try:
        text = gzip.decompress(row["text_gz"]).decode("utf-8", errors="replace")
    except (OSError, EOFError, zlib.error):
        text = None
    return {"id": row["id"], "mac": row["mac"], "session_id": row["session_id"],
            "reason": row["reason"], "at": row["at"], "truncated": bool(row["truncated"]),
            "text": text}
