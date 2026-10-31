"""‏#435 — פס ההתקדמות של קליטה: מכנה אמיתי, או "לא ידוע" בגלוי.

בקליטה ה-`pv` סופר בייטים **דחוסים** (אחרי zstd), ואת הגודל הדחוס אי
אפשר לדעת מראש — ולכן `bytes_total` נשאר אפס. הבאג לא היה האפס, אלא
שהאפס **הוצג כאחוז**: ``bytes_total = 0`` רונדר בדיוק כמו ``width:0%``,
ומפעיל ראה קליטה תקינה של 20 דקות עם פס ריק (עיקרון 5, בצורתו התצוגתית).

המכנה הכן קיים — בציר הלא-דחוס. ‏partclone יודע כמה בלוקים במחיצה
ומדפיס אחוז ל-`-L logfile`. הסוכן קורא אותו ומדווח `source_progress`
נפרד (מחיצה, בלוקים-שנקראו, בלוקים-בסך), והשרת מעביר אותו לקונסולה.
**"לא הצלחנו לקרוא" מדווח כהיעדר** — לא כאפס — והקונסולה מציגה אז פס
בלתי-מוגדר (עיקרון 5).

שתי שכבות נבדקות כאן:
* השרת — הדיווח נשמר ל-`tasks.targets_json` ו-`/api/console/tasks`
  מוציא `source_progress` למעלה, או `None` כשאין. נבדק בווינדוס.
* הסוכן — `build_progress` גוזר את המכנה מ-`partclone.log`. נבדק תחת
  ‏`busybox ash` (מדולג על תחנת ווינדוס — הסוכן רץ תחת busybox, לא awk
  של המכונה).
"""

from __future__ import annotations

import json

import pytest

from server.core.tasks import TOKEN_HEADER
from test_capture import make_task, setup_build_machine, task_token
from test_json_escape import BUSYBOX, bb, source_line
from native import requires_native

pytest.importorskip("fastapi")


def _report(task_id, mac, target):
    return {"task_id": task_id, "mac": mac, "state": "capturing",
            "targets": [target]}


def _post(server, report):
    """הדיווח נשלח עם אסימון המשימה, כמו הסוכן (#855)."""
    return server["anon"].post(
        "/api/v1/agent/progress", json=report,
        headers={TOKEN_HEADER: task_token(server, report["task_id"])})


def _console_task(server):
    tasks = server["admin"].get("/api/console/tasks").json()
    return tasks[0]


# --- השרת: מה שהקונסולה מקבלת ------------------------------------------------


def test_a_capture_report_carrying_source_progress_reaches_the_console(server):
    """המכנה הלא-דחוס עובר דרך `tasks.targets_json` אל הקונסולה."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    report = _report(task_id, mac, {
        "dev": "sda", "bytes_written": 58801287168, "bytes_total": 0,
        "state": "capturing",
        "source_progress": {"partition": 3, "blocks_read": 118, "blocks_total": 1000},
    })
    assert _post(server, report).json()["ok"]
    task = _console_task(server)
    assert task["source_progress"] == {"partition": 3, "blocks_read": 118,
                                       "blocks_total": 1000}


def test_a_capture_without_a_denominator_reports_absent_not_zero(server):
    """‏`bytes_total = 0` ו-`source_progress` חסר → הקונסולה מקבלת
    ‏`None` בשניהם. זה מה שמונע את הקיפול ל-`0%` (עיקרון 5, ה-DoD)."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    report = _report(task_id, mac, {
        "dev": "sda", "bytes_written": 58801287168, "bytes_total": 0,
        "state": "capturing"})
    assert _post(server, report).json()["ok"]
    task = _console_task(server)
    assert task["bytes_total"] is None, "אפס הוצג כמכנה — הקיפול חזר"
    assert task["source_progress"] is None
    assert task["bytes_written"] == 58801287168


def test_a_contradictory_block_total_is_dropped_not_shown(server):
    """מכנה 0 או `blocks_read > blocks_total` הוא "לא ידוע", לא אחוז.
    השרת מוריד אותו, ולא מעביר לקונסולה מספר שאי אפשר להאמין לו."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    report = _report(task_id, mac, {
        "dev": "sda", "bytes_written": 1, "bytes_total": 0, "state": "capturing",
        "source_progress": {"partition": 3, "blocks_read": 5, "blocks_total": 0}})
    assert _post(server, report).json()["ok"]
    assert _console_task(server)["source_progress"] is None


# --- הסוכן: גזירת המכנה מ-partclone ------------------------------------------

requires_busybox = requires_native(
    ("busybox", BUSYBOX), posix=True,
    why="הסוכן רץ תחת busybox ash, לא תחת awk של המכונה")

#: לוג partclone אמיתי באמצע קריאת מחיצה. הפורמט — ‏`Starting to clone
#: device (/dev/sdaN)`, ‏`Space in use: ... = N Blocks`, ‏`Completed: P%`
#: — הוא זה ש-FOG/Clonezilla קוראים אף הם. ⚠️ **הפורמט המדויק אומת מול
#: partclone אמיתי במעבדה** (ראה דוח המשימה): אם גרסה עתידית תשנה שורה,
#: הפרסר מחזיר היעדר, והקונסולה נופלת לפס בלתי-מוגדר — לא לאחוז שגוי.
PCL_LOG = """Partclone v0.3.27 http://partclone.org
Starting to clone device (/dev/sda3) to image (-)
Reading Super Block
File system:  NTFS
Device size:  53.7 GB = 13107200 Blocks
Space in use:   6.8 GB = 1665979 Blocks
Free Space:   46.9 GB = 11441221 Blocks
Block size:   4096 Byte
Elapsed: 00:00:03, Remaining: 00:00:22, Completed:  11.80%, Rate: 2.10GB/min
"""


def _build_progress(tmp_path, log=None, who="task", partclone=True):
    """מריץ את `build_progress` האמיתי מ-`progress.sh` ומחזיר את ה-JSON.

    ‏`who="task"` = קליטה (task id), ‏`who="session"` = סבב כיתה. רק
    לקליטה אמור לצאת `source_progress`.
    """
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    if partclone and log is not None:
        (run / "targets" / "sda" / "partclone.log").write_text(log, encoding="utf-8")
    ids = ('"" b4:2e:99:07:1a:c4 tsk_4e10' if who == "task"
           else 'ses_1 b4:2e:99:07:1a:c4')
    out = bb(
        tmp_path,
        f'export RUN_DIR="{run.as_posix()}"\n'
        + source_line("common.sh", "progress.sh")
        + 'target_init sda 0\n'
        f'build_progress {ids}\n',
    )
    return json.loads(out)


@requires_busybox
def test_build_progress_emits_the_partclone_block_denominator(tmp_path):
    body = _build_progress(tmp_path, PCL_LOG)
    sp = body["targets"][0]["source_progress"]
    assert sp["partition"] == 3
    assert sp["blocks_total"] == 1665979
    # ‏11.80% מתוך 1,665,979 = 196,585. הבלוקים, לא הבייטים הדחוסים.
    assert sp["blocks_read"] == int(0.118 * 1665979)


@requires_busybox
def test_a_partition_still_reading_its_super_block_reports_no_progress(tmp_path):
    """‏`Starting to clone` נכתב, אבל אין עדיין `Completed:` — היעדר,
    לא אפס. זה מה שמונע פס שקופץ ל-0% ברגע שהמחיצה נפתחת."""
    early = "\n".join(PCL_LOG.splitlines()[:-1]) + "\n"
    body = _build_progress(tmp_path, early)
    assert "source_progress" not in body["targets"][0]


@requires_busybox
def test_a_new_partition_never_inherits_the_previous_block_percent(tmp_path):
    """מחיצה חדשה מאפסת את המונה. הלוג עדיין מחזיק את ה-`Completed:
    100%` של הקודמת, אבל `Starting to clone` של החדשה מבטל אותו —
    אחרת פס של מחיצה חדשה היה מתחיל מ-100% של הקודמת."""
    log = (PCL_LOG.replace("11.80%", "100.00%")
           + "Starting to clone device (/dev/sda4) to image (-)\n"
             "Space in use:   2.0 GB = 500000 Blocks\n")
    body = _build_progress(tmp_path, log)
    assert "source_progress" not in body["targets"][0]


@requires_busybox
def test_a_round_report_never_carries_source_progress(tmp_path):
    """‏`source_progress` הוא ציר הקליטה בלבד. בסבב כיתה/חדר `bytes_total`
    אמיתי, ומכנה-בלוקים היה גובר עליו בטעות (guistate.sh). גם כשקובץ
    לוג נוכח — דיווח סבב אינו נושא אותו."""
    body = _build_progress(tmp_path, PCL_LOG, who="session")
    assert "source_progress" not in body["targets"][0]


# --- #1254: האחוז של partclone יוצא ל-stderr, לא ל-`-L` ----------------------
#
# נמדד על ברזל 10/10 (קליטה tsk_749e מ-HP-BUILD): דף האימג'ים הראה "נקראו
# בייטים · הסך לא ידוע" אחרי 50.8GB — `source_progress` לא הגיע. ‏partclone
# כותב ל-`-L` את שורות הכותרת (‏`Starting to clone`, ‏`Space in use`), ואת
# שורת ההתקדמות (‏`\rElapsed: … Completed: P%`) הוא מדפיס ל-**stderr** — שם
# FOG קורא אותה. הסוכן שלח את ה-stderr ליומן הכללי, ולכן לפרסר לא הגיע
# אחוז. ⚠️ זו מסקנה מהקוד ומהסימפטום, לא מדידה של partclone חי — האימות
# הוא קליטה על ברזל (‏needs:metal).

PCL_HEADER = "\n".join(PCL_LOG.splitlines()[:-1]) + "\n"
#: ה-stderr כמו שהוא: ריפוד ו-`\r` לפני כל עדכון, בלי שורה חדשה ביניהם.
PCL_STDERR = ("Starting to clone device (/dev/sda3) to image (-)\n"
              + "".join(f"\r{' ' * 81}\rElapsed: 00:00:0{i}, Remaining: 00:00:22, "
                        f"Completed: {p:6.2f}%, Rate: 2.10GB/min"
                        for i, p in enumerate((0.0, 5.1, 11.8))))


def _build_progress_with(tmp_path, log, err=None, parts=None):
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "partclone.log").write_text(log, encoding="utf-8")
    if err is not None:
        (run / "targets" / "sda" / "partclone.err").write_bytes(err.encode())
    if parts is not None:
        (run / "parts.txt").write_text(parts, encoding="utf-8", newline="\n")
    out = bb(
        tmp_path,
        f'export RUN_DIR="{run.as_posix()}"\n'
        + source_line("common.sh", "progress.sh")
        + 'target_init sda 0\n'
        'build_progress "" b4:2e:99:07:1a:c4 tsk_4e10\n',
    )
    return json.loads(out)["targets"][0]


@requires_busybox
def test_the_percent_partclone_prints_to_stderr_is_the_one_reported(tmp_path):
    sp = _build_progress_with(tmp_path, PCL_HEADER, PCL_STDERR)["source_progress"]
    assert sp["partition"] == 3
    assert sp["blocks_total"] == 1665979
    assert sp["blocks_read"] == int(0.118 * 1665979)


@requires_busybox
def test_the_partition_is_numbered_k_of_m_from_the_captured_table(tmp_path):
    """‏`parts.txt` היא הטבלה שהקליטה עוברת עליה (`idx|guid|…`). ‏K הוא
    המקום של המחיצה בה, לא האינדקס — טבלה 1,2,5 היא "3 מתוך 3", לא "5 מתוך 3"."""
    parts = "1|g|u|2048|204800\n2|g|u|206848|32768\n3|g|u|239616|104857600\n4|g|u|105097216|1024000\n"
    sp = _build_progress_with(tmp_path, PCL_HEADER, PCL_STDERR, parts)["source_progress"]
    assert (sp["partition_seq"], sp["partitions_total"]) == (3, 4)
    holes = "1|g|u|2048|204800\n2|g|u|206848|32768\n5|g|u|239616|104857600\n"
    sp = _build_progress_with(tmp_path / "b", PCL_HEADER.replace("sda3", "sda5"),
                              PCL_STDERR.replace("sda3", "sda5"), holes)["source_progress"]
    assert (sp["partition"], sp["partition_seq"], sp["partitions_total"]) == (5, 3, 3)


@requires_busybox
def test_without_the_table_the_percent_still_goes_out_but_k_of_m_does_not(tmp_path):
    sp = _build_progress_with(tmp_path, PCL_HEADER, PCL_STDERR)["source_progress"]
    assert "partition_seq" not in sp and "partitions_total" not in sp
    assert sp["blocks_total"] == 1665979


#: ‏partclone מזויף שמתנהג כמו האמיתי בשני הערוצים: כותרת ל-`-L`, אחוז ל-stderr.
PARTCLONE_TWO_CHANNELS = (
    '#!/bin/sh\n'
    'log=""\n'
    'while [ $# -gt 0 ]; do [ "$1" = "-L" ] && { log="$2"; shift; }; shift; done\n'
    "printf 'Starting to clone device (/dev/sda1) to image (-)\\nSpace in use:   0.1 GB = 25600 Blocks\\n' > \"$log\"\n"
    "printf 'Starting to clone device (/dev/sda1) to image (-)\\n' >&2\n"
    "printf '\\rElapsed: 00:00:01, Remaining: 00:00:09, Completed:  10.00%%, Rate: 1.00GB/min' >&2\n"
    "printf '\\rElapsed: 00:00:02, Remaining: 00:00:03, Completed:  42.00%%, Rate: 1.00GB/min' >&2\n"
    'head -c 4096 /dev/zero\n'
)


def test_a_real_capture_reports_the_stderr_percent_and_k_of_m(tmp_path):
    """‏`capture_disk` האמיתי עם partclone דו-ערוצי, ואחריו `build_progress`
    של אותה ריצה: האחוז מה-stderr מגיע לדיווח, עם "מחיצה 1 מתוך 1"."""
    from test_capture_refusals import CURL_SINK, ONE_PARTITION, capture_run
    box, run, out = capture_run(tmp_path, stubs={
        "sgdisk": ONE_PARTITION, "curl": CURL_SINK, "partclone.dd": PARTCLONE_TWO_CHANNELS})
    assert out.strip().endswith("rc=0"), \
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    target = json.loads((box / "progress.json").read_text(encoding="utf-8"))["targets"][0]
    assert target.get("source_progress") == {
        "partition": 1, "blocks_read": int(0.42 * 25600), "blocks_total": 25600,
        "partition_seq": 1, "partitions_total": 1}, target


def test_k_of_m_reaches_the_console_and_a_contradiction_drops_only_it(server):
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    base = {"partition": 3, "blocks_read": 118, "blocks_total": 1000}
    for extra, expected in (
            ({"partition_seq": 3, "partitions_total": 4}, {"partition_seq": 3, "partitions_total": 4}),
            ({"partition_seq": 5, "partitions_total": 4}, {}),
            ({"partition_seq": 0, "partitions_total": 4}, {}),
            ({"partition_seq": "3", "partitions_total": 4}, {}),
            ({"partitions_total": 4}, {})):
        report = _report(task_id, mac, {"dev": "sda", "bytes_written": 1, "bytes_total": 0,
                                        "state": "capturing", "source_progress": {**base, **extra}})
        assert _post(server, report).json()["ok"]
        assert _console_task(server)["source_progress"] == {**base, **expected}, extra
