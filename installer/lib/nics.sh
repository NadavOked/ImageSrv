# nics.sh -- the install GUI's NIC rows (gui-bridge.sh inventory). POSIX sh.
# shellcheck disable=SC2154 # sourced by gui-bridge.sh: emit, SYSROOT and DHCP_DIR are its own
#
# Split from gui-bridge.sh before its 300-line wall (#2144 added the clock),
# without a change of behaviour. Defines functions only; the bridge calls nic_rows.

# --- NIC rows: name|model|mac|link|current|source|address|netmask|gateway|dns --
nic_model() {   # #2096: a readable name, never "VMware 0x07b0"; unknown = empty -> "כרטיס רשת N" on screen
    _nm_dev="$SYSROOT/sys/class/net/$1/device"
    [ -r "$_nm_dev/vendor" ] && [ -r "$_nm_dev/device" ] || return 0
    _nm_v=$(cat "$_nm_dev/vendor"); _nm_d=$(cat "$_nm_dev/device")
    case "$_nm_v:$_nm_d" in
        0x15ad:0x07b0) printf 'VMware VMXNET3' ;; 0x15ad:*) printf 'VMware' ;; 0x1414:*) printf 'Hyper-V' ;;
        0x8086:0x100e|0x8086:0x100f) printf 'Intel E1000' ;; 0x8086:0x10d3) printf 'Intel 82574L' ;; 0x8086:*) printf 'Intel' ;;
        0x10ec:0x8168) printf 'Realtek RTL8111' ;; 0x10ec:0x8125) printf 'Realtek RTL8125' ;; 0x10ec:*) printf 'Realtek' ;;
        0x14e4:*) printf 'Broadcom' ;; 0x1af4:*) printf 'virtio' ;;
    esac
}

nic_rows() {
    # C-NET-01/02: display only. An unreadable sysfs value or a failed `ip` shows as empty/unknown; nothing decides from it.
    for _d in "$SYSROOT"/sys/class/net/*; do
        _n=${_d##*/}; [ "$_n" = lo ] && continue
        _mac=$(cat "$_d/address" 2>/dev/null || printf '')
        _oper=$(cat "$_d/operstate" 2>/dev/null || printf 'unknown')
        _carrier=$(cat "$_d/carrier" 2>/dev/null || printf '')
        # #1183 wording: down = not brought up, no carrier = no cable, else connected
        if [ "$_oper" = down ]; then _link='לא מודלק'
        elif [ "$_carrier" = 1 ]; then _link='מחובר'
        else _link='אין קישור'; fi
        _addr=; _gw=; _dns=; _state=
        if [ -r "$DHCP_DIR/$_n" ]; then
            _state=$(sed -n 's/^state=//p' "$DHCP_DIR/$_n" | head -n1)
            _addr=$(sed -n 's/^address=//p' "$DHCP_DIR/$_n" | head -n1)
            _gw=$(sed -n 's/^gateway=//p' "$DHCP_DIR/$_n" | head -n1)
            _dns=$(sed -n 's/^dns=//p' "$DHCP_DIR/$_n" | head -n1)
        fi
        if [ -z "$_addr" ]; then
            _addr=$(ip -o -4 addr show dev "$_n" scope global 2>/dev/null | sed -n 's/.* inet \([0-9./]*\).*/\1/p' | head -n1)
        fi
        case "$_state" in
            lease) _current=$_addr; _source="DHCP · שער $_gw" ;;
            no-offer) _current='—'; _source='אין כתובת (אין DHCP ברשת הזו)' ;;
            no-carrier) _current='—'; _source='—' ;;
            *) if [ -n "$_addr" ]; then _current=$_addr; _source='כתובת קיימת'; else _current='—'; _source='—'; fi ;;
        esac
        _ip=${_addr%%/*}; _mask=
        case "$_addr" in */24) _mask=255.255.255.0 ;; */16) _mask=255.255.0.0 ;; */8) _mask=255.0.0.0 ;; */22) _mask=255.255.252.0 ;; */23) _mask=255.255.254.0 ;; */25) _mask=255.255.255.128 ;; esac
        emit nic "$_n|$(nic_model "$_n")|$_mac|$_link|$_current|$_source|$_ip|$_mask|$_gw|$_dns"
    done
}
