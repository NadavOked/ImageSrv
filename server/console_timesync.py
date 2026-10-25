from fastapi import APIRouter, Body, Depends, HTTPException

from . import auth, timesync
from .db import get_setting, journal, set_setting


def create_timesync_router(ctx, supplied_hooks=None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    hooks = {**timesync.default_hooks(), **(supplied_hooks or {})}

    _current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/timesync")
    def get_timesync(user=Depends(admin_only)):
        return {"ntp_servers": timesync.configured(ctx.conn), "status": timesync.status(hooks)}

    @router.post("/timesync")
    def set_timesync(body: dict = Body(default={}), user=Depends(admin_only)):
        try:
            value = timesync.validate(body.get("ntp_servers", ""))
            measured = timesync.apply(value, hooks)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            journal(ctx.conn, "timesync_change_failed", str(exc), user[0])
            raise HTTPException(500, str(exc)) from exc
        old = get_setting(ctx.conn, timesync.SETTING) or ""
        set_setting(ctx.conn, timesync.SETTING, value)
        journal(ctx.conn, "timesync_changed", f"{old or 'אוטומטי'} → {value or 'אוטומטי'}", user[0])
        return {"ntp_servers": value, "status": measured}

    @router.post("/timesync/check")
    def check_timesync(user=Depends(admin_only)):
        try:
            return timesync.check_now(hooks)
        except TimeoutError as exc:
            raise HTTPException(504, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    return router
