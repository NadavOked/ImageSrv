# hwissues.sh -- #1921: the hardware table -- the cable, the drawer, the controller.
# POSIX sh (busybox ash). Nadav 20/10: these are not problems of the disk. They are a
# NOTE on the drawer (or the machine), in their own section of the diag, and never the
# disk's colour -- the colour is the SMART table's alone (smartjudge.sh).
#
# The words and their sentences are lib/data/hw-issues.tsv (the server reads the same
# file). What each word means is measured here, from what the machine says about itself:
#   crc         -- 199/218/210 or devstat CRC over crc_warn_threshold (smartjudge.sh)
#   icrc_logged -- an ICRC in the disk's own ATA error log (smartjudge.sh; Nadav 21/10)
#   link_slow   -- the link below what BOTH the port and the disk support (#432; an HP
#                  8100 is SATA II, so 3.0 Gb/s there is its ceiling, not slow)
#   link_reset  -- libata reset the link (dmesg: hard resetting link, COMRESET failed)
#   drawer_gone -- the link went down after it was up, or the device was disabled, or the
#                  disk left /sys/block -- only AFTER the drawer was identified (#1937): the
#                  first hw_refresh keeps the time of the last dmesg line, and only a later
#                  line counts. The probe at boot is not a drawer that dropped (ata_piix in
#                  IDE mode: "ataN.01: SATA link down" is the empty slave link of the port;
#                  smartctl's Phy Event counters count since power-on -- neither is read).
#   usb2       -- the disk sits behind a USB port at 480 Mb/s
# The machine's IDE mode is usbdiag.sh's (lc_ide), said on the source screen.
# Each one is said only on positive evidence; one that cannot be read says nothing --
# and nothing here ever turns a disk green or red (principle 5 stays on the SMART side).

DMESG="${DMESG:-dmesg}"

_hw_ata() {   # $1 = disk. Its libata port number (the N of ataN), or nothing.
    _ha_p=$(readlink -f "$SYSROOT/sys/block/$1/device" 2>/dev/null)
    case "$_ha_p" in */ata[0-9]*/*) _ha_n=${_ha_p##*/ata}; _ha_n=${_ha_n%%/*}
        case "$_ha_n" in ''|*[!0-9]*) ;; *) printf '%s' "$_ha_n" ;; esac ;; esac
}

hw_link_words() {
    # $1 = the ataN number, $2 = the dmesg time the drawer was identified at (empty = not
    # read: no drawer_gone, never a guess), stdin = dmesg. link_reset / drawer_gone, a line
    # each. A link is ataN, or ataN.NN (a slave link): "down" counts on the link that was up.
    awk -v re="ata$1(\\\\.[0-9]+)?: " -v t0="$2" '
        $0 !~ re { next }
        { ts = ""; if (match($0, /^\[ *[0-9]+\.[0-9]+\]/)) ts = substr($0, RSTART + 1, RLENGTH - 2) + 0
          l = $0; sub(/^\[[^]]*\] */, "", l); sub(/: .*/, "", l)
          after = t0 != "" && ts != "" && ts > t0 + 0 }
        /hard resetting link|COMRESET failed|softreset failed|SRST failed/ { reset = 1 }
        /SATA link up/ { up[l] = 1 }
        /SATA link down/ && up[l] && after { gone = 1 }
        /\.[0-9]+: disable/ && after { gone = 1 }
        END { if (reset) print "link_reset"; if (gone) print "drawer_gone" }'
}

_hw_dmesg_t() { "$DMESG" 2>/dev/null | sed -n '$s/^\[ *\([0-9][0-9]*\.[0-9]*\)\].*/\1/p'; }   # the last line's time, or nothing

hw_refresh() {
    # $1 = disk. Its words into $RUN_DIR/smart/<d>.hw (renamed into place), from the
    # judge's findings (crc), the link, dmesg and the USB port. Called by smart_probe
    # (identified) and again after the write (crcdelta.sh).
    _hr_f="$RUN_DIR/smart/$1.hw"
    [ -s "$RUN_DIR/smart/$1.hw0" ] || _hw_dmesg_t > "$RUN_DIR/smart/$1.hw0"   # #1937: identified at
    _hr_t0=$(cat "$RUN_DIR/smart/$1.hw0" 2>/dev/null)
    {
        awk -F'|' '$1 == "hw" && !seen[$2]++ { print $2 }' "$RUN_DIR/smart/$1.find" 2>/dev/null   # crc, icrc_logged
        if command -v disk_link >/dev/null 2>&1; then
            _hr_l=$(disk_link "$1"); _hr_s=${_hr_l% *}; _hr_m=${_hr_l#* }
            case "$_hr_s:$_hr_m" in
                *null*) ;;   # either one not read: not slow, and not "fine" either
                *) [ "${_hr_s%.*}${_hr_s#*.}" -lt "${_hr_m%.*}${_hr_m#*.}" ] && echo link_slow ;;
            esac
        fi
        _hr_a=$(_hw_ata "$1")
        [ -z "$_hr_a" ] || "$DMESG" 2>/dev/null | hw_link_words "$_hr_a" "$_hr_t0"
        [ -n "$_hr_t0" ] && [ ! -e "$SYSROOT/sys/block/$1" ] && echo drawer_gone   # it was here, and is not
        _hr_u=$(readlink -f "$SYSROOT/sys/block/$1" 2>/dev/null | tr '/' '\n' | grep -E '^[0-9]+-[0-9.]+$' | tail -n 1)
        [ -n "$_hr_u" ] && [ "$(cat "$SYSROOT/sys/bus/usb/devices/$_hr_u/speed" 2>/dev/null)" = 480 ] && echo usb2
        :
    } > "$_hr_f.next" && mv "$_hr_f.next" "$_hr_f"
}

hw_words() { cat "$RUN_DIR/smart/$1.hw" 2>/dev/null; }   # $1 = disk; the cached words, a line each

hw_note_he() {
    # $1 = disk, $2 = words not to say (the cloner's screen already draws crc and the
    # link from their own fields). The sentences of hw-issues.tsv, joined by " · ";
    # the field separator of a GUI record ('|') never appears in them.
    for _hn_w in $(hw_words "$1"); do
        case " $2 " in *" $_hn_w "*) continue ;; esac
        _hn_s=$(smart_kb_he "$_hn_w"); [ -n "$_hn_s" ] && printf '%s\n' "$_hn_s"
    done | awk 'NR > 1 { printf " · " } { printf "%s", $0 }' | tr -d '|'
}

hw_diag() {
    # The diag's own section (usbdiag.sh): every drawer's hardware notes, by port --
    # model and size only, never a serial (Nadav 20/10).
    echo "== hardware issues (cable / drawer / controller -- not the disk; #1921)"
    _hd_any=0
    for _hd_d in $(list_drawers); do
        grep -qx "$_hd_d" "$RUN_DIR/usb-media.disks" 2>/dev/null && continue
        _hd_n=$(hw_note_he "$_hd_d" ""); [ -n "$_hd_n" ] || continue; _hd_any=1
        echo "$_hd_d (port $(disk_port "$_hd_d"), $(trim "$(cat "$SYSROOT/sys/block/$_hd_d/device/model" 2>/dev/null)")): $(hw_words "$_hd_d" | tr '\n' ' ')-- $_hd_n"
    done
    [ "$(cut -d'|' -f1 "$RUN_DIR/usb.ctrl" 2>/dev/null)" != ide ] || { _hd_any=1; echo "machine: ide -- $(smart_kb_he ide)"; }
    [ "$_hd_any" = 1 ] || echo "(none found -- of what could be read)"
}
