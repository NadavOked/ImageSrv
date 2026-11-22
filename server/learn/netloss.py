"""#1846 — אבדן חבילות ברשת ההפצה מול הבסיס שלה: "קפץ — סוויץ'/כבל/IGMP".

הכרעת נדב 19/10: השרת לומד **ממדידה שלו**, ושום דבר אינו נחסם בגלל הלמידה —
כאן זו שורת יומן ושורה במסך הבריאות. "1% אבדן" לבדו אינו אומר דבר; מה שאומר משהו
הוא **קפיצה** מול מה שאותה רשת עשתה באותם תנאים.

#3164 — **גל חתיכות** (מנוע החתיכות, `chunksend`). #3173: המנוע הקודם נמחק, וזה המדד היחיד. המדד עצמו ומה שלא נמדד
על ברזל מתועדים ב-`docs/architecture/interfaces.md`, סעיף #3164; כאן העיקר:

    האבדן      = `abandoned` — חתיכות שהמקלט נטש באמצע (`chunkloss.sh`, G7), סכום על המחשבים שבגל,
                 לכל GB שהם קיבלו (`delivered`: בייטים שנכתבו / מגירות שסיימו, לכל מחשב)
    הבסיס      = חציון האבדן של עד `WINDOW` הגלים הקודמים **על אותו כרטיס הפצה** (`decision.link.interface`),
                 **עם אותו מספר מגירות ואותו קצב** — קפיצה שבאה עם קצב אחר היא הלמידה של הקצב, לא תקלה ברשת
    קפץ        = אבדן ≥ `JUMP` × הבסיס, **וגם** מעל `CHUNK_FLOOR_PER_GB`
    `local`    = מה שהקרנל והכרטיס של המקלטים עצמם זרקו (`rcvbuf` + `softnet` + `nic`, חבילות לכל GB):
                 מוצג ליד האבדן ואינו מכריע. אחד משלושת המונים לא נקרא — `None`
    גל שאינו מלמד: לא הסתיים ב-`send_done`; או נרשם בו `chunk_net_gap` (#3141: ניתוק — אינו בסיס, ואין בו
                 קפיצה), `state: "net_gap"`. כמו `server_disk` — הרשת אינה מואשמת בגל כזה

בלי בסיס — פחות מ-`MIN_WAVES` גלים כאלה — `state: "unchecked"` ("לא נבדק"), **לא** "תקין".
מה שנשמר: אין טבלה חדשה — הכול נגזר מ-`wave_rates` (#447/#1758). לעולם אינו זורק.
גל של המנוע הקודם מ-DB משודרג (#3173) אינו נושא `abandoned`, ולכן אינו מדידה ואינו בסיס: נשאר בטבלה ואינו נקרא.

⚠️ **לא נמדד על ברזל** (`untested-on-metal`): הרצפה `CHUNK_FLOOR_PER_GB` מנומקת ולא מכוילת.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics

from server.rounds import drive_rates
from server.learn import server_io
from server.db import journal
from server.system.health_rows import check

log = logging.getLogger("imagesrv.learn_netloss")

#: כמה גלים קודמים באותם תנאים נכנסים לבסיס — כמו חלון הכוננים (`drive_rates.WINDOW`).
WINDOW = 5
#: בלי לפחות כמה גלים כאלה אין בסיס — "N" של ה-Issue. ⚠️ ניחוש מנומק: שלושה הם
#: המינימום שבו חציון אינו גל בודד.
MIN_WAVES = 3
#: פי כמה מהבסיס נחשב קפיצה — ההגדרה ב-Issue.
JUMP = 3
#: כמה גלים אחרונים סורקים — כמו `drive_rates.MEMORY_SCAN`.
SCAN = 50

#: #3164: סוג הגל במדידה (`kind`). #3173: מנוע החתיכות הוא היחיד.
CHUNK = "chunk"
#: #3141: אירוע היומן של `chunksend` — חלקים לא הגיעו למחשב ברשת בשידור הראשון (ניתוק או אבדן).
CHUNK_GAP = "chunk_net_gap"
#: #3164: מתחת לכמה חתיכות נטושות לכל GB אין קפיצה: בלי רצפה, בסיס אפס
#: הופך חתיכה אחת לקפיצה. ⚠️ ניחוש מנומק, **לא נמדד על ברזל**: חתיכה אחת לכל GB שקיבל מחשב.
CHUNK_FLOOR_PER_GB = 1.0
#: #3164: המונים של הקרנל והכרטיס של המקלט עצמו. `inerrors` אינו כאן: הוא כולל את `rcvbuf` (ספירה כפולה).
LOCAL_FIELDS = ("rcvbuf", "softnet", "nic")
GB = 10 ** 9

JUMP_HE = "האבדן ברשת ההפצה קפץ — סוויץ'/כבל/IGMP"


def _load(row, seen: dict | None = None) -> dict | None:
    """הגל כמדידה אחת, או None — אינו מדידה (ישיר, מונה שלא נקרא, גל שלא הסתיים, גל ישן של המנוע הקודם)."""
    try:
        past = json.loads(row["decision"])
        drops = json.loads(row["drops"]) if row["drops"] else None
        delivered = json.loads(row["delivered"]) if row["delivered"] else None
    except (TypeError, ValueError):
        return None
    if not isinstance(past, dict) or past.get("direct"):
        return None
    return _load_chunk(row, past, drops, delivered, seen or {})       # #3164


def _load_chunk(row, past: dict, drops, delivered, seen: dict) -> dict | None:
    """#3164: גל חתיכות. כל מחשב ברשימת הגל חייב לדווח `abandoned` ולהיות מי שכתב (`done`) — מחשב אחד שלא נקרא
    הוא "לא נקרא" לכל הגל (סכום חלקי היה נקרא "נקי", עיקרון 5). גל ישן של המנוע הקודם (#3173) אינו נושא את `abandoned` — לא נכנס לכאן."""
    marks = seen.get(row["session_id"]) or ()
    roster = past.get("roster") or []
    macs = {d.get("mac") for d in roster if isinstance(d, dict)}
    if "send_done" not in marks or not macs or not isinstance(drops, dict) \
            or not isinstance(delivered, dict):
        return None                        # לא הסתיים, או אין מה לקרוא — אינו מדידה
    abandoned, gb, local = 0, 0.0, 0
    for mac in macs:
        got, wrote = drops.get(mac), delivered.get(mac)
        if not isinstance(got, dict) or not drive_rates._count(got.get("abandoned")) \
                or not isinstance(wrote, dict):
            return None
        done, written = wrote.get("done"), wrote.get("written_bytes")
        if not drive_rates._count(done) or done <= 0 or not drive_rates._count(written):
            return None
        abandoned += got["abandoned"]
        gb += written / done / GB          # מה שהמחשב קיבל: כל מגירה כתבה אותו זרם
        counts = [got.get(field) for field in LOCAL_FIELDS]
        local = None if local is None or not all(map(drive_rates._count, counts)) else local + sum(counts)
    if gb <= 0:
        return None
    link = past.get("link") if isinstance(past.get("link"), dict) else {}
    return {"session_id": row["session_id"], "kind": CHUNK, "gap": CHUNK_GAP in marks,
            "interface": link.get("interface"), "drawers": len(roster),
            "rate": past.get("max_bitrate"), "loss": abandoned / gb, "abandoned": abandoned,
            "local": None if local is None else local / gb}


def _seen(conn: sqlite3.Connection, ids: list[str]) -> dict:
    """session_id ← אילו מאירועי היומן שגל חתיכות נשען עליהם נרשמו לו. שאילתה אחת לכל הגלים."""
    if not ids:
        return {}
    out: dict = {}
    for r in conn.execute(
            f"SELECT round_id, event FROM journal WHERE event IN ('send_done', ?)"
            f" AND round_id IN ({','.join('?' * len(ids))})", (CHUNK_GAP, *ids)):
        out.setdefault(r["round_id"], set()).add(r["event"])
    return out


def _loaded(conn: sqlite3.Connection) -> list[dict]:
    """הגלים האחרונים שהם מדידה, מהחדש לישן."""
    rows = conn.execute(
        "SELECT session_id, decision, drops, delivered FROM wave_rates"
        " ORDER BY rowid DESC LIMIT ?", (SCAN,)).fetchall()
    # היומן נשאל רק על גלים שנושאים `abandoned` -- שורות ישנות של המנוע הקודם אינן מוסיפות שאילתה
    seen = _seen(conn, [r["session_id"] for r in rows if r["drops"] and '"abandoned"' in r["drops"]])
    return [w for w in (_load(r, seen) for r in rows) if w is not None]


def _show(value: float) -> str:
    return f"{value:.2f}/GB"


def _local_note(got: dict) -> str:
    """#3164: מה שהקרנל והכרטיס של המקלטים עצמם זרקו — ליד האבדן, לא במקומו. `None` = לא נקרא."""
    if got["local"] is None:
        return " · מה שהמקלטים עצמם זרקו לא נקרא"
    base = got.get("local_baseline")
    return f" · המקלטים עצמם זרקו {got['local']:.2f}/GB" + ("" if base is None else f" (בסיס {base:.2f})")


def assess(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """הגל `session_id` מול הבסיס שלו. None — הגל אינו מדידה (ישיר, המונה לא
    נקרא, אין שורה). אחרת `{"session_id", "kind", "interface", "drawers", "rate", "loss",
    "baseline", "waves", "local", "local_baseline", "state"}` — `state`: `jump` / `ok` /
    `unchecked` / `server_disk` / `net_gap` (#3164: גל עם ניתוק). `kind`: `chunk`
    (`loss` = חתיכות נטושות לכל GB)."""
    loaded = _loaded(conn)
    me = next((w for w in loaded if w["session_id"] == session_id), None)
    if me is None:
        return None
    none = {"waves": 0, "baseline": None, "local_baseline": None}
    if server_io.bound(conn, session_id):          # #1884: השרת האט, לא הרשת
        return {**me, **none, "state": "server_disk"}
    if me["gap"]:                                  # #3164, #3141: ניתוק — לא מאשימים ואין בסיס
        return {**me, **none, "state": "net_gap"}
    later = True
    same = []
    for w in loaded:                       # מהחדש לישן — רק הגלים שלפני `me`
        if w["session_id"] == session_id:
            later = False
            continue
        if later or len(same) >= WINDOW:
            continue
        if (w["interface"], w["drawers"], w["rate"]) == (me["interface"], me["drawers"], me["rate"]) \
                and not w["gap"] and not server_io.bound(conn, w["session_id"]):   # #1884, #3164: לא בסיס
            same.append(w)
    out = {**me, **none, "waves": len(same), "state": "unchecked"}
    if len(same) < MIN_WAVES:
        return out
    out["baseline"] = statistics.median(w["loss"] for w in same)
    got = [w["local"] for w in same if w["local"] is not None]
    out["local_baseline"] = statistics.median(got) if got else None
    out["state"] = "jump" if me["loss"] >= JUMP * out["baseline"] and me["loss"] > CHUNK_FLOOR_PER_GB else "ok"
    return out


def _note_for_brain(conn: sqlite3.Connection, session_id: str, got: dict) -> None:
    """B58 (#3471): LR16 אל המוח, צל -- שורה אחת לגל. כישלון כאן אינו נוגע ביומן ובמסך הבריאות שלמטה."""
    try:
        from server.brain import sendlimit               # noqa: PLC0415
        sendlimit.note_netloss(conn, session_id, got)
    except Exception:             # noqa: BLE001 — המוח אינו מפיל את סוף הגל
        log.exception("net loss note for the brain of %s failed", session_id)


def _evaluate(conn: sqlite3.Connection, session_id: str) -> dict | None:
    try:
        got = assess(conn, session_id)
        if got is None:
            return None
        _note_for_brain(conn, session_id, got)
        if got["state"] == "jump" and conn.execute(
                "SELECT 1 FROM journal WHERE event = 'net_loss_jump' AND round_id = ?",
                (session_id,)).fetchone() is None:        # גל נסגר פעמיים — שורה אחת
            local = "?" if got["local"] is None else f"{got['local']:.2f}/GB"
            journal(conn, "net_loss_jump",
                    f"{session_id} if={got['interface'] or '?'} drawers={got['drawers']}"
                    f" rate={got['rate']} loss={_show(got['loss'])}"
                    f" baseline={_show(got['baseline'])} ({got['waves']} waves)"
                    f" local={local} — {JUMP_HE}", round_id=session_id)
        return got
    except Exception:             # noqa: BLE001 — הלמידה אינה מפילה את סיום השידור
        log.exception("net loss assessment of %s failed", session_id)
        return None


def evaluate_chunk(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """#3164: נקרא כשהגל נסגר בחדר (`room`, אחרי `drive_rates.record_wave`): רק אז `wave_rates.drops` ו-`delivered`
    של גל חתיכות שלמים. לעולם אינו זורק.
    #3064 (X10): השיפוט של הגל מול הבסיס נרשם גם במוח (`server.brain.streamhit`), פעם אחת לגל."""
    got = _evaluate(conn, session_id)
    from server.brain import streamhit           # noqa: PLC0415 — המוח מעל הלמידה, לא להפך
    streamhit.judge(conn, session_id, got)
    return got


def health_checks(conn: sqlite3.Connection) -> list[dict]:
    """שורה במסך הבריאות לכל כרטיס הפצה — לפי הגל האחרון שנמדד עליו.
    `warn` = קפץ; `ok` = בתוך הבסיס; `off` = "לא נבדק" (אין בסיס עדיין).
    בלי אף גל שנמדד — אין שורה. כשל בשאילתה — שורה `off` שאומרת זאת."""
    try:
        latest: dict = {}
        for w in _loaded(conn):
            latest.setdefault(w["interface"], w["session_id"])
        out = []
        for interface, session_id in sorted(latest.items(), key=lambda kv: kv[0] or ""):
            got = assess(conn, session_id)
            if got is None:
                continue
            label = f"אבדן ברשת ההפצה ({interface or 'כרטיס לא נקרא'}, מנוע חתיכות)"
            now = _show(got["loss"])
            if got["state"] == "server_disk":                # #1884
                out.append(check(f"net_loss:{interface}", label, "off",
                                 f"לא נבדק — הגל האחרון היה מוגבל בדיסק הספרייה בשרת; {now}"))
            elif got["state"] == "net_gap":                  # #3164, #3141
                out.append(check(f"net_loss:{interface}", label, "off",
                                 f"לא נבדק — בגל האחרון חלקים לא הגיעו למחשב ברשת (ניתוק); {now}"))
            elif got["state"] == "unchecked":
                out.append(check(f"net_loss:{interface}", label, "off",
                                 f"לא נבדק — {got['waves']} מתוך {MIN_WAVES} גלים באותם תנאים"
                                 f" ({got['drawers']} מגירות, {got['rate']}); הגל האחרון: {now}"))
            else:
                base = _show(got["baseline"])
                if got["state"] == "jump":
                    out.append(check(f"net_loss:{interface}", label, "warn",
                                     f"{JUMP_HE}: {now} מול בסיס {base} ({got['waves']} גלים,"
                                     f" {got['drawers']} מגירות, {got['rate']}) — {got['session_id']}"
                                     + _local_note(got)))
                else:
                    out.append(check(f"net_loss:{interface}", label, "ok",
                                     f"{now} מול בסיס {base} ({got['waves']} גלים)"))
        return out
    except Exception:             # noqa: BLE001 — "לא בדקנו" אינו "תקין"
        log.exception("net loss health failed")
        return [check("net_loss", "אבדן ברשת ההפצה", "off", "לא נבדק — הקריאה נכשלה")]
