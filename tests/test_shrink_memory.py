"""‏#926 — הצד של הסוכן: הרשומה בשרת לפני הכיווץ, וההצעה להחזיר באתחול.

‏#87 שמר את הגודל המקורי של מחיצת המקור ב-`targets/<disk>/shrunk` — ‏tmpfs.
אובדן חשמל בין `ntfsresize -s` לבין ההחזרה השאיר את דיסק הבנייה מכווץ
בלי שאיש יודע. ‏`agent/lib/shrink/shrinkmem.sh`:

* **‏#1466 (16/10): הקליטה כבר אינה פותחת רשומה.** הכיווץ רץ על עותק
  בזיכרון והמקור אינו נכתב -- אין מה לזכור. מה שנבדק כאן הוא לדיסקים
  שכווצו במקום **לפני** #1466: רשומה פתוחה ב-hello עוצרת קליטה, וההחזרה
  באתחול סוגרת אותה (`shrink-close`); החזרה שנכשלה משאירה אותה פתוחה.
* **באתחול** (‏`shrink_offer_pending`, מלולאת מחשב הבנייה): הרשומה מה-hello
  מושווית לכניסה **על הדיסק** (‏sgdisk -i), ורק אם התחילה וה-GUID הייחודי
  תואמים והגודל קטן — שאלה ליד המכונה: החזר / השאר. **בלי תשובה — השאר.**
  ההחזרה היא shrink_restore_source עם הנתונים מהרשומה, לא מ-tmpfs.

הכלים ההרסניים מזויפים כמו ב-`test_shrink_on_capture.py`, וכל צעד נמדד
על הסדר ועל הארגומנטים — לא על "רץ בלי שגיאה".
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix
from test_capture_refusals import CURL_SHRINK_SERVER, JQ_STUB, capture_run, refusal_reason
from test_shrink_on_capture import (
    BLOCKDEV_OK,
    DATA_SECTORS,
    DATA_START,
    DATA_UGUID,
    FS_MAP,
    FS_MAP_C_D,
    MIN_BYTES,
    MIN_D,
    NTFSFIX_OK,
    NTFSRESIZE_GROW_FAILS,
    NTFSRESIZE_OK,
    NTFSRESIZE_TWO,
    SAY_YES,
    SGDISK_C_THEN_D,
    SGDISK_WIN_REC,
    WIN_GUID,
    WIN_SECTORS,
    WIN_START,
    WIN_UGUID,
    manifest,
    order,
    rewrites,
    shrink_target,
    stubs,
)
from test_timeouts import log_of, make_stubs, run_sh
from agentlib import flat_lib, lib_path

pytestmark = requires_native(("bash", BASH))

JQ = shutil.which("jq")
requires_jq = pytest.mark.skipif(JQ is None, reason="jq אינו מותקן — הביטוי לא היה רץ בכלל")

SERIAL = "S926"


def sent(run: Path, name: str) -> list[dict]:
    path = run / f"{name}.sent"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def step(steps: list[str], prefix: str) -> int:
    for i, line in enumerate(steps):
        if line.startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} לא נמצא ב-{steps}")


# --- בקליטה: הרשומה לפני הכיווץ, הסגירה אחרי ההחזרה --------------------------------


@pytest.mark.parametrize("http", ["200", "500", "000"])
def test_a_capture_shrink_asks_the_server_nothing_and_needs_no_serial(tmp_path, http):
    """#1466: המקור אינו נכתב, ולכן אין רשומה: לא shrink-open ולא shrink-close,
    בלי קשר למה שהשרת היה עונה ולמספר הסידורי -- והכיווץ (על העותק) עובר."""
    no_serial = FS_MAP.replace("printf S926", ":")
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=no_serial + SAY_YES,
                                env={"SHRINK_OPEN_HTTP": http})
    assert out.strip().endswith("rc=0"), out
    steps = order(run)
    assert "shrink-open" not in steps and "shrink-close" not in steps, steps
    assert sent(run, "shrink_open") == [] and sent(run, "shrink_close") == []
    assert [st for st in steps if st.startswith("ntfsresize -s ") and "/mapper/lc-src-3" in st], steps
    assert not rewrites(run)


def test_a_pending_record_in_the_hello_answer_refuses_before_the_question(tmp_path):
    """ה-hello שהביא את המשימה אומר שהדיסק הזה כבר מכווץ (רשומה #5):
    הקליטה נעצרת לפני שאדם נשאל — ולפני שהשרת נשאל שוב."""
    pending = ('json_get_join() { echo ok; echo "5|S926|3|1085440|998244352|' + WIN_GUID + '|' + WIN_UGUID
               + '|Basic data partition|0000000000000000|511101108224|t"; }; '
               'shrink_ask() { echo asked >> "$RUN_DIR/order.log"; echo continue; }; ')
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + pending)
    reason = refusal_reason(box, run, out)
    assert "כבר כווצה" in reason and "#5" in reason and "מחיצה 3" in reason, reason
    assert "asked" not in order(run)
    assert sent(run, "shrink_open") == []
    assert not rewrites(run)


# --- באתחול: ההצעה להחזיר, על מחשב הבנייה ------------------------------------------

SHRUNK_SECTORS = shrink_target(MIN_BYTES, WIN_SECTORS * 512) // 512


def record(**over) -> dict:
    rec = {"id": 5, "serial": SERIAL, "idx": 3, "start_sector": WIN_START, "size_sectors": WIN_SECTORS,
           "type_guid": WIN_GUID, "unique_guid": WIN_UGUID, "name": "Basic data partition",
           "attrs": "0000000000000000", "ntfs_bytes": WIN_SECTORS * 512,
           "opened_at": "2026-09-16T20:00:00+00:00", "mac": "b4:2e:99:07:1a:c4", "port": 1,
           "dev": "sda", "model": "Samsung SSD", "image_name": "win11", "task_id": "tsk_1"}
    rec.update(over)
    return rec


PART_KEYS = ("idx", "start_sector", "size_sectors", "type_guid", "unique_guid", "name", "attrs", "ntfs_bytes")


def two_part_record(**over) -> dict:
    """‏#929: רשומה אחת לדיסק עם **שתי** מחיצות — ‏C: (3) ו-D: (4) — כפי שהשרת
    עונה ב-hello: השדות העליונים הם של הראשונה (תאימות), `partitions` את כולן."""
    rec = record(**over)
    d = {"idx": 4, "start_sector": DATA_START, "size_sectors": DATA_SECTORS, "type_guid": WIN_GUID,
         "unique_guid": DATA_UGUID, "name": "Data", "attrs": "0000000000000000", "ntfs_bytes": DATA_SECTORS * 512}
    rec["partitions"] = [{k: rec[k] for k in PART_KEYS}, d]
    return rec


def record_line(rec: dict, part: dict | None = None) -> str:
    part = part or rec
    return "|".join(str(rec[k]) for k in ("id", "serial")) + "|" \
        + "|".join(str(part[k]) for k in ("idx", "start_sector", "size_sectors", "type_guid", "unique_guid",
                                           "name", "attrs", "ntfs_bytes")) + f"|{rec['opened_at']}"


def record_lines(rec: dict) -> list[str]:
    """מה ש-shrink_records_refresh מפיק: שורה ל**מחיצה** (‏#929), עם ה-id
    והסידורי של הרשומה בראשה; רשומה בלי `partitions` (שרת ישן) — שורה אחת."""
    return [record_line(rec, p) for p in rec.get("partitions") or [rec]]


def fake_jq(*records: dict) -> str:
    """‏json_get_join מזויף: מה ש-jq היה מדפיס על התשובה — "ok" ואז שורה
    למחיצה (הביטוי האמיתי נבדק ב-test_the_real_jq_expression…)."""
    lines = ["ok"]
    for r in records:
        lines.extend(record_lines(r))
    body = " ".join(f"echo {line!r};" for line in lines)
    return 'json_get_join() { [ -s "$1" ] || return 1; ' + body + ' }; '


GUI_ASK_STUB = (
    'gui_ask_choice() { echo "ASK $1 $2" >&2; '
    'while IFS= read -r _r; do echo "ROW $_r" >&2; done < "$3"; '
    '_a=$(cat "$RUN_DIR/gui_answer"); [ -n "$_a" ] || return 1; echo "$_a"; }; '
)


def offer_run(tmp_path, *, answer_records: list[dict] | None, keys: str = "",
              disk_sectors: int = SHRUNK_SECTORS, prelude: str = "", calls: int = 1,
              extra_stubs: dict | None = None, sizes: str | None = None):
    """מריץ `shrink_offer_pending` (מה שלולאת מחשב הבנייה מריצה לפני
    התפריט) מול דיסק מזויף שהכניסה 3 שלו היא `disk_sectors` סקטורים
    (או `sizes` — תוכן sgdisk.sizes המלא, לדיסק עם יותר ממחיצה מכווצת אחת)."""
    box = tmp_path / "box"
    run = box / "run"
    run.mkdir(parents=True)
    (box / "dev").mkdir()
    (box / "dev" / "sda").write_bytes(bytes(1024))
    sysd = box / "sys" / "block" / "sda"
    (sysd / "device").mkdir(parents=True)
    (sysd / "queue").mkdir()
    (sysd / "device" / "serial").write_text(SERIAL + "\n", encoding="utf-8")
    (sysd / "queue" / "logical_block_size").write_text("512\n", encoding="utf-8")
    (sysd / "removable").write_text("0\n", encoding="utf-8")
    (run / "sgdisk.sizes").write_text(sizes if sizes is not None else f"3={disk_sectors}\n4=2048000\n",
                                      encoding="utf-8", newline="\n")
    if answer_records is not None:
        (run / "response.json").write_text(json.dumps({"schema": 1, "known": True, "role": "build",
                                                       "shrink_open": answer_records}),
                                           encoding="utf-8", newline="\n")
    stub = {"sgdisk": SGDISK_WIN_REC, "ntfsresize": NTFSRESIZE_OK, "ntfsfix": NTFSFIX_OK,
            "blockdev": BLOCKDEV_OK, "curl": CURL_SHRINK_SERVER}
    stub.update(extra_stubs or {})
    if JQ is None:
        stub["jq"] = JQ_STUB
    libs = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in (
        "common", "sysinfo", "waits", "jsonq", "restore", "ui", "attended",
        "shrinkplan", "shrink", "shrinkmem", "shrinkoffer", "askgui"))   # ‏#929: ההצעה באתחול ב-shrinkoffer.sh
    # קלט מקובץ ולא מצינור: פונקציה בצינור רצה בתת-מעטפת, ו-`_shrink_offered`
    # (פעם אחת לאתחול) לא היה שורד — בסוכן הקריאה ישירה, כמו כאן.
    (run / "keys.txt").write_text(keys, encoding="utf-8", newline="\n")
    keys_sh = f"< {posix(run / 'keys.txt')!r} "
    # ‏11/10 (GUI בלבד): השאלה היא דיאלוג של הגואי (askgui.sh). ‏`keys` הוא
    # כעת **תשובת הגואי** — "1" = restore, "2" = leave, ריק = אין תשובה —
    # והמקשים עצמם נשארים ב-stdin, שהקוד אינו קורא. הזיוף מדפיס את הבקשה
    # (ASK/ROW) — הראיה החיובית שהשאלה הוצגה ומה היה בה.
    (run / "gui_answer").write_text({"1": "restore", "2": "leave"}.get(keys[:1], ""),
                                    encoding="utf-8", newline="\n")
    out = run_sh(
        make_stubs(box / "stubs", stub)
        + f"export IMAGESRV_TEST=1 RUN_DIR={posix(run)!r} DEVROOT={posix(box / 'dev')!r} "
        f"SYSROOT={posix(box)!r} SERVER=http://s MAC=b4:2e:99:07:1a:c4; "
        + libs + " attended_hello() { :; }; " + GUI_ASK_STUB + prelude
        + " ".join(f"{keys_sh}shrink_offer_pending; echo \"rc=$?\";" for _ in range(calls))
    )
    return box, run, out


def test_a_pending_record_asks_and_one_grows_the_partition_back(tmp_path):
    """הדיסק עומד על הגודל המכווץ, הרשומה תואמת (תחילה + GUID ייחודי):
    השאלה מוצגת, "1" מחזיר — הטבלה **מהרשומה** (הגודל המקורי, GUID, שם,
    דגלים), ואז ntfsresize -f -f, ‏ntfsfix -d, ואז shrink-close עם ה-id."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", prelude=fake_jq(record()))
    assert "rc=0" in out, out
    assert "ASK growback sda" in out and "|table" in out, out
    (call,) = rewrites(run)
    assert call.startswith("-a 1 -d 3 -n 3:"), call
    assert f"-n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1} -t 3:{WIN_GUID} -u 3:{WIN_UGUID} -c 3:Basic data partition -A 3:=:0x0000000000000000" in call, call
    steps = order(run)
    node = f"{posix(box / 'dev')}/sda3"
    reread = step(steps, "blockdev --rereadpt")
    grow = step(steps, f"ntfsresize -f -f --no-progress-bar {node}")
    fix = step(steps, f"ntfsfix -d {node}")
    close = step(steps, "shrink-close")
    assert reread < grow < fix < close, steps
    assert not [s for s in steps if s.startswith("ntfsresize -s")], "החזרה, לא כיווץ"
    (body,) = sent(run, "shrink_close")
    assert body["serial"] == SERIAL and body["id"] == 5
    assert "back to its original size" in out
    assert "restored to" in log_of(run)


@pytest.mark.parametrize("keys", ["2\n", ""])
def test_leave_and_no_answer_write_nothing(tmp_path, keys):
    """‏[2] וגם EOF (מסך שנעלם): שום דבר לא נכתב, הרשומה נשארת פתוחה —
    עיקרון 1, שום כתיבה על דיסק בלי אדם שאמר כן."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys=keys, prelude=fake_jq(record()))
    assert "rc=0" in out, out
    assert "ASK growback sda" in out, "השאלה כן הוצגה"
    assert rewrites(run) == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize", "ntfsfix", "blockdev"))]
    assert sent(run, "shrink_close") == []
    assert "left at" in log_of(run) and "stays open" in log_of(run)


def test_a_partition_already_at_its_size_closes_the_record_without_asking(tmp_path):
    """הסגירה של הקליטה הקודמת לא הגיעה לשרת (או שהמפעיל הרחיב בעצמו):
    הדיסק כבר בגודלו — הרשומה נסגרת, בלי שאלה ובלי כתיבה."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()) + 'shrink_restore_ask() { echo asked >> "$RUN_DIR/order.log"; echo restore; }; ')
    assert "rc=0" in out, out
    assert "asked" not in order(run)
    assert rewrites(run) == []
    # ‏ntfsresize --info (קריאה בלבד) כן רץ: הוא מה שמוכיח שמערכת הקבצים ממלאת
    # את המחיצה (סקירת Fable) — כתיבה לא.
    assert not [s for s in order(run) if s.startswith(("ntfsresize -s", "ntfsresize -f", "ntfsfix"))]
    (body,) = sent(run, "shrink_close")
    assert body["id"] == 5 and body["serial"] == SERIAL
    assert "already" in log_of(run) and "closing the record" in log_of(run)


@pytest.mark.parametrize("field, value", [("unique_guid", "4C7B1E00-0000-4000-8000-00000000BEEF"),
                                          ("start_sector", WIN_START + 2048)])
def test_an_entry_that_does_not_match_the_record_is_not_touched(tmp_path, field, value):
    """הרשומה אומרת מחיצה אחרת ממה שעל הדיסק (GUID ייחודי / תחילה):
    לא שואלים, לא כותבים, לא סוגרים — אזהרה ביומן שאומרת מה נקרא."""
    rec = record(**{field: value})
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n",
                              prelude=fake_jq(rec) + 'shrink_restore_ask() { echo asked >> "$RUN_DIR/order.log"; echo restore; }; ')
    assert "rc=0" in out, out
    assert "asked" not in order(run)
    assert rewrites(run) == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize", "ntfsfix", "blockdev"))]
    assert sent(run, "shrink_close") == []
    assert "does not match the disk" in log_of(run) and "nothing changed" in log_of(run)


def test_a_failed_grow_back_at_boot_shows_the_error_and_keeps_the_record(tmp_path):
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", prelude=fake_jq(record()),
                              extra_stubs={"ntfsresize": NTFSRESIZE_GROW_FAILS})
    # ההצעה אינה עוצרת את המכונה (rc=0 של shrink_offer_pending) — הכשל
    # נראה על המסך ונשאר בשרת, ומחשב הבנייה ממשיך לתפריט.
    assert "rc=0" in out, out
    assert "FAILED:" in out and "המקור לא הוחזר לגודלו" in out, out
    assert "The record stays in the console" in out
    assert sent(run, "shrink_close") == []


def test_the_question_is_asked_once_per_boot_and_nothing_pending_asks_nobody(tmp_path):
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="2\n", prelude=fake_jq(record()), calls=3)
    assert out.count("ASK growback") == 1, out
    assert out.count("rc=0") == 3

    box, run, out = offer_run(tmp_path / "none", answer_records=[], keys="1\n", prelude=fake_jq())
    assert "rc=0" in out and "ASK growback" not in out
    assert rewrites(run) == [] and sent(run, "shrink_close") == []

    # תשובה שלא נקראה (jq נפל) אינה "אין רשומות": לא שואלים, ולא מכריזים על נקי.
    box, run, out = offer_run(tmp_path / "unreadable", answer_records=[record()], keys="1\n",
                              prelude='json_get_join() { return 5; }; ')
    assert "rc=0" in out and "ASK growback" not in out
    assert not (run / "shrink_open").exists() and not (run / "shrink_open.next").exists()


def test_a_disk_with_another_serial_is_not_the_one(tmp_path):
    """הרשומה על S-OTHER; הדיסק כאן S926 — לא מציעים ולא נוגעים."""
    rec = record(serial="S-OTHER")
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n", prelude=fake_jq(rec))
    assert "rc=0" in out and "ASK growback" not in out, out
    assert rewrites(run) == [] and sent(run, "shrink_close") == []


# --- סקירת Fable (PR #946), ממצא 2: הטבלה הוחזרה אך מערכת הקבצים לא נמתחה ---------

#: ‏ntfsresize שמדווח ב---info ווליום **קטן** בתוך מחיצה בגודלה המלא —
#: המצב אחרי "טבלה נכתבה, ntfsresize -f -f נפל" (או אחרי אתחול באמצע):
#: ווינדוס אינו יכול להרחיב (אין שטח לא-מוקצה), והמחיצה "בגודלה".
NTFSRESIZE_SMALL_FS = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "Cluster size       : 4096 bytes"
            echo "Current volume size: {SHRUNK_SECTORS * 512 - 512} bytes"
            exit 0 ;;
  *"-f -f"*) exit "${{NTFSRESIZE_GROW_RC:-0}}" ;;
esac
exit 0
'''
#: ‏--info שלא הדפיס גודל ווליום כלל: "לא הצלחנו לבדוק" ≠ "מתאים".
NTFSRESIZE_INFO_BLIND = '#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\necho "ERROR: NTFS is inconsistent"\nexit 1\n'


def test_a_full_partition_with_a_small_filesystem_is_offered_a_grow_not_closed_silently(tmp_path):
    """הכניסה כבר בגודלה המקורי, אבל מערכת הקבצים בפנים עדיין ~110GB: סגירה
    שקטה הייתה משאירה דיסק שווינדוס לא יכול להרחיב ואף אחד לא רואה. במקום
    זה: שאלה ליד המכונה — [1] מותח את מערכת הקבצים (ntfsresize -f -f →
    ntfsfix -d) **בלי לגעת בטבלה**, ורק אז shrink-close."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()), extra_stubs={"ntfsresize": NTFSRESIZE_SMALL_FS})
    assert "rc=0" in out, out
    assert "ASK growback" in out and "|fs" in out and "|table" not in out, out
    assert rewrites(run) == [], "הטבלה כבר נכונה — לא נכתבת שוב"
    steps = order(run)
    node = f"{posix(box / 'dev')}/sda3"
    info = step(steps, f"ntfsresize --info --no-progress-bar {node}")
    grow = step(steps, f"ntfsresize -f -f --no-progress-bar {node}")
    fix = step(steps, f"ntfsfix -d {node}")
    close = step(steps, "shrink-close")
    assert info < grow < fix < close, steps
    (body,) = sent(run, "shrink_close")
    assert body["id"] == 5
    assert "filesystem grown" in log_of(run)


@pytest.mark.parametrize("keys", ["2\n", ""])
def test_leaving_a_small_filesystem_keeps_the_record_open(tmp_path, keys):
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys=keys, disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()), extra_stubs={"ntfsresize": NTFSRESIZE_SMALL_FS})
    assert "rc=0" in out and "ASK growback" in out and "|fs" in out, out
    assert not [s for s in order(run) if s.startswith(("ntfsresize -f", "ntfsfix", "blockdev"))]
    assert sent(run, "shrink_close") == [] and rewrites(run) == []
    assert "stays open" in log_of(run)


def test_a_failed_filesystem_grow_keeps_the_record_open_with_the_reason(tmp_path):
    """‏[1] ו-ntfsresize -f -f נופל: אין close, המסך אומר FAILED, והסיבה
    נשלחת לשרת (shrink-note) כדי שהקונסולה תראה **למה** הרשומה פתוחה."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()) + "export NTFSRESIZE_GROW_RC=5; ",
                              extra_stubs={"ntfsresize": NTFSRESIZE_SMALL_FS})
    assert "rc=0" in out and "FAILED:" in out and "The record stays in the console" in out, out
    assert sent(run, "shrink_close") == []
    (note,) = sent(run, "shrink_note")
    assert note["id"] == 5 and note["serial"] == SERIAL
    assert "ntfsresize" in note["note"] and "מערכת הקבצים" in note["note"]
    assert not [s for s in order(run) if s.startswith("ntfsfix")], "בלי ntfsfix אחרי מתיחה שנפלה"


def test_a_filesystem_size_that_cannot_be_read_is_not_a_fit(tmp_path):
    """‏--info שלא הדפיס גודל: "לא הצלחנו לבדוק" אינו "מתאים" — לא סוגרים,
    לא שואלים על מתיחה (אין מספר להציג), אזהרה ביומן והרשומה נשארת."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()), extra_stubs={"ntfsresize": NTFSRESIZE_INFO_BLIND})
    assert "rc=0" in out and "ASK growback" not in out, out
    assert sent(run, "shrink_close") == [] and rewrites(run) == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize -f", "ntfsfix"))]
    assert "could not read the NTFS volume size" in log_of(run) and "stays open" in log_of(run)


def test_two_disks_with_the_same_serial_are_ambiguous_and_neither_is_touched(tmp_path):
    """שני דיסקים במכונה עם אותו סידורי ("0000000000000000" של גשר USB, ‏VM
    בלי סידורי אמיתי): הרשומה יכולה להיות של כל אחד מהם, והשער השני אינו
    מבחין — שחזור מאותו אימג' שומר תחילה ו-GUID ייחודי (#26), ודיסק שכבר
    "בגודלו" היה סוגר בשקט את הרשומה של אחיו המכווץ. לא שואלים, לא כותבים,
    לא סוגרים — אזהרה ביומן שנוקבת בסידורי."""
    twin = ('mkdir -p "$SYSROOT/sys/block/sdb/device"; echo S926 > "$SYSROOT/sys/block/sdb/device/serial"; '
            'cp "$DEVROOT/sda" "$DEVROOT/sdb"; ')
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", prelude=fake_jq(record()) + twin)
    assert "rc=0" in out and "ASK growback" not in out, out
    assert rewrites(run) == [] and sent(run, "shrink_close") == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize", "ntfsfix", "blockdev"))]
    assert "same serial" in log_of(run) and "S926" in log_of(run) and "nothing changed" in log_of(run)


@requires_jq
def test_the_real_jq_expression_reads_the_hello_answer(tmp_path):
    """הביטוי האמיתי של shrink_records_refresh מול jq אמיתי: "ok" ואז
    שורה **למחיצה** (‏#929) בסדר השדות ש-shrink_offer_disk קורא — רשומה עם
    שתי מחיצות היא שתי שורות עם אותו id; רשומה ישנה בלי `partitions` (שרת
    שקדם ל-#929) עדיין שורה אחת מהשדות העליונים."""
    old = record(id=9, serial="Z", unique_guid=None, name=None)
    box, run, out = offer_run(tmp_path, answer_records=[two_part_record(), old], keys="2\n",
                              sizes=f"3={SHRUNK_SECTORS}\n4={DATA_SECTORS}\n", extra_stubs={"sgdisk": SGDISK_C_THEN_D})
    assert "rc=0" in out, out
    lines = (run / "shrink_open").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ok"
    assert lines[1:3] == record_lines(two_part_record())
    assert lines[3] == "9|Z|3|1085440|998244352|" + WIN_GUID + "|||0000000000000000|" + str(WIN_SECTORS * 512) + "|2026-09-16T20:00:00+00:00"
    assert len(lines) == 4
    assert "ASK growback" in out, "והרשומה של S926 אכן זוהתה מהשורה שנקראה"


# --- ‏#929: רשומה אחת לדיסק עם כמה מחיצות — ההצעה מחזירה את כולן ------------------

#: ‏ntfsresize לפי מחיצה, לצד השחזור באתחול: ‏--info על sda3 מדווח ווליום מלא
#: (‏C: כבר בגודלה) או קטן (NTFSRESIZE_SMALL_3=1), על sda4 — מלא; ‏-f -f נופל
#: על המחיצה ב-NTFSRESIZE_GROW_FAIL_ON בלבד.
NTFSRESIZE_BY_NODE = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "Cluster size       : 4096 bytes"
            case "$*" in
              *3) [ "${{NTFSRESIZE_SMALL_3:-0}}" = 1 ] && echo "Current volume size: {SHRUNK_SECTORS * 512 - 512} bytes" \\
                  || echo "Current volume size: {WIN_SECTORS * 512 - 512} bytes" ;;
              *) echo "Current volume size: {DATA_SECTORS * 512 - 512} bytes" ;;
            esac
            exit 0 ;;
  *"-f -f"*) [ -n "${{NTFSRESIZE_GROW_FAIL_ON:-}}" ] && case "$*" in *"$NTFSRESIZE_GROW_FAIL_ON") exit 5 ;; esac ;;
esac
exit 0
'''
SHRUNK_D_SECTORS = shrink_target(MIN_D, DATA_SECTORS * 512) // 512


def two_part_run(tmp_path, *, sizes: str, keys: str = "1\n", prelude: str = "", ntfsresize: str = NTFSRESIZE_BY_NODE):
    return offer_run(tmp_path, answer_records=[two_part_record()], keys=keys, sizes=sizes,
                     prelude=fake_jq(two_part_record()) + prelude,
                     extra_stubs={"sgdisk": SGDISK_C_THEN_D, "ntfsresize": ntfsresize})


def test_a_two_partition_record_asks_once_and_grows_both_back_in_reverse(tmp_path):
    """שתי המחיצות עומדות על גודלן המכווץ, שתי הכניסות תואמות: שאלה **אחת**
    שמציגה את שתיהן, "1" מחזיר — ‏D: ואז C: (סדר הפוך לכיווץ), כל אחת
    טבלה → מתיחה → fix — ו-shrink-close אחד, אחרי ששתיהן חזרו."""
    box, run, out = two_part_run(tmp_path, sizes=f"3={SHRUNK_SECTORS}\n4={SHRUNK_D_SECTORS}\n")
    assert "rc=0" in out, out
    assert out.count("ASK growback") == 1, out
    assert "ROW 3|" in out and "ROW 4|" in out and out.count("|table") == 2, out
    calls = rewrites(run)
    assert [c.split(" -d ")[1].split(" ")[0] for c in calls] == ["4", "3"], calls
    assert f"-n 4:{DATA_START}:{DATA_START + DATA_SECTORS - 1} -t 4:{WIN_GUID} -u 4:{DATA_UGUID} -c 4:Data -A 4:=:0x0000000000000000" in calls[0], calls[0]
    assert f"-n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1} -t 3:{WIN_GUID} -u 3:{WIN_UGUID}" in calls[1], calls[1]
    steps = order(run)
    dev = posix(box / "dev")
    grow4 = step(steps, f"ntfsresize -f -f --no-progress-bar {dev}/sda4")
    grow3 = step(steps, f"ntfsresize -f -f --no-progress-bar {dev}/sda3")
    close = step(steps, "shrink-close")
    assert grow4 < steps.index(f"ntfsfix -d {dev}/sda4", grow4) < grow3 < steps.index(f"ntfsfix -d {dev}/sda3", grow3) < close, steps
    assert steps.count("shrink-close") == 1
    (body,) = sent(run, "shrink_close")
    assert body["id"] == 5 and body["serial"] == SERIAL


def test_a_partition_that_is_already_back_is_skipped_and_the_other_is_offered(tmp_path):
    """‏C: כבר בגודלה ומערכת הקבצים ממלאת אותה (החזרה שנקטעה אחרי C:);
    ‏D: עדיין מכווצת. השאלה על D: בלבד, הטבלה של C: אינה נכתבת שוב, והרשומה
    נסגרת אחרי ש-D: חזרה."""
    box, run, out = two_part_run(tmp_path, sizes=f"3={WIN_SECTORS}\n4={SHRUNK_D_SECTORS}\n")
    assert "rc=0" in out, out
    assert "ROW 4|" in out and "ROW 3|" not in out, out
    assert [c.split(" -d ")[1].split(" ")[0] for c in rewrites(run)] == ["4"], rewrites(run)
    assert "partition 3 is already" in log_of(run)
    (body,) = sent(run, "shrink_close")
    assert body["id"] == 5


def test_one_entry_that_does_not_match_stops_the_whole_offer(tmp_path):
    """הכניסה של C: תואמת, זו של D: לא (GUID ייחודי אחר): לא שואלים על אף
    אחת, לא כותבים, לא סוגרים — הרשומה היא לדיסק, וחצי-החזרה אינה החזרה."""
    rec = two_part_record()
    rec["partitions"][1]["unique_guid"] = "4C7B1E00-0000-4000-8000-00000000BEEF"
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n", sizes=f"3={SHRUNK_SECTORS}\n4={SHRUNK_D_SECTORS}\n",
                              prelude=fake_jq(rec) + 'shrink_restore_ask() { echo asked >> "$RUN_DIR/order.log"; echo restore; }; ',
                              extra_stubs={"sgdisk": SGDISK_C_THEN_D, "ntfsresize": NTFSRESIZE_BY_NODE})
    assert "rc=0" in out, out
    assert "asked" not in order(run) and rewrites(run) == [] and sent(run, "shrink_close") == []
    assert "partition 4" in log_of(run) and "does not match the disk" in log_of(run) and "nothing changed" in log_of(run)


def test_the_record_closes_only_when_every_partition_is_back(tmp_path):
    """‏C: בטבלה בגודלה אך מערכת הקבצים קטנה (מתיחה שנפלה בפעם הקודמת);
    ‏D: מכווצת בטבלה. "1" מותח את C: (בלי טבלה) ומחזיר את D: (טבלה+מתיחה);
    כשהמתיחה של D: נופלת — אין shrink-close, המסך אומר FAILED, והסיבה
    (shrink-note) נוקבת במחיצה 4 — למרות ש-C: הצליחה."""
    box, run, out = two_part_run(tmp_path, sizes=f"3={WIN_SECTORS}\n4={SHRUNK_D_SECTORS}\n",
                                 prelude="export NTFSRESIZE_SMALL_3=1 NTFSRESIZE_GROW_FAIL_ON=4; ")
    assert "rc=0" in out, out
    assert "ROW 3|" in out and "|fs" in out and "ROW 4|" in out, out
    assert out.count("ASK growback") == 1, out
    dev = posix(box / "dev")
    steps = order(run)
    assert [c.split(" -d ")[1].split(" ")[0] for c in rewrites(run)] == ["4"], "הטבלה של C: כבר נכונה — לא נכתבת"
    grow3 = step(steps, f"ntfsresize -f -f --no-progress-bar {dev}/sda3")
    assert steps.index(f"ntfsfix -d {dev}/sda3", grow3), "‏C: נמתחה ונוקתה"
    assert [s for s in steps if s.startswith(f"ntfsresize -f -f --no-progress-bar {dev}/sda4")]
    assert not [s for s in steps if s == f"ntfsfix -d {dev}/sda4"], "בלי ntfsfix אחרי מתיחה שנפלה"
    assert sent(run, "shrink_close") == [] and "FAILED:" in out and "The record stays in the console" in out
    (note,) = sent(run, "shrink_note")
    assert note["id"] == 5 and "מחיצה 4" in note["note"], note
    assert "מחיצה 3" not in note["note"], "‏C: הצליחה — היא אינה הסיבה"


def test_a_pending_two_partition_record_in_the_hello_refuses_the_capture_naming_both(tmp_path):
    """הצד של הקליטה: ה-hello מביא רשומה פתוחה על שתי מחיצות — הקליטה
    נעצרת לפני השאלה, וההודעה נוקבת בשתיהן וברשומה."""
    # בלי `[ -s "$1" ]` של fake_jq: בקופסת הקליטה אין response.json, והזיוף חייב
    # לענות בכל זאת — כמו ב-test_a_pending_record_in_the_hello_answer_refuses_before_the_question.
    pending = ('json_get_join() { ' + " ".join(f"echo {line!r};" for line in ("ok", *record_lines(two_part_record())))
               + ' }; shrink_ask() { echo asked >> "$RUN_DIR/order.log"; echo continue; }; ')
    box, run, out = capture_run(tmp_path, stubs=stubs(sgdisk=SGDISK_C_THEN_D, ntfsresize=NTFSRESIZE_TWO),
                                shell_pre=FS_MAP_C_D + pending)
    reason = refusal_reason(box, run, out)
    assert "כבר כווצה" in reason and "#5" in reason and "מחיצה 3, 4" in reason, reason
    assert "asked" not in order(run) and sent(run, "shrink_open") == [] and not rewrites(run)


# --- הנעילות המבניות ------------------------------------------------------------


def test_the_agent_loads_shrinkmem_after_shrink_and_offers_after_the_gui():
    src = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert src.index('"$LIB_DIR/shrinkplan.sh"') < src.index('"$LIB_DIR/shrink.sh"') < src.index('"$LIB_DIR/shrinkmem.sh"') \
        < src.index('"$LIB_DIR/shrinkoffer.sh"') < src.index('"$LIB_DIR/capture.sh"')
    body = src[src.index("build_console_screen()"):src.index("do_restore_drawers()")]   # #1217: do_task -> task.sh
    # 11/10: the grow-back is a GUI dialog -- the GUI comes up first, then the offer.
    assert body.index("gui_parent") < body.index("shrink_offer_pending")


@pytest.mark.parametrize("name", ["shrinkmem.sh", "shrinkoffer.sh"])
def test_no_destructive_step_in_shrinkmem_hides_its_exit_code(name):
    src = (lib_path(name)).read_text(encoding="utf-8")
    assert "|| true" not in src
    for line in src.splitlines():
        if "sgdisk -i" in line and not line.strip().startswith("#"):
            assert "2>/dev/null" not in line, line
    assert "curl -sfS" not in src, "בלי -f: 409 ו-000 הן שתי תשובות שונות"


def test_the_interfaces_document_describes_the_memory():
    text = (REPO / "docs" / "interfaces.md").read_text(encoding="utf-8")
    for needle in ("`shrink_open`", "/api/v1/agent/shrink-open", "/api/v1/agent/shrink-close",
                   "/api/console/shrink-records", "shrink_records"):
        assert needle in text, needle
    assert "**הרשומה של הגודל המקורי יושבת ב-tmpfs בלבד**" not in text, "התיאור הישן של #87 — הרשומה כבר בשרת"
