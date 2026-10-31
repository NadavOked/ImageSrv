# postdeploy.sh -- driver staging into the restored Windows (#720;
# interfaces.md section 17). Runs after restore+grow and the hostname,
# before the reboot. POSIX sh (busybox ash).
#
# This is NOT an offline injection (no DISM from Linux): the packages the
# server matched to this machine's inventory are copied to
# \ImageSrv\Drivers\<package>\ on the restored NTFS, and that folder is
# added to DevicePath (SOFTWARE hive, REG_EXPAND_SZ -- hivewrite -x) so
# that Windows searches it on first boot when it meets a device without a
# driver. "staged" is not "installed". A storage driver Windows needs in
# order to boot at all: the in-box one is made boot-start by bootstorage.sh
# (#1331); a third-party one is still out of scope.
#
# Never fatal, never silent (עיקרון 5): the restore is complete and a
# machine that boots without its NIC driver is fixable in a minute. The
# outcome -- staged / no_match / skipped / failed <reason> -- is written to
# $RUN_DIR/drivers.json and travels in the final report (progress.sh), so
# "done, drivers not staged" is a state the console shows, not a lost log
# line. Every file's sha256 is checked twice: after the download, and again
# on the NTFS after the copy -- "copied" is what reads back, not cp's exit.

# #433: the UEFI Boot#### entry rides on the same hook (see below);
# bootentry.sh is loaded by imagesrv-agent, right before this file.
#
# #1269: a cloner's drawers go to OTHER machines, so its own inventory says
# nothing about them -- there is no "auto" for a drawer. The operator picks a
# model per target disk; stage_drawer_drivers (bottom) runs the same staging
# per drawer with DR_PORT/DR_SESSION/DR_OUT set: the server answers by the
# chosen model, the outcome goes to targets/<dev>/drivers.json, and the
# machine's top-level state is left alone.

DRIVERS_WIN_DIR="ImageSrv/Drivers"
DEVICE_PATH_KEY='Microsoft\Windows\CurrentVersion'
DEVICE_PATH_ENTRY='%SystemRoot%\..\ImageSrv\Drivers'

_drivers_result() {
    # $1 = state, $2 = JSON array body of package names ("" = none),
    # $3 = error/reason text (optional). Writes drivers.json and logs.
    # #1331: the boot-storage outcome (bootstorage.sh), when that step ran.
    _dr_out="${DR_OUT:-$RUN_DIR/drivers.json}"
    _boot=""; [ -n "${_dr_boot:-}" ] && _boot=",\"boot\":$_dr_boot"
    # #1331: the Linux initramfs check (linuxdrivers.sh), on a Linux target.
    [ -n "${_dr_linux:-}" ] && _boot="$_boot,\"linux\":$_dr_linux"
    if [ -n "${3:-}" ]; then
        _key="error"; [ "$1" = "skipped" ] && _key="reason"
        printf '{"state":"%s","packages":[%s],"%s":"%s"%s}\n' \
            "$1" "$2" "$_key" "$(json_escape "$3")" "$_boot" > "$_dr_out"
    else
        printf '{"state":"%s","packages":[%s]%s}\n' "$1" "$2" "$_boot" > "$_dr_out"
    fi
    log "drivers${DR_PORT:+ (drawer $DR_PORT)}: $(cat "$_dr_out")"
}

_drivers_fail() {
    # $1 = reason. Unmounts if a mount is open, records, returns 0 (never fatal).
    [ -n "${_dr_mnt:-}" ] && { umount "$_dr_mnt" >> "$LOG_FILE" 2>&1; _dr_mnt=""; }
    _drivers_result "failed" "" "$1"
    return 0
}

_file_sha256() {
    # $1 = file. The digest, or nothing when it cannot be computed.
    sha256sum "$1" 2>/dev/null | awk '{ print $1 }'
}

_drivers_fetch() {
    # Downloads every file of every matched package into $RUN_DIR/drivers/,
    # verifying sha256 as it goes. Reads the rows file ($1): name|path|sha|url.
    # Returns 1 with the reason in $_dr_why.
    while IFS='|' read -r _pkg _path _sha _url; do
        case "$_pkg" in ''|*/*|*..*|.*) _dr_why="שם חבילה לא תקין: '$_pkg'"; return 1 ;; esac
        case "$_path" in ''|/*|*..*|*\\*) _dr_why="נתיב קובץ לא תקין '$_path' בחבילה $_pkg"; return 1 ;; esac
        _dst="$RUN_DIR/drivers/$_pkg/$_path"
        mkdir -p "${_dst%/*}"
        if ! http_get_stream "$SERVER$_url" > "$_dst" 2>> "$LOG_FILE"; then
            _dr_why="ההורדה נכשלה: $_pkg/$_path"; return 1
        fi
        _got=$(_file_sha256 "$_dst")
        if ! is_sha256 "$_got"; then
            _dr_why="ה-sha256 לא חושב: $_pkg/$_path"; return 1
        elif [ "$_got" != "$_sha" ]; then
            _dr_why="אי-התאמת sha256: $_pkg/$_path"; return 1
        fi
    done < "$1"
    return 0
}

_drivers_copy() {
    # $1 = mount point, $2 = rows file. Copies each package folder onto the
    # NTFS and reads every file back against the manifest's sha256.
    while IFS='|' read -r _pkg _path _sha _url; do
        _to="$1/$DRIVERS_WIN_DIR/$_pkg/$_path"
        mkdir -p "${_to%/*}" 2>> "$LOG_FILE" || { _dr_why="mkdir נכשל בדיסק: $_pkg"; return 1; }
        if ! cp "$RUN_DIR/drivers/$_pkg/$_path" "$_to" 2>> "$LOG_FILE"; then
            _dr_why="ההעתקה נכשלה: $_pkg/$_path"; return 1
        fi
        if [ "$(_file_sha256 "$_to")" != "$_sha" ]; then
            _dr_why="אי-התאמה בקריאה חזרה מהדיסק: $_pkg/$_path"; return 1
        fi
    done < "$2"
    return 0
}

_device_path_add() {
    # $1 = SOFTWARE hive. Appends the drivers folder to DevicePath, keeping
    # what is there ("%SystemRoot%\inf" at least), as REG_EXPAND_SZ -- the
    # value's own type, without which %SystemRoot% is not expanded. Idempotent.
    _cur=$(hivewrite -g "$1" "$DEVICE_PATH_KEY" DevicePath 2>> "$LOG_FILE")
    _grc=$?
    if [ "$_grc" -ne 0 ]; then _dr_why="לא הצלחנו לקרוא את DevicePath (rc=$_grc)"; return 1; fi
    case "$_cur" in
        *ImageSrv\\Drivers*) log "DevicePath already lists ImageSrv\\Drivers"; return 0 ;;
        '') _new="$DEVICE_PATH_ENTRY" ;;
        *)  _new="$_cur;$DEVICE_PATH_ENTRY" ;;
    esac
    hivewrite -x "$1" "$DEVICE_PATH_KEY" DevicePath "$_new" >> "$LOG_FILE" 2>&1
    _wrc=$?
    _back=$(hivewrite -g "$1" "$DEVICE_PATH_KEY" DevicePath 2>> "$LOG_FILE")
    if [ "$_wrc" -ne 0 ] || [ "$_back" != "$_new" ]; then
        _dr_why="כתיבת DevicePath לא אומתה (rc=$_wrc, נקרא בחזרה '$_back')"; return 1
    fi
    log "DevicePath = $_new"
    return 0
}

stage_drivers() {
    # $1 = disk name, $2 = manifest file. Always returns 0, and always leaves
    # the top-level state non-terminal; the caller writes done only after every
    # postdeploy step and the incomplete-write mark have finished (#1935).
    # #433: the boot entry comes after the drivers, on the same hook -- both
    # restore paths (the round, single_restore.sh) pass through here.
    _stage_drivers "$@"
    ensure_boot_entry "$@"
    return 0
}

_stage_drivers() {
    _dr_mnt=""; _dr_why=""; _dr_boot=""; _dr_linux=""
    rm -f "${DR_OUT:-$RUN_DIR/drivers.json}"
    _dr_plan="$RUN_DIR/drivers.plan"
    if ! manifest_plan "$2" > "$_dr_plan"; then
        _drivers_fail "לא הצלחנו לקרוא את תוכנית המחיצות"
        return 0
    fi
    grep -qE '^[^|]*\|[^|]*\|windows\|' "$_dr_plan"; _dr_plan_rc=$?
    if [ "$_dr_plan_rc" -eq 1 ]; then
        # #1331: a Linux target is checked, never changed (linuxdrivers.sh).
        grep -qE '^[^|]*\|[^|]*\|linux\|' "$_dr_plan"
        case $? in
            0) linux_initramfs_check "$1" "$2" "$_dr_plan" ;;
            1) ;;
            *) _dr_linux='{"state":"failed","error":"לא הצלחנו לבדוק את תוכנית המחיצות"}' ;;
        esac
        _drivers_result "skipped" "" "not_windows"
        return 0
    fi
    if [ "$_dr_plan_rc" -ne 0 ]; then
        _drivers_fail "לא הצלחנו לבדוק את תוכנית המחיצות"
        return 0
    fi
    _q="mac=$MAC"
    if [ -n "${DR_PORT:-}" ]; then _q="$_q&session=$DR_SESSION&port=$DR_PORT"
    else echo "staging" > "$RUN_DIR/state"; fi

    _ans="$RUN_DIR/drivers.answer"
    if ! http_get "$SERVER/api/v1/agent/drivers?$_q" > "$_ans" 2>> "$LOG_FILE"; then
        _drivers_fail "השרת לא ענה לשאילתת הדרייברים"; return 0
    fi
    [ "$(json_get "$_ans" ".ok")" = "true" ] || { _drivers_fail "לא הצלחנו לקרוא את תשובת הדרייברים"; return 0; }
    if [ -n "${DR_PORT:-}" ]; then
        # #1269: no model for this port is a skip; a model with no package is a failure (below).
        [ "$(json_get "$_ans" ".chosen")" != "null" ] || { _drivers_result "skipped" "" "no_model"; return 0; }
    # עיקרון 5: "אין מלאי בשרת" אינו "אין חבילה תואמת" -- הראשון הוא כשל.
    elif [ "$(json_get "$_ans" ".inventory")" != "true" ]; then
        _drivers_fail "לשרת אין מלאי של המחשב הזה"; return 0
    fi

    _rows="$RUN_DIR/drivers.rows"
    json_get "$_ans" '.packages[] as $p | $p.files[] | "\($p.name)|\(.path)|\(.sha256)|\(.url)"' > "$_rows"
    _names=$(json_get "$_ans" '.packages[].name')
    case "$_names" in
        null) _drivers_fail "לא הצלחנו לקרוא את רשימת החבילות"; return 0 ;;
        '')   if [ -n "${DR_PORT:-}" ]; then _drivers_fail "לשרת אין חבילת דרייברים לדגם שנבחר"
              else _drivers_result "no_match" ""; fi; return 0 ;;
    esac
    _list=""
    for _n in $_names; do _list="$_list${_list:+,}\"$(json_escape "$_n")\""; done
    log "drivers: staging $(echo "$_names" | tr '\n' ' ')"

    rm -rf "$RUN_DIR/drivers"
    _drivers_fetch "$_rows" || { _drivers_fail "$_dr_why"; return 0; }

    _dr_mnt=$(_mount_windows "$1" "$2" "$_dr_plan") || {
        _dr_mnt=""; _drivers_fail "לא הצלחנו לעגן את מחיצת Windows"; return 0
    }
    _hive="$_dr_mnt/Windows/System32/config/SOFTWARE"
    [ -f "$_hive" ] || { _drivers_fail "כוורת SOFTWARE לא נמצאה"; return 0; }
    for _n in $_names; do rm -rf "${_dr_mnt:?}/${DRIVERS_WIN_DIR:?}/$_n"; done   # SC2115: לעולם לא "/"
    _drivers_copy "$_dr_mnt" "$_rows" || { _drivers_fail "$_dr_why"; return 0; }
    _device_path_add "$_hive" || { _drivers_fail "$_dr_why"; return 0; }
    # #1331: only once the packages are on the disk -- every earlier failure
    # leaves the disk untouched. Never fails the staging: "staged" with
    # boot.state "failed" is what gets reported.
    boot_storage_enable "$1" "$_dr_mnt"
    _umount_checked "$_dr_mnt"
    _umrc=$?
    _dr_mnt=""
    [ "$_umrc" -eq 0 ] || { _drivers_fail "לא הצלחנו לנתק את המחיצה אחרי הנחת הדרייברים (rc=$_umrc)"; return 0; }

    _drivers_result "staged" "$_list"
    return 0
}

stage_drawer_drivers() {
    # #1269: $1 = session id, $2 = manifest, $3.. = this wave's drawers. Only
    # the ports hello listed in session.driver_ports, and only a drawer
    # written in full (target state "done"). No list = nothing is asked: the
    # round without drivers is the round it was. Always returns 0.
    _dd_ses="$1"; _dd_man="$2"; shift 2
    _dd_ports=$(json_get "$RESP" '.session.driver_ports // [] | .[]' | tr '\n' ' ')
    case "$_dd_ports" in ''|' '|null*) return 0 ;; esac
    for _dd in "$@"; do
        [ "$(cat "$RUN_DIR/targets/$_dd/state" 2>/dev/null)" = "done" ] || continue
        _dd_port=$(disk_port "$_dd")
        case " $_dd_ports" in *" $_dd_port "*) ;; *) continue ;; esac   # tr left one trailing space
        DR_PORT="$_dd_port"; DR_SESSION="$_dd_ses"; DR_OUT="$RUN_DIR/targets/$_dd/drivers.json"
        _stage_drivers "$_dd" "$_dd_man"
    done
    DR_PORT=""; DR_SESSION=""; DR_OUT=""
    return 0
}
