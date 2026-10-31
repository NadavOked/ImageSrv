# cancel.sh -- #1386 (Nadav 13/10 + 17/10): "בטל דיסק זה" -- one drawer
# leaves a live stream, the others keep writing. POSIX sh (busybox ash).
#
# A request is a file, targets/<dev>/cancel, holding who asked as the screens
# say it ("על ידי nadav", "ממחשב הבנייה (nadav)", "ממסך מחשב השיכפול"). Two
# writers: the progress loop, from the server's answer (`cancel`, interface 4
# -- the console and the build machine), and the GUI bridge, from this
# cloner's own screen (no sign-in, like the SMART choice: a person stands at
# the machine). The stream's own watcher carries it out (cancel_drawer in
# verdict.sh, which every drawer path loads) with #1603's mechanism: the
# drawer's zstd is killed, its fifo loses the reader, fanout's next write is
# EPIPE and only that drawer is out.
#
# Cancelled is not failed: no ATA evidence, no disk_failure (#874), the next
# round writes the disk as a green one. It is not done either -- the GPT is
# wiped (primary and backup), so a half-written disk never looks like a system.

cancel_request() {
    # $1 = dev, $2 = who. Only a drawer that is writing now; 0 = it is asked.
    case "$1" in ''|*[!a-z0-9]*) return 1 ;; esac
    _cr_t="$RUN_DIR/targets/$1"
    [ "$(cat "$_cr_t/state" 2>/dev/null)" = writing ] || return 1
    [ -f "$_cr_t/cancel" ] && return 2   # already asked: not again, not logged again
    printf '%s\n' "$2" > "$_cr_t/cancel.next" && mv "$_cr_t/cancel.next" "$_cr_t/cancel"
}

cancel_from_reply() {
    # The last progress answer's `cancel` list -> requests. An answer that was
    # not read is "no request", and the next one (2 s) asks again.
    [ -s "$RUN_DIR/progress.reply" ] || return 0
    json_get_join "$RUN_DIR/progress.reply" '[(.cancel // [])[] | "\(.dev)|\(.who_he // "")"]' \
        | while IFS='|' read -r _cf_dev _cf_who; do
            if cancel_request "$_cf_dev" "$_cf_who"; then log "$_cf_dev: cancel asked $_cf_who"; fi
        done
    return 0
}
