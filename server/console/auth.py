"""אימות לקונסולה — cookie חתום ב-HMAC, בלי תלות חיצונית.

הטוקן: username|role|expiry חתומים בסוד שנולד בהתקנה. אין sessions
בזיכרון — שרת שקם מחדש לא מנתק אף אחד, והחתימה היא ההוכחה.

החתימה מוכיחה זהות בלבד. ההרשאה נקראת מטבלת המשתמשים בכל בקשה, כי
תפקיד שנחתם לפני שעתיים אינו התפקיד של עכשיו (#91).

בלי from __future__ import annotations — האנוטציה Request בתוך
dependencies() חייבת להיות אובייקט אמיתי בשביל FastAPI (אותו באג
שנתפס ב-boot/http.py).
"""

from typing import NamedTuple

import hashlib
import hmac
import sqlite3
import threading
import time

from server.db import _settle, _write_lock, get_setting, writing

COOKIE_NAME = "imagesrv_session"
#: #1179: הזהות של בקשה שהשרת הראשי שלח בערוץ הבין-שרתי (8443) — ``(user, role)``.
#: מפתח ב-scope של ASGI, שרק קוד בתוך התהליך יכול לשים: uvicorn בונה את ה-scope
#: מבקשת HTTP ואינו מעתיק לתוכו כותרות או עוגיות. ``interserver_api.run_console_get``
#: שם אותו רק אחרי mTLS + טוקן כרוך (``authenticate_parent``).
PEER_SCOPE_KEY = "imagesrv.peer_user"
TTL_SECONDS = 12 * 3600
PURPOSE_PWCHANGE = "pwchange"
PURPOSE_MFAENROLL = "mfaenroll"
#: #1340: עוגייה שהקיוסק (פורט הסוכן, :8080) הנפיק — סיסמה בלבד, בלי MFA,
#: ב-HTTP בוילן ההפצה. תקפה בפורט הסוכן; הקונסולה (``CONSOLE_APP_FLAG``) מסרבת לה.
PURPOSE_KIOSK = "kiosk"
#: #1340: ``app.state`` של אפליקציית הקונסולה (:8081) נושא את הדגל הזה, ושם
#: עוגיית קיוסק אינה session; של אפליקציית הסוכן (:8080) — ``KIOSK_APP_FLAG``,
#: ושם הכניסה מנפיקה עוגיית קיוסק. ``create_app`` המשולב (בדיקות) אינו נושא
#: אף אחד מהם, ושם ההתנהגות היא הישנה — הפיצול נבדק על שתי האפליקציות.
CONSOLE_APP_FLAG = "refuses_kiosk_sessions"
KIOSK_APP_FLAG = "issues_kiosk_sessions"

SECRET_KEY = "console_secret"
#: `db._initialize` זורע 32 בייטים. פחות מ-16 אינו סוד, הוא שריד.
MIN_SECRET_BYTES = 16

_HOW_TO_FIX = (
    "הסוד נולד פעם אחת באתחול בסיס הנתונים "
    "(\u200fsettings.console_secret) ואינו נכלל בגיבוי, ולכן אין מה לשחזר. "
    "כדי ליצור סוד חדש: עצרו את השרת, מחקו את השורה "
    "(DELETE FROM settings WHERE key='console_secret') והפעילו אותו "
    "מחדש — הוא יזרע סוד חדש. כל המחוברים ינותקו ויכנסו שוב; סיסמאות "
    "ו-MFA נשארים. זו החלטה של מפעיל ולא של השרת."
)


class SecretUnusable(RuntimeError):
    """הסוד שחותם את ה-cookies חסר או פגום.

    לא נתפס בשום מקום, בכוונה: "לא הצלחנו לקרוא את הסוד" איננו
    "אין משתמש מחובר", ובוודאי לא "יש לנו סוד". עיקרון 5.
    """


class Session(NamedTuple):
    username: str
    role: str
    purpose: str  # "" = מלא; pwchange / mfaenroll = מוגבל
    expiry: int = 0   # #2757: תפוגת העוגייה, ל-``session_renewal``
    epoch: int = 0    # #2757: ה-auth_epoch שבעוגייה — חידוש נושא אותו, לא קורא חדש


def _enforce_purpose(request, session: Session) -> None:
    from fastapi import HTTPException
    # #326: הנתיב שהניתוב הולך לפיו, לא ``request.url.path`` — זה נבנה מכותרת
    # Host, ו-starlette לא מוטלא (CVE-2026-48710) נותן ל-Host לשקר בו.
    path = request.scope["path"]
    method = request.method
    # #1127: משתמש שמתחרט באמצע החלפת סיסמה/רישום MFA חייב לוכל להתנתק —
    # 403 על logout השאיר אותו כלוא במצב הביניים.
    if method == "POST" and path.rstrip("/") == "/api/console/logout":
        return
    if session.purpose == PURPOSE_KIOSK:
        # #1340: הקיוסק נכנס בסיסמה בלבד וב-HTTP. בקונסולה זו אינה כניסה —
        # 401 ולא 403: הדפדפן נשלח למסך הכניסה של הקונסולה, עם MFA.
        # deploy מקבל את הסירוב של #1073 כמו בכל נתיב קונסולה.
        if getattr(request.app.state, CONSOLE_APP_FLAG, False):
            if session.role == "deploy":
                raise HTTPException(403, DEPLOY_NO_CONSOLE_HE)
            raise HTTPException(401, "לא מחובר")
        return
    if session.purpose == PURPOSE_PWCHANGE:
        if not (method == "POST" and path.rstrip("/") == "/api/console/me/password"):
            raise HTTPException(403, "password_change_required")
    elif session.purpose == PURPOSE_MFAENROLL:
        if not path.startswith("/api/console/me/mfa/"):
            raise HTTPException(403, "mfa_enrollment_required")


def dependencies(conn: sqlite3.Connection):
    """(current_user, admin_only) — ה-dependencies המשותפים לכל הראוטרים."""
    from fastapi import Depends, HTTPException, Request

    def current_user(request: Request) -> tuple[str, str]:
        peer = request.scope.get(PEER_SCOPE_KEY)
        if peer is not None:
            return peer
        token = request.cookies.get(COOKIE_NAME)
        found = read_session(conn, token,
                             ip=request.client.host if request.client else "",
                             idle_exempt=_is_logout(request))
        if found is None:
            raise HTTPException(401, "לא מחובר")
        _enforce_purpose(request, found)
        from server.console import session_renewal   # noqa: PLC0415 — הוא טוען את auth, לא להפך
        session_renewal.propose(request, conn, found, token)   # #2757
        return found.username, found.role

    def admin_only(user: tuple[str, str] = Depends(current_user)) -> tuple[str, str]:
        if user[1] != "admin":
            raise HTTPException(403, "פעולה למנהל בלבד")
        return user

    return current_user, admin_only


#: #1073 (הכרעת נדב 18/09): למשתמש הפצה אין קונסולה. הוא עובד ממחשב
#: הבנייה (התפריט/ה-GUI, דרך פורט הסוכן); הניהול הוובי
#: (:8081) הוא של admin בלבד.
DEPLOY_NO_CONSOLE = "deploy_no_console"
DEPLOY_NO_CONSOLE_HE = "משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה"


def console_only(conn: sqlite3.Connection):
    """dependency לאפליקציית הקונסולה (:8081): תפקיד ``deploy`` → 403 על
    **כל** נתיב, לא רק בכניסה (#1073).

    הכניסה עצמה מסרבת ל-deploy, אבל העוגייה שפורט הסוכן מנפיק
    חתומה באותו סוד ותקפה גם כאן — ולכן הסירוב יושב על הנתיבים ולא רק
    על הדלת. ההרשאה נקראת מהטבלה (``check``), כמו ב-``current_user``:
    מנהל שהורד ל-deploy מאבד את הקונסולה מיד, לא בסוף ה-TTL (#91).
    בלי עוגייה — עוברים: ``current_user`` של הנתיב יחזיר 401 כרגיל,
    וכניסה (שאין לה עוגייה עדיין) מכריעה לבד.

    HTTP בלבד: ראוטר שמצורף עם ה-dependency הזה יכול לשאת גם WebSocket
    (המוניטור דרך המשני, `console_storage`), ושם השער נסגר ב-4403
    **אחרי** accept כדי שהסיבה תגיע לדפדפן (#904) — `HTTPConnection`
    ולא ``Request`` כדי ש-FastAPI ימלא את הפרמטר בשני הסוגים, והסירוב
    עצמו נשאר של ה-HTTP.

    #1039 / #2414: "מי מחובר לקונסולה" והניתוק בחוסר פעילות יושבים ב-``read_session``
    — השער שכל נתיב עובר בו (HTTP בשני הפורטים, WebSocket, הקיוסק) — ולא כאן."""
    from fastapi import HTTPException
    from starlette.requests import HTTPConnection

    def no_deploy(connection: HTTPConnection) -> None:
        if connection.scope.get("type") != "http":
            return
        token = connection.cookies.get(COOKIE_NAME)
        found = check(conn, token, ip=connection.client.host if connection.client else "",
                      idle_exempt=_is_logout(connection))
        if found is None:
            # #1340: עוגיית קיוסק של deploy — אותו סירוב בדיוק כמו קודם. של
            # admin — עוברת לנתיב, ו-``current_user`` מחזיר 401 (לא "מחובר").
            kiosk = read_session(conn, token)
            if kiosk is not None and kiosk.purpose == PURPOSE_KIOSK                     and kiosk.role == "deploy":
                raise HTTPException(403, DEPLOY_NO_CONSOLE_HE)
        if found is not None and found[1] == "deploy":
            raise HTTPException(403, DEPLOY_NO_CONSOLE_HE)

    return no_deploy


def _is_logout(connection) -> bool:
    return (connection.scope.get("method") == "POST"
            and connection.scope["path"].rstrip("/") == "/api/console/logout")


def _secret(conn: sqlite3.Connection) -> bytes:
    """הסוד שחותם את ה-cookies, או חריגה. אין ברירת מחדל.

    כאן ישב ``or "00"``: סוד חסר הפך למפתח HMAC של בייט אחד **ידוע**,
    וכל מי שיודע את זה חתם לעצמו ``admin|admin|<תפוגה>`` ונכנס כמנהל
    (#90). זה עיקרון 5 במקום הרגיש ביותר במערכת — הנפילה של המנגנון
    שאמור לקרוא את הסוד הפכה ל"קראנו סוד". מכאן והלאה: מה שלא ניתן
    לקרוא ולאמת מפוצץ את הבקשה, ולא מייצר הרשאה.
    """
    raw = (get_setting(conn, SECRET_KEY) or "").strip()
    if not raw:
        raise SecretUnusable(f"\u200f{SECRET_KEY} חסר בבסיס הנתונים. {_HOW_TO_FIX}")
    try:
        secret = bytes.fromhex(raw)
    except ValueError:
        raise SecretUnusable(
            f"\u200f{SECRET_KEY} אינו hex תקין. {_HOW_TO_FIX}"
        ) from None
    if len(secret) < MIN_SECRET_BYTES:
        raise SecretUnusable(
            f"\u200f{SECRET_KEY} קצר מ-{MIN_SECRET_BYTES} בייטים. {_HOW_TO_FIX}"
        )
    return secret


def assert_secret(conn: sqlite3.Connection) -> None:
    """בדיקת עלייה: קוראים את הסוד פעם אחת, ליד ההתקנה ולא מול כיתה.

    `_secret` כבר מגן על כל בקשה, אבל שרת שעולה עם סוד שבור צריך
    להיכשל בזמן שמישהו מסתכל על המסך — לא בבוקר אחרי, כשמסך התחנה
    מחזיר 500 והתקלה נראית כמו רשת.
    """
    _secret(conn)


def _auth_epoch(conn: sqlite3.Connection, username: str) -> int:
    row = conn.execute(
        "SELECT auth_epoch FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    return int(row["auth_epoch"] or 0) if row is not None else 0


def issue(conn: sqlite3.Connection, username: str, role: str,
          purpose: str = "", epoch: int | None = None) -> str:
    expiry = int(time.time()) + TTL_SECONDS
    if epoch is None:
        epoch = _auth_epoch(conn, username)
    payload = f"{username}|{role}|{expiry}|{purpose}:{epoch}"
    signature = hmac.new(_secret(conn), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def cookie_flags(tls) -> dict:
    """דגלי עוגיית ה-session — מקור יחיד, גם לכניסה וגם לחידוש (#2757)."""
    return {"httponly": True, "samesite": "lax", "max_age": TTL_SECONDS,
            "secure": tls is not None}


def attach_cookie(response, conn: sqlite3.Connection, username: str, role: str,
                  tls, purpose: str = "") -> None:
    response.set_cookie(
        # codeql[py/cookie-injection] false positive: הערך חתום HMAC, ושם המשתמש מוגבל ל-[a-z0-9._-] (users._USERNAME_RE, #291)
        COOKIE_NAME, issue(conn, username, role, purpose=purpose),
        **cookie_flags(tls),
    )


def read_session(conn: sqlite3.Connection, token: str | None, *,
                 ip: str = "", idle_exempt: bool = False) -> Session | None:
    """מפענח עוגייה: 3 מפרידים (ישן) או 4 (purpose:epoch). None אם פסול.

    #2414: **השער היחיד** לחוסר פעילות. כל דרך לעוגייה עוברת כאן — ``current_user``
    (HTTP בקונסולה ובפורט הסוכן, כולל ראוטרים שמחוץ ל-``console_only``), ``check``
    (WebSocket של המוניטור, ה-VM והמשני, ``still_admin``) ופתיחת pull מהגואי. עד
    כאן הבדיקה ישבה ב-``console_only`` בלבד, ושלושה מסלולים עקפו אותה."""
    if not token:
        return None
    n = token.count("|")
    if n not in (3, 4):
        return None
    payload, _, signature = token.rpartition("|")
    expected = hmac.new(_secret(conn), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    parts = payload.split("|")
    if n == 3:
        username, _signed_role, expiry = parts
        purpose, epoch = "", 0
    else:
        username, _signed_role, expiry, extra = parts
        purpose, _, epoch_s = extra.partition(":")
        try:
            epoch = int(epoch_s or 0)
        except ValueError:
            return None
        if purpose not in ("", PURPOSE_PWCHANGE, PURPOSE_MFAENROLL, PURPOSE_KIOSK):
            return None
    try:
        expiry_at = int(expiry)
        if expiry_at < time.time():
            return None
    except ValueError:
        return None
    # #1633: עוגייה שיצאה ב-logout — באותה שאילתה, לא SELECT נוסף לכל בקשה.
    row = conn.execute(
        "SELECT username, role, disabled_at, auth_epoch,"
        " EXISTS (SELECT 1 FROM revoked_tokens WHERE token_hash = ?) AS revoked"
        " FROM users WHERE username = ? COLLATE NOCASE",
        (_token_hash(token), username),
    ).fetchone()
    if row is None or not row["role"]:
        return None
    if row["disabled_at"] or (row["revoked"] and not in_grace(token)):
        return None
    if epoch < int(row["auth_epoch"] or 0):
        return None
    session = Session(row["username"], row["role"], purpose, expiry_at, epoch)
    return session if _active(conn, token, session, ip, idle_exempt) else None


def _active(conn: sqlite3.Connection, token: str, session: Session, ip: str,
            idle_exempt: bool) -> bool:
    """#2414: ``console_idle_seconds`` בשרת, לכל עוגייה חוץ מהקיוסק.

    - **מלאה** — הפעילות האחרונה היא ``last_seen`` שלה ב-``console_sessions``; כל
      שימוש מאומת הוא פעילות (``touch``, לכל היותר כתיבה בדקה) — ולכן גם "מי מחובר".
    - **pwchange / mfaenroll** — אין להן שורה (אינן "מחוברות"), והמדידה היא מרגע
      ההנפקה: החלפת הסיסמה או הרישום צריכים לקרות בתוך חלון אחד.
    - **קיוסק** — פטורה. היא של מחשב בנייה פיזי, בפורט הסוכן בלבד (הקונסולה מסרבת
      לה, #1340), והגואי מחזיק אותה לאורך פעולה שלמה בלי בקשות: סבב של 34 דקות
      ואז סגירה, קליטה ואז ביטול מכפתור ההפעלה. לגואי שעון חוסר-פעילות משלו (#434).

    שפג — מבוטלת (``revoke``) ולא רק נדחית, כדי ששורה חדשה לא תחייה אותה, ובכל
    הפורטים. ``idle_exempt`` — ל-logout בלבד: הוא עצמו מבטל, ודפדפן שחזר משינה
    מתנתק בלי "הניתוק לא אושר"."""
    if session.purpose == PURPOSE_KIOSK:
        return True
    if in_grace(token):
        # #2757: העוגייה הוחלפה זה עתה בחדשה — הפעילות והשורה כבר אצל החדשה.
        return True
    from server.console import presence   # noqa: PLC0415 — presence אינו תלוי ב-auth; כאן בשביל הסדר
    if not idle_exempt and presence.idle_expired(
            conn, token, int(get_setting(conn, "console_idle_seconds") or 600), TTL_SECONDS):
        revoke(conn, token)
        presence.forget(conn, token)
        return False
    if not session.purpose:
        presence.touch(conn, token, session.username, ip, TTL_SECONDS)
    return True


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def revoke(conn: sqlite3.Connection, token: str | None) -> None:
    """#1633: logout מבטל את העוגייה הזו בשרת — רק אותה, לא את שאר
    הדפדפנים של המשתמש (זה ``users.bump_auth_epoch``). השורה נשמרת עד
    התפוגה שבעוגייה, ואז נמחקת; עוגייה שאינה מפוענחת אינה נרשמת."""
    if not token or token.count("|") not in (3, 4):
        return
    try:
        expires = float(token.rpartition("|")[0].split("|")[2])
    except (IndexError, ValueError):
        return
    revoke_hash(conn, _token_hash(token), expires)


#: #2757: חידוש מחליף עוגייה בחדשה, והישנה מבוטלת (``revoked_tokens``) — אבל בקשות
#: שכבר יצאו מהדפדפן עם הישנה עוד בדרך. לחסד קצר, בזיכרון בלבד, הן עוברות. ביטול
#: אמיתי (``revoke`` / ``revoke_hash``) מוחק את החסד. אתחול שרת מאבד אותו, וזה בכיוון הבטוח.
GRACE_SECONDS = 60
_grace: dict[str, float] = {}
_grace_lock = threading.Lock()


def in_grace(token: str) -> bool:
    key = _token_hash(token)
    with _grace_lock:
        until = _grace.get(key)
        if until is not None and until < time.time():
            del _grace[key]
            return False
        return until is not None


def supersede(conn: sqlite3.Connection, token: str) -> None:
    """#2757: מבטל את ``token`` בשרת (כמו logout) ופותח לו חסד קצר."""
    revoke(conn, token)
    now = time.time()
    with _grace_lock:
        for stale in [k for k, t in _grace.items() if t < now]:
            del _grace[stale]
        _grace[_token_hash(token)] = now + GRACE_SECONDS


def revoke_hash(conn: sqlite3.Connection, token_hash: str, expires: float) -> None:
    """#2552: אותו ביטול לפי הגיבוב — מסך הסשנים של המנהל מכיר רק את ה-sha256
    של העוגייה (``console_sessions``), לא אותה. ``expires`` = תפוגת העוגייה."""
    with _grace_lock:
        _grace.pop(token_hash, None)
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (time.time(),))
        conn.execute(
            "INSERT OR REPLACE INTO revoked_tokens (token_hash, expires_at) VALUES (?, ?)",
            (token_hash, expires),
        )


def check(conn: sqlite3.Connection, token: str | None, *, ip: str = "",
          idle_exempt: bool = False) -> tuple[str, str] | None:
    """מחזיר (username, role) לטוקן תקף, אחרת None.

    החתימה מוכיחה **מי** — שהטוקן יצא מהשרת הזה ולא שונה בדרך. היא לא
    מוכיחה **מה מותר לו**: התפקיד שבמטען הוא צילום מרגע הכניסה, ומנהל
    שהורד ל-deploy או שנמחק נשאר מנהל עד סוף ה-TTL (#91). לכן ההרשאה
    נקראת מהטבלה בכל בקשה, והטוקן משמש רק כזהות.

    שאילתה לכל בקשה מאומתת היא המחיר, והוא נבדק: SELECT יחיד לפי
    מפתח ראשי, קריאה בלבד, ולכן אינו נוגע בנעילת הכתיבה של WAL.
    `conn` הוא ``db.Database`` — חיבור לכל תהליכון, וזה בדיוק
    התהליכון שבו uvicorn מריץ את ``current_user`` (#54).

    אין כאן מטמון: מטמון היה מחזיר בדיוק את החלון שהתיקון סוגר.

    Session מוגבלת (החלפת סיסמה / הרשמת MFA) אינה session מלאה —
    WebSocket והקוראים הישנים מקבלים None, לא הרשאה חלקית בשקט.
    """
    found = read_session(conn, token, ip=ip, idle_exempt=idle_exempt)
    if found is None or found.purpose:
        return None
    return found.username, found.role
