# Verify the site's CA and its DNS name, while connecting to the PXE address.
# CURL_HOME also reaches the watchdog and the kiosk's separate shell processes.
tls_config() {
    TLS_WHY="TLS: פרטי האמון של השרת חסרים או פגומים ב-initrd"
    TLS_CA=${SERVER_CA_FILE:-/etc/imagesrv/agent-ca.pem}
    [ -s "$TLS_CA" ] && IFS= read -r TLS_NAME < "${AGENT_NAME_FILE:-/etc/imagesrv/agent-name}" || return 1
    _suffix=${TLS_NAME#imagesrv-agent-}
    [ "$TLS_NAME" != "$_suffix" ] && [ "${#_suffix}" = 16 ] || return 1
    case "$_suffix" in *[!a-f0-9]*) return 1 ;; esac
    TLS_WHY="TLS: כתובת שרת ההפצה אינה תקינה"
    case "$IMAGESRV_SERVER" in https://*) _addr=${IMAGESRV_SERVER#https://} ;; *) return 1 ;; esac
    TLS_IP=${_addr%:*}; _port=${_addr##*:}
    case "$TLS_IP" in ''|*[!0-9.]*) return 1 ;; esac
    case "$_port" in ''|*[!0-9]*) return 1 ;; esac
    [ "$TLS_IP" != "$_addr" ] && [ "$_port" -gt 0 ] && [ "$_port" -le 65535 ] || return 1
    TLS_CONNECT="$TLS_NAME:$_port:$TLS_IP:$_port"
    TLS_WHY="TLS: לא ניתן להגדיר את אימות תעודת השרת"
    CURL_HOME="$RUN_DIR/agent-tls"
    mkdir -p "$CURL_HOME" || return 1
    # Multiple processes (early GUI, init and agent) may configure simultaneously.
    printf 'cacert = "%s"\ncapath = "%s"\nconnect-to = "%s"\nproto-redir = "=https"\n' \
        "$TLS_CA" "$CURL_HOME" "$TLS_CONNECT" > "$CURL_HOME/curlrc.$$" &&
        mv "$CURL_HOME/curlrc.$$" "$CURL_HOME/.curlrc" || return 1
    SERVER="https://$TLS_NAME:$_port"
    CURL_CA_BUNDLE=$TLS_CA
    SSL_CERT_DIR=$CURL_HOME
    export SERVER TLS_IP CURL_HOME TLS_CA TLS_CONNECT CURL_CA_BUNDLE SSL_CERT_DIR
}

# The machine's own view first: a clock before the day every certificate starts (2000-01-01) refuses the right server.
tls_clock_note() {
    _tc=$(date +%s 2>/dev/null); case "$_tc" in ''|*[!0-9]*) return 0 ;; esac
    [ "$_tc" -ge 946684800 ] || printf ' -- שעון המכונה (%s) לפני 2000-01-01, תחילת התעודה: לא חושדים בשרת, יש לתקן את שעון ה-BIOS' "$(date -u +%Y-%m-%d 2>/dev/null)"
}
