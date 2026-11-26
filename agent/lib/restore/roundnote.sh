# roundnote.sh -- a sentence the agent knows and the server's journal did not (#3125: L12, U5, U6 of the brain's catalog).
# POSIX sh (busybox ash).
#
# A drawer pushed back in, the decision to complete it and the ranges, another disk pushed into its slot: until now each
# was one line in the agent's own log, and the server -- and so the console and the brain -- learned nothing until the
# log tail arrived at the end of the round (agent_log_send), if at all.
#
#   round_note KIND KEY SENTENCE   -- one note, once per (KIND, KEY) in this job. KIND is a word the server knows
#                                     (server/agent/round_notes.py KINDS); KEY says which instance, so a sentence that
#                                     repeats (a loop that looks again) is one note.
#   round_notes_json SESSION       -- ,"round_notes":[{"kind","key","text"}...] -- every progress report of the job
#                                     carries ALL its notes (machine_extra_json, datareport.sh); the server writes each to
#                                     its journal once. Nothing when there are none, or when this is not a session's report.
#   round_notes_reset              -- a new job begins (job_reset, progress.sh): the last job's notes are not this one's.
#
# The JSON of a note is built ONCE, when it is written -- the report runs every two seconds on a weak cloner and a fork per
# note per report is not free. At most ROUND_NOTES_MAX notes a job: the rest are dropped, and said in the agent log.

ROUND_NOTES_MAX="${ROUND_NOTES_MAX:-30}"

round_note() {   # $1 = kind, $2 = key, $3 = the sentence. Never fails: a note is not worth stopping a round for
    [ -n "${RUN_DIR:-}" ] && [ -n "${1:-}" ] && [ -n "${2:-}" ] || return 0
    _rn_f="$RUN_DIR/round_notes"
    _rn_head="{\"kind\":\"$(json_escape "$1")\",\"key\":\"$(json_escape "$2")\""
    if [ -s "$_rn_f" ] && grep -qF -- "$_rn_head," "$_rn_f" 2>/dev/null; then return 0; fi
    if [ $(( $(wc -l 2>/dev/null < "$_rn_f" || echo 0) + 0 )) -ge "$ROUND_NOTES_MAX" ]; then
        log "round note $1 ($2) dropped: $ROUND_NOTES_MAX notes already"; return 0
    fi
    printf '%s,"text":"%s"}\n' "$_rn_head" "$(json_escape "${3:-}")" >> "$_rn_f" 2>/dev/null || log "round note $1 ($2) was not written"
    return 0
}

round_notes_json() {   # $1 = the session of the report
    [ -n "${1:-}" ] && [ -s "$RUN_DIR/round_notes" ] || return 0
    _rj=""
    while IFS= read -r _rl; do _rj="${_rj:+$_rj,}$_rl"; done < "$RUN_DIR/round_notes"
    [ -z "$_rj" ] || printf ',"round_notes":[%s]' "$_rj"
}

round_notes_reset() { rm -f "$RUN_DIR/round_notes"; }
