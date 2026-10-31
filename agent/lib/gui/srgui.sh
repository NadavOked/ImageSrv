# srgui.sh -- #1561: "שחזור לדיסק הזה" on the native GUI. POSIX sh.
#
# Metal 17/10: the bridge releases the display for the write (guiparent.sh),
# so the whole restore was English log lines and curl errors on a bare tty --
# no Hebrew font there (ui.sh) -- while the other writes of this product have
# a progress screen. Owner, 17/10: "no screen that is not graphic in our
# design". So the GUI comes back the moment the bridge lets go -- one process,
# `--screen progress` (#st-progress, the card a machine shows with no
# operator; native-gui README) -- and stays until the machine powers off.
#
# No new channel: the GUI's state file of key=value records, built from the
# files the write already keeps (progress.sh) -- target_bytes (the stream's
# bytes into the disk), targets/<disk>/total (the manifest's total), the
# partition the restore loop is on (restore.sh), and cloner_gui_pace's
# average rate (#410). Nothing is estimated before it was measured.
#
# The writer is a subshell, so all it shows comes from files in $RUN_DIR/gui:
#   restore.head   disk / title / disk label / partitions / name (sr_gui_start)
#   restore.done   the write succeeded (sr_gui_done)
#   restore.end    the epoch of the power-off (sr_end_poweroff)
#   restore.final  a last card -- a refusal or a failure -- for good
#   restore.out    the GUI's stdout: "end-poweroff" is "כבה עכשיו"
# No GUI binary, or a GUI that will not start: the console lines stay.

SR_GUI_BIN="${SR_GUI_BIN:-/usr/bin/imagesrv-station-gui}"
SR_GUI_TICK_S="${SR_GUI_TICK_S:-1}"     # the state is re-written this often
SR_POWEROFF_S="${SR_POWEROFF_S:-20}"    # Nadav 17/10: after a success, then off
SR_RLM=$(printf '\342\200\217')         # U+200F: "SATA 0 ..." would lay the line out LTR (#1502)

sr_disk_label() {
    # $1 = disk. "דיסק N" by the drawer mapping (#1495) -- the restore
    # screen's own number; not in the mapping: its connection "(לא ממופה)";
    # neither: the device name. Never a guessed number.
    _sl_p=$(disk_port "$1"); case "$_sl_p" in ''|*[!0-9]*) _sl_p=0 ;; esac
    drawer_slots_refresh || :
    _sl_m=$(disk_drawer "$1" "$_sl_p"); _sl_n=${_sl_m%%|*}; _sl_c=${_sl_m#*|}
    if [ -n "$_sl_n" ] && [ "$_sl_n" != 0 ]; then printf 'דיסק %s' "$_sl_n"
    elif [ -n "$_sl_c" ]; then printf '%s (לא ממופה)' "$_sl_c"
    else printf '%s' "$1"; fi
}

sr_pace_text() {
    # From cloner_gui_pace: _cg_rate (bytes/s), _cg_eta (s); -1 = not measured
    # -- said in words, never drawn as 0 (principle 5); 0 = measured still.
    [ "${_cg_rate:--1}" != 0 ] || { printf 'ממתין לזרם מהשרת · 0 MB/s'; return 0; }   # #2055: measured still
    [ "${_cg_rate:--1}" -gt 0 ] || { printf 'מודד את הקצב…'; return 0; }
    awk -v r="$_cg_rate" -v e="${_cg_eta:--1}" 'BEGIN {
        printf "%.1f MB/s · ", r / 1000000
        if (e < 0) printf "הזמן שנותר לא ידוע"
        else if (e < 60) printf "פחות מדקה נותרה"
        else if (e < 90) printf "פחות מ-2 דק׳ נותרו"
        else printf "כ-%d דק׳ נותרו", int(e / 60 + 0.5) }'
}

sr_gui_state() {
    # The #st-progress records: title= (the image), sub= (the disk by the
    # mapping, partition N of M, rate, time left), pct=, bytes=, moving=,
    # updated=. No partition= on purpose: the card would add "(בלוקים)", the
    # capture's block axis -- this pct is the stream's bytes.
    _ss_g="$RUN_DIR/gui"
    # the header's machine, server and versions -- from the hello answer, as the cloner's screen
    if command -v gui_header_state >/dev/null 2>&1; then gui_header_state "$RUN_DIR/response.json" 2>/dev/null || :; fi
    [ ! -s "$RUN_DIR/power.ask" ] || printf 'power_ask=%s\n' "$(tr -d '\r\n' < "$RUN_DIR/power.ask")"   # #1918 (powerbtn.sh)
    if command -v gui_ask_state >/dev/null 2>&1; then GUI_DIR=$_ss_g gui_ask_state || :; fi   # #1898: sr_ask's question; none drawn = no answer = not written
    if [ -s "$_ss_g/restore.final" ]; then cat "$_ss_g/restore.final"; cat "$RUN_DIR/fail.state" 2>/dev/null; return 0; fi   # #2068: the hold's screen (failscreen.sh), with its buttons
    _ss_d=; _ss_title="שחזור אימג׳ מהשרת"; _ss_lbl=; _ss_parts=0; _ss_name=
    [ -s "$_ss_g/restore.head" ] && { read -r _ss_d; read -r _ss_title; read -r _ss_lbl; read -r _ss_parts; read -r _ss_name; } < "$_ss_g/restore.head"
    case "$_ss_parts" in ''|*[!0-9]*) _ss_parts=0 ;; esac
    if [ -z "$_ss_d" ]; then   # the handoff's checks: the image and the session
        printf 'title=%s%s\nsub=%sבודק את האימג׳ ואת ההרשאה מול השרת — הדיסק עוד לא שונה\npct=0\nbytes=0\nmoving=0\nupdated=%s\n' \
            "$SR_RLM" "$_ss_title" "$SR_RLM" "$(date +%s)"; return
    fi
    _ss_t="$RUN_DIR/targets/$_ss_d"
    _ss_b=$(target_bytes "$_ss_d"); case "$_ss_b" in ''|*[!0-9]*) _ss_b=0 ;; esac
    if [ -f "$_ss_g/restore.done" ] || [ -f "$_ss_g/restore.cancelled" ]; then
        _ss_w="מכבה את המחשב…"; _ss_e=$(cat "$_ss_g/restore.end" 2>/dev/null); _ss_l=0
        case "$_ss_e" in ''|*[!0-9]*) _ss_w="האימג׳ נכתב לדיסק"; _ss_e= ;;
            *) _ss_l=$((_ss_e - $(date +%s))); [ "$_ss_l" -le 0 ] || _ss_w="המחשב יכבה בעוד $_ss_l שניות" ;; esac
        if [ -f "$_ss_g/restore.cancelled" ]; then   # #1561 (Nadav 17/10): cancelled, the table wiped
            [ -n "$_ss_e" ] || _ss_w="טבלת המחיצות נמחקה"
            printf 'message=השחזור בוטל — הדיסק ריק|%s%s · %s\n' "$SR_RLM" "$_ss_lbl" "$_ss_w"; return
        fi
        # #1560's count dialog (its button says end-poweroff); a GUI without it ignores the record
        [ "$_ss_l" -le 0 ] || printf 'end_poweroff=%s|restore\n' "$_ss_e"
        printf 'title=%sהשחזור הושלם%s\nsub=%s%s · %s\npct=100\nbytes=%s\nmoving=1\nupdated=%s\n' \
            "$SR_RLM" "${_ss_name:+: $_ss_name}" "$SR_RLM" "$_ss_lbl" "$_ss_w" "$_ss_b" "$(date +%s)"
        return
    fi
    _ss_z=$(cat "$_ss_t/total" 2>/dev/null); case "$_ss_z" in ''|*[!0-9]*) _ss_z=0 ;; esac
    _ss_p=-1; [ "$_ss_z" -gt 0 ] && { _ss_p=$((_ss_b * 100 / _ss_z)); [ "$_ss_p" -le 100 ] || _ss_p=100; }
    _ss_ts=$(cat "$_ss_t/state" 2>/dev/null)
    _ss_n=$(cat "$_ss_t/partition" 2>/dev/null); case "$_ss_n" in *[!0-9]*) _ss_n= ;; esac
    cloner_gui_pace "$_ss_d" "$_ss_ts" "$_ss_b" "$_ss_z"
    _ss_c=0   # #1561: "ביטול השחזור" -- while the write can still be stopped, and not asked yet
    if [ -f "$_ss_g/restore.cancel" ]; then _ss_w="מבטל: עוצר את הכתיבה ומוחק את טבלת המחיצות…"
    elif [ "$_ss_ts" = "done" ]; then _ss_w="נכתב — מסיים: דרייברים ודיווח לשרת"
    elif [ "$_ss_ts" = failed ]; then _ss_w="הכתיבה נעצרה"
    elif [ "$_ss_ts" = verifying ] || { [ "$_ss_ts" = writing ] && [ "$(cat "$RUN_DIR/state" 2>/dev/null)" = verifying ]; }; then
        _ss_w="כל המחיצות נכתבו — קורא בחזרה מהדיסק, מרחיב ובודק את מערכת הקבצים"   # #1805: the read-back first
    elif [ "$_ss_ts" = writing ] && [ -n "$_ss_n" ]; then
        _ss_w="מחיצה $_ss_n"; [ "$_ss_parts" -gt 0 ] && _ss_w="$_ss_w מתוך $_ss_parts"
        _ss_w="$_ss_w · $(sr_pace_text)"; _ss_c=1
    else _ss_w="מכין את הדיסק: בדיקות וטבלת המחיצות"; _ss_c=1; fi
    [ "$_ss_c" = 0 ] || echo "cancel=1"
    printf 'title=%s%s\nsub=%s%s · %s\npct=%s\nbytes=%s\nmoving=%s\nupdated=%s\n' \
        "$SR_RLM" "$_ss_title" "$SR_RLM" "$_ss_lbl" "$_ss_w" "$_ss_p" "$_ss_b" \
        "$([ "$_ss_b" -gt 0 ] && echo 1 || echo 0)" "$(date +%s)"
}

sr_gui_open() {
    # The GUI and its one writer, from the bridge's handoff on. $1 = fresh:
    # a new restore -- the files of a previous one go. 0 = the GUI process
    # was started (or already runs). One writer, renamed into place: never
    # half a picture.
    sr_gui_alive && return 0
    [ -x "$SR_GUI_BIN" ] || return 1
    _so_g="$RUN_DIR/gui"; mkdir -p "$_so_g" || return 1
    [ "${1:-}" != fresh ] || rm -f "$_so_g/restore.head" "$_so_g/restore.done" "$_so_g/restore.end" "$_so_g/restore.final" \
        "$_so_g/restore.cancel" "$_so_g/restore.cancelled"
    sr_gui_state > "$_so_g/restore.state" || return 1
    : > "$_so_g/restore.out" || return 1
    # #1726: the writer keeps the GUI: one that dies (a crash, SIGKILL; 0 is its own end, "כבה עכשיו")
    # is started again on the next tick, GUI_RESPAWN_MAX times, then the static screen. Its rc: restore.rc (no bare wait).
    ( _sk_n=0; while :; do
          rm -f "$_so_g/restore.rc"; : > "$_so_g/beat"   # #1946: each GUI's start grace -- before the &, the loop looks at once (#2112)
          # shellcheck disable=SC2046 # --backend/--fb-file are words. nice: the write wins the CPU (#1493); restore.gpid = the GUI (nice execs it)
          { FONTCONFIG_FILE=/etc/imagesrv/fonts.conf nice -n 10 "$SR_GUI_BIN" --screen progress $(gui_backend_args) \
              --mac "$MAC" --ip "${IP:-}" --state "$_so_g/restore.state" & echo "$!" > "$_so_g/restore.gpid"; wait "$!"; echo "$?" > "$_so_g/restore.rc"; } >> "$_so_g/restore.out" 2>> "$LOG_FILE" & _sk_g=$!
          while kill -0 "$_sk_g" 2>/dev/null; do
              sr_gui_state > "$_so_g/restore.next" && mv "$_so_g/restore.next" "$_so_g/restore.state"
              gui_beat_fresh || { log "restore: native GUI heartbeat older than ${GUI_BEAT_MAX_S}s (stopped or wedged) -- killing it"
                                  kill -9 "$(cat "$_so_g/restore.gpid" 2>/dev/null)" 2>/dev/null; }
              sleep "$SR_GUI_TICK_S"
          done
          wait_pid "$_sk_g" "$WAIT_HELPER_S" "restore GUI" || :; [ "$(cat "$_so_g/restore.rc" 2>/dev/null)" != 0 ] || exit 0
          _sk_n=$((_sk_n + 1)); [ "$_sk_n" -le "${GUI_RESPAWN_MAX:-3}" ] ||
              { gui_gone "restore: the GUI died again after ${GUI_RESPAWN_MAX:-3} restarts -- the static screen instead"; exit 1; }
          log "restore: the GUI died -- starting it again ($_sk_n of ${GUI_RESPAWN_MAX:-3})"
      done ) & SR_GUI_PID=$!   # the keeper
    echo "$SR_GUI_PID" > "$RUN_DIR/gui.pid"   # the GUI that owns the display now (#1569 reads it)
    log "restore: on the GUI ($SR_GUI_PID)"
}

sr_gui_start() {
    # $1 = manifest file, $2 = disk. The progress card's head, then the GUI
    # if the handoff did not bring it up. 1 = no GUI to show it on.
    _sg_g="$RUN_DIR/gui"; mkdir -p "$_sg_g" || return 1
    rm -f "$_sg_g/pace.$2" "$RUN_DIR/targets/$2/partition" "$_sg_g/restore.cancel" "$_sg_g/restore.cancelled" "$RUN_DIR/power.stop"
    echo "$(date +%s)-$2" > "$_sg_g/restore.id" || return 1   # #1918: this restore's run id -- the power button's stop names it
    _sg_n=$(json_get "$1" ".name" | tr -d '\r\n' | tr '|' ' '); [ "$_sg_n" != null ] || _sg_n=
    # title= is 128 bytes in the GUI: a longer name is left out, not cut mid-letter
    [ "$(printf '%s' "$_sg_n" | wc -c)" -le 90 ] || _sg_n=
    _sg_t="שחזור אימג׳ מהשרת"; [ -z "$_sg_n" ] || _sg_t="שחזור: $_sg_n"
    _sg_p=$(json_get "$1" ".partitions | length"); case "$_sg_p" in ''|*[!0-9]*) _sg_p=0 ;; esac
    printf '%s\n%s\n%s\n%s\n%s\n' "$2" "$_sg_t" "$(sr_disk_label "$2")" "$_sg_p" "$_sg_n" > "$_sg_g/restore.head.next" &&
        mv "$_sg_g/restore.head.next" "$_sg_g/restore.head" || return 1
    sr_gui_open ""
}

sr_gui_alive() { [ -n "${SR_GUI_PID:-}" ] && kill -0 "$SR_GUI_PID" 2>/dev/null; }

sr_gui_done() {
    # The success card. It stays until the power-off (sr_end_poweroff).
    : > "$RUN_DIR/gui/restore.done" 2>/dev/null || :
    sr_gui_alive && log "restore: done -- shown on the GUI"
}

sr_gui_message() {
    # $1 = title, $2 = the reason -- #1545's message card (message= routes
    # over --screen progress, app_route), for good. Onto the GUI that shows
    # the restore; without one, a GUI is started for it. 1 = no GUI at all.
    _gm_r=$(printf '%s' "$2" | tr -d '\r\n|')
    # msg_sub is 200 bytes in the GUI; a longer reason would be cut mid-letter.
    if [ -z "$_gm_r" ] || [ "$(printf '%s' "$_gm_r" | wc -c)" -gt 190 ]; then _gm_r="הסיבה המלאה ביומן הסוכן ובקונסולה"; fi
    mkdir -p "$RUN_DIR/gui" || return 1
    # RLM: a reason that opens in Latin ("sgdisk ...", "Wake-on-LAN ...") would lay the line out LTR (#1502)
    printf 'message=%s|%s%s\n' "$1" "$SR_RLM" "$_gm_r" > "$RUN_DIR/gui/restore.final.next" &&
        mv "$RUN_DIR/gui/restore.final.next" "$RUN_DIR/gui/restore.final" || return 1
    sr_gui_open ""
}

sr_refused() {
    # $1 = why, a key -- its Hebrew is here, the screen's only (the callers'
    # files print no Hebrew: no font on the tty); $2 = in English (the journal).
    # Refused or failed after the handoff and BEFORE a byte was written: the
    # operator pressed "שחזר", the screen went, an English FATAL line and a
    # reboot. Now the reason is shown and held long enough to read, then the
    # fail-safe reboot as before (die_local). Never returns.
    case "$1" in
        malformed) _rf="המסך שלח מזהה אימג׳ פגום" ;;
        session) _rf="הכניסה לקונסולה פגה או לא אומתה — היכנסו שוב ונסו שוב" ;;
        not-allowed) _rf="האימג׳ כבר אינו מותר למחשב הזה" ;;
        no-image) _rf="לא נבחר אימג׳" ;;
        manifest) _rf="לא ניתן לקרוא מהשרת את פרטי האימג׳" ;;
        no-disk) _rf="לא נמצא דיסק פנימי במחשב" ;;
        many-disks) _rf="במחשב יותר מדיסק פנימי אחד — לא משחזרים בניחוש" ;;   # #1807
        share) _rf="חלוקת המקום בין המחיצות לא אושרה" ;; share-plan) _rf="לא הצלחנו לחשב את חלוקת המקום בין המחיצות" ;;   # #1898 (single_restore.sh)
        smart-red|readonly) _rf="$SMART_RED_HE"; [ "$1" = smart-red ] || _rf="$SMART_RO_HE" ;;   # #1732, #1742 (smart.sh)
        *) _rf="הסיבה ביומן הסוכן ובקונסולה" ;;
    esac
    if sr_gui_message "השחזור לא התחיל — לא נכתב דבר" "$_rf · המחשב יופעל מחדש"; then
        [ "${IMAGESRV_TEST:-0}" = 1 ] || sleep "${GUI_FAIL_SHOW_S:-10}"
    fi
    die_local "$2"
}

sr_end_poweroff() {
    # Nadav 17/10: a restore that succeeded -- or was cancelled, its table
    # wiped -- ends powered off, never a reboot (it did one until then):
    # SR_POWEROFF_S on the card, or
    # at once on "כבה עכשיו" (the GUI says end-poweroff on its stdout). The
    # power-off is finish_and_stop -> safe_poweroff (WoL armed first, #587).
    # Returns only when the machine stayed on; the screen then says why.
    _ep_at=$(( $(date +%s) + SR_POWEROFF_S ))
    echo "$_ep_at" > "$RUN_DIR/gui/restore.end" 2>/dev/null || :
    _ep_k="השחזור הושלם"; [ ! -f "$RUN_DIR/gui/restore.cancelled" ] || _ep_k="השחזור בוטל"
    log "restore: ended -- powering off in ${SR_POWEROFF_S}s, or at once on 'כבה עכשיו'"
    _ep_n=0
    while [ "$_ep_n" -lt "$SR_POWEROFF_S" ]; do
        { grep -qx 'end-poweroff' "$RUN_DIR/gui/restore.out" 2>/dev/null || power_stop_for_restore "$(cat "$RUN_DIR/gui/restore.id" 2>/dev/null)"; } && { log "restore: 'כבה עכשיו' pressed (or the power button's stop, #1918)"; rm -f "$RUN_DIR/power.stop"; break; }
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        sleep 1; _ep_n=$((_ep_n + 1))
    done
    date +%s > "$RUN_DIR/gui/restore.end" 2>/dev/null || :   # the card: "מכבה את המחשב…"
    finish_and_stop && return 0
    # safe_poweroff refused (#1483): power.fail says why; a WoL refusal does not write it.
    _ep_w=$(cat "$RUN_DIR/power.fail" 2>/dev/null)
    [ -n "$_ep_w" ] || _ep_w="Wake-on-LAN לא נדרך בכרטיס הרשת, ובלעדיו אי אפשר להדליק את המחשב מרחוק. כבו אותו בכפתור ההפעלה."
    sr_gui_message "$_ep_k — המחשב לא כובה" "$_ep_w" || :
    log "restore: ended -- the machine stayed on (${POWEROFF_WHY:-unknown})"
    return 1
}

# --- "ביטול השחזור" (#1561, Nadav 17/10) -------------------------------------
# The progress card offers it (cancel=1) while the write can be stopped; the
# GUI asks with the red button (#1270) and says restore-cancel on its stdout.

sr_cancel_watch() {
    # $1 = image id, $2 = disk. In the background while run_restore runs. The
    # cancel ends the source reader -- the curl of this image's files -- and
    # everything downstream sees EOF, as the local clone's stop: the write
    # fails by itself, no process is left hanging. Retried every second: a
    # cancel during the disk's preparation waits for the first stream, and a
    # stream that will not die is caught after run_restore (sr_cancelled).
    while :; do
        if grep -qx 'restore-cancel' "$RUN_DIR/gui/restore.out" 2>/dev/null || power_stop_for_restore "$(cat "$RUN_DIR/gui/restore.id" 2>/dev/null)"; then   # #1918: "עצור וכבה" for THIS restore
            [ -f "$RUN_DIR/gui/restore.cancel" ] || { : > "$RUN_DIR/gui/restore.cancel"; log "restore: cancelled on the GUI -- stopping the stream"; }
            # a cancel is not a disk fault: no ATA evidence against the drive (fail_written_target, #1429)
            rm -f "$RUN_DIR/targets/$2/write_started"
            sr_stop_stream "$1"
        fi
        sleep 1
    done
}

sr_stop_stream() {
    # $1 = image id. The curl that pulls this image's files (http_get_stream),
    # found in /proc -- no pkill in every busybox. Nothing found: no stream yet.
    # The program itself must be curl (argv 0, or 1 under an interpreter): a
    # shell whose script merely mentions the URL is not the stream.
    for _sp in /proc/[0-9]*; do
        _sc=$( { tr '\0' '\n' < "$_sp/cmdline"; } 2>/dev/null | head -n 2 | tr '\n' ' ')
        case "$_sc" in curl\ *|*/curl\ *) ;; *) continue ;; esac
        { tr '\0' '\n' < "$_sp/cmdline"; } 2>/dev/null | grep -qF "/api/v1/images/$1/files/" || continue
        # said before the kill: the write ends at once, and this watcher with it
        log "restore: stopping the stream (curl ${_sp#/proc/})"; kill "${_sp#/proc/}" 2>/dev/null
    done
}

sr_cancel_requested() { [ -f "$RUN_DIR/gui/restore.cancel" ]; }

sr_cancelled() {
    # $1 = disk, $2 = the reporter's pid. Principle 4: an empty disk, never a
    # half-written one that looks whole -- the primary and backup GPT go. 0 =
    # wiped (sgdisk exit 0): the card says so, the caller counts to the
    # power-off. 1 = not wiped: said on the screen, the machine held for a person.
    if sgdisk --zap-all "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 ||
        sgdisk --zap-all "$DEVROOT/$1" >> "$LOG_FILE" 2>&1; then   # #1545: a damaged old table needs the second
        blockdev --rereadpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || log "$1: re-read of the wiped table failed (the kernel keeps the old view)"
        target_set "$1" failed "השחזור בוטל במחשב — טבלת המחיצות נמחקה, הדיסק ריק"
        : > "$RUN_DIR/gui/restore.cancelled"
        log "restore: cancelled -- $1: primary and backup GPT wiped"
        pull_close "$2" "$PULL_SESSION" "$MAC" "$SERVER" || :   # the console sees why (report_final)
        return 0
    fi
    target_set "$1" failed "השחזור בוטל, אבל מחיקת טבלת המחיצות נכשלה — הדיסק כתוב בחלקו"
    sr_gui_message "השחזור בוטל — מחיקת הטבלה נכשלה" "הדיסק כתוב בחלקו ועלול להיראות תקין. אל תאתחלו ממנו." || :
    log "WARNING: restore: cancelled, but $1's GPT could not be wiped"
    return 1
}
