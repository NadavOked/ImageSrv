"""גוף ה-JSON של בקשות הסוכן — נקרא עם תקרה, נפרס בלי לזרוק (#2410, #2416).

רשת ההפצה חצי-אמינה: כל מחשב שמחובר אליה מגיע לפורט 8080. עד כאן כל נתיב
ב-`api.py` קרא `await request.json()` על הגוף **כולו**, לפני כל בדיקה — גם לפני
שער הזהות. 200MB הגיעו לשיא RSS של 621MB, ושש בקשות של 100MB במקביל ל-1,183MB
(מדידה 04/11, שרת פיתוח מקומי). ושני גופים קטנים הפכו ל-500 עם traceback:
JSON מקונן לעומק 10,000 (`RecursionError`) ו-surrogate בודד בשדה שנשמר ל-DB
(`UnicodeEncodeError` מ-sqlite).

**הכלל:** הגוף נקרא כזרם עם מונה — `Content-Length` גדול מהתקרה נדחה לפני
שנקרא בייט, וגוף chunked נעצר ברגע שעבר אותה (413 `payload_too_large`). JSON
פגום, עמוק מ-`MAX_DEPTH` או כזה שהמפענח קרס עליו — 400 `bad_json`. מחרוזת עם
surrogate בודד (`"\\ud800"`, שאין לו קידוד UTF-8) מוחלפת ב-`?` — אותו נרמול
של `errors="replace"`, בכל מחרוזת ובכל מפתח, לפני שהגוף מגיע לקוד שכותב ל-DB.
הסוכן שלנו שולח UTF-8 תקין, ולכן אצלו הנרמול אינו משנה דבר.

`BodyCap` (middleware של ASGI) מחיל את אותה תקרה על **כל** נתיבי הכתיבה של
אפליקציית הסוכן, גם אלה שאינם עוברים ב-`read_json` (#2410). המודול אינו כותב
יומן (גוף ענק או פגום מגיע ממי שעוד לא עבר את שער הזהות).
"""

from __future__ import annotations

import json
import math
import re

from starlette.responses import JSONResponse

#: התקרה לגוף JSON אחד מהסוכן. hello הוא הגדול שבהם: ארבעת השדות שנשמרים
#: כ-JSON חסומים ב-64KB כל אחד (`hello.HELLO_FIELD_MAX_BYTES`), ועוד המלאי
#: (`disks`) של משכפל — קילובייטים. 1MiB הוא פי ארבעה מהגרוע שבתקרות השדות.
MAX_BYTES = 1024 * 1024

#: עומק הקינון המרבי של הגוף כולו (מילון/רשימה = רמה). ה-hello העמוק ביותר
#: של הסוכן הוא ~6 (`probe.disks[].nvme_smart`), ו-`HELLO_FIELD_MAX_DEPTH` = 8
#: לשדה. 32 רחוק משניהם, וקרוב מספיק ל-0 כדי שאף רקורסיה בהמשך לא תתקרב
#: למגבלת המפרש (`hello._json_depth` רקורסיבית).
MAX_DEPTH = 32


class BodyError(Exception):
    """גוף שאין להמשיך איתו. `status`/`code`/`message` — התשובה בתבנית הרוחבית."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def read_json(request, limit: int = MAX_BYTES) -> object:
    """הגוף, מפוענח ומנורמל — או `BodyError`. לעולם אינו קורא יותר מ-`limit`+chunk."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise BodyError(413, "payload_too_large", "request body exceeds the size cap")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            raise BodyError(413, "payload_too_large", "request body exceeds the size cap")
    return parse(bytes(raw))


def _no_constant(name: str) -> float:
    """#2493: `NaN`/`Infinity` אינם JSON. `json.loads` מקבל אותם, `json.dumps` כותב אותם
    כמו שהם ל-DB, ו-`JSONResponse` מסרב להם — `GET /machines` כולו נפל ב-500 על שורה אחת."""
    raise ValueError(f"non-finite JSON constant {name}")


def _finite(text: str) -> float:
    """#2493: וכך גם `1e999` — מספר תקין בתחביר שהופך ל-`inf`."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def parse(raw: bytes) -> object:
    """bytes → ערך JSON שאפשר לשמור. `BodyError` 400 על כל דבר אחר."""
    try:
        value = json.loads(raw, parse_constant=_no_constant, parse_float=_finite)
    except RecursionError:
        raise BodyError(400, "bad_json", "body is nested too deeply") from None
    except ValueError:      # JSONDecodeError, וגם UnicodeDecodeError על bytes שאינם UTF-8
        raise BodyError(400, "bad_json", "body is not JSON") from None
    if _too_deep(value):
        raise BodyError(400, "bad_json", "body is nested too deeply")
    return _clean(value)


def _too_deep(value: object) -> bool:
    """איטרטיבי בכוונה: הבדיקה עצמה אינה יכולה ליפול על הקלט שהיא בודקת."""
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > MAX_DEPTH:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def _text(value: str) -> str:
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:         # surrogate בודד — אין לו קידוד, ו-sqlite נופל עליו
        return value.encode("utf-8", "replace").decode("utf-8")


def _clean(value: object) -> object:
    """רקורסיבי — בטוח, כי `_too_deep` כבר חסם עומק מעל `MAX_DEPTH`."""
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return {_text(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


#: #2410: הנתיבים היחידים בפורט הסוכן שגופם **אינו** JSON — נתוני האימג' עצמם
#: (`capture.py`, `stream_to`: נכתבים לדיסק כזרם, עם sha256 ובדיקת מקום). כל
#: נתיב כתיבה אחר באפליקציית הסוכן חסום ב-`MAX_BYTES`. נתיב העלאה חדש נכנס כאן
#: במפורש, ו-`tests/test_agent_body_cap_2410.py` נופל על כל נתיב שאינו כאן ואינו חסום.
STREAM_ROUTES = (
    ("PUT", re.compile(r"^/api/v1/capture/[^/]+/files/[^/]+$")),
)


def is_stream(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in STREAM_ROUTES)


class BodyCap:
    """#2410: תקרת הגוף של **כל** אפליקציית הסוכן (`app.create_agent_app`, :8080).

    `read_json` מגן על הנתיבים ש-`api.py` מפרסר בעצמו, אבל בפורט הסוכן יושבים
    גם הקיוסק של מחשב הבנייה (`/api/console/{login,sessions,room…}` — אותם
    route-ים של הקונסולה), `station.py`, `cloner_round.py`, והמניפסטים של
    הקליטה וההפצה הישירה — וכל אחד מהם קרא `request.json()`/`request.body()`
    על הגוף כולו. שער אחד לפני הניתוב: `Content-Length` גדול מהתקרה — 413
    בלי לקרוא; אחרת הגוף נקרא כאן עד התקרה (ולא בייט מעבר לחתיכה שחצתה אותה),
    ונמסר לאפליקציה מהזיכרון. GET/HEAD/OPTIONS ו-`STREAM_ROUTES` עוברים כמו שהם."""

    def __init__(self, app, limit: int = MAX_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send) -> None:
        if (scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS")
                or is_stream(scope["method"], scope["path"])):
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or ()).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.limit:
            await _too_large(scope, receive, send)
            return
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return                              # הלקוח עזב — אין למי לענות
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.limit:
                await _too_large(scope, receive, send)
                return
            chunks.append(chunk)
            more = message.get("more_body", False)
        replay = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]

        async def replayed():
            return replay.pop() if replay else await receive()

        await self.app(scope, replayed, send)


async def _too_large(scope, receive, send) -> None:
    await JSONResponse({"ok": False, "error": "request body exceeds the size cap",
                        "code": "payload_too_large"}, status_code=413)(scope, receive, send)


__all__ = ["BodyCap", "BodyError", "MAX_BYTES", "MAX_DEPTH", "STREAM_ROUTES", "is_stream",
           "parse", "read_json"]
