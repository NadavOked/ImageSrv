"""מה שמנוע החתיכות צריך מסביבת השידור, ואינו שלו לבדו (#3173).

עד #3173 הדברים האלה ישבו בשולח הישן (`sender.py`), ומנוע החתיכות שאל אותם ממנו. השולח הישן
נמחק; מה שנשאר נחוץ עבר לכאן, בשמות שהיו לו:

* הפורטים: `PRODUCTION_PORTBASE`, `DEFAULT_PORTBASE`. מנוע החתיכות (Trident) משדר על `portbase + 2`
  (`chunksend.PORT_OFFSET`) ושומע את הבקרה על `portbase + 3`; בייצור אלה `TRIDENT_DATA` ו-`TRIDENT_CONTROL`
  של `server/core/ports.py` (#3871). חבילת הטסטים דורסת את `DEFAULT_PORTBASE` כאן
  (`tests/hygiene.assign_test_portbase`).
* תקרות ההמתנה: `DEFAULT_MAX_WAIT`, `DEFAULT_START_TIMEOUT`, `LATER_MAX_WAIT`,
  `later_max_wait`, `later_start_timeout`.
* עצירת תהליך-בן: `STOP_TERM_WAIT`, `STOP_KILL_WAIT`, `reaped`.
* גג הקצב שהמפעיל קבע: `env_max_bitrate` (`IMAGESRV_MAX_BITRATE`).
* מהירות הקישור של כרטיס ההפצה: `link_of`.
* שרת שאסור לו לשדר (`--sender-simulate`, `tools/restore-drill.sh`): `simulated_runner`.

`max_bitrate`, `link()`, `link_of()`, `gate` ו-`select_interface` עצמם הם שדות של `ChunkEngine`
(`ctx.chunk`), שנבנים מהמודול הזה. אין כאן תהליך ואין כאן מצב.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

from server.core import ports as product_ports   # #3871: the one place a port is named

#: הבסיס של פורטי השידור בייצור. #3871: נגזר מ-`server/core/ports.py`: הנתונים של Trident ב-`TRIDENT_DATA`
#: (= הבסיס + `chunksend.PORT_OFFSET`), והבקרה, שאליה המקבלים שולחים משוב, בפורט שאחריו. הבסיס עצמו אינו פורט
#: שמישהו מאזין בו; הוא נשאר כי `--sender-portbase` וחבילת הטסטים מזיזים את שני הפורטים דרכו (#156).
PRODUCTION_PORTBASE = product_ports.TRIDENT_DATA - 2
#: ברירת המחדל בפועל. חבילת הטסטים דורסת אותה ב-portbase גבוה ואקראי, כדי שיתום של ריצה שנקטעה
#: לא יוכל להתנגש בהפצה אמיתית.
DEFAULT_PORTBASE = PRODUCTION_PORTBASE
#: כמה להמתין למקבלים שטרם התייצבו לפני שמשדרים בכל זאת: מכונה שנתקעה באתחול אינה עוצרת את השאר.
DEFAULT_MAX_WAIT = 120
#: כמה להמתין ל**מקבל הראשון** לפני שמוותרים (#341). שלוש דקות: מכונה שכבר אמרה hello צריכה פחות
#: מזה כדי להתייצב, והמפעיל אינו נשאר מול מסך קפוא יותר מזה.
DEFAULT_START_TIMEOUT = 180
#: ההמתנה למקבלים **מהמחיצה השנייה והלאה** (#957). למחיצה הראשונה כל המכונות מגיעות יחד מהרשת;
#: לבאות כל מכונה מגיעה כשסיימה **לכתוב** את הקודמת, ומגירה איטית מסיימת הרבה אחרי המהירה.
#: הערך הוא `max(600, max_wait)`: לא פחות מעשר דקות, ולא פחות מהראשונה.
LATER_MAX_WAIT = 600

#: תהליך-בן שנעצר: כמה ממתינים אחרי SIGTERM, וכמה אחרי SIGKILL, לראיה שהוא מת (#439).
STOP_TERM_WAIT = 0.5
STOP_KILL_WAIT = 1.0


def later_max_wait(max_wait: int) -> int:
    return max(LATER_MAX_WAIT, max_wait)


def later_start_timeout(max_wait: int, start_timeout: float) -> int:
    return later_max_wait(max_wait) + int(start_timeout)


def reaped(process, seconds: float) -> bool:
    """True רק כשיש ראיה שהילד מת. היעדר בדיקה אינו ראיה (#439)."""
    deadline = time.monotonic() + seconds
    while True:
        if process.poll() is not None:
            pid = getattr(process, "pid", None)
            root = Path("/proc")
            if pid is None or not root.is_dir():
                return True
            try:
                return not (root / str(pid)).is_dir()
            except OSError:
                return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def env_max_bitrate() -> str | None:
    """גג הקצב שהמפעיל קבע בסביבה (`IMAGESRV_MAX_BITRATE`, למשל ``"700m"``), או None = לא נקבע.

    ריסון קצב: ברשת 1G הכבל מרסן מעצמו; ברשת מהירה (מעבדת VM, 10G) חייבים רסן מפורש בקצב
    שהדיסקים מעכלים (#24). `drive_rates.decide` מקבל אותו כגג ולעולם אינו בוחר מעליו."""
    return os.environ.get("IMAGESRV_MAX_BITRATE") or None


def link_of(link_speed: Callable[[str | None], dict] | None, interface: str | None) -> dict:
    """כרטיס ומהירותו (`drive_rates.read_link`), או למה לא נקראה. `link_speed` שהוא None = לא מחובר
    (בדיקות; `server.main` מחבר תמיד): "לא נקרא" (``why="not_wired"``), לא ניחוש של 1GbE (עיקרון 5)."""
    if link_speed is None:
        return {"interface": interface, "speed_mbps": None, "why": "not_wired"}
    return link_speed(interface)


class SimulatedSend:
    """תהליך-דמה במקום `chunkcast send`: לא משדר דבר, ו"יוצא" מיד בקוד 0. המשכפלים של גל כזה יחסרו להם כל החתיכות,
    וההשלמה (HTTP) היא שתביא אותן -- שרת כזה אינו שרת ייצור (`--sender-simulate`)."""

    pid = None
    returncode = 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def simulated_runner(cmd: list[str]) -> SimulatedSend:
    """ה-`runner` של שרת שאסור לו לשדר: אף תהליך לא רץ, ואף פורט לא נתפס."""
    return SimulatedSend()
