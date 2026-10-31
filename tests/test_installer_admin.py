"""‏#504 — המתקין מבטיח מנהל גם כששם המשתמש כבר קיים כ-deploy.

הבאג: `except Exception` בלע את `IntegrityError` של PRIMARY KEY, ואז
`users.update` רץ בלי `role=` — שדה שלא נשלח אינו משתנה. הסיסמה
התעדכנה, התפקיד נשאר deploy, והמתקין הדפיס את השם כמנהל. בכניסה
הראשונה כל פעולת ניהול קיבלה 403.

הטסט מריץ את בלוק ה-Python שמוטמע ב-`deploy/setup-boot-server.sh`,
לא העתק שלו: סטייה בין הסקריפט לבדיקה הייתה בדיוק החור.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pytest

from server.console import users
from server.db import connect

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"


def installer_admin_python() -> str:
    """שולף את בלוק יצירת המנהל מתוך ה-heredoc. אין עותק שני."""
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"<<PYEOF\n(.*?)\nPYEOF\b", text, flags=re.DOTALL)
    assert match, "לא נמצא heredoc PYEOF בסקריפט ההתקנה"
    return match.group(1)


def run_installer_admin(data_dir: Path, username: str, password: str,
                        monkeypatch: pytest.MonkeyPatch) -> None:
    """מריץ את בלוק המתקין מול DB אמיתי, עם החלפת נתיבי ה-bash.

    ‏#1131 ס' 4: הסיסמה מגיעה ב-**fd 3** (המתקין: `3< <(printf …)`), לא ב-env
    — כאן צינור על fd 3, ומה שישב שם קודם (pytest) מוחזר אחרי הבלוק."""
    code = installer_admin_python()
    code = code.replace("$APP_DIR", REPO.resolve().as_posix())
    code = code.replace("$DATA_DIR", data_dir.resolve().as_posix())
    monkeypatch.setenv("ADMIN_USER", username)
    monkeypatch.delenv("ADMIN_PASS", raising=False)      # הבלוק אסור שיקרא מכאן
    read_end, write_end = os.pipe()
    with os.fdopen(write_end, "w", encoding="utf-8") as w:
        w.write(password)
    saved = os.dup(3)
    os.dup2(read_end, 3)
    os.close(read_end)
    try:
        exec(compile(code, "deploy/setup-boot-server.sh", "exec"), {})
    finally:
        os.dup2(saved, 3)
        os.close(saved)


def _role(data_dir: Path, username: str, password: str) -> str | None:
    return users.verify(connect(data_dir / "imagesrv.db"), username, password)


def test_existing_deploy_user_becomes_admin(tmp_path, monkeypatch):
    """התרחיש של #504: labtech כבר deploy, המתקין רץ עם אותו שם."""
    conn = connect(tmp_path / "imagesrv.db")
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="seed", check_policy=False)
    run_installer_admin(tmp_path, "labtech", "new-admin-9", monkeypatch)
    assert _role(tmp_path, "labtech", "new-admin-9") == "admin"


def test_installer_creates_admin_on_empty_db(tmp_path, monkeypatch):
    """המסלול הרגיל — יצירה ראשונה — לא נשבר על ידי שומר השם התפוס."""
    run_installer_admin(tmp_path, "admin", "admin-pass-1", monkeypatch)
    assert _role(tmp_path, "admin", "admin-pass-1") == "admin"


def test_the_password_is_read_from_fd3_not_from_the_environment():
    """‏#1131 ס' 4: env של תהליך-ילד נראה ב-`ps e`; הבלוק קורא fd 3 בלבד."""
    code = installer_admin_python()
    assert "os.fdopen(3" in code
    assert 'environ.get("ADMIN_PASS")' not in code and 'environ["ADMIN_PASS"]' not in code
    text = INSTALLER.read_text(encoding="utf-8")
    assert "3< <(printf '%s' \"$ADMIN_PASS\")" in text
    assert 'ADMIN_PASS="$ADMIN_PASS"' not in text


def test_install_summary_distinguishes_default_and_installer_passwords():
    """#1160: the operator is told which credential path was actually used."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'if [[ -n "$ADMIN_PASS" ]]; then' in text
    assert 'משתמש $ADMIN_USER נוצר עם הסיסמה שנקבעה בהתקנה' in text
    assert 'כניסה ראשונה: $ADMIN_USER / admin — הקונסולה תדרוש החלפת סיסמה' in text
    assert "must_change_password=False" in installer_admin_python()


def test_operational_error_is_not_swallowed(tmp_path, monkeypatch):
    """‏`except Exception` היה הופך דיסק נעול ל-«משתמש לא קיים».

    תופסים IntegrityError בשמו, ולכן OperationalError עולה כמו שהוא.
    """
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(users, "create", boom)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        run_installer_admin(tmp_path, "admin", "admin-pass-1", monkeypatch)
