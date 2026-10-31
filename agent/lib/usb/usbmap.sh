# usbmap.sh -- #1962/#2001: the connections of the clone stick, by machine model. POSIX sh.
#
# The server's #1485 (drawer_slots): drawer k -> the connection it is wired to,
# "sata:<ataN>" or "nvme:<PCI>" (docs/interfaces.md s2). The stick has no server,
# so the mapping lives in the stick's settings.json, BY MACHINE MODEL (Nadav
# 22/10) -- {"drawer_slots": {"<DMI product_name>": ["sata:2", "sata:1"]}} --
# never by a disk's serial: the stick keeps nothing about disks. usb_answer
# puts the list in force into response.json as a server's hello would, and every
# screen numbers by it (slotnames.sh drawer_slots_refresh, disk_drawer).
#
# #2001 (Nadav 22/10, the approved mockup slot-map-2001): one window of every
# connection (usbmapwin.sh). The list is the active rows in the window's order;
# the names go to slot_names[model] ({key: name}, #704's rules) and replace
# "דיסק N" on every screen of the stick. No saved list = the default: every
# connection with a disk now, in order -- "דיסק 1, 2, 3...". A connection added
# by hand is a type and a number only: "nvme#<n>" stands for the n-th NVMe
# until a disk is seen there, and then it is that controller's PCI address.

#: the keys: the server's (console_api #1485), 1-32, PCI lower case -- and the stick's
#: own "nvme#<n>", an NVMe added by hand that the machine does not show yet
USBMAP_KEY_RE='^(sata:([1-9]|[12][0-9]|3[0-2])|nvme:[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}[.][0-7]|nvme#[1-9])$'
#: a name -- server/slot_names.py: 1-24 characters, letters, digits, space and - . # ( ) /,
#: no space at either end. Never | or a tab: the state files and the frames split on them.
USBMAP_NAME_RE='^[A-Za-z0-9_א-ת.#()/ -]{1,24}$'
USBMAP_ROWS=12   # the window's rows at most (dcui.h DCUI_MAX_EL, a 768-line screen)
#: jq: nvme#<n> -> the n-th NVMe the machine shows ($d, in order) when no row has that one yet
USBMAP_RESOLVE='def rkey($d; $taken): if test("^nvme#") then (ltrimstr("nvme#") | tonumber) as $n
    | if $n <= ($d | length) and ($taken | index([$d[$n - 1]])) == null then $d[$n - 1] else . end else . end;'

usb_model() {   # this machine's model (DMI product_name), or nothing
    trim "$(tr -d '\r\n' < "$SYSROOT/sys/class/dmi/id/product_name" 2>/dev/null)"
}

usb_map_json() {
    # This model's list from the settings file, compact JSON; 1 = none. 2 = there
    # is one and it is not valid (1-8 keys, no repeat, the keys above): refused.
    _um_m=$(usb_model); [ -n "$_um_m" ] || return 1
    _um_f=$(usb_settings_path); [ -f "$_um_f" ] || return 1
    _um_r=$(jq -c --arg m "$_um_m" 'if type == "object" and (.drawer_slots | type) == "object"
        then .drawer_slots[$m] else null end' "$_um_f" 2>/dev/null) || return 1
    [ -n "$_um_r" ] && [ "$_um_r" != null ] || return 1
    printf '%s' "$_um_r" | jq -ce --arg re "$USBMAP_KEY_RE" 'select(type == "array" and length >= 1 and length <= 8
        and (unique | length) == length and all(.[]; type == "string" and test($re)))' 2>/dev/null || return 2
}

usb_names_json() {
    # This model's names, {key: name}, compact; 1 = none. 2 = there are and they are
    # not valid (a key not one of the above, a name past #704's rules): refused, none used.
    _un_m=$(usb_model); [ -n "$_un_m" ] || return 1
    _un_f=$(usb_settings_path); [ -f "$_un_f" ] || return 1
    _un_r=$(jq -c --arg m "$_un_m" 'if type == "object" and (.slot_names | type) == "object"
        then .slot_names[$m] else null end' "$_un_f" 2>/dev/null) || return 1
    [ -n "$_un_r" ] && [ "$_un_r" != null ] || return 1
    printf '%s' "$_un_r" | jq -ce --arg re "$USBMAP_KEY_RE" --arg nre "$USBMAP_NAME_RE" 'select(type == "object"
        and all(to_entries[]; (.key | test($re)) and (.value | type == "string" and test($nre) and (test("^ | $") | not))))' \
        2>/dev/null || return 2
}

_um_size() {   # $1 = bytes. The size as the mockup writes it: "500GB", "1TB", "1.5TB".
    awk -v b="$1" 'BEGIN { g = int(b / 1e9); if (g < 1000) { printf "%dGB", g; exit }
        t = g / 1000; if (t == int(t)) printf "%dTB", t; else printf "%.1fTB", t }'
}

usb_map_conns() {
    # "key|kind|model|size" per connection this machine shows: each SATA port
    # (/sys/class/ata_port) -- disk, optical (a CD/DVD drive: never a drawer) or empty --
    # then each NVMe controller with a disk, by PCI address. Never the stick itself.
    _uc_t=""
    for _uc_d in $(list_disks); do
        lc_is_boot_media "$_uc_d" && continue
        _uc_p=$(disk_port "$_uc_d"); _uc_k=$(disk_key "$_uc_d" "${_uc_p:-0}"); [ -n "$_uc_k" ] || continue
        _uc_s=$(cat "$SYSROOT/sys/block/$_uc_d/size" 2>/dev/null); case "$_uc_s" in ''|*[!0-9]*) _uc_s=0 ;; esac
        _uc_w=$(trim "$(cat "$SYSROOT/sys/block/$_uc_d/device/model" 2>/dev/null)" | tr '|\r\n' '   ')
        _uc_t="$_uc_t$_uc_k|disk|${_uc_w:-דיסק}|$(_um_size $((_uc_s * 512)))
"
    done
    for _uc_o in "$SYSROOT"/sys/block/sr*; do
        [ -e "$_uc_o" ] || continue
        _uc_p=$(port_from_path "$(readlink -f "$_uc_o/device" 2>/dev/null)"); case "$_uc_p" in ''|*[!0-9]*) continue ;; esac
        _uc_t="${_uc_t}sata:$_uc_p|optical|$(trim "$(cat "$_uc_o/device/model" 2>/dev/null)" | tr '|\r\n' '   ')|
"
    done
    for _uc_a in "$SYSROOT"/sys/class/ata_port/ata*; do
        [ -e "$_uc_a" ] && echo "${_uc_a##*/ata}"
    done | sort -n | while read -r _uc_n; do
        _uc_l=$(printf '%s' "$_uc_t" | grep -m1 "^sata:$_uc_n|")
        echo "${_uc_l:-sata:$_uc_n|empty||}"
    done
    printf '%s' "$_uc_t" | grep '^nvme:' | sort
    return 0
}

usb_conns_json() {   # usb_map_conns as [{k, kind, m, s}]
    usb_map_conns | jq -Rcs 'split("\n") | map(select(length > 0) | split("|") | {k: .[0], kind: .[1], m: .[2], s: .[3]})'
}

usb_map_live() {
    # The list in force, compact: this model's saved one, its nvme#<n> resolved against
    # the machine now -- else the default, every connection with a disk in order (at most 8).
    # 1 = none (no saved list and no disk).
    _ul_c=$(usb_conns_json)
    if _ul_l=$(usb_map_json); then
        printf '%s' "$_ul_l" | jq -c --argjson c "$_ul_c" "$USBMAP_RESOLVE"' . as $l
            | [$c[] | .k | select(startswith("nvme:"))] as $d | map(rkey($d; $l))'
    else
        printf '%s' "$_ul_c" | jq -ce '[.[] | select(.kind == "disk") | .k] | .[:8] | select(length > 0)' || return 1
    fi
}

usb_drawer_names() {
    # $RUN_DIR/drawer_names, "key|name" per named connection (nvme#<n> resolved as the
    # list is) -- what the screens read (usb_drawer_name). No valid names = no file.
    if _dn_j=$(usb_names_json); then
        _dn_l=$(usb_map_json) || _dn_l='[]'
        printf '%s' "$_dn_j" | jq -r --argjson c "$(usb_conns_json)" --argjson l "$_dn_l" "$USBMAP_RESOLVE"'
            [$c[] | .k | select(startswith("nvme:"))] as $d | ($l + keys) as $t
            | to_entries[] | "\(.key | rkey($d; $t))|\(.value)"' > "$RUN_DIR/drawer_names.next" \
            && mv "$RUN_DIR/drawer_names.next" "$RUN_DIR/drawer_names"
    else rm -f "$RUN_DIR/drawer_names"; fi
}

usb_drawer_name() {   # $1 = dev, $2 = its port. The name of its connection, or nothing.
    [ -f "$RUN_DIR/drawer_names" ] || return 0
    _dn_k=$(disk_key "$1" "$2"); [ -n "$_dn_k" ] || return 0
    awk -F'|' -v k="$_dn_k" '$1 == k { print $2; exit }' "$RUN_DIR/drawer_names"
}

usb_default_follow() {
    # The default list follows the disks: with no saved list, a disk put in (or taken out)
    # renumbers the drawers as the window shows them. Called by lc_gui_state on the stick.
    [ "${IMAGESRV_MODE:-}" = usbclone ] || return 0
    usb_data_state_read; if usb_map_json > /dev/null 2>&1; then return 0; fi
    _uf_s=$(list_disks | tr '\n' ' ')
    [ "$_uf_s" != "$(cat "$RUN_DIR/usb.default.sig" 2>/dev/null)" ] || return 0
    usb_settings_load 2> /dev/null && usb_answer && printf '%s\n' "$_uf_s" > "$RUN_DIR/usb.default.sig"
}

usb_map_save() {
    # $1 = the window's rows ([{k, n, a, kind}]), or "" = this model's list and names removed
    # (the default). The active rows' keys in order -> drawer_slots[model], the names (no
    # space at either end) -> slot_names[model]; written, then read back (principle 5).
    # 0 = saved and in force; 2 = the write failed; 1 = it did not read back the same.
    _ms_m=$(usb_model); [ -n "$_ms_m" ] || return 2
    usb_data_state_read; _ms_f=$(usb_settings_path); _ms_j=$(cat "$_ms_f" 2>/dev/null); [ -n "$_ms_j" ] || _ms_j='{}'
    _ms_r=${1:-[]}
    _ms_l=$(printf '%s' "$_ms_r" | jq -c '[.[] | select(.a and .kind != "optical") | .k]') || return 2
    _ms_n=$(printf '%s' "$_ms_r" | jq -c '[.[] | {key: .k, value: (.n | sub("^ +"; "") | sub(" +$"; ""))}
        | select(.value != "")] | from_entries') || return 2
    _ms_new=$(printf '%s' "$_ms_j" | jq -c --arg m "$_ms_m" --argjson l "$_ms_l" --argjson n "$_ms_n" '
        def put($d; $v): (if (.[$d] | type) == "object" then .[$d] else {} end)
            | if ($v | length) == 0 then del(.[$m]) else .[$m] = $v end;
        (if type == "object" then . else {} end) | .drawer_slots = put("drawer_slots"; $l) | .slot_names = put("slot_names"; $n)
        | if (.drawer_slots | length) == 0 then del(.drawer_slots) else . end
        | if (.slot_names | length) == 0 then del(.slot_names) else . end') || return 2
    printf '%s\n' "$_ms_new" > "$_ms_f.next" && mv "$_ms_f.next" "$_ms_f" && sync || return 2
    jq -e --argjson want "$_ms_new" '. == $want' "$_ms_f" > /dev/null 2>&1 || return 1
    if [ "$_ms_l" = '[]' ]; then ! usb_map_json > /dev/null || return 1
    else [ "$(usb_map_json)" = "$_ms_l" ] || return 1; fi
    if [ "$_ms_n" = '{}' ]; then ! usb_names_json > /dev/null || return 1
    else [ "$(usb_names_json)" = "$_ms_n" ] || return 1; fi
    usb_settings_load && usb_answer || return 1
}
