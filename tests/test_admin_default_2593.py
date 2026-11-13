"""#2593 — התקנה ידנית בלי סיסמה לא נופלת ל-admin/admin.

הבלוק (``admin_in_db`` + השער שאחריו) נשלף מ-``deploy/setup-boot-server.sh``
עצמו ורץ ב-bash מול DB אמיתי — לא עותק. שלושה מצבים, לא שניים: יש מנהל /
אין מנהל / לא הצלחנו לבדוק (עיקרון 5). והמסלול שנשאר: שרת קיים שמנהלו
admin/admin ממשיך להיכנס, עם החלפה כפויה.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from server.console import users
from server.db import connect

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="צריך bash")

HELPERS = (
    "say() { echo \"say: $*\"; }\n"
    "die() { echo \"die: $*\" >&2; exit 1; }\n"
    "set -euo pipefail\n"
)


def gate_block() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"^admin_in_db\(\) \{.*?^    esac\nfi\n", text, flags=re.DOTALL | re.MULTILINE)
    assert match, "לא נמצא השער של סיסמת המנהל ב-setup-boot-server.sh"
    return match.group(0)


def run_gate(data_dir: Path, admin_pass: str) -> subprocess.CompletedProcess:
    script = (HELPERS + f'DATA_DIR="{data_dir.as_posix()}"\nADMIN_PASS="{admin_pass}"\n'
              + gate_block() + 'echo REACHED_AFTER_GATE\n')
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60,
                          stdin=subprocess.DEVNULL)


def test_fresh_install_without_a_password_stops(tmp_path):
    got = run_gate(tmp_path, "")
    assert got.returncode == 1, got
    assert "REACHED_AFTER_GATE" not in got.stdout
    assert "דורשת סיסמה" in got.stderr and "--admin-pass-fd" in got.stderr


def test_an_empty_db_without_an_admin_stops(tmp_path):
    connect(tmp_path / "imagesrv.db")
    got = run_gate(tmp_path, "")
    assert got.returncode == 1, got
    assert "REACHED_AFTER_GATE" not in got.stdout


def test_a_db_without_a_users_table_stops(tmp_path):
    sqlite3.connect(tmp_path / "imagesrv.db").close()
    got = run_gate(tmp_path, "")
    assert got.returncode == 1, got
    assert "דורשת סיסמה" in got.stderr


def test_a_deploy_only_db_stops(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="t", check_policy=False)
    got = run_gate(tmp_path, "")
    assert got.returncode == 1, got


def test_an_unreadable_db_is_not_assumed_to_have_an_admin(tmp_path):
    """"לא הצלחנו לבדוק" אינו "בדקנו ויש": קובץ שאינו SQLite עוצר, בהודעה אחרת."""
    (tmp_path / "imagesrv.db").write_bytes(b"this is not a database" * 50)
    got = run_gate(tmp_path, "")
    assert got.returncode == 1, got
    assert "REACHED_AFTER_GATE" not in got.stdout
    assert "לא הצלחתי לבדוק" in got.stderr
    assert "דורשת סיסמה" not in got.stderr   # לא הודעת "אין מנהל"


def test_an_existing_admin_continues_without_a_password(tmp_path):
    """הרצה חוזרת (--force) על שרת קיים: ממשיכים, והמנהל לא נוגעים בו."""
    conn = connect(tmp_path / "imagesrv.db")
    users.create(conn, "nadav", "Aa12345!", "admin", by="t")
    before = conn.execute("SELECT pw_hash FROM users WHERE username='nadav'").fetchone()[0]
    got = run_gate(tmp_path, "")
    assert got.returncode == 0, got
    assert "REACHED_AFTER_GATE" in got.stdout
    after = connect(tmp_path / "imagesrv.db").execute(
        "SELECT pw_hash FROM users WHERE username='nadav'").fetchone()[0]
    assert after == before


def test_an_explicit_password_skips_the_check(tmp_path):
    got = run_gate(tmp_path, "Aa12345!")
    assert got.returncode == 0, got
    assert "REACHED_AFTER_GATE" in got.stdout


def test_the_installer_no_longer_creates_admin_admin():
    """המתקין לא קורא ל-ensure_admin (שיוצר admin/admin) ולא מכריז על ברירת מחדל."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "ensure_admin" not in text
    assert "/ admin — הקונסולה תדרוש" not in text


# --- מה שנשאר: שרת קיים עם admin/admin לא ננעל החוצה --------------------------


def test_an_existing_admin_admin_server_stays_reachable_and_forced(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    assert users.ensure_admin(conn) == "admin"           # ההתנהגות של שרת שעולה על DB ריק
    assert users.verify(conn, "admin", "admin") == "admin"
    assert users.flags(conn, "admin")["must_change_password"] is True
    # העלאת מספר הסבבים אינה נועלת אותו, והכפייה נשארת
    conn.execute("UPDATE users SET pw_hash = ? WHERE username = 'admin'",
                 (users._hash("admin", "aa" * 16, 50_000),))
    conn.commit()
    assert users.verify(conn, "admin", "admin") == "admin"
    assert users.flags(conn, "admin")["must_change_password"] is True
