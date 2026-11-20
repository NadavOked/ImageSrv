"""#2533 (#3108): מגירה שנשלפה באמצע סבב במנוע החתיכות והוחזרה -- ממשיכה וגומרת, **ולא מעכבת אף אחת אחרת**.

ברזל 13/11 21:41, משכפל 2: נדב שלף את מגירה 3 (ata3, naa.5002538d4192eab7) באמצע מעבר והחזיר אותה. הקרנל
גילה אותה לבד, **בשם חדש** (sdd -> sde) ובאותו serial -- והסוכן השאיר אותה `failed`.

ברזל 13/11 22:58, room_714f980d (התיקון הראשון, `lab/vm-1113c`): הזיהוי עבד, אבל ההשלמה רצה **בתוך** המחיצה:
13 דקות המשכפל לא התייצב לשידור של p4, השרת חיכה 180 שניות (lost_wait) וסגר את הסבב, וגם המגירה התקינה, שהייתה
ב-96%, נכשלה ונמחקה. מכאן הדרישה המרכזית כאן: ההשלמה רצה **ברקע**, והסבב ממשיך בלעדיה.

כאן רצה `chunk_partition_drawers` האמיתית, ו-`drawer_back` האמיתית (`agent/lib/restore/drawerback.sh`), מול
`chunkcast`, שרת, sysfs ו-dmesg מזויפים -- הרתמה של test_chunkrecv_2533.py. מה שנבדק הוא מה שהמפעיל והשרת
רואים: המגירה חוזרת לפי ה-serial ולא לפי השם, משלימה ב-HTTP (`chunkcast apply` אחד, על המגירה הזו בלבד) בזמן
שהמעברים של הסבב רצים בלעדיה, נחתמת באותן בדיקות סוף כמו כל מגירה, והדיווח נושא את השם שבו נבחרה.

**בקרה שלילית:** החזרת כל קובצי הייצור של התיקון (`chunkrecv.sh`, `chunkfetch.sh`, `chunkwatch.sh`,
`drawers.sh`, `progress.sh`, `imagesrv-agent`) ומחיקת `drawerback.sh` מפילה כאן את כל הטסטים חוץ מהשומר
`[False]` של הדיווח (בלי הקובץ `dev` -- שם הקרנל, נכון בשני המצבים).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_chunkrecv_2533 import DONE, INDEX, RECEIVE1, _run

REPO = AGENT.parent
# the passes are real seconds, and what is asserted is their order -- never how many seconds: not load-sensitive (#2118)
pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

SLOW_PASS = "REAL_SLEEP=1 TEST_START_CEILING=30"
PULLED_BACK = f"RECV_DIES=sdb:vanish RECV_BACK_AS=sdc RECV_PARTIAL=1 {SLOW_PASS}"
RECEIVE2 = {"ok": True, "action": "receive", "pass": 2}
GONE = "הכונן נעלם מהמערכת (נשלף או נותק)"
ZAP = "sgdisk --zap-all /dev/"                         # the harness's sgdisk only traces: what wipe_written_gpt would run


def _applies(trace: list[str]) -> list[str]:
    return [t for t in trace if t.startswith("chunkcast apply")]


def _recvs_after_the_first(trace: list[str]) -> list[str]:
    return [t for t in trace if t.startswith("chunkcast recv")][1:]


def test_a_drawer_pulled_and_back_under_a_new_name_completes_what_it_lacks_and_is_done(tmp_path):
    """הליבה, כמו על הברזל: sdb נשלפה אחרי שתי חתיכות, וחזרה כ-sdc באותו serial. אחרי התשובה של השרת היא
    נמצאת לפי ה-serial, ה-target עובר לשם החדש וחוזר ל"כותב", החסר לה (חתיכות 2-4) מורד ב-HTTP אליה בלבד,
    והיא נחתמת עם גיבוי סקטור האתחול של NTFS -- כמו המגירה שלא נשלפה."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=PULLED_BACK)
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "PART-RETURNED rc=0" in trace, got
    fail = next(t for t in trace if t.startswith("FAIL sdb"))
    assert GONE in fail                                                     # it failed when it was pulled, by name
    assert trace.index(fail) < trace.index("BACK sdc") < trace.index("SET sdc writing"), trace
    applies = _applies(trace)
    assert len(applies) == 1 and "/dev/sdc3" in applies[0] and "/dev/sda3" not in applies[0], applies
    back = tmp_path / "chunk" / "back.sdc"                                  # its own directory: none of the round's files
    assert (back / "fetch.ranges").read_text().strip() == "2-4"            # what it lacked, not the whole partition
    assert "/dev/sdc3 ok 5 0-4" in (back / "p3.state").read_text().splitlines()
    assert "/dev/sdc3" not in (tmp_path / "chunk" / "p3.state").read_text()
    assert trace.index("SET sdc writing") < trace.index(applies[0]) < trace.index("ntfs_boot sdc 3 ntfs") < trace.index("DONE sdc")
    assert "DONE sda" in trace and not any(t.startswith("sgdisk") for t in trace), trace
    # the positive evidence (principle 5): the lines of the log
    assert "מגירה 3 חזרה (serial SER-sdb, sdc)" in log, log
    assert "chunk fetch: 1 ranges: curl rc=0, apply rc=0" in log and "השלמה: 1 טווחים" in log, log
    assert "המגירה sdc הושלמה עד הסוף ברקע" in log, log
    # the target followed it, and the report keeps the name the round chose it by
    assert not (tmp_path / "targets" / "sdb").exists()
    assert (tmp_path / "targets" / "sdc" / "dev").read_text().strip() == "sdb"
    assert (tmp_path / "targets" / "sdc" / "bound").read_text().strip() == "SER-sdb|1000|3"   # bound again: another slot
    assert not (tmp_path / "targets" / "sdc" / "pulled").exists()          # pulled again later = heard again
    assert not (tmp_path / "targets" / "sdc" / "catchup").exists()         # its completion ended


def test_a_slow_completion_holds_no_other_drawer_and_the_round_goes_on(tmp_path):
    """הברזל של 13/11 22:58, בדיוק: ההשלמה של המגירה שחזרה ארוכה (כאן 15 שניות, על הברזל 13 דקות -- יותר מה-180
    של השרת). היא רצה **ברקע**: מעבר 2 של הסבב מתחיל, רץ ונגמר בזמן שהיא מורידה, בלי המגירה הזו; המחיצה של
    הסבב נגמרת והמגירה התקינה נחתמת לפניה; השרת לא שומע עליה עד שהיא מצטרפת (הוא לא מחכה לה); ובסוף -- לפני
    האימות, כשאין עוד מה לכתוב -- היא נחתמת גם כן."""
    got = _run(tmp_path, [RECEIVE1, RECEIVE2, DONE], env=f"APPLY_SLOW=15 {PULLED_BACK}")
    trace = got["trace"]
    assert trace[-1] == "rc=0", got
    applies = _applies(trace)
    assert len(applies) == 1 and "/dev/sdc3" in applies[0], applies
    second = [t for t in _recvs_after_the_first(trace) if "--pass 2" in t]
    assert second and "/dev/sda3" in second[0] and "/dev/sdc3" not in second[0], trace
    # the round did not wait: its second pass ran while the completion was downloading, and the partition ended before it
    assert trace.index("SET sdc writing") < trace.index(second[0]) < trace.index("APPLY-END"), trace
    assert trace.index("DONE sda") < trace.index("PART-RETURNED rc=0") < trace.index("APPLY-END") < trace.index("DONE sdc"), trace
    # the server is not told of a drawer it would wait for: after it came back, only the healthy one is reported
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    assert {t["serial"]: t["failed"] for t in ended[0]["targets"]} == {"SER-sda": False, "SER-sdb": True}
    assert [t["serial"] for t in ended[-1]["targets"]] == ["SER-sda"], ended[-1]


def test_the_machine_settles_on_the_next_partition_while_a_drawer_completes(tmp_path):
    """הטיימר השני של הברזל (room_714f980d): השרת מחכה שהמשכפל יתחיל להאזין למחיצה **הבאה** (`later_start_timeout`,
    780 שניות), ועד #3112 המשכפל היה תקוע בהשלמה של p3 ולא הגיע לשם. כאן ההשלמה של p3 ארוכה (10 שניות): המשכפל
    אומר לשרת `listening` על p4 לפני שהיא נגמרה, p4 נכתבת על המגירה התקינה בלי המגירה שחזרה, ואחרי כן -- לפני
    האימות -- המגירה שחזרה משלימה גם את p4 ב-HTTP ונחתמת על שתיהן."""
    got = _run(tmp_path, [RECEIVE1, DONE, RECEIVE1, DONE], env=f"SECOND_PART=1 APPLY_SLOW=10 {PULLED_BACK}")
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "PART4-RETURNED rc=0" in trace, got
    assert trace.index("POST listening 4") < trace.index("APPLY-END"), trace   # settled on p4 while it was completing
    p4 = [t for t in trace if t.startswith("chunkcast recv") and "--part 4" in t]
    assert p4 and all("/dev/sda4" in t and "/dev/sdc4" not in t for t in p4), p4
    assert trace.count("DONE sda") == 2 and trace.count("DONE sdc") == 2, trace
    assert "ntfs_boot sdc 4 ntfs" in trace and not any(t.startswith("FAIL sda") for t in trace), trace
    assert not (tmp_path / "targets" / "sdc" / "catchup").exists()


def test_a_completion_that_does_not_end_whole_fails_the_drawer_by_name_and_is_not_tried_again(tmp_path):
    """ההורדה נעצרה באמצע (שלוש חתיכות מתוך חמש): המגירה **אינה** נחתמת על מילה של מישהו. היא נכשלת בשמה,
    עם הסיבה והמספרים, ה-GPT שלה נמחק (#1388, כמו כל מגירה שנכשלה חצי-כתובה), והיא אינה מנוסה שוב בסבב הזה.
    המגירה האחרת אינה מושפעת."""
    got = _run(tmp_path, [RECEIVE1, RECEIVE2, DONE], env=f"APPLY_SHORT=1 {PULLED_BACK}")
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace and "DONE sdc" not in trace, got
    fail = [t for t in trace if t.startswith("FAIL sdc")]
    assert len(fail) == 1 and "המגירה חזרה, וההשלמה שלה נכשלה: מחיצה 3: 3 מתוך 5 חלקים אחרי ההשלמה" in fail[0], fail
    assert f"{ZAP}sdc" in trace and f"{ZAP}sda" not in trace, trace
    assert len(_applies(trace)) == 1, trace                                 # once
    assert all("/dev/sdc3" not in t for t in _recvs_after_the_first(trace)), trace


def test_another_disk_pushed_into_the_slot_does_not_join_and_is_said_once(tmp_path):
    """דיסק **אחר** (serial אחר) שהוכנס לחריץ אינו מצטרף בגרסה הזו -- זה פס הייצור המלא (#3064). המגירה
    שנשלפה נשארת `failed`, הדיסק החדש אינו נכתב, ושורה אחת ביומן אומרת מה קרה, גם אחרי כמה מעברים."""
    got = _run(tmp_path, [RECEIVE1, RECEIVE2, DONE], env=f"RECV_BACK_SERIAL=SER-other {PULLED_BACK}")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace, got
    assert not any(t.startswith(("DONE sdc", "DONE sdb", "SET sdc", "chunkcast apply")) for t in trace), trace
    assert all("/dev/sdc3" not in t for t in trace if t.startswith("chunkcast")), trace
    assert log.count("דיסק אחר הוכנס באמצע הסבב (sdc, serial SER-other)") == 1, log
    assert (tmp_path / "targets" / "sdb" / "state").read_text().strip() == "failed"


# --- drawer_back alone, across partitions (drawers.sh) ------------------------------------------------


PLAN3 = ["1|g|efi|vfat|2048|1|p1.efi.pcl.zst|s|0|u|uuid1|", "2|g|windows|ntfs|4096|1|p2.windows.pcl.zst|s|0|u|uuid2|",
         "3|g|data|ntfs|8192|1|p3.data.pcl.zst|s|0|u|uuid3|"]


def _between(tmp_path, plan: list[str], steps: str, done: int) -> dict:
    """sdb ended `done` partitions, was pulled inside partition 2 (two chunks durable), and is back as sdc."""
    chunk = tmp_path / "chunk"
    chunk.mkdir(parents=True)
    (tmp_path / "restore.plan").write_text("".join(p + "\n" for p in plan), newline="\n")
    (chunk / "p2.idx").write_text(INDEX, encoding="ascii", newline="\n")
    (chunk / "p2.args").write_text(f"--index {posix(chunk / 'p2.idx')} --index-xxh64 abc --raw\n", newline="\n")
    (chunk / "p2.state").write_text("chunkcast-state 1 abc 2 5\n/dev/sda2 ok 5 0-4\n"
                                    "/dev/sdb2 failed 2 0-1 the flush to the disk failed\n", newline="\n")
    return _run(tmp_path, [DONE], env=f"BACK_ONLY=sdc BACK_STEPS='{steps}' BACK_DONE={done} REAL_SLEEP=1")


def test_a_drawer_level_with_the_round_joins_the_next_partitions_stream(tmp_path):
    """בתוך מחיצה 2 (`in:2`) היא חוזרת: ברקע היא משלימה את מחיצה 2 בלבד (את 1 גמרה לפני השליפה, ו-3 עוד לא
    התחילה) -- ולפני מחיצה 3 (`before:3`) היא שווה לסבב, ומצטרפת לזרם של 3 כמגירה רגילה."""
    got = _between(tmp_path, PLAN3, "in:2 before:3", 1)
    trace, log = got["trace"], got["log"]
    assert "back in:2 rc=0 disks=sda sdc" in trace and "back before:3 rc=1 disks=sda sdc" in trace, got
    applies = _applies(trace)
    assert len(applies) == 1 and "--part 2" in applies[0] and "/dev/sdc2" in applies[0], applies
    assert [t for t in trace if t.startswith(("ntfs_boot", "DONE"))] == ["ntfs_boot sdc 2 ntfs", "DONE sdc"], trace
    assert "המגירה sdc הושלמה עד מחיצה 3 -- מצטרפת לזרם שלה" in log, log
    assert not (tmp_path / "targets" / "sdc" / "catchup").exists()         # a drawer of the round again


def test_a_drawer_behind_the_round_waits_for_the_partition_it_missed_and_stays_out_of_its_stream(tmp_path):
    """היא חזרה רק לפני מחיצה 3 (`before:3`): הסבב מתחיל את 3 בלעדיה. ההשלמה שלה גומרת את 2 ועוצרת לפני 3 --
    אין לה עוד את הקבצים של 3 -- והיא נשארת מחוץ לזרם של 3 (`catchup`) עד שהסבב יכתוב אותם."""
    got = _between(tmp_path, PLAN3, "before:3", 1)
    trace = got["trace"]
    assert "back before:3 rc=0 disks=sda sdc" in trace, got
    assert [t for t in trace if t.startswith(("ntfs_boot", "DONE"))] == ["ntfs_boot sdc 2 ntfs", "DONE sdc"], trace
    assert (tmp_path / "targets" / "sdc" / "catchup").exists()             # behind: not in the round's stream of 3


def test_a_drawer_back_before_the_read_back_completes_every_partition_it_lacks_swap_too(tmp_path):
    """לפני האימות (`all`): כל מה שחסר לה עד הסוף -- מחיצה 2 ב-HTTP, וה-swap (שאינה משודרת) נבראת עליה --
    והסבב מחכה לה כאן, ורק כאן."""
    plan = ["1|g|efi|vfat|2048|1|p1.efi.pcl.zst|s|0|u|uuid1|", "2|g|windows|ntfs|4096|1|p2.windows.pcl.zst|s|0|u|uuid2|",
            "5|g|swap|swap|8192|1||||u|uuid5|"]
    got = _between(tmp_path, plan, "all:", 1)
    trace = got["trace"]
    assert trace[-1] == "back all: rc=0 disks=sda sdc", trace
    assert "mkswap /dev/sdc5 uuid5" in trace and trace.count("DONE sdc") == 2, trace
    assert trace.index("DONE sdc") < trace.index("back all: rc=0 disks=sda sdc")   # waited for, before the read-back
    assert "המגירה sdc הושלמה עד הסוף ברקע" in got["log"]


# --- the report: `dev` is the name the round chose the drawer by ---------------------------------------


PROGRESS = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log"
''' + agent_sources("common", "jsonq", "sysinfo", "diskinfo", "progress", "chunkledger", "failmark") + r'''   # #2272: imagesrv-agent's order
for _d in sda sdc; do mkdir -p "$RUN_DIR/targets/$_d"; echo writing > "$RUN_DIR/targets/$_d/state"; echo 100 > "$RUN_DIR/targets/$_d/total"; : > "$RUN_DIR/targets/$_d/bytes.0"; done
[ -z "${ALIAS:-}" ] || echo sdb > "$RUN_DIR/targets/sdc/dev"
build_progress ses_1 aa:bb:cc:00:00:21 > "$RUN_DIR/progress.json"
'''


@pytest.mark.parametrize("alias", [True, False])
def test_the_report_names_a_drawer_back_under_a_new_name_by_the_name_it_was_chosen_by(tmp_path, alias):
    """השרת מזהה מגירה לפי ה-`dev` שבדיווח מול המלאי של ה-hello (`slot_names`, `stamp_done_serials`), ושם היא
    `sdb`: מגירה שחזרה כ-sdc ממשיכה להיקרא כך, והקונסולה רואה את **אותה** מגירה חוזרת ל"כותב". בלי הקובץ -- השם
    שלה בקרנל, כמו קודם."""
    (tmp_path / "progress.sh").write_text(PROGRESS, encoding="utf-8", newline="\n")
    env = "ALIAS=1 " if alias else ""
    proc = subprocess.run([BASH, "-c", f'{env}. {posix(tmp_path / "progress.sh")!r} {posix(tmp_path)!r}'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    report = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert sorted(t["dev"] for t in report["targets"]) == (["sda", "sdb"] if alias else ["sda", "sdc"]), proc.stderr
    assert all(t["state"] == "writing" for t in report["targets"])


def test_the_agent_sources_drawerback():
    """הקובץ נארז ב-initramfs עם כל `agent/lib`, וה-agent טוען אותו: בלעדיו הקריאות ב-chunkrecv.sh וב-drawers.sh
    נכשלות כ-"not found", והמגירה שחזרה נשארת בחוץ בשקט."""
    agent = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/drawerback.sh"' in agent


# --- the end of the round, under the name it is back by (metal 14/11, room_1838cf62) ------------------
#
# Metal 14/11 00:13-00:34, lab/vm-1113d: drawer 3 (sdb) was pulled inside p3, pushed back in as sdd, and completed
# in the background; the server closed the round with written_drives=2. The agent's own end said
#   drawers complete (1 of 2) -- a disk has a problem ...: sdb|r|מצב הדיסק לא נקרא — לא ידוע אם נ...
#   sdd: crc delta not measured (no baseline)
# -- the end's list (`_all`, imagesrv-agent) and the SMART reading before the write ($RUN_DIR/smart/<d>.*) were
# still under the old name: an orphan "sdb" held the results on the screen and the power-off never came.

END_STUBS = r'''
identify_watch_stop() { :; }; smart_selftest_cancel() { :; }; session_stream() { return 0; }; chunk_gate() { return 0; }
resolve_target_drawers() { _disks="sda sdb"; }; job_reset() { :; }; target_init() { :; }; session_open_gate() { :; }
progress_loop() { :; }; drawers_bound() { echo "$@"; }; smart_gate() { shift; echo "$@"; }; drawer_gates() { echo "$@"; }
smart_note_apply() { :; }; name_drawers() { :; }; stage_drawer_drivers() { :; }; writemark_release_done() { return 0; }
report_final() { trace "REPORT"; }; agent_log_send() { :; }; round_beep() { :; }; round_beep_result() { trace "BEEPS $*"; }
finish_and_stop() { trace "POWEROFF"; }; task_done_stays_on() { trace "STAYED ON"; }
attended_power_check() { _held=$((${_held:-0} + 1)); [ "$_held" -lt 3 ] || : > "$RUN_DIR/end.now"; }   # held: "כבה", after 3 s
# the SMART reading of the gate, before the write: sdb (drawer 3) had CRC 59,165, sda 667 -- and the pull added none
SMARTCTL=fake_smartctl
fake_smartctl() { case "$*" in *sda) _n=667 ;; *) _n=59165 ;; esac; echo "199 UDMA_CRC_Error_Count 0x003e 100 100 000 Old_age Always - $_n"; }
mkdir -p "$RUN_DIR/smart"; for _d in sda:667 sdb:59165; do echo "ok ok 0 0 0 ${_d#*:}" > "$RUN_DIR/smart/${_d%%:*}.v"; echo "SER-${_d%%:*}" > "$RUN_DIR/smart/${_d%%:*}.serial"; done
cp "$RUN_DIR/manifest.json" "$RUN_DIR/manifest.keep"; echo '{}' > "$RUN_DIR/resp.json"; RESP="$RUN_DIR/resp.json"
run_restore_drawers() {   # drawers.sh's, for p3 alone: the partition, the wait for a completion, then the read-back's verdict
    shift 4; _disks="$*"; cp "$RUN_DIR/manifest.keep" "$RUN_DIR/manifest.json"
    # shellcheck disable=SC2086
    chunk_partition_drawers http://srv img_1 3 ntfs p3.windows.pcl.zst "$RUN_DIR/manifest.json" $_disks; echo "PART-RETURNED rc=$?" >> "$RUN_DIR/trace"
    # shellcheck disable=SC2086
    drawer_back all "" http://srv img_1 $_disks
    for _d in $_disks; do   # readback_drawers + finish_grow: sampled, and done
        [ "$(cat "$RUN_DIR/targets/$_d/state")" = writing ] || continue
        echo sampled > "$RUN_DIR/targets/$_d/readback"; target_set "$_d" done
    done
}
END_POWEROFF_S=1
do_restore_drawers img_1 ses_1; echo "rc=$?" >> "$RUN_DIR/trace"
'''


def _end_of_round(tmp_path, monkeypatch, env: str) -> dict:
    """The chunk harness's drawers, with imagesrv-agent's own `do_restore_drawers` around them -- its end included."""
    import test_chunkrecv_2533 as harness
    from test_final_report import cut_function

    setup, sep, _ = harness.HARNESS.partition('\n_disks="sda sdb"\n')
    assert sep, "the harness's action part moved: this test cuts it off"
    tail = agent_sources("smart", "crcdelta", "idle") + "\n" + cut_function("do_restore_drawers") + "\n" + END_STUBS
    monkeypatch.setattr(harness, "HARNESS", setup + "\n" + tail)
    return _run(tmp_path, [RECEIVE1, DONE], env=env)


def test_a_drawer_back_under_a_new_name_ends_the_round_under_it_counted_measured_and_powered_off(tmp_path, monkeypatch):
    """הברזל של 14/11, בדיוק: מגירה 3 (sdb) נשלפה ב-p3, חזרה כ-sdd, הושלמה ברקע ונחתמה. בסוף הסבב היא נספרת
    בשמה החדש -- "drawers complete (2 of 2)", בלי בעיה בשם הישן, והמכונה נכבית מעצמה -- והפרש ה-CRC שלה נמדד
    מול הקריאה של לפני הכתיבה, שעברה איתה לשם החדש (59,165 -> 59,165: השליפה לא הוסיפה)."""
    got = _end_of_round(tmp_path, monkeypatch, f"RECV_DIES=sdb:vanish RECV_BACK_AS=sdd RECV_PARTIAL=1 {SLOW_PASS}")
    trace, log = got["trace"], got["log"]
    assert "DONE sdd" in trace and "rc=0" in trace, got
    assert "drawers complete (2 of 2) -- powering off in 1s" in log, log
    assert "a disk has a problem" not in log and "sdb|" not in log, log         # no orphan under the old name
    assert trace[-2:] == ["POWEROFF", "rc=0"], trace                             # off by itself, not held for 'כבה'
    assert "BEEPS sda sdd" in trace, trace                                       # the result tune counts it too
    run = tmp_path
    assert (run / "targets" / "sdd" / "crc_delta").read_text().strip() == "0"
    assert (run / "targets" / "sda" / "crc_delta").read_text().strip() == "0"
    assert "crc delta not measured" not in log, log
    assert not (run / "targets" / "sdb").exists() and not list((run / "smart").glob("sdb.*")), \
        sorted(p.name for p in (run / "smart").iterdir())


def test_a_drawer_that_never_left_ends_the_round_as_before(tmp_path, monkeypatch):
    """השומר: בלי שליפה -- שתי המגירות בשמן, נספרות, נמדדות, והמכונה נכבית. נכון בשני המצבים."""
    got = _end_of_round(tmp_path, monkeypatch, SLOW_PASS)
    trace, log = got["trace"], got["log"]
    assert "drawers complete (2 of 2) -- powering off in 1s" in log, log
    assert trace[-2:] == ["POWEROFF", "rc=0"] and "BEEPS sda sdb" in trace, trace
    assert (tmp_path / "targets" / "sdb" / "crc_delta").read_text().strip() == "0"


CANCEL = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log"
''' + agent_sources("common", "jsonq", "cancel") + r'''
# sdb was pulled and is back as sdd; then sdc was pulled and is back as sdb -- the name sdb was free by then
for _t in sda: sdd:sdb sdb:sdc; do
    mkdir -p "$RUN_DIR/targets/${_t%%:*}"; echo writing > "$RUN_DIR/targets/${_t%%:*}/state"
    [ -z "${_t#*:}" ] || echo "${_t#*:}" > "$RUN_DIR/targets/${_t%%:*}/dev"
done
echo '{"ok":true,"cancel":[{"dev":"sdb","who_he":"על ידי nadav"},{"dev":"sda","who_he":"x"}]}' > "$RUN_DIR/progress.reply"
cancel_from_reply
'''


def test_the_console_cancels_a_drawer_back_under_a_new_name_by_the_name_it_knows(tmp_path):
    """"בטל דיסק זה" (#1386) מהקונסולה מגיע בשם שהשרת מכיר (`dev` של הדיווח: sdb), והמגירה כבר ב-sdd. הבקשה
    מגיעה אל sdd -- ולא אל דיסק אחר שחזר בינתיים בשם sdb (שם היא הייתה מבטלת את הדיסק הלא נכון). sda, שלא זזה,
    מבוטלת בשמה (השומר)."""
    (tmp_path / "cancel.sh").write_text(CANCEL, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, "-c", f'. {posix(tmp_path / "cancel.sh")!r} {posix(tmp_path)!r}'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    targets = tmp_path / "targets"
    assert (targets / "sdd" / "cancel").read_text(encoding="utf-8").strip() == "על ידי nadav", proc.stderr
    assert not (targets / "sdb" / "cancel").exists(), "the disk that came back as sdb was not asked"
    assert (targets / "sda" / "cancel").read_text(encoding="utf-8").strip() == "x"
