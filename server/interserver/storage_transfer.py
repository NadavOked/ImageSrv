"""Storage Nodes — העברת אימג' מהראשי למשני (#655 v1, הכרעת נדב 16/09).

הראשי **דוחף**: משימת רקע קוראת את תיקיית האימג' מהספרייה שעל הדיסק
(``archive.tar_stream`` — אותו tar של "הורדה למחשב"), ומזרימה אותה למשני
דרך הערוץ הבין-שרתי המאומת (mTLS 1.3, SPKI-pin, טוקן כרוך לתעודה —
‏#740). המשני מכניס דרך ``import_tar_stream`` (‏#1457: חילוץ ו-sha256 תוך כדי
הזרם) — ‏sha256 של כל מחיצה מול המניפסט
**לפני** שהאימג' נכנס לספרייה (עיקרון 6); הראשי לא מקבל "הצלחה" אלא
מתשובת 200 שאחרי האימות.

מה נשמר ב-DB (``storage_transfers``) הוא **תיאום זמני**: מצב, בייטים
שנשלחו/סה"כ, ושגיאה. לא מלאי — "האם למשני יש X" נשאל מהדיסק של המשני.

חידוש אחרי ניתוק: v1 = **מתחיל מחדש בגלוי**. ניתוק באמצע מסתיים ב-
``failed`` עם "נותק אחרי N מתוך M בייטים", והמפעיל מפעיל שוב; המשני
מחק את מה שהתקבל (``import_tar`` לעולם לא משאיר תיקייה חלקית). לא
"נתקע" ולא "ממשיך" מאמצע.

חד-כיווני: הפונקציות כאן רצות רק על standalone (``assert_can_manage_nodes``),
ומשני אינו דוחף מעלה.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from server.storage import disk_reserve
from server.interserver import interserver_auth, storage_client, storage_nodes, transfer_cap
from server.core.atomicfile import fsync_dir, fsync_file, write_atomic
from server.db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.storage_transfer")

STATES = ("queued", "sending", "verifying", "done", "failed")
ACTIVE_STATES = ("queued", "sending", "verifying")
#: כמה בייטים בין עדכוני התקדמות ל-DB — לא כל מנה של 1MB היא כתיבה.
PROGRESS_STEP = 64 * 1024 * 1024
#: כמה העברות מוחזרות לתצוגה.
LIST_LIMIT = 20
#: תיקיית הביניים של pull — אזור עבודה (מתחיל בנקודה) שהספרייה מדלגת עליו.
INCOMING = ".incoming"
#: ‏#1458: שוליים מעל קבצי המחיצה ב-pull — כמו ``FREE_SPACE_MARGIN`` של
#: הקליטה במשני. בלי מכפיל: הקבצים נכתבים ל-``.incoming`` ונכנסים לספרייה
#: ב-``rename`` על אותה מערכת קבצים, בלי עותק.
PULL_FREE_SPACE_MARGIN = 256 * 1024 * 1024

_cancel_flags: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


class Cancelled(Exception):
    """המפעיל ביטל את ה-pull."""


class TransferError(ValueError):
    """בקשת העברה שנדחתה לפני שהתחילה — עם קוד HTTP שה-route מתרגם."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _note_contact_failure(conn, node_id: str, exc: BaseException, detail: str) -> None:
    """‏#1017: כשל **בערוץ** (חיבור/TLS/תשובה שאינה 200) נרשם על השורה של
    המשני — "לא ענה — HH:MM" בטבלת הסניפים. כשל לוגי של ההעברה ("האימג'
    כבר קיים", sha256) אינו כשל חיבור, והמשני שענה עליו כבר נרשם כ"ענה"."""
    if isinstance(exc, (storage_client.InterserverClientError, OSError)):
        storage_nodes.record_contact(conn, node_id, error=detail)


def _node(conn, node_id: str):
    return conn.execute(
        "SELECT id, label, base_url, pinned_spki, credential_ref, disabled_at"
        " FROM storage_nodes WHERE id = ?", (node_id,)).fetchone()


def list_transfers(conn, user: tuple[str, str], *,
                   limit: int = LIST_LIMIT) -> list[dict]:
    """ההעברות האחרונות (חדשה ראשונה), לכל המשניים."""
    storage_nodes.assert_can_manage_nodes(conn, user)
    rows = conn.execute(
        "SELECT t.id, t.node_id, n.label AS node_label, t.image_id, t.image_name,"
        " t.state, t.bytes_sent, t.bytes_total, t.error, t.started_by,"
        " t.created_at, t.updated_at, t.direction, t.location_id"
        " FROM storage_transfers t JOIN storage_nodes n ON n.id = t.node_id"
        " ORDER BY t.created_at DESC, t.id LIMIT ?", (limit,)).fetchall()
    now = datetime.now(timezone.utc)
    return [{**dict(r), "seconds_since_update": _seconds_since(r["updated_at"], now)}
            for r in rows]


def _seconds_since(stamp: str | None, now: datetime) -> int | None:
    """‏#1462: כמה שניות עברו מאז העדכון האחרון של השורה — בשעון **השרת**, כדי
    שהקונסולה לא תחסיר את שעון הדפדפן מזמן של השרת. בשלב ``verifying`` זה
    הזמן שעבר מאז הכניסה לשלב (אין עדכון באמצעו); ב-``sending`` — מאז מנת
    ה-64MiB האחרונה. ‏``None`` = חותמת שלא נקראה, לא 0."""
    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        return None
    return max(0, int((now - then).total_seconds()))


def _set_state(conn, transfer_id: str, state: str, *, bytes_sent: int | None = None,
               bytes_total: int | None = None, error: str | None = None) -> None:
    assert state in STATES
    sets = ["state = ?", "updated_at = ?"]
    params: list = [state, now_iso()]
    if bytes_sent is not None:
        sets.append("bytes_sent = ?")
        params.append(bytes_sent)
    if bytes_total is not None:
        sets.append("bytes_total = ?")
        params.append(bytes_total)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    params.append(transfer_id)
    with _write_lock, writing(conn):
        conn.execute(f"UPDATE storage_transfers SET {', '.join(sets)} WHERE id = ?",
                     params)


def start_transfer(ctx, data_dir, node_id: str, image_id: str,
                   user: tuple[str, str], *, location_id: str = "loc_local",
                   run_in_thread: bool = True) -> str:
    """מאמת (משני קיים ופעיל, אימג' קיים, אין העברה פעילה לאותו זוג), רושם
    שורה ``queued`` ומפעיל את הדחיפה ברקע. מחזיר את מזהה ההעברה."""
    conn = ctx.conn
    storage_nodes.assert_can_manage_nodes(conn, user)
    if re.fullmatch(r"loc_(?:local|[0-9a-f]{8})", location_id) is None:
        raise TransferError(400, "מזהה מיקום האחסון אינו תקין")
    node = _node(conn, node_id)
    if node is None:
        raise TransferError(404, "שרת משנה לא קיים")
    if node["disabled_at"]:
        raise TransferError(409, "שרת המשנה מושבת")
    manifest = ctx.library.get(image_id)
    if manifest is None:
        raise TransferError(404, "אימג' לא קיים")
    if data_dir is None:
        raise TransferError(500, "data_dir לא הוגדר — אין זהות TLS בין-שרתית")
    from server.library.archive import tar_size
    total = tar_size(Path(manifest["_dir"]))
    transfer_id = uuid.uuid4().hex
    with _write_lock, writing(conn):
        active = conn.execute(
            "SELECT 1 FROM storage_transfers WHERE node_id = ? AND image_id = ?"
            " AND state IN ('queued', 'sending', 'verifying')",
            (node_id, image_id)).fetchone()
        if active is not None:
            raise TransferError(409, "כבר רצה העברה של האימג' הזה לשרת המשנה הזה")
        stamp = now_iso()
        conn.execute(
            "INSERT INTO storage_transfers (id, node_id, image_id, image_name,"
            " state, bytes_sent, bytes_total, error, started_by, created_at,"
            " updated_at, location_id)"
            " VALUES (?, ?, ?, ?, 'queued', 0, ?, NULL, ?, ?, ?, ?)",
            (transfer_id, node_id, image_id, manifest["name"], total, user[0],
             stamp, stamp, location_id))
    journal(conn, "storage_transfer_start",
            f'{transfer_id} {image_id} "{manifest["name"]}" -> {node["label"]}',
            user[0])
    if run_in_thread:
        threading.Thread(target=run_transfer, args=(ctx, data_dir, transfer_id),
                         name=f"transfer-{transfer_id[:8]}", daemon=True).start()
    return transfer_id


def run_transfer(ctx, data_dir, transfer_id: str) -> None:
    """גוף המשימה — רץ בתהליכון משלו (חיבור DB משלו, ``db.Database``).
    כל חריגה מסתיימת ב-``failed`` עם סיבה מוסתרת-סודות; אין "נתקע"."""
    conn = ctx.conn
    row = conn.execute(
        "SELECT node_id, image_id, image_name, bytes_total, location_id"
        " FROM storage_transfers"
        " WHERE id = ?", (transfer_id,)).fetchone()
    if row is None:
        return
    try:
        _push(ctx, data_dir, transfer_id, row)
    except Exception as exc:                                     # noqa: BLE001
        detail = interserver_auth.redact_secrets(str(exc))
        _note_contact_failure(conn, row["node_id"], exc, detail)
        log.warning("transfer %s failed: %s", transfer_id, detail)
        # היומן לפני המצב הסופי, בכל אתר: המצב הוא מה שהקונסולה (והטסטים)
        # ממתינים לו, ומי שרואה ``failed``/``done`` חייב לראות גם את
        # האירוע — בסדר ההפוך יש חלון שבו המצב סופי והיומן עוד ריק.
        journal(conn, "storage_transfer_failed",
                f"{transfer_id} {row['image_id']}: {detail}", "")
        _set_state(conn, transfer_id, "failed", error=detail)


def _push(ctx, data_dir, transfer_id: str, row) -> None:
    from server.library.archive import tar_stream
    conn = ctx.conn
    node = _node(conn, row["node_id"])
    if node is None:
        raise RuntimeError("שרת המשנה נמחק לפני שההעברה התחילה")
    manifest = ctx.library.get(row["image_id"])
    if manifest is None:
        raise RuntimeError("האימג' נמחק לפני שההעברה התחילה")
    client, token = storage_nodes.node_client(conn, data_dir, node)
    image_id = row["image_id"]
    last = {"mark": 0}
    # ‏#1473: ‏put_stream קורא לזה אחרי כל מנה שנשלחה — ההמתנה כאן היא הקצב.
    pace = transfer_cap.pacer(conn)

    def on_progress(sent: int) -> None:
        if sent - last["mark"] >= PROGRESS_STEP:
            last["mark"] = sent
            _set_state(conn, transfer_id, "sending", bytes_sent=sent)
        pace(sent)

    _set_state(conn, transfer_id, "sending", bytes_sent=0)
    with client:
        present = client.get_json(f"/images/{image_id}", token)
        storage_nodes.record_contact(conn, node["id"], error=None)   # #1017: ענה
        if present.get("present") is True:
            raise RuntimeError(f"האימג' {image_id} כבר קיים בספריית המשני")

        def chunks():
            for chunk in tar_stream(Path(manifest["_dir"]), image_id):
                yield chunk
            # כל הבייטים יצאו; מכאן המשני משווה sha256 ומכניס לספרייה. משני
            # מעודכן כבר חישב תוך כדי (#1457); משני ישן מחלץ ומאמת עכשיו — דקות.
            _set_state(conn, transfer_id, "verifying", bytes_sent=row["bytes_total"])

        result = client.put_stream(f"/images/{image_id}", token, chunks(),
                                   row["bytes_total"], on_progress=on_progress,
                                   headers={"ImageSrv-Location-Id": row["location_id"]})
    if result.get("ok") is not True or result.get("id") != image_id:
        raise RuntimeError(f"תשובה לא צפויה מהמשני: {result!r}")
    journal(conn, "storage_transfer_done",
            f'{transfer_id} {image_id} "{row["image_name"]}" -> {node["label"]}', "")
    _set_state(conn, transfer_id, "done", bytes_sent=row["bytes_total"])


def _cancel_flag(transfer_id: str) -> threading.Event:
    with _cancel_lock:
        return _cancel_flags.setdefault(transfer_id, threading.Event())


def _is_cancelled(transfer_id: str) -> bool:
    flag = _cancel_flags.get(transfer_id)
    return flag is not None and flag.is_set()


def _clear_incoming(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        log.warning("pull incoming was left behind at %s", path)


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _check_pull_space(root: Path, incoming: Path, files: list[str], total: int) -> None:
    """‏#1458: לפני הבייט הראשון — האם מה שנשאר להוריד נכנס לספרייה.

    ‏``total`` הוא ``size_bytes`` של המשני (סכום קבצי המחיצה). מה שכבר
    ב-``.incoming`` מניסיון קודם כבר תופס מקום, ולכן מנוכה. "לא הצלחנו
    לבדוק" — גודל שלא דווח או מקום שלא נקרא — הוא כשל, לא "נכנס" (עיקרון 5)."""
    if total <= 0:
        raise RuntimeError("שרת המשנה לא דיווח את גודל האימג' (size_bytes) —"
                           " לא ניתן לבדוק אם יש מקום בראשי")
    already = sum((incoming / name).stat().st_size for name in files
                  if (incoming / name).is_file())
    # ‏#1737: ועוד הרזרבה למערכת (`disk_reserve`).
    keep = disk_reserve.reserve()
    need = max(total - already, 0) + PULL_FREE_SPACE_MARGIN + keep
    try:
        free = _free_bytes(root)
    except OSError as exc:
        raise RuntimeError(f"לא ניתן לקרוא את המקום הפנוי בספריית הראשי: {exc}") from exc
    if free < need:
        raise RuntimeError(f"אין די מקום פנוי בראשי: נדרשים {need} בייטים"
                           f" (מתוכם {keep} נשמרים למערכת), פנויים {free}"
                           f" (חסרים {need - free})")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def cancel_transfer(conn, transfer_id: str, user: tuple[str, str]) -> dict:
    """מסמן pull פעיל לביטול. ה-worker מוחק את ``.incoming`` ומסיים failed."""
    storage_nodes.assert_can_manage_nodes(conn, user)
    row = conn.execute(
        "SELECT id, state, direction FROM storage_transfers WHERE id = ?",
        (transfer_id,)).fetchone()
    if row is None:
        raise TransferError(404, "העברה לא קיימת")
    if row["direction"] != "pull":
        raise TransferError(409, "ביטול נתמך רק בהעברה משרת המשנה")
    if row["state"] not in ACTIVE_STATES:
        raise TransferError(409, "ההעברה אינה פעילה")
    _cancel_flag(transfer_id).set()
    return {"ok": True}


def start_pull(ctx, data_dir, node_id: str, image_id: str,
               user: tuple[str, str], *, run_in_thread: bool = True) -> str:
    """מאמת (משני קיים ופעיל, האימג' אינו בראשי, אין העברה פעילה לאותו זוג),
    רושם שורה ``queued`` עם ``direction=pull`` ומפעיל את המשיכה ברקע."""
    from server.library.images import valid_image_id
    conn = ctx.conn
    storage_nodes.assert_can_manage_nodes(conn, user)
    node = _node(conn, node_id)
    if node is None:
        raise TransferError(404, "שרת משנה לא קיים")
    if node["disabled_at"]:
        raise TransferError(409, "שרת המשנה מושבת")
    if not valid_image_id(image_id):
        raise TransferError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    if ctx.library.get(image_id) is not None:
        raise TransferError(409, f"האימג' {image_id} כבר קיים בספריית הראשי")
    if data_dir is None:
        raise TransferError(500, "data_dir לא הוגדר — אין זהות TLS בין-שרתית")
    transfer_id = uuid.uuid4().hex
    with _write_lock, writing(conn):
        active = conn.execute(
            "SELECT 1 FROM storage_transfers WHERE node_id = ? AND image_id = ?"
            " AND state IN ('queued', 'sending', 'verifying')",
            (node_id, image_id)).fetchone()
        if active is not None:
            raise TransferError(409, "כבר רצה העברה של האימג' הזה עם שרת המשנה הזה")
        stamp = now_iso()
        conn.execute(
            "INSERT INTO storage_transfers (id, node_id, image_id, image_name,"
            " state, bytes_sent, bytes_total, error, started_by, created_at,"
            " updated_at, direction) VALUES (?, ?, ?, ?, 'queued', 0, 0, NULL,"
            " ?, ?, ?, 'pull')",
            (transfer_id, node_id, image_id, image_id, user[0], stamp, stamp))
    journal(conn, "storage_transfer_start",
            f'{transfer_id} {image_id} <- {node["label"]}', user[0])
    if run_in_thread:
        threading.Thread(target=run_pull, args=(ctx, data_dir, transfer_id),
                         name=f"pull-{transfer_id[:8]}", daemon=True).start()
    return transfer_id


def run_pull(ctx, data_dir, transfer_id: str) -> None:
    """גוף ה-pull — תהליכון עם חיבור DB משלו. כשל/ביטול = ``failed`` גלוי."""
    from server.library.images import inside, valid_image_id
    conn = ctx.conn
    row = conn.execute(
        "SELECT node_id, image_id, image_name, bytes_total FROM storage_transfers"
        " WHERE id = ?", (transfer_id,)).fetchone()
    if row is None:
        return
    incoming = None
    image_id = row["image_id"]
    if valid_image_id(image_id):
        incoming = inside(Path(ctx.library.root) / INCOMING / image_id,
                          Path(ctx.library.root))
    try:
        _pull(ctx, data_dir, transfer_id, row, incoming)
    except Cancelled:
        if incoming is not None:
            _clear_incoming(incoming)
        log.info("pull %s cancelled", transfer_id)
        journal(conn, "storage_transfer_failed",
                f"{transfer_id} {image_id}: בוטל", "")
        _set_state(conn, transfer_id, "failed", error="בוטל")
    except Exception as exc:                                     # noqa: BLE001
        if _is_cancelled(transfer_id):
            if incoming is not None:
                _clear_incoming(incoming)
            journal(conn, "storage_transfer_failed",
                    f"{transfer_id} {image_id}: בוטל", "")
            _set_state(conn, transfer_id, "failed", error="בוטל")
        else:
            detail = interserver_auth.redact_secrets(str(exc))
            _note_contact_failure(conn, row["node_id"], exc, detail)
            log.warning("pull %s failed: %s", transfer_id, detail)
            journal(conn, "storage_transfer_failed",
                    f"{transfer_id} {image_id}: {detail}", "")
            _set_state(conn, transfer_id, "failed", error=detail)
    finally:
        with _cancel_lock:
            _cancel_flags.pop(transfer_id, None)


def _pull(ctx, data_dir, transfer_id: str, row, incoming: Path | None) -> None:
    from server.library.images import (ImageLibrary, inside, safe_partition_filename, streamed_partitions,
                           validate_display_name, valid_image_id)
    conn = ctx.conn
    node = _node(conn, row["node_id"])
    if node is None:
        raise RuntimeError("שרת המשנה נמחק לפני שההעברה התחילה")
    image_id = row["image_id"]
    if not valid_image_id(image_id):
        raise RuntimeError(f"מזהה אימג' לא תקין: {image_id!r}")
    if ctx.library.get(image_id) is not None:
        raise RuntimeError(f"האימג' {image_id} כבר קיים בספריית הראשי")
    root = Path(ctx.library.root)
    if incoming is None:
        raise RuntimeError("תיקיית הביניים יוצאת משורש הספרייה")
    last = {"mark": 0, "seen": 0}
    # ‏#1473: הקריאה בקצב התקרה — חלון ה-TCP מאט את המשני ששולח (`transfer_cap`).
    pace = transfer_cap.pacer(conn)
    # ‏#1737: הבדיקה המוקדמת סופרת את מה שהמשני הצהיר; כותב אחר (קליטה במקביל)
    # או קובץ ארוך מההצהרה נעצרים כאן, ברזרבה ולא באפס.
    guard = disk_reserve.StreamGuard(lambda: _free_bytes(root), "ההעברה משרת המשנה")

    def on_progress(sent: int) -> None:
        if _is_cancelled(transfer_id):
            raise Cancelled()
        if sent > last["seen"]:
            guard.wrote(sent - last["seen"])
        last["seen"] = sent
        if sent - last["mark"] >= PROGRESS_STEP:
            last["mark"] = sent
            _set_state(conn, transfer_id, "sending", bytes_sent=sent)
        pace(sent)

    client, token = storage_nodes.node_client(conn, data_dir, node)
    with client:
        listing = client.get_json("/images", token)
        storage_nodes.record_contact(conn, node["id"], error=None)   # #1017: ענה
        if not isinstance(listing, list):
            raise RuntimeError("תשובה לא צפויה משרת המשנה לרשימת אימג'ים")
        item = next((x for x in listing if isinstance(x, dict)
                     and x.get("id") == image_id), None)
        if item is None:
            raise RuntimeError(f"האימג' {image_id} אינו בספריית המשני")
        total = int(item.get("size_bytes") or 0)
        name = str(item.get("name") or image_id)
        with _write_lock, writing(conn):
            conn.execute(
                "UPDATE storage_transfers SET image_name = ?, bytes_total = ?,"
                " updated_at = ? WHERE id = ?",
                (name, total, now_iso(), transfer_id))
        if _is_cancelled(transfer_id):
            raise Cancelled()
        _set_state(conn, transfer_id, "sending", bytes_sent=0, bytes_total=total)
        manifest = client.get_json(f"/images/{image_id}/manifest", token)
        if not isinstance(manifest, dict) or manifest.get("id") != image_id:
            raise RuntimeError("מניפסט לא צפוי משרת המשנה")
        validate_display_name(str(manifest.get("name") or ""), "שם האימג'")
        if manifest.get("folder"):
            validate_display_name(str(manifest["folder"]), "שם התיקייה")
        # ‏#1340: המניפסט הוא של המשני — אותו שער כמו מניפסט בספרייה, **לפני**
        # שנוגעים בדיסק. ``file`` הוא שם קובץ יחיד בתוך ``.incoming``: ‏"../" או
        # נתיב מוחלט היו כותבים כ-root מחוץ לספרייה, וה-sha256 נבדק רק אחרי הכתיבה.
        problem = ImageLibrary._validate(manifest)
        if problem is not None:
            raise RuntimeError(f"מניפסט המשני נפסל: {problem}")
        files = [p["file"] for p in streamed_partitions(manifest)]
        for filename in files:
            if not safe_partition_filename(filename) or inside(
                    incoming / filename, incoming) is None:
                raise RuntimeError(f"שם קובץ מחיצה לא תקין במניפסט המשני: {filename!r}")
        _check_pull_space(root, incoming, files, total)
        incoming.mkdir(parents=True, exist_ok=True)
        completed = 0
        for filename in files:
            if _is_cancelled(transfer_id):
                raise Cancelled()
            dest = incoming / filename
            def _file_progress(n, _c=completed):
                on_progress(_c + n)
            client.get_stream(
                f"/images/{image_id}/files/{quote(filename, safe='')}",
                token, dest, on_progress=_file_progress)
            completed += dest.stat().st_size
        _set_state(conn, transfer_id, "verifying", bytes_sent=completed)
        try:
            for part in streamed_partitions(manifest):
                filename = part["file"]
                path = incoming / filename
                if not path.is_file():
                    raise RuntimeError(f"חסר קובץ מחיצה אחרי ההורדה: {filename}")
                digest = _sha256_file(path)
                if digest != part["sha256"]:
                    raise RuntimeError(
                        f"אימות נכשל: {filename} אינו תואם ל-sha256")
        except RuntimeError:
            _clear_incoming(incoming)
            raise
        public = {k: v for k, v in manifest.items() if not str(k).startswith("_")}
        # ‏#1813: המחיצות לדיסק, ואז המניפסט (אטומי, עם fsync) והתיקייה —
        # כל אלה לפני ה-rename שמכניס את האימג' לספרייה.
        for filename in files:
            fsync_file(incoming / filename)
        write_atomic(incoming / "manifest.json",
                     json.dumps(public, ensure_ascii=False, indent=2) + "\n")
        fsync_dir(incoming)
        if ctx.library.get(image_id) is not None:
            _clear_incoming(incoming)
            raise RuntimeError(f"האימג' {image_id} כבר קיים בספריית הראשי")
        target = inside(root / image_id, root)
        if target is None:
            _clear_incoming(incoming)
            raise RuntimeError("יעד האימג' יוצא משורש הספרייה")
        if target.exists():
            _clear_incoming(incoming)
            raise RuntimeError(f"התיקייה {image_id} כבר קיימת")
        try:
            incoming.rename(target)
        except OSError as extra:
            _clear_incoming(incoming)
            raise RuntimeError(f"לא ניתן להכניס את האימג' לספרייה: {extra}") from extra
        fsync_dir(root)
    journal(conn, "storage_transfer_done",
            f'{transfer_id} {image_id} "{name}" <- {node["label"]}', "")
    _set_state(conn, transfer_id, "done", bytes_sent=completed)
