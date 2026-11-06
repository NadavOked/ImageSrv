"""#2396: the "folder" backup destination (#1956) on a second disk must be writable by the server.

A second disk is mounted under ``/mnt`` (by hand) or ``/media`` (automatically). The server unit runs
with ``ProtectSystem=strict``, where everything outside ``ReadWritePaths`` is read-only, so the
console's write probe failed with "Read-only file system" and the option it offers could never
succeed. The console has to say so *at setup* (principle 5) -- and it still does for a path outside
the two roots; what changes is that the documented destination now passes.

The unit cannot be run under a systemd mount namespace here, so the test ties the three facts that
must agree: the destination the console documents (the example in ``check_dest``'s refusal), the
unit's ``ReadWritePaths``, and the hardening that must stay (no ``/``, still ``strict``).
"""

from __future__ import annotations

import re
from pathlib import Path

from server.system import backup_job

REPO = Path(__file__).resolve().parent.parent
SERVER_UNIT = REPO / "deploy" / "imagesrv-server.service"
BACKUP_UNIT = REPO / "deploy" / "imagesrv-backup.service"


def _unit_values(path: Path) -> dict[str, str]:
    """Key=value lines of a unit; a unit that is not there reads as empty, so the assertions say what is missing."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {k: v for k, _, v in (l.partition("=") for l in text.splitlines()
                                 if l and not l.startswith("#") and "=" in l)}


def writable_roots(unit: str) -> list[str]:
    """The ReadWritePaths entries with the optional '-' prefix stripped."""
    return [p.lstrip("-") for p in unit.split()]


def _covers(entry: str, path: str) -> bool:
    return path == entry or path.startswith(entry.rstrip("/") + "/")


def writable(unit: str, path: str) -> bool:
    return any(_covers(entry, path) for entry in writable_roots(unit))


def _documented_destination() -> str:
    """The example path the console shows a user who typed a bad folder, e.g. ``/mnt/backup``."""
    path, why = backup_job.check_dest("backup")           # relative: refused, with the example
    assert path is None and why
    found = re.search(r"(/(?:mnt|media)/\w+)", why)
    assert found, f"check_dest no longer documents a destination path: {why!r}"
    return found.group(1)


def test_the_documented_second_disk_destination_is_writable_for_the_server():
    rw = _unit_values(SERVER_UNIT).get("ReadWritePaths", "")
    example = _documented_destination()
    assert writable(rw, example), f"{example} (documented in the console) is read-only under ProtectSystem=strict"
    assert writable(rw, "/media/user/DISK"), "an automounted disk (/media) is read-only for the server"
    assert writable(rw, "/mnt/backup-lab/db"), "a hand-mounted disk (/mnt) is read-only for the server"


def test_the_fix_opens_only_the_mount_roots_not_the_filesystem():
    values = _unit_values(SERVER_UNIT)
    roots = writable_roots(values.get("ReadWritePaths", ""))
    assert values.get("ProtectSystem") == "strict"
    assert "/" not in roots, "ReadWritePaths=/ would cancel ProtectSystem=strict"
    assert "/home" not in roots and not any(_covers(r, "/home/x") for r in roots)
    # The mount roots are optional (-): a server without /media must still start.
    line = next(l for l in SERVER_UNIT.read_text(encoding="utf-8").splitlines() if l.startswith("ReadWritePaths="))
    assert "-/mnt" in line.split() and "-/media" in line.split()


def test_a_path_outside_the_roots_is_still_refused_at_setup_not_at_backup_time():
    rw = _unit_values(SERVER_UNIT).get("ReadWritePaths", "")
    assert not writable(rw, "/opt/backup"), "an arbitrary path must stay read-only, so the probe still refuses it"
    assert not writable(rw, "/mntx/backup"), "prefix match must be by path component"


def test_the_checker_itself_says_no_when_the_roots_are_missing():
    # The helper must not say "writable" for everything -- otherwise the tests above prove nothing.
    old = "/var/lib/imagesrv /srv/imagesrv/images /etc/dnsmasq.d /etc -/srv/tftp"
    assert not writable(old, "/mnt/backup")
    assert not writable(old, "/media/user/DISK")
    assert writable(old + " -/mnt", "/mnt/backup")


def test_the_hourly_job_unit_is_not_stricter_than_the_server_probe():
    # The job writes where the console approved; the two must not disagree the other way around.
    assert _unit_values(BACKUP_UNIT).get("ProtectSystem") == "full"


def test_an_existing_server_gets_the_unit_through_the_upgrade():
    # The server unit lives in deploy/*.service, which server-upgrade.sh installs, daemon-reloads and restarts on.
    upgrade = (REPO / "tools" / "server-upgrade.sh").read_text(encoding="utf-8")
    assert 'install -m 0644 deploy/*.service "$UNIT_DIR/"' in upgrade
    assert "systemctl daemon-reload" in upgrade
    assert "systemctl restart imagesrv-server" in upgrade
