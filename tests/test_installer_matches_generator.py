"""שומר על סקריפט ההתקנה מסונכרן עם המחולל.

סקריפט ההתקנה הוא קובץ אחד עצמאי, ולכן הוא נושא עותק מוטמע של קובץ
ה-GRUB הקבוע. עותק שני פירושו סיכון לסטייה: מישהו יתקן באג ב-Python,
ישכח את ה-bash, ואז שרת חדש יקבל את הגרסה הישנה.

הבדיקות כאן הופכות את הסטייה הזו לכשל בבנייה במקום להפתעה בהתקנה.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from boot.grub_menu import GrubConfig, render_bootstrap

INSTALLER = Path(__file__).resolve().parent.parent / "deploy" / "setup-boot-server.sh"
PLACEHOLDER = "@@GRUB_HOST@@"
PORT_PLACEHOLDER = "@@GRUB_PORT@@"
HOST = "10.44.12.10:8080"
PORT = "8080"


def embedded_bootstrap() -> str:
    """שולף את קובץ ה-GRUB המוטמע מתוך ה-heredoc בסקריפט ההתקנה."""
    text = INSTALLER.read_text(encoding="utf-8")
    # שורת ה-heredoc נגמרת ב-"|| true", ולכן חייבים להרשות זנב אחרי הסמן.
    match = re.search(
        r"<<'GRUBCFG'[^\n]*\n(.*?)\nGRUBCFG\b", text, flags=re.DOTALL
    )
    assert match, "לא נמצא heredoc בשם GRUBCFG בסקריפט ההתקנה"
    return match.group(1)


def test_the_installer_carries_the_same_bootstrap_as_the_generator():
    from_python = render_bootstrap(GrubConfig(server_base=f"http://{HOST}"))
    from_bash = (
        embedded_bootstrap()
        .replace(PLACEHOLDER, HOST)
        .replace(PORT_PLACEHOLDER, PORT)
    ) + "\n"

    assert from_bash == from_python, (
        "העותק המוטמע ב-deploy/setup-boot-server.sh סטה מ-render_bootstrap().\n"
        "הרץ:  python tools/render_bootstrap.py http://" + HOST + "\n"
        "והחלף את גוף ה-heredoc GRUBCFG, כשהכתובת מוחלפת בחזרה ב-"
        + PLACEHOLDER
    )


def test_the_placeholder_is_actually_present():
    """בלי הפלייסהולדר ההתקנה תכתוב כתובת קשיחה של מישהו אחר."""
    assert PLACEHOLDER in embedded_bootstrap()


def test_the_installer_substitutes_both_placeholders():
    """‏@@GRUB_PORT@@ נוסף אחרי #37. פלייסהולדר שלא מוחלף נשאר כטקסט
    בקובץ שה-GRUB קורא, והתחנה פונה לכתובת שאינה כתובת."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert PORT_PLACEHOLDER in embedded_bootstrap()
    for name in (PLACEHOLDER, PORT_PLACEHOLDER):
        assert f"//{name}/" in text, f"אין החלפה של {name} בסקריפט"


def test_the_installer_still_works_without_the_repo():
    """מעתיקים את הקובץ לשרת חדש ומריצים: קובץ ה-GRUB מוטמע, והקוד
    נמשך מ-git כשהריפו לא לצידו."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "render_bootstrap.py" not in text
    assert "git clone" in text


def test_the_installer_refuses_https():
    """ה-GRUB החתום נבנה בלי TLS. עדיף להיכשל בהתקנה מאשר מול כיתה."""
    assert "https לא נתמך" in INSTALLER.read_text(encoding="utf-8")


@pytest.mark.parametrize("arch", ["client-arch,7", "client-arch,9"])
def test_both_uefi_client_arch_values_are_matched(arch):
    """קושחות שונות שולחות 7 או 9. מי שמגדיר רק אחד מהם מגלה את זה
    מול מחשב בודד שלא עולה בזמן שכל השאר עובדים.

    השורות עברו מהמתקין למחולל של הקונסולה — DHCP מוגדר משם."""
    dhcp = INSTALLER.parent.parent / "server" / "net" / "dhcp.py"
    assert arch in dhcp.read_text(encoding="utf-8")


def test_shim_chains_to_the_exact_filename_it_looks_for():
    """shim מחפש grubx64.efi באותה תיקייה, קשיח. שינוי שם שובר הכל."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "$TFTP_ROOT/bootx64.efi" in text
    assert "$TFTP_ROOT/grubx64.efi" in text


def test_the_installer_never_enables_dhcp():
    """DHCP בהתקנה היה מקור לטעות המסוכנת ביותר. היום הוא מוגדר רק
    מהקונסולה, מאחורי שכבות הבטיחות של סעיף 24 — המתקין מרים TFTP בלבד."""
    text = INSTALLER.read_text(encoding="utf-8")
    # "=" מבדיל שורת תצורה מהדגל הישן --dhcp-range, שנשאר רק כדי להסביר שבוטל.
    assert "dhcp-range=" not in text
    assert "pxe-service" not in text
    assert re.search(r"^port=0$", text, re.M)


def test_the_installer_no_longer_writes_enable_tftp():
    """‏#1013: enable-tftp/tftp-root עברו לקובץ שהשרת מרנדר (dhcp.render),
    כי dnsmasq קורא את /etc/dnsmasq.d במצטבר — שורה פעילה בקובץ המתקין
    הייתה הופכת את המתג של 69 בקונסולה ל"כבוי" מזויף. הערה מותרת; שורה
    פעילה לא. ‏--tftp-root נשאר: הוא נתיב ההעתקה של shim/GRUB ומועבר לשרת."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert not re.search(r"^enable-tftp\s*$", text, re.M)
    assert not re.search(r"^tftp-root=", text, re.M)
    assert "--tftp-root" in text


def test_the_storage_args_environment_is_quoted():
    """‏STORAGE_ARGS מכיל רווחים (--storage-role X --primary-url Y). ‏Environment=
    בלי גרשיים = systemd מפצל למילים וקולט רק ``IMAGESRV_STORAGE_ARGS=--storage-role``
    → כל התקנת secondary קורסת "expected one argument". חייב גרשיים סביב הערך."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'Environment="IMAGESRV_STORAGE_ARGS=$STORAGE_ARGS"' in text
    assert "Environment=IMAGESRV_STORAGE_ARGS=$STORAGE_ARGS" not in text
