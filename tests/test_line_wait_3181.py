"""#3181 (כותב E, צד הסוכן): בפס ייצור, משכפל שכל הדיסקים שלו סיימו **ממתין לדיסק הבא** כל עוד נשארו שמות.

עד כאן (`prodline.sh`, כותב D) סוף הסבב במכונה הלך אחרי הדיסק האחרון שנכתב: הדיווח הסופי, צפצוף, ספירה וכיבוי --
גם כשבתור של השרת עוד היו שמות, והדיסק הבא פגש מכונה כבויה. `agent/lib/restore/linewait.sh` האמיתי רץ כאן:

1. **לבדו** (`_wait`): מה שמכריע אם המכונה ממתינה. השאלה יוצאת בדיווח (`line_wait`), וההכרעה היא רק לפי **תשובה
   לדיווח שנשא אותה** (עיקרון 5): `queue_left` ו-`insert`. אין תקרה להמתנה; יש תקרה לשאלה שאיש לא ענה עליה.
2. **בדיווח האמיתי** (`build_progress`): `line_wait` ברמת הגוף, ומי שהוא כל יעד כשהשם שלו כבר של דיסק אחר.
3. **מקצה לקצה** (`_e2e`), עם הרתמה של test_chunkrecv_2533.py והזיופים של test_prodline_newdisk_3181.py: שתי מגירות
   וארבעה שמות. המגירות נכתבות ומנותקות, המפעיל מוציא אותן, **המכונה נשארת בגל**, שני דיסקים חדשים נכנסים (הראשון
   לחריץ 2, והקרנל נותן לו את השם הפנוי הנמוך -- `sda`, השם של הדיסק הכתוב שהיה בחריץ 1), נכתבים בשמות ששמורים
   לחריצים שלהם, ו**רק אז** הסיום. השרת מזויף ב-jq, לפי החוזה של `prodline.reply` (שנבדק מול ה-API ב-
   test_prodline_wait_3181.py); הדיווח נבנה מ-`drawer_back_json` ו-`line_wait_json` האמיתיים.

**בקרה שלילית:** החזרת `prodline.sh`, `drawerplug.sh`, `neighborfill.sh`, `datareport.sh` ו-`imagesrv-agent` לבסיס
ומחיקת `linewait.sh` מפילה כאן הכול: בלי ההמתנה המכונה מסיימת כשהמגירות מנותקות, ושום דיסק חדש אינו נכתב.
"""

from __future__ import annotations

import json

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_chunkrecv_2533 import DONE, RECEIVE1, _run
from test_prodline_newdisk_3181 import EXTRA, _out

pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

# --- 1. linewait.sh by itself: the question, its answer, and what ends the wait ------------------------------------------

WAIT = r'''
export RUN_DIR={run} LOG_FILE={run}/agent.log; CHUNK_DIR={run}/chunk; mkdir -p "$CHUNK_DIR"; : > "$RUN_DIR/splash.on"
. {agent}/lib/core/common.sh
[ ! -f {agent}/lib/restore/linewait.sh ] || . {agent}/lib/restore/linewait.sh   # not there = the tree before the wait
round_closed() {{ [ -e "$RUN_DIR/closed" ]; }}
date() {{ cat "$RUN_DIR/clock"; }}; clock() {{ echo "$1" > "$RUN_DIR/clock"; }}; clock 1000   # the agent's only clock here is `date +%s`
tick() {{ line_wait; echo "rc=$? state=$(cat "$RUN_DIR/state" 2> /dev/null) asks=$([ -s "$RUN_DIR/linewait" ] && echo yes || echo no)"; }}
report() {{ printf '{{"targets":[]%s}}\n' "$(line_wait_json)" > "$RUN_DIR/progress.json"; : > "$RUN_DIR/progress.reply"; }}   # a body goes out
answer() {{ printf '%s\n' "$1" > "$RUN_DIR/progress.reply"; }}                                                              # and the server's answer to it
line() {{ answer "{{\"ok\": true, \"production_line\": $1}}"; }}
{body}
'''


def _wait(tmp_path, body: str) -> dict:
    run = tmp_path / "run"
    run.mkdir(parents=True)
    out = sh(WAIT.format(run=posix(run), agent=posix(AGENT), body=body))
    return {"ticks": out.strip().split("\n"), "run": run,
            "log": (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""}


def test_a_machine_with_names_left_in_the_queue_waits_and_nothing_but_the_server_ends_it(tmp_path):
    """הליבה: בתור נשארו שני שמות. המכונה שואלת, נשארת `writing`, וממתינה -- גם אחרי יממה, וגם כשהשרת השתתק."""
    got = _wait(tmp_path, '''tick; report; tick
        line '{"queue_left": 2, "insert": {}, "next_name": "LAB-03"}'; tick
        clock 87400; tick; : > "$RUN_DIR/progress.reply"; tick; rm -f "$RUN_DIR/progress.reply"; tick''')
    assert got["ticks"] == ["rc=0 state=writing asks=yes"] * 6, got
    assert "ולסבב נשארו שמות (2 בתור, 0 שמורים למגירות כאן)" in got["log"], got["log"]
    assert "ממתינה לדיסק. השם הבא: LAB-03" in got["log"]
    assert got["log"].count("ממתינה לדיסק") == 1, "said once, not every tick"
    assert "מסיימת את הסבב" not in got["log"]


def test_a_name_kept_for_one_of_its_own_emptied_slots_is_enough_to_wait(tmp_path):
    got = _wait(tmp_path, '''tick; report; tick; line '{"queue_left": 0, "insert": {"2": "LAB-04"}, "next_name": null}'; tick''')
    assert got["ticks"][-1] == "rc=0 state=writing asks=yes", got
    assert "(0 בתור, 1 שמורים למגירות כאן)" in got["log"] and "השם הבא: LAB-04" in got["log"], got["log"]


def test_the_wait_ends_when_the_server_answers_that_nothing_is_left(tmp_path):
    """ורק אז: תשובה מ**לפני** השאלה ("לא נשאר דבר") אינה התשובה. המכונה מסיימת רק אחרי שדיווח שנשא את השאלה נענה."""
    nothing = '''line '{"queue_left": 0, "insert": {}, "next_name": null}' '''
    got = _wait(tmp_path, f'''{nothing}; tick; tick; tick      # an old answer is lying there: no report carried the question yet
        report; {nothing}; tick                                # the body is seen at this tick; its answer is read at the next
        tick; tick''')
    assert got["ticks"][:4] == ["rc=0 state=writing asks=yes"] * 4, got
    assert got["ticks"][4] == "rc=1 state=writing asks=no", got       # the mark is gone: the report stops saying it waits
    assert "התור ריק, ואין שם שמחכה למגירה במכונה הזו -- המכונה מסיימת את הסבב" in got["log"], got["log"]
    # a machine that waited ends the same way, once the queue is empty (another cloner took the last name)
    late = _wait(tmp_path / "late", f'''tick; report; tick; line '{{"queue_left": 1, "insert": {{}}}}'; tick; {nothing}; tick''')
    assert late["ticks"][-2:] == ["rc=0 state=writing asks=yes", "rc=1 state=writing asks=no"], late


def test_a_round_the_operator_closed_ends_the_wait_at_once(tmp_path):
    got = _wait(tmp_path, '''tick; report; tick; line '{"queue_left": 3, "insert": {}}'; tick; : > "$RUN_DIR/closed"; tick''')
    assert got["ticks"][-2:] == ["rc=0 state=writing asks=yes", "rc=1 state=writing asks=no"], got
    assert "השרת סגר את הסבב -- המכונה מסיימת את הסבב" in got["log"], got["log"]


def test_a_question_nobody_answers_is_not_asked_for_ever_but_a_wait_the_server_began_is_kept(tmp_path):
    """שרת שלא ענה לשאלה הראשונה תוך 60 שניות: המכונה מסיימת כמו לפני, והסיבה ביומן. שרת שהשתתק **אחרי** שאמר
    לחכות אינו מסיים את ההמתנה (המבחן הראשון). ותשובה בלי `queue_left` היא שרת שאינו מדבר על המתנה."""
    got = _wait(tmp_path, '''tick; report; tick; clock 1060; tick; clock 1061; tick''')
    assert got["ticks"] == ["rc=0 state=writing asks=yes"] * 3 + ["rc=1 state=writing asks=no"], got
    assert "השרת לא ענה תוך 60 שניות אם נשארו שמות בתור -- המכונה מסיימת את הסבב" in got["log"], got["log"]
    old = _wait(tmp_path / "old", '''tick; report; tick; line '{"written": 2, "target": 4, "insert": {}}'; tick''')
    assert old["ticks"][-1] == "rc=1 state=writing asks=no", old
    assert "השרת אינו אומר כמה שמות נשארו בתור (queue_left)" in old["log"], old["log"]
    # and an answer that is not the server's whole word is not an answer: not ok, or with no production_line at all
    for n, reply in enumerate(['{"ok": false, "code": "bad_report"}', '{"ok": true}', '{"ok": true, "production_line"']):
        deaf = _wait(tmp_path / f"deaf{n}", f"tick; report; tick; answer '{reply}'; tick; tick")
        assert deaf["ticks"][-1] == "rc=0 state=writing asks=yes", (reply, deaf)


def test_a_machine_whose_end_was_already_said_does_not_wait(tmp_path):
    """`failed`, `partial` ו-`done` סופיים בשרת: הגל כבר אינו מחכה למכונה, והיא אינה נשארת `writing` לשווא."""
    got = _wait(tmp_path, '''echo failed > "$RUN_DIR/state"; tick; line '{"queue_left": 5, "insert": {}}'; report; tick''')
    assert got["ticks"] == ["rc=1 state=failed asks=no"] * 2, got
    assert "המכונה כבר דיווחה failed -- היא אינה ממתינה לדיסק" in got["log"], got["log"]


def test_the_report_says_how_long_and_stops_when_a_disk_is_being_written(tmp_path):
    got = _wait(tmp_path, '''echo "json=[$(line_wait_json)]"; tick; clock 1012; echo "json=[$(line_wait_json)]"
        clock 990; echo "json=[$(line_wait_json)]"; line_wait_off; echo "json=[$(line_wait_json)]"''')
    assert got["ticks"] == ["json=[]", "rc=0 state=writing asks=yes", 'json=[,"line_wait":{"s":12}]',
                            'json=[,"line_wait":{"s":0}]', "json=[]"], got       # a clock set back is not a negative wait


# --- 2. the real report: the question at the body's level, and who a target is once its name is another disk's ------------

REPORT = r'''
export RUN_DIR={run} SYSROOT={run}/sysroot LOG_FILE={run}/agent.log; : > "$RUN_DIR/splash.on"
. {agent}/lib/core/common.sh; {sources}
[ ! -f {agent}/lib/restore/linewait.sh ] || . {agent}/lib/restore/linewait.sh
mkdir -p "$RUN_DIR/targets/sda"; echo "done" > "$RUN_DIR/targets/sda/state"; echo 1000 > "$RUN_DIR/targets/sda/total"; : > "$RUN_DIR/targets/sda/bytes.0"
echo writing > "$RUN_DIR/state"
build_progress ses_1 aa:bb:cc:00:00:21; echo
date +%s > "$RUN_DIR/linewait"; build_progress ses_1 aa:bb:cc:00:00:21; echo
build_progress "" aa:bb:cc:00:00:21 tsk_1
'''


def test_the_real_report_carries_the_question_at_the_bodys_level_and_only_in_a_round(tmp_path):
    run = tmp_path / "run"
    (run / "sysroot").mkdir(parents=True)
    out = sh(REPORT.format(run=posix(run), agent=posix(AGENT),
                           sources=agent_sources("sysinfo", "diskinfo", "progress", "failmark", "datareport")))
    plain, asking, task = (json.loads(line) for line in out.strip().split("\n"))
    assert "line_wait" not in plain, plain
    assert isinstance(asking.get("line_wait"), dict), asking
    assert isinstance(asking["line_wait"]["s"], int) and 0 <= asking["line_wait"]["s"] < 60, asking
    assert asking["state"] == "writing" and asking["targets"][0]["dev"] == "sda", asking
    assert "line_wait" not in task, "a task's report (a capture) is not a round's"


def test_a_detached_disk_is_still_itself_when_another_disk_answers_under_its_name(tmp_path):
    """אחרי ניתוק בטוח ההתקן יוצא מהקרנל, והשם הפנוי הנמוך הוא של הדיסק הבא שייכנס -- בכל חריץ. עד כאן היעד הכתוב
    דיווח אז את ה-serial והחריץ של הדיסק **החדש**: השרת קשר לדיסק ריק שורה "נכתב", והסוכן חיכה לנצח לתשובה על
    `removed` של ה-serial הישן. היעד הוא מה שנקשר בבחירה; מה שעונה בשם שלו עכשיו הוא שלו רק כשזה אותו דיסק."""
    honest = 'target_same() { [ -e "$SYSROOT/sys/block/$1" ] && [ "$(disk_serial "$1")" = "$(target_bound_serial "$1")" ]; }'
    serials = 'for _d in sda sdb sdc sdd; do echo "SER-$_d" > "$SYSROOT/sys/block/$_d/serial"; done'
    pushed = 'mkdir -p "$SYSROOT/sys/block/sda"; echo NEW-9 > "$SYSROOT/sys/block/sda/serial"; echo 4 > "$SYSROOT/sys/block/sda/port"'
    got = _out(tmp_path, f"{honest}; {serials}; drawer_out_detach sda; {pushed}; build_progress ses_1 aa:bb:cc:00:00:21")
    by = {t["dev"]: t for t in json.loads(got["out"])["targets"]}
    assert "delete sda" in got["calls"] and by["sda"].get("detached") is True, got
    assert (by["sda"].get("serial"), by["sda"].get("port")) == ("SER-sda", 1), by["sda"]     # not NEW-9, not slot 4
    assert (by["sdb"].get("serial"), by["sdb"].get("port")) == ("SER-sdb", 2), by["sdb"]     # a disk that is still itself: read now


# --- 3. end to end: two drawers, four names ---------------------------------------------------------------------------------

LINE_E2E = r'''
# the kernel's clock, for a detach's stamp (drawerout.sh); and who a target is, as failmark.sh's target_same says it -- the
# harness's own says "always the same", which is what hides a disk answering under another one's name
mkdir -p "$SYSROOT/proc"; echo "100.00 90.00" > "$SYSROOT/proc/uptime"
target_same() { [ -e "$SYSROOT/sys/block/$1" ] && [ "$(disk_serial "$1")" = "$(target_bound_serial "$1")" ]; }
# what run_restore_drawers and do_restore_drawers do between the last partition and prodline_finish: the read-back, the grow
eval "_real_$(declare -f prodline_finish)"
prodline_finish() {
    for _e in $_disks; do readback_drawers m "$_e"; finish_grow "$_e"; done
    echo verifying > "$RUN_DIR/state"; _real_prodline_finish "$@"
}
_server() {   # the body on stdin -> the server's answer, by prodline.reply's contract: two names in the queue (LAB-03, LAB-04); a slot
    # whose written disk was pulled keeps the next one; a new disk takes the name kept for its slot. Stateless: a round's own disk that
    # is no longer reported was pulled, and the server heard (the agent retires it only then)
    jq -c 'def q: {"1": "LAB-03", "2": "LAB-04"};
        [.targets[] | select(.serial != null)] as $t
        | [{"s": "SER-sda", "p": "1"}, {"s": "SER-sdb", "p": "2"}
           | select(.s as $s | [$t[] | select(.serial == $s and .removed != true)] | length == 0) | .p] as $emptied
        | [$t[] | select(.serial | startswith("NEW-"))] as $new
        | {ok: true, production_line: {target: 4, written: ([$t[] | select(.state == "done")] | length),
             next_name: ([q[]] - [$emptied[] | q[.]] | .[0]), queue_left: (2 - ($emptied | length)),
             names: ({"SER-sda": "LAB-01", "SER-sdb": "LAB-02"} + ([$new[] | {key: .serial, value: q[.port | tostring]}] | from_entries)),
             insert: ([$emptied[] | select(. as $p | [$new[] | select((.port | tostring) == $p)] | length == 0) | {key: ., value: q[.]}] | from_entries),
             removable: [], refused: []}}'
}
_reporter2() {   # progress.sh's loop: every target as the REAL report says it (drawer_back_json: serial, port, detached, removed), the
    # machine's question (line_wait_json), then the answer -- emptied the moment the body goes out, as progress.sh empties it
    while [ ! -e "$RUN_DIR/ticker.stop" ]; do
        { printf '{"targets":['; _rs=""
          for _rt in "$RUN_DIR"/targets/*/; do
              [ -f "$_rt/state" ] || continue; _rh=${_rt%/}; _rh=${_rh##*/}
              printf '%s{"dev":"%s","state":"%s"%s}' "$_rs" "$_rh" "$(cat "$_rt/state" 2> /dev/null)" "$(drawer_back_json "$_rh" 2> /dev/null)"; _rs=","
          done; printf ']%s}\n' "$(line_wait_json 2> /dev/null)"; } > "$RUN_DIR/progress.json.new"
        cat "$RUN_DIR/progress.json.new" >> "$RUN_DIR/said.jsonl"; mv "$RUN_DIR/progress.json.new" "$RUN_DIR/progress.json"
        : > "$RUN_DIR/progress.reply"   # empty for as long as the answer takes, and no longer: a pause here would hide the answer from a loop that ticks in step with it
        if _server < "$RUN_DIR/progress.json" > "$RUN_DIR/reply.new" 2>> "$RUN_DIR/server.err"; then cat "$RUN_DIR/reply.new" > "$RUN_DIR/progress.reply"; fi
        command sleep 0.5
    done
}
_push() {   # $1 = the name the kernel gives it, $2 = its serial, $3 = the slot it is pushed into. No uevent: the waiting machine looks by itself
    mkdir -p "$SYSROOT/sys/block/$1/device"; echo "$2" > "$SYSROOT/sys/block/$1/device/serial"
    echo 1000 > "$SYSROOT/sys/block/$1/size"; echo "$3" > "$SYSROOT/sys/block/$1/port"; : > "$SYSROOT/sys/block/$1/answers"; trace "PUSH $1 $2 slot $3"
}
_when() { until eval "$1"; do [ ! -e "$RUN_DIR/ticker.stop" ] || exit 0; command sleep 0.2; done; }
_operator() {   # the person at the cloner: pulls what is detached, and some seconds later pushes two new disks in
    _when '[ -e "$RUN_DIR/targets/sda/detached" ] && [ -e "$RUN_DIR/targets/sdb/detached" ]'
    trace "OPERATOR pulls the two written disks"
    printf '%s\n' '[  150.000000] ata1: SATA link down (SStatus 0 SControl 300)' '[  150.100000] ata2: SATA link down (SStatus 0 SControl 300)' >> "$RUN_DIR/kmsg"
    echo "200.00 190.00" > "$SYSROOT/proc/uptime"   # a later detach is stamped after these lines: they are not ITS pull
    _when '[ -e "$RUN_DIR/linewait.on" ] && [ -e "$RUN_DIR/targets/sda/removed" ] && [ -e "$RUN_DIR/targets/sdb/removed" ]'
    command sleep 4   # nobody pushes a disk for a while: the machine is still there
    [ ! -e "$RUN_DIR/final" ] || trace "ENDED-WITH-NAMES-LEFT"
    # the kernel hands out the lowest free name: the first disk pushed -- into slot 2 -- is sda, the name the written disk of slot 1 had
    _push sda NEW-1 2; _push sdb NEW-2 1
}
_reporter2 > /dev/null 2>&1 &
( _operator ) > /dev/null 2>&1 &
'''
E2E_EXTRA = EXTRA + LINE_E2E


def test_line_e2e_harness_can_load_its_reporter(tmp_path):
    extra = tmp_path / "extra.sh"
    extra.write_text(E2E_EXTRA, encoding="utf-8", newline="\n")
    sh(f"bash -n {posix(extra)!r}")


def _e2e(tmp_path) -> dict:
    assert E2E_EXTRA.count("_reporter > /dev/null") == 0 and "_reporter2 > /dev/null" in E2E_EXTRA   # one reporter: this file's
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra.sh").write_text(E2E_EXTRA, encoding="utf-8", newline="\n")
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"REAL_SLEEP=1 TEST_START_CEILING=30 HARNESS_EXTRA={posix(tmp_path / 'extra.sh')!r}")
    got["final"] = (tmp_path / "final").read_text().strip() if (tmp_path / "final").exists() else ""
    said = (tmp_path / "said.jsonl").read_text(encoding="utf-8").splitlines() if (tmp_path / "said.jsonl").exists() else []
    got["said"] = [json.loads(line) for line in said if line.strip()]
    got["server_err"] = (tmp_path / "server.err").read_text() if (tmp_path / "server.err").exists() else ""
    return got


def test_two_drawers_and_four_names_the_cloner_waits_takes_two_new_disks_and_only_then_ends(tmp_path):
    """מקצה לקצה. הסדר הוא מה שנבדק, לא השניות: המגירות מנותקות -> המכונה ממתינה -> המפעיל מוציא -> שני דיסקים
    חדשים נכנסים ונכתבים, כל אחד בשם של החריץ שלו -> ורק אז הסיום."""
    got = _e2e(tmp_path)
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and got["server_err"] == "", got
    # 1. the round's two drawers were written and detached, and the machine did not end: it stayed in the wave
    assert "המכונה נשארת בגל וממתינה לדיסק" in log, "the machine ended with names left in the queue:\n" + log
    assert log.index("sdb (serial SER-sdb, חריץ 2): נותק בבטחה") < log.index("המכונה נשארת בגל וממתינה לדיסק"), log
    assert "ENDED-WITH-NAMES-LEFT" not in trace and "PUSH sda NEW-1 slot 2" in trace, trace
    asked = [b for b in got["said"] if "line_wait" in b]
    assert asked and all(isinstance(b["line_wait"]["s"], int) and b["line_wait"]["s"] >= 0 for b in asked), got["said"][-3:]
    # 2. the two new disks became targets, each got the name kept for ITS slot, and each was read back and detached
    pushed = trace.index("PUSH sdb NEW-2 slot 1")
    assert trace.index("INIT sda") > pushed and trace.index("INIT sdb") > pushed, trace
    for dev, serial, name in (("sda", "NEW-1", "LAB-04"), ("sdb", "NEW-2", "LAB-03")):
        target = tmp_path / "targets" / dev
        assert (target / "bound").read_text().startswith(serial + "|"), f"{dev} is not the new disk's target"
        end = [f"apply_gpt {dev}", f"readback {dev}", f"grow {dev}", f"HOSTNAME {dev} {name}", f"mark_off {dev}"]
        at = [len(trace) - 1 - trace[::-1].index(step) for step in end]          # the new disk's steps: the last of each
        assert at == sorted(at) and at[0] > pushed, (dev, at, trace)
        assert (target / "state").read_text().strip() == "done" and (target / "detached").exists(), dev
        assert trace.count(f"DELETE {dev}") == 2, trace                         # the round's own disk, then the new one under its name
        assert f"השרת קיבל את הדיסק החדש {dev} (serial {serial}) לסבב -- שם המחשב: {name}" in log, log
    assert "אינו מצטרף" not in log, log                                          # neither was refused for the name the kernel gave it
    # 3. and only then the end: the queue is empty, and nothing else ended the wait
    assert log.count("המכונה מסיימת את הסבב") == 1, log
    ended = log.index("התור ריק, ואין שם שמחכה למגירה במכונה הזו -- המכונה מסיימת את הסבב")
    assert ended > log.rindex("נותק בבטחה -- אפשר להוציא"), log
    assert got["final"].startswith("finish=0 disks="), got["final"]
    assert sorted(got["final"].split("disks=")[1].split(" all=")[0].split()) == ["sda", "sdb"], got["final"]   # the round's list: the two new disks
