# idlebrain.sh -- #3268 (B62, Nadav 18/11): the waiting-screen write check is reported to the server, and what the screen shows
# is the brain's decision on it. POSIX sh (busybox ash).
#
# The check (idlecheck.sh, idlelong.sh) measures a disk NOW. The server remembers what the same serial wrote in the last
# wave it fell behind in. Nadav 18/11, on #3650: "the brain decides if it fell behind last time AND if the check showed it
# falls behind now". So the agent no longer joins the two itself:
#   up   idle_wc_report_json: every drawer whose check finished (ok | slow, with a number) says its serial, its state, the MB/s
#        of the last half (the sustained rate), of the first half, the floor the check used for THIS drawer (`floor_mbps`, null =
#        none: a mechanical disk, whose policy is not the SSD floor), and -- slow -- its own reason: the hello's `write_check`
#   down idle_wc_decisions_refresh: the hello answer's `write_decisions` -> $RUN_DIR/write_decisions. First line "ok" (the answer
#        was read whole -- otherwise the previous file stays), then "verdict|state|mbps|first|serial|text". The serial is the
#        5th field, matched whole: it has spaces (#3574)
#   idle_wc_decision: the decision on EXACTLY this result (same serial, state and numbers) as the screen's "state|text":
#        RED = slow|the reason -- also on a check that passed, when the brain's reason is the history (a sustained rate under the
#        floor in the last wave, #3268 18/11) -- INFO = history|a neutral line (no colour)
# No decision, a decision on another result (the disk was checked again, a peer finished and changed "slow"), a file that was
# not read, a word that is not RED/INFO: the check's own word stands (idle_wc_field). A missing decision is NOT "ok": a disk
# the check says slow is red without one -- red comes from the check of now, the decision only adds the history.

idle_wc_report_json() { # the hello fragment ',"write_check":[...]'; nothing when no drawer has a finished, numbered result
    _iz_o=""; _iz_fl=$(_ic_floor); writecheck_hdd_policy "$RUN_DIR/response.json"   # the floor of the check (idlecheck.sh): the brain compares the last wave's sustained rate to THIS, not to a threshold of its own
    for _iz_d in $(list_drawers); do
        _iz_f=$(_ic_field_now "$_iz_d"); _iz_t=${_iz_f%%|*}
        case "$_iz_t" in ok|slow) ;; *) continue ;; esac   # checking / failed / unchecked: nothing to decide on
        _iz_s=$(disk_serial "$_iz_d"); [ -n "$_iz_s" ] || continue
        _iz_m=$(cat "$RUN_DIR/idlecheck/res.$_iz_d/mbps" 2> /dev/null); _iz_1=$(cat "$RUN_DIR/idlecheck/res.$_iz_d/first" 2> /dev/null)
        case "$_iz_m" in ''|*[!0-9]*|0[0-9]*) continue ;; esac   # a number JSON accepts, or no report (a hello that does not parse is a machine nobody hears, #3632)
        case "$_iz_1" in ''|*[!0-9]*|0[0-9]*) _iz_1=null ;; esac
        _iz_w=""; [ "$_iz_t" != slow ] || _iz_w=${_iz_f#*|}
        _iz_k=$_iz_fl; case "${WC_HDD:-}:$(_wc_kind "$_iz_d")" in on:מכני|off:מכני) _iz_k=null ;; esac   # as _wc_slow_why: a mechanical disk with the HDD switch set has no SSD floor
        _iz_o="$_iz_o${_iz_o:+,}{\"serial\":\"$(json_escape "$_iz_s")\",\"state\":\"$_iz_t\",\"mbps\":$_iz_m,\"first_mbps\":$_iz_1,\"floor_mbps\":$_iz_k,\"reason\":\"$(json_escape "$_iz_w")\"}"
    done
    [ -z "$_iz_o" ] || printf ',"write_check":[%s]' "$_iz_o"
}

idle_wc_decisions_refresh() { # 0 = refreshed (an answer with no write_decisions empties the file); 1 = the answer was not read whole: the previous file stays
    json_get_join "$RUN_DIR/response.json" \
        '["ok"] + ((.write_decisions // []) | map("\(.verdict)|\(.state)|\(.mbps)|\(.first_mbps // "")|\(.serial)|\(.reason | gsub("[|\r\n]"; " "))"))' \
        > "$RUN_DIR/write_decisions.next" 2> /dev/null
    [ "$(head -n 1 "$RUN_DIR/write_decisions.next" 2> /dev/null)" = ok ] || { rm -f "$RUN_DIR/write_decisions.next"; return 1; }
    mv "$RUN_DIR/write_decisions.next" "$RUN_DIR/write_decisions"
}

idle_wc_decision() { # $1 = disk, $2 = the check's own "state|text". Prints the brain's "state|text" for exactly this result, or returns 1: no decision
    _iz_s=$(disk_serial "$1"); [ -n "$_iz_s" ] && [ -f "$RUN_DIR/write_decisions" ] || return 1
    _iz_m=$(cat "$RUN_DIR/idlecheck/res.$1/mbps" 2> /dev/null); _iz_1=$(cat "$RUN_DIR/idlecheck/res.$1/first" 2> /dev/null)
    case "$_iz_m" in ''|*[!0-9]*) return 1 ;; esac
    case "$_iz_1" in *[!0-9]*) return 1 ;; esac
    _iz_l=$(WD_SERIAL="$_iz_s" WD_KEY="${2%%|*}|$_iz_m|$_iz_1|" awk -F'|' \
        'NR > 1 && ($2 "|" $3 "|" $4 "|") == ENVIRON["WD_KEY"] && $5 == ENVIRON["WD_SERIAL"] { print $1 "|" $6; exit }' \
        "$RUN_DIR/write_decisions" 2> /dev/null)
    case "${_iz_l%%|*}:${2%%|*}:${_iz_l#*|}" in
        RED:slow:?*|RED:ok:?*) printf 'slow|%s' "${_iz_l#*|}" ;;   # ok: the check of now passed and the history is the reason (the server's word, B62)
        INFO:ok:?*) printf 'history|%s' "${_iz_l#*|}" ;;
        *) return 1 ;;
    esac
}
