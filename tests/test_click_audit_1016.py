"""‏ביקורת לחיצות 16/10 (#1016): שגיאות מערכת באנגלית לא מגיעות למסך, ומחיקת
כיתה עם מחשבים אינה 500.

העיקרון: הטקסט הגולמי (``ReadError('not a gzip file')``, ‏``[WinError 10061]``,
‏``systemctl … [WinError 2]``) הולך ללוג בלבד; למשתמש — עברית, והכישלון נשאר
כישלון גלוי (עיקרון 5: אותו סטטוס, לא 200).
"""

from __future__ import annotations

import re
import socket

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom

RAW = re.compile(r"ReadError|gzip|WinError|refused|No such file|systemctl .*\[|Errno", re.I)


def _hebrew(text: str) -> bool:
    return bool(re.search(r"[֐-׿]", text))


# --- 1א: העלאת tar פגום -----------------------------------------------------


def test_bad_tar_upload_is_hebrew_not_python_repr(server):
    response = server["admin"].post("/api/console/images/upload", content=b"not a tar" * 200)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert _hebrew(detail) and "ארכיון" in detail
    assert not RAW.search(detail), detail


def test_bad_tar_upload_logs_raw_text(server, caplog):
    caplog.set_level("WARNING", logger="imagesrv.archive")
    server["admin"].post("/api/console/images/upload", content=b"not a tar" * 200)
    assert any("archive unreadable" in r.getMessage() for r in caplog.records)


# --- 1ב: רישום משני לכתובת שלא עונה -----------------------------------------


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_enroll_preview_unreachable_secondary_is_hebrew(server):
    port = _closed_port()
    response = server["admin"].post("/api/console/storage-nodes/enroll/preview",
                                    json={"url": f"https://127.0.0.1:{port}"})
    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    assert _hebrew(detail) and "שרת המשנה לא עונה" in detail and str(port) in detail
    assert not RAW.search(detail), detail


# --- 1ג: systemctl / כתיבת קובץ (הוספת מחשב, שירותי NIC) --------------------


def test_systemctl_oserror_is_hebrew_and_logged(monkeypatch, caplog):
    from server.net import dhcp_host

    def boom(*a, **k):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")
    monkeypatch.setattr(dhcp_host.subprocess, "run", boom)
    caplog.set_level("WARNING", logger="server.net.dhcp_host")
    error = dhcp_host._systemctl("restart", "dnsmasq")
    assert error and _hebrew(error) and "dnsmasq" in error and not RAW.search(error), error
    assert any("WinError 2" in r.getMessage() for r in caplog.records)   # הגולמי בלוג


def test_systemctl_nonzero_stderr_stays_in_log(monkeypatch):
    from server.net import dhcp_host

    class Done:
        returncode, stdout, stderr = 1, "", "Failed to restart dnsmasq.service: Unit not found."
    monkeypatch.setattr(dhcp_host.subprocess, "run", lambda *a, **k: Done())
    error = dhcp_host._systemctl("restart", "dnsmasq")
    assert error and _hebrew(error) and "Unit not found" not in error


def test_write_failure_is_hebrew(tmp_path):
    from server.net import dhcp_host
    blocker = tmp_path / "file"
    blocker.write_text("x")
    error = dhcp_host._write("x", blocker / "sub" / "conf")   # אב שהוא קובץ -> OSError
    assert error and _hebrew(error) and not RAW.search(error), error
    assert "ביומן השרת" in error          # הפרטים הגולמיים — בלוג, לא במסך


def test_deploy_net_run_oserror_is_hebrew(monkeypatch):
    from server.net import deploy_net

    def boom(*a, **k):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")
    monkeypatch.setattr(deploy_net.subprocess, "run", boom)
    ok, _out, err = deploy_net._run(["systemctl", "enable", "dnsmasq"])
    assert not ok and _hebrew(err) and not RAW.search(err), err


# --- 4: מחיקת כיתה עם מחשבים ------------------------------------------------


def test_delete_class_with_machines_is_409_in_hebrew_and_changes_nothing(server):
    admin = server["admin"]
    setup_classroom(server)
    response = admin.delete("/api/console/groups/grp_LAB1")
    assert response.status_code == 409, response.text      # לא 500 (FOREIGN KEY)
    assert "2 מחשבים" in response.json()["detail"]
    # בלי שאריות: הכיתה והמחשבים שלה כמות שהיו, והיומן לא מתעד מחיקה.
    assert any(g["id"] == "grp_LAB1" for g in admin.get("/api/console/groups").json())
    assert len(admin.get("/api/console/machines?group=grp_LAB1").json()) == 2
    journal = [row["event"] for row in admin.get("/api/console/journal").json()]
    assert "group_delete" not in journal


def test_delete_class_after_machines_removed_works(server):
    admin = server["admin"]
    setup_classroom(server)
    for m in admin.get("/api/console/machines?group=grp_LAB1").json():
        assert admin.delete(f"/api/console/machines/{m['mac']}").status_code == 200
    assert admin.delete("/api/console/groups/grp_LAB1").status_code == 200
