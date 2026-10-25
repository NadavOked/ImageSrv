# datareport.sh -- what the server keeps about a round, beyond the progress report
# (#1882-#1889, Nadav 20/10: "also in v1"). Everything here is best effort: a
# failure never changes the machine's screen or its round, and is logged locally.
# Network: once per round (the last report / the end of the round) -- never on
# every two-second progress tick.
# POSIX sh (busybox ash).

AGENT_LOG_LINES="${AGENT_LOG_LINES:-400}"
AGENT_LOG_BYTES="${AGENT_LOG_BYTES:-65536}"

agent_log_send() {
    # #1882: the tail of this machine's log, gzip, to the server -- /run is gone
    # after the power-off and production has no SSH. $1 = session id. The reason
    # is "failed" when any drawer failed, else "round_end". The server filters
    # secrets and anything read from a disk before it stores a byte (#1924).
    _al_r=round_end
    for _al_s in "$RUN_DIR"/targets/*/state; do
        [ "$(cat "$_al_s" 2>/dev/null)" = failed ] && _al_r=failed
    done
    [ "$(cat "$RUN_DIR/state" 2>/dev/null)" = failed ] && _al_r=failed
    _al_f="$RUN_DIR/agentlog.gz"
    if tail -n "$AGENT_LOG_LINES" "$LOG_FILE" 2>/dev/null | tail -c "$AGENT_LOG_BYTES" | gzip -c > "$_al_f" 2>/dev/null \
        && curl -sfS --max-time "${HTTP_TIMEOUT:-10}" -H "Content-Type: application/gzip" \
            --data-binary "@$_al_f" "$SERVER/api/v1/agent/log?mac=$MAC&session_id=${1:-}&reason=$_al_r" \
            > /dev/null 2>&1; then
        log "agent log: sent to the server ($_al_r)"
    else
        log "agent log: NOT delivered to the server ($_al_r) -- the round is unaffected"
    fi
    rm -f "$_al_f"
    return 0
}

stage_at() {
    # #1883: "stage $2 starts now" on each disk of the word list $1 -- one line
    # "<name> <uptime>" in targets/<d>/stages. A disk whose stages already ended,
    # or that failed/was cancelled, takes no more marks. No uptime = no mark.
    _sa_t=$(cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null)
    case "$_sa_t" in ''|*[!0-9.]*) return 0 ;; esac
    for _sa_d in $1; do
        _sa_f="$RUN_DIR/targets/$_sa_d/stages"
        [ -d "$RUN_DIR/targets/$_sa_d" ] || continue
        grep -q '^end ' "$_sa_f" 2>/dev/null && continue
        case "$2:$(cat "$RUN_DIR/targets/$_sa_d/state" 2>/dev/null)" in end:*) ;; *:failed|*:cancelled) continue ;; esac
        echo "$2 $_sa_t" >> "$_sa_f"
    done
    return 0
}

target_extra_json() {
    # #1883: ,"stages":[["gpt",12.3],...,["end",80.1]] -- only once the target is
    # final and its stages ended; a few dozen bytes per final target, repeated
    # with it because the server keeps the targets of the latest report. A target
    # still open while the machine is final gets its "end" now.
    _te="$RUN_DIR/targets/$1"
    [ -s "$_te/stages" ] || return 0
    case "$(cat "$_te/state" 2>/dev/null)" in done|failed|cancelled) ;; *) return 0 ;; esac
    if ! grep -q '^end ' "$_te/stages"; then
        case "$(cat "$RUN_DIR/state" 2>/dev/null)" in done|failed|partial) stage_at "$1" end ;; *) return 0 ;; esac
    fi
    awk 'BEGIN { printf ",\"stages\":[" }
        $1 ~ /^[a-z0-9_]+$/ && $2 ~ /^[0-9]+(\.[0-9]+)?$/ { printf "%s[\"%s\",%s]", (n++ ? "," : ""), $1, $2 }
        END { printf "]" }' "$_te/stages"
}

_cpu_stat() {
    # #1885: "total idle iowait" jiffies from /proc/stat's aggregate cpu line; nothing = unread.
    awk '$1 == "cpu" { t = 0; for (i = 2; i <= NF; i++) t += $i; print t, $5, $6; exit }' "${SYSROOT:-}/proc/stat" 2>/dev/null
}

_cpu_throttle() {
    # #1885: the sum of every core's thermal_throttle/core_throttle_count; nothing = no CPU has one.
    _ct=
    for _ctf in "${SYSROOT:-}"/sys/devices/system/cpu/cpu[0-9]*/thermal_throttle/core_throttle_count; do
        [ -r "$_ctf" ] || continue
        read -r _ctv < "$_ctf" 2>/dev/null
        case "$_ctv" in ''|*[!0-9]*) continue ;; esac
        _ct=$(( ${_ct:-0} + _ctv ))
    done
    echo "$_ct"
}

_cpu_temp() {
    # #1885: the hottest CPU reading now, whole degrees C -- thermal zones, and coretemp/k10temp
    # hwmon sensors; nothing = no source (#1870 thermal), never 0.
    for _ctf in "${SYSROOT:-}"/sys/class/thermal/thermal_zone*/temp "${SYSROOT:-}"/sys/class/hwmon/hwmon*/temp*_input; do
        case "$_ctf" in */hwmon/*) grep -qxE 'coretemp|k10temp|zenpower' "${_ctf%/*}/name" 2>/dev/null || continue ;; esac
        cat "$_ctf" 2>/dev/null
    done | awk '/^[0-9]+$/ { c = int($1 / 1000); if (c > 0 && c < 150 && c > m) m = c } END { if (m) print m }'
}

machine_extra_json() {
    # #1885: $1 = session id, $2 = task id (a capture: nothing). From the moment this machine
    # writes: the /proc/stat and throttle counters once (cpu.start), the peak temperature on
    # every tick (local reads, no network); ,"cpu":{...} only once the machine's round is final.
    [ -n "${1:-}" ] && [ -z "${2:-}" ] || return 0
    _cs="$RUN_DIR/cpu.start"
    [ "$(head -n 1 "$_cs" 2>/dev/null)" = "$1" ] || rm -f "$_cs" "$RUN_DIR/cpu.peak"
    _cst=$(cat "$RUN_DIR/state" 2>/dev/null)
    if [ ! -s "$_cs" ]; then
        [ "$_cst" = writing ] || return 0
        { echo "$1"; _cpu_stat; echo "t=$(_cpu_throttle)"; cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null; } > "$_cs"
    fi
    _ctn=$(_cpu_temp)
    if [ -n "$_ctn" ]; then
        _ctp=$(cat "$RUN_DIR/cpu.peak" 2>/dev/null)
        [ "${_ctp:-0}" -ge "$_ctn" ] 2>/dev/null || echo "$_ctn" > "$RUN_DIR/cpu.peak"
    fi
    case "$_cst" in done|failed|partial) ;; *) return 0 ;; esac
    awk -v e="$(_cpu_stat)" -v et="$(_cpu_throttle)" -v pk="$(cat "$RUN_DIR/cpu.peak" 2>/dev/null)" \
        -v eu="$(cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null)" '
        NR == 2 { ns = split($0, s, " ") } NR == 3 { st = substr($0, 3) } NR == 4 { su = $1 }
        END {
            busy = "null"; iow = "null"; thr = "null"; secs = "null"; tmp = "null"
            if (split(e, n, " ") == 3 && ns == 3 && n[1] - s[1] > 0) {
                dt = n[1] - s[1]
                busy = sprintf("%d", 100 * (dt - (n[2] - s[2]) - (n[3] - s[3])) / dt + 0.5)
                iow = sprintf("%d", 100 * (n[3] - s[3]) / dt + 0.5)
            }
            if (st ~ /^[0-9]+$/ && et ~ /^[0-9]+$/ && et + 0 >= st + 0) thr = et - st
            if (su ~ /^[0-9.]+$/ && eu ~ /^[0-9.]+$/ && eu + 0 >= su + 0) secs = sprintf("%d", eu - su)
            if (pk ~ /^[0-9]+$/) tmp = pk
            printf ",\"cpu\":{\"busy_pct\":%s,\"iowait_pct\":%s,\"throttle_delta\":%s,\"temp_max_c\":%s,\"seconds\":%s}", busy, iow, thr, tmp, secs
        }' "$_cs"
}
