# tools_hw.sh -- hardware and platform-security toolbox. POSIX sh.
#
# #1050: only tpm-clear -- the one id of this domain that Nadav chose in
# server/tools_catalog.json -- remains. The fourteen read-only inventory
# tools of the original module were removed, not hidden (the machine card,
# #1049, is where that inventory goes).

tools_hw_list() {
    cat <<'EOF'
tpm-clear|hw|מחיקת בעלות ומפתחות TPM|destroy|machine-name confirmation
EOF
}

_hw_out() { printf '%s/tool-%s.out\n' "$RUN_DIR" "$1"; }
_hw_meaning() { printf '\nמה זה אומר: %s\n' "$1"; }
_hw_missing() {
    printf 'לא ניתן לבדוק: הכלי %s אינו קיים ב-initramfs.\n' "$2" >> "$(_hw_out "$1")"
    _hw_meaning 'הבדיקה לא בוצעה; אין להסיק שהחומרה תקינה.' >> "$(_hw_out "$1")"
    return 2
}
_hw_need() { command -v "$2" >/dev/null 2>&1 || _hw_missing "$1" "$2"; }
_hw_failed() {
    printf '\nלא ניתן לבדוק: הפקודה נכשלה (rc=%s).\n' "$2" >> "$(_hw_out "$1")"
    _hw_meaning 'אין תוצאה מאומתת; יש לבדוק את הודעת השגיאה.' >> "$(_hw_out "$1")"
    return 2
}

tools_hw_run() {
    _id=$1; _arg=${2:-}; _confirm=${3:-}; mkdir -p "$RUN_DIR" || return 2
    case "$_id" in
        tpm-clear)
            _out=$(_hw_out tpm-clear); printf '=== מחיקת TPM ===\n' > "$_out"
            printf 'יימחקו בעלות ה-TPM, מפתחות ונתונים חתומים התלויים בו; BitLocker עלול לדרוש מפתח שחזור.\n' >> "$_out"
            _name=${MACHINE_NAME:-$(hostname 2>/dev/null)}
            [ -n "$_name" ] && [ "$_confirm" = "$_name" ] || { _hw_meaning 'הפעולה סורבה; דבר לא נמחק.' >> "$_out"; return 3; }
            _hw_need tpm-clear tpm2_clear || return 2
            tpm2_clear >> "$_out" 2>&1 || { _rc=$?; _hw_failed tpm-clear "$_rc"; return 2; }
            _hw_meaning 'פקודת מחיקת ה-TPM הסתיימה בהצלחה; יש לאמת את מצב ה-TPM מחדש לאחר אתחול.' >> "$_out" ;;
        *) return 2 ;;
    esac
}
