"""האם ה-GRUB של השרת עצמו נעול — שורת הבריאות של #2452.

שרת שעודכן מהכפתור משרת שהותקן לפני #2450 נשאר עם `grub>` פתוח בלי סיסמה, וביומן אין דבר: העדכון הראשון
רץ בקוד של הגרסה **המותקנת**, שאינה קוראת ל-`deploy/grub-lock-host.sh` (VM, 21/11). שורת `grub_password`
(#2454) אינה עונה על זה — היא תזכורת לשמור את הסיסמה מחוץ לשרת, וצהובה גם בשרת נעול.

הראיה היא `/boot/grub/grub.cfg` שנקרא בחזרה, כמו ה-verdict של המתקין (`installer/lib/grublock.sh`,
`grub_menu_locked`) — נעול = `set superusers="imagesrv"` ושורת `password_pbkdf2 imagesrv <hash>` **לפני
הערך הראשון**. שלוש תשובות נפרדות (עיקרון 5): ``ok`` נעול, ``bad`` נקרא ואינו נעול, ``unknown`` לא נקרא
או שאינו תפריט בכלל. "לא נקרא" אינו "נעול" ואינו "פתוח".

לא נבדק כאן: שערכי האתחול עוברים בלי סיסמה (`--unrestricted`) — זה נשאר ל-verdict של
`deploy/grub-lock-host.sh`, שרץ לפני שה-grub.cfg נכנס.
"""

from __future__ import annotations

import logging
from pathlib import Path

from boot.grub_lock import GRUB_USER, is_pbkdf2_hash

log = logging.getLogger("imagesrv.grub_lock_row")

__all__ = ["GRUB_CFG", "lock_status"]

#: דביאן, BIOS ו-UEFI כאחד. grub-mkconfig כותב אותו 0400 כשיש בו `password_*`, והשרת רץ כ-root.
GRUB_CFG = Path("/boot/grub/grub.cfg")

_FIX = ("הנעילה נכנסת בעדכון נוסף מהכפתור, או בהרצת "
        "`sh /opt/imagesrv/deploy/grub-lock-host.sh /opt/imagesrv` כ-root")


def lock_status(path: str | Path | None = None) -> tuple[str, str]:
    """(מצב, פירוט) של ה-GRUB של השרת: ``ok`` / ``bad`` / ``unknown``."""
    path = Path(path) if path is not None else GRUB_CFG
    try:
        text = path.read_bytes().decode("utf-8", "replace")
    except OSError as exc:
        log.warning("grub.cfg %s unreadable: %s", path, exc)
        return "unknown", f"לא נבדק אם ה-GRUB של השרת נעול — {path} לא נקרא; הפרטים ביומן השרת"
    superusers = password = entries = False
    for line in text.splitlines():
        words = line.split()
        if words[:1] in (["menuentry"], ["submenu"]):
            entries = True
            break                                  # GRUB קורא את הנעילה לפני הערך הראשון בלבד
        if line.strip() == f'set superusers="{GRUB_USER}"':
            superusers = True
        elif len(words) == 3 and words[:2] == ["password_pbkdf2", GRUB_USER] and is_pbkdf2_hash(words[2]):
            password = True
    if not entries:
        log.warning("grub.cfg %s has no menu entry: not a menu, the lock cannot be told", path)
        return "unknown", f"לא נבדק אם ה-GRUB של השרת נעול — ב-{path} אין אף ערך תפריט"
    if superusers and password:
        return "ok", f"ה-GRUB של השרת נעול: set superusers ו-password_pbkdf2 ב-{path}"
    missing = [name for name, there in (("set superusers", superusers), ("password_pbkdf2", password)) if not there]
    if superusers:       # נעילה בלי סיסמה: איש אינו עורך, וגם ערך השחזור לא נפתח
        return "bad", (f"ה-GRUB של השרת אינו נעול כמו ש-#2450 מתכוון — ב-{path} חסר password_pbkdf2 לפני הערך "
                       f"הראשון: אף אחד אינו עורך, אבל גם ערך השחזור לא ייפתח בסיסמה. {_FIX}")
    return "bad", (f"ה-GRUB של השרת פתוח — ב-{path} חסר {' ו-'.join(missing)} לפני הערך הראשון: מי שעומד מול "
                   f"השרת עורך שורת קרנל (e, c) ומקבל root בלי סיסמה. {_FIX}")
