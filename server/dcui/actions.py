"""Authenticated writes performed by the physical console.

All host commands are argv lists and accept an injectable runner.  Network
changes deliberately use the same ``netcfg``/``netcfg_host``/
``netcfg_rollback`` primitives as the web console.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from server.net import netcfg, netcfg_host, netcfg_rollback
from server.console import users
from server.db import (_write_lock, get_setting, journal, set_setting,
                       writing)

from .data import CommandResult, Runner, run_argv

LOCKOUT_ATTEMPTS = 3
LOCKOUT_SECONDS = 30
AUTH_IDLE_SECONDS = 90
# users.validate_password decides; its messages are Hebrew and tty1 is ASCII.
PASSWORD_POLICY = "Use 8-128 characters with letters, digits and a special character."
PASSWORD_SPECIALS = 'Special characters: ! @ # $ % ^ & * ( ) , . ? " : { } | < >'


@dataclass
class AuthGuard:
    failures: int = 0
    locked_until: float = 0.0

    def seconds_left(self, now: float | None = None) -> int:
        current = time.time() if now is None else now
        return max(0, int(self.locked_until - current + 0.999))

    def authenticate(self, conn, password: str, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        if self.locked_until > current:
            journal(conn, "dcui_auth_locked", "admin", "dcui")
            return False
        if self.locked_until and self.locked_until <= current:
            self.failures = 0
            self.locked_until = 0.0
        role = users.verify(conn, "admin", password)
        if role == "admin":
            self.failures = 0
            journal(conn, "dcui_auth_success", "admin", "dcui")
            return True
        self.record_failure(current)
        journal(conn, "dcui_auth_failure", f"admin attempt={self.failures}", "dcui")
        return False

    def record_failure(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        if self.locked_until and self.locked_until <= current:
            self.failures = 0
            self.locked_until = 0.0
        self.failures += 1
        if self.failures >= LOCKOUT_ATTEMPTS:
            self.locked_until = current + LOCKOUT_SECONDS


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    details: tuple[str, ...] = ()
    #: #1988: the Hebrew sentence for the graphical screen when the backend
    #: wrote one (tty1 is ASCII and shows ``message``).
    he: str = ""


def password_problem(password: str) -> str | None:
    """The installer/console policy (``users.validate_password``), in ASCII."""
    try:
        users.validate_password(password)
    except ValueError:
        return PASSWORD_POLICY
    return None


def record_reset_failure(conn, guard: AuthGuard, reason: str,
                         now: float | None = None) -> None:
    guard.record_failure(now)
    locked = f" locked={LOCKOUT_SECONDS}s" if guard.locked_until else ""
    journal(conn, "dcui_reset_failure",
            f"admin attempt={guard.failures} reason={reason}{locked}", "dcui")


def physical_reset_admin_password(conn, new_password: str) -> ActionResult:
    """#1249: reset by physical presence at tty1 -- no current password.

    Replaces the hash, ends every admin session, drops admin's MFA
    (whoever lost the password has likely lost the phone
    too) and clears the web-console login lockout. Success is read back.
    """
    problem = password_problem(new_password)
    if problem:
        return ActionResult(False, problem)
    stored = users.canonical_name(conn, "admin")
    if stored is None:
        journal(conn, "admin_password_reset_dcui", "refused: no admin account", "dcui")
        return ActionResult(False, "The server DB has no 'admin' account; nothing changed.")
    users.update(conn, stored, by="dcui", password=new_password)
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE users SET must_change_password = 0, mfa_enabled = 0,"
            " mfa_secret = NULL, mfa_enrolled_at = NULL, mfa_last_step = NULL"
            " WHERE username = ?", (stored,))
        conn.execute("DELETE FROM mfa_backup_codes WHERE username = ?", (stored,))
        conn.execute("DELETE FROM login_attempts WHERE key LIKE ?",
                     (stored.lower() + "|%",))
    users.bump_auth_epoch(conn, stored)
    info = users.flags(conn, stored)
    verified = (users.verify(conn, stored, new_password) == "admin"
                and not info["mfa_enabled"] and not info["must_change_password"])
    journal(conn, "admin_password_reset_dcui",
            "admin password reset from the server console (DCUI);"
            f" verified={str(verified).lower()}", "dcui")
    if not verified:
        return ActionResult(False, "The new password could not be read back from the DB.")
    return ActionResult(
        True, "Admin password reset. Log in to the web console with the new password.",
        ("Every admin web session was signed out.",
         "Two-factor enrollment was removed."))


def _text(result: CommandResult) -> str:
    return (result.stderr or result.stdout or "").strip()


def restart_services(conn, runner: Runner = run_argv) -> ActionResult:
    units = ["imagesrv-server"]
    enabled = runner(["systemctl", "is-enabled", "dnsmasq"])
    if enabled.returncode == 0 and enabled.stdout.strip() == "enabled":
        units.append("dnsmasq")
    failures = []
    for unit in units:
        result = runner(["systemctl", "restart", unit])
        if result.returncode != 0:
            failures.append(f"{unit}: {_text(result) or 'restart failed'}")
    states = []
    for unit in units:
        state = runner(["systemctl", "is-active", unit])
        value = (state.stdout or state.stderr).strip() or "unknown"
        states.append(f"{unit}: {value}")
        if value != "active":
            failures.append(f"{unit} did not read back active")
    ok = not failures
    journal(conn, "dcui_services_restart",
            f"units={','.join(units)} ok={str(ok).lower()}", "dcui")
    return ActionResult(ok, "Services restarted." if ok else "Service restart failed.",
                        tuple(states + failures))


def note_tls_display(conn) -> None:
    journal(conn, "dcui_tls_display", "console certificate", "dcui")


def power_action(conn, choice: str, runner: Runner = run_argv) -> ActionResult:
    """#1270 (Nadav, 10/10): no typed word confirms anything -- not the hostname
    either. The F12 screen behind the admin password, then <Enter>, is the
    confirmation."""
    command = "reboot" if choice == "restart" else "poweroff"
    journal(conn, "dcui_power_action", command, "dcui")
    result = runner(["systemctl", command])
    if result.returncode == 0:
        return ActionResult(True, f"System {command} requested.")
    return ActionResult(False, f"System {command} failed.",
                        (_text(result) or "systemctl returned no detail",))


def _ipv4(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 4
    except ValueError:
        return False


def _netmask(value: str) -> str | None:
    raw = value.strip()
    try:
        if raw.isdigit():
            prefix = int(raw)
            if not 0 <= prefix <= 32:
                return None
            return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
        network = ipaddress.IPv4Network(f"0.0.0.0/{raw}")
        return str(network.netmask)
    except (ValueError, ipaddress.NetmaskValueError):
        return None


def validate_network_form(form: dict[str, str], interface_names: set[str]) -> dict[str, str]:
    """Wizard-equivalent IPv4 validation with ASCII English VT messages."""
    errors: dict[str, str] = {}
    name = form.get("interface", "").strip()
    mode = form.get("mode", "").strip()
    if name not in interface_names:
        errors["interface"] = "Select an existing network interface."
    if mode not in {netcfg.MODE_DHCP, netcfg.MODE_STATIC}:
        errors["mode"] = "Select DHCP or Static."
    if mode == netcfg.MODE_STATIC:
        address = form.get("address", "").strip()
        mask = form.get("netmask", "").strip()
        gateway = form.get("gateway", "").strip()
        dns_values = [item.strip() for item in form.get("dns", "").split(",")
                      if item.strip()]
        if not _ipv4(address):
            errors["address"] = "Invalid IPv4 address."
        if _netmask(mask) is None:
            errors["netmask"] = "Invalid prefix or subnet mask."
        if gateway and not _ipv4(gateway):
            errors["gateway"] = "Invalid gateway IPv4 address."
        if any(not _ipv4(item) for item in dns_values):
            errors["dns"] = "Invalid DNS IPv4 address; separate entries with commas."
    return errors


def network_config(form: dict[str, str]) -> netcfg.NetConfig:
    mode = form["mode"].strip()
    mask = _netmask(form.get("netmask", "")) or form.get("netmask", "").strip()
    return netcfg.NetConfig(
        name=form["interface"].strip(), mode=mode,
        address=form.get("address", "").strip() if mode == netcfg.MODE_STATIC else "",
        netmask=mask or "255.255.255.0",
        gateway=form.get("gateway", "").strip() if mode == netcfg.MODE_STATIC else "",
        dns=[item.strip() for item in form.get("dns", "").split(",") if item.strip()],
    )


def apply_preinstall_static(form: dict[str, str], interface_names: set[str],
                            runner: Runner = run_argv) -> ActionResult:
    """Apply an in-memory-only address while the installer wizard is running."""
    address_with_prefix = form.get("address", "").strip()
    if "/" in address_with_prefix:
        address, prefix = address_with_prefix.rsplit("/", 1)
    else:
        address, prefix = address_with_prefix, ""
    static_form = {
        **form, "mode": netcfg.MODE_STATIC, "dns": "",
        "address": address, "netmask": prefix,
    }
    errors = validate_network_form(static_form, interface_names)
    if not form.get("gateway", "").strip():
        errors["gateway"] = "Gateway is required."
    if errors:
        return ActionResult(False, "Static address form is invalid.",
                            tuple(errors.values()))
    cfg = network_config(static_form)
    prefix = ipaddress.IPv4Network(f"0.0.0.0/{cfg.netmask}").prefixlen
    commands = (
        ["ip", "addr", "flush", "dev", cfg.name],
        ["ip", "addr", "add", f"{cfg.address}/{prefix}", "dev", cfg.name],
        ["ip", "route", "replace", "default", "via", cfg.gateway,
         "dev", cfg.name],
    )
    for argv in commands:
        result = runner(argv)
        if result.returncode != 0:
            return ActionResult(
                False, f"Temporary network command failed: {' '.join(argv[:3])}.",
                (_text(result) or "ip returned no detail",),
            )
    return ActionResult(True, f"Temporary address applied to {cfg.name}.")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().isoformat(
        timespec="seconds")


def _read_resolv(path: str = netcfg_host.RESOLV_CONF) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _all_configs(conn, replacement: netcfg.NetConfig) -> list[netcfg.NetConfig]:
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?",
        (netcfg.SETTING_PREFIX + "%",),
    ).fetchall()
    found = []
    for row in rows:
        name = row["key"][len(netcfg.SETTING_PREFIX):]
        if name != replacement.name:
            try:
                found.append(netcfg.NetConfig.from_json(name, row["value"]))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
    return sorted([*found, replacement], key=lambda item: item.name)


def default_network_hooks() -> dict[str, Callable]:
    return {
        **netcfg_host.default_hooks(),
        "boot_id": netcfg_rollback.boot_id,
        "now": time.time,
        "sleep": time.sleep,
        "monotonic": time.monotonic,
        "hostname": socket.gethostname,
        "read_resolv": _read_resolv,
    }


def apply_network(conn, form: dict[str, str], data_dir: Path,
                  interface_names: set[str], hooks: dict[str, Callable] | None = None) -> ActionResult:
    errors = validate_network_form(form, interface_names)
    if errors:
        journal(conn, "net_config_refused",
                f"validation fields={','.join(sorted(errors))}", "dcui")
        return ActionResult(False, "Network form is invalid.", tuple(errors.values()))
    cfg = network_config(form)
    before_raw = get_setting(conn, netcfg.SETTING_PREFIX + cfg.name)
    try:
        before = netcfg.NetConfig.from_json(cfg.name, before_raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        before = netcfg.NetConfig(cfg.name)
    validation = netcfg.problems(cfg, _all_configs(conn, cfg))
    if validation:
        # netcfg's operator-facing messages are Hebrew; tty1 is ASCII-only.
        journal(conn, "net_config_refused", f"{cfg.name} conflicts={len(validation)}",
                "dcui")
        return ActionResult(False, "Network configuration conflicts with existing settings.",
                            tuple(f"Conflict {index + 1}" for index in range(len(validation))))
    state_dir = data_dir / "netcfg"
    if netcfg_rollback.read_pending(state_dir) is not None:
        journal(conn, "net_config_refused", f"{cfg.name} pending change", "dcui")
        return ActionResult(False, "A previous network change still awaits confirmation.")
    active_hooks = {**default_network_hooks(), **(hooks or {})}
    ready, detail = active_hooks["netcfg_timer_active"]()
    if not ready:
        journal(conn, "net_config_refused", f"{cfg.name} rollback timer inactive", "dcui")
        return ActionResult(False, "The automatic network rollback timer is not active.",
                            (str(detail),))
    now = float(active_hooks["now"]())
    marker = netcfg_rollback.Pending(
        interface=cfg.name,
        deadline=now + netcfg_rollback.WINDOW_SECONDS,
        armed_at=_iso(now), boot=str(active_hooks["boot_id"]()),
        files=[{"name": cfg.name,
                "text": active_hooks["netcfg_read_conf"](
                    cfg.name, netcfg_host.INTERFACES_DIR)}],
        resolv=active_hooks["read_resolv"](), setting=before.to_json(),
    )
    marker_error = netcfg_rollback.write_pending(state_dir, marker)
    if marker_error:
        journal(conn, "net_config_refused", f"{cfg.name} marker write failed", "dcui")
        return ActionResult(False, "Could not arm automatic network rollback.")
    journal(conn, "net_rollback_armed",
            f"{cfg.name} window={netcfg_rollback.WINDOW_SECONDS}s", "dcui")
    set_setting(conn, netcfg.SETTING_PREFIX + cfg.name, cfg.to_json())
    journal(conn, "net_config",
            f"{netcfg.summary(cfg)} changed={','.join(netcfg.changed(before, cfg))}",
            "dcui")
    configs = _all_configs(conn, cfg)
    apply_errors = [
        active_hooks["netcfg_write_conf"](
            cfg.name, netcfg.render(cfg, hostname=active_hooks["hostname"]()),
            netcfg_host.INTERFACES_DIR),
        active_hooks["netcfg_write_resolv"](
            netcfg.render_resolv(configs), netcfg_host.RESOLV_CONF),
    ]
    error = next((item for item in apply_errors if item), None)
    if error is None:
        error = active_hooks["netcfg_apply"](cfg.name)
    live = active_hooks["netcfg_state"]()
    gaps = netcfg_host.mismatches(cfg, live)
    if error or gaps:
        journal(conn, "net_config_unverified",
                f"{cfg.name} {error or 'live state mismatch'}", "dcui")
        detail_lines = (["Apply failed; automatic rollback remains armed."] if error else [])
        detail_lines += ["Live read-back did not match the requested configuration."] if gaps else []
        return ActionResult(False, "Network change was not verified.", tuple(detail_lines))
    return ActionResult(True, "Network change applied; confirm within 60 seconds.")


def restart_management_network(conn, interface: str, data_dir: Path,
                               hooks: dict[str, Callable] | None = None) -> ActionResult:
    raw = get_setting(conn, netcfg.SETTING_PREFIX + interface)
    if not raw:
        journal(conn, "dcui_management_network_restart",
                f"{interface} ok=false no saved configuration", "dcui")
        return ActionResult(False, "The management interface has no saved configuration.")
    try:
        cfg = netcfg.NetConfig.from_json(interface, raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        journal(conn, "dcui_management_network_restart",
                f"{interface} ok=false invalid saved configuration", "dcui")
        return ActionResult(False, "The saved management network configuration is invalid.")
    # #1537: a restart changes no setting and writes no file, so there is
    # nothing to roll back and nothing to confirm -- no marker is armed.
    # Success is the saved address read back live, never the commands' exit.
    if netcfg_rollback.read_pending(data_dir / "netcfg") is not None:
        journal(conn, "dcui_management_network_restart",
                f"{interface} ok=false pending change", "dcui")
        return ActionResult(False, "A previous network change still awaits confirmation.")
    active_hooks = {**default_network_hooks(), **(hooks or {})}
    error = active_hooks["netcfg_apply"](interface)
    # #2094: a DHCP read-back waits for the lease (dhcpcd -b returns at once)
    gaps = [] if error else netcfg_host.settled_mismatches(
        cfg, active_hooks["netcfg_state"], sleep=active_hooks.get("sleep", time.sleep),
        clock=active_hooks.get("monotonic", time.monotonic))
    if error or gaps:
        journal(conn, "dcui_management_network_restart",
                f"{interface} ok=false {'apply failed' if error else 'live state mismatch'}",
                "dcui")
        return ActionResult(False, "Management network restart was not verified.",
                            ("ifdown/ifup failed." if error else
                             "Live read-back did not match the saved configuration.",))
    journal(conn, "dcui_management_network_restart", f"{interface} ok=true", "dcui")
    return ActionResult(True, "Management network restarted; the address read back live.")


def confirm_network(conn, data_dir: Path, now: float | None = None) -> ActionResult:
    state_dir = data_dir / "netcfg"
    marker = netcfg_rollback.read_pending(state_dir)
    current = time.time() if now is None else now
    if marker is None:
        journal(conn, "dcui_net_confirm_refused", "no pending change", "dcui")
        return ActionResult(False, "No network change is waiting for confirmation.")
    if netcfg_rollback.expired(marker, current):
        journal(conn, "dcui_net_confirm_refused", f"{marker.interface} expired", "dcui")
        return ActionResult(False, "The confirmation window closed; rollback will run.")
    netcfg_rollback.clear_pending(state_dir)
    if netcfg_rollback.pending_path(state_dir).exists():
        journal(conn, "dcui_net_confirm_refused",
                f"{marker.interface} marker still present", "dcui")
        return ActionResult(False, "The rollback marker could not be cleared.")
    set_setting(conn, "dcui:management_interface", marker.interface)
    if get_setting(conn, "dcui:management_interface") != marker.interface:
        journal(conn, "dcui_net_confirm_refused",
                f"{marker.interface} management selection not read back", "dcui")
        return ActionResult(False, "The management interface selection was not verified.")
    journal(conn, "net_confirmed", marker.interface, "dcui")
    return ActionResult(True, "Network change confirmed.")


def request_network_rollback(conn, data_dir: Path,
                             runner: Runner = run_argv) -> ActionResult:
    marker = netcfg_rollback.read_pending(data_dir / "netcfg")
    if marker is None:
        journal(conn, "dcui_net_rollback_requested", "no pending change", "dcui")
        return ActionResult(False, "No network change is waiting for rollback.")
    result = runner(["systemctl", "start", "imagesrv-netrollback.service"])
    ok = (result.returncode == 0
          and not netcfg_rollback.pending_path(data_dir / "netcfg").exists())
    journal(conn, "dcui_net_rollback_requested",
            f"{marker.interface} ok={str(ok).lower()}", "dcui")
    return ActionResult(ok, "Network rollback requested." if ok
                        else "Network rollback could not be started.")


# ---- #1988: "Add Disk" -- the same flow as the web console ------------------

def blank_disks(hooks: dict | None = None) -> tuple[dict | None, ActionResult | None]:
    """(listing, None) or (None, the reason it could not be read)."""
    from server.storage import storage_disks
    from server.storage import storage_locations as sl
    try:
        return storage_disks.blank_disks({**sl.default_hooks(), **(hooks or {})}), None
    except storage_disks.AddDiskError as exc:
        return None, ActionResult(False, exc.en, he=exc.message)


def disk_name(conn, disk: dict) -> str:
    """The location name for a disk added here (nothing is typed on tty1):
    ``disk-<last 6 of the serial>`` (or the kernel name), unique."""
    tail = (disk.get("serial") or disk.get("name") or "x")[-6:]
    clean = "".join(ch for ch in tail if ch.isascii() and (ch.isalnum() or ch in "-_"))
    base = "disk-" + (clean or "x")
    name, n = base, 2
    while conn.execute("SELECT 1 FROM storage_locations WHERE name = ?", (name,)).fetchone():
        name, n = f"{base}-{n}", n + 1
    return name


def add_disk(conn, data_dir: Path, disk: dict, hooks: dict | None = None) -> ActionResult:
    from server.storage import storage_disks
    from server.storage import storage_locations as sl
    if conn is None:
        return ActionResult(False, "The server database is not available.")
    name = disk_name(conn, disk)
    try:
        row = storage_disks.add_disk(
            conn, {**sl.default_hooks(), **(hooks or {})}, data_dir,
            device=disk.get("device") or "", serial=disk.get("serial") or "",
            size_bytes=disk.get("size_bytes"), name=name, user="dcui")
    except storage_disks.AddDiskError as exc:
        return ActionResult(False, exc.en, he=exc.message)
    mount = row["mount_point"]
    return ActionResult(True, f"Disk {disk.get('device')} added as storage location {name}.",
                        (f"Mounted at {mount} (UUID in /etc/fstab, nofail).",
                         "Write/read-back check passed."),
                        he=f"הכונן {disk.get('device')} נוסף כמיקום האחסון {name}, "
                           f"מחובר קבוע ב-{mount}. קובץ בדיקה נכתב ונקרא בחזרה.")
