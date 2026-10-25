# raid.sh -- software RAID (mdadm) for the live installer (#1989). POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module shares engine and answer state
#
# Nadav, 22/10: the installer counts the suitable disks and offers a default
# -- 2 -> RAID1, 3 -> RAID5, 4 or more -> RAID10 -- and the person may pick
# another (RAID1 with a spare, RAID6 from 4) or no RAID. A virtual server is
# not offered RAID: the host (ESXi/Hyper-V) does it.
#
# The live installer is our own engine (sgdisk + debootstrap, #1190), not
# debian-installer, so partman-auto-raid has nothing to run in: the array is
# made here with mdadm, every step through must_run, so --dry-run prints the
# exact commands and touches no disk.
#
# Sourced by the engine AND by gui-bridge.sh (inventory). Nothing runs at
# source time; virt_kind, raid_default and raid_eligible_count need no engine
# state.

RAID_ARRAY=md0

# virtual | metal | unknown -- positive evidence only (principle 5). The CPU's
# "hypervisor" flag or a hypervisor's name in DMI is virtual; metal needs BOTH
# sources read and clean; anything unread is unknown, never metal.
virt_kind() {
    _vk_cpu=unknown; _vk_dmi=unknown; _vk_text=
    if [ -r "${SYSROOT:-}/proc/cpuinfo" ]; then
        # awk: 0 = the flag is there, 1 = flags read and it is not, 2 = unread.
        awk '$1 == "flags" { for (i = 2; i <= NF; i++) if ($i == "hypervisor") f = 1 }
             END { exit f ? 0 : 1 }' "${SYSROOT:-}/proc/cpuinfo"
        case "$?" in 0) printf virtual; return 0 ;; 1) _vk_cpu=clean ;; esac
    fi
    for _vk_f in sys_vendor product_name board_vendor; do
        _vk_p="${SYSROOT:-}/sys/class/dmi/id/$_vk_f"
        [ -r "$_vk_p" ] || continue
        IFS= read -r _vk_line < "$_vk_p" || continue
        _vk_dmi=clean; _vk_text="$_vk_text|$_vk_line"
    done
    case "$_vk_text" in
        *VMware*|*QEMU*|*KVM*|*innotek*|*VirtualBox*|*Xen*|*Parallels*|*Bochs*|*"Virtual Machine"*)
            printf virtual; return 0 ;;
    esac
    if [ "$_vk_cpu" = clean ] && [ "$_vk_dmi" = clean ]; then printf metal; else printf unknown; fi
}

# The default level for N suitable disks. Not metal (virtual, or not known)
# -> none: the person may still choose on unknown, but nothing is assumed.
raid_default() {
    _rd_n=$1; _rd_virt=$2
    [ "$_rd_virt" = metal ] || { printf none; return 0; }
    if [ "$_rd_n" -ge 4 ]; then printf raid10
    elif [ "$_rd_n" -eq 3 ]; then printf raid5
    elif [ "$_rd_n" -eq 2 ]; then printf raid1
    else printf none; fi
}

# stdin: inventory lines (disk=/dev/x|...|bus=..|removable=0|...|iso=0).
# Suitable = not the install medium, not removable, not on USB.
raid_eligible_count() {
    _rc_n=0
    while IFS= read -r _rc_line; do
        case "$_rc_line" in disk=*) ;; *) continue ;; esac
        case "$_rc_line" in *'|iso=1'*|*'|removable=1|'*|*'|bus=usb|'*) continue ;; esac
        _rc_n=$((_rc_n + 1))
    done
    printf '%s' "$_rc_n"
}

# Minimum disks per level; raid1 with a spare needs one more.
raid_minimum() {
    case "$1" in raid1) printf '%s' $((2 + $2)) ;; raid5) printf 3 ;; raid6|raid10) printf 4 ;; *) printf 1 ;; esac
}

raid_member_check() {
    _rm_name=$1
    whole_disk_exists "$_rm_name" || fail invalid-raid "RAID disk does not exist or is not a whole disk: /dev/$_rm_name"
    disk_is_iso "$_rm_name" && fail iso-target "refusing the disk that contains /cdrom: /dev/$_rm_name"
    IFS= read -r _rm_rm < "$SYSROOT/sys/block/$_rm_name/removable" || fail invalid-raid "cannot read removable for /dev/$_rm_name"
    [ "$_rm_rm" = 0 ] || fail invalid-raid "a removable disk cannot be a RAID member: /dev/$_rm_name"
    _rm_bus=$(disk_bus "$_rm_name") || fail invalid-raid "cannot identify the bus of /dev/$_rm_name"
    [ "$_rm_bus" != usb ] || fail invalid-raid "a USB disk cannot be a RAID member: /dev/$_rm_name"
}

# Publishes RAID_LEVEL, RAID_SPARES, RAID_MEMBERS (names) and BOOT_DISKS
# (/dev paths, grub-install --target=i386-pc on each). No RAID = the one disk.
validate_raid() {
    RAID_LEVEL=${ANSWER_RAID_LEVEL:-none}; RAID_SPARES=${ANSWER_RAID_SPARES:-0}
    RAID_MEMBERS=; BOOT_DISKS=$ANSWER_DISK
    case "$RAID_SPARES" in 0|1) ;; *) fail invalid-raid "raid_spares must be 0 or 1" ;; esac
    if [ "$RAID_LEVEL" = none ]; then
        [ -z "${ANSWER_RAID_DISKS:-}" ] || fail invalid-raid "raid_disks given without a RAID level"
        [ "$RAID_SPARES" = 0 ] || fail invalid-raid "a spare needs a RAID level"
        export RAID_LEVEL RAID_SPARES RAID_MEMBERS BOOT_DISKS; return 0
    fi
    case "$RAID_LEVEL" in raid1|raid5|raid6|raid10) ;; *) fail invalid-raid "unknown RAID level: $RAID_LEVEL" ;; esac
    [ "$RAID_SPARES" = 0 ] || [ "$RAID_LEVEL" = raid1 ] || fail invalid-raid "a spare is offered only with RAID1"
    [ "$(virt_kind)" != virtual ] || fail raid-virtual "this server is a virtual machine: RAID is done by the host (ESXi/Hyper-V), not inside the VM"
    _vr_rest=${ANSWER_RAID_DISKS:-}; _vr_n=0; BOOT_DISKS=
    while [ -n "$_vr_rest" ]; do
        case "$_vr_rest" in *,*) _vr_one=${_vr_rest%%,*}; _vr_rest=${_vr_rest#*,} ;; *) _vr_one=$_vr_rest; _vr_rest= ;; esac
        case "$_vr_one" in /dev/*) _vr_name=${_vr_one#/dev/} ;; *) fail invalid-raid "RAID disk must be an absolute /dev path: $_vr_one" ;; esac
        case "$_vr_name" in ''|*/*) fail invalid-raid "RAID disk is not a whole-disk path: $_vr_one" ;; esac
        case " $RAID_MEMBERS " in *" $_vr_name "*) fail invalid-raid "RAID disk listed twice: $_vr_one" ;; esac
        [ "$_vr_n" -gt 0 ] || [ "$_vr_one" = "$ANSWER_DISK" ] || fail invalid-raid "the first RAID disk must be the chosen disk ($ANSWER_DISK)"
        raid_member_check "$_vr_name"
        RAID_MEMBERS="${RAID_MEMBERS:+$RAID_MEMBERS }$_vr_name"; BOOT_DISKS="${BOOT_DISKS:+$BOOT_DISKS }$_vr_one"
        _vr_n=$((_vr_n + 1))
    done
    _vr_min=$(raid_minimum "$RAID_LEVEL" "$RAID_SPARES")
    [ "$_vr_n" -ge "$_vr_min" ] || fail invalid-raid "$RAID_LEVEL needs at least $_vr_min disks, got $_vr_n"
    RAID_COUNT=$_vr_n
    export RAID_LEVEL RAID_SPARES RAID_MEMBERS BOOT_DISKS RAID_COUNT
}

wait_node() {
    [ "$DRY_RUN" -eq 0 ] || return 0
    _wn_wait=0
    while ! partition_node_ready "$1" && [ "$_wn_wait" -lt 5 ]; do sleep 1; _wn_wait=$((_wn_wait + 1)); done
    partition_node_ready "$1" || fail "$2" "$1 did not appear within 5 seconds"
}

verify_type() {
    [ "$DRY_RUN" -eq 0 ] || return 0
    _vt_type=$(blkid_value TYPE "$1"); _vt_rc=$?
    [ "$_vt_rc" -eq 0 ] || fail format-probe "blkid could not verify $1"
    [ "$_vt_type" = "$2" ] || fail format-verify "$1 is not $2 after formatting"
}

# Every member: ESP (EF00) + bios_grub (EF02) + the array's share (FD00).
# One array over every share; ext4 on it; each step verified by blkid.
partition_raid() {
    RAID_PARTS=; ESP_NODES=
    for _pr_name in $RAID_MEMBERS; do
        _pr_disk="$DEVROOT/$_pr_name"
        _pr_esp=$(partition_node "$_pr_name" 1); _pr_part=$(partition_node "$_pr_name" 2)
        must_run partition-zap "could not erase the old partition table on $_pr_disk" sgdisk --zap-all "$_pr_disk"
        must_run partition-create "could not create the GPT partitions on $_pr_disk" sgdisk \
            --new=1:1MiB:+512MiB --typecode=1:EF00 --change-name=1:EFI \
            --new=3:0:+1MiB --typecode=3:EF02 --change-name=3:bios_grub \
            --new=2:0:0 --typecode=2:FD00 --change-name=2:raid "$_pr_disk"
        must_run partition-reread "kernel did not accept the new partition table on $_pr_disk" blockdev --rereadpt "$_pr_disk"
        wait_node "$_pr_esp" partition-nodes; wait_node "$_pr_part" partition-nodes
        must_run format-esp "mkfs.vfat failed on $_pr_esp" mkfs.vfat -F 32 -n EFI "$_pr_esp"
        verify_type "$_pr_esp" vfat
        RAID_PARTS="${RAID_PARTS:+$RAID_PARTS }$_pr_part"; ESP_NODES="${ESP_NODES:+$ESP_NODES }$_pr_esp"
    done
    case "$RAID_LEVEL" in raid1) _pr_mod=raid1; _pr_lvl=1 ;; raid5) _pr_mod=raid456; _pr_lvl=5 ;;
        raid6) _pr_mod=raid456; _pr_lvl=6 ;; raid10) _pr_mod=raid10; _pr_lvl=10 ;; esac
    must_run raid-module "the kernel could not load $_pr_mod" modprobe "$_pr_mod"
    _pr_md="$DEVROOT/$RAID_ARRAY"; _pr_active=$((RAID_COUNT - RAID_SPARES))
    # --run: no "continue creating array?" question over old superblocks.
    # --homehost: the installed server's name, so it assembles as its own.
    # The partition list is word-split on purpose (validated /dev names).
    # shellcheck disable=SC2086
    if [ "$RAID_SPARES" -eq 0 ]; then
        must_run raid-create "mdadm could not create the $RAID_LEVEL array" mdadm --create "$_pr_md" --run \
            --metadata=1.2 --homehost="$ANSWER_HOSTNAME" --level="$_pr_lvl" --raid-devices="$_pr_active" $RAID_PARTS
    else
        must_run raid-create "mdadm could not create the $RAID_LEVEL array" mdadm --create "$_pr_md" --run \
            --metadata=1.2 --homehost="$ANSWER_HOSTNAME" --level="$_pr_lvl" --raid-devices="$_pr_active" \
            --spare-devices="$RAID_SPARES" $RAID_PARTS
    fi
    wait_node "$_pr_md" raid-node
    must_run format-root "mkfs.ext4 failed on $_pr_md" mkfs.ext4 -F -L root "$_pr_md"
    verify_type "$_pr_md" ext4
    ESP_NODE=${ESP_NODES%% *}; ROOT_NODE=$_pr_md
    export ESP_NODE ROOT_NODE ESP_NODES RAID_PARTS
}

# In the target, after its packages (mdadm among them): the array's own
# line in mdadm.conf, an initramfs that assembles it, and the fact the
# server's health page reads (/etc/imagectl/raid, world-readable).
raid_target_config() {
    [ "${RAID_LEVEL:-none}" != none ] || return 0
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command mdadm --detail --scan; _tc_scan="ARRAY <scan>"
    else
        _tc_scan=$(mdadm --detail --scan) || fail raid-scan "mdadm --detail --scan failed"
        case "$_tc_scan" in *"ARRAY "*) ;; *) fail raid-scan "mdadm --detail --scan listed no array" ;; esac
    fi
    write_target 0644 /etc/mdadm/mdadm.conf "# written by the ImageSrv installer (#1989)
HOMEHOST <system>
MAILADDR root
$_tc_scan
"
    must_run raid-initramfs "update-initramfs failed after writing mdadm.conf" chroot "$TARGET" update-initramfs -u -k all
    write_target 0644 /etc/imagectl/raid "level=$RAID_LEVEL
disks=$ANSWER_RAID_DISKS
spares=$RAID_SPARES
array=/dev/$RAID_ARRAY
"
}

# UEFI on every member: the first ESP is /boot/efi (finish.sh); the others
# get the same loader, without an NVRAM entry, so any one disk boots.
raid_extra_esps() {
    _re_i=0
    for _re_esp in $ESP_NODES; do
        _re_i=$((_re_i + 1)); [ "$_re_i" -gt 1 ] || continue
        _re_mnt="/boot/efi$_re_i"
        must_run esp-dir "could not create $_re_mnt" mkdir -p "$TARGET$_re_mnt"
        must_run mount-esp "could not mount $_re_esp" mount "$_re_esp" "$TARGET$_re_mnt"
        must_run grub-uefi "UEFI grub-install failed on $_re_esp" chroot "$TARGET" grub-install \
            --target=x86_64-efi --efi-directory="$_re_mnt" --bootloader-id=debian --force-extra-removable --no-nvram
        must_run unmount-esp "could not unmount $_re_esp" umount "$TARGET$_re_mnt"
    done
}
