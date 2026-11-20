# drawerout.sh -- a written disk is taken out safely, and said so: `detached`, then `removed` (#3181).
# POSIX sh (busybox ash).
#
# Only in a production line (prodline.sh): the operator pulls a written disk while the machine is on, and pushes
# the next one in. "Written" is not "safe to pull" -- the server shows "אפשר להוציא" only on `detached: true`
# (interfaces.md section 4), and this file is the only place that says it:
#
#   which     a target that is `done` AND was read back (`readback`: verified or sampled, readback.sh) -- after
#             its name, its drivers and its incomplete-write mark, the last writes of a round. Done and not
#             verified is said in the log by name, and stays attached;
#   how       each partition node and the disk are flushed (blockdev --flushbufs: the page cache, and the drive's
#             own write cache), a spinning disk is stopped (hdparm -y; an SSD has nothing to stop -- refused is
#             said, and is not the evidence), and the device is deleted from the kernel
#             (/sys/block/<d>/device/delete);
#   evidence  the flush's exit code AND the node gone from /sys/block (principle 5). Either one missing = no
#             `detached`, the reason in the log, and the disk is not offered for pulling;
#   removed   a detached disk has no node to vanish, so its pull is the kernel's own word about its PORT, after
#             the detach: DevExch, or "SATA link down" with no "link up" after it (drawer_port_gone, verdict.sh).
#             Or another disk answering in its slot (prodline.sh). The target stays in the report -- `done`,
#             `detached`, `removed` -- until the wave ends, or until a new disk takes its slot.
#
# Not measured on metal: whether the delete itself prints a link line on these controllers (it would read as a
# pull). The lines counted are those stamped after the delete returned.

DRAWER_OUT_GONE_S="${DRAWER_OUT_GONE_S:-5}"   # how long the kernel has to drop the node after the delete

_drawer_out_delete() { echo 1 > "$SYSROOT/sys/block/$1/device/delete"; }   # the kernel's side: tests fake it

_drawer_out() {   # $1 = disk. 0 = flushed, and its node left the kernel. DO_WHY = why not
    _do_s="$SYSROOT/sys/block/$1"
    for _do_p in "$_do_s"/"$1"*; do   # every partition written through, then the disk
        [ -e "$_do_p/partition" ] || continue
        blockdev --flushbufs "$DEVROOT/${_do_p##*/}" >> "$LOG_FILE" 2>&1 || { DO_WHY="ריקון המטמון של ${_do_p##*/} נכשל"; return 1; }
    done
    blockdev --flushbufs "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || { DO_WHY="ריקון המטמון של הדיסק נכשל (blockdev --flushbufs)"; return 1; }
    if [ "$(cat "$_do_s/queue/rotational" 2> /dev/null)" = 1 ]; then
        hdparm -y "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || log "$1: הדיסק לא נעצר (hdparm -y) -- מנותק בלי עצירת סיבוב"
    fi
    _drawer_out_delete "$1" 2>> "$LOG_FILE" || { DO_WHY="הקרנל לא קיבל את בקשת הניתוק (device/delete)"; return 1; }
    _do_k=0
    while [ -e "$_do_s" ] && [ "$_do_k" -lt $((DRAWER_OUT_GONE_S * 10)) ]; do sleep 0.1 2> /dev/null || sleep 1; _do_k=$((_do_k + 1)); done
    [ ! -e "$_do_s" ] || { DO_WHY="ההתקן לא יצא מהקרנל תוך $DRAWER_OUT_GONE_S שניות אחרי הניתוק"; return 1; }
}

drawer_out_detach() {   # $@ = targets. In a production line: each one done and verified is detached -- once, and said only when it happened
    prodline_on || return 0
    for _dd in "$@"; do
        _dd_t="$RUN_DIR/targets/$_dd"
        [ "$(cat "$_dd_t/state" 2> /dev/null)" = "done" ] && [ ! -e "$_dd_t/detached" ] && [ ! -e "$_dd_t/detach_failed" ] || continue
        case "$(cat "$_dd_t/readback" 2> /dev/null)" in
            verified|sampled) ;;
            *) : > "$_dd_t/detach_failed"; log "$_dd: נכתב, ולא אומת בקריאה חוזרת -- אינו מנותק, ואינו מוצע להוצאה"; continue ;;
        esac
        # the disk under this name is the one that was written (#2434) -- compared, never assumed: another disk's device is not deleted
        target_same "$_dd" || { : > "$_dd_t/detach_failed"; log "$_dd: הדיסק שבשם הזה אינו הדיסק שנכתב -- אינו מנותק"; continue; }
        if _drawer_out "$_dd"; then
            cut -d' ' -f1 "$SYSROOT/proc/uptime" > "$_dd_t/detached" 2> /dev/null || : > "$_dd_t/detached"
            log "$_dd (serial $(target_bound_serial "$_dd"), חריץ $(disk_port_kept "$_dd")): נותק בבטחה -- אפשר להוציא"
        else
            : > "$_dd_t/detach_failed"; log "$_dd: הניתוק הבטוח נכשל -- $DO_WHY. הדיסק נכתב, ואינו מוצע להוצאה"
        fi
    done
}

disk_port_kept() { _dk=$(cat "$RUN_DIR/targets/$1/seen" 2> /dev/null); _dk=${_dk%|*}; _dk=${_dk##*|}; printf '%s' "${_dk:-?}"; }   # the slot it was chosen in: its node is gone

drawer_out_tick() {   # a tick while the machine is in the wave: a detached disk that was pulled is `removed`, by its port's own line
    _dt_any=0; for _dx in "$RUN_DIR"/targets/*/detached; do [ ! -s "$_dx" ] || [ -e "${_dx%/*}/removed" ] || _dt_any=1; done
    [ "$_dt_any" = 1 ] || return 0
    dmesg > "$CHUNK_DIR/dmesg.out" 2> /dev/null || return 0   # not read = not known: asked again at the next tick
    for _dx in "$RUN_DIR"/targets/*/detached; do
        [ -s "$_dx" ] && [ ! -e "${_dx%/*}/removed" ] || continue
        _dx_h=${_dx%/*}; _dx_h=${_dx_h##*/}
        # only what the kernel said AFTER the detach: the lines of the round's own write are not this disk's pull
        awk -v t="$(cat "$_dx")" 'match($0, /^\[ *[0-9]+\.[0-9]+\]/) && substr($0, RSTART + 1, RLENGTH - 2) + 0 >= t + 0' \
            "$CHUNK_DIR/dmesg.out" > "$CHUNK_DIR/dmesg.out.after" 2> /dev/null || continue
        drawer_port_gone "$_dx_h" "$CHUNK_DIR/dmesg.out.after" || continue
        : > "${_dx%/*}/removed"; log "$_dx_h (חריץ $(disk_port_kept "$_dx_h")): הדיסק שנותק הוצא מהמגירה"
    done
    rm -f "$CHUNK_DIR/dmesg.out" "$CHUNK_DIR/dmesg.out.after"
}

drawer_out_json() {   # $1 = target -> what the progress report adds to it (drawer_back_json, drawerplug.sh)
    [ ! -e "$RUN_DIR/targets/$1/detached" ] || printf ',"detached":true'
    [ ! -e "$RUN_DIR/targets/$1/removed" ] || printf ',"removed":true'
}
