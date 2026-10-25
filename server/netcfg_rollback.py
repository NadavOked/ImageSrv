"""ההחזרה האוטומטית של תצורת רשת שלא אושרה (‏#56).

שינוי כתובת על הכרטיס שדרכו המנהל מחובר מנתק אותו מהקונסולה — ואז אין
דרך לבטל. הדפוס הוא של Cisco ו-MikroTik: מחילים, ואם לא אושר תוך דקה
שהחיבור עדיין חי — חוזרים.

**זה אינו תהליכון ב-uvicorn, ולא במקרה.** שינוי שהפיל את הרשת מפיל
לעיתים גם את השירות, ואז מי שאמור להחזיר כבר לא רץ. לכן:

* ההגדרה הקודמת נשמרת **לדיסק** בסמן `pending.json` — ‏*הטקסט* הקודם
  של כל קובץ שנגענו בו, לא הפרשי-הגדרות.
* המודול הזה רץ כיחידת systemd עצמאית (`imagectl-netrollback.service`)
  שטיימר מפעיל כל עשר שניות. הוא אינו מייבא FastAPI, אינו נוגע ב-DB,
  ורץ מצוין כשהשרת מת.
* הטיימר נורה גם ב-`OnBootSec=0`, ולכן **הבדיקה רצה גם בעלייה**.

`boot_id` הוא מה שהופך את בדיקת העלייה למדויקת: הסמן נושא את מזהה
האתחול שבו הוא נכתב. מזהה שונה פירושו שהמכונה אותחלה בזמן שהשינוי עוד
לא אושר — ואז מחזירים **מיד**, בלי לחכות לפקיעה, כי אתחול עם שינוי לא
מאושר הוא בדיוק התסמין של השינוי שהפיל הכל.

**היעדר ניתוק אינו אישור** (עיקרון 5). אם לא נאמר במפורש "החיבור חי" —
מחזירים. וכיוון שההחזרה קורית כשאיש לא מסתכל, היא משאירה **פירור** על
הדיסק; השרת ממיר אותו לשורת יומן בהפעלה הבאה, עם זמן ההחזרה האמיתי.
אירוע שקרה כשאיש לא הסתכל וגם לא השאיר ראיה שקול ללא-קרה.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import netcfg_host
from .atomicfile import write_atomic

PENDING_NAME = "pending.json"
CRUMB_SUFFIX = ".rollback.json"
SCHEMA = 1

#: כמה זמן יש למפעיל לומר "אני עדיין רואה את הקונסולה".
WINDOW_SECONDS = 60

REASON_EXPIRED = "expired"
REASON_BOOT = "boot"
REASON_HE = {
    REASON_EXPIRED: "לא אושר בזמן",
    REASON_BOOT: "המכונה אותחלה לפני שהשינוי אושר",
}


def boot_id(path: str | Path = "/proc/sys/kernel/random/boot_id") -> str:
    """מזהה האתחול הנוכחי, או "" אם אי אפשר לקרוא.

    ‏"" נשמר בסמן כמו כל ערך אחר, והשוואה בין שני "" אינה מפעילה החזרה
    בעלייה — במכונה בלי `/proc` (פיתוח, ווינדוס) נשארת רק הפקיעה.
    """
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


# --- הסמן --------------------------------------------------------------------


@dataclass(frozen=True)
class Pending:
    """מה שצריך כדי להחזיר, גם בלי DB וגם בלי השרת.

    ‏`files` הוא [{"name": כרטיס, "text": הטקסט הקודם או None}]. ‏None
    פירושו "לא היה קובץ" — והחזרה תמחק אותו, ולא תשאיר קובץ ריק שנראה
    מנוהל.
    """

    interface: str
    deadline: float
    armed_at: str
    boot: str
    files: list[dict]
    resolv: str | None
    #: ה-JSON של ההגדרה כפי שהייתה. הזרוע אינה נוגעת ב-DB, אבל היא
    #: מעבירה אותו הלאה בפירור — כדי שהשרת יחזיר גם את מה שהקונסולה
    #: מציגה, ולא רק את מה שעל הכרטיס.
    setting: str | None = None
    interfaces_dir: str = netcfg_host.INTERFACES_DIR
    resolv_path: str = netcfg_host.RESOLV_CONF

    def to_json(self) -> str:
        return json.dumps({"schema": SCHEMA, **self.__dict__}, ensure_ascii=False)


def pending_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / PENDING_NAME


def read_pending(state_dir: str | Path) -> Pending | None:
    """הסמן, או None — גם כשהוא לא קיים וגם כשאי אפשר לפענח אותו.

    סמן פגום אינו מפעיל החזרה: אין לנו את הטקסט הקודם, והחזרה שמנחשת
    גרועה מהעדר החזרה. הוא כן נמחק ומדווח כפירור בלתי-קריא, כדי שלא
    יישאר בשקט לנצח.
    """
    try:
        data = json.loads(pending_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return Pending(
            interface=str(data["interface"]),
            deadline=float(data["deadline"]),
            armed_at=str(data.get("armed_at", "")),
            boot=str(data.get("boot", "")),
            files=list(data["files"]),
            resolv=data.get("resolv"),
            setting=data.get("setting"),
            interfaces_dir=str(data.get("interfaces_dir",
                                        netcfg_host.INTERFACES_DIR)),
            resolv_path=str(data.get("resolv_path", netcfg_host.RESOLV_CONF)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_pending(state_dir: str | Path, marker: Pending) -> str | None:
    path = pending_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ‏#2160: הסמן מעתיק את טקסט ההגדרה ואת resolv.conf — ‏0600, לא 0644 של קובץ חדש.
        write_atomic(path, marker.to_json(), mode=0o600)
    except OSError as exc:
        return f"לא ניתן לכתוב את סמן ההחזרה {path}: {exc.strerror or exc}"
    return None


def clear_pending(state_dir: str | Path) -> None:
    try:
        pending_path(state_dir).unlink(missing_ok=True)
    except OSError:
        # לא נבלע: מי שמדווח "אושר" בודק `pending_path().exists()` אחרי
        # הקריאה (‏#291); ‏`run_once` שנכשל כאן פשוט מחזיר שוב בסבב הבא.
        pass


def corrupt_pending(state_dir: str | Path) -> bool:
    """קובץ סמן קיים שאי אפשר לפענח. שני מצבים שאסור לקפל לאחד."""
    return pending_path(state_dir).exists() and read_pending(state_dir) is None


# --- ההחלטה (טהורה) ----------------------------------------------------------


def decide(marker: Pending | None, now: float, boot: str) -> str | None:
    """למה להחזיר עכשיו — או None. ‏**זו כל הלוגיקה**, והיא טהורה.

    סדר הבדיקות מכוון: אתחול קודם לפקיעה, כדי שהפירור יסביר את הסיבה
    האמיתית גם כשגם הזמן עבר.
    """
    if marker is None:
        return None
    if marker.boot and boot and marker.boot != boot:
        return REASON_BOOT
    if now >= marker.deadline:
        return REASON_EXPIRED
    return None


def expired(marker: Pending | None, now: float) -> bool:
    """האם חלון האישור נסגר. אישור אחרי הרגע הזה אינו מבטל החזרה."""
    return marker is not None and now >= marker.deadline


# --- הפירור ------------------------------------------------------------------


def write_crumb(state_dir: str | Path, interface: str, reason: str,
                at: str, errors: list[str], setting: str | None = None) -> None:
    """מה שקרה, לקריאה בהפעלה הבאה של השרת. כישלון בכתיבה אינו עוצר
    את ההחזרה עצמה — עדיף רשת שחזרה בלי שורת יומן מאשר ההפך."""
    path = Path(state_dir) / f"{at.replace(':', '-')}{CRUMB_SUFFIX}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(
            {"schema": SCHEMA, "interface": interface, "reason": reason,
             "at": at, "errors": errors, "setting": setting},
            ensure_ascii=False))
    except OSError:
        # מכוון (ה-docstring): ההחזרה כבר קרתה, ו-`main` מדפיס אותה ליומן
        # של systemd גם כשהפירור לא נכתב.
        pass


def read_crumbs(state_dir: str | Path) -> list[dict | None]:
    """הפירורים, ישן→חדש. ‏`None` = פירור שלא ניתן לפענח — הוא מדווח
    ככזה ולא נבלע בשקט."""
    root = Path(state_dir)
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob(f"*{CRUMB_SUFFIX}")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found.append(None)
            continue
        found.append(data if isinstance(data, dict) else None)
    return found


def clear_crumbs(state_dir: str | Path) -> None:
    for path in Path(state_dir).glob(f"*{CRUMB_SUFFIX}"):
        try:
            path.unlink()
        except OSError:
            # פירור שלא נמחק נקרא שוב ב-`drain_crumbs` הבא — כפילות גלויה ביומן,
            # לא אירוע שנעלם.
            pass


# --- ההחזרה עצמה -------------------------------------------------------------


def restore(marker: Pending, hooks: dict | None = None) -> list[str]:
    """מחזיר את הקבצים הקודמים ומרים את הכרטיס. מחזיר רשימת שגיאות.

    הכתיבה קודמת לכל `ifup`: תצורה שחזרה לדיסק שורדת גם אתחול שיקרה
    מיד אחרי, גם אם ההרמה כאן נכשלה.
    """
    hooks = {**netcfg_host.default_hooks(), **(hooks or {})}
    errors = []
    for entry in marker.files:
        error = hooks["netcfg_write_conf"](entry.get("name", marker.interface),
                                           entry.get("text"),
                                           marker.interfaces_dir)
        if error:
            errors.append(error)
    if marker.resolv is not None:
        error = hooks["netcfg_write_resolv"](marker.resolv, marker.resolv_path)
        if error:
            errors.append(error)
    error = hooks["netcfg_apply"](marker.interface)
    if error:
        errors.append(error)
    return errors


def run_once(state_dir: str | Path, hooks: dict | None = None,
             now: float | None = None, boot: str | None = None) -> str | None:
    """סבב אחד של הזרוע: להחליט, להחזיר, לנקות, להשאיר פירור.

    מחזיר את סיבת ההחזרה או None. הסמן נמחק **אחרי** ההחזרה: אם המכונה
    מתה באמצע, הסבב הבא (או האתחול הבא) יראה סמן פתוח ויחזור שוב —
    החזרה חוזרת על עצמה היא בטוחה, החזרה שלא קרתה אינה.
    """
    now = time.time() if now is None else now
    boot = boot_id() if boot is None else boot
    if corrupt_pending(state_dir):
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        write_crumb(state_dir, "", "", stamp, ["סמן ההחזרה לא ניתן לפענוח"])
        clear_pending(state_dir)
        return None
    marker = read_pending(state_dir)
    reason = decide(marker, now, boot)
    if reason is None or marker is None:
        return None
    errors = restore(marker, hooks)
    stamp = datetime.fromtimestamp(now, timezone.utc).astimezone().isoformat(
        timespec="seconds")
    write_crumb(state_dir, marker.interface, reason, stamp, errors,
                marker.setting)
    clear_pending(state_dir)
    return reason


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ImageSrv: restore an unconfirmed network change (#56)")
    parser.add_argument("--state-dir", default=netcfg_host.STATE_DIR)
    args = parser.parse_args(argv)
    reason = run_once(args.state_dir)
    if reason:
        print(f"imagectl: network configuration rolled back ({reason})",
              flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(main())
