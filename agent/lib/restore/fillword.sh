# fillword.sh -- the brain's word on a completion: from where, and how fast (#3181, K5).
# POSIX sh (busybox ash).
#
# A drawer that completes a partition (drawerfill.sh) measures what it lacks and tells the server (`rejoin`). In
# shadow mode -- the default -- the brain decides, writes it down, and the agent hears nothing. In `enforce` the
# answer to that report carries `fill` (interfaces.md section 4):
#
#   {"<serial>": {"verdict", "source", "neighbors": ["sdb"], "per_neighbor_bps", "budget_bps"}}
#
#   fill_word  before the neighbours are read (_fl_neighbour): the measure just written is seen in a report, and
#              that report's answer is read (prodline.sh's _pl_heard -- evidence, not a pause). No answer within
#              FILL_WORD_S, or an answer with no plan for this serial = as before: every neighbour, neighborfill's
#              own ceiling. A plan:
#                budget_bps   the ceiling of ALL the reads of this cloner together, bytes a second -> MiB/s in
#                             $RUN_DIR/neighborfill.cap, which neighborfill.sh reads before every piece (the
#                             lowest word wins, never above its own 100). Not a number = no file, and said;
#                source       HTTP = no neighbour is read. NEIGHBOR / NEIGHBORS = only the ones named, FILL_ONLY
#                             (their names here: the report says `dev`, the name the round chose a drawer by).
#                             Anything else (PASS, NONE, a word of a newer server) = as before;
#                bad_max      A27 (#3659): pieces a neighbour may fail before it is not asked again ->
#                             $RUN_DIR/neighborfill.bad (neighborfill.sh); word_s: how long a measure waits for the
#                             brain's answer, from the NEXT measure on -> $RUN_DIR/fillword.word_s. Not there, or not a whole
#                             number in range = no file: the agent's own default (NF_BAD_MAX, FILL_WORD_S), and the log says
#                             which one was used. NO plan, no answer, or a source that is no instruction = no decision, and the
#                             log says so too: neighbours first, then HTTP for what is left (drawerfill.sh);
#   fill_tick  a tick (drawerplug.sh): the brain moves the budget on every report (AIMD: half on RcvbufErrors or
#              skipped). While a ceiling is in force, the last whole answer's budget replaces it.
#
# The share of each neighbour is neighborfill.sh's own (the ceiling over the readers), which is the brain's
# per_neighbor_bps for an even split. The fill itself, XXH64 before a byte is written, and the fall to HTTP are
# the agent's: the brain only says from where and how fast.

FILL_WORD_S="${FILL_WORD_S:-6}"   # three reports: how long a measure waits for the answer to it, when the brain has not said (A27)

_fill_cap() {   # $1 = budget_bps as the server said it -> the ceiling file, MiB/s. 1 = not a number: no file
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    echo $(($1 / 1048576)) > "$RUN_DIR/neighborfill.cap.new" && mv "$RUN_DIR/neighborfill.cap.new" "$RUN_DIR/neighborfill.cap"
}

_fill_whole() {   # $1 = a plan, $2 = field, $3 = the most -> the field as a whole number 1..$3. 1 = not there, or not one
    _fi=$(printf '%s' "$1" | jq -r --arg f "$2" '.[$f] | select(type == "number" and . == floor and . >= 1)' 2> /dev/null)
    case "$_fi" in ''|*[!0-9]*) return 1 ;; esac
    [ "${#_fi}" -le 6 ] && [ "$_fi" -le "$3" ] || return 1
    printf '%s' "$_fi"
}

_fill_keep() {   # $1 = plan, $2 = field, $3 = its file in $RUN_DIR, $4 = the most. A whole word is kept; none (said when it was there and was not one) = no file
    if _fk=$(_fill_whole "$1" "$2" "$4"); then echo "$_fk" > "$RUN_DIR/$3.new" && mv "$RUN_DIR/$3.new" "$RUN_DIR/$3"
    else
        rm -f "$RUN_DIR/$3"
        [ "$(printf '%s' "$1" | jq -r --arg f "$2" 'has($f)' 2> /dev/null)" != true ] || log "המוח: $2 בתוכנית אינו מספר שלם בטווח 1-$4 -- הקבוע של הסוכן"
    fi
}

fill_word() {   # $1 = the drawer, $2 = partition, $3 = chunks it lacks (as `rejoin` says). 0 = read neighbours (FILL_ONLY); 1 = HTTP only
    FILL_ONLY=""; _fw_n=0; _fw_note=""; rm -f "$RUN_DIR/targets/$1/fill.told"
    _fw_w=$(cat "$RUN_DIR/fillword.word_s" 2> /dev/null) || _fw_w=""   # what the brain last said about the wait
    case "$_fw_w" in ''|*[!0-9]*) _fw_w=$FILL_WORD_S; _fw_ws="ברירת המחדל של הסוכן, המוח עוד לא אמר" ;; *) _fw_ws="המוח אמר" ;; esac
    until _pl_heard "$1" fill.told ".rejoin.part == $2 and .rejoin.missing == $3"; do
        _fw_n=$((_fw_n + 1))
        [ "$_fw_n" -le "$_fw_w" ] || { log "המוח לא ענה על המדידה של $1 (מחיצה $2) תוך ${_fw_w} ש' ($_fw_ws) -- אין החלטה: שכנות קודם, ואז HTTP למה שנשאר (ברירת המחדל של הסוכן)"; return 0; }
        sleep 1
    done
    _fw_p=$(printf '%s' "$PL_REPLY" | jq -ce --arg s "$(target_bound_serial "$1")" '.fill[$s] | objects' 2> /dev/null) || _fw_p=""
    if [ -z "$_fw_p" ]; then   # shadow, or no plan for it: neighborfill's own ceiling and limits
        rm -f "$RUN_DIR/neighborfill.cap" "$RUN_DIR/neighborfill.bad"
        log "המוח ענה על המדידה של $1 (מחיצה $2) בלי תוכנית למגירה הזו (צל, או שלא נאמר) -- אין החלטה: שכנות קודם, ואז HTTP למה שנשאר (ברירת המחדל של הסוכן)"; return 0
    fi
    _fill_keep "$_fw_p" bad_max neighborfill.bad 100; _fill_keep "$_fw_p" word_s fillword.word_s 60
    _fw_b=$(printf '%s' "$_fw_p" | jq -r '.budget_bps | numbers | floor' 2> /dev/null)
    if _fill_cap "$_fw_b"; then _fw_c="$(cat "$RUN_DIR/neighborfill.cap")MiB/s"
    else rm -f "$RUN_DIR/neighborfill.cap"; _fw_c="לא נאמרה"; log "המוח: התקציב בתוכנית של $1 אינו מספר -- התקרה של neighborfill עצמה"; fi
    _fw_src=$(printf '%s' "$_fw_p" | jq -r '.source | strings' 2> /dev/null)
    case "$_fw_src" in
        HTTP) log "המוח (מחיצה $2, $1): $(printf '%s' "$_fw_p" | jq -r '.verdict | strings' 2> /dev/null) -- השלמה ב-HTTP, לא קוראים משכנות"; return 1 ;;
        NEIGHBOR|NEIGHBORS)
            for _fw_dev in $(printf '%s' "$_fw_p" | jq -r '.neighbors[]? | strings' 2> /dev/null); do
                for _fw_t in "$RUN_DIR"/targets/*/; do   # the report's `dev` -> the target's name here
                    _fw_h=${_fw_t%/}; _fw_h=${_fw_h##*/}
                    [ "$(cat "$_fw_t/dev" 2> /dev/null || echo "$_fw_h")" != "$_fw_dev" ] || FILL_ONLY="$FILL_ONLY $_fw_h"
                done
            done
            FILL_ONLY="${FILL_ONLY# }" ;;
        *) _fw_note=" (המקור '$_fw_src' אינו הוראת השלמה: אין החלטה, שכנות קודם ואז HTTP)" ;;
    esac
    log "המוח (מחיצה $2, $1): מקור $_fw_src, שכנות: ${FILL_ONLY:-כולן}, תקרה כוללת: $_fw_c$_fw_note"
    return 0
}

fill_tick() {   # the ceiling in force follows the brain's last word (the lowest budget of a whole answer)
    [ -e "$RUN_DIR/neighborfill.cap" ] || return 0
    _ft=$(jq -r 'select(.ok == true) | [.fill[]?.budget_bps | numbers] | min // empty | floor' "$RUN_DIR/progress.reply" 2> /dev/null)
    case "$_ft" in ''|*[!0-9]*) return 0 ;; esac
    [ "$((_ft / 1048576))" != "$(cat "$RUN_DIR/neighborfill.cap" 2> /dev/null)" ] || return 0
    _fill_cap "$_ft" && log "המוח: תקרת ההשלמה מהשכנות עכשיו $((_ft / 1048576))MiB/s"
}
