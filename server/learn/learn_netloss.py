"""‏#1846 — אבדן חבילות ברשת ההפצה מול הבסיס שלה: "קפץ — סוויץ'/כבל/IGMP".

הכרעת נדב 19/10: השרת לומד **ממדידה שלו**, ושום דבר אינו נחסם בגלל הלמידה —
כאן זו שורת יומן ושורה במסך הבריאות.

השידורים החוזרים של udp-sender (‏`loss_blocks`, ‏`record_stream` — המונה של
השולח עצמו) תלויים בקצב, במספר המגירות ובמקלטים — ולכן "1% אבדן" לבדו אינו
אומר דבר. מה שאומר משהו הוא **קפיצה** מול מה שאותה רשת עשתה באותם תנאים:

    יחס האבדן   = ‏`loss_blocks` / הבלוקים שנשלחו (‏`sent_bytes` / ‏`UDPCAST_BLOCK`)
    הבסיס       = חציון היחס של עד `WINDOW` הגלים הקודמים **על אותו כרטיס הפצה**
                  (‏`decision.link.interface`), **עם אותו מספר מגירות ואותו קצב**
    קפץ         = יחס ≥ `JUMP` × הבסיס, **וגם** מעל `drive_rates.LOSS_DOWN_RATIO`

"באותו מספר מגירות ובאותו קצב" הוא מה שה-Issue מבקש ("בלי שינוי במספר המגירות
או בקצב"): קפיצה שבאה עם קצב גבוה יותר היא חיפוש השיא של #1758, לא תקלה ברשת.
הרצפה ‏`LOSS_DOWN_RATIO` (‏0.1%) — מתחתיה udp-sender עצמו מדפיס "0.0%", ובסיס
של אפס היה הופך כל בלוק לקפיצה.

בלי בסיס — פחות מ-`MIN_WAVES` גלים כאלה — ‏`state: "unchecked"` ("לא נבדק"),
**לא** "תקין". גם `rcvbuf` (הקרנל של המקלטים, ‏`record_wave`) ו-`timeouts`
(‏`Timeout notAnswered` לכל GB) מחושבים לאותו בסיס ומוצגים — ההתראה רק על האבדן.

מה שנשמר: אין טבלה חדשה — הכול נגזר מ-`wave_rates` (#447/#1758). לעולם אינו זורק.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics

from server.rounds import drive_rates
from server.learn import server_io
from server.db import journal

log = logging.getLogger("imagesrv.learn_netloss")

#: כמה גלים קודמים באותם תנאים נכנסים לבסיס — כמו חלון הכוננים (‏`drive_rates.WINDOW`).
WINDOW = 5
#: בלי לפחות כמה גלים כאלה אין בסיס — "N" של ה-Issue. ⚠️ ניחוש מנומק: שלושה הם
#: המינימום שבו חציון אינו גל בודד.
MIN_WAVES = 3
#: פי כמה מהבסיס נחשב קפיצה — ההגדרה ב-Issue.
JUMP = 3
#: כמה גלים אחרונים סורקים — כמו `drive_rates.MEMORY_SCAN`.
SCAN = 50

JUMP_HE = "האבדן ברשת ההפצה קפץ — סוויץ'/כבל/IGMP"


def _load(row) -> dict | None:
    try:
        past = json.loads(row["decision"])
        stream = json.loads(row["stream"]) if row["stream"] else None
        drops = json.loads(row["drops"]) if row["drops"] else None
    except (TypeError, ValueError):
        return None
    if not isinstance(past, dict) or past.get("direct") or not isinstance(stream, dict):
        return None
    loss, sent = stream.get("loss_blocks"), stream.get("sent_bytes")
    if not drive_rates._count(loss) or not drive_rates._count(sent) or sent <= 0:
        return None                        # המונה לא נקרא — אינו מדידה (עיקרון 5)
    blocks = -(-sent // drive_rates.UDPCAST_BLOCK)
    roster = past.get("roster") or []
    rcvbuf = drive_rates._rcvbuf(drops, roster)
    timeouts = stream.get("timeouts")
    link = past.get("link") if isinstance(past.get("link"), dict) else {}
    return {"session_id": row["session_id"], "interface": link.get("interface"),
            "drawers": len(roster), "rate": past.get("max_bitrate"),
            "loss": loss / blocks, "loss_blocks": loss,
            "rcvbuf": None if rcvbuf is None else rcvbuf / blocks,
            "timeouts_per_gb": (timeouts * 10 ** 9 / sent
                                if drive_rates._count(timeouts) else None)}


def assess(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """הגל `session_id` מול הבסיס שלו. ‏None — הגל אינו מדידה (ישיר, המונה לא
    נקרא, אין שורה). אחרת `{"session_id", "interface", "drawers", "rate", "loss",
    "baseline", "waves", "rcvbuf", "rcvbuf_baseline", "timeouts_per_gb",
    "timeouts_baseline", "state"}` — ‏`state`: ‏`jump` / ‏`ok` / ‏`unchecked`."""
    rows = conn.execute(
        "SELECT session_id, decision, stream, drops FROM wave_rates"
        " ORDER BY rowid DESC LIMIT ?", (SCAN,)).fetchall()
    loaded = [w for w in (_load(r) for r in rows) if w is not None]
    me = next((w for w in loaded if w["session_id"] == session_id), None)
    if me is None:
        return None
    if server_io.bound(conn, session_id):          # ‏#1884: השרת האט, לא הרשת
        return {**me, "waves": 0, "baseline": None, "rcvbuf_baseline": None,
                "timeouts_baseline": None, "state": "server_disk"}
    later = True
    same = []
    for w in loaded:                       # מהחדש לישן — רק הגלים שלפני `me`
        if w["session_id"] == session_id:
            later = False
            continue
        if later or len(same) >= WINDOW:
            continue
        if (w["interface"], w["drawers"], w["rate"]) == (me["interface"], me["drawers"],
                                                         me["rate"]) \
                and not server_io.bound(conn, w["session_id"]):   # ‏#1884: לא בסיס
            same.append(w)
    out = {**me, "waves": len(same), "baseline": None, "rcvbuf_baseline": None,
           "timeouts_baseline": None, "state": "unchecked"}
    if len(same) < MIN_WAVES:
        return out
    out["baseline"] = statistics.median(w["loss"] for w in same)
    for key, base in (("rcvbuf", "rcvbuf_baseline"), ("timeouts_per_gb", "timeouts_baseline")):
        got = [w[key] for w in same if w[key] is not None]
        out[base] = statistics.median(got) if got else None
    jumped = (me["loss"] >= JUMP * out["baseline"]
              and me["loss"] > drive_rates.LOSS_DOWN_RATIO)
    out["state"] = "jump" if jumped else "ok"
    return out


def evaluate(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """נקרא כשהשולח סיים גל (‏`app.on_sender_event`, אחרי `record_stream`).
    קפיצה — שורת יומן `net_loss_jump`. לעולם אינו זורק."""
    try:
        got = assess(conn, session_id)
        if got is not None and got["state"] == "jump":
            journal(conn, "net_loss_jump",
                    f"{session_id} if={got['interface'] or '?'} drawers={got['drawers']}"
                    f" rate={got['rate']} loss={100 * got['loss']:.2f}%"
                    f" baseline={100 * got['baseline']:.2f}% ({got['waves']} waves)"
                    f" — {JUMP_HE}")
        return got
    except Exception:             # noqa: BLE001 — הלמידה אינה מפילה את סיום השידור
        log.exception("net loss assessment of %s failed", session_id)
        return None


def health_checks(conn: sqlite3.Connection) -> list[dict]:
    """שורה במסך הבריאות לכל כרטיס הפצה — לפי הגל האחרון שנמדד עליו.
    ‏`warn` = קפץ; ‏`ok` = בתוך הבסיס; ‏`off` = "לא נבדק" (אין בסיס עדיין).
    בלי אף גל שנמדד — אין שורה. כשל בשאילתה — שורה `off` שאומרת זאת."""
    from server.system.health import check                # noqa: PLC0415 — health מייבא אותנו
    try:
        rows = conn.execute(
            "SELECT session_id, decision, stream, drops FROM wave_rates"
            " ORDER BY rowid DESC LIMIT ?", (SCAN,)).fetchall()
        latest: dict = {}
        for w in (_load(r) for r in rows):
            if w is not None:
                latest.setdefault(w["interface"], w["session_id"])
        out = []
        for interface, session_id in sorted(latest.items(), key=lambda kv: kv[0] or ""):
            got = assess(conn, session_id)
            if got is None:
                continue
            label = f"אבדן ברשת ההפצה ({interface or 'כרטיס לא נקרא'})"
            now = f"{100 * got['loss']:.2f}%"
            if got["state"] == "server_disk":                # ‏#1884
                out.append(check(f"net_loss:{interface}", label, "off",
                                 f"לא נבדק — הגל האחרון היה מוגבל בדיסק הספרייה בשרת; {now}"))
            elif got["state"] == "unchecked":
                out.append(check(f"net_loss:{interface}", label, "off",
                                 f"לא נבדק — {got['waves']} מתוך {MIN_WAVES} גלים באותם תנאים"
                                 f" ({got['drawers']} מגירות, {got['rate']}); הגל האחרון: {now}"))
            else:
                base = f"{100 * got['baseline']:.2f}%"
                if got["state"] == "jump":
                    out.append(check(f"net_loss:{interface}", label, "warn",
                                     f"{JUMP_HE}: {now} מול בסיס {base} ({got['waves']} גלים,"
                                     f" {got['drawers']} מגירות, {got['rate']}) — {got['session_id']}"))
                else:
                    out.append(check(f"net_loss:{interface}", label, "ok",
                                     f"{now} מול בסיס {base} ({got['waves']} גלים)"))
        return out
    except Exception:             # noqa: BLE001 — "לא בדקנו" אינו "תקין"
        log.exception("net loss health failed")
        return [check("net_loss", "אבדן ברשת ההפצה", "off", "לא נבדק — הקריאה נכשלה")]
