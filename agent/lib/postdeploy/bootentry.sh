# bootentry.sh -- the UEFI Boot#### entry for the restored Windows (#433).
# Runs after restore+grow, the hostname and the driver staging, before the
# reboot; called from stage_drivers (postdeploy.sh), which is the one hook
# both restore paths (the round and the single-station pull) go through.
# POSIX sh (busybox ash).
#
# Restoring partitions byte for byte restores the disk -- not the firmware's
# NVRAM. Boot#### and BootOrder live on the board, so a restored disk can be
# perfect and have no boot entry at all; the firmware then has only the
# removable-media fallback (\EFI\Boot\bootx64.efi), which the spec defines
# for removable media and which "most" firmwares also try on a fixed disk.
# "Most" is what #416 looked like on the build machine: no disk in F12.
#
# What this does, and does not do:
#   * Legacy BIOS (no efivarfs)   -> skipped, by name. There is no API.
#   * not a Windows image         -> skipped, by name.
#   * an entry already points at the ESP of THIS disk + bootmgfw.efi
#                                 -> reused (no duplicate; NVRAM is finite).
#   * otherwise efibootmgr --create-only, then efibootmgr -v is read BACK and
#     the entry must be in it (UEFI lets the firmware drop entries it does
#     not like -- a write is not evidence, R17 / עיקרון 5).
#   * BootNext = that entry, read back the same way: the first boot after
#     the restore is certain. BootOrder is NOT touched: on this network the
#     stations must keep PXE first, and `-c` (without -C) would put Windows
#     ahead of it for good.
#
# Failure is not silent and not a disk failure: the target stays `done`
# (the restore IS complete, and a top-level `failed` would re-offer the
# round on the next hello -- hello.py member_done -- i.e. a restore loop)
# but carries the reason in its `error` field, exactly like the hostname
# (#856). The outcome also lands in $RUN_DIR/bootentry.json for a
# technician at the machine (tmpfs, local only).

BOOTENTRY_LABEL="Windows Boot Manager"
BOOTENTRY_LOADER='\EFI\Microsoft\Boot\bootmgfw.efi'
ESP_TYPE_GUID="C12A7328-F81F-11D2-BA4B-00A0C93EC93B"

_bootentry_result() {
    # $1 = state (created/present/skipped/failed), $2 = entry number or "",
    # $3 = reason/error text (optional). Writes bootentry.json and logs.
    _key="error"; [ "$1" = "skipped" ] && _key="reason"
    if [ -n "${3:-}" ]; then
        printf '{"state":"%s","entry":"%s","%s":"%s"}\n' "$1" "$2" "$_key" "$(json_escape "$3")"
    else
        printf '{"state":"%s","entry":"%s"}\n' "$1" "$2"
    fi > "$RUN_DIR/bootentry.json"
    log "boot entry: $(cat "$RUN_DIR/bootentry.json")"
}

_bootentry_fail() {
    # $1 = disk, $2 = reason. Records, marks the target's error, returns 0.
    _bootentry_result "failed" "" "$2"
    _why="רשומת האתחול (Boot####) לא נוצרה: $2"
    _errf="$RUN_DIR/targets/$1/error"
    [ -s "$_errf" ] && _why="$(cat "$_errf") | $_why"
    target_set "$1" "done" "$_why"
    return 0
}

_bootentry_find() {
    # $1 = ESP partition number, $2 = ESP PARTUUID (lower case). Echoes the
    # number (upper-case hex) of the first Boot#### whose device path is
    # HD(<n>,GPT,<partuuid>,...) and whose file is bootmgfw.efi. Windows
    # writes the path in capitals, so the whole table is folded to lower
    # case first (tr, not grep -i: MSYS grep -i aborts on the dev station,
    # and one code path is better than two). Nothing echoed = no such
    # entry. -F: the path holds backslashes.
    _want=$(printf '%s' "$BOOTENTRY_LOADER" | tr 'A-Z' 'a-z')
    efibootmgr -v 2>> "$LOG_FILE" | tr 'A-Z' 'a-z' \
        | grep -F "hd($1,gpt,$2," | grep -F "$_want" \
        | sed -n 's/^boot\([0-9a-f][0-9a-f][0-9a-f][0-9a-f]\).*/\1/p' | head -n 1 \
        | tr 'a-f' 'A-F'
}

ensure_boot_entry() {
    # $1 = disk name (sda), $2 = manifest file. Always returns 0.
    rm -f "$RUN_DIR/bootentry.json"
    if ! manifest_plan "$2" 2>/dev/null | awk -F'|' '$3 == "windows"' | grep -q .; then
        _bootentry_result "skipped" "" "not_windows"; return 0
    fi
    _efivars="${SYSROOT:-}/sys/firmware/efi/efivars"
    # Mounted and populated -- an empty directory is a mount that failed
    # (init says so), not a firmware with no variables.
    if [ ! -d "$_efivars" ] || [ -z "$(ls -A "$_efivars" 2>/dev/null)" ]; then
        _bootentry_result "skipped" "" "no_efivars"; return 0
    fi

    _esp_idx=$(manifest_plan "$2" 2>/dev/null | awk -F'|' -v g="$ESP_TYPE_GUID" \
        'toupper($2) == g || $3 == "esp" { print $1; exit }')
    [ -n "$_esp_idx" ] || { _bootentry_fail "$1" "אין מחיצת ESP במניפסט"; return 0; }
    _esp_node=$(partition_node "$1" "$_esp_idx")
    _esp_uuid=$(blkid -s PARTUUID -o value "$_esp_node" 2>> "$LOG_FILE" | tr 'A-F' 'a-f')
    case "$_esp_uuid" in
        [0-9a-f]*-*-*-*-[0-9a-f]*) ;;
        *) _bootentry_fail "$1" "לא הצלחנו לקרוא את ה-PARTUUID של $_esp_node"; return 0 ;;
    esac

    _num=$(_bootentry_find "$_esp_idx" "$_esp_uuid")
    if [ -n "$_num" ]; then
        _state="present"
    else
        efibootmgr -C -d "$DEVROOT/$1" -p "$_esp_idx" -L "$BOOTENTRY_LABEL" \
            -l "$BOOTENTRY_LOADER" >> "$LOG_FILE" 2>&1
        _crc=$?
        # The read-back is the evidence, not the exit code: the entry must
        # be in the table, pointing at this disk's ESP.
        _num=$(_bootentry_find "$_esp_idx" "$_esp_uuid")
        if [ -z "$_num" ]; then
            _bootentry_fail "$1" "efibootmgr --create-only rc=$_crc ולא נמצא Boot#### עבור HD($_esp_idx,GPT,$_esp_uuid)/$BOOTENTRY_LOADER בקריאה בחזרה"
            return 0
        fi
        _state="created"
    fi

    efibootmgr -n "$_num" >> "$LOG_FILE" 2>&1
    _nrc=$?
    _next=$(efibootmgr 2>> "$LOG_FILE" | sed -n 's/^BootNext: *//p' | tr 'a-f' 'A-F' | tr -d ' \r')
    if [ "$_next" != "$_num" ]; then
        _bootentry_fail "$1" "Boot$_num $_state, אך BootNext נקרא בחזרה '$_next' (efibootmgr -n rc=$_nrc)"
        return 0
    fi
    _bootentry_result "$_state" "$_num"
    return 0
}
