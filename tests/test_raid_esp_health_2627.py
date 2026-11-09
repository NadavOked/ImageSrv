"""#2627: a RAID server whose ESP did not mount says so on the health page.

After #2622 the ``/boot/efi`` line of a RAID server is ``nofail``: a server whose first member is gone (or
whose vfat is damaged) comes up instead of stopping in emergency mode. The price was that nothing in the health
page said the ESP was not mounted -- the array row reads ``/proc/mdstat`` only, and with every drive present it
is green. The evidence for a mount is ``/proc/mounts``.

* RAID declared, ``/boot/efi`` absent from the mounts -> orange row, with what to look at;
* mounted -> a green row (a positive answer, not silence);
* mounts not readable -> ``unknown``: neither mounted nor not mounted (principle 5);
* a single-disk server -> no row: there the mount is mandatory (no ``nofail``) and a failure stops the boot loudly.
"""
from __future__ import annotations

import pytest

from server.system import raid_status
from test_server_health import health_server  # noqa: F401 -- the fixture

HEALTHY = """Personalities : [raid1]
md0 : active raid1 sdb2[1] sda2[0]
      976630464 blocks super 1.2 [2/2] [UU]

unused devices: <none>
"""
DECLARED = "level=raid1\ndisks=/dev/sda,/dev/sdb\nspares=0\narray=/dev/md0\n"
SINGLE = "level=none\n"

MOUNTED = """/dev/md0 / ext4 rw,relatime 0 0
/dev/sda1 /boot/efi vfat rw,relatime,fmask=0077,dmask=0077 0 0
tmpfs /run tmpfs rw,nosuid,nodev 0 0
"""
NOT_MOUNTED = """/dev/md0 / ext4 rw,relatime 0 0
tmpfs /run tmpfs rw,nosuid,nodev 0 0
"""
# Lines that mention the path but are not the mount point of /boot/efi.
LOOKALIKES = """/dev/md0 / ext4 rw,relatime 0 0
/dev/sda1 /boot/efi2 vfat rw 0 0
/dev/sda1 /mnt/boot/efi vfat rw 0 0
/dev/sdb1 /boot/efi/old vfat rw 0 0
/boot/efi /srv/elsewhere none bind 0 0
"""


def reader(value):
    def read():
        if isinstance(value, Exception):
            raise value
        return value
    return read


def rows(mounts, declared=DECLARED, mdstat=HEALTHY):
    hook = None if mounts is ... else reader(mounts)
    return raid_status.health_checks(reader(mdstat), reader(declared), hook)


def esp(found):
    matching = [r for r in found if r["id"] == "raid_esp"]
    assert len(matching) <= 1, found
    return matching[0] if matching else None


def test_an_esp_that_is_not_mounted_on_a_raid_server_is_orange_and_says_what_to_check():
    found = rows(NOT_MOUNTED)
    assert [r["id"] for r in found] == ["raid_md0", "raid_esp"], found
    assert found[0]["state"] == "ok", "the array itself is fine -- that is exactly the case being missed"
    row = esp(found)
    assert row["state"] == "warn", row
    assert "/boot/efi" in row["label"] and "לא מעוגנת" in row["detail"] and "boot-efi.mount" in row["detail"], row


def test_an_esp_that_is_mounted_is_a_green_row():
    row = esp(rows(MOUNTED))
    assert row is not None and row["state"] == "ok" and "מעוגנת" in row["detail"], row


@pytest.mark.parametrize("mounts", [LOOKALIKES, "", "\n\n"])
def test_only_the_mount_point_itself_counts_as_mounted(mounts):
    assert esp(rows(mounts))["state"] == "warn"


@pytest.mark.parametrize("value", [PermissionError(13, "Permission denied"), None])
def test_mounts_that_cannot_be_read_are_unknown_neither_mounted_nor_missing(value):
    row = esp(rows(value))
    assert row["state"] == "unknown", row
    assert "Permission denied" not in row["detail"], "the strerror goes to the server log, not the page (#2510)"


@pytest.mark.parametrize("declared", [SINGLE, "", None])
def test_a_single_disk_server_gets_no_esp_row(declared):
    """The mount is mandatory there (no nofail); a failure stops the boot, so the page has nothing to add."""
    assert esp(rows(NOT_MOUNTED, declared=declared, mdstat="Personalities : \nunused devices: <none>\n")) is None
    assert esp(rows(NOT_MOUNTED, declared=declared)) is None       # an array made by hand, never declared


def test_hook_not_injected_adds_no_row():
    found = rows(...)
    assert esp(found) is None and [r["id"] for r in found] == ["raid_md0"]


def test_the_health_page_carries_the_esp_row_from_the_hooks(health_server):
    """health.collect, the page's own list and its default wiring -- not only the module."""
    from server.system import health
    from test_server_health import SS_DNSMASQ
    base = {**health.default_hooks(),
            "ss": lambda: SS_DNSMASQ, "unit_active": lambda n: "active",
            "http_get": lambda u: 200, "http_size": lambda u: (200, 31_000_000),
            "interfaces": lambda: [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
            "tftp_root": lambda: health_server["tftp"], "udp_sender_pids": lambda: []}
    ctx = health_server["admin"].app.state.ctx

    def collect(**hooks):
        return {r["id"]: r for r in health.collect(ctx, {**base, **hooks}, "http://x")}

    got = collect(mdstat=lambda: HEALTHY, raid_declared=lambda: DECLARED, mounts=lambda: NOT_MOUNTED)
    assert got["raid_md0"]["state"] == "ok" and got["raid_esp"]["state"] == "warn", got.get("raid_esp")
    got = collect(mdstat=lambda: HEALTHY, raid_declared=lambda: DECLARED, mounts=lambda: MOUNTED)
    assert got["raid_esp"]["state"] == "ok"
    got = collect(mdstat=lambda: HEALTHY, raid_declared=lambda: SINGLE, mounts=lambda: NOT_MOUNTED)
    assert "raid_esp" not in got
