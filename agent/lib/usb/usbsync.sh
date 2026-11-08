# usbsync.sh -- verify the server's USB bundle before using any of it. POSIX sh.
_ys_bad() { _ys_error=$1; return 1; }

_ys_need() {
    jq -e -s "length == 1 and (.[0] | $1)" "$_ys_json" >/dev/null || { _ys_bad "$2"; return 1; }
}

_ys_size() {
    _yb_n=$(wc -c < "$1") || return 1
    case "$_yb_n" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s' "$_yb_n"
}

_ys_hash() {
    _yh_out=$(sha256sum "$1") || return 1
    _yh_hex=${_yh_out%% *}
    case "$_yh_hex" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#_yh_hex}" = 64 ] || return 1
    printf '%s' "$_yh_hex"
}

_ys_tables_hash() {
    # Check every reader separately: a pipeline's final success cannot prove cat read.
    { cat "$1/smart-knowledge.tsv" && printf '\000' && cat "$1/hw-issues.tsv"; } > "$_ys_dest/.tables" || return 1
    _yt_hex=$(_ys_hash "$_ys_dest/.tables") || return 1
    rm -f "$_ys_dest/.tables" || return 1
    printf '%.12s' "$_yt_hex"
}

_ys_version() {
    _yv_rest=${1#v}
    case "$_yv_rest" in *.*.*) ;; *) return 1 ;; esac
    _yv_major=${_yv_rest%%.*}; _yv_rest=${_yv_rest#*.}
    _yv_minor=${_yv_rest%%.*}; _yv_rest=${_yv_rest#*.}
    _yv_patch=${_yv_rest%%[!0-9]*}
    for _yv_c in "$_yv_major" "$_yv_minor" "$_yv_patch"; do
        case "$_yv_c" in ''|*[!0-9]*|???????*) return 1 ;; esac
        # Strip zeros before shell arithmetic, which otherwise interprets octal.
        while [ "${_yv_c#0}" != "$_yv_c" ]; do _yv_c=${_yv_c#0}; done
        printf '%s ' "$(( ${_yv_c:-0} + 0 ))"
    done
}

_ys_older() {
    # Six decimal components: server major/minor/patch, then agent major/minor/patch.
    while [ "$#" -gt 3 ]; do
        [ "$1" -lt "$4" ] && return 0
        [ "$1" -gt "$4" ] && return 1
        shift
    done
    return 1
}

_ys_manifest() {
    _ys_need 'type == "object"' 'manifest.json: invalid JSON object' || return 1
    _ys_need '.schema == 1' 'manifest.json: schema must be 1' || return 1
    _ys_need '.kind == "imagesrv-server-sync"' 'manifest.json: kind is invalid' || return 1
    _ym_keys='["schema","kind","written_at","server_version","device_id","smart_table_version","models","files"]'
    _ym_bad=$(jq -er "((keys - $_ym_keys) + ($_ym_keys - keys)) | join(\", \")" "$_ys_json") || {
        _ys_bad 'manifest.json: could not check keys'; return 1;
    }
    [ -z "$_ym_bad" ] || { _ys_bad "manifest.json: unexpected or missing key $_ym_bad"; return 1; }
    _ys_need '.written_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")' 'manifest.json: written_at is invalid' || return 1
    _ys_need '.server_version | type == "string" and length <= 64 and test("^[A-Za-z0-9._+-]*$")' 'manifest.json: server_version is invalid' || return 1
    _ys_need '.device_id | type == "string" and length >= 1 and length <= 128 and (explode | all(.[]; . >= 32 and . != 127))' 'manifest.json: device_id is invalid' || return 1
    _ys_need '.smart_table_version | type == "string" and test("^[0-9a-f]{12}$")' 'manifest.json: smart_table_version is invalid' || return 1
    _ys_need '.models | type == "number" and . >= 0 and floor == .' 'manifest.json: models is invalid' || return 1
    _ys_need '.files | type == "object"' 'manifest.json: files must be an object' || return 1
    _ym_files='["rates.json","smart-knowledge.tsv","hw-issues.tsv"]'
    _ym_bad=$(jq -er ".files | ((keys - $_ym_files) + ($_ym_files - keys)) | join(\", \")" "$_ys_json") || {
        _ys_bad 'manifest.json: could not check files keys'; return 1;
    }
    [ -z "$_ym_bad" ] || { _ys_bad "manifest.json: files unexpected or missing key $_ym_bad"; return 1; }
    for _ym_name in rates.json smart-knowledge.tsv hw-issues.tsv; do
        _ym_filter=".files[\"$_ym_name\"]"
        _ys_need "$_ym_filter | type == \"object\" and keys == [\"sha256\",\"size\"]" "manifest.json: files.$_ym_name keys are invalid" || return 1
        _ys_need "$_ym_filter.sha256 | type == \"string\" and test(\"^[0-9a-f]{64}$\")" "manifest.json: $_ym_name sha256 is invalid" || return 1
        _ys_need "$_ym_filter.size | type == \"number\" and . >= 0 and floor == ." "manifest.json: $_ym_name size is invalid" || return 1
    done
}

_ys_rates() {
    _ys_need 'type == "object"' 'rates.json: invalid JSON object' || return 1
    _ys_need '.schema == 1' 'rates.json: schema must be 1' || return 1
    _ys_need '.kind == "imagesrv-rates"' 'rates.json: kind is invalid' || return 1
    _ys_need '.entries | type == "array" and length <= 500' 'rates.json: entries must be an array of at most 500' || return 1
    _ys_need 'all(.entries[]; type == "object" and keys == ["basis","controller","mbps","model","samples","source"])' 'rates.json: entry keys are invalid' || return 1
    _ys_need 'all(.entries[]; .model | type == "string" and length >= 1 and length <= 128)' 'rates.json: model is invalid' || return 1
    _ys_need 'all(.entries[]; .controller | . == null or . == "ahci" or . == "ide" or . == "other")' 'rates.json: controller is invalid' || return 1
    _ys_need 'all(.entries[]; .basis | . == "preflight" or . == "copy")' 'rates.json: basis is invalid' || return 1
    _ys_need 'all(.entries[]; .source | . == "server" or . == "usb")' 'rates.json: source is invalid' || return 1
    for _yr_field in mbps samples; do
        _ys_need "all(.entries[]; .$_yr_field | type == \"number\" and . > 0 and floor == .)" "rates.json: $_yr_field must be a positive integer" || return 1
    done
}

_ys_verify() {
    [ ! -L "$_ys_src" ] || { _ys_bad 'server-sync is a symlink'; return 1; }
    [ -d "$_ys_src" ] || { _ys_bad 'server-sync is not a directory'; return 1; }
    for _yc_name in manifest.json rates.json smart-knowledge.tsv hw-issues.tsv; do
        [ ! -L "$_ys_src/$_yc_name" ] || { _ys_bad "$_yc_name is a symlink"; return 1; }
        [ -f "$_ys_src/$_yc_name" ] || { _ys_bad "$_yc_name is missing or not a regular file"; return 1; }
        _yc_size=$(_ys_size "$_ys_src/$_yc_name") || { _ys_bad "$_yc_name: could not read size"; return 1; }
        [ "$_yc_size" -le 1048576 ] || { _ys_bad "$_yc_name exceeds 1048576 bytes"; return 1; }
    done
    _ys_json="$_ys_src/manifest.json"; _ys_manifest || return 1
    rm -rf "$_ys_dest" || { _ys_bad 'usb.sync.d: could not remove old copies'; return 1; }
    mkdir -p "$_ys_dest" || { _ys_bad 'usb.sync.d: could not create directory'; return 1; }
    for _yc_name in manifest.json rates.json smart-knowledge.tsv hw-issues.tsv; do
        cp "$_ys_src/$_yc_name" "$_ys_dest/$_yc_name" || { _ys_bad "$_yc_name: could not copy"; return 1; }
    done
    # From here onward only RAM copies are read. Revalidate the copied manifest too.
    _ys_json="$_ys_dest/manifest.json"; _ys_manifest || return 1
    for _yc_name in manifest.json rates.json smart-knowledge.tsv hw-issues.tsv; do
        _yc_size=$(_ys_size "$_ys_dest/$_yc_name") || { _ys_bad "$_yc_name: could not read copied size"; return 1; }
        [ "$_yc_size" -le 1048576 ] || { _ys_bad "$_yc_name exceeds 1048576 bytes"; return 1; }
        [ "$_yc_name" != manifest.json ] || continue
        _yc_expected=$(jq -er ".files[\"$_yc_name\"].size | tostring" "$_ys_json") || { _ys_bad "$_yc_name: could not read manifest size"; return 1; }
        [ "$_yc_size" = "$_yc_expected" ] || { _ys_bad "$_yc_name: size does not match the manifest"; return 1; }
        _yc_hash=$(_ys_hash "$_ys_dest/$_yc_name") || { _ys_bad "could not hash $_yc_name"; return 1; }
        _yc_expected=$(jq -er ".files[\"$_yc_name\"].sha256" "$_ys_json") || { _ys_bad "$_yc_name: could not read manifest sha256"; return 1; }
        [ "$_yc_hash" = "$_yc_expected" ] || { _ys_bad "$_yc_name: sha256 does not match the manifest"; return 1; }
    done
    _ys_json="$_ys_dest/rates.json"; _ys_rates || return 1
    for _yc_name in smart-knowledge.tsv hw-issues.tsv; do
        [ -s "$_ys_dest/$_yc_name" ] || { _ys_bad "$_yc_name is empty"; return 1; }
    done
    _yc_tables=$(_ys_tables_hash "$_ys_dest") || { _ys_bad 'smart_table_version: could not hash the tables'; return 1; }
    _ys_json="$_ys_dest/manifest.json"
    _yc_expected=$(jq -er '.smart_table_version' "$_ys_json") || { _ys_bad 'smart_table_version could not be read'; return 1; }
    [ "$_yc_tables" = "$_yc_expected" ] || { _ys_bad 'smart_table_version in the manifest does not match the tables'; return 1; }
    _yc_server=$(jq -er '.server_version' "$_ys_json") || { _ys_bad 'server_version could not be read'; return 1; }
    _yc_sv=$(_ys_version "$_yc_server") && _yc_av=$(_ys_version "${AGENT_VERSION:-unknown}") || {
        _ys_bad "the versions cannot be compared (server_version '$_yc_server', AGENT_VERSION '${AGENT_VERSION:-unknown}'): the server's tables were not used"; return 1;
    }
    USB_SYNC_STATE=ok
    # shellcheck disable=SC2086 # each is three numbers, one argument apiece, on purpose
    if _ys_older $_yc_sv $_yc_av; then
        _ys_reason="server-sync verified, written by an older server ($_yc_server < $AGENT_VERSION): the stick's own tables stay"
    elif _yc_builtin=$(_ys_tables_hash "$_ys_builtin") && [ "$_yc_builtin" = "$_yc_tables" ]; then
        _ys_reason='same tables as built in'
    else
        USB_SYNC_TABLES=server
        _ys_reason="verified tables from server $_yc_server adopted by stick $AGENT_VERSION"
    fi
    # Nothing consumes rates.json yet; a future ETA will read usb.sync.d/rates.json.
}

usb_sync_load() {
    USB_SYNC_STATE=none; USB_SYNC_TABLES=builtin; USB_SYNC_NOTICE=''
    _ys_builtin=$SMART_KB; _ys_src="$USB_DATA_DIR/server-sync"; _ys_dest="$RUN_DIR/usb.sync.d"
    _ys_error=''; _ys_reason='server-sync is absent or USB data is not mounted'
    if [ "${USB_DATA_STATE:-none}" = mounted ] && { [ -e "$_ys_src" ] || [ -L "$_ys_src" ]; }; then
        if ! _ys_verify; then USB_SYNC_STATE=failed; fi
    fi
    if ! printf '%s\n' "$USB_SYNC_STATE" > "$RUN_DIR/usb.sync"; then
        USB_SYNC_STATE=failed; _ys_error='usb.sync: could not record state'
    elif _ys_readback=$(cat "$RUN_DIR/usb.sync") && [ "$_ys_readback" = "$USB_SYNC_STATE" ]; then
        :
    else
        USB_SYNC_STATE=failed; _ys_error='usb.sync: state readback failed'
    fi
    if [ "$USB_SYNC_STATE" = failed ]; then
        USB_SYNC_TABLES=builtin
        if ! rm -rf "$_ys_dest"; then _ys_error="$_ys_error; usb.sync.d: cleanup failed"; fi
        if ! printf 'failed\n' > "$RUN_DIR/usb.sync"; then _ys_error="$_ys_error; usb.sync: could not record failure"; fi
        USB_SYNC_NOTICE="נתוני הסנכרון מהשרת (server-sync) לא בשימוש: $_ys_error"; _ys_reason=$_ys_error
    elif [ "$USB_SYNC_TABLES" = server ]; then
        SMART_KB=$_ys_dest; export SMART_KB
    fi
    log "usbclone: server-sync: $_ys_reason"
    return 0
}
