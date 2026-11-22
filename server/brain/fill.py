"""#3181 (K5, "השלמה"): המוח מבין כמה חסר למגירה שחזרה, ומחליט מאיפה להשלים. **במצב צל כברירת מחדל.**

**נדב, 14/11:** "המוח צריך להיות מסוגל להבין אם הדיסק חזר אחרי 2 שניות, כמעט לא חסר לו מידע, ואם הוא חזר
אחרי דקה וחצי, חסר לו הרבה מידע." **הכמות נמדדת, ולא מוערכת לפי הזמן:** הקלט הוא מה שהסוכן ספר ב-ledger של
המגירה מול האינדקס (``targets[].rejoin.missing``). משך השליפה כפול קצב השידור הוא רק בדיקת שפיות, בראיות.

**ההחלטה** (`decide`, פונקציה טהורה), אחת משלוש:

- ``FEW`` -- חסר מעט (עד `FEW_MAX_CHUNKS`): משלימים **משכנה אחת** (``NEIGHBOR``). אין שכנה = ``HTTP``; לא חסר
  כלום = ``NONE``.
- ``MANY`` -- חסר הרבה: משלימים **מכמה שכנות במקביל** (``NEIGHBORS``), והתקציב מתחלק ביניהן. אין שכנות = ``HTTP``.
- ``REPASS`` -- חסר הרבה **להרבה מגירות** בסבב (לפחות `REPASS_MIN_DRAWERS`, ולפחות `REPASS_SHARE` מהמגירות, בתוך
  `WINDOW_S`): מעבר שידור נוסף זול יותר מהשלמה של כל אחת לחוד (``PASS``).

שכנה = מגירה אחרת **באותו משכפל** שבאותו דיווח ``done`` או ``writing``, ושאינה משלימה בעצמה. ``done`` קודמת:
היא כבר לא כותבת את השידור, והקריאה ממנה אינה מתחרה בכתיבה (M1/M2, #3181).

**התקציב** (`step`, AIMD לכל משכפל): תקרת ההשלמה **100MiB/s לכל משכפל** -- M1/M2 על משכפל 2 (HP 8100, i5 650,
ברזל 14/11): מעל ~100MiB/s של קריאה בסך הכול כל המשכפל האט (27–31MiB/s כתיבה בשתי המגירות, ו-Rcvbuf עלה), ועד 100
זה היה בתוך הרעש. המוח **מוריד** אותה בחצי כשהוא רואה ``RcvbufErrors`` שעלו או חתיכות ``skipped`` שעלו במשכפל,
ומעלה בצעד אחד כשהשניים נמדדו ולא עלו. אין מדידה = לא זז ("לא נמדד" אינו "נקי"). לעולם לא מעל התקרה ולא מתחת
לרצפה. ``FEW`` לוקח את כל התקציב לשכנה אחת; ``MANY`` מחלק אותו שווה בין השכנות.

**הספים הם נקודת התחלה בקוד, לא הגדרות** (הכרעת נדב 14/11 בערב: "המוח צריך לשלוט בהם"). `FEW_MAX_CHUNKS`
ו-`REPASS_*` הם **הערכה** -- M3 (שליפה של 2 ש' ושל 90 ש') עוד לא נמדדה.

**הרישום** -- אותו מבנה של K7 (`budget.py`): שורת ``brain {...}`` בלוגר `imagesrv.brain`, ושורה ב-``brain_decisions``
(`memory.record`, ``rule="K5"``): ``FILL_SOURCE`` על הדיסק, ו-``FILL_BUDGET`` על המשכפל כשמשפחת ההחלטה משתנה ובכל
קיצוץ. **מצבים** -- המתג של הווטו (``brain_veto_mode``): ``off`` -- דבר לא מחושב; ``shadow`` (ברירת המחדל) --
מחושב ונרשם, והסוכן לא שומע עליו; ``enforce`` -- התוכנית חוזרת לסוכן בתשובה (``fill``).

**A27 (#3659, אפיק #3653, נדב 18/11: "כל ההחלטות למוח"): הגבולות של ההשלמה כבר אינם קבועים בסוכן.** `limits` אומר, לכל תוכנית
שהסוכן מבצע (משכנות ואז HTTP, או HTTP בלבד): התקרה (תקציב K5), כמה חתיכות שלא הגיעו שלמות שכנה מורשית לפספס לפני שלא שואלים אותה
עוד (``bad_max``), הרצפה של שכן-משכפל (``PEER_SPEED_*``, ``peer.py``) וכמה מחכים לתשובת המוח (``word_s``). **הערכים הם אלה שהיו קבועים
בסוכן** (``NF_BAD_MAX``, ``PF_SPEED_BPS``/``PF_SPEED_S``, ``FILL_WORD_S``): אותה התנהגות, ועכשיו הערך מגיע מכאן. נרשם ב-``brain_decisions``
(``rule="K-FILL"``, ``action="FILL_LIMITS"``) פעם אחת לכל (גל, מגירה, מחיצה), בצל וב-``enforce``; ב-``enforce`` ``bad_max`` ו-``word_s``
חוזרים בתוכנית (``fill``) ו-``speed_kibs``/``speed_s`` בהוראת ``peer_fill`` (``peer.py``). **אין החלטה** (הכלל נפל, תוכנית שהיא ``PASS``/
``NONE``, או צל) = התוכנית אינה נושאת אותם, והסוכן משתמש בקבוע שלו ורושם ביומן שהשתמש בו.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE = "K5"
OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
FEW, MANY, REPASS = "FEW", "MANY", "REPASS"
NEIGHBOR, NEIGHBORS, HTTP, PASS, NONE = "NEIGHBOR", "NEIGHBORS", "HTTP", "PASS", "NONE"
CUT_, RAISE, HOLD = "CUT", "RAISE", "HOLD"

MIB = 1024 * 1024
#: חתיכה במנוע החתיכות (`chunksend.CHUNK_BYTES`), לבדיקת השפיות בלבד.
CHUNK_BYTES = 4 * MIB
#: הערכה (M3 לא נמדדה): 64 חתיכות = 256MiB, כ-3–5 ש' של זרם ב-49–78MiB/s (סבבים 1 ו-2 של #3181, ברזל 14/11).
#: שליפה של 2 ש' משאירה כ-25–40 חתיכות; דקה וחצי -- כ-1,100–1,750.
FEW_MAX_CHUNKS = 64
#: הערכה: מעבר נוסף כשלפחות 2 מגירות, ולפחות מחצית מהמגירות של הסבב, חסרות הרבה בתוך 2 דקות.
REPASS_MIN_DRAWERS = 2
REPASS_SHARE = 0.5
WINDOW_S = 120.0
#: M1/M2, #3181 (ברזל 14/11, משכפל 2): מעל ~100MiB/s של קריאה בסך הכול המשכפל כולו האט.
CEILING_BPS = 100 * MIB
FLOOR_BPS = 8 * MIB
STEP_BPS = 8 * MIB
CUT = 0.5

#: A27 (#3659): הגבולות של ההשלמה, שהיו קבועים בסוכן. אותם ערכים, והמוח הוא עכשיו מקור הערך. **הערכה, לא מדידה** חוץ מהתקרה (M1/M2):
#: ``NF_BAD_MAX`` (`neighborfill.sh`), ``PF_SPEED_BPS``/``PF_SPEED_S`` (`peerfill.sh`), ``FILL_WORD_S`` (`fillword.sh`).
BAD_MAX = 3
PEER_SPEED_KIBS = 1024
PEER_SPEED_S = 30
WORD_S = 6
RULE_LIMITS, ACTION_LIMITS, POINT_LIMITS = "K-FILL", "FILL_LIMITS", "A27"
NEIGHBORS_THEN_HTTP, HTTP_ONLY = "NEIGHBORS_THEN_HTTP", "HTTP_ONLY"
#: תוכניות שהמוח כבר אמר עליהן את הגבולות, בזיכרון (תהליך אחד). מעל זה נשכחות הישנות, והשורה נרשמת שוב אם ההחלטה חוזרת.
MAX_TOLD = 512


# --- ההחלטה (טהורה) ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Need:
    """מה שהסוכן ספר למגירה שחזרה. ``None`` = לא נאמר (לעולם לא אפס)."""
    missing: int
    chunks: int | None = None
    part: int | None = None
    away_s: float | None = None


@dataclass(frozen=True)
class Plan:
    verdict: str                    # FEW / MANY / REPASS
    source: str                     # NEIGHBOR / NEIGHBORS / HTTP / PASS
    neighbors: tuple[str, ...]      # ה-dev של כל שכנה, לפי הסדר שבו קוראים ממנה
    per_neighbor_bps: int           # 0 כשלא קוראים משכנה
    budget_bps: int
    reason: str
    evidence: tuple[str, ...] = ()


def parse_need(raw) -> Need | None:
    """``targets[].rejoin`` -> `Need`, או ``None`` כשהשדה חסר או פגום (אין החלטה בלי ספירה)."""
    if not isinstance(raw, dict):
        return None

    def count(value) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    missing = count(raw.get("missing"))
    if missing is None:
        return None
    away = raw.get("away_s")
    away = float(away) if isinstance(away, (int, float)) and not isinstance(away, bool) and away >= 0 else None
    return Need(missing, count(raw.get("chunks")), count(raw.get("part")), away)


def join_policy(missing: int) -> str:
    """B34 (#3064, מ-NORM): שם מדיניות ההצטרפות ביומן. ``CURRENT`` = מצטרפת מהנקודה הנוכחית ולא חסר לה עבר; ``ALL`` = מצטרפת
    לשידור החי ומשלימה גם את כל מה שעבר. ה-docstring של `server/brain/completing.py` מסביר את ההבדל."""
    return "CURRENT" if missing == 0 else "ALL"


def _mib(bps: float) -> str:
    return f"{bps / MIB:.0f}MiB/s"


@dataclass(frozen=True)
class Limits:
    """A27: הגבולות של השלמה אחת. ``verdict`` = הסדר: ``NEIGHBORS_THEN_HTTP`` (שכנות קודם, ומה שלא הושלם שלם ב-HTTP) או ``HTTP_ONLY``."""
    verdict: str
    ceiling_bps: int                # תקציב K5 של המשכפל: כל הקריאות משכנות יחד
    bad_max: int                    # חתיכות שלא הגיעו שלמות משכנה לפני שלא שואלים אותה עוד
    peer_speed_kibs: int            # מתחת לזה, ל-`peer_speed_s` שניות, שכן-משכפל נכשל
    peer_speed_s: int
    word_s: int                     # כמה שניות הסוכן מחכה לתשובת המוח על המדידה
    reason: str
    evidence: tuple[str, ...] = ()


def limits(plan: Plan) -> Limits | None:
    """A27: התוכנית -> הגבולות שלה. ``None`` = אין החלטה (``PASS``: מעבר שידור, ``NONE``: לא חסר כלום): הסוכן משתמש בקבועים שלו
    ורושם שהשתמש. ההשלמה תמיד נגמרת ב-HTTP למה שלא הוכח שנכתב (עיקרון 5), ולכן "שכנות קודם ואז HTTP" אינו שאלה כאן."""
    if plan.source == HTTP:
        verdict, order = HTTP_ONLY, "HTTP בלבד: לא קוראים משכנות"
    elif plan.source in (NEIGHBOR, NEIGHBORS):
        verdict, order = NEIGHBORS_THEN_HTTP, "שכנות קודם, ומה שלא הושלם שלם ב-HTTP"
    else:
        return None
    reason = (f"{order}. תקרה {_mib(plan.budget_bps)} לכל הקריאות יחד; שכנה שלא מסרה {BAD_MAX} חתיכות שלמות לא נשאלת עוד; "
              f"שכן-משכפל מתחת ל-{PEER_SPEED_KIBS}KiB/s ל-{PEER_SPEED_S} ש' נכשל; הסוכן מחכה {WORD_S} ש' לתשובת המוח "
              f"(הערכים של הקבועים שהיו בסוכן)")
    evidence = (f"order={'http' if verdict == HTTP_ONLY else 'neighbors,then=http'}", f"ceiling={_mib(plan.budget_bps)}",
                f"bad_max={BAD_MAX}", f"peer_speed={PEER_SPEED_KIBS}KiB/s for {PEER_SPEED_S}s", f"word_s={WORD_S}",
                f"neighbors={len(plan.neighbors)}", "values=same as the agent's own defaults (NF_BAD_MAX, PF_SPEED_*, FILL_WORD_S)")
    return Limits(verdict, plan.budget_bps, BAD_MAX, PEER_SPEED_KIBS, PEER_SPEED_S, WORD_S, reason, evidence)


def decide(need: Need, neighbors: list[str], many_drawers: int, round_drawers: int, budget_bps: int,
           stream_bps: int | None = None) -> Plan:
    """(כמה חסר, השכנות, כמה מגירות חסרות הרבה בסבב, כמה מגירות בסבב, התקציב) -> התוכנית."""
    evidence = [f"missing={need.missing}", f"chunks={'unknown' if need.chunks is None else need.chunks}",
                f"neighbors={len(neighbors)}", f"many_drawers={many_drawers}/{round_drawers}",
                f"budget={_mib(budget_bps)}"]
    if need.away_s is not None and stream_bps:
        expected = need.away_s * stream_bps / CHUNK_BYTES
        evidence.append(f"sanity: away {need.away_s:.0f}s x {_mib(stream_bps)} = ~{expected:.0f} chunks")
    else:
        # #3814: WHICH input is missing -- `away` (the agent did not say how long the drawer was out; a new disk never does)
        # and/or `stream` (no rate sent, and none held). 19/11 LabRoot: 403 rows `away`, 159 rows `stream`.
        lacking = [name for name, got in (("away", need.away_s is not None), ("stream", bool(stream_bps))) if not got]
        evidence.append(f"sanity=unmeasured:{'+'.join(lacking)}")
    few = need.missing <= FEW_MAX_CHUNKS
    repass_at = max(REPASS_MIN_DRAWERS, math.ceil(round_drawers * REPASS_SHARE))
    if not few and many_drawers >= repass_at:
        return Plan(REPASS, PASS, (), 0, budget_bps,
                    f"חסרות הרבה חתיכות ל-{many_drawers} מגירות (מתוך {round_drawers}): מעבר שידור נוסף", tuple(evidence))
    if need.missing == 0:
        return Plan(FEW, NONE, (), 0, budget_bps, "לא חסר כלום: מצטרפת לשידור בלי השלמה", tuple(evidence))
    if not neighbors:
        verdict = FEW if few else MANY
        return Plan(verdict, HTTP, (), 0, budget_bps,
                    f"חסרות {need.missing} חתיכות ואין שכנה במשכפל שיש לה אותן: השלמה ב-HTTP", tuple(evidence))
    if few:
        return Plan(FEW, NEIGHBOR, (neighbors[0],), budget_bps, budget_bps,
                    f"חסרות מעט ({need.missing} חתיכות): השלמה משכנה אחת", tuple(evidence))
    per = max(budget_bps // len(neighbors), 1)
    return Plan(MANY, NEIGHBORS, tuple(neighbors), per, budget_bps,
                f"חסרות הרבה ({need.missing} חתיכות): השלמה מ-{len(neighbors)} שכנות במקביל, "
                f"{_mib(per)} מכל אחת", tuple(evidence))


def step(budget_bps: int | None, rcvbuf_delta: int | None, skipped_delta: int | None) -> tuple[int, str, str]:
    """מחזור אחד של התקציב: (התקציב הקודם, מה עלה מאז) -> (התקציב, הפעולה, הסיבה). ``None`` = לא נמדד."""
    prev = CEILING_BPS if budget_bps is None else budget_bps
    cuts = []
    if rcvbuf_delta:
        cuts.append(f"RcvbufErrors עלו ב-{rcvbuf_delta}")
    if skipped_delta:
        cuts.append(f"{skipped_delta} חתיכות דולגו (הדיסק פיגר)")
    if cuts:
        return max(FLOOR_BPS, int(prev * CUT)), CUT_, f"{'; '.join(cuts)}: תקרת ההשלמה הופחתה בחצי"
    if rcvbuf_delta is None and skipped_delta is None:
        return prev, HOLD, "אין מדידה של Rcvbuf או של skipped (לא 'נקי'): התקרה לא זזה"
    new = min(CEILING_BPS, prev + STEP_BPS)
    if new == prev:
        return prev, HOLD, "שקט, והתקרה כבר במקסימום שנמדד (100MiB/s למשכפל)"
    return new, RAISE, "שקט: תקרת ההשלמה עולה בצעד אחד"


# --- המצב והרישום ----------------------------------------------------------------------------------------------


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Brain:
    """המצב של K5 בשרת אחד: תקציב לכל משכפל, ומי חסר הרבה בחלון. כל המפתחות כוללים את הגל."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time):
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._budget: dict[tuple[str, str], int] = {}
        self._seen: dict[tuple[str, str], tuple[int | None, int | None]] = {}
        self._family: dict[tuple[str, str], str] = {}
        self._many: dict[tuple[str, str], float] = {}
        self._told: dict[tuple[str, str, int | None], tuple] = {}     # A27: (גל, מגירה, מחיצה) -> ההחלטה שנרשמה
        self._said: dict[tuple[str, str, int | None], tuple] = {}     # #3837: כמו _told, ל-FILL_SOURCE
        self._stream: dict[str, int] = {}                             # #3814: גל -> הקצב האחרון ששודר (בייטים לשנייה)

    def budget(self, session_id: str, mac: str) -> int:
        with self._lock:
            return self._budget.get((session_id, mac), CEILING_BPS)

    def observe(self, conn, round_row, mac: str, body: dict, *, mode: str, round_drawers: int,
                stream_bps: int | None = None) -> dict:
        """דיווח התקדמות -> תוכנית לכל מגירה שחזרה (``{serial: plan}``), רק ב-``enforce``. לעולם אינו זורק."""
        try:
            return self._observe(conn, round_row, mac, body, mode, round_drawers, stream_bps)
        except Exception:                       # noqa: BLE001 — מוח שנפל הוא שורת יומן, לא דיווח שנכשל
            log.exception("brain: the fill decision for %s failed", mac)
            return {}

    def _observe(self, conn, round_row, mac, body, mode, round_drawers, stream_bps) -> dict:
        if mode == OFF:
            return {}
        mode = mode if mode in (SHADOW, ENFORCE) else SHADOW
        session = body.get("session_id")
        targets = [t for t in body.get("targets") or [] if isinstance(t, dict)]
        self._tick_budget(conn, round_row, mac, session, targets, mode)
        out: dict = {}
        now = self._clock()
        stream_bps = self._hold_stream(session, stream_bps)
        needs = [(t, parse_need(t.get("rejoin"))) for t in targets]
        with self._lock:
            for t, need in needs:
                if need is not None and need.missing > FEW_MAX_CHUNKS:
                    self._many[(session, str(t.get("serial") or t.get("dev")))] = now
            self._many = {k: at for k, at in self._many.items() if now - at <= WINDOW_S}
            many = sum(1 for (s, _who) in self._many if s == session)
        for t, need in needs:
            if need is None:
                continue
            neighbors = sorted((n for n in targets if n is not t and n.get("rejoin") is None
                                and n.get("state") in ("done", "writing") and n.get("dev")),
                               key=lambda n: 0 if n.get("state") == "done" else 1)
            plan = decide(need, [str(n["dev"]) for n in neighbors], many, round_drawers,
                          self.budget(session, mac), stream_bps)
            who = str(t.get("serial") or t.get("dev") or "?")
            row = self._record(conn, round_row, mode, mac, session, who, plan, need)
            lim, lim_row = self._limits(conn, round_row, mode, mac, session, who, plan, need)
            if mode == ENFORCE:
                out[who] = {"verdict": plan.verdict, "source": plan.source, "neighbors": list(plan.neighbors),
                            "per_neighbor_bps": plan.per_neighbor_bps, "budget_bps": plan.budget_bps}
                if lim is not None:                      # A27: בלי החלטה התוכנית אינה נושאת אותם, והסוכן משתמש בקבוע שלו
                    out[who].update({"bad_max": lim.bad_max, "word_s": lim.word_s})
                    memory.resolve(conn, [lim_row], f"הגבולות bad_max={lim.bad_max} word_s={lim.word_s} נמסרו בתשובה למשכפל")
                # #3750: `out` חוזר לקורא ומשם לתשובה למשכפל; בצל השורה נרשמה כבר עם n/a
                memory.resolve(conn, [row], f"המקור {plan.source} נמסר בתשובה למשכפל")
        return out

    def _hold_stream(self, session, stream_bps: int | None) -> int | None:
        """#3814: הקצב שהגל ביקש בזמן ההיעדרות, גם אחרי שהגל הפסיק לשדר (`sending_bps` = 0 מחוץ ל-sending/repairing). קצב שנשלח
        נזכר לפי הגל; גל שלא שודר מעולם = ``None`` (לא נמדד), לעולם לא אפס."""
        if not isinstance(session, str):
            return stream_bps
        with self._lock:
            if stream_bps:
                self._stream[session] = stream_bps
                while len(self._stream) > 64:
                    self._stream.pop(next(iter(self._stream)))
                return stream_bps
            return self._stream.get(session)

    def _limits(self, conn, round_row, mode, mac, session, who, plan: Plan, need: Need) -> tuple[Limits | None, int | None]:
        """A27: הגבולות של התוכנית, ושורה ב-``brain_decisions`` כשהם חדשים (ומספרה; ``None`` כשלא נרשמה שורה). כלל שנפל = ``None`` (אין החלטה), ולא תוכנית שנופלת.
        #3750: בצל השורה נסגרת ``n/a`` כאן; ב-enforce המבצע (``plan``) סוגר אותה כשהגבולות נמסרים בתשובה."""
        try:
            lim = limits(plan)
        except Exception:                       # noqa: BLE001 — אין החלטה: הסוכן בקבועים שלו, התוכנית עצמה נשארת
            log.exception("brain: the completion limits of %s (%s) were not decided", who, mac)
            return None, None
        if lim is None:
            return None, None
        key, sig = (str(session), who, need.part), (lim.verdict, lim.ceiling_bps, lim.bad_max, lim.word_s)
        with self._lock:
            if self._told.get(key) == sig:
                return lim, None
            self._told[key] = sig
            while len(self._told) > MAX_TOLD:
                self._told.pop(next(iter(self._told)))
        self._log({"mode": mode, "rule": RULE_LIMITS, "action": ACTION_LIMITS, "verdict": lim.verdict, "mac": mac,
                   "disk": who, "part": need.part, "bad_max": lim.bad_max, "word_s": lim.word_s,
                   "peer_speed_kibs": lim.peer_speed_kibs, "peer_speed_s": lim.peer_speed_s,
                   "ceiling_mibps": round(lim.ceiling_bps / MIB, 1), "enforced": mode == ENFORCE, "reason": lim.reason})
        row = memory.record(conn, mode=mode, rule=RULE_LIMITS, point=POINT_LIMITS, action=ACTION_LIMITS, verdict=lim.verdict,
                      reason=lim.reason, evidence=(f"mac={mac}", f"part={'unmeasured' if need.part is None else need.part}",
                                                   *lim.evidence),
                      round_id=round_row["id"], entity=("disk", who),
                      outcome=memory.na_in_shadow(mode, "צל: נרשם בלבד, הסוכן לא שומע את הגבולות"))
        return lim, row

    def _tick_budget(self, conn, round_row, mac, session, targets, mode) -> None:
        rcvbuf = None
        try:
            row = conn.execute("SELECT udp_drops_json FROM session_members WHERE session_id = ? AND mac = ?",
                               (session, mac)).fetchone()
            got = json.loads(row[0]) if row is not None and row[0] else None
            rcvbuf = _int(got.get("rcvbuf")) if isinstance(got, dict) else None
        except Exception:                       # noqa: BLE001 — חוש שנפל הוא "לא נמדד"
            log.exception("brain: the Rcvbuf counter of %s could not be read", mac)
        told = [_int(t.get("skipped")) for t in targets if "skipped" in t]
        skipped = sum(told) if told and None not in told else None
        key = (session, mac)
        with self._lock:
            before = self._seen.get(key)
            self._seen[key] = (rcvbuf, skipped)
            prev = self._budget.get(key)
        if before is None:
            return                               # המחזור הראשון: אין הפרש עדיין
        delta = lambda a, b: b - a if a is not None and b is not None and b >= a else None   # noqa: E731
        new, action, why = step(prev, delta(before[0], rcvbuf), delta(before[1], skipped))
        with self._lock:
            self._budget[key] = new
            family = action if action == CUT_ else "TUNE"
            changed = self._family.get(key) != family or action == CUT_
            self._family[key] = family
        if not changed:
            return
        self._log({"mode": mode, "rule": RULE, "action": "FILL_BUDGET", "verdict": action, "mac": mac,
                   "budget_mibps": round(new / MIB, 1), "reason": why})
        # #3750: התקציב הוא קלט פנימי של FILL_SOURCE, לא פעולה: אין לו תוצאה משלו, וההשפעה שלו נראית בשורות FILL_SOURCE
        memory.record(conn, mode=mode, rule=RULE, action="FILL_BUDGET", verdict=action, reason=why,
                      evidence=(f"budget={_mib(new)}", f"ceiling={_mib(CEILING_BPS)}",
                                f"rcvbuf={'unmeasured' if rcvbuf is None else rcvbuf}",
                                f"skipped={'unmeasured' if skipped is None else skipped}"),
                      round_id=round_row["id"], entity=("machine", mac),
                      outcome=memory.na("תקציב פנימי של המוח: ההשפעה שלו נראית בשורות FILL_SOURCE"))

    def _record(self, conn, round_row, mode, mac, session, who, plan: Plan, need: Need) -> int | None:
        key, sig = (str(session), who, need.part), (plan.verdict, plan.source, tuple(plan.neighbors))
        with self._lock:                        # #3837: אותה החלטה בדיווח הבא אינה שורה חדשה (כמו _told של _limits)
            if self._said.get(key) == sig:
                return None                     # #3750: כפילות = אין שורה חדשה, ו-resolve מתעלם מ-None
            self._said[key] = sig
            while len(self._said) > MAX_TOLD:
                self._said.pop(next(iter(self._said)))
        self._log({"mode": mode, "rule": RULE, "action": "FILL_SOURCE", "verdict": plan.verdict,
                   "source": plan.source, "mac": mac, "disk": who, "missing": need.missing, "join": join_policy(need.missing),
                   "neighbors": list(plan.neighbors), "per_neighbor_mibps": round(plan.per_neighbor_bps / MIB, 1),
                   "enforced": mode == ENFORCE, "reason": plan.reason, "evidence": list(plan.evidence)})
        return memory.record(conn, mode=mode, rule=RULE, action="FILL_SOURCE", verdict=plan.verdict,
                             reason=plan.reason, evidence=(f"source={plan.source}", f"mac={mac}", f"join={join_policy(need.missing)}",
                                                           *plan.evidence),
                             round_id=round_row["id"], entity=("disk", who),
                             outcome=memory.na_in_shadow(mode, "צל: נרשם בלבד, המוח לא ביצע פעולה"))

    def _log(self, fields: dict) -> None:
        stamp = datetime.fromtimestamp(self._wall(), timezone.utc).isoformat(timespec="seconds")
        line = "brain " + json.dumps({"at": stamp, **fields,
                                      "say": "נאכף" if fields.get("mode") == ENFORCE
                                      else "מחושב ונרשם בלבד (צל)"}, ensure_ascii=False)
        (log.warning if fields.get("verdict") in (CUT_, REPASS) else log.info)("%s", line)
