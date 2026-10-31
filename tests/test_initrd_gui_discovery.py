"""‏#32 — הקצה שמחליט אם יש בכלל initramfs גרפי, ומה שהתחנה מקבלת בפועל.

‏`boot/grub_menu.py` טהור ואינו נוגע בדיסק: הוא מקבל נתיב או `None`.
מי שמחליט מה מהשניים הוא `boot/http.py`, והוא מחליט לפי **ראיה חיובית**
בלבד — ‏`is_file()` ענה True. כל היתר מחזיר `None`.

הכשל שהקובץ הזה מונע הוא הגרוע ביותר של #32: התפריט מפנה את GRUB
ל-`/boot/initrd.img.gui`, השרת מחזיר 404, ו**המכונה לא עולה בכלל** —
לא לגואי, לא לטקסט, ולא לדיסק. מכונה בלי גואי עובדת. זו לא.

הבדיקות התחתונות רצות דרך האפליקציה האמיתית ומודדות את התשובה של
‏`/boot/menu`, לא ערך פנימי.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import GrubConfig
from boot.http import GUI_INITRD_NAME, config_for_scope, gui_initrd_path

SERVED = f"/boot/{GUI_INITRD_NAME}"


# --- הגילוי: קובץ קיים או שלא -------------------------------------------------


def test_the_gui_path_is_offered_only_when_the_file_is_there(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "initrd.img").write_bytes(b"text initramfs")

    assert gui_initrd_path(boot) is None, "אין קובץ גרפי — אין נתיב"

    (boot / GUI_INITRD_NAME).write_bytes(b"gui initramfs")
    assert gui_initrd_path(boot) == SERVED


def test_no_boot_dir_at_all_means_no_gui():
    """שרת פיתוח בלי `--boot-dir`. ‏None נכנס, ‏None יוצא — בלי חריגה."""
    assert gui_initrd_path(None) is None


def test_a_missing_boot_dir_means_no_gui(tmp_path):
    assert gui_initrd_path(tmp_path / "does-not-exist") is None


def test_a_directory_named_like_the_gui_initramfs_is_not_a_file(tmp_path):
    """‏`exists()` היה עונה True כאן. ‏`is_file()` הוא הבדיקה הנכונה:
    תיקייה בשם הזה אינה initramfs, ו-GRUB היה מקבל עליה 404."""
    boot = tmp_path / "boot"
    (boot / GUI_INITRD_NAME).mkdir(parents=True)
    assert gui_initrd_path(boot) is None


def test_an_unusable_path_means_no_gui_and_does_not_raise():
    """הבדיקה עצמה יכולה להיכשל — נתיב פגום, הרשאה חסרה. ‏"לא הצלחנו
    לבדוק" נספר כ"אין", לא כ"יש" (עיקרון 5)."""
    assert gui_initrd_path("\0/bad/path") is None


# --- ההעברה הלאה: הנתיב שורד את בניית התצורה מחדש ------------------------------


def test_config_for_scope_keeps_the_gui_path(tmp_path):
    """‏`config_for_scope` בונה `GrubConfig` **חדש** לכל בקשה שהגיעה
    לכתובת אחרת (#39), שדה-שדה. שדה שנשכח שם נעלם בשקט — ותחנה שעלתה
    דרך proxy בווילן זר (תרחיש 3) הייתה מקבלת טקסט בזמן שהזהה לה
    בווילן ההפצה מקבלת גואי. אותה מכונה, שני מסכים שונים.

    הכשל כשמסירים את `gui_initrd_path=` מ-`config_for_scope`:

        AssertionError: assert None == '/boot/initrd.img.gui'
    """
    base = GrubConfig(server_base="http://10.44.12.10:8080",
                      gui_initrd_path=SERVED)
    scoped = config_for_scope({"server": ("10.44.99.10", 8080)}, base)

    assert scoped.server_base == "http://10.44.99.10:8080", "השדה שזה בשבילו"
    assert scoped.gui_initrd_path == SERVED
    assert scoped.initrd_path == base.initrd_path


# --- דרך האפליקציה האמיתית ----------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402

MAC = "b4:2e:99:00:00:01"


def make_client(tmp_path, *, with_gui: bool):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "vmlinuz").write_bytes(b"fake-kernel")
    (boot / "initrd.img").write_bytes(b"fake-initramfs")
    if with_gui:
        (boot / GUI_INITRD_NAME).write_bytes(b"fake-gui-initramfs")
    app = create_app(tmp_path / "data", tmp_path / "images",
                     "http://127.0.0.1:8080", boot_dir=boot)
    return TestClient(app)


def test_an_unregistered_mac_never_gets_the_gui_even_when_it_is_installed(tmp_path):
    """‏MAC לא רשום = דיסק מקומי מיד, בלי ImageSrv ובלי initrd כלשהו
    (עיקרון 1). הקובץ הגרפי קיים כאן דווקא כדי שהבדיקה תהיה אמיתית."""
    client = make_client(tmp_path, with_gui=True)
    text = client.get(f"/boot/menu?mac={MAC}").text
    assert GUI_INITRD_NAME not in text
    assert "initrd " not in text
    assert "--id imagesrv" not in text


def test_a_malformed_mac_never_gets_the_gui(tmp_path):
    client = make_client(tmp_path, with_gui=True)
    text = client.get("/boot/menu?mac=not-a-mac").text
    assert GUI_INITRD_NAME not in text


def test_the_gui_initramfs_is_actually_served_when_present(tmp_path):
    """הנתיב שהתפריט מפנה אליו חייב להחזיר 200. אילו הוא היה מוחזר
    404, כל מכונה עם מסך הייתה נתקעת ב-GRUB — וזה בדיוק מה שהופך
    "פיצ'ר שלא עבד" ל"כיתה שלא עולה"."""
    client = make_client(tmp_path, with_gui=True)
    resp = client.get(f"/boot/{GUI_INITRD_NAME}")
    assert resp.status_code == 200
    assert resp.content == b"fake-gui-initramfs"


def test_without_the_file_the_server_offers_no_gui_path_at_all(tmp_path):
    """בקרה שלילית על ההגשה: בלי הקובץ, גם הנתיב לא קיים — ולכן חייב
    להיות בטוח שהתפריט לא מפנה אליו. ‏404 כאן הוא התוצאה הנכונה."""
    client = make_client(tmp_path, with_gui=False)
    assert client.get(f"/boot/{GUI_INITRD_NAME}").status_code == 404


# --- החוליה שסוגרת את המעגל: השרת באמת מחבר את הגילוי לתצורה -------------------
#
# בלי הבדיקות האלה אפשר היה למחוק את `gui_initrd_path=gui_initrd_path(boot_dir)`
# מ-`server/app.py` — כלומר לכבות את הפיצ'ר כולו, לכל מכונה — וכל 66 הבדיקות
# האחרות היו עוברות. נמדד: מוטציה שהסירה את השורה עברה 66/66. שוב אותו דפוס
# של #320 בשכבה אחרת: היחידות נכונות, והמערכת המורכבת מהן לא עושה דבר.

CLASSROOM_MAC = "b4:2e:99:07:1a:c4"
CLONER_MAC = "b4:2e:99:07:1a:d1"


def registered_server(tmp_path, *, with_gui: bool):
    """אפליקציה אמיתית עם תיקיית אתחול, קבוצת כיתה וקבוצת שיכפול רשומות."""
    from server.console import users

    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "vmlinuz").write_bytes(b"fake-kernel")
    (boot / "initrd.img").write_bytes(b"fake-initramfs")
    if with_gui:
        (boot / GUI_INITRD_NAME).write_bytes(b"fake-gui-initramfs")

    images = tmp_path / "images"
    images.mkdir()
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080",
                     boot_dir=boot)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    assert admin.post("/api/console/login",
                      json={"username": "noc",
                            "password": "admin-pass-123"}).status_code == 200

    for gid, label, role, line in (
        ("grp_LAB1", "כיתה LAB1", "classroom", f"{CLASSROOM_MAC} 05"),
        ("grp_CLONE", "חדר שיכפולים", "cloner", f"{CLONER_MAC} CLONER1"),
    ):
        assert admin.post("/api/console/groups",
                          json={"id": gid, "label": label,
                                "role": role}).status_code == 200
        saved = admin.post("/api/console/machines/import",
                           json={"group_id": gid, "text": line + "\n"}).json()
        assert saved["saved"] == 1 and not saved["rejected"], saved

    return TestClient(app)


def test_a_registered_classroom_station_really_gets_the_gui_from_the_server(tmp_path, classrooms_on):
    """הדרישה מקצה לקצה: ‏GRUB שואל, והשרת עונה בנתיב הגרפי.

    הכשל כשמסירים את החיווט מ-`server/app.py`:

        AssertionError: assert 'initrd.img.gui' in <menu text>
    """
    client = registered_server(tmp_path, with_gui=True)
    text = client.get(f"/boot/menu?mac={CLASSROOM_MAC}").text
    assert "--id imagesrv" in text, "התחנה רשומה, ולכן יש לה ערך ImageSrv"
    assert f"initrd (http,10.44.12.10:8080)/boot/{GUI_INITRD_NAME}" in text


def test_a_registered_cloner_really_gets_the_gui_initramfs_from_the_server(tmp_path, classrooms_on):
    """מאז שמחשב השיכפול קיבל מסך (#679, הכרעת הבעלים), הוא ב-
    ‏ROLES_WITH_GUI — ולכן אותו שרת, אותו רגע, מחזיר לו את ה-initramfs
    הגרפי, בדיוק כמו לתחנת כיתה. זה הטסט שקידד את ההחלטה **הישנה**
    שהופכה: קודם הוא אימת שהקלונר מקבל טקסט.

    ‏#17 אינו נפגע — הבחירה כאן היא בשורת ה-initrd בלבד; הקלונר נשאר
    diskless (נבדק ב-test_cloner_boot_path.py)."""
    client = registered_server(tmp_path, with_gui=True)
    text = client.get(f"/boot/menu?mac={CLONER_MAC}").text
    assert "--id imagesrv" in text
    assert f"initrd (http,10.44.12.10:8080)/boot/{GUI_INITRD_NAME}" in text
    assert "initrd (http,10.44.12.10:8080)/boot/initrd.img\n" not in text


def test_without_the_gui_file_the_classroom_station_falls_back(tmp_path, classrooms_on):
    """אותה תחנה בדיוק, על שרת שלא נבנה בו initramfs גרפי. היא עולה —
    בטקסט. זו ההתנהגות שמפרידה בין "בלי גואי" לבין "לא עולה"."""
    client = registered_server(tmp_path, with_gui=False)
    text = client.get(f"/boot/menu?mac={CLASSROOM_MAC}").text
    assert "initrd (http,10.44.12.10:8080)/boot/initrd.img\n" in text
    assert GUI_INITRD_NAME not in text
