# sensecore.sh -- the cloner's senses for the brain (#3064): one place that joins them into the progress report.
# POSIX sh (busybox ash).
#
# The brain decides on what the machine says about itself (server/brain/agentsenses.py reads it). Nothing here
# changes a screen, a verdict or a round: a sense that cannot be read is `null` ("not measured") -- never 0,
# never "fine" (principle 5) -- and a failure to read one costs only that one.
#
#   senses_machine_json SESSION TASK  -> ,"senses":{carrier,psi,ctrl,rx,http,tune}   (machine_extra_json, datareport.sh)
#   senses_target_json DEV            -> ,"senses":{phy,resets,conn,temp,ready_s,at_boot}   (target_extra_json)
#
# The sensors live in hostsense.sh (carrier, PSI, controller), linksense.sh (SATA link: phy, resets, connection),
# disksense.sh (temperature, insertion-to-ready, present at boot) and restore/rxsense.sh (the receiver's counters,
# the HTTP fetches). A round's files are under $RUN_DIR/senses/<session>/ -- a new session removes the old ones;
# a drawer's are targets/<dev>/ss.* -- job_reset (progress.sh) already clears those for the next job.

_ss_num() { case "$1" in ''|*[!0-9]*) printf '%s' "${2:-0}" ;; *) printf '%s' "$1" ;; esac; }   # a count, or the default

ss_dir() {
    # $1 = session. Its directory (made), and the earlier rounds' removed. Nothing printed = unusable.
    case "$1" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
    _sd="$RUN_DIR/senses/$1"; mkdir -p "$_sd" 2>/dev/null || return 1
    for _sd_o in "$RUN_DIR"/senses/*/; do
        [ -d "$_sd_o" ] && [ "${_sd_o%/}" != "$_sd" ] && rm -rf "$_sd_o"
    done
    printf '%s' "$_sd"
}

_ss_add() { _ss_out="${_ss_out:+$_ss_out,}\"$1\":$2"; }

ss_tune_json() {
    # #3816: what drawer_tune chose this job, one line of JSON; null = it did not run -- the brain records "not
    # read", never a default. #3173: drawer_tune left with the old receiver's pipeline, so today it is always null.
    _st_v=$(head -n 1 "$RUN_DIR/drawer_tune.json" 2>/dev/null) || _st_v=
    printf '%s' "${_st_v:-null}"
}

senses_machine_json() {
    # $1 = session id, $2 = task id (a capture: nothing). From the first tick the machine writes.
    [ -n "${1:-}" ] && [ -z "${2:-}" ] || return 0
    case "$(cat "$RUN_DIR/state" 2>/dev/null)" in writing|done|failed|partial) ;; *) return 0 ;; esac
    _sm_d=$(ss_dir "$1") || return 0
    ds_temp_tick >/dev/null 2>&1 || :   # one drawer's temperature at most every SENSE_TEMP_EVERY_S -- a sample, not a verdict
    _ss_out=""
    _ss_add carrier "$(ss_carrier_json "$_sm_d")"
    _ss_add psi "$(ss_psi_json "$_sm_d")"
    _ss_add ctrl "$(ss_ctrl_json)"
    _ss_add rx "$(ss_rx_json "$_sm_d")"
    _ss_add http "$(ss_http_json "$_sm_d")"
    _ss_add tune "$(ss_tune_json)"
    printf ',"senses":{%s}' "$_ss_out"
}

senses_target_json() {
    # $1 = dev. Only for a drawer this job is writing (or wrote): nothing for one that has not begun.
    _st_s=$(cat "$RUN_DIR/targets/$1/state" 2>/dev/null)
    case "$_st_s" in writing|done|failed) ;; *) return 0 ;; esac
    ls_resets_refresh >/dev/null 2>&1 || :
    printf ',"senses":{"phy":%s,"resets":%s,"conn":%s,"temp":%s,"ready_s":%s,"at_boot":%s}' \
        "$(ls_phy_json "$1")" "$(ls_resets_json "$1" "$_st_s")" "$(ls_conn_json "$1")" \
        "$(ds_temp_json "$1")" "$(ds_ready_json "$1")" "$(ds_boot_json "$1")"
}
