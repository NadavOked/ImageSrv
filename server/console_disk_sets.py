"""נתיבי הקונסולה לסטים של דיסקים ולרשימת "כל הדיסקים" (#1857, מוקאפ 04). מנהל בלבד."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from . import disk_sets
from .api import ServerContext


def _guard(fn, *args):
    try:
        return fn(*args)
    except disk_sets.SetError as exc:
        raise HTTPException(exc.status, str(exc)) from None


async def _body(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "הגוף חייב להיות אובייקט JSON")
    return body


def register(router: APIRouter, ctx: ServerContext, admin_only) -> None:
    @router.get("/disk-sets")
    def list_sets(user=Depends(admin_only)):
        return disk_sets.list_sets(ctx.conn)

    @router.post("/disk-sets")
    async def create_set(request: Request, user=Depends(admin_only)):
        b = await _body(request)
        set_id = _guard(disk_sets.create, ctx.conn, b.get("name"), b.get("prefix"), b.get("students"),
                        b.get("instructor", True), user[0])
        return {"ok": True, "id": set_id}

    # ‏לפני `{set_id}`: "rounds" אינו מספר
    @router.get("/disk-sets/rounds")
    def assignable_rounds(user=Depends(admin_only)):
        return disk_sets.rounds(ctx.conn)

    @router.get("/disk-sets/{set_id}/members")
    def set_members(set_id: int, user=Depends(admin_only)):
        return _guard(disk_sets.members, ctx.conn, set_id)

    @router.post("/disk-sets/{set_id}/assign")
    async def assign(set_id: int, request: Request, user=Depends(admin_only)):
        b = await _body(request)
        return {"ok": True, "assigned": _guard(disk_sets.assign, ctx.conn, set_id, b.get("items"), user[0])}

    @router.post("/disk-sets/{set_id}/replace")
    async def replace(set_id: int, request: Request, user=Depends(admin_only)):
        b = await _body(request)
        _guard(disk_sets.replace, ctx.conn, set_id, b.get("suffix"), b.get("new_serial"), user[0])
        return {"ok": True}

    @router.delete("/disk-sets/members/{serial}")
    def remove_member(serial: str, user=Depends(admin_only)):
        _guard(disk_sets.remove, ctx.conn, serial, user[0])
        return {"ok": True}

    @router.get("/disks")
    def all_disks(user=Depends(admin_only)):
        """‏כל הדיסקים שהשרת מכיר + האפשרויות לסינון; הסינון עצמו בקונסולה (רשימה של מאות לכל היותר)."""
        rows = disk_sets.disks(ctx.conn)
        rows.sort(key=disk_sets.sort_key)
        return {"disks": rows}
