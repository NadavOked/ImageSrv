# peerwire.sh -- B29 in the flow (#3064): what the server's answer tells a neighbour cloner, and the cloner that lacks.
# POSIX sh (busybox ash). Needs peerfill.sh (and neighborfill.sh under it).
#
#   peer_reply_take <progress-reply>         every progress answer, from the reporter's loop (progress.sh)
#   peerfill_try <drawer> <part> <answer>    the drawer that came back, just before its HTTP fetch (drawerback.sh)
#
# Both act only on the server's word, and the server speaks only in brain_peer_mode=enforce. In shadow, the default,
# the answer carries neither `peer_serve` nor `peer_fill`: nothing is started, nothing is kept, nothing is read from
# a neighbour, and HTTP goes on as it did (principle 1: the default is what ran before).
#
#   - the cloner that HOLDS the chunks hears first (`peer_serve`): peer_serve_take starts its frame server, once per id;
#   - the cloner that LACKS them hears after (`peer_fill`, keyed by the drawer's serial). The answer comes every two
#     seconds and the drawer's completion starts when it starts, so the instruction is kept here
#     ($RUN_DIR/pfill/<serial>.json, 0600, the token in it) with the time it came, and used ONCE:
#     peerfill_try asks for what is missing of THIS partition, aged by the seconds that went by (a neighbour whose
#     end has passed is not asked: the result is `none`, the cloner's own, and the neighbour is not blamed);
#   - what a neighbour did not bring whole stays in the answer file, and `_chunk_fetch` fetches exactly that from the
#     server (peerfill_run's list is the evidence, and the drawer's own state file counts: drawerback.sh's `_dp_have`).
#     Every refusal, every failure and every missing piece of the context is "all of it over HTTP".
#
# The result for the server ("<id> ok|bad|none <why>", interfaces.md `peer_fill_result`) is kept in
# $RUN_DIR/targets/<drawer>/pf_result, where the reporter finds it whichever directory the completion ran in.

_pw_key() { printf '%s' "$1" | tr -c 'A-Za-z0-9' '_'; }   # a serial as a file name

peer_fill_take() {   # $1 = a progress reply. Keeps each instruction of `.peer_fill` once per id. 0 = read (or nothing there)
    grep -q '"peer_fill"' "$1" 2>/dev/null || return 0
    _pw_keys=$(jq -r '(.peer_fill // {}) | if type == "object" then keys[] else empty end' "$1" 2>/dev/null) \
        || { _pf_say "the answer's peer_fill was not read -- nothing is kept"; return 1; }
    _pw_d="$RUN_DIR/pfill"; ( umask 077; mkdir -p "$_pw_d" ) || return 1
    for _pw_s in $_pw_keys; do
        _pw_k=$(_pw_key "$_pw_s")
        _pw_i=$(jq -r --arg s "$_pw_s" '.peer_fill[$s].id // ""' "$1" 2>/dev/null)
        _pf_hex "$_pw_i" 8 32 || { _pf_say "an instruction to fill has no id -- not kept"; continue; }
        [ "$(cat "$_pw_d/$_pw_k.id" 2>/dev/null)" != "$_pw_i" ] || continue        # the server says it again in every answer
        rm -f "$_pw_d/$_pw_k.id" "$_pw_d/$_pw_k.used"
        ( umask 077; jq -c --arg s "$_pw_s" '.peer_fill[$s]' "$1" > "$_pw_d/$_pw_k.json" ) 2>/dev/null \
            && date +%s > "$_pw_d/$_pw_k.at" && echo "$_pw_i" > "$_pw_d/$_pw_k.id" \
            || { _pf_say "$_pw_i: the instruction to fill was not kept"; rm -f "$_pw_d/$_pw_k.json"; }   # no .id = not there
    done
    return 0
}

peer_reply_take() {   # $1 = a progress reply: the neighbour serves what it was told to, the cloner that lacks keeps what it was told
    peer_serve_take "$1" || :
    peer_fill_take "$1" || :
}

_pw_result() {   # $1 = drawer, $2 = the result line for the server
    mkdir -p "$RUN_DIR/targets/$1" && printf '%s\n' "$2" > "$RUN_DIR/targets/$1/pf_result.new" \
        && mv "$RUN_DIR/targets/$1/pf_result.new" "$RUN_DIR/targets/$1/pf_result"
}

peerfill_try() {   # $1 = the drawer (its name now), $2 = partition index, $3 = {"ranges":[[a,b],...]} (_chunk_fetch's answer).
    # 0 = go on to HTTP with $3 (rewritten to what is still missing; untouched when nothing was tried). 1 = nothing is missing
    _pt_s=$(disk_serial "$1" 2>/dev/null); [ -n "$_pt_s" ] || return 0
    _pt_k=$(_pw_key "$_pt_s"); _pt_d="$RUN_DIR/pfill"
    [ -f "$_pt_d/$_pt_k.json" ] && _pt_i=$(cat "$_pt_d/$_pt_k.id" 2>/dev/null) && [ -n "$_pt_i" ] || return 0    # no word of the server
    [ "$(cat "$_pt_d/$_pt_k.used" 2>/dev/null)" != "$_pt_i" ] || return 0                                        # one pairing, one try
    [ "$(jq -r '.part // ""' "$_pt_d/$_pt_k.json" 2>/dev/null)" = "$2" ] || return 0                            # another partition's: its turn
    echo "$_pt_i" > "$_pt_d/$_pt_k.used"
    # the instruction is aged by the seconds since it came: its end counted from then, the neighbour's own end with it
    _pt_u=$(jq -r '.until_s // 0' "$_pt_d/$_pt_k.json" 2>/dev/null); _pt_at=$(cat "$_pt_d/$_pt_k.at" 2>/dev/null); _pt_now=$(date +%s)
    case "$_pt_u/$_pt_at/$_pt_now" in *[!0-9/]*|*//*|/*|*/) _pt_left=0 ;; *) _pt_left=$((_pt_u - (_pt_now - _pt_at))) ;; esac
    rm -f "$_pt_d/$_pt_k.run"
    if [ "$_pt_left" -lt 10 ]; then
        _pf_say "$1: $_pt_i: the instruction ended before this cloner got to it -- all of it over HTTP"
        _pw_result "$1" "$_pt_i none the instruction ended before this cloner got to it"; rm -f "$_pt_d/$_pt_k.json"; return 0
    fi
    ( umask 077; jq -c --argjson u "$_pt_left" '.until_s = $u' "$_pt_d/$_pt_k.json" > "$_pt_d/$_pt_k.run" ) 2>/dev/null \
        && jq -r '.ranges[] | "\(.[0])-\(.[1])"' "$3" > "$_pt_d/$_pt_k.list" 2>/dev/null && [ -s "$_pt_d/$_pt_k.list" ] \
        || { _pf_say "$1: $_pt_i: the instruction or the list was not read -- all of it over HTTP"; rm -f "$_pt_d/$_pt_k.run" "$_pt_d/$_pt_k.json"; return 0; }
    # shellcheck disable=SC2034 # PF_STATE is read by peerfill.sh
    PF_STATE="${CHUNK_STATE:-}"                      # what a neighbour writes enters the drawer's own state file, as an HTTP fetch's would
    peerfill_run "$1" "$_pt_d/$_pt_k.list" "$_pt_d/$_pt_k.run"; _pt_rc=$?
    PF_STATE=""; rm -f "$_pt_d/$_pt_k.run" "$_pt_d/$_pt_k.json"                    # the token is not kept past its use
    [ ! -s "$NF_DIR/result" ] || _pw_result "$1" "$(cat "$NF_DIR/result")"
    [ "$_pt_rc" != 2 ] || return 0                   # the context was not read: the list is as it was, all of it over HTTP
    if awk '$0 !~ /^[0-9]+-[0-9]+$/ { bad = 1 } END { exit bad }' "$_pt_d/$_pt_k.list" 2>/dev/null; then :; else
        _pf_say "$1: the list a neighbour left is not a list -- all of it over HTTP"; return 0
    fi
    # its counter and its ledger follow what was written, as after any pass (a stale one costs a report, not a chunk)
    if command -v _chunk_counters >/dev/null 2>&1 && [ -n "${CHUNK_IDX:-}" ]; then _chunk_counters "$2" "$1" || :; fi
    if [ ! -s "$_pt_d/$_pt_k.list" ]; then echo '{"ranges":[]}' > "$3"; return 1; fi
    awk -F- 'BEGIN { printf "{\"ranges\":[" } { printf "%s[%d,%d]", (NR > 1 ? "," : ""), $1, $2 } END { print "]}" }'         "$_pt_d/$_pt_k.list" > "$3.new" && mv "$3.new" "$3"
    return 0
}
