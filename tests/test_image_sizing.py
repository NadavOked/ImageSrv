"""כמה מקום אימג' באמת צריך — ‏#82.

עד כאן `min_target_bytes` היה **גודל דיסק המקור כפי שהוא**, ולכן אימג'
שנקלט ממכונה וירטואלית לא נכנס לאף מחשב פיזי מאותה מחלקת גודל: דיסק VM
‏"256GB" נוצר כ-256 GiB, וכונן פיזי "256GB" הוא 256 מיליארד בייט —
שבעה אחוזים פחות. הדרישה היא עכשיו **סוף הפריסה**, נגזרת מגיאומטריית
טבלת המחיצות, ומחושבת באותו כלל בשלושה מקומות: השרת (פייתון), בדיקת
‏2.7 של הסוכן (jq) והקליטה (awk). יש כאן בדיקה שמריצה את שלושתם על
אותם מניפסטים ומשווה מספר למספר.

מה שהבדיקות האלה שומרות עליו יותר מכל: **החישוב אינו נוגע ב-`used_bytes`.**
בכל ארבעת האימג'ים שכבר בספרייה השדה הזה הוא 0 (זה היה #84, תוקן
ב-v0.10.6 אבל מניפסטים ישנים אינם משתנים רטרואקטיבית), ודרישה שנשענת
עליו הייתה יוצאת אפס — כלומר אימג' שמתקבל לכונן שאינו יכול להכיל אותו.
זהו הבאג ההפוך, והוא חמור מזה שבאיסיו.
"""

from __future__ import annotations

import json
import shutil

import pytest

from server.library.images import ImageLibrary, disk_family, layout_end_bytes, required_bytes

from conftest import ESP_GUID, WINDOWS_GUID, write_image
from test_agent import AGENT, BASH, posix, sh
from agentlib import lib_path

#: הכונן שעליו נשבר #82 — NVMe פיזי "256GB" במחשב הלנובו של המעבדה.
NVME_256 = 256060514304
#: דיסק של מכונה וירטואלית "256GB", כלומר 256 GiB — שבעה אחוזים יותר.
VM_256 = 274877906944

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")


def manifest(parts, source=VM_256, declared=None, **extra):
    """מניפסט ממשק 1 מינימלי. `parts` = זוגות (start_sector, size_bytes).

    ‏`used_bytes` נכתב 0 בכל מחיצה בכוונה — זה בדיוק מה שיש בספרייה.
    """
    return {
        "schema": 1, "id": "img_t", "name": "t", "family": 256,
        "scheme": "gpt", "sector_size": 512,
        "source_disk_bytes": source,
        "min_target_bytes": source if declared is None else declared,
        "partitions": [
            {"index": i + 1, "type_guid": ESP_GUID if i == 0 else WINDOWS_GUID,
             "role": "esp" if i == 0 else "windows",
             "fs": "vfat" if i == 0 else "ntfs",
             "start_sector": start, "size_bytes": size, "used_bytes": 0,
             "file": f"p{i + 1}.x.pcl.zst", "sha256": "aa" * 32,
             "expandable": i > 0}
            for i, (start, size) in enumerate(parts)
        ],
        **extra,
    }


#: אימג' זהב כפי שבונים אותו באמת: מכונה וירטואלית עם דיסק 256 GiB,
#: מערכת מותקנת במחיצה של ~39GB, והשאר לא מוקצה.
GOLDEN_FROM_VM = manifest([(2048, 314572800), (616448, 41875931136)])

#: ‏tiny11 של המעבדה (img_6f28b0), מספרים אמיתיים: אותו דיסק VM, אבל
#: המחיצות ממלאות אותו עד הסוף. ה-NTFS לבדו הוא 273.7GB.
TINY11 = manifest([
    (2048, 314572800), (616448, 16777216),
    (649216, 273724473344), (535267328, 818937856),
])


# --- הדרישה עצמה ------------------------------------------------------------


def test_an_image_built_in_a_vm_fits_a_physical_drive_of_its_class():
    """הבקרה השלילית של #82, ולב האיסיו.

    לפני התיקון הדרישה הייתה 274,877,906,944 — גודל דיסק ה-VM — וכל
    כונן פיזי "256GB" נחסם. מה שהאימג' באמת צריך הוא סוף המחיצה
    האחרונה: כ-42GB, ומכאן גם לכל כונן גדול יותר.
    """
    need = required_bytes(GOLDEN_FROM_VM)
    assert need is not None
    assert need < NVME_256 < GOLDEN_FROM_VM["min_target_bytes"]


def test_a_drive_that_really_is_too_small_is_still_refused():
    """‏tiny11 של המעבדה **אינו** נכנס ל-NVMe של 256, וזה נכון.

    ה-NTFS שלו הוא 273.7GB — גדול מהכונן כולו. הדרישה ירדה בשני
    מגה-בייט בלבד, וזה כל מה שאפשר: partclone מסרב לשחזר מערכת קבצים
    לתוך מחיצה קטנה ממנה, ולכן הרצפה של כל מחיצה היא הגודל שנקלט.
    """
    need = required_bytes(TINY11)
    assert need > NVME_256
    assert need < TINY11["min_target_bytes"]


def test_the_requirement_never_reads_used_bytes():
    """הנקודה המסוכנת ביותר: ‏`used_bytes` הוא 0 בכל מניפסט קיים.

    אותה פריסה בדיוק, פעם עם 0 ופעם עם ערכים מלאים — אותו מספר. אילו
    החישוב היה נשען על השדה, הגרסה עם ה-0 הייתה מחזירה דרישה אפסית
    ומכניסה כל אימג' לכל כונן.
    """
    zeros = json.loads(json.dumps(TINY11))
    filled = json.loads(json.dumps(TINY11))
    for part in filled["partitions"]:
        part["used_bytes"] = part["size_bytes"] // 3
    assert required_bytes(zeros) == required_bytes(filled)
    assert required_bytes(zeros) > 274_000_000_000   # ולא אפס, ולא זעום


def test_the_requirement_never_exceeds_the_disk_it_was_captured_from():
    """מה שנכנס לדיסק המקור חייב להיכנס לדיסק זהה: עיגול כלפי מעלה למגה
    לא יהפוך אימג' לבלתי-שחזיר על תאום של המכונה שממנה נקלט."""
    for image in (GOLDEN_FROM_VM, TINY11):
        assert required_bytes(image) <= image["source_disk_bytes"]


def test_the_gpt_backup_gets_room_at_the_tail():
    """‏sgdisk -e מזיז את עותק הגיבוי לסוף הכונן, וצריך לו מקום. כונן
    בדיוק בגודל המחיצה האחרונה אינו מספיק.

    ‏#1171: המקום הזה הוא 256KiB — עותק הגיבוי הוא 33 סקטורים (135,168
    בייט בכונן 4Kn, הגרוע שבשניים) — ו**לא** מגה-בייט מעוגל כלפי מעלה
    למגה. הכמות היא דרישה על גודל הכונן; היישור של `sgdisk` הוא של
    המחיצות, ואינו שייך לכאן.
    """
    exact = manifest([(0, 100 << 20)], source=10 ** 15)
    assert required_bytes(exact) == (100 << 20) + (256 << 10)
    assert required_bytes(exact) > 33 * 4096 + (100 << 20)


def test_a_layout_from_a_byte_identical_drive_no_longer_needs_the_source_clamp():
    """‏#1171, נמדד על ברזל: דיסק ווינדוס אמיתי של 256,060,514,304 בייט —
    בדיוק גודל ה-Samsung שפסלנו — נגמר ב-256,060,162,048, כלומר 344KB
    לפני הסוף. הדרישה הישנה (מגה ועיגול למגה) הייתה 256,061,210,624,
    גדולה מהכונן עצמו: הפריסה עברה **רק** דרך ההצמדה ל-`source_disk_bytes`,
    ולכן כל יעד קטן ולו בבייט אחד מהמקור נפסל. עכשיו היא עוברת לבדה.
    """
    real_end = 256_060_162_048            # מדידה, 22/09: Get-Partition על דיסק GPT חי
    real_disk = 256_060_514_304
    image = manifest([(0, real_end)], source=real_disk + (2 << 20))
    assert required_bytes(image) <= real_disk
    assert required_bytes(image) - real_end == 256 << 10


def test_a_manifest_without_geometry_falls_back_to_the_declared_value():
    """מניפסט ישן בלי start_sector — הערך המוצהר, השמרני מבין השניים."""
    old = manifest([(2048, 104857600)], declared=123456789)
    old["partitions"][0].pop("start_sector")
    assert layout_end_bytes(old) is None
    assert required_bytes(old) == 123456789


def test_a_requirement_that_cannot_be_determined_is_not_a_pass():
    """בלי גיאומטריה **ובלי** ערך מוצהר תקין — None, והקורא חוסם.
    ‏"לא הצלחנו לחשב" אינו "אז נניח שזה בסדר" (עיקרון 5)."""
    broken = manifest([(2048, 104857600)])
    broken["partitions"][0].pop("size_bytes")
    broken["min_target_bytes"] = "רבע טרה"
    assert required_bytes(broken) is None


def test_one_partition_without_geometry_disqualifies_the_whole_layout():
    """מחיצה אחת בלי מידות והפריסה כולה אינה ידועה — לא מסתפקים בשאר."""
    partial = manifest([(2048, 314572800), (616448, 41875931136)])
    partial["partitions"][1].pop("size_bytes")
    assert layout_end_bytes(partial) is None


# --- הסינון בשרת ------------------------------------------------------------


def library_with(tmp_path, *manifests):
    for i, image in enumerate(manifests):
        copy = json.loads(json.dumps(image))
        copy["id"] = f"img_{i:06x}"           # ‏#1407: מזהה תקין, אחרת `_validate` מדלג
        write_image(tmp_path, copy)
    return ImageLibrary(tmp_path)


def test_the_golden_vm_image_is_offered_to_the_physical_lenovo(tmp_path):
    """קצה-לקצה של הסינון, מול הכונן שעליו נשבר #82."""
    library = library_with(tmp_path, GOLDEN_FROM_VM, TINY11)
    assert sorted(library.scan()) == ["img_000000", "img_000001"]   # שניהם בספרייה
    assert library.allowed_for_disks(
        [{"size_bytes": NVME_256, "removable": False}]) == ["img_000000"]


def test_a_five_hundred_image_that_fits_a_smaller_drive_is_offered(tmp_path):
    """‏#1863 (נדב 20/10) הופך את הכלל הישן: המשפחה **אינה** תקרה. אימג' 500
    שהגיאומטריה שלו נכנסת לכונן 256 מוצע לו — מאז הכיווץ (#1171) זה המקרה
    הרגיל, והמשפחה הסתירה אותו ממסך המשכפל בזמן שהקונסולה אישרה."""
    small_500 = json.loads(json.dumps(GOLDEN_FROM_VM))
    small_500["family"] = 500
    library = library_with(tmp_path, small_500)
    assert sorted(library.scan()) == ["img_000000"]
    assert required_bytes(small_500) < NVME_256, "הגיאומטריה כאן דווקא נכנסת"
    assert library.allowed_for_disks(
        [{"size_bytes": NVME_256, "removable": False}]) == ["img_000000"]
    assert library.allowed_for_disks(
        [{"size_bytes": 500107862016, "removable": False}]) == ["img_000000"]


def test_the_list_counts_the_minimum_free_space_as_the_console_does(tmp_path):
    """‏#1863: "נכנס" ברשימה = ‏`required_bytes` + ‏min_free (‏#1354), אותו מספר
    שהקונסולה מציגה ("נכנס לדיסק מ-X") ו-`fit_refusal` אוכף. מחיצת Windows
    עם 10GB פנויים: עם min_free=20 היא צריכה לגדול, ולכן דיסק שגדול מהפריסה
    ב-5GB אינו מספיק, ודיסק שגדול ב-15GB כן."""
    image = json.loads(json.dumps(GOLDEN_FROM_VM))
    win = image["partitions"][1]
    win["used_bytes"] = win["size_bytes"] - 10 * 10 ** 9
    library = library_with(tmp_path, image)
    layout = required_bytes(image)
    near = [{"size_bytes": layout + 5 * 10 ** 9, "removable": False}]
    roomy = [{"size_bytes": layout + 15 * 10 ** 9, "removable": False}]
    assert library.allowed_for_disks(near, 0) == ["img_000000"]
    assert library.allowed_for_disks(near, 20) == []
    assert library.allowed_for_disks(roomy, 20) == ["img_000000"]


def test_an_unmeasured_system_partition_is_not_offered_while_min_free_is_on(tmp_path):
    """‏#1494/#1863: ‏`used_bytes` חסר = הכלל אינו ניתן לבדיקה — הסוכן מסרב בכל
    מגירה והקונסולה משביתה. גם הרשימה לא מציעה (עיקרון 5); בכלל כבוי — כן."""
    image = json.loads(json.dumps(GOLDEN_FROM_VM))
    image["partitions"][1]["used_bytes"] = None
    library = library_with(tmp_path, image)
    disk = [{"size_bytes": NVME_256, "removable": False}]
    assert library.allowed_for_disks(disk, 20) == []
    assert library.allowed_for_disks(disk, 0) == ["img_000000"]


def test_the_family_alone_would_have_admitted_an_image_that_does_not_fit(tmp_path):
    """למה לא סיננו לפי `family` בלבד, כפי שהאיסיו מציע כחלופה.

    ‏tiny11 והכונן הפיזי שניהם `256` — סינון לפי המשפחה היה מציע אותו,
    והשחזור היה מת על הדיסק. המשפחה היא תווית בת שתי מחלקות, לא מידה.
    """
    library = library_with(tmp_path, TINY11)
    assert sorted(library.scan()) == ["img_000000"]   # נדחה בגודל, לא כמניפסט פגום
    assert TINY11["family"] == disk_family(NVME_256) == 256
    assert library.allowed_for_disks(
        [{"size_bytes": NVME_256, "removable": False}]) == []


def test_a_manifest_with_an_unreadable_requirement_is_skipped(tmp_path):
    """מניפסט שאי אפשר להכריע לפיו נדחה בסריקה ולא נספר כמתאים."""
    bad = json.loads(json.dumps(GOLDEN_FROM_VM))
    bad["min_target_bytes"] = None
    library = library_with(tmp_path, bad)
    assert library.scan() == {}
    assert library.allowed_for_disks(
        [{"size_bytes": NVME_256, "removable": False}]) == []


# --- שלושת המימושים מסכימים -------------------------------------------------


CASES = [
    GOLDEN_FROM_VM, TINY11,
    manifest([(2048, 104857600), (1085440, 254803968000)], source=NVME_256),
    manifest([(2048, 1048576)], source=2 << 30),
    # מחיצה שנגמרת בדיוק על גבול מגה — העיגול לא יוסיף מגה מיותר
    manifest([(2048, (100 << 20) - 2048 * 512)], source=2 << 30),
]


def capture_awk() -> str:
    """הקטע שמחשב `_min_target` בתוך capture.sh, כלשונו. נחתך מהקובץ
    ולא משוכפל כאן: בדיקה שמריצה עותק אינה מעידה על הקוד."""
    source = (AGENT / "lib" / "capture" / "capture.sh").read_text(encoding="utf-8")
    start = source.index("    _min_target=$(awk")
    return source[start:source.index("\"$_parts\")", start) + len("\"$_parts\")")]


@needs_shell
@pytest.mark.parametrize("case", CASES, ids=range(len(CASES)))
def test_the_agent_and_the_server_agree_on_the_requirement(tmp_path, case):
    """אותו כלל, שני מימושים. פער בין השרת לסוכן פירושו אימג' שהשרת
    מציע ובדיקת 2.7 חוסמת — או, גרוע יותר, ההפך."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    out = sh(f'. {posix(AGENT)}/lib/restore/diskfit.sh; required_bytes {posix(path)!r}')   # #1217
    assert int(out.strip()) == required_bytes(case)


@needs_shell
@pytest.mark.parametrize("case", CASES, ids=range(len(CASES)))
def test_capture_sizes_the_layout_and_not_the_source_disk(tmp_path, case):
    """ה-awk של `capture_disk`, על אותם מקרים. הקלט הוא `parts.txt` כפי
    ש-capture בונה אותו: index|guid|uguid|start_sector|size_in_**sectors**."""
    parts = tmp_path / "parts.txt"
    parts.write_text("".join(
        f"{p['index']}|g|u|{p['start_sector']}|{p['size_bytes'] // 512}\n"
        for p in case["partitions"]), encoding="utf-8")
    out = sh(f'_disk_bytes={case["source_disk_bytes"]}; _parts={posix(parts)!r}; '
             f'_sector_size=512; '
             f'log() {{ :; }}; ' + capture_awk() + '; printf "%s" "$_min_target"')
    assert int(out.strip()) == required_bytes(case)


def test_capture_no_longer_writes_the_source_disk_size_as_the_requirement():
    """בקרה שלילית ברמת המקור: השורה שהעבירה `$_disk_bytes` פעמיים —
    פעם ל-source_disk_bytes ופעם ל-min_target_bytes — היא #82 עצמו."""
    source = (AGENT / "lib" / "capture" / "capture.sh").read_text(encoding="utf-8")
    line = next(ln for ln in source.splitlines() if '"$_family" "$_os"' in ln)
    assert '"$_disk_bytes" "$_min_target"' in line
    assert '"$_disk_bytes" "$_disk_bytes"' not in line


# --- בדיקה 2.7 בסוכן --------------------------------------------------------


def fits_box(tmp_path, blockdev_body: str, image=TINY11):
    """קופסה עם blockdev מזויף שמדווח גודל נתון, ומגירה אחת במצב writing."""
    box = tmp_path / "box"
    stubs = box / "stubs"
    stubs.mkdir(parents=True)
    run = box / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    path = box / "manifest.json"
    path.write_text(json.dumps(image), encoding="utf-8")
    (stubs / "blockdev").write_text(f"#!/bin/sh\n{blockdev_body}\n", encoding="utf-8")
    prelude = (
        f"chmod 0755 {posix(stubs)}/blockdev; "
        f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/progress.sh; '
        f'. {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/restore/diskfit.sh; '   # #1217
        "raid_member_reason() { :; }; "   # #1808: the RAID gate has its own tests (test_capture_gates.py); these disks are files
    )
    return run, path, prelude


@needs_shell
def test_a_too_small_disk_is_named_with_both_numbers_before_it_is_touched(tmp_path):
    """דרישה 2.7: נחסם **לפני** שנגענו בכונן, ושני המספרים בהודעה."""
    run, path, prelude = fits_box(tmp_path, f"echo {NVME_256}")
    out = sh(prelude + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
    assert out.strip().endswith("rc=1")
    error = (run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert str(required_bytes(TINY11)) in error and str(NVME_256) in error
    assert (run / "targets" / "sda" / "state").read_text().strip() == "failed"


@needs_shell
def test_the_vm_image_passes_the_same_check_on_the_same_drive(tmp_path):
    """הצד השני של אותה בדיקה — אחרת "הכל נחסם" היה עובר כהצלחה."""
    run, path, prelude = fits_box(tmp_path, f"echo {NVME_256}", image=GOLDEN_FROM_VM)
    out = sh(prelude + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
    assert out.strip().endswith("rc=0")
    assert not (run / "targets" / "sda" / "error").exists()


@needs_shell
def test_a_disk_whose_size_cannot_be_read_fails_instead_of_writing(tmp_path):
    """עיקרון 5 בשורה אחת: ‏blockdev שנכשל אינו "הכונן גדול מספיק".

    קודם לכן ההשוואה כולה ישבה תחת `2>/dev/null`, וערך לא מספרי דילג
    על הבדיקה והמשיך לכתוב על הדיסק.
    """
    run, path, prelude = fits_box(tmp_path, "exit 1", image=GOLDEN_FROM_VM)
    out = sh(prelude + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
    assert out.strip().endswith("rc=1")
    assert "לא ניתן לדעת אם האימג' נכנס" in \
        (run / "targets" / "sda" / "error").read_text(encoding="utf-8")


def test_both_restore_paths_check_before_they_write_the_table():
    """מגירה קטנה מדי נפסלה עד כה רק אחרי ש---zap-all כבר מחק אותה,
    ודיווחה "could not write the partition table" — תקלת חומרה למראה.

    הסדר נמדד על הקריאות בפועל: שורות הערה שמזכירות את שני השמות אינן
    עדות על מה שרץ קודם.
    """
    # ‏#1366: the cloning room asks disk_fits through write_check_round (writecheck.sh),
    # which asks it first -- before the write check, and both before any table.
    for name, entry, check, then in (("restore.sh", "run_restore() {", "disk_fits", "apply_gpt"),
                                      ("drawers.sh", "run_restore_drawers() {", "write_check_round", "apply_gpt"),
                                      ("writecheck.sh", "write_check_round() {", "disk_fits", 'write_check "')):
        source = (lib_path(name)).read_text(encoding="utf-8")
        body = source[source.index(entry):]
        calls = [ln.strip() for ln in body.splitlines()
                 if not ln.lstrip().startswith("#")]
        text = "\n".join(calls[1:])          # the order on the code itself, the entry line aside
        assert text.index(check) < text.index(then), name
