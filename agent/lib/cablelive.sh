# cablelive.sh -- ethtool --cable-test on a LIVE link, only while the cloner idles (#1968).
# POSIX sh (busybox ash). Needs netprobe.sh (_np_iface, _np_cable_run).
#
# The test drops the link for seconds, so hello (netprobe.sh) never runs it on
# a live link (interfaces.md, netprobe). Nadav 22/10: a link that is up but
# slow (< CABLE_TEST_MIN_MBPS, 100 on a gigabit card is the classic broken
# pair) or that dropped since the agent started is tested anyway -- from here,
# the cloner's idle branch (wait_poll), never with a round open or a task, and
# once per boot. "Dropped" = carrier_changes rose over the first value this
# boot read: the count at boot is not a drop (the link came up once).
# The result waits in $RUN_DIR/cable-live.json, which netprobe serves instead
# of {"skipped":"link up"}; the netprobe cache is aged so the next hello has it.

CABLE_TEST_MIN_MBPS="${CABLE_TEST_MIN_MBPS:-1000}"

cable_live_check() {
    [ "${D_ROLE:-}" = cloner ] && [ "${D_SESSION_STATE:-}" != open ] && [ "${D_TASK:-null}" = null ] || return 0
    [ -e "$RUN_DIR/cable-live.json" ] && return 0
    _cl_if=$(_np_iface) || return 0
    _cl_d="$SYSFS_ROOT/class/net/$_cl_if"; _cl_why=""
    _cl_s=$(cat "$_cl_d/speed" 2>/dev/null)
    case "$_cl_s" in ''|*[!0-9]*) ;; *)
        [ "$_cl_s" -gt 0 ] && [ "$_cl_s" -lt "$CABLE_TEST_MIN_MBPS" ] && _cl_why="slow: ${_cl_s}Mb/s" ;; esac
    _cl_n=$(cat "$_cl_d/statistics/carrier_changes" 2>/dev/null)
    case "$_cl_n" in ''|*[!0-9]*) ;; *)
        [ -s "$RUN_DIR/carrier0.$_cl_if" ] || echo "$_cl_n" > "$RUN_DIR/carrier0.$_cl_if"
        [ "$_cl_n" -gt "$(cat "$RUN_DIR/carrier0.$_cl_if")" ] && _cl_why="${_cl_why:+$_cl_why, }the link dropped" ;; esac
    [ -n "$_cl_why" ] || return 0
    log "cable test on the live link $_cl_if ($_cl_why) -- the link drops for a few seconds"
    _np_cable_run "$_cl_if" > "$RUN_DIR/cable-live.json.tmp" && mv "$RUN_DIR/cable-live.json.tmp" "$RUN_DIR/cable-live.json"
    log "cable test on $_cl_if: $(cat "$RUN_DIR/cable-live.json" 2>/dev/null)"
    rm -f "$RUN_DIR/netprobe.json.at"
}
