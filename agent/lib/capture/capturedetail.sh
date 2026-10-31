# capturedetail.sh -- the per-partition table a capture reports to the console
# (interfaces.md, "פירוט קליטה", #1505). POSIX sh (busybox ash).
#
# One file, $RUN_DIR/cdetail.txt, one line per partition of the source disk:
#   index|role|fs|size_bytes|used_bytes|state
# used_bytes is digits or "-" (not measured: the console shows "לא נמדד", never
# 0). state is waiting | shrinking | reading | done. capture.sh, capturesink.sh
# and shrink.sh call these as `cd_x ... 2>/dev/null || :` -- the table is
# display only, so a missing helper (a test that sources capture.sh alone)
# costs the console its detail and never the capture its bytes. The server
# derives bytes-done, rate and ETA from this table plus source_progress; the
# agent measures nothing here that it cannot read from the disk.

cd_init() {
    # $1 disk, $2 parts file (idx|guid|uguid|start|size_sectors), $3 sector
    # size. Rebuilt after the shrink (sizes change); a used_bytes already
    # measured by the preflight is carried over.
    _cdf="$RUN_DIR/cdetail.txt"
    if [ -f "$_cdf" ]; then cp "$_cdf" "$_cdf.old"; else : > "$_cdf.old"; fi
    while IFS='|' read -r _cd_i _cd_g _cd_u _cd_st _cd_sz; do
        [ -n "$_cd_i" ] || continue
        _cd_fs=$(_fs_of "$(partition_node "$1" "$_cd_i")")
        _cd_role=$(_partition_role "$_cd_g")
        [ "$_cd_fs" = swap ] && _cd_role=swap
        _cd_used=$(awk -F'|' -v i="$_cd_i" '$1 == i { print $5 }' "$_cdf.old")
        [ "$_cd_role" = swap ] && _cd_used=0   # recorded, never read: a measured zero, like the manifest's
        printf '%s|%s|%s|%s|%s|waiting\n' "$_cd_i" "$_cd_role" "$_cd_fs" $((_cd_sz * $3)) "${_cd_used:--}"
    done < "$2" > "$_cdf.new" && mv "$_cdf.new" "$_cdf"
}

_cd_edit() {
    # $1 idx, $2 role, $3 fs, $4 used, $5 state -- an empty argument keeps the field.
    [ -f "$RUN_DIR/cdetail.txt" ] || return 0
    awk -F'|' -v OFS='|' -v i="$1" -v r="$2" -v f="$3" -v u="$4" -v s="$5" '
        BEGIN { gsub(/[^A-Za-z0-9_.+-]/, "", r); gsub(/[^A-Za-z0-9_.+-]/, "", f) }   # a "|" would shift the columns
        $1 == i { if (r != "") $2 = r; if (f != "") $3 = f; if (u != "") $5 = u; if (s != "") $6 = s }
        { print }' "$RUN_DIR/cdetail.txt" > "$RUN_DIR/cdetail.new" && mv "$RUN_DIR/cdetail.new" "$RUN_DIR/cdetail.txt"
}

cd_part()  { _cd_edit "$1" "$2" "$3" "" "$4"; }   # idx role fs state
cd_state() { _cd_edit "$1" "" "" "" "$2"; }       # idx state
cd_used() {                                       # idx bytes -- anything but digits stays "not measured"
    case "$2" in ''|*[!0-9]*) return 0 ;; esac
    _cd_edit "$1" "" "" "$2" ""
}

cd_json() {
    # The `,"partitions":[...],"compression":"zstd-N"` tail of a capture target;
    # nothing at all when the table was never built (an old flow, a round).
    [ -s "$RUN_DIR/cdetail.txt" ] || return 0
    awk -F'|' '
        function word(s) { gsub(/[^A-Za-z0-9_.+-]/, "", s); return s }
        function num(s) { return s ~ /^[0-9]+$/ ? s : "null" }
        BEGIN { printf ",\"partitions\":[" }
        $1 ~ /^[0-9]+$/ { printf "%s{\"index\":%d,\"role\":\"%s\",\"fs\":\"%s\",\"size_bytes\":%s,\"used_bytes\":%s,\"state\":\"%s\"}", \
            (n++ ? "," : ""), $1, word($2), word($3), num($4), num($5), word($6) }
        END { printf "]" }' "$RUN_DIR/cdetail.txt"
    case "${CAPTURE_LEVEL:-}" in ''|*[!0-9]*) ;; *) printf ',"compression":"zstd-%s"' "$CAPTURE_LEVEL" ;; esac
}
