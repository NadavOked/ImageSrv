# disks.sh -- shared disk inventory and destructive partitioning. POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module consumes and exports engine state

PROC_MOUNTS="${PROC_MOUNTS:-${SYSROOT:-}/proc/mounts}"

iso_source_device() {
    if [ -n "${ISO_SCAN_DEVICE:-}" ]; then printf '%s\n' "$ISO_SCAN_DEVICE"; return; fi
    if [ -r "$PROC_MOUNTS" ]; then
        while IFS=' ' read -r _id_dev _id_mnt _id_rest; do
            [ "$_id_mnt" = /cdrom ] && { printf '%s\n' "$_id_dev"; return; }
        done < "$PROC_MOUNTS"
    fi
    for _id_file in "${SYSROOT:-}/var/lib/iso-scan/device" \
        "${SYSROOT:-}/run/imagesrv/iso-device"; do
        if [ -r "$_id_file" ]; then IFS= read -r _id_dev < "$_id_file"; printf '%s\n' "$_id_dev"; return; fi
    done
}

disk_is_iso() {
    _di_name=$1; _di_src=$(iso_source_device)
    [ -n "$_di_src" ] || return 1
    case "$_di_src" in
        "/dev/$_di_name"|"/dev/$_di_name"[0-9]*|"/dev/$_di_name"p[0-9]*) return 0 ;;
        "$DEVROOT/$_di_name"|"$DEVROOT/$_di_name"[0-9]*|"$DEVROOT/$_di_name"p[0-9]*) return 0 ;;
    esac
    return 1
}

whole_disk_exists() {
    _wd_name=$1
    _wd_seen=
    for _wd_seen in $(list_disks); do [ "$_wd_seen" = "$_wd_name" ] && break; done
    [ "${_wd_seen:-}" = "$_wd_name" ] || return 1
    if [ -n "${SYSROOT:-}" ]; then [ -e "$DEVROOT/$_wd_name" ]; else [ -b "$DEVROOT/$_wd_name" ]; fi
}

partition_node() {
    case "$1" in *[0-9]) printf '%s/%sp%s' "$DEVROOT" "$1" "$2" ;; *) printf '%s/%s%s' "$DEVROOT" "$1" "$2" ;; esac
}

partition_node_ready() {
    if [ -n "${SYSROOT:-}" ]; then [ -e "$1" ]; else [ -b "$1" ]; fi
}

disk_bus() {
    _db_name=$1
    if [ -r "$SYSROOT/sys/block/$_db_name/device/bus" ]; then
        IFS= read -r _db_bus < "$SYSROOT/sys/block/$_db_name/device/bus"
    else
        _db_path=$(readlink -f "$SYSROOT/sys/block/$_db_name/device") || return 1
        case "$_db_path" in *nvme*) _db_bus=nvme ;; *usb*) _db_bus=usb ;; *ata*) _db_bus=sata ;;
            *virtio*) _db_bus=virtio ;; *scsi*) _db_bus=scsi ;; *) _db_bus=unknown ;; esac
    fi
    printf '%s' "$_db_bus" | tr '\r\n|' '   '
}

blkid_value() {
    _bv_tag=$1; _bv_node=$2
    _bv_out=$(blkid -s "$_bv_tag" -o value "$_bv_node" 2>/dev/null); _bv_rc=$?
    case "$_bv_rc" in 0) printf '%s' "$_bv_out" ;; 2) return 2 ;;
        *) printf 'installer: [inventory-blkid] blkid failed for %s (%s, rc=%s)\n' \
            "$_bv_node" "$_bv_tag" "$_bv_rc" >&2; return 3 ;; esac
}

disk_contents() {
    _dc_name=$1; _dc_node="$DEVROOT/$_dc_name"; _dc_count=0; _dc_types=
    _dc_scheme=$(blkid_value PTTYPE "$_dc_node"); _dc_rc=$?
    [ "$_dc_rc" -eq 0 ] || [ "$_dc_rc" -eq 2 ] || return 3
    [ -n "$_dc_scheme" ] || _dc_scheme=none
    [ "$_dc_scheme" != dos ] || _dc_scheme=mbr
    for _dc_path in "$SYSROOT/sys/block/$_dc_name/$_dc_name"*; do
        [ -r "$_dc_path/partition" ] || continue
        _dc_part=${_dc_path##*/}; _dc_count=$((_dc_count + 1))
        _dc_type=$(blkid_value TYPE "$DEVROOT/$_dc_part"); _dc_trc=$?
        [ "$_dc_trc" -eq 0 ] || [ "$_dc_trc" -eq 2 ] || return 3
        [ -n "$_dc_type" ] || _dc_type=unknown
        [ -z "$_dc_types" ] || _dc_types="$_dc_types, "
        _dc_types="$_dc_types$_dc_type"
    done
    if [ "$_dc_count" -gt 0 ]; then
        printf '%s:%s partitions (%s)' "$_dc_scheme" "$_dc_count" "$_dc_types"
        return
    fi
    _dc_type=$(blkid_value TYPE "$_dc_node"); _dc_trc=$?
    [ "$_dc_trc" -eq 0 ] || [ "$_dc_trc" -eq 2 ] || return 3
    if [ -n "$_dc_type" ]; then printf 'filesystem %s' "$_dc_type"
    elif [ "$_dc_scheme" != none ]; then printf '%s:0 partitions' "$_dc_scheme"
    else printf 'empty'; fi
}

print_inventory() {
    for _pi_name in $(list_disks); do
        _pi_base="$SYSROOT/sys/block/$_pi_name"
        IFS= read -r _pi_sectors < "$_pi_base/size" || fail inventory-size "cannot read size for $_pi_name"
        case "$_pi_sectors" in ''|*[!0-9]*) fail inventory-size "invalid size for $_pi_name" ;; esac
        _pi_size=$((_pi_sectors * 512))
        _pi_model=; [ ! -r "$_pi_base/device/model" ] || IFS= read -r _pi_model < "$_pi_base/device/model"
        _pi_model=$(trim "$(printf '%s' "$_pi_model" | tr '\r\n|' '   ')")
        IFS= read -r _pi_rm < "$_pi_base/removable" || fail inventory-removable "cannot read removable for $_pi_name"
        case "$_pi_rm" in 0|1) ;; *) fail inventory-removable "invalid removable flag for $_pi_name" ;; esac
        _pi_bus=$(disk_bus "$_pi_name") || fail inventory-bus "cannot identify bus for $_pi_name"
        _pi_has=$(disk_contents "$_pi_name"); _pi_hrc=$?
        [ "$_pi_hrc" -eq 0 ] || fail inventory-blkid "could not inspect contents of /dev/$_pi_name"
        _pi_iso=0; disk_is_iso "$_pi_name" && _pi_iso=1
        printf 'disk=/dev/%s|model=%s|size=%s|bus=%s|removable=%s|has=%s|iso=%s\n' \
            "$_pi_name" "$_pi_model" "$_pi_size" "$_pi_bus" "$_pi_rm" "$_pi_has" "$_pi_iso"
    done
}

partition_disk() {
    _pd_name=$1; _pd_disk="$DEVROOT/$_pd_name"
    _pd_esp=$(partition_node "$_pd_name" 1); _pd_root=$(partition_node "$_pd_name" 2)
    must_run partition-zap "could not erase the old partition table" sgdisk --zap-all "$_pd_disk"
    # Partition 3 (bios_grub, EF02) is created before 2 so it sits between
    # the ESP and root: grub-install --target=i386-pc embeds core.img there,
    # and without it refuses ("will not proceed with blocklists", ESXi 21/09).
    must_run partition-create "could not create the GPT partitions" sgdisk \
        --new=1:1MiB:+512MiB --typecode=1:EF00 --change-name=1:EFI \
        --new=3:0:+1MiB --typecode=3:EF02 --change-name=3:bios_grub \
        --new=2:0:0 --typecode=2:8300 --change-name=2:root "$_pd_disk"
    must_run partition-reread "kernel did not accept the new partition table" blockdev --rereadpt "$_pd_disk"
    if [ "$DRY_RUN" -eq 0 ]; then
        _pd_wait=0
        while { ! partition_node_ready "$_pd_esp" || ! partition_node_ready "$_pd_root"; } && [ "$_pd_wait" -lt 5 ]; do
            sleep 1; _pd_wait=$((_pd_wait + 1))
        done
        partition_node_ready "$_pd_esp" && partition_node_ready "$_pd_root" || fail partition-nodes "partition nodes did not appear within 5 seconds"
    fi
    must_run format-esp "mkfs.vfat failed on $_pd_esp" mkfs.vfat -F 32 -n EFI "$_pd_esp"
    must_run format-root "mkfs.ext4 failed on $_pd_root" mkfs.ext4 -F -L root "$_pd_root"
    if [ "$DRY_RUN" -eq 0 ]; then
        _pd_type=$(blkid_value TYPE "$_pd_esp"); _pd_rc=$?
        [ "$_pd_rc" -eq 0 ] || fail format-probe "blkid could not verify $_pd_esp"
        [ "$_pd_type" = vfat ] || fail format-verify "$_pd_esp is not vfat after formatting"
        _pd_type=$(blkid_value TYPE "$_pd_root"); _pd_rc=$?
        [ "$_pd_rc" -eq 0 ] || fail format-probe "blkid could not verify $_pd_root"
        [ "$_pd_type" = ext4 ] || fail format-verify "$_pd_root is not ext4 after formatting"
    fi
    ESP_NODE=$_pd_esp; ROOT_NODE=$_pd_root; export ESP_NODE ROOT_NODE
}
