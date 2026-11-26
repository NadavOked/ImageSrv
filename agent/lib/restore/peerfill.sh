# peerfill.sh -- B29: what a drawer lacks, from a drawer of a neighbour CLONER the server paired it with.
# POSIX sh (busybox ash). Needs neighborfill.sh (its list, piece, ceiling and re-read helpers).
#
#   peer_serve_take <progress-reply>                         the cloner that HOLDS the chunks
#   peerfill_run <drawer> <missing-list-file> <instruction>  the cloner that LACKS them
#
# Both act only on the server's word (the brain, server/brain/peer.py, brain_peer_mode=enforce; in
# shadow, the default, the server says nothing and nothing here runs). The neighbour is NOT trusted:
#
#   - nothing listens and nothing connects without an instruction of the server, for one pairing:
#     one id, one token (32 hex), one asker, one port, one ceiling, one end. The server names both
#     sides; neither takes an address, a port or a list from the other;
#   - the server of frames (`chunkcast serve`) listens on the address the server named for this
#     cloner -- never 0.0.0.0 -- answers only --allow <the asker> carrying the token, reads its own
#     disk at --read-mibs at most (counted with neighborfill.sh's reads in the same 100MiB/s), and
#     ends by itself at --until-s, or with the shell that started it;
#   - what arrives goes through `chunkcast apply` with THIS cloner's $CHUNK_IMAGE_ARGS -- the index,
#     its XXH64 from the manifest and the head, all from the image server (#3116) -- and the BLAKE3
#     list of the partition in them (--b3-file, chunkb3.sh, #3332; Aegis 15/11: XXH64 does not stand
#     against a hostile neighbour). No BLAKE3 list in the arguments = no P2P at all;
#   - then re-read from the drawer itself past its cache (neighborfill.sh's _nf_reread). Only what
#     read back whole counts; everything else stays in the list, for HTTP (principle 5);
#   - curl: the token in a -K file (0600, never argv or log), --interface <this cloner's address>,
#     --max-time to the pairing's end, and below the floor is a neighbour that failed. The floor is the
#     brain's word (A27, #3659): the instruction's speed_kibs for speed_s seconds. A field that is not
#     there (an older server) = PF_SPEED_BPS / PF_SPEED_S, the agent's own, and the log says so; a field
#     that is there and is not a whole number refuses the instruction (like every other field of it).
#
# peerfill_run: 0 = every chunk of the list written and re-read. 1 = some left (the list rewritten),
# 2 = the list, the context or the instruction not read (the list left as it was: all for HTTP).
# $NF_DIR/result is the word for the server (targets[].peer_fill_result): "<id> ok|bad <why>".

PF_SPEED_BPS="${PF_SPEED_BPS:-1048576}"   # A27: the floor when the instruction has none: below this for PF_SPEED_S seconds, the neighbour failed
PF_SPEED_S="${PF_SPEED_S:-30}"

_pf_say() { log "peer fill: $*" >&2; }

_pf_ip() {   # $1 -> 0 = a dotted IPv4 address, nothing else
    printf '%s\n' "$1" | awk -F. 'NF != 4 { exit 1 } { for (i = 1; i <= 4; i++) if ($i !~ /^[0-9][0-9]?[0-9]?$/ || $i + 0 > 255) exit 1 }'
}

_pf_num() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ "${#1}" -le 6 ] && [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]; }

_pf_hex() { [ "${#1}" -ge "$2" ] && [ "${#1}" -le "$3" ] && case "$1" in *[!0-9a-f]*) return 1 ;; esac; }

_pf_fields() {   # $1 = a json object file, $2 = jq path, $3.. = field names -> one line, a word each ("-" = absent)
    _pff_j="$1"; _pff_p="$2"; shift 2
    _pff_q=""; for _pff_k in "$@"; do _pff_q="$_pff_q${_pff_q:+, }(.$_pff_k // \"-\" | tostring | gsub(\"[[:space:]]\"; \"?\"))"; done
    jq -r "$_pff_p | [$_pff_q] | join(\" \")" "$_pff_j" 2>/dev/null
}

# --- the cloner that holds the chunks ----------------------------------------------------------------

_peer_serve_one() {   # $1 = reply, $2 = its place in .peer_serve. 0 = serving (or already); 1 = refused, said
    _ps_line=$(_pf_fields "$1" ".peer_serve[$2]" id dev part listen port allow token rate_mibs until_s) || return 1
    read -r _ps_id _ps_dev _ps_p _ps_l _ps_port _ps_a _ps_tok _ps_r _ps_u <<EOF
$_ps_line
EOF
    _pf_hex "$_ps_id" 8 32 || { _pf_say "an instruction to serve has no id -- not served"; return 1; }
    _ps_d="$RUN_DIR/pserve"; mkdir -p "$_ps_d" || return 1
    [ ! -e "$_ps_d/$_ps_id.started" ] || return 0                 # the server says it again in every answer: once is enough
    _ps_why=""
    case "$_ps_dev" in ''|-|*[!a-z0-9]*) _ps_why="the drawer is not a device name" ;; esac
    _pf_num "$_ps_p" 0 65535 || _ps_why="the partition is not a number"
    _pf_ip "$_ps_l" || _ps_why="the address to listen on is not an IPv4 address"
    [ "$_ps_l" != 0.0.0.0 ] || _ps_why="0.0.0.0 is every card -- only the one the server named"
    _pf_ip "$_ps_a" || _ps_why="the cloner that may ask is not an IPv4 address"
    _pf_num "$_ps_port" 1024 65535 || _ps_why="the port is not a port"
    _pf_hex "$_ps_tok" 32 32 || _ps_why="the token is not 32 hex digits"
    _pf_num "$_ps_r" 1 100 || _ps_why="the ceiling is not 1-100 MiB/s"
    _pf_num "$_ps_u" 1 3600 || _ps_why="the end is not 1-3600 s"
    if [ -z "$_ps_why" ]; then
        _ps_s=$(cat "$RUN_DIR/targets/$_ps_dev/state" 2>/dev/null) || _ps_s="not read"
        case "$_ps_s" in writing|verifying|done) ;; *) _ps_why="its state is '$_ps_s'" ;; esac
        for _ps_f in pulled foreign dead; do [ ! -e "$RUN_DIR/targets/$_ps_dev/$_ps_f" ] || _ps_why="it is marked $_ps_f"; done
        [ -r "${CHUNK_DIR:-$RUN_DIR/chunk}/p$_ps_p.args" ] || _ps_why="the image arguments of partition $_ps_p are not here"
    fi
    if [ -z "$_ps_why" ]; then
        _ps_c=$(_nf_cap) || _ps_c=0                                   # what neighborfill.sh's ceiling leaves (B29 included)
        [ "$_ps_c" -ge "$_ps_r" ] || _ps_r=$_ps_c
        [ "$_ps_r" -gt 0 ] || _ps_why="nothing is left of the 100MiB/s of this cloner's reads"
    fi
    : > "$_ps_d/$_ps_id.started"
    [ -z "$_ps_why" ] || { _pf_say "$_ps_id: $_ps_dev is not served -- $_ps_why"; return 1; }
    ( umask 077; printf '%s\n' "$_ps_tok" > "$_ps_d/$_ps_id.token" ) || return 1
    echo "$_ps_r" > "$_ps_d/$_ps_id.mibs.new" && mv "$_ps_d/$_ps_id.mibs.new" "$_ps_d/$_ps_id.mibs" || return 1
    # shellcheck disable=SC2046 # the image arguments are a word list on purpose
    chunkcast serve --listen "$_ps_l:$_ps_port" --token-file "$_ps_d/$_ps_id.token" --allow "$_ps_a" \
        $(cat "${CHUNK_DIR:-$RUN_DIR/chunk}/p$_ps_p.args") --device "$(partition_node "$_ps_dev" "$_ps_p")" \
        --level "${NF_LEVEL:-${CAPTURE_LEVEL:-3}}" --until-s "$_ps_u" --read-mibs "$_ps_r" \
        < /dev/null > "$_ps_d/$_ps_id.out" 2>> "$LOG_FILE" &
    echo "$!" > "$_ps_d/$_ps_id.pid"; _ps_n=0
    until grep -qx serving "$_ps_d/$_ps_id.out" 2>/dev/null || ! kill -0 "$(cat "$_ps_d/$_ps_id.pid")" 2>/dev/null || [ "$_ps_n" -ge 100 ]; do
        sleep 0.1 2>/dev/null || sleep 1; _ps_n=$((_ps_n + 1))
    done
    if grep -qx serving "$_ps_d/$_ps_id.out" 2>/dev/null; then
        _pf_say "$_ps_id: $_ps_dev partition $_ps_p served to $_ps_a on $_ps_l:$_ps_port, ${_ps_r}MiB/s at most, ${_ps_u}s at most"
        return 0
    fi
    kill "$(cat "$_ps_d/$_ps_id.pid")" 2>/dev/null; rm -f "$_ps_d/$_ps_id.mibs" "$_ps_d/$_ps_id.pid"
    _pf_say "$_ps_id: the frame server for $_ps_dev did not start -- not served"; return 1
}

peer_serve_reap() {   # the frame servers that ended (--until-s, the shell, a kill) give their share of the ceiling back
    for _pr_f in "$RUN_DIR"/pserve/*.pid; do
        [ -f "$_pr_f" ] || continue
        kill -0 "$(cat "$_pr_f")" 2>/dev/null && continue
        rm -f "${_pr_f%.pid}.mibs" "${_pr_f%.pid}.token" "$_pr_f"
        _pf_say "${_pr_f##*/}: the frame server for a neighbour cloner ended"
    done
}

peer_serve_take() {   # $1 = a progress reply. 0 = every instruction in it is served or refused by name; 1 = not read
    peer_serve_reap
    [ -s "$1" ] || return 0
    _pt_n=$(jq -r '(.peer_serve // []) | length' "$1" 2>/dev/null)
    case "$_pt_n" in ''|*[!0-9]*) _pf_say "the answer's peer_serve was not read -- nothing is served"; return 1 ;; esac
    _pt_i=0
    while [ "$_pt_i" -lt "$_pt_n" ]; do _peer_serve_one "$1" "$_pt_i" || :; _pt_i=$((_pt_i + 1)); done   # each one refused is said by name
}

# --- the cloner that lacks them ------------------------------------------------------------------------

_pf_feed() {   # $1 = pieces, $2 = MiB/s, $3 = the end (cs). stdout = the frames, in order. $NF_DIR/why: "<first> bad|stop"
    while read -r _pfe_a _pfe_b _pfe_f _pfe_t; do
        _pfe_0=$(_nf_now) || { echo "$_pfe_a stop" > "$NF_DIR/why"; return 1; }
        _pfe_left=$((($3 - _pfe_0) / 100))
        [ "$_pfe_left" -gt 0 ] || { echo "$_pfe_a stop" > "$NF_DIR/why"; return 1; }
        curl -sS --fail -K "$NF_DIR/peer.curl" --interface "$PF_ME" --connect-timeout "${HTTP_TIMEOUT:-10}" \
            --max-time "$_pfe_left" --speed-limit "$PF_SB" --speed-time "$PF_SS" -r "$_pfe_f-$_pfe_t" \
            "http://$PF_IP:$PF_PORT/" 2>> "$LOG_FILE" || { echo "$_pfe_a bad" > "$NF_DIR/why"; return 1; }
        _pfe_due=$((_pfe_0 + (_pfe_b - _pfe_a + 1) * 400 / $2))     # 4MiB a chunk at MiB/s, in centiseconds
        while _pfe_n=$(_nf_now) && [ "$_pfe_n" -lt "$_pfe_due" ]; do sleep 0.05 2>/dev/null || sleep 1; done
    done < "$1"
}

peerfill_run() {   # $1 = drawer, $2 = the missing list, $3 = the instruction (one peer_fill entry, a json file)
    NF_DIR="${CHUNK_DIR:-$RUN_DIR/chunk}/pf.$1"; rm -rf "$NF_DIR"; mkdir -p "$NF_DIR" || return 2
    _pf_l=$(_pf_fields "$3" "." id part ip port me token rate_mibs until_s speed_kibs speed_s) || { _pf_say "$1: the instruction was not read"; return 2; }
    read -r PF_ID NF_P PF_IP PF_PORT PF_ME _pf_tok _pf_r _pf_u _pf_sk _pf_ss <<EOF
$_pf_l
EOF
    _pf_why=""
    _pf_hex "$PF_ID" 8 32 || _pf_why="no id"
    _pf_num "$NF_P" 0 65535 || _pf_why="the partition is not a number"
    _pf_ip "$PF_IP" && [ "$PF_IP" != 0.0.0.0 ] || _pf_why="the neighbour's address is not an IPv4 address"
    _pf_ip "$PF_ME" && [ "$PF_ME" != 0.0.0.0 ] || _pf_why="this cloner's address is not an IPv4 address"
    _pf_num "$PF_PORT" 1024 65535 || _pf_why="the port is not a port"
    _pf_hex "$_pf_tok" 32 32 || _pf_why="the token is not 32 hex digits"
    _pf_num "$_pf_r" 1 100 || _pf_why="the ceiling is not 1-100 MiB/s"
    _pf_num "$_pf_u" 1 3600 || _pf_why="the end is not 1-3600 s"
    _pf_dflt=""   # the floor (A27): the brain's word, or -- not there -- the agent's own, said below
    if [ "$_pf_sk" = - ]; then PF_SB=$PF_SPEED_BPS; _pf_dflt=" speed_kibs=$((PF_SPEED_BPS / 1024))"
    elif _pf_num "$_pf_sk" 1 99999; then PF_SB=$((_pf_sk * 1024)); else _pf_why="the speed floor is not a whole number of KiB/s"; fi
    if [ "$_pf_ss" = - ]; then PF_SS=$PF_SPEED_S; _pf_dflt="$_pf_dflt speed_s=$PF_SPEED_S"
    elif _pf_num "$_pf_ss" 1 3600; then PF_SS=$_pf_ss; else _pf_why="the speed floor's seconds are not a whole number"; fi
    _pf_note=""; [ -z "$_pf_dflt" ] || _pf_note="; no word from the brain for:$_pf_dflt -- the agent's own defaults (A27)"
    [ "${CHUNK_IDX##*/}" = "p$NF_P.idx" ] || _pf_why="the index here ($CHUNK_IDX) is not of partition $NF_P"
    [ -z "$_pf_why" ] || { _pf_say "$1: the instruction is refused -- $_pf_why"; return 2; }
    echo "$PF_ID bad not started" > "$NF_DIR/result"
    # #3332: chunkb3.sh put the list into the arguments, or nowhere. The arguments are the evidence, not a path built here: a
    # drawer's completion in the background runs in a directory of its own (drawerback.sh), the round's list is in another
    PF_B3=$(printf '%s\n' "$CHUNK_IMAGE_ARGS" | awk '{ for (i = 1; i < NF; i++) if ($i == "--b3-file") { print $(i + 1); exit } }')
    [ -n "$PF_B3" ] && [ -f "$PF_B3" ] \
        || { _pf_say "$1: no BLAKE3 list of partition $NF_P ($PF_B3) -- XXH64 does not stand against a neighbour, nothing is read from it"
             echo "$PF_ID none no blake3" > "$NF_DIR/result"; return 1; }
    _pf_n=$(awk 'NR == 1 { print $9 }' "$CHUNK_IDX" 2>/dev/null)
    case "$_pf_n" in ''|*[!0-9]*) _pf_say "$1: the index ($CHUNK_IDX) was not read"; return 2 ;; esac
    [ -f "$2" ] && [ -r "$2" ] || { _pf_say "$1: the list $2 was not read"; return 2; }
    _nf_chunks "$2" "$_pf_n" > "$NF_DIR/missing" || { _pf_say "$1: $2 is not a list of chunks of this partition"; return 2; }
    [ -s "$NF_DIR/missing" ] || { echo "$PF_ID ok nothing missing" > "$NF_DIR/result"; return 0; }
    jq -r '.ranges[] | "\(.[0])-\(.[1])"' "$3" > "$NF_DIR/offered.list" 2>/dev/null \
        && _nf_chunks "$NF_DIR/offered.list" "$_pf_n" > "$NF_DIR/offered" || { _pf_say "$1: the instruction's ranges are not of this partition"; return 2; }
    _nf_only "$NF_DIR/offered" 1 > "$NF_DIR/want"
    NF_NODE=$(partition_node "$1" "$NF_P")
    ( umask 077; printf 'header = "X-Imagesrv-Serve-Token: %s"\n' "$_pf_tok" > "$NF_DIR/peer.curl" ) || return 2
    _pf_end=$(_nf_now) || { _pf_say "$1: no clock -- nothing is read"; return 2; }
    _pf_end=$((_pf_end + _pf_u * 100))
    _pf_k=$(grep -c . "$NF_DIR/want")
    _pf_say "$1 ($NF_NODE): $_pf_k chunks of partition $NF_P from $PF_IP:$PF_PORT, ${_pf_r}MiB/s, ${_pf_u}s at most ($PF_ID); below ${PF_SB}B/s for ${PF_SS}s = the neighbour failed$_pf_note"
    if [ "$_pf_k" -gt 0 ] && _nf_pieces "$NF_DIR/want" > "$NF_DIR/pieces"; then
        awk '{ printf "%s%s-%s", (NR > 1 ? "," : ""), $1, $2 } END { print "" }' "$NF_DIR/pieces" > "$NF_DIR/list"
        # shellcheck disable=SC2086 # the image arguments are a word list on purpose
        _pf_feed "$NF_DIR/pieces" "$_pf_r" "$_pf_end" | chunkcast apply --part "$NF_P" $CHUNK_IMAGE_ARGS \
            --state "${PF_STATE:-$NF_DIR/peer.state}" --chunks-file "$NF_DIR/list" "$NF_NODE" > "$NF_DIR/apply.out" 2>> "$LOG_FILE"
        _pf_rc=$?
        read -r _pf_wk _pf_ww 2>/dev/null < "$NF_DIR/why" || _pf_ww=""
        case "$_pf_ww|$_pf_rc" in
            *"|2") echo "$PF_ID none apply did not start (rc=2): this cloner's, not the neighbour's" > "$NF_DIR/result" ;;
            bad*) echo "$PF_ID bad the piece from chunk $_pf_wk did not arrive whole" > "$NF_DIR/result" ;;
            stop*) echo "$PF_ID bad the pairing ended before chunk $_pf_wk" > "$NF_DIR/result" ;;
            "|0") echo "$PF_ID ok" > "$NF_DIR/result" ;;
            *) echo "$PF_ID bad apply rc=$_pf_rc (a frame was short, or not the index's)" > "$NF_DIR/result" ;;
        esac
    fi
    rm -f "$NF_DIR/peer.curl"
    awk -v n="$NF_NODE" '$1 == n && $2 == "ok" && $4 != "-" { m = split($4, r, ","); for (i = 1; i <= m; i++) {
        split(r[i], p, "-"); for (k = p[1] + 0; k <= p[2] + 0; k++) print k } }' "${PF_STATE:-$NF_DIR/peer.state}" 2>/dev/null \
        | sort -n -u > "$NF_DIR/durable"
    _nf_only "$NF_DIR/durable" 1 > "$NF_DIR/written"
    # the re-read: a frame server of our own drawer on the loopback, with a token of its own
    ( umask 077; od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > "$NF_DIR/token"
      printf 'header = "X-Imagesrv-Serve-Token: %s"\n' "$(cat "$NF_DIR/token")" > "$NF_DIR/token.curl" ) 2>> "$LOG_FILE"
    NF_LEVEL="${NF_LEVEL:-${CAPTURE_LEVEL:-3}}"
    _nf_reread "$NF_DIR/written" || : > "$NF_DIR/verified"       # not re-read = not written (principle 5)
    _nf_only "$NF_DIR/verified" 0 > "$NF_DIR/left" || return 2
    _nf_runs "$NF_DIR/left" > "$2.pf" && mv "$2.pf" "$2" || { _pf_say "$1: $2 was not rewritten"; return 2; }
    _pf_v=$(grep -c . "$NF_DIR/verified"); _pf_left=$(grep -c . "$NF_DIR/left")
    _pf_say "$1: $_pf_v chunks written from $PF_IP and re-read whole; $_pf_left left for HTTP ($(cut -d' ' -f2- "$NF_DIR/result"))"
    [ "$_pf_left" = 0 ]
}

peerfill_result_json() {   # $1 = drawer -> ',"peer_fill_result":{"id","result","why"}' for its progress target, or nothing
    _pj=$(cat "$RUN_DIR/targets/$1/pf_result" 2>/dev/null) || _pj=$(cat "${CHUNK_DIR:-$RUN_DIR/chunk}/pf.$1/result" 2>/dev/null) || return 0   # peerwire.sh keeps it by the drawer
    printf '%s\n' "$_pj" | jq -Rc 'split(" ") | {id: .[0], result: .[1], why: (.[2:] | join(" "))}' 2>/dev/null \
        | sed 's/^/,"peer_fill_result":/'
}
