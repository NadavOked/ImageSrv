#!/usr/bin/env python3
"""בניית חבילת דרייברים מ-driver pack של היצרן (‏#959, החוסם המעשי של #720).

המפעיל מוריד CAB של HP/Dell/Lenovo (או פורש EXE לתיקייה), והכלי בונה
את מה שהשרת מקבל ב-`POST /api/console/drivers/upload`: תיקייה אחת עם
`manifest.json` — שם, כללי התאמה, ‏sha256 לכל קובץ — בתוך tar.

    python tools/drivers/build-package.py sp150000.cab \\
        --vendor "HP" --model "HP EliteDesk 800 G6 Desktop Mini PC" --out hp-800-g6.tar

    python tools/drivers/build-package.py <תיקייה-שנפרשה> --vendor LENOVO \\
        --model "ThinkCentre M720q" --version 2.1

`--vendor` ו-`--model` הם מה שהמכונה מדווחת ב-hello (‏`dmi.sys_vendor`,
‏`dmi.product_name`/`product_version`) — מוצגים בכרטיס המכונה בקונסולה.

**כללי ההתאמה שנכתבים** (‏`docs/interfaces.md` §17):

1. ‏`{"vendor", "model"}` — כשניתן `--vendor`. בלעדיו הכלל אינו נכתב
   (השרת דורש את שניהם) והסיכום אומר זאת במפורש.
2. ‏`{"pci_any": [...]}` — כל `PCI\\VEN_xxxx&DEV_xxxx` מכל INF שה-`Class`
   שלו הוא רשת או אחסון (‏Net / HDC / SCSIAdapter): אלה שני ה-classes
   היחידים שהסוכן מדווח במלאי (‏`agent/lib/hw/inventory.sh`), ומזהה של
   כרטיס מסך או שבב אודיו לא יתאים לעולם. ‏SUBSYS/REV נזרקים — המלאי
   אינו מדווח אותם. ‏INF אינו נוקב בקוד class, ולכן המזהה נכתב בלי
   class (‏`8086:15bc`) והשרת מתאים לפי vendor:device.

המניפסט עובר את `server.library.drivers.validate_manifest` **לפני** שה-tar
נכתב — הכלי אינו יכול לבנות מה שהשרת ידחה, כי זו אותה פונקציה.

יציאה: ‏0 כשה-tar נכתב והסיכום הודפס; ‏1 על כל סירוב, בשמו.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.library.drivers import (FILES_LIMIT, NAME_RE, PCI_ANY_LIMIT,  # noqa: E402
                            safe_relative, validate_manifest)

#: ה-classes של INF שהמלאי יודע לזהות — רשת (02xxxx) ואחסון (01xxxx).
MATCHABLE_CLASSES = {"net", "hdc", "scsiadapter"}
HWID_RE = re.compile(r"PCI\\VEN_([0-9A-Fa-f]{4})&DEV_([0-9A-Fa-f]{4})")
CLASS_RE = re.compile(r"^\s*Class\s*=\s*\"?([A-Za-z0-9_]+)", re.MULTILINE | re.IGNORECASE)


class Refusal(Exception):
    pass


# --- פריסת CAB ---------------------------------------------------------------


def _extractor() -> list[str] | None:
    """פקודת הפריסה, בלי הארגומנטים התלויים בקובץ. ``None`` = אין."""
    if sys.platform == "win32":
        # **לא** `shutil.which("expand")`: ב-Git Bash הוא מוצא את `expand`
        # של coreutils (מרחיב טאבים), שהיה "מצליח" בלי לפרוש דבר.
        exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "expand.exe"
        if exe.is_file():
            return [str(exe), "-F:*"]
    if shutil.which("cabextract"):
        return ["cabextract", "-q", "-d"]
    for name in ("7z", "7za"):
        if shutil.which(name):
            return [name, "x", "-y", "-bso0", "-o"]
    return None


def extract_cab(cab: Path, dest: Path) -> None:
    tool = _extractor()
    if tool is None:
        raise Refusal("אין כלי לפריסת CAB: נדרש expand.exe (Windows), cabextract או 7z. "
                      "לחלופין פרוש את הקובץ ידנית והעבר את התיקייה.")
    if tool[0].endswith("expand.exe"):
        cmd = [*tool, str(cab), str(dest)]
    elif tool[0] == "cabextract":
        cmd = [*tool, str(dest), str(cab)]
    else:
        cmd = [*tool[:-1], f"-o{dest}", str(cab)]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise Refusal(f"פריסת ה-CAB נכשלה ({cmd[0]} יצא {proc.returncode}): "
                      f"{(proc.stderr or proc.stdout).strip()[:300]}")
    produced = [p for p in dest.rglob("*") if p.is_file()]
    if not produced:
        raise Refusal(f"{cmd[0]} יצא 0 אך לא פרש אף קובץ — ה-CAB ריק או שאינו CAB")
    # ‏expand.exe יוצא 0 ו**מעתיק** קובץ שאינו CAB אל היעד בשמו (נמדד 17/09):
    # יציאה 0 אינה ראיה. קובץ "שנפרש" שזהה בייט-בבייט למקור הוא העתק.
    size = cab.stat().st_size
    if any(p.stat().st_size == size and p.read_bytes() == cab.read_bytes() for p in produced):
        raise Refusal(f"{cmd[0]} העתיק את {cab.name} במקום לפרוש אותו — הקובץ אינו CAB")


# --- סריקת INF ---------------------------------------------------------------


def read_inf(path: Path) -> str:
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def scan_inf(text: str) -> tuple[str, list[str]]:
    """(‏class של ה-INF באותיות קטנות, מזהי `vendor:device` ממוינים בלי כפילויות)."""
    found = CLASS_RE.search(text)
    klass = found.group(1).lower() if found else ""
    ids = {f"{v.lower()}:{d.lower()}" for v, d in HWID_RE.findall(text)}
    return klass, sorted(ids)


def collect(root: Path) -> tuple[list[str], list[str], dict[str, int]]:
    """(נתיבי הקבצים, מזהי ה-PCI, ספירת INF לפי class שדולג)."""
    files: list[str] = []
    ids: set[str] = set()
    skipped: dict[str, int] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        raw = path.relative_to(root).as_posix()
        rel = safe_relative(raw)
        if rel is None:
            raise Refusal(f"נתיב שהשרת לא יקבל (לא ASCII, `..`, או תו בקרה): {raw!r}")
        if rel == "manifest.json":
            raise Refusal("התיקייה כבר מכילה manifest.json — הכלי כותב אותו בעצמו")
        files.append(rel)
        if path.suffix.lower() == ".inf":
            klass, inf_ids = scan_inf(read_inf(path))
            if klass in MATCHABLE_CLASSES:
                ids.update(inf_ids)
            else:
                skipped[klass or "?"] = skipped.get(klass or "?", 0) + 1
    if not files:
        raise Refusal("אין קבצים במקור")
    if len(files) > FILES_LIMIT:
        raise Refusal(f"{len(files)} קבצים — השרת מקבל עד {FILES_LIMIT}; פצל את החבילה")
    if len(ids) > PCI_ANY_LIMIT:
        raise Refusal(f"{len(ids)} מזהי PCI — השרת מקבל עד {PCI_ANY_LIMIT} בכלל אחד; "
                      "פצל את החבילה (רשת ואחסון בנפרד)")
    return files, sorted(ids), skipped


# --- המניפסט וה-tar -----------------------------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().encode("ascii", "ignore").decode()).strip("-")[:64]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(args: argparse.Namespace, root: Path, files: list[str],
                   ids: list[str], inf_count: int) -> dict:
    name = args.name or slug(f"{args.vendor or ''} {args.model}")
    if not NAME_RE.match(name):
        raise Refusal(f"שם החבילה {name!r} אינו תקין (ASCII, עד 64) — תן --name")
    rules: list[dict] = []
    if args.vendor:
        rules.append({"vendor": args.vendor.strip(), "model": args.model.strip()})
    if ids:
        rules.append({"pci_any": ids})
    if not rules:
        raise Refusal("אין על מה להתאים: אין --vendor (כלל דגם דורש vendor+model) "
                      "ואף INF של רשת/אחסון לא נקב במזהה PCI")
    description = args.description or " ".join(filter(None, [
        f"{args.vendor or ''} {args.model}".strip(),
        f"— driver pack {args.version}" if args.version else "",
        f"({inf_count} INF, built {dt.date.today().isoformat()})",
    ]))
    manifest = {
        "schema": 1, "name": name, "description": description, "match": rules,
        "files": [{"path": rel, "sha256": sha256_of(root / rel)} for rel in files],
    }
    problem = validate_manifest(manifest)
    if problem:
        raise Refusal(f"המניפסט שנבנה לא יתקבל בשרת: {problem}")
    return manifest


def write_tar(manifest: dict, root: Path, out: Path) -> None:
    name = manifest["name"]
    with tarfile.open(out, "w") as tar:
        blob = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        info = tarfile.TarInfo(f"{name}/manifest.json")
        info.size, info.mtime = len(blob), int(dt.datetime.now().timestamp())
        tar.addfile(info, io.BytesIO(blob))
        for entry in manifest["files"]:
            # רק קבצים רגילים — השרת דוחה פריט תיקייה/קישור (`_safe_members`).
            tar.add(root / entry["path"], arcname=f"{name}/{entry['path']}", recursive=False)


# --- הכניסה --------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="בניית חבילת דרייברים ל-ImageSrv מ-CAB או מתיקייה")
    ap.add_argument("source", help="קובץ .cab, או תיקייה שכבר נפרשה (CAB/EXE)")
    ap.add_argument("--model", required=True, help="dmi.product_name / product_version כפי שהמכונה מדווחת")
    ap.add_argument("--vendor", help="dmi.sys_vendor (HP / LENOVO / Dell Inc.); בלעדיו אין כלל דגם")
    ap.add_argument("--name", help="שם החבילה (ASCII); ברירת מחדל נגזרת מ-vendor+model")
    ap.add_argument("--version", help="גרסת ה-driver pack של היצרן — נכנסת ל-description")
    ap.add_argument("--description")
    ap.add_argument("--out", help="קובץ ה-tar (ברירת מחדל: <name>.tar בתיקייה הנוכחית)")
    return ap.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.source)
    with tempfile.TemporaryDirectory(prefix="imagesrv-drv-") as tmp:
        if source.is_dir():
            root = source
        elif source.is_file() and source.suffix.lower() == ".cab":
            root = Path(tmp) / "cab"
            root.mkdir()
            extract_cab(source, root)
        else:
            raise Refusal(f"המקור אינו תיקייה ואינו קובץ .cab: {source}")
        files, ids, skipped = collect(root)
        inf_total = sum(1 for f in files if f.lower().endswith(".inf"))
        manifest = build_manifest(args, root, files, ids, inf_total)
        out = Path(args.out) if args.out else Path(f"{manifest['name']}.tar")
        write_tar(manifest, root, out)
    size = out.stat().st_size
    used_inf = inf_total - sum(skipped.values())
    print(f"חבילה:      {manifest['name']}")
    print(f"דגם:        {args.vendor or '(בלי vendor — אין כלל דגם)'} · {args.model}")
    print(f"INF:        {inf_total} (רשת/אחסון: {used_inf}; דולגו: "
          + (", ".join(f"{k}={v}" for k, v in sorted(skipped.items())) or "0") + ")")
    print(f"מזהי PCI:   {len(ids)}" + ("" if ids else " — ההתאמה לפי דגם בלבד"))
    print(f"קבצים:      {len(files)}")
    print(f"tar:        {out} ({size:,} bytes)")
    print(f"sha256:     {sha256_of(out)}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # פלט עברי; בווינדוס הלוקאל cp1252
        stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(run())
    except Refusal as exc:
        print(f"build-package: {exc}", file=sys.stderr)
        sys.exit(1)
