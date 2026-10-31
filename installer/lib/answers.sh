# answers.sh -- strict GUI answer-file parser and preflight. POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module publishes validated answers

answer_set() {
    _as_key=$1; _as_value=$2
    case " $ANSWER_KEYS " in *" $_as_key "*) fail duplicate-key "duplicate answer: $_as_key" ;; esac
    ANSWER_KEYS="$ANSWER_KEYS $_as_key"
    case "$_as_key" in
        disk) ANSWER_DISK=$_as_value ;; role) ANSWER_ROLE=$_as_value ;;
        primary_url) ANSWER_PRIMARY_URL=$_as_value ;; servers_if) ANSWER_SERVERS_IF=$_as_value ;;
        servers_mac) ANSWER_SERVERS_MAC=$_as_value ;; servers_mode) ANSWER_SERVERS_MODE=$_as_value ;;
        servers_addr) ANSWER_SERVERS_ADDR=$_as_value ;; servers_mask) ANSWER_SERVERS_MASK=$_as_value ;;
        servers_gw) ANSWER_SERVERS_GW=$_as_value ;; servers_dns) ANSWER_SERVERS_DNS=$_as_value ;;
        hostname) ANSWER_HOSTNAME=$_as_value ;; admin_pass) ANSWER_ADMIN_PASS=$_as_value ;;
        admin_user) ANSWER_ADMIN_USER=$_as_value ;; timezone) ANSWER_TIMEZONE=$_as_value ;;
        primary_fingerprint) ANSWER_PRIMARY_FINGERPRINT=$_as_value ;;
        raid_level) ANSWER_RAID_LEVEL=$_as_value ;; raid_disks) ANSWER_RAID_DISKS=$_as_value ;;
        raid_spares) ANSWER_RAID_SPARES=$_as_value ;;   # #1989: optional, absent = no RAID
        root_pass) ANSWER_ROOT_PASS=$_as_value ;;   # #1632: the operator's own, from the admin screen
        *) fail unknown-key "unknown answer: $_as_key" ;;
    esac
}

parse_answers() {
    _pa_file=$1; [ -r "$_pa_file" ] || fail answers-unreadable "cannot read answers file: $_pa_file"
    ANSWER_KEYS=; ANSWER_RAID_LEVEL=none; ANSWER_RAID_DISKS=; ANSWER_RAID_SPARES=0
    while IFS= read -r _pa_line || [ -n "$_pa_line" ]; do
        case "$_pa_line" in *=*) ;; *) fail invalid-line "answer line has no '='" ;; esac
        _pa_key=${_pa_line%%=*}; _pa_value=${_pa_line#*=}
        case "$_pa_key" in ''|*[!a-z_]*) fail invalid-key "invalid answer key: $_pa_key" ;; esac
        answer_set "$_pa_key" "$_pa_value"
    done < "$_pa_file"
    for _pa_key in disk role primary_url primary_fingerprint servers_if servers_mac servers_mode \
        servers_addr servers_mask servers_gw servers_dns hostname timezone admin_user admin_pass root_pass; do
        case " $ANSWER_KEYS " in *" $_pa_key "*) ;; *) fail missing-key "missing answer: $_pa_key" ;; esac
    done
}

# #1310: the primary's address as the person types it -- an IP or a name,
# optionally HOST:8443 or https://HOST:8443[/] -- printed in the one stored
# form, https://HOST:8443. Anything else (http://, another port, a path,
# a space inside) is refused, not "fixed": the inter-site port is 8443.
normalize_primary_url() {
    _np=$1
    while :; do case "$_np" in ' '*) _np=${_np# } ;; *' ') _np=${_np% } ;; *) break ;; esac; done
    _np=${_np#https://}; _np=${_np%/}; _np=${_np%:8443}
    case "$_np" in ''|*[!A-Za-z0-9.-]*|.*|-*) return 1 ;; esac
    printf 'https://%s:8443' "$_np"
}

valid_ipv4() {
    _vi_oldifs=$IFS; IFS=.; read -r _vi_a _vi_b _vi_c _vi_d _vi_extra <<EOF
$1
EOF
    IFS=$_vi_oldifs
    [ -z "$_vi_extra" ] || return 1
    for _vi_octet in "$_vi_a" "$_vi_b" "$_vi_c" "$_vi_d"; do
        case "$_vi_octet" in ''|*[!0-9]*) return 1 ;; esac
        [ "$_vi_octet" -le 255 ] || return 1
    done
}

valid_dns_list() {
    _vd_rest=$1
    while :; do
        case "$_vd_rest" in *,*) _vd_one=${_vd_rest%%,*}; _vd_rest=${_vd_rest#*,} ;;
            *) _vd_one=$_vd_rest; _vd_rest= ;; esac
        valid_ipv4 "$_vd_one" || return 1
        [ -n "$_vd_rest" ] || return 0
    done
}

valid_mac() {
    case "$1" in
        [0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]) return 0 ;;
    esac
    return 1
}

validate_answers() {
    case "$ANSWER_DISK" in /dev/*) DISK_NAME=${ANSWER_DISK#/dev/} ;; *) fail invalid-disk "disk must be an absolute /dev path" ;; esac
    case "$DISK_NAME" in ''|*/*) fail invalid-disk "disk is not a whole-disk path: $ANSWER_DISK" ;; esac
    whole_disk_exists "$DISK_NAME" || fail invalid-disk "disk does not exist or is not a whole disk: $ANSWER_DISK"
    disk_is_iso "$DISK_NAME" && fail iso-target "refusing the disk that contains /cdrom: $ANSWER_DISK"
    case "$ANSWER_ROLE" in standalone)
            [ -z "$ANSWER_PRIMARY_URL" ] || fail invalid-primary-url "standalone requires an empty primary_url"
            [ -z "$ANSWER_PRIMARY_FINGERPRINT" ] || fail invalid-primary-url "standalone requires an empty primary_fingerprint" ;;
        secondary)
            ANSWER_PRIMARY_URL=$(normalize_primary_url "$ANSWER_PRIMARY_URL") \
                || fail invalid-primary-url "secondary requires the primary's IP or name (or https://HOST:8443)"
            # #1310: no secondary without a primary that answered -- the GUI
            # bridge writes the fingerprint only from its own green check.
            case "$ANSWER_PRIMARY_FINGERPRINT" in *[!0-9a-f]*|'') fail primary-unverified "the primary was not verified (no green connection check)" ;; esac
            [ "${#ANSWER_PRIMARY_FINGERPRINT}" -eq 64 ] || fail primary-unverified "the primary was not verified (no green connection check)"
            ;;
        *) fail invalid-role "role must be standalone or secondary" ;; esac
    # #1997: the server's name is a computer name -- server/core/hostname_rule.py, the same rule
    # (letters, digits, hyphen, up to 15, no hyphen at either end); a test compares the two.
    case "$ANSWER_HOSTNAME" in ''|*[!A-Za-z0-9-]*|-*|*-) fail invalid-hostname "hostname must be letters, digits and hyphens, not at either end" ;; esac
    [ "${#ANSWER_HOSTNAME}" -le 15 ] || fail invalid-hostname "hostname is longer than 15 characters"
    case "$ANSWER_SERVERS_IF" in ''|*/*|*[!A-Za-z0-9_.:-]*) fail invalid-interface "invalid management interface name" ;; esac
    [ -r "$SYSROOT/sys/class/net/$ANSWER_SERVERS_IF/address" ] || fail invalid-interface "management interface does not exist: $ANSWER_SERVERS_IF"
    valid_mac "$ANSWER_SERVERS_MAC" || fail invalid-mac "invalid management MAC: $ANSWER_SERVERS_MAC"
    IFS= read -r _va_live_mac < "$SYSROOT/sys/class/net/$ANSWER_SERVERS_IF/address" || fail interface-check "cannot read MAC for $ANSWER_SERVERS_IF"
    [ "$_va_live_mac" = "$ANSWER_SERVERS_MAC" ] || fail mac-mismatch "$ANSWER_SERVERS_MAC is not on $ANSWER_SERVERS_IF"
    case "$ANSWER_SERVERS_MODE" in
        dhcp) [ -z "$ANSWER_SERVERS_ADDR$ANSWER_SERVERS_MASK$ANSWER_SERVERS_GW$ANSWER_SERVERS_DNS" ] || fail invalid-network "DHCP requires empty static fields" ;;
        static)
            valid_ipv4 "$ANSWER_SERVERS_ADDR" || fail invalid-address "invalid static address"
            valid_ipv4 "$ANSWER_SERVERS_MASK" || fail invalid-netmask "invalid static netmask"
            # #2096: the screen says "(optional)" -- empty is no gateway / no DNS
            # (setup-boot-server and netcfg take them empty); a value is checked.
            [ -z "$ANSWER_SERVERS_GW" ] || valid_ipv4 "$ANSWER_SERVERS_GW" || fail invalid-gateway "invalid static gateway"
            [ -z "$ANSWER_SERVERS_DNS" ] || valid_dns_list "$ANSWER_SERVERS_DNS" || fail invalid-dns "invalid comma-separated DNS list"
            ;;
        *) fail invalid-network-mode "servers_mode must be dhcp or static" ;;
    esac
    # #1447: one of tzdata's zones (zones.txt), checked here -- a typo is a
    # field error on the hostname step, not a failed install after partitioning.
    # grep: 1 = not in the list (the answer), 2 = the list itself is missing.
    case "$ANSWER_TIMEZONE" in ''|*[!A-Za-z0-9_/+-]*) fail invalid-timezone "timezone must be a tzdata zone name (Asia/Jerusalem)" ;; esac
    _va_zones=${ZONES_FILE:-$INSTALLER_DIR/lib/zones.txt}
    grep -qxF -- "$ANSWER_TIMEZONE" "$_va_zones"; _va_rc=$?
    case "$_va_rc" in 0) ;; 1) fail invalid-timezone "unknown timezone: $ANSWER_TIMEZONE" ;;
        *) fail zones-list-missing "cannot read the zone list $_va_zones (grep $_va_rc)" ;; esac
    # The admin's name is the operator's choice (21/09) -- same rules as a
    # console user name; `admin` is only the GUI's default.
    case "$ANSWER_ADMIN_USER" in ''|*[!a-z0-9._-]*|-*) fail invalid-admin-user "admin_user must be lowercase letters, digits, . _ - and not start with -" ;; esac
    [ "${#ANSWER_ADMIN_USER}" -le 32 ] || fail invalid-admin-user "admin_user is longer than 32 characters"
    [ -n "$ANSWER_ADMIN_PASS" ] || fail invalid-admin-password "admin_pass must not be empty"
    # #1632 (Nadav 24/10): root's password is chosen on the install screen; no
    # value from the ISO and no default. Not exported: finish.sh is sourced.
    [ -n "$ANSWER_ROOT_PASS" ] || fail invalid-root-password "root_pass must not be empty"
    validate_raid   # #1989 (raid.sh): no RAID unless the answers ask for it
    export DISK_NAME ANSWER_DISK ANSWER_ROLE ANSWER_PRIMARY_URL ANSWER_PRIMARY_FINGERPRINT ANSWER_SERVERS_IF
    export ANSWER_SERVERS_MAC ANSWER_SERVERS_MODE ANSWER_SERVERS_ADDR ANSWER_SERVERS_MASK
    export ANSWER_SERVERS_GW ANSWER_SERVERS_DNS ANSWER_HOSTNAME ANSWER_TIMEZONE ANSWER_ADMIN_USER ANSWER_ADMIN_PASS
    export ANSWER_RAID_LEVEL ANSWER_RAID_DISKS ANSWER_RAID_SPARES
}
