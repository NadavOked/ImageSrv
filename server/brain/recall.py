"""#3064 שלב 5: המוח מחליט לפי מה שזכר -- הקריאה מהזיכרון (#3185) אל הצילום, והתצפית שמזינה אותו.

**הכלל היחיד שנבנה כאן: "משכפל שחוזר משתיקה" (K2, `decide.remembered`).** התכנון המלא, כולל הכללים שנבחנו ולא
נבנו ולמה: ראו #3064.

**התצפית (`returned`).** משכפל שהמוח קבע עליו LOST בהחלטת K2 (שורה ב-``brain_decisions`` עם ``mac=LOST`` בראיות),
**וחזר באותו סבב** -- דיווח progress כשהוא מסומן ``lost`` (`reports`), או hello מצטרף אחרי שאותחל (`room.rejoined_mid_stream`)
-- נרשם כשורת K1 ``RETURNED`` על המשכפל, עם השתיקה בשניות. השתיקה היא **חסם תחתון**: מההחלטה הראשונה שקבעה LOST
אחרי החזרה הקודמת, ועד עכשיו, ועוד `lost_wait_seconds` (ההחלטה נשאלת רק אחרי שתיקה כזו). חסם תחתון = החזקה קצרה יותר.

**הקריאה (`load`).** לכל משכפל שפעולה נשענת על השתיקה שלו: החזרות, והסבבים שבהם נקבע LOST ולא נרשמה חזרה, בתוך
`Config.max_age_s`. **דרך `memory.decisions` בלבד** -- נקודת הקריאה היחידה של הזיכרון (#3185).

**הספים אינם הגדרות** (נדב 14/11 בערב): `Config.min_returns` ו-`Config.max_age_s`, ערכי התחלה בקוד.

**שום דבר כאן אינו זורק** אל הנקודה ההרסנית, אל דיווח ההתקדמות או אל ה-hello: זיכרון שלא נקרא הוא ``unread``
(ההחלטה של היום), ותצפית שלא נרשמה היא שורת לוג.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Iterable

from server.agent import timing as agent_timing
from server.brain import memory
from server.brain.observe import Recall, epoch

log = logging.getLogger("imagesrv.brain")

#: **הספים של הזיכרון אינם הגדרות** (נדב 14/11 בערב): `Config.min_returns` (3 = כמו `learn.boot.MIN_SAMPLES` ו-
#: `learn.wol.MIN_SAMPLES`: פחות משלוש תצפיות אינן דפוס) ו-`Config.max_age_s` (90 יום = שמירת ההחלטות המלאות ב-
#: ``brain_decisions``, `housekeeping._rules`) הם ערכי התחלה בקוד. התוויות נשארות לשורות יומן ישנות של שינוי הגדרה.
LABELS_HE = {
    "brain_memory_min_returns": "המוח: חזרות משתיקה לפני שסומכים על הזיכרון (סבבים)",
    "brain_memory_max_age_days": "המוח: גיל הזיכרון שנחשב (ימים)",
}

RULE, ACTION, RETURNED = "K1", "RETURN", "RETURNED"
RETURNED_HE = "המשכפל חזר באותו סבב"
#: כמה החלטות אחרונות של משכפל אחד נקראות. מעבר לזה -- משכפל שנקבע LOST מאות פעמים בחלון -- נקרא החלק החדש בלבד.
LIMIT = 500
_SILENCE = re.compile(r"^silence_s=(\d+)$")


def _lost(row: dict, mac: str) -> bool:
    """החלטת K2 שקבעה את המשכפל LOST (הראיות של `veto._remember`: ``mac=STATE`` לכל משכפל בפעולה)."""
    return row["rule"] == "K2" and isinstance(row.get("evidence"), list) and f"{mac}=LOST" in row["evidence"]


def _returned(row: dict) -> bool:
    return row["rule"] == RULE and row["verdict"] == RETURNED


def _silence(row: dict) -> float | None:
    for item in row.get("evidence") or ():
        m = _SILENCE.match(item) if isinstance(item, str) else None
        if m:
            return float(m.group(1))
    return None


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat(timespec="seconds")


def one(conn, mac: str, *, now: float, max_age_s: float) -> Recall:
    """הזיכרון על משכפל אחד. כשל בקריאה = ``unread``, לא ריק."""
    try:
        rows = memory.decisions(conn, kind="machine", key=mac, since=_iso(now - max_age_s), limit=LIMIT)
    except Exception:                       # noqa: BLE001 — הווטו אינו נופל בגלל הזיכרון
        log.exception("brain: the memory of %s was not read", mac)
        return Recall(mac, unread=True)
    returns, losses = [], []
    by_round: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        if row.get("round_id"):
            by_round.setdefault(row["round_id"], []).append(row)
    for round_id, mine in by_round.items():
        pending = None                      # ההחלטה האחרונה שקבעה LOST ושעוד לא נרשמה אחריה חזרה
        for row in mine:
            if _lost(row, mac):
                pending = row
            elif _returned(row):
                silence, at = _silence(row), epoch(row["at"])
                if silence is not None and at is not None:
                    returns.append((row["id"], at, silence, round_id))
                pending = None
        lost_at = epoch(pending["at"]) if pending is not None else None
        if pending is not None and lost_at is not None:
            losses.append((pending["id"], lost_at, round_id))
    return Recall(mac, tuple(sorted(returns, key=lambda x: x[1])), tuple(sorted(losses, key=lambda x: x[1])))


def load(conn, macs: Iterable[str], *, now: float, max_age_s: float) -> tuple[Recall, ...]:
    return tuple(one(conn, mac, now=now, max_age_s=max_age_s) for mac in sorted(set(macs)))


def returned(conn, mac: str, round_id: str, *, now: float | None = None) -> int | None:
    """משכפל שהיה מסומן ``lost`` חזר באותו סבב. נרשם רק אם המוח קבע עליו LOST בסבב הזה, ופעם אחת לכל שתיקה.
    ב-``off`` לא נרשם דבר (כמו כל המוח). מחזיר את מספר השורה, או ``None``. לעולם אינו זורק."""
    try:
        from server.brain import veto                  # noqa: PLC0415 — veto קורא את הספים מכאן
        mode = veto.mode(conn)
        if mode == veto.OFF:
            return None
        now = time.time() if now is None else now
        rows = memory.decisions(conn, kind="machine", key=mac, round_id=round_id, limit=LIMIT)
        first = None                        # ההחלטה הראשונה שקבעה LOST אחרי החזרה האחרונה (החדשה ראשונה)
        for row in rows:
            if _returned(row):
                break
            if _lost(row, mac):
                first = row
        if first is None:
            return None
        start = epoch(first["at"])
        if start is None:
            return None
        silence = int(max(now - start, 0.0) + agent_timing.lost_seconds(conn))
        got = memory.record(conn, mode=mode, rule=RULE, point=ACTION, action="LIVENESS", verdict=RETURNED,
                            reason=f"חזר אחרי שתיקה של {silence}s לפחות", round_id=round_id,
                            evidence=(f"after=#{first['id']}", f"silence_s={silence}"),
                            entity=("machine", mac), outcome=RETURNED_HE)
        open_ = [r["id"] for r in rows if r["outcome"] is None and _lost(r, mac)]
        memory.resolve(conn, open_, RETURNED_HE)   # החזקה של הזיכרון שהתבררה כנכונה
        log.info("brain: %s returned in %s after %ss of silence (#%s)", mac, round_id, silence, got)
        return got
    except Exception:                       # noqa: BLE001
        log.exception("brain: the return of %s in %s was not remembered", mac, round_id)
        return None
