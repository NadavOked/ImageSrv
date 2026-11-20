# diskslots.sh -- #3051: which disk is which, for the disk step. POSIX sh (busybox ash).
# shellcheck disable=SC2154 # sourced by imagesrv-install: SYSROOT, DEVROOT and RUN_DIR are its own
#
# Three identical disks look the same by model and size, and the one chosen is erased. The
# inventory therefore carries, per disk, what a person can point at:
#   serial=<serial>      disk_serial (diskinfo.sh); nothing when the disk reports none
#   slot=<N>             the drawer the mapping of the clone stick gives this disk's connection
#   slotname=<name>      that connection's own name (slot_names), when it has one
# The mapping is the agent's own run-dir files, written from the stick's settings.json
# (usbmap.sh -> slotnames.sh drawer_slots_refresh / slot_names_refresh): $SLOTS_DIR/drawer_slots
# ("ok", then "<drawer>|<key>") and $SLOTS_DIR/slot_names ("ok", then "<port>|<name>").
# No file, a file that does not say "ok", or a disk whose connection is not in it: no slot --
# never a number guessed from the order of /dev/sdX (that is the mistake the mapping prevents).
# The installer only reads: nothing here writes to the stick.

SLOTS_DIR="${SLOTS_DIR:-${RUN_DIR:-/run/imagesrv}}"

disk_slot_fields() {
    # $1 = disk name. Prints "|slot=N" and "|slotname=NAME" (each only when known).
    _ds_f="$SLOTS_DIR/drawer_slots"
    [ -r "$_ds_f" ] && [ "$(head -n 1 "$_ds_f")" = ok ] || return 0
    _ds_port=$(disk_port "$1"); case "$_ds_port" in ''|*[!0-9]*) _ds_port=0 ;; esac
    _ds_key=$(disk_key "$1" "$_ds_port"); [ -n "$_ds_key" ] || return 0
    _ds_n=$(awk -F'|' -v k="$_ds_key" 'NR > 1 && $2 == k { print $1; exit }' "$_ds_f")
    case "$_ds_n" in ''|*[!0-9]*) return 0 ;; esac
    printf '|slot=%s' "$_ds_n"
    case "$_ds_key" in sata:*) [ -r "$SLOTS_DIR/slot_names" ] && [ "$(head -n 1 "$SLOTS_DIR/slot_names")" = ok ] || return 0
        _ds_name=$(awk -F'|' -v p="${_ds_key#sata:}" 'NR > 1 && $1 == p { sub(/^[^|]*\|/, ""); print; exit }' "$SLOTS_DIR/slot_names" | tr '\r\n|' '   ')
        [ -z "$_ds_name" ] || printf '|slotname=%s' "$_ds_name" ;; esac
}

disk_serial_field() {
    # $1 = disk name. "|serial=X" when the disk reports one; the | of a field is a space.
    _dsf_s=$(trim "$(disk_serial "$1" | tr '\r\n|' '   ')")
    [ -z "$_dsf_s" ] || printf '|serial=%s' "$_dsf_s"
}
