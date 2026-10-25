"""פתיחת סבב ממסך התחנה — זרימה 13.3, הנקודה השנייה.

נדב ניגש למחשב אחד בכיתה, מזדהה, בוחר כיתה מהרשימה ואימג' — והשרת
מעיר את שאר המחשבים ב-WoL. הם עולים ב-PXE, מצטרפים לסבב, והוא חופשי
ללכת: הסבב חי בשרת, לא במחשב שפתח אותו.

ברירות המחדל נגזרות מהשרת, לא מוקלדות: הקידומת ממזהה הקבוצה, ומספר
המחשבים ממספר הרשומות בטבלה — התחנה לא צריכה לדעת כמה מחשבים יש בכיתה.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .progress_view import capture_progress
from .agent_version import server_version
from . import capabilities, minfree, ports, storage_locations, timezone
from .api import ServerContext, _error, agent_credentials, identity_gate
from .db import journal
from .hello import class_deploy_enabled
from .images import restore_refusal
from .registry import normalize_mac
from .sessions import SessionError


#: מי רשאי לפתוח סבב מהתחנה. רשימת-היתר מפורשת, ולא "הסיסמה נכונה".
#:
#: היום זה כל התפקידים שקיימים, ולכן זה לא משנה התנהגות. אבל הקוד קרא
#: את ``role`` ולא בדק אותו, ומשמעות ברירת המחדל השקטה הזאת היא
#: שתפקיד שלישי — צופה, מבקר, חשבון ניטור — היה מקבל ביום היוולדו את
#: הזכות למחוק דיסקים בכיתה שלמה. "לא ידענו מה התפקיד" הוא סירוב.
ROUND_OPENER_ROLES = ("admin", "deploy")


def classrooms_off() -> JSONResponse | None:
    """הכרעת נדב 11/10: "השרת מסרב לפקודות כיתות כל עוד הכיתות כבויות" —
    גם בנתיבי הסוכן, ובלי תלות במתג ‏`class_deploy_enabled` (#880). ‏409
    בשם, **לפני** כל בדיקה אחרת (MAC, זהות, סיסמה): נתיב שאין מאחוריו כלום
    לא בודק סיסמאות (#1282). ‏`None` = הכיתות דלוקות (v1.2)."""
    if capabilities.classrooms():
        return None
    return _error(409, "כיתות אינן במהדורה זו (v1.2)", "classrooms_off")


def default_prefix(group_id: str) -> str:
    return group_id.removeprefix("grp_").upper() or "CLASS"


def create_station_router(ctx: ServerContext,
                          version: Callable[[], str | None] | None = None) -> APIRouter:
    """‏`version` (‏#1252) — תג ה-git של השרת, אותו מקור כמו `/me.version`;
    ‏`None` (בדיקות ישנות) = לא ידוע, ואז `server_version` הוא `null`."""
    router = APIRouter(prefix="/api/v1/agent")
    # Populate before concurrent polls arrive; cache also retains None.
    # #1252's injected reader (tests, /me's source) wins, read once here too (#1257).
    running_version = version() if version else server_version()

    @router.get("/groups")
    def classes():
        """הכיתות, לרשימת הבחירה שבתחנה. שמות בלבד — אין כאן סודות
        שה-hello לא חושף ממילא."""
        refused = classrooms_off()
        if refused is not None:
            return refused
        rows = ctx.conn.execute(
            "SELECT g.id, g.label, COUNT(m.mac) AS machines FROM groups g"
            " LEFT JOIN machines m ON m.group_id = g.id"
            " WHERE g.role = 'classroom' GROUP BY g.id ORDER BY g.sort, g.id"
        ).fetchall()
        return [dict(r) for r in rows]

    @router.get("/groups/{group_id}/machines")
    def class_machines(group_id: str):
        """מחשבי הכיתה, לחלון בחירת המחשבים — בשמות, ה-MAC רק כמזהה."""
        refused = classrooms_off()
        if refused is not None:
            return refused
        group = ctx.conn.execute(
            "SELECT role FROM groups WHERE id = ?", (group_id,)
        ).fetchone()
        if group is None or group["role"] != "classroom":
            return _error(400, "not a classroom group", "bad_group")
        rows = ctx.conn.execute(
            "SELECT mac, suffix AS name FROM machines WHERE group_id = ?"
            " ORDER BY suffix", (group_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    @router.get("/sessions/active")
    def active_session_view():
        """תצוגת הסבב הפעיל למסכי התחנה — בלי כניסה.

        עמוד התחנה רץ כקיוסק, בלי אדם ליד: ההתנתקות האוטומטית של
        הקונסולה (חוסר פעילות) מוחקת את ה-cookie המשותף לדפדפן, ומסך
        שנשען עליו קפא באמצע סבב והציג מצב ישן (#34). זו תצוגה בלבד —
        אותו מידע שחברי הסבב מקבלים ומציגים על המסכים בכיתה ממילא;
        כל פעולה (פתיחה, התחלה, עצירה) עדיין דורשת כניסה.
        """
        session = ctx.store.active()
        if session is None:
            return {"session": None}
        return {"session": ctx.store.view(ctx.store.maybe_start(session), ctx.library)}

    @router.get("/state")
    def station_state(request: Request, mac: str = ""):
        """מה שמסך התחנה הגרפי מציג: זהות, דיסקים, והמשימה הרצה.

        בלי כניסה — זה אותו מידע שהמכונה עצמה מקבלת ב-hello, והדף רץ
        על המכונה עצמה. פעולות (יצירת קליטה) כן דורשות כניסה.
        """
        canonical = normalize_mac(mac)
        if canonical is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, canonical, request, "state")
        if refused is not None:
            return refused

        machine = ctx.conn.execute(
            "SELECT m.suffix, g.label, g.role FROM machines m"
            " JOIN groups g ON g.id = m.group_id WHERE m.mac = ?", (canonical,)
        ).fetchone()
        device = ctx.conn.execute(
            "SELECT disks_json, agent_version FROM net_devices WHERE mac = ?", (canonical,)
        ).fetchone()
        task = ctx.conn.execute(
            "SELECT * FROM tasks WHERE mac = ? ORDER BY created_at DESC LIMIT 1",
            (canonical,),
        ).fetchone()

        disks = (json.loads(device["disks_json"])
                 if device and device["disks_json"] else [])
        # #706: images allowed for THIS machine's disks, so the graphical
        # restore screen offers a picker instead of the crashing text flow.
        # {id,name,folder} per image; the server filters (interface rule 3).
        allowed_images = []
        # #1863: by size only (required_bytes + min_free), as hello and the console.
        for _iid in ctx.library.allowed_for_disks(disks, minfree.min_free_gb(ctx.conn)):
            _m = ctx.library.get(_iid)
            if _m:
                allowed_images.append({"id": _iid, "name": _m["name"],
                                       "folder": _m.get("folder", "")})
        return {
            "mac": canonical,
            "known": machine is not None,
            "agent_version": device["agent_version"] if device else None,
            "server_version": running_version,
            "role": machine["role"] if machine else "unknown",
            "name": machine["suffix"] if machine else None,
            "group_label": machine["label"] if machine else None,
            "disks": disks,
            "allowed_images": allowed_images,
            # ‏#1896: אימג' ישן עם שתי מחיצות windows שוות — "לא נבדק" במסך, עם
            # סימון C: (‏{id,name,folder,candidates}); מסומן — עובר ל-allowed_images.
            "unchecked_images": ctx.library.unmarked_images(minfree.min_free_gb(ctx.conn)),
            # #1081: edition flag, same as hello. Missing = off. v2 turns this on.
            "classrooms": capabilities.classrooms(),
            # v1 without the toolbox (Nadav, 19/09), same as hello. v1.1 turns this on.
            "tools": capabilities.tools(),
            # #1252: the GUI header said "imagectl-srv · 10.10.10.1 · v0.48.1" --
            # mockup strings. The name is /me's (#936: the setting, else the host
            # name); the version is /me's (#915: the git tag) and null when it was
            # not read -- the screen then says "unknown", never a number.
            # The version itself is "server_version" above (#1257: read once).
            "server_name": ports.server_name(ctx.conn),
            "tz": timezone.posix_tz(ctx.conn),
            "task": {
                "id": task["id"], "type": task["type"], "state": task["state"],
                "disk": task["disk"], "name": task["name"], "error": task["error"],
                **capture_progress(task),
            } if task else None,
        }

    @router.post("/sessions")
    async def open_from_station(request: Request) -> JSONResponse:
        """פתיחת סבב. תמיד עם שם וסיסמה — גם כשההצטרפות חופשית,
        פתיחה היא החלטה (סעיף 13.3: סיסמה → כיתה → אימג')."""
        refused = classrooms_off()
        if refused is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")

        # ‏#997: המכונה שפותחת — לפני הסיסמה. זר בוילן אינו פותח סבב על
        # כיתה שלמה בשם תחנה, גם עם סיסמה שדלפה (ו-`bad_mac` קודם לזהות).
        opener = normalize_mac(body.get("mac"))
        if opener is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, opener, request, "sessions")
        if refused is not None:
            return refused

        if not class_deploy_enabled(ctx.conn):
            # ‏#880: v1 "מהדורת שיכפול" — הסוכן מסתיר את הכרטיס, והשרת
            # מסרב גם למי שלא הסתיר. ‏#1282: **לפני** הסיסמה — אחרת נתיב
            # שאין מאחוריו שום פתיחה היה עונה 409 לנכונה ו-401 לשגויה,
            # כלומר בודק סיסמאות לכל מי שבוילן.
            journal(ctx.conn, "class_deploy_refused",
                    f'{body.get("username", "")} at station round open')
            return _error(409, "הפצה לכיתות כבויה (v1 מהדורת שיכפול)",
                          "class_deploy_disabled")
        # ‏#1282: אותו שער כמו `/agent/login` — ‏login_guard + החלפה כפויה.
        role, refused = agent_credentials(
            ctx, request, body.get("username", ""), body.get("password", ""),
            f'{body.get("username", "")} at station round open')
        if refused is not None:
            return refused
        if role not in ROUND_OPENER_ROLES:
            journal(ctx.conn, "agent_role_refused",
                    f'{body.get("username", "")} role={role} at station round open')
            return _error(403, "התפקיד הזה אינו רשאי לפתוח סבב", "role_not_allowed")

        group_id = body.get("group_id", "")
        group = ctx.conn.execute(
            "SELECT role FROM groups WHERE id = ?", (group_id,)
        ).fetchone()
        if group is None or group["role"] != "classroom":
            return _error(400, "not a classroom group", "bad_group")
        manifest = ctx.library.get(body.get("image_id", ""))
        if manifest is None:
            return _error(400, "unknown image", "no_image")
        unavailable = storage_locations.unavailable_message(manifest)
        if unavailable:
            return _error(409, unavailable, "image_unavailable")

        machines = ctx.conn.execute(
            "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (group_id,)
        ).fetchone()["n"]
        if machines == 0:
            return _error(400, "the class has no registered machines", "empty_group")

        # בחירת מחשבים (רשות): הסבב מיועד רק להם. בלי השדה — כל הכיתה.
        roster = None
        if body.get("macs") is not None:
            if not isinstance(body["macs"], list):
                return _error(400, "macs must be a list", "bad_macs")
            roster = [normalize_mac(m) for m in body["macs"]]
            if not roster or None in roster:
                return _error(400, "missing or malformed mac in macs", "bad_macs")
            roster = sorted(set(roster))     # בחירה כפולה אינה שני מחשבים

        # ‏#381, אחרי שה-roster ידוע: אימג' קשור נפרס רק על המכונה שלו,
        # וכיתה שלמה בלי בחירת מחשבים אינה "רק היא".
        refusal = restore_refusal(manifest, roster)
        if refusal is not None:
            journal(ctx.conn, "session_image_bound",
                    f'{body.get("image_id", "")} — {refusal}')
            return _error(400, refusal, "image_bound_to_another_machine")

        try:
            session_id = ctx.store.open(
                group_id, body["image_id"],
                prefix=body.get("prefix") or default_prefix(group_id),
                expected_clients=len(roster) if roster else machines,
                opened_by=body["username"].strip(),
                opener_mac=opener,
                roster=roster,
            )
        except SessionError as exc:
            return _error(409, str(exc), "session_conflict")

        session = ctx.conn.execute(
            "SELECT prefix, expected_clients FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return JSONResponse({
            "id": session_id,
            "prefix": session["prefix"],
            "expected_clients": session["expected_clients"],
        })

    return router
