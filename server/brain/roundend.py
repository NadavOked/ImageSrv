"""#3064, כלל 6 (K10), #3276: סוף סבב וכיבוי -- בצל.

**המקרים.** 4 מתוך 27 סבבים ב-DB של LabRoot (12-14/11) נשארו פתוחים, הארוך 7:21 שעות. משכפל "דלוק ומחכה ל'כבה'"
(#2031) נספר ב-`room._unseen` כמי שהגל ממתין לו: `_rewake` שולח לו WoL שש פעמים (הוא דלוק -- WoL לא יעשה דבר),
והסבב נכשל רק אחרי ``REWAKE_MAX × REWAKE_SECONDS + REWAKE_BOOT_SECONDS``. וב-#1721 (ברזל 18/10) ה-WoL של פתיחת הגל
נשלח בשנייה שבה המשכפל עוד היה בספירת הכיבוי, ונבלע.

**מה הכלל אומר** (על גל פתוח של סבב חדר עם בחירת משכפלים):

- ``ROUND_END`` / ``CLOSE`` -- **אין עבודה פתוחה**: כל משכפל שעוד יש לו מגירה לכתוב דלוק ומחכה ל"כבה". הוא לא
  יצטרף לפני לחיצה, ולכן הסבב היה נסגר עכשיו, בשם המשכפל. התוצאה: מה הקוד הקיים עשה ואחרי כמה זמן -- או שהעבודה
  נפתחה מחדש (המשכפל הצטרף).
- ``WAKE`` / ``DEFER`` -- ה-WoL של פתיחת הגל נשלח למשכפל **בתוך ספירת הכיבוי שלו** (`gui_end_poweroff_seconds`,
  ברירת מחדל 20ש'), כלומר נבלע: היה ממתין שהמשכפל יכבה. התוצאה: אחרי כמה זמן מהפתיחה הוא הצטרף (14ש' טיפוסי מול 32ש',
  ברזל 13/11) -- או מה קרה לגל.

**מה לא משתנה** (נדב): המסך במשכפל. כשהכול ירוק -- צפצוף וכיבוי אחרי 20ש'; דיסק שנכשל -- אדום, המכונה לא נכבית
ומאפשרת להשלים. הכלל נוגע רק במה שהשרת עושה עם הסבב.

**צל בלבד.** סגירה וכיבוי בפועל הם שלב 5 ב-`server/brain/README.md` ("פעולות הרסניות"), ולכן גם ב-``enforce`` הכלל
רק רושם. המתג הוא ``brain_veto_mode``: ``off`` = לא מחושב ולא נרשם. **הכלל לעולם אינו זורק** אל `room.tick`.

**לא נמדד:** כמה זמן המשכפל נכבה אחרי שהספירה נגמרה. WoL באותו חלון אולי נבלע גם הוא; הכלל בודק את הספירה בלבד.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE = "K10"
ROUND_END, WAKE = "ROUND_END", "WAKE"
CLOSE, DEFER = "CLOSE", "DEFER"
OFF, ENFORCE = "off", "enforce"
SAY = "צל: הסבב לא נסגר ושום WoL לא נדחה -- הקוד הקיים ממשיך כרגיל"


@dataclass(frozen=True)
class Cloner:
    """משכפל שנבחר לסבב, כפי שהגל הפתוח רואה אותו."""
    mac: str
    name: str                       # מה שהמפעיל רואה ("משכפל 2")
    fresh: bool                     # יש לו מגירה נבחרת שעוד לא נכתבה
    held: bool                      # דלוק ומחכה ל"כבה" (#2031)
    joined: bool                    # הצטרף לגל הפתוח
    done_gap_s: float | None = None     # כמה שניות אחרי הדיווח הסופי האחרון שלו נפתח הגל; None = אין דיווח כזה


@dataclass(frozen=True)
class Finding:
    action: str
    verdict: str
    target: str                     # MAC ב-WAKE; "round" ב-ROUND_END
    reason: str
    evidence: tuple[str, ...] = ()


def judge(cloners: list[Cloner], end_s: int) -> list[Finding]:
    """טהור. מה K10 אומר על גל פתוח."""
    out = []
    work = [c for c in cloners if c.fresh and not c.held]
    held = [c for c in cloners if c.fresh and c.held and not c.joined]
    if held and not work:
        names = ", ".join(c.name for c in held)
        out.append(Finding(ROUND_END, CLOSE, "round",
                           f"אין עבודה פתוחה: {names} דלוק ומחכה ללחיצה על 'כבה' (דיסק עם בעיה) ולא יצטרף לגל."
                           " היה סוגר את הסבב עכשיו, בשם המשכפל -- לא שולח לו WoL שוב ושוב",
                           tuple(f"held={c.name}" for c in held)))
    for c in cloners:
        if c.joined or c.held or not c.fresh or c.done_gap_s is None or not 0 <= c.done_gap_s < end_s:
            continue
        out.append(Finding(WAKE, DEFER, c.mac,
                           f"ה-WoL של פתיחת הגל נשלח ל{c.name} {c.done_gap_s:.0f}ש' אחרי שסיים, בתוך ספירת הכיבוי"
                           f" ({end_s}ש'), ולכן נבלע. היה ממתין שהמשכפל יכבה ורק אז מעיר אותו",
                           (f"machine={c.name}", f"done_gap_s={c.done_gap_s:.0f}", f"end_poweroff_s={end_s}")))
    return out


#: ההחלטות הפתוחות של התהליך הזה: גל -> {"round", "rows": {(action, target): (מספר שורה, שעון מונוטוני)}}.
#: שרת שהופעל מחדש סוגר אותן כ"לא ידוע" (`memory._close_orphans`), ולכן אין צורך לשחזר אותן מה-DB.
_open: dict[str, dict] = {}


def _mode(conn) -> str:
    from server.brain import veto                # כמו ב-`budget.production`: המתג מגיע מהווטו
    return veto.mode(conn)


def forget() -> None:
    """לבדיקות: התהליך מתחיל בלי החלטות פתוחות."""
    _open.clear()


def _close(conn, wave_id: str, key: tuple[str, str], outcome: str) -> None:
    """התוצאה של החלטה פתוחה -- רק אם השורה באמת של K10 ושל הגל הזה ב-DB הזה (מספר שורה לבדו אינו ראיה)."""
    entry = _open.get(wave_id)
    if entry is None or key not in entry["rows"]:
        return
    row, at = entry["rows"].pop(key)
    if not entry["rows"]:
        _open.pop(wave_id, None)
    if row in {d["id"] for d in memory.decisions(conn, round_id=wave_id, rule=RULE, limit=64)}:
        memory.resolve(conn, [row], f"{outcome} ({max(time.monotonic() - at, 0):.0f}ש' אחרי ההחלטה)")


def observe(conn, *, round_id: str, wave_id: str, cloners: list[Cloner], end_s: int) -> list[Finding]:
    """גל פתוח: שופט, רושם החלטה חדשה פעם אחת לגל, וסוגר את מה שהשתנה. לעולם אינו זורק."""
    try:
        mode = _mode(conn)
        if mode == OFF:
            return []
        found = judge(cloners, end_s)
        keys = {(f.action, f.target) for f in found}
        by_mac = {c.mac: c for c in cloners}
        for action, target in list((_open.get(wave_id) or {}).get("rows", {})):
            if action == ROUND_END and (action, target) not in keys:
                _close(conn, wave_id, (action, target), "העבודה נפתחה מחדש: משכפל הצטרף או סיים להחזיק")
            elif action == WAKE and target in by_mac and by_mac[target].joined:
                _close(conn, wave_id, (action, target), f"{by_mac[target].name} הצטרף לגל")
        recorded = {(d["action"], d["entity_key"] if d["action"] == WAKE else "round")
                    for d in memory.decisions(conn, round_id=wave_id, rule=RULE, limit=64)} if found else set()
        for f in found:
            if (f.action, f.target) in recorded:
                continue
            entity = ("machine", f.target) if f.action == WAKE else ("round", round_id)
            row = memory.record(conn, mode=mode, rule=RULE, action=f.action, verdict=f.verdict, reason=f.reason,
                                evidence=f.evidence, round_id=wave_id, entity=entity)
            _open.setdefault(wave_id, {"round": round_id, "rows": {}})["rows"][(f.action, f.target)] = \
                (row, time.monotonic())
            say = SAY + (" (ל-K10 אין אכיפה לפני שלב 5)" if mode == ENFORCE else "")
            log.info("brain %s", json.dumps({"mode": mode, "rule": RULE, "action": f.action, "verdict": f.verdict,
                                              "round": wave_id, "target": f.target, "say": say,
                                              "reason": f.reason, "evidence": list(f.evidence)},
                                             ensure_ascii=False))
        return found
    except Exception:                           # noqa: BLE001 -- הסבב ממשיך גם כשהמוח נכשל
        log.exception("brain: K10 on %s failed", wave_id)
        return []


_STATE_HE = {"active": "עדיין פעיל", "closing": "בסגירה", "failed": "נכשל", "closed": "נסגר"}


def settle(conn, open_wave_id: str | None) -> None:
    """כל החלטה של גל שכבר אינו הגל הפתוח מקבלת את התוצאה שלה: מה הקוד הקיים עשה עם הסבב. לעולם אינו זורק."""
    for wave_id in [w for w in _open if w != open_wave_id]:
        try:
            round_id = _open[wave_id]["round"]
            row = conn.execute("SELECT state, failed_reason, wave_session_id FROM room_rounds WHERE id = ?",
                               (round_id,)).fetchone()
            if row is None:
                what = "הסבב לא נמצא"
            elif row["state"] == "active" and row["wave_session_id"] == wave_id:
                what = "הגל יצא לדרך"
            elif row["state"] == "active":
                what = "הקוד הקיים פתח גל חדש"
            else:
                what = f"הקוד הקיים: הסבב {_STATE_HE.get(row['state'], row['state'])}" \
                       + (f" -- {row['failed_reason']}" if row["failed_reason"] else "")
            for key in list(_open[wave_id]["rows"]):
                _close(conn, wave_id, key, what)
        except Exception:                       # noqa: BLE001
            log.exception("brain: K10 outcome of %s was not recorded", wave_id)
            _open.pop(wave_id, None)
