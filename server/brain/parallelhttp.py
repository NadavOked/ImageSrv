"""#3064 (B5, X13): HTTP במקביל לזרם, לאותו דיסק -- **ההחלטה בלבד, בצל.** הביצוע (כותב יחיד שמקבל חתיכות גם מהזרם
וגם מ-HTTP) הוא שינוי ב-`agent/chunkcast.c`, ולא נבנה כאן.

**למה:** היום ההשלמה ב-HTTP (`apply`) רצה רק אחרי המעבר (`recv`). מגירה שיש לה מקום פנוי בכתיבה בזמן הזרם יכלה להשלים
במקביל את מה שחסר לה, וכך לקצר את הסבב. התנאי: התור לכתיבה של המגירה מתרוקן מהר, כלומר הזרם לבדו לא ממלא אותה.

**החוש** (S46/S47, `agent/lib/restore/wavesense.sh`, #3324): לכל מגירה, בכל חלון של `WAVE_SENSE_S` שניות, ``inflight``
(בקשות בתור ההתקן ברגע סגירת החלון) ו-``w_ms`` (זמן כתיבה ממוצע בחלון, `/proc/diskstats`).

**זמן הריקון** (`drain_ms`) = ``inflight × w_ms``: כמה זמן ייקח לתור שעומד שם עכשיו להתרוקן, אם כל כתיבה לוקחת את
הממוצע של החלון. בדיסק שמשרת כמה בקשות במקביל (NCQ) זה גבוה מהאמת, וההחלטה מחמירה, לעולם לא מקלה.

**ההחלטה** (`decide`, טהורה), עם היסטרזיס (X13):

- ``OPEN`` -- הריקון עד ``START_MS`` (250ms): היה פותח HTTP במקביל למגירה.
- ``CLOSE`` -- הריקון ``STOP_MS`` (1 ש') ומעלה: היה סוגר.
- בין השניים: מה שהיה קודם נשאר. בלי החלטה קודמת: סגור.
- ``UNMEASURED`` -- ``inflight`` או ``w_ms`` לא נקראו ("לא נמדד", לא 0). ``w_ms`` ריק כשלא הושלמה כתיבה בחלון, ואז נלקח
  ``w_ms_max`` של הגל (`write_ms`, #3440: ברזל 15/11, B5 היה ``UNMEASURED`` כשהחוש ``disk_w_ms_max`` כבר ב-DB); הראיה
  אומרת ממה נלקח (``w_ms_from=window|wave_max``). שניהם ריקים = לא נמדד, גם עכשיו.

**הרישום:** ``brain_decisions`` (כלל ``B5``, פעולה ``PARALLEL_HTTP``), שורה **רק כשהמצב של המגירה משתנה** (הדיווח מגיע כל
כמה שניות). הישות היא המכונה (``machine``, MAC), והמגירה בראיות. התוצאה: "צל: אין ביצוע".

**הספים הם נקודת התחלה** (R63, gpt6-b: 250ms / 1 ש', הערכה), לא הגדרות (נדב 14/11). M1 עוד לא נמדדה במקביל לזרם.
המתג הוא ``brain_veto_mode``: ``off`` = לא מחושב ולא נרשם. **לעולם אינו זורק** אל הדיווח.
"""

from __future__ import annotations

import logging
import threading

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "B5", "PARALLEL_HTTP"
OFF = "off"
OPEN, CLOSE, UNMEASURED = "OPEN", "CLOSE", "UNMEASURED"
#: X13: הריקון שעד אליו HTTP במקביל היה נפתח, ושממנו היה נסגר. הערכה (R63, gpt6-b), לא מדידה.
START_MS = 250.0
STOP_MS = 1000.0
#: הגל שומר את המצב של כל מגירה עד שהוא נגמר; מעבר לזה זיכרון התהליך אינו גדל
MAX_WAVES = 64
#: #3819: ``w_ms_from`` של UNMEASURED אומר למה: הדיסק לא נקרא כלל, או שנקרא ואף כתיבה לא הושלמה (לא "תור מהיר")
NO_COUNTERS, NO_WRITES = "none:no_counters", "none:no_writes"

_lock = threading.Lock()
_state: dict[str, dict[tuple[str, str], str]] = {}


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def write_ms(disk: object) -> tuple[float | None, str]:
    """(זמן הכתיבה לחישוב הריקון, ממה הוא נלקח): ``w_ms`` של החלון האחרון -- ``window``; ובחלון בלי כתיבה שהושלמה
    (`w_ms` ריק: הסוכן אינו ממציא ממוצע מאפס כתיבות) -- ``w_ms_max``, הגבוה בחלונות הגל (S47, #3440) -- ``wave_max``.
    שניהם ריקים = ``(None, "none:no_writes" | "none:no_counters")`` (#3819): לא נמדד. החלון הבודד אינו ראיה לכל הגל, אבל ``w_ms_max`` אינו נמוך מאף חלון
    שלו, ולכן ההחלטה מחמירה ולעולם לא מקלה (כמו שנאמר למעלה)."""
    if not isinstance(disk, dict):
        return None, NO_COUNTERS
    got = _num(disk.get("w_ms"))
    if got is not None:
        return got, "window"
    got = _num(disk.get("w_ms_max"))
    if got is not None:
        return got, "wave_max"
    # #3819: the counters were read (inflight is a number) but no write completed in any window = no_writes; the
    # drawer's counters were not read at all (null disk, or no inflight) = no_counters. Both stay UNMEASURED.
    return None, NO_WRITES if _num(disk.get("inflight")) is not None else NO_COUNTERS


def drain_ms(disk: object) -> float | None:
    """``inflight × w_ms`` של מגירה אחת, או ``None`` כשאחד מהם לא נקרא (`write_ms`: גם ``w_ms_max`` ריק)."""
    if not isinstance(disk, dict):
        return None
    inflight, w_ms = _num(disk.get("inflight")), write_ms(disk)[0]
    return None if inflight is None or w_ms is None else inflight * w_ms


def decide(drain: float | None, was: str | None) -> str:
    """(זמן הריקון, המצב הקודם של המגירה: OPEN / CLOSE / None) -> OPEN / CLOSE / UNMEASURED. בין שני הספים (HOLD)
    המצב הקודם נשאר; בלי מצב קודם -- סגור."""
    if drain is None:
        return UNMEASURED
    if drain <= START_MS:
        return OPEN
    if drain >= STOP_MS:
        return CLOSE
    return was if was in (OPEN, CLOSE) else CLOSE


def _say(value) -> str:
    return "unmeasured" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def _mode(conn) -> str:
    from server.brain import veto                # noqa: PLC0415 -- המתג הכללי של המוח
    return veto.mode(conn)


def observe(conn, session_id: str, mac: str, senses: dict | None) -> list[int]:
    """הסיכום של מכונה אחת בגל (`wavesense.well_formed`): לכל מגירה שהמצב שלה השתנה -- שורה. מחזיר את מספרי השורות."""
    rows: list[int] = []
    try:
        if senses is None or not isinstance(senses.get("disks"), dict):
            return rows
        mode = _mode(conn)
        if mode == OFF:
            return rows
        for drawer, disk in sorted(senses["disks"].items()):
            drain = drain_ms(disk)
            with _lock:
                if session_id not in _state and len(_state) >= MAX_WAVES:
                    _state.pop(next(iter(_state)))
                was = _state.setdefault(session_id, {}).get((mac, drawer))
                now = decide(drain, was)
                if now == was:
                    continue
                _state[session_id][(mac, drawer)] = now
            d = disk if isinstance(disk, dict) else {}
            w_used, w_from = write_ms(d)
            evidence = (f"drawer={drawer}", f"drain_ms={_say(None if drain is None else round(drain))}",
                        f"inflight={_say(d.get('inflight'))}", f"w_ms={_say(w_used)}", f"w_ms_from={w_from}",
                        f"util={_say(d.get('util'))}",
                        f"start_ms={START_MS:g}", f"stop_ms={STOP_MS:g}", f"was={was or 'none'}")
            if now == UNMEASURED:
                reason = f"זמן הריקון של {drawer} לא נמדד (inflight או w_ms לא נקראו): לא הוכרע"
            elif now == OPEN:
                reason = f"התור של {drawer} מתרוקן ב-{drain:.0f}ms (עד {START_MS:g}): היה פותח HTTP במקביל לזרם"
            else:
                reason = (f"התור של {drawer} מתרוקן ב-{drain:.0f}ms (מעל {START_MS:g}, והיה סגור, או {STOP_MS:g} ומעלה):"
                          " HTTP במקביל היה סגור")
            row = memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=now, reason=reason, evidence=evidence,
                                round_id=session_id, entity=("machine", mac),
                                outcome="צל: אין ביצוע (B5 דורש כותב יחיד ב-chunkcast)",
                                predicted=None if drain is None else round(drain, 1), unit="ms")   # #3446
            if row is not None:
                rows.append(row)
    except Exception:                           # noqa: BLE001 -- המוח אינו מפיל דיווח
        log.exception("brain: the parallel-HTTP decision of %s in %s failed", mac, session_id)
    return rows


def forget(session_id: str | None = None) -> None:
    with _lock:
        if session_id is None:
            _state.clear()
        else:
            _state.pop(session_id, None)
