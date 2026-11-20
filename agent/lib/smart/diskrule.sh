# diskrule.sh -- #3666 (אפיק #3653, הכרעת נדב 18/11): the disk's colour from THE table, on a machine that has no brain to ask.
# POSIX sh (busybox ash) + awk.
#
# A disk's colour, whether it may be copied from (source) and written to (target), and why, are decided by the brain on the
# server (server/brain/diskcolor.py, K3/DISK_COLOR); the console and the room screens show what it sent. A machine WITHOUT a
# server -- the local clone (no server by design) and the cloner's waiting screen, which hold the facts of their own drawers --
# has nobody to ask. This file applies the same table to those facts and hands the GUI the verdict, so the GUI works nothing out.
#
# It decides nothing of its own: agent/lib/data/disk-rule.tsv is the one source (the server's disk_rule.py reads the same file;
# tests/test_disk_rule_agent_3666.py runs both over every combination of the facts and wants the same answer). The table is
# group (c) of #3653: a fixed policy and hard safety -- a red disk is not written -- so nothing here may soften it.
# Not read (no table, a broken row, no default row) = nothing is printed and the GUI says "not checked": never green (principle 5).

# Not under SMART_KB: a USB stick points that at its server-synced copy of the two SMART tables (usbsync.sh), which has no disk-rule.tsv --
# this one is part of the build (build_initramfs.sh packs it next to the libs), so the stick's own initramfs has it too.
DISK_RULE_TABLE="${DISK_RULE_TABLE:-${LIB_DIR:-/usr/lib/imagesrv}/data/disk-rule.tsv}"

disk_rule_judge() {
    # $1=smart (ok|warn|fail|failed_last|unchecked; anything else = not read)  $2..$5 = failed_last write_incomplete sata_issue write_slow
    # (1 = true). Prints color|source|target|reason|source_reason|target_reason|sentence -- source/target 1 = allowed, `reason` the
    # colour's own (red first, then yellow), `sentence` its words, from the table. 1 = the table could not be read (nothing printed).
    [ -s "$DISK_RULE_TABLE" ] || return 1
    awk -F '\t' -v smart="$1" -v fl="$2" -v wi="$3" -v si="$4" -v ws="$5" '
        BEGIN { F["smart"] = smart; F["failed_last"] = fl; F["write_incomplete"] = wi; F["sata_issue"] = si; F["write_slow"] = ws }
        function holds(fact,   n, alt, i, p) {
            n = split(fact, alt, "|")
            for (i = 1; i <= n; i++) {
                if (alt[i] == "*") return 1
                p = index(alt[i], "=")
                if (p ? F[substr(alt[i], 1, p - 1)] == substr(alt[i], p + 1) : F[alt[i]] == "1") return 1
            }
            return 0
        }
        /^#/ || $0 == "" { next }
        NF != 6 { bad = 1; next }
        { last1 = $1; last3 = $3 }
        !($1 in W) && holds($3) { W[$1] = $2; C[$1] = $4; HE[$1] = $6 }
        END {
            # a broken row, or a table whose last row is not the "not checked" default (smart, fact *): no verdict -- as disk_rule.py refuses it
            if (bad || last1 != "smart" || last3 != "*" || !("smart" in W)) exit 1
            color = ("red" in W) ? "red" : ("yellow" in W) ? "yellow" : C["smart"]
            print color "|" (("yellow" in W) ? 0 : 1) "|" (("red" in W) ? 0 : 1) "|" (("red" in W) ? W["red"] : ("yellow" in W) ? W["yellow"] : "") \
                "|" (("yellow" in W) ? W["yellow"] : "") "|" (("red" in W) ? W["red"] : "") "|" (("red" in W) ? HE["red"] : ("yellow" in W) ? HE["yellow"] : "")
        }' "$DISK_RULE_TABLE"
}

disk_rule_record() {
    # A GUI state record for one drawer: <record>=<dev>|color|source|target|reason|sentence -- the GUI takes it as delivered.
    # $1=record name  $2=dev  $3=smart ("passed" = ok)  $4=write mark (incomplete = a cut write)  $5=the drawer's hw words,
    # space-separated (link_reset / drawer_gone = a SATA problem)  $6=the waiting screen's write check (slow = slow in it).
    # The table not readable = no record: the GUI says "not checked" (never green).
    case "$3" in passed) _dr_sm=ok ;; ok|warn|fail|failed_last) _dr_sm=$3 ;; *) _dr_sm=unchecked ;; esac
    _dr_mk=0; [ "$4" = incomplete ] && _dr_mk=1
    _dr_sa=0; case " $5 " in *" link_reset "*|*" drawer_gone "*) _dr_sa=1 ;; esac
    _dr_ws=0; [ "${6:-}" = slow ] && _dr_ws=1
    _dr_out=$(disk_rule_judge "$_dr_sm" 0 "$_dr_mk" "$_dr_sa" "$_dr_ws") || return 0
    printf '%s=%s|%s\n' "$1" "$2" "$_dr_out"
}

disk_rule_lc_records() {
    # lc_rule= for every drawer the local clone inventoried ($LC/inv/<dev>.line = port|dev|model|serial|size|smart|..., its mark in
    # <dev>.mark; lcinv.sh). A local clone measures no round's write check: slow is 0.
    for _dr_f in "$LC"/inv/*.line; do
        [ -f "$_dr_f" ] || continue
        IFS='|' read -r _dr_p _dr_d _dr_m _dr_s _dr_z _dr_sm _dr_x < "$_dr_f" || :
        [ -n "$_dr_d" ] || continue
        disk_rule_record lc_rule "$_dr_d" "$_dr_sm" "$(cat "$LC/inv/$_dr_d.mark" 2>/dev/null)" \
            "$(command -v hw_words >/dev/null 2>&1 && hw_words "$_dr_d" | tr '\n|' '  ')" ""
    done
}
