# inventory.sh -- the hardware inventory the hello carries for driver
# matching (#720; interfaces.md section 2, hello schema 2): the DMI
# identity, the PCI IDs of the network and storage functions, and the TPM.
# POSIX sh (busybox ash). Reads sysfs only -- SYSROOT prefixes it in tests.
#
# Why PCI IDs and not just the model name: FOG matches drivers by the
# SMBIOS model string, and every vendor puts that string in a different
# field (Lenovo: product_version, Dell: product_name, Intel: board_name).
# A vendor:device pair is what the driver's INF itself matches on, and it
# does not depend on how the firmware spells the model.

_dmi_json_field() {
    # $1 = file under /sys/class/dmi/id. A JSON string, or null when the
    # firmware left it empty -- null, not "": an empty vendor is no vendor.
    _v=$(trim "$(cat "$SYSROOT/sys/class/dmi/id/$1" 2>/dev/null)")
    if [ -n "$_v" ]; then printf '"%s"' "$(json_escape "$_v")"; else printf 'null'; fi
}

inventory_dmi() {
    printf '{"sys_vendor":%s,"product_name":%s,"product_version":%s,"board_name":%s}' \
        "$(_dmi_json_field sys_vendor)" "$(_dmi_json_field product_name)" \
        "$(_dmi_json_field product_version)" "$(_dmi_json_field board_name)"
}

_pci_hex() {
    # $1 = a sysfs attribute holding 0x8086 / 0x020000. Prints lowercase hex
    # without the prefix; prints nothing and fails on anything that is not
    # hex -- a device whose ID cannot be read is left out, not made up.
    _h=$(tr -d '\n\r ' < "$1" 2>/dev/null | tr 'A-F' 'a-f')
    _h=${_h#0x}
    case "$_h" in ''|*[!0-9a-f]*) return 1 ;; esac
    printf '%s' "$_h"
}

inventory_pci() {
    # One vendor:device:class per line for every network (class 02xxxx) and
    # storage (class 01xxxx) function, in sysfs order (= bus address). Only
    # those two classes: they are the ones a restored Windows may lack a
    # driver for and then be unreachable; GPU/audio are not in the MVP.
    for _d in "$SYSROOT"/sys/bus/pci/devices/*; do
        [ -e "$_d" ] || continue
        _c=$(_pci_hex "$_d/class") || continue
        case "$_c" in 01*|02*) ;; *) continue ;; esac
        _v=$(_pci_hex "$_d/vendor") || continue
        _i=$(_pci_hex "$_d/device") || continue
        printf '%s:%s:%s\n' "$_v" "$_i" "$_c"
    done
}

inventory_tpm() {
    # Three states, not two (עיקרון 5): null = could not look (no
    # /sys/class/tpm at all -- the tpm modules are not in this initramfs);
    # {"present":false} = looked and there is none; present with the
    # version the kernel reports (tpm_version_major: 2 -> "2.0", 1 -> "1.2").
    _t="$SYSROOT/sys/class/tpm"
    [ -d "$_t" ] || { printf 'null'; return; }
    [ -e "$_t/tpm0" ] || { printf '{"present":false}'; return; }
    _maj=$(tr -dc '0-9' < "$_t/tpm0/tpm_version_major" 2>/dev/null)
    case "$_maj" in
        2) printf '{"present":true,"version":"2.0"}' ;;
        1) printf '{"present":true,"version":"1.2"}' ;;
        *) printf '{"present":true,"version":null}' ;;   # there, version unread
    esac
}

inventory_json() {
    # Prints ,"inventory":{...} -- the fragment build_hello (sysinfo.sh)
    # appends. The leading comma is here so that sysinfo.sh, which sits at
    # the 280-line lamp, adds no line of its own for the field.
    _pci=""
    for _p in $(inventory_pci); do
        [ -n "$_pci" ] && _pci="$_pci,"
        _pci="$_pci\"$_p\""
    done
    printf ',"inventory":{"dmi":%s,"pci":[%s],"tpm":%s}' \
        "$(inventory_dmi)" "$_pci" "$(inventory_tpm)"
}
