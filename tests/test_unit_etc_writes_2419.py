"""#2419 (4): every place the server writes under ``/etc`` has its own ``ReadWritePaths`` entry.

``imagesrv-server.service`` runs under ``ProtectSystem=strict``: whatever is not in
``ReadWritePaths`` is read-only, and a write there fails with "Read-only file system" -- on an
installed server only, never in a test (#1241, #1552). The bare ``/etc`` entry hid every such
gap, and it also opens all of ``/etc`` to the server.

The inventory here is the contract. Every ``/etc`` path constant in the code that runs in the
server process is classified -- read only, or the path that must be writable for the write to
succeed -- and the classification is compared both ways with the unit:

* a new ``/etc`` constant that is not classified fails (someone added a write, or a read, and
  must say which);
* a write target with no specific entry fails -- not even when bare ``/etc`` would cover it;
* an ``/etc/...`` entry in the unit that no write needs fails (the list is exact);
* bare ``/etc`` is allowed only while a write needs ``/etc`` itself: the root-level files that
  are written atomically -- a temporary file beside them and ``os.replace`` (#1123, #1908) --
  and that therefore need the directory, not the file. Bind-mounting the file alone makes
  ``os.replace`` onto it fail (EBUSY). Removing bare ``/etc`` waits for a decision on those
  three (the Issue: a privileged helper, or writing them in place).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UNIT = REPO / "deploy" / "imagesrv-server.service"

#: Code that does not run in the server process -- each has its own unit and its own sandbox.
OTHER_UNITS = ("server/dcui/", "server/wizard/")

#: Every ``/etc`` path constant in server code: ``None`` = read only (with who writes it), otherwise
#: the path that must be writable. An atomic write needs its directory; an in-place write
#: (``write_text``) needs only the file.
ETC_CONSTANTS: dict[str, str | None] = {
    # root-level, atomic: these need /etc itself
    "/etc/nftables.conf": "/etc",                       # deploy_net: .candidate beside it + os.replace
    "/etc/resolv.conf": "/etc",                         # netcfg_host.write_resolv: write_atomic
    "/etc/fstab": "/etc",                               # locations: write_atomic (#1123)
    # DHCP (dhcp_host)
    "/etc/dnsmasq.d/imagesrv-dhcp.conf": "/etc/dnsmasq.d",
    "/etc/dnsmasq.d/imagesrv.conf": "/etc/dnsmasq.d",   # the TFTP switch rewrites it (#1013)
    "/etc/imagesrv/dnsmasq-proxy.conf": "/etc/imagesrv",
    "/etc/imagesrv/known-macs": "/etc/imagesrv",
    "/etc/imagesrv/creds": "/etc/imagesrv",             # locations: SMB credentials, 0600 (#1123)
    "/etc/imagesrv/grub-password": "/etc/imagesrv",     # created when missing (#2437)
    # the server's own network (netcfg_host)
    "/etc/network/interfaces.d": "/etc/network/interfaces.d",
    "/etc/modules-load.d/imagesrv-bond.conf": "/etc/modules-load.d",
    # dnsmasq_dropin: unit_dir() / "dnsmasq.service.d" / "imagesrv-restart.conf" (#2174)
    "/etc/systemd/system": "/etc/systemd/system/dnsmasq.service.d",
    # timesync: write_text -- the drop-ins (mkdir conf.d), and chrony.conf in place
    "/etc/chrony/conf.d": "/etc/chrony/conf.d",
    "/etc/chrony/chrony.conf": "/etc/chrony/chrony.conf",
    # ups: nut.conf, ups.conf, upsd.conf in place (#1973)
    "/etc/nut": "/etc/nut",
    # read only
    "/etc/network/interfaces": None,                    # written by setup-boot-server.sh (adopt_interface)
    "/etc/network/if-pre-up.d/ifenslave": None,         # bond_supported: exists?
    "/etc/netplan": None,                               # foreign_manager
    "/etc/systemd/network": None,                       # foreign_manager
    "/etc": None,                                       # storage/locations: refused prefix for a local location (#2444)
    "/etc/imagesrv/firstboot.status": None,             # written by the installer / wizard
    "/etc/imagesrv/raid": None,                         # written by the installer (#1989)
}

#: Writes no constant shows -- a program the server runs, in the server's namespace -- and the
#: path each one needs writable.
SUBPROCESS_WRITES: dict[str, str] = {
    # iscsiadm discovery/node records (#1552, test_storage_locations.ISCSIADM_WRITES)
    "/etc/iscsi": "/etc/iscsi",
    # the dhcpcd/dhclient hooks under ifup rewrite resolv.conf on a DHCP interface: a helper
    # that takes over write_resolv alone would not take /etc out of the unit
    "/etc/resolv.conf": "/etc",
}

_ETC_PATH = re.compile(r"^/etc(?:/[\w.@+-]+)*/?$")


def etc_constants(root: Path) -> dict[str, list[str]]:
    """``/etc`` path constants in ``root/server`` (outside ``OTHER_UNITS``) -> where they are."""
    found: dict[str, list[str]] = {}
    for path in sorted((root / "server").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(OTHER_UNITS):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and _ETC_PATH.match(node.value):
                found.setdefault(node.value.rstrip("/") or "/", []).append(f"{rel}:{node.lineno}")
    return found


def unit_rw(text: str) -> list[str]:
    line = next(l for l in text.splitlines() if l.startswith("ReadWritePaths="))
    return [p.lstrip("-") for p in line.split("=", 1)[1].split()]


def _covers(entry: str, path: str) -> bool:
    return path == entry or path.startswith(entry.rstrip("/") + "/")


def needed() -> set[str]:
    return {p for p in ETC_CONSTANTS.values() if p} | set(SUBPROCESS_WRITES.values())


def test_every_etc_constant_in_the_server_is_classified():
    """A new ``/etc`` path in server code fails here until it is classified -- read only, or
    written and given its entry in the unit. Without this, the write passes every test and
    fails on the first installed server."""
    found = etc_constants(REPO)
    unknown = {p: where for p, where in found.items() if p not in ETC_CONSTANTS}
    stale = sorted(set(ETC_CONSTANTS) - set(found))
    assert not unknown, f"unclassified /etc paths (add to ETC_CONSTANTS and the unit): {unknown}"
    assert not stale, f"classified but no longer in the code: {stale}"


def test_every_etc_write_has_its_own_entry_not_only_bare_etc():
    rw = unit_rw(UNIT.read_text(encoding="utf-8"))
    specific = [e for e in rw if e != "/etc"]
    missing = sorted(p for p in needed() - {"/etc"}
                     if not any(_covers(e, p) for e in specific))
    assert not missing, f"written by the server, covered only by bare /etc: {missing}"


def test_the_unit_opens_no_etc_path_the_server_does_not_write():
    rw = unit_rw(UNIT.read_text(encoding="utf-8"))
    extra = sorted(e for e in rw if e.startswith("/etc/") and e not in needed())
    assert not extra, f"/etc entries no write needs: {extra}"


def test_bare_etc_stays_only_for_the_root_level_atomic_files():
    """Bare ``/etc`` is there for these and only these. When none is left -- moved, or written
    through a helper -- this fails and says to take ``/etc`` out of the unit."""
    rw = unit_rw(UNIT.read_text(encoding="utf-8"))
    root_level = sorted({c for c, p in (*ETC_CONSTANTS.items(), *SUBPROCESS_WRITES.items())
                         if p == "/etc"})
    assert root_level == ["/etc/fstab", "/etc/nftables.conf", "/etc/resolv.conf"]
    assert ("/etc" in rw) == bool(root_level), "/etc in ReadWritePaths must match the need for it"


def test_the_scanner_sees_a_new_etc_write(tmp_path):
    """The guard of the guard: a module that starts writing a new ``/etc`` file is reported,
    in a plain constant, a Path() and an f-string prefix alike; dcui/wizard are not."""
    (tmp_path / "server" / "system").mkdir(parents=True)
    (tmp_path / "server" / "dcui").mkdir()
    (tmp_path / "server" / "system" / "new.py").write_text(
        'from pathlib import Path\nA = "/etc/foo.conf"\nB = Path("/etc/bar")\n'
        'C = f"/etc/{1}"\nD = "not /etc/x"\n', encoding="utf-8")
    (tmp_path / "server" / "dcui" / "own.py").write_text('X = "/etc/dcui-only"\n', encoding="utf-8")
    assert set(etc_constants(tmp_path)) == {"/etc/foo.conf", "/etc/bar", "/etc"}
