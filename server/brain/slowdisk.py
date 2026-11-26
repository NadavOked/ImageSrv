"""#3268 (B62): דיסק איטי לפני הסבב -- המוח מכריע לפי שני נתונים יחד: מה הדיסק עשה בגל הקודם, ומה הבדיקה המתמשכת מראה עכשיו.

**הכרעת נדב, 18/11 (#3268, על #3650):** "זה המוח צריך להכריע אם פיגר פעם קודמת ואם הבדיקה הראתה שהוא מפגר עכשיו."
ובהמשך: "המוח מציע ולא פוסל" (16/11). לכן ההיסטוריה היא **ראיה ולא פסק דין**:

| הבדיקה עכשיו | היסטוריה (גל שפיגר בו) | פסק | מה רואים במסך ההמתנה |
|---|---|---|---|
| איטית | יש | ``RED`` | **אדום**, והיסטוריה מצורפת לסיבה |
| איטית | אין | ``RED`` | אדום, כמו תמיד |
| תקינה | יש, והקצב המתמשך שלה **מתחת לסף** | ``RED`` | **אדום**, והסיבה היא ההיסטוריה (הבדיקה הקצרה לא ארוכה מספיק כדי לראות את זה) |
| תקינה | יש, והקצב המתמשך שלה מעל הסף | ``INFO`` | שורת מידע ניטרלית (פיגור חד-פעמי), **בלי אדום** |
| תקינה | אין | ``NONE`` | כלום |
| תקינה | לא נקראה / הלמידה כבויה | ``UNDECIDED`` | כלום, והשורה אומרת למה (עיקרון 5: "לא ידוע" אינו "אין") |

**עדכון להכרעה, נדב 18/11 (#3268, אחרי #3718):** בדיקת 30 ש' אינה עוברת את המטמון של ה-APS (כ-6GiB), ובדיקה ארוכה היא "הרבה זמן".
לכן הבדיקה נשארת קצרה, וההיסטוריה היא שחושפת: כונן שהבדיקה עכשיו עברה, אבל **בגל שפיגר בו כתב בקצב מתמשך מתחת לסף**
(``slow_memory.Memory.mbps`` = ``chunk_rate.sustained``, MB/s שהכונן הפך עמידים בגל), הוא ``RED``. הסף הוא **אותו סף של הבדיקה**,
כמו שהסוכן השתמש בו (``floor_mbps`` ב-``write_check``): השרת אינו מחשב סף ואינו מוסיף סף. סף שלא דווח (סוכן ישן, או דיסק מכני
שאין לו סף כזה) = ההיסטוריה לבדה אינה צובעת: ``INFO``. "לא ידוע" אינו "איטי" (עיקרון 5).

**מה עובר.** הסוכן מדווח ב-hello את תוצאת הבדיקה של כל מגירה שסיימה (``write_check``: סידורי, מצב, MB/s בחצי האחרון,
MB/s בחצי הראשון, והסיבה שלו אם איטי). ההחלטה חוזרת בתשובה (``write_decisions``, רק ``RED`` ו-``INFO``) ו**המסך מציג אותה
כמו שהיא**: המוח הוא המקור היחיד, והסוכן אינו מחבר היסטוריה לבדו. אין החלטה (שרת שקט, סוכן שלא דיווח, המתג ``off``, תוצאה
אחרת מזו שהוחלט עליה) = המסך נשען על הבדיקה המקומית בלבד, כמו לפני הכלל. **"לא קיבלנו החלטה" אינו "תקין"**: דיסק שהבדיקה
המקומית שלו אומרת איטי נשאר אדום גם בלי החלטה, כי אדום מגיע מהבדיקה של עכשיו.

**"איטי עכשיו" הוא של הסוכן.** הוא יודע סוג דיסק (מוצק/מכני), את מדיניות ה-HDD ואת שאר המגירות (כמו ``write_speed_verdict`` ו-
``slow_write.py``), והשרת לא מחשב את הסף מחדש. המוח מוסיף את מה שהסוכן אינו יודע: ההיסטוריה (`slow_memory`).

**מצב.** צל, כמו אזהרת K3 (``linkwarn``): הכלל **מוצג** למפעיל גם בצל, ו**אינו חוסם דבר בשום מצב** (הסבב אינו קורא אותו).
``brain_veto_mode`` = ``off`` מכבה אותו לגמרי: לא מחושב, לא נשלח ולא נרשם. ב-``brain_decisions`` נרשם ``mode`` כמו שהוא.

**רישום.** שורה ב-``brain_decisions`` (``B62 / SLOW_DISK``, ישות ``disk``) **לכל ראיה שונה**, עם שתי הראיות (``now_*`` ו-
``history_*``). ה-hello חוזר כל ~2 שניות, ולכן אותה ראיה בדיוק אינה נרשמת שוב (נבדק מול השורה האחרונה של הדיסק, לא מול זיכרון
בתהליך). ``NONE`` ו-``UNDECIDED`` נרשמים גם הם: "המוח קרא את שתי הראיות ואין מה לומר" שונה מ"הכלל לא רץ".

**לעולם אינו זורק** אל ה-hello: כשל הוא שורת לוג, ואין החלטה.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TypeGuard

from server.brain import memory, veto
from server.disks import slow_memory

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "B62", "SLOW_DISK"
RED, INFO, NONE, UNDECIDED = "RED", "INFO", "NONE", "UNDECIDED"
#: מה נשלח לסוכן: רק מה שמשנה את המסך. ``NONE`` ו-``UNDECIDED`` נרשמים ואינם נשלחים (המסך נשען על הבדיקה המקומית).
SENT = (RED, INFO)
#: דיווחים מקסימליים ב-hello אחד (מגירות במשכפל: ארבע, ופי ארבעה בטוח), וגודל הסיבה. עודף נזנח, ה-hello נענה.
MAX_REPORTS = 16
MAX_REASON = 300
MAX_MBPS = 100_000
SHOWN = "נשלח למסך ההמתנה של המשכפל (הצגה בלבד, שום דבר לא נחסם)"
NOT_SHOWN = "אין מה להציג: המסך נשען על הבדיקה המקומית"


@dataclass(frozen=True)
class Now:
    """מה שהסוכן דיווח על בדיקת הכתיבה של מגירה שסיימה. ``mbps`` = החצי האחרון (המתמשך), ``first_mbps`` = החצי הראשון
    (``None`` = לא נמדד: בדיקה קצרה בלבד, או שהשעון לא נקרא)."""
    serial: str
    state: str                  # "ok" | "slow" -- הבדיקה המקומית, כמו שהסוכן אמר אותה
    mbps: int
    first_mbps: int | None
    reason: str                 # הסיבה של הסוכן כשהוא איטי ("איטי — 59 MB/s, מתחת לסף 74 MB/s"), אחרת ""
    floor_mbps: int | None = None   # הסף שהסוכן בדק מולו את המגירה הזו (``None`` = לא דווח: סוכן ישן, או מגירה שאין לה סף כזה)


def _count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_MBPS


def _line(value: object) -> str:
    """טקסט חיצוני לשורה אחת בלי `|` (תו המפריד של הקובץ בסוכן) ובלי תווי בקרה."""
    text = " ".join(re.sub(r"[\x00-\x1f\x7f|]", " ", value).split()) if isinstance(value, str) else ""
    return text[:MAX_REASON]


def well_formed(raw: object, disks: object) -> list[Now]:
    """הדיווחים התקינים של ה-hello. מגירה שאינה בין הדיסקים של אותו hello, סידורי כפול, מצב שאינו ``ok``/``slow`` או מספר שאינו
    מספר -- נזנחים בשמם ללא פגיעה ב-hello. שדה חסר או פגום אינו מומצא."""
    if not isinstance(raw, list):
        return []
    serials = {d["serial"] for d in disks if isinstance(d, dict) and isinstance(d.get("serial"), str)} \
        if isinstance(disks, list) else set()
    out: list[Now] = []
    seen: set[str] = set()
    for item in raw[:MAX_REPORTS]:
        if not isinstance(item, dict):
            continue
        serial, state, mbps, first = item.get("serial"), item.get("state"), item.get("mbps"), item.get("first_mbps")
        floor = item.get("floor_mbps")
        if (not isinstance(serial, str) or serial not in serials or serial in seen or state not in ("ok", "slow")
                or not _count(mbps) or (first is not None and not _count(first))):
            continue
        seen.add(serial)
        out.append(Now(serial, state, mbps, first, _line(item.get("reason")) if state == "slow" else "",
                       floor if _count(floor) else None))   # סף פגום אינו מומצא: אינו ידוע
    return out


def _detail(was: slow_memory.Memory) -> str:
    """"55MB/s, 17/11": מה הדיסק כתב בגל שפיגר בו ומתי -- ראיה, לא פסק דין. בלי תאריך כשהשורה של הגל נעלמה."""
    date = re.match(r"\d{4}-(\d{2})-(\d{2})", was.at) if isinstance(was.at, str) else None
    return f"{was.mbps}MB/s" + (f", {date.group(2)}/{date.group(1)}" if date else "")


def _when(was: slow_memory.Memory) -> str:
    """"17/11" -- מתי נרשם הגל, או "" כשהשורה שלו נעלמה."""
    date = re.match(r"\d{4}-(\d{2})-(\d{2})", was.at) if isinstance(was.at, str) else None
    return f"{date.group(2)}/{date.group(1)}" if date else ""


def judge(now: Now, was: slow_memory.Memory) -> tuple[str, str]:
    """(פסק, הסיבה): טהורה. הסיבה היא מה שהמסך מציג ומה שנרשם."""
    if now.state == "slow":
        base = now.reason or f"איטי — {now.mbps} MB/s"
        if was.state == slow_memory.FOUND:
            return RED, f"{base}; וגם בגל הקודם פיגר ({_detail(was)})"
        return RED, base
    if was.state == slow_memory.FOUND:
        if now.floor_mbps is not None and was.mbps is not None and was.mbps < now.floor_mbps:
            when = _when(was)
            return RED, (f"בגל הקודם כתב {was.mbps}MB/s לאורך כל הגל{f' ({when})' if when else ''}, מתחת לסף {now.floor_mbps} MB/s"
                         " — הבדיקה הקצרה לא מספיק ארוכה כדי לראות את זה")
        return INFO, f"פיגר בגל הקודם ({_detail(was)}); הבדיקה עכשיו תקינה"
    if was.state == slow_memory.NONE:
        return NONE, "אין היסטוריה של איטיות לדיסק, והבדיקה עכשיו תקינה"
    why = "הלמידה האוטומטית כבויה בשרת, ולכן לא ידוע אם פיגר בעבר" if was.state == slow_memory.OFF \
        else "ההיסטוריה של הדיסק בשרת לא נקראה"
    return UNDECIDED, f"{why}; הבדיקה עכשיו תקינה"


def _evidence(mac: str, now: Now, was: slow_memory.Memory) -> list[str]:
    """שתי הראיות, בנוסח קבוע (ההשוואה לשורה הקודמת היא לפיו)."""
    first = "null" if now.first_mbps is None else str(now.first_mbps)
    out = [f"mac={mac}", f"serial={now.serial}", f"now_state={now.state}", f"now_mbps={now.mbps}", f"now_first_mbps={first}",
           f"now_floor_mbps={'null' if now.floor_mbps is None else now.floor_mbps}"]
    if now.reason:
        out.append(f"now_reason={now.reason}")
    out.append(f"history={was.state}")
    if was.state == slow_memory.FOUND:
        out += [f"history_mbps={was.mbps}", f"history_at={was.at or 'null'}", f"history_wave={was.session_id}"]
    out += [f"history_note={n.get('session_id')}: {n.get('why')}" for n in was.notes if isinstance(n, dict)]
    return out


def _remember(conn, mode: str, mac: str, now: Now, was: slow_memory.Memory, verdict: str, reason: str) -> None:
    """שורה ב-``brain_decisions``, אלא אם הראיה זהה לזו של השורה האחרונה של הדיסק."""
    evidence = _evidence(mac, now, was)
    last = memory.decisions(conn, kind="disk", key=now.serial, rule=RULE, limit=1)
    if last and last[0]["verdict"] == verdict and last[0]["reason"] == reason and last[0].get("evidence") == evidence:
        return
    memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=verdict, reason=reason, evidence=evidence,
                  entity=("disk", now.serial), outcome=SHOWN if verdict in SENT else NOT_SHOWN)


def decide(conn, mac: str, raw: object, disks: object) -> list[dict]:
    """מה שחוזר לסוכן בתשובת ה-hello: ``[{"serial", "verdict", "state", "mbps", "first_mbps", "reason"}]`` ל-``RED`` ול-``INFO``.
    ``state``/``mbps``/``first_mbps`` הם **הראיה שעליה הוחלט**: הסוכן מציג את ההחלטה רק כשהתוצאה המקומית שלו זהה להן. ``[]`` = אין
    החלטה (גם כשהמתג ``off``)."""
    try:
        mode = veto.mode(conn)
        if mode == veto.OFF:
            return []
        sent: list[dict] = []
        for now in well_formed(raw, disks):
            try:
                was = slow_memory.of(conn, now.serial)
                verdict, reason = judge(now, was)
                try:
                    _remember(conn, mode, mac, now, was, verdict, reason)
                except Exception:                # noqa: BLE001 -- ההחלטה נשלחת גם כשהרישום נכשל (`memory.record` רושם בלוג)
                    log.exception("brain: B62 could not remember %r of %s", now.serial, mac)
                if verdict in SENT:
                    sent.append({"serial": now.serial, "verdict": verdict, "state": now.state, "mbps": now.mbps,
                                 "first_mbps": now.first_mbps, "reason": reason})
            except Exception:                    # noqa: BLE001 -- דיסק אחד אינו עוצר את האחרים
                log.exception("brain: B62 could not decide on %r of %s", now.serial, mac)
        return sent
    except Exception:                            # noqa: BLE001 -- המוח אינו מפיל hello
        log.exception("brain: B62 failed for %s", mac)
        return []
