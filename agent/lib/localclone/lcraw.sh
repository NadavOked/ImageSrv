# lcraw.sh -- #2017 (Nadav 22/10, "never worse than dd"): what a local clone
# cannot copy smartly it copies raw, and says so. POSIX sh (busybox ash).
#
# Raw = partclone.dd: every byte of the partition, into a target partition of
# the same size or larger, and that partition is never grown. Three ways in:
#   1. a filesystem partclone does not know (unknown, BitLocker, LUKS, ReFS...):
#      the manifest says so before the copy (lc_raw_json) -- copy "raw";
#   2. partclone refused a filesystem it knows (an NTFS it cannot mount): the
#      partition is streamed again, raw (lc_stream_any);
#   3. the raw read itself failed -- bad sectors. The operator is asked (askgui.sh,
#      kind badsect) for a minute, and the copy goes on (Nadav 22/10): "continue",
#      no answer in LC_RESCUE_ASK_S, or no GUI to ask copy it with rescuecat
#      (#2527; was busybox dd conv=noerror,sync): the sectors not read are zeros
#      on the targets, which are then NOT identical to the source and say so in
#      orange. rescuecat leaves a MAP of the unread ranges (rescue.map, kept per
#      target and in the log), and after the stream the unread sectors are asked
#      again (lc_rescue_retry, bounded in rounds and seconds): one that reads is
#      written into the targets and leaves the map. A disk that stops answering
#      ends the copy -- a short stream, never zeros to the end.
#      Only an explicit "stop" fails the copy. The source is never written.
# An MSR and a BIOS boot partition have no filesystem by design: raw, no note.
# A disk with an MBR table is still refused (lcinv.sh): MBR is v1.1.

LC_RESCUE=0
LC_RESCUE_ASK_S="${LC_RESCUE_ASK_S:-60}"   # Nadav 22/10: a minute, then on by itself

lc_raw_why() {
    # $1 = fs (_fs_of), $2 = role, $3 = type GUID. stdout = why the partition is
    # copied raw, in Hebrew; nothing when partclone copies it or it is raw by design.
    [ "$1" = swap ] && return 0
    [ "$(partclone_for_fs "$1")" = partclone.dd ] || return 0
    [ "$2" = msr ] && return 0
    case "$(printf '%s' "$3" | tr 'a-f' 'A-F')" in 21686148-6449-6E6F-744E-656564454649) return 0 ;; esac   # BIOS boot
    case "$1" in
        BitLocker) echo "${CAPTURE_BITLOCKER_NOTE:-מוצפנת ב-BitLocker}" ;;
        crypto_LUKS) echo "מוצפנת ב-LUKS" ;;
        unknown|'') echo "מערכת הקבצים לא מזוהה" ;;
        *) echo "מערכת הקבצים $1 אינה נתמכת בהעתקה חכמה" ;;
    esac
}

lc_raw_json() {
    # $1 = fs, $2 = role, $3 = type GUID. The manifest's extra fields, or nothing.
    _rj_w=$(lc_raw_why "$1" "$2" "$3")
    [ -z "$_rj_w" ] || printf ',"copy":"raw","raw_why":"%s"' "$(json_escape "$_rj_w")"
}

lc_raw_fix() {
    # $1 = manifest. A raw partition is never the one that grows: when it was the
    # candidate, nothing grows -- the target keeps the source's layout, like dd.
    jq '.partitions |= map(if .copy == "raw" then .expandable = false else . end)' "$1" > "$1.raw" 2>> "$LOG_FILE" \
        && mv "$1.raw" "$1"
}

lc_raw_mark() {
    # $1 = manifest, $2 = idx, $3 = why. Case 2: from now on the partition is raw.
    jq --argjson i "$2" --arg w "$3" \
        '.partitions |= map(if .index == $i then .copy = "raw" | .raw_why = $w | .expandable = false else . end)' \
        "$1" > "$1.raw" 2>> "$LOG_FILE" && mv "$1.raw" "$1"
}

lc_rescue_ask() {
    # $1 = source, $2 = idx. stdout = the operator's answer (continue|stop), or nothing.
    # The row carries the epoch the GUI counts down to (screens_ask.c).
    _ra_p=$(disk_port "$1"); case "$_ra_p" in ''|*[!0-9]*) _ra_p=0 ;; esac
    printf '%s|%s|%s\n' "$_ra_p" "$2" "$(($(date +%s) + LC_RESCUE_ASK_S))" > "$LC/run/badsect.rows" || return 1
    log "local clone: partition $2 of $1 did not read, raw either -- the operator is asked (#2017)" >&2   # stdout is the answer
    gui_ask_choice badsect "$1" "$LC/run/badsect.rows" "$LC_RESCUE_ASK_S" 2>> "$LOG_FILE"
}

lc_rescue_count() {
    # $1 = rescuecat's map. stdout = the sectors (512 B) not read. 1 = no evidence: no map,
    # or one without its "# complete" line (an aborted read, a run that never ended).
    grep -qx '# complete' "$1" 2>/dev/null || return 1
    awk '!/^#/ && NF >= 3 { n += $2 } END { printf "%.0f\n", n / 512 }' "$1"
}

lc_rescue_retry() {
    # $1 = idx, $2 = source, $3.. = targets. #2527: the unread sectors asked again, LC_RESCUE_ROUNDS
    # rounds over all of them and LC_RESCUE_RETRY_S seconds at most; a sector that reads is written into
    # every target that took the partition, and leaves the map. Best effort: when it does not finish the
    # map stays as it was -- more unread than there is, never fewer.
    _rr_i=$1; _rr_s=$(lc_src_node "$2" "$1"); shift 2; _rr_a=""; _rr_o="$LC/run/retry.out"
    for _rr_t in "$@"; do
        grep -qx "$_rr_i" "$RUN_DIR/targets/$_rr_t/lc_ok" 2>/dev/null && _rr_a="$_rr_a -t $(partition_node "$_rr_t" "$_rr_i")"
    done
    [ -n "$_rr_a" ] || return 0
    : > "$_rr_o"
    # shellcheck disable=SC2086 # the target list is a word list on purpose
    rescuecat retry -s "$_rr_s" -b "$LC_RESCUE_BS" -m "$LC/run/rescue.map" -n "${LC_RESCUE_ROUNDS:-3}" \
        -T "${LC_RESCUE_RETRY_S:-600}" -P "$LC/run/retry.round" $_rr_a > "$_rr_o" 2>> "$LOG_FILE" &   # -P: the screen's "סבב X מתוך N"
    _rr_p=$!
    while kill -0 "$_rr_p" 2>/dev/null; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        _nap "$_rr_p"
        [ ! -f "$LC/stop.req" ] || kill "$_rr_p" 2>/dev/null
    done
    wait "$_rr_p"; _rr_rc=$?; rm -f "$LC/run/retry.round"   # wait-bounded: after-kill-0-gone -- the loop above left once it was gone (rescuecat has its own -T ceiling); this collects its exit code
    [ "$_rr_rc" = 0 ] || log "local clone: partition $_rr_i: the retry of the unread sectors ended rc=$_rr_rc -- the map is as it was"
    for _rr_t in "$@"; do
        _rr_e=$(awk -v n="$(partition_node "$_rr_t" "$_rr_i")" '$1 == "failed" && $2 == n { print $3; exit }' "$_rr_o")
        [ -z "$_rr_e" ] || fail_written_target "$_rr_t" "מחיצה $_rr_i: כתיבת סקטור ששוחזר נכשלה (errno $_rr_e)"
    done
}

lc_rescue_keep() {
    # $1 = idx, $2.. = targets. #2527: what was not read, by address: the map beside each target that took the
    # partition, and the first ranges in the log (the screen keeps its count -- 400 bytes of note, lc_raw_note).
    _rk_i=$1; shift
    for _rk_t in "$@"; do
        grep -qx "$_rk_i" "$RUN_DIR/targets/$_rk_t/lc_ok" 2>/dev/null \
            && cp "$LC/run/rescue.map" "$RUN_DIR/targets/$_rk_t/rescue.map.$_rk_i" 2>> "$LOG_FILE"
    done
    awk '!/^#/ && NF >= 3 && ++n <= 10 { printf "unread: %s bytes at byte %s (errno %s)\n", $2, $1, $3 }' "$LC/run/rescue.map" \
        | while IFS= read -r _rk_l; do log "local clone: partition $_rk_i: $_rk_l"; done
}

lc_stream_any() {
    # $1 = idx, $2 = fs, $3 = source, $4.. = targets. lc_stream, and what #2017
    # does when the source was not read: lc_stream's 3 (partclone refused) and 4
    # (the raw read failed) end here. 0/1/2 as lc_stream.
    _sa_i=$1; _sa_fs=$2; _sa_src=$3; shift 3; _sa_m="$LC/run/manifest.json"
    lc_stream "$_sa_i" "$_sa_fs" "$_sa_src" "$@"; _sa_rc=$?
    if [ "$_sa_rc" = 3 ]; then
        _sa_why="partclone סירב למערכת הקבצים ($_sa_fs, rc=$LC_SRC_RC)"
        log "local clone: partition $_sa_i: partclone refused $_sa_fs (rc=$LC_SRC_RC) -- streamed again, raw (#2017)"
        lc_raw_mark "$_sa_m" "$_sa_i" "$_sa_why" \
            || { _fail_alive "$*" "מחיצה $_sa_i: $_sa_why, וההעתקה הגולמית לא נרשמה — לא הועתקה"; return 1; }
        lc_stream "$_sa_i" raw "$_sa_src" "$@"; _sa_rc=$?
    fi
    [ "$_sa_rc" = 4 ] || return "$_sa_rc"
    _sa_dd=$LC_SRC_RC
    _sa_a=$(lc_rescue_ask "$_sa_src" "$_sa_i")
    if [ "$_sa_a" = stop ]; then
        log "local clone: partition $_sa_i: bad sectors -- the operator stopped the copy"
        _fail_alive "$*" "מחיצה $_sa_i: קריאת המקור נכשלה גם בהעתקה גולמית (rc=$_sa_dd) — כנראה סקטורים פגומים. המפעיל בחר לעצור; המקור לא שונה"
        echo "$_sa_i|failed" > "$LC/run/cur"; source_failed_beep; return 1   # #2067: the operator stopped at bad sectors -- heard now
    fi
    [ "$_sa_a" = continue ] || log "local clone: partition $_sa_i: no answer in ${LC_RESCUE_ASK_S}s, or no GUI -- continued by itself (Nadav 22/10)"
    _sa_z=$(jq -r --argjson i "$_sa_i" '.partitions[] | select(.index == $i) | .size_bytes' "$_sa_m" 2>/dev/null)
    case "$_sa_z" in ''|*[!0-9]*) _fail_alive "$*" "מחיצה $_sa_i: גודל המחיצה לא נקרא — ההמשך לא בוצע"; return 1 ;; esac
    LC_RESCUE_BS=512; [ $((_sa_z % 4096)) -ne 0 ] || LC_RESCUE_BS=4096
    lc_raw_mark "$_sa_m" "$_sa_i" "rescue" || { _fail_alive "$*" "מחיצה $_sa_i: מצב ההצלה לא נרשם — ההמשך לא בוצע"; return 1; }
    log "local clone: partition $_sa_i: continued (${_sa_a:-no answer}) -- rescuecat bs=$LC_RESCUE_BS (#2017, #2527)"
    LC_RESCUE=1; lc_stream "$_sa_i" raw "$_sa_src" "$@"; _sa_rc=$?; LC_RESCUE=0
    [ "$_sa_rc" = 0 ] || return "$_sa_rc"
    _sa_n=$(lc_rescue_count "$LC/run/rescue.map") \
        || { _fail_alive "$*" "מחיצה $_sa_i: לא נספר כמה סקטורים לא נקראו — אין ראיה למה שנכתב"; return 1; }
    _sa_b=$_sa_n
    if [ "$_sa_n" -gt 0 ]; then   # #2527: asked again, now that every readable sector is in
        lc_rescue_retry "$_sa_i" "$_sa_src" "$@"
        _sa_n=$(lc_rescue_count "$LC/run/rescue.map") \
            || { _fail_alive "$*" "מחיצה $_sa_i: מפת הסקטורים שלא נקראו אבדה אחרי הניסיון החוזר — אין ראיה למה שנכתב"; return 1; }
    fi
    lc_rescue_keep "$_sa_i" "$@"
    echo "$_sa_i|$_sa_n" >> "$LC/run/rescued"
    log "local clone: partition $_sa_i: $_sa_n sector(s) not read, $((_sa_b - _sa_n)) read by the retry -- zeros on the targets (#2527)"
    for _sa_t in "$@"; do
        grep -qx "$_sa_i" "$RUN_DIR/targets/$_sa_t/lc_ok" 2>/dev/null && echo "$_sa_i|$_sa_n" >> "$RUN_DIR/targets/$_sa_t/rescued"
    done
    return 0
}

lc_raw_nogrow() {
    # $1 = target, $2 = manifest. After the table check, before finish_grow: a raw
    # partition the table already grew (case 2/3 came after the table was written)
    # keeps its filesystem at the source's size -- the grow is dropped.
    _rn_i=$(cut -d'|' -f1 "$RUN_DIR/targets/$1/expanded" 2>/dev/null)
    case "$_rn_i" in ''|*[!0-9]*) return 0 ;; esac
    jq -e --argjson i "$_rn_i" 'any(.partitions[]; .index == $i and .copy == "raw")' "$2" > /dev/null 2>&1 || return 0
    rm -f "$RUN_DIR/targets/$1/expanded" "$RUN_DIR/targets/$1/expanded.more"
    log "$1: partition $_rn_i was copied raw -- its filesystem is not grown (#2017)"
}

lc_raw_note() {
    # $1 = target, $2 = manifest. stdout = the orange line of a done target, or nothing.
    _no_r=$(jq -r '[.partitions[] | select(.copy == "raw" and .raw_why != "rescue") | "מחיצה \(.index) — \(.raw_why)"] | join("; ")' "$2" 2>/dev/null)
    _no_s=""; _no_f="$RUN_DIR/targets/$1/rescued"
    [ ! -s "$_no_f" ] || while IFS='|' read -r _no_i _no_n; do
        _no_s="$_no_s${_no_s:+; }מחיצה $_no_i במצב הצלה: $_no_n סקטורים לא נקראו ונכתבו כאפסים"
    done < "$_no_f"
    [ -z "$_no_r" ] || printf 'הועתק במצב גולמי (כמו dd): %s' "$_no_r"
    [ -z "$_no_s" ] || printf '%s%s — לא זהה למקור' "${_no_r:+ · }" "$_no_s"
}

lc_raw_flags() {
    # $1 = target, $2 = manifest. lc_tgt's field 10: raw partitions (with a note),
    # sectors not read (-1 = none rescued). The screen's header says it (lcrun).
    _rf_n=$(jq '[.partitions[] | select(.copy == "raw")] | length' "$2" 2>/dev/null)
    _rf_s=-1; [ ! -s "$RUN_DIR/targets/$1/rescued" ] || _rf_s=$(awk -F'|' '{ n += $2 } END { print n + 0 }' "$RUN_DIR/targets/$1/rescued")
    printf '%s,%s' "${_rf_n:-0}" "${_rf_s:--1}"
}

lc_smart_unread() {
    # $1 = disk. Nadav 22/10: what SMART already says the source could not read, said
    # before the copy (lcgui.sh lc_unread=). The larger of 197 (pending) and 198 / NVMe
    # media errors -- smart.sh's record, fields 4/5, kept by the inventory's smart_probe:
    # no new read. 197 and 198 mostly count the same sectors, so never their sum.
    # Nothing when SMART was not read (never "0") or both are 0. Not a question.
    smart_was_read "$1" || return 0
    _su_p=$(smart_field "$1" 4); _su_u=$(smart_field "$1" 5)
    case "$_su_p" in ''|*[!0-9]*) return 0 ;; esac
    case "$_su_u" in ''|*[!0-9]*) return 0 ;; esac
    [ "$_su_u" -le "$_su_p" ] || _su_p=$_su_u
    [ "$_su_p" -eq 0 ] || echo "$_su_p"
}
