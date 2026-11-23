"""קצב המולטיקאסט לכל גל — נקבע בשרת לבד (#447 + הכרעת נדב 11/10).

#3173 (נדב 21/11): המנוע הקודם נמחק, ומנוע החתיכות הוא המנוע היחיד. כאן נשארו הגבולות של הגל ("`choose`") והזיכרון של
הכוננים; הקצב שהגל הבא לומד מהגל הקודם הוא של `chunk_rate` (`adjust`, K8). לפני כל גל:

    פתיחה = min(START_BITRATE, גג)    גג = IMAGESRV_MAX_BITRATE, ובלעדיו TOP_BITRATE
    (15/10: כשמהירות כרטיס ההפצה נקראה — גג 90% ממנה ופתיחה 60%; ראו `read_link`;
     ולעולם לא מעל 90%/60% ממחשב השיכפול האיטי בגל — `nic_speed`, הנחת 1G כשלא ידוע)

    קצב = המינימום על **מחשבי השיכפול** בגל (#1471, הכרעת נדב 16/10): לכל מכונה (MAC) — הקצב שהגל
          האחרון שלה **במנוע החתיכות** יצא בו, עם אותו מספר מגירות, ובלעדיו הקרוב (`_pick`); מהקרוב עם
          **פחות** מגירות — × DOWN_FACTOR לכל הכפלה (#1842). מכונה שלא נראתה בגל חתיכות — **כל הגל** בפתיחה.
          הבסיס `unmeasured`: הגל ההוא לא הוכיח כאן דבר, ו-`chunk_rate.adjust` מחליף אותו בנלמד כשיש גל שלימד.
    קצב = min(קצב, הכונן האיטי המוכר × MARGIN)        (#447, למטה)
    קצב = בין FLOOR_RATIO × פתיחה לבין הגג

**שורות ישנות של המנוע הקודם ב-DB משודרג** (#3173): נשארות ב-`wave_rates` וב-`unsafe_rates`, ואינן נקראות להחלטה —
גל שאינו של מנוע החתיכות (`_chunk_wave`) אינו "הגל הקודם" של אף מכונה. המונים של השולח הקודם (שידורים חוזרים,
`Timeout notAnswered`, `Dropping client`, goodput של #1758) יצאו איתו: אין להם מקביל במנוע החתיכות, ולא מומצא כזה.

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
2. **הערכת היכולת היא המקסימום, לא האחרון.** `WINDOW` הגלים האחרונים: דגימה
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
from pathlib import Path
from typing import Callable

from server.agent import cloner_cpu
from server.disks import ata_cause, disk_failures
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
#: מתחת לאיזה חלק מתקרת הגל דגימה נחשבת "הכונן הגביל". השולח הקודם הגיע
#: ל-95.7% מהתקרה בתשלום (191.4Mbit/s מתוך 200m, handoff 06/09); הממוצע
#: של מגירה כולל גם את ההמתנות בין המחיצות, ולכן הסף נמוך מזה.
KEEP_UP = 0.8
#: דגימה קצרה או קטנה מדי היא רעש של התחלה וסוף, לא קצב.
MIN_SAMPLE_BYTES = 10 ** 9
MIN_SAMPLE_S = 60
#: כמה גלים שונים שבהם הכונן הגביל נדרשים לפני שהוא מוריד גל. דגימה אחת —
#: כונן חם, גל חריג, או דיווח כוזב של מכונה אחת — אינה תכונה של כונן.
MIN_HELD_SAMPLES = 2
#: רצפה לקצב, כחלק מקצב הפתיחה (600m → 150m; 200m → 50m). מה שהלמידה
#: יכולה לעשות במקרה הגרוע — דגימות מורעלות, כונן גוסס, מקבל שמבקש שידור
#: חוזר על כל בלוק — חסום כאן, ולא משתק חדר שלם בקצב של 1m.
FLOOR_RATIO = 0.25

#: קצב הפתיחה, בביטים לשנייה: 600m — נמדד על ברזל ב-06/09 (מ.19,
#: `docs/lab/verification-register.md`): 61.06MB/s לשני מקבלים, re-xmits=0.
#: ⚠️ המדידה ההיא הייתה **בלי כתיבה לדיסק** — לכן הוא נקודת פתיחה, לא
#: הבטחה; הירידה על שידורים חוזרים והכוננים האיטיים (#447) הם מה שמגן.
START_BITRATE = 600_000_000
#: הגג כשאין `IMAGESRV_MAX_BITRATE`: קו של 1Gb/s, פחות מקום ל-ACK-ים של
#: המנוע ולשאר התעבורה (השולח הקודם הגיע ל-95.7% מהתקרה, handoff 06/09).
#: `IMAGESRV_MAX_BITRATE`, כשהוגדר, מחליף אותו — כלפי מטה או מעלה.
TOP_BITRATE = 900_000_000
#: #2028: קצב הפתיחה שסבב למידה הוכיח, ב-Mb/s (`server/learn/tuning.py`). חסר = START_BITRATE.
#: אינו ב-`WRITE_SETTINGS`: נכתב רק מלמידה, ומוחזר מ"החזר".
START_SETTING = "learned_start_mbps"
#: צעד העלייה אחרי גל נקי: 600→900 בשלושה גלים נקיים. #3173: נמדד על השולח הקודם; היום קבוע הפתיחה של
#: `chunk_rate` (`STEP_MBPS`), כמו `DOWN_FACTOR` ו-`LOSS_DOWN_RATIO`.
STEP_UP = 100_000_000
#: הירידה אחרי גל עם שידורים חוזרים: רבע מהקצב.
DOWN_FACTOR = 0.75
#: מעל איזה חלק מהבלוקים שידורים חוזרים הורידו במנוע הקודם (#3173: היום `chunk_rate.LOSS_PERMILLE`, וסף
#: האבדן של סבב הלמידה, `tuning`). השולח הקודם הדפיס את
#: היחס הזה בעצמו ברזולוציה של 0.1% — מתחת לסף הוא מדפיס "0.0%". בין
#: אפס לסף הקצב נשאר: לא נקי מספיק כדי לעלות, לא רע מספיק כדי לרדת.
#: ⚠️ ניחוש מנומק, לא מדידה — לכייל על ברזל.
LOSS_DOWN_RATIO = 0.001
#: כמה גלים אחרונים סורקים כדי למצוא את הגל האחרון של כל מכונה (#1471).
MEMORY_SCAN = 50

#: #1758 — הצעד הקטן ביותר בחיפוש השיא של המנוע הקודם (#3173: היום רק העיגול של `learned_start_mbps`, `tuning`),
#: בין קצב טוב לקצב שמעליו שלא היה מהיר יותר. 25m הם 5% מ-482m: ברזל 18/10 הראה שמתחת לשיא הזמן נע עם
#: הקצב (482m→375ש', 361m→476ש'), ולכן 5% הם כ-19 שניות בגל של 375 —
#: מעל רעש ה-goodput (3%). צעד קטן מזה אינו נמדד.
REFINE_STEP = 25_000_000

#: הכרעת נדב 15/10: "הכי מהר שאפשר, והשרת מגביל לפי כרטיס הרשת שלו".
#: כשמהירות הקישור של כרטיס ההפצה נקראה (`read_link`), במקום 600m/900m:
#:     גג    = LINK_CEILING_PER_MBPS × מהירות   (90% — מקום ל-ACK ולשידור חוזר)
#:     פתיחה = LINK_START_PER_MBPS × מהירות    (60% — שני שלישים מהגג)
#: ב-ביטים לשנייה לכל Mb/s. על 1GbE זה **בדיוק** 900m/600m — ההתנהגות של
#: היום; על 10GbE 9000m/6000m. `IMAGESRV_MAX_BITRATE`, כשהוגדר, נשאר גג
#: — הנמוך מבין השניים. הרצפה **אינה** גדלה עם הקישור (`_floor`).
LINK_CEILING_PER_MBPS = TOP_BITRATE // 1000
LINK_START_PER_MBPS = START_BITRATE // 1000
SYS_NET = Path("/sys/class/net")
#: דרייברים של כרטיס וירטואלי: המהירות שלהם היא מה שהמארח **מצהיר**
#: (vmxnet3 תמיד 10000), לא הקו הפיזי — עיקרון 5א. בלי ראיה — ההתנהגות
#: של היום.
VIRTUAL_DRIVERS = frozenset({"vmxnet3", "virtio_net", "hv_netvsc", "xen_netfront"})


def read_link(interface: str | None, sys_net: Path = SYS_NET) -> dict:
    """מהירות הקישור של כרטיס ההפצה מ-`/sys/class/net/<if>/speed`.

    `{"interface", "speed_mbps", "why"}`: `speed_mbps` מספר חיובי ו-`why`
    None — נקרא; אחרת `speed_mbps` None ו-`why` אומר למה (עיקרון 5: "לא
    נקרא" אינו 1GbE): `no_interface` · `absent` (אין כרטיס/קובץ) ·
    `unreadable` (OSError — לינוקס מחזיר EINVAL כשהקישור למטה) · `unknown`
    (-1 או לא מספר) · `virtual` (`VIRTUAL_DRIVERS`). bond — הסלייב
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
    """(גג, פתיחה) בביטים מהקישור; None — המהירות לא נקראה."""
    mbps = (link or {}).get("speed_mbps")
    if not isinstance(mbps, int) or isinstance(mbps, bool) or mbps <= 0:
        return None
    return mbps * LINK_CEILING_PER_MBPS, mbps * LINK_START_PER_MBPS

_BITRATE = re.compile(r"^(\d+(?:\.\d+)?)([kmg]?)$")
_UNIT = {"": 1, "k": 10 ** 3, "m": 10 ** 6, "g": 10 ** 9}


def parse_bitrate(raw: object) -> int | None:
    """קצב בכתיב `600m` (`IMAGESRV_MAX_BITRATE`, `decision.max_bitrate`) בביטים לשנייה; None = אינו מספר חיובי."""
    if not isinstance(raw, str):
        return None
    match = _BITRATE.match(raw.strip().lower())
    if match is None:
        return None
    bits = int(float(match.group(1)) * _UNIT[match.group(2)])
    return bits if bits > 0 else None


def enabled(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SETTING) == "true"


def learned_start(conn: sqlite3.Connection) -> int | None:
    """#2028: קצב הפתיחה שנלמד, בביטים; None — לא נלמד, או ערך פגום (עם שורת יומן),
    ואז הפתיחה נשארת START_BITRATE. למידה רק מעלה את הפתיחה."""
    raw = get_setting(conn, START_SETTING)
    if raw is None:
        return None
    bits = int(raw) * 10 ** 6 if raw.isascii() and raw.isdigit() else None
    if bits is None or bits <= START_BITRATE:
        log.warning("setting %s=%r is not above the start rate -- ignored", START_SETTING, raw)
        return None
    return bits


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


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _pick(counts: dict[int, tuple], want: int) -> tuple[int, tuple]:
    """(מספר המגירות, הגל) שממנו מכונה עם `want` מגירות יורשת (הכרעת נדב
    17/10): אותו מספר; ובלעדיו — הקרוב, ובתיקו **היותר** מגירות. קצב שנלמד
    תחת יותר עומס בטוח לפחות עומס; ההפך מוקטן ב-`_from_previous` (#1842)."""
    drawers = min(counts, key=lambda c: (abs(c - want), -c))
    return drawers, counts[drawers]


def _chunk_wave(past: dict, stream: object) -> bool:
    """#3173: גל של מנוע החתיכות — ההחלטה שלו נרשמה עם `engine` (#3296), או שהמנוע כתב לו `stream.chunk`
    (`chunk_rate.record`; גלים מלפני #3296). שורה אחרת היא גל של המנוע הקודם מ-DB משודרג: נשארת בטבלה ואינה נקראת."""
    return past.get("engine") == "chunk" or (isinstance(stream, dict) and isinstance(stream.get("chunk"), dict))


def _from_previous(conn: sqlite3.Connection, drives: list[dict], start: int,
                   exclude: str | None) -> tuple[int, str, dict | None]:
    """(הקצב לפני הכוננים והגבולות, basis, הגל הקודם) — **לפי מחשבי השיכפול**.

    #1471, הכרעת נדב 16/10: בחדר מחליפים מגירות בכל גל, ולכן הזיכרון הוא לכל **מכונה** (MAC — מכונה שהוחלפה
    היא MAC חדש), ולפי מספר המגירות שלה בגל (הכרעת נדב 17/10):

        לכל MAC בגל: גל החתיכות האחרון (לא ישיר, עם קצב) שהמכונה הייתה ב-`roster` שלו, עם אותו מספר
                     מגירות, ובלעדיו הקרוב (`_pick`) — והקצב שהוא יצא בו (`unmeasured`: הגל ההוא אינו ראיה
                     לעלייה או לירידה; הנלמד הוא של `chunk_rate.adjust`)
        מגל עם פחות מגירות — × DOWN_FACTOR ^ log2(מגירות עכשיו / מגירות אז) (#1842, הכרעת נדב 20/10:
                     432m של 2 מגירות → 324m ל-4, הקצב שהברזל הוכיח ב-19/10) — `basis: "drawers"`
        קצב = המינימום על המכונות; `previous` = הגל של המכונה שקבעה, ו-`mac` שלה
        מכונה אחת בלי גל כזה (או כונן בלי MAC) → **כל הגל** בפתיחה

    גל שהשרת היה צוואר הבקבוק בו (#1884) וגל שבו המעבד של המכונה הואט (#1885) אינם הגל הקודם שלה.
    גל של המנוע הקודם מ-DB משודרג אינו נקרא (`_chunk_wave`, #3173)."""
    want = Counter(d.get("mac") for d in drives)
    if not want or None in want:
        return start, "start", None
    # mac → {מגירות: (session_id, past, rate)} — הגל האחרון של כל מספר מגירות
    seen: dict[str, dict[int, tuple[str, dict, int]]] = {}
    for row in conn.execute(
            "SELECT session_id, decision, stream, delivered FROM wave_rates"
            " WHERE session_id IS NOT ? ORDER BY rowid DESC LIMIT ?",
            (exclude, MEMORY_SCAN)):
        try:
            past = json.loads(row["decision"])
            stream = json.loads(row["stream"]) if row["stream"] else {}
            delivered = json.loads(row["delivered"]) if row["delivered"] else None
        except (TypeError, ValueError):
            continue
        if not isinstance(past, dict) or not _chunk_wave(past, stream):
            continue                                 # #3173: המנוע הקודם, DB משודרג — אינו נקרא
        rate = parse_bitrate(past.get("max_bitrate"))
        if past.get("direct") or rate is None:
            continue
        # #1884: גל שהשרת היה צוואר הבקבוק בו (דיסק הספרייה) אינו ראיה על המשכפלים.
        if server_io.bound(conn, row["session_id"]):
            continue
        had = Counter(d.get("mac") for d in past.get("roster") or [] if isinstance(d, dict))
        for mac in want.keys() & had.keys():
            # #1885: גל שבו המעבד של המכונה הואט (throttle) אינו ראיה על הקצב שלה.
            if isinstance(delivered, dict) and cloner_cpu.throttled((delivered.get(mac) or {}).get("cpu")):
                continue
            seen.setdefault(mac, {}).setdefault(had[mac], (row["session_id"], past, rate))
    if any(mac not in seen for mac in want):
        return start, "start", None                  # מכונה בלי גל חתיכות קודם
    found: dict[str, tuple[int, str, dict]] = {}
    for mac, counts in seen.items():
        drawers, (session_id, past, rate) = _pick(counts, want[mac])
        bits, basis = rate, "unmeasured"
        if drawers < want[mac]:                      # #1842: מפחות מגירות
            scaled = int(rate * DOWN_FACTOR ** math.log2(want[mac] / drawers))
            if bits > scaled:
                bits, basis = scaled, "drawers"
        found[mac] = bits, basis, {"session_id": session_id, "max_bitrate": past["max_bitrate"],
                                   "loss_blocks": None, "drawers": drawers}
    mac, (bits, basis, previous) = min(found.items(), key=lambda kv: (kv[1][0], kv[0]))
    return bits, basis, {**previous, "mac": mac}


def choose(conn: sqlite3.Connection, drives: list[dict],
           ceiling_raw: str | None, session_id: str | None = None,
           direct: bool = False, link: dict | None = None) -> dict:
    """הקצב לגל של `drives` (`serial`, `model`, `mac`, `port`) — קריאה בלבד.

    `max_bitrate` הוא הקצב של הגל (`chunkcast --kbps`, אחרי `chunk_rate.adjust`), ו-`basis` אומר למה: `start`
    (מכונה בגל שאין לה גל חתיכות קודם — #1471) · `unmeasured` (הקצב שהגל הקודם של המכונה שקבעה יצא בו —
    ב-`previous`, עם ה-`mac` שלה) · `drawers` (#1842 — נגזר מגל עם פחות מגירות למכונה, מוקטן לפי
    מספר המגירות; `_from_previous`) · `learned`
    (כונן מוכר הוריד, והוא ב-`by`) · `ceiling` (הגג עצר) · `disabled` (המתג
    כבוי — התקרה של הגל, קבועה; #3036) · `bad_ceiling` (התקרה אינה מספר; עוברת כמות
    שהיא). `session_id` — הגל שבוחרים לו; הוא אינו "הגל הקודם" של עצמו.

    `direct` — מחשב בנייה משדר (#715): אותה פתיחה, אותו גג ואותה ירידה
    לכונן איטי מוכר — אבל **בלי זיכרון ובלי עלייה**. המשדר הוא מחשב הבנייה
    ולא השרת, ובלי המונים שלו אין ראיה לגל נקי (עיקרון 5); וגל
    כזה אינו "הגל הקודם" של אף גל ואינו מלמד את הכוננים (`record_wave`) —
    המקור עצמו (דיסק חי + דחיסה) יכול להיות האיטי, והייחוס לכוננים היה שקר.

    `link` — מה ש-`read_link` קרא על כרטיס ההפצה (הכרעת נדב 15/10): גג
    ופתיחה לפי מהירות הקישור (`LINK_CEILING_PER_MBPS`). מהירות שלא נקראה —
    600m/900m של היום, והסיבה ב-`link.why`. **לא** לגל ישיר: הזרם יוצא
    מהכרטיס של מחשב הבנייה, לא מזה של השרת.
    """
    ceiling = parse_bitrate(ceiling_raw) if ceiling_raw else TOP_BITRATE
    configured = ceiling if ceiling_raw else None
    server = None if direct or ceiling is None else _link_bounds(link)
    # 15/10 (ב): המקבלים — מחשב השיכפול האיטי בגל (ידני / מדווח / הנחת 1G).
    # גם בגל ישיר: המקבלים אותם מקבלים.
    cloner = (nic_speed.slowest(conn, [d.get("mac") for d in drives])
              if ceiling is not None else None)
    receivers = _link_bounds(cloner)
    # שרת שלא נקרא — הגג של היום (`IMAGESRV_MAX_BITRATE` או 900m) והפתיחה 600m.
    base = server or (ceiling, START_BITRATE)
    bounds = server
    if receivers is not None:
        bounds = (min(base[0], receivers[0]), min(base[1], receivers[1]))
    start_at = START_BITRATE
    if bounds is not None:
        # `IMAGESRV_MAX_BITRATE` שהוגדר נשאר גג — הנמוך מבין השניים.
        ceiling = min(ceiling, bounds[0]) if ceiling_raw else bounds[0]
        start_at = bounds[1]
    # #2028: קצב שהוכח בסבב למידה — רק על קישור של 1G ומעלה: קישור איטי יותר נשאר ב-60% שלו.
    learned = learned_start(conn) if start_at >= START_BITRATE else None
    start_at = max(start_at, learned or 0)
    # מי קבע את התקרה — למסך. None = 900m של היום (שום דבר לא נקרא).
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
              **({"start_learned": True} if learned and learned == start_at else {}),
              **({"link": {**link, "applied": server is not None}} if link else {}),
              **({"cloner": cloner} if cloner else {}),
              **({"cloner_links": links} if cloner and (links := nic_speed.reported_speeds(
                  conn, [d.get("mac") for d in drives])) else {})}
    if ceiling is None:
        return {**result, "basis": "bad_ceiling"}
    start = min(start_at, ceiling)
    result = {**result, "max_bitrate": _fmt(start), "start": _fmt(start)}
    if not enabled(conn):
        # #3036, הכרעת נדב 12/11: כבוי = קצב קבוע בתקרה (`IMAGESRV_MAX_BITRATE`, בלעדיו 900m, ולא מעל הכרטיס
        # או המחשב האיטי), כמו שהנוסח בקונסולה אומר. לא קצב הפתיחה, והקצב הנלמד (`learned_start`) אינו משפיע.
        return {**result, "max_bitrate": _fmt(ceiling), "basis": "disabled"}
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
    """מה שנבחר לגל כשיצא; None = לא נבחר (גל שהשרת אינו משדר, או ישן)."""
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
           link: Callable[[], dict] | None = None,
           adjust: Callable[[dict, list[dict]], dict] | None = None) -> str | None:
    """נקרא כשהגל יוצא: בוחר, שומר, רושם ביומן, ומחזיר את הקצב (`600m`).
    `direct` — המשדר הוא מחשב בנייה (#715, #3161), ראו `choose`.
    `link` — קורא את כרטיס ההפצה של הגל; נקרא פעם אחת לגל.
    `adjust` (#3163) — (תשובת `choose`, הכוננים) → תשובה: גל של מנוע החתיכות מחליף בה את הקצב בנלמד
    (`chunk_rate.adjust`, שאינו זורק). חסר — התשובה של `choose` כמות שהיא (גל ישיר).

    לעולם אינו זורק: כשל כאן הוא "לא נלמד" — נרשם ביומן, והגל יוצא בקצב
    הפתיחה. הלמידה אינה סיבה שהשידור לא ייצא.
    """
    try:
        wave = drives()
        chosen = choose(conn, wave, ceiling_raw, session_id, direct=direct,
                        link=link() if link is not None else None)
        if adjust is not None:
            # #3296: `engine` -- the drive memory of this wave is learned from its first passes (`record_wave`), and it
            # must know that even when the room closes before the engine says how the wave ended
            chosen = {**adjust(chosen, wave), "engine": "chunk"}
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
                f" basis={chosen['basis']}"
                + (f" by={by['model'] or '?'} {by['serial']}" if by else "")
                + (f" from={previous['session_id']} {previous['max_bitrate']}"
                   f" rexmits={previous['loss_blocks']} mac={previous.get('mac')}"
                   f" drawers={previous.get('drawers')}"
                   f"/{sum(d.get('mac') == previous.get('mac') for d in wave)}"
                   if previous else "")
                + (f" link={nic['interface']}"
                   f" {str(nic['speed_mbps']) + 'Mb/s' if nic['applied'] else nic['why'] or 'unused'}"
                   f" ceiling={chosen['ceiling']}" if nic else "")
                + _cloner_note(chosen.get("cloner"), chosen.get("cap_by")),
                round_id=session_id)
        return chosen["max_bitrate"]
    except Exception as exc:      # noqa: BLE001 — הלמידה אינה מפילה גל
        log.exception("wave rate for %s failed; using the start rate", session_id)
        _failed(conn, f"{session_id} decide — {exc}")
        return _fallback(ceiling_raw)


def _cloner_note(cloner: dict | None, cap_by: str | None) -> str:
    """ליומן: מחשב השיכפול האיטי ומאיפה המהירות — `assumed_1g(<why>)` כשהונחה."""
    if not cloner:
        return ""
    source = (f"assumed_1g({cloner['why']})" if cloner["source"] == "assumed"
              else cloner["source"])
    return (f" cloner={cloner['mac']} {cloner['speed_mbps']}Mb/s {source}"
            f" assumed={len(cloner['assumed'])} cap_by={cap_by}")


def direct_bitrate(conn: sqlite3.Connection, session_id: str | None,
                   ceiling_raw: str | None) -> str:
    """`--max-bitrate` למחשב בנייה שמשדר (#715, `task.direct.multicast`):
    מה שנבחר לגל כשיצא (`app.on_running`); לפני כן, או בלי בחירה — קצב
    הפתיחה. לעולם לא "בלי תקרה": בלי `IMAGESRV_MAX_BITRATE` — 600m."""
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


def _from_stream(chunk: dict | None, mac: str, serial: str, written: int) -> tuple[int, float, int] | str:
    """#3296: (bytes, seconds, offered bytes/s) -- what a drive of a chunk wave took from the multicast stream: the
    compressed bytes durable on it when the first pass of each partition ended (`rate_outcome.drives`), over the
    seconds the first passes ran (`first_pass_s`); offered = what those passes multicast over the same seconds
    (`stream_zbytes`), the ceiling the drive kept up with or not. Or why there is no such number:
    `no_first_pass` (the wave's first passes were not measured, or not yet: the room closed before `send_done`) ·
    `not_in_first_pass` (this drive is not in them) · `http_majority` (most of what it wrote came by HTTP after the
    first pass: it proves no rate the drive cannot do -- the wave does not teach it a drop). Not measured is not slow."""
    chunk = chunk or {}
    seconds, offered = chunk.get("first_pass_s"), chunk.get("stream_zbytes")
    drives = chunk.get("drives")
    if (not isinstance(drives, dict) or not _count(offered) or not offered
            or not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0):
        return "no_first_pass"
    took = (drives.get(mac) or {}).get(serial) if isinstance(drives.get(mac), dict) else None
    if not _count(took):
        return "not_in_first_pass"
    if 2 * took < written:
        return "http_majority"
    return took, float(seconds), int(offered / seconds)


def record_wave(conn: sqlite3.Connection, session_id: str, members) -> int:
    """כל מגירה שסיימה (`done`) בגל משאירה את הקצב שכתבה. מחזיר כמה נרשמו.

    הקצב = מה שהכונן לקח מהשידור במעברים הראשונים (`_from_stream`, #3296). #3173: כל גל שהשרת משדר הוא גל
    חתיכות; גל בלי מעבר ראשון שנמדד אינו מלמד (`drives_unmeasured`). חוזר על עצמו בבטחה (שורה לכל גל+סידורי),
    ולעולם אינו זורק: כשל כאן נרשם ביומן ואינו עוצר את סיום הגל. באותה
    כתיבה — `wave_rates.drops`: `udp_drops` של כל מכונה ברשימת הגל, שממנו
    `learn_netloss` ו-`capability` קוראים את מה שהמקלטים זרקו; ו-`wave_rates.delivered`
    (#1758): לכל מכונה ברשימת הגל — כמה מגירות היו לה, כמה `done`, וסכום
    ה-`bytes_written` שלהן; מכונה שלא דיווחה — None (`learn_eta`, `tuning`, `learn_netloss`).
    """
    try:
        decided = decision(conn, session_id) or {}
        cap = parse_bitrate(decided.get("max_bitrate"))
        if cap is None or decided.get("direct"):
            return 0                  # אין תקרה ידועה, או מקור שאולי הוא האיטי
        wave = conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?",
                            (session_id,)).fetchone()
        try:
            stream = json.loads(wave["stream"]) if wave and wave["stream"] else {}
        except (TypeError, ValueError):
            stream = {}
        roster = {(d.get("mac"), d.get("serial")): d.get("model")
                  for d in decided.get("roster") or [] if isinstance(d, dict)}
        # הכרעת נדב 17/10: מה שכל מקלט דיווח שהקרנל שלו זרק (`udp_drops` בדיווח,
        # `reports.ingest`) — לכל מכונה ברשימת הגל; חסר = None, "לא נקרא".
        reported = {member["mac"]: _udp_drops(member) for member in members}
        failed = {member["mac"]: sum(isinstance(t, dict) and t.get("state") == "failed"
                                     for t in json.loads(member["targets_json"] or "[]"))
                  for member in members}
        # rc2: כמה מגירות של המכונה הסוכן דיווח שנכשלו.
        drops = {mac: ({**reported[mac], "failed_drawers": failed.get(mac, 0)}
                       if reported.get(mac) else None) for mac, _ in roster}
        # #1758: מה שכל מכונה ברשימת הגל כתבה בפועל — המונה של goodput. מכונה
        # שלא דיווחה = None ("לא נמדד"); מגירה שלא `done` — אפס בייטים.
        delivered = {mac: ({"drawers": sum(m == mac for m, _ in roster), "done": 0,
                            "disk_failed": 0, "stream_failed": 0, "unknown_failed": 0,
                            "written_bytes": 0}
                           if mac in reported else None) for mac, _ in roster}
        # #1885: מעבד/throttle/טמפ' של כל מכונה בגל (`cpu_json`) — `_from_previous`. לא דווח —
        # המפתח חסר (כמו לפני #1885), לא אפס.
        for member in members:
            got, cpu = delivered.get(member["mac"]), _cpu(member)
            if got is not None and cpu is not None:
                got["cpu"] = cpu
        disk_failed = {(r["mac"], r["dev"]) for r in conn.execute(
            "SELECT mac, dev FROM disk_failures WHERE session_id = ?", (session_id,))}
        # #3296: a chunk wave -- the drives learn from what they took from the first passes (`ChunkEngine.rate_outcome`,
        # kept by `chunk_rate.record` at `send_done`), not from their counter's window: on 14/11 (ses_43eebdbc) a
        # Samsung that took all of its chunks in the first pass by 20:15 waited 12 minutes for its neighbour's HTTP
        # completion, wrote the last partition at 20:27, and was learned at 9MB/s
        chunk = stream.get("chunk") if isinstance(stream, dict) and isinstance(stream.get("chunk"), dict) else None
        unmeasured: dict[str, str] = {}
        rows = []
        # #3046: "קצב שידור אוטומטי" כבוי (`learned_rate_enabled`) = הזיכרון לא נכתב. `drops` ו-`delivered` למטה כן
        # נכתבים: הם מונים של הגל (goodput, האבדן), לא למידה. נמדד ב-14/11 וב-15/11 (#3034): גלים שהושלמו כתבו
        # שורות `drive_rates` של 10.3MB/s עם המתג כבוי.
        learning = enabled(conn)
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
                elif t.get("state") == "failed" and got is not None:
                    key = (member["mac"], t.get("dev"))
                    cause = ata_cause.classify(ata_cause.clean_log(t.get("ata_log")),
                                               t.get("error") or "")
                    if key in disk_failed:
                        got["disk_failed"] += 1
                    elif (disk_failures.stream_fault(cause, t.get("error"))
                          or disk_failures.interrupted(cause, t.get("error"))):
                        got["stream_failed"] += 1
                    else:
                        got["unknown_failed"] += 1
                if (not learning or t.get("state") != "done" or not serial or not isinstance(written, int)
                        or written < MIN_SAMPLE_BYTES):
                    continue
                measured = _from_stream(chunk, member["mac"], serial, written)
                if isinstance(measured, str):
                    unmeasured[serial] = measured
                    continue
                sample, seconds, offered = measured
                if seconds < MIN_SAMPLE_S or sample < MIN_SAMPLE_BYTES:
                    continue
                rows.append((serial, roster[(member["mac"], serial)], session_id,
                             int(sample / seconds), offered, sample,
                             int(seconds), now_iso()))
        # #3269: a wave the server held back (`server_io`: the library disk, or its IO pressure) teaches the drives no
        # DROP -- they wrote at what the server fed them. On 13-14/11 such a wave taught a 179MB/s SSD "21MB/s" and the
        # next waves fell to the floor. A sample that kept up still counts: it proves at least the ceiling.
        from server.rounds import chunk_rate          # here, not above: chunk_rate imports this module
        pressure = chunk_rate.server_pressure(conn, session_id)
        held = [r for r in rows if r[3] < KEEP_UP * r[4]] if pressure else []
        rows = [r for r in rows if r not in held]
        with _write_lock, writing(conn):
            conn.execute("UPDATE wave_rates SET drops = ?, delivered = ? WHERE session_id = ?",
                         (json.dumps(drops), json.dumps(delivered), session_id))
            if held or unmeasured:
                got = conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (session_id,)).fetchone()
                try:
                    now_stream = json.loads(got["stream"]) if got and got["stream"] else {}
                except (TypeError, ValueError):
                    now_stream = {}
                now_stream = now_stream if isinstance(now_stream, dict) else {}
                conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = ?",
                             (json.dumps({**now_stream,
                                          **({"drives_unlearned": {"reason": pressure,
                                                                   "serials": sorted({r[0] for r in held})}}
                                             if held else {}),
                                          **({"drives_unmeasured": unmeasured} if unmeasured else {})}), session_id))
            written = sum(conn.execute(
                "INSERT OR IGNORE INTO drive_rates (serial, model, session_id,"
                " rate_bps, cap_bps, bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
                row).rowcount for row in rows)
        if held:
            from server.brain import memory
            journal(conn, "drive_rates_unlearned",
                    f"{session_id} {pressure}: " + " ".join(f"{r[0]}={r[3] / 1e6:.0f}MB/s" for r in held),
                    round_id=session_id)
            memory.record(conn, mode="enforce", rule=chunk_rate.RULE, point="#3269", action="DRIVE_MEMORY",
                          verdict="UNLEARNED", reason=pressure,
                          evidence=[{"serial": r[0], "rate_bps": r[3], "cap_bps": r[4]} for r in held],
                          round_id=session_id, entity=("round", session_id), outcome="applied")
        if unmeasured:
            from server.brain import memory
            journal(conn, "drive_rates_unmeasured",
                    f"{session_id}: " + " ".join(f"{s}={why}" for s, why in sorted(unmeasured.items())),
                    round_id=session_id)
            memory.record(conn, mode="enforce", rule=chunk_rate.RULE, point="#3296", action="DRIVE_MEMORY",
                          verdict="UNLEARNED", reason=",".join(sorted(set(unmeasured.values()))),
                          evidence=[{"serial": s, "why": why} for s, why in sorted(unmeasured.items())],
                          round_id=session_id, entity=("round", session_id), outcome="applied")
        return written
    except Exception as exc:      # noqa: BLE001 — הזיכרון אינו עוצר סיום גל
        log.exception("recording drive rates of %s failed", session_id)
        _failed(conn, f"{session_id} record — {exc}")
        return 0


def _udp_drops(member) -> dict | None:
    """`session_members.udp_drops_json` של חבר, או None — לא דווח."""
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
    """#1885: `session_members.cpu_json` של חבר, או None — לא דווח."""
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
