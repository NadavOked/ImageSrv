# localclone.sh -- #1322: a cloning machine booted from the network asks,
# once, "from the server or local clone?", and the local flow copies one of
# its disks to the others with no server. The controller; the read-only
# inventory is lcinv.sh, the plan lcplan.sh, the copy lccopy.sh, and the
# screen's side (state records, the clicks) lcgui.sh. POSIX sh (busybox ash).
#
# Unattended behaviour does not change (CLAUDE.md QA, #17): with no touch for
# LC_BOOT_S the choice is "from the server" and the machine goes on to the
# standby screen exactly as before. "Not chosen" is the server, never local
# (principle 1): a GUI that dies, a decision that cannot be read, a single
# disk, no GUI at all -- all of them are the server. Nothing is written to a
# disk before the confirm dialog's red button (principle 7), and the kernel
# command line is not read for any of it (principle 2).
#
# The GUI talks through files in $LC, one per question, written by the
# kiosk's bridge (lcgui.sh) and consumed here: boot.decision (server|local|
# pull|pause), measure.req (<dev>), start.req (<src>|<t1,t2>|<shrink 0/1>),
# stop.req, end.req (poweroff|again|back), pull.req (lcpull.sh, 16/10).

LC_BOOT_S="${LC_BOOT_S:-20}"
#: #1367: a choice nobody makes is not a wait for ever. No touch on the screen
#: for this long -- the paused choice, the local screen, its result -- and the
#: machine goes on from the server, as the count does. Not while a copy runs.
LC_QUIET_S="${LC_QUIET_S:-600}"

lc_gui_alive() { [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null; }

local_clone_arm() {
    # Before the GUI starts on the first idle hello: the boot screen is the
    # first thing it draws, never a flash of the standby screen. Only a cloner,
    # only once a boot, only with a disk. 16/10: one disk is still a target for
    # "restore from the server"; the local clone needs two (boot.local).
    [ "$D_ROLE" = cloner ] && [ -z "${_lc_armed:-}" ] || return 0
    _lc_armed=1; _lc_n=$(list_drawers | wc -l)
    [ "$_lc_n" -ge 1 ] || { log "local clone: no disks -- no boot choice"; return 0; }
    mkdir -p "$LC" || return 0
    rm -f "$LC/boot.decision" "$LC/measure.req" "$LC/start.req" "$LC/stop.req" "$LC/end.req" "$LC/pull.req" "${GUI_DIR:-$RUN_DIR/gui}/lcboot.shown"
    if [ "$_lc_n" -ge 2 ]; then echo 1; else echo 0; fi > "$LC/boot.local"
    echo "0|0" > "$LC/boot"; echo boot > "$LC/screen"
}

local_clone_offer() {
    # After gui_cloner_parent. Returns when the machine goes on from the server.
    [ -f "$LC/screen" ] || return 0
    if ! lc_gui_alive; then
        log "local clone: the GUI is not up -- from the server, as before"
        rm -f "$LC/screen"; return 0
    fi
    LC_PAUSED=0; [ "$(cut -d'|' -f2 "$LC/boot" 2>/dev/null)" = 2 ] && LC_PAUSED=2   # #2079: back from the wait (guibridge.sh choice-back) -- no count
    # #1367: the screens below wait for a person, and nothing else said hello
    # meanwhile -- the server drew the machine as off, a round opened without
    # it and the console's power-off was never delivered. The beat (attended.sh)
    # says hello all along; what it brings back is read in the loops.
    _lq_max=$(attended_secs LC_QUIET_S "$LC_QUIET_S" 600)
    rm -f "$RUN_DIR/power.resp" "$RUN_DIR/attended.round"
    while :; do
        lc_boot_choice
        case "$LC_CHOICE" in local) lc_session ;; pull) lc_pull_session ;; *) break ;; esac
        [ "$LC_END" = back ] || break
        LC_PAUSED=1      # back from the source screen: the choice, not counting
    done
    attended_stop
    rm -f "$LC/screen" "$LC/boot" "$RUN_DIR/attended.round"
    log "local clone: going on from the server"
}

lc_boot_choice() {
    # Sets LC_CHOICE (server|local|pull). The countdown is the agent's: the GUI
    # only draws the deadline written here, so a frozen screen cannot hold
    # the machine -- it is the server when the time is up.
    LC_CHOICE=server
    rm -f "$LC/boot.decision"
    if [ "${LC_PAUSED:-0}" != 0 ]; then echo "0|$LC_PAUSED" > "$LC/boot"
    else _bc_dl=0; _bc_w0=$(date +%s); echo "0|0" > "$LC/boot"; fi   # 0: counted from its first frame (lc_boot_due)
    echo boot > "$LC/screen"
    log "local clone: boot choice offered -- the ${LC_BOOT_S}s count starts with its first frame on the screen"
    attended_start "Local clone screen: from the server, or a local clone"; lc_quiet_reset
    while :; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        lc_gui_alive || { log "local clone: the GUI is gone -- from the server"; return 0; }
        lc_power_check
        if lc_round_calls; then log "local clone: a round is open for this machine -- from the server"; return 0; fi   # #1560
        _bc_d=""; [ -f "$LC/boot.decision" ] && { _bc_d=$(cat "$LC/boot.decision"); rm -f "$LC/boot.decision"; }
        case "$_bc_d" in
            server) log "local clone: 'from the server' chosen"; return 0 ;;
            local) [ "$(cat "$LC/boot.local" 2>/dev/null)" = 1 ] || { log "local clone: 'local clone' refused -- one disk"; continue; }
                   log "local clone: 'local clone' chosen"; LC_CHOICE=local; return 0 ;;
            pull) log "local clone: 'restore an image from the server' chosen"; LC_CHOICE=pull; return 0 ;;
            pause) [ "$LC_PAUSED" = 1 ] || log "local clone: touched -- the countdown stops"
                   LC_PAUSED=1; echo "0|1" > "$LC/boot"; lc_quiet_reset ;;
        esac
        if [ "$LC_PAUSED" != 1 ] && lc_boot_due; then
            log "local clone: no touch for ${LC_BOOT_S}s -- from the server"; return 0
        fi
        if [ "$LC_PAUSED" = 1 ] && lc_quiet_over; then
            log "local clone: no touch for ${_lq_max}s on the paused choice -- from the server"; return 0
        fi
        sleep 1
    done
}

lc_session() {
    # The local flow until the person leaves it. Sets LC_END (back), or does
    # not return (power off).
    LC_END=""; identify_watch_stop; smart_selftest_cancel   # #1966/#1970: no read/self-test under a copy
    rm -rf "$LC/run"; rm -f "$LC/measure.req" "$LC/start.req" "$LC/stop.req" "$LC/end.req"
    echo idle > "$LC/phase"; echo local > "$LC/screen"
    attended_start "Local clone at the machine -- not from the server"
    lc_inventory; lc_quiet_reset
    while :; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        lc_gui_alive || { log "local clone: the GUI is gone -- leaving the local flow"; LC_END=back; return 0; }
        lc_power_check
        [ "$(cat "$LC/phase")" = idle ] && lc_round_calls && { log "local clone: a round is open for this machine -- leaving the local screen"; LC_END=round; return 0; }   # #1560
        if [ -f "$LC/measure.req" ]; then
            _ss_m=$(cat "$LC/measure.req"); rm -f "$LC/measure.req"; lc_quiet_reset
            if [ -f "$LC/inv/$_ss_m.line" ]; then echo "$_ss_m" > "$LC/source"; lc_measure "$_ss_m"; fi
        fi
        if [ -f "$LC/start.req" ]; then
            IFS='|' read -r _ss_s _ss_t _ss_k < "$LC/start.req"; rm -f "$LC/start.req"
            lc_run "$_ss_s" "$(printf '%s' "$_ss_t" | tr ',' ' ')" "$_ss_k"
            # #1367: a machine that was writing is not powered off from afar, not
            # even after: a request that came during the copy is dropped, aloud.
            # One the operator confirmed over the warning is carried out during
            # the copy by the beat (lc_power_during_copy); this one was not.
            [ -s "$RUN_DIR/power.resp" ] && { log "local clone: a power request came during the copy, not confirmed -- not carried out"; rm -f "$RUN_DIR/power.resp"; }
            lc_quiet_reset   # the count starts when the result is on the screen
            [ "$(cat "$LC/phase")" != "done" ] || lc_end_count || :   # #1560: failed too; stopped by hand: the choice
            # stopped (the mockup: back to choosing): the drawers are read again --
            # what is on the targets now is half a copy, and the screen must say so
            if [ "$(cat "$LC/phase")" = stopped ]; then
                rm -rf "$LC/run"; echo idle > "$LC/phase"; lc_inventory
                _ss_m=$(cat "$LC/source" 2>/dev/null)   # the screen keeps its choice: measure it again
                [ -f "$LC/inv/$_ss_m.line" ] && lc_measure "$_ss_m"
            fi
        fi
        if [ -f "$LC/end.req" ]; then
            _ss_e=$(cat "$LC/end.req"); rm -f "$LC/end.req"; lc_quiet_reset
            case "$_ss_e" in
                poweroff) lc_poweroff ;;
                reboot) lc_reboot ;;   # #1914: the USB stick's result screen (usbclone.sh)
                again) rm -rf "$LC/run"; rm -f "$LC/stop.req"; echo idle > "$LC/phase"; lc_inventory ;;
                back) LC_END=back; rm -rf "$LC/run"; echo idle > "$LC/phase"; return 0 ;;
            esac
        fi
        if lc_quiet_over; then
            log "local clone: no touch for ${_lq_max}s on the local clone screen -- from the server"
            LC_END=quiet; rm -rf "$LC/run"; echo idle > "$LC/phase"; return 0
        fi
        sleep 1
    done
}

lc_run_total() {
    # $1 = manifest. The bytes the copy reads: the used blocks, swap 0. #1440: one
    # partition not measured makes the total not known (null) -- its size is not
    # what partclone reads, and a total built on it ran the time left 2.5x long.
    jq -r 'if any(.partitions[]; .fs != "swap" and .used_bytes == null) then null
        else [.partitions[] | if .fs == "swap" then 0 else .used_bytes end] | add end' "$1"
}

lc_run() {
    # $1 = source, $2 = targets (words), $3 = 1 when the confirm dialog showed
    # and the person accepted a temporary shrink of the source.
    _lr_src="$1"; _lr_k="$3"; _lr_r="$LC/run"
    rm -rf "$_lr_r" "$LC/stop.req"; mkdir -p "$_lr_r"; : > "$_lr_r/targets"
    # The cloner's dead-man (watchdog.sh, 600 s) must not reboot the machine
    # in the middle of ntfsresize on the source's RAM view (#1466): shrinking
    # a big volume has no byte counter to beat on. A person started this
    # and is at the machine; the copy's own waits still end a stalled stream.
    ( while :; do if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi; sleep 20; done ) < /dev/null > /dev/null 2>&1 &
    _lr_beat=$!
    echo copying > "$LC/phase"; date +%s > "$_lr_r/started"; echo "$_lr_src" > "$_lr_r/src"
    for _lr_t in $2; do target_init "$_lr_t" 0; rm -f "$RUN_DIR/targets/$_lr_t/lc_ok" "$RUN_DIR/targets/$_lr_t/lc_table" "$RUN_DIR/targets/$_lr_t/lc_part" "$RUN_DIR/targets/$_lr_t/ntfs_note" "$RUN_DIR/targets/$_lr_t/rescued"; echo "$_lr_t" >> "$_lr_r/targets"; done
    _lr_all=$(tr '\n' ' ' < "$_lr_r/targets"); _lr_small=""
    # shellcheck disable=SC2086 # #1914: the clone stick snapshots its disks' serials (RAM, usbdiag.sh)
    log "local clone: $_lr_src -> $_lr_all"; [ "${IMAGESRV_MODE:-}" != usbclone ] || usb_serials_snap "$_lr_src" $_lr_all   # #1914 (RAM)
    # Fresh evidence, not the screen's 2-second-old picture (principle 5/7).
    if [ "$(lc_inv_field "$_lr_src" 12)" != 1 ] || [ "$(_lc_clean "$(disk_serial "$_lr_src")")" != "$(lc_inv_field "$_lr_src" 4)" ]; then
        _fail_alive "$_lr_all" "דיסק המקור אינו מה שנבדק — חזרו ובחרו שוב. לא נכתב דבר"; lc_end; return 1
    fi
    [ -f "$LC/measure/$_lr_src" ] && [ "$(cat "$LC/source" 2>/dev/null)" = "$_lr_src" ] || lc_measure "$_lr_src"
    for _lr_t in $_lr_all; do
        if [ "$_lr_t" = "$_lr_src" ] || [ ! -f "$LC/inv/$_lr_t.line" ] || [ "$(_lc_clean "$(disk_serial "$_lr_t")")" != "$(lc_inv_field "$_lr_t" 4)" ]; then
            fail_written_target "$_lr_t" "היעד אינו מה שנבדק — לא נכתב"; continue
        fi
        smart_red "$_lr_t" && { fail_written_target "$_lr_t" "$(smart_red_he "$_lr_t") — לא נכתב"; continue; }   # #1732: never a target; #1742: why
        IFS='|' read -r _lr_v _lr_n _lr_w <<EOF
$(lc_verdict_for "$_lr_src" "$_lr_t")
EOF
        case "$_lr_v" in
            fits) ;;
            shrink) if [ "$_lr_k" = 1 ]; then _lr_small="$_lr_small $_lr_t"
                    else fail_written_target "$_lr_t" "נדרש כיווץ זמני של המקור, וחלון האישור לא הציג אותו — לא נכתב"; fi ;;
            short) fail_written_target "$_lr_t" "${_lr_w:-לא נכנס: חסרים $(lc_gb_short "$_lr_n" "$(lc_inv_field "$_lr_t" 5)")} — לא נכתב" ;;   # #1354: the reason when it is the free space
            *) fail_written_target "$_lr_t" "לא נבדק אם נכנס${_lr_w:+: $_lr_w} — לא נכתב" ;;
        esac
    done
    _any_alive "$_lr_all" || { lc_end; return 1; }
    lc_source_parts "$_lr_src" || { _fail_alive "$_lr_all" "$LC_ERROR — לא נכתב דבר"; lc_end; return 1; }
    rm -f "$RUN_DIR/parts.orig.txt" "$RUN_DIR/targets/$_lr_src/shrunk"
    if [ -n "$_lr_small" ]; then
        echo shrinking > "$LC/phase"
        lc_shrink_source "$_lr_src" "$_lr_small" \
            || for _lr_t in $_lr_small; do fail_written_target "$_lr_t" "המקור לא כווץ, ולכן היעד לא נכתב: $LC_ERROR"; done
        echo copying > "$LC/phase"
    fi
    _any_alive "$_lr_all" || { lc_end; return 1; }
    lc_manifest "$_lr_src" "$_lr_r/parts.txt" || { _fail_alive "$_lr_all" "${LC_ERROR:-לא הצלחנו לתאר את המקור}"; lc_end; return 1; }
    lc_run_total "$_lr_r/manifest.json" > "$_lr_r/total" 2>> "$LOG_FILE"; lc_ntfs_source "$_lr_src" "$_lr_r/manifest.json"   # #2009: the evidence, logged
    for _lr_t in $_lr_all; do
        [ "$(cat "$RUN_DIR/targets/$_lr_t/state")" = failed ] && continue
        cp "$_lr_r/total" "$RUN_DIR/targets/$_lr_t/total"
        lc_prepare_target "$_lr_t" "$_lr_r/manifest.json"
    done
    # shellcheck disable=SC2086 # the target list is a word list on purpose
    _lr_c0=$(date +%s); lc_copy "$_lr_r/manifest.json" "$_lr_src" $_lr_all; _lr_rc=$?; echo $(( $(date +%s) - _lr_c0 )) > "$_lr_r/copy_s"
    # shellcheck disable=SC2086
    [ "$_lr_rc" = 2 ] || { echo verifying > "$LC/phase"; _lr_c0=$(date +%s); lc_finish "$_lr_r/manifest.json" $_lr_all
                           echo $(( $(date +%s) - _lr_c0 )) > "$_lr_r/verify_s"; }
    [ "$_lr_rc" = 2 ] && echo 1 > "$_lr_r/stopped"
    lc_end
}

lc_end() {
    # Whatever happened: the RAM views released (read back), the result, the
    # report. The source was never written (#1466 option B): nothing to grow back.
    _le_t0=$(date +%s)
    if [ -d "$_lr_r/snap" ] && ! lc_snap_teardown; then
        printf 'המקור לא נכתב, אבל העותק בזיכרון לא שוחרר (%s) — אתחלו את המחשב לפני שיכפול נוסף\n' "$(_lc_clean "$LC_SNAP_TD")" > "$_lr_r/source_restore"
    fi
    [ ! -f "$_lr_r/shrink_path" ] || echo $(( $(date +%s) - _le_t0 )) > "$_lr_r/restore_s"
    date +%s > "$_lr_r/ended"
    # shellcheck disable=SC2086 # #2067: all done = success, else failure (a failed source fails them all); stopped by hand = none
    [ -f "$_lr_r/stopped" ] || round_beep_result $_lr_all
    lc_report
    kill "$_lr_beat" 2>/dev/null; wait_pid "$_lr_beat" "$WAIT_HELPER_S" "local clone watchdog beat"
    # #1483: the result -- and its "כבה את המחשב" -- last: the loop that reads
    # end.req runs next. Before the report (curl, up to 10 s) the key waited
    # behind it, unseen, and the screen said "not reported" before it was tried.
    if [ -f "$_lr_r/stopped" ]; then echo stopped > "$LC/phase"; else echo "done" > "$LC/phase"; fi
}

lc_gb_short() { awk -v n="$1" -v h="$2" 'BEGIN { printf "%d GB", int((n - h + 999999999) / 1e9) }'; }

lc_report() {
    # #1322 D: best effort, labelled -- the copy did not pass through the
    # server, the machine reports what it did when the server answers.
    # Nothing here can change the result on the screen.
    _rp_r="$LC/run"; _rp_t=""
    for _rp_d in $(cat "$_rp_r/targets" 2>/dev/null); do
        _rp_s=$(cat "$RUN_DIR/targets/$_rp_d/state" 2>/dev/null); _rp_e=$(cat "$RUN_DIR/targets/$_rp_d/error" 2>/dev/null); _rp_id=$(target_ident "$_rp_d"); _rp_pt=${_rp_id#*|}   # #2045: kept at target_init -- a pulled disk has no port in sysfs now
        _rp_t="$_rp_t${_rp_t:+,}{\"dev\":\"$_rp_d\",\"port\":$(_lc_num "${_rp_pt%%|*}"),\"serial\":\"$(json_escape "${_rp_id%%|*}")\",\"state\":\"$_rp_s\",\"error\":\"$(json_escape "$_rp_e")\",\"ntfs_note\":\"$(json_escape "$(cat "$RUN_DIR/targets/$_rp_d/ntfs_note" 2>/dev/null)")\"}"
    done
    # #1466: which shrink ran, the view's release, and the stage times (P1) --
    # null = not measured, never 0.
    _rp_p=$(cat "$_rp_r/shrink_path" 2>/dev/null); case "$_rp_p" in snapshot|refused) _rp_p="\"$_rp_p\"" ;; *) _rp_p=null ;; esac
    _rp_x=""; for _rp_k in info_s resize_s relocations_mb copy_s verify_s restore_s; do
        _rp_x="$_rp_x,\"$_rp_k\":$(_lc_num "$(cat "$_rp_r/$_rp_k" 2>/dev/null)")"; done
    printf '{"mac":"%s","source":{"dev":"%s","port":%s,"serial":"%s"},"targets":[%s],"shrunk":%s,"source_restore":"%s","stopped":%s,"bytes":%s,"elapsed_s":%s,"shrink_path":%s,"snapshot_teardown":"%s"%s}\n' \
        "${MAC:-}" "$_lr_src" "$(_lc_num "$(disk_port "$_lr_src")")" "$(json_escape "$(disk_serial "$_lr_src")")" "$_rp_t" \
        "$([ -f "$_rp_r/shrunk" ] && echo true || echo false)" "$(json_escape "$(cat "$_rp_r/source_restore" 2>/dev/null)")" \
        "$([ -f "$_rp_r/stopped" ] && echo true || echo false)" "$(_lc_num "$(cat "$_rp_r/total" 2>/dev/null)")" \
        "$(( $(cat "$_rp_r/ended") - $(cat "$_rp_r/started") ))" "$_rp_p" \
        "$(json_escape "$(cat "$_rp_r/snap_teardown" 2>/dev/null)")" "$_rp_x" > "$_rp_r/report.json"
    [ "${IMAGESRV_MODE:-}" != usbclone ] || { usb_report_save "$_rp_r"; return 0; }   # #1914: no server -- on the screen and the stick
    _rp_c=$(curl -s -o "$_rp_r/report.resp" -w '%{http_code}' --max-time 10 -H "Content-Type: application/json" \
        --data-binary "@$_rp_r/report.json" "${SERVER:-http://0.0.0.0}/api/v1/agent/local-clone" 2>/dev/null)
    if [ "$_rp_c" = 200 ]; then echo sent > "$_rp_r/report"; log "local clone: reported to the server journal"
    else echo "failed|${_rp_c:-000}" > "$_rp_r/report"; lc_report_keep; log "local clone: the server did not take the report (http ${_rp_c:-000}) -- the copy stands, kept to send again"; fi
}

lc_poweroff() {
    # "כבה את המחשב": the person asked. Wake-on-LAN first when it can be
    # armed (#587: a remote power-on needs it), then off -- by request.
    # #1483: never a key that does nothing. Arming and sync take a while (a
    # dead disk), so the screen says so first; a refusal says why, and stays.
    _lc_req run/notice "מכבה את המחשב…"
    safe_poweroff "local clone: operator request" && return 0
    if [ "$POWEROFF_WHY" = wol ]; then
        _lc_req run/notice "המחשב לא כובה: Wake-on-LAN לא נדרך בכרטיס הרשת, ובלעדיו אי אפשר להדליק אותו מרחוק. כבו אותו בכפתור ההפעלה."
    else
        _lc_req run/notice "המחשב לא כובה: פקודת הכיבוי חזרה והמחשב עדיין דולק. כבו אותו בכפתור ההפעלה."
    fi
}
