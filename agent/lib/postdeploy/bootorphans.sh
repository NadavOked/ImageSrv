# bootorphans.sh -- dead UEFI Boot#### entries after a restore or a clone (#2231).
# POSIX sh (busybox ash). Loaded right after bootentry.sh.
#
# A restored or cloned disk brings its own GPT PARTUUIDs; the NVRAM of the board
# still holds entries for the disk that used to be there. The firmware tries
# them (a delay, "no boot device", a menu) -- #2231.
#
# An entry is DEAD only on positive evidence: its device path is HD(n,GPT,<uuid>,..)
# and <uuid> is the PARTUUID of NO partition the kernel lists, while every
# partition was read (a partition blkid could not read, no efivars, no partitions
# at all, a failed `efibootmgr -v` = "not checked", never "all dead" -- principle 5).
# PXE, USB, network, MBR and file-less entries are never candidates.
#
# bootorphans_scan   READ ONLY. Writes $RUN_DIR/bootorphans.json:
#                    {"state":"found","entries":[{"num","partuuid","label"}]} | "none"
#                    | {"state":"unchecked","reason"} | {"state":"skipped","reason":"no_efivars"}
#                    Always returns 0; called from stage_drivers (postdeploy.sh).
# bootorphans_clean  NUM...  deletes ONLY the numbers it is given -- the approval is the
#                    argument; no argument deletes nothing (rc 3). Each number is checked
#                    against a FRESH scan (never trusted), all-or-nothing. After the delete
#                    the table is read back: the numbers must be gone, BootOrder must be
#                    what it was minus them (PXE stays first -- never reordered), and no
#                    other Boot#### may have changed. Writes $RUN_DIR/bootorphans-clean.json.
#                    rc 0 = cleaned and read back, 3 = refused (nothing deleted), 1 = failed.
# The approval transport (machine card, console) is not here: it waits for a mockup.

_bo_say() {
    # $1 = file, $2 = state, $3 = extra JSON members (leading comma) or empty
    printf '{"state":"%s"%s}\n' "$2" "$3" > "$RUN_DIR/$1"
    log "boot orphans ($1): $(cat "$RUN_DIR/$1")"
}

_bo_efivars() {
    _bo_ev="${SYSROOT:-}/sys/firmware/efi/efivars"
    [ -d "$_bo_ev" ] && [ -n "$(ls -A "$_bo_ev" 2>/dev/null)" ]
}

_bo_partuuids() {
    # The PARTUUID (lower case) of every partition listed. blkid rc 2 = the partition has none;
    # any other rc = not read -> the whole answer is "not checked".
    for _bp in "${SYSROOT:-}"/sys/class/block/*/partition; do
        [ -e "$_bp" ] || continue
        _bn=${_bp%/partition}; _bn=${_bn##*/}
        _bv=$(blkid -s PARTUUID -o value "$DEVROOT/$_bn" 2>> "$LOG_FILE"); _brc=$?
        case "$_brc" in 0) printf '%s\n' "$_bv" | tr 'A-F' 'a-f' ;; 2) ;; *) return 1 ;; esac
    done
}

_bo_find() {
    # Fills $RUN_DIR/bootorphans.list with "NUM|partuuid|label" of every dead entry.
    # rc 0 = checked (the list may be empty); rc 1 = not checked, the reason in $_bo_why.
    : > "$RUN_DIR/bootorphans.list" || { _bo_why="cannot write $RUN_DIR"; return 1; }
    _bo_tab=$(efibootmgr -v 2>> "$LOG_FILE"); _bo_rc=$?
    if [ "$_bo_rc" -ne 0 ] || [ -z "$_bo_tab" ]; then _bo_why="efibootmgr -v rc=$_bo_rc"; return 1; fi
    _bo_have=$(_bo_partuuids) || { _bo_why="a partition PARTUUID could not be read"; return 1; }
    [ -n "$_bo_have" ] || { _bo_why="no partition with a PARTUUID"; return 1; }
    printf '%s\n' "$_bo_tab" | awk '
        /^Boot[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]/ {
            num = toupper(substr($0, 5, 4)); rest = substr($0, 9); sub(/^\*? */, "", rest)
            t = index(rest, "\t"); label = (t ? substr(rest, 1, t - 1) : rest); path = (t ? substr(rest, t + 1) : "")
            gsub(/\|/, " ", label)
            if (match(path, /HD\([0-9]+,GPT,[0-9A-Fa-f-]+,/)) {
                n = split(substr(path, RSTART, RLENGTH), a, ","); print num "|" tolower(a[3]) "|" label
            }
        }' > "$RUN_DIR/bootorphans.cand" || { _bo_why="awk rc=$?"; return 1; }
    while IFS='|' read -r _bn _bu _bl; do
        [ -n "$_bn" ] || continue
        printf '%s\n' "$_bo_have" | grep -qxF "$_bu"; _bg=$?
        case "$_bg" in 0) continue ;; 1) ;; *) _bo_why="grep rc=$_bg"; return 1 ;; esac
        printf '%s|%s|%s\n' "$_bn" "$_bu" "$_bl" >> "$RUN_DIR/bootorphans.list"
    done < "$RUN_DIR/bootorphans.cand"
    return 0
}

bootorphans_scan() {
    rm -f "$RUN_DIR/bootorphans.json"
    _bo_efivars || { _bo_say bootorphans.json skipped ',"reason":"no_efivars"'; return 0; }
    _bo_why=""
    _bo_find || { _bo_say bootorphans.json unchecked ",\"reason\":\"$(json_escape "$_bo_why")\""; return 0; }
    if [ ! -s "$RUN_DIR/bootorphans.list" ]; then _bo_say bootorphans.json none ""; return 0; fi
    _bo_items=""
    while IFS='|' read -r _bn _bu _bl; do
        _bo_items="${_bo_items:+$_bo_items,}{\"num\":\"$_bn\",\"partuuid\":\"$_bu\",\"label\":\"$(json_escape "$_bl")\"}"
    done < "$RUN_DIR/bootorphans.list"
    _bo_say bootorphans.json found ",\"entries\":[$_bo_items]"
    return 0
}

_bo_nums() {
    # Every Boot#### number, in the order efibootmgr prints them; rc 1 = unreadable.
    _bt=$(efibootmgr 2>> "$LOG_FILE") || return 1
    printf '%s\n' "$_bt" | sed -n 's/^Boot\([0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]\).*/\1/p' | tr 'a-f' 'A-F' | tr '\n' ' '
}

_bo_order() {
    _bt=$(efibootmgr 2>> "$LOG_FILE") || return 1
    printf '%s\n' "$_bt" | sed -n 's/^BootOrder: *//p' | tr -d ' \r' | tr 'a-f' 'A-F'
}

_bo_minus() {
    # $1 = items separated by "$2" (space or comma), $3 = the numbers to drop (" A B "). Same separator out.
    _bm=""; _bi=$IFS; IFS=$2
    for _bmx in $1; do
        case " $3 " in *" $_bmx "*) continue ;; esac
        _bm="${_bm:+$_bm$2}$_bmx"
    done
    IFS=$_bi; printf '%s' "$_bm"
}

_bo_clean_check() {
    # After the delete: the numbers are gone, BootOrder = before minus them, nothing else moved.
    _bo_na=$(_bo_nums) || { _bo_err="efibootmgr could not be read back"; return 1; }
    _bo_oa=$(_bo_order) || { _bo_err="BootOrder could not be read back"; return 1; }
    for _bx in $_bo_want; do
        case " $_bo_na" in *" $_bx "*) _bo_err="Boot$_bx is still in the table after efibootmgr -B"; return 1 ;; esac
    done
    [ "$_bo_oa" = "$(_bo_minus "$_bo_ob" , "$_bo_want")" ] || { _bo_err="BootOrder is '$_bo_oa', expected '$(_bo_minus "$_bo_ob" , "$_bo_want")' (it must only lose the deleted entries)"; return 1; }
    [ "$(_bo_minus "$_bo_nb" ' ' "$_bo_want")" = "$(_bo_minus "$_bo_na" ' ' '')" ] || { _bo_err="another Boot#### changed: before ' $_bo_nb', after ' $_bo_na'"; return 1; }
    return 0
}

bootorphans_clean() {
    rm -f "$RUN_DIR/bootorphans-clean.json"
    [ $# -gt 0 ] || { _bo_say bootorphans-clean.json refused ',"reason":"no_approval"'; return 3; }
    _bo_want=""
    for _ba in "$@"; do
        case "$_ba" in [0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]) ;; *) _bo_say bootorphans-clean.json refused ",\"reason\":\"bad_number: $(json_escape "$_ba")\""; return 3 ;; esac
        _bn=$(printf '%s' "$_ba" | tr 'a-f' 'A-F')
        case "$_bo_want " in *" $_bn "*) ;; *) _bo_want="$_bo_want $_bn" ;; esac   # a number twice would be a failed second delete
    done
    _bo_want="$_bo_want "
    _bo_efivars || { _bo_say bootorphans-clean.json refused ',"reason":"no_efivars"'; return 3; }
    _bo_why=""
    _bo_find || { _bo_say bootorphans-clean.json refused ",\"reason\":\"not checked: $(json_escape "$_bo_why")\""; return 3; }
    for _bx in $_bo_want; do
        grep -q "^$_bx|" "$RUN_DIR/bootorphans.list"; _bg=$?
        [ "$_bg" -eq 0 ] || { _bo_say bootorphans-clean.json refused ",\"reason\":\"Boot$_bx is not a dead entry (grep rc=$_bg)\""; return 3; }
    done
    _bo_nb=$(_bo_nums) && _bo_ob=$(_bo_order) || { _bo_say bootorphans-clean.json refused ',"reason":"the table could not be read before deleting"'; return 3; }
    _bo_err=""
    for _bx in $_bo_want; do
        efibootmgr -B -b "$_bx" >> "$LOG_FILE" 2>&1; _bc=$?
        [ "$_bc" -eq 0 ] || { _bo_err="efibootmgr -B -b $_bx rc=$_bc"; break; }
    done
    [ -n "$_bo_err" ] || _bo_clean_check
    _bo_json=""; for _bx in $_bo_want; do _bo_json="${_bo_json:+$_bo_json,}\"$_bx\""; done
    if [ -n "$_bo_err" ]; then
        _bo_say bootorphans-clean.json failed ",\"error\":\"$(json_escape "$_bo_err")\",\"asked\":[$_bo_json]"; return 1
    fi
    _bo_say bootorphans-clean.json cleaned ",\"deleted\":[$_bo_json]"
    return 0
}
