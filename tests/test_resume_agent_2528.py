"""#2528 שלב 4, צד הסוכן (`agent/lib/restore/resume.sh`): משכפל שכבה באמצע סבב וחזר ממשיך ממה שכבר על המגירות.

ברזל 14/11 (משכפל 2): המשכפל כבה ב-9,172 מתוך 18,254 חתיכות של מחיצת Windows, ו"כתוב מחדש" כתב הכול מההתחלה --
35 דקות. כאן רצים `resume_round`, `resume_seed` ו-`run_restore_drawers` האמיתיים, ו-`fanout`, השרת, הדיסקים והטבלה
מזויפים. מה שנבדק הוא מה שקורה לדיסק:

1. רק מגירה שנושאת את סימן הכתיבה שלא הושלמה (#1798) נשאלת, עם קצב קריאה **שנמדד** בה וזמן הקריאה החוזרת.
2. רק "resume" עם "enforced" ממשיך. צל, תשובה אחרת, או שרת שלא ענה = נכתבת מההתחלה כמו היום.
3. מגירה שממשיכה אינה עוברת TRIM ואינה נכתבת בבדיקת הכתיבה -- שתיהן מוחקות מה שעליה. הטבלה נכתבת שוב.
4. לכל מחיצה: רק חתיכה שה-XXH64 שלה **כפי שנקרא מהדיסק** הוא של האינדקס נכנסת לקובץ המצב של `chunkcast`, ול-ledger
   שיוצא לשרת. ה-ledger של השרת אינו ראיה.
5. עיקרון 5: קריאה חוזרת שנכשלה, או שנגמרה לפני הסוף = המחיצה נכתבת מההתחלה, לעולם לא "הכול כתוב".

**בקרה שלילית:** החזרת `drawers.sh`, `chunkrecv.sh` ו-`imagesrv-agent` לבסיס ומחיקת `resume.sh` מפילה את כל הטסטים כאן
(אין שאלה לשרת, אין קובץ מצב, ו-TRIM ובדיקת הכתיבה רצים על המגירה) -- חוץ מ-
`test_a_drawer_without_the_mark_is_not_asked_about`, שהוא שומר: הוא עובר גם על הבסיס.
"""

from __future__ import annotations

import json
import subprocess

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
pytestmark = requires_native(("bash", BASH), "jq", why="התשובה של השרת נקראת ב-jq, כמו בסוכן")

# five chunks; the XXH64 of chunk k is k as 16 hex digits (the shape of test_chunkrecv_2533.py's index)
INDEX = ("IMAGESRV-IDX 2 raw 4194304 0 0 - 20971520 5 1000\n"
         + "".join(f"{k * 200} 200 {k:016x}\n" for k in range(5)))
# what the disk gives back: 0, 1 and 3 are the image's; 2 was cut mid-write, 4 never arrived
READBACK = "".join(f"c {k} {k:016x}\n" for k in (0, 1)) + f"c 2 {'e' * 16}\n" + f"c 3 {3:016x}\n" \
    + f"c 4 {'0' * 16}\n" + "n 5 20971520\n"
MANIFEST = {"partitions": [{"index": 3, "index_xxh64": "abc", "readback_chunks": 5}]}

HARNESS = r'''
export RUN_DIR="$1"; LOG_FILE="$1/agent.log"; MAC=aa:bb:cc:00:00:21; SERVER=http://srv; CHUNK_SESSION=ses_2; IFACE=""
. agent/lib/core/common.sh; LOG_FILE="$1/agent.log"; . agent/lib/core/jsonq.sh
. agent/lib/restore/chunkledger.sh; . agent/lib/restore/chunkrecv.sh; ''' + agent_sources("drawers", "writeback") + r'''   # #2272: drawers the agent's way, with what it starts (writeback_watch)
# not there = the tree before #2528 stage 4 (the negative control): nothing below is defined, and the tests fail on what happens
[ ! -f agent/lib/restore/resume.sh ] || . agent/lib/restore/resume.sh
trace() { echo "$*" >> "$RUN_DIR/trace"; }
export SYSROOT="$RUN_DIR/sysroot"; DEVROOT="$RUN_DIR/dev"; mkdir -p "$SYSROOT/proc" "$DEVROOT"; echo "100.00 1.00" > "$SYSROOT/proc/uptime"
writemark_state() { case " ${MARKED:-} " in *" $1 "*) echo incomplete ;; *) echo clean ;; esac; }
disk_serial() { echo "SER-$1"; }
blockdev() { echo 256060514304; }
# 64MiB read in half a second of the machine's clock: 134 MB/s
dd() { trace "dd ${*##*/}"; echo "100.50 1.00" > "$SYSROOT/proc/uptime"; printf 'x'; }
fanout() {
    case "$1" in
        --xxh64) cat > /dev/null; echo 0123456789abcdef ;;
        --readback) trace "readback $*"; cat > /dev/null
                    [ -z "${RB_FAIL:-}" ] || return 4
                    [ -z "${RB_SLOW:-}" ] || command sleep "$RB_SLOW"   # a read-back that takes this long (seconds, real time)
                    if [ -n "${RB_SHORT:-}" ]; then grep -v '^n ' "$RUN_DIR/readback"; else cat "$RUN_DIR/readback"; fi ;;
    esac
}
http_post_json() { cp "$2" "$RUN_DIR/asked.json"; trace "POST ${1#http://srv}"; [ -z "${NO_ANSWER:-}" ] || return 22; cat "$RUN_DIR/answer.json"; }
target_set() { mkdir -p "$RUN_DIR/targets/$1"; echo "$2" > "$RUN_DIR/targets/$1/state"; }
disk_fits() { trace "fits $1"; }; plan_gate() { trace "plan $1"; }
partition_node() { echo "/dev/$1$2"; }
sleep() { command sleep 0.05; }
for d in sda sdb; do mkdir -p "$RUN_DIR/targets/$d"; echo writing > "$RUN_DIR/targets/$d/state"; done
case "$2" in
ceiling)   # #3670: the read-back's ceiling for sda and $3 bytes, by the numbers of the server's answer
    cp "$RUN_DIR/answer.json" "$RUN_DIR/resume.answer.json"; trace "ceiling=$(_rs_ceiling sda "$3")" ;;
round)
    resume_round chunk "$SERVER" img_1 "$RUN_DIR/manifest.json" sda sdb; trace "rc=$?"
    trace "REST=$(echo $RESUME_REST)"
    for d in sda sdb; do [ ! -e "$RUN_DIR/targets/$d/resume" ] || trace "HELD $d"; done ;;
seed)
    : > "$RUN_DIR/targets/sda/resume"; cp "$RUN_DIR/answer.json" "$RUN_DIR/resume.answer.json"
    CHUNK_DIR="$RUN_DIR/chunk"; mkdir -p "$CHUNK_DIR"; CHUNK_IDX="$CHUNK_DIR/p3.idx"; CHUNK_STATE="$CHUNK_DIR/p3.state"
    cp "$RUN_DIR/idx" "$CHUNK_IDX"
    resume_seed 3 abc sda sdb; trace "rc=$?" ;;
drawers)   # run_restore_drawers up to its partition plan: the tables, and what came before them
    round_closed_fail() { return 1; }; stage_at() { :; }; _any_alive() { return 0; }; _fail_alive() { trace "fail_alive $2"; }
    write_check_round() { shift; trace "write_check $*"; }
    discard_before_restore() { trace "discard $1"; }
    apply_gpt() { trace "table $1"; }; expand_last() { trace "expand $1"; }
    fail_written_target() { trace "FAIL $1 $2"; }
    wait_pid() { while kill -0 "$1" 2>/dev/null; do command sleep 0.05; done; }
    manifest_plan() { return 1; }   # the partitions are not this test's: it stops after the tables
    run_restore_drawers chunk "$SERVER" img_1 "$RUN_DIR/manifest.json" sda sdb; trace "rc=$?" ;;
esac
'''


def _run(tmp_path, what: str, answer: dict | None = None, env: str = "") -> dict:
    (tmp_path / "idx").write_text(INDEX, encoding="ascii", newline="\n")
    (tmp_path / "readback").write_text(READBACK, encoding="ascii", newline="\n")
    (tmp_path / "manifest.json").write_text(json.dumps(MANIFEST))
    (tmp_path / "answer.json").write_text(json.dumps(answer or {"ok": True, "targets": []}))
    (tmp_path / "harness.sh").write_text(HARNESS, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, "-c", f'export PATH="/usr/bin:$PATH"; {env} . {posix(tmp_path / "harness.sh")!r}'
                                       f' {posix(tmp_path)!r} {what}'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL,
                          timeout=120)
    trace = (tmp_path / "trace").read_text(encoding="utf-8").splitlines() if (tmp_path / "trace").exists() else []
    asked = tmp_path / "asked.json"
    return {"trace": trace, "out": proc.stdout + proc.stderr,
            "asked": json.loads(asked.read_text()) if asked.exists() else None,
            "log": (tmp_path / "agent.log").read_text(encoding="utf-8") if (tmp_path / "agent.log").exists() else ""}


def _answer(verdict="resume", enforced=True, serial="SER-sda"):
    return {"ok": True, "targets": [{"serial": serial, "verdict": verdict, "enforced": enforced,
                                     "reason": "להמשיך: חסרות 2 מתוך 5", "ledger": {"3": 4},
                                     "readback": {"min_mbps": 10, "slack_s": 60}}]}   # the brain's ceiling (#3670)


# --- 1-2. השאלה לשרת ---------------------------------------------------------------------------------------------


def test_a_marked_drawer_is_asked_about_with_its_measured_read_rate_and_resumes_only_when_enforced(tmp_path):
    got = _run(tmp_path, "round", _answer(), env="MARKED=sda")
    trace = got["trace"]
    assert "POST /api/v1/agent/resume" in trace, got
    target = got["asked"]["targets"]
    assert [t["serial"] for t in target] == ["SER-sda"], "only the drawer that carries the mark is asked about"
    assert got["asked"]["session_id"] == "ses_2"
    assert target[0]["read_mbps"] == 134 and target[0]["read_bytes"] == 5 * 4194304
    assert "readback_s" not in target[0], "how long the read-back takes is the brain's to compute (#3670), not the agent's"
    assert "HELD sda" in trace and "HELD sdb" not in trace
    assert "REST=sdb" in trace, "the drawer that resumes is not test-written"
    assert trace.index("fits sda") < trace.index("HELD sda") and "plan sda" in trace
    assert "ממשיכה ממה שכבר עליה" in got["log"]


def test_shadow_writes_from_the_start_and_says_what_the_brain_would_have_done(tmp_path):
    got = _run(tmp_path, "round", _answer(enforced=False), env="MARKED=sda")
    assert "HELD sda" not in got["trace"] and "REST=sda sdb" in got["trace"], got
    assert "המוח במצב צל היה ממשיך" in got["log"]


def test_a_server_that_does_not_answer_means_every_drawer_from_the_start(tmp_path):
    got = _run(tmp_path, "round", _answer(), env="MARKED=sda NO_ANSWER=1")
    assert "POST /api/v1/agent/resume" in got["trace"], got
    assert "HELD sda" not in got["trace"] and "REST=sda sdb" in got["trace"]
    assert "השרת לא ענה על ההמשך" in got["log"]


def test_a_drawer_without_the_mark_is_not_asked_about(tmp_path):
    """שומר: דיסק שאין עליו את הסימן -- לא נקטעה עליו כתיבה שלנו -- אינו נשאל ואינו ממשיך (עובר גם על הבסיס)."""
    got = _run(tmp_path, "round", _answer())
    assert "POST /api/v1/agent/resume" not in got["trace"] and "HELD sda" not in got["trace"], got


# --- 3. TRIM ובדיקת הכתיבה ------------------------------------------------------------------------------------------


def test_a_drawer_that_resumes_is_neither_trimmed_nor_test_written_and_its_table_is_written_again(tmp_path):
    got = _run(tmp_path, "drawers", _answer(), env="MARKED=sda")
    trace = got["trace"]
    assert "write_check sdb" in trace, got                    # the check of the round, without sda
    assert "discard sdb" in trace and "discard sda" not in trace, trace
    assert "table sda" in trace and "table sdb" in trace and "expand sda" in trace


# --- 4-5. הקריאה החוזרת ----------------------------------------------------------------------------------------------


def test_only_the_chunks_read_back_whole_enter_the_state_and_the_ledger(tmp_path):
    got = _run(tmp_path, "seed", _answer())
    trace = got["trace"]
    assert any(t.startswith("readback --readback -x /dev/sda3 20971520") for t in trace), got
    assert not any("/dev/sdb3" in t for t in trace), "only the drawer that resumes is read back"
    state = (tmp_path / "chunk" / "p3.state").read_text()
    assert state == "chunkcast-state 1 abc 3 5\n/dev/sda3 ok 3 0-1,3-3\n", state
    assert (tmp_path / "targets" / "sda" / "ledger" / "durable.3").read_text() == "0-1\n3-3\n"
    assert "אומתו 3 מתוך 5" in got["log"] and "אמר 4" in got["log"]          # the ledger said 4; 3 are believed


def test_a_read_back_that_failed_writes_the_partition_from_the_start(tmp_path):
    got = _run(tmp_path, "seed", _answer(), env="RB_FAIL=1")
    assert not (tmp_path / "chunk" / "p3.state").exists(), got
    assert "לא הצלחנו לקרוא בחזרה את sda" in got["log"] and "rc=0" in got["trace"]


def test_a_read_back_that_ended_before_its_last_chunk_believes_nothing_of_it(tmp_path):
    got = _run(tmp_path, "seed", _answer(), env="RB_SHORT=1")
    assert not (tmp_path / "chunk" / "p3.state").exists(), got
    assert "לא הצלחנו לקרוא בחזרה את sda" in got["log"]


# --- the hook in chunk_partition_drawers: what was read back goes out with `listening` ------------------------------


def test_the_partition_loop_reads_back_before_it_listens_and_tells_the_server_what_is_verified(tmp_path):
    """הליבה מקצה לקצה במחיצה: `chunk_partition_drawers` האמיתי (הרתמה של test_chunkrecv_2533.py), sda ממשיכה. לפני
    המקלט הראשון היא נקראת בחזרה, ו-`listening` נושא לשרת את מה שאומת בה -- 0-1 ו-3, לא 4 של ה-ledger -- כדי שהמעבר
    הראשון ישודר רק עם מה שחסר. ל-sdb, שנכתבת מההתחלה, אין דבר."""
    import test_chunkrecv_2533 as base
    harness = base.HARNESS.replace(
        '\nmkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb" "$RUN_DIR/chunk"\n',
        '\n[ ! -f agent/lib/restore/resume.sh ] || . agent/lib/restore/resume.sh\n'
        'mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb" "$RUN_DIR/chunk"\n', 1).replace(
        '\n_disks="sda sdb"\n',
        '\n: > "$RUN_DIR/targets/sda/resume"   # #2528: the brain said resume, enforced (resume_round)\n'
        'fanout() { case "$1" in --readback) trace "readback $3"; cat "$RUN_DIR/readback" ;; *) echo abc ;; esac; }\n'
        '_disks="sda sdb"\n', 1)
    # the line put in names resume.sh twice ("-f" and "."): what is counted is the line, once -- the base did not move under it
    assert harness.count("[ ! -f agent/lib/restore/resume.sh ] || . agent/lib/restore/resume.sh") == 1 \
        and "targets/sda/resume" in harness, "the base harness moved: this test proves nothing"
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "idx").write_text(base.INDEX, encoding="ascii", newline="\n")
    (tmp_path / "readback").write_text(READBACK, encoding="ascii", newline="\n")
    (tmp_path / "b3").write_bytes(base.B3_LIST)   # #3332: the partition's BLAKE3 list is required before a chunk is written
    (tmp_path / "manifest.json").write_text(json.dumps({"partitions": [{"index": 3, "index_xxh64": "abc", "chunk_b3": base.B3_HASH}]}))
    (tmp_path / "resume.answer.json").write_text(json.dumps(_answer()))
    for n, answer in enumerate([base.RECEIVE1, base.DONE], start=1):
        (tmp_path / f"answer.{n}.json").write_text(json.dumps(answer))
    (tmp_path / "answer.last.json").write_text(json.dumps(base.DONE))
    (tmp_path / "harness.sh").write_text(harness, encoding="utf-8", newline="\n")
    # REAL_SLEEP: the read-back is waited for a second at a time, as on the machine -- the harness's no-op sleep would race it
    proc = subprocess.run([BASH, "-c", f'B3_HASH={base.B3_HASH} REAL_SLEEP=1 . {posix(tmp_path / "harness.sh")!r} {posix(tmp_path)!r}'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL,
                          timeout=120)
    trace = (tmp_path / "trace").read_text(encoding="utf-8").splitlines() if (tmp_path / "trace").exists() else []
    got = proc.stdout + proc.stderr
    assert "readback /dev/sda3" in trace, (trace, got)
    recv = next(t for t in trace if t.startswith("chunkcast recv"))
    assert trace.index("readback /dev/sda3") < trace.index(recv), trace
    listening = json.loads((tmp_path / "body.1.json").read_text())
    assert listening["state"] == "listening", listening
    by = {t["serial"]: t for t in listening["targets"]}
    assert by["SER-sda"]["durable"] == [{"part": 3, "ranges": [[0, 1], [3, 3]]}], by
    assert "durable" not in by["SER-sdb"], by
    assert "rc=0" in trace, trace
