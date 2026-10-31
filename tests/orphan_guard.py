"""שומר תהליכים יתומים: טסט שהשאיר תהליך חי נכשל בשמו (#1736).

על ה-Testrunner נמדדו ~100 תהליכים בני 10-23 שעות אחרי ריצות מלאות
(`bash -c` עטיפה, ‏`sleep 300`, ‏stub של fanout) -- ותפסו את הדיסק עד 92%.
התיקון בשורש (`hygiene.run_group`) מכסה את העוזרים שנמצאו; **השומר הזה
מכסה את הבא**: בסוף כל טסט שהשתמש ב-`tmp_path` הוא קורא ב-`/proc/*/cmdline`
מי עוד חי ומזכיר את הנתיב הזה. ‏`tmp_path` ייחודי לטסט, ולכן כל התאמה
היא תהליך של הטסט הזה ושלו בלבד -- לא של ריצה מקבילה ולא של מישהו אחר.

* נשאר תהליך = הטסט נכשל בשמו (ERROR בפירוק), עם pid ושורת הפקודה.
* ההריגה היא לפי PID בלבד, ואחריה קריאה חוזרת ב-/proc -- "שלחנו SIGKILL"
  אינו "התהליך מת".
* תהליך שכבר בדרך החוצה (`sleep 1` שהטסט המתין לו) מקבל חסד קצר.
* מה שהשומר רואה הוא **שורת הפקודה** בלבד. צאצא שאין לו את הנתיב
  (`sleep 300` שהמעטפת הפעילה) אינו נראה לו -- זה תפקידו של
  `hygiene.run_group`, שהורג קבוצה שלמה.
* בלי `/proc` (ווינדוס) אין ראיה, והשומר כבוי -- ונאמר בכותרת הריצה
  (`header_line`), לא בשקט.
* ‏`IMAGESRV_ORPHAN_GUARD=off`: רק לטסט שמודד את העוזרים בלי השומר
  (`test_no_orphans_1736`); לא לריצות רגילות.
"""

from __future__ import annotations

import os
import re
import signal
import time
from pathlib import Path

import pytest

OFF_ENV = "IMAGESRV_ORPHAN_GUARD"
GRACE_S = 3.0


def supported(proc_root: Path = Path("/proc")) -> bool:
    return proc_root.is_dir()


def active() -> bool:
    return supported() and os.environ.get(OFF_ENV) != "off"


def header_line() -> str:
    if not supported():
        return "orphan guard: OFF -- no /proc here (Windows): leaked processes are not detected"
    if os.environ.get(OFF_ENV) == "off":
        return f"orphan guard: OFF ({OFF_ENV}=off)"
    return "orphan guard: on (a test that leaves a process mentioning its tmp_path fails)"


def processes_mentioning(marker: str, proc_root: Path = Path("/proc")) -> list[tuple[int, str]]:
    """תהליכים חיים ששורת הפקודה שלהם מכילה את `marker` כנתיב שלם.

    ‏`.../test_x1` אינו תופס את `.../test_x10`: אחרי ההתאמה אסור תו של שם.
    התהליך הנוכחי לא נספר.
    """
    pattern = re.compile(re.escape(marker) + r"(?![0-9A-Za-z_])")
    found: list[tuple[int, str]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:                        # התהליך מת בינתיים
            continue
        line = " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)
        if pattern.search(line):
            found.append((int(entry.name), line))
    return sorted(found)


def settle_and_kill(marker: str, proc_root: Path = Path("/proc"), grace: float = GRACE_S,
                    kill: bool = True) -> list[tuple[int, str]]:
    """ממתין עד `grace` שתהליכים ייעלמו מעצמם; מי שנשאר נהרג לפי PID
    ונקרא שוב. מחזיר את מי ש**היה** חי אחרי החסד (גם אם נהרג כעת)."""
    deadline = time.monotonic() + grace
    leaked = processes_mentioning(marker, proc_root)
    while leaked and time.monotonic() < deadline:
        time.sleep(0.1)
        leaked = processes_mentioning(marker, proc_root)
    if leaked and kill:
        for pid, _ in leaked:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:                    # כבר מת
                pass
        end = time.monotonic() + 3.0
        while processes_mentioning(marker, proc_root) and time.monotonic() < end:
            time.sleep(0.05)
    return leaked


@pytest.fixture(autouse=True)
def orphan_guard(request):
    marker = None
    if active() and "tmp_path" in request.fixturenames:
        marker = str(request.getfixturevalue("tmp_path"))
    yield
    if marker is None:
        return
    leaked = settle_and_kill(marker)
    if leaked:
        pytest.fail(
            f"#1736: הטסט השאיר {len(leaked)} תהליכים חיים שמזכירים את ה-tmp_path שלו "
            "(נהרגו עכשיו לפי PID):\n"
            + "\n".join(f"    pid {pid}: {line[:300]}" for pid, line in leaked)
            + "\nהפעלה דרך `hygiene.run_group` הורגת את כל הקבוצה בסוף הטסט.",
            pytrace=False,
        )
