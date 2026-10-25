# drawergate.sh -- what a cloner drawer settles with the operator before the
# first byte, after the SMART gate (smartgate.sh) and before the round's write
# (run_restore_drawers). POSIX sh (busybox ash). Both questions go through the
# GUI's question dialog (askgui.sh) on the cloner screen, like SMART; the
# drawer is orange ("asking") while it waits, and NO answer is NO write.
#
#   #1808 (Nadav 22/10): a drawer that still carries an old RAID set's
#   metadata is not refused outright any more: the operator is told what was
#   found, and an explicit "write" lets the write wipe it (raid_wipe, before
#   the write check). A capture from a RAID member stays refused (capturegate.sh).
#
#   #1898 (Nadav 22/10): a shrunk partition BEFORE the one that grows (C:
#   before the linux root) takes its share of the room -- the cloner shows the
#   split it will write (share_plan) and asks first.

drawer_gates() {
    # $1.. = the drawers the SMART gate left. PRINTS the ones to write (log on
    # stderr: this runs inside `$( )`). EXPAND_OVERRIDE as for the restore.
    _dg_raid=$(drawer_raid_gate "$@")
    # shellcheck disable=SC2086 # a word list on purpose
    [ -z "$_dg_raid" ] || drawer_share_gate "$RUN_DIR/manifest.json" $_dg_raid
}

_drawer_ask() {
    # $1 = disk, $2 = kind, $3 = rows file, $4 = what the drawer says (orange).
    # PRINTS the operator's answer, or nothing.
    target_set "$1" asking "$4"
    attended "$(smart_disk_label "$1" דיסק): $4" gui_ask_choice "$2" "$1" "$3" "${WAIT_SMART_GUI_S:-1800}" 2>> "$LOG_FILE"
}

_drawer_back() {
    # $1 = disk: the question is over, the drawer waits for the stream again.
    rm -f "$RUN_DIR/targets/$1/error"; target_set "$1" waiting
}

drawer_raid_gate() {
    # $1.. = drawers. A drawer with metadata of a RAID set is asked (raid);
    # "wipe" marks it targets/<d>/raid_wipe = the type, and disk_fits lets it
    # through. A probe that could not run is left to disk_fits, which refuses it
    # by name (principle 5) -- a question is only for what was found.
    for _rg_d in "$@"; do
        _rg_t=$(raid_member_probe "$_rg_d")
        case "$_rg_t" in none|\?*|'') echo "$_rg_d"; continue ;; esac
        case "$_rg_t" in *[!a-z_]*) echo "$_rg_d"; continue ;; esac   # not a word: disk_fits refuses it
        _rg_p=$(disk_port "$_rg_d"); case "$_rg_p" in ''|*[!0-9]*) _rg_p=0 ;; esac
        printf '%s|%s\n' "$_rg_p" "$_rg_t" > "$RUN_DIR/raid.rows" || { echo "$_rg_d"; continue; }
        _rg_why="מידע RAID ישן על הדיסק ($_rg_t)"   # short: the cloner row holds 160 bytes; the dialog says the rest
        _rg_a=$(_drawer_ask "$_rg_d" raid "$RUN_DIR/raid.rows" "$_rg_why — ממתין לאישור")
        if [ "$_rg_a" = wipe ]; then
            echo "$_rg_t" > "$RUN_DIR/targets/$_rg_d/raid_wipe" || { target_set "$_rg_d" failed "לא הצלחנו לרשום את האישור — לא נכתב"; continue; }
            log "$_rg_d: $_rg_t on the disk -- the operator chose to write and wipe it (#1808)" >&2
            _drawer_back "$_rg_d"; echo "$_rg_d"
        else
            target_set "$_rg_d" skipped "לא נכתב: $_rg_why, לא אושר"
            log "$_rg_d: $_rg_t on the disk -- not written (answer: ${_rg_a:-none})" >&2
        fi
    done
}

raid_wipe() {
    # $1 = disk the operator approved (targets/<d>/raid_wipe). Zeroes the first
    # and the last MiB -- md 1.1/1.2 at the start; IMSM, DDF and md 0.90/1.0 at
    # the end -- and asks again: only `none` afterwards is a wiped disk
    # (principle 5). The table is written after it (apply_gpt), as always.
    _rw_t=$(cat "$RUN_DIR/targets/$1/raid_wipe" 2>/dev/null)
    _rw_now=$(raid_member_probe "$1")
    [ -n "$_rw_t" ] && [ "$_rw_now" = "$_rw_t" ] || {
        target_set "$1" failed "מה שנמצא על הדיסק השתנה מאז האישור ($_rw_t → ${_rw_now:-לא ידוע}) — לא נכתב"; return 1; }
    _rw_n=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null)
    case "$_rw_n" in ''|*[!0-9]*) target_set "$1" failed "גודל הדיסק לא זמין — מידע ה-RAID לא נמחק, לא נכתב"; return 1 ;; esac
    _rw_c=2048; [ "$_rw_n" -ge "$_rw_c" ] || _rw_c=$_rw_n
    dd if=/dev/zero of="$DEVROOT/$1" bs=512 count="$_rw_c" conv=notrunc,fsync 2>> "$LOG_FILE" \
        && dd if=/dev/zero of="$DEVROOT/$1" bs=512 seek=$((_rw_n - _rw_c)) count="$_rw_c" conv=notrunc,fsync 2>> "$LOG_FILE" \
        || { target_set "$1" failed "מחיקת מידע ה-RAID נכשלה (dd) — לא נכתב"; return 1; }
    _rw_after=$(raid_member_probe "$1")
    [ "$_rw_after" = none ] || {
        target_set "$1" failed "מידע ה-RAID נשאר על הדיסק אחרי המחיקה (${_rw_after#\?}) — לא נכתב"; return 1; }
    log "$1: the $_rw_t metadata was wiped and the disk reads as none (#1808)"
}

share_plan() {
    # $1 = disk, $2 = manifest. #1898: what expand_last WILL give each partition
    # that shares the room on this disk -- the same steps (twins; a test
    # compares). PRINTS idx|now bytes|after bytes per partition that grows, the
    # candidate last; nothing when no partition BEFORE the candidate grows.
    _sp_line=$(_expand_candidate "$2"); [ -n "$_sp_line" ] || return 0
    _sp_start=$(echo "$_sp_line" | cut -d'|' -f5); _sp_size=$(echo "$_sp_line" | cut -d'|' -f6)
    _sp_tail=$(manifest_plan "$2" | awk -F'|' -v s="$_sp_start" '$5 + 0 > s + 0')
    _sp_tsec=$(printf '%s\n' "$_sp_tail" | awk -F'|' '{ n += int($6 / 512) } END { print n + 0 }')
    _sp_disk=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null)
    case "$_sp_disk" in ''|*[!0-9]*) return 1 ;; esac
    _sp_end=$((_sp_start + _sp_size / 512))
    [ $((_sp_disk - _sp_end - _sp_tsec)) -gt 2097152 ] || return 0
    _sp_add=$((_sp_disk - 34 - _sp_end - _sp_tsec))
    _sp_head=$(SHARE_MORE=/dev/null share_head "$1" "$2" "$_sp_line" "$_sp_add") || return 1
    _sp_tail2=$(SHARE_MORE=/dev/null SHARE_HEAD=1 share_tail "$1" "$2" "$_sp_line" "$_sp_tail" "$_sp_add") || return 1
    manifest_plan "$2" | SP_HEAD="$_sp_head" SP_TAIL="$_sp_tail2" awk -F'|' -v cand="$_sp_line" -v disk="$_sp_disk" '
        { size[$1] = $6 }
        END { nh = split(ENVIRON["SP_HEAD"], h, "\n"); split(h[1], s0, "|"); cstart = s0[1]
              for (i = 2; i <= nh; i++) { split(h[i], p, "|"); if (p[6] + 0 > size[p[1]] + 0) { rows[++r] = p[1] "|" size[p[1]] "|" p[6]; grew = 1 } }
              if (!grew) exit
              nt = split(ENVIRON["SP_TAIL"], t, "\n"); tb = 0
              for (i = 1; i <= nt; i++) { if (t[i] == "") continue; split(t[i], p, "|"); tb += p[6]
                  if (p[6] + 0 > size[p[1]] + 0) rows[++r] = p[1] "|" size[p[1]] "|" p[6] }
              split(cand, c, "|")
              for (i = 1; i <= r; i++) print rows[i]
              printf "%s|%s|%.0f\n", c[1], c[6], (disk - 34 - cstart) * 512 - tb }'
}

drawer_share_gate() {
    # $1 = manifest, $2.. = drawers. One question for every drawer the split
    # applies to: rows port|idx|now|after; "split" writes, anything else skips
    # them (in the open). A plan that did not compute is not a question: the
    # drawer fails by name -- never written on a split nobody saw.
    _sg_m=$1; shift
    : > "$RUN_DIR/share.rows" || { printf '%s\n' "$@"; return 0; }
    _sg_ask=""
    for _sg_d in "$@"; do
        if ! _sg_rows=$(share_plan "$_sg_d" "$_sg_m"); then
            target_set "$_sg_d" failed "לא הצלחנו לחשב את חלוקת המקום בין המחיצות — לא נכתב"; continue
        fi
        [ -n "$_sg_rows" ] || { echo "$_sg_d"; continue; }
        _sg_p=$(disk_port "$_sg_d"); case "$_sg_p" in ''|*[!0-9]*) _sg_p=0 ;; esac
        printf '%s\n' "$_sg_rows" | awk -v p="$_sg_p" 'NF { print p "|" $0 }' >> "$RUN_DIR/share.rows"
        _sg_ask="$_sg_ask $_sg_d"
    done
    [ -n "$_sg_ask" ] || return 0
    _sg_why="חלוקת המקום בין המחיצות ממתינה לאישור"
    for _sg_d in $_sg_ask; do target_set "$_sg_d" asking "$_sg_why"; done
    set -- $_sg_ask
    _sg_a=$(_drawer_ask "$1" share "$RUN_DIR/share.rows" "$_sg_why")
    for _sg_d in $_sg_ask; do
        if [ "$_sg_a" = split ] && : > "$RUN_DIR/targets/$_sg_d/share.ok"; then _drawer_back "$_sg_d"; echo "$_sg_d"   # expand_last reads it
        else target_set "$_sg_d" skipped "לא נכתב: חלוקת המקום בין המחיצות לא אושרה"; fi
    done
    log "share question (#1898):$_sg_ask -- ${_sg_a:-no answer}" >&2
}
