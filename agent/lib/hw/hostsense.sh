# hostsense.sh -- what the machine itself says, for the brain (#3064: S51 carrier, S60 PSI, S65 controller driver).
# POSIX sh (busybox ash). SYSROOT prefixes /sys and /proc in tests. Joined by sensecore.sh.
#
# Every function prints a JSON value, and `null` when it could not read -- never 0 for "not read":
#   ss_carrier_json DIR  {"iface","changes","delta"}  carrier_changes of the active NIC: now, and since this round's
#                        first tick. A link that dropped mid-wave explains a short stall the receiver counts as loss.
#   ss_psi_json DIR      {"samples","peak":{cpu_some,io_some,io_full,mem_some,mem_full}}  the kernel's pressure
#                        stall averages (avg10), the highest seen this round. Without CONFIG_PSI (no /proc/pressure) null.
#   ss_ctrl_json         [{"mode","driver"}]  per storage controller: the PCI class (ide/raid/ahci) and the driver
#                        bound to it ("none" = no driver took it). null = the PCI bus could not be read.
#   ss_sata_driver MODE  the driver of the first controller of that mode, for the hello (fwimage.sh); nothing if unread.

_ss_sysfs() { printf '%s' "${SYSFS_ROOT:-${SYSROOT:-}/sys}"; }

ss_carrier_json() {
    # $1 = the round's directory
    _hc_if=$(_np_iface 2>/dev/null) || { printf 'null'; return 0; }
    _hc_n=$(cat "$(_ss_sysfs)/class/net/$_hc_if/carrier_changes" 2>/dev/null)
    case "$_hc_n" in ''|*[!0-9]*) printf 'null'; return 0 ;; esac
    [ -s "$1/carrier0.$_hc_if" ] || echo "$_hc_n" > "$1/carrier0.$_hc_if"
    _hc_0=$(cat "$1/carrier0.$_hc_if" 2>/dev/null)
    case "$_hc_0" in ''|*[!0-9]*) printf '{"iface":"%s","changes":%s,"delta":null}' "$_hc_if" "$_hc_n"; return 0 ;; esac
    [ "$_hc_n" -ge "$_hc_0" ] || { printf '{"iface":"%s","changes":%s,"delta":null}' "$_hc_if" "$_hc_n"; return 0; }   # a counter that fell is a different NIC
    printf '{"iface":"%s","changes":%s,"delta":%s}' "$_hc_if" "$_hc_n" "$((_hc_n - _hc_0))"
}

ss_psi_json() {
    # $1 = the round's directory. Peaks are kept in psi.peak: "cpu_some io_some io_full mem_some mem_full samples".
    _hp_p="${SYSROOT:-}/proc/pressure"
    [ -r "$_hp_p/memory" ] && [ -r "$_hp_p/io" ] || { printf 'null'; return 0; }
    _hp_now=$(awk '
        FILENAME ~ /cpu$/    && $1 == "some" { split($2, a, "="); cs = a[2] }
        FILENAME ~ /io$/     && $1 == "some" { split($2, a, "="); is = a[2] }
        FILENAME ~ /io$/     && $1 == "full" { split($2, a, "="); iff = a[2] }
        FILENAME ~ /memory$/ && $1 == "some" { split($2, a, "="); ms = a[2] }
        FILENAME ~ /memory$/ && $1 == "full" { split($2, a, "="); mf = a[2] }
        END { if (is == "" || iff == "" || ms == "" || mf == "") exit 1
              printf "%s %s %s %s %s", (cs == "" ? "-" : cs), is, iff, ms, mf }' \
        "$_hp_p/cpu" "$_hp_p/io" "$_hp_p/memory" 2>/dev/null) || { printf 'null'; return 0; }   # an unreadable line is not 0
    printf '%s\n' "$_hp_now" > "$1/psi.now"
    {   cat "$1/psi.peak" 2>/dev/null
        cat "$1/psi.now"
    } | awk '
        NR == 1 && NF == 6 { for (i = 1; i <= 5; i++) m[i] = $i; n = $6; seen = 1; next }
        { n = (seen ? n : 0) + 1; seen = 1
          for (i = 1; i <= 5; i++) if ($i != "-" && ($i + 0 > m[i] + 0 || m[i] == "" || m[i] == "-")) m[i] = $i
          for (i = 1; i <= 5; i++) if (m[i] == "") m[i] = "-" }
        END { for (i = 1; i <= 5; i++) printf "%s ", m[i]; print n }' > "$1/psi.peak.new" 2>/dev/null &&
        mv "$1/psi.peak.new" "$1/psi.peak" || { printf 'null'; return 0; }
    awk 'function v(x) { return x == "-" ? "null" : x }
         { printf "{\"samples\":%d,\"peak\":{\"cpu_some\":%s,\"io_some\":%s,\"io_full\":%s,\"mem_some\":%s,\"mem_full\":%s}}", $6, v($1), v($2), v($3), v($4), v($5) }' \
        "$1/psi.peak"
}

_ss_ctrls() {   # "<mode> <driver>" per storage controller: IDE 0x0101, RAID 0x0104, SATA 0x0106 -- as fw_sata_mode reads them
    for _hs_d in "$(_ss_sysfs)"/bus/pci/devices/*; do
        [ -r "$_hs_d/class" ] || continue
        read -r _hs_c < "$_hs_d/class" || continue
        case "$_hs_c" in 0x0101??) _hs_m=ide ;; 0x0104??) _hs_m=raid ;; 0x0106??) _hs_m=ahci ;; *) continue ;; esac
        _hs_n=none
        if [ -e "$_hs_d/driver" ]; then
            _hs_n=$(basename "$(readlink -f "$_hs_d/driver" 2>/dev/null)")
            case "$_hs_n" in ''|*[!A-Za-z0-9_.-]*) _hs_n=unknown ;; esac
        fi
        printf '%s %s\n' "$_hs_m" "$_hs_n"
    done
}

ss_ctrl_json() {
    [ -d "$(_ss_sysfs)/bus/pci/devices" ] || { printf 'null'; return 0; }
    printf '['
    _ss_ctrls | awk '{ printf "%s{\"mode\":\"%s\",\"driver\":\"%s\"}", (n++ ? "," : ""), $1, $2 }'
    printf ']'
}

ss_sata_driver() {
    # $1 = the mode fw_sata_mode read. Its controller's driver: ahci for AHCI, ata_piix for an IDE-mode Intel chip.
    [ -n "${1:-}" ] || return 0
    _ss_ctrls | awk -v m="$1" '$1 == m { print $2; exit }'
}
