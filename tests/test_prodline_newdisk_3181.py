"""#3181 (כותב D, צד הסוכן): בפס ייצור, דיסק **חדש** שהוכנס באמצע סבב הוא יעד חדש; דיסק שנכתב ואומת מנותק בבטחה;
והסוכן קורא את מה שהמוח אומר על השלמה (`fill`).

עד כאן (`drawerback.sh`, #3064) דיסק עם serial אחר שהוכנס לחריץ היה שורה ביומן. `prodline.sh`, `drawerout.sh`
ו-`fillword.sh` האמיתיים רצים כאן מול הרתמה של test_chunkrecv_2533.py; מה שמזויף הוא הצד של הקרנל ושל שאר הסבב
(`target_init`, שער ה-SMART, בדיקת הכתיבה, הטבלה, הקריאה החוזרת, ההרחבה) והמדווח: כל 0.7 שניות גוף עם ה-serial של כל
יעד, ואחריו תשובת השרת -- שמתרוקנת קודם, כמו ש-`progress.sh` מרוקן אותה ברגע שגוף יוצא.

מה שנבדק הוא מה שהמפעיל והשרת רואים:

1. דיסק חדש בפס ייצור: יעד משלו, שער SMART, המילה של השרת, טבלה, הצטרפות לשידור שרץ, השלמה של מה שהחמיץ.
2. דיסק אדום, ודיסק שהשרת סירב לו (`refused`), אינם נכתבים -- ואינם נכנסים לרשימה שמעבר מקבל.
3. דיסק חדש בחריץ (ובשם) של מגירה שנשלפה: היעד הישן יוצא מהדיווח, והחדש אינו יורש את החתיכות שלו. הוא מסיים
   אחרי שאר המגירות, בסיום משלו: קריאה חוזרת, השם שהשרת נתן ל-**serial** שלו, וניתוק בטוח.
4. `detached` רק אחרי ריקון מטמון **ו**יציאה מהקרנל, רק ליעד שאומת, ורק בפס ייצור; `removed` לפי שורת הקרנל על
   הפורט, **אחרי** הניתוק; `serial` ו-`port` בכל יעד בדיווח.
5. `fill`: HTTP = לא קוראים משכנות; שכנות = רק אלה שנקבו, והתקציב נכתב כתקרה; בלי תוכנית = כמו קודם.

**בקרה שלילית:** החזרת `drawerback.sh`, `drawerplug.sh`, `drawerfill.sh`, `drawernames.sh` ו-`imagesrv-agent` לבסיס
ומחיקת `prodline.sh`, `drawerout.sh` ו-`fillword.sh` מפילה כאן הכול חוץ משלושה שומרים, שעוברים בשני המצבים:
`test_in_a_regular_round_another_disk_is_still_only_said`, `test_in_a_regular_round_nothing_is_detached`
ו-`test_without_a_plan_the_neighbours_are_read_as_before` (בלי תוכנית של המוח ההתנהגות היא של הבסיס).
"""

from __future__ import annotations

import json

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_chunkrecv_2533 import DONE, RECEIVE1, _run
from test_drawer_rejoin_3181 import PULLED

# the passes are real seconds, and what is asserted is their order -- never how many seconds: not load-sensitive (#2118)
pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

EXTRA = r'''
# what a new disk meets of the kernel and of the rest of the round, faked (progress.sh, smartgate.sh, writecheck.sh,
# expand.sh, readback.sh, grow.sh, hostname.sh)
for _d in sda sdb; do _p=$(cat "$SYSROOT/sys/block/$_d/port"); echo "SER-$_d|$_p|$_p" > "$RUN_DIR/targets/$_d/seen"; done
target_init() {
    mkdir -p "$RUN_DIR/targets/$1"; echo waiting > "$RUN_DIR/targets/$1/state"; echo 0 > "$RUN_DIR/targets/$1/counter.index"
    echo "$(disk_serial "$1")|1000|$(disk_port "$1")" > "$RUN_DIR/targets/$1/bound"
    echo "$(disk_serial "$1")|$(disk_port "$1")|$(disk_port "$1")" > "$RUN_DIR/targets/$1/seen"
    disk_serial "$1" > "$RUN_DIR/targets/$1/write_started"; trace "INIT $1"
}
target_unique() { return 0; }
smart_gate() { trace "smart_gate $2"; [ "$2" != "${SMART_RED:-}" ] || { target_set "$2" skipped; return 1; }; echo "$2"; }
write_check_round() { trace "write_check $2"; target_set "$2" writing; }
discard_before_restore() { :; }; stage_at() { :; }; expand_last() { :; }
apply_gpt() { trace "apply_gpt $1"; }
readback_drawers() { trace "readback $2"; echo sampled > "$RUN_DIR/targets/$2/readback"; target_set "$2" verifying; }
finish_grow() { trace "grow $1"; target_set "$1" "done"; }
crc_delta_measure() { :; }; smart_note_apply() { :; }; stage_drawer_drivers() { :; }
writemark_release_done() { trace "mark_off $1"; }
write_hostname() { trace "HOSTNAME $1 $3"; echo '{}'; }
_drawer_out_delete() { trace "DELETE $1"; rm -rf "$SYSROOT/sys/block/$1"; }   # the kernel drops the node
. agent/lib/postdeploy/drawernames.sh
RESP="$RUN_DIR/response.json"
echo '{"session": {"hostnames": {"1": "LAB-01", "2": "LAB-02", "3": "LAB-03"}, "hostname_os": null}}' > "$RESP"
[ -z "${NF_TRACE:-}" ] || neighborfill_run() {
    trace "neighborfill $1 only=${NF_ONLY:-} cap=$(cat "$RUN_DIR/neighborfill.cap" 2> /dev/null)"; : > "$2"; return 0
}
_back() {   # BACK_PORT: the slot the disk is pushed into (default 3, an empty one)
    mkdir -p "$SYSROOT/sys/block/$2/device"; echo "$3" > "$SYSROOT/sys/block/$2/device/serial"
    echo 1000 > "$SYSROOT/sys/block/$2/size"; echo "${BACK_PORT:-3}" > "$SYSROOT/sys/block/$2/port"; : > "$SYSROOT/sys/block/$2/answers"
    trace "BACK $2"; [ -z "${PLUG_EVENT:-}" ] || : > "$RUN_DIR/chunk/plug"
    [ -z "${NF_TRACE:-}" ] || _await_file "chunk/back.$2/p3.state"
}
if [ "${BACK_PORT:-}" = 2 ]; then
    harness_answer() {
        [ "$(jq -r .state "$1")" = ended ] || return 1
        if [ ! -e "$RUN_DIR/back.inserted" ]; then
            _back sdb sdb SER-other; : > "$RUN_DIR/back.inserted"
        fi
        if [ -f "$RUN_DIR/targets/sdb/newdisk" ] && [ "$(cat "$RUN_DIR/targets/sdb/state")" = writing ]; then
            cat "$RUN_DIR/answer.last.json"
        elif [ "$(cat "$RUN_DIR/posts")" -ge 66 ]; then
            trace "SYNC-TIMEOUT targets/sdb/state=writing"
            cat "$RUN_DIR/answer.last.json"
        else
            printf '%s\n' '{"ok":true,"action":"wait","whole":true}'
        fi
    }
    harness_apply_wait() { _await_file targets/sdb/late; }
fi
_report_body() {
    { printf '{"targets":['; _rs=""
      for _rt in "$RUN_DIR"/targets/*/; do
          [ -f "$_rt/state" ] || continue; _rh=${_rt%/}; _rh=${_rh##*/}; _rb=$(cat "$_rt/bound" 2> /dev/null); _rb=${_rb%|*|*}
          printf '%s{"dev":"%s","serial":"%s","state":"%s"' "$_rs" "$_rh" "$_rb" "$(cat "$_rt/state")"
          [ ! -e "$_rt/removed" ] || printf ',"removed":true'
          [ ! -s "$_rt/rejoin" ] || printf ',"rejoin":%s' "$(cat "$_rt/rejoin")"
          printf '}'; _rs=","
      done; printf ']%s}\n' "$(line_wait_json 2> /dev/null)"; } > "$RUN_DIR/progress.json.new" && mv "$RUN_DIR/progress.json.new" "$RUN_DIR/progress.json"   # line_wait: linewait.sh's (not there = nothing)
}
_report_reply() {
    [ ! -s "$RUN_DIR/reply.json" ] || cat "$RUN_DIR/reply.json" > "$RUN_DIR/progress.reply"
}
_reporter() {   # progress.sh's loop: a body with every target, then the server's answer -- emptied the moment the body goes out
    while [ ! -e "$RUN_DIR/ticker.stop" ]; do
        _report_body
        : > "$RUN_DIR/progress.reply"; command sleep 0.3
        _report_reply
        command sleep 0.4
    done
}
'''

REPORTER = r'''
if [ -n "${NF_TRACE:-}" ]; then
    sleep() {
        if [ "${FUNCNAME[1]:-}" = fill_word ]; then
            # Drive the fake reporter on the agent's polling tick, after rejoin has been written.
            _report_body && : > "$RUN_DIR/progress.reply" && _report_reply
        else
            [ -z "${REAL_SLEEP:-}" ] || command sleep "$@"
        fi
    }
else
    _reporter > /dev/null 2>&1 &
fi
'''

LINE = {"written": 1, "target": 5, "remaining": 4, "next_name": "LAB-11", "names": {"SER-other": "LAB-10"},
        "insert": {}, "removable": [], "refused": []}
# sdb is pulled four seconds into the pass; ANOTHER disk is pushed in four seconds later as sdc, into an empty slot, and
# the pass goes on for fourteen more. Listening for itself it gets chunks 3-4: it never had 0-1, and 2 went by before it wrote
NEW = f"{PULLED} RECV_BACK_AS=sdc RECV_BACK_SERIAL=SER-other PLUG_EVENT=1 RECV_TAIL=14 SIDE_STATE='2 3-4'"


def _line(tmp_path, answers, env, reply: dict | None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "extra.sh").write_text(EXTRA + REPORTER, encoding="utf-8", newline="\n")
    if reply is not None:
        (tmp_path / "reply.json").write_text(json.dumps(reply))
    got = _run(tmp_path, answers, env=f"{env} HARNESS_EXTRA={posix(tmp_path / 'extra.sh')!r}")
    assert not any(t.startswith("SYNC-TIMEOUT ") for t in got["trace"]), got
    got["final"] = (tmp_path / "final").read_text().strip() if (tmp_path / "final").exists() else ""
    return got


def _state(tmp_path, dev: str) -> str:
    return (tmp_path / "targets" / dev / "state").read_text().strip()


def test_a_new_disk_in_a_production_line_becomes_a_target_and_takes_the_pass_on_the_air(tmp_path):
    """הליבה: דיסק אחר (serial אחר) הוכנס לחריץ ריק באמצע המעבר. הוא עובר את שער ה-SMART, השרת מאשר אותו, נכתבת לו
    טבלה, מקלט משלו מאזין למעבר שרץ, ומה שהחמיץ -- שלוש חתיכות מתוך חמש -- מורד ב-HTTP."""
    got = _line(tmp_path, [RECEIVE1, DONE], NEW, {"ok": True, "production_line": LINE})
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0", got
    order = ["BACK sdc", "INIT sdc", "smart_gate sdc", "write_check sdc", "SET sdc writing", "apply_gpt sdc"]
    assert [trace.index(step) for step in order] == sorted(trace.index(step) for step in order), trace
    side = [t for t in trace if t.startswith("chunkcast recv") and "/back." in t]
    assert len(side) == 1 and "/dev/sdc3" in side[0] and "/dev/sda3" not in side[0], side
    assert trace.index("apply_gpt sdc") < trace.index(side[0]), trace          # no chunk before its table
    assert (tmp_path / "chunk" / "back.sdc" / "fetch.ranges").read_text().strip() == "0-2"
    assert "DONE sdc" in trace and "DONE sda" in trace, trace
    assert "דיסק חדש הוכנס באמצע הסבב (sdc, serial SER-other, חריץ 3)" in log, log
    assert "השרת קיבל את הדיסק החדש sdc (serial SER-other) לסבב -- שם המחשב: LAB-10" in log, log
    assert "מדידה: למגירה sdc חסרות 3 מתוך 5 חתיכות במחיצה 3" in log, log
    assert "אינו מצטרף לסבב הזה (#3064)" not in log, log
    assert "disks=sda sdb sdc " in got["final"], got["final"]                  # the round's own: its end is the round's end
    assert _state(tmp_path, "sdb") == "failed"                                 # the drawer that was pulled is another slot's


def test_in_a_regular_round_another_disk_is_still_only_said(tmp_path):
    """השומר: בלי `production_line` מהשרת זה סבב רגיל. הדיסק נאמר ביומן, פעם אחת, ואינו נכתב."""
    got = _line(tmp_path, [RECEIVE1, DONE], NEW, {"ok": True})
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace, got
    assert log.count("דיסק אחר הוכנס באמצע הסבב (sdc, serial SER-other)") == 1, log
    assert not any(t.startswith(("INIT sdc", "apply_gpt", "SET sdc", "DELETE")) for t in trace), trace
    assert not (tmp_path / "targets" / "sdc").exists()


@pytest.mark.parametrize("env, reply, said", [
    ("SMART_RED=sdc", LINE, "sdc: הדיסק החדש לא עבר את שער ה-SMART -- אינו נכתב"),
    ("", {**LINE, "names": {}, "refused": ["SER-other"]}, "sdc: הדיסק החדש אינו נכתב -- הסבב כבר קיבל את כל הדיסקים שביקשו"),
])
def test_a_red_disk_and_a_disk_the_server_refused_are_not_written_and_never_reach_a_pass(tmp_path, env, reply, said):
    got = _line(tmp_path, [RECEIVE1, {"ok": True, "action": "receive", "pass": 2}, DONE], f"{NEW} {env}",
                {"ok": True, "production_line": reply})
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "INIT sdc" in trace and "smart_gate sdc" in trace, got
    assert said in got["log"], got["log"]
    assert _state(tmp_path, "sdc") == "skipped"
    assert not any(t.startswith(("write_check sdc", "apply_gpt sdc", "DONE sdc", "DELETE")) for t in trace), trace
    assert not any("/dev/sdc" in t for t in trace if t.startswith("chunkcast")), trace   # the second pass too
    assert "disks=sda sdb " in got["final"] and "all=sdc" in got["final"], got["final"]  # reported and counted, never listed


def test_a_new_disk_in_the_slot_and_under_the_name_of_a_pulled_drawer_holds_none_of_its_chunks_and_ends_by_itself(tmp_path):
    """sdb (חריץ 2) נשלפה אחרי שתי חתיכות עמידות, והמעבר נגמר. דיסק **אחר** הוכנס לאותו חריץ, והקרנל נתן לו את אותו
    שם. היעד הישן יוצא מהדיווח; החדש מתחיל מאפס -- השורה של `/dev/sdb3` בקובץ המצב של הסבב היא של הדיסק הקודם -- ומוריד
    את כל חמש החתיכות. שאר המגירות אינן מחכות לו: הוא מסיים לבד, בשם שהשרת נתן ל-serial שלו, ומנותק."""
    got = _line(tmp_path, [RECEIVE1, DONE],
                f"{PULLED} PLUG_EVENT=1 BACK_PORT=2",
                {"ok": True, "production_line": LINE})
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0", got
    assert "המגירה sdb (serial SER-sdb, failed) יצאה מהמכונה" in log, log
    assert (tmp_path / "targets" / "sdb" / "bound").read_text().startswith("SER-other|"), "the target is the new disk's"
    assert (tmp_path / "chunk" / "back.sdb" / "fetch.ranges").read_text().strip() == "0-4"   # not 2-4: 0-1 were the old disk's
    end = ["apply_gpt sdb", "DONE sdb", "readback sdb", "grow sdb", "HOSTNAME sdb LAB-10", "mark_off sdb", "DELETE sdb"]
    assert [trace.index(step) for step in end] == sorted(trace.index(step) for step in end), trace
    timing = ["POST ended 3", "BACK sdb", "PART-RETURNED rc=0", "APPLY-END"]
    assert [trace.index(step) for step in timing] == sorted(trace.index(step) for step in timing), trace
    assert "HOSTNAME sdb LAB-02" not in trace, trace                           # the slot's name was the old disk's
    assert got["final"].startswith("finish=0 disks=sda sdb "), got["final"]
    assert (tmp_path / "targets" / "sdb" / "detached").exists() and _state(tmp_path, "sdb") == "done"
    assert "פס ייצור: הדיסקים שהצטרפו באמצע" in log or "המכונה נשארת בגל" in log, log


# --- the brain's word on a fill (fillword.sh), where the neighbours are read (drawerfill.sh) ----------------------------

BACK = f"{PULLED} RECV_BACK_AS=sdc PLUG_EVENT=1 RECV_TAIL=0 SIDE_STATE='4 0-1,3-4' TEST_FILL_WORD_S=6"


def _plan(source: str, neighbors: list[str]) -> dict:
    return {"ok": True, "fill": {"SER-sdb": {"verdict": "FEW", "source": source, "neighbors": neighbors,
                                             "per_neighbor_bps": 52428800, "budget_bps": 52428800}}}


def test_the_brain_says_http_and_no_neighbour_is_read(tmp_path):
    got = _line(tmp_path, [RECEIVE1, DONE], f"{BACK} NF_TRACE=1", _plan("HTTP", []))
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sdc" in trace, got
    assert not any(t.startswith("neighborfill") for t in trace), trace
    assert (tmp_path / "chunk" / "back.sdc" / "fetch.ranges").read_text().strip() == "2-2"
    assert "השלמה ב-HTTP, לא קוראים משכנות" in got["log"], got["log"]


def test_the_brain_names_the_neighbours_and_its_budget_is_the_ceiling(tmp_path):
    got = _line(tmp_path, [RECEIVE1, DONE], f"{BACK} NF_TRACE=1", _plan("NEIGHBOR", ["sda"]))
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sdc" in trace, got
    assert "neighborfill sdc only=sda cap=50" in trace, trace                  # 52428800 bytes a second = 50MiB/s


def test_without_a_plan_the_neighbours_are_read_as_before(tmp_path):
    got = _line(tmp_path, [RECEIVE1, DONE], f"{BACK} NF_TRACE=1", {"ok": True})
    assert "neighborfill sdc only= cap=" in got["trace"], got["trace"]
    assert not (tmp_path / "neighborfill.cap").exists()


# --- detached, removed, and serial and port on every target (drawerout.sh, drawerplug.sh) -------------------------------

OUT = r'''
export RUN_DIR={run} SYSROOT={run}/sysroot LOG_FILE={run}/agent.log DEVROOT={run}/dev; CHUNK_DIR={run}/chunk; CHUNK_SESSION=ses_1; RESP={run}/response.json
. {agent}/lib/core/common.sh; {sources}
. {agent}/lib/restore/verdict.sh
for _f in prodline drawerout; do [ ! -f {agent}/lib/restore/$_f.sh ] || . {agent}/lib/restore/$_f.sh; done   # not there = the tree before #3181's production line
disk_serial() {{ cat "$SYSROOT/sys/block/$1/serial" 2>/dev/null; }}; disk_port() {{ cat "$SYSROOT/sys/block/$1/port" 2>/dev/null; }}
target_same() {{ [ -e "$SYSROOT/sys/block/$1" ]; }}; sleep() {{ :; }}; DRAWER_OUT_GONE_S=1
blockdev() {{ echo "flush $2" >> "$RUN_DIR/calls"; [ "$2" != "$DEVROOT/sdc" ]; }}
_drawer_out_delete() {{ echo "delete $1" >> "$RUN_DIR/calls"; [ "$1" = sdd ] || rm -rf "$SYSROOT/sys/block/$1"; }}
dmesg() {{ cat "$RUN_DIR/kmsg" 2>/dev/null; }}
{body}
'''


def _out(tmp_path, body: str, line: bool = True) -> dict:
    run = tmp_path / "run"
    (run / "chunk").mkdir(parents=True)
    (run / "response.json").write_text(json.dumps({"session": {"production_line": LINE if line else None}}))
    (run / "sysroot" / "proc").mkdir(parents=True)
    (run / "sysroot" / "proc" / "uptime").write_text("100.00 90.00\n")
    (run / "state").write_text("writing")
    (run / "splash.on").write_text("")                                          # log() writes to the file alone: stdout is the report
    for n, (dev, readback) in enumerate([("sda", "sampled"), ("sdb", "unverified"), ("sdc", "verified"), ("sdd", "verified")], 1):
        t = run / "targets" / dev
        t.mkdir(parents=True)
        (t / "state").write_text("done")
        (t / "readback").write_text(readback)
        (t / "bytes.0").write_text("0")
        (t / "total").write_text("1000")
        (t / "seen").write_text(f"SER-{dev}|{n}|{n}\n")
        (t / "bound").write_text(f"SER-{dev}|1000|{n}\n")
        (t / "since").write_text("10.00\n")
        (run / "sysroot" / "sys" / "block" / dev).mkdir(parents=True)
    out = sh(OUT.format(run=posix(run), agent=posix(AGENT), body=body,
                        sources=agent_sources("sysinfo", "diskinfo", "progress", "failmark", "drawerplug")))
    calls = (run / "calls").read_text().split("\n") if (run / "calls").exists() else []
    return {"out": out, "run": run, "calls": calls, "log": (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""}


def test_only_a_verified_disk_that_was_flushed_and_left_the_kernel_is_detached(tmp_path):
    got = _out(tmp_path, "drawer_out_detach sda sdb sdc sdd; build_progress ses_1 aa:bb:cc:00:00:21")
    by = {t["dev"]: t for t in json.loads(got["out"])["targets"]}
    assert by["sda"]["state"] == "done" and by["sda"].get("detached") is True, by["sda"]
    assert "delete sda" in got["calls"] and got["calls"].index(f"flush {posix(got['run'])}/dev/sda") < got["calls"].index("delete sda")
    assert "detached" not in by["sdb"] and "delete sdb" not in got["calls"], by["sdb"]     # done, and not read back
    assert "detached" not in by["sdc"] and "delete sdc" not in got["calls"], by["sdc"]     # its flush failed
    assert "detached" not in by["sdd"] and "delete sdd" in got["calls"], by["sdd"]         # deleted, and its node stayed
    assert "sdb: נכתב, ולא אומת בקריאה חוזרת" in got["log"] and "sdd: הניתוק הבטוח נכשל" in got["log"], got["log"]
    assert all("removed" not in t for t in by.values()), by
    # every target says who it is: a production line knows a disk by nothing else
    assert [(by[d].get("serial"), by[d].get("port")) for d in ("sda", "sdb", "sdc", "sdd")]         == [(f"SER-sd{c}", n) for n, c in enumerate("abcd", 1)], by


def test_in_a_regular_round_nothing_is_detached(tmp_path):
    got = _out(tmp_path, "drawer_out_detach sda sdb sdc sdd; build_progress ses_1 aa:bb:cc:00:00:21", line=False)
    assert got["calls"] == [], got["calls"]
    assert all("detached" not in t for t in json.loads(got["out"])["targets"])


def test_a_detached_disk_is_removed_by_its_ports_own_word_after_the_detach(tmp_path):
    """שורת `link down` של הפורט **לפני** הניתוק היא של הכתיבה (או של דיסק קודם) ואינה שליפה. אחריו -- כן."""
    before = "printf '%s\\n' '[   50.000000] ata1: SATA link down (SStatus 0 SControl 300)' > \"$RUN_DIR/kmsg\""
    after = "printf '%s\\n' '[  150.000000] ata1: SATA link down (SStatus 0 SControl 300)' >> \"$RUN_DIR/kmsg\""
    report = "build_progress ses_1 aa:bb:cc:00:00:21"
    got = _out(tmp_path, f"drawer_out_detach sda; {before}; drawer_out_tick; {report}; echo; {after}; drawer_out_tick; {report}")
    first, second = ({t["dev"]: t for t in json.loads(line)["targets"]} for line in got["out"].strip().split("\n"))
    assert first["sda"].get("detached") is True and "removed" not in first["sda"], first["sda"]
    assert second["sda"].get("detached") is True and second["sda"].get("removed") is True, second["sda"]
    assert second["sda"]["state"] == "done" and "removed" not in second["sdb"], second     # still reported, and only it
