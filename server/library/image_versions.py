"""גרסאות אימג' וברירת המחדל לסבב חדש (‏#1334, הכרעה 2 של נדב 13/10).

**מה זו גרסה.** אימג' שנשמר מעדכון ב-VM הוא אימג' רגיל בספרייה (תיקייה +
מניפסט), עם שלושה שדות: `lineage` (מזהה הגרסה הראשונה), `version`
(מספר רץ) ו-`previous_id`. כל מה שנקלט לפני #1334 הוא גרסה 1 של שושלת
עצמו. הדיסק הוא מקור האמת (עיקרון 3) — שושלת אינה טבלה.

**ברירת המחדל** = מה שמחשב בנייה מקבל לסבב **חדש** (`allowed_for_disks`).
בלי הכרעה — הגרסה הגבוהה ביותר, ולכן שמירה מ-VM הופכת את החדשה לברירת
המחדל מעצמה. "קבע כברירת מחדל" על כל גרסה כותב הצבעה (`settings`,
‏`image_default:<שושלת>` — אין לה ייצוג טבעי כקובץ) ושורת יומן, **ואינו
מוחק אף גרסה**. סבב פתוח ממשיך עם המזהה שלו — הוא נשמר בסבב.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from server.console import auth
from server.agent.api import ServerContext
from server.db import journal, set_setting
from server.library.images import lineage_of, version_of

POINTER_PREFIX = "image_default:"


def pointers(conn) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE ?",
                        (POINTER_PREFIX + "%",)).fetchall()
    return {row[0][len(POINTER_PREFIX):]: row[1] for row in rows}


def open_round_images(conn) -> dict[str, int]:
    """כמה סבבים פתוחים משתמשים בכל אימג' — "בשימוש בסבב"."""
    rows = conn.execute("SELECT image_id, COUNT(*) FROM sessions "
                        "WHERE state IN ('open', 'running') GROUP BY image_id").fetchall()
    return {row[0]: row[1] for row in rows}


def versions(ctx: ServerContext, image_id: str) -> dict:
    scanned = ctx.library.scan()
    manifest = scanned.get(image_id)
    if manifest is None:
        raise HTTPException(404, "אין אימג' כזה בספרייה")
    lineage = lineage_of(manifest)
    members = [m for m in scanned.values() if lineage_of(m) == lineage]
    default = ctx.library.defaults(scanned).get(lineage)
    in_rounds = open_round_images(ctx.conn)
    rows = []
    for m in sorted(members, key=lambda m: (version_of(m), str(m.get("created", ""))),
                    reverse=True):
        rows.append({
            "id": m["id"], "name": m["name"], "description": m.get("description", ""),
            "version": version_of(m), "previous_id": m.get("previous_id"),
            "origin": m.get("origin") or "capture", "created": m.get("created", ""),
            "created_by": m.get("created_by", ""),
            "total_compressed_bytes": m.get("total_compressed_bytes"),
            "is_default": m["id"] == default, "open_rounds": in_rounds.get(m["id"], 0),
            "available": m.get("_available", True),
        })
    return {"lineage": lineage, "default_id": default,
            "latest_id": rows[0]["id"] if rows else None, "versions": rows}


def create_versions_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console/images")
    _, admin_only = auth.dependencies(ctx.conn)

    @router.get("/{image_id}/versions")
    def list_versions(image_id: str, user=Depends(admin_only)):
        return versions(ctx, image_id)

    @router.put("/{image_id}/default")
    def make_default(image_id: str, user=Depends(admin_only)):
        manifest = ctx.library.get(image_id)
        if manifest is None:
            raise HTTPException(404, "אין אימג' כזה בספרייה")
        lineage = lineage_of(manifest)
        before = ctx.library.defaults().get(lineage)
        set_setting(ctx.conn, POINTER_PREFIX + lineage, image_id)
        journal(ctx.conn, "image_default_set",
                f"{lineage}: {before} → {image_id} (v{version_of(manifest)})", user[0])
        return versions(ctx, image_id)

    return router
