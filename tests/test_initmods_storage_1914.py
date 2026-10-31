"""#1914 (an old HP in the college, 20/10: "לא נמצאו דיסקים במחשב"): the storage
controllers that are present are loaded by modalias, before the fixed list.

The fixed list named only ahci/nvme of the ATA drivers; ata_piix (SATA in
IDE/Legacy mode, ICH7-ICH10), ata_generic and pata_* were packed and never
loaded. Here a machine whose only disk controller is an ICH10 in IDE mode gets
its modalias asked of modprobe -- which, through modules.alias, is ata_piix --
and that before the fixed list.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, posix

REPO = Path(__file__).resolve().parent.parent
INITMODS = REPO / "agent" / "lib" / "core" / "initmods.sh"

pytestmark = requires_native(("bash", BASH))

#: ICH10 SATA in IDE mode (8086:3a20, class 01018a) -- ata_piix's modules.alias line matches it
ICH10_IDE = "pci:v00008086d00003A20sv0000103Csd00003048bc01sc01i8a"
#: modules.alias in miniature: what busybox modprobe resolves a modalias to
ALIASES = {ICH10_IDE: "ata_piix", "pci:v00008086d0000A0D3": "vmd"}


def boot(tmp_path: Path, devices: dict[str, tuple[str, str]], fixed: list[str]) -> list[str]:
    """mods_load_all on a fake /sys; returns what modprobe was asked, in order, resolved.
    (Device dirs are named 0000_00_1f.2: Windows has no colon in a path, and the loop globs.)"""
    sysd = tmp_path / "sys"
    for bdf, (cls, alias) in devices.items():
        d = sysd / "bus" / "pci" / "devices" / bdf
        d.mkdir(parents=True)
        (d / "class").write_text(cls + "\n", newline="\n")
        (d / "modalias").write_text(alias + "\n", newline="\n")
    (sysd / "class" / "net").mkdir(parents=True)
    mod = tmp_path / "mod"; mod.mkdir()
    (mod / "modules").write_text("\n".join(fixed) + "\n", newline="\n")
    log = tmp_path / "modprobe.log"
    cases = "".join(f'{a}) echo {m} >> {posix(log)!r}; return 0 ;; ' for a, m in ALIASES.items())
    script = (f'SYS_DIR={posix(sysd)!r}; MOD_DIR={posix(mod)!r}; _required=" "; '
              'poll_tick() { _tk=10; }; platform_module_expected() { return 1; }; '
              f'modprobe() {{ case "$1" in {cases}pci:*) return 1 ;; *) echo "$1" >> {posix(log)!r} ;; esac; }}; '
              f'. {posix(INITMODS)!r}; mods_load_all')
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    return log.read_text().split() if log.exists() else []


def test_an_ich10_in_ide_mode_gets_ata_piix_before_the_fixed_list(tmp_path):
    asked = boot(tmp_path, {"0000_00_1f.2": ("0x01018a", ICH10_IDE)}, ["ahci", "nvme", "sd_mod"])
    assert "ata_piix" in asked, asked
    assert asked.index("ata_piix") < asked.index("ahci"), asked


def test_a_display_or_network_device_is_not_taken_for_storage(tmp_path):
    asked = boot(tmp_path, {"0000_00_02.0": ("0x030000", "pci:v00008086d00000046sv0bc03sc00i00")}, ["ahci"])
    assert asked == ["ahci"], asked


def test_the_fixed_list_names_the_old_and_vmd_drivers_as_the_fallback():
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    line = next(ln for ln in build.splitlines() if "ahci" in ln and "nvme" in ln and "printf" in ln)
    names = line.split("'%s\\n'", 1)[1].split()
    assert names.index("vmd") < names.index("ahci") < names.index("nvme"), names
    assert "ata_piix" in names and "ata_generic" in names, names
    assert "kernel/drivers/pci/controller" in build   # vmd.ko's tree is packed
