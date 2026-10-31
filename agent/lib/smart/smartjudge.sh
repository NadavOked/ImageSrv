# smartjudge.sh -- #1921: a disk judged by the knowledge tables, with no history.
# POSIX sh (busybox ash) + awk. Reads the disk's `smartctl -x` output that smart_probe
# keeps ($RUN_DIR/smart/<d>.out) -- never smartctl itself.
#
# The two tables are data files (lib/data/, packed into the initramfs next to this file),
# and the server reads the same two files (server/smart_names.py; a test checks it):
#   smart-knowledge.tsv -- the disk itself: the colour (orange / red) and its reason word;
#   hw-issues.tsv       -- the cable, the drawer, the controller: a NOTE on the drawer or
#                          the machine, never the disk's colour (Nadav 20/10).
# An attribute is matched by (id, the name smartctl printed): smartctl already applied
# drivedb.h, so the name carries the vendor's meaning (#1922: 198 on an OCZ Intrepid is
# "Host_Reads_GiB", not "Offline_Uncorrectable"). An id or a name in neither table is
# "unknown" -- printed as such, never "fine" and never a colour (principle 5).

SMART_KB="${SMART_KB:-${LIB_DIR:-/usr/lib/imagesrv}/data}"

smart_judge() {
    # $1 = smartctl -x output, $2 = the CRC threshold (crc_warn_threshold; 0 or empty = off).
    # One finding a line: verdict|word|kind|key|name|value -- verdict red|orange|hw|unknown.
    # Nothing printed for a rule that did not hold, nor for an information row.
    # 1 = a table could not be read: no finding is evidence of nothing then (principle 5).
    [ -s "$SMART_KB/smart-knowledge.tsv" ] && [ -s "$SMART_KB/hw-issues.tsv" ] || return 1
    awk -v crc="${2:-0}" -v kb1="$SMART_KB/smart-knowledge.tsv" -v kb2="$SMART_KB/hw-issues.tsv" '
        function lead(s) { gsub(/,/, "", s); sub(/^[^0-9]*/, "", s); sub(/[^0-9].*/, "", s); return s }
        function hex(s,   v, i, c) { v = 0; s = tolower(s); sub(/^0x/, "", s)
            for (i = 1; i <= length(s); i++) { c = index("0123456789abcdef", substr(s, i, 1)); if (!c) return -1; v = v * 16 + c - 1 }
            return v }
        function holds(k, x,   t) {
            t = (V[k] == "crc") ? crc + 0 : (V[k] == "wctemp") ? wctemp : V[k] + 0
            if (V[k] == "wctemp" && wctemp == "") return 0      # no WCTEMP printed: not judged (below: unknown)
            if (V[k] == "crc" && crc + 0 == 0) return 0          # crc_warn_threshold 0 = the note is off (crcwarn.py)
            return (O[k] == ">" && x > t) || (O[k] == ">=" && x >= t) || (O[k] == "<" && x < t) || (O[k] == "<=" && x <= t)
        }
        function say(k, key, name, x) { if (W[k] != "") print C[k] "|" W[k] "|" K[k] "|" key "|" name "|" x }
        function judge(k, key, name, x) {
            if (!(k in O)) { print "unknown|-|" substr(k, 1, index(k, SUBSEP) - 1) "|" key "|" name "|" x; return }
            if (O[k] == "-" || x == "") return
            if (M[k] != "any" && M[k] != media) return       # e.g. 183 Runtime_Bad_Block: an SSD only (ס3)
            if (holds(k, x + 0)) say(k, key, name, x)
        }
        BEGIN {
            for (f = 1; f <= 2; f++) {
                file = f == 1 ? kb1 : kb2
                while ((getline l < file) > 0) {
                    if (l ~ /^#/ || l == "") continue
                    split(l, c, "\t"); k = c[1] SUBSEP (c[1] == "ata" ? c[2] SUBSEP c[3] : c[1] == "nvme" || c[1] == "nvmebit" ? c[2] : c[3])
                    K[k] = c[1]; F[k] = c[4]; O[k] = c[5]; V[k] = c[6]; C[k] = c[7]; W[k] = (c[8] == "-") ? "" : c[8]; M[k] = c[9]
                    if (c[1] == "nvme") NL[c[3]] = c[2]
                    if (c[1] == "selftest" || c[1] == "errlog") { n = ++LN[c[1]]; L[c[1], n] = c[3]; LK[c[1], n] = k }
                }
                close(file)
            }
            media = "any"
        }
        /^Rotation Rate:/ { media = ($0 ~ /Solid State/) ? "ssd" : ($0 ~ /rpm/) ? "hdd" : media; next }
        /^SMART\/Health Information/ { media = "ssd"; next }
        /^Warning  *Comp\. Temp\. Threshold:/ { wctemp = lead($0); next }
        # an ATA attribute row: -f old (FLAG 0x0033, RAW from $10) or brief (FLAGS PO--CK, RAW from $8)
        $1 ~ /^[0-9]+$/ && NF >= 8 && ($3 ~ /^0x[0-9a-fA-F]+$/ || $3 ~ /^[-POSRCK]+$/) && $4 ~ /^[0-9]+$/ {
            k = "ata" SUBSEP ($1 + 0) SUBSEP $2; raw = lead($3 ~ /^0x/ ? $10 : $8)
            if (!(k in O)) { judge(k, $1 + 0, $2, raw); next }
            if (F[k] == "value") x = $4 + 0
            else if (F[k] == "temp") { x = raw + 0; if (x > 150) x = x % 256; if (raw == "" || x < 1 || x > 150) x = "" }
            else x = raw
            judge(k, $1 + 0, $2, x); next
        }
        # devstat (`-l devstat`): Page Offset Size Value Flags Description
        $1 ~ /^0x0[0-9a-f]$/ && $2 ~ /^0x[0-9a-f]+$/ && $4 ~ /^[0-9]+$/ && NF >= 6 {
            d = $0; sub(/^[^ ]+ +[^ ]+ +[^ ]+ +[^ ]+ +[^ ]+ +/, "", d)
            if (("devstat" SUBSEP d) in O) judge("devstat" SUBSEP d, "-", d, $4 + 0); next
        }
        # NVMe health lines: "Label:   value"
        /^[A-Z][A-Za-z .\/]*:/ {
            lb = $0; sub(/:.*/, "", lb)
            if (lb in NL) { v = $0; sub(/^[^:]*:[ \t]*/, "", v); NV[NL[lb]] = v; NN[NL[lb]] = lb }
        }
        /[Ss]elf-test [Ll]og/ { insel = 1; next }
        insel && ($0 ~ /^# *1 / || $0 ~ /^ *0 +[A-Za-z]/) {
            for (i = 1; i <= LN["selftest"]; i++) if (index($0, L["selftest", i])) { say(LK["selftest", i], "1", L["selftest", i], 1); break }
            insel = 0; next
        }
        /^ *Error: [A-Z]/ {
            e = $0; sub(/^ *Error: */, "", e); sub(/ at LBA.*| [0-9].*/, "", e); n = split(e, et, /[, ]+/)
            for (j = 1; j <= n; j++) for (i = 1; i <= LN["errlog"]; i++)
                if (et[j] == L["errlog", i] && !(et[j] in SEEN)) { SEEN[et[j]] = 1; say(LK["errlog", i], "-", et[j], 1) }
            next
        }
        END {
            for (key in NV) {
                v = NV[key]; k = "nvme" SUBSEP key
                if (key == "critical_warning") {
                    cw = hex(v); if (cw < 0) { print "unknown|-|nvme|critical_warning|" NN[key] "|" v; continue }
                    for (b = 0; b < 8; b++) if (int(cw / 2 ^ b) % 2) {
                        bk = "nvmebit" SUBSEP b
                        if (bk in O) say(bk, b, NN[key], v); else print "unknown|-|nvmebit|" b "|" NN[key] "|" v
                    }
                    continue
                }
                x = lead(v)
                if (V[k] == "wctemp" && wctemp == "") { print "unknown|-|nvme|" key "|" NN[key] "|" x; continue }
                judge(k, key, NN[key], x)
            }
        }' "$1"
}

smart_judge_disk() {
    # $1 = disk. Judges its cached output into $RUN_DIR/smart/<d>.find and prints the
    # disk's own worst: "red <word>", "orange <word>" (the first of SMART_WARN_REASONS
    # that holds -- smart.sh) or nothing. hw lines stay in the file for hw_words (hwissues.sh).
    _jd_f="$RUN_DIR/smart/$1.find"
    _jd_crc=$(json_get "$RUN_DIR/response.json" ".crc_warn_threshold" 2>/dev/null)
    case "$_jd_crc" in ''|*[!0-9]*) _jd_crc=0 ;; esac   # an older server: off, as clonergui.sh
    smart_judge "$RUN_DIR/smart/$1.out" "$_jd_crc" > "$_jd_f.next" || { rm -f "$_jd_f.next"; return 1; }
    mv "$_jd_f.next" "$_jd_f"
    _jd_r=$(awk -F'|' '$1 == "red" { print $2; exit }' "$_jd_f")
    [ -n "$_jd_r" ] && { printf 'red %s' "$_jd_r"; return 0; }
    for _jd_w in $SMART_WARN_REASONS; do
        grep -q "^orange|$_jd_w|" "$_jd_f" && { printf 'orange %s' "$_jd_w"; return 0; }
    done
    return 0
}

smart_kb_he() {
    # $1 = a word of hw-issues.tsv's hw rows -> its Hebrew sentence (the note on the drawer).
    awk -F'\t' -v w="$1" '$1 == "hw" && $2 == w { print $11; exit }' "$SMART_KB/hw-issues.tsv" 2>/dev/null
}
