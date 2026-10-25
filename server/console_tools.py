"""ארגז הכלים (#649, שלב 1) — הקטלוג והבחירה של נדב, לקונסולה.

הקטלוג הוא `server/tools_catalog.json` — נגזר משני מסמכי הבחירה
(`docs/tools/TOOLS-CHOICE.md`, `docs/tools/CATALOG.md`) על ידי
`tools/tools-catalog-from-docs.py`, ו-`tests/test_tools_catalog.py` נופל
כשהוא סוטה מהם. השרת **אינו** ממציא כלים ואינו משנה אותם.

הבחירה — שתי רשימות id נפרדות, **בנייה/שיכפול** ו-**תלמיד** (תלמיד = v2,
אבל הבחירה נשמרת כבר עכשיו) — נשמרת בשני מקומות: ‏`settings` (‏
`tools.selection`, JSON) ו-`<data_dir>/tools-selection.json`. הקובץ הוא מה
שבניית ה-initrd תקרא בשלב 2 (‏`build_initramfs.sh --tools-selection`), ולכן
הוא נכתב **בכל שמירה**, לא רק ב-DB. id שאינו בקטלוג נדחה ב-422 בשמו —
לא נזרק בשקט (עיקרון 5).

הכרעת נדב 19/09: v1 יוצאת בלי ארגז הכלים (v1.1 = הכלים). כש-
`capabilities.tools()` כבוי שני המסלולים עונים **404 בשם** — לפני
הזדהות, כי הדף אינו קיים במהדורה הזו (לא 403: אין כאן "אסור לך").
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, capabilities
from .atomicfile import write_atomic
from .api import ServerContext
from .db import get_setting, journal, set_setting

CATALOG_PATH = Path(__file__).parent / "tools_catalog.json"
SELECTION_KEY = "tools.selection"
SELECTION_FILE = "tools-selection.json"
TARGETS = ("build", "student")


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _empty() -> dict[str, list[str]]:
    return {t: [] for t in TARGETS}


def read_selection(conn) -> dict[str, list[str]]:
    """הבחירה השמורה; חסרה או פגומה → ריקה (ולא חריגה — הדף חייב לעלות)."""
    raw = get_setting(conn, SELECTION_KEY)
    if not raw:
        return _empty()
    try:
        data = json.loads(raw)
    except ValueError:
        return _empty()
    return {t: [i for i in data.get(t, []) if isinstance(i, str)] for t in TARGETS}


def summarize(tools: list[dict], ids: list[str]) -> dict:
    """ספירה וגודל משוער של הכלים המסומנים. ‏`size_kb_known` סוכם רק מה
    שנמדד ואינו ארוז; ‏`size_unknown` סופר את המסומנים בלי מספר — כדי
    שהדף יגיד "+K לא נמדדו" ולא יציג סכום חלקי כמלא (עיקרון 5)."""
    by_id = {t["id"]: t for t in tools}
    chosen = [by_id[i] for i in ids if i in by_id]
    known = sum(t["size_kb"] for t in chosen if not t["packed"] and t["size_kb"] is not None)
    unknown = sum(1 for t in chosen if not t["packed"] and t["size_kb"] is None)
    return {"count": len(chosen), "size_kb_known": known, "size_unknown": unknown}


def write_selection_file(data_dir: Path, selection: dict[str, list[str]]) -> Path:
    path = Path(data_dir) / SELECTION_FILE
    # ‏#1828: אטומי — קובץ בחירה חתוך אינו "שום דבר לא נבחר".
    write_atomic(path, json.dumps({"schema": 1, **selection}, ensure_ascii=False, indent=1) + "\n")
    return path


def _edition_gate() -> None:
    """‏v1 בלי ארגז הכלים: הדף אינו קיים, ולכן 404 — לכל קורא, לפני auth."""
    if not capabilities.tools():
        raise HTTPException(404, "ארגז הכלים אינו במהדורה זו (v1.1)")


def create_tools_router(ctx: ServerContext, data_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/console", dependencies=[Depends(_edition_gate)])
    current_user, admin_only = auth.dependencies(ctx.conn)
    catalog = load_catalog()
    known_ids = {t["id"] for t in catalog["tools"]}

    @router.get("/tools/catalog")
    def tools_catalog(user=Depends(admin_only)):
        selection = read_selection(ctx.conn)
        return {"groups": catalog["groups"], "tools": catalog["tools"], "selection": selection,
                "summary": {t: summarize(catalog["tools"], selection[t]) for t in TARGETS}}

    @router.put("/tools/selection")
    async def put_selection(request: Request, user=Depends(admin_only)):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, "הגוף חייב להיות אובייקט עם build ו-student")
        selection: dict[str, list[str]] = {}
        for target in TARGETS:
            ids = body.get(target)
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise HTTPException(422, f"{target}: חייבת להיות רשימת מזהי כלים")
            unknown = sorted(set(ids) - known_ids)
            if unknown:
                raise HTTPException(422, f"{target}: כלים שאינם בקטלוג: {', '.join(unknown)}")
            selection[target] = sorted(set(ids), key=ids.index)   # בלי כפילויות, בסדר שנשלח
        set_setting(ctx.conn, SELECTION_KEY, json.dumps(selection, ensure_ascii=False))
        write_selection_file(data_dir, selection)
        journal(ctx.conn, "tools_selection",
                f"build={len(selection['build'])} student={len(selection['student'])}", user[0])
        return {"ok": True, "selection": selection,
                "summary": {t: summarize(catalog["tools"], selection[t]) for t in TARGETS}}

    return router
