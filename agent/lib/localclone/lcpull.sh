# lcpull.sh -- 16/10 (Nadav): the cloner's third boot choice, "שחזור אימג'
# מהשרת". The person picks one library image and some of THIS machine's
# drawers on the build machine's restore screen; the confirm dialog's red
# button sends lc-pull|<image>|<p1,p2>, and this opens a room round of one
# machine on the server (POST /api/v1/agent/cloner-round, interfaces.md).
#
# Nothing is written here. When the round is open the machine goes on from
# the server, and the round reaches it like any room round: hello -> the
# wave -> do_restore_drawers (the SMART gate, multicast, per-drawer progress
# on the cloner screen, power-off for the swap). No sign-in: being at the
# machine is the authorization (owner, 16/10); the server takes this
# machine's own attached drawers and nothing else.
#
# The controller (lc_pull_session) runs in the agent; lc_pull_state runs in
# the kiosk and adds the screen's records. POSIX sh (busybox ash).

lc_pull_ports() {
    # $1 = "p1,p2" from the screen. Every port is a disk number (1..) that
    # names exactly one drawer of list_drawers NOW, none twice. stdout "p1 p2";
    # 1 = refused, and a refusal is never "all the drawers" (#1410).
    case "$1" in ''|*[!0-9,]*|,*|*,|*,,*) return 1 ;; esac
    _pp_out=""
    for _pp_p in $(printf '%s' "$1" | tr ',' ' '); do
        case "$_pp_p" in 0*) return 1 ;; esac
        case " $_pp_out " in *" $_pp_p "*) return 1 ;; esac
        _pp_n=0
        for _pp_d in $(list_drawers); do
            [ "$(disk_port "$_pp_d")" = "$_pp_p" ] && _pp_n=$((_pp_n + 1))
        done
        [ "$_pp_n" -eq 1 ] || return 1
        _pp_out="$_pp_out $_pp_p"
    done
    printf '%s\n' "${_pp_out# }"
}

lc_pull_images() {
    # The images allowed for THIS machine -- the server filters them, the same
    # list the build machine's restore screen offers (allowed_images, #706) --
    # as the screen's image= records. Read once per visit, not every second.
    http_get "$SERVER/api/v1/agent/state?mac=$MAC" > "$LC/pull.state.json" || return 1
    jq -r 'if (.allowed_images | type) != "array" then error("no allowed_images") else
        .allowed_images[] | "image=" + ([.id, .name, .folder]
          | map((. // "") | tostring | gsub("[\\r\\n|]"; " ")) | join("|")) end' \
        "$LC/pull.state.json" > "$LC/pull.images.next" 2>> "$LOG_FILE" || return 1
    syspart_records "$LC/pull.state.json" >> "$LC/pull.images.next" 2>> "$LOG_FILE" || return 1   # #1896: "לא נבדק" + C:
    mv "$LC/pull.images.next" "$LC/pull.images"
}

lc_pull_post() {
    # $1 = path under /api/v1/agent/, $2 = body file, $3 = answer file. stdout
    # the HTTP code ("000" = no answer). No --retry: a retried open whose first
    # answer was lost would read "a round is active".
    : > "$3"
    curl -sS --max-time "$HTTP_TIMEOUT" -o "$3" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary "@$2" \
        "$SERVER/api/v1/agent/$1" 2>> "$LOG_FILE"
}

lc_pull_nonce() {
    # The server's single-use token that this screen is open (security review,
    # 16/10): asked for when the screen comes up and after every attempt.
    # 0 = $LC/pull.nonce holds a fresh one; else none, and the open is refused.
    rm -f "$LC/pull.nonce"
    printf '{"mac":"%s"}' "$(json_escape "$MAC")" > "$LC/pull.nonce.json"
    _pn_c=$(lc_pull_post cloner-round/nonce "$LC/pull.nonce.json" "$LC/pull.nonce.resp")
    _pn_n=$(json_get "$LC/pull.nonce.resp" ".nonce" 2>/dev/null)
    case "$_pn_c:$_pn_n" in
        200:*[!0-9a-f]*|200:|200:null) ;;
        200:*) printf '%s\n' "$_pn_n" > "$LC/pull.nonce"; return 0 ;;
    esac
    log "pull: the server gave no screen token (http ${_pn_c:-000})"
    return 1
}

lc_pull_open() {
    # $1 = image id, $2 = ports (words). 0 = the server answered 200 with
    # ok:true -- positive evidence that the round is open.
    _po_ports=$(printf '%s' "$2" | tr ' ' ',')
    _po_n=$(cat "$LC/pull.nonce" 2>/dev/null)
    printf '{"mac":"%s","nonce":"%s","image_id":"%s","ports":[%s]}' "$(json_escape "$MAC")" "$_po_n" "$1" "$_po_ports" > "$LC/pull.json"
    _po_c=$(lc_pull_post cloner-round "$LC/pull.json" "$LC/pull.resp")
    if [ "$_po_c" = 200 ] && [ "$(json_get "$LC/pull.resp" ".ok" 2>/dev/null)" = true ]; then
        log "pull: room round $(json_get "$LC/pull.resp" ".id") opened on drawers $2 ($1)"
        return 0
    fi
    _po_e=$(json_get "$LC/pull.resp" ".error" 2>/dev/null)
    case "$_po_e" in ''|null) _po_e="השרת לא פתח את הסבב (http ${_po_c:-000})" ;; esac
    log "pull: the server did not open the round (http ${_po_c:-000})"
    _lc_req pull.error "$(_lc_clean "$_po_e")"
    return 1
}

lc_pull_session() {
    # The restore screen until the person leaves it. Sets LC_END: back (to the
    # choice), opened (the round is open -- go on from the server), quiet, gone.
    LC_END=""
    rm -f "$LC/pull.req" "$LC/pull.error" "$LC/pull.images" "$LC/end.req" "$LC/syspart.req"
    lc_pull_images || { log "pull: the allowed images could not be read"
        _lc_req pull.error "לא ניתן לקרוא מהשרת את רשימת האימג׳ים למחשב הזה"; }
    lc_pull_nonce || :   # none = the open is refused, aloud; asked again after each try
    echo pull > "$LC/screen"
    attended_start "Restore an image from the server onto this machine's drawers"; lc_quiet_reset
    while :; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        lc_gui_alive || { log "pull: the GUI is gone -- from the server"; LC_END=gone; return 0; }
        lc_power_check
        # #1560: a round opened for this machine meanwhile (not by this screen: that one returns "opened")
        lc_round_calls && { log "pull: a round is open for this machine -- from the server"; LC_END=round; return 0; }
        if [ -f "$LC/pull.req" ]; then
            IFS='|' read -r _ps_img _ps_ports < "$LC/pull.req"; rm -f "$LC/pull.req" "$LC/pull.error"; lc_quiet_reset
            case "$_ps_img" in ''|*[!A-Za-z0-9_.-]*) _ps_img="" ;; esac
            if [ -z "$_ps_img" ] || ! _ps_w=$(lc_pull_ports "$_ps_ports"); then
                log "pull: the choice is not what is attached now -- nothing opened"
                _lc_req pull.error "המגירות שנבחרו אינן מה שמחובר עכשיו — בחרו שוב"
            elif lc_pull_open "$_ps_img" "$_ps_w"; then
                LC_END=opened; return 0
            fi
            lc_pull_nonce || :   # the token is single-use: a fresh one for the next try
        fi
        if [ -f "$LC/syspart.req" ]; then   # #1896: C: of an old image (syspartgui.sh)
            lc_quiet_reset; lc_pull_syspart || :; lc_pull_nonce || :   # the token is single-use
        fi
        if [ -f "$LC/end.req" ]; then
            _ps_e=$(cat "$LC/end.req"); rm -f "$LC/end.req"
            [ "$_ps_e" = back ] && { LC_END=back; return 0; }
        fi
        if lc_quiet_over; then
            log "pull: no touch for ${_lq_max}s on the restore screen -- from the server"
            LC_END=quiet; return 0
        fi
        sleep 1
    done
}

lc_pull_state() {
    # Kiosk side, inside lc_gui_state while $LC/screen is "pull": the images,
    # the drawers (cloner_cdisks -- the standby screen's records: disk number,
    # SMART colour; list_drawers only, never a removable boot stick), the
    # slot names, and the server's refusal in words.
    [ ! -f "$LC/pull.images" ] || cat "$LC/pull.images" || return 1
    cloner_cdisks "$(list_drawers)" || return 1
    slot_names_refresh || :; drawer_slots_refresh || :
    slot_names_records || return 1
    [ ! -s "$LC/pull.error" ] || printf 'form_error=%s\n' "$(tr -d '\r\n' < "$LC/pull.error")"
}
