# finish.sh -- boot loader, host facts, and firstboot answers. POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module consumes bootstrap and answer state

root_password_hash() {
    # #1632 (Nadav 24/10): root's password is the one the operator chose on the
    # install screen (answers root_pass), hashed here -- nothing from the ISO,
    # no default. openssl reads it on stdin (printf is a builtin): it is never
    # in a process's argv or environment, and the plaintext never reaches the target.
    [ -n "${ANSWER_ROOT_PASS:-}" ] || fail root-password-missing "no root password from the install screen"
    ROOT_PASSWORD_HASH=$(printf '%s\n' "$ANSWER_ROOT_PASS" | openssl passwd -6 -stdin) \
        || fail root-password-hash "openssl passwd -6 failed"
    case "$ROOT_PASSWORD_HASH" in '$6$'*) ;; *) fail root-password-hash "openssl did not return a sha512-crypt hash" ;; esac
}

filesystem_uuid() {
    _fu_out=$(blkid -s UUID -o value "$1" 2>/dev/null); _fu_rc=$?
    [ "$_fu_rc" -eq 0 ] && [ -n "$_fu_out" ] || fail filesystem-uuid "cannot read UUID for $1"
    printf '%s' "$_fu_out"
}

# #2093: is an efivarfs mounted on the target's efivars? 0 yes, 1 no, 2 the
# mount table could not be read (not "no").
efivars_on_target() {
    _eo_list=$(target_mounts) || return 2
    case "
$_eo_list
" in *"
$TARGET/sys/firmware/efi/efivars
"*) return 0 ;; esac
    return 1
}

# #2093 (rc1, 24/10, measured in the mount table at each step): Debian's mdadm
# initramfs hook (Bug#962844) mounts efivarfs on an empty efivars directory --
# and our bind of /sys has an empty one -- when update-initramfs runs in the
# chroot during the package step, and leaves it there. The mount below then
# stacked on it, the umount after grub-install peeled only ours, and the
# cleanup found /target/sys busy. Every efivarfs already there goes first.
release_target_efivars() {
    _rt_n=0
    while :; do
        efivars_on_target; _rt_rc=$?
        [ "$_rt_rc" -ne 1 ] || return 0
        [ "$_rt_rc" -eq 0 ] || fail efivars-unchecked "cannot read $PROC_MOUNTS before mounting efivarfs"
        [ "$_rt_n" -lt 4 ] || fail efivars-unmount "efivarfs stays mounted on the target"
        umount "$TARGET/sys/firmware/efi/efivars" || fail efivars-unmount "could not unmount a leftover efivarfs from the target"
        progress bootloader 70 "Configuring UEFI and BIOS boot" "$1"
        _rt_n=$((_rt_n + 1))
    done
}

install_bootloader() {
    root_password_hash
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ printf root:<redacted> | chroot %s chpasswd -e\n' "$TARGET"
    else
        printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | chroot "$TARGET" chpasswd -e
        [ "$?" -eq 0 ] || fail root-password "chpasswd -e failed"
    fi
    # No `chage -d 0` (#1632): the operator chose this password on the screen.
    # The NVRAM entry is written through the target's efivarfs; a bind of
    # /sys does not carry that submount. Named, not fatal: EFI/BOOT
    # (--force-extra-removable) boots without an entry (ESXi, 21/09).
    _ib_efivars=0
    if [ "$DRY_RUN" -eq 0 ] && [ -d "${SYSROOT:-}/sys/firmware/efi/efivars" ]; then
        release_target_efivars "unmounted an efivarfs left on the target by update-initramfs (mdadm hook)"
        if mount -t efivarfs efivarfs "$TARGET/sys/firmware/efi/efivars" 2>/dev/null; then _ib_efivars=1
        else progress bootloader 70 "Configuring UEFI and BIOS boot" "efivarfs not mounted in the target: no NVRAM boot entry, EFI/BOOT fallback only"; fi
    fi
    # Debian's grub-install refuses --removable together with
    # --force-extra-removable; the latter alone writes EFI/debian and the
    # EFI/BOOT fallback (ESXi, 21/09).
    must_run grub-uefi "UEFI grub-install failed" chroot "$TARGET" grub-install \
        --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian \
        --force-extra-removable
    if [ "$_ib_efivars" -eq 1 ]; then
        umount "$TARGET/sys/firmware/efi/efivars" || fail efivars-unmount "could not unmount efivarfs from the target"
        # #2093: released = the table no longer lists it (not "umount said 0")
        efivars_on_target; _ib_rc=$?
        [ "$_ib_rc" -ne 2 ] || fail efivars-unchecked "cannot read $PROC_MOUNTS after unmounting efivarfs"
        [ "$_ib_rc" -eq 1 ] || fail efivars-unmount "efivarfs is still mounted on the target after umount"
    fi
    # #1989: every RAID member carries core.img, so any one of them boots.
    for _ib_disk in ${BOOT_DISKS:-$ANSWER_DISK}; do
        must_run grub-bios "BIOS grub-install failed on $_ib_disk" chroot "$TARGET" grub-install --target=i386-pc "$_ib_disk"
    done
    [ "${RAID_LEVEL:-none}" = none ] || raid_extra_esps
    # Boot straight through like ESXi, while leaving a short Esc/Shift window.
    # Written before update-grub reads it.
    write_target 0644 /etc/default/grub.d/imagesrv.cfg "GRUB_TIMEOUT_STYLE=hidden
GRUB_TIMEOUT=3
"
    # A deterministic recovery path that does not depend on Debian's optional
    # recovery submenu. grub-probe runs when update-grub generates grub.cfg, so
    # both BIOS and UEFI installations use the installed root filesystem UUID.
    write_target 0755 /etc/grub.d/41_imagesrv_recovery '#!/bin/sh
set -eu
root_uuid=$(grub-probe --target=fs_uuid /)
cat <<EOF
menuentry "ImageSrv recovery (text console)" {
    search --no-floppy --fs-uuid --set=root $root_uuid
    linux /vmlinuz root=UUID=$root_uuid ro nomodeset systemd.unit=multi-user.target
    initrd /initrd.img
}
EOF
'
    link_root_by_uuid
    must_run grub-config "update-grub failed" chroot "$TARGET" update-grub
    grub_boots_root_by_uuid
}

# #2137 (rc3, Hyper-V 24/10): grub.cfg booted root=/dev/sda2, and a second
# disk made the system disk sdb. Debian's 10_linux writes root=UUID= only
# when /dev/disk/by-uuid/<uuid> exists, else the device name -- and the live
# initramfs has no udev, so the target's /dev (a bind of ours) has no
# by-uuid at all. The link is the one udev would make, for the root as
# grub-probe sees it (the md array under RAID, #1989); on the installed
# system udev makes it for every later update-grub (a kernel upgrade).
link_root_by_uuid() {
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command chroot "$TARGET" grub-probe --target=fs_uuid /
        print_command chroot "$TARGET" grub-probe --target=device /
        print_command ln -sf '<root-device>' "$TARGET/dev/disk/by-uuid/<root-uuid>"
        return
    fi
    ROOT_FS_UUID=$(chroot "$TARGET" grub-probe --target=fs_uuid /) \
        || fail grub-root-uuid "grub-probe could not read the root filesystem UUID"
    case "$ROOT_FS_UUID" in ''|*[!0-9A-Fa-f-]*) fail grub-root-uuid "grub-probe gave no usable root UUID: '$ROOT_FS_UUID'" ;; esac
    _lr_dev=$(chroot "$TARGET" grub-probe --target=device /) \
        || fail grub-root-uuid "grub-probe could not name the root device"
    case "$_lr_dev" in /dev/*) ;; *) fail grub-root-uuid "grub-probe gave no root device: '$_lr_dev'" ;; esac
    mkdir -p "$TARGET/dev/disk/by-uuid" && ln -sf "$_lr_dev" "$TARGET/dev/disk/by-uuid/$ROOT_FS_UUID" \
        || fail grub-root-uuid "could not link /dev/disk/by-uuid/$ROOT_FS_UUID for update-grub"
}

# The verdict is grub.cfg read back (principle 5): every kernel line boots
# root=UUID=<this root>, and there is at least one. A file awk cannot read is
# "not checked", which fails too -- not "no lines".
grub_boots_root_by_uuid() {
    [ "$DRY_RUN" -eq 0 ] || return 0
    _gb_cfg="$TARGET/boot/grub/grub.cfg"
    [ -r "$_gb_cfg" ] || fail grub-root-unchecked "cannot read $_gb_cfg after update-grub"
    _gb_counts=$(awk -v want="root=UUID=$ROOT_FS_UUID" '
        $1 == "linux" { for (i = 2; i <= NF; i++) if ($i ~ /^root=/) { all++; if ($i == want) ok++ } }
        END { print all + 0, ok + 0 }' "$_gb_cfg") \
        || fail grub-root-unchecked "cannot read the kernel lines of $_gb_cfg"
    _gb_all=${_gb_counts% *}; _gb_uuid=${_gb_counts#* }
    [ "$_gb_all" -gt 0 ] || fail grub-root-not-uuid "update-grub wrote no kernel line with root= to $_gb_cfg"
    [ "$_gb_all" -eq "$_gb_uuid" ] \
        || fail grub-root-not-uuid "$((_gb_all - _gb_uuid)) of $_gb_all kernel lines in $_gb_cfg do not boot root=UUID=$ROOT_FS_UUID"
}

write_host_configuration() {
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command blkid -s UUID -o value "$ROOT_NODE"
        print_command blkid -s UUID -o value "$ESP_NODE"
        _wh_root_uuid='<root-uuid>'; _wh_esp_uuid='<esp-uuid>'
    else
        _wh_root_uuid=$(filesystem_uuid "$ROOT_NODE")
        _wh_esp_uuid=$(filesystem_uuid "$ESP_NODE")
    fi
    write_target 0644 /etc/fstab "UUID=$_wh_root_uuid / ext4 defaults 0 1
UUID=$_wh_esp_uuid /boot/efi vfat umask=0077 0 1
"
    write_target 0644 /etc/hostname "$ANSWER_HOSTNAME
"
    write_target 0644 /etc/hosts "127.0.0.1 localhost
127.0.1.1 $ANSWER_HOSTNAME
::1 localhost ip6-localhost ip6-loopback
"
    # #1447: Debian's default is Etc/UTC; the DCUI clock reads the OS zone.
    # The zone the person chose (default Asia/Jerusalem), before time sync;
    # firstboot hands it to the server's setting (setup-boot-server --timezone).
    must_run timezone-missing "$ANSWER_TIMEZONE is not in the target's zoneinfo" \
        chroot "$TARGET" test -f "/usr/share/zoneinfo/$ANSWER_TIMEZONE"
    must_run timezone "could not set the server timezone" \
        chroot "$TARGET" ln -sf "/usr/share/zoneinfo/$ANSWER_TIMEZONE" /etc/localtime
    write_target 0644 /etc/timezone "$ANSWER_TIMEZONE
"
    # Journal ordering, TLS validity, and session expiry all depend on a sane
    # clock. The package is in the offline ISO pool; enabling it here makes
    # synchronization start on the first installed-system boot.
    must_run time-sync "could not enable chrony" \
        chroot "$TARGET" systemctl enable chrony.service
    write_target 0600 /etc/imagesrv/answers "disk=$ANSWER_DISK
role=$ANSWER_ROLE
primary_url=$ANSWER_PRIMARY_URL
primary_fingerprint=$ANSWER_PRIMARY_FINGERPRINT
servers_if=$ANSWER_SERVERS_IF
servers_mac=$ANSWER_SERVERS_MAC
servers_mode=$ANSWER_SERVERS_MODE
servers_addr=$ANSWER_SERVERS_ADDR
servers_mask=$ANSWER_SERVERS_MASK
servers_gw=$ANSWER_SERVERS_GW
servers_dns=$ANSWER_SERVERS_DNS
hostname=$ANSWER_HOSTNAME
timezone=$ANSWER_TIMEZONE
admin_user=$ANSWER_ADMIN_USER
raid_level=${RAID_LEVEL:-none}
raid_disks=${ANSWER_RAID_DISKS:-}
raid_spares=${RAID_SPARES:-0}
"
    write_target_secret /etc/imagesrv/answers.secret "$ANSWER_ADMIN_PASS"
}
