# chunkhold.sh -- the server's "hold" and "fail" in its answer to a fetch's "fetching" report (#2023).
# POSIX sh (busybox ash).
#
# A drawer that completes over HTTP holds the whole wave: the machines that are whole wait for it before the next
# partition (server/rounds/chunksend.py). Nadav 15/11: only the drawer that completes waits -- and the brain
# (server/brain/drawerhold.py) decides whether its completion is worth it. With `brain_drawer_hold` = on the server
# answers a `fetching` report, which is otherwise only "wait", with:
#
#   hold: [serial]   the drawer is taken out of this partition and completes IN THE BACKGROUND, with the machinery of a
#                    drawer that was pulled and came back (drawerback.sh): what chunkcast made durable on it is kept
#                    (`p<n>.kept`), it is marked `catchup` (the round's passes leave it out: _chunk_live, _chunk_body),
#                    and `_db_start` completes this partition and every one after it, up to the one the round is at, over
#                    HTTP and from the agent's own ranges -- the server is not asked and not told. When it is level
#                    with the round at a partition's start it joins that partition's stream (drawer_back before).
#   fail: {serial: why}   the drawer fails by name, as at the end of any pass: it is written again in the next wave.
#
# The fetch is stopped for it (SIGTERM: chunkcast apply flushes, writes its state and says "stopped"; the curl
# that is left with no reader dies on its next write) and waited for -- the state file is read AFTER it, so the line
# kept is what is durable. The other drawers of the machine are not asked to do more than they did: the next question
# of the round (`ended`) says what they hold, and the server plans the rest as it always does.
#
# Not here, on purpose: an answer without `hold` and `fail` -- every answer with the setting off -- does nothing at
# all. A serial that is not a live drawer here (it failed, or it is not ours) is not touched. A drawer that cannot be
# held (no place in the plan, or no drawerback.sh) is not left half-held: it fails by name, never "kept" without a
# completion (principle 5).

chunk_hold_take() {   # $1 = pass, $2 = index, $3.. = the partition's disks. After a "fetching" report: its answer is in answer.json
    _hk_idx="$2"; shift 2
    [ -s "$CHUNK_DIR/answer.json" ] || return 0
    jq -r 'if (.hold | type) == "array" then .hold[] else empty end' "$CHUNK_DIR/answer.json" 2>/dev/null > "$CHUNK_DIR/hold.list"
    jq -r 'if (.fail | type) == "object" then .fail | to_entries[] | "\(.key)|\(.value)" else empty end' "$CHUNK_DIR/answer.json" 2>/dev/null > "$CHUNK_DIR/fail.fetch"
    [ -s "$CHUNK_DIR/hold.list" ] || [ -s "$CHUNK_DIR/fail.fetch" ] || return 0
    _hk_hold=""; _hk_fail=""
    for _hk_d in $(_chunk_live "$@"); do
        _hk_s=$(disk_serial "$_hk_d"); [ -n "$_hk_s" ] || _hk_s=$(target_bound_serial "$_hk_d"); [ -n "$_hk_s" ] || continue
        if grep -qxF "$_hk_s" "$CHUNK_DIR/hold.list"; then _hk_hold="$_hk_hold $_hk_d"
        elif awk -F'|' -v s="$_hk_s" '$1 == s { f = 1 } END { exit !f }' "$CHUNK_DIR/fail.fetch"; then _hk_fail="$_hk_fail $_hk_d"; fi
    done
    [ -n "$_hk_hold$_hk_fail" ] || return 0
    _hk_at=$(_db_pos "$_hk_idx") || _hk_at=""   # where in the plan this partition is: a completion goes up to it
    if [ -n "$_hk_hold" ] && { [ -z "$_hk_at" ] || ! command -v _db_start > /dev/null; }; then
        log "partition $_hk_idx: the server holds a drawer, and it cannot be completed in the background here -- it fails by name"
        for _hk_d in $_hk_hold; do
            echo "$(disk_serial "$_hk_d")|ההשלמה ברקע אינה אפשרית במשכפל הזה (אין לה מקום בתוכנית המחיצות) -- הדיסק ייכתב בגל הבא" >> "$CHUNK_DIR/fail.fetch"
        done
        _hk_fail="$_hk_fail$_hk_hold"; _hk_hold=""
    fi
    _chunk_hold_stop "$_hk_idx"
    for _hk_d in $_hk_fail; do _chunk_hold_fail "$_hk_d" "$_hk_idx"; done
    # shellcheck disable=SC2086 # a word list of disks
    [ -z "$_hk_hold" ] || _chunk_hold_go "$_hk_idx" "$_hk_at" $_hk_hold
}

_chunk_hold_stop() {   # $1 = index. The fetch of this partition is stopped, and waited for: only then is its state file whole
    _hs_pid=$(cat "$CHUNK_DIR/apply.pid" 2>/dev/null)
    case "$_hs_pid" in
        ''|*[!0-9]*) log "partition $1: the fetch's pid is not known -- it is not stopped here" ;;
        *) kill "$_hs_pid" 2>/dev/null
           wait_pid "$_hs_pid" "${WAIT_HELPER_S:-10}" "ההורדה של מחיצה $1 (המוח החליט על מגירה)" || log "partition $1: the fetch did not stop in time and was killed" ;;
    esac
}

_chunk_hold_fail() {   # $1 = disk, $2 = index. "fail" of the server: written again in the next wave
    _hf_why=$(awk -F'|' -v s="$(disk_serial "$1")" '$1 == s { sub(/^[^|]*\|/, ""); print; exit }' "$CHUNK_DIR/fail.fetch")
    fail_written_target "$1" "מחיצה $2: ${_hf_why:-השרת הכשיל את הדיסק בהשלמה}" stream
}

_chunk_hold_go() {   # $1 = index, $2 = its place in the plan, $3.. = disks: each completes in the background, the round goes on
    _hg_idx="$1"; _hg_at="$2"; shift 2
    # _cp_server, _cp_image: the round's own server and image, set by chunk_partition_drawers (the only caller up the stack)
    # shellcheck disable=SC2154
    _db_srv="$_cp_server"
    # shellcheck disable=SC2154
    _db_img="$_cp_image"; _db_at="$_hg_at"
    echo "$_db_at" > "$CHUNK_DIR/at"
    for _hg_d in "$@"; do
        _hg_n=$(partition_node "$_hg_d" "$_hg_idx")
        # what chunkcast made durable on it stays (drawerback.sh: the next run of the partition drops the line of a target it is not given)
        _db_line "$CHUNK_DIR/p$_hg_idx.kept" "$_hg_n" > /dev/null \
            || _db_line "$CHUNK_STATE" "$_hg_n" >> "$CHUNK_DIR/p$_hg_idx.kept" \
            || log "partition $_hg_idx: $_hg_d has no line in the fetch's state -- the whole partition is fetched for it again, never less"
        log "partition $_hg_idx: the server holds $_hg_d ($(disk_serial "$_hg_d")) -- it completes in the background, the others go on"
        _db_start "$_hg_d" "$_hg_d"
    done
}
