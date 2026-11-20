"""אילו אימג'ים עוד בלי אינדקס או רשימת BLAKE3 -- ומה המפעיל רואה על זה (#3502).

גל של מנוע החתיכות אינו יוצא בלי רשימה (נדב 15/11: "הרשימה חובה"; `server/rounds/indexprep.py`). אימג' שנקלט
לפני #3357 אין לו אחת, והגל הראשון שנפתח עליו המתין לגזירה שלה **486 שניות** מול כיתה (ברזל, 15/11,
`img_3fa17f`, 18.5GB דחוס). עכשיו האינדקס נגזר בקליטה (`capture._index_at_ingest`), ואימג' ישן מקבל אותו
ברקע אחרי שדרוג (`readbackhash.run_forever`, מיד בעלייה). **מה שנוסף כאן הוא מה שרואים**: שורת בריאות
(`health_checks`) והדפסה בסוף `tools/server-upgrade.sh` (`python3 -m server.library.indexwait`).

**זמן משוער:** `ESTIMATE_BPS` -- 18.5GB ב-486 שניות = 38MB/s (ברזל 15/11, #3502); 1.7GB ב-43 שניות = 40MB/s
(מעבדת VM 14/11, #3137, `indexprep.ceiling_s`). זו הערכה לפי גודל הקבצים הדחוסים, לא הבטחה, והיא נאמרת "בערך".

**מה השורה אומרת**, כל אחד בשמו (עיקרון 5):

- אין `chunkcast` עם `b3list` בשרת, ויש אימג' בלי רשימה -- **אדום**: הגל שלו לא יצא ואין מה שיגזור אותה.
- המעבר ברקע ניסה ולא הצליח (`readbackhash.failed`) -- **אדום**, עם הסיבה.
- אימג'ים שעוד נגזרים -- **צהוב**: כמה, אחוז לכל אחד (כשהוא נגזר עכשיו) ובערך כמה זמן. אם נפתח סבב על אחד מהם, זה נאמר
  בשמו: הסבב אינו נחסם, וגל שלו ימתין לרשימה.
"""

from __future__ import annotations

import sys
from pathlib import Path

from server.agent import readbackhash
from server.library.images import ImageLibrary, guarded_image_path
from server.system.health_rows import check

#: בייטים דחוסים בשנייה של הגזירה (מקור וסביבה במסמך המודול).
ESTIMATE_BPS = 40_000_000
DEFAULT_IMAGES = "/srv/imagesrv/images"


def awaiting(library) -> list[dict]:
    """[{image_id, name, bytes, parts}] לכל אימג' מופץ שיש בו מחיצה בלי אינדקס או רשימה. `bytes` = הבייטים
    הדחוסים של הקבצים שחסר להם משהו."""
    found = []
    for image_id, manifest in sorted(library.scan().items()):
        if readbackhash.format_problem(manifest) is not None:
            continue
        image_dir = manifest.get("_dir") or ""
        parts = [p for p in manifest.get("partitions") or [] if readbackhash.lacks_list(image_dir, p)]
        if not parts:
            continue
        size = 0
        for part in parts:
            path = guarded_image_path(image_dir, part.get("file"))
            try:
                size += path.stat().st_size if path is not None else 0
            except OSError:
                pass
        found.append({"image_id": image_id, "name": manifest.get("name") or image_id, "bytes": size,
                      "parts": [p.get("index") for p in parts]})
    return found


def minutes(row: dict) -> int:
    """בערך כמה דקות נשארו: הבייטים שנשארו (אחרי האחוז שכבר נגזר) לפי `ESTIMATE_BPS`, לפחות דקה."""
    got = readbackhash.percent(row["image_id"]) or 0
    return max(1, round(row["bytes"] * (100 - got) / 100 / ESTIMATE_BPS / 60))


def _open_rounds(conn) -> set[str]:
    """האימג'ים שיש להם עכשיו סבב פתוח (חדר או הפצה)."""
    found: set[str] = set()
    for table, states in (("room_rounds", "('active', 'closing')"), ("sessions", "('open', 'running')")):
        found.update(r[0] for r in conn.execute(f"SELECT image_id FROM {table} WHERE state IN {states}").fetchall())
    return found


def health_checks(ctx) -> list[dict]:
    """שורה אחת, ורק כשיש מה לומר. ספרייה שאי אפשר לקרוא היא שורה אדומה -- לא היעדר שורה."""
    library = getattr(ctx, "library", None)
    if library is None:
        return []
    try:
        rows = awaiting(library)
        tool = readbackhash.b3_tool() if rows else True
        failures = readbackhash.failed()
        opened = _open_rounds(ctx.conn) if rows else set()
    except Exception as exc:                                  # noqa: BLE001 -- "לא הצלחנו לבדוק" אינו "אין חסרים"
        return [check("image_index", "רשימות BLAKE3 של אימג'ים", "unknown", f"לא ניתן לבדוק: {type(exc).__name__}: {exc}")]
    if not rows:
        return []
    names = ", ".join(r["name"] for r in rows[:3]) + (f" ועוד {len(rows) - 3}" if len(rows) > 3 else "")
    if tool is None:
        return [check("image_index", "רשימות BLAKE3 של אימג'ים", "bad",
                      f"{len(rows)} אימג'ים בלי רשימת BLAKE3 ({names}), ואין בשרת chunkcast שמייצר אותה -- גל שלהם לא "
                      "יצא. יש לבנות אותו מחדש (tools/server-tools-build.sh)")]
    broken = [f"{r['name']}: {failures[r['image_id']]}" for r in rows if r["image_id"] in failures]
    if broken:
        return [check("image_index", "רשימות BLAKE3 של אימג'ים", "bad",
                      "הגזירה נכשלה -- " + "; ".join(broken[:3]) + (f" ועוד {len(broken) - 3}" if len(broken) > 3 else ""))]
    parts = []
    for r in rows:
        got = readbackhash.percent(r["image_id"])
        parts.append(f"{r['name']} ({'%d%%' % got if got is not None else 'בתור'}, בערך {minutes(r)} דק')")
    detail = f"{len(rows)} אימג'ים עוד בלי רשימת BLAKE3, נגזרת ברקע: " + ", ".join(parts[:5])
    if len(parts) > 5:
        detail += f" ועוד {len(parts) - 5}"
    live = [r["name"] for r in rows if r["image_id"] in opened]
    if live:
        detail += (f". סבב פתוח על {', '.join(live)}: הוא אינו נחסם, והגל שלו ימתין לרשימה "
                   "(הכנת האימג' לשידור במסך החדר)")
    return [check("image_index", "רשימות BLAKE3 של אימג'ים", "warn", detail)]


def main(argv: list[str] | None = None) -> int:
    """`python3 -m server.library.indexwait [תיקיית-ספרייה]` -- ההדפסה של `tools/server-upgrade.sh`. קוד 0 = נבדק
    (גם כשיש מי שמחכה: זה מצב, לא כישלון); 2 = לא נבדק."""
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else DEFAULT_IMAGES)
    try:
        rows = awaiting(ImageLibrary(root))
        tool = readbackhash.b3_tool() if rows else True
    except Exception as exc:                                  # noqa: BLE001
        print(f"לא נבדק אילו אימג'ים עוד בלי רשימת BLAKE3 ({root}): {type(exc).__name__}: {exc}")
        return 2
    if not rows:
        print("כל האימג'ים בספרייה כבר עם אינדקס ורשימת BLAKE3")
        return 0
    total = sum(r["bytes"] for r in rows)
    print(f"{len(rows)} אימג'ים עוד בלי אינדקס או רשימת BLAKE3 -- נגזרים ברקע אחרי העלייה, בערך "
          f"{max(1, round(total / ESTIMATE_BPS / 60))} דקות בסך הכול; גל על אימג' כזה ימתין לרשימה: "
          + ", ".join(f"{r['name']} (~{minutes(r)} דק')" for r in rows))
    if tool is None:
        print("אין בשרת chunkcast שמייצר רשימות BLAKE3 -- הן לא ייגזרו עד שייבנה מחדש (tools/server-tools-build.sh)")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
