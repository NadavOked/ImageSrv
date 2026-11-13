"""הגשת קבצי האתחול של הסוכן — הפער שמעבדת ה-VM חשפה (issue #12).

המתקין מניח vmlinuz ו-initrd.img ב-HTTP_ROOT והתפריט מפנה אליהם;
השרת חייב להגיש אותם תחת /boot, עם Content-Length באותיות גדולות — GRUB
לא מזהה content-length באותיות קטנות של uvicorn, וקריאה-עד-EOF על חיבור
פתוח נתקעת עד timeout. **השרת אינו סוגר את החיבור ראשון** (#1423): GRUB
יודע איפה הקובץ נגמר לפי Content-Length וסוגר בעצמו, ו-TIME_WAIT בצד
השרת היה מה שתקע אתחול חוזר מהיר.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.app import create_app


def make_client(tmp_path, with_boot=True):
    boot = tmp_path / "boot"
    if with_boot:
        boot.mkdir()
        (boot / "vmlinuz").write_bytes(b"fake-kernel")
        (boot / "initrd.img").write_bytes(b"fake-initramfs")
    app = create_app(tmp_path / "data", tmp_path / "images",
                     "http://127.0.0.1:8080", boot_dir=boot)
    return TestClient(app)


def test_boot_files_are_served(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/boot/vmlinuz")
    assert response.status_code == 200
    assert response.content == b"fake-kernel"
    assert response.headers["Content-Length"] == str(len(response.content))
    assert "connection" not in response.headers


def test_boot_menu_still_wins_over_the_mount(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/boot/menu?mac=aa:bb:cc:dd:ee:ff")
    assert response.status_code == 200
    assert b"chain_local" in response.content
    # Content-Length, לא chunked: GRUB של EFI עם TPM דוחה קובץ שגודלו לא
    # ידוע ב-open (#1518, tests/test_boot_size_known_at_open_1518.py).
    assert response.headers["Content-Length"] == str(len(response.content))
    assert "connection" not in response.headers


def test_missing_boot_dir_is_not_fatal(tmp_path):
    client = make_client(tmp_path, with_boot=False)
    response = client.get("/boot/menu?mac=aa:bb:cc:dd:ee:ff")
    assert response.status_code == 200
    assert client.get("/boot/vmlinuz").status_code == 404
