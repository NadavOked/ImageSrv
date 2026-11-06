"""‏#1633: שומר הקונסולה (‎:8081) מפני דף זר בדפדפן של מנהל מחובר.

שלושה דברים, כולם על אפליקציית הקונסולה בלבד (``create_console_app``):

1. **‏`X-Frame-Options: DENY` על כל תשובה** — דף זר אינו יכול לעטוף את
   הקונסולה ב-iframe ולגנוב לחיצה (clickjacking). ‏CSP עם ``frame-ancestors``
   מחכה עד שה-``onclick=`` המוטמעים ייצאו מה-HTML (#2493).
2. **‏Fetch Metadata** — בקשה משנת-מצב (POST/PUT/PATCH/DELETE) שהדפדפן
   סימן ``Sec-Fetch-Site`` שאינו ``same-origin`` או ``none`` → 403. עבור
   כתובת IP, "same-site" הוא ה-IP כולו בלי פורט, ולכן ``same-site`` נדחה
   גם הוא: דף על אותו IP בפורט אחר שולח את העוגייה (‏SameSite=Lax לא עוצר).
   **כותרת חסרה** — נבדק ``Origin``: דפדפן ישן בלי Fetch Metadata עדיין
   שולח ``Origin`` ב-POST/PUT/PATCH/DELETE, ולכן ``Origin`` שאינו בדיוק
   ה-scheme, ה-host והפורט שהבקשה הגיעה אליהם (``Host``) → 403, וכך גם
   ``Origin: null`` (sandbox, ‏file://, הפניה). **אין גם ``Origin`` — עובר**:
   סוכן, ‏curl, הערוץ הבין-שרתי (``_run_console``) ו-TestClient אינם דפדפן,
   ומי שאינו דפדפן אינו נושא את העוגייה של המנהל שמולו — הוא יכול לשלוח רק
   עוגייה שכבר יש לו, ואז אין כאן זיוף בקשה.
3. **סוג גוף מרשימה מותרת** — בקשה משנת-מצב עם ``Content-Type`` חייבת
   ``application/json`` (או ``application/x-tar``, ההעלאות של אימג' ודרייבר);
   בלי ``Content-Type`` — רק בלי גוף. Starlette ``request.json()`` קורא את
   הגוף בלי להסתכל על הכותרת, ו-``text/plain``/טופס הם בדיוק מה שדף זר
   שולח בלי preflight. סוג אחר מחייב preflight, והקונסולה אינה עונה ל-CORS.

middleware גולמי ולא ``@app.middleware`` — מאותו טעם כמו ``ConsoleNoStaleCache``
(‏app.py): העטיפה של Starlette בונה כל תשובה מחדש. כאן נוסף שם כותרת אחד
ולא נוגעים בשאר.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

#: השיטות שמשנות מצב. GET/HEAD/OPTIONS עוברים — ניווט רגיל, קישור, רענון.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: ``same-origin`` — הקונסולה עצמה; ``none`` — פעולה של המשתמש (שורת כתובת,
#: סימנייה). ``same-site`` ו-``cross-site`` — דף אחר.
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})

#: מה שהקונסולה שולחת בפועל: ‏``api()`` ב-console.js, ‏monitor.js, ‏vmupdate.js
#: — JSON; ‏``images/upload`` ו-``drivers/upload`` (XHR) — ‏tar.
ALLOWED_BODY_TYPES = frozenset({"application/json", "application/x-tar"})

#: פורט ברירת המחדל לכל scheme — ‏``Origin`` ו-``Host`` משמיטים אותו.
_DEFAULT_PORTS = {"http": 80, "https": 443}

CROSS_SITE_HE = "הבקשה הגיעה מדף אחר ונדחתה (‏Sec-Fetch-Site). פתחו את הקונסולה ישירות"
CROSS_ORIGIN_HE = "הבקשה הגיעה מדף אחר ונדחתה (‏Origin). פתחו את הקונסולה ישירות"
BODY_TYPE_HE = "סוג גוף הבקשה אינו נתמך — הקונסולה מקבלת JSON (application/json)"


def _values(scope, name: bytes) -> list[str]:
    return [v.decode("latin-1").strip() for k, v in scope.get("headers", [])
            if k.lower() == name]


def _has_body(scope) -> bool:
    """גוף = ``Content-Length`` שאינו אפס, או ``Transfer-Encoding`` כלשהו.
    אורך שאינו מספר נחשב גוף — "לא הצלחנו לקרוא" אינו "אין גוף" (עיקרון 5)."""
    if _values(scope, b"transfer-encoding"):
        return True
    for value in _values(scope, b"content-length"):
        try:
            if int(value) != 0:
                return True
        except ValueError:
            return True
    return False


def _endpoint(scheme: str, netloc: str) -> tuple[str, str, int] | None:
    """``(scheme, host, port)`` מנורמל, או ``None`` כשאי אפשר לפרק — ולא "תואם"."""
    scheme = scheme.lower()
    if scheme not in _DEFAULT_PORTS or not netloc or "@" in netloc:
        return None
    try:
        parts = urlsplit(f"{scheme}://{netloc}")
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:
        return None
    if not parts.hostname or parts.path or parts.query or parts.fragment:
        return None
    return scheme, parts.hostname.lower(), port


def _same_origin(scope, origins: list[str]) -> bool:
    """``Origin`` יחיד, שהוא בדיוק ה-scheme+host+port שהבקשה הגיעה אליהם.
    ‏``null``, ערך שאינו מתפרק, ``Host`` חסר או שני ערכים — אינם תואמים."""
    hosts = _values(scope, b"host")
    if len(origins) != 1 or len(hosts) != 1:
        return False
    origin = urlsplit(origins[0])
    if origin.path or origin.query or origin.fragment:
        return False
    theirs = _endpoint(origin.scheme, origin.netloc)
    ours = _endpoint(scope.get("scheme", ""), hosts[0])
    return theirs is not None and theirs == ours


def refusal(scope) -> tuple[int, str, str] | None:
    """``(status, code, message_he)`` לבקשה שנדחית, ‏``None`` לבקשה שעוברת."""
    if scope.get("method") not in UNSAFE_METHODS:
        return None
    # כל מופע של הכותרת נבדק — שני ערכים, אחד מהם זר, הם זר.
    sites = _values(scope, b"sec-fetch-site")
    if any(v.lower() not in ALLOWED_FETCH_SITES for v in sites):
        return 403, "cross_site", CROSS_SITE_HE
    # דפדפן בלי Fetch Metadata: ‏Origin, אם נשלח, חייב להיות הקונסולה עצמה.
    origins = _values(scope, b"origin")
    if not sites and origins and not _same_origin(scope, origins):
        return 403, "cross_origin", CROSS_ORIGIN_HE
    types = _values(scope, b"content-type")
    if types:
        media = {t.split(";", 1)[0].strip().lower() for t in types}
        if len(types) != 1 or not media <= ALLOWED_BODY_TYPES:
            return 415, "body_type", BODY_TYPE_HE
    elif _has_body(scope):
        return 415, "body_type", BODY_TYPE_HE
    return None


def _with_frame_deny(headers) -> list:
    kept = [(k, v) for k, v in headers if k.lower() != b"x-frame-options"]
    return kept + [(b"X-Frame-Options", b"DENY")]


class ConsoleFetchGuard:
    """ה-middleware: ‏``app.add_middleware(ConsoleFetchGuard)`` בקונסולה בלבד."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        refused = refusal(scope)
        if refused is not None:
            status, code, message = refused
            body = json.dumps({"detail": message, "code": code}, ensure_ascii=False).encode("utf-8")
            await send({"type": "http.response.start", "status": status,
                        "headers": _with_frame_deny([
                            (b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode("ascii"))])})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_frame_deny(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": _with_frame_deny(message.get("headers", []))}
            await send(message)

        await self.inner(scope, receive, send_frame_deny)
