# askgui.sh -- the agent's questions as a native-GUI dialog (11/10). POSIX sh.
#
# Nadav, 11/10: "הכול גואי". The two questions that were still asked on the
# text console -- the boot-time grow-back of a disk left shrunk (#926/#929,
# shrinkoffer.sh) and "power off or back to the menu" at the end of a
# capture (#409, task.sh) -- are a dialog in the style of the shrink
# question (#928), over the same handshake as shrinkgui.sh:
#
#   $GUI_DIR/ask-request   nonce|kind|count|subject, then (growback only)
#                          idx|now bytes|orig bytes|table|fs per row
#   $GUI_DIR/ask-decision  nonce|answer   (written by the bridge)
#
# kind growback: answers restore / leave; capture-end: poweroff / menu.
# On the cloner screen (drawergate.sh): raid (#1808) wipe / skip, one row
# port|type; share (#1898) split / skip, rows port|idx|now bytes|after bytes.
# On the local clone screen (lcraw.sh): badsect (#2017) continue / stop, one row
# port|idx|epoch -- the source's partition that did not read, raw either, and
# when the copy goes on by itself (Nadav 22/10: there, no answer = continue).
# guistate.sh copies the request into ask_prompt= / ask_row=, the GUI draws
# it (screens_ask.c) and emits ask-<answer>|<nonce>. Nothing here answers by
# itself: a dead GUI, a malformed request, a broken handshake or no answer
# within the caller's ceiling all return non-zero, and the caller keeps its
# safe default (leave the disk as it is / power off).

#: The most rows the GUI draws (MAX_ASK_ROWS in native-gui/src/ui.h).
ASK_GUI_MAX_ROWS=16

gui_ask_clear() {
    rm -f "$GUI_DIR/ask-request" "$GUI_DIR/ask-decision" \
        "$GUI_DIR/ask-request.next" "$GUI_DIR/ask-decision.next"
}

gui_ask_answers() {
    # $1 = kind. The answers that kind accepts, one word list.
    case "$1" in
        growback) echo "restore leave" ;;
        capture-end|send-end|round-end) echo "poweroff menu" ;;   # #1560: task.sh build_end_ask
        raid) echo "wipe skip" ;;     # #1808: a cloner drawer with old RAID metadata (drawergate.sh)
        share) echo "split skip" ;;   # #1898: the room shared with a shrunk partition before the one that grows
        badsect) echo "continue stop" ;;   # #2017: a local clone's source with bad sectors (lcraw.sh)
        *) return 1 ;;
    esac
}

gui_ask_choice() {
    # $1 = kind, $2 = subject (a disk name, or the epoch of the power-off), $3 = rows file
    # (idx|now|orig|table|fs; "" for none), $4 = ceiling in seconds.
    # stdout = the answer; non-zero = the GUI could not answer.
    _ag_ok=$(gui_ask_answers "$1") || return 1
    [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null || return 1
    case "${4:-}" in ''|*[!0-9]*) return 1 ;; esac
    case "$2" in *[!A-Za-z0-9._/-]*) return 1 ;; esac
    GUI_DIR=${GUI_DIR:-$RUN_DIR/gui}
    gui_ask_clear || return 1
    # File-backed and monotonic, like shrinkgui.sh: this runs inside `$( )`.
    _ag_seq=0
    if [ -f "$GUI_DIR/ask-seq" ]; then
        read -r _ag_seq < "$GUI_DIR/ask-seq" || return 1
    fi
    case "$_ag_seq" in ''|*[!0-9]*) return 1 ;; esac
    _ag_seq=$((_ag_seq + 1))
    printf '%s\n' "$_ag_seq" > "$GUI_DIR/ask-seq" || return 1
    _ag_nonce="$_ag_seq-0"
    # The whole request is validated before it is published -- a row is never
    # dropped silently (the GUI refuses a partial table, and so does this).
    if [ "$1" = growback ]; then
        [ -s "${3:-}" ] || return 1
        awk -F'|' -v nonce="$_ag_nonce" -v subj="$2" -v max="$ASK_GUI_MAX_ROWS" '
            NF != 4 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ ||
            $1 < 1 || $2 + 0 < 1 || $2 + 0 >= $3 + 0 || ($4 != "table" && $4 != "fs") ||
            seen[$1]++ { bad = 1; exit }
            { rows[++n] = $0 }
            END { if (bad || n < 1 || n > max) exit 1
                  print nonce "|growback|" n "|" subj; for (i = 1; i <= n; i++) print rows[i] }
        ' "$3" > "$GUI_DIR/ask-request.next" || { gui_ask_clear; return 1; }
    elif [ "$1" = raid ] || [ "$1" = share ] || [ "$1" = badsect ]; then   # #1808: port|type, one; #1898: port|idx|now|after; #2017: port|idx|epoch, one
        [ -s "${3:-}" ] || return 1
        awk -F'|' -v nonce="$_ag_nonce" -v subj="$2" -v kind="$1" -v max="$ASK_GUI_MAX_ROWS" '
            kind == "raid" && (NF != 2 || $1 !~ /^[0-9]+$/ || $2 !~ /^[a-z_]+$/) { bad = 1; exit }
            kind == "badsect" && (NF != 3 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ || $2 < 1 || $3 !~ /^[1-9][0-9]*$/) { bad = 1; exit }
            kind == "share" && (NF != 4 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ ||
                $4 !~ /^[0-9]+$/ || $2 < 1 || $3 + 0 < 1 || $4 + 0 <= $3 + 0) { bad = 1; exit }
            { rows[++n] = $0 }
            END { if (bad || n < 1 || n > max || ((kind == "raid" || kind == "badsect") && n != 1)) exit 1
                  print nonce "|" kind "|" n "|" subj; for (i = 1; i <= n; i++) print rows[i] }
        ' "$3" > "$GUI_DIR/ask-request.next" || { gui_ask_clear; return 1; }
    else
        printf '%s|%s|0|%s\n' "$_ag_nonce" "$1" "$2" > "$GUI_DIR/ask-request.next" || return 1
    fi
    mv "$GUI_DIR/ask-request.next" "$GUI_DIR/ask-request" || { gui_ask_clear; return 1; }
    _ag_waited=0
    while kill -0 "$_gui_pid" 2>/dev/null && [ -e "$GUI_DIR/ask-request" ]; do   # #1726: a bridge that died took the question with it
        case "$1" in raid|share) [ ! -e "$RUN_DIR/session-closed" ] || break ;; esac   # a closed round ends a drawer's question, as SMART's
        attended_power_check
        if [ -s "$GUI_DIR/ask-decision" ]; then
            IFS='|' read -r _ag_nonce_back _ag_answer < "$GUI_DIR/ask-decision" || {
                gui_ask_clear; return 1;
            }
            if [ "$_ag_nonce_back" = "$_ag_nonce" ]; then
                case " $_ag_ok " in
                    *" $_ag_answer "*)
                        gui_ask_clear || return 1
                        printf '%s\n' "$_ag_answer"; return 0 ;;
                esac
            fi
            # A stale or malformed decision is dropped; the wait goes on.
            rm -f "$GUI_DIR/ask-decision" || { gui_ask_clear; return 1; }
        fi
        if [ "$_ag_waited" -ge "$4" ]; then
            printf 'native GUI: no answer to the %s question in %ss\n' "$1" "$4" >&2
            break
        fi
        sleep 1
        _ag_waited=$((_ag_waited + 1))
    done
    gui_ask_clear
    return 1
}

gui_ask_dispatch() {
    # `ask-<answer>|<nonce>` from the build GUI. A pending local question,
    # like shrink-*: no console session needed, and accepted only for the
    # nonce of the request on screen and an answer its kind allows.
    case "$token" in *'|'*) ;; *) return 1 ;; esac
    _ad_answer=${token%%|*}; _ad_answer=${_ad_answer#ask-}; _ad_click=${token#*|}
    case "$_ad_click" in ''|*[!0-9-]*) return 1 ;; esac
    [ -s "$GUI_DIR/ask-request" ] || return 1
    IFS='|' read -r _ad_active _ad_kind _ < "$GUI_DIR/ask-request" || return 1
    [ -n "$_ad_active" ] && [ "$_ad_click" = "$_ad_active" ] || return 1
    case "$_ad_kind:${GUI_SCREEN:-}" in raid:cloner|share:cloner|badsect:cloner) ;; raid:*|share:*|badsect:*|*:?*) return 1 ;; esac   # a drawer's question on the cloner screen; the rest on the build screen
    _ad_ok=$(gui_ask_answers "$_ad_kind") || return 1
    case " $_ad_ok " in *" $_ad_answer "*) ;; *) return 1 ;; esac
    printf '%s|%s\n' "$_ad_active" "$_ad_answer" > "$GUI_DIR/ask-decision.next" &&
        mv "$GUI_DIR/ask-decision.next" "$GUI_DIR/ask-decision"
}

gui_ask_state() {
    # The pending question, copied into the state file (guistate.sh) the way
    # gui_shrink_state copies the shrink question: one awk, one open.
    [ -s "$GUI_DIR/ask-request" ] || return 0
    awk 'NR == 1 { print "ask_prompt=" $0; next }
         { print "ask_row=" $0 }' "$GUI_DIR/ask-request" 2>/dev/null ||
        [ ! -e "$GUI_DIR/ask-request" ]
}
