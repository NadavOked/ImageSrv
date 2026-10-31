# tools.sh -- the IT toolbox framework (#649, domain 1). POSIX sh (busybox ash).
#
# The contract (issue #649, Nadav 17/09): every agent/lib/tools_<domain>.sh
# defines tools_<domain>_list (one line per tool: id|domain|title|risk|args,
# risk = ro / rw / destroy) and tools_<domain>_run <id> [arg]. This file loads
# them, unifies the list, guards rw/destroy behind the typed machine name
# BEFORE any module runs, and owns the output file $RUN_DIR/tool-<id>.out.
#
# Return codes of tools_run (the GUI shows each one differently):
#   0 ran, nothing wrong   1 ran, found a problem   2 could not check (orange,
#   never green -- principle 5)   3 confirm did not match the machine name
#   4 unknown tool / bad id
#
# Two built-in tools ride in the framework so the screen is usable before the
# domain modules land: sysinfo (dmidecode) and smart-health (smartctl -H per
# disk). They are listed only when their binary is really packed.
#
# #1050: the selection the operator saved in the console is the allow-list.
# It rides in the initrd as /etc/imagesrv/tools-selection.json (toolbins.sh
# reads it), and an id that is not in it is neither listed nor run -- a
# built-in included. No file = nothing offered, said once on stderr.

TOOLS_LOADED=""
TOOLS_DOMAINS=""
TOOLS_SELECTED=""

tools_load() {
    # Source agent/lib/tools_*.sh once. None today: the framework works with
    # zero modules and the screen says so. Lazy, not at source time -- the
    # agent sources this file for the load gate (#84) and must stay inert.
    [ -z "$TOOLS_LOADED" ] || return 0
    TOOLS_LOADED=1; TOOLS_DOMAINS=""
    . "${LIB_DIR:-/usr/lib/imagesrv}/toolbins.sh"
    TOOLS_SELECTED=$(tools_selection_ids) || TOOLS_SELECTED=""
    for _tf in "${LIB_DIR:-/usr/lib/imagesrv}"/tools_*.sh; do
        [ -f "$_tf" ] || continue                  # unmatched glob = the literal
        _td=${_tf##*/tools_}; _td=${_td%.sh}
        case "$_td" in ''|*[!a-z0-9_]*) printf 'tools: bad module name %s\n' "$_tf" >&2; continue ;; esac
        . "$_tf"
        TOOLS_DOMAINS="$TOOLS_DOMAINS $_td"
    done
}

tools_id_ok() {
    # The id becomes a file name: letters, digits, _ and - only, no slash.
    case "$1" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
}

tools_selected() {
    # $1 = id -> rc 0 only when the selection file names it (#1050). The
    # list is newline separated, so the match is on a whole line.
    case "
$TOOLS_SELECTED
" in *"
$1
"*) return 0 ;; esac
    return 1
}

tools_machine_name() {
    # The name the operator must type for rw/destroy: the server's name for
    # this machine when the caller knows it, else the kernel hostname. Empty
    # is allowed here -- tools_run then refuses everything (fail closed).
    if [ -n "${MACHINE_NAME:-}" ]; then printf '%s\n' "$MACHINE_NAME"; else hostname 2>/dev/null; fi
}

# --- the two built-ins ---------------------------------------------------------

tools_core_list() {
    command -v dmidecode >/dev/null 2>&1 &&
        printf 'sysinfo|hw|זיהוי המחשב: יצרן, דגם, מספר סידורי, BIOS|ro|\n'
    command -v smartctl >/dev/null 2>&1 &&
        printf 'smart-health|disk|בריאות SMART של כל דיסק|ro|\n'
    return 0
}

_tools_dmi_field() {
    # $1 = dmidecode -s keyword, $2 = Hebrew label. A failed dmidecode is a
    # failed check (rc 2), an empty answer is "not reported" -- two states.
    _v=$(dmidecode -s "$1" 2>/dev/null) || { printf '%s: לא הצלחנו לבדוק (dmidecode נכשל)\n' "$2"; return 2; }
    _v=$(printf '%s' "$_v" | head -n 1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    case "$_v" in
        ''|"To be filled by O.E.M."|"Default string"|"Not Specified"|"System Product Name")
            printf '%s: לא דווח על ידי הקושחה\n' "$2" ;;
        *) printf '%s: %s\n' "$2" "$_v" ;;
    esac
}

tools_core_sysinfo() {
    _rc=0
    _tools_dmi_field system-manufacturer 'יצרן' || _rc=2
    _tools_dmi_field system-product-name 'דגם' || _rc=2
    _tools_dmi_field system-serial-number 'מספר סידורי' || _rc=2
    _tools_dmi_field baseboard-product-name 'לוח אם' || _rc=2
    _tools_dmi_field bios-vendor 'יצרן BIOS' || _rc=2
    _tools_dmi_field bios-version 'גרסת BIOS' || _rc=2
    _tools_dmi_field bios-release-date 'תאריך BIOS' || _rc=2
    printf '\nמה זה אומר: המספר הסידורי הוא מה שמזהה את המחשב מול היצרן והאחריות; גרסת ה-BIOS ותאריכה אומרים אם יש עדכון קושחה שטרם הותקן.\n'
    return $_rc
}

tools_core_smart_health() {
    # smartctl -H per whole disk. Exit code bits (smartctl(8)): 1/2 = could
    # not open / identify (unchecked, rc 2), 8 = SMART overall-health FAILED.
    # Worst verdict wins for the return code: a failed disk (1) over an
    # unchecked one (2) over clean (0) -- and the text lists every disk.
    _fail=0; _unk=0; _n=0
    for _p in "${SYSROOT:-}"/sys/block/*; do
        [ -e "$_p" ] || continue
        _d=${_p##*/}
        case "$_d" in sd*|nvme*|hd*|vd*|mmcblk*) ;; *) continue ;; esac
        case "$_d" in nvme*p[0-9]*|mmcblk*p[0-9]*) continue ;; esac
        _n=$((_n + 1))
        _model=$(sed 's/^[[:space:]]*//;s/[[:space:]]*$//' "$_p/device/model" 2>/dev/null)
        [ -n "$_model" ] || _model='דגם לא ידוע'
        smartctl -H "/dev/$_d" >/dev/null 2>&1; _src=$?
        if [ $((_src & 8)) -ne 0 ]; then
            printf '/dev/%s (%s): נכשל -- הדיסק עצמו מדווח על כשל SMART. להחליף לפני שמשתמשים בו.\n' "$_d" "$_model"; _fail=1
        elif [ $((_src & 3)) -ne 0 ]; then
            printf '/dev/%s (%s): לא הצלחנו לבדוק -- smartctl לא הצליח לקרוא את הדיסק (rc %s). זה לא "תקין".\n' "$_d" "$_model" "$_src"; _unk=1
        else
            printf '/dev/%s (%s): תקין -- SMART overall-health PASSED\n' "$_d" "$_model"
        fi
    done
    [ "$_n" -gt 0 ] || { printf 'לא נמצאו דיסקים ב-/sys/block -- לא הצלחנו לבדוק.\n'; return 2; }
    printf '\nמה זה אומר: PASSED הוא הדיווח של הדיסק על עצמו, לא ערובה; כשל ב-SMART הוא ראיה מספקת להחלפה.\n'
    [ "$_fail" = 1 ] && return 1
    [ "$_unk" = 1 ] && return 2
    return 0
}

tools_core_run() {
    case "$1" in
        sysinfo) tools_core_sysinfo ;;
        smart-health) tools_core_smart_health ;;
        *) return 4 ;;
    esac
}

# --- the unified list, and the guarded run ---------------------------------------

tools_list() {
    # id|domain|title|risk|args, built-ins first, then every module in load
    # order. A line without its four leading fields is dropped and said on
    # stderr, not folded into a nameless row.
    tools_load
    for _tp in core $TOOLS_DOMAINS; do
        "tools_${_tp}_list" 2>/dev/null | while IFS= read -r _tl; do
            IFS='|' read -r _id _dom _title _risk _ <<EOF
$_tl
EOF
            if ! tools_id_ok "$_id" || [ -z "$_dom" ] || [ -z "$_title" ] || [ -z "$_risk" ]; then
                printf 'tools: %s: malformed line dropped: %s\n' "$_tp" "$_tl" >&2
            elif tools_selected "$_id"; then
                printf '%s\n' "$_tl"
            fi
        done
    done
}

_tools_find() {
    # $1 = id -> TOOL_PROVIDER, TOOL_LINE, TOOL_RISK; rc 1 when unknown.
    TOOL_PROVIDER=""; TOOL_LINE=""; TOOL_RISK=""
    for _tp in core $TOOLS_DOMAINS; do
        _hit=$("tools_${_tp}_list" 2>/dev/null | awk -F'|' -v id="$1" '$1 == id { print; exit }')
        [ -n "$_hit" ] || continue
        TOOL_PROVIDER=$_tp; TOOL_LINE=$_hit
        TOOL_RISK=$(printf '%s' "$_hit" | cut -d'|' -f4)
        return 0
    done
    return 1
}

tools_run() {
    # tools_run <id> [arg] [confirm]. The confirm check for rw/destroy runs
    # here, before the module is even called -- the guard lives in the
    # framework, a module cannot forget it. The confirm is still handed on:
    # the modules gate again with it (#1050). Output: $RUN_DIR/tool-<id>.out.
    _id=$1; _arg=${2:-}; _confirm=${3:-}
    tools_load
    tools_id_ok "$_id" || return 4
    _tools_find "$_id" || return 4
    tools_selected "$_id" || { log "tool $_id refused: not in the server's selection"; return 4; }
    case "$TOOL_RISK" in
        ro) ;;
        *)  # rw, destroy -- and any risk word this file does not know reads
            # as destroy: an unknown label must not open a door (principle 5).
            _name=$(tools_machine_name)
            if [ -z "$_name" ] || [ "$_confirm" != "$_name" ]; then
                log "tool $_id ($TOOL_RISK) refused: confirmation does not match the machine name"
                return 3
            fi ;;
    esac
    mkdir -p "$RUN_DIR" 2>/dev/null
    _out="$RUN_DIR/tool-$_id.out"
    log "tool $_id ($TOOL_RISK) running via $TOOL_PROVIDER${_arg:+ with arg $_arg}"
    "tools_${TOOL_PROVIDER}_run" "$_id" "$_arg" "$_confirm" > "$_out" 2>&1
    _rc=$?
    log "tool $_id finished rc=$_rc"
    return $_rc
}

# --- the kiosk side (guibridge.sh dispatches here) ----------------------------------

tools_gui_list() {
    # $GUI_DIR/tools: first line machine=<name the operator must type>, then
    # the tools_list rows. Atomic, like every file the GUI polls.
    { printf 'machine=%s\n' "$(tools_machine_name)"; tools_list; } > "$GUI_DIR/tools.next" &&
        mv "$GUI_DIR/tools.next" "$GUI_DIR/tools"
}

tools_gui_run() {
    # $1 = the record token: tool-run|<id>|<arg>|<confirm>. The old result is
    # removed first so the GUI never reads a stale one as this run's; the new
    # one is id|rc|<path>|<seq>, seq strictly increasing per kiosk session.
    # Returns 0 whenever a result was written -- a tool's own rc is a result,
    # not a failed action (gui_records would toast it as one).
    # #1303: the GUI hides the button on menu_tools=0, but a record can still
    # arrive; the gate is here. Off (v1) or unreadable = no run, no result.
    tools_on || { log "tool-run refused: the toolbox is off in this edition"; return 1; }
    _tok=$1
    IFS='|' read -r _ _id _arg _confirm _ <<EOF
$_tok
EOF
    tools_id_ok "$_id" || return 1
    case "$_arg" in *[!A-Za-z0-9_./:@-]*) return 1 ;; esac
    rm -f "$GUI_DIR/tool-result" || return 1
    _seq=$(cat "$GUI_DIR/tool-seq" 2>/dev/null); case "$_seq" in ''|*[!0-9]*) _seq=0 ;; esac
    _seq=$((_seq + 1)); printf '%s\n' "$_seq" > "$GUI_DIR/tool-seq" || return 1
    # The name the server registered for this machine (station.json is the
    # kiosk's own fresh copy of /agent/state); absent = hostname fallback.
    MACHINE_NAME=$(jq -r '.name // empty' "$GUI_DIR/station.json" 2>/dev/null | tr -d '\r')
    export MACHINE_NAME
    tools_run "$_id" "$_arg" "$_confirm"
    _rc=$?
    printf '%s|%s|%s|%s\n' "$_id" "$_rc" "$RUN_DIR/tool-$_id.out" "$_seq" > "$GUI_DIR/tool-result.next" &&
        mv "$GUI_DIR/tool-result.next" "$GUI_DIR/tool-result"
}
