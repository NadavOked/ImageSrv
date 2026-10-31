# drawerfeed.sh -- how the stream reaches each drawer's partclone.
# POSIX sh (busybox ash). Loaded with drawers.sh.
#
# Two shapes, one switch (IMAGESRV_DRAWER_DECODE=auto|shared|each; #1746):
#   shared            source | fanout -S -P > mid ; zstd -dc < mid | fanout -U > feed1..N ; partclone < feedN
#   each              source | fanout -S -P > feed1..N ; zstd -dc < feedN | partclone      (a zstd per drawer)
# On the cloner (HP 8100, i5-650: 2 cores + HT) a zstd per drawer was, after
# partclone, the drawers' biggest CPU bill: 0.68 of a logical CPU each at
# 75MB/s (17/10, cloner-pipeline-perf.md 1.1), and two drawers ran at 264-370m
# against 450m for one (18/10). Every drawer decodes the very same bytes, so
# `shared` decodes them once. Isolation does not move: the second fanout gives
# every drawer its own ring and drops only the one whose reader went
# (#1598/#1603), and each drawer still leaves the same three records verdict.sh
# reads -- its fanout report line, zstd.rc, pipeline.rc.
#
# A lab experiment changes a running agent between partitions through
# $RUN_DIR/tune (KEY=value lines, read, never sourced), which wins over the
# environment. Anything that is not a known value is refused out loud.

_tune() {   # $1 = name, $2 = the environment's value
    _tn=$(sed -n "s/^$1=//p" "$RUN_DIR/tune" 2>/dev/null | tail -n 1)
    echo "${_tn:-$2}"
}

drawer_tune() {
    # $1 = partition index (for the log line), $2.. = disks. Sets DRAWER_DECODE
    # (shared|each), DRAWER_NICE ("nice -n N" for the decoding/writing side, or
    # empty) and DRAWER_DIRECT (partclone's --write-direct-io, or empty). Not
    # called in $( ): log() also writes stdout.
    # auto = shared from two live drawers. With one, the second fanout is pure
    # cost: +13% wall, +22% CPU (Testrunner i7-10700 on 2 logical CPUs, 1.5GB
    # tar at zstd -3, 18/10) -- against -10% wall / -9% CPU with two drawers
    # and -26% / -24% with three, same bench.
    _dt_i="$1"; shift; _dt_n=0
    for _dt_d in "$@"; do
        case "$(cat "$RUN_DIR/targets/$_dt_d/state" 2>/dev/null)" in failed|cancelled) ;; *) _dt_n=$((_dt_n + 1)) ;; esac
    done
    DRAWER_DECODE=$(_tune IMAGESRV_DRAWER_DECODE "${IMAGESRV_DRAWER_DECODE:-auto}")
    case "$DRAWER_DECODE" in
        shared|each) ;;
        auto) DRAWER_DECODE=each; [ "$_dt_n" -lt 2 ] || DRAWER_DECODE=shared ;;
        *) log "WARNING: IMAGESRV_DRAWER_DECODE=$DRAWER_DECODE is not auto|shared|each -- auto"
           DRAWER_DECODE=each; [ "$_dt_n" -lt 2 ] || DRAWER_DECODE=shared ;;
    esac
    # The receiver is never niced down: what it misses is a re-xmit for the
    # whole room, while the writers have a ring to catch up from (#1571).
    _dn=$(_tune IMAGESRV_DRAWER_NICE "${IMAGESRV_DRAWER_NICE:-0}"); DRAWER_NICE=""
    case "$_dn" in
        0) ;;
        [1-9]|1[0-9]) DRAWER_NICE="nice -n $_dn" ;;
        *) log "WARNING: IMAGESRV_DRAWER_NICE=$_dn is not 0-19 -- not niced" ;;
    esac
    # O_DIRECT: no copy into the page cache and no writeback behind it -- CPU a
    # drawer pays per byte. Off until metal says what it does to a drive's pace
    # (each write then waits for the disk). Debian 13's partclone 0.3.36 has it.
    _dx=$(_tune IMAGESRV_DRAWER_DIRECTIO "${IMAGESRV_DRAWER_DIRECTIO:-0}"); DRAWER_DIRECT=""
    case "$_dx" in
        0) ;;
        1) DRAWER_DIRECT="--write-direct-io" ;;
        *) log "WARNING: IMAGESRV_DRAWER_DIRECTIO=$_dx is not 0|1 -- through the cache" ;;
    esac
    log "partition $_dt_i: decode=$DRAWER_DECODE drawers=$_dt_n${DRAWER_NICE:+ ($DRAWER_NICE)}${DRAWER_DIRECT:+ $DRAWER_DIRECT}"
}

_drawer_pipeline() {
    # $1 = disk, $2 = fs, $3 = partition index, $4 = shared|each. Reads its fifo,
    # writes the disk. reader.pid is whoever reads the fifo -- the one drawer_watch
    # and cancel_drawer kill, so fanout's next write is EPIPE and only this drawer
    # is out (#1598, #1386).
    _tdir="$RUN_DIR/targets/$1"
    _node=$(partition_node "$1" "$3")
    _pcl=$(partclone_for_fs "$2")
    if [ "$4" = shared ]; then
        (
            read -r _dp_me _ < /proc/self/stat   # this shell's own pid (a subshell's: not $$), for the watcher
            rm -f "$_tdir/writeback.failed"; writeback_watch "$_tdir" "$_node" "$_dp_me" > /dev/null 2>&1 & _dp_w=$!   # #1841: EIO mid-partition (writeback.sh)
            # shellcheck disable=SC2016,SC2046,SC2086 # $$ is sh -c's own; nice and partclone_mode flags are word lists
            sh -c 'echo "$$" > "$1"; shift; exec "$@"' partclone "$_tdir/reader.pid" $DRAWER_NICE \
                "$_pcl" $(partclone_mode "$_pcl" -r) $DRAWER_DIRECT -s - -O "$_node" -L "$_tdir/partclone.log" \
                < "$_tdir/feed" 2>> "$LOG_FILE"
            _dp_rc=$?; kill "$_dp_w" 2>/dev/null; wait "$_dp_w" 2>/dev/null
            [ ! -f "$_tdir/writeback.failed" ] || _dp_rc=1   # a refused fsync fails it, whatever partclone said
            echo "$_dp_rc" > "$_tdir/pipeline.rc"
        ) &
        echo "$!" > "$_tdir/pipeline.pid"
        return
    fi
    (
        read -r _dp_me _ < /proc/self/stat   # this shell's own pid (a subshell's: not $$), for the watcher
        rm -f "$_tdir/writeback.failed"; writeback_watch "$_tdir" "$_node" "$_dp_me" > /dev/null 2>&1 & _dp_w=$!   # #1841: EIO mid-partition (writeback.sh)
        # ה-rc של zstd נלכד לחוד, כמו `source.rc` וכמו `capture.sh:177`:
        # ב-busybox ash אין `pipefail`, ו-`$?` של צינור הוא של האחרון בלבד.
        # ‏zstd שנפל באמצע ו-partclone שקיבל EOF ויצא 0 הגיעו לכאן
        # כ-`pipeline.rc=0`, והמגירה נחתמה `done` על כתיבה קצרה (#527).
        #
        # ⚠️ **לא `set -o pipefail`** — הוא אינו קיים ב-busybox ash. הוא
        # היה עובר בטסט (Git Bash) ומשאיר את הבאג על המכונה.
        # shellcheck disable=SC2016,SC2046,SC2086,SC2094 # $$ is sh -c's own; fifo opened once; word lists
        { sh -c 'echo "$$" > "$1"; shift; exec "$@"' zstd "$_tdir/reader.pid" $DRAWER_NICE zstd -dc \
              < "$_tdir/feed" 2>> "$LOG_FILE"
          echo "$?" > "$_tdir/zstd.rc"; } \
            | $DRAWER_NICE "$_pcl" $(partclone_mode "$_pcl" -r) $DRAWER_DIRECT -s - -O "$_node" \
                -L "$_tdir/partclone.log" 2>> "$LOG_FILE"
        _dp_rc=$?; kill "$_dp_w" 2>/dev/null; wait "$_dp_w" 2>/dev/null
        [ ! -f "$_tdir/writeback.failed" ] || _dp_rc=1   # a refused fsync fails it, whatever partclone said
        echo "$_dp_rc" > "$_tdir/pipeline.rc"
    ) &
    echo "$!" > "$_tdir/pipeline.pid"
}

drawer_feed_start() {
    # $1 = shared|each, $2 = ring bytes, $3.. = the drawers' fifos, their pipelines
    # already started. Sets FEED_OUT (what the stream's fanout writes to),
    # FEED_REPORT (where its report goes) and FEED_WATCH (the pid that lives as
    # long as bytes still flow to a drawer).
    _fs_how="$1"; _fs_ring="$2"; shift 2
    FEED_OUT="$*"; FEED_REPORT="$RUN_DIR/fanout.out"; FEED_WATCH=""; FEED_EVICT=""
    [ "$_fs_how" = shared ] || return 0
    rm -f "$RUN_DIR/mid" "$RUN_DIR/mid.bytes" "$RUN_DIR/stream.out" "$RUN_DIR/fanout.out" \
          "$RUN_DIR/zstd.rc" "$RUN_DIR/zstd.pid" "$RUN_DIR/dfanout.rc" "$RUN_DIR/dfanout.pid"
    mkfifo "$RUN_DIR/mid"
    (
        # -U: every drawer's counter stays in compressed bytes, the unit of the
        # image's total and of the server's drive rates (fanout.c, units_now).
        # FANOUT_EXPECTED_BYTES is the compressed stream's length: checked upstream.
        # shellcheck disable=SC2016,SC2086 # $$ is sh -c's own; nice is a word list
        { sh -c 'echo "$$" > "$1"; shift; exec "$@"' zstd "$RUN_DIR/zstd.pid" $DRAWER_NICE zstd -dc \
              < "$RUN_DIR/mid" 2>> "$LOG_FILE"
          echo "$?" > "$RUN_DIR/zstd.rc"; } \
            | { unset FANOUT_EXPECTED_BYTES
                sh -c 'echo "$$" > "$1"; shift; exec "$@"' fanout "$RUN_DIR/dfanout.pid" $DRAWER_NICE \
                    fanout -U "$RUN_DIR/mid.bytes" "$_fs_ring" "$@" > "$RUN_DIR/fanout.out" 2>> "$LOG_FILE"; }
        echo "$?" > "$RUN_DIR/dfanout.rc"
    ) &
    FEED_WATCH=$!
    FEED_OUT="$RUN_DIR/mid"; FEED_REPORT="$RUN_DIR/stream.out"
    # The stream's fanout waits on the decoder, and the decoder on the slowest
    # drawer. A hung drawer is the drawers' fanout's to evict (fanout.c,
    # STALL_EVICT_MS); the stream's waits twice as long, so the two ceilings
    # never race and drop the whole machine for one drawer.
    _fs_e="${FANOUT_STALL_EVICT_MS:-150000}"
    case "$_fs_e" in ''|*[!0-9]*) _fs_e=150000 ;; esac
    FEED_EVICT=$((_fs_e * 2))
}

feed_fanout() {   # the stream's fanout, with the ceiling drawer_feed_start chose
    if [ -n "$FEED_EVICT" ]; then FANOUT_STALL_EVICT_MS="$FEED_EVICT" fanout "$@"; else fanout "$@"; fi
}

drawer_feed_finish() {
    # $1 = partition index, $2.. = disks. shared only, after the stream: wait for
    # the decoder, hand its zstd.rc to every drawer it fed, and fold the two
    # fanouts' exit codes into the one drawers.sh judges by ($_fanout_rc).
    # Sets _dec_why when the decoder failed while a drawer was still being fed.
    _ff_i="$1"; shift
    _ff_max="$WAIT_DRAWER_S"
    if [ "$_fanout_rc" != 0 ]; then
        # A stream fanout that ended badly may never have opened mid, and zstd
        # would wait in open() for good (#49) -- released, and the short ceiling.
        unblock_fifo "$RUN_DIR/mid"
        [ "$_fanout_rc" = 1 ] || _ff_max="$WAIT_HELPER_S"
    fi
    if ! wait_pid "$FEED_WATCH" "$_ff_max" "פענוח הזרם למגירות (מחיצה $_ff_i)"; then
        # No job control in ash: zstd and fanout outlive their subshell. Stopped
        # by pid, every partclone gets EOF and ends -- judged below, not left hanging.
        kill -9 "$(cat "$RUN_DIR/zstd.pid" 2>/dev/null)" "$(cat "$RUN_DIR/dfanout.pid" 2>/dev/null)" 2>/dev/null
        unblock_fifo "$RUN_DIR/mid"
    fi
    _ff_z=$(cat "$RUN_DIR/zstd.rc" 2>/dev/null); _ff_2=$(cat "$RUN_DIR/dfanout.rc" 2>/dev/null)
    for _ff_d in "$@"; do
        [ -z "$_ff_z" ] || echo "$_ff_z" > "$RUN_DIR/targets/$_ff_d/zstd.rc"
    done
    case "$_fanout_rc:$_ff_2" in
        [01]:[01]) [ "$_fanout_rc$_ff_2" = 00 ] || _fanout_rc=1 ;;
        [01]:) _fanout_rc=1 ;;              # stopped at its ceiling: no report, each drawer by name
        [01]:*) _fanout_rc="$_ff_2" ;;      # the drawers' fanout itself fell (2; 137 = OOM, #21)
    esac
    # One decoder fed every drawer. Failing while one still took bytes is the
    # stream's failure for all of them -- not a disk's (#440), never done (#527).
    # No ok line = every drawer had died first, and zstd only got their EPIPE.
    # A fanout that fell or a stream that timed out is judged as that, not here.
    _dec_why=""
    case "$_fanout_rc" in 0|1) ;; *) return 0 ;; esac
    [ "$_ff_z" != 0 ] && grep -q ' ok$' "$RUN_DIR/fanout.out" 2>/dev/null \
        && _dec_why="פענוח הזרם נכשל (zstd rc=${_ff_z:-לא נרשם})"
    return 0
}
