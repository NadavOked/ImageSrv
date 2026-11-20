# rbchunks.sh -- #3287 (S63): the chunks a read-back found different, named to the server.
# POSIX sh (busybox ash).
#
# readback.sh judges a disk. Until #3287 a disk that failed said so in a sentence
# ("האימות אחרי הכתיבה נכשל", the sample's first chunk only in its text), and the
# server's brain (server/brain/integrity.py, K4) could never tell a bad image --
# the same chunk wrong on two disks of a wave -- from a bad disk. Now each target
# carries, in its progress entry (interfaces.md, section 4):
#
#   readback_failed_chunks     [{"partition": P, "chunk_index": K}, ...] -- each chunk
#                              READ and different from the image's list (xxh64, 4MiB).
#   readback_first_bad_offset  {"partition": P, "offset": B} -- the first of them, as a
#                              byte of the partition's read-back stream (K x 4MiB: the
#                              used blocks in order, as fanout cuts them), not a disk byte.
#
# Never folded (principle 5): the list is sent only on positive evidence --
#   verified/sampled  []: the comparison ran and nothing differed (`readback` says which).
#   failed            the chunks, when they could be named: in a sample, the drawn chunks
#                     that differ; in full mode the image's list is fetched only now
#                     (all was compared by one XXH64) and compared chunk by chunk.
#   anything else     no field: the read broke or never ran (unchecked), the map differs,
#                     the disk did not give the bytes back, full mode's list did not come
#                     down, nothing to compare with -- "not reported", never "no failure".
# The bound: at most RB_BAD_MAX (64) chunks, the lowest; the log says how many differed.
# Only the first partition that failed: readback.sh stops reading there, as it did.
# The verdict and the error of the disk are readback.sh's, unchanged.

RB_BAD_MAX="${RB_BAD_MAX:-64}"

rb_bad_list() {
    # $1 = partition, $2 = the image's list (line k+1 = chunk k), $3 = fanout -x output, $4 = work
    # dir. Appends "P K" to $4/bad for each chunk read that differs -- as strings (#1890), at most
    # RB_BAD_MAX. Nothing named = nothing written. Always 0: what it names is a report, not a verdict.
    case "$1" in ''|*[!0-9]*) return 0 ;; esac
    _bl_n=$(awk -v p="$1" -v max="$RB_BAD_MAX" -v out="$4/bad.$1" 'NR == FNR { l[NR - 1] = $0; next }
        $1 == "c" && $2 ~ /^[0-9]+$/ && (l[$2] "") != ($3 "") { if (n < max) print p, $2 > out; n++ }
        END { print n + 0 }' "$2" "$3" 2>> "$LOG_FILE")
    case "$_bl_n" in ''|*[!0-9]*|0) rm -f "$4/bad.$1"; return 0 ;; esac
    cat "$4/bad.$1" >> "$4/bad" || rm -f "$4/bad"
    log "readback: partition $1: $_bl_n chunk(s) differ from the image's list (the report names $(grep -c . "$4/bad" 2>/dev/null))" > /dev/null
    return 0
}

rb_bad_full() {
    # $1 = partition, $2 = the manifest's xxh64 of its list, $3 = its chunks, $4 = work dir. Full mode
    # compared the whole list by one XXH64; to name the chunks, the image's list (_rb_fetch, checked
    # against the manifest) -- or nothing named.
    if _rb_fetch "$1" "$4/srv.$1" "$2" "$3"; then
        rb_bad_list "$1" "$4/srv.$1" "$4/rb.out" "$4"
    else
        log "readback: partition $1: the image's list did not come down -- the chunks that differ are not named" > /dev/null
    fi
    return 0
}

rb_chunks_keep() {
    # $1 = disk, $2 = its result line (_rb_apply). targets/<dev>/rb_chunks: "P K" lines, or empty
    # after a check that passed; no file = not reported.
    _rk_f="$RUN_DIR/targets/$1/rb_chunks"; rm -f "$_rk_f"
    case "$2" in
        verified\|*|sampled\|*) : > "$_rk_f" ;;
        failed\|*) if [ -s "$RUN_DIR/readback/$1/bad" ]; then cp "$RUN_DIR/readback/$1/bad" "$_rk_f" || rm -f "$_rk_f"; fi ;;
    esac
    return 0
}

rb_chunks_json() {
    # $1 = disk. The two fields of the top, from targets/<dev>/rb_chunks; no file = nothing.
    [ -f "$RUN_DIR/targets/$1/rb_chunks" ] || return 0
    awk -v cb="${RB_CHUNK_BYTES:-4194304}" '$1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {
            r = r (n++ ? "," : "") "{\"partition\":" $1 ",\"chunk_index\":" $2 "}"
            if (n == 1 || $1 + 0 < fp || ($1 + 0 == fp && $2 + 0 < fk)) { fp = $1 + 0; fk = $2 + 0 } }
        END { if (NR && !n) exit   # lines and none of them a chunk: not evidence, not "none failed"
              printf ",\"readback_failed_chunks\":[%s]", r
              if (n) printf ",\"readback_first_bad_offset\":{\"partition\":%d,\"offset\":%.0f}", fp, fk * cb }' \
        "$RUN_DIR/targets/$1/rb_chunks"
}
