# smartselftest.sh -- SMART short self-test of the drawers while the cloner idles (#1970).
# POSIX sh (busybox ash).
#
# Nadav 22/10: while the cloner waits, `smartctl -t short` runs on its disks in
# the background; the result joins the SMART verdict, so a dying disk shows
# BEFORE it is written; a round that starts cancels the test.
#
# Once per disk per boot (a mark in $RUN_DIR/smart). After SMART_SHORT_WAIT_S
# the verdict cache of that disk is dropped and smart_probe reads it again --
# `-x` carries the self-test log, and smartjudge.sh fails a disk whose last
# self-test failed (selftest_failed).
#
# Never under a write: started only from the cloner's idle branch (wait_poll),
# and every write path -- do_restore_drawers, lc_session -- calls
# smart_selftest_cancel, which first STOPS the background loop and waits for
# it (a loop still alive could start a test after the abort), and only then
# sends `smartctl -X` to every disk a test was started on.

SMART_SHORT_WAIT_S="${SMART_SHORT_WAIT_S:-120}"
SMART_SELFTEST_STOP_S="${SMART_SELFTEST_STOP_S:-70}"   # covers smart_probe: 2 x SMARTCTL_MAX_S (30) with -d sat

smart_selftest_one() {
    # $1 = disk. Returns at once when cancelled -- before the start and while waiting.
    _st_c="$RUN_DIR/smart/selftest-cancel"; _st_mark="$RUN_DIR/smart/$1.selftest"
    [ -e "$_st_mark" ] || [ -e "$_st_c" ] && return 0
    : > "$_st_mark"
    capped_run "$SMARTCTL_MAX_S" /dev/null "$SMARTCTL" -t short "$DEVROOT/$1"; _st_rc=$?
    if [ "$CAPPED_TIMED_OUT" = 1 ] || [ $((_st_rc & 7)) -ne 0 ]; then   # bits 0-2: the command itself failed
        log "$1: SMART short self-test was not started (rc=$_st_rc) -- the verdict stays as it was"
        return 0
    fi
    log "$1: SMART short self-test started"
    _st_t=0
    while [ "$_st_t" -lt $((SMART_SHORT_WAIT_S * 10)) ]; do
        [ -e "$_st_c" ] && return 0
        poll_tick; _st_t=$((_st_t + _tk))
    done
    [ -e "$_st_c" ] && return 0
    rm -f "$RUN_DIR/smart/$1.v"
    log "$1: SMART after the short self-test: $(smart_probe "$1")"
}

smart_selftest_idle_start() {
    [ "$D_ROLE" = cloner ] || return 0
    [ -n "${_smart_selftest_pid:-}" ] && kill -0 "$_smart_selftest_pid" 2>/dev/null && return 0
    mkdir -p "$RUN_DIR/smart"; rm -f "$RUN_DIR/smart/selftest-cancel"
    ( for _st_d in $(list_drawers); do smart_selftest_one "$_st_d"; done ) < /dev/null &
    _smart_selftest_pid=$!
}

smart_selftest_cancel() {
    mkdir -p "$RUN_DIR/smart"; : > "$RUN_DIR/smart/selftest-cancel"
    if [ -n "${_smart_selftest_pid:-}" ]; then
        wait_pid "$_smart_selftest_pid" "$SMART_SELFTEST_STOP_S" "SMART self-test loop" || :
        _smart_selftest_pid=
    fi
    for _st_m in "$RUN_DIR"/smart/*.selftest; do
        [ -e "$_st_m" ] || continue
        _st_d=${_st_m##*/}; _st_d=${_st_d%.selftest}
        capped_run "$SMARTCTL_MAX_S" /dev/null "$SMARTCTL" -X "$DEVROOT/$_st_d"; _st_rc=$?
        [ "$CAPPED_TIMED_OUT" = 0 ] && [ $((_st_rc & 7)) -eq 0 ] \
            || log "$_st_d: SMART self-test abort not confirmed (rc=$_st_rc) -- a test may still run under the write"
    done
}
