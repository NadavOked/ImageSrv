"""‏`/api/console/vmupdate/*` — עדכון אימג' ב-VM (‏#1334). ‏admin בלבד.

ה-HTTP דרך `admin_only` (‏deploy → ‏403, לא מחובר → ‏401). ה-WebSocket של
המסך עובר **באותו שער** של המוניטור (`monitor.py`, ‏#904/#1129): ‏Origin
זר נסגר לפני accept, ‏cookie חסר → ‏4401, לא admin → ‏4403 — ושניהם
**לפני** שנפתח ה-socket של ה-VNC. מכאן הגשר הוא `monitor.bridge_browser`
עצמו: הדפדפן רואה אותה לחיצת-יד, אותה תקרת חוסר-פעילות, ואותו סירוב
ל-ClientCutText.

החוזה: `docs/interfaces.md` סעיף 33.
"""

from __future__ import annotations

import asyncio
import logging
import struct

from fastapi import APIRouter, Depends, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from server.console import auth, monitor, viewer_sessions
from server.agent.api import ServerContext
from server.db import journal
from server.library.images import DISPLAY_NAME_RULE
from server.vm.vmupdate import SETTINGS, Refusal, VmUpdate, read_settings, write_settings

log = logging.getLogger("imagesrv.vmupdate_api")

#: ה-VM אינו פתוח (או עוד לא הגיע לריצה) — אין מסך לחבר.
WS_NO_VM = 4409

#: ‏#1535: הסירובים של ה-API נבנים מהכללים עצמם, לא מטקסט של חריגה —
#: הטקסט המלא הולך ליומן השרת.
SETTINGS_RULE = "הגדרות ה-VM לא נשמרו — מותרים רק " + ", ".join(
    f"{name} (מספר שלם בין {low} ל-{high})" for name, (_key, _default, low, high) in SETTINGS.items())
NAME_RULE = f"שם הגרסה אינו תקין — {DISPLAY_NAME_RULE}"


def _one_line(exc: Exception) -> str:
    """טקסט החריגה ליומן — בלי שבירת שורה (ערכים מהגוף של הבקשה)."""
    return str(exc).replace("\r", " ").replace("\n", " ")


def _refused(exc: Refusal) -> JSONResponse:
    """המוסכמה הרוחבית (`api._error`): ‏`ok`/‏`error`/‏`code`, ועוד המספרים."""
    body = {"ok": False, "error": exc.message, "code": exc.reason}
    if exc.numbers:
        body["details"] = exc.numbers
    return JSONResponse(body, status_code=exc.status)


async def qemu_handshake(reader, writer) -> None:
    """לחיצת-יד RFB 3.8 מול ה-VNC של QEMU עד SecurityResult.

    ה-socket מקומי (unix, בתוך אזור העבודה) ולכן QEMU מציע None (סוג 1) —
    ההגנה היא השער של ה-WebSocket ולא סיסמת VNC. מכאן הממסר זהה למוניטור:
    הדפדפן שולח ClientInit דרך `bridge_browser`."""
    banner = await monitor._read_exact(reader, 12)
    if not banner.startswith(b"RFB 003."):
        raise monitor.MachineAuthError("על ה-socket של ה-VM עונה משהו שאינו RFB")
    writer.write(monitor.RFB_VERSION)
    await writer.drain()
    count = (await monitor._read_exact(reader, 1))[0]
    if count == 0:
        raise monitor.MachineAuthError(f"ה-VNC של ה-VM דחה: {await monitor._reason(reader)}")
    offered = await monitor._read_exact(reader, count)
    if 1 not in offered:
        raise monitor.MachineAuthError(f"ה-VNC של ה-VM אינו מציע None ({sorted(offered)})")
    writer.write(b"\x01")
    await writer.drain()
    result = struct.unpack(">I", await monitor._read_exact(reader, 4))[0]
    if result != 0:
        raise monitor.MachineAuthError(f"ה-VNC של ה-VM דחה: {await monitor._reason(reader)}")


def _open_unix(path: str):
    # ‏`asyncio.open_unix_connection` אינו קיים בווינדוס (תחנת הפיתוח); השרת
    # הוא דביאן. נפתר בזמן הקריאה ולא בייבוא, כדי שהמודול ייטען בכל מקום.
    return asyncio.open_unix_connection(path)


def create_vmupdate_router(ctx: ServerContext, engine: VmUpdate) -> APIRouter:
    """ה-HTTP. ‏app.py מוסיף אותו דרך `include` — עם `console_only`, כך שעוגיית
    deploy (גם זו שהקיוסק הנפיק) מקבלת את הסירוב האחיד של #1073 לפני admin_only."""
    router = APIRouter(prefix="/api/console/vmupdate")
    _, admin_only = auth.dependencies(ctx.conn)

    def act(fn, *args):
        try:
            return {"ok": True, **fn(*args)}
        except Refusal as exc:
            return _refused(exc)

    @router.get("/settings")
    def get_settings(user=Depends(admin_only)):
        return read_settings(ctx.conn)

    @router.put("/settings")
    def put_settings(body: dict, user=Depends(admin_only)):
        try:
            saved = write_settings(ctx.conn, body)
        except ValueError as exc:
            log.info("vmupdate settings refused: %s", _one_line(exc))
            return JSONResponse({"ok": False, "error": SETTINGS_RULE, "code": "settings"},
                                status_code=400)
        journal(ctx.conn, "vmupdate_settings",
                f"memory_mib={saved['memory_mib']} cpus={saved['cpus']}", user[0])
        return saved

    @router.get("/network")
    def get_network(user=Depends(admin_only)):
        """‏#1468: "כרטיס לעדכוני VM", ה-MAC שנוצר לו, וה-lease האחרון."""
        return act(engine.network_view)

    @router.put("/network")
    def put_network(body: dict, user=Depends(admin_only)):
        if set(body) != {"interface"}:
            return JSONResponse({"ok": False, "error": "הגוף חייב להיות {\"interface\": שם|null}",
                                 "code": "vm_nic_invalid"}, status_code=400)
        return act(engine.set_network, body["interface"], user[0])

    @router.get("/status")
    def status(user=Depends(admin_only)):
        # הקריאה הזו היא גם ה"דופק" של המנהל (הכרעה 6): השולט שלא שאל
        # שתי דקות נחשב "איננו", ובקשת שליטה מאושרת אוטומטית.
        return engine.status(user[0])

    @router.post("/control/request")
    def control_request(user=Depends(admin_only)):
        return act(engine.request_control, user[0])

    @router.post("/control/answer")
    def control_answer(body: dict, user=Depends(admin_only)):
        approve = body.get("approve") if isinstance(body, dict) else None
        if type(approve) is not bool:
            return JSONResponse({"ok": False, "error": "נדרש approve: true/false",
                                 "code": "approve"}, status_code=400)
        return act(engine.answer_control, user[0], approve)

    @router.get("/precheck")
    def precheck(image_id: str, user=Depends(admin_only)):
        # חלון ההתחלה: מה ש-start היה מסרב לו, לפני הלחיצה (err-kvm, err-busy).
        return engine.precheck(image_id)

    @router.post("/start", status_code=202)
    def start(body: dict, user=Depends(admin_only)):
        image_id = body.get("image_id") if isinstance(body, dict) else None
        if not isinstance(image_id, str) or not image_id:
            return JSONResponse({"ok": False, "error": "נדרש image_id", "code": "image"},
                                status_code=400)
        return act(engine.start, image_id, user[0])

    @router.post("/shutdown")
    def shutdown(user=Depends(admin_only)):
        return act(engine.shutdown, user[0])

    @router.post("/force-off")
    def force_off(user=Depends(admin_only)):
        return act(engine.force_off, user[0])

    @router.post("/boot")
    def boot(user=Depends(admin_only)):
        return act(engine.boot, user[0])

    @router.post("/save", status_code=202)
    def save(body: dict, user=Depends(admin_only)):
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str):
            return JSONResponse({"ok": False, "error": "נדרש שם לגרסה", "code": "name"},
                                status_code=400)
        try:
            return act(engine.save, user[0], name, body.get("description") or "")
        except ValueError as exc:   # validate_display_name — נדחה בשמו, לא מנוקה
            log.info("vmupdate save refused: %s", _one_line(exc))
            return JSONResponse({"ok": False, "error": NAME_RULE, "code": "name"},
                                status_code=400)

    @router.post("/discard")
    def discard(user=Depends(admin_only)):
        return act(engine.discard, user[0])

    return router


def create_vmupdate_viewer_router(ctx: ServerContext, engine: VmUpdate, *,
                                  open_unix=_open_unix) -> APIRouter:
    """ה-WebSocket של המסך — מחוץ ל-`console_only` (שהוא Request בלבד), כמו
    המוניטור: הוא סוגר בעצמו, אחרי accept, ‏4401 בלי עוגייה ו-4403 לכל מי שאינו
    admin — כולל deploy — **לפני** שנפתח ה-socket של ה-VNC."""
    router = APIRouter(prefix="/api/console/vmupdate")
    viewers: set[str] = set()
    lock = asyncio.Lock()

    @router.websocket("/viewer")
    async def viewer(websocket: WebSocket):
        if not monitor.origin_allowed(websocket.headers):
            await websocket.close(code=monitor.WS_FORBIDDEN, reason=monitor.ORIGIN_REFUSED)
            return
        await monitor.accept_browser(websocket)
        found = auth.check(ctx.conn, websocket.cookies.get(auth.COOKIE_NAME))
        if found is None:
            await websocket.close(code=monitor.WS_UNAUTHENTICATED, reason="נדרשת התחברות")
            return
        if found[1] != "admin":
            await websocket.close(code=monitor.WS_FORBIDDEN, reason="פעולה למנהל בלבד")
            return
        path = engine.vnc_socket()
        if path is None:
            await websocket.close(code=WS_NO_VM, reason="אין VM רץ")
            return
        name = found[0]
        token = websocket.cookies.get(auth.COOKIE_NAME) or ""
        task = asyncio.current_task()
        async with lock:
            # צופה אחד לכל מנהל; כמה מנהלים — כן (‏share=force-shared). מי
            # שאינו השולט רואה בלבד: הקלט שלו נזרק בגשר (הכרעה 6).
            if name in viewers:
                await websocket.close(code=WS_NO_VM, reason="כבר פתוח אצלך מסך ל-VM")
                return
            viewers.add(name)
        if task is not None:
            viewer_sessions.register(token, name, task)
        engine.touch(name)
        writer = None
        try:
            try:
                reader, writer = await asyncio.wait_for(open_unix(str(path)), timeout=3.0)
                await asyncio.wait_for(qemu_handshake(reader, writer), timeout=5.0)
            except (OSError, asyncio.TimeoutError, monitor.MachineAuthError) as exc:
                await websocket.close(code=4502, reason=monitor.close_reason(
                    f"המסך של ה-VM אינו זמין: {exc}"))
                return
            address = websocket.client.host if websocket.client else "?"
            journal(ctx.conn, "vmupdate_viewer_open", address, found[0])
            presence = engine.viewer_join(name, address)
            outcome = "error"
            try:
                outcome = await monitor.bridge_browser(
                    websocket, reader, writer, idle_seconds=monitor.IDLE_SECONDS,
                    allow_input=lambda: engine.controls(name))
            except asyncio.CancelledError:
                # ‏#1394: רק ביטול ש-viewer_sessions עשה הוא ביטול התחברות.
                # כל ביטול אחר הוא פירוק החיבור (#725) — כמו לפני #1394.
                if task is None or not viewer_sessions.was_revoked(task):
                    outcome = "browser"
                    raise
                outcome = "session_revoked"
                await websocket.close(code=monitor.WS_UNAUTHENTICATED,
                                      reason="ההתחברות הסתיימה")
            except WebSocketDisconnect:
                outcome = "browser"
                raise
            finally:
                engine.viewer_leave(presence)
                journal(ctx.conn, "vmupdate_viewer_closed", outcome, found[0])
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass   # ראו monitor.py: ביטול ה-endpoint בפירוק החיבור אינו כשל (#725)
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            async with lock:
                viewers.discard(name)
            if task is not None:
                viewer_sessions.forget(token, task)

    return router
