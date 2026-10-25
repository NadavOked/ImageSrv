"""‏#1545 — דיסק עם GPT ראשי פגום נדחה בשחזור, אף שהוא בדיוק הדיסק שצריך אותו.

ברזל 17/10, מחשב הבנייה (sgdisk 1.0.10): סבב שנקטע ב-16/10 השאיר את 34
הסקטורים הראשונים של הדיסק זבל — ה-MBR, ה-header הראשי ומערך המחיצות
הראשי. ה-header והמערך הגיבויים בסוף הדיסק תקינים. "שחזור לדיסק הזה" נכשל
ב-`sgdisk דחה את תוכנית השחזור — הדיסק לא שונה`.

**הפיקסצ'ר הוא הבייטים מהברזל**, לא שבירה מדומה (‏`tests/fixtures/gpt-metal-1017/`:
‏head = 34 הסקטורים הראשונים, ‏tail = 33 האחרונים, ‏`sg.out` = פלט sgdisk שם),
על קובץ דליל בגודל המקורי (500118192 סקטורים). ניסיון ראשון שבר רק CRC ב-
header ובמערך על דיסק עם MBR מגן תקין — ו-sgdisk טען אותו בשלום (rc=0 על
Testrunner). מה שמכשיל את הטעינה הוא **MBR לא תקין יחד עם GPT פגום**:
‏sgdisk הלא-אינטראקטיבי לא בוחר, ומדפיס `Invalid partition data!`.

השורש ב-`gptcl.cc`: כש-`LoadPartitions` נכשל, הענף החלופי מטפל רק ב-
‏`-l`/`-o`/`-v`/`-z`/`-Z`, מתעלם מ-`-n`/`-t`, ו-`retval = 2` מושם **אחרי**
הלולאה — ולכן:

* ‏`--pretend --clear -n …` על הדיסק החי יוצא 2 על כל תוכנית (השער).
* ‏`--zap-all` מוחק (אין `--pretend`) **ויוצא 2** — ו-`apply_gpt` נכשל אחרי
  שהדיסק כבר נמחק. תיקון השער לבדו היה מזיז את הכישלון שורה אחת.

sgdisk **אמיתי**, תחת busybox ash כמו בסוכן. לינוקס בלבד (sgdisk+busybox).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_json_escape import BUSYBOX, bb, source_line

SGDISK = shutil.which("sgdisk")

pytestmark = requires_native(
    ("busybox", BUSYBOX), ("sgdisk", SGDISK), posix=True,
    why="ההתנהגות שנבדקת היא של sgdisk האמיתי על טבלה פגומה — זיוף היה בודק את ההנחה שלנו")

METAL = Path(__file__).parent / "fixtures" / "gpt-metal-1017"
SECTORS = 500118192          # דיסק מחשב הבנייה; הסקטור השמיש האחרון: 500118158
HEAD, TAIL = 34, 33          # סקטורים
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
BASIC = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
SHA = "a" * 64
ROWS = [
    f"1|{ESP}|esp|vfat|2048|{65536 * 512}|p1.img.zst|{SHA}|false||",
    f"2|{BASIC}|windows|ntfs|67584|{32768 * 512}|p2.img.zst|{SHA}|true||",
]


def _sg(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([SGDISK, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


def _blank(tmp_path: Path) -> Path:
    dev = tmp_path / "dev"
    dev.mkdir()
    img = dev / "sda"
    with img.open("wb") as f:
        f.truncate(SECTORS * 512)     # דליל: רק הקצוות נכתבים, כאן ועל ידי sgdisk
    return img


def _metal(tmp_path: Path) -> Path:
    """הדיסק מהברזל: הבייטים של שני הקצוות במקומם, ובאמצע אפסים."""
    img = _blank(tmp_path)
    head = (METAL / "gpt-head.bin").read_bytes()
    tail = (METAL / "gpt-tail.bin").read_bytes()
    assert len(head) == HEAD * 512 and len(tail) == TAIL * 512
    with img.open("r+b") as f:
        f.write(head)
        f.seek((SECTORS - TAIL) * 512)
        f.write(tail)
    return img


def _clean(tmp_path: Path) -> Path:
    """דיסק תקין באותו גודל, עם טבלה קודמת שאינה התוכנית."""
    img = _blank(tmp_path)
    made = _sg("-a", "1", "-n", "1:2048:40959", "-t", "1:8300",
               "-n", "2:40960:120000", "-t", "2:8300", str(img))
    assert made.returncode == 0, made.stdout + made.stderr
    return img


def _ends(img: Path) -> bytes:
    """המקומות היחידים ש-sgdisk נוגע בהם. קובץ של 256GB לא נקרא כולו."""
    with img.open("rb") as f:
        head = f.read(HEAD * 512)
        f.seek((SECTORS - TAIL) * 512)
        return head + f.read()


def _run(tmp_path: Path, call: str, rows: list[str]) -> str:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    plan = tmp_path / "plan.txt"
    plan.write_text("\n".join(rows) + "\n", encoding="utf-8")
    body = (
        f'RUN_DIR="{run.as_posix()}"; LOG_FILE="{(tmp_path / "log").as_posix()}"\n'
        f'DEVROOT="{(tmp_path / "dev").as_posix()}"\n'
        'log() { echo "LOG $*" >> "$LOG_FILE"; }\n'
        # waits.sh: apply_gpt rereads through reread_table (#1594). Without it the call is
        # "not found", and the log says the kernel refused -- the agent sources it first.
        + source_line("waits.sh", "restore.sh", "expand.sh", "writemark.sh")
        + f'manifest_plan() {{ cat "{plan.as_posix()}"; }}\n'
        'json_get() { case "$2" in .sector_size) echo 512 ;; .scheme) echo gpt ;; *) echo null ;; esac; }\n'
        f'blockdev() {{ case "$1" in --getss) echo 512 ;; --getsz) echo {SECTORS} ;; *) return 0 ;; esac; }}\n'
        'target_write_started() { :; }\n'
        'verify_table() { :; }   # הקריאה בחזרה נבדקת למטה עם sgdisk אמיתי\n'
        f'if {call} sda manifest.json; then echo OK; else echo "FAIL $PLAN_ERROR"; fi\n'
    )
    return bb(tmp_path, body).strip()


def test_the_fixture_is_the_metal_state_and_sgdisk_behaves_as_gptcl_cc_says(tmp_path):
    """הנחות התיקון, נמדדות ולא מצוטטות: הפקודה של השער הישן על הבייטים
    מהברזל יוצאת 2 עם אותו פלט כמו שם (`sg.out`), ו-`--zap-all` עליהם מוחק
    ויוצא ≠0, והשני יוצא 0. אם גרסת sgdisk אחרת מתנהגת אחרת — זה ייפול כאן."""
    img = _metal(tmp_path)
    old_gate = _sg("-a", "1", "--pretend", "--clear", "-n", "1:2048:67583", str(img))
    text = old_gate.stdout + old_gate.stderr
    assert old_gate.returncode == 2, (old_gate.returncode, text)
    for line in ("Main and backup partition tables differ!", "Main header: ERROR",
                 "Backup header: OK", "Main partition table: ERROR",
                 "Backup partition table: OK", "Invalid partition data!",
                 "will override earlier problems"):
        assert line in text, (line, text)
        assert line in (METAL / "sg.out").read_text(encoding="utf-8"), line   # אותו פלט כמו על הברזל
    first = _sg("--zap-all", str(img))
    assert first.returncode != 0, "‏--zap-all על הטבלה הזו: gptcl.cc מחזיר 2"
    second = _sg("--zap-all", str(img))
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Invalid partition data!" not in second.stdout + second.stderr


def test_a_damaged_disk_passes_the_plan_gate_and_is_not_touched(tmp_path):
    img = _metal(tmp_path)
    before = _ends(img)
    assert _run(tmp_path, "validate_restore_plan", ROWS) == "OK"
    assert _ends(img) == before, "השער כתב לדיסק"
    assert not (tmp_path / "run" / "preflight.disk").exists()


def test_apply_gpt_writes_the_new_table_over_a_damaged_one(tmp_path):
    img = _metal(tmp_path)
    assert _run(tmp_path, "apply_gpt", ROWS) == "OK"
    check = _sg("-v", str(img))
    assert check.returncode == 0 and "No problems found" in check.stdout, check.stdout + check.stderr
    p2 = _sg("-i", "2", str(img)).stdout
    assert "First sector: 67584 " in p2 and "Last sector: 100351 " in p2, p2
    # המחיקה הראשונה יצאה ≠0 (המבחן הקודם), והשנייה היא שאישרה דיסק נקי
    assert "again on the wiped disk" in (tmp_path / "log").read_text(encoding="utf-8")


def _bad_plans() -> dict[str, list[str]]:
    # שתיהן עוברות את ה-awk (בתוך גודל הדיסק, בלי חפיפה) — רק sgdisk יודע
    # שהן נוגעות באזורי ה-GPT. כך מוכח שההדמיה על הקובץ עדיין בודקת משהו.
    into_backup_gpt = ROWS[:1] + [ROWS[1].replace(
        f"|{32768 * 512}|", f"|{(500118170 - 67584 + 1) * 512}|")]
    over_primary_gpt = [ROWS[0].replace("|2048|", "|1|")] + ROWS[1:]
    return {"into-backup-gpt": into_backup_gpt, "over-primary-gpt": over_primary_gpt}


def test_an_invalid_plan_is_still_refused_by_name_on_damaged_and_clean_disks(tmp_path):
    for name, rows in _bad_plans().items():
        for kind, make in (("metal", _metal), ("clean", _clean)):
            sub = tmp_path / f"{name}-{kind}"
            sub.mkdir()
            img = make(sub)
            before = _ends(img)
            out = _run(sub, "validate_restore_plan", rows)
            assert out == "FAIL sgdisk דחה את תוכנית השחזור — הדיסק לא שונה", (name, kind, out)
            assert _ends(img) == before, (name, kind)
            out = _run(sub, "apply_gpt", rows)
            assert out.startswith("FAIL sgdisk דחה"), (name, kind, out)
            assert _ends(img) == before, f"{name}/{kind}: apply_gpt נגע בדיסק אחרי סירוב"
