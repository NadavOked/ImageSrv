#!/bin/sh
# gui-bridge.sh -- the install GUI's only door to the engine (#1190 part 2).
# POSIX sh (busybox ash). The native GUI fork/execs this with ONE action in
# argv and a JSON object on stdin (the form as the person filled it), and
# reads key=value lines from stdout -- the same exchange main.c already
# uses for --auth-cmd. Exit 0 = the call itself worked (validation errors are
# data: errors.<field>=...); non-zero = the bridge could not do its job, and
# the GUI says "the installer is not available (<action>)", never "ok".
#
#   inventory      disk= lines (imagectl-install --inventory) + nic= lines
#                  + virt= and raid_default= (#1989)
#   validate       the form (+ step) -> errors.<field>=... or nothing
#   apply          writes the answers (0600), starts the engine, job=
#   progress       the engine's state file, plus output= for the log tail
#   check-primary  GET https://<primary>:8443/api/interserver/v1/alive (#1310);
#                  green only with name + fingerprint of the key TLS showed
#   finish         what the done screen shows after a reboot-less handoff
#   eject          eject the install medium (reported, never fatal)
#   reboot         reboot -f; only the GUI's "restart" (button/countdown, #1250)
#   boot-local     eject + reboot, for a disk that already carries ImageCtl
#
# No jq-less JSON parsing: jq is in the image (agent/ uses it). Every value
# the GUI sends is read with `jq -r`, never eval'd.
set -u
LC_ALL=C; export LC_ALL

case "$0" in */*) BRIDGE_DIR=${0%/*} ;; *) BRIDGE_DIR=. ;; esac
BRIDGE_DIR=$(CDPATH="" cd "$BRIDGE_DIR" && pwd) || exit 2
ENGINE="${ENGINE:-$BRIDGE_DIR/imagectl-install}"
RUN_DIR="${RUN_DIR:-/run/imagectl}"
STATE_FILE="${STATE_FILE:-$RUN_DIR/install.state}"
ANSWERS="${ANSWERS:-$RUN_DIR/answers}"
ENGINE_LOG="${ENGINE_LOG:-$RUN_DIR/install.log}"
DHCP_DIR="${DHCP_DIR:-$RUN_DIR/dhcp}"
SYSROOT="${SYSROOT:-}"
MEDIA_FILE="${MEDIA_FILE:-$RUN_DIR/install-media}"
# #1310: the last GREEN check (url/name/version/fingerprint). Only this file
# puts primary_fingerprint into the answers -- never the form the GUI sent.
PRIMARY_CHECK="${PRIMARY_CHECK:-$RUN_DIR/primary-check}"
PRIMARY_ALIVE_PATH=/api/interserver/v1/alive
# shellcheck source=installer/lib/answers.sh
. "$BRIDGE_DIR/lib/answers.sh"   # normalize_primary_url -- one rule, engine and GUI
# shellcheck source=installer/lib/fielderrors.sh
. "$BRIDGE_DIR/lib/fielderrors.sh"   # field_for_code, he_for_code (#1503)
# shellcheck source=installer/lib/raid.sh
. "$BRIDGE_DIR/lib/raid.sh"          # virt_kind, raid_default, raid_eligible_count (#1989)

ACTION=${1:-}; [ -n "$ACTION" ] || { echo 'usage: gui-bridge.sh <action> < form.json' >&2; exit 2; }
INPUT=$(cat)
[ -n "$INPUT" ] || INPUT='{}'
command -v jq >/dev/null 2>&1 || { echo 'gui-bridge: jq is missing from this image' >&2; exit 3; }

field() { printf '%s' "$INPUT" | jq -r --arg k "$1" '.[$k] // "" | tostring' 2>/dev/null; }
# One line per key, always: a value never carries a line break to the GUI (#1310).
emit() { printf '%s=%s\n' "$1" "$(printf '%s' "$2" | tr '\r\n' '  ')"; }

# --- NIC rows: name|model|mac|link|current|source|address|netmask|gateway|dns --
nic_model() {   # #2096: a readable name, never "VMware 0x07b0"; unknown = empty -> "כרטיס רשת N" on screen
    _nm_dev="$SYSROOT/sys/class/net/$1/device"
    [ -r "$_nm_dev/vendor" ] && [ -r "$_nm_dev/device" ] || return 0
    _nm_v=$(cat "$_nm_dev/vendor"); _nm_d=$(cat "$_nm_dev/device")
    case "$_nm_v:$_nm_d" in
        0x15ad:0x07b0) printf 'VMware VMXNET3' ;; 0x15ad:*) printf 'VMware' ;; 0x1414:*) printf 'Hyper-V' ;;
        0x8086:0x100e|0x8086:0x100f) printf 'Intel E1000' ;; 0x8086:0x10d3) printf 'Intel 82574L' ;; 0x8086:*) printf 'Intel' ;;
        0x10ec:0x8168) printf 'Realtek RTL8111' ;; 0x10ec:0x8125) printf 'Realtek RTL8125' ;; 0x10ec:*) printf 'Realtek' ;;
        0x14e4:*) printf 'Broadcom' ;; 0x1af4:*) printf 'virtio' ;;
    esac
}

nic_rows() {
    for _d in "$SYSROOT"/sys/class/net/*; do
        _n=${_d##*/}; [ "$_n" = lo ] && continue
        _mac=$(cat "$_d/address" 2>/dev/null || printf '')
        _oper=$(cat "$_d/operstate" 2>/dev/null || printf 'unknown')
        _carrier=$(cat "$_d/carrier" 2>/dev/null || printf '')
        # #1183 wording: down = not brought up, no carrier = no cable, else connected
        if [ "$_oper" = down ]; then _link='לא מודלק'
        elif [ "$_carrier" = 1 ]; then _link='מחובר'
        else _link='אין קישור'; fi
        _addr=; _gw=; _dns=; _state=
        if [ -r "$DHCP_DIR/$_n" ]; then
            _state=$(sed -n 's/^state=//p' "$DHCP_DIR/$_n" | head -n1)
            _addr=$(sed -n 's/^address=//p' "$DHCP_DIR/$_n" | head -n1)
            _gw=$(sed -n 's/^gateway=//p' "$DHCP_DIR/$_n" | head -n1)
            _dns=$(sed -n 's/^dns=//p' "$DHCP_DIR/$_n" | head -n1)
        fi
        if [ -z "$_addr" ]; then
            _addr=$(ip -o -4 addr show dev "$_n" scope global 2>/dev/null | sed -n 's/.* inet \([0-9./]*\).*/\1/p' | head -n1)
        fi
        case "$_state" in
            lease) _current=$_addr; _source="DHCP · שער $_gw" ;;
            no-offer) _current='—'; _source='אין כתובת (אין DHCP ברשת הזו)' ;;
            no-carrier) _current='—'; _source='—' ;;
            *) if [ -n "$_addr" ]; then _current=$_addr; _source='כתובת קיימת'; else _current='—'; _source='—'; fi ;;
        esac
        _ip=${_addr%%/*}; _mask=
        case "$_addr" in */24) _mask=255.255.255.0 ;; */16) _mask=255.255.0.0 ;; */8) _mask=255.0.0.0 ;; */22) _mask=255.255.252.0 ;; */23) _mask=255.255.254.0 ;; */25) _mask=255.255.255.128 ;; esac
        emit nic "$_n|$(nic_model "$_n")|$_mac|$_link|$_current|$_source|$_ip|$_mask|$_gw|$_dns"
    done
}

# --- the form -> an answers file (never printed; the password stays in the file) --
write_answers() {
    _wa_out=$1; _wa_if=$(field interface); _wa_mac=
    [ -n "$_wa_if" ] && _wa_mac=$(cat "$SYSROOT/sys/class/net/$_wa_if/address" 2>/dev/null || printf '')
    _wa_user=$(field admin_user); [ -n "$_wa_user" ] || _wa_user="admin"
    umask 077
    {
        emit disk "$(field disk)"
        emit role "$(field role)"
        _wa_purl=; _wa_pfp=
        if [ "$(field role)" = secondary ]; then
            _wa_purl=$(normalize_primary_url "$(field primary_url)") || _wa_purl=$(field primary_url)
            # The record is exactly two lines, url= then fingerprint=; an extra
            # or repeated key is not a green check (#1310).
            [ "$(wc -l < "$PRIMARY_CHECK" 2>/dev/null)" = 2 ] && [ "$(sed -n 1p "$PRIMARY_CHECK")" = "url=$_wa_purl" ] \
                && _wa_pfp=$(sed -n '2s/^fingerprint=\([0-9a-f]\{64\}\)$/\1/p' "$PRIMARY_CHECK")
        else
            _wa_purl=$(field primary_url)
        fi
        emit primary_url "$_wa_purl"
        emit primary_fingerprint "$_wa_pfp"
        emit servers_if "$_wa_if"
        emit servers_mac "$_wa_mac"
        emit servers_mode "$(field mode)"
        if [ "$(field mode)" = static ]; then
            emit servers_addr "$(field address)"; emit servers_mask "$(field netmask)"
            emit servers_gw "$(field gateway)"; emit servers_dns "$(field dns)"
        else
            emit servers_addr ''; emit servers_mask ''; emit servers_gw ''; emit servers_dns ''
        fi
        emit hostname "$(field hostname)"
        emit timezone "$(field timezone)"
        emit admin_user "$_wa_user"
        # Before the admin screen the password is legitimately empty; the engine's
        # own check ("admin_pass must not be empty") belongs to step 4+, not to
        # a disk/role/network validation. A placeholder keeps the dry-run honest
        # for every other field; apply always sends the real one.
        secret_answer admin_pass password; secret_answer root_pass root_password   # #1632: root's too
        # #1989: the GUI's RAID choice; a form without one is "no RAID".
        _wa_raid=$(field raid_level); _wa_spares=$(field raid_spares)
        emit raid_level "${_wa_raid:-none}"; emit raid_disks "$(field raid_disks)"; emit raid_spares "${_wa_spares:-0}"
    } > "$_wa_out"
}

secret_answer() { if [ -n "$(field "$2")" ] || [ "${_wa_placeholder:-0}" = 0 ]; then emit "$1" "$(field "$2")"; else emit "$1" "not-yet-asked-1!"; fi; }
pw_errors() {   # #1632: one rule for both passwords -- the field, its confirmation, the mismatch line
    _pe_p=$(field "$1"); _pe_c=$(field "$2"); _pe_bad=0
    [ "${#_pe_p}" -ge 8 ] || { emit "errors.$1" 'לפחות 8 תווים'; _pe_bad=1; }
    case "$_pe_p" in *[A-Za-z]*) ;; *) emit "errors.$1" 'נדרשת לפחות אות אחת'; _pe_bad=1 ;; esac
    case "$_pe_p" in *[0-9]*) ;; *) emit "errors.$1" 'נדרשת לפחות ספרה אחת'; _pe_bad=1 ;; esac
    case "$_pe_p" in *[!A-Za-z0-9]*) ;; *) emit "errors.$1" 'נדרש לפחות תו מיוחד אחד'; _pe_bad=1 ;; esac
    [ "$_pe_p" = "$_pe_c" ] || { emit "errors.$2" "$3"; _pe_bad=1; }
    return $_pe_bad
}
password_errors() { pw_errors password password_confirm 'הסיסמאות שהזנת אינן תואמות'; _pr_bad=$?
    pw_errors root_password root_password_confirm 'סיסמאות ה-root שהזנת אינן תואמות' || _pr_bad=1; return $_pr_bad; }

# Run the engine's own validation (--dry-run: no command touches a disk) on
# the form as an answers file; the first failure names the field.
engine_validate() {
    _ev_tmp="$RUN_DIR/validate.$$"; write_answers "$_ev_tmp"
    _ev_err=$("$ENGINE" "$_ev_tmp" --dry-run --state "$RUN_DIR/validate.state.$$" 2>&1 >/dev/null); _ev_rc=$?
    rm -f "$_ev_tmp" "$RUN_DIR/validate.state.$$"
    [ "$_ev_rc" -eq 0 ] && return 0
    _ev_code=$(printf '%s\n' "$_ev_err" | sed -n 's/^installer: \[\([a-z-]*\)\].*/\1/p' | head -n1)
    _ev_msg=$(printf '%s\n' "$_ev_err" | sed -n 's/^installer: \[[a-z-]*\] //p' | head -n1)
    [ -n "$_ev_msg" ] || _ev_msg=$(printf '%s\n' "$_ev_err" | tail -n1)
    printf 'gui-bridge: validation [%s] %s\n' "${_ev_code:-unknown}" "$_ev_msg" >&2   # the English line is the log
    emit "errors.$(field_for_code "${_ev_code:-unknown}")" "$(he_for_code "${_ev_code:-unknown}")"
    return 1
}

do_inventory() {
    _di_inv=$("$ENGINE" --inventory) || { echo 'gui-bridge: engine inventory failed' >&2; return 4; }
    printf '%s
' "$_di_inv"
    nic_rows
    # #1989: virtual servers get no RAID (the host does it); metal gets the
    # default for its count of suitable disks (2 -> raid1, 3 -> raid5, 4+ -> raid10).
    _di_virt=$(virt_kind); _di_n=$(printf '%s
' "$_di_inv" | raid_eligible_count)
    emit virt "$_di_virt"; emit raid_default "$(raid_default "$_di_n" "$_di_virt")"
    _di_role=standalone; _di_if=
    for _d in "$SYSROOT"/sys/class/net/*; do _n=${_d##*/}; [ "$_n" = lo ] && continue
        [ -r "$DHCP_DIR/$_n" ] && grep -q '^state=lease$' "$DHCP_DIR/$_n" && { _di_if=$_n; break; }; done
    emit role "$_di_role"; emit primary_url ''; emit interface "$_di_if"; emit mode dhcp
    emit hostname imagesrv-server; emit timezone Asia/Jerusalem; emit admin_user admin; emit rerun 0
}

do_validate() {
    _dv_step=$(field step); _dv_bad=0
    case "$_dv_step" in
        ''|5) engine_validate || _dv_bad=1; password_errors || _dv_bad=1 ;;
        4) password_errors || _dv_bad=1 ;;
        *) _wa_placeholder=1; engine_validate || _dv_bad=1; _wa_placeholder=0 ;;
    esac
    return 0
}

do_apply() {
    if [ -s "$STATE_FILE" ] && grep -qE '^state=(partitioning|bootstrap|packages|bootloader|finishing)$' "$STATE_FILE"; then
        emit job running; return 0
    fi
    engine_validate || return 0
    password_errors || return 0
    mkdir -p "$RUN_DIR" && write_answers "$ANSWERS" || { echo 'gui-bridge: cannot write the answers' >&2; return 4; }
    printf 'state=partitioning\npct=0\ntitle=starting\n' > "$STATE_FILE"
    ( "$ENGINE" "$ANSWERS" --state "$STATE_FILE" > "$ENGINE_LOG" 2>&1 ) &
    emit job "$!"
}

do_progress() {
    [ -r "$STATE_FILE" ] || { emit state ready; return 0; }
    cat "$STATE_FILE"
    [ -r "$ENGINE_LOG" ] && tail -n 20 "$ENGINE_LOG" | sed 's/\x1b\[[0-9;]*m//g; s/^/output=/'
    return 0
}

# #1310 (Nadav 12/10, option A): the secondary's one call to the primary --
# "are you there?". Green = HTTP 200 + exactly {name, version, fingerprint}
# + that fingerprint is the SHA-256 of the key TLS showed. Anything else is
# red with its reason, never green (principle 5); each check first forgets
# the previous green.
check_fail() { rm -f "$_cp_tmp".*; emit ok 0; emit error "$1"; return 0; }

do_check_primary() {
    rm -f "$PRIMARY_CHECK"
    _cp_url=$(normalize_primary_url "$(field primary_url)") || {
        emit ok 0; emit error 'כתובת לא תקינה: הקלד את כתובת ה-IP (או השם) של השרת הראשי'; return 0; }
    _cp_host=${_cp_url#https://}; _cp_host=${_cp_host%:8443}
    mkdir -p "$RUN_DIR" || { echo 'gui-bridge: cannot create the run dir' >&2; return 4; }
    _cp_tmp="$RUN_DIR/check.$$"
    curl -k -sS --max-time 5 -o "$_cp_tmp.body" -w '%{http_code}\n%{certs}' \
        "$_cp_url$PRIMARY_ALIVE_PATH" > "$_cp_tmp.meta" 2> "$_cp_tmp.err"
    _cp_rc=$?
    case "$_cp_rc" in
        0) _cp_why= ;;
        6) _cp_why="השם $_cp_host לא נפתר לכתובת (DNS). הקלד את כתובת ה-IP של השרת הראשי" ;;
        7) _cp_why="אין נתיב אל $_cp_host בפורט 8443: בדוק את הכתובת, ושחומת האש של אתר זה מתירה יציאה אליה"
           grep -qi refused "$_cp_tmp.err" && _cp_why="$_cp_host דחה את החיבור בפורט 8443 (refused): השרת הראשי אינו מאזין שם, או שחומת האש של האתר הראשי אינה מעבירה את 8443 אליו" ;;
        28) _cp_why="אין תשובה מ-$_cp_host בפורט 8443 תוך 5 שניות (timeout): חומת אש חוסמת בשקט, או שהכתובת שגויה" ;;
        35|53|54|58|59|60|66|80|83|90|91) _cp_why="מו\"מ ה-TLS עם $_cp_host:8443 נכשל (curl $_cp_rc): בפורט הזה עונה משהו שאינו השרת הראשי" ;;
        52|56) _cp_why="$_cp_host:8443 סגר את החיבור בלי תשובה: זה אינו שרת ImageSrv ראשי" ;;
        *) _cp_why="בדיקת החיבור אל $_cp_host:8443 נכשלה (curl $_cp_rc)" ;;
    esac
    [ -z "$_cp_why" ] || { check_fail "$_cp_why"; return 0; }
    _cp_code=$(head -n1 "$_cp_tmp.meta")
    [ "$_cp_code" = 200 ] || { check_fail "$_cp_host:8443 ענה HTTP $_cp_code: זה אינו שרת ImageSrv ראשי (או גרסה ישנה בלי בדיקת חיבור)"; return 0; }
    jq -e '(type | IN("object")) and (keys | IN(["fingerprint", "name", "version"]))
           and (.name | type | IN("string")) and (.name | test("^.{1,63}$") and (test("[[:cntrl:]]") | not))
           and (.fingerprint | type | IN("string")) and (.fingerprint | test("^[0-9a-f]{64}$"))
           and (.version | type | IN("string", "null")) and ((.version // "v") | test("^[A-Za-z0-9._+-]{1,64}$"))' "$_cp_tmp.body" >/dev/null 2>&1 \
        || { check_fail "$_cp_host:8443 ענה, אבל לא בתשובת השרת הראשי (name/version/fingerprint): זה אינו שרת ImageSrv ראשי"; return 0; }
    _cp_name=$(jq -r '.name' "$_cp_tmp.body"); _cp_ver=$(jq -r '.version // ""' "$_cp_tmp.body")
    _cp_fp=$(jq -r '.fingerprint' "$_cp_tmp.body")
    # The key TLS really showed (the first certificate curl printed). Each
    # step is checked: a failed openssl must not hash an empty input.
    sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' "$_cp_tmp.meta" > "$_cp_tmp.pem"
    openssl x509 -in "$_cp_tmp.pem" -pubkey -noout > "$_cp_tmp.pub" 2> "$_cp_tmp.err" \
        && openssl pkey -pubin -in "$_cp_tmp.pub" -outform DER -out "$_cp_tmp.der" 2> "$_cp_tmp.err" \
        && _cp_seen=$(sha256sum < "$_cp_tmp.der") \
        || { check_fail "לא ניתן לקרוא את התעודה ש-$_cp_host:8443 הציג (openssl), ולכן החיבור לא אומת"; return 0; }
    _cp_seen=${_cp_seen%% *}
    [ "${#_cp_seen}" -eq 64 ] && [ "$_cp_seen" = "$_cp_fp" ] \
        || { check_fail "טביעת האצבע בתשובה אינה של התעודה ש-$_cp_host:8443 הציג: זה אינו השרת הראשי"; return 0; }
    printf 'url=%s\nfingerprint=%s\n' "$_cp_url" "$_cp_fp" > "$PRIMARY_CHECK" \
        || { check_fail 'לא ניתן לשמור את תוצאת הבדיקה'; return 0; }
    rm -f "$_cp_tmp".*
    emit ok 1; emit name "$_cp_name"; emit version "$_cp_ver"; emit fingerprint "$_cp_fp"
}

do_finish() {
    [ -r "$STATE_FILE" ] && cat "$STATE_FILE" || emit state ready
    _df_if=$(sed -n 's/^servers_if=//p' "$ANSWERS" 2>/dev/null | head -n1)
    _df_addr=$(sed -n 's/^servers_addr=//p' "$ANSWERS" 2>/dev/null | head -n1)
    [ -n "$_df_addr" ] || _df_addr=$(sed -n 's/^address=//p' "$DHCP_DIR/$_df_if" 2>/dev/null | head -n1)
    [ -n "$_df_addr" ] && emit console_url "https://${_df_addr%%/*}:8081"
    emit user "$(sed -n 's/^admin_user=//p' "$ANSWERS" 2>/dev/null | head -n1)"
}

do_eject() {
    _de_dev=$(cat "$MEDIA_FILE" 2>/dev/null || printf '')
    [ -n "$_de_dev" ] || { emit ejected 0; emit error 'no install medium recorded'; return 0; }
    umount /cdrom 2>/dev/null
    if eject "$_de_dev" 2>/dev/null; then emit ejected 1; else emit ejected 0; emit error "eject $_de_dev failed (remove the medium by hand)"; fi
}

case "$ACTION" in
    inventory|interfaces) do_inventory ;;
    validate) do_validate ;;
    apply) do_apply ;;
    progress) do_progress ;;
    check-primary) do_check_primary ;;
    finish) do_finish ;;
    eject) do_eject ;;
    reboot) sync; reboot -f ;;
    boot-local) do_eject; sync; reboot -f ;;
    *) echo "gui-bridge: unknown action $ACTION" >&2; exit 2 ;;
esac
