"""ספריית האימג'ים — הדיסק הוא מקור האמת.

אימג' = תיקייה עם manifest.json (ממשק 1) וקבצי המחיצות שלצדו. אין טבלת
אימג'ים ב-DB: מה שמונח בתיקייה קיים, מה שנמחק ממנה איננו. זה מה שהופך
גיבוי והעברה ל"להעתיק תיקייה".

בטיחות הגשה: קובץ מוגש רק אם הוא מופיע במניפסט בשמו המדויק — רשימה
לבנה, לא סניטציה של נתיבים.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from pathlib import Path

from .atomicfile import write_atomic

log = logging.getLogger("imagectl.images")

REQUIRED_FIELDS = ("id", "name", "family", "min_target_bytes", "partitions")
REQUIRED_PARTITION_FIELDS = ("index", "file", "sha256")

#: מזהה אימג' — בדיוק מה שהשרת מייצר בקליטה: `"img_" + secrets.token_hex(3)`
#: (‏`create_capture` ב-capture.py), כלומר שש ספרות הקסה קטנות.
#:
#: המזהה אינו מחרוזת תצוגה: הוא **שם התיקייה** שהאימג' נכנס אליה, ולכן
#: הוא הגבול בין "מזהה" לבין "נתיב". מניפסט מגיע גם מבחוץ (ייבוא ארכיון),
#: ומזהה כמו `../evil` בו הוא בקשה לכתוב אל מחוץ לספרייה (‏#110).
IMAGE_ID = re.compile(r"img_[0-9a-f]{6}")
GUID_RE = re.compile(r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}")
UUID_RE = re.compile(r"(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9A-F]{4}-[0-9A-F]{4}|[0-9A-F]{16})")
ATTRS_RE = re.compile(r"[0-9A-F]{16}")
PARTITION_ROLES = {"esp", "msr", "windows", "recovery", "linux", "swap", "data"}
# "" / "unknown" = מחיצה בלי מערכת קבצים (MSR, ‏BIOS boot) — הסוכן מזרים אותה ב-partclone.dd
# (#1419). ‏"unknown" הוא מה ש-_fs_of (manifest.sh) כותב כש-blkid יוצא ≠0 — וזה בדיוק מה
# ש-blkid עושה על מחיצה בלי חתימה.
FILESYSTEMS = {"ntfs", "vfat", "ext4", "btrfs", "swap", "", "unknown"}

MIB = 1 << 20
#: מה שהכונן חייב להחזיק **אחרי** סוף הפריסה: עותק הגיבוי של ה-GPT.
#: הוא 33 סקטורים — 16,896 בייט בכונן 512, ‏135,168 בכונן 4Kn — ו-256KiB
#: נותן פי שניים מהגרוע שבהם. קודם לכן זה היה מגה-בייט **ועיגול כלפי
#: מעלה למגה**, כלומר עד 2MiB בפועל: כונן פיזי אינו כפולה של מגה
#: (‏256,060,514,304 mod 1MiB = 352,256), ולכן פריסה שנגמרה 1.3MB לפני
#: סוף היעד נפסלה למרות שהיא נכנסת — 1.32MB של פסילות שווא, נמדד ב-#1171
#: מול שני כוננים שהם "256GB" משני יצרנים. היישור של `sgdisk` הוא של
#: **המחיצות** ואינו דרישה על גודל הכונן.
GPT_TAIL_BYTES = 256 * 1024
#: הסף בין המשפחות — אותו מספר בדיוק שהסוכן גוזר בו בקליטה (capture.sh).
FAMILY_SPLIT_BYTES = 322122547200


def valid_image_id(value: object) -> bool:
    """האם זה מזהה אימג' — ולא משהו שאפשר "לתקן" לכדי מזהה.

    התשובה היא כן/לא בלבד: אין כאן `strip`, אין `basename` ואין החלפת
    תווים. ניקוי שקט הופך קלט זדוני לקלט תקין, והקורא שנשען עליו כבר
    לא יודע מה בדיוק אישר (עיקרון 5 בכיוון ההפוך). מזהה שאינו תואם —
    נדחה בשמו, עם הודעה שאומרת מה נדחה.
    """
    return isinstance(value, str) and IMAGE_ID.fullmatch(value) is not None


def inside(path: Path, root: Path) -> Path | None:
    """הנתיב המלא אם הוא באמת בתוך `root`, ואחרת `None`.

    החגורה השנייה מתחת ל-`valid_image_id`, ובכוונה לא במקומה: היא בודקת
    את מה שיוצא — הנתיב אחרי הצירוף — ולא את מה שנכנס. ‏regex שמישהו
    ירחיב בעתיד, או שדה נוסף שמישהו יצרף לנתיב, לא ישברו את הגבול.

    ‏`resolve` פותח גם קישורים סימבוליים, ולכן "בתוך" כאן הוא היכן שהנתיב
    באמת נוחת. אם לא הצלחנו לקבוע — אין ראיה חיובית שהוא בפנים, והתשובה
    היא `None` ולא "כנראה בסדר".
    """
    try:
        resolved = path.resolve()
        base = root.resolve()
    except (OSError, ValueError) as exc:      # גם שם עם בייט אפס בתוכו
        log.warning("cannot resolve %s under %s: %s", path, root, exc)
        return None
    return resolved if resolved.is_relative_to(base) else None


def safe_partition_filename(value: object) -> bool:
    """True only for a non-special, single-component partition filename."""
    return (isinstance(value, str) and bool(value) and value not in (".", "..")
            and "/" not in value and "\\" not in value and "\0" not in value)


def _unsafe_agent_string(value: object) -> bool:
    if isinstance(value, str):
        return any(ord(ch) < 32 or ch == "|" for ch in value)
    if isinstance(value, dict):
        return any(_unsafe_agent_string(item) for item in value.values())
    if isinstance(value, list):
        return any(_unsafe_agent_string(item) for item in value)
    return False


def guarded_image_path(image_dir: str | Path, filename: object) -> Path | None:
    """Resolve one image file without following a symlink or leaving its directory."""
    if not safe_partition_filename(filename):
        return None
    root = Path(image_dir)
    candidate = root / filename
    try:
        if candidate.is_symlink():
            return None
    except OSError:
        return None
    resolved = inside(candidate, root)
    return resolved if resolved is not None and resolved.is_file() else None


def disk_family(size_bytes: int) -> int:
    """המשפחה של כונן בגודל נתון (אפיון סעיף 13): מתחת ל-300GiB — 256."""
    return 256 if size_bytes < FAMILY_SPLIT_BYTES else 500


def _whole(value: object) -> int | None:
    """מספר שלם אמיתי, או None. ‏bool הוא int בפייתון ואינו גודל."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def has_partition_geometry(part: object) -> bool:
    """Whether a partition has meaningful GPT geometry."""
    if not isinstance(part, dict):
        return False
    start = _whole(part.get("start_sector"))
    size = _whole(part.get("size_bytes"))
    # A real GPT data partition cannot start at LBA 0, and an empty
    # partition cannot be recreated or receive a filesystem image.
    return start is not None and start > 0 and size is not None and size > 0


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

    ‏#82: הדרישה אינה גודל דיסק המקור אלא **סוף הפריסה** — המחיצה
    האחרונה שהמניפסט מצהיר עליה, ועוד `GPT_TAIL_BYTES`. דיסק מקור עם
    שטח לא מוקצה בזנב אינו חלק ממה שהאימג' צריך, וזה כל ההבדל בין
    אימג' זהב שנבנה במכונה וירטואלית לבין אימג' שאינו נכנס לשום ברזל:
    דיסק VM ‏"256GB" הוא 256 GiB, שבעה אחוזים יותר מכל כונן פיזי
    מאותה מחלקה.

    **הרצפה של המחיצה המורחבת היא הגודל שבו נקלטה, לא כמה תפוס בה.**
    ‏partclone מסרב לשחזר מערכת קבצים לתוך מחיצה קטנה ממנה ("Target
    partition size is smaller than source"), ועם `-C` הוא כותב עד
    "No space left on device" ומשאיר superblock פגום. לכן החישוב אינו
    נוגע ב-`used_bytes` כלל — וממילא אינו נשען על שדה שבכל מניפסט
    שנקלט לפני #84 הוא 0.

    ‏`None` = לא ניתן לקבוע. הקורא חוסם, לא מוותר (עיקרון 5).
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


def used_bytes_total(manifest: dict) -> int | None:
    """כמה תפוס בכל מערכות הקבצים של האימג' יחד — מה שהקונסולה מציגה
    כ"בשימוש" (‏#953).

    ‏`None` = לפחות מחיצה אחת **לא נמדדה** (‏`used_bytes: null`, סעיף 1
    בממשקים — "would not mount", משפחת #84). סכום חלקי היה מספר שנראה
    אמיתי ואינו: אימג' שמחיצת ה-Windows שלו לא נמדדה היה מוצג "בשימוש
    30 MB" — ה-ESP בלבד — ואיש לא היה רואה שהמדידה חסרה (עיקרון 5).
    לכן חסר אחד = "לא ידוע" כולו, לא 0 ולא סכום של מה שכן נמדד.
    """
    total = 0
    for part in manifest.get("partitions") or []:
        used = _whole(part.get("used_bytes")) if isinstance(part, dict) else None
        if used is None:
            return None
        total += used
    return total


#: ה-MAC שהאימג' קשור אליו — ‏#381, הצד הכותב של #69. אימג' שנקלט
#: ממחשב כיתה שייך למכונה שנקלט ממנה, ומשוחזר **רק אליה**: "אני רוצה
#: שאותו מחשב יקבל בשיעור הבא את הדיסק שלו".
#:
#: השדה יושב במניפסט ולא רק ב-DB (עיקרון 3 — הדיסק הוא מקור האמת):
#: תיקייה שהועתקה לשרת אחר, לגיבוי או לארכיון נושאת את הקשירה איתה.
#: אימג' בלי השדה הוא אימג' חופשי, וזו כל הספרייה שנקלטה עד #381.
MACHINE_MAC = "machine_mac"


def bound_mac(manifest: dict) -> str | None:
    """ה-MAC שהאימג' הזה שייך לו, או `None` לאימג' חופשי.

    מחרוזת ריקה, `null`, מספר או רשימה — כולם "לא קשור", ולא "קשור
    למשהו שלא הבנו": מניפסט מגיע גם מייבוא ארכיון, ושדה פגום בו אינו
    מזהה מכונה. השער עצמו מכריע לפי מה שיוצא מכאן.
    """
    value = manifest.get(MACHINE_MAC)
    return value if isinstance(value, str) and value else None


def restore_refusal(manifest: dict, macs: list[str] | None) -> str | None:
    """ההודעה שמסרבת לשחזר אימג' קשור למכונה שאינה שלו, או `None`.

    ‏`macs` — היעדים שהשחזור הזה יגיע אליהם: ‏`[mac]` למשיכת יוניקאסט,
    בחירת המחשבים לסבב כיתה, ו-`None` כשהיעד אינו רשימה ידועה — כיתה
    שלמה בלי בחירה, או מגירות בחדר השיכפולים.

    ‏`None` הוא **סירוב** לאימג' קשור, לא היתר: "לא ידענו לאן זה הולך"
    אינו "בדקנו והכל תקין" (עיקרון 5). ההיתר דורש ראיה חיובית —
    רשימת יעדים שאין בה אף אחד מלבד הבעלים.

    הסירוב **נוקב בשמות** ולא אומר "נדחה": מפעיל שרואה "האימג' הזה
    שייך למכונה אחרת" בלי לדעת לאיזו, ובלי לדעת מה ביקש, אינו יודע מה
    לעשות — בדיוק הכשל של #106.
    """
    owner = bound_mac(manifest)
    if owner is None:
        return None
    if macs and set(macs) == {owner}:
        return None
    asked = ", ".join(sorted(macs)) if macs else "כל הקבוצה"
    return (f"האימג' הזה שייך למכונה {owner} ומשוחזר רק אליה — "
            f"היעד שנבחר הוא {asked}")


def lineage_of(manifest: dict) -> str:
    """השושלת של האימג' — מזהה הגרסה הראשונה (‏#1334). גרסה שנשמרה מ-VM
    נושאת `lineage`; אימג' שלא נגזר מאף אחד הוא השושלת של עצמו."""
    value = manifest.get("lineage")
    return value if isinstance(value, str) and valid_image_id(value) else manifest["id"]


def version_of(manifest: dict) -> int:
    """`version` — מספר שלם מ-1; חסר או פגום = 1 (כל מה שנקלט לפני #1334)."""
    value = manifest.get("version")
    return value if type(value) is int and value >= 1 else 1


def default_versions(images: dict[str, dict], pointers: dict[str, str]) -> dict[str, str]:
    """{שושלת: מזהה ברירת המחדל}. ההצבעה שנקבעה בקונסולה גוברת — אם היא
    מצביעה על גרסה שקיימת **באותה שושלת**; אחרת הגרסה הגבוהה ביותר (ובשוויון
    — החדשה ביותר לפי `created`). כך שמירה מ-VM הופכת את הגרסה החדשה לברירת
    המחדל, ו"קבע כברירת מחדל" מחזיר אחורה בלי למחוק דבר (הכרעה 2)."""
    by_lineage: dict[str, list[dict]] = {}
    for manifest in images.values():
        by_lineage.setdefault(lineage_of(manifest), []).append(manifest)
    result = {}
    for lineage, members in by_lineage.items():
        ids = {m["id"] for m in members}
        chosen = pointers.get(lineage)
        if chosen not in ids:
            chosen = max(members, key=lambda m: (version_of(m), str(m.get("created", ""))))["id"]
        result[lineage] = chosen
    return result


def streamed_partitions(manifest: dict) -> list[dict]:
    """המחיצות שיש להן קובץ בזרם. swap מתועד במניפסט אבל לא נשמר ולא
    משודר (אפיון סעיף 14) — הסוכן יוצר אותו מחדש; `file` שלו הוא null."""
    return [p for p in manifest["partitions"] if p.get("file")]


def carries_a_stream_file(part: dict) -> bool:
    """האם למחיצה הזו יש קובץ בזרם — ולא "משהו שאפשר לקרוא לו קובץ".

    שלוש הצורות שאינן קובץ נבדקות יחד בכוונה: `null`, מחרוזת ריקה,
    והמחרוזת `"null"`. השלישית אינה תיאורטית — הסוכן קורא את המניפסט
    ב-`jq -r`, שמרנדר JSON null **כמחרוזת** `null`, ולכן שם אי אפשר
    להבחין בין השתיים. מחיצה שבאמת נקראת `null` תיפסל כאן, וזה הכיוון
    הנכון של השגיאה: להיכשל בקול על שם מוזר, ולא לתת לו להתפרש כ-swap
    ולהגיע ל-`mkswap` (#424).
    """
    name = part.get("file")
    return isinstance(name, str) and name.strip() not in ("", "null")


def has_sha256(part: dict) -> bool:
    """Whether a streamed partition declares a canonical SHA-256 digest."""
    value = part.get("sha256")
    return (
        isinstance(value, str)
        and re.fullmatch(r"^[0-9a-f]{64}$", value) is not None
    )


def image_os(manifest: dict) -> str:
    """`os` מהמניפסט, ולאימג'ים שנקלטו לפני שהשדה נוסף — מתפקידי המחיצות."""
    declared = manifest.get("os")
    if declared in ("windows", "linux"):
        return declared
    roles = {p.get("role") for p in manifest["partitions"]}
    if "windows" in roles:
        return "windows"
    if "linux" in roles:
        return "linux"
    return "unknown"


def has_disk_guid(manifest: dict) -> bool:
    """האם למניפסט יש `disk_guid` אמיתי — ולא חוסר שמתחזה לערך.

    ארבע הצורות שאינן GUID נבדקות יחד, כמו ב-`carries_a_stream_file`:
    מפתח חסר או `None`, מחרוזת ריקה, המחרוזת `"null"`, וה-GUID
    האפס-מוחלט (nil UUID, `00000000-0000-...`). הסוכן גוזר את
    ה-GUID מ-`sgdisk -p` בקליטה (`capture.sh`), וכשהקריאה **נכשלת**
    השדה יוצא מחרוזת ריקה — לא `null`. ‏`disk_guid` חסר אינו "אין צורך
    ב-GUID" אלא "לא ידענו מה ה-GUID", ושני המצבים שונים (עיקרון 5).

    האפס-המוחלט הוא ערך שנקרא כזבל — GPT פגום, דיסק שאופס עם חתימה
    ששרדה, או כלי partition לא-תקני. הוא "לא הצלחנו לקרוא GUID אמיתי",
    לא זהות, ולכן נכשל-סגור בדיוק כמו ריק ואינו מתחזה לערך.
    """
    value = manifest.get("disk_guid")
    if not isinstance(value, str):
        return False
    v = value.strip()
    if v in ("", "null"):
        return False
    # פורמט GUID תקין בלבד — מחרוזת לא-GUID כמו "unknown" אינה זהות, ותיפול
    # ב-sgdisk -U מול הכיתה. מרחיב את #443/#760 (פורמט, לא רק ריק/אפס-מוחלט).
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", v):
        return False
    return set(v.replace("-", "")) - {"0"} != set()


#: מה ששם תיקייה או שם אימג' יכולים להיות. **‏ASCII בלבד, ובכוונה.**
#:
#: מסך התחנה הוא קונסולת טקסט של לינוקס, ושם עברית אינה ניתנת להצגה
#: משתי סיבות בלתי תלויות: ב-initrd אין `setfont` ואין קובץ פונט,
#: **ולקונסולת לינוקס אין תמיכת RTL בכלל**. היפוך המחרוזת מראש עובד על
#: עברית טהורה ונשבר על מעורב — וכל שמות האימג'ים במעבדה מעורבים.
#:
#: והאכיפה כאן ולא במסך, כי הקונסולה היא דפדפן ומציגה עברית מצוין:
#: בלי בדיקה השם ייכתב **בהצלחה**, והכשל יתגלה חודשים אחר כך מול מכונה
#: בכיתה כמסך של ריבועים. זה בדיוק #111 — שם משתמש בעברית עבר את
#: היצירה ואת בדיקת הסיסמה, והפיל את `set_cookie` ב-500 על הסיסמה
#: **הנכונה**: הכישלון היה ניתן לאבחון וההצלחה לא.
#:
#: ‏48 תווים: שורת התפריט בתחנה היא ``N) <שם>  [<משפחה> GB family]``
#: על קונסולה של 80 עמודות.
#: מתחיל **וגם מסתיים** באות או בספרה. רווח בסוף בלתי נראה על המסך,
#: ו-`nicdesc:eth0 ` מ-#130 הוא בדיוק מה שהוא עושה: מפתח אחר לגמרי
#: מזה שהמפעיל התכוון לו, בלי שום סימן.
DISPLAY_NAME_RE = re.compile(r"[A-Za-z0-9א-ת]([A-Za-z0-9א-ת ._-]{0,46}[A-Za-z0-9א-ת])?")
#: הכלל במילים — לחריגה, ול-API שמחזיר אותו בלי טקסט של חריגה (‏#1535).
DISPLAY_NAME_RULE = ("מותרים אותיות אנגליות או עבריות, ספרות, רווח, נקודה, מקף וקו תחתון; "
                     "מתחיל באות או בספרה, עד 48 תווים")


def validate_display_name(value: str, what: str = "השם") -> str:
    """שם תקין לתצוגה, או ValueError בעברית.

    **נדחה בשמו ואינו מנוקה לצורה תקינה** — אותה הכרעה של #110 ושל #102:
    ניקוי שקט הופך "מה שהקלדתי" ל"מה שנשמר" בלי שאיש יידע.

    האכיפה היא **ביצירה בלבד**, כמו ב-`users._check_username`: שם קיים
    לא ייחסם רטרואקטיבית על ידי כלל שנוסף אחריו.
    """
    # **בלי `.strip()`.** רווח בתחילת השם או בסופו הוא בדיוק המקרה
    # ש-"נדחה ואינו מנוקה" נכתב בשבילו: ניקוי שקט הופך "מה שהקלדתי"
    # ל"מה שנשמר" בלי שאיש יידע, ו-`nicdesc:eth0 ` מ-#130 הוא מה שקורה
    # כשהרווח כן נשמר במקום אחד ולא באחר.
    text = value or ""
    if not DISPLAY_NAME_RE.fullmatch(text):
        raise ValueError(f"{what} {text!r} אינו תקין — {DISPLAY_NAME_RULE}")
    return text


def _replace_json(path: Path, data: dict) -> None:
    """כתיבה אטומית של manifest.json: קובץ זמני באותה תיקייה, ואז replace."""
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


class ImageLibrary:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        # #1066: מיקומים נוספים (NFS/SMB/iSCSI). None = רק `--images`,
        # כמו לפני המיקומים — בדיקות יחידה של הספרייה עצמה.
        self._locations = None
        # ‏#1334: הצבעות "ברירת המחדל" לכל שושלת (טבלת settings). None = בלי
        # הצבעות — הגרסה הגבוהה ביותר.
        self._default_pointers = None
        self._invalid_reporter = None
        self._reported_invalid: set[tuple[str, str]] = set()
        # ‏#1494: שני כותבים ל-manifest.json — עריכת שדות תצוגה והשלמת
        # `used_bytes` מכותרת partclone. קריאה-שינוי-כתיבה של אחד בלי השני
        # הייתה מוחקת את מה שהשני כתב (שינוי שם של המפעיל, למשל).
        self._write_lock = threading.Lock()

    def bind_invalid_reporter(self, reporter) -> None:
        """Report each rejected on-disk manifest once to the operator journal."""
        self._invalid_reporter = reporter

    def _report_invalid(self, path: Path, reason: str) -> None:
        key = (str(path), reason)
        if self._invalid_reporter is not None and key not in self._reported_invalid:
            self._reported_invalid.add(key)
            self._invalid_reporter(path, reason)

    def bind_defaults(self, provider) -> None:
        """מזרים את הצבעות ברירת המחדל מה-DB (‏`image_versions.pointers`)."""
        self._default_pointers = provider

    def defaults(self, images: dict[str, dict] | None = None) -> dict[str, str]:
        found = images if images is not None else self.scan()
        pointers = self._default_pointers() if self._default_pointers else {}
        return default_versions(found, pointers or {})

    def bind_locations(self, provider) -> None:
        """מזרים את רשימת המיקומים מה-DB. נקרא פעם אחת ב-create_runtime."""
        self._locations = provider

    #: תיקיות העבודה של השרת בתוך שורש הספרייה — קליטה (`.capture-<task>`)
    #: וייבוא (`.import-<token>`). מה שמונח בהן עדיין לא אומת, ולכן הן
    #: אינן חלק מהספרייה.
    WORK_AREA_PREFIX = "."

    def _in_work_area(self, path: Path, root: Path | None = None) -> bool:
        base = root if root is not None else self.root
        try:
            rel = path.relative_to(base)
        except ValueError:
            return False
        return any(part.startswith(self.WORK_AREA_PREFIX) for part in rel.parts[:-1])

    def _scan_root(self, root: Path, loc_id: str, loc_meta: dict) -> dict[str, dict]:
        """מניפסטים תקינים תחת שורש אחד. פגום מדולג, כפילות — אף אחד."""
        found: dict[str, dict] = {}
        ambiguous: set[str] = set()
        if not root.is_dir():
            return found
        for path in sorted(root.rglob("manifest.json")):
            if self._in_work_area(path, root):
                continue
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("skipping unreadable manifest %s: %s", path, exc)
                self._report_invalid(path, f"unreadable JSON: {exc}")
                continue
            problem = self._validate(manifest)
            if problem:
                log.warning("skipping manifest %s: %s", path, problem)
                self._report_invalid(path, problem)
                continue
            image_id = manifest["id"]
            if image_id in found or image_id in ambiguous:
                log.warning("duplicate image id %s at %s -- serving neither", image_id, path)
                ambiguous.add(image_id)
                found.pop(image_id, None)
                continue
            manifest["_dir"] = str(path.parent)
            manifest["_location_id"] = loc_id
            manifest["_location_name"] = loc_meta.get("name") or loc_id
            manifest["_available"] = True
            manifest["_state_since"] = loc_meta.get("state_since") or ""
            found[image_id] = manifest
        return found

    def scan(self) -> dict[str, dict]:
        """כל המניפסטים התקינים, לפי id. פגום — מדולג עם אזהרה, לא מפיל.

        שדה לא ידוע במניפסט — מתעלמים, לפי המוסכמות הרוחביות.

        שני מצבים מדולגים בשלמותם, ומאותו טעם — אימג' שאיננו יודעים מה
        הוא לא יוצע לכיתה (עיקרון 5): מניפסט שיושב באזור עבודה, ומזהה
        שיותר מתיקייה אחת מצהירה עליו.

        #1066: מיקום `unreachable`/`disconnected` — האימג'ים האחרונים
        שנראו עליו נשארים ברשימה עם `_available=False` ("לא זמין", לא
        "חסר"). הדיסק הוא מקור האמת כשהוא נגיש; המטמון הוא מה שאין לו
        ייצוג טבעי כשהוא לא.
        """
        found: dict[str, dict] = {}
        ambiguous: set[str] = set()
        roots = self._location_roots()
        for loc in roots:
            if loc["available"]:
                chunk = self._scan_root(Path(loc["path"]), loc["id"], loc)
            else:
                chunk = {}
                for cached in loc.get("cached") or []:
                    if not isinstance(cached, dict) or "id" not in cached:
                        continue
                    item = dict(cached)
                    item["_location_id"] = loc["id"]
                    item["_location_name"] = loc.get("name") or loc["id"]
                    item["_available"] = False
                    item["_state_since"] = loc.get("state_since") or ""
                    item["_dir"] = None
                    chunk[item["id"]] = item
            for image_id, manifest in chunk.items():
                if image_id in found or image_id in ambiguous:
                    log.warning("duplicate image id %s -- serving neither", image_id)
                    ambiguous.add(image_id)
                    found.pop(image_id, None)
                    continue
                found[image_id] = manifest
        return found

    def _location_roots(self) -> list[dict]:
        if self._locations is None:
            return [{"id": "loc_local", "name": "מקומי", "path": self.root,
                     "available": True, "cached": [], "state_since": ""}]
        return list(self._locations() or [])

    @staticmethod
    def _validate(manifest: object, *, require_library_fields: bool = True) -> str | None:
        if not isinstance(manifest, dict):
            return "not an object"
        if manifest.get("schema") != 1:
            return f"unknown schema: {manifest.get('schema')!r}"
        required = REQUIRED_FIELDS if require_library_fields else ("family", "min_target_bytes", "partitions")
        for field in required:
            if field not in manifest:
                return f"missing field: {field}"
        if _unsafe_agent_string(manifest):
            return "a string field contains a control or agent delimiter character"
        if require_library_fields and (not valid_image_id(manifest["id"])
                                       or not isinstance(manifest["name"], str)
                                       or not manifest["name"]):
            return "image id or name is malformed"
        # שני השדות שההחלטה "האם האימג' נכנס לכונן" נשענת עליהם. מניפסט
        # שהם פגומים בו נדחה כאן, ולא מגיע להשוואה שתשווה מחרוזת למספר.
        if manifest["family"] not in (256, 500):
            return f"family must be 256 or 500, not {manifest['family']!r}"
        if not (_whole(manifest["min_target_bytes"]) or 0) > 0:
            return "min_target_bytes must be a whole number of bytes"
        for field in ("source_disk_bytes", "total_compressed_bytes"):
            if field in manifest and _whole(manifest[field]) is None:
                return f"{field} must be a whole number of bytes"
        if manifest.get("scheme", "gpt") != "gpt" or manifest.get("sector_size", 512) != 512:
            return "only a GPT manifest with 512-byte sectors is supported"
        if not isinstance(manifest["partitions"], list) or not manifest["partitions"]:
            return "partitions must be a non-empty list"
        for field in ("disk_guid",):
            value = manifest.get(field)
            if value is not None and (not isinstance(value, str) or GUID_RE.fullmatch(value) is None):
                return f"{field} must be a canonical GUID"
        indexes: set[int] = set()
        geometry: list[tuple[int, int, int]] = []
        for part in manifest["partitions"]:
            for field in REQUIRED_PARTITION_FIELDS:
                if not isinstance(part, dict) or field not in part:
                    return f"partition missing field: {field}"
            index = _whole(part["index"])
            if index is None or not 1 <= index <= 128 or index in indexes:
                return f"partition index must be unique and between 1 and 128: {part['index']!r}"
            indexes.add(index)
            if part.get("role") not in PARTITION_ROLES or part.get("fs") not in FILESYSTEMS:
                return f"partition {index} has an unsupported role or filesystem"
            for field in ("type_guid", "unique_guid"):
                value = part.get(field)
                if field == "type_guid" or value not in (None, ""):
                    if not isinstance(value, str) or GUID_RE.fullmatch(value) is None:
                        return f"partition {index} has malformed {field}"
            uuid = part.get("uuid")
            if uuid not in (None, "") and (not isinstance(uuid, str) or UUID_RE.fullmatch(uuid) is None):
                return f"partition {index} has malformed uuid"
            attrs = part.get("attrs")
            if attrs not in (None, "") and (not isinstance(attrs, str) or ATTRS_RE.fullmatch(attrs) is None):
                return f"partition {index} has malformed attrs"
            used = part.get("used_bytes")
            size = _whole(part.get("size_bytes"))
            if used is not None and (_whole(used) is None or size is None or used > size):
                return f"partition {index} has malformed used_bytes"
            if not isinstance(part.get("expandable", False), bool):
                return f"partition {index} has malformed expandable flag"
            if not isinstance(part.get("system", False), bool):   # #1875: "is the system" decides min-free
                return f"partition {index} has malformed system flag"
            for field in ("role", "fs", "file", "sha256", "unique_guid", "uuid", "attrs"):
                value = part.get(field)
                if isinstance(value, str) and any(ord(ch) < 32 or ch in "|" for ch in value):
                    return f"partition {index} field {field} contains a control or delimiter character"
            if carries_a_stream_file(part) and not has_sha256(part):
                return f"partition {part['index']} has malformed sha256"
            if carries_a_stream_file(part) and not safe_partition_filename(part.get("file")):
                return f"partition {part['index']} has an unsafe file name"
            if not has_partition_geometry(part):
                return (f"partition {part.get('index')!r} has malformed geometry:"
                        " start_sector and size_bytes must both be positive")
            start = part["start_sector"] * 512
            end = start + part["size_bytes"]
            if part["size_bytes"] % 512 or end > manifest["min_target_bytes"]:
                return f"partition {index} geometry is outside the target disk or not sector-aligned"
            geometry.append((start, end, index))
            # מי שפטור מקובץ הוא **`fs == "swap"`**, ולא `role == "swap"`:
            # הסוכן מחליט לפי `fs` בלבד, ורשומה ששני השדות אינם מסכימים
            # בה עברה כאן בשלמותה והגיעה שם ל-`mkswap` (#424). מחיצה
            # שאינה swap ובלי קובץ היא מניפסט פגום, ונפסלת בשמה —
            # עיקרון 6: פגום נתפס כאן, לא מול כיתה.
            if not carries_a_stream_file(part) and part.get("fs") != "swap":
                return (f"partition {part['index']} ({part.get('fs')}) has no file,"
                        " and only a swap partition may have none")
        if not streamed_partitions(manifest):
            return "no partition carries data"
        ordered = sorted(geometry)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            return "partition geometry overlaps"
        return None

    def get(self, image_id: str) -> dict | None:
        return self.scan().get(image_id)

    def file_path(self, image_id: str, filename: str) -> Path | None:
        """הנתיב לקובץ מחיצה — רק אם המניפסט מכריז עליו בדיוק בשם הזה."""
        manifest = self.get(image_id)
        if manifest is None:
            return None
        if not manifest.get("_dir") or not manifest.get("_available", True):
            return None
        declared = {part["file"] for part in streamed_partitions(manifest)}
        if filename not in declared:
            return None
        return guarded_image_path(manifest["_dir"], filename)

    def unmarked_images(self, min_free_gb: int) -> list[dict]:
        """‏#1896: האימג'ים שמסך מחשב הבנייה ומסך המשכפל מציגים "לא נבדק" עם
        סימון C: — ברירת המחדל של כל שושלת, זמינים, שאפשר לסמן בהם ועוד לא
        סומנו. רק כשהכלל דלוק (`min_free_gb` > 0): כבוי — הם כבר מוצעים."""
        from .minfree import candidates_view, unmarked   # minfree ← images
        if min_free_gb <= 0:
            return []
        scanned = self.scan()
        chosen = set(self.defaults(scanned).values())
        return sorted(({"id": iid, "name": m["name"], "folder": m.get("folder", ""),
                        "candidates": candidates_view(m)}
                       for iid, m in scanned.items()
                       if iid in chosen and m.get("_available", True) and unmarked(m)),
                      key=lambda item: item["id"])

    def allowed_for_disks(self, disks: list[dict] | None, min_free_gb: int = 0) -> list[str]:
        """מזהי האימג'ים שמתאימים לדיסקים שדווחו ב-hello.

        **הכרעה אחת, לפי הגודל בלבד** (‏#1863, הכרעת נדב 20/10): אימג' מוצע
        כשהוא נכנס לדיסק הפנימי הגדול ביותר — ‏`required_bytes` (‏#82) ועוד
        ‏`min_free_gb` פנויים במחיצת המערכת (‏#1354, ‏`min_disk_bytes`). זו
        אותה הכרעה ש-`room.fit_refusal` עושה מול המגירה שנבחרה: אימג' שנכנס
        לחלק מהמגירות מוצע, והמגירה הקטנה מדי נדחית בפתיחת הסבב.

        ‏`family` (256/500) **אינה מסננת יותר** — היא תווית תצוגה. מאז הכיווץ
        (‏#1171) אימג' "500" נכנס לדיסק 256, והמשפחה הסתירה אותו ממסך
        המשכפל וממחשב הבנייה בזמן שהקונסולה אישרה אותו.

        דרישה שאינה ניתנת לקביעה (‏`None`) — לא מוצע (עיקרון 5). הסוכן רק
        מציג, הוא לא מסנן בעצמו (כלל ממשק 3).
        """
        # ייבוא מקומי: ‏minfree ← imagefit ← images. במעלה הקובץ זה מעגל.
        from .minfree import min_disk_bytes
        scanned = self.scan()
        # ‏#1334: למכונה מוצעת **ברירת המחדל** של כל שושלת בלבד — סבב חדש
        # מקבל את הגרסה העדכנית (או זו שנקבעה); סבב פתוח ממשיך עם המזהה
        # שלו, כי הוא נשמר בסבב ולא נבחר מכאן.
        chosen = set(self.defaults(scanned).values())
        images = {iid: m for iid, m in scanned.items()
                  if m.get("_available", True) and iid in chosen}
        if not disks:
            return sorted(images)
        internal = [
            d.get("size_bytes", 0)
            for d in disks
            if isinstance(d, dict) and not d.get("removable", False)
        ]
        if not internal:
            return []
        biggest = max(internal)
        allowed = []
        for image_id, manifest in images.items():
            need = min_disk_bytes(manifest, min_free_gb)
            if need is None:
                # לא ידענו לחשב כמה הוא צריך — לא מציעים אותו (עיקרון 5).
                log.warning("skipping %s: its size requirement is unknown", image_id)
                continue
            if need <= biggest:
                allowed.append(image_id)
        return sorted(allowed)

    def public_list(self, min_free_gb: int = 0) -> list[dict]:
        """מה שהקונסולה מציגה — בלי נתיבי דיסק פנימיים.

        הסדר בתוך תיקייה: קודם `sort` (שהקונסולה עורכת), ואז שם.
        ‏`min_free_gb` (‏#1354) — ההגדרה, כדי ש"נכנס לדיסק מ-X" יכלול אותה.
        """
        # ייבוא מקומי: ‏minfree ← imagefit ← images. במעלה הקובץ זה מעגל.
        from .minfree import candidates_view, markable, min_disk_bytes, unchecked, unmarked
        result = []
        scanned = self.scan()
        chosen = set(self.defaults(scanned).values())
        for manifest in scanned.values():
            result.append(
                {
                    "id": manifest["id"],
                    "name": manifest["name"],
                    "description": manifest.get("description", ""),
                    "folder": manifest.get("folder", ""),
                    "sort": manifest.get("sort", 10**9),
                    "family": manifest["family"],
                    "os": image_os(manifest),
                    "created": manifest.get("created", ""),
                    "source_disk_bytes": manifest.get("source_disk_bytes"),
                    # ‏#953: "נכנס לדיסק מ-X" — הדרישה **הנגזרת** (`required_bytes`,
                    # מה שבדיקה 2.7 אוכפת), לא השדה הגולמי; ‏None = לא ניתן לקבוע.
                    # ‏#1354: כולל המינימום הפנוי במחיצת המערכת; ‏None גם כשה-
                    # `used_bytes` שלה לא נמדד. ‏`layout_bytes` — הפריסה לבדה.
                    "min_target_bytes": min_disk_bytes(manifest, min_free_gb),
                    "layout_bytes": required_bytes(manifest),
                    "min_free_gb": min_free_gb,
                    # ‏#1494: הכלל אינו ניתן לבדיקה (‏`used_bytes` של מחיצת
                    # המערכת לא נמדד) — הסוכן יסרב בכל מגירה. מחרוזת הסוכן, או None.
                    "min_free_unchecked": unchecked(manifest, min_free_gb),
                    # ‏#1896: המחיצות שאפשר לסמן ביניהן את C: (‏[] = אין בין מה
                    # לבחור, או שהקליטה סימנה), ו"לא נבדק" עד שמסמנים.
                    "system_candidates": candidates_view(manifest) if markable(manifest) else [],
                    "system_unmarked": unmarked(manifest),
                    "used_bytes": used_bytes_total(manifest),
                    "total_compressed_bytes": manifest.get("total_compressed_bytes", 0),
                    "partitions": len(manifest.get("partitions") or []),
                    # ‏#1012: מסך התחנה צריך להציג את בחירת ההרחבה, אבל
                    # הקיוסק אינו חושף את manifest endpoint בוילן. רק השדות
                    # הדרושים לתצוגה נוסעים בתשובת הקונסולה המאומתת.
                    "expand_partitions": [
                        {key: part.get(key) for key in
                         ("index", "role", "size_bytes", "expandable")}
                        for part in (manifest.get("partitions") or [])
                        if isinstance(part, dict) and part.get("role") in ("windows", "linux")
                    ],
                    # #1066: באיזה מיקום האימג' יושב, והאם המיקום נגיש.
                    "location_id": manifest.get("_location_id") or "loc_local",
                    "available": manifest.get("_available", True),
                    # ‏#1334: גרסאות — השושלת, מספר הגרסה, והאם היא ברירת המחדל.
                    "lineage": lineage_of(manifest),
                    "version": version_of(manifest),
                    "is_default": manifest["id"] in chosen,
                }
            )
        return sorted(result, key=lambda m: (m["folder"], m["sort"], m["name"]))

    EDITABLE_FIELDS = ("name", "description", "folder", "sort")

    def write_meta(self, image_id: str, changes: dict) -> bool:
        """עריכת שדות תצוגה במניפסט — שם, תיאור, תיקייה, סדר.

        הדיסק הוא מקור האמת, ולכן העריכה נכתבת ל-manifest.json עצמו,
        אטומית (קובץ זמני + replace). שדות שאינם ברשימה — נדחים.
        """
        manifest = self.get(image_id)
        if manifest is None:
            return False
        if not manifest.get("_dir") or not manifest.get("_available", True):
            raise ValueError("האימג' לא זמין — המיקום אינו נגיש")
        path = Path(manifest["_dir"]) / "manifest.json"
        with self._write_lock:
            return self._write_meta_locked(path, changes)

    def _write_meta_locked(self, path: Path, changes: dict) -> bool:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if key not in self.EDITABLE_FIELDS:
                raise ValueError(f"השדה {key} אינו ניתן לעריכה")
            # ‏`folder` ריק לגיטימי — אימג' שאינו בתיקייה. ‏`name` ריק
            # אינו: אימג' בלי שם אינו ניתן לבחירה במסך התחנה, והוא
            # היה עובר כאן בשקט כי התנאי הקודם דילג על ערך ריק.
            if key == "name" or (key == "folder" and value not in (None, "")):
                # ‏#138: כאן עוברים גם `PUT /images/{id}` וגם שינוי שם
                # תיקייה, ששניהם כותבים דרך write_meta.
                value = validate_display_name(
                    str(value), "שם האימג'" if key == "name" else "שם התיקייה")
            raw[key] = value
        _replace_json(path, raw)
        return True

    #: ‏#1494: המקור של `used_bytes` שלא נמדד בקליטה אלא נגזר אחריה.
    USED_BYTES_SOURCES = ("partclone_header",)

    def record_used_bytes(self, image_id: str, values: dict[int, int], source: str) -> list[int]:
        """משלים `used_bytes` למחיצות שלא נמדדו בקליטה (‏#1494) — ורק להן.

        ‏`values` = ‏{index: בייטים}. נכתב ל-manifest.json עצמו (עיקרון 3: הדיסק
        הוא מקור האמת, והסוכן קורא את `used_bytes` מהמניפסט שהשרת מגיש),
        אטומית, ולצד כל ערך `used_bytes_source` — ערך נגזר אינו מתחזה למדידה.
        מחיצה שכבר יש לה `used_bytes` אינה נדרסת. מניפסט שהשינוי היה הופך
        לפסול בעיני `_validate` (למשל `used_bytes` גדול מהמחיצה) — אינו נכתב
        כלל, כי אימג' פסול נעלם מהספרייה. מחזיר את האינדקסים שנכתבו.
        """
        if source not in self.USED_BYTES_SOURCES:
            raise ValueError(f"unknown used_bytes source: {source}")
        manifest = self.get(image_id)
        if manifest is None or not manifest.get("_dir") or not manifest.get("_available", True):
            return []
        path = Path(manifest["_dir"]) / "manifest.json"
        with self._write_lock:
            raw = json.loads(path.read_text(encoding="utf-8"))
            written = []
            for part in raw.get("partitions") or []:
                if not isinstance(part, dict) or part.get("used_bytes") is not None:
                    continue
                index = _whole(part.get("index"))
                value = values.get(index) if index is not None else None
                if _whole(value) is None:
                    continue
                part["used_bytes"] = value
                part["used_bytes_source"] = source
                written.append(index)
            if not written:
                return []
            problem = self._validate(raw)
            if problem:
                log.warning("not recording used_bytes for %s: %s", image_id, problem)
                return []
            _replace_json(path, raw)
            return sorted(written)

    def record_system_partition(self, image_id: str, index: object, *,
                                only_unmarked: bool = False) -> str | None:
        """‏#1896 (הכרעת נדב 22/10): מסמן איזו מחיצה היא C: — `"system": true` ועמו
        `"system_source": "operator"` — באימג' ישן שהקליטה לא סימנה בו, ושתי
        מחיצות ה-windows שלו (או ה-linux, ‏#1899) אינן ניתנות להבחנה. נכתב
        ל-manifest.json עצמו (עיקרון 3: הסוכן והשרת קוראים אותו סימון), **אטומית
        ועמיד** (`write_atomic`, ‏#1908). ‏`only_unmarked` — המשכפל: רק אימג' שעוד
        לא סומן; שינוי סימון — מהקונסולה. מחזיר `None`, או את הסיבה שלא נכתב."""
        from .minfree import OPERATOR, markable, system_candidates  # minfree ← images
        if isinstance(index, bool) or not isinstance(index, int):
            return "לא נבחרה מחיצה"
        manifest = self.get(image_id)
        if manifest is None or not manifest.get("_dir") or not manifest.get("_available", True):
            return "האימג' לא נמצא בספרייה, או שהמיקום שלו אינו נגיש"
        path = Path(manifest["_dir"]) / "manifest.json"
        with self._write_lock:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not markable(raw):
                return "באימג' הזה אין מחיצות לבחור ביניהן, או שהקליטה כבר סימנה את מחיצת המערכת"
            parts = [p for p in raw.get("partitions") or [] if isinstance(p, dict)]
            if only_unmarked and any(p.get("system") is True for p in parts):
                return "מחיצת המערכת באימג' הזה כבר סומנה — שינוי מהקונסולה"
            allowed = [_whole(p.get("index")) for p in system_candidates(raw)]
            if index not in allowed:
                return f"מחיצה {index} אינה אחת המחיצות לבחירה ({', '.join(map(str, allowed))})"
            for part in parts:
                if _whole(part.get("index")) == index:
                    part["system"], part["system_source"] = True, OPERATOR
                else:
                    part.pop("system", None)
                    part.pop("system_source", None)
            problem = self._validate(raw)
            if problem:
                log.warning("not recording the system partition of %s: %s", image_id, problem)
                return f"המניפסט לא נשמר: {problem}"
            write_atomic(path, json.dumps(raw, ensure_ascii=False, indent=2))
        return None

    #: ‏#1805/#1864: חתיכה של זרם הנתונים שהרשימה מגבבת — ‏RB_CHUNK של fanout.c.
    READBACK_CHUNK_BYTES = 4 * 1024 * 1024

    def record_readback(self, image_id: str, values: dict) -> list[int]:
        """רושם את רשימת הקריאה החוזרת (‏#1805, הכרעת נדב 20/10) למחיצות שקיבלו.

        ‏`values` = ‏{index: (רשימה, בייטים, XXH64 של הרשימה)} מ-`readbackhash.derive`.
        קודם קובץ הרשימה ליד קובץ המחיצה (`<file>.readback`, אטומית), ורק אז
        המניפסט: ‏`readback_xxh64`, ‏`readback_chunks`, ‏`readback_bytes`,
        ‏`readback_chunk_bytes` — הסוכן קורא את המניפסט שהשרת מגיש (עיקרון 3).
        ערך שאינו עקבי — רשימה שאינה שורות של 16 ספרות hex, מספר שורות שאינו
        תואם את הבייטים, בייטים מעל `size_bytes`, ‏XXH64 שאינו 16 ספרות — אינו
        נכתב: הסוכן היה מכשיל עליו מגירות תקינות. ‏`readback_sha256` ישן נשאר כמו
        שהוא. מניפסט שהשינוי היה פוסל — לא נכתב כלל. מחזיר את האינדקסים שנכתבו."""
        manifest = self.get(image_id)
        if manifest is None or not manifest.get("_dir") or not manifest.get("_available", True):
            return []
        folder = Path(manifest["_dir"])
        path = folder / "manifest.json"
        hexd = set("0123456789abcdef")
        with self._write_lock:
            raw = json.loads(path.read_text(encoding="utf-8"))
            written = []
            for part in raw.get("partitions") or []:
                if not isinstance(part, dict):
                    continue
                index = _whole(part.get("index"))
                value = values.get(index) if index is not None else None
                if not isinstance(value, tuple) or len(value) != 3:
                    continue
                text, count, xxh = value
                size = _whole(part.get("size_bytes"))
                lines = text.split("\n")[:-1] if isinstance(text, str) and text.endswith("\n") else None
                chunk = self.READBACK_CHUNK_BYTES
                if lines is None or _whole(count) is None or size is None or count > size \
                        or len(lines) != (count + chunk - 1) // chunk \
                        or any(len(x) != 16 or not set(x) <= hexd for x in lines) \
                        or not (isinstance(xxh, str) and len(xxh) == 16 and set(xxh) <= hexd):
                    continue
                base = guarded_image_path(folder, part.get("file"))
                if base is None:
                    continue
                target = base.with_name(base.name + ".readback")
                write_atomic(target, text, encoding="ascii")
                part.update({"readback_xxh64": xxh, "readback_chunks": len(lines),
                             "readback_bytes": count, "readback_chunk_bytes": chunk})
                written.append(index)
            if not written:
                return []
            problem = self._validate(raw)
            if problem:
                log.warning("not recording readback for %s: %s", image_id, problem)
                return []
            _replace_json(path, raw)
            return sorted(written)

    def delete(self, image_id: str) -> bool:
        """מוחק את תיקיית האימג' כולה. האישור בהקלדת שם — אצל הקורא."""
        manifest = self.get(image_id)
        if manifest is None:
            return False
        if not manifest.get("_dir") or not manifest.get("_available", True):
            return False
        shutil.rmtree(manifest["_dir"])
        return True
