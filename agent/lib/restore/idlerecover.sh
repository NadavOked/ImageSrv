# idlerecover.sh -- #3268: the copy of the waiting-screen write check, put back only where it is ours.
# POSIX sh (busybox ash).
#
# A copy still open says the check was cut (a power cut, a pulled disk, a dd past its ceiling). Putting
# it back blindly could overwrite an image written since, or another disk with the same serial. So the
# copy carries, after the originals, exactly what the check was going to write in each region (the
# same lengths, region by region: _ic_body), and the recovery compares **every 4KiB block** (#3271 H3):
#   o -- it holds the original: nothing to do;
#   u -- it holds the check's own pattern (a place's, or a marker's over it): the original is put back;
#   x -- anything else (an image written since, another disk): not touched.
# A region cut in the middle of a 4MiB write is half o, half u -- and comes back whole.
# The copy goes from the server only when no block is x. Otherwise it **stays** (never deleted by the
# agent): some x = stale, marked on the server and kept; all x and no o/u = most likely another disk
# with the same serial and size, and the copy is not touched at all. Either way the disk itself carries
# nothing of the check any more (every u was put back), so it is settled (idlegate.sh).

_ic_sha() { sha256sum "$1" 2> /dev/null | cut -d' ' -f1; }

_ic_marks() { # $1 = work dir, $2.. = regions: three places, then markers. mk.1..N, made before the copy leaves
    _im_w=$1; _im_k=1; _im_n=$(($# - 4))
    while [ "$_im_k" -le "$_im_n" ]; do
        [ -s "$_im_w/mk.$_im_k" ] || head -c 4096 /dev/urandom > "$_im_w/mk.$_im_k" 2> /dev/null
        [ "$(wc -c < "$_im_w/mk.$_im_k" 2> /dev/null | tr -d ' ')" = 4096 ] || return 1
        _im_k=$((_im_k + 1))
    done
}

_ic_body() { # $1 = work dir (blob, pat.1-3, mk.N), $2.. = regions. body = the originals, then what the check writes, region by region
    _ib_w=$1; shift; _ib_i=0; cp "$_ib_w/blob" "$_ib_w/body" || return 1
    for _ib_r in "$@"; do
        _ib_i=$((_ib_i + 1)); _ib_f="$_ib_w/pat.$_ib_i"; [ "$_ib_i" -le 3 ] || _ib_f="$_ib_w/mk.$((_ib_i - 3))"
        [ "$(wc -c < "$_ib_f" 2> /dev/null | tr -d ' ')" = "${_ib_r#*:}" ] || return 1
        cat "$_ib_f" >> "$_ib_w/body" || return 1
    done
}

_ic_alt() { # $1 = work dir (w.N), $2.. = regions. a.N: the other thing a block of region N may hold -- a place with the markers written over it; a marker that sits in a place, the place's pattern under it
    _ia_w=$1; shift; _ia_i=0
    for _ia_r in "$@"; do _ia_i=$((_ia_i + 1)); cp "$_ia_w/w.$_ia_i" "$_ia_w/a.$_ia_i" || return 1; done
    _ia_i=0
    for _ia_r in "$@"; do
        _ia_i=$((_ia_i + 1)); [ "$_ia_i" -gt 3 ] || continue; _ia_mo=${_ia_r%:*}; _ia_j=0
        for _ia_p in "$@"; do
            _ia_j=$((_ia_j + 1)); [ "$_ia_j" -le 3 ] || break
            [ "$_ia_mo" -ge "${_ia_p%:*}" ] && [ "$_ia_mo" -lt $((${_ia_p%:*} + ${_ia_p#*:})) ] || continue
            _ia_b=$(((_ia_mo - ${_ia_p%:*}) / 4096))
            dd if="$_ia_w/w.$_ia_i" of="$_ia_w/a.$_ia_j" bs=4096 seek="$_ia_b" count=1 conv=notrunc 2> /dev/null || return 1
            dd if="$_ia_w/w.$_ia_j" of="$_ia_w/a.$_ia_i" bs=4096 skip="$_ia_b" count=1 conv=notrunc 2> /dev/null || return 1
        done
    done
}

_ic_class() { # $1 = work dir (cur/r/w/a.N), $2 = N, $3 = blocks. Prints "o u x" (how many of each); u.N = the runs of u, "first count" a line
    rm -f "$1/u.$2"
    for _ic_f in r w a; do   # the whole region one thing: no pass block by block
        cmp -s "$1/cur.$2" "$1/$_ic_f.$2"; _ic_rc=$?   # 0 = the same, 1 = not (an answer), else cmp broke
        case "$_ic_rc:$_ic_f" in 0:r) echo "$3 0 0"; : > "$1/u.$2"; return 0 ;; 0:*) echo "0 $3 0"; echo "0 $3" > "$1/u.$2"; return 0 ;; 1:*) ;; *) return 1 ;; esac
    done
    for _ic_f in cur r w a; do od -An -v -tx8 -w4096 "$1/$_ic_f.$2" > "$1/$_ic_f.$2.od" || return 1; done   # a line a 4KiB block, the same on busybox and GNU
    awk -v R="$1/r.$2.od" -v W="$1/w.$2.od" -v A="$1/a.$2.od" -v U="$1/u.$2" -v n="$3" '
        BEGIN { s = -1; printf "" > U }
        { if ((getline r < R) <= 0 || (getline p < W) <= 0 || (getline q < A) <= 0) { bad = 1; exit }
          b = NR - 1; l = $0 ""
          if (l == r "") { o++; next }
          if (l != p "" && l != q "") { x++; next }
          u++
          if (s >= 0 && b == s + k) k++; else { if (s >= 0) print s, k > U; s = b; k = 1 } }
        END { if (bad || NR != n) exit 2; if (s >= 0) print s, k > U; print o + 0, u + 0, x + 0 }' "$1/cur.$2.od" || return 1
}

_ic_restore_ours() { # $1 = disk, $2 = work dir (r/w.N), $3 = its key, $4.. = regions. Sets _ic_o/_ic_u/_ic_x (blocks). 0 = no u is left on the disk
    _io_d=$1; _io_n="$DEVROOT/$1"; _io_w=$2; _io_k=$3; shift 3; _io_i=0; _ic_o=0; _ic_u=0; _ic_x=0
    _ic_alt "$_io_w" "$@" || return 1
    for _io_r in "$@"; do   # the whole classification first, from the disk as it is now
        _io_i=$((_io_i + 1)); _io_c=$((${_io_r#*:} / 4096))
        dd if="$_io_n" of="$_io_w/cur.$_io_i" bs=4096 skip=$((${_io_r%:*} / 4096)) count="$_io_c" iflag=direct 2> "$_io_w/err" || return 1   # not read = not classified: nothing is written
        _io_t=$(_ic_class "$_io_w" "$_io_i" "$_io_c") || return 1
        _ic_o=$((_ic_o + ${_io_t%% *})); _io_t=${_io_t#* }; _ic_u=$((_ic_u + ${_io_t%% *})); _ic_x=$((_ic_x + ${_io_t#* }))
    done
    [ "$_ic_u" -gt 0 ] || return 0
    _io_i=0
    for _io_r in "$@"; do
        _io_i=$((_io_i + 1)); _io_s=$((${_io_r%:*} / 4096)); _io_c=$((${_io_r#*:} / 4096))
        [ -s "$_io_w/u.$_io_i" ] || continue
        while read -r _io_b _io_m; do
            _ic_same "$_io_d" "$_io_k" || { log "$_io_n: the disk under the name changed -- the copy is not put back on it"; return 1; }   # #3271 H1
            dd if="$_io_w/r.$_io_i" of="$_io_n" bs=4096 skip="$_io_b" seek=$((_io_s + _io_b)) count="$_io_m" oflag=direct conv=fsync 2> "$_io_w/err" || return 1
        done < "$_io_w/u.$_io_i"
        dd if="$_io_n" of="$_io_w/cur.$_io_i" bs=4096 skip="$_io_s" count="$_io_c" iflag=direct 2> "$_io_w/err" || return 1   # read back, classified again:
        _io_t=$(_ic_class "$_io_w" "$_io_i" "$_io_c") || return 1; _io_t=${_io_t#* }
        [ "${_io_t%% *}" = 0 ] || { log "$_io_n: region $_io_r still holds the check's pattern after it was put back"; return 1; }
    done
}

_ic_valid() { # $1 = id, $2 = sha256, $3 = size, $4 = regions. #3271 L2: the agent checks what it is going to write by, not only the server
    case "$1" in ''|*[!0-9a-f]*) return 1 ;; esac; [ "${#1}" = 32 ] || return 1
    case "$2" in ''|*[!0-9a-f]*) return 1 ;; esac; [ "${#2}" = 64 ] || return 1
    case "$3" in ''|*[!0-9]*) return 1 ;; esac
    case "$4" in ''|*[!0-9:,]*|*,|,*|*,,*) return 1 ;; esac
    for _iv_r in $(echo "$4" | tr ',' ' '); do
        case "$_iv_r" in *:*:*) return 1 ;; [0-9]*:[0-9]*) ;; *) return 1 ;; esac
        [ $((${_iv_r%:*} % 4096)) = 0 ] && [ $((${_iv_r#*:} % 4096)) = 0 ] && [ "${_iv_r#*:}" -gt 0 ] || return 1
    done
}

_ic_mark_stale() { # $1 = id, $2 = key, $3 = blocks not ours. The copy stays on the server, marked ("התיישן" in its journal)
    curl -sfS --max-time "$HTTP_TIMEOUT" -X POST "$(_ic_url "/$1/stale" "$2")&blocks=$3" > /dev/null 2>> "$LOG_FILE"
}

_ic_load_short() { # the short copy: from the server, checked, split into r.N (the original) and w.N (what the check wrote), put back only where it is ours. $1 = disk, $2 = work dir. 0 = no u is left; sets _ic_o/_ic_u/_ic_x
    curl -sfS --max-time "$IDLE_WC_UPLOAD_S" -o "$_ir_w/body" "$(_ic_url "/$_ir_cid/blob" "$_ir_k")" 2>> "$LOG_FILE" || return 1
    [ "$(_ic_sha "$_ir_w/body")" = "$_ir_s" ] || { log "$_ir_n: the copy $_ir_cid did not match its sha256 -- not put back"; return 1; }
    _ir_g=$(echo "$_ir_g" | tr ',' ' '); _ir_t=0
    for _ir_r in $_ir_g; do _ir_t=$((_ir_t + ${_ir_r#*:})); done
    [ "$(wc -c < "$_ir_w/body" | tr -d ' ')" = $((_ir_t * 2)) ] || { log "$_ir_n: the copy $_ir_cid is not the originals and the patterns of its regions -- not put back"; return 1; }
    _ir_p=0; _ir_i=0
    for _ir_r in $_ir_g; do   # r.N = the original, w.N = what the check wrote there
        _ir_i=$((_ir_i + 1)); _ir_c=$((${_ir_r#*:} / 4096))
        dd if="$_ir_w/body" of="$_ir_w/r.$_ir_i" bs=4096 skip=$((_ir_p / 4096)) count="$_ir_c" 2> "$_ir_w/err" || return 1
        dd if="$_ir_w/body" of="$_ir_w/w.$_ir_i" bs=4096 skip=$(((_ir_t + _ir_p) / 4096)) count="$_ir_c" 2> "$_ir_w/err" || return 1
        _ir_p=$((_ir_p + ${_ir_r#*:}))
    done
    # shellcheck disable=SC2086 # the regions are a word list
    _ic_restore_ours "$1" "$_ir_w" "$_ir_id" $_ir_g || { log "$_ir_n: putting the copy $_ir_cid back failed -- it stays on the server"; return 1; }
}

idle_wc_recover() { # $1 = disk. 0 = nothing of a check is left on it (settled.<dev>); _ic_kept=1 when an open copy stays on the server. 1 = not known, or not put back: the disk is left alone
    idle_ceilings_refresh; mkdir -p "$RUN_DIR/idlecheck"; rm -f "$RUN_DIR/idlecheck/settled.$1"   # #3672 (A15): IDLE_WC_UPLOAD_S below is the brain's
    _ir_id=$(_ic_ident "$1") || return 0   # no serial: no copy could have been made
    _ir_k=${_ir_id%|*}; _ir_n="$DEVROOT/$1"
    _ir_l=$(curl -sfS --max-time "$HTTP_TIMEOUT" "$(_ic_url "" "$_ir_k")" 2>> "$LOG_FILE") || return 1   # the server did not answer: unknown, not "none"
    _ir_c=$(printf '%s' "$_ir_l" | jq -r 'if .ok == true then (.backups | length) else "x" end' 2> /dev/null)
    case "$_ir_c" in 0) printf '%s' "$_ir_id" > "$RUN_DIR/idlecheck/settled.$1"; return 0 ;; ''|*[!0-9]*) return 1 ;; esac
    _ic_kept=1   # from here the copy is on the server until a verified restore drops it
    [ "$_ir_c" = 1 ] || { log "$_ir_n: $_ir_c open copies of serial $_ir_k (twin disks?) -- not known which one is this disk's: nothing put back, nothing dropped, not settled"; return 1; }   # #3300
    _ir_cid=$(printf '%s' "$_ir_l" | jq -r '.backups[0].id' 2> /dev/null); _ir_z=$(printf '%s' "$_ir_l" | jq -r '.backups[0].size' 2> /dev/null)
    _ir_g=$(printf '%s' "$_ir_l" | jq -r '.backups[0].regions' 2> /dev/null); _ir_s=$(printf '%s' "$_ir_l" | jq -r '.backups[0].sha256' 2> /dev/null)
    _ic_valid "$_ir_cid" "$_ir_s" "$_ir_z" "$_ir_g" || { log "$_ir_n: the open copy of serial $_ir_k is not readable -- not put back"; return 1; }
    [ "$_ir_z" = "$(($(cat "$SYSROOT/sys/block/$1/size" 2> /dev/null || echo 0) * 512))" ] \
        || { log "$_ir_n: the open copy of serial $_ir_k is of a disk of another size -- not this disk; kept"; printf '%s' "$_ir_id" > "$RUN_DIR/idlecheck/settled.$1"; return 0; }
    _ir_p=$(_ic_writers "$_ir_n") && [ -z "$_ir_p" ] || { log "$_ir_n: a dd still writes the disk (or /proc was not read) -- not put back now"; return 1; }   # #3271 H4
    _ir_w="$RUN_DIR/idlecheck/work.$1"; rm -rf "$_ir_w"; mkdir -p "$_ir_w"
    _ir_pl=$(printf '%s' "$_ir_l" | jq -r '.backups[0].pattern // 0' 2> /dev/null)
    if [ "$(printf '%s' "$_ir_l" | jq -r '.backups[0].kind // "short"' 2> /dev/null)" = long ]; then   # #3268 (18/11): the sustained part's copy: one region, its pattern repeated (idlelong.sh)
        _il_restore_ours "$1" "$_ir_w" "$_ir_id" "$_ir_cid" "$_ir_k" "$_ir_s" "$_ir_g" "$_ir_pl" || { log "$_ir_n: putting the sustained copy $_ir_cid back failed -- it stays on the server"; return 1; }
    else
        _ic_load_short "$1" "$_ir_w" || return 1
    fi
    printf '%s' "$_ir_id" > "$RUN_DIR/idlecheck/settled.$1"   # no block of the check is left on it
    if [ "$_ic_x" = 0 ]; then
        log "$_ir_n: WRITE CHECK RECOVERY: a copy of serial $_ir_k was open -- $_ic_u blocks put back and verified"
        if _ic_release "$_ir_cid" "$_ir_k"; then _ic_kept=0; else log "$_ir_n: the copy $_ir_cid could not be dropped on the server -- kept"; fi
    elif [ $((_ic_o + _ic_u)) = 0 ]; then
        log "$_ir_n: WRITE CHECK RECOVERY: no block holds the original or the check's pattern -- another disk with serial $_ir_k, or written over whole; not touched, the copy $_ir_cid kept"
    else
        log "$_ir_n: WRITE CHECK RECOVERY: the copy $_ir_cid is STALE ($_ic_x blocks hold something else, not touched) -- $_ic_u blocks put back; the copy is kept"
        _ic_mark_stale "$_ir_cid" "$_ir_k" "$_ic_x" || log "$_ir_n: the copy $_ir_cid could not be marked stale on the server -- kept all the same"
    fi
    rm -rf "$_ir_w"
}
