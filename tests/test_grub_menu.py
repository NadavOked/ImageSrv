"""בדיקות למחולל ה-grub.cfg.

הבדיקה החשובה כאן היא test_agent_config_leaks_nothing_from_the_answer:
היא מוודאת שהקובץ שמוגש ל-GRUB לא נושא שום פרט מהמשימה. הרגע שבו מזהה
סבב או image_id ידלוף לשורת הפקודה של הקרנל הוא הרגע שבו נוצר ממשק שני,
לא מתועד, שיכול לסתור את ממשק 3.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import (
    AGENT,
    LOCAL,
    GrubConfig,
    decide,
    normalize_mac,
    render,
    render_bootstrap,
    render_local_only,
    LOCAL_BOOT_PATHS,
)

CFG = GrubConfig(server_base="http://10.44.12.10:8080")


# --- תשובת שרת מלאה, מועתקת מסעיף 3 בממשקים ---------------------------------


def answer(**overrides) -> dict:
    base = {
        "schema": 1,
        "known": True,
        "role": "classroom",
        "group": {"id": "grp_LAB1", "label": "כיתה LAB1 חומרה", "suffix": "05"},
        "task": None,
        "session": None,
        "allowed_images": ["img_7f3a91", "img_2c8e04"],
        "ui": {"language": "he", "require_login": False},
    }
    base.update(overrides)
    return base


OPEN_SESSION = {
    "id": "ses_a91f",
    "state": "open",
    "image_id": "img_7f3a91",
    "prefix": "LAB1",
    "expected_clients": 30,
    "joined": 11,
    "starts_in_seconds": 134,
}


# --- normalize_mac ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "b4:2e:99:07:1a:c4",
        "B4:2E:99:07:1A:C4",
        "b4-2e-99-07-1a-c4",
        "B4-2E-99-07-1A-C4",
        "b42e99071ac4",
        "B42E99071AC4",
        "b42e.9907.1ac4",
    ],
)
def test_normalize_mac_accepts_every_variation_in_the_spec(raw):
    assert normalize_mac(raw) == "b4:2e:99:07:1a:c4"


@pytest.mark.parametrize(
    "raw", ["", "not-a-mac", "b4:2e:99:07:1a", "b4:2e:99:07:1a:c4:d5", None, 42, ["b4"]]
)
def test_normalize_mac_rejects_garbage(raw):
    assert normalize_mac(raw) is None


# --- טבלת ההחלטה ------------------------------------------------------------


def test_unknown_mac_boots_locally_and_offers_nothing():
    d = decide(answer(known=False))
    assert d.action == LOCAL
    assert d.offer_recovery is False
    assert d.show_menu is False


def test_missing_known_field_is_treated_as_unknown():
    a = answer()
    del a["known"]
    assert decide(a).action == LOCAL


def test_known_must_be_exactly_true():
    for value in ["true", 1, {}, None]:
        assert decide(answer(known=value)).action == LOCAL


def test_no_task_and_no_session_auto_boots_imagesrv_for_a_gui_role():
    """‏#641 (היפוך #140): תחנת כיתה רשומה בלי משימה עולה ישר ל-ImageSrv,
    אוטומטי — במקום התפריט הגלוי שהמתין לאדם."""
    d = decide(answer(task=None, session=None))
    assert d.action == AGENT
    assert d.show_menu is False


def test_no_task_still_reaches_recovery_on_a_classroom_station():
    """הצוהר לשחזור נשאר — אבל עכשיו הוא אוטומטי: תחנת כיתה בלי משימה
    עולה ישר ל-ImageSrv במצב recovery (#144, #641), בלי לעבור בקונסולה
    ובלי תפריט."""
    d = decide(answer(task=None, session=None))
    assert d.action == AGENT
    assert d.recovery_mode is True
    assert "imagesrv.mode=recovery" in render(answer(), CFG)


def test_build_machine_with_no_task_auto_boots_the_build_console():
    """‏#641: מחשב הבנייה בלי משימה עולה ישר ל-ImageSrv (הערך הרגיל —
    build_console, #29), אוטומטי, בלי תפריט שממתין לאדם."""
    d = decide(answer(role="build"))
    assert d.action == AGENT
    assert d.show_menu is False
    assert d.recovery_mode is False           # הערך הרגיל, לא recovery
    text = render(answer(role="build"), CFG)
    assert "set default=imagesrv" in text
    assert "set timeout_style=hidden" in text


def test_build_menu_reaches_the_build_console_not_only_recovery():
    """הדרך היחידה למסך הקליטה/הפצה מאתחול קר היא ערך סוכן בלי recovery —
    בלעדיו ערך ה-ImageSrv בתפריט היה רק צוהר השחזור (#29). מ-#144 זהו
    גם הערך היחיד שמחשב הבנייה מקבל."""
    text = render(answer(role="build"), CFG)
    assert "--id imagesrv {" in text                       # סוכן רגיל
    plain = text.split("--id imagesrv {")[1].split("}")[0]
    assert "imagesrv.mode=recovery" not in plain


def test_cloner_with_no_task_waits_on_the_agent():
    """למחשב שיכפול אין מערכת מקומית — "דיסק מקומי" הוא מבוי סתום שמכבה
    אותו. בלי סבב הוא עולה לסוכן וממתין (wait_poll), כמו בטבלת ההחלטות
    של הסוכן עצמו (#17)."""
    d = decide(answer(role="cloner"))
    assert d.action == AGENT
    assert d.code == "cloner-wait"


def test_a_task_loads_the_agent():
    assert decide(answer(task={"id": "tsk_0091"})).action == AGENT


def test_an_empty_task_object_is_not_a_task():
    """‏`{}` אינו משימה — ההחלטה נופלת למסלול no-task ולא ל-task-assigned.
    (בתחנת כיתה המסלול הזה עולה אוטומטית ל-ImageSrv מ-#641, ולכן נבדק
    ה-`code` ולא ה-action.)"""
    assert decide(answer(task={})).code == "no-task"


@pytest.mark.parametrize("state", ["open", "running"])
def test_a_joinable_session_loads_the_agent(state):
    session = dict(OPEN_SESSION, state=state)
    assert decide(answer(session=session)).action == AGENT


def test_a_closed_session_is_not_joinable():
    """סבב סגור אינו מצורף — ההחלטה נופלת ל-no-task, לא ל-session-joinable.
    (בתחנת כיתה המסלול הזה עולה אוטומטית ל-ImageSrv מ-#641.)"""
    session = dict(OPEN_SESSION, state="closed")
    assert decide(answer(session=session)).code == "no-task"


def test_an_unrecognised_session_state_is_not_joined():
    session = dict(OPEN_SESSION, state="paused")
    assert decide(answer(session=session)).code == "no-task"


def test_a_newer_contract_schema_boots_locally():
    assert decide(answer(schema=2)).action == LOCAL


def test_a_missing_schema_is_assumed_to_be_one():
    a = answer(task={"id": "tsk_1"})
    del a["schema"]
    assert decide(a).action == AGENT


def test_unknown_fields_are_ignored_not_fatal():
    a = answer(task={"id": "tsk_1"}, future_field="whatever", another=[1, 2])
    assert decide(a).action == AGENT


@pytest.mark.parametrize("junk", [None, "", [], 42, "known"])
def test_a_non_object_answer_boots_locally(junk):
    assert decide(junk).action == LOCAL


# --- רינדור -----------------------------------------------------------------


def test_a_machine_with_no_task_auto_boots_imagesrv_but_keeps_a_local_entry():
    """‏#641: ברירת המחדל עברה ל-ImageSrv, אבל הערך המקומי נשאר בקובץ
    (חבוי) — הבחירה בדיסק עוברת אל תוך מסך ה-ImageSrv, לא נעלמת."""
    text = render(answer(), CFG)
    assert "set default=imagesrv" in text
    assert "--id local {" in text
    assert "chain_local" in text
    assert "bootmgfw.efi" in text


def test_agent_config_loads_kernel_and_initrd_from_the_server():
    text = render(answer(task={"id": "tsk_1"}), CFG)
    assert "linux (http,10.44.12.10:8080)/boot/vmlinuz" in text
    assert "initrd (http,10.44.12.10:8080)/boot/initrd.img" in text
    assert "imagesrv.server=http://10.44.12.10:8080" in text


@pytest.mark.parametrize(
    "a",
    [
        answer(),
        answer(role="build"),
        answer(role="cloner"),
        answer(task={"id": "tsk_1"}),
        answer(session=OPEN_SESSION),
    ],
)
def test_every_network_entry_retries_kernel_and_initrd_three_times(a):
    text = render(a, CFG)
    entry = text.split('menuentry "ImageSrv" --id imagesrv {', 1)[1].split("\n}", 1)[0]
    assert entry.count("for attempt in 1 2 3; do") == 2
    assert 'if linux (http,10.44.12.10:8080)/boot/vmlinuz ' in entry
    # ‏`then` בשורה משלו מאז 54223f94 ("Fix GRUB retry command boundaries")
    assert 'if initrd (http,10.44.12.10:8080)/boot/initrd.img\n        then\n' in entry
    assert 'echo "Loading kernel over HTTP (attempt $attempt of 3)..."' in entry
    assert 'echo "Loading initrd over HTTP (attempt $attempt of 3)..."' in entry


def test_network_load_failure_reaches_the_existing_safe_fallback():
    classroom = render(answer(task={"id": "tsk_1"}), CFG)
    assert classroom.count('echo "Falling back after a short delay."') == 2
    assert classroom.count("sleep --interruptible 3") == 2
    assert classroom.count("        try_local") == 2

    cloner = render(answer(role="cloner", task={"id": "tsk_1"}), CFG)
    assert "try_local" not in cloner
    assert cloner.count("This Legacy BIOS cloning machine has no local system") == 2
    assert cloner.count("while [ \"$stay_on\" = \"y\" ]; do") == 2


def test_retry_keeps_the_kernel_command_line_clean_and_the_menu_ascii():
    text = render(answer(task={"id": "tsk_1"}), CFG)
    linux = next(line for line in text.splitlines() if line.strip().startswith("if linux "))
    imagesrv_args = [word for word in linux.split() if word.startswith("imagesrv.")]
    assert imagesrv_args == ["imagesrv.server=http://10.44.12.10:8080"]
    assert "imagesrv.mode=recovery" not in linux
    text.encode("ascii")

    recovery = render(answer(), CFG)
    recovery_linux = next(line for line in recovery.splitlines()
                          if line.strip().startswith("if linux "))
    recovery_args = [word for word in recovery_linux.split() if word.startswith("imagesrv.")]
    assert recovery_args == ["imagesrv.server=http://10.44.12.10:8080",
                             "imagesrv.mode=recovery"]


def test_agent_config_leaks_nothing_from_the_answer():
    """הפרטים מגיעים לסוכן מ-hello, לא מ-GRUB."""
    text = render(answer(task={"id": "tsk_0091"}, session=OPEN_SESSION), CFG)
    for secret in [
        "tsk_0091",
        "ses_a91f",
        "img_7f3a91",
        "img_2c8e04",
        "grp_LAB1",
        "LAB1",
        "classroom",
        "expected_clients",
    ]:
        assert secret not in text, f"{secret!r} leaked into the boot config"


def test_local_boot_tries_windows_then_linux_then_the_generic_path():
    """אפיון סעיף 9: מחשב Linux שלא נמצא לו bootloader הוא כשל שקט."""
    text = render_local_only("x")
    order = [text.index(p) for p in LOCAL_BOOT_PATHS]
    assert order == sorted(order), "the paths are not tried in the declared order"
    assert LOCAL_BOOT_PATHS[0] == "/EFI/Microsoft/Boot/bootmgfw.efi"
    assert LOCAL_BOOT_PATHS[-1].lower() == "/efi/boot/bootx64.efi"
    assert any("/EFI/ubuntu/" in p for p in LOCAL_BOOT_PATHS)
    assert any("/EFI/debian/" in p for p in LOCAL_BOOT_PATHS)
    # תחת Secure Boot: shim (חתום Microsoft) לפני grubx64.efi של ההפצה.
    for distro in ("ubuntu", "debian", "fedora"):
        shim = LOCAL_BOOT_PATHS.index(f"/EFI/{distro}/shimx64.efi")
        grub = LOCAL_BOOT_PATHS.index(f"/EFI/{distro}/grubx64.efi")
        assert shim < grub


def test_every_local_boot_path_is_chainloaded_not_just_searched():
    text = render_local_only("x")
    assert "for path in" in text and 'try_chain "$path"' in text
    assert 'chainloader "$1"' in text and "boot\n" in text


def test_the_local_entry_is_always_present_as_a_fallback():
    for a in [answer(), answer(task={"id": "t"}), answer(known=False)]:
        assert "--id local" in render(a, CFG)


def test_recovery_entry_carries_the_mode_flag():
    text = render(answer(), CFG)
    assert "imagesrv.mode=recovery" in text


def test_a_real_task_is_not_a_recovery_boot():
    text = render(answer(task={"id": "tsk_1"}), CFG)
    assert "imagesrv.mode=recovery" not in text


def test_unknown_mac_gets_no_agent_entry_at_all():
    text = render(answer(known=False), CFG)
    assert "--id imagesrv" not in text
    assert "vmlinuz" not in text
    assert "linux (http," not in text
    assert "set default=local" in text


def test_render_never_raises_whatever_it_is_given():
    for junk in [None, 0, "x", [1], {"known": object()}, {"schema": "x"}]:
        text = render(junk, CFG)
        assert "chain_local" in text
        assert text.endswith("\n")


def test_output_is_pure_ascii_because_grub_cannot_render_hebrew():
    a = answer(role="כיתה", group={"label": "כיתה LAB1 חומרה"})
    text = render(a, CFG)
    text.encode("ascii")  # raises if any Hebrew survived


def test_a_machine_we_do_not_serve_boots_instantly():
    """הבדיקה הזו הגנה עד #140 על "מחשב בלי משימה עולה תוך שניות", עם
    צוהר חבוי של שתי שניות. מכונה רשומה בלי משימה מקבלת מאז תפריט גלוי
    ללא טיימר, והכיסוי עבר למכונה הזרה — שם עיקרון 1 עדיין מבטיח אתחול
    מיידי. שתי השניות ירדו לאפס: אין שם תפריט ואין צוהר, ולכן לא הייתה
    להן תכלית."""
    for a in [answer(known=False), answer(schema=2)]:
        text = render(a, CFG)
        assert "set timeout=0" in text
        assert "set timeout_style=hidden" in text


def test_local_only_is_silent_and_immediate():
    text = render_local_only("whatever")
    assert "set timeout=0" in text
    assert "vmlinuz" not in text


# --- הרמה העליונה של הקובץ --------------------------------------------------
#
# עד #140 היה כאן רמז ל-ESC (‏#41): שורת echo שהודיעה על הצוהר החבוי. אין
# יותר צוהר חבוי במכונה רשומה — יש תפריט גלוי, והערכים עצמם על המסך. מה
# שהרמז נשמר בזכותו ולא נעלם איתו הוא שני הגבולות שהבדיקות שלו קיבעו:
# ששום פקודה חוסמת לא מתגנבת לרמה העליונה, ושמה שכן מודפס שם אינו נושא
# מילה מתשובת השרת. שניהם נבדקים כאן, עכשיו על כל מסלול ולא רק על אחד.


def _top_level(text: str) -> list[str]:
    """השורות שרצות מיד בטעינת הקובץ — מחוץ לכל בלוק { }.

    ההפרדה הזו היא הבדיקה עצמה: פקודה בתוך menuentry או בתוך function רצה
    רק אחרי שמישהו בחר בה, כלומר רק למי שכבר ידע. פקודה ברמה העליונה רצה
    לפני שהטיימר מתחיל, כלומר מוצגת בזמן הצוהר.
    """
    lines, depth = [], 0
    for raw in text.splitlines():
        line = raw.strip()
        if depth == 0 and line and not line.startswith("#"):
            lines.append(line)
        depth += line.count("{") - line.count("}")
    return lines


ANSWERS = [
    answer(),                                       # תחנת כיתה בלי משימה
    answer(role="build"),                           # מחשב בנייה
    answer(role="cloner"),                          # מחשב שיכפול
    answer(task={"id": "tsk_0091"}),                # משימה
    answer(session=OPEN_SESSION),                   # סבב פתוח
    answer(known=False),                            # לא רשום
    answer(schema=2),                               # schema לא מוכר
    answer(task={"id": "tsk_0091"}, boot_guard="exhausted"),   # שומר #75
]


@pytest.mark.parametrize("a", ANSWERS)
def test_nothing_blocking_runs_at_the_top_level(a):
    """הקובץ נטען לפני שהטיימר מתחיל. ‏sleep/pause/read ברמה העליונה
    היו מוסיפים המתנה שאיש לא ביקש — גם בתפריט הגלוי של #140, שבו כבר
    אין טיימר לצמצם, וגם במסלול המכונה הזרה שחייב להישאר מיידי."""
    for line in _top_level(render(a, CFG)):
        assert not line.startswith(("sleep", "pause", "read")), line


@pytest.mark.parametrize("a", ANSWERS)
def test_the_top_level_leaks_nothing_from_the_answer(a):
    """עיקרון 2 חל גם על המסך, לא רק על שורת הפקודה: מסך האתחול נראה
    בידי מי שלא אמור לדעת מה מוקצה למכונה — ומאז #140 הוא נראה **לכל
    תלמיד בכל אתחול**, ולא רק בשתי שניות חבויות."""
    for line in _top_level(render(a, CFG)):
        for secret in ["grp_LAB1", "LAB1", "classroom", "img_7f3a91",
                       "ses_a91f", "tsk_0091", "expected_clients"]:
            assert secret not in line, f"{secret!r} דלף לרמה העליונה"


def test_no_screen_still_tells_anyone_to_press_esc():
    """הרמז של #41 הוסר יחד עם הצוהר החבוי. שורה שאומרת "לחצו ESC" מול
    תפריט גלוי היא הוראה למקש שאין לו מה לחשוף."""
    for a in ANSWERS:
        assert "ESC" not in render(a, CFG)
    assert "ESC" not in render_local_only("generator-error")


def test_the_escape_hatch_file_stays_silent():
    """render_local_only הוא מסלול הכשל — אין בו סוכן ואין תפריט."""
    text = render_local_only("generator-error")
    assert "--id imagesrv" not in text
    assert "set timeout_style=hidden" in text


def test_the_boot_loop_guard_screen_also_points_at_recovery():
    """דווקא מכונה שהשומר של #75 החזיר לדיסק היא המכונה שטכנאי צריך
    להיכנס אליה. עד #140 היא הודיעה על ESC; היום ערך השחזור פשוט מוצג."""
    a = answer(task={"id": "tsk_0091"}, boot_guard="exhausted")
    decision = decide(a)
    assert decision.show_menu is True and decision.offer_recovery is True
    text = render(a, CFG)
    # מ-#144 זהו הערך היחיד בתחנת כיתה, ולכן `--id imagesrv` — והוא
    # עדיין נושא את דגל השחזור.
    assert "--id imagesrv {" in text
    assert "imagesrv.mode=recovery" in text
    assert "set timeout_style=menu" in text


# --- תצורה ------------------------------------------------------------------


def test_https_is_rejected_because_signed_grub_has_no_tls():
    with pytest.raises(ValueError, match="http"):
        GrubConfig(server_base="https://10.44.12.10:8080")


def test_a_server_base_with_no_host_is_rejected():
    with pytest.raises(ValueError):
        GrubConfig(server_base="http://")


# --- הקובץ הסטטי ------------------------------------------------------------


def test_bootstrap_fetches_the_dynamic_menu_with_the_mac():
    text = render_bootstrap(CFG)
    assert "configfile (http,$imagesrv_server)/boot/menu?mac=$net_default_mac" in text


def test_bootstrap_prefers_the_address_that_answered_dhcp():
    """תחנה שעלתה דרך PXE proxy ברשת זרה (תרחיש 3, בדיקות 3.4-3.5) לא
    מגיעה לכתובת של וילן ההפצה. ‏net_default_server היא הכתובת שענתה לה
    בפועל, ולכן היא הראשית; הפורט נלקח מהתצורה."""
    text = render_bootstrap(CFG)
    assert 'if [ -n "$net_default_server" ]; then' in text
    assert "set imagesrv_server=${net_default_server}:8080" in text


def test_bootstrap_keeps_the_configured_address_as_a_fallback():
    """next-server חסר או שגוי לא משאיר את התחנה בלי תפריט: הכתובת
    המפורשת מהתצורה נשארת, גם כברירת מחדל וגם כניסיון שני בכל סבב."""
    text = render_bootstrap(CFG)
    assert "set imagesrv_fallback=10.44.12.10:8080" in text
    assert "set imagesrv_server=$imagesrv_fallback" in text
    loop = text.split("for attempt in 1 2 3; do", 1)[1].split("done", 1)[0]
    assert "configfile (http,$imagesrv_server)/boot/menu?mac=$net_default_mac" in loop
    assert "configfile (http,$imagesrv_fallback)/boot/menu?mac=$net_default_mac" in loop
    # לא לפנות פעמיים לאותה כתובת כששתיהן זהות.
    assert '[ "$imagesrv_server" != "$imagesrv_fallback" ]' in loop


def test_bootstrap_defaults_to_port_80_when_the_address_has_none():
    text = render_bootstrap(GrubConfig(server_base="http://10.44.12.10"))
    assert "set imagesrv_server=${net_default_server}:80" in text
    assert "set imagesrv_fallback=10.44.12.10" in text


def test_bootstrap_falls_back_to_local_disk_when_the_server_is_down():
    text = render_bootstrap(CFG)
    # try_local must be both defined and called after the configfile line.
    # It is the only way in to chain_local since #323 -- a UEFI station still
    # reaches the disk, a Legacy BIOS machine (a cloner) stops instead.
    assert "function chain_local" in text
    assert "function try_local" in text
    after = text.split("configfile", 1)[1]
    assert "\ntry_local\n" in after


def test_bootstrap_is_ascii():
    render_bootstrap(CFG).encode("ascii")


# --- #61: chainloader שנדחה אינו "אין מערכת הפעלה" ---------------------------
#
# על הלנובו (Secure Boot, ‏2026-08-29) ‏bootmgfw.efi נמצא על הדיסק,
# ‏chainloader נדחה בידי shim — והמסך אמר "No operating system found".
# שני מצבים שונים שקופלו לאחד (עיקרון 5). הבדיקות כאן שומרות שהם נפרדים.


def _chain_local() -> str:
    text = render_local_only("x")
    return text[text.index("function chain_local") :]


def test_boot_runs_only_when_chainloader_actually_succeeded():
    """‏`boot` אחרי chainloader שנכשל הדפיס 'you need to load the kernel
    first' — רעש שהסתיר את השגיאה האמיתית. הצלחה נקבעת לפי ראיה חיובית."""
    text = render_local_only("x")
    try_chain = text[text.index("function try_chain") : text.index("function chain_local")]
    assert 'if chainloader "$1"; then' in try_chain
    assert try_chain.count("boot\n") == 1
    assert try_chain.index('if chainloader "$1"; then') < try_chain.index("boot\n")


def test_a_refused_loader_is_remembered_and_not_reported_as_no_os():
    chain = _chain_local()
    # הנתיב שנדחה נשמר, והמצב מאופס לפני כל סבב (משתני GRUB גלובליים,
    # ו-chain_local נקרא משתי נקודות בקובץ הקבוע).
    assert "set chain_refused=" in render_local_only("x")
    assert chain.index("unset chain_refused") < chain.index("for path in")
    # "No operating system found" נגיש רק כשלא נדחה כלום.
    refused = chain.index('if [ -n "$chain_refused" ]')
    no_os = chain.index("No operating system found")
    assert refused < chain.index("else", refused) < no_os


def test_the_refusal_screen_says_what_and_why():
    chain = _chain_local()
    refused = chain[chain.index('if [ -n "$chain_refused" ]') : chain.index("No operating system found")]
    # מה: הנתיב שנמצא ונדחה, במילים שאומרות שהדיסק כן נמצא.
    assert 'echo "  $chain_refused"' in refused
    assert "found" in refused.lower() and "refused" in refused.lower()
    # למה: Secure Boot רק כשיש ראיה — GRUB מייצא shim_lock=y בדיוק כשהוא
    # מעביר chainloader דרך shim. בלי הראיה לא טוענים שזה Secure Boot.
    assert 'if [ "$shim_lock" = "y" ]' in refused
    assert "Secure Boot" in refused and "shim" in refused
    sb = refused[refused.index('if [ "$shim_lock" = "y" ]') :]
    assert "else" in sb, "a refusal without shim_lock=y must not be blamed on Secure Boot"


def test_the_bootstrap_carries_the_same_refusal_screen():
    boot = render_bootstrap(CFG)
    assert 'if [ -n "$chain_refused" ]' in boot
    assert 'if [ "$shim_lock" = "y" ]' in boot
    boot.encode("ascii")


# --- #419: הצהרה בתפריט אינה הגדרה בקושחה ------------------------------------
#
# מחשב שיכפול 1 עלה לווינדוס מהמגירה (05/09, ושוב 15/09 — סוללת CMOS
# מתה). המסך של Legacy הבטיח "they are never booted from" — הבטחה
# שהקושחה אינה מחויבת לה. הטקסט אומר עכשיו מה התפריט עושה ומה שומר
# בפועל (סדר האתחול), במקום להבטיח בשם מי שאינו שלנו.


def _try_local() -> str:
    text = render_local_only("x")
    start = text.index("function try_local {")
    return text[start : text.index("\n}", start)]


def test_the_legacy_screen_does_not_promise_what_the_firmware_decides():
    body = _try_local()
    assert "never booted from" not in body
    assert "This menu will not start them" in body
    # מה כן שומר, ומה מבטל אותו — כדי שטכנאי מול המסך ידע לאן ללכת.
    assert "boot order" in body.lower()
    assert "BEFORE any disk" in body and "battery" in body


def test_the_bootstrap_carries_the_same_legacy_screen():
    boot = render_bootstrap(CFG)
    assert "never booted from" not in boot
    assert "This menu will not start them" in boot
