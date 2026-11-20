# neighborfill.sh -- #3181: what a drawer lacks, from the drawers beside it in the same cloner.
# POSIX sh (busybox ash).
#
#   neighborfill_run <drawer> <missing-list-file>
#
# A drawer that came back (or a disk pushed in mid-round) lacks chunks of the partition being
# written. Its neighbours in the same cloner hold them -- their durable ledger says which, and
# a chunk named durable there was flushed (chunkledger.sh). This reads them over SATA, no network:
#
#   - the source: for each chunk, a neighbour whose ledger (targets/<n>/ledger/durable.<part>)
#     holds it. Never the drawer itself, a disk that is the source of a local clone ($LC/source),
#     a red one (SMART fail), or one whose state is not "writing"/"verifying" (failed, skipped,
#     cancelled, pulled, not read) -- a neighbour is a source by positive evidence only;
#   - shared: a chunk more than one neighbour holds goes to the one given least so far, and
#     they are read in parallel -- each slows its own write less (the issue, M2);
#   - read and checked BEFORE a byte is written: `chunkcast serve` reads the chunk from the
#     neighbour's node and refuses one whose XXH64 is not the index's (the disk changed, or never
#     held it), and `chunkcast apply` checks it again before it writes -- chunkfetch.sh's way,
#     with the neighbour's node instead of the image server. What did not arrive whole stays missing;
#   - the ceiling: all the neighbours' reads together. The brain's word (A27, #3659): NF_CAP_MIBS, or the
#     file $RUN_DIR/neighborfill.cap -- the lowest word wins, read again before every piece. NO word =
#     100MiB/s, NF_CAP_MAX_MIBS (M1/M2, metal 14/11: above ~100MiB/s of reads the whole cloner wrote
#     slower), and the log says so. A word that is there and is not a number stops the fill (principle 5:
#     "not read" is not "no limit"). Each reader takes its share, cap/readers, paced by the clock at the
#     end of each piece (4MiB a chunk, NF_BATCH chunks a piece);
#   - re-read: what was written is read back from the drawer itself, past its page cache
#     (blockdev --flushbufs, as readback.sh), through the same XXH64 check, under the same ceiling.
#
# 0 = every chunk of the list was written AND re-read whole. Anything else = the list file is
# rewritten with what is still missing ("A-B" a line), for HTTP -- the caller's (drawerback.sh).
# 2 = the list or the context could not be read; the file is left as it was (all of it missing).
# The context is chunkfetch.sh's: CHUNK_IDX, CHUNK_IMAGE_ARGS, CHUNK_DIR; the partition is NF_PART,
# or the number in the index's name (p<N>.idx); NF_LEVEL is chunkcut's level (CAPTURE_LEVEL); NF_ONLY, when not empty, names the only neighbours read (the brain's choice).
# Nothing here reads the server, and nothing is told to it: the drawer's own state and ledger do that.

NF_CAP_MAX_MIBS=100                 # A27: no word from the brain = this (said in the log); a word replaces it
NF_BATCH="${NF_BATCH:-8}"           # chunks a piece: one request, then the clock
NF_PORT="${NF_PORT:-47960}"         # loopback only: a frame server per neighbour, 16 ports each
NF_BAD_MAX="${NF_BAD_MAX:-3}"       # pieces a neighbour may fail before it is not asked again: no word ($RUN_DIR/neighborfill.bad) = this

_nf_say() { log "neighbour fill: $*" >&2; }   # stderr: some of these run where stdout is frames or a list

_nf_now() {   # centiseconds of uptime. 1 = no clock: the ceiling cannot be kept, and nothing more is read
    _nw=$(awk '{ printf "%d", $1 * 100 }' "${SYSROOT:-}/proc/uptime" 2>/dev/null)
    case "$_nw" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s' "$_nw"
}

_nf_cap() {   # -> the ceiling in MiB/s: the lowest word, or NF_CAP_MAX_MIBS when there is none. 1 = a word is there and is not a number
    _nc=""; _nc_f="$RUN_DIR/neighborfill.cap"; _nc_w=""
    if [ -e "$_nc_f" ]; then _nc_w=$(cat "$_nc_f" 2>/dev/null) || return 1; [ -n "$_nc_w" ] || return 1; fi
    for _nc_v in "${NF_CAP_MIBS:-}" "$_nc_w"; do
        [ -n "$_nc_v" ] || continue
        case "$_nc_v" in *[!0-9]*) return 1 ;; esac
        _nc_v=${_nc_v#"${_nc_v%%[!0]*}"}; _nc_v=${_nc_v:-0}   # "010" is ten, not an octal eight
        if [ -z "$_nc" ] || [ "$_nc_v" -lt "$_nc" ]; then _nc=$_nc_v; fi
    done
    [ -n "$_nc" ] || _nc=$NF_CAP_MAX_MIBS
    # B29: what a neighbour CLONER reads from this one (peerfill.sh) comes out of the same 100MiB/s. Not read = stop
    _nc_p=$(cat "$RUN_DIR"/pserve/*.mibs 2>/dev/null | awk '/^[0-9]+$/ { s += $1; next } { bad = 1 } END { if (bad) exit 1; print s + 0 }') || return 1
    _nc=$((_nc > _nc_p ? _nc - _nc_p : 0))
    printf '%s' "$_nc"
}

_nf_bad_word() {   # -> the brain's word: pieces a neighbour may fail ($RUN_DIR/neighborfill.bad, fillword.sh). 1 = no word (not a whole number >= 1 is none)
    _nb=$(cat "$RUN_DIR/neighborfill.bad" 2>/dev/null) || return 1
    case "$_nb" in ''|*[!0-9]*) return 1 ;; esac
    _nb=${_nb#"${_nb%%[!0]*}"}; [ -n "$_nb" ] || return 1
    printf '%s' "$_nb"
}

_nf_limits() {   # A27: NF_BAD_N = pieces a neighbour may fail. What is the agent's own default (no word from the brain) is said, once
    _nl=""
    NF_BAD_N=$(_nf_bad_word) || { NF_BAD_N=$NF_BAD_MAX; _nl="$_nl bad_max=$NF_BAD_MAX"; }
    [ -e "$RUN_DIR/neighborfill.cap" ] || [ -n "${NF_CAP_MIBS:-}" ] || _nl="$_nl ceiling=${NF_CAP_MAX_MIBS}MiB/s"
    [ -z "$_nl" ] || _nf_say "no word from the brain for:$_nl -- the agent's own defaults (A27)"
}

_nf_pace() {   # $1 = when the piece began (cs), $2 = its chunks, $3 = readers. Waits out its share. 1 = stop reading
    _np_c=$(_nf_cap) || { _nf_say "the ceiling ($RUN_DIR/neighborfill.cap or NF_CAP_MIBS) is not a number -- reading stops"; return 1; }
    [ "$_np_c" -gt 0 ] || { _nf_say "the ceiling is 0 -- reading stops"; return 1; }
    _np_due=$(($1 + $2 * 400 * $3 / _np_c))   # 4MiB a chunk at cap/readers MiB/s, in centiseconds
    while :; do
        _np_n=$(_nf_now) || { _nf_say "no clock (/proc/uptime) -- the ceiling cannot be kept, reading stops"; return 1; }
        [ "$_np_n" -lt "$_np_due" ] || return 0
        sleep 0.05 2>/dev/null || sleep 1   # a sleep that refuses fractions costs a whole second: slower, never faster
    done
}

_nf_chunks() {   # $1 = a list ("A-B" or "K", by commas or lines), $2 = chunks in the index -> one chunk a line, ascending
    tr -s ', \t\r' '\n\n\n\n' < "$1" | awk -v n="$2" 'NF == 0 { next }
        $0 !~ /^[0-9]+(-[0-9]+)?$/ { bad = 1; exit }
        { m = split($0, p, "-"); lo = p[1] + 0; hi = (m == 2 ? p[2] : p[1]) + 0
          if (hi < lo || hi >= n + 0) { bad = 1; exit }
          for (k = lo; k <= hi; k++) print k }
        END { exit bad }' > "$NF_DIR/chunks.raw" || return 1
    sort -n -u "$NF_DIR/chunks.raw"
}

_nf_only() {   # $1 = chunks, $2 = 1 (those of the missing list in it) | 0 (not in it). Not NR == FNR: a first file may be empty
    awk -v f="$1" -v keep="$2" 'BEGIN { while ((getline l < f) > 0) w[l] = 1 } ($1 in w) == keep + 0' "$NF_DIR/missing"
}

_nf_runs() { awk 'NR == 1 || $1 != p + 1 { if (NR > 1) print s "-" p; s = $1 } { p = $1 } END { if (NR) print s "-" p }' "$1"; }

_nf_pieces() {   # $1 = chunks (a line each) -> "first last from to": runs of NF_BATCH chunks at most, and their bytes in the partition file
    awk -v b="$NF_BATCH" '{ if (NR == 1 || $1 != p + 1 || $1 - s >= b) { if (NR > 1) print s, p; s = $1 } p = $1 }
        END { if (NR) print s, p }' "$1" > "$1.runs" || return 1
    awk 'NR == FNR { r[++n] = $0; next } FNR > 1 { o[FNR - 2] = $1; z[FNR - 2] = $2 }
        END { for (i = 1; i <= n; i++) { split(r[i], q, " ")
                  if (!(q[1] in o) || !(q[2] in o)) exit 1
                  print q[1], q[2], o[q[1]], o[q[2]] + z[q[2]] - 1 } }' "$1.runs" "$CHUNK_IDX"
}

_nf_neighbours() {   # $1 = drawer -> the drawers that may be read from, a name a line. Each one left out is said, with why
    for _nn_t in "$RUN_DIR"/targets/*/; do
        _nn=${_nn_t%/}; _nn=${_nn##*/}
        [ -d "$_nn_t" ] && [ "$_nn" != "$1" ] || continue
        _nn_s=$(cat "$_nn_t/state" 2>/dev/null) || _nn_s="not read"
        case "$_nn_s" in writing|verifying) _nn_why="" ;; *) _nn_why="its state is '$_nn_s'" ;; esac
        for _nn_f in pulled foreign dead; do [ ! -e "$_nn_t$_nn_f" ] || _nn_why="it is marked $_nn_f"; done
        [ "$(smart_field "$_nn" 1)" != fail ] || _nn_why="it is red (SMART)"
        [ "$(cat "${LC:-$RUN_DIR/lc}/source" 2>/dev/null)" != "$_nn" ] || _nn_why="it is the source disk"
        if [ -z "$_nn_why" ] && [ -n "${NF_ONLY:-}" ]; then case " $NF_ONLY " in *" $_nn "*) ;; *) _nn_why="the brain did not name it (NF_ONLY: $NF_ONLY)" ;; esac; fi   # fillword.sh: none named = all
        [ -n "$_nn_why" ] || [ -r "$_nn_t/ledger/durable.$NF_P" ] || _nn_why="no durable ledger for partition $NF_P"
        if [ -n "$_nn_why" ]; then _nf_say "$_nn is not read for $1 -- $_nn_why"; else echo "$_nn"; fi
    done
}

_nf_plan() {   # $1 = chunks, $@ = neighbours -> $NF_DIR/want.<n> for each; what none holds -> $NF_DIR/none
    _npl_c="$1"; shift
    for _npl_n in "$@"; do
        echo "@ $_npl_n"
        cat "$RUN_DIR/targets/$_npl_n/ledger/durable.$NF_P" 2>/dev/null || _nf_say "the ledger of $_npl_n was not read -- it holds nothing here"
    done | awk -v dir="$NF_DIR" 'NR == FNR { want[++w] = $1; next }
        $1 == "@" { g++; name[g] = $2; next }
        { m = split($0, p, "-"); if (m == 2 && p[1] ~ /^[0-9]+$/ && p[2] ~ /^[0-9]+$/) { c[g]++; lo[g, c[g]] = p[1] + 0; hi[g, c[g]] = p[2] + 0 } }
        END { for (i = 1; i <= w; i++) { k = want[i] + 0; best = 0
                  for (h = 1; h <= g; h++) {   # the ledgers run ascending, as the chunks do: one pointer each
                      if (!j[h]) j[h] = 1
                      while (j[h] <= c[h] && hi[h, j[h]] < k) j[h]++
                      if (j[h] <= c[h] && lo[h, j[h]] <= k && (!best || got[h] < got[best])) best = h }
                  if (best) { print k > (dir "/want." name[best]); got[best]++ } else print k > (dir "/none") } }' "$_npl_c" -
}

_nf_serve() {   # $1 = node, $2 = tag, $3 = first port. NF_SV_PORT, NF_SV_PID = a frame server on the loopback. 1 = none started
    _ns_port=$3; _ns_end=$(($3 + 16))
    while [ "$_ns_port" -lt "$_ns_end" ]; do
        : > "$NF_DIR/$2.serve.out"; : > "$NF_DIR/$2.serve.err"
        # shellcheck disable=SC2086 # the image arguments are a word list on purpose
        chunkcast serve --listen "127.0.0.1:$_ns_port" --token-file "$NF_DIR/token" $CHUNK_IMAGE_ARGS --device "$1" \
            --level "$NF_LEVEL" < /dev/null > "$NF_DIR/$2.serve.out" 2> "$NF_DIR/$2.serve.err" &
        NF_SV_PID=$!; _ns_n=0
        until grep -qx serving "$NF_DIR/$2.serve.out" 2>/dev/null || ! kill -0 "$NF_SV_PID" 2>/dev/null || [ "$_ns_n" -ge 100 ]; do
            sleep 0.1 2>/dev/null || sleep 1; _ns_n=$((_ns_n + 1))
        done
        if grep -qx serving "$NF_DIR/$2.serve.out" 2>/dev/null; then NF_SV_PORT=$_ns_port; return 0; fi
        kill "$NF_SV_PID" 2>/dev/null; wait "$NF_SV_PID" 2>/dev/null
        cat "$NF_DIR/$2.serve.err" >> "$LOG_FILE" 2>/dev/null
        grep -q "cannot listen" "$NF_DIR/$2.serve.err" 2>/dev/null || return 1   # another port helps only a port taken
        _ns_port=$((_ns_port + 1))
    done
    return 1
}

_nf_unserve() { kill "$NF_SV_PID" 2>/dev/null; wait "$NF_SV_PID" 2>/dev/null; cat "$NF_DIR/$1.serve.err" >> "$LOG_FILE" 2>/dev/null; }

_nf_get() {   # $1 = from, $2 = to, $3.. = curl's own: one piece of the frame server, its whole frames
    _ng_r="$1-$2"; shift 2
    curl -sS --fail -K "$NF_DIR/token.curl" --connect-timeout "${HTTP_TIMEOUT:-10}" --speed-limit 1 \
        --speed-time "${WAIT_STREAM_STALL_S:-120}" -r "$_ng_r" "$@" "http://127.0.0.1:$NF_SV_PORT/" 2>> "$LOG_FILE"
}

_nf_feed() {   # $1 = pieces, $2 = readers, $3 = the word file. stdout = the frames, in order. The word: "<first> bad" | "stop"
    while read -r _nf_a _nf_b _nf_f _nf_t; do
        _nf_t0=$(_nf_now) || { echo stop > "$3"; return 1; }
        _nf_get "$_nf_f" "$_nf_t" || { echo "$_nf_a bad" > "$3"; return 1; }
        _nf_pace "$_nf_t0" $((_nf_b - _nf_a + 1)) "$2" || { echo stop > "$3"; return 1; }
    done < "$1"
}

_nf_worker() {   # $1 = neighbour, $2 = its number (its ports), $3 = readers. In the background. Its evidence: $NF_DIR/<n>.state
    _nw_d="$NF_DIR/$1"; _nw_bad=0
    _nf_pieces "$NF_DIR/want.$1" > "$_nw_d.pieces" || { _nf_say "the chunks for $1 are not in the index"; return 1; }
    _nf_serve "$(partition_node "$1" "$NF_P")" "$1" $((NF_PORT + 16 * $2)) || { _nf_say "$1 could not be read (its frame server did not start)"; return 1; }
    echo "$NF_SV_PID" > "$_nw_d.serve.pid"   # still there after the loop = this worker was killed at the ceiling
    while [ -s "$_nw_d.pieces" ]; do
        rm -f "$_nw_d.why"
        awk '{ printf "%s%s-%s", (NR > 1 ? "," : ""), $1, $2 } END { print "" }' "$_nw_d.pieces" > "$_nw_d.list"
        # shellcheck disable=SC2086 # the image arguments are a word list on purpose
        _nf_feed "$_nw_d.pieces" "$3" "$_nw_d.why" | chunkcast apply --part "$NF_P" $CHUNK_IMAGE_ARGS --state "$_nw_d.state" \
            --chunks-file "$_nw_d.list" "$NF_NODE" >> "$_nw_d.apply.out" 2>> "$LOG_FILE"
        _nw_rc=$?
        read -r _nw_k _nw_w < "$_nw_d.why" 2>/dev/null || { [ "$_nw_rc" = 0 ] || _nf_say "$1 -> $NF_NODE: apply rc=$_nw_rc"; break; }
        [ "$_nw_w" = bad ] && [ "$_nw_rc" != 5 ] || break   # stopped (the ceiling, the clock), or no target: the rest stays missing
        _nw_bad=$((_nw_bad + 1))
        _nf_say "$1 -> $NF_NODE: the piece from chunk $_nw_k was not read whole from $1 -- what of it was not written stays missing"
        [ "$_nw_bad" -lt "$NF_BAD_N" ] || { _nf_say "$1 failed $_nw_bad pieces -- it is not asked again"; break; }
        awk -v k="$_nw_k" '$1 > k' "$_nw_d.pieces" > "$_nw_d.rest" && mv "$_nw_d.rest" "$_nw_d.pieces" || break
    done
    _nf_unserve "$1"; rm -f "$_nw_d.serve.pid"
}

_nf_reread() {   # $1 = the chunks written -> $NF_DIR/verified: those read back whole from the drawer. 1 = none could be
    : > "$NF_DIR/verified"; [ -s "$1" ] || return 0
    blockdev --flushbufs "$NF_NODE" >> "$LOG_FILE" 2>&1 || {
        _nf_say "$NF_NODE: its cache was not dropped (blockdev --flushbufs) -- no re-read from the disk, nothing counts as written"; return 1; }
    _nf_pieces "$1" > "$NF_DIR/reread.pieces" || return 1
    _nf_serve "$NF_NODE" reread "$NF_PORT" || { _nf_say "$NF_NODE could not be re-read (its frame server did not start)"; return 1; }
    while read -r _nr_a _nr_b _nr_f _nr_t; do
        _nr_t0=$(_nf_now) || break
        # the evidence is the answer and its whole length: a chunk that is not the index's ends the body short
        _nr_got=$(_nf_get "$_nr_f" "$_nr_t" -o /dev/null -w '%{http_code} %{size_download}') \
            && [ "$_nr_got" = "206 $((_nr_t - _nr_f + 1))" ] \
            && awk -v a="$_nr_a" -v b="$_nr_b" 'BEGIN { for (k = a; k <= b; k++) print k }' >> "$NF_DIR/verified" \
            || _nf_say "$NF_NODE: chunks $_nr_a-$_nr_b did not read back whole (${_nr_got:-no answer}) -- they stay missing"
        _nf_pace "$_nr_t0" $((_nr_b - _nr_a + 1)) 1 || break
    done < "$NF_DIR/reread.pieces"
    _nf_unserve reread
}

neighborfill_run() {   # $1 = drawer, $2 = the missing list (rewritten with what is still missing). 0 = all written and re-read
    _nr_list="$2"; NF_DIR="${CHUNK_DIR:-$RUN_DIR/chunk}/nf.$1"
    NF_P=${NF_PART:-}; [ -n "$NF_P" ] || { NF_P=${CHUNK_IDX##*/}; NF_P=${NF_P#p}; NF_P=${NF_P%.idx}; }
    NF_LEVEL="${NF_LEVEL:-${CAPTURE_LEVEL:-3}}"
    case "$NF_P" in ''|*[!0-9]*) _nf_say "$1: the partition is not known (NF_PART, or p<N>.idx)"; return 2 ;; esac
    rm -rf "$NF_DIR"; mkdir -p "$NF_DIR" || return 2
    _nr_n=$(awk 'NR == 1 { print $9 }' "$CHUNK_IDX" 2>/dev/null)
    case "$_nr_n" in ''|*[!0-9]*) _nf_say "$1: the index ($CHUNK_IDX) was not read"; return 2 ;; esac
    [ -f "$_nr_list" ] && [ -r "$_nr_list" ] || { _nf_say "$1: the list $_nr_list was not read"; return 2; }   # not read is not "nothing missing"
    _nf_chunks "$_nr_list" "$_nr_n" > "$NF_DIR/missing" || { _nf_say "$1: $_nr_list is not a list of chunks of this partition"; return 2; }
    [ -s "$NF_DIR/missing" ] || { _nf_say "$1: nothing is missing"; return 0; }
    NF_NODE=$(partition_node "$1" "$NF_P"); _nr_k=$(grep -c . "$NF_DIR/missing")
    _nr_c=$(_nf_cap) || { _nf_say "$1: the ceiling ($RUN_DIR/neighborfill.cap or NF_CAP_MIBS) is not a number -- nothing is read"; return 1; }
    _nf_limits
    _nf_neighbours "$1" > "$NF_DIR/neighbours"
    _nr_r=$(grep -c . "$NF_DIR/neighbours")
    if [ "$_nr_r" -gt 0 ] && [ "$_nr_c" -gt 0 ]; then
        # the token: who may ask the frame servers. A file only this process reads, never an argument (ps shows those)
        ( umask 077; od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > "$NF_DIR/token"
          printf 'header = "X-Imagesrv-Serve-Token: %s"\n' "$(cat "$NF_DIR/token")" > "$NF_DIR/token.curl" ) 2>> "$LOG_FILE"
        _nr_tk=$(cat "$NF_DIR/token" 2>/dev/null); [ "${#_nr_tk}" -ge 16 ] || { _nf_say "$1: no token for the frame servers -- nothing is read"; return 1; }
        # shellcheck disable=SC2046 # the neighbours are a word list on purpose
        _nf_plan "$NF_DIR/missing" $(cat "$NF_DIR/neighbours")
        _nr_readers=0; for _nr_m in $(cat "$NF_DIR/neighbours"); do [ ! -s "$NF_DIR/want.$_nr_m" ] || _nr_readers=$((_nr_readers + 1)); done
        _nf_say "$1 ($NF_NODE) lacks $_nr_k chunks of partition $NF_P -- $_nr_readers neighbours read, ${_nr_c}MiB/s together at most"
        _nr_i=0; _nr_pids=""
        for _nr_m in $(cat "$NF_DIR/neighbours"); do
            [ -s "$NF_DIR/want.$_nr_m" ] || continue
            _nr_i=$((_nr_i + 1)); _nf_worker "$_nr_m" "$_nr_i" "$_nr_readers" < /dev/null > /dev/null &
            _nr_pids="$_nr_pids $!"
        done
        # a read that stands still ends by itself (curl --speed-time); the fill as a whole has a ceiling too: three times
        # what the cap allows for these chunks, and two minutes. Past it the worker is killed: what it did not write stays missing
        _nr_t=$((_nr_k * 4 * 3 / _nr_c + 120))
        for _nr_p in $_nr_pids; do wait_pid "$_nr_p" "$_nr_t" "the neighbour fill of $1 (pid $_nr_p)"; done   # its own log line on a timeout; the re-read below is the evidence
    else
        _nf_say "$1 lacks $_nr_k chunks of partition $NF_P -- no neighbour to read ($_nr_r, ceiling ${_nr_c}MiB/s)"
    fi
    # a worker killed at the ceiling never reached _nf_unserve: its frame server is stopped here, said
    for _nr_s in "$NF_DIR"/*.serve.pid; do
        [ ! -f "$_nr_s" ] || { kill "$(cat "$_nr_s")" 2>/dev/null; _nf_say "${_nr_s##*/}: the frame server of a reader stopped at the ceiling is stopped"; }
    done
    # what each apply made durable on the drawer (its own state file: only this fill's chunks), and only those of the list
    for _nr_s in "$NF_DIR"/*.state; do [ ! -f "$_nr_s" ] || cat "$_nr_s"; done | awk -v n="$NF_NODE" '$1 == n && $2 == "ok" && $4 != "-" {
        m = split($4, r, ","); for (i = 1; i <= m; i++) { split(r[i], p, "-"); for (k = p[1] + 0; k <= p[2] + 0; k++) print k } }' \
        | sort -n -u > "$NF_DIR/durable"
    _nf_only "$NF_DIR/durable" 1 > "$NF_DIR/written"
    _nf_reread "$NF_DIR/written" || : > "$NF_DIR/verified"   # not re-read = not written (principle 5)
    _nf_only "$NF_DIR/verified" 0 > "$NF_DIR/left" || return 2
    _nf_runs "$NF_DIR/left" > "$_nr_list.nf" && mv "$_nr_list.nf" "$_nr_list" || { _nf_say "$1: $_nr_list was not rewritten"; return 2; }
    _nr_v=$(grep -c . "$NF_DIR/verified"); _nr_l=$(grep -c . "$NF_DIR/left")
    _nf_say "$1: $_nr_v of $_nr_k chunks written from the neighbours and re-read whole; $_nr_l left for HTTP"
    [ "$_nr_l" = 0 ]
}
