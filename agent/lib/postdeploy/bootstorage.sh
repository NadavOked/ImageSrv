# bootstorage.sh -- make the restored Windows load the in-box storage
# driver of the disk it was just written to at boot (#1331; interfaces.md
# section 17, step 5a). POSIX sh (busybox ash).
#
# Why: a storage miniport that is not boot-start (Services\<svc>\Start=0)
# is not loaded before the kernel looks for the system disk, and the result
# is 0x7B INACCESSIBLE_BOOT_DEVICE -- long before the DevicePath staging
# (postdeploy.sh) is ever read. Our Win11 25H2 images captured on SATA
# (img_8da804, img_b37bd6) do NOT have stornvme at Start=3: they have
# Start=0 with StartOverride\0=3, and on a VM that boots on NVMe as is.
# Start=3 alone does give 0x7B (#1331 T4, 18/10: E1 vs E1b). So Start is
# measured to decide; StartOverride\0 is zeroed too because R24 says a
# RAID-mode install keeps storahci at 3 with it (not measured here) and
# zeroing it was harmless in the same test (E2). Both drivers ship in Windows,
# so the fix is two DWORDs in the SYSTEM hive -- what Safe Mode does by
# hand (docs/research/R24-offline-boot-drivers.md has the sources).
#
# The service is chosen by the disk we wrote, not by this machine's
# inventory: a cloner's drawer goes to another machine (#1269), but the
# disk's bus goes with it. nvme* -> stornvme; sd* -> storahci (a SATA
# disk, also in a USB dock -- the target runs in AHCI mode, which
# server-install.md already requires). Anything else is skipped by name.
#
# This is case A of R24 only. A controller without an in-box driver
# (Intel VMD/RST, PVSCSI) is NOT covered: that needs DriverDatabase entries
# and is not built until a VM experiment proves it (#1331).
#
# Called from _stage_drivers with the Windows partition already mounted;
# sets $_dr_boot to the JSON object that rides in drivers.json as "boot".
# Never fatal, never silent (עיקרון 5): enabled / already / skipped / failed.

boot_storage_service() {
    # $1 = disk name. The in-box miniport for its bus, or nothing (rc 1).
    case "$1" in
        nvme*) echo stornvme ;;
        sd*)   echo storahci ;;
        *)     return 1 ;;
    esac
}

_boot_result() {
    # $1 = state, $2 = service ("" = none), $3 = error/reason (optional).
    _br="{\"state\":\"$1\""
    [ -n "$2" ] && _br="$_br,\"service\":\"$2\""
    if [ -n "${3:-}" ]; then
        _bk="error"; [ "$1" = "skipped" ] && _bk="reason"
        _br="$_br,\"$_bk\":\"$(json_escape "$3")\""
    fi
    _dr_boot="$_br}"
    log "boot storage${DR_PORT:+ (drawer $DR_PORT)}: $_dr_boot"
}

_boot_dword() {
    # $1 = hive, $2 = key, $3 = name. Echoes the value; rc 0 read, 3 absent
    # (hivewrite's "not found"), anything else = the read itself broke.
    hivewrite -g "$1" "$2" "$3" 2>> "$LOG_FILE"
}

_boot_set_zero() {
    # $1 = hive, $2 = key, $3 = name. Writes 0 and reads it back; the read,
    # not hivewrite's exit code, is the evidence. Returns 1 with $_bs_why.
    hivewrite -d "$1" "$2" "$3" 0 >> "$LOG_FILE" 2>&1
    _bw=$?
    _bb=$(_boot_dword "$1" "$2" "$3")
    if [ "$_bw" -ne 0 ] || [ "$_bb" != "0" ]; then
        _bs_why="$2\\$3=0 לא אומת (rc=$_bw, נקרא בחזרה '$_bb')"; return 1
    fi
    return 0
}

boot_storage_enable() {
    # $1 = disk name, $2 = the mounted Windows root. Always returns 0.
    _dr_boot=""; _bs_why=""
    _bs_svc=$(boot_storage_service "$1") || { _boot_result skipped "" "unknown_bus"; return 0; }
    _bs_hive="$2/Windows/System32/config/SYSTEM"
    [ -f "$_bs_hive" ] || { _boot_result failed "$_bs_svc" "כוורת SYSTEM לא נמצאה"; return 0; }
    _bs_cs=$(_control_set "$_bs_hive" 2>> "$LOG_FILE") || {
        _boot_result failed "$_bs_svc" "לא הצלחנו לקרוא את Select\\Current"; return 0; }
    _bs_key="$_bs_cs\\Services\\$_bs_svc"

    _bs_start=$(_boot_dword "$_bs_hive" "$_bs_key" Start); _bs_rc=$?
    # A Windows without the service (stornvme on Windows 7) cannot boot from
    # this disk at all -- that is a finding, not a skip.
    [ "$_bs_rc" -eq 3 ] && { _boot_result failed "$_bs_svc" "באימג' אין את השירות $_bs_svc"; return 0; }
    [ "$_bs_rc" -eq 0 ] || { _boot_result failed "$_bs_svc" "לא הצלחנו לקרוא את $_bs_key\\Start (rc=$_bs_rc)"; return 0; }
    _bs_over=$(_boot_dword "$_bs_hive" "$_bs_key\\StartOverride" 0); _bs_orc=$?
    case "$_bs_orc" in
        0) ;;
        3) _bs_over="" ;;   # no StartOverride key or value: Start alone decides
        *) _boot_result failed "$_bs_svc" "לא הצלחנו לקרוא את StartOverride (rc=$_bs_orc)"; return 0 ;;
    esac

    if [ "$_bs_start" = "0" ] && { [ -z "$_bs_over" ] || [ "$_bs_over" = "0" ]; }; then
        _boot_result already "$_bs_svc"; return 0
    fi
    if [ "$_bs_start" != "0" ]; then
        _boot_set_zero "$_bs_hive" "$_bs_key" Start || { _boot_result failed "$_bs_svc" "$_bs_why"; return 0; }
    fi
    if [ -n "$_bs_over" ] && [ "$_bs_over" != "0" ]; then
        _boot_set_zero "$_bs_hive" "$_bs_key\\StartOverride" 0 || {
            _boot_result failed "$_bs_svc" "$_bs_why"; return 0; }
    fi
    _boot_result enabled "$_bs_svc"
    return 0
}
