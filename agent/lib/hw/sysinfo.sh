# sysinfo.sh -- gathers hardware facts and builds the hello body
# (interfaces.md section 2). Pure sh string assembly, no jq needed.
# POSIX sh (busybox ash).
#
# Test hooks: SYSROOT prefixes /sys and /proc, DEVROOT prefixes /dev.
# In the real initramfs both are empty.

SYSROOT="${SYSROOT:-}"
DEVROOT="${DEVROOT:-/dev}"

detect_firmware() {
    [ -d "$SYSROOT/sys/firmware/efi" ] && echo "uefi" || echo "bios"
}

detect_secure_boot() {
    # EFI variable: 4 attribute bytes, then 1 data byte (1 = enabled).
    #
    # שלושה מצבים ולא שניים. למכונת BIOS אין Secure Boot, ולכן `false`
    # הוא נכון עליה. אבל מכונת UEFI שה-efivars שלה לא נקרא היא מכונה
    # שאיננו יודעים — ו-`false` שם הוא טענה חיובית שגויה, לא שדה חסר.
    # ככה מכונה שה-Secure Boot דלוק בה דיווחה "כבוי" חודשים: efivarfs
    # הוא מודול שלא נארז ב-initramfs, והמאונט נכשל בשקט (#84).
    #
    # `null` אומר "לא הצלחנו לבדוק". ההבדל בינו לבין `false` הוא ההבדל
    # בין השערה לידיעה.
    [ -d "$SYSROOT/sys/firmware/efi" ] || { echo "false"; return; }
    _f="$SYSROOT/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    [ -r "$_f" ] || { echo "null"; return; }
    _b=$(dd if="$_f" bs=1 skip=4 count=1 2>/dev/null | od -An -tu1 | tr -d ' \n')
    case "$_b" in
        1) echo "true" ;;
        0) echo "false" ;;
        *) echo "null" ;;   # נקרא ולא הובן — עדיין לא ידוע
    esac
}

detect_uuid() {
    trim "$(cat "$SYSROOT/sys/class/dmi/id/product_uuid" 2>/dev/null)"
}

detect_memory_bytes() {
    # MemTotal is in kB.
    awk '/^MemTotal:/ { printf "%.0f", $2 * 1024; exit }' "$SYSROOT/proc/meminfo"
}

list_macs() {
    # All network interfaces except loopback, lowercase-with-colons --
    # the canonical format. Matters for machines with more than one NIC.
    for _d in "$SYSROOT"/sys/class/net/*; do
        [ -e "$_d" ] || continue
        _n=$(basename "$_d")
        [ "$_n" = "lo" ] && continue
        _a=$(cat "$_d/address" 2>/dev/null)
        [ -n "$_a" ] && [ "$_a" != "00:00:00:00:00:00" ] && echo "$_a"
    done
}

build_hello() {
    # Emits the full hello body. Needs IFACE and IP in the environment
    # (init writes them after DHCP).
    #
    # $1 = "false" for a heartbeat hello: the machine says it is still alive
    # but does NOT ask to join an open session. A machine holding an error
    # screen has to stay visible without being counted into the next wave
    # (#64). Any other value -- including none -- is a normal hello.
    _mac=$(cat "$SYSROOT/sys/class/net/$IFACE/address" 2>/dev/null)
    [ -n "$_mac" ] || return 1

    _all=""
    for _m in $(list_macs); do
        [ -n "$_all" ] && _all="$_all,"
        _all="$_all\"$_m\""
    done

    _disks=""
    for _n in $(list_disks); do
        [ -n "$_disks" ] && _disks="$_disks,"
        _disks="$_disks$(build_disk_entry "$_n")"
    done

    _joining=true
    [ "$1" = "false" ] && _joining=false

    # UUID חסר ב-DMI הוא `null`, לא `""` — כמו serial/port (#524).
    _uuid=$(detect_uuid)
    _uuid_json="null"
    [ -n "$_uuid" ] && _uuid_json="\"$(json_escape "$_uuid")\""

    # #839: the boot secret for the RFB monitor (monitor.sh). Absent when
    # nobody can draw it -- absent, not empty: the server keeps the previous
    # secret on a missing field and ignores a malformed one.
    _msecret=""
    if command -v monitor_secret >/dev/null 2>&1; then
        _ms=$(monitor_secret) && [ -n "$_ms" ] &&
            _msecret=",\"monitor_secret\":\"$_ms\",\"monitor_auth\":\"hmac\""
    fi

    # #720+#1049+#1048: inventory, probe, netprobe fragments (each with a leading comma).
    _wol=""; [ "$(cat "$RUN_DIR/wol_armed" 2>/dev/null)" = false ] && _wol=',"wol_armed":false'
    printf '{"schema":2,"mac":"%s","all_macs":[%s],"ip":"%s","uuid":%s,"firmware":"%s","secure_boot":%s,"agent_version":"%s","memory_bytes":%s,"joining":%s,"disks":[%s],"disk_probe":"%s"%s%s%s}' \
        "$_mac" "$_all" "$IP" "$_uuid_json" \
        "$(detect_firmware)" "$(detect_secure_boot)" "$AGENT_VERSION" \
        "$(detect_memory_bytes)" "$_joining" "$_disks" "$(disk_probe)" \
        "$_msecret" "$(command -v inventory_json >/dev/null 2>&1 && inventory_json)$(command -v probe_json >/dev/null 2>&1 && probe_json)$(command -v netprobe_json >/dev/null 2>&1 && netprobe_json)$(command -v optical_json >/dev/null 2>&1 && optical_json)$(command -v sata_lpm_json >/dev/null 2>&1 && sata_lpm_json)$(command -v timing_json >/dev/null 2>&1 && timing_json)$(command -v firmware_json >/dev/null 2>&1 && firmware_json)" "$_wol$(cat "$RUN_DIR/direct.finished" 2>/dev/null)"   # #1952: direct_hello's fragment; absent = not reported
}
