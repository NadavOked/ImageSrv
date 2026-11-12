"""‏#2843: הפעלה מחדש וכיבוי של השרת מהקונסולה — ‏``POST /api/console/server-power``.

מנהל בלבד (deploy → 403), כמו ‏F12 במסך השרת (``dcui.actions.power_action``).
עד כאן זה היה אפשרי רק משם.

**הפעולה עוברת דרך hook אחד, ``schedule(action)``** — ``None`` בהצלחה, אחרת
הודעת שגיאה. ‏``server.main`` מתקין את האמיתי (``schedule_later``), ורק הוא:
בלי hook מוזרק הנתיב עונה ‏503, כדי שאף הרצת בדיקות לא תגיע ל-``systemctl
poweroff`` של המכונה שהיא רצה עליה (כמו ``_start_ups_monitor``).

**התשובה יוצאת לפני שהשרת יורד:** ``systemctl reboot|poweroff`` רץ ביחידת
systemd חולפת עם ``--on-active`` — אותה דרך כמו ``deploy_net.restart_server_later``
ו-``update.py``. תהליך-ילד שלנו היה נהרג יחד עם השירות, ו-``sleep`` בתהליכון
היה מחזיק את התשובה. זו גם ההרשאה: היחידה רצה כ-root (אין ``User=``), כמו
ה-``systemctl restart dnsmasq`` של לשונית הרשת ו-``auto_reboot.default_reboot``,
ולכן אין כאן sudoers ואין polkit.

**סבב או משימה פעילים** (``update.restart_blocker`` — אותה הגדרה כמו כפתור
העדכון, ‏#1891): ‏409 ``{code: "power_busy", busy}``, אלא אם המנהל ראה את
האזהרה ולחץ על הכפתור האדום (``force: true``). השרת מכריע, לא הרשימה שבדפדפן:
רשימה שנטענה לפני דקה אינה ראיה שאין עבודה (עיקרון 5).
"""

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from server.console import auth
from server.db import journal
from server.net import deploy_net
from server.update import restart_blocker

ACTIONS = {"reboot": "הפעלה מחדש", "poweroff": "כיבוי"}
VERB = {"reboot": "ההפעלה מחדש", "poweroff": "הכיבוי"}
#: כמה שניות אחרי התשובה. מספיק כדי שהתשובה תגיע לדפדפן; לא מספיק כדי
#: שמישהו יתחיל משהו חדש באמצע.
DELAY_SECONDS = 3


def schedule_later(action: str, delay: int = DELAY_SECONDS) -> str | None:
    """``systemctl <action>`` בעוד ``delay`` שניות, ביחידה חולפת בשם ייחודי
    (‏#1121: שם קבוע שעוד ממתין מכשיל את הבקשה הבאה ב-"Unit already exists")."""
    unit = f"imagesrv-power-{uuid.uuid4().hex[:12]}"
    ok, out, err = deploy_net._run(["systemd-run", f"--on-active={delay}",
                                    f"--unit={unit}", "--collect",
                                    "systemctl", action], timeout=15)
    return None if ok else f"systemd-run נכשל: {err or out.strip()}"


def create_server_power_router(ctx, schedule=None, busy_source=None) -> APIRouter:
    """``schedule`` — ה-hook (``None`` = לא הותקן → ‏503). ‏``busy_source`` — מה
    ש-``restart_blocker`` שואל (בשרת: ה-runtime, שנושא גם את ``vmupdate``)."""
    router = APIRouter(prefix="/api/console")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    @router.post("/server-power")
    def server_power(body: dict = Body(default={}), user=Depends(admin_only)):
        action = body.get("action") if isinstance(body, dict) else None
        if not isinstance(action, str) or action not in ACTIONS:
            raise HTTPException(400, "פעולה לא מוכרת — reboot או poweroff")
        force = isinstance(body, dict) and body.get("force") is True
        blocker = restart_blocker(busy_source or ctx)
        if blocker and not force:
            return JSONResponse(status_code=409, content={
                "detail": f"{blocker} — {VERB[action]} יקטע את העבודה",
                "code": "power_busy", "busy": blocker})
        if schedule is None:
            journal(ctx.conn, "server_power_failed", f"{action}: לא הותקן מנגנון", user[0])
            raise HTTPException(503, "השרת הזה רץ בלי מנגנון הפעלה מחדש וכיבוי "
                                     "(הרצת פיתוח או בדיקה) — שום דבר לא בוצע")
        detail = action + (f" · נקטע: {blocker}" if blocker else "")
        error = schedule(action)
        if error:
            journal(ctx.conn, "server_power_failed", f"{detail}: {error}", user[0])
            raise HTTPException(500, f"{ACTIONS[action]} השרת לא תוזמן: {error}")
        journal(ctx.conn, "server_power", detail, user[0])
        return {"ok": True, "action": action, "in_seconds": DELAY_SECONDS,
                "interrupted": blocker}

    return router
