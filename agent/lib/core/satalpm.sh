#!/bin/sh
# #1827: a cloner's SATA links stay at max_performance. Debian's default
# (med_power_with_dipm) lets an idle drawer -- one waiting for the wave, or for
# a 30-minute SMART question -- slumber, and some drives (PNY CS900, WD Gold)
# drop off the bus from it. Cloners only: never a station, never the server.
#
# The outcome is the value read back, never the write's exit code (principle
# 5). It is kept in $RUN_DIR/sata_lpm -- for the hello (sata_lpm_json) and for
# a pulled drawer's sentence (verdict.sh):
#   max_performance        every host with the knob read it back
#   none                   no host has the knob (no libata/AHCI host)
#   host<N>=<value> ...    the hosts that did not; "unreadable" = no read back
# POSIX sh (busybox ash).

sata_lpm_cloner() {
    # Once per boot, after the hello answer said "cloner" (D_ROLE).
    [ "${D_ROLE:-}" = cloner ] || return 0
    [ ! -e "$RUN_DIR/sata_lpm" ] || return 0
    _sl_n=0; _sl_bad=""
    for _sl_f in "${SYSROOT:-}"/sys/class/scsi_host/host*/link_power_management_policy; do
        [ -e "$_sl_f" ] || continue
        _sl_n=$((_sl_n + 1))
        # The write's error is not the evidence -- the read below is: a host
        # without LPM refuses the write and already reads max_performance.
        { printf '%s\n' max_performance > "$_sl_f"; } 2>/dev/null
        _sl_v=$(cat "$_sl_f" 2>/dev/null) || _sl_v=""
        [ -n "$_sl_v" ] || _sl_v=unreadable
        _sl_h=${_sl_f%/*}; _sl_h=${_sl_h##*/}
        [ "$_sl_v" = max_performance ] || _sl_bad="$_sl_bad${_sl_bad:+ }$_sl_h=$_sl_v"
    done
    if [ "$_sl_n" = 0 ]; then _sl_r=none
    elif [ -z "$_sl_bad" ]; then _sl_r=max_performance
    else _sl_r=$_sl_bad
    fi
    printf '%s\n' "$_sl_r" > "$RUN_DIR/sata_lpm" \
        || log "WARNING: sata lpm: could not record the result ($_sl_r)"
    log "sata lpm: $_sl_r ($_sl_n hosts)"
}

sata_lpm_json() {
    # The hello fragment (leading comma); absent until sata_lpm_cloner ran.
    _sl_r=$(cat "$RUN_DIR/sata_lpm" 2>/dev/null) || return 0
    printf ',"sata_lpm":"%s"' "$(json_escape "$_sl_r")"
}
