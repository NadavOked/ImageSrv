"""‏#320 — למחשב שיכפול יש מסלול אתחול אחד, והוא PXE.

המגירות של מחשב השיכפול הן הסחורה, לא דיסק אתחול. אתחול מאחת מהן הוא
נגיעה בדיסק שאמור להיכתב — ולכן הקובץ שהוא מקבל חייב לצאת בלי ערך
"עלה מהדיסק המקומי", בלי `chainloader`, ובלי תפריט גלוי.

**הקובץ הזה בודק את מה שנרנדר, לא את ההחלטה.** ‏`decide()` החזיר
`Decision(AGENT, "cloner-wait")` נכון מאז #17, ובכל זאת הקובץ שיצא בפועל
הכיל שני ערכים — `imagesrv` **ו**-`menuentry "Boot from local disk"` עם
‏`chain_local` שמאחוריו, שמנסה `chainloader` על שנים-עשר נתיבי EFI. החלטה
נכונה אינה מוכיחה קובץ נכון, וזה בדיוק הפער ש-#320 סגר.

הבדיקות רצות על **שלושת** המסלולים שבהם cloner מגיע ל-AGENT — בלי סבב,
עם סבב פתוח, ועם משימה — כי מחר בבוקר המכונות מתחילות בהמתנה ועוברות
לסבב, וקובץ שמצמיח ערך מקומי ברגע שנפתח סבב הוא אותו באג בדיוק.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import GrubConfig, render, render_local_only

CFG = GrubConfig(server_base="http://10.44.12.10:8080")

#: אותו שרת, אבל עם ה-initramfs הגרפי זמין על הדיסק. מאז שה-cloner
#: קיבל מסך (הכרעת הבעלים) הוא ב-ROLES_WITH_GUI, וזה המצב שבו נבדק
#: שהמסך לא שבר את #17: עדיין ערך יחיד, חבוי, ובלי דיסק מקומי.
CFG_GUI = GrubConfig(
    server_base="http://10.44.12.10:8080",
    gui_initrd_path="/boot/initrd.img.gui",
)


def answer(**overrides) -> dict:
    base = {
        "schema": 1,
        "known": True,
        "role": "cloner",
        "group": {"id": "grp_CLONERS", "label": "חדר שיכפולים", "suffix": "01"},
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


#: שלושת המסלולים שבהם מחשב שיכפול מגיע לסוכן. הראשון הוא המצב של מחר
#: בבוקר: מכונה שעולה קרה, אין לה משימה ואין סבב פתוח.
CLONER_ANSWERS = [
    pytest.param(answer(), id="no-task-no-session"),
    pytest.param(answer(session={"id": "ses_a91f", "state": "open"}), id="session-open"),
    pytest.param(answer(session={"id": "ses_a91f", "state": "running"}), id="session-running"),
    pytest.param(answer(task={"id": "tsk_0091"}), id="task-assigned"),
]


# --- הדרישה: ערך יחיד, והוא הסוכן --------------------------------------------


@pytest.mark.parametrize("a", CLONER_ANSWERS)
def test_the_cloner_file_holds_exactly_one_entry(a):
    """ערך אחד בקובץ. שניים פירושם שיש מה לבחור, ובחירה במחשב שיכפול
    היא בחירה בין הסוכן לבין כתיבה על הסחורה."""
    text = render(a, CFG)
    assert text.count("menuentry ") == 1
    assert 'menuentry "ImageSrv" --id imagesrv {' in text


@pytest.mark.parametrize("a", CLONER_ANSWERS)
def test_the_cloner_file_has_no_local_boot_entry(a):
    """הערך שהיה שם עד #320 — "Boot from local disk"."""
    text = render(a, CFG)
    assert "--id local" not in text
    assert "Boot from local disk" not in text


@pytest.mark.parametrize("a", CLONER_ANSWERS)
def test_the_cloner_file_never_chainloads_anything(a):
    """‏`chainloader` הוא הפקודה שמעבירה שליטה לדיסק. היא לא בערך המקומי
    אלא בפונקציה `chain_local` שמעליו, ולכן מחיקת הערך לבדה הייתה
    משאירה אותה בקובץ עם רשימת שנים-עשר נתיבי ה-EFI שהיא מנסה."""
    text = render(a, CFG)
    assert "chainloader" not in text
    assert "chain_local" not in text
    assert "try_chain" not in text
    assert "bootmgfw.efi" not in text          # רשימת נתיבי האתחול כולה
    assert "shimx64.efi" not in text
    assert "search_fs_file" not in text


@pytest.mark.parametrize("a", CLONER_ANSWERS)
def test_the_cloner_file_shows_no_menu_and_waits_for_nobody(a):
    """‏timeout=0 **וגם** timeout_style=hidden. ‏hidden לבדו עם טיימר
    חיובי הוא עדיין המתנה, ו-timeout=0 לבדו עם style=menu הוא עדיין
    מסך שנדלק — ליד מכונה שאין לה מסך ואין לה אדם."""
    conf = settings(render(a, CFG))
    assert conf["set timeout"] == "0"
    assert conf["set timeout_style"] == "hidden"
    assert conf["set default"] == "imagesrv"


@pytest.mark.parametrize("a", CLONER_ANSWERS)
def test_the_cloner_file_still_loads_the_agent(a):
    """בקרה על הכיוון השני: קובץ בלי דיסק **ובלי סוכן** הוא מכונה
    תקועה. מה שנשאר חייב להיות מסלול שלם — קרנל, initrd, וכתובת השרת."""
    text = render(a, CFG)
    assert "linux (http,10.44.12.10:8080)/boot/vmlinuz" in text
    assert "initrd (http,10.44.12.10:8080)/boot/initrd.img" in text
    assert "imagesrv.server=http://10.44.12.10:8080" in text


@pytest.mark.parametrize("a", CLONER_ANSWERS)
def test_the_cloner_file_is_pure_ascii(a):
    """‏GRUB לא מרנדר עברית — ותווית הקבוצה כאן היא "חדר שיכפולים"."""
    render(a, CFG).encode("ascii")


# --- #17 לא זז כשל-cloner יש מסך --------------------------------------------


@pytest.mark.parametrize("a", CLONER_ANSWERS)
def test_gui_cloner_still_has_one_hidden_agent_entry(a):
    """מאז שהתווסף ה-cloner ל-ROLES_WITH_GUI (מסך למחשב השיכפול), הקובץ
    מושך את ה-initramfs הגרפי — אבל שום דבר אחר לא זז. עדיין ערך יחיד,
    חבוי, בלי דיסק מקומי ובלי `chainloader`, בכל שלושת המסלולים.

    זו בדיוק הבדיקה ש-#320 לימד שצריך: החלטה נכונה אינה מוכיחה קובץ
    נכון, וכאן נבדק שהמסך התבטא ב**שורת ה-initrd בלבד**."""
    text = render(a, CFG_GUI)
    conf = settings(text)
    assert conf["set timeout"] == "0"
    assert conf["set timeout_style"] == "hidden"
    assert conf["set default"] == "imagesrv"
    assert text.count("menuentry ") == 1
    assert "--id local {" not in text
    assert "chainloader" not in text
    # הראיה החיובית שהמסך אכן נבחר: זה ה-initramfs הגרפי, לא הטקסטואלי.
    assert "initrd.img.gui" in text


# --- מה שאסור לשבור: הצמצום הוא ל-cloner בלבד --------------------------------


@pytest.mark.parametrize("role", ["classroom", "build"])
def test_a_gui_role_auto_boots_imagesrv_with_a_hidden_local_entry(role):
    """‏#641: תחנת כיתה ומחשב בנייה בלי משימה עולים ישר ל-ImageSrv
    (‏`default=imagesrv`, ‏`timeout=0`), אבל הדיסק המקומי **נשאר בקובץ**
    (חבוי) — בשונה מ-cloner, שאין לו דיסק כלל. שני ערכים, ו-chain_local
    מאחורי הערך המקומי."""
    text = render(answer(role=role), CFG)
    conf = settings(text)
    assert conf["set timeout"] == "0"
    assert conf["set timeout_style"] == "hidden"
    assert conf["set default"] == "imagesrv"
    assert text.count("menuentry ") == 2
    assert "--id local {" in text
    assert "chainloader" in text


def test_a_menu_role_outside_the_gui_set_keeps_its_visible_menu():
    """תפקיד רשום שאינו ב-ROLES_WITH_GUI (למשל unknown) לא השתנה ב-#641:
    הוא עדיין מקבל את התפריט הגלוי של #140, בלי טיימר, עם הדיסק לצד
    ImageSrv."""
    text = render(answer(role="unknown"), CFG)
    conf = settings(text)
    assert conf["set timeout"] == "-1"
    assert conf["set timeout_style"] == "menu"
    assert text.count("menuentry ") == 2
    assert "--id local {" in text
    assert "chainloader" in text


def test_a_classroom_station_with_a_task_keeps_its_local_fallback():
    """המסלול המקביל בדיוק — ‏AGENT עם משימה — בתחנה שיש לה מערכת
    מקומית. שם הערך המקומי נשאר, כי שם הוא רשת ביטחון ולא סכנה."""
    text = render(answer(role="classroom", task={"id": "tsk_0091"}), CFG)
    assert text.count("menuentry ") == 2
    assert "--id local {" in text
    assert "chainloader" in text


# --- #323: הקובץ הסטטי, שרץ לפני שיש את מי לשאול --------------------------
#
# ‏#320/#322 תיקנו את הקובץ ה**דינמי** — זה שהשרת מייצר לפי MAC.
# ‏`render_bootstrap()` מייצר את הקובץ ה**סטטי** שיושב על ה-TFTP, והוא רץ
# לפני שהמכונה שאלה על ה-MAC שלה. כשהשרת שקט אין ממי לשאול, ולכן הקובץ
# הזה אינו יכול להיוודע שהמכונה שמולו היא מחשב שיכפול — הוא נפל
# ל-`chain_local` על שנים-עשר נתיבי EFI מול הכוננים המחוברים.
#
# מה שכן זמין לו לפני כל פנייה לשרת הוא `$grub_platform`: מי שעלה עם
# ליבת ה-i386-pc הוא מחשב Legacy BIOS, כי `server/net/dhcp.py` מוסר את
# ‏`grub/i386-pc/core.0` אך ורק ל-`client-arch 0` — וברשת הזאת מחשבי
# השיכפול הם היחידים שאין להם UEFI (#38, אפיון סעיף 4).


def _bootstrap() -> str:
    from boot.grub_menu import render_bootstrap

    return render_bootstrap(CFG)


def _function(text: str, name: str) -> str:
    """גוף הפונקציה בלבד, מ-`function <name> {` ועד הסוגר שלה."""
    start = text.index(f"function {name} {{")
    return text[start : text.index("\n}\n", start) + 3]


def _executable_body(text: str) -> str:
    """הקובץ בלי הגדרות הפונקציות ובלי הערות — מה שבאמת **רץ**.
    ‏`try_local` היא האחרונה שמוגדרת, ולכן כל מה שאחריה הוא זרימת
    הריצה."""
    end = text.index("function try_local {")
    tail = text[text.index("\n}\n", end) + 3 :]
    return "\n".join(ln for ln in tail.splitlines() if not ln.lstrip().startswith("#"))


def test_the_static_bootstrap_stops_a_legacy_machine_instead_of_chainloading():
    """הדרישה של #323. על i386-pc — כלומר מחשב שיכפול — הנפילה חייבת
    להסתיים בעצירה, לא בסריקת הכוננים. ‏`chain_local` נשאר בקובץ (הוא
    נכון ל-UEFI), אבל אין אליו מעבר מ-Legacy."""
    guard = _function(_bootstrap(), "try_local")
    assert 'if [ "$grub_platform" = "efi" ]; then' in guard, (
        "הנפילה לדיסק אינה נבדלת בין UEFI ל-Legacy — זו הפרצה של #323"
    )
    efi = guard.index('if [ "$grub_platform" = "efi" ]; then')
    assert efi < guard.index("chain_local"), "chain_local לפני הבדיקה = הפרצה"
    after = guard[guard.index("\n    fi\n", efi) :]
    assert "chain_local" not in after, "המסלול של Legacy חוזר אל chain_local"
    assert "chainloader" not in after
    # ‏#345: העצירה הייתה `halt`, וזה כיבוי ולא עצירה. הדרישה של #323 היא
    # שהמכונה לא תמשיך אל הכוננים — לא שהיא תיכבה — ולכן מה שנאכף כאן הוא
    # שהמסלול מסתיים בהמתנה שאין ממנה יציאה.
    assert "sleep --interruptible" in after and "while " in after, (
        "מחשב שיכפול שלא ניתן להעלות חייב לעצור"
    )


def test_every_fallback_in_the_static_bootstrap_goes_through_the_guard():
    """הפרצה לא הייתה בהגדרה אלא ב**קריאה**: הקובץ קרא ל-`chain_local`
    ישירות בשתי נקודות. אף אחת מהן לא ידעה מול מי היא עומדת."""
    body = _executable_body(_bootstrap())
    assert "try_local" in body
    assert "chain_local" not in body, "קריאה ישירה ל-chain_local עוקפת את השומר"


def test_the_static_bootstrap_still_boots_a_uefi_station_from_its_disk():
    """בקרה על הכיוון ההפוך — עיקרון 1 לא זז. תחנת כיתה ומחשב בנייה הם
    UEFI, ולהם הנפילה לדיסק המקומי **נשארת** בדיוק כפי שהייתה."""
    text = _bootstrap()
    guard = _function(text, "try_local")
    efi = guard[guard.index('if [ "$grub_platform" = "efi" ]; then') :]
    assert "chain_local" in efi[: efi.index("\n    fi\n")]
    # ורשימת הנתיבים עצמה לא נגעה.
    assert "/EFI/Microsoft/Boot/bootmgfw.efi" in _function(text, "chain_local")


def test_the_local_disk_menu_entry_goes_through_the_guard_too():
    """הערך שהמפעיל בוחר ידנית מוביל לאותו מקום — שומר אחד, לא שניים."""
    entry = render(answer(role="classroom"), CFG)
    entry = entry[entry.index('--id local {') :]
    assert "try_local" in entry[: entry.index("\n}")]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(render(answer(role="classroom"), CFG), id="dynamic-menu"),
        pytest.param(render(answer(role="build"), CFG), id="dynamic-build"),
        pytest.param(render(answer(known=False), CFG), id="dynamic-unregistered"),
        pytest.param(render_local_only("bad-mac"), id="local-only"),
    ],
)
def test_no_file_calls_the_guard_without_defining_it(text):
    """‏`try_local` נפלטת רק כשנפלטת גם `chain_local`. קובץ שקורא לה
    בלעדיה הוא "unknown command" מול מכונה — כלומר מסך שחור."""
    if "try_local" in text:
        assert "function try_local {" in text
        assert "function chain_local {" in text
