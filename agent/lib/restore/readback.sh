# readback.sh -- #1805: a disk is done by what it holds, not by what the stream said.
# POSIX sh (busybox ash).
#
# Until #1805 every "done" was judged on evidence from the stream (the sha256
# of the compressed bytes before zstd and partclone, partclone's exit code) --
# nothing read the disk (CLR-01/03, research-failures-cloner.md).
#
# After every partition of a disk was written and before the filesystem grows
# (finish_grow changes what is on it), each disk is
#   1. flushed: an fsync of the disk (the drive's own write cache), then
#      `blockdev --flushbufs` on the disk and on every partition node partclone
#      wrote through -- each exit code checked (CLR-03);
#   2. read back: `partclone.<fs> -c` names the used blocks of the WRITTEN
#      filesystem and `fanout --readback -x` reads them with O_DIRECT, cut into
#      4MiB chunks of the data stream, an XXH64 a chunk (fanout.c, readback());
#   3. judged against the server's list for the same image (`GET
#      …/readback/<index>`, server/readbackhash.py), itself checked against the
#      manifest's `readback_xxh64` -- or, a local clone, the source read the
#      same way at the same time.
# Nadav, 20/10 (#1864, a full sha256 cost ~8 minutes a round of 4 drawers):
#   sample (the default) -- ~5% of the chunks: the first, the middle, the last,
#          the rest drawn from a seed (logged). Says "sampled", never "verified".
#   full   -- every chunk (`readback_full` in the console's settings; the server
#          adds `readback_mode` to the manifest it serves).
# A manifest from before #1864 that carries only `readback_sha256` is still
# read back whole with sha256 in full mode; in sample mode it is "not verified"
# until the server has made its list.
#
# The outcome of a disk, never folded (principle 5):
#   verified    every chunk of every partition matched.
#   sampled     the drawn chunks matched -- "נבדק במדגם".
#   unverified  nothing to compare with (no list yet, a direct send in sample
#               mode): the disk is done and says so -- never "verified". "unfetched" (#1907): the
#               same, its own reason -- the server's list did not come down (readback_why).
#   failed     the disk gave back something else: the drawer fails, by name.
#   unchecked   the check could not run (a flush refused, no map, a read that
#               broke): the drawer fails too -- "not checked" is not "fine".
#
# Loaded by imagesrv-agent after restore.sh, verdict.sh and failmark.sh.
# WAIT_READBACK_S, the ceiling of one disk's whole read-back, sits in waits.sh with the others.

READBACK_FAIL="האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב"
READBACK_NOT_RUN="האימות אחרי הכתיבה לא רץ — אין ראיה שהדיסק שמר את מה שנכתב"
RB_PERMILLE="${RB_PERMILLE:-50}"        # the sample: 5% of the chunks (Nadav 20/10)
RB_CHUNK_BYTES=4194304                  # fanout.c RB_CHUNK, server CHUNK_BYTES
# #1907 (Nadav 22/10): a list that did not come down is fetched again -- N tries, a pause apart, none after the ceiling.
RB_LIST_TRIES="${RB_LIST_TRIES:-4}"; RB_LIST_PAUSE_S="${RB_LIST_PAUSE_S:-5}"; RB_LIST_WAIT_S="${RB_LIST_WAIT_S:-60}"

rb_flushbufs() {
    # A partition BLKFLSBUF can race udev/blkid after the table changes (#1879).
    # Retry briefly, but preserve the final non-zero status as visible failure.
    _fb_try=0
    while [ "$_fb_try" -lt 3 ]; do
        blockdev --flushbufs "$1" >> "$LOG_FILE" 2>&1 && return 0
        _fb_rc=$?; _fb_try=$((_fb_try + 1))
        [ "$_fb_try" -ge 3 ] || sleep 1
    done
    return "$_fb_rc"
}

rb_flush() {
    # $1 = disk, $2 = node function. Every non-swap partition of RB_ALL. 0 = every
    # flush returned 0; otherwise RB_WHY. BLKFLSBUF writes back AND drops the
    # clean pages of that block device, and the partition's own cache is the
    # one partclone wrote through -- so each partition node, not only the disk.
    # BLKFLSBUF does not reach the drive's write cache; fsync on the disk does
    # (dd writes nothing here: count=0, conv=fsync).
    dd if=/dev/null of="$DEVROOT/$1" bs=512 count=0 conv=notrunc,fsync 2>> "$LOG_FILE" \
        || { RB_WHY="fsync לדיסק נכשל (dd conv=fsync rc=$?)"; return 1; }
    rb_flushbufs "$DEVROOT/$1" \
        || { RB_WHY="ריקון המטמון לדיסק נכשל (blockdev --flushbufs rc=$?)"; return 1; }
    for _rf_i in $(cut -d'|' -f1 "$RB_ALL"); do
        _rf_n=$("$2" "$1" "$_rf_i")
        rb_flushbufs "$_rf_n" \
            || { RB_WHY="ריקון המטמון של מחיצה $_rf_i נכשל (blockdev --flushbufs rc=$?)"; return 1; }
    done
}

rb_digest() {
    # $1 = node, $2 = fs, $3 = bytes (partclone.dd: the whole partition), $4 = work
    # dir, $5.. = fanout's own flags: "-x" (every chunk), "-x -s SEED PERMILLE" (a
    # sample), nothing (the sha256 of a manifest from before #1864). fanout's lines
    # go to $4/rb.out. 1 = no answer: RB_KIND (failed = the disk did not give the
    # bytes back, unchecked = the check broke) and RB_WHY say why.
    _rd_n=$1; _rd_fs=$2; _rd_b=$3; _rd_w=$4; shift 4
    _rd_p=$(partclone_for_fs "$_rd_fs"); rm -f "$_rd_w/rb.out" "$_rd_w/rb.err"
    if [ "$_rd_p" = partclone.dd ]; then
        fanout --readback "$@" "$_rd_n" "$_rd_b" < /dev/null > "$_rd_w/rb.out" 2> "$_rd_w/rb.err"; _rd_rc=$?
    else
        _rd_f=""; [ "$_rd_fs" = ntfs ] && _rd_f="-I"   # as in capture: the copy carries the source's own state
        # partclone dies of EPIPE once fanout has the map: its exit code is not
        # the evidence -- the header's and the map's CRCs are (fanout.c).
        # shellcheck disable=SC2086 # -I is a word or nothing
        "$_rd_p" -c $_rd_f -s "$_rd_n" -L "$_rd_w/rb.pcl.log" < /dev/null 2>> "$_rd_w/rb.err" \
            | fanout --readback "$@" "$_rd_n" > "$_rd_w/rb.out" 2>> "$_rd_w/rb.err"
        _rd_rc=$?
    fi
    _rd_e=$(grep 'fanout: readback' "$_rd_w/rb.err" 2>/dev/null | tail -n 1 | tr -d '\r')
    [ -z "$_rd_e" ] || log "readback $_rd_n: $_rd_e" > /dev/null   # the file only
    case "$_rd_rc" in
        0) return 0 ;;
        4) RB_KIND=failed; RB_WHY="$READBACK_FAIL (הקריאה מהדיסק נכשלה: ${_rd_e#*readback: })" ;;
        3) RB_KIND=unchecked; RB_WHY="מפת הבלוקים לא נקראה מהדיסק (${_rd_e#*readback: })" ;;
        *) RB_KIND=unchecked; RB_WHY="fanout --readback rc=$_rd_rc${_rd_e:+: ${_rd_e#*readback: }}" ;;
    esac
    return 1
}

rb_total() {   # $1 = fanout -x output. Prints "chunks bytes" of its n line; 1 = none.
    awk '$1 == "n" && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ { print $2, $3; f = 1 } END { exit !f }' "$1" 2>/dev/null
}

rb_list_xxh() {   # $1 = fanout -x output -> its list -> the XXH64 of the list, as the server takes it.
    awk '$1 == "c" { print $3 }' "$1" > "$1.list" && fanout --xxh64 < "$1.list"
}

_rb_part() {
    # $1 = disk, $2 = work dir, $3 = one line of RB_LIST: idx|fs|kind|a|b|c. Prints
    # "<state>|<why>": ok-full, ok-sample <picked>/<chunks>, none <why>, failed, unchecked.
    IFS='|' read -r _rp_i _rp_fs _rp_k _rp_a _rp_b _rp_c <<EOF
$3
EOF
    _rp_n=$(partition_node "$1" "$_rp_i")
    case "$_rp_k" in
    full)       # _rp_a = xxh64 of the list, _rp_b = chunks, _rp_c = bytes
        rb_digest "$_rp_n" "$_rp_fs" "$_rp_c" "$2" -x || { echo "$RB_KIND|מחיצה $_rp_i: $RB_WHY"; return; }
        _rp_t=$(rb_total "$2/rb.out") || { echo "unchecked|מחיצה $_rp_i: fanout --readback לא החזיר רשימה"; return; }
        [ "$_rp_t" = "$_rp_b $_rp_c" ] \
            || { echo "failed|מחיצה $_rp_i: $READBACK_FAIL (מפת הבלוקים בדיסק: ${_rp_t#* } בייט, באימג': $_rp_c)"; return; }
        _rp_x=$(rb_list_xxh "$2/rb.out") || { echo "unchecked|מחיצה $_rp_i: ה-XXH64 של הרשימה לא חושב"; return; }
        [ "$_rp_x" = "$_rp_a" ] || { echo "failed|מחיצה $_rp_i: $READBACK_FAIL"; return; }
        echo "ok-full|" ;;
    sample)     # _rp_a = the server's list (a file), _rp_b = chunks, _rp_c = bytes
        rb_digest "$_rp_n" "$_rp_fs" "$_rp_c" "$2" -x -s "$RB_SEED" "$RB_PERMILLE" \
            || { echo "$RB_KIND|מחיצה $_rp_i: $RB_WHY"; return; }
        _rp_t=$(rb_total "$2/rb.out") || { echo "unchecked|מחיצה $_rp_i: fanout --readback לא החזיר רשימה"; return; }
        [ "$_rp_t" = "$_rp_b $_rp_c" ] \
            || { echo "failed|מחיצה $_rp_i: $READBACK_FAIL (מפת הבלוקים בדיסק: ${_rp_t#* } בייט, באימג': $_rp_c)"; return; }
        [ "$_rp_b" = 0 ] && { echo "ok-full|"; return; }   # an empty filesystem: nothing to draw
        # Never "sampled" on fewer chunks than the fixed three (start, middle, end), and never on none:
        # a sample that read nothing vouches for nothing (principle 5).
        _rp_k=$(grep -c '^c ' "$2/rb.out"); _rp_m=3; [ "$_rp_b" -lt 3 ] && _rp_m=$_rp_b
        [ "${_rp_k:-0}" -ge "$_rp_m" ] \
            || { echo "failed|מחיצה $_rp_i: המדגם לא נקרא מהדיסק (${_rp_k:-0} חתיכות, לפחות $_rp_m נדרשות)"; return; }
        # line k+1 of the server's list is chunk k. The first chunk that differs, or how many matched.
        # As strings (#1890): a hash like 954480869382e837 is a number to awk, ~inf, and busybox
        # compares two of those as different -- two healthy drawers failed on chunk 8060 that way.
        _rp_v=$(awk 'NR == FNR { l[NR - 1] = $0; next } $1 == "c" { if ((l[$2] "") != ($3 "")) { print "bad " $2; bad = 1; exit } n++ }
                     END { if (!bad && n) print "ok " n }' "$_rp_a" "$2/rb.out")   # END runs after exit too
        case "$_rp_v" in
            "ok "*) echo "ok-sample|${_rp_v#ok } מתוך $_rp_b" ;;
            "bad "*) echo "failed|מחיצה $_rp_i: $READBACK_FAIL (חתיכה ${_rp_v#bad } של 4MiB מתוך $_rp_b)" ;;
            *) echo "unchecked|מחיצה $_rp_i: המדגם לא הושווה" ;;
        esac ;;
    sha)        # a manifest from before #1864, full mode: _rp_a = sha256, _rp_c = bytes
        rb_digest "$_rp_n" "$_rp_fs" "$_rp_c" "$2" || { echo "$RB_KIND|מחיצה $_rp_i: $RB_WHY"; return; }
        read -r _rp_s _rp_g _rp_x < "$2/rb.out" 2>/dev/null
        [ "$_rp_g" = "$_rp_c" ] \
            || { echo "failed|מחיצה $_rp_i: $READBACK_FAIL (מפת הבלוקים בדיסק: $_rp_g בייט, באימג': $_rp_c)"; return; }
        [ "$_rp_s" = "$_rp_a" ] || { echo "failed|מחיצה $_rp_i: $READBACK_FAIL"; return; }
        echo "ok-full|" ;;
    *)          # _rp_a = why there is nothing to compare with
        echo "none|$_rp_i" ;;
    esac
}

_rb_read() {
    # $1 = disk, $2 = work dir, $3 = node function. Flushes, then every line of
    # RB_LIST in order. Prints the disk's one result line (see the top).
    rb_flush "$1" "$3" || { echo "unchecked|$RB_WHY"; return; }
    _rk_none=""; _rk_s=0; _rk_sw=""
    while read -r _rk_l <&3; do
        _rk_r=$(_rb_part "$1" "$2" "$_rk_l")
        case "$_rk_r" in
            ok-full\|*) ;;
            ok-sample\|*) _rk_s=1; _rk_sw="$_rk_sw, מחיצה ${_rk_l%%|*}: ${_rk_r#*|}" ;;
            none\|*) _rk_none="$_rk_none ${_rk_r#*|}" ;;
            *) echo "$_rk_r"; return ;;
        esac
    done 3< "$RB_LIST"
    if [ -n "$_rk_none" ]; then
        echo "$RB_NONE_KIND|$RB_NONE_WHY (מחיצות:$_rk_none) — מה שנכתב לא נבדק מול הדיסק"
    elif [ "$_rk_s" = 1 ]; then
        echo "sampled|נבדק במדגם — חתיכות של 4MiB שנקראו בחזרה ותאמו —${_rk_sw#,}"
    else
        echo "verified|$(grep -c . "$RB_LIST") מחיצות, כל החתיכות"
    fi
}

_rb_apply() {
    # $1 = disk, $2 = its result line. The one place a result becomes a state.
    _ra_t="$RUN_DIR/targets/$1"; rm -f "$_ra_t/readback" "$_ra_t/readback_reason" "$_ra_t/readback_why"
    case "$2" in
        verified\|*|sampled\|*|unverified\|*|unfetched\|*)
            _ra_s=${2%%|*}; [ "$_ra_s" != unfetched ] || { _ra_s=unverified; echo list_not_fetched > "$_ra_t/readback_why"; }   # #1907
            echo "$_ra_s" > "$_ra_t/readback"; printf '%s' "${2#*|}" > "$_ra_t/readback_reason"
            log "$1: read back: ${2%%|*} -- ${2#*|}" ;;
        failed\|*) fail_written_target "$1" "${2#*|}" ;;
        unchecked\|*) fail_written_target "$1" "$READBACK_NOT_RUN — ${2#*|}" ;;
        *) fail_written_target "$1" "$READBACK_NOT_RUN (הבדיקה לא החזירה תוצאה)" ;;
    esac
}

_rb_start() {
    # $1 = disk, $2 = node function, $3 = reader (_rb_read, or _rb_raw for a local
    # clone), $4 = `source` (see _rb_raw). A background read, a child of this shell; RB_PID.
    _rs_w="$RUN_DIR/readback/$1"; rm -rf "$_rs_w"; mkdir -p "$_rs_w"
    "${3:-_rb_read}" "$1" "$_rs_w" "$2" "${4:-}" > "$_rs_w/read" 2>> "$LOG_FILE" &
    RB_PID=$!
}

_rb_wait() {
    # $1 = disk, $2 = pid. RB_RESULT = the read's own line.
    if wait_pid "$2" "$WAIT_READBACK_S" "האימות אחרי הכתיבה של $1"; then
        RB_RESULT=$(head -n 1 "$RUN_DIR/readback/$1/read" 2>/dev/null)
        [ -n "$RB_RESULT" ] || RB_RESULT="unchecked|הקריאה לא החזירה תוצאה"
    else
        RB_RESULT="unchecked|לא הסתיים תוך $WAIT_READBACK_S שניות"
    fi
}

_rb_live() {   # $@ = disks. The ones still being written: not failed, not cancelled.
    for _rl_d in "$@"; do
        case "$(cat "$RUN_DIR/targets/$_rl_d/state" 2>/dev/null)" in failed|cancelled) ;; *) printf '%s ' "$_rl_d" ;; esac
    done
}

rb_mode() {   # $1 = a JSON file with readback_mode (the manifest, or the last hello). sample|full.
    case "$(jq -r '.readback_mode // "sample"' "$1" 2>/dev/null)" in full) echo full ;; *) echo sample ;; esac
}

rb_seed() {   # the sample's seed: drawn, and logged -- the same seed draws the same chunks.
    _rs_s=$(od -An -N4 -tu4 /dev/urandom 2>/dev/null | tr -d ' \n')
    case "$_rs_s" in ''|*[!0-9]*) _rs_s=$(date +%s) ;; esac
    echo "$_rs_s"
}

_rb_fetch() {   # $1 = index, $2 = file, $3 = xxh64, $4 = lines. 0 = the manifest's own list is in $2. #1907: tries again.
    _rg_n=1
    until http_get "$RB_SERVER/api/v1/images/$RB_IMAGE/readback/$1" > "$2" 2>> "$LOG_FILE" \
          && [ "$(fanout --xxh64 < "$2" 2>/dev/null)" = "$3" ] && [ "$(grep -c . "$2")" = "$4" ]; do
        [ "$_rg_n" -lt "$RB_LIST_TRIES" ] && [ $(( $(date +%s) - _pl_t0 )) -lt "$RB_LIST_WAIT_S" ] || return 1
        log "readback: partition $1: the list did not come down (try $_rg_n of $RB_LIST_TRIES)" > /dev/null
        sleep "$RB_LIST_PAUSE_S"; _rg_n=$((_rg_n + 1))
    done
}

_rb_plan() {
    # $1 = manifest. RB_ALL (every non-swap partition: idx|fs|) and RB_LIST (what to
    # read and how: idx|fs|kind|a|b|c). The lists (sample mode) come from the server
    # and are checked against the manifest before use. 1 = the manifest was not read.
    jq -r '.partitions[] | select(.fs != "swap") | [.index, (.fs // ""), ""] | join("|")' "$1" \
        > "$RB_ALL" 2>> "$LOG_FILE" || return 1
    jq -r '.partitions[] | select(.fs != "swap") | [.index, (.fs // ""),
            (if ((.readback_xxh64 | type) == "string" and (.readback_chunks | type) == "number"
                 and (.readback_bytes | type) == "number" and .readback_chunk_bytes == 4194304)
             then "x|\(.readback_xxh64)|\(.readback_chunks)|\(.readback_bytes)"
             elif ((.readback_sha256 | type) == "string" and (.readback_bytes | type) == "number")
             then "sha|\(.readback_sha256)||\(.readback_bytes)" else "none|||" end)] | join("|")' "$1" \
        > "$RB_LIST.in" 2>> "$LOG_FILE" || return 1
    : > "$RB_LIST"; RB_NONE_WHY="לאימג' אין עדיין רשימת בדיקה"; RB_NONE_KIND=unverified; _pl_t0=$(date +%s)
    while IFS='|' read -r _pl_i _pl_fs _pl_k _pl_a _pl_b _pl_c <&4; do
        case "$RB_MODE:$_pl_k" in
            full:x)   echo "$_pl_i|$_pl_fs|full|$_pl_a|$_pl_b|$_pl_c" ;;
            full:sha) echo "$_pl_i|$_pl_fs|sha|$_pl_a||$_pl_c" ;;
            sample:x)
                _pl_f="$RUN_DIR/readback/list.$_pl_i"
                if _rb_fetch "$_pl_i" "$_pl_f" "$_pl_a" "$_pl_b"; then
                    echo "$_pl_i|$_pl_fs|sample|$_pl_f|$_pl_b|$_pl_c"
                else
                    log "readback: partition $_pl_i: the server's list was not fetched or is not the manifest's ($_rg_n tries)" > /dev/null
                    RB_NONE_WHY="רשימת הבדיקה לא הורדה מהשרת או אינה של המניפסט ($_rg_n ניסיונות)"; RB_NONE_KIND=unfetched; echo "$_pl_i|$_pl_fs|none|||"
                fi ;;
            *) echo "$_pl_i|$_pl_fs|none|||" ;;
        esac
    done 4< "$RB_LIST.in" >> "$RB_LIST"
}

readback_drawers() {
    # $1 = the manifest the server served, $2.. = disks; RB_SERVER and RB_IMAGE name
    # where the lists are. A round, a pull, a station: every disk in parallel; one
    # that fails fails alone. 0 = at least one disk is alive.
    _rr_m=$1; shift; mkdir -p "$RUN_DIR/readback"
    RB_ALL="$RUN_DIR/readback/all"; RB_LIST="$RUN_DIR/readback/list"
    RB_MODE=$(rb_mode "$_rr_m"); RB_SEED=$(rb_seed)
    _rb_plan "$_rr_m" || { _fail_alive "$*" "$READBACK_NOT_RUN — המניפסט לא נקרא"; return 1; }
    log "readback: mode $RB_MODE, sample seed $RB_SEED, $RB_PERMILLE/1000"
    _rr_jobs=""; _rr_t0=$(date +%s)
    for _rr_d in $(_rb_live "$@"); do
        target_set "$_rr_d" verifying; _rb_start "$_rr_d" partition_node; _rr_jobs="$_rr_jobs $_rr_d:$RB_PID"
    done
    for _rr_j in $_rr_jobs; do
        _rb_wait "${_rr_j%%:*}" "${_rr_j#*:}"; _rb_apply "${_rr_j%%:*}" "$RB_RESULT"
    done
    log "readback: $(echo "$_rr_jobs" | wc -w | tr -d " ") disk(s) in $(( $(date +%s) - _rr_t0 ))s ($RB_MODE)"   # #1805: the price, measured on metal
    _any_alive "$*"
}

# The local clone and the direct send's pass 1: readbacklc.sh.
