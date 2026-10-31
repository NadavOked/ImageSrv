"""API למיקומי אחסון — `/api/console/storage-locations` (#1066 שלב א').

admin בלבד (deploy → 403). מחוץ ל-allowlist של הקיוסק. כלי המערכת רק
דרך `storage_hooks` שמוזרקים ל-`create_app`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from server.console import auth
from server.agent.api import ServerContext
from server.db import _settle, _write_lock, journal, now_iso, writing
from server.storage import storage_disks
from server.storage import storage_locations as sl

log = logging.getLogger("imagesrv.storage_locations")


def create_storage_locations_router(
    ctx: ServerContext,
    hooks: dict | None = None,
    data_dir: str | Path | None = None,
    server_base: str = "",
) -> APIRouter:
    router = APIRouter(prefix="/api/console/storage-locations")
    current_user, admin_only = auth.dependencies(ctx.conn)
    del current_user
    hooks = {**sl.default_hooks(), **(hooks or {})}
    data_dir = Path(data_dir) if data_dir else Path(".")

    def _row(loc_id: str) -> dict:
        row = sl.get(ctx.conn, loc_id)
        if row is None:
            raise HTTPException(404, "מיקום לא קיים")
        return row

    def _validate(kind: str, params: dict) -> None:
        """‏#1123 (3): 400 בשם השדה, לפני שכלי מערכת כלשהו רץ."""
        reason = sl.validate_params(kind, params)
        if reason:
            raise HTTPException(400, reason)

    def _delete_row(loc_id: str) -> None:
        _settle(ctx.conn)
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute("DELETE FROM storage_locations WHERE id = ?", (loc_id,))

    def _fstab_or_unmount(row: dict) -> None:
        """‏#1123 (4): עיגון בלי שורת fstab אינו `connected` — הוא לא ישרוד reboot."""
        written = sl.rewrite_fstab(ctx.conn, hooks, include=row["id"])   # ‏#1993
        if written.status != sl.OK:
            hooks["umount"](row["mount_point"])
            raise HTTPException(
                409, f"העיגון הצליח אבל fstab לא נכתב — בוטל: {written.reason}")

    @router.get("")
    def list_locations(user=Depends(admin_only)):
        del user
        sl.refresh_catalog(ctx.conn, ctx.library)
        rows = sl.list_rows(ctx.conn)
        return {
            "locations": [sl.public_row(r, ctx.library) for r in rows],
            "summary": sl.summary_of(rows, ctx.library),
        }

    @router.post("/scan")
    async def scan(request: Request, user=Depends(admin_only)):
        del user
        body = await request.json()
        kind = body.get("type")
        server = body.get("server") or ""
        creds = body.get("creds") or {}
        if kind in ("nfs", "smb") and not sl.valid_host(server):
            raise HTTPException(400, "כתובת שרת לא תקינה — IP או שם מארח (RFC 1123)")
        if kind == "nfs":
            result = hooks["nfs_scan"](server)
        elif kind == "smb":
            _validate("smb", {"server": server, "share": "x", **creds})  # תווי בקרה ב-creds
            result = hooks["smb_scan"](server, creds)
        elif kind == "iscsi":
            portal = body.get("portal") or server
            if not sl.valid_portal(portal):
                raise HTTPException(400, "פורטל לא תקין — IP[:port] או שם מארח[:port]")
            chap = creds if creds.get("user") or creds.get("chap_user") else None
            if chap and "user" not in chap and creds.get("chap_user"):
                chap = {"user": creds.get("chap_user"), "secret": creds.get("chap_secret")}
            result = hooks["iscsi_discover"](portal, chap)
        else:
            raise HTTPException(400, "סריקה רק ל-nfs / smb / iscsi")
        if result.status == sl.UNCHECKED:
            raise HTTPException(503, result.reason or "לא הצלחנו לסרוק")
        if result.status != sl.OK:
            raise HTTPException(409, result.reason or "הסריקה נכשלה")
        return {"ok": True, "items": (result.payload or {}).get("items") or []}

    @router.post("/test")
    async def test(request: Request, user=Depends(admin_only)):
        del user
        body = await request.json()
        kind = body.get("type")
        params = body.get("params") or {}
        if kind not in sl.TYPES:
            raise HTTPException(400, "סוג לא מוכר")
        _validate(kind, params)
        tmp = data_dir / "storage-test"
        outcome = sl.test_connection(
            kind, params, hooks, tmp_target=tmp, server_base=server_base)
        return {
            "ok": bool(outcome.get("ok")),
            "free_bytes": outcome.get("free_bytes"),
            "total_bytes": outcome.get("total_bytes"),
            "warnings": outcome.get("warnings") or [],
            "reason": outcome.get("reason") or "",
            "targets": outcome.get("targets"),
        }

    @router.post("")
    async def create(request: Request, user=Depends(admin_only)):
        body = await request.json()
        if body.get("tested") is not True:
            raise HTTPException(422, "אין יצירה בלי בדיקת חיבור מוצלחת באותה בקשה")
        kind = body.get("type")
        name = (body.get("name") or "").strip()
        params = dict(body.get("params") or {})
        if kind not in sl.TYPES:
            raise HTTPException(400, "סוג לא מוכר")
        if not name:
            raise HTTPException(400, "חסר שם תצוגה")
        _validate(kind, params)
        existing = ctx.conn.execute(
            "SELECT id FROM storage_locations WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            raise HTTPException(409, f"כבר יש מיקום בשם {name}")
        tmp = data_dir / "storage-test"
        outcome = sl.test_connection(
            kind, params, hooks, tmp_target=tmp, server_base=server_base)
        if not outcome.get("ok"):
            raise HTTPException(
                409, outcome.get("reason") or "בדיקת החיבור נכשלה — אין יצירה")
        loc_id = sl.new_id()
        if kind == "local":
            mount = str(Path(params.get("path") or params.get("mount_point") or ""))
            if not mount:
                raise HTTPException(400, "חסר נתיב")
        else:
            mount = str(sl.mount_point_for(data_dir, name, loc_id))
        taken = ctx.conn.execute(
            "SELECT name FROM storage_locations WHERE mount_point = ?", (mount,)
        ).fetchone()
        if taken:
            raise HTTPException(409, f"כבר יש מיקום על הנתיב הזה: {taken['name']}")
        now = now_iso()
        # ‏#1123 (4): מיקום רשת נולד `unchecked` — `connected` רק אחרי fstab וגם mount.
        state = "connected" if kind == "local" else "unchecked"
        try:
            _settle(ctx.conn)
            with _write_lock, writing(ctx.conn):
                ctx.conn.execute(
                    "INSERT INTO storage_locations (id, name, type, params_json,"
                    " mount_point, state, state_since, state_detail, created_by,"
                    " created_at, last_images_json, last_df_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, '[]', ?)",
                    (loc_id, name, kind, json.dumps(params, ensure_ascii=False), mount,
                     state, now, user[0], now,
                     json.dumps({"free_bytes": outcome.get("free_bytes"),
                                 "total_bytes": outcome.get("total_bytes"),
                                 "checked_at": now})
                     if outcome.get("free_bytes") is not None else None),
                )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"כבר יש מיקום על הנתיב הזה: {mount}") from None
        row = sl.get(ctx.conn, loc_id)
        if kind in ("nfs", "smb"):
            written = sl.rewrite_fstab(ctx.conn, hooks)
            if written.status != sl.OK:
                _delete_row(loc_id)
                raise HTTPException(
                    409, f"fstab לא נכתב — המיקום לא נוצר: {written.reason}")
            mounted = sl.apply_mount(row, hooks)
            if mounted.status != sl.OK:
                _delete_row(loc_id)
                undone = sl.rewrite_fstab(ctx.conn, hooks)
                if undone.status != sl.OK:
                    log.error("fstab line for %s not removed after mount failure: %s",
                              loc_id, undone.reason)
                if kind == "smb" and sl.smb_creds(params):
                    hooks["creds_remove"](sl.creds_path(loc_id))
                raise HTTPException(409, mounted.reason or "העיגון נכשל")
            sl.set_state(ctx.conn, loc_id, "connected", "")
        elif kind == "local":
            space = hooks["df"](mount)
            if space.status == sl.OK:
                sl.save_df(ctx.conn, loc_id, space.payload.get("free_bytes"),
                           space.payload.get("total_bytes"))
        journal(ctx.conn, "storage_location_create",
                f"{loc_id} {kind} {name}", user[0])
        return sl.public_row(sl.get(ctx.conn, loc_id), ctx.library)

    # ‏#1988: "הוסף כונן" — כוננים ריקים בשרת, ואז חלוקה+ext4+fstab לפי UUID.
    @router.get("/disks")
    def disks(user=Depends(admin_only)):
        del user
        try:
            return storage_disks.blank_disks(hooks)
        except storage_disks.AddDiskError as exc:
            raise HTTPException(exc.status, exc.message) from None

    @router.post("/add-disk")
    async def add_disk(request: Request, user=Depends(admin_only)):
        body = await request.json()
        if body.get("confirm_erase") is not True:
            raise HTTPException(422, "נדרש אישור: כל המידע בכונן יימחק")
        try:
            row = await run_in_threadpool(
                storage_disks.add_disk, ctx.conn, hooks, data_dir,
                device=str(body.get("device") or ""), serial=str(body.get("serial") or ""),
                size_bytes=body.get("size_bytes"), name=str(body.get("name") or ""),
                user=user[0])
        except storage_disks.AddDiskError as exc:
            raise HTTPException(exc.status, exc.message) from None
        sl.refresh_catalog(ctx.conn, ctx.library)
        return sl.public_row(row, ctx.library)

    @router.post("/{loc_id}/iscsi-login")
    def iscsi_login(loc_id: str, user=Depends(admin_only)):
        row = _row(loc_id)
        if row["type"] != "iscsi":
            raise HTTPException(400, "התחברות ליעד רק למיקום iSCSI")
        params = sl.params_of(row)
        chap = None
        if params.get("chap_user"):
            chap = {"user": params.get("chap_user"), "secret": params.get("chap_secret")}
        result = hooks["iscsi_login"](
            params.get("iqn") or "", params.get("portal") or "",
            chap, bool(params.get("auto", True)))
        if result.status != sl.OK:
            status = 503 if result.status == sl.UNCHECKED else 409
            raise HTTPException(status, result.reason or "ההתחברות נכשלה")
        payload = result.payload or {}
        if payload.get("device_by_path"):
            params["device_by_path"] = payload["device_by_path"]
        sl.save_params(ctx.conn, loc_id, params)
        journal(ctx.conn, "storage_iscsi_login",
                f"{loc_id} {params.get('iqn')}", user[0])
        return {"ok": True, "device_by_path": params.get("device_by_path") or "",
                "iqn": params.get("iqn"), "portal": params.get("portal")}

    @router.get("/{loc_id}/disk")
    def disk(loc_id: str, user=Depends(admin_only)):
        del user
        row = _row(loc_id)
        params = sl.params_of(row)
        device = params.get("device_by_path")
        if not device:
            raise HTTPException(409, "אין התקן — יש להתחבר ליעד קודם")
        result = hooks["disk_probe"](device)
        payload = dict(result.payload or {})
        payload.setdefault("device", device)
        if result.status == sl.UNCHECKED:
            payload["disk_status"] = "unknown"
            payload["reason"] = result.reason or "לא הצלחנו לקרוא את הדיסק"
            return payload
        if result.status != sl.OK:
            payload["disk_status"] = "unknown"
            payload["reason"] = result.reason
            return payload
        payload.setdefault("disk_status",
                           "fs" if payload.get("fs_type") else "empty")
        return payload

    @router.post("/{loc_id}/mount")
    async def mount_existing(loc_id: str, request: Request, user=Depends(admin_only)):
        row = _row(loc_id)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if body.get("format"):
            raise HTTPException(400, "עיגון עם פירמוט עובר ב-/format")
        params = sl.params_of(row)
        device = params.get("device_by_path")
        if row["type"] == "iscsi":
            if not device:
                raise HTTPException(409, "אין התקן — יש להתחבר ליעד קודם")
            probed = hooks["disk_probe"](device)
            payload = probed.payload or {}
            status = payload.get("disk_status")
            if probed.status != sl.OK or status == "unknown":
                raise HTTPException(409, "לא הצלחנו לקרוא את הדיסק — אין עיגון")
            if status == "empty" or not payload.get("fs_type"):
                raise HTTPException(409, "אין מערכת קבצים על הדיסק — עיגון בלי פירמוט רק כשיש FS")
            params["fs_type"] = payload.get("fs_type")
            params["fs_label"] = payload.get("fs_label")
            params["fs_size"] = payload.get("fs_size")
            sl.save_params(ctx.conn, loc_id, params)
            row = sl.get(ctx.conn, loc_id)
        mounted = sl.apply_mount(row, hooks)
        if mounted.status != sl.OK:
            raise HTTPException(409, mounted.reason or "העיגון נכשל")
        _fstab_or_unmount(row)
        sl.set_state(ctx.conn, loc_id, "connected", "", force_since=True)
        space = hooks["df"](row["mount_point"])
        if space.status == sl.OK:
            sl.save_df(ctx.conn, loc_id, space.payload.get("free_bytes"),
                       space.payload.get("total_bytes"))
        journal(ctx.conn, "storage_location_mount",
                f"{loc_id} {row['name']}", user[0])
        return sl.public_row(sl.get(ctx.conn, loc_id), ctx.library)

    @router.post("/{loc_id}/format")
    async def format_disk(loc_id: str, request: Request, user=Depends(admin_only)):
        row = _row(loc_id)
        body = await request.json()
        params = sl.params_of(row)
        iqn = params.get("iqn") or ""
        confirm = body.get("confirm")
        if not confirm:
            raise HTTPException(422, "פירמוט דורש הקלדת ה-IQN")
        if confirm != iqn:
            raise HTTPException(403, "ה-IQN שהוקלד אינו זהה ליעד")
        device = params.get("device_by_path")
        if not device:
            raise HTTPException(409, "אין התקן — יש להתחבר ליעד קודם")
        probed = hooks["disk_probe"](device)
        payload = probed.payload or {}
        disk_status = payload.get("disk_status")
        if probed.status == sl.UNCHECKED or disk_status == "unknown":
            raise HTTPException(409, "לא הצלחנו לקרוא את הדיסק — לא מפרמטים")
        has_fs = disk_status == "fs" or bool(payload.get("fs_type"))
        if has_fs and not body.get("wipe"):
            raise HTTPException(409, "על הדיסק יש מערכת קבצים — פירמוט רק עם wipe ואישור IQN")
        made = hooks["make_fs"](device, row["name"])
        if made.status != sl.OK:
            raise HTTPException(409, made.reason or "הפירמוט נכשל")
        params["fs_type"] = "ext4"
        params["fs_label"] = row["name"]
        if payload.get("fs_size") is not None:
            params["fs_size"] = payload.get("fs_size")
        sl.save_params(ctx.conn, loc_id, params)
        row = sl.get(ctx.conn, loc_id)
        mounted = sl.apply_mount(row, hooks)
        if mounted.status != sl.OK:
            raise HTTPException(409, mounted.reason or "העיגון אחרי הפירמוט נכשל")
        _fstab_or_unmount(row)
        sl.set_state(ctx.conn, loc_id, "connected", "", force_since=True)
        space = hooks["df"](row["mount_point"])
        if space.status == sl.OK:
            sl.save_df(ctx.conn, loc_id, space.payload.get("free_bytes"),
                       space.payload.get("total_bytes"))
        journal(ctx.conn, "storage_location_format",
                f"{loc_id} {iqn}", user[0])
        return sl.public_row(sl.get(ctx.conn, loc_id), ctx.library)

    @router.post("/{loc_id}/check")
    def check(loc_id: str, user=Depends(admin_only)):
        row = _row(loc_id)
        result = sl.measure(row, hooks)
        if result.status == sl.OK:
            sl.save_df(ctx.conn, loc_id, result.payload.get("free_bytes"),
                       result.payload.get("total_bytes"))
            sl.set_state(ctx.conn, loc_id, "connected", "", force_since=row["state"] != "connected")
            if row["state"] != "connected":
                journal(ctx.conn, "storage_location_connected",
                        f"{loc_id} {row['name']}", user[0])
        else:
            detail = result.reason or "הבדיקה נכשלה"
            sl.set_state(ctx.conn, loc_id, "unreachable", detail,
                         force_since=row["state"] != "unreachable")
            journal(ctx.conn, "storage_location_unreachable",
                    f"{loc_id} {row['name']} — {detail}", user[0])
        sl.refresh_catalog(ctx.conn, ctx.library)
        return sl.public_row(sl.get(ctx.conn, loc_id), ctx.library)

    @router.post("/{loc_id}/disconnect")
    def disconnect(loc_id: str, user=Depends(admin_only)):
        row = _row(loc_id)
        if row["id"] == sl.LOCAL_ID:
            raise HTTPException(409, "המיקום המקומי אינו ניתן לניתוק")
        sl.refresh_catalog(ctx.conn, ctx.library)
        hooks["umount"](row["mount_point"])
        params = sl.params_of(row)
        if row["type"] == "iscsi" and params.get("iqn"):
            hooks["iscsi_logout"](params.get("iqn"), params.get("portal") or "")
        sl.set_state(ctx.conn, loc_id, "disconnected", "המפעיל ניתק", force_since=True)
        sl.rewrite_fstab(ctx.conn, hooks)
        journal(ctx.conn, "storage_location_disconnect",
                f"{loc_id} {row['name']}", user[0])
        return sl.public_row(sl.get(ctx.conn, loc_id), ctx.library)

    @router.post("/{loc_id}/connect")
    def connect(loc_id: str, user=Depends(admin_only)):
        row = _row(loc_id)
        params = sl.params_of(row)
        if row["type"] == "iscsi":
            chap = None
            if params.get("chap_user"):
                chap = {"user": params.get("chap_user"),
                        "secret": params.get("chap_secret")}
            logged = hooks["iscsi_login"](
                params.get("iqn") or "", params.get("portal") or "",
                chap, bool(params.get("auto", True)))
            if logged.status != sl.OK:
                raise HTTPException(409, logged.reason or "ההתחברות נכשלה")
            if (logged.payload or {}).get("device_by_path"):
                params["device_by_path"] = logged.payload["device_by_path"]
                sl.save_params(ctx.conn, loc_id, params)
                row = sl.get(ctx.conn, loc_id)
        mounted = sl.apply_mount(row, hooks)
        if mounted.status != sl.OK:
            raise HTTPException(409, mounted.reason or "העיגון נכשל")
        _fstab_or_unmount(row)
        sl.set_state(ctx.conn, loc_id, "connected", "", force_since=True)
        space = hooks["df"](row["mount_point"])
        if space.status == sl.OK:
            sl.save_df(ctx.conn, loc_id, space.payload.get("free_bytes"),
                       space.payload.get("total_bytes"))
        journal(ctx.conn, "storage_location_connect",
                f"{loc_id} {row['name']}", user[0])
        return sl.public_row(sl.get(ctx.conn, loc_id), ctx.library)

    @router.delete("/{loc_id}")
    async def delete(loc_id: str, request: Request, user=Depends(admin_only)):
        row = _row(loc_id)
        if row["id"] == sl.LOCAL_ID:
            raise HTTPException(409, "המיקום המקומי אינו ניתן להסרה")
        body = await request.json()
        if body.get("confirm") != row["name"]:
            raise HTTPException(403, "השם שהוקלד אינו זהה לשם המיקום")
        if row["state"] != "disconnected":
            raise HTTPException(409, "הסרה רק אחרי ניתוק")
        sl.refresh_catalog(ctx.conn, ctx.library)
        total, _ = sl.image_counts(ctx.library, loc_id)
        if total:
            raise HTTPException(409, f"על המיקום יש {total} אימג'ים — אין הסרה")
        # גם מטמון אחרון: אימג'ים על מיקום מנותק עדיין נספרים
        cached = sl.last_images(sl.get(ctx.conn, loc_id))
        if cached:
            raise HTTPException(409, f"על המיקום יש {len(cached)} אימג'ים — אין הסרה")
        _delete_row(loc_id)
        sl.rewrite_fstab(ctx.conn, hooks)
        if row["type"] == "smb":
            hooks["creds_remove"](sl.creds_path(loc_id))
        journal(ctx.conn, "storage_location_delete",
                f"{loc_id} {row['name']}", user[0])
        return {"ok": True}

    return router
