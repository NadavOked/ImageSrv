# chunkprog.sh -- #3070: what a drawer's progress bar and rate count in the chunk engine.
# POSIX sh (busybox ash).
#
# The counter the report carries (targets/<dev>/feed.bytes -> bytes.<N>, chunkrecv.sh
# `_chunk_counters`) is the COMPRESSED bytes of what is durable. Compressed bytes are not
# time: a stretch of the image that compresses to nothing (a zeroed swap file; metal
# 13/11, image img_3fa17f, partition 3: 4,300 chunks holding 0.1% of the compressed bytes,
# then a tail of 4,701 chunks holding none) is written at the disk's pace and moves that
# counter by a few hundred bytes. The bar stood 92 s at 84% and 117 s at 92% on drawers
# that were writing (their `0 skipped` and 17,814 of 18,254 durable at the end said so), and
# the screen said "waiting for the stream" -- cloner_gui_pace saw a counter that did not move.
#
# The screen is given a second pair that does move with the writing: the DATA STREAM bytes of
# the durable chunks (the unit of the disk's write: what --write-kbps and the minimum drive
# speed are measured in), out of the stream bytes of every partition (the manifest's
# readback_bytes). The report to the server is NOT changed -- its counter, its total and
# everything the server learns from them stay compressed (the server's unit is #3163/#3165's).
#
#   targets/<dev>/stream_total   the data stream of the whole image; absent = not measured
#   targets/<dev>/stream.<part>  the durable stream bytes of that partition, one number, replaced by a rename
#
# No pair, no screen change: a drawer without `stream_total` (another engine, a manifest that
# does not say) keeps the compressed pair it always had.

chunk_stream_init() {   # $1 = manifest, $2 = dev. 0 = the drawer has a total; 1 = it has none (and any old one is gone)
    _cpg_t="$RUN_DIR/targets/$2"
    [ -d "$_cpg_t" ] || return 1
    # every partition that has a file is streamed; one that does not say its stream's length leaves the whole total unsaid
    _cpg_z=$(jq -r '[.partitions[] | select((.file // "") != "") | .readback_bytes]
                   | if length > 0 and all(.[]; type == "number" and . >= 0 and . == floor) then add else empty end' "$1" 2>/dev/null)
    case "$_cpg_z" in ''|*[!0-9]*|0) rm -f "$_cpg_t/stream_total"; return 1 ;; esac
    printf '%s\n' "$_cpg_z" > "$_cpg_t/stream_total.new" && mv -f "$_cpg_t/stream_total.new" "$_cpg_t/stream_total"
}

chunk_stream_write() {   # $1 = dev, $2 = partition index. From $CHUNK_STATE and $CHUNK_IDX (chunkrecv.sh). Nothing read = nothing written
    _cpg_t="$RUN_DIR/targets/$1"
    [ -s "$_cpg_t/stream_total" ] || return 0
    _cpg_v=$(_chunk_line "$(partition_node "$1" "$2")" | awk -v idx="$CHUNK_IDX" '
        BEGIN { if ((getline h < idx) > 0) split(h, f, " ") }
        $2 == "ok" && f[4] ~ /^[0-9]+$/ && f[8] ~ /^[0-9]+$/ && f[9] ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ && f[9] * f[4] >= f[8] {
            last = 0   # the last chunk is the only short one: if it is durable, the stream ends where the data does
            if ($4 != "-") { m = split($4, r, ","); split(r[m], p, "-"); last = (p[2] + 0 == f[9] - 1) }
            printf "%.0f\n", $3 * f[4] - last * (f[9] * f[4] - f[8]) }' 2>/dev/null)
    case "$_cpg_v" in ''|*[!0-9]*) return 0 ;; esac
    printf '%s\n' "$_cpg_v" > "$_cpg_t/tmp.stream.$2" && mv -f "$_cpg_t/tmp.stream.$2" "$_cpg_t/stream.$2"
}

chunk_stream_pair() {   # $1 = dev. Sets _cpg_bytes and _cpg_total; 1 = this drawer has no stream pair (the caller keeps the compressed one)
    _cpg_t="$RUN_DIR/targets/$1"
    _cpg_total=$(cat "$_cpg_t/stream_total" 2>/dev/null)
    case "$_cpg_total" in ''|*[!0-9]*|0) return 1 ;; esac
    _cpg_bytes=0
    for _cpg_f in "$_cpg_t"/stream.[0-9]*; do
        [ -f "$_cpg_f" ] || continue
        _cpg_n=$(_last_number "$_cpg_f"); _cpg_bytes=$((_cpg_bytes + ${_cpg_n:-0}))
    done
    [ "$_cpg_bytes" -le "$_cpg_total" ]   # more than the whole is no measurement: the compressed pair stays
}
