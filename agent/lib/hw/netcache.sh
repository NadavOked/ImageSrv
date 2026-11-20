# netcache.sh -- the netprobe.json cache: one background job at a time, a whole file or none (#3632). POSIX sh.
# Used by netprobe_json (netprobe.sh); loaded with it.
#
# #3632 (18/11, cloner 2): every hello that found the cache missing or stale started its own 35 s job, and all of them wrote
# the same $RUN_DIR/netprobe.json.tmp (cablelive.sh aging the cache started more). The first job's `mv` made that name the
# cache; the next job still held the same inode open and wrote at offset 0 -- a longer first answer left its tail behind:
# `...}]}}`. Not JSON, stamped fresh for NETPROBE_TTL, so the server answered every hello 400 bad_json and the machine vanished.
# Now: one job at a time ($_pc.running, stale after NETPROBE_JOB_MAX_S, as probe_bg.sh), a tmp name of its own, an mv only of
# a whole fragment, and a cache that is not whole is dropped instead of sent -- a body that is not JSON must never leave here.

# #3652 (18/11, cloner 2): every stamp here -- `.running`, `.at`, `.stale`, the job's start -- is whole seconds of UPTIME, not `date`.
# The first hello of a cold boot goes out on the RTC, and only its answer sets the clock from the server (hello.sh, "was 835s off"):
# the first job started between the two, and wall time put `.at` and `.running` 738 s in the future -- a dead job was not
# restarted until the clock caught up, the cache was "fresh" for TTL + the jump, and `_np_stamp` compared two clocks. /proc/uptime
# only rises and /run is empty at every boot. A stamp that is not usable -- missing, not a number, later than now -- is no
# "fresh" and no "alive" (principle 5); no readable uptime = no job (said in the log) and nothing counted fresh.

# Whole seconds since boot; rc 1 = unreadable (UPTIME_FILE as guiearly.sh, for tests).
_np_mono() {
    [ -r "${UPTIME_FILE:-/proc/uptime}" ] || return 1
    IFS=' ' read -r _nm_u _nm_r < "${UPTIME_FILE:-/proc/uptime}" || return 1
    _nm_u=${_nm_u%%.*}
    case "$_nm_u" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s' "$_nm_u"
}

# Age in seconds of the stamp in file $1 at uptime $2. rc 1 = no usable stamp: missing, not a number, or later than $2.
_np_age() {
    case "$2" in ''|*[!0-9]*) return 1 ;; esac
    _na=$(cat "$1" 2>/dev/null) || return 1
    case "$_na" in ''|*[!0-9]*) return 1 ;; esac
    [ "$_na" -le "$2" ] || return 1
    printf '%s' $(($2 - _na))
}

# rc 0 = $1 is exactly one ,"netprobe":{...} fragment: the object opens right after the key and closes on the LAST byte,
# brackets pair, strings end. Missing or empty = not whole (principle 5: "could not check" is not "fine").
_np_whole() {
    [ -s "$1" ] && awk '
        { s = s $0 "\n" }
        END {
            sub(/\n+$/, "", s); n = length(s); p = ",\"netprobe\":"; k = length(p)
            bad = (substr(s, 1, k) != p || substr(s, k + 1, 1) != "{"); q = 0; st = ""
            for (i = k + 1; i <= n && !bad; i++) {
                c = substr(s, i, 1)
                if (q) { if (c == "\\") i++; else if (c == "\"") q = 0; continue }
                if (c == "\"") q = 1
                else if (c == "{" || c == "[") st = st c
                else if (c == "}" || c == "]") {
                    if (substr(st, length(st)) != (c == "}" ? "{" : "[")) bad = 1
                    st = substr(st, 1, length(st) - 1)
                }
                if (st == "" && i < n) bad = 1      # closed before the last byte: a tail follows
            }
            exit (bad || q || st != "" || n <= k)
        }' "$1"
}

# #3636: stamps the cache fresh ($1.at = $2, the job's start) -- unless cablelive.sh wrote $1.stale (epoch) at or after this
# job started: the job measured before cable-live.json existed, and its cache must stay aged so the next hello measures again.
# A flag that cannot be read counts as "after" (principle 5). A job that started after the flag clears it.
_np_stamp() {
    printf '%s' "$2" > "$1.at" || return 1
    _ns=$(cat "$1.stale" 2>/dev/null) || return 0
    case "$_ns" in ''|*[!0-9]*) _ns=$2 ;; esac
    [ "$2" -le "$_ns" ] || { rm -f "$1.stale"; return 0; }
    rm -f "$1.at"; log "netprobe: job from $2 is older than cable-live.json -- the cache stays aged"
}

# $1 = cache path, $2 = uptime now (_np_mono). No second job while one runs; a marker older than the ceiling -- or one that cannot
# be read as an age -- is a dead job.
_np_bg() {
    _nb_pc=$1; _nb_now=$2
    case "$_nb_now" in ''|*[!0-9]*) log "netprobe: no uptime to stamp a job with -- none started, nothing counted fresh" >&2; return 1 ;; esac
    if [ -s "$_nb_pc.running" ]; then
        _nb_a=$(_np_age "$_nb_pc.running" "$_nb_now") && [ "$_nb_a" -le "${NETPROBE_JOB_MAX_S:-120}" ] && return 0
        log "netprobe: job marker is ${_nb_a:-unusable} s old (ceiling ${NETPROBE_JOB_MAX_S:-120}s) -- another starts" >&2   # not stdout: a $( ) around hello is the fragment
    fi
    printf '%s\n' "$_nb_now" > "$_nb_pc.running" || { log "netprobe: job not started (marker not written)" >&2; return 1; }
    (
        bg_detach_fds   # #3548 (#3524)
        _nb_t="$_nb_pc.tmp.$_nb_now.$$"
        _netprobe_fresh > "$_nb_t" && _np_whole "$_nb_t" && mv "$_nb_t" "$_nb_pc" && _np_stamp "$_nb_pc" "$_nb_now"
        _nb_rc=$?
        [ "$_nb_rc" = 0 ] || log "netprobe: job failed (rc $_nb_rc) -- no cache written, the next hello measures again"
        rm -f "$_nb_t"
        [ "$(cat "$_nb_pc.running" 2>/dev/null)" != "$_nb_now" ] || rm -f "$_nb_pc.running"
    ) > /dev/null 2>&1 < /dev/null &
}
