"""#1736: טסט שעבר לא משאיר תהליך חי -- והשומר שתופס את מי שכן.

על ה-Testrunner נמדדו ~100 תהליכים בני 10-23 שעות אחרי ריצה מלאה:
`bash -c` עטיפה (לולאת ה-beat של `attended_start`, שהטסטים של
`lc_pull_session` מפעילים בלי `attended_stop` שבקוד הייצור שאחריו),
`sleep 300`, ו-stub של `fanout -S`. השורש בכלי הבדיקה: `subprocess.run`
מחכה לתהליך הישיר ולא לצאצאיו.

* `test_the_tests_that_leaked_leave_nothing` -- מריץ את הטסטים שדלפו
  בריצת pytest משלהם **בלי השומר** (כדי שלא הוא ינקה), ואז סורק את
  `/proc` אחרי נתיב ה-tmp שלהם. (`test_round_closed.py` נכנס לרשימה
  כשהוא קיים בעץ.)
* `test_the_guard_*` -- הבקרה השלילית של השומר: טסט שמשאיר `sleep` חי
  נכשל בשמו, ונקי / תהליך שבדרך החוצה אינם נכשלים.
* `test_the_scan_*` -- לוגיקת הסריקה על עץ `/proc` מזויף; רצה בכל מערכת.

הבדיקות שצריכות `/proc` מדולגות בווינדוס (אין שם ראיה, ואין udpcast).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import orphan_guard

TESTS = Path(__file__).resolve().parent
LINUX = pytest.mark.skipif(
    not orphan_guard.supported() or shutil.which("bash") is None,
    reason="צריך /proc ו-bash: בווינדוס אין ראיה לתהליכי יתומים (מוצהר, #1736)",
)

#: הטסטים שדלפו (#1736). הראשונים בקובץ שבעץ; השני נכנס כשהקובץ קיים.
LEAKERS = [
    "tests/test_cloner_pull_agent.py::test_the_session_checks_again_then_opens_and_leaves",
    "tests/test_cloner_pull_agent.py::test_a_refused_open_stays_on_the_screen_and_back_leaves",
    "tests/test_cloner_pull_agent.py::test_images_that_could_not_be_read_are_said_not_hidden",
]
ROUND_CLOSED = ("tests/test_round_closed.py::"
                "test_a_round_closed_while_waiting_for_the_stream_stops_the_receiver")


def _inner(args: list[str], cwd: Path, out: Path, env_extra: dict[str, str],
           timeout: int = 240) -> int:
    """pytest בתהליך נפרד. הפלט לקובץ, לא ל-PIPE (תהליך יתום מחזיק צינור)."""
    env = {k: v for k, v in os.environ.items() if k != orphan_guard.OFF_ENV}
    env.update(env_extra, PYTHONIOENCODING="utf-8")
    with out.open("wb") as fh:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *args],
            cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, timeout=timeout,
        ).returncode


def _kill_by_pid(marker: str) -> None:
    for pid, _ in orphan_guard.processes_mentioning(marker):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


# --- הלוגיקה של הסריקה, על /proc מזויף ---------------------------------------

def _fake_proc(root: Path, entries: dict[str, list[str]]) -> Path:
    root.mkdir()
    for name, argv in entries.items():
        (root / name).mkdir()
        (root / name / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    return root


def test_the_scan_matches_the_whole_path_and_only_live_numbered_entries(tmp_path):
    mine = str(tmp_path / "test_x1")
    proc = _fake_proc(tmp_path / "proc", {
        "100": ["bash", "-c", f"export RUN_DIR={mine}/run; sleep 300"],
        "101": ["sleep", "300"],                                     # בלי הנתיב
        "102": ["bash", "-c", f"cd {mine}0"],                        # test_x10 -- לא אותו טסט
        "103": ["bash", f"{mine}"],                                  # הנתיב בסוף השורה
        "self": ["bash", mine],                                      # לא מספרי
        str(os.getpid()): ["pytest", mine],                          # התהליך הנוכחי
    })
    found = orphan_guard.processes_mentioning(mine, proc)
    assert [pid for pid, _ in found] == [100, 103], found
    assert orphan_guard.settle_and_kill(mine, proc, grace=0, kill=False) == found
    assert orphan_guard.settle_and_kill(str(tmp_path / "other"), proc, grace=0, kill=False) == []


def test_the_guard_says_in_the_header_when_it_cannot_look(monkeypatch):
    """בלי /proc אין ראיה: השומר מצהיר שהוא כבוי, לא שותק."""
    monkeypatch.setattr(orphan_guard, "supported", lambda *_: False)
    assert "OFF" in orphan_guard.header_line() and "Windows" in orphan_guard.header_line()


# --- הבקרה השלילית של השומר: טסט שמשאיר sleep חי ----------------------------

_CONFTEST = """\
import sys
sys.path.insert(0, {tests!r})
from orphan_guard import orphan_guard  # noqa: F401
"""

_LEAKY = '''\
import subprocess


def _sleeper(tmp_path, seconds):
    # exec -a: התהליך עצמו הוא ה-sleep, ושורת הפקודה שלו נושאת את הנתיב
    subprocess.Popen(["bash", "-c", 'exec -a "$0" sleep ' + str(seconds), str(tmp_path / "m")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def test_leaves_a_sleep_alive(tmp_path):
    _sleeper(tmp_path, 300)


def test_leaves_a_sleep_that_ends_within_the_grace(tmp_path):
    _sleeper(tmp_path, 1)


def test_leaves_nothing(tmp_path):
    (tmp_path / "f").write_text("x")


def test_does_not_use_tmp_path():
    assert True
'''


@LINUX
def test_the_guard_fails_the_test_that_leaves_a_sleep_and_only_it(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "conftest.py").write_text(_CONFTEST.format(tests=str(TESTS)), encoding="utf-8")
    (proj / "test_leaky.py").write_text(_LEAKY, encoding="utf-8")
    out = tmp_path / "out.txt"
    bt = tmp_path / "bt"
    try:
        rc = _inner([str(proj), f"--basetemp={bt}", "--rootdir", str(proj), "-rA"], proj, out, {})
        text = out.read_text(encoding="utf-8", errors="replace")
        assert rc != 0, text
        # הטסט הדולף עובר ב-call ונופל בפירוק: 4 passed + 1 error, לא ירוק
        assert "1 error" in text and "4 passed" in text, text
        # ה-ERROR הוא של הטסט הדולף ושלו
        assert re.search(r"ERROR .*test_leaky\.py::test_leaves_a_sleep_alive", text), text
        assert "#1736" in text, text
        pid = int(re.search(r"pid (\d+): .*bt/test_leaves_a_sleep_alive0", text).group(1))
        # נהרג לפי PID, ו-/proc מאשר: לא "שלחנו SIGKILL" אלא "אינו שם"
        gone = not Path(f"/proc/{pid}").exists() or not Path(f"/proc/{pid}/cmdline").read_bytes()
        assert gone, f"pid {pid} עדיין חי אחרי שהשומר דיווח שהרג אותו"
        assert orphan_guard.processes_mentioning(str(bt)) == []
    finally:
        _kill_by_pid(str(bt))


# --- ההוכחה לטסטים שדלפו (#1736) -------------------------------------------

@LINUX
def test_the_tests_that_leaked_leave_nothing(tmp_path):
    repo = TESTS.parent
    targets = list(LEAKERS)
    if (repo / "tests" / "test_round_closed.py").exists():
        targets.append(ROUND_CLOSED)
    bt = tmp_path / "bt"
    out = tmp_path / "out.txt"
    try:
        # בלי השומר: הוא מנקה, וכאן רוצים למדוד את העוזרים עצמם
        rc = _inner([*targets, f"--basetemp={bt}"], repo, out, {orphan_guard.OFF_ENV: "off"})
        text = out.read_text(encoding="utf-8", errors="replace")
        assert rc == 0 and f"{len(targets)} passed" in text and "skipped" not in text, text
        left = orphan_guard.processes_mentioning(str(bt))
        assert left == [], "תהליכים שנשארו חיים אחרי הטסטים:\n" + "\n".join(
            f"  pid {pid}: {line[:200]}" for pid, line in left)
    finally:
        _kill_by_pid(str(bt))
