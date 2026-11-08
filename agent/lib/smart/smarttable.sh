# smarttable.sh -- #1859 (Nadav 07/11, mockup 1859-smart-table): every SMART attribute of an
# idle disk, for the cloner's screen. POSIX sh (busybox ash) + awk. The GUI draws what is
# written here (state records csmart= / csmartrow=, interfaces.md) -- it knows no table.
#
# Same sources as the console's table, no second opinion:
#  * the disk's cached `smartctl -x` output ($RUN_DIR/smart/<d>.out -- smart_probe) and the
#    judge's findings on it (<d>.find, smartjudge.sh): the orange / red / hardware rows;
#  * the two knowledge tables (data/*.tsv): the Hebrew name of (id, the name smartctl printed),
#    the reason word -- a critical word makes the row "critical" (server/disks/smart_names.py);
#  * hello's `smart_rose` -- what rose since this serial's previous round.
# An attribute the tables do not know is shown by the name the vendor printed, "unknown" --
# never fine, never a colour (principle 5). "No comparison" is three words, not two:
# 1 compared, 0 the server has no previous round, - the answer was not read (an older server).
# Nothing is written for a disk smartctl did not read, nor when the judge's findings are missing.

#: the reason words of Grok's critical list (+ the cable's "crc"): smart_names.CRITICAL_WORDS | {"crc"}
SMART_CRITICAL_WORDS="reallocated spin_retry reported_uncorrectable realloc_event pending_sectors uncorrectable bad_blocks crc"
#: the NVMe health lines smartattrs.sh sends (the console shows these), by the table's key
SMART_NVME_SHOWN="critical_warning temperature available_spare available_spare_threshold percentage_used media_errors unsafe_shutdowns power_on_hours power_cycles error_log_entries"

smart_rose_refresh() {
    # hello's `smart_rose` -> $RUN_DIR/smart_rose: the first line "ok" (the answer was read, and
    # carries the field), then serial|id=n,id=n for each disk that has a previous round. Not an
    # array (an older server) or unreadable: the previous file stays (principle 5, as smart_learned_refresh).
    json_get_join "$RUN_DIR/response.json" \
        'if (.smart_rose | type) == "array" then ["ok"] + (.smart_rose | map("\(.serial // "")|\((.rose // {}) | to_entries | map("\(.key)=\(.value)") | join(","))")) else [] end' \
        > "$RUN_DIR/smart_rose.next" 2>/dev/null
    [ "$(head -n 1 "$RUN_DIR/smart_rose.next" 2>/dev/null)" = ok ] || { rm -f "$RUN_DIR/smart_rose.next"; return 1; }
    mv "$RUN_DIR/smart_rose.next" "$RUN_DIR/smart_rose"
}

smart_table_one() {
    # $1 = disk, $2 = compared (1|0|-), $3 = what rose, "5=3,9=31". Prints the disk's records.
    _st_o="$RUN_DIR/smart/$1.out"; _st_f="$RUN_DIR/smart/$1.find"
    smart_was_read "$1" && [ -s "$_st_o" ] && [ -f "$_st_f" ] && [ -s "$SMART_KB/smart-knowledge.tsv" ] || return 0
    awk -v dev="$1" -v cmp="$2" -v rose="$3" -v crit=" $SMART_CRITICAL_WORDS " -v shown=" $SMART_NVME_SHOWN " \
        -v kb1="$SMART_KB/smart-knowledge.tsv" -v kb2="$SMART_KB/hw-issues.tsv" -v fd="$_st_f" '
        function lead(s) { gsub(/,/, "", s); sub(/^[^0-9]*/, "", s); sub(/[^0-9].*/, "", s); return s }
        function hex(s,   v, i, c) { v = 0; s = tolower(s); sub(/^[ \t]*0x/, "", s)
            for (i = 1; i <= length(s); i++) { c = index("0123456789abcdef", substr(s, i, 1)); if (!c) return ""; v = v * 16 + c - 1 }
            return v }
        function add(sort, id, fl, raw, name, ro) { n++; S[n] = sort; R[n] = id "|" fl "|" raw "|" ro "|" name }
        BEGIN {
            for (f = 1; f <= 2; f++) { file = f == 1 ? kb1 : kb2
                while ((getline l < file) > 0) { if (l ~ /^#/ || l == "") continue
                    split(l, c, "\t")
                    if (c[1] == "ata") { HE[c[2] SUBSEP c[3]] = c[10]; WD[c[2] SUBSEP c[3]] = c[8] }
                    else if (c[1] == "nvme") { KEY[c[3]] = c[2]; NHE[c[2]] = c[10] } }
                close(file) }
            while ((getline l < fd) > 0) { split(l, p, "|")        # verdict|word|kind|key|name|value
                if (p[1] != "orange" && p[1] != "red" && p[1] != "hw") continue
                if (p[3] == "ata") V[p[4] SUBSEP p[5]] = p[1]
                else if (p[3] == "nvme") NV[p[4]] = p[1]
                else if (p[3] == "nvmebit" && NV["critical_warning"] != "red") NV["critical_warning"] = p[1] }
            close(fd)
            m = split(rose, rp, ","); for (i = 1; i <= m; i++) { split(rp[i], kv, "="); if (kv[2] + 0 > 0) RO[kv[1]] = kv[2] + 0 }
        }
        # an ATA attribute row: -f old (FLAG 0x0033, RAW from $10) or brief (FLAGS PO--CK, RAW from $8) -- as smartattrs.sh
        $1 ~ /^[0-9]+$/ && ($3 ~ /^0x[0-9a-fA-F]+$/ && NF >= 10 || $3 ~ /^[-POSRCK]+$/ && $4 ~ /^[0-9]+$/ && NF >= 8) {
            id = $1 + 0; nm = $2; gsub(/[^A-Za-z0-9_-]/, "", nm); k = id SUBSEP nm
            raw = lead($3 ~ /^0x/ ? $10 : $8); val = $4 ~ /^[0-9]+$/ ? $4 + 0 : -1; th = $6 ~ /^[0-9]+$/ ? $6 + 0 : 0
            if (k in HE) { name = HE[k]; fl = ""; w = WD[k] } else { name = nm; fl = "u"; w = "" }
            if (w != "" && index(crit, " " w " ")) fl = fl "c"
            if (w == "crc") fl = fl "k"                            # the cable: a rise there is not "replace the disk" (#872)
            if (V[k] == "orange") fl = fl "o"; else if (V[k] == "red") fl = fl "R"; else if (V[k] == "hw") fl = fl "h"
            if (th > 0 && val >= 0 && val <= th) fl = fl "f"       # at the vendor threshold -- what the BIOS sees; red (Nadav 07/11)
            add(id, id, fl, raw, name, RO[id]); next }
        /^[A-Z][A-Za-z .\/]*:/ {
            lb = $0; sub(/:.*/, "", lb); if (!(lb in KEY)) next
            key = KEY[lb]; if (!index(shown, " " key " ") || (key in SEEN)) next
            SEEN[key] = 1; v = $0; sub(/^[^:]*:[ \t]*/, "", v)
            raw = key == "critical_warning" ? hex(v) : lead(v); fl = ""
            if (key == "critical_warning" || key == "media_errors") fl = fl "c"
            if (NV[key] == "orange") fl = fl "o"; else if (NV[key] == "red") fl = fl "R"
            add(100000 + n, key, fl, raw, NHE[key], RO[key]) }
        END {
            for (i = 2; i <= n; i++) { s = S[i]; r = R[i]; j = i - 1               # by id, NVMe after (the console sorts so)
                while (j >= 1 && S[j] > s) { S[j + 1] = S[j]; R[j + 1] = R[j]; j-- }
                S[j + 1] = s; R[j + 1] = r }
            printf "csmart=%s|%d|%s\n", dev, n, cmp
            for (i = 1; i <= n; i++) printf "csmartrow=%s|%s\n", dev, R[i] }' "$_st_o"
}

smart_tables_state() {
    # $1 = the disks (list_disks). The records of every disk smartctl read -- on the idle
    # screen, where the cloner's disks are. A table that cannot be built is a disk with no
    # "all attributes" line, never a table that says everything is fine.
    smart_rose_refresh || :
    for _st_d in $1; do
        _st_s=$(disk_serial "$_st_d"); _st_c=0; _st_r=
        if [ ! -f "$RUN_DIR/smart_rose" ]; then _st_c=-
        elif [ -n "$_st_s" ] && _st_l=$(awk -F'|' -v s="$_st_s" 'NR > 1 && $1 == s { print "1|" $2; exit }' "$RUN_DIR/smart_rose") && [ -n "$_st_l" ]; then
            _st_c=1; _st_r=${_st_l#1|}
        fi
        smart_table_one "$_st_d" "$_st_c" "$_st_r" || :
    done
}
