"""#1973: הגדרת ה-UPS מהקונסולה — GET/POST /api/console/ups (admin)."""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from server.console import auth
from server.system import ups
from server.db import get_setting, journal

log = logging.getLogger("imagesrv.console_ups")
CONFIG_FAILED = "הגדרת ה-UPS נכשלה ולא נשמרה — הפרטים ביומן השרת"


def create_ups_router(ctx, supplied_hooks: dict | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    hooks = {**ups.default_hooks(), **(supplied_hooks or {})}
    _current_user, admin_only = auth.dependencies(ctx.conn)

    def view() -> dict:
        mode, _target = ups.configured(ctx.conn)
        monitor = getattr(ctx, "ups", None)
        state, detail = monitor.health() if monitor is not None else (None, None)
        return {"mode": mode, "target": get_setting(ctx.conn, ups.TARGET) or "",
                "modes": ups.MODES_HE, "usb_target": ups.USB_TARGET,
                # None = אין מנטר בתהליך הזה (בדיקות/הרצה ישירה) — "לא נבדק", לא "תקין".
                "state": state, "detail": detail,
                "reading": monitor.snapshot() if monitor is not None else None}

    @router.get("/ups")
    def get_ups(user=Depends(admin_only)):
        return view()

    @router.post("/ups")
    def set_ups(body: dict = Body(default={}), user=Depends(admin_only)):
        try:
            result = ups.apply(ctx.conn, str(body.get("mode", "")), body.get("target", ""),
                               hooks, user[0])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — ההחלה נכשלה: נאמר, ולא נשמר
            # #2510: טקסט החריגה (systemctl, כתיבת קובץ) ליומן השרת; ללקוח — משפט קבוע.
            # #2543: גם ביומן האירועים (מוצג בקונסולה) — המשפט שלנו; הטקסט ביומן השרת בלבד.
            log.warning("ups config failed: %s", exc)
            journal(ctx.conn, "ups_config_failed", CONFIG_FAILED, user[0])
            raise HTTPException(500, CONFIG_FAILED) from exc
        monitor = getattr(ctx, "ups", None)
        if monitor is not None:
            monitor.tick()       # המצב החדש נקרא עכשיו, לא בדגימה הבאה
        return {**view(), "applied": result}

    return router
