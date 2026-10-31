# lcsize.sh -- #1443 local clone: how many bytes the copy will move, measured
# before it starts, the way the copy itself counts them. READ-ONLY: nothing
# here writes to a disk (#1466). POSIX sh (busybox ash).
#
# Nadav, 17/10: "לפני העתקה אני רוצה מספר מדויק, לפני שמתחילים." The
# inventory's used bytes are df's on a read-only mount (_used_bytes) -- the
# file system's view. The copy moves partclone's bitmap: "Space in use: ... =
# N Blocks" times "Block size: B Byte", printed (and flushed to -L) after
# read_bitmap + update_used_blocks_count, before the first data block.
#
# How, without copying: the copy's own command (lccopy.sh lc_stream: -c, the
# same flags) with its stream into /dev/null, killed -- its own PID, a direct
# child -- as soon as both lines are in its log. Its source is opened for
# reading only, and no -o/-O: there is no target at all. Not -D (domain
# mode): in partclone 0.3.27 it skips update_used_blocks_count, so its "Space
# in use" is the superblock's count, not the bitmap the copy moves.
#
# $LC/measure/<dev>.copy: "<bytes>|" measured, "-1|<reason>" not measured
# (principle 5: never 0). No file = not measured yet. Per partition:
# $LC/measure/<dev>.copy.parts, idx|bytes (-1 = not measured); what it was
# measured on: .copy.table (the table) and .copy.serial (#1662, lc_copy_used).

LC_SIZE_MAX_S="${LC_SIZE_MAX_S:-120}"   # per partition; the bitmap is seconds

lc_pcl_bytes() {
    # $1.. = partclone's -L log (and stderr). stdout: Space in use x Block
    # size, in bytes. 1 = either line missing -- not measured, never 0.
    cat "$@" 2>/dev/null | tr '\r' '\n' | awk '
        /Space in use:/ && n == "" { if (match($0, /= *[0-9]+ *Blocks/)) { n = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", n) } }
        /Block size:/ && b == "" { if (match($0, /: *[0-9]+ *Byte/)) { b = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", b) } }
        END { if (n == "" || b == "" || b + 0 <= 0) exit 1; printf "%.0f\n", n * b }'
}

_lc_pcl_measure() {
    # $1 = node, $2 = fs, $3 = file prefix in $LC. stdout: the bytes; 1 = not
    # measured, and stdout is the reason instead.
    _lz_pcl=$(partclone_for_fs "$2"); _lz_ign="-a0 -z 524288"; [ "$2" = ntfs ] && _lz_ign="-I $_lz_ign"   # as lc_stream
    rm -f "$3.log" "$3.err"
    command -v "$_lz_pcl" > /dev/null 2>&1 || { echo "$_lz_pcl חסר"; return 1; }
    # shellcheck disable=SC2086 # the flags are a word list
    "$_lz_pcl" -c $_lz_ign -s "$1" -L "$3.log" < /dev/null > /dev/null 2> "$3.err" &
    _lz_pid=$!; _lz_n=0; _lz_b=""
    while :; do
        _lz_b=$(lc_pcl_bytes "$3.log" "$3.err") && break
        kill -0 "$_lz_pid" 2>/dev/null || break
        [ "$_lz_n" -lt $((LC_SIZE_MAX_S * 10)) ] || break
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        poll_tick; _lz_n=$((_lz_n + _tk))   # tenths (waits.sh, #1450)
    done
    kill -9 "$_lz_pid" 2>/dev/null; wait "$_lz_pid" 2>/dev/null; _lz_rc=$?
    # it may have printed the lines and ended between two looks
    [ -n "$_lz_b" ] || _lz_b=$(lc_pcl_bytes "$3.log" "$3.err") || _lz_b=""
    [ -z "$_lz_b" ] || { echo "$_lz_b"; return 0; }
    if [ "$_lz_n" -ge $((LC_SIZE_MAX_S * 10)) ]; then echo "partclone לא דיווח תוך $LC_SIZE_MAX_S שניות"
    else echo "partclone הסתיים בלי לדווח כמה בשימוש (rc=$_lz_rc)"; fi
    return 1
}

lc_copy_size() {
    # $1 = source disk. Writes $LC/measure/$1.copy and .copy.parts (above).
    _lz_o="$LC/measure/$1.copy"; _lz_p="$_lz_o.parts"; mkdir -p "$LC/measure"; : > "$_lz_p"
    rm -f "$_lz_o.table" "$_lz_o.serial"
    cp "$LC/inv/$1.parts" "$_lz_o.table" 2>/dev/null; _lc_clean "$(disk_serial "$1")" > "$_lz_o.serial"
    _lz_t=0; _lz_why=""
    if [ ! -s "$LC/inv/$1.parts" ]; then _lz_why="לא הצלחנו לקרוא את טבלת המחיצות של המקור"
    else
        while IFS='|' read -r _lz_i _lz_g _lz_u _lz_f _lz_s; do
            _lz_node=$(partition_node "$1" "$_lz_i"); _lz_fs=$(_fs_of "$_lz_node")
            case "$_lz_fs:$(partclone_for_fs "$_lz_fs")" in
                swap:*) _lz_x=0 ;;                           # recreated, never copied (lc_run_total)
                *:partclone.dd) _lz_x=$((_lz_s * 512)) ;;     # no known file system: all of it
                *) _lz_x=$(_lc_pcl_measure "$_lz_node" "$_lz_fs" "$LC/measure/$1.p$_lz_i") \
                       || { [ -n "$_lz_why" ] || _lz_why="מחיצה $_lz_i: $_lz_x"; _lz_x=-1; } ;;
            esac
            echo "$_lz_i|$_lz_x" >> "$_lz_p"
            [ "$_lz_x" -lt 0 ] || _lz_t=$((_lz_t + _lz_x))
        done < "$LC/inv/$1.parts"
    fi
    if [ -n "$_lz_why" ]; then
        log "local clone: how much $1 copies was not measured -- $_lz_why"
        printf -- '-1|%s\n' "$(_lc_clean "$_lz_why")" > "$_lz_o.next"
    else
        log "local clone: $1 copies $_lz_t bytes (partclone's used blocks, #1443)"
        printf '%s|\n' "$_lz_t" > "$_lz_o.next"
    fi
    mv "$_lz_o.next" "$_lz_o"
}

lc_copy_used() {
    # $1 = source disk, $2 = the parts file the run copies. #1662: the run
    # counts what the screen said before the start -- lc_copy_size's per
    # partition, when it measured this very disk and table (as lc_shrink_plan
    # reuses the plan, #1466 P2). stdout: the .copy.parts file. 1 = not this
    # one, and stdout says why: the caller keeps df's for EVERY partition --
    # never one of each. A check that cannot run is a "no", never a reuse.
    _lu_m="$LC/measure/$1.copy"; _lu_s=$(_lc_clean "$(disk_serial "$1")")
    case "$(cut -d'|' -f1 "$_lu_m" 2>/dev/null)" in
        ''|*[!0-9]*) echo "not measured: $(cut -d'|' -f2- "$_lu_m" 2>/dev/null)"; return 1 ;; esac
    { [ -n "$_lu_s" ] && [ "$(cat "$_lu_m.serial" 2>/dev/null)" = "$_lu_s" ]; } || { echo "measured on another disk"; return 1; }
    cmp -s "$_lu_m.table" "$2" || { echo "measured on another table (the shrunk layout?)"; return 1; }
    echo "$_lu_m.parts"
}
