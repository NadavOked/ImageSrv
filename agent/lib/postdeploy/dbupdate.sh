# dbupdate.sh -- #86: qualify the machine during a restore. When the firmware's
# UEFI db lacks the CA the image's boot loader is signed by (boot_ca_preflight
# said "failed"), append Microsoft's own signed db update, carried beside the
# image (manifest `db_update`), and read db back. POSIX sh (busybox ash).
#
# What may be written: db only, as a signed APPEND, with the payload exactly as
# Microsoft signed it. Never dbx, KEK or PK; never Setup Mode; never a
# signature of ours. Every unclear state ends with NO write and the target
# failing exactly as before (principle 1). Success is the CA read back from db
# (principle 5) -- the write's exit code is logged and is not evidence. Every
# decision, with db before and after, is logged and reported (targets[].db_update).

EFI_GLOBAL_GUID="8be4df61-93ca-11d2-aa0d-00e098032b8c"
DB_UPDATE_KEK_CA="Microsoft Corporation KEK CA 2011"   # the key Microsoft signs db updates with
DB_UPDATE_ATTRS=39                                      # 0x27 NV|BS|RT|TIME_BASED_AUTHENTICATED_WRITE
EFI_PKCS7_GUID_HEX="9dd2af4adf68ee498aa9347d375665a7"   # EFI_CERT_TYPE_PKCS7_GUID, as on disk

_du_list() { # file of names -> "a","b" (JSON array body)
    _dl=""
    while IFS= read -r _dn; do [ -n "$_dn" ] && _dl="$_dl\"$(json_escape "$_dn")\","; done < "$1"
    printf '%s' "${_dl%,}"
}

_du_finish() { # state, reason, disk
    log "db update: $1 -- $2 (db before: $(tr '\n' ';' 2>/dev/null < "$_du_w/before.txt"); after: $(tr '\n' ';' 2>/dev/null < "$_du_w/after.txt"))"
    mkdir -p "$RUN_DIR/targets/$3"
    printf '{"state":"%s","reason":"%s","before":[%s],"after":[%s]}' "$1" "$(json_escape "$2")" \
        "$(_du_list "$_du_w/before.txt" 2>/dev/null)" "$(_du_list "$_du_w/after.txt" 2>/dev/null)" \
        > "$RUN_DIR/targets/$3/db_update.json"
}

_du_wrap() { # PKCS#7 from the payload -> a DER ContentInfo openssl reads. UEFI allows a bare SignedData.
    _dw=$(od -An -N 15 -tx1 "$1" 2>/dev/null | tr -d ' \n')
    case "$_dw" in 3082????06092a864886f70d010702) cp "$1" "$2"; return ;; 3082*) ;; *) return 1 ;; esac
    _dn=$(wc -c < "$1"); _do=$((_dn + 15))   # OID (11 bytes) + [0] header (4) + SignedData
    [ "$_do" -lt 65536 ] || return 1
    # shellcheck disable=SC2059 # the format is built here from fixed octal bytes and two lengths
    { printf "$(printf '\\060\\202\\%03o\\%03o\\006\\011\\052\\206\\110\\206\\367\\015\\001\\007\\002\\240\\202\\%03o\\%03o' \
        $((_do / 256)) $((_do % 256)) $((_dn / 256)) $((_dn % 256)))"; cat "$1"; } > "$2"
}

_du_parse() { # $1 = work dir holding payload.bin (EFI_VARIABLE_AUTHENTICATION_2 + ESL)
    _dp="$1/payload.bin"; _dz=$(wc -c < "$_dp") || return 1
    _dl=$(_bc_u32 "$_dp" 16) || return 1                    # WIN_CERTIFICATE.dwLength
    [ "$(od -An -j 20 -N 4 -tx1 "$_dp" | tr -d ' \n')" = "0002f10e" ] || return 1   # rev 0x0200, WIN_CERT_TYPE_EFI_GUID
    [ "$(od -An -j 24 -N 16 -tx1 "$_dp" | tr -d ' \n')" = "$EFI_PKCS7_GUID_HEX" ] || return 1
    [ "$_dl" -gt 24 ] && [ $((16 + _dl)) -lt "$_dz" ] || return 1
    dd if="$_dp" of="$1/p7.raw" bs=1 skip=40 count=$((_dl - 24)) 2>/dev/null || return 1
    dd if="$_dp" of="$1/esl.bin" bs=1 skip=$((16 + _dl)) 2>/dev/null || return 1
    { printf '\007\000\000\000'; cat "$1/esl.bin"; } > "$1/esl.var" || return 1   # as an efivar, for _bc_x509_subjects
    # What the KEK signed: name "db" (UTF-16, no NUL) || db GUID || attributes with APPEND || timestamp || ESL.
    { printf 'd\000b\000\313\262\031\327\072\075\226\105\243\274\332\320\016\147\145\157\147\000\000\000'
      dd if="$_dp" bs=1 count=16 2>/dev/null; cat "$1/esl.bin"; } > "$1/signed.bin" || return 1
    _du_wrap "$1/p7.raw" "$1/p7.der"
}

_du_verify() { # $1 = work dir: the signature, against the KEK CA certificate this machine's KEK holds
    openssl cms -verify -binary -inform DER -in "$1/p7.der" -content "$1/signed.bin" \
        -CAfile "$1/kek.pem" -partial_chain -no_check_time -purpose any -out /dev/null 2>>"$LOG_FILE"
}

db_update_apply() { # manifest, disk, server URL, image id; 0 = db now holds the image's CA, read back
    _du_d=$2; _du_w="$RUN_DIR/dbupdate"; _du_db="$EFIVARS_ROOT/db-$EFI_DB_GUID"
    rm -rf "$_du_w"; mkdir -p "$_du_w/kek" || { _du_finish refused "אין מקום לעבודה ב-$RUN_DIR" "$_du_d"; return 1; }
    [ "$(jq -c '.secure_boot_db_update' "$RUN_DIR/response.json" 2>/dev/null)" = true ] || {
        _du_finish refused "עדכון ה-db כבוי בהגדרות (או שהשרת לא אמר שהוא דלוק)" "$_du_d"; return 1; }
    { _du_file=$(jq -er '.db_update.file' "$1" 2>/dev/null) && _du_sha=$(jq -er '.db_update.sha256' "$1" 2>/dev/null) \
        && [ "$_du_file" != null ] && [ "$_du_sha" != null ]; } || {
        _du_finish refused "לאימג' אין עדכון db חתום" "$_du_d"; return 1; }
    case "$_du_file" in .|..|*[!A-Za-z0-9._-]*|'') _du_finish refused "שם קובץ עדכון ה-db אינו תקין" "$_du_d"; return 1 ;; esac
    case "$_du_sha" in *[!0-9a-f]*|'') _du_sha=bad ;; esac
    [ "${#_du_sha}" -eq 64 ] || { _du_finish refused "ה-sha256 של עדכון ה-db במניפסט אינו תקין" "$_du_d"; return 1; }
    [ -s "$RUN_DIR/bootca-required.txt" ] || { _du_finish refused "אין רשימת CA של האימג'" "$_du_d"; return 1; }
    _du_sm=$(od -An -j 4 -N 1 -tu1 "$EFIVARS_ROOT/SetupMode-$EFI_GLOBAL_GUID" 2>/dev/null | tr -d ' ')
    [ "$_du_sm" = 0 ] || { _du_finish refused "SetupMode אינו 0 (${_du_sm:-לא נקרא}) -- לא כותבים" "$_du_d"; return 1; }
    _du_a=$(_bc_u32 "$_du_db" 0) || _du_a=""
    [ "$_du_a" = "$DB_UPDATE_ATTRS" ] || { _du_finish refused "מאפייני db אינם 0x27 (${_du_a:-לא נקרא}) -- לא כותבים" "$_du_d"; return 1; }
    _bc_x509_subjects "$_du_db" > "$_du_w/before.txt" || { _du_finish refused "לא ניתן לפענח את UEFI db" "$_du_d"; return 1; }
    _bc_x509_subjects "$EFIVARS_ROOT/KEK-$EFI_GLOBAL_GUID" "$_du_w/kek" > /dev/null || {
        _du_finish refused "לא ניתן לקרוא או לפענח את KEK" "$_du_d"; return 1; }
    _du_k=""
    for _du_c in "$_du_w"/kek/*.cn; do grep -Fqx "$DB_UPDATE_KEK_CA" "$_du_c" 2>/dev/null && _du_k=${_du_c%.cn}.der; done
    [ -n "$_du_k" ] || { _du_finish refused "ב-KEK אין $DB_UPDATE_KEK_CA -- הקושחה לא תסמוך על העדכון" "$_du_d"; return 1; }
    openssl x509 -inform DER -in "$_du_k" -out "$_du_w/kek.pem" 2>>"$LOG_FILE" || {
        _du_finish refused "לא ניתן לקרוא את תעודת ה-KEK" "$_du_d"; return 1; }
    http_get "$3/api/v1/images/$4/files/$_du_file" > "$_du_w/payload.bin" 2>>"$LOG_FILE" || {
        _du_finish refused "עדכון ה-db לא הורד מהשרת" "$_du_d"; return 1; }
    _du_got=$(sha256sum "$_du_w/payload.bin" 2>/dev/null | awk '{print $1}')
    [ "$_du_got" = "$_du_sha" ] || { _du_finish refused "ה-sha256 של עדכון ה-db שהורד אינו זה שבמניפסט" "$_du_d"; return 1; }
    _du_parse "$_du_w" || { _du_finish refused "קובץ עדכון ה-db אינו משתנה מאומת חתום (EFI_VARIABLE_AUTHENTICATION_2)" "$_du_d"; return 1; }
    _bc_x509_subjects "$_du_w/esl.var" > "$_du_w/adds.txt" || { _du_finish refused "לא ניתן לפענח את התעודות שבעדכון" "$_du_d"; return 1; }
    _du_ca=""
    while IFS= read -r _du_c; do grep -Fqx "$_du_c" "$_du_w/adds.txt" && { _du_ca=$_du_c; break; }; done < "$RUN_DIR/bootca-required.txt"
    [ -n "$_du_ca" ] || { _du_finish refused "העדכון אינו מוסיף את ה-CA שהאימג' חתום בו" "$_du_d"; return 1; }
    _du_verify "$_du_w" || { _du_finish refused "החתימה על העדכון לא אומתה מול $DB_UPDATE_KEK_CA שב-KEK" "$_du_d"; return 1; }
    { printf 'g\000\000\000'; cat "$_du_w/payload.bin"; } > "$_du_w/write.bin" || {   # 0x67 = 0x27 | APPEND_WRITE
        _du_finish refused "לא ניתן להכין את הכתיבה" "$_du_d"; return 1; }
    chattr -i "$_du_db" 2>>"$LOG_FILE" || { _du_finish refused "לא ניתן להסיר את ההגנה מ-db (chattr -i)" "$_du_d"; return 1; }
    _du_z=$(wc -c < "$_du_w/write.bin")
    log "db update: appending $_du_ca ($_du_z bytes, signed by Microsoft) to $_du_db"
    dd if="$_du_w/write.bin" of="$_du_db" bs="$_du_z" count=1 conv=notrunc 2>>"$LOG_FILE"; _du_rc=$?   # one write(): efivarfs takes a variable whole
    chattr +i "$_du_db" 2>>"$LOG_FILE" || log "db update: WARNING: chattr +i on db failed"
    log "db update: the write returned $_du_rc -- not evidence; reading db back"
    _du_a=$(_bc_u32 "$_du_db" 0) || _du_a=""
    if [ "$_du_a" != "$DB_UPDATE_ATTRS" ] || ! _bc_x509_subjects "$_du_db" > "$_du_w/after.txt"; then
        _du_finish failed "db לא נקרא בחזרה אחרי הכתיבה (הכתיבה החזירה $_du_rc)" "$_du_d"; return 1
    fi
    grep -Fqx "$_du_ca" "$_du_w/after.txt" || { _du_finish failed "db נקרא בחזרה ואין בו $_du_ca (הכתיבה החזירה $_du_rc)" "$_du_d"; return 1; }
    while IFS= read -r _du_c; do
        grep -Fqx "$_du_c" "$_du_w/after.txt" || { _du_finish failed "ב-db שנקרא בחזרה חסרה תעודה שהייתה בו: $_du_c" "$_du_d"; return 1; }
    done < "$_du_w/before.txt"
    _du_finish applied "נוסף ל-UEFI db: $_du_ca (העדכון החתום של מיקרוסופט, נקרא בחזרה)" "$_du_d"
    _bc_finish ok "ה-CA של האימג' נוסף ל-UEFI db ונקרא בחזרה: $_du_ca" "$_du_d"
}
