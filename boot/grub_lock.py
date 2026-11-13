"""ImageSrv — נעילת עורך ה-GRUB של התחנות (#2437).

בלי `superusers`, GRUB משאיר את `e` (עריכת ערך) ואת `c` (שורת פקודה)
פתוחים לכל מי שיושב מול המקלדת: `init=/bin/sh` בשורת ה-`linux` הוא
מעטפת root ב-initramfs של הסוכן, עם כל הדיסקים של המחשב. ESC שנלחץ
בזמן האתחול פותח את התפריט גם בקובץ החבוי (`timeout=0`), ולכן הנעילה
נכנסת לכל קובץ שהשרת מגיש — לא רק לתפריט הגלוי.

שני חלקים, ושניהם נדרשים:

* `set superusers` + `password_pbkdf2` — עריכה ושורת פקודה דורשות סיסמה.
* `--unrestricted` על **כל** `menuentry` — אחרת GRUB דורש סיסמה גם
  כדי **לאתחל** את הערך, ותחנה (או משכפל) נתקעת מול שאלת סיסמה
  (עיקרון 1). זה ב-`grub_menu.py`, ליד הערכים עצמם.

המודול טהור: הוא מקבל סיסמה ומחזיר טקסט. השמירה והקריאה של הסיסמה הן
של השרת (`server/system/grub_password.py`).

**כשאין hash תקין — נועלים בכל זאת.** `set superusers` בלי משתמש רשום
פירושו שאיש אינו יכול לערוך — וזה הכיוון הבטוח: "לא הצלחנו לקרוא את
הסיסמה" אינו "פתוח לכולם" (עיקרון 5). האתחול עצמו אינו נפגע, כי כל
הערכים `--unrestricted`.
"""

from __future__ import annotations

import hashlib
import re
import secrets

__all__ = ["GRUB_USER", "ITERATIONS", "pbkdf2_hash", "is_pbkdf2_hash", "lock_lines"]

#: שם המשתמש שהטכנאי מקליד ב-GRUB. ASCII, בלי תו שמפורש בתחביר.
GRUB_USER = "imagesrv"

#: grub-mkpasswd-pbkdf2 משתמש כברירת מחדל ב-10,000. הקובץ מוגש ב-TFTP
#: וב-HTTP וקריא לכל מי שברשת ההפצה, ולכן ה-hash חייב להיות יקר לניחוש.
#: המחיר משולם רק כשמישהו מקליד סיסמה ב-GRUB, לא באתחול רגיל.
ITERATIONS = 100_000

#: אורכי ברירת המחדל של grub-mkpasswd-pbkdf2 (64 בתים מלח, 64 בתים פלט).
SALT_BYTES = 64
HASH_BYTES = 64

#: התחביר ש-`password_pbkdf2` של GRUB מקבל (grub-core/commands/password_pbkdf2.c):
#: `grub.pbkdf2.sha512.<c>.<salt hex>.<hash hex>`, ו-hex בזוגות. כאן צר מזה
#: בכוונה: ספירה עשרונית בלי אפס מוביל (GRUB קורא אותה ב-strtoul בבסיס 0,
#: ו-"0..." היה נקרא כאוקטלי), ו-hex באותיות גדולות כמו הכלי של GRUB.
_PBKDF2 = re.compile(r"^grub\.pbkdf2\.sha512\.[1-9][0-9]*\.(?:[0-9A-F]{2})+\.(?:[0-9A-F]{2})+$")


def pbkdf2_hash(password: str, *, salt: bytes | None = None,
                iterations: int = ITERATIONS) -> str:
    """ה-hash בפורמט של grub-mkpasswd-pbkdf2 — בלי להריץ אותו.

    GRUB בודק בדיוק PBKDF2-HMAC-SHA512 על בתי הסיסמה, עם המלח, הספירה
    ואורך הפלט שקרא מהמחרוזת — ולכן hashlib נותן אותה תוצאה.
    """
    if not password:
        raise ValueError("empty GRUB password")
    if salt is None:
        salt = secrets.token_bytes(SALT_BYTES)
    if not salt:
        raise ValueError("empty salt")
    digest = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt,
                                 iterations, HASH_BYTES)
    return f"grub.pbkdf2.sha512.{iterations}.{salt.hex().upper()}.{digest.hex().upper()}"


def is_pbkdf2_hash(value: object) -> bool:
    return isinstance(value, str) and bool(_PBKDF2.match(value))


def lock_lines(password_hash: str = "") -> list[str]:
    """השורות שנכנסות לראש כל grub.cfg, לפני כל `menuentry`.

    hash שאינו בתחביר הצפוי אינו נכתב — הוא היה מגיע לשורת פקודה של
    GRUB — אבל הנעילה נשארת.
    """
    lines = [f'set superusers="{GRUB_USER}"']
    if is_pbkdf2_hash(password_hash):
        lines.append(f"password_pbkdf2 {GRUB_USER} {password_hash}")
    return lines
