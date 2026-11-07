# rbprog.sh -- #2046 (Nadav 24/10): how far each disk's read-back has got, for the
# screen. POSIX sh (busybox ash).
#
# DISPLAY ONLY. Nothing here decides a verdict -- readback.sh does, from the chunks
# the disk gave back. A bar that is missing or stale never fails a disk, and a bar
# at 100% never makes one "verified".
#
# fanout --readback -p 9 <base> <total> keeps one line "<done> <total>" in the file
# the caller opened for it on fd 9 (`9<>file`, readback.sh: fanout opens no path),
# $RUN_DIR/targets/<dev>/rb_prog (progress.sh clears it with the rest of the
# round): <total> = the bytes of every partition of the disk, <done> = the
# partitions before this one (<base>) + what this one has passed. A chunk the
# sample did not draw is passed, not read -- so in sample mode the bar runs fast
# through the chunks nobody asked for. The kiosk reads the file (clonergui.sh,
# lcgui.sh); the check itself runs in parallel on every disk of the round
# (readback_drawers), so each has its own file.
#
# RB_TOTAL (bytes; unset or 0 = no bar) and RB_BASE are the caller's globals.
# A disk with no target directory (a local clone's source) has no bar.

rb_pflags() {
    # $1 = the disk's work dir ($RUN_DIR/readback/<dev>). Sets RB_PF: fanout's -p flags, or nothing.
    RB_PF=""; RB_PFILE=/dev/null   # RB_PFILE: what fanout's fd 9 is opened on -- /dev/null when there is no bar
    case "${RB_TOTAL:-}" in ''|*[!0-9]*|0) return 0 ;; esac
    [ -d "$RUN_DIR/targets/${1##*/}" ] || return 0
    # a file that cannot be opened is no bar -- never a reason for the check not to run (the open is in readback.sh)
    ( : 9<>"$RUN_DIR/targets/${1##*/}/rb_prog" ) 2> /dev/null || return 0
    RB_PFILE="$RUN_DIR/targets/${1##*/}/rb_prog"; RB_PF="-p 9 ${RB_BASE:-0} $RB_TOTAL"
}

rb_list_bytes() {
    # $1 = RB_LIST (idx|fs|kind|a|b|c): the bytes the check will pass, every partition that has a list.
    awk -F'|' '$3 != "none" && $6 ~ /^[0-9]+$/ { s += $6 } END { print s + 0 }' "$1" 2>/dev/null || echo 0
}

rb_lc_bytes() {
    # $1 = the source's manifest, $2 = RB_ALL of a local clone (idx|fs|size_bytes). The bytes the check will
    # pass: a partition read whole (partclone.dd) is its size; the others, the blocks in use -- the manifest's
    # used_bytes, an estimate (partclone counts whole blocks), so the bar is clamped to its total.
    _rq_sum=0
    while IFS='|' read -r _rq_i _rq_fs _rq_sz; do
        _rq_c=$_rq_sz
        if [ "$(partclone_for_fs "$_rq_fs")" != partclone.dd ]; then
            _rq_c=$(jq -r --argjson i "$_rq_i" '.partitions[] | select(.index == $i) | (.used_bytes // .size_bytes)' "$1" 2>/dev/null)
        fi
        case "$_rq_c" in ''|*[!0-9]*) _rq_c=0 ;; esac
        _rq_sum=$((_rq_sum + _rq_c))
    done < "$2"
    echo "$_rq_sum"
}
