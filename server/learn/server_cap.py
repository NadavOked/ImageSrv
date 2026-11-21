"""#3464 (K8, LR20/V2): תקרת הקריאה של דיסק הספרייה -- נלמדת מראיה בלבד, לפי ההתקן.

`server_io` (#1884, #3365) מזהה גל שדיסק הספרייה האט אותו (`basis = server_disk`: util ו-await של **ההתקן של הספרייה**) ומוציא
אותו מהלמידה של הרשת והכוננים. עד כאן זה היה כל מה שהמוח עשה בו: על שרת עם דיסק איטי הוא לא למד כלום, ולא ידע מה
התקרה. ברזל 15/11 (LabRoot, `lab/metal-1115-brain3`): שלושה סבבים, `read=27.5-28.9MB/s device=sdb`, והשידור 263-293Mb/s.

**מה נלמד.** `learn`: מ-`WINDOW` הגלים האחרונים שנמדדו על ההתקן הנוכחי (ההתקן של הגל האחרון שנמדד), החציון של `read_mbps`
(קריאות ההתקן מ-`/proc/diskstats` לשעון הגל) **של הגלים ש-`basis = server_disk` וגם הדיסק היה רווי בהם (`SATURATED_UTIL`)**.
גל שבו ההתקן לא היה צוואר הבקבוק (`ok`) מוכיח רק שהתקרה **לפחות** כך, ולכן אינו מעלה ואינו מוריד אותה -- חוץ מגל **אחרון** שאינו ראיה לתקרה (`ok`, או `server_disk` בלי רוויה) וקרא יותר
מהתקרה: זו ראיה שהתקרה כבר אינה נכונה (דיסק שהוחלף, שנוקה) והיא נשמטת. **גל בלי מונה (`read_mbps` חסר) אינו נכנס.**
בלי `MIN_WAVES` גלים כאלה -- אין תקרה (`None`): "עוד לא נלמד", לא 0 ולא ניחוש (עיקרון 5, 5ב).

**#3525 -- רוויה היא ראיה, "עסוק" אינה.** `basis = server_disk` אומר ש-util >= 20% ו-await >= 10ms (ובגל חתיכות, מאז #3508, גם
שהשולח חיכה לקובץ הספרייה יותר מ-`LIBRARY_WAIT_MAX` מהמעבר הראשון -- #3553: `sendstats`, לא שינה לתקרות), וזה מספיק כדי להוציא גל מלמידת הרשת -- לא כדי לקבוע מה הדיסק **יכול** לתת. תקרה היא "כמה הדיסק נותן
כשהוא נדרש עד הסוף", ולכן גל נכנס אליה רק עם util >= `SATURATED_UTIL` (0.90): אז `read_mbps` נמוך מהתקרה האמיתית ב-10% לכל היותר
(`read_mbps` / util). ברזל 16/11 (`metal-1116-r12`, `sdb`): udpcast קרא 62.6-62.9MB/s ב-util 73% (פנוי 27% -- הדיסק נתן לפחות ~86MB/s), והשולח
החדש 44-47MB/s ב-util 42-46%; התקרה שנלמדה (40-49MB/s) הייתה מהירות השולח, והמוח היה נועל אותה. **גלי udpcast ושולח החתיכות נבחנים באותו
מבחן** -- הרוויה היא הראיה, לא המנוע; בלי `util` (שדה חסר) אין ראיה לרוויה. ערבוב המנועים בחלון נשאר: גל רווי מראה מה הדיסק נותן בקריאה
הזו, ואין עדיין גל רווי של אף מנוע שאפשר להשוות אליו (⚠️ לא אומת על ברזל).

**אין סכמה חדשה:** הכול נגזר משורות `server_io` שכבר נכתבות (`result_json`). הקריאה בלבד, ואינה זורקת.

**יחידות.** `bps` = `read_mbps` x 8e6: הגלים קוראים מהספרייה את הבייטים הדחוסים שהם משדרים, אותה יחידה של `--kbps`
ו-`asked_bps` (`chunk_rate.sample_of`). ⚠️ **לא אומת על ברזל:** ב-15/11 התקרה שנלמדת (220-231Mb/s) נמוכה ב-14-33% ממה
שנשלח (263-293Mb/s), כי המונה הוא לשעון הגל כולו ולא למשך המעבר הראשון. לכן הביצוע (`SETTING`) כבוי.

**⚠️ ניחוש מנומק, לא מדידה:** `MIN_WAVES`, `WINDOW` ו-`SATURATED_UTIL`.

הקובץ אינו תלוי בשרת (sqlite ו-stdlib בלבד), כדי שגם `tools/dev/brainreport.py` יקרא ממנו את אותה הגדרה.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass

log = logging.getLogger("imagesrv.server_cap")

#: המתג שמאפשר לתקרה לקצץ את הקצב החי של גל (`chunk_rate.start`). כבוי כברירת מחדל: בלעדיו ההחלטה רק נרשמת בצל.
SETTING = "brain_server_cap"
LABEL_HE = "המוח: הגבלת קצב הגל לתקרת הקריאה של דיסק הספרייה"
#: ⚠️ ניחוש מנומק: כמה גלים שהדיסק הגביל בהם לפחות לפני שתקרה נחשבת נלמדת, וכמה גלים אחרונים של ההתקן נקראים.
#: אותו חלון כמו `learn.eta.WINDOW`. אין עדיין גל שבו דיסק בריא נמדד, ולכן אין על מה לכייל.
MIN_WAVES = 2
WINDOW = 5
SCAN = 50
#: #3525: מאיזה ניצול (`util`, io_ticks לשעון הקיר) הדיסק נחשב רווי, ולכן ה-`read_mbps` של הגל הוא מה שהוא נותן ולא מה שהתבקש ממנו.
#: ⚠️ ניחוש מנומק, לא נמדד על ברזל: ה-`read_mbps` הוא כ-util x התקרה, ולכן ב-0.90 התקרה נמוכה מהאמיתית ב-10% לכל היותר; ב-0.73 של udpcast
#: ב-16/11 (62.9MB/s) היא נמוכה ב-27%, וב-0.42-0.46 של השולח החדש -- ביותר ממחצית.
SATURATED_UTIL = 0.90
MEASURED = ("server_disk", "ok")      # `server_io.measure`: מתוך ההתקן של הספרייה. `None` = לא נמדד, ואינו כאן
MB = 10 ** 6


@dataclass(frozen=True)
class Cap:
    device: str
    read_mbps: float                  # חציון הגלים שהדיסק הגביל
    waves: tuple[str, ...]            # הגלים שממנם נלמד (`server_io.ref`)

    @property
    def bps(self) -> int:
        return int(self.read_mbps * 8 * MB)

    def text_he(self) -> str:
        """השורה שבבריאות ובדוח: מה התקרה, ומאיפה היא."""
        return (f"דיסק הספרייה מוגבל ל-{self.read_mbps:.0f} MB/s — זה הגבול של הסבב"
                f" (נלמד מ-{len(self.waves)} גלים, {self.device})")


def enabled(conn: sqlite3.Connection) -> bool:
    """הביצוע דלוק: רק `true` בדיוק. חסר, פגום, או שלא נקרא = כבוי (ההחלטה נרשמת בצל והקצב לא זז)."""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING,)).fetchone()
        return row is not None and row[0] == "true"
    except Exception:                      # noqa: BLE001
        log.exception("%s unreadable, off", SETTING)
        return False


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def saturated(got: dict) -> bool:
    """#3525: ראיה חיובית שהדיסק היה רווי בגל: `util` נמדד והוא לפחות `SATURATED_UTIL`. חסר או לא מספר = אין ראיה."""
    util = got.get("util")
    return _number(util) and util >= SATURATED_UTIL


def learn(conn: sqlite3.Connection) -> Cap | None:
    """התקרה הנלמדת של ההתקן הנוכחי, או None -- עוד לא נלמד, או שהראיה האחרונה סותרת אותה. לעולם אינו זורק."""
    try:
        seen: list[tuple[str, dict]] = []
        for ref, raw in conn.execute(
                "SELECT ref, result_json FROM server_io WHERE kind = 'wave' AND result_json IS NOT NULL"
                " ORDER BY ended_at DESC, rowid DESC LIMIT ?", (SCAN,)):
            try:
                got = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(got, dict) and got.get("basis") in MEASURED and isinstance(got.get("device"), str):
                seen.append((ref, got))
        if not seen:
            return None
        device = seen[0][1]["device"]
        window = [(ref, got) for ref, got in seen if got["device"] == device][:WINDOW]
        bound = [(ref, got["read_mbps"]) for ref, got in window
                 if got["basis"] == "server_disk" and _number(got.get("read_mbps")) and saturated(got)]
        if len(bound) < MIN_WAVES:
            return None
        cap = Cap(device, round(statistics.median(mbps for _ref, mbps in bound), 1),
                  tuple(ref for ref, _mbps in bound))
        ref0, last = window[0]
        if ref0 not in cap.waves and _number(last.get("read_mbps")) and last["read_mbps"] > cap.read_mbps:
            return None                    # הגל האחרון קרא יותר מהתקרה בלי שההתקן הוגבל או נוּצל עד הסוף: התקרה אינה עוד
        return cap
    except Exception:                      # noqa: BLE001 -- למידה אינה מפילה גל
        log.exception("the library disk cap could not be learned")
        return None
