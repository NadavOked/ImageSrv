# installer_boot.sh -- init's handover to the live installer (#1190). POSIX sh.
# Sourced by /init only when the kernel line says imagesrv.mode=installer,
# i.e. the machine booted the ImageSrv install ISO. Three jobs, then exec:
#   1. temporary DHCP on every NIC with carrier -- for the network screen's
#      "current address" and the primary check; a NIC without an offer is a
#      named outcome, never a reason to stop (the install itself is offline);
#   2. mount the install media at /cdrom by its volume label;
#   3. hand the console to /usr/lib/imagesrv/installer/imagesrv-installer.
# Nothing here reboots: a reboot-loop on a machine with no display or no
# media is exactly what a person in front of the screen cannot diagnose.

INSTALL_LABEL="${INSTALL_LABEL:-IMAGESRV_INSTALL}"
DHCP_DIR="${DHCP_DIR:-/run/imagesrv/dhcp}"
CDROM="${CDROM:-/cdrom}"
INSTALLER_BIN="${INSTALLER_BIN:-/usr/lib/imagesrv/installer/imagesrv-installer}"

installer_note() { printf 'imagesrv installer: %s\n' "$*"; }

# One NIC: up, wait for carrier (3 s), one-shot udhcpc (12 s), record the result.
installer_dhcp_one() {
    _nic=$1; _out="$DHCP_DIR/$_nic"
    ip link set "$_nic" up 2>/dev/null || { printf 'state=link-failed\n' > "$_out"; return 0; }
    _c=0; _n=0
    while [ "$_n" -lt 3 ]; do
        _c=$(cat "/sys/class/net/$_nic/carrier" 2>/dev/null || echo 0)
        [ "$_c" = 1 ] && break
        sleep 1; _n=$((_n + 1))
    done
    if [ "$_c" != 1 ]; then printf 'state=no-carrier\n' > "$_out"; installer_note "nic $_nic: no carrier"; return 0; fi
    if ! udhcpc -i "$_nic" -n -q -t 4 -T 3 -s /etc/imagesrv/udhcpc.script >/dev/null 2>&1; then
        printf 'state=no-offer\n' > "$_out"; installer_note "nic $_nic: no dhcp offer"; return 0
    fi
    _addr=$(ip -o -4 addr show dev "$_nic" scope global 2>/dev/null | sed -n 's/.* inet \([0-9./]*\).*/\1/p' | head -n 1)
    _gw=$(ip -4 route show default dev "$_nic" 2>/dev/null | sed -n 's/^default via \([0-9.]*\).*/\1/p' | head -n 1)
    _dns=$(sed -n 's/^nameserver \([0-9.]*\).*/\1/p' /etc/resolv.conf 2>/dev/null | paste -sd, -)
    case "$_addr" in
        169.254.*|"") printf 'state=no-offer\n' > "$_out"; installer_note "nic $_nic: no dhcp offer"; return 0 ;;
    esac
    printf 'state=lease\naddress=%s\ngateway=%s\ndns=%s\n' "$_addr" "$_gw" "$_dns" > "$_out"
    installer_note "nic $_nic: $_addr (dhcp)"
}

installer_dhcp_all() {
    mkdir -p "$DHCP_DIR" || return 1
    _pids=
    for _d in /sys/class/net/*; do
        _nic=$(basename "$_d")
        [ "$_nic" = lo ] && continue
        installer_dhcp_one "$_nic" &
        _pids="$_pids $!"
    done
    # Each child is bounded by udhcpc's own -t/-T (about 15 s with the
    # carrier wait); the ceiling here is the safety net, not the clock.
    _n=0
    while [ "$_n" -lt 40 ]; do
        _live=0
        for _p in $_pids; do kill -0 "$_p" 2>/dev/null && _live=1; done
        [ "$_live" -eq 0 ] && break
        sleep 1; _n=$((_n + 1))
    done
    for _p in $_pids; do kill -0 "$_p" 2>/dev/null && { kill "$_p" 2>/dev/null; installer_note "dhcp probe pid $_p stopped after 40s"; }; done
    return 0
}

# The media: the volume label build-iso sets. blkid -L is the positive
# evidence; a device found by scanning /dev/sr* is the fallback, and both
# are logged by name (principle 5: "mounted /dev/sr0" beats "mounted").
installer_mount_media() {
    mkdir -p "$CDROM" || return 1
    # The CD/ISO modules are in modules.required for the installer image; init
    # already loaded them. Load again by name here so a missing one is named
    # next to the mount that needs it, not two screens earlier.
    # ata_piix: the IDE controller VMware/QEMU put a virtual CD on (the PXE
    # load list only has ahci -- disks); without it there is no /dev/sr0 at
    # all, and the label search says "no media" (ESXi, 21/09). ata_generic
    # is the fallback for odd chipsets. Both are in the packed tree.
    for _m in ata_piix ata_generic ahci sr_mod cdrom isofs usb-storage; do
        modprobe "$_m" 2>/dev/null || installer_note "module $_m did not load (the media may be unreadable)"
    done
    # give the block layer a moment to enumerate the newly driven controller
    _n=0
    while [ "$_n" -lt 8 ]; do
        [ -b /dev/sr0 ] && break
        if blkid -L "$INSTALL_LABEL" >/dev/null 2>&1; then break; fi
        sleep 1; _n=$((_n + 1))
    done
    _dev=$(blkid -L "$INSTALL_LABEL" 2>/dev/null || true)
    if [ -z "$_dev" ]; then
        for _cand in /dev/sr0 /dev/sr1; do
            [ -b "$_cand" ] || continue
            if blkid "$_cand" 2>/dev/null | grep -q "LABEL=\"$INSTALL_LABEL\""; then _dev=$_cand; break; fi
        done
    fi
    [ -n "$_dev" ] || { installer_note "no media with label $INSTALL_LABEL"; return 1; }
    mount -o ro "$_dev" "$CDROM" 2>/dev/null || { installer_note "mount $_dev on $CDROM failed"; return 1; }
    [ -x "$CDROM/imagesrv-src/tools/iso/build-iso.sh" ] || [ -f "$CDROM/imagesrv-iso.json" ] \
        || { installer_note "$_dev is not an ImageSrv install medium (no imagesrv-iso.json)"; return 1; }
    printf '%s\n' "$_dev" > /run/imagesrv/install-media
    installer_note "media $_dev mounted on $CDROM"
}

installer_boot() {
    installer_note "install mode (kernel line), starting"
    ip link set lo up 2>/dev/null
    installer_dhcp_all || installer_note "temporary DHCP step failed to start"
    # Site/lab overlay hooks (#1266), with the same contract as in the agent:
    # $IP is the first lease. The product ships none; named if it is missing.
    if [ -f "$LIB_DIR/sitehooks.sh" ]; then
        . "$LIB_DIR/sitehooks.sh"
        IP=$(cat "$DHCP_DIR"/* 2>/dev/null | sed -n 's/^address=\([0-9.]*\).*/\1/p' | head -n 1); export IP
        run_site_hooks
    else
        installer_note "image without sitehooks.sh -- overlay hooks not run"
    fi
    installer_mount_media || installer_note "continuing without media: the installer will say so on screen"
    [ -x "$INSTALLER_BIN" ] || fail "installer image without $INSTALLER_BIN"
    exec "$INSTALLER_BIN"
}
