"""משתמשים והרשאות — סעיף 11 באפיון.

שני תפקידים בלבד: admin (הכל) ו-deploy (לבחור אימג' ולהפיץ, ותו לא).
משתמש הפצה מקליד סיסמה על מסך שעומד בכיתה — גם אם היא דולפת, הכי גרוע
שיקרה זה התקנה מחדש של אימג' תקין.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import string

from .db import _write_lock, journal, now_iso, writing

#: מספר הסבבים ל**סיסמאות חדשות**. סיסמה קיימת נבדקת במספר שנשמר איתה.
_ITERATIONS = 200_000
_SCHEME = "pbkdf2"

#: כל כתיבה כאן עוברת ב-``with _write_lock, writing(conn)`` — שני
#: המנגנונים של `db.py`, מאותה סיבה שבגללה `net_seen` קיבל אותם (#272,
#: ‏#356, ‏#313): כתיבה שנכשלה בלי ``rollback`` משאירה את החיבור בתוך
#: טרנזאקציה, ומשם **כל** כתיבה עליו נכשלת מיד עד אתחול השרת — כלומר
#: ניהול משתמשים שהפסיק לעבוד. ‏`journal` נוטל את אותה נעילה בעצמו,
#: והיא ``Lock`` ולא ``RLock``, ולכן הרישום ביומן נשאר **מחוץ** לבלוק.

#: #1093: ערכת הנושא היא של המשתמש, לא של הדפדפן ולא של השרת.
THEMES = frozenset({"auto", "light", "dark"})


def _derive(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), iterations
    ).hex()


#: אנגלית בלבד, לפי הכרעת נדב (#111) ועדכון #1085: בלי רווח, 2–50.
_USERNAME_RE = re.compile(r"[a-z0-9._-]{2,50}", re.IGNORECASE)

#: תו מיוחד — אותם תווים ואותה הודעה כמו באלפון (`users_helpers.validate_password`).
_PASSWORD_SPECIAL = re.compile(r'[!@#$%^&*(),.?":{}|<>]')


class UsernameTaken(ValueError):
    """שם תפוס, כולל כפילות case-insensitive. הקורא מתרגם ל-409."""


class BuiltinProtected(ValueError):
    """‏#1262: המנהל שנוצר בהתקנה (``is_builtin=1``) הוא דלת החירום — בלי
    MFA בכוונה (``builtin_no_mfa``), ולכן לא נמחק, לא מושבת ולא מורד
    מתפקיד מנהל, **גם כשיש מנהל פעיל אחר**. הקורא מתרגם ל-409.

    ‏``ValueError`` כדי שקורא ישן שתופס רק אותו עדיין יסרב ולא יקרוס.
    """


#: ‏#1120: תקרה. PBKDF2 עם 200K סבבים על סיסמה של מגה-בייט הוא DoS;
#: ‏NIST 800-63B דורש להתיר ≥64, ו-128 משאיר מרווח לביטויי-סיסמה.
MAX_PASSWORD_LEN = 128


def validate_password(password: str) -> None:
    """נוהל אלפון: ≥8, אותיות, ספרות, תו מיוחד. הודעות בעברית כמו במקור."""
    if len(password) < 8:
        raise ValueError("הסיסמה חייבת להכיל לפחות 8 תווים")
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError(f"הסיסמה ארוכה מדי — עד {MAX_PASSWORD_LEN} תווים")
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("הסיסמה חייבת להכיל אותיות")
    if not re.search(r"[0-9]", password):
        raise ValueError("הסיסמה חייבת להכיל מספרים")
    if not _PASSWORD_SPECIAL.search(password):
        raise ValueError("הסיסמה חייבת להכיל תו מיוחד")


def generate_password(length: int = 12) -> str:
    """סיסמה אקראית שעומדת בנוהל — כמו `users_admin_ops.generate_password`."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    pwd = "".join(secrets.choice(alphabet) for _ in range(length))
    if (len(pwd) < 8 or not re.search(r"[A-Za-z]", pwd)
            or not re.search(r"[0-9]", pwd)
            or not _PASSWORD_SPECIAL.search(pwd)):
        pwd = (
            secrets.choice(string.ascii_letters)
            + secrets.choice(string.digits)
            + secrets.choice("!@#$%^&*")
            + "".join(secrets.choice(alphabet) for _ in range(length - 3))
        )
    validate_password(pwd)
    return pwd


def _hash(password: str, salt: str, iterations: int = _ITERATIONS) -> str:
    """הרשומה נושאת את הפרמטרים שלה — סכימה ומספר סבבים — כדי שאפשר
    יהיה לאמת אותה גם אחרי שברירת המחדל תשתנה."""
    return f"{_SCHEME}${iterations}${salt}${_derive(password, salt, iterations)}"


def _check_username(username: str) -> None:
    """שם משתמש הוא אנגלית בלבד — החלטה, לא עקיפה.

    רשימת-היתר ולא רשימת-איסור: ‏`[A-Za-z0-9._-]`, 2 עד 50 תווים. שלושת
    האיסורים שקדמו לה כיסו כל אחד באג אחר, ורשימת איסורים תמיד מפספסת
    את הבא בתור.

    מה שכל אחד מהם היה:

    * ‏`|` — הטוקן הוא ``username|role|expiry|signature``, ו-`auth.check`
      פותח בספירת מפרידים. שם עם `|` **עובר את הכניסה** (‏200 ו-cookie)
      ונופל בבקשה הבאה: חשבון שנראה תקין ומתנהג כמו סיסמה שגויה.
    * תווי בקרה — שוברים את כותרת ה-cookie.
    * שם שאינו latin-1 — ‏starlette מקודד את הכותרת ב-latin-1, ולכן שם
      בעברית עבר את היצירה ואת בדיקת הסיסמה **ואז הפיל את `set_cookie`
      ב-500 — על הסיסמה הנכונה.** סיסמה שגויה החזירה 401 נקי, כלומר
      הכישלון היה ניתן לאבחון וההצלחה לא (#111).

    נדב הכריע שהשמות באנגלית בלבד, ולכן #111 נסגר כאן ולא בפורמט
    ה-cookie — שהחלפתו הייתה מנתקת פעם אחת את כל המחוברים.

    האכיפה היא **ביצירה בלבד**. ``verify`` אינו בודק שוב, כדי שחשבון
    קיים לא ייחסם רטרואקטיבית על ידי כלל שנוסף אחריו.
    """
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(
            "שם משתמש באותיות אנגליות, ספרות, נקודה, מקף או קו "
            "תחתון — 2 עד 50 תווים"
        )


def lookup(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    """שורה לפי שם, בלי תלות ברישיות. None אם אין."""
    return conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
        (username.strip(),),
    ).fetchone()


def canonical_name(conn: sqlite3.Connection, username: str) -> str | None:
    row = lookup(conn, username)
    return None if row is None else row["username"]


def flags(conn: sqlite3.Connection, username: str) -> dict:
    row = lookup(conn, username)
    if row is None:
        return {"must_change_password": False, "mfa_enabled": False,
                "is_builtin": False, "mfa_secret": None, "mfa_last_step": None,
                "mfa_required": False}
    return {
        "must_change_password": bool(row["must_change_password"]),
        "mfa_enabled": bool(row["mfa_enabled"]),
        "is_builtin": bool(row["is_builtin"]),
        "mfa_secret": row["mfa_secret"],
        "mfa_last_step": row["mfa_last_step"],
        "mfa_required": bool(row["mfa_required"]),   # נדב 18/10
    }


def create(conn: sqlite3.Connection, username: str, password: str, role: str, by: str,
           *, is_builtin: bool = False, must_change_password: bool = False,
           check_policy: bool = True, mfa_required: bool = False) -> None:
    if role not in ("admin", "deploy"):
        raise ValueError("התפקיד חייב להיות admin או deploy")
    username = username.strip()
    if not username:
        raise ValueError("שם משתמש ריק")
    _check_username(username)
    if check_policy:
        validate_password(password)
    elif not password:
        raise ValueError("סיסמה ריקה")
    if lookup(conn, username) is not None:
        raise UsernameTaken("משתמש בשם הזה כבר קיים")
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO users (username, pw_hash, role, created_at,"
            " must_change_password, is_builtin, mfa_required)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, _hash(password, secrets.token_hex(16)), role, now_iso(),
             1 if must_change_password else 0, 1 if is_builtin else 0,
             1 if mfa_required else 0),
        )
    journal(conn, "user_create",
            f"{username} ({role})" + (" mfa_required" if mfa_required else ""), by)


def verify(conn: sqlite3.Connection, username: str, password: str) -> str | None:
    """מחזיר את התפקיד אם הסיסמה נכונה, אחרת None. השוואה קבועת-זמן.

    הפרמטרים לגיבוב באים **מהרשומה** ולא מהקבועים של הקובץ. כאן ישבו
    ‏`_scheme` ו-`_iters` — מפוענחים, ואז מושלכים לטובת `_ITERATIONS`
    הקבוע. כל עוד לא שינו את הקבוע זה עבד; ביום שהוא היה עולה (וזה מה
    שקבוע כזה נועד לו) **כל הסיסמאות הקיימות היו נכשלות בשקט**, בהודעה
    "שם משתמש או סיסמה שגויים" שנראית כמו טעות הקלדה ולא כמו מיגרציה
    שלא נעשתה. עם המנהל האחרון — קונסולה נעולה.

    ורשומה שלא ניתן לפענח נדחית, ולא נופלת חזרה לקבוע: "לא הבנו את
    הרשומה" איננו "הסיסמה שגויה במקרה", ובוודאי לא "ננחש 200,000".
    """
    # ‏#186: חסום אינו נכנס. הבדיקה כאן **בנוסף** ל-`auth.check` ולא
    # במקומה — זו הכניסה, וזו הבקשה שאחריה.
    row = lookup(conn, username)
    if row is None or row["disabled_at"]:
        return None
    try:
        scheme, raw_iterations, salt, digest = row["pw_hash"].split("$")
        iterations = int(raw_iterations)
        bytes.fromhex(salt)                    # מלח שאינו hex — רשומה פגומה
    except ValueError:
        return None
    if scheme != _SCHEME or iterations < 1 or not digest:
        return None
    if hmac.compare_digest(_derive(password, salt, iterations), digest):
        return row["role"]
    return None


def update(
    conn: sqlite3.Connection, username: str, by: str,
    password: str | None = None, role: str | None = None,
    *, check_policy: bool = True, mfa_required: bool | None = None,
) -> None:
    """שינוי תפקיד, איפוס סיסמה ו/או "דרוש אימות דו-שלבי". שדה שלא נשלח — לא נוגעים בו."""
    row = lookup(conn, username)
    if row is None:
        raise ValueError("משתמש לא קיים")
    username = row["username"]
    if role is not None and role not in ("admin", "deploy"):
        raise ValueError("תפקיד לא חוקי")
    if password is not None:
        if check_policy:
            validate_password(password)
        elif not password:
            raise ValueError("סיסמה ריקה")
    # ‏#1262: לפני כל כתיבה — גם הסיסמה שנשלחה באותה בקשה לא נכתבת.
    # ‏``is_builtin`` נכתב רק ב-INSERT ואינו משתנה, ולכן קריאה כאן אינה TOCTOU.
    if role == "deploy" and row["is_builtin"]:
        raise BuiltinProtected("המנהל שנוצר בהתקנה אינו ניתן להורדה מתפקיד מנהל")
    # הורדת המנהל האחרון מתפקידו נועלת את כולם מחוץ לניהול.
    if role == "deploy" and row["role"] == "admin" and admin_count(conn) <= 1:
        raise ValueError("זה המנהל האחרון — אי אפשר להוריד אותו מתפקידו")

    # שני העדכונים הם טרנזאקציה אחת: תפקיד שהשתנה בלי הסיסמה שנשלחה
    # איתו הוא חשבון שאיש אינו יודע באיזה מצב הוא.
    changed = []
    with _write_lock, writing(conn):
        if role is not None and role != row["role"]:
            conn.execute("UPDATE users SET role = ? WHERE username = ?",
                         (role, username))
            changed.append(f"role={role}")
        if password is not None:
            conn.execute(
                "UPDATE users SET pw_hash = ? WHERE username = ?",
                (_hash(password, secrets.token_hex(16)), username),
            )
            changed.append("password")
        if mfa_required is not None and bool(mfa_required) != bool(row["mfa_required"]):
            conn.execute("UPDATE users SET mfa_required = ? WHERE username = ?",
                         (1 if mfa_required else 0, username))
            changed.append("mfa_required=true" if mfa_required else "mfa_required=false")
    if changed:
        journal(conn, "user_edit", f"{username} " + ", ".join(changed), by)


def set_disabled(conn: sqlite3.Connection, username: str, disabled: bool,
                 by: str) -> None:
    """חסימה או שחרור. שני שומרים, ושניהם על אותו היגיון כמו במחיקה.

    **חסימה אינה מחיקה.** היא הפיכה, והיא משאירה את הרשומה — כך ששורות
    היומן שמזכירות את השם ממשיכות להצביע על מישהו. מחיקה הופכת אותן
    לשם שאין מאחוריו כלום.
    """
    row = lookup(conn, username)
    if row is None:
        raise ValueError("משתמש לא קיים")
    username = row["username"]
    # ‏#1262: רק הכיוון שנועל. builtin חסום ממצב ישן עדיין ניתן לשחרור.
    if disabled and row["is_builtin"]:
        raise BuiltinProtected("המנהל שנוצר בהתקנה אינו ניתן להשבתה")
    if disabled and row["role"] == "admin" and active_admin_count(conn) <= 1:
        raise ValueError("זה המנהל הפעיל האחרון — חסימתו תנעל את הקונסולה")
    with _write_lock, writing(conn):
        conn.execute("UPDATE users SET disabled_at = ? WHERE username = ?",
                     (now_iso() if disabled else None, username))
    journal(conn, "user_disabled" if disabled else "user_enabled", username, by)


def active_admin_count(conn: sqlite3.Connection) -> int:
    """מנהלים שאינם חסומים. ‏`admin_count` סופר גם חסומים, ולכן הוא
    התשובה הלא נכונה לשאלה "האם יישאר מי שינהל"."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users"
        " WHERE role = 'admin' AND disabled_at IS NULL"
    ).fetchone()["n"]


def admin_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
    ).fetchone()["n"]


def delete(conn: sqlite3.Connection, username: str, by: str) -> None:
    """מחיקה שתובעת את השארית באותה כתיבה.

    השומר על "יישאר מי שינהל" ישב עד ‏#521 ב-``console_api.del_user``
    כ**קריאה לפני הכתיבה**: ‏``active_admin_count`` נקרא, ואז המחיקה
    רצה. בין השניים אין נעילה, ולכן שני מנהלים שנמחקים בו-זמנית ראו
    שניהם "יש שניים", שניהם דילגו על השומר, ושניהם נמחקו.

    כאן התנאי הוא **חלק מה-DELETE**: השורה נמחקת רק אם היא אינה מנהל
    פעיל, או אם יישאר עוד אחד. ‏``rowcount == 0`` הוא **התשובה** — לא
    כישלון — והקורא מבחין בין "לא היה" ל"היה, ולא מוחקים אותו".

    ‏#1262: ``is_builtin = 0`` באותו WHERE. בניגוד לשומר המנהל האחרון,
    הוא חל גם על builtin **חסום** — דלת החירום אינה נמחקת לעולם.
    """
    with _write_lock, writing(conn):
        cur = conn.execute(
            "DELETE FROM users WHERE username = ? AND is_builtin = 0"
            "  AND (role != 'admin' OR disabled_at IS NOT NULL"
            "       OR (SELECT COUNT(*) FROM users"
            "           WHERE role = 'admin' AND disabled_at IS NULL) > 1)",
            (username,),
        )
        removed = cur.rowcount == 1
    if not removed:
        # שלושה מצבים שונים, ואסור לקפל אותם: משתמש שאינו קיים, המנהל
        # שנוצר בהתקנה (#1262), והמנהל הפעיל האחרון שהשומר עצר. הקורא
        # שואל, ולא מנחש.
        row = conn.execute(
            "SELECT is_builtin FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise ValueError("משתמש לא קיים")
        if row["is_builtin"]:
            raise BuiltinProtected("המנהל שנוצר בהתקנה אינו ניתן למחיקה")
        raise ValueError("זה המנהל האחרון — מחיקתו תנעל את הקונסולה")
    journal(conn, "user_delete", username, by)


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT username, role, created_at, disabled_at,"
        " must_change_password, mfa_enabled, is_builtin, mfa_required,"
        " last_login_at, last_login_from"
        " FROM users ORDER BY username"
    ).fetchall()
    return [{
        **dict(r),
        "disabled": bool(r["disabled_at"]),
        "must_change_password": bool(r["must_change_password"]),
        "mfa_enabled": bool(r["mfa_enabled"]),
        "is_builtin": bool(r["is_builtin"]),
        "mfa_required": bool(r["mfa_required"]),
    } for r in rows]


def record_login(conn: sqlite3.Connection, username: str, ip: str) -> None:
    """‏#1008: כניסה שהנפיקה עוגייה — מתי ומאיזו כתובת (``request.client.host``).

    נקרא ליד כל ``attach_cookie`` של כניסה (``console_login``, ‏``/login/mfa``),
    ולא ב-``mfa_required``: סיסמה בלי הגורם השני אינה כניסה. זה המקור של
    ``last_login_*`` ב-``GET /users`` — לא ``console_sessions`` (#1039), שהשורה
    שלה נמחקת ביציאה ובתפוגה."""
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE users SET last_login_at = ?, last_login_from = ?"
            " WHERE username = ?",
            (now_iso(), ip, username),
        )


def get_theme(conn: sqlite3.Connection, username: str) -> str:
    """ערכת הנושא של המשתמש. ערך זר או שורה חסרה → ``auto`` — לא קריסה."""
    row = conn.execute(
        "SELECT theme FROM users WHERE username = ?", (username,)
    ).fetchone()
    theme = row["theme"] if row is not None else None
    return theme if theme in THEMES else "auto"


def set_theme(conn: sqlite3.Connection, username: str, theme: str) -> None:
    """המשתמש על עצמו. בלי יומן — זו העדפה, לא פעולת ניהול."""
    if theme not in THEMES:
        raise ValueError("theme חייב להיות auto, light או dark")
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE users SET theme = ? WHERE username = ?",
            (theme, username),
        )


def change_own_password(conn: sqlite3.Connection, username: str,
                        current: str, new: str) -> None:
    """החלפת סיסמה עצמית. מאמתת את הנוכחית, אוכפת נוהל, מורידה כפייה."""
    if verify(conn, username, current) is None:
        raise ValueError("הסיסמה הנוכחית שגויה")
    validate_password(new)
    stored = canonical_name(conn, username)
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE users SET pw_hash = ?, must_change_password = 0"
            " WHERE username = ?",
            (_hash(new, secrets.token_hex(16)), stored),
        )
    # ‏#1120: סיסמה חדשה = כל session ישנה וכל דפדפן "זכור" מתבטלים.
    # הקורא (`/me/password`) מנפיק לעצמו עוגייה חדשה אחרי הקריאה הזו.
    # מחוץ ל-`_write_lock` — הוא נוטל אותה בעצמו והיא ``Lock``, לא ``RLock``.
    bump_auth_epoch(conn, stored)
    journal(conn, "password_changed", stored, stored)


def reset_password(conn: sqlite3.Connection, username: str, by: str) -> str:
    """איפוס על ידי admin: סיסמה אקראית חד-פעמית + החלפה כפויה בכניסה הבאה."""
    row = lookup(conn, username)
    if row is None:
        raise ValueError("משתמש לא קיים")
    stored = row["username"]
    new = generate_password()
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE users SET pw_hash = ?, must_change_password = 1"
            " WHERE username = ?",
            (_hash(new, secrets.token_hex(16)), stored),
        )
    # ‏#1120: איפוס הוא התגובה לחשבון שנפרץ — ה-session של התוקף והדפדפן
    # ה"זכור" שלו (שעוקף MFA) חייבים למות יחד עם הסיסמה הישנה.
    bump_auth_epoch(conn, stored)
    journal(conn, "password_reset", stored, by)
    return new


def bump_auth_epoch(conn: sqlite3.Connection, username: str) -> None:
    """מבטל את כל ה-sessions החתומות של המשתמש. העוגייה נושאת את ה-epoch."""
    stored = canonical_name(conn, username)
    if stored is None:
        raise ValueError("משתמש לא קיים")
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE users SET auth_epoch = auth_epoch + 1 WHERE username = ?",
            (stored,),
        )
        conn.execute("DELETE FROM mfa_challenges WHERE username = ?", (stored,))
    from . import viewer_sessions
    viewer_sessions.revoke_user(stored)


def ensure_admin(conn: sqlite3.Connection, password: str | None = None) -> str | None:
    """בהתקנה טרייה נוצר admin/admin עם החלפה כפויה, is_builtin=1.

    אם כבר יש מנהל — לא נוגעים. `password` (‏`--admin-pass`) מדלג על הכפייה.
    מחזיר את הסיסמה המודפסת אם נוצר ברירת-המחדל, אחרת None.
    """
    row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if row is not None:
        return None
    if password:
        create(conn, "admin", password, "admin", by="",
               is_builtin=True, must_change_password=False, check_policy=False)
        return None
    create(conn, "admin", "admin", "admin", by="",
           is_builtin=True, must_change_password=True, check_policy=False)
    return "admin"
