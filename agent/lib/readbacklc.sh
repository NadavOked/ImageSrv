# readbacklc.sh -- #1805: the read-back where the server has no list to give:
# a local clone (against its own source) and a direct send's pass 1 (which
# makes the digest the cloners compare with). POSIX sh (busybox ash).
# The rest -- the flush, fanout --readback, the outcomes -- is readback.sh's,
# loaded before this file (its functions and READBACK_* are used here).

_rb_raw() {
    # $1 = disk, $2 = work dir, $3 = node function, $4 = `source` (never flushed:
    # this run did not write it, and it is never opened for writing, #1466). A local
    # clone: fanout's own lines for every partition, one after another, to $2/got.
    : > "$2/got"
    [ "${4:-}" = source ] || rb_flush "$1" "$3" || { echo "unchecked|$RB_WHY"; return; }
    _rw_x="-x"; [ "$RB_MODE" = full ] || _rw_x="-x -s $RB_SEED $RB_PERMILLE"
    while IFS='|' read -r _rw_i _rw_fs _rw_b <&3; do
        # shellcheck disable=SC2086 # fanout's flags are a word list
        rb_digest "$("$3" "$1" "$_rw_i")" "$_rw_fs" "$_rw_b" "$2" $_rw_x \
            || { echo "$RB_KIND|מחיצה $_rw_i: $RB_WHY"; return; }
        sed "s/^/$_rw_i /" "$2/rb.out" >> "$2/got"
    done 3< "$RB_ALL"
    echo ok
}

readback_local() {
    # $1 = the source's manifest, $2 = source, $3.. = targets. A local clone has
    # no server list: the source is read the same way (same mode, same seed) at the
    # same time, and every target's lines must be the source's. A dd partition is
    # read whole (size_bytes). The mode is the last hello's (`readback_mode`).
    _rl_m=$1; _rl_src=$2; shift 2; mkdir -p "$RUN_DIR/readback"
    RB_ALL="$RUN_DIR/readback/all"; RB_MODE=$(rb_mode "$RUN_DIR/response.json"); RB_SEED=$(rb_seed)
    # #2017: a partition copied raw is read raw (its fs may be one partclone refused); one copied
    # through bad sectors (rescued) is not read at all -- the source would not read back.
    _rl_x="${LC:-$RUN_DIR/lc}/run/rescued"; [ -f "$_rl_x" ] || _rl_x=/dev/null
    jq -r '.partitions[] | select(.fs != "swap") | [.index, (if .copy == "raw" then "raw" else (.fs // "") end), .size_bytes] | join("|")' \
        "$_rl_m" > "$RB_ALL.in" 2>> "$LOG_FILE" && awk -F'|' -v x="$(cut -d'|' -f1 "$_rl_x" | tr '\n' ' ')" \
            'BEGIN { n = split(x, a, " "); for (i = 1; i <= n; i++) r[a[i]] } !($1 in r)' "$RB_ALL.in" > "$RB_ALL" \
        || { _fail_alive "$*" "$READBACK_NOT_RUN — תוכנית המחיצות לא נקראה"; return 1; }
    _rl_live=$(_rb_live "$@"); [ -n "$_rl_live" ] || return 1
    log "readback: local clone, mode $RB_MODE, sample seed $RB_SEED, $RB_PERMILLE/1000"
    _rl_t0=$(date +%s); _rb_start "$_rl_src" lc_src_node _rb_raw source; _rl_sp=$RB_PID; _rl_jobs=""
    for _rl_t in $_rl_live; do
        target_set "$_rl_t" verifying; _rb_start "$_rl_t" partition_node _rb_raw; _rl_jobs="$_rl_jobs $_rl_t:$RB_PID"
    done
    _rb_wait "$_rl_src" "$_rl_sp"; _rl_sr=$RB_RESULT
    [ "$_rl_sr" = ok ] || log "local clone: the source was not read back: $_rl_sr"
    for _rl_j in $_rl_jobs; do
        _rl_t=${_rl_j%%:*}; _rb_wait "$_rl_t" "${_rl_j#*:}"
        if [ "$_rl_sr" != ok ]; then RB_RESULT="unchecked|המקור לא נקרא לאימות: ${_rl_sr#*|}"
        elif [ "$RB_RESULT" = ok ]; then
            _rl_bad=$(awk 'NR == FNR { s[FNR] = $0; n = FNR; next } { if ($0 != s[FNR]) { print $1 " " $3; bad = 1; exit } m = FNR }
                           END { if (!bad && m != n) print "count" }' "$RUN_DIR/readback/$_rl_src/got" "$RUN_DIR/readback/$_rl_t/got")
            # a partition with fewer chunks than min(3, its chunks) read: the sample vouches for nothing
            _rl_few=$(awk '$2 == "c" { c[$1]++ } $2 == "n" { n[$1] = $3 }
                           END { for (i in n) { m = n[i] < 3 ? n[i] : 3; if (c[i] + 0 < m) { print i; exit } } }' \
                           "$RUN_DIR/readback/$_rl_t/got")
            if [ -n "$_rl_few" ]; then RB_RESULT="failed|מחיצה $_rl_few: המדגם לא נקרא מהדיסק"
            elif [ "$_rl_bad" = count ]; then RB_RESULT="failed|$READBACK_FAIL (מספר החתיכות שונה מהמקור)"
            elif [ -n "$_rl_bad" ]; then RB_RESULT="failed|מחיצה ${_rl_bad%% *}: $READBACK_FAIL"
            elif [ "$RB_MODE" = full ]; then RB_RESULT="verified|כל החתיכות תאמו למקור"
            else RB_RESULT="sampled|נבדק במדגם — $(grep -c '^[0-9]* c ' "$RUN_DIR/readback/$_rl_t/got") חתיכות של 4MiB תאמו למקור"
            fi
        fi
        case "$RB_RESULT" in verified\|*|sampled\|*) [ ! -s "$_rl_x" ] \
            || RB_RESULT="unverified|מחיצות שהועתקו במצב הצלה ($(cut -d'|' -f1 "$_rl_x" | tr '\n' ' '| sed 's/ $//')) לא נבדקו מול המקור, שאינו נקרא — השאר: ${RB_RESULT#*|}" ;; esac
        _rb_apply "$_rl_t" "$RB_RESULT"
    done
    log "readback: the source and $(echo "$_rl_jobs" | wc -w | tr -d " ") target(s) in $(( $(date +%s) - _rl_t0 ))s ($RB_MODE)"
    _any_alive "$*"
}

rb_capture_json() {
    # $1 = node, $2 = fs, $3 = the partition's bytes. A direct send (#715) never
    # puts an image on the server, so nothing there could make the list: pass 1
    # reads the partition back (every chunk) and the manifest carries the XXH64 of
    # the list -- enough for a full read-back on the cloners; in sample mode they
    # have no list and say "not verified". Elsewhere nothing (the server makes it).
    # No digest = no fields, said in the log. stdout is the JSON only.
    [ "${CAPTURE_SINK:-upload}" = discard ] || return 0
    _rc_w="$RUN_DIR/readback/capture"; rm -rf "$_rc_w"; mkdir -p "$_rc_w"
    if rb_digest "$1" "$2" "$3" "$_rc_w" -x && _rc_t=$(rb_total "$_rc_w/rb.out") && _rc_x=$(rb_list_xxh "$_rc_w/rb.out"); then
        printf ',"readback_xxh64":"%s","readback_chunks":%s,"readback_bytes":%s,"readback_chunk_bytes":%s' \
            "$_rc_x" "${_rc_t% *}" "${_rc_t#* }" "$RB_CHUNK_BYTES"
    else
        log "WARNING: $1: no read-back list for the direct send (${RB_WHY:-no list}) -- its targets will say NOT VERIFIED" > /dev/null
    fi
}
