"""#2533, שלב 3: `chunk_partition_drawers` (`agent/lib/restore/chunkrecv.sh`) -- הלולאה של
מחיצה אחת בצד הסוכן, עם ה-shell האמיתי.

`chunkcast`, השרת והדיסקים מזויפים כאן (את התהליכים האמיתיים בודקים
test_chunkcast_2533.py ו-test_chunk_round_2533.py); מה שנבדק הוא מה שה-shell עצמו
מכריע, וכל טסט כאן נולד מממצא של סקירה על cffe9589:

1. מכונה שכל המגירות שלה שלמות אינה נכשלת בזמן שהיא ממתינה למכונות אחרות.
2. גיבוי סקטור האתחול של NTFS (#1487) נכתב לפני שהמגירה נחתמת, כמו ב-verdict.sh.
3. זהות הדיסק נבדקת לפני כל מעבר, ולא פעם אחת למחיצה.
9. `listening` מדווח רק כשהמקלט אמר שהוא מאזין.
"""

from __future__ import annotations

import json
import subprocess

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

INDEX = ("IMAGESRV-IDX 2 raw 4194304 0 0 - 20971520 5 1000\n"
         + "".join(f"{k * 200} 200 {k:016x}\n" for k in range(5)))
RECEIVE1 = {"ok": True, "action": "receive", "pass": 1}
#: #3332: the partition's BLAKE3 list -- 32 bytes for each of INDEX's 5 chunks -- and the manifest's name for it. What the
#: fake chunkcast gets is only its path and this name: the list is checked by the real one (test_chunk_b3_3332.py).
B3_LIST = bytes(range(160))
B3_HASH = "ab" * 32
DONE = {"ok": True, "action": "done"}

HARNESS = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log"; MAC=aa:bb:cc:00:00:21; SERVER=http://srv; IFACE=""
. agent/lib/core/common.sh; . agent/lib/core/jsonq.sh; . agent/lib/core/waits.sh; . agent/lib/restore/verdict.sh
. agent/lib/core/roundbeep.sh; ''' + agent_sources("sysinfo", "failmark") + r'''   # the outage tune and the GPT wipe are the agent's own (#2272: failmark the agent's way); what they touch is faked below
. agent/lib/restore/chunkledger.sh; . agent/lib/restore/chunkrecv.sh; . agent/lib/restore/chunkfetch.sh
[ ! -f agent/lib/restore/chunkb3.sh ] || . agent/lib/restore/chunkb3.sh   # #3332: the partition's BLAKE3 list, required
# as the agent sources it. Not there = the tree before the pass was watched (the negative control of
# test_chunk_drawer_pull_2533.py): the loop then runs as it did, and the tests fail on what it does.
[ ! -f agent/lib/restore/chunkwatch.sh ] || . agent/lib/restore/chunkwatch.sh
[ ! -f agent/lib/restore/chunkrc.sh ] || . agent/lib/restore/chunkrc.sh   # #3259: what the receiver's exit codes mean
# the same for a drawer pushed back in (drawerback.sh, test_chunk_drawer_returns_2533.py): not there = it stays failed
[ ! -f agent/lib/restore/drawerback.sh ] || . agent/lib/restore/drawerback.sh
# and for the last drawer pulled (drawerhold.sh, test_chunk_drawer_hold_3140.py): not there = the machine leaves the round at once
[ ! -f agent/lib/restore/drawerhold.sh ] || . agent/lib/restore/drawerhold.sh
# and for a drawer found at the kernel's word, that takes the pass on the air and completes from a neighbour first
# (drawerplug.sh, drawerfill.sh, test_drawer_rejoin_3181.py): not there = it is found at the fixed points, and completes over HTTP
[ ! -f agent/lib/restore/drawerplug.sh ] || . agent/lib/restore/drawerplug.sh
[ ! -f agent/lib/restore/drawerfill.sh ] || . agent/lib/restore/drawerfill.sh
# and for the production line (prodline.sh, drawerout.sh, fillword.sh, test_prodline_newdisk_3181.py): not there = another
# disk pushed in is a line in the log, nothing is detached, and the brain's word on a fill is not read
[ ! -f agent/lib/restore/prodline.sh ] || . agent/lib/restore/prodline.sh
[ ! -f agent/lib/restore/drawerout.sh ] || . agent/lib/restore/drawerout.sh
[ ! -f agent/lib/restore/fillword.sh ] || . agent/lib/restore/fillword.sh
# and for a production line's cloner that waits for the next disk (linewait.sh, test_line_wait_3181.py): not there = its
# round ends with the last disk written, names left or not
[ ! -f agent/lib/restore/linewait.sh ] || . agent/lib/restore/linewait.sh
# NEIGHBOR=<how>: neighborfill_run (neighborfill.sh, #3181), by its contract -- the list file is left with what is still missing.
# Not set = this agent has no neighborfill_run at all
[ -z "${NEIGHBOR:-}" ] || neighborfill_run() {
    trace "neighborfill $1 $(tr '\n' ',' < "$2")"; cp "$RUN_DIR/targets/$1/rejoin" "$RUN_DIR/rejoin.seen" 2>/dev/null   # what the report says meanwhile
    case "$NEIGHBOR" in
        all)    : > "$2"; return 0 ;;                                           # every chunk written and re-read whole
        left:*) echo "${NEIGHBOR#left:}" | tr ',' '\n' > "$2"; return 1 ;;      # what it did not bring
        unread) return 2 ;;                                                     # the list or the context was not read: the file is as it was
        slow)   # it reads for half a minute, with a writer of its own this harness knows only by its pid
            command sleep 30 > /dev/null 2>&1 & echo "$!" > "$RUN_DIR/neighbour.pid"; : > "$RUN_DIR/neighbour.started"
            wait "$!"; trace "NEIGHBOUR-END"; : > "$2"; return 0 ;;
    esac
}
# and for what a receiver lost (chunkloss.sh, test_chunk_loss_3064.py): not there = udp_drops is not written in a chunk round
[ ! -f agent/lib/restore/chunkloss.sh ] || . agent/lib/restore/chunkloss.sh
# and for the source the receiver takes packets from, and the count of the others it dropped (chunkgate.sh, test_chunkcast_stray_report_1079.py)
[ ! -f agent/lib/restore/chunkgate.sh ] || . agent/lib/restore/chunkgate.sh
# and for what the agent tells the server's journal (roundnote.sh, test_round_notes_3125.py): not there = it stays in the agent log
[ ! -f agent/lib/restore/roundnote.sh ] || . agent/lib/restore/roundnote.sh
WAIT_STREAM_STALL_S=1; WAIT_DRAWER_S=1; STREAM_START_CEILING=${TEST_START_CEILING:-1}; CHUNK_ASK_POLL_S=1; HTTP_TIMEOUT=5
CHUNK_FETCH_TELL_S=${TEST_TELL_S:-1}; FILL_WORD_S=${TEST_FILL_WORD_S:-0}   # no reporter here: a measure does not wait for the brain's answer (fillword.sh)
CHUNK_GROUP=239.84.82.1; CHUNK_PORT=7713; CHUNK_KEY=0123456789abcdef; CHUNK_SESSION=ses_1   # פורט-הפצה-מכוון
trace() { echo "$*" >> "$RUN_DIR/trace"; }
_await_file() {
    local n=0
    until [ -e "$RUN_DIR/$1" ]; do
        [ "$n" -lt 600 ] || { trace "SYNC-TIMEOUT $1"; return 1; }
        command sleep 0.1; n=$((n + 1))
    done
}
sleep() { [ -z "${REAL_SLEEP:-}" ] || command sleep "$@"; }
watchdog_beat() { trace "beat"; }
# the kernel's side, faked under $SYSROOT: a disk that left sysfs has no serial to read any more
export SYSROOT="$RUN_DIR/sysroot"; mkdir -p "$SYSROOT/sys/block/sda/device" "$SYSROOT/sys/block/sdb/device"
dmesg() { cat "$RUN_DIR/kmsg" 2>/dev/null; }
event_beep() { trace "beep $1"; }
# a disk's serial is SER-<its name>, unless its sysfs says another (a drawer back under a new name keeps its own)
disk_serial() { [ "$1" = "${NOSERIAL:-}" ] || [ ! -e "$SYSROOT/sys/block/$1" ] || cat "$SYSROOT/sys/block/$1/device/serial" 2>/dev/null || echo "SER-$1"; }
target_bound_serial() { [ "$1" = "${NOSERIAL:-}" ] || { _tb=$(cat "$RUN_DIR/targets/$1/bound") && printf '%s' "${_tb%|*|*}"; }; }   # the serial it was chosen by (failmark.sh)
list_drawers() { ls "$SYSROOT/sys/block"; }; list_disks() { list_drawers; }
disk_port() { cat "$SYSROOT/sys/block/$1/port" 2>/dev/null; }
target_set() { echo "$2" > "$RUN_DIR/targets/$1/state"; trace "SET $1 $2"; }
is_swap_partition() { [ "$1" = swap ]; }; make_swap() { trace "mkswap $*"; }
drawer_answers() { [ -e "$SYSROOT/sys/block/$1/answers" ]; }   # a read of it came back (drawerback.sh): only a disk that came back
STREAM_MODE=chunk
_wc_mbps() { echo 150; }; partition_node() { echo "/dev/$1$2"; }
node_is_block() { return 0; }; target_counter() { :; }; next_stream_ceiling() { :; }
round_closed() { [ -e "$RUN_DIR/closed" ]; }; round_closed_why() { echo "the round was closed"; }
cancel_pending() { trace "cancel_pending"; }
assert_target() {   # counted per disk; SWAPPED=<disk>:<n> = at its n-th check it is no longer the disk chosen
    _n=$(cat "$RUN_DIR/assert.$1" 2>/dev/null || echo 0); _n=$((_n + 1)); echo "$_n" > "$RUN_DIR/assert.$1"
    trace "assert $1 $_n"
    [ "$1:$_n" != "${SWAPPED:-}" ] || { echo failed > "$RUN_DIR/targets/$1/state"; trace "FOREIGN $1"; return 1; }
}
fail_written_target() {
    [ -e "$RUN_DIR/failed_at.$1" ] || date +%s > "$RUN_DIR/failed_at.$1"
    echo failed > "$RUN_DIR/targets/$1/state"; trace "FAIL $1 $2"
    wipe_written_gpt "$1" "a failed restore" || :   # failmark.sh's own (#1388): only a disk whose serial write_started holds
}
# the host's /dev is never handed to the agent (test_tests_never_touch_host_dev.py): the trace drops this run's prefix
DEVROOT="$RUN_DIR/dev"; target_same() { return 0; }; blockdev() { :; }
sgdisk() {   # ZAP_HANGS=<disk>: the wipe of that disk is held by the disk itself, as a dying one holds it -- for a minute
    trace "sgdisk $(echo "$*" | sed "s|$RUN_DIR||g")"
    case "$*" in *"/${ZAP_HANGS:-none}") command sleep 60 > /dev/null 2>&1 & echo "$!" >> "$RUN_DIR/hung.pids"; wait "$!" ;; esac
}
target_partition_done() {   # counted, as progress.sh counts it: drawerback.sh reads how far a drawer is
    trace "DONE $1"; echo $(($(cat "$RUN_DIR/targets/$1/counter.index") + 1)) > "$RUN_DIR/targets/$1/counter.index"
}
ntfs_boot_target() {
    trace "ntfs_boot $1 $2 $3"
    [ "${NTFS_BAD:-}" != "$1" ] || { NTFS_BOOT_ERROR="NTFS backup boot sector of $1 was not written"; return 1; }
}
http_get() {   # the index, or the BLAKE3 list (#3332; B3_FAILS: it does not arrive)
    trace "GET $1"; case "$1" in */b3/*) [ -z "${B3_FAILS:-}" ] && cat "$RUN_DIR/b3" ;; *) cat "$RUN_DIR/idx" ;; esac
}
fanout() { echo abc; }
http_post_json() {   # the n-th control message gets the n-th canned answer (the last one repeats)
    if [ "$(jq -r .state "$2")" = fetching ]; then   # a fetch's reports are not questions: counted apart
        _f=$(cat "$RUN_DIR/fetchings" 2>/dev/null || echo 0); _f=$((_f + 1)); echo "$_f" > "$RUN_DIR/fetchings"
        cp "$2" "$RUN_DIR/fetching.$_f.json"; echo TOLD >> "$RUN_DIR/order"; echo '{"ok": true, "action": "wait"}'; return 0
    fi
    _n=$(cat "$RUN_DIR/posts" 2>/dev/null || echo 0); _n=$((_n + 1)); echo "$_n" > "$RUN_DIR/posts"
    cp "$2" "$RUN_DIR/body.$_n.json"; trace "POST $(jq -r '"\(.state) \(.part)"' "$2")"
    # SERVER_GONE_AFTER=N: from the (N+1)-th question on the server does not answer (curl's 22)
    [ -z "${SERVER_GONE_AFTER:-}" ] || [ "$_n" -le "$SERVER_GONE_AFTER" ] || { trace "post-failed"; return 22; }
    if command -v harness_answer > /dev/null && harness_answer "$2"; then return 0; fi
    cat "$RUN_DIR/answer.$_n.json" 2>/dev/null || cat "$RUN_DIR/answer.last.json"
}
_fake_state() {   # $1 = count, $2 = ranges, $3.. = chunkcast's own arguments: every node holds that
    _fs_n="$1"; _fs_r="$2"; shift 2
    { echo "chunkcast-state 1 abc 3 5"; for _a in "$@"; do case "$_a" in /dev/*) echo "$_a ok $_fs_n $_fs_r" ;; esac; done; } > "$CHUNK_STATE.new"
    mv "$CHUNK_STATE.new" "$CHUNK_STATE"
    # NET_TICK: what the kiosk writes when a drawer's counter moves (cloner_gui_pace, moved.<dev>)
    [ -z "${NET_TICK:-}" ] || for _fs_d in sda sdb; do echo "$(date +%s) $_fs_n" > "$GUI_DIR/moved.$_fs_d"; done
}
_fake_fail() {   # $1 = node, $2 = chunkcast's reason (default: a flush that failed): from now on chunkcast itself calls it failed
    sed "s|^$1 ok \([0-9]*\) \([^ ]*\).*|$1 failed \1 \2 ${2:-the flush to the disk failed}|" "$CHUNK_STATE" > "$CHUNK_STATE.new"
    mv "$CHUNK_STATE.new" "$CHUNK_STATE"
}
_dies() {   # $1 = disk, $2 = how: what the kernel (or chunkcast alone) says of a drawer that dies inside a pass
    case "$2" in
        vanish)      rm -rf "$SYSROOT/sys/block/$1" ;;
        offline)     echo offline > "$SYSROOT/sys/block/$1/device/state" ;;
        write_error) echo "[  200.000001] sd 2:0:0:0: [$1] tag#3 device offline error, dev $1, sector 4096 op 0x1:(WRITE) flags 0x0 phys_seg 1 prio class 2" >> "$RUN_DIR/kmsg" ;;
        write_error_gone)   # #3270: the same refused WRITE, node still in sysfs -- and the node goes only AFTER the round has failed the drawer
            echo "[  200.000001] sd 2:0:0:0: [$1] tag#3 device offline error, dev $1, sector 4096 op 0x1:(WRITE) flags 0x0 phys_seg 1 prio class 2" >> "$RUN_DIR/kmsg"
            ( _g=0; until [ "$(cat "$RUN_DIR/targets/$1/state" 2>/dev/null)" = failed ] || [ "$_g" -ge 150 ]; do command sleep 0.2; _g=$((_g + 1)); done
              command sleep 0.5; rm -rf "$SYSROOT/sys/block/$1"; trace "GONE $1" ) > /dev/null 2>&1 & ;;
        link_down)   # #2045: the node is still in sysfs, the WRITE failed, and its port says the disk left
            echo "x|3|3" > "$RUN_DIR/targets/$1/seen"
            { echo "[  200.000001] ata3: SATA link down (SStatus 0 SControl 300)"
              echo "[  212.000001] I/O error, dev $1, sector 4096 op 0x1:(WRITE) flags 0x0 phys_seg 1 prio class 2"; } >> "$RUN_DIR/kmsg" ;;
        # the kernel says nothing this watch reads: chunkcast's verdict is all there is, in its own words (chunkcast.c)
        flush)       _fake_fail "/dev/${1}3" ;;
        write)       _fake_fail "/dev/${1}3" "write error at chunk 2: Input/output error" ;;
        hung)        _fake_fail "/dev/${1}3" "the disk has not taken a write or a flush for too long -- it is not waited for" ;;
        quiet)       : ;;                       # nothing at all: a drawer that is only slow
    esac
}
# STATE_UNREAD=cp|awk: chunkcast's state is there and the watch cannot read it -- its copy fails, or the count of what is durable does
cp() { case "${STATE_UNREAD:-}:$2" in cp:*dmesg.chunkwatch.state) return 1 ;; esac; command cp "$@"; }
awk() { case "${STATE_UNREAD:-}:$*" in awk:*'had[$1]'*) return 2 ;; esac; command awk "$@"; }
curl() { trace "curl $*"; printf 'frames'; }   # the fetch's ONE curl: what it prints is what apply reads
chunkcast() {
    trace "chunkcast $*"
    case "$1" in
        recv)   # says it listens, holds RECV_RANGES (default: every chunk) on every node it was given, ends the pass
            [ -z "${RECV_MUTE:-}" ] || return 2
            # #3181: a drawer back inside the pass listens beside the round's receiver (drawerfill.sh), with a state of its own.
            # SIDE_STATE="<count> <ranges>" = what it holds when the pass ends for it, SIDE_S seconds after it began to listen
            # shellcheck disable=SC2086
            case " $* " in
                *"/back."*)
                    echo listening; [ -z "${RECV_WAIT_SIDE:-}" ] || : > "$RUN_DIR/side.listening"
                    command sleep "${SIDE_S:-2}"; _fake_state ${SIDE_STATE:-5 0-4} "$@"; trace "SIDE-END"; echo "pass ended"; return 0 ;;
            esac
            echo listening
            [ -z "${RECV_RC:-}" ] || return "$RECV_RC"   # RECV_RC=<n>: it listened, and then ended with that exit code (#3259)
            # RECV_DIES=<disk>:<how> (pass 1 only): a pass of seconds, and that drawer dies two seconds into it
            _fc_died=""
            case "${RECV_DIES:+ $* }" in *" --pass 1 "*)
                # the first chunks come two seconds into the pass -- or at once (RECV_EARLY): before the agent's loop has begun to watch it
                _fc_died=1; [ -n "${RECV_EARLY:-}" ] || command sleep 2; _fake_state 2 "0-1" "$@"; command sleep 2
                date +%s > "$RUN_DIR/died_at"; trace "DIED"; _dies "${RECV_DIES%%:*}" "${RECV_DIES#*:}"
                command sleep 2   # RECV_DIES2=<disk>:<how>: two seconds later another drawer dies too
                [ -z "${RECV_DIES2:-}" ] || { date +%s > "$RUN_DIR/died2_at"; trace "DIED2"; _dies "${RECV_DIES2%%:*}" "${RECV_DIES2#*:}"; }
                command sleep 2
                [ -z "${RECV_BACK:-}" ] || { mkdir -p "$SYSROOT/sys/block/${RECV_DIES%%:*}/device"; echo 1000 > "$SYSROOT/sys/block/${RECV_DIES%%:*}/size"; : > "$SYSROOT/sys/block/${RECV_DIES%%:*}/answers"; trace "BACK"; }
                # RECV_BACK_AS=<name>: it comes back under another name -- with its own serial, or RECV_BACK_SERIAL's (another disk)
                [ -z "${RECV_BACK_AS:-}" ] || _back "${RECV_DIES%%:*}" "$RECV_BACK_AS" "${RECV_BACK_SERIAL:-SER-${RECV_DIES%%:*}}"
                [ -z "${RECV_WAIT_SIDE:-}" ] || _await_file side.listening || return 2
                command sleep "${RECV_TAIL:-1}"; trace "PASS-END"   # RECV_TAIL: the pass goes on that long after the drawer came back
                # RECV_BACK_LATE=<seconds>:<name>[:<serial>]: it comes back that long AFTER the pass ended (#3140: the machine is waiting for it)
                [ -z "${RECV_BACK_LATE:-}" ] || ( command sleep "${RECV_BACK_LATE%%:*}"; _bl="${RECV_BACK_LATE#*:}"; _bs="SER-${RECV_DIES%%:*}"
                    case "$_bl" in *:*) _bs="${_bl#*:}"; _bl="${_bl%%:*}" ;; esac; _back "${RECV_DIES%%:*}" "$_bl" "$_bs" ) > /dev/null 2>&1 &
                # CLOSE_LATE=<seconds>: the server closes the round that long after the pass ended
                [ -z "${CLOSE_LATE:-}" ] || ( command sleep "$CLOSE_LATE"; : > "$RUN_DIR/closed" ) > /dev/null 2>&1 & ;;
            esac
            [ -z "${RECV_DIES:-}" ] || [ -n "$_fc_died" ] || { RECV_COUNT=5; RECV_RANGES=0-4; }   # a later pass of that round brings the rest
            # RECV2_QUIET (the repair pass): one more chunk on the nodes it was given, then six seconds of nothing
            case "${RECV2_QUIET:+ $* }" in *" --pass 2 "*)
                command sleep 2; _fake_state 3 "0-2" "$@"; trace "QUIET2"; command sleep 6; trace "PASS2-END" ;;
            esac
            _fake_state "${RECV_COUNT:-5}" "${RECV_RANGES:-0-4}" "$@"
            # the pass's last word of a drawer that left: chunkcast's flush failed (unless it never had anything due to it)
            [ -z "$_fc_died" ] || [ -n "${RECV_UNNOTICED:-}" ] || [ "${RECV_DIES#*:}" = quiet ] || _fake_fail "/dev/${RECV_DIES%%:*}3"
            # RECV_PARTIAL: the drawer that left holds what it had when it left (two chunks), the other one the whole pass
            [ -z "$_fc_died" ] || [ -z "${RECV_PARTIAL:-}" ]                 || sed -i "s|^/dev/${RECV_DIES%%:*}3 .*|/dev/${RECV_DIES%%:*}3 failed 2 0-1 the flush to the disk failed|" "$CHUNK_STATE"
            # RECV_HELD=<word>: it wrote that verdict and is held by a disk -- the process does not end
            if [ -n "${RECV_HELD:-}" ]; then echo "pass $RECV_HELD"; exec sleep 30; fi
            # RECV_SKIPPED="<node>:<n> ...": what chunkcast says each drive was too slow to take (#3141/#3170), before its verdict
            for _sk in ${RECV_SKIPPED:-}; do echo "skipped ${_sk%:*} ${_sk##*:}"; done
            # RECV_STRAY="<n> ...": the `stray <n>` lines of a receiver that was given --from (#1079): once listening, again as it grew, at the end
            for _st in ${RECV_STRAY:-}; do echo "stray $_st"; done
            echo "pass complete" ;;
        apply)  # reads its stream, and what it was asked becomes durable in two flushes, a second apart
            cat > "$RUN_DIR/apply.stdin"
            # APPLY_TAKEN=<n>: a slow drive (#3149). It takes a chunk every 0.7 s -- the count in its own file, next to the state's -- and the first flush is back only after the n-th
            for _t in $(seq 1 "${APPLY_TAKEN:-0}"); do echo "$_t" > "$CHUNK_STATE.taken.new"; mv "$CHUNK_STATE.taken.new" "$CHUNK_STATE.taken"; command sleep 0.7; done
            command sleep 1.5; echo FLUSH >> "$RUN_DIR/order"; _fake_state 3 "0-2" "$@"
            if [ -z "${APPLY_SHORT:-}" ]; then   # APPLY_SHORT: it stops halfway; APPLY_SLOW: a long download
                if command -v harness_apply_wait > /dev/null; then harness_apply_wait || return 2
                else command sleep "${APPLY_SLOW:-1.5}"; fi
                _fake_state 5 "0-4" "$@"
            fi
            trace "APPLY-END"
            echo "pass applied" ;;
    esac
}
mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb" "$RUN_DIR/chunk"
echo writing > "$RUN_DIR/targets/sda/state"; echo writing > "$RUN_DIR/targets/sdb/state"
# PRE_FAILED=<disk>: that drawer failed before the pass (the write check, #3140) -- the pass has the other one alone
[ -z "${PRE_FAILED:-}" ] || echo failed > "$RUN_DIR/targets/$PRE_FAILED/state"
_p=0; for _d in sda sdb; do   # as target_init and target_write_started left them (progress.sh, failmark.sh, expand.sh), before any stream
    _p=$((_p + 1)); echo "SER-$_d" > "$RUN_DIR/targets/$_d/write_started"; echo 0 > "$RUN_DIR/targets/$_d/counter.index"
    echo 1000 > "$SYSROOT/sys/block/$_d/size"; echo "$_p" > "$SYSROOT/sys/block/$_d/port"; echo "SER-$_d|1000|$_p" > "$RUN_DIR/targets/$_d/bound"
    echo "$_d|SER-$_d" >> "$RUN_DIR/chunk/present.$CHUNK_SESSION"   # the drawers there when the round began (drawerback.sh)
done
if [ -n "${SECOND_PART:-}" ]; then   # a later partition after it: p4, which the server waits for the machine to settle on
    printf '%s
' "3|g|windows|ntfs|2048|20971520|p3.windows.pcl.zst|sha|0|ug|uuid|" "4|g|data|ntfs|4096|20971520|p4.data.pcl.zst|sha|0|ug|uuid4|" > "$RUN_DIR/restore.plan"
    echo '{"partitions": [{"index": 3, "index_xxh64": "abc", "chunk_b3": "'"$B3_HASH"'"}, {"index": 4, "index_xxh64": "abc", "chunk_b3": "'"$B3_HASH"'"}]}' > "$RUN_DIR/manifest.json"
fi
[ -s "$RUN_DIR/restore.plan" ] || echo "3|g|windows|ntfs|2048|20971520|p3.windows.pcl.zst|sha|0|ug|uuid|" > "$RUN_DIR/restore.plan"
_back() {   # $1 = the disk that left, $2 = the name it comes back under, $3 = its serial: the kernel finds it by itself (metal 13/11)
    mkdir -p "$SYSROOT/sys/block/$2/device"; echo "$3" > "$SYSROOT/sys/block/$2/device/serial"
    echo 1000 > "$SYSROOT/sys/block/$2/size"; echo 3 > "$SYSROOT/sys/block/$2/port"; : > "$SYSROOT/sys/block/$2/answers"; trace "BACK $2"
    [ -z "${PLUG_EVENT:-}" ] || : > "$RUN_DIR/chunk/plug"   # PLUG_EVENT: the kernel says so (imagesrv-uevent touches this file, drawerplug.sh)
}
if [ -n "${NET_TICK:-}" ]; then   # the kiosk's loop, once a second: the real stream_beep_tick over this run's files
    echo writing > "$RUN_DIR/state"; export GUI_DIR="$RUN_DIR/gui"; mkdir -p "$GUI_DIR"
    ( while [ ! -e "$RUN_DIR/ticker.stop" ]; do stream_beep_tick; command sleep 1; done ) &
fi
_disks="sda sdb"
[ -z "${HARNESS_EXTRA:-}" ] || . "$HARNESS_EXTRA"   # a test's own fakes, over the ones above (test_prodline_newdisk_3181.py)
if [ -n "${BACK_ONLY:-}" ]; then   # drawerback.sh alone, between partitions: sdb was pulled, and is back as BACK_ONLY
    echo failed > "$RUN_DIR/targets/sdb/state"; : > "$RUN_DIR/targets/sdb/pulled"; rm -rf "$SYSROOT/sys/block/sdb"
    echo "${BACK_DONE:-0}" > "$RUN_DIR/targets/sdb/counter.index"   # how many partitions it ended before it left
    _back sdb "$BACK_ONLY" SER-sdb
    for _step in $BACK_STEPS; do   # mode:index, one after another -- the completion each one starts is let end before the next
        # shellcheck disable=SC2086
        drawer_back "${_step%%:*}" "${_step#*:}" http://srv img_1 $_disks; echo "back ${_step} rc=$? disks=$_disks" >> "$RUN_DIR/trace"
        while [ -f "$RUN_DIR/targets/$BACK_ONLY/catchup" ] && read -r _bp _ < "$RUN_DIR/targets/$BACK_ONLY/catchup" && kill -0 "$_bp" 2>/dev/null; do command sleep 0.2; done
    done
else
    chunk_partition_drawers http://srv img_1 3 ntfs p3.windows.pcl.zst "$RUN_DIR/manifest.json" sda sdb; _rc=$?
    echo "PART-RETURNED rc=$_rc" >> "$RUN_DIR/trace"; date +%s > "$RUN_DIR/returned_at"   # returned_at: how long the machine stayed (#3140)
    if [ -n "${SECOND_PART:-}" ]; then   # what run_restore_drawers does next: drawerback.sh before p4, and p4 itself
        unset RECV_DIES RECV_PARTIAL RECV_BACK_AS
        # shellcheck disable=SC2086
        drawer_back before 4 http://srv img_1 $_disks
        # shellcheck disable=SC2086
        chunk_partition_drawers http://srv img_1 4 ntfs p4.data.pcl.zst "$RUN_DIR/manifest.json" $_disks; echo "PART4-RETURNED rc=$?" >> "$RUN_DIR/trace"
    fi
    # what run_restore_drawers does after the last partition: it waits for a drawer completing in the background (drawerback.sh)
    ! command -v drawer_back > /dev/null || drawer_back all "" http://srv img_1 $_disks
    # and after the round's own end (imagesrv-agent): a production line's disks that joined mid-round end here (prodline.sh)
    ! command -v prodline_finish > /dev/null || { prodline_finish ses_1 img_1 http://srv; echo "finish=$? disks=$_disks all=${_all:-}" > "$RUN_DIR/final"; }
    echo "rc=$_rc" >> "$RUN_DIR/trace"
fi
: > "$RUN_DIR/ticker.stop"
kill $(cat "$RUN_DIR/hung.pids" 2>/dev/null) 2>/dev/null   # a wipe "held by a disk" is this test's own sleep
kill $(jobs -p) 2>/dev/null   # a receiver "held by a disk" is this test's own process
'''


def _run(tmp_path, answers: list[dict], env: str = "", *, part: dict | None = None, b3: bytes = B3_LIST) -> dict:
    """`part`: the manifest's partition 3 (default: its index and its BLAKE3 list); `b3`: the list the server gives."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "idx").write_text(INDEX, encoding="ascii", newline="\n")
    (tmp_path / "b3").write_bytes(b3)
    part = {"index": 3, "index_xxh64": "abc", "chunk_b3": B3_HASH} if part is None else part
    (tmp_path / "manifest.json").write_text(json.dumps({"partitions": [part]}))
    for n, answer in enumerate(answers, start=1):
        (tmp_path / f"answer.{n}.json").write_text(json.dumps(answer))
    (tmp_path / "answer.last.json").write_text(json.dumps(answers[-1]))
    (tmp_path / "harness.sh").write_text(HARNESS, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, "-c", f'B3_HASH={B3_HASH} {env} . {posix(tmp_path / "harness.sh")!r} {posix(tmp_path)!r}'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    trace = (tmp_path / "trace").read_text(encoding="utf-8").splitlines() if (tmp_path / "trace").exists() else []
    bodies = [json.loads(p.read_text()) for p in sorted(tmp_path.glob("body.*.json"),
                                                         key=lambda p: int(p.name.split(".")[1]))]
    return {"trace": trace, "bodies": bodies, "out": proc.stdout + proc.stderr,
            "log": (tmp_path / "agent.log").read_text(encoding="utf-8") if (tmp_path / "agent.log").exists() else ""}


def test_a_whole_partition_is_reported_and_each_drawer_gets_its_ntfs_backup_boot_before_it_is_done(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE])
    trace = got["trace"]
    assert trace[-1] == "rc=0", got
    for disk in ("sda", "sdb"):
        assert trace.index(f"ntfs_boot {disk} 3 ntfs") < trace.index(f"DONE {disk}"), trace    # review 2 (#1487)
    assert [b["state"] for b in got["bodies"]] == ["listening", "ended"]
    assert got["bodies"][1]["targets"][0] == {"serial": "SER-sda", "write_mbps": 150, "failed": False,
                                              "durable": [{"part": 3, "ranges": [[0, 4]]}]}
    for disk in ("sda", "sdb"):          # the ledger file stage 2's progress report reads (format-spec 9.1)
        assert (tmp_path / "targets" / disk / "ledger" / "durable.3").read_text() == "0-4\n"


def test_a_drawer_whose_ntfs_backup_boot_was_not_written_fails_and_is_not_done(tmp_path):
    trace = _run(tmp_path, [RECEIVE1, DONE], env="NTFS_BAD=sdb")["trace"]
    assert "DONE sda" in trace and "DONE sdb" not in trace
    assert "FAIL sdb NTFS backup boot sector of sdb was not written" in trace
    assert trace[-1] == "rc=0"                                               # its neighbour is whole


def test_a_receiver_that_never_said_it_listens_is_not_reported_as_listening(tmp_path):
    """סקירה 9: המקלט מת לפני שהאזין. השרת אינו שומע `listening` ואינו משדר בשבילו."""
    got = _run(tmp_path, [RECEIVE1, DONE], env="RECV_MUTE=1")
    assert [b["state"] for b in got["bodies"]] == [], got["bodies"]
    assert "never said it listens" in got["log"]
    assert any(line.startswith("FAIL sda") and "rc=2" in line for line in got["trace"])
    assert got["trace"][-1] == "rc=1"


def test_a_whole_machine_waits_for_the_others_as_long_as_it_takes(tmp_path):
    """סקירה 1: התקרה כאן היא 3 שניות של `wait`. מכונה שכל המגירות שלה שלמות מקבלת
    `whole` -- ההמתנה היא למכונות אחרות -- ואינה נכשלת גם אחרי 20 תשובות כאלה."""
    waiting = {"ok": True, "action": "wait", "poll": 2, "whole": True}
    trace = _run(tmp_path, [RECEIVE1, *[waiting] * 20, DONE])["trace"]
    assert "DONE sda" in trace and "DONE sdb" in trace and not any(t.startswith("FAIL") for t in trace), trace
    assert trace[-1] == "rc=0"


def test_a_wait_that_is_not_for_others_still_has_its_ceiling(tmp_path):
    """הצד השני של אותו כלל: `wait` בלי `whole` אינו אינסופי, והמגירות נכשלות בשמן."""
    trace = _run(tmp_path, [RECEIVE1, {"ok": True, "action": "wait", "poll": 2}])["trace"]
    assert any(t.startswith("FAIL sda") for t in trace) and any(t.startswith("FAIL sdb") for t in trace), trace
    assert "DONE sda" not in trace and trace[-1] == "rc=1"


def test_a_disk_that_is_no_longer_the_one_chosen_is_not_handed_to_the_next_pass(tmp_path):
    """סקירה 3: המגירה נשלפה ודיסק אחר הוכנס בין שני מעברים. `assert_target` נשאל לפני
    כל מעבר, והדיסק הזר אינו מגיע למקלט של המעבר השני."""
    again = {"ok": True, "action": "receive", "pass": 2}
    trace = _run(tmp_path, [RECEIVE1, again, again, DONE], env="SWAPPED=sdb:3")["trace"]
    passes = [t for t in trace if t.startswith("chunkcast recv")]
    assert len(passes) == 2, trace
    assert "/dev/sda3" in passes[0] and "/dev/sdb3" in passes[0]
    assert "/dev/sda3" in passes[1] and "/dev/sdb3" not in passes[1]
    assert "DONE sda" in trace and "DONE sdb" not in trace
    assert trace.count("cancel_pending") >= 2                                # #1386: carried out between passes


def test_a_server_that_lost_its_place_is_sent_everything_again_at_once(tmp_path):
    """הדלתא (13/11): השרת עונה `durable_full` כשאינו יכול למקם את הדיווח. הסוכן שוכח מה שחשב שאושר,
    לוקח את המספר של השרת, ושולח הכול מחדש מיד -- בלי לחכות לסיבוב הבא, ובלי לספור זאת מול התקרה."""
    lost = {"ok": True, "action": "wait", "durable_seq": 7, "durable_full": True}
    got = _run(tmp_path, [{**RECEIVE1, "durable_seq": 1}, lost, {**DONE, "durable_seq": 8}])
    assert got["trace"][-1] == "rc=0", got
    bodies = got["bodies"]
    assert [(b["state"], b["durable_seq"]) for b in bodies] == [("listening", 0), ("ended", 1), ("ended", 7)]
    assert all(t["durable"] == [{"part": 3, "ranges": [[0, 4]]}] for b in bodies[1:] for t in b["targets"])
    assert (tmp_path / "targets" / "sda" / "ledger" / "acked.pass.3").read_text() == "0-4\n"


def test_a_ledger_longer_than_a_report_goes_in_pieces_and_the_answer_is_the_last_pieces(tmp_path):
    """מקום לטווח אחד בהודעה, ושתי מגירות: שתי הודעות `ended`, הראשונה אומרת `durable_more`. התשובה
    `wait` לחתיכה אינה תשובה להמתין לה -- החתיכה הבאה יוצאת מיד, ורק התשובה לאחרונה מכריעה."""
    piece = {"ok": True, "action": "wait", "durable_seq": 2}
    got = _run(tmp_path, [{**RECEIVE1, "durable_seq": 1}, piece, {**DONE, "durable_seq": 3}],
               env="LEDGER_PIECE_RANGES=1")
    assert got["trace"][-1] == "rc=0" and "DONE sda" in got["trace"] and "DONE sdb" in got["trace"], got
    first, last = got["bodies"][1:]
    assert len(got["bodies"]) == 3 and first["durable_more"] is True and "durable_more" not in last
    assert [("durable" in t) for t in first["targets"]] == [True, False]
    assert [("durable" in t) for t in last["targets"]] == [False, True]
    assert (first["durable_seq"], last["durable_seq"]) == (1, 2)


def test_a_drawer_without_a_serial_has_no_ledger_anywhere(tmp_path):
    """בלי מספר סידורי אין לדיסק זהות ביומן (התכנון, 2.3). הוא אינו נכנס להודעת הבקרה, וגם קובץ היומן
    של דיווח ההתקדמות אינו נכתב לו: השרת מסרב לדיסק כזה סירוב קבוע בשמו, ואין טעם לשלוח את הטווחים שלו."""
    got = _run(tmp_path, [RECEIVE1, DONE], env="NOSERIAL=sdb")
    assert [t["serial"] for t in got["bodies"][1]["targets"]] == ["SER-sda"]
    assert (tmp_path / "targets" / "sda" / "ledger" / "durable.3").read_text() == "0-4" + chr(10)
    assert not (tmp_path / "targets" / "sdb" / "ledger" / "durable.3").exists()


# --- the adversarial review of #3063 (13/11) -------------------------------------------


FETCH = {"ok": True, "action": "fetch", "pass": 2, "ranges": [[1, 1], [3, 4]]}


def test_a_fetch_is_one_curl_and_one_apply_and_the_server_hears_it_advance(tmp_path):
    """סקירה, ממצאים 1 ו-3. שני טווחים חסרים: `curl` אחד שמקבל את שניהם כקובץ הגדרות (חיבור אחד),
    ו-`chunkcast apply` אחד שמקבל את הרשימה. תוך כדי ההורדה השרת שומע `fetching` עם מה שנעשה עמיד,
    וה-watchdog מוזן; בסוף המגירות שלמות."""
    got = _run(tmp_path, [RECEIVE1, FETCH, DONE],
               env="RECV_COUNT=2 RECV_RANGES=0-0,2-2 REAL_SLEEP=1 TEST_START_CEILING=30")
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace and "DONE sdb" in trace, got
    curls = [t for t in trace if t.startswith("curl ")]
    applies = [t for t in trace if t.startswith("chunkcast apply")]
    assert len(curls) == 1 and len(applies) == 1, trace
    assert "--fail-early" in curls[0] and "-K " in curls[0]
    assert "--chunks-file" in applies[0] and "/dev/sda3" in applies[0] and "/dev/sdb3" in applies[0]
    assert (tmp_path / "chunk" / "fetch.ranges").read_text().strip() == "1-1,3-4"
    config = (tmp_path / "chunk" / "fetch.curl").read_text()
    # chunk k is bytes [200k, 200k + 200) of the file: one transfer a range, a "next" between them
    assert [line for line in config.splitlines() if line.startswith(("url", "range", "next"))] == [
        'url = "http://srv/api/v1/images/img_1/files/p3.windows.pcl.zst"', 'range = "200-399"', "next",
        'url = "http://srv/api/v1/images/img_1/files/p3.windows.pcl.zst"', 'range = "600-999"']
    assert config.count("fail") == 2 and config.count("speed-time = 1") == 2     # each transfer carries its own ceiling
    assert (tmp_path / "apply.stdin").read_text() == "frames"
    told = [json.loads(p.read_text()) for p in sorted(tmp_path.glob("fetching.*.json"))]
    assert told and all(b["state"] == "fetching" and b["pass"] == 2 for b in told), told
    carried = [r for b in told for t in b["targets"] for d in t.get("durable", []) for r in d["ranges"]]
    assert carried, "the fetching reports carried nothing of what became durable"
    assert trace.count("beat") >= 3
    assert "chunk fetch: 2 ranges: curl rc=0, apply rc=0" in got["log"]
    assert [b["state"] for b in got["bodies"]] == ["listening", "ended", "ended"]


def test_the_end_of_a_pass_tells_the_server_what_each_drive_was_too_slow_to_take(tmp_path):
    """#3141/#3170: חוסר אחרי המעבר הראשון הוא אחד משניים -- חבילות שלא הגיעו (רשת), או חתיכות שהגיעו והדיסק לא
    הספיק לכתוב (`skipped`). רק הסוכן יודע איזה, ועד כאן זה נשאר ביומן שלו, והשרת קרא לניתוק של 4 דקות "לא עמד בקצב".
    `ended` של מעבר נושא את המספר של **אותו** מעבר לכל מגירה; `listening` לא, וגם לא `ended` של הורדה."""
    got = _run(tmp_path, [RECEIVE1, FETCH, DONE],
               env="RECV_COUNT=2 RECV_RANGES=0-0,2-2 RECV_SKIPPED='/dev/sda3:7 /dev/sdb3:0' REAL_SLEEP=1 TEST_START_CEILING=30")
    assert got["trace"][-1] == "rc=0", got
    assert [b["state"] for b in got["bodies"]] == ["listening", "ended", "ended"]
    said = [{t["serial"]: t.get("skipped") for t in b["targets"]} for b in got["bodies"]]
    assert said[1] == {"SER-sda": 7, "SER-sdb": 0}, said         # 0 is said: "nothing skipped" is evidence too
    assert said[0] == {"SER-sda": None, "SER-sdb": None}, said   # listening: nothing has been taken yet
    assert said[2] == {"SER-sda": None, "SER-sdb": None}, said   # after the fetch: the receive pass's count is not this one's


def test_a_receiver_that_said_nothing_of_what_it_skipped_is_not_reported_as_zero(tmp_path):
    """השומר: chunkcast שלא אמר (גרסה ישנה, או פלט שלא נקרא) אינו "0 חתיכות דולגו" -- השדה חסר, והשרת אינו מסיק ממנו דבר."""
    got = _run(tmp_path, [RECEIVE1, DONE])
    assert all("skipped" not in t for b in got["bodies"] for t in b["targets"]), got["bodies"]


def test_a_fetch_tells_the_server_how_many_chunks_are_durable_and_the_count_grows(tmp_path):
    """#3112 (מעבדת ה-VM 13/11: הורדה של 2,751 טווחים, והשרת סגר את הגל כ"שתק" אחרי 6.7 דקות). כל דיווח
    `fetching` נושא את המספר שהסוכן קרא בקובץ המצב של `apply` -- חתיכות עמידות על המגירות -- והוא גדל (שתי
    מגירות: 2 כל אחת לפני ההורדה, 3 ואז 5 ב-apply). ושורה ביומן לכל דיווח: מה נאמר לשרת."""
    got = _run(tmp_path, [RECEIVE1, FETCH, DONE],
               env="RECV_COUNT=2 RECV_RANGES=0-0,2-2 REAL_SLEEP=1 TEST_START_CEILING=30")
    told = [json.loads(p.read_text()) for p in sorted(tmp_path.glob("fetching.*.json"), key=lambda p: int(p.name.split(".")[1]))]
    counts = [b.get("fetched") for b in told]
    assert counts and all(isinstance(c, int) for c in counts), told
    assert counts == sorted(counts) and counts[0] < counts[-1] <= 10, counts      # 2+2 before it, 3+3 then 5+5 by apply
    assert "chunks durable on the drawers -- told to the server" in got["log"], got["log"]
    assert all("fetched" not in b for b in got["bodies"]), got["bodies"]          # only a fetch's reports carry it


def _told(tmp_path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(tmp_path.glob("fetching.*.json"), key=lambda p: int(p.name.split(".")[1]))]


def test_a_fetch_on_a_slow_drive_is_told_alive_before_its_first_flush_is_durable(tmp_path):
    """#3149 (מעבדת ה-VM 14/11, room_a6cf9813): `apply` לקח 2,569 חתיכות מהזרם בתוך 342 שניות, אבל בקובץ המצב שלו היו רק
    ארבע כתיבות (ה-flush של דיסק thin איטי) -- והדיווח הראשון יצא אחרי 340 שניות, התקרה של השרת 330. כאן הזמנים הם של הייצור
    (`CHUNK_FETCH_TELL_S` 20): ה-flush הראשון חוזר אחרי 4.3 שניות, ובקובץ המצב דבר לא זז עד אז. ההורדה אומרת מה היא רואה --
    החתיכות שנלקחו -- לפניו, ולא מחכה להפסקה בין שני דיווחים."""
    got = _run(tmp_path, [RECEIVE1, FETCH, DONE],
               env="RECV_COUNT=2 RECV_RANGES=0-0,2-2 REAL_SLEEP=1 TEST_START_CEILING=30 TEST_TELL_S=20 APPLY_TAKEN=4")
    assert got["trace"][-1] == "rc=0", got
    order = (tmp_path / "order").read_text().split()
    assert "FLUSH" in order and order[0] == "TOLD", ("the first word came after the first flush, or never", order)
    first = _told(tmp_path)[0]
    assert isinstance(first.get("received"), int) and first["received"] >= 1, first
    assert isinstance(first.get("fetched"), int) and first["fetched"] == 4, first      # 2 + 2 durable before the fetch began: nothing new, and said so
    assert "taken from the stream)" in got["log"] and "(? taken" not in got["log"], got["log"]


def test_chunks_taken_while_no_flush_is_back_keep_the_agents_own_ceiling_from_closing_the_fetch(tmp_path):
    """הצד של הסוכן באותו באג: `_chunk_run` מכשיל הורדה שקובץ המצב שלה לא זז `STREAM_START_CEILING + WAIT_STREAM_STALL_S +
    WAIT_DRAWER_S` שניות. הורדה שלוקחת חתיכות ואין flush שחזר אינה "לא התקדמה". כאן התקרות 3 שניות (1+1+1) וההורדה לוקחת
    חתיכה כל 0.7 שניות במשך 5.6, ואין flush עד אז: היא לא נהרגת."""
    got = _run(tmp_path, [RECEIVE1, FETCH, DONE], env="RECV_COUNT=2 RECV_RANGES=0-0,2-2 REAL_SLEEP=1 TEST_START_CEILING=1 APPLY_TAKEN=8")
    assert "לא הסתיימה" not in got["log"] and "פג הזמן" not in got["log"], got["log"]
    assert got["trace"][-1] == "rc=0" and "DONE sda" in got["trace"] and "DONE sdb" in got["trace"], got


def test_a_fetch_with_nothing_taken_says_nothing_of_it(tmp_path):
    """שום מספר לא נקרא = שום מספר לא נשלח (עיקרון 5): בלי `.taken` -- סוכן `chunkcast` ישן, או קובץ שלא נכתב -- הדיווח אינו נושא
    `received`, ולעולם לא 0 שהשרת יקרא "לא לקח דבר"."""
    got = _run(tmp_path, [RECEIVE1, FETCH, DONE], env="RECV_COUNT=2 RECV_RANGES=0-0,2-2 REAL_SLEEP=1 TEST_START_CEILING=30")
    told = _told(tmp_path)
    assert told and all("received" not in b for b in told), told
    assert got["trace"][-1] == "rc=0"


def test_every_answered_question_feeds_the_watchdog(tmp_path):
    """סקירה, ממצא 2: לולאת השאלה לא הזינה את ה-watchdog, ומכונה בריאה שממתינה לאחרות אותחלה אחרי 600
    שניות. כל שאלה שנענתה היא ראיה שהלולאה והשרת חיים."""
    waiting = {"ok": True, "action": "wait", "poll": 2, "whole": True}
    long = _run(tmp_path, [RECEIVE1, *[waiting] * 20, DONE])["trace"]
    assert long[-1] == "rc=0", long
    # Not "more beats than a quiet run": the beats of the loops that watch a live receiver follow the clock (14 in a
    # quiet run on a loaded runner, 22 in the long one -- public gate 20/11), and the answers' are what is asked.
    # Each answered question of the loop is followed, before the next one, by its own beat (#3943).
    posts = [i for i, t in enumerate(long) if t == "POST ended 3"]
    assert len(posts) == 21, (len(posts), long)
    for here, after in zip(posts, [*posts[1:], len(long)]):
        assert "beat" in long[here + 1:after], (here, long[here:after])


def test_a_server_that_stops_answering_is_given_up_on_by_name_before_the_watchdog(tmp_path):
    """הצד השני: שאלה שלא נענתה אינה מזינה את ה-watchdog, ולכן יש לה תקרה משלה (`WAIT_STREAM_STALL_S`,
    כאן שנייה) שנמוכה מה-lease. המגירות נכשלות בשם הסיבה -- לא `reboot -f` למכונה בלי הסבר."""
    got = _run(tmp_path, [RECEIVE1, DONE], env="SERVER_GONE_AFTER=1")
    trace = got["trace"]
    assert trace[-1] == "rc=1", got
    assert any(t.startswith("FAIL sda") and "השרת לא ענה במשך" in t for t in trace), trace
    after = trace[trace.index("post-failed"):]
    assert "beat" not in after, "the watchdog was fed while the server was silent"
    assert after.count("post-failed") <= 4                                  # a ceiling of seconds, not of 900


def test_a_receiver_held_by_a_disk_returns_the_verdict_it_wrote_not_always_success(tmp_path):
    """סקירה, ממצא 9: המקלט כתב את הפסק שלו ונתקע על דיסק. הלולאה ממשיכה בלעדיו -- עם הפסק שנכתב.
    עד כאן היא החזירה 0 תמיד, ו"הזרם לא הגיע" נקרא כמעבר שהסתיים."""
    trace = _run(tmp_path, [RECEIVE1, DONE], env="RECV_HELD=no-stream RECV_COUNT=0 RECV_RANGES=- TEST_START_CEILING=30")["trace"]
    assert any(t.startswith("FAIL sda") and "הזרם לא הגיע" in t for t in trace), trace
    assert "DONE sda" not in trace and trace[-1] == "rc=1"
    held = _run(tmp_path / "ok", [RECEIVE1, DONE], env="RECV_HELD=complete TEST_START_CEILING=30")["trace"]
    assert "DONE sda" in held and held[-1] == "rc=0"                        # a verdict of success is still one

