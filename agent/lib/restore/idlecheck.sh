# idlecheck.sh -- #3268 (Nadav 14/11): the write check on the waiting screen, with a copy.
# POSIX sh (busybox ash).
#
# A disk that is slow was found only after the round began. Nadav: "it should
# have been red before the round". The check of writecheck.sh writes 3 x 4MiB and
# a few 4KiB markers and never puts them back (apply_gpt wipes them a second
# later) -- fine for a disk chosen for the round, destruction for a disk that is
# only sitting in a drawer (a capture source, a disk with data). So here it runs
# with a copy (the decision of 14/11 updates 16/09 and #2848 for the write check only):
#   1. read exactly the regions the check will overwrite;
#   2. send them to the server (write_backup.py), with what the check will write there,
#      and wait for ok + the same sha256 -- no confirmation, no write ("unchecked");
#   3. the check, measured as in the round (_wc_io);
#   4. put the originals back, fsync, read back, compare the sha256 -- only then
#      the server drops the copy. A copy still open (a power cut in the middle) is put
#      back block by block where the check's pattern still is (idle_wc_recover) -- by the
#      scanner, and by every task before it reads the disk (idlegate.sh).
# Before every write, the disk under the name is asked again for its serial and size: a
# drawer swapped meanwhile is never written (#3271 H1). A check past its ceiling is not put
# back while a dd of it still writes the disk (H4). Not on a disk with BitLocker or a
# hibernated/dirty NTFS (read-only probes), with a mounted partition, red or unread SMART or
# an open disk failure on the server (M2), outside list_drawers (USB/SD/eMMC, M1), while a
# task holds the drawers (idlegate.sh), not without a serial.
# The result is per **insertion**: a disk that went out and came back is checked
# again even if the serial is known. The round uses the result of its disk
# (idle_wc_adopt) instead of writing the check again.
#
# Files: $RUN_DIR/idlecheck/res.<dev>/{key,state,reason,mbps}, busy.<dev>/ (the lock), work.<dev>/.

# #3672 (A15, Nadav 18/11): the three ceilings of this check -- the upload, how long a task waits for a running check on its disk, and (idlegate.sh) how
# long a dd that still writes it is waited for -- are the brain's now (hello answer, idle_ceilings; server/brain/idleceil.py, K-IDLE;
# idle_ceilings_refresh below). What is written here is the value of today, for a server that decides nothing: no decision = the values of today.
IDLE_WC_UPLOAD_S="${IDLE_WC_UPLOAD_S:-60}"
IDLE_WC_BUSY_WAIT_S="${IDLE_WC_BUSY_WAIT_S:-300}"

idle_ceilings_refresh() { # the brain's ceilings from the last answer, each a whole number 1..3600; a missing, broken or out-of-range one is the value this process loaded (60 / 300 / 10, or the environment's)
    # idle_wc_scan calls this once a second per cloner: jq only when response.json changed (mtime + size) since this process last read it.
    # No stat, or no file = no signature = read it every time, as before.
    _icr_g=$(stat -c '%Y %s' "${RUN_DIR:-/nonexistent}/response.json" 2> /dev/null) || _icr_g=
    [ -z "$_icr_g" ] || [ "$_icr_g" != "${_icr_sig:-}" ] || return 0
    : "${_ic_d_b:=$IDLE_WC_BUSY_WAIT_S}" "${_ic_d_u:=$IDLE_WC_UPLOAD_S}" "${_ic_d_k:=${IDLE_WC_KILL_S:-10}}"
    read -r _icr_b _icr_u _icr_k << EOF
$(jq -r '.idle_ceilings | if type == "object" then [.busy_wait_s, .upload_s, .kill_wait_s] | map(if type == "number" and . == floor and . >= 1 and . <= 3600 then tostring else "-" end) | join(" ") else "- - -" end' "${RUN_DIR:-/nonexistent}/response.json" 2> /dev/null)
EOF
    _icr_sig=$_icr_g; IDLE_WC_BUSY_WAIT_S=$_ic_d_b; IDLE_WC_UPLOAD_S=$_ic_d_u; IDLE_WC_KILL_S=$_ic_d_k
    case "$_icr_b" in ''|*[!0-9]*) ;; *) IDLE_WC_BUSY_WAIT_S=$_icr_b ;; esac
    case "$_icr_u" in ''|*[!0-9]*) ;; *) IDLE_WC_UPLOAD_S=$_icr_u ;; esac
    case "$_icr_k" in ''|*[!0-9]*) ;; *) IDLE_WC_KILL_S=$_icr_k ;; esac
}

_ic_key() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }   # the serial on the wire and in file names

_ic_ident() { # $1 = disk. serial|sectors, or nothing: no serial = no copy to find again
    _ii_s=$(_ic_key "$(disk_serial "$1")"); [ -n "$_ii_s" ] || return 1
    _ii_z=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null)
    case "$_ii_z" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s|%s' "$_ii_s" "$_ii_z"
}

_ic_regions() { # $1 = the disk in MiB. "offset:length" in bytes -- the places and the markers of writecheck.sh
    _ir_w=$((WRITECHECK_MIB * 1048576))
    _ir_o="0:$_ir_w $(($1 / 2 * 1048576)):$_ir_w $((($1 - WRITECHECK_MIB) * 1048576)):$_ir_w 0:4096"
    _ir_m=1
    while [ "$_ir_m" -lt "$1" ]; do _ir_o="$_ir_o $((_ir_m * 1048576)):4096"; _ir_m=$((_ir_m * 2)); done
    printf '%s %s' "$_ir_o" "$(($1 * 1048576 - 4096)):4096"
}

_ic_res() { # $1 = disk, $2 = key, $3 = state, $4 = reason, $5 = mbps (or nothing), $6 = the first half's mbps of the sustained part (or nothing; idlebrain.sh reports both)
    _ir_d="$RUN_DIR/idlecheck/res.$1"; rm -rf "$_ir_d"; mkdir -p "$_ir_d"
    printf '%s' "$2" > "$_ir_d/key"; printf '%s' "$4" > "$_ir_d/reason"; printf '%s' "$5" > "$_ir_d/mbps"; printf '%s' "${6:-}" > "$_ir_d/first"
    printf '%s' "$3" > "$_ir_d/state"   # the state last: a reader that sees it sees the rest
    log "$1: waiting-screen write check: $3 -- $4"
}

_ic_save() { # $1 = node, $2 = work dir, $3.. = regions. r.N per region, and blob = their concatenation
    _is_n=$1; _is_w=$2; shift 2; _is_i=0; : > "$_is_w/blob" || return 1
    for _is_r in "$@"; do
        _is_i=$((_is_i + 1))
        dd if="$_is_n" of="$_is_w/r.$_is_i" bs=4096 skip=$((${_is_r%:*} / 4096)) count=$((${_is_r#*:} / 4096)) \
            iflag=direct 2> "$_is_w/err" || return 1
        [ "$(wc -c < "$_is_w/r.$_is_i" 2>/dev/null | tr -d ' ')" = "${_is_r#*:}" ] || return 1
        cat "$_is_w/r.$_is_i" >> "$_is_w/blob" || return 1
    done
}

_ic_same() { [ "$(_ic_ident "$1")" = "$2" ]; }   # $1 = disk, $2 = serial|sectors: still the disk the copy is of (#3271 H1, as assert_target)

_ic_putback() { # $1 = node, $2 = work dir (r.N), $3 = sha256 of the originals, $4 = disk, $5 = its key, $6.. = regions. 0 = written, read back, the sha256 matches; 2 = the disk was swapped: not written
    _ip_n=$1; _ip_w=$2; _ip_s=$3; _ip_d=$4; _ip_k=$5; shift 5; _ip_i=0
    for _ip_r in "$@"; do
        _ip_i=$((_ip_i + 1)); _ic_same "$_ip_d" "$_ip_k" || return 2
        dd if="$_ip_w/r.$_ip_i" of="$_ip_n" bs=4096 seek=$((${_ip_r%:*} / 4096)) count=$((${_ip_r#*:} / 4096)) \
            oflag=direct conv=fsync 2> "$_ip_w/err" || return 1
    done
    _ip_i=0; : > "$_ip_w/back" || return 1
    for _ip_r in "$@"; do
        _ip_i=$((_ip_i + 1))
        dd if="$_ip_n" of="$_ip_w/back.$_ip_i" bs=4096 skip=$((${_ip_r%:*} / 4096)) count=$((${_ip_r#*:} / 4096)) \
            iflag=direct 2> "$_ip_w/err" || return 1
        cat "$_ip_w/back.$_ip_i" >> "$_ip_w/back" || return 1
    done
    [ "$(sha256sum "$_ip_w/back" 2>/dev/null | cut -d' ' -f1)" = "$_ip_s" ]
}

_ic_url() { printf '%s/api/v1/agent/write-backup%s?mac=%s&serial=%s' "$SERVER" "$1" "$MAC" "$2"; }

_ic_release() { # $1 = id, $2 = key. Only after a verified restore. Three tries
    for _ie_t in 1 2 3; do
        curl -sfS --max-time "$HTTP_TIMEOUT" -X DELETE "$(_ic_url "/$1" "$2")" \
            > /dev/null 2>> "$LOG_FILE" && return 0
        sleep 1
    done
    return 1
}

_ic_guard() { # $1 = disk. Prints why the disk is not checked, or nothing
    _ig_p=$(grep -c "^$DEVROOT/$1" "$SYSROOT/proc/mounts" 2>/dev/null)
    case "$_ig_p" in 0) ;; *) echo "יש מחיצה מחוברת בדיסק"; return ;; esac
    for _ig_x in "$SYSROOT/sys/block/$1/$1"*; do
        [ -d "$_ig_x" ] || continue
        _ig_n="$DEVROOT/${_ig_x##*/}"
        _ig_b=$(bitlocker_state 0 "" "$_ig_n")
        [ "$_ig_b" = clear ] || { echo "${_ig_x##*/}: BitLocker ($_ig_b)"; return; }
        case "$(blkid -o value -s TYPE "$_ig_n" 2>/dev/null)" in
            ntfs) command -v ntfs-3g.probe > /dev/null 2>&1 || { echo "${_ig_x##*/}: אי אפשר לבדוק שינה (ntfs-3g.probe חסר)"; return; }
                  ntfs-3g.probe --readwrite "$_ig_n" > /dev/null 2>&1 < /dev/null; _ig_rc=$?
                  [ "$_ig_rc" = 0 ] || { echo "${_ig_x##*/}: חלונות במצב שינה או לא נכבה נקי (rc=$_ig_rc)"; return; } ;;
        esac
    done
    case "$(smart_probe "$1")" in   # #3271 M2: never on the disk a rescue is for. Cached (smart_probe_idle reads the same)
        ok|warn) ;; fail) echo "SMART אדום — לא נבדק, ולא נכתב"; return ;; *) echo "SMART לא נקרא — לא נבדק, ולא נכתב"; return ;;
    esac
    [ -f "$RUN_DIR/disk_failures" ] || disk_failures_refresh || { echo "רשימת תקלות הדיסקים מהשרת לא נקראה — לא נבדק"; return; }
    if disk_failure_cause "$1" > /dev/null; then echo "לדיסק (או לחריץ) יש תקלה פתוחה בשרת — לא נבדק, ולא נכתב"; fi
}

_ic_wait() { # $1 = pid, $2 = node, $3 = the ceiling in seconds (default WAIT_WRITECHECK_S; the sustained part's is longer). 0 = it ended; 1 = the ceiling: killed, and /proc shows no dd writing the node; 2 = a dd may still write it
    _iw_t=0
    while kill -0 "$1" 2>/dev/null; do
        if [ "$_iw_t" -ge $((${3:-$WAIT_WRITECHECK_S} * 10)) ]; then
            kill -9 "$1" 2>/dev/null; wait "$1" 2>/dev/null   # the subshell first: it starts no dd after this
            _ic_stop_writers "$2" || return 2   # #3271 H4: its dd lives on (reparented, or in D state)
            sleep 0.5; _ic_stop_writers "$2" && return 1; return 2   # #3299: a dd killed between fork and exec still reads as sh -- the second look finds it
        fi
        sleep 0.1; _iw_t=$((_iw_t + 1))
    done
    wait "$1" 2>/dev/null; return 0   # wait-bounded: after-kill-0-gone -- the loop above has its ceiling (WAIT_WRITECHECK_S, then H4); this collects the exit code
}

idle_wc_disk() { # $1 = disk. The lock first, the hold after: a task that set the hold earlier is seen here, one that sets it later waits for this lock (idlegate.sh)
    mkdir -p "$RUN_DIR/idlecheck"; mkdir "$RUN_DIR/idlecheck/busy.$1" 2>/dev/null || return 0   # held already (a task's recovery): not now
    [ ! -e "$RUN_DIR/idlecheck/hold" ] || { rm -rf "$RUN_DIR/idlecheck/busy.$1"; return 0; }
    _id_k=$(_ic_ident "$1") || _id_k=""   # #3271 L3: under the lock
    command exec 9< "$DEVROOT/$1" || { _ic_res "$1" "$_id_k" unchecked "הדיסק לא נפתח להחזקה — לא נבדק, ולא נכתב דבר" ""; rm -rf "$RUN_DIR/idlecheck/busy.$1"; return 0; }   # #3297: an fd on the node for the whole check and put-back -- the kernel does not hand its sdX to a disk that comes in meanwhile. `command`: a failed exec must not end this shell, it holds the busy lock
    _id_run "$1" "$DEVROOT/$1" "$_id_k"; exec 9<&-; rm -rf "$RUN_DIR/idlecheck/busy.$1"   # one close for every return of _id_run (it has many); the subshell's exit would close it too
}

_id_run() { # $1 = disk, $2 = node, $3 = key
    [ -n "$3" ] || { _ic_res "$1" "" unchecked "אין מספר סידורי לדיסק — אי אפשר לשמור עותק, לא נבדק" ""; return; }
    _id_g=$(_ic_guard "$1"); [ -z "$_id_g" ] || { _ic_res "$1" "$3" unchecked "$_id_g" ""; return; }
    _ic_kept=0; idle_wc_recover "$1" || { _ic_res "$1" "$3" unchecked "עותק ישן של הדיסק לא הוחזר — לא נבדק, ולא נכתב דבר" ""; return; }
    [ "$_ic_kept" = 0 ] || { _ic_res "$1" "$3" unchecked "לסידורי הזה יש עותק פתוח בשרת שלא שוחרר (התיישן, או של דיסק תאום) — לא נבדק" ""; return; }
    _wc_mib=$(blockdev --getsize64 "$2" 2>> "$LOG_FILE"); case "$_wc_mib" in ''|*[!0-9]*) _ic_res "$1" "$3" unchecked "גודל הדיסק לא נקרא" ""; return ;; esac
    _id_b=$_wc_mib; _wc_mib=$((_wc_mib / 1048576))
    [ "$_wc_mib" -ge $((WRITECHECK_MIB * 3)) ] || { _ic_res "$1" "$3" unchecked "הדיסק קטן מ-$((WRITECHECK_MIB * 3))MiB" ""; return; }
    _id_w="$RUN_DIR/idlecheck/work.$1"; rm -rf "$_id_w"; mkdir -p "$_id_w"
    _id_g=$(_ic_regions "$_wc_mib")
    _wb_w=$_id_w; _wb_b=$((WRITECHECK_MIB * 1048576))   # the patterns and markers are made here, before the copy leaves: they go with it (_ic_body)
    _wc_pats 0 0 0 > "$_id_w/result" || { _ic_res "$1" "$3" unchecked "לא נוצר דפוס לבדיקה (urandom)" ""; rm -rf "$_id_w"; return; }
    _ic_marks "$_id_w" $_id_g || { _ic_res "$1" "$3" unchecked "לא נוצרו סמנים לבדיקה (urandom) — לא נכתב" ""; rm -rf "$_id_w"; return; }
    _ic_save "$2" "$_id_w" $_id_g || { _ic_res "$1" "$3" unchecked "קריאת האזורים לגיבוי נכשלה — לא נכתב" ""; rm -rf "$_id_w"; return; }
    _ic_body "$_id_w" $_id_g || { _ic_res "$1" "$3" unchecked "העותק לשרת לא נבנה — לא נכתב" ""; rm -rf "$_id_w"; return; }
    _id_s=$(sha256sum "$_id_w/blob" 2>/dev/null | cut -d' ' -f1); _id_u=$(sha256sum "$_id_w/body" 2>/dev/null | cut -d' ' -f1)
    _id_ser=${3%|*}
    curl -sfS --max-time "$IDLE_WC_UPLOAD_S" -X POST -H 'Content-Type: application/octet-stream' --data-binary "@$_id_w/body" \
        "$(_ic_url "" "$_id_ser")&size=$_id_b&regions=$(echo $_id_g | tr ' ' ',')&sha256=$_id_u" > "$_id_w/up.json" 2>> "$LOG_FILE" \
        && jq -e --arg s "$_id_u" '.ok == true and .sha256 == $s' "$_id_w/up.json" > /dev/null 2>&1 \
        || { _ic_res "$1" "$3" unchecked "השרת לא אישר שמירת עותק — לא נכתב" ""; rm -rf "$_id_w"; return; }
    _id_id=$(jq -r '.id' "$_id_w/up.json" 2>/dev/null)
    _ic_same "$1" "$3" || { _ic_res "$1" "$3" unchecked "הדיסק הוחלף בזמן שליחת העותק — לא נכתב עליו" ""; rm -rf "$_id_w"; return; }   # #3271 H1: the copy stays, of a disk that was not touched
    rm -f "$RUN_DIR/idlecheck/settled.$1"   # from the first write on the disk may carry the check: clean again only after a verified put-back
    _ic_check "$1" "$2" "$_id_w" || {   # the writes -- the copy is on the server
        _ic_res "$1" "$3" failed "$_ic_why — לא שוחזר עכשיו; העותק נשמר בשרת ויוחזר כשלא יהיה dd חי" ""; rm -rf "$_id_w"; return; }
    _ic_putback "$2" "$_id_w" "$_id_s" "$1" "$3" $_id_g; _id_rc=$?
    [ "$_id_rc" != 2 ] || { _ic_res "$1" "$3" failed "הדיסק הוחלף באמצע הבדיקה — לא נכתב על החדש; העותק של הקודם נשמר בשרת" ""; rm -rf "$_id_w"; return; }
    [ "$_id_rc" = 0 ] || { _ic_res "$1" "$3" failed "השחזור של תוכן הדיסק נכשל — העותק נשמר בשרת ויוחזר בעלייה הבאה" ""; rm -rf "$_id_w"; return; }
    printf '%s' "$3" > "$RUN_DIR/idlecheck/settled.$1"   # back byte for byte: clean of the check (idlegate.sh)
    _ic_release "$_id_id" "$_id_ser" || { _ic_res "$1" "$3" unchecked "הדיסק שוחזר, אבל העותק בשרת לא נמחק" ""; rm -rf "$_id_w"; return; }
    rm -rf "$_id_w"
    [ "$_ic_st" != ok ] || idle_long "$1" "$2" "$3" "$_id_b"   # #3268 (18/11): the sustained part, with a copy of its own; sets _ic_st/_ic_why/_ic_mb
    _ic_res "$1" "$3" "$_ic_st" "$_ic_why" "$_ic_mb" "${_ic_m1:-}"
}

_ic_check() { # $1 = disk, $2 = node, $3 = work dir. Sets _ic_st/_ic_why/_ic_mb (the check's own words). 1 = a dd of it may still write the disk: no put-back now
    _wb_d=$1; _wb_n=$2; _wb_w=$3; _wb_b=$((WRITECHECK_MIB * 1048576)); rm -f "$3/mbps" "$3/result"; _ic_m1=""   # pat.N and mk.N stay: _id_run made them before the copy
    ( _wc_io 0 $((_wc_mib / 2)) $((_wc_mib - WRITECHECK_MIB)) > "$3/result" ) &
    _ic_wait "$!" "$2"; _ic_r=$?
    if [ "$_ic_r" != 0 ]; then
        _ic_st=failed; _ic_why="לא סיים לכתוב ולקרוא בחזרה תוך $WAIT_WRITECHECK_S שניות"; _ic_mb=""
        [ "$_ic_r" = 1 ] || { _ic_why="$_ic_why, ו-dd שלו עוד כותב על הדיסק"; return 1; }
        return 0
    fi
    _ic_r=$(head -n 1 "$3/result" 2>/dev/null)
    case "$_ic_r" in ok\|*|failed\|*|unchecked\|*) _ic_st=${_ic_r%%|*}; _ic_why=${_ic_r#*|} ;; *) _ic_st=unchecked; _ic_why="הבדיקה לא החזירה תוצאה" ;; esac
    _ic_mb=""; [ "$_ic_st" = ok ] && { _ic_mb=$(cat "$3/mbps" 2>/dev/null); case "$_ic_mb" in ''|*[!0-9]*) _ic_mb="" ;; esac; }
    [ "$_ic_st" != failed ] || _ic_why="הדיסק לא מקבל כתיבה — $_ic_why"
}

idle_wc_scan() { # one pass: every drawer with no result for this insertion is checked; a disk that went out loses its result
    [ ! -e "$RUN_DIR/idlecheck/hold" ] || return 0   # a task or a round holds the drawers (idlegate.sh); #3271 M3: not the targets/<dev> a finished round leaves for the screen
    idle_ceilings_refresh   # #3672 (A15): the checks started below inherit the brain's ceilings
    mkdir -p "$RUN_DIR/idlecheck"; _is_here=" "
    for _is_d in $(list_drawers); do _is_here="$_is_here$_is_d "; done   # #3271 M1: no USB, SD or eMMC (#1832)
    for _is_x in "$RUN_DIR"/idlecheck/res.* "$RUN_DIR"/idlecheck/settled.*; do
        [ -e "$_is_x" ] || continue; _is_d=${_is_x##*/res.}; _is_d=${_is_d##*/settled.}
        case "$_is_here" in *" $_is_d "*) [ ! -d "$_is_x" ] || [ "$(cat "$_is_x/key" 2>/dev/null)" = "$(_ic_ident "$_is_d")" ] || rm -rf "$_is_x" ;; *) rm -rf "$_is_x" ;; esac
    done
    [ -f "$RUN_DIR/disk_failures" ] || disk_failures_refresh > /dev/null 2>&1 || :   # once, before the checks run side by side (they share its file)
    for _is_d in $_is_here; do   # #3268 (18/11): side by side -- the time is the slowest disk's, not the sum. idle_wc_disk takes its own lock
        [ ! -e "$RUN_DIR/idlecheck/hold" ] || return 0
        [ -d "$RUN_DIR/idlecheck/res.$_is_d" ] || [ -d "$RUN_DIR/idlecheck/busy.$_is_d" ] || ( idle_wc_disk "$_is_d" ) &
    done
}

idle_wc_start() { # re-entrant, like disk_uevent_start; cloners only
    [ "$D_ROLE" = cloner ] || return 0
    [ -n "${_idle_wc_pid:-}" ] && kill -0 "$_idle_wc_pid" 2>/dev/null && return 0
    ( while :; do idle_wc_scan || :; sleep 1; done ) &
    _idle_wc_pid=$!
}

idle_wc_adopt() { # $1 = a disk of the round. 0 = its waiting-screen result is taken, 1 = there is none (the round writes the check), 2 = failed by name
    _ia_t=0; idle_ceilings_refresh   # #3672 (A15)
    while [ -e "$RUN_DIR/idlecheck/busy.$1" ]; do   # the check on it is still running: the round does not write over its copy
        [ "$_ia_t" -lt "$IDLE_WC_BUSY_WAIT_S" ] || { target_set "$1" failed "בדיקת הכתיבה מהמסך הממתין עוד רצה על הדיסק — לא נכתב"; return 2; }
        sleep 1; _ia_t=$((_ia_t + 1))
    done
    _ia_w=$(idle_wc_ready "$1" dst) || { target_set "$1" failed "$_ia_w"; return 2; }   # #3298: no dd of the check may still write it
    _ia_d="$RUN_DIR/idlecheck/res.$1"
    [ "$(cat "$_ia_d/state" 2>/dev/null)" = ok ] && [ "$(cat "$_ia_d/key" 2>/dev/null)" = "$(_ic_ident "$1")" ] || return 1
    mkdir -p "$RUN_DIR/targets/$1"; rm -f "$RUN_DIR/targets/$1/write_check_mbps"
    printf '%s' "$(cat "$_ia_d/reason")" > "$RUN_DIR/targets/$1/write_check_reason"
    case "$(cat "$_ia_d/mbps" 2>/dev/null)" in ''|*[!0-9]*) ;; *) cp "$_ia_d/mbps" "$RUN_DIR/targets/$1/write_check_mbps" ;; esac
    printf 'ok' > "$RUN_DIR/targets/$1/write_check_state"   # last
    log "$1: write check: taken from the waiting screen (this insertion) -- not written again"
}

_ic_floor() { # the console's floor from the hello answer (#2338), else WRITECHECK_MIN_MBPS: never 0, never none
    _if_v=$(jq -c '.writecheck_min_mbps' "$RUN_DIR/response.json" 2>/dev/null)
    case "$_if_v" in [1-9][0-9]|[1-9][0-9][0-9]) [ "$_if_v" -le 500 ] && { printf '%s' "$_if_v"; return; } ;; esac
    printf '%s' "$WRITECHECK_MIN_MBPS"
}

idle_wc_field() { # $1 = disk. "state|text" -- the check's own word (_ic_field_now), unless the brain decided on exactly this result (idlebrain.sh, B62): RED = slow|its reason, INFO = history|a neutral line. No decision is not "ok": the check's word stands, and a disk it says slow is red without one
    _iq_n=$(_ic_field_now "$1")
    command -v idle_wc_decision > /dev/null 2>&1 && idle_wc_decision "$1" "$_iq_n" && return
    printf '%s' "$_iq_n"
}

_ic_field_now() { # $1 = disk. "state|text" -- cdisk fields 18-19: checking | ok | slow | failed | unchecked. Never ok without a result (idle_wc_field, above, takes the brain's decision on it)
    [ ! -e "$RUN_DIR/idlecheck/busy.$1" ] || { printf 'checking|'; return; }
    _if_s=$(cat "$RUN_DIR/idlecheck/res.$1/state" 2>/dev/null)
    case "$_if_s" in
        ok)
            _if_all=""
            for _if_d in $(list_disks); do
                [ "$(cat "$RUN_DIR/idlecheck/res.$_if_d/state" 2>/dev/null)" = ok ] || continue
                _if_m=$(cat "$RUN_DIR/idlecheck/res.$_if_d/mbps" 2>/dev/null)
                case "$_if_m" in ''|*[!0-9]*) continue ;; esac
                _if_all="$_if_all$_if_d $_if_m $(_wc_kind "$_if_d")
"
            done
            writecheck_hdd_policy "$RUN_DIR/response.json"
            _if_w=$(_wc_slow_why "$1" "$_if_all" "$(_ic_floor)")
            if [ -n "$_if_w" ]; then printf 'slow|%s' "$_if_w"; else printf 'ok|'; fi ;;
        failed|unchecked) printf '%s|%s' "$_if_s" "$(tr '\r\n|' '   ' < "$RUN_DIR/idlecheck/res.$1/reason")" ;;
        *) if [ -e "$RUN_DIR/idlecheck/hold" ]; then printf 'unchecked|לא נבדק — המגירות בשימוש (סבב או העתקה)'; else printf 'checking|'; fi ;;   # #3271 M3: never "checking" while nothing runs
    esac
}
