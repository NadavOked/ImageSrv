"""מלכודת כיבוי: טסט שמגיע ל-reboot/poweroff/halt/shutdown אמיתי נכשל בשמו (#2181).

הסוכן מסיים משימה, נכשל, או מקבל "כבה" מהקונסולה ב-`reboot -f` או
`poweroff -f` — בלי נתיב, דרך ה-PATH. על ה-testrunner ‏`type -P reboot` הוא
‏`/usr/sbin/reboot` (נמדד ב-#2077), ומה שמנע אתחול אמיתי באמצע ריצה היה רק
שהמשתמש שם אינו root. ‏#2077 סגר את `run_path`; רתמות אחרות לא נסקרו.

כאן השומר הכללי, בשתי שכבות:

1. **המלכודת ב-PATH.** בתחילת הריצה נוצרת תיקייה עם ארבעה זיופים
   (‏`POWER_COMMANDS`) שרושמים כל קריאה ליומן ויוצאים 1, והיא נכנסת
   **לראש** ה-PATH של התהליך. כל תהליך-בן יורש אותה; זיוף של טסט שנכנס
   לפניה (‏`PATH="$stubs:$PATH"`) ממשיך לגבור, ורק הבינארי האמיתי מוצל.
   ‏`env=` מפורש שבונה PATH מאפס (‏`{stubs}:/usr/sbin:/usr/bin`) מקבל את
   המלכודת **לפני התיקייה הראשונה של המערכת** — אחרי זיופי הטסט (POSIX).
2. **השומר לכל טסט** (`power_guard`, autouse): קריאה חדשה ביומן בזמן
   הטסט = הטסט נכשל בשמו, עם הפקודה והארגומנטים. "הקריאה נכשלה כי איננו
   root" אינה עוד הצלחה שקטה.

**מה לא מכוסה, ונאמר כאן ולא בשקט:**

* סקריפט שמאפס PATH **בתוך עצמו** לתיקייה שיש בה הבינארי האמיתי
  (‏`export PATH=/usr/sbin:...`) — ‏Python אינו רואה את זה. ‏`/usr/bin`
  בדביאן 13 אינו מחזיק את reboot (‏`/usr/sbin` כן), ולכן `PATH=/usr/bin:/bin`
  פשוט אינו מוצא אותו.
* קריאה בנתיב מלא (‏`/sbin/reboot`). הסוכן אינו קורא כך.
* applet של busybox: ‏`busybox ash` עם FEATURE_SH_STANDALONE מריץ את
  ה-applet לפני ה-PATH. לסקריפט `-c` של busybox (POSIX) מוזרקות לכן
  **פונקציות** מלכודת בראש הסקריפט — פונקציה גוברת על applet, וזיוף-פונקציה
  של הטסט שמוגדר אחריהן גובר עליהן. סקריפט busybox **מקובץ** אינו מכוסה.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePath

import pytest

POWER_COMMANDS = ("reboot", "poweroff", "halt", "shutdown")

#: תיקיות שבהן יושב הבינארי האמיתי. ‏PATH מפורש מקבל את המלכודת לפני הראשונה.
SYSTEM_DIRS = frozenset({"/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin",
                         "/sbin", "/bin"})

_trap: dict[str, Path] = {}
_original_init = subprocess.Popen.__init__
_acknowledged = [0]


def trap_dir() -> Path:
    return _trap["dir"]


def log_file() -> Path:
    return _trap["log"]


def _stub(log: Path) -> str:
    return (
        "#!/bin/sh\n"
        f"# #2181: power trap -- a test reached a real power command\n"
        f"printf '%s\\n' \"${{0##*/}}${{*:+ $*}}\" >> '{log.as_posix()}'\n"
        "echo \"powertrap: ${0##*/} $* -- stub it in the test (#2181)\" >&2\n"
        "exit 1\n"
    )


def _sh_path(path: Path) -> str:
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def install() -> Path:
    """יוצר את המלכודת, שם אותה בראש ה-PATH ומתקין את השומר על `Popen`."""
    global _original_init
    if _trap:
        return _trap["dir"]
    directory = Path(tempfile.mkdtemp(prefix="imagesrv-powertrap-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    log = directory / "calls.log"
    log.write_text("", encoding="utf-8")
    for name in POWER_COMMANDS:
        stub = directory / name
        stub.write_text(_stub(log), encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    _trap.update(dir=directory, log=log)
    os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
    _original_init = subprocess.Popen.__init__
    subprocess.Popen.__init__ = _guarded_init
    return directory


def with_trap(path: str, trap: str | None = None) -> str:
    """‏PATH של POSIX שהמלכודת בו לפני התיקייה הראשונה של המערכת."""
    trap = trap or str(trap_dir())
    entries = path.split(":")
    if trap in entries:
        return path
    at = next((i for i, e in enumerate(entries)
               if (e.rstrip("/") or "/") in SYSTEM_DIRS), len(entries))
    return ":".join(entries[:at] + [trap] + entries[at:])


def trap_functions() -> str:
    """פונקציות מלכודת בשורה אחת, בלי שורה חדשה — מספרי השורות של הסקריפט נשמרים."""
    d = _sh_path(trap_dir())
    return "".join(f"{n}() {{ '{d}/{n}' \"$@\"; }}; " for n in POWER_COMMANDS)


def _busybox_trapped(args):
    """‏`[busybox, ash, -c, script, ...]` עם פונקציות המלכודת בראש הסקריפט."""
    if isinstance(args, (str, bytes, os.PathLike)) or len(args) < 4:
        return args
    if PurePath(os.fspath(args[0])).name != "busybox" or args[1] not in ("ash", "sh"):
        return args
    for i in range(2, len(args) - 1):
        flag = args[i]
        if not (isinstance(flag, str) and flag.startswith("-")):
            return args
        if "c" in flag[1:]:
            args = list(args)
            args[i + 1] = trap_functions() + args[i + 1]
            return args
    return args


def _guarded_init(self, args, *rest, **kwargs):
    if os.name != "nt":
        env = kwargs.get("env")
        if env is not None and "PATH" in env:
            kwargs["env"] = {**env, "PATH": with_trap(env["PATH"])}
        args = _busybox_trapped(args)
    _original_init(self, args, *rest, **kwargs)


def calls() -> list[str]:
    """כל קריאה שהגיעה למלכודת בריצה הזאת, לפי הסדר."""
    return log_file().read_text(encoding="utf-8").splitlines()


def acknowledge() -> None:
    """לטסטים של המלכודת עצמה: הקריאות עד כאן צפויות, והשומר לא יכשיל עליהן."""
    _acknowledged[0] = len(calls())


def escaped(start: int) -> list[str]:
    """הקריאות מאז `start` שאיש לא הצהיר עליהן — מה ש-`power_guard` מכשיל."""
    return calls()[max(start, _acknowledged[0]):]


@pytest.fixture(autouse=True)
def power_guard():
    """טסט שהגיע למלכודת נכשל בשמו — ‏ERROR בפירוק, עם הפקודה."""
    if not _trap:
        yield
        return
    start = len(calls())
    yield
    new = escaped(start)
    if new:
        pytest.fail(
            "הטסט הגיע לפקודת כיבוי אמיתית (#2181): " + " | ".join(new)
            + ". זייפו אותה בטסט — פונקציה, או זיוף ב-PATH לפני המלכודת.",
            pytrace=False,
        )
