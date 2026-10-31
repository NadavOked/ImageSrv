# slotnames.sh -- the operator's name for each SATA slot of this machine (#704).
# POSIX sh (busybox ash).
#
# The console names a connection, not a disk: "sata:<port>" (the ataN of
# interface 2) keeps its name when the disk in that drawer is replaced. hello
# answers them as `slot_names` ({"sata:1": "..."}). The cloner screen draws
# the name instead of "disk N"; with no name it stays "disk N" (SATA 0 = disk
# 1). A label only -- selection and target_ports stay by port.
#
# Only sata: keys reach the screen: it lays out one card per SATA port, and a
# disk with no port (NVMe, keyed serial:) has no card there to name.

slot_names_refresh() {
    # The last hello answer -> $RUN_DIR/slot_names, one "port|name" per line
    # after a first line "ok" -- the evidence jq read the answer whole. An
    # answer without the field (a poll response, an older server) or one jq
    # cannot read is not "no names": the previous file stays (principle 5).
    # 0 = refreshed.
    _sn_n="$RUN_DIR/slot_names.next"
    jq -r 'if (.slot_names | type) != "object" then error("no slot_names") else
        "ok", (.slot_names | to_entries[] | select(.key | test("^sata:[1-9][0-9]?$")) |
        "\(.key | ltrimstr("sata:"))|\(.value | tostring | gsub("[\\r\\n|]"; " "))") end' \
        "$RUN_DIR/response.json" > "$_sn_n" 2>/dev/null
    [ "$(head -n 1 "$_sn_n" 2>/dev/null)" = ok ] || { rm -f "$_sn_n"; return 1; }
    mv "$_sn_n" "$RUN_DIR/slot_names"
}

drawer_slots_refresh() {
    # Preserve the previous mapping unless jq reads a complete array.
    _sn_n="$RUN_DIR/drawer_slots.next"
    jq -r 'if (.drawer_slots | type) != "array" then error("no drawer_slots") else
        "ok", (.drawer_slots | to_entries[] |
        select(.value | type == "string") |
        select((.value | test("^nvme:[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\\.[0-7]$")) or
               ((.value | test("^sata:[1-9][0-9]?$")) and
                ((.value | ltrimstr("sata:") | tonumber) <= 32))) |
        "\(.key + 1)|\(.value)") end' \
        "$RUN_DIR/response.json" > "$_sn_n" 2>/dev/null
    [ "$(head -n 1 "$_sn_n" 2>/dev/null)" = ok ] || { rm -f "$_sn_n"; return 1; }
    mv "$_sn_n" "$RUN_DIR/drawer_slots"
}

slot_names_records() {
    # cslot_name=<port>|<name> per named slot, for the cloner state file.
    [ -f "$RUN_DIR/slot_names" ] || return 0
    sed -n '2,$s/^/cslot_name=/p' "$RUN_DIR/slot_names"
}

# #1495: every screen on this machine numbers a disk by the mapping above --
# "דיסק N" is the drawer, never the ATA port, which stays the identity on the
# wire (target_ports, SMART). The screen only draws what these give it.

_ds_conn() {
    # $1 = key. The connection as the console writes it (console.js connText).
    case "$1" in
        sata:*) printf 'SATA %s' "$((${1#sata:} - 1))" ;;
        nvme:*) _ds_c=${1#nvme:}; printf 'NVMe %s' "${_ds_c#0000:}" ;;
    esac
}

disk_key() {
    # $1 = dev, $2 = its port (0 = none). sata:<port>, else nvme:<PCI> of the
    # controller (disklink.sh), else nothing: a connection we cannot name.
    case "$2" in ''|0|*[!0-9]*) ;; *) printf 'sata:%s' "$2"; return 0 ;; esac
    _dk_p=$(disk_pci_json "$1" 2>/dev/null | sed -n 's/^,"pci":"\(.*\)"$/\1/p')
    [ -z "$_dk_p" ] || printf 'nvme:%s' "$_dk_p"
}

disk_drawer() {
    # $1 = dev, $2 = port. "<drawer>|<connection>": the drawer of the disk's
    # connection, 0 = not in the mapping -- the screen says "SATA n (לא
    # ממופה)", never a guessed number. No mapping file (an older server, no
    # answer yet): the declared convention drawer k = sata:k, as the console.
    _dd_k=$(disk_key "$1" "$2"); _dd_n=
    if [ -f "$RUN_DIR/drawer_slots" ]; then
        [ -z "$_dd_k" ] || _dd_n=$(awk -F'|' -v k="$_dd_k" 'NR > 1 && $2 == k { print $1; exit }' "$RUN_DIR/drawer_slots")
    else
        case "$_dd_k" in sata:*) _dd_n=${_dd_k#sata:} ;; esac
    fi
    printf '%s|%s' "${_dd_n:-0}" "$(_ds_conn "$_dd_k")"
}

drawer_slots_records() {
    # cdrawer_slot=<drawer>|<connection>|<slot name> per drawer of the mapping
    # (empty drawers too) -- none without a mapping file: the screen keeps the
    # convention. coptical=<drawer>|<connection>|<model> per CD/DVD drive: it
    # is shown by name, never as a disk or an empty drawer. No sr_mod = no
    # /sys/block/sr* to look at: no records (disklink.sh optical_json).
    if [ -f "$RUN_DIR/drawer_slots" ]; then
        sed -n '2,$p' "$RUN_DIR/drawer_slots" | while IFS='|' read -r _dr_n _dr_k; do
            _dr_name=
            case "$_dr_k" in sata:*) [ ! -f "$RUN_DIR/slot_names" ] ||
                _dr_name=$(awk -F'|' -v p="${_dr_k#sata:}" 'NR > 1 && $1 == p { print $2; exit }' "$RUN_DIR/slot_names") ;; esac
            printf 'cdrawer_slot=%s|%s|%s\n' "$_dr_n" "$(_ds_conn "$_dr_k")" "$_dr_name"
        done
    fi
    [ -d "$SYSROOT/sys/module/sr_mod" ] || return 0
    for _dr_d in "$SYSROOT"/sys/block/sr*; do
        [ -e "$_dr_d" ] || continue
        _dr_p=$(port_from_path "$(readlink -f "$_dr_d/device" 2>/dev/null)")
        printf 'coptical=%s|%s\n' "$(disk_drawer "${_dr_d##*/}" "${_dr_p:-0}")" \
            "$(trim "$(cat "$_dr_d/device/model" 2>/dev/null)" | tr '\r\n|' '   ')"
    done
}

disk_slot_records() {
    # disk_slot=<dev>|<drawer>|<connection> per local disk: the build
    # machine's capture list (gui_state) numbers its own disks the same way.
    drawer_slots_refresh || :
    for _dsr_d in $(list_disks); do
        _dsr_p=$(disk_port "$_dsr_d"); case "$_dsr_p" in ''|*[!0-9]*) _dsr_p=0 ;; esac
        printf 'disk_slot=%s|%s\n' "$_dsr_d" "$(disk_drawer "$_dsr_d" "$_dsr_p")"
    done
}
