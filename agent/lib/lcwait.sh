# lcwait.sh -- #1367: what the local clone's screens do while they wait for a
# person (localclone.sh), split off before the 300-line wall. The machine kept
# no hello on these screens and had no ceiling: a cloner went silent for an
# hour, missed the wave, and the console's power-off never reached it.
#
# * the ceiling: no touch for LC_QUIET_S and the machine goes on from the
#   server (lc_quiet_reset / lc_quiet_over);
# * the console's power request that the beat (attended.sh) kept: carried out
#   on the screens (lc_power_check), and during a copy only when the operator
#   confirmed it over the warning (lc_power_during_copy -- Nadav 14/10).
# POSIX sh (busybox ash).

#: Metal 18/10 (rc6, cloner 2): the boot choice's count starts with its first
#: frame on the display -- the GUI's stamp, $GUI_DIR/lcboot.shown (main.c) --
#: never before it: the agent said "on screen" and counted while the GUI was
#: still opening the display, and the 20 s were gone before the picture came.
#: 60 = three counts: a first start of the GUI (font check, the display's
#: mode) took more than the whole 20 s there, and a cloner with no screen at
#: all still goes on from the server within about a minute and a half.
LC_SHOWN_WAIT_S="${LC_SHOWN_WAIT_S:-60}"

lc_boot_due() {
    # 0 = the boot choice's count is over. Not started yet (_bc_dl=0): it
    # starts at the stamp's second, or at the offer (_bc_w0) when the frame
    # came first. A stamp that cannot be read is no frame; after
    # LC_SHOWN_WAIT_S the count starts without it, aloud (principle 1).
    if [ "${_bc_dl:-0}" = 0 ]; then
        _bd_t=$(cat "${GUI_DIR:-$RUN_DIR/gui}/lcboot.shown" 2>/dev/null)
        case "$_bd_t" in
            ''|*[!0-9]*) [ $(( $(date +%s) - _bc_w0 )) -ge "$LC_SHOWN_WAIT_S" ] || return 1
                log "local clone: the boot choice was not on the screen after ${LC_SHOWN_WAIT_S}s -- the ${LC_BOOT_S}s count starts without it"
                _bd_t=$(date +%s) ;;
            *) log "local clone: boot choice on screen (${LC_BOOT_S}s to the server unless touched)" ;;
        esac
        [ "$_bd_t" -ge "$_bc_w0" ] || _bd_t=$_bc_w0
        _bc_dl=$((_bd_t + LC_BOOT_S)); echo "$_bc_dl|0" > "$LC/boot"
    fi
    [ "$(date +%s)" -ge "$_bc_dl" ]
}

lc_quiet_reset() { _lq_at=$(date +%s); touch "$LC/quiet.mark"; }   # touch: a truncate may keep the mtime

lc_quiet_over() {
    # 0 = nobody touched the screen for LC_QUIET_S. A key or a click is the
    # kiosk's idle.touch (#434, at most every 10 s): newer than the mark is a
    # touch. A find that fails is not one -- the count runs on, toward the
    # server (principle 1), never toward a wait for ever.
    if [ -f "$RUN_DIR/idle.touch" ] && _lq_n=$(find "$LC/quiet.mark" -newer "$RUN_DIR/idle.touch" 2>/dev/null) &&
            [ -z "$_lq_n" ]; then
        lc_quiet_reset; return 1
    fi
    [ $(( $(date +%s) - ${_lq_at:-0} )) -ge "${_lq_max:-600}" ]
}

lc_round_calls() {
    # #1560 (metal 17/10: cloner 2 sat on a menu while a round was opened for
    # it; WoL wakes nothing that is on). The beat's answer (attended.sh keeps
    # .session.state) names a wave offered to THIS machine -- the server
    # offers one only to a machine in its roster with fresh drawers
    # (hello.py). A screen with no local work running leaves for it, as if
    # woken by WoL, and the main loop's hello joins. Unread = no round.
    # #1703: the main loop's own answer counts too -- it said "open" a moment
    # ago, and the boot choice waited a sleep and the beat's round trip to hear
    # it again. Stale only where it cannot say "open": a choice that saw it
    # left for the server at once, so the local screens never start with it.
    case "${D_SESSION_STATE:-}" in open|running) return 0 ;; esac
    case "$(cat "$RUN_DIR/attended.round" 2>/dev/null)" in open|running) return 0 ;; esac
    return 1
}

lc_end_count() {
    # #1560 (Nadav 17/10): a copy that ran to its end ("done" -- not stopped
    # by hand, which goes back to the choice) counts to the power-off, done
    # or failed (idle.sh end_countdown): every target written, some, none.
    # shellcheck disable=SC2046 # the target list is a word list on purpose
    end_tally $(cat "$LC/run/targets" 2>/dev/null)
    if [ "$_et_n" -gt 0 ] && [ "$_et_ok" = "$_et_n" ]; then _lk=localclone
    elif [ "$_et_ok" -gt 0 ]; then _lk=partial; else _lk=failed; fi
    # the result screen's own buttons (end.req: power off, again, back) stay the person's
    END_ABORT_FILE="$LC/end.req"
    # shellcheck disable=SC2046 # #2024: every target's result decides (none = a problem, held)
    end_countdown "$_lk" "local clone ended ($_et_ok of $_et_n)" "$_et_ok" "$_et_n" $(cat "$LC/run/targets" 2>/dev/null); _lk=$?
    END_ABORT_FILE=""; return "$_lk"
}

lc_copy_busy() {
    # 0 = a local copy is writing now: from lc_run's first write to its end.
    case "$(cat "${LC:-/nonexistent}/phase" 2>/dev/null)" in
        copying|shrinking|verifying|restoring) return 0 ;;
    esac
    return 1
}

lc_power_during_copy() {
    # #1367 (Nadav 14/10): the operator saw "in the middle of a local clone --
    # the target will be broken" and confirmed (power_confirmed). Runs in the
    # beat (attended.sh), since lc_run holds the screen's loop. Wake-on-LAN
    # first for a power-off (#587): not armed = nothing is done, the copy goes
    # on, aloud. Then the targets are marked failed, the journal is told
    # (lc_report, best effort) -- and only then the power goes.
    mv "$RUN_DIR/power.resp" "$RUN_DIR/power.taken" 2>/dev/null || return 0
    _pd_a=$(json_get "$RUN_DIR/power.taken" .power_action)
    log "local clone: the console confirmed '$_pd_a' in the middle of the copy -- the targets are marked failed"
    if [ "$_pd_a" = poweroff ]; then
        safe_poweroff "local clone: confirmed during copy" lc_power_commit || \
            log "local clone: wake-on-LAN was not armed -- not powering off, the copy goes on"
        return 0
    fi
    lc_power_commit
    _pk_r="${RESP:-}"; RESP="$RUN_DIR/power.taken"; power_check; RESP="$_pk_r"
}

lc_power_commit() {
    for _pd_t in $(cat "$LC/run/targets" 2>/dev/null); do
        fail_written_target "$_pd_t" "המחשב כובה מהקונסולה באמצע שיכפול מקומי — הדיסק שבור ואינו שמיש"
    done
    _lr_src=$(cat "$LC/run/src" 2>/dev/null); _lr_r="$LC/run"
    date +%s > "$_lr_r/ended"; echo 1 > "$_lr_r/stopped"; lc_report
}

lc_power_check() {
    # #1367: the console's power-off/reboot (#980) came in a beat's answer --
    # attended.sh kept it. Carried out by power_check's own rule (idle.sh:
    # Wake-on-LAN first, or the machine stays on and says so).
    [ -s "$RUN_DIR/power.resp" ] && mv "$RUN_DIR/power.resp" "$RUN_DIR/power.taken" || return 0
    log "local clone: the console asked to $(json_get "$RUN_DIR/power.taken" .power_action) -- carrying it out"
    _pk_r="${RESP:-}"; RESP="$RUN_DIR/power.taken"; power_check; RESP="$_pk_r"
}

#: #1913 (S7b, 20/10): a report the server did not take -- it was down -- is
#: kept and sent again after a hello the server answered, at most this often.
LC_REPORT_TRIES="${LC_REPORT_TRIES:-30}"

lc_report_keep() {
    # After lc_report's POST failed. Kept only when nobody answered (000) or
    # the server failed (5xx): a 4xx is an answer, and the same body would get
    # it again. The file is named <started>_<ended>, the run's own clock.
    case "$_rp_c" in ''|000|5??) ;; *) return 0 ;; esac
    mkdir -p "$LC/pending" && cp "$_rp_r/report.json" "$LC/pending/$(cat "$_rp_r/started")_$(cat "$_rp_r/ended").json" ||
        log "local clone: the report could not be kept to send again -- it is lost"
}

lc_report_resend() {
    # Called after a hello the server answered (the main loop, the attended
    # beat). Each kept report goes once more, marked late: late_s = seconds
    # from the copy's end to now, by this machine's clock. Claimed by mv, so
    # the beat and the main loop never send the same one twice. 200 = gone,
    # and the result screen says "reported"; LC_REPORT_TRIES failures = given
    # up aloud -- never a retry for ever.
    for _rr_f in "${LC:-/nonexistent}"/pending/*.json; do
        [ -f "$_rr_f" ] && mv "$_rr_f" "$_rr_f.sending" 2>/dev/null || continue
        _rr_n=${_rr_f##*/}; _rr_n=${_rr_n%.json}; _rr_b=$(cat "$_rr_f.sending")
        printf '%s' "${_rr_b%\}},\"late_s\":$(( $(date +%s) - ${_rr_n#*_} ))}" > "$_rr_f.body"
        _rr_c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Content-Type: application/json" \
            --data-binary "@$_rr_f.body" "${SERVER:-http://0.0.0.0}/api/v1/agent/local-clone" 2>/dev/null)
        rm -f "$_rr_f.body"
        if [ "$_rr_c" = 200 ]; then
            rm -f "$_rr_f.sending" "$_rr_f.tries"; log "local clone: the kept report reached the server journal (late)"
            [ "$(cat "$LC/run/started" 2>/dev/null)" != "${_rr_n%_*}" ] || echo sent > "$LC/run/report"
            continue
        fi
        _rr_t=$(( $(cat "$_rr_f.tries" 2>/dev/null || echo 0) + 1 ))
        if [ "$_rr_t" -ge "$LC_REPORT_TRIES" ]; then
            rm -f "$_rr_f.sending" "$_rr_f.tries"
            log "local clone: the kept report was refused $_rr_t times (http ${_rr_c:-000}) -- given up, it is not in the journal"
        else echo "$_rr_t" > "$_rr_f.tries"; mv "$_rr_f.sending" "$_rr_f"; fi
    done
    return 0   # the hello it follows succeeded; a report never fails it
}
