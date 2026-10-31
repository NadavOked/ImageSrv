"""קליטת אימג' — זרימה 13.1, מהקונסולה אל מחשב הבנייה ובחזרה.

הקונסולה יוצרת משימה למכונת בנייה מסוימת. המכונה מקבלת אותה בתשובת
ה-hello (שדה `task` בסעיף 3), קוראת את הדיסק, ומעלה את הקבצים לכאן.

למה כך ולא מסך על מחשב הבנייה: שמות האימג'ים בעברית, וקונסולת לינוקס
לא מרנדרת RTL. ההקלדה נשארת בדפדפן, שם היא עובדת.

האימות בקבלה הוא העיקר: אימג' נכנס לספרייה רק אחרי שכל קובץ נבדק מול
ה-sha256 שבמניפסט. אימג' פגום שמתגלה מול כיתה הוא מה שאסור.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from server.console import auth
from server.agent import registry
from server.storage import disk_reserve, storage_history, storage_locations
from server.learn import server_io
from server.core.atomicfile import fsync_dir, fsync_file, write_atomic
from server.agent.api import ServerContext, identity_gate
from server.library.images import validate_display_name
from server.db import _write_lock, journal, now_iso, update_one, writing
from server.library import capture_detail
from server.learn import learn_eta
from server.system import priority
from server.agent import readbackhash
from server.core.progress_view import capture_progress
from server.library.images import (ImageLibrary, MACHINE_MAC, carries_a_stream_file, has_disk_guid,
                                   has_partition_geometry, image_os, inside, required_bytes,
                                   valid_image_id)
# ⚠️ **לא `from . import tasks`.** יש כאן endpoint בשם `tasks`
# (שורה 280), והוא דורס את המודול — ‏`tasks.new_token` הופך
# ל-`AttributeError` על אובייקט פונקציה. ייבוא שמות מפורש.
from server.core.tasks import (OPEN_STATES, TOKEN_HEADER, active_task, claim_task, new_token,
                               request_origin, staging_dir)


def _peer(request: Request) -> str:
    """מי פנה — ליומן בלבד, ולעולם לא כשער.

    ‏#95 כבר קבע ש-IP אינו זהות: הוא מתעדכן בקצב DHCP, והעמוד נפתח
    גם מחוץ למכונה. כאן הוא הראיה שתאפשר לאתר מי ניסה.
    """
    return request.client.host if request.client else "?"

log = logging.getLogger("imagesrv.capture")

SAFE_FILE = re.compile(r"^p\d+\.[a-z]+\.pcl\.zst$")
CHUNK = 1024 * 1024

#: ‏#1470: ה-sha256 שהשרת חישב **בזמן הקבלה** של כל קובץ מחיצה, ליד הקובץ
#: באזור הביניים (`p3.windows.pcl.zst.upload-sha256`). הדיסק הוא מקור האמת
#: (עיקרון 3), ולכן הוא שורד אתחול כמו הקבצים עצמם. אינו תואם `SAFE_FILE`,
#: ונמחק לפני שהתיקייה נכנסת לספרייה.
UPLOAD_SHA = ".upload-sha256"

#: כל כמה שניות האימות ברקע כותב את ההתקדמות שלו (`tasks.verify_bytes`).
VERIFY_PROGRESS_EVERY_S = 1.0


def staged_file(folder: Path, name: str) -> Path:
    """‏`folder/name` — רק כש-`name` הוא שם קובץ מחיצה ונוחת ישירות בתוך `folder`.

    השם מגיע ממכונה ברשת הלימודית (נתיב ה-URL של ההעלאה, או שדה `file`
    במניפסט שהיא שלחה). ‏`SAFE_FILE` הוא הרשימה הלבנה ונבדק אצל הקורא עם
    הודעה בשמה; כאן הוא נבדק שוב, ואחריו הנתיב שנפתר (‏realpath) חייב להיות
    ילד ישיר של התיקייה — הצורה ש-CodeQL מזהה כמנקה (py/path-injection).
    שם שאינו עובר — ‏ValueError, לא "תיקון" (עיקרון 5)."""
    if not isinstance(name, str) or not SAFE_FILE.fullmatch(name):
        raise ValueError(f"unexpected partition file name: {name!r}")
    base = os.path.realpath(folder)
    full = os.path.realpath(os.path.join(base, name))
    if not full.startswith(base + os.sep) or os.path.dirname(full) != base:
        raise ValueError(f"partition file {name!r} falls outside its staging folder")
    return Path(full)


def _write_and_hash(handle, digest, data: bytes) -> None:
    """‏#1488: אצווה אחת של ההעלאה — רצה בתהליכון (`asyncio.to_thread`)."""
    digest.update(data)
    handle.write(data)


def _flush_to_disk(handle) -> None:
    """הקובץ שהתקבל — מהחוצץ של פייתון ומהמטמון של הקרנל, לדיסק. כשל זורק."""
    handle.flush()
    os.fsync(handle.fileno())


class _DiskFull(Exception):
    """אזל המקום באזור הביניים תוך כדי זרם. הסיבה נוקבת בשם הקובץ."""


class _StagingGone(Exception):
    """‏#1470+#532: תיקיית הביניים נמחקה מתחת לזרם (ביטול, בלינוקס) — לרשומת
    ה-sha256 אין לאן להיכתב. מצב המשימה מכריע: מבוטלת = 409, פתוחה = כשל בשם."""


def _free_bytes(folder: Path) -> int:
    return shutil.disk_usage(folder).free

#: ממי מותר לקלוט — **רשימת היתר מפורשת**, ולא "כל מי שאינו build" (#381).
#:
#: ‏`classroom` נוסף כאן כדי שמחשב כיתה יקלוט את הדיסק של עצמו (מסלול 8
#: ב-#380, הצד הכותב של #69). ‏`cloner` נשאר **בחוץ**: למכונת שיכפול אין
#: מערכת מקומית משלה, ומשימת קליטה עליה היא בקשה לקרוא דיסק ריק (#17).
#:
#: רשימת היתר ולא רשימת מניעה, מאותו טעם שבו `public-manifest.list` הוא
#: רשימת היתר (#369): תפקיד רביעי שייוולד מחר — צופה, מחשב ניהול — היה
#: מקבל ביום היוולדו את הזכות לקלוט, בשקט.
CAPTURE_ROLES = ("build", "classroom")


def _bind_machine(conn, manifest: dict, mac: str) -> None:
    """קושר את האימג' ל-MAC שנקלט ממנו, או משחרר אותו — ‏#381.

    **הקשירה נכתבת כאן ולא מגיעה מהסוכן.** ‏`machine_mac` שהגיע במניפסט
    שהמכונה העלתה נמחק בכל מקרה, בדיוק כמו `id` ו-`name`: השדה הזה הוא
    שער בטיחות, ומכונה ברשת הלימודית אינה מי שקובעת אותו.

    **הכיוון הבטוח הוא לקשור.** אימג' חופשי נולד רק מ**ראיה חיובית**
    שהמכונה היא מחשב בנייה — הרשומה קיימת והתפקיד `build`. מכונה שנמחקה
    מהמרשם בין יצירת המשימה לסיומה, או תפקיד שאיננו מכירים, מסתיימים
    באימג' קשור: "לא ידענו" אינו "מותר לכולם" (עיקרון 5).
    """
    machine = registry.lookup(conn, mac)
    if machine is not None and machine["role"] == "build":
        manifest.pop(MACHINE_MAC, None)
        return
    manifest[MACHINE_MAC] = mac


def _fail(conn, library_root: Path, task_id: str, message: str) -> None:
    """המשימה נכשלת בשם, ואזור העבודה שלה נמחק — כמו בביטול ובאימות שנכשל.

    ‏#1915: עד כאן האזור נשאר עד הסחיפה הבאה (‏`work_areas`, עד 24 שעות),
    וכשהסיבה הייתה דיסק מלא (507) — השרת שמר בדיוק את מה שמילא אותו.
    אזור של משימה במצב סופי הוא שטח של השרת, לא נתונים של אדם."""
    conn.execute(
        "UPDATE tasks SET state = 'failed', error = ?, updated_at = ? WHERE id = ?",
        (message, now_iso(), task_id),
    )
    conn.commit()
    shutil.rmtree(staging_dir(library_root, task_id), ignore_errors=True)
    journal(conn, "capture_failed", f"{task_id} {message}")


def create_agent_capture_router(ctx: ServerContext) -> APIRouter:
    """מה שמחשב הבנייה מדבר איתו. הרשאה: ה-MAC חייב להיות בעל המשימה."""
    router = APIRouter(prefix="/api/v1/capture")

    def task_for(task_id: str, request: Request):
        """המשימה, אם הפונה הוא בעליה.

        עד ‏#530 הפרמטר ``request`` התקבל כאן **ולא היה בשימוש**,
        בזמן שה-docstring של הראוטר הצהיר "ההרשאה: ה-MAC חייב להיות
        בעל המשימה". כל פונה שהכיר `task_id` — ‏65,536 ערכים
        אפשריים — העלה אימג' משלו, והשרת פרסם אותו.

        **שלוש שאלות שונות, שלוש תשובות:** משימה שאינה קיימת או
        שנסגרה → ‏404 · בקשה בלי אסימון → ‏401 · אסימון שאינו של
        המשימה הזו → ‏403. קיפולן לאחת היה מספר לפונה מה קיים.
        """
        token = request.headers.get(TOKEN_HEADER, "")
        row = ctx.conn.execute(
            "SELECT id, state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None or row["state"] not in OPEN_STATES:
            raise HTTPException(404, "no such open task")
        if not token:
            raise HTTPException(401, f"missing {TOKEN_HEADER}")
        claimed = claim_task(ctx.conn, task_id, token)
        if claimed is None:
            journal(ctx.conn, "task_token_refused", f"{task_id} from {_peer(request)}")
            raise HTTPException(403, "this is not your task")
        return claimed

    async def stream_to(request: Request, path: Path) -> str:
        digest = hashlib.sha256()
        # ‏#1737: הזרם הוא chunked — אורכו אינו ידוע מראש, ולכן הרזרבה למערכת
        # נבדקת **תוך כדי**, לא רק לפני הבייט הראשון.
        # השורש ולא תיקיית הביניים: ביטול מוחק את התיקייה מתחת לזרם (‏#532),
        # ומדידה עליה הייתה הופכת את ה-409 של הביטול ל"לא נמדד".
        guard = disk_reserve.StreamGuard(lambda: _free_bytes(ctx.library.root),
                                         f"הקליטה של {path.name}")
        # ‏#1470: רשומה ישנה של העלאה קודמת לאותו שם אינה ראיה על הבייטים האלה.
        record = upload_sha_path(path)
        record.unlink(missing_ok=True)
        try:
            with path.open("wb") as handle:
                # ‏#1488: הכתיבה והגיבוב בתהליכון, באצוות של CHUNK — לא על
                # הלולאה שמשותפת לסוכנים ולקונסולה. ה-await על כל אצווה הוא
                # הלחץ-החוזר: הזרם אינו נקרא הלאה לפני שהדיסק קיבל את הקודמת,
                # ולכן הזיכרון תחום לאצווה אחת (‏#15) כמו קודם.
                # ‏#1974: במאגר המונמך של הקליטה — לא במאגר של `to_thread`,
                # שהקונסולה חולקת.
                loop = asyncio.get_running_loop()
                batch = bytearray()
                async for chunk in request.stream():
                    batch += chunk
                    if len(batch) >= CHUNK:
                        await loop.run_in_executor(priority.CAPTURE_IO, _write_and_hash,
                                                   handle, digest, bytes(batch))
                        guard.wrote(len(batch))
                        batch.clear()
                if batch:
                    await loop.run_in_executor(priority.CAPTURE_IO, _write_and_hash,
                                               handle, digest, bytes(batch))
                # ‏#1951: הבייטים לדיסק **לפני** "התקבל" — fsync אחד לקובץ, לא לכל
                # אצווה. בלעדיו כיבוי קשיח אחרי capture_verifying מחק את סוף
                # המטמון, והאימות שהתחיל מחדש נכשל ב-sha256 (‏QA-G49, 22/10).
                # ‏#1974: גם הוא במאגר המונמך — כתיבת הקליטה לדיסק.
                await loop.run_in_executor(priority.CAPTURE_IO, _flush_to_disk, handle)
            # ‏#1470: מה שהתקבל נשמר ליד הקובץ — ‏finish_capture משווה אותו
            # למניפסט, כבדיקה נוספת על הקריאה החוזרת מהדיסק (עיקרון 6).
            try:
                record.write_text(digest.hexdigest(), encoding="ascii")
                fsync_file(record)
                fsync_dir(path.parent)   # ‏#1951: רשומות התיקייה של שני הקבצים
            except FileNotFoundError as exc:
                raise _StagingGone(f"תיקיית הביניים של {path.name} נמחקה בזמן ההעלאה") from exc
        except disk_reserve.ReserveReached as exc:
            path.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            raise _DiskFull(str(exc)) from exc
        except OSError as exc:
            # ‏#411: דיסק שהתמלא תוך כדי זרם הוא כשל גלוי (עיקרון 4), לא 500
            # באמצע עם קובץ חלקי שנשאר. שאר ה-OSError ממשיכים כשהיו.
            if exc.errno != errno.ENOSPC:
                raise
            path.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            raise _DiskFull(
                f"אזל המקום בשרת בזמן כתיבת {path.name} — הדיסק התמלא") from exc
        return digest.hexdigest()

    @router.post("/{task_id}/preflight")
    async def preflight(task_id: str, request: Request):
        row = task_for(task_id, request)
        if row["type"] != "capture":
            raise HTTPException(400, "בדיקת מקום מיועדת למשימת קליטה")
        refused = identity_gate(ctx, row["mac"], request, "capture-preflight")
        if refused is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "הגוף אינו JSON תקין")
        # ‏#1884: מכאן ההעלאה — צילום הדיסק של הספרייה (קצב הכתיבה ולחץ ה-I/O של הקליטה).
        server_io.begin(ctx.conn, "capture", task_id, ctx.library.root)
        parts = body.get("partitions") if isinstance(body, dict) else None
        if not isinstance(parts, list) or not parts or any(
            not isinstance(p, dict) or type(p.get("index")) is not int
            or p["index"] <= 0 for p in parts
        ) or len({p["index"] for p in parts}) != len(parts):
            raise HTTPException(400, "נדרשת רשימת מחיצות עם מספרים ייחודיים")
        missing = [str(p["index"]) for p in parts
                   if type(p.get("used_bytes")) is not int or p["used_bytes"] < 0]
        used = None if missing else sum(p["used_bytes"] for p in parts)
        # Measure the actual upload destination, not a cached health sample or
        # the source disk. Capture staging and final images both live here.
        free = None
        unchecked = []
        if missing:
            unchecked.append("נפח תפוס במחיצות " + ", ".join(missing))
        try:
            free = _free_bytes(ctx.library.root)
            if type(free) is not int or free < 0:
                free = None
        except OSError:
            pass
        if free is None:
            unchecked.append("מקום פנוי באחסון השרת (loc_local)")
        if unchecked:
            status, reason = "unchecked", "לא נבדק: " + " · ".join(unchecked) + " — הקליטה נעצרה לפני הכיווץ"
        else:
            # ‏#1737: הרזרבה למערכת (`disk_reserve`) אינה מקום לקליטה — גם לא באזהרה.
            keep = disk_reserve.reserve()
            numbers = f"תפוס {used / 1e9:.1f} GB · פנוי בשרת {free / 1e9:.1f} GB"
            if keep:
                numbers += f" · נשמרים למערכת {disk_reserve.gb(keep)}"
            if used + keep > free:
                status, reason = "refused", "אין מקום בשרת לקליטה: " + numbers
            elif used * 2 + keep > free:
                status, reason = "warning", "אזהרת מקום: " + numbers + " — יחס הדחיסה אינו ידוע; ניתן להמשיך"
            else:
                status, reason = "ok", numbers
        if status in ("refused", "unchecked", "warning"):
            # #54: every write inside the lock + writing(), never a bare commit.
            if status == "warning":
                sql, params = ("UPDATE tasks SET error = ? WHERE id = ? AND state IN ('pending', 'running')",
                               (reason, task_id))
            else:
                sql, params = ("UPDATE tasks SET state = 'failed', error = ?, updated_at = ?"
                               " WHERE id = ? AND state IN ('pending', 'running')",
                               (reason, now_iso(), task_id))
            with _write_lock, writing(ctx.conn):
                changed = ctx.conn.execute(sql, params).rowcount == 1
            if not changed:
                raise HTTPException(409, "המשימה כבר אינה פתוחה")
            journal(ctx.conn, "capture_space_warning" if status == "warning" else "capture_failed",
                    f"{task_id} {reason}")
        return {"status": status, "reason": reason, "used_bytes": used,
                "free_bytes": free, "location_id": storage_locations.LOCAL_ID}

    @router.put("/{task_id}/files/{filename}")
    async def upload_partition(task_id: str, filename: str, request: Request):
        row = task_for(task_id, request)
        refused = identity_gate(ctx, row["mac"], request, "capture-files")
        if refused is not None:
            return refused
        # רשימה לבנה על שם הקובץ: הוא מגיע ממכונה ברשת הלימודית.
        if not SAFE_FILE.fullmatch(filename):
            raise HTTPException(400, "unexpected partition file name")
        folder = staging_dir(ctx.library.root, task_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = staged_file(folder, filename)
        # ‏#411: הזמנת מקום לפני הבייט הראשון. ההעלאה מזרימה ב-chunked (‏`curl
        # -T` על fifo, כדי לא להיחנק ב-OOM — ‏agent/lib/capture/capture.sh), ולכן לרוב
        # אין Content-Length; כשיש, מקום פנוי שאינו מכיל אותו נדחה כאן ומספרי,
        # כמו הסירוב על "דיסק קטן מדי". בלי גודל מוצהר די ב-chunk אחד של מקום
        # כדי להתחיל — ‏ENOSPC תוך כדי זרם נתפס ב-`stream_to` ככשל גלוי.
        declared = request.headers.get("content-length")
        needed = int(declared) if declared and declared.isdigit() else CHUNK
        free = _free_bytes(folder)
        # ‏#1737: גם בלי אורך מוצהר — הזרם לא מתחיל כשהמקום כבר ברזרבה.
        reason = disk_reserve.refusal(free, needed, f"אחסון הקליטה ({filename})")
        if reason is not None:
            _fail(ctx.conn, ctx.library.root, task_id, reason)
            raise HTTPException(507, reason)
        gone = None
        try:
            got = await stream_to(request, path)
        except _DiskFull as exc:
            _fail(ctx.conn, ctx.library.root, task_id, str(exc))
            raise HTTPException(507, str(exc))
        except _StagingGone as exc:
            gone = str(exc)   # ‏#1470+#532: מצב המשימה למטה מכריע — ביטול = 409
        # ‏#532: תביעת מצב ב-`WHERE`. ‏`task_for` בדק **לפני** ההזרמה,
        # וההזרמה של מחיצה נמשכת דקות — מנהל שביטל בינתיים קיבל
        # `ok: true`, והשורה הזו החזירה את המשימה ל-`running`.
        # ובלינוקס גם תיקיית ה-staging כבר הוסרה, וה-handle הפתוח
        # ממשיך לכתוב אל inode שנמחק.
        if not update_one(
            ctx.conn,
            "UPDATE tasks SET state = 'running', updated_at = ?"
            " WHERE id = ? AND state IN ('pending', 'running')",
            (now_iso(), task_id),
        ):
            path.unlink(missing_ok=True)
            upload_sha_path(path).unlink(missing_ok=True)
            raise HTTPException(409, "the task is no longer open")
        ctx.conn.commit()
        if gone is not None:
            # המשימה פתוחה ובכל זאת התיקייה איננה: אין ראיה לבייטים — כשל בשם, לא "ok".
            _fail(ctx.conn, ctx.library.root, task_id, gone)
            raise HTTPException(500, gone)
        # ‏#1535: ‏task_id ושם הקובץ באים מה-URL — שבירת שורה אינה מגיעה ליומן,
        # גם אם בדיקה למעלה תשתנה (לא נשען על SAFE_FILE ועל ה-DB לבדם).
        log.info("capture %s: received %s", task_id.replace("\r", " ").replace("\n", " "),
                 filename.replace("\r", " ").replace("\n", " "))
        return {"ok": True, "sha256": got}

    # ‏#1470 (הכרעת נדב 16/10): ‏202 — המניפסט התקבל והבדיקות הזולות עברו,
    # ומחשב הבנייה משתחרר. הקריאה החוזרת של כל קובץ מהדיסק (עיקרון 6) רצה
    # ברקע, בתהליכון ולא על לולאת האירועים (#1461), והאימג' נכנס לספרייה
    # רק כשהיא עוברת.
    @router.put("/{task_id}/manifest", status_code=202)
    async def finish_capture(task_id: str, request: Request):
        row = task_for(task_id, request)
        refused = identity_gate(ctx, row["mac"], request, "capture-manifest")
        if refused is not None:
            return refused
        # המזהה נוצר כאן בשרת, ולכן הבדיקה הזאת אינה אמורה להיכשל לעולם —
        # וזו הסיבה שהיא כתובה: המזהה הופך לשם תיקייה, והכלל הזה נאכף
        # בשני המסלולים שמכניסים אימג' לספרייה, לא רק בזה שקלט מבחוץ
        # (‏#110). אם הוא נכשל, השורה בבסיס הנתונים אינה מה שחשבנו.
        if not valid_image_id(row["image_id"]):
            _fail(ctx.conn, ctx.library.root, task_id, f'malformed image id on task: {row["image_id"]!r}')
            raise HTTPException(500, "task carries a malformed image id")
        folder = staging_dir(ctx.library.root, task_id)
        server_io.end(ctx.conn, "capture", task_id, ctx.library.root)   # ‏#1884: סוף ההעלאה
        raw = await request.body()
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # קטע מהגוף נכנס ליומן: "לא JSON" בלי ראיה השאיר אותנו
            # מגששים מול סוכן שאי אפשר להיכנס אליו (מעבדה, #12).
            snippet = raw[:160].decode("utf-8", "replace")
            _fail(ctx.conn, ctx.library.root, task_id, f"manifest is not valid JSON: {snippet}")
            raise HTTPException(400, "manifest is not valid JSON")

        # ‏#1470: כאן רק מה שזול — המבנה, שכל קובץ הגיע, ושה-sha256 שחושב
        # בזמן הקבלה תואם את המניפסט. הקריאה החוזרת מהדיסק — ברקע, למטה.
        problem = manifest_problem(manifest, folder) or upload_sha_problem(manifest, folder)
        if problem:
            _fail(ctx.conn, ctx.library.root, task_id, problem)
            raise HTTPException(400, problem)

        # שמות התצוגה נקבעו בקונסולה ולא במחשב הבנייה.
        manifest["id"] = row["image_id"]
        manifest["name"] = row["name"]
        manifest["description"] = row["description"]
        manifest["folder"] = row["folder"]
        manifest["created"] = now_iso()
        manifest["created_by"] = row["created_by"]
        _bind_machine(ctx.conn, manifest, row["mac"])
        # ‏#82: הדרישה נגזרת מהפריסה, ולא מגודל דיסק המקור שהסוכן שלח.
        # הנרמול כאן ולא רק בקריאה כדי שהערך שמונח בספרייה יהיה הנכון —
        # הדיסק הוא מקור האמת, וסוכן ישן שממשיך לשלוח את גודל המקור
        # אינו מכניס ערך שגוי לתיקייה. ‏_validate כבר ווידא שיש מה לגזור.
        manifest["min_target_bytes"] = required_bytes(manifest)
        # ‏#1908: אטומי ועמיד — מניפסט חתוך הוא אימג' שבור (עיקרון 3).
        write_atomic(folder / "manifest.json",
                     json.dumps(manifest, ensure_ascii=False, indent=2))

        target = inside(ctx.library.root / row["image_id"], ctx.library.root)
        if target is None:
            _fail(ctx.conn, ctx.library.root, task_id, "image target falls outside the library root")
            raise HTTPException(500, "image target falls outside the library root")
        if target.exists():
            _fail(ctx.conn, ctx.library.root, task_id, "an image with this id already exists")
            raise HTTPException(409, "image already exists")

        # ‏#1370: המכנה של פס ההתקדמות. בזמן הקליטה הגודל הדחוס לא ידוע
        # ו-bytes_total נשאר 0 (#435); כאן המניפסט אומר כמה עבר. ערך שאינו
        # מספר שלם אי-שלילי אינו מנוחש — bytes_total נשאר כמו שהיה.
        total = manifest.get("total_compressed_bytes")
        known = type(total) is int and 0 <= total < 2 ** 63
        verify_total = sum(staged_file(folder, name).stat().st_size
                           for name, _ in stream_files(manifest))
        now = now_iso()
        # ‏#1470: ‏`verifying` אינו מצב פתוח — ‏hello כבר לא מוסר את המשימה,
        # והדיווח הסופי של הסוכן מקבל `not_open` כמו אחרי `done`. השלב
        # `verify` נכתב כאן בשרת: הדיווח של הסוכן עליו הגיע (אם בכלל) לפני.
        with _write_lock, writing(ctx.conn):
            opened = update_one(
                ctx.conn,
                "UPDATE tasks SET state = 'verifying', updated_at = ?,"
                " bytes_total = COALESCE(?, bytes_total),"
                " stage_started_at = CASE WHEN stage IS 'verify' THEN stage_started_at ELSE ? END,"
                " stage = 'verify', verify_bytes = 0, verify_total = ?"
                " WHERE id = ? AND state IN ('pending', 'running')",
                (now, total if known else None, now, verify_total, task_id),
            )
        if not opened:
            raise HTTPException(409, "the task is no longer open")
        journal(ctx.conn, "capture_verifying",
                f'{task_id} {row["image_id"]} "{row["name"]}" {verify_total} bytes')
        start_verification(ctx.conn, ctx.library.root, task_id)
        return {"ok": True, "image_id": row["image_id"], "state": "verifying"}

    return router


def validate_manifest_files(manifest: object, folder: Path) -> str | None:
    """המניפסט וקבצי המחיצות שבתיקייה, לפני כניסה לספרייה — או הסיבה.

    בשימוש הקליטה מהסוכן וגם שמירת גרסה מ-VM (#1334): שני המסלולים
    מכניסים אימג' לספרייה, ושער אחד מכריע לשניהם — כל sha256 נקרא שוב
    מהדיסק (עיקרון 6). ‏#1470: הקליטה מהסוכן מריצה את שני החצאים בנפרד —
    ‏`manifest_problem` בתוך ה-PUT, ‏`reread_problem` ברקע."""
    return manifest_problem(manifest, folder) or reread_problem(manifest, folder)


def stream_files(manifest: dict) -> list[tuple[str, object]]:
    """‏(שם קובץ, sha256 מהמניפסט) לכל מחיצה שנושאת קובץ זרם. אחרי
    ‏`manifest_problem` בלבד — הוא מוודא שהמבנה תקין ושהשמות בטוחים."""
    return [(part["file"], part.get("sha256"))
            for part in manifest["partitions"] if carries_a_stream_file(part)]


def upload_sha_path(path: Path) -> Path:
    return path.with_name(path.name + UPLOAD_SHA)


def upload_sha_problem(manifest: dict, folder: Path) -> str | None:
    """‏#1470: ה-sha256 שהשרת חישב בזמן הקבלה, מול המניפסט — או הסיבה.

    בדיקה **נוספת** ולא תחליף לקריאה החוזרת (עיקרון 6 נשאר): היא תופסת כל
    שגיאת העברה מיד, בתוך ה-PUT, אבל לא כתיבה פגומה לדיסק השרת. רשומה
    חסרה היא "לא הצלחנו לבדוק" — כישלון, לא ויתור (עיקרון 5)."""
    for name, expected in stream_files(manifest):
        try:
            got = upload_sha_path(staged_file(folder, name)).read_text(encoding="ascii").strip()
        except OSError:
            return (f"the sha256 computed while receiving {name} was not recorded"
                    " -- the upload cannot be checked")
        if got != expected:
            return f"sha256 mismatch on {name} (the bytes the server received)"
    return None


def reread_problem(manifest: dict, folder: Path, on_bytes=None) -> str | None:
    """כל קובץ זרם נקרא שוב מהדיסק ומגובב מול המניפסט — עיקרון 6."""
    for name, expected in stream_files(manifest):
        if _sha256(staged_file(folder, name), on_bytes) != expected:
            return f"sha256 mismatch on {name}"
    return None


def manifest_problem(manifest: object, folder: Path) -> str | None:
    """כל מה ש-`validate_manifest_files` בודק **חוץ מהגיבוב** — זול, ובלי
    לקרוא את קבצי המחיצות (‏#1470)."""
    problem = ImageLibrary._validate(manifest, require_library_fields=False)
    if problem:
        return problem
    parts = manifest.get("partitions")
    if not isinstance(parts, list) or not parts:
        return "manifest has no partitions"
    for part in parts:
        if not has_partition_geometry(part):
            index = part.get("index") if isinstance(part, dict) else None
            return (f"partition {index!r} has malformed geometry:"
                    " start_sector and size_bytes must both be positive")
    if manifest.get("family") not in (256, 500):
        return "family must be 256 or 500"
    # בלי דרישת גודל אין החלטה "האם האימג' נכנס לכונן", והאימג' היה
    # נכנס לספרייה כדי להידלג שם בשקט בכל סריקה. נתפס כאן, בשמו.
    if required_bytes(manifest) is None:
        return "cannot determine how much room the image needs"
    # אימג' Windows קושר את ה-BCD ל-GUID של הדיסק — בלעדיו כל מחשב
    # משוחזר עולה ל-winload.efi 0xc000000e (#26, #571). ‏disk_guid ריק
    # אינו "אין צורך ב-GUID" אלא "הקליטה לא הצליחה לקרוא אותו": הסוכן
    # גוזר אותו מ-`sgdisk -p`, וכשל קריאה יוצא מחרוזת ריקה. נתפס כאן,
    # בקליטה, ולא מול כיתה (עיקרון 6) — אימג' פגום אינו נכנס לספרייה,
    # במקום שהשחזור ידלג עליו בשקט ויגיע ל-done. לינוקס פטור: ‏GRUB
    # מאתר לפי UUID של מערכת הקבצים, ולכן הוא נקלט בלי disk_guid כרגיל.
    if image_os(manifest) == "windows" and not has_disk_guid(manifest):
        return ("a windows image needs its source disk_guid (the BCD binds"
                " to it) but the manifest has none -- capture did not read it")
    expandable = [p for p in parts if p.get("expandable")]
    if len(expandable) > 1:
        return "at most one expandable partition"
    if expandable:
        # אחרי המחיצה המורחבת מותרת כל מחיצה שאינה windows/linux (‏#58,
        # מרחיב את #46). הסוכן מעביר את כל הזנב הזה לסוף הכונן לפני
        # שנכתב בייט: ‏swap נבראת שם מחדש (אפיון סעיף 14) וכל השאר —
        # ‏recovery של Windows 11, למשל — נכתבת שם מקובץ הזרם שלה.
        # הכלל הישן ("רק swap") אמר שאף אימג' Windows לא יורחב לעולם.
        # מה שנשאר אסור הוא מחיצת מערכת אחרי המורחבת: אז המועמד אינו
        # האחרון על הדיסק, ולמתוח אותו היה דורס אותה.
        #
        # ההשוואה לפי start_sector ולא לפי סדר הרשימה — הרשימה בסדר
        # אינדקסים, ובאימג' ענן השורש הוא מחיצה 1 שרשומה ראשונה
        # ויושבת אחרונה. אותו כלל בדיוק שהסוכן מסמן לפיו.
        start = expandable[0].get("start_sector") or 0
        after = [p for p in parts if (p.get("start_sector") or 0) > start]
        if any(p.get("role") in ("windows", "linux") for p in after):
            return "no system partition may follow the expandable partition"
    for part in parts:
        # הפטור מקובץ נשען על `fs` ולא על `role` (#424): הסוכן מכריע
        # לפי `fs` בלבד, ורשומה עם `role: "swap"` ו-`fs: "ntfs"` עברה
        # כאן **בלי שום בדיקה** ואז קיבלה `mkswap` על מחיצת ווינדוס.
        if not carries_a_stream_file(part):
            if part.get("fs") == "swap":
                continue                  # swap: recorded, never uploaded
            return (f"partition {part.get('index')} ({part.get('fs')}) has no"
                    " file, and only a swap partition may have none")
        name = part["file"]
        if not isinstance(name, str) or not SAFE_FILE.fullmatch(name):
            return f"unexpected partition file name: {name}"
        path = staged_file(folder, name)
        if not path.is_file():
            return f"partition file was never uploaded: {name}"
        # ‏#1830: קובץ קיים אינו ראיה שהבייטים הגיעו. זרם של partclone (גם
        # של מערכת קבצים ריקה — כותרת ומפת סיביות) או של partclone.dd אינו
        # ריק לעולם, ו-sha256 של קובץ ריק "תואם" כשהסוכן גיבב את מה שלא נשלח.
        if path.stat().st_size == 0:
            return f"partition file is empty: {name} -- no bytes arrived"
    return None

def _sha256(path: Path, on_bytes=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            if on_bytes is not None:
                on_bytes(len(chunk))
    return digest.hexdigest()


# --- #1470: האימות ברקע ------------------------------------------------------
#
# הכרעת נדב 16/10: מחשב הבנייה משתחרר מיד אחרי ההעלאה, והקריאה החוזרת של
# כל קובץ מדיסק השרת רצה כאן, בתהליכון. האימג' נשאר באזור הביניים — שם
# שמתחיל בנקודה, שהספרייה מדלגת עליו (לא ברשימה, לא נמשך, לא משוחזר) —
# עד שכל sha256 נקרא שוב ותאם. כישלון = `failed` בשם, ביומן ובקונסולה.
# ‏#1461: עד כאן הגיבוב רץ בתוך `finish_capture` על לולאת האירועים, והשרת
# כולו — hello, דיווחים, קונסולה — לא ענה למשך כל האימות.

#: המשימות שהאימות שלהן רץ **בתהליך הזה**. מונע אימות כפול של אותה משימה.
_VERIFYING: dict[str, threading.Thread] = {}
_VERIFYING_LOCK = threading.Lock()


def start_verification(conn, library_root: Path, task_id: str) -> threading.Thread | None:
    """מפעיל את האימות ברקע — או ``None`` כשהוא כבר רץ למשימה הזו.

    ‏daemon: שרת שנעצר באמצע אינו ממתין לקריאה של 100GB. המשימה נשארת
    ‏`verifying`, ו-`reconcile_on_start` מריץ את האימות מחדש בעלייה הבאה."""
    with _VERIFYING_LOCK:
        live = _VERIFYING.get(task_id)
        if live is not None and live.is_alive():
            return None
        thread = threading.Thread(target=run_verification,
                                  args=(conn, Path(library_root), task_id),
                                  name=f"verify-{task_id}", daemon=True)
        _VERIFYING[task_id] = thread
        thread.start()
    return thread


def run_verification(conn, library_root: Path, task_id: str) -> str:
    """האימות עצמו, סינכרוני. מחזיר `done`, ‏`failed`, או `gone` (המשימה
    כבר אינה `verifying`). כל חריגה היא כישלון בשם — אימות שקרס לא עבר."""
    # ‏#1974: קריאת האימג' כולו וגיבובו — בעדיפות הרקע, כמו הכתיבה שלפניו.
    priority.lower_this_thread()
    try:
        return _verify(conn, library_root, task_id)
    except Exception as exc:  # noqa: BLE001 — עיקרון 5: "לא בדקנו" אינו "עבר"
        log.exception("capture %s: verification crashed", task_id)
        return _verify_failed(conn, library_root, task_id,
                              f"the verification on the server crashed: {exc!r}")
    finally:
        with _VERIFYING_LOCK:
            if _VERIFYING.get(task_id) is threading.current_thread():
                del _VERIFYING[task_id]


def _verify(conn, library_root: Path, task_id: str) -> str:
    row = conn.execute(
        "SELECT id, image_id, name, created_by FROM tasks"
        " WHERE id = ? AND state = 'verifying'", (task_id,)).fetchone()
    if row is None:
        return "gone"
    folder = staging_dir(library_root, task_id)
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _verify_failed(conn, library_root, task_id,
                              f"the staged manifest could not be read: {exc}")
    problem = manifest_problem(manifest, folder)
    if problem:
        return _verify_failed(conn, library_root, task_id, problem)
    files = stream_files(manifest)
    total = sum(staged_file(folder, name).stat().st_size for name, _ in files)
    # מאפס: אימות שרץ מחדש אחרי אתחול מתחיל מאפס, לא מהמונה של הקודם.
    _verify_progress(conn, task_id, 0, total)
    started = time.monotonic()
    seen = {"bytes": 0, "at": started}

    def tick(count: int) -> None:
        seen["bytes"] += count
        now = time.monotonic()
        if now - seen["at"] >= VERIFY_PROGRESS_EVERY_S:
            seen["at"] = now
            _verify_progress(conn, task_id, seen["bytes"], total)

    problem = reread_problem(manifest, folder, tick)
    if problem:
        return _verify_failed(
            conn, library_root, task_id,
            f"{problem} -- the file read back from the server disk differs from the"
            " manifest; the image did not enter the library")
    target = inside(library_root / row["image_id"], library_root)
    if target is None:
        return _verify_failed(conn, library_root, task_id,
                              "image target falls outside the library root")
    if target.exists():
        return _verify_failed(conn, library_root, task_id,
                              "an image with this id already exists")
    for name, _ in files:
        upload_sha_path(staged_file(folder, name)).unlink(missing_ok=True)
    # ‏#1813: הבייטים לדיסק לפני הפרסום — fsync אחד לכל קובץ (לא לכל chunk),
    # ואז התיקייה; רק אחריהם ה-rename לספרייה, ו-fsync לשורש שה-rename יחזיק.
    for name, _ in files:
        fsync_file(staged_file(folder, name))
    fsync_file(folder / "manifest.json")
    fsync_dir(folder)
    folder.rename(target)
    fsync_dir(library_root)
    # ‏rename ואז UPDATE, כמו קודם: נפילה ביניהם = אימג' מאומת בספרייה עם
    # משימה `verifying`, ו-`reconcile_on_start` סוגר אותה כ-`done` (#411).
    with _write_lock, writing(conn):
        closed = update_one(
            conn,
            "UPDATE tasks SET state = 'done', verify_bytes = ?, updated_at = ?"
            " WHERE id = ? AND state = 'verifying'",
            (total, now_iso(), task_id),
        )
    if not closed:
        log.warning("capture %s: verified and in the library, but the task was no"
                    " longer 'verifying'", task_id)
    seconds = time.monotonic() - started
    journal(conn, "capture_done",
            f'{row["image_id"]} "{row["name"]}" verified {total} bytes in {seconds:.1f}s',
            row["created_by"])
    readbackhash.kick()   # #1805: the read-back digest of the new image -- now, not in ten minutes
    return "done"


def _verify_progress(conn, task_id: str, done: int, total: int) -> None:
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE tasks SET verify_bytes = ?, verify_total = ?, updated_at = ?"
            " WHERE id = ? AND state = 'verifying'",
            (done, total, now_iso(), task_id))


def _verify_failed(conn, library_root: Path, task_id: str, reason: str) -> str:
    """‏`verifying` → ‏`failed` בשם, ואזור הביניים נמחק — האימג' נשאר בחוץ.
    רק אם המשימה עדיין `verifying`: אחרת מישהו אחר כבר הכריע, ולא נוגעים."""
    with _write_lock, writing(conn):
        changed = update_one(
            conn,
            "UPDATE tasks SET state = 'failed', error = ?, updated_at = ?"
            " WHERE id = ? AND state = 'verifying'",
            (reason, now_iso(), task_id),
        )
    if not changed:
        return "gone"
    shutil.rmtree(staging_dir(library_root, task_id), ignore_errors=True)
    journal(conn, "capture_failed", f"{task_id} {reason}")
    return "failed"


def _reported_serial(conn, mac: str, disk: str) -> str | None:
    """‏#1901: המספר הסידורי של `disk` כפי שה-hello האחרון של המכונה דיווח —
    מה שהמפעיל ראה כשבחר. ``None`` = לא ידוע (הדיסק לא דווח, או דווח בלי
    serial); הסוכן מכריע אז לפי מספר הדיסקים, ולא לפי השם לבדו."""
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    try:
        disks = json.loads(row["disks_json"]) if row is not None and row["disks_json"] else []
    except (TypeError, ValueError):
        return None
    for entry in disks if isinstance(disks, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("dev"), str) \
                and entry["dev"].removeprefix("/dev/") == disk:
            serial = entry.get("serial")
            return serial if isinstance(serial, str) and serial else None
    return None


def order_capture(ctx: ServerContext, body: dict, created_by: str, origin: str) -> dict:
    """הזמנת קליטה — הגוף של `POST /api/console/tasks/capture`. ההרשאה
    נבדקת אצל הקורא; מה שכאן הוא רק מה שהמשימה עצמה דורשת."""
    mac = registry.normalize_mac(body.get("mac", ""))
    machine = registry.lookup(ctx.conn, mac) if mac else None
    if machine is None:
        raise HTTPException(400, "מכונה לא רשומה")
    role = machine["role"]
    if role not in CAPTURE_ROLES:
        raise HTTPException(
            400,
            "קליטת אימג' אינה נעשית ממכונה בתפקיד "
            f"{role!r} — אין לה מערכת מקומית משלה שיש מה לקלוט ממנה")
    name = (body.get("name") or "").strip()
    disk = (body.get("disk") or "").strip()
    if not name or not disk:
        raise HTTPException(400, "צריך שם אימג' ודיסק מקור")
    # ‏#138: השם והתיקייה נבדקים כאן ולא במסך — מסך התחנה הוא קונסולת
    # טקסט של לינוקס, בלי גליפים עבריים ובלי RTL.
    # הערכים **הגולמיים** נבדקים, לא המנוקים: רווח בקצה נדחה בשמו.
    folder = body.get("folder") or ""
    # ‏#1251 (נדב, 10/10): אימג' חדש נשמר תמיד בתיקייה. האכיפה כאן ולא
    # רק במסכים — כל לקוח מגיע לנקודה הזו. אימג'ים ישנים בלי תיקייה
    # ממשיכים להופיע ולשחזר; ההכרעה היא על קליטה חדשה בלבד.
    if not folder:
        raise HTTPException(
            400, "צריך תיקיית יעד — אימג' חדש נשמר תמיד בתיקייה. "
                 "אם אין תיקייה, צרו אחת (בקונסולה: אימג'ים ← תיקייה חדשה)")
    try:
        name = validate_display_name(body.get("name") or "", "שם האימג'")
        folder = validate_display_name(folder, "שם התיקייה")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if active_task(ctx.conn, mac) is not None:
        raise HTTPException(409, "כבר יש משימה פתוחה למכונה הזו")
    loc_id = body.get("location_id") or storage_locations.LOCAL_ID
    if not storage_locations.location_writable(ctx.conn, loc_id):
        raise HTTPException(409, "המיקום אינו זמין לקליטה")
    # ‏#1886: לפני שהמשימה נוצרת — מקום לגרסה הקודמת של האימג' + מרווח + הרזרבה, או סירוב
    # עכשיו (לא 45 דקות שנכשלות בסוף). בלי גרסה קודמת / בלי מדידה — אזהרה, לא סירוב.
    try:
        free = _free_bytes(ctx.library.root)
    except OSError:
        free = None
    storage_history.sample_local(ctx.conn, ctx.library, "capture")
    space = storage_history.capture_check(ctx.conn, ctx.library, name, folder, mac, free)
    if space["status"] == "refused":
        journal(ctx.conn, "capture_space_refused", f'{mac} "{name}" — {space["reason"]}', created_by)
        raise HTTPException(409, space["reason"])
    if space["status"] == "warning":
        journal(ctx.conn, "capture_space_unchecked", f'{mac} "{name}" — {space["reason"]}', created_by)

    # ‏#1901: הזהות של הדיסק שנבחר, לא רק שמו — סדר `sda`/`sdb` מתחלף בין אתחולים.
    serial = _reported_serial(ctx.conn, mac, disk)
    task_id = "tsk_" + secrets.token_hex(2)
    image_id = "img_" + secrets.token_hex(3)
    now = now_iso()
    # ‏#54/#1934: הכתיבה בתוך הנעילה + writing(). ה-commit החשוף עבר מ-
    # ‏create_capture לכאן ב-#1786 ונשאר מחוץ לכלל.
    with _write_lock, writing(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO tasks (id, mac, type, disk, disk_serial, image_id, name, description,"
            " folder, created_by, created_at, updated_at, token, origin)"
            " VALUES (?, ?, 'capture', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, mac, disk, serial, image_id, name,
             (body.get("description") or "").strip(),
             folder, created_by, now, now, new_token(), origin),
        )
    journal(ctx.conn, "capture_start",
            f'{task_id} {mac} disk={disk} serial={serial or "-"} "{name}"', created_by)
    # ‏**האסימון אינו מוחזר כאן.** הוא נמסר רק ב-hello, ורק למכונה
    # שה-MAC שלה תואם — אחרת הוא היה יוצא לקונסולה, ליומן ולכל
    # מי שרואה את התשובה.
    return {"id": task_id, "image_id": image_id}


def create_console_capture_router(ctx: ServerContext) -> APIRouter:
    """מה שהקונסולה מדברת איתו — יצירת משימה, מעקב, ביטול."""
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    def _capture_forecast(r) -> dict | None:
        if r["type"] != "capture" or r["state"] != "running":
            return None
        detail = capture_detail.view(r) or {}
        return learn_eta.capture(ctx.conn, ctx.library, r["mac"], detail.get("total_used_bytes"),
                                 r["started_at"], r["id"])

    @router.get("/tasks")
    def tasks(user=Depends(current_user)):
        rows = ctx.conn.execute(
            "SELECT t.*, m.suffix, g.label AS group_label, u.role AS created_by_role"
            " FROM tasks t"
            " LEFT JOIN machines m ON m.mac = t.mac"
            " LEFT JOIN groups g ON g.id = m.group_id"
            " LEFT JOIN users u ON u.username = t.created_by COLLATE NOCASE"
            " ORDER BY t.created_at DESC LIMIT 20"
        ).fetchall()
        return [
            {
                "id": r["id"], "mac": r["mac"], "machine": r["suffix"],
                "group_label": r["group_label"], "type": r["type"],
                "disk": r["disk"], "image_id": r["image_id"], "name": r["name"],
                # ‏#968: תיקיית היעד כפי שהוזמנה — עמודת "יעד" בסקירה.
                # ‏"" = שורש הספרייה (בלי תיקייה), לא "לא ידוע".
                "folder": r["folder"],
                "state": r["state"], "error": r["error"],
                # ‏#980 §4: השלב שהסוכן דיווח ומתי נכנס אליו; ‏None = לא דווח.
                "stage": r["stage"], "stage_started_at": r["stage_started_at"],
                # ‏#435: ‏bytes_* דחוסים, ‏source_progress בציר הבלוקים —
                # ‏`capture_progress` מקפל אפס-מכנה ל-`None` (לא ל-`0%`).
                **capture_progress(r),
                # ‏#1470: האימות ברקע — כמה נקרא שוב מהדיסק מתוך כמה. ‏None =
                # המשימה לא הגיעה לאימות (לא "0 מתוך 0").
                "verify_bytes": r["verify_bytes"], "verify_total": r["verify_total"],
                # ‏#1505: פירוט חי — מחיצות, קצב, ETA; ‏None = הסוכן לא דיווח טבלה.
                "capture_detail": capture_detail.view(r),
                # ‏#1843: קליטה שרצה — כמה זמן צפויה לפי הקליטות הקודמות של המכונה.
                # ‏None = לא קליטה רצה; ‏`seconds: null` + ‏`why` = אין עדיין הערכה.
                "forecast": _capture_forecast(r),
                "created_at": r["created_at"], "updated_at": r["updated_at"],
                # ‏#1255: מי יצר את המשימה (עמודת "יוזם" בפאנל "משימות אחרונות"), ותפקידו —
                # משתמש הפצה אינו נכנס לקונסולה (#1073), לכן קליטה שלו הופעלה ממחשב הבנייה.
                # ‏`None` = אין רשומה (משתמש שנמחק), לא "ריק".
                "created_by": r["created_by"] or None,
                "created_by_role": r["created_by_role"],
            }
            for r in rows
        ]

    @router.post("/tasks/capture")
    async def create_capture(request: Request, user=Depends(admin_only)):
        try:
            body = await request.json()
        except ValueError:
            # גוף פגום הוא שגיאת לקוח (400), לא קריסת שרת (500).
            raise HTTPException(400, "הגוף אינו JSON תקין")
        return order_capture(ctx, body, user[0], request_origin(request))

    @router.post("/tasks/{task_id}/cancel")
    def cancel(task_id: str, user=Depends(admin_only)):
        if not update_one(
            ctx.conn,
            "UPDATE tasks SET state = 'cancelled', updated_at = ? WHERE id = ?"
            " AND state IN ('pending', 'running')",
            (now_iso(), task_id),
        ):
            raise HTTPException(409, "המשימה כבר אינה פתוחה")
        ctx.conn.commit()
        shutil.rmtree(staging_dir(ctx.library.root, task_id), ignore_errors=True)
        journal(ctx.conn, "capture_cancel", task_id, user[0])
        return {"ok": True}

    return router
