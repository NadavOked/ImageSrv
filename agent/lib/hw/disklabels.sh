# disklabels.sh -- the set and sticker of each disk: "קוריאים · xx-7" (#1857, mockup 04).
# POSIX sh (busybox ash).
#
# The name goes with the disk by its serial, not with the drawer: hello answers
# `disk_labels` ({"<serial>": {"set", "label", "text"}}) for the disks this machine
# sent, and only those that are in a set -- a disk with no set is simply absent.
# The cloner screen draws `text` next to the drawer so the person at the machine
# knows which disk to pull. Display only: nothing is written to the disk.

disk_labels_refresh() {
    # The last hello answer -> $RUN_DIR/disk_labels, "ok" then one "serial|text" per
    # line -- "ok" is the evidence jq read the answer whole. An answer without the
    # field (a poll response, an older server) or one jq cannot read is not "no sets":
    # the previous file stays (principle 5). {} is an answer: it clears. 0 = refreshed.
    _dl_n="$RUN_DIR/disk_labels.next"
    jq -r 'if (.disk_labels | type) != "object" then error("no disk_labels") else
        "ok", (.disk_labels | to_entries[] | select((.value.text | type) == "string") |
        "\(.key | gsub("[\r\n|]"; " "))|\(.value.text | gsub("[\r\n|]"; " "))") end' \
        "$RUN_DIR/response.json" > "$_dl_n" 2>/dev/null
    [ "$(head -n 1 "$_dl_n" 2>/dev/null)" = ok ] || { rm -f "$_dl_n"; return 1; }
    mv "$_dl_n" "$RUN_DIR/disk_labels"
}

disk_label_records() {
    # $1 = the disks. cdisk_label=<dev>|<text> per disk whose serial is in a set; a disk
    # with no serial, no set or no file yet draws nothing (the screen then shows what it
    # always did). Keyed by dev like cused=: the idle inventory and a round's drawers
    # both look it up by the device.
    [ -f "$RUN_DIR/disk_labels" ] || return 0
    for _dl_d in $1; do
        _dl_s=$(disk_serial "$_dl_d"); [ -n "$_dl_s" ] || continue
        awk -F'|' -v s="$_dl_s" -v d="$_dl_d" 'NR > 1 && $1 == s { print "cdisk_label=" d "|" $2; exit }' \
            "$RUN_DIR/disk_labels"
    done
}
