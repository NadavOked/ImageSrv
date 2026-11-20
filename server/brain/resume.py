"""#2528 שלב 4 (K11): משכפל שכבה באמצע סבב וחזר -- להמשיך מה שכבר נכתב, או להתחיל מחדש.

**הכרעת נדב, 14/11:** "צריך שהמוח יבין כמה חסר, ולפי זה יחליט אם להתחיל מחדש או לא." ההחלטה היא לכל דיסק של
משכפל שחזר, אחרי שהמפעיל ענה "כתוב מחדש" (`interrupt.settle`). השאלה למפעיל עצמה נשארת כמו בשלב 1.

**שני הקריטריונים, ושניהם נמדדים:**

- **כמה חסר:** לפי ה-ledger של השרת (`chunk_ledger`). הוא נשמר כאן (`snapshot`) ברגע שהמפעיל ענה "כתוב מחדש",
  כי הדיווח הראשון של הגל החדש מוחק אותו (`chunkledger.forget_others`). אין ledger = אין ראיה למה שנכתב, וחסר הכול.
- **קריאה חוזרת מול שידור:** הסוכן מודד את קצב הקריאה של הדיסק (קריאה ישירה עם XXH64, כמו הקריאה החוזרת עצמה),
  ושולח כמה שניות תיקח הקריאה החוזרת (`readback_s`). קצב הכתיבה הוא מה שהדיסק הזה כתב בפועל בגל שנקטע: החתיכות
  שבledger, חלקי הזמן מתחילת הגל ועד העדכון האחרון של ה-ledger.

**הכלל** (`decide`, פונקציה טהורה): כתיבה מחדש = הכול בקצב הכתיבה. המשך = הקריאה החוזרת של הכול, ועוד החסר בקצב הכתיבה.
ממשיכים רק כשההמשך קצר מ-`MARGIN` מזמן הכתיבה מחדש. כל מספר שלא נמדד = מתחילים מחדש (עיקרון 5): "לא ידוע" אינו
"משתלם". על ברזל (משכפל 2, 14/11): קריאה עם XXH64 ב-210MiB/s, כתיבה מלאה ב-35 דקות, הכיבוי ב-9,172 מתוך 18,254
חתיכות -- המשך של כ-23 דקות מול 35.

**מצבים** (``brain_resume_mode``, כמו ``brain_veto_mode``): ``off`` -- לא מחושב ולא נרשם. ``shadow`` (ברירת המחדל) --
מחושב ונרשם ב-``brain_decisions``, והסוכן כותב מחדש כמו היום. ``enforce`` -- הסוכן ממשיך כשהמוח אמר "להמשיך".
**ההחלטה אינה מחליפה ראיה:** גם ב-enforce הסוכן קורא בחזרה כל חתיכה מול האינדקס לפני שהיא נחשבת כתובה, וקריאה שנכשלה
= המחיצה נכתבת מההתחלה (`agent/lib/restore/resume.sh`).

**לעולם אינו זורק** אל נקודת ה-API או אל `settle`: מוח שנפל הוא "להתחיל מחדש" ושורת לוג.

**#3670 (אפיק #3653: המוח מחליט, הסוכן והשרת מבצעים) -- מה עבר לכאן, בלי לשנות אף מספר:**

- **תקרת הקריאה החוזרת** (`READBACK_MIN_MBPS` 10, ו-`READBACK_SLACK_S` 60) ישבה בסוכן כ-`RESUME_MIN_MBPS` וכ-`+ 60`.
  היא נאמרת עכשיו בתשובת `/agent/resume` לכל דיסק (`readback`), והסוכן רק מחשב ממנה כמה זמן מותר לקריאה. תשובה בלי
  המספרים האלה = אין החלטה = המחיצה נכתבת מההתחלה (עיקרון 1).
- **כמה זמן תיקח הקריאה החוזרת** (`readback_seconds`) מחושב כאן מהמדידה הגולמית שהסוכן שולח (`read_bytes`, `read_mbps`).
  הסוכן כבר אינו מחשב ואינו שולח `readback_s`: חישוב שמזין את ההחלטה אינו נעשה אצל מי שמבצע.
- **המעבר הראשון** (`first_pass`, לשעבר גוף `chunksend._first_pass`): משדר רק את מה שחסר כשלכל דיסק של כל מי שהתייצב יש
  ב-ledger של הגל חתיכות שאומתו. ההחלטה נרשמת ב-``brain_decisions``; `chunksend` רק מוסר עובדות ומבצע את התשובה.

**מה לא עבר, ולמה:** גודל המדידה ומגבלת הזמן שלה (`RESUME_PROBE_MIB` 64, `RESUME_PROBE_S` 30) נשארים בסוכן: הם צורת החוש
ולא החלטה, והם קודמים לשאלה (את המדידה שולחים כדי לשאול). מדידה שלא הסתיימה היא `null`, וההחלטה עליה כאן: להתחיל מחדש.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from server.brain import memory
from server.db import get_setting

log = logging.getLogger("imagesrv.brain")

SETTING = "brain_resume_mode"
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
MODES = (OFF, SHADOW, ENFORCE)
DEFAULT = SHADOW
RULE = "K11"
RESUME, RESTART = "RESUME", "RESTART"
#: חתיכה של מנוע החתיכות (`interfaces.md` סעיף 7). החתיכה האחרונה של מחיצה קצרה יותר: ההערכה גסה בחתיכה אחת.
CHUNK_BYTES = 4 << 20
#: ממשיכים רק כשההמשך חוסך לפחות 10%. **הערכה, לא מדידה:** "אם הקריאה וההשלמה לוקחות בדיוק כמו כתיבה מחדש, לא
#: הרווחנו כלום" (נדב 12/11). מדידת הברזל של #2528 מכיילת אותו.
MARGIN = 0.9
#: כמה זמן צילום ה-ledger נשמר: התקרה של השאלה למפעיל (`interrupt.MAXIMUM`, 240 דקות) ועוד גל.
KEEP_S = 6 * 3600
#: #3670 (A32): התקרה של הקריאה החוזרת של מחיצה, שישבה בסוכן (`RESUME_MIN_MBPS` 10 ו-`+ 60`): המחיצה בקצב הזה, ועוד דקה.
#: קריאה שלא הסתיימה בה = המחיצה נכתבת מההתחלה (`agent/lib/restore/resume.sh`). **אותם ערכים בדיוק**, לא מדידה חדשה.
READBACK_MIN_MBPS = 10
READBACK_SLACK_S = 60
POINT_REWRITE, POINT_FIRST_PASS = "interrupt.rewrite", "chunksend.first_pass"

SHADOW_HE = "צל: הסוכן כותב מחדש כמו היום"
SENT_HE = "נשלח לסוכן"


@dataclass(frozen=True)
class Candidate:
    """מה שה-ledger אמר על דיסק אחד ברגע "כתוב מחדש": {מחיצה: (index_xxh64, חתיכות עמידות, חתיכות במחיצה)}."""
    round_id: str
    image_id: str
    parts: dict
    pace_bps: float | None          # בייטים לשנייה שהדיסק הזה כתב בגל שנקטע; None = לא נמדד
    pace_from: str
    taken: float


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str
    evidence: tuple = ()


_lock = threading.Lock()
_candidates: dict[tuple[str, int], Candidate] = {}
_decided: dict[tuple[str, str], dict] = {}          # (גל, serial) -> התשובה שכבר ניתנה: אותה החלטה, שורה אחת


def mode(conn) -> str:
    try:
        raw = get_setting(conn, SETTING)
    except Exception:                               # noqa: BLE001
        log.exception("brain: %s unreadable, using %s", SETTING, DEFAULT)
        return DEFAULT
    return raw if raw in MODES else DEFAULT


def decide(*, total_chunks: int, written_chunks: int | None, readback_s: float | None,
           pace_bps: float | None, margin: float = MARGIN) -> Decision:
    """להמשיך או להתחיל מחדש. כל קלט שלא נמדד = להתחיל מחדש, בשמו."""
    if written_chunks is None:
        return Decision(RESTART, "אין לדיסק הזה ledger בשרת מהסבב הזה: אין ראיה למה שנכתב, וחסר הכול")
    if written_chunks <= 0 or total_chunks <= 0:
        return Decision(RESTART, "ה-ledger ריק: לא נכתב דבר שאפשר להמשיך ממנו")
    if not readback_s or readback_s <= 0:
        return Decision(RESTART, "זמן הקריאה החוזרת לא נמדד בסוכן: אין מול מה להשוות")
    if not pace_bps or pace_bps <= 0:
        return Decision(RESTART, "קצב הכתיבה של הדיסק בגל שנקטע לא נמדד: אין מול מה להשוות")
    written = min(written_chunks, total_chunks)
    missing = total_chunks - written
    restart_s = total_chunks * CHUNK_BYTES / pace_bps
    resume_s = readback_s + missing * CHUNK_BYTES / pace_bps
    evidence = (f"written={written}/{total_chunks}", f"missing={missing}", f"readback_s={readback_s:.0f}",
                f"write_MBps={pace_bps / 1e6:.1f}", f"resume_s={resume_s:.0f}", f"restart_s={restart_s:.0f}")
    say = (f"חסרות {missing} מתוך {total_chunks} חתיכות. קריאה חוזרת {readback_s / 60:.1f} דק' ועוד השלמה"
           f" = {resume_s / 60:.1f} דק', מול כתיבה מחדש {restart_s / 60:.1f} דק'")
    if resume_s <= restart_s * margin:
        return Decision(RESUME, f"להמשיך: {say}", evidence)
    return Decision(RESTART, f"להתחיל מחדש: {say} -- החיסכון קטן מ-{round((1 - margin) * 100)}%", evidence)


def _whole(value: object) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def readback_seconds(read_bytes: object, read_mbps: object) -> int | None:
    """כמה שניות תיקח הקריאה החוזרת של `read_bytes` בקצב שנמדד, `read_mbps` (MB/s של קריאה ישירה דרך XXH64). הנוסחה
    היא של הסוכן לפני #3670, ספרה בספרה (חילוק שלם, ועוד שנייה): `bytes / (mbps * 1000000) + 1`. לא נמדד = None."""
    b, r = _whole(read_bytes), _whole(read_mbps)
    return int(b // (r * 1_000_000)) + 1 if b is not None and r is not None else None


def readback_limits() -> dict:
    """מה שהסוכן מקבל כדי לחשב את התקרה של קריאה חוזרת: `bytes / (min_mbps * 1000000) + slack_s`."""
    return {"min_mbps": READBACK_MIN_MBPS, "slack_s": READBACK_SLACK_S}


@dataclass(frozen=True)
class FirstPass:
    """מה שה-ledger המאומת אומר על מעבר ראשון של מחיצה. `need` = הטווחים לשידור, או None = הכול, כמו בלי המשך."""
    need: list | None
    reason: str
    evidence: tuple = ()


def first_pass(conn, *, session_id: str, part: int, chunks: int, listening: list, disks: list, held) -> FirstPass:
    """#3670 (S16, K11): המעבר הראשון של מחיצה משדר רק את מה שחסר -- כשלכל דיסק של כל מי שהתייצב (`listening`) יש ב-ledger של
    הגל הזה חתיכות (`held(mac, serial)`: מה שנקרא בחזרה מהדיסק ונשלח עם `listening`, לא מה שהשרת זוכר).

    `disks` = (mac, serial) של כל דיסק שלא נכשל אצל כל מי שהתייצב. מי שהתייצב בלי אף דיסק בדיווח, או דיסק אחד בלי אף חתיכה,
    = אין לדעת מה חסר לו = הכול משודר, כמו היום. [] = לאף אחד לא חסר דבר. ההחלטה נרשמת רק כשהיא סוטה מברירת המחדל
    (שידור מלא): זו שורה אחת למחיצה ולגל, ושידור מלא הוא כל מחיצה של כל גל רגיל. **לעולם אינו זורק.**"""
    from server.rounds import repair   # noqa: PLC0415 -- rounds imports the brain, not the other way
    full = FirstPass(None, "אין ledger מאומת לכל הדיסקים שהתייצבו: המחיצה משודרת כולה")
    try:
        if not disks or set(listening) - {mac for mac, _s in disks}:
            return full
        need: list = []
        for mac, serial in disks:
            have = held(mac, serial)
            if not have:
                return full
            need = repair.union(need, repair.missing(chunks, have))
        if need == [(0, chunks - 1)]:
            return full
        sending = repair.count(need)
        d = FirstPass(need, f"המעבר הראשון של מחיצה {part} משדר {sending} מתוך {chunks} חתיכות: כל הדיסקים שהתייצבו נקראו בחזרה",
                      (f"part={part}", f"send={sending}/{chunks}", f"disks={len(disks)}", f"machines={len(set(listening))}"))
        now_mode = mode(conn) if conn is not None else OFF   # no connection (a test that wires none) = not recorded
        if now_mode != OFF:
            memory.record(conn, mode=now_mode, rule=RULE, point=POINT_FIRST_PASS, action="RESUME_OR_RESTART", verdict=RESUME,
                          reason=d.reason, evidence=d.evidence, round_id=session_id, entity=("round", session_id),
                          outcome="המעבר הראשון שודר רק עם מה שחסר")
        return d
    except Exception:                               # noqa: BLE001 -- the first pass goes out whole, as before the resume
        log.exception("brain: the first pass of %s part %s was not weighed", session_id, part)
        return full


def _age_s(start: object, end: object) -> float | None:
    try:
        return (datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds()
    except (TypeError, ValueError):
        return None


def snapshot(conn, round_row, macs) -> int:
    """"כתוב מחדש" נענה: מה שה-ledger אומר על הדיסקים של המשכפלים שחזרו נשמר, לפני שהגל החדש מוחק אותו.
    מחזיר כמה דיסקים נשמרו. **לעולם אינו זורק** (מסלול `settle`)."""
    try:
        from server.rounds import chunkledger   # noqa: PLC0415 -- rounds imports the brain, not the other way
        now, kept = time.monotonic(), 0
        for mac in macs:
            for serial, size in set(chunkledger.disks_of(conn, mac).values()):
                parts = {p: r for p, r in chunkledger.of_disk(conn, serial, size).items()
                         if r["image_id"] == round_row["image_id"]}
                if not parts:
                    continue
                written = sum(sum(b - a + 1 for a, b in r["ranges"]) for r in parts.values())
                waves = {r["round_id"] for r in parts.values()}
                pace, why = None, "לא נמדד: ה-ledger של הדיסק הוא מכמה גלים"
                if len(waves) == 1:
                    row = conn.execute("SELECT started_at FROM sessions WHERE id = ?", (next(iter(waves)),)).fetchone()
                    took = _age_s(row["started_at"] if row is not None else None,
                                  max(r["updated_at"] for r in parts.values()))
                    if took and took > 0 and written > 0:
                        pace, why = written * CHUNK_BYTES / took, f"{written} חתיכות ב-{took:.0f} שניות"
                    else:
                        why = "לא נמדד: אין זמן התחלה לגל, או שלא עבר זמן"
                with _lock:
                    _candidates[(serial, size)] = Candidate(
                        round_row["id"], round_row["image_id"],
                        {p: (r["index_xxh64"], sum(b - a + 1 for a, b in r["ranges"]), r["chunks"])
                         for p, r in parts.items()}, pace, why, now)
                kept += 1
        with _lock:
            for key in [k for k, c in _candidates.items() if now - c.taken > KEEP_S]:
                del _candidates[key]
        return kept
    except Exception:                               # noqa: BLE001 -- the rewrite goes on; nothing to resume from
        log.exception("brain: the ledger of %s was not kept for a resume", round_row["id"])
        return 0


def answer(conn, *, session_id: str, image_id: str, manifest: dict, targets: list) -> list[dict]:
    """תשובת `/agent/resume`: לכל דיסק -- verdict (resume/restart), האם נאכף, הסיבה, וכמה חתיכות ה-ledger אמר לכל
    מחיצה. ההחלטה נרשמת ב-``brain_decisions`` פעם אחת לגל ולדיסק. **לעולם אינו זורק.**"""
    now_mode = mode(conn)
    out = []
    for t in targets:
        serial, size = t.get("serial"), t.get("size_bytes")
        if not isinstance(serial, str) or not serial or isinstance(size, bool) or not isinstance(size, int):
            continue
        try:
            out.append(_one(conn, now_mode, session_id, image_id, manifest, serial, size,
                            readback_seconds(t.get("read_bytes"), t.get("read_mbps"))))
        except Exception:                           # noqa: BLE001
            log.exception("brain: the resume of %s was not decided", serial)
            out.append({"serial": serial, "verdict": "restart", "enforced": False,
                        "reason": "ההחלטה נכשלה בשרת: מתחילים מחדש", "ledger": {}})
    return out


def _one(conn, now_mode, session_id, image_id, manifest, serial, size, readback_s) -> dict:
    if now_mode == OFF:
        return {"serial": serial, "verdict": "restart", "enforced": False, "reason": "המוח כבוי", "ledger": {}}
    with _lock:
        given = _decided.get((session_id, serial))
        cand = _candidates.get((serial, size))
    if given is not None:
        return given
    parts = {p["index"]: p for p in manifest.get("partitions") or []
             if isinstance(p, dict) and type(p.get("index")) is int and type(p.get("readback_chunks")) is int}
    total = sum(p["readback_chunks"] for p in parts.values())
    ledger, written, pace, pace_from = {}, None, None, "אין ledger"
    if cand is not None and cand.image_id == image_id:
        # רק מחיצה שהאינדקס שלה הוא של הסבב: אחרת אין זה המשך של אותה כתיבה (`chunkledger.adopt`)
        ledger = {str(p): n for p, (xxh, n, _c) in cand.parts.items()
                  if p in parts and parts[p].get("index_xxh64") == xxh}
        written, pace, pace_from = sum(ledger.values()), cand.pace_bps, cand.pace_from
    d = decide(total_chunks=total, written_chunks=written, readback_s=readback_s, pace_bps=pace)
    enforced = now_mode == ENFORCE
    verdict = "resume" if d.verdict == RESUME else "restart"
    memory.record(conn, mode=now_mode, rule=RULE, point=POINT_REWRITE, action="RESUME_OR_RESTART",
                  verdict=d.verdict, reason=d.reason,
                  evidence=(*d.evidence, f"pace_from={pace_from}", f"readback_min_mbps={READBACK_MIN_MBPS}",
                            f"readback_slack_s={READBACK_SLACK_S}"),
                  round_id=session_id, entity=("disk", serial),
                  outcome=f"{SENT_HE}: {verdict}" if enforced else SHADOW_HE)
    result = {"serial": serial, "verdict": verdict, "enforced": enforced, "reason": d.reason, "ledger": ledger,
              "readback": readback_limits()}
    with _lock:
        if len(_decided) > 4096:                    # גלים ישנים: תשובה חוזרת לגל שנגמר כבר אינה נשאלת
            _decided.clear()
        _decided[(session_id, serial)] = result
    return result
