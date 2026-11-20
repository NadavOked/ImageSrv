"""שורות הגישה של uvicorn: רושמים חריגים, לא polling שגרתי (#3184).

הבעיה, נמדדה ב-LabRoot ב-13/11 בשעת השיא: 87% משורות `imagesrv-server`
ב-journald היו שורות גישה (כ-2,630 מתוך 3,024 בשעה), והן דחקו את הראיות
האמיתיות מתקרת ה-1G. השמירה בפועל הייתה כשבועיים.

**מה נרשם** (כל השאר נשמט, ורק משורות הגישה -- שום לוגר אחר לא נוגע):

* כל תשובה 4xx/5xx;
* כל כתיבה (POST/PUT/PATCH/DELETE) -- כתיבה של מפעיל בקונסולה, וגם כתיבת
  סוכן נדירה כמו `disk-event`. החריג היחיד הוא `ROUTINE_AGENT_POSTS`, שלושת
  ה-POST ש**הם** ה-polling של הסוכן (`hello`, `progress`, `pulls`);
* כל בקשה איטית: `SLOW_S` שניות ומעלה **עד תחילת התשובה**. הזמן לגוף
  המלא אינו נמדד: הורדת אימג' ארוכה מטבעה, ואינה ראיה לתקלה.

שאר הקריאות (GET שהצליח: ריענון הקונסולה, סטטוס הסוכן, `/boot`, הורדות) הן
polling שגרתי. הצעדים החשובים שבהן כבר נרשמים ביומן המפעיל וב-DB, ושורת
הגישה רק שוכפלה אותם.

**עיקרון 5: מה שלא הצלחנו לסווג נרשם, לא נשמט.** רשומה שהארגומנטים שלה אינם
הצורה ש-uvicorn כותב (חמישה ערכים, סטטוס מספרי) עוברת כמות שהיא.

**שתי חתיכות, ושתיהן כאן:**

1. `AccessFilter` -- פילטר על הלוגר `uvicorn.access`. ל-uvicorn יש ללוגר הזה
   handler משלו וללא propagate, ולכן הוא מחוץ לכל הגדרת לוגינג של ה-root
   (#3157), והפילטר אינו נוגע בה. פילטר שנוסף ללוגר שורד את `dictConfig` של
   uvicorn, שמחליף handlers ולא פילטרים (נבדק: `uvicorn.Config(...)` אחרי
   ההתקנה).
2. `SlowStamp` -- middleware גולמי שמודד את הזמן עד `http.response.start`
   ומשאיר אותו ב-`ContextVar`. uvicorn כותב את שורת הגישה **באותה קריאת
   `send`**, באותו task, ולכן הפילטר קורא את הערך. הרשומה עצמה אינה נושאת
   זמן, ובלי זה אין דרך להבחין בבקשה איטית.

הבקשה האיטית נרשמת עם הזמן בנתיב: ``GET /api/console/x [slow 2.3s]``.
"""

import contextvars
import logging
import time

ACCESS_LOGGER = "uvicorn.access"
#: עד תחילת התשובה. קבוע מודול ולא הגדרה: אין מי שיכוון אותו.
SLOW_S = 2.0
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: ה-POST של ה-polling של הסוכן, בשם. כל POST אחר לפורט הסוכן הוא אירוע.
ROUTINE_AGENT_POSTS = frozenset({
    "/api/v1/agent/hello",
    "/api/v1/agent/progress",
    "/api/v1/agent/pulls",
})

#: כמה זמן ארכה הבקשה הנוכחית עד התשובה, כשעברה את `SLOW_S`; אחרת None.
_slow: contextvars.ContextVar = contextvars.ContextVar("imagesrv_slow_request", default=None)


class SlowStamp:
    """middleware גולמי (לא ``@app.middleware``): לא נוגע בתשובה ובכותרות, רק
    מודד. אותה צורה כמו ``ConsoleNoStaleCache``."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        started = time.monotonic()

        async def stamp(message):
            if message["type"] == "http.response.start":
                elapsed = time.monotonic() - started
                _slow.set(elapsed if elapsed >= SLOW_S else None)
            await send(message)

        await self.inner(scope, receive, stamp)


class AccessFilter(logging.Filter):
    """True = השורה נרשמת. הארגומנטים של uvicorn:
    ``(client_addr, method, path_with_query, http_version, status_code)``."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not (isinstance(args, tuple) and len(args) == 5):
            return True
        client, method, target, version, status = args
        if not (isinstance(method, str) and isinstance(target, str) and isinstance(status, int)):
            return True
        slow = _slow.get()
        if slow is not None:
            record.args = (client, method, f"{target} [slow {slow:.1f}s]", version, status)
            return True
        if status >= 400:
            return True
        if method not in SAFE_METHODS and target.split("?", 1)[0] not in ROUTINE_AGENT_POSTS:
            return True
        return False


def install(app) -> None:
    """הפילטר על ``uvicorn.access`` (פעם אחת לתהליך) והמדידה על האפליקציה."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, AccessFilter) for f in logger.filters):
        logger.addFilter(AccessFilter())
    app.add_middleware(SlowStamp)
