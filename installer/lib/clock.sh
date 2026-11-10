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

# #2765: or "ידני — בלי שרת זמן" (a server with no internet): clock_manual= is the time the
# operator set, "YYYY-MM-DD HH:MM[:SS]" in timezone=, and ntp_servers= stays empty -- the two are
# alternatives. The engine sets the system clock and the hardware clock in the target's chroot
# (GNU date/hwclock and tzdata are there; the live image's busybox is not relied on), and records
# the moment so the server's setting says when the time was set.

CLOCK_CHECK_S=${CLOCK_CHECK_S:-20}
CLOCK_T0=0 CLOCK_MANUAL_EPOCH=0 CLOCK_MANUAL_BASE=0 CLOCK_MANUAL_AT=
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

# The date part of clock_manual= is a real calendar day, 2000..2099; the hour and minute exist.
validate_clock_manual() {
    ANSWER_CLOCK_MANUAL=${ANSWER_CLOCK_MANUAL:-}
    [ -n "${ANSWER_CLOCK_MANUAL:-}" ] || return 0
    [ -z "$ANSWER_NTP_SERVERS" ] \
        || fail invalid-clock-manual "clock_manual and ntp_servers are alternatives: a time server, or no time server"
    case "$ANSWER_CLOCK_MANUAL" in
        [0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]\ [0-2][0-9]:[0-5][0-9]|\
        [0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]\ [0-2][0-9]:[0-5][0-9]:[0-5][0-9]) ;;
        *) fail invalid-clock-manual "clock_manual must be YYYY-MM-DD HH:MM" ;;
    esac
    _cm_y=${ANSWER_CLOCK_MANUAL%%-*}; _cm_r=${ANSWER_CLOCK_MANUAL#*-}
    _cm_m=${_cm_r%%-*}; _cm_m=${_cm_m#0}; _cm_r=${_cm_r#*-}
    _cm_d=${_cm_r%% *}; _cm_d=${_cm_d#0}; _cm_h=${_cm_r#* }; _cm_h=${_cm_h%%:*}; _cm_h=${_cm_h#0}
    [ "$_cm_y" -ge 2000 ] && [ "$_cm_y" -le 2099 ] && [ "$_cm_m" -ge 1 ] && [ "$_cm_m" -le 12 ] \
        && [ "$_cm_d" -ge 1 ] && [ "$_cm_h" -le 23 ] \
        || fail invalid-clock-manual "clock_manual is not a date and time that exist"
    case "$_cm_m" in 4|6|9|11) _cm_max=30 ;; 2)
        if [ $((_cm_y % 4)) -eq 0 ] && { [ $((_cm_y % 100)) -ne 0 ] || [ $((_cm_y % 400)) -eq 0 ]; }; then _cm_max=29; else _cm_max=28; fi ;;
        *) _cm_max=31 ;; esac
    [ "$_cm_d" -le "$_cm_max" ] || fail invalid-clock-manual "clock_manual is not a date that exists"
}

clock_mark_start() {
    CLOCK_T0=$(date +%s)
    case "$CLOCK_T0" in ''|*[!0-9]*) fail clock-manual-failed "the live system's clock cannot be read" ;; esac
}

# The typed time -> epoch seconds, in the target (its tzdata, its GNU date), plus what the install
# has taken since it started. Done once, before the answers file is written: the same instant is
# the server's "set at" (ntp_manual_at, UTC ISO) and what set_manual_clock sets.
resolve_manual_clock() {
    CLOCK_MANUAL_AT=
    [ -n "${ANSWER_CLOCK_MANUAL:-}" ] || return 0
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command chroot "$TARGET" env "TZ=$ANSWER_TIMEZONE" date -d "$ANSWER_CLOCK_MANUAL" +%s
        CLOCK_MANUAL_EPOCH=0; CLOCK_MANUAL_BASE=0; CLOCK_MANUAL_AT=1970-01-01T00:00:00+00:00
        return 0
    fi
    _rm_typed=$(chroot "$TARGET" env "TZ=$ANSWER_TIMEZONE" date -d "$ANSWER_CLOCK_MANUAL" +%s)
    case "$_rm_typed" in ''|*[!0-9]*) fail clock-manual-failed "cannot turn $ANSWER_CLOCK_MANUAL ($ANSWER_TIMEZONE) into a time" ;; esac
    CLOCK_MANUAL_BASE=$(date +%s)
    CLOCK_MANUAL_EPOCH=$((_rm_typed + CLOCK_MANUAL_BASE - CLOCK_T0))
    CLOCK_MANUAL_AT=$(chroot "$TARGET" date -u -d "@$CLOCK_MANUAL_EPOCH" +%Y-%m-%dT%H:%M:%S+00:00)
    case "$CLOCK_MANUAL_AT" in [0-9][0-9][0-9][0-9]-*+00:00) ;; *) fail clock-manual-failed "cannot write $CLOCK_MANUAL_EPOCH as a date" ;; esac
}

# timesync._write_manual, byte for byte: no source at all
chrony_manual_text() {
    printf '# Managed by ImageSrv \342\200\224 time source (#2765): manual, no time server\n'
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
    if [ -n "${ANSWER_CLOCK_MANUAL:-}" ]; then
        write_target 0644 /etc/chrony/conf.d/imagesrv.conf "$(chrony_manual_text)
"
        return
    fi
    write_target 0644 /etc/chrony/conf.d/imagesrv.conf "$(chrony_source_text "$ANSWER_NTP_SERVERS")
"
}

# #2765: set the running clock and then the hardware clock the target will boot from -- each by its
# own command, and the running clock read back (rule 5: an exit code is not the proof). Unlike the
# time-server check this FAILS the installation: the operator asked for this very time, and a
# server that comes up with another one is the dangerous result, not an inconvenient one.
set_manual_clock() {
    _sm_file=${CLOCK_FILE:-${STATE_FILE%/*}/clock-check}
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command chroot "$TARGET" date -u -s '@<epoch>'
        print_command chroot "$TARGET" hwclock --systohc --utc
        return 0
    fi
    rm -f "$_sm_file" "$_sm_file.next"
    _sm_epoch=$((CLOCK_MANUAL_EPOCH + $(date +%s) - CLOCK_MANUAL_BASE))
    must_run clock-manual-failed "could not set the system clock" chroot "$TARGET" date -u -s "@$_sm_epoch"
    _sm_got=$(date +%s)
    case "$_sm_got" in ''|*[!0-9]*) fail clock-manual-failed "the system clock cannot be read back" ;; esac
    _sm_diff=$((_sm_got - _sm_epoch)); [ "$_sm_diff" -ge 0 ] || _sm_diff=$((0 - _sm_diff))
    [ "$_sm_diff" -le 5 ] || fail clock-manual-failed "the system clock reads $_sm_got after setting $_sm_epoch"
    must_run clock-manual-rtc "could not write the hardware clock" chroot "$TARGET" hwclock --systohc --utc
    printf 'installer: clock manual (%s) set to %s\n' "$ANSWER_TIMEZONE" "$ANSWER_CLOCK_MANUAL" >&2
    printf 'clock=manual\nclock_server=\nclock_manual_at=%s\n' "$CLOCK_MANUAL_AT" > "$_sm_file.next" \
        && mv "$_sm_file.next" "$_sm_file" \
        || printf 'installer: [clock-record-failed] cannot write %s; the done screen says "not checked"\n' "$_sm_file" >&2
}

# Is the chosen source answering? Asked in the target, before the reboot:
# `chronyd -Q` measures and sets nothing. Three outcomes, each by positive
# evidence (principle 5) -- "synced" only on chronyd's own offset line,
# "unsynced" only when it ran to its timeout without one; anything else
# (no chronyd, a fatal error, killed) is "unchecked", never "synced". Never
# fails the installation: the done screen and the DCUI say it (Nadav 04/11).
check_clock() {
    if [ -n "${ANSWER_CLOCK_MANUAL:-}" ]; then set_manual_clock; return 0; fi   # #2765: nothing to ask
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
