"""Pure 80x25 renderers and the curses event loop for tty1."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from server import netcfg, netcfg_rollback
from server.db import connect

from . import actions, pairing
from .data import (DATA_DIR, NicInfo, Snapshot, collect_snapshot,
                   demo_snapshot)

WIDTH = 80
HEIGHT = 25
MENU_ITEMS = (
    "Configure Management Network",
    "Restart Management Network",
    "Restart Services",
    "Display TLS Certificate",
    "Primary-Visible Address",
    "Add Disk",                       # #1988
)


class Canvas:
    def __init__(self, title: str, footer: str):
        self.rows = [[" "] * WIDTH for _ in range(HEIGHT)]
        self.hline(0)
        self.hline(2)
        self.hline(22)
        self.hline(24)
        for row in range(1, 24):
            self.rows[row][0] = "|"
            self.rows[row][-1] = "|"
        self.put(1, 2, title)
        self.put(23, 2, footer)

    def hline(self, row: int) -> None:
        self.rows[row] = list("+" + "-" * (WIDTH - 2) + "+")

    def put(self, row: int, column: int, text: object, width: int | None = None) -> None:
        if not 0 <= row < HEIGHT or column >= WIDTH - 1:
            return
        clean = str(text).encode("ascii", "replace").decode("ascii")
        if width is not None:
            clean = clean[:width].ljust(width)
        room = max(0, WIDTH - 1 - max(1, column))
        for index, char in enumerate(clean[:room]):
            self.rows[row][max(1, column) + index] = char

    def lines(self) -> list[str]:
        return ["".join(row) for row in self.rows]


def _title(left: str, right: str = "") -> str:
    usable = WIDTH - 4
    if not right:
        return left[:usable]
    spaces = max(1, usable - len(left) - len(right))
    return (left + " " * spaces + right)[:usable]


def _service(snapshot: Snapshot, name: str) -> str:
    state = snapshot.services.get(name)
    if name == "dnsmasq" and snapshot.deploy is None and state and state.active != "active":
        return "inactive (Unconfigured - no deploy network)"
    return state.text() if state else "unknown (enablement unknown)"


def _fingerprint_lines(value: str) -> tuple[str, str]:
    parts = value.split(":")
    if len(parts) >= 32:
        return ":".join(parts[:16]), ":".join(parts[16:32])
    midpoint = min(len(value), 47)
    return value[:midpoint], value[midpoint:midpoint + 47]


def render_main(snapshot: Snapshot, message: str = "") -> list[str]:
    shown_version = snapshot.version + (" [UNVERIFIED]" if not snapshot.version_verified else "")
    footer = ("<F4> Pairing  <F2> Customize  <F9> Reset Password  <F12> Power  <F5> Refresh"
              if snapshot.storage_role == "secondary" else
              "<F2> Customize  <F9> Reset Password  <F12> Restart/Shutdown  <F5> Refresh")
    canvas = Canvas(
        _title("ImageSrv Server Operations Console",
               f"{shown_version}  {snapshot.storage_role}"),
        footer,
    )
    fp1, fp2 = _fingerprint_lines(snapshot.fingerprint)
    canvas.put(4, 4, "Hostname:")
    canvas.put(4, 23, snapshot.hostname)
    canvas.put(5, 4, "Console:")
    console = snapshot.console_url
    if snapshot.address_lookup_failed:
        console += "   IPv4 address lookup failed"
    elif snapshot.no_address:
        console += "   No DHCP address assigned"
    canvas.put(5, 23, console)
    canvas.put(6, 4, "Certificate:")
    canvas.put(6, 23, f"SHA256 {fp1}")
    canvas.put(7, 30, fp2)
    mgmt = snapshot.management
    speed = f" {mgmt.speed}" if mgmt.speed and mgmt.speed != "unknown" else ""
    canvas.put(9, 4, "Management NIC:")
    canvas.put(9, 23, f"{mgmt.name}  {mgmt.address or 'no IPv4'}  "
                       f"{mgmt.mode.upper()}  link {mgmt.link}{speed}")
    canvas.put(10, 4, "Deploy NIC:")
    if snapshot.deploy is None:
        canvas.put(10, 23, "not configured (set from the console)")
    else:
        deploy = snapshot.deploy
        canvas.put(10, 23, f"{deploy.name}  {deploy.address or 'no IPv4'}  link {deploy.link}")
    canvas.put(12, 4, "imagectl-server:")
    canvas.put(12, 23, _service(snapshot, "imagectl-server"))
    canvas.put(13, 4, "dnsmasq:")
    canvas.put(13, 23, _service(snapshot, "dnsmasq"))
    canvas.put(14, 4, "nftables:")
    canvas.put(14, 23, _service(snapshot, "nftables"))
    plural = "secondary" if snapshot.secondary_count == 1 else "secondaries"
    canvas.put(15, 4, "Storage Role:")
    canvas.put(15, 23, f"{snapshot.storage_role}  ({snapshot.secondary_count} {plural} paired)")
    canvas.put(16, 4, "System updates:")   # #1411
    canvas.put(16, 23, f"from {snapshot.system_updates}" if snapshot.system_updates else "date unknown")
    canvas.put(17, 4, "Deploy round:")
    round_text = "none"
    if snapshot.deploy_round:
        item = snapshot.deploy_round
        round_text = f"{item.label}  {item.state}  {item.writing}/{item.targets} writing"
    canvas.put(17, 23, round_text)
    if message:
        canvas.put(19, 4, message, 72)
    else:
        canvas.put(19, 4, "This screen is read-only. F2/F12 need the admin password; F9 resets it.")
    canvas.put(21, 4, f"Last refresh: {snapshot.refreshed}   (every 5 s)")
    return canvas.lines()


def render_preinstall(snapshot: Snapshot, message: str = "") -> list[str]:
    shown_version = snapshot.version + (" [UNVERIFIED]" if not snapshot.version_verified else "")
    canvas = Canvas(
        _title(f"ImageSrv {shown_version} - installation"),
        "F2  Set a static address       F5  Refresh",
    )
    canvas.put(4, 4, "Management network is not configured yet.")
    canvas.put(6, 4, "Addresses obtained by DHCP:")
    for index, nic in enumerate(snapshot.interfaces[:6]):
        address = nic.address or "-"
        state = "link up" if nic.link == "up" else "no carrier"
        canvas.put(8 + index, 6,
                   f"{nic.name:<8}{nic.mac:<20}{address:<19}({state})", 68)
    canvas.put(15, 4, "Continue the installation from a browser:")
    if snapshot.console_url:
        canvas.put(17, 6, snapshot.console_url)
    else:
        canvas.put(17, 6, "No address is available. Press F2 to set a static address.")
    if message:
        canvas.put(20, 4, message, 70)
    return canvas.lines()


def render_preinstall_network(interfaces: list[NicInfo], form: dict[str, str],
                              field_index: int = 0, error: str = "") -> list[str]:
    canvas = Canvas(
        _title("Set a Temporary Static Address", "installation"),
        "<Space> Interface  <Tab> Next field  <Enter> Apply  <Esc> Cancel",
    )
    canvas.put(4, 4, "This address lasts only until permanent setup is applied.")
    canvas.put(7, 4, "Interface:")
    for index, nic in enumerate(interfaces[:4]):
        marker = "*" if nic.name == form.get("interface") else " "
        canvas.put(8 + index, 8,
                   f"({marker}) {nic.name:<8} {nic.mac:<17} ({nic.link})", 62)
    cursor = "_"
    canvas.put(14, 4, "IPv4 address / prefix:")
    canvas.put(14, 30, f"[ {form.get('address', '')[:23]:<23}"
               f"{cursor if field_index == 1 else ' '} ]")
    canvas.put(16, 4, "Gateway:")
    canvas.put(16, 30, f"[ {form.get('gateway', '')[:23]:<23}"
               f"{cursor if field_index == 2 else ' '} ]")
    canvas.put(19, 4, error or
               "Applies: ip addr flush/add and ip route replace default.", 70)
    return canvas.lines()


def render_auth(failures: int = 0, lockout_seconds: int = 0,
                password_length: int = 0, message: str = "") -> list[str]:
    canvas = Canvas(
        _title("ImageSrv Server Operations Console", "Authentication Required"),
        "<Enter> OK                    <Esc> Cancel (back to main screen)",
    )
    canvas.put(5, 9, "+----------------------------------------------------------+")
    for row in range(6, 16):
        canvas.put(row, 9, "|")
        canvas.put(row, 68, "|")
    canvas.put(16, 9, "+----------------------------------------------------------+")
    canvas.put(6, 12, "Enter the console admin password to continue.")
    canvas.put(8, 12, "Login Name:   admin                         (fixed)")
    masked = "*" * min(password_length, 25) + "_"
    canvas.put(10, 12, f"Password:     [ {masked:<27} ]")
    canvas.put(12, 12, "The password is verified locally against the server DB;")
    canvas.put(13, 12, "it works even when imagectl-server is not running.")
    if lockout_seconds:
        status = f"Locked after 3 failures. Try again in {lockout_seconds} s."
    else:
        status = f"Wrong password: {failures} of 3 attempts (30 s lockout)."
    canvas.put(15, 12, status)
    canvas.put(18, 9, message or "Authentication is required for F2 and F12 actions.")
    return canvas.lines()


def render_reset(stage: str = "confirm", typed: str = "", failures: int = 0,
                 message: str = "", result: actions.ActionResult | None = None) -> list[str]:
    """#1249: F9 -- reset the admin password by physical presence (like ESXi)."""
    footer = ("<Enter/Esc> Back to main screen" if stage == "done" else
              "<Enter> Continue                       <Esc> Cancel (back to main screen)")
    canvas = Canvas(_title("Reset Admin Password", "physical console only"), footer)
    canvas.put(4, 4, "WARNING: This sets a new password for the console account 'admin'.")
    canvas.put(5, 4, "No current password is needed: being at this screen is the")
    canvas.put(6, 4, "authorization, as with Reset System Configuration on ESXi.")
    canvas.put(8, 4, "The reset also signs out every admin web session, removes admin's")
    canvas.put(9, 4, "two-factor enrollment, if any, and is journaled.")
    if stage == "done":
        result = result or actions.ActionResult(False, "No result.")
        canvas.put(12, 4, result.message, 72)
        for index, detail in enumerate(result.details[:6]):
            canvas.put(14 + index, 6, detail, 70)
        return canvas.lines()
    cursor = "_"
    if stage == "confirm":
        # #1270: no typed word -- this screen and <Enter> are the confirmation.
        canvas.put(11, 4, "Press <Enter> to choose a new password, or <Esc> to cancel.")
    if stage in ("password", "repeat"):
        first = "*" * min(len(typed), 24) if stage == "password" else "*" * 8
        canvas.put(13, 4, f"New password:            [ {first:<24}"
                          f"{cursor if stage == 'password' else ' '} ]")
    if stage == "repeat":
        canvas.put(14, 4, f"Repeat the new password: [ {'*' * min(len(typed), 24):<24}{cursor} ]")
    canvas.put(16, 4, actions.PASSWORD_POLICY)
    canvas.put(17, 4, actions.PASSWORD_SPECIALS)
    canvas.put(19, 4, message, 72)
    canvas.put(20, 4, f"Failed attempts: {failures} of {actions.LOCKOUT_ATTEMPTS} "
                      f"({actions.LOCKOUT_SECONDS} s lockout).")
    return canvas.lines()


def render_menu(snapshot: Snapshot, selected: int = 0, seconds: int = 90) -> list[str]:
    canvas = Canvas(
        _title("System Customization", f"admin  (auto-logout in {seconds} s)"),
        "<Up/Down> Select   <Enter> Open   <Esc> Back to main screen",
    )
    for row in range(3, 22):
        canvas.put(row, 35, "|")
    for index, item in enumerate(MENU_ITEMS):
        canvas.put(4 + index, 2, ("> " if index == selected else "  ") + item, 32)
    canvas.put(12, 3, "Not available here:")
    canvas.put(13, 4, "- delete images")
    canvas.put(14, 4, "- remove secondaries")
    canvas.put(15, 4, "- deploy network / MAC tables")
    canvas.put(16, 3, "(use the web console)")
    descriptions = (
        ("Configure Management Network", "The NIC the web console listens on",
         "(HTTPS 8081). DHCP client or a static", "address, gateway and DNS.",
         f"Current: {snapshot.management.name}  {snapshot.management.address or 'no IPv4'}  {snapshot.management.mode.upper()}",
         "Changes have a 60 s rollback window."),
        ("Restart Management Network", f"ifdown/ifup {snapshot.management.name} with its saved",
         "configuration. Nothing changes, so", "nothing is armed or confirmed.",
         "Success = the address read back live."),
        ("Restart Services", "Restarts imagectl-server and dnsmasq only", "when dnsmasq is enabled, then reads back", "each service state."),
        ("Display TLS Certificate", "Shows SHA-256, certificate SANs and", "the not-after date read from the cert."),
        ("Primary-Visible Address", "The address at which the primary", "server reaches this secondary (port",
         "8443); behind NAT, the outside one.", "F4 shows it on the pairing screen."),
        ("Add Disk", "An EMPTY disk in this server becomes", "an image storage location: GPT, ext4,",
         "mounted by UUID (fstab, nofail).", "Only disks with no partition table,",
         "no signature and zeroed head/tail.", "ALL DATA ON THE DISK IS ERASED."),
    )
    for row, line in enumerate(descriptions[selected], start=4):
        canvas.put(row, 38, line, 39)
    canvas.put(14, 38, "The deploy network is not set here:")
    canvas.put(15, 38, "use the console (Network > Deploy).")
    return canvas.lines()


def render_network(snapshot: Snapshot, interfaces: list[NicInfo], form: dict[str, str],
                   field_index: int = 0, seconds: int = 90,
                   rollback_seconds: int = 0, error: str = "") -> list[str]:
    canvas = Canvas(
        _title("Configure Management Network", f"admin  (auto-logout in {seconds} s)"),
        "<Space> Toggle  <Tab> Next field  <Enter> Apply (60 s window)  <Esc> Cancel",
    )
    canvas.put(3, 3, "Interface (web console, HTTPS port 8081):")
    selected = form.get("interface", "")
    for index, nic in enumerate(interfaces[:3]):
        marker = "*" if nic.name == selected else " "
        current = " <- current" if nic.name == snapshot.management.name else ""
        model = (nic.model + " ") if nic.model else ""
        canvas.put(5 + index, 5,
                   f"({marker}) {nic.name:<7} {model[:18]:<18} {nic.mac:<17} "
                   f"link {nic.link} {nic.speed}{current}", 70)
    canvas.put(9, 3, "Address:")
    canvas.put(11, 5, f"({'*' if form.get('mode') == 'dhcp' else ' '}) DHCP client")
    canvas.put(12, 5, f"({'*' if form.get('mode') == 'static' else ' '}) Static")
    values = (
        ("IPv4 Address:", "address"), ("Prefix / Mask:", "netmask"),
        ("Gateway:", "gateway"), ("DNS:", "dns"),
    )
    for index, (label, key) in enumerate(values):
        cursor = "_" if field_index == index + 2 else ""
        canvas.put(14 + index, 8, f"{label:<16} [ {form.get(key, '')[:22]:<22}{cursor} ]")
    canvas.put(19, 3, "After <Enter>: applied for 60 s, then rolled back unless confirmed.")
    if rollback_seconds:
        canvas.put(20, 5, f"[C] Confirm   [R] Rollback now   (auto-rollback in {rollback_seconds} s)")
    elif error:
        canvas.put(20, 5, error, 70)
    else:
        canvas.put(20, 5, "Apply (60 s rollback) after reviewing every field.")
    return canvas.lines()


def render_power(snapshot: Snapshot, choice: str = "restart",
                 seconds: int = 90, message: str = "",
                 focus: str = "cancel") -> list[str]:
    # #1270: no typed hostname -- this screen (behind the admin password) and
    # <Enter> are the confirmation; <Esc> runs nothing.
    action = "Restart" if choice == "restart" else "Shut down"
    canvas = Canvas(
        _title("Restart / Shut Down", f"admin  (auto-logout in {seconds} s)"),
        "<Arrows> Choice  <Tab> Switch button  <Enter> Run marked button  <Esc> Cancel",
    )
    canvas.put(5, 9, "+----------------------------------------------------------+")
    for row in range(6, 18):
        canvas.put(row, 9, "|")
        canvas.put(row, 68, "|")
    canvas.put(18, 9, "+----------------------------------------------------------+")
    canvas.put(6, 12, "What should the server do?")
    canvas.put(8, 14, f"({'*' if choice == 'restart' else ' '}) Restart")
    canvas.put(9, 14, f"({'*' if choice == 'shutdown' else ' '}) Shut down")
    if snapshot.deploy_round and snapshot.deploy_round.state == "running":
        item = snapshot.deploy_round
        canvas.put(11, 12, f"WARNING: A deploy round is running: {item.writing} machines writing", 55)
        canvas.put(12, 14, f"{item.label}  {item.writing} of {item.targets} targets are writing", 52)
        canvas.put(13, 12, "Restarting now fails active targets; warn only, do not block.", 55)
    else:
        canvas.put(11, 12, "No running deploy round was found.")
    canvas.put(15, 12, f"Server: {snapshot.hostname}", 55)
    # #1203: the focus starts on Cancel; <Tab> moves it to the action.
    cancel = ">> <Cancel>" if focus != "act" else "   <Cancel>"
    act = f">> <{action} now>" if focus == "act" else f"   <{action} now>"
    canvas.put(16, 12, f"{cancel}    {act}", 55)
    if snapshot.rollback_interface:
        canvas.put(20, 9, f"Network rollback pending on {snapshot.rollback_interface} "
                          f"({snapshot.rollback_seconds} s left).", 62)
    else:
        canvas.put(20, 9, message or "Every web session and deploy client loses the server.", 62)
    return canvas.lines()


def render_tls(snapshot: Snapshot, seconds: int = 90) -> list[str]:
    canvas = Canvas(
        _title("TLS Certificate", f"admin  (auto-logout in {seconds} s)"),
        "<Esc> Back to System Customization",
    )
    fp1, fp2 = _fingerprint_lines(snapshot.fingerprint)
    canvas.put(5, 4, "SHA-256 fingerprint:")
    canvas.put(7, 6, fp1)
    canvas.put(8, 6, fp2)
    canvas.put(11, 4, "Subject Alternative Names:")
    for index, value in enumerate(snapshot.certificate_sans[:5]):
        canvas.put(12 + index, 6, f"- {value}")
    canvas.put(18, 4, f"Not after: {snapshot.certificate_not_after}")
    return canvas.lines()


def render_message(title: str, message: str, details: tuple[str, ...] = (),
                   seconds: int = 90) -> list[str]:
    canvas = Canvas(_title(title, f"admin  (auto-logout in {seconds} s)"),
                    "<Enter/Esc> Back to System Customization")
    canvas.put(6, 5, message, 68)
    for index, detail in enumerate(details[:8]):
        canvas.put(8 + index, 7, detail, 66)
    return canvas.lines()


def _disk_line(disk: dict) -> str:
    size = disk.get("size_bytes")
    gb = f"{size / 1e9:.0f} GB" if isinstance(size, int) else "size ?"
    return f"{disk.get('device', '?'):<14} {gb:>8}  {(disk.get('model') or '')[:20]:<20} {disk.get('serial') or ''}"


def render_disks(listing: dict | None, selected: int = 0, seconds: int = 90,
                 message: str = "") -> list[str]:
    """#1988: the empty disks (selectable) and the others with the reason."""
    canvas = Canvas(_title("Add Disk", f"admin  (auto-logout in {seconds} s)"),
                    "<Up/Down> Select  <Enter> Continue  <F5> Re-read  <Esc> Back")
    if listing is None:
        canvas.put(5, 4, "The disk list could not be read - this is NOT 'no empty disk'.", 72)
        canvas.put(7, 4, message, 72)
        return canvas.lines()
    items, hidden = listing.get("items") or [], listing.get("hidden") or []
    canvas.put(3, 3, "Empty disks (no partition table, no signature, zeroed head/tail):")
    if not items:
        canvas.put(5, 5, "No empty disk was found in this server.")
    for index, disk in enumerate(items[:6]):
        canvas.put(5 + index, 3, ("> " if index == selected else "  ") + _disk_line(disk), 74)
    canvas.put(12, 3, f"Not offered ({len(hidden)}):")
    for index, disk in enumerate(hidden[:6]):
        reason = disk.get("reason") or ""
        reason = reason if reason.isascii() else "has data or could not be read"
        canvas.put(13 + index, 5, f"{disk.get('device', '?'):<14} {reason}", 72)
    canvas.put(20, 3, message or "Nothing is changed until you confirm on the next screen.", 74)
    return canvas.lines()


def render_disk_confirm(disk: dict, seconds: int = 90, focus: str = "cancel") -> list[str]:
    canvas = Canvas(_title("Add Disk - confirm", f"admin  (auto-logout in {seconds} s)"),
                    "<Tab> Switch button  <Enter> Run marked button  <Esc> Cancel")
    canvas.put(5, 4, "WARNING: ALL DATA ON THIS DISK WILL BE ERASED.")
    canvas.put(7, 6, _disk_line(disk), 70)
    canvas.put(9, 4, "The server will: create a GPT with one partition, make ext4,")
    canvas.put(10, 4, "mount it by UUID (/etc/fstab, nofail), and check a written file")
    canvas.put(11, 4, "reads back. A failure is shown and no location is left behind.")
    cancel = ">> <Cancel>" if focus != "act" else "   <Cancel>"
    act = ">> <Erase and add>" if focus == "act" else "   <Erase and add>"
    canvas.put(14, 6, f"{cancel}    {act}", 60)
    return canvas.lines()


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def spki_groups(spki: str) -> list[str]:
    """64 hex -> 16 upper-case groups of four, as the primary shows them."""
    clean = spki.upper()
    return [clean[i:i + 4] for i in range(0, len(clean), 4)]


def render_pair(code: pairing.Code | None, stage: str, now_wall: float,
                error: str = "", paired: dict | None = None) -> list[str]:
    """F4 on a secondary (16/10): the one-time code, by physical presence."""
    footers = {
        "code": "<F5> New code         <Esc> Close (the code is cancelled)",
        "paired": "<Enter/Esc> Back to main screen",
    }
    canvas = Canvas(_title("Pairing with the Primary Server", "secondary server"),
                    footers.get(stage, "<Enter> New code      <Esc> Back to main screen"))
    if stage == "paired":
        paired = paired or {}
        canvas.put(5, 4, "Paired. The code was used and can not be used again.")
        canvas.put(8, 4, f"Primary server:  {paired.get('primary_url') or 'unknown'}", 72)
        canvas.put(9, 4, f"Primary ID:      {paired.get('parent_id') or 'unknown'}", 72)
        canvas.put(10, 4, f"Paired at:       {paired.get('enrolled_at') or 'unknown'}", 72)
        return canvas.lines()
    if stage in ("expired", "closed") or code is None:
        canvas.put(5, 4, "WARNING: " + ("The code expired." if stage == "expired" else
                                        "The code was closed before its time."))
        canvas.put(7, 4, "It can no longer be used, and no pairing was made.")
        if stage == "closed":
            canvas.put(8, 4, "(Wrong tries on the primary, or a newer code was opened.)")
        canvas.put(10, 4, "Press <Enter> for a new code.")
        return canvas.lines()
    canvas.put(4, 4, "One-time pairing code -- type it on the primary server:")
    canvas.put(6, 20, code.code)
    canvas.put(6, 40, f"({_clock(code.expires - now_wall)} left)")
    groups = spki_groups(code.spki)
    if groups:
        canvas.put(8, 4, f"Server identity:  {' '.join(groups[:4])}   ({code.node_id})", 72)
        canvas.put(9, 4, f"Full SPKI SHA-256: {' '.join(groups[:8])}", 72)
        canvas.put(10, 23, " ".join(groups[8:16]), 54)
    else:
        canvas.put(8, 4, "WARNING: This server has no inter-server identity yet;", 72)
        canvas.put(9, 4, "the primary cannot connect. Restart the services (F2).", 72)
    if code.advertised:
        canvas.put(12, 4, f"Address the primary types: {code.advertised}", 72)
    else:
        canvas.put(12, 4, "WARNING: The address the primary sees is not set.", 72)
        canvas.put(13, 4, "Set it in F2 > Primary-Visible Address.", 72)
    canvas.put(15, 4, "On the primary: Branches > Register secondary. Type the address,")
    canvas.put(16, 4, "check that the identity matches this screen, then type the code.")
    canvas.put(18, 4, "Single use, 10 minutes, 5 wrong tries. Leaving this screen cancels it.")
    if error:
        canvas.put(20, 4, error, 72)
    return canvas.lines()


def render_unpair(primary_url: str = "") -> list[str]:
    canvas = Canvas(_title("Disconnect from the Primary Server", "secondary server"),
                    "<Enter> Disconnect                      <Esc> Cancel (nothing changes)")
    canvas.put(5, 4, "WARNING: Disconnect this server from the primary server?")
    canvas.put(7, 4, f"The primary {primary_url} stops seeing this server.", 72)
    canvas.put(8, 4, "Pairing again needs a new code (F4). Images in storage are not deleted.")
    canvas.put(11, 4, "No typing is needed: <Enter> on this screen is the confirmation.")
    return canvas.lines()


def render_advertised(value: str, seconds: int = 90, error: str = "") -> list[str]:
    canvas = Canvas(_title("Primary-Visible Address", f"admin  (auto-logout in {seconds} s)"),
                    "<Enter> Save                            <Esc> Cancel")
    canvas.put(4, 4, "The address at which the primary server reaches this server")
    canvas.put(5, 4, "(port 8443). Behind NAT: the outside address. Empty clears it.")
    canvas.put(8, 4, f"Address:  [ {value[:44]:<44}_ ]")
    canvas.put(10, 4, "Example: 203.0.113.40:8443   (https:// is added)")
    if error:
        canvas.put(13, 4, error, 72)
    return canvas.lines()


def render_demo() -> str:
    snapshot = demo_snapshot()
    interfaces = [snapshot.management,
                  NicInfo("ens20", "", "static", "up", "1000",
                          "52:54:00:aa:14:01", "Intel I350"),
                  NicInfo("ens21", "", "dhcp", "down", "unknown",
                          "52:54:00:aa:99:01", "Intel I350")]
    form = {"interface": "ens20", "mode": "static", "address": "10.44.9.12",
            "netmask": "255.255.255.0", "gateway": "10.44.9.254",
            "dns": "10.44.9.2"}
    screens = [
        render_main(snapshot), render_auth(1, 0, 11), render_menu(snapshot),
        render_network(snapshot, interfaces, form, rollback_seconds=47),
        render_power(snapshot),
    ]
    return "\n\n".join("\n".join(screen) for screen in screens)


@dataclass
class Runtime:
    data_dir: Path
    conn: object
    snapshot: Snapshot
    screen: str = "main"
    auth_target: str = "menu"
    password: str = ""
    auth: actions.AuthGuard = field(default_factory=actions.AuthGuard)
    authenticated_at: float = 0.0
    last_key_at: float = 0.0
    menu_index: int = 0
    message: str = ""
    result: actions.ActionResult | None = None
    power_choice: str = "restart"
    form: dict[str, str] = field(default_factory=dict)
    form_field: int = 0
    interfaces: list[NicInfo] = field(default_factory=list)
    last_refresh: float = 0.0
    reset_stage: str = "confirm"
    reset_typed: str = ""
    reset_first: str = ""
    reset_guard: actions.AuthGuard = field(default_factory=actions.AuthGuard)
    # #1203: the graphical screen. power_focus starts on "cancel" so a stray
    # <Enter> in F12 runs nothing; caps is the keyboard's Caps Lock (the GUI
    # reports it); extras are the facts only the graphical screen shows.
    power_focus: str = "cancel"
    caps: bool = False
    extras: object = None
    # 16/10: F4 on a secondary -- the code screen (pair_stage: code | expired |
    # closed | paired), the disconnect confirmation, and F2's address field.
    pair: pairing.Code | None = None
    pair_stage: str = "code"
    pair_checked: float = 0.0
    pair_error: str = ""
    paired: dict = field(default_factory=dict)
    advert: str = ""
    # #1988: "Add Disk" -- the listing read on entry (None = not read, with
    # the reason in message), the selected row and the confirm focus.
    storage_hooks: dict | None = None
    disks: dict | None = None
    disk_index: int = 0
    disk_focus: str = "cancel"

    def seconds(self, now: float) -> int:
        return max(0, actions.AUTH_IDLE_SECONDS - int(now - self.last_key_at))


def _initial_form(snapshot: Snapshot) -> dict[str, str]:
    nic = snapshot.management
    address = nic.address.split("/")[0] if nic.address else ""
    prefix = nic.address.split("/", 1)[1] if "/" in nic.address else "255.255.255.0"
    return {"interface": nic.name, "mode": nic.mode if nic.mode in {"dhcp", "static"} else "dhcp",
            "address": address, "netmask": prefix, "gateway": "", "dns": ""}


def _preinstall_form(snapshot: Snapshot) -> dict[str, str]:
    nic = snapshot.management
    address = nic.address if nic.address else ""
    return {"interface": nic.name, "address": address, "gateway": ""}


def _refresh(runtime: Runtime) -> None:
    db_path = runtime.data_dir / "imagectl.db"
    if runtime.conn is None and db_path.is_file():
        runtime.conn = connect(db_path)
    runtime.snapshot = collect_snapshot(runtime.data_dir, conn=runtime.conn)
    runtime.last_refresh = time.monotonic()
    runtime.interfaces = list(runtime.snapshot.interfaces or (runtime.snapshot.management,))
    if not runtime.form:
        runtime.form = _initial_form(runtime.snapshot)


def _current_lines(runtime: Runtime, now: float) -> list[str]:
    seconds = runtime.seconds(now)
    if runtime.screen == "main":
        if runtime.snapshot.pre_install:
            return render_preinstall(runtime.snapshot, runtime.message)
        lock = runtime.auth.seconds_left(time.time())
        message = runtime.message or (f"Authentication locked for {lock} more seconds." if lock else "")
        return render_main(runtime.snapshot, message)
    if runtime.screen == "preinstall_network":
        return render_preinstall_network(
            runtime.interfaces, runtime.form, runtime.form_field, runtime.message)
    if runtime.screen == "auth":
        return render_auth(runtime.auth.failures, runtime.auth.seconds_left(time.time()),
                           len(runtime.password), runtime.message)
    if runtime.screen == "reset":
        return render_reset(runtime.reset_stage, runtime.reset_typed,
                            runtime.reset_guard.failures, runtime.message, runtime.result)
    if runtime.screen == "menu":
        return render_menu(runtime.snapshot, runtime.menu_index, seconds)
    if runtime.screen == "network":
        return render_network(runtime.snapshot, runtime.interfaces, runtime.form,
                              runtime.form_field, seconds, error=runtime.message)
    if runtime.screen == "network_confirm":
        marker = netcfg_rollback.read_pending(runtime.data_dir / "netcfg")
        left = max(0, int(marker.deadline - time.time())) if marker else 0
        return render_network(runtime.snapshot, runtime.interfaces, runtime.form,
                              runtime.form_field, seconds, rollback_seconds=left,
                              error=runtime.message)
    if runtime.screen == "power":
        return render_power(runtime.snapshot, runtime.power_choice, seconds,
                            runtime.message, runtime.power_focus)
    if runtime.screen == "tls":
        return render_tls(runtime.snapshot, seconds)
    if runtime.screen == "pair":
        return render_pair(runtime.pair, runtime.pair_stage, time.time(),
                           runtime.pair_error, runtime.paired)
    if runtime.screen == "unpair":
        return render_unpair(pairing.paired_details(runtime.conn)["primary_url"]
                             if runtime.conn is not None else "")
    if runtime.screen == "advertised":
        return render_advertised(runtime.advert, seconds, runtime.message)
    if runtime.screen == "disk":
        return render_disks(runtime.disks, runtime.disk_index, seconds, runtime.message)
    if runtime.screen == "disk_confirm" and _chosen_disk(runtime):
        return render_disk_confirm(_chosen_disk(runtime), seconds, runtime.disk_focus)
    result = runtime.result or actions.ActionResult(False, runtime.message or "No result.")
    return render_message("Action Result", result.message, result.details, seconds)


def _typable(key: object) -> bool:
    """#1203: passwords are English only -- a Hebrew letter (a tty keymap, or
    anything the GUI did not force to US) is not typed into a password."""
    return (isinstance(key, str) and len(key) == 1 and key.isascii()
            and key.isprintable())


def _is_backspace(key: object, curses_module) -> bool:
    """#1258: Backspace in every form it reaches us -- get_wch() on tty1 gives
    "\x7f" (or "\b") as str, keypad mode gives KEY_BACKSPACE, and the GUI
    (server/dcui/gui.py) sends KEY_BACKSPACE as well."""
    return key in (8, 127, "\b", "\x7f", curses_module.KEY_BACKSPACE)


def _authenticate(runtime: Runtime) -> None:
    if runtime.auth.authenticate(runtime.conn, runtime.password):
        runtime.screen = runtime.auth_target
        runtime.power_choice, runtime.power_focus = "restart", "cancel"
        runtime.last_key_at = time.monotonic()
        runtime.authenticated_at = runtime.last_key_at
        runtime.message = ""
    elif runtime.auth.locked_until:
        runtime.screen = "main"
        runtime.message = "Authentication locked for 30 seconds after 3 failures."
    else:
        runtime.message = "Wrong password."
    runtime.password = ""


def _leave_reset(runtime: Runtime, message: str) -> None:
    runtime.screen = "main"
    runtime.message = message
    runtime.reset_stage = "confirm"
    runtime.reset_typed = runtime.reset_first = ""


def _start_reset(runtime: Runtime) -> None:
    guard = runtime.reset_guard
    lock = guard.seconds_left(time.time())
    if lock:
        runtime.message = f"Admin password reset is locked for {lock} more seconds."
        return
    if guard.locked_until:
        guard.failures, guard.locked_until = 0, 0.0
    _leave_reset(runtime, "")
    runtime.screen = "reset"
    runtime.result = None


def _reset_enter(runtime: Runtime) -> None:
    typed, runtime.reset_typed = runtime.reset_typed, ""
    if runtime.reset_stage == "confirm":
        # #1270: <Enter> on the warning screen is the confirmation -- no word.
        runtime.reset_stage, runtime.message = "password", ""
        return
    if runtime.reset_stage == "password":
        problem = actions.password_problem(typed)
        if problem is None:
            runtime.reset_first, runtime.reset_stage, runtime.message = typed, "repeat", ""
            return
        reason = "password policy"
    elif typed == runtime.reset_first:
        runtime.result = actions.physical_reset_admin_password(runtime.conn, typed)
        runtime.reset_first, runtime.reset_stage, runtime.message = "", "done", ""
        if runtime.result.ok:
            runtime.reset_guard = actions.AuthGuard()
        return
    else:
        reason, problem = "passwords differ", "The two passwords differ. Enter the new password again."
        runtime.reset_first, runtime.reset_stage = "", "password"
    actions.record_reset_failure(runtime.conn, runtime.reset_guard, reason)
    if runtime.reset_guard.seconds_left(time.time()):
        _leave_reset(runtime, f"Admin password reset locked for {actions.LOCKOUT_SECONDS} "
                              f"seconds after {actions.LOCKOUT_ATTEMPTS} failures.")
    else:
        runtime.message = problem


def _reset_key(runtime: Runtime, key: object, enter: bool, escape: bool,
               curses_module) -> None:
    if runtime.reset_stage == "done":
        if enter or escape:
            result = runtime.result
            _leave_reset(runtime, result.message if result else "")
        return
    if escape:
        _leave_reset(runtime, "Admin password reset cancelled; nothing was changed.")
    elif enter:
        _reset_enter(runtime)
    elif runtime.reset_stage == "confirm":
        return                                  # #1270: nothing is typed to confirm
    elif _is_backspace(key, curses_module):
        runtime.reset_typed = runtime.reset_typed[:-1]
    elif _typable(key):
        runtime.reset_typed += key


def _open_menu_item(runtime: Runtime) -> None:
    item = runtime.menu_index
    runtime.message = ""
    if item == 0:
        runtime.screen = "network"
        runtime.form = _initial_form(runtime.snapshot)
        runtime.form_field = 0
    elif item == 1:
        runtime.result = actions.restart_management_network(
            runtime.conn, runtime.snapshot.management.name, runtime.data_dir)
        runtime.screen = "result"
    elif item == 2:
        runtime.result = actions.restart_services(runtime.conn)
        runtime.screen = "result"
    elif item == 3:
        actions.note_tls_display(runtime.conn)
        runtime.screen = "tls"
    elif item == 4:
        current = pairing.advertised(runtime.conn)
        runtime.advert = current.removeprefix("https://")
        runtime.screen = "advertised"
    elif item == 5:
        _load_disks(runtime)
        runtime.screen = "disk"


def _load_disks(runtime: Runtime) -> None:
    runtime.disks, failure = actions.blank_disks(runtime.storage_hooks)
    runtime.disk_index, runtime.disk_focus = 0, "cancel"
    runtime.message = failure.message if failure else ""
    runtime.result = failure


def _chosen_disk(runtime: Runtime) -> dict | None:
    items = (runtime.disks or {}).get("items") or []
    return items[runtime.disk_index] if 0 <= runtime.disk_index < len(items) else None


def _disk_key(runtime: Runtime, key: object, enter: bool, curses_module) -> None:
    if runtime.screen == "disk":
        count = len((runtime.disks or {}).get("items") or [])
        if key == curses_module.KEY_F5:
            _load_disks(runtime)
        elif key == curses_module.KEY_UP and count:
            runtime.disk_index = (runtime.disk_index - 1) % count
        elif key == curses_module.KEY_DOWN and count:
            runtime.disk_index = (runtime.disk_index + 1) % count
        elif enter and _chosen_disk(runtime):
            runtime.screen, runtime.disk_focus, runtime.message = "disk_confirm", "cancel", ""
        return
    if key in (9, "\t", curses_module.KEY_BTAB, curses_module.KEY_LEFT, curses_module.KEY_RIGHT):
        runtime.disk_focus = "act" if runtime.disk_focus == "cancel" else "cancel"
    elif enter and runtime.disk_focus == "act" and _chosen_disk(runtime):
        runtime.result = actions.add_disk(runtime.conn, runtime.data_dir, _chosen_disk(runtime),
                                          runtime.storage_hooks)
        runtime.screen, runtime.disks = "result", None
    elif enter:
        # the focus starts on Cancel -- a stray <Enter> erases nothing
        runtime.screen, runtime.message = "disk", "Cancelled; nothing was erased."


#: Never logged out after 90 s: no admin session behind them, and the code
#: screen stays up for the code's 10 minutes.
NO_ADMIN_SCREENS = {"main", "auth", "pair"}
ADVERT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-[]/")


def _new_code(runtime: Runtime) -> None:
    result, code = pairing.open_code(runtime.conn)
    runtime.pair_error = ""
    if code is None:
        runtime.screen, runtime.message = "main", result.message
        return
    runtime.pair, runtime.pair_stage = code, "code"
    runtime.pair_checked = time.monotonic()
    runtime.screen, runtime.message = "pair", ""


def _f4(runtime: Runtime) -> None:
    """F4 exists only on a secondary: a code when unpaired, the disconnect
    confirmation when paired.  On a primary/standalone it does nothing."""
    if runtime.snapshot.storage_role != "secondary":
        return
    if runtime.conn is None:
        runtime.message = "The server database is not available."
        return
    if pairing.has_parent(runtime.conn):
        runtime.screen, runtime.message = "unpair", ""
    else:
        _new_code(runtime)


def _leave_pair(runtime: Runtime, message: str = "") -> None:
    if runtime.pair_stage == "code" and runtime.conn is not None and runtime.pair:
        pairing.close_code(runtime.conn, runtime.pair)
        message = message or "Pairing code cancelled; it can no longer be used."
    runtime.pair = None
    runtime.screen, runtime.message = "main", message


def _pair_key(runtime: Runtime, key: object, enter: bool, escape: bool,
              curses_module) -> None:
    if runtime.pair_stage == "code":
        if escape:
            _leave_pair(runtime)
        elif key == curses_module.KEY_F5:
            _new_code(runtime)
    elif runtime.pair_stage == "paired":
        if enter or escape:
            _leave_pair(runtime, "Paired with the primary server.")
    elif escape:
        _leave_pair(runtime)
    elif enter:
        _new_code(runtime)


def check_pairing(runtime: Runtime, now: float, wall: float) -> None:
    """Once a second on the code screen: did the primary use it, did it end."""
    if runtime.screen != "pair" or runtime.pair_stage != "code" or runtime.pair is None:
        return
    if now - runtime.pair_checked < 1.0:
        return
    runtime.pair_checked = now
    found = pairing.state(runtime.conn, runtime.pair, wall)
    runtime.pair_error = ("The code's state could not be read from the DB."
                          if found == "unchecked" else "")
    if found == "paired":
        runtime.pair_stage, runtime.paired = "paired", pairing.paired_details(runtime.conn)
    elif found in ("expired", "closed"):
        runtime.pair_stage = found


def _network_key(runtime: Runtime, key: object, curses_module) -> None:
    if key in (9, "\t"):
        runtime.form_field = (runtime.form_field + 1) % 6
        return
    if key == " ":
        if runtime.form_field == 0 and runtime.interfaces:
            names = [nic.name for nic in runtime.interfaces]
            index = names.index(runtime.form["interface"]) if runtime.form["interface"] in names else 0
            runtime.form["interface"] = names[(index + 1) % len(names)]
        elif runtime.form_field == 1:
            runtime.form["mode"] = "static" if runtime.form["mode"] == "dhcp" else "dhcp"
        return
    field_names = ("address", "netmask", "gateway", "dns")
    if 2 <= runtime.form_field <= 5:
        field = field_names[runtime.form_field - 2]
        if _is_backspace(key, curses_module):
            runtime.form[field] = runtime.form[field][:-1]
        elif isinstance(key, str) and len(key) == 1 and key in "0123456789.,":
            runtime.form[field] += key


def _preinstall_network_key(runtime: Runtime, key: object, curses_module) -> None:
    if key in (9, "\t"):
        runtime.form_field = (runtime.form_field + 1) % 3
        return
    if key == " " and runtime.form_field == 0 and runtime.interfaces:
        names = [nic.name for nic in runtime.interfaces]
        current = runtime.form.get("interface", "")
        index = names.index(current) if current in names else 0
        runtime.form["interface"] = names[(index + 1) % len(names)]
        return
    if runtime.form_field in (1, 2):
        field = "address" if runtime.form_field == 1 else "gateway"
        if _is_backspace(key, curses_module):
            runtime.form[field] = runtime.form[field][:-1]
        elif isinstance(key, str) and len(key) == 1 and key in "0123456789./":
            runtime.form[field] += key


def _handle_key(runtime: Runtime, key: object, curses_module) -> bool:
    now = time.monotonic()
    if key != -1:
        runtime.last_key_at = now
    # get_wch() returns str for ordinary keys: Enter is "\n" and Esc "\x1b",
    # not 10/27 (#1249: the F9 reset needs Enter on the real tty1).
    enter = key in (10, 13, "\n", "\r", curses_module.KEY_ENTER)
    escape = key in (27, "\x1b")
    if runtime.screen not in NO_ADMIN_SCREENS and runtime.seconds(now) <= 0:
        runtime.screen = "main"
        runtime.message = "Session expired after 90 seconds without a key."
        return True
    if runtime.screen == "main":
        if runtime.snapshot.pre_install:
            if key == curses_module.KEY_F5:
                _refresh(runtime)
            elif key == curses_module.KEY_F2:
                runtime.screen = "preinstall_network"
                runtime.form = _preinstall_form(runtime.snapshot)
                runtime.form_field = 0
                runtime.message = ""
            return True
        if key == curses_module.KEY_F5:
            _refresh(runtime)
        elif key == curses_module.KEY_F4:
            _f4(runtime)
        elif key == curses_module.KEY_F9:
            _start_reset(runtime)
        elif key in (curses_module.KEY_F2, curses_module.KEY_F12):
            if runtime.auth.seconds_left(time.time()):
                runtime.message = "Authentication is temporarily locked."
            else:
                runtime.auth_target = "menu" if key == curses_module.KEY_F2 else "power"
                runtime.screen = "auth"
                runtime.password = ""
                runtime.message = ""
        return True
    if runtime.screen == "auth":
        if escape:
            runtime.screen = "main"
        elif enter:
            _authenticate(runtime)
        elif _is_backspace(key, curses_module):
            runtime.password = runtime.password[:-1]
        elif _typable(key):
            runtime.password += key
        return True
    if runtime.screen == "reset":
        _reset_key(runtime, key, enter, escape, curses_module)
        return True
    if runtime.screen == "pair":
        _pair_key(runtime, key, enter, escape, curses_module)
        return True
    if runtime.screen == "unpair":
        if enter:
            result = pairing.disconnect(runtime.conn)
            runtime.screen, runtime.message = "main", result.message
        elif escape:
            runtime.screen, runtime.message = "main", "Disconnect cancelled; nothing was changed."
        return True
    if runtime.screen == "advertised":
        if escape:
            runtime.screen, runtime.message = "menu", ""
        elif enter:
            runtime.result = pairing.save_advertised(runtime.conn, runtime.advert)
            if runtime.result.ok:
                runtime.screen, runtime.message = "result", ""
            else:
                runtime.message = runtime.result.message
        elif _is_backspace(key, curses_module):
            runtime.advert = runtime.advert[:-1]
        elif isinstance(key, str) and key in ADVERT_CHARS and len(runtime.advert) < 64:
            runtime.advert += key
        return True
    if runtime.screen == "preinstall_network":
        if escape:
            runtime.screen = "main"
            runtime.message = ""
        elif enter:
            runtime.result = actions.apply_preinstall_static(
                runtime.form, {nic.name for nic in runtime.interfaces})
            runtime.message = runtime.result.message
            if runtime.result.ok:
                runtime.screen = "main"
                _refresh(runtime)
        else:
            _preinstall_network_key(runtime, key, curses_module)
        return True
    if escape:
        if runtime.screen == "network_confirm":
            active, _detail = actions.netcfg_host.timer_active()
            runtime.result = actions.ActionResult(
                active,
                ("Rollback remains armed; the previous configuration will return."
                 if active else "Rollback timer is not active; recovery was not verified."),
            )
            runtime.screen = "result"
            return True
        runtime.screen = "main" if runtime.screen in {"menu", "power"} else "menu"
        return True
    if runtime.screen == "menu":
        if key == curses_module.KEY_UP:
            runtime.menu_index = (runtime.menu_index - 1) % len(MENU_ITEMS)
        elif key == curses_module.KEY_DOWN:
            runtime.menu_index = (runtime.menu_index + 1) % len(MENU_ITEMS)
        elif enter:
            _open_menu_item(runtime)
    elif runtime.screen == "network":
        if enter:
            runtime.result = actions.apply_network(
                runtime.conn, runtime.form, runtime.data_dir,
                {nic.name for nic in runtime.interfaces})
            if runtime.result.ok:
                runtime.screen = "network_confirm"
            else:
                runtime.message = runtime.result.message
        else:
            _network_key(runtime, key, curses_module)
    elif runtime.screen == "network_confirm":
        if key in ("c", "C"):
            runtime.result = actions.confirm_network(runtime.conn, runtime.data_dir)
            runtime.screen = "result"
        elif key in ("r", "R"):
            runtime.result = actions.request_network_rollback(runtime.conn, runtime.data_dir)
            runtime.screen = "result"
    elif runtime.screen == "power":
        if key in (curses_module.KEY_UP, curses_module.KEY_DOWN, curses_module.KEY_LEFT,
                   curses_module.KEY_RIGHT, " "):
            runtime.power_choice = "shutdown" if runtime.power_choice == "restart" else "restart"
        elif key in (9, "\t", curses_module.KEY_BTAB):
            runtime.power_focus = "act" if runtime.power_focus == "cancel" else "cancel"
        elif enter and runtime.power_focus == "act":
            runtime.result = actions.power_action(runtime.conn, runtime.power_choice)
            runtime.screen = "result"
        elif enter:
            # #1203: the focus starts on Cancel -- a stray <Enter> runs nothing.
            runtime.screen = "main"
            runtime.message = "Restart/shutdown cancelled; nothing was done."
    elif runtime.screen in {"disk", "disk_confirm"}:
        _disk_key(runtime, key, enter, curses_module)
    elif runtime.screen in {"result", "tls"} and enter:
        runtime.screen = "menu"
    return True


def _draw(stdscr, lines: list[str], curses_module) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < HEIGHT or width < WIDTH:
        message = "terminal too small (80x25 needed)"
        try:
            stdscr.addstr(0, 0, message[:max(0, width - 1)])
        except curses_module.error:
            pass
        stdscr.refresh()
        return
    top, left = (height - HEIGHT) // 2, (width - WIDTH) // 2
    for index, line in enumerate(lines):
        attr = curses_module.A_NORMAL
        if "WARNING:" in line and curses_module.has_colors():
            attr = curses_module.color_pair(1) | curses_module.A_BOLD
        try:
            stdscr.addstr(top + index, left, line, attr)
        except curses_module.error:
            # ncurses may report ERR after successfully painting bottom-right.
            pass
    stdscr.refresh()


def new_runtime(data_dir: Path) -> Runtime:
    """The state both screens start from (text and graphical, #1203)."""
    db_path = data_dir / "imagectl.db"
    conn = connect(db_path) if db_path.is_file() else None
    snapshot = collect_snapshot(data_dir, conn=conn)
    runtime = Runtime(data_dir, conn, snapshot,
                      last_key_at=time.monotonic(), last_refresh=time.monotonic())
    runtime.form = _initial_form(snapshot)
    runtime.interfaces = list(snapshot.interfaces or (snapshot.management,))
    return runtime


def tick(runtime: Runtime, now: float) -> None:
    """The clock-driven part of the loop, shared by both screens: the 5 s
    refresh, the 90 s auto-logout and the end of a network rollback window."""
    if runtime.screen == "main" and now - runtime.last_refresh >= 5:
        _refresh(runtime)
    if runtime.screen not in NO_ADMIN_SCREENS and runtime.seconds(now) <= 0:
        runtime.screen = "main"
        runtime.message = "Session expired after 90 seconds without a key."
    check_pairing(runtime, now, time.time())
    if (runtime.screen == "network_confirm"
            and netcfg_rollback.read_pending(runtime.data_dir / "netcfg") is None):
        active, detail = actions.netcfg_host.timer_active()
        runtime.result = actions.ActionResult(
            active,
            "The pending marker is gone; the timed rollback has completed.",
            (str(detail),),
        )
        runtime.screen = "result"


def curses_main(stdscr, data_dir: Path = DATA_DIR, why_text: str = "") -> None:
    import curses
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(200)
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    runtime = new_runtime(data_dir)
    if why_text:
        # #1203: the text screen is the fallback -- say why, do not just appear.
        runtime.message = f"Graphic screen unavailable: {why_text}"[:72]
    while True:
        now = time.monotonic()
        tick(runtime, now)
        _draw(stdscr, _current_lines(runtime, now), curses)
        try:
            key = stdscr.get_wch()
        except curses.error:
            key = -1
        _handle_key(runtime, key, curses)


def run(data_dir: Path = DATA_DIR, text_only: bool = False) -> None:
    """#1203: the graphical screen first; the 80x25 text screen only when the
    graphical one cannot run (no DRM/fbdev, no binary, no fonts) or stops."""
    import curses
    import sys

    from . import gui
    why = "text screen requested (--text)" if text_only else gui.run(data_dir)
    print(f"imagectl-dcui: text screen: {why}", file=sys.stderr, flush=True)
    curses.wrapper(curses_main, data_dir, why)
