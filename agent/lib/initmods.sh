# initmods.sh -- how agent/init loads kernel modules (#1702). POSIX sh, run as
# PID 1 before any network, so it needs nothing but busybox.
#
# The load used to be one `modprobe` per name over /etc/imagectl/modules: about
# 300 NIC drivers (and amdgpu/nouveau on the GUI image) that this machine has
# no hardware for, 9.2 s on the cloner (59 boots, 17/10), and only then did the
# link start negotiating (3.5 s more). Now:
#   1. PHY drivers by name (a MAC probing before its PHY fails and is never
#      retried, #76), the paravirtual NIC the DMI/CPUID says we run on, and the
#      PCI network devices that are actually present, by modalias;
#   2. `ip link set up` -- autonegotiation runs while step 3 loads;
#   3. the mass-storage controllers present, by modalias (#1914), then
#      everything else, in the old order (USB, storage, filesystems, crypto);
#   4. no physical NIC after (1)? Load every packaged NIC driver, as before.
#      A display device with no framebuffer after (3) gets every GPU driver.
# An initramfs without modules.nic (old build) loads the whole list in order.
#
# Needs from init: MOD_DIR, _required, _req_fail, _opt_fail, poll_tick,
# platform_module_expected. SYS_DIR is only overridden by tests.

SYS_DIR="${SYS_DIR:-/sys}"

# One module by name. Required failures and expected-platform failures are kept
# by name for init's report (#407); any other failure is hardware we lack.
mods_load() {
    modprobe "$1" 2>/dev/null && return 0
    case "$_required" in
        *" $1 "*) if mods_alt_ok "$1"; then _opt_fail="$_opt_fail$1 "; else _req_fail="$_req_fail$1 "; fi ;;
        *) platform_module_expected "$1" && _opt_fail="$_opt_fail$1 " ;;
    esac
    return 0
}

# #1937 (the college, 21/10): crc32c-intel needs SSE4.2 -- no Core2, no Phenom II -- and its
# failure said "ERROR ... restore may corrupt" on each of them. What is required is crc32c,
# not this driver of it: 0 only on positive evidence that the kernel has a crc32c
# (crc32c_generic, loaded before it). /proc/crypto unread = not excused (principle 5).
mods_alt_ok() {
    [ "$1" = crc32c-intel ] && grep -q '^name[[:space:]]*:[[:space:]]*crc32c$' "${PROC_DIR:-/proc}/crypto" 2>/dev/null
}

# $1 = list file, $2 = " name name " to leave out.
mods_load_file() {
    [ -f "$1" ] || return 0
    while read -r _m; do
        [ -n "$_m" ] || continue
        case "$2" in *" $_m "*) continue ;; esac
        mods_load "$_m"
    done < "$1"
}

mods_names() { printf ' %s ' "$(tr '\n' ' ' < "$1" 2>/dev/null)"; }

# $1 = PCI base class (02 network, 03 display), $2 = "report" to name a device
# no packaged driver claims. Only reads sysfs; `read` forks nothing.
mods_load_pci() {
    _seen=" "
    for _d in "$SYS_DIR"/bus/pci/devices/*; do
        [ -r "$_d/class" ] && [ -r "$_d/modalias" ] || continue
        read -r _c < "$_d/class"
        case "$_c" in 0x$1*) ;; *) continue ;; esac
        read -r _al < "$_d/modalias"
        case "$_seen" in *" $_al "*) continue ;; esac
        _seen="$_seen$_al "
        modprobe "$_al" 2>/dev/null || [ "$2" != report ] \
            || echo "ImageSrv: no driver module for network device $_al"
    done
}

mods_pci_class_present() {
    for _d in "$SYS_DIR"/bus/pci/devices/*; do
        [ -r "$_d/class" ] || continue
        read -r _c < "$_d/class"
        case "$_c" in 0x$1*) return 0 ;; esac
    done
    return 1
}

# A physical NIC has a `device` link; lo, dummy and tunnels do not.
mods_have_nic() {
    for _d in "$SYS_DIR"/class/net/*; do
        [ -e "$_d/device" ] && return 0
    done
    return 1
}

mods_links_up() {
    for _d in "$SYS_DIR"/class/net/*; do
        [ -e "$_d" ] || continue
        _n=${_d##*/}
        [ "$_n" = lo ] || ip link set "$_n" up 2>/dev/null
    done
}

mods_nic_phase() {
    mods_load_file "$MOD_DIR/modules.phy" ""
    for _m in $_nic; do
        platform_module_expected "$_m" && mods_load "$_m"
    done
    mods_load_pci 02 report
    _t=0
    until mods_have_nic; do
        if [ "$_t" -ge 10 ]; then
            echo "ImageSrv: no network device from modalias -- loading every packaged NIC driver"
            mods_load_file "$MOD_DIR/modules.nic" ""
            break
        fi
        poll_tick; _t=$((_t + _tk))
    done
    mods_links_up
    _skip="$_phy$_nic"
}

mods_gpu_fallback() {
    if [ -e "$SYS_DIR/class/graphics/fb0" ] || [ -e "$SYS_DIR/class/drm/card0" ]; then
        return 0
    fi
    mods_pci_class_present 03 || return 0
    echo "ImageSrv: display device without a driver from modalias -- loading every GPU driver"
    mods_load_file "$MOD_DIR/modules.gpu" ""
}

mods_load_all() {
    _phy=$(mods_names "$MOD_DIR/modules.phy")
    _nic=$(mods_names "$MOD_DIR/modules.nic")
    _gpu=$(mods_names "$MOD_DIR/modules.gpu")
    _skip=""
    [ -s "$MOD_DIR/modules.nic" ] && mods_nic_phase
    if [ -s "$MOD_DIR/modules.gpu" ]; then
        mods_load_pci 03 ""
        _skip="$_skip$_gpu"
    fi
    # #1914 (an old HP in the college, 20/10: "no disks"): the mass-storage
    # controllers that are present, by modalias -- ata_piix for SATA in IDE
    # mode, pata_* on PATA, vmd for Intel VMD -- before the fixed list, which
    # names only ahci/nvme first and stays the fallback.
    mods_load_pci 01 ""
    mods_load_file "$MOD_DIR/modules" "$_skip"
    [ -s "$MOD_DIR/modules.gpu" ] && mods_gpu_fallback
    return 0
}
