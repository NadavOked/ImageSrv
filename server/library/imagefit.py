"""האם אימג' נכנס לכונן יעד, ואם לא — מה צריך לכווץ (‏#87).

מודול נפרד מ-`images.py` ולא עוד שלושים שורות בתוכו: ‏`images.py` הוא
**הספרייה** — סריקה, הגשה, אימות — ו-#297 כבר מונה אותו בין המודולים
שחצו את מגבלת ~300 השורות. מה שיושב כאן הוא שאלה אחת, והיא לא של
הספרייה: בהינתן פריסה ובהינתן כונן, מה ההפרש ומי המחיצה שנושאת אותו.

הגבול בין המודולים הוא גם הגבול בין שתי החלטות שאסור לערבב:
‏`images.required_bytes` היא **הדרישה** — מה שהשחזור אוכף בבדיקה 2.7,
ומה שהסינון בשרת נשען עליו. מה שכאן הוא **הפער** בלבד, והוא ייעוץ:
אף מסלול שחזור אינו קורא אותו, ואי אפשר להרפות דרך כאן את מה
שנאכף שם.
"""

from __future__ import annotations

# ‏`_whole` מיובא ולא משוכפל: "מה נחשב מספר בייטים" חייב להיות תשובה
# אחת. ‏`bool` הוא `int` בפייתון, ורצפה `True` שנקראת כ-1 היא מספר
# שנראה תקין לחלוטין ואינו נכון — בדיוק סוג הפער ששני עותקים מייצרים.
from server.library.images import (GPT_TAIL_BYTES, _whole, layout_end_bytes,
                           required_bytes)


def expandable_candidate(manifest: dict) -> dict | None:
    """המחיצה שהשחזור מותח — ולכן היחידה שכיווץ בקליטה נוגע בה.

    אותו כלל בדיוק כמו `_expand_candidate` בסוכן, ומאותו טעם: מניפסט
    שמסמן **בדיוק** אחת גובר (זו העקיפה הידנית), ואחרת המחיצה האחרונה
    **פיזית** שתפקידה `windows`/`linux` — לפי `start_sector` ולא לפי סדר
    הרשימה, שהוא סדר אינדקסים (‏#58). יותר ממחיצה מסומנת אחת חוזרת
    לבחירה האוטומטית במקום להכריע בין השתיים.

    ‏`recovery` לעולם אינה מועמדת, וזו לא פינה: היא הפריסה השכיחה של
    Windows 11 והיא יושבת **אחרי** המערכת. לכווץ אותה היה מקטין את
    האימג' בלי לגעת במה שתופס אותו.

    ‏`None` = אין מועמד. זה **אינו** "אין מה לכווץ בכל מקרה" — זה
    "לא ידוע מה לכווץ", והקורא אומר זאת במילים ולא מציע מספר.
    """
    parts = [p for p in manifest.get("partitions") or [] if isinstance(p, dict)]
    marked = [p for p in parts if p.get("expandable") is True]
    if len(marked) == 1:
        return marked[0]
    system = [p for p in parts
              if p.get("role") in ("windows", "linux")
              and _whole(p.get("start_sector")) is not None]
    return max(system, key=lambda p: p["start_sector"]) if system else None


#: מה שפותח סבב שלא עודכן בשביל #59 ממשיך לכתוב (pulls.py, station.py):
#: לא ידוע = הבחירה האוטומטית, בדיוק ההתנהגות מלפני ה-Issue הזה.
EXPAND_AUTO = "auto"
#: כיבוי מפורש של ההרחבה — לשחזר בגודל המקורי בלי לגעת בטבלה.
EXPAND_NONE = "none"


def validate_expand_choice(manifest: dict, raw: object) -> str:
    """מנרמלת ומאמתת את בחירת ההרחבה שהגיעה מהקונסולה בפתיחת סבב (#59).

    ‏`None` (השדה לא נשלח בכלל) הוא בדיוק `EXPAND_AUTO` — כדי ש-`pulls.py`
    ו-`station.py`, ששניהם עדיין לא שולחים את השדה, ימשיכו לקבל את
    הבחירה האוטומטית בלי לדעת שהיא קיימת (עיקרון 1). ‏`EXPAND_NONE`
    מכבה. מספר (כמחרוזת או `int`) חייב להצביע על מחיצת windows/linux
    שקיימת **במניפסט הזה** — בדיוק המחיצות שהבחירה האוטומטית עצמה
    שוקלת (`expandable_candidate` למעלה) — אחרת נדחה בשמו, לפני שהסבב
    נפתח ולא אחרי שהוא כבר משדר לכיתה.
    """
    if raw is None or raw == EXPAND_AUTO:
        return EXPAND_AUTO
    if raw == EXPAND_NONE:
        return EXPAND_NONE
    try:
        index = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"בחירת הרחבה לא מוכרת: {raw!r}")
    parts = [p for p in manifest.get("partitions") or [] if isinstance(p, dict)]
    match = next((p for p in parts if p.get("index") == index), None)
    if match is None or match.get("role") not in ("windows", "linux"):
        raise ValueError(
            f"מחיצה {index} אינה מחיצת windows/linux באימג' הזה — "
            "אי אפשר לבחור אותה להרחבה")
    return str(index)


def shrink_bytes(manifest: dict, floor_bytes: object) -> int | None:
    """כמה בייטים הפריסה חייבת לאבד כדי להיכנס לכונן בגודל `floor_bytes`.

    ‏`0` = נכנס כמו שהוא. ‏`None` = **לא ניתן להכריע** — רצפה שאינה מספר
    בייטים, או מניפסט בלי גיאומטריה מלאה: אפשר לדעת שאימג' כזה אינו
    נכנס (`required_bytes` נופל אחורה לערך המוצהר) ואי אפשר לדעת
    **בכמה** לכווץ. ‏`0` ו-`None` נראים דומה לקורא רשלני, והם המלצות
    הפוכות (עיקרון 5).

    **ההפרש `required_bytes − floor` הוא התשובה — מאז #1171 בלבד.**
    כל עוד `required_bytes` עיגל כלפי מעלה למגה-בייט הוא לא היה: כונן
    פיזי אינו כפולה של מגה (‏`256,060,514,304 mod 1MiB = 352,256`), וכיווץ
    בגודל ההפרש נחת ברזולוציה שהעיגול דחף בחזרה מעל הרצפה — אימג' שכווץ
    "בדיוק כמה שנדרש" נחסם בבדיקה 2.7 מול כיתה, וה-CI הוא שתפס את זה.
    העיגול הוסר (הדרישה היא סוף הפריסה ועוד `GPT_TAIL_BYTES`), ומכאן
    שהנוסחה והדרישה נגזרות מאותו ביטוי אחד. התוצאה היא המינימום — כיווץ
    בבייט אחד פחות אינו נכנס, ויש על כך טסט, והבדיקה החוצה-מימושים
    (פייתון מול ה-jq של הסוכן) נשארת: שני הכללים חייבים להישאר אחד.

    השאלה שהמספר הזה **אינו** עונה עליה: האם מערכת הקבצים בכלל מסוגלת
    להתכווץ בכזה שיעור. זו מדידה על מערכת קבצים אמיתית (`ntfsresize
    --info`, ‏`resize2fs -P`) ואינה נגזרת משום שדה במניפסט — ובוודאי לא
    מ-`used_bytes`, שהוא `0` בכל מניפסט שנקלט לפני #84 ודרישה שנשענת
    עליו הייתה יוצאת אפס (‏#298).
    """
    need = required_bytes(manifest)
    floor = _whole(floor_bytes)
    if need is None or floor is None:
        return None
    if need <= floor:
        return 0
    end = layout_end_bytes(manifest)
    if end is None:
        # יודעים שהוא אינו נכנס, לא יודעים איפה לכווץ. הקורא אומר זאת.
        return None
    return max(0, end + GPT_TAIL_BYTES - floor)
