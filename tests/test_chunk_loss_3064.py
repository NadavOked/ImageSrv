"""#3064 שלב 4 (G6/G7): **מה שהמקלט איבד בגל חתיכות מגיע לשרת** -- `agent/lib/restore/chunkloss.sh`.

עד כאן `RcvbufErrors` נספר רק במחיצת udpcast (`waits.sh`), ו"chunks abandoned half-received" של `chunkcast` הגיע רק
ללוג, אחרי המעבר. תקציב הרשת של המוח (K7) לא ראה שום אות מהמקלטים בגל חתיכות, ואמר "אין מדידה".

- תוך כדי המעבר `udp_drops.json` מתעדכן: rcvbuf/inerrors מ-`/proc/net/snmp` מאז תחילת המעבר, ו-`abandoned` מהשורה
  האחרונה ש-`chunkcast` הדפיס -- ועוד מה שהמעברים הקודמים של אותו שחזור איבדו.
- מונה שלא נקרא = ``null`` לכל הסכום, לא 0 (עיקרון 5). `chunkcast` שלא אמר "abandoned" = "לא נקרא".
- `build_progress` (progress.sh) נושא את `udp_drops.json` כמו שהוא, והשרת שומר את `abandoned` לצד `rcvbuf`.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

import chunkcast_rig as C
import test_chunkrecv_2533 as recv_test
from test_chunkcast_2533 import _pass, cc   # noqa: F401 -- cc is a fixture: the real chunkcast
from native import requires_native
from server.agent import reports
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
pytestmark = requires_native(("bash", BASH), why="the agent's shell")
#: the loop of chunkrecv.sh reads the server's answers with jq, as the agent does
needs_jq = requires_native(("bash", BASH), "jq", why="the server's answers are read with jq")

UNIT = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log" SYSROOT="$1/sysroot"
. agent/lib/core/common.sh; . agent/lib/core/waits.sh
[ ! -f agent/lib/restore/chunkloss.sh ] || . agent/lib/restore/chunkloss.sh
snmp() { mkdir -p "$SYSROOT/proc/net"; printf 'Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors\nUdp: 1 0 %s 1 %s\n' "$2" "$1" > "$SYSROOT/proc/net/snmp"; }
show() { echo "$1 $(cat "$RUN_DIR/udp_drops.json" 2>/dev/null || echo none)"; }
udp_drops_reset
'''


def _unit(tmp_path, script: str) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.sh").write_text(UNIT + script, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, posix(tmp_path / "t.sh"), posix(tmp_path)], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return [line.split(" ", 1) for line in proc.stdout.splitlines()]


#: the three counters of #3064 stage 4. The two facts beside them (rcvbuf_bytes, pass_word: G7) have their own file,
#: test_chunk_loss_g7_3064.py -- here the counters are judged as they were, with nothing else to compare.
COUNTERS = ("rcvbuf", "inerrors", "abandoned")


def _counters(report):
    return None if report is None else {key: report[key] for key in COUNTERS}


def _json(got, name):
    """the file as the reporter would read it; ``None`` = not written at all."""
    text = dict(got)[name]
    return None if text == "none" else _counters(json.loads(text))


def _drops(tmp_path):
    path = tmp_path / "udp_drops.json"
    return _counters(json.loads(path.read_text())) if path.exists() else None


def test_a_pass_tells_its_losses_while_it_runs_and_adds_them_to_the_restore_when_it_ends(tmp_path):
    got = _unit(tmp_path, r'''
snmp 100 5; chunk_loss_mark; printf 'listening\nabandoned 0\n' > "$RUN_DIR/recv.out"
snmp 130 6; printf 'abandoned 4\n' >> "$RUN_DIR/recv.out"; chunk_loss_tell "$RUN_DIR/recv.out"; show during1
snmp 150 6; printf 'abandoned 7\n' >> "$RUN_DIR/recv.out"; chunk_loss_end "$RUN_DIR/recv.out"; show end1
snmp 160 6; chunk_loss_mark; printf 'listening\nabandoned 0\n' > "$RUN_DIR/recv.out"
snmp 165 9; printf 'abandoned 2\n' >> "$RUN_DIR/recv.out"; chunk_loss_tell "$RUN_DIR/recv.out"; show during2
chunk_loss_end "$RUN_DIR/recv.out"; show end2
''')
    assert _json(got, "during1") == {"rcvbuf": 30, "inerrors": 1, "abandoned": 4}
    assert _json(got, "end1") == {"rcvbuf": 50, "inerrors": 1, "abandoned": 7}
    assert _json(got, "during2") == {"rcvbuf": 55, "inerrors": 4, "abandoned": 9}   # the restore's sum + this pass
    assert _json(got, "end2") == {"rcvbuf": 55, "inerrors": 4, "abandoned": 9}
    assert "chunks abandoned half-received=2" in (tmp_path / "agent.log").read_text(encoding="utf-8")


def test_a_counter_not_read_is_null_for_the_whole_restore_never_zero(tmp_path):
    got = _unit(tmp_path, r'''
chunk_loss_mark; printf 'listening\n' > "$RUN_DIR/recv.out"; chunk_loss_tell "$RUN_DIR/recv.out"; show nothing
snmp 10 0; chunk_loss_end "$RUN_DIR/recv.out"; show end1
snmp 10 0; chunk_loss_mark; printf 'listening\nabandoned 3\n' > "$RUN_DIR/recv.out"; snmp 12 0
chunk_loss_tell "$RUN_DIR/recv.out"; show later
''')
    # no /proc/net/snmp when the pass began, and a chunkcast that never said "abandoned" (an older binary)
    assert _json(got, "nothing") == {"rcvbuf": None, "inerrors": None, "abandoned": None}
    assert _json(got, "end1") == {"rcvbuf": None, "inerrors": None, "abandoned": None}
    # one pass unread = the restore's sum unread: a partial sum would read as "the receiver is clean"
    assert _json(got, "later") == {"rcvbuf": None, "inerrors": None, "abandoned": None}


def test_a_new_restore_starts_from_nothing(tmp_path):
    got = _unit(tmp_path, r'''
snmp 0 0; chunk_loss_mark; printf 'abandoned 5\n' > "$RUN_DIR/recv.out"; snmp 4 0; chunk_loss_end "$RUN_DIR/recv.out"
udp_drops_reset; show reset
snmp 4 0; chunk_loss_mark; printf 'abandoned 1\n' > "$RUN_DIR/recv.out"; snmp 5 0; chunk_loss_tell "$RUN_DIR/recv.out"; show next
''')
    assert dict(got)["reset"] == "none"
    assert _json(got, "next") == {"rcvbuf": 1, "inerrors": 0, "abandoned": 1}


def test_the_server_keeps_abandoned_beside_rcvbuf_and_absent_stays_absent():
    payload = {"udp_drops": {"rcvbuf": 3, "inerrors": 0, "abandoned": 12}}
    assert reports.well_formed_udp_drops(payload) == {"rcvbuf": 3, "inerrors": 0, "abandoned": 12}
    # a udpcast wave has no such field: absent stays absent, not "0 abandoned"
    assert reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 3, "inerrors": 0}}) == {"rcvbuf": 3, "inerrors": 0}
    assert reports.well_formed_udp_drops({"udp_drops": {"rcvbuf": 3, "inerrors": 0, "abandoned": -1}}) == {
        "rcvbuf": None, "inerrors": None, "abandoned": None}


# --- inside chunkrecv.sh's loop (the harness of test_chunkrecv_2533) -------------------------------------------------


@pytest.fixture()
def lossy_harness(monkeypatch):
    """The receiver of the harness says "abandoned" as chunkcast does, and the kernel's counter grows during the pass."""
    harness = recv_test.HARNESS.replace(
        "            echo listening\n",
        "            echo listening; echo 'abandoned 0'\n"
        "            [ -z \"${RECV_LOSS:-}\" ] || { command sleep 2; echo \"abandoned $RECV_LOSS\";"
        " printf 'Udp: InErrors RcvbufErrors\\nUdp: 0 %s\\n' \"$RECV_LOSS\" > \"$SYSROOT/proc/net/snmp\"; command sleep 2; }\n",
        1)
    assert harness != recv_test.HARNESS
    harness = harness.replace(
        'export SYSROOT="$RUN_DIR/sysroot"; ',
        'export SYSROOT="$RUN_DIR/sysroot"; mkdir -p "$SYSROOT/proc/net";'
        " printf 'Udp: InErrors RcvbufErrors\\nUdp: 0 0\\n' > \"$SYSROOT/proc/net/snmp\"; ", 1)
    monkeypatch.setattr(recv_test, "HARNESS", harness)


@needs_jq
def test_a_chunk_pass_writes_what_its_receiver_lost_while_it_runs(tmp_path, lossy_harness):
    seen = tmp_path / "seen"
    # what the progress reporter would read, sampled once a second beside the pass
    watcher = subprocess.Popen([BASH, "-c", f'for i in $(seq 1 40); do cat {posix(tmp_path)}/udp_drops.json'
                                f' >> {posix(seen)} 2>/dev/null; echo >> {posix(seen)}; sleep 0.25; done'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # REAL_SLEEP + a long start ceiling, as the other slow fakes of test_chunkrecv_2533 do: the fake receiver is silent for
        # four seconds, and the loop's ceiling (1+1+1 s in this harness) is counted in ticks that are no-ops unless sleep is real
        got = recv_test._run(tmp_path, [recv_test.RECEIVE1, recv_test.DONE], env="RECV_LOSS=6 REAL_SLEEP=1 TEST_START_CEILING=30")
    finally:
        watcher.kill()
        watcher.wait()
    assert got["trace"][-1] == "rc=0", got
    assert _drops(tmp_path) == {"rcvbuf": 6, "inerrors": 0, "abandoned": 6}
    during = [_counters(json.loads(line)) for line in seen.read_text().splitlines() if line.strip()]
    assert {"rcvbuf": 6, "inerrors": 0, "abandoned": 6} in during, "the report did not move before the pass ended"
    assert "chunks abandoned half-received=6" in got["log"]


@needs_jq
def test_without_chunkcast_saying_it_the_report_says_not_read(tmp_path, monkeypatch):
    harness = recv_test.HARNESS.replace(
        'export SYSROOT="$RUN_DIR/sysroot"; ',
        'export SYSROOT="$RUN_DIR/sysroot"; mkdir -p "$SYSROOT/proc/net";'
        " printf 'Udp: InErrors RcvbufErrors\\nUdp: 0 0\\n' > \"$SYSROOT/proc/net/snmp\"; ", 1)
    monkeypatch.setattr(recv_test, "HARNESS", harness)
    got = recv_test._run(tmp_path, [recv_test.RECEIVE1, recv_test.DONE])
    assert got["trace"][-1] == "rc=0", got
    assert _drops(tmp_path) == {"rcvbuf": 0, "inerrors": 0, "abandoned": None}


# --- chunkcast itself: the "abandoned N" line (agent/chunkcast.c) ---------------------------------------------------


needs_cc = requires_native("gcc", "zstd", posix=True, why="chunkcast needs gcc, libzstd and zstd")


@needs_cc
def test_chunkcast_says_what_it_abandoned_and_the_count_is_the_one_in_its_log(cc, tmp_path):
    lost = {0, 1, 2, 3}    # four chunks that never complete hold the four slots (CC_SLOTS); the fifth pushes one out
    # a chunk holds a slot only when it is more than one packet, so the five chunks are all incompressible (the shared
    # `img` has two such chunks, and four slots are never full of them)
    img = C.build(tmp_path / "img", cc / "fanout", noisy=(0, 1, 2, 3, 4))

    def rule(receiver, kind, chunk, seq, packet):
        return [] if kind == C.DATA and chunk in lost and seq == 0 else [packet]

    relay = C.Relay(1, rule)
    try:
        (run,) = _pass(cc, img, relay, C.new_session(), [(tmp_path / "state", [img.blank(tmp_path / "a.disk")])],
                       tmp_path, 1)
    finally:
        relay.close()
    out = run.out.read_text().splitlines()
    said = [int(line.split()[1]) for line in out if line.startswith("abandoned ")]
    logged = int(re.search(r"(\d+) chunks abandoned half-received", run.err.read_text()).group(1))
    assert out[:2] == ["listening", "abandoned 0"], out
    assert said[-1] == logged >= 1, (said, logged)
    assert said == sorted(said)
    last = max(i for i, line in enumerate(out) if line.startswith("abandoned "))
    assert last < next(i for i, line in enumerate(out) if line.startswith("pass ")), out
