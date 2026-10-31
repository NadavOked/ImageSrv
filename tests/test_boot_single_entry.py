"""‏#144 — ערך ImageSrv **אחד** בתפריט, והוא הנכון לתפקיד המכונה.

עד כאן קיבלה מכונה רשומה בלי משימה שלושה ערכים: הדיסק, `imagesrv`
ו-`imagesrv-recovery` — **ואין על המסך שום דבר שמסביר את ההבדל**. ב-30/08
נדב אתחל את הלנובו (`role=classroom`), בחר `ImageSrv`, והמכונה אתחלה בלי
לעשות דבר: `agent/lib/core/decide.sh` על `classroom` בלי משימה מחזיר `local`
ו-`die_local` מאתחל. הערך שהיה שימושי לו הוא דווקא השחזור. ומאז #641
המכונה עולה אל ערך ה-ImageSrv הזה **אוטומטית** (היפוך #140), ולכן חשוב
כפליים שזה יהיה הערך השימושי — recovery, ולא הרגיל שמאתחל בלי לעשות דבר.

המיפוי כאן הוא טבלת ההחלטה של הסוכן קרואה מהצד השני, לא העדפה:

    classroom (וכל תפקיד לא מוכר) → recovery — כי "רגיל" מחזיר local
    build                          → רגיל     — כי הוא מחזיר build_console

הקובץ בודק גם את מה ש**לא** זז: מכונה לא מזוהה, מחשב שיכפול, ומכונה עם
משימה. כמו ב-test_visible_boot_menu.py, הייבוא כאן הוא של שמות שהיו
קיימים לפני השינוי בלבד — כדי שהרצה מול הקוד הישן תיפול על **התנהגות**
ולא על ImportError.
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
    return {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("set ") and "=" in line
    }


def agent_entry(text: str) -> str:
    """גוף הערך היחיד שאינו הדיסק המקומי."""
    assert "--id imagesrv {" in text, "אין ערך ImageSrv בקובץ"
    return text.split("--id imagesrv {", 1)[1].split("}", 1)[0]


# --- ערך אחד, והוא הנכון ------------------------------------------------------


@pytest.mark.parametrize("role", ["classroom", "build", "unknown", "teacher"])
def test_a_registered_machine_gets_exactly_one_imagesrv_entry(role):
    """שני ערכים בסך הכל: הדיסק, ו-ImageSrv אחד. זו הבקשה של נדב —
    "רק אפשרות אחת בנוסף לעלות מהדיסק"."""
    text = render(answer(role=role), CFG)
    assert text.count("menuentry ") == 2
    assert text.count("--id imagesrv") == 1
    assert "--id local {" in text
    assert "--id imagesrv-recovery" not in text


def test_the_classroom_entry_is_the_one_that_actually_does_something():
    """‏`decide.sh` על classroom בלי משימה: מצב רגיל מחזיר `local`
    ומאתחל, ‏recovery מגיע למסך הכניסה והשחזור. מ-#641 המכונה עולה אליו
    אוטומטית, ולכן הערך שנבחר חייב להיות recovery — לא הרגיל (#144)."""
    decision = decide(answer(role="classroom"))
    assert decision.action == AGENT
    assert decision.recovery_mode is True, "הערך הרגיל מאתחל בלי לעשות דבר"

    assert "imagesrv.mode=recovery" in agent_entry(render(answer(), CFG))


def test_the_build_machine_entry_reaches_the_build_console():
    """מחשב הבנייה הוא ההפך הגמור: ‏`decide.sh` מחזיר `build_console`
    במצב רגיל, ו-recovery היה מסתיר את מסך הקליטה/ההפצה (#29). מ-#641
    הוא עולה אליו אוטומטית, במצב הרגיל."""
    decision = decide(answer(role="build"))
    assert decision.action == AGENT
    assert decision.recovery_mode is False

    assert "imagesrv.mode=recovery" not in agent_entry(
        render(answer(role="build"), CFG))


@pytest.mark.parametrize("role", ["unknown", "teacher", ""])
def test_a_role_we_do_not_recognise_is_treated_like_a_classroom_station(role):
    """ברירת המחדל של `decide.sh` לכל תפקיד שאינו build/cloner היא
    `local` — בדיוק כמו classroom, ולכן אותו ערך."""
    assert decide(answer(role=role)).offer_recovery is True
    assert "imagesrv.mode=recovery" in agent_entry(render(answer(role=role), CFG))


def test_exactly_one_of_the_two_offers_is_lit_for_every_menu_role():
    """השומר מפני חזרה לשניים, לתפקידים שעדיין מקבלים את התפריט הגלוי
    (שאינם ב-ROLES_WITH_GUI): לא "שניהם" ולא "אף אחד"."""
    for role in ["unknown", "teacher", "", "guest"]:
        decision = decide(answer(role=role))
        assert decision.offer_agent != decision.offer_recovery, role


def test_a_gui_role_auto_boots_with_the_mode_its_role_needs():
    """מ-#641 build/classroom אינם בתפריט אלא עולים ישר לסוכן, והערך
    שלהם נקבע ב-recovery_mode: ‏build רגיל (build_console), ‏classroom
    recovery — אותה טבלת החלטה, צד אחר."""
    assert decide(answer(role="build")).recovery_mode is False
    assert decide(answer(role="classroom")).recovery_mode is True


def test_the_single_entry_is_called_plainly_imagesrv():
    """"ImageSrv - recovery / imaging" היה מובן רק ליד "ImageSrv". עם
    ערך אחד הוא רק שואל את המפעיל שאלה שאין לו דרך לענות עליה."""
    for role in ["classroom", "build"]:
        text = render(answer(role=role), CFG)
        assert 'menuentry "ImageSrv" --id imagesrv {' in text   # #1302: השם הגלוי; ה-id נשאר
        assert 'echo "Loading ImageSrv..."' in text and "Loading ImageCtl" not in text
        assert "recovery / imaging" not in text
        text.encode("ascii")                    # פלט GRUB תמיד ASCII


def test_the_gui_roles_auto_boot_without_a_menu():
    """‏#641 הפך את #140: build/classroom בלי משימה עולים ישר ל-ImageSrv,
    בלי תפריט ובלי טיימר. מה שלא זז הוא #144 — ערך ImageSrv אחד."""
    for role in ["classroom", "build"]:
        conf = settings(render(answer(role=role), CFG))
        assert conf["set timeout_style"] == "hidden"
        assert conf["set timeout"] == "0"
        assert conf["set default"] == "imagesrv"


def test_the_entry_carries_nothing_but_the_server_and_the_mode():
    """עיקרון 2 — שורת הקרנל נקייה. ‏`imagesrv.mode=recovery` הוא החריג
    היחיד המותר, וצמצום התפריט אינו תירוץ להוסיף עוד."""
    body = agent_entry(render(answer(), CFG))
    line = next(part for part in body.splitlines() if part.strip().startswith("if linux "))
    for leak in ["classroom", "grp_LAB1", "img_7f3a91", "LAB1", "role=", "task"]:
        assert leak not in line
    flags = [word for word in line.split() if word.startswith("imagesrv.")]
    assert flags == ["imagesrv.server=http://10.44.12.10:8080",
                     "imagesrv.mode=recovery"]


# --- מה שאסור לשבור -----------------------------------------------------------


@pytest.mark.parametrize("a", [answer(known=False), answer(schema=2)])
def test_a_machine_we_do_not_serve_gets_no_menu_and_no_imagesrv(a):
    """עיקרון 1, ושלב 7 בהדגמה: ערך אחד בקובץ, `timeout=0`, בלי תפריט,
    בלי ImageSrv ובלי שחזור."""
    conf = settings(render(a, CFG))
    assert conf["set timeout"] == "0"
    assert conf["set timeout_style"] == "hidden"
    text = render(a, CFG)
    assert text.count("menuentry ") == 1
    assert "--id imagesrv" not in text and "vmlinuz" not in text


def test_the_cloning_machine_still_goes_straight_to_the_agent():
    """"לא מדבר על מחשבי שיכפול" (נדב, #144). אין לו מערכת מקומית, ומסך
    שממתין לאדם ליד מכונה חסרת מסך היה משאיר אותו תקוע (#17)."""
    decision = decide(answer(role="cloner"))
    assert decision.action == AGENT and decision.code == "cloner-wait"
    assert decision.show_menu is False

    conf = settings(render(answer(role="cloner"), CFG))
    assert conf["set timeout_style"] == "hidden"
    assert conf["set timeout"] == "0"
    assert conf["set default"] == "imagesrv"


def test_a_machine_with_work_still_goes_straight_to_the_agent():
    """מסלול המשימה והסבב לא נגענו בו — הוא AGENT, מסלול אחר לגמרי."""
    for a in [answer(task={"id": "tsk_0091"}),
              answer(session={"id": "ses_a91f", "state": "open"})]:
        conf = settings(render(a, CFG))
        assert conf["set timeout"] == "0"
        assert conf["set default"] == "imagesrv"
        assert "imagesrv.mode=recovery" not in render(a, CFG)


def test_the_boot_loop_guard_still_leaves_a_way_into_recovery():
    """‏#75: דווקא המכונה שהשומר החזיר לדיסק היא זו שטכנאי צריך להיכנס
    אליה. עם ערך אחד — הערך הזה הוא השחזור, וזה עדיין הפתח היחיד לתקן
    מכונה תקועה בשטח."""
    a = answer(task={"id": "tsk_0091"}, boot_guard="exhausted")
    decision = decide(a)
    assert decision.code == "boot-loop-guard" and decision.show_menu is True

    text = render(a, CFG)
    assert settings(text)["set timeout_style"] == "menu"
    assert "imagesrv.mode=recovery" in agent_entry(text)


def test_the_guarded_build_machine_keeps_its_capture_screen():
    """אותו שומר על מחשב הבנייה: מה שהדגל מוריד הוא המשימה, לא הדרך
    למסך הקליטה — וזו עדיין הדרך היחידה אליו מאתחול קר (#29)."""
    a = answer(role="build", task={"id": "tsk_4b1e"}, boot_guard="exhausted")
    assert decide(a).offer_agent is True
    assert "imagesrv.mode=recovery" not in agent_entry(render(a, CFG))
