"""#1846 — אבדן חבילות ברשת ההפצה מול הבסיס שלה: "קפץ — סוויץ'/כבל/IGMP".

הכרעת נדב 19/10: השרת לומד **ממדידה שלו**, ושום דבר אינו נחסם בגלל הלמידה —
כאן זו שורת יומן ושורה במסך הבריאות.

השידורים החוזרים של udp-sender (`loss_blocks`, `record_stream` — המונה של
השולח עצמו) תלויים בקצב, במספר המגירות ובמקלטים — ולכן "1% אבדן" לבדו אינו
אומר דבר. מה שאומר משהו הוא **קפיצה** מול מה שאותה רשת עשתה באותם תנאים:

    יחס האבדן   = `loss_blocks` / הבלוקים שנשלחו (`sent_bytes` / `UDPCAST_BLOCK`)
    הבסיס       = חציון היחס של עד `WINDOW` הגלים הקודמים **על אותו כרטיס הפצה**
                  (`decision.link.interface`), **עם אותו מספר מגירות ואותו קצב**
    קפץ         = יחס ≥ `JUMP` × הבסיס, **וגם** מעל `drive_rates.LOSS_DOWN_RATIO`

"באותו מספר מגירות ובאותו קצב" הוא מה שה-Issue מבקש ("בלי שינוי במספר המגירות
או בקצב"): קפיצה שבאה עם קצב גבוה יותר היא חיפוש השיא של #1758, לא תקלה ברשת.
הרצפה `LOSS_DOWN_RATIO` (0.1%) — מתחתיה udp-sender עצמו מדפיס "0.0%", ובסיס
של אפס היה הופך כל בלוק לקפיצה.

בלי בסיס — פחות מ-`MIN_WAVES` גלים כאלה — `state: "unchecked"` ("לא נבדק"),
**לא** "תקין". גם `rcvbuf` (הקרנל של המקלטים, `record_wave`) ו-`timeouts`
(`Timeout notAnswered` לכל GB) מחושבים לאותו בסיס ומוצגים — ההתראה רק על האבדן.

מה שנשמר: אין טבלה חדשה — הכול נגזר מ-`wave_rates` (#447/#1758). לעולם אינו זורק.

#3164 — **גל חתיכות** (מנוע החתיכות, `chunksend`): אין שם `loss_blocks` ואין שידורים חוזרים, ולכן המדד אחר.
המדד עצמו ומה שלא נמדד על ברזל מתועדים ב-`docs/architecture/interfaces.md`, סעיף #3164; כאן העיקר:

    האבדן      = `abandoned` — חתיכות שהמקלט נטש באמצע (`chunkloss.sh`, G7), סכום על המחשבים שבגל,
                 לכל GB שהם קיבלו (`delivered`: בייטים שנכתבו / מגירות שסיימו, לכל מחשב)
    הבסיס      = כמו למעלה, **רק מגלי חתיכות**: שני המנועים אינם ברי השוואה (Issue #3164)
    `local`    = מה שהקרנל והכרטיס של המקלטים עצמם זרקו (`rcvbuf` + `softnet` + `nic`, חבילות לכל GB):
                 מוצג ליד האבדן ואינו מכריע (כמו `rcvbuf` של udpcast). אחד משלושת המונים לא נקרא — `None`
    גל שאינו מלמד: לא הסתיים ב-`send_done`; או נרשם בו `chunk_net_gap` (#3141: ניתוק — אינו בסיס, ואין בו
                 קפיצה), `state: "net_gap"`. כמו `server_disk` — הרשת אינה מואשמת בגל כזה

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

#: #3164: שני סוגי הגלים — הבסיס של כל אחד נלמד רק מגלים מהסוג שלו.
UDPCAST, CHUNK = "udpcast", "chunk"
#: #3141: אירוע היומן של `chunksend` — חלקים לא הגיעו למחשב ברשת בשידור הראשון (ניתוק או אבדן).
CHUNK_GAP = "chunk_net_gap"
#: #3164: מתחת לכמה חתיכות נטושות לכל GB אין קפיצה — כמו `LOSS_DOWN_RATIO` של udpcast: בלי רצפה, בסיס אפס
#: הופך חתיכה אחת לקפיצה. ⚠️ ניחוש מנומק, **לא נמדד על ברזל**: חתיכה אחת לכל GB שקיבל מחשב.
CHUNK_FLOOR_PER_GB = 1.0
#: #3164: המונים של הקרנל והכרטיס של המקלט עצמו. `inerrors` אינו כאן: הוא כולל את `rcvbuf` (ספירה כפולה).
LOCAL_FIELDS = ("rcvbuf", "softnet", "nic")
GB = 10 ** 9

JUMP_HE = "האבדן ברשת ההפצה קפץ — סוויץ'/כבל/IGMP"


def _load(row, seen: dict | None = None) -> dict | None:
    """הגל כמדידה אחת, או None — אינו מדידה (ישיר, מונה שלא נקרא, גל חתיכות שלא הסתיים)."""
    try:
        past = json.loads(row["decision"])
        stream = json.loads(row["stream"]) if row["stream"] else None
        drops = json.loads(row["drops"]) if row["drops"] else None
        delivered = json.loads(row["delivered"]) if row["delivered"] else None
    except (TypeError, ValueError):
        return None
    if not isinstance(past, dict) or past.get("direct"):
        return None
    if isinstance(stream, dict) and stream.get("loss_blocks") is not None:
        return _load_udpcast(row, past, stream, drops)
    return _load_chunk(row, past, drops, delivered, seen or {})       # #3164


def _load_udpcast(row, past: dict, stream: dict, drops) -> dict | None:
    loss, sent = stream.get("loss_blocks"), stream.get("sent_bytes")
    if not drive_rates._count(loss) or not drive_rates._count(sent) or sent <= 0:
        return None                        # המונה לא נקרא — אינו מדידה (עיקרון 5)
    blocks = -(-sent // drive_rates.UDPCAST_BLOCK)
    roster = past.get("roster") or []
    rcvbuf = drive_rates._rcvbuf(drops, roster)
    timeouts = stream.get("timeouts")
    link = past.get("link") if isinstance(past.get("link"), dict) else {}
    return {"session_id": row["session_id"], "kind": UDPCAST, "gap": False,
            "interface": link.get("interface"),
            "drawers": len(roster), "rate": past.get("max_bitrate"),
            "loss": loss / blocks, "loss_blocks": loss,
            "rcvbuf": None if rcvbuf is None else rcvbuf / blocks, "local": None,
            "timeouts_per_gb": (timeouts * 10 ** 9 / sent
                                if drive_rates._count(timeouts) else None)}


def _load_chunk(row, past: dict, drops, delivered, seen: dict) -> dict | None:
    """#3164: גל חתיכות. כל מחשב ברשימת הגל חייב לדווח `abandoned` ולהיות מי שכתב (`done`) — מחשב אחד שלא נקרא
    הוא "לא נקרא" לכל הגל (סכום חלקי היה נקרא "נקי", עיקרון 5). גל udpcast אינו נושא את `abandoned` — לא נכנס לכאן."""
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
            "rcvbuf": None, "local": None if local is None else local / gb, "timeouts_per_gb": None}


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
        "SELECT session_id, decision, stream, drops, delivered FROM wave_rates"
        " ORDER BY rowid DESC LIMIT ?", (SCAN,)).fetchall()
    # היומן נשאל רק על גלים שנושאים `abandoned` -- בהתקנה של udpcast בלבד אין שאילתה נוספת בסוף כל גל
    seen = _seen(conn, [r["session_id"] for r in rows if r["drops"] and '"abandoned"' in r["drops"]])
    return [w for w in (_load(r, seen) for r in rows) if w is not None]


def _show(kind: str, value: float) -> str:
    return f"{100 * value:.2f}%" if kind == UDPCAST else f"{value:.2f}/GB"


def _local_note(got: dict) -> str:
    """#3164: מה שהקרנל והכרטיס של המקלטים עצמם זרקו — ליד האבדן, לא במקומו. `None` = לא נקרא."""
    if got["kind"] != CHUNK:
        return ""
    if got["local"] is None:
        return " · מה שהמקלטים עצמם זרקו לא נקרא"
    base = got.get("local_baseline")
    return f" · המקלטים עצמם זרקו {got['local']:.2f}/GB" + ("" if base is None else f" (בסיס {base:.2f})")


def assess(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """הגל `session_id` מול הבסיס שלו. None — הגל אינו מדידה (ישיר, המונה לא
    נקרא, אין שורה). אחרת `{"session_id", "kind", "interface", "drawers", "rate", "loss",
    "baseline", "waves", "rcvbuf", "rcvbuf_baseline", "local", "local_baseline",
    "timeouts_per_gb", "timeouts_baseline", "state"}` — `state`: `jump` / `ok` /
    `unchecked` / `server_disk` / `net_gap` (#3164: גל חתיכות עם ניתוק). `kind`: `udpcast`
    (`loss` = יחס בלוקים) או `chunk` (`loss` = חתיכות נטושות לכל GB)."""
    loaded = _loaded(conn)
    me = next((w for w in loaded if w["session_id"] == session_id), None)
    if me is None:
        return None
    none = {"waves": 0, "baseline": None, "rcvbuf_baseline": None, "local_baseline": None,
            "timeouts_baseline": None}
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
        if (w["kind"], w["interface"], w["drawers"], w["rate"]) == (
                me["kind"], me["interface"], me["drawers"], me["rate"]) \
                and not w["gap"] and not server_io.bound(conn, w["session_id"]):   # #1884, #3164: לא בסיס
            same.append(w)
    out = {**me, **none, "waves": len(same), "state": "unchecked"}
    if len(same) < MIN_WAVES:
        return out
    out["baseline"] = statistics.median(w["loss"] for w in same)
    for key, base in (("rcvbuf", "rcvbuf_baseline"), ("local", "local_baseline"),
                      ("timeouts_per_gb", "timeouts_baseline")):
        got = [w[key] for w in same if w[key] is not None]
        out[base] = statistics.median(got) if got else None
    floor = drive_rates.LOSS_DOWN_RATIO if me["kind"] == UDPCAST else CHUNK_FLOOR_PER_GB
    out["state"] = "jump" if me["loss"] >= JUMP * out["baseline"] and me["loss"] > floor else "ok"
    return out


def _note_for_brain(conn: sqlite3.Connection, session_id: str, got: dict) -> None:
    """B58 (#3471): LR16 אל המוח, צל -- שורה אחת לגל. כישלון כאן אינו נוגע ביומן ובמסך הבריאות שלמטה."""
    try:
        from server.brain import sendlimit               # noqa: PLC0415
        sendlimit.note_netloss(conn, session_id, got)
    except Exception:             # noqa: BLE001 — המוח אינו מפיל את סוף הגל
        log.exception("net loss note for the brain of %s failed", session_id)


def _evaluate(conn: sqlite3.Connection, session_id: str, kind: str) -> dict | None:
    try:
        got = assess(conn, session_id)
        if got is None or got["kind"] != kind:
            return None
        _note_for_brain(conn, session_id, got)
        if got["state"] == "jump" and conn.execute(
                "SELECT 1 FROM journal WHERE event = 'net_loss_jump' AND round_id = ?",
                (session_id,)).fetchone() is None:        # גל נסגר פעמיים — שורה אחת
            local = "?" if got["local"] is None else f"{got['local']:.2f}/GB"
            journal(conn, "net_loss_jump",
                    f"{session_id} if={got['interface'] or '?'} drawers={got['drawers']}"
                    f" rate={got['rate']} loss={_show(kind, got['loss'])}"
                    f" baseline={_show(kind, got['baseline'])} ({got['waves']} waves)"
                    + ("" if kind == UDPCAST else f" local={local}")
                    + f" — {JUMP_HE}", round_id=session_id)
        return got
    except Exception:             # noqa: BLE001 — הלמידה אינה מפילה את סיום השידור
        log.exception("net loss assessment of %s failed", session_id)
        return None


def evaluate(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """נקרא כשהשולח סיים גל (`app.on_sender_event`, אחרי `record_stream`).
    קפיצה — שורת יומן `net_loss_jump`. לעולם אינו זורק."""
    return _evaluate(conn, session_id, UDPCAST)


def evaluate_chunk(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """#3164: נקרא כשהגל נסגר בחדר (`room`, אחרי `drive_rates.record_wave`): רק אז `wave_rates.drops` ו-`delivered`
    של גל חתיכות שלמים. גל udpcast — None (השולח כבר העריך אותו). לעולם אינו זורק.
    #3064 (X10): השיפוט של הגל מול הבסיס נרשם גם במוח (`server.brain.streamhit`), פעם אחת לגל."""
    got = _evaluate(conn, session_id, CHUNK)
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
            label = (f"אבדן ברשת ההפצה ({interface or 'כרטיס לא נקרא'}"
                     + (", מנוע חתיכות" if got["kind"] == CHUNK else "") + ")")
            now = _show(got["kind"], got["loss"])
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
                base = _show(got["kind"], got["baseline"])
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
