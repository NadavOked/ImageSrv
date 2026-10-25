"""agent/init loads only the NIC/GPU drivers the hardware asks for, and starts
the link before the rest of the modules (#1702).

Measured 17/10 on cloner 2 (59 boots): 9.2 s of `modprobe` over ~300 NIC
drivers, then 3.5 s waiting for a link that only started negotiating after
that. The real module block of agent/init runs here against a fake sysfs and a
`modprobe`/`ip` that record what they were asked, in order.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_initramfs_modules import KVER, modules_region_snippet

pytestmark = requires_native(("bash", BASH))

E1000E = "pci:v00008086d00001502sv00001028sd00000494bc02sc00i00"
I915 = "pci:v00008086d00000046sv0000103Csd0000304Bbc03sc00i00"
HEAD = ["ahci", "sd_mod", "ext4", "simpledrm", "evdev"]
PHY = ["realtek", "broadcom"]
NIC = ["e1000e", "r8169", "tg3", "hv_netvsc", "virtio_net"]


CRC = ["crc32c_generic", "crc32c-intel"]   # build_initramfs.sh: both in modules.required


def _fixture(tmp: Path, *, files: bool = True, gui: bool = False, crc: bool = False) -> Path:
    mod = tmp / "etc" / "imagectl"
    mod.mkdir(parents=True)
    write = lambda n, items: (mod / n).write_text(  # noqa: E731
        "\n".join(items) + "\n", encoding="utf-8", newline="\n")
    complete = HEAD + PHY + NIC + (["i915", "amdgpu", "nouveau"] if gui else []) + (CRC if crc else [])
    write("modules", complete)
    write("modules.required", ["ext4"] + (CRC if crc else []))
    if files:
        write("modules.phy", PHY)
        write("modules.nic", NIC)
        if gui:
            write("modules.gpu", ["i915", "amdgpu", "nouveau"])
    return mod


def _pci(sys: Path, slot: str, cls: str, alias: str) -> None:
    d = sys / "bus" / "pci" / "devices" / slot
    d.mkdir(parents=True)
    (d / "class").write_text(cls + "\n", newline="\n")
    (d / "modalias").write_text(alias + "\n", newline="\n")


def run(tmp: Path, *, pci=(), gui=False, files=True, dmi="", hyp=False,
        makes_nic=E1000E, makes_card=I915, fail=(), crc=False, crypto=None):
    mod = _fixture(tmp, files=files, gui=gui, crc=crc)
    proc = tmp / "proc"; proc.mkdir()
    if crypto is not None:
        (proc / "crypto").write_text(crypto, encoding="utf-8", newline="\n")
    sysd = tmp / "sys"
    (sysd / "class" / "net" / "lo").mkdir(parents=True)
    for slot, cls, alias in pci:
        _pci(sysd, slot, cls, alias)
    run_dir = tmp / "run"
    run_dir.mkdir()
    log = tmp / "log"
    lines = (AGENT / "init").read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "mkdir -p /run/imagectl")
    end = next(i for i, l in enumerate(lines) if l.startswith("# efivars"))
    block = "\n".join(lines[start + 1:end]).replace("/etc/imagectl", posix(mod))
    block = block.replace(
        'cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name 2>/dev/null',
        f"printf %s {dmi!r}").replace(
        "grep -q '[[:space:]]hypervisor\\([[:space:]]\\|$\\)' /proc/cpuinfo 2>/dev/null",
        "true" if hyp else "false")
    block = block.replace("/usr/lib/imagectl", posix(AGENT / "lib"))
    S = posix(sysd)
    stubs = f"""
LOG={posix(log)!r}; SYS_DIR={S!r}; PROC_DIR={posix(proc)!r}; RUN_DIR={posix(run_dir)!r}; FAIL="{' '.join(fail)}"
fail() {{ echo "FAIL: $1"; exit 3; }}
poll_tick() {{ _tk=1; }}
ip() {{ echo "ip $*" >> "$LOG"; }}
modprobe() {{
    echo "modprobe $1" >> "$LOG"
    for _f in $FAIL; do [ "$_f" = "$1" ] && return 1; done
    case "$1" in
        {makes_nic!r}|hv_netvsc) mkdir -p "$SYS_DIR/class/net/eth0"; : > "$SYS_DIR/class/net/eth0/device" ;;
        {makes_card!r}) mkdir -p "$SYS_DIR/class/drm/card0" ;;
        pci:*) return 1 ;;
    esac
    return 0
}}
"""
    done = subprocess.run(["sh", "-c", stubs + block + "\n:\n"], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls, done.stdout


NET_DEV = [("dev19", "0x020000", E1000E), ("dev02", "0x030000", I915)]


def test_only_the_nic_driver_the_hardware_asks_for_is_loaded(tmp_path):
    calls, _ = run(tmp_path, pci=NET_DEV)
    assert f"modprobe {E1000E}" in calls
    for unused in ("r8169", "tg3", "hv_netvsc", "virtio_net"):
        assert f"modprobe {unused}" not in calls, unused


def test_phy_drivers_still_load_before_the_nic_alias(tmp_path):
    calls, _ = run(tmp_path, pci=NET_DEV)
    assert calls.index("modprobe realtek") < calls.index(f"modprobe {E1000E}")
    assert calls.index("modprobe broadcom") < calls.index(f"modprobe {E1000E}")


def test_the_link_is_brought_up_before_the_storage_modules_load(tmp_path):
    calls, _ = run(tmp_path, pci=NET_DEV)
    assert "ip link set eth0 up" in calls
    up = calls.index("ip link set eth0 up")
    assert up < calls.index("modprobe ahci") < calls.index("modprobe ext4")
    assert "ip link set lo up" not in calls


def test_every_other_module_still_loads_exactly_once(tmp_path):
    calls, _ = run(tmp_path, pci=NET_DEV)
    for name in HEAD:
        assert calls.count(f"modprobe {name}") == 1, name


def test_a_machine_with_no_nic_gets_every_packaged_nic_driver(tmp_path):
    calls, out = run(tmp_path, pci=[("dev19", "0x020000", "pci:unknown")])
    for name in PHY + NIC:
        assert f"modprobe {name}" in calls, name
    assert "loading every packaged NIC driver" in out
    assert "no driver module for network device pci:unknown" in out


def test_the_paravirtual_nic_of_the_detected_platform_loads_by_name(tmp_path):
    calls, _ = run(tmp_path, dmi="Microsoft Corporation Virtual Machine")
    assert "modprobe hv_netvsc" in calls
    for unused in ("r8169", "tg3", "e1000e", "virtio_net"):
        assert f"modprobe {unused}" not in calls, unused


def test_a_required_module_failure_is_still_named(tmp_path):
    _, out = run(tmp_path, pci=NET_DEV, fail=["ext4"])
    assert "ERROR required modules did not load" in out and "ext4" in out


#: /proc/crypto on a Core2 / Phenom II (no SSE4.2): crc32c from crc32c_generic only
CRYPTO_GENERIC = ("name         : crc32c\ndriver       : crc32c-generic\nmodule       : crc32c_generic\npriority     : 100\n\n"
                  "name         : sha256\ndriver       : sha256-generic\nmodule       : kernel\n\n")


def test_crc32c_intel_without_sse42_is_no_error_when_crc32c_is_there(tmp_path):
    # #1937 (the college, 21/10): "ERROR required modules did not load, restore may corrupt:
    # crc32c-intel" on every machine without SSE4.2 -- crc32c_generic does the same work
    _, out = run(tmp_path, pci=NET_DEV, crc=True, fail=["crc32c-intel"], crypto=CRYPTO_GENERIC)
    assert "ERROR" not in out, out
    assert "platform modules absent, expected on this hardware: crc32c-intel" in out, out
    assert not (tmp_path / "run" / "modules.failed").exists()   # the diag's "required modules that did not load"


def test_crc32c_intel_is_an_error_when_no_crc32c_is_registered(tmp_path):
    _, out = run(tmp_path, pci=NET_DEV, crc=True, fail=["crc32c-intel"], crypto=CRYPTO_GENERIC.split("\n\n", 1)[1])
    assert "ERROR required modules did not load, restore may corrupt: crc32c-intel" in out, out


def test_crc32c_intel_is_an_error_when_proc_crypto_cannot_be_read(tmp_path):
    _, out = run(tmp_path, pci=NET_DEV, crc=True, fail=["crc32c-intel"], crypto=None)   # principle 5
    assert "ERROR required modules did not load, restore may corrupt: crc32c-intel" in out, out


def test_crc32c_generic_failing_stays_an_error(tmp_path):
    _, out = run(tmp_path, pci=NET_DEV, crc=True, fail=["crc32c_generic", "crc32c-intel"], crypto=CRYPTO_GENERIC)
    assert "ERROR required modules did not load" in out and "crc32c_generic" in out, out


def test_a_failed_platform_nic_is_reported_as_expected_absence(tmp_path):
    calls, out = run(tmp_path, dmi="Microsoft Corporation", pci=NET_DEV, fail=["hv_netvsc"])
    assert "platform modules absent, expected on this hardware: hv_netvsc" in out
    assert f"modprobe {E1000E}" in calls


def test_gpu_drivers_are_not_loaded_for_hardware_that_is_not_there(tmp_path):
    calls, _ = run(tmp_path, pci=NET_DEV, gui=True)
    assert f"modprobe {I915}" in calls
    for unused in ("i915", "amdgpu", "nouveau"):
        assert f"modprobe {unused}" not in calls, unused
    assert calls.index(f"modprobe {I915}") < calls.index("modprobe simpledrm")


def test_a_display_device_without_a_driver_gets_every_gpu_driver(tmp_path):
    calls, out = run(tmp_path, pci=NET_DEV, gui=True, makes_card="none")
    for name in ("i915", "amdgpu", "nouveau"):
        assert f"modprobe {name}" in calls, name
    assert "loading every GPU driver" in out


def test_no_display_device_means_no_gpu_driver_at_all(tmp_path):
    calls, _ = run(tmp_path, pci=NET_DEV[:1], gui=True)
    assert not [c for c in calls if c.split()[-1] in ("i915", "amdgpu", "nouveau")]


def test_an_initramfs_without_the_split_files_loads_the_whole_list_in_order(tmp_path):
    calls, _ = run(tmp_path, pci=NET_DEV, files=False)
    names = [c.split()[1] for c in calls if c.startswith("modprobe") and "cpufreq" not in c]
    assert names == HEAD + PHY + NIC
    assert not [c for c in calls if c.startswith("ip ")]


# --- the builder writes the split files -------------------------------------


def _build_lists(tmp: Path, gui: int):
    root = tmp / "root"
    base = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "net"
    for sub, names in (("phy", PHY), ("ethernet", ["e1000e", "r8169"]),
                       ("hyperv", ["hv_netvsc"])):
        (base / sub).mkdir(parents=True, exist_ok=True)
        for n in names:
            (base / sub / f"{n}.ko.xz").write_bytes(b"")
    (root / "etc" / "imagectl").mkdir(parents=True)
    script = (f"ROOT={posix(root)!r}\nKVER={KVER!r}\nWITH_GUI={gui}\n"
              "REQUIRED_FS_MODULES=(ext4)\n" + modules_region_snippet())
    subprocess.run([BASH, "-c", script], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=90)
    rd = lambda n: (root / "etc" / "imagectl" / n).read_text().split()  # noqa: E731
    return rd, root / "etc" / "imagectl"


def test_the_builder_splits_phy_nic_and_leaves_modules_complete(tmp_path):
    rd, _ = _build_lists(tmp_path, 0)
    assert rd("modules.phy") == sorted(PHY)
    assert rd("modules.nic") == ["e1000e", "hv_netvsc", "r8169"]
    full = rd("modules")
    assert set(rd("modules.phy")) | set(rd("modules.nic")) <= set(full)


def test_the_builder_writes_gpu_list_only_for_the_gui_image(tmp_path):
    _, d = _build_lists(tmp_path / "a", 0)
    assert not (d / "modules.gpu").exists()
    rd, _ = _build_lists(tmp_path / "b", 1)
    # #1914 (Nadav 20/10, old college hardware): the modern three first, then older/virtual adapters
    assert rd("modules.gpu") == ["i915", "amdgpu", "nouveau", "radeon", "mgag200", "ast",
                                 "bochs", "cirrus-qemu", "vmwgfx", "simpledrm"]
