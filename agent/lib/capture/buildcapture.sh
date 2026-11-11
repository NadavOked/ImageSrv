# buildcapture.sh -- ordering a capture from the build machine's GUI (#135).
# POSIX sh (busybox ash). No bashisms.
#
# Spec 26 step 3 is "name, description, target folder". The GUI collects
# them (guibridge.sh:gui_capture checks the disk against the server's fresh
# inventory), and this posts the same body the console sends -- so the work
# itself runs through the ordinary task path: the next hello brings
# `task.type=capture` back and do_task does exactly what it always did.
# The text picker that lived here (folders, name, disk, y/N) was removed with
# the rest of the text screens (owner, 11/10: GUI only).
#
# Creating a folder is admin_only, and so is the capture; gui_capture is
# reached only by an admin session (gui_role).

build_say_refusal() {
    # #2842: the server's own words (its `detail`, Hebrew: which name rule broke)
    # to the screen's error line, not only a log line the operator never sees.
    # $1 = HTTP code, $2 = response file, $3 = English for the log. A detail
    # that is not a string (a 422's list) is not shown as JSON noise: the
    # screen then says the request was refused, with the code.
    _bs_d=$(jq -r 'if (.detail | type) == "string" then .detail else empty end' "$2" 2>/dev/null)
    gui_error "${_bs_d:-הבקשה נדחתה (http $1)}" "$3 (http $1)"
    return 0
}

build_new_folder() {
    # $1 = folder name (the server checks it, #2842). Prints the name of
    # the folder it created. POST /folders is admin_only; a 403 here means
    # the account is not what the menu was built from, and that stops the
    # capture instead of carrying on with a folder that does not exist.
    _nm=$1
    [ -n "$_nm" ] || return 1
    printf '{"name":"%s"}' "$(json_escape "$_nm")" \
        > "$RUN_DIR/folder_new.json"
    _code=$(console_post "folders" "$RUN_DIR/folder_new.json" \
        "$RUN_DIR/folder_resp.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "Could not create the folder" >&2
        build_say_refusal "$_code" "$RUN_DIR/folder_resp.json" 'The folder was not created'
        return 1
    fi
    log "folder created from the build machine: $_nm" >&2
    echo "$_nm"
}

build_capture_post() {
    # $1 name, $2 disk, $3 folder, $4 optional description.
    printf '{"mac":"%s","name":"%s","disk":"%s","folder":"%s","description":"%s"}' \
        "$MAC" "$(json_escape "$1")" "$(json_escape "$2")" \
        "$(json_escape "$3")" "$(json_escape "${4:-}")" > "$RUN_DIR/capture_req.json"
    _code=$(console_post "tasks/capture" "$RUN_DIR/capture_req.json" \
        "$RUN_DIR/capture_resp.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The capture was not created"
        build_say_refusal "$_code" "$RUN_DIR/capture_resp.json" 'The capture was not created'
        return 1
    fi
    # 200 is not a task; the id is. An answer we cannot read is a capture we
    # cannot claim was ordered (rule 5).
    _tid=$(json_get "$RUN_DIR/capture_resp.json" ".id")
    case "$_tid" in
        ""|null)
            log "capture order: 200 without an id"
            return 1
            ;;
    esac
    log "capture ordered from the build machine: $_tid folder=$3 name=$1"
    return 0
}
