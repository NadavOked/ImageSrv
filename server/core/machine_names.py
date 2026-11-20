"""#3123 (נדב, 14/11): המפעיל נותן **מספר**, והשרת קובע את השם לפי התפקיד.

"נתתי למחשב שם 1, הוא יוסיף לו אוטומטית משכפל-1, ובבנייה זה יהיה בנייה-1."

שני שמות לכל מכונה ממוספרת, ולא במקרה:

* **השם הטכני** (`cloner-N`, `build-N`) הוא מה שנשמר ב-`machines.suffix`. הוא
  ה-hostname, ולכן הוא באנגלית (`hostname_rule`, #1997), ופלט GRUB חייב להיות
  ASCII. הוא גם מה שהסוכן מקבל ב-hello ומה שהאישורים בשרת משווים אליו.
* **שם התצוגה** (`משכפל-N`, `בנייה-N`) הוא מה שהמפעיל רואה. הוא נגזר מהשם
  הטכני בקריאה ואינו נשמר: שינוי תפקיד משנה את הקידומת ולא את המספר, ואין
  שני עותקים שיכולים לסטות.

מכונה ששמה אינו בצורה הזו (נרשמה לפני #3123) נשארת כמו שהיא: ``number_of``
מחזיר ``None``, ו-``label`` מחזיר את ה-``suffix`` כפי שהוא. אין מיגרציה.

המספר הוא ממה שהמוח קורא כדי לבחור איזה משכפל נשאר דלוק (#3064, G13):
``number_of(role, suffix)``.
"""

from __future__ import annotations

import re

#: תפקיד הקבוצה (`groups.role`) → (קידומת טכנית, קידומת תצוגה).
PREFIX = {"cloner": ("cloner", "משכפל"), "build": ("build", "בנייה")}
NUMBER_MIN = 1
NUMBER_MAX = 99

_DIGITS = re.compile(r"\d+")
_TECH = re.compile(r"(cloner|build)-([1-9]\d?)")

#: ההסבר שהמפעיל קורא — אותו ניסוח בכל מסלולי הרישום.
RULE = (f"מספר שלם בין {NUMBER_MIN} ל-{NUMBER_MAX} — השרת משלים את השם "
        "(משכפל-N או בנייה-N)")


def numbered_roles() -> tuple[str, ...]:
    """התפקידים ששמם נקבע לפי מספר (כיתה: סיומת 01-99 או INS, כמו קודם)."""
    return tuple(PREFIX)


def technical(role: str, number: int) -> str:
    """``cloner-3`` / ``build-3`` — מה שנשמר ב-`machines.suffix`."""
    return f"{PREFIX[role][0]}-{number}"


def display(role: str, number: int) -> str:
    """``משכפל-3`` / ``בנייה-3`` — מה שהמפעיל רואה."""
    return f"{PREFIX[role][1]}-{number}"


def parse_number(text: object) -> int | None:
    """מה שהמפעיל הקליד כמספר: ספרות בלבד (``"3"``, ``"03"``), 1..99, או ``None``.

    ספרות בלבד, בכוונה: ``"3.5"``, ``"-3"``, ``"3 "`` בפנים, ו-``"٣"``
    (ספרה ערבית ש-``int()`` היה מקבל) אינם מספר. רווחים בקצוות נחתכים."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not (s.isascii() and _DIGITS.fullmatch(s)):
        return None
    value = int(s)
    return value if NUMBER_MIN <= value <= NUMBER_MAX else None


def refusal(text: object) -> str | None:
    """הודעת הסירוב למספר שאינו תקין, או ``None``. נוקבת במה שהוקלד."""
    if parse_number(text) is not None:
        return None
    shown = text.strip() if isinstance(text, str) else ""
    if not shown:
        return f"המספר ריק — {RULE}"
    return f"המספר '{shown}' אינו תקין — {RULE}"


def is_number_text(text: object) -> bool:
    """האם ``text`` הוא **ניסיון** להקליד מספר (כולו ספרות) — גם אם מחוץ לטווח.

    ההבחנה: ``"100"`` הוא מספר שגוי ויקבל את הודעת המספר, ולא יתפרש כשם
    חופשי שהיה מתקבל כ-hostname (``"100"`` חוקי כ-hostname אבל Windows דוחה
    שם שכולו ספרות, ו-#3123 אומר שמספר הוא מספר)."""
    return isinstance(text, str) and text.strip().isascii() and bool(_DIGITS.fullmatch(text.strip()))


def number_of(role: str | None, suffix: object) -> int | None:
    """המספר של מכונה בתפקיד ``role`` ששמה ``suffix``, או ``None`` (שם ישן/כיתה).

    הקידומת חייבת להתאים לתפקיד: ``build-1`` במשכפל אינו "בנייה-1" אלא שם
    ישן שמקרה שהוא נראה כך, ומוצג כמו שהוא."""
    if role not in PREFIX or not isinstance(suffix, str):
        return None
    m = _TECH.fullmatch(suffix)
    if m is None or m.group(1) != PREFIX[role][0]:
        return None
    return int(m.group(2))


def label(role: str | None, suffix: object) -> str:
    """השם שהמפעיל רואה: ``משכפל-N`` / ``בנייה-N`` לממוספרת, אחרת ה-``suffix`` כמו שהוא."""
    number = number_of(role, suffix)
    if number is None:
        return suffix if isinstance(suffix, str) else ""
    return display(role, number)


def match(role: str, token: object, suffix: object) -> bool:
    """האם ``token`` (מספר, שם תצוגה, שם טכני או ה-suffix עצמו) מציין את המכונה.

    "לפי שם או לפי מספר" (#3123): ``3``, ``"3"``, ``"משכפל-3"``, ``"cloner-3"``.
    מספר מתאים רק למכונה **ממוספרת** בתפקיד הזה — מכונה בשם ישן אינה "מספר 3"."""
    if isinstance(token, bool):
        return False
    number = number_of(role, suffix)
    if isinstance(token, int):
        return number is not None and number == token
    if not isinstance(token, str):
        return False
    t = token.strip()
    if not t:
        return False
    if _DIGITS.fullmatch(t) and t.isascii():
        return number is not None and number == int(t)
    return t == suffix or (number is not None and t in (display(role, number), technical(role, number)))
