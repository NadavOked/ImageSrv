# progress.sh -- atomic installer state records. POSIX sh (busybox ash).
# shellcheck disable=SC2034,SC2154 # sourced module shares state with the entry point

STATE_FILE="${STATE_FILE:-/run/imagesrv/installer.state}"
PROGRESS_PCT=0
PROGRESS_LOG=
_FAIL_ACTIVE=0

progress_clean() {
    printf '%s' "$1" | tr '\r\n' '  '
}

progress_write() {
    # state, pct, title, log, error
    _pw_state=$1; _pw_pct=$2; _pw_title=$(progress_clean "$3")
    _pw_log=$(progress_clean "$4"); _pw_error=$(progress_clean "$5")
    case "$_pw_state" in
        partitioning|bootstrap|packages|bootloader|finishing|done|failed) ;;
        *) printf 'installer: invalid progress state: %s\n' "$_pw_state" >&2; return 1 ;;
    esac
    case "$_pw_pct" in ''|*[!0-9]*) return 1 ;; esac
    [ "$_pw_pct" -le 100 ] || return 1
    _pw_dir=${STATE_FILE%/*}; [ "$_pw_dir" = "$STATE_FILE" ] && _pw_dir=.
    mkdir -p "$_pw_dir" || return 1
    # if/then, not `[ ] && printf`: with an empty log and error the last `&&`
    # returns 1, the group returns 1, and a successful write became
    # "progress-write-failed" on every step (#231 rule; found on Linux 21/09).
    {
        printf 'state=%s\npct=%s\ntitle=%s\n' "$_pw_state" "$_pw_pct" "$_pw_title"
        if [ -n "$_pw_log" ]; then printf 'log=%s\n' "$_pw_log"; fi
        if [ -n "$_pw_error" ]; then printf 'error=%s\n' "$_pw_error"; fi
    } > "$STATE_FILE.next" || return 1
    mv "$STATE_FILE.next" "$STATE_FILE" || return 1
    printf 'state=%s pct=%s title=%s' "$_pw_state" "$_pw_pct" "$_pw_title" >&2
    if [ -n "$_pw_log" ]; then printf ' log=%s' "$_pw_log" >&2; fi
    if [ -n "$_pw_error" ]; then printf ' error=%s' "$_pw_error" >&2; fi
    printf '\n' >&2
    PROGRESS_PCT=$_pw_pct; PROGRESS_LOG=$_pw_log
}

progress() {
    progress_write "$1" "$2" "$3" "${4:-}" "" || {
        printf 'installer: progress-write-failed: %s\n' "$STATE_FILE" >&2
        exit 2
    }
}

fail() {
    _f_code=$1; shift; _f_message=$*
    if [ "$_FAIL_ACTIVE" -eq 0 ]; then
        _FAIL_ACTIVE=1
        if command -v cleanup_mounts >/dev/null 2>&1; then cleanup_mounts; fi
        progress_write failed "$PROGRESS_PCT" "Installation failed" "$PROGRESS_LOG" \
            "$_f_code: $_f_message" ||
            printf 'installer: progress-write-failed while reporting %s\n' "$_f_code" >&2
    fi
    printf 'installer: [%s] %s\n' "$_f_code" "$_f_message" >&2
    exit 2
}
