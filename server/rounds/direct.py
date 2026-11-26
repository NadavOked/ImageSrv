"""הפצה ישירה — מחשב הבנייה כמקור המולטיקאסט (#715, v1: #888).

"בלי לעבור בשרת" = האימג' **לא נכתב ולא נשמר** בשרת (הכרעת נדב 12/09).
השרת נשאר המתזמר היחיד: סבב חדר (`room.py`) עם `source_kind =
build_disk`, גל רגיל על `grp_CLONERS`, אותם דיווחי התקדמות, אותה
קונסולה. מה שמשתנה הוא **המקור**: במקום שולח של השרת על קובץ מהספרייה,
מחשב הבנייה מקבל משימת `direct_send` (ממשק 3) ומשדר ב-`chunkcast send` את
הדיסק שלו, קריאה בלבד. הבייטים זורמים מחשב-בנייה → מגירות נבחרות.

מה כן עובר בשרת: **המניפסט**. מחשב הבנייה קורא את הדיסק פעם אחת כמו
בקליטה (`capture_disk` בלי העלאה), שולח את המניפסט ל-`PUT
/api/v1/direct/<task>/manifest`, והמקבלים מושכים אותו כ-`GET
/api/v1/images/live_…/manifest` — אותו נתיב בדיוק כמו לאימג' מהספרייה,
ולכן `restore.sh` **אינו משתנה**. הגל אינו מתחיל לפני שהמניפסט הגיע:
מקבל שמתחיל בלי מניפסט אין לו מה לאמת מולו (עיקרון 5).

סבב **יחיד**: הסט הנבחר קבוע (`target_slots` חובה, `target_drives` =
סכומו), ובסיום הגל הסבב נסגר — גם אם מגירה נכשלה. גלי-החלפה מדיסק חי
נשארו הכרעה פתוחה (#715), והמלצת Astra — סבב יחיד — היא מה שנבנה.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
from typing import TypeGuard

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from server.brain import ceilings as brain_ceilings, ingest as brain_ingest   # #3669 (B6, B8), #3817 (F2, F3), בצל
from server.rounds import prompt_hold, room, room_drivers
from server.agent import registry
from server.core import finite_json
from server.disks import disk_rule
from server.db import _write_lock, journal, now_iso, SqlConnection, update_one, writing
from server.library.images import journal_ntfs_inconsistent, restore_refusal
from server.rounds.sessions import SessionError
from server.core.tasks import (ORIGIN_CONSOLE, OPEN_STATES, TOKEN_HEADER, active_task, claim_task,
                               new_token)

log = logging.getLogger("imagesrv.direct")

#: ערכי `room_rounds.source_kind`.
LIBRARY = "library"
BUILD_DISK = "build_disk"
#: סוג המשימה של המקור (`tasks.type`).
DIRECT_SEND = "direct_send"
#: מזהה אימג' **חי** — לא בספרייה, לא בתיקייה. הצורה שונה מ-`img_` בכוונה:
#: `valid_image_id` דוחה אותו, ולכן שום נתיב שכותב לספרייה לא יקבל אותו.
LIVE_ID = re.compile(r"live_[0-9a-f]{8}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def new_live_id() -> str:
    return "live_" + secrets.token_hex(4)


def is_live_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and LIVE_ID.fullmatch(value) is not None


# --- פתיחה --------------------------------------------------------------------


def _reported_devs(conn: SqlConnection, mac: str) -> set[str]:
    return {d.get("dev") for d in room._disks(conn, mac)
            if isinstance(d, dict) and isinstance(d.get("dev"), str)}


def target_floor(conn: SqlConnection, selected: list[dict] | None) -> dict:
    """#3942: המגירה **הקטנה ביותר** מבין אלה שנבחרו להפצה הישירה — השדות
    `target_bytes`/`target_name` של `task.direct` (ממשק 3).

    מחשב הבנייה מכווץ את הפריסה עד אליה (`shrinkplan.sh`), או מסרב לפני
    הקריאה הראשונה בשמה (`capture.sh`, השער החמישי). בלי זה הכיווץ כוון
    לסוף דיסק **המקור**, ומקור 500GB מול מגירת 256GB נכשל במשכפל אחרי
    עשר דקות קריאה (ברזל 20/11, `room_13e6b4ec`).

    `target_bytes: None` = **מגירה שנבחרה לא דיווחה גודל** (סוכן ישן, דיווח
    חסר) — לא "אין מגבלה" (עיקרון 5); `target_name` אומר איזו. אז הפריסה
    נבדקת מול דיסק המקור בלבד, וכל מגירה בודקת את עצמה (`disk_fits`).
    """
    smallest: tuple[int, str] | None = None
    for item in selected or []:
        row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (item["mac"],)).fetchone()
        who = room._room_label(row["suffix"]) if row else item["mac"]
        disks = [d for d in room._disks(conn, item["mac"]) if isinstance(d, dict)]
        for port in item["ports"]:
            name = f"דיסק {port} במחשב {who}"
            size = next((d.get("size_bytes") for d in disks if d.get("port") == port), None)
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                return {"target_bytes": None, "target_name": name}
            if smallest is None or size < smallest[0]:
                smallest = (size, name)
    if smallest is None:
        return {"target_bytes": None, "target_name": None}
    return {"target_bytes": smallest[0], "target_name": smallest[1]}


def _floor_note(floor: dict) -> str:
    """#3942: הרצפה בשורת היומן של הפתיחה — מספר, או "לא ידוע" ואיזו מגירה."""
    if floor["target_bytes"] is not None:
        return f"target_bytes={floor['target_bytes']}"
    return f"target_bytes=unknown ({floor['target_name'] or 'אין מגירה'})"


def open_direct_round(ctx, source: dict, target_slots, target_drives,
                      user: str, driver_models=None, origin: str = ORIGIN_CONSOLE) -> dict:
    """פותח סבב חדר שהמקור שלו הוא דיסק במחשב בנייה.

    כל הבדיקות לפני כל כתיבה (עיקרון 5/7): המקור חייב להיות מחשב
    **בנייה** רשום שדיווח את הדיסק הזה ב-hello, בלי משימה פתוחה; היעדים
    הם בחירה מפורשת (`_validate_target_slots` — אין "כל הדיסקים"); ואם
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
    # #2848 (נדב 11/11): צהוב — נכשל בשיכפול הקודם / כתיבה לא הושלמה — אינו מקור, בכל מסלול
    # (קונסולה, קיוסק מחשב הבנייה), באותו טקסט שהמסכים כותבים (`disk_rule`).
    why = disk_rule.source_refusal(ctx.conn, mac, disk)
    if why:
        raise ValueError(f"אי אפשר להעתיק מהדיסק {disk}: {why}")
    selected = room._validate_target_slots(ctx.conn, target_slots)
    count = sum(len(item["ports"]) for item in selected)
    if target_drives is not None and int(target_drives) != count:
        raise ValueError(
            f"בהפצה ישירה היעד הוא בדיוק הדיסקים שנבחרו ({count}) — סבב יחיד")
    # #1269: אותה בחירת דרייברים לכל דיסק כמו בסבב מהספרייה.
    chosen_drivers = room_drivers.validate(driver_models, selected,
                                           room.library_packages(ctx))
    if active_task(ctx.conn, mac) is not None:
        raise SessionError("כבר יש משימה פתוחה למחשב הבנייה הזה")
    if room.active_round(ctx.conn) is not None:
        raise SessionError("כבר יש סבב חדר פעיל")

    # #1955: הזהות של הדיסק שנבחר, כמו בקליטה (#1901) — סדר `sda`/`sdb` מתחלף.
    from server.library.capture import _reported_serial  # noqa: PLC0415 — capture→api→direct
    serial = _reported_serial(ctx.conn, mac, disk)
    image_id = new_live_id()
    roster = [item["mac"] for item in selected]
    # הגל תופס את חריץ השידור היחיד ומעיר **רק את הנבחרים** — roster הוא
    # מה ש-`wake_class` (app.py) מעביר ל-WoL, ומה ש-`in_roster` מסנן ב-hello.
    wave_id = ctx.store.open(
        room.CLONERS_GROUP, image_id, prefix="ROOM",
        expected_clients=len(roster), opened_by=user, roster=roster,
    )
    task_id = "tsk_" + secrets.token_hex(2)
    round_id = "room_" + secrets.token_hex(4)
    now = now_iso()
    # המשימה והסבב הם כתיבה אחת: סבב בלי משימה הוא גל שאיש לא ישדר בו,
    # ומשימה בלי סבב היא מקור בלי גל. `_write_lock, writing` כמו כל כותב
    # במסלול בקשה (#272/#313) — store.open כבר סגר את הטרנזאקציה שלו.
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
            + (f" drivers={len(chosen_drivers)}" if chosen_drivers else "")
            + f" {_floor_note(target_floor(ctx.conn, selected))}", user,   # #3942
            machine=mac, round_id=round_id)
    brain_ingest.silence_shadow(ctx.conn, round_id, task_id)   # #3669 (B8), בצל
    from server.agent import timing as agent_timing   # noqa: PLC0415
    from server.rounds import direct_settle, directchunk   # noqa: PLC0415 — both import this module
    brain_ceilings.relay_shadow(ctx.conn, round_id, task_id, directchunk.RELAY_TIMEOUT_S)   # #3817 (F3), בצל
    brain_ceilings.source_silence_shadow(ctx.conn, round_id, task_id, agent_timing.lost_seconds(ctx.conn),
                                         directchunk.SILENCE_GRACE_S, direct_settle.SOURCE_SILENT_S)   # #3687 (S32), בצל
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


def store_live_manifest(conn: SqlConnection, task_row: dict,
                        manifest: dict) -> None:
    """שומר את המניפסט על הסבב הפעיל של המשימה ומעביר אותה ל-running.

    שתי הכתיבות הן טרנזאקציה אחת; סבב שאינו פעיל עוד זורק **בתוך**
    `writing`, וה-rollback שלו הוא מה שמשחרר את הנעילה (#54).
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
            f' bytes={manifest["total_compressed_bytes"]}', machine=task_row.get("mac"))


def live_manifest(conn: SqlConnection, image_id: str) -> dict | None:
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


def round_for_task(conn: SqlConnection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM room_rounds WHERE source_task_id = ? ORDER BY created_at"
        " DESC LIMIT 1", (task_id,),
    ).fetchone()


def task_block(conn: SqlConnection, store, task: dict) -> dict:
    """השדה `task.direct` של ממשק 3: מצב הגל וכמה הצטרפו. פרמטרי השידור עצמם הם של מנוע החתיכות
    (`directchunk.DirectChunks.task_fields`: `engine`, `chunk`)."""
    round_row = round_for_task(conn, task["id"])
    wave = None
    if round_row is not None and round_row["wave_session_id"]:
        wave = conn.execute(
            "SELECT id, state FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
    receivers = store.live_count(wave["id"]) if wave is not None else 0
    # #1613: אפס חיים כי **מישהו סיים** (`done`/`partial` — מצבים שהסוכן
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

        # #3842: `fail` אינו קורא ל-`on_closed` — בלי זה שולח מנוע החתיכות של הגל נשאר חי עד "אף מחשב לא
        # התייצב" (180 ש'), וכל גל חדש נדחה. `on_closed` מוגבל למזהה הגל (#773), ועצירה כפולה בטוחה.
        if store.fail(wave["id"], reason, related=fail_direct_round):
            store.on_closed(wave["id"])
        wave = conn.execute(
            "SELECT id, state FROM sessions WHERE id = ?", (wave["id"],)
        ).fetchone()
        journal(conn, "direct_failed", f'{task["id"]} {reason}',
                machine=round_row["source_mac"], round_id=round_row["id"])
    # #1581: חברי הגל שעומדים עכשיו על שאלה למפעיל (`prompt_hold`, אותן תקרות). המקור אינו מתחיל את המחיצה
    # הראשונה כל עוד המספר אינו 0.
    held = (len(prompt_hold.waiting_in_wave(conn, wave["id"]))
            if wave is not None and wave["state"] == "running" else 0)
    # #3942: המגירה הקטנה שנבחרה, מהמלאי של הרגע -- הכיווץ של מחשב הבנייה מכוון אליה.
    slots = (json.loads(round_row["target_slots_json"])
             if round_row is not None and round_row["target_slots_json"] else None)
    return {
        "session_id": wave["id"] if wave is not None else None,
        "session_state": wave["state"] if wave is not None else "closed",
        "receivers": receivers,
        "operator_wait": held,
        **target_floor(conn, slots),
    }


def is_direct_wave(conn: SqlConnection, session_id: str) -> bool:
    """האם הגל הזה שייך לסבב שהמקור שלו הוא מחשב בנייה — ואז השרת מתזמר את
    הגל ומחשב הבנייה משדר (app.py)."""
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


def source_label(conn: SqlConnection, round_row: sqlite3.Row) -> str:
    """שם המקור כפי שהמפעיל מקליד אותו כדי לעצור: `<סיומת המכונה>:<דיסק>`."""
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?",
                       (round_row["source_mac"],)).fetchone()
    who = row["suffix"] if row else round_row["source_mac"]
    return f"{who}:{round_row['source_disk']}"


def source_view(conn: SqlConnection, round_row: sqlite3.Row) -> dict:
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


#: #2906 — הסיבה על שורת הסבב כשמחשב הבנייה שלח מניפסט חי שהשרת דחה.
MANIFEST_REJECTED = "מחשב הבנייה שלח מניפסט שנדחה"


def fail_round_of_task(conn: SqlConnection, store, task_id: str,
                       reason: str) -> bool:
    """#2906: משימת המקור נכשלה — הסבב שלה והגל שלו נכשלים באותה טרנזאקציה.

    בלי זה: `tasks=failed`, `room_rounds=active`, `sessions=open` לעד — `tick`
    ממתין למניפסט שלא יגיע (`source_ready`), החריץ תפוס והמשכפלים ממתינים.
    החברים שעוד לא סיימו מקבלים `failed` עם הסיבה (`SessionStore.fail`).
    גל שכבר אינו open/running (נכשל או נסגר) לא נוגעים בו כאן: ה-`tick`
    מטפל בו. מחזיר האם הקריאה הזו היא שהפילה אותו."""
    round_row = round_for_task(conn, task_id)
    if (round_row is None or round_row["state"] != "active"
            or not round_row["wave_session_id"]):
        return False
    wave_id = round_row["wave_session_id"]
    failed = store.fail(
        wave_id, reason,
        related=lambda: room.fail_wave(conn, wave_id, reason, commit=False))
    if failed:
        store.on_closed(wave_id)                # #3842: כמו ב-`task_block`
        journal(conn, "room_failed", f'{round_row["id"]} {wave_id} {reason}'
                f' written={round_row["written_drives"]}/{round_row["target_drives"]}',
                round_id=round_row["id"])
    return failed


def cancel_source_task(conn: SqlConnection, round_row: sqlite3.Row,
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
        journal(conn, "direct_cancel", round_row["source_task_id"], user,
                machine=round_row["source_mac"], round_id=round_row["id"])


# --- ה-API של המקור ------------------------------------------------------------


def create_direct_router(ctx) -> APIRouter:
    """מה שמחשב הבנייה מדבר איתו כמקור. הרשאה: אסימון המשימה (#530)."""
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
        # #2750: כמו #2748 — רק קריאת הגוף על הלולאה; כל עבודת ה-sqlite (לפניה
        # ואחריה) בתהליכון מהמאגר.
        row, refused = await run_in_threadpool(_manifest_gate, task_id, request)
        if refused is not None:
            return refused
        raw = await request.body()
        return await run_in_threadpool(_put_manifest_body, task_id, row, raw)

    def _manifest_gate(task_id: str, request: Request):
        row = task_for(task_id, request)
        # #997: שומר שני לצד האסימון — כתובת המקור מול חכירת ה-MAC של
        # המשימה. לפני קריאת הגוף, כדי שזר עם אסימון לא יכשיל את המשימה
        # (`_fail`) במניפסט פגום. ייבוא מקומי: `api` מייבא את המודול הזה.
        from server.agent.api import identity_gate
        return row, identity_gate(ctx, row["mac"], request, "direct-manifest")

    def _put_manifest_body(task_id: str, row, raw: bytes):
        try:
            manifest = finite_json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):   # #2447: deep JSON is 400, not 500
            snippet = raw[:160].decode("utf-8", "replace")
            _fail(task_id, f"manifest is not valid JSON: {snippet}", row["mac"])
            raise HTTPException(400, "manifest is not valid JSON")
        problem = validate_live_manifest(manifest)
        if problem:
            _fail(task_id, problem, row["mac"])
            raise HTTPException(400, problem)
        if not isinstance(manifest, dict):     # `validate_live_manifest` already refused anything else, by name
            raise HTTPException(400, "manifest schema must be 1")
        # #381 בנתיב הזה: מניפסט שנושא קשירה למכונה אחת (`machine_mac`)
        # אינו נשפך על מגירות של מכונות אחרות. מחשב בנייה אינו קושר —
        # אבל השער יושב במי שפותח שחזור, לא בהנחה על מי ישלח (עיקרון 5).
        round_row = round_for_task(ctx.conn, task_id)
        targets = ([item["mac"] for item in json.loads(round_row["target_slots_json"])]
                   if round_row is not None and round_row["target_slots_json"] else None)
        refusal = restore_refusal(manifest, targets)
        if refusal is not None:
            _fail(task_id, refusal, row["mac"])
            raise HTTPException(400, refusal)
        # #3161: מקור שהעלה אינדקסים של חתיכות לפני המניפסט -- כולם תואמים לו, או שהוא מסורב.
        live = getattr(ctx, "direct_chunk", None)
        if live is not None:
            from server.rounds import directchunk   # noqa: PLC0415 — directchunk imports this module
            unsealed = directchunk.seal(live.store, row["image_id"], manifest)
            if unsealed is not None:
                _fail(task_id, unsealed, row["mac"])
                raise HTTPException(400, unsealed)
        manifest["id"] = row["image_id"]
        manifest["name"] = row["name"]
        try:
            store_live_manifest(ctx.conn, row, manifest)
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        brain_ingest.compression_shadow(ctx.conn, task_id, manifest, "direct")   # #3669 (B6), בצל
        journal_ntfs_inconsistent(ctx.conn, manifest, f'{task_id} {row["image_id"]}', machine=row["mac"])
        return {"ok": True, "image_id": row["image_id"]}

    def _fail(task_id: str, message: str, mac: str) -> None:
        # #2916 §2: רק משימה פתוחה נכשלת — ביטול (או סגירת הסבב) שהקדים אותנו נשאר.
        with _write_lock, writing(ctx.conn):
            failed = update_one(
                ctx.conn,
                "UPDATE tasks SET state = 'failed', error = ?, updated_at = ? WHERE id = ?"
                " AND state IN ('pending', 'running')",
                (message, now_iso(), task_id),
            )
        if failed:
            journal(ctx.conn, "direct_failed", f"{task_id} {message}", machine=mac)
            # #2906: המשימה כבר נכשלה — והסבב והגל שלה לא נכשלים איתה בלי זה.
            # release/v0.92.0 (#2916 §2): only when this call failed it -- a cancel that came first stays.
            fail_round_of_task(ctx.conn, ctx.store, task_id, f"{MANIFEST_REJECTED}: {message}")

    # #3161: האינדקס, הראש, רשימת החתיכות של מעבר השלמה ותוצאת המעבר -- אותו אסימון ואותו שער.
    from server.rounds import directchunk   # noqa: PLC0415 — directchunk imports this module
    directchunk.add_routes(router, ctx, task_for, _fail)
    return router
