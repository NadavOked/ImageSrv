"""הפצה ישירה — מחשב הבנייה כמקור המולטיקאסט (‏#715, ‏v1: ‏#888).

"בלי לעבור בשרת" = האימג' **לא נכתב ולא נשמר** בשרת (הכרעת נדב 12/09).
השרת נשאר המתזמר היחיד: סבב חדר (`room.py`) עם `source_kind =
build_disk`, גל רגיל על `grp_CLONERS`, אותם דיווחי התקדמות, אותה
קונסולה. מה שמשתנה הוא **המקור**: במקום `SenderEngine` על קובץ מהספרייה,
מחשב הבנייה מקבל משימת `direct_send` (ממשק 3) ומריץ `udp-sender` על
הדיסק שלו, קריאה בלבד. הבייטים זורמים מחשב-בנייה → מגירות נבחרות.

מה כן עובר בשרת: **המניפסט**. מחשב הבנייה קורא את הדיסק פעם אחת כמו
בקליטה (‏`capture_disk` בלי העלאה), שולח את המניפסט ל-`PUT
/api/v1/direct/<task>/manifest`, והמקבלים מושכים אותו כ-`GET
/api/v1/images/live_…/manifest` — אותו נתיב בדיוק כמו לאימג' מהספרייה,
ולכן `restore.sh` **אינו משתנה**. הגל אינו מתחיל לפני שהמניפסט הגיע:
מקבל שמתחיל בלי מניפסט אין לו מה לאמת מולו (עיקרון 5).

סבב **יחיד**: הסט הנבחר קבוע (‏`target_slots` חובה, ‏`target_drives` =
סכומו), ובסיום הגל הסבב נסגר — גם אם מגירה נכשלה. גלי-החלפה מדיסק חי
נשארו הכרעה פתוחה (‏#715), והמלצת Astra — סבב יחיד — היא מה שנבנה.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import secrets
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from . import drive_rates, registry, room, room_drivers, sender as sender_module
from .db import _write_lock, journal, now_iso, writing
from .images import restore_refusal
from .sessions import SessionError
from .tasks import (ORIGIN_CONSOLE, OPEN_STATES, TOKEN_HEADER, active_task, claim_task,
                    new_token)

log = logging.getLogger("imagectl.direct")

#: ערכי `room_rounds.source_kind`.
LIBRARY = "library"
BUILD_DISK = "build_disk"
#: סוג המשימה של המקור (‏`tasks.type`).
DIRECT_SEND = "direct_send"
#: מזהה אימג' **חי** — לא בספרייה, לא בתיקייה. הצורה שונה מ-`img_` בכוונה:
#: ‏`valid_image_id` דוחה אותו, ולכן שום נתיב שכותב לספרייה לא יקבל אותו.
LIVE_ID = re.compile(r"live_[0-9a-f]{8}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def new_live_id() -> str:
    return "live_" + secrets.token_hex(4)


def is_live_id(value: object) -> bool:
    return isinstance(value, str) and LIVE_ID.fullmatch(value) is not None


def default_multicast() -> dict:
    """הפרמטרים כשאין מנוע שידור (בדיקות יחידה, resolver של התפריט)."""
    return {
        "portbase": sender_module.DEFAULT_PORTBASE,
        "max_wait": sender_module.DEFAULT_MAX_WAIT,
        "start_timeout": sender_module.DEFAULT_START_TIMEOUT,
        "max_wait_later": sender_module.later_max_wait(sender_module.DEFAULT_MAX_WAIT),
        "start_timeout_later": sender_module.later_start_timeout(
            sender_module.DEFAULT_MAX_WAIT, sender_module.DEFAULT_START_TIMEOUT),
        "retries_until_drop": sender_module.DEFAULT_RETRIES_UNTIL_DROP,
        "max_bitrate": None,
    }


# --- פתיחה --------------------------------------------------------------------


def _reported_devs(conn: sqlite3.Connection, mac: str) -> set[str]:
    return {d.get("dev") for d in room._disks(conn, mac)
            if isinstance(d, dict) and isinstance(d.get("dev"), str)}


def open_direct_round(ctx, source: dict, target_slots, target_drives,
                      user: str, driver_models=None, origin: str = ORIGIN_CONSOLE) -> dict:
    """פותח סבב חדר שהמקור שלו הוא דיסק במחשב בנייה.

    כל הבדיקות לפני כל כתיבה (עיקרון 5/7): המקור חייב להיות מחשב
    **בנייה** רשום שדיווח את הדיסק הזה ב-hello, בלי משימה פתוחה; היעדים
    הם בחירה מפורשת (‏`_validate_target_slots` — אין "כל הדיסקים"); ואם
    נמסר `target_drives` הוא חייב להיות בדיוק מספר הדיסקים שנבחרו — סבב
    יחיד, לא מצטבר.
    """
    mac = registry.normalize_mac(str(source.get("mac", "")))
    machine = registry.lookup(ctx.conn, mac) if mac else None
    if machine is None:
        raise ValueError("מחשב המקור אינו רשום")
    if machine["role"] != "build":
        raise ValueError("הפצה ישירה יוצאת ממחשב בנייה בלבד — לא ממכונה"
                         f" בתפקיד {machine['role']!r}")
    disk = source.get("disk")
    if not isinstance(disk, str) or not disk.strip():
        raise ValueError("צריך לבחור את דיסק המקור")
    disk = disk.strip()
    devs = _reported_devs(ctx.conn, mac)
    if disk not in devs:
        raise ValueError(
            f"מחשב הבנייה לא דיווח דיסק בשם {disk!r}"
            + (f" (דיווח: {', '.join(sorted(devs))})" if devs else " (לא דיווח דיסקים)"))
    selected = room._validate_target_slots(ctx.conn, target_slots)
    count = sum(len(item["ports"]) for item in selected)
    if target_drives is not None and int(target_drives) != count:
        raise ValueError(
            f"בהפצה ישירה היעד הוא בדיוק הדיסקים שנבחרו ({count}) — סבב יחיד")
    # ‏#1269: אותה בחירת דרייברים לכל דיסק כמו בסבב מהספרייה.
    chosen_drivers = room_drivers.validate(driver_models, selected,
                                           room.library_packages(ctx))
    if active_task(ctx.conn, mac) is not None:
        raise SessionError("כבר יש משימה פתוחה למחשב הבנייה הזה")
    if room.active_round(ctx.conn) is not None:
        raise SessionError("כבר יש סבב חדר פעיל")

    # ‏#1955: הזהות של הדיסק שנבחר, כמו בקליטה (#1901) — סדר `sda`/`sdb` מתחלף.
    from .capture import _reported_serial  # noqa: PLC0415 — capture→api→direct
    serial = _reported_serial(ctx.conn, mac, disk)
    image_id = new_live_id()
    roster = [item["mac"] for item in selected]
    # הגל תופס את חריץ השידור היחיד ומעיר **רק את הנבחרים** — ‏roster הוא
    # מה ש-`wake_class` (app.py) מעביר ל-WoL, ומה ש-`in_roster` מסנן ב-hello.
    wave_id = ctx.store.open(
        room.CLONERS_GROUP, image_id, prefix="ROOM",
        expected_clients=len(roster), opened_by=user, roster=roster,
    )
    task_id = "tsk_" + secrets.token_hex(2)
    round_id = "room_" + secrets.token_hex(4)
    now = now_iso()
    # המשימה והסבב הם כתיבה אחת: סבב בלי משימה הוא גל שאיש לא ישדר בו,
    # ומשימה בלי סבב היא מקור בלי גל. ‏`_write_lock, writing` כמו כל כותב
    # במסלול בקשה (#272/#313) — ‏store.open כבר סגר את הטרנזאקציה שלו.
    with _write_lock, writing(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO tasks (id, mac, type, disk, disk_serial, image_id, name, description,"
            " folder, created_by, created_at, updated_at, token, origin)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?)",
            (task_id, mac, DIRECT_SEND, disk, serial, image_id,
             f"{machine['suffix']}:{disk}", user, now, now, new_token(), origin),
        )
        ctx.conn.execute(
            "INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json,"
            " expand_partition, driver_models_json, state, wave_session_id, opened_by,"
            " created_at, source_kind, source_mac, source_disk, source_task_id)"
            " VALUES (?, ?, ?, ?, NULL, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
            (round_id, image_id, count,
             json.dumps(selected, separators=(",", ":")),
             json.dumps(chosen_drivers, ensure_ascii=False) if chosen_drivers else None,
             wave_id, user, now, BUILD_DISK, mac, disk, task_id),
        )
    journal(ctx.conn, "room_open_direct",
            f"{round_id} source={mac}:{disk} serial={serial or '-'} task={task_id} target={count}"
            + (f" drivers={len(chosen_drivers)}" if chosen_drivers else ""), user)
    return {"id": round_id, "wave_session_id": wave_id,
            "task_id": task_id, "image_id": image_id}


# --- המניפסט החי --------------------------------------------------------------


def validate_live_manifest(manifest: object) -> str | None:
    """מה שמקבל צריך כדי לכתוב ולאמת: מחיצות עם קובץ ו-sha256 לכל אחת."""
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        return "manifest schema must be 1"
    parts = manifest.get("partitions")
    if not isinstance(parts, list) or not parts:
        return "manifest has no partitions"
    streamed = 0
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("index"), int):
            return "a partition entry is malformed"
        name = part.get("file")
        if name is None:
            continue
        if not isinstance(name, str) or not name.strip() or name.strip() == "null":
            return f"partition {part['index']}: file name is malformed"
        if not isinstance(part.get("sha256"), str) or not SHA256.fullmatch(part["sha256"]):
            return f"partition {part['index']}: sha256 is missing or malformed"
        streamed += 1
    if streamed == 0:
        return "manifest has no streamed partition"
    total = manifest.get("total_compressed_bytes")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        return "total_compressed_bytes is missing or malformed"
    return None


def store_live_manifest(conn: sqlite3.Connection, task_row: dict,
                        manifest: dict) -> None:
    """שומר את המניפסט על הסבב הפעיל של המשימה ומעביר אותה ל-running.

    שתי הכתיבות הן טרנזאקציה אחת; סבב שאינו פעיל עוד זורק **בתוך**
    ‏`writing`, וה-rollback שלו הוא מה שמשחרר את הנעילה (#54).
    """
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE room_rounds SET live_manifest_json = ? WHERE source_task_id = ?"
            " AND state = 'active'",
            (json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
             task_row["id"]),
        )
        if cur.rowcount != 1:
            raise SessionError("הסבב של המשימה הזאת כבר אינו פעיל")
        conn.execute(
            "UPDATE tasks SET state = 'running', updated_at = ? WHERE id = ?"
            " AND state = 'pending'", (now_iso(), task_row["id"]),
        )
    streamed = sum(1 for p in manifest["partitions"] if p.get("file"))
    journal(conn, "direct_manifest",
            f'{task_row["id"]} {task_row["image_id"]} partitions={streamed}'
            f' bytes={manifest["total_compressed_bytes"]}')


def live_manifest(conn: sqlite3.Connection, image_id: str) -> dict | None:
    """המניפסט שמחשב הבנייה דיווח עבור מזהה חי — או `None` (טרם / לא כזה)."""
    if not is_live_id(image_id):
        return None
    row = conn.execute(
        "SELECT live_manifest_json FROM room_rounds WHERE image_id = ?"
        " ORDER BY created_at DESC LIMIT 1", (image_id,),
    ).fetchone()
    if row is None or not row["live_manifest_json"]:
        return None
    return json.loads(row["live_manifest_json"])


# --- מה שרואים משני הצדדים ------------------------------------------------------


def round_for_task(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM room_rounds WHERE source_task_id = ? ORDER BY created_at"
        " DESC LIMIT 1", (task_id,),
    ).fetchone()


#: ‏#1952: תקרה לרשימת הכתובות ב-`direct_finished` — גל אמיתי הוא עשרות.
FINISHED_MAX = 256


def clean_finished(body: dict) -> tuple[bool, list[str] | None]:
    """‏#1952: השדה `direct_finished` של ה-hello (ממשק 2) — (דווח?, הכתובות).

    מחשב הבנייה שולח אותו לפני כל מחיצה מהשנייה: מי קיבל את הקודמת עד
    סופה, מהלוג של ה-udp-sender **שלו** (לשרת אין את הלוג הזה במסלול
    הישיר). היעדר = לא דווח (המחיצה הראשונה, סוכן ישן). ‏`null`, או צורה
    פגומה — דווח ש"לא נבדק" (‏None): עיקרון 5, לא "אף אחד"."""
    if "direct_finished" not in body:
        return False, None
    raw = body["direct_finished"]
    if (not isinstance(raw, list) or len(raw) > FINISHED_MAX
            or not all(isinstance(item, str) for item in raw)):   # ip_address(7) = 0.0.0.7
        return True, None
    try:
        return True, [str(ipaddress.ip_address(item)) for item in raw]
    except (TypeError, ValueError):
        return True, None


def _lost_still_receiving(conn: sqlite3.Connection, store, session_id: str,
                          finished: list[str] | None) -> int:
    """‏#1952 — הכלל של #1950 (‏`SenderEngine._receivers`) במסלול הישיר: חבר
    `lost` שכתובתו קיבלה את המחיצה הקודמת עד סופה יתחבר גם לבאה, ובלי
    לספור אותו הוא "ממלא" את `--min-receivers` והשולח יוצא בלי החבר החי.
    חבר שנזרק — לא נספר (‏#1432). ‏`finished` None (לא נבדק) או כתובת לא
    ידועה — נספר: המחיר הוא המתנה של `--max-wait`, לא מגירה חיה ריקה."""
    still = [ip for ip in store.lost_ips(session_id)
             if finished is None or ip is None or ip in finished]
    if still:
        journal(conn, "send_counts_lost", f"{session_id} {' '.join(ip or '?' for ip in still)}")
    return len(still)


def task_block(conn: sqlite3.Connection, store, task: dict,
               multicast: dict | None, finished_reported: bool = False,
               finished: list[str] | None = None) -> dict:
    """השדה `task.direct` של ממשק 3: מצב הגל, כמה הצטרפו, ופרמטרי השידור.

    ‏`multicast` הוא `SenderEngine.multicast_params()` — אותם ערכים שהמנוע
    היה משדר בהם; ‏`min_receivers` נגזר כאן, מהגל, כמו ב-`_run` של המנוע.
    ‏`finished_reported`/`finished` — ‏`clean_finished` של ה-hello (‏#1952).
    """
    round_row = round_for_task(conn, task["id"])
    wave = None
    if round_row is not None and round_row["wave_session_id"]:
        wave = conn.execute(
            "SELECT id, state FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
    receivers = store.live_count(wave["id"]) if wave is not None else 0
    # ‏#1613: אפס חיים כי **מישהו סיים** (`done`/`partial` — מצבים שהסוכן
    # מדווח רק אחרי הזרם האחרון) הוא גל שנכתב וממתין לדופק שיסגור אותו,
    # לא "לא נשאר מי שיקבל". רק כשאף חבר לא קיבל את הזרם כולו — כישלון.
    if (wave is not None and wave["state"] == "running" and receivers == 0
            and conn.execute(
                "SELECT 1 FROM session_members WHERE session_id = ?"
                " AND state IN ('done', 'partial') LIMIT 1", (wave["id"],),
            ).fetchone() is None):
        reason = "ההפצה הישירה נכשלה: לא נשארו מחשבי יעד חיים לקבלת המחיצה הבאה"

        def fail_direct_round() -> None:
            room.fail_wave(conn, wave["id"], reason, commit=False)
            conn.execute(
                "UPDATE tasks SET state = 'failed', error = ?, updated_at = ?"
                " WHERE id = ? AND state IN ('pending', 'running')",
                (reason, now_iso(), task["id"]),
            )

        store.fail(wave["id"], reason, related=fail_direct_round)
        wave = conn.execute(
            "SELECT id, state FROM sessions WHERE id = ?", (wave["id"],)
        ).fetchone()
        journal(conn, "direct_failed", f'{task["id"]} {reason}')
    params = dict(multicast or default_multicast())
    still = (_lost_still_receiving(conn, store, wave["id"], finished)
             if finished_reported and wave is not None and wave["state"] == "running" else 0)
    params["min_receivers"] = max(1, receivers + still)
    # קצב אוטומטי: מה שנבחר לגל כשיצא, ולפני כן קצב הפתיחה — לא "בלי תקרה".
    params["max_bitrate"] = drive_rates.direct_bitrate(
        conn, wave["id"] if wave is not None else None, params.get("max_bitrate"))
    return {
        "session_id": wave["id"] if wave is not None else None,
        "session_state": wave["state"] if wave is not None else "closed",
        "receivers": receivers,
        "multicast": params,
    }


def is_direct_wave(conn: sqlite3.Connection, session_id: str) -> bool:
    """האם הגל הזה שייך לסבב שהמקור שלו הוא מחשב בנייה — ואז `SenderEngine`
    של השרת **אינו** מתחיל (‏app.py)."""
    return conn.execute(
        "SELECT 1 FROM room_rounds WHERE wave_session_id = ? AND source_kind = ?",
        (session_id, BUILD_DISK),
    ).fetchone() is not None


def source_ready(round_row: sqlite3.Row) -> bool:
    """האם מותר לגל לצאת: מספרייה — תמיד; מדיסק חי — רק אחרי המניפסט."""
    if _kind(round_row) != BUILD_DISK:
        return True
    return bool(round_row["live_manifest_json"])


def _kind(round_row: sqlite3.Row) -> str:
    try:
        return round_row["source_kind"] or LIBRARY
    except (IndexError, KeyError):
        return LIBRARY


def is_build_disk(round_row: sqlite3.Row) -> bool:
    return _kind(round_row) == BUILD_DISK


def source_label(conn: sqlite3.Connection, round_row: sqlite3.Row) -> str:
    """שם המקור כפי שהמפעיל מקליד אותו כדי לעצור: `<סיומת המכונה>:<דיסק>`."""
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?",
                       (round_row["source_mac"],)).fetchone()
    who = row["suffix"] if row else round_row["source_mac"]
    return f"{who}:{round_row['source_disk']}"


def source_view(conn: sqlite3.Connection, round_row: sqlite3.Row) -> dict:
    """השדה `round.source` של `GET /api/console/room`."""
    if not is_build_disk(round_row):
        return {"kind": LIBRARY}
    task = conn.execute("SELECT state FROM tasks WHERE id = ?",
                        (round_row["source_task_id"],)).fetchone()
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?",
                       (round_row["source_mac"],)).fetchone()
    return {
        "kind": BUILD_DISK,
        "mac": round_row["source_mac"],
        "disk": round_row["source_disk"],
        "name": row["suffix"] if row else None,
        "task_state": task["state"] if task else None,
        "manifest_ready": bool(round_row["live_manifest_json"]),
    }


def cancel_source_task(conn: sqlite3.Connection, round_row: sqlite3.Row,
                       user: str) -> None:
    """הסבב נעצר — המשימה של המקור נסגרת איתו, כדי שמחשב הבנייה יפסיק
    (הוא רואה ב-hello שהמשימה אינה שלו עוד, ואינו משדר)."""
    if not is_build_disk(round_row) or not round_row["source_task_id"]:
        return
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE tasks SET state = 'cancelled', updated_at = ? WHERE id = ?"
            " AND state IN ('pending', 'running')",
            (now_iso(), round_row["source_task_id"]),
        )
        cancelled = cur.rowcount == 1
    if cancelled:
        journal(conn, "direct_cancel", round_row["source_task_id"], user)


# --- ה-API של המקור ------------------------------------------------------------


def create_direct_router(ctx) -> APIRouter:
    """מה שמחשב הבנייה מדבר איתו כמקור. הרשאה: אסימון המשימה (‏#530)."""
    router = APIRouter(prefix="/api/v1/direct")

    def task_for(task_id: str, request: Request) -> dict:
        # אותן שלוש תשובות כמו `capture.task_for`: אין/נסגרה → 404, בלי
        # אסימון → 401, אסימון זר → 403. ומשימה שאינה `direct_send` היא
        # 404 כאן — נתיב הקליטה אינו מקבל מניפסט חי ולהפך.
        token = request.headers.get(TOKEN_HEADER, "")
        row = ctx.conn.execute(
            "SELECT id, state, type FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None or row["state"] not in OPEN_STATES or row["type"] != DIRECT_SEND:
            raise HTTPException(404, "no such open direct-send task")
        if not token:
            raise HTTPException(401, f"missing {TOKEN_HEADER}")
        claimed = claim_task(ctx.conn, task_id, token)
        if claimed is None:
            peer = request.client.host if request.client else "?"
            journal(ctx.conn, "task_token_refused", f"{task_id} from {peer}")
            raise HTTPException(403, "this is not your task")
        return claimed

    @router.put("/{task_id}/manifest")
    async def put_manifest(task_id: str, request: Request):
        row = task_for(task_id, request)
        # ‏#997: שומר שני לצד האסימון — כתובת המקור מול חכירת ה-MAC של
        # המשימה. לפני קריאת הגוף, כדי שזר עם אסימון לא יכשיל את המשימה
        # (`_fail`) במניפסט פגום. ייבוא מקומי: `api` מייבא את המודול הזה.
        from .api import identity_gate
        refused = identity_gate(ctx, row["mac"], request, "direct-manifest")
        if refused is not None:
            return refused
        raw = await request.body()
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            snippet = raw[:160].decode("utf-8", "replace")
            _fail(task_id, f"manifest is not valid JSON: {snippet}")
            raise HTTPException(400, "manifest is not valid JSON")
        problem = validate_live_manifest(manifest)
        if problem:
            _fail(task_id, problem)
            raise HTTPException(400, problem)
        # ‏#381 בנתיב הזה: מניפסט שנושא קשירה למכונה אחת (`machine_mac`)
        # אינו נשפך על מגירות של מכונות אחרות. מחשב בנייה אינו קושר —
        # אבל השער יושב במי שפותח שחזור, לא בהנחה על מי ישלח (עיקרון 5).
        round_row = round_for_task(ctx.conn, task_id)
        targets = ([item["mac"] for item in json.loads(round_row["target_slots_json"])]
                   if round_row is not None and round_row["target_slots_json"] else None)
        refusal = restore_refusal(manifest, targets)
        if refusal is not None:
            _fail(task_id, refusal)
            raise HTTPException(400, refusal)
        manifest["id"] = row["image_id"]
        manifest["name"] = row["name"]
        try:
            store_live_manifest(ctx.conn, row, manifest)
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True, "image_id": row["image_id"]}

    def _fail(task_id: str, message: str) -> None:
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE tasks SET state = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (message, now_iso(), task_id),
            )
        journal(ctx.conn, "direct_failed", f"{task_id} {message}")

    return router
