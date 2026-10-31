"""‏#1212 — ה-GUID הייחודי של כל מחיצה נבדק בקליטה ונקרא בחזרה בשחזור.

ה-BCD של Windows מאתר את מחיצת המערכת לפי זוג GUID — של הדיסק ושל
המחיצה (#26). עד כאן:

* **בקליטה** הקריאה נעשתה ב-`sgdisk -i ... 2>/dev/null`, וקריאה שנכשלה
  הפכה למחרוזת ריקה במניפסט, בלי מילה.
* **בשחזור** ‏`apply_gpt` דילג על `-u` כשהערך ריק, sgdisk המציא GUID חדש,
  ו-`verify_table` קרא בחזרה רק את ה-GUID של הדיסק. תוצאה: ‏`done` ואז
  ‏`winload.efi 0xc000000e` על כל מחשב משוחזר.

הפטור של לינוקס נשמר (`server/library/capture.py`: ‏GRUB מאתר לפי UUID של מערכת
הקבצים): באימג' שאינו Windows, מחיצה ש-sgdisk לא הדפיס לה GUID ייחודי
נרשמת ריקה כמו קודם — אבל ביומן, בשמה. ‏sgdisk שנכשל (אז גם סוג המחיצה
לא נקרא, ואין ממה לגזור "אינו Windows") וערך שאינו GUID נדחים בכל אימג'.

ההבחנה בין Windows ללינוקס היא מתפקידי המחיצות, אותו כלל כמו `_image_os`
בסוכן ו-`image_os` בשרת.
"""

from __future__ import annotations

import json

import pytest

from native import requires_native
from test_agent import BASH, posix, sh
from test_capture_refusals import CURL_SINK, ONE_PARTITION, capture_run, refusal_reason
from test_restore_evidence import (
    PLAN, build_box, log_of, rc_of, restore_run, state_of, target_error, wrote,
)
from test_timeouts import log_of as capture_log

pytestmark = requires_native(("bash", BASH))

WINDOWS_TYPE = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
LINUX_TYPE = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
#: ה-GUID הייחודי ש-`ONE_PARTITION` מדפיס.
SOURCE_UGUID = "4C7B1E00-0000-4000-8000-000000000002"
UGUID_LINE = f'  echo "Partition unique GUID: {SOURCE_UGUID}"\n'


def source_disk(type_guid: str, *, uguid_line: str = UGUID_LINE, i_rc: int = 0) -> str:
    """‏`ONE_PARTITION` עם סוג מחיצה אחר, שורת GUID ייחודי אחרת, או `-i` שנכשל.

    ‏`i_rc` מפיל את קריאת ה-`-i` **השנייה** בלבד — זו של ה-GUID הייחודי
    (‏capture.sh קורא סוג, GUID ייחודי, סקטור ראשון וגודל, בסדר הזה). כש-
    `-i` נכשל כולו, ‏`_shrink_entry` כבר דוחה את הקליטה על הדגלים — מסלול
    אחר, שקיים גם ב-main. כאן נבדק הכשל של הקריאה הזו בדיוק."""
    stub = (ONE_PARTITION
            .replace("C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI)", f"{type_guid} (x)")
            .replace(UGUID_LINE, uguid_line))
    assert type_guid in stub and (uguid_line == UGUID_LINE or SOURCE_UGUID not in stub)
    if i_rc:
        stub = stub.replace(
            'if [ "$1" = "-i" ]; then\n',
            'if [ "$1" = "-i" ]; then\n'
            '  n=$(cat "$0.n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$0.n"\n'
            f'  [ "$n" -eq 2 ] && exit {i_rc}\n')
    return stub


def capture(tmp_path, stub):
    # ‏#1369: מחיצת ה-Windows כאן היא קובץ ריק בלי מערכת קבצים; מה שנבדק
    # הוא ה-GUID, ולכן היא "מכילה" מערכת (התפקיד נבדק ב-test_capture_measure_1368).
    # ‏#1822: דיסק Windows בלי ESP נדחה — הפריסה אינה הנושא כאן (test_capture_layout_1822_1824).
    return capture_run(tmp_path, stubs={"sgdisk": stub, "curl": CURL_SINK},
                       shell_pre="_system_role() { echo windows; }; capture_layout_reason() { :; }; ")


def manifest_of(run):
    return json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))


# --- הקליטה -------------------------------------------------------------------


def test_a_windows_capture_records_the_unique_guid_it_read(tmp_path):
    """הצד החיובי: ‏Windows עם GUID תקין נקלט, והערך מגיע למניפסט."""
    box, run, out = capture(tmp_path, source_disk(WINDOWS_TYPE))
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(errors="replace")
    part = manifest_of(run)["partitions"][0]
    assert part["role"] == "windows"
    assert part["unique_guid"] == SOURCE_UGUID


def test_a_windows_partition_without_a_unique_guid_fails_the_capture(tmp_path):
    """הליבה של #1212 בצד הקליטה. עד כאן: ‏rc=0 ו-`"unique_guid":""` —
    אימג' שייראה תקין בספרייה ולא יעלה על אף מחשב שישוחזר ממנו."""
    box, run, out = capture(tmp_path, source_disk(WINDOWS_TYPE, uguid_line=""))
    reason = refusal_reason(box, run, out)
    assert "מחיצה 1" in reason, reason
    assert "Windows" in reason and "GUID ייחודי" in reason, reason
    assert not (run / "new-manifest.json").exists()


def test_a_linux_partition_without_a_unique_guid_stays_exempt_but_is_named(tmp_path):
    """הפטור של לינוקס נשמר כמו ב-main: הקליטה מצליחה והשדה ריק. מה
    שהשתנה הוא שזה נרשם ביומן בשם המחיצה, ולא בשקט."""
    box, run, out = capture(tmp_path, source_disk(LINUX_TYPE, uguid_line=""))
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(errors="replace")
    written = manifest_of(run)
    assert written["os"] == "linux"
    assert written["partitions"][0]["unique_guid"] == ""
    assert "partition 1: sgdisk -i 1 לא הדפיס GUID ייחודי" in capture_log(run)
    assert "exempt" in capture_log(run)


@pytest.mark.parametrize("type_guid", [WINDOWS_TYPE, LINUX_TYPE], ids=["windows", "linux"])
def test_a_failed_sgdisk_read_fails_the_capture_on_any_image(tmp_path, type_guid):
    """‏sgdisk שיצא שונה מאפס לא קרא גם את **סוג** המחיצה, ולכן אין ממה
    לגזור שהאימג' אינו Windows — הפטור לא חל. ‏"לא הצלחנו לבדוק" נכשל."""
    box, run, out = capture(tmp_path, source_disk(type_guid, i_rc=5))
    reason = refusal_reason(box, run, out)
    assert "מחיצה 1" in reason and "rc=5" in reason, reason
    assert not (run / "new-manifest.json").exists()


@pytest.mark.parametrize("type_guid", [WINDOWS_TYPE, LINUX_TYPE], ids=["windows", "linux"])
@pytest.mark.parametrize("value", ["not-a-guid", "4C7B1E00000040008000000000000002",
                                   "4C7B1E00-0000-4000-8000-00000000000Z"])
def test_a_unique_guid_that_is_not_a_guid_fails_the_capture_on_any_image(tmp_path, type_guid, value):
    """ערך שאינו GUID היה נכתב למניפסט ונופל על `sgdisk -u` בכל שחזור —
    גם בלינוקס. המחיצה "נושאת GUID", והוא פגום: נדחה כאן, לא מול כיתה."""
    line = f'  echo "Partition unique GUID: {value}"\n'
    box, run, out = capture(tmp_path, source_disk(type_guid, uguid_line=line))
    reason = refusal_reason(box, run, out)
    assert "מחיצה 1" in reason and "שאינו GUID" in reason, reason
    assert not (run / "new-manifest.json").exists()


# --- השחזור: הקריאה בחזרה -------------------------------------------------------


def test_unique_guids_that_came_back_from_the_disk_reach_done(tmp_path):
    box, run, prelude = build_box(tmp_path)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=0", out
    assert state_of(run) == "verifying"   # #1935: done is the caller's, after the name, the drivers and the mark
    assert wrote(run) == ["1", "2", "3"]
    assert "3 partition unique GUIDs came back from the disk" in log_of(run)
    # הראיה נקראה מהדיסק ולא נגזרה מקוד היציאה של `-u`.
    calls = (box / "sgdisk.calls").read_text().splitlines()
    for index in ("1", "2", "3"):
        assert any(c.startswith(f"-i {index} ") for c in calls), calls


def test_a_unique_guid_the_disk_did_not_keep_fails_the_restore(tmp_path):
    """הליבה של #1212 בצד השחזור: ‏`sgdisk -u` יצא 0 והמחיצה נושאת GUID
    אחר. עד כאן — ‏`done`, ו-0xc000000e בעלייה הראשונה."""
    box, run, prelude = build_box(tmp_path)
    (box / "uguid.lie.2").write_text("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA\n", newline="\n")
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert wrote(run) == [], "מחיצה נכתבה על טבלה עם GUID שגוי"
    err = target_error(run)
    # #1924: the error reaches the server (progress/done, the journal) -- the
    # GUIDs stay in the agent's log; the error says what differs.
    assert "מחיצה 2" in err and "אינו של האימג'" in err, err
    assert "AAAAAAAA" not in err and "22222222" not in err, err
    assert "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA, in the manifest 22222222-2222-2222-2222-222222222222" in log_of(run)


@pytest.mark.parametrize("mode,expect", [
    ("sgdisk_failed", "נכשל (rc=2)"),
    ("no_value", "לא הדפיס GUID ייחודי"),
    ("garbage", "שאינו GUID"),
])
def test_a_unique_guid_that_could_not_be_read_back_fails_the_restore(tmp_path, mode, expect):
    """‏"לא הצלחנו לקרוא בחזרה" אינו "תואם" (עיקרון 5), בשלוש צורותיו."""
    box, run, prelude = build_box(
        tmp_path, sgdisk_fail=["-i 2 *"] if mode == "sgdisk_failed" else None)
    if mode != "sgdisk_failed":
        (box / "uguid.lie.2").write_text(
            "" if mode == "no_value" else "garbage\n", newline="\n")
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert wrote(run) == []
    err = target_error(run)
    assert "מחיצה 2" in err and "לא הצלחנו לקרוא בחזרה" in err and expect in err, err


def test_a_partition_without_a_guid_in_the_manifest_is_logged_not_checked(tmp_path):
    """אימג' ישן — או לינוקס שנפטר בקליטה — אינו נכשל, אבל "לא נבדק"
    נרשם בשמו ואינו נספר כ"נבדק, תואם"."""
    fields = PLAN[1].split("|")
    fields[9] = ""
    box, run, prelude = build_box(tmp_path, plan=[PLAN[0], "|".join(fields), PLAN[2]])
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=0", out
    assert state_of(run) == "verifying"   # #1935: done is the caller's, after the name, the drivers and the mark
    log = log_of(run)
    assert "partition 2 has no unique GUID in the manifest -- not checked" in log
    assert "2 partition unique GUIDs came back from the disk" in log


def test_the_readback_ignores_the_case_of_the_hex_digits(tmp_path):
    """‏#1408: המניפסט נושא GUID קנוני (אותיות גדולות) — ‏validate_restore_plan
    דוחה אחר לפני --zap-all. הדיסק הוא שמחזיר כאן אותיות קטנות, וההשוואה
    עדיין אינה תלויה ברישיות."""
    fields = PLAN[1].split("|")
    fields[9] = "ABCDEF00-1111-2222-3333-444444444444"
    box, run, prelude = build_box(tmp_path, plan=[PLAN[0], "|".join(fields), PLAN[2]])
    (box / "uguid.lie.2").write_text("abcdef00-1111-2222-3333-444444444444\n", newline="\n")
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=0", out
    assert state_of(run) == "verifying"   # #1935: done is the caller's, after the name, the drivers and the mark


def test_the_expansion_reads_the_unique_guids_back_too(tmp_path):
    """‏expand_last בונה את המחיצה מחדש (`-d` ואז `-n ... -u`) — ה-GUID
    חייב לשרוד גם אותה. כאן `-u` של ההרחבה "לא נתפס": הטבלה של apply_gpt
    תקינה, ורק אחרי שהיא נקראה בחזרה הדיסק מתחיל לשקר על מחיצה 2."""
    box, run, prelude = build_box(tmp_path)
    (box / "sys" / "block" / "sda").mkdir(parents=True)
    (box / "sys" / "block" / "sda" / "size").write_text("976773168\n")
    lie = f"{posix(box)}/uguid.lie.2"
    out = sh(prelude
             + 'apply_gpt sda m.json || { echo "rc=apply"; exit; }; '
             + f"echo AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA > '{lie}'; "
             + 'expand_last sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "expanding partition 2" in log_of(run)
    assert "מחיצה 2: ה-GUID הייחודי בדיסק אינו של האימג'" in log_of(run)
    assert "partition 2 unique GUID on the disk AAAAAAAA" in log_of(run)   # #1924: the values, in the log only
    assert not (run / "targets" / "sda" / "expanded").exists(), \
        "סימון ההרחבה נכתב בלי שה-GUID אושר"
