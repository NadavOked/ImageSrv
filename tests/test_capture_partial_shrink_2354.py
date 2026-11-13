"""#2354 (הכרעת נדב 04/11, אפשרות א): קליטה שהתוכנית שלה לא נכנסת בזיכרון מכווצת
חלקית, עד מה שנכנס, והאימג' יוצא קצת גדול יותר. בלי אזהרה בחלון האישור.

ברזל 03/11, HP-BUILD-2 (v0.80.6): "להזזת 27706 MB נדרשים 30860 MB זיכרון, ויש
15156 MB". הטסטים מריצים את `capture_disk` האמיתי מול ntfsresize מזויף, שעקומת
ההזזה שלו היא זו שנמדדה על ברזל ב-26/10 (lab/metal-1026-shrink/reloc-curve.txt):
מתחת לסף, כל MB שהמחיצה מתכווצת הוא MB להזיז (16,639 ב-94.4GB, 21,882 ב-89.1GB).
הסף כאן נבחר כך שבגודל התוכנית יוצאים 27,706 MB של ה-Issue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH, posix
from test_capture_fast_startup_2213 import fast_run, no_source_writes
from test_capture_refusals import capture_run, refusal_reason
from test_direct_tail_1171 import NEARLY_FULL_MIN, WIN_BYTES, _capture, _ok
from test_directsend import TWO_PARTITIONS
from test_shrink_on_capture import (
    DATA_SECTORS, DATA_START, FS_MAP, FS_MAP_C_D, MIN_BYTES, MIN_D, NTFSRESIZE_OK, NTFSRESIZE_TWO,
    REC_START, SAY_YES, SGDISK_C_THEN_D, WIN_SECTORS, manifest, order, part, rewrites, shrink_target,
    source_writes, stubs, view_sys,
)

pytestmark = requires_native(("bash", BASH))

MIB = 1 << 20
CUR = WIN_SECTORS * 512
PLAN = shrink_target(MIN_BYTES, CUR)
PLAN_MOVES = 27706                       # MB, ה-Issue
ZERO = PLAN + PLAN_MOVES * 10**6         # מעל הגודל הזה אין מה להזיז
STORE_MB = 15156                         # ה-Issue: מה שיש ל-brd אחרי השמירה של ההעתקה
RESERVE_MB = 1024
MEM_16GB = {"MEM_KIB": str((STORE_MB + RESERVE_MB) * 1024)}
#: LC_SNAP_FIT_MB: הכיווץ החלקי נעצר במרחק הזה מהזיכרון
STEP = 1024 * MIB

#: NO_MEASURE_ON = המדידה ה-N של `-n` נכשלת (2 = הראשונה של החיפוש).
MEASURE = f'''  "-n "*) n=$(grep -c '^ntfsresize -n' "$RUN_DIR/order.log")
          [ "$n" != "${{NO_MEASURE_ON:-0}}" ] || {{ echo "ERROR: Could not read the MFT"; exit 1; }}
          left=$((${{ZERO_AT:-{ZERO}}} - $3)); [ "$left" -gt 0 ] || left=0
          echo "Needed relocations : 7 ($((left / 1000000)) MB)"; exit 0 ;;
'''
CURVE = NTFSRESIZE_OK.replace(
    '  "-n "*) echo "Needed relocations : 2560 (${RELOC_MB:-10} MB)"; exit 0 ;;\n', MEASURE)
assert CURVE != NTFSRESIZE_OK, "הזיוף לא הוחלף: הטסטים היו רצים על הזזה קבועה"
#: C: על העקומה, D: מזיזה 10 MB -- לכל מחיצה מאגר משלה.
CURVE_C = NTFSRESIZE_TWO.replace(
    '  "-n "*) echo "Needed relocations : 2560 (10 MB)"; exit 0 ;;\n',
    '  "-n "*lc-src-4) echo "Needed relocations : 2560 (10 MB)"; exit 0 ;;\n' + MEASURE)
assert CURVE_C != NTFSRESIZE_TWO, "הזיוף של שתי המחיצות לא הוחלף"


def moved(size: int, zero: int = ZERO) -> int:
    return max(0, zero - size) // 10**6


def need(mb: int, hibernated: bool = False) -> int:
    return mb + mb // 10 + 256 + (128 if hibernated else 0)


def edge(store_mb: int, hibernated: bool = False, zero: int = ZERO) -> int:
    """הגודל הקטן ביותר שההזזה שלו נכנסת במאגר, לפי העקומה."""
    size = min(zero, CUR)
    while need(moved(size - MIB, zero), hibernated) <= store_mb:
        size -= MIB
    return size


def as_far_as_the_memory_goes(size: int, store_mb: int, hibernated: bool = False) -> None:
    assert PLAN < size < CUR, (PLAN, size, CUR)
    assert (CUR - size) % MIB == 0, "ההפרש בכפולות MiB, אחרת הזנב אינו מיושר"
    assert need(moved(size), hibernated) <= store_mb, (size, moved(size))
    assert size - edge(store_mb, hibernated) <= STEP, (size, edge(store_mb, hibernated))


def probes(run: Path) -> list[int]:
    return [int(s.split()[3]) for s in order(run) if s.startswith("ntfsresize -n")]


def captured(box: Path, out: str) -> None:
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")


def test_16gb_against_27_7gb_shrinks_partly_instead_of_failing(tmp_path):
    """המקרה של ה-Issue: הקליטה מצליחה, והמחיצה מתכווצת עד מה שנכנס בזיכרון."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES, env=MEM_16GB)
    captured(box, out)
    win = part(manifest(run), 3)
    size = win["size_bytes"]
    as_far_as_the_memory_goes(size, STORE_MB)
    assert win["shrunk_from_bytes"] == CUR
    # מה שהמניפסט אומר הוא מה שרץ: אותו גודל נמדד, כווץ, נחתך ונקרא.
    steps, dev = order(run), posix(box / "dev")
    assert size in probes(run), probes(run)
    assert f"ntfsresize -s {size} --no-progress-bar {dev}/mapper/lc-src-3" in steps, steps
    assert f"dmsetup create lc-cut-3 --table 0 {size // 512} linear {dev}/mapper/lc-src-3 0" in steps, steps
    assert any(s.startswith("partclone") and "lc-cut-3" in s for s in steps), steps
    # recovery שאחריה זז אחורה בהפרש של הכיווץ החלקי, לא של התוכנית
    assert part(manifest(run), 4)["start_sector"] == REC_START - (CUR - size) // 512
    assert source_writes(steps, box) == [] and not rewrites(run), source_writes(steps, box)
    assert "rmmod brd" in steps
    assert not (run / "targets" / "sda" / "error").exists(), "האימג' גדול יותר בלי אזהרה (הכרעת נדב)"


def test_the_search_is_a_few_measures_and_not_a_walk_of_the_disk(tmp_path):
    """כל מדידה היא מעבר על ה-MFT בנתיב הקריטי: חצייה, עד הצעד."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES, env=MEM_16GB)
    captured(box, out)
    assert probes(run)[0] == PLAN, "התוכנית נמדדת ראשונה"
    assert 2 <= len(probes(run)) <= 1 + ((CUR - PLAN) // STEP).bit_length(), probes(run)


def test_the_machine_of_the_issue_hibernated_as_on_metal(tmp_path):
    """המספרים של הברזל כוללים את 128 ה-MB של ביטול ההשהיה (30860). כאן המאגר
    משרת גם את הביטול וגם את ההזזה, והכיווץ החלקי רץ אחרי הביטול, על העותק."""
    table = TWO_PARTITIONS.replace("204800 sectors", f"{WIN_SECTORS} sectors")
    table = table.replace(f"{WIN_SECTORS} sectors", "204800 sectors", 1)   # ה-ESP נשארת קטנה
    pre = view_sys({2: WIN_SECTORS}) + 'shrink_ask() { echo continue; }; attended() { shift; "$@"; }; '
    box, run, out = fast_run(tmp_path, pre=pre, env=MEM_16GB, extra={"sgdisk": table, "ntfsresize": CURVE})
    captured(box, out)
    win = manifest(run)["partitions"][1]
    as_far_as_the_memory_goes(win["size_bytes"], STORE_MB, hibernated=True)
    assert manifest(run)["src_unhib"] == "unhibernated"
    steps, node = order(run), posix(box / "dev/mapper/lc-src-2")
    drop = next(i for i, s in enumerate(steps) if s.startswith(f"ntfs-3g -o remove_hiberfile {node} "))
    resize = next(i for i, s in enumerate(steps) if s.startswith(f'ntfsresize -s {win["size_bytes"]} '))
    assert drop < resize, steps
    no_source_writes(box, run)


def test_each_view_fits_its_own_store_and_d_keeps_its_plan(tmp_path):
    """שתי מחיצות: לכל אחת מאגר משלה. D: נכנסת כמתוכנן, ורק C: מתכווצת חלקית,
    ו-D: זזה אחורה בהפרש האמיתי של C:."""
    box, run, out = capture_run(tmp_path, stubs=stubs(sgdisk=SGDISK_C_THEN_D, ntfsresize=CURVE_C), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"MEM_KIB": str((2 * STORE_MB + RESERVE_MB) * 1024)})
    captured(box, out)
    m = manifest(run)
    as_far_as_the_memory_goes(part(m, 3)["size_bytes"], STORE_MB)
    assert part(m, 4)["size_bytes"] == shrink_target(MIN_D, DATA_SECTORS * 512)
    assert part(m, 4)["start_sector"] == DATA_START - (CUR - part(m, 3)["size_bytes"]) // 512


def test_a_plan_that_fits_is_measured_once_and_shrinks_to_the_minimum(tmp_path):
    """שומר: כשהתוכנית נכנסת אין חיפוש, והאימג' הוא הקטן ביותר, כמו תמיד."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES,
                                env={"MEM_KIB": str(64 * 1024 * 1024)})
    captured(box, out)
    assert part(manifest(run), 3)["size_bytes"] == PLAN
    assert probes(run) == [PLAN]


def refused_cleanly(box: Path, run: Path) -> None:
    steps = order(run)
    assert source_writes(steps, box) == [] and not rewrites(run), source_writes(steps, box)
    assert not [s for s in steps if s.startswith(("ntfsresize -s", "partclone"))], steps
    assert not (run / "new-manifest.json").exists()
    assert not (box / "sys/module/brd").exists(), "העותק משוחרר גם בסירוב"


def test_no_memory_even_for_a_partial_shrink_is_refused_by_name(tmp_path):
    """עיקרון 5: מה שלא נכנס גם חלקית נכשל בשם ועם המספרים, ולא "נקלט בלי כיווץ"
    בשקט. המספרים הם של התוכנית, והמפעיל מקבל את הדרך לאימג' בגודל מלא."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES,
                                env={"MEM_KIB": str((RESERVE_MB + 200) * 1024)})
    reason = refusal_reason(box, run, out)
    assert f"להזזת {PLAN_MOVES} MB נדרשים {need(PLAN_MOVES)} MB זיכרון, ויש 200 MB" in reason, reason
    assert "גם כיווץ חלקי לא נכנס בזיכרון" in reason and "המקור לא נכתב" in reason and "בלי כיווץ" in reason, reason
    refused_cleanly(box, run)


def test_a_measure_that_fails_in_the_search_refuses_and_never_counts_as_fits(tmp_path):
    """עיקרון 5: "לא הצלחנו למדוד" באמצע החיפוש אינו "נכנס"."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES,
                                env={**MEM_16GB, "NO_MEASURE_ON": "3"})
    reason = refusal_reason(box, run, out)
    assert "ntfsresize -n לא אמר כמה צריך להזיז (rc=1)" in reason and "המקור לא נכתב" in reason, reason
    assert len(probes(run)) == 3, probes(run)
    refused_cleanly(box, run)


def test_a_direct_sends_tail_is_whole_or_not_at_all(tmp_path):
    """שומר: הזנב של הפצה ישירה קטן מהצעד. אין לו חצי: בלי זיכרון הפריסה נשלחת
    בגודל מלא, כמו שהיה, ובלי מדידות נוספות."""
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN, env={"MEM_KIB": str((RESERVE_MB + 200) * 1024)})
    _ok(box, run, out)
    assert part(manifest(run), 3)["size_bytes"] == WIN_BYTES and "shrunk_from_bytes" not in part(manifest(run), 3)
    assert len(probes(run)) == 1, probes(run)


# --- הבהרת נדב 07/11: דינמי לפי ה-RAM שנמדד במכונה, לא 16GB ---------------------------
#: מה שהקרנל וה-initramfs לקחו מ-16GiB על ברזל (03/11, HP-BUILD-2): MemAvailable היה
#: 15,156 + 1,024 = 16,180MiB מתוך 16,384. אותו הפרש לכל גודל זיכרון -- הנחה של הטסט, לא מדידה.
LOST_MIB = 16 * 1024 - (STORE_MB + RESERVE_MB)


def mem_of(gib: float) -> dict[str, str]:
    return {"MEM_KIB": str(int((gib * 1024 - LOST_MIB) * 1024))}


def store_of(gib: float) -> int:
    return int(gib * 1024 - LOST_MIB - RESERVE_MB)


@pytest.mark.parametrize("gib", [4, 8, 16, 32, 64])
def test_the_shrink_follows_the_machines_own_ram(tmp_path, gib):
    """אותו דיסק (התוכנית של ה-Issue, 27,706MB להזיז) על מחשב של 4, 8, 16, 32 ו-64GB:
    המאגר נגזר מ-MemAvailable של המכונה. מה שנכנס -- כיווץ מלא, במדידה אחת; מה שלא --
    חלקי, עד מה שהזיכרון של **המכונה הזו** מחזיק. אף גודל אינו מקודד."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES, env=mem_of(gib))
    captured(box, out)
    win = part(manifest(run), 3)
    size, store = win["size_bytes"], store_of(gib)
    assert win["shrunk_from_bytes"] == CUR
    full = need(PLAN_MOVES) <= store
    assert full == (gib >= 32), (gib, store, need(PLAN_MOVES))   # 30,732MB: מעל 16GB, מתחת ל-32
    if full:
        assert size == PLAN and probes(run) == [PLAN], (size, probes(run))
    else:
        as_far_as_the_memory_goes(size, store)
    # המאגר הוא מה שהמכונה הזו מחזיקה: MemAvailable פחות השמירה של ההעתקה
    assert f"modprobe brd rd_nr=1 rd_size={store * 1024}" in order(run), order(run)
    assert not (run / "targets" / "sda" / "error").exists()


def test_a_very_small_ram_shrinks_a_little_instead_of_refusing(tmp_path):
    """הקצה התחתון: מחשב של 1.5GB (מאגר של 308MB), והנתונים של C: יושבים עד סוף
    המחיצה -- כל MiB שמתכווץ הוא MiB להזיז. הכיווץ כמעט לא קורה (עשרות MiB מתוך
    מאות GB), אבל הקליטה מצליחה: החיפוש יורד מתחת לצעד של 1GiB עד שמשהו נכנס."""
    gib = 1.5
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES,
                                env={**mem_of(gib), "ZERO_AT": str(CUR)})
    captured(box, out)
    size, store = part(manifest(run), 3)["size_bytes"], store_of(gib)
    assert MIB <= CUR - size <= 64 * MIB and (CUR - size) % MIB == 0, (CUR - size) / MIB
    assert need(moved(size, CUR)) <= store, (size, moved(size, CUR))
    assert size - edge(store, zero=CUR) <= STEP, (size, edge(store, zero=CUR))
    assert not (run / "targets" / "sda" / "error").exists()


@pytest.mark.parametrize("meminfo", [
    'echo "MemTotal:       16777216 kB" > "$SYSROOT/proc/meminfo"; ',   # אין שורת MemAvailable
    'echo "MemAvailable:   lots kB" > "$SYSROOT/proc/meminfo"; ',
    'rm -f "$SYSROOT/proc/meminfo"; ',
], ids=["no-line", "not-a-number", "no-file"])
def test_ram_that_cannot_be_read_refuses_and_is_never_assumed(tmp_path, meminfo):
    """עיקרון 5: זיכרון שלא נקרא אינו "יש מספיק" -- הקליטה נעצרת בשם, לפני שנמדד או
    נכתב דבר, ולא מניחה גודל."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=CURVE), shell_pre=FS_MAP + SAY_YES + meminfo)
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לקרוא כמה זיכרון פנוי (MemAvailable)" in reason and "המקור לא נכתב" in reason, reason
    assert probes(run) == [], probes(run)
    refused_cleanly(box, run)
