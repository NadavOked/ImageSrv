"""‏#1997 (נדב, 22/10): שם של מחשב או של שרת הוא ה-hostname — ולכן באנגלית.

הכלל אחד, והוא חל על: מחשב בנייה, משכפל ותחנה; שם השרת עצמו (המתקין, האשף,
‏``POST /hostname``); ושם של שרת משנה ברישום ובעריכה. אותיות לטיניות, ספרות
ומקף, עד 15 תווים (NetBIOS — Windows מקצר שם ארוך יותר בשקט), לא מתחיל ולא
נגמר במקף.

עברית מותרת **רק** בשם אימג', שם תיקיית אימג'ים ושם דיסק (``images.
validate_display_name``, ‏``slot_names``) — לא כאן.

אותו כלל יושב בשני מקומות שאינם Python, והטסטים משווים אותם לזה:
‏``agent/lib/postdeploy/hostname.sh`` (``write_hostname`` — מסרב לפני הכתיבה ל-Windows)
ו-``installer/lib/answers.sh`` (שם השרת במתקין).
"""

from __future__ import annotations

import re

#: NetBIOS. ‏``sessions.HOSTNAME_MAX`` הוא אותו מספר, מכאן.
HOSTNAME_MAX = 15

_OK = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")

#: מה שהמפעיל קורא בכל הודעת סירוב — אותו ניסוח בכל המסלולים.
RULE = (f"שם מחשב או שרת באנגלית בלבד: אותיות אנגליות, ספרות ומקף, "
        f"עד {HOSTNAME_MAX} תווים, לא מתחיל ולא נגמר במקף")


def is_valid(name: object) -> bool:
    """האם ``name`` (כפי שהוא — בלי strip) הוא שם מחשב חוקי."""
    return (isinstance(name, str) and len(name) <= HOSTNAME_MAX
            and _OK.fullmatch(name) is not None)


def refusal(name: object, what: str = "השם") -> str | None:
    """הודעת הסירוב בעברית ל-``name``, או ``None`` כשהוא חוקי."""
    if is_valid(name):
        return None
    if not isinstance(name, str) or not name:
        return f"{what} ריק — {RULE}"
    return f"{what} '{name}' אינו חוקי — {RULE}"
