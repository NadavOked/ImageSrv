# datareport.sh -- what the server keeps about a round, beyond the progress report
# (#1882-#1889, Nadav 20/10: "also in v1"). Everything here is best effort: a
# failure never changes the machine's screen or its round, and is logged locally.
# Network: once per round (the last report / the end of the round) -- never on
# every two-second progress tick.
# POSIX sh (busybox ash).

# #3125 (R13): a long chunk wave wrote more than 400 lines / 64KB, and the tail lost the start of the wave. 2000 lines /
# 256KB is five times that; the server keeps the same (`agent_logs.MAX_TEXT`) and says `truncated` when it still cut.
AGENT_LOG_LINES="${AGENT_LOG_LINES:-2000}"
AGENT_LOG_BYTES="${AGENT_LOG_BYTES:-262144}"

agent_log_send() {
    # #1882: the tail of this machine's log, gzip, to the server -- /run is gone
    # after the power-off and production has no SSH. $1 = session id. The reason
    # is "failed" when any drawer failed, else "round_end". The server filters
    # secrets and anything read from a disk before it stores a byte (#1924).
    # #2841: $2 = a reason of the caller's own (checkpoint, fatal -- below).
    _al_r=round_end
    for _al_s in "$RUN_DIR"/targets/*/state; do
        [ "$(cat "$_al_s" 2>/dev/null)" = failed ] && _al_r=failed
    done
    [ "$(cat "$RUN_DIR/state" 2>/dev/null)" = failed ] && _al_r=failed
    [ -z "${2:-}" ] || _al_r=$2
    _al_f="$RUN_DIR/agentlog.gz"; _al_t="$RUN_DIR/agentlog.txt"
    # #3031: partclone's progress frames out, then the tail; $_al_cut = 1 when the agent cut (or could not tell)
    _al_cut=$(agent_log_prepare "$_al_t")
    if gzip -c 2>/dev/null < "$_al_t" > "$_al_f" \
        && curl -sfS --max-time "${HTTP_TIMEOUT:-10}" -H "Content-Type: application/gzip" -H "X-Imagesrv-Log-Cut: $_al_cut" \
            --data-binary "@$_al_f" "$SERVER/api/v1/agent/log?mac=$MAC&session_id=${1:-}&reason=$_al_r" \
            > /dev/null 2>&1; then
        log "agent log: sent to the server ($_al_r)"
    else
        log "agent log: NOT delivered to the server ($_al_r) -- the round is unaffected"
    fi
    rm -f "$_al_f" "$_al_t"
    return 0
}

agent_log_compact() {
    # #3031: $1 = the log with every CR already a newline. partclone redraws its
    # progress (ESC[A + CR) a few lines each update, and 84-99% of the 64KB tail
    # was those frames -- the start of the wave never reached the server. Frames
    # out, everything else as it was; the LAST frame (its two lines) only, once, at the end. A
    # log line glued behind a frame (the frame has no newline) is kept whole.
    awk '
        function keep(s) {
            if (s ~ /^[ \t]*$/) return
            if (s ~ /Elapsed:.*Completed:/ || s ~ /[Cc]urrent block:.*[Cc]omplete:/) {
                if (s ~ /Elapsed:/) lastE = s; else lastB = s
                if (match(s, /\[[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\] /)) print substr(s, RSTART)
                return
            }
            print s
        }
        { n = split($0, p, "\033")
          for (i = 1; i <= n; i++) { s = p[i]; if (i > 1) sub(/^\[[0-9;]*[A-Za-z]/, "", s); keep(s) } }
        END { if (lastE != "") print "(last partclone progress frame) " lastE
              if (lastB != "") print "(last partclone progress frame) " lastB }' "$1"
}

agent_log_prepare() {
    # $1 = the file to write; prints 0 or 1: 1 = the agent cut the log (past the
    # lines/bytes ceilings) or could not tell. "Not known to be whole" is not "whole"
    # (principle 5): the server sets agent_logs.truncated from this (#3031).
    _ap_all="$1.all"
    if tr '\r' '\n' 2>/dev/null < "$LOG_FILE" > "$1.raw" && agent_log_compact "$1.raw" > "$_ap_all" 2>/dev/null; then
        _ap_l=$(wc -l 2>/dev/null < "$_ap_all" | tr -d ' '); _ap_b=$(wc -c 2>/dev/null < "$_ap_all" | tr -d ' ')
        tail -n "$AGENT_LOG_LINES" "$_ap_all" 2>/dev/null | tail -c "$AGENT_LOG_BYTES" > "$1" 2>/dev/null
        case "$_ap_l$_ap_b" in
            ''|*[!0-9]*) echo 1 ;;   # the count itself failed
            *) if [ "$_ap_l" -le "$AGENT_LOG_LINES" ] && [ "$_ap_b" -le "$AGENT_LOG_BYTES" ]; then echo 0; else echo 1; fi ;;
        esac
    else
        log "agent log: progress frames NOT removed (tr/awk failed) -- the raw tail is sent, marked cut" >&2
        tail -n "$AGENT_LOG_LINES" "$LOG_FILE" 2>/dev/null | tail -c "$AGENT_LOG_BYTES" > "$1" 2>/dev/null
        echo 1
    fi
    rm -f "$1.raw" "$_ap_all"
}

# #2841 (work, 11/11): a cloner went quiet mid-read-back and rebooted; the log
# was sent only at the round's end, which never came -- agent_logs stayed empty.
_al_session() {   # the open job's session id from the last hello answer; nothing = none
    _as=$(json_get "${RESP:-$RUN_DIR/response.json}" ".session.id")
    case "$_as" in null|*[!A-Za-z0-9_.-]*) _as= ;; esac
    echo "$_as"
}
agent_log_checkpoint() {
    # A cloner's long stage begins (the read-back): the log so far, so that a
    # machine that goes quiet afterwards -- a reset nothing here can see -- still
    # left its stream and the start of the check on the server.
    [ "${D_ROLE:-}" = cloner ] && [ -n "${SERVER:-}" ] && [ -n "${MAC:-}" ] || return 0
    agent_log_send "$(_al_session)" checkpoint
}
agent_log_fatal() {
    # die_local is about to reboot. Only with a job open (a state file): the
    # everyday "no work for this machine" of every station sends nothing.
    [ -s "$RUN_DIR/state" ] && [ -n "${SERVER:-}" ] && [ -n "${MAC:-}" ] || return 0
    agent_log_send "$(_al_session)" fatal
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
    # #2987/#2988: the operator's sentence and a drawer that stands (opsay.sh, stall.sh), then the stages.
    if command -v target_say_json > /dev/null; then target_say_json "$1"; fi
    if command -v stall_json > /dev/null; then stall_json "$1"; fi
    # #3167: a note on a done target (the name written into an image not shut down clean) -- not `error`
    [ ! -s "$RUN_DIR/targets/$1/note" ] || printf ',"note":"%s"' "$(json_escape "$(cat "$RUN_DIR/targets/$1/note")")"
    if command -v rb_chunks_json > /dev/null; then rb_chunks_json "$1"; fi   # #3287: the chunks the read-back found different (rbchunks.sh)
    _stages_json "$1"
    if command -v senses_target_json > /dev/null; then senses_target_json "$1"; fi   # #3064: the link, the temperature, the connection (sensecore.sh)
}

_stages_json() {
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
        read -r _ctv 2>/dev/null < "$_ctf"
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
    # #1885: the CPU's; #3064: and the brain's senses (sensecore.sh) -- each fragment is its own, a failure of one costs only it.
    _cpu_machine_json "$@"
    if command -v senses_machine_json > /dev/null; then senses_machine_json "$@"; fi
}

_cpu_machine_json() {
    # #1885: $1 = session id, $2 = task id (a capture: nothing). From the moment this machine
    # writes: the /proc/stat and throttle counters once (cpu.start), the peak temperature on
    # every tick (local reads, no network); ,"cpu":{...} only once the machine's round is final.
    report_seq_json 2>/dev/null   # #3331 (I14): which boot and how far into it -- every progress report, session or task (reportseq.sh)
    [ -n "${1:-}" ] && [ -z "${2:-}" ] || return 0
    if command -v wave_sense_json > /dev/null; then wave_sense_json "$1"; fi   # #3041: S46-S50 of a chunk wave, while it runs (wavesense.sh)
    beeps_json "$1"   # #3125 (S62)
    if command -v line_wait_json > /dev/null 2>&1; then line_wait_json; fi   # #3181: a production line's cloner that waits for the next disk (linewait.sh)
    if command -v round_notes_json > /dev/null; then round_notes_json "$1"; fi   # #3125: what the agent knows and the journal did not (roundnote.sh)
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

beeps_json() {
    # #3125 (S62): $1 = session id. ,"beeps":[{"at","kind","outcome"[,"why"]}] -- the round's tunes as
    # roundbeep.sh noted them ($RUN_DIR/beeps), the last 50; outcome requested | played | unheard (with why).
    # The first report of a round starts the file afresh: what an earlier round heard is not this one's.
    [ "$(cat "$RUN_DIR/beeps.session" 2>/dev/null)" = "$1" ] || {
        rm -f "$RUN_DIR/beeps"; echo "$1" > "$RUN_DIR/beeps.session" || log "beeps: the round was not kept -- a later report may carry another round's tunes"; return 0; }
    [ -s "$RUN_DIR/beeps" ] || return 0
    tail -n 50 "$RUN_DIR/beeps" | awk -F'|' -v q='"' '
        $1 !~ /^[0-9]+$/ || $2 !~ /^[a-z]+$/ { next }
        { o = $3; w = ""; if (o ~ /^unheard:/) { w = substr(o, 9); o = "unheard" }
          if (o !~ /^(requested|played|unheard)$/) next
          gsub(/[\\"[:cntrl:]]/, "", w)   # the why is in roundbeep.sh words: nothing in it that JSON would escape is kept
          e = "{" q "at" q ":" $1 "," q "kind" q ":" q $2 q "," q "outcome" q ":" q o q
          if (o == "unheard") e = e "," q "why" q ":" q w q
          out = out (n++ ? "," : "") e "}" }
        END { if (n) printf ",%sbeeps%s:[%s]", q, q, out }'
}
