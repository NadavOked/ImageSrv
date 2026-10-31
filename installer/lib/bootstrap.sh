# bootstrap.sh -- Debian bootstrap and offline package payload. POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module shares engine and answer state

TARGET="${TARGET:-/target}"
CDROM="${CDROM:-/cdrom}"
BOOTSTRAP_LOG="${BOOTSTRAP_LOG:-/run/imagesrv/debootstrap.log}"

last_bootstrap_line() {
    [ -s "$BOOTSTRAP_LOG" ] || return 0
    tail -n 1 "$BOOTSTRAP_LOG" | tr '\r\n' '  '
}

run_debootstrap() {
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command debootstrap --arch amd64 --no-check-gpg trixie "$TARGET" "file://$CDROM"
        return
    fi
    mkdir -p "${BOOTSTRAP_LOG%/*}" || fail bootstrap-log "cannot create bootstrap log directory"
    # debootstrap is #!/bin/sh; in the live image that is busybox ash, whose
    # dpkg-deb applet has no --fsys-tarfile ("tar failed" on the first
    # package, ESXi 21/09). Run it under dash, which finds the real dpkg-deb.
    _bd_sh=$(command -v dash 2>/dev/null) || _bd_sh=
    _bd_bin=$(command -v debootstrap 2>/dev/null) || fail bootstrap-missing "debootstrap is not on this image"
    # The exit code comes back through a file, not a bare `wait`: the wait
    # has a ceiling -- a log that has not grown for BOOTSTRAP_STALL_S is a
    # hung debootstrap, killed and named, not a frozen progress screen.
    _bd_rcfile="${BOOTSTRAP_LOG}.rc"; rm -f "$_bd_rcfile"
    { ${_bd_sh:+"$_bd_sh"} "$_bd_bin" --arch amd64 --no-check-gpg trixie "$TARGET" "file://$CDROM" > "$BOOTSTRAP_LOG" 2>&1; echo "$?" > "$_bd_rcfile"; } &
    _bd_pid=$!
    _bd_size=0; _bd_still=0
    while kill -0 "$_bd_pid" 2>/dev/null; do
        _bd_last=$(last_bootstrap_line)
        progress bootstrap 25 "Installing Debian base" "$_bd_last"
        _bd_now=$(wc -c < "$BOOTSTRAP_LOG" 2>/dev/null || echo 0)
        if [ "$_bd_now" = "$_bd_size" ]; then _bd_still=$((_bd_still + 1)); else _bd_size=$_bd_now; _bd_still=0; fi
        if [ "$_bd_still" -ge "${BOOTSTRAP_STALL_S:-900}" ]; then
            kill -9 "$_bd_pid" 2>/dev/null
            PROGRESS_LOG=$_bd_last; fail bootstrap-stalled "debootstrap wrote nothing for ${BOOTSTRAP_STALL_S:-900}s and was stopped"
        fi
        sleep 1
    done
    _bd_rc=$(cat "$_bd_rcfile" 2>/dev/null) || _bd_rc=
    _bd_last=$(last_bootstrap_line)
    [ -n "$_bd_rc" ] || fail bootstrap-failed "debootstrap ended without an exit code"
    [ "$_bd_rc" -eq 0 ] || { PROGRESS_LOG=$_bd_last; fail bootstrap-failed "debootstrap exited $_bd_rc"; }
    progress bootstrap 35 "Installing Debian base" "$_bd_last"
}

read_package_list() {
    _rp_file="$CDROM/imagesrv-src/tools/iso/packages.txt"
    [ -r "$_rp_file" ] || fail packages-list-missing "cannot read $_rp_file"
    PACKAGE_WORDS=
    while IFS= read -r _rp_line || [ -n "$_rp_line" ]; do
        _rp_line=${_rp_line%%#*}
        for _rp_pkg in $_rp_line; do
            case "$_rp_pkg" in *[!A-Za-z0-9+.-]*) fail packages-list-invalid "invalid package name: $_rp_pkg" ;; esac
            case " $PACKAGE_WORDS " in *" $_rp_pkg "*) ;; *) PACKAGE_WORDS="$PACKAGE_WORDS $_rp_pkg" ;; esac
        done
    done < "$_rp_file"
    [ -n "$PACKAGE_WORDS" ] || fail packages-list-empty "package list is empty"
}

mount_target_root() {
    must_run target-root-dir "could not create the target directory" mkdir -p "$TARGET"
    must_run mount-root "could not mount the target root" mount "$ROOT_NODE" "$TARGET"
    must_run target-esp-dir "could not create the EFI mount point" mkdir -p "$TARGET/boot/efi"
    must_run mount-esp "could not mount the EFI partition" mount "$ESP_NODE" "$TARGET/boot/efi"
}

bind_target_filesystems() {
    must_run target-dirs "could not create target bind points" mkdir -p \
        "$TARGET/dev" "$TARGET/proc" "$TARGET/sys" "$TARGET/cdrom"
    must_run mount-dev "could not bind /dev" mount --bind /dev "$TARGET/dev"
    must_run mount-proc "could not bind /proc" mount --bind /proc "$TARGET/proc"
    must_run mount-sys "could not bind /sys" mount --bind /sys "$TARGET/sys"
    must_run mount-cdrom "could not bind /cdrom" mount --bind "$CDROM" "$TARGET/cdrom"
}

# The local repository is the ISO's pool and dists/ **together** (#1371).
# dists/ indexes the merged pool -- netinst's own debs (dbus-user-session
# and the rest) next to pool/*/imagesrv -- so copying only
# pool/*/imagesrv published ~500 packages whose files were not there
# ("File not found" on the first install that needed one, 13/10). Success is
# positive evidence: every Filename in every binary index is a file here.
copy_local_repo() {
    _cr_repo="$TARGET/var/lib/imagesrv/apt-repo"
    must_run repo-dirs "could not create the local apt repository" mkdir -p "$_cr_repo"
    _cr_seen=0
    for _cr_comp in "$CDROM"/pool/*/imagesrv; do
        [ -d "$_cr_comp" ] && _cr_seen=$((_cr_seen + 1))
    done
    if [ "$DRY_RUN" -eq 0 ]; then [ "$_cr_seen" -gt 0 ] || fail apt-repo-missing "the ISO contains no imagesrv package pool"; fi
    must_run repo-copy "could not copy the package pool" cp -a "$CDROM/pool" "$_cr_repo/"
    must_run repo-index "could not copy apt indexes" cp -a "$CDROM/dists" "$_cr_repo/"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ verify-package-pool %s/pool %s\n' "$CDROM" "$_cr_repo"
        return
    fi
    _cr_dir=${BOOTSTRAP_LOG%/*}
    find "$CDROM/pool" -name '*.deb' -print > "$_cr_dir/packages.source" \
        || fail apt-repo-check "could not enumerate packages on the ISO"
    find "$_cr_repo/pool" -name '*.deb' -print > "$_cr_dir/packages.target" \
        || fail apt-repo-check "could not enumerate copied packages"
    _cr_src=$(wc -l < "$_cr_dir/packages.source") || fail apt-repo-check "could not count ISO packages"
    _cr_dst=$(wc -l < "$_cr_dir/packages.target") || fail apt-repo-check "could not count copied packages"
    case "$_cr_src:$_cr_dst" in *[!0-9:]*) fail apt-repo-check "invalid package counts" ;; esac
    [ "$_cr_src" -gt 0 ] && [ "$_cr_dst" -eq "$_cr_src" ] ||
        fail apt-repo-check "package pool copy failed ($_cr_dst of $_cr_src files)"
    : > "$_cr_dir/packages.listed" || fail apt-repo-check "could not create the index file list"
    for _cr_index in "$_cr_repo"/dists/*/*/binary-*/Packages.gz; do
        [ -f "$_cr_index" ] || continue
        gzip -dc "$_cr_index" > "$_cr_dir/packages.index" \
            || fail apt-repo-check "could not read ${_cr_index#"$_cr_repo/"}"
        sed -n 's/^Filename: *//p' "$_cr_dir/packages.index" >> "$_cr_dir/packages.listed" \
            || fail apt-repo-check "could not list files in ${_cr_index#"$_cr_repo/"}"
    done
    _cr_listed=0; _cr_missing=0; _cr_first=
    while IFS= read -r _cr_file; do
        _cr_listed=$((_cr_listed + 1))
        [ -f "$_cr_repo/$_cr_file" ] && continue
        _cr_missing=$((_cr_missing + 1)); [ -n "$_cr_first" ] || _cr_first=$_cr_file
    done < "$_cr_dir/packages.listed"
    [ "$_cr_listed" -gt 0 ] || fail apt-repo-check "the copied indexes list no packages"
    [ "$_cr_missing" -eq 0 ] ||
        fail apt-repo-check "$_cr_missing of $_cr_listed indexed packages are not in the local repository (first: $_cr_first)"
    rm -f "$_cr_dir/packages.source" "$_cr_dir/packages.target" "$_cr_dir/packages.index" "$_cr_dir/packages.listed" \
        || fail apt-repo-check "could not remove package count files"
}

write_installer_facts() {
    write_target 0644 /etc/imagesrv/installer-nic "state=configured
interface=$ANSWER_SERVERS_IF
mac=$ANSWER_SERVERS_MAC
reason=$ANSWER_SERVERS_MODE
d_i_choose_interface=$ANSWER_SERVERS_IF
source=/etc/imagesrv/answers
"
    write_target 0644 /etc/imagesrv/installer-role "role=$ANSWER_ROLE
primary=$ANSWER_PRIMARY_URL
primary_fingerprint=$ANSWER_PRIMARY_FINGERPRINT
"
    must_run iso-manifest "could not copy imagesrv-iso.json" cp "$CDROM/imagesrv-iso.json" "$TARGET/etc/imagesrv/iso-release.json"
}

install_target_packages() {
    read_package_list
    write_target 0644 /etc/apt/sources.list "deb [trusted=yes] file:/cdrom trixie main contrib non-free-firmware
"
    must_run apt-sources-clean "could not clear inherited apt sources" rm -f \
        "$TARGET/etc/apt/sources.list.d/"'*.list' "$TARGET/etc/apt/sources.list.d/"'*.sources'
    DEBIAN_FRONTEND=noninteractive; export DEBIAN_FRONTEND
    must_run apt-update "offline apt update failed" chroot "$TARGET" apt-get update
    # PACKAGE_WORDS contains only validated apt package names; splitting is intentional.
    # shellcheck disable=SC2086
    must_run apt-install "offline package installation failed" chroot "$TARGET" apt-get install -y --no-install-recommends \
        $PACKAGE_WORDS linux-image-amd64 grub-efi-amd64-signed shim-signed grub-pc-bin
    must_run source-copy "could not copy imagesrv-src" cp -a "$CDROM/imagesrv-src" "$TARGET/opt/imagesrv-src"
    # .git is what the update button works on (#1185). An ISO from the public
    # clone always carries it; a lab ISO (build-iso --source) says so in its
    # manifest (source_git=false) and is allowed through, named on screen.
    if [ "$DRY_RUN" -eq 0 ] && [ ! -e "$TARGET/opt/imagesrv-src/.git" ]; then
        _src_git=$(jq -r 'if .source_git == false then "no" else "yes" end' "$CDROM/imagesrv-iso.json" 2>/dev/null || echo yes)
        [ "$_src_git" = no ] || fail source-git-missing "imagesrv-src was copied without .git"
        progress packages 62 "Installing packages" "lab ISO (--source): imagesrv-src has no .git, the update button will not work on this server"
    fi
    must_run firstboot-copy "could not install firstboot" cp "$CDROM/imagesrv/firstboot.sh" "$TARGET/usr/local/sbin/imagesrv-firstboot"
    must_run firstboot-mode "could not mark firstboot executable" chmod 0755 "$TARGET/usr/local/sbin/imagesrv-firstboot"
    must_run firstboot-service "could not install firstboot service" cp "$CDROM/imagesrv/imagesrv-firstboot.service" "$TARGET/etc/systemd/system/imagesrv-firstboot.service"
    must_run firstboot-tty1 "could not reserve tty1 for firstboot progress" chroot "$TARGET" systemctl mask getty@tty1.service
    must_run firstboot-enable "could not enable firstboot service" chroot "$TARGET" systemctl enable imagesrv-firstboot.service
    copy_local_repo
    write_target 0644 /etc/apt/sources.list ""
    write_target 0644 /etc/apt/sources.list.d/imagesrv-iso.list \
        "deb [trusted=yes] file:/var/lib/imagesrv/apt-repo trixie main contrib
"
    write_installer_facts
}

# What is mounted under the target, deepest first, as the kernel lists it
# (#2093). A child sorts after its parent's path, so the reverse order
# unmounts every submount before the mount it sits on, and two mounts
# stacked on one point are two lines. No output = nothing mounted; a table
# that cannot be read is a failure (return 1), never an empty list.
target_mounts() {
    _tm_list=$(awk -v t="$TARGET" '$2 == t || index($2, t "/") == 1 { print $2 }' "$PROC_MOUNTS") || return 1
    [ -n "$_tm_list" ] || return 0
    printf '%s\n' "$_tm_list" | sort -r
}

# The release is decided by the kernel's mount table, not by flags kept for
# the mounts this engine made (#2093): an efivarfs the engine never made (the
# mdadm initramfs hook's, see finish.sh) sat under /target/sys, the flagged
# `umount /target/sys` was "busy", the leftover pass then unmounted
# everything -- and the flag still said "mounted", so a fully released target
# failed at 88%. The verdict is the caller's: target_mounts read again after
# this (principle 5).
cleanup_mounts() {
    [ "$DRY_RUN" -eq 0 ] || return 0
    _cm_list=$(target_mounts) || { printf 'installer: [unmount-unchecked] cannot read %s\n' "$PROC_MOUNTS" >&2; return 1; }
    for _cm_mp in $_cm_list; do
        if umount "$_cm_mp"; then printf 'installer: unmounted %s\n' "$_cm_mp" >&2
        else printf 'installer: [unmount-failed] %s\n' "$_cm_mp" >&2; fi
    done
}
