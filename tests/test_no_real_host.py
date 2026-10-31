"""השומר על קריאת המכונה האמיתית — ובעיקר: הראיה שהוא בכלל מותקן (‏#113).

‏`hostguard.block_real_host_reads()` רץ ב-`pytest_configure`, כלומר לפני
כל בדיקה בריצה הזאת. אבל שומר שלא תפס כלום נראה **בדיוק** כמו שומר
שאין מה לתפוס, וזה עיקרון 5 בגרסת כלי הבדיקה: אם היה מסתפקים ב"אף
בדיקה לא נכשלה", אז סריקה שהחמיצה את כל אתרי הקישור הייתה נחשבת
הצלחה. לכן כאן נבדקת ההתקנה עצמה, בראיה חיובית:

* היא תפסה את האתרים שידוע שקיימים;
* אחרי ההתקנה **לא נשאר** אתר עם הפונקציה המקורית — קריאה חוזרת, לא
  הנחה;
* השומר באמת זורק כשקוראים לו בלי שורש, ובאמת עובד כשמזריקים שורש.

הבדיקה הזאת היא מה שהופך את "‏931 עברו" למשפט על הקוד ולא על המכונה
שהריצה עליה.
"""

from __future__ import annotations

import pytest

import hostguard

#: אתרי הקישור שקיימים בקוד היום. ‏`console_dhcp`, ‏`console_netcfg`
#: ו-`health` מרכיבים את ה-hook בזמן קריאה (‏`dhcp.list_interfaces`
#: בתוך גוף `default_hooks`), ולכן הם נפתרים דרך שני השמות האלה.
KNOWN_SITES = {"server.net.dhcp.list_interfaces",
               "server.net.dhcp_host.list_interfaces"}


def test_the_guard_is_installed_and_says_where():
    """‏[] כאן אינו "אין בעיה" — הוא "הסריקה לא מצאה כלום"."""
    sites = set(hostguard.patched_sites)
    assert sites, ("השומר לא תפס אף אתר קישור — הסריקה לא עבדה, "
                   "והריצה הזאת אינה מוגנת בכלל")
    missing = KNOWN_SITES - sites
    assert not missing, f"אתרי קישור ידועים שלא נתפסו: {sorted(missing)}"


def test_every_module_in_the_package_was_scanned():
    """מודול שלא ניתן לייבא הוא חור בשומר, לא הערה.

    ‏`unguarded_sites()` סורק שוב **אחרי** ההתקנה: זו הקריאה החוזרת
    שמכריעה, ולא העובדה שההתקנה לא זרקה.
    """
    assert hostguard.unimportable_modules == [], (
        "מודולים בחבילת server לא יובאו ולכן לא נסרקו: "
        f"{hostguard.unimportable_modules}")
    assert hostguard.unguarded_sites() == []


def test_reading_the_machine_fails_loudly_and_names_the_fix():
    """ברירת המחדל — ‏`/sys/class/net` האמיתי — נכשלת, ובשם."""
    from server.net import dhcp

    before = len(hostguard.blocked_host_reads)
    with pytest.raises(AssertionError) as caught:
        dhcp.list_interfaces()
    assert "interfaces" in str(caught.value)
    assert "dhcp_hooks" in str(caught.value)
    assert len(hostguard.blocked_host_reads) == before + 1


def test_an_injected_root_still_reads_normally(tmp_path):
    """השומר חוסם את המכונה, לא את הפונקציה: שורש מוזרק עובד כרגיל —
    אחרת הבדיקה של `list_interfaces` עצמה הייתה נעלמת יחד עם הבאג."""
    from server.net import dhcp

    for name, state in (("lo", "unknown"), ("eth0", "up")):
        card = tmp_path / name
        card.mkdir()
        (card / "operstate").write_text(state)
        (card / "address").write_text("b4:2e:99:07:1a:c4\n")
    found = dhcp.list_interfaces(tmp_path)
    assert [n["name"] for n in found] == ["eth0"]


def test_the_console_apps_never_fall_back_to_the_machine():
    """הראיה מהצד של ה-HTTP: כל ראוטר שיש לו hook בשם `interfaces`
    מקבל אותו מ-`default_hooks()`, ובריצת בדיקות ברירת המחדל הזאת
    היא השומר. ‏endpoint שלא הוזרק לו hook ייכשל — ולא יקרא מכונה."""
    from server.console import console_dhcp, console_netcfg
    from server.system import health

    for module in (console_dhcp, console_netcfg, health):
        hook = module.default_hooks()["interfaces"]
        assert hook is hostguard._guarded_list_interfaces, (
            f"{module.__name__}.default_hooks()['interfaces'] אינו מוגן")
