# buildmenu.sh -- the console session the build machine's GUI menu works
# through (#135, #1073). POSIX sh (busybox ash). No bashisms.
#
# The menu itself is the native GUI (guibridge.sh). Until 11/10 this file was
# also the text menu behind it -- sign-in, the numbered options, standby --
# and the owner removed it: GUI only, no text fallback, and the code goes
# with it so it cannot be reached again. What stays is what the GUI bridge
# calls: the console sign-in and cookie, GET/POST against the console API,
# the one-line reason for a refusal, and the name check.
#
# What the GUI menu offers (the role, classroom and tools gates) lives in
# buildmenuitems.sh.

#: The console session cookie. The console API (folders, capture, room) is
#: cookie-authenticated; /api/v1/agent/login proves the password but issues
#: no cookie, so the credentials are exchanged once more here.
CONSOLE_JAR="${CONSOLE_JAR:-$RUN_DIR/console.jar}"

#: The cookie the server sets (server/auth.py:COOKIE_NAME).
CONSOLE_COOKIE="imagesrv_session"

console_signin() {
    # Exchanges the operator's console credentials for a session cookie.
    #
    # The password goes to curl over a pipe and never becomes a file:
    # $RUN_DIR is tmpfs on a machine that stands in a room, a hang or a
    # power switch can skip the `rm` after the POST, and a cleanup that has
    # to succeed for the fix to hold is not a fix.
    #
    # Not `curl -f`: it collapses every answer from 400 up into exit 22, and
    # 401 ("checked, wrong") is not 000 ("never asked"). The verdict is the
    # status code itself, and then the cookie -- 200 without a session
    # cookie is not a session (rule 5: positive evidence, twice).
    #
    # A fresh session every time an option is chosen, on purpose: a cookie
    # kept from earlier can have expired (12h) and would come back as a 401
    # in the middle of a flow, which is a stale-credential bug for the price
    # of one POST. It also puts every action in the journal under its user.
    # Every failure below also drops console_login.json: curl writes the
    # refusal's body there, and gui_role reads a role from that file.
    rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json"
    _code=$(printf '{"username":"%s","password":"%s"}' \
        "$(json_escape "$RECOVERY_USER")" "$(json_escape "$RECOVERY_PASS")" \
    | curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -c "$CONSOLE_JAR" -o "$RUN_DIR/console_login.json" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary @- \
        "$SERVER/api/console/login") || {
        log "console sign-in: transport failed" >&2
        rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json"
        return 1
    }
    if [ "$_code" = "401" ]; then
        rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json"
        log "console sign-in: refused (http 401)"
        return 1
    fi
    if [ "$_code" != "200" ]; then
        rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json"
        log "console sign-in refused (http ${_code:-000})"
        console_say "$_code" "Could not open a console session"
        return 1
    fi
    # Fails closed: a jar that cannot be read is not a session we hold.
    if ! awk -F '\t' -v cookie="$CONSOLE_COOKIE" \
        '$6 == cookie && length($7) > 0 { found=1 } END { exit !found }' "$CONSOLE_JAR"; then
        rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json"
        log "console sign-in: 200 without a session cookie"
        return 1
    fi
    return 0
}

console_get() {
    # $1 = path under /api/console, $2 = output file. Prints the HTTP code.
    curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -b "$CONSOLE_JAR" -o "$2" -w '%{http_code}' \
        "$SERVER/api/console/$1"
}

console_post() {
    # $1 = path, $2 = JSON body file, $3 = output file. Prints the HTTP code.
    # Bodies here carry no passwords -- names, a mac and a disk -- so unlike
    # the sign-in they may live in a file.
    curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -b "$CONSOLE_JAR" -o "$3" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary "@$2" \
        "$SERVER/api/console/$1"
}

gui_saved_theme() {
    # #1499 (Nadav 16/10): the theme the signed-in user saved (users.theme),
    # from the login answer console_signin kept. light|dark as saved; auto,
    # absent or anything else = auto, which the GUI draws as its default.
    jq -r 'if .theme == "light" or .theme == "dark" then .theme else "auto" end' \
        "$RUN_DIR/console_login.json" 2>/dev/null || echo auto
}

gui_theme_save() {
    # #1499: a switch made in the GUI while signed in is the user's own saved
    # theme -- the console's PUT /me/theme, with the GUI's session. 200 only.
    case "$1" in light|dark) ;; *) return 1 ;; esac
    printf '{"theme":"%s"}' "$1" > "$RUN_DIR/gui-theme.json" || return 1
    _gt=$(curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" -X PUT \
        -b "$CONSOLE_JAR" -o "$RUN_DIR/gui-theme-resp.json" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary "@$RUN_DIR/gui-theme.json" \
        "$SERVER/api/console/me/theme") || _gt=000
    [ "$_gt" = 200 ] && return 0
    console_say "$_gt" "Saving the theme"
    gui_error "ערכת הנושא לא נשמרה (http $_gt)" "The theme was not saved (http $_gt)"   # $1 screen (Hebrew, #1503), $2 log
}

console_say() {
    # $1 = HTTP code, $2 = what was being attempted. One English line into
    # the agent log -- the GUI's error toast points there ("see agent log").
    # The server's own `detail` is Hebrew and stays in $RUN_DIR as it came.
    case "$1" in
        401) log "$2: the console session expired" ;;
        403) log "$2: this account may not do that" ;;
        409) log "$2: something is already open (a round, or a task)" ;;
        400) log "$2: the server rejected the request" ;;
        200) log "$2: unexpected answer from the server" ;;
        *)   log "$2: no answer from the server (http ${1:-000})" ;;
    esac
}

build_name_ok() {
    # Folder and image names are ASCII: letters, digits, dot, underscore,
    # dash and spaces. Nadav's ruling of 2026-08-30 -- "English or numbers".
    #
    # A courtesy check before the POST (guibridge.sh:gui_capture), not
    # enforcement: the server still accepts any name through the console.
    case "$1" in
        "") return 1 ;;
        *[!A-Za-z0-9._\ -]*) return 1 ;;
    esac
    [ "${#1}" -le 60 ]
}
