"""‏allowlist מחשב הבנייה על פורט הסוכן (:8080) — #738, מצומצם ב-#1247.

עד #1247 היה כאן גם הקיוסק כאפליקציה נפרדת על :8082 עם דף web
(`/console/station`). הדף והמאזין הוסרו — מחשב הבנייה עולה לגואי
הנייטיב. **הראוטר נשאר**, כי הצרכן שלו הוא הסוכן: ‏buildmenu.sh/
roomflow.sh/הגואי נכנסים ל-`/api/console/login`, קולטים וניהול חדר
השיכפולים דרך `$SERVER` שהוא פורט הסוכן (ראה `_add_agent_routes`).

אסטרה (#703): **הגבול הוא ה-socket, לא הרשאת-route** — אפליקציית הניהול
המלאה לא מאזינה על כתובת שמחשב בנייה רואה, והסוכן מקבל רק רשימת-היתר.
טסט ה-allowlist מונה את הנתיבים ונופל אם נתיב ניהול הופיע.

הראוטרים המשותפים (console_api/console_library/capture) מכילים גם נתיבי
ניהול, ולכן **מסננים** מהם רק את הנתיבים שברשימה — אותם אובייקטי route
בדיוק, ולכן חוזה-התגובה זהה לזה של הקונסולה בלי שכפול לוגיקה.
`create_room_router`/`create_station_router` נכנסים בשלמותם."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute

from . import capabilities
from .api import ServerContext
from .capture import create_console_capture_router
from .console_api import create_console_router
from .console_library import create_library_router
from .room import create_room_router
from .station import create_station_router
from .tasks import ORIGIN_BUILD_GUI

#: רשימת-ההיתר הקשיחה לנתיבי ``/api/console`` שמחשב הבנייה צורך — (method, path).
#: נמדדה (#738) מקוד ה-JS של מסך התחנה בדפדפן, שהוסר (#1247). הצרכנים
#: היום הם הסוכן והקיוסק (`agent/lib`, דרך `console_get`/`console_post`):
#:  - login — כניסה (`buildmenu.sh`)
#:  - images — רשימת האימג'ים לסבב (`roomflow.sh`, ‏`guistate.sh`)
#:  - folders (GET+POST) — בחירת/יצירת תיקייה לפני קליטה (`buildcapture.sh`, ‏`guistate.sh`)
#:  - tasks/capture — הזמנת קליטה (`buildcapture.sh`)
#:  - sessions/{id}/start|close — סבב כיתה מהגואי (`guibridge.sh`, ‏class-start/class-close)
#:  - groups ו-POST sessions — **אין להם היום צרכן בסוכן** (זרימת הכיתה שלו
#:    עוברת ב-`/api/v1/agent/groups` ו-`/api/v1/agent/sessions`); groups אינו
#:    מוגש ב-v1 (‏`KIOSK_CLASS_ONLY_ROUTES`), ו-POST sessions מסרב 409 בלי כיתות.
#:  - POST images/{id}/system-partition — ‏#1896 (נדב 22/10): סימון C: באימג' ישן
#:    ממסך השחזור (`syspartgui.sh`); ‏deploy — רק אימג' שעוד לא סומן (כמו המשכפל).
#:  - PUT me/theme — ‏#1499 (נדב 16/10): החלפת ערכה בגואי נשמרת למשתמש עצמו
#:    (`guibridge.sh`, ‏theme-light|theme-dark) — גם ל-deploy. זו העדפה של
#:    המשתמש על עצמו, לא הגדרת ניהול; ‏`GET /me` נשאר מחוץ לרשימה.
#: כל נתיב ניהול אחר באותם ראוטרים **אינו** נכנס.
KIOSK_CONSOLE_ROUTES = frozenset({
    ("POST", "/api/console/login"),
    ("PUT", "/api/console/me/theme"),
    ("GET", "/api/console/groups"),
    ("GET", "/api/console/images"),
    ("GET", "/api/console/folders"),
    ("POST", "/api/console/folders"),
    ("POST", "/api/console/sessions"),
    ("POST", "/api/console/sessions/{session_id}/start"),
    ("POST", "/api/console/sessions/{session_id}/close"),
    ("POST", "/api/console/tasks/capture"),
    ("POST", "/api/console/images/{image_id}/system-partition"),   # #1896
})

#: הכרעת נדב 11/10: רשימת הכיתות של מסך התחנה שהוסר. כש-
#: ``capabilities.classrooms()`` כבוי (v1) הנתיב **אינו מוגש** על פורט
#: הסוכן — אי אפשר לסרב לו ב-route עצמו, כי אותו GET בקונסולה משרת את
#: המנהל גם ב-v1 (קבוצות בנייה/שיכפול). ‏``POST /sessions`` נשאר ברשימה:
#: הוא אותו route של הקונסולה, והשער (‏409) יושב בו. ‏start/close נשארים,
#: כי גל חדר השיכפולים הוא סבב רגיל ונסגר באותו endpoint.
KIOSK_CLASS_ONLY_ROUTES = frozenset({
    ("GET", "/api/console/groups"),
})


def _allowlisted(source: APIRouter, allow: frozenset[tuple[str, str]]) -> list[APIRoute]:
    """הנתיבים מ-`source` שברשימת-ההיתר — אובייקטי ה-route המקוריים.

    אותו endpoint, אותן dependencies, אותו response_model: החוזה זהה
    לזה של הקונסולה כי זה **אותו** route, לא עותק. נתיב שברשימה אך אינו
    קיים במקור פשוט לא יתווסף — הטסט של ה-allowlist תופס פער כזה בכך
    שהוא דורש שהקבוצה הממומשת תהיה שווה לרשימה המוצהרת.

    ‏#742: route רב-מתודי נכנס רק אם **כל** המתודות שלו ברשימה — אחרת
    ‏GET מותר היה מכניס איתו DELETE על אותו אובייקט. ‏fail-closed: route
    כזה נשמט כולו, והטסט של ה-allowlist נופל על ה-GET החסר — בקול."""
    picked: list[APIRoute] = []
    for route in source.routes:
        if isinstance(route, APIRoute) and route.methods and all(
            (method, route.path) in allow for method in route.methods
        ):
            picked.append(route)
    return picked


def mark_build_gui(request: Request) -> None:
    """‏#1576: בקשה שנכנסה בראוטר הזה על פורט הסוכן באה מהגואי של מחשב הבנייה.
    אותם אובייקטי route משרתים גם את הקונסולה (‎:8081), ולכן הסימון הוא
    dependency של **ההכללה** ב-`app._add_agent_routes`, לא של ה-route: משימה
    שהוזמנה כאן נרשמת `origin=build_gui` (`tasks.request_origin`) בלי קשר
    למה שהגוף אומר."""
    request.state.task_origin = ORIGIN_BUILD_GUI


def create_kiosk_router(ctx: ServerContext, *, room_wake, version=None) -> APIRouter:
    """הראוטר של הקיוסק: תת-קבוצת ``/api/console`` המסוננת + חדר + תחנה.

    ‏`room_wake` מוזרק (כמו ב-app.py) כדי ששליחת ה-WoL של החדר תהיה אותה
    פונקציה בכל המערכת וניתנת לזיוף בבדיקות."""
    router = APIRouter()
    allow = KIOSK_CONSOLE_ROUTES if capabilities.classrooms() \
        else KIOSK_CONSOLE_ROUTES - KIOSK_CLASS_ONLY_ROUTES
    # ‏#1073: ‏`kiosk=True` — הכניסה כאן מקבלת גם `deploy`. זו הדלת של
    # מחשב הבנייה (buildmenu.sh/ה-GUI נכנסים דרך `/api/console/login` על
    # פורט הסוכן, שנושא את אותו allowlist); הקונסולה (‎:8081) מסרבת לו.
    for source in (create_console_router(ctx, kiosk=True), create_library_router(ctx),
                   create_console_capture_router(ctx)):
        router.routes.extend(_allowlisted(source, allow))
    router.include_router(create_room_router(ctx, wake=room_wake, origin="build"))   # #1386
    router.include_router(create_station_router(ctx, version=version))   # #1252
    return router
