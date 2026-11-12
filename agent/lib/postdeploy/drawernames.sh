# drawernames.sh -- the computer name of each drawer of a room round (#2786).
# POSIX sh (busybox ash).
#
# The console chose a name for every (cloner, port) of the round and the
# server hands this machine its own: session.hostnames, {"<port>": "<name>"}
# (interfaces.md section 3). session.hostname_os narrows a disk with both
# Windows and Linux to one side ("windows"/"linux"); null = every side.
# Each drawer written in full (target state "done") gets its name through
# write_hostname (hostname.sh) -- the same offline-registry and /etc/hostname
# writing as a classroom station (#62/#89/#107), not a second copy of it.
#
# Never fatal and never silent, like name_this_machine (#856): a name that was
# not written leaves the drawer "done" with the reason in its error -- the
# image was written, the console colours it orange. No hostnames (null) = the
# round without names: nothing is mounted and the image's name stays.

_nd_warn() {
    # $1 = disk, $2 = why. The drawer stays done; the reason joins its error.
    _nd_why="שם המחשב לא נכתב: $2"
    [ -s "$RUN_DIR/targets/$1/error" ] && _nd_why="$(cat "$RUN_DIR/targets/$1/error") | $_nd_why"
    target_set "$1" "done" "$_nd_why"
    log "hostname (drawer $1): $_nd_why"
}

name_drawers() {
    # $@ = this wave's drawers. Always returns 0.
    # jq: the field's type is the answer -- "null" = no names; a read that
    # broke is not "no names" (principle 5): every done drawer says so.
    _nd_type=$(jq -r '.session.hostnames | type' "$RESP" 2>> "$LOG_FILE")
    _nd_trc=$?
    [ "$_nd_trc" -eq 0 ] && [ "$_nd_type" = "null" ] && return 0
    _nd_os=$(jq -r '.session.hostname_os // [] | if length == 1 then .[0] else "" end' "$RESP" 2>> "$LOG_FILE")
    for _nd in "$@"; do
        [ "$(cat "$RUN_DIR/targets/$_nd/state" 2>/dev/null)" = "done" ] || continue
        if [ "$_nd_trc" -ne 0 ] || [ "$_nd_type" != "object" ]; then
            _nd_warn "$_nd" "שמות המחשב בתשובת השרת לא נקראו"; continue
        fi
        _nd_port=$(disk_port "$_nd")
        _nd_name=$(jq -r --arg p "$_nd_port" '.session.hostnames[$p] // empty | strings' "$RESP" 2>> "$LOG_FILE")
        [ -n "$_nd_name" ] || { _nd_warn "$_nd" "השרת לא שלח שם לחריץ ${_nd_port:-לא ידוע}"; continue; }
        assert_target "$_nd" || continue   # #2434: never mount a disk that is not the one chosen
        _nd_res=$(HOSTNAME_OS="$_nd_os" write_hostname "$_nd" "$RUN_DIR/manifest.json" "$_nd_name")
        _nd_rc=$?
        printf '%s\n' "$_nd_res" > "$RUN_DIR/targets/$_nd/hostname.json"
        log "hostname (drawer $_nd, port $_nd_port): $_nd_res"
        [ "$_nd_rc" -eq 0 ] && continue
        _nd_err=$(json_get "$RUN_DIR/targets/$_nd/hostname.json" ".error")
        { [ -n "$_nd_err" ] && [ "$_nd_err" != "null" ]; } || _nd_err="סיבה לא ידועה (rc=$_nd_rc)"
        _nd_warn "$_nd" "$_nd_err"
    done
    return 0
}
