"""לשונית ISO בקונסולה (#404, מוקאפ docs/design/mockups-1107b/404-iso.html).

העלאה, רשימה ומחיקה של קובצי ISO, ותיקיות לארגון שלהם. הכול למנהל; משתמש
הפצה מקבל 403 על כל נתיב קונסולה (#1073) ממילא, בהרכבה ב-`app.py`.

**אתחול מחשב מ-ISO ברשת אינו כאן.** איך מגישים ISO לאתחול (memdisk, iPXE או
פריסה ל-HTTP) ואיך זה חי עם Secure Boot דלוק במחשבי המכללה -- ממתין להכרעה
(#404). שום נתיב כאן לא נוגע ב-GRUB, ב-TFTP או ב-dnsmasq.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from server.agent.api import ServerContext
from server.console import auth
from server.console.library import UPLOAD_BATCH, UPLOAD_PREFIX
from server.db import get_setting, journal, set_setting
from server.library import isos
from server.library.images import validate_display_name
from server.storage import disk_reserve
from server.system import inflight

FOLDERS_KEY = "iso_folders"
SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _free(root: Path) -> int:
    """נקודת ההזרקה של הבדיקות -- אותה שאלה כמו `console.library._library_free`."""
    return shutil.disk_usage(root).free


def _checked(value: str, what: str) -> str:
    try:
        return validate_display_name(value, what)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def create_iso_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)
    store = isos.IsoStore(ctx.library.root)

    def stored_folders() -> list[str]:
        try:
            names = json.loads(get_setting(ctx.conn, FOLDERS_KEY) or "[]")
        except ValueError:
            return []
        return [n for n in names if isinstance(n, str)]

    def known_folders(listed: list[dict]) -> list[str]:
        """מה שנוצר בקונסולה + מה שרשומות ה-ISO מצביעות עליו (הדיסק הוא מקור האמת)."""
        names = stored_folders()
        names += sorted({m["folder"] for m in listed if m["folder"] and m["folder"] not in names})
        return names

    def public(meta: dict) -> dict:
        return {k: meta.get(k) for k in ("id", "name", "folder", "filename", "size", "sha256",
                                        "uploaded_at", "uploaded_by", "present")}

    @router.get("/isos")
    def list_isos(user=Depends(current_user)):
        listed = store.list()
        counts: dict[str, int] = {}
        for meta in listed:
            counts[meta["folder"]] = counts.get(meta["folder"], 0) + 1
        return {"isos": [public(m) for m in listed],
                "folders": [{"name": n, "isos": counts.get(n, 0)} for n in known_folders(listed)]}

    @router.post("/isos/upload")
    async def upload_iso(request: Request, user=Depends(admin_only)):
        """קליטת ISO. הגוף הוא הקובץ עצמו (לא multipart); ‏`name`, ‏`folder` ו-`filename`
        ב-query. ‏`X-Expected-Sha256` (לא חובה): ה-sha256 שהאתר שפרסם את ה-ISO נותן.

        נכתב לדיסק תוך כדי קבלה -- ISO שוקל ג'יגות. כל ההעלאה, כולל קריאת
        האימות מהדיסק, היא "עסוק" ל"החל עדכון" (`inflight`), כמו העלאת אימג'."""
        with inflight.holding("העלאת ISO"):
            try:
                return await _upload(request, user)
            except isos.IsoError as exc:
                raise HTTPException(exc.status, str(exc))
            except disk_reserve.ReserveReached as exc:
                raise HTTPException(507, str(exc))

    async def _upload(request: Request, user) -> dict:
        query = request.query_params
        filename = isos.clean_filename(query.get("filename", ""))
        name = _checked(query.get("name") or Path(filename).stem, "שם ה-ISO")
        folder = query.get("folder", "")
        if folder and folder not in known_folders(store.list()):
            raise HTTPException(400, f"התיקייה {folder!r} לא קיימת")
        expected = (request.headers.get("x-expected-sha256") or "").strip().lower()
        if expected and not SHA256.fullmatch(expected):
            raise HTTPException(400, "ה-sha256 שהוזן אינו תקין (64 ספרות הקס)")
        if store.name_taken(name):
            raise HTTPException(409, f'כבר יש ISO בשם "{name}" -- בחרו שם אחר')

        root = Path(ctx.library.root)
        root.mkdir(parents=True, exist_ok=True)

        def room_for(need: int) -> None:
            try:
                free = _free(root)
            except OSError as exc:              # לא נמדד ≠ יש מקום (עיקרון 5)
                raise HTTPException(507, f"לא ניתן לקרוא כמה מקום פנוי בשרת: {exc}")
            refused = disk_reserve.refusal(free, need, "העלאת ה-ISO")
            if refused is not None:
                raise HTTPException(507, refused)

        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            await asyncio.to_thread(room_for, int(declared))
        guard = disk_reserve.StreamGuard(lambda: _free(root), "העלאת ה-ISO")
        # שם שמתחיל בנקודה, כמו העלאת tar: אזור עבודה שהסחיפה היומית מכירה אם השרת מת באמצע.
        handle = tempfile.NamedTemporaryFile(delete=False, dir=root, prefix=UPLOAD_PREFIX, suffix=".upload")
        temp = Path(handle.name)
        try:
            intake = isos.IsoIntake(handle, guard)
            with handle:
                batch = bytearray()
                async for chunk in request.stream():
                    batch += chunk
                    if len(batch) >= UPLOAD_BATCH:
                        await asyncio.to_thread(intake.feed, bytes(batch))
                        batch.clear()
                if batch:
                    await asyncio.to_thread(intake.feed, bytes(batch))
            refusal = intake.finish()
            if refusal is not None:
                raise HTTPException(400, refusal)
            if intake.size == 0:
                raise HTTPException(400, "לא התקבל קובץ")
            if expected and expected != intake.sha256:
                raise HTTPException(400, f"sha256 של הקובץ שהתקבל ({intake.sha256}) אינו ה-sha256 שהוזן "
                                         f"({expected}). הקובץ לא נשמר -- ההעלאה נקטעה או שהקובץ שונה.")
            # עיקרון 6: ה-ISO נכנס רק אחרי שמה שנקרא בחזרה מהדיסק הוא מה שהתקבל.
            on_disk, size = await asyncio.to_thread(lambda: (isos.file_sha256(temp), temp.stat().st_size))
            if on_disk != intake.sha256 or size != intake.size:
                raise HTTPException(500, "אימות sha256 אחרי הכתיבה נכשל: מה שנכתב לדיסק אינו מה שהתקבל. "
                                         "הקובץ לא נשמר -- בדקו את הדיסק ונסו שוב.")
            meta = await asyncio.to_thread(
                lambda: store.admit(temp, name=name, folder=folder, filename=filename,
                                     size=size, sha256=on_disk, user=user[0]))
        finally:
            await asyncio.to_thread(temp.unlink, missing_ok=True)
        journal(ctx.conn, "iso_upload", f'{meta["id"]} "{name}" {meta["size"]} {meta["sha256"]}', user[0])
        return public(meta)

    @router.post("/isos/{iso_id}/delete")
    def delete_iso(iso_id: str, user=Depends(admin_only)):
        try:
            meta = store.delete(iso_id)
        except isos.IsoError as exc:
            raise HTTPException(exc.status, str(exc))
        journal(ctx.conn, "iso_delete", f'{meta["id"]} "{meta["name"]}"', user[0])
        return {"ok": True}

    @router.post("/iso-folders")
    async def add_folder(request: Request, user=Depends(admin_only)):
        name = _checked((await request.json()).get("name") or "", "שם התיקייה")
        if name in known_folders(store.list()):
            raise HTTPException(409, "כבר יש תיקייה בשם הזה")
        set_setting(ctx.conn, FOLDERS_KEY, json.dumps(stored_folders() + [name], ensure_ascii=False))
        journal(ctx.conn, "iso_folder_create", name, user[0])
        return {"ok": True}

    @router.post("/iso-folders/{name}/delete")
    def delete_folder(name: str, user=Depends(admin_only)):
        listed = store.list()
        if any(m["folder"] == name for m in listed):
            raise HTTPException(409, "התיקייה אינה ריקה -- מחקו קודם את ה-ISO שבה")
        names = stored_folders()
        if name not in names:
            raise HTTPException(404, "תיקייה לא קיימת")
        names.remove(name)
        set_setting(ctx.conn, FOLDERS_KEY, json.dumps(names, ensure_ascii=False))
        journal(ctx.conn, "iso_folder_delete", name, user[0])
        return {"ok": True}

    return router
