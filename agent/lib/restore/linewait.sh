# linewait.sh -- a production line's cloner whose disks are all finished waits for the next disk (#3181).
# POSIX sh (busybox ash).
#
# A production line asks for more disks than the cloners have drawers (prodline.sh). A cloner whose drawers are
# written and detached is therefore not at its end: the operator pulls them and pushes the next disks in, with
# the machine on. Until here the round's end followed the last disk written -- the final report, the beep, the
# count, the power-off -- while the server's queue still had names, and the next disk met a machine that was off.
#
#   when     prodline_finish's loop (prodline.sh), once a tick, when no disk of this machine is being written:
#            line_wait. Never after the machine's own end was said: `done`, `partial` and `failed` are final on
#            the server, and the wave does not wait for a machine that said one of them;
#   asked    the progress report says `line_wait: {"s": N}` (line_wait_json, from machine_extra_json) -- this
#            machine has nothing to write, for N seconds now -- and the machine stays `writing`: not final, so
#            the wave and the round stay open, and reported every two seconds, so the server never calls it lost;
#   answer   the server's word is its answer to a report that CARRIED the question (principle 5: the reply is read
#            a tick after the body was seen, as prodline.sh's _pl_heard): `production_line.queue_left` -- names no
#            disk holds -- and `insert` -- this machine's emptied slots that keep a name. Any = it waits; "הכנס
#            דיסק" with the name is the screen's, from `insert`. None = the line has nothing more for this machine,
#            and the round ends as any round does: the beep, the count, the power-off -- or, with a failed disk,
#            red and no power-off (imagesrv-agent, idle.sh);
#   waiting  no ceiling: cloning is attended (Nadav), and a server gone quiet does not end a wait its own word
#            began. The drawers are looked at on every tick, event or not (drawerplug.sh's look): one uevent lost
#            would leave the operator's disk unseen for as long as the machine waits. A disk pushed in is
#            prodline.sh's new target, and while one is written the machine does not say it waits (line_wait_off);
#   ends     the server answers that nothing is left; the round was closed (the operator, from the console); or
#            nobody answered the question within LINE_WAIT_ASK_S -- a server that does not answer is not asked
#            for ever, and the machine ends as it did before this file. An answer with no `queue_left` is a
#            server that does not speak of waiting: the same. Each end is said in the log, by its reason.

LINE_WAIT_ASK_S="${LINE_WAIT_ASK_S:-60}"   # how long the question waits for its first answer

line_wait_off() { rm -f "$RUN_DIR/linewait" "$RUN_DIR/linewait.told" "$RUN_DIR/linewait.on"; }   # a disk is being written, or the wait ended

_lw_end() { line_wait_off; log "פס ייצור: $1 -- המכונה מסיימת את הסבב"; }

line_wait() {   # 0 = the line still has names for a disk pushed in here: the machine stays in the wave. 1 = it ends as any round does
    _lw_f="$RUN_DIR/linewait"
    if round_closed; then _lw_end "השרת סגר את הסבב"; return 1; fi
    if [ ! -s "$_lw_f" ]; then
        _lw_s=$(cat "$RUN_DIR/state" 2> /dev/null)
        case "$_lw_s" in done|failed|partial) log "פס ייצור: המכונה כבר דיווחה $_lw_s -- היא אינה ממתינה לדיסק"; return 1 ;; esac
        rm -f "$_lw_f.told" "$_lw_f.on"; date +%s > "$_lw_f" || return 1
        echo writing > "$RUN_DIR/state"; return 0   # asked: the next report carries it
    fi
    : > "$CHUNK_DIR/plug"   # looked at every tick, as if the kernel had said so (drawer_plug_tick, right after this in the loop)
    _lw_t=$(cat "$_lw_f" 2> /dev/null); case "$_lw_t" in ''|*[!0-9]*) _lw_t=0 ;; esac
    _lw_a=$(($(date +%s) - _lw_t)); [ "$_lw_a" -ge 0 ] || _lw_a=0   # how long THIS question has been asked
    if [ ! -e "$_lw_f.told" ]; then   # a body left from an earlier wait says a longer time than this one has lasted: not this question
        if jq -e --argjson a "$_lw_a" '.line_wait.s | numbers | . <= $a' "$RUN_DIR/progress.json" > /dev/null 2>&1; then : > "$_lw_f.told"; fi
    elif _lw=$(jq -ce 'select(.ok == true) | .production_line | objects' "$RUN_DIR/progress.reply" 2> /dev/null) && [ -n "$_lw" ]; then
        _lw_q=$(printf '%s' "$_lw" | jq -r '.queue_left | numbers | floor' 2> /dev/null)
        _lw_i=$(printf '%s' "$_lw" | jq -r '(.insert // {}) | length' 2> /dev/null)
        case "$_lw_q$_lw_i" in ''|*[!0-9]*) _lw_q="" ;; esac
        if [ -z "$_lw_q" ] || [ -z "$_lw_i" ]; then _lw_end "השרת אינו אומר כמה שמות נשארו בתור (queue_left)"; return 1; fi
        if [ "$_lw_q" = 0 ] && [ "$_lw_i" = 0 ]; then _lw_end "התור ריק, ואין שם שמחכה למגירה במכונה הזו"; return 1; fi
        [ -e "$_lw_f.on" ] || log "פס ייצור: כל הדיסקים במכונה סיימו, ולסבב נשארו שמות ($_lw_q בתור, $_lw_i שמורים למגירות כאן) -- המכונה נשארת בגל וממתינה לדיסק. השם הבא: $(printf '%s' "$_lw" | jq -r '[.insert[]?, .next_name] | map(strings) | .[0] // "לא נאמר"' 2> /dev/null)"
        : > "$_lw_f.on"; return 0
    fi
    [ ! -e "$_lw_f.on" ] || return 0   # it waits by the server's own word: an answer not read now is not "nothing left"
    [ "$_lw_a" -gt "$LINE_WAIT_ASK_S" ] || return 0
    _lw_end "השרת לא ענה תוך $LINE_WAIT_ASK_S שניות אם נשארו שמות בתור"; return 1
}

line_wait_json() {   # what the progress report says of it (machine_extra_json, datareport.sh). Nothing = the machine does not wait
    _lj=$(cat "$RUN_DIR/linewait" 2> /dev/null) || return 0
    case "$_lj" in ''|*[!0-9]*) return 0 ;; esac
    _lj=$(($(date +%s) - _lj)); [ "$_lj" -ge 0 ] || _lj=0   # a clock set back mid-wait is not a negative wait
    printf ',"line_wait":{"s":%s}' "$_lj"
}
