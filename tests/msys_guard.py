"""סקריפט ארוך ב-`bash -c` בווינדוס נקטע בשקט — והטסט "עובר" (#893).

נמדד 03/11/2026, ‏bash של Git for Windows (‏MSYS): מחרוזת `-c` שורדת עד
**8,186 תווים** בדיוק — אותו מספר בעברית ובאנגלית, כלומר תווים ולא
בייטים — ומה שמעבר נחתך. ‏bash מריץ את מה שנשאר ו**יוצא 0**, גם תחת
`-e`: סקריפט של 12,000 תווים שנגמר ב-`echo END` לא מדפיס כלום ומדווח
הצלחה. ארגומנט רגיל (‏`$1`) של 20,000 תווים עובר שלם — זה `-c` בלבד.

ולכן טסט עם קטע גדול מהבנאי או stub גדול רץ בווינדוס על **חצי סקריפט**,
ומה שהוא מודד הוא החיתוך ולא הקוד (‏#893: כ-25 טסטים של `run_required`
נכשלו ב-`unexpected EOF`; טסטים אחרים באותו מצב פשוט עוברים). בלינוקס
אין חיתוך, ולכן זה עוד "ירוק בווינדוס, אחר במעבדה".

השומר: בווינדוס, כל תהליך-בן של מעטפת שמקבל סקריפט `-c` ארוך מהסף
**מכשיל את הטסט בשמו** לפני שהוא רץ — לא מתקן אותו בשקט. התיקון בכל אתר
הוא לכתוב את הסקריפט לקובץ ולהריץ `bash <קובץ>` (‏`run_script_file`).
בלינוקס השומר אינו מותקן: שם אין מה לתפוס, וזה נבדק ב-`test_msys_guard_893`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePath

import pytest

#: הארוך ביותר ששורד (‏8,186 עובר, ‏8,187 נחתך) — נמדד 03/11/2026, Git for Windows.
MSYS_C_LIMIT = 8186

SHELLS = frozenset({"bash", "sh", "dash", "ash"})

#: כל סקריפט שנחסם, לפי הסדר — לספירה בטסטים של השומר עצמו.
blocked: list[tuple[str, int]] = []

_original_init = subprocess.Popen.__init__


def c_script(argv) -> str | None:
    """מחרוזת ה-`-c` ש-`argv` מעביר למעטפת, או None כשאין כזו."""
    if isinstance(argv, (str, bytes, os.PathLike)) or not argv:
        return None
    parts = [a.decode(errors="replace") if isinstance(a, bytes) else os.fspath(a)
             for a in argv]
    name = PurePath(parts[0].replace("\\", "/")).name.lower().removesuffix(".exe")
    i = 1
    if name == "busybox" and len(parts) > 1 and parts[1] in SHELLS:
        i = 2
    elif name not in SHELLS:
        return None
    has_c = False
    while i < len(parts):
        arg = parts[i]
        if arg in ("-o", "+o", "-O", "+O"):
            i += 2
            continue
        if arg == "--":
            i += 1
            break
        if arg.startswith("--"):
            i += 1
            continue
        if len(arg) > 1 and arg[0] in "-+":
            has_c = has_c or (arg[0] == "-" and "c" in arg[1:])
            i += 1
            continue
        break
    return parts[i] if has_c and i < len(parts) else None


def check(argv, platform: str = os.name) -> None:
    """מכשיל את הטסט כש-`argv` יעביר בווינדוס סקריפט `-c` שייחתך."""
    if platform != "nt":
        return
    script = c_script(argv)
    if script is None or len(script) <= MSYS_C_LIMIT:
        return
    blocked.append((str(argv[0]), len(script)))
    pytest.fail(
        f"סקריפט `-c` של {len(script):,} תווים — מעל {MSYS_C_LIMIT:,}, ש-bash של "
        "Git for Windows חותך בשקט ויוצא 0 (#893). כתבו אותו לקובץ והריצו "
        "`bash <קובץ>` (‏msys_guard.run_script_file).",
        pytrace=False,
    )


def _guarded_init(self, args, *rest, **kwargs):
    check(args)
    _original_init(self, args, *rest, **kwargs)


_installed: list[bool] = []


def install() -> bool:
    """מתקין את השומר על `subprocess.Popen` — בווינדוס בלבד. ‏True = הותקן.

    עוטף את מה שמותקן **עכשיו** ולא את מה שהיה ביבוא: שומר אחר שנעטף לפני
    כן נשאר בשרשרת. ההוכחה שהשומר פעיל היא התנהגותית (`test_msys_guard_893`)."""
    global _original_init
    if os.name != "nt" or _installed:
        return bool(_installed)
    _original_init = subprocess.Popen.__init__
    subprocess.Popen.__init__ = _guarded_init
    _installed.append(True)
    return True


def installed() -> bool:
    return bool(_installed)


def bash_path(path: Path) -> str:
    """נתיב ש-bash מבין בשתי הסביבות (‏`C:/x` ← ‏`/c/x` בווינדוס)."""
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def run_script_file(shell: str, script: str, script_file: Path,
                    **kwargs) -> subprocess.CompletedProcess:
    """מריץ `script` מקובץ ולא ב-`-c` — שלם בכל אורך, בשתי הסביבות.

    ‏`newline="\\n"`: ‏`\\r` בסוף שורה הוא חלק מהפקודה בעיני המעטפת."""
    script_file.write_text(script, encoding="utf-8", newline="\n")
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run([shell, bash_path(script_file)], **kwargs)
