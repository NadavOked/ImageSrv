#!/usr/bin/env python3
"""‏#1128 (R41 §4.3): אימות אימג'ים מול ה-`manifest.json` שלהם — על השרת
ועל יעד הגיבוי. ‏rsync שסיים אינו "גיבוי תקין": כל מחיצה נבדקת ב-sha256
מול המניפסט, וזו הראיה החיובית (עיקרון 5).

    python3 tools/verify-images.py /backup/imagesrv/images
    python3 tools/verify-images.py /srv/imagesrv/images --quiet

יציאה: 0 = כל האימג'ים אומתו · 1 = חסר/פגום (הפירוט בפלט) · 3 = לא נמצא
אף אימג' — "לא היה מה לבדוק" אינו "נבדק ותקין", אלא אם `--allow-empty`.
תיקיות `.capture-*`/`.import-*` (קליטה/ייבוא באמצע) מדולגות בשמן; מניפסט
שלצדו `manifest.unverified` נספר כ"לא מאומת" ואינו נחשב תקין.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def in_transient_dir(manifest: Path, root: Path) -> bool:
    return any(part.startswith((".capture-", ".import-"))
               for part in manifest.relative_to(root).parts)


def verify_image(manifest: Path, say) -> tuple[int, list[str]]:
    """(מחיצות שאומתו, רשימת כשלים) לאימג' אחד."""
    image_dir = manifest.parent
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return 0, [f"BAD MANIFEST {manifest}: {exc}"]
    parts = m.get("partitions")
    if not isinstance(parts, list):
        return 0, [f"BAD MANIFEST {manifest}: no partitions list"]
    ok, bad = 0, []
    for part in parts:
        name, expected = part.get("file"), part.get("sha256")
        if not name or not expected:
            continue                      # swap: file/sha256=null לפי החוזה
        path = image_dir / name
        if not path.is_file():
            bad.append(f"MISSING {path}")
            continue
        got = sha256_of(path)
        if got != expected:
            bad.append(f"BAD SHA256 {path}: {got} != {expected}")
            continue
        ok += 1
        say(f"OK {path}")
    return ok, bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path, help="תיקיית האימג'ים (השרת או יעד הגיבוי)")
    ap.add_argument("--quiet", action="store_true", help="בלי שורת OK לכל מחיצה")
    ap.add_argument("--allow-empty", action="store_true",
                    help="אפס אימג'ים הוא תוצאה תקינה (תיקייה טרייה)")
    args = ap.parse_args(argv)
    say = (lambda _line: None) if args.quiet else print
    root = args.root
    if not root.is_dir():
        print(f"NOT A DIRECTORY {root}", file=sys.stderr)
        return 1
    images = parts_ok = unverified = 0
    failures: list[str] = []
    for manifest in sorted(root.rglob("manifest.json")):
        if in_transient_dir(manifest, root):
            say(f"SKIP (in progress) {manifest.parent}")
            continue
        if (manifest.parent / "manifest.unverified").exists():
            unverified += 1
            failures.append(f"UNVERIFIED {manifest.parent} (manifest.unverified present)")
            continue
        n, bad = verify_image(manifest, say)
        images += 1
        parts_ok += n
        failures.extend(bad)
    for line in failures:
        print(line, file=sys.stderr)
    print(f"verified {images} images, {parts_ok} partitions, "
          f"{len(failures)} problems, {unverified} unverified")
    if failures:
        return 1
    if images == 0 and not args.allow_empty:
        print(f"NO IMAGES under {root} — nothing was verified (use --allow-empty if expected)",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
