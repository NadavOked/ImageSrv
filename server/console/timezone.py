"""לשונית ההגדרות: אזור הזמן של השרת (‏#1447, הכרעת נדב 17/10).

אזור אחד משמש שלושה: התחנות (מחרוזת POSIX ב-hello, ``timezone.posix_tz``),
מסך השרת (DCUI) וזמני השרת המקומיים (הפעלה מחדש אוטומטית). לכן השינוי
הוא ``timedatectl set-timezone`` **וגם** ההגדרה ב-DB — לא אחד מהם: הגדרה
בלי המערכת הייתה משאירה את מסך השרת ב-UTC, ומערכת בלי הגדרה הייתה
נדרסת בעלייה הבאה (``timezone.apply_on_start``).

**עיקרון 5:** האזור "שונה" רק כשהמערכת מחזירה אותו בקריאה חוזרת, ורק אז
נכתבת ההגדרה. אזור שאינו ברשימת tzdata נדחה לפני שנוגעים במכונה. ה-DB
שומר UTC כמו תמיד — רק התצוגה מקומית. **admin בלבד.**

‏hooks (בדיקות מזריקות דרך ``host_hooks``, כמו ``console_hostcfg``):
‏``timezone()`` — האזור שהמערכת מדווחת (‏``None`` = לא נקרא);
‏``set_timezone(zone)`` — ‏``None`` בהצלחה, אחרת הודעת שגיאה.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from server.console import auth
from server.system import timezone
from server.agent.api import ServerContext
from server.db import journal, set_setting


def create_timezone_router(ctx: ServerContext, hooks: dict | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)
    hooks = {**timezone.default_hooks(), **(hooks or {})}

    def _zones() -> list[str]:
        try:
            return timezone.zones()
        except OSError as exc:
            raise HTTPException(500, f"רשימת אזורי הזמן לא נקראה: {exc}") from None

    @router.get("/timezone")
    def timezone_status(user=Depends(admin_only)):
        return {"zone": timezone.zone_name(ctx.conn), "system": hooks["timezone"](),
                "zones": _zones()}

    @router.post("/timezone")
    def timezone_change(body: dict = Body(default={}), user=Depends(admin_only)):
        zone = body.get("zone") if isinstance(body, dict) else None
        if not isinstance(zone, str) or zone.strip() not in _zones():
            raise HTTPException(400, f"אזור זמן לא מוכר: {zone!r}")
        zone = zone.strip()
        old = timezone.zone_name(ctx.conn)
        error = timezone.set_system_zone(zone, hooks)
        if error:
            journal(ctx.conn, "timezone_change_failed", f"{old} → {zone}: {error}", user[0])
            raise HTTPException(500, f"אזור הזמן לא שונה: {error}")
        set_setting(ctx.conn, timezone.SETTING, zone)
        journal(ctx.conn, "timezone_changed", f"{old} → {zone}", user[0])
        return {"ok": True, "zone": zone}

    return router
