# bootcadb.sh -- compare an image boot CA with the target UEFI db (#672).
# POSIX sh (busybox ash). EFI variables include a four-byte attributes prefix.

EFIVARS_ROOT="${EFIVARS_ROOT:-${SYSROOT:-}/sys/firmware/efi/efivars}"
EFI_DB_GUID="d719b2cb-3d3a-4596-a3bc-dad00e67656f"
EFI_X509_GUID_HEX="a159c0a5e494a74a87b5ab155c2bf072"
BOOT_CA_PREFLIGHT_STATE=""
BOOT_CA_PREFLIGHT_REASON=""

_bc_u32() { # file, byte offset -- little-endian uint32
    # shellcheck disable=SC2046 # word splitting is the point: four bytes -> $1..$4
    set -- $(od -An -j "$2" -N 4 -tu1 "$1" 2>/dev/null) || return 1
    [ "$#" -eq 4 ] || return 1
    echo $(($1 + ($2 * 256) + ($3 * 65536) + ($4 * 16777216)))
}

_bc_x509_subjects() { # db variable; one CN per X.509 EFI signature
    _bc_file=$1; _bc_size=$(wc -c < "$_bc_file" 2>/dev/null) || return 1
    case "$_bc_size" in ''|*[!0-9]*) return 1 ;; esac
    [ "$_bc_size" -ge 32 ] || return 1
    _bc_at=4; _bc_left=128
    while [ "$_bc_at" -lt "$_bc_size" ]; do
        [ "$_bc_left" -gt 0 ] || return 1
        _bc_left=$((_bc_left - 1))
        _bc_list=$(_bc_u32 "$_bc_file" $((_bc_at + 16))) || return 1
        _bc_head=$(_bc_u32 "$_bc_file" $((_bc_at + 20))) || return 1
        _bc_sig=$(_bc_u32 "$_bc_file" $((_bc_at + 24))) || return 1
        [ "$_bc_list" -ge 28 ] && [ "$_bc_sig" -gt 16 ] || return 1
        _bc_end=$((_bc_at + _bc_list)); [ "$_bc_end" -le "$_bc_size" ] || return 1
        _bc_data=$((_bc_at + 28 + _bc_head)); [ "$_bc_data" -le "$_bc_end" ] || return 1
        _bc_guid=$(od -An -j "$_bc_at" -N 16 -tx1 "$_bc_file" 2>/dev/null | tr -d ' \n')
        if [ "$_bc_guid" = "$EFI_X509_GUID_HEX" ]; then
            [ $(((_bc_end - _bc_data) % _bc_sig)) -eq 0 ] || return 1
            while [ "$_bc_data" -lt "$_bc_end" ]; do
                _bc_len=$((_bc_sig - 16)); [ "$_bc_len" -gt 0 ] || return 1
                dd if="$_bc_file" of="$RUN_DIR/bootca-db.der" bs=1 \
                    skip=$((_bc_data + 16)) count="$_bc_len" 2>/dev/null || return 1
                openssl x509 -inform DER -in "$RUN_DIR/bootca-db.der" \
                    -noout -subject -nameopt RFC2253 \
                    > "$RUN_DIR/bootca-db-subject.txt" 2>/dev/null || return 1
                sed -n 's/^subject=//; s/^.*CN=\([^,]*\).*$/\1/p' \
                    "$RUN_DIR/bootca-db-subject.txt"
                _bc_data=$((_bc_data + _bc_sig))
            done
        fi
        _bc_at=$_bc_end
    done
    [ "$_bc_at" -eq "$_bc_size" ]
}

_bc_finish() { # state, reason, target disk (optional)
    BOOT_CA_PREFLIGHT_STATE=$1; BOOT_CA_PREFLIGHT_REASON=$2
    log "boot CA preflight: $1 -- $2"
    if [ -n "${3:-}" ]; then
        mkdir -p "$RUN_DIR/targets/$3"
        printf '%s' "$1" > "$RUN_DIR/targets/$3/boot_ca_state"
        printf '%s' "$2" > "$RUN_DIR/targets/$3/boot_ca_reason"
    fi
}

boot_ca_preflight() { # manifest, target disk; 0=write, 1=known mismatch
    _bc_manifest=$1; _bc_target=${2:-}
    _bc_sb=$(find "$EFIVARS_ROOT" -maxdepth 1 -name 'SecureBoot-*' 2>/dev/null | head -1)
    if [ ! -d "$EFIVARS_ROOT" ] || [ -z "$_bc_sb" ]; then
        _bc_finish unchecked "לא ניתן לקרוא efivars (UEFI לא זמין או BIOS ישן)" "$_bc_target"; return 0
    fi
    _bc_enabled=$(od -An -j 4 -N 1 -tu1 "$_bc_sb" 2>/dev/null | tr -d ' ')
    case "$_bc_enabled" in
        0) _bc_finish ok "Secure Boot כבוי; התאמת CA אינה נדרשת" "$_bc_target"; return 0 ;;
        1) ;;
        *) _bc_finish unchecked "לא ניתן לקרוא את מצב Secure Boot" "$_bc_target"; return 0 ;;
    esac
    _bc_required="$RUN_DIR/bootca-required.txt"
    jq -er '.boot_ca | arrays | .[] | strings' "$_bc_manifest" > "$_bc_required" 2>/dev/null || {
        _bc_finish unchecked "לאימג' אין boot_ca שניתן להשוות" "$_bc_target"; return 0; }
    [ -s "$_bc_required" ] || {
        _bc_finish unchecked "רשימת boot_ca של האימג' ריקה" "$_bc_target"; return 0; }
    _bc_db="$EFIVARS_ROOT/db-$EFI_DB_GUID"
    _bc_subjects="$RUN_DIR/bootca-db-subjects.txt"
    if [ ! -r "$_bc_db" ] || ! _bc_x509_subjects "$_bc_db" > "$_bc_subjects"; then
        _bc_finish unchecked "לא ניתן לקרוא או לפענח את UEFI db" "$_bc_target"; return 0
    fi
    while IFS= read -r _bc_ca; do
        grep -Fqx "$_bc_ca" "$_bc_subjects" && {
            _bc_finish ok "ה-CA של האימג' נמצא ב-UEFI db: $_bc_ca" "$_bc_target"; return 0; }
    done < "$_bc_required"
    _bc_finish failed "המחשב לא יאמין לאימג' — Secure Boot יחסום אתחול" "$_bc_target"
    return 1
}
