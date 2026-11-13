"""#2064: שער Impeccable על הקונסולה — גלאי בגרסה נעולה, ומחגר.

הכלי (github.com/pbakaus/impeccable, Apache-2.0) מזהה דפוסי "עיצוב AI
גנרי" ב-HTML/CSS. כאן רץ **רק** `detect` על `server/static`, מקומית, בלי
מודל ובלי רשת. המחגר:

* ממצא שאינו ב-`impeccable_baseline.json` — **נכשל** (דפוס חדש).
* ממצא שברשימה ואינו נמצא עוד — **נכשל** גם הוא, עד שמוחקים אותו
  מהרשימה. כך ממצא שתוקן אינו יכול לחזור בשקט.

זהות ממצא היא (קובץ, כלל, snippet) — **בלי** מספר השורה. שורה זזה בכל
עריכה מעליה, ושער שנופל על כל עריכה כזאת מכבים אחרי יומיים. השורה נשמרת
ברשימה לקריאה בלבד, כפי שהייתה בריצה שכתבה אותה. ממצאים זהים נספרים
(multiset): עוד `border-inline-start: 4px solid` זהה לקיים הוא ממצא חדש.

`--no-config` תמיד: הוא מכבה גם הערות `impeccable-disable` בתוך הקוד,
ולכן אי-אפשר לעבור את השער בהערה (ה-Issue אוסר זאת במפורש).

עיקרון 5 לאורך כל הדרך: sha256 נבדק לפני **כל** הרצה, ו-sha שאינו תואם,
יציאה 1 של הגלאי ("יעד לא נסרק"), פלט שאינו JSON או פקיעת זמן — כולם
`GateError`, לא "אין ממצאים".

שימוש כתוכנית (ה-CI והמתאם):
    python tests/impeccable_gate.py pin            # sha256=/url= ל-GITHUB_OUTPUT
    python tests/impeccable_gate.py fetch <dest>   # הורדה מה-release, אימות sha
    python tests/impeccable_gate.py dump           # הממצאים הנוכחיים בפורמט הרשימה
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"
BASELINE = Path(__file__).resolve().parent / "impeccable_baseline.json"

#: הגרסה הנעולה. ה-sha הוא של הנכס ב-release (`digest` ב-API של GitHub,
#: 04/11) וזהה לזה שאומת מול ה-sidecar על ה-testrunner ב-23/10 (#2061).
ENGINE = "0.1.16"
ASSET = "impeccable-linux-x64"
SHA256 = "a890a5b4b7d9d79ec54aa74612b5e924e5f5b422600a25fde8bcbda11b031787"
URL = f"https://github.com/pbakaus/impeccable/releases/download/engine-v{ENGINE}/{ASSET}"

#: ה-CI מוריד לכאן ומצהיר על הנתיב; ה-testrunner — הנתיב הקבוע.
ENV_BIN = "IMAGESRV_IMPECCABLE_BIN"
LAB_BIN = Path("/work/impeccable/imp")

#: זמן הריצה על כל `server/static` לא נמדד עדיין (ה-Issue, "מה לא אומת").
TIMEOUT_S = 600

BASELINE_DOC = [
    "#2064: הממצאים המוכרים של Impeccable ב-server/static (tests/test_impeccable_gate.py).",
    "ממצא שאינו כאן מכשיל את השער; ממצא שכאן ואינו נמצא עוד מכשיל גם הוא, עד שמוחקים",
    "את הרשומה — ממצא שתוקן אינו חוזר. הזהות: קובץ + כלל + snippet. 'line' הוא מהריצה",
    "שכתבה את הרשומה ואינו מושווה. רשומות זהות נספרות.",
    "לעולם לא מוסיפים רשומה כדי שממצא חדש יעבור — מתקנים את העיצוב. 'dump' כותב את",
    "הקובץ מחדש רק בהצמדת engine חדשה (engine/sha256 חייבים להתאים ל-impeccable_gate.py).",
]


class GateError(RuntimeError):
    """הבדיקה עצמה לא נעשתה. אינה "אין ממצאים" ואינה דילוג."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def resolve_binary(env: dict | None = None, lab: Path = LAB_BIN) -> Path | None:
    """איפה הבינארי. None = אין בסביבה הזו (דילוג מוצהר, או כישלון כשהכלים
    אמורים להיות). נתיב שהוצהר ב-`ENV_BIN` ואינו קיים הוא שגיאה: מי שהצהיר
    עליו אמר שהוריד אותו."""
    env = os.environ if env is None else env
    declared = env.get(ENV_BIN, "").strip()
    if declared:
        path = Path(declared)
        if not path.is_file():
            raise GateError(f"{ENV_BIN}={declared} הוצהר, והקובץ אינו שם")
        return path
    return lab if lab.is_file() else None


def verify(path: Path, expected: str = SHA256) -> None:
    actual = sha256_of(path)
    if actual != expected:
        raise GateError(
            f"sha256 של {path} אינו הגרסה הנעולה ({ENGINE}): {actual} != {expected}"
        )


def run_detect(cmd: list[str], target: Path = STATIC, timeout: float = TIMEOUT_S) -> list[dict]:
    """מריץ `detect --json --no-config` ומחזיר את רשימת הממצאים הגולמית.

    קודי היציאה של הגלאי (engine 0.1.16, `crates/detect/src/cli.rs`):
    0 — אין ממצאים ראשיים, 2 — יש, 1 — יעד שלא נסרק. רק 0 ו-2 הם סריקה
    שהושלמה; כל השאר שגיאה, גם אם stdout נראה תקין.
    """
    try:
        proc = subprocess.run(
            [*cmd, "detect", "--json", "--no-config", str(target)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"הגלאי לא סיים תוך {timeout}s") from exc
    if proc.returncode not in (0, 2):
        raise GateError(
            f"הגלאי יצא {proc.returncode} (1 = יעד שלא נסרק): {proc.stderr.strip()[-2000:]}"
        )
    try:
        found = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GateError(f"פלט הגלאי אינו JSON: {proc.stdout[:500]!r}") from exc
    if not isinstance(found, list):
        raise GateError(f"פלט הגלאי אינו רשימה: {type(found).__name__}")
    if proc.returncode == 2 and not found:
        raise GateError("הגלאי יצא 2 (יש ממצאים) ופלט רשימה ריקה")
    return found


def normalize(found: list[dict], root: Path = REPO) -> list[dict]:
    """ממצא גולמי → רשומה ברשימה: נתיב יחסי לריפו ב-`/`, ובלי השורש ב-snippet."""
    root_text = str(root)
    out = []
    for f in found:
        file = str(f.get("file", ""))
        try:
            file = Path(file).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass  # מחוץ לריפו — נשאר מלא, ויופיע כממצא חדש
        snippet = " ".join(str(f.get("snippet", "")).replace(root_text, "").split())
        out.append({"file": file, "rule": str(f.get("antipattern", "")),
                    "snippet": snippet, "line": int(f.get("line") or 0)})
    return sorted(out, key=lambda e: (e["file"], e["rule"], e["line"], e["snippet"]))


def _key(entry: dict) -> tuple[str, str, str]:
    return entry["file"], entry["rule"], entry["snippet"]


def compare(current: list[dict], known: list[dict]) -> tuple[list[dict], list[dict]]:
    """(חדשים, כאלה שתוקנו ועדיין ברשימה) — כ-multiset על (קובץ, כלל, snippet)."""
    cur, base = Counter(map(_key, current)), Counter(map(_key, known))
    new_keys, gone_keys = cur - base, base - cur

    def pick(entries: list[dict], wanted: Counter) -> list[dict]:
        left, picked = Counter(wanted), []
        for e in entries:
            if left[_key(e)] > 0:
                left[_key(e)] -= 1
                picked.append(e)
        return picked

    return pick(current, new_keys), pick(known, gone_keys)


def load_baseline(path: Path = BASELINE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("findings"), list):
        raise GateError(f"{path.name}: אין רשימת findings")
    return data


def fetch(dest: Path, url: str = URL, expected: str = SHA256, opener=urllib.request.urlopen) -> str:
    """ה-CI: הבינארי בנתיב `dest`, מאומת. "cache" כשהעותק השמור תואם,
    "downloaded" אחרי הורדה. קובץ שלא תאם אינו נשאר בנתיב לעולם."""
    if dest.is_file() and sha256_of(dest) == expected:
        return "cache"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with opener(url, timeout=120) as resp, open(part, "wb") as out:
            for block in iter(lambda: resp.read(1 << 20), b""):
                out.write(block)
        verify(part, expected)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    os.chmod(part, 0o755)
    os.replace(part, dest)
    return "downloaded"


def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "pin":
            print(f"sha256={SHA256}\nurl={URL}\nengine={ENGINE}")
        elif cmd == "fetch" and len(argv) == 3:
            print(f"impeccable {ENGINE}: {fetch(Path(argv[2]))} -> {argv[2]}")
        elif cmd == "dump":
            binary = resolve_binary()
            if binary is None:
                raise GateError(f"אין בינארי: {ENV_BIN} ריק ו-{LAB_BIN} אינו קיים")
            verify(binary)
            findings = normalize(run_detect([str(binary)]))
            print(json.dumps({"_doc": BASELINE_DOC, "engine": ENGINE, "sha256": SHA256,
                              "findings": findings},
                             ensure_ascii=False, indent=2))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except GateError as exc:
        print(f"impeccable gate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
