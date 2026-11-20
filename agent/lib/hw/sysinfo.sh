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
    _b=$(dd if="$_f" bs=1 skip=4 count=1 2>/dev/null | od -An -tu1 | tr -d ' \n')   # B-HW-05: a failed read is empty -> null below
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

_nl_iface_ok() { case "${IFACE:-}" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac; }

nic_speed_mbps() {
    # #3277: the distribution card's link speed, read NOW -- one sysfs file, outside the probe's 600 s cache
    # (probe_bg.sh PROBE_TTL), so a link that renegotiated to 100 Mb/s after the first hello is seen by the next one.
    # Prints N, or nothing when it cannot be read (the link is down: "-1"; a driver without speed; a missing file) --
    # never 0 and never the last good value.
    _nl_iface_ok || return 0
    _nl_sp=$(cat "$SYSROOT/sys/class/net/$IFACE/speed" 2>/dev/null) || return 0
    case "$_nl_sp" in ''|0|*[!0-9]*) ;; *) printf '%s' "$_nl_sp" ;; esac
}

nic_max_mbps() {
    # The highest speed the card says it supports (`ethtool`, "Supported link modes"), cached for the boot in
    # $RUN_DIR/nic.max: the card does not change, and ethtool is not run on every hello. Nothing when it cannot be read
    # (no ethtool, no link modes) -- the banner then knows only the "100 Mb/s is always a bottleneck" half.
    _nl_iface_ok || return 0
    _nl_c="${RUN_DIR:-/nonexistent}/nic.max"
    if [ -f "$_nl_c" ]; then cat "$_nl_c" 2>/dev/null; return 0; fi
    _nl_m=$("${ETHTOOL:-ethtool}" "$IFACE" 2>/dev/null | awk '
        /Supported link modes:/ { on = 1 }
        /Supported pause frame|Supported FEC|Advertised link modes:/ { on = 0 }
        on { while (match($0, /[0-9]+base/)) { n = substr($0, RSTART, RLENGTH - 4) + 0; if (n > m + 0) m = n; $0 = substr($0, RSTART + RLENGTH) } }
        END { if (m + 0 > 0) print m }')
    case "$_nl_m" in ''|*[!0-9]*) _nl_m= ;; esac
    { printf '%s' "$_nl_m" > "$_nl_c.new" && mv "$_nl_c.new" "$_nl_c"; } 2>/dev/null || :
    printf '%s' "$_nl_m"
}

nic_carrier_json() {
    # #3663 (A10): the facts the brain judges "the link dropped" from -- carrier_changes now, and the first value this boot read
    # ($RUN_DIR/carrier0.<if>, written by the first hello: the count is >= 1 from boot, so a rise over it is a drop, not the boot).
    # "null,null" when the counter cannot be read; a first value that cannot be stored is null too: the brain says "not measured",
    # never "no drop" (principle 5).
    _nc_n=$(cat "$SYSROOT/sys/class/net/$IFACE/carrier_changes" 2>/dev/null)
    case "$_nc_n" in ''|*[!0-9]*) printf 'null,null'; return 0 ;; esac
    _nc_f="${RUN_DIR:-/nonexistent}/carrier0.$IFACE"
    [ -s "$_nc_f" ] || ( echo "$_nc_n" > "$_nc_f" ) 2>/dev/null
    _nc_0=$(cat "$_nc_f" 2>/dev/null)
    case "$_nc_0" in ''|*[!0-9]*) _nc_0=null ;; esac
    printf '%s,%s' "$_nc_n" "$_nc_0"
}

nic_link_json() {
    # ,"nic_link":{"iface":..,"speed_mbps":N,"max_mbps":M,"carrier_changes":C,"carrier_first":F,"cable_tested":bool}; each number
    # null when it cannot be read. No IFACE: no field (the server: unknown, never 1000). #3663: carrier_* and cable_tested are FACTS
    # for the brain (linkverdict.py, K-CABLE: whether to run the cable test); this decides nothing.
    _nl_iface_ok || return 0
    _nl_a=$(nic_speed_mbps); _nl_b=$(nic_max_mbps); _nl_t=false; _nl_c=$(nic_carrier_json)
    [ -e "${RUN_DIR:-/nonexistent}/cable-live.json" ] && _nl_t=true
    printf ',"nic_link":{"iface":"%s","speed_mbps":%s,"max_mbps":%s,"carrier_changes":%s,"carrier_first":%s,"cable_tested":%s}'         "$IFACE" "${_nl_a:-null}" "${_nl_b:-null}" "${_nl_c%,*}" "${_nl_c#*,}" "$_nl_t"
}

nic_banner_flag() {
    # #3663 (G2): the brain's verdict from the last hello answer (link_verdict.banner, K-NICBANNER). 1 = SHOW; 0 = it said no (NONE) or could
    # not tell (NOT_MEASURED); EMPTY = no verdict (an old server, a rule that failed, an answer not read): the screen draws no banner.
    case "$(jq -r '.link_verdict.banner // empty' "${RUN_DIR:-/nonexistent}/response.json" 2>/dev/null)" in
        SHOW) printf 1 ;; NONE|NOT_MEASURED) printf 0 ;; esac
}

nic_bus_banner_record() {
    # #3776: the cloner screen's nic_bus=<flag> -- the brain's bus verdict from the last hello answer (link_verdict.bus_banner, K-NICBUS). 1 = SHOW; anything else -- NONE,
    # NOT_MEASURED, no verdict at all (an old server, a rule that failed) -- is 0: the screen draws nothing and holds no threshold.
    # A record of its own, so nic_link=... keeps its exact shape (cloner_nic_link in clonergui.sh prints both).
    [ "$(jq -r '.link_verdict.bus_banner // empty' "${RUN_DIR:-/nonexistent}/response.json" 2>/dev/null)" = SHOW ] && printf 'nic_bus=1\n' || printf 'nic_bus=0\n'
}

nic_link_record() {
    # #3277: the cloner screen's record, nic_link=<iface>|<speed>|<max>|<flag> (empty = not read). Written every tick by
    # cloner_gui_state. The three numbers are the card's own reading, for the banner's text; the flag (#3663) is the brain's
    # verdict, and the only thing that decides whether the banner is drawn -- the screen has no threshold of its own.
    # #3644: no usable IFACE in this process = rc 1 and nothing printed (it was rc 0, the same silence as "no banner needed");
    # cloner_nic_link (clonergui.sh) turns that into a log line.
    _nl_iface_ok || return 1
    printf 'nic_link=%s|%s|%s|%s\n' "$IFACE" "$(nic_speed_mbps)" "$(nic_max_mbps)" "$(nic_banner_flag)"
}

# A PCI status byte reports 66 MHz capability, never the negotiated clock or width.
_pci_66_capable() {
    byte=$(od -An -tu1 -j "$2" -N 1 "$1" 2>/dev/null)
    byte=${byte#"${byte%%[![:space:]]*}"}
    case "$byte" in ''|255) printf null ;; *) [ "$((byte & 32))" -eq 0 ] && printf false || printf true ;; esac
}

nic_bus_record() (
    dev="$SYSROOT/sys/class/net/${IFACE:-}/device"
    if ! _nl_iface_ok || ! cd -P "$dev" 2>/dev/null; then
        printf '{"bus":"unknown"}'
        return
    fi
    for field in current_link_speed current_link_width max_link_speed max_link_width; do
        [ -e "$field" ] || continue
        printf '{"bus":"pcie"'
        for attr in current_link_speed current_link_width max_link_speed max_link_width; do
            value=$(awk -v attr="$attr" '
                /./ && attr ~ /speed$/ && $1 ~ /^[1-9][0-9]*(\.[0-9]+)?$/ && $2 == "GT/s" {print $1; exit}
                /^[1-9][0-9]*$/ && attr ~ /width$/ {print $1; exit}
            ' "$attr" 2>/dev/null)
            printf ',"%s":%s' "$attr" "${value:-null}"
        done
        printf '}'
        return
    done
    # Match this exact device in the PCI subsystem. A USB or virtual NIC can
    # also lack all four link attributes.
    pci="$SYSROOT/sys/bus/pci/devices/${PWD##*/}"
    if [ "$(cd -P "$pci" 2>/dev/null && pwd -P)" != "$PWD" ]; then
        printf '{"bus":"unknown"}'
        return
    fi
    bridge=null
    header=$(od -An -tu1 -j 14 -N 1 ../config 2>/dev/null)
    case $((${header:-0} & 127)) in 1) bridge=$(_pci_66_capable ../config 30) ;; esac
    printf '{"bus":"pci","capable_66mhz":%s,"bridge_capable_66mhz":%s}' \
        "$(_pci_66_capable "$pci/config" 6)" "$bridge"
)

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

    # #2844: did THIS boot start the monitor service? Only the kernel line
    # decides (common.sh), so a machine that came up before the console
    # switch went on says false -- the server can then tell that apart from
    # a service that is on and refusing.
    _mon=false; [ "${IMAGESRV_MONITOR:-0}" = 1 ] && _mon=true
    _msecret="$_msecret,\"monitor_on\":$_mon"

    # #720+#1049+#1048: inventory, probe, netprobe fragments (each with a leading comma).
    _wol=""; [ "$(cat "$RUN_DIR/wol_armed" 2>/dev/null)" = false ] && _wol=',"wol_armed":false'
    if command -v chunkcast >/dev/null 2>&1; then _wol="$_wol,\"engines\":[\"udpcast\",\"chunk\"]"; fi   # #2533: the stream engines this image can receive
    # #2528: the kernel's id of THIS boot. The server keeps the one that was handed a wave; a
    # joining hello with another id mid-stream is a machine that was powered off or rebooted.
    # Unreadable or not in the kernel's form = the field is absent ("not known"), never invented.
    _boot=$(cat "$SYSROOT/proc/sys/kernel/random/boot_id" 2>/dev/null)
    case "$_boot" in
        ''|*[!0-9a-f-]*) _boot="" ;;
        *) if [ "${#_boot}" -eq 36 ]; then _boot=",\"boot_id\":\"$_boot\""; else _boot=""; fi ;;
    esac
    _boot="$_boot$(nic_link_json)"   # #3277: with the boot id: both say what THIS machine is, now
    _boot="$_boot,\"nic_bus\":$(nic_bus_record)"
    printf '{"schema":2,"mac":"%s","all_macs":[%s],"ip":"%s","uuid":%s,"firmware":"%s","secure_boot":%s,"agent_version":"%s","memory_bytes":%s,"joining":%s,"disks":[%s],"disk_probe":"%s"%s%s%s}' \
        "$_mac" "$_all" "$IP" "$_uuid_json" \
        "$(detect_firmware)" "$(detect_secure_boot)" "$AGENT_VERSION" \
        "$(detect_memory_bytes)" "$_joining" "$_disks" "$(disk_probe)" \
        "$_msecret" "$(command -v inventory_json >/dev/null 2>&1 && inventory_json)$(command -v probe_json >/dev/null 2>&1 && probe_json)$(command -v netprobe_json >/dev/null 2>&1 && netprobe_json)$(command -v netwatch_json >/dev/null 2>&1 && netwatch_json)$(command -v optical_json >/dev/null 2>&1 && optical_json)$(command -v sata_lpm_json >/dev/null 2>&1 && sata_lpm_json)$(command -v timing_json >/dev/null 2>&1 && timing_json)$(command -v firmware_json >/dev/null 2>&1 && firmware_json)$(command -v idle_wc_report_json >/dev/null 2>&1 && idle_wc_report_json)" "$_wol$_boot$(cat "$RUN_DIR/direct.finished" 2>/dev/null)"   # #1952: direct_hello's fragment; absent = not reported
}
