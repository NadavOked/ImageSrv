"""מעגנים ו-loop שטסט השאיר מאחוריו (#2702).

‏`rm -rf` על תיקיית הריצה **מדלג** על נקודת עגינה ("on a different
device"), ו-loop שקובץ הגיבוי שלו נמחק נשאר `(deleted)` ותופס מקום ושם.
ב-08/11 נמצאו כאלה תחת חמש תיקיות ריצה על ה-testrunner, ו-`/work` התמלא
ב-100% באמצע ריצה מלאה. הקורא כאן הוא **ראיה חיובית** (עיקרון 5): הוא
קורא את `/proc/mounts` ואת `/sys/block/loop*/loop/backing_file` — אותם
מקורות שהקרנל עצמו מחזיק — ולא את מה שהטסט חושב שניקה.

* ‏`leftovers(root)` — מה שחי תחת `root` (נתיב עגינה, או קובץ גיבוי של loop).
* ‏`sweep(root)` — ניתוק לפי ההתאמה הזאת בלבד, כ-root דרך `sudo -n`. הוא
  לא בא במקום ניקוי הטסט: הוא רשת, והטסט שהשאיר נכשל בשמו.
* בלי `/proc` ו-`/sys` (ווינדוס) אין ראיה: ‏`supported()` אומר זאת.
"""

from __future__ import annotations

import os
import posixpath
import shutil
import subprocess
import time
from pathlib import Path

PROC_MOUNTS = Path("/proc/mounts")
SYS_BLOCK = Path("/sys/block")
SBIN = "/usr/sbin:/sbin"


def supported(mounts: Path = PROC_MOUNTS, sys_block: Path = SYS_BLOCK) -> bool:
    return mounts.is_file() and sys_block.is_dir()


def _roots(root: Path | str) -> tuple[str, ...]:
    """הנתיב כפי שקיבלנו ובפתרון symlinks — הקרנל מדווח את השני."""
    raw = posixpath.normpath(Path(root).as_posix())
    return tuple(dict.fromkeys((raw, raw if os.name == "nt" else os.path.realpath(raw))))


def _under(path: str, roots: tuple[str, ...]) -> bool:
    # גבול שם שלם: ‏`.../fx` אינו תופס את `.../fx2`
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _unescape(field: str) -> str:
    """‏/proc/mounts כותב רווח כ-`\\040` וטאב כ-`\\011`."""
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and field[i + 1:i + 4].isdigit() and len(field[i + 1:i + 4]) == 3:
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def mounts_under(root: Path | str, mounts: Path = PROC_MOUNTS) -> list[tuple[str, str, str]]:
    """‏(מקור, נקודת עגינה, סוג) לכל עגינה שנקודתה תחת `root`."""
    roots = _roots(root)
    found = []
    for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and _under(_unescape(parts[1]), roots):
            found.append((_unescape(parts[0]), _unescape(parts[1]), parts[2]))
    return found


def loops_under(root: Path | str, sys_block: Path = SYS_BLOCK) -> list[tuple[str, str]]:
    """‏(שם ה-loop, קובץ הגיבוי) לכל loop שקובץ הגיבוי שלו תחת `root` —
    כולל כשהקובץ נמחק (`(deleted)`), שזה בדיוק היתום."""
    roots = _roots(root)
    found = []
    for back in sorted(sys_block.glob("loop*/loop/backing_file")):
        try:
            target = back.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:                        # ה-loop התנתק בינתיים
            continue
        if _under(target.removesuffix(" (deleted)"), roots):
            found.append((back.parent.parent.name, target))
    return found


def leftovers(root: Path | str, mounts: Path = PROC_MOUNTS, sys_block: Path = SYS_BLOCK) -> list[str]:
    """תיאור קריא של כל מעגן ו-loop תחת `root`; ריק = נקי."""
    return ([f"mount {src} ({fstype}) at {mp}" for src, mp, fstype in mounts_under(root, mounts)]
            + [f"loop {name} -> {target}" for name, target in loops_under(root, sys_block)])


def wait_clean(root: Path | str, timeout: float = 10.0) -> list[str]:
    """‏`losetup -d` מנתק בעצלתיים (autoclear) ו-udev מחזיק צמתים עוד רגע, ולכן
    קריאה מיד אחרי הניקוי רואה לפעמים את מה שכבר בדרך החוצה. מחכים עד
    `timeout`; מה שנשאר אחריו הוא יתום אמיתי."""
    end = time.monotonic() + timeout
    left = leftovers(root)
    while left and time.monotonic() < end:
        time.sleep(0.2)
        left = leftovers(root)
    return left


def _as_root(*args: str) -> subprocess.CompletedProcess:
    env_path = f"PATH={os.environ.get('PATH', '')}:{SBIN}"
    argv = ["env", env_path, *args] if os.geteuid() == 0 else ["sudo", "-n", "env", env_path, *args]
    return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)


def sweep(root: Path | str) -> list[str]:
    """מנתק את מה ש-`leftovers(root)` מוצא: עגינות מהעמוקה לרדודה (`umount -l`,
    כי הניקוי הרגיל כבר נכשל כאן), ואז loop לפי השם שנקרא מ-sysfs. מחזיר את
    מה שנמצא (לדיווח); לא מוחק קבצים."""
    found = leftovers(root)
    if not found or (os.geteuid() != 0 and not shutil.which("sudo")):
        return found
    for _src, mp, _fstype in sorted(mounts_under(root), key=lambda m: len(m[1]), reverse=True):
        _as_root("umount", "-l", mp)
    for name, _target in loops_under(root):
        _as_root("losetup", "-d", f"/dev/{name}")
    return found


if __name__ == "__main__":
    # ‏`python3 mountleak.py list|sweep <dir>` -- למי שמוחק תיקיית ריצה בלי pytest
    # (‏`/work/full*.sh`: ‏`rm -rf` דולג על עגינה). ‏`list` יוצא 1 כשנמצא משהו.
    import sys

    if len(sys.argv) != 3 or sys.argv[1] not in ("list", "sweep") or not supported():
        sys.exit("usage: mountleak.py list|sweep <dir>   (Linux only)")
    found = sweep(sys.argv[2]) if sys.argv[1] == "sweep" else leftovers(sys.argv[2])
    print(chr(10).join(found))
    sys.exit(1 if found and sys.argv[1] == "list" else 0)
