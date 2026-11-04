"""‏#2174: הניסיון החוזר של dnsmasq (‏#1816) מותאם בכל עליית שרת.

‏`deploy/dnsmasq-imagesrv-restart.conf` הוא drop-in של systemd
(‏`Restart=on-failure`): כרטיס הפצה שעולה אחרי `network.target` (‏USB, ‏VLAN)
מחזיר את dnsmasq בלי לוותר על `bind-interfaces`. המתקין כותב אותו, ו-
‏`tools/server-upgrade.sh` כותב אותו בעדכון — אבל עדכון מהכפתור מריץ את
הסקריפט של התג **הישן** (‏#2350), ולכן שרת שמשתדרג מגרסה שאין בה את הצעד
לא היה מקבל אותו לעולם. הקוד ב-/opt/imagesrv כן מתחלף בכל עדכון, ולכן
ההתאמה יושבת כאן: בעלייה, הקובץ מושווה למקור שבעץ — זהה ו-systemd רואה
אותו = כלום; חסר/שונה = כתיבה אטומית ו-`daemon-reload`.

‏`daemon-reload` ולא restart ל-dnsmasq: מדיניות ה-Restart נקראת בטעינת
היחידה, ו-restart היה מנתק DHCP בלי צורך.

הצלחה נקבעת לפי **ראיה חיובית** (עיקרון 5): ‏`DropInPaths` של dnsmasq,
כפי ש-systemd מדווח, מכיל את הקובץ — לא היעדר שגיאה. כך גם reload שנכשל
בעלייה קודמת (הקובץ זהה, systemd לא טען) מתוקן בעלייה הבאה ולא נקרא "זהה".
כל תוצאה שאינה "כבר תקין" היא שורת יומן; כשל אינו מפיל את השרת —
‏dnsmasq עובד גם בלעדיו כשהכרטיס כבר קיים — אבל נאמר בשם.

הקובץ הוא שלנו (‏`imagesrv-restart.conf`) ונדרס, כמו במתקין ובעדכון.
מי שרוצה מדיניות אחרת כותב drop-in בשם משלו ב-`dnsmasq.service.d/`.
‏`IMAGESRV_UNIT_DIR` — אותו משתנה כמו ב-server-upgrade.sh — לבדיקות.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable

from server.core.atomicfile import write_atomic
from server.db import journal
from server.paths import REPO_ROOT

UNIT = "dnsmasq.service"
SOURCE = Path("deploy") / "dnsmasq-imagesrv-restart.conf"
TARGET = Path("dnsmasq.service.d") / "imagesrv-restart.conf"

Hooks = dict[str, Callable]


def unit_dir() -> Path:
    return Path(os.environ.get("IMAGESRV_UNIT_DIR") or "/etc/systemd/system")


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", *args], capture_output=True, text=True,
                          timeout=60, check=False, stdin=subprocess.DEVNULL)


def _loaded_dropins() -> list[str] | str:
    """ה-drop-ins ש-systemd טען ל-dnsmasq — רשימה, או הודעת שגיאה."""
    try:
        result = _systemctl("show", UNIT, "--property=DropInPaths", "--value")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"systemctl show {UNIT} לא רץ: {exc}"
    if result.returncode != 0:
        return (f"systemctl show {UNIT} נכשל (קוד {result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[:200]}")
    return result.stdout.split()


def _daemon_reload() -> str | None:
    try:
        result = _systemctl("daemon-reload")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"systemctl daemon-reload לא רץ: {exc}"
    if result.returncode != 0:
        return (f"systemctl daemon-reload נכשל (קוד {result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[:200]}")
    return None


def default_hooks() -> Hooks:
    return {"dropins": _loaded_dropins, "daemon_reload": _daemon_reload}


def ensure(hooks: Hooks, repo_dir: str | Path | None = None) -> tuple[str, str]:
    """(event, detail): ‏`""` = כבר תקין; אחרת אירוע היומן ופירוטו."""
    source = Path(repo_dir or REPO_ROOT) / SOURCE
    target = unit_dir() / TARGET
    try:
        wanted = source.read_bytes()
    except OSError as exc:
        return "dnsmasq_dropin_failed", f"המקור {source} לא נקרא: {exc}"
    try:
        current = target.read_bytes()
    except FileNotFoundError:
        current = None
    except OSError as exc:
        return "dnsmasq_dropin_failed", f"{target} לא נקרא: {exc}"

    if current != wanted:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(target, wanted, mode=0o644)
        except OSError as exc:
            return "dnsmasq_dropin_failed", f"{target} לא נכתב: {exc}"

    loaded = hooks["dropins"]()
    if isinstance(loaded, str):
        return "dnsmasq_dropin_failed", f"לא נבדק אם systemd טען את {target}: {loaded}"
    if current == wanted and str(target) in loaded:
        return "", str(target)

    error = hooks["daemon_reload"]()
    if error:
        return "dnsmasq_dropin_failed", f"{target} על הדיסק, אבל {error}"
    loaded = hooks["dropins"]()
    if isinstance(loaded, str):
        return "dnsmasq_dropin_failed", f"לא נבדק אם systemd טען את {target}: {loaded}"
    if str(target) not in loaded:
        return ("dnsmasq_dropin_failed",
                f"{target} על הדיסק, אבל systemd אינו מדווח אותו ב-DropInPaths של {UNIT} "
                f"אחרי daemon-reload ({' '.join(loaded) or 'ריק'})")
    if current == wanted:
        return "dnsmasq_dropin_applied", f"{target} היה על הדיסק ולא נטען; נטען עכשיו ל-{UNIT}"
    what = "נוסף" if current is None else "עודכן"
    return "dnsmasq_dropin_applied", f"{target} {what} ונטען ל-{UNIT}"


def apply_on_start(conn: sqlite3.Connection, hooks: Hooks,
                   repo_dir: str | Path | None = None) -> None:
    """עליית השרת: כל תוצאה שאינה "כבר תקין" — שורת יומן, וגם ביומן המערכת."""
    event, detail = ensure(hooks, repo_dir)
    if not event:
        print(f"dnsmasq retry drop-in (#1816): up to date, loaded ({detail})", flush=True)
        return
    print(f"dnsmasq retry drop-in (#1816): {event}: {detail}", flush=True)
    journal(conn, event, detail)
