# pull.sh -- registering a single-station unicast restore with the server, so
# that the progress reports in progress.sh have a session id to travel under.
# POSIX sh (busybox ash). Sourced after progress.sh: pull_close calls
# report_final.
#
# It lived inside progress.sh until #562, on the reasoning below -- opening a
# pull is a reporting concern and not a screen. That reasoning still holds and
# the file order preserves it; what did not hold is one file carrying both, at
# 316 lines against a 300-line wall.
#
# The interface: interfaces.md, "משיכת יוניקאסט לתחנה בודדת".
#
# A single-station restore pulls the image over HTTP. The bytes move whether
# or not the server was told -- /api/v1/images/... serves them to any machine
# in the registry -- so opening a pull is not asking permission. It is how the
# work becomes *visible*: a session id to address the reports above to, a line
# in the journal, and a row on the console. Until the agent called it, a
# station pulled for twenty minutes while the operator watched an idle
# server (#60 built the server side, #63 is this side).

pull_body() {
    # $1 = mac, $2 = image id, $3 = username, $4 = password.
    # Assembled by hand like hello and login -- nothing writes JSON with jq.
    printf '{"mac":"%s","image_id":"%s","username":"%s","password":"%s"}' \
        "$(json_escape "$1")" "$(json_escape "$2")" \
        "$(json_escape "$3")" "$(json_escape "$4")"
}

pull_post() {
    # $1 = server, $2 = body file, $3 = response file. Prints the HTTP code.
    #
    # Deliberately not http_post_json: `curl -f` collapses every answer
    # from 400 up into exit 22, and the two answers
    # that matter most here are opposite diagnoses. 404 means this server is
    # older than the endpoint and the restore should simply go on unwatched;
    # 503 means the server that the restore is about to pull 40GB *from* is
    # falling over. Folding them into "the pull failed" sends a technician
    # after the wrong fault.
    #
    # $PULL_JAR (11/10): the GUI's console session -- sent as the cookie, it
    # is the authorization the server accepts instead of a password.
    if [ -n "${PULL_JAR:-}" ] && [ -s "$PULL_JAR" ]; then
        curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
            -b "$PULL_JAR" -o "$3" -w '%{http_code}' \
            -H "Content-Type: application/json" \
            --data-binary "@$2" "$1/api/v1/agent/pulls" 2>/dev/null
        return
    fi
    curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -o "$3" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        --data-binary "@$2" "$1/api/v1/agent/pulls" 2>/dev/null
}

pull_open() {
    # $1 = server, $2 = mac, $3 = image id, $4 = username, $5 = password.
    #
    # Sets PULL_SESSION and returns 0 on positive evidence only: a 200 that
    # carried an id back. "No error" is not an open stream -- a 200 with an
    # empty body would report progress into a session that does not exist.
    #
    # Every other road returns 1 *and writes a line*. Rule 1 decides what the
    # caller does with that (the restore goes on; a person is standing there
    # and the disk is already being erased), but a pull that failed to open
    # silently would be the same blindness #60 set out to end.
    PULL_SESSION=""
    _resp="$RUN_DIR/pull_resp.json"
    : > "$_resp"
    pull_body "$2" "$3" "$4" "$5" > "$RUN_DIR/pull.json"
    _code=$(pull_post "$1" "$RUN_DIR/pull.json" "$_resp")
    rm -f "$RUN_DIR/pull.json"

    if [ "$_code" = "200" ]; then
        _sid=$(json_get "$_resp" ".id")
        if [ -n "$_sid" ] && [ "$_sid" != "null" ]; then
            PULL_SESSION="$_sid"
            log "unicast pull registered as $_sid"
            return 0
        fi
        log "pull not opened: the server answered 200 without a session id"
        return 1
    fi

    _why=$(json_get "$_resp" ".code")
    if [ "$_code" = "000" ]; then
        log "pull not opened: no answer from the server -- restoring unwatched"
    elif [ "$_code" = "404" ] && [ "$_why" = "null" ]; then
        # Our own refusals carry {"ok":false,"code":...}. A 404 without one is
        # the router saying the path does not exist: a server older than #60.
        log "pull not opened: this server has no /api/v1/agent/pulls" \
            "(older than the pull view) -- restoring unwatched"
    else
        log "pull not opened: the server refused it (http $_code, $_why)" \
            "-- restoring unwatched"
    fi
    return 1
}

pull_close() {
    # $1 = reporter pid ("" when no stream was opened), $2 = session id,
    # $3 = mac, $4 = server URL.
    #
    # The server closes a pull on positive evidence: a report that says
    # `done`. So the closing report *is* the close, and it goes through
    # report_final like every other closing report -- one mechanism, not
    # three, which is what let the other two paths drift into `sleep 6`.
    #
    # Nothing to close when no stream was ever opened (an older server, a
    # server that refused): the restore went on unwatched and pull_open has
    # already said so.
    [ -n "${1:-}" ] || return 0
    if report_final "$1" "$2" "$3" "$4"; then
        log "unicast pull $2 closed"
        return 0
    fi
    log "WARNING: the closing report for pull $2 did not get through --" \
        "it may stay 'running' on the console until an operator clears it"
    return 1
}
