"""#3627 part 2 -- the initramfs packs LVM for boot without udev.

`tests/lvm-initramfs-3627.sh` needs a booted initramfs with a real LVM and does not run in the
queue. These read the builder itself: the binary and the package are listed, the generated
`lvm.conf` turns every udev dependency off, and the loader check runs after the pack marker
(the overlay test runs only the code above it, and has no root for `chroot`).

Negative control: tools/build_initramfs.sh back to the base -- see the PR.
"""

from __future__ import annotations

import re
from pathlib import Path

BUILDER = (Path(__file__).resolve().parent.parent / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")


def _list_after(marker: str) -> list[str]:
    start = BUILDER.index(marker)
    return re.split(r"\s+", BUILDER[start:BUILDER.index(")", start)].split("(", 1)[1].strip().replace("\\n", " "))


def test_lvm_is_a_packed_binary_and_lvm2_is_installed():
    assert "lvm" in _list_after("BINARIES=(")
    assert any(" lvm2 " in BUILDER[m.start():m.start() + 1500] for m in re.finditer("apt-get install", BUILDER))


def test_the_generated_lvm_conf_does_not_depend_on_udev():
    m = re.search(r"cat > \"\$ROOT/etc/lvm/lvm\.conf\" <<'EOF'\n(.*?)\nEOF\n", BUILDER, re.S)
    assert m, "no lvm.conf is generated"
    conf = dict(re.findall(r"^\s*(\w+)\s*=\s*(.+?)\s*$", m.group(1), re.M))
    assert conf == {
        "obtain_device_list_from_udev": "0",
        "external_device_info_source": '"none"',
        "use_devicesfile": "0",
        "udev_sync": "0",
        "udev_rules": "0",
        "monitoring": "0",
    }


def test_the_loader_check_of_the_packed_lvm_runs_after_the_pack_marker():
    check = BUILDER.find('chroot "$ROOT" /lib64/ld-linux-x86-64.so.2 --list /usr/bin/lvm')
    assert check != -1, "no loader check of the packed lvm"
    assert check > BUILDER.index("# --- pack ---")
    assert 'exit "$rc"' in BUILDER[check:check + 300]
