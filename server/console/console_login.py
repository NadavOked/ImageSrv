"""כניסת קונסולה — #1085 + #1073. מנותק מ-console_api כדי ששני המיזוגים יישארו ממוקדים.

הסדר אחרי סיסמה נכונה:
  (1) deploy בקונסולה → 403 deploy_no_console, לפני MFA/כפייה (#1073)
  (2) must_change_password / mfa_required / mfa_enrollment_required — או session מלאה
הקיוסק (`kiosk=True`) מקבל גם deploy, מדלג על MFA; החלפה כפויה → 403.
הגבלת ניסיונות (#1075) חלה על שלב הסיסמה לכל תפקיד.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from server.console import auth, console_mfa, login_guard, users
from server.db import get_setting, journal


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _idle(conn) -> int:
    return int(get_setting(conn, "console_idle_seconds") or 600)


def _raise_blocked(exc: login_guard.LoginBlocked) -> None:
    headers = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    raise HTTPException(exc.status, exc.message, headers=headers)


async def finish_login(conn, request: Request, response: Response, *,
                       tls, kiosk: bool = False):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    ip = _ip(request)
    auth.assert_secret(conn)
    try:
        login_guard.check(conn, username, ip)
    except login_guard.LoginBlocked as exc:
        _raise_blocked(exc)

    role = users.verify(conn, username, password)
    if role is None:
        login_guard.record_failure(conn, username, ip)
        journal(conn, "login_failed", username)
        raise HTTPException(401, "שם משתמש או סיסמה שגויים")

    stored = users.canonical_name(conn, username) or username
    info = users.flags(conn, stored)
    # ‏#1120: למשתמש MFA בקונסולה הסיסמה היא חצי כניסה — המונה מתאפס רק
    # ב-`/login/mfa`. אחרת קוד שגוי + כניסה מחדש
    # היו מאפסים את הספירה, ו-10 ניסיונות לא היו מצטברים לנעילה לעולם.
    if kiosk or not info["mfa_enabled"] or info["must_change_password"]:
        login_guard.clear(conn, username, ip)

    if role == "deploy" and not kiosk:
        # ‏#1073: הסיסמה נכונה — וזו בדיוק הסיבה שהתשובה אינה 401.
        # "בדקנו, ואתה לא נכנס מכאן" הוא מצב משלו (עיקרון 5), עם
        # הודעה שמסך הכניסה מציג כלשונה. לפני MFA/כפייה: להפצה אין
        # קונסולה בכלל. ‏JSONResponse ולא HTTPException: הגוף הוא חוזה
        # (`error` + `message_he`), לא `detail` חופשי.
        journal(conn, "login_refused_console", "deploy", stored)
        return JSONResponse(
            {"error": auth.DEPLOY_NO_CONSOLE,
             "message_he": auth.DEPLOY_NO_CONSOLE_HE}, status_code=403)

    if kiosk:
        if info["must_change_password"]:
            raise HTTPException(403, "החלף סיסמה בקונסולה קודם")
        # ‏#1340: בפורט הסוכן העוגייה מסומנת — סיסמה בלבד, ב-HTTP; הקונסולה מסרבת לה.
        purpose = auth.PURPOSE_KIOSK if getattr(
            request.app.state, auth.KIOSK_APP_FLAG, False) else ""
        auth.attach_cookie(response, conn, stored, role, tls, purpose=purpose)
        users.record_login(conn, stored, ip)
        journal(conn, "login", "kiosk", stored)
        # ‏#1499 (נדב 16/10): ערכת הנושא השמורה של המשתמש — ה-GUI הנייטיבי
        # של מחשב הבנייה נפתח בה. ‏auto/light/dark; ‏auto = ברירת המחדל (בהיר).
        return {"username": stored, "role": role, "idle_seconds": _idle(conn),
                "theme": users.get_theme(conn, stored)}

    if info["must_change_password"]:
        auth.attach_cookie(response, conn, stored, role, tls,
                           purpose=auth.PURPOSE_PWCHANGE)
        users.record_login(conn, stored, ip)
        journal(conn, "login", "must_change_password", stored)
        return {"must_change_password": True}

    if info["mfa_enabled"]:
        challenge = console_mfa.make_challenge(conn, stored)
        journal(conn, "login", "mfa_required", stored)
        return {"mfa_required": True, "challenge": challenge}

    if console_mfa.enrollment_required(conn, role, info):   # נדב 18/10: רק כשהחיוב דלוק
        auth.attach_cookie(response, conn, stored, role, tls,
                           purpose=auth.PURPOSE_MFAENROLL)
        users.record_login(conn, stored, ip)
        journal(conn, "login", "mfa_enrollment_required", stored)
        return {"mfa_enrollment_required": True}

    auth.attach_cookie(response, conn, stored, role, tls)
    users.record_login(conn, stored, ip)
    journal(conn, "login", "", stored)
    return {"username": stored, "role": role, "idle_seconds": _idle(conn)}
