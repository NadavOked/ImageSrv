# hostname.sh -- writes the computer name into the restored system before
# its first boot (interfaces.md section 5). Windows and Linux alike.
# POSIX sh (busybox ash).
#
# Windows: offline registry editing, not a running system. The SYSTEM hive is
# mounted from the restored NTFS partition and three values are set.
# "CurrentControlSet" does not exist offline -- it is a runtime alias -- so
# the real control set is read from Select\Current first. Editing the wrong
# one silently does nothing.
#
# Linux: hostname_linux.sh (/etc/hostname and /etc/hosts). A dual-boot disk
# is named on both sides (#107).

HOSTNAME_METHOD="offline-registry"

_mount_windows() {
    # $1 = disk, $2 = manifest, $3 = an already verified plan (optional).
    # Echoes the mount point, or fails. ‏#876: הקורא
    # לוכד את הפלט ב-`$( )`, ולכן כל `log` כאן (ובכל עוזרת של write_hostname)
    # הולך ל-stderr -- אחרת שורת היומן נכנסת לערך.
    if [ -n "${3:-}" ]; then
        _mount_plan="$3"
    else
        _mount_plan="$RUN_DIR/mount-windows.plan"
        manifest_plan "$2" > "$_mount_plan" || return 1
    fi
    _idxs=$(awk -F'|' '$3 == "windows" { print $1 }' "$_mount_plan") || return 1
    [ -n "$_idxs" ] || { log "no windows partition in the manifest" >&2; return 1; }
    _mnt="$RUN_DIR/win"
    mkdir -p "$_mnt"
    # ‏#1369: מניפסט שנקלט לפני התיקון מסמן כל מחיצת Basic Data כ-windows —
    # גם מחיצת OEM/נתונים שיושבת לפני C:, ו"הראשונה" היא אז מחיצה בלי
    # מערכת. נבחרת זו ש-Windows\System32 יושבת בה; אין כזו — הראשונה, כמו
    # קודם, והקורא אומר בשמו מה חסר (no_hive). מניפסט חדש: מועמדת אחת.
    # ‏#1875: מחיצת המערכת (system_partition, diskfit.sh — הכלל של min-free)
    # נבדקת ראשונה; עד כאן מחיצת ה-OEM הקטנה עוגנה לכתיבה לפני C:.
    # לא נקבעה (מניפסט לא נקרא) = הסדר הקודם; הראיה למטה מכריעה בכל מקרה.
    _sys=$(system_partition "$2") || { log "the system partition could not be read from $2" >&2; _sys=""; }
    _rest=$(printf '%s\n' "$_idxs" | awk -v s="$_sys" '$1 != s')
    [ "$_rest" = "$_idxs" ] || _idxs="$_sys
$_rest"
    _first=""
    for _idx in $_idxs; do
        [ -n "$_first" ] || _first="$_idx"
        _mount_windows_one "$1" "$_idx" || return 1
        [ -d "$_mnt/Windows/System32" ] && { echo "$_mnt"; return 0; }
        log "partition $_idx: no Windows\\System32 -- not the system partition" >&2
        _umount_checked "$_mnt" >&2 || return 1
    done
    _mount_windows_one "$1" "$_first" || return 1
    echo "$_mnt"
}

_mount_windows_one() {
    # $1 = disk, $2 = partition index; mounts it on $_mnt.
    _node=$(partition_node "$1" "$2")
    # remove_hiberfile: an image captured from a hibernated machine leaves the
    # volume "dirty", and ntfs-3g would mount it read-only.
    if ! ntfs-3g -o remove_hiberfile "$_node" "$_mnt" >> "$LOG_FILE" 2>&1; then
        log "could not mount $_node" >&2
        return 1
    fi
}

_umount_checked() {
    # $1 = mount point. Flushes and unmounts, returning umount's exit code.
    # A disk left mounted after a successful write is NOT success -- the next
    # stage would run against a live mount, and "wrote the name" is a
    # different state from "wrote it and released the disk" (עיקרון 5). Any
    # non-zero umount is a real failure; there is no "1 is an answer" case.
    sync
    umount "$1" >> "$LOG_FILE" 2>&1
    _umrc=$?
    [ "$_umrc" -eq 0 ] || log "umount of $1 failed (rc=$_umrc) -- disk left mounted" >&2
    return "$_umrc"
}

_control_set() {
    # $1 = hive path. Echoes e.g. ControlSet001 on stdout; returns 1 without
    # echoing when Select\Current cannot be read. הקריאה דרך hivewrite -g
    # ולא hivexget — זה wrapper ל-hivexsh שלא רץ ב-initramfs כלל (#33).
    # עיקרון 5 (#505): כישלון קריאה אינו ControlSet001 בשקט. מבחינים בין
    # "נקרא" (כל ערך מספרי — 1, 2, ...) לבין "לא נקרא" (יציאה לא-אפסית או
    # פלט ריק), והשני הוא כשל גלוי — אחרת כתיבה לסט־הבקרה הלא-פעיל ואימות
    # שמאשר את ההנחה של עצמו. ה-`| tr` הישן בלע גם את קוד היציאה.
    _raw=$(hivewrite -g "$1" Select Current 2>>"$LOG_FILE")
    _crc=$?
    _current=$(printf '%s' "$_raw" | tr -dc '0-9')
    if [ "$_crc" -ne 0 ] || [ -z "$_current" ]; then
        log "could not read Select\\Current (rc=$_crc, raw='$_raw')" >&2
        return 1
    fi
    printf 'ControlSet%03d' "$_current"
}

write_hostname() {
    # $1 = disk name, $2 = manifest file, $3 = hostname.
    # Emits the section 5 result object on stdout.
    _disk="$1"; _manifest="$2"; _name="$3"

    case "$_name" in
        *[!A-Za-z0-9-]*|""|-*|*-)
            # #1977: the name and the rule. #1997: the same rule as server/hostname_rule.py
            # (a test compares them) -- a name registered before it ("עמדה 3") lands here.
            printf '{"ok":false,"error":"%s","code":"bad_hostname"}\n' "$(json_escape "שם המחשב '$_name' אינו חוקי ב-Windows — מותרים אותיות אנגליות, ספרות ומקף בלבד, לא בהתחלה ולא בסוף")"
            return 1 ;;
    esac
    # NetBIOS caps names at 15 characters; a longer one is silently truncated
    # by Windows and the machine ends up with a name nobody expects.
    [ "${#_name}" -le 15 ] || {
        printf '{"ok":false,"error":"%s","code":"bad_hostname"}\n' "$(json_escape "שם המחשב '$_name' ארוך מ-15 תווים — Windows היה מקצר אותו")"
        return 1
    }

    # Which system is on the disk decides how the name is written. The
    # partition roles in the manifest are the source of truth for this
    # (spec section 14) -- there is no separate "which OS" flag to get wrong.
    _hostname_plan="$RUN_DIR/hostname.plan"
    if ! manifest_plan "$_manifest" > "$_hostname_plan"; then
        printf '{"ok":false,"error":"לא הצלחנו לקרוא את תוכנית המחיצות","code":"manifest_plan_failed"}\n'
        return 1
    fi
    # grep: 0 = found, 1 = not found (an answer), 2+ = the check broke.
    grep -qE '^[^|]*\|[^|]*\|windows\|' "$_hostname_plan"
    _plan_rc=$?
    grep -qE '^[^|]*\|[^|]*\|linux\|' "$_hostname_plan"
    _lin_rc=$?
    if [ "$_plan_rc" -gt 1 ] || [ "$_lin_rc" -gt 1 ]; then
        printf '{"ok":false,"error":"לא הצלחנו לבדוק את תוכנית המחיצות","code":"manifest_plan_check_failed"}\n'
        return 1
    fi
    if [ "$_plan_rc" -eq 1 ]; then
        _write_hostname_linux "$_disk" "$_manifest" "$_name" "$_hostname_plan"
        return $?
    fi
    [ "$_lin_rc" -eq 0 ] || { _write_hostname_windows; return $?; }
    _write_hostname_both
}

_write_hostname_both() {
    # #107: a dual-boot disk is named on BOTH sides -- until #107 only Windows
    # was, and every Linux boot came up with the build machine's name. Each
    # side runs in its own subshell (their _mnt/_rc must not cross). Both /
    # windows only / linux only / neither are four results, not two: a side
    # that failed is named with its own error, never hidden by the other's
    # success (עיקרון 5). Uses write_hostname's _disk/_manifest/_name/_hostname_plan.
    _w=$(_write_hostname_windows); _wrc=$?
    _l=$(_write_hostname_linux "$_disk" "$_manifest" "$_name" "$_hostname_plan"); _lrc=$?
    _werr=$(printf '%s' "$_w" | sed -n 's/.*"error":"\([^"]*\)".*/\1/p')
    _lerr=$(printf '%s' "$_l" | sed -n 's/.*"error":"\([^"]*\)".*/\1/p')
    [ -n "$_werr" ] || _werr="rc=$_wrc"
    [ -n "$_lerr" ] || _lerr="rc=$_lrc"
    if [ "$_wrc" -eq 0 ] && [ "$_lrc" -eq 0 ]; then
        printf '{"ok":true,"hostname":"%s","method":"%s+%s"}\n' "$_name" "$HOSTNAME_METHOD" "$HOSTNAME_METHOD_LINUX"
        return 0
    elif [ "$_wrc" -eq 0 ]; then
        printf '{"ok":false,"error":"צד הלינוקס לא קיבל שם (צד Windows קיבל): %s","code":"named_windows_only"}\n' "$_lerr"
    elif [ "$_lrc" -eq 0 ]; then
        printf '{"ok":false,"error":"צד Windows לא קיבל שם (צד הלינוקס קיבל): %s","code":"named_linux_only"}\n' "$_werr"
    else
        printf '{"ok":false,"error":"אף צד לא קיבל שם — Windows: %s; לינוקס: %s","code":"named_neither"}\n' "$_werr" "$_lerr"
    fi
    return 1
}

_write_hostname_windows() {
    # The offline-registry path. Uses write_hostname's _disk/_manifest/_name/
    # _hostname_plan; emits the section 5 result.
    _mnt=$(_mount_windows "$_disk" "$_manifest" "$_hostname_plan") || {
        printf '{"ok":false,"error":"לא הצלחנו לעגן את מחיצת Windows","code":"mount_failed"}\n'
        return 1
    }

    _hive="$_mnt/Windows/System32/config/SYSTEM"
    if [ ! -f "$_hive" ]; then
        umount "$_mnt" 2>/dev/null
        printf '{"ok":false,"error":"כוורת SYSTEM לא נמצאה","code":"no_hive"}\n'
        return 1
    fi

    _cs=$(_control_set "$_hive") || {
        umount "$_mnt" 2>/dev/null
        printf '{"ok":false,"error":"לא הצלחנו לקרוא את ה-ControlSet הפעיל","code":"no_controlset"}\n'
        return 1
    }
    log "writing hostname $_name into $_cs" >&2

    # שלושה ערכים בשני מפתחות, כולם דרך hivewrite (‏agent/hivewrite.c):
    # כתיבה משמרת-ערכים — ‏setval של hivexsh מחליף את *כל* רשימת ערכי
    # המפתח, וב-Tcpip\Parameters זה היה מוחק את שאר הגדרות ה-TCP/IP ‏(#33).
    # ‏ActiveComputerName הוא מפתח *נדיף* — קיים רק בזיכרון של Windows רץ,
    # ‏Windows בונה אותו מחדש מ-ComputerName בכל אתחול. ‏Hostname נבנה
    # מ-"NV Hostname" באתחול, אבל נכתבים שניהם — כמו שעושה sysprep.
    hivewrite "$_hive" "$_cs\\Control\\ComputerName\\ComputerName" \
        ComputerName "$_name" >> "$LOG_FILE" 2>&1
    _rc=$?
    hivewrite "$_hive" "$_cs\\Services\\Tcpip\\Parameters" \
        Hostname "$_name" "NV Hostname" "$_name" >> "$LOG_FILE" 2>&1
    _rc2=$?
    # "נכתב" הוא מה שנקרא בחזרה מהדיסק — לא קוד יציאה. הכתיבה הקודמת
    # כבר החזירה 0 פעם אחת בלי לכתוב כלום (#33).
    _back=$(hivewrite -g "$_hive" "$_cs\\Control\\ComputerName\\ComputerName" \
                ComputerName 2>> "$LOG_FILE")
    _back_host=$(hivewrite -g "$_hive" "$_cs\\Services\\Tcpip\\Parameters" \
                Hostname 2>> "$LOG_FILE")
    _back_nv=$(hivewrite -g "$_hive" "$_cs\\Services\\Tcpip\\Parameters" \
                "NV Hostname" 2>> "$LOG_FILE")
    # #1976: a sysprepped image renames itself in specialize -- the answer file carries the name too.
    sysprep_name "$_mnt" "$_name" "$_cs"; _sprc=$?
    _umount_checked "$_mnt"
    _umrc=$?

    if [ "$_rc" -ne 0 ] || [ "$_rc2" -ne 0 ] || [ "$_back" != "$_name" ] \
        || [ "$_back_host" != "$_name" ] || [ "$_back_nv" != "$_name" ]; then
        log "hostname verify failed: wrote '$_name', read back computer='$_back' hostname='$_back_host' nv='$_back_nv' (rc=$_rc/$_rc2)" >&2
        printf '{"ok":false,"error":"עריכת הרג׳יסטרי נכשלה","code":"hive_write_failed"}\n'
        return 1
    fi
    if [ "$_umrc" -ne 0 ]; then
        printf '{"ok":false,"error":"לא הצלחנו לנתק את המחיצה אחרי עריכת הרג׳יסטרי","code":"umount_failed"}\n'
        return 1
    fi
    if [ "$_sprc" -ne 0 ]; then
        printf '{"ok":false,"error":"%s","code":"unattend_failed"}\n' "$(json_escape "השם נכתב לרג׳יסטרי, אך לא לקובץ התשובות של sysprep: ${UNATTEND_ERROR:-rc=$_sprc}")"
        return 1
    fi
    printf '{"ok":true,"hostname":"%s","method":"%s"}\n' "$_name" "$HOSTNAME_METHOD"
}

compose_hostname() {
    # $1 = prefix, $2 = suffix. INS is always uppercase (section 10).
    printf '%s-%s' "$(printf '%s' "$1" | tr 'a-z' 'A-Z')" \
                   "$(printf '%s' "$2" | tr 'a-z' 'A-Z')"
}

# --- שלב השם במשימה --------------------------------------------------------

# ‏#539: הפונקציה ישבה ב-`imagectl-agent` עד שהקובץ נגע בקיר 300 של
# `sizelimit`, ותוספת האסימון של #530 חצתה אותו. הקיר הזה בנוי
# בדיוק נגד הפתרון הקל — ‏#478 "נפתר" בכך שהחציה הועברה לקובץ אחר —
# ולכן הפיצול כאן ולא דחיסת שורות. המקום נבחר כי `compose_hostname`
# ו-`write_hostname` כבר כאן, ו-`RESP` כבר נקרא ב-libs אחרים
# (`task.sh`, `buildmenuitems.sh`). האתר שקורא לה נשאר במסלול השחזור.
name_this_machine() {
    # $1 = disk, $2 = session id. Never fatal: an image that boots with the
    # wrong name is fixable in a minute; one that does not boot is not.
    _prefix=$(json_get "$RESP" ".session.prefix")
    _suffix=$(json_get "$RESP" ".group.suffix")
    if [ "$_prefix" = "null" ] || [ "$_suffix" = "null" ]; then
        log "no prefix/suffix in the server answer -- skipping the hostname"
        return 0
    fi
    echo "naming" > "$RUN_DIR/state"
    _name=$(compose_hostname "$_prefix" "$_suffix")
    _result=$(write_hostname "$1" "$RUN_DIR/manifest.json" "$_name")
    _hrc=$?
    echo "$_result" > "$RUN_DIR/hostname.json"
    log "hostname: $_result"
    # ‏#856: "לא הצלחנו לכתוב שם" אינו "הכול תקין" (עיקרון 5). היעד נשאר
    # done -- השחזור הושלם -- אבל נושא את הסיבה בשדה `error`, כמו
    # "done (grow deferred)" של finish_grow: זה מה שמגיע לשרת ולקונסולה;
    # ‏hostname.json ב-tmpfs נמחק באתחול (#106). אחרי אזהרה קודמת, לא במקומה.
    if [ "$_hrc" -ne 0 ]; then
        _why=$(json_get "$RUN_DIR/hostname.json" ".error")
        { [ -n "$_why" ] && [ "$_why" != "null" ]; } || _why="סיבה לא ידועה (rc=$_hrc)"
        _why="שם המחשב לא נכתב: $_why"
        _errf="$RUN_DIR/targets/$1/error"
        [ -s "$_errf" ] && _why="$(cat "$_errf") | $_why"
        target_set "$1" "done" "$_why"
    fi
}
