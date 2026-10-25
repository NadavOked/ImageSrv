# usbdiag.sh -- #1914: what the clone stick keeps about the machine, split
# from usbclone.sh before its 300-line wall. POSIX sh. Sourced by the agent
# (load gate); used in usbclone mode only:
#   * the controller's mode (IDE is said on the screen, Nadav 20/10);
#   * the clone report kept on IMGSRV_DATA and diag-<stamp>.txt -- never a
#     disk's serial (Nadav 20/10). By structure first: the report's serial
#     fields are deleted, the diag has no serial-bearing source (no smartctl,
#     no /sys/.../serial, vpd or by-id). The free text it does carry (dmesg,
#     agent.log) is kept only when every disk's bare serial is known -- and
#     then scrubbed of them; one unknown serial and those sections are left
#     out, said in their place (fail closed, review of d7166eb1).

# Nadav 20/10: the stick keeps nothing about the disks -- no serial, no SMART by
# serial. The machine's own facts (DMI model, controller and driver, link and
# stage speeds) and a disk's model, size and drawer stay: they are the diagnosis.
usb_serial_candidates() {
    # $1 = disk. Its bare serial from every place one lives, one a line: sysfs,
    # VPD page 0x80 (4-byte header), the cached smartctl -i, and the last word
    # of a t10 wwid ("t10.ATA <model> <serial>"). A naa./eui. wwid is a number,
    # not the serial: it is printed (it identifies the disk too) but proves nothing.
    trim "$(cat "$SYSROOT/sys/block/$1/device/serial" 2>/dev/null)"; echo
    tail -c +5 "$SYSROOT/sys/block/$1/device/vpd_pg80" 2>/dev/null | tr -d '\000'; echo
    sed -n 's/^Serial Number:[[:space:]]*//p' "$RUN_DIR/smart/$1.out" 2>/dev/null
    _sc_w=$(trim "$(cat "$SYSROOT/sys/block/$1/device/wwid" 2>/dev/null)")
    case "$_sc_w" in t10.*) echo "${_sc_w##* }" ;; esac
    # #1937: a disk behind USB (the stick itself, a USB drawer) -- its USB device's iSerialNumber
    _sc_u=$(usb_dev_of "$1"); [ -z "$_sc_u" ] || trim "$(cat "$SYSROOT/sys/bus/usb/devices/$_sc_u/serial" 2>/dev/null)"; echo
}

usb_dev_of() { usb_disk_syspath "$1" | tr '/' '\n' | grep -E '^[0-9]+-[0-9.]+$' | tail -n 1; }   # $1 = disk: its USB device (2-1), or nothing

usb_stick_no_serial() {
    # #1937: 0 = $1 is the boot stick and its USB device has no serial string -- read: the
    # device is there (idVendor), its `serial` is not (the kernel makes it only for an
    # iSerialNumber). Nothing to leak, so not "unknown". Any other disk stays fail closed.
    grep -qx "$1" "$RUN_DIR/usb-media.disks" 2>/dev/null || return 1
    _sn_u=$(usb_dev_of "$1"); [ -n "$_sn_u" ] && [ -s "$SYSROOT/sys/bus/usb/devices/$_sn_u/idVendor" ] \
        && [ ! -e "$SYSROOT/sys/bus/usb/devices/$_sn_u/serial" ]
}

usb_serials_snap() {
    # $@ = the clone's source and targets, at its start: each one's trimmed
    # candidates into $RUN_DIR/usb.snap/<disk> -- RAM, never the stick. The
    # report is saved later, when the name may be another disk's (review of
    # c5ed7ff3: a drawer swapped under the same sdX scrubbed the wrong serial).
    mkdir -p "$RUN_DIR/usb.snap" || return 1
    for _sn_d in "$@"; do
        usb_serial_candidates "$_sn_d" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | awk 'length($0) >= 4 { print }' \
            > "$RUN_DIR/usb.snap/$_sn_d"
        _sn_w=$(disk_serial "$_sn_d"); [ -z "$_sn_w" ] || printf '%s\n' "$_sn_w" >> "$RUN_DIR/usb.snap/$_sn_d"
    done
}

usb_serials() {
    # $1 = file: every string that identifies a disk (bare serials, wwid), one a
    # line; $2.. = disks the text names (a report's source and targets): theirs
    # come from usb_serials_snap, taken at the clone's start -- the name now may
    # be another disk, or none. 1 = a disk present whose bare serial was found
    # nowhere, or a named one with no snapshot: free text cannot be shown clean.
    _ss_f=$1; shift
    : > "$_ss_f" || return 1; _ss_rc=0
    for _ss_x in "$@"; do
        if grep -q . "$RUN_DIR/usb.snap/$_ss_x" 2>/dev/null; then cat "$RUN_DIR/usb.snap/$_ss_x" >> "$_ss_f"
        else _ss_rc=1; log "usbclone: $_ss_x: no serial snapshot from the clone's start -- its free text is left out" >&2; fi
    done
    for _ss_d in $(list_disks); do
        _ss_n=0
        usb_serial_candidates "$_ss_d" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | awk 'length($0) >= 4 { print }' > "$_ss_f.disk"
        [ -s "$_ss_f.disk" ] && { cat "$_ss_f.disk" >> "$_ss_f"; _ss_n=1; }
        _ss_w=$(disk_serial "$_ss_d"); [ -z "$_ss_w" ] || printf '%s\n' "$_ss_w" >> "$_ss_f"
        [ "$_ss_n" = 1 ] || usb_stick_no_serial "$_ss_d" || { _ss_rc=1; log "usbclone: $_ss_d: its serial was found nowhere -- free text is left out of the stick's files" >&2; }
    done
    return "$_ss_rc"
}

usb_scrub() {   # $1 = usb_serials' file; stdin -> stdout with each of them as <serial>
    # Nadav 20/10: nothing from what is ON a disk either. The log carries the
    # source's disk and partition GUIDs (expand.sh: "disk GUID ... came back",
    # sgdisk's -u/-U line on a refusal, which _lc_gpt_why puts in a target's
    # error), mkswap's UUID= and LABEL=, and ext4's "mounted filesystem <uuid>"
    # in dmesg. A value is not edited out (a label has spaces, quotes of any
    # kind): the whole line goes, said in its place -- first, before the serial
    # replacement can break a GUID's shape (review). Fail closed: lines about
    # GUIDs at all go too; the allowed facts come from structured sources.
    # FILENAME, not NR == FNR: an empty $1 would make every input line a "serial", and print nothing
    awk -v re="$USB_ID_RE" -v gone="$USB_ID_GONE" 'FILENAME == ARGV[1] { if (length($0) >= 4) s[$0]; next }
         tolower($0) ~ re { print gone; next }
         { for (k in s) while ((i = index($0, k)) > 0) $0 = substr($0, 1, i - 1) "<serial>" substr($0, i + length(k)); print }' "$1" -
}
_h4='[0-9a-f][0-9a-f][0-9a-f][0-9a-f]'; _nb='(^|[^0-9a-z])'; _na='([^0-9a-z]|$)'
#: what names a disk's contents (lower case; no {n}: mawk): LABEL=/UUID=/PARTUUID=/PARTLABEL=,
#: GUID, volume serial / vol id, a GUID-shaped word, a XXXX-XXXX volume serial, 16 hex (NTFS)
USB_ID_RE="(part)?(label|uuid)[[:space:]]*[=:]|guid|volume serial|vol(ume)? id|$_h4$_h4-$_h4-$_h4-$_h4-$_h4$_h4$_h4|$_nb$_h4-$_h4$_na|$_nb$_h4$_h4$_h4$_h4$_na"
USB_ID_GONE="[שורה הוסרה — מזהה דיסק]"

usb_machine_json() {   # the machine (DMI), never a disk
    jq -n --arg v "$(cat "$SYSROOT/sys/class/dmi/id/sys_vendor" 2>/dev/null)" \
        --arg p "$(cat "$SYSROOT/sys/class/dmi/id/product_name" 2>/dev/null)" '{vendor: $v, product: $p}'
}

# Nadav 20/10 (measured in the college: 60-80 MB/s in IDE mode, 134 in AHCI):
# a controller in IDE/Legacy mode is said on the screen. By the driver of the
# PCI controller the drawers sit behind; a driver that cannot be read says nothing.
usb_disk_syspath() { readlink -f "$SYSROOT/sys/block/$1" 2>/dev/null; }
usb_pci_driver() { _pd_r=$(readlink "$SYSROOT/sys/bus/pci/devices/$1/driver" 2>/dev/null); printf '%s' "${_pd_r##*/}"; }

usb_disk_ctrl() {   # $1 = disk. The driver of the deepest PCI device on its path, or nothing.
    _dc_b=$(usb_disk_syspath "$1" | tr '/' '\n' | grep -E '^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$' | tail -n 1)
    [ -z "$_dc_b" ] || usb_pci_driver "$_dc_b"
}

usb_ctrl_mode() {
    # $RUN_DIR/usb.ctrl = mode|disk=driver ...; mode ide (one drawer behind
    # ata_piix/pata_*/ata_generic), other, or "" (no driver read: nothing said).
    _cm_m=""; _cm_d=""
    for _cm_x in $(list_drawers); do
        grep -qx "$_cm_x" "$RUN_DIR/usb-media.disks" 2>/dev/null && continue
        _cm_r=$(usb_disk_ctrl "$_cm_x"); [ -n "$_cm_r" ] || continue
        _cm_d="$_cm_d $_cm_x=$_cm_r"
        case "$_cm_r" in ata_piix|pata_*|ata_generic) _cm_m=ide ;; *) [ "$_cm_m" = ide ] || _cm_m=other ;; esac
    done
    printf '%s|%s\n' "$_cm_m" "${_cm_d# }" > "$RUN_DIR/usb.ctrl"
    [ "$_cm_m" != ide ] || log "usbclone: the disk controller is in IDE mode (${_cm_d# }) -- AHCI in the BIOS is about twice as fast" >&2
}

usb_report_save() {
    # $1 = the run dir of lc_report. #1913: no server to send to -- the report
    # is on the screen (report = usb) and, when the data partition is there,
    # kept on the stick: without a serial (Nadav 20/10), with the machine's
    # DMI model. A copy that could not be made so is not kept, aloud.
    echo usb > "$1/report"
    [ "${USB_DATA_STATE:-none}" = mounted ] || { log "usbclone: report not kept on the stick (no data partition)"; return 0; }
    _rs_f="$USB_DATA_DIR/reports/clone-$(date -u +%Y%m%d-%H%M%S).json"
    # The serial fields go by structure. The free text (a target's error, the
    # source's restore and the view's teardown) passes the scrub only when every
    # serial is known; otherwise it is replaced, said -- the numbers, states and
    # times stay (fail closed, as the diag; review of 1e79ace0).
    # shellcheck disable=SC2046 # the report's disks are a word list
    _rs_ok=true; usb_serials "$RUN_DIR/usb.serials" $(cat "$1/src" "$1/targets" 2>/dev/null) || _rs_ok=false
    mkdir -p "$USB_DATA_DIR/reports" \
        && jq --argjson m "$(usb_machine_json)" --arg c "$(cat "$RUN_DIR/usb.ctrl" 2>/dev/null)" \
            --argjson ok "$_rs_ok" --arg r "הוסר — לא ניתן לוודא שאין בו serial" --arg re "$USB_ID_RE" --arg g "$USB_ID_GONE" \
            'del(.. | .serial?) | .machine = $m | .controller = $c
             | walk(if type == "string" and (ascii_downcase | test($re)) then $g else . end)   # one JSON line: by field, not by line
             | if $ok then . else
                 (if (.targets | type) == "array" then .targets |= map(if has("error") then .error = $r else . end) else . end)
                 | (if has("source_restore") then .source_restore = $r else . end)
                 | (if has("snapshot_teardown") then .snapshot_teardown = $r else . end) end' "$1/report.json" \
            | usb_scrub "$RUN_DIR/usb.serials" > "$_rs_f.next" && jq -e . "$_rs_f.next" > /dev/null && mv "$_rs_f.next" "$_rs_f" && sync \
        && { log "usbclone: report kept on the stick (no serials)"; usb_diag clone || :; return 0; }
    rm -f "$_rs_f.next"; log "usbclone: the report could not be written to the stick without serials -- not kept"
}

# The machine survey (Nadav 20/10, a round over several models): every field is
# read here, none is asked of a person; one that cannot be read says so, never
# blank. Nothing about a disk but its port, model and size.
USB_NR="לא נקרא"
_dx() { _dx_v=$(cat "$SYSROOT$1" 2>/dev/null | head -n 1); printf '%s' "${_dx_v:-$USB_NR}"; }   # $1 = a sysfs/proc path

usb_ctrl_kind() {   # $1 = driver, $2 = PCI class. AHCI|IDE|RAID|VMD|NVMe|other.
    case "$1" in ahci) echo AHCI ;; ata_piix|pata_*|ata_generic) echo IDE ;; vmd) echo VMD ;; nvme) echo NVMe ;;
        *) case "$2" in 0x0101*) echo IDE ;; 0x0104*) echo RAID ;; 0x0106*) echo AHCI ;; 0x0108*) echo NVMe ;; *) echo other ;; esac ;; esac
}

usb_secure_boot() {
    [ -d "$SYSROOT/sys/firmware/efi" ] || { echo "BIOS (Secure Boot: n/a)"; return; }
    _sb=$(od -An -tu1 "$SYSROOT/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c" 2>/dev/null | awk '{ print $NF }' | tail -n 1)
    case "$_sb" in 1) echo "UEFI, Secure Boot on" ;; 0) echo "UEFI, Secure Boot off" ;; *) echo "UEFI, Secure Boot $USB_NR" ;; esac
}

usb_diag_machine() {
    echo "machine: $(_dx /sys/class/dmi/id/sys_vendor) $(_dx /sys/class/dmi/id/product_name)"
    echo "BIOS: $(_dx /sys/class/dmi/id/bios_version), $(_dx /sys/class/dmi/id/bios_date)"
    echo "boot: $(usb_secure_boot)"
    _cpu=$(sed -n 's/^model name[[:space:]]*:[[:space:]]*//p' "$SYSROOT/proc/cpuinfo" 2>/dev/null | head -n 1)
    _cor=$(sed -n 's/^cpu cores[[:space:]]*:[[:space:]]*//p' "$SYSROOT/proc/cpuinfo" 2>/dev/null | head -n 1)
    _thr=$(grep -c '^processor' "$SYSROOT/proc/cpuinfo" 2>/dev/null)
    _flg=$(grep -m1 '^flags' "$SYSROOT/proc/cpuinfo" 2>/dev/null)
    echo "CPU: ${_cpu:-$USB_NR} · cores ${_cor:-$USB_NR} · threads ${_thr:-$USB_NR}" \
        "· aes $(case "$_flg" in '') echo "$USB_NR" ;; *" aes"*) echo yes ;; *) echo no ;; esac)" \
        "· sha $(case "$_flg" in '') echo "$USB_NR" ;; *" sha_ni"*) echo yes ;; *) echo no ;; esac)"
    _mem=$(awk '/^MemTotal:/ { printf "%.1f GB", $2 / 1048576 }' "$SYSROOT/proc/meminfo" 2>/dev/null)
    echo "RAM: ${_mem:-$USB_NR}"
    _tmp=""
    for _h in "$SYSROOT"/sys/class/hwmon/*; do
        case "$(cat "$_h/name" 2>/dev/null)" in coretemp|k10temp|zenpower) _tmp=$(cat "$_h/temp1_input" 2>/dev/null); break ;; esac
    done
    [ -n "$_tmp" ] || for _z in "$SYSROOT"/sys/class/thermal/thermal_zone*; do
        case "$(cat "$_z/type" 2>/dev/null)" in x86_pkg_temp|acpitz) _tmp=$(cat "$_z/temp" 2>/dev/null); break ;; esac
    done
    case "$_tmp" in ''|*[!0-9]*) echo "CPU temperature: $USB_NR" ;; *) echo "CPU temperature: $((_tmp / 1000)) C" ;; esac
    echo; echo "== SATA links: port, speed, the disk on it (model, size -- no serial)"
    _sl=0
    for _l in "$SYSROOT"/sys/class/ata_link/link*; do
        [ -e "$_l" ] || continue; _sl=1; _ln=${_l##*/link}; _ld="(none)"
        for _b in $(list_disks); do
            case "$(usb_disk_syspath "$_b")" in */ata${_ln%%.*}/*)
                _ld="$_b $(trim "$(cat "$SYSROOT/sys/block/$_b/device/model" 2>/dev/null)") $(( $(cat "$SYSROOT/sys/block/$_b/size" 2>/dev/null || echo 0) / 2097152 )) GB" ;; esac
        done
        echo "port $_ln: $(_dx "${_l#"$SYSROOT"}/sata_spd") · $_ld"
    done
    [ "$_sl" = 1 ] || echo "(no SATA links -- $USB_NR or no SATA controller)"
    echo; echo "== controllers: driver, mode"
    for _p in "$SYSROOT"/sys/bus/pci/devices/*; do
        [ -r "$_p/class" ] || continue; read -r _pc < "$_p/class"
        _pr=$(readlink "$_p/driver" 2>/dev/null); _pr=${_pr##*/}
        case "$_pc" in
            0x01*) echo "storage ${_p##*/} $(_dx "${_p#"$SYSROOT"}/vendor"):$(_dx "${_p#"$SYSROOT"}/device") driver=${_pr:-$USB_NR} mode=$(usb_ctrl_kind "$_pr" "$_pc")" ;;
            0x02*) echo "network ${_p##*/} $(_dx "${_p#"$SYSROOT"}/vendor"):$(_dx "${_p#"$SYSROOT"}/device") driver=${_pr:-$USB_NR}" ;;
            0x03*) echo "display ${_p##*/} $(_dx "${_p#"$SYSROOT"}/vendor"):$(_dx "${_p#"$SYSROOT"}/device") driver=${_pr:-$USB_NR}" ;;
        esac
    done
    echo "fb0: $(_dx /sys/class/graphics/fb0/name) $(_dx /sys/class/graphics/fb0/virtual_size) $(_dx /sys/class/graphics/fb0/bits_per_pixel)bpp"
    _us=$(head -n 1 "$RUN_DIR/usb-media.disks" 2>/dev/null); _up=$(usb_disk_syspath "${_us:-none}")
    _uport=$(printf '%s\n' "$_up" | tr '/' '\n' | grep -E '^[0-9]+-[0-9.]+$' | tail -n 1)
    _usp=$(cat "$SYSROOT/sys/bus/usb/devices/$_uport/speed" 2>/dev/null)
    case "$_usp" in 480) _usp="480 Mb/s (USB 2.0)" ;; 5000|10000|20000) _usp="$_usp Mb/s (USB 3.x)" ;; '') _usp=$USB_NR ;; *) _usp="$_usp Mb/s" ;; esac
    _uc=$(printf '%s\n' "$_up" | tr '/' '\n' | grep -E '^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$' | tail -n 1)
    echo "USB boot: ${_us:-$USB_NR} on ${_uc:-$USB_NR} ($(usb_pci_driver "${_uc:-none}" | grep . || echo "$USB_NR")), $_usp"
    echo "kernel to GUI: $(cat "$RUN_DIR/usb.gui_up" 2>/dev/null | grep . || echo "$USB_NR") s (uptime when the clone screen was up)"
    _rn="${LC:-$RUN_DIR/lc}/run"; _tot=$(cat "$_rn/total" 2>/dev/null)
    if [ -s "$_rn/started" ]; then
        echo; echo "== the clone of this boot: MB/s per stage (the bytes it read / the stage's seconds)"
        for _st in copy_s verify_s info_s resize_s restore_s; do
            _ss=$(cat "$_rn/$_st" 2>/dev/null)
            case "$_ss:$_tot" in *[!0-9:]*|:*|*:) echo "${_st%_s}: $USB_NR" ;;
                0:*) echo "${_st%_s}: 0 s" ;; *) echo "${_st%_s}: $_ss s, $((_tot / _ss / 1000000)) MB/s" ;; esac
        done
    fi
}

usb_diag() {
    # $1 = why. A report for whoever tests in the field (college 20/10: "no
    # disks" on an old HP, and nothing to send back): diag-<stamp>.txt on the
    # data partition, which Windows 10+ shows as a drive. The name goes to
    # $RUN_DIR/usb.diag for the screens. Read-only questions only.
    [ "${USB_DATA_STATE:-none}" = mounted ] \
        || { log "usbclone: no data partition -- the diagnostic report is not kept"; return 1; }
    _dg_n=$(cat "$RUN_DIR/usb.diag" 2>/dev/null); [ -n "$_dg_n" ] || _dg_n="diag-$(date +%Y%m%d-%H%M%S).txt"
    _dg_f="$USB_DATA_DIR/$_dg_n"   # one file a boot: written again with what came since (the screen, a clone)
    # 0: dmesg and agent.log stay out. The clone's disks too: one pulled since is still in the log.
    # shellcheck disable=SC2046 # a word list
    _dg_ft=1; usb_serials "$RUN_DIR/usb.serials" $(cat "${LC:-$RUN_DIR/lc}/run/src" "${LC:-$RUN_DIR/lc}/run/targets" 2>/dev/null) || _dg_ft=0
    _dg_out="הוסר — לא ניתן לוודא שאין בו serial (removed: cannot verify it holds no serial)"
    {
        echo "ImageSrv USB ${AGENT_VERSION:-unknown} -- diagnostic report ($1)"
        echo "time: $(date '+%Y-%m-%d %H:%M:%S') (the machine's clock, not verified)"
        echo "kernel: $(uname -r 2>&1)"
        usb_diag_machine
        echo; echo "== storage controllers (PCI class 01): vendor:device, driver"; _dg_k=""
        for _dg_d in "$SYSROOT"/sys/bus/pci/devices/*; do
            [ -r "$_dg_d/class" ] || continue
            read -r _dg_c < "$_dg_d/class"; case "$_dg_c" in 0x01*) ;; *) continue ;; esac
            _dg_v="$(cat "$_dg_d/vendor" 2>/dev/null) $(cat "$_dg_d/device" 2>/dev/null)"
            _dg_r=$(readlink "$_dg_d/driver" 2>/dev/null); _dg_r=${_dg_r##*/}
            echo "${_dg_d##*/} class=$_dg_c id=$_dg_v driver=${_dg_r:-(none)} $(cat "$_dg_d/modalias" 2>/dev/null)"; _dg_k="$_dg_k ${_dg_d##*/}=${_dg_r:-(none)}:$(usb_ctrl_kind "$_dg_r" "$_dg_c")"
        done
        _dg_m=$(cat "$RUN_DIR/usb.ctrl" 2>/dev/null); case "$_dg_m" in ''|'|') _dg_m="$USB_NR (no drawer with a disk) -- by controller:${_dg_k:- none}" ;; esac   # #1937: never "|"
        echo "controller mode (drawers): $_dg_m"
        echo; if command -v hw_diag >/dev/null 2>&1; then hw_diag; else echo "== hardware issues: $USB_NR (hwissues.sh not loaded)"; fi   # #1921
        echo; echo "== /proc/partitions"; cat "$SYSROOT/proc/partitions" 2>&1
        echo; echo "== /sys/block: removable, size (512-byte sectors)"
        for _dg_b in "$SYSROOT"/sys/block/*; do
            [ -e "$_dg_b" ] && echo "${_dg_b##*/} removable=$(cat "$_dg_b/removable" 2>/dev/null) size=$(cat "$_dg_b/size" 2>/dev/null)"
        done
        echo "boot media (never a source or a target): $(tr '\n' ' ' < "$RUN_DIR/usb-media.disks" 2>/dev/null)"
        echo; echo "== required modules that did not load"; cat "$RUN_DIR/modules.failed" 2>/dev/null || echo "(none)"
        echo; echo "== framebuffer / DRM"; ls "$SYSROOT/sys/class/graphics" "$SYSROOT/sys/class/drm" 2>&1
        echo; echo "== dmesg: ata, ahci, piix, pata, nvme, vmd, scsi, sd (no identify/serial lines)"
        if [ "$_dg_ft" = 1 ]; then
            dmesg 2>&1 | grep -iE 'ata[0-9]|ahci|piix|pata|nvme|vmd|scsi|sd[a-z]|drm|fb[0-9]' \
                | grep -viE 'serial|wwn|wwid|vpd|by-id|scsi_id|Product:|SerialNumber' | tail -n 150
        else echo "$_dg_out"; fi
        echo; echo "== agent.log (tail)"
        if [ "$_dg_ft" = 1 ]; then tail -n 150 "$LOG_FILE" 2>&1; else echo "$_dg_out"; fi
        :   # the file is the report: a section that failed says so in it
    } 2>&1 | usb_scrub "$RUN_DIR/usb.serials" > "$_dg_f.next" && [ -s "$_dg_f.next" ] && mv "$_dg_f.next" "$_dg_f" && sync \
        || { log "usbclone: the diagnostic report could not be written to the stick"; return 1; }
    echo "$_dg_n" > "$RUN_DIR/usb.diag"; log "usbclone: diagnostic report kept on the stick: $_dg_n"
}
