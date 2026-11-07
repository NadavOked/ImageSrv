# fwimage.sh -- the machine's firmware image for the server (#2080, mockup 06). POSIX sh.
#
# Two things the hello did not carry: the SATA controller mode (ahci/ide/raid) and the BIOS
# version. The server keeps an image per machine and says when it changed (server/agent/
# firmware_image.py). And the way back: the server's warning about a moved SATA connection is
# drawn on the cloner's wait screen, with "זה מכוון" (Nadav 25/10) -- fw_accept_post.
# SYSROOT prefixes sysfs in tests.

fw_dmi_field() {   # $1 = file under /sys/class/dmi/id -> a JSON string, or null (an empty field is no field)
    _fd_v=$(trim "$(cat "$SYSROOT/sys/class/dmi/id/$1" 2>/dev/null)")
    if [ -n "$_fd_v" ]; then printf '"%s"' "$(json_escape "$_fd_v")"; else printf 'null'; fi
}

fw_sata_mode() {
    # By the PCI class of the storage controllers, not by the driver that bound: a driver left out of
    # the initramfs (#84) would read as "no controller". 0x0101 IDE, 0x0104 RAID (VMD/RST), 0x0106 SATA.
    # Exactly one mode among them, or nothing: two different modes (SATA AHCI + a VMD) cannot be said as one,
    # and a class that cannot be read is "not reported" -- never a mode made up (principle 5).
    _fm_seen=""
    for _fm_d in "$SYSROOT"/sys/bus/pci/devices/*; do
        [ -r "$_fm_d/class" ] || continue
        read -r _fm_c < "$_fm_d/class" || continue
        case "$_fm_c" in 0x0101??) _fm_m=ide ;; 0x0104??) _fm_m=raid ;; 0x0106??) _fm_m=ahci ;; *) continue ;; esac
        case " $_fm_seen " in *" $_fm_m "*) ;; *) _fm_seen="$_fm_seen $_fm_m" ;; esac
    done
    case "${_fm_seen# }" in ahci|ide|raid) printf '%s' "${_fm_seen# }" ;; esac
}

fw_bios_json() {   # {"version","date","vendor"}; nothing without a version
    [ -n "$(trim "$(cat "$SYSROOT/sys/class/dmi/id/bios_version" 2>/dev/null)")" ] || return 0
    printf '{"version":%s,"date":%s,"vendor":%s}' "$(fw_dmi_field bios_version)" "$(fw_dmi_field bios_date)" "$(fw_dmi_field bios_vendor)"
}

firmware_json() {
    # ,"sata_mode":..,"bios":{..} -- the hello's fragment (build_hello); a part that could not be read is absent,
    # and the server keeps the previous value. Cached for the boot: neither changes while the machine is up.
    _fj_f="${RUN_DIR:-/nonexistent}/fwimage.hello"
    if [ -s "$_fj_f" ]; then cat "$_fj_f"; return 0; fi
    _fj_m=$(fw_sata_mode); _fj_b=$(fw_bios_json); _fj_o=""
    [ -z "$_fj_m" ] || _fj_o=",\"sata_mode\":\"$_fj_m\""
    [ -z "$_fj_b" ] || _fj_o="$_fj_o,\"bios\":$_fj_b"
    printf '%s' "$_fj_o"
    if [ -n "$_fj_o" ] && [ -d "${RUN_DIR:-/nonexistent}" ]; then printf '%s' "$_fj_o" > "$_fj_f.next" && mv "$_fj_f.next" "$_fj_f"; fi
    return 0
}

fw_warn_record() {
    # fw_warn=<count>|<text · text> from the last hello answer (clonergui.sh); nothing when there is no warning
    # or the answer cannot be read -- the line is advice, the round does not wait on it. And a toast for a
    # "זה מכוון" the server did not take, for a few seconds after it (the cloner's state file has no other way
    # to say it): a failure the operator is told, not one that looks like a button that did nothing.
    _fw_t=$(cat "$RUN_DIR/fw.failed" 2>/dev/null)
    case "$_fw_t" in ''|*[!0-9]*) ;; *) [ $(( $(date +%s) - _fw_t )) -gt "${FW_FAIL_SHOW_S:-4}" ] ||
        printf 'toast=%s
' 'השמירה נכשלה — השרת לא אישר. נסו שוב' ;; esac
    [ -s "$RUN_DIR/response.json" ] || return 0
    # count above what is listed (the server sends the first few): "· ועוד N" -- a change is never hidden
    jq -r 'select((.fw_warning.count // 0) > 0 and (.fw_warning.changes | type) == "array") | (.fw_warning.changes | length) as $n |
        "fw_warn=\(.fw_warning.count)|\(.fw_warning.changes | map(gsub("[\r\n|]"; " ")) | join(" · "))\(if .fw_warning.count > $n then " · ועוד \(.fw_warning.count - $n)" else "" end)"' \
        "$RUN_DIR/response.json" 2>/dev/null || return 0
}

fw_accept_post() {
    # "זה מכוון": this machine's current image becomes the right one. Done only on the server's 200 -- anything else
    # (no answer, 403, 409) is a failure the screen says, and the warning stays. On 200 the warning goes from the
    # kept answer at once; the next hello would take it away in a few seconds anyway.
    printf '{"mac":"%s"}' "$MAC" > "$RUN_DIR/fw_accept.json" || return 1
    _fa_c=$(curl -s -o /dev/null -w '%{http_code}' --max-time "${HTTP_TIMEOUT:-10}" -H "Content-Type: application/json" \
        --data-binary "@$RUN_DIR/fw_accept.json" "${SERVER:-http://0.0.0.0}/api/v1/agent/firmware-accept" 2>/dev/null)
    rm -f "$RUN_DIR/fw_accept.json"
    [ "$_fa_c" = 200 ] || { date +%s > "$RUN_DIR/fw.failed"; return 1; }
    rm -f "$RUN_DIR/fw.failed"
    jq 'del(.fw_warning)' "$RUN_DIR/response.json" > "$RUN_DIR/response.json.fw" 2>/dev/null &&
        mv "$RUN_DIR/response.json.fw" "$RUN_DIR/response.json" || rm -f "$RUN_DIR/response.json.fw"
    return 0
}
