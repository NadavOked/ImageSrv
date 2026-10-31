"""ה-API של ספריית הדרייברים (#720) — לקונסולה ולסוכן.

קונסולה (`/api/console/drivers`): רשימה עם "מתאים ל-N מכונות" לפי
המלאי האחרון של המכונות הרשומות, ייבוא tar (admin, sha256 של כל קובץ
לפני הכניסה — `drivers.DriverLibrary.import_tar`), ומחיקה מאחורי
הקלדת שם (עיקרון 7).

סוכן (`/api/v1/agent/drivers`): אחרי השחזור ולפני האתחול הסוכן שואל
אילו חבילות מתאימות ל-MAC שלו — השרת מתאים לפי המלאי האחרון שאותו
MAC דיווח ב-hello — ומוריד את הקבצים בשמם המדויק מהמניפסט.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from boot.grub_menu import normalize_mac as lenient_mac

from server.console import auth
from server.agent import inventory
from server.rounds import room_drivers
from server.library.archive import unreadable_message
from server.agent.api import ServerContext, identity_gate
from server.db import journal
from server.library.drivers import DriverError, DriverLibrary, for_model, match


def _matches_by_package(conn, library: DriverLibrary) -> dict[str, list[str]]:
    """שם חבילה → ה-MACs של המכונות **הרשומות** שהמלאי האחרון שלהן תואם."""
    registered = {r["mac"] for r in conn.execute("SELECT mac FROM machines")}
    packages = list(library.scan().values())
    result: dict[str, list[str]] = {p["name"]: [] for p in packages}
    for mac, seen in inventory.latest_all(conn).items():
        if mac not in registered:
            continue
        for hit in match(seen["inventory"], packages):
            result[hit["name"]].append(mac)
    return result


def create_drivers_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)
    library: DriverLibrary = ctx.drivers

    @router.get("/drivers")
    def list_drivers(user=Depends(current_user)):
        hits = _matches_by_package(ctx.conn, library)
        return [{**p, "matches": sorted(hits.get(p["name"], []))}
                for p in library.public_list()]

    @router.post("/drivers/upload")
    async def upload_driver(request: Request, user=Depends(admin_only)):
        """קליטת חבילה מקובץ tar. הגוף הוא הקובץ עצמו, כמו העלאת אימג'."""
        library.root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(delete=False, dir=library.root, suffix=".upload")
        temp = Path(handle.name)
        try:
            with handle:
                async for chunk in request.stream():
                    handle.write(chunk)
            if temp.stat().st_size == 0:
                raise DriverError("לא התקבל קובץ")
            manifest = library.import_tar(temp)
        except DriverError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:                       # tar פגום, קלט חתוך
            raise HTTPException(400, unreadable_message(exc))
        finally:
            temp.unlink(missing_ok=True)
        journal(ctx.conn, "driver_upload",
                f'{manifest["name"]} ({len(manifest["files"])} files)', user[0])
        return {"name": manifest["name"], "files": len(manifest["files"])}

    @router.post("/drivers/{name}/delete")
    async def delete_driver(name: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        if library.get(name) is None:
            raise HTTPException(404, "חבילה לא קיימת")
        if body.get("confirm_name", "") != name:
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם החבילה")
        library.delete(name)
        journal(ctx.conn, "driver_delete", name, user[0])
        return {"ok": True}

    return router


def _entry(name: str, by: str, manifest: dict) -> dict:
    """חבילה אחת בתשובה לסוכן — הקבצים בשמם המדויק מהמניפסט."""
    return {"name": name, "by": by,
            "files": [{"path": f["path"], "sha256": f["sha256"],
                       "url": f"/api/v1/agent/drivers/{name}/files/{f['path']}"}
                      for f in manifest["files"]]}


def _for_drawer(ctx: ServerContext, library: DriverLibrary, mac: str,
                session: str, port: int) -> JSONResponse:
    """‏#1269: מגירה של מחשב שיכפול — הדגם שהמפעיל בחר לחריץ הזה בסבב.

    שלושה מצבים, לא שניים: ‏`chosen: null` = לא נבחר דגם לחריץ (הסוכן
    מדלג); ‏`chosen` עם `packages: []` = נבחר דגם ואין לו עכשיו חבילה
    בספרייה (נמחקה מאז הפתיחה) — הסוכן מדווח `failed`, לא `no_match`;
    ואחרת החבילות. סבב שאינו גל של חדר → 404 `no_round`."""
    row = ctx.conn.execute(
        "SELECT driver_models_json FROM room_rounds WHERE wave_session_id = ?",
        (session,)).fetchone()
    if row is None:
        return JSONResponse({"ok": False, "error": "no room round for this session",
                             "code": "no_round"}, status_code=404)
    chosen = room_drivers.choice(row, mac, port)
    if chosen is None:
        return JSONResponse({"ok": True, "chosen": None, "packages": []})
    packages = library.scan()
    names = for_model(chosen["vendor"], chosen["model"], list(packages.values()))
    return JSONResponse({"ok": True, "chosen": chosen,
                         "packages": [_entry(n, "model", packages[n]) for n in names]})


def create_agent_drivers_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    library: DriverLibrary = ctx.drivers

    @router.get("/agent/drivers")
    def agent_drivers(request: Request, mac: str = "", session: str = "",
                      port: int | None = None) -> JSONResponse:
        """מה מתאים למכונה הזו. שלושה מצבים, לא שניים (עיקרון 5):
        ‏`inventory: false` = המכונה מעולם לא דיווחה מלאי (סוכן ישן /
        schema 1) — הסוכן מדווח `failed`, לא `no_match`; ‏`inventory:
        true` עם `packages: []` = יש מלאי ואין חבילה תואמת.

        ‏#1269: עם `session` ו-`port` — מגירה בסבב החדר, לפי הדגם שנבחר
        (‏`_for_drawer`); המלאי של המשכפל אינו של המכונה שתקבל את הדיסק."""
        canonical = lenient_mac(mac)
        if canonical is None:
            return JSONResponse({"ok": False, "error": "missing or malformed mac",
                                 "code": "bad_mac"}, status_code=400)
        refused = identity_gate(ctx, canonical, request, "drivers")
        if refused is not None:
            return refused
        if session or port is not None:
            if not session or port is None:
                return JSONResponse({"ok": False, "error": "session and port go together",
                                     "code": "bad_query"}, status_code=400)
            return _for_drawer(ctx, library, canonical, session, port)
        seen = inventory.latest(ctx.conn, canonical)
        if seen is None:
            return JSONResponse({"ok": True, "inventory": False, "packages": []})
        packages = library.scan()
        answer = [_entry(hit["name"], hit["by"], packages[hit["name"]])
                  for hit in match(seen["inventory"], list(packages.values()))]
        return JSONResponse({"ok": True, "inventory": True, "packages": answer})

    @router.get("/agent/drivers/{name}/files/{path:path}")
    def agent_driver_file(name: str, path: str):
        # רשימה לבנה: מוגש רק קובץ שהמניפסט מכריז עליו בשמו המדויק.
        found = library.file_path(name, path)
        if found is None:
            return JSONResponse({"ok": False, "error": "file not in this package",
                                 "code": "no_file"}, status_code=404)
        return FileResponse(found, media_type="application/octet-stream")

    return router
