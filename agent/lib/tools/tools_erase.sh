# tools_erase.sh -- additional catalog erasers (#1104). POSIX sh.
# tools_disk.sh supplies the same device and named-confirmation gates.

tools_erase_list() {
    cat <<'EOF'
nvme-sanitize|disk|מחיקה מלאה בבקר NVMe (Block Erase)|destroy|/dev/nvmeXn1
nvme-sanitize-status|disk|מצב מחיקת NVMe|ro|/dev/nvmeXnY
zap-partition-table|disk|מחיקת טבלת מחיצות GPT/MBR|destroy|/dev/sdX
nwipe|disk|מחיקה בשלושה מעברים ואימות (DoD short)|destroy|/dev/sdX
EOF
}

_te_controller() {
    _ten=${_arg#/dev/nvme}; _ten=${_ten%%n*}
    case "$_ten" in ''|*[!0-9]*) return 2 ;; esac
    _tec="/dev/nvme$_ten"
    _tens=${_arg#"${_tec}n"}
    case "$_tens" in ''|*[!0-9]*) return 2 ;; esac
    [ -d "${SYSROOT:-}/sys/class/nvme/${_tec#/dev/}/${_arg#/dev/}" ] || return 2
}

_te_sanitize_status() {
    # Raw log: little-endian SPROG and SSTAT; JSON changed between nvme-cli releases.
    _telog="$RUN_DIR/tool-$_id.sanitize-log"
    nvme sanitize-log "$_tec" --raw-binary > "$_telog" || return 2
    _telen=$(wc -c < "$_telog") || return 2
    [ "$_telen" -eq 512 ] || { printf 'Incomplete sanitize log (%s bytes).\n' "$_telen"; return 2; }
    _tebytes=$(od -An -tu1 -N4 "$_telog") || return 2
    set -- $_tebytes
    [ "$#" -eq 4 ] || return 2
    _testat=$(($3 & 7)); _teprogress=$(($1 + 256 * $2))
    printf 'Sanitize status=%s, progress=%s/65535 (%s).\n' "$_testat" "$_teprogress" "$_tec"
    case "$_testat" in
        1|4) printf 'Sanitize completed successfully.\n'; return 0 ;;
        3) printf 'Sanitize failed. The disk is not clean.\n'; return 1 ;;
        2) printf 'Sanitize is still running; check nvme-sanitize-status again.\n'; return 2 ;;
        *) printf 'No successful sanitize completion was reported.\n'; return 2 ;;
    esac
}

_te_sanitize() {
    _te_controller || { printf 'Cannot identify the selected NVMe controller.\n'; return 2; }
    _tectrl="$RUN_DIR/tool-$_id.id-ctrl"
    nvme id-ctrl "$_tec" --raw-binary > "$_tectrl" || return 2
    _telen=$(wc -c < "$_tectrl") || return 2
    [ "$_telen" -eq 4096 ] || { printf 'Incomplete controller identity.\n'; return 2; }
    _tecmic=$(od -An -tu1 -j76 -N1 "$_tectrl") || return 2
    _tenn=$(od -An -tu1 -j516 -N4 "$_tectrl") || return 2
    set -- $_tenn
    # Sanitize covers the subsystem, not just the selected namespace.
    if [ "$_tens" != 1 ] || [ "$_tecmic" -ne 0 ] || [ "$*" != '1 0 0 0' ]; then
        printf 'Refused: sanitize requires one namespace and a single-port, single-controller device.\n'
        return 2
    fi
    printf 'Block erase of %s (only namespace: %s).\n' "$_tec" "$_arg"
    nvme sanitize "$_tec" --sanact=2 </dev/null || return 1
    printf 'Sanitize command accepted; checking completion.\n'
    _te_sanitize_status
}

_te_nwipe() {
    _tenlog="$RUN_DIR/tool-nwipe.log"
    : > "$_tenlog" || return 2
    nwipe --autonuke --nogui --nowait --method=dodshort --rounds=1 --verify=all \
        --pdfreportpath=noPDF --logfile="$_tenlog" "$_arg" </dev/null || return 1
    # nwipe can exit zero after an abort: require its positive completion report too.
    grep -F 'Nwipe successfully completed.' "$_tenlog" || {
        printf 'No successful nwipe completion report.\n'; return 2;
    }
}

tools_erase_run() {
    _id=${1:-}; _arg=${2:-}; _confirm=${3:-}
    _out="${RUN_DIR:?}/tool-$_id.out"
    case "$_id" in
        nvme-sanitize|nvme-sanitize-status) _tebin=nvme ;;
        zap-partition-table) _tebin=sgdisk ;;
        nwipe) _tebin=nwipe ;;
        *) return 4 ;;
    esac
    : > "$_out" || return 2
    _tools_disk_prep "$_tebin" || return 2
    if [ "$_id" != nvme-sanitize-status ]; then
        _tools_disk_destroy_gate "$_out" "$_id on $_arg" "$_confirm" || return 3
    fi
    {
        case "$_id" in
            nvme-sanitize) _te_sanitize ;;
            nvme-sanitize-status) _te_controller && _te_sanitize_status ;;
            zap-partition-table) sgdisk --zap-all "$_arg" </dev/null ;;
            nwipe) _te_nwipe ;;
        esac
        _terc=$?
        case "$_terc" in
            0) _tools_disk_means 'הפעולה הושלמה לפי דיווח הכלי.' ;;
            *) _tools_disk_means "הפעולה לא הושלמה או לא אומתה (rc $_terc). אין לראות בדיסק נקי." ;;
        esac
        case "$_terc" in 0|2) return "$_terc" ;; *) return 1 ;; esac
    } >> "$_out" 2>&1
}
