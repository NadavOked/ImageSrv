"""‏#2000 — כשל בצעד של החזרה (rollback) אינו "החזרה הצליחה".

‏``install_units_and_restart`` נקרא רק מתוך ``if`` ב-``rollback``, ושם
``set -e`` כבוי. בלי בדיקה של כל צעד, כשל בהתקנת היחידות של התג הקודם נבלע,
ה-``restart`` הצליח, והסטטוס אמר ``reason: rollback`` ("עובד, על הגרסה
הקודמת") על שרת שחזר רק חלקית. הטסט מריץ את הסקריפט האמיתי: השדרוג נכשל
באימות (``/health/live`` לא עונה), ובעץ של התג הקודם אין קובצי ``*.service``
— ההתקנה בחזרה נכשלת. הכשל חייב להיות גלוי ובשם (עיקרון 5).
"""

from __future__ import annotations

from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    _fake_repo, _run_upgrade, _seed_db, _status, _upgrade_fakes, bindir, calls, pytestmark,
)


def _prev_tree_without_units(bindir):
    git = bindir / "git"   # העץ של v0.48.0 — ‏deploy/*.service אינם בו, ‏install נכשל
    git.write_text(git.read_text(encoding="utf-8").replace(
        "checkout) exit 0 ;;",
        'checkout) if [ "$3" = v0.48.0 ]; then rm -f deploy/*.service; fi; exit 0 ;;'),
        encoding="utf-8", newline="\n")


def test_a_unit_install_that_fails_during_rollback_is_not_a_successful_rollback(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=False)
    _upgrade_fakes(bindir)
    _prev_tree_without_units(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    seen = calls(tmp_path)
    assert "git checkout --detach v0.48.0" in seen
    status = _status(data)
    assert status and status["state"] == "failed", status
    assert status["reason"] != "rollback", status                    # לא "עובד, על הגרסה הקודמת"
    assert status["reason"] == "timeout", status                     # הסוג של הכשל שלא חזרנו ממנו
    assert "והחזרה ל-v0.48.0 נכשלה" in status["error"], status
    assert "התקנת היחידות" in status["error"], status                # הצעד בשמו
    assert "health/live" in status["detail"], status                 # הסיבה המקורית נשארת
    # השרת לא הופעל מחדש על חזרה חלקית, והסמן נשאר לשחזור באתחול (#1893).
    assert seen.count("systemctl restart imagesrv-server") == 1, seen
    assert (data / "server-upgrade.state").exists()


def test_a_whole_rollback_still_reports_rollback(tmp_path, bindir):
    """השומר מהצד השני: חזרה שכל צעדיה הצליחו נשארת ``rollback``."""
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=False)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    status = _status(data)
    assert status and status["reason"] == "rollback" and "הוחזר ל-v0.48.0" in status["error"], status
    assert calls(tmp_path).count("systemctl restart imagesrv-server") == 2
    assert not (data / "server-upgrade.state").exists()
