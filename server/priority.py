"""עדיפות מעבד ודיסק לעבודה הכבדה של השרת (‏#1974, הכרעת נדב 22/10).

בזמן שידור או קליטה הקונסולה וה-DB צריכים להישאר מהירים. השידור
(‏udp-sender) והכתיבה של הקליטה רצים לכן **בעדיפות נמוכה מהשרת עצמו** —
‏nice 10 למעבד, ‏ionice best-effort 7 לדיסק — ולא idle: עדיפות idle
עלולה להרעיב את השידור לכיתה כשיש עבודת דיסק אחרת, ו-"מהירות מרבית
אצלנו" (נדב 17/10) אומר שהגבלה באה רק כהגנה. כשהמכונה פנויה העדיפות אינה
משנה דבר; היא מכריעה רק כשיש תחרות, ואז הקונסולה קודמת.

שני מנגנונים, לפי מי שמבצע את העבודה:

* **תהליך-בן** (‏udp-sender): קידומת `nice`/`ionice` לשורת הפקודה. ‏`nice`
  ו-`ionice` מבצעים `exec`, ולכן ה-PID שהשרת שומר הוא של udp-sender
  עצמו — העצירה וה-`/proc/<pid>/comm` של #439 אינם משתנים.
* **תהליכון בתוך השרת** (הכתיבה והגיבוב של הקליטה, האימות שאחריה):
  ‏`setpriority` ו-`ioprio_set` על ה-TID — בלינוקס כל תהליכון הוא משימה
  משלו, וההנמכה חלה עליו בלבד, לא על הלולאה של uvicorn.

**גבול ידוע:** עדיפות הדיסק נאכפת רק במתזמן שמכבד אותה (‏BFQ; ‏mq-deadline
מבחין במחלקה בלבד, ו-`none` — ‏NVMe ו-virtio — לא בכלל). עדיפות המעבד
נאכפת תמיד. ההנמכה אינה נכשלת בשקט: כלי חסר או קריאת מערכת שנדחתה נרשמים
ביומן השרת (עיקרון 5), והעבודה ממשיכה בעדיפות רגילה.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("imagectl.priority")

NICE = 10
#: ‏ionice: מחלקה 2 (best-effort), רמה 7 — הנמוכה במחלקה, ואינה מרעיבה.
IO_CLASS, IO_LEVEL = 2, 7
BACKGROUND_PREFIX = ("nice", "-n", str(NICE), "ionice", "-c", str(IO_CLASS), "-n", str(IO_LEVEL))

#: ‏ioprio_set(2) — אין לו עטיפה בספרייה התקנית. מספר קריאת המערכת לפי
#: ארכיטקטורה; ארכיטקטורה שאינה כאן — ההנמכה של הדיסק נאמרת ביומן ומדולגת.
_IOPRIO_SET = {"x86_64": 251, "aarch64": 30, "i686": 289, "i386": 289}
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_CLASS_SHIFT = 13


def background_cmd(cmd: list[str], which=None) -> list[str]:
    """‏`cmd` בעדיפות הרקע — או `cmd` כמות שהוא, ובקול, כשאחד הכלים חסר."""
    which = which or shutil.which
    if which("nice") and which("ionice"):
        return [*BACKGROUND_PREFIX, *cmd]
    log.warning("priority: nice/ionice missing -- %s runs at normal priority", cmd[0])
    return list(cmd)


def ioprio_value(io_class: int = IO_CLASS, level: int = IO_LEVEL) -> int:
    return (io_class << _IOPRIO_CLASS_SHIFT) | level


def lower_this_thread(*, setpriority=getattr(os, "setpriority", None),
                      syscall=None, machine: str | None = None) -> list[str]:
    """מנמיך את התהליכון **הנוכחי** בלבד. מחזיר את מה שלא הונמך (ריק = הכול).

    ‏`setpriority`/`syscall`/`machine` מוזרקים בבדיקות. כשל אינו זורק — זו
    עדיפות, לא תנאי לעבודה — אבל הוא נרשם ביומן ומוחזר."""
    problems: list[str] = []
    tid = threading.get_native_id()
    if setpriority is None:
        problems.append("cpu: setpriority unavailable on this platform")
    else:
        try:
            setpriority(getattr(os, "PRIO_PROCESS", 0), tid, NICE)
        except OSError as exc:
            problems.append(f"cpu: {exc}")
    arch = machine if machine is not None else (os.uname().machine if hasattr(os, "uname") else "")
    number = _IOPRIO_SET.get(arch)
    native = sys.platform.startswith("linux") or syscall is not None
    if number is None or not native:
        problems.append(f"io: ioprio_set unknown on {arch or sys.platform}")
    else:
        call = syscall or ctypes.CDLL(None, use_errno=True).syscall
        if call(number, _IOPRIO_WHO_PROCESS, tid, ioprio_value()) != 0:
            problems.append(f"io: ioprio_set failed (errno {ctypes.get_errno()})")
    if problems:
        log.warning("priority: thread %s not fully lowered: %s", tid, "; ".join(problems))
    return problems


#: ‏#1974: הכתיבה והגיבוב של הקליטה — מאגר משלה, שכל תהליכון בו מונמך פעם
#: אחת בהיווצרו. ‏`asyncio.to_thread` הוא מאגר **משותף** עם כל endpoint
#: סינכרוני של הקונסולה, והנמכה שם הייתה מנמיכה גם את הקונסולה.
CAPTURE_IO = ThreadPoolExecutor(max_workers=8, thread_name_prefix="capture-io",
                                initializer=lower_this_thread)
