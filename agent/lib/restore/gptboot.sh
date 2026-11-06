# gptboot.sh -- #2585: BIOS boot.img outside the GPT partitions.
# POSIX sh (busybox ash). Bytes 440 onward belong to the new target table.

_gpt_boot_read() {
    # $1 = disk node, $2 = output file. dd can succeed on a short read.
    dd if="$1" of="$2" bs=1 count=440 2>> "$LOG_FILE" || return 1
    _gb_size=$(wc -c < "$2") || return 1
    [ "$_gb_size" -eq 440 ]
}

_gpt_boot_nonzero() {
    # 0 = non-zero, 1 = all zero, 2 = could not check.
    tr -d '\000' < "$1" > "$2" || return 2
    [ -s "$2" ]
}

_gpt_boot_base64() {
    _gb_encoded=$(base64 "$1") || return 1
    printf '%s' "$_gb_encoded" | tr -d '\n'
}

capture_gpt_boot_code() (
    # $1 = source node. An optional JSON member, including its leading comma.
    # Subshell + private files: no binary shell variables or shared drawer state.
    _gb_tmp=$(mktemp -d "$RUN_DIR/gpt-boot.XXXXXX") || return 1
    trap 'rm -rf "$_gb_tmp"' 0
    _gpt_boot_read "$1" "$_gb_tmp/code" || return 1
    _gpt_boot_nonzero "$_gb_tmp/code" "$_gb_tmp/nonzero"
    case $? in 0) ;; 1) return 0 ;; *) return 1 ;; esac
    _gb_value=$(_gpt_boot_base64 "$_gb_tmp/code") || return 1
    [ "${#_gb_value}" -eq 588 ] || return 1
    printf ',"gpt_boot_code_base64":"%s"' "$_gb_value"
)

_restore_gpt_boot_code() (
    # Absent means old image: no read or write of the target boot code.
    _gb_value=$(jq -er 'if has("gpt_boot_code_base64") then
        .gpt_boot_code_base64 | if type == "string" then
            if length == 588 and test("^[A-Za-z0-9+/]{587}=$") then .
            else error("invalid GPT boot code base64") end
        else error("GPT boot code must be a string") end
        else "" end' "$2" 2>> "$LOG_FILE") || return 1
    [ -n "$_gb_value" ] || return 0
    _gb_tmp=$(mktemp -d "$RUN_DIR/gpt-boot.XXXXXX") || return 1
    trap 'rm -rf "$_gb_tmp"' 0
    printf '%s' "$_gb_value" > "$_gb_tmp/encoded" || return 1
    base64 -d "$_gb_tmp/encoded" > "$_gb_tmp/code" 2>> "$LOG_FILE" || return 1
    _gb_size=$(wc -c < "$_gb_tmp/code") || return 1
    [ "$_gb_size" -eq 440 ] || return 1
    _gb_canonical=$(_gpt_boot_base64 "$_gb_tmp/code") || return 1
    [ "$_gb_canonical" = "$_gb_value" ] || return 1
    _gpt_boot_nonzero "$_gb_tmp/code" "$_gb_tmp/nonzero"
    case $? in 0) ;; 1) return 0 ;; *) return 1 ;; esac
    dd if="$_gb_tmp/code" of="$DEVROOT/$1" bs=1 count=440 conv=notrunc,fsync \
        2>> "$LOG_FILE" || return 1
    _gpt_boot_read "$DEVROOT/$1" "$_gb_tmp/readback" || return 1
    cmp "$_gb_tmp/code" "$_gb_tmp/readback" >> "$LOG_FILE" 2>&1
)

restore_gpt_boot_code() {
    # Keep the reason in the caller's shell for fail_written_target's card.
    _restore_gpt_boot_code "$1" "$2" || {
        PLAN_ERROR="$1: לא הצלחנו לשחזר ולאמת את קוד האתחול של GPT (440 בתים)"
        log "$PLAN_ERROR"; return 1
    }
}
