"""ספריית ה-ISO (#404) -- קובצי ISO שמועלים לשרת, לצד ספריית האימג'ים.

ISO אינו אימג' דיסק: הוא קובץ אחד (מערכת קבצים ISO 9660), בלי מחיצות ובלי
manifest. לכן יש לו ספרייה משלו, באותו עיקרון -- **הדיסק הוא מקור האמת**
(עיקרון 3): תיקייה לכל ISO ובה הקובץ ו-`iso.json` שלצדו, בלי טבלה ב-DB.

    <שורש האימג'ים>/isos/iso_ab12cd/image.iso
    <שורש האימג'ים>/isos/iso_ab12cd/iso.json

ה-ISO נכנס לספרייה רק אחרי שהשרת הוכיח **שני דברים** (עיקרון 6, ועיקרון 5:
"לא הצלחנו לבדוק" אינו "בדקנו, הכול תקין"):

1. שהוא ISO -- מבנה ISO 9660 בסקטור 16. קובץ אחר נדחה בגלוי ואינו נשמר;
2. ש-sha256 שחושב על הזרם בזמן הקבלה הוא אותו sha256 שנקרא בחזרה מהדיסק
   (``file_sha256``) -- כלומר מה שנכתב הוא מה שהתקבל.

הגשת ה-ISO לאתחול ברשת **אינה כאן** -- היא ממתינה להכרעה (ראו #404).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from server.core.atomicfile import fsync_dir, fsync_file, write_atomic

log = logging.getLogger("imagesrv.isos")

#: תיקיית ה-ISO בתוך שורש הספרייה. בלי נקודה בתחילתה: זה לא אזור עבודה
#: שהסחיפה (`work_areas`) מוחקת, אלא נתונים של אדם. `scan` של האימג'ים
#: מחפש רק `manifest.json`, ולכן אינו מבלבל ביניהם.
DIRNAME = "isos"
FILE = "image.iso"
META = "iso.json"
#: `iso_` + שש ספרות הקס -- כמו `img_` באימג'ים: המזהה הוא שם התיקייה,
#: ולכן הוא הגבול בין "מזהה" ל"נתיב" (#110).
ISO_ID = re.compile(r"iso_[0-9a-f]{6}")
HASH_CHUNK = 4 * 1024 * 1024
SECTOR = 2048
#: Primary Volume Descriptor: סקטור 16, בייט 0 = 1, בייטים 1-5 = "CD001", בייט 6 = 1.
PVD_AT = 16 * SECTOR
HEAD_BYTES = PVD_AT + 7
NAME_MAX = 200

#: סימנים של קבצים שמועלים בטעות במקום ISO -- רק כדי לומר למפעיל מה זה נראה.
_LOOKS_LIKE = (
    (0, b"PK\x03\x04", "ZIP"), (0, b"PK\x05\x06", "ZIP"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z"), (0, b"Rar!\x1a\x07", "RAR"),
    (0, b"\x1f\x8b", "gzip"), (0, b"\xfd7zXZ\x00", "xz"), (0, b"BZh", "bzip2"),
    (257, b"ustar", "tar"), (0, b"MZ", "קובץ הפעלה של Windows"),
)

REFUSE_NOT_ISO = ("הקובץ אינו ISO תקין, ולא נשמר. לא נמצא מבנה ISO 9660 בקובץ{looks}. "
                  "אם זה ISO שנארז, פותחים אותו ומעלים את קובץ ה-ISO עצמו. "
                  "מה שהועלה נמחק מהשרת.")


class IsoError(ValueError):
    """סירוב בעברית. ``status`` הוא קוד ה-HTTP שהנתיב יחזיר."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def head_refusal(head: bytes) -> str | None:
    """למה הקובץ שראשו ``head`` אינו ISO, או ``None`` אם יש בו מבנה ISO 9660.

    ``head`` חייב להכיל לפחות ``HEAD_BYTES`` בייטים, אלא אם הקובץ קצר מזה."""
    if len(head) >= HEAD_BYTES and head[PVD_AT:PVD_AT + 7] == b"\x01CD001\x01":
        return None
    looks = ""
    for offset, magic, label in _LOOKS_LIKE:
        if head[offset:offset + len(magic)] == magic:
            looks = f" (הוא נראה כמו {label})"
            break
    return REFUSE_NOT_ISO.format(looks=looks)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class IsoIntake:
    """קבלת ISO בזרם: כותב לקובץ הזמני, מחשב sha256 ובודק את הראש.

    ``feed`` מחזיר את הסירוב ברגע שהראש הוכיח שזה לא ISO -- מאותו רגע
    דבר אינו נכתב עוד, והקורא ממשיך לרוקן את הזרם כדי שהדפדפן יקבל תשובה
    ולא ניתוק. ``finish`` סוגר את המקרה של קובץ קצר מהראש."""

    def __init__(self, handle, guard=None):
        self._handle = handle
        self._guard = guard
        self._hash = hashlib.sha256()
        self._head = bytearray()
        self._checked = False
        self.refusal: str | None = None
        self.size = 0

    def feed(self, chunk: bytes) -> str | None:
        if self.refusal is not None:
            return self.refusal
        if len(self._head) < HEAD_BYTES:
            self._head += chunk[:HEAD_BYTES - len(self._head)]
            if len(self._head) >= HEAD_BYTES:
                self._checked = True
                self.refusal = head_refusal(bytes(self._head))
                if self.refusal is not None:
                    return self.refusal
        self._handle.write(chunk)
        self._hash.update(chunk)
        self.size += len(chunk)
        if self._guard is not None:
            self._guard.wrote(len(chunk))
        return None

    def finish(self) -> str | None:
        if self.refusal is None and not self._checked:
            self.refusal = head_refusal(bytes(self._head))
        return self.refusal

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()


def clean_filename(value: str) -> str:
    """שם הקובץ המקורי להצגה בלבד: בלי נתיב ובלי תווי בקרה. הוא לעולם
    אינו משמש כשם קובץ בדיסק (הקובץ הוא תמיד ``image.iso``)."""
    base = re.split(r"[\\/]", value or "")[-1]
    base = "".join(ch for ch in base if ch.isprintable())
    return base[:NAME_MAX]


class IsoStore:
    def __init__(self, library_root: str | Path):
        self.dir = Path(library_root) / DIRNAME
        self._lock = threading.Lock()

    def _read(self, directory: Path) -> dict | None:
        try:
            meta = json.loads((directory / META).read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            log.warning("skipping unreadable ISO record %s: %s", directory, exc)
            return None
        if (not isinstance(meta, dict) or meta.get("id") != directory.name
                or not ISO_ID.fullmatch(directory.name)
                or not isinstance(meta.get("name"), str)
                or not isinstance(meta.get("size"), int)
                or not isinstance(meta.get("sha256"), str)):
            log.warning("skipping malformed ISO record %s", directory)
            return None
        meta.setdefault("folder", "")
        meta.setdefault("filename", "")
        # "present" נקבע מהדיסק עכשיו, לא מהרשומה: קובץ שנעלם או השתנה בגודלו אינו "אומת".
        path = directory / FILE
        try:
            meta["present"] = path.is_file() and not path.is_symlink() and path.stat().st_size == meta["size"]
        except OSError:
            meta["present"] = False
        return meta

    def list(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        found = [m for d in sorted(self.dir.iterdir())
                 if d.is_dir() and not d.is_symlink() and (m := self._read(d)) is not None]
        return sorted(found, key=lambda m: (m["name"].lower(), m["id"]))

    def get(self, iso_id: object) -> dict | None:
        if not isinstance(iso_id, str) or not ISO_ID.fullmatch(iso_id):
            return None
        directory = self.dir / iso_id
        return self._read(directory) if directory.is_dir() and not directory.is_symlink() else None

    def name_taken(self, name: str) -> bool:
        return any(m["name"].lower() == name.lower() for m in self.list())

    def admit(self, temp: Path, *, name: str, folder: str, filename: str, size: int,
               sha256: str, user: str) -> dict:
        """מכניס את הקובץ שאומת לספרייה. `temp` חייב להיות באותו כונן (rename)."""
        with self._lock:
            if self.name_taken(name):
                raise IsoError(f'כבר יש ISO בשם "{name}" -- בחרו שם אחר', 409)
            self.dir.mkdir(parents=True, exist_ok=True)
            while True:
                iso_id = "iso_" + secrets.token_hex(3)
                if not (self.dir / iso_id).exists():
                    break
            directory = self.dir / iso_id
            directory.mkdir()
            meta = {"schema": 1, "id": iso_id, "name": name, "folder": folder, "filename": filename,
                    "size": size, "sha256": sha256, "uploaded_by": user,
                    "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            try:
                os.replace(temp, directory / FILE)
                fsync_file(directory / FILE)
                write_atomic(directory / META, json.dumps(meta, ensure_ascii=False, indent=2))
                fsync_dir(directory)
                fsync_dir(self.dir)
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)
                raise
        return {**meta, "present": True}

    def delete(self, iso_id: str) -> dict:
        """מוחק ISO. "נמחק" נקבע בקריאה חוזרת מהדיסק, לא בהיעדר חריגה."""
        found = self.get(iso_id)
        if found is None:
            raise IsoError("ה-ISO לא קיים", 404)
        directory = self.dir / iso_id
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            log.warning("ISO delete of %s failed: %s", directory, exc)
        if directory.exists():
            raise IsoError("המחיקה לא הושלמה -- הקבצים עדיין בדיסק. הפרטים ביומן השרת.", 500)
        return found
