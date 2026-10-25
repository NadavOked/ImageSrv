"""‏#1956 — בחירת יעד הגיבוי השעתי של המסד (הכרעת נדב, 22/10).

שלושה סוגי יעד:

1. **תיקייה מקומית** — ואם היא על הדיסק של המסד: אזהרה כתומה "אותו דיסק — לא מגן מתקלת
   דיסק" (בתשובה, בשורת ההגדרות ובשורת "גיבוי המסד" בבריאות).
2. **שיתוף SMB** עם משתמש וסיסמה.
3. **NFS**.

בכל אחד מהם נכתב **ונקרא בחזרה** קובץ בדיקה בזמן ההגדרה (`write_probe`) — כשל מתגלה עכשיו,
לא בגיבוי הראשון בעוד שעה (עיקרון 5: הצלחה = ראיה חיובית).

**לא מנגנון חדש** — הכול על התשתית של מיקומי האחסון (#1066/#1123, ‏`storage_locations`):
אותם hooks מוזרקים (`mount`/`umount`/`fstab_write`/`creds_write`/`creds_remove`, בבדיקות
מזויפים), אותה בדיקת פרמטרים (`validate_params`), אותן אפשרויות עיגון (`_smb_opts`/`_nfs_opts`),
ואותו בלוק `# BEGIN imagectl-storage` ב-fstab — `storage_locations.fstab_entries` מצרף את שורת
הגיבוי, כך שכל כתיבה מחדש של fstab (גם של מיקום אחסון) שומרת אותה. **סיסמת SMB** נשמרת כמו
של מיקום אחסון: קובץ `credentials=` ‏0600 ב-`/etc/imagectl/creds/backup` (‏`creds_path`),
ולא ב-DB, לא ב-argv ולא באפשרויות העיגון.

נקודת העיגון: `<data_dir>/storage/.db-backup` (ובדיקה: `.db-backup-test`) — תחת `storage/`,
שגם גיבוי ההגדרות (`backup.SKIP_DIRS`) וגם `tools/backup-server.sh` כבר מדלגים עליה (אחרת
הגיבוי היה מעתיק את ה-NAS לתוך עצמו).

ההגדרות (key-value, בלי שינוי סכמה): `backup_dest` — הנתיב שהגיבוי כותב אליו (כמו ב-#1895),
ו-`backup_target` — JSON בלי סוד: `{type, ...פרמטרים ציבוריים, mount_point, same_disk}`.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from . import backup_job
from . import storage_locations as sl
from .db import get_setting, journal, set_settings

SETTING = "backup_target"
KINDS = ("none", "local", "smb", "nfs")
NETWORK = ("smb", "nfs")
KIND_LABELS = {"none": "לא נבחר", "local": "תיקייה", "smb": "SMB", "nfs": "NFS"}
MOUNT_NAME = ".db-backup"
TEST_NAME = ".db-backup-test"
CREDS_ID = "backup"
TEST_CREDS_ID = "backup-test"
SAME_DISK = "אותו דיסק — לא מגן מתקלת דיסק"
NOT_CHECKED = "לא נבדק אם התיקייה על דיסק אחר מהמסד"
PUBLIC_KEYS = {"local": ("path",), "smb": ("server", "share", "username", "domain"),
               "nfs": ("server", "export", "version")}


class TargetError(Exception):
    """סירוב בשם: `status` הוא קוד ה-HTTP (400 קלט, 409 היעד לא עבר, 503 כלי חסר)."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


# --- הקריאה ------------------------------------------------------------------------


def load(conn) -> dict:
    """היעד השמור. ‏`backup_dest` בלי `backup_target` (נבחר לפני #1956) = תיקייה שלא נבדק
    אם היא על דיסק המסד (`same_disk: None`), לא "תקין"."""
    dest = (get_setting(conn, backup_job.SETTING) or "").strip()
    try:
        target = json.loads(get_setting(conn, SETTING) or "null")
    except ValueError:
        target = None
    if not isinstance(target, dict) or target.get("type") not in KINDS:
        target = {"type": "local", "path": dest, "same_disk": None} if dest else {"type": "none"}
    if not dest:
        target = {"type": "none"}
    target["dest"] = dest
    return target


def display(target: dict) -> str:
    kind = target.get("type")
    if kind == "smb":
        return f"//{target.get('server', '')}/{target.get('share', '')}"
    if kind == "nfs":
        return f"{target.get('server', '')}:{target.get('export', '')}"
    return target.get("dest") or target.get("path") or ""


def describe(conn) -> dict:
    """מה שהקונסולה רואה (‏`GET /settings` → `backup_target`). אין כאן סוד — אין סוד ב-DB."""
    target = load(conn)
    out = {"type": target["type"], "label": KIND_LABELS[target["type"]], "dest": target["dest"],
           "display": display(target), "same_disk": target.get("same_disk")}
    for key in PUBLIC_KEYS.get(target["type"], ()):
        out[key] = target.get(key) or ""
    return out


def disk_warning(target: dict) -> str | None:
    """האזהרה הכתומה של תיקייה מקומית: על דיסק המסד, או שלא נבדק. ‏None — דיסק אחר / רשת."""
    if target.get("type") != "local":
        return None
    if target.get("same_disk") is True:
        return SAME_DISK
    if target.get("same_disk") is None:
        return NOT_CHECKED
    return None


def fstab_entry(conn) -> dict | None:
    """שורת העיגון הקבוע של יעד רשת, בפורמט של `storage_locations.fstab_entries`."""
    target = load(conn)
    point = target.get("mount_point")
    if target["type"] not in NETWORK or not point:
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
    probe = folder / f".imagectl-backup-test-{secrets.token_hex(4)}"
    payload = f"imagesrv backup test {secrets.token_hex(16)}\n".encode()
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
    except OSError as exc:
        raise TargetError(409, f"כתיבת קובץ הבדיקה ל-{folder} נכשלה: {exc}") from None
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


def _hook(result, what: str) -> None:
    if result.status == sl.OK:
        return
    raise TargetError(503 if result.status == sl.UNCHECKED else 409, f"{what}: {result.reason or result.status}")


def _mount_and_probe(kind: str, params: dict, point: Path, creds_id: str, hooks: dict) -> str:
    """עיגון ל-`point` (‏credentials ‏0600 ל-SMB) + קובץ בדיקה. מחזיר את נתיב ה-credentials ("" בלי).
    בכשל — מפרק את מה שהקים וזורק."""
    creds = sl.smb_creds(params) if kind == "smb" else {}
    creds_file = sl.creds_path(creds_id) if creds else ""
    if creds_file:
        _hook(hooks["creds_write"](creds_file, creds), "קובץ ה-credentials לא נכתב")
    try:
        point.mkdir(parents=True, exist_ok=True)
        if kind == "smb":
            mounted = hooks["mount"]("cifs", f"//{params['server']}/{params['share']}", str(point),
                                     sl._smb_opts(params, creds_file))
        else:
            mounted = hooks["mount"]("nfs", f"{params['server']}:{params['export']}", str(point),
                                     sl._nfs_opts(params))
        _hook(mounted, "העיגון נכשל")
        try:
            if not hooks["is_mount"](str(point)):
                raise TargetError(409, f"העיגון דווח כמוצלח אבל {point} אינו נקודת עיגון — "
                                       "קובץ הבדיקה היה נכתב לדיסק של השרת")
            write_probe(point)
        except TargetError:
            hooks["umount"](str(point))
            raise
    except Exception:
        if creds_file:
            hooks["creds_remove"](creds_file)
        raise
    return creds_file


def _leave_network(conn, data_dir: Path, hooks: dict) -> None:
    """פירוק יעד רשת קודם: umount, שורת ה-fstab (כתיבה מחדש אחרי שההגדרה כבר הוחלפה), credentials."""
    hooks["umount"](str(Path(data_dir) / "storage" / MOUNT_NAME))
    sl.rewrite_fstab(conn, hooks)
    hooks["creds_remove"](sl.creds_path(CREDS_ID))


def _public(kind: str, params: dict) -> dict:
    return {key: str(params.get(key) or "").strip() for key in PUBLIC_KEYS[kind]}


def configure(conn, data_dir: Path, body: object, hooks: dict, user: str = "") -> dict:
    """‏`POST /api/console/backup-target`. מחזיר {target, warnings}; סירוב — `TargetError`."""
    if not isinstance(body, dict) or body.get("type") not in KINDS:
        raise TargetError(400, "יעד הגיבוי: סוג לא מוכר — none / local / smb / nfs")
    kind = body["type"]
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise TargetError(400, "יעד הגיבוי: params חייב להיות אובייקט")
    data_dir = Path(data_dir)
    previous = load(conn)
    warnings: list[str] = []

    if kind == "none":
        target, dest = {"type": "none"}, ""
    elif kind == "local":
        dest, why = backup_job.check_dest(params.get("path", ""))
        if why is not None or not dest:
            raise TargetError(400, why or "יעד הגיבוי: חסר נתיב תיקייה")
        write_probe(Path(dest))
        target = {"type": "local", "path": dest, "same_disk": same_disk(Path(dest), data_dir)}
        warning = disk_warning(target)
        if warning:
            warnings.append(warning)
    else:
        params = {**params, **{k: str(params.get(k) or "").strip() for k in ("server", "share", "export")}}
        reason = sl.validate_params(kind, params)   # שרת, שיתוף/ייצוא, בלי תווי בקרה — לפני כל כלי
        if reason:
            raise TargetError(400, f"יעד הגיבוי: {reason}")
        # שלב 1: עיגון בדיקה בנקודה נפרדת — היעד הקודם לא נפגע אם החדש לא עובד.
        test_point = data_dir / "storage" / TEST_NAME
        test_creds = _mount_and_probe(kind, params, test_point, TEST_CREDS_ID, hooks)
        hooks["umount"](str(test_point))
        if test_creds:
            hooks["creds_remove"](test_creds)
        # שלב 2: הנקודה הקבועה.
        point = data_dir / "storage" / MOUNT_NAME
        if previous["type"] in NETWORK:
            hooks["umount"](str(point))
        _mount_and_probe(kind, params, point, CREDS_ID, hooks)
        dest = point.as_posix()
        target = {"type": kind, **_public(kind, params), "mount_point": dest}
        if kind == "nfs" and not target.get("version"):
            target["version"] = "4.1"

    before = {backup_job.SETTING: previous["dest"],
              SETTING: get_setting(conn, SETTING) or ""}
    set_settings(conn, {backup_job.SETTING: dest, SETTING: json.dumps(target, ensure_ascii=False)})
    if kind in NETWORK:
        written = sl.rewrite_fstab(conn, hooks)
        if written.status != sl.OK:
            hooks["umount"](str(Path(dest)))
            hooks["creds_remove"](sl.creds_path(CREDS_ID))
            if before[SETTING]:
                set_settings(conn, before)
            else:
                set_settings(conn, {backup_job.SETTING: before[backup_job.SETTING]}, remove=(SETTING,))
            lost = " · היעד הקודם נותק — יש לבחור אותו מחדש" if previous["type"] in NETWORK else ""
            raise TargetError(409, f"העיגון הצליח אבל fstab לא נכתב — בוטל, היעד לא נשמר: {written.reason}{lost}")
    elif previous["type"] in NETWORK:
        _leave_network(conn, data_dir, hooks)
    journal(conn, "setting_change", f"{SETTING}={kind} — {display({**target, 'dest': dest}) or 'ללא'}", user)
    return {"target": describe(conn), "warnings": warnings}
