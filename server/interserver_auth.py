"""Storage Nodes — פרימיטיבים ל-enrollment מוקשח (#740, tracer 2.1).

**מה שכן כאן, ומה שאינו — במכוון.** הדגם הנעול של #740 נשען על ערוץ
mTLS 1.3 עם channel-binding לפי RFC 9266 (``tls-exporter``) ועל SPKI-pin.
שלושתם דורשים ``export_keying_material`` ו-parsing של SubjectPublicKeyInfo,
ואלה **אינם** בספריית התקן של פייתון (‏3.12: אין ``export_keying_material``,
ו-channel binding היחיד הוא ``tls-unique`` שאינו מוגדר ל-TLS 1.3). הם
דורשים ``pyOpenSSL``/``cryptography``, שאינם מותקנים על תחנת הפיתוח.

לכן המודול הזה מכיל **רק** את הפרימיטיבים שאין להם תלות ב-TLS ושניתן
לאמת אותם כאן במלואם: יצירת קוד ה-pairing וגיבובו האיטי המלוח, אימות
בזמן-קבוע, ניהול חלון ה-pairing (פתיחה/מצב/צריכה עם תקרת ניסיונות
ותפוגה), יצירת/גיבוב/אימות של טוקן האב, ניתוח קפדני של כתובת הבין-שרתי,
כתיבת קובץ אישורים ``0600`` אטומית וטעינתו עם דחיית קובץ לא-בטוח,
ועזרי הסתרת סודות ליומן. ה-context של ה-TLS, חילוץ ה-SPKI, ה-exporter
והלקוח המוצמד — שלב נפרד שממתין להכרעת התלות (ראו דוח ה-PR של #740).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import stat as stat_module
import urllib.parse
from datetime import datetime, timedelta, timezone

from .db import _write_lock, writing

#: גרסת הפרוטוקול הבין-שרתי. #740 נועל אותה על ``2.1`` בדיוק — התאמה
#: מדויקת, לא ">=" (עיקרון 5: גרסה לא-מוכרת נדחית, לא "קרוב מספיק").
PROTOCOL_VERSION = "2.1"

#: חלון ה-pairing: ברירת המחדל (ה-API המקומי) היא 3 דקות. התקרה עלתה מ-5
#: ל-10 דקות בהכרעת נדב (16/10): הקוד שמסך השרת (DCUI, ‏F4) מציג בנוכחות
#: פיזית תקף 10 דקות — זמן ללכת לקונסולה של הראשי ולהקליד אותו.
WINDOW_TTL_SECONDS = 180
WINDOW_TTL_MIN = 120
WINDOW_TTL_MAX = 600
PHYSICAL_WINDOW_TTL_SECONDS = 600
#: כמה ניסיונות קוד לפני שהחלון נסגר מעצמו (rate-limit על קוד אנטרופיה נמוכה).
MAX_ATTEMPTS = 5

#: פרמטרי scrypt — גיבוב איטי לקוד ה-pairing בעל האנטרופיה הנמוכה, כדי
#: שאפילו חשיפת הגיבוב לא תיתן ניחוש offline זול. סטנדרטי ל-interactive.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

#: אלפבית הקוד — קריא לאדם, בלי תווים דו-משמעיים (0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
#: ‏``XXXX-XXXX`` — שמונה תווים (הכרעת נדב 16/10, מוקאפ #1525): שמונה
#: תיבות בקונסולת הראשי. 31^8 ≈ 2^39.6, עם 5 ניסיונות לחלון.
_CODE_GROUPS = 2
_CODE_GROUP_LEN = 4


# --- אכיפת מקומיות (loopback) -----------------------------------------------

def is_loopback(host: str | None) -> bool:
    """האם ה-peer הוא loopback אמיתי — 127.0.0.0/8 או ``::1``.

    ‏"מקומי" בדגם הנעול נאכף, לא רק מוצג: חלון ה-pairing והפינוי נפתחים
    רק מחיבור loopback מאומת. ראיה חיובית בלבד (עיקרון 5) — ``host``
    ריק/לא-ידוע מחזיר ``False``, לא "כנראה מקומי". ``Forwarded``/
    ``X-Forwarded-For`` **אינם** נבדקים כאן: הם כותרות שניתן לזייף,
    וההחלטה נשענת על כתובת ה-peer של ה-ASGI בלבד.
    """
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False              # ‏#860: "127.evil" אינו כתובת, לא loopback
    # ‏IPv4-mapped (``::ffff:127.0.0.1``) נשפט לפי הכתובת שבפנים — בלי
    # להסתמך על השינוי של 3.13 ב-``IPv6Address.is_loopback``.
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool((mapped or addr).is_loopback)


# --- קוד ה-pairing: יצירה, גיבוב איטי, אימות בזמן-קבוע -----------------------

def generate_pairing_code() -> str:
    """קוד חד-פעמי קריא לאדם, ``XXXX-XXXX`` מאלפבית לא-דו-משמעי.

    ‏``secrets.choice`` ולא ``random`` — זה סוד. שמונה תווים מ-31 הם
    כ-39 סיביות; עם חלון של דקות ותקרת 5 ניסיונות, די והותר.
    """
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_LEN))
        for _ in range(_CODE_GROUPS)
    ]
    return "-".join(groups)


def _normalize_code(code: str) -> bytes:
    """מנרמל קלט משתמש (רישיות, מקפים, רווחים) לפני גיבוב/השוואה."""
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper().encode()


def hash_pairing_code(code: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """מחזיר ``(salt, hash)`` — גיבוב scrypt מלוח של הקוד המנורמל."""
    salt = salt if salt is not None else secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        _normalize_code(code), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return salt, digest


def verify_pairing_code(code: str, salt: bytes, expected: bytes) -> bool:
    """אימות בזמן-קבוע (``compare_digest``) מול הגיבוב השמור."""
    _, digest = hash_pairing_code(code, salt)
    return hmac.compare_digest(digest, expected)


# --- חלון ה-pairing: singleton בטבלת ``pairing_windows`` --------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def open_pairing_window(conn, *, ttl_seconds: int = WINDOW_TTL_SECONDS,
                        max_attempts: int = MAX_ATTEMPTS,
                        now: datetime | None = None) -> str:
    """פותח (או מחליף) את החלון הפתוח ומחזיר את הקוד **פעם אחת בלבד**.

    הקוד הגלוי חוזר רק כאן; נשמר ממנו רק הגיבוב המלוח. פתיחה חוזרת
    דורסת חלון קודם שלא נצרך (``INSERT OR REPLACE`` על ה-singleton).
    """
    if not WINDOW_TTL_MIN <= ttl_seconds <= WINDOW_TTL_MAX:
        raise ValueError(
            f"חלון ה-pairing נעול לטווח {WINDOW_TTL_MIN}-{WINDOW_TTL_MAX} שניות")
    now = now or _utcnow()
    code = generate_pairing_code()
    salt, digest = hash_pairing_code(code)
    opened_at = now.isoformat(timespec="seconds")
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT OR REPLACE INTO pairing_windows"
            " (singleton, code_hash, code_salt, opened_at, expires_at,"
            "  attempts_remaining) VALUES (1, ?, ?, ?, ?, ?)",
            (digest, salt, opened_at, expires_at, max_attempts),
        )
    return code


def pairing_window_status(conn, *, now: datetime | None = None) -> dict:
    """מצב החלון — פתוח/סגור, תפוגה וניסיונות שנותרו. **לעולם לא הקוד.**"""
    now = now or _utcnow()
    row = conn.execute(
        "SELECT opened_at, expires_at, attempts_remaining FROM pairing_windows"
        " WHERE singleton = 1"
    ).fetchone()
    if row is None:
        return {"open": False}
    expired = _parse_iso(row["expires_at"]) <= now
    return {
        "open": not expired,
        "opened_at": row["opened_at"],
        "expires_at": row["expires_at"],
        "attempts_remaining": row["attempts_remaining"],
    }


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def verify_and_consume_code(conn, code: str, *, now: datetime | None = None) -> bool:
    """מאמת קוד מול החלון הפתוח, וצורך אותו בהצלחה. ראיה חיובית בלבד.

    ``False`` לכל אחד מ: אין חלון, החלון פג, קוד שגוי. קוד נכון סוגר את
    החלון (חד-פעמי). כל ניסיון מוריד את המונה, וכשהוא מתאפס — החלון
    נסגר (rate-limit). התפוגה נבדקת **לפני** האימות, ואינה סופרת ניסיון.

    ‏#745: **כל** מסלול הקריאה→אימות→צריכה/הפחתה יושב תחת נעילת כתיבה
    אחת (``_write_lock`` + ``writing``), כי הוא read-modify-write. שתי
    בקשות עם הקוד הנכון במקביל היו קוראות את החלון לפני שאחת סוגרת,
    ושתיהן מחזירות ``True`` — קוד חד-פעמי שנצרך פעמיים; והמונה שנקרא
    מחוץ לנעילה היה יורד 5→4 על חמישה ניחושים במקום 5→0. הצריכה היא
    ``DELETE`` מותנה שהמנצח בו נקבע ב-``rowcount`` (בקשה אחת בלבד מקבלת
    ``True``), וההפחתה היא ``UPDATE ... attempts_remaining - 1`` אטומי.
    """
    now = now or _utcnow()
    with _write_lock, writing(conn):
        row = conn.execute(
            "SELECT code_hash, code_salt, expires_at, attempts_remaining"
            " FROM pairing_windows WHERE singleton = 1"
        ).fetchone()
        if row is None:
            return False
        if _parse_iso(row["expires_at"]) <= now:
            conn.execute("DELETE FROM pairing_windows WHERE singleton = 1")
            return False
        if verify_pairing_code(code, row["code_salt"], row["code_hash"]):
            # צריכה אטומית: רק המחיקה שהסירה שורה (rowcount==1) זוכה.
            return conn.execute(
                "DELETE FROM pairing_windows WHERE singleton = 1"
            ).rowcount == 1
        # קוד שגוי — הפחתה אטומית, וסגירה כשהמונה הגיע לאפס.
        conn.execute(
            "UPDATE pairing_windows SET attempts_remaining = attempts_remaining - 1"
            " WHERE singleton = 1 AND attempts_remaining > 0"
        )
        after = conn.execute(
            "SELECT attempts_remaining FROM pairing_windows WHERE singleton = 1"
        ).fetchone()
        if after is not None and after["attempts_remaining"] <= 0:
            conn.execute("DELETE FROM pairing_windows WHERE singleton = 1")
        return False


# --- טוקן האב: יצירה, גיבוב, אימות ------------------------------------------

def generate_token() -> str:
    """טוקן אקראי בן 256 סיביות, כמחרוזת hex."""
    return secrets.token_hex(32)


def hash_token(token: str) -> bytes:
    """גיבוב הטוקן לאחסון על המשני. אנטרופיה מלאה → sha256 מספיק (בניגוד
    לקוד ה-pairing, שדורש scrypt)."""
    return hashlib.sha256(token.encode()).digest()


def verify_token(token: str, expected_hash: bytes) -> bool:
    """אימות בזמן-קבוע של טוקן מול הגיבוב השמור."""
    return hmac.compare_digest(hash_token(token), expected_hash)


# --- ניתוח קפדני של כתובת הבין-שרתי -----------------------------------------

class InterserverURLError(ValueError):
    """כתובת בין-שרתי שאינה עומדת בדרישות המוקשחות."""


def parse_interserver_url(url: str) -> tuple[str, int, str]:
    """מחזיר ``(host, port, path)`` לכתובת בין-שרתי חוקית, אחרת חריגה.

    דורש ``https`` מפורש, host מפורש, **port מפורש**, ובלי אישורים
    (user:pass), query או fragment. ‏HTTP, פורט חסר, או כל תוספת סמויה
    נדחים בקול (עיקרון 5) — לא "מתקנים" בשקט לברירת מחדל.
    """
    parts = urllib.parse.urlsplit((url or "").strip())
    if parts.scheme != "https":
        raise InterserverURLError("הכתובת חייבת להתחיל ב-https://")
    if parts.username or parts.password:
        raise InterserverURLError("הכתובת לא יכולה לכלול שם משתמש/סיסמה")
    if parts.query or parts.fragment:
        raise InterserverURLError("הכתובת לא יכולה לכלול query או fragment")
    if not parts.hostname:
        raise InterserverURLError("הכתובת חייבת לכלול host")
    try:
        port = parts.port
    except ValueError as exc:
        raise InterserverURLError("פורט לא תקין") from exc
    if port is None:
        raise InterserverURLError("הכתובת חייבת לכלול פורט מפורש")
    return parts.hostname, port, parts.path


# --- קובץ אישורים: כתיבת 0600 אטומית, וטעינה שדוחה קובץ לא-בטוח --------------

class CredentialFileError(RuntimeError):
    """קובץ אישורים שאינו בטוח לקריאה — קישור, לא-רגיל, או הרשאות פתוחות."""


def write_credential_0600(path, token: str) -> None:
    """כותב את הטוקן לקובץ ``0600`` בכתיבה אטומית עמידה.

    קובץ זמני באותה תיקייה במצב 0600, כתיבה+flush+fsync, שינוי שם אטומי,
    ואז fsync על התיקייה (POSIX). הטוקן לא עובר ב-SQLite, ביומן או
    בהודעת שגיאה — רק לקובץ הזה.

    ‏#860: הקובץ הזמני נפתח ב-``O_EXCL`` (ו-``O_NOFOLLOW`` ב-POSIX) — שם
    קבוע בתיקייה הוא הזמנה ל-symlink שיפנה את הטוקן החוצה, ו-``O_TRUNC``
    היה עוקב אחריו. שארית מריצה שקרסה מוסרת קודם ב-``unlink`` (שאינו
    עוקב אחרי קישור); אם משהו מופיע שוב בין ההסרה לפתיחה — ``O_EXCL``
    נכשל ב-``FileExistsError`` במקום לכתוב לתוכו.

    ‏#291: ‏CodeQL ‏``py/path-injection`` על הפונקציה הזו הוא false positive.
    לה שני קוראים, ושניהם בונים את הנתיב בעצמם: ‏``console_storage.enroll``
    (‏``secondaries/sn_<16 hex>.token``, אחרי ``storage_nodes.valid_node_id``)
    ו-``console_tls`` (שם קבוע תחת ``data_dir``). שום רכיב של הנתיב אינו
    מחרוזת חופשית מהבקשה.
    """
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    tmp = os.path.join(directory, f".{os.path.basename(path)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        # codeql[py/path-injection] false positive: הקוראים בונים את הנתיב בעצמם (docstring, #291)
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = None
    try:
        # codeql[py/path-injection] false positive: הקוראים בונים את הנתיב בעצמם (docstring, #291)
        fd = os.open(tmp, flags, 0o600)
        os.write(fd, token.encode())
        os.fsync(fd)
    finally:
        if fd is not None:
            os.close(fd)
    if os.name == "posix":
        # codeql[py/path-injection] false positive: הקוראים בונים את הנתיב בעצמם (docstring, #291)
        os.chmod(tmp, 0o600)          # מכבד umask שאולי הרחיב את המצב
    # codeql[py/path-injection] false positive: הקוראים בונים את הנתיב בעצמם (docstring, #291)
    os.replace(tmp, path)
    if os.name == "posix":
        # codeql[py/path-injection] false positive: הקוראים בונים את הנתיב בעצמם (docstring, #291)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def load_credential(path) -> str:
    """טוען טוקן מקובץ אישורים, ודוחה קובץ שאינו בטוח.

    דוחה קישור סימבולי, קובץ שאינו רגיל, ו-(POSIX) קובץ שאינו בבעלות
    התהליך או שהרשאותיו אינן בדיוק ``0600``. עיקרון 5: אישור שנטען
    מקובץ פתוח־לכל אינו "אישור תקין".
    """
    path = os.fspath(path)
    # ‏#747: פותחים תחילה עם ``O_NOFOLLOW`` (קישור סימבולי נדחה בפתיחה
    # עצמה, אטומית) ו-``O_CLOEXEC``, ואז מאמתים על ה-fd **הפתוח** דרך
    # ``os.fstat`` — ולא ``lstat`` על הנתיב לפני הפתיחה. אחרת תוקף עם
    # כתיבה לספרייה מחליף את הקובץ שאומת ב-symlink בין ה-בדיקה ל-open
    # (‏TOCTOU), והשירות קורא יעד לא-מאומת. האימות על ה-fd מבטיח שמה
    # שנבדק הוא בדיוק מה שנקרא (עיקרון 5).
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        # ‏ELOOP = הנתיב האחרון הוא קישור סימבולי (‏O_NOFOLLOW).
        raise CredentialFileError(
            f"קובץ האישורים אינו קובץ בטוח לפתיחה: {path}") from exc
    try:
        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode):
            raise CredentialFileError(f"קובץ האישורים אינו קובץ רגיל: {path}")
        if os.name == "posix":
            if st.st_uid != os.getuid():
                raise CredentialFileError("קובץ האישורים אינו בבעלות התהליך")
            if st.st_mode & 0o777 != 0o600:
                raise CredentialFileError(
                    f"קובץ האישורים אינו 0600 ({oct(st.st_mode & 0o777)})")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = None                # ה-fd עבר לבעלות ה-file object
            return fh.read().strip()
    finally:
        if fd is not None:
            os.close(fd)


# --- הסתרת סודות ליומן ------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),                     # טוקן/גיבוב hex
    re.compile(r"(?i)(code\s*[:=]\s*)[A-Z0-9-]{5,}"),
    # ‏#860: קוד pairing בצורתו (XXX-XXX-XXX מהאלפבית שלנו) גם בלי `code=`.
    re.compile(r"\b[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}\b"),
    # ‏16/10: והצורה של היום, ‏XXXX-XXXX (שמונה תווים, מסך השרת F4).
    re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b"),
    re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----",
               re.DOTALL),
]


def redact_secrets(text: str) -> str:
    """מסתיר Authorization/bearer/טוקן/קוד/מפתח פרטי לפני כתיבה ליומן."""
    result = text or ""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            result = pattern.sub(lambda m: m.group(1) + "[redacted]", result)
        else:
            result = pattern.sub("[redacted]", result)
    return result


# --- שכבת ה-TLS: SPKI-pin, cert-ref, exporter, contexts (#740) --------------
#
# ⚠️ **ממצא נמדד (2026-09-13).** ה-tls-exporter (RFC 9266) מגיע מ-**pyOpenSSL**,
# ולא מ-stdlib ssl כפי שהונח: ל-``ssl.SSLSocket``/``SSLObject`` **אין**
# ``export_keying_material`` — לא ב-3.12, וגם לא ב-3.13/3.14 (נמדד מול
# ‏``Lib/ssl.py`` של v3.13.0 ומול whatsnew-3.13). ‏``get_channel_binding``
# היחיד ב-stdlib הוא ``tls-unique``, שאינו מוגדר ל-TLS 1.3. ‏uvicorn מריץ
# stdlib ssl, ולכן מאזין ה-enrollment הוא **terminator של pyOpenSSL** (בדיוק
# כפי שאסטרה הצביעה ב-#740) — וממנו גם ה-exporter, וגם קליטת תעודת-הלקוח
# החתומה-עצמית הלא-מוכרת בזמן ה-pairing: ‏stdlib ssl דורש CA ואינו יכול
# לקבל תעודה לא-מוכרת (VERIFY אין לו callback). ‏pyOpenSSL 25.0.0 (דביאן 13)
# חושף את ``Connection.export_keying_material`` ואת ``set_verify`` עם callback.
# **המשמעות:** בדיקת ה-channel-binding **רצה גם על 3.12** (היכולת קיימת),
# ואינה מדולגת שם — הדילוג יחול רק אם pyOpenSSL באמת חסר (עיקרון 5:
# מדלגים כשבאמת אין יכולת, לא כדי להסתיר).

#: תווית ה-exporter הנעולה ל-#740 (RFC 9266). ‏bytes — כך pyOpenSSL מצפה.
EXPORTER_LABEL = b"EXPORTER-ImageCtl-Pairing-2.1"
EXPORTER_LENGTH = 32


class ChannelBindingUnavailable(RuntimeError):
    """אי אפשר לחלץ tls-exporter מהחיבור — יכולת ה-TLS חסרה.

    לא נבלע בשקט: ערוץ בלי channel-binding אינו "ערוץ מאובטח בלי הבדיקה",
    הוא ערוץ שלא ניתן לאמת (עיקרון 5). מי שקורא ומקבל את החריגה מכשיל
    את ה-pairing, לא ממשיך בלי הכריכה."""


def _to_der(cert) -> bytes:
    """מנרמל תעודה (pyOpenSSL X509 / cryptography Certificate / PEM / DER)
    ל-DER גולמי, כדי שכל חישובי הטביעה יעבדו על אותו ייצוג בדיוק."""
    from cryptography import x509 as _x509
    from cryptography.hazmat.primitives.serialization import Encoding
    # pyOpenSSL X509
    if cert.__class__.__module__.startswith("OpenSSL"):
        from OpenSSL import crypto
        return crypto.dump_certificate(crypto.FILETYPE_ASN1, cert)
    if isinstance(cert, _x509.Certificate):
        return cert.public_bytes(Encoding.DER)
    data = cert if isinstance(cert, (bytes, bytearray)) else str(cert).encode()
    data = bytes(data)
    try:
        return _x509.load_der_x509_certificate(data).public_bytes(Encoding.DER)
    except ValueError:
        return _x509.load_pem_x509_certificate(data).public_bytes(Encoding.DER)


def spki_sha256(cert) -> str:
    """טביעת SHA-256 (hex) של ה-SubjectPublicKeyInfo של התעודה.

    ‏SPKI ולא טביעת התעודה כולה: המפתח שורד חידוש תעודה, ולכן ה-pin נשאר
    יציב. זה מה שמוצג בחלון ה-pairing ומה שהראשי/משני נצמדים אליו."""
    from cryptography import x509 as _x509
    from cryptography.hazmat.primitives import serialization
    spki = _x509.load_der_x509_certificate(_to_der(cert)).public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()


def certificate_ref(cert) -> str:
    """הפניית SHA-256 (hex) לתעודה **כולה** (DER) — RFC 8705 sender-constraint.

    המשני קושר את הטוקן לתעודת-הלקוח הזו; ping עם טוקן נכון אך תעודה אחרת
    נדחה. בניגוד ל-SPKI, זו הטביעה של התעודה השלמה, לא רק המפתח."""
    return hashlib.sha256(_to_der(cert)).hexdigest()


def verify_pinned_spki(cert, expected_spki: str) -> bool:
    """השוואת SPKI בזמן-קבוע מול ה-pin השמור. ראיה חיובית בלבד."""
    return hmac.compare_digest(spki_sha256(cert), (expected_spki or "").lower())


def verify_pinned_spki_value(actual_spki: str, expected_spki: str) -> bool:
    """כמו :func:`verify_pinned_spki`, אבל בין שתי מחרוזות SPKI מוכנות —
    להצמדה ההדדית ב-pair-begin (המשני משווה את שלו למה שהראשי הצהיר).

    ‏#860: צד חסר אינו התאמה. ``expected`` מגיע מגוף JSON ו-``actual``
    מזהות השרת; ריק מול ריק היה "עובר" — ראיה חיובית בלבד (עיקרון 5)."""
    if not actual_spki or not expected_spki:
        return False
    return hmac.compare_digest(actual_spki.lower(), expected_spki.lower())


def node_id_from_spki(spki: str) -> str:
    """מזהה משני יציב, נגזר מ-SPKI התעודה (לא UUID מקומי, #740).

    ‏#860: ‏SPKI ריק אינו "sn_" — זו זהות בלי מקור, ומעלה ``ValueError``."""
    if not spki:
        raise ValueError("SPKI ריק — אין ממנו מזהה שרת משנה")
    return "sn_" + spki[:16]


def can_export_keying_material() -> bool:
    """האם ניתן לחלץ tls-exporter (RFC 9266) בסביבה הזו — כלומר pyOpenSSL זמין.

    היכולת אינה תלוית-גרסת-פייתון (pyOpenSSL עובד על 3.12 וגם 3.13), ולכן
    בדיקת ה-channel-binding אינה מדולגת על תחנת הפיתוח כל עוד pyOpenSSL מותקן."""
    try:
        from OpenSSL import SSL
    except Exception:                                        # noqa: BLE001
        return False
    return hasattr(SSL.Connection, "export_keying_material")


def tls_exporter_binding(conn, *, label: bytes = EXPORTER_LABEL,
                         context: bytes = b"", length: int = EXPORTER_LENGTH) -> bytes:
    """מחזיר את ה-tls-exporter (RFC 9266) של החיבור, או חריגה אם אין יכולת.

    עובד על ``OpenSSL.SSL.Connection`` (וגם על כל אובייקט עתידי שיחשוף
    ``export_keying_material``). ‏begin ו-complete על **אותה** session TLS
    מקבלים ערך זהה; שתי sessions שונות — ערכים שונים, וזה מה שקושר את
    שני השלבים לאותו ערוץ (הבקרה "begin על A, complete על B")."""
    if hasattr(conn, "export_keying_material"):
        return conn.export_keying_material(label, length, context or None)
    raise ChannelBindingUnavailable(
        "לחיבור ה-TLS אין export_keying_material — התקינו pyOpenSSL")


#: ‏#2139: כמה אחורה notBefore — מעבר להיסט של RTC בשעון מקומי (עד ‏UTC±14).
NOT_BEFORE_BACKDATE = timedelta(hours=24)


def generate_self_signed(common_name: str, *, ip_sans=(), dns_sans=(),
                         days: int = 3650) -> tuple[bytes, bytes]:
    """‏(cert_pem, key_pem) — תעודת EC P-256 חתומה-עצמית לזהות בין-שרתית.

    ‏EC P-256: מפתח קטן, מהיר, ונתמך ב-TLS 1.3. ה-SANs נדרשים כדי
    שהצד השני יוכל להצמיד SPKI מול חיבור לכתובת הנכונה בבדיקות loopback.

    ‏notBefore יום אחורה (‏#2139): שעון חומרה בשעון מקומי (‏Hyper-V, ברזל
    שהיה עליו Windows) מקדים את השעון ב-3 שעות עד ש-chrony מתקן. תעודה
    שנוצרה בחלון הזה הייתה "עדיין לא בתוקף" לדפדפן, ולשרת שני ששעונו
    נכון, עד שהשעון מדביק אותה."""
    import datetime
    from ipaddress import ip_address
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    sans = [x509.IPAddress(ip_address(ip)) for ip in ip_sans]
    sans += [x509.DNSName(d) for d in dns_sans]
    builder = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - NOT_BEFORE_BACKDATE)
        .not_valid_after(now + datetime.timedelta(days=days))
    )
    if sans:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(sans), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _load_cert_key(ctx, cert_pem: bytes, key_pem: bytes) -> None:
    """טוען תעודה+מפתח ל-context כ**אובייקטי cryptography** (לא pyOpenSSL
    X509/PKey, שמסומנים deprecated ב-pyOpenSSL 25)."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    ctx.use_certificate(x509.load_pem_x509_certificate(cert_pem))
    ctx.use_privatekey(load_pem_private_key(key_pem, password=None))
    ctx.check_privatekey()


def _accept_any(*_args) -> bool:
    """callback אימות שקולט **כל** תעודת-לקוח, כולל חתומה-עצמית לא-מוכרת.

    זה בדיוק מה ש-stdlib ssl אינו יכול, וזו הסיבה ל-pyOpenSSL: המשני
    קולט את תעודת הראשי בזמן ה-pairing ואז **נצמד** אליה בקוד — האמון
    אינו מהשרשרת אלא מה-pin + הקוד החד-פעמי."""
    return True


def build_server_tls_context(cert_pem: bytes, key_pem: bytes):
    """‏context של מאזין ה-enrollment (המשני): TLS 1.3 בלבד, דורש תעודת-לקוח,
    וקולט אותה גם כשאינה מוכרת (הצמדה בקוד, לא בשרשרת)."""
    from OpenSSL import SSL
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_max_proto_version(SSL.TLS1_3_VERSION)
    _load_cert_key(ctx, cert_pem, key_pem)
    ctx.set_verify(SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, _accept_any)
    return ctx


def build_pinned_client_context(expected_server_spki: str,
                                cert_pem: bytes, key_pem: bytes):
    """‏context של הלקוח היוצא (הראשי): מציג תעודת-לקוח, ומצמיד את ה-SPKI
    של המשני. אי-התאמת SPKI מפילה את ה-handshake — hard fail, בלי TOFU."""
    from OpenSSL import SSL
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_max_proto_version(SSL.TLS1_3_VERSION)
    _load_cert_key(ctx, cert_pem, key_pem)
    want = (expected_server_spki or "").lower()

    def verify_spki(_conn, x509obj, _errno, depth, _ok):
        if depth != 0:
            return True                  # תעודות ביניים (אין כאן) — לא מצמידים
        return verify_pinned_spki(x509obj, want)

    ctx.set_verify(SSL.VERIFY_PEER, verify_spki)
    return ctx
