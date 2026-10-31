"""האם האימג'ים שבספרייה נכנסים לכונן היעד — ואם לא, בכמה (‏#87).

הכלי שעונה על השאלה שנשאלת מול חומרה, בלי לנחש אותה מראש: מפעיל מודד
כונן יעד אחד, מוסר את המספר, והכלי מכריע לכל אימג' בספרייה.

    # על מכונת היעד, בבייטים ולא בג'יגה — מתוך מסך התחנה של ImageSrv:
    blockdev --getsize64 /dev/sda

    # על השרת, עם המספר שהתקבל:
    python tools/imagefit.py --images /var/lib/imagesrv/images --target-bytes 256060514304

‏`blockdev` ולא `lsblk`: זו הפקודה ש-`disk_fits` עצמו מריץ
(`agent/lib/restore/restore.sh`), והיא היחידה מבין השתיים שנארזת ב-initramfs
(`BINARIES` ב-`tools/build_initramfs.sh`). למדוד באותה פקודה שהקוד
משתמש בה מסלק מראש את "המספר שמדדתי אינו המספר שהקוד רואה".

בלי `--target-bytes` הכלי מדפיס רק **מה כל אימג' צריך**, ואומר איזו
פקודה למדוד — כי דרישה בלי כונן להשוות אליה אינה הכרעה.

מה הכלי **אינו** יודע, ובכוונה: האם מערכת הקבצים שבתוך המחיצה מסוגלת
להתכווץ עד לגודל שנדרש. זו מדידה על מערכת קבצים אמיתית (`ntfsresize
--info -f /dev/sdaN`), ולא שדה במניפסט — ‏`used_bytes` הוא `0` בכל
מניפסט שנקלט לפני #84, ודרישה שנשענת עליו הייתה מחזירה אפס.

יציאה: ‏0 כשכל האימג'ים שנבדקו נכנסים, ‏1 כשלפחות אחד אינו נכנס או
שלא ניתן היה להכריע לגביו. "לא הצלחנו להכריע" אינו יציאה 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.library.imagefit import expandable_candidate, shrink_bytes  # noqa: E402
from server.library.images import ImageLibrary, required_bytes  # noqa: E402

#: המדידה שהכלי מבקש, מילה במילה — כדי שלא תומצא מחדש בכל פעם, וכדי
#: שתהיה **אותה** פקודה שבדיקה 2.7 מריצה על הכונן עצמו.
MEASURE = "blockdev --getsize64 /dev/<disk>"


def gb(value: int) -> str:
    """ג'יגה עשרוני, כפי שיצרן הכונן מודד ולא כפי שווינדוס מציג."""
    return f"{value / 1_000_000_000:.1f}GB"


def verdict(manifest: dict, floor: int | None) -> tuple[str, list[str]]:
    """(מצב, שורות הסבר). המצב הוא `fits` / `shrink` / `unknown`."""
    need = required_bytes(manifest)
    if need is None:
        return "unknown", [
            "לא ניתן לגזור מהמניפסט כמה מקום האימג' צריך — "
            "חסרה גיאומטריה (`start_sector`/`size_bytes`) וגם `min_target_bytes` תקין.",
        ]

    lines = [f"צריך {need:,} בייט ({gb(need)})"]
    if floor is None:
        lines.append(f"כונן היעד לא נמדד — הרץ על מכונת היעד: {MEASURE}")
        return "unknown", lines

    # **ההכרעה "נכנס או לא" נגזרת מ-`required_bytes` בלבד** — אותה
    # פונקציה שבדיקה 2.7 אוכפת. ‏`shrink_bytes` עונה על שאלה אחרת
    # ("בכמה לכווץ"), ויש מניפסטים שאפשר להכריע עליהם בלעדיה.
    if need <= floor:
        lines.append(f"נכנס לכונן של {floor:,} בייט ({gb(floor)}), עם "
                     f"{floor - need:,} בייט להותיר")
        return "fits", lines

    lines.append(f"אינו נכנס: הכונן מחזיק {floor:,} בייט ({gb(floor)})")
    missing = shrink_bytes(manifest, floor)
    if missing is None:
        lines.append("אין במניפסט גיאומטריה מלאה, ולכן אי אפשר לגזור "
                     "בכמה לכווץ — רק שהאימג' אינו נכנס.")
        return "shrink", lines
    lines.append(f"הפריסה חייבת לאבד {missing:,} בייט ({gb(missing)}) — "
                 f"וזה **לא** ההפרש מהדרישה, כי היא מעוגלת כלפי מעלה למגה")
    part = expandable_candidate(manifest)
    if part is None:
        lines.append("אין במניפסט מחיצת `windows`/`linux` שאפשר לכווץ — "
                     "האימג' הזה אינו ניתן להתאמה אוטומטית.")
        return "shrink", lines

    size = part.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= missing:
        lines.append(f"המחיצה שהשחזור מותח היא #{part.get('index')} "
                     f"({part.get('fs')}), ואי אפשר לגזור ממנה כיווץ בגודל הזה.")
        return "shrink", lines

    lines.append(
        f"המחיצה שהשחזור מותח היא #{part.get('index')} ({part.get('fs')}), "
        f"{size:,} בייט — צריך לכווץ אותה ל-{size - missing:,} בייט לכל היותר")
    lines.append(
        "האם מערכת הקבצים מסוגלת לכך — `ntfsresize --info -f` על מחיצת המקור. "
        "זה לא נגזר מהמניפסט.")
    return "shrink", lines


def manifests(args: argparse.Namespace) -> list[tuple[str, dict]]:
    if args.manifest:
        raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        return [(raw.get("id") or args.manifest, raw)]
    found = ImageLibrary(args.images).scan()
    return sorted(found.items())


def main(argv: list[str] | None = None) -> int:
    # הפלט בעברית; בלי זה ההרצה נופלת במסופים שאינם UTF-8 (ווינדוס,
    # צינורות). ‏**בנקודת הכניסה ולא ברמת המודול**: הטסטים מייבאים מכאן
    # את `MEASURE`, ושינוי קידוד על ה-stdout ש-pytest לוכד הוא תופעת
    # לוואי שנשארת לכל שאר הריצה.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--images", help="שורש ספריית האימג'ים")
    src.add_argument("--manifest", help="קובץ manifest.json בודד")
    ap.add_argument("--target-bytes", type=int, default=None,
                    help=f"גודל כונן היעד בבייטים ({MEASURE})")
    args = ap.parse_args(argv)

    floor = args.target_bytes
    if floor is not None and floor <= 0:
        print(f"גודל כונן יעד חייב להיות חיובי, לא {floor}")
        return 1

    items = manifests(args)
    if not items:
        # ספרייה ריקה אינה "הכול נכנס". אין מה להכריע, וזו אינה הצלחה.
        print("לא נמצא אף מניפסט תקין.")
        return 1

    worst = 0
    for image_id, manifest in items:
        state, lines = verdict(manifest, floor)
        mark = {"fits": "נכנס", "shrink": "אינו נכנס", "unknown": "לא הוכרע"}[state]
        print(f"\n{image_id}  [{mark}]  {manifest.get('name', '')}")
        for line in lines:
            print(f"    {line}")
        worst = max(worst, 0 if state == "fits" else 1)
    print()
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
