# writemark.sh -- #1798: "the write did not finish", written ON the target disk.
# POSIX sh (busybox ash).
#
# The table is written before the stream (expand.sh), so a target whose write
# was cut by `reboot -f` or a power cut keeps a full, valid GPT and half its
# data -- and /run (tmpfs) with every record of the failure is gone. On metal
# (19/10, rc7nl2, cloner 2) such a disk was "SMART תקין" on the standby screen
# and "Windows · 4 מחיצות" on the local clone screen, and could be the SOURCE.
# The cleanup of #1388 (wipe_written_gpt) never runs when the power falls.
#
# The mark: every partition of the table is NAMED $WRITEMARK_NAME, in the
# same sgdisk call that writes the table (apply_gpt, and expand_last when it
# rebuilds it) -- there is no moment with a new table and no mark. Only after
# the last write -- the whole image and the grow after it (finish_grow: a round,
# a restore and a local clone alike) -- the names go back, each is read back,
# and then the target is done. Not a "check outside cloning" (#874): it is part
# of the table write.
#
# Why the partition name, and not the disk GUID (sgdisk -U) or a block outside
# the partitions: the disk GUID is what Windows' BCD is bound to (#26) and
# verify_table reads it back; a block outside the partitions is invisible to
# every tool and outlives a disk that anyone else re-partitions. The name costs
# no byte of data, Windows does not identify partitions by it, `sgdisk -p`
# shows it to a technician, and any new table -- ours or another tool's --
# replaces it.
#
# The ORIGINAL names: a local clone copies its source's -- $LC/run/entries
# (idx|name|attrs, read with sgdisk -i from the source before the copy: "Basic
# data partition", "EFI system partition", "Microsoft reserved partition"),
# passed as $2 of writemark_clear (through finish_grow); its table check runs
# before the grow and finds the mark on every partition. A library image carries no names: the
# manifest has no such field and apply_gpt never wrote one, so a restored disk
# had empty names before #1798 and gets them back empty -- exactly the image.
#
# Reading it (principle 5): three answers, never two. `unchecked` is a table
# sgdisk could not read -- never "clean".

WRITEMARK_NAME="imagectl-incomplete"
WRITEMARK_HE="כתיבה לא הושלמה — לא שמיש"

writemark_args() {
    # $1 = plan file (manifest_plan's lines). The sgdisk options that name every
    # partition of the plan -- appended to the ONE call that writes the table.
    awk -F'|' -v n="$WRITEMARK_NAME" '$1 ~ /^[0-9]+$/ { printf " -c %s:%s", $1, n }' "$1"
}

_writemark_marked() {
    # $1 = disk. The indexes that carry the mark, one per line; 1 = sgdisk could
    # not read the table. A partition row of `sgdisk -p` ends with the name.
    _wm_p=$(sgdisk -p "$DEVROOT/$1" 2>> "$LOG_FILE") || return 1
    printf '%s\n' "$_wm_p" | awk -v n="$WRITEMARK_NAME" '$1 ~ /^[0-9]+$/ && $NF == n { print $1 }'
}

writemark_state() {
    # $1 = disk. incomplete | clean | unchecked.
    _ws=$(_writemark_marked "$1") || { echo unchecked; return 0; }
    if [ -n "$_ws" ]; then echo incomplete; else echo clean; fi
}

writemark_json() {
    # $1 = disk, $2 = its scheme (disk_scheme). ,"write_mark":"..." for the hello
    # disk entry. Only a GPT can carry the mark: none/mbr is clean without
    # asking sgdisk; a scheme that was not read is unchecked.
    case "$2" in gpt) _wj=$(writemark_state "$1") ;; none|mbr) _wj=clean ;; *) _wj=unchecked ;; esac
    printf ',"write_mark":"%s"' "$_wj"
}

_writemark_want() {
    # $1 = index, $2 = names file or "". The partition's original name ("" = none).
    [ -z "$2" ] || awk -F'|' -v i="$1" '$1 == i { print $2; exit }' "$2"
}

writemark_clear() {
    # $1 = disk, $2 = names file (idx|name|..., optional). Every marked partition
    # gets its original name back -- from $2, else empty -- in ONE sgdisk call
    # (#1714). 0 only when each name, read back with sgdisk -i, is the one we
    # meant: sgdisk's exit code is not evidence (principle 5).
    _wc_d="$1"; _wc_names="${2:-}"
    _wc_i=$(_writemark_marked "$_wc_d") || { log "$_wc_d: the table could not be read to clear the incomplete-write mark"; return 1; }
    [ -n "$_wc_i" ] || return 0
    set --
    for _wc_x in $_wc_i; do set -- "$@" -c "$_wc_x:$(_writemark_want "$_wc_x" "$_wc_names")"; done
    sgdisk "$@" "$DEVROOT/$_wc_d" >> "$LOG_FILE" 2>&1 \
        || { log "$_wc_d: sgdisk refused to clear the incomplete-write mark: $*"; return 1; }
    for _wc_x in $_wc_i; do
        _wc_r=$(sgdisk -i "$_wc_x" "$DEVROOT/$_wc_d" 2>> "$LOG_FILE") \
            || { log "$_wc_d: partition $_wc_x could not be read back after the mark was cleared"; return 1; }
        _wc_got=$(printf '%s\n' "$_wc_r" | sed -n "s/^Partition name: '\(.*\)'.*/\1/p")
        _wc_want=$(_writemark_want "$_wc_x" "$_wc_names")
        [ "$_wc_got" = "$_wc_want" ] \
            || { log "$_wc_d: partition $_wc_x is named '$_wc_got' after sgdisk -c, not '$_wc_want'"; return 1; }
    done
    log "$_wc_d: the incomplete-write mark is cleared (partitions $(echo $_wc_i))"
}

writemark_release() {
    # $1 = disk, $2 = names file (optional). The mark comes off, or the target
    # fails in the open (principle 4): finish_grow's last step, or the caller's
    # after the post-deploy writes (WRITEMARK_HOLD=1, #1877).
    writemark_clear "$1" "${2:-}" && return 0
    fail_written_target "$1" "הכתיבה הושלמה, אך הסימון 'כתיבה לא הושלמה' לא הוסר מהדיסק"
    return 1
}

writemark_release_done() {
    # $@ = disks. #1877: the name (hostname.sh) and the drivers (postdeploy.sh)
    # write to the target AFTER finish_grow -- a cut in them must leave the mark
    # on. So under WRITEMARK_HOLD=1 every target that reached done is released
    # here, after them. 1 = at least one mark stayed (that target is failed now).
    _wr_rc=0
    for _wr_d in "$@"; do
        [ "$(cat "$RUN_DIR/targets/$_wr_d/state" 2>/dev/null)" = "done" ] || continue
        writemark_release "$_wr_d" || _wr_rc=1
    done
    return "$_wr_rc"
}
