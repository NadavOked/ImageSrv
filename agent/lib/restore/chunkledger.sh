# chunkledger.sh -- #2533/#2528: which chunks of a target are DURABLE on its disk,
# for the progress report (interfaces.md section 4, `durable`). POSIX sh (busybox ash).
#
# The write ledger lives on the server and nowhere else (Nadav 12/11): the disk
# carries the #1798 mark, and no list. What the server may hear from here is only
# what an fsync has vouched for:
#
#   $RUN_DIR/targets/<dev>/ledger/written.<part>   the writer's: every chunk it has
#       written so far, a line "<first>-<last>" a run, cumulative, replaced through a
#       rename (never appended to: ledger_sync copies it while the writer runs)
#   $RUN_DIR/targets/<dev>/ledger/durable.<part>   ledger_sync's: the lines that were
#       there BEFORE an fsync that came back 0
#
# ledger_json reads durable.* and nothing else. A chunk written while the fsync ran
# waits for the next one, and an fsync that fails publishes nothing: the ledger may
# lag behind the disk, never lead it -- a chunk named durable that is not on the
# platter is a quiet hole in the middle of a disk that looks whole (#1388).
#
# A stream through partclone (zstd -dc | partclone -r) writes no
# `written.<part>`: partclone is a closed box, and nothing here can say which
# chunks it had written before a given fsync. The receiver of stage 3 writes chunk
# by chunk, and is the first writer of these files.

ledger_sync() {
    # $1 = target (dev), $2 = the node to fsync (the partition being written, or the
    # disk). 0 = the fsync came back 0, and what was written before it is durable.
    _ly_d="$RUN_DIR/targets/$1/ledger"
    rm -rf "$_ly_d/snap"; mkdir -p "$_ly_d/snap" || return 1
    for _ly_f in "$_ly_d"/written.*; do                  # the snapshot: BEFORE the fsync starts
        [ -f "$_ly_f" ] || continue
        cp "$_ly_f" "$_ly_d/snap/${_ly_f##*/}" || return 1
    done
    dd if=/dev/null of="$2" bs=512 count=0 conv=notrunc,fsync 2> "$_ly_d/fsync.err" || {
        log "$2: fsync failed -- nothing more is reported durable on $1: $(tail -n 1 "$_ly_d/fsync.err" 2>/dev/null)"
        return 1; }
    for _ly_f in "$_ly_d"/snap/written.*; do
        [ -f "$_ly_f" ] || continue
        mv "$_ly_f" "$_ly_d/durable.${_ly_f##*.}" || return 1     # a rename: whole or not at all
    done
}

# --- the delta (13/11): a report carries only what the server does not have yet ---------
#
# The cumulative list, sent whole in every report, grows with the damage: a 200GB image
# with every other chunk missing on two disks is 47,500 ranges -- over the 1MiB a request
# body may be (server/agent/body.py), and then the whole report is refused. It was also
# sent again every two seconds. So a report carries only the ranges that became durable
# since the last report the server ACKNOWLEDGED on that channel -- "progress" (the
# periodic report, progress.sh) or "pass" (the chunk engine's control message,
# chunkrecv.sh) -- and at most LEDGER_PIECE_RANGES of them:
#
#   .../ledger/acked.<ch>.<part>   what the server acknowledged (a union; sorted runs)
#   .../ledger/piece.<ch>.<part>   what the request being built, or in flight, carries
#   $RUN_DIR/ledger.<ch>.seq       "<session> <seq>": the server's last durable_seq
#   $RUN_DIR/ledger.<ch>.left      the ranges the request being built still has room for
#   $RUN_DIR/ledger.<ch>.more      there = ranges were left out of it, for the next piece
#
# The request says durable_seq, the last one it was answered. The server answers the next
# one -- the piece is the server's now -- or durable_full: it cannot place the report (a
# reply that was lost, a server that restarted), so what was acknowledged is forgotten
# here and everything is sent again, in pieces. Nothing is cut quietly: what did not fit
# stays in durable.<part>, unacknowledged, for the next request.
LEDGER_PIECE_RANGES="${LEDGER_PIECE_RANGES:-30000}"   # x 16 bytes a range at most = 480KB: under the 512KB a report may be

_ledger_forget() {   # $1 = channel: nothing is acknowledged on it any more
    rm -f "$RUN_DIR"/targets/*/ledger/acked."$1".* "$RUN_DIR"/targets/*/ledger/piece."$1".* \
        "$RUN_DIR/ledger.$1.left" "$RUN_DIR/ledger.$1.more"
}

ledger_seq() {   # $1 = channel, $2 = session -> the durable_seq to send. Another session (or none) starts the channel again
    _ls_s=""; _ls_n=0
    [ ! -s "$RUN_DIR/ledger.$1.seq" ] || read -r _ls_s _ls_n < "$RUN_DIR/ledger.$1.seq"
    if [ -z "$2" ] || [ "$_ls_s" != "$2" ]; then
        _ledger_forget "$1"; _ls_n=0
        [ -z "$2" ] || echo "$2 0" > "$RUN_DIR/ledger.$1.seq"
    fi
    case "$_ls_n" in ''|*[!0-9]*) _ls_n=0 ;; esac
    echo "$_ls_n"
}

ledger_seq_json() {   # $1 = channel -> ,"durable_seq":N when the request being built carries ranges; else nothing
    [ -n "${LEDGER_SESSION:-}" ] || return 0
    for _lq_f in "$RUN_DIR"/targets/*/ledger/piece."$1".*; do
        [ -s "$_lq_f" ] || continue
        printf ',"durable_seq":%s' "$(ledger_seq "$1" "$LEDGER_SESSION")"; return 0
    done
}

ledger_json() {
    # $1 = target, $2 = channel (default: progress), $3 = one partition (default: every one).
    # ,"durable":[{"part":3,"ranges":[[0,8811],[8815,9000]]}] -- what is durable on this
    # target and the server has not acknowledged on this channel ($LEDGER_SESSION names the
    # round; none = nothing is ever acknowledged, the whole list every time), cut at the room
    # the request has left. Nothing while there is no news (no field is "no information",
    # never "none"). A line that is not "<first>-<last>" with first <= last drops the whole
    # field: a ledger that cannot be read is not reported in part.
    _lj_c="${2:-progress}"; _lj_d="$RUN_DIR/targets/$1/ledger"; _lj_out=""
    ledger_seq "$_lj_c" "${LEDGER_SESSION:-}" > /dev/null
    for _lj_f in "$_lj_d"/durable.*; do
        [ -s "$_lj_f" ] || continue
        _lj_p=${_lj_f##*.}
        case "$_lj_p" in ''|*[!0-9]*) rm -f "$_lj_d"/piece."$_lj_c".*; return 0 ;; esac
        [ -z "${3:-}" ] || [ "$_lj_p" = "$3" ] || continue
        _lj_left=$(cat "$RUN_DIR/ledger.$_lj_c.left" 2>/dev/null)
        case "$_lj_left" in ''|*[!0-9]*) _lj_left=$LEDGER_PIECE_RANGES ;; esac
        # durable minus acknowledged (both sorted runs), up to the room that is left
        ACKED="$_lj_d/acked.$_lj_c.$_lj_p" awk -F- -v room="$_lj_left" '
            function out(a, b) { if (n < room) { print a "-" b; n++ } else more = 1 }
            BEGIN { acked = ENVIRON["ACKED"]; while ((getline l < acked) > 0) { split(l, p, "-"); alo[++na] = p[1] + 0; ahi[na] = p[2] + 0 }; k = 1 }
            NF != 2 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ || $1 + 0 > $2 + 0 { bad = 1; exit }
            { lo = $1 + 0; hi = $2 + 0
              while (k <= na && ahi[k] < lo) k++
              for (j = k; lo <= hi; j++) {
                  if (j > na || alo[j] > hi) { out(lo, hi); break }
                  if (alo[j] > lo) out(lo, alo[j] - 1)
                  lo = ahi[j] + 1 } }
            END { exit bad ? 2 : more ? 3 : 0 }' "$_lj_f" > "$_lj_d/piece.$_lj_c.$_lj_p"
        case "$?" in
            0) ;;
            3) : > "$RUN_DIR/ledger.$_lj_c.more" ;;
            *) rm -f "$_lj_d"/piece."$_lj_c".*; return 0 ;;
        esac
        [ -s "$_lj_d/piece.$_lj_c.$_lj_p" ] || continue
        echo $((_lj_left - $(grep -c . "$_lj_d/piece.$_lj_c.$_lj_p"))) > "$RUN_DIR/ledger.$_lj_c.left"
        _lj_r=$(awk -F- '{ printf "%s[%d,%d]", (NR > 1 ? "," : ""), $1, $2 }' "$_lj_d/piece.$_lj_c.$_lj_p")
        _lj_out="$_lj_out${_lj_out:+,}{\"part\":$_lj_p,\"ranges\":[$_lj_r]}"
    done
    [ -z "$_lj_out" ] || printf ',"durable":[%s]' "$_lj_out"
}

ledger_ack() {
    # $1 = channel, $2 = the server's reply (a file), $3 = session, $4 = the send's exit
    # code (not 0 = there is no reply). What the request carried becomes the server's only
    # on its word:
    #   0 = acknowledged, and nothing waits      3 = acknowledged, and more pieces wait
    #   2 = the server asked for everything again (what was acknowledged is forgotten)
    #   1 = no word about the ledger (no reply, or a report that carried no durable_seq)
    _la_rc=1; _la_seq=""; _la_full=""
    if [ "${4:-0}" = 0 ] && [ -s "$2" ]; then
        _la_seq=$(jq -r '.durable_seq // empty' "$2" 2>/dev/null)
        _la_full=$(jq -r '.durable_full // empty' "$2" 2>/dev/null)
    fi
    case "$_la_seq" in
        ''|*[!0-9]*) ;;
        *)  if [ "$_la_full" = true ]; then
                _ledger_forget "$1"; _la_rc=2
            else
                _la_rc=0; [ ! -e "$RUN_DIR/ledger.$1.more" ] || _la_rc=3
                for _la_f in "$RUN_DIR"/targets/*/ledger/piece."$1".*; do
                    [ -s "$_la_f" ] || continue
                    _la_a="${_la_f%/piece.*}/acked.$1.${_la_f##*.}"; [ -f "$_la_a" ] || : > "$_la_a"
                    sort -t- -k1,1n "$_la_a" "$_la_f" | awk -F- '
                        { lo = $1 + 0; hi = $2 + 0
                          if (n && lo <= chi + 1) { if (hi > chi) chi = hi }
                          else { if (n) print clo "-" chi; clo = lo; chi = hi; n = 1 } }
                        END { if (n) print clo "-" chi }' > "$_la_a.new" && mv "$_la_a.new" "$_la_a"
                done
            fi
            [ -z "$3" ] || echo "$3 $_la_seq" > "$RUN_DIR/ledger.$1.seq" ;;
    esac
    rm -f "$RUN_DIR"/targets/*/ledger/piece."$1".* "$RUN_DIR/ledger.$1.left" "$RUN_DIR/ledger.$1.more"
    return "$_la_rc"
}
