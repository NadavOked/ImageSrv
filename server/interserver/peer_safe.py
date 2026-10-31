"""‏#1179: תשובה של שרת משני — בטוחה לציור בקונסולה של הראשי.

המשני הוא שרת אחר, באתר אחר, ואינו מקור אמין לדפדפן של הראשי. הקונסולה
מציירת את העמודים שלו **באותן פונקציות** שמציירות את הראשי, וברוב המקומות
היא מבריחה (``esc``) — אבל לא בכל אינטרפולציה: מספר מוצג כפי שהוא, ``.length``
של רשימה מוצג כפי שהוא. משני עוין שישלח מחרוזת במקום מספר, או אובייקט
``{"length": "<img …>"}`` במקום רשימה, היה מזריק HTML. ההגנה כאן כללית ואינה
תלויה בצורה של כל נתיב: **אף מחרוזת** בתשובה (ערך או מפתח) אינה יכולה לשאת
את התווים שבונים תגית, סוגרים attribute או יוצרים ישות HTML. הם מוחלפים
בתווים דומים לעין, כך שהטקסט נשאר קריא.

המחיר: שם אמיתי עם ``<``/``&``/גרש יוצג בתו הדומה. בשלב 2 (כתיבה) ערך כזה
אסור שיחזור למשני כפי שהוא — הוא אינו המקור.
"""

from __future__ import annotations

#: ‏‹ › ” ’ ˋ ＆ — דומים לעין, אינם תחביר HTML.
_DEFANG = str.maketrans({"<": "‹", ">": "›", '"': "”",
                         "'": "’", "`": "ˋ", "&": "＆"})


#: התווים ש-``defang`` כותב — כדי לזהות אותם בכתיבה בחזרה (שלב 2).
DEFANGED_CHARS = frozenset("‹›”’ˋ＆")


def carries_defanged(value) -> str | None:
    """המחרוזת הראשונה (ערך או מפתח) שנושאת תו של ``defang``, או ``None``.

    ‏#1179 שלב 2: טופס בהקשר משני מתמלא מתשובה שעברה ``defang``. שמירה שלו כפי
    שהוא הייתה כותבת למשני ‹ במקום < — שינוי שקט של ערך שהמפעיל לא נגע בו.
    הראשי מסרב לכתיבה כזו בגלוי; את הערך המקורי מתקנים בקונסולה של המשני עצמו."""
    if isinstance(value, str):
        return value if DEFANGED_CHARS & set(value) else None
    if isinstance(value, list):
        return next((hit for v in value if (hit := carries_defanged(v))), None)
    if isinstance(value, dict):
        return next((hit for k, v in value.items()
                     if (hit := carries_defanged(str(k)) or carries_defanged(v))), None)
    return None


def defang(value):
    """עותק של ``value`` (JSON מפוענח) שבו כל מחרוזת — גם מפתח — בלי תווי HTML."""
    if isinstance(value, str):
        return value.translate(_DEFANG)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return [defang(v) for v in value]
    if isinstance(value, dict):
        return {defang(str(k)): defang(v) for k, v in value.items()}
    return None
