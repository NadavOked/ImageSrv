"""‏#87: אימג' שאינו נכנס לכונן היעד — נתפס בקליטה, לא מול כיתה.

‏#82 תיקן את **החישוב**: הדרישה היא סוף הפריסה ולא גודל דיסק המקור.
מה שהוא לא יכול היה לתקן הוא שלושת אימג'י ה-tiny11 שבספרייה, שבהם
מחיצת ה-NTFS לבדה היא 273.7GB — גדולה מכונן ה-256 **כולו**. אין ערך
של `min_target_bytes` שמשנה את זה. האימג' באמת לא נכנס.

מכאן חלוקת העבודה שהקובץ הזה נועל, ושתי המחציות שלה אינן מחליפות זו
את זו:

* **הסינון בשחזור** (`disk_fits`, בדיקה 2.7) כבר שלם ונכון, והוא
  **אינו** נוגע כאן. הוא הראיה שהתיקון עבד, לא התיקון. להרפות ממנו
  היה הבאג ההפוך — כתיבת אימג' לכונן שאינו יכול להכיל אותו.
* **הקליטה** היא המקום היחיד שבו התשובה יכולה להשתנות. הכיווץ עצמו
  דורש דיסק מקור אמיתי ואינו כאן; מה שכן כאן הוא ה**הכרעה**: פריסה
  שאינה נכנסת לרצפה שנמסרה מסרבת לפני שבייט אחד זז, ועם המספר שאומר
  בכמה לכווץ.

ולכן הבדיקה המרכזית כאן היא זו שמחברת ביניהן: כיווץ בדיוק בגודל
ש-`shrink_bytes` מחזיר גורם לאותו `disk_fits` **הבלתי-משונה** לעבור.
שתי המחציות מסכימות על מספר אחד, ואף אחת מהן לא הורפתה כדי שיסתדר.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from server.library.imagefit import expandable_candidate, shrink_bytes
from server.library.images import required_bytes

from test_agent import AGENT, BASH, posix, sh
from test_capture_refusals import (CURL_SINK, ONE_PARTITION, capture_run,
                                   refusal_reason)
from test_image_sizing import (NVME_256, GOLDEN_FROM_VM, fits_box,
                               manifest, needs_shell)

REPO = Path(__file__).resolve().parent.parent

#: השער בקליטה אינו נוגע ב-jq — `capture_disk` קורא את הטבלה ב-sgdisk
#: וב-awk בלבד. ‏`needs_shell` (שדורש גם jq) היה מדלג עליו על תחנת
#: הפיתוח, וזו בדיוק בדיקה שמדולגת בסביבה שבה כן אפשר להריץ אותה.
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is required")

#: ‏tiny11 של המעבדה (`img_6f28b0`) עם **התפקידים האמיתיים** שלו, ולא
#: רק עם הגיאומטריה: ‏ESP, ‏MSR, המערכת, ומחיצת השחזור של Windows 11
#: שיושבת אחריה. ‏`TINY11` שב-`test_image_sizing` נבנה לחישוב הגודל
#: בלבד ומסמן שם `windows` על כל המחיצות — מספיק לשם, ומטעה כאן:
#: בחירת המועמד היא בדיוק ההבחנה בין `windows` ל-`recovery`.
LAB_TINY11 = manifest([
    (2048, 314572800), (616448, 16777216),
    (649216, 273724473344), (535267328, 818937856),
])
for _i, (_role, _fs) in enumerate(
        (("esp", "vfat"), ("msr", "vfat"), ("windows", "ntfs"),
         ("recovery", "ntfs"))):
    LAB_TINY11["partitions"][_i].update(
        role=_role, fs=_fs, expandable=_role == "windows")


def variant(**marks: bool) -> dict:
    """עותק של `LAB_TINY11` שבו `expandable` נקבע לפי אינדקס."""
    copy = json.loads(json.dumps(LAB_TINY11))
    for part in copy["partitions"]:
        part["expandable"] = marks.get(f"p{part['index']}", False)
    return copy

#: הפריסה של `ONE_PARTITION` (מחיצת ESP של 100MiB בסקטור 2048), כפי
#: ש-`capture_disk` יחשב אותה: סוף המחיצה, ועוד 256KiB לעותק הגיבוי של
#: ה-GPT (‏#1171 — קודם לכן מגה מעוגל למגה). המספר כתוב כאן במפורש כדי
#: שהטסטים למטה ישבו על **גבול** ולא על "בערך": רצפה אחת בדיוק מספיקה,
#: ובייט אחד פחות אינה.
ONE_PARTITION_NEED = (2048 * 512 + 100 * (1 << 20) + (256 << 10))


# --- ההכרעה עצמה --------------------------------------------------------------


def test_the_lab_tiny11_needs_a_named_number_of_bytes_it_does_not_have():
    """המספר שנעדר מ-#87: לא "לא נכנס" אלא **בכמה** לא נכנס.

    זה ההבדל בין תקלה לבין פעולה — הטכנאי שמקבל את המספר יודע לכמה
    לכווץ את המחיצה, ומי שמקבל "לא נכנס" מחליף כונן.
    """
    missing = shrink_bytes(LAB_TINY11, NVME_256)
    assert missing is not None and missing > 0
    assert required_bytes(LAB_TINY11) > NVME_256


def test_the_amount_to_shrink_is_exactly_the_overflow():
    """מה שהיה כאן עד #1171, ולמה זה השתנה.

    ‏`required_bytes` עיגל כלפי מעלה למגה-בייט, וכונן פיזי אינו כפולה של
    מגה — ‏`256,060,514,304 mod 1MiB = 352,256`. אז ההפרש `דרישה − רצפה`
    **לא** היה התשובה: כיווץ בגודלו נחת ברזולוציה שהעיגול דחף בחזרה מעל
    הרצפה, והאימג' "שכווץ בדיוק" עדיין נחסם מול כיתה (ה-CI תפס את זה).
    העיגול הוסר, ומאז הדרישה והכיווץ הם ביטוי אחד — ולכן ההפרש **כן** הוא
    התשובה, והטענה החזקה שמחליפה את הישנה היא שוויון ולא אי-שוויון.
    """
    assert NVME_256 % (1 << 20) != 0, "הרצפה כאן חייבת להיות לא-מיושרת"
    assert shrink_bytes(LAB_TINY11, NVME_256) == required_bytes(LAB_TINY11) - NVME_256


def test_an_image_that_fits_needs_to_shrink_by_nothing():
    """הצד השני — אחרת "הכל צריך כיווץ" היה עובר כהצלחה."""
    assert shrink_bytes(GOLDEN_FROM_VM, NVME_256) == 0


def test_a_requirement_that_cannot_be_determined_is_not_zero():
    """עיקרון 5 בשורה אחת: "לא ידענו לחשב" אינו "לא צריך לכווץ".

    ‏`0` ו-`None` נראים דומה לקורא רשלני, והם שני מצבים הפוכים: האחד
    אומר "כתוב את האימג'", השני "אל תיגע בו".
    """
    broken = manifest([(2048, 104857600)])
    broken["partitions"][0].pop("size_bytes")
    broken["min_target_bytes"] = "רבע טרה"
    assert required_bytes(broken) is None
    assert shrink_bytes(broken, NVME_256) is None


@pytest.mark.parametrize("floor", [None, "256GB", -1, True, 3.5])
def test_a_floor_that_is_not_a_byte_count_is_not_a_pass(floor):
    """רצפה פגומה חייבת להחזיר `None` ולא `0`. ‏`True` בפייתון הוא `1`,
    ורצפה של בייט אחד הייתה מכריזה שכל אימג' צריך כיווץ ענק — כלומר
    מספר שנראה תקין לחלוטין ואינו נכון."""
    assert shrink_bytes(GOLDEN_FROM_VM, floor) is None


# --- מי המחיצה שתכווץ ---------------------------------------------------------


def test_the_candidate_is_the_partition_the_restore_would_have_stretched():
    """כיווץ במחיצה שאינה זו שהשחזור מותח היה מקטין את האימג' **ולא**
    מחזיר את המקום למערכת אחרי השחזור. אותו כלל בדיוק כמו בסוכן."""
    part = expandable_candidate(LAB_TINY11)
    assert part is not None
    assert part["index"] == 3 and part["role"] == "windows"
    assert part["size_bytes"] == 273724473344


def test_recovery_is_never_the_candidate_even_though_it_sits_last():
    """‏#58: מחיצת ה-`recovery` של Windows 11 יושבת **אחרי** המערכת, והיא
    לעולם אינה המועמדת. לכווץ אותה היה מקטין את האימג' בלי לגעת במה
    שתופס אותו, ולהשאיר מחיצת שחזור ענקית ליד מערכת שלא זזה."""
    part = expandable_candidate(variant())
    assert part["index"] == 3, "המחיצה האחרונה על הפלטה נבחרה במקום המערכת"


def test_a_single_marked_partition_overrides_the_automatic_choice():
    """העקיפה הידנית של #58 שרירה גם כאן — מניפסט שמסמן אחת בדיוק גובר."""
    assert expandable_candidate(variant(p4=True))["index"] == 4


def test_two_marked_partitions_fall_back_instead_of_guessing_between_them():
    """שתיים מסומנות אינן הכרעה — חוזרים לבחירה האוטומטית, ולא בוחרים
    את הראשונה ברשימה. "הראשונה" כאן היא הכרעה שקטה בין שתי מחיצות."""
    assert expandable_candidate(variant(p3=True, p4=True))["index"] == 3


def test_the_last_system_partition_is_chosen_by_platter_order_not_list_order():
    """‏#58 מילה במילה: באימג' ענן של דביאן השורש הוא מחיצה `1` —
    ראשון ברשימה ואחרון על הפלטה."""
    cloud = manifest([(2048, 104857600), (616448, 41875931136)])
    cloud["partitions"][0].update(role="linux", fs="ext4", start_sector=616448 * 2)
    cloud["partitions"][1].update(role="linux", fs="ext4")
    for part in cloud["partitions"]:
        part["expandable"] = False
    assert expandable_candidate(cloud)["index"] == 1


def test_an_image_with_no_system_partition_has_no_candidate():
    """‏`None` הוא "לא ידוע מה לכווץ", והקורא אומר זאת במילים ולא ממציא
    מחיצה. אימג' נתונים בלבד הוא בדיוק המקרה."""
    data_only = manifest([(2048, 104857600), (616448, 41875931136)])
    for part in data_only["partitions"]:
        part.update(role="data", expandable=False)
    assert expandable_candidate(data_only) is None


# --- שתי המחציות מסכימות ------------------------------------------------------


def shrunk_by(amount: int) -> dict:
    """‏`LAB_TINY11` שהמחיצה המורחבת בו איבדה `amount` בייט.

    כל מה שיושב אחרי המועמד יורד באותו הפרש — הפריסה **מתכווצת**, לא
    נפערת. זה מה ש-`_move_to_tail` עושה בשחזור, מהכיוון ההפוך.
    """
    copy = json.loads(json.dumps(LAB_TINY11))
    candidate = next(p for p in copy["partitions"] if p["role"] == "windows")
    candidate["size_bytes"] -= amount
    for part in copy["partitions"]:
        if part["start_sector"] > candidate["start_sector"]:
            part["start_sector"] -= amount // 512
    return copy


def test_shrinking_by_exactly_that_number_satisfies_the_requirement():
    """אותה טענה כמו הבדיקה שמתחתיה, בפייתון בלבד — היא רצה גם על
    תחנת פיתוח בלי `jq`, ושם הבאג של העיגול היה מתגלה מיד במקום ב-CI."""
    assert required_bytes(shrunk_by(shrink_bytes(LAB_TINY11, NVME_256))) \
        <= NVME_256


def test_an_image_without_geometry_cannot_say_by_how_much_to_shrink():
    """‏"אינו נכנס" ו"בכמה לכווץ" הן שתי שאלות. מניפסט ישן בלי
    `start_sector` עונה על הראשונה (‏`required_bytes` נופל אחורה לערך
    המוצהר) ולא על השנייה — ואז `None`, לא ניחוש."""
    old = manifest([(2048, 104857600)], declared=10 ** 15)
    old["partitions"][0].pop("start_sector")
    assert required_bytes(old) > NVME_256, "הוא אכן אינו נכנס"
    assert shrink_bytes(old, NVME_256) is None


@needs_shell
def test_shrinking_by_exactly_that_number_makes_the_restore_check_pass(tmp_path):
    """הבדיקה שמחברת את הקליטה לשחזור, ולב הקובץ הזה.

    ‏`disk_fits` בסוכן **אינו משתנה כאן** — הוא נטען מהקובץ כמו שהוא.
    אותו tiny11 שנחסם על ה-NVMe עובר את אותה בדיקה בדיוק אחרי שהמחיצה
    המורחבת כווצה במדויק ב-`shrink_bytes`, ולא בייט יותר. אילו החישוב
    בקליטה והבדיקה בשחזור היו נגזרים משני כללים, המספר הזה היה מפספס
    לכאן או לכאן — ואז או שהאימג' עדיין נחסם, או שהוא נכתב וגולש.
    """
    missing = shrink_bytes(LAB_TINY11, NVME_256)
    shrunk = shrunk_by(missing)

    before = fits_box(tmp_path / "before", f"echo {NVME_256}", image=LAB_TINY11)
    after = fits_box(tmp_path / "after", f"echo {NVME_256}", image=shrunk)
    for (_run, path, prelude), want in ((before, "rc=1"), (after, "rc=0")):
        out = sh(prelude + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
        assert out.strip().endswith(want), out
    assert required_bytes(shrunk) <= NVME_256


def test_shrinking_by_one_byte_less_is_still_refused():
    """הבקרה השלילית של המספר עצמו: הוא ה**מינימום**, לא הערכה. כיווץ
    קטן ממנו אינו "כמעט" — האימג' עדיין אינו נכנס.

    שתי הטענות יחד קובעות את המספר לערך יחיד: זו מוכיחה שאי אפשר
    פחות, וזו שלמעלה שכך מספיק. בלי אחת מהן כל מספר גדול מספיק היה
    עובר, וכיווץ-יתר הוא מחיצה שאיבדה מקום בלי סיבה.
    """
    assert required_bytes(shrunk_by(shrink_bytes(LAB_TINY11, NVME_256) - 1)) \
        > NVME_256


# --- השער בקליטה --------------------------------------------------------------


@needs_bash
def test_a_layout_bigger_than_the_target_is_refused_before_a_byte_moves(tmp_path):
    """‏#87 כפי שהוא נראה למפעיל: לא אימג' שנקלט שעה ואז נחסם על כל
    תחנה בנפרד, אלא סירוב מיידי עם שלושת המספרים שהופכים אותו לפעולה.
    """
    floor = ONE_PARTITION_NEED - 1
    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION},
        env={"CAPTURE_TARGET_BYTES": str(floor)})
    reason = refusal_reason(box, run, out)
    assert str(ONE_PARTITION_NEED) in reason, f"מה צריך אינו בהודעה: {reason}"
    assert str(floor) in reason, f"מה יש אינו בהודעה: {reason}"
    assert "1" in reason and "לכווץ" in reason, f"בכמה לכווץ אינו בהודעה: {reason}"


@needs_bash
@pytest.mark.parametrize("floor", [ONE_PARTITION_NEED, None])
def test_a_layout_that_fits_is_captured_and_the_floor_is_recorded(tmp_path, floor):
    """שני הצדדים שהשער חייב להבדיל ביניהם, ובדיוק על הגבול.

    רצפה השווה לדרישה **עוברת**, והקליטה רצה עד המניפסט — בלי הבדיקה
    הזאת "הכל נחסם" היה עובר כתיקון. ובלי רצפה כלל היא רצה בדיוק כמו
    קודם, והמניפסט אומר `null`: "לא נבדק" חייב להיות קריא במניפסט ולא
    להיראות כמו "נבדק ונכנס" (עיקרון 5).
    """
    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK},
        env={"CAPTURE_TARGET_BYTES": str(floor)} if floor else None)
    assert out.strip().endswith("rc=0"), \
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    written = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    assert written["min_target_bytes"] == ONE_PARTITION_NEED
    assert written["target_floor_bytes"] == floor


@needs_bash
def test_a_floor_with_leading_zeros_is_refused_before_the_capture_runs(tmp_path):
    """רצפה שההשוואה המספרית מקבלת אבל JSON אינו.

    ‏`010000000000` עובר `*[!0-9]*` ועובר את `-gt`, ולכן הקליטה הייתה
    רצה **עד סופה** — המניפסט נכתב אחרון — ואז נכתב
    `"target_floor_bytes":010000000000`, שאינו JSON תקין, והשרת היה
    דוחה את האימג'. שעה של קריאת דיסק, וכישלון שאין לו קשר נראה לערך
    שהוקלד. הסירוב חייב להיות כאן, לפני הבייט הראשון.
    """
    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION},
        env={"CAPTURE_TARGET_BYTES": f"0{ONE_PARTITION_NEED}"})
    reason = refusal_reason(box, run, out)
    assert "אפסים מובילים" in reason, reason


@needs_bash
def test_a_floor_that_is_not_a_number_refuses_instead_of_skipping_the_check(tmp_path):
    """הדפוס שהפרויקט נכווה בו שבע פעמים: ערך שאי אפשר לקרוא **מדלג**
    על הבדיקה, וההצלחה נראית זהה לבדיקה שעברה. כאן הוא סירוב בשמו."""
    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION},
        env={"CAPTURE_TARGET_BYTES": "256GB"})
    reason = refusal_reason(box, run, out)
    assert "256GB" in reason, reason
    assert "מספר" in reason, reason


def test_an_unset_floor_is_recorded_as_null_and_not_as_a_number():
    """‏"לא נבדק" חייב להיות קריא במניפסט. שדה שנעדר או `0` היו שני
    ניסוחים של "נבדק ונכנס", וזה בדיוק הקיפול שעיקרון 5 אוסר."""
    source = (AGENT / "lib" / "capture" / "capture.sh").read_text(encoding="utf-8")
    assert '_floor_json="null"' in source
    assert '"target_floor_bytes":%s' in source
    # הארגומנט שמוזן לשדה הוא המשתנה, ומיד אחרי min_target_bytes.
    args = next(ln for ln in source.splitlines() if '"$_family" "$_os"' in ln)
    assert '"$_min_target" "$_floor_json"' in args, args


def test_the_gate_runs_before_the_first_partition_is_read():
    """סירוב שעולה אחרי שכבר קראנו מחיצה אינו "לפני שבייט אחד זז".
    הסדר נמדד על הקוד בפועל, לא על הערה שמזכירה את שני השמות."""
    from test_capture_refusals import capture_body, first_index
    lines = capture_body()
    assert first_index(lines, "CAPTURE_TARGET_BYTES") < first_index(lines, "partclone_for_fs")
    assert first_index(lines, "CAPTURE_TARGET_BYTES") < first_index(lines, "mkfifo")


# --- הכלי שמכריע על הספרייה ---------------------------------------------------


def imagefit(*args: str) -> subprocess.CompletedProcess:
    # ‏stdin=DEVNULL: בריצה רב-קבצית בווינדוס ה-handle נשבר תחת capture,
    # וכל subprocess שיורש אותו נופל ב-WinError 50 (#14).
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "imagefit.py"), *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)


def written(tmp_path: Path, image: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(image), encoding="utf-8")
    return path


def test_the_tool_refuses_the_image_that_really_is_too_big(tmp_path):
    """מה שנדב יריץ ביום חמישי עם המספר שמדד: הכרעה, ולא הערכה."""
    result = imagefit("--manifest", str(written(tmp_path, LAB_TINY11)),
                      "--target-bytes", str(NVME_256))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "אינו נכנס" in result.stdout
    assert str(shrink_bytes(LAB_TINY11, NVME_256)) in result.stdout.replace(",", "")


def test_the_tool_passes_the_image_that_fits(tmp_path):
    result = imagefit("--manifest", str(written(tmp_path, GOLDEN_FROM_VM)),
                      "--target-bytes", str(NVME_256))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "נכנס" in result.stdout


def test_without_a_measurement_the_tool_says_so_instead_of_deciding(tmp_path):
    """הנקודה שכל ה-Issue עומד עליה: בלי מדידה על ברזל אין הכרעה.
    כלי שהיה מכריז "נכנס" בלי כונן להשוות אליו הוא בדיוק עיקרון 5."""
    result = imagefit("--manifest", str(written(tmp_path, GOLDEN_FROM_VM)))
    assert result.returncode == 1, result.stdout
    assert "לא הוכרע" in result.stdout
    # ‏`blockdev` דווקא: זו הפקודה ש-`disk_fits` מריץ, והיחידה מבין
    # השתיים שנארזת ב-initramfs. ‏`lsblk` אינו ב-`BINARIES`, וכלי
    # שמפנה לפקודה שאינה על המכונה אינו הופך את חמישי לחמש דקות.
    assert "blockdev" in result.stdout, "הכלי אינו אומר איזו פקודה למדוד"
    assert "lsblk" not in result.stdout


def test_the_command_the_tool_asks_for_is_actually_on_the_machine():
    """כלי שמפנה לפקודה שאינה ארוזה ב-initramfs שולח את המפעיל למכונה
    שאין בה מה להריץ — וזה בדיוק ההפך מ"חמש דקות של אימות".

    הנעילה היא על `BINARIES` בפועל (`tools/build_initramfs.sh`), לא על
    הערה: ‏`lsblk` **אינו** שם, ו-`blockdev` כן. שתי הפקודות נראות
    מתחלפות, ורק אחת מהן קיימת בסביבה שבה המדידה נעשית.
    """
    from tools.imagefit import MEASURE
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    binaries = build[build.index("BINARIES=("):]
    binaries = binaries[:binaries.index(")")]
    assert MEASURE.split()[0] in binaries.split(), \
        f"‏{MEASURE.split()[0]} אינו ב-BINARIES — הכלי מפנה לפקודה שאינה על המכונה"
    assert "lsblk" not in binaries.split(), \
        "‏lsblk נארז — כדאי לעדכן את MEASURE ואת הנימוק שלידו"


def test_an_empty_library_is_not_a_pass(tmp_path):
    """ספרייה ריקה אינה "כל האימג'ים נכנסים"."""
    result = imagefit("--images", str(tmp_path), "--target-bytes", str(NVME_256))
    assert result.returncode == 1, result.stdout
