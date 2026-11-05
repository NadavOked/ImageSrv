"""‏#2419 (1) — ‏`server-upgrade.sh` עם ‏`set -E`: כשל בתוך פונקציה חוזר אחורה.

בלי errtrace, ה-ERR trap אינו עובר לפונקציות. כשל בתוך ``stage()`` אחרי
ה-checkout (כאן: סמן השחזור שלא נכתב) יצא ב-``set -e`` בלי rollback — העץ
על התג החדש, והסטטוס ``running`` לתמיד. הטסט מריץ את הסקריפט האמיתי.

והצד השני: עם ``-E`` ה-trap עובר גם לתת-מעטפות. כשל בתוך ``$(…)`` שאינו
בהקשר מותנה חייב להחזיר אחורה **פעם אחת**, מהמעטפת הראשית — לא פעם מתוך
תת-המעטפת (שהפלט שלה נבלע במשתנה) ועוד פעם מבחוץ.
"""

from __future__ import annotations

from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    _fake_repo, _run_upgrade, _seed_db, _status, _upgrade_fakes, bindir, calls, fake_tool,
    pytestmark,
)


def test_a_failure_inside_stage_after_checkout_rolls_back(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=True)
    # צעד החבילות רץ אחרי ה-checkout; הוא חוסם את הקובץ הזמני של סמן השחזור,
    # ולכן `stage payload` שאחריו נכשל בתוך write_recovery_marker.
    (repo / "deploy" / "security-upgrade.sh").write_text(
        '#!/usr/bin/env bash\nmkdir -p "$IMAGESRV_DATA_DIR/server-upgrade.state.tmp"\n',
        encoding="utf-8", newline="\n")
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode != 0, log
    status = _status(data)
    assert status and status["state"] == "failed", (status, log)
    assert status["reason"] == "rollback", (status, log)
    assert "נכשל בשורה" in status["error"], status
    seen = calls(tmp_path)
    assert seen.count("git checkout --detach v0.48.0") == 1, seen
    assert not any(c.startswith("boot-payload-build") for c in seen), seen


def test_a_failed_command_substitution_rolls_back_once_from_the_main_shell(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    # ‏AGENT_URL="$(systemctl show -p Environment … | sed … | head)" — לא בהקשר מותנה.
    fake_tool(bindir, "systemctl",
              'case "$1" in show) [ "$3" = Environment ] && exit 1; '
              'echo "LoadState=not-found" ;; esac; exit 0')
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode != 0, log
    seen = calls(tmp_path)
    assert seen.count("git checkout --detach v0.48.0") == 1, seen
    status = _status(data)
    assert status and status["state"] == "failed" and status["reason"] == "rollback", status
