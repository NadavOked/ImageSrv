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

build_new_folder() {
    # $1 = folder name (already checked by build_name_ok). Prints the name of
    # the folder it created. POST /folders is admin_only; a 403 here means
    # the account is not what the menu was built from, and that stops the
    # capture instead of carrying on with a folder that does not exist.
    _nm=$1
    build_name_ok "$_nm" || return 1
    printf '{"name":"%s"}' "$(json_escape "$_nm")" \
        > "$RUN_DIR/folder_new.json"
    _code=$(console_post "folders" "$RUN_DIR/folder_new.json" \
        "$RUN_DIR/folder_resp.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "Could not create the folder" >&2
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
