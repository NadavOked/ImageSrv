"""‏#1331 T8 — האם ה-initramfs של לינוקס שנכתב ימצא את הדיסק שלו
(`agent/lib/linuxdrivers.sh`, ‏interfaces.md §17 צעד 1ל).

אותה קופסה של `test_postdeploy.py` (‏sh אמיתי, כלים מזויפים) עם תוכנית
לינוקס ועץ-שורש מזויף ב-`$RUN_DIR/linux` (‏`mount` מזויף). ה-initrd נבנה
כאן כ-cpio ‏newc אמיתי — ארכיון microcode לא-דחוס לפניו, דחיסה אמיתית —
ונקרא בכלים האמיתיים (‏od/tail/head/cut, ‏zcat/xzcat/zstd, ‏cpio). מה שנבדק:

* מודול ה-bus של **הדיסק שנכתב** (‏nvme* → ‏nvme.ko, ‏sd* → ‏ahci.ko) ברשימת
  הקבצים של ה-initrd או ב-`modules.builtin` → ‏`generic`; לא שם → ‏`hostonly`;
* "לא הצלחנו לבדוק" (‏initrd פגום, פורמט לא מוכר, עיגון שנכשל, אין initrd) הוא
  `failed`/`unsupported` בשמו — **לעולם לא** `generic` (עיקרון 5);
* הבדיקה קוראת בלבד: שום קובץ בשורש אינו משתנה, וה-staging נשאר
  `skipped`/`not_windows` והשחזור `done`.
"""

from __future__ import annotations

import gzip
import hashlib
import lzma
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_postdeploy import LINUX_PLAN, Box

from server import reports

pytestmark = requires_native(("bash", BASH))

KVER = "6.12.48+deb13-amd64"
AHCI = f"usr/lib/modules/{KVER}/kernel/drivers/ata/ahci.ko"
NVME = f"usr/lib/modules/{KVER}/kernel/drivers/nvme/host/nvme.ko"
OS_RELEASE = 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\n'


def newc(names: list[str], pad: int = 512) -> bytes:
    """ארכיון cpio ‏newc אמיתי: קובץ קטן לכל שם, ו-TRAILER!!! בסוף."""
    out = bytearray()

    def entry(ino: int, name: str, data: bytes, mode: int) -> None:
        nb = name.encode() + b"\0"
        fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0]
        out.extend(b"070701" + "".join(f"{f:08X}" for f in fields).encode() + nb)
        out.extend(b"\0" * (-len(out) % 4))
        out.extend(data)
        out.extend(b"\0" * (-len(out) % 4))

    for i, name in enumerate(names, 1):
        entry(i, name, f"content of {name}\n".encode(), 0o100644)
    entry(0, "TRAILER!!!", b"", 0)
    out.extend(b"\0" * (-len(out) % pad))
    return bytes(out)


MICROCODE = newc(["kernel", "kernel/x86", "kernel/x86/microcode",
                  "kernel/x86/microcode/GenuineIntel.bin"])
MAIN = ["init", "usr/bin/udevadm", f"usr/lib/modules/{KVER}/modules.dep"]


def zstd(data: bytes) -> bytes:
    return subprocess.run(["zstd", "-q", "-c"], input=data, capture_output=True,
                          check=True).stdout


def initrd(modules: list[str], compress=gzip.compress, early: bytes = MICROCODE) -> bytes:
    return early + compress(newc(MAIN + modules))


def linux_box(tmp_path: Path, *, image: bytes | None = None, builtin: str = "",
              kernels: tuple[str, ...] = (KVER,), dracut: bool = False,
              fstab: str = "", stubs: dict[str, str] | None = None) -> Box:
    box = Box(tmp_path, plan=LINUX_PLAN, system=None,
              stubs={"mount": "exit 0", **(stubs or {})})
    root = box.run / "linux"
    (root / "etc").mkdir(parents=True)
    (root / "usr/lib").mkdir(parents=True)
    (root / "usr/lib/os-release").write_text(OS_RELEASE, newline="\n")
    (root / "etc/fstab").write_text(fstab, newline="\n")
    (root / "boot").mkdir()
    for kv in kernels:
        (root / f"boot/vmlinuz-{kv}").write_bytes(b"MZ kernel")
        mods = root / "usr/lib/modules" / kv
        mods.mkdir(parents=True)
        (mods / "modules.builtin").write_text(builtin, newline="\n")
        if image is not None:
            name = f"boot/initramfs-{kv}.img" if dracut else f"boot/initrd.img-{kv}"
            (root / name).write_bytes(image)
    box.root = root
    return box


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def stage_linux(box: Box, disk: str = "sda") -> dict:
    before = tree(box.root)
    _, result = box.stage(disk)
    assert result["state"] == "skipped" and result["reason"] == "not_windows"
    state = box.run / "state"   # #1935: never terminal here -- done is the caller's, after the mark
    assert not state.exists() or state.read_text().strip() not in ("done", "partial", "failed")
    assert tree(box.root) == before, "הבדיקה קוראת בלבד"
    return result["linux"]


def test_a_debian_initrd_with_ahci_on_a_sata_disk_is_generic(tmp_path):
    box = linux_box(tmp_path, image=initrd([AHCI, NVME]))
    assert stage_linux(box, "sda") == {
        "state": "generic", "module": "ahci", "kver": KVER,
        "initrd": f"/boot/initrd.img-{KVER}", "os": "Debian GNU/Linux 13 (trixie)"}


def test_a_host_only_initrd_without_nvme_on_an_nvme_disk_is_hostonly(tmp_path):
    """‏MODULES=dep שנבנה על מכונת SATA: ‏ahci בפנים, ‏nvme לא."""
    box = linux_box(tmp_path, image=initrd([AHCI]))
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "hostonly" and linux["module"] == "nvme"
    assert linux["kver"] == KVER and "nvme" in linux["error"]


def test_the_same_initrd_is_generic_for_the_bus_it_has(tmp_path):
    box = linux_box(tmp_path, image=initrd([AHCI]))
    assert stage_linux(box, "sdb")["state"] == "generic"


def test_a_module_built_into_the_kernel_needs_no_initrd_entry(tmp_path):
    box = linux_box(tmp_path, image=initrd([AHCI]),
                    builtin="kernel/drivers/nvme/host/nvme.ko\n")
    assert stage_linux(box, "nvme0n1")["state"] == "generic"


def test_a_dracut_xz_initrd_with_compressed_modules_and_no_early_cpio(tmp_path):
    box = linux_box(tmp_path, dracut=True, image=initrd(
        [f"lib/modules/{KVER}/kernel/drivers/nvme/host/nvme.ko.xz"], compress=lzma.compress, early=b""))
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "generic"
    assert linux["initrd"] == f"/boot/initramfs-{KVER}.img"


def test_two_early_archives_are_both_walked(tmp_path):
    """‏Debian שם microcode של AMD ושל Intel כארכיונים נפרדים, עם ריפוד."""
    amd = newc(["kernel/x86/microcode/AuthenticAMD.bin"], pad=4)
    box = linux_box(tmp_path, image=initrd([NVME], early=MICROCODE + amd + b"\0" * 1024))
    assert stage_linux(box, "nvme0n1")["state"] == "generic"


@requires_native("zstd")
def test_a_zstd_initrd(tmp_path):
    box = linux_box(tmp_path, image=initrd([NVME], compress=zstd))
    assert stage_linux(box, "nvme0n1")["state"] == "generic"
    box2 = linux_box(tmp_path / "b", image=initrd([AHCI], compress=zstd))
    assert stage_linux(box2, "nvme0n1")["state"] == "hostonly"


def test_a_truncated_initrd_is_failed_not_hostonly(tmp_path):
    """לא הצלחנו לקרוא את הרשימה ≠ "המודול לא שם" (עיקרון 5)."""
    full = initrd([AHCI, NVME])
    box = linux_box(tmp_path, image=full[: len(MICROCODE) + 200])
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "failed" and "zcat" in linux["error"]


def test_an_unknown_initrd_format_is_failed(tmp_path):
    box = linux_box(tmp_path, image=MICROCODE + b"\x89LZO garbage" * 10)
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "failed" and "לא מזוהה" in linux["error"]


def test_a_kernel_without_an_initrd_is_unsupported(tmp_path):
    box = linux_box(tmp_path, image=None)
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "unsupported" and KVER in linux["error"]


def test_a_separate_boot_partition_is_unsupported_by_name(tmp_path):
    box = linux_box(tmp_path, kernels=(), fstab="UUID=1234 /boot ext4 defaults 0 2\n")
    linux = stage_linux(box, "sda")
    assert linux["state"] == "unsupported" and "/boot" in linux["error"]


def test_an_older_host_only_kernel_is_reported(tmp_path):
    old = "6.1.0-30-amd64"
    box = linux_box(tmp_path, kernels=(old, KVER), image=initrd([AHCI, NVME]))
    (box.root / f"boot/initrd.img-{old}").write_bytes(initrd([AHCI]))
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "hostonly" and linux["kver"] == old


def test_an_unknown_bus_is_skipped_without_mounting(tmp_path):
    box = linux_box(tmp_path, image=initrd([AHCI]),
                    stubs={"mount": 'echo "$@" >> "$FAKE_SERVER/mounts"; exit 0'})
    linux = stage_linux(box, "mmcblk0")
    assert linux == {"state": "skipped", "reason": "unknown_bus"}
    assert not (box.server / "mounts").exists()


def test_a_mount_that_fails_is_failed(tmp_path):
    box = linux_box(tmp_path, image=initrd([NVME]), stubs={"mount": "exit 32"})
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "failed" and "לעגן" in linux["error"]


def test_an_umount_that_fails_is_failed(tmp_path):
    box = linux_box(tmp_path, image=initrd([NVME]), stubs={"umount": "exit 16"})
    linux = stage_linux(box, "nvme0n1")
    assert linux["state"] == "failed" and "לנתק" in linux["error"]


def test_a_windows_target_has_no_linux_result(tmp_path):
    box = Box(tmp_path)
    _, result = box.stage("sda")
    assert "linux" not in result


@pytest.mark.skipif(shutil.which("busybox") is None or os.name == "nt",
                    reason="busybox — הכלים של הסוכן")
def test_the_listing_runs_on_busybox_applets(tmp_path):
    """הסוכן רץ על busybox ash ועל ה-applets שלו (od של coreutils נארז)."""
    tools = tmp_path / "bb"; tools.mkdir()
    for applet in ("sh", "cpio", "zcat", "tail", "head", "cut", "wc", "tr", "awk"):
        (tools / applet).symlink_to(shutil.which("busybox"))
    (tools / "od").symlink_to(shutil.which("od"))
    rd = tmp_path / "initrd.img"
    rd.write_bytes(initrd([AHCI, NVME], early=MICROCODE + newc(["x"], pad=4)))
    out = subprocess.run(
        [str(tools / "sh"), "-c",
         f'PATH={tools}; LOG_FILE=/dev/null; . {posix(AGENT)}/lib/linuxdrivers.sh; '
         f'_initrd_list {rd} || echo "FAILED $_ld_why"'],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    names = out.stdout.splitlines()
    assert "FAILED" not in out.stdout, out.stdout + out.stderr
    assert "kernel/x86/microcode/GenuineIntel.bin" in names and "x" in names
    assert AHCI in names and NVME in names


# --- השרת ------------------------------------------------------------------

def test_the_server_keeps_drivers_linux():
    value = {"state": "skipped", "packages": [], "reason": "not_windows",
             "linux": {"state": "hostonly", "module": "nvme", "kver": KVER,
                       "initrd": "/boot/initrd.img-x", "os": "Debian", "error": "e", "junk": 1}}
    assert reports.well_formed_drivers(value)["linux"] == {
        "state": "hostonly", "module": "nvme", "kver": KVER,
        "initrd": "/boot/initrd.img-x", "os": "Debian", "error": "e"}


@pytest.mark.parametrize("bad", [{"state": "fine"}, "hostonly", {"module": "nvme"}])
def test_a_bad_linux_is_dropped_alone(bad):
    out = reports.well_formed_drivers({"state": "skipped", "packages": [], "linux": bad})
    assert out == {"state": "skipped", "packages": []}


@pytest.mark.parametrize("state,logged", [("hostonly", True), ("unsupported", True),
                                          ("failed", True), ("generic", False),
                                          ("skipped", False)])
def test_only_an_unverified_initramfs_is_journaled(monkeypatch, state, logged):
    calls = []
    monkeypatch.setattr(reports, "journal",
                        lambda c, event, detail, *a, **k: calls.append((event, detail)))
    reports._journal_linux(None, {"state": "skipped", "linux": {"state": state, "error": "x"}},
                           "aa:bb in s1")
    assert (calls == [("linux_initramfs_warning", f"aa:bb in s1 — {state}: x")]) is logged


def test_the_agent_loads_linuxdrivers_before_postdeploy():
    main = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert main.index('"$LIB_DIR/hostname_linux.sh"') < main.index('"$LIB_DIR/linuxdrivers.sh"')
    assert main.index('"$LIB_DIR/linuxdrivers.sh"') < main.index('"$LIB_DIR/postdeploy.sh"')


def test_the_journal_event_has_a_hebrew_label_and_is_a_warning():
    from server import journal_he   # noqa: PLC0415
    assert "linux_initramfs_warning" in journal_he.EVENTS_HE
    assert journal_he.severity("linux_initramfs_warning") == "warn"


def test_interfaces_documents_the_field():
    doc = (AGENT.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "drivers.linux" in doc
    for state in ("generic", "hostonly", "unsupported"):
        assert f"`{state}`" in doc
