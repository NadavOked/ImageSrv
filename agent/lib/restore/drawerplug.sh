# drawerplug.sh -- a drawer pushed back in is seen when the KERNEL says so, and what the server is told of it (#3181).
# POSIX sh (busybox ash).
#
# Until #3181 a drawer that was pulled and pushed back in was looked for at fixed points only -- after each answer
# of the server inside a partition, before each partition, before the read-back (drawerback.sh). Inside a pass of
# twenty minutes that is twenty minutes: metal 14/11, the kernel found sdd within a second of the push, and no
# line about it was written for 17 minutes. Every second out is chunks the drawer has to be brought later.
#
#   - the kernel's own word: imagesrv-uevent (agent/uevent.c, #1971) touches $CHUNK_DIR/plug on every whole-disk
#     add / remove / change. A listener of the round's own -- the kiosk's file is the kiosk's;
#   - drawer_plug_tick is called once a tick wherever the round waits: inside a pass and a fetch (_chunk_run) and
#     while it asks the server what now (chunkrecv.sh). It costs a file test. On an event it looks -- then, and
#     for DRAWER_PLUG_SETTLE_S after it: a disk just pushed in needs seconds until it answers a read
#     (drawer_answers), and the watch needs a tick to name the pull (`pulled`, verdict.sh);
#   - found = drawerback.sh's own join (drawer_back), by serial and size, now. A drawer back under another name
#     takes its target with it, and the watch of the pass that runs holds the old list: it is stopped before the
#     join and started again with the new one, or it would call the old name pulled a second time;
#   - another disk (another serial) is said once, there too -- at the kernel's word, not at the next fixed point.
#     In a regular round it is not written: no one chose it (#2434). In a production line it is a new target
#     (prodline.sh): seen here, followed every tick until it writes or is out, and listed at drawer_back's point
#     with the pass's watch stopped -- as a drawer back under another name is;
#   - no imagesrv-uevent in the image = said once, and the fixed points are all there is. They stay in any case:
#     an event can be lost, and a drawer whose link came back with no uevent is found there.
#
# What the server is told (interfaces.md section 4 and the chunk-pass message):
#
#   "pulled": true     on a target that is out because it was PULLED and that the round still waits for --
#                      in the progress report while the machine writes, and in chunk-pass beside "failed": true.
#                      The state word stays `failed`: the server keeps a failed target out of the wave's plan, and
#                      knows a drawer that came back under another name by the failure record of that report
#                      (#3139). `pulled` is what tells "pulled, waiting" from "failed".
#   "rejoin": {...}    on a target that came back, while it completes a partition: {"part", "missing", "chunks",
#                      "away_s", "step"} -- how many chunks of the partition are not in its ledger (measured after
#                      the pass it joined), of how many, how long it was out (null = not measured), and where they
#                      come from now: neighbor | http (drawerfill.sh). Gone from the report once the partition is whole.
#   "serial", "port"   on EVERY target (#3181): a production line knows a disk by nothing else, a target that came
#                      back reports the name the round chose it by (`dev`), and the server's inventory may hold
#                      that name for nothing, or for another disk. Not known = not said.
#   "detached", "removed"   a written disk taken out safely, and pulled (drawerout.sh).

DRAWER_PLUG_SETTLE_S="${DRAWER_PLUG_SETTLE_S:-15}"

drawer_plug_listen() {   # the round's listener, started once. 1 = there is none
    if [ -n "${_px_pid:-}" ] && kill -0 "$_px_pid" 2> /dev/null; then return 0; fi
    if ! command -v imagesrv-uevent > /dev/null 2>&1; then
        [ -n "${_px_said:-}" ] || log "WARNING: imagesrv-uevent is not in this image -- a drawer pushed back in is looked for at the round's fixed points only"
        _px_said=1; return 1
    fi
    imagesrv-uevent "$CHUNK_DIR/plug" >> "$LOG_FILE" 2>&1 &
    _px_pid=$!
}

drawer_plug_stop() {   # the round has nothing more to write (drawer_back all)
    [ -z "${_px_pid:-}" ] || kill "$_px_pid" 2> /dev/null
    _px_pid=""; _px_until=""; rm -f "$CHUNK_DIR/plug"
}

drawer_plug_tick() {   # $1 = the pid of the pass or the fetch that runs now ("" = none), $2 = partition index, $3.. = the round's disks.
    # 0 = a drawer is back under another name: _disks is the round's list with it, and CHUNK_RELIST tells the partition's loop to take it.
    # The server and the image are chunk_partition_drawers' own (_cp_server, _cp_image): this is called only below it.
    [ -z "${CHUNK_BG:-}" ] && [ "${STREAM_MODE:-}" = chunk ] || return 1
    _px_run="$1"; _px_idx="$2"; shift 2
    drawer_plug_listen || :; if command -v fill_tick > /dev/null 2>&1; then fill_tick; fi   # #3181: the brain's ceiling, while one is in force (fillword.sh)
    if [ -e "$CHUNK_DIR/plug" ]; then rm -f "$CHUNK_DIR/plug"; _px_until=$(($(date +%s) + DRAWER_PLUG_SETTLE_S)); fi
    _px_q="$RUN_DIR/prodline.${D_SESSION_ID:-${CHUNK_SESSION:-0}}.new"   # new disks of a production line, until each writes or is out (prodline.sh): followed every tick
    [ -n "${_px_until:-}" ] || [ -s "$_px_q" ] || return 1
    [ -s "$_px_q" ] || [ "$(date +%s)" -le "$_px_until" ] || { _px_until=""; return 1; }
    PL_CHANGED=""; _db_strangers "$@"; if command -v prodline_poll > /dev/null 2>&1; then prodline_poll; fi
    _px_new=""; for _px_d in "$@"; do if _px_new=$(_db_found "$_px_d"); then break; fi; done
    [ -n "$_px_new$PL_CHANGED" ] || return 1
    # the round's list changes -- a name, a new disk, a target that left: the watch of the pass holds the old one
    _px_same=1; [ -z "$PL_CHANGED" ] && [ "$_px_new" = "$_px_d" ] || _px_same=0
    [ -z "$_px_run" ] || [ "$_px_same" = 1 ] || chunk_watch_end
    drawer_back in "$_px_idx" "$_cp_server" "$_cp_image" "$@"; _px_rc=$?
    # the server has not heard that it left (the pass's report comes when the pass ends), and a drawer that completes by itself
    # is left out of that report: unheard of, the server would plan the wave's repair for it. `untold` = said once, as failed
    [ -z "$_px_new" ] || [ ! -e "$RUN_DIR/targets/$_px_new/catchup" ] || : > "$RUN_DIR/targets/$_px_new/untold"
    # shellcheck disable=SC2086 # the round's list is a word list on purpose
    [ "$_px_rc" != 0 ] || { CHUNK_RELIST=1; set -- $_disks; }
    [ -z "$_px_run" ] || [ "$_px_same" = 1 ] || chunk_watch_start "$_px_run" "$_px_idx" "$@"
    return "$_px_rc"
}

drawer_waits() {   # $1 = target. 0 = it is out because it was PULLED, and may still come back in this round (_db_found's own tests)
    _wj_t="$RUN_DIR/targets/$1"
    [ "$(cat "$_wj_t/state" 2> /dev/null)" = failed ] && [ -f "$_wj_t/pulled" ] && [ ! -f "$_wj_t/foreign" ] && [ ! -f "$_wj_t/back_failed" ]
}

drawer_left_mark() {   # $1 = target. 0 = the kernel failed its writes, and its disk has LEFT the system since: it is `pulled`
    # #3270 (metal 18/11, a `delete` on cloner 2; and 15/11): a disk dropped from the kernel has its WRITEs refused ("device offline
    # error") while its node is still in sysfs, and with no DevExch or link-down on its port -- so drawer_dead_reason (verdict.sh)
    # said "rejected a write", not "pulled". The drawer failed, and nobody looked at it again: no grey on the screen, and no look
    # for its disk when it came back (_db_found). The node gone -- or another disk under its name -- is the evidence (principle 5),
    # and the same the kernel gives for a pull the port told of. A disk that still answers under its name, or one whose serial
    # cannot be read, is not shown to have left: it stays failed. Said on stderr: _db_found runs inside $( ).
    _lm_t="$RUN_DIR/targets/$1"
    [ "$(cat "$_lm_t/state" 2> /dev/null)" = failed ] && [ -f "$_lm_t/dead" ] && [ ! -f "$_lm_t/pulled" ] || return 1
    [ ! -f "$_lm_t/foreign" ] && [ ! -f "$_lm_t/back_failed" ] && [ -f "$_lm_t/bound" ] || return 1
    if [ -e "$SYSROOT/sys/block/$1" ]; then
        _lm_s=$(disk_serial "$1"); [ -n "$_lm_s" ] && [ "$_lm_s" != "$(target_bound_serial "$1")" ] || return 1
    fi
    : > "$_lm_t/pulled"
    log "$1: the kernel failed its writes, and its disk has left the system since -- pulled, and waited for (#3270)" >&2
}

drawer_window_open() {   # $1 = target. A drawer that came back is judged by the kernel's lines from THIS moment on (#3270)
    # metal 18/11 (n=2, cloner 2): `since` (progress.sh: the uptime at target_init) is the window of every dmesg verdict, and the refused
    # WRITE that made the drawer `pulled` was inside it. The disk came back under the SAME name (sdc), the next look at dmesg found that
    # line again, called it a new failure four seconds after the drawer rejoined -- and its GPT was wiped. The pull's evidence was
    # reported when it failed (_db_join moves it aside): what the kernel wrote before the drawer was found again is not this return's.
    # Uptime not read = the old window stays, and it is said: not a "fresh start" we cannot prove (principle 5).
    _wo_u=$(cut -d' ' -f1 "$SYSROOT/proc/uptime" 2> /dev/null)
    case "$_wo_u" in ''|*[!0-9.]*) log "WARNING: $1: the uptime was not read -- the kernel's lines from before it came back may still count against it (#3270)"; return 1 ;; esac
    echo "$_wo_u" > "$RUN_DIR/targets/$1/since"
}

drawer_back_json() {   # $1 = target -> what the progress report adds to it (progress.sh)
    # past the write (verifying and on) nobody waits for it any more: it is failed, by its reason, like any other
    if [ "$(cat "$RUN_DIR/state" 2> /dev/null)" = writing ] && drawer_waits "$1"; then printf ',"pulled":true'; fi
    if [ ! -f "$RUN_DIR/targets/$1/ata_log.json" ]; then   # a failed target's evidence carries both already (failure_json)
        # who it is: what answers under its name now, only while that is the disk the round chose (target_same, #2434). A disk that left --
        # pulled, or detached -- has no node, and its name may be ANOTHER disk's by now (#3181, drawerout.sh): then, what was kept of it
        if target_same "$1"; then _wj_i=$(target_ident "$1"); else _wj_i=$(cat "$RUN_DIR/targets/$1/seen" 2> /dev/null); fi
        _wj_i=${_wj_i%|*}; _wj_p=${_wj_i##*|}; _wj_s=${_wj_i%|*}   # serial|port|ata: a serial may hold a '|', the others do not
        case "$_wj_p" in ''|*[!0-9]*) _wj_p=null ;; esac
        [ -z "$_wj_s" ] || printf ',"serial":"%s","port":%s' "$(json_escape "$_wj_s")" "$_wj_p"
    fi
    if command -v drawer_out_json > /dev/null 2>&1; then drawer_out_json "$1"; fi   # detached, removed (drawerout.sh)
    [ ! -s "$RUN_DIR/targets/$1/rejoin" ] || printf ',"rejoin":%s' "$(tr -d '\n' < "$RUN_DIR/targets/$1/rejoin")"
}
