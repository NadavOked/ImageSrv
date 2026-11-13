"""דיסקי ה-loop של טסטי השיכפול המקומי: הרצה כ-root וניקוי שמוכח (#2702).

כל טסט שמרים את `localclone-fixture.sh` (loop, tmpfs של 96MB, עגינות) עושה
את זה **רק** דרך `fixture_dir`. ארבעה fixtures נפרדים כתבו כל אחד לעצמו
`try/finally`, ושלושה מהם לא קראו בחזרה מה נשאר; ב-08/11 נמצאו תחת חמש
תיקיות ריצה על ה-testrunner loops עם קובץ גיבוי `(deleted)` ו-tmpfs
מעוגנים, ו-`rm -rf` על התיקייה דילג עליהם.

שלושה דברים, כל אחד על ראיה חיובית:

1. **הניקוי בא גם כשההרמה נקטעה.** ה-`setup` נמצא בתוך ה-`try`: פקיעת זמן
   באמצע שלו (`TimeoutExpired`) משאירה loops, ו-teardown קורא את `devs` שנכתב
   תוך כדי.
2. **ההרצה כ-root נהרגת לפי PID.** `subprocess.run` על `sudo` הורג בפקיעה
   את ה-`sudo` בלבד; הילדים של root שורדים ומחזיקים את
   `/tmp/imagesrv-lc-engine.lock` (ריצה 4 בסבב 1107c נתקעה עד ה-timeout).
   `root` מריץ בקבוצה משלו ו-`hygiene.kill_group` הורג את כולה דרך `sudo -n`.
3. **אחרי ה-teardown קוראים מה נשאר** (`mountleak`). נשאר משהו -- מנתקים
   אותו (כדי שהמכונה לא תצבור) והטסט נכשל בשמו.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import hygiene
import mountleak

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "localclone-fixture.sh"
SBIN = "/usr/sbin:/sbin"


class SetupCut(subprocess.TimeoutExpired):
    """ה-setup נקטע בכוונה (#2738); `seen` = מה שחי תחת התיקייה ברגע הקטיעה."""

    def __init__(self, cmd, seen: list[str]):
        super().__init__(cmd, 0)
        self.seen = seen


def _root_argv(args: tuple[str, ...]) -> list[str]:
    return ["sudo", "-n", "env", f"PATH={os.environ.get('PATH', '')}:{SBIN}", *args]


def root_cut(fx: Path, *args: str, cut_when: Callable[[list[str]], bool], timeout: int = 600) -> subprocess.CompletedProcess:
    """כמו `root`, אבל נקטע כש-`cut_when(מה שחי תחת fx)` נכון -- לפי מצב, לא לפי שעון (#2738).
    נקטע: `SetupCut`. הסתיים לפני: CompletedProcess, והקורא מחליט שהקטיעה לא נבדקה."""
    argv = _root_argv(args)
    with subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace") as proc:
        end = time.monotonic() + timeout
        try:
            while True:
                try:
                    out, err = proc.communicate(timeout=0.05)
                    return subprocess.CompletedProcess(argv, proc.returncode, out, err)
                except subprocess.TimeoutExpired:
                    pass
                seen = mountleak.leftovers(fx)
                if cut_when(seen):
                    # #2838: the cut is made and the group is dead (kill_group is synchronous). Nothing
                    # reads the pipes any more: the old one-second `communicate` drain waited for a
                    # root child, killed in the middle of mount/losetup, to leave the kernel, and
                    # under load (10-18 on 14 cores) that took longer than a second -- a
                    # TimeoutExpired raised in place of the SetupCut the test is waiting for. The
                    # `with` closes the pipes and reaps the sudo; a child that outlives that is
                    # the orphan guard's finding, not this helper's.
                    hygiene.kill_group(proc.pid)
                    raise SetupCut(argv, seen)
                if time.monotonic() > end:
                    hygiene.kill_group(proc.pid)
                    proc.communicate(timeout=hygiene.RUN_GROUP_DRAIN_S)
                    raise subprocess.TimeoutExpired(argv, timeout)
        finally:
            hygiene.kill_group(proc.pid)


def root(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """`sudo -n env PATH=... <args>` בקבוצת תהליכים משלו: פקיעה (או Ctrl-C)
    הורגת את הקבוצה כולה, כולל ילדי root, לפי ה-PID שנשמר."""
    return hygiene.run_group(
        _root_argv(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        stdin=subprocess.DEVNULL)


def release(fx: Path, *, pre: Callable[[], None] | None = None) -> None:
    """teardown, קריאה חוזרת של מה שנשאר, וניתוק כפוי של השאר. נכשל בשמו."""
    problems: list[str] = []
    try:
        if pre is not None:
            try:
                pre()
            except subprocess.TimeoutExpired as exc:      # teardown still runs: it is the one that detaches
                problems.append(f"pre-teardown step timed out: {exc}")
        down = root("sh", str(FIXTURE), "teardown", str(fx), timeout=120)
        if down.returncode != 0:
            problems.append(f"teardown rc {down.returncode}: {down.stdout}{down.stderr}")
    except subprocess.TimeoutExpired as exc:
        problems.append(f"teardown timed out: {exc}")
    left = mountleak.wait_clean(fx) if mountleak.supported() else []
    if left:
        mountleak.sweep(fx)
        problems.append("left behind under the test dir (detached now):\n    " + "\n    ".join(left))
    if problems:
        pytest.fail("#2702: " + "\n".join(problems))


@contextlib.contextmanager
def fixture_dir(fx: Path, kind: str, *args: str, setup_timeout: int = 300,
                pre_teardown: Callable[[], None] | None = None,
                cut_when: Callable[[list[str]], bool] | None = None) -> Iterator[Path]:
    """`localclone-fixture.sh <kind> <fx> [args]` ואחריו -- תמיד -- הניקוי.

    ה-`setup` בתוך ה-`try`: הוא שמעגן tmpfs ופותח loops, ופקיעה באמצעו היא
    בדיוק המקרה שהשאיר יתומים."""
    try:
        if cut_when is None:
            up = root("sh", str(FIXTURE), kind, str(fx), *args, timeout=setup_timeout)
        else:       # #2738: נקטע כשהתנאי מתקיים, לא אחרי N שניות
            up = root_cut(fx, "sh", str(FIXTURE), kind, str(fx), *args, cut_when=cut_when, timeout=setup_timeout)
        assert up.returncode == 0, up.stdout + up.stderr
        yield fx
    finally:
        release(fx, pre=pre_teardown)
