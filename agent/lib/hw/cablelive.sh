# cablelive.sh -- ethtool --cable-test on a LIVE link, only while the cloner idles (#1968). The server's brain decides, this runs it (#3663, A10).
# POSIX sh (busybox ash). Needs netprobe.sh (_np_iface, _np_cable_run).
#
# The test drops the link for seconds, so hello (netprobe.sh) never runs it on
# a live link (interfaces.md, netprobe). Nadav 22/10: a link that is up but
# slow or that dropped since the agent started is tested anyway -- from here,
# the cloner's idle branch (wait_poll), never with a round open or a task, and
# once per boot.
#
# #3663 (Nadav 18/11, "everything is decided in the brain"): WHETHER the link is slow or dropped is no longer judged
# here. The hello carries the facts (sysinfo.sh nic_link_json: the speed, carrier_changes now and the first value this boot
# read, whether this test already ran); the answer's link_verdict.cable_test is the brain's verdict (server/brain/linkverdict.py,
# rule K-CABLE), and only RUN runs the test. No verdict (an old server, a rule that failed) = no test: the test drops the link,
# and what was not decided is not done. What stays here is what makes running it safe, not a decision: the cloner's idle branch
# only, no round open and no task in the answer in hand, and once per boot (the verdict says RUN until the next hello reports
# the test as done).
# The result waits in $RUN_DIR/cable-live.json, which netprobe serves instead
# of {"skipped":"link up"}; the netprobe cache is aged so the next hello has it.

cable_live_check() {
    [ "${D_ROLE:-}" = cloner ] && [ "${D_SESSION_STATE:-}" != open ] && [ "${D_TASK:-null}" = null ] || return 0
    [ -e "$RUN_DIR/cable-live.json" ] && return 0
    [ "$(jq -r '.link_verdict.cable_test // empty' "$RUN_DIR/response.json" 2>/dev/null)" = RUN ] || return 0
    _cl_if=$(_np_iface) || return 0
    _cl_why=$(jq -r '.link_verdict.cable_why // empty' "$RUN_DIR/response.json" 2>/dev/null)
    log "cable test on the live link $_cl_if (the brain: ${_cl_why:-no reason given}) -- the link drops for a few seconds"
    _np_cable_run "$_cl_if" > "$RUN_DIR/cable-live.json.tmp" && mv "$RUN_DIR/cable-live.json.tmp" "$RUN_DIR/cable-live.json"
    log "cable test on $_cl_if: $(cat "$RUN_DIR/cable-live.json" 2>/dev/null)"
    # #3636: a netprobe job already running measured without this result and would stamp the cache fresh when it ends (netcache.sh _np_stamp): the flag stops that.
    # #3652: uptime, the clock the job's start is stamped with -- not `date`, which the hello answer moves.
    _cl_u=$(_np_mono) && echo "$_cl_u" > "$RUN_DIR/netprobe.json.stale" || log "netprobe cache not marked stale -- cable-live.json may wait NETPROBE_TTL"
    rm -f "$RUN_DIR/netprobe.json.at"
}
