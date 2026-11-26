"""#2023 (הכרעת נדב 15/11), #3064 (K3): ההשלמה של דיסק איטי -- ממשיכה, או שהדיסק נכשל בשמו וייכתב בגל הבא. **בצל: רושם בלבד.**

**הכרעת נדב, 15/11:** בהשלמה עוצרים רק את המגירה שנפגעה. **המוח מחליט** מה נחשב איטי מספיק כדי להוריד אותה, ומה ממשיך
להשלים. אין תקרת זמן קבועה. השתיקה (`FETCH_SLOW_S`, `_fail_silent`) נשארת כמו היום: הורדה שלא מתקדמת אינה כאן בכלל.
ההחלטה רצה תמיד, ורושמת; **הביצוע** (שאר המגירות עוברות למחיצה הבאה בזמן שהמגירה הזו משלימה ברקע, או שהדיסק נכשל בשמו וייכתב
בגל הבא) שייך להגדרה ``brain_drawer_hold``: ``off`` / ``slow`` / ``on``. **#3605 (נדב 17/11): ברירת המחדל ``slow``** -- מבוצע רק דיסק שהמוח סימן
איטי (``DROP`` פעמיים), וכהחזקה (משלים ברקע, **אינו נכשל**); ``on`` הוא הביצוע המלא שלמטה. כבויה: `evaluate` רושם ומחזיר דבר ריק, ושום דבר אינו משתנה
(**צל**). דולקת: `evaluate` מחזיר מה לבצע -- ``CONTINUE`` (המגירה מוחזקת ומשלימה ברקע) ו-``DROP`` -- והמנוע
(`ChunkEngine._execute`) מבצע רק כשיש מי שממתין. **``DROP`` מבוצע רק אחרי שתי הערכות רצופות שיוצאות ``DROP``** (היסטרזיס: החלטה
אינה מתהפכת בגלל מדידה רועשת אחת); ``CONTINUE`` מבוצע מיד, כי הוא שומר על מה שכבר רץ.

**הקריטריון** (המתאם, 15/11 -- שתי חזיות, ושתיהן ממדידה):

- ``T_continue`` -- הזמן עד שההשלמה של הדיסק הזה נגמרת, בקצב ההשלמה שנמדד בו: **מה שחסר לו במחיצה, ועוד המחיצות שאחריה
  בגל**. מגירה שהשאר עברו הלאה בלעדיה לא תקבל את הזרם שלהן (הסוכן מטפל במחיצה אחת בכל פעם), והיא משלימה אותן ב-HTTP
  באותו קצב (`agent/lib/restore/drawerback.sh`). בלי זה, מגירה שחסרות לה 100 חתיכות אחרונות במחיצה הראשונה נראית "כמעט
  גמורה" ופורשת לעשרות GB. הערך של המחיצה הנוכחית לבדה נרשם בראיות (``t_continue_this_s``) והוא מה שנמדד מול בפועל.
- ``T_redo`` -- הזמן לכתוב את הדיסק מחדש בגל הבא: כל האימג' בקצב הגל (`waveeta.predict`, ``T1``), ועוד **תקורה לכל מחיצה**.
  ל-`waveeta` אין מודל של תקורה, ולכן היא נמדדת כאן: מה שעובר מסוף מחיצה עד שהשולח מתחיל את הבאה (`ChunkEngine`, 
  `part_gap`) -- חציון הגל הזה, ואם אין בו עדיין מחיצה שנייה, חציון הגלים הקודמים (``PART_OVERHEAD``, נרשם בסוף כל גל).
- ``CONTINUE`` אם ``T_continue <= T_redo``, אחרת ``DROP`` ("ההשלמה איטית מכתיבה מחדש -- הדיסק ייכתב בגל הבא").

**מה שלא נמדד הוא "לא נמדד", לא אפס ולא תקין** (עיקרון 5): קצב שאי אפשר לקרוא (עוד לא הוסיפה דבר, או שאין שעון), מה שחסר
או גודל האימג' שאינם ידועים -- ``UNDECIDED``, ושום דבר לא משתנה. **תקורה שלא נמדדה** נרשמת ``overhead=unmeasured`` ונספרת
0, אבל היא רק מקטינה את ``T_redo``: ``CONTINUE`` שנכון גם בלי תקורה נשאר ``CONTINUE``, ו-``DROP`` **אינו נגזר** מתקורה
שלא נמדדה -- כשההבדל תלוי בה ההחלטה ``UNDECIDED``.

**הרישום** (`brain_decisions`, כלל ``K3``, ישות = הדיסק): שורה ``DRAWER_HOLD`` **רק כשההחלטה משתנה**, כמו שאר הכללים,
עם כל המספרים בראיות; כשההשלמה נגמרת, או הגל, השורה נסגרת עם מה שקרה בפועל. ההערכה חוזרת בכל דיווח `fetching`.
המתג של הרישום הוא ``brain_veto_mode``: ``off`` = לא מחושב ולא נרשם. **לעולם אינו זורק** אל הגל.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from server.brain import memory, waveeta
from server.db import get_setting

log = logging.getLogger("imagesrv.brain")

RULE, ACTION, OVERHEAD = "K3", "DRAWER_HOLD", "PART_OVERHEAD"
SETTING = "brain_drawer_hold"
OFF, SLOW, ON = "off", "slow", "on"
MODES = (OFF, SLOW, ON)
#: #3605 (Nadav 17/11): the reason of a drive held because the brain marked it slow -- it is NOT failed.
SLOW_HE = "הדיסק סומן איטי: ההשלמה שלו ממשיכה ברקע והוא אינו נכשל, והמגירות האחרות עוברות למחיצה הבאה"
LABEL_HE = "המוח: השלמה מעכבת רק את המגירה שנפגעה"
CONTINUE, DROP, UNDECIDED = "CONTINUE", "DROP", "UNDECIDED"
#: כמה הערכות רצופות של `DROP` נדרשות לפני שהמנוע מכשיל את הדיסק (הכרעת המתאם, 15/11).
DROP_CONFIRMS = 2
DROP_HE = "ההשלמה איטית מכתיבה מחדש — הדיסק ייכתב בגל הבא"
#: כמה גלים אחרונים נכנסים לחציון של התקורה (`PART_OVERHEAD`).
HISTORY = 10
#: כמה שורות K3 אחרונות נקראות כדי למצוא בהן את `PART_OVERHEAD` (שורה אחת לגל, בין עוד שתיים-שלוש של K3).
SCAN = 200


# --- ההגדרה ------------------------------------------------------------------------------------------------------


def parse_mode(raw: object) -> str | None:
    """off / slow / on בדיוק -- או ``None`` (הקונסולה דוחה ב-400)."""
    return raw if isinstance(raw, str) and raw in MODES else None


def mode(conn) -> str:
    """ההגדרה ``brain_drawer_hold``. **חסר = ``slow``** (#3605, הכרעת נדב 17/11: מבצעים רק דיסק שהמוח סימן איטי); ערך לא מוכר,
    או שלא נקרא = ``off``: כשל בקריאה אינו סיבה לבצע."""
    try:
        raw = get_setting(conn, SETTING)
    except Exception:                           # noqa: BLE001 -- המוח אינו מפיל גל
        log.exception("brain: %s unreadable, using %s", SETTING, OFF)
        return OFF
    if raw is None:
        return SLOW
    return raw if raw in MODES else OFF


# --- ההחלטה: פונקציה טהורה ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    verdict: str
    reason: str
    evidence: tuple[str, ...]
    t_continue_s: float | None = None
    t_this_s: float | None = None               # רק המחיצה הנוכחית: מה שנמדד מול בפועל כשההשלמה נגמרת
    t_redo_s: float | None = None
    rate_chunks_s: float | None = None          # #3895: הקצב העמיד של הדיסק הזה (המקור שממנו נגזר ה-ETA)
    rate_mbps: float | None = None
    below_floor: bool | None = None             # None = הקצב או הסף לא נמדדו: אין עילה להכשיל


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _positive(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _fmt(value: float | None) -> str:
    return "unmeasured" if value is None else f"{value:.0f}"


def judge(*, remaining: object, grown: object, received: object, elapsed_s: object, later_chunks: object,
          chunks_total: object, zbytes_total: object, bits: object, parts: object, overhead_s: float | None,
          overhead_n: int = 0, floor_mbps: object = None) -> Judgment:
    """(מה שחסר לדיסק, מה שגדל בו מאז שההורדה התחילה, מה ש-`apply` לקח מהזרם, שניות, חתיכות במחיצות שאחרי, האימג' כולו,
    קצב הגל, מחיצות, התקורה לכל מחיצה או None) -> ההחלטה. טהורה: אינה קוראת דבר ואינה כותבת דבר.

    קצב ההשלמה = מה שגדל ביומן של הדיסק הזה חלקי השניות (#3895). ``received`` הוא מונה המחשב, משותף לכל מגירותיו: ראיית
    עבודה שנרשמת, לא קצב של דיסק (לא ``max(grown, received)`` של #3189)."""
    left, later, took = _count(remaining), _count(later_chunks), [n for n in (_count(grown),) if n]
    done = max(took, default=0)
    secs = elapsed_s if isinstance(elapsed_s, (int, float)) and not isinstance(elapsed_s, bool) \
        and math.isfinite(elapsed_s) and elapsed_s > 0 else None
    chunks_i, zbytes_i, bits_i, parts_i = _positive(chunks_total), _positive(zbytes_total), _positive(bits), _positive(parts)
    redo = [v for v in (chunks_i, zbytes_i, bits_i, parts_i) if v is not None]
    why = [text for ok, text in (
        (left is not None, "מה שחסר לדיסק לא נקרא"),
        (later is not None, "מספר החתיכות של המחיצות שאחרי לא ידוע"),
        (done > 0 and secs is not None, "ההשלמה עוד לא הוסיפה דבר עמיד ליומן של הדיסק הזה: אין קצב של דיסק"
         " (מה שהמחשב קיבל מהזרם הוא ראיית עבודה משותפת, לא קצב; שתיקה נשפטת בכלל השתיקה, לא כאן)"),
        (len(redo) == 4, "גודל האימג' או קצב הגל לא ידועים")) if not ok]
    t_this: float | None = None
    t_cont: float | None = None
    t_redo: float | None = None
    rate: float | None = None
    rate_mbps: float | None = None
    below: bool | None = None
    if (not why and left is not None and later is not None and secs is not None and chunks_i is not None
            and zbytes_i is not None and bits_i is not None and parts_i is not None):    # `not why` כבר מבטיח הכול; התנאים רק מצמצמים טיפוס
        rate = done / secs
        rate_mbps = rate * waveeta.CHUNK_BYTES / 1e6
        floor = floor_mbps if isinstance(floor_mbps, (int, float)) and not isinstance(floor_mbps, bool) and floor_mbps > 0 else None
        below = None if floor is None else rate_mbps < floor
        t_this, t_cont = left / rate, (left + later) / rate
        t1 = waveeta.predict(chunks_i, zbytes_i, bits_i, []).eta_s
        t_redo = t1 + (parts_i * overhead_s if overhead_s is not None else 0.0)
    ev = (f"remaining_chunks={'unmeasured' if left is None else left}",
          f"later_chunks={'unmeasured' if later is None else later}", f"done_chunks={done if took else 'unmeasured'}",
          f"elapsed_s={_fmt(secs)}", f"rate_chunks_s={'unmeasured' if rate is None else f'{rate:.2f}'}", "rate_source=durable_grown",
          f"received_chunks={_count(received) if _count(received) is not None else 'unmeasured'}",
          f"rate_mbps={'unmeasured' if rate_mbps is None else f'{rate_mbps:.1f}'}",
          f"floor_mbps={'unmeasured' if floor_mbps is None or below is None else floor_mbps}",
          f"below_floor={'unmeasured' if below is None else 'yes' if below else 'no'}",
          f"t_continue_s={_fmt(t_cont)}", f"t_continue_this_s={_fmt(t_this)}", f"t_redo_s={_fmt(t_redo)}",
          *(("overhead=unmeasured", "t_redo_basis=lower_bound") if overhead_s is None else
            (f"overhead_s={overhead_s:.1f}", f"overhead_n={overhead_n}", "t_redo_basis=measured")),
          f"chunks={_fmt(chunks_i if len(redo) == 4 else None)}",
          f"zbytes={_fmt(zbytes_i if len(redo) == 4 else None)}",
          f"stream_mbps={_fmt(bits / 1e6 if isinstance(bits, int) and bits > 0 else None)}",
          f"parts={parts if isinstance(parts, int) and parts > 0 else 'unmeasured'}")
    if why or t_cont is None or t_redo is None:     # בלי `why` שני המספרים נקבעו; התנאים האחרונים רק מצמצמים טיפוס
        return Judgment(UNDECIDED, "לא נשפט: " + "; ".join(why), ev, t_cont, t_this, t_redo, rate, rate_mbps, below)
    if t_cont <= t_redo:
        return Judgment(CONTINUE, f"ההשלמה ממשיכה: {t_cont / 60:.0f} דק' עד שהדיסק שלם, מול {t_redo / 60:.0f} דק' לכתיבה מחדש"
                        + ("" if overhead_s is not None else " (לפחות: התקורה לכל מחיצה לא נמדדה, והיא רק מגדילה את הכתיבה מחדש)"),
                        ev, t_cont, t_this, t_redo, rate, rate_mbps, below)
    if overhead_s is None:
        return Judgment(UNDECIDED, f"לא נשפט: ההשלמה {t_cont / 60:.0f} דק' מול {t_redo / 60:.0f} דק' לכתיבה מחדש בלי תקורה,"
                        " וההבדל תלוי בתקורה לכל מחיצה שלא נמדדה -- DROP אינו נגזר ממספר שלא נמדד", ev, t_cont, t_this, t_redo, rate, rate_mbps, below)
    return Judgment(DROP, f"{DROP_HE}: {t_cont / 60:.0f} דק' עד שהדיסק שלם מול {t_redo / 60:.0f} דק' לכתיבה מחדש", ev,
                    t_cont, t_this, t_redo, rate, rate_mbps, below)


# --- המצב בשרת אחד -----------------------------------------------------------------------------------------------


class DrawerHold:
    """``hold`` = ההגדרה ``brain_drawer_hold`` (בדיקות מזריקות; ברירת המחדל קוראת את ה-DB). ההחלטה רושמת בלבד."""

    def __init__(self, conn, *, mode_fn: Callable[[], str] | None = None, hold: Callable[[], str] | None = None,
                 clock: Callable[[], float] = time.monotonic, floor: Callable[[], object] | None = None):
        self.conn, self._mode_fn, self._hold_fn, self._clock, self._floor_fn = conn, mode_fn, hold, clock, floor
        self._lock = threading.Lock()
        self._gaps: dict[str, list[float]] = {}
        #: (גל, מחיצה, מחשב, serial) -> {"verdict", "row", "elapsed" (בהחלטה), "t_this"}
        self._open: dict[tuple[str, int, str, str], dict] = {}
        #: אותו מפתח -> כמה הערכות `DROP` רצופות (מתאפס בכל תוצאה אחרת)
        self._drops: dict[tuple[str, int, str, str], int] = {}

    def _mode(self) -> str:
        if self._mode_fn is not None:
            return self._mode_fn()
        from server.brain import veto            # כמו `waveeta`: המתג מגיע מהווטו
        return veto.mode(self.conn)

    def _floor(self) -> int | None:
        """#3895: הסף שבהגדרות (MB/s, `writefloor.mbps`) -- מתחתיו בלבד דיסק איטי נכשל. לא נקרא = ``None``: אין עילה להכשיל."""
        try:
            if self._floor_fn is not None:
                got = self._floor_fn()
            else:
                from server.disks import writefloor      # noqa: PLC0415 -- כמו `veto`: נטען רק כשצריך
                got = writefloor.mbps(self.conn)
            return got if isinstance(got, int) and not isinstance(got, bool) and got > 0 else None
        except Exception:                       # noqa: BLE001 -- סף שלא נקרא אינו סיבה להכשיל
            log.exception("brain: the write floor was not read; no drive is failed on a rate")
            return None

    def _setting(self) -> str:
        return self._hold_fn() if self._hold_fn is not None else mode(self.conn)

    # -- התקורה לכל מחיצה --

    def gap(self, session_id: str, seconds: object) -> None:
        """`ChunkEngine`: כמה עבר מסוף מחיצה עד שהשולח התחיל את הבאה. לעולם אינו זורק."""
        try:
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0:
                with self._lock:
                    self._gaps.setdefault(session_id, []).append(float(seconds))
        except Exception:                       # noqa: BLE001
            log.exception("brain: a partition gap of %s was not kept", session_id)

    def _history(self) -> list[float]:
        """`overhead_s` של הגלים הקודמים (החדש ראשון), מהזיכרון של המוח."""
        found: list[float] = []
        for row in memory.decisions(self.conn, rule=RULE, limit=SCAN):
            if row["action"] != OVERHEAD or row["verdict"] != "MEASURED" or not isinstance(row.get("evidence"), list):
                continue
            for item in row["evidence"]:
                if isinstance(item, str) and item.startswith("overhead_s="):
                    try:
                        value = float(item.split("=", 1)[1])
                    except ValueError:
                        continue
                    if math.isfinite(value) and value >= 0:
                        found.append(value)
            if len(found) >= HISTORY:
                break
        return found[:HISTORY]

    def _overhead(self, session_id: str) -> tuple[float | None, int, str]:
        """(החציון, כמה מדידות, מאיפה) -- מה שנמדד בגל הזה, ואם עוד אין, בגלים הקודמים; אחרת ``None``: לא נמדד."""
        with self._lock:
            own = list(self._gaps.get(session_id, ()))
        if own:
            return statistics.median(own), len(own), "wave"
        try:
            past = self._history()
        except Exception:                       # noqa: BLE001 -- זיכרון שלא נקרא = "לא נמדד", לא אפס
            log.exception("brain: the partition overhead of past waves was not read")
            return None, 0, "unread"
        return (statistics.median(past), len(past), "history") if past else (None, 0, "none")

    # -- הכניסות: לעולם אינן זורקות --

    def evaluate(self, *, session_id: str, part: int, mac: str, drawers: Iterable[dict], received: object,
                 elapsed_s: object, later_chunks: object, chunks_total: object, zbytes_total: object, bits: object,
                 parts: object, ended: bool = False) -> dict[str, tuple[str, str]]:
        """`ChunkEngine.report`, בכל דיווח `fetching` (וב-`ended` של מי שהוריד): ``drawers`` = לכל דיסק שעוד חסר לו
        משהו ``{"serial", "remaining", "grown"}``. דיסק שאינו ברשימה שלם. ב-``ended`` כל ההחלטות הפתוחות של המחשב נסגרות.
        מחזיר מה לבצע, ``{serial: (CONTINUE | DROP, הסיבה)}`` -- רק כש-`brain_drawer_hold` = on, ו-``DROP`` רק אחרי
        `DROP_CONFIRMS` הערכות רצופות; ``UNDECIDED`` אינו בו. כבוי, ``ended`` או כשל: ``{}``."""
        todo: dict[str, tuple[str, str]] = {}
        try:
            if self._mode() == OFF:
                return todo
            drawers = list(drawers)
            lacking = {d.get("serial") for d in drawers if isinstance(d, dict) and d.get("remaining")}
            self._close(session_id, part, mac, lacking, elapsed_s, ended)
            if ended:
                return todo
            setting = self._setting()
            execute, slow_hold = setting == ON, setting == SLOW
            overhead, n, source = self._overhead(session_id)
            floor = self._floor()
            for d in drawers:
                if not isinstance(d, dict) or not isinstance(d.get("serial"), str) or not d.get("remaining"):
                    continue
                j = judge(remaining=d.get("remaining"), grown=d.get("grown"), received=received, elapsed_s=elapsed_s,
                          later_chunks=later_chunks, chunks_total=chunks_total, zbytes_total=zbytes_total, bits=bits,
                          parts=parts, overhead_s=overhead, overhead_n=n, floor_mbps=floor)
                self._note(session_id, part, mac, d["serial"], j, elapsed_s, source)
                drops = self._count_drops((session_id, part, mac, d["serial"]), j.verdict)
                if execute and j.verdict == DROP and drops >= DROP_CONFIRMS and j.below_floor is not True:
                    # #3895: the proposal (DROP: completing takes longer than a rewrite) is not a failure. A slow drive is failed only when its
                    # durable rate was MEASURED below the floor in the settings; above it, or unmeasured, it is held and completes (Nadav 16/11).
                    todo[d["serial"]] = (CONTINUE, f"{SLOW_HE}: {j.reason}")
                elif execute and (j.verdict == CONTINUE or (j.verdict == DROP and drops >= DROP_CONFIRMS)):
                    todo[d["serial"]] = (j.verdict, j.reason)
                elif slow_hold and j.verdict == DROP and drops >= DROP_CONFIRMS:
                    # #3605: the brain marked it slow (completing it takes longer than writing it again, twice in a row). It is not
                    # failed -- nobody is dropped (Nadav 16/11): it is HELD, and completes in the background. Every other verdict: as today.
                    todo[d["serial"]] = (CONTINUE, f"{SLOW_HE}: {j.reason}")
        except Exception:                       # noqa: BLE001 -- המוח אינו מפיל גל
            log.exception("brain: the drawer hold of %s part %s was not judged", session_id, part)
            return {}
        return todo

    def executed(self, session_id: str, part: int, mac: str, serial: str, kind: str) -> None:
        """`ChunkEngine._execute`: המנוע עשה מה שהוחלט -- ``kind`` = CONTINUE (המגירה הוחזקה) או DROP (הדיסק נכשל בשמו). ההחלטה
        נסגרת עם זה, ולא עם "הדיסק שלם". לעולם אינו זורק."""
        try:
            with self._lock:
                got = self._open.pop((session_id, part, mac, serial), None)
                self._drops.pop((session_id, part, mac, serial), None)
            if got is not None:
                memory.resolve(self.conn, [got["row"]], "בוצע: המגירה הוחזקה -- ההשלמה ממשיכה ברקע והשאר עברו למחיצה הבאה"
                               if kind == CONTINUE else "בוצע: הדיסק נכשל בשמו וייכתב בגל הבא")
        except Exception:                       # noqa: BLE001
            log.exception("brain: the execution of %s %s was not recorded", session_id, serial)

    def _count_drops(self, key: tuple, verdict: str) -> int:
        """כמה הערכות `DROP` רצופות עד עכשיו (כולל זו). כל תוצאה אחרת מאפסת."""
        with self._lock:
            if verdict != DROP:
                self._drops.pop(key, None)
                return 0
            self._drops[key] = self._drops.get(key, 0) + 1
            return self._drops[key]

    def on_event(self, event: str, detail: str, session_id: str) -> None:
        try:
            if event in ("send_done", "send_failed", "send_stopped"):
                self._finish(session_id, event)
        except Exception:                       # noqa: BLE001
            log.exception("brain: the drawer hold of %s failed on %s", session_id, event)

    # -- פנים --

    def _note(self, session_id: str, part: int, mac: str, serial: str, j: Judgment, elapsed_s: object, source: str) -> None:
        key = (session_id, part, mac, serial)
        with self._lock:
            prev = self._open.get(key)
        if prev is not None and prev["verdict"] == j.verdict:
            return                              # רק שינוי נרשם
        held = self._setting()
        if prev is not None:
            memory.resolve(self.conn, [prev["row"]], f"הוחלפה ב-{j.verdict} אחרי {self._since(prev, elapsed_s)}")
        say = ("מבוצע (brain_drawer_hold=on), רק אם יש מי שממתין" + (f"; DROP אחרי {DROP_CONFIRMS} הערכות רצופות: הדיסק נכשל רק אם הקצב העמיד"
                                                                       " שלו נמדד מתחת לסף שבהגדרות, אחרת הוא מוחזק ומשלים"
                                                                       if j.verdict == DROP else "")) if held == ON else (
            ("מבוצע רק לדיסק שסומן איטי (brain_drawer_hold=slow): DROP אחרי " f"{DROP_CONFIRMS} הערכות רצופות = ההשלמה ממשיכה ברקע, הדיסק אינו נכשל, רק אם יש מי שממתין"
             if j.verdict == DROP else "צל: דיסק שלא סומן איטי אינו משתנה") if held == SLOW else "צל: שום דבר לא משתנה")
        evidence = (f"drive={serial}", f"mac={mac}", f"part={part}", *j.evidence, f"overhead_from={source}",
                    f"proposal={j.verdict}", f"execution={held}", "execution_wired=yes" if held in (ON, SLOW) else "execution_wired=n/a")
        row = memory.record(self.conn, mode=self._mode(), rule=RULE, action=ACTION, verdict=j.verdict,
                            reason=f"{j.reason}. {say}", evidence=evidence, round_id=session_id,
                            entity=("disk", serial))
        with self._lock:
            self._open[key] = {"verdict": j.verdict, "row": row, "elapsed": elapsed_s, "t_this": j.t_this_s}
        log.info("brain %s", json.dumps({"rule": RULE, "action": ACTION, "round": session_id, "part": part, "drive": serial,
                                          "verdict": j.verdict, "reason": j.reason, "evidence": list(evidence),
                                          "say": say}, ensure_ascii=False))

    @staticmethod
    def _since(prev: dict, elapsed_s: object) -> str:
        then, now = prev.get("elapsed"), elapsed_s
        if (isinstance(then, (int, float)) and not isinstance(then, bool)
                and isinstance(now, (int, float)) and not isinstance(now, bool)):
            return f"{now - then:.0f}s"
        return "זמן שלא נמדד"

    def _close(self, session_id: str, part: int, mac: str, lacking: set, elapsed_s: object, ended: bool) -> None:
        """החלטות פתוחות של המחשב במחיצה שהדיסק שלהן כבר שלם (או, ב-``ended``, כולן): נסגרות עם מה שקרה."""
        with self._lock:
            shut = [(k, v) for k, v in self._open.items()
                    if k[:3] == (session_id, part, mac) and (ended or k[3] not in lacking)]
            for k, _v in shut:
                del self._open[k]
                self._drops.pop(k, None)
        for key, got in shut:
            whole = key[3] not in lacking
            took = self._since(got, elapsed_s)
            pred = got.get("t_this")
            text = (f"הדיסק {'שלם' if whole else 'עוד חסר לו משהו'} אחרי עוד {took} בהשלמה"
                    + ("" if pred is None else f" (חזוי בהחלטה: {pred:.0f}s למחיצה הזו)"))
            memory.resolve(self.conn, [got["row"]], text if whole else text + ": ההשלמה הסתיימה בלי שהדיסק שלם")

    def _finish(self, session_id: str, event: str) -> None:
        with self._lock:
            shut = [(k, self._open.pop(k)) for k in [k for k in self._open if k[0] == session_id]]
            for k in [k for k in self._drops if k[0] == session_id]:
                del self._drops[k]
            gaps = self._gaps.pop(session_id, [])
        for _key, got in shut:
            memory.resolve(self.conn, [got["row"]], f"הגל הסתיים ב-{event}: ההשלמה האחרונה לא נמדדה")
        if event != "send_done" or not gaps or self._mode() == OFF:
            return
        median = statistics.median(gaps)
        memory.record(self.conn, mode=self._mode(), rule=RULE, action=OVERHEAD, verdict="MEASURED",
                      reason=f"תקורה לכל מחיצה: חציון {median:.1f}s מ-{len(gaps)} מעברים בין מחיצות בגל",
                      evidence=(f"overhead_s={median:.1f}", f"samples={len(gaps)}", f"max_s={max(gaps):.1f}"),
                      round_id=session_id, entity=("round", session_id), outcome="נמדד")
