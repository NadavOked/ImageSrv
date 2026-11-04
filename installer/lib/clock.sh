# clock.sh -- the time source chosen on the "שעון השרת" step (#2144). POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module shares state with the entry point
#
# ntp_servers= in the answers file: empty = automatic (Debian's pools), else
# up to four IPv4 addresses or names, separated by spaces -- the console's
# setting (server/system/timesync.py, #1773), stricter: no IPv6 literal here,
# so every value the installer accepts the server accepts too (a test runs
# both rules on the same values). The same file the server writes
# (/etc/chrony/conf.d/imagesrv.conf, timesync._write_client), and firstboot
# hands the value to the server's setting (setup-boot-server --ntp-servers).

CLOCK_CHECK_S=${CLOCK_CHECK_S:-20}
DEBIAN_POOL_NAME=debian.pool.ntp.org

# One name: labels of letters, digits and inner hyphens, 1-63 each, 253 in all.
valid_ntp_name() {
    _vn=${1%.}
    [ -n "$_vn" ] && [ "${#_vn}" -le 253 ] || return 1
    case "$_vn" in .*|*..*) return 1 ;; esac
    _vn_rest=$_vn
    while :; do
        _vn_label=${_vn_rest%%.*}
        case "$_vn_label" in ''|-*|*-|*[!A-Za-z0-9-]*) return 1 ;; esac
        [ "${#_vn_label}" -le 63 ] || return 1
        case "$_vn_rest" in *.*) _vn_rest=${_vn_rest#*.} ;; *) return 0 ;; esac
    done
}

# Prints the value in its one stored form (single spaces); 1 = refused.
normalize_ntp_servers() {
    _ns_out=; _ns_n=0
    # shellcheck disable=SC2086 # split on blanks, globbing off
    set -f; set -- $1; set +f
    for _ns_one in "$@"; do
        _ns_n=$((_ns_n + 1)); [ "$_ns_n" -le 4 ] || return 1
        valid_ipv4 "$_ns_one" || valid_ntp_name "$_ns_one" || return 1
        _ns_out="${_ns_out:+$_ns_out }$_ns_one"
    done
    printf '%s' "$_ns_out"
}

validate_ntp_servers() {
    ANSWER_NTP_SERVERS=$(normalize_ntp_servers "${ANSWER_NTP_SERVERS:-}") \
        || fail invalid-ntp-servers "ntp_servers must be up to four IPv4 addresses or names, separated by spaces"
    export ANSWER_NTP_SERVERS
}

# timesync._write_client, byte for byte: the server rewrites this file on
# every start from the same setting, so the first boot and the console agree.
chrony_source_text() {
    printf '# Managed by ImageSrv \342\200\224 time source (#1773)\nmakestep 1.0 -1\n'
    if [ -z "$1" ]; then
        for _cs_n in 0 1 2 3; do printf 'pool %s.%s iburst\n' "$_cs_n" "$DEBIAN_POOL_NAME"; done
    else
        set -f
        # shellcheck disable=SC2086 # split on blanks, globbing off
        for _cs_one in $1; do printf 'server %s iburst\n' "$_cs_one"; done
        set +f
    fi
}

write_chrony_source() {
    write_target 0644 /etc/chrony/conf.d/imagesrv.conf "$(chrony_source_text "$ANSWER_NTP_SERVERS")
"
}

# Is the chosen source answering? Asked in the target, before the reboot:
# `chronyd -Q` measures and sets nothing. Three outcomes, each by positive
# evidence (principle 5) -- "synced" only on chronyd's own offset line,
# "unsynced" only when it ran to its timeout without one; anything else
# (no chronyd, a fatal error, killed) is "unchecked", never "synced". Never
# fails the installation: the done screen and the DCUI say it (Nadav 04/11).
check_clock() {
    # beside the state file -- --state is parsed after this file is sourced
    _ck_file=${CLOCK_FILE:-${STATE_FILE%/*}/clock-check}
    _ck_server=${ANSWER_NTP_SERVERS:-$DEBIAN_POOL_NAME}
    set -f; set --   # one argv word per directive: chronyd reads no file then
    if [ -z "$ANSWER_NTP_SERVERS" ]; then
        for _ck_n in 0 1 2 3; do set -- "$@" "pool $_ck_n.$DEBIAN_POOL_NAME iburst"; done
    else
        # shellcheck disable=SC2086 # split on blanks, globbing off
        for _ck_one in $ANSWER_NTP_SERVERS; do set -- "$@" "server $_ck_one iburst"; done
    fi
    set +f
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command chroot "$TARGET" timeout $((CLOCK_CHECK_S + 10)) chronyd -Q -t "$CLOCK_CHECK_S" "$@"
        return 0
    fi
    rm -f "$_ck_file" "$_ck_file.next"   # a stale answer is never read as this one
    _ck_out=$(chroot "$TARGET" timeout $((CLOCK_CHECK_S + 10)) chronyd -Q -t "$CLOCK_CHECK_S" "$@" 2>&1)
    _ck_rc=$?
    clock_verdict "$_ck_rc" "$_ck_out"
    printf 'installer: clock %s (%s) chronyd=%s: %s\n' "$CLOCK_STATE" "$_ck_server" "$_ck_rc" \
        "$(printf '%s\n' "$_ck_out" | tail -n 1)" >&2
    { printf 'clock=%s\nclock_server=%s\n' "$CLOCK_STATE" "$_ck_server"
      if [ -n "$CLOCK_OFFSET_MS" ]; then printf 'clock_offset_ms=%s\n' "$CLOCK_OFFSET_MS"; fi
    } > "$_ck_file.next" && mv "$_ck_file.next" "$_ck_file" \
        || printf 'installer: [clock-record-failed] cannot write %s; the done screen says "not checked"\n' "$_ck_file" >&2
    return 0
}

# chronyd's exit code and output -> CLOCK_STATE (+ CLOCK_OFFSET_MS when synced).
clock_verdict() {
    CLOCK_STATE=unchecked; CLOCK_OFFSET_MS=
    _cv_off=$(printf '%s\n' "$2" | sed -n 's/.*System clock wrong by \([-+]*[0-9][0-9.]*\) seconds.*/\1/p' | head -n 1)
    if [ -n "$_cv_off" ]; then
        CLOCK_STATE=synced
        CLOCK_OFFSET_MS=$(printf '%s\n' "$_cv_off" | awk '{v = $1 * 1000; if (v < 0) v = -v; printf "%d", v + 0.5}')
    elif [ "$1" -ne 0 ] && printf '%s\n' "$2" | grep -q 'Timeout reached'; then
        CLOCK_STATE=unsynced
    fi
}
