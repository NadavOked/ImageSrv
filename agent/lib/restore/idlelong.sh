# idlelong.sh -- #3268 (Nadav 18/11): the waiting-screen write check, the sustained part.
# POSIX sh (busybox ash).
#
# 12MiB (idlecheck.sh) fits in the write cache of a cheap SSD. APS-SL3N-512, cloner 2, 14-17/11: 209 MB/s in the
# 3 x 4MiB check and 43-55 MB/s written for minutes -- it passed 74 MB/s, stayed green on the screen and was found
# slow in the middle of the wave (#3295). So after the short check the disk is written WRITECHECK_LONG_MIB in a
# row, and the rate is taken from the **last half**: the first half is what the cache takes. Nadav 18/11: it adds
# 10-30 s a disk on the waiting screen, "it's fine". The drawers are checked side by side (idle_wc_scan), so the
# time is the slowest disk's, not the sum.
#
# **Segments (18/11, after the metal).** 256MiB was still in the APS's cache: 213 then 216 MB/s in the two halves, while
# the same drive writes 43 in the wave. A cache is a plateau too, so "write until the rate is stable" would stop at the
# second window, exactly where the single 256MiB stopped. What is written is a SEGMENT of WRITECHECK_LONG_MIB (its own copy,
# write, verified put-back, release -- the proven cycle, one open copy per serial), and the next segment follows it while
# the LAST window is not under the floor, nothing holds the drawers, there is room, and the next segment would still end
# inside IDLE_WC_LONG_BUDGET_S (Nadav's 30 s, copy and put-back included). The rate judged is the last window of the last
# segment. A budget of 0 is one segment, as before. How much a segment must be to pass the APS's cache: docs/architecture/interfaces.md.
#
# **The copy.** The rule is the short check's: no write without a copy on the server, no "ok" without a verified
# put-back, an open copy is put back at the next boot (_il_restore_ours, below). But 256MiB x four drawers is a
# gigabyte, the cloner's RAM is the initramfs's tmpfs (a size nobody has measured on the college's machines), and a
# copy in RAM does not survive the power cut it is there for. So the copy **never sits on the cloner**: it leaves as a
# stream (dd | curl) and comes back as a stream (curl | dd), and what proves the put-back is the sha256 of the disk's
# own bytes, read again. The body of the copy is the region, then the pattern (_IL_PAT MiB, written over it again and
# again). The only place it is held whole is a recovery after a power cut (a rare path, with a RAM check).
#
# Files: $RUN_DIR/idlecheck/long.<dev>/{pat,result,rates,up.json} -- the work dir; longrec.lock (one recovery at a time).

WRITECHECK_LONG_MIB="${WRITECHECK_LONG_MIB:-256}"   # 0 = no sustained part; else a multiple of 16, up to 1024
IDLE_WC_LONG_WAIT_S="${IDLE_WC_LONG_WAIT_S:-120}"   # the ceiling of the sustained write itself (256MiB at 2 MB/s)
IDLE_WC_LONG_NET_S="${IDLE_WC_LONG_NET_S:-180}"     # curl --max-time: the copy out, or back
IDLE_WC_LONG_BUDGET_S="${IDLE_WC_LONG_BUDGET_S:-30}" # no segment starts that would end after this many seconds since the sustained part began (0 = one segment)
_IL_PAT=8                                            # MiB of the pattern

_il_now() { cut -d' ' -f1 "$SYSROOT/proc/uptime" 2> /dev/null; }

_il_size() { # $1 = the disk in MiB. 0 = sets _il_n (MiB to write); 1 = no sustained part (_il_note = why; empty = switched off)
    _il_note=""; _il_n=$WRITECHECK_LONG_MIB
    [ "$_il_n" != 0 ] || return 1
    case "$_il_n" in ''|*[!0-9]*|0*) _il_n=0 ;; *) [ $((_il_n % 16)) = 0 ] && [ "$_il_n" -le 1024 ] || _il_n=0 ;; esac
    [ "$_il_n" != 0 ] || { log "WRITECHECK_LONG_MIB=$WRITECHECK_LONG_MIB is not a multiple of 16 up to 1024 -- 256"; _il_n=256; }
    [ "$1" -ge $((_il_n * 2)) ] || { _il_note="בדיקה קצרה בלבד: הדיסק קטן מ-$((_il_n * 2))MiB, אין בו מקום לכתיבה מתמשכת"; return 1; }
}

_il_bsha() { # $1 = node, $2 = start MiB, $3 = MiB, $4 = pattern file. sha256 of the region, then the pattern: the body the server keeps. A read that stops short is another sha256
    { dd if="$1" bs=1048576 skip="$2" count="$3" iflag=direct 2> /dev/null; cat "$4"; } | sha256sum | cut -d' ' -f1
}

_il_write() { # $1 = node, $2 = work dir (pat), $3 = start MiB, $4 = MiB. A background job (_ic_wait bounds it). $2/rates = "first-half last-half" MB/s, or nothing (5b). Prints state|reason
    _lw_c=$(($4 / _IL_PAT)); _lw_h=$((_lw_c / 2)); _lw_i=0; _lw_a=$(_il_now); _lw_m=""; : > "$2/rates"
    while [ "$_lw_i" -lt "$_lw_c" ]; do
        dd if="$2/pat" of="$1" bs=1048576 seek=$(($3 + _lw_i * _IL_PAT)) count="$_IL_PAT" oflag=direct conv=fsync 2> "$2/err" \
            || { echo "failed|ב-$((_lw_i * _IL_PAT)) MiB מתוך $4: $(_wc_why "$2/err" 'הכתיבה נכשלה')"; return; }
        _lw_i=$((_lw_i + 1)); [ "$_lw_i" != "$_lw_h" ] || _lw_m=$(_il_now)
    done
    awk -v a="$_lw_a" -v m="$_lw_m" -v b="$(_il_now)" -v h=$(($4 / 2 * 1048576)) 'BEGIN { n = "^[0-9]+\\.[0-9]+$"
        if (a ~ n && m ~ n && b ~ n && m + 0 > a + 0 && b + 0 > m + 0) printf "%d %d\n", h / (m - a) / 1000000, h / (b - m) / 1000000 }' > "$2/rates" 2> /dev/null
    echo "ok|"
}

_il_putback() { # $1 = disk, $2 = node, $3 = key, $4 = start MiB, $5 = MiB, $6 = pattern file, $7 = the body's sha256, $8 = copy id, $9 = serial. 0 = back, read again, the same sha256; 1 = not back; 2 = the disk was swapped: not written
    _ip_t=0
    while [ "$_ip_t" -lt 3 ]; do
        _ip_t=$((_ip_t + 1)); _ic_same "$1" "$3" || return 2
        curl -sfS --max-time "$IDLE_WC_LONG_NET_S" "$(_ic_url "/$8/blob" "$9")" 2>> "$LOG_FILE" | head -c $(($5 * 1048576)) \
            | dd of="$2" bs=1048576 seek="$4" count="$5" iflag=fullblock oflag=direct conv=fsync 2>> "$LOG_FILE"
        [ "$(_il_bsha "$2" "$4" "$5" "$6")" = "$7" ] && return 0   # the proof is the disk read again, not dd's word
        sleep 1
    done
    return 1
}

_il_end() { # $1 = state, $2 = why, $3 = mbps (or nothing), $4 = the first half's mbps (or nothing): the sustained part's word on the disk
    _ic_st=$1; _ic_why=$2; _ic_mb=$3; _ic_m1=${4:-}; rm -rf "$_il_w"
}

_il_seg() { # $1 = disk, $2 = node, $3 = key. ONE segment: the copy of _il_n MiB at _il_s, the write, the put-back, the release. 0 = done ($_il_w/rates); 1 = nothing was written, $_il_fw says why; 2 = a final word, $_il_fs / $_il_fw
    rm -rf "$_il_w"; mkdir -p "$_il_w"
    head -c $((_IL_PAT * 1048576)) /dev/urandom > "$_il_w/pat" 2> /dev/null
    [ "$(wc -c < "$_il_w/pat" 2> /dev/null | tr -d ' ')" = $((_IL_PAT * 1048576)) ] \
        || { _il_fw="לא נוצר דפוס לכתיבה המתמשכת (urandom) — לא נכתב"; return 1; }
    _il_h=$(_il_bsha "$2" "$_il_s" "$_il_n" "$_il_w/pat")
    { dd if="$2" bs=1048576 skip="$_il_s" count="$_il_n" iflag=direct 2> /dev/null; cat "$_il_w/pat"; } \
        | curl -sfS --max-time "$IDLE_WC_LONG_NET_S" -X POST -H 'Expect:' -H 'Content-Type: application/octet-stream' -T - \
            "$(_ic_url /long "$_il_ser")&size=$_il_b&regions=$((_il_s * 1048576)):$((_il_n * 1048576))&pattern=$((_IL_PAT * 1048576))&sha256=$_il_h" \
            > "$_il_w/up.json" 2>> "$LOG_FILE"   # the server counts the bytes and the sha256: a read that differed twice is refused there
    jq -e --arg s "$_il_h" '.ok == true and .sha256 == $s' "$_il_w/up.json" > /dev/null 2>&1 \
        || { _il_fw="השרת לא אישר שמירת עותק של הכתיבה המתמשכת — לא נכתב"; return 1; }
    _il_id=$(jq -r '.id' "$_il_w/up.json" 2> /dev/null)
    _ic_same "$1" "$3" || { _il_fw="הדיסק הוחלף בזמן שליחת העותק של הכתיבה המתמשכת — לא נכתב עליו"; return 1; }
    rm -f "$RUN_DIR/idlecheck/settled.$1"   # clean again only after a verified put-back
    ( _il_write "$2" "$_il_w" "$_il_s" "$_il_n" > "$_il_w/result" ) &
    _ic_wait "$!" "$2" "$IDLE_WC_LONG_WAIT_S"; _il_r=$?; _il_fs=failed
    [ "$_il_r" != 2 ] || { _il_fw="הכתיבה המתמשכת לא הסתיימה תוך $IDLE_WC_LONG_WAIT_S שניות, ו-dd שלה עוד כותב על הדיסק — לא שוחזר עכשיו; העותק נשמר בשרת ויוחזר כשלא יהיה dd חי"; return 2; }
    _il_putback "$1" "$2" "$3" "$_il_s" "$_il_n" "$_il_w/pat" "$_il_h" "$_il_id" "$_il_ser"; _il_p=$?
    [ "$_il_p" != 2 ] || { _il_fw="הדיסק הוחלף באמצע הכתיבה המתמשכת — לא נכתב על החדש; העותק של הקודם נשמר בשרת"; return 2; }
    [ "$_il_p" = 0 ] || { _il_fw="השחזור של תוכן הדיסק אחרי הכתיבה המתמשכת לא אומת — העותק נשמר בשרת ויוחזר בעלייה הבאה"; return 2; }
    printf '%s' "$3" > "$RUN_DIR/idlecheck/settled.$1"   # back, byte for byte: clean of the check
    _ic_release "$_il_id" "$_il_ser" || { _il_fs=unchecked; _il_fw="הדיסק שוחזר אחרי הכתיבה המתמשכת, אבל העותק בשרת לא נמחק"; return 2; }
    [ "$_il_r" = 0 ] || { _il_fw="הדיסק לא מקבל כתיבה מתמשכת — לא סיים לכתוב ${_il_n}MiB תוך $IDLE_WC_LONG_WAIT_S שניות"; return 2; }
    _il_x=$(head -n 1 "$_il_w/result" 2> /dev/null); _il_rt=$(cat "$_il_w/rates" 2> /dev/null)
    case "$_il_x" in
        ok\|*) ;;
        failed\|*) _il_fw="הדיסק לא מקבל כתיבה מתמשכת — ${_il_x#*|}"; return 2 ;;
        *) _il_fs=unchecked; _il_fw="הכתיבה המתמשכת לא החזירה תוצאה"; return 2 ;;
    esac
    case "$_il_rt" in [0-9]*\ [0-9]*) ;; *) _il_fs=unchecked; _il_fw="קצב הכתיבה המתמשכת לא נמדד (השעון לא נקרא) — לא נבדק"; return 2 ;; esac
}

_il_more() { # 0 = one more segment. The LAST window is what is judged, so none once it is under the floor (slow is slow: nothing left to find), a task holds the drawers, there is no room, or the next one would end past IDLE_WC_LONG_BUDGET_S (it takes as long as the last did; a clock not read = no more). Sets _il_stop
    _il_stop="no room"; [ $((_il_s + 2 * _il_n)) -le "$_il_dm" ] || return 1
    _il_stop="a task holds the drawers"; [ ! -e "$RUN_DIR/idlecheck/hold" ] || return 1
    _il_stop="the last window is under the floor"; [ "$_il_lr" -ge "$(_ic_floor)" ] || return 1
    _il_stop="the time budget"; [ "$IDLE_WC_LONG_BUDGET_S" != 0 ] || return 1
    awk -v t0="$_il_t0" -v ts="$_il_ts" -v tn="$(_il_now)" -v b="$IDLE_WC_LONG_BUDGET_S" 'BEGIN { n = "^[0-9]+\.[0-9]+$"
        exit !(t0 ~ n && ts ~ n && tn ~ n && tn - t0 + (tn - ts) <= b) }' 2> /dev/null
}

idle_long() { # $1 = disk, $2 = node, $3 = key (serial|sectors), $4 = the disk in bytes. The short check is ok and put back; sets _ic_st/_ic_why/_ic_mb/_ic_m1
    _il_size $(($4 / 1048576)) || { [ -z "$_il_note" ] || _ic_why="$_ic_why; $_il_note"; return 0; }
    _il_dm=$(($4 / 1048576)); _il_b=$4; _il_ser=${3%|*}; _il_w="$RUN_DIR/idlecheck/long.$1"; _il_sw=$_ic_why
    _il_s=$((_il_dm / 2)); _il_k=0; _il_t0=$(_il_now)   # the middle of the disk, like the second place; each segment after it starts where the last ended
    while :; do
        _il_ts=$(_il_now); _il_seg "$1" "$2" "$3"; _il_c=$?
        [ "$_il_c" != 2 ] || { _il_end "$_il_fs" "$_il_fw" ""; return 0; }
        if [ "$_il_c" = 1 ]; then   # nothing was written: the first segment is "unchecked"; a later one only ends the loop, the disk is as the last put-back left it
            [ "$_il_k" != 0 ] || { _il_end unchecked "$_il_fw" ""; return 0; }
            log "$1: sustained write check: segment $((_il_k + 1)) not started: $_il_fw"; _il_stop="the next segment did not start"; break
        fi
        _il_rt=$(cat "$_il_w/rates"); _il_lr=${_il_rt#* }; [ "$_il_k" != 0 ] || _il_fr=${_il_rt% *}   # the first window of the first segment, the last window of the last
        _il_k=$((_il_k + 1)); _il_more || break
        _il_s=$((_il_s + _il_n))
    done
    log "$1: sustained write check: $_il_k x ${_il_n}MiB, stopped by: $_il_stop"
    if [ "$_il_k" = 1 ]; then _il_end ok "$_il_sw; כתיבה מתמשכת של ${_il_n}MiB: $_il_lr MB/s בחצי האחרון ($_il_fr בחצי הראשון)" "$_il_lr" "$_il_fr"
    else _il_end ok "$_il_sw; כתיבה מתמשכת של $((_il_k * _il_n))MiB ב-$_il_k מקטעים: $_il_lr MB/s בחלון האחרון ($_il_fr בראשון)" "$_il_lr" "$_il_fr"; fi
}

_il_restore_ours() { # $1 = disk, $2 = work dir, $3 = key, $4 = copy id, $5 = serial, $6 = body sha256, $7 = "offset:length" (bytes), $8 = pattern bytes. The contract of _ic_restore_ours: sets _ic_o/_ic_u/_ic_x; 0 = no block of the pattern is left. One recovery at a time: the copy is held whole here
    _ix_l="$RUN_DIR/idlecheck/longrec.lock"; _ix_t=0
    until mkdir "$_ix_l" 2> /dev/null; do
        [ "$_ix_t" -lt 900 ] || { log "$DEVROOT/$1: another recovery of a sustained-check copy holds the room for 900s -- not now"; return 1; }
        sleep 1; _ix_t=$((_ix_t + 1))
    done
    _il_recover "$@"; _ix_rc=$?
    rm -rf "$_ix_l" "$2/body"; return "$_ix_rc"
}

_il_recover() { # the body of _il_restore_ours, under its lock
    _ix_o=${7%:*}; _ix_z=${7#*:}; _ix_p=$8
    [ $((_ix_z % 1048576)) = 0 ] && [ $((_ix_p % 1048576)) = 0 ] && [ "$_ix_p" -gt 0 ] || { log "$DEVROOT/$1: the open sustained copy $4 is not whole MiB -- not put back"; return 1; }
    _ix_av=$(awk '/^MemAvailable:/ { print $2; exit }' "$SYSROOT/proc/meminfo" 2> /dev/null)   # the copy comes whole into RAM, here only
    case "$_ix_av" in ''|*[!0-9]*) log "$DEVROOT/$1: MemAvailable not read -- the sustained copy $4 is not brought into RAM"; return 1 ;; esac
    [ "$_ix_av" -ge $(((_ix_z + _ix_p) / 1024 + 131072)) ] || { log "$DEVROOT/$1: $_ix_av KiB free, the sustained copy $4 needs $(((_ix_z + _ix_p) / 1048576))MiB and 128MiB more -- not now"; return 1; }
    curl -sfS --max-time "$IDLE_WC_LONG_NET_S" -o "$2/body" "$(_ic_url "/$4/blob" "$5")" 2>> "$LOG_FILE" || return 1
    [ "$(_ic_sha "$2/body")" = "$6" ] || { log "$DEVROOT/$1: the sustained copy $4 did not match its sha256 -- not put back"; return 1; }
    [ "$(wc -c < "$2/body" | tr -d ' ')" = $((_ix_z + _ix_p)) ] || { log "$DEVROOT/$1: the sustained copy $4 is not its region and its pattern -- not put back"; return 1; }
    dd if="$2/body" of="$2/pat" bs=1048576 skip=$((_ix_z / 1048576)) count=$((_ix_p / 1048576)) 2> "$2/err" || return 1
    _ix_n=$((_ix_z / 1048576)); _ix_k=0; _ix_a=0; _ix_b=0; _ix_c=0
    while [ "$_ix_k" -lt "$_ix_n" ]; do   # a MiB at a time, each the way a short copy's region is: the original, the pattern, or something else
        dd if="$2/body" of="$2/r.1" bs=1048576 skip="$_ix_k" count=1 2> "$2/err" || return 1
        dd if="$2/pat" of="$2/w.1" bs=1048576 skip=$((_ix_k % (_ix_p / 1048576))) count=1 2> "$2/err" || return 1
        _ic_restore_ours "$1" "$2" "$3" "$((_ix_o + _ix_k * 1048576)):1048576" || return 1
        _ix_a=$((_ix_a + _ic_o)); _ix_b=$((_ix_b + _ic_u)); _ix_c=$((_ix_c + _ic_x)); _ix_k=$((_ix_k + 1))
    done
    _ic_o=$_ix_a; _ic_u=$_ix_b; _ic_x=$_ix_c
}
