"""#3064, שלב 2 — **V6**: ראיית חיים מבייטים שהשרת עצמו הגיש.

מונה לכל כתובת מקור ב-HTTP של קובצי האימג' (`GET /images/<id>/files/<file>`, `server/agent/api.py`): מתי נשלח
בייט אחרון, וכמה בסך הכול. **זו ראיה שאינה תלויה בסוכן:** משכפל שמוריד חתיכות בהשלמה (או שמשלים מגירה שחזרה)
ושותק ב-`progress` וב-`chunk-pass` עדיין "נראה" כאן, כל עוד השרת כותב לו בייטים. אלה הבייטים שהיו מצילים את
13/11 גם בלי לתקן את הסוכן.

**הכתובת היא המפתח, וה-MAC נגזר ברגע ההחלטה** (`attribute`), מקובץ החכירות — לא בכל בקשה: שורה אחת למכונה
בקובץ, ושער הזהות כבר קורא אותו פעם בכל בקשה. מכונה אחת בכתובת אחת היא המקרה הרגיל; בסימולציה כל המכונות על
loopback, ולכן הבייטים נספרים לכולן (שמרני: יותר ראיה = יותר HOLD, לעולם לא פחות).

**"לא הצלחנו לייחס" אינו "אין ראיה" (עיקרון 5).** כתובת עם בייטים בחלון שלא ניתן לייחס אותה (הקובץ לא נקרא, אין
חכירה, או שאין מקור חכירות) מוסיפה חוש `served_http` ל-`unknown` של הצילום: משכפל שאין לו ראיה אחרת נקרא UNKNOWN
(מחזיק), לא LOST.

**בלי זה שום דבר אינו חסום:** ההוק ב-`server/agent/api.py` אינו זורק לעולם, ומונה שנפל אינו מפיל הורדה.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from server.brain import observe

log = logging.getLogger("imagesrv.brain")

#: מקורות שנשמרים בזיכרון: כתובת שלא הגישו לה בייט שעה נשכחת, וכך המונה אינו גדל עם כל מחשב שעבר ברשת.
KEEP_S = 3600.0
MAX_SOURCES = 4096
SOURCE = "http bytes to {ip}"
UNKNOWN_SENSE = "served_http"


@dataclass
class _Source:
    last: float         # שניות epoch של הבייט האחרון שהוגש
    total: int          # סך הבייטים מאז שהמקור נראה


class Served:
    def __init__(self, *, clock: Callable[[], float] = time.time, keep_s: float = KEEP_S,
                 max_sources: int = MAX_SOURCES):
        self._clock, self._keep_s, self._max = clock, keep_s, max_sources
        self._lock = threading.Lock()
        self._by_ip: dict[str, _Source] = {}
        self._grand = 0                            # סך כל הבייטים מאז שהתהליך עלה: עולה בלבד, גם כשמקור נשכח (K7)
        self._open: dict[str, int] = {}            # #3125 (V6): כמה הורדות פתוחות עכשיו לכל כתובת

    def note(self, ip: str, nbytes: int) -> None:
        """נשלחו `nbytes` ל-`ip` (מופעל לכל חתיכת גוף של תשובה). לעולם אינו זורק: מונה אינו מפיל הורדה."""
        if nbytes <= 0 or not ip:
            return
        try:
            now = self._clock()
            with self._lock:
                src = self._by_ip.get(ip)
                if src is None:
                    if len(self._by_ip) >= self._max:
                        self._prune(now)
                    if len(self._by_ip) >= self._max:        # עדיין מלא: מפנים את הישן ביותר
                        del self._by_ip[min(self._by_ip, key=lambda k: self._by_ip[k].last)]
                    src = self._by_ip[ip] = _Source(now, 0)
                src.last, src.total = now, src.total + nbytes
                self._grand += nbytes
        except Exception:                          # noqa: BLE001
            log.exception("brain: the served-bytes counter failed for %s", ip)

    def opened(self, ip: str) -> None:
        """#3125 (V6): הורדה של קובץ אימג' נפתחה ל-`ip`. לעולם אינו זורק."""
        with self._lock:
            self._open[ip] = self._open.get(ip, 0) + 1

    def closed(self, ip: str) -> None:
        """#3125 (V6): ההורדה נסגרה (הושלמה, נכשלה, או שהפונה נעלם). לעולם אינו זורק."""
        with self._lock:
            left = self._open.get(ip, 0) - 1
            if left > 0:
                self._open[ip] = left
            else:
                self._open.pop(ip, None)

    def snapshot(self) -> dict:
        """#3125 (V6): ``{"bytes": {ip: סך הבייטים}, "active": {ip: הורדות פתוחות}}`` ברגע אחד -- ממנו
        `server_senses` גוזר את הקצב לכל כתובת בחלון ואת מספר ההורדות במקביל."""
        with self._lock:
            return {"bytes": {ip: s.total for ip, s in self._by_ip.items()}, "active": dict(self._open)}

    def _prune(self, now: float) -> None:
        for ip in [k for k, v in self._by_ip.items() if now - v.last > self._keep_s]:
            del self._by_ip[ip]

    def total_all(self) -> int:
        """סך כל הבייטים שהוגשו לכל הכתובות. מונה שעולה בלבד (מקור שנשכח אינו מוריד אותו): ממנו נגזר קצב ההגשה
        של תקציב הרשת (#3064 שלב 3, K7)."""
        with self._lock:
            return self._grand

    def total(self, ip: str) -> int:
        with self._lock:
            src = self._by_ip.get(ip)
            return src.total if src else 0

    def sources(self, now: float, window_s: float) -> list[tuple[str, float]]:
        """``(ip, גיל הבייט האחרון בשניות)`` לכל כתובת שהוגש לה בייט בתוך `window_s`."""
        with self._lock:
            return sorted((ip, max(now - s.last, 0.0)) for ip, s in self._by_ip.items() if now - s.last <= window_s)

    def attribute(self, now: float, window_s: float,
                  resolve: Callable[[str], tuple[str, ...] | None] | None) -> tuple[list[tuple[str, str, float, str]], list[str]]:
        """הראיות לצילום: ``([(mac, "served", age_s, source)], unknown)``.

        `resolve(ip)` = ה-MAC-ים החכורים לכתובת, או ``None`` כשלא ניתן היה לקרוא. ללא `resolve`, או כשהוא נכשל,
        או כשאין חכירה — כתובת עם בייטים בחלון נרשמת ב-`unknown` (``served_http``), לא נעלמת בשקט."""
        out: list[tuple[str, str, float, str]] = []
        unknown = False
        for ip, age in self.sources(now, window_s):
            try:
                macs = resolve(ip) if resolve is not None else None
            except Exception:                      # noqa: BLE001
                log.exception("brain: the lease of %s could not be resolved", ip)
                macs = None
            if not macs:
                unknown = True
                continue
            out += [(mac, observe.SERVED, age, SOURCE.format(ip=ip)) for mac in macs]
        return out, [UNKNOWN_SENSE] if unknown else []
