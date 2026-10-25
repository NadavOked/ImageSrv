#!/usr/bin/env bash
#
# firstboot-answers.sh -- stage B from the live installer's answers (#1190).
# Sourced by firstboot.sh when /etc/imagectl/answers exists: the person
# already answered everything on the server's screen, so there is no wizard
# to wait for. This does what the wizard's apply does (server/wizard/core.py
# `_apply`), in the same order and with the same flags, then verifies the
# boot payload and writes the stamp -- and only then starts the DCUI.
#
# The admin password comes from /etc/imagectl/answers.secret through a file
# descriptor (never argv/env/log), and the secret is shredded right after.
# Every step reports by name; a failed step is a failed first boot that the
# DCUI shows, not a server that "came up" half-configured.

ANSWERS="${ANSWERS:-/etc/imagectl/answers}"
ANSWERS_SECRET="${ANSWERS_SECRET:-/etc/imagectl/answers.secret}"
APP_DIR="${APP_DIR:-/opt/imagectl}"
DATA_DIR="${DATA_DIR:-/var/lib/imagectl}"

answers_get() { sed -n "s/^$1=//p" "$ANSWERS" | head -n1; }

# Resolve the management NIC by MAC first (names can change between the live
# kernel and the installed one), then by name; both must agree with a live NIC.
answers_servers_if() {
    local mac name path
    mac=$(answers_get servers_mac); name=$(answers_get servers_if)
    if [[ -n "$mac" ]]; then
        for path in /sys/class/net/*; do
            [[ "$(cat "$path/address" 2>/dev/null)" == "$mac" ]] && { basename "$path"; return 0; }
        done
    fi
    [[ -n "$name" && -e "/sys/class/net/$name" ]] && { printf '%s\n' "$name"; return 0; }
    return 1
}

# #2094: the first global IPv4 address on the NIC, polled for up to
# FIRSTBOOT_ADDR_WAIT_S (the ESXi lease took ~7 s: offer, then 5 s of ARP
# probing). Empty = no address in time; return 2 = `ip` itself failed, which
# is "not checked", not "no address" (principle 5).
wait_servers_address() {
    local nic="$1" waited=0 addr="" out
    while :; do
        out=$(ip -o -4 addr show dev "$nic" scope global) || return 2
        addr=$(awk 'NR == 1 {print $4}' <<<"$out")
        [[ -n "$addr" || "$waited" -ge "${FIRSTBOOT_ADDR_WAIT_S:-30}" ]] && break
        sleep 1; waited=$((waited + 1))
    done
    printf '%s\n' "$addr"
}

firstboot_from_answers() {
    local role primary mode host servers_if argv rc
    [[ -s "$ANSWERS_SECRET" ]] || fail answers-secret-missing "$ANSWERS exists but $ANSWERS_SECRET is missing or empty"
    role=$(answers_get role); mode=$(answers_get servers_mode); host=$(answers_get hostname); primary=$(answers_get primary_url)
    [[ "$role" == standalone || "$role" == secondary ]] || fail answers-invalid "role in $ANSWERS is '$role'"
    [[ "$mode" == dhcp || "$mode" == static ]] || fail answers-invalid "servers_mode in $ANSWERS is '$mode'"
    [[ -n "$host" ]] || fail answers-invalid "hostname missing from $ANSWERS"
    [[ -n "$(answers_get admin_user)" ]] || fail answers-invalid "admin_user missing from $ANSWERS"
    servers_if=$(answers_servers_if) || fail answers-nic-missing "no live NIC matches servers_mac/servers_if from $ANSWERS"
    log "stage B from answers: role=$role nic=$servers_if ($mode) hostname=$host"
    printf 'state=installing\ntimestamp=%s\nrole_default=%s\nsource=answers\n' "$(date -u +%FT%TZ)" "$role" > "$STATUS"

    hostnamectl set-hostname "$host" || fail hostname-failed "hostnamectl set-hostname $host failed"
    # read back, not exit code: hostnamectl without a system bus (no dbus) returns 1 with the
    # name already right in /etc/hostname from the engine -- and the reverse would be silent
    [[ "$(hostname)" == "$host" ]] || fail hostname-failed "hostname reads back '$(hostname)', not $host"
    argv=(bash "$SRC/install/setup-boot-server.sh" --servers-if "$servers_if" --servers-mode "$mode")
    if [[ "$mode" == static ]]; then
        argv+=(--servers-address "$(answers_get servers_addr)" --servers-netmask "$(answers_get servers_mask)")
        [[ -n "$(answers_get servers_gw)" ]] && argv+=(--servers-gateway "$(answers_get servers_gw)")
        [[ -n "$(answers_get servers_dns)" ]] && argv+=(--servers-dns "$(answers_get servers_dns)")
    fi
    argv+=(--console-host "$servers_if" --storage-role "$role")
    [[ "$role" == secondary ]] && argv+=(--primary-url "$primary")
    argv+=(--admin-user "$(answers_get admin_user)" --admin-pass-fd 3)
    # #1447: the zone chosen on the screen becomes the server's setting -- else
    # the server's start would bring the OS back to the default. An answers
    # file from an older ISO has none: the default, as before.
    if [[ -n "$(answers_get timezone)" ]]; then argv+=(--timezone "$(answers_get timezone)"); fi
    # answers.secret is `admin_pass=<value>` (interfaces.md); setup-boot-server
    # reads fd 3 as the bare password. Feeding it the whole file made the
    # admin password literally "admin_pass=..." (ESXi, 21/09) -- the value
    # only, and an empty value is a named failure, not an empty password.
    secret=$(sed -n 's/^admin_pass=//p' "$ANSWERS_SECRET" | head -n 1)
    [[ -n "$secret" ]] || fail answers-secret-missing "$ANSWERS_SECRET has no admin_pass= line"
    set +e
    # #2148: the certificate is issued again below, once the NIC has its
    # address -- the fingerprint goes on the DCUI, not into this log
    IMAGECTL_CONSOLE_FP_ON_DCUI=1 "${argv[@]}" 3< <(printf '%s\n' "$secret") >>"$BUILD_LOG" 2>&1
    rc=$?
    set -e
    unset secret
    shred -u "$ANSWERS_SECRET" 2>/dev/null || rm -f "$ANSWERS_SECRET"
    [[ $rc -eq 0 ]] || fail installer-failed "setup-boot-server.sh exited $rc; see $BUILD_LOG"

    # setup-boot-server writes interfaces.d/imagectl-<nic> and deliberately does
    # no ifup (the web wizard is connected through that NIC); here nobody is,
    # and networking.service ran before the file existed -- without this the
    # first DCUI says "link down" until the next restart (ESXi, 21/09).
    # #2094: through networking.service, not `ifup` here. The DHCP client
    # (`dhcpcd -b`) stays in the cgroup of whoever ran ifup; this oneshot unit
    # ends a second later, systemd stops its cgroup, and dhcpcd's SIGTERM
    # removed the address ("received SIGTERM ... ens33: removing interface",
    # ESXi 24/10). networking.service stays active (RemainAfterExit), as on
    # every later boot -- where the address was always there.
    addr=
    if systemctl restart networking.service >>"$BUILD_LOG" 2>&1; then
        if addr=$(wait_servers_address "$servers_if" 2>>"$BUILD_LOG"); then
            log "nic $servers_if: ${addr:-up, no IPv4 address after ${FIRSTBOOT_ADDR_WAIT_S:-30}s}"
        else
            addr=
            log "[nic-check-failed] could not read the addresses of $servers_if (see $BUILD_LOG)"
        fi
    else
        log "[nic-up-failed] networking.service restart for $servers_if failed (see $BUILD_LOG); the console is reachable after a restart"
    fi
    # #2095: setup-boot-server issued the console certificate before the NIC
    # had an address (loopback + name only). With the address on the NIC the
    # server starts again and issues it for that address -- here, before the
    # DCUI shows a fingerprint to photograph -- not on the second boot, after
    # the operator already trusted the first one.
    if [[ -n "$addr" ]]; then
        systemctl restart imagectl-server.service >>"$BUILD_LOG" 2>&1 \
            || fail installer-failed "imagectl-server did not restart after $servers_if got $addr"
    fi

    bash "$APP_DIR/install/verify-boot-payload.sh" --app-dir "$APP_DIR" --http-root "$HTTP_ROOT" \
        --server-url http://127.0.0.1:8080 >>"$BUILD_LOG" 2>&1 \
        || fail verify-failed "verify-boot-payload.sh failed; the installation is not marked complete (see $BUILD_LOG)"
    printf 'state=done\ntimestamp=%s\nservers_if=%s\nrole=%s\nsource=answers\n' "$(date -u +%FT%TZ)" "$servers_if" "$role" > "$STATUS"
    touch "$STAMP"
    log "stage B complete from answers: console on $servers_if, role $role"
}
