"""אימג' → דיסק וירטואלי, ודיסק → גרסה חדשה של האימג' (‏#1334).

שני הכיוונים הם **שחזור וקליטה**, לא משהו שדומה להם: הטבלה, ההרחבה,
הכיווץ, שערי ההברנציה וה-BitLocker ו-`boot_ca` רצים בפונקציות של הסוכן
עצמן (`server/vm/agent_shell.py`) מול `/dev/loopN`. כאן נשאר רק מה שבסוכן
קשור לרשת/למסך: הזרם (קובץ ↔ ‏zstd ↔ ‏partclone, עם sha256 על הבייטים
הדחוסים — אותו מקום מדידה כמו בסוכן, ‏#73), קריאת הטבלה בחזרה, והרכבת
המניפסט.

ארבעת המיפויים הקטנים (`partclone_for_fs`, ‏`partclone_mode`,
‏`partition_role`, ‏`mark_expandable`) הם העתקים — ולכן יש להם טסט
(`tests/test_vmdisk_agent_parity.py`) שמריץ את גרסת הסוכן ואת זו על אותו
קלט ומשווה. סטייה ביניהם היא טסט אדום.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from server.vm import agent_shell
from server.library.images import (MACHINE_MAC, bound_mac, carries_a_stream_file, disk_family, image_os,
                           lineage_of, required_bytes, version_of)

log = logging.getLogger("imagesrv.vmdisk")

CHUNK = 1 << 20
MIB = 1 << 20
SECTOR = 512
#: כמו `CAPTURE_LEVEL`/`CAPTURE_THREADS` של `agent/lib/capture/capture.sh`.
CAPTURE_LEVEL = 3
CAPTURE_THREADS = 2
#: תקרה לכל כלי בזרם. נגמר הזרם — הכלים יוצאים בשניות; כלי שלא יצא אחרי
#: זה תקוע, ונהרג בשמו (לולאות רקע חייבות לפוג).
TOOL_WAIT_S = 3600

Progress = Callable[[int], None]


class DiskError(Exception):
    """כשל בשם: `reason` קבוע למכונה, `message` בעברית לאדם."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- ההעתקים הקטנים מהסוכן (נבדקים מול המקור) ---------------------------------


def partclone_for_fs(fs: str) -> str:
    """`partclone_for_fs` של `agent/lib/restore/restore.sh`."""
    if fs == "ntfs":
        return "partclone.ntfs"
    if fs in ("vfat", "fat", "fat32", "fat16"):
        return "partclone.fat"
    if fs in ("ext4", "ext3", "ext2"):
        return "partclone.ext4"
    if fs == "btrfs":
        return "partclone.btrfs"
    return "partclone.dd"


def partclone_mode(tool: str, flag: str) -> list[str]:
    """`partclone_mode`: ‏partclone.dd אינו מכיר -c/-r."""
    return [] if tool == "partclone.dd" else [flag]


_ROLES = {
    "C12A7328-F81F-11D2-BA4B-00A0C93EC93B": "esp",
    "E3C9E316-0B5C-4DB8-817D-F92DF00215AE": "msr",
    "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC": "recovery",
    "0FC63DAF-8483-4772-8E79-3D69D8477DE4": "linux",
    "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F": "swap",
    "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7": "windows",
}


def partition_role(type_guid: str) -> str:
    """`_partition_role` של `agent/lib/capture/manifest.sh`."""
    return _ROLES.get((type_guid or "").upper(), "data")


def mark_expandable(parts: list[dict]) -> None:
    """`_mark_expandable`: ‏windows/linux עם ה-`start_sector` הגדול ביותר;
    בשוויון — האחרונה ברשימה (‏`s >= best`). במקום."""
    pick = None
    best = 0
    for i, part in enumerate(parts):
        if part.get("role") not in ("windows", "linux"):
            continue
        start = part.get("start_sector")
        start = start if isinstance(start, int) else -1
        if pick is None or start >= best:
            best, pick = start, i
    if pick is not None:
        parts[pick]["expandable"] = True


# --- המכונה: פקודות והגשר. בדיקות מחליפות את כולה -------------------------------


class Host:
    """כל מה שנוגע במכונה. ‏`tests/` מחליף אותו בזיוף."""

    def __init__(self, sbin_path: bool = True):
        env = dict(os.environ)
        if sbin_path:
            parts = env.get("PATH", "").split(os.pathsep)
            env["PATH"] = os.pathsep.join(parts + [d for d in ("/usr/sbin", "/sbin")
                                                   if d not in parts])
        self.env = env

    def run(self, argv: list[str], *, input: str | None = None,
            timeout: float = 600) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, input=input, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=self.env,
            **({} if input is not None else {"stdin": subprocess.DEVNULL}))

    def popen(self, argv: list[str], **kw) -> subprocess.Popen:
        return subprocess.Popen(argv, env=self.env, **kw)

    def free_bytes(self, path: Path) -> int | None:
        """פנוי למי שאינו root (‏f_bavail). ‏None = לא נקרא — לא 0 ולא "מספיק"."""
        try:
            stat = os.statvfs(path)
        except (OSError, AttributeError):
            return None
        return stat.f_bavail * stat.f_frsize

    def agent(self, func: str, *args: str, run_dir: Path,
              env: dict[str, str] | None = None, user: "VmUser | None" = None) -> agent_shell.Call:
        return agent_shell.call(func, *args, run_dir=run_dir, env=env,
                                as_user=(user.uid, user.gid) if user else None)


# --- התקן loop ----------------------------------------------------------------


def _loop_name(text: str) -> str | None:
    match = re.fullmatch(r"/dev/(loop\d+)", text.strip())
    return match.group(1) if match else None


def loops_of(host: Host, disk: Path) -> list[str]:
    """כל התקני ה-loop שמחוברים לקובץ. ‏losetup שנכשל אינו "אין" (עיקרון 5)."""
    proc = host.run(["losetup", "-j", str(disk)])
    if proc.returncode != 0:
        raise DiskError("loop", f"לא ניתן לבדוק אילו התקני loop מחוברים לדיסק העבודה "
                                f"(losetup -j rc={proc.returncode}): {proc.stderr.strip()}")
    found = []
    for line in proc.stdout.splitlines():
        name = _loop_name(line.split(":", 1)[0])
        if name:
            found.append(name)
    return found


def detach_all(host: Host, disk: Path) -> None:
    for name in loops_of(host, disk):
        host.run(["losetup", "-d", f"/dev/{name}"])
    left = loops_of(host, disk)
    if left:
        raise DiskError("loop", f"התקן loop נשאר מחובר לדיסק העבודה: {', '.join(left)}")


def attach(host: Host, disk: Path) -> str:
    """`/dev/loopN` עם `--partscan` — הצורה ש-`partition_node` מצפה לה."""
    detach_all(host, disk)
    proc = host.run(["losetup", "--find", "--show", "--partscan", str(disk)])
    name = _loop_name(proc.stdout) if proc.returncode == 0 else None
    if name is None:
        raise DiskError("loop", f"לא ניתן לחבר את דיסק העבודה להתקן loop "
                                f"(rc={proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return name


def node(loop: str, index: int) -> str:
    return f"/dev/{loop}p{index}"


# --- הזרם ----------------------------------------------------------------------


def _finish(procs: list[subprocess.Popen], what: str) -> list[int]:
    codes = []
    for proc in procs:
        try:
            codes.append(proc.wait(timeout=TOOL_WAIT_S))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)
            raise DiskError("stream", f"{what}: {proc.args[0]} לא יצא תוך {TOOL_WAIT_S} שניות")
    return codes


def restore_partition(host: Host, part: dict, src: Path, target: str, run_dir: Path,
                      progress: Progress) -> None:
    """`zstd -dc | partclone -r` כמו `restore_partition` בסוכן, וה-sha256 על
    הדחוס, תוך כדי. כשל — ‏DiskError בשם המחיצה."""
    index = part["index"]
    tool = partclone_for_fs(part.get("fs") or "")
    err = run_dir / f"restore.{index}.err"
    with err.open("wb") as errfh:
        unzip = host.popen(["zstd", "-dc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=errfh)
        writer = host.popen([tool, *partclone_mode(tool, "-r"), "-s", "-", "-O", target,
                             "-L", str(run_dir / f"partclone.{index}.log")],
                            stdin=unzip.stdout, stdout=subprocess.DEVNULL, stderr=errfh)
        unzip.stdout.close()
        digest = hashlib.sha256()
        broken = False
        try:
            with src.open("rb") as fh:
                while chunk := fh.read(CHUNK):
                    digest.update(chunk)
                    try:
                        unzip.stdin.write(chunk)
                    except BrokenPipeError:
                        broken = True
                        break
                    progress(len(chunk))
        except BaseException:
            # ביטול (או כל חריגה) באמצע הזרם: הכלים נהרגים ונאספים כאן —
            # לא נשארים תהליכים יתומים שכותבים לדיסק שעומד להימחק.
            for proc in (unzip, writer):
                proc.kill()
            _finish([unzip, writer], f"מחיצה {index}")
            raise
        finally:
            try:
                unzip.stdin.close()
            except (BrokenPipeError, ValueError):
                broken = True
        rc_unzip, rc_write = _finish([unzip, writer], f"מחיצה {index}")
    tail = err.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] or [""]
    if broken or rc_unzip or rc_write:
        raise DiskError("restore", f"מחיצה {index} ({src.name}): הכתיבה לדיסק העבודה נכשלה "
                                   f"(zstd rc={rc_unzip}, {tool} rc={rc_write}) {tail[0]}".strip())
    if digest.hexdigest() != part.get("sha256"):
        raise DiskError("sha256", f"מחיצה {index} ({src.name}): ה-sha256 של הקובץ בספרייה אינו "
                                  "תואם למניפסט — האימג' פגום, הדיסק לא ייפתח")


def capture_partition(host: Host, fs: str, source: str, out: Path, log_dir: Path,
                      index: int, progress: Progress, *, user: "VmUser | None" = None,
                      limit: int | None = None, err_dir: Path | None = None) -> tuple[str, int]:
    """`partclone -c | zstd` כמו `capture_disk` בסוכן, ל-out. ‏(sha256, בייטים).

    ‏`user` — ‏partclone (המפרסר של מערכת הקבצים) רץ כמשתמש ה-VM ולא כ-root.
    ‏`limit` — זרם שגדל מעבר לזה נעצר: מחיצה אינה נדחסת ליותר מגודלה."""
    tool = partclone_for_fs(fs)
    ignore = ["-I"] if fs == "ntfs" else []
    # ה-stderr נפתח כאן, כ-root — ולכן **לא** בתיקייה של משתמש ה-VM.
    err = (err_dir or log_dir) / f"capture.{index}.err"
    # כמשתמש ה-VM — ‏`-L /dev/null`: היומן של partclone הוא התקדמות; השגיאות
    # יוצאות ב-stderr, שנאסף כאן (כ-root) ל-err. כך אין תיקייה שמשתמש ה-VM
    # צריך לכתוב אליה, ואין תלות בהרשאות המעבר בדרך לספרייה (נמדד: partclone
    # נפל על "open logfile" כשהספרייה ישבה מתחת ל-/home/test).
    logfile = "/dev/null" if user else str(log_dir / f"partclone.c{index}.log")
    argv = [tool, *partclone_mode(tool, "-c"), *ignore, "-s", source, "-L", logfile]
    with err.open("wb") as errfh:
        reader = host.popen(as_user(user, argv) if user else argv,
                            stdout=subprocess.PIPE, stderr=errfh, stdin=subprocess.DEVNULL)
        squeeze = host.popen(["zstd", f"-{CAPTURE_LEVEL}", f"-T{CAPTURE_THREADS}", "-c"],
                             stdin=reader.stdout, stdout=subprocess.PIPE, stderr=errfh)
        reader.stdout.close()
        digest = hashlib.sha256()
        total = 0
        too_big = False
        with out.open("wb") as fh:
            while chunk := squeeze.stdout.read(CHUNK):
                digest.update(chunk)
                fh.write(chunk)
                total += len(chunk)
                progress(len(chunk))
                if limit is not None and total > limit:
                    too_big = True
                    reader.kill()
                    squeeze.kill()
                    break
        rc_read, rc_squeeze = _finish([reader, squeeze], f"מחיצה {index}")
    if too_big:
        raise DiskError("capture", f"מחיצה {index}: הזרם גדל מעבר לגודל המחיצה ({limit} בייט) — נעצר")
    if rc_read or rc_squeeze:
        tail = err.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] or [""]
        raise DiskError("capture", f"מחיצה {index}: הקריאה מדיסק העבודה נכשלה "
                                   f"({tool} rc={rc_read}, zstd rc={rc_squeeze}) {tail[0]}".strip())
    return digest.hexdigest(), total


# --- אימג' → דיסק ---------------------------------------------------------------


def disk_bytes_for(manifest: dict) -> int:
    """הדיסק שהאימג' נקלט ממנו, ולפחות מה שהפריסה דורשת — מעוגל למגה."""
    need = required_bytes(manifest)
    if need is None:
        raise DiskError("image", "לא ניתן לחשב כמה מקום האימג' צריך — המניפסט חסר גיאומטריה")
    source = manifest.get("source_disk_bytes")
    size = max(need, source if isinstance(source, int) and source > 0 else 0)
    return -(-size // MIB) * MIB


def check_openable(manifest: dict) -> None:
    """מה ש-`apply_gpt` היה מסרב לו — כאן, לפני שהוקצה בייט."""
    sector = manifest.get("sector_size", SECTOR)
    if sector not in (None, SECTOR):
        raise DiskError("image", f"האימג' נקלט מדיסק {sector} בייט/סקטור — נתמך 512 בלבד")
    if manifest.get("scheme", "gpt") != "gpt":
        raise DiskError("image", f"סכמת מחיצות לא נתמכת: {manifest.get('scheme')!r}")
    if image_os(manifest) not in ("windows", "linux"):
        raise DiskError("image", "האימג' אינו windows ואינו linux — אין מה להפעיל")


def public_manifest(manifest: dict) -> dict:
    """המניפסט כפי שעל הדיסק — בלי השדות הפנימיים של הספרייה (`_dir`…)."""
    return {k: v for k, v in manifest.items() if not k.startswith("_")}


@dataclass
class BuildResult:
    disk_bytes: int
    warnings: list[str] = field(default_factory=list)


def build_disk(host: Host, manifest: dict, image_dir: Path, workdir: Path,
               progress: Progress) -> BuildResult:
    """בונה `workdir/disk.raw` מהאימג'. כל כשל = DiskError; הקורא מוחק."""
    check_openable(manifest)
    run_dir = workdir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    source_file = workdir / "source-manifest.json"
    source_file.write_text(json.dumps(public_manifest(manifest), ensure_ascii=False, indent=2),
                           encoding="utf-8")
    disk = workdir / "disk.raw"
    size = disk_bytes_for(manifest)
    with disk.open("wb") as fh:
        fh.truncate(size)            # דליל: אינו תופס מקום עד שנכתב
    warnings: list[str] = []
    loop = attach(host, disk)
    try:
        table = host.agent("apply_gpt", loop, str(source_file), run_dir=run_dir)
        if not table.ok:
            raise DiskError("table", table.reason("לא ניתן לכתוב את טבלת המחיצות"))
        grown = host.agent("expand_last", loop, str(source_file), run_dir=run_dir,
                           env={"EXPAND_OVERRIDE": "auto"})
        if not grown.ok:
            # בדיוק `run_restore`: הרחבה שנכשלה חוזרת לטבלת המניפסט.
            warnings.append("ההרחבה נכשלה — הדיסק נפתח בגודל האימג'")
            again = host.agent("apply_gpt", loop, str(source_file), run_dir=run_dir)
            if not again.ok:
                raise DiskError("table", again.reason("לא ניתן לכתוב את טבלת המחיצות"))
        for part in manifest["partitions"]:
            progress(0)          # נקודת ביטול בין מחיצות
            target = node(loop, part["index"])
            if part.get("fs") == "swap":
                made = host.agent("make_swap", target, str(part.get("uuid") or ""),
                                  run_dir=run_dir)
                if not made.ok:
                    raise DiskError("restore", f"מחיצה {part['index']}: mkswap נכשל")
                continue
            if not carries_a_stream_file(part):
                raise DiskError("image", f"מחיצה {part['index']} בלי קובץ ואינה swap — מניפסט פגום")
            from server.library.images import guarded_image_path
            src = guarded_image_path(image_dir, part["file"])
            if src is None:
                raise DiskError("image", f"קובץ המחיצה {part['file']} חסר בספרייה")
            restore_partition(host, part, src, target, run_dir, progress)
        grow = host.agent("grow_expanded", loop, run_dir=run_dir)
        if not grow.ok:
            # כמו `finish_grow`: אזהרה ולא כשל — Windows עולה גם כך, רק בלי מרווח.
            warnings.append("מערכת הקבצים לא הוגדלה: "
                            + grow.reason("ההגדלה נכשלה") + " — יש פחות מקום לעדכונים")
    finally:
        detach_all(host, disk)
    return BuildResult(size, warnings)


# --- דיסק → גרסה חדשה: הדיסק שהאורח שינה אינו מהימן -------------------------------
#
# אחרי שה-VM רץ, כל בייט בדיסק העבודה הוא קלט של האורח — Windows שהתעדכן
# מהאינטרנט. לכן בשמירה **שום מערכת קבצים שלו אינה נעגנת בקרנל של השרת,
# ושום מפרסר שלו אינו רץ כ-root**:
#
# * הטבלה, סוגי מערכות הקבצים, BitLocker, מצב השינה, המינימום לכיווץ, הכיווץ
#   עצמו, הנפח התפוס וקובצי האתחול של ה-ESP — **בתוך ה-appliance של
#   libguestfs** (קרנל משלו, ב-VM משלו). הכיווץ — על עותק (‏#1491, `shrink_view`).
# * ‏`partclone -c`, ‏`ntfsfix -d` ו-`ntfsresize -n` — על המארח, אבל **כמשתמש ה-VM** (‏setpriv,
#   ‏no-new-privs) על התקן loop של מחיצה אחת בהיסט שה-appliance מסר ושעבר
#   אימות — בלי `--partscan`, כך שהקרנל אינו קורא את ה-GPT של האורח.
# * חתימות ה-PE של קובצי האתחול (`boot_ca_json` של הסוכן, עם ESPROOT) —
#   כמשתמש ה-VM.
# * כל ערך שנקרא מהדיסק עובר אימות פורמט לפני שהוא נוגע בפקודה, ומושווה
#   למניפסט המקור: מחיצה שסוג מערכת הקבצים שלה השתנה, מחיצה חדשה או חסרה,
#   ‏GUID של דיסק אחר — סירוב בשם.

GUID_RE = re.compile(r"[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
FS_KNOWN = ("ntfs", "vfat", "ext4", "ext3", "ext2", "btrfs", "xfs", "swap", "unknown")
BOOT_PATH_RE = re.compile(r"/[A-Za-z0-9._/-]{1,200}")
#: קובץ אתחול גדול מזה אינו מטען אתחול — לא מורידים אותו מה-appliance.
BOOT_FILE_MAX = 64 * MIB
#: הדחוס של מחיצה לעולם אינו גדול מהמחיצה עצמה בהרבה; זרם שגדל מעבר לזה
#: נעצר (מערכת קבצים שנבנתה כדי לנפח את הזרם).
STREAM_SLACK = 64 * MIB


def norm_fs(value: str | None) -> str:
    fs = (value or "").strip().lower()
    if fs in ("", "unknown"):
        return "unknown"
    return "ntfs" if fs == "ntfs-3g" else fs


@dataclass
class VmUser:
    uid: int
    gid: int


def as_user(user: VmUser, argv: list[str]) -> list[str]:
    """`argv` כמשתמש ה-VM: בלי קבוצות, ‏no-new-privs."""
    return ["setpriv", f"--reuid={user.uid}", f"--regid={user.gid}", "--clear-groups",
            "--no-new-privs", "--", *argv]


class Appliance:
    """עטיפה דקה על libguestfs — כל קריאה של הדיסק הלא-מהימן עוברת כאן.
    בדיקות מחליפות את `factory` בזיוף."""

    DEV = "/dev/sda"

    def __init__(self, disk: Path, *, factory: Callable[[], object] | None = None,
                 memsize: int = 1024, writable: bool = False):
        """‏`writable=False` (‏#1491): ‏libguestfs פותח את הדיסק לקריאה בלבד —
        כתיבה של ה-appliance (‏ext4 שמשחזר יומן ב-mount_ro) נוחתת ב-overlay
        זמני שלו. ‏`writable=True` — רק על העותק של הכיווץ (`shrink_view`)."""
        if factory is None:
            try:
                import guestfs   # noqa: PLC0415 — רק בשרת; ‏python3-guestfs
            except ImportError as exc:
                raise DiskError("missing_tool", "python3-guestfs אינו מותקן בשרת") from exc

            def factory():
                return guestfs.GuestFS(python_return_dict=True)
        self.g = factory()
        try:
            self.g.set_memsize(memsize)
            self.g.add_drive_opts(str(disk), format="raw", readonly=not writable)
            self.g.launch()
        except RuntimeError as exc:
            raise DiskError("appliance", f"ה-appliance של libguestfs לא עלה: {exc}") from exc

    def dev(self, index: int) -> str:
        return f"{self.DEV}{index}"

    def close(self) -> None:
        try:
            self.g.shutdown()
        finally:
            self.g.close()

    def table(self) -> tuple[str, list[dict]]:
        g = self.g
        if g.part_get_parttype(self.DEV) != "gpt":
            raise DiskError("table", "דיסק העבודה אינו GPT")
        disk_guid = str(g.part_get_disk_guid(self.DEV))
        if not GUID_RE.fullmatch(disk_guid):
            raise DiskError("table", f"GUID הדיסק שנקרא אינו GUID: {disk_guid[:40]!r}")
        parts = []
        for row in g.part_list(self.DEV):
            index, start, size = row["part_num"], row["part_start"], row["part_size"]
            if not (type(index) is int and 1 <= index <= 128 and type(start) is int
                    and type(size) is int and start > 0 and size > 0
                    and start % SECTOR == 0 and size % SECTOR == 0):
                raise DiskError("table", f"כניסת טבלה לא תקינה: {row!r}"[:200])
            type_guid = str(g.part_get_gpt_type(self.DEV, index))
            unique = str(g.part_get_gpt_guid(self.DEV, index))
            attrs = g.part_get_gpt_attributes(self.DEV, index)
            if not GUID_RE.fullmatch(type_guid) or not (unique == "" or GUID_RE.fullmatch(unique)):
                raise DiskError("table", f"מחיצה {index}: GUID שאינו GUID")
            # ‏#1421: libguestfs מחזיר int64 עם סימן — ביט 63 (ESP/Recovery) שלילי
            if type(attrs) is not int or not -(1 << 63) <= attrs < 1 << 64:
                raise DiskError("table", f"מחיצה {index}: דגלים לא תקינים")
            attrs &= (1 << 64) - 1
            parts.append({"index": index, "type_guid": type_guid.upper(),
                          "unique_guid": unique.upper(), "attrs": f"{attrs:016X}",
                          "first": start // SECTOR, "sectors": size // SECTOR})
        if not parts:
            raise DiskError("table", "לא נמצאו מחיצות על דיסק העבודה")
        return disk_guid.upper(), parts

    def fs(self, index: int) -> str:
        try:
            return norm_fs(self.g.vfs_type(self.dev(index)))
        except RuntimeError:
            return "unknown"

    def bitlocker(self, index: int, fs: str) -> bool:
        if "bitlocker" in fs.lower():
            return True
        try:
            return bytes(self.g.pread_device(self.dev(index), 8, 3)) == b"-FVE-FS-"
        except RuntimeError as exc:
            raise DiskError("unchecked", f"לא ניתן לבדוק BitLocker במחיצה {index}: {exc}") from exc

    def hibernation(self, index: int) -> int:
        try:
            return int(self.g.ntfs_3g_probe(True, self.dev(index)))
        except RuntimeError as exc:
            raise DiskError("unchecked", f"לא ניתן לבדוק מצב שינה במחיצה {index}: {exc}") from exc

    def min_size(self, index: int) -> int:
        try:
            value = self.g.vfs_minimum_size(self.dev(index))
        except RuntimeError as exc:
            raise DiskError("shrink", f"לא ניתן לבדוק כמה אפשר לכווץ את מחיצה {index}: {exc}"
                                      " — הריצו chkdsk ב-VM ושמרו שוב") from exc
        if type(value) is not int or value <= 0:
            raise DiskError("shrink", f"מחיצה {index}: מינימום לא תקין ({value!r})")
        return value

    def shrink(self, index: int, first: int, new_bytes: int, keep: dict) -> None:
        """‏ntfsresize ואז הכניסה בטבלה, וקריאה בחזרה של התחילה, הגודל,
        וה-GUIDים והדגלים (אותה ראיה כמו `_shrink_gpt`)."""
        g = self.g
        try:
            g.ntfsresize_opts(self.dev(index), size=new_bytes)
            # הכניסה נכתבת מחדש באותו מקום ובאותו אינדקס — כמו `_shrink_gpt`
            # (‏sgdisk -d/-n). ‏`part_resize` של libguestfs הוא parted resizepart,
            # שעוצר על "Shrinking a partition can cause data loss" (נמדד ב-Testrunner).
            # ‏parted נותן למחיצה החדשה את האינדקס הפנוי הנמוך — זה שנמחק.
            g.part_del(self.DEV, index)
            g.part_add(self.DEV, "p", first, first + new_bytes // SECTOR - 1)
            g.part_set_gpt_type(self.DEV, index, keep["type_guid"])
            if keep["unique_guid"]:
                g.part_set_gpt_guid(self.DEV, index, keep["unique_guid"])
            g.part_set_gpt_attributes(self.DEV, index, int(keep["attrs"], 16))
        except RuntimeError as exc:
            raise DiskError("shrink", f"הכיווץ של מחיצה {index} נכשל: {exc}") from exc
        _guid, table = self.table()
        got = next((p for p in table if p["index"] == index), None)
        if (got is None or got["first"] != first or got["sectors"] != new_bytes // SECTOR
                or got["type_guid"] != keep["type_guid"]
                or got["unique_guid"] != keep["unique_guid"] or got["attrs"] != keep["attrs"]):
            raise DiskError("shrink", f"מחיצה {index} כווצה אך הטבלה לא אושרה בקריאה חוזרת")

    def used_bytes(self, index: int) -> int | None:
        """‏df בתוך ה-appliance. ‏None = לא נמדד (לא נעגן) — לא 0."""
        g = self.g
        try:
            g.mount_ro(self.dev(index), "/")
        except RuntimeError:
            return None
        try:
            st = g.statvfs("/")
            used = (st["blocks"] - st["bfree"]) * st["frsize"]
            return used if type(used) is int and used >= 0 else None
        except (RuntimeError, KeyError, TypeError):
            return None
        finally:
            g.umount_all()

    def fetch(self, index: int, paths: list[str], dest: Path) -> None:
        """מעתיק את קובצי האתחול מה-ESP ל-`dest` — עד BOOT_FILE_MAX כל אחד."""
        g = self.g
        try:
            g.mount_ro(self.dev(index), "/")
        except RuntimeError:
            return
        try:
            for path in paths:
                try:
                    real = g.case_sensitive_path(path)
                    if not g.is_file(real, followsymlinks=False):
                        continue
                    if g.filesize(real) > BOOT_FILE_MAX:
                        continue
                except RuntimeError:
                    continue
                target = dest / path.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                g.download(real, str(target))
        finally:
            g.umount_all()


def check_against_source(source: dict, disk_guid: str, table: list[dict],
                         fs_of: dict[int, str]) -> None:
    """מה שהאורח רשאי לשנות: גדלים ומיקומים. לא: אילו מחיצות יש, את סוגן,
    את מערכת הקבצים שבהן, ואת זהות הדיסק."""
    want = {p["index"]: p for p in source["partitions"]}
    got = {p["index"] for p in table}
    if got != set(want):
        added, gone = sorted(got - set(want)), sorted(set(want) - got)
        raise DiskError("layout", "מחיצות דיסק העבודה אינן אלה של האימג'"
                                  + (f" — נוספו {added}" if added else "")
                                  + (f" — חסרות {gone}" if gone else ""))
    declared = source.get("disk_guid")
    if declared and str(declared).upper() != disk_guid:
        raise DiskError("layout", "ה-GUID של דיסק העבודה שונה מזה של האימג'")
    for part in table:
        src = want[part["index"]]
        if str(src.get("type_guid", "")).upper() != part["type_guid"]:
            raise DiskError("layout", f"סוג מחיצה {part['index']} השתנה")
        if norm_fs(src.get("fs")) != fs_of[part["index"]]:
            raise DiskError("fs_changed",
                            f"מחיצה {part['index']}: מערכת הקבצים היא {fs_of[part['index']]}"
                            f" ובאימג' {norm_fs(src.get('fs'))} — לא נשמר")
        if fs_of[part["index"]] not in FS_KNOWN:
            raise DiskError("fs_changed", f"מחיצה {part['index']}: מערכת קבצים לא מוכרת")


#: מי שמחזיק את התקני ה-loop כשאיש אינו משתמש בהם (‏udev של דביאן).
LOOP_OWNER = "root:disk"
LOOP_MODE = "0660"


@contextmanager
def partition_loop(host: Host, disk: Path | str, part: dict, user: VmUser, *, writable: bool):
    """התקן loop על מחיצה **אחת** — היסט וגודל מהטבלה שאומתה, בלי partscan —
    שנמסר למשתמש ה-VM למשך העבודה בלבד. ‏`partclone`/`ntfsfix` פותחים נתיב
    (פתיחה חוזרת של `/dev/fd/N` בודקת הרשאות מחדש), ולכן מעבירים בעלות
    ולא fd; וב-`finally` — גם בכשל — הבעלות וההרשאות **חוזרות** לפני
    `losetup -d`, כדי שהתקן loop שיחזור לשימוש לא יישאר בידי המשתמש."""
    argv = ["losetup", "--find", "--show", "--offset", str(part["first"] * SECTOR),
            "--sizelimit", str(part["sectors"] * SECTOR)]
    if not writable:
        argv.append("--read-only")
    proc = host.run([*argv, str(disk)])
    name = _loop_name(proc.stdout) if proc.returncode == 0 else None
    if name is None:
        raise DiskError("loop", f"לא ניתן לחבר את מחיצה {part['index']} (rc={proc.returncode})")
    node = f"/dev/{name}"
    restored = False
    try:
        owned = host.run(["chown", f"{user.uid}:{user.gid}", node])
        if owned.returncode != 0:
            raise DiskError("loop", f"לא ניתן למסור את {node} למשתמש ה-VM")
        yield node
    finally:
        back = host.run(["chown", LOOP_OWNER, node])
        mode = host.run(["chmod", LOOP_MODE, node])
        host.run(["losetup", "-d", node])
        restored = back.returncode == 0 and mode.returncode == 0
    if not restored:
        raise DiskError("loop", f"הבעלות על {node} לא הוחזרה ל-{LOOP_OWNER} — בדקו ידנית")


# --- ‏#1491: הכיווץ נעשה על עותק, ודיסק ה-VM אינו נכתב ------------------------------
#
# הכלל של #1466 (נדב 16/10) גם כאן: דיסק המקור של שמירה — `disk.raw`, הדיסק
# שה-VM רץ עליו — **אינו נכתב**. ‏`ntfsresize`, הטבלה החדשה ו-`ntfsfix -d` רצים
# על dm-snapshot מעליו: המקור מחובר ל-loop **לקריאה בלבד** (נבדק ב-`blockdev
# --getro`), וה-copy-on-write הוא קובץ דליל ב-`run/` בגודל התקציב — עותק שחורג
# ממנו נפסל (‏I/O error בקול), ולא ממלא את דיסק השרת. ‏partclone קורא מהעותק,
# וכשל בכל שלב משאיר את הדיסק שלם: מדליקים שוב, מתקנים ושומרים.

VIEW_NAME = "imagesrv-vmsave"
#: סקטורים — ‏4KiB, אשכול NTFS (‏`LC_SNAP_CHUNK` של הסוכן).
VIEW_CHUNK = 8
#: ‏`LC_SNAP_META_MB` של הסוכן: ‏$LogFile, ‏$MFT/$Bitmap, הטבלה וגיבויה.
VIEW_META = 256 * MIB
#: ה-"MB" של ‏`ntfsresize` (‏NTFS_MBYTE) הוא 10^6.
NTFS_MB = 10**6


def view_budget(host: Host, disk: Path, todo: list[tuple[dict, int]], user: VmUser,
                run_dir: Path) -> int:
    """כמה בייטים העותק צריך. ‏`ntfsresize -n` (ריצת בדיקה; כמשתמש ה-VM, על loop
    **לקריאה בלבד** של המחיצה) אומר כמה יוזז — ‏M + M/10 + VIEW_META לכל מחיצה,
    כמו `_snap_views` של הסוכן, ו-`_lc_relocs` שלו קורא את המספר. לא נמדד =
    נכשל, לא "מספיק" (עיקרון 5)."""
    need = 0
    for part, new in todo:
        idx = part["index"]
        with partition_loop(host, disk, part, user, writable=False) as node:
            proc = host.run(as_user(user, ["ntfsresize", "-n", "-s", str(new),
                                           "--no-progress-bar", node]))
        said = run_dir / f"relocs.{idx}.txt"
        said.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8", newline="\n")
        mb = host.agent("_lc_relocs", str(said), run_dir=run_dir).out.strip()
        if proc.returncode != 0 or not mb.isdigit():
            raise DiskError("unchecked", f"לא ניתן למדוד כמה מקום זמני הכיווץ של מחיצה {idx} "
                                         f"צריך (ntfsresize -n rc={proc.returncode}) — דיסק ה-VM "
                                         "לא נכתב")
        need += (int(mb) + int(mb) // 10) * NTFS_MB + VIEW_META
    return need


def _dm_names(text: str) -> list[str]:
    return [line.split()[0] for line in text.splitlines() if line.strip()]


def view_valid(host: Host) -> bool:
    """‏`dmsetup status`: ‏`0 <len> snapshot <בשימוש>/<סה"כ> <meta>` = תקין.
    ‏Invalid, ‏Overflow, או שאין תשובה — לא תקין (כמו `_snap_valid`)."""
    proc = host.run(["dmsetup", "status", VIEW_NAME])
    fields = proc.stdout.split() if proc.returncode == 0 else []
    return (len(fields) >= 4 and fields[2] == "snapshot"
            and re.fullmatch(r"\d+/\d+", fields[3]) is not None)


def _release_view(host: Host, disk: Path, cow: Path) -> list[str]:
    """העותק, ה-loop של המקור וה-loop של ה-copy-on-write — מוסרים, וכל אחד
    נקרא בחזרה. ‏[] = לא נשאר דבר (או שלא היה); אחרת — מה שנשאר."""
    problems = []
    listed = host.run(["dmsetup", "ls"])
    if listed.returncode != 0:
        problems.append("dmsetup ls נכשל — לא נבדק שהעותק שוחרר")
    elif VIEW_NAME in _dm_names(listed.stdout):
        host.run(["dmsetup", "remove", "--retry", VIEW_NAME])
        again = host.run(["dmsetup", "ls"])
        if again.returncode != 0 or VIEW_NAME in _dm_names(again.stdout):
            problems.append(f"{VIEW_NAME} עדיין קיים")
    for backing in (cow, disk):
        try:
            if backing.exists():
                detach_all(host, backing)
        except DiskError as exc:
            problems.append(exc.message)
    if not problems:
        cow.unlink(missing_ok=True)
        if cow.exists():
            problems.append(f"{cow.name} לא נמחק")
    return problems


def clear_leftovers(host: Host, workdir: Path) -> None:
    """‏#1649: שאריות של שמירה שנקטעה (אתחול השרת באמצע כיווץ) — העותק
    `imagesrv-vmsave`, ה-loop של `disk.raw`, ה-loop של `run/shrink.cow` והקובץ
    עצמו. מזהים **לפי השם והנתיב** (`dmsetup ls`, ‏`losetup -j`) — לא נוגעים
    בדיסקים אחרים. ‏`_release_view` מסיר וקורא בחזרה כל אחד; מה שנשאר, או
    כלי שלא רץ (‏OSError) = ‏DiskError בשם, לא "אין מה לנקות" (עיקרון 5)."""
    try:
        left = _release_view(host, workdir / "disk.raw", workdir / "run" / "shrink.cow")
    except OSError as exc:
        raise DiskError("loop", f"לא ניתן לנקות שאריות של שמירה קודמת — כלי לא רץ: {exc}") from exc
    if left:
        raise DiskError("loop", "שאריות משמירה קודמת לא נוקו: " + "; ".join(left))


@contextmanager
def shrink_view(host: Host, disk: Path, need: int, run_dir: Path):
    """עותק copy-on-write של `disk` — ‏`/dev/mapper/imagesrv-vmsave` — שכל
    כתיבה אליו נוחתת ב-`run/shrink.cow` (דליל, בגודל `need`), ולא בדיסק.
    לפני שנוצר דבר: המקום הפנוי (‏`disk_space` כשחסר). ביציאה — גם בכשל —
    הכול מוסר ונקרא בחזרה; מה שנשאר = ‏DiskError("loop")."""
    free = host.free_bytes(run_dir)
    if free is None:
        raise DiskError("unchecked", "לא ניתן לקרוא כמה מקום פנוי לעותק הזמני של הכיווץ")
    if free < need:
        raise DiskError("disk_space", f"אין מקום לעותק הזמני של הכיווץ: נדרשים {need / 10**9:.1f}GB, "
                                      f"פנויים {free / 10**9:.1f}GB — דיסק ה-VM לא נכתב")
    cow = run_dir / "shrink.cow"
    stale = _release_view(host, disk, cow)       # שמירה קודמת שמתה באמצע
    if stale:
        raise DiskError("loop", "עותק כיווץ משמירה קודמת לא שוחרר: " + "; ".join(stale))
    with cow.open("xb") as fh:
        fh.truncate(need)
    os.chmod(cow, 0o600)
    try:
        origin = host.run(["losetup", "--find", "--show", "--read-only", str(disk)])
        origin_node = _loop_name(origin.stdout) if origin.returncode == 0 else None
        if origin_node is None:
            raise DiskError("loop", f"לא ניתן לחבר את דיסק ה-VM לקריאה בלבד (rc={origin.returncode})")
        ro = host.run(["blockdev", "--getro", f"/dev/{origin_node}"])
        if ro.returncode != 0 or ro.stdout.strip() != "1":
            raise DiskError("loop", "התקן ה-loop של דיסק ה-VM לא אושר כקריאה בלבד — לא מכווצים")
        store = host.run(["losetup", "--find", "--show", str(cow)])
        store_node = _loop_name(store.stdout) if store.returncode == 0 else None
        if store_node is None:
            raise DiskError("loop", f"לא ניתן לחבר את העותק הזמני (rc={store.returncode})")
        sectors = disk.stat().st_size // SECTOR
        made = host.run(["dmsetup", "create", VIEW_NAME, "--table",
                         f"0 {sectors} snapshot /dev/{origin_node} /dev/{store_node} N {VIEW_CHUNK}"])
        view = f"/dev/mapper/{VIEW_NAME}"
        size = host.run(["blockdev", "--getsize64", view])
        if (made.returncode != 0 or size.returncode != 0
                or size.stdout.strip() != str(sectors * SECTOR) or not view_valid(host)):
            raise DiskError("loop", f"העותק של דיסק ה-VM לא נוצר או לא נקרא בחזרה "
                                    f"(dmsetup rc={made.returncode}): {made.stderr.strip()}")
        yield view
    finally:
        left = _release_view(host, disk, cow)
    if left:
        raise DiskError("loop", "העותק הזמני של הכיווץ לא שוחרר: " + "; ".join(left))


#: ‏#1535/#1610: משתמש ה-VM מגיע לאזור העבודה **בשמו בלבד** (ACL של ה-uid;
#: ‏`agent_shell.grant_user`). ‏root נשאר הבעלים (הוא כותב כאן, ולכן אסור
#: שמשתמש ה-VM ישתול symlink), והקבוצה ו"אחרים" — אפס.
#: תיקייה: מעבר בלבד (‏`[ -f "$ESPROOT$p" ]` על נתיבים ידועים; אין רשימה).
VM_PASS = agent_shell.ACL_X
#: קובץ אתחול שהורד: קריאה, לחתימות של `boot_ca_json`.
VM_READ = agent_shell.ACL_R


def boot_files(box: "Appliance", esp: int, paths: list[str], run_dir: Path, uid: int) -> Path:
    """קובצי האתחול של ה-ESP, בתיקייה **חדשה של root** (‏mkdtemp) — root
    כותב רק לשם, ואיש אחר אינו יכול לשתול בה symlink. אחרי ההורדה היא
    נפתחת **למשתמש ה-VM בלבד** (בעלות root נשארת), והוא קורא ממנה
    את החתימות."""
    target = Path(tempfile.mkdtemp(prefix="esp-", dir=run_dir))
    box.fetch(esp, paths, target)
    for folder, _dirs, files in os.walk(target, followlinks=False):
        agent_shell.grant_user(folder, uid, VM_PASS)
        for name in files:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                agent_shell.grant_user(path, uid, VM_READ)
    return target


def _clear_stale(workdir: Path, run_dir: Path) -> None:
    """שאריות של שמירה קודמת שמשתמש ה-VM החזיק — נמחקות (‏rmtree אינו עוקב
    אחרי symlinks) לפני ש-root כותב משהו בסביבה."""
    stale = workdir / "unpriv"
    if stale.is_symlink():
        stale.unlink()
    else:
        shutil.rmtree(stale, ignore_errors=True)
    for child in (run_dir.iterdir() if run_dir.exists() else []):
        if child.name.startswith(("u-", "esp-")):
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child, ignore_errors=True)


def _boot_paths(host: Host, run_dir: Path) -> list[str]:
    """`BOOTCA_PATHS` של `agent/lib/postdeploy/bootca.sh` — אותה רשימה, לא עותק."""
    got = host.agent("eval", 'printf "%s\\n" "$BOOTCA_PATHS"', run_dir=run_dir)
    return [p for p in got.out.splitlines() if BOOT_PATH_RE.fullmatch(p)]


def capture_disk(host: Host, workdir: Path, out_dir: Path, progress: Progress,
                 on_partition: Callable[[int, int], None] | None = None, *,
                 user: VmUser, appliance: Callable[..., "Appliance"] = Appliance) -> dict:
    """קולט את `workdir/disk.raw` ל-`out_dir` — מחזיר את חלק המניפסט שנגזר
    מהדיסק. הכיווץ נעשה על עותק (`shrink_view`) — ‏`disk.raw` אינו נכתב (‏#1491)."""
    disk = workdir / "disk.raw"
    run_dir = workdir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    # ‏root בלבד כותב כאן (משתמש ה-VM מקבל תת-תיקיות חדשות בלבד, ‏fresh_user_dir),
    # בלי תלות ב-umask של התהליך. משתמש ה-VM רק עובר כאן (‏#1535) — ‏QEMU פותח
    # את הדיסק כ-root לפני `-run-with`, והשמירה רצה כשהוא כבוי.
    for folder in (workdir, run_dir):
        if hasattr(os, "setxattr"):
            agent_shell.grant_user(folder, user.uid, VM_PASS)
    _clear_stale(workdir, run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source = json.loads((workdir / "source-manifest.json").read_text(encoding="utf-8"))

    # 1. ה-appliance, לקריאה בלבד: אימות, תוכנית הכיווץ, נפח, קובצי אתחול.
    box = appliance(disk)
    try:
        disk_guid, table = box.table()
        fs_of = {p["index"]: box.fs(p["index"]) for p in table}
        check_against_source(source, disk_guid, table, fs_of)
        for part in table:
            if box.bitlocker(part["index"], fs_of[part["index"]]):
                raise DiskError("bitlocker", f"מחיצה {part['index']} מוצפנת ב-BitLocker — "
                                             "כבו את BitLocker ב-VM ושמרו שוב")
            if fs_of[part["index"]] == "ntfs":
                rc = box.hibernation(part["index"])
                if rc in (14, 15):
                    # #1504 (כמו #1502 בסוכן, ברזל 16/10): עם Fast Startup כיבוי רגיל
                    # **הוא** שינה — "כיבוי מלא" לא משחרר; התיקון שעובד הוא powercfg.
                    raise DiskError("hibernated", f"חלונות במחיצה {part['index']} נכבה ב-Fast "
                                                  "Startup — גם כיבוי רגיל משאיר את הדיסק נעול. "
                                                  "הדליקו את ה-VM, הריצו בו כמנהל powercfg /h off, "
                                                  "כבו ושמרו שוב")
                if rc != 0:
                    raise DiskError("unchecked", f"לא ניתן לבדוק מצב שינה במחיצה "
                                                 f"{part['index']} (ntfs-3g.probe rc={rc})")
        # הנפח נמדד **לפני** הכיווץ: אחרי ntfsresize הווליום מסומן לבדיקה,
        # ועגינה לקריאה שלו עלולה להיכשל. הכיווץ אינו משנה כמה תפוס.
        used = {p["index"]: box.used_bytes(p["index"]) for p in table
                if fs_of[p["index"]] not in ("swap", "unknown")}
        parts_path = run_dir / "parts.txt"
        fails = run_dir / "uguid.fail"
        fails.write_text("".join(f"{p['index']}|2|no unique GUID\n" for p in table
                                 if not p["unique_guid"]), encoding="utf-8", newline="\n")
        _parts_file(parts_path, table)
        gate = host.agent("unique_guid_gate", str(parts_path), str(fails), run_dir=run_dir)
        if gate.out.strip():
            raise DiskError("table", gate.out.strip())

        # כיווץ — אותם מועמדים ואותו יעד כמו `shrink_plan`: מחיצות windows/linux
        # שהן NTFS, לפי start_sector; היעד מ-`_shrink_target` של הסוכן.
        mark = run_dir / "shrunk.mark"
        mark.write_text("", encoding="utf-8")
        original = [dict(p) for p in table]
        shrunk: list[int] = []
        measured = run_dir / "shrink.meas"
        candidates = []
        for part in sorted(table, key=lambda p: p["first"]):
            if partition_role(part["type_guid"]) not in ("windows", "linux"):
                continue
            if fs_of[part["index"]] != "ntfs":
                continue
            current = part["sectors"] * SECTOR
            minimum = box.min_size(part["index"])
            target = host.agent("_shrink_target", str(minimum), str(current), run_dir=run_dir)
            new = target.out.strip()
            if not target.ok or not new.isdigit():
                raise DiskError("shrink", f"לא ניתן לחשב יעד כיווץ למחיצה {part['index']}")
            candidates.append((part, current, minimum, int(new)))
        measured.write_text("".join(
            f"{part['index']}|{part['type_guid']}|{part['unique_guid']}|{part['first']}|"
            f"{part['sectors']}|{current}|{new}|{minimum}\n"
            for part, current, minimum, new in candidates), encoding="utf-8", newline="\n")
        layout_end = max((p["first"] + p["sectors"]) * SECTOR for p in table)
        slack = max(0, disk.stat().st_size - layout_end)
        tail = host.agent("_shrink_tail", str(measured), str(slack), run_dir=run_dir)
        if not tail.ok:
            raise DiskError("shrink", "לא ניתן לחשב את זנב הפריסה לשמירה מה-VM")
        planned = {}
        for line in tail.out.splitlines():
            fields = line.split("|")
            if len(fields) != 7 or not fields[0].isdigit() or not fields[6].isdigit():
                raise DiskError("shrink", "תוכנית זנב הפריסה של הסוכן אינה תקינה")
            planned[int(fields[0])] = int(fields[6])
        todo = [(part, planned.get(part["index"], current))
                for part, current, _minimum, _ordinary in candidates
                if planned.get(part["index"], current) < current]
        esp = next((p for p in table if partition_role(p["type_guid"]) == "esp"), None)
        boot_dir = (boot_files(box, esp["index"], _boot_paths(host, run_dir), run_dir, user.uid)
                    if esp is not None else None)
    finally:
        box.close()

    # ‏#1649: שאריות משמירה שנקטעה (עותק, ‏loop-ים, ‏shrink.cow) מנקים **לפני** כל
    # שימוש בדיסק — גם כשאין מה לכווץ, כי ה-`detach_all` שבסוף נכשל על loop
    # שהעותק הישן עוד מחזיק. אחרי האימות: לפני שהוא עבר, המארח אינו נגוע.
    clear_leftovers(host, workdir)

    parts: list[dict] = []
    total = 0
    views = ExitStack()
    try:
        # 2. הכיווץ — על העותק בלבד (‏#1491). ‏`reads` הוא מה ש-ntfsfix ו-partclone
        # פותחים; בלי כיווץ אין עותק, ו-partclone קורא את הדיסק לקריאה בלבד.
        reads: Path | str = disk
        if todo:
            need = view_budget(host, disk, todo, user, run_dir)
            reads = views.enter_context(shrink_view(host, disk, need, run_dir))
            shrinker = appliance(Path(reads), writable=True)
            try:
                for part, new in todo:
                    shrinker.shrink(part["index"], part["first"], new, part)
                    with mark.open("a", encoding="utf-8", newline="\n") as fh:
                        fh.write(f"{part['index']}|{part['first']}|{part['sectors']}|"
                                 f"{new // SECTOR}|{part['type_guid']}|{part['unique_guid']}||"
                                 f"{part['attrs']}\n")
                    part["sectors"] = new // SECTOR
                    shrunk.append(part["index"])
            finally:
                shrinker.close()
            if not view_valid(host):
                raise DiskError("shrink", "העותק של דיסק ה-VM אינו תקין אחרי הכיווץ (המקום הזמני "
                                          "התמלא?) — דיסק ה-VM לא נכתב")

        # 3. הפריסה המצומצמת — `_shrink_compact`/`shrink_json_extra` של הסוכן, על
        # קבצים שלנו בלבד (מספרים שאומתו). ‏parts.orig.txt = לפני הכיווץ.
        on_disk = {p["index"]: dict(p) for p in table}
        if shrunk:
            orig = run_dir / "parts.orig.txt"
            _parts_file(orig, original)
            compact = host.agent("_shrink_compact", str(mark), str(orig), run_dir=run_dir)
            if not compact.ok or not compact.out.strip():
                raise DiskError("shrink", "לא ניתן לחשב את הפריסה המצומצמת")
            parts_path.write_text(compact.out, encoding="utf-8", newline="\n")
        else:
            (run_dir / "parts.orig.txt").unlink(missing_ok=True)
            _parts_file(parts_path, table)
        rows = _read_parts_file(parts_path)

        # 4. הזרם — כמשתמש ה-VM, על loop של מחיצה אחת.
        for count, row in enumerate(rows, 1):
            idx = row["index"]
            fs = fs_of[idx]
            real = on_disk[idx]
            role = "swap" if fs == "swap" else partition_role(row["type_guid"])
            extra = _json_fragment(host.agent("shrink_json_extra", str(idx), str(row["first"]),
                                              str(row["sectors"]), str(SECTOR),
                                              run_dir=run_dir).out)
            entry = {"index": idx, "type_guid": row["type_guid"],
                     "unique_guid": row["unique_guid"], "attrs": real["attrs"], "role": role,
                     "fs": fs, "start_sector": row["first"],
                     "size_bytes": row["sectors"] * SECTOR}
            if on_partition:
                on_partition(count, len(rows))
            if role == "swap":
                entry.update({"uuid": next((p.get("uuid") for p in source["partitions"]
                                            if p["index"] == idx), None),
                              "used_bytes": 0, "file": None, "sha256": None,
                              "expandable": False, **extra})
                parts.append(entry)
                continue
            if idx in shrunk:
                with partition_loop(host, reads, real, user, writable=True) as node:
                    fixed = host.run(as_user(user, ["ntfsfix", "-d", node]))
                if fixed.returncode != 0:
                    raise DiskError("shrink", f"דגל ה-dirty לא נוקה אחרי כיווץ מחיצה {idx} "
                                              f"(ntfsfix -d rc={fixed.returncode})")
                if not view_valid(host):
                    raise DiskError("shrink", f"העותק של מחיצה {idx} אינו תקין אחרי ntfsfix -d "
                                              "(המקום הזמני התמלא?) — דיסק ה-VM לא נכתב")
            name = f"p{idx}.{role}.pcl.zst"
            with partition_loop(host, reads, real, user, writable=False) as node:
                sha, size = capture_partition(host, fs, node, out_dir / name, run_dir, idx,
                                              progress, user=user, err_dir=run_dir,
                                              limit=real["sectors"] * SECTOR + STREAM_SLACK)
            total += size
            entry.update({"used_bytes": used.get(idx), "file": name, "sha256": sha,
                          "expandable": False, **extra})
            parts.append(entry)
    finally:
        try:
            views.close()          # העותק משוחרר לפני ה-loop של המקור שהוא מחזיק
        finally:
            detach_all(host, disk)
    mark_expandable(parts)

    # 5. ‏boot_ca — `boot_ca_json` של הסוכן, כמשתמש ה-VM, על הקבצים שהורדו.
    boot: dict = {}
    if boot_dir is not None:
        got = host.agent("boot_ca_json", "esp", run_dir=run_dir,
                         env={"ESPROOT": str(boot_dir)}, user=user)
        if got.out.strip().startswith('"boot_ca"'):
            boot = _json_fragment(got.out)
    if "boot_ca" not in boot:
        boot = {"boot_ca": None, "boot_ca_error": "החותם לא נגזר בשמירה הזו"}
    disk_bytes = disk.stat().st_size
    return {
        "family": disk_family(disk_bytes),
        "os": image_os({"partitions": parts}),
        **boot,
        "source_disk_bytes": disk_bytes,
        "scheme": "gpt",
        "sector_size": SECTOR,
        "disk_guid": disk_guid,
        "partitions": parts,
        "total_compressed_bytes": total,
        "compression": f"zstd-{CAPTURE_LEVEL}",
    }


def _parts_file(path: Path, parts: list[dict]) -> None:
    """`idx|guid|uguid|first|size` — הקובץ ש-`capture.sh` בונה (`$_parts`)."""
    path.write_text("".join(
        f"{p['index']}|{p['type_guid']}|{p['unique_guid']}|{p['first']}|{p['sectors']}\n"
        for p in parts), encoding="utf-8", newline="\n")

def _read_parts_file(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        idx, guid, uguid, first, size = line.split("|")[:5]
        rows.append({"index": int(idx), "type_guid": guid, "unique_guid": uguid,
                     "first": int(first), "sectors": int(size)})
    return rows

def _json_fragment(text: str) -> dict:
    """`"a":1,"b":null` או `,"a":1` → dict. ריק → {}."""
    body = text.strip().strip(",")
    return json.loads("{" + body + "}") if body else {}


def compose_manifest(source: dict, captured: dict, *, new_id: str, name: str,
                     description: str, user: str, now: str) -> dict:
    """המניפסט של הגרסה החדשה: מה שנקרא מהדיסק + מה שעובר מהמקור."""
    version = version_of(source)
    manifest = {"schema": 1, "id": new_id, "name": name, "description": description,
                "folder": source.get("folder", ""), "created": now, "created_by": user,
                **captured, "target_floor_bytes": None,
                "version": version + 1, "previous_id": source.get("id"),
                # ‏#1334: השושלת — מזהה הגרסה הראשונה; ‏origin — איך נוצרה הגרסה.
                "lineage": lineage_of(source), "origin": "vm"}
    owner = bound_mac(source)
    if owner is not None:
        # הקשירה (#381) עוברת לגרסה — גרסה חדשה של אימג' שקשור למכונה אינה
        # הופכת לחופשית רק כי עודכנה ב-VM.
        manifest[MACHINE_MAC] = owner
    manifest["min_target_bytes"] = required_bytes(manifest)
    return manifest
