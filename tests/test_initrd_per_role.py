"""‏#32 — איזה initramfs כל תפקיד מקבל, ובקובץ שנרנדר בפועל.

הכרעת הבעלים (2026-09-04):

    build      → מסך גרפי
    classroom  → מסך גרפי
    cloner     → מסך גרפי (הכרעת הבעלים החדשה: למחשב השיכפול יש מסך,
                 והוא מציג התקדמות פר-מגירה ותפריט SMART גרפי)

מה שחסם: ‏`GrubConfig.initrd_path` היה **ערך יחיד**, ולכן כל מכונה קיבלה
את אותו initramfs — הטקסטואלי בן 37MB או הגרפי בן 212MB, לפי מה שהיה
מותקן על השרת באותו רגע. לא היה מנגנון שמבדיל.

**הקובץ הזה בודק את שורת ה-`initrd` בקובץ שיצא, לא את ההחלטה.** זה
הלקח של #320: ‏`decide()` היה נכון, והקובץ בכל זאת יצא שגוי. החלטה
נכונה אינה מוכיחה קובץ נכון.

שני העקרונות שנבדקים כאן במפורש:

1. **ברירת מחדל בטוחה (עיקרון 1).** תפקיד לא מוכר, ‏MAC לא רשום, או
   קובץ GUI שאינו קיים על הדיסק — כולם מקבלים את הטקסטואלי. מכונה
   שלא עולה גרועה ממכונה בלי גואי.
2. **הבחירה תלויה בתפקיד, לא במסלול.** ‏cloner מגיע ל-AGENT בשלושה
   מסלולים (`cloner-wait`, ‏`session-joinable`, ‏`task-assigned`), וגם
   ‏build ו-classroom מגיעים לערך הסוכן בשני מסלולים שונים. כיסוי של
   מסלול אחד בלבד הוא בדיוק הבור ש-#320 נפל בו.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import ROLES_WITH_GUI, GrubConfig, render

#: תצורה שבה שני ה-initramfs זמינים. זה המצב שבו הבחירה בכלל אפשרית,
#: ולכן זה המצב שבו ההבחנה בין התפקידים נמדדת.
CFG = GrubConfig(
    server_base="http://10.44.12.10:8080",
    gui_initrd_path="/boot/initrd.img.gui",
)

#: אותו שרת בדיוק, אבל בלי initramfs גרפי על הדיסק. זה מה ש-`boot/http.py`
#: מוסר כשהקובץ אינו קיים — ‏None, ולא נתיב שאולי יעבוד.
CFG_NO_GUI = GrubConfig(server_base="http://10.44.12.10:8080")

TEXT_INITRD = "initrd (http,10.44.12.10:8080)/boot/initrd.img"
GUI_INITRD = "initrd (http,10.44.12.10:8080)/boot/initrd.img.gui"


def answer(**overrides) -> dict:
    base = {
        "schema": 1,
        "known": True,
        "role": "classroom",
        "task": None,
        "session": None,
        "allowed_images": ["img_7f3a91"],
        "ui": {"language": "he", "require_login": False},
    }
    base.update(overrides)
    return base


def initrd_lines(text: str) -> list[str]:
    """שורות ה-`initrd` בקובץ. הן היחידות שקובעות מה נטען בפועל."""
    return [line.strip().removeprefix("if ").removesuffix("; then")
            for line in text.splitlines()
            if line.strip().startswith(("initrd ", "if initrd "))]


#: כל מסלול שבו נוצר ערך `menuentry "ImageSrv"` — כלומר כל מסלול שבו
#: יש בכלל שורת initrd בקובץ. מ-#641 כל ארבעת המסלולים (כולל no-task)
#: מגיעים ל-AGENT עבור build/classroom, ובכולם ערך ה-ImageSrv נטען
#: מה-initramfs של התפקיד.
AGENT_ROUTES = [
    pytest.param({}, id="no-task"),
    pytest.param({"task": {"id": "tsk_0091"}}, id="task-assigned"),
    pytest.param({"session": {"id": "ses_a91f", "state": "open"}}, id="session-open"),
    pytest.param({"session": {"id": "ses_a91f", "state": "running"}}, id="session-running"),
]


# --- הדרישה: מי מקבל גרפי ---------------------------------------------------


@pytest.mark.parametrize("route", AGENT_ROUTES)
@pytest.mark.parametrize("role", ["build", "classroom", "cloner"])
def test_gui_roles_get_the_gui_initramfs_on_every_route(role, route):
    """שלושת התפקידים שיש להם מסך, בכל מסלול שמייצר ערך ImageSrv.

    ‏cloner נוסף בהכרעת הבעלים: למחשב השיכפול יש מסך, והוא מציג
    התקדמות פר-מגירה ותפריט SMART גרפי. הפרמטריזציה על המסלולים אינה
    קישוט — ‏cloner מגיע לסוכן ב-`cloner-wait`, ב-`session-joinable`
    וב-`task-assigned`, ומחשבי השיכפול מתחילים כל בוקר בהמתנה ועוברים
    לסבב. אילו הבחירה הייתה לפי `code` ולא לפי תפקיד, המסך היה נעלם
    ברגע שנפתח סבב — אותו באג של #320.
    """
    text = render(answer(role=role, **route), CFG)
    assert initrd_lines(text) == [GUI_INITRD]


def test_the_gui_role_set_includes_the_cloner():
    """אותה דרישה על הקבוע עצמו, כדי שהכוונה תהיה קריאה ולא רק נגזרת.

    **זו הבקרה השלילית של השינוי:** מחזירים את `ROLES_WITH_GUI`
    לקבוצה הישנה `{"build", "classroom"}`, והטסטים כאן ובמסלול
    ה-cloner נופלים — כי אז ה-cloner מקבל שוב את ה-initramfs הטקסטואלי.
    """
    assert "cloner" in ROLES_WITH_GUI
    assert ROLES_WITH_GUI == frozenset({"build", "classroom", "cloner"})


# --- ברירת מחדל בטוחה --------------------------------------------------------


@pytest.mark.parametrize("route", AGENT_ROUTES)
@pytest.mark.parametrize("role", ["build", "classroom", "cloner"])
def test_no_gui_file_on_disk_means_everyone_gets_the_text_initramfs(role, route):
    """‏`gui_initrd_path=None` = הקובץ אינו קיים על השרת. מכונה שלא עולה
    גרועה ממכונה בלי גואי (עיקרון 1), ולכן **כל** תפקיד נופל לטקסטואלי.

    זו גם ההגנה מפני הפער בין הבנייה לפריסה: ה-GUI נבנה בגרסה משלו,
    ומי שפורס גרסה חדשה בלי לבנות אותו מחדש היה מקבל 404 מול GRUB
    בכיתה שלמה."""
    text = render(answer(role=role, **route), CFG_NO_GUI)
    assert initrd_lines(text) == [TEXT_INITRD]


@pytest.mark.parametrize("route", AGENT_ROUTES)
@pytest.mark.parametrize("role", ["unknown", "", "kiosk"])
def test_an_unrecognised_role_gets_the_text_initramfs(role, route):
    """תפקיד שאינו ברשימה — כולל מחר, כשמישהו יוסיף תפקיד ל-DB ולא
    לכאן. מצב לא ברור מסתיים בבטוח, לא בגדול."""
    text = render(answer(role=role, **route), CFG)
    assert initrd_lines(text) == [TEXT_INITRD]


@pytest.mark.parametrize("route", AGENT_ROUTES)
def test_a_non_string_role_gets_the_text_initramfs(route):
    """‏`decide()` הופך role שאינו מחרוזת ל-"unknown". זה אותו מסלול,
    ובכל זאת נבדק — כי `None in frozenset` לא זורק, והוא היה עובר
    בשקט אילו ההשוואה נעשתה על הערך הגולמי."""
    text = render(answer(role=None, **route), CFG)
    assert initrd_lines(text) == [TEXT_INITRD]


def test_an_unregistered_mac_loads_no_initramfs_at_all():
    """‏known=false אינו מקבל ערך ImageSrv בכלל — לא גרפי ולא טקסטואלי.
    בקרה על כך שההרחבה לא הצמיחה שורת initrd במסלול שלא הייתה בו."""
    text = render(answer(known=False, role="build"), CFG)
    assert initrd_lines(text) == []
    assert "initrd.img.gui" not in text


def test_a_bad_schema_loads_no_initramfs_at_all():
    text = render(answer(schema=99, role="classroom"), CFG)
    assert initrd_lines(text) == []
    assert "initrd.img.gui" not in text


# --- מה שאסור לשבור ----------------------------------------------------------


def test_the_gui_path_never_reaches_the_kernel_command_line():
    """שורת הפקודה נשארת נקייה (עיקרון 2). הבחירה מתבטאת בשורת ה-initrd
    בלבד — לא בפרמטר קרנל חדש שהסוכן היה צריך לפרש."""
    text = render(answer(role="build"), CFG)
    linux_line = [ln for ln in text.splitlines()
                  if ln.strip().startswith("if linux ")][0]
    assert "gui" not in linux_line
    assert "initrd" not in linux_line


def test_the_default_config_has_no_gui_path():
    """ברירת המחדל של `GrubConfig` היא בלי גואי. מי שרוצה אותו מצהיר
    עליו — אחרי שווידא שהקובץ קיים."""
    assert GrubConfig(server_base="http://10.44.12.10:8080").gui_initrd_path is None


@pytest.mark.parametrize("role", ["build", "classroom"])
def test_the_gui_roles_keep_everything_else_around_the_initrd(role):
    """‏#641 שינה את האתחול לאוטומטי (`default=imagesrv`, `timeout=0`),
    אבל #144 לא זז — שני ערכים, הדיסק המקומי לצד ImageSrv — ו-#32 נוגע
    בשורת ה-initrd ובה בלבד."""
    text = render(answer(role=role), CFG)
    assert "set timeout=0" in text
    assert "set timeout_style=hidden" in text
    assert "set default=imagesrv" in text
    assert text.count("menuentry ") == 2
    assert "--id local {" in text
    assert "chainloader" in text


@pytest.mark.parametrize("route", AGENT_ROUTES)
def test_the_cloner_file_is_still_diskless(route):
    """‏#320 לא זז. הבחירה לפי תפקיד נוספת לחריג הקיים, לא מחליפה אותו."""
    text = render(answer(role="cloner", **route), CFG)
    assert text.count("menuentry ") == 1
    assert "chainloader" not in text
    assert "chain_local" not in text


@pytest.mark.parametrize("role", ["build", "classroom", "cloner", "unknown"])
def test_the_file_stays_pure_ascii_with_a_gui_path(role):
    render(answer(role=role), CFG).encode("ascii")


def test_a_recovery_entry_follows_the_role_too():
    """תחנת כיתה בלי משימה מקבלת את ערך ImageSrv במצב `recovery` (#144).
    גם הוא נטען מה-initramfs של התפקיד — הבחירה היא לפי תפקיד, לא לפי
    מצב הערך. אחרת אותה מכונה הייתה מקבלת מסך שונה בשני מסלולים."""
    text = render(answer(role="classroom"), CFG)
    assert "imagesrv.mode=recovery" in text
    assert initrd_lines(text) == [GUI_INITRD]
