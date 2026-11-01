"""לשונית "רשת" — מה חי ברשת, לפי מה שהשרת ראה בפועל.

כל מכונה שדיברה עם השרת (hello או תפריט אתחול) נרשמת אוטומטית עם
הכתובת שקיבלה. הרשימה כוללת גם מכונות שאינן בטבלת ה-MAC — זה בדיוק
הערך שלה: לראות מה יש ברשת לפני שמחליטים למי לתת תפקיד.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from server.console import auth
from server.agent import boottrace, registry
from server.agent.api import ServerContext
from server.db import journal, now_iso, update_one


def create_net_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/net")
    def devices(user=Depends(current_user)):
        """ההתקנים + האם הם רשומים, ובאיזו קבוצה.

        ‏#400: וגם **היכן באתחול** כל מכונה נמצאת. זו הרשימה שכל מכונה
        שדיברה עם השרת מופיעה בה, ולכן זה המקום היחיד שבו מחשב שיכפול
        חסר-ראש שנתקע בין התפריט ל-hello יכול להיראות בכלל.
        """
        rows = ctx.conn.execute(
            "SELECT d.mac, d.ip, d.description, d.first_seen, d.last_seen,"
            "       d.disk_probe, d.firmware, d.secure_boot, d.agent_version,"
            "       m.suffix, g.id AS group_id, g.label AS group_label, g.role,"
            "       b.step AS boot_step, b.at AS boot_at"
            "  FROM net_devices d"
            "  LEFT JOIN machines m ON m.mac = d.mac"
            "  LEFT JOIN groups g ON g.id = m.group_id"
            "  LEFT JOIN boot_steps b ON b.mac = d.mac"
            " ORDER BY d.last_seen DESC"
        ).fetchall()
        return [
            {
                "mac": r["mac"], "ip": r["ip"], "description": r["description"],
                "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                "registered": r["suffix"] is not None,
                "name": r["suffix"], "group_id": r["group_id"],
                "group_label": r["group_label"], "role": r["role"],
                "boot": boottrace.describe(r["boot_step"], r["boot_at"]),
                # ‏#402: drives/no_disks/no_ports/unchecked; null = סוכן ישן
                "disk_probe": r["disk_probe"],
                # ‏#500: uefi/bios ו-true/false כפי שנמדדו; null = לא דווח
                "firmware": r["firmware"],
                "secure_boot": None if r["secure_boot"] is None else bool(r["secure_boot"]),
                # ‏#1257: גרסת ה-initrd שהמכונה דיווחה; null = לא דווח
                "agent_version": r["agent_version"],
            }
            for r in rows
        ]

    @router.post("/net")
    async def add_device(request: Request, user=Depends(admin_only)):
        """הוספה ידנית — למכונה שעוד לא דיברה עם השרת."""
        body = await request.json()
        mac = registry.normalize_mac(body.get("mac", ""))
        if mac is None:
            raise HTTPException(400, "MAC לא תקין")
        ts = now_iso()
        ctx.conn.execute(
            "INSERT INTO net_devices (mac, ip, description, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (mac) DO UPDATE SET description = excluded.description",
            (mac, (body.get("ip") or "").strip() or None,
             (body.get("description") or "").strip(), ts, ts),
        )
        ctx.conn.commit()
        journal(ctx.conn, "net_add", mac, user[0])
        return {"mac": mac}

    @router.put("/net/{mac}")
    async def describe_device(mac: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        canonical = registry.normalize_mac(mac)
        if not update_one(
            ctx.conn,
            "UPDATE net_devices SET description = ? WHERE mac = ?",
            ((body.get("description") or "").strip(), canonical),
        ):
            raise HTTPException(404, "התקן לא ברשימה")
        ctx.conn.commit()
        journal(ctx.conn, "net_describe", f"{canonical} {body.get('description', '')}", user[0])
        return {"ok": True}

    @router.delete("/net/{mac}")
    def forget_device(mac: str, user=Depends(admin_only)):
        """הסרה מהרשימה בלבד. אם המכונה תדבר שוב — היא תחזור."""
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        if not update_one(
            ctx.conn, "DELETE FROM net_devices WHERE mac = ?", (canonical,)
        ):
            raise HTTPException(404, "התקן לא ברשימה")
        ctx.conn.commit()
        journal(ctx.conn, "net_forget", canonical, user[0])
        return {"ok": True}

    return router
