"""#980 — שתי פעולות של אובייקט "מחשבי שיכפול" בקונסולה שלא היה להן מקור.

1. **כיבוי כל המשכפלים** (`POST /api/console/room/poweroff`). **דרך הסוכן,
   לא `poweroff -f` מהשרת** (הכרעת נדב; #587): מכונה שכובתה בלי
   `arm_wol` אינה מתעוררת ב-WoL. השרת רק **רושם בקשה** לכל משכפל; הבקשה
   יוצאת בתשובת ה-hello הבא של המכונה (`power_action`, סעיף 3) — אותו
   hello באותו קצב, בלי polling חדש — והסוכן מכבה באותו מסלול כמו כיבוי
   ההמתנה (`idle.sh`: `arm_wol` נקרא בחזרה, ורק אז `poweroff -f`).
   בקשה נמסרת **פעם אחת** ופגה אחרי `POWEROFF_TTL_SECONDS`: מכונה שהייתה
   כבויה בזמן הלחיצה ועולה מחר אינה נכבית ברגע שהיא עולה.
2. **"השאר דלוקים"** (`POST /api/console/room/keep-on`, או `keep_on` בפתיחת
   סבב — #3123, #3064): המפעיל בוחר אילו משכפלים נשארים, **לפי שם או לפי
   מספר**, והשרת מכבה את השאר — באותו ערוץ של סעיף 1 (בקשה שנוסעת ב-hello,
   `arm_wol` ואז כיבוי). מכונה **באמצע סבב** (חברה בגל פתוח או רץ שלא סיים,
   או שיכפול מקומי כותב) אינה נכבית, ונרשמת ביומן כמי שדולגה, עם הסיבה.
   מי כובה ולמה — שורה ליומן לכל מכונה.
3. **היסטוריית סבבי החדר** (`GET /api/console/room/history`) — מטבלת
   `room_rounds`, שכבר שומרת כל סבב.

**לא בראוטר של `room.py`**: הוא נכנס בשלמותו לקיוסק (`kiosk.py`), וכיבוי
החדר הוא פעולת ניהול שאין לה מקום על וילן ההפצה.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from server.brain import memory_view
from server.rounds import room_stream   # #3978: the column "הרשת"
from server.console import auth
from server.core import machine_names
from server.learn import waits as learn_waits
from server.db import _write_lock, journal, now_iso, writing
from server.agent import registry
from server.agent.registry import ROOM_GROUPS
from server.agent.reports import FINAL
from server.rounds.room import (
    CLONERS_GROUP, active_round, local_copy_now, round_label, visible_round,
)
from server.rounds.room_state import _roster

#: מה שהשרת מבקש מסוכן דרך ה-hello. reboot — "אתחול מרחוק" ממגירת המחשב
#: (#977 §4, `wake_api.py`): הצרכן שחסר כש-#980 פתח רק את הכיבוי.
POWEROFF = "poweroff"
REBOOT = "reboot"
#: כמה זמן בקשת כיבוי ממתינה ל-hello של המכונה. ה-hello של משכפל ממתין
#: מגיע לכל המאוחר כל `POLL_MAX` (15 ש', `agent/lib/core/poll.sh`); 120 ש' הם
#: שמונה פעימות — ומעבר לזה מכונה שלא ענתה אינה "תכבה אחר כך".
POWEROFF_TTL_SECONDS = 120
HISTORY_DEFAULT = 10
HISTORY_MAX = 50


def _cloners_label(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT label FROM groups WHERE id = ?", (CLONERS_GROUP,)).fetchone()
    return row["label"] if row else None


def request_power(conn: sqlite3.Connection, macs: list[str], action: str,
                  user: str, confirmed: bool = False) -> int:
    """בקשה אחת לכל MAC (בקשה קודמת שעוד ממתינה — נדרסת). מחזיר כמה נרשמו.

    #1367 (נדב 14/10): ``confirmed`` = המפעיל אישר מעל האזהרה "המחשב באמצע
    שיכפול מקומי". רק בקשה כזו מכבה מכונה שכותבת; בלעדיו הסוכן זורק אותה."""
    at = now_iso()
    with _write_lock, writing(conn):
        for mac in macs:
            conn.execute(
                "INSERT INTO power_requests (mac, action, requested_at, requested_by, confirmed)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (mac) DO UPDATE SET"
                " action = excluded.action, requested_at = excluded.requested_at,"
                " requested_by = excluded.requested_by, confirmed = excluded.confirmed",
                (mac, action, at, user, 1 if confirmed else 0))
    return len(macs)


def take_power(conn: sqlite3.Connection, mac: str,
               now: datetime | None = None) -> tuple[str | None, bool]:
    """מה שה-hello של `mac` נושא ב-`power_action`: `"poweroff"`, `"reboot"` או None.

    הבקשה נמחקת בקריאה — נמסרת פעם אחת. סוכן שלא הצליח לדרוך WoL נשאר
    דולק ואומר זאת, ואינו מקבל את הבקשה שוב בכל פעימה. בקשה שפגה נמחקת
    ואינה נמסרת (ראו `POWEROFF_TTL_SECONDS`)."""
    row = conn.execute(
        "SELECT action, requested_at, confirmed FROM power_requests WHERE mac = ?",
        (mac,)).fetchone()
    if row is None:
        return None, False
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM power_requests WHERE mac = ?", (mac,))
    try:
        at = datetime.fromisoformat(row["requested_at"])
    except (TypeError, ValueError):
        return None, False                # חותמת שאינה נקראת — לא מכבים
    age = (now or datetime.now(timezone.utc)) - at
    if age > timedelta(seconds=POWEROFF_TTL_SECONDS) or age < timedelta(0):
        journal(conn, "power_expired", f"{mac} {row['action']}", machine=mac)
        return None, False
    confirmed = bool(row["confirmed"])
    journal(conn, "power_delivered",
            f"{mac} {row['action']}" + (" (confirmed over a local copy)" if confirmed else ""),
            machine=mac)
    if row["action"] == POWEROFF:
        _leave_open_wave(conn, mac)
    return row["action"], confirmed


def _leave_open_wave(conn: sqlite3.Connection, mac: str) -> None:
    """#65: מכונה שנאמר לה לכבות יוצאת מהגל **הפתוח** — באותו hello שמוסר
    את הכיבוי (ה-hello הזה עצמו כבר צירף אותה מחדש). אחרת המגירות שדיווחה
    לפני שכבתה נספרות ב-`ready_drives`, ו"התחל בכל זאת" היה משגר גל עם
    מכונה כבויה. כשתעלה אחרי החלפת המגירות — ה-hello שלה מצרף אותה שוב.
    גל שכבר רץ אינו נוגע כאן: לא מכבים מכונה באמצע כתיבה (`/room/poweroff`
    ו-`/missing/{mac}/poweroff` מסרבים לזה)."""
    with _write_lock, writing(conn):
        conn.execute(
            "DELETE FROM session_members WHERE mac = ? AND session_id IN"
            " (SELECT id FROM sessions WHERE state = 'open')", (mac,))


# --- #3123 / #3064: "השאר דלוקים" ---------------------------------------------

def _room_machines(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """כל מחשבי השיכפול הרשומים (כל קבוצה שתפקידה cloner, #1554), לפי שם."""
    return conn.execute(
        "SELECT m.mac, m.suffix, d.last_seen, d.local_clone FROM machines m"
        " LEFT JOIN net_devices d ON d.mac = m.mac"
        f" WHERE m.group_id IN {ROOM_GROUPS} ORDER BY m.suffix").fetchall()


def resolve_keep(conn: sqlite3.Connection, tokens: object) -> list[str]:
    """הרשימה ש-המפעיל בחר → MAC-ים של משכפלים רשומים, או ValueError.

    כל איבר: מספר (``3``, ``"3"``), שם תצוגה (``"משכפל-3"``), השם הטכני
    (``"cloner-3"``) או MAC. **רשימה ריקה היא שגיאה, לא "כבה את כולם"**: זו
    בקשה אחרת, שיש לה נתיב משלה והקלדה משלו (``/room/poweroff``). איבר שלא
    נמצא, או שמתאים ליותר ממכונה אחת, מבטל את כל הבקשה — לא מכבים את
    החדר לפי רשימה שנקראה חלקית (עיקרון 5)."""
    if not isinstance(tokens, list) or not tokens:
        raise ValueError("לא נבחר אף משכפל להישאר דלוק — כיבוי כל החדר הוא פעולה אחרת")
    rows = _room_machines(conn)
    keep: list[str] = []
    for token in tokens:
        mac = registry.normalize_mac(token) if isinstance(token, str) else None   # שם אינו MAC תקין
        hits = ([r["mac"] for r in rows if r["mac"] == mac] if mac else
                [r["mac"] for r in rows if machine_names.match("cloner", token, r["suffix"])])
        if not hits:
            raise ValueError(f"אינו מחשב שיכפול רשום: {str(token)[:32]}")
        if len(hits) > 1:
            raise ValueError(f"'{str(token)[:32]}' מתאים ליותר ממחשב אחד — {len(hits)} מחשבים")
        if hits[0] not in keep:
            keep.append(hits[0])
    return keep


def _busy(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict[str, str]:
    """מי **באמצע סבב** ואסור לכבות: mac → הסיבה (קוד).

    ``in_round`` — חבר בגל פתוח או רץ שלא הגיע למצב סופי (``reports.FINAL``);
    גל שנסגר אינו חוסם, וזה בדיוק המקרה (#3064): אחרי הגל הראשון נשארו
    מעט דיסקים, והשאר צריכים להיכבות. ``local_copy`` — שיכפול מקומי כותב
    עכשיו (כיבוי שובר את היעד, #1367)."""
    busy: dict[str, str] = {}
    for r in conn.execute(
            "SELECT DISTINCT sm.mac FROM session_members sm JOIN sessions s ON s.id = sm.session_id"
            " WHERE s.state IN ('open', 'running') AND sm.state NOT IN ({})".format(
                ",".join("?" * len(FINAL))), tuple(sorted(FINAL))):
        busy[r["mac"]] = "in_round"
    for r in rows:
        if r["mac"] not in busy and local_copy_now(r["local_clone"], r["last_seen"]):
            busy[r["mac"]] = "local_copy"
    return busy


_BUSY_HE = {"in_round": "באמצע סבב", "local_copy": "באמצע שיכפול מקומי"}


def keep_on(conn: sqlite3.Connection, keep: list[str], user: str, why: str,
            protect: frozenset[str] | set[str] = frozenset()) -> dict:
    """משאיר דלוקים את ``keep`` ומבקש כיבוי מכל שאר המשכפלים — חוץ ממי שבאמצע סבב.

    ``protect`` — מי שנשאר דלוק גם בלי להיבחר (בפתיחת סבב: המשכפלים של הסבב
    עצמו, שה-WoL של הפתיחה מעיר). ``why`` — מאיפה הבקשה, נכנס לכל שורת יומן.
    ``keep`` מגיע מ-``resolve_keep`` (לא ריק, רק משכפלים רשומים). מחזיר מה נעשה:
    ``{"kept", "off", "busy"}``, כל אחד ``[{mac, name}]``."""
    rows = _room_machines(conn)
    role_name = {r["mac"]: machine_names.label("cloner", r["suffix"]) for r in rows}
    busy = _busy(conn, rows)
    stay = set(keep) | set(protect)
    off = [r["mac"] for r in rows if r["mac"] not in stay and r["mac"] not in busy]
    skipped = [r["mac"] for r in rows if r["mac"] not in stay and r["mac"] in busy]
    kept_names = ", ".join(role_name[m] for m in keep if m in role_name)
    if off:
        request_power(conn, off, POWEROFF, user)
    for mac in off:
        journal(conn, "room_keep_on_off", f"{mac} — {why}; נשארים דלוקים: {kept_names}", user, machine=mac)
    for mac in skipped:
        journal(conn, "room_keep_on_busy",
                f"{mac} — לא נכבה, {_BUSY_HE[busy[mac]]}; {why}", user, machine=mac)
    journal(conn, "room_keep_on",
            f"{why}; נשארים דלוקים: {kept_names}; כיבוי התבקש: {len(off)}; דולגו (באמצע סבב): {len(skipped)}",
            user)
    def named(macs) -> list[dict]:
        return [{"mac": m, "name": role_name.get(m, m)} for m in macs]

    return {"kept": named(sorted(stay & set(role_name))), "off": named(off),
            "busy": [{**n, "reason": busy[n["mac"]]} for n in named(skipped)]}


def round_protect(conn: sqlite3.Connection) -> set[str]:
    """המשכפלים של הסבב הפעיל (הבחירה שלו) — לא נכבים בפתיחה גם אם לא נבחרו ב"השאר דלוקים"."""
    row = active_round(conn)
    return set(_roster(row) or []) if row is not None else set()


def round_history(ctx, limit: int) -> list[dict]:
    """סבבי חדר שהסתיימו (`closed`/`failed`), מהחדש לישן. הסבב שמוצג
    עכשיו ב-`GET /room` (פעיל, או כושל שעוד לא נסגר) אינו כאן."""
    shown = visible_round(ctx.conn)
    rows = ctx.conn.execute(
        "SELECT * FROM room_rounds WHERE state IN ('closed', 'failed') AND id != ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (shown["id"] if shown is not None else "", limit)).fetchall()
    return [{
        "id": r["id"],
        "state": r["state"],
        "image_id": r["image_id"],
        "image_name": round_label(ctx, r),
        "source_kind": r["source_kind"],
        "target_drives": r["target_drives"],
        "written_drives": r["written_drives"],
        "opened_by": r["opened_by"],
        "created_at": r["created_at"],
        "closed_at": r["closed_at"],
        "failed_reason": r["failed_reason"],
        # #1889: כמה זמן הסבב חיכה לאדם, ועל מה (`learn_waits.round_waits`; null = לא נמדד).
        "waits": learn_waits.round_waits(ctx.conn, r["created_at"], r["closed_at"]),
        # #3185: "קצב ואיבוד" -- מ-`wave_rates` של גלי הסבב; ``null`` בשדה = לא נשמר / לא נמדד, לא 0.
        "flow": memory_view.flow(memory_view.round_waves(ctx.conn, r["created_at"], r["closed_at"])),
        # #3978: "הרשת" -- מה שמנוע החתיכות רשם על הגל האחרון של הסבב (`room_stream.network`); null = לא נרשם גל.
        "network": room_stream.network(room_stream.recorded(ctx.conn, r["created_at"], r["closed_at"])),
    } for r in rows]


def create_room_console_router(ctx) -> APIRouter:
    router = APIRouter(prefix="/api/console/room")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/history")
    def history(limit: int = HISTORY_DEFAULT, user=Depends(current_user)):
        # קריאה בלבד — פתוחה לכל מחובר, כמו `GET /room`.
        if limit < 1:
            raise HTTPException(400, "limit חייב להיות לפחות 1")
        return round_history(ctx, min(limit, HISTORY_MAX))

    @router.get("/history/{round_id}/summary")
    def history_summary(round_id: str, user=Depends(current_user)):
        """#3185: השורה שנפתחת בהיסטוריית הסבבים -- הגלים, המקלטים, הדיסקים והחלטות המוח."""
        row = ctx.conn.execute("SELECT created_at, closed_at FROM room_rounds WHERE id = ?", (round_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "אין סבב כזה")
        return memory_view.round_summary(ctx.conn, row["created_at"], row["closed_at"])
    @router.post("/keep-on")
    async def keep_on_route(request: Request, user=Depends(admin_only)):
        """#3123: ``{"keep": [3, "משכפל-5", "cloner-6"]}`` — אלה נשארים דלוקים, השאר
        מתבקשים להיכבות (חוץ ממי שבאמצע סבב). מנהל בלבד, כמו `/poweroff`: כיבוי
        משכפלים אינו פעולה של וילן ההפצה (משתמש הפצה מסורב בקונסולה). בלי הקלדת שם: הקונסולה מציגה
        את הרשימה בחלון אישור, ובקשה שלא הובנה נדחית בשלמותה (``resolve_keep``)."""
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        try:
            keep = resolve_keep(ctx.conn, body.get("keep") if isinstance(body, dict) else None)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        result = keep_on(ctx.conn, keep, user[0], "השאר דלוקים (מדף החדר)")
        return {**result, "requested": len(result["off"]), "ttl_s": POWEROFF_TTL_SECONDS}

    @router.post("/poweroff")
    async def poweroff(request: Request, user=Depends(admin_only)):
        # גוף ריק או לא-JSON נופל לאישור ריק — 400, כמו `/room/close`.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        label = _cloners_label(ctx.conn)
        if label is None or typed != label:
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם קבוצת מחשבי השיכפול")
        shown = visible_round(ctx.conn)
        if shown is not None and shown["state"] in ("active", "closing"):
            # כיבוי באמצע גל מפיל כתיבה חיה — הסבב נעצר קודם, בהקלדה משלו.
            raise HTTPException(409, "יש סבב חדר פעיל — עצרו אותו לפני כיבוי החדר")
        confirmed = isinstance(body, dict) and body.get("confirm_local_copy") is True
        macs = [r["mac"] for r in ctx.conn.execute(
            f"SELECT mac FROM machines WHERE group_id IN {ROOM_GROUPS}")]   # #1554
        count = request_power(ctx.conn, macs, POWEROFF, user[0], confirmed=confirmed)
        journal(ctx.conn, "room_poweroff", f"requested={count}" +
                (" local copy confirmed" if confirmed else ""), user[0])
        return {"requested": count, "ttl_s": POWEROFF_TTL_SECONDS}

    return router
