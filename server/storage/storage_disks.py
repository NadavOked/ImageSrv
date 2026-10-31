""""הוסף כונן" (#1988) — כונן ריק בשרת הופך למיקום אחסון `local`.

הכרעת נדב 22/10: ברשימה רק כוננים ריקים לפי ראיה (‏`blkid -p` וקריאת ראש
הכונן וסופו); אישור בכפתור אדום "כל המידע בכונן יימחק"; השרת מחלק, מכין
ext4 ומחבר קבוע לפי UUID (fstab עם `nofail`); המיקום נבדק בקריאה חוזרת;
כשל באמצע מוצג בגלוי ולא נשאר מיקום חלקי.

הזרימה כאן משותפת לקונסולה (`console_storage_locations`) ולמסך השרת
(`dcui`). כלי המערכת רק דרך ה-hooks של `storage_locations` — ‏`blank_disks`,
‏`prepare_disk`, ‏`mount`, ‏`umount`, ‏`df`, ‏`fstab_write` — והבדיקות מזייפות
את כולם.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from server.storage import storage_locations as sl
from server.db import _settle, _write_lock, journal, now_iso, writing

#: קובץ נעילה ב-data_dir: הקונסולה ומסך השרת הם שני תהליכים.
LOCK_NAME = "add-disk.lock"
#: נעילה ישנה מזה (קריסה באמצע) אינה חוסמת לעד. הוספה אמיתית אורכת דקות.
LOCK_STALE_S = 3600
READBACK_NAME = ".imagesrv-readback"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_thread_lock = threading.Lock()


class AddDiskError(Exception):
    """כשל עם קוד HTTP והודעה למפעיל (עברית). המסך מציג אותה כמו שהיא."""

    def __init__(self, status: int, message: str, en: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        #: מסך הטקסט של השרת (tty1) הוא ASCII — שם מוצג המשפט הזה.
        self.en = en or "Adding the disk failed; no storage location was added."


def blank_disks(hooks: dict) -> dict:
    """``{"items": [...], "hidden": [...]}`` — או AddDiskError. "לא הצלחנו
    לבדוק" אינו רשימה ריקה (עיקרון 5)."""
    result = hooks["blank_disks"]()
    if result.status == sl.UNCHECKED:
        raise AddDiskError(503, result.reason or "לא הצלחנו לקרוא את רשימת הכוננים",
                           "The disk list could not be read (unchecked).")
    if result.status != sl.OK:
        raise AddDiskError(409, result.reason or "קריאת רשימת הכוננים נכשלה",
                           "Reading the disk list failed.")
    payload = result.payload or {}
    return {"items": list(payload.get("items") or []),
            "hidden": list(payload.get("hidden") or [])}


def _acquire(data_dir: Path) -> Path:
    if not _thread_lock.acquire(blocking=False):
        raise AddDiskError(409, "הוספת כונן אחרת רצה עכשיו — נסו שוב כשתסתיים",
                           "Another disk is being added right now.")
    lock = Path(data_dir) / LOCK_NAME
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                lock.unlink()
        except FileNotFoundError:
            pass
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            # ‏#2160: הקובץ כבר קיים (O_EXCL) וריק. בלי להסיר אותו, כל הוספה עד
            # LOCK_STALE_S הייתה חוטפת "כונן אחר נוסף עכשיו" על מנעול ללא בעלים.
            lock.unlink(missing_ok=True)
            raise
        finally:
            os.close(fd)
    except FileExistsError:
        _thread_lock.release()
        raise AddDiskError(409, "הוספת כונן אחרת רצה עכשיו (מהקונסולה או ממסך השרת)",
                           "Another disk is being added (web console or server screen).") from None
    except OSError as exc:
        _thread_lock.release()
        raise AddDiskError(503, f"לא הצלחנו ליצור את קובץ הנעילה: {exc}",
                           f"The lock file could not be created: {exc}") from None
    return lock


def _release(lock: Path) -> None:
    try:
        lock.unlink()
    except OSError:
        pass
    _thread_lock.release()


def readback(target: Path) -> str | None:
    """כותב קובץ בדיקה, קורא אותו בחזרה ומוחק. None = הצליח; אחרת הסיבה."""
    token = secrets.token_hex(16)
    probe = Path(target) / READBACK_NAME
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write(token)
            fh.flush()
            os.fsync(fh.fileno())
        back = probe.read_text(encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"כתיבה וקריאה חוזרת נכשלו: {exc}"
    if back != token:
        return "הקובץ שנקרא בחזרה אינו מה שנכתב"
    if probe.exists():
        return "קובץ הבדיקה לא נמחק"
    return None


def _validate_name(conn, name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise AddDiskError(400, "חסר שם תצוגה", "A display name is required.")
    if len(name) > 64 or _CONTROL.search(name):
        raise AddDiskError(400, "שם תצוגה לא תקין — עד 64 תווים, בלי תווי בקרה",
                           "Invalid display name.")
    if conn.execute("SELECT id FROM storage_locations WHERE name = ?", (name,)).fetchone():
        raise AddDiskError(409, f"כבר יש מיקום בשם {name}",
                           "A storage location with this name already exists.")
    return name


def _match(listing: dict, device: str, serial: str, size_bytes) -> dict:
    """הכונן שנבחר חייב להופיע **עכשיו** ברשימת הריקים, עם אותו מספר
    סידורי ואותו גודל — שם התקן זז בין אתחולים ובחיבור חם."""
    for disk in listing["items"]:
        if disk.get("device") == device:
            if (disk.get("serial") or "") != (serial or "") or disk.get("size_bytes") != size_bytes:
                raise AddDiskError(409, f"{device} השתנה מאז שהרשימה נקראה — רעננו ובחרו שוב",
                                   f"{device} changed since the list was read; nothing was erased.")
            return disk
    for disk in listing["hidden"]:
        if disk.get("device") == device:
            raise AddDiskError(409, f"{device} אינו ריק: {disk.get('reason') or 'בלי סיבה'} — לא נמחק דבר",
                               f"{device} is not empty; nothing was erased.")
    raise AddDiskError(409, f"{device} אינו ברשימת הכוננים הריקים — רעננו ובחרו שוב",
                       f"{device} is not in the list of empty disks; nothing was erased.")


def add_disk(conn, hooks: dict, data_dir: Path, *, device: str, serial: str,
             size_bytes, name: str, user: str) -> dict:
    """כל הזרימה. מחזיר את שורת המיקום; כל כשל — AddDiskError, ובלי שורה."""
    name = _validate_name(conn, name)
    lock = _acquire(Path(data_dir))
    try:
        disk = _match(blank_disks(hooks), device, serial, size_bytes)
        loc_id = sl.new_id()
        mount = str(sl.mount_point_for(Path(data_dir), name, loc_id))
        taken = conn.execute("SELECT name FROM storage_locations WHERE mount_point = ?",
                             (mount,)).fetchone()
        if taken:
            raise AddDiskError(409, f"כבר יש מיקום על הנתיב הזה: {taken['name']}",
                               "A storage location already uses this mount point.")
        journal(conn, "storage_disk_prepare_start",
                f"{loc_id} {device} serial={serial or '-'} size={size_bytes}", user)
        prepared = hooks["prepare_disk"](device, loc_id)
        if prepared.status != sl.OK:
            journal(conn, "storage_disk_prepare_failed", f"{loc_id} {device} — {prepared.reason}", user)
            raise AddDiskError(409 if prepared.status == sl.FAILED else 503,
                               f"הכנת הכונן נכשלה: {prepared.reason}. לא נוסף מיקום; "
                               "ייתכן שהכונן כבר חולק בחלקו, ולכן לא יופיע עוד כריק.",
                               f"Preparing {device} failed; no location was added. The disk "
                               "may be partly partitioned now.")
        payload = prepared.payload or {}
        uuid = str(payload.get("uuid") or "")
        if not sl.UUID_RE.fullmatch(uuid):
            raise AddDiskError(409, "הכונן הוכן אבל ה-UUID לא נקרא — לא נוסף מיקום",
                               "The disk was prepared but its UUID was not read; no location was added.")
        params = {"uuid": uuid, "fs_type": payload.get("fs_type") or "ext4",
                  "device": device, "partition": payload.get("partition") or "",
                  "serial": disk.get("serial") or "", "model": disk.get("model") or "",
                  "size_bytes": disk.get("size_bytes")}
        mounted = hooks["mount"](params["fs_type"], f"UUID={uuid}", mount, "")
        if mounted.status != sl.OK:
            raise AddDiskError(409, f"העיגון נכשל: {mounted.reason}. לא נוסף מיקום.",
                               "Mounting the new filesystem failed; no location was added.")
        try:
            # עיגון שדווח כמוצלח אינו ראיה; נקודת עיגון אמיתית היא (עיקרון 5).
            problem = (None if hooks.get("is_mount", os.path.ismount)(mount)
                       else "נקודת העיגון אינה מעוגנת אחרי mount")
            if problem is None:
                problem = readback(Path(mount))
            if problem is None:
                try:
                    (Path(mount) / "images").mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    problem = f"תיקיית images/ לא נוצרה: {exc}"
            space = hooks["df"](mount) if problem is None else None
            if problem is None and space.status != sl.OK:
                problem = f"המקום הפנוי לא נקרא: {space.reason}"
            if problem is not None:
                raise AddDiskError(409, f"{problem}. הכונן נותק, ולא נוסף מיקום.",
                                   "The write/read-back check failed; the disk was unmounted "
                                   "and no location was added.")
            _insert(conn, loc_id, name, params, mount, user, space.payload or {})
            written = sl.rewrite_fstab(conn, hooks)
            if written.status != sl.OK:
                _delete(conn, loc_id)
                undone = sl.rewrite_fstab(conn, hooks)
                detail = "" if undone.status == sl.OK else f" (גם ההחזרה נכשלה: {undone.reason})"
                raise AddDiskError(409, f"fstab לא נכתב: {written.reason}{detail}. "
                                        "הכונן נותק, ולא נוסף מיקום.",
                                   "/etc/fstab was not written; the disk was unmounted and "
                                   "no location was added.")
        except AddDiskError as exc:
            hooks["umount"](mount)
            journal(conn, "storage_disk_add_failed", f"{loc_id} {device} — {exc.message}", user)
            raise
        except Exception as exc:  # noqa: BLE001 — גם תקלה לא צפויה לא משאירה מיקום חלקי
            _delete(conn, loc_id)
            sl.rewrite_fstab(conn, hooks)
            hooks["umount"](mount)
            journal(conn, "storage_disk_add_failed", f"{loc_id} {device} — {exc}", user)
            raise AddDiskError(500, f"תקלה פנימית: {exc}. הכונן נותק, ולא נוסף מיקום.",
                               f"Internal error: {exc}. No location was added.") from exc
        journal(conn, "storage_location_add_disk",
                f"{loc_id} {device} UUID={uuid} {name}", user)
        return sl.get(conn, loc_id)
    finally:
        _release(lock)


def _insert(conn, loc_id: str, name: str, params: dict, mount: str, user: str,
            space: dict) -> None:
    now = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO storage_locations (id, name, type, params_json, mount_point,"
            " state, state_since, state_detail, created_by, created_at,"
            " last_images_json, last_df_json)"
            " VALUES (?, ?, 'local', ?, ?, 'connected', ?, '', ?, ?, '[]', ?)",
            (loc_id, name, json.dumps(params, ensure_ascii=False), mount, now, user, now,
             json.dumps({"free_bytes": space.get("free_bytes"),
                         "total_bytes": space.get("total_bytes"), "checked_at": now})),
        )


def _delete(conn, loc_id: str) -> None:
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM storage_locations WHERE id = ?", (loc_id,))
