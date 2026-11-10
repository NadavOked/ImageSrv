from fastapi import APIRouter, Body, Depends, HTTPException

from server.console import auth
from server.system import timesync, timezone
from server.db import get_setting, journal


def create_timesync_router(ctx, supplied_hooks=None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    hooks = {**timesync.default_hooks(), **(supplied_hooks or {})}

    _current_user, admin_only = auth.dependencies(ctx.conn)

    def current() -> dict:
        return {"ntp_servers": timesync.configured(ctx.conn), "mode": timesync.mode(ctx.conn),
                "manual_at": timesync.manual_at(ctx.conn)}

    @router.get("/timesync")
    def get_timesync(user=Depends(admin_only)):
        return {**current(), "status": timesync.status(hooks)}

    @router.post("/timesync")
    def set_timesync(body: dict = Body(default={}), user=Depends(admin_only)):
        if body.get("mode") == timesync.MODE_MANUAL:   # #2765: no time server, the operator's time
            return set_manual(body, user)
        try:
            value = timesync.validate(body.get("ntp_servers", ""))
            measured = timesync.apply(value, hooks)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            journal(ctx.conn, "timesync_change_failed", str(exc), user[0])
            raise HTTPException(500, str(exc)) from exc
        old = describe_old()
        timesync.save(ctx.conn, value)
        journal(ctx.conn, "timesync_changed", f"{old} → {value or 'אוטומטי'}", user[0])
        return {**current(), "status": measured}

    def describe_old() -> str:
        if timesync.is_manual(ctx.conn):
            return "ידני"
        return (get_setting(ctx.conn, timesync.SETTING) or "") or "אוטומטי"

    def set_manual(body: dict, user) -> dict:
        zone = timezone.zone_name(ctx.conn)
        try:
            epoch = timesync.parse_local(body.get("time"), zone)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
        try:
            done = timesync.apply_manual(epoch, hooks)
            old = describe_old()
            timesync.save(ctx.conn, "", done["set_at"])
        except Exception as exc:
            journal(ctx.conn, "timesync_change_failed", str(exc), user[0])
            raise HTTPException(500, str(exc)) from exc
        journal(ctx.conn, "timesync_changed",
                f"{old} → ידני, {timesync.local_text(done['set_at'], zone)}"
                + ("" if done["rtc_written"] else " (שעון החומרה לא נכתב)"), user[0])
        return {**current(), "status": done["status"], "rtc_written": done["rtc_written"]}

    @router.post("/timesync/check")
    def check_timesync(user=Depends(admin_only)):
        try:
            return timesync.check_now(hooks)
        except TimeoutError as exc:
            raise HTTPException(504, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc

    return router
