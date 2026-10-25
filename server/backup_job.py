"""‏#1895 — גיבוי המסד כל שעה, ושורת "גיבוי" בבריאות.

הכרעת נדב, 22/10: יעד הגיבוי נבחר בקונסולה (ובמתקין — Issue המשך); הסטטוס **כתום**
עד שנבחר יעד; גיבוי שעבר עליו יותר משעתיים הוא **"ישן"**; המסד מגובה כל שעה; גיבויים
נשמרים 30 יום.

‏`python3 -m server.backup_job --data-dir /var/lib/imagectl` — מה ש-`imagectl-backup.timer`
מריץ כל שעה (‏`install/imagectl-backup.service`, כ-root ובלי ‏`ProtectSystem=strict`: היעד
הוא דיסק אחר או NAS, מחוץ ל-`ReadWritePaths` של השרת):

1. היעד נקרא מההגדרה `backup_dest` (‏DB לקריאה בלבד). אין יעד — יציאה 0 בלי לגעת
   בדבר: זה מצב מוצהר, והבריאות אומרת אותו בכתום. יעד SMB/NFS (‏`backup_target`, #1956)
   חייב להיות מעוגן (`ensure_mounted`) — אחרת כשל בשם, לא כתיבה לדיסק המקומי.
2. עותק עקבי דרך ‏`sqlite3.Connection.backup` (‏`server.backup.consistent_db_copy` —
   ‏`integrity_check` = ok על העותק), **כתיבה אטומית** ל-`<dest>/db/imagectl-<ts>.db`: קובץ
   זמני באותה תיקייה, ‏`fsync`, ‏`os.replace`, ‏`fsync` לתיקייה — ואז קריאה חוזרת:
   ‏sha256 של מה שעל היעד = של מה שנכתב, ו-`integrity_check` על הקובץ שביעד (עיקרון 5).
3. גיזום: ‏`imagectl-<ts>.db` ב-`<dest>/db` שהחותמת **בשמו** מלפני יותר מ-30 יום. רק
   שמות בצורה הזו — לא שום קובץ אחר ביעד.
4. ‏`<data-dir>/last-backup.json` (אטומי): מתי, הצליח או לא, היעד, הקובץ, השגיאה,
   ומתי ההצלחה האחרונה. כשל = יציאה 1 והשגיאה בקובץ — לא בשקט.

‏`tools/backup-server.sh` (ידני/לילי, כולל אימג'ים וקובצי מערכת) כותב לאותו `<dest>/db`
באותם שמות, ולכן הגיזום כאן חל גם עליו.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: ההגדרה בקונסולה (`POST /api/console/settings`). ריק / חסר = לא נבחר יעד.
SETTING = "backup_dest"
STATUS_NAME = "last-backup.json"
KEEP_DAYS = 30
STALE_SECONDS = 2 * 3600
TIMER_UNIT = "imagectl-backup.timer"
_NAME = re.compile(r"^imagectl-(\d{8}-\d{6})\.db$")
_STAMP = "%Y%m%d-%H%M%S"
LABEL = "גיבוי המסד"


def check_dest(value: object) -> tuple[str | None, str | None]:
    """(היעד המנורמל, None) או (None, סיבה בעברית). ‏"" = ביטול הבחירה (חוזר לכתום).

    נתיב מוחלט, בלי `..`, לא `/`, ותיקייה **קיימת** — ראיה חיובית שהיעד שם עכשיו
    (דיסק שלא עוגן או NAS שנפל נדחים כאן, לא בגיבוי הראשון בעוד שעה)."""
    if not isinstance(value, str):
        return None, "יעד הגיבוי: נתיב תיקייה (מחרוזת)"
    path = value.strip()
    if path == "":
        return "", None
    if (len(path) > 255 or not path.startswith("/") or any(ord(c) < 32 for c in path)
            or ".." in path.split("/")):
        return None, "יעד הגיבוי: נתיב מוחלט לתיקייה, למשל /mnt/backup"
    path = "/" + "/".join(p for p in path.split("/") if p)
    if path == "/":
        return None, "יעד הגיבוי: לא שורש מערכת הקבצים — דיסק אחר או NAS מעוגן"
    if not os.path.isdir(path):
        return None, f"יעד הגיבוי: התיקייה {path} אינה קיימת (דיסק לא מעוגן? NAS לא מחובר?)"
    return path, None


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":                      # ‏Windows (תחנת פיתוח): אין fsync לתיקייה
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(target: Path, data: bytes, mode: int = 0o600) -> None:
    tmp = target.with_name(f".{target.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "no answer"
    finally:
        conn.close()


def prune(db_dir: Path, now: datetime, keep_days: int = KEEP_DAYS) -> list[str]:
    """מוחק `imagectl-<ts>.db` שהחותמת בשמו ישנה מ-`keep_days`. מחזיר את השמות שנמחקו."""
    gone = []
    limit = now - timedelta(days=keep_days)
    for path in sorted(db_dir.iterdir()):
        m = _NAME.match(path.name)
        if not m or not path.is_file():
            continue
        stamp = datetime.strptime(m.group(1), _STAMP).replace(tzinfo=timezone.utc)
        if stamp < limit:
            path.unlink()
            gone.append(path.name)
    return gone


def backup_once(db_path: Path, dest: Path, now: datetime) -> dict:
    """גיבוי אחד ל-`dest/db`, מאומת. מחזיר {file, bytes, pruned}; כל כשל — חריגה."""
    from .backup import consistent_db_copy   # noqa: PLC0415 — כמו ב-backup-server.sh

    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        data = consistent_db_copy(src)        # integrity_check == ok על העותק, אחרת חריגה
    finally:
        src.close()
    db_dir = dest / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    target = db_dir / f"imagectl-{now.strftime(_STAMP)}.db"
    _atomic_write(target, data)
    # ראיה חיובית מהיעד עצמו: מה שנקרא בחזרה הוא מה שנכתב, וה-DB שביעד נפתח ותקין.
    if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(data).hexdigest():
        raise RuntimeError(f"הקובץ ביעד ({target}) אינו מה שנכתב")
    verdict = _integrity(target)
    if verdict != "ok":
        raise RuntimeError(f"integrity_check על {target}: {verdict}")
    return {"file": target.name, "bytes": len(data), "pruned": prune(db_dir, now)}


def _mount_from_fstab(dest: Path) -> tuple[int, str]:
    """‏`mount <נקודה>` — לפי השורה ש-#1956 כתב ל-fstab. ‏(קוד, הודעה); כשל הרצה = (‎-1, הסיבה)."""
    try:
        proc = subprocess.run(["mount", str(dest)], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def ensure_mounted(dest: Path) -> None:
    """‏#1956: יעד SMB/NFS חייב להיות **מעוגן** — ‏`nofail` ב-fstab משאיר תיקייה ריקה כשה-NAS
    נפל באתחול, וגיבוי אליה נוחת על הדיסק של השרת ונקרא בחזרה "תקין". לא מעוגן → ‏`mount` אחד
    מה-fstab, ואם עדיין לא — חריגה בשם (לא כתיבה לדיסק המקומי)."""
    if os.path.ismount(dest):
        return
    code, message = _mount_from_fstab(dest)
    if not os.path.ismount(dest):
        raise RuntimeError(f"היעד {dest} אינו מעוגן (SMB/NFS), והעיגון מ-fstab נכשל "
                           f"({message or f'exit {code}'}) — הגיבוי לא נכתב לדיסק המקומי")


def read_status(data_dir: Path) -> dict | None:
    """הסטטוס האחרון, או None כשאין קובץ. קובץ שאינו JSON — חריגה (לא "אין גיבוי")."""
    path = data_dir / STATUS_NAME
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("הסטטוס אינו אובייקט")
    return value


def run(data_dir: Path, dest: str, now: datetime | None = None, network: bool = False) -> dict:
    """גיבוי אחד + קובץ הסטטוס. מחזיר את הסטטוס שנכתב (`ok` אומר אם הצליח).
    ‏`network` — יעד SMB/NFS (#1956): קודם `ensure_mounted`."""
    now = now or datetime.now(timezone.utc)
    try:
        previous = read_status(data_dir) or {}
    except (OSError, ValueError):
        previous = {}
    status = {"at": now.isoformat(timespec="seconds"), "dest": dest, "ok": False,
              "file": None, "bytes": None, "error": None, "pruned": [],
              "last_ok": previous.get("last_ok") if previous.get("dest") == dest else None}
    try:
        if network:
            ensure_mounted(Path(dest))
        status.update(backup_once(data_dir / "imagectl.db", Path(dest), now))
        status["ok"] = True
        status["last_ok"] = status["at"]
    except Exception as exc:                  # noqa: BLE001 — כל כשל נכתב בשמו, ויציאה 1
        status["error"] = f"{type(exc).__name__}: {exc}"
    _atomic_write(data_dir / STATUS_NAME,
                  (json.dumps(status, ensure_ascii=False, indent=1) + "\n").encode("utf-8"), 0o644)
    return status


def _dest_from_db(db_path: Path) -> tuple[str, bool]:
    """(היעד, האם הוא SMB/NFS לפי `backup_target` של #1956)."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = dict(conn.execute("SELECT key, value FROM settings WHERE key IN (?, ?)",
                                 (SETTING, "backup_target")).fetchall())
    finally:
        conn.close()
    try:
        kind = (json.loads(rows.get("backup_target") or "null") or {}).get("type")
    except (ValueError, AttributeError):
        kind = None
    return (rows.get(SETTING) or "").strip(), kind in ("smb", "nfs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ImageSrv: hourly database backup (#1895)")
    parser.add_argument("--data-dir", default="/var/lib/imagectl")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    dest, network = _dest_from_db(data_dir / "imagectl.db")
    if not dest:
        print("backup_job: no backup destination chosen (console settings) -- nothing to do")
        return 0
    status = run(data_dir, dest, network=network)
    if not status["ok"]:
        print(f"backup_job: FAILED to {dest}: {status['error']}", file=sys.stderr)
        return 1
    print(f"backup_job: {status['file']} ({status['bytes']} bytes) -> {dest}/db, pruned {len(status['pruned'])}")
    return 0


# --- הבריאות -------------------------------------------------------------------


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "לפני פחות מדקה"
    if minutes < 120:
        return f"לפני {minutes} דק'"
    return f"לפני {minutes // 60} שעות"


def health_check(conn: sqlite3.Connection, data_dir: Path | None, timer_state: str,
                 now: datetime | None = None) -> dict:
    """שורת "גיבוי המסד" ב-`/health`. ‏`timer_state` — `systemctl is-active` של
    `imagectl-backup.timer` ("" = לא ניתן לבדוק). ‏{"id","label","state","detail"}."""
    from .backup_target import disk_warning, load   # noqa: PLC0415 — ‏backup_target מייבא את המודול הזה
    from .db import get_setting   # noqa: PLC0415

    def row(state: str, detail: str) -> dict:
        return {"id": "backup", "label": LABEL, "state": state, "detail": detail}

    now = now or datetime.now(timezone.utc)
    dest = (get_setting(conn, SETTING) or "").strip()
    if not dest:
        return row("warn", "לא נבחר יעד גיבוי — בוחרים בהגדרות (דיסק אחר או NAS); עד אז המסד אינו מגובה")
    if data_dir is None:
        return row("unknown", "תיקיית הנתונים אינה ידועה — לא נבדק")
    if timer_state and timer_state != "active":
        return row("bad", f"הגיבוי השעתי אינו מתוזמן ({TIMER_UNIT}: {timer_state}) — "
                          f"systemctl enable --now {TIMER_UNIT}")
    try:
        status = read_status(data_dir)
    except (OSError, ValueError) as exc:
        return row("bad", f"קובץ הסטטוס של הגיבוי לא נקרא: {exc}")
    if status is None or status.get("dest") != dest:
        return row("warn", f"עוד לא רץ גיבוי אל {dest} — הגיבוי רץ כל שעה")
    last_ok = status.get("last_ok")
    if not status.get("ok"):
        tail = f" · ההצלחה האחרונה: {last_ok}" if last_ok else " · אין גיבוי מוצלח ליעד הזה"
        return row("bad", f"הגיבוי האחרון ({status.get('at')}) נכשל: {status.get('error')}{tail}")
    try:
        age = (now - datetime.fromisoformat(str(last_ok))).total_seconds()
    except (TypeError, ValueError):
        return row("bad", f"זמן הגיבוי האחרון אינו קריא ({last_ok!r}) — לא נבדק")
    if age < 0:
        return row("warn", f"זמן הגיבוי האחרון ({last_ok}) בעתיד — השעון זז? לא נבדק")
    if age > STALE_SECONDS:
        return row("warn", f"ישן — הגיבוי האחרון הצליח {_ago(age)} ({status.get('file')}, {dest})")
    done = f"גובה {_ago(age)} — {status.get('file')} ב-{dest}"
    # ‏#1956: תיקייה על דיסק המסד (או שלא נבדק) — כתום גם כשהגיבוי מצליח: לא מגן מתקלת דיסק.
    warning = disk_warning(load(conn))
    return row("warn", f"{warning} · {done}") if warning else row("ok", done)


if __name__ == "__main__":
    sys.exit(main())
