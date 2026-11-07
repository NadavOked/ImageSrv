"""‏#1956 — לאן גיבוי המסד נכתב, כל כמה מקום שמור לו ובאיזו שעה (הכרעת נדב, 06/11).

שני סוגי יעד:

1. **כונן השרת** (`default`) — ברירת המחדל, התיקייה הקבועה `<data_dir>/backups`. אין "לא נבחר": הגיבוי
   תמיד קיים. אזהרה כתומה קבועה "אותו כונן, לא מגן מתקלת כונן" — זו האמת.
2. **תיקייה מעוגנת** (`local`) — עותק על כונן אחר. על כונן המסד — אותה אזהרה; לא נבדק — כתום "לא נבדק".

בכל אחד מהם נכתב **ונקרא בחזרה** קובץ בדיקה בזמן ההגדרה (`write_probe`) — כשל מתגלה עכשיו,
לא בגיבוי הראשון (עיקרון 5: הצלחה = ראיה חיובית).

**‏SMB ו-NFS אינם יעד** — הם לאחסון האימג'ים בלבד. שרת שנשמר בו יעד כזה לפני ההכרעה ממשיך בבטחה:
הגיבוי עובר מיד לכונן השרת (`backup_job.effective_dest`), וההגדרה **נשארת** ב-DB — שורת ה-fstab
(`fstab_entry`), קובץ ה-credentials והשיתוף עצמו לא נוגעים בהם, והגיבויים הישנים נשארים עליו. ‏`load`
מחזיר את היעד הישן ב-`legacy`, והבריאות והקונסולה אומרות את זה בכתום עד שהמפעיל בוחר יעד
בקונסולה. רק הבחירה הזו מפרקת את העיגון (`_leave_network`); מהשיתוף לא נמחק דבר בשום מצב.

ההגדרות (key-value, בלי שינוי סכמה): `backup_dest` — התיקייה שנבחרה (ריק = כונן השרת),
‏`backup_target` — JSON בלי סוד, ‏`backup_time` ו-`backup_reserve_gb` (`set_policy`).
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from server.system import backup_job
from server.storage import locations as sl
from server.db import get_setting, journal, set_settings

SETTING = backup_job.TARGET_SETTING
KINDS = ("default", "local")
KIND_LABELS = {"default": "כונן השרת", "local": "תיקייה", "smb": "SMB", "nfs": "NFS"}
MOUNT_NAME = ".db-backup"
CREDS_ID = "backup"
SAME_DISK = "אותו כונן, לא מגן מתקלת כונן"
NOT_CHECKED = "לא נבדק אם התיקייה על כונן אחר מהמסד"
NO_NETWORK = "SMB ו-NFS משמשים רק לאחסון האימג'ים. הגיבויים נשמרים על כונן השרת, או בתיקייה מעוגנת"


class TargetError(Exception):
    """סירוב בשם: `status` הוא קוד ה-HTTP (400 קלט, 409 היעד לא עבר, 503 כלי חסר)."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


# --- הקריאה ------------------------------------------------------------------------


def _stored(conn) -> dict | None:
    try:
        target = json.loads(get_setting(conn, SETTING) or "null")
    except ValueError:
        return None
    return target if isinstance(target, dict) else None


def load(conn) -> dict:
    """היעד בתוקף. ‏`backup_dest` בלי `backup_target` (נבחר לפני #1956) = תיקייה שלא נבדק אם היא על
    כונן המסד (`same_disk: None`), לא "תקין". יעד SMB/NFS שנשמר — כונן השרת, והישן ב-`legacy`."""
    dest = (get_setting(conn, backup_job.SETTING) or "").strip()
    target = _stored(conn) or {}
    if target.get("type") in backup_job.LEGACY_KINDS:
        return {"type": "default", "dest": "", "legacy": target}
    if not dest:
        return {"type": "default", "dest": ""}
    if target.get("type") != "local":
        target = {"type": "local", "path": dest, "same_disk": None}
    return {**target, "dest": dest}


def legacy_display(legacy: dict) -> str:
    if legacy.get("type") == "smb":
        return f"//{legacy.get('server', '')}/{legacy.get('share', '')}"
    return f"{legacy.get('server', '')}:{legacy.get('export', '')}"


def describe(conn) -> dict:
    """מה שהקונסולה רואה (‏`GET /settings` → `backup_target`). אין כאן סוד — אין סוד ב-DB."""
    target = load(conn)
    out = {"type": target["type"], "label": KIND_LABELS[target["type"]], "dest": target["dest"],
           "display": target["dest"], "same_disk": True if target["type"] == "default" else target.get("same_disk")}
    legacy = target.get("legacy")
    if legacy:
        out["legacy"] = {"type": legacy["type"], "label": KIND_LABELS[legacy["type"]],
                         "display": legacy_display(legacy)}
    return out


def disk_warning(target: dict) -> str | None:
    """האזהרה הכתומה: יעד רשת שהוסר; כונן השרת; תיקייה על כונן המסד, או שלא נבדק. ‏None — כונן אחר."""
    legacy = target.get("legacy")
    if legacy:
        return (f"יעד הגיבוי {KIND_LABELS[legacy['type']]} {legacy_display(legacy)} הוסר: מגבים לכונן השרת. "
                "הגיבויים הישנים נשארו על השיתוף ולא נמחקו. בוחרים יעד בהגדרות כדי לנתק אותו")
    if target.get("type") == "default" or target.get("same_disk") is True:
        return SAME_DISK
    if target.get("same_disk") is None:
        return NOT_CHECKED
    return None


def fstab_entry(conn) -> dict | None:
    """שורת העיגון של יעד רשת **שנשמר לפני שהוסרו**, בפורמט של `storage_locations.fstab_entries` —
    כתיבה מחדש של fstab אינה מנתקת את השיתוף בשקט; הוא יורד רק כשהמפעיל בוחר יעד."""
    target = _stored(conn) or {}
    point = target.get("mount_point")
    if target.get("type") not in backup_job.LEGACY_KINDS or not point:
        return None
    if target["type"] == "nfs":
        return {"source": f"{target['server']}:{target['export']}", "target": point, "fstype": "nfs",
                "opts": sl._nfs_opts(target) + ",_netdev,nofail"}
    creds = sl.creds_path(CREDS_ID) if target.get("username") else ""
    return {"source": f"//{target['server']}/{target['share']}", "target": point, "fstype": "cifs",
            "opts": sl._smb_opts(target, creds) + ",_netdev,nofail"}


# --- "אותו דיסק" --------------------------------------------------------------------


def _disks(dev: int, sys_root: Path) -> set[str]:
    """הדיסקים השלמים שמאחורי התקן — `/sys/dev/block/<maj>:<min>`; מחיצה → ההורה; ‏dm/LVM/RAID →
    דרך `slaves/`. קבוצה ריקה = לא נקרא (לא "אין דיסק")."""
    try:
        node = (sys_root / "dev" / "block" / f"{os.major(dev)}:{os.minor(dev)}").resolve(strict=True)
    except (OSError, AttributeError):
        return set()
    return _whole(node, 0)


def _whole(node: Path, depth: int) -> set[str]:
    slaves = node / "slaves"
    try:
        below = sorted(slaves.iterdir()) if slaves.is_dir() else []
    except OSError:
        return set()
    if below and depth < 8:
        found: set[str] = set()
        for item in below:
            try:
                found |= _whole(item.resolve(strict=True), depth + 1)
            except OSError:
                return set()
        return found
    return {node.parent.name if (node / "partition").exists() else node.name}


def same_disk_devs(dev_a: int, dev_b: int, sys_root: Path = Path("/sys")) -> bool | None:
    """True/False לפי sysfs; ‏None = לא הצלחנו לקרוא אחד מהם (לא "דיסק אחר")."""
    if dev_a == dev_b:
        return True
    disks_a, disks_b = _disks(dev_a, sys_root), _disks(dev_b, sys_root)
    if not disks_a or not disks_b:
        return None
    return bool(disks_a & disks_b)


def same_disk(path: Path, data_dir: Path) -> bool | None:
    try:
        return same_disk_devs(os.stat(path).st_dev, os.stat(data_dir).st_dev)
    except OSError:
        return None


# --- קובץ הבדיקה --------------------------------------------------------------------


def _read_back(path: Path) -> bytes:
    return path.read_bytes()


def write_probe(folder: Path) -> None:
    """כותב קובץ בדיקה, ‏fsync, קורא אותו **בחזרה** ומשווה, ומוחק — ומוודא שנמחק. כל כשל — `TargetError` 409."""
    probe = folder / f".imagesrv-backup-test-{secrets.token_hex(4)}"
    payload = f"imagesrv backup test {secrets.token_hex(16)}\n".encode()
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
    except OSError as exc:
        raise TargetError(409, f"כתיבת קובץ הבדיקה ל-{folder} נכשלה: {exc}") from None
    why = ""            # ‏CodeQL #693: ‏`got is None` רק אחרי ה-except שקובע אותו
    try:
        got = _read_back(probe)
    except OSError as exc:
        got = None
        why = str(exc)
    finally:
        try:
            probe.unlink()
        except OSError as exc:
            raise TargetError(409, f"קובץ הבדיקה {probe} לא נמחק: {exc}") from None
    if got != payload:
        detail = why if got is None else f"{len(got)} בתים במקום {len(payload)}, תוכן אחר"
        raise TargetError(409, f"קובץ הבדיקה ב-{folder} לא נקרא בחזרה כפי שנכתב ({detail})")
    if probe.exists():
        raise TargetError(409, f"קובץ הבדיקה {probe} עדיין קיים אחרי מחיקה")


# --- ההגדרה ----------------------------------------------------------------------------


def _leave_network(conn, data_dir: Path, hooks: dict) -> list[str]:
    """פירוק יעד רשת שנשמר: umount, שורת ה-fstab (כתיבה מחדש אחרי שההגדרה כבר הוחלפה), credentials.
    מהשיתוף לא נמחק דבר. מחזיר אזהרה על כל שלב שלא הצליח — ההגדרה כבר הוחלפה, ולכן לא חריגה."""
    steps = (("הניתוק", hooks["umount"](str(Path(data_dir) / "storage" / MOUNT_NAME))),
             ("הסרת השורה מ-fstab", sl.rewrite_fstab(conn, hooks)),
             ("מחיקת קובץ ה-credentials", hooks["creds_remove"](sl.creds_path(CREDS_ID))))
    return [f"היעד הישן: {what} נכשל: {result.reason or result.status}"
            for what, result in steps if result.status != sl.OK]


def configure(conn, data_dir: Path, body: object, hooks: dict, user: str = "") -> dict:
    """‏`POST /api/console/backup-target`. מחזיר {target, warnings}; סירוב — `TargetError`."""
    kind = body.get("type") if isinstance(body, dict) else None
    if kind in backup_job.LEGACY_KINDS:
        raise TargetError(400, NO_NETWORK)
    if kind not in KINDS:
        raise TargetError(400, "יעד הגיבוי: סוג לא מוכר — default / local")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise TargetError(400, "יעד הגיבוי: params חייב להיות אובייקט")
    data_dir = Path(data_dir)
    legacy = load(conn).get("legacy")

    if kind == "default":
        folder = data_dir / backup_job.DEFAULT_DIR
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TargetError(409, f"התיקייה {folder} לא נוצרה: {exc}") from None
        write_probe(folder)
        target, dest = {"type": "default"}, ""
    else:
        dest, why = backup_job.check_dest(params.get("path", ""))
        if why is not None or not dest:
            raise TargetError(400, why or "יעד הגיבוי: חסר נתיב תיקייה")
        write_probe(Path(dest))
        target = {"type": "local", "path": dest, "same_disk": same_disk(Path(dest), data_dir)}

    set_settings(conn, {backup_job.SETTING: dest, SETTING: json.dumps(target, ensure_ascii=False)})
    warning = disk_warning(load(conn))
    warnings = [warning] if warning else []
    if legacy:
        warnings += _leave_network(conn, data_dir, hooks)
    journal(conn, "setting_change", f"{SETTING}={kind} — {dest or KIND_LABELS[kind]}", user)
    return {"target": describe(conn), "warnings": warnings}


# --- המדיניות: שעה ומקום שמור -----------------------------------------------------------


def _cap_gb(conn, data_dir: Path | None) -> int | None:
    """התקרה ב-GB שלמים על הכונן של היעד; ‏None = לא נבדק."""
    if data_dir is None:
        return None
    dest = backup_job.effective_dest(get_setting(conn, backup_job.SETTING), get_setting(conn, SETTING), data_dir)
    try:
        return backup_job.drive_cap_bytes(Path(dest)) // backup_job.GB
    except (OSError, StopIteration):
        return None


def policy(conn, data_dir: Path | None) -> dict:
    """מה שהקונסולה רואה (‏`GET /settings` → `backup_policy`): הערכים שבתוקף, והתקרה לפי הכונן."""
    return {"time": backup_job.parse_time(get_setting(conn, backup_job.TIME_SETTING)) or backup_job.DEFAULT_TIME,
            "reserve_gb": backup_job.parse_reserve(get_setting(conn, backup_job.RESERVE_SETTING))
            or backup_job.DEFAULT_RESERVE_GB,
            "min_gb": backup_job.MIN_RESERVE_GB, "cap_gb": _cap_gb(conn, data_dir),
            "cap_percent": backup_job.CAP_PERCENT}


def set_policy(conn, data_dir: Path, body: object, user: str = "") -> dict:
    """‏`POST /api/console/backup-policy`. סירוב — `TargetError`, ושום דבר לא נשמר."""
    if not isinstance(body, dict):
        raise TargetError(400, "מדיניות הגיבוי: נדרש אובייקט")
    at = backup_job.parse_time(body.get("time"))
    if at is None:
        raise TargetError(400, "שעת הגיבוי: נדרש HH:MM")
    reserve = backup_job.parse_reserve(body.get("reserve_gb"))
    if reserve is None:
        raise TargetError(400, f"מקום שמור: מספר שלם של GB, לפחות {backup_job.MIN_RESERVE_GB}")
    cap = _cap_gb(conn, Path(data_dir))
    if cap is None:
        raise TargetError(409, "לא הצלחנו לקרוא את גודל הכונן — לא נשמר")
    if reserve > cap:
        raise TargetError(400, f"מקום שמור: לא יותר מ-{backup_job.CAP_PERCENT}% מהכונן (עד {cap} GB)")
    set_settings(conn, {backup_job.TIME_SETTING: at, backup_job.RESERVE_SETTING: str(reserve)})
    journal(conn, "setting_change", f"backup_policy={at}, {reserve} GB", user)
    return {"policy": policy(conn, Path(data_dir))}
