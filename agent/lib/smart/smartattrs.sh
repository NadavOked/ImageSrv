# smartattrs.sh -- every SMART attribute of a disk, and the orange of #1859. POSIX sh
# (busybox ash). smart.sh reads the disk once (smart_probe: `smartctl -x -f old`, kept in
# $RUN_DIR/smart/<d>.out); this file reads that same output again, never smartctl.
#
# Nadav, 20/10 (#1859): "yes to all", "if the server is smart and learns".
#  * every attribute the disk reports goes to the server -- ids and numbers in the
#    hello, with the vendor's name in the disk_event once a round. The Hebrew names
#    live in the server (server/smart_names.py): the 300-line wall is here.
#  * ORANGE (asked; no answer = write) on a disk smart_classify called ok: #1921's table
#    (smartjudge.sh -- 5/187/10/196 over 0, worn, out of spare, ...), and -- what the server
#    learned from this serial's previous round (hello `smart_learned`) -- 183/188
#    that rose, a critical attribute that rose ("replace the disk"), a disk that ran
#    hot while writing. RED (#1921): the disk's own FAILED, NVMe read-only, a read-only disk -- 197/198 are orange.

#    The reason words, worst first: SMART_WARN_REASONS in smart.sh (loaded before this).

_smart_attr_rows() {
    # $1 = smartctl output, $2 = 1 to add the vendor's name. One JSON row per attribute:
    # ATA [id,value,worst,thresh,raw(,name)] -- thresh "---" and a raw with no leading
    # number are null, never 0 (principle 5) -- and the NVMe health lines by key.
    awk -v names="$2" '
        function num(s) { gsub(/,/, "", s); sub(/^[^0-9]*/, "", s); sub(/[^0-9].*/, "", s); return s == "" ? "null" : s + 0 }
        function hex(s,   v, i, c) { v = 0; s = tolower(s); sub(/^0x/, "", s)
            for (i = 1; i <= length(s); i++) { c = index("0123456789abcdef", substr(s, i, 1)); if (!c) return "null"; v = v * 16 + c - 1 }
            return v }
        $1 ~ /^[0-9]+$/ && ($3 ~ /^0x[0-9a-fA-F]+$/ && NF >= 10 || $3 ~ /^[-POSRCK]+$/ && $4 ~ /^[0-9]+$/ && NF >= 8) {
            raw = ($3 ~ /^0x/) ? $10 : $8; t = ($6 ~ /^[0-9]+$/) ? $6 + 0 : "null"   # #1921: -f old, or -x brief
            row = "[" ($1 + 0) "," num($4) "," num($5) "," t "," num(raw)
            if (names == 1) { n = $2; gsub(/[^A-Za-z0-9_-]/, "", n); row = row ",\"" n "\"" }
            ata = ata (ata == "" ? "" : ",") row "]"; next }
        /^Critical Warning:/ { k["critical_warning"] = hex($3); next }
        /^Temperature:/ { k["temperature"] = num($2); next }
        /^Available Spare:/ { k["available_spare"] = num($3); next }
        /^Available Spare Threshold:/ { k["available_spare_threshold"] = num($4); next }
        /^Percentage Used:/ { k["percentage_used"] = num($3); next }
        /^Media and Data Integrity Errors:/ { k["media_errors"] = num($6); next }
        /^Unsafe Shutdowns:/ { k["unsafe_shutdowns"] = num($3); next }
        /^Power On Hours:/ { k["power_on_hours"] = num($4); next }
        /^Power Cycles:/ { k["power_cycles"] = num($3); next }
        /^Error Information Log Entries:/ { k["error_log_entries"] = num($5); next }
        END {
            nv = ""; for (x in k) nv = nv (nv == "" ? "" : ",") "\"" x "\":" k[x]
            printf "{\"ata\":[%s],\"nvme\":%s}", ata, (nv == "" ? "null" : "{" nv "}") }' "$1"
}

smart_attrs_json() {
    # $1 = disk, $2 = 1 for the vendor's names (the disk_event). Prints the object, or
    # null: a disk smartctl did not read (smart_was_read) is null, never an empty list (principle 5).
    if smart_was_read "$1" && [ -s "$RUN_DIR/smart/$1.out" ]; then
        _smart_attr_rows "$RUN_DIR/smart/$1.out" "${2:-0}"
    else printf null; fi
}

smart_attrs_hello() { printf ',"smart_attrs":%s' "$(smart_attrs_json "$1")"; }   # $1 = disk: sysinfo.sh's fragment

smart_learned_refresh() {
    # hello's `smart_learned` (the server's comparison with this serial's previous round)
    # -> $RUN_DIR/smart_learned, serial|reason a line. The first line "ok" is the proof the
    # answer was read whole; otherwise the previous file stays (principle 5, as
    # disk_failures_refresh). 0 = refreshed.
    json_get_join "$RUN_DIR/response.json" \
        '["ok"] + ((.smart_learned // []) | map("\(.serial // "")|\(.reason // "")"))' \
        > "$RUN_DIR/smart_learned.next" 2>/dev/null
    [ "$(head -n 1 "$RUN_DIR/smart_learned.next" 2>/dev/null)" = ok ] || { rm -f "$RUN_DIR/smart_learned.next"; return 1; }
    mv "$RUN_DIR/smart_learned.next" "$RUN_DIR/smart_learned"
}

smart_learned_reason() {
    # $1 = disk. The reason the server learned for its serial (one of SMART_WARN_REASONS),
    # 0; nothing and 1 without a serial, a record or a known word.
    _sl_s=$(disk_serial "$1"); [ -n "$_sl_s" ] && [ -f "$RUN_DIR/smart_learned" ] || return 1
    _sl_r=$(awk -F'|' -v s="$_sl_s" 'NR > 1 && $1 == s { print $2; exit }' "$RUN_DIR/smart_learned" 2>/dev/null)
    case " $SMART_WARN_REASONS " in *" $_sl_r "*) [ -n "$_sl_r" ] && printf '%s' "$_sl_r" ;; *) return 1 ;; esac
}

smart_temp_after() {
    # $1 = smartctl -A output read after the write (crcdelta.sh). The disk's temperature
    # in Celsius -- 194 (or 190) raw, NVMe Temperature -- or nothing.
    awk '$3 ~ /^0x/ && NF >= 10 && ($1 == 194 || $1 == 190) { v = $10; sub(/[^0-9].*/, "", v); if (v != "") { print v + 0; exit } }
         /^Temperature:/ { v = $2; sub(/[^0-9].*/, "", v); if (v != "") { print v + 0; exit } }' "$1" 2>/dev/null
}
