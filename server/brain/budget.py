"""#3064, שלב 3 (K7): תקציב רשת אוטומטי להגשת קובצי האימג' ב-HTTP.

**הכרעת נדב, 14/11: "אין אחוזים קבועים, הכול אוטומטי."** רשת ההפצה בוילן משלה. המוח מודד ומתאים את עצמו,
ובהגדרות יש רק **גבול עליון**: ``brain_net_ceiling_mbps``.

**המודל.**

- ``גבול עליון`` -- ההגדרה. ``0`` או חסר = נמדד מהכרטיס (90% ממהירות הקישור, כמו תקרת הגל ב-`drive_rates`), ואם
  גם הקישור לא נקרא (כרטיס וירטואלי, ווינדוס) -- 900 Mbps, אותה תקרה שהגל מקבל בלי `IMAGESRV_MAX_BITRATE`.
- ``השידור קודם.`` HTTP מקבל רק **מה שנשאר מהגבול אחרי השידור בפועל**. השידור בפועל (#3596) = **מה שהכרטיס נשא ואינו הגשת
  הקבצים שלנו** (מונה V9 פחות הבייטים שהוגשו, בין שני מחזורים), כשהוא נמדד -- גם כשהוא פחות ממה שהמנוע ביקש מהזרם
  (`ChunkEngine.sending_bps`, או `max_bitrate` של הגל ב-udpcast; 17/11, J1: ביקשנו 892Mbps והחוט נשא 412, והתקציב ישב על הרצפה
  כל הגל). **הקצב המבוקש** הוא רק המקור במחזור הראשון (אין הפרש), או כשהכרטיס לא נקרא או נשא אפס; הוא נרשם בראיות (``asked``).
  אין שידור = כל הגבול.
- ``AIMD בתוך השארית.`` אובדן במשכפלים (`RcvbufErrors`, ו-#3064 שלב 4: חתיכות שהמקלט נטש באמצע, G7)
  או זריקות בכרטיס של השרת = **מחצית**. שקט שנמדד ברשת = **צעד אחד** למעלה. אין אות רשת בכלל = לא זז, וביומן "אין מדידה", לא "תקין".
- ``כרטיס המקלטים (#3597).`` `rx_missed` ו-`rx_no_buffer` של הכרטיס אצל המקלטים (S50) -- אותם מונים ש-B59 ו-K8 סופרים -- נרשמים
  בראיות בשמם (`RcvbufErrors` של הקרנל נרשם `rcvbuf`, לא `rx_dropped`). **אינם מקצצים**: בלי ספי כיול (21-885 בגל שלם) החיתוך
  היה רעש. ה"שקט" אינו אומר עליהם "נקי" כשזרקו או כשלא נקראו: הסיבה אומרת את זה במפורש.
- ``הדיסק אינו הרשת (#3463).`` לחץ I/O על דיסק הספרייה (`io_full`) **אינו ראיה על הרשת**: אינו מקצץ ואינו נספר "שקט".
  ברזל 15/11: התקציב ירד לרצפה 8Mbps בגלל `io_full` 0.2 בשלושה סבבים, בלי אבדן ברשת. `io_full` נשאר בראיות ובחוש (K5 קורא
  אותו). התגובה ללחץ דיסק היא של תקרת השרת, לא של תקציב הרשת. `Config.disk_cuts_budget=True` משחזר את ההתנהגות הישנה.
- ``רצפה.`` לא יורדים מתחת ל-`FLOOR_BPS` (8 Mbps, 1MB/s): הורדה שאינה מקבלת בייט אינה "ממתינה בתור", היא נכשלת
  בסוכן (D14), והשרת לא מאשים משכפל על ההמתנה שלו (I9). הרצפה לעולם לא מעל הגבול. **אובדן על תקציב שכבר ברצפה אינו חיתוך** (#3596):
  הפסק הוא ``AT_FLOOR`` ("על הרצפה, אין מה לחתוך") עם אותה ראיה, ולא ``CUT`` "הופחת בחצי" שאינו משנה דבר.
- ``הגינות.`` דלי אסימונים אחד לכל השרת (GCRA), ו-**round-robin בין משכפלים** (SFQ, `Fair`): כל גוף של 64KB ממתין
  בתור, והתור נפתח לפי הסדר של "כמה כל משכפל כבר קיבל", לא לפי מי הגיע ראשון. משכפל עם שלושה חיבורים מקבל כמו
  משכפל עם חיבור אחד -- החלוקה **לפי משכפל**, לא לפי חיבור, ומשכפל שאינו מנצל את חלקו משאיר אותו לאחרים.

**מצבים** (אותו מתג של הווטו, ``brain_veto_mode``): ``off`` -- דבר לא מחושב. ``shadow`` (ברירת המחדל) --
התקציב **מחושב ונרשם, ולא נאכף**: שום הורדה לא מושהית. ``enforce`` -- נאכף. כל החלטה נרשמת בלוגר `imagesrv.brain`
עם הסיבה והראיות (`"brain {...}"`, כלל K7).

**הזיכרון (#3185):** כל החלטה שמשנה כיוון (אין שידור / קיצוץ / כיוונון בתוך השארית), וכל קיצוץ, נרשמת ב-
``brain_decisions`` כהחלטה כללית, עם התוצאה שלה: מה החליף אותה ואחרי כמה זמן (`server/brain/memory.py`).

**מה שאין כאן, בכוונה:** שידור חוזר ממוקד, ובחירה בין multicast ל-unicast לפי מספר המשכפלים. התקציב אינו מחליט
*מה* להוריד, רק *כמה מהר* -- וה-poll נשאר אצל הסוכן.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from server.brain import memory, netsense
from server.db import get_setting
from server.rounds import drive_rates

log = logging.getLogger("imagesrv.brain")

SETTING = "brain_net_ceiling_mbps"
AUTO = 0
#: 0 = אוטומטי; אחרת בטווח הזה. 10 Mbps הוא כרטיס Fast Ethernet בקושי; 100,000 = 100GbE.
CEILING_RANGE = (10, 100_000)
#: כשהגבול לא הוגדר והקישור לא נקרא: אותה תקרה שהגל מקבל בלי `IMAGESRV_MAX_BITRATE` (`drive_rates.TOP_BITRATE`).
DEFAULT_CEILING_MBPS = drive_rates.TOP_BITRATE // 1_000_000
LABEL_HE = "המוח: תקרת רשת ההפצה (Mbps, 0 = נמדד מהכרטיס)"

#: מחזור ההחלטה (A1 בטיוטת התכנון: לולאה אחת, כל 10 שניות). הערכה, לא מדידה.
TICK_S = 10.0
#: הרצפה והצעד של ה-AIMD: 1MB/s. מספיק להורדה שמתקדמת, וזניח מול השידור (0.8% מ-1GbE).
FLOOR_BPS = 8_000_000
STEP_BPS = 8_000_000
CUT = 0.5
#: לחץ I/O מלא על דיסק הספרייה שמעליו "השרת הוא הצוואר" (X12, B19). הערכה: 0.18 נמדד בגל udpcast על vmdk thin,
#: `server_io` שופט גל שלם לפי ההתקן של הספרייה (#3365: `SERVER_DISK_UTIL` ו-`SERVER_DISK_AWAIT_MS`), לא לפי PSI --
#: כאן הסף של ה-PSI לחלון של 10 שניות.
PSI_FULL_MAX = 0.2
#: כמה זמן של תקציב מותר "לצבור" אחרי שקט, כדי שהורדה קצרה לא תמתין על כל חתיכה.
BURST_S = 0.25
#: תקרת ההמתנה לחתיכה אחת. הרצפה כבר מבטיחה קצב חיובי; זו רשת ביטחון שהמתנה לא תהיה אינסופית אם תהיה בעיית חישוב.
MAX_WAIT_S = 5.0

#: KT7 (הערכה מטיוטת התכנון): הורדות HTTP במקביל -- נקודת התחלה של 2 בכל השרת, ו-1 לכל משכפל. X6: התקרה של כל השרת
#: זזה בתוך `CONC_RANGE` לפי אותו AIMD של התקציב (קיצוץ = חצי, שקט שנמדד = +1). **B10: נרשם בלבד** -- שום הורדה אינה
#: ממתינה בגלל התקרה, גם ב-``enforce``: מעבר ל-2 צריך את M14 (6 משכפלים מורידים יחד), ועד אז זה מה שהמוח לומד ממנו.
CONC_START, CONC_PER_CLONER = 2, 1
CONC_RANGE = (1, 12)

OFF, SHADOW, ENFORCE = "off", "shadow", "enforce"
OPEN, CUT_, RAISE, HOLD, CLAMP = "OPEN", "CUT", "RAISE", "HOLD", "CLAMP"
#: #3838 (ג), הכרעת המתאם: K7 מתקצב את כרטיס **השרת**. בהפצה ישירה השולח הוא מחשב הבנייה והזרם לא עובר בכרטיס הזה, ולכן K7 אינו
#: מחליט (לא CLAMP, לא קצב) ורושם "לא רלוונטי". תקצוב הכרטיס של השולח הוא כלל נפרד, אחרי שיהיה נתון מדוד ממנו.
NOT_APPLICABLE = "NOT_APPLICABLE"
DIRECT_OUTCOME = "n/a: direct — השולח הוא מחשב הבנייה"
#: #3596: אובדן שנמדד כשהתקציב כבר על הרצפה: אין מה לחתוך. אינו `CUT`: הוא לא הפחית דבר.
AT_FLOOR = "AT_FLOOR"


# --- ההגדרה ------------------------------------------------------------------------------------------------------


def parse_ceiling(raw: object) -> int | None:
    """``0`` (אוטומטי) או שלם בטווח `CEILING_RANGE` -- אחרת ``None`` (הקונסולה דוחה ב-400). `bool` אינו מספר,
    ואפס מוביל וטקסט עשרוני נדחים, כמו `veto.parse_seconds`."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() and (raw == "0" or not raw.startswith("0")) \
            and len(raw) <= 6:
        value = int(raw)
    else:
        return None
    return value if value == AUTO or CEILING_RANGE[0] <= value <= CEILING_RANGE[1] else None


def stored(conn) -> int:
    """ההגדרה שנשמרה, או ``AUTO`` כשחסרה, פגומה, או שלא נקראה: ערך שהמוח אינו יכול להשתמש בו הוא "אוטומטי"."""
    try:
        got = parse_ceiling(get_setting(conn, SETTING))
    except Exception:                           # noqa: BLE001
        log.exception("brain: %s unreadable, measuring from the card", SETTING)
        return AUTO
    return AUTO if got is None else got


def effective(conn) -> str:
    """הערך שבתוקף, לקונסולה: ``"0"`` = נמדד מהכרטיס -- לא "לא נקבע"."""
    return str(stored(conn))


def ceiling_bps(conn, link: dict | None) -> tuple[int, str]:
    """(הגבול העליון בביטים לשנייה, ומאיפה הוא): `הגדרה` / `הכרטיס` / `ברירת מחדל`."""
    value = stored(conn)
    if value:
        return value * 1_000_000, "הגדרה"
    speed = (link or {}).get("speed_mbps")
    if isinstance(speed, int) and not isinstance(speed, bool) and speed > 0:
        return speed * drive_rates.LINK_CEILING_PER_MBPS, "הכרטיס"
    return DEFAULT_CEILING_MBPS * 1_000_000, "ברירת מחדל"


# --- המדידה והחישוב (טהורים) -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    ceiling_bps: int
    floor_bps: int = FLOOR_BPS
    step_bps: int = STEP_BPS
    cut: float = CUT
    psi_full_max: float = PSI_FULL_MAX
    #: #3463: ``False`` = לחץ על דיסק השרת אינו מקצץ ואינו נספר שקט (רק ראיות רשת). ``True`` = ההתנהגות שלפני #3463.
    disk_cuts_budget: bool = False

    @property
    def floor(self) -> int:
        return min(self.floor_bps, self.ceiling_bps)


@dataclass(frozen=True)
class Reading:
    """מונים מצטברים ברגע אחד. ``None`` = לא נקרא (לעולם לא אפס)."""
    at: float                       # שעון מונוטוני
    served_bytes: int               # `Served.total_all()`
    broadcast_bps: int | None       # הקצב שביקשנו מהזרם עכשיו: 0 = אין זרם, None = לא הצלחנו לשאול
    nic_tx_bytes: int | None = None
    nic_tx_dropped: int | None = None
    rx_dropped: int | None = None   # `RcvbufErrors` (הקרנל, `rcvbuf` בראיות) המצטבר של מקלטי הגל (S44)
    psi_full_us: int | None = None
    rx_abandoned: int | None = None  # חתיכות שהמקלטים נטשו באמצע, מצטבר (S41, G7)
    #: #3597: כרטיס הרשת של **המקלטים** (S50, `wave_senses.nic`): אותם מונים ש-B59 ו-K8 סופרים כאובדן. מצטבר בגל.
    rx_missed: int | None = None
    rx_no_buffer: int | None = None
    #: #3838 (ג): הגל שמשדר עכשיו הוא הפצה ישירה (`direct.is_direct_wave`): השולח הוא מחשב הבנייה ולא כרטיס השרת
    direct: bool = False


@dataclass(frozen=True)
class Sample:
    """מה שנמדד בין שתי קריאות."""
    broadcast_bps: int | None       # None = לא ידוע (לא "אין שידור")
    served_bps: float
    rx_dropped: int | None          # הפרש; None = לא נמדד
    tx_dropped: int | None
    io_full: float | None           # שבר מהזמן שבו כל המשימות המתינו ל-I/O
    notes: tuple[str, ...] = ()
    abandoned: int | None = None    # הפרש; None = לא נמדד
    rx_missed: int | None = None    # #3597: הפרש של כרטיס המקלטים; None = לא נמדד. נרשם בראיות, אינו מקצץ (אין ספי כיול)
    rx_no_buffer: int | None = None
    direct: bool = False            # #3838 (ג): ראה `Reading.direct`


def _mbps(bps: float) -> str:
    return f"{bps / 1e6:.0f}Mbps"


def _after_cut(before: Sample, after: Sample | None) -> str:
    """#3596: מה שה-CUT כיוון אליו, לפני ואחרי: מוני האובדן של המחזור שחתך מול המחזור שאחריו. **לא נמדד** כשאחד מהם ``None`` (או שאין
    מחזור אחרי: המוח כובה) -- לא "ירד" ולא "לא ירד". אין כאן טענה שהחיתוך גרם לשינוי: ההורדות ב-HTTP אינן בהכרח מקור האובדן."""
    parts = []
    for name in ("rx_dropped", "abandoned", "tx_dropped"):
        was = getattr(before, name)
        now = getattr(after, name) if after is not None else None
        parts.append(f"{'rcvbuf' if name == 'rx_dropped' else name} {was} -> {now}" if was is not None and now is not None
                     else f"{'rcvbuf' if name == 'rx_dropped' else name} לא נמדד")
    return "אחרי החיתוך, המחזור הבא: " + ", ".join(parts)


def _delta(a: int | None, b: int | None) -> int | None:
    """הפרש בין שני מונים מצטברים. מונה שירד (אתחול, מכונה שיצאה מהסכום) הוא "לא נמדד", לא אפס ולא שלילי."""
    return b - a if isinstance(a, int) and isinstance(b, int) and b >= a else None


def derive(prev: Reading | None, cur: Reading) -> Sample:
    """שתי קריאות -> מה שנמדד. בלי קריאה קודמת (המחזור הראשון) רק הקצב המבוקש של השידור ידוע: ההפרשים ``None``."""
    notes: list[str] = []
    dt = cur.at - prev.at if prev is not None else 0.0
    if prev is None or dt <= 0:
        broadcast = cur.broadcast_bps
        if broadcast is not None and broadcast > 0:
            notes.append(f"broadcast {_mbps(broadcast)} (asked)")
        return Sample(broadcast, 0.0, None, None, None, tuple(notes), direct=cur.direct)
    served_bps = max(cur.served_bytes - prev.served_bytes, 0) * 8 / dt
    broadcast = cur.broadcast_bps
    if broadcast is not None and broadcast > 0:
        asked = broadcast
        sent = _delta(prev.nic_tx_bytes, cur.nic_tx_bytes)
        # #3596: מה שהכרטיס נשא ואינו הגשת הקבצים שלנו הוא השידור **בפועל**. אפס (או כרטיס שלא נקרא) אינו מדידה של "אין שידור" כשהמנוע
        # אומר שהוא שולח: אז המבוקש נשאר, והראיה אומרת שלא נמדד.
        measured = int(max(sent * 8 / dt - served_bps, 0.0)) if sent is not None else 0
        # #3838: מדידה מתחת לצעד אחד של התקציב (`STEP_BPS`) היא רעש של הכרטיס, לא שידור: התקציב כולו זז בצעדים כאלה, ושארית שקטנה מצעד
        # אינה משנה אותו. כמו אפס, היא משאירה את המבוקש, והראיה אומרת מה נמדד מול מה שהתבקש.
        if cur.direct:
            notes.append(f"broadcast {_mbps(asked)} (asked; sent by the build machine, not on the server's card)")
        elif measured >= STEP_BPS:
            broadcast = measured
            notes.append(f"broadcast {_mbps(measured)} (measured on the card)")
            notes.append(f"asked {_mbps(asked)}")
            if measured > asked:
                notes.append(f"card carries {_mbps(measured)} besides the files (more than asked)")
        elif measured > 0:
            notes.append(f"broadcast {_mbps(asked)} (asked; the card measured {_mbps(measured)}, below one step: noise)")
        else:
            notes.append(f"broadcast {_mbps(asked)} (asked; the card was not measured)")
    if served_bps:
        notes.append(f"served {_mbps(served_bps)}")
    psi = _delta(prev.psi_full_us, cur.psi_full_us)
    return Sample(broadcast, served_bps, _delta(prev.rx_dropped, cur.rx_dropped),
                  _delta(prev.nic_tx_dropped, cur.nic_tx_dropped),
                  None if psi is None else min(psi / 1e6 / dt, 1.0), tuple(notes),
                  _delta(prev.rx_abandoned, cur.rx_abandoned),
                  _delta(prev.rx_missed, cur.rx_missed), _delta(prev.rx_no_buffer, cur.rx_no_buffer), cur.direct)


@dataclass(frozen=True)
class Decision:
    rate_bps: int
    action: str
    reason: str
    limit_bps: int                  # מה שנשאר מהגבול אחרי השידור: התקציב לעולם לא עובר אותו (מעל הרצפה)
    broadcast_bps: int | None
    evidence: tuple[str, ...] = ()


def _card_say(s: Sample) -> str:
    """#3597: מה שה"שקט" אינו אומר על כרטיס המקלטים (B59, K8). לא מקצץ -- בלי ספי כיול 21-885 בגל שלם הם רעש -- אבל
    אינו נקרא "נקי" כשהוא זרק, ואינו נקרא "נקי" כשלא נקרא."""
    card = (("rx_missed", s.rx_missed), ("rx_no_buffer", s.rx_no_buffer))
    dropped = [f"{name}={got}" for name, got in card if got]
    unread = [name for name, got in card if got is None]
    out = ""
    if dropped:
        out += f"; כרטיס המקלטים זרק ({', '.join(dropped)}): נרשם, אינו מקצץ"
    if unread:
        out += f"; כרטיס המקלטים לא נקרא ({', '.join(unread)})"
    return out


def step(rate_bps: float | None, cfg: Config, s: Sample) -> Decision:
    """מחזור אחד של ה-AIMD: (התקציב הקודם, הגבול, מה שנמדד) -> ההחלטה. פונקציה טהורה.

    ``rate_bps=None`` = אין תקציב קודם (עלייה ראשונה, או שהמוח היה כבוי): מתחילים מהשארית."""
    floor = cfg.floor
    evidence = list(s.notes)
    if s.direct:
        # #3838 (ג): הזרם אינו בכרטיס השרת: אין CLAMP ואין שינוי קצב. כל הגבול פנוי לכרטיס הזה, והתקציב שבתוקף נשאר כמות שהוא
        kept = cfg.ceiling_bps if rate_bps is None else min(int(rate_bps), cfg.ceiling_bps)
        return Decision(kept, NOT_APPLICABLE, "הפצה ישירה: השולח הוא מחשב הבנייה והזרם אינו בכרטיס השרת, K7 אינו מחליט",
                        cfg.ceiling_bps, 0, (*evidence, "direct=1"))
    if s.broadcast_bps is None:
        limit, tail = floor, "קצב השידור לא נקרא, ולכן אין מה להניח שנשאר משהו: רצפה בלבד"
    else:
        limit = max(cfg.ceiling_bps - s.broadcast_bps, floor)
        tail = "" if s.broadcast_bps < cfg.ceiling_bps - floor else "השידור תופס את הגבול: רצפה בלבד"
    if s.broadcast_bps == 0:
        return Decision(limit, OPEN, "אין שידור: כל הגבול העליון להורדות", limit, 0, tuple(evidence))
    prev = limit if rate_bps is None else rate_bps
    cuts = []
    if s.rx_dropped:
        cuts.append(f"המקלטים זרקו {s.rx_dropped} חבילות")
    if s.abandoned:
        cuts.append(f"המקלטים נטשו {s.abandoned} חתיכות באמצע")
    if s.tx_dropped:
        cuts.append(f"כרטיס השרת זרק {s.tx_dropped} חבילות")
    disk_busy = s.io_full is not None and s.io_full > cfg.psi_full_max
    if disk_busy and cfg.disk_cuts_budget:
        cuts.append(f"דיסק הספרייה של השרת לחוץ ({s.io_full:.2f} מהזמן)")
    # #3463: הדיסק נספר שקט רק כשהוא עצמו מקצץ; אחרת "שקט" הוא ראיה מהרשת בלבד
    # #3597: "נמדדו נקיים" נאמר על מקור בשמו (`rcvbuf` = RcvbufErrors של הקרנל במקלטים, לא "המקלטים" כולל)
    measured = [name for name, got in (("rcvbuf", s.rx_dropped), ("נטישות", s.abandoned), ("tx_dropped של השרת", s.tx_dropped))
                if got is not None]
    if cfg.disk_cuts_budget and s.io_full is not None:
        measured.append("דיסק")
    for label, got in (("rcvbuf", s.rx_dropped), ("abandoned", s.abandoned), ("tx_dropped", s.tx_dropped),
                       ("io_full", s.io_full), ("rx_missed", s.rx_missed), ("rx_no_buffer", s.rx_no_buffer)):
        evidence.append(f"{label}={'unmeasured' if got is None else got}")
    if disk_busy and not cfg.disk_cuts_budget:
        evidence.append("io_full: דיסק לחוץ, אינו ראיה על הרשת ולא נספר בתקציב (#3463)")
    if cuts:
        new, action, why = max(floor, prev * cfg.cut), CUT_, f"{'; '.join(cuts)}: התקציב הופחת בחצי"
        if new >= prev:
            # #3596: התקציב כבר על הרצפה. "הופחת בחצי" היה נכתב כמות שהוא (7 פעמים ברצף, 17/11) בלי שדבר ירד: האובדן נרשם, החיתוך לא
            action, why = AT_FLOOR, f"{'; '.join(cuts)}: התקציב כבר על הרצפה ({_mbps(floor)}), אין מה לחתוך"
    elif measured:
        new, action, why = prev + cfg.step_bps, RAISE, f"שקט ({', '.join(measured)} נמדדו נקיים){_card_say(s)}: התקציב עולה בצעד אחד"
    else:
        new, action, why = prev, HOLD, "אין מדידה של אובדן או של עומס (לא 'תקין'): התקציב לא זז"
    capped = min(new, limit)
    if capped < new and action == RAISE:
        action, why = HOLD, "התקציב בגבול של מה שנשאר אחרי השידור"
    # #3838: CLAMP = התקציב ירד בפועל בצעד אחד לפחות; ירידה קטנה מזה (900 -> 899.8) אינה "התקציב ירד"
    if prev - capped >= cfg.step_bps and action != CUT_:
        action, why = CLAMP, "השידור תופס יותר מקודם: התקציב ירד למה שנשאר אחריו"
    if tail:
        why = f"{why} ({tail})"
    return Decision(int(capped), action, why, limit, s.broadcast_bps, tuple(evidence))


# --- האכיפה והלוג -------------------------------------------------------------------------------------------------


class Fair:
    """התור של ההגשה: דלי אסימונים אחד (GCRA) ו-round-robin בין משכפלים (SFQ). לוגיקה טהורה: הזמן מגיע כארגומנט,
    ואין כאן asyncio -- כך אפשר לבדוק גם הגינות וגם קצב בלי שעון אמיתי.

    כל בקשה נושאת **תג התחלה** `S = max(v, F[ip])`, ו-`F[ip] = S + nbytes`: `F` הוא "כמה המשכפל הזה כבר קיבל" ו-`v`
    הוא התג של מה שמוגש עכשיו. התור נפתח לפי `S` הקטן ביותר, ולכן שלושה חיבורים של אותו משכפל חולקים תג אחד, ומשכפל
    שלא ביקש זמן מה אינו צובר קרדיט (`F` שלו נשאר מאחורי `v`). הקצב: בקשה מוגשת כשהקו פנוי (`next_free`), והיא תופסת
    אותו `nbytes / bytes_per_s` שניות; אחרי שקט מותר פרץ של `burst_s`."""

    def __init__(self, burst_s: float = BURST_S):
        self.burst_s = burst_s
        self.v = 0.0
        self.finish: dict[str, float] = {}
        self.next_free = 0.0
        self._heap: list = []
        self._seq = 0

    def __len__(self) -> int:
        return len(self._heap)

    def add(self, ip: str, nbytes: int, token) -> None:
        start = max(self.v, self.finish.get(ip, 0.0))
        self.finish[ip] = start + nbytes
        self._seq += 1
        heapq.heappush(self._heap, (start, self._seq, ip, nbytes, token))

    def grant(self, now: float, bytes_per_s: float, alive: Callable = lambda token: True, unlimited: bool = False) -> list:
        """הבקשות שמוגשות עכשיו, לפי הסדר. ``unlimited`` (כבוי, צל, או אין עוד תקציב): כולן, בלי לתפוס את הקו.
        בקשה שמי ששלח אותה כבר לא מחכה לה (`alive` שקר: הפונה נעלם) נשכחת, ואינה תופסת את הקו."""
        out: list = []
        while self._heap:
            start, _seq, _ip, nbytes, token = self._heap[0]
            if not alive(token):
                heapq.heappop(self._heap)
                continue
            if not unlimited:
                self.next_free = max(self.next_free, now - self.burst_s)
                if self.next_free > now:
                    break
            heapq.heappop(self._heap)
            self.v = start
            if not unlimited:
                self.next_free += nbytes / bytes_per_s
            out.append(token)
        if not self._heap:
            self.finish = {ip: f for ip, f in self.finish.items() if f > self.v}     # מי שהשלים את חלקו נשכח
        return out

    def next_due(self, now: float) -> float | None:
        """בעוד כמה שניות הקו פנוי לבקשה הבאה, או ``None`` כשאין תור."""
        return max(self.next_free - now, 0.0) if self._heap else None


class Conc:
    """ההורדות שבדרך, לכל כתובת (B10, X6, KT7). לוגיקה טהורה ובלי I/O: `enter`/`leave` מלולאת האירועים, `drain` מהמחזור.
    הורדה שנכנסת כשכבר יש `cap` בדרך, או כשלאותו משכפל כבר יש `CONC_PER_CLONER`, נספרת "מעל התקרה"."""

    def __init__(self, cap: int = CONC_START):
        self.cap = cap
        self.active: dict[str, int] = {}
        self._reset()

    def _reset(self) -> None:
        self.over, self.who = 0, set()
        self.peak = sum(self.active.values())
        self.peak_one = max(self.active.values(), default=0)

    def enter(self, ip: str) -> None:
        mine = self.active[ip] = self.active.get(ip, 0) + 1
        total = sum(self.active.values())
        self.peak, self.peak_one = max(self.peak, total), max(self.peak_one, mine)
        if total > self.cap or mine > CONC_PER_CLONER:
            self.over += 1
            self.who.add(ip)

    def leave(self, ip: str) -> None:
        left = self.active.get(ip, 0) - 1
        if left > 0:
            self.active[ip] = left
        else:
            self.active.pop(ip, None)

    def drain(self) -> dict:
        """מה שקרה מאז המחזור הקודם, והתחלה חדשה: ``over`` הורדות מעל התקרה, ``peak`` במקביל, ``peak_one`` מאותו משכפל."""
        got = {"over": self.over, "who": sorted(self.who), "peak": self.peak, "peak_one": self.peak_one, "cap": self.cap}
        self._reset()
        return got

    def step(self, action: str) -> tuple[int, int]:
        """(התקרה הקודמת, החדשה) אחרי החלטת התקציב: ``CUT`` = חצי, ``RAISE`` = +1, אחרת לא זזה. תמיד בתוך `CONC_RANGE`."""
        before = self.cap
        if action in (CUT_, AT_FLOOR):          # #3596: אובדן על הרצפה עדיין מקצץ את תקרת ההורדות, כמו לפני
            self.cap = max(CONC_RANGE[0], self.cap // 2)
        elif action == RAISE:
            self.cap = min(CONC_RANGE[1], self.cap + 1)
        return before, self.cap


class Budget:
    """התקציב של שרת אחד: `tick` מחשב (מחוץ ללולאת האירועים), `acquire` נאכף (בתוכה, בלי I/O).

    ``mode()``, ``ceiling()`` ו-``read()`` מוזרקים: בייצור -- `production`, בבדיקות -- מזויפים."""

    def __init__(self, *, mode: Callable[[], str], ceiling: Callable[[], tuple[int, str]],
                 read: Callable[[], Reading], clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, remember: Callable[..., int | None] | None = None,
                 resolve: Callable[[int, str], object] | None = None):
        self._mode_fn, self._ceiling_fn, self._read_fn = mode, ceiling, read
        self._remember_fn, self._resolve_fn = remember, resolve    # #3185: `memory.record` / `memory.resolve`
        self._mem: tuple[str, int | None, float, Sample | None] | None = None   # (משפחה, שורה, מתי, מה נמדד בחיתוך) של ההחלטה שבתוקף
        self.conc = Conc()                                         # B10/X6/KT7: ההורדות שבדרך (נרשם בלבד)
        self._conc_mem: list | None = None                         # [שורה, מתי התחיל, כמה מעל, שיא, שיא למשכפל]
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._mode = SHADOW
        self._enforce = False
        self._rate_bps: float | None = None       # התקציב שבתוקף. None = עוד לא הוחלט (אין אכיפה)
        self._prev: Reading | None = None
        self._fair = Fair()
        self._handle = None                       # הטיימר שמעיר את התור (בלולאת האירועים בלבד)
        self._delayed, self._delay_s = 0, 0.0     # מה שהאכיפה השהתה מאז הרישום הקודם
        self._last_key: tuple | None = None
        self.last: Decision | None = None
        #: #3064 (K5, `route.py`): מה שנמדד במחזור האחרון -- שומר ההשלמה (B15, B19) קורא ממנו. None = עוד לא נמדד.
        self.sample: Sample | None = None

    # -- בצד הלולאה: בלי I/O --

    def enter(self, ip: str) -> None:
        """הורדה התחילה (`_CountedFile`). סופר בלבד: לעולם אינו ממתין ואינו זורק."""
        try:
            with self._lock:
                self.conc.enter(ip)
        except Exception:                       # noqa: BLE001
            log.exception("brain: the download count of %s failed", ip)

    def leave(self, ip: str) -> None:
        try:
            with self._lock:
                self.conc.leave(ip)
        except Exception:                       # noqa: BLE001
            log.exception("brain: the download count of %s failed", ip)

    async def acquire(self, ip: str, nbytes: int) -> None:
        """ממתין לתורו של `nbytes` ל-`ip`. חוזר מיד כשלא נאכף (כבוי, צל, או עוד לא הוחלט). לעולם אינו חוסם לנצח:
        אחרי `MAX_WAIT_S` ממשיכים (רשת ביטחון מתקלת חישוב, לא קצב). ביטול (הפונה נעלם) משחרר את מקומו בתור, ותקלה
        בתור עצמו אינה מפילה הורדה."""
        if not self._enforce or nbytes <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            began = self._clock()
            with self._lock:
                self._fair.add(ip, nbytes, waiter)
            self._pump(loop)
            try:
                await asyncio.wait_for(waiter, MAX_WAIT_S)
            except asyncio.TimeoutError:
                log.warning("brain: a download of %s waited %.0fs for its turn in the budget and went on", ip, MAX_WAIT_S)
            waited = self._clock() - began
            if waited > 0.001:
                with self._lock:
                    self._delayed += 1
                    self._delay_s += waited
        except Exception:                       # noqa: BLE001 — ביטול (CancelledError) אינו Exception ועובר הלאה
            log.exception("brain: the budget failed to place %s bytes for %s", nbytes, ip)

    def _pump(self, loop) -> None:
        """מגיש את מי שהקו פנוי לו, ומתזמן את ההער האחרון. רץ רק בלולאת האירועים. לעולם אינו זורק."""
        try:
            now = self._clock()
            with self._lock:
                rate = self._rate_bps
                free = not self._enforce or rate is None or rate <= 0
                granted = self._fair.grant(now, (rate or 1.0) / 8, alive=lambda w: not w.done(), unlimited=free)
                due = self._fair.next_due(now)
            for waiter in granted:
                if not waiter.done():
                    waiter.set_result(None)
            if self._handle is not None:
                self._handle.cancel()
                self._handle = None
            if due is not None:
                self._handle = loop.call_later(max(due, 0.001), self._pump, loop)
        except Exception:                       # noqa: BLE001 — תור שנפל אינו נתקע: ההמתנות נגמרות ב-MAX_WAIT_S
            log.exception("brain: the budget queue failed")

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def rate_bps(self) -> float | None:
        return self._rate_bps

    # -- בצד המחזור: קורא ורושם --

    def tick(self) -> Decision | None:
        """מחזור אחד: קורא את המתג, מודד, מחליט, רושם. לעולם אינו זורק (מוח שנפל הוא שורת יומן, לא הורדה שנתקעה)."""
        try:
            return self._tick()
        except Exception:                       # noqa: BLE001
            log.exception("brain: the network budget cycle failed")
            return None

    def _tick(self) -> Decision | None:
        mode = self._mode_fn()
        mode = mode if mode in (OFF, SHADOW, ENFORCE) else SHADOW
        with self._lock:
            self._mode, self._enforce = mode, mode == ENFORCE
            if mode == OFF:
                self._rate_bps, self._prev, self._last_key, self.sample = None, None, None, None
        if mode == OFF:
            self._close("המוח כובה")
            with self._lock:
                self.conc.drain()               # מה שנספר בזמן שהמוח כבוי אינו נרשם אחר כך
            mem, self._conc_mem = self._conc_mem, None
            if mem is not None and mem[0] is not None and self._resolve_fn is not None:
                self._resolve_fn(mem[0], "המוח כובה")
            return None
        ceiling, source = self._ceiling_fn()
        cfg = Config(ceiling_bps=ceiling)
        cur = self._read_fn()
        with self._lock:
            prev, rate = self._prev, self._rate_bps
        sample = derive(prev, cur)
        d = step(rate, cfg, sample)
        with self._lock:
            self._prev, self._rate_bps, self.last, self.sample = cur, float(d.rate_bps), d, sample
            delayed, delay_s, self._delayed, self._delay_s = self._delayed, self._delay_s, 0, 0.0
        self._record(d, mode, ceiling, source, delayed, delay_s)
        self._memorize(d, mode, ceiling)
        self._concurrency(d)
        return d

    def _concurrency(self, d: Decision) -> None:
        """B10/X6/KT7: התקרה זזה עם התקציב, ומה שעבר אותה נרשם -- שורה כשזה מתחיל, ותוצאה כשזה נגמר. צל תמיד."""
        with self._lock:
            self.conc.step(d.action)            # התקרה שבתוקף נרשמת בשורה של מה שעבר אותה (`cap=`), לא בשורה משלה
            got = self.conc.drain()
        mem = self._conc_mem
        if got["over"]:
            if mem is None:
                why = (f"{got['over']} הורדות התחילו מעל התקרה ({got['cap']} בכל השרת, {CONC_PER_CLONER} למשכפל): עד"
                       f" {got['peak']} במקביל, {got['peak_one']} מאותו משכפל. צל: אף הורדה לא המתינה")
                evidence = (f"over={got['over']}", f"cap={got['cap']}", f"per_cloner={CONC_PER_CLONER}",
                            f"peak={got['peak']}", f"peak_one={got['peak_one']}", *(f"ip={ip}" for ip in got["who"]))
                log.info("brain %s", json.dumps({"mode": SHADOW, "rule": "K7", "action": "HTTP_CONC",
                                                  "verdict": "WOULD_QUEUE", "reason": why, "evidence": list(evidence)},
                                                 ensure_ascii=False))
                row = self._remember_fn(mode=SHADOW, rule="K7", action="HTTP_CONC", verdict="WOULD_QUEUE",
                                        reason=why, evidence=evidence) if self._remember_fn is not None else None
                self._conc_mem = [row, self._clock(), got["over"], got["peak"], got["peak_one"]]
            else:
                mem[2] += got["over"]
                mem[3], mem[4] = max(mem[3], got["peak"]), max(mem[4], got["peak_one"])
            return
        if mem is not None:
            self._conc_mem = None
            if mem[0] is not None and self._resolve_fn is not None:
                self._resolve_fn(mem[0], f"נמשך {max(self._clock() - mem[1], 0):.0f}s: {mem[2]} הורדות מעל התקרה,"
                                         f" עד {mem[3]} במקביל ו-{mem[4]} מאותו משכפל")

    def _close(self, why: str, after: Sample | None = None) -> None:
        """התוצאה של ההחלטה שבתוקף: מה החליף אותה ואחרי כמה זמן, ולחיתוך גם **מה קרה לאובדן** במחזור שאחריו (#3596)."""
        mem, self._mem = self._mem, None
        if mem is not None and mem[1] is not None and self._resolve_fn is not None:
            tail = f"; {_after_cut(mem[3], after)}" if mem[3] is not None else ""
            self._resolve_fn(mem[1], f"{why} אחרי {max(self._clock() - mem[2], 0):.0f}s{tail}")

    def _memorize(self, d: Decision, mode: str, ceiling: int) -> None:
        """#3185: שורה בזיכרון ארוך-הטווח כשמשפחת ההחלטה משתנה (אין שידור / קיצוץ / כיוונון בתוך השארית), ובכל
        קיצוץ. ההחלטה הקודמת מקבלת את התוצאה שלה: מה החליף אותה, ואחרי כמה זמן. לא בכל מחזור: 360 בשעה."""
        if self._remember_fn is None:
            return
        family = f"{mode}:{d.action if d.action in (OPEN, CUT_, AT_FLOOR, NOT_APPLICABLE) else 'TUNE'}"
        if self._mem is not None and self._mem[0] == family and d.action != CUT_:
            return
        self._close(f"{d.action}: {d.reason}", self.sample)
        na = d.action == NOT_APPLICABLE
        row = self._remember_fn(
            mode=mode, rule="K7", action="HTTP_BUDGET", verdict=d.action, reason=d.reason,
            evidence=(f"budget={d.rate_bps / 1e6:.1f}Mbps", f"limit={d.limit_bps / 1e6:.1f}Mbps",
                      f"ceiling={ceiling / 1e6:.1f}Mbps", *d.evidence),
            **({"outcome": DIRECT_OUTCOME} if na else {}))      # #3750: החלטה שאין לה תוצאה נרשמת "n/a: <למה>", לא ריקה
        self._mem = (family, None if na else row, self._clock(), self.sample if d.action == CUT_ else None)

    def _record(self, d: Decision, mode: str, ceiling: int, source: str, delayed: int, delay_s: float) -> None:
        """שורה אחת להחלטה. מצב סרק יציב (אין שידור, אותו תקציב) אינו נרשם שוב: הוא אינו החלטה חדשה."""
        key = (d.action, d.rate_bps, d.limit_bps, mode)
        if d.action in (OPEN, NOT_APPLICABLE) and key == self._last_key and not delayed:
            return
        self._last_key = key
        line = "brain " + json.dumps({
            "at": datetime.fromtimestamp(self._wall(), timezone.utc).isoformat(timespec="seconds"),
            "mode": mode, "rule": "K7", "action": d.action,
            "budget_mbps": round(d.rate_bps / 1e6, 1), "limit_mbps": round(d.limit_bps / 1e6, 1),
            "ceiling_mbps": round(ceiling / 1e6, 1), "ceiling_from": source,
            "broadcast_mbps": None if d.broadcast_bps is None else round(d.broadcast_bps / 1e6, 1),
            "enforced": mode == ENFORCE,
            **({"delayed_chunks": delayed, "delayed_s": round(delay_s, 2)} if mode == ENFORCE else {}),
            "say": "התקציב נאכף" if mode == ENFORCE else "התקציב מחושב ונרשם בלבד (צל): שום הורדה לא מושהית",
            "reason": d.reason, "evidence": list(d.evidence)}, ensure_ascii=False)
        (log.warning if d.action in (CUT_, AT_FLOOR) else log.info)("%s", line)


# --- החיבור לשרת ------------------------------------------------------------------------------------------------


def _broadcast_bps(conn, chunk, sender) -> int | None:
    """הקצב שביקשנו מהזרם עכשיו: 0 = אין זרם, ``None`` = לא הצלחנו לשאול (אין להניח "אין שידור")."""
    try:
        asked = chunk.sending_bps()
        if asked:
            return asked
        status = sender.status()
        if status and status.get("state") in ("starting", "sending"):
            decided = drive_rates.decision(conn, status["session_id"]) or {}
            return drive_rates.parse_bitrate(decided.get("max_bitrate"))      # הזרם רץ ואין קצב ידוע = None
        return 0
    except Exception:                           # noqa: BLE001
        log.exception("brain: the rate of the stream could not be read")
        return None


def production(conn, *, served, chunk, sender, interface: str | None = None, mode: Callable[[], str] | None = None,
               sys_net=netsense.SYS_NET, proc=netsense.PROC) -> Budget:
    """התקציב של השרת האמיתי: המתג מההגדרות, הגבול מההגדרה או מהכרטיס, והמונים מהמנועים, מההגשה ומ-sysfs/proc."""
    from server.brain import veto                # כאן ולא למעלה: המתג מגיע מהווטו, והווטו לא צריך את התקציב
    from server.brain import integrity           # #3064: CRC ו-K4 רצים על אותו שעון (KT1), בצל בלבד

    integrity_observer = integrity.Observer(conn)

    def direct_wave() -> bool:
        """#3838 (ג): הגל שמשדר עכשיו הוא הפצה ישירה. ראיה חיובית בלבד: לא נקרא = לא ישיר (K7 ממשיך כרגיל)."""
        from server.rounds import direct         # כאן: `direct` מייבא את המוח (`ingest`)
        try:
            st = chunk.status()
            return bool(st and st.get("state") in ("sending", "repairing") and direct.is_direct_wave(conn, st["session_id"]))
        except Exception:                       # noqa: BLE001
            log.exception("brain: whether the wave is a direct send could not be read")
            return False

    def read() -> Reading:
        integrity_observer.tick()                # לעולם אינו זורק; כבוי = לא נקרא (המחזור לא מגיע לכאן ב-off)
        nic = netsense.nic_counters(chunk.interface or sender.interface or interface, sys_net)
        return Reading(at=time.monotonic(), served_bytes=served.total_all(),
                       broadcast_bps=_broadcast_bps(conn, chunk, sender),
                       nic_tx_bytes=nic["tx_bytes"], nic_tx_dropped=nic["tx_dropped"],
                       rx_dropped=netsense.receivers_rcvbuf(conn), psi_full_us=netsense.psi_io_full_us(proc),
                       rx_abandoned=netsense.receivers_abandoned(conn),
                       rx_missed=netsense.receivers_card(conn, "rx_missed"),
                       rx_no_buffer=netsense.receivers_card(conn, "rx_no_buffer"), direct=direct_wave())

    return Budget(mode=mode or (lambda: veto.mode(conn)),
                  ceiling=lambda: ceiling_bps(conn, sender.link()), read=read,
                  remember=lambda **row: memory.record(conn, **row),
                  resolve=lambda row, outcome: memory.resolve(conn, [row], outcome))
