"""KT2: כיול סף החשד (`Config.suspect_s`) לכל משכפל, ממה שהמוח ראה -- "המוח שולט בספים" (נדב 14/11 בערב).

**מה נצפה.** ה-tick (`tick.run`) רואה משכפל שנהיה SUSPECT (אין ראיה כבר `suspect_s`) ואחר כך חוזר לחיים (ALIVE או
STALLED) בלי ש-LOST נקבע. זו שתיקה שנגמרה מעצמה, והיא נרשמת כשורת K1 ``RECOVERED`` על המשכפל ב-``brain_decisions``
(זיכרון ארוך-הטווח, #3185; **בלי שינוי סכמה**), עם ``silence_s=N``: כמה זמן בערך שתק. ה-N הוא **חסם עליון**: הגיל של
הראיה כשנהיה חשוד, ועוד הזמן עד ה-tick שראה אותו חי (ה-tick רץ לכל היותר פעם ב-10 ש'). חסם עליון = סף גבוה יותר =
פחות רעש, לא יותר.

**מה נלמד (`learn`).** משכפל שיש עליו לפחות ``MIN_OBSERVATIONS`` תצפיות מ-``MIN_ROUNDS`` סבבים שונים בחלון הזיכרון:
הסף שלו הוא האחוזון ה-``PERCENTILE`` של השתיקות שנגמרו מעצמן, כפול ``MARGIN``, מעוגל כלפי מעלה, **לא פחות מערך ההתחלה**
(תצפית נעשית רק על שתיקה שכבר עברה את הסף, ולכן אין ממה ללמוד שהסף צריך לרדת) **ולא יותר מחצי מ-LOST**
(``CAP_OF_LOST``: חשד חייב לבוא לפני אובדן, אחרת אין בו תועלת).

**עיקרון 5.** כשאין מספיק היסטוריה, או שהיא לא נקראה, הערך הוא ערך ההתחלה (30), והסיבה נרשמת: היא כתובה בשורת
ה-SUSPECT עצמה (`decide.liveness`, ``Config.suspect_note``). "אין מספיק" ו"לא נקרא" הם שתי סיבות שונות.

**מה זה משנה.** רק את התווית SUSPECT מול ALIVE ושורות ה-K1 ב-tick. ``decide.BLOCKING`` כולל את שניהם, ולכן K2 מחליט
אותו דבר עם סף מכויל ובלעדיו: הכיול אינו יכול להחזיק פעולה הרסנית שלא הייתה מוחזקת, ולא לשחרר אחת שהייתה. הוא נרשם בצל
כמו כל המוח. כל המספרים כאן הערכות (`estimate`), לא מדידות.

**לעולם אינו זורק** אל ה-tick או אל הווטו.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE, POINT, VERDICT = "K1", "KT2", "RECOVERED"
#: כמו `learn.boot.MIN_SAMPLES` ו-`recall`: פחות משלוש תצפיות אינן דפוס, ופחות משלושה סבבים הן מקרה אחד.
MIN_OBSERVATIONS = 3
MIN_ROUNDS = 3
#: האחוזון (nearest-rank) של השתיקות שנגמרו מעצמן, והמרווח מעליו. הערכה.
PERCENTILE = 0.9
MARGIN = 1.25
#: הסף לא עולה מעל החלק הזה של LOST.
CAP_OF_LOST = 0.5
#: כמה תצפיות אחרונות של משכפל אחד נקראות (כמו `recall.LIMIT`).
LIMIT = 500
RECOVERED_HE = "הראיה חזרה בלי ש-LOST נקבע"
_SILENCE = re.compile(r"^silence_s=(\d+)$")


@dataclass(frozen=True)
class Learned:
    threshold_s: float
    reason: str
    observations: int = 0
    calibrated: bool = False


def record(conn, mac: str, round_id: str, silence_s: float, threshold_s: float, *, mode: str) -> int | None:
    """תצפית אחת: שתיקה שנגמרה מעצמה. מחזיר את מספר השורה, או ``None`` (נרשם בלוג, לא נזרק)."""
    n = int(silence_s)
    return memory.record(conn, mode=mode, rule=RULE, point=POINT, action="LIVENESS", verdict=VERDICT,
                         reason=f"הראיה חזרה אחרי כ-{n}s בלי ראיה (הסף היה {threshold_s:g}s), ובלי ש-LOST נקבע",
                         evidence=(f"silence_s={n}", f"threshold_s={threshold_s:g}"), round_id=round_id,
                         entity=("machine", mac), outcome=RECOVERED_HE)


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat(timespec="seconds")


def _silence(row: dict) -> int | None:
    for item in row.get("evidence") or ():
        m = _SILENCE.match(item) if isinstance(item, str) else None
        if m:
            return int(m.group(1))
    return None


def learn(conn, mac: str, *, now: float, start_s: float, lost_s: float, max_age_s: float) -> Learned:
    """הסף של משכפל אחד, והסיבה לו. כל כשל בקריאה = ערך ההתחלה וסיבה "לא נקרא"."""
    try:
        rows = memory.decisions(conn, kind="machine", key=mac, rule=RULE, since=_iso(now - max_age_s), limit=LIMIT)
        seen = [(_silence(r), r.get("round_id")) for r in rows if r.get("verdict") == VERDICT]
        seen = [(s, rnd) for s, rnd in seen if s is not None]
    except Exception:                       # noqa: BLE001 — הכיול אינו מפיל את ה-tick או הווטו
        log.exception("brain: the suspect history of %s was not read", mac)
        return Learned(start_s, f"ערך התחלה {start_s:g}s: ההיסטוריה לא נקראה")
    rounds = {rnd for _s, rnd in seen if rnd}
    if len(seen) < MIN_OBSERVATIONS or len(rounds) < MIN_ROUNDS:
        return Learned(start_s, f"ערך התחלה {start_s:g}s: אין מספיק היסטוריה ({len(seen)} שתיקות שנגמרו מעצמן ב-"
                                f"{len(rounds)} סבבים, נדרשות {MIN_OBSERVATIONS} ב-{MIN_ROUNDS})", len(seen))
    silences = sorted(s for s, _rnd in seen)
    rank = silences[max(math.ceil(PERCENTILE * len(silences)) - 1, 0)]
    wanted = math.ceil(rank * MARGIN)
    cap = math.floor(lost_s * CAP_OF_LOST)
    if wanted <= start_s:
        return Learned(start_s, f"ערך התחלה {start_s:g}s: השתיקות שלו קצרות ממנו ({len(seen)} תצפיות, עד {rank}s)",
                       len(seen))
    if wanted > cap:
        return Learned(float(max(cap, start_s)),
                       f"מכויל {cap}s, תקרה של חצי מ-LOST: {len(seen)} תצפיות, האחוזון ה-{int(PERCENTILE * 100)} "
                       f"{rank}s", len(seen), True)
    return Learned(float(wanted), f"מכויל {wanted}s: {len(seen)} תצפיות ב-{len(rounds)} סבבים, האחוזון ה-"
                                  f"{int(PERCENTILE * 100)} {rank}s כפול {MARGIN:g}", len(seen), True)


def apply(conn, cfg, macs, *, now: float):
    """`cfg` עם הסף המכויל ועם סיבת הסף, לכל אחד מ-`macs`. ללא `macs` (או מתג כבוי, שאינו קורא כאן) -- בלי שינוי."""
    by_mac, notes = dict(cfg.suspect_by_mac), dict(cfg.suspect_note)
    for mac in sorted(set(macs)):
        got = learn(conn, mac, now=now, start_s=cfg.suspect_s, lost_s=cfg.lost_s, max_age_s=cfg.max_age_s)
        notes[mac] = got.reason
        if got.threshold_s != cfg.suspect_s:
            by_mac[mac] = got.threshold_s
    return dataclasses.replace(cfg, suspect_by_mac=by_mac, suspect_note=notes)
