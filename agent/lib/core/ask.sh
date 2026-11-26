# ask.sh -- #3654 (K14): the brain's questions to the user (server/brain/ask.py). POSIX sh (busybox ash).
#
# The agent DECIDES NOTHING here. The server asks and the server's clock ends the wait; the agent shows what it is
# told and carries out what it is told. Three things:
#
# * ask_keep   -- every hello answer carries `asks`: the open questions about THIS machine, and the ones closed in the
#                 last quarter hour (answered / expired / cancelled, with what to do). Kept whole in asks.json.
#                 An answer with no readable `asks` array (null = the server could not read, or an old server)
#                 leaves the last list alone: "could not read" is not "there is none" (principle 5).
# * ask_state  -- what became of question N: open | answered X | expired X | cancelled X | gone | unknown.
#                 X is `effective`: the option to carry out. After "expired" or "cancelled" it is the SAFE DEFAULT, and
#                 never a destructive one; a destructive option arrives only as "answered", from a person.
#                 gone = the list was read and does not name it any more; unknown = no list was ever read.
#                 Neither is a decision: the caller keeps its own safe default (the code that was there before).
# * ask_answer -- the person at this machine's screen answered (POST /api/v1/agent/ask-answer). 0 only when the
#                 server accepted it (and its word on the question replaces the one in asks.json at once); 2 = 409
#                 (answered elsewhere, or expired) -- not a failure of the screen: the next ask_state says what
#                 happened; 1 = it did not reach the server.
#
# The machine's screen (#3654, mockup approved 18/11 -- native-gui brain_ask.h / screens_brain_ask.c): ask_gui_records
# copies asks.json into the GUI's state on every screen (guistate.sh gui_hello_state), and ask_gui_answer posts what
# the person chose there (guibridge.sh, `brain-ask|<id>|<option>`). The screen shows what the server says -- open,
# answered here, answered in the console, expired -- and nothing it did not hear. ask_wait only waits, and says on
# the console that the machine is waiting for an operator (attended.sh, the same prompt every other question uses).

ask_keep() {
    # $1 = a hello answer file.
    _ak_f="$RUN_DIR/asks.json"
    jq -ec 'if type == "object" and (.asks | type) == "array" then .asks else error("none") end' "$1" \
        > "$_ak_f.next" 2>/dev/null || { rm -f "$_ak_f.next"; return 0; }
    # #3654: when the list was read, by THIS machine's clock -- the GUI counts expires_in_s from it. No moment = no count
    # (the GUI then draws no open question): a stale moment would count the wrong minutes.
    { date +%s > "$RUN_DIR/asks.at.next" && mv "$RUN_DIR/asks.at.next" "$RUN_DIR/asks.at"; } 2>/dev/null ||
        rm -f "$RUN_DIR/asks.at" "$RUN_DIR/asks.at.next"
    mv "$_ak_f.next" "$_ak_f"
}

ask_state() {
    # $1 = the question's number. One line on stdout.
    case "${1:-}" in ''|*[!0-9]*) echo unknown; return 0 ;; esac
    _as_f="$RUN_DIR/asks.json"
    [ -s "$_as_f" ] || { echo unknown; return 0; }
    jq -r --argjson id "$1" '[.[] | select(.id == $id)]
        | if length == 0 then "gone"
          elif .[0].state == "open" then "open"
          else "\(.[0].state) \(.[0].effective // "")" end' "$_as_f" 2>/dev/null || echo unknown
}

ask_answer() {
    # $1 = the question's number, $2 = the option the person chose (an id from the question's own list).
    case "${1:-}" in ''|*[!0-9]*) return 1 ;; esac
    case "${2:-}" in [a-z]*) ;; *) return 1 ;; esac
    case "$2" in *[!a-z0-9_]*) return 1 ;; esac
    printf '{"mac":"%s","ask_id":%s,"option":"%s"}' "$MAC" "$1" "$2" > "$RUN_DIR/ask-answer.json" || return 1
    rm -f "$RUN_DIR/ask-answer.hdr"
    http_post_json "$SERVER/api/v1/agent/ask-answer" "$RUN_DIR/ask-answer.json" "" "$RUN_DIR/ask-answer.hdr" \
        > "$RUN_DIR/ask-answer.resp" || {
        # #3654: 409 = closed before this answer arrived -- positive evidence of the code only (curl -f hides the body)
        if grep -qs '^HTTP/[0-9.]* 409' "$RUN_DIR/ask-answer.hdr"; then
            log "question $1: already closed (409) -- the next hello says what happened"; return 2; fi
        log "question $1: the answer '$2' did not reach the server -- the question stays open"; return 1; }
    log "question $1: answered here: $2"
    ask_took "$1"
}

ask_took() {
    # $1 = the question's number. The server's own word on it -- the answer's `ask`, closed as answered -- replaces it in
    # asks.json now, so the screen says "registered" from what the server said, not from what it sent (#3654). Not
    # readable: the next hello brings it. The effective option of an answer is the answer (ask.resolution).
    _at_f="$RUN_DIR/asks.json"
    jq -e --argjson id "$1" --slurpfile r "$RUN_DIR/ask-answer.resp" '($r[0].ask // null) as $a
        | if type == "array" and ($a | type) == "object" and $a.id == $id and $a.state == "answered"
          then map(if .id == $id then $a + {effective: $a.answer, by_user: true} else . end) else error("no") end' \
        "$_at_f" > "$_at_f.next" 2>/dev/null && mv "$_at_f.next" "$_at_f" || rm -f "$_at_f.next"
    return 0
}

ask_gui_records() {
    # #3654: asks.json as the GUI's bask= / bask_opt= records (native-gui brain_ask.h). The deadline is this machine's
    # clock: the moment the list was read (asks.at) plus the server's expires_in_s. Open questions first, then the
    # closed ones (the newest first), 8 at most. No list, or no moment for it: no records.
    [ -s "$RUN_DIR/asks.json" ] || return 0
    read -r _ag_at 2>/dev/null < "$RUN_DIR/asks.at" || return 0
    case "$_ag_at" in ''|*[!0-9]*) return 0 ;; esac
    jq -r --argjson at "$_ag_at" '
      def c: (. // "") | tostring | gsub("[\\r\\n|]"; " ");
      def ep: (. // "") | tostring | sub("\\+00:00$"; "Z") | (try fromdateiso8601 catch "") | tostring;
      [.[] | select(type == "object" and (.id | type) == "number" and (.options | type) == "array")]
      | (map(select(.state == "open")) + (map(select(.state != "open")) | reverse)) | .[:8] | .[]
      | "bask=" + ([.id, .state, (.options | length), (.columns // 1), (.ttl_s // 0),
          (if .state == "open" and (.expires_in_s | type) == "number" then $at + .expires_in_s else "" end),
          .safe_default, .answer, .answered_role, .answered_by, (.closed_at | ep),
          (if ((.who // []) | index("admin")) != null then 1 else 0 end), .title_he, .detail_he] | map(c) | join("|")),
        (.id as $q | .options[] | "bask_opt=" + ([$q, .id, (if .destructive == true then 1 else 0 end),
          .label_he, .verb_he, .consequence_he, .outcome_he] | map(c) | join("|")))' "$RUN_DIR/asks.json" 2>/dev/null || :
}

ask_gui_answer() {
    # #3654: `brain-ask|<id>|<option>` from the GUI (guibridge.sh; no sign-in -- `machine` is whoever is at this
    # screen). Posted only for a question the last list names, with one of its own options. 0 = the server took it,
    # or it was closed already (409, or the list says so): the screen then shows what happened; 1 = not posted, or
    # it did not reach the server -- the question stays open on the screen.
    _ga=${1#brain-ask|}; _ga_id=${_ga%%|*}; _ga_opt=${_ga#*|}
    case "$_ga_id" in ''|*[!0-9]*) return 1 ;; esac
    _ga_s=$(jq -r --argjson id "$_ga_id" --arg o "$_ga_opt" '[.[] | select(.id == $id and any(.options[]; .id == $o))]
        | if length == 0 then "none" else .[0].state end' "$RUN_DIR/asks.json" 2>/dev/null) || return 1
    case "$_ga_s" in open) ;; answered|expired|cancelled) return 0 ;; *) return 1 ;; esac
    ask_answer "$_ga_id" "$_ga_opt"
    [ $? -ne 1 ]
}

ask_wait() {
    # $1 = the question's number, $2 = the ceiling in seconds, $3 = what the console shows while this machine waits.
    # stdout = the final ask_state line when the question was decided; return 1 on the ceiling, with nothing decided --
    # the caller then does what it did before there was a question (the server's own clock ends the question anyway).
    _aw_end=$(( $(date +%s) + ${2:-0} ))
    while :; do
        attended_hello "${3:-}" || :
        _aw_s=$(ask_state "$1")
        case "$_aw_s" in
            answered*|expired*|cancelled*) printf '%s\n' "$_aw_s"; return 0 ;;
            gone|unknown) ;;
        esac
        [ "$(date +%s)" -lt "$_aw_end" ] || return 1
        sleep "${ATTENDED_BEAT_S:-10}"
    done
}
