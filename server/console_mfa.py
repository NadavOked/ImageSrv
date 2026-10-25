"""MFA (TOTP) לקונסולה — #1085.

setup → verify → enable. קוד גיבוי חד-פעמי. disable על ידי admin.
המקומי (is_builtin) אינו יכול להפעיל MFA.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import string
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import auth, login_guard, totp, users
from .api import ServerContext
from .db import _write_lock, get_setting, journal, now_iso, writing

#: ‏נדב 18/10: "תכלס לא צריך לזה MFA. אם בנוי, אפשר להפעיל או לא". ‏"true" = כל מנהל
#: שאינו המקומי (is_builtin) חייב לרשום MFA בכניסה ובהחלפת סיסמה — ההתנהגות שהייתה
#: עד rc6 לכולם. ברירת המחדל "false" (‏db.DEFAULT_SETTINGS): אין חיוב; מי שהפעיל MFA
#: ממשיך להתבקש לקוד, ומ"החשבון שלי" אפשר להפעיל או לבטל (‏/me/mfa/disable, עם קוד).
SETTING = "mfa_required_for_admins"

#: הסירוב לביטול עצמי כשהחיוב דלוק — אחרת הכניסה הבאה הייתה מחייבת רישום מחדש.
SELF_DISABLE_REQUIRED_HE = ("אימות דו-שלבי חובה למנהלים בשרת הזה — כדי לבטל אותו לעצמך, "
                            "כבו קודם את \"חיוב אימות דו-שלבי למנהלים\" בהגדרות ← אבטחה")


def mfa_demanded(conn: sqlite3.Connection, info: dict) -> bool:
    """האם MFA חובה למשתמש הזה: החיוב הכללי למנהלים דלוק, או שסומן לו "דרוש אימות
    דו-שלבי" (נדב 18/10, ‏users.mfa_required)."""
    return get_setting(conn, SETTING) == "true" or bool(info.get("mfa_required"))


def enrollment_required(conn: sqlite3.Connection, role: str, info: dict) -> bool:
    """האם המשתמש חייב לרשום MFA לפני session מלאה. רק מנהל (ל-deploy אין קונסולה,
    ו-MFA הוא למנהל בלבד), לא המקומי (is_builtin), ורק כש-MFA חובה לו."""
    return (role == "admin" and not info["is_builtin"] and not info["mfa_enabled"]
            and mfa_demanded(conn, info))


#: ‏409 כשסומן למשתמש "דרוש אימות דו-שלבי" (נדב 18/10).
SELF_DISABLE_FLAGGED_HE = ('למשתמש שלך מסומן "דרוש אימות דו-שלבי" — מנהל יכול להסיר את הסימון'
                           ' בדף "משתמשים והרשאות"')
#: ‏400 לאיפוס של עצמך מדף המשתמשים.
RESET_SELF_HE = 'את האימות הדו-שלבי שלך מבטלים ב"החשבון שלי" (עם הסיסמה וקוד)'

CHALLENGE_TTL = 5 * 60
#: ‏#2101: כמה זמן setup שלא אושר מחזיר את אותו סוד ממתין. אחרי זה — סוד חדש.
PENDING_TTL = 15 * 60
#: ‏#2101: מתי הוגרל הסוד הממתין של כל משתמש. בזיכרון ולא בעמודה: בלי שינוי
#: סכמה, ואתחול שרת באמצע הגדרה פשוט מגריל סוד חדש — כמו לפני התיקון.
_pending_since: dict[str, float] = {}
_now = time.time
BACKUP_COUNT = 8
BACKUP_LEN = 10
BACKUP_ALPHABET = string.ascii_uppercase + string.digits


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_backup(code: str) -> str:
    salt = secrets.token_hex(8)
    digest = hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()
    return f"{salt}${digest}"


def _backup_matches(code: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    guess = hashlib.sha256(f"{salt}:{code.strip().upper()}".encode()).hexdigest()
    return hmac.compare_digest(guess, digest)


def make_challenge(conn: sqlite3.Connection, username: str) -> str:
    raw = secrets.token_urlsafe(32)
    expires = time.time() + CHALLENGE_TTL
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO mfa_challenges (token_hash, username, expires_at)"
            " VALUES (?, ?, ?)",
            (_hash_token(raw), username, expires),
        )
    return raw


def _challenge_user(conn: sqlite3.Connection, raw: str) -> str | None:
    row = conn.execute(
        "SELECT username, expires_at FROM mfa_challenges WHERE token_hash = ?",
        (_hash_token(raw or ""),),
    ).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    return row["username"]


def consume_challenge(conn: sqlite3.Connection, raw: str) -> None:
    with _write_lock, writing(conn):
        conn.execute(
            "DELETE FROM mfa_challenges WHERE token_hash = ?",
            (_hash_token(raw or ""),),
        )


def _new_backup_codes(conn: sqlite3.Connection, username: str) -> list[str]:
    codes = [
        "".join(secrets.choice(BACKUP_ALPHABET) for _ in range(BACKUP_LEN))
        for _ in range(BACKUP_COUNT)
    ]
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM mfa_backup_codes WHERE username = ?", (username,))
        conn.executemany(
            "INSERT INTO mfa_backup_codes (username, code_hash) VALUES (?, ?)",
            [(username, _hash_backup(c)) for c in codes],
        )
    return codes


def consume_backup(conn: sqlite3.Connection, username: str, code: str) -> bool:
    rows = conn.execute(
        "SELECT code_hash, used_at FROM mfa_backup_codes WHERE username = ?",
        (username,),
    ).fetchall()
    for row in rows:
        if row["used_at"]:
            continue
        if _backup_matches(code, row["code_hash"]):
            with _write_lock, writing(conn):
                cur = conn.execute(
                    "UPDATE mfa_backup_codes SET used_at = ?"
                    " WHERE username = ? AND code_hash = ? AND used_at IS NULL",
                    (now_iso(), username, row["code_hash"]),
                )
            return cur.rowcount == 1
    return False


def _qr_svg(otpauth_url: str) -> str:
    """‏QR של ה-URI כ-SVG inline (בלי `<?xml`), ל-`innerHTML` של הקונסולה.

    ‏#1150: הייבוא בתוך הפונקציה — ‏python3-qrcode (דביאן 13: 8.2) מגיע
    מהמתקין, ושרת שהותקן לפניו לא ייפול בכניסה; הוא מחזיר ‏`""` והקורא
    אומר זאת בשם. ‏`SvgPathImage` אינו צריך PIL.
    """
    try:
        import qrcode
        import qrcode.image.svg as qsvg
    except ImportError:
        return ""
    img = qrcode.make(otpauth_url, image_factory=qsvg.SvgPathImage)
    return img.to_string(encoding="unicode")


def register(router: APIRouter, ctx: ServerContext, current_user, admin_only, tls) -> None:
    """מוסיף את נתיבי ה-MFA לראוטר הקונסולה הקיים — מיזוג קל מול #1073."""

    @router.post("/me/mfa/setup")
    def mfa_setup(user=Depends(current_user)):
        username, role = user
        info = users.flags(ctx.conn, username)
        if info["is_builtin"]:
            raise HTTPException(403, "builtin_no_mfa")
        if role != "admin":
            raise HTTPException(403, "MFA למנהל בלבד")
        # ‏#2101: setup שעוד לא אושר מחזיר את **אותו** סוד ממתין עד `PENDING_TTL` —
        # רענון הדף אחרי סריקת ה-QR לא ינתק את מה שהטלפון כבר שמר. ההחלטה בתוך
        # נעילת הכתיבה, כדי ששתי קריאות במקביל לא יגרילו שני סודות.
        now = _now()
        with _write_lock, writing(ctx.conn):
            row = ctx.conn.execute(
                "SELECT mfa_secret, mfa_enabled FROM users WHERE username = ?", (username,),
            ).fetchone()
            since = _pending_since.get(username)
            if (row is not None and not row["mfa_enabled"] and row["mfa_secret"]
                    and since is not None and 0 <= now - since < PENDING_TTL):
                secret = row["mfa_secret"]
            else:
                secret = totp.new_secret()
                ctx.conn.execute(
                    "UPDATE users SET mfa_secret = ?, mfa_enabled = 0,"
                    " mfa_enrolled_at = NULL WHERE username = ?",
                    (secret, username),
                )
                _pending_since[username] = now
        url = totp.otpauth_url(username, secret)
        svg = _qr_svg(url)
        # ‏#1150: "אין חבילה" נאמר בשם ביומן ובקונסולה — לא נקפל ל"הצלחה"
        # (עיקרון 5). ההרשמה עצמה ממשיכה: הסוד וה-URI מספיקים להקלדה ידנית.
        journal(ctx.conn, "mfa_setup",
                "QR SVG" if svg else
                "python3-qrcode חסר בשרת — בלי QR, URI להקלדה ידנית",
                username)
        return {"secret": secret, "otpauth_url": url, "svg": svg}

    @router.post("/me/mfa/verify")
    async def mfa_verify(request: Request, user=Depends(current_user)):
        body = await request.json()
        username = user[0]
        info = users.flags(ctx.conn, username)
        if info["is_builtin"]:
            raise HTTPException(403, "builtin_no_mfa")
        if not info["mfa_secret"]:
            raise HTTPException(400, "יש לקרוא ל-setup קודם")
        if totp.verify_totp(info["mfa_secret"], body.get("code", "")) is None:
            raise HTTPException(401, "קוד שגוי, נסה שוב")
        return {"verified": True}

    @router.post("/me/mfa/enable")
    async def mfa_enable(request: Request, response: Response,
                         user=Depends(current_user)):
        body = await request.json()
        username, role = user
        info = users.flags(ctx.conn, username)
        if info["is_builtin"]:
            raise HTTPException(403, "builtin_no_mfa")
        if not info["mfa_secret"]:
            raise HTTPException(400, "יש לקרוא ל-setup קודם")
        step = totp.verify_totp(info["mfa_secret"], body.get("code", ""))
        if step is None:
            raise HTTPException(401, "קוד שגוי, לא ניתן להפעיל MFA")
        codes = _new_backup_codes(ctx.conn, username)
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE users SET mfa_enabled = 1, mfa_enrolled_at = ?"
                " WHERE username = ?",
                (now_iso(), username),
            )
        _pending_since.pop(username, None)   # #2101: אושר — אין עוד סוד ממתין
        journal(ctx.conn, "mfa_enabled", username, username)
        auth.attach_cookie(response, ctx.conn, username, role, tls)
        return {"mfa_enabled": True, "backup_codes": codes}

    @router.post("/me/mfa/disable")
    async def mfa_disable_self(request: Request, response: Response,
                               user=Depends(current_user)):
        """נדב 18/10: מי שהפעיל MFA לעצמו יכול לבטל אותו — עם **הסיסמה הנוכחית וקוד**
        (TOTP או קוד גיבוי). כשהחיוב דלוק — ‏409: ביטול היה מוביל לרישום כפוי בכניסה הבאה.

        סקירת האבטחה על 7534e0e7: בלי הגבלה, session גנוב יכול לנחש קוד עד שיצליח.
        עכשיו אותו מונה ואותם ספים כמו ב-`/login/mfa` (‏login_guard — שם משתמש + IP):
        בדיקה לפני כל אימות, כשל נספר (סיסמה או קוד), הצלחה מאפסת; בזמן נעילה גם
        סיסמה וקוד נכונים נדחים."""
        body = await request.json()
        username, role = user
        info = users.flags(ctx.conn, username)
        if info["is_builtin"]:
            raise HTTPException(403, "builtin_no_mfa")
        if not info["mfa_enabled"] or not info["mfa_secret"]:
            raise HTTPException(400, "MFA אינו מופעל")
        if get_setting(ctx.conn, SETTING) == "true":
            raise HTTPException(409, SELF_DISABLE_REQUIRED_HE)
        if info.get("mfa_required"):
            raise HTTPException(409, SELF_DISABLE_FLAGGED_HE)
        ip = request.client.host if request.client else "?"
        try:
            login_guard.check(ctx.conn, username, ip)
        except login_guard.LoginBlocked as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else {}
            raise HTTPException(exc.status, exc.message, headers=headers)
        password_ok = users.verify(ctx.conn, username, str(body.get("current_password") or "")) is not None
        code = str(body.get("code") or "")
        step = totp.verify_totp(info["mfa_secret"], code) if password_ok else None
        replay = step is not None and info["mfa_last_step"] is not None and step == info["mfa_last_step"]
        # קוד גיבוי נצרך רק אחרי סיסמה נכונה — ניחוש סיסמה אינו שורף קודים.
        if not password_ok or ((step is None or replay)
                               and not consume_backup(ctx.conn, username, code)):
            login_guard.record_failure(ctx.conn, username, ip)
            journal(ctx.conn, "mfa_disable_failed", username, username)
            raise HTTPException(401, "סיסמה או קוד שגויים — MFA לא בוטל")
        login_guard.clear(ctx.conn, username, ip)
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE users SET mfa_enabled = 0, mfa_secret = NULL,"
                " mfa_enrolled_at = NULL, mfa_last_step = NULL WHERE username = ?",
                (username,),
            )
            ctx.conn.execute("DELETE FROM mfa_backup_codes WHERE username = ?", (username,))
        journal(ctx.conn, "mfa_disabled", username, username)
        auth.attach_cookie(response, ctx.conn, username, role, tls)
        return {"ok": True, "mfa_enabled": False}

    @router.post("/users/{target}/mfa/disable")
    def mfa_disable(target: str, user=Depends(admin_only)):
        """נדב 18/10: "איפוס אימות דו-שלבי" למשתמש אחר — הסוד, ההפעלה וקודי הגיבוי נמחקים,
        וכל ה-sessions שלו מבוטלים (bump_auth_epoch): הוא נכנס מחדש — עם רישום MFA אם
        הוא חובה לו (`mfa_demanded`), אחרת בסיסמה בלבד. יומן `mfa_reset` (מי, למי, מתי)."""
        actor = user[0]
        if target.strip().lower() == actor.lower():
            raise HTTPException(400, RESET_SELF_HE)
        row = users.lookup(ctx.conn, target)
        if row is None:
            raise HTTPException(404, "משתמש לא קיים")
        if row["is_builtin"]:
            raise HTTPException(400, "לחשבון המקומי אין MFA")
        stored = row["username"]
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE users SET mfa_enabled = 0, mfa_secret = NULL,"
                " mfa_enrolled_at = NULL, mfa_last_step = NULL"
                " WHERE username = ?",
                (stored,),
            )
            ctx.conn.execute(
                "DELETE FROM mfa_backup_codes WHERE username = ?", (stored,),
            )
        users.bump_auth_epoch(ctx.conn, stored)
        journal(ctx.conn, "mfa_reset", stored, actor)
        return {"ok": True, "mfa_enabled": False}

    @router.post("/login/mfa")
    async def login_mfa(request: Request, response: Response):
        body = await request.json()
        raw = body.get("challenge") or ""
        code = body.get("code") or ""
        auth.assert_secret(ctx.conn)
        stored = _challenge_user(ctx.conn, raw)
        if stored is None:
            raise HTTPException(401, "אתגר MFA פג או שגוי")
        # ‏#1120: אותו מונה ואותם ספים כמו שלב הסיסמה — שם משתמש + IP.
        # ‏`/login` אינו מאפס אותו למשתמש MFA (ראו console_login), אחרת
        # "סיסמה נכונה → קוד שגוי → כניסה מחדש" היה מאפס בכל סיבוב.
        ip = request.client.host if request.client else "?"
        try:
            login_guard.check(ctx.conn, stored, ip)
        except login_guard.LoginBlocked as exc:
            headers = {}
            if exc.retry_after is not None:
                headers["Retry-After"] = str(exc.retry_after)
            raise HTTPException(exc.status, exc.message, headers=headers)
        info = users.flags(ctx.conn, stored)
        if not info["mfa_enabled"] or not info["mfa_secret"]:
            raise HTTPException(401, "MFA אינו מופעל")
        step = totp.verify_totp(info["mfa_secret"], code)
        used_backup = False
        if step is None and consume_backup(ctx.conn, stored, code):
            used_backup = True
        elif step is None or (info["mfa_last_step"] is not None
                              and step == info["mfa_last_step"]):
            # ‏#1120: ה-challenge חד-פעמי גם בכישלון — ניחוש שני דורש
            # סיסמה מחדש. הכישלון נספר, והמסך חוזר לכניסה.
            consume_challenge(ctx.conn, raw)
            login_guard.record_failure(ctx.conn, stored, ip)
            journal(ctx.conn, "login_failed", f"{stored} (mfa)")
            raise HTTPException(401, "קוד שגוי — היכנס מחדש")
        consume_challenge(ctx.conn, raw)
        login_guard.clear(ctx.conn, stored, ip)
        if step is not None:
            with _write_lock, writing(ctx.conn):
                ctx.conn.execute(
                    "UPDATE users SET mfa_last_step = ? WHERE username = ?",
                    (step, stored),
                )
        row = users.lookup(ctx.conn, stored)
        auth.attach_cookie(response, ctx.conn, stored, row["role"], tls)
        users.record_login(ctx.conn, stored, ip)
        journal(ctx.conn, "login", "mfa_backup" if used_backup else "mfa", stored)
        idle = int(get_setting(ctx.conn, "console_idle_seconds") or 600)
        return {"username": stored, "role": row["role"], "idle_seconds": idle}

    @router.post("/sessions/revoke")
    async def revoke_sessions(request: Request, user=Depends(admin_only)):
        body = await request.json()
        target = (body.get("username") or "").strip()
        row = users.lookup(ctx.conn, target)
        if row is None:
            raise HTTPException(404, "משתמש לא קיים")
        users.bump_auth_epoch(ctx.conn, row["username"])
        journal(ctx.conn, "sessions_revoked", row["username"], user[0])
        return {"ok": True}
