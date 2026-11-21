"""#3284: עותקים פתוחים של בדיקת הכתיבה במסך ההמתנה -- לראות ולשחרר (מנהל בלבד).

העותק (`server/disks/write_backup.py`, #3268) הוא הרשומה היחידה של מה שהיה על הדיסק. הסוכן אינו מוחק עותק שלא
הוחזר במלואו (#3271 H3), ולכן עותק `stale` נשאר פתוח, ובינתיים אי אפשר לשמור עותק חדש לסידורי הזה (409).
כאן המנהל רואה אותם ומשחרר עותק **בבקשה מפורשת אחת**. אין שחרור אוטומטי -- לא לפי זמן ולא לפי מספר הכנסות.

"לא נקרא" אינו "אין" (עיקרון 5): רשומה שאי אפשר לקרוא מופיעה ב-`unreadable`, והרשימה עצמה אינה ריקה בשבילה.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from server.agent.api import ServerContext
from server.core.logtext import one_line
from server.db import journal
from server.disks import disk_sets
from server.disks.write_backup import _ID, UnreadableCopy, _meta

log = logging.getLogger("imagesrv.write_copies")
#: #3891: the record is named (key, file); the exception's text goes to the server log (CodeQL py/stack-trace-exposure)
UNREADABLE_HE = "הרשומה לא נקראה — הפרטים ביומן השרת"

_HEX = re.compile(r"^(?:[0-9a-f]{2})+$")


def _serial_of(key: str) -> str | None:
    try:
        return bytes.fromhex(key).decode("utf-8")
    except ValueError:
        return None


def open_copies(root: Path, names: dict[str, str]) -> dict:
    """כל העותקים הפתוחים. רשומה בלי הקובץ שלה אינה עותק (הקובץ נכתב ראשון ונמחק אחרון); רשומה שנקראת רע -- `unreadable`."""
    copies, unreadable = [], []
    base = root / "write-backups"
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            blob = path.with_suffix(".bin")
            if not blob.is_file():
                continue
            try:
                meta = _meta(path)
                stale = meta.get("stale")
                copies.append({
                    "id": str(meta["id"]), "serial": str(meta["serial"]), "key": folder.name,
                    "created": int(meta["created"]), "mac": meta.get("mac") or "",
                    "machine": names.get(meta.get("mac") or ""), "bytes": blob.stat().st_size,
                    "stale": {"blocks": int(stale["blocks"]), "at": int(stale["at"])} if isinstance(stale, dict) else None})
            except (UnreadableCopy, KeyError, TypeError, ValueError, OSError) as exc:
                log.warning("write copy %s/%s cannot be read: %s", one_line(folder.name), one_line(path.name), one_line(exc))
                unreadable.append({"key": folder.name, "file": path.name, "error": UNREADABLE_HE})
    copies.sort(key=lambda c: c["created"], reverse=True)
    return {"copies": copies, "unreadable": unreadable}


def register(router: APIRouter, ctx: ServerContext, admin_only) -> None:
    @router.get("/write-copies")
    def listing(user=Depends(admin_only)):
        if ctx.data_dir is None:
            raise HTTPException(503, "אין תיקיית נתונים")
        try:
            return open_copies(ctx.data_dir, disk_sets._machine_names(ctx.conn))
        except OSError as exc:   # לא נקרא ≠ אין עותקים
            raise HTTPException(500, f"לא הצלחתי לקרוא את תיקיית העותקים: {exc}") from None

    @router.delete("/write-copies/{key}/{bid}")
    def release(key: str, bid: str, user=Depends(admin_only)):
        """שחרור עותק אחד. הרשומה נמחקת ראשונה (מכאן הוא כבר אינו עותק פתוח), אחר כך הקובץ; נרשם ביומן עם שם המנהל."""
        serial = _serial_of(key) if _HEX.match(key) else None
        if ctx.data_dir is None or serial is None or not _ID.match(bid):
            raise HTTPException(400, "מזהה לא תקין")
        record = ctx.data_dir / "write-backups" / key / f"{bid}.json"
        if not record.is_file():
            raise HTTPException(404, "אין עותק כזה — אולי כבר שוחרר")
        try:
            meta = _meta(record)
        except UnreadableCopy:
            meta = {}   # לא נקרא: משחררים רק עותק שהמנהל ראה ברשימה, והיומן רושם מה שאפשר
        record.unlink()
        (record.with_suffix(".bin")).unlink(missing_ok=True)
        mac = meta.get("mac") or None
        journal(ctx.conn, "write_backup_admin_released",
                f"{mac or '-'} serial={serial} id={bid} stale={'yes' if meta.get('stale') else 'no'} -- released by {user[0]}",
                user[0], machine=mac)
        return {"ok": True}
