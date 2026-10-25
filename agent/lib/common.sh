# common.sh -- logging, HTTP, kernel command line, fail-safe reboot.
# POSIX sh (busybox ash). No bashisms.

# A missing stamp is unknown; never substitute the script's original version.
AGENT_VERSION=$(cat "${AGENT_VERSION_FILE:-/etc/imagectl/agent-version}" 2>/dev/null) || AGENT_VERSION=unknown
case "$AGENT_VERSION" in ''|*[!a-zA-Z0-9._+-]*) AGENT_VERSION=unknown ;; esac
RUN_DIR="${RUN_DIR:-/run/imagectl}"
LOG_FILE="$RUN_DIR/agent.log"

log() {
    # Timestamped line to the log file; to the console only while no screen of ours owns it (owner 17/10): the live GUI (gui.pid, guiparent.sh) or the static one (splash.on).
    _ts=$(date -u +%H:%M:%S 2>/dev/null || echo "--:--:--")
    echo "[$_ts] $*" >> "$LOG_FILE" 2>/dev/null
    { read -r _lp < "$RUN_DIR/gui.pid" && kill -0 "$_lp"; } 2>/dev/null || [ -e "$RUN_DIR/splash.on" ] || echo "imagectl: $*"
}

die_local() {
    # The safety principle: every unclear state ends at the local disk.
    #
    # A reboot is NOT a descent to the local disk -- it only hopes for one.
    # GRUB asks the server for a menu, and while a session is open that menu
    # answers "boot the agent" with timeout=0: the machine lands right back
    # here and fails again, for ever (#75). Nothing this side of the wire can
    # promise where the next boot goes, because the server writes the menu.
    #
    # So this stays the loud, fast failure it always was, and the guarantee
    # lives where the evidence is: the server counts the agent boots it hands
    # out for one job, and after a small budget serves a local-only menu. An
    # agent that dies before it has a network -- the case that was reproduced
    # on hardware -- never reports anything, and is covered all the same.
    log "FATAL: $1 -- rebooting; the server decides where the next boot goes"
    command -v fail_fatal >/dev/null 2>&1 && fail_fatal "$1" || :   # #1569: the reason on a screen first, held FAIL_SHOW_S (failscreen.sh)
    sleep 3
    if [ "${IMAGECTL_TEST:-0}" = "1" ]; then
        echo "TEST-REBOOT: $1"
        exit 86
    fi
    sync
    reboot -f
}

# --- kernel command line -----------------------------------------------------
# Only two keys are ever read: imagectl.server and imagectl.mode.
# Nothing about the task travels here -- the task comes from hello (section 3
# of the interfaces). Any other imagectl.* key is a bug elsewhere: ignore it
# loudly so it cannot become a second, undocumented interface.

#: מה עושים כשמשימה הסתיימה בהצלחה. **בלי override מפורש הברירה נגזרת
#: מהתפקיד**: תחנת כיתה מאתחלת, בנייה/שיכפול מתכבים — מגירה מוחלפת
#: במכונה כבויה (נספח א׳), ולכן כיבוי הוא השלב הבא של הסבב ולא כשל.
#: אבל במעבדה מרוחקת אין מי שידליק, ולכן כלי התכנאי כותב `reboot`
#: מפורש. הערך נכתב בזמן בניית ה-initramfs (`--after-task`) ואינו מגיע
#: משורת הפקודה של הקרנל — שם מותר רק `imagectl.server`
#: ו-`imagectl.mode` (עיקרון 2).
arm_wol() {
    # Read back the NIC state: a successful set alone is not evidence (#587).
    command -v ethtool >/dev/null 2>&1 || {
        log "wol: ethtool unavailable -- no interface armed"
        return 1
    }
    _wol_supported=0
    _wol_failed=0
    for _wol_path in "${SYSROOT:-}/sys/class/net/"*; do
        [ -e "$_wol_path" ] || continue
        _wol_n=${_wol_path##*/}
        [ "$_wol_n" = lo ] && continue
        [ -d "$_wol_path/wireless" ] && continue
        if ! _wol_info=$(ethtool "$_wol_n" 2>/dev/null); then
            log "wol: $_wol_n -- cannot read capabilities"
            _wol_failed=$((_wol_failed + 1))
            continue
        fi
        case "$(printf '%s\n' "$_wol_info" | awk '/Supports Wake-on:/ {print $3}')" in
            *g*) _wol_supported=$((_wol_supported + 1)) ;;
            *) log "wol: $_wol_n -- MagicPacket unsupported"; continue ;;
        esac
        if ! ethtool -s "$_wol_n" wol g >/dev/null 2>&1; then
            log "wol: $_wol_n -- arming command failed"
            _wol_failed=$((_wol_failed + 1))
            continue
        fi
        if ! _wol_info=$(ethtool "$_wol_n" 2>/dev/null); then
            log "wol: $_wol_n -- cannot read back arming"
            _wol_failed=$((_wol_failed + 1))
            continue
        fi
        case "$(printf '%s\n' "$_wol_info" | awk '/^[[:space:]]*Wake-on:/ {print $2}')" in
            *g*) log "wol: $_wol_n armed" ;;
            *) log "wol: $_wol_n -- arming did not take"; _wol_failed=$((_wol_failed + 1)) ;;
        esac
    done
    [ "$_wol_supported" -gt 0 ] && [ "$_wol_failed" -eq 0 ]
}

safe_poweroff() {
    # Every S5 path ends here. Failure is persisted before the last hello;
    # staying on leaves the ordinary heartbeat another chance if it fails.
    # POWEROFF_WHY names a refusal for a screen to say (#1483): wol|poweroff;
    # power.fail is the one every screen draws (guistate.sh gui_hello_state).
    POWEROFF_WHY=""; rm -f "$RUN_DIR/power.fail"
    if ! arm_wol; then
        printf '%s\n' false > "$RUN_DIR/wol_armed"
        log "wol: not all supported wired interfaces were armed -- not powering off"
        command -V send_hello >/dev/null 2>&1 && send_hello || :
        POWEROFF_WHY=wol; return 1
    fi
    rm -f "$RUN_DIR/wol_armed"
    if [ -n "${2:-}" ]; then "$2" || return 1; fi
    log "${1:-power}: powering off with wake-on-LAN armed"
    if [ "${IMAGECTL_TEST:-0}" = 1 ]; then echo "TEST-POWEROFF"; return 0; fi
    sync
    # #1483: -f. PID 1 is agent/init, a shell: busybox poweroff without -f only
    # signals it, the kernel drops the signal, and it exits 0 -- still on.
    poweroff -f
    _po_rc=$?; log "${1:-power}: poweroff -f returned rc=$_po_rc -- the machine is still on"
    echo "המחשב לא כובה: פקודת הכיבוי חזרה (rc=$_po_rc). כבו אותו בכפתור ההפעלה." > "$RUN_DIR/power.fail"
    POWEROFF_WHY=poweroff; return 1
}

finish_and_stop() {
    # הנתיב ניתן להזרקה כדי שטסט יריץ את הפונקציה **הזאת** ולא עותק
    # שלה. טסט שמשכפל את הלוגיקה עובר גם כשהמימוש נסחף.
    _f="${AFTER_TASK_FILE:-/etc/imagectl/after-task}"
    _after=""
    [ -r "$_f" ] && _after=$(cat "$_f")
    # ערך מפורש (poweroff/reboot) גובר תמיד — זה מה שכלי התכנאי כותב
    # לצי המעבדה (tools/lab/after-task-reboot.sh). בלי ערך מפורש
    # (auto/ריק/חסר) הברירה נגזרת מהתפקיד: תחנת כיתה מאתחלת כי אדם
    # ממתין לידה, ומכונת בנייה/שיכפול מתכבה כי מגירה מוחלפת במכונה
    # כבויה (נספח א׳). כל שאר המצבים — כולל role ריק או לא-מוכר —
    # נופלים לכיבוי, הברירה הבטוחה (עיקרון 1). ה-role הוא D_ROLE,
    # שנקבע ב-read_answer מתשובת ה-hello והוא גלובלי לתהליך.
    case "$_after" in
        reboot|poweroff) ;;
        *)
            case "${D_ROLE:-}" in
                classroom|student) _after=reboot ;;
                *)                  _after=poweroff ;;
            esac
            ;;
    esac
    case "$_after" in
        reboot)
            log "task complete -- rebooting (after-task=reboot)"
            sync; reboot -f
            ;;
        *)
            safe_poweroff "task complete"
            ;;
    esac
}


parse_cmdline() {
    _file="${CMDLINE_FILE:-/proc/cmdline}"
    IMAGECTL_SERVER=""
    IMAGECTL_MODE="normal"
    IMAGECTL_MONITOR="0"
    for _word in $(cat "$_file" 2>/dev/null); do
        case "$_word" in
            imagectl.server=*)
                IMAGECTL_SERVER="${_word#imagectl.server=}"
                ;;
            imagectl.mode=recovery)
                IMAGECTL_MODE="recovery"
                ;;
            imagectl.mode=installer|imagectl.mode=usbclone)
                # ‏#1190: ה-ISO של ההתקנה מריץ את אותו initramfs, ו-init מוסר
                # למתקין החי במקום לסוכן. ‏PXE מהשרת לעולם אינו שולח את זה.
                # ‏#1914 usbclone: הדיסק-און-קי לשיכפול בלי שרת (usbclone.sh).
                IMAGECTL_MODE="${_word#imagectl.mode=}"
                ;;
            imagectl.monitor=1)
                # מוניטור מרחוק (#690): שירות RFB על /dev/fb0, מגודר בשער
                # משלו בשורת הקרנל. השרת מוסיף אותו לשורת הקרנל רק
                # כשהמתג בקונסולה דלוק (monitor.py:station_cmdline).
                IMAGECTL_MONITOR="1"
                ;;
            imagectl.*)
                log "WARNING: ignoring unknown kernel parameter: $_word"
                ;;
        esac
    done
    unset _word _file
    export IMAGECTL_SERVER IMAGECTL_MODE IMAGECTL_MONITOR
}

# --- HTTP --------------------------------------------------------------------
# All server traffic goes through these two helpers.

HTTP_RETRIES="${HTTP_RETRIES:-3}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-10}"
# ‏העברה ארוכה (מחיצה, העלאת קליטה) לא מקבלת תקרת *משך* — מחיצה של
# 100GB על כונן איטי לוקחת דקות וזה תקין. מה שהיא כן מקבלת הוא תקרת
# *חוסר התקדמות*: פחות מבייט לשנייה במשך כך וכך שניות = חיבור מת, ו-curl
# יוצא עם שגיאה במקום להיתלות. אותו עיקרון של WAIT_STREAM_STALL_S.
HTTP_STALL_TIMEOUT="${HTTP_STALL_TIMEOUT:-120}"

http_post_json() {
    # $1 = URL, $2 = file holding the JSON body, $3 = one extra header
    # (optional; #855 -- the task token on a capture report), $4 = where to dump the response headers (optional; #1916). Response on stdout.
    curl -sfS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -H "Content-Type: application/json" ${3:+-H "$3"} ${4:+-D "$4"} \
        --data-binary "@$2" "$1"
}

http_get() {
    # $1 = URL. Response on stdout.
    curl -sfS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" "$1"
}

http_get_stream() {
    # $1 = URL. Long transfer: no total timeout, fail fast on connect --
    # and abort a transfer that stopped moving instead of waiting for ever.
    curl -sfS --connect-timeout "$HTTP_TIMEOUT" \
        --speed-limit 1 --speed-time "$HTTP_STALL_TIMEOUT" "$1"
}

# --- boot trace (#400) -------------------------------------------------------
# A cloning machine is headless by design (#17): no screen, no keyboard, no
# mouse. When it stops between the GRUB menu and the first hello there is
# nothing at all to look at -- that is what blocked the first test on real
# hardware. So the agent says where it is, over HTTP, in one short request.
#
# Two rules, and they pull in opposite directions on purpose:
#
#   1. This NEVER fails its caller. A diagnostic that stops a machine from
#      booting is damage. trace_step always returns 0.
#   2. It is never silent. A failed delivery is logged here, and the server
#      side has the full list of steps -- so a step that never arrives is
#      itself the finding, not a gap.
TRACE_TIMEOUT="${TRACE_TIMEOUT:-3}"

trace_step() {
    # $1 = step name; optional $2 = one key=value query fragment. Uses
    # $SERVER and $MAC, which the agent sets before it can do anything else.
    [ -n "$SERVER" ] && [ -n "$MAC" ] || return 0
    if curl -sf -m "$TRACE_TIMEOUT" \
        "$SERVER/boot/step?mac=$MAC&s=$1${2:+&$2}" > /dev/null 2>&1; then
        return 0
    fi
    log "boot step '$1' was not delivered -- continuing"
    return 0
}

# --- misc --------------------------------------------------------------------

json_escape() {
    # Escape a string for embedding inside a JSON string literal:
    # backslash, double quote and every control character below 0x20.
    # Newlines become \n, CR is dropped (as it always was), and any other
    # control character becomes \u00XX. Everything else -- UTF-8 Hebrew
    # included -- passes through byte for byte.
    #
    # Not gsub(), on purpose. In the *replacement* string of gsub a
    # backslash is an escape of its own, and gawk, mawk and busybox awk
    # read those escapes differently. That is what made the previous
    # version replace every character with itself and escape nothing at
    # all: it passed under mawk on the workstation and did nothing under
    # the busybox awk the agent actually runs, so a password or a
    # partclone error holding a quote produced broken JSON and a 400 that
    # never reached the password check (#145).
    #
    # A lookup table has one meaning in every awk: the value is printed
    # as it stands, with no second round of interpretation. LC_ALL=C so
    # the scan is over bytes -- a multi-byte character is copied through
    # one byte at a time and is never split by a table hit.
    printf '%s' "$1" | LC_ALL=C awk 'BEGIN {
        ORS = ""
        for (i = 1; i < 32; i++) esc[sprintf("%c", i)] = sprintf("\\u%04x", i)
        esc[sprintf("%c", 8)] = "\\b"
        esc[sprintf("%c", 9)] = "\\t"
        esc[sprintf("%c", 12)] = "\\f"
        esc[sprintf("%c", 13)] = ""
        esc["\\"] = "\\\\"
        esc["\""] = "\\\""
    }
    {
        if (NR > 1) printf "%s", "\\n"
        out = ""
        n = length($0)
        for (i = 1; i <= n; i++) {
            c = substr($0, i, 1)
            if (c in esc) out = out esc[c]
            else out = out c
            # Appending to a string costs its whole length in awk, so a
            # long line would cost the square of it. Flushing a short
            # buffer keeps that flat: 48KB takes 69ms instead of 210ms,
            # and a normal field (tens of bytes) is unaffected.
            if (length(out) >= 256) { printf "%s", out; out = "" }
        }
        printf "%s", out
    }'
}

trim() {
    # Strip leading/trailing whitespace.
    printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}
