# syspartgui.sh -- #1896 (Nadav 22/10): marking C: in an old image from the
# build machine's restore screen and the cloner's ("שחזור אימג' מהשרת").
# POSIX sh (busybox ash).
#
# An image captured before #1875 with two Windows partitions of the same size
# has no "system" mark, and the server cannot tell which one is C: -- min-free
# is "not checked" and the image is offered nowhere (principle 5). The server
# lists it in /agent/state as `unchecked_images` ({id,name,folder,candidates});
# the screen shows it "לא נבדק" with the candidates (screens_syspart.c), and the
# choice goes to the server, which writes it into the image's manifest.json:
#   build machine: syspart|<id>|<idx> -> the console API, with the session
#   cloner:        lc-syspart|<id>|<idx> -> the kiosk files a request, the
#                  agent posts it with the screen's single-use nonce (lcpull.sh)
# A refusal is said on the screen in the server's words; nothing is assumed.

syspart_records() {
    # $1 = an /agent/state answer. The picker's records: image_unchecked= and
    # image_cand=<id>|<idx>|<size>|<used>. No field (an older server) = none.
    jq -r 'if (.unchecked_images | type) != "array" then empty else .unchecked_images[] |
        ("image_unchecked=" + ([.id, .name, .folder] | map((. // "") | tostring | gsub("[\\r\\n|]"; " ")) | join("|"))),
        (.id as $i | (.candidates // [])[] | "image_cand=" + ([$i, .index, .size_bytes, .used_bytes]
            | map((. // "") | tostring | gsub("[\\r\\n|]"; " ")) | join("|")))
      end' "$1"
}

_syspart_token() {
    # $1 = <id>|<idx>. Sets _sp_img and _sp_idx; 1 = not an id and a number.
    _sp_img=${1%%|*}; _sp_idx=${1#*|}
    case "$_sp_img" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
    case "$_sp_idx" in ''|*[!0-9]*|0*) return 1 ;; esac
}

gui_syspart() {
    # $1 = the token, syspart|<id>|<idx>. The build machine: the console's own
    # route, with the signed-in session (admin or deploy).
    _syspart_token "${1#syspart|}" || return 1
    printf '{"index":%s}' "$_sp_idx" > "$RUN_DIR/syspart.json" || return 1
    _sp_c=$(console_post "images/$_sp_img/system-partition" "$RUN_DIR/syspart.json" "$RUN_DIR/syspart.resp")
    [ "$_sp_c" = 200 ] && jq -e '.ok == true' "$RUN_DIR/syspart.resp" > /dev/null 2>&1 && {
        log "C: of $_sp_img marked as partition $_sp_idx from the build machine (#1896)"; return 0; }
    _sp_e=$(jq -r '.detail // .error // empty' "$RUN_DIR/syspart.resp" 2>/dev/null)
    gui_error "${_sp_e:-מחיצת המערכת לא נשמרה (http ${_sp_c:-000})}" "C: not marked (http ${_sp_c:-000})"
}

lc_pull_syspart() {
    # Agent side of the cloner's screen: $LC/syspart.req = <id>|<idx>, filed by
    # the kiosk. The same gate as opening a round from this screen (identity,
    # a registered cloner, the single-use screen nonce). The images are read
    # again either way, so the screen shows what the server holds now.
    IFS= read -r _ls_req < "$LC/syspart.req"; rm -f "$LC/syspart.req" "$LC/pull.error"
    if ! _syspart_token "$_ls_req" || [ ! -s "$LC/pull.nonce" ]; then
        _lc_req pull.error "הסימון לא נשלח — חזרו ובחרו שוב"; return 1
    fi
    printf '{"mac":"%s","nonce":"%s","image_id":"%s","index":%s}' "$(json_escape "$MAC")" \
        "$(cat "$LC/pull.nonce")" "$_sp_img" "$_sp_idx" > "$LC/syspart.json" || return 1
    _ls_c=$(lc_pull_post cloner-round/system-partition "$LC/syspart.json" "$LC/syspart.resp")
    if [ "$_ls_c" = 200 ] && [ "$(json_get "$LC/syspart.resp" ".ok" 2>/dev/null)" = true ]; then
        log "pull: C: of $_sp_img marked as partition $_sp_idx (#1896)"
    else
        _ls_e=$(json_get "$LC/syspart.resp" ".error" 2>/dev/null)
        case "$_ls_e" in ''|null) _ls_e="מחיצת המערכת לא נשמרה (http ${_ls_c:-000})" ;; esac
        _lc_req pull.error "$(_lc_clean "$_ls_e")"
    fi
    lc_pull_images || _lc_req pull.error "לא ניתן לקרוא מהשרת את רשימת האימג׳ים למחשב הזה"
}
