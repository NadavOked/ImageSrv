"""המארח של "עדכון אימג' ב-VM" — הקוד שרץ מוודא את מה שהוא צריך (‏#1372).

עדכון מהכפתור רץ ב-`tools/server-upgrade.sh` של **התג הישן** (הוא מופעל לפני
ה-checkout, ו-bash ממשיך לקרוא את הקובץ שנפתח). שרת שעודכן מ-v0.71 ל-v0.73.2
לא הריץ את צעד 3ב (‏deploy/vmupdate-host.sh, ‏#1334) — הוא לא היה בסקריפט הישן —
והעדכון דווח "אומת" בירוק בלי qemu, ‏ovmf, ‏guestfs ובלי imagesrv-vm (‏13/10).

לכן ההכנה אינה תלויה במי שהפעיל את העדכון: **השרת החדש**, בעלייה, שואל את
המנוע אם המארח מוכן (`VmUpdate.host_problem`, אותן בדיקות שההפעלה מסרבת
לפיהן), ואם לא — מפעיל את deploy/vmupdate-host.sh **של הגרסה שרצה** ביחידה
מנותקת (`systemd-run`, כמו העדכון עצמו: השירות בארגז חול ואין לו apt). כל עוד
המארח אינו מוכן זה נאמר בשני מקומות, ולא ירוק:

* סטטוס העדכון — `warning` ("אומת — אבל עדכון ב-VM לא זמין: …"), מחושב בזמן
  הקריאה: הסקריפט הישן כותב `done` **אחרי** שהשרת החדש עלה, ואזהרה שנכתבה
  בעלייה הייתה נמחקת.
* מסך הבריאות — שורה צהובה עם הסיבה ומה נעשה בה.

המצב נקרא בכל שאלה מחדש — כשההכנה ברקע מסתיימת, האזהרה נעלמת בלי restart.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

log = logging.getLogger("imagesrv.vmhost")

#: היחידה המנותקת. ‏`journalctl -u` עליה מראה את פלט ההכנה.
UNIT = "imagesrv-vmhost"
SCRIPT = Path("deploy") / "vmupdate-host.sh"
WARNING_PREFIX = "עדכון ב-VM לא זמין: "


def _run(argv: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def _can_launch() -> str | None:
    """None = אפשר להפעיל יחידה מנותקת; אחרת למה לא (תחנת פיתוח, משתמש רגיל)."""
    if shutil.which("systemd-run") is None:
        return "אין systemd-run"
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return "השרת אינו רץ כ-root"
    return None


class VmHost:
    def __init__(self, problem: Callable[[], str | None], repo_dir: str | Path, *,
                 run: Callable[[list[str]], tuple[bool, str]] = _run,
                 can_launch: Callable[[], str | None] = _can_launch):
        self._problem = problem
        self.repo_dir = Path(repo_dir)
        self._run = run
        self._can_launch = can_launch
        #: מה קרה להכנה ברקע בעלייה הזו — חלק מהאזהרה. ‏None = לא הופעלה.
        self.ensure_note: str | None = None

    def problem(self) -> str | None:
        try:
            return self._problem()
        except Exception as exc:   # noqa: BLE001 — "לא נבדק" אינו "מוכן" (עיקרון 5)
            log.warning("vmhost: readiness not checked", exc_info=True)
            return f"מצב המארח לא נבדק ({exc})"

    def ensure(self) -> None:
        """פעם אחת, בעליית השרת: מארח שאינו מוכן → ההכנה של הגרסה שרצה, ברקע."""
        why = self.problem()
        if why is None:
            return
        script = self.repo_dir / SCRIPT
        blocked = None if script.is_file() else f"{SCRIPT} אינו בגרסה הזו"
        blocked = blocked or self._can_launch()
        if blocked:
            self.ensure_note = f"ההכנה לא הופעלה בעליית השרת ({blocked})"
        else:
            ok, out = self._run(["systemd-run", f"--unit={UNIT}", "--collect",
                                 "bash", str(script)])
            if ok:
                self.ensure_note = (f"ההכנה ({SCRIPT.as_posix()}) הופעלה ברקע בעליית השרת — "
                                    f"הפירוט: journalctl -u {UNIT}")
            elif "already" in out and UNIT in out:
                self.ensure_note = f"ההכנה כבר רצה ברקע — journalctl -u {UNIT}"
            else:
                self.ensure_note = f"הפעלת ההכנה ברקע נכשלה: {out or 'systemd-run נכשל'}"
        log.warning("vmhost: %s — %s", why, self.ensure_note)

    def warning(self) -> str | None:
        why = self.problem()
        if why is None:
            return None
        return WARNING_PREFIX + why + (f" · {self.ensure_note}" if self.ensure_note else "")

    def health(self) -> tuple[str, str]:
        """(state, detail) לשורת "עדכון ב-VM" במסך הבריאות."""
        warning = self.warning()
        if warning is None:
            return "ok", "QEMU, ‏OVMF, ‏guestfs והמשתמש imagesrv-vm — מוכנים"
        return "warn", warning.removeprefix(WARNING_PREFIX)
