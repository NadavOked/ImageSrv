"""אימות מלא בפקודה אחת — הטסטים בקבוצות + סימולציית הקצה-לקצה.

תוכנית הסימולציה (בדיקה 0.4) דורשת ריצה אחת שמסכמת הכול, מדווחת
לפי קבוצות כדי שיהיה ברור אם נפל משהו בפונקציונליות או בבידוד,
ומציינת דילוגים — על השרת (לינוקס) היעד הוא אפס.

    python tools/verify.py            # הכול
    python tools/verify.py --no-sim   # בלי הסימולציה (מהיר)

עם `IMAGESRV_REQUIRE_NATIVE=1` הדיווח על דילוגים הופך לפסק דין: ריצה
עם ולו דילוג אחד אינה ירוקה. זה המצב הנכון על שרת המעבדה וב-CI, שם
הכלים אמורים להיות — "ירוק, אבל 22 דילוגים" הוא בדיוק המשפט שהחביא
שלוש חבילות שלא רצו (#52).

היוצא מן הכלל הוא **דילוג מוצהר** (#295): טסט שדורש כלי הקיים רק על
תחנת הפיתוח — ‏PowerShell, שאינו על שרת המעבדה בכוונה. הוא נספר בנפרד,
מדווח בשמו, ואינו מפיל. אחרת החבילה היתה אדומה שם לתמיד, ואדום קבוע
מנרמל את עצמו עד שכשל אמיתי נבלע בתוכו.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NATIVE_FLAG = "IMAGESRV_REQUIRE_NATIVE"

#: שם קבוצה ← קבצי הטסטים שלה (יחסית לשורש הריפו).
GROUPS: dict[str, list[str]] = {
    "סוכן ואתחול": [
        "tests/test_agent.py", "tests/test_boot_files.py",
        "tests/test_grub_menu.py", "tests/test_installer_matches_generator.py",
        "tests/test_timeouts.py", "tests/test_initramfs_modules.py",
    ],
    "שרת וקונסולה": [
        "tests/test_server_core.py", "tests/test_server_api.py",
        "tests/test_server_net.py", "tests/test_server_dhcp.py",
        "tests/test_server_users.py", "tests/test_server_library.py",
        "tests/test_server_room.py", "tests/test_server_order.py",
        "tests/test_server_health.py", "tests/test_console_brand.py",
        "tests/test_archive.py", "tests/test_capture.py", "tests/test_station.py",
        "tests/test_wol.py", "tests/test_server_streams.py",
        "tests/test_concurrency.py",
    ],
    "שידור ו-fanout": ["tests/test_sender.py", "tests/test_fanout.py"],
    "אתחול, VLAN ורג'יסטרי": [
        "tests/test_boot_http.py", "tests/test_boot_loop.py",
        "tests/test_hello_vlan.py", "tests/test_hivewrite.py",
    ],
    # הקבוצה הזאת בודקת את מכשיר המדידה עצמו: שדילוג במקום הלא נכון
    # נספר ככישלון, ושהריצה לא משאירה אחריה תהליך שידור חי.
    "מכשיר הבדיקה": ["tests/test_hygiene.py"],
}

TALLY = re.compile(r"(\d+) (passed|failed|skipped|error)")

#: התג ש-`tests/native.py` מדפיס בסוף ריצה שהיו בה דילוגים מוצהרים.
#: ‏ASCII, כי בקונסולת cp1252 העברית באותה שורה נבלעת ב-backslashreplace.
DECLARED = re.compile(r"\[declared-skips=(\d+)\]")


def uncovered_test_files() -> list[str]:
    """קבצי טסט שקיימים ב-tests/ ואינם באף קבוצה.

    ‏"אימות מלא" נמדד לפי מה שרץ, לא לפי היעדר כישלונות: קובץ שנוסף
    ל-tests/ ונשכח כאן פשוט לא הורץ, והסיכום היה מדפיס "ירוק" בדיוק
    כמו אילו עבר. שלושה קבצים (‏boot_http, ‏hello_vlan, ‏hivewrite) אכן
    נשכחו ככה. הבדיקה הזו הופכת שכחה כזו לכישלון גלוי.
    """
    listed = {f for files in GROUPS.values() for f in files}
    found = {f"tests/{p.name}" for p in (REPO / "tests").glob("test_*.py")}
    return sorted(found - listed)


def run_pytest(files: list[str]) -> dict[str, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *files, "-q"],
        capture_output=True, cwd=REPO, stdin=subprocess.DEVNULL,
        encoding="utf-8", errors="replace",  # פלט הטסטים עברי; בווינדוס הלוקאל cp1252
    )
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "declared": 0}
    for line in proc.stdout.splitlines():
        for num, kind in TALLY.findall(line):
            counts[kind] = int(num)
        if found := DECLARED.search(line):
            counts["declared"] = int(found.group(1))
    if proc.returncode not in (0, 1) or (proc.returncode and not counts["failed"]):
        counts["error"] = counts["error"] or 1
        print(proc.stdout[-2000:], file=sys.stderr)
    return counts


def main() -> int:
    # קונסולת Windows היא cp1252 כברירת מחדל — העברית שלנו צריכה UTF-8.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    with_sim = "--no-sim" not in sys.argv
    width = max(len(name) for name in GROUPS) + 2
    total_failed = total_skipped = total_declared = 0

    if missing := uncovered_test_files():
        print("FAIL  קבצי טסט שלא רצו — אינם באף קבוצה ב-GROUPS:")
        for path in missing:
            print(f"        {path}")
        print("\nהוסף אותם לקבוצה. עד אז אין כאן אימות מלא.")
        return 1

    for name, files in GROUPS.items():
        c = run_pytest(files)
        total_failed += c["failed"] + c["error"]
        # דילוג מוצהר אינו "לא בדקנו בטעות" — נספר לחוד, ואינו מפיל (#295).
        undeclared = c["skipped"] - c["declared"]
        total_skipped += undeclared
        total_declared += c["declared"]
        mark = "PASS" if not (c["failed"] or c["error"]) else "FAIL"
        skips = f", {undeclared} דילוגים" if undeclared else ""
        skips += f", {c['declared']} מוצהרים" if c["declared"] else ""
        print(f"{mark}  {name:<{width}} {c['passed']} עברו"
              f"{', ' + str(c['failed'] + c['error']) + ' נפלו' if mark == 'FAIL' else ''}{skips}")

    if with_sim:
        sim = subprocess.run(
            [sys.executable, "tools/e2e_simulation.py"],
            capture_output=True, cwd=REPO, stdin=subprocess.DEVNULL,
            encoding="utf-8", errors="replace",
        )
        mark = "PASS" if sim.returncode == 0 else "FAIL"
        if sim.returncode:
            total_failed += 1
            print(sim.stdout[-2000:], sim.stderr[-1000:], file=sys.stderr)
        print(f"{mark}  {'סימולציה קצה-לקצה':<{width}}")

    print()
    if total_failed:
        print(f"נפל: {total_failed}. לא ממשיכים למעבדה לפני שזה ירוק.")
        return 1
    declared_note = (f" {total_declared} דילוגים מוצהרים (תחנת פיתוח בלבד)."
                     if total_declared else "")
    if total_skipped:
        strict = os.environ.get(NATIVE_FLAG, "").strip().lower() not in (
            "", "0", "no", "false")
        # דילוג הוא "לא בדקנו", ולכן במקום שהכלים אמורים להיות הוא כישלון
        # ולא הערה בשוליים. בעמדת פיתוח (ווינדוס) הדגל כבוי וזו אכן הערה.
        print(f"{'נפל' if strict else 'ירוק, אבל'} {total_skipped} דילוגים"
              f" — על שרת המעבדה היעד הוא אפס.{declared_note}")
        return 1 if strict else 0
    print(f"ירוק, אפס דילוגים לא-מוצהרים.{declared_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
