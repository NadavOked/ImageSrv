"""עדכון השרת מול הריפו הציבורי (‏#748).

הכרעות נדב (16/09, תגובה אחרונה ב-#748): המתג **כבוי בברירת מחדל**,
בדיקת-עדכון היא מול הריפו **הציבורי** בלבד (לא הפרטי — שם אין CI),
המנהל בוחר אם לעדכן פר-עדכון, והחיבור היוצא נפתח **רק** בזמן הבדיקה/
העדכון עצמם — לא חיבור קבוע.

מקור הגרסה החדשה: `tag` אחרון ב-remote (‏`git ls-remote --tags`), לא
release API — הריפו הציבורי עדיין בלי releases. גרסה נוכחית: תג semver
מאומת שמצביע על HEAD, עם fallback מסומן ל-`git describe --tags` בהתקנה
ישנה (`--repo-dir`, ברירת מחדל תיקיית הקוד עצמה).

ההגדרה (`update_enabled`) קובעת רק מה *מותר
לנסות*; המצב בפועל (הגרסה שרצה) נקרא תמיד מחדש, לא מונח.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from .db import get_setting, journal, now_iso, set_setting

log = logging.getLogger("imagesrv.update")

Hooks = dict[str, Callable]
_DISPLAY_VERSION_CACHE: dict[tuple[str, str], "DisplayVersion"] = {}

#: מפתח המתג ב-``WRITE_SETTINGS`` (console_api.py) — כבוי כברירת מחדל,
#: כמו כל דגל אחר שם: ``get_setting`` שמחזיר ``None`` נקרא "כבוי".
ENABLED_KEY = "update_enabled"
#: התג הקודם, נשמר לפני כל ``apply`` — כדי ש"חזור לגרסה הקודמת" לא
#: יצטרך לזכור אותו בעצמו.
PREVIOUS_KEY = "update_previous"
#: מצב הרצת העדכון האחרון — נשרד את ה-restart כי הוא ב-DB, לא בזיכרון.
STATUS_KEY = "update_status"
#: ‏#1000: תוצאת בדיקת-העדכון האחרונה ``{at, current, latest, available,
#: reason}`` — נכתבת ב-``POST /update/check`` ומוחזרת ב-``GET /update`` כ-
#: ``last_check``, כדי שהדף יציג "בדיקה אחרונה: <זמן השרת> — <תוצאה>" גם
#: אחרי רענון, ולא "לא נבדק בסשן הזה" משעון הדפדפן.
LAST_CHECK_KEY = "update_last_check"

#: הריפו הציבורי — הוא שער ה-CI (CLAUDE.md, "ארבעת הריפואים"). הבדיקה
#: אף פעם אינה מול הפרטי: שם `tests` לא רץ ואין ראיה שהתג ירוק.
PUBLIC_UPDATE_URL = "https://github.com/NadavOked/ImageSrv.git"
NO_NETWORK_MESSAGE = (
    "השרת לא הצליח להגיע ל-github.com בפורט 443 (חומת אש או DNS). "
    "כדי לעדכן, בקשו לפתוח בחומת האש של המכללה: השרת ← github.com:443."
)

_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_REMOTE_TAG_RE = re.compile(r"refs/tags/(v\d+\.\d+\.\d+)(\^\{\})?$")


def semver_key(tag: str) -> tuple[int, int, int] | None:
    m = _TAG_RE.match(tag)
    return tuple(int(g) for g in m.groups()) if m else None


def _base_version(describe_output: str | None) -> str | None:
    """‏`git describe --tags` מחזיר לפעמים `v0.24.0-3-gabc123` (עצי עבודה
    שאינם *בדיוק* על תג) — משווים לפי התג הבסיסי, לא למחרוזת כולה."""
    if not describe_output:
        return None
    m = re.match(r"^(v\d+\.\d+\.\d+)", describe_output.strip())
    return m.group(1) if m else None


def parse_remote_tags(ls_remote_output: str) -> list[str]:
    """‏`vX.Y.Z` בלבד, ממוינים מהישן לחדש. שורות `^{}` (תג מוער שהוצמד)
    ושורות שאינן תואמות תבנית semver נבדלות — לא כל שורה ב-ls-remote
    היא תג-מוצר."""
    names = set()
    for line in ls_remote_output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        m = _REMOTE_TAG_RE.search(parts[1])
        if m:
            names.add(m.group(1))
    return sorted(names, key=semver_key)


# --- הרצת פקודות בפועל — כולן מוזרקות, כדי שאף בדיקה לא תיגע ברשת/git -------


def _run(cmd: list[str], timeout: int, cwd: str | Path | None = None) -> tuple[bool, str, str]:
    """‏(הרצה עצמה הצליחה, stdout, stderr). קוד יציאה שאינו 0, וגם חריגה
    בהרצה עצמה, שניהם "לא הצלחנו" — לא "אין תוצאה" (עיקרון 5)."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              check=False, cwd=str(cwd) if cwd else None,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        # ‏#1185: "[Errno 2] No such file or directory: 'git'" הוא הניחוש של
        # המכונה על עצמה — למנהל זה אומר כלום. הכלי חסר, בשמו.
        return False, "", f"{cmd[0]} {_NOT_INSTALLED} — הותקן מ-ISO ישן (#1185)? התקנה מחדש מ-ISO עדכני"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", str(exc)
    if done.returncode != 0:
        return False, done.stdout, (done.stderr or "").strip()[:300]
    return True, done.stdout, ""


_NOT_INSTALLED = "אינו מותקן בשרת הזה"

#: ‏#1159: הקטגוריות של ``version_error`` ב-``/health/live``. ה-endpoint הזה
#: אינו דורש הזדהות, ולכן הוא מקבל **רק** את אחת המחרוזות הקבועות האלה —
#: בלי נתיבים ובלי stderr. הסיבה המלאה הולכת ליומן ול-``GET /update``.
VERSION_ERRORS = ("git-missing", "not-a-git-tree", "dubious-ownership",
                  "no-tag", "git-failed", "not-wired")


class UpgradeLaunchError(RuntimeError):
    """‏#2160: ``systemd-run`` של סקריפט השדרוג נכשל, ולא בגלל "היחידה כבר
    רצה". הסטטוס כבר נכתב ``failed``; הנתיב מחזיר את זה כשגיאה בשם, לא 409
    "עדכון קודם עוד רץ"."""


class VersionError(RuntimeError):
    """כישלון קריאת הגרסה: ``category`` מתוך ``VERSION_ERRORS``, והסיבה
    המלאה (עם נתיב ו-stderr) ב-``str(exc)``."""

    def __init__(self, category: str, detail: str):
        super().__init__(detail)
        self.category = category


def _describe_error(err: str) -> str:
    if _NOT_INSTALLED in err:
        return "git-missing"
    low = err.lower()
    if "not a git repository" in low:
        return "not-a-git-tree"
    if "dubious ownership" in low:
        return "dubious-ownership"
    if "no names found" in low or "no tags can describe" in low or "cannot describe" in low:
        return "no-tag"
    return "git-failed"


def _describe(repo_dir: str | Path) -> str:
    """‏#1159: כישלון זורק ``VersionError`` — קטגוריה + הסיבה של git.
    ‏``current_version`` מקפל אותו ל-``None`` כמו קודם."""
    ok, out, err = _run(["git", "-C", str(repo_dir), "describe", "--tags"], timeout=10)
    if not ok:
        raise VersionError(_describe_error(err),
                           f"git describe --tags על {repo_dir} נכשל: {err or 'בלי פלט שגיאה'}")
    if not out.strip():
        raise VersionError("no-tag", f"git describe --tags על {repo_dir} לא החזיר תג")
    return out.strip()


def _head(repo_dir: str | Path) -> str:
    ok, out, err = _run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], timeout=10)
    if not ok or not out.strip():
        raise VersionError(_describe_error(err),
                           f"git rev-parse HEAD על {repo_dir} נכשל: {err or 'בלי פלט שגיאה'}")
    return out.strip()


def _tags_at_head(repo_dir: str | Path) -> list[str]:
    ok, out, err = _run(
        ["git", "-C", str(repo_dir), "tag", "--points-at", "HEAD"], timeout=10)
    if not ok:
        raise VersionError(_describe_error(err),
                           f"git tag --points-at HEAD על {repo_dir} נכשל: {err or 'בלי פלט שגיאה'}")
    return sorted((tag for tag in out.splitlines() if semver_key(tag)),
                  key=semver_key, reverse=True)


def _verify_installed_tag(repo_dir: str | Path, tag: str) -> bool:
    tool = Path(repo_dir) / "tools" / "verify-release-tag.py"
    signers = Path(repo_dir) / "deploy" / "release-signers"
    try:
        done = subprocess.run(
            [sys.executable, "-I", str(tool), str(repo_dir), tag, str(signers)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.startswith("signed: ")


def _ls_remote_tags(url: str) -> tuple[bool, str, str]:
    # חד-פעמי, 20 שניות: זה החיבור היוצא היחיד שהבדיקה פותחת (סעיף 4
    # בתגובת נדב) — אין polling ואין חיבור קבוע לציבורי.
    return _run(["git", "ls-remote", "--tags", url], timeout=20)


#: היחידה המנותקת שמריצה את השדרוג; ‏systemd-run שני נכשל עליה כל עוד היא חיה.
UPGRADE_UNIT = "imagesrv-upgrade"


def unit_already_exists(err: str) -> bool:
    """‏#1273: ‏systemd-run נכשל כי יחידת שדרוג **כבר רצה** — זה "עדכון רץ", לא
    "עדכון נכשל". שני הנוסחים: "already exists" (ישן) ו-"was already loaded"
    (‏systemd 257, דביאן 13). שם היחידה חייב להופיע — לא כל "already"."""
    return f"{UPGRADE_UNIT}.service" in (err or "") and "already" in (err or "")


def _run_upgrade_script(repo_dir: str | Path, tag: str) -> tuple[bool, str]:
    """מפעיל את `tools/server-upgrade.sh` **מנותק** מתהליך השרת, דרך
    `systemd-run` — כי השלב האחרון שלו הוא `systemctl restart
    imagesrv-server`, שמנהל התהליך הזה. תהליך-ילד היה נהרג יחד איתו."""
    script = Path(repo_dir) / "tools" / "server-upgrade.sh"
    ok, _out, err = _run(
        ["systemd-run", f"--unit={UPGRADE_UNIT}", "--collect",
         "bash", str(script), tag, str(repo_dir)],
        timeout=15,
    )
    return ok, err


#: ‏#1277: מצבי החתימה של תג בבדיקת-עדכון — "לא נבדק" אינו "לא חתום", ושניהם
#: אינם "חתום" (עיקרון 5). השער עצמו הוא ``tools/server-upgrade.sh``; כאן
#: המנהל רואה את התוצאה **לפני** שהוא לוחץ "החל".
SIGNATURE_STATES = ("signed", "unsigned", "unchecked")


def _verify_remote_tag(url: str, tag: str, repo_dir: str | Path) -> dict:
    """‏#1277: מוריד את התג בלבד (clone חלקי ורדוד לתיקייה זמנית — לא לעץ
    הקוד, שתהליך ה-web אינו כותב אליו, #1193) ומריץ עליו את אותו מאמת
    ש-``server-upgrade.sh`` מריץ, מול ``deploy/release-signers`` של העץ
    המותקן. קוד יציאה 0 **וגם** שורת ``signed:`` = חתום; 1 = לא חתום; כל
    השאר = לא נבדק."""
    tool = Path(repo_dir) / "tools" / "verify-release-tag.py"
    signers = Path(repo_dir) / "deploy" / "release-signers"
    with tempfile.TemporaryDirectory(prefix="imagesrv-tagcheck-") as tmp:
        git_dir = str(Path(tmp) / "tag.git")
        ok, _out, err = _run(["git", "clone", "--quiet", "--bare", "--depth=1",
                              "--filter=blob:none", "--single-branch", "--branch", tag,
                              url, git_dir], timeout=60)
        if not ok:
            return {"state": "unchecked", "detail": f"הורדת התג {tag} נכשלה: {err}"}
        try:
            done = subprocess.run([sys.executable, "-I", str(tool), git_dir, tag, str(signers)],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60, check=False,
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"state": "unchecked", "detail": f"המאמת לא רץ: {exc}"}
    out, err = done.stdout.strip(), done.stderr.strip()
    if done.returncode == 0 and out.startswith("signed: "):
        return {"state": "signed", "detail": out[len("signed: "):]}
    if done.returncode == 1:
        return {"state": "unsigned", "detail": err or out}
    return {"state": "unchecked",
            "detail": err or out or f"המאמת יצא {done.returncode} בלי פלט"}


def default_hooks() -> Hooks:
    return {
        "describe": _describe,
        "head": _head,
        "tags_at_head": _tags_at_head,
        "verify_installed_tag": _verify_installed_tag,
        "_display_version_cache": _DISPLAY_VERSION_CACHE,
        "ls_remote_tags": _ls_remote_tags,
        "run_upgrade": _run_upgrade_script,
        "verify_tag": _verify_remote_tag,
    }


# --- שכבת המידע --------------------------------------------------------------


def enabled(conn) -> bool:
    try:
        return get_setting(conn, ENABLED_KEY) == "true"
    except Exception:                                  # noqa: BLE001 — כשל סוגר
        return False


def current_version(hooks: Hooks, repo_dir: str | Path) -> str | None:
    return display_version(hooks, repo_dir).version


class DisplayVersion(NamedTuple):
    version: str | None
    verified: bool


def display_version(hooks: Hooks, repo_dir: str | Path) -> DisplayVersion:
    """Highest trusted semver tag at HEAD, cached for that commit.

    Older installations and injected test hooks do not have the new verifier
    hooks.  They deliberately retain the old ``git describe`` value, marked
    unverified.  A checkout changes HEAD and therefore misses the cache.
    """
    cache = hooks.setdefault("_display_version_cache", {})
    try:
        head = hooks["head"](repo_dir) if "head" in hooks else None
    except Exception:                                  # noqa: BLE001
        head = None
    key = (str(Path(repo_dir).resolve()), head)
    if head is not None and key in cache:
        return cache[key]
    if head is not None and "tags_at_head" in hooks and "verify_installed_tag" in hooks:
        try:
            for tag in hooks["tags_at_head"](repo_dir):
                if hooks["verify_installed_tag"](repo_dir, tag):
                    result = DisplayVersion(tag, True)
                    cache[key] = result
                    return result
        except Exception:                              # noqa: BLE001
            pass
    try:
        result = DisplayVersion(hooks["describe"](repo_dir), False)
    except Exception:                                  # noqa: BLE001
        result = DisplayVersion(None, False)
    if head is not None:
        cache[key] = result
    return result


class VersionRead(NamedTuple):
    """‏``version`` שנקרא ו-``error``/``detail`` שניהם ``None``; או ``version``
    ‏``None`` עם ``error`` (קטגוריה קבועה, ל-endpoint ללא הזדהות) ו-``detail``
    (הסיבה המלאה, ליומן ולמנהל)."""
    version: str | None
    error: str | None
    detail: str | None


def read_version(hooks: Hooks, repo_dir: str | Path) -> VersionRead:
    """‏#1159: לעולם לא גרסה ``None`` בלי קטגוריה, ולעולם לא ניחוש (בלי
    נפילה לתג של ה-ISO: אחרי עדכון הוא כבר לא נכון). גרסה שלא נקראה
    נכתבת גם ליומן, כי ``/health/live`` נשאל בדיוק ברגעים שבהם היא
    קובעת — סוף ההתקנה וסוף השדרוג."""
    shown = display_version(hooks, repo_dir)
    if shown.version:
        return VersionRead(shown.version, None, None)
    try:
        version = hooks["describe"](repo_dir)
    except VersionError as exc:
        category, detail = exc.category, str(exc)
    except Exception as exc:                           # noqa: BLE001
        category, detail = "git-failed", str(exc) or type(exc).__name__
    else:
        if version:
            return VersionRead(version, None, None)
        category, detail = "no-tag", f"git describe --tags על {repo_dir} לא החזיר תג"
    log.warning("version: not read from %s (%s): %s", repo_dir, category, detail)
    return VersionRead(None, category, detail)


def check_update(hooks: Hooks, repo_dir: str | Path, public_url: str) -> dict:
    """בדיקה חד-פעמית מול הריפו הציבורי. אינה כותבת דבר — ``apply`` בלבד
    נוגע בעץ. הראיה חיובית: ``available`` נגזר משוואת גרסאות, לא מ"אין
    שגיאה"."""
    current = current_version(hooks, repo_dir)
    try:
        ok, out, err = hooks["ls_remote_tags"](public_url)
    except Exception as exc:                            # noqa: BLE001
        ok, out, err = False, "", str(exc)
    if not ok:
        detail = err or "הפקודה נכשלה בלי פלט שגיאה"
        low = detail.lower()
        no_network = any(marker in low for marker in (
            "timed out", "timeout", "connection refused", "could not resolve host",
            "couldn't resolve host", "network is unreachable", "network unreachable",
            "no route to host",
        ))
        return {"current": current, "latest": None, "available": False,
                "reason": "no_network" if no_network else "failed",
                "message": NO_NETWORK_MESSAGE if no_network
                else f"הבדיקה מול {public_url} נכשלה: {detail}",
                "detail": detail}
    tags = parse_remote_tags(out)
    if not tags:
        return {"current": current, "latest": None, "available": False,
                "reason": "אין גרסאות (tags) בריפו הציבורי"}
    latest = tags[-1]
    base = _base_version(current)
    available = base is None or (semver_key(latest) or ()) > (semver_key(base) or ())
    result = {"current": current, "latest": latest, "available": available, "reason": None}
    if available:
        result["signature"] = tag_signature(hooks, public_url, latest, repo_dir)
    return result


def tag_signature(hooks: Hooks, public_url: str, tag: str, repo_dir: str | Path) -> dict:
    """‏``{state, detail}`` מתוך ``SIGNATURE_STATES``. כשל של הבדיקה עצמה —
    חריגה, או תשובה שאינה אחד המצבים — הוא ``unchecked``, לעולם לא ``signed``."""
    try:
        doc = hooks["verify_tag"](public_url, tag, repo_dir)
    except Exception as exc:                            # noqa: BLE001
        return {"state": "unchecked", "detail": f"בדיקת החתימה נכשלה: {exc!r}"}
    if not isinstance(doc, dict) or doc.get("state") not in SIGNATURE_STATES:
        return {"state": "unchecked", "detail": f"תשובה לא מוכרת מבדיקת החתימה: {doc!r}"}
    return {"state": doc["state"], "detail": str(doc.get("detail") or "")}


def _set_status(conn, doc: dict) -> None:
    try:
        set_setting(conn, STATUS_KEY, json.dumps(doc))
    except Exception:                                    # noqa: BLE001
        # ‏#1127: DB נעול = המפעיל רואה `idle` במקום `failed`; לפחות ביומן
        log.warning("update: status %s not written (db)", doc.get("state"), exc_info=True)


def get_status(conn) -> dict | None:
    return _get_json_setting(conn, STATUS_KEY)


def _get_json_setting(conn, key: str) -> dict | None:
    try:
        raw = get_setting(conn, key)
    except Exception:                                    # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def record_last_check(conn, result: dict) -> dict:
    """‏#1000: שומר את תוצאת הבדיקה עם חותמת השרת. הכתיבה **אינה** נבלעת:
    בדיקה שרצה ולא נזכרה תציג "לא נבדק" — וזה שקר שקט (עיקרון 5)."""
    doc = {"at": now_iso(), **result}
    set_setting(conn, LAST_CHECK_KEY, json.dumps(doc))
    return doc


def get_last_check(conn) -> dict | None:
    """‏``None`` = מעולם לא נבדק (או רשומה פגומה) — לא "אין חדש"."""
    return _get_json_setting(conn, LAST_CHECK_KEY)


def running_verdict(doc: dict, current: str | None) -> bool | None:
    """‏#1642: מה ``running`` שב-DB שווה, מול הגרסה שרצה **עכשיו**.

    ‏``None`` = הסטטוס אינו "רץ עם תג"; ‏``True`` = הגרסה הרצה היא התג, כלומר
    העדכון הסתיים והסטטוס רק לא עודכן (הקונסולה מציגה "הושלם"); ‏``False`` =
    גרסה אחרת (או שלא נקראה) רצה — העדכון אולי עדיין רץ. תשובה אחת לשני
    הקוראים: הקונסולה (``status_with_verification``) וההפעלה המתוזמנת."""
    if doc.get("state") != "running" or not doc.get("tag"):
        return None
    return current == doc["tag"]


def last_progress(doc: dict) -> datetime | None:
    """‏#1642: הרגע האחרון שבו ריצת העדכון זזה — ‏``started_at`` וחותמות
    ‏``stages`` שהסקריפט כותב בכל מעבר שלב. ‏``None`` = אין חותמת, או שאחת
    מהן אינה זמן עם אזור זמן: "לא ידוע" ולא "ישן" (עיקרון 5)."""
    stages = doc.get("stages")
    stamps = []
    for value in [doc.get("started_at"), *(stages.values() if isinstance(stages, dict) else [])]:
        try:
            moment = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            return None
        stamps.append(moment)
    return max(stamps) if stamps else None


def status_with_verification(conn, hooks: Hooks, repo_dir: str | Path,
                             vm_warning: Callable[[], str | None] | None = None) -> dict:
    """מצב ההרצה האחרונה + הראיה החיובית: האם הגרסה שרצה בפועל, עכשיו,
    היא התג שביקשנו. "לא ידוע עדיין" (השרת עוד לא עלה מחדש) שונה מפורשות
    מ"נכשל" — עיקרון 5.

    ‏#1301: ``now`` — שעון השרת ברגע הקריאה, כדי שהקונסולה תחשב "זמן שעבר"
    מ-``started_at``/``stages`` בלי להסתמך על שעון הדפדפן. ‏``done`` שהסקריפט
    כתב בעצמו מאומת באותה השוואה."""
    doc = get_status(conn) or {"state": "idle"}
    current = current_version(hooks, repo_dir)
    result = {**doc, "current": current, "now": now_iso()}
    verdict = running_verdict(doc, current)
    if verdict is not None:
        result["verified"] = verdict
        if verdict:
            result["state"] = "done"
    elif doc.get("state") == "done" and doc.get("tag"):
        result["verified"] = current == doc["tag"]
    # ‏#1372: עדכון שהסתיים ואומת על הגרסה שרצה — אבל המארח של "עדכון ב-VM" אינו
    # מוכן. נקרא עכשיו ולא נשמר: הסקריפט של התג הישן כותב `done` בלי אזהרה **אחרי**
    # שהשרת החדש עלה, וכשההכנה ברקע מסתיימת האזהרה נעלמת בלי לכתוב דבר.
    if (vm_warning is not None and result.get("state") == "done" and result.get("verified")
            and not result.get("warning")):
        warning = vm_warning()
        if warning:
            result["warning"] = warning
    return result


def _apply_in_background(conn, hooks: Hooks, repo_dir: str | Path, tag: str,
                         user: str, forced: str | None = None) -> bool:
    """‏``False`` = יחידת שדרוג כבר רצה (#1273): הסטטוס הקודם חוזר כמו שהיה,
    ו-failed **אינו** נכתב — המפעיל היה רואה "נכשל" בזמן שהעדכון רץ.
    כל כשל אחר בהפעלה: ``failed`` נכתב ו-``UpgradeLaunchError`` נזרק (#2160)."""
    prior = get_status(conn)
    previous = current_version(hooks, repo_dir)
    if previous:
        try:
            set_setting(conn, PREVIOUS_KEY, previous)
        except Exception:                                # noqa: BLE001
            log.warning("update: previous version %s not recorded (db)", previous, exc_info=True)

    # ‏#1193: תהליך ה-web רץ עם ProtectSystem=strict, ולכן אסור לו לכתוב
    # לעץ הקוד. ה-fetch וה-checkout נעשים בתוך server-upgrade.sh, ביחידה
    # המנותקת שמחוץ לארגז החול. הסטטוס נכתב לפני השיגור כדי שלא יהיה חלון
    # שבו בקשה שהתקבלה עדיין נראית idle.
    # ‏#1301: ‏started_at/by/from — הסקריפט ממשיך את אותה ריצה (אותו שעון ואותו
    # מפעיל), ומסך העדכון בקונסולה מציג אותם גם אחרי רענון.
    doc = {"state": "running", "tag": tag, "started_at": now_iso(),
           "by": user, "from": previous}
    if forced:
        # ‏#1891: המנהל אישר מעל חסימה — הסקריפט קורא את זה ואינו מסרב שוב.
        doc["forced"] = forced
    _set_status(conn, doc)
    ok, err = hooks["run_upgrade"](repo_dir, tag)
    if not ok and unit_already_exists(err):
        _set_status(conn, prior or {"state": "idle"})
        journal(conn, "update_apply_busy", f"{tag}: {err}", user)
        return False
    if not ok:
        _set_status(conn, {"state": "failed", "tag": tag,
                           "error": f"הפעלת tools/server-upgrade.sh נכשלה: {err}"})
        journal(conn, "update_apply_failed", f"{tag}: script — {err}", user)
        raise UpgradeLaunchError(f"הפעלת tools/server-upgrade.sh נכשלה: {err}")

    # ‏#748 סעיף 5: "אומת" נקבע רק ב-``status_with_verification`` אחרי
    # שהשרת עלה מחדש עם התג — לא כאן. ``running`` נשאר עד אז; הסקריפט
    # עצמו מחליף אותו ל-``failed`` אם אחד משלבי השדרוג נכשל.
    journal(conn, "update_apply_started", tag, user)
    return True


def start_apply(ctx, hooks: Hooks, repo_dir: str | Path, tag: str, user: str,
                forced: str | None = None) -> bool:
    """שיגור הסקריפט המנותק (שניות ספורות, לא השדרוג עצמו). ‏FastAPI
    מריץ נתיבי `def` סינכרוניים בתוך thread pool משלו, כך שזה אינו חוסם
    את ה-event loop; אין צורך ב-``threading.Thread`` נוסף."""
    return _apply_in_background(ctx.conn, hooks, repo_dir, tag, user, forced)


def restart_blocker(rt) -> str | None:
    """מה מונע עכשיו פעולה שמסתיימת ב-restart של השרת, או None (#1891).

    ההגדרה היא `auto_reboot.busy_reason` — סבב, סבב חדר **גם בין גלים**
    (‏`room_rounds` ‏active/closing, כש-`sessions` כבר סגור), קליטה רצה,
    עדכון VM ושיכפול מקומי. עד כאן נבדקה כאן רק `sessions`, ו"החל עדכון"
    התקבל בין גל לגל ובאמצע קליטה. ‏``rt`` הוא כל אובייקט עם ``conn`` (ובשרת
    — גם ``vmupdate``).

    DB שלא נקרא אינו "לא עסוק": זה סירוב בשם (עיקרון 5)."""
    from . import auto_reboot
    try:
        return auto_reboot.busy_reason(rt, include_update=False)
    except Exception as exc:                             # noqa: BLE001 — לא ידוע = סירוב
        log.warning("restart_blocker: could not read the busy state: %r", exc)
        return "לא ניתן היה לבדוק אם השרת עסוק (קריאת ה-DB נכשלה)"


def create_update_router(ctx, repo_dir: str | Path, server_base: str,
                         hooks: Hooks | None = None,
                         public_url: str = PUBLIC_UPDATE_URL,
                         vm_warning: Callable[[], str | None] | None = None,
                         busy_source=None) -> APIRouter:
    """‏``busy_source`` (‏#1891): מה ש-`restart_blocker` שואל — בשרת, ה-runtime
    (שנושא גם את ``vmupdate``); ‏None = ``ctx``."""
    from server.console import auth

    router = APIRouter(prefix="/api/console/update")
    current_user, admin_only = auth.dependencies(ctx.conn)
    del current_user
    del server_base  # שם השרת מגיע מ-/me ומהגדרת server_name, לא מכתובת ההאזנה.
    hooks = {**default_hooks(), **(hooks or {})}
    # ‏#1273: השומר "עדכון כבר רץ" וכתיבת running — פעולה אחת. בלעדיה שתי
    # בקשות מה-thread pool (לחיצה כפולה) עוברות שתיהן את השומר.
    apply_lock = threading.Lock()

    @router.get("/backup")
    def settings_backup(user=Depends(admin_only)):
        """‏#1128: "הורד גיבוי הגדרות" — tar.gz של data_dir עם עותק עקבי של
        ה-DB (backup API, לא cp), ‏MANIFEST.txt ו-SHA256SUMS; **בלי אימג'ים**.
        הארכיון מכיל סודות — הקונסולה מזהירה לפני ההורדה."""
        from fastapi.responses import Response  # noqa: PLC0415
        from server.system import backup  # noqa: PLC0415
        if ctx.data_dir is None:
            raise HTTPException(503, "תיקיית הנתונים אינה ידועה לשרת הזה — אין מה לגבות")
        payload, name = backup.build_archive(ctx.conn, Path(ctx.data_dir),
                                             current_version(hooks, repo_dir))
        journal(ctx.conn, "settings_backup_downloaded", f"{name} {len(payload)} bytes", user[0])
        return Response(payload, media_type="application/gzip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "no-store"})

    @router.get("")
    def info(user=Depends(admin_only)):
        del user
        ver = read_version(hooks, repo_dir)
        return {
            "current": ver.version,
            # ‏#1159: הסיבה המלאה (נתיב + stderr של git) — admin בלבד; ל-
            # ‏`/health/live` ללא ההזדהות מגיעה רק הקטגוריה.
            "current_error": ver.detail,
            "enabled": enabled(ctx.conn),
            "previous": get_setting(ctx.conn, PREVIOUS_KEY),
            # ‏#1000: ‏null = מעולם לא נבדק בשרת הזה; אחרת {at, current,
            # latest, available, reason} כפי שנשמר ב-POST /update/check.
            "last_check": get_last_check(ctx.conn),
        }

    @router.get("/status")
    def status(user=Depends(admin_only)):
        del user
        return status_with_verification(ctx.conn, hooks, repo_dir, vm_warning)

    @router.post("/check")
    def check(user=Depends(admin_only)):
        if not enabled(ctx.conn):
            raise HTTPException(404, "עדכון כבוי בהגדרות השרת")
        result = record_last_check(ctx.conn, check_update(hooks, repo_dir, public_url))
        journal(ctx.conn, "update_check",
               f"current={result['current']} latest={result['latest']}", user[0])
        return result

    def _apply_or_revert(user, tag: str, force: bool = False):
        if not enabled(ctx.conn):
            raise HTTPException(404, "עדכון כבוי בהגדרות השרת")
        blocker = restart_blocker(busy_source or ctx)
        if blocker and not force:
            # ‏#1891 (סקירה): חלק מהחסימות הן דיווח של מחשבים, ומחשב תקוע היה חוסם
            # עדכון אבטחה לעד. לכן זו אזהרה עם סיבה ו-"עדכן בכל זאת" (`force`),
            # לא קיר. האתחול המתוזמן (`auto_reboot`) אינו עוקף — רק מנהל, בלחיצה.
            return JSONResponse(status_code=409, content={
                "detail": f"{blocker} — לא ניתן לעדכן עכשיו; העדכון יתאפשר כשזה "
                          "יסתיים, או בלחיצה על \"עדכן בכל זאת\"",
                "code": "update_busy", "busy": blocker})
        with apply_lock:
            # ‏running שאומת כבר הוא done (status_with_verification) — לא חוסם.
            now = status_with_verification(ctx.conn, hooks, repo_dir)
            if now.get("state") == "running":
                raise HTTPException(409, f"עדכון ל-{now.get('tag')} כבר רץ — ממתינים "
                                         "שיסתיים (השרת יאותחל) לפני עדכון נוסף")
            try:
                started = start_apply(ctx, hooks, repo_dir, tag, user[0], forced=blocker or None)
            except UpgradeLaunchError as exc:
                raise HTTPException(500, str(exc)) from exc
            if not started:
                raise HTTPException(409, f"יחידת השדרוג ({UPGRADE_UNIT}) כבר רצה — "
                                         "עדכון קודם עוד לא הסתיים")
        if blocker:
            # ‏#1891: העקיפה גלויה ביומן — מי, לאיזו גרסה, ומעל איזו חסימה.
            journal(ctx.conn, "update_forced", f"{tag}: הוחל מעל חסימה — {blocker}", user[0])
        return {"ok": True, "started": True, "tag": tag}

    # ‏def רגיל (לא async): FastAPI מריץ אותו ב-thread pool משלו. הקריאה
    # ממתינה רק ל-systemd-run שמקבל את היחידה; git והרסטארט רצים ביחידה
    # המנותקת, מחוץ לתהליך ולארגז החול של שרת ה-web.
    @router.post("/apply")
    def apply(body: dict = Body(default={}), user=Depends(admin_only)):
        tag = body.get("tag", "") if isinstance(body, dict) else ""
        if not semver_key(tag):
            raise HTTPException(400, f"תג לא תקין: {tag!r}")
        # עדכון הפיך דרך update_previous, ולכן אינו דורש הקלדת שם (#1192).
        return _apply_or_revert(user, tag, force=body.get("force") is True)

    # ‏#1778 (הכרעת #1270): בלי הקלדת שם — חלון אישור עם כפתור אדום בקונסולה.
    # החזרה הפיכה בדיוק כמו apply: היא עוברת באותו מסלול, ו-update_previous
    # מקבל את הגרסה שממנה חזרנו. ‏confirm_name שנשלח (קונסולה ישנה) מתעלמים ממנו.
    @router.post("/revert")
    def revert(body: dict = Body(default={}), user=Depends(admin_only)):
        previous = get_setting(ctx.conn, PREVIOUS_KEY)
        if not previous:
            raise HTTPException(404, "אין גרסה קודמת לחזור אליה")
        force = isinstance(body, dict) and body.get("force") is True   # ‏#1891
        return _apply_or_revert(user, previous, force=force)

    return router
