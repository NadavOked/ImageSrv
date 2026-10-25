"""הדיסק-און-קי לשיכפול בלי שרת (#1914) — ההורדה מהקונסולה.

‏`tools/build_usb_clone.sh --publish <data_dir>` כותב שלושה קבצים
ל-`<data_dir>/usb/`: ‏`imagesrv-usb.img`, ‏`imagesrv-usb.img.sha256`
(``<sha>  <שם>``) ו-`imagesrv-usb.json` (גרסה, גודל, מתי נבנה). השרת
**אינו** בונה ואינו משנה אותם — הוא מגיש.

למנהל בלבד (`admin_only`); משתמש הפצה מקבל 403 גם מ-`console_only`
ב-app.py. ‏sha256 שאינו 64 תווי hex, או מניפסט שהגודל שבו אינו גודל הקובץ —
"לא זמין" עם הסיבה, לא הורדה של קובץ שאין לו ראיה (עיקרון 5/6). הגיבוב
של הקובץ עצמו מחושב פעם אחת לכל (גודל, mtime) ונשמר בזיכרון — מאות MB אינם
נקראים בכל רענון של דף ההגדרות — וקובץ שאינו תואם לשורה אינו מוגש.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from . import auth
from .api import ServerContext

USB_DIR = "usb"
IMG = "imagesrv-usb.img"
SHA = IMG + ".sha256"
MANIFEST = "imagesrv-usb.json"
_SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._+-]+\.img)\n?$")
_HASHED: dict[tuple, str] = {}
_HASH_LOCK = threading.Lock()


def _file_sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    with _HASH_LOCK:
        if key not in _HASHED:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            _HASHED.clear()
            _HASHED[key] = h.hexdigest()
        return _HASHED[key]


BUILD_HINT = "sudo tools/build_usb_clone.sh --out /var/tmp/usb --publish <data_dir>"


def usb_status(data_dir: Path) -> dict:
    """מה שהקונסולה מציגה. ‏`available` רק כשהקובץ, ה-sha256 והמניפסט כולם
    נקראו ותואמים זה לזה; אחרת `reason` אומר מה חסר."""
    base = Path(data_dir) / USB_DIR
    img, sha, man = base / IMG, base / SHA, base / MANIFEST
    if not img.is_file():
        return {"available": False, "reason": "לא נבנה בשרת הזה", "build": BUILD_HINT}
    try:
        m = _SHA_LINE.match(sha.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return {"available": False, "reason": f"קובץ ה-SHA256 לא נקרא: {exc}"}
    if not m:
        return {"available": False, "reason": "קובץ ה-SHA256 אינו בפורמט '<sha256>  <שם>.img'"}
    try:
        meta = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"המניפסט לא נקרא: {exc}"}
    size = img.stat().st_size
    if not isinstance(meta, dict) or meta.get("size") != size or meta.get("sha256") != m.group(1):
        return {"available": False, "reason": "המניפסט אינו תואם לקובץ (גודל או sha256) — בנו מחדש"}
    try:
        real = _file_sha256(img)
    except OSError as exc:
        return {"available": False, "reason": f"הקובץ לא נקרא: {exc}"}
    if real != m.group(1):
        return {"available": False, "reason": "ה-sha256 של הקובץ אינו זה שנרשם בבנייה — הקובץ פגום, בנו מחדש"}
    return {"available": True, "file": m.group(2), "sha256": m.group(1), "size": size,
            "version": meta.get("version"), "built_at": meta.get("built_at"),
            "bios": meta.get("bios") is True}


def create_usb_router(ctx: ServerContext, data_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/usb-clone")
    def usb_clone(user=Depends(admin_only)):
        return usb_status(data_dir)

    @router.get("/usb-clone/download")
    def usb_clone_download(user=Depends(admin_only)):
        st = usb_status(data_dir)
        if not st["available"]:
            return JSONResponse({"ok": False, "error": st["reason"], "code": "usb_unavailable"}, status_code=404)
        return FileResponse(Path(data_dir) / USB_DIR / IMG, media_type="application/octet-stream",
                            filename=st["file"])

    @router.get("/usb-clone/sha256")
    def usb_clone_sha(user=Depends(admin_only)):
        st = usb_status(data_dir)
        if not st["available"]:
            return JSONResponse({"ok": False, "error": st["reason"], "code": "usb_unavailable"}, status_code=404)
        return PlainTextResponse(f"{st['sha256']}  {st['file']}\n",
                                 headers={"Content-Disposition": f'attachment; filename="{st["file"]}.sha256"'})

    return router
