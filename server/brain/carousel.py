"""#3064 (B7, B12, B47; X9, X21): מעבר multicast מלא נוסף ("carousel") במקום השלמה ממוקדת, כשהרבה משכפלים חסרים
הרבה. **ההחלטה רצה בצל ונרשמת. הביצוע מאחורי מתג משלו, כבוי כברירת מחדל** (``brain_carousel`` = ``on``).

**למה:** אחרי מעבר, `repair.plan` משדר שוב רק מה שחסר לשני מחשבים ומעלה, ומה שחסר למחשב אחד יורד ב-HTTP. כשהרבה
משכפלים חסרים הרבה, ובמקומות שונים (מגירות שנשלפו בזמנים שונים), כמעט הכול יורד ב-HTTP דרך הקישור של השרת: בטיוטת
התכנון (סעיף 6ג) ~18 דק' ל-6 משכפלים, מול ~8.5 דק' למעבר מלא. מעבר מלא, בלי בקשות מהמקלטים ובלי "רק מה שחסר", משלים
את כולם בשידור אחד.

**ההחלטה** (`decide`, טהורה), אחרי כל מעבר של מחיצה, מול התוכנית שהמנוע עומד להריץ:

- ``CAROUSEL`` (B7) -- לפחות שני משכפלים חסרים, והזמן של מעבר מלא (``t_pass``) קצר מהזמן של התוכנית (``t_plan``).
- ``KEEP`` -- לפחות שני משכפלים חסרים, והתוכנית מהירה יותר. נרשם, כדי שאפשר יהיה לראות כמה פעמים ה-carousel לא כדאי.
- ``LONE`` (B12, lone handoff) -- ב-carousel נשאר משכפל אחד חסר: לא משדרים לאחד. ה-carousel נגמר, וההשלמה ב-HTTP
  (זו בדיוק התוכנית של `repair.plan` למחשב אחד).
- ``SUSPEND`` (B47, Loop Suspend) -- ``EMPTY_PASSES`` (X21) מעברים מלאים ברצף שאף משכפל לא קיבל בהם חתיכה חדשה: עוצרים
  את ה-carousel, והתוכנית הרגילה חוזרת.
- ``UNDECIDED`` -- הגודל של החתיכות או קצב הזרם לא ידועים: "לא הוכרע", לא ברירת מחדל שקטה (עיקרון 5).

**המודל של הזמן** (הערכה, לא מדידה -- `research-3064` 1ד וטיוטת התכנון 6ג):

- שידור: הבתים הדחוסים של החתיכות (מהאינדקס) בקצב הזרם של הגל (``bits``, `ChunkEngine._Wave.bits`).
- HTTP: הקישור של השרת משותף, ולכן כל ההורדות של סבב נספרות יחד: ``OVERHEAD_S`` + החתיכות ÷ ``R_HTTP`` (KT4,
  `waveeta.R_HTTP_START`: 42 חתיכות/ש', הנמוך שנמדד על ברזל). השידור וההורדות באותו קישור, ולכן הזמנים מתחברים.
- ``t_plan`` = השידור הממוקד + ההורדות של הסבב + מה שנשאר למאזינים של הסבב (`repair.plan` נותן למאזין פעולה אחת בסבב:
  מה שחסר רק לו יורד בסבב הבא).
- ``t_pass`` = כל המחיצה בקצב הזרם. ``t_http`` (הכול ב-HTTP) נרשם בראיות בלבד, להשוואה.

**הצל:** ה-carousel של הצל עוקב אחרי ההחלטות, לא אחרי הביצוע: אחרי ``CAROUSEL`` הצל "נמצא ב-carousel", ושם ``LONE``
ו-``SUSPEND`` נרשמים. כך הצל רושם את כל המעגל גם כשהמנוע ממשיך בתוכנית הרגילה.

**הביצוע** (``brain_carousel`` = ``on`` **וגם** ``brain_veto_mode`` שאינו ``off``): ``CAROUSEL`` מחליף את התוכנית --
כל החתיכות של המחיצה במולטיקאסט לכל מי שחסר לו, ואין הורדות. בזמן מעבר כזה המנוע שואל את `cut` פעם ב-``POLL_S``:
נשאר מאזין חי אחד (B12) או אף אחד (B47) במשך ``LONE_DWELL_S`` (X9) -- המעבר נקטע, והסבב הבא מתוכנן מחדש.

**הספים הם נקודת התחלה, לא הגדרות** (נדב 14/11): ``LONE_DWELL_S`` = 20 ש' (R63, Sonnet: "LONE dwell 20 s", הערכה);
``EMPTY_PASSES`` = 1 (הערכה: ל-ASR אין מספר; מעבר מלא ריק הוא כל המחיצה ברשת בשביל כלום). ``R_HTTP`` יוחלף בנלמד
(`route.r_http_of`, #3342) כשימוזג. **לעולם אינו זורק** אל הגל: כשל הוא שורת לוג, והתוכנית הרגילה רצה.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from typing import Callable

from server.brain import memory, waveeta
from server.db import get_setting
from server.rounds import repair

log = logging.getLogger("imagesrv.brain")

RULE, ACTION, CUT_ACTION = "B7", "CAROUSEL", "CAROUSEL_CUT"
SETTING, ON, OFF = "brain_carousel", "on", "off"
CAROUSEL, KEEP, LONE, SUSPEND, UNDECIDED = "CAROUSEL", "KEEP", "LONE", "SUSPEND", "UNDECIDED"
#: X9: כמה שניות ב-carousel עם מאזין חי אחד (או אף אחד) עד שהמעבר נקטע. הערכה (R63, Sonnet).
LONE_DWELL_S = 20.0
#: X21: כמה מעברים מלאים ריקים ברצף עד שה-carousel נעצר (Loop Suspend). הערכה.
EMPTY_PASSES = 1
#: KT4 (ברזל 13/11): חתיכות בשנייה ב-HTTP, כל ההורדות של השרת יחד.
R_HTTP = waveeta.R_HTTP_START
#: `research-3064` 1ד: ``T ≈ 4 ש' + 0.02 ש' לחתיכה`` -- הקבוע של הורדה (חיבור, אינדקס, state).
OVERHEAD_S = 4.0


@dataclasses.dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()


def _zbytes(sizes: list[int], ranges: repair.Ranges) -> int:
    return sum(sum(sizes[lo:hi + 1]) for lo, hi in ranges)


def _http_s(chunks: int) -> float:
    return OVERHEAD_S + chunks / R_HTTP if chunks else 0.0


def lacks_of(chunks: int, durable: dict[str, dict[str, repair.Ranges]],
             failed: dict[tuple[str, str], str]) -> dict[str, repair.Ranges]:
    """מחשב ← מה שחסר לדיסקים שלו שלא נכשלו (איחוד). מחשב שאין לו חוסר אינו ברשימה."""
    out: dict[str, repair.Ranges] = {}
    for mac, disks in durable.items():
        lacks: repair.Ranges = []
        for serial, have in disks.items():
            if (mac, serial) not in failed:
                lacks = repair.union(lacks, repair.missing(chunks, have))
        if lacks:
            out[mac] = lacks
    return out


def decide(chunks: int, sizes: list[int] | None, bits: int | None, lacks: dict[str, repair.Ranges],
           plan: repair.Plan, *, carouseling: bool = False, empty: int = 0) -> Verdict | None:
    """(חתיכות המחיצה, הגודל הדחוס של כל חתיכה, קצב הזרם, מה שחסר לכל מחשב, התוכנית של `repair.plan`, האם ה-carousel
    כבר רץ, כמה מעברים מלאים ריקים ברצף) -> ההחלטה, או ``None`` כשאין מה להחליט (אין חוסר, או חוסר למחשב אחד מחוץ
    ל-carousel -- זו התוכנית הרגילה, ואין מה לרשום)."""
    needy = {mac: r for mac, r in lacks.items() if r}
    missing = sum(repair.count(r) for r in needy.values())
    evidence = [f"needy={len(needy)}", f"missing={missing}", f"chunks={chunks}", f"empty_passes={empty}"]
    if not needy:
        return Verdict(KEEP, "ה-carousel נגמר: לאף משכפל לא חסר דבר", tuple(evidence)) if carouseling else None
    if carouseling and empty >= EMPTY_PASSES:
        return Verdict(SUSPEND, f"{empty} מעברים מלאים ברצף ואף משכפל לא קיבל חתיכה חדשה (X21={EMPTY_PASSES}):"
                                " ה-carousel נעצר, והתוכנית הרגילה חוזרת", tuple(evidence))
    if len(needy) == 1:
        if not carouseling:
            return None
        (mac,) = needy
        return Verdict(LONE, f"ב-carousel נשאר משכפל אחד חסר ({mac}, {missing} חתיכות): לא משדרים לאחד,"
                             " והוא משלים ב-HTTP", tuple(evidence))
    if sizes is None or len(sizes) != chunks or not isinstance(bits, int) or isinstance(bits, bool) or bits <= 0:
        return Verdict(UNDECIDED, "לא הוכרע: הגודל הדחוס של החתיכות או קצב הזרם לא ידועים", tuple(evidence))
    byte_s = bits / 8
    t_pass = sum(sizes) / byte_s
    later = 0
    for mac in plan.receive:                     # מה שחסר רק למאזין יורד בסבב הבא (`repair.plan`)
        have = repair.missing(chunks, needy.get(mac, []))
        later += repair.count(repair.missing(chunks, repair.union(have, plan.multicast)))
    fetch = sum(repair.count(r) for r in plan.fetch.values())
    t_plan = _zbytes(sizes, plan.multicast) / byte_s + _http_s(fetch) + _http_s(later)
    t_http = _http_s(missing)
    evidence += [f"t_pass_s={t_pass:.0f}", f"t_plan_s={t_plan:.0f}", f"t_http_s={t_http:.0f}",
                 f"stream_mbps={bits / 1e6:.0f}", f"r_http={R_HTTP:g}", f"overhead_s={OVERHEAD_S:g}",
                 f"plan_multicast={repair.count(plan.multicast)}", f"plan_fetch={fetch}", f"plan_later={later}"]
    if t_pass < t_plan:
        return Verdict(CAROUSEL, f"{len(needy)} משכפלים חסרים {missing} חתיכות: מעבר מלא {t_pass:.0f} ש' מול"
                                 f" {t_plan:.0f} ש' להשלמה הממוקדת וה-HTTP", tuple(evidence))
    return Verdict(KEEP, f"{len(needy)} משכפלים חסרים {missing} חתיכות: ההשלמה הממוקדת וה-HTTP ({t_plan:.0f} ש')"
                         f" מהירות ממעבר מלא ({t_pass:.0f} ש')", tuple(evidence))


def full_pass(chunks: int, lacks: dict[str, repair.Ranges], plan: repair.Plan) -> repair.Plan:
    """התוכנית של מעבר מלא: כל החתיכות במולטיקאסט לכל מי שחסר לו, בלי הורדות. מה שנכשל נשאר נכשל."""
    return dataclasses.replace(plan, multicast=[(0, chunks - 1)], receive=set(lacks), fetch={})


class Carousel:
    """המצב בשרת אחד: לכל (גל, מחיצה) -- האם ה-carousel (של הצל או של הביצוע) רץ, כמה מעברים ריקים, וכמה היה חסר."""

    def __init__(self, conn, *, mode: Callable[[], str] | None = None, switch: Callable[[], str | None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.conn, self._mode_fn, self._switch_fn, self._clock = conn, mode, switch, clock
        self._lock = threading.Lock()
        self._state: dict[tuple[str, int], dict] = {}
        self._alone: dict[tuple[str, int], float] = {}

    def _mode(self) -> str:
        if self._mode_fn is not None:
            return self._mode_fn()
        from server.brain import veto            # noqa: PLC0415 -- המתג הכללי של המוח
        return veto.mode(self.conn)

    def _on(self) -> bool:
        raw = self._switch_fn() if self._switch_fn is not None else get_setting(self.conn, SETTING)
        return raw == ON

    def consider(self, session_id: str, part: int, rnd: int, chunks: int, durable: dict, plan: repair.Plan,
                 sizes: list[int] | None, bits: int | None) -> repair.Plan | None:
        """אחרי מעבר ``rnd`` של המחיצה: ההחלטה נרשמת; תוכנית חדשה רק כשהמתג דלוק וההחלטה ``CAROUSEL``. לעולם אינו זורק."""
        try:
            mode = self._mode()
            if mode == OFF:
                return None
            lacks = lacks_of(chunks, durable, plan.failed)
            key = (session_id, part)
            with self._lock:
                st = self._state.setdefault(key, {"on": False, "empty": 0, "missing": None})
                missing = sum(repair.count(r) for r in lacks.values())
                if st["on"] and st["missing"] is not None:
                    st["empty"] = st["empty"] + 1 if missing >= st["missing"] else 0
                got = decide(chunks, sizes, bits, lacks, plan, carouseling=st["on"], empty=st["empty"])
                st["on"] = got is not None and got.verdict == CAROUSEL
                st["missing"] = missing if st["on"] else None
                st["empty"] = st["empty"] if st["on"] else 0
            if got is None:
                return None
            act = got.verdict == CAROUSEL and self._on()
            memory.record(self.conn, mode=mode, rule=RULE, action=ACTION, verdict=got.verdict, reason=got.reason,
                          evidence=(*got.evidence, f"part={part}", f"pass={rnd}", f"executed={'yes' if act else 'no'}"),
                          round_id=session_id, entity=("round", session_id),
                          outcome=None if act else "צל: התוכנית הרגילה רצה")
            log.info("brain %s", json.dumps({"rule": RULE, "verdict": got.verdict, "round": session_id, "part": part,
                                              "pass": rnd, "reason": got.reason, "executed": act}, ensure_ascii=False))
            return full_pass(chunks, lacks, plan) if act else None
        except Exception:                       # noqa: BLE001 -- המוח אינו מפיל גל
            log.exception("brain: the carousel of %s, partition %s, failed", session_id, part)
            return None

    def cut(self, session_id: str, part: int, rnd: int, listening: list[str]) -> str | None:
        """בזמן מעבר מלא שהמתג הריץ: הסיבה לקטוע אותו, או ``None``. ``listening`` = המאזינים החיים של המעבר."""
        try:
            key, now = (session_id, part), self._clock()
            if len(listening) > 1:
                self._alone.pop(key, None)
                return None
            since = self._alone.setdefault(key, now)
            if now - since < LONE_DWELL_S:
                return None
            self._alone.pop(key, None)
            verdict, why = ((LONE, f"נשאר מאזין חי אחד ({listening[0]})") if listening
                            else (SUSPEND, "אין אף מאזין חי"))
            reason = f"{why} במעבר המלא {LONE_DWELL_S:g} ש' (X9): המעבר נקטע, והסבב הבא מתוכנן מחדש"
            memory.record(self.conn, mode=self._mode(), rule=RULE, action=CUT_ACTION, verdict=verdict, reason=reason,
                          evidence=(f"listening={len(listening)}", f"dwell_s={now - since:.0f}", f"part={part}",
                                    f"pass={rnd}"), round_id=session_id, entity=("round", session_id),
                          outcome="המעבר נקטע")
            return reason
        except Exception:                       # noqa: BLE001
            log.exception("brain: the carousel cut of %s failed", session_id)
            return None

    def forget(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k in self._state if k[0] == session_id]:
                self._state.pop(key, None)
            for key in [k for k in self._alone if k[0] == session_id]:
                self._alone.pop(key, None)
