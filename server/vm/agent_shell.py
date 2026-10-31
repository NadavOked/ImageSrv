"""גשר לפונקציות של הסוכן — השרת מריץ את `agent/lib/*.sh` עצמם (‏#1334).

עדכון אימג' ב-VM (`server/vm/vmupdate.py`) צריך לבנות טבלת מחיצות מהמניפסט,
למתוח את מחיצת Windows, לכווץ ולגזור `boot_ca` — בדיוק מה שהסוכן כבר עושה
בשחזור ובקליטה, ומה שכל שורה בו נולדה מבאג על ברזל (#26, #51, #58, #424,
#571, #929, #1212). עותק Python של הלוגיקה הזו היה **פורמט שני**, שהיה
נסחף מהראשון בשקט. לכן השרת אינו מעתיק אותה: הוא טוען את קובצי הסוכן
ב-`sh` ומפעיל פונקציה אחת בכל קריאה, מול התקן loop.

**מה הגשר מוסיף על הסוכן, ותו לא:**

* ‏`log` נדרס **אחרי** הטעינה כך שיכתוב רק ל-`$LOG_FILE`. ב-`common.sh`
  הוא מדפיס גם ל-stdout, ופונקציה שמחזירה ערך ב-stdout (‏`_shrink_entry`,
  ‏`boot_ca_json`) הייתה מחזירה אותו מעורבב בשורות יומן.
* משתני השגיאה שהסוכן ממלא (‏`PLAN_ERROR`, ‏`GROW_ERROR`, ‏`SHRINK_ERROR`,
  ‏`BOOTCA_ERROR`) נכתבים לקובץ ונקראים בחזרה — הם הסיבה בשמה, וקוד יציאה
  לבדו אינו אומר לטכנאי מה נפל (עיקרון 4).
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from server.paths import AGENT_LIB

#: ‏#1610: מעבר/קריאה למשתמש ה-VM **בשמו** (ACL), לא דרך קבוצה ולא דרך
#: `os.chmod`. ‏CodeQL (‏`py/overly-permissive-file`) מסמן כל ביט שאינו של
#: הבעלים — גם 0o710 — וההערה `codeql[...]` אינה סוגרת התראה בשער (נמדד
#: 17/10: ‏#341 נשארה פתוחה). ‏ACL עם ערך אחד בשם ה-uid הוא גם **מינימלי
#: יותר** מקבוצה: חבר אחר בקבוצה של משתמש ה-VM אינו עובר.
ACL_X, ACL_R = 1, 4
_ACL_XATTR = "system.posix_acl_access"
_ACL_USER_OBJ, _ACL_USER, _ACL_GROUP_OBJ, _ACL_MASK, _ACL_OTHER = 0x01, 0x02, 0x04, 0x10, 0x20
_ACL_NO_ID = 0xFFFFFFFF


def grant_user(path: str | Path, uid: int, perm: int) -> None:
    """`uid` (בלבד) מקבל `perm` (‏`ACL_X`/`ACL_R`) על `path`. הבעלים והקבוצה
    הבעלת נשארים כפי שהם, והקבוצה ו"אחרים" — אפס. ביטי הבעלים נשמרים.

    כשל — ‏OSError עם הסיבה, לא הרשאה רחבה יותר בשקט (עיקרון 5): מערכת קבצים
    בלי ACL אינה ממשיכה בלי הגנה."""
    owner = (os.lstat(path).st_mode >> 6) & 7
    entries = [(_ACL_USER_OBJ, owner, _ACL_NO_ID), (_ACL_USER, perm, uid),
               (_ACL_GROUP_OBJ, 0, _ACL_NO_ID), (_ACL_MASK, perm, _ACL_NO_ID),
               (_ACL_OTHER, 0, _ACL_NO_ID)]
    blob = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)
    try:
        os.setxattr(path, _ACL_XATTR, blob, follow_symlinks=False)
    except OSError as exc:
        raise OSError(exc.errno, f"{path}: לא ניתן לתת למשתמש {uid} גישה (ACL): {exc.strerror}") from exc

#: מה שנטען, בסדר הזה. אותה רשימה ש-`tests/test_restore_evidence.py`
#: טוענת מול הזיופים, ועוד ארבעת קובצי הקליטה. אף אחד מהם אינו מריץ דבר
#: בטעינה מלבד השמות ערכי ברירת מחדל (נבדק ב-`tests/test_agent_shell.py`).
LIBS = (
    "common.sh", "waits.sh", "jsonq.sh", "progress.sh", "restore.sh",
    "expand.sh", "grow.sh", "ntfsunhib.sh", "failmark.sh", "diskfit.sh", "manifest.sh",
    "hibernation.sh", "shrinkplan.sh", "shrink.sh", "bootca.sh",
    "lcsnap.sh",    # ‏#1491: ‏`_lc_relocs` — כמה `ntfsresize -n` אומר שיוזז
)

#: משתני הסיבה שהסוכן ממלא, והגשר מחזיר.
ERROR_VARS = ("PLAN_ERROR", "GROW_ERROR", "SHRINK_ERROR", "BOOTCA_ERROR")

_VARS_FILE = ".bridge-vars"

_SCRIPT = """\
set +e
for _lib in {libs}; do
    . "$_lib" || {{ echo "bridge: could not load $_lib" >&2; exit 97; }}
done
log() {{ printf '[%s] %s\\n' "$(date -u +%H:%M:%S)" "$*" >> "$LOG_FILE"; }}
_bridge_fn=$1; shift
"$_bridge_fn" "$@"
_bridge_rc=$?
{{ {dump} }} > "$RUN_DIR/{vars}"
exit "$_bridge_rc"
"""


def lib_path(name: str) -> Path:
    """הנתיב של קובץ סוכן לפי שמו. הריפו שומר אותם בתיקיות נושא (#2239); רק
    ה-initramfs שטוח, והגשר טוען מהריפו — לכן מחפשים את השם, ושם כפול או חסר
    נכשל בקול במקום לטעון קובץ אחר."""
    hits = [p for p in AGENT_LIB.rglob(name) if p.is_file()]
    if len(hits) != 1:
        raise RuntimeError(f"agent library {name}: expected one file under {AGENT_LIB}, found {len(hits)}")
    return hits[0]


def script() -> str:
    """גוף ה-`sh -c`: טעינה, דריסת log, הפונקציה, והחזרת משתני הסיבה."""
    dump = " ".join(
        f"printf '{name}=%s\\n' \"$(printf '%s' \"${{{name}:-}}\" | tr '\\n' ' ')\";"
        for name in ERROR_VARS)
    libs = " ".join(shlex.quote(lib_path(name).as_posix()) for name in LIBS)
    return _SCRIPT.format(libs=libs, dump=dump, vars=_VARS_FILE)


@dataclass(frozen=True)
class Call:
    """תוצאת קריאה אחת לפונקציה של הסוכן."""

    rc: int
    out: str
    err: str
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def reason(self, fallback: str) -> str:
        """הסיבה שהסוכן נתן, או `fallback` — לעולם לא מחרוזת ריקה."""
        for name in ERROR_VARS:
            if self.errors.get(name):
                return self.errors[name]
        return fallback


def _env(run_dir: Path, extra: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    path = env.get("PATH", "")
    # ‏sgdisk/losetup/blkid/partclone יושבים ב-sbin. השירות רץ כ-root ושם
    # הם ב-PATH; ‏`sudo` בבדיקות מאפס PATH ל-secure_path, שגם בו הם.
    # מוסיפים בכל זאת: כלי שלא נמצא הוא 127 שנראה כמו "הטבלה נכשלה".
    for extra_dir in ("/usr/sbin", "/sbin"):
        if extra_dir not in path.split(os.pathsep):
            path = f"{path}{os.pathsep}{extra_dir}" if path else extra_dir
    env.update({
        "PATH": path,
        "RUN_DIR": str(run_dir),
        "DEVROOT": "/dev",
        "SYSROOT": "",
    })
    env.update(extra or {})
    return env


#: תקרה לקריאה אחת. הפונקציות הארוכות (‏ntfsresize בהגדלה) מסתיימות בדקות.
CALL_TIMEOUT = 3 * 3600


def fresh_user_dir(parent: Path, uid: int, gid: int) -> Path:
    """תיקייה **חדשה** שמשתמש ה-VM כותב אליה — בלי ש-root ייגע בשום נתיב
    שהמשתמש יכול לשנות.

    ‏`chown` כ-root על נתיב בתוך תיקייה שהמשתמש כותב אליה הוא העלאת הרשאות:
    הוא מחליף את הנתיב ב-symlink ל-`/etc/shadow` לפני ה-chown. לכן: ההורה
    חייב להיות תיקייה אמיתית (lstat, לא symlink) של root שאיש אחר אינו כותב
    אליה; הילד נוצר ב-`mkdtemp` (שם ייחודי, ‏0700, אטומי); ה-chown נעשה על
    ה-fd שנפתח ב-`O_NOFOLLOW|O_DIRECTORY` — לא על נתיב."""
    parent = Path(parent)
    if not parent.exists():
        # ‏#1535/#1610: המשתמש רק **עובר** דרך ההורה אל הילד שלו — בשמו (ACL),
        # לא כ"אחרים" ולא לפי ה-umask. ‏root נשאר הבעלים.
        parent.mkdir(parents=True, mode=0o700)
        grant_user(parent, uid, ACL_X)
    st = os.lstat(parent)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        raise PermissionError(f"{parent}: not a root-only directory -- refusing to hand out a child")
    path = Path(tempfile.mkdtemp(prefix="u-", dir=parent))
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchown(fd, uid, gid)
    finally:
        os.close(fd)
    return path


def _read_vars(path: Path) -> dict[str, str]:
    """קובץ הסיבות — בלי לעקוב אחרי symlink (המשתמש כתב את התיקייה)."""
    errors: dict[str, str] = {}
    # ‏O_NONBLOCK: ‏FIFO שנשתל במקום הקובץ היה חוסם את ה-open לנצח (תהליכון
    # של השרת תקוע). נפתח בלי לחסום, נבדק שהוא קובץ רגיל, ורק אז נקרא.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return errors     # הפונקציה לא הגיעה לסוף, או symlink — rc מספר את זה
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return errors
        if hasattr(os, "set_blocking"):
            os.set_blocking(fd, True)
        text = os.read(fd, 64 * 1024).decode("utf-8", "replace")
    finally:
        os.close(fd)
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ERROR_VARS and value.strip():
            errors[key] = value.strip()
    return errors


def call(func: str, *args: str, run_dir: str | Path, env: dict[str, str] | None = None,
         timeout: float | None = CALL_TIMEOUT, shell: str = "sh",
         as_user: tuple[int, int] | None = None, runner=subprocess.run) -> Call:
    """מפעיל את `func` של הסוכן עם `args`. ‏`run_dir` הוא ה-RUN_DIR שלה
    (שם היומן `agent.log` והקבצים הזמניים), ונשמר בין קריאות — ‏`expand_last`
    משאיר סימון ש-`grow_expanded` קורא.

    **הארגומנטים עוברים כפרמטרים מיקומיים** (`"$@"`) ולא משורשרים לסקריפט —
    ערך שנקרא מהדיסק אינו יכול להפוך לפקודה. ‏`as_user=(uid, gid)` — כמשתמש
    ה-VM (‏setpriv, ‏no-new-privs), למה שמפרסר קלט של האורח (#1334); אז
    `run_dir` הוא **הורה** של root, ובתוכו נוצרת תיקייה חדשה לקריאה הזו
    (`fresh_user_dir`) ונמחקת אחריה."""
    prefix: list[str] = []
    scratch: Path | None = None
    if as_user is None:
        run = Path(run_dir)
        run.mkdir(parents=True, exist_ok=True)
    else:
        uid, gid = as_user
        run = scratch = fresh_user_dir(Path(run_dir), uid, gid)
        prefix = ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups",
                  "--no-new-privs", "--"]
    vars_file = run / _VARS_FILE
    try:
        if scratch is None:
            vars_file.unlink(missing_ok=True)
        proc = runner(
            [*prefix, shell, "-c", script(), "sh", func, *[str(a) for a in args]],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=_env(run, env), stdin=subprocess.DEVNULL, timeout=timeout,
        )
        errors = _read_vars(vars_file)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)   # ‏rmtree אינו עוקב אחרי symlinks
    return Call(proc.returncode, proc.stdout, proc.stderr, errors)
