"""סיסמת עורך ה-GRUB של התחנות (#2437).

התפריט שהשרת מגיש נעול (`boot/grub_lock.py`): `e` ו-`c` דורשים משתמש
`imagesrv` וסיסמה. הסיסמה נוצרת כאן, אקראית, בעלייה הראשונה של השרת —
בהתקנה חדשה (השרת עולה בסוף ההתקנה) ובשרת שמשודרג מגרסה שלא הכירה אותה —
ונשמרת ב-`/etc/imagesrv/grub-password`, 0600, בבעלות root. **לתפריט נכנס
רק ה-hash**: הקובץ מוגש ב-TFTP וב-HTTP וקריא לכל מי שברשת ההפצה.

#2450: זו גם הסיסמה של ה-GRUB של השרת עצמו. המתקין יוצר את הקובץ לפני
ה-update-grub הראשון (`installer/lib/grublock.sh`), וכאן הוא רק נקרא — אותה
סיסמה לשרת ולתחנות. מנהל מחובר רואה אותה בהגדרות ("סיסמת GRUB", `read`
דרך `server/console/grubpw.py`, admin בלבד, וכל צפייה ביומן). זה הממשק היחיד
שמחזיר אותה.

הסיסמה עצמה אינה משתנה מעלייה לעלייה; ה-hash כן (מלח חדש בכל עלייה),
וזה בסדר — כל hash שנוצר ממנה מאמת אותה.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from boot.grub_lock import pbkdf2_hash
from server.core.atomicfile import write_atomic

__all__ = ["PASSWORD_FILE", "new_password", "load_or_create", "read", "ensure"]

PASSWORD_FILE = Path("/etc/imagesrv/grub-password")

#: מה שטכנאי מקליד במקלדת של GRUB — שהיא תמיד US, בלי הד על המסך. בלי
#: אותיות גדולות ובלי תווים שמתבלבלים (0/o, 1/l/i), בארבע קבוצות של חמש:
#: 31^20 ≈ 2^99.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_GROUPS, _GROUP_LEN = 4, 5


def new_password() -> str:
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN))
                    for _ in range(_GROUPS))


def _usable(password: str) -> bool:
    """מה שאפשר להקליד ב-GRUB: ASCII מודפס בלי רווח. טכנאי רשאי להחליף
    את הסיסמה ביד — אבל לא בכזו שאי-אפשר להקליד."""
    return bool(password) and all("!" <= ch <= "~" for ch in password)


def load_or_create(path: Path = PASSWORD_FILE) -> tuple[str, bool]:
    """הסיסמה, ו-True אם נוצרה עכשיו. זורקת OSError/ValueError על כל מה
    שאינו סיסמה שנקראה בחזרה (עיקרון 5) — הקורא נועל בלי סיסמה."""
    path = Path(path)
    created = False
    try:
        text = path.read_text(encoding="ascii")
    except FileNotFoundError:
        password = new_password()
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        write_atomic(path, password + "\n", encoding="ascii", mode=0o600)
        text, created = path.read_text(encoding="ascii"), True
    password = text.strip()
    if not _usable(password):
        raise ValueError(f"{path} does not hold a password GRUB can take "
                         "(printable ASCII, no spaces)")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        # נקרא אבל פתוח לאחרים — סוגרים, ולא מוותרים עליו.
        os.chmod(path, 0o600)
    return password, created


def read(path: Path = PASSWORD_FILE) -> str:
    """הסיסמה כפי שהיא בקובץ — בלי ליצור אותו (#2450, הקונסולה). זורקת
    OSError/ValueError על כל מה שאינו סיסמה שאפשר להקליד ב-GRUB."""
    path = Path(path)
    password = path.read_text(encoding="ascii").strip()
    if not _usable(password):
        raise ValueError(f"{path} does not hold a password GRUB can take")
    return password


def ensure(path: Path = PASSWORD_FILE) -> tuple[str, bool, str | None]:
    """(hash, נוצרה עכשיו, שגיאה). בשגיאה ה-hash ריק — והתפריטים ננעלים
    בלי סיסמה בכלל (איש אינו עורך), לא נפתחים."""
    try:
        password, created = load_or_create(path)
        return pbkdf2_hash(password), created, None
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return "", False, (f"GRUB password {path}: {exc} -- the boot menus stay locked, "
                           "with no password that opens them (#2437)")
