# diskused.sh -- how much of each disk is in use, for the cloner screen (#2030).
# POSIX sh (busybox ash).
#
# The drawer card says the disk's size and how much of it is taken: before a
# round, what the write is about to erase; after it, what was written (that
# one is the round's own counter, not this file). The size is a sysfs read.
# The used bytes need the filesystems read -- a read-only mount per partition,
# a few seconds a disk -- so it is measured ONCE per disk, in the background,
# while the cloner is idle, and cached under $GUI_DIR/used/. The state loop
# runs once a second and only ever reads the cache.
#
# Rules this file keeps (principle 5):
#   * "not measured" is -1, never 0. A dirty NTFS that will not mount, a
#     filesystem blkid cannot read, a umount that failed: -1, and the screen
#     says "לא נמדד". A disk with no partition table at all is a measured 0.
#   * Nothing is mounted while a round owns the disks: the scan stops at the
#     first targets/<dev> directory, before every partition, and drops a value
#     it was taking when the round arrived.
#   * A disk a round has written is stale: its cache is marked dirty and the
#     record says -1 until the next idle scan measures it again.
# The convention is lcinv.sh's: swap is 0, a filesystem partclone does not know
# counts as its whole partition, the rest is what df says on a read-only mount.

DU_DIR="${GUI_DIR:-$RUN_DIR/gui}/used"

_du_round_open() {
    for _du_t in "$RUN_DIR"/targets/*/; do [ -d "$_du_t" ] && return 0; done
    return 1
}

_du_mount_used() {
    # $1 = node, $2 = fs. Bytes in use, or nothing when it was not measured.
    _du_m="$DU_DIR/mnt"; mkdir -p "$_du_m" || return 1
    _du_o=ro; case "$2" in ext2|ext3|ext4) _du_o=ro,noload ;; btrfs) _du_o=ro,nologreplay ;; esac
    _probe_mount "$1" "$2" "$_du_o" "$_du_m" || return 1
    _du_b=$(df -kP "$_du_m" 2>> "$LOG_FILE" | awk 'NR==2 && $3 ~ /^[0-9]+$/ { printf "%.0f\n", $3 * 1024 }')
    umount "$_du_m" 2>> "$LOG_FILE" || { log "diskused: $1 would not unmount -- not a measurement"; return 1; }
    [ -n "$_du_b" ] && echo "$_du_b"
}

disk_used_measure() {
    # $1 = disk. stdout: the bytes in use, or -1 (not measured).
    _du_sum=0; _du_n=0
    for _du_pf in "$SYSROOT/sys/block/$1"/*/partition; do
        [ -r "$_du_pf" ] || continue
        if _du_round_open; then echo -1; return 0; fi
        _du_idx=$(cat "$_du_pf" 2>/dev/null); _du_secs=$(cat "${_du_pf%/partition}/size" 2>/dev/null)
        case "$_du_idx:$_du_secs" in *[!0-9:]*|:*|*:) echo -1; return 0 ;; esac
        _du_node=$(partition_node "$1" "$_du_idx"); _du_fs=$(_fs_of "$_du_node"); _du_n=$((_du_n + 1))
        case "$_du_fs" in swap) continue ;; unreadable) echo -1; return 0 ;; esac
        case "$(partclone_for_fs "$_du_fs")" in
            partclone.dd) _du_u=$((_du_secs * 512)) ;;
            *) _du_u=$(_du_mount_used "$_du_node" "$_du_fs") ;;
        esac
        case "$_du_u" in ''|*[!0-9]*) echo -1; return 0 ;; esac
        _du_sum=$((_du_sum + _du_u))
    done
    # no partition in sysfs: an empty disk (a table that says so) or one we could not read
    if [ "$_du_n" -eq 0 ] && [ "$(disk_scheme "$DEVROOT/$1")" != none ]; then echo -1; return 0; fi
    echo "$_du_sum"
}

_du_valid() {
    # $1 = disk, $2 = its size in bytes: a cache entry that says this size and was not written over
    [ -f "$DU_DIR/$1" ] && [ ! -f "$DU_DIR/$1.dirty" ] || return 1
    read -r _du_cs _du_cu < "$DU_DIR/$1" 2>/dev/null && [ "$_du_cs" = "$2" ]
}

disk_used_scan() {
    # Background: measure every disk that has no valid value, while no round is open.
    mkdir -p "$DU_DIR" || return 1
    for _du_d in $(list_disks); do
        _du_round_open && break
        lc_is_boot_media "$_du_d" 2>/dev/null && continue
        _du_s=$(cat "$SYSROOT/sys/block/$_du_d/size" 2>/dev/null)
        case "$_du_s" in ''|*[!0-9]*) continue ;; esac
        _du_s=$((_du_s * 512)); _du_valid "$_du_d" "$_du_s" && continue
        rm -f "$DU_DIR/$_du_d" "$DU_DIR/$_du_d.dirty"
        _du_v=$(disk_used_measure "$_du_d")
        _du_round_open && break   # it arrived while we measured: the number may be torn
        echo "$_du_s $_du_v" > "$DU_DIR/$_du_d.next" && mv "$DU_DIR/$_du_d.next" "$DU_DIR/$_du_d"
    done
    rm -f "$DU_DIR/scan.pid"
}

disk_used_records() {
    # $1 = the disks. cused=dev|size|used per disk whose size reads; used -1 =
    # not measured. Starts the background scan when something is unmeasured.
    mkdir -p "$DU_DIR" 2>/dev/null || return 0
    _du_todo=0
    for _du_d in $1; do
        _du_s=$(cat "$SYSROOT/sys/block/$_du_d/size" 2>/dev/null)
        case "$_du_s" in ''|*[!0-9]*) continue ;; esac
        _du_s=$((_du_s * 512)); _du_u=-1
        case "$(cat "$RUN_DIR/targets/$_du_d/state" 2>/dev/null)" in
            writing|verifying|done|failed|cancelled) : > "$DU_DIR/$_du_d.dirty" ;;   # a round wrote it: the old number is gone
        esac
        if _du_valid "$_du_d" "$_du_s"; then _du_u=$_du_cu
        else _du_round_open || _du_todo=1; fi
        printf 'cused=%s|%s|%s\n' "$_du_d" "$_du_s" "$_du_u"
    done
    [ "$_du_todo" = 1 ] || return 0
    _du_p=$(cat "$DU_DIR/scan.pid" 2>/dev/null)
    case "$_du_p" in ''|*[!0-9]*) ;; *) kill -0 "$_du_p" 2>/dev/null && return 0 ;; esac
    disk_used_scan < /dev/null > /dev/null 2>&1 &
    echo "$!" > "$DU_DIR/scan.pid"
    return 0
}
