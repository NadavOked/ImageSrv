"""#4041: ה-`?v=` של הקונסולה = גרסת המוצר, והשרת ממלא אותו בהגשה.

בקבצי המקור (`index.html`, `monitor.html`) יושב placeholder קבוע, ואף PR לא נוגע בו;
השרת מחליף אותו בגרסה של `/health/live` (`read_version`). גרסה חדשה = כתובות חדשות
בדפדפן. בעבר מונה ידני נספר בכל PR, והתנגש בכל PR אחר שנגע בקונסולה.

**גרסה שלא נקראה** (`version_error`) נופלת לערך שמשתנה בכל עלייה של השרת, ולעולם
לא לערך קבוע: ערך קבוע משאיר בדפדפן JS ישן מול API חדש (עיקרון 5, וה-gotcha של
המטמון ב-CLAUDE.md).
"""

import hashlib
import logging
import time
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Request, Response

from server.paths import STATIC_DIR

log = logging.getLogger("imagesrv.console_static_pages")

#: מה שיושב אחרי `?v=` בקבצי המקור. אין לשנות אותו בשום PR.
PLACEHOLDER = "__IMAGESRV_V__"


def boot_token() -> str:
    """ערך שמשתנה בכל עלייה (ננו-שניות), לגרסה שלא נקראה."""
    return f"boot{time.time_ns():x}"


def render(name: str, version: str | None, boot: str) -> bytes:
    """הדף עם ה-placeholder מוחלף: בגרסה, ואם אין — ב-`boot`."""
    raw = (STATIC_DIR / name).read_text(encoding="utf-8")
    return raw.replace(PLACEHOLDER, quote(version or boot, safe="")).encode("utf-8")


def create_static_pages_router(version: Callable[[], str | None]) -> APIRouter:
    """`/console/`, `/console/index.html`, `/console/monitor.html`. נרשם **לפני**
    ה-mount של `/console`, כדי שהוא יקבל אותם לפני `StaticFiles`."""
    router = APIRouter()
    boot = boot_token()

    def current() -> str | None:
        try:
            return version()
        except Exception:                              # noqa: BLE001
            # קריאת גרסה שהתפוצצה לא מפילה את הקונסולה: ממשיכים לערך הבוט.
            log.warning("console ?v=: the version read raised; using the boot value", exc_info=True)
            return None

    def serve(name: str, request: Request) -> Response:
        body = render(name, current(), boot)
        etag = f'"{hashlib.sha1(body).hexdigest()}"'   # noqa: S324 - not security, a validator
        headers = {"ETag": etag}
        if etag in (request.headers.get("if-none-match") or ""):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="text/html; charset=utf-8", headers=headers)

    @router.api_route("/console/", methods=["GET", "HEAD"], include_in_schema=False)
    def index_root(request: Request) -> Response:
        return serve("index.html", request)

    @router.api_route("/console/index.html", methods=["GET", "HEAD"], include_in_schema=False)
    def index_page(request: Request) -> Response:
        return serve("index.html", request)

    @router.api_route("/console/monitor.html", methods=["GET", "HEAD"], include_in_schema=False)
    def monitor_page(request: Request) -> Response:
        return serve("monitor.html", request)

    return router
