"""#1920 -- תשתית ← דיסק-און-קי: הדיסק-און-קי של השיכפול כשהוא מחובר פיזית לשרת.

למנהל בלבד (`admin_only`); משתמש הפצה מקבל 403 גם מ-`console_only` ב-app.py. אין קליטה אוטומטית
בחיבור: ‏`GET /usb-stick` רק קורא (ההתקן מעוגן לקריאה בלבד), ו-`POST /usb-stick/sync` כותב — ורק עם
ה-`digest` של הסיכום שהמנהל ראה ואישר, ורק אם ההתקן והנתונים עדיין כפי שהוצגו. מה זה מזהה, איך זה
נקרא ומה נכתב: `server/usbstick/`.

ההורדה של קובץ ה-USB (‏#1914) נשארת ב-`console_usb` (`/usb-clone*`); העמוד הזה רק מציג את הכפתור.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.agent.api import ServerContext
from server.console import auth
from server.usbstick import device, records, sync

log = logging.getLogger("imagesrv.console_usb_stick")


class SyncBody(BaseModel):
    digest: str
    confirm: bool = False


def _refuse(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status)


def create_usb_stick_router(ctx: ServerContext, hooks: dict | None = None,
                            version: Callable[[], str] = lambda: "") -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _current_user, admin_only = auth.dependencies(ctx.conn)
    h = {**device.default_hooks(), **(hooks or {})}

    def status() -> dict:
        scan = h["scan"]()
        out: dict = {"state": scan.state, "reason": scan.reason, "notes": scan.notes, "device": None,
                     "plan": None, "rows": [], "read_error": None, "last_sync": sync.last_sync(ctx.conn)}
        if scan.state != device.CONNECTED or scan.stick is None:
            return out
        out["device"] = scan.stick.public()
        try:
            plan = sync.plan(ctx.conn, h, scan.stick)
        except (device.StickError, records.ReadError) as exc:
            log.warning("usb stick %s: %s", scan.stick.device_id, exc)
            out["read_error"] = str(exc)
            return out
        out["plan"], out["rows"] = plan.public(), plan.rows()
        return out

    @router.get("/usb-stick")
    def usb_stick(user=Depends(admin_only)):
        if not sync.LOCK.acquire(blocking=False):
            return {"state": "busy", "reason": "ההתקן בשימוש כרגע (קריאה או סנכרון) — נסו שוב בעוד רגע",
                    "notes": [], "device": None, "plan": None, "rows": [], "read_error": None,
                    "last_sync": sync.last_sync(ctx.conn)}
        try:
            return status()
        finally:
            sync.LOCK.release()

    @router.post("/usb-stick/sync")
    def usb_stick_sync(body: SyncBody, user=Depends(admin_only)):
        if body.confirm is not True:
            return _refuse(400, "הסנכרון נכתב רק אחרי אישור המנהל על הסיכום", "confirm_required")
        scan = h["scan"]()
        if scan.state != device.CONNECTED or scan.stick is None:
            return _refuse(409, scan.reason or "הדיסק-און-קי כבר אינו מחובר", "usb_stick_" + scan.state)
        try:
            summary = sync.run(ctx.conn, h, scan.stick, body.digest, user[0], version() or "")
        except sync.Busy:
            return _refuse(409, "סנכרון אחר רץ עכשיו — המתינו שיסתיים", "usb_stick_busy")
        except sync.PlanChanged:
            return _refuse(409, "ההתקן או הנתונים השתנו מאז הסיכום — לא כתבתי כלום. לחצו \"סנכרן\" שוב וקראו את הסיכום החדש",
                           "usb_stick_plan_changed")
        except (device.StickError, records.ReadError) as exc:
            return _refuse(409, str(exc), "usb_stick_unreadable")
        return summary

    return router
