"""‏#1882–#1889 — מה שהשרת אסף על מכונה, לקונסולה (מגירת המכונה, "נתונים שנאספו").

קריאה בלבד, מנהל בלבד — כמו מגירת המכונה עצמה. כל מפתח בתשובה שייך ל-Issue אחד, ו-`null`
בו = "לא נאסף / לא נקרא", לעולם לא "אין" (עיקרון 5).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from server.agent import agent_logs, cloner_cpu, registry
from server.console import auth
from server.net import pxe_log
from server.learn import net_history


def create_console_data_router(ctx) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    def _mac(raw: str) -> str:
        canonical = registry.normalize_mac(raw)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        return canonical

    @router.get("/machines/{mac}/data")
    def machine_data(mac: str, user=Depends(admin_only)):
        canonical = _mac(mac)
        return {"mac": canonical,
                "agent_logs": agent_logs.list_for(ctx.conn, canonical),   # #1882
                "cpu": cloner_cpu.history(ctx.conn, canonical),            # #1885
                "pxe": pxe_log.verdict(ctx.conn, canonical,                  # #1887
                                       pxe_log.since_last_wake(ctx.conn, canonical)),
                "ip_history": net_history.ip_history(ctx.conn, canonical),   # #1888
                "nic_history": net_history.nic_history(ctx.conn, canonical),
                "wave_drops": net_history.wave_drops(ctx.conn, canonical)}

    @router.get("/agent-logs/{log_id}")
    def agent_log_text(log_id: int, user=Depends(admin_only)):
        """‏#1882: הטקסט (מסונן) של רשומה אחת. ‏`text: null` = הרשומה פגומה ולא נפרסה."""
        got = agent_logs.text_of(ctx.conn, log_id)
        if got is None:
            raise HTTPException(404, "אין רשומה כזו")
        return got

    return router
