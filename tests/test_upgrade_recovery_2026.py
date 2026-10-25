"""‏#2026 — שחזור עדכון שנקטע (#1893) מחזיר גם טיימרים, ומסיר כל יחידה
שהעדכון הוסיף.

עד #2026 ‏``server-upgrade-recover.sh`` התקין מחדש רק ``install/*.service``
של התג הקודם. מאז #1957 העדכון מתקין ומפעיל גם ``*.timer``; חשמל שנפל אחרי
``enable_timers`` השאיר את ‏``imagectl-backup.timer`` מופעל מול עץ שאין בו
את המודול שלו. רשימת היחידות שהעדכון הביא נקראת מ-git (``ls-tree`` של התג),
לא מהסמן — סמן שנכתב ב-``server-upgrade.sh`` ישן (‏v0.76 בשטח) אין בו רשימה.
"""

from __future__ import annotations

from test_installer_1131 import fake_tool, posix  # noqa: F401
from test_upgrade_recovery_1893 import (  # noqa: F401 -- bindir is a fixture
    _box, _marker, _recover, _status, bindir, calls, pytestmark,
)

NEW = ("imagectl-backup.service", "imagectl-backup.timer")   # ‏#1895/#1957 — רק בתג החדש


def _cut_after_enable_timers(tmp_path, bindir, *, git_ls_tree="ok", disable_fails=False):
    data, boot, repo = _box(tmp_path, bindir)
    units = tmp_path / "units"
    # העץ על הדיסק — של התג החדש: יחידות הגיבוי, וטיימר שקיים גם בתג הקודם.
    for name in (*NEW, "imagectl-old.timer"):
        (repo / "install" / name).write_text("[Unit]\n", encoding="utf-8")
        (units / name).write_text("[Unit]\n# installed by the cut upgrade\n", encoding="utf-8")
    listing = "\\n".join(f"install/{p.name}" for p in sorted((repo / "install").iterdir()))
    ls_tree = {"ok": f'printf "{listing}\\n"', "empty": "exit 0", "fails": "exit 128"}[git_ls_tree]
    rm_new = " ".join(f"install/{n}" for n in NEW)
    fake_tool(bindir, "git", f'case "$1" in ls-tree) {ls_tree} ;; checkout) rm -f {rm_new} ;; esac')
    fake_tool(bindir, "systemctl",
              'case "$1" in disable) exit 1 ;; esac' if disable_fails else "exit 0")
    _marker(data, repo, boot, "payload")
    return data, units


def test_a_cut_upgrade_disables_and_removes_the_units_it_added(tmp_path, bindir):
    data, units = _cut_after_enable_timers(tmp_path, bindir)
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = calls(tmp_path)
    assert "systemctl disable --now imagectl-backup.timer" in seen, seen
    assert seen.index("git checkout -q --detach v0.48.0") < seen.index(
        "systemctl disable --now imagectl-backup.timer") < seen.index("systemctl daemon-reload")
    for name in NEW:
        assert not (units / name).exists(), name
    status = _status(data)
    assert status["state"] == "failed" and status["reason"] == "rollback", status
    assert "imagectl-backup.timer" in status["error"], status
    assert not (data / "server-upgrade.state").exists()


def test_the_previous_versions_timers_are_reinstalled_and_left_on(tmp_path, bindir):
    data, units = _cut_after_enable_timers(tmp_path, bindir)
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (units / "imagectl-old.timer").read_text(encoding="utf-8") == "[Unit]\n"   # PREV's copy
    assert "systemctl disable --now imagectl-old.timer" not in calls(tmp_path)


def test_a_unit_that_does_not_disable_fails_the_recovery_and_keeps_the_marker(tmp_path, bindir):
    data, _units = _cut_after_enable_timers(tmp_path, bindir, disable_fails=True)
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode != 0
    status = _status(data)
    assert status["reason"] == "install" and "imagectl-backup" in status["error"], status
    assert (data / "server-upgrade.state").exists()


def test_units_of_the_cut_tag_that_cannot_be_read_fail_the_recovery(tmp_path, bindir):
    for case in ("fails", "empty"):            # "לא נקרא" אינו "לא נוסף דבר"
        box = tmp_path / case
        box.mkdir()
        bins = box / "bin"
        bins.mkdir()
        data, _units = _cut_after_enable_timers(box, bins, git_ls_tree=case)
        proc = _recover(box, bins, data)
        assert proc.returncode != 0, case
        status = _status(data)
        assert status["reason"] == "install" and "ls-tree" in status["error"], (case, status)
        assert (data / "server-upgrade.state").exists(), case
        assert not [c for c in calls(box) if c.startswith("git checkout")], case
