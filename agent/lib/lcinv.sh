# lcinv.sh -- #1322 local clone: what is in the drawers, and will a source
# fit a target. READ-ONLY: nothing in this file writes to a disk.
# POSIX sh (busybox ash).
#
# Three layers, each a file under $LC (the controller is localclone.sh):
#   inv/<dev>      one line per disk, lc_inventory -- what the screen shows
#                  before anything is chosen (port, model, size, SMART, what
#                  is on it, and how much room its layout needs);
#   measure/<dev>  the shrink measurement of one SOURCE, lc_measure -- only
#                  when a drawer is smaller than the source's layout
#                  (ntfsresize --info, the capture's shrinkplan.sh, #929);
#   lc_verdict     the pure rule that turns the two into one word per target.
#
# The verdicts (owner, 13/10): fits -- same size or larger, the last
# partition grows (#648); shrink -- smaller, but the data fits once the
# source is shrunk for the copy and grown back after (#87); short -- does not
# fit, by real data; measuring; unchecked -- the measurement itself failed.
# unchecked is NOT fits (principle 5): such a drawer cannot be a target.

LC="${LC:-$RUN_DIR/lc}"
LC_GPT_TAIL=262144   # #1171: the GPT backup the layout must leave room for
LC_RLM=$(printf '\342\200\217')   # U+200F: a Latin tool message after an amount stays its own run on screen

_lc_num() { case "$1" in ''|*[!0-9]*) echo null ;; *) echo "$1" ;; esac; }
_lc_clean() { printf '%s' "$1" | tr '\r\n|' '   '; }

lc_parts() {
    # $1 = disk, $2 = out file: idx|type_guid|unique_guid|first|size_sectors,
    # the same shape as capture.sh's parts.txt (shrinkplan.sh reads it). A
    # unique GUID that cannot be read is recorded in $2.fail (idx|rc|reason)
    # for unique_guid_gate. 1 = sgdisk could not list the table.
    _lp_node="$DEVROOT/$1"; : > "$2"; : > "$2.fail"
    _lp_p=$(sgdisk -p "$_lp_node" 2>> "$LOG_FILE") || return 1
    for _lp_i in $(printf '%s\n' "$_lp_p" | awk '/^ *[0-9]+ / { print $1 }'); do
        _lp_info=$(sgdisk -i "$_lp_i" "$_lp_node" 2>> "$LOG_FILE") || return 1
        _lp_t=$(printf '%s\n' "$_lp_info" | awk -F': ' '/Partition GUID code/ { print $2 }' | awk '{print $1}')
        _lp_f=$(printf '%s\n' "$_lp_info" | awk -F': ' '/^First sector/ { print $2 }' | awk '{print $1}')
        _lp_s=$(printf '%s\n' "$_lp_info" | awk -F': ' '/^Partition size/ { print $2 }' | awk '{print $1}')
        _lp_u=$(read_unique_guid "$_lp_node" "$_lp_i") || { echo "$_lp_i|$?|$_lp_u" >> "$2.fail"; _lp_u=""; }
        case "$_lp_f:$_lp_s" in *[!0-9:]*|:*|*:) return 1 ;; esac
        echo "$_lp_i|$_lp_t|$_lp_u|$_lp_f|$_lp_s" >> "$2"
    done
}

lc_layout_need() {
    # $1 = parts file, $2 = disk bytes, $3 = sector size. The bytes a target
    # must hold for this layout: the end of the last partition + the GPT
    # backup -- capture.sh's min_target_bytes, the twin of required_bytes
    # (diskfit.sh). A disk exactly the source's size always fits its own layout.
    awk -F'|' -v disk="$2" -v ss="$3" -v tail="$LC_GPT_TAIL" '
        { e = ($4 + $5) * ss; if (e > end) end = e }
        END { if (end <= 0) exit 1
              need = end + tail; if (disk >= end && disk < need) need = disk
              printf "%.0f\n", need }' "$1"
}

_lc_os_name() {
    # $1 = node, $2 = fs. PRETTY_NAME of a Linux root, read-only, or nothing.
    _lo_m="$RUN_DIR/lc-probe"; mkdir -p "$_lo_m"
    _lo_o=ro; case "$2" in ext2|ext3|ext4) _lo_o=ro,noload ;; esac
    mount -t "$2" -o "$_lo_o" "$1" "$_lo_m" 2>> "$LOG_FILE" || return 0
    sed -n 's/^PRETTY_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$_lo_m/etc/os-release" 2>/dev/null | head -n 1
    umount "$_lo_m" 2>> "$LOG_FILE"
}

lc_inv_disk() {
    # $1 = disk. One inventory line on stdout:
    # port|dev|model|serial|size|smart|smart_reason|os|nparts|used|need|usable|why
    # used = the bytes a copy of this disk would move (partclone's used blocks,
    # a whole partition where the filesystem is not known), -1 = not measured.
    # usable 1 = can be a source; why says what stops it.
    _li_d="$1"; _li_node="$DEVROOT/$1"
    _li_port=$(disk_port "$1"); case "$_li_port" in ''|*[!0-9]*) _li_port=0 ;; esac
    _li_model=$(_lc_clean "$(trim "$(cat "$SYSROOT/sys/block/$1/device/model" 2>/dev/null)")")
    _li_ser=$(_lc_clean "$(disk_serial "$1")")
    _li_sec=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null); case "$_li_sec" in ''|*[!0-9]*) _li_sec=0 ;; esac
    _li_size=$((_li_sec * 512))
    _li_smart=$(smart_probe "$1"); _li_sr=$(smart_field "$1" 2)
    [ "$_li_smart" != fail ] && _li_cause=$(disk_failure_cause "$1" 2>/dev/null) && { _li_smart=failed_last; _li_sr=$_li_cause; }   # #1580: red SMART wins
    _li_os=""; _li_n=0; _li_used=0; _li_need=-1; _li_ok=0; _li_why=""; _li_mark=""; _li_unread=""
    _li_ss=$(logical_block_size "$_li_node")
    _li_sch=$(disk_scheme "$_li_node")
    if [ "$_li_sch" != gpt ]; then
        case "$_li_sch" in none) _li_why="אין עליו טבלת מחיצות" ;; mbr) _li_why="טבלת MBR — שיכפול מקומי של MBR יגיע ב-v1.1" ;; *) _li_why="לא הצלחנו לקרוא את הדיסק" ;; esac
    elif [ "$_li_ss" != 512 ]; then
        _li_why="גודל סקטור ${_li_ss:-לא ידוע} — נתמך 512 בלבד"
    elif ! lc_parts "$1" "$LC/inv/$1.parts"; then
        _li_why="לא הצלחנו לקרוא את טבלת המחיצות (sgdisk)"
    else
        _li_n=$(awk 'END { print NR }' "$LC/inv/$1.parts")
        _li_need=$(lc_layout_need "$LC/inv/$1.parts" "$_li_size" 512) || _li_need=-1
        : > "$LC/inv/$1.used"   # idx|bytes a copy moves -- the progress denominator
        : > "$LC/inv/$1.roles"  # idx|role -- windows only with Windows\System32 on it (#1369)
        while IFS='|' read -r _li_i _li_t _li_u _li_f _li_z; do
            _li_pn=$(partition_node "$1" "$_li_i"); _li_fs=$(_fs_of "$_li_pn"); _li_role=$(_partition_role "$_li_t")
            [ "$_li_fs" != unreadable ] || _li_unread="$_li_unread $_li_i"   # #1905: blkid failed -- never copied raw as a source
            if [ "$_li_role" = windows ]; then   # #1354: the system partition is the capture's (#1369)
                _li_role=$(_system_role "$_li_i" "$_li_pn" "$_li_fs" 2>/dev/null) || _li_role=unchecked
            fi
            echo "$_li_i|$_li_role" >> "$LC/inv/$1.roles"
            [ "$_li_fs" = swap ] && { echo "$_li_i|0" >> "$LC/inv/$1.used"; continue; }
            case "$_li_role" in
                windows) [ -n "$_li_os" ] || _li_os=Windows ;;
                linux) [ -n "$_li_os" ] || _li_os=$(_lc_os_name "$_li_pn" "$_li_fs"); [ -n "$_li_os" ] || _li_os=Linux ;;
            esac
            case "$(partclone_for_fs "$_li_fs")" in
                partclone.dd) _li_u2=$((_li_z * 512)) ;;
                *) _li_u2=$(_used_bytes "$_li_pn" "$_li_fs" 2>/dev/null) ;;
            esac
            echo "$_li_i|$_li_u2" >> "$LC/inv/$1.used"
            case "$_li_u2" in ''|*[!0-9]*) _li_used=-1 ;; *) [ "$_li_used" -lt 0 ] || _li_used=$((_li_used + _li_u2)) ;; esac
        done < "$LC/inv/$1.parts"
        _li_gate=$(unique_guid_gate "$LC/inv/$1.parts" "$LC/inv/$1.parts.fail" 2>/dev/null)
        _li_mark=$(writemark_state "$1")   # #1798: a write that was cut is never a source
        if [ "$_li_n" -eq 0 ]; then _li_why="אין מחיצות על הדיסק"
        elif [ "$_li_mark" = incomplete ]; then _li_why=$WRITEMARK_HE
        elif [ "$_li_mark" != clean ]; then _li_why="לא הצלחנו לבדוק אם הכתיבה האחרונה אליו הושלמה"
        elif [ -n "$_li_unread" ]; then _li_why="לא הצלחנו לקרוא את מערכת הקבצים במחיצה$_li_unread"
        elif [ "$_li_need" -lt 0 ]; then _li_why="לא הצלחנו לחשב את פריסת המחיצות"
        elif [ -n "$_li_gate" ]; then _li_why=$_li_gate
        else _li_ok=1; fi
    fi
    echo "$_li_mark" > "$LC/inv/$1.mark"   # the screen's lc_disk field 16 (lcgui.sh); "" = not a GPT
    printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' "$_li_port" "$1" "$_li_model" "$_li_ser" \
        "$_li_size" "$_li_smart" "$(_lc_clean "$_li_sr")" "$(_lc_clean "$_li_os")" "$_li_n" "$_li_used" \
        "$_li_need" "$_li_ok" "$(_lc_clean "$_li_why")"
}

lc_inventory() {
    # Every drawer, once, into $LC/inv/. inv.state says when it is complete:
    # a half-written inventory must never look like a machine with fewer disks.
    mkdir -p "$LC/inv" || return 1
    rm -f "$LC/inv/"* "$LC/measure/"* 2>/dev/null
    echo scanning > "$LC/inv.state"
    for _lv_d in $(list_drawers); do
        lc_is_boot_media "$_lv_d" && continue   # #1914: the USB stick is never a source or a target
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        lc_inv_disk "$_lv_d" > "$LC/inv/$_lv_d.next" && mv "$LC/inv/$_lv_d.next" "$LC/inv/$_lv_d.line"
    done
    echo "done" > "$LC/inv.state"
}

lc_is_boot_media() {
    # #1914: 0 = $1 is the stick this machine booted from (usbclone.sh found it by
    # label). In usbclone mode a list that was not written is every disk: fail closed.
    [ "${IMAGECTL_MODE:-}" = usbclone ] || return 1
    [ -s "$RUN_DIR/usb-media.disks" ] || return 0
    grep -qx "$1" "$RUN_DIR/usb-media.disks"
}

lc_role() {
    # $1 = source, $2 = idx, $3 = type GUID. The inventory's role (System32
    # checked, #1369), else the GUID's. "unchecked" = the check itself failed.
    _lrl_x=$(awk -F'|' -v i="$2" '$1 == i { print $2; exit }' "$LC/inv/$1.roles" 2>/dev/null)
    [ -n "$_lrl_x" ] && echo "$_lrl_x" || _partition_role "$3"
}

lc_inv_field() {
    # $1 = disk, $2 = field number of its inventory line. Empty = no line.
    awk -F'|' -v i="$2" '{ print $i; exit }' "$LC/inv/$1.line" 2>/dev/null
}

lc_measure() {
    # $1 = source disk: will a smaller drawer take it (_lc_measure_fit), then
    # how many bytes the copy moves (lc_copy_size, lcsize.sh, #1443). The
    # return code is the fit's; the copy's size has its own file.
    rm -f "$LC/measure/$1.copy"
    _lc_measure_fit "$1"; _lm_rc=$?
    lc_copy_size "$1"
    return "$_lm_rc"
}

_lc_src_he() {   # $1 = source disk -> " (דיסק N)" by its drawer (slotnames.sh), "" when unmapped -- never "sdc" (#2084)
    _ls_n=$(disk_drawer "$1" "$(disk_port "$1" 2>/dev/null || echo 0)" 2>/dev/null); _ls_n=${_ls_n%%|*}
    case "$_ls_n" in ''|0|*[!0-9]*) ;; *) printf ' (דיסק %s)' "$_ls_n" ;; esac
}

_lc_measure_fit() {
    # $1 = source disk. $LC/measure/$1: state|need_min|reason. state: none (no
    # drawer is smaller than the layout -- nothing to measure), done (need_min
    # = the bytes a target needs once the source is shrunk), failed (reason).
    _lm_out="$LC/measure/$1"; mkdir -p "$LC/measure"
    echo "measuring||" > "$_lm_out"
    _lm_need=$(lc_inv_field "$1" 11); _lm_parts="$LC/inv/$1.parts"
    case "$_lm_need" in ''|-*|*[!0-9]*) echo "failed||המקור לא נקרא" > "$_lm_out"; return 1 ;; esac
    _lm_small=0
    for _lm_t in $(list_drawers); do
        [ "$_lm_t" = "$1" ] && continue
        _lm_ts=$(lc_inv_field "$_lm_t" 5); case "$_lm_ts" in ''|*[!0-9]*) continue ;; esac
        [ "$_lm_ts" -lt "$_lm_need" ] && _lm_small=1
    done
    [ "$_lm_small" = 1 ] || { echo "none||" > "$_lm_out"; return 0; }
    # #651: ntfsresize refuses a hibernated volume; say that, not its message.
    while IFS='|' read -r _lm_i _lm_g _lm_rest; do
        case "$(_partition_role "$_lm_g")" in windows|linux) ;; *) continue ;; esac
        _lm_pn=$(partition_node "$1" "$_lm_i")
        [ "$(_fs_of "$_lm_pn")" = ntfs ] || continue
        _lm_why=$(capture_ntfs_hibernation_reason "$_lm_pn" "$RUN_DIR/lc-probe") || {
            case "$_lm_why" in   # #2084 (mockup 09, until #2069): the words of the mockup, a disk by its number
                *"Fast Startup"*) echo "failed||המקור$(_lc_src_he "$1") במצב שינה של Windows — אי אפשר לכווץ אותו לדיסק הזה, שקטן ממנו. ב-Windows של המקור: ⁦powercfg /h off⁩, ואז כיבוי" ;;
                *) echo "failed||במקור$(_lc_src_he "$1") — מחיצה $_lm_i: $(_lc_clean "${_lm_why%.}")" ;;   # #1937: drawn on a TARGET's row
            esac > "$_lm_out"; return 1; }
    done < "$_lm_parts"
    cp "$_lm_parts" "$LC/measure/$1.parts" || { echo "failed||לא הצלחנו להכין את המדידה" > "$_lm_out"; return 1; }
    if ! shrink_plan "$1" "$LC/measure/$1.parts" 512; then
        echo "failed||במקור$(_lc_src_he "$1") — $(_lc_clean "${SHRINK_ERROR%.}")" > "$_lm_out"; return 1
    fi
    # #1466 P2: the run reuses this plan (lc_shrink_plan) on the same disk and table.
    cp "$RUN_DIR/shrink.plan" "$LC/measure/$1.plan" && _lc_clean "$(disk_serial "$1")" > "$LC/measure/$1.serial" || rm -f "$LC/measure/$1.plan"
    if [ ! -s "$RUN_DIR/shrink.plan" ]; then
        echo "done|$_lm_need|אין מה לכווץ במקור" > "$_lm_out"; return 0
    fi
    # The compacted layout, as shrink.sh will write it: every partition after
    # a shrunk one moves back by the sum of the shrinks before it.
    awk -F'|' '{ printf "%s|%s|%s|%.0f\n", $1, $4, $5, $7 / 512 }' "$RUN_DIR/shrink.plan" > "$LC/measure/$1.mark"
    _shrink_compact "$LC/measure/$1.mark" "$_lm_parts" > "$LC/measure/$1.compact" \
        && _lm_min=$(lc_layout_need "$LC/measure/$1.compact" "$(lc_inv_field "$1" 5)" 512) \
        || { echo "failed||לא הצלחנו לחשב את הפריסה המכווצת" > "$_lm_out"; return 1; }
    # #1478: no server here. The run shrinks a RAM view and never writes the
    # source (#1466), so there is no original layout to record (#926 is the
    # capture's in-place shrink). A view that cannot be had is refused in
    # lc_run by name, before a byte reaches the target.
    echo "done|$_lm_min|" > "$_lm_out"
}

lc_verdict() {
    # $1 target bytes, $2 source layout need (-1 = unknown), $3 measure state,
    # $4 need_min, $5 reason. stdout: verdict|need|reason. Pure: no disk.
    case "$1:$2" in *[!0-9:-]*|:*|*:) echo "unchecked||לא הצלחנו למדוד"; return ;; esac
    [ "$2" -ge 0 ] || { echo "unchecked||$5"; return; }
    [ "$1" -ge "$2" ] && { echo "fits|$2|"; return; }
    case "$3" in
        done) case "$4" in ''|*[!0-9]*) echo "unchecked||$5"; return ;; esac
              if [ "$1" -ge "$4" ]; then echo "shrink|$4|"; else echo "short|$4|"; fi ;;
        failed) # a minimum that was measured still says "does not fit" when it does not
                case "$4" in ''|*[!0-9]*) ;; *) [ "$1" -lt "$4" ] && { echo "short|$4|"; return; } ;; esac
                echo "unchecked|$4|${5:-המדידה נכשלה}" ;;
        *) echo "measuring|$2|" ;;
    esac
}

lc_verdict_for() {
    # $1 = source disk, $2 = target disk: lc_verdict from the files.
    _lf_m="measuring||"; [ -f "$LC/measure/$1" ] && _lf_m=$(cat "$LC/measure/$1")
    IFS='|' read -r _lf_st _lf_min _lf_why <<EOF
$_lf_m
EOF
    _lf_v=$(lc_verdict "$(lc_inv_field "$2" 5)" "$(lc_inv_field "$1" 11)" "$_lf_st" "$_lf_min" "$_lf_why")
    # #1354: "fits" and "fits with a shrink" also leave the minimum free in the
    # system partition -- the layout the target would get, the same check as
    # disk_fits. Not measured = unchecked, never fits.
    case "${_lf_v%%|*}" in
        fits) _lf_p="$LC/inv/$1.parts" ;;
        shrink) _lf_p="$LC/measure/$1.compact" ;;
        *) echo "$_lf_v"; return ;;
    esac
    _lf_f=$(lc_min_free "$1" "$(lc_inv_field "$2" 5)" "$_lf_p")
    _lf_n=${_lf_v#*|}; _lf_n=${_lf_n%%|*}
    case "$_lf_f" in
        ok) echo "$_lf_v" ;;
        short\|*) echo "short|$_lf_n|${_lf_f#*|}" ;;
        *) echo "unchecked||${_lf_f#*|}" ;;
    esac
}

lc_min_free() {
    # $1 = source, $2 = target bytes, $3 = the parts file of the layout the
    # target gets (original or compacted). min_free_check's answer, on a
    # manifest built from the inventory: roles as the inventory checked them (lc_role), used bytes
    # from inv/<src>.used (an unread one stays null -- unchecked).
    _fm_m="$LC/fit.json"; _fm_p=""
    # #1484: a value that is not a number is "not checked", never "ok" -- in the
    # kiosk min_free_gb was "not found", "bad number" fell through to ok.
    _fm_g=$(min_free_gb); case "$_fm_g" in ''|*[!0-9]*) echo "unchecked|לא הצלחנו לקרוא כמה מקום פנוי נדרש"; return 0 ;; esac
    [ "$_fm_g" -gt 0 ] || { echo ok; return 0; }   # 0 = the rule is off
    [ -s "$3" ] || { echo "unchecked|לא הצלחנו לקרוא את פריסת המקור"; return 0; }
    while IFS='|' read -r _fm_i _fm_t _fm_u _fm_f _fm_s; do
        _fm_b=$(awk -F'|' -v i="$_fm_i" '$1 == i { print $2; exit }' "$LC/inv/$1.used" 2>/dev/null)
        case "$_fm_b" in ''|*[!0-9]*) _fm_b=null ;; esac
        _fm_r=$(lc_role "$1" "$_fm_i" "$_fm_t")
        # #1591: a role that is not one of the known ones (empty: its resolver was missing) is "not
        # checked" -- not "no system partition", which min_free_check answers with ok.
        case "$_fm_r" in esp|msr|recovery|linux|swap|windows|data) ;; *) echo "unchecked|מחיצה $_fm_i: לא הצלחנו לבדוק אם היא מחיצת המערכת"; return 0 ;; esac
        _fm_p="$_fm_p{\"index\":$_fm_i,\"type_guid\":\"$_fm_t\",\"role\":\"$_fm_r\",\"fs\":null,\"start_sector\":$_fm_f,\"size_bytes\":$((_fm_s * 512)),\"used_bytes\":$_fm_b,\"file\":null,\"sha256\":null,\"expandable\":false},"
    done < "$3"
    printf '{"sector_size":512,"partitions":[%s]}\n' "${_fm_p%,}" > "$_fm_m"
    _fm_n=$(required_bytes "$_fm_m")
    case "$_fm_n:$2" in *[!0-9:]*|:*|*:) echo "unchecked|לא הצלחנו לחשב את הפריסה"; return 0 ;; esac
    min_free_check "$_fm_m" "$2" "$_fm_n"
}
