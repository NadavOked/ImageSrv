# linksense.sh -- a drawer's SATA link, as the brain senses it (#3064: S52 phy events, S53 link resets, S58 the
# physical connection's name). POSIX sh (busybox ash). Needs hwissues.sh (_hw_ata), sensecore.sh (_ss_num).
#
# All three say what the LINK did, never what the disk is: a drawer with resets is a cable or a contact, not a verdict
# on the disk (Nadav 20/10, hwissues.sh). Each is `null` when it could not be read -- "not measured", never 0.
#
#   ls_phy_after DISK    once, after the write (crcdelta.sh): `smartctl -l sataphy` against the table the first SMART read
#                        kept (smart/<d>.out) -> targets/<d>/phy.json. The counters run since power-on, so only a delta says
#                        anything about this round. A counter that fell, a disk that did not answer, a first read without
#                        the table: no file, and the reason in the log.
#   ls_resets_refresh    the kernel log's "hard resetting link" / COMRESET / softreset failures, per ataN, at most every
#                        SENSE_RESETS_EVERY_S -> senses/resets.now ("ok" first, then "ataN count").
#   ls_resets_json D ST  {"boot","delta"}: the count since boot, and since the drawer's first tick as `writing`
#                        (targets/<d>/ss.resets0). A drawer first seen after the write has no baseline: delta null.
#   ls_conn_json D       {"ata","sata","drawer"}: the kernel's ataN, the "SATA N-1" the console draws, and the drawer
#                        number the server mapped to it (drawer_slots) -- "unmapped" when the map was read without it.

SENSE_RESETS_EVERY_S="${SENSE_RESETS_EVERY_S:-10}"
SMARTCTL="${SMARTCTL:-smartctl}"

_ls_phy_table() {   # $1 = smartctl output -> "id value" per counter of the SATA Phy Event table
    awk '/^SATA Phy Event Counters/ { on = 1; next }
         on && /^0x[0-9a-fA-F]+[ \t]/ { print $1, $3; next }
         on && /^ID[ \t]/ { next }
         on { on = 0 }' "$1" 2>/dev/null
}

ls_phy_after() {
    _lp_j="$RUN_DIR/targets/$1/phy.json"; rm -f "$_lp_j"
    command -v "$SMARTCTL" >/dev/null 2>&1 || { log "$1: phy events not measured (no smartctl)"; return 0; }
    _lp_b=$(_ls_phy_table "$RUN_DIR/smart/$1.out")
    [ -n "$_lp_b" ] || { log "$1: phy events not measured (the first SMART read has no phy table)"; return 0; }
    _lp_o="$RUN_DIR/smart/$1.phy"
    capped_run "$SMARTCTL_MAX_S" "$_lp_o" "$SMARTCTL" -l sataphy -n never "$DEVROOT/$1"; _lp_rc=$?
    [ "$CAPPED_TIMED_OUT" = 0 ] && [ "$((_lp_rc / 2 % 2))" -eq 1 ] &&
        { capped_run "$SMARTCTL_MAX_S" "$_lp_o" "$SMARTCTL" -d sat -l sataphy -n never "$DEVROOT/$1"; _lp_rc=$?; }
    if [ "$CAPPED_TIMED_OUT" = 1 ] || [ "$((_lp_rc / 2 % 2))" -eq 1 ]; then
        log "$1: phy events not measured (smartctl did not answer)"; return 0
    fi
    _lp_a=$(_ls_phy_table "$_lp_o")
    [ -n "$_lp_a" ] || { log "$1: phy events not measured (the second read has no phy table)"; return 0; }
    printf '%s\n---\n%s\n' "$_lp_b" "$_lp_a" | awk '
        $1 == "---" { second = 1; next }
        !second { b[$1] = $2 + 0; next }
        { if (!($1 in b)) next
          d = $2 - b[$1]; if (d < 0) bad = 1; t += d
          if (d > 0) ids = ids (ids == "" ? "" : ",") sprintf("\"%s\":%d", $1, d); seen++ }
        END { if (bad || !seen) exit 1; printf "{\"total\":%d,\"ids\":{%s}}", t, ids }' > "$_lp_j.new" &&
        mv "$_lp_j.new" "$_lp_j" || { rm -f "$_lp_j.new"; log "$1: phy events not measured (a counter fell or the tables did not match)"; }
    return 0
}

ls_phy_json() { if [ -s "$RUN_DIR/targets/$1/phy.json" ]; then tr -d '\n' < "$RUN_DIR/targets/$1/phy.json"; else printf 'null'; fi; }

ls_resets_refresh() {
    _lr_f="$RUN_DIR/senses/resets.now"; _lr_t=$(date +%s)
    _lr_at=$(_ss_num "$(cat "$RUN_DIR/senses/resets.at" 2>/dev/null)" 0)
    [ -s "$_lr_f" ] && [ $((_lr_t - _lr_at)) -lt "$SENSE_RESETS_EVERY_S" ] && return 0
    mkdir -p "$RUN_DIR/senses" 2>/dev/null || return 0
    "${DMESG:-dmesg}" > "$_lr_f.log" 2>/dev/null && [ -s "$_lr_f.log" ] || { rm -f "$_lr_f.log"; return 0; }   # a log that did not read is not "no resets"
    { echo ok
      awk '/hard resetting link|COMRESET failed|softreset failed|SRST failed/ {
               if (match($0, /ata[0-9]+(\.[0-9]+)?: /)) { p = substr($0, RSTART, RLENGTH); sub(/\..*/, "", p); sub(/:.*/, "", p); n[p]++ } }
           END { for (p in n) print p, n[p] }' "$_lr_f.log"; } > "$_lr_f.new" && mv "$_lr_f.new" "$_lr_f"
    rm -f "$_lr_f.log"; echo "$_lr_t" > "$RUN_DIR/senses/resets.at"
}

ls_resets_json() {
    # $1 = disk, $2 = its state now
    _lj_a=$(_hw_ata "$1")
    [ -n "$_lj_a" ] && [ "$(head -n 1 "$RUN_DIR/senses/resets.now" 2>/dev/null)" = ok ] || { printf 'null'; return 0; }
    _lj_n=$(awk -v k="ata$_lj_a" '$1 == k { print $2 }' "$RUN_DIR/senses/resets.now" 2>/dev/null); _lj_n=$(_ss_num "$_lj_n" 0)
    _lj_f="$RUN_DIR/targets/$1/ss.resets0"
    [ -e "$_lj_f" ] || [ "$2" != writing ] || echo "$_lj_n" > "$_lj_f"   # the baseline is taken at the first tick of the write, or not at all
    _lj_0=$(cat "$_lj_f" 2>/dev/null)
    case "$_lj_0" in ''|*[!0-9]*) printf '{"boot":%s,"delta":null}' "$_lj_n" ;;
        *) if [ "$_lj_n" -ge "$_lj_0" ]; then printf '{"boot":%s,"delta":%s}' "$_lj_n" "$((_lj_n - _lj_0))"; else printf '{"boot":%s,"delta":null}' "$_lj_n"; fi ;; esac
}

ls_conn_json() {
    _lc_a=$(_hw_ata "$1"); [ -n "$_lc_a" ] || { printf 'null'; return 0; }   # NVMe, USB, a VM's SCSI: no ATA connection
    _lc_d=null
    if [ "$(head -n 1 "$RUN_DIR/drawer_slots" 2>/dev/null)" = ok ]; then
        _lc_d=$(awk -F'|' -v k="sata:$_lc_a" 'NR > 1 && $2 == k { print $1; f = 1; exit } END { if (!f) print "\"unmapped\"" }' "$RUN_DIR/drawer_slots")
    fi
    printf '{"ata":%s,"sata":%s,"drawer":%s}' "$_lc_a" "$((_lc_a - 1))" "$_lc_d"
}
