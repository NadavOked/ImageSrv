"""#3336 (of #2988): a drawer that stands still inside a chunk pass is said in seconds.

#2988 gave the cloner's screen and the console the line "waiting for the disk ... N s" / "the network was cut --
waiting". The record behind it (targets/<dev>/stall) was written by `drawer_stall_tick`, and the only caller was
`drawer_watch` -- which does not run in a chunk pass. So in a chunk round a pulled disk or a pulled cable left the
drawer on "writing" with its last speed, as before #2988.

`chunk_stall_tick` (agent/lib/restore/chunkstall.sh) is ticked by `chunk_watch` (chunkwatch.sh) with what a chunk
pass can say of itself: the durable bytes of each drawer, the datagrams of the stream (a fetch: the chunks apply
took), and chunkcast's own ceilings. stall.sh still decides nothing.

The first group runs the tick on a fake clock, deterministically. The second runs the real `chunk_partition_drawers`
(the harness of test_chunkrecv_2533.py) against a `chunkcast` that stands still, and reads what the screen's reader
(`stall_json`) says WHILE the pass runs.

**בקרה שלילית:** החזרת `stall.sh`, `chunkwatch.sh` ו-`imagesrv-agent` למצבם ומחיקת `chunkstall.sh` (כל קובצי הייצור
של התיקון) מפילה את כל הטסטים כאן.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_chunkrecv_2533 import B3_HASH, B3_LIST, DONE, HARNESS, INDEX, RECEIVE1

REPO = AGENT.parent
pytestmark = [
    requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן"),
    # "within seconds" is measured in the real seconds of the pass's own loop (#2118)
    pytest.mark.serial,
]
REAL = requires_native(("bash", BASH), "jq", posix=True, why="ln -s: the counters are the real files")

SNMP = ('printf "Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors IgnoredMulti '
        'MemErrors\\nUdp: %s 0 0 0 0 0 0 0 0\\n" "$1" > "$SYSROOT/proc/net/snmp"')

#: one tick of the watch on a fake clock. now, sda's and sdb's durable bytes, the datagrams so far, the chunks each
#: holds of the 5 (5 = whole), and `taken` (a fetch's own count of the chunks it took) when there is one
TICK = (
    'export SYSROOT="$RUN_DIR/sysroot" WAIT_STREAM_STALL_S=300; '
    + agent_sources("common", "sysinfo", "waits", "progress", "failmark", "stall", "chunkrecv", "chunkstall")
    + 'date() { cat "$RUN_DIR/clock"; }; log() { echo "$*" >> "$RUN_DIR/log"; }; '   # common.sh sets its own LOG_FILE
    'partition_node() { echo "/dev/$1$2"; }; CHUNK_IDX="$RUN_DIR/idx"; CHUNK_STATE="$RUN_DIR/chunk/state"; '
    'snmp() { ' + SNMP + '; }; '
    'step() { echo "$1" > "$RUN_DIR/clock"; echo "$2" > "$RUN_DIR/targets/sda/bytes.0"; echo "$3" > "$RUN_DIR/targets/sdb/bytes.0"; '
    + 'if [ "$4" = none ]; then rm -f "$SYSROOT/proc/net/snmp"; else snmp "$4"; fi; '
    '{ echo "chunkcast-state 1 abc 3 5"; echo "/dev/sda3 ok $5 0-$(($5 - 1))"; echo "/dev/sdb3 ok $6 0-$(($6 - 1))"; } > "$CHUNK_STATE"; '
    'if [ -n "$7" ]; then echo "$7" > "$CHUNK_STATE.taken"; fi; '
    'chunk_stall_tick 3 "$CHUNK_STATE" sda sdb; }; '
)


def _ticks(tmp_path: Path, steps: list[tuple], carrier: str = "1", pre: str = "", tail: str = "") -> dict:
    run = tmp_path / "run"
    for d in ("sda", "sdb"):
        (run / "targets" / d).mkdir(parents=True)
        (run / "targets" / d / "state").write_text("writing\n", newline="\n")
    (run / "chunk").mkdir()
    (run / "idx").write_text(INDEX, encoding="ascii", newline="\n")
    (run / "sysroot" / "proc" / "net").mkdir(parents=True)
    net = run / "sysroot" / "sys" / "class" / "net"
    (net / "eth0").mkdir(parents=True); (net / "eth0" / "carrier").write_text(carrier + "\n", newline="\n")
    (net / "lo").mkdir(); (net / "lo" / "carrier").write_text("1\n", newline="\n")   # lo never counts
    calls = "".join("step " + " ".join(f"'{'' if v is None else v}'" for v in (*s, *([None] * (7 - len(s))))) + "; "
                    for s in steps)
    out = sh(f"export RUN_DIR={posix(run)!r}; " + pre + TICK + calls + tail)
    got = {"out": out, "log": (run / "log").read_text(encoding="utf-8") if (run / "log").exists() else ""}
    for d in ("sda", "sdb"):
        f = run / "targets" / d / "stall"
        got[d] = f.read_text().split() if f.exists() else None
    got["files"] = sorted(p.name for p in run.iterdir())
    return got


#: both drawers take bytes until 101; then sda's disk stands (20 for ever) while sdb and the stream go on
DISK = [(100, 10, 10, 1000, 3, 3), (101, 20, 20, 2000, 3, 3)] + [
    (t, 20, 20 + 10 * (t - 101), 1000 * (t - 99), 3, 3) for t in range(102, 108)]
#: ...and a cut: nothing moves any more -- the bytes, and the datagrams
CUT = [(100, 10, 10, 1000, 3, 3), (101, 20, 20, 2000, 3, 3)] + [(t, 20, 20, 2000, 3, 3) for t in range(102, 108)]


def test_a_drawer_whose_disk_stops_while_the_stream_flows_is_waiting_for_its_disk(tmp_path):
    got = _ticks(tmp_path, DISK, tail='stall_json sda; echo; stall_json sdb; echo end')
    assert got["sda"] == ["waiting_disk", "101", "120"]   # since its last byte; CHUNKCAST_WRITE_STALL_MS's 120 s (18/11; was 150)
    assert got["sdb"] is None
    assert got["out"].splitlines()[0] == ',"stall":"waiting_disk","stall_s":6,"stall_limit_s":120'
    assert "sda: no bytes for 6s -- waiting_disk" in got["log"]


def test_the_disk_ceiling_shown_is_chunkcasts_not_fanouts(tmp_path):
    """#3035: the number on the screen is the one that applies. chunkcast fails a disk by CHUNKCAST_WRITE_STALL_MS."""
    got = _ticks(tmp_path, DISK, pre="export CHUNKCAST_WRITE_STALL_MS=90000 FANOUT_STALL_EVICT_MS=30000; ")
    assert got["sda"] == ["waiting_disk", "101", "90"]


def test_a_pulled_cable_is_the_network_and_shown_with_the_streams_ceiling(tmp_path):
    got = _ticks(tmp_path, CUT, carrier="0")
    assert got["sda"] == ["waiting_net", "101", "300"] and got["sdb"] == ["waiting_net", "101", "300"]   # WAIT_STREAM_STALL_S


def test_a_stream_that_stopped_with_the_cable_in_is_the_network_too(tmp_path):
    got = _ticks(tmp_path, CUT, carrier="1")
    assert got["sda"][0] == "waiting_net" and got["sdb"][0] == "waiting_net"
    assert _ticks(tmp_path / "early", CUT[:6], carrier="1")["sda"] is None   # 3 s of nothing is not yet six


def test_a_stray_broadcast_is_not_the_stream(tmp_path):
    """Three datagrams a tick (mDNS, DHCP) must not read as "the stream kept coming": that would blame the disk."""
    stray = CUT[:2] + [(t, 20, 20, 2000 + 3 * (t - 101), 3, 3) for t in range(102, 108)]
    got = _ticks(tmp_path, stray)
    assert got["sda"][0] == "waiting_net", got


def test_a_fetch_is_judged_by_the_chunks_it_took_not_by_datagrams(tmp_path):
    """A fetch is HTTP: no datagrams. A drive whose flush is held (#3149) takes chunks and has nothing durable."""
    steps = [(100, 10, 10, 1000, 3, 3, 1), (101, 20, 20, 1000, 3, 3, 2)] + [
        (t, 20, 20 + 10 * (t - 101), 1000, 3, 3, t - 99) for t in range(102, 108)]
    got = _ticks(tmp_path, steps, pre="export CHUNK_TELL=1; ")
    assert got["sda"] == ["waiting_disk", "101", "120"] and got["sdb"] is None
    # and a fetch that took nothing either is the network, not the disk
    silent = [(100, 10, 10, 1000, 3, 3, 1), (101, 20, 20, 1000, 3, 3, 2)] + [(t, 20, 20, 1000, 3, 3, 2) for t in range(102, 108)]
    assert _ticks(tmp_path / "net", silent, pre="export CHUNK_TELL=1; ")["sda"][0] == "waiting_net"


def test_a_drawer_that_holds_the_whole_partition_is_not_standing(tmp_path):
    """Nothing is due to it, whatever the stream does for the others -- not "waiting for its disk"."""
    whole = [s[:4] + (5, 3) for s in DISK]
    got = _ticks(tmp_path, whole)
    assert got["sda"] is None and got["sdb"] is None, got


def test_a_first_chunk_that_has_not_come_is_not_a_stall(tmp_path):
    nothing = [(t, 0, 0, 1000, 3, 3) for t in range(100, 110)]
    got = _ticks(tmp_path, nothing, carrier="1")
    assert got["sda"] is None and got["sdb"] is None, got


def test_a_stream_counter_that_was_not_read_names_no_cause_but_a_cut_cable(tmp_path):
    """No /proc/net/snmp: nothing to judge the stream by (principle 5) -- never "the disk" on a guess."""
    unread = [(100, 10, 10, "none", 3, 3), (101, 20, 20, "none", 3, 3)] + [(t, 20, 20 + t, "none", 3, 3) for t in range(102, 108)]
    got = _ticks(tmp_path / "in", unread, carrier="1")
    assert got["sda"] is None and got["sdb"] is None, got
    assert _ticks(tmp_path / "out", unread, carrier="0")["sda"][0] == "waiting_net"   # a cable that reads 0 is evidence


def test_the_end_of_the_pass_clears_it_and_the_next_pass_starts_clean(tmp_path):
    got = _ticks(tmp_path, DISK, tail='chunk_stall_end sda sdb')
    assert got["sda"] is None
    assert "sda: stood still 6s (waiting_disk), then the partition's stream ended" in got["log"]
    for left in ("stall.src", "stall.dg"):
        assert left not in got["files"], got["files"]


def test_a_completion_in_the_background_is_not_the_rounds_stream(tmp_path):
    got = _ticks(tmp_path, DISK, pre="export CHUNK_BG=1; ")
    assert got["sda"] is None and got["sdb"] is None and "stall.src" not in got["files"]


def test_the_chunk_watch_ticks_it_and_the_agent_loads_it():
    watch = (AGENT / "lib/restore/chunkwatch.sh").read_text(encoding="utf-8")
    body = watch[watch.index("chunk_watch() {"):watch.index("_chunk_arrived() {")]
    assert 'chunk_stall_tick "$_cw_idx" "$_cw_live" "$@"' in body and 'chunk_stall_end "$@"' in body
    # a state that was not read judges nothing: the tick is behind `_cw_read`
    assert re.search(r'"\$_cw_read" = 1 \] && command -v chunk_stall_tick', body)
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/chunkstall.sh"' in main


# --- the real pass --------------------------------------------------------------------------------------

#: stall.sh, and what the pass's counters are read with (progress.sh) -- before the harness's stubs, which win
HARNESS_STALL = (
    HARNESS.replace(
        "WAIT_STREAM_STALL_S=1; WAIT_DRAWER_S=1;",
        # the agent's order and what each calls (#2272): progress.sh reads the disks (sysinfo) and writes the seen mark (failmark)
        agent_sources("sysinfo", "failmark", "progress", "stall") + '\n'
        '[ ! -f agent/lib/restore/chunkstall.sh ] || . agent/lib/restore/chunkstall.sh\n'
        "WAIT_STREAM_STALL_S=1; WAIT_DRAWER_S=1;", 1)
    .replace(
        'mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb" "$RUN_DIR/chunk"\n',
        # the drawers' counters are the real files (progress.sh's target_bytes reads bytes.<n>), a cable the pass
        # can pull, and a `chunkcast recv` that takes chunks for three seconds and then stands still.
        r'''target_counter() { rm -f "$2"; ln -s "$RUN_DIR/targets/$1/bytes.0" "$2"; }
mkdir -p "$SYSROOT/proc/net" "$SYSROOT/sys/class/net/eth0" "$SYSROOT/sys/class/net/lo"
echo 1 > "$SYSROOT/sys/class/net/eth0/carrier"; echo 1 > "$SYSROOT/sys/class/net/lo/carrier"
chunkcast() {
    [ "$1" = recv ] || return 2
    trace "chunkcast $*"; echo listening
    _snmp() { printf 'Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors InCsumErrors IgnoredMulti MemErrors\nUdp: %s 0 0 0 0 0 0 0 0\n' "$1" > "$SYSROOT/proc/net/snmp.new" && mv "$SYSROOT/proc/net/snmp.new" "$SYSROOT/proc/net/snmp"; }
    _two() { { echo "chunkcast-state 1 abc 3 5"; echo "/dev/sda3 ok $1 $2"; echo "/dev/sdb3 ok $3 $4"; } > "$CHUNK_STATE.new"; mv "$CHUNK_STATE.new" "$CHUNK_STATE"; }
    _dg=1000; _snmp "$_dg"
    for _c in 1 2 3; do _two "$_c" "0-$((_c - 1))" "$_c" "0-$((_c - 1))"; _dg=$((_dg + 1000)); _snmp "$_dg"; command sleep 1; done
    date +%s > "$RUN_DIR/standing_at"; trace "STANDING"
    [ "$STALL_CASE" != link_down ] || echo 0 > "$SYSROOT/sys/class/net/eth0/carrier"
    [ "$STALL_CASE" != disk ] || _two 5 0-4 3 0-2        # sda holds every chunk; sdb's disk stands, and the stream goes on
    _w=0
    until [ -s "$RUN_DIR/targets/sdb/stall" ] || [ "$_w" -ge 80 ]; do
        [ "$STALL_CASE" != disk ] || { _dg=$((_dg + 1000)); _snmp "$_dg"; }
        command sleep 0.5; _w=$((_w + 1))
    done
    date +%s > "$RUN_DIR/seen_at"
    cp "$RUN_DIR/targets/sdb/stall" "$RUN_DIR/seen.stall.sdb" 2> /dev/null
    stall_json sdb > "$RUN_DIR/seen.json.sdb"; stall_json sda > "$RUN_DIR/seen.json.sda"; trace "SEEN"
    _two 5 0-4 5 0-4
    echo "pass complete"
}
mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb" "$RUN_DIR/chunk"
''', 1))
assert 'trace "STANDING"' in HARNESS_STALL and "/progress.sh; " in HARNESS_STALL and "/stall.sh; " in HARNESS_STALL

PASS = "REAL_SLEEP=1 TEST_START_CEILING=30 CHUNK_STALL_SHOW_S=2"   # the loop's own seconds are real; the ceiling is not what this tests


def _pass(tmp_path: Path, case: str, env: str = "") -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "idx").write_text(INDEX, encoding="ascii", newline="\n")
    (tmp_path / "b3").write_bytes(B3_LIST)   # #3332: the partition's BLAKE3 list is required before a chunk is written
    (tmp_path / "manifest.json").write_text(json.dumps({"partitions": [{"index": 3, "index_xxh64": "abc", "chunk_b3": B3_HASH}]}))
    for n, answer in enumerate([RECEIVE1, DONE], start=1):
        (tmp_path / f"answer.{n}.json").write_text(json.dumps(answer))
    (tmp_path / "answer.last.json").write_text(json.dumps(DONE))
    (tmp_path / "harness.sh").write_text(HARNESS_STALL, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, "-c", f'B3_HASH={B3_HASH} STALL_CASE={case} {PASS} {env} . {posix(tmp_path / "harness.sh")!r} {posix(tmp_path)!r}'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)

    def read(name: str) -> str | None:
        p = tmp_path / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    trace = (read("trace") or "").splitlines()
    return {"trace": trace, "out": proc.stdout + proc.stderr, "log": read("agent.log") or "",
            "json_sdb": read("seen.json.sdb"), "json_sda": read("seen.json.sda"), "stall_sdb": read("seen.stall.sdb"),
            "took": int(read("seen_at") or 0) - int(read("standing_at") or 0)}


@REAL
def test_in_a_chunk_pass_a_disk_that_stands_is_waiting_for_its_disk_in_the_report(tmp_path):
    """The core. sdb's disk stands while the stream flows and sda is whole: sdb's drawer record says so, while the
    pass runs, in the words the screen and the console read -- and sda says nothing."""
    got = _pass(tmp_path, "disk")
    assert "SEEN" in got["trace"] and got["trace"][-1] == "rc=0", got
    assert re.fullmatch(r',"stall":"waiting_disk","stall_s":\d+,"stall_limit_s":120', got["json_sdb"]), got["json_sdb"]
    assert got["json_sda"] == "", got["json_sda"]
    assert got["took"] <= 15, f"it took {got['took']}s to be said"
    assert "sdb: no bytes for" in got["log"] and "waiting_disk" in got["log"]
    for d in ("sda", "sdb"):          # the pass ended: nothing stands any more (by which word it ended is a race: whole, moved, ended)
        assert not (tmp_path / "targets" / d / "stall").exists()


@REAL
@pytest.mark.parametrize("case", ["quiet", "link_down"])
def test_in_a_chunk_pass_a_cut_network_is_the_network_for_every_drawer(tmp_path, case):
    """Nothing moves: the stream stopped (the cable in, or pulled -- carrier 0). Both drawers say the network,
    with WAIT_STREAM_STALL_S (the harness's 1) -- chunkcast's own --stall-s -- as the ceiling."""
    got = _pass(tmp_path, case)
    assert "SEEN" in got["trace"] and got["trace"][-1] == "rc=0", got
    for who in ("json_sda", "json_sdb"):
        assert re.fullmatch(r',"stall":"waiting_net","stall_s":\d+,"stall_limit_s":1', got[who]), (who, got[who])
    assert got["took"] <= 15, f"it took {got['took']}s to be said"
    assert "waiting_net" in got["log"] and "waiting_disk" not in got["log"]
