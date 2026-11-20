"""#3462: תקרת רשת נלמדת לכל נתיב -- כרטיס ההפצה של השרת, וכל משכפל.

**התקרה** היא הקצב הגבוה ביותר שהוכח בלי אבדן מעל הסף. היא נגזרת מ-`wave_rates` (אין טבלה חדשה, כמו `learn/netloss.py`),
מ-`MEMORY_SCAN` הגלים האחרונים של מנוע החתיכות, ו**מראיה חיובית בלבד**:

- נכנס רק גל שלימד (`stream.chunk.teaches`: הסתיים ב-`send_done`, בלי ניתוק, ארוך מספיק), ושדיסק הספרייה לא עיכב
  אותו (`server_io` `basis != server_disk`). גל שהדיסק של השרת קבע את קצבו אינו אומר דבר על הרשת, לא לכאן ולא לכאן.
- אבדן שנמדד (`loss_permille` אינו `None`): "לא נמדד" אינו "אפס" (עיקרון 5).
- הקצב שהוכח = `min(asked_bps, sent_bps)` של גל נקי (אבדן עד הסף של `chunk_rate`): מה שהמנוע דחף בפועל, לא מה שהתבקש.
- **#3536 -- גל נקי הוא הוכחה לרשת רק כשהשולח לא היה הגבול.** תקרה היא "הרשת העבירה את זה בלי אבדן, והיא מי שהגבילה"; גל שהשולח
  עצמו האט אינו אומר דבר על הרשת. לכן גל מוכיח קצב רק אם **כל** אלה (ראיה חיובית, לא היעדר סימן): (א) דיסק הספרייה נמדד ולא קבע
  אותו (`server_io.basis == "ok"`; `None` = לא נמדד, אינו הוכחה); (ב) השולח נמדד ולא ישן לקצב שלו יותר מ-`SENDER_ASLEEP_MAX` מהמעבר
  הראשון (`server_io.sender_asleep`, `paced_ms`); (ג) הקצב ששלח (`sent_bps`) לפחות `KEEP_UP` מהקצב שהתבקש (`drive_rates.KEEP_UP`):
  השולח עמד בקצב. ברזל 16/11 (`metal-1116-r12`): בכל ריצות החתיכות השולח ישן 63-64% מהמעבר הראשון ושלח 349-372Mb/s מתוך 669m
  שהתבקשו, והתקרה שנלמדה (371m, 277m) הייתה הקצב האיטי של השולח. גל כזה לא מוכיח; הוא גם לא מבטל דבר (רק גל עם אבדן מבטל).
- גל עם אבדן מעל הסף **מבטל הוכחות ישנות מקצב שווה או גבוה ממנו** באותו נתיב, ולא הוכחות מקצב נמוך יותר.
- אין הוכחה = אין תקרה (`None`). אף פעם לא מספר שהומצא.

**נתיב** = כרטיס ההפצה של השרת (`decision.link.interface`) או משכפל (`mac` מרשימת הגל). `loss_permille` הוא של המחשב
הגרוע בגל ולא ידוע איזה, ולכן גל עם אבדן נחשב לכל משכפל שבו (שמרני: התקרה שלו נעלמת, לא יורדת למספר שהומצא).

**השימוש -- בצל בלבד:** `shadow` רושם ב-`brain_decisions` (כלל `K8`, פעולה `WAVE_NETCAP`, מצב `shadow`) מה היה קורה לקצב
של הגל אילו נלקח `min` עם התקרות הנלמדות. **הקצב אינו משתנה.** נקודות החיבור: `bound` (min טהור, ללא DB) --
ל-`waveeta` (#3295, עוד לא בבסיס) ולתקרת השרת (#3464, `_server_path`: `server:<device>` עם `{"bps": N}`): הן מוסיפות מפתחות למילון התקרות.

⚠️ לא נמדד על ברזל: הסף והחלון הם של `chunk_rate` (פתיחה, לא כויל על chunkcast).
"""

from __future__ import annotations

import json
import logging
import sqlite3

from server.brain import memory
from server.learn import server_cap, server_io
from server.rounds import drive_rates

log = logging.getLogger("imagesrv.brain")

RULE, ACTION, POINT = "K8", "WAVE_NETCAP", "#3462"
#: היכן הדגימה של גל חתיכות יושבת ב-`wave_rates.stream` -- אותו מפתח של `chunk_rate.STREAM_KEY` (הטסט מצמיד)
STREAM_KEY = "chunk"
CARD, CLONER, SERVER = "card", "cloner", "server"
WOULD_CAP, WITHIN, UNPROVEN = "WOULD_CAP", "WITHIN", "UNPROVEN"
SHADOW_OUTCOME_HE = "צל: הקצב של הגל לא שונה"


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def fmt(bps: int | None) -> str:
    return "?" if bps is None else f"{max(1, bps // 10 ** 6)}m"


def _basis(conn: sqlite3.Connection, session_id: str) -> str | None:
    """`server_io.basis` של הגל, או None -- לא נמדד (או לא נקרא). קריאה בלבד."""
    row = conn.execute("SELECT basis FROM server_io WHERE kind = 'wave' AND ref = ?", (session_id,)).fetchone()
    return row["basis"] if row is not None else None


def _pushed(sample: dict, disk: str | None) -> bool:
    """#3536: ראיה חיובית שהרשת היא מה שהגביל את הגל הנקי (ולא השולח או הדיסק), ולכן קצבו הוא הוכחה לה. שלושתן, ובלעדיהן לא:
    `server_io.basis == "ok"` (דיסק הספרייה נמדד ולא קבע; `None` = לא נמדד); השולח נמדד ולא ישן לקצב שלו מעל `SENDER_ASLEEP_MAX`
    (`server_io.sender_asleep`); והוא שלח לפחות `KEEP_UP` ממה שהתבקש (חסר או 0 = אין ראיה). ⚠️ `KEEP_UP` ו-`SENDER_ASLEEP_MAX`
    ניחוש מנומק של אחרים, לא כויל על chunkcast."""
    asked, sent = sample.get("asked_bps"), sample.get("sent_bps")
    return (disk == "ok" and server_io.sender_asleep(sample) is None and _count(sent) and sent > 0
            and sent >= drive_rates.KEEP_UP * asked)


def _waves(conn: sqlite3.Connection, loss_permille: float) -> list[dict]:
    """הגלים שמלמדים, מהחדש לישן: `{"session_id", "interface", "macs", "rate", "lossy", "disk", "pushed"}`.
    `rate` = מה שהמנוע דחף (`min(asked, sent)`; `asked` כשלא ידוע), `lossy` = האבדן שנמדד מעל `loss_permille`,
    `pushed` = ראיה חיובית שהשולח לא היה הגבול (`_pushed`, #3536): רק גל כזה מוכיח קצב.
    גל שאינו מלמד, או שדיסק הספרייה קבע אותו, או שהאבדן שלו לא נמדד -- אינו כאן."""
    out = []
    for row in conn.execute("SELECT session_id, decision, stream FROM wave_rates ORDER BY rowid DESC LIMIT ?",
                            (drive_rates.MEMORY_SCAN,)):
        try:
            decision = json.loads(row["decision"]) if row["decision"] else None
            sample = (json.loads(row["stream"]) if row["stream"] else {}).get(STREAM_KEY)
        except (TypeError, ValueError, AttributeError):
            continue
        if not isinstance(decision, dict) or decision.get("direct") or not isinstance(sample, dict):
            continue
        asked, sent, loss = sample.get("asked_bps"), sample.get("sent_bps"), sample.get("loss_permille")
        if sample.get("teaches") is not True or sample.get("skip") or not _number(loss) \
                or not _count(asked) or asked <= 0:
            continue
        disk = _basis(conn, row["session_id"])
        if disk == "server_disk":
            continue
        link = decision.get("link") if isinstance(decision.get("link"), dict) else {}
        roster = [d for d in decision.get("roster") or [] if isinstance(d, dict)]
        out.append({"session_id": row["session_id"], "interface": link.get("interface"),
                    "macs": {d["mac"] for d in roster if d.get("mac")},
                    "rate": min(asked, sent) if _count(sent) and sent > 0 else asked,
                    "lossy": loss > loss_permille, "disk": disk, "pushed": _pushed(sample, disk)})
    return out


def _of_path(waves: list[dict], kind: str, name: str) -> dict:
    low_lossy, best, proofs = None, None, 0
    for w in waves:                                           # מהחדש לישן
        if (w["interface"] == name) if kind == CARD else (name in w["macs"]):
            if w["lossy"]:
                low_lossy = w["rate"] if low_lossy is None else min(low_lossy, w["rate"])
            elif w["pushed"] and (low_lossy is None or w["rate"] < low_lossy):
                proofs += 1
                best = w if best is None or w["rate"] > best["rate"] else best
    return {"path": f"{kind}:{name}", "bps": None if best is None else best["rate"], "waves": proofs,
            "session_id": None if best is None else best["session_id"], "lossy_bps": low_lossy,
            "disk_unmeasured": best is not None and best["disk"] is None}


def learn(conn: sqlite3.Connection, interface: str | None, macs, loss_permille: float) -> dict[str, dict]:
    """לכל נתיב -- `{"path", "bps", "waves", "session_id", "lossy_bps", "disk_unmeasured"}`. `bps` None = אין הוכחה.
    `macs` = משכפלי הגל; `interface` = כרטיס ההפצה (None = לא נקרא: אין נתיב כרטיס). `waves` = כמה הוכחות נקיות
    שלא בוטלו (לא רק זו שנבחרה). לעולם אינו זורק: כשל בקריאה = מילון ריק (אין תקרה, לא תקרה שהומצאה)."""
    try:
        waves = _waves(conn, loss_permille)
        paths = ([(CARD, interface)] if interface else []) + [(CLONER, m) for m in sorted(m for m in macs if m)]
        return {f"{kind}:{name}": _of_path(waves, kind, name) for kind, name in paths}
    except Exception:             # noqa: BLE001 -- הלמידה אינה מפילה גל
        log.exception("brain: the learned network caps were not read")
        return {}


def bound(bits: int, caps: dict[str, dict]) -> tuple[int, str | None]:
    """`min` של הקצב עם כל תקרה שיש לה הוכחה: `(הקצב אחרי min, הנתיב שקבע או None)`. טהור, בלי DB.
    נקודת החיבור: מי שמוסיף תקרה (תקרת שרת, `waveeta`) מוסיף מפתח עם `{"bps": ...}`."""
    known = {name: c["bps"] for name, c in caps.items() if _count(c.get("bps")) and c["bps"] > 0}
    if not known:
        return bits, None
    who, low = min(known.items(), key=lambda kv: kv[1])
    return (low, who) if low < bits else (bits, None)


def _server_path(conn: sqlite3.Connection) -> dict[str, dict]:
    """#3464: תקרת הקריאה של דיסק הספרייה (`server_cap.learn`) כנתיב נוסף ב-`bound`, בצורה `{"bps": N}`. עוד לא נלמדה
    (`None`) = אין נתיב, לא נתיב עם 0. אינו זורק."""
    try:
        cap = server_cap.learn(conn)
        if cap is None:
            return {}
        return {f"{SERVER}:{cap.device}": {"path": f"{SERVER}:{cap.device}", "bps": cap.bps, "waves": len(cap.waves),
                                           "session_id": cap.waves[0] if cap.waves else None, "lossy_bps": None,
                                           "disk_unmeasured": False}}
    except Exception:             # noqa: BLE001 -- הלמידה אינה מפילה גל
        log.exception("brain: the server read cap was not added to the network caps")
        return {}


def shadow(conn: sqlite3.Connection, session_id: str, decision: dict, bits: int, loss_permille: float) -> None:
    """הגל יצא ב-`bits`: שורת צל אחת לגל -- מה היה הקצב אילו נלקח `min` עם התקרות הנלמדות. לא משנה דבר. לעולם אינו זורק."""
    try:
        if any(d["action"] == ACTION for d in memory.decisions(conn, round_id=session_id, rule=RULE, limit=64)):
            return                                            # גל אחד, שורה אחת
        link = decision.get("link") if isinstance(decision.get("link"), dict) else {}
        macs = [d.get("mac") for d in decision.get("roster") or [] if isinstance(d, dict)]
        caps = {**learn(conn, link.get("interface"), macs, loss_permille), **_server_path(conn)}
        would, who = bound(bits, caps)
        proved = [c for c in caps.values() if c["bps"] is not None]
        verdict = UNPROVEN if not proved else (WOULD_CAP if who else WITHIN)
        evidence = [f"{c['path']}={fmt(c['bps'])} waves={c['waves']} from={c['session_id']}"
                    f" lossy_at={fmt(c['lossy_bps'])}" + (" server_io=unmeasured" if c["disk_unmeasured"] else "")
                    for c in caps.values()] or ["no_paths"]
        reason = {UNPROVEN: f"{fmt(bits)}: אין הוכחה לאף נתיב (אין גל נקי שלימד על הרשת), בלי תקרה",
                  WITHIN: f"{fmt(bits)} בתוך כל התקרות שהוכחו",
                  WOULD_CAP: f"{fmt(bits)} -> {fmt(would)} לפי {who} (הקצב הגבוה ביותר שהוכח בלי אבדן)"}[verdict]
        memory.record(conn, mode="shadow", rule=RULE, point=POINT, action=ACTION, verdict=verdict, reason=reason,
                      evidence=[f"asked={fmt(bits)}", f"would={fmt(would)}", *evidence], round_id=session_id,
                      entity=("round", session_id), outcome=SHADOW_OUTCOME_HE)
    except Exception:             # noqa: BLE001 -- הלמידה אינה מפילה גל
        log.exception("brain: the learned network cap of %s was not recorded", session_id)
