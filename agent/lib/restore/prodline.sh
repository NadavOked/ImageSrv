# prodline.sh -- the production line on a cloner: the server's word, and a NEW disk pushed in mid-round (#3181).
# POSIX sh (busybox ash).
#
# A production line is a round that asks for more disks than the cloners have drawers (Nadav 14/11: "the same
# thing, with a few small things"). The server decides it alone and says so: `production_line` in the answer of
# every progress report and in the hello (interfaces.md sections 3-4). No word = a regular round, and everything
# here stands aside: another disk pushed in is said in the log, as before (#3064), and nothing is detached.
#
# Until here another disk (another serial) pushed in mid-round was a line in the log. In a production line it is
# a new TARGET of the round:
#
#   seen     prodline_seen, from drawerback.sh's look at the drawers (_db_strangers: at the kernel's word, and at
#            the round's fixed points). It must have a serial and a slot -- the server knows a disk by nothing
#            else. The disk the round had in that slot has left the machine (another one answers there): a
#            written one is reported `removed` first, and the server HEARD it; one still being written is the
#            watch's to name, and the new disk is asked about again at the next tick. The same for a written,
#            detached disk of ANOTHER slot whose device name the kernel gave the new one (a detach frees the name);
#   listed   prodline_apply, at drawer_back's own point -- the one place the round's list changes, with the
#            pass's watch stopped (drawerplug.sh). The target the slot had leaves the report (two targets in one
#            slot are two rows of one drawer for the screens and for the server). The new one gets a target of
#            its own (target_init: its identity bound, #2434) and a background job, as a drawer that came back.
#            Until it WRITES it is `unlisted`: a red or refused disk is never in the list a pass is handed;
#   gates    in the job, each one failing the disk alone and by name: no twin (#2448); SMART -- a red disk is
#            never a target (Nadav 18/10), skipped by its reason, and the gate's hello tells the server's
#            inventory of the disk; the server's WORD -- a report that carried the disk was answered, and the
#            answer does not refuse it (`refused`: the round has all the disks it asked for); the size, the plan
#            and the write check, as at a round's start (write_check_round);
#   written  TRIM, the table and the expansion (the steps of run_restore_drawers), then drawerback.sh's own
#            completion from partition 0 (_db_catch): the pass on the air, a neighbour, HTTP (drawerfill.sh).
#            From the moment it WRITES it is in the round's list, and the round's end is its end too: the
#            read-back, the name the server gave its serial (`names`, prodline_name) and done;
#   late     a new disk still completing when the round's own drawers are written does not hold them: they are
#            read back, named and detached (drawerout.sh) while it goes on, and it gets an end of its own
#            (prodline_finish). The machine stays in the wave -- and reports -- while a disk is being written,
#            and a disk pushed in meanwhile joins the same way. Nobody to hold = the round waits for it, as it
#            waits for a drawer that came back. With every disk finished and names left in the server's queue,
#            the machine waits there for the next disk instead of ending (linewait.sh).
#
# Evidence, not timing (principle 5): "the server heard" is a whole answer read a tick AFTER a report that carried
# the thing was seen in progress.json -- progress.sh empties the reply file the moment a body goes out, so that
# answer is to that body or to a later one. A reply that does not parse is being written: asked again.

PL_WORD_S="${PL_WORD_S:-60}"   # how long a new disk waits for the server's word about it

prodline_word() {   # -> the last production_line the server said (compact JSON). 1 = its answers say nothing of one: a regular round. 2 = not known right now
    _pw_f="$RUN_DIR/prodline.${D_SESSION_ID:-${CHUNK_SESSION:-0}}.json"; _pw_rc=1
    if [ -e "$RUN_DIR/progress.reply" ]; then   # the answer to the last report: whole and with a line, whole without one, or being written
        _pw=$(jq -c 'select(.ok == true) | .production_line // false' "$RUN_DIR/progress.reply" 2> /dev/null) || _pw=""
        case "$_pw" in
            '{'*) echo "$_pw" > "$_pw_f.new" && mv "$_pw_f.new" "$_pw_f" ;;
            '') _pw_rc=2 ;;   # not an answer about the line: the last word stands, and with none it is "not known" -- never "regular"
        esac
    fi
    if [ ! -s "$_pw_f" ]; then   # no report said it yet: the hello's (session.production_line)
        _pw=$(jq -ce '.session.production_line | objects' "${RESP:-/nonexistent}" 2> /dev/null) && [ -n "$_pw" ] || return "$_pw_rc"
        echo "$_pw" > "$_pw_f"
    fi
    cat "$_pw_f"
}
prodline_on() { prodline_word > /dev/null; }

prodline_name() {   # $1 = target -> the computer name the server gave its SERIAL. Nothing = none was said
    # The last word kept may be OLDER than the disk (an answer being written is not read, and nothing reads one while a new disk is
    # written): "not in that word" is not "no name" (principle 5) -- asked again for PL_NAME_S seconds, until a whole answer says it
    _pn_s=$(target_bound_serial "$1"); _pn_k=0
    until _pn=$(prodline_word | jq -r --arg s "$_pn_s" '.names[$s] // empty | strings' 2> /dev/null) && [ -n "$_pn" ]; do
        _pn_k=$((_pn_k + 1)); [ "$_pn_k" -le "${PL_NAME_S:-6}" ] || return 1; sleep 1
    done
    printf '%s' "$_pn"
}

_pl_heard() {   # $1 = target, $2 = mark, $3 = jq test of its entry in the report. 0 = a report that carried it was ANSWERED: PL_REPLY
    _ph_t="$RUN_DIR/targets/$1"
    if [ -e "$_ph_t/$2" ]; then PL_REPLY=$(jq -ce 'select(.ok == true)' "$RUN_DIR/progress.reply" 2> /dev/null) && [ -n "$PL_REPLY" ]; return; fi
    jq -e --arg s "$(target_bound_serial "$1")" ".targets[] | select(.serial == \$s) | $3" "$RUN_DIR/progress.json" > /dev/null 2>&1 \
        && : > "$_ph_t/$2"
    return 1
}

_pl_port() { _pp=$(cat "$RUN_DIR/targets/$1/seen" 2> /dev/null); _pp=${_pp%|*}; printf '%s' "${_pp##*|}"; }   # serial|port|ata: a serial may hold a '|'

_pl_out() {   # $1 = disk, $2 = serial, $3 = why: it does not join, said once
    echo "$1|$2" >> "$CHUNK_DIR/present.$CHUNK_SESSION.said"
    log "דיסק חדש הוכנס באמצע הסבב ($1, serial ${2:-לא ידוע}) -- פס ייצור, והוא אינו מצטרף: $3"
}

prodline_seen() {   # $1 = a disk that is not the round's, $2 = its serial. 1 = not a production line: said as before (drawerback.sh)
    prodline_on; case "$?" in 0) ;; 2) return 0 ;; *) return 1 ;; esac   # 2: the answer is being written -- asked again at the next tick
    [ ! -e "$CHUNK_DIR/new.$1" ] || return 0   # decided: it waits for the list's next point (prodline_apply)
    [ -n "$2" ] || { _pl_out "$1" "$2" "אין לו מספר סידורי"; return 0; }
    _ps_p=$(disk_port "$1"); case "$_ps_p" in ''|*[!0-9]*) _pl_out "$1" "$2" "החריץ שלו לא ידוע"; return 0 ;; esac
    _ps_old=""
    for _ps_t in "$RUN_DIR"/targets/*/; do   # the target this round had in that slot
        [ -f "$_ps_t/state" ] || continue; _ps_h=${_ps_t%/}; _ps_h=${_ps_h##*/}
        [ "$(_pl_port "$_ps_h")" = "$_ps_p" ] || continue
        case "$(cat "$_ps_t/state" 2> /dev/null)" in
            failed|skipped|cancelled) ;;
            done)   # written, and out: another disk answers in its slot. The server hears `removed` before it leaves the report
                [ -e "$_ps_t/removed" ] || { : > "$_ps_t/removed"; log "$_ps_h: הדיסק שנכתב הוצא מחריץ $_ps_p -- דיסק אחר עונה שם"; }
                _pl_heard "$_ps_h" removed.told '.removed == true' || return 0 ;;
            *) return 0 ;;   # the round's still, by its state: its pull is the watch's to name -- asked again
        esac
        _ps_old="$_ps_old $_ps_h"
    done
    case " $_ps_old " in
        *" $1 "*) ;;
        *)  # the kernel gave it the name of ANOTHER slot's target. One that was detached gave the name up by itself (drawerout.sh: its
            # node left the kernel, and the lowest free name is the next disk's), and leaves the report as a slot's own does: pulled,
            # and the server heard. Until then the new disk is asked about again. Any other target holds its name, and the disk is out
            _ps_t="$RUN_DIR/targets/$1"
            if [ -e "$_ps_t/detached" ]; then
                if [ -e "$_ps_t/removed" ] && _pl_heard "$1" removed.told '.removed == true'; then _ps_old="$_ps_old $1"
                else [ -e "$CHUNK_DIR/held.$1" ] || log "דיסק חדש הוכנס לחריץ $_ps_p ($1, serial $2): השם שהקרנל נתן לו היה של הדיסק הכתוב שבחריץ $(_pl_port "$1") -- הוא מצטרף כשהדיסק ההוא יוצא מהמגירה"
                    : > "$CHUNK_DIR/held.$1"; return 0; fi
            elif [ -e "$_ps_t" ]; then _pl_out "$1" "$2" "השם שהקרנל נתן לו שייך למגירה אחרת של הסבב"; return 0; fi ;;
    esac
    rm -f "$CHUNK_DIR/held.$1"; printf '%s\n%s\n%s\n' "$2" "$_ps_p" "${_ps_old# }" > "$CHUNK_DIR/new.$1" && PL_CHANGED=1
    return 0
}

prodline_poll() {   # a tick (drawerplug.sh): a new disk that left `waiting` -- it writes, or it is out -- is the list's business now
    _pq_f="$RUN_DIR/prodline.${D_SESSION_ID:-${CHUNK_SESSION:-0}}.new"; [ -s "$_pq_f" ] || return 0
    while read -r _pq; do
        [ "$(cat "$RUN_DIR/targets/$_pq/state" 2> /dev/null)" = waiting ] || PL_CHANGED=1
    done < "$_pq_f"
    return 0
}

_pl_retire() {   # $1 = a target whose slot another disk took: it left the machine, and leaves the round's report
    _pr_t="$RUN_DIR/targets/$1"
    log "המגירה $1 (serial $(target_bound_serial "$1"), $(cat "$_pr_t/state" 2> /dev/null)) יצאה מהמכונה -- דיסק אחר בחריץ שלה, והיא יוצאת מהדיווח של הסבב"
    [ ! -f "$_pr_t/catchup" ] || drawer_catchup_stop "$1"
    rm -rf "$_pr_t"
    _pr_l=""; for _pr_a in ${_all:-}; do [ "$_pr_a" = "$1" ] || _pr_l="$_pr_l $_pr_a"; done; _all="${_pr_l# }"
}

prodline_apply() {   # drawer_back's point, the watch stopped: the round's list changes here and nowhere else. PL_ADDED = targets to add
    PL_ADDED=""; _pa_q="$RUN_DIR/prodline.${D_SESSION_ID:-${CHUNK_SESSION:-0}}.new"; _pa_fresh=" "
    for _pa_f in "$CHUNK_DIR"/new.*; do
        [ -f "$_pa_f" ] || continue; _pa_n=${_pa_f##*/new.}
        { read -r _pa_s; read -r _pa_p; read -r _pa_old; } < "$_pa_f"; rm -f "$_pa_f"
        [ "$(disk_serial "$_pa_n")" = "$_pa_s" ] || continue   # gone again, or another one by now: seen anew
        for _pa_h in $_pa_old; do   # never a target this very pass listed under that name (two new disks, each under the other's old name), nor one already out
            case "$_pa_fresh" in *" $_pa_h "*) ;; *) [ ! -d "$RUN_DIR/targets/$_pa_h" ] || _pl_retire "$_pa_h" ;; esac
        done
        [ ! -e "$RUN_DIR/targets/$_pa_n" ] || { _pl_out "$_pa_n" "$_pa_s" "השם שהקרנל נתן לו שייך למגירה אחרת של הסבב"; continue; }
        echo "$_pa_n|$_pa_s" >> "$CHUNK_DIR/present.$CHUNK_SESSION.said"
        target_init "$_pa_n" "$(json_get "$RUN_DIR/manifest.json" ".total_compressed_bytes")"
        # where the round stood when it came in: up to there, what the round's files say of this NAME is another disk's (_db_part)
        cat "$CHUNK_DIR/at" > "$RUN_DIR/targets/$_pa_n/newdisk" 2> /dev/null || echo -1 > "$RUN_DIR/targets/$_pa_n/newdisk"
        : > "$RUN_DIR/targets/$_pa_n/unlisted"   # not the round's to write yet -- under a name the list already holds, too (drawer_back)
        _all="${_all:+$_all }$_pa_n"   # the end's count (imagesrv-agent), a red one included -- as the gate's skips are
        log "דיסק חדש הוכנס באמצע הסבב ($_pa_n, serial $_pa_s, חריץ $_pa_p) -- פס ייצור: נבדק, ומצטרף לסבב כשהשרת מאשר"
        _pl_start "$_pa_n"; echo "$_pa_n" >> "$_pa_q"; _pa_fresh="$_pa_fresh$_pa_n "
    done
    [ -s "$_pa_q" ] || return 0
    : > "$_pa_q.left"
    while read -r _pa_n; do   # in the round's list from the moment it WRITES; out (skipped, failed, cancelled) is its own reason's
        case "$(cat "$RUN_DIR/targets/$_pa_n/state" 2> /dev/null)" in
            waiting) echo "$_pa_n" >> "$_pa_q.left" ;;
            writing) rm -f "$RUN_DIR/targets/$_pa_n/unlisted"; PL_ADDED="$PL_ADDED $_pa_n" ;;
            *) rm -f "$RUN_DIR/targets/$_pa_n/catchup" ;;   # its job ended with it: nothing to tick, or to stop
        esac
    done < "$_pa_q"
    mv "$_pa_q.left" "$_pa_q"
}

_pl_word() {   # $1 = the new disk. 0 = the server answered a report that carried it and did not refuse it. PL_WHY = why not
    _pd_n=0; _pd_s=$(target_bound_serial "$1")
    while :; do
        if _pl_heard "$1" told 'true' && _pd_l=$(printf '%s' "$PL_REPLY" | jq -ce '.production_line | objects' 2> /dev/null); then
            if printf '%s' "$_pd_l" | jq -e --arg s "$_pd_s" '(.refused // []) | index($s) != null' > /dev/null 2>&1; then
                PL_WHY="הסבב כבר קיבל את כל הדיסקים שביקשו"; return 1
            fi
            _pd_name=$(printf '%s' "$_pd_l" | jq -r --arg s "$_pd_s" '.names[$s] // empty | strings' 2> /dev/null)
            log "השרת קיבל את הדיסק החדש $1 (serial $_pd_s) לסבב -- שם המחשב: ${_pd_name:-לא נאמר}"; return 0
        fi
        [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" = waiting ] || { PL_WHY="בוטל לפני שנכתב"; return 1; }
        if round_closed; then PL_WHY=$(round_closed_why); return 1; fi
        _pd_n=$((_pd_n + 1)); [ "$_pd_n" -le "$PL_WORD_S" ] || { PL_WHY="השרת לא אישר את הדיסק תוך $PL_WORD_S שניות"; return 1; }
        sleep 1
    done
}

_pl_gates() {   # $1 = the new disk, in its job. 0 = its table is written, and it is `writing`. Anything else = out, by name
    _pg_m="$RUN_DIR/manifest.json"
    target_unique "$1" || return 1
    _pg=$(smart_gate "$CHUNK_SESSION" "$1") || _pg=""
    if [ -z "$_pg" ]; then   # red: skipped by the gate, by its reason. A gate that said nothing is not "healthy"
        [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" != waiting ] || target_set "$1" skipped "בדיקת ה-SMART לא החזירה תשובה — לא נכתב"
        log "$1: הדיסק החדש לא עבר את שער ה-SMART -- אינו נכתב"; return 1
    fi
    if ! _pl_word "$1"; then
        [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" != waiting ] || target_set "$1" skipped "לא נכתב: $PL_WHY"
        log "$1: הדיסק החדש אינו נכתב -- $PL_WHY"; return 1
    fi
    write_check_round "$_pg_m" "$1"
    [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" = writing ] || return 1
    PREP_TAG=".$1"; discard_before_restore "$1"
    [ "$?" != 2 ] || { fail_written_target "$1" "ה-TRIM לא הסתיים תוך $DISCARD_MAX_S שניות"; return 1; }
    stage_at "$1" gpt 2> /dev/null
    apply_gpt "$1" "$_pg_m" || { fail_written_target "$1" "${PLAN_ERROR:-לא הצלחנו לכתוב את טבלת המחיצות}"; return 1; }
    stage_at "$1" expand 2> /dev/null
    if ! expand_last "$1" "$_pg_m"; then
        log "WARNING: expansion failed on $1 -- writing at the image's own size"
        apply_gpt "$1" "$_pg_m" || { fail_written_target "$1" "${PLAN_ERROR:-לא הצלחנו לכתוב את טבלת המחיצות}"; return 1; }
    fi
    [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" = writing ]
}

_pl_start() {   # $1 = the new disk: its job, in the background. `catchup` makes it drawerback.sh's -- ticked, waited for, stopped on cancel
    _db_main="$CHUNK_DIR"
    (   CHUNK_BG=1; CHUNK_DIR="$_db_main/back.$1"; mkdir -p "$CHUNK_DIR" || exit 1
        _pl_gates "$1" || exit 0
        _db_catch "$1" "$1"; _pj_rc=$?
        [ "$_pj_rc" != 1 ] || : > "$RUN_DIR/targets/$1/back_failed"
        [ "$_pj_rc" != 1 ] || [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" != writing ] \
            || fail_written_target "$1" "הדיסק הצטרף באמצע הסבב, וההשלמה שלו נכשלה: $DB_WHY"
        [ "$_pj_rc" != 0 ] || log "השלמה: $DB_K טווחים -- הדיסק החדש $1 הגיע לסבב"
    ) &
    echo "$! $1 0" > "$RUN_DIR/targets/$1/catchup"
}

prodline_late() {   # $@ = the round's list, before the read-back (drawer_back all). 0 = PL_LATE: new disks that go on while the others end
    PL_LATE=""; _pe_rest=""
    for _pe_d in "$@"; do
        if [ -f "$RUN_DIR/targets/$_pe_d/newdisk" ] && [ -f "$RUN_DIR/targets/$_pe_d/catchup" ]; then PL_LATE="$PL_LATE $_pe_d"
        else _pe_rest="$_pe_rest $_pe_d"; fi
    done
    [ -n "$PL_LATE" ] && _any_alive "$_pe_rest" || { PL_LATE=""; return 1; }   # nobody to hold: the round waits for it
    for _pe_d in $PL_LATE; do : > "$RUN_DIR/targets/$_pe_d/late"; done
    log "פס ייצור: הדיסקים שהצטרפו באמצע ($PL_LATE ) עוד נכתבים -- שאר המגירות מסיימות בלי לחכות להם"
    _disks="${_pe_rest# }"
}

_pl_end_one() {   # $1 = a disk whose completion ended whole, $2 = session, $3 = image, $4 = server: the round's end, for it alone
    _po_m="$RUN_DIR/manifest.json"; rm -f "$RUN_DIR/targets/$1/late"
    stage_at "$1" readback 2> /dev/null; WRITEMARK_HOLD=1; RB_SERVER="$4" RB_IMAGE="$3" readback_drawers "$_po_m" "$1"
    case "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" in failed|cancelled) WRITEMARK_HOLD=0; return 1 ;; esac
    stage_at "$1" grow 2> /dev/null; finish_grow "$1" "" "$_po_m"; stage_at "$1" end 2> /dev/null
    crc_delta_measure "$1"; smart_note_apply "$1"
    name_drawers "$1"; stage_drawer_drivers "$2" "$_po_m" "$1"
    WRITEMARK_HOLD=0; writemark_release_done "$1" || return 1
    [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" = "done" ] || return 1
    drawer_out_detach "$1"
}

prodline_finish() {   # $1 = session, $2 = image, $3 = server. After the round's own end (imagesrv-agent). 0 = a late disk ended done here
    _pf_rc=1; _pf_n=0
    until prodline_on; do [ "$?" = 2 ] && [ "$_pf_n" -lt 10 ] || return 1; _pf_n=$((_pf_n + 1)); sleep 1; done   # an answer being written is not "regular"
    # shellcheck disable=SC2086 # the round's list is a word list on purpose
    drawer_out_detach $_disks
    # what the round's own end left standing as `writing` with nothing running is not this function's to end again
    _pf_skip=" "; for _pf_d in $_disks; do [ "$(cat "$RUN_DIR/targets/$_pf_d/state" 2> /dev/null)" != writing ] || _pf_skip="$_pf_skip$_pf_d "; done
    _disks="$_disks$PL_LATE"; _disks=${_disks# }; PL_LATE=""; _pf_q="$RUN_DIR/prodline.${D_SESSION_ID:-${CHUNK_SESSION:-0}}.new"
    _pf_last=$(tail -n 1 "$RUN_DIR/restore.plan" 2> /dev/null | cut -d'|' -f1)
    while :; do
        for _pf_d in $_disks; do   # its completion ended, and it is still `writing`: whole -- the round's end, for it alone
            [ ! -f "$RUN_DIR/targets/$_pf_d/catchup" ] && [ "$(cat "$RUN_DIR/targets/$_pf_d/state" 2> /dev/null)" = writing ] || continue
            case "$_pf_skip" in *" $_pf_d "*) continue ;; esac
            if _pl_end_one "$_pf_d" "$1" "$2" "$3"; then _pf_rc=0; fi
        done
        _pf_busy=""; for _pf_d in $_disks; do [ ! -f "$RUN_DIR/targets/$_pf_d/catchup" ] || _pf_busy="$_pf_busy $_pf_d"; done
        # #3181: nothing is being written. The line still has names = the machine waits for the next disk, in the wave (linewait.sh)
        if [ -n "$_pf_busy" ] || [ -s "$_pf_q" ]; then line_wait_off; else line_wait || break; fi
        [ "$(cat "$RUN_DIR/state" 2> /dev/null)" = writing ] || { echo writing > "$RUN_DIR/state"; log "פס ייצור: דיסק שהצטרף באמצע הסבב עוד נכתב -- המכונה נשארת בגל"; }
        _chunk_beat; round_closed_fail "$_pf_busy" || :
        drawer_out_tick
        # shellcheck disable=SC2086 # a disk pushed in now joins the same way; a change = the list is in _disks
        drawer_plug_tick "" "$_pf_last" $_disks || :
        _db_mode=all; _db_at=$(($(grep -c . "$RUN_DIR/restore.plan" 2> /dev/null || echo 0) - 1))
        # shellcheck disable=SC2086 # a completion that ended is closed here, as before the read-back (drawerback.sh)
        _db_tick $_disks
        sleep 1
    done
    return "$_pf_rc"
}
