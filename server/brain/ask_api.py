"""#3654 (K14): הנתיבים של ערוץ השאלה למשתמש -- הקונסולה והסוכן, על `server/brain/ask.py`.

**שני מקומות עונים, ושניהם עונים לאותה שאלה:**

- **הקונסולה** (`/api/console/brain/asks`, מנהל): רשימת השאלות הפתוחות ואלה שנסגרו לאחרונה, ותשובה. משתמש הפצה אינו
  נכנס לקונסולה (#1073), ולכן אין לו כאן נתיב.
- **המכונה** (`POST /api/v1/agent/ask-answer`, פורט הסוכן): מי שיושב ליד המסך של המכונה. אין שם משתמש מחובר, והשרת מזהה
  את המכונה לפי ה-MAC מול חכירת ה-DHCP (`identity_gate`, כמו `shrink-note`); **מכונה עונה רק על שאלה על עצמה**, ורק
  על שאלה שה-`who` שלה כולל `machine`. הסוכן לומד שאלות מתשובת ה-hello (`asks`, `hello.py`).

תשובה אחת סוגרת את השאלה בכל מקום: מי שמאחר מקבל 409 ואינו דורס. הסיכום ב-`docs/architecture/interfaces.md` סעיף 50.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from server.brain import ask

#: כמה שאלות שנסגרו הקונסולה מציגה (ההיסטוריה המלאה היא ב-`brain_decisions`).
RECENT = 20


def create_console_router(ctx) -> APIRouter:
    from server.console import auth                    # noqa: PLC0415 -- ייבוא עצל, כמו `services.create_router`
    router = APIRouter(prefix="/api/console/brain")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/asks")
    def asks(days: int = ask.HISTORY_DAYS, state: str | None = None, who: str | None = None, kind: str | None = None,
             q: str | None = None, limit: int = ask.HISTORY_LIMIT, user=Depends(current_user)):
        """השאלות הפתוחות (הישנה ראשונה), ואלה שנסגרו לאחרונה (``recent``, החדשה ראשונה: לפעמון), וההיסטוריה של 30 הימים
        האחרונים עם סינון (``history``: דף "שאלות המוח"). פקיעה נקראת כאן, לא רק בשעון. ערך סינון שאינו מוכר -- 400."""
        try:
            history = ask.history(ctx.conn, days=days, state=state or None, who=who or None, kind=kind or None, q=q,
                                  limit=limit)
        except ValueError as exc:
            raise HTTPException(400, f"סינון לא מוכר: {exc}") from exc
        return {"open": ask.open_asks(ctx.conn), "recent": ask.recent_closed(ctx.conn, RECENT), "history": history}

    @router.post("/asks/{ask_id}/answer")
    async def answer(ask_id: int, request: Request, user=Depends(admin_only)):
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001 -- גוף ריק או לא-JSON = אין אפשרות
            body = {}
        option = body.get("option") if isinstance(body, dict) else None
        return await run_in_threadpool(_answer, ask_id, option, user[0])

    def _answer(ask_id: int, option, who: str) -> dict:
        if not isinstance(option, str):
            raise HTTPException(400, "חסרה האפשרות שנבחרה")
        try:
            return ask.answer(ctx.conn, ask_id, option, by=who, role=ask.ADMIN)
        except ask.AskError as exc:
            raise HTTPException(exc.status, exc.message_he) from exc

    return router


def create_agent_router(ctx) -> APIRouter:
    from server.agent import registry                  # noqa: PLC0415
    from server.agent.api import _error, _read_body, identity_gate, lenient_mac   # noqa: PLC0415 -- api.py טוען את המודול הזה לפני כן
    router = APIRouter(prefix="/api/v1/agent")

    @router.post("/ask-answer")
    async def ask_answer(request: Request) -> JSONResponse:
        """התשובה של מי שליד המסך של המכונה: ``{"mac", "ask_id", "option"}``. 200 עם השאלה כפי שנסגרה; 409 כשכבר נסגרה
        (המסך סוגר את הדיאלוג, והשאלה שנסגרה ב-hello הבא אומרת מה קרה); 403 לשאלה שאינה על המכונה הזו."""
        body, refused = await _read_body(request)
        if refused is not None:
            return refused
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        return await run_in_threadpool(_answer, request, body)

    def _answer(request: Request, body: dict) -> JSONResponse:
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "ask-answer")
        if refused is not None:
            return refused
        if registry.lookup(ctx.conn, mac) is None:
            return _error(403, "this mac is not registered", "unknown_mac")
        ask_id, option = body.get("ask_id"), body.get("option")
        if isinstance(ask_id, bool) or not isinstance(ask_id, int) or not isinstance(option, str):
            return _error(400, "ask_id must be an integer and option a string", "bad_answer")
        try:
            done = ask.answer(ctx.conn, ask_id, option, by=mac, role=ask.MACHINE, only_entity=("machine", mac))
        except ask.AskError as exc:
            return _error(exc.status, exc.message_he, exc.code)
        return JSONResponse({"ok": True, "ask": done})

    return router
