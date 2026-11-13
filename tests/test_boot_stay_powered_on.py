"""#345 — מסך הכישלון מבטיח "will stay powered on", ו-`halt` מכבה.

שני מסכי העצירה בקובץ ה-GRUB — הכישלון של `chain_local` (UEFI, לא נמצאה
או נדחתה מערכת מקומית) והמסך של `try_local` ל-Legacy BIOS (#323) —
מסתיימים במשפט `Contact IT. This computer will stay powered on.`, ומיד
אחריו ב-`sleep --interruptible 60` ו-`halt`.

`halt` **אינו** "עצור והשאר את המסך". על EFI הוא קורא ל-ResetSystem עם
Shutdown, ועל i386-pc הוא מכבה דרך APM/ACPI — בשתי הפלטפורמות המכונה
נכבית, והדקה שהמפעיל רואה היא ה-`sleep` שלפניה. כלומר: המסך הזה קיים
בדיוק כדי שטכנאי יקרא אותו, והוא מוחק את עצמו לפני שהטכנאי מגיע —
ואז המכונה שהוא מוצא כבויה נראית כמו תקלת חשמל, לא ככישלון אתחול.

**הבדיקות כאן הן על הפער, לא על מימוש מסוים.** מסך שמבטיח להישאר דולק
לא יכיל פקודת כיבוי, ו"לא מכבה" לבדו אינו מספיק: קובץ GRUB שנגמר מחזיר
את השליטה ל-normal, ולכן צריכה להיות ראיה חיובית שההמתנה אינה נגמרת
(עיקרון 5).

**מה לא נבדק כאן:** אם `halt` באמת מכבה. זה קוד GRUB על חומרה, וההוכחה
היחידה לו היא המכונה במעבדה — היא מה שפתח את #345.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import GrubConfig, render, render_bootstrap, render_local_only

CFG = GrubConfig(server_base="http://10.44.12.10:8080")

#: פקודות GRUB שמסיימות את ההפעלה של המכונה. מסך שמבטיח להישאר דולק
#: לא מכיל אף אחת מהן.
POWER_OFF_COMMANDS = ("halt", "poweroff", "shutdown", "reboot")

#: המשפט שמסך העצירה מבטיח לאדם שעומד מול המכונה.
PROMISE = "This computer will stay powered on."

#: שתי הפונקציות שמסתיימות במסך עצירה. `chain_local` הוא מסלול ה-UEFI
#: שלא מצא (או שנדחה) בדיסק, ו-`try_local` הוא השומר של #323 — הענף
#: שלו ל-Legacy BIOS הוא מחשב שיכפול שאין לו מערכת מקומית להעלות.
STOP_SCREENS = ("chain_local", "try_local")


def answer(**overrides) -> dict:
    base = {
        "schema": 1,
        "known": True,
        "role": "classroom",
        "group": {"id": "grp_A", "label": "כיתה א", "suffix": "01"},
        "task": None,
        "session": None,
        "allowed_images": ["img_7f3a91"],
        "ui": {"language": "he", "require_login": False},
    }
    base.update(overrides)
    return base


#: כל קובץ שבו הפונקציות האלה מופיעות — הסטטי שעל ה-TFTP והדינמיים.
#: מסך העצירה מוגדר במקום אחד, אבל הוא מוגש משלושה, ותיקון שנעצר
#: באחד מהם משאיר את הפער בשניים.
RENDERED = [
    pytest.param(render_bootstrap(CFG), id="bootstrap"),
    pytest.param(render(answer(), CFG), id="dynamic-menu"),
    pytest.param(render_local_only("bad-mac"), id="local-only"),
]


def _function(text: str, name: str) -> str:
    """גוף הפונקציה בלבד, מ-`function <name> {` ועד הסוגר שלה."""
    start = text.index(f"function {name} {{")
    return text[start : text.index("\n}", start) + 2]


def _commands(body: str) -> list[str]:
    """המילה הראשונה בכל שורה שאינה הערה — הפקודות שבאמת ירוצו.
    `echo "... halt ..."` הוא טקסט על המסך, לא פקודה."""
    words = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            words.append(stripped.split()[0])
    return words


# --- הפער עצמו ---------------------------------------------------------------


@pytest.mark.parametrize("text", RENDERED)
@pytest.mark.parametrize("name", STOP_SCREENS)
def test_a_screen_that_promises_to_stay_on_does_not_power_the_machine_off(name, text):
    """הדרישה של #345. המסך אומר לטכנאי שהמכונה תישאר דולקת — ולכן
    אסור שתרוץ בו פקודה שמכבה אותה."""
    body = _function(text, name)
    assert PROMISE in body, f"{name} כבר אינו מבטיח להישאר דולק — עדכן את הבדיקה"
    found = [c for c in _commands(body) if c in POWER_OFF_COMMANDS]
    assert not found, (
        f"{name} מבטיח '{PROMISE}' ומריץ {found} — זה הפער של #345"
    )


@pytest.mark.parametrize("text", RENDERED)
@pytest.mark.parametrize("name", STOP_SCREENS)
def test_the_stop_screen_waits_in_a_loop_that_has_no_way_out(name, text):
    """"לא מכבה" אינו "נשאר". קובץ GRUB שנגמר מחזיר את השליטה ל-normal,
    והמסך נעלם בלי לכבות. הראיה החיובית היא לולאה שההמתנה יושבת בתוכה,
    ושהמשתנה בתנאי שלה אינו משתנה בגוף — כלומר היא לעולם לא מסתיימת."""
    body = _function(text, name)
    assert "while " in body, f"{name} אינו ממתין בלולאה — המסך ייעלם"

    # `chain_local` פותח ב-`for ... done` על נתיבי האתחול, ולכן ה-`done`
    # שמעניין כאן הוא זה שאחרי ה-`while`, לא הראשון בגוף.
    start = body.index("while ")
    loop = body[start : body.index("done", start)]
    assert "sleep" in loop, "ההמתנה אינה בתוך הלולאה — לולאה עסוקה תשרוף מעבד"
    # התנאי הוא השוואת משתנה, והמשתנה נקבע פעם אחת לפני הלולאה בלבד.
    condition = loop.splitlines()[0]
    assert "$stay_on" in condition, f"תנאי הלולאה של {name} אינו על stay_on"
    assert loop.count("set stay_on=") == 0, (
        "משהו בגוף הלולאה מציב stay_on — כלומר יש לה יציאה"
    )
    assert body.count("set stay_on=") == 1, (
        f"{name} מציב stay_on יותר מפעם אחת"
    )


# --- בקרה על הכיוון ההפוך: מה שנשאר כפי שהיה ----------------------------------


@pytest.mark.parametrize("text", RENDERED)
def test_the_stop_screens_are_still_ascii(text):
    """עיקרון 8 — פלט GRUB תמיד ASCII."""
    text.encode("ascii")


@pytest.mark.parametrize("text", RENDERED)
def test_the_wait_is_still_interruptible_so_a_key_does_not_hang_the_screen(text):
    """`sleep --interruptible` נשאר: בלעדיו לחיצת מקש אינה נקלטת כלל
    (GRUB אינו קורא מהמקלדת תוך כדי `sleep` רגיל), והמסך נראה תקוע."""
    for name in STOP_SCREENS:
        body = _function(text, name)
        assert "sleep --interruptible" in body


def test_the_local_disk_is_still_tried_before_any_stop_screen():
    """עיקרון 1 לא זז — ההמתנה היא **אחרי** שכל נתיבי האתחול נוסו,
    לא במקומם."""
    body = _function(render_bootstrap(CFG), "chain_local")
    assert body.index("try_chain") < body.index("while ")
    assert "/EFI/Microsoft/Boot/bootmgfw.efi" in render_bootstrap(CFG)
