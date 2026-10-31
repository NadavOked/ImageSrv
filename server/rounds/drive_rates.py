"""קצב המולטיקאסט לכל גל — נקבע בשרת לבד (#447 + הכרעת נדב 11/10).

‏`udp-sender --max-bitrate` הוא מספר אחד לכל הזרם. **הכרעת נדב 11/10:**
"אני לא נוגע בזה, והשרת לבד מחליט". לפני כל גל:

    פתיחה = min(START_BITRATE, גג)    גג = IMAGESRV_MAX_BITRATE, ובלעדיו TOP_BITRATE
    (15/10: כשמהירות כרטיס ההפצה נקראה — גג 90% ממנה ופתיחה 60%; ראו `read_link`;
     ולעולם לא מעל 90%/60% ממחשב השיכפול האיטי בגל — `nic_speed`, הנחת 1G כשלא ידוע)

    קצב = המינימום על **מחשבי השיכפול** בגל (#1471, הכרעת נדב 16/10) — לכל
          מכונה (MAC), מ-GOODPUT_WINDOW הגלים האחרונים שלה עם אותו מספר מגירות (`_pick`),
          לפי **goodput** (‏#1758): הבייטים שהמגירות שלה כתבו בפועל, לכל מגירה,
          חלקי משך הזרם (הבייט הראשון → `send_done`). חיפוש שיא (`_climb`):
          ‏climb — הקצב שבו הגל היה הכי מהיר רץ בקצב שלו (‏≥ KEEP_UP) ואין מעליו
                  קצב שנוסה — ‏+STEP_UP; יש מעליו קצב שלא היה מהיר יותר — באמצע
                  (כל עוד הפער ≥ 2×REFINE_STEP)
          ‏peak  — השיא נמצא — נשארים בו
          ‏back  — גם הגל המהיר ביותר לא רץ בקצב שלו, ואין מתחתיו קצב שנוסה — ×DOWN_FACTOR
          ‏held  — בשיא היו שידורים חוזרים מעל LOSS_CLIMB_MAX, או שאבדה בו מגירה,
                  או ש-udp-sender כתב בו `Timeout notAnswered` מעל TIMEOUTS_PER_GB_HELD
                  לכל GB שנשלח (‏#1761)
          אבדן אינו מטרה — הוא כבר בתוך הזמן. הוא **הגנה** בשני מקומות בלבד:
          מגירה שאבדה בגל עם שידורים חוזרים מעל LOSS_DOWN_RATIO, ושולח שנכשל
          עם שידורים חוזרים כאלה (goodput 0) — מעליהם לא מחפשים.
          ‏#1761 — האות הישיר: גל שבו udp-sender כתב `Dropping client` (‏goodput 0)
          — הקצב לא בטוח. קצב לא בטוח נרשם ב-`unsafe_rates` ואינו נשכח כשיוצא
          מהחלון. יומן שולח שלא נקרא — אין טיפוס (‏`unmeasured`).
          בלי אף גל עם goodput (גלים מלפני #1758) — הכלל הקודם, על הגל האחרון:
          ‏+STEP_UP      — שידורים חוזרים ≤ LOSS_DOWN_RATIO **וגם** סכום
                          RcvbufErrors של המקלטים ≤ LOSS_DOWN_RATIO מהבלוקים,
                          וכל מקלט דיווח (הכרעת נדב 17/10)
                          **וגם** כל כונן שלו עמד בקצב
                          **וגם** הזרם עצמו רץ ב-KEEP_UP מהקצב לפחות
          ‏× DOWN_FACTOR — שידורים חוזרים מעל LOSS_DOWN_RATIO מהבלוקים
          כמות שהוא    — כל השאר, כולל "לא נמדד" (עיקרון 5)
          "הגל האחרון" — עם אותו מספר מגירות במכונה, ובלעדיו הקרוב (`_pick`);
          מהקרוב עם **פחות** מגירות — הקצב שהוכח × DOWN_FACTOR לכל הכפלה (‏#1842)
          מכונה בלי גל כזה (חדשה / הוחלפה) — **כל הגל** בפתיחה (`_from_previous`)
    קצב = min(קצב, הכונן האיטי המוכר × MARGIN)        (#447, למטה)
    קצב = בין FLOOR_RATIO × פתיחה לבין הגג

עלייה רק על **ראיה חיובית** לגל נקי: המונה של udp-sender עצמו נקרא בסוף
הגל (‏`record_stream`) ואינו עובר את הסף, כל מקלט דיווח כמה datagrams הקרנל
שלו זרק (‏`RcvbufErrors`, ‏`record_wave`) והסכום אינו עובר אותו סף, ולכל כונן של הגל יש דגימה
שעמד בה בקצב. מונה שלא נקרא — של השולח או של מקלט אחד — גל שנעצר לפני
הסוף, כונן שנכשל או בלי דגימה — אין ראיה, והקצב נשאר. הזרם עצמו אינו משתנה; רק המספר שעובר ל-`--max-bitrate`.

**הזיכרון של הכוננים (#447).** כאן נשמר מה שכל כונן (דגם + סידורי) באמת
כתב בגלים שהסתיימו, וכונן שידוע שהוא איטי מוריד את הגל אליו:

    תקרת כונן = הקצב של הכונן האיטי המוכר × MARGIN

כונן שאינו מוכר אינו מוריד דבר (לא מנחשים).

שלוש הבחנות שבלעדיהן הלמידה מכיילת את עצמה על שקר:

1. **כונן שעמד בקצב הגל אינו מגביל.** במולטיקאסט כל כונן כותב בקצב הזרם,
   והזרם חסום בתקרה — כונן שכתב 24MB/s תחת 200m "עשה 24" רק כי זה מה
   שנשלח. דגימה כזו אומרת "לפחות התקרה", ולמידה שהייתה לוקחת אותה כקצב
   הייתה מורידה כל גל ב-10% עד אינסוף (ראצ'ט). רק דגימה מתחת ל-`KEEP_UP`
   מתקרת הגל היא ראיה שהכונן הגביל.
2. **הערכת היכולת היא המקסימום, לא האחרון.** ‏`WINDOW` הגלים האחרונים: דגימה
   מוגבלת שווה את הקצב שלה, דגימה שעמדה בקצב שווה את תקרת הגל שלה. אחרי
   שהגל הורד לכונן איטי הוא עומד בקצב — וזו אינה סיבה להוריד שוב, וגם לא
   לחזור לתקרה. בלי אף דגימה מוגבלת בחלון — הכונן אינו מגביל.
3. **ייחוס אינו מושלם, ונאמר כך.** כל הכוננים בגל כותבים בקצב האיטי שבהם,
   ולכן כונן מהיר שישב עם כונן איטי נרשם "מוגבל" גם הוא. גל אחד שבו עמד
   בקצב הגל מנקה אותו, ואחרת הדגימה יוצאת מהחלון אחרי `WINDOW` גלים.

שלושה סייגים נגד הרעלה (סקירת אבטחה, 11/10): כונן מוריד רק אחרי
`MIN_HELD_SAMPLES` גלים שונים שבהם הגביל; נלמד רק מכונן שהיה ברשימת הגל
כשיצא (מכונה + סידורי, כפי שהשרת ראה לפני הגל — לא מה שמדווח בסוף); והקצב
אינו יורד מתחת ל-`FLOOR_RATIO` מקצב הפתיחה — גם לא על שידורים חוזרים.

עיקרון 5: תקרה לא-מספרית — עוברת כמות שהיא, בלי למידה. מתג שאינו "true" —
קצב הפתיחה, קבוע. גל בלי תקרה ידועה, דגימה בלי זמנים, מונה שלא נקרא —
"לא נלמד". וכשל בלמידה עצמה אינו מפיל גל: הוא נרשם ביומן, והגל יוצא
בקצב הפתיחה.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from server.agent import cloner_cpu
from server.rounds import nic_speed
from server.learn import server_io
from server.db import _write_lock, get_setting, journal, now_iso, writing

log = logging.getLogger("imagesrv.drive_rates")

#: מתג ההגדרה (טבלת `settings`). ברירת המחדל "true" (`db.DEFAULT_SETTINGS`).
SETTING = "learned_rate_enabled"
#: כמה גלים אחרונים של כל כונן נחשבים.
WINDOW = 5
#: שולי ביטחון: משדרים מעט מתחת למה שהכונן האיטי הוכיח שהוא כותב.
MARGIN = 0.9
#: מתחת לאיזה חלק מתקרת הגל דגימה נחשבת "הכונן הגביל". ‏udp-sender מגיע
#: ל-95.7% מהתקרה בתשלום (191.4Mbit/s מתוך 200m, ‏handoff 06/09); הממוצע
#: של מגירה כולל גם את ההמתנות בין המחיצות, ולכן הסף נמוך מזה.
KEEP_UP = 0.8
#: דגימה קצרה או קטנה מדי היא רעש של התחלה וסוף, לא קצב.
MIN_SAMPLE_BYTES = 10 ** 9
MIN_SAMPLE_S = 60
#: כמה גלים שונים שבהם הכונן הגביל נדרשים לפני שהוא מוריד גל. דגימה אחת —
#: כונן חם, גל חריג, או דיווח כוזב של מכונה אחת — אינה תכונה של כונן.
MIN_HELD_SAMPLES = 2
#: רצפה לקצב, כחלק מקצב הפתיחה (600m → 150m; ‏200m → 50m). מה שהלמידה
#: יכולה לעשות במקרה הגרוע — דגימות מורעלות, כונן גוסס, מקבל שמבקש שידור
#: חוזר על כל בלוק — חסום כאן, ולא משתק חדר שלם בקצב של 1m.
FLOOR_RATIO = 0.25

#: קצב הפתיחה, בביטים לשנייה: ‏600m — נמדד על ברזל ב-06/09 (מ.19,
#: ‏`docs/verification-register.md`): ‏61.06MB/s לשני מקבלים, ‏re-xmits=0.
#: ⚠️ המדידה ההיא הייתה **בלי כתיבה לדיסק** — לכן הוא נקודת פתיחה, לא
#: הבטחה; הירידה על שידורים חוזרים והכוננים האיטיים (#447) הם מה שמגן.
START_BITRATE = 600_000_000
#: הגג כשאין `IMAGESRV_MAX_BITRATE`: קו של 1Gb/s, פחות מקום ל-ACK-ים של
#: udpcast ולשאר התעבורה (‏udp-sender מגיע ל-95.7% מהתקרה, ‏handoff 06/09).
#: ‏`IMAGESRV_MAX_BITRATE`, כשהוגדר, מחליף אותו — כלפי מטה או מעלה.
TOP_BITRATE = 900_000_000
#: צעד העלייה אחרי גל נקי: ‏600→900 בשלושה גלים נקיים.
STEP_UP = 100_000_000
#: הירידה אחרי גל עם שידורים חוזרים: רבע מהקצב.
DOWN_FACTOR = 0.75
#: מעל איזה חלק מהבלוקים שידורים חוזרים מורידים. ‏udp-sender מדפיס את
#: היחס הזה בעצמו ברזולוציה של 0.1% — מתחת לסף הוא מדפיס "0.0%". בין
#: אפס לסף הקצב נשאר: לא נקי מספיק כדי לעלות, לא רע מספיק כדי לרדת.
#: ⚠️ ניחוש מנומק, לא מדידה — לכייל על ברזל.
LOSS_DOWN_RATIO = 0.001
#: גודל הבלוק של udpcast (‏`--blocksize`; ברירת המחדל לפי ה-man page, והשרת
#: אינו מעביר אותו) — המכנה של `LOSS_DOWN_RATIO`. לא נמדד על ברזל.
UDPCAST_BLOCK = 1456
#: כמה גלים אחרונים סורקים כדי למצוא את הגל האחרון של כל מכונה (#1471).
MEMORY_SCAN = 50

#: ‏#1758 — goodput. קצב גבוה יותר "מהיר יותר" רק כשה-goodput שלו עולה על
#: זה של הנמוך ביותר מ-3%; אחרת הנמוך עדיף (אותו זמן, פחות עומס). המקור:
#: ברזל 18/10, משכפל 2, ‏19.5GB — היעילות (goodput / קצב) של שלושה גלים
#: בקצבים דומים: ‏92.7% ‏(270m) · ‏90.9% ‏(361m) · ‏90.0% ‏(370m), פיזור של
#: ‏2.7 נקודות. הבדל קטן מזה אינו מבדיל בין קצבים.
GOODPUT_NOISE = 0.03
#: ‏#1758 — הצעד הקטן ביותר בחיפוש השיא, בין קצב טוב לקצב שמעליו שלא היה
#: מהיר יותר. ‏25m הם 5% מ-482m: ברזל 18/10 הראה שמתחת לשיא הזמן נע עם
#: הקצב (‏482m→375ש', ‏361m→476ש'), ולכן 5% הם כ-19 שניות בגל של 375 —
#: מעל הרעש של GOODPUT_NOISE. צעד קטן מזה אינו נמדד.
REFINE_STEP = 25_000_000
#: ‏#1758 — מעל איזה חלק שידורים חוזרים לא עולים מהשיא, גם כשהזרם רץ בקצב
#: שלו. זה **גבול השטח שנמדד**, לא ניחוש: הגלים עם הכי הרבה שידורים
#: חוזרים שהסתיימו — ‏1.70% (‏600m, ‏i5-650, ‏17/10, ‏228044 על ~13.4M
#: בלוקים) ו-1.47% (‏482m, ‏18/10, ‏375ש', המהיר בסדרה). מעל 2% אין לנו גל
#: אחד שהסתיים, ושם `--retries-until-drop` עלול להפיל מקלט (נפילת ה-90%, 11/09).
LOSS_CLIMB_MAX = 0.02
#: ‏#1761 — מעל כמה שורות `Timeout notAnswered` לכל GB שנשלח (‏10⁹ בייט של
#: ‏`sent_bytes`) לא עולים מהשיא. המקור: ברזל 19/10, משכפל 2, 1-2 מגירות,
#: ‏19.5GB — פקיעות הן חלק רגיל מזרם שהצליח: מגירה אחת ב-900m ‏55; שתי
#: מגירות 277m→1, ‏377m→7, ‏477m→16, ‏577m→53/47/42/67 — אפס ניתוקים, 14/14
#: מגירות הצליחו. המקסימום בלי ניתוק: ‏67/19.5GB = ‏3.4/GB. "פקיעה אחת עוצרת"
#: היה נועל שתי מגירות ב-277m. ניתוק (`Dropping client`) הוא האות הקשה.
TIMEOUTS_PER_GB_HELD = 5.0
#: ‏#1758 — כמה גלים אחרונים של מכונה (באותו מספר מגירות) נחשבים בחיפוש
#: השיא. קצב שלא היה מהיר יותר נשכח אחרי החלון, ואז נבדק שוב — בעקומה עם
#: שיא זה עד שלושה גלים איטיים (‏+100m, ואז באמצע פעמיים, כל אחד 2%–9% איטי
#: יותר בטסט `_peaked`). על פני 10 גלים — כ-1.5% בממוצע; חלון של 5 (כמו
#: לכוננים) היה מכפיל את המחיר. ומספיק קצר כדי שמגירות ואימג'ים שהתחלפו
#: ייבדקו שוב באותו יום עבודה.
GOODPUT_WINDOW = 10

#: ‏הכרעת נדב 15/10: "הכי מהר שאפשר, והשרת מגביל לפי כרטיס הרשת שלו".
#: כשמהירות הקישור של כרטיס ההפצה נקראה (‏`read_link`), במקום 600m/900m:
#:     גג    = ‏LINK_CEILING_PER_MBPS × מהירות   (90% — מקום ל-ACK ולשידור חוזר)
#:     פתיחה = ‏LINK_START_PER_MBPS × מהירות    (60% — שני שלישים מהגג)
#: ב-ביטים לשנייה לכל Mb/s. על 1GbE זה **בדיוק** 900m/600m — ההתנהגות של
#: היום; על 10GbE ‏9000m/6000m. ‏`IMAGESRV_MAX_BITRATE`, כשהוגדר, נשאר גג
#: — הנמוך מבין השניים. הרצפה **אינה** גדלה עם הקישור (`_floor`).
LINK_CEILING_PER_MBPS = TOP_BITRATE // 1000
LINK_START_PER_MBPS = START_BITRATE // 1000
SYS_NET = Path("/sys/class/net")
#: דרייברים של כרטיס וירטואלי: המהירות שלהם היא מה שהמארח **מצהיר**
#: (‏vmxnet3 תמיד 10000), לא הקו הפיזי — עיקרון 5א. בלי ראיה — ההתנהגות
#: של היום.
VIRTUAL_DRIVERS = frozenset({"vmxnet3", "virtio_net", "hv_netvsc", "xen_netfront"})


def read_link(interface: str | None, sys_net: Path = SYS_NET) -> dict:
    """מהירות הקישור של כרטיס ההפצה מ-`/sys/class/net/<if>/speed`.

    ‏`{"interface", "speed_mbps", "why"}`: ‏`speed_mbps` מספר חיובי ו-`why`
    ‏None — נקרא; אחרת `speed_mbps` ‏None ו-`why` אומר למה (עיקרון 5: "לא
    נקרא" אינו 1GbE): ‏`no_interface` · ‏`absent` (אין כרטיס/קובץ) ·
    ‏`unreadable` (‏OSError — לינוקס מחזיר EINVAL כשהקישור למטה) · ‏`unknown`
    ‏(‏-1 או לא מספר) · ‏`virtual` (‏`VIRTUAL_DRIVERS`). ‏bond — הסלייב
    האיטי: זרם מולטיקאסט אחד יוצא בסלייב אחד. לעולם אינו זורק."""
    view = {"interface": interface or None, "speed_mbps": None, "why": None}
    if not interface:
        return {**view, "why": "no_interface"}
    try:
        mbps, why = _link_speed(Path(sys_net), interface)
    except OSError:
        mbps, why = None, "unreadable"
    return {**view, "speed_mbps": mbps, "why": why}


def _link_speed(sys_net: Path, name: str) -> tuple[int | None, str | None]:
    entry = sys_net / name
    if not entry.is_dir():
        return None, "absent"
    slaves = entry / "bonding" / "slaves"
    if slaves.exists():
        got = [_link_speed(sys_net, s) for s in slaves.read_text().split()]
        if not got:
            return None, "unknown"
        why = next((w for m, w in got if m is None), None)
        return (None, why) if why else (min(m for m, _ in got), None)
    uevent = entry / "device" / "uevent"
    if uevent.exists() and any(
            line.strip() in {f"DRIVER={d}" for d in VIRTUAL_DRIVERS}
            for line in uevent.read_text().splitlines()):
        return None, "virtual"
    speed = entry / "speed"
    if not speed.exists():
        return None, "absent"
    try:
        mbps = int(speed.read_text().strip())
    except ValueError:
        return None, "unknown"
    return (mbps, None) if mbps > 0 else (None, "unknown")


def _link_bounds(link: dict | None) -> tuple[int, int] | None:
    """(גג, פתיחה) בביטים מהקישור; ‏None — המהירות לא נקראה."""
    mbps = (link or {}).get("speed_mbps")
    if not isinstance(mbps, int) or isinstance(mbps, bool) or mbps <= 0:
        return None
    return mbps * LINK_CEILING_PER_MBPS, mbps * LINK_START_PER_MBPS

_BITRATE = re.compile(r"^(\d+(?:\.\d+)?)([kmg]?)$")
_UNIT = {"": 1, "k": 10 ** 3, "m": 10 ** 6, "g": 10 ** 9}


def parse_bitrate(raw: object) -> int | None:
    """‏`--max-bitrate` של udpcast בביטים לשנייה; ‏None = אינו מספר חיובי."""
    if not isinstance(raw, str):
        return None
    match = _BITRATE.match(raw.strip().lower())
    if match is None:
        return None
    bits = int(float(match.group(1)) * _UNIT[match.group(2)])
    return bits if bits > 0 else None


def enabled(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SETTING) == "true"


def _limit_of(conn: sqlite3.Connection, serial: str,
              model: str | None) -> tuple[int | None, int | None, bool]:
    """(התקרה בביטים שהכונן מצדיק, הקצב המוגבל שנמדד לו, האם מוכר בכלל).

    דגימה שהכונן הגביל בה מצדיקה את הקצב שלה × `MARGIN`; דגימה שעמד בה
    בקצב מצדיקה את תקרת הגל שלה **כמות שהיא** — הוא הוכיח שהוא עומד בה,
    ומקדם ביטחון עליה היה הראצ'ט מהדלת האחורית. ההערכה היא המקסימום
    בחלון. בלי דגימה מוגבלת בחלון — (None, None, מוכר?)."""
    rows = conn.execute(
        "SELECT rate_bps, cap_bps FROM drive_rates WHERE serial = ? AND model IS ?"
        " ORDER BY id DESC LIMIT ?", (serial, model, WINDOW),
    ).fetchall()
    def held_back(r) -> bool:
        return r["rate_bps"] < KEEP_UP * r["cap_bps"]
    held = [r["rate_bps"] for r in rows if held_back(r)]
    if len(held) < MIN_HELD_SAMPLES:
        return None, None, bool(rows)
    bits = max(int(r["rate_bps"] * 8 * MARGIN) if held_back(r) else r["cap_bps"] * 8
               for r in rows)
    return bits, max(held), True


def _fmt(bits: int) -> str:
    return f"{max(1, bits // 10 ** 6)}m"


def _all_kept_up(conn: sqlite3.Connection, session_id: str, roster: list,
                 drops: dict | None = None) -> bool:
    """ראיה חיובית שכל כונן של הגל עמד בקצב: לכל אחד דגימה מהגל הזה, והיא
    לפחות `KEEP_UP` מתקרת הגל. כונן בלי דגימה (גל קצר, דיווח שלא הגיע) —
    אין ראיה.

    ‏**מגירה שהסוכן דיווח שנכשלה** (‏`failed_drawers` של המכונה ב-`drops`,
    ‏`record_wave`) — אינה חוסמת: ‏rc2, ‏ses_54a923dd — מגירה נפלה בדקה
    הראשונה על ‏`device offline`, והזרם, השידורים החוזרים והמגירה השנייה
    הוכיחו את הקצב. לכל מכונה עדיין נדרשות דגימות לכל מגירה שלא נכשלה,
    ולפחות אחת; ודגימה אחת שלא עמדה בקצב חוסמת כמו קודם."""
    mine: dict[str, set] = {}
    for d in roster:
        if not isinstance(d, dict) or d.get("serial") is None:
            return False
        mine.setdefault(d.get("mac"), set()).add(d["serial"])
    if not mine:
        return False
    kept, short = set(), set()
    for r in conn.execute(
            "SELECT serial, rate_bps, cap_bps FROM drive_rates WHERE session_id = ?",
            (session_id,)):
        (kept if r["rate_bps"] >= KEEP_UP * r["cap_bps"] else short).add(r["serial"])
    for mac, serials in mine.items():
        seen = (drops or {}).get(mac)
        failed = seen.get("failed_drawers") if isinstance(seen, dict) else 0
        failed = failed if isinstance(failed, int) and failed > 0 else 0
        if serials & short or len(serials & kept) < max(1, len(serials) - failed):
            return False
    return True


def _rcvbuf(drops: dict | None, roster: list) -> int | None:
    """סכום `RcvbufErrors` של מחשבי השיכפול בגל (‏`record_wave`), או ‏None:
    מכונה אחת שלא דיווחה, או דיווחה "לא נקרא", היא "לא נקרא" לכל הגל —
    סכום חלקי היה נקרא כ"אפס זריקות" (עיקרון 5)."""
    macs = {d.get("mac") for d in roster if isinstance(d, dict)}
    if not macs or not isinstance(drops, dict):
        return None
    total = 0
    for mac in macs:
        seen = drops.get(mac)
        got = seen.get("rcvbuf") if isinstance(seen, dict) else None
        if not isinstance(got, int) or isinstance(got, bool) or got < 0:
            return None
        total += got
    return total


def _next_from(conn: sqlite3.Connection, session_id: str, past: dict, stream: dict,
               rate: int, roster: list, drops: dict | None = None) -> tuple[int, str, dict]:
    """(הקצב שהגל הבא יורש מהגל הזה, ‏basis, ‏previous) — לפי מה שהגל הזה
    עצמו הוכיח: ‏`up`/‏`down`/‏`held`/‏`unmeasured` (ראו `choose`).

    ‏הכרעת נדב 17/10: עד כאן עלייה דרשה `loss == 0` **בדיוק**, ומחשב שיכפול
    שזורק מעט בכל קצב (i5-650: ‏600→450→337→252) לא עלה לעולם. מעכשיו —
    שידורים חוזרים עד הסף **וגם** `RcvbufErrors` של כל המקלטים יחד עד אותו
    סף (‏`drops`, ‏`record_wave`); מקלט שלא נקרא — לא עולים."""
    loss, sent = stream.get("loss_blocks"), stream.get("sent_bytes")
    bps, secs = stream.get("stream_bps"), stream.get("stream_s")
    previous = _summary(session_id, past, stream, roster, drops)
    rcvbuf = previous["rcvbuf"]
    if not isinstance(loss, int) or not isinstance(sent, int) or sent <= 0:
        return rate, "unmeasured", previous         # עיקרון 5: לא נמדד ≠ נקי
    budget = LOSS_DOWN_RATIO * -(-sent // UDPCAST_BLOCK)
    if loss > budget:
        return int(rate * DOWN_FACTOR), "down", previous
    if rcvbuf is None:
        return rate, "unmeasured", previous         # מקלט שלא נקרא אינו מקלט נקי
    # אותו תקציב כמו השידורים החוזרים (ses_54a923dd, 17/10: ‏rcvbuf=189 על ~13M
    # בלוקים — "אפס בדיוק" היה מחזיר את הספירלה). datagram של udpcast = בלוק.
    if rcvbuf > budget or not _all_kept_up(conn, session_id, roster, drops):
        return rate, "held", previous
    # התנאי השני לעלייה: הזרם עצמו רץ בקצב הגל. ‏`bytes_written` של כונן
    # נספר אחרי פריסת הדחיסה — כונן שהאט את הזרם עלול להיראות "עמד".
    if (not isinstance(bps, int) or not isinstance(secs, (int, float))
            or secs < MIN_SAMPLE_S):
        return rate, "unmeasured", previous
    if bps < KEEP_UP * rate / 8:
        return rate, "held", previous
    return rate + STEP_UP, "up", previous


def _summary(session_id: str, past: dict, stream: dict, roster: list,
             drops: dict | None) -> dict:
    """‏`previous` — מה שהגל ההוא ספר (למסך וליומן)."""
    return {"session_id": session_id, "max_bitrate": past["max_bitrate"],
            "loss_blocks": stream.get("loss_blocks"), "sent_bytes": stream.get("sent_bytes"),
            "stream_bps": stream.get("stream_bps"), "rcvbuf": _rcvbuf(drops, roster)}


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _point(session_id: str, past: dict, rate: int, stream: dict,
           delivered: dict | None, mac: str) -> dict | None:
    """נקודת goodput אחת של המכונה `mac` בגל אחד (‏#1758), או ‏None — לא נמדד.

    ‏goodput = הבייטים שהמגירות של המכונה כתבו בפועל (‏`delivered`, מגירות
    `done` בלבד), חלקי מספר המגירות שלה בגל, חלקי `wall_s` — משך הזרם
    בשעון. מגירה שלא סיימה תורמת אפס: הבייטים שלה לא הגיעו. ‏`eff` = ה-goodput
    חלקי הקצב שהגל רץ בו (‏`max_bitrate / 8`) — האם **הקצב** הוא שהגביל.

    שולח **שנכשל** עם שידורים חוזרים מעל LOSS_DOWN_RATIO הוא נקודה של goodput
    אפס ו-`lost` — ההגנה; שולח שנכשל או נעצר בלעדיהם אינו ראיה על הקצב.
    ‏**‏#1761:** גל שבו udp-sender ניתק מקלט (`dropped_clients` > 0) — נקודה של
    goodput אפס ו-`lost`, בכל סיום. ‏`dropped` / ‏`timeouts` בנקודה — ‏None כשיומן
    השולח לא נקרא ("לא נבדק").
    מונה שלא נקרא, זרם קצר מ-MIN_SAMPLE_S, פחות מ-MIN_SAMPLE_BYTES, מכונה
    שלא דיווחה — ‏None (עיקרון 5: לא נמדד ≠ איטי)."""
    sent, loss = stream.get("sent_bytes"), stream.get("loss_blocks")
    if not _count(sent) or sent <= 0 or not _count(loss):
        return None
    dropped, timeouts = stream.get("dropped_clients"), stream.get("timeouts")
    base = {"session_id": session_id, "max_bitrate": past.get("max_bitrate"),
            "rate": rate, "loss": loss / -(-sent // UDPCAST_BLOCK), "sent": sent,
            "dropped": dropped if _count(dropped) else None,
            "timeouts": timeouts if _count(timeouts) else None}
    if base["dropped"]:                 # ‏#1761: udp-sender ניתק מקלט בקצב הזה
        return {**base, "goodput_bps": 0, "eff": 0.0, "lost": True}
    if stream.get("ended") == "send_failed":
        return ({**base, "goodput_bps": 0, "eff": 0.0, "lost": True}
                if base["loss"] > LOSS_DOWN_RATIO else None)
    wall, got = stream.get("wall_s"), (delivered or {}).get(mac)
    if (not isinstance(wall, (int, float)) or isinstance(wall, bool) or wall < MIN_SAMPLE_S
            or sent < MIN_SAMPLE_BYTES or not isinstance(got, dict)):
        return None
    drawers, done, written = got.get("drawers"), got.get("done"), got.get("written_bytes")
    if not (_count(drawers) and drawers > 0 and _count(done) and done <= drawers
            and _count(written)):
        return None
    goodput = int(written / drawers / wall)
    return {**base, "goodput_bps": goodput, "eff": goodput * 8 / rate,
            "lost": done < drawers}


def _unsafe(point: dict) -> bool:
    """ההגנה (‏#1758): מגירה אבדה בגל — או השולח נכשל — עם שידורים חוזרים
    מעל LOSS_DOWN_RATIO. זה הנזק היחיד של אבדן שאינו כבר בתוך הזמן: מגירה
    שצריך לכתוב מחדש היא גל שלם נוסף. ‏#1761: או ש-udp-sender ניתק מקלט —
    האות הישיר (‏senddata.c: ניתוק רק כש-rxmitId עובר את retries-until-drop)."""
    return bool(point.get("dropped")) or (point["lost"] and point["loss"] > LOSS_DOWN_RATIO)


def _climb(points: list[dict], unsafe: list[int] = ()) -> tuple[int, str, dict]:
    """(הקצב הבא, ‏basis, השיא) — חיפוש שיא על goodput (‏#1758).

    ‏`points` — הגלים האחרונים של המכונה, מהחדש לישן; לכל קצב נחשבת המדידה
    החדשה שלו. **השיא** — עוברים מהקצב הנמוך לגבוה, וקצב גבוה יותר מחליף רק
    כשה-goodput שלו עולה ביותר מ-GOODPUT_NOISE: אותו זמן בקצב גבוה יותר אינו
    סיבה לעומס נוסף. ואז:

        השיא לא רץ בקצב שלו (‏eff < KEEP_UP):
            אין מתחתיו קצב שנוסה → ‏×DOWN_FACTOR (‏`back`); יש → נשארים (‏`peak`)
        בשיא אבדה מגירה עם אבדן, או שידורים חוזרים מעל LOSS_CLIMB_MAX, או
        ש-udp-sender כתב בו `Timeout notAnswered` מעל TIMEOUTS_PER_GB_HELD לכל
        GB שנשלח (‏#1761), או שקצב לא-בטוח
        שנזכר (`unsafe`) אינו מעליו → ‏`held`
        הקצב הקרוב מעליו — בחלון או בזיכרון — לא בטוח → נשארים (‏`peak`)
        אין מעליו קצב שנוסה → ‏+STEP_UP (‏`climb`)
        מעליו קצב שלא היה מהיר יותר, בטוח, בפער ≥ 2×REFINE_STEP → באמצע (‏`climb`)
        אחרת → נשארים (‏`peak`)
        ‏`climb` כשיומן השולח של השיא לא נקרא → נשארים (‏`unmeasured`, עיקרון 5)

    נקודה יוצאת מהחלון אחרי GOODPUT_WINDOW גלים — ולכן קצב שלא היה מהיר יותר נבדק
    שוב מדי כמה גלים: המגירות, האימג' והעומס משתנים. **קצב לא-בטוח אינו
    נשכח** (‏#1761): ‏`unsafe` — הקצבים מ-`unsafe_rates` (`_remember_unsafe`)."""
    newest: dict[int, dict] = {}
    for point in points:
        newest.setdefault(point["rate"], point)
    tried = sorted(newest.values(), key=lambda p: p["rate"])
    best = tried[0]
    for point in tried[1:]:
        if point["goodput_bps"] > best["goodput_bps"] * (1 + GOODPUT_NOISE):
            best = point
    rate = best["rate"]
    above = [p for p in tried if p["rate"] > rate]
    if best["eff"] < KEEP_UP:
        if any(p["rate"] < rate for p in tried):
            return rate, "peak", best
        return int(rate * DOWN_FACTOR), "back", best
    timeouts = best.get("timeouts")
    if (_unsafe(best) or best["loss"] > LOSS_CLIMB_MAX
            or (timeouts is not None and timeouts * 10 ** 9 / best["sent"] > TIMEOUTS_PER_GB_HELD)
            or any(r <= rate for r in unsafe)):
        return rate, "held", best
    wall = min((r for r in unsafe if r > rate), default=None)
    if wall is not None and (not above or wall <= above[0]["rate"]):
        return rate, "peak", best                  # הקצב הקרוב מעליו לא בטוח
    if not above:
        bits = rate + STEP_UP
    elif not _unsafe(above[0]) and above[0]["rate"] - rate >= 2 * REFINE_STEP:
        bits = (rate + above[0]["rate"]) // 2
    else:
        return rate, "peak", best
    if best.get("timeouts") is None or best.get("dropped") is None:
        return rate, "unmeasured", best            # יומן השולח לא נקרא ≠ נקי
    return bits, "climb", best


def _pick(counts: dict[int, tuple], want: int) -> tuple[int, tuple]:
    """(מספר המגירות, הגל) שממנו מכונה עם `want` מגירות יורשת (הכרעת נדב
    17/10): אותו מספר; ובלעדיו — הקרוב, ובתיקו **היותר** מגירות. קצב שנלמד
    תחת יותר עומס בטוח לפחות עומס; ההפך מוקטן ב-`_from_previous` (‏#1842)."""
    drawers = min(counts, key=lambda c: (abs(c - want), -c))
    return drawers, counts[drawers]


def _from_previous(conn: sqlite3.Connection, drives: list[dict], start: int,
                   exclude: str | None) -> tuple[int, str, dict | None]:
    """(הקצב לפני הכוננים והגבולות, ‏basis, הגל הקודם) — **לפי מחשבי השיכפול**.

    ‏#1471, הכרעת נדב 16/10: בחדר מחליפים מגירות בכל גל, ולכן "הגל האחרון
    שכל הכוננים האלה היו בו" כמעט לא קיים, וכמעט כל גל יצא בפתיחה (60%).
    הזיכרון הוא לכל **מכונה** (‏MAC — מכונה שהוחלפה היא MAC חדש):

        לכל MAC בגל: הגל האחרון (לא ישיר, עם קצב) שהמכונה הייתה ב-`roster`
                     שלו, והקצב שהגל ההוא מוריש (`_next_from`: ‏+STEP_UP על
                     גל נקי, ×DOWN_FACTOR על שידורים חוזרים, אחרת כמות שהוא)
        קצב = המינימום על המכונות; ‏`previous` = הגל של המכונה שקבעה, ו-`mac` שלה
        מכונה אחת בלי גל כזה (או כונן בלי MAC) → **כל הגל** בפתיחה

    ‏**ולפי מספר המגירות שלה בגל** (הכרעת נדב 17/10): המעבד של המקלט עובד
    לכל מגירה (‏zstd ו-partclone לכל אחת), וקצב שנלמד עם שלוש מגירות אינו
    מעניש גל של אחת. "הגל האחרון" הוא האחרון עם אותו מספר מגירות למכונה;
    בלעדיו — הקרוב (`_pick`). ‏`previous.drawers` — כמה מגירות היו למכונה
    שקבעה בגל ההוא.

    ‏**מגל עם פחות מגירות — הקצב מוקטן לפי מספר המגירות** (‏#1842):

        קצב = min(הקצב שהגל ההוא מוריש,
                  הקצב שהוכח × DOWN_FACTOR ^ log2(מגירות עכשיו / מגירות אז))

    (‏`basis: "drawers"`) — ‏×0.75 **לכל הכפלה** של מספר המגירות (2→4: ‏×0.75;
    1→4: ‏×0.5625; 1→3: ‏×0.634). **הכרעת נדב 20/10**, על המדידה מברזל 19/10,
    משכפל 2, ‏19.5GB: סבב של 4 מגירות ב-432m — הקצב של 2 מגירות (‏`held`,
    ‏ses_5002758a) כמות שהוא — ‏963k שידורים חוזרים, ‏623 פקיעות, ‏10:27;
    הסבב הבא של 4, אחרי שהכלל ירד ×0.75 ל-324m — ‏437k, ‏78 פקיעות, ‏9:18.
    ‏432 × 0.75 = 324m: הגל הראשון של 4 יוצא בקצב שהברזל הוכיח, בלי הגל הרע
    שלפניו. (נשקל גם יחס ישר — מגירות אז / עכשיו, ‏216m — ונדחה: גל ראשון
    איטי מ-324m שנמדד.) ממנו החיפוש (`_climb`) ממשיך על הזיכרון של 4.
    עד #1842 — "לכל היותר הקצב שהוכח", כלומר 432m כמות שהוא על 4 מגירות.
    ומקצב שנגזר כך שנרשם כלא-בטוח (‏`unsafe_rates`) למכונה עם מספר מגירות
    כזה או קטן ממנו — מתחתיו, ‏×DOWN_FACTOR: אין לנגזרת ראיה משלה.
    מגל עם **יותר** מגירות — כמות שהוא (קצב שעמד ביותר עומס בטוח לפחות).

    ‏**ולפי goodput** (‏#1758): מאותו מספר מגירות נלקחים עד GOODPUT_WINDOW הגלים
    האחרונים, ומהם נקודות goodput (`_point`) — וחיפוש השיא (`_climb`) קובע.
    ‏`previous` הוא עדיין הגל האחרון של המכונה, ועליו גם `rule: "goodput"`,
    ‏`wall_s`, ‏`goodput_bps` (שלו) ו-`best` (השיא). בלי אף נקודה — כל הגלים
    מלפני #1758 — הכלל הקודם על הגל האחרון (`_next_from`). "הקצב שהוכח"
    למעלה — השיא, או הגל ההוא בכלל הקודם.

    הגל יוצא, נמדד, ובפעם הבאה המכונה החדשה כבר מוכרת. ההגנות לכל כונן
    אינן משתנות: הכונן האיטי המוכר (`_limit_of`) מוריד אחרי זה, בדיקת
    הכתיבה בסוכן פוסלת מגירה איטית, ו"עלייה" עדיין דורשת שכל כונן של הגל
    הקודם עמד בקצב והזרם רץ בו (`_next_from`)."""
    want = Counter(d.get("mac") for d in drives)
    if not want or None in want:
        return start, "start", None
    # mac → {מגירות: עד GOODPUT_WINDOW הגלים האחרונים, מהחדש לישן}
    seen: dict[str, dict[int, list[tuple]]] = {}
    for row in conn.execute(
            "SELECT session_id, decision, stream, drops, delivered FROM wave_rates"
            " WHERE session_id IS NOT ? ORDER BY rowid DESC LIMIT ?",
            (exclude, MEMORY_SCAN)):
        try:
            past = json.loads(row["decision"])
            stream = json.loads(row["stream"]) if row["stream"] else {}
            drops = json.loads(row["drops"]) if row["drops"] else None
            delivered = json.loads(row["delivered"]) if row["delivered"] else None
        except (TypeError, ValueError):
            continue
        roster = past.get("roster") or []
        rate = parse_bitrate(past.get("max_bitrate"))
        if past.get("direct") or rate is None:
            continue
        # ‏#1884: גל שהשרת היה צוואר הבקבוק בו (דיסק הספרייה) אינו ראיה על המשכפלים.
        if server_io.bound(conn, row["session_id"]):
            continue
        had = Counter(d.get("mac") for d in roster if isinstance(d, dict))
        for mac in want.keys() & had.keys():
            # ‏#1885: גל שבו המעבד של המכונה הואט (throttle) אינו ראיה על הקצב שלה.
            if isinstance(delivered, dict) and cloner_cpu.throttled((delivered.get(mac) or {}).get("cpu")):
                continue
            waves = seen.setdefault(mac, {}).setdefault(had[mac], [])
            if len(waves) < GOODPUT_WINDOW:
                waves.append((row["session_id"], past, stream, rate, roster, drops,
                              delivered if isinstance(delivered, dict) else None))
    if seen.keys() < want.keys():
        return start, "start", None                 # מכונה שלא נראתה — פתיחה
    found: dict[str, tuple[int, str, dict]] = {}
    for mac, counts in seen.items():
        drawers, waves = _pick(counts, want[mac])
        session_id, past, stream, rate, roster, drops, _ = waves[0]
        points = [p for w in waves if (p := _point(w[0], w[1], w[3], w[2], w[6], mac))]
        unsafe = [r["rate_bps"] for r in conn.execute(
            "SELECT rate_bps FROM unsafe_rates WHERE mac = ? AND drawers <= ?",
            (mac, max(drawers, want[mac])))]
        if points:                                   # ‏#1758: goodput
            bits, basis, best = _climb(points, unsafe)
            mine = next((p for p in points if p["session_id"] == session_id), None)
            previous = {**_summary(session_id, past, stream, roster, drops),
                        "rule": "goodput", "wall_s": stream.get("wall_s"),
                        "goodput_bps": mine["goodput_bps"] if mine else None,
                        "best": {key: best[key] for key in
                                 ("session_id", "max_bitrate", "goodput_bps")}
                                | {"eff": round(best["eff"], 3)}}
            proven = best["rate"]
        else:                                        # גלים מלפני #1758
            bits, basis, previous = _next_from(conn, session_id, past, stream, rate,
                                               roster, drops)
            proven = rate
        if drawers < want[mac]:                      # ‏#1842: מפחות מגירות
            scaled = int(proven * DOWN_FACTOR ** math.log2(want[mac] / drawers))
            if bits > scaled:
                bits, basis = scaled, "drawers"
            wall = min((r for r in unsafe if r <= bits), default=None)
            if wall is not None:
                bits, basis = int(wall * DOWN_FACTOR), "drawers"
        found[mac] = bits, basis, {**previous, "drawers": drawers}
    mac, (bits, basis, previous) = min(found.items(), key=lambda kv: (kv[1][0], kv[0]))
    return bits, basis, {**previous, "mac": mac}


def choose(conn: sqlite3.Connection, drives: list[dict],
           ceiling_raw: str | None, session_id: str | None = None,
           direct: bool = False, link: dict | None = None) -> dict:
    """הקצב לגל של `drives` (‏`serial`, ‏`model`, ‏`mac`, ‏`port`) — קריאה בלבד.

    ‏`max_bitrate` הוא מה שעובר ל-udp-sender, ו-`basis` אומר למה: ‏`start`
    (מכונה בגל שאין לה גל קודם — #1471) · ‏`climb` / ‏`peak` / ‏`back` (‏#1758,
    חיפוש השיא על goodput — `_climb`; ‏`previous.best` = השיא) · ‏`up` (הגל הקודם של המכונה שקבעה
    נקי — ב-`previous`, עם ה-`mac` שלה) · ‏`drawers` (‏#1842 — נגזר מגל עם
    פחות מגירות למכונה, מוקטן לפי מספר המגירות; `_from_previous`) ·
    ‏`down` (שידורים חוזרים בגל הקודם) · ‏`held` (הגל הקודם לא הוכיח שאפשר
    לעלות) · ‏`unmeasured` (המונה של הגל הקודם לא נקרא — נשאר) · ‏`learned`
    (כונן מוכר הוריד, והוא ב-`by`) · ‏`ceiling` (הגג עצר) · ‏`disabled` (המתג
    כבוי — הפתיחה, קבועה) · ‏`bad_ceiling` (התקרה אינה מספר; עוברת כמות
    שהיא). ‏`session_id` — הגל שבוחרים לו; הוא אינו "הגל הקודם" של עצמו.

    ‏`direct` — מחשב בנייה משדר (#715): אותה פתיחה, אותו גג ואותה ירידה
    לכונן איטי מוכר — אבל **בלי זיכרון ובלי עלייה**. המונה של udp-sender
    נמצא במחשב הבנייה ולא בשרת, ובלעדיו אין ראיה לגל נקי (עיקרון 5); וגל
    כזה אינו "הגל הקודם" של אף גל ואינו מלמד את הכוננים (`record_wave`) —
    המקור עצמו (דיסק חי + דחיסה) יכול להיות האיטי, והייחוס לכוננים היה שקר.

    ‏`link` — מה ש-`read_link` קרא על כרטיס ההפצה (הכרעת נדב 15/10): גג
    ופתיחה לפי מהירות הקישור (`LINK_CEILING_PER_MBPS`). מהירות שלא נקראה —
    600m/900m של היום, והסיבה ב-`link.why`. **לא** לגל ישיר: הזרם יוצא
    מהכרטיס של מחשב הבנייה, לא מזה של השרת.
    """
    ceiling = parse_bitrate(ceiling_raw) if ceiling_raw else TOP_BITRATE
    configured = ceiling if ceiling_raw else None
    server = None if direct or ceiling is None else _link_bounds(link)
    # ‏15/10 (ב): המקבלים — מחשב השיכפול האיטי בגל (ידני / מדווח / הנחת 1G).
    # גם בגל ישיר: המקבלים אותם מקבלים.
    cloner = (nic_speed.slowest(conn, [d.get("mac") for d in drives])
              if ceiling is not None else None)
    receivers = _link_bounds(cloner)
    # שרת שלא נקרא — הגג של היום (‏`IMAGESRV_MAX_BITRATE` או 900m) והפתיחה 600m.
    base = server or (ceiling, START_BITRATE)
    bounds = server
    if receivers is not None:
        bounds = (min(base[0], receivers[0]), min(base[1], receivers[1]))
    start_at = START_BITRATE
    if bounds is not None:
        # ‏`IMAGESRV_MAX_BITRATE` שהוגדר נשאר גג — הנמוך מבין השניים.
        ceiling = min(ceiling, bounds[0]) if ceiling_raw else bounds[0]
        start_at = bounds[1]
    # מי קבע את התקרה — למסך. ‏None = 900m של היום (שום דבר לא נקרא).
    if configured is not None and (bounds is None or configured <= bounds[0]):
        cap_by = "configured"
    elif receivers is not None and receivers[0] <= base[0] \
            and (server is None or receivers[0] < server[0]):
        cap_by = "cloner"
    else:
        cap_by = "server" if server is not None else None
    result = {"max_bitrate": ceiling_raw or None,
              "ceiling": _fmt(ceiling) if bounds else ceiling_raw or _fmt(TOP_BITRATE),
              "start": None, "basis": "start", "by": None, "previous": None,
              "drives": len(drives), "unknown_drives": 0, "cap_by": cap_by,
              **({"direct": True} if direct else {}),
              **({"link": {**link, "applied": server is not None}} if link else {}),
              **({"cloner": cloner} if cloner else {})}
    if ceiling is None:
        return {**result, "basis": "bad_ceiling"}
    start = min(start_at, ceiling)
    result = {**result, "max_bitrate": _fmt(start), "start": _fmt(start)}
    if not enabled(conn):
        return {**result, "basis": "disabled"}
    bits, basis, previous = ((start, "start", None) if direct
                             else _from_previous(conn, drives, start, session_id))
    slowest, limit, rate = None, None, None
    for drive in drives:
        got, measured, known = _limit_of(conn, drive["serial"], drive.get("model"))
        if not known:
            result["unknown_drives"] += 1
        if got is not None and (limit is None or got < limit):
            slowest, limit, rate = drive, got, measured
    by = None
    if slowest is not None and limit < bits:
        bits, basis = limit, "learned"
        by = {"serial": slowest["serial"], "model": slowest.get("model"),
              "mac": slowest.get("mac"), "port": slowest.get("port"),
              "rate_bps": rate}
    if bits > ceiling:
        bits, basis, by = ceiling, "ceiling", None
    # הרצפה מקצב הפתיחה **של 1GbE לכל היותר** (150m): כרטיס 10G של השרת אינו
    # אומר שהמקבלים ב-10G, ורצפה של 1500m הייתה נועלת חדר של מקבלים ב-1G
    # מעל מה שהם מסוגלים לקבל.
    bits = max(bits, int(min(start, START_BITRATE) * FLOOR_RATIO))
    return {**result, "max_bitrate": _fmt(bits), "basis": basis, "by": by,
            "previous": previous}


def decision(conn: sqlite3.Connection, session_id: str | None) -> dict | None:
    """מה שנבחר לגל כשיצא; ‏None = לא נבחר (גל שהשרת אינו משדר, או ישן)."""
    if not session_id:
        return None
    row = conn.execute("SELECT decision FROM wave_rates WHERE session_id = ?",
                       (session_id,)).fetchone()
    try:
        return json.loads(row["decision"]) if row else None
    except (TypeError, ValueError):
        return None


def decide(conn: sqlite3.Connection, session_id: str,
           drives: Callable[[], list[dict]], ceiling_raw: str | None,
           direct: bool = False,
           link: Callable[[], dict] | None = None) -> str | None:
    """נקרא כשהגל יוצא: בוחר, שומר, רושם ביומן, ומחזיר את `--max-bitrate`.
    ‏`direct` — המשדר הוא מחשב בנייה (#715, ‏`app.on_running`), ראו `choose`.
    ‏`link` — קורא את כרטיס ההפצה (`SenderEngine.link`); נקרא פעם אחת לגל.

    לעולם אינו זורק: כשל כאן הוא "לא נלמד" — נרשם ביומן, והגל יוצא בקצב
    הפתיחה. הלמידה אינה סיבה שהשידור לא ייצא.
    """
    try:
        wave = drives()
        chosen = choose(conn, wave, ceiling_raw, session_id, direct=direct,
                        link=link() if link is not None else None)
        # הכוננים שהגל יצא אליהם, כפי שהשרת ראה אותם **לפני** הגל — רק מהם
        # נלמד בסופו (`record_wave`), ולא ממה שמדווח בסוף.
        stored = {**chosen, "roster": [
            {"mac": d.get("mac"), "serial": d["serial"], "model": d.get("model")}
            for d in wave]}
        with _write_lock, writing(conn):
            conn.execute(
                "INSERT OR REPLACE INTO wave_rates (session_id, decision, at)"
                " VALUES (?, ?, ?)",
                (session_id, json.dumps(stored, ensure_ascii=False), now_iso()))
        by, previous, nic = chosen["by"], chosen["previous"], chosen.get("link")
        journal(conn, "wave_rate",
                f"{session_id} {chosen['max_bitrate']}"
                f" basis={_basis_note(chosen['basis'], previous)}"
                + (f" by={by['model'] or '?'} {by['serial']}" if by else "")
                + (f" from={previous['session_id']} {previous['max_bitrate']}"
                   f" rexmits={previous['loss_blocks']} mac={previous.get('mac')}"
                   f" drawers={previous.get('drawers')}"
                   f"/{sum(d.get('mac') == previous.get('mac') for d in wave)}"
                   if previous else "")
                + (f" link={nic['interface']}"
                   f" {str(nic['speed_mbps']) + 'Mb/s' if nic['applied'] else nic['why'] or 'unused'}"
                   f" ceiling={chosen['ceiling']}" if nic else "")
                + _cloner_note(chosen.get("cloner"), chosen.get("cap_by")))
        return chosen["max_bitrate"]
    except Exception as exc:      # noqa: BLE001 — הלמידה אינה מפילה גל
        log.exception("wave rate for %s failed; using the start rate", session_id)
        _failed(conn, f"{session_id} decide — {exc}")
        return _fallback(ceiling_raw)


def _basis_note(basis: str, previous: dict | None) -> str:
    """ליומן (הכרעת נדב 17/10): הבסיס, ואם נגזר מהגל הקודם — על מה:
    ‏`up(loss=0.04%,rcvbuf=0)`. ‏`?` = המונה של השולח לא נקרא, ‏`rcvbuf=None`
    = מקלט שלא דיווח. ‏#1758 — כשהבסיס הוא goodput, גם השיא ומה שהגל הקודם
    עצמו עשה: ‏`climb(best=482m:52.1MB/s,eff=86%,goodput=52.1MB/s,loss=1.47%,rcvbuf=198228)`."""
    if not previous or basis not in ("up", "held", "down", "unmeasured",
                                     "climb", "peak", "back", "drawers"):
        return basis
    loss, sent = previous.get("loss_blocks"), previous.get("sent_bytes")
    pct = (f"{100 * loss / -(-sent // UDPCAST_BLOCK):.2f}%"
           if isinstance(loss, int) and isinstance(sent, int) and sent > 0 else "?")
    tail = f"loss={pct},rcvbuf={previous.get('rcvbuf')}"
    best = previous.get("best") if previous.get("rule") == "goodput" else None
    if not best:
        return f"{basis}({tail})"
    mine = previous.get("goodput_bps")
    return (f"{basis}(best={best['max_bitrate']}:{best['goodput_bps'] / 1e6:.1f}MB/s"
            f",eff={100 * best['eff']:.0f}%"
            f",goodput={'?' if mine is None else f'{mine / 1e6:.1f}MB/s'},{tail})")


def _cloner_note(cloner: dict | None, cap_by: str | None) -> str:
    """ליומן: מחשב השיכפול האיטי ומאיפה המהירות — ‏`assumed_1g(<why>)` כשהונחה."""
    if not cloner:
        return ""
    source = (f"assumed_1g({cloner['why']})" if cloner["source"] == "assumed"
              else cloner["source"])
    return (f" cloner={cloner['mac']} {cloner['speed_mbps']}Mb/s {source}"
            f" assumed={len(cloner['assumed'])} cap_by={cap_by}")


def direct_bitrate(conn: sqlite3.Connection, session_id: str | None,
                   ceiling_raw: str | None) -> str:
    """‏`--max-bitrate` למחשב בנייה שמשדר (#715, ‏`task.direct.multicast`):
    מה שנבחר לגל כשיצא (`app.on_running`); לפני כן, או בלי בחירה — קצב
    הפתיחה. לעולם לא "בלי תקרה": בלי `IMAGESRV_MAX_BITRATE` — ‏600m."""
    try:
        chosen = (decision(conn, session_id) or {}).get("max_bitrate")
    except Exception:             # noqa: BLE001 — קצב הפתיחה, לא כישלון ה-hello
        log.exception("direct bitrate for %s failed", session_id)
        chosen = None
    return chosen if isinstance(chosen, str) and chosen else _fallback(ceiling_raw)


def _fallback(ceiling_raw: str | None) -> str:
    """קצב הפתיחה בלי לגעת ב-DB — מה שהגל יוצא בו כשהבחירה עצמה נכשלה."""
    ceiling = parse_bitrate(ceiling_raw) if ceiling_raw else TOP_BITRATE
    return ceiling_raw if ceiling is None else _fmt(min(START_BITRATE, ceiling))


def record_stream(conn: sqlite3.Connection, session_id: str, outcome: dict) -> None:
    """נקרא כשהשולח סיים את הגל (הצליח, נכשל או נעצר): מה ש-udp-sender
    עצמו ספר — ‏`sent_bytes` ו-`loss_blocks` (‏`SenderEngine.wave_outcome`),
    ‏`wall_s` — משך הזרם בשעון (‏#1758), ‏`ended` — האירוע שסגר אותו
    (‏`send_done` / ‏`send_failed` / ‏`send_stopped`, ‏`app.on_sender_event`),
    ו-`dropped_clients` / ‏`timeouts` — שורות היומן של udp-sender (‏#1761,
    ‏`sender.wave_events`). באותה כתיבה — `_remember_unsafe`.

    ‏`None` נשמר כמות שהוא — "לא נמדד", שאינו "אפס שידורים חוזרים". גל שלא
    נבחר לו קצב (אין שורה ב-`wave_rates`) — אין מה לעדכן. לעולם אינו זורק.
    """
    try:
        stream = {key: outcome.get(key) for key in
                  ("sent_bytes", "loss_blocks", "stream_bps", "stream_s",
                   "wall_s", "ended", "dropped_clients", "timeouts")}
        with _write_lock, writing(conn):
            conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = ?",
                         (json.dumps(stream), session_id))
            _remember_unsafe(conn, session_id)
    except Exception as exc:      # noqa: BLE001 — הזיכרון אינו מפיל את השולח
        log.exception("recording the stream of %s failed", session_id)
        _failed(conn, f"{session_id} stream — {exc}")


def _remember_unsafe(conn: sqlite3.Connection, session_id: str) -> None:
    """‏#1761: קצב שהיה לא בטוח (`_unsafe`) למכונה בגל `session_id` נרשם
    ב-`unsafe_rates` — ולא נשכח כשהגל יוצא מחלון ה-goodput. נקרא מתוך
    הכתיבה של `record_stream` ושל `record_wave`: מי שמגיע שני רואה את שני
    החצאים (ניתוק נראה כבר מהשולח לבד). חוזר על עצמו בבטחה."""
    row = conn.execute("SELECT decision, stream, delivered FROM wave_rates"
                       " WHERE session_id = ?", (session_id,)).fetchone()
    if row is None or not row["stream"]:
        return
    past = json.loads(row["decision"])
    rate = parse_bitrate(past.get("max_bitrate"))
    if past.get("direct") or rate is None:
        return
    stream = json.loads(row["stream"])
    delivered = json.loads(row["delivered"]) if row["delivered"] else None
    had = Counter(d.get("mac") for d in past.get("roster") or [] if isinstance(d, dict))
    for mac, drawers in had.items():
        point = _point(session_id, past, rate, stream,
                       delivered if isinstance(delivered, dict) else None, mac)
        if mac and point and _unsafe(point):
            conn.execute("INSERT OR REPLACE INTO unsafe_rates (mac, drawers, rate_bps,"
                         " session_id, at) VALUES (?,?,?,?,?)",
                         (mac, drawers, rate, session_id, now_iso()))


def _seconds(since: object, until: object) -> float | None:
    try:
        return (datetime.fromisoformat(until) - datetime.fromisoformat(since)).total_seconds()
    except (TypeError, ValueError):
        return None


def record_wave(conn: sqlite3.Connection, session_id: str, members) -> int:
    """כל מגירה שסיימה (`done`) בגל משאירה את הקצב שכתבה. מחזיר כמה נרשמו.

    הקצב = ‏`bytes_written` חלקי הזמן בין התנועה הראשונה לאחרונה של המונה
    (‏`first_moved_at` → ‏`moved_at`, ‏`reports.stamp_movement`) — בלי ההמתנה
    למקבלים לפני הבייט הראשון. חוזר על עצמו בבטחה (שורה לכל גל+סידורי),
    ולעולם אינו זורק: כשל כאן נרשם ביומן ואינו עוצר את סיום הגל. באותה
    כתיבה — ‏`wave_rates.drops`: ‏`udp_drops` של כל מכונה ברשימת הגל, שממנו
    הגל הבא יודע אם המקלטים נקיים (`_next_from`); ו-`wave_rates.delivered`
    (‏#1758): לכל מכונה ברשימת הגל — כמה מגירות היו לה, כמה `done`, וסכום
    ה-`bytes_written` שלהן; מכונה שלא דיווחה — ‏None. המונה של goodput (`_point`).
    """
    try:
        decided = decision(conn, session_id) or {}
        cap = parse_bitrate(decided.get("max_bitrate"))
        if cap is None or decided.get("direct"):
            return 0                  # אין תקרה ידועה, או מקור שאולי הוא האיטי
        roster = {(d.get("mac"), d.get("serial")): d.get("model")
                  for d in decided.get("roster") or [] if isinstance(d, dict)}
        # הכרעת נדב 17/10: מה שכל מקלט דיווח שהקרנל שלו זרק (‏`udp_drops` בדיווח,
        # ‏`reports.ingest`) — לכל מכונה ברשימת הגל; חסר = ‏None, "לא נקרא".
        reported = {member["mac"]: _udp_drops(member) for member in members}
        failed = {member["mac"]: sum(isinstance(t, dict) and t.get("state") == "failed"
                                     for t in json.loads(member["targets_json"] or "[]"))
                  for member in members}
        # ‏rc2: כמה מגירות של המכונה הסוכן דיווח שנכשלו — `_all_kept_up`.
        drops = {mac: ({**reported[mac], "failed_drawers": failed.get(mac, 0)}
                       if reported.get(mac) else None) for mac, _ in roster}
        # ‏#1758: מה שכל מכונה ברשימת הגל כתבה בפועל — המונה של goodput. מכונה
        # שלא דיווחה = ‏None ("לא נמדד"); מגירה שלא `done` — אפס בייטים.
        delivered = {mac: ({"drawers": sum(m == mac for m, _ in roster), "done": 0,
                            "written_bytes": 0}
                           if mac in reported else None) for mac, _ in roster}
        # ‏#1885: מעבד/throttle/טמפ' של כל מכונה בגל (‏`cpu_json`) — `_from_previous`. לא דווח —
        # המפתח חסר (כמו לפני #1885), לא אפס.
        for member in members:
            got, cpu = delivered.get(member["mac"]), _cpu(member)
            if got is not None and cpu is not None:
                got["cpu"] = cpu
        rows = []
        for member in members:
            for t in json.loads(member["targets_json"] or "[]"):
                serial = t.get("serial")
                if (member["mac"], serial) not in roster:
                    continue          # לא היה בגל כשיצא — לא לומדים ממנו
                written = t.get("bytes_written")
                got = delivered.get(member["mac"])
                if t.get("state") == "done" and _count(written) and got is not None:
                    got["done"] += 1
                    got["written_bytes"] += written
                seconds = _seconds(t.get("first_moved_at"), t.get("moved_at"))
                if (t.get("state") != "done" or not serial or seconds is None
                        or seconds < MIN_SAMPLE_S or not isinstance(written, int)
                        or written < MIN_SAMPLE_BYTES):
                    continue
                rows.append((serial, roster[(member["mac"], serial)], session_id,
                             int(written / seconds), cap // 8, written,
                             int(seconds), now_iso()))
        with _write_lock, writing(conn):
            conn.execute("UPDATE wave_rates SET drops = ?, delivered = ? WHERE session_id = ?",
                         (json.dumps(drops), json.dumps(delivered), session_id))
            _remember_unsafe(conn, session_id)
            return sum(conn.execute(
                "INSERT OR IGNORE INTO drive_rates (serial, model, session_id,"
                " rate_bps, cap_bps, bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
                row).rowcount for row in rows)
    except Exception as exc:      # noqa: BLE001 — הזיכרון אינו עוצר סיום גל
        log.exception("recording drive rates of %s failed", session_id)
        _failed(conn, f"{session_id} record — {exc}")
        return 0


def _udp_drops(member) -> dict | None:
    """‏`session_members.udp_drops_json` של חבר, או ‏None — לא דווח."""
    try:
        raw = member["udp_drops_json"]
    except (KeyError, IndexError):
        return None
    try:
        got = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _cpu(member) -> dict | None:
    """‏#1885: ‏`session_members.cpu_json` של חבר, או ‏None — לא דווח."""
    try:
        raw = member["cpu_json"]
    except (KeyError, IndexError):
        return None
    try:
        return cloner_cpu.well_formed(json.loads(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _failed(conn: sqlite3.Connection, detail: str) -> None:
    try:
        journal(conn, "drive_rates_failed", " ".join(detail.split())[:200])
    except Exception:             # noqa: BLE001 — גם היומן אינו מפיל
        log.exception("drive_rates failure was not journaled")
