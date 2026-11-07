"""#2622: the fstab line of /boot/efi on a RAID server carries `nofail`.

QEMU, 07/11 (lab/release-tests/vmclose-1106/1989-raid1): RAID1 without disk 1
stopped in emergency mode -- "Timed out waiting for device
/dev/disk/by-uuid/<esp>", "Dependency failed for /boot/efi", "Dependency failed
for Local File Systems" -- because /boot/efi is the first member's ESP and its
fstab line had no `nofail`. Every member carries its own loader
(raid.sh raid_extra_esps), so the array must come up without that one ESP.

The real write_host_configuration runs (not dry-run, which prints no content)
and the fstab it writes is read back. The root line is not touched, and a
single-disk install keeps the line as it was: its ESP is not optional.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_installer_engine import INSTALLER, LINUX_ONLY, SH

pytestmark = pytest.mark.skipif(SH is None, reason="a POSIX sh is required")

ROOT_UUID = "11111111-2222-3333-4444-555555555555"
ESP_UUID = "D2C8-E09D"


def _fstab(tmp_path: Path, raid_level: str | None) -> list[str]:
    target = tmp_path / "target"
    target.mkdir()
    level = "" if raid_level is None else f"RAID_LEVEL='{raid_level}'\n"
    harness = tmp_path / "harness.sh"
    harness.write_text(f"""DRY_RUN=0 TARGET='{target.as_posix()}'
{level}ROOT_NODE=/dev/md0 ESP_NODE=/dev/vda1
ANSWER_HOSTNAME=imagesrv-ta ANSWER_TIMEZONE=Asia/Jerusalem ANSWER_DISK=/dev/vda
fail() {{ echo "fail $*" >&2; exit 2; }}
must_run() {{ shift 2; "$@"; }}
chroot() {{ return 0; }}
write_target() {{ mkdir -p "$TARGET${{2%/*}}" && printf '%s' "$3" > "$TARGET$2"; }}
. '{(INSTALLER / "lib" / "finish.sh").as_posix()}'
filesystem_uuid() {{ case "$1" in /dev/md0) printf '%s' '{ROOT_UUID}' ;; *) printf '%s' '{ESP_UUID}' ;; esac; }}
write_chrony_source() {{ :; }}
write_target_secret() {{ :; }}
write_host_configuration
""", newline="\n")
    got = subprocess.run([SH, str(harness)], text=True, capture_output=True, stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    return (target / "etc" / "fstab").read_text(encoding="utf-8").splitlines()


@LINUX_ONLY
@pytest.mark.parametrize("level", ["raid1", "raid5", "raid6", "raid10"])
def test_a_raid_server_boots_without_the_first_members_esp(tmp_path, level):
    root, esp = _fstab(tmp_path, level)
    assert root == f"UUID={ROOT_UUID} / ext4 defaults 0 1"     # the root line is the same as ever
    fields = esp.split()
    assert fields[:3] == [f"UUID={ESP_UUID}", "/boot/efi", "vfat"], esp
    assert "nofail" in fields[3].split(","), esp
    assert "umask=0077" in fields[3].split(","), esp


@LINUX_ONLY
@pytest.mark.parametrize("level", [None, "none"])
def test_a_single_disk_server_keeps_its_esp_mandatory(tmp_path, level):
    root, esp = _fstab(tmp_path, level)
    assert root == f"UUID={ROOT_UUID} / ext4 defaults 0 1"
    assert esp == f"UUID={ESP_UUID} /boot/efi vfat umask=0077 0 1"
