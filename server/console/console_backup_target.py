"""‏`POST /api/console/backup-target` — בחירת יעד גיבוי המסד (#1956). admin בלבד.

כלי המערכת רק דרך `storage_hooks` שמוזרקים ל-`create_app` — אותם hooks של מיקומי
האחסון (#1066), ועוד `is_mount` (‏`os.path.ismount`). הבדיקות מזייפות אותם.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from server.console import auth
from server.system import backup_target
from server.storage import storage_locations as sl
from server.agent.api import ServerContext


def create_backup_target_router(ctx: ServerContext, hooks: dict | None = None,
                                data_dir: str | Path | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console/backup-target")
    _, admin_only = auth.dependencies(ctx.conn)
    hooks = {**sl.default_hooks(), "is_mount": os.path.ismount, **(hooks or {})}
    data_dir = Path(data_dir) if data_dir else Path(".")

    @router.post("")
    async def choose(request: Request, user=Depends(admin_only)):
        body = await request.json()
        try:
            return backup_target.configure(ctx.conn, data_dir, body, hooks, user[0])
        except backup_target.TargetError as exc:
            raise HTTPException(exc.status, exc.reason) from None

    return router
