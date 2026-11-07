"""נתיבי הקונסולה ללמידה האוטומטית ולהמלצת החיווט (#2028). הכול מנהל בלבד.

המתג עצמו הוא `learning_enabled` ב-`POST /settings`; כאן רק המצב והפעולות שסביבו.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from server.agent.api import ServerContext
from server.agent import registry
from server.learn import tuning, wiring


def _guard(fn, *args):
    try:
        return fn(*args)
    except tuning.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from None


def register(router: APIRouter, ctx: ServerContext, admin_only) -> None:
    @router.get("/learning")
    def learning_status(user=Depends(admin_only)):
        return tuning.status(ctx.conn)

    @router.post("/learning/changes/{change_id}/revert")
    def revert_one(change_id: int, user=Depends(admin_only)):
        return {"reverted": _guard(tuning.revert, ctx.conn, change_id, user[0])}

    @router.post("/learning/revert-all")
    def revert_all(user=Depends(admin_only)):
        return {"reverted": _guard(tuning.revert, ctx.conn, None, user[0])}

    def known(mac: str) -> str:
        canonical = registry.normalize_mac(mac)
        if canonical is None or ctx.conn.execute("SELECT 1 FROM machines WHERE mac = ?",
                                                 (canonical,)).fetchone() is None:
            raise HTTPException(404, "מכונה לא רשומה")
        return canonical

    @router.post("/machines/{mac}/learning")
    async def machine_offer(mac: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        _guard(tuning.answer_offer, ctx.conn, known(mac),
               body.get("action") if isinstance(body, dict) else None, user[0])
        return {"ok": True}

    @router.get("/machines/{mac}/wiring")
    def machine_wiring(mac: str, user=Depends(admin_only)):
        return wiring.advice(ctx.conn, known(mac))
