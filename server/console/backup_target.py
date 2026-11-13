"""`POST /api/console/backup-target`, `/backup-policy` — יעד גיבוי המסד, השעה והמקום השמור
(#1956). admin בלבד.

כלי המערכת רק דרך `storage_hooks` שמוזרקים ל-`create_app` (ניתוק יעד SMB/NFS ישן). הבדיקות מזייפות אותם.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from server.console import auth
from server.system import backup_target
from server.storage import locations as sl
from server.agent.api import ServerContext


def create_backup_target_router(ctx: ServerContext, hooks: dict | None = None,
                                data_dir: str | Path | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)
    hooks = {**sl.default_hooks(), **(hooks or {})}
    data_dir = Path(data_dir) if data_dir else Path(".")

    @router.post("/backup-target")
    async def choose(request: Request, user=Depends(admin_only)):
        body = await request.json()
        try:
            return backup_target.configure(ctx.conn, data_dir, body, hooks, user[0])
        except backup_target.TargetError as exc:
            raise HTTPException(exc.status, exc.reason) from None

    @router.post("/backup-policy")
    async def set_policy(request: Request, user=Depends(admin_only)):
        body = await request.json()
        try:
            return backup_target.set_policy(ctx.conn, data_dir, body, user[0])
        except backup_target.TargetError as exc:
            raise HTTPException(exc.status, exc.reason) from None

    return router
