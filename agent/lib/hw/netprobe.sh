# netprobe.sh -- cable (ethtool) + switch/port (LLDP) in hello (#1048). POSIX sh.
# Three states (principle 5): measured | null (tool absent) | {"error"} (tried, failed).
# SYSFS_ROOT default /sys; SYSROOT prefixes it in tests. ETHTOOL=/path, LLDPSNIFF=/path.

ETHTOOL="${ETHTOOL:-ethtool}"; LLDPSNIFF="${LLDPSNIFF:-imagesrv-lldpsniff}"
SYSFS_ROOT="${SYSFS_ROOT:-${SYSROOT:-}/sys}"
_np_pe() { printf '{"error":"%s"}' "$(json_escape "$1")"; }
# Host tools on a fake sysfs are not the initramfs: explicit path only.
_np_have() {
    command -v "$1" >/dev/null 2>&1 || return 1
    [ -z "${SYSROOT:-}" ] || case "$1" in /*|*/*) true ;; *) false ;; esac
}
_np_iface() {
    if [ -n "${IFACE:-}" ]; then printf '%s' "$IFACE"; return; fi
    _d="$SYSFS_ROOT/class/net"; [ -d "$_d" ] || return 1
    for _p in "$_d"/*; do
        [ -e "$_p" ] || continue; _n=${_p##*/}; [ "$_n" = lo ] && continue
        _car=$(cat "$_p/carrier" 2>/dev/null) || continue
        [ "$_car" = 1 ] && { printf '%s' "$_n"; return; }
    done
    return 1
}

# ethtool --cable-test output: "Pair A code OK/Open/Short, length Xm".
_np_parse_cable() {
    awk '
    function emit() {
        if (pair == "") return
        if (pairs != "") pairs = pairs ","
        pairs = pairs sprintf("{\"pair\":\"%s\",\"code\":%s,\"length_m\":%s}",
            pair, (code == "" ? "null" : "\"" code "\""), (len == "" ? "null" : len))
        if (code == "Short") st = "short"
        else if (code == "Open" && st != "short") st = "open"
        else if (code == "OK" && st == "unknown") st = "ok"
        pair = ""; code = ""; len = ""
    }
    BEGIN { st = "unknown" }
    {
        p = ""
        if (match($0, /Pair [A-Da-d]/)) p = toupper(substr($0, RSTART + 5, 1))
        if (p != "" && pair != "" && p != pair) emit()
        if (p != "") pair = p
        if (pair == "") next
        if ($0 ~ /[Ss]hort/) code = "Short"
        else if ($0 ~ /[Oo]pen/) code = "Open"
        else if ($0 ~ /OK/) code = "OK"
        if (match($0, /[0-9][0-9.]*[ ]*[mM]/)) {
            l = substr($0, RSTART, RLENGTH); gsub(/[^0-9.]/, "", l)
            if (l != "") len = l + 0
        }
    }
    END { emit(); printf "{\"status\":\"%s\",\"pairs\":[%s]}", st, pairs }'
}

_np_cable_run() {
    # $1 = interface. The test itself: absent (null) | unsupported | failed | parsed.
    _np_have "$ETHTOOL" || { printf 'null'; return; }
    _raw=$("$ETHTOOL" --cable-test "$1" 2>&1); _rc=$?
    if [ "$_rc" != 0 ]; then   # #1968: a NIC without the test is "not checked", never ok
        case "$_raw" in *[Nn]ot\ supported*) printf '{"status":"unchecked","reason":"unsupported"}' ;;
            *) _np_pe "ethtool --cable-test rc $_rc" ;; esac
        return
    fi
    printf '%s' "$_raw" | _np_parse_cable
}

_np_cable() {
    # Never on a live link from here (the test drops it). #1968: a live link that
    # is slow or dropped is tested by cable_live_check (cablelive.sh) while the
    # cloner idles; its result is served instead of "skipped".
    _car=$(cat "$SYSFS_ROOT/class/net/$1/carrier" 2>/dev/null) || _car=""
    if [ "$_car" = 1 ]; then
        if [ -s "${RUN_DIR:-/nonexistent}/cable-live.json" ]; then cat "$RUN_DIR/cable-live.json"
        else printf '{"skipped":"link up"}'; fi
        return
    fi
    _np_cable_run "$1"
}

_np_lldp() {
    _np_have "$LLDPSNIFF" || { printf 'null'; return; }
    _out=$("$LLDPSNIFF" "$1" "${LLDP_WAIT:-35}" 2>/dev/null); _rc=$?
    [ -n "$_out" ] || { _np_pe "lldpsniff rc $_rc"; return; }
    printf '%s' "$_out"
}

_netprobe_fresh() {
    _if=$(_np_iface) || _if=""
    if [ -z "$_if" ]; then
        printf ',"netprobe":{"cable":null,"lldp":null,"report":null}'; return
    fi
    # #2234: "report" = the layers up to the server (netreport.sh); absent loader = not checked
    _rep=null
    if command -v netreport_json >/dev/null 2>&1; then _rep=$(netreport_json "$_if" "${IMAGESRV_SERVER:-${SERVER:-}}"); [ -n "$_rep" ] || _rep=null; fi
    printf ',"netprobe":{"cable":%s,"lldp":%s,"report":%s}' "$(_np_cable "$_if")" "$(_np_lldp "$_if")" "$_rep"
}

# ,"netprobe":{...} — like probe_json. First hello must not wait 35s for LLDP:
# no cache → pending + background; later hellos serve $RUN_DIR/netprobe.json. #3632 (netcache.sh): ONE job at a
# time, and a cache that is not a whole fragment is dropped, never sent (a torn file made every hello 400 bad_json).
# #3652: its stamps are uptime, not `date` (netcache.sh).
netprobe_json() {
    _pc="${RUN_DIR:-/run/imagesrv}/netprobe.json"; _now=$(_np_mono) || _now=""
    if [ -d "${_pc%/*}" ]; then
        if [ -s "$_pc" ] && ! _np_whole "$_pc"; then
            log "netprobe: the cache is not whole JSON -- dropped, measured again" >&2; rm -f "$_pc" "$_pc.at"
        fi
        if [ -s "$_pc" ] && _age=$(_np_age "$_pc.at" "$_now") && [ "$_age" -lt "${NETPROBE_TTL:-900}" ]; then
            cat "$_pc"; return
        fi
        if [ -s "$_pc" ]; then cat "$_pc"; else printf ',"netprobe":{"pending":true}'; fi
        _np_bg "$_pc" "$_now"
        return
    fi
    _netprobe_fresh
}
