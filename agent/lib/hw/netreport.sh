# netreport.sh -- the layers between this machine and the server (#2234). POSIX sh.
# Rides inside the netprobe object ("report"): same background job, same cache.
# Every layer is one of three states (principle 5): measured | null = not
# checked (never "ok") | {"error": "<why>"} = tried and failed to look.
# $1 = interface, $2 = the server URL. Tools: PING, NSLOOKUP, CURL, IPCMD
# (explicit path only under SYSROOT, like netprobe.sh); RESOLV_CONF.

PING="${PING:-ping}"; NSLOOKUP="${NSLOOKUP:-nslookup}"; CURL="${CURL:-curl}"; IPCMD="${IPCMD:-ip}"
RESOLV_CONF="${RESOLV_CONF:-${SYSROOT:-}/etc/resolv.conf}"

# http://host:port/... -> "host port" (port 80 when none). Anything else -> failure.
_nr_hostport() {
    _u=${1#http://}; _u=${_u%%/*}
    case "$_u" in ''|*://*|*@*) return 1 ;; esac
    case "$_u" in *:*) _h=${_u%%:*}; _p=${_u##*:} ;; *) _h=$_u; _p=80 ;; esac
    case "$_p" in ''|*[!0-9]*) return 1 ;; esac
    [ -n "$_h" ] || return 1
    printf '%s %s' "$_h" "$_p"
}
_nr_is_ipv4() { case "$1" in *[!0-9.]*|'') return 1 ;; *.*.*.*) return 0 ;; *) return 1 ;; esac; }

# The DHCP server that answered this NIC: the file agent/init's probe hook wrote.
_nr_dhcp() {
    _f="${RUN_DIR:-/run/imagesrv}/dhcp-probe.$1"
    [ -r "$_f" ] || { printf 'null'; return; }
    _sid=$(sed -n 's/^serverid=//p' "$_f" 2>/dev/null | head -n 1)
    [ -n "$_sid" ] || { printf 'null'; return; }
    printf '{"server_id":"%s"}' "$(json_escape "$_sid")"
}

_nr_gateway() {
    _np_have "$IPCMD" || { printf 'null'; return; }
    _rt=$("$IPCMD" -4 route show default dev "$1" 2>/dev/null); _rc=$?
    [ "$_rc" = 0 ] || { _np_pe "ip route rc $_rc"; return; }
    _gw=$(printf '%s\n' "$_rt" | sed -n 's/^default via \([0-9.]*\).*/\1/p' | head -n 1)
    [ -n "$_gw" ] || { printf '{"addr":null,"reachable":null}'; return; }   # no default route: a fact, not a failure
    _np_have "$PING" || { printf '{"addr":"%s","reachable":null}' "$_gw"; return; }
    if "$PING" -c 1 -W 1 "$_gw" >/dev/null 2>&1; then _ok=true; else _ok=false; fi   # ICMP may be filtered: the card says "does not answer ping"
    printf '{"addr":"%s","reachable":%s}' "$_gw" "$_ok"
}

_nr_dns() {
    [ -r "$RESOLV_CONF" ] || { printf 'null'; return; }
    _srv=$(sed -n 's/^nameserver[ \t]*\([0-9.]*\).*/\1/p' "$RESOLV_CONF" 2>/dev/null)
    _list=""; for _s in $_srv; do _list="${_list:+$_list,}\"$_s\""; done
    _res=null
    # A name is looked up only when the server is given by name; an address needs no DNS.
    if [ -n "$_srv" ] && _hp=$(_nr_hostport "$2") && ! _nr_is_ipv4 "${_hp% *}" && _np_have "$NSLOOKUP"; then
        _ns1=$(printf '%s\n' "$_srv" | head -n 1)
        if "$NSLOOKUP" "${_hp% *}" "$_ns1" >/dev/null 2>&1; then _res=true; else _res=false; fi
    fi
    printf '{"servers":[%s],"resolves":%s}' "$_list" "$_res"
}

# TCP to the server's port: any HTTP status is a connection that opened; "000" and a
# failed curl are not -- the evidence is the code, never the absence of an error.
_nr_tcp() {
    _hp=$(_nr_hostport "$1") || { printf 'null'; return; }
    _np_have "$CURL" || { printf 'null'; return; }
    _code=$("$CURL" -s -o /dev/null --connect-timeout 3 -m 5 -w '%{http_code}' "http://${_hp% *}:${_hp#* }/" 2>/dev/null); _rc=$?
    case "$_code" in [1-5][0-9][0-9]) _ok=true ;; *) _ok=false ;; esac
    printf '[{"host":"%s","port":%s,"ok":%s,"curl_rc":%s}]' "$(json_escape "${_hp% *}")" "${_hp#* }" "$_ok" "$_rc"
}

netreport_json() {
    printf '{"dhcp":%s,"gateway":%s,"dns":%s,"tcp":%s}' \
        "$(_nr_dhcp "$1")" "$(_nr_gateway "$1")" "$(_nr_dns "$1" "$2")" "$(_nr_tcp "$2")"
}
