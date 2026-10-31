#!/bin/sh
# Offline Windows local-account recovery (#649, domain 4).
#
# #1050: only the two ids Nadav chose in server/tools_catalog.json remain
# (win-users, win-blank-password); the six offline diagnostics of the
# original module were removed, not hidden.

tools_windows_list() {
    cat <<'EOF'
win-users|windows|רשימת משתמשי Windows|ro|SAM path
win-blank-password|windows|איפוס סיסמה מקומית|destroy|SAM:path:user
EOF
}

_win_out() { printf '%s/tool-%s.out' "${RUN_DIR:?RUN_DIR is required}" "$1"; }
_win_meaning() { printf '\nמה זה אומר: %s\n' "$1"; }
_win_missing() { printf 'לא ניתן לבדוק: הכלי %s חסר.\n' "$1"; _win_meaning 'הבדיקה לא בוצעה; אין כאן תוצאת הצלחה.'; }
_win_need() { command -v "$1" >/dev/null 2>&1 || { _win_missing "$1"; return 2; }; }
_win_confirm() {
    _expected=${MACHINE_NAME:-$(hostname 2>/dev/null)}
    [ -n "$_expected" ] && [ "$1" = "$_expected" ] && return 0
    printf 'סירוב: פעולה הרסנית; יש להקליד את שם המכונה "%s".\n' "$_expected"
    _win_meaning 'דבר לא נמח ולא שונה.'
    return 3
}
_win_file() { [ -f "$1" ] || { printf 'לא ניתן לבדוק: הקובץ לא נמצא: %s\n' "$1"; _win_meaning 'נדרש נתיב אחר או פתיחת הצפנה.'; return 2; }; }

_win_users() {
    _win_need chntpw || return 2; _win_file "$1" || return 2
    printf '=== משתמשים מקומיים ב-Windows ===\nSAM: %s\n' "$1"
    chntpw -l "$1" 2>&1 || { _win_meaning 'קריאת SAM נכשלה; לא ניתן לקבוע אילו משתמשים קיימים.'; return 2; }
    _win_meaning 'אלה חשבונות SAM מקומיים; Microsoft Account, PIN ו-Windows Hello אינם סיסמאות SAM.'
}
_win_blank() {
    _spec=$1; _confirm=$2; _sam=${_spec%%:*}; _user=${_spec#*:}
    [ "$_sam" != "$_spec" ] && [ -n "$_user" ] || { printf 'לא ניתן לבצע: נדרש SAM:path:user.\n'; _win_meaning 'שום דבר לא שונה.'; return 2; }
    _win_confirm "$_confirm" || return 3; _win_need chntpw || return 2; _win_file "$_sam" || return 2
    printf '=== איפוס סיסמה מקומית ===\nאזהרה: הסיסמה המקומית של "%s" תימחק מהקובץ %s.\n' "$_user" "$_sam"
    printf '1\ny\n' | chntpw -u "$_user" "$_sam" 2>&1 || { _win_meaning 'הכתיבה ל-SAM נכשלה או לא אומתה.'; return 2; }
    _win_meaning 'הכלי דיווח שסיסמת SAM המקומית נמחקה; זה לא מאפס Microsoft Account, PIN או Hello.'
}

tools_windows_run() {
    _id=$1; _arg=${2-}; _confirm=${3-}; mkdir -p "$RUN_DIR"; _out=$(_win_out "$_id")
    case "$_id" in
        win-users) _win_users "$_arg" >"$_out" 2>&1 ;;
        win-blank-password) _win_blank "$_arg" "$_confirm" >"$_out" 2>&1 ;;
        *) printf 'לא ניתן לבצע: כלי Windows לא מוכר.\n' >"$_out"; _win_meaning 'לא בוצעה פעולה.' >>"$_out"; return 2 ;;
    esac
}
