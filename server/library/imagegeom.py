"""How many bytes an image needs (#82), and the whole-number reading it rests on.

Split out of `images.py` (#2531): `minfree.py` and `imagefit.py` need only this arithmetic, and
importing `images` for it closed an import cycle (images imports minfree inside its functions).
`images` re-exports every name, so `images.required_bytes` and the others are unchanged.
"""
from __future__ import annotations


#: מה שהכונן חייב להחזיק **אחרי** סוף הפריסה: עותק הגיבוי של ה-GPT.
#: הוא 33 סקטורים — 16,896 בייט בכונן 512, 135,168 בכונן 4Kn — ו-256KiB
#: נותן פי שניים מהגרוע שבהם. קודם לכן זה היה מגה-בייט **ועיגול כלפי
#: מעלה למגה**, כלומר עד 2MiB בפועל: כונן פיזי אינו כפולה של מגה
#: (256,060,514,304 mod 1MiB = 352,256), ולכן פריסה שנגמרה 1.3MB לפני
#: סוף היעד נפסלה למרות שהיא נכנסת — 1.32MB של פסילות שווא, נמדד ב-#1171
#: מול שני כוננים שהם "256GB" משני יצרנים. היישור של `sgdisk` הוא של
#: **המחיצות** ואינו דרישה על גודל הכונן.
GPT_TAIL_BYTES = 256 * 1024


def _whole(value: object) -> int | None:
    """מספר שלם אמיתי, או None. bool הוא int בפייתון ואינו גודל."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def layout_end_bytes(manifest: dict) -> int | None:
    """הבייט שאחרי המחיצה האחרונה בפריסה, או None אם הגיאומטריה חסרה.

    מספיק שלמחיצה אחת אין `start_sector` או `size_bytes` כדי שהפריסה
    כולה לא תהיה ידועה: "לא הצלחנו לחשב" אינו "יצא קטן" (עיקרון 5).
    """
    sector = _whole(manifest.get("sector_size")) or 512
    ends = []
    for part in manifest.get("partitions") or []:
        start = _whole(part.get("start_sector")) if isinstance(part, dict) else None
        size = _whole(part.get("size_bytes")) if isinstance(part, dict) else None
        if start is None or size is None:
            return None
        ends.append(start * sector + size)
    return max(ends) if ends else None


def required_bytes(manifest: dict) -> int | None:
    """כמה בייטים כונן יעד חייב להחזיק כדי שהאימג' ייכתב עליו.

    #82: הדרישה אינה גודל דיסק המקור אלא **סוף הפריסה** — המחיצה
    האחרונה שהמניפסט מצהיר עליה, ועוד `GPT_TAIL_BYTES`. דיסק מקור עם
    שטח לא מוקצה בזנב אינו חלק ממה שהאימג' צריך, וזה כל ההבדל בין
    אימג' זהב שנבנה במכונה וירטואלית לבין אימג' שאינו נכנס לשום ברזל:
    דיסק VM "256GB" הוא 256 GiB, שבעה אחוזים יותר מכל כונן פיזי
    מאותה מחלקה.

    **הרצפה של המחיצה המורחבת היא הגודל שבו נקלטה, לא כמה תפוס בה.**
    partclone מסרב לשחזר מערכת קבצים לתוך מחיצה קטנה ממנה ("Target
    partition size is smaller than source"), ועם `-C` הוא כותב עד
    "No space left on device" ומשאיר superblock פגום. לכן החישוב אינו
    נוגע ב-`used_bytes` כלל — וממילא אינו נשען על שדה שבכל מניפסט
    שנקלט לפני #84 הוא 0.

    `None` = לא ניתן לקבוע. הקורא חוסם, לא מוותר (עיקרון 5).
    """
    end = layout_end_bytes(manifest)
    if end is None:
        # מניפסט בלי גיאומטריה מלאה — הערך המוצהר, שהוא השמרני מבין השניים.
        return _whole(manifest.get("min_target_bytes"))
    need = end + GPT_TAIL_BYTES
    source = _whole(manifest.get("source_disk_bytes"))
    if source is not None and end <= source < need:
        # דיסק המקור החזיק את הפריסה הזו בפועל — ראיה חיובית שדי בו.
        need = source
    return need
