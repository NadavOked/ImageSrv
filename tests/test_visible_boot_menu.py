"""‏#641 — היפוך #140: build/classroom בלי משימה עולה ישר ל-ImageSrv.

הכרעת נדב מ-2026-09-10: תחנת תלמיד ומחשב בנייה רשומים שאין להם משימה
עולים **ישר ל-ImageSrv, אוטומטי לגמרי** — בלי מסך בחירה, בלי ESC ובלי
טיימר. בחירת "דיסק מקומי" עברה אל תוך מסך ה-ImageSrv עצמו. זה הופך את
#140 (תפריט בחירה גלוי שהמתין לאדם, `timeout=-1`).

הקובץ הזה נפרד מ-test_grub_menu.py בכוונה: חציו הראשון מקבע את ההתנהגות
החדשה, וחציו השני הוא בקרה שלילית על המסלולים ש**לא** השתנו. הם שומרי
עיקרון 1, והם הסיבה היחידה שהשינוי הזה בטוח: מכונה זרה, ‏schema לא מוכר
ומחשב שיכפול אינם משתנים, ובדיקה שתעבור עליהם בשקט תסתיר בדיוק את מה
שחשוב.

הבדיקות כאן מייבאות רק שמות שהיו קיימים לפני השינוי, כדי שהרצה מול הקוד
הישן תיפול על **התנהגות** ולא על ImportError.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import AGENT, LOCAL, GrubConfig, decide, render

CFG = GrubConfig(server_base="http://10.44.12.10:8080")


def answer(**overrides) -> dict:
    base = {
        "schema": 1,
        "known": True,
        "role": "classroom",
        "group": {"id": "grp_LAB1", "label": "כיתה LAB1 חומרה", "suffix": "05"},
        "task": None,
        "session": None,
        "allowed_images": ["img_7f3a91"],
        "ui": {"language": "he", "require_login": False},
    }
    base.update(overrides)
    return base


def settings(text: str) -> dict[str, str]:
    """שורות ה-‎set שבראש הקובץ, כפי ש-GRUB יקרא אותן."""
    return {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("set ") and "=" in line
    }


# --- ההתנהגות החדשה ---------------------------------------------------------


def test_a_classroom_station_with_no_task_auto_boots_imagesrv():
    """זה השינוי עצמו: עד #641 היה כאן תפריט גלוי שהמתין לאדם
    (`show_menu=True`, `timeout=-1`). עכשיו תחנת כיתה בלי משימה עולה ישר
    ל-ImageSrv, אוטומטי — בלי תפריט. הערך נשאר recovery (#144): הערך
    הרגיל בתחנת כיתה מחזיר `local` ומאתחל."""
    decision = decide(answer())
    assert decision.action == AGENT
    assert decision.show_menu is False
    assert decision.recovery_mode is True


def test_the_classroom_station_boots_without_a_menu_and_without_a_timer():
    text = render(answer(), CFG)
    conf = settings(text)
    assert conf["set timeout_style"] == "hidden", "נשאר תפריט גלוי"
    assert conf["set timeout"] == "0", "נשאר טיימר המתנה"
    assert conf["set default"] == "imagesrv"


def test_the_classroom_file_still_carries_a_hidden_local_entry():
    """"בחירת דיסק מקומי מתוך מסך ImageSrv" — הערך המקומי נשאר בקובץ
    (חבוי, לעולם לא נבחר תחת timeout=0), בדיוק כמו במסלול המשימה. שני
    ערכים: ImageSrv וה-disk."""
    text = render(answer(), CFG)
    assert "--id local {" in text
    assert "--id imagesrv {" in text
    assert text.count("menuentry ") == 2


def test_the_build_machine_also_auto_boots_without_a_menu():
    """מחשב הבנייה — אותה התנהגות, אבל הערך הרגיל (build_console, #29)
    ולא recovery: אין `imagesrv.mode=recovery` בשורת הקרנל."""
    text = render(answer(role="build"), CFG)
    conf = settings(text)
    assert conf["set timeout_style"] == "hidden"
    assert conf["set timeout"] == "0"
    assert conf["set default"] == "imagesrv"
    assert "--id imagesrv {" in text
    assert "imagesrv.mode=recovery" not in text


@pytest.mark.parametrize("role", ["classroom", "build"])
def test_a_gui_role_with_no_task_auto_boots_imagesrv_hidden(role):
    """הטסט החדש של #641, על שני התפקידים: בלי משימה → `default=imagesrv`,
    `timeout=0`, `show_menu=False`. זה הקיבוע הישיר של הכרעת נדב."""
    decision = decide(answer(role=role))
    assert decision.action == AGENT
    assert decision.show_menu is False

    conf = settings(render(answer(role=role), CFG))
    assert conf["set default"] == "imagesrv"
    assert conf["set timeout"] == "0"
    assert conf["set timeout_style"] == "hidden"


@pytest.mark.parametrize("role", ["classroom", "build"])
def test_the_visible_menu_is_ascii_because_grub_cannot_render_hebrew(role):
    render(answer(role=role), CFG).encode("ascii")


# --- בקרה שלילית: שלושת השומרים שלא נגענו בהם --------------------------------


@pytest.mark.parametrize(
    "a, code",
    [
        (answer(schema=2), "bad-schema"),
        (answer(known=False), "unregistered"),
    ],
)
def test_a_machine_we_cannot_identify_still_gets_no_menu(a, code):
    """עיקרון 1: מצב לא ברור מסתיים באתחול רגיל. תפריט שם הוא ניחוש —
    ובמכונה זרה הוא גם מסך שאיש לא ביקש על מחשב שאינו שלנו."""
    decision = decide(a)
    assert decision.action == LOCAL
    assert decision.code == code
    assert decision.show_menu is False
    assert decision.offer_agent is False
    assert decision.offer_recovery is False

    text = render(a, CFG)
    conf = settings(text)
    assert conf["set timeout_style"] == "hidden"
    assert conf["set default"] == "local"
    assert "--id imagesrv" not in text
    assert "vmlinuz" not in text


@pytest.mark.parametrize("a", [answer(schema=2), answer(known=False)])
def test_a_machine_we_cannot_identify_waits_for_nothing_at_all(a):
    """"אם אין MAC הוא לא אמור לקבל GRUB" (נדב, #140). הקובץ מכיל ערך
    אחד — הדיסק המקומי — ואין בו מה ללחוץ עליו: לא ImageSrv, לא שחזור,
    ואפילו לא רמז. שתי שניות המתנה שם היו עיכוב בלי תכלית על מחשב זר,
    ולכן הוא מקבל אותו טיפול כמו MAC פגום ב-render_local_only."""
    text = render(a, CFG)
    assert settings(text)["set timeout"] == "0"
    assert text.count("menuentry ") == 1
    assert "--id local {" in text


def test_the_cloning_machine_still_goes_straight_to_the_agent():
    """למחשב שיכפול אין מערכת מקומית — מסך בחירה שממתין לאדם היה מותיר
    אותו תקוע, ו"דיסק מקומי" הוא מבוי סתום שמכבה אותו (#17)."""
    decision = decide(answer(role="cloner"))
    assert decision.action == AGENT
    assert decision.code == "cloner-wait"
    assert decision.show_menu is False

    conf = settings(render(answer(role="cloner"), CFG))
    assert conf["set timeout_style"] == "hidden"
    assert conf["set timeout"] == "0"
    assert conf["set default"] == "imagesrv"


def test_a_machine_with_a_task_still_goes_straight_to_the_agent():
    """הבקרה על הצד השני: התפריט הגלוי לא נכנס למסלול שבו יש עבודה."""
    conf = settings(render(answer(task={"id": "tsk_0091"}), CFG))
    assert conf["set timeout_style"] == "hidden"
    assert conf["set timeout"] == "0"
    assert conf["set default"] == "imagesrv"
