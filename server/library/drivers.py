"""ספריית חבילות הדרייברים (#720) — תיקייה לכל חבילה, `manifest.json`
ליד הקבצים, והדיסק הוא מקור האמת (כמו ספריית האימג'ים, עיקרון 3).

```
<data-dir>/drivers/<name>/manifest.json
<data-dir>/drivers/<name>/<files...>
```

**מה נכנס ומה לא (עיקרון 6, כמו `archive.py`):** חבילה נכנסת רק דרך
`import_tar`, וכל קובץ שהיא מצהירה עליו נבדק מול ה-sha256 שבמניפסט
**לפני** שהמניפסט מקבל את שמו. קובץ שאינו מוצהר, קובץ שחסר, או קובץ
שה-sha שלו שונה — החבילה כולה נדחית ואינה משאירה דבר. אחרי הכניסה
החבילה **בלתי-משתנה**: אין עריכה, רק מחיקה (מאחורי הקלדת שם) וייבוא
מחדש. הסוכן מאמת את אותם sha256 שוב לפני ההעתקה ל-NTFS (‏postdeploy.sh).

**ההתאמה** (`match`) היא פונקציה טהורה על המלאי (‏`server/agent/inventory.py`)
ורשימת החבילות — דטרמיניסטית, בלי מצב: אותו מלאי ואותן חבילות נותנים
תמיד אותה רשימה, באותו סדר. אין התאמה = רשימה ריקה, לא שגיאה.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from server.library.archive import _clear, _safe_members, _sha256, _shown
from server.library.images import inside

log = logging.getLogger("imagesrv.drivers")

#: שם חבילה = שם התיקייה = מה שמופיע ב-`\ImageSrv\Drivers\<name>` על
#: הדיסק המשוחזר. ‏ASCII בלבד: הנתיב הזה נכתב ל-NTFS מלינוקס ונקרא
#: ב-Windows, ושם-תיקייה שאינו ASCII הוא סיכון בשני הצדדים.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
STAGING_PREFIX = ".import-"
UNVERIFIED = "manifest.unverified"
#: תקרת קבצים לחבילה — חבילת דרייבר אמיתית היא עשרות קבצים, לא אלפים.
FILES_LIMIT = 2000
MATCH_LIMIT = 64
#: ‏`vendor:device[:class]` — ה-class אופציונלי (#959): ‏INF של דרייבר נוקב
#: ב-`PCI\VEN_8086&DEV_15BC` בלבד, ואין ממנו דרך לגזור את קוד ה-class
#: ש-`inventory.PCI_RE` דורש. מזהה בלי class תואם כל בקר עם אותו
#: vendor:device, בכל class.
PCI_ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}(:[0-9a-f]{6})?$")
#: תקרה לכלל `pci_any` — חבילת דגם של HP/Lenovo מונה מאות מזהי רשת
#: ואחסון (‏INF אחד של Intel מונה עשרות דורות), וכולם בכלל אחד.
PCI_ANY_LIMIT = 512


class DriverError(ValueError):
    pass


# --- המניפסט -----------------------------------------------------------------


def safe_relative(path: object) -> str | None:
    """נתיב יחסי תקין בתוך החבילה — ‏`x/y.inf` — או ``None``.

    לוכסן אחורי (‏tar שנוצר ב-Windows) מנורמל ל-`/`; מוחלט, `..`, רכיב
    ריק או תו בקרה נדחים. ‏ASCII בלבד מאותו טעם כמו `NAME_RE`.
    """
    if not isinstance(path, str) or not path or len(path) > 255:
        return None
    text = path.replace("\\", "/")
    if text.startswith("/") or not text.isascii() or not text.isprintable():
        return None
    parts = PurePosixPath(text).parts
    if not parts or any(p in ("..", ".", "") for p in parts):
        return None
    return "/".join(parts)


def validate_manifest(manifest: object) -> str | None:
    """הבעיה הראשונה במניפסט, בעברית, או ``None`` כשהוא תקין."""
    if not isinstance(manifest, dict):
        return "המניפסט אינו אובייקט"
    if manifest.get("schema") != 1:
        return f"schema לא נתמך: {_shown(manifest.get('schema'))}"
    name = manifest.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return f"שם חבילה לא תקין: {_shown(name)}"
    if "description" in manifest and not isinstance(manifest["description"], str):
        return "description חייב להיות מחרוזת"
    rules = manifest.get("match")
    if not isinstance(rules, list) or not rules or len(rules) > MATCH_LIMIT:
        return "match חייב להיות רשימה לא ריקה של כללי התאמה"
    for rule in rules:
        problem = _rule_problem(rule)
        if problem:
            return problem
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > FILES_LIMIT:
        return "files חייב להיות רשימה לא ריקה של קבצים"
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            return "פריט ב-files אינו אובייקט"
        rel = safe_relative(entry.get("path"))
        if rel is None:
            return f"נתיב קובץ לא תקין: {_shown(entry.get('path'))}"
        if rel in seen:
            return f"נתיב כפול ב-files: {_shown(rel)}"
        seen.add(rel)
        if not isinstance(entry.get("sha256"), str) or not SHA_RE.match(entry["sha256"]):
            return f"sha256 לא תקין עבור {_shown(rel)}"
    return None


def _rule_problem(rule: object) -> str | None:
    if not isinstance(rule, dict):
        return "כלל התאמה אינו אובייקט"
    kinds = [k for k, present in (("pci", "pci" in rule), ("pci_any", "pci_any" in rule),
                                  ("model", "vendor" in rule or "model" in rule)) if present]
    if len(kinds) != 1:
        return ("כלל התאמה הוא או {vendor, model} או {pci: [...]} או {pci_any: [...]}"
                " — לא שניהם ולא אף אחד")
    if kinds[0] in ("pci", "pci_any"):
        key = kinds[0]
        ids = rule[key]
        limit = MATCH_LIMIT if key == "pci" else PCI_ANY_LIMIT
        if not isinstance(ids, list) or not ids or len(ids) > limit:
            return f"{key} חייב להיות רשימה לא ריקה (עד {limit})"
        for pci in ids:
            if not isinstance(pci, str) or not PCI_ID_RE.match(pci):
                return f"מזהה PCI לא תקין: {_shown(pci)} (vendor:device[:class], hex קטן)"
        return None
    vendor, model = rule.get("vendor"), rule.get("model")
    if not isinstance(vendor, str) or not vendor.strip():
        return "כלל דגם דורש vendor"
    if not isinstance(model, str) or not model.strip():
        return "כלל דגם דורש model"
    return None


# --- ההתאמה ------------------------------------------------------------------


def _norm(text: object) -> str:
    return text.strip().casefold() if isinstance(text, str) else ""


def _id_present(pci: str, have: list[str]) -> bool:
    """מזהה עם class = זהות מלאה; בלי class = אותו vendor:device בכל class."""
    return pci in have if pci.count(":") == 2 else any(h.startswith(pci + ":") for h in have)


def _rule_matches(rule: dict, inventory: dict) -> str | None:
    """‏`"pci"` / `"model"` כשהכלל תואם, אחרת ``None``."""
    have = list(inventory.get("pci") or [])
    if "pci" in rule:
        return "pci" if all(_id_present(p, have) for p in rule["pci"]) else None
    if "pci_any" in rule:
        return "pci" if any(_id_present(p, have) for p in rule["pci_any"]) else None
    dmi = inventory.get("dmi") or {}
    if _norm(rule.get("vendor")) != _norm(dmi.get("sys_vendor")):
        return None
    model = _norm(rule.get("model"))
    # שם הדגם יושב ב-`product_name` אצל Dell/HP וב-`product_version` אצל
    # Lenovo — שני השדות נבדקים, כדי שהמפעיל יכתוב את מה שמודפס על המחשב.
    if model in (_norm(dmi.get("product_name")), _norm(dmi.get("product_version"))):
        return "model"
    return None


def match(inventory: dict | None, packages: list[dict]) -> list[dict]:
    """אילו חבילות מתאימות למלאי — ``[{"name": …, "by": "pci"|"model"}]``.

    - כלל `pci` תואם כש**כל** המזהים שבו קיימים במלאי, וכלל `pci_any`
      כש**אחד** מהם קיים (#959); מזהה בלי class תואם כל בקר עם אותו
      vendor:device. כלל דגם תואם כשה-vendor שווה ל-`sys_vendor` וה-model
      ל-`product_name` **או** `product_version` (בלי רגישות לרישיות
      ולרווחים בקצוות).
    - חבילה מתאימה כשאחד מכלליה תואם. ‏**PCI גובר על דגם**: חבילה
      שתאמה ב-PCI מסומנת `pci` גם אם תאמה גם בדגם, וההתאמות לפי PCI
      קודמות ברשימה — הן הראיה מהחומרה עצמה, ושם הדגם הוא נפילה אחורה.
    - בלי מלאי, או בלי חבילה תואמת — רשימה ריקה. לא שגיאה.
    - הסדר דטרמיניסטי: PCI לפני דגם, ובתוך כל קבוצה לפי שם.
    """
    if not inventory:
        return []
    found: list[tuple[int, str, str]] = []
    for pkg in packages:
        kinds = {k for r in pkg.get("match", []) if (k := _rule_matches(r, inventory))}
        if kinds:
            by = "pci" if "pci" in kinds else "model"
            found.append((0 if by == "pci" else 1, pkg["name"], by))
    return [{"name": name, "by": by} for _rank, name, by in sorted(found)]


# --- דגם שנבחר ביד (#1269) ---------------------------------------------------


def model_key(vendor: object, model: object) -> tuple[str, str]:
    """הזהות של דגם — בלי רישיות ורווחים בקצוות, כמו ההשוואה ב-`match`."""
    return _norm(vendor), _norm(model)


def _model_rules(pkg: dict) -> list[dict]:
    return [r for r in pkg.get("match", []) if "vendor" in r and "model" in r]


def models(packages: list[dict]) -> list[dict]:
    """הדגמים שיש להם חבילה בספרייה — ``[{"vendor", "model", "packages"}]``.

    רק מ**כללי הדגם** של המניפסטים: זה מה שמישהו כתב שהחבילה שייכת לו.
    חבילה עם כללי PCI בלבד אינה מוסיפה דגם — אין ממנה שם לבחור, ולא
    ממציאים אחד. כפילות לפי `model_key`; התצוגה מהחבילה הראשונה לפי שם.
    הסדר דטרמיניסטי: לפי vendor ואז model (מנורמלים).
    """
    found: dict[tuple[str, str], dict] = {}
    for pkg in sorted(packages, key=lambda p: p["name"]):
        for rule in _model_rules(pkg):
            entry = found.setdefault(model_key(rule["vendor"], rule["model"]), {
                "vendor": rule["vendor"].strip(), "model": rule["model"].strip(),
                "packages": []})
            if pkg["name"] not in entry["packages"]:
                entry["packages"].append(pkg["name"])
    return [found[key] for key in sorted(found)]


def for_model(vendor: str, model: str, packages: list[dict]) -> list[str]:
    """שמות החבילות שכלל דגם שלהן הוא בדיוק הדגם הזה, לפי שם. **רק כלל
    דגם** — דגם שנבחר ביד אינו מלאי, ואין כאן PCI להשוות אליו."""
    key = model_key(vendor, model)
    return sorted(p["name"] for p in packages
                  if any(model_key(r["vendor"], r["model"]) == key for r in _model_rules(p)))


# --- הספרייה על הדיסק ------------------------------------------------------


class DriverLibrary:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def scan(self) -> dict[str, dict]:
        """כל חבילה מאומתת (יש לה `manifest.json`), לפי שם. תיקייה
        שהמניפסט שלה אינו נקרא, פגום, או ששמו שונה משם התיקייה — מדולגת
        ונרשמת ביומן: תיקייה כזו אינה חבילה (עיקרון 5)."""
        found: dict[str, dict] = {}
        if not self.root.is_dir():
            return found
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if folder.name.startswith("."):
                continue
            path = folder / "manifest.json"
            if not path.is_file():
                continue
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("driver package %s: manifest unreadable: %s", folder.name, exc)
                continue
            problem = validate_manifest(manifest)
            if problem or manifest["name"] != folder.name:
                log.warning("driver package %s skipped: %s", folder.name,
                            problem or "name differs from the folder")
                continue
            manifest["_dir"] = str(folder)
            found[manifest["name"]] = manifest
        return found

    def get(self, name: str) -> dict | None:
        return self.scan().get(name) if NAME_RE.match(name or "") else None

    def public_list(self) -> list[dict]:
        return [self.public(m) for m in self.scan().values()]

    @staticmethod
    def public(manifest: dict) -> dict:
        return {
            "name": manifest["name"],
            "description": manifest.get("description", ""),
            "match": manifest["match"],
            "files": [{"path": f["path"], "sha256": f["sha256"]} for f in manifest["files"]],
        }

    def file_path(self, name: str, rel: str) -> Path | None:
        """הקובץ, רק אם המניפסט מצהיר עליו בשמו המדויק ונוחת בתוך החבילה."""
        manifest = self.get(name)
        if manifest is None:
            return None
        wanted = safe_relative(rel)
        if wanted is None or not any(f["path"] == wanted for f in manifest["files"]):
            return None
        folder = Path(manifest["_dir"])
        path = inside(folder / wanted, folder)
        return path if path is not None and path.is_file() else None

    def delete(self, name: str) -> bool:
        manifest = self.get(name)
        if manifest is None:
            return False
        shutil.rmtree(manifest["_dir"])
        return True

    def import_tar(self, archive: Path) -> dict:
        """קולט חבילה מקובץ tar. מחזיר את המניפסט שנקלט.

        אותו מבנה כמו `archive.import_tar` (#71): חילוץ לאזור ביניים
        שהספרייה מדלגת עליו, המניפסט תחת שם אחר עד שכל sha256 נבדק,
        ורק אז `rename` לשם החבילה. **כל** קובץ נבדק — מוצהר-וחסר,
        קיים-ולא-מוצהר, או sha שונה — דוחים את החבילה כולה.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / f"{STAGING_PREFIX}{secrets.token_hex(4)}"
        staging.mkdir()
        try:
            with tarfile.open(archive, "r:*") as tar:
                members = _safe_members(tar)
                tar.extractall(staging, members=members, filter="data")
            roots = {Path(m.name.replace("\\", "/")).parts[0] for m in members}
            if len(roots) != 1:
                raise DriverError("הארכיון חייב להכיל תיקיית חבילה אחת")
            folder = staging / roots.pop()
            manifest_path = folder / "manifest.json"
            if not manifest_path.is_file():
                raise DriverError("אין manifest.json בארכיון")
            unverified = folder / UNVERIFIED
            manifest_path.replace(unverified)
            try:
                manifest = json.loads(unverified.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise DriverError(f"המניפסט אינו JSON: {exc}") from exc
            problem = validate_manifest(manifest)
            if problem:
                raise DriverError(f"מניפסט לא תקין: {problem}")
            self._verify_files(folder, manifest)
            target = inside(self.root / manifest["name"], self.root)
            if target is None:
                raise DriverError(f"יעד החבילה יוצא מהספרייה: {_shown(manifest['name'])}")
            if target.exists() or manifest["name"] in self.scan():
                raise DriverError(f"חבילה בשם {manifest['name']} כבר קיימת — מחק אותה קודם")
            # השם ניתן רק אחרי שכל הבייטים נבדקו: ייבוא שנקטע אינו משאיר
            # תיקייה שנראית כמו חבילה.
            unverified.replace(folder / "manifest.json")
            try:
                folder.rename(target)
            except OSError as exc:
                if target.exists():
                    raise DriverError(
                        f"חבילה בשם {manifest['name']} כבר קיימת") from exc
                raise
            return manifest
        finally:
            _clear(staging)

    @staticmethod
    def _verify_files(folder: Path, manifest: dict) -> None:
        declared = {f["path"]: f["sha256"] for f in manifest["files"]}
        present = {
            p.relative_to(folder).as_posix()
            for p in folder.rglob("*") if p.is_file()
        } - {UNVERIFIED}
        extra = sorted(present - set(declared))
        if extra:
            raise DriverError(f"קובץ בארכיון שאינו במניפסט: {_shown(extra[0])}")
        for rel, sha in declared.items():
            path = inside(folder / rel, folder)
            if path is None or not path.is_file():
                raise DriverError(f"חסר קובץ בארכיון: {_shown(rel)}")
            if _sha256(path) != sha:
                raise DriverError(f"אימות נכשל: {_shown(rel)} אינו תואם ל-sha256")
