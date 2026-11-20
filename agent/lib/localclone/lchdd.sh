# lchdd.sh -- X16 (#3064, Nadav 15/11) local clone: "HDD support" with no server.
# A target HDD is refused when the switch is off (the default); an HDD SOURCE is
# never asked about. The switch is the last value the server sent this cloner
# (hello, like min_free_gb: min_free_take, diskfit.sh), kept for this boot in
# $RUN_DIR/hdd_support. None stored = off. POSIX sh (busybox ash).
#
# The evidence about the disk is $LC/inv/<dev>.rot, written by lc_inv_disk from
# queue/rotational: 1 = HDD, 0 = not. Anything else -- no file, empty, malformed --
# is "not read", which is NOT "not an HDD": it is judged as an SSD (the owner's
# rule, as the receiver's hdd_target_policy does) and the log says so, once per
# disk per inventory (the kiosk asks every second; $LC/inv/<dev>.rotlog marks it).
# The screen draws the refusal as the target's "short" verdict (red, "לא ישמש
# כיעד"); the run refuses it by name before any write (lc_hdd_gate).

HDD_OFF_HE='HDD — תמיכה ב-HDD כבויה בהגדרות'

hdd_support_take() {
    # $1 = hello answer. Only a literal true/false is kept; a missing field leaves
    # the last value (an older server, a short answer); anything else is off, aloud.
    _hs_v=$(json_get "$1" ".hdd_support")
    case "$_hs_v" in
        true|false) echo "$_hs_v" > "$RUN_DIR/hdd_support" ;;
        null|'') ;;
        *) log "hdd_support from the server is not true/false ($_hs_v) -- off, the default"; rm -f "$RUN_DIR/hdd_support" ;;
    esac
}

hdd_support_on() { [ "$(cat "$RUN_DIR/hdd_support" 2>/dev/null)" = true ]; }

lc_hdd_refusal() {
    # $1 = a target disk. 0 = refused, the reason on stdout; 1 = not refused.
    _hr_r=$(cat "${LC:-$RUN_DIR/lc}/inv/$1.rot" 2>/dev/null)
    case "$_hr_r" in
        0) return 1 ;;
        1) ;;
        *) [ -e "${LC:-$RUN_DIR/lc}/inv/$1.rotlog" ] || {
               log "$1: rotational not read (${_hr_r:-no file}) -- not measured; judged as an SSD (X16)" >&2   # stdout is the caller's capture
               : > "${LC:-$RUN_DIR/lc}/inv/$1.rotlog" 2>/dev/null || :   # no marker = the line again, never a lost one
           }
           return 1 ;;
    esac
    hdd_support_on && return 1
    printf '%s' "$HDD_OFF_HE"
}

lc_fit_verdict() {
    # $1 = source, $2 = target: lc_verdict_for, and before it the HDD rule. The word
    # is "short" with the reason -- the screen draws that red, "<reason>. לא ישמש כיעד.",
    # and lc_run refuses it by name (lccopy/localclone.sh); the need stays empty.
    _fv_why=$(lc_hdd_refusal "$2"); _fv_rc=$?
    case "$_fv_rc" in
        0) echo "short||$_fv_why" ;;
        1) lc_verdict_for "$1" "$2" ;;
        *) echo "unchecked||לא הצלחנו לבדוק אם הדיסק הוא HDD" ;;
    esac
}

lc_hdd_gate() {
    # $1 = target, right before its table is written (lc_prepare_target). 0 = go on;
    # 1 = refused: the target is failed by name and nothing was written to it.
    _hg_why=$(lc_hdd_refusal "$1"); _hg_rc=$?
    case "$_hg_rc" in 1) return 0 ;; 0) ;; *) _hg_why="לא הצלחנו לבדוק אם הדיסק הוא HDD" ;; esac
    log "$1: refused as a target -- $_hg_why (hdd_support: $(cat "$RUN_DIR/hdd_support" 2>/dev/null || echo 'none stored'))"
    fail_written_target "$1" "$_hg_why — לא נכתב"; return 1
}
