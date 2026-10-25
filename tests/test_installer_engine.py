"""Live installer engine (#1190): preflight, contracts, and shimmed stages."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import sizelimit

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "installer" / "imagectl-install"
INSTALLER = REPO / "installer"
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="a POSIX sh is required")
LINUX_ONLY = pytest.mark.skipif(os.name == "nt", reason="executable shims and block-device flow are Linux-only")

FP = "3fa3221ea017f9ce3493fe0fe0c180aaa5ea6293a2a399b0f14977280a074915"
KEYS = (
    "disk", "role", "primary_url", "primary_fingerprint", "servers_if", "servers_mac", "servers_mode",
    "servers_addr", "servers_mask", "servers_gw", "servers_dns", "hostname", "timezone", "admin_user", "admin_pass",
    "root_pass",
)
ROOT_PW = "R00t!chosen-on-screen"   # #1632: what the operator typed on the admin screen


def _machine(tmp_path: Path) -> dict[str, Path | dict[str, str]]:
    root = tmp_path / "root"
    disk = root / "sys" / "block" / "sda"
    (disk / "device").mkdir(parents=True)
    (disk / "size").write_text("125829120\n", newline="\n")
    (disk / "removable").write_text("0\n", newline="\n")
    (disk / "device" / "model").write_text("VMware Virtual disk\n", newline="\n")
    (disk / "device" / "bus").write_text("sata\n", newline="\n")
    nic = root / "sys" / "class" / "net" / "ens33"
    nic.mkdir(parents=True)
    (nic / "address").write_text("00:50:56:01:02:01\n", newline="\n")
    proc = root / "proc"
    proc.mkdir()
    mounts = proc / "mounts"
    mounts.write_text("/dev/sr0 /cdrom iso9660 ro 0 0\n", newline="\n")
    dev = tmp_path / "dev"
    dev.mkdir()
    for name in ("sda", "sda1", "sda2"):
        (dev / name).touch()
    cdrom = tmp_path / "cdrom"
    (cdrom / "imagectl-src" / ".git").mkdir(parents=True)
    (cdrom / "imagectl-src" / "tools" / "iso").mkdir(parents=True)
    (cdrom / "imagectl-src" / "tools" / "iso" / "packages.txt").write_text(
        "busybox-static\n", newline="\n")
    (cdrom / "imagectl").mkdir()
    (cdrom / "imagectl" / "firstboot.sh").write_text("#!/bin/bash\n", newline="\n")
    (cdrom / "imagectl" / "imagectl-firstboot.service").write_text("[Service]\n", newline="\n")
    (cdrom / "imagectl-iso.json").write_text('{"tag":"test"}\n', newline="\n")
    (cdrom / "pool" / "main" / "imagectl").mkdir(parents=True)
    (cdrom / "pool" / "main" / "imagectl" / "x.deb").touch()
    (cdrom / "dists" / "trixie").mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    state = tmp_path / "state"
    env = os.environ.copy()
    env.update({
        "SYSROOT": str(root), "DEVROOT": str(dev), "PROC_MOUNTS": str(mounts),
        "CDROM": str(cdrom), "TARGET": str(target), "STATE_FILE": str(state),
        "BOOTSTRAP_LOG": str(tmp_path / "debootstrap.log"), "IMAGECTL_TEST": "1",
    })
    return {"root": root, "dev": dev, "cdrom": cdrom, "target": target,
            "state": state, "mounts": mounts, "env": env}


def _answers(path: Path, **changes: str) -> Path:
    data = {
        "disk": "/dev/sda", "role": "standalone", "primary_url": "", "primary_fingerprint": "",
        "servers_if": "ens33", "servers_mac": "00:50:56:01:02:01",
        "servers_mode": "dhcp", "servers_addr": "", "servers_mask": "",
        "servers_gw": "", "servers_dns": "", "hostname": "imagectl-ta", "timezone": "Asia/Jerusalem",
        "admin_user": "admin", "admin_pass": "correct horse battery staple", "root_pass": ROOT_PW,
    }
    data.update(changes)
    path.write_text("".join(f"{key}={data[key]}\n" for key in KEYS), newline="\n")
    return path


def _run(box, answers: Path | None = None, *args: str, engine: Path = ENGINE):
    cmd = [SH, str(engine)]
    if answers is not None:
        cmd.append(str(answers))
    cmd.extend(args)
    return subprocess.run(cmd, text=True, capture_output=True, env=box["env"],
                          cwd=REPO, stdin=subprocess.DEVNULL)


def _shim(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, newline="\n")
    path.chmod(0o755)


# What chroot answers for grub (#2137): grub-probe names the root, and
# update-grub writes root= the way Debian's /etc/grub.d/10_linux decides it
# (grub-common 2.12, trixie) -- UUID= only when /dev/disk/by-uuid/<uuid>
# exists in the target, else the device name. The recovery entry is ours
# (41_imagectl_recovery) and always UUID. GRUB_IGNORES_UUID stands for an
# update-grub that writes the device anyway.
_FAKE_GRUB = r"""
fake_grub() {
    _fg_t=$1; shift
    _fg_u=${PROBE_UUID-0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d}; _fg_d=${PROBE_DEV-/dev/sda2}
    case "$*" in
    "grub-probe --target=fs_uuid /") [ -z "${PROBE_FAIL:-}" ] || return 1; printf '%s\n' "$_fg_u" ;;
    "grub-probe --target=device /") printf '%s\n' "$_fg_d" ;;
    update-grub)
        if [ -L "$_fg_t/dev/disk/by-uuid/$_fg_u" ] && [ -z "${GRUB_IGNORES_UUID:-}" ]; then _fg_r=UUID=$_fg_u
        else _fg_r=$_fg_d; fi
        mkdir -p "$_fg_t/boot/grub" || return 1
        printf 'menuentry "Debian GNU/Linux" {\n\tlinux\t/boot/vmlinuz-6.12.113+deb13-amd64 root=%s ro  quiet\n}\n' "$_fg_r" > "$_fg_t/boot/grub/grub.cfg"
        printf 'menuentry "ImageSrv recovery (text console)" {\n    linux /vmlinuz root=UUID=%s ro nomodeset\n}\n' "$_fg_u" >> "$_fg_t/boot/grub/grub.cfg" ;;
    esac
}
"""
ROOT_UUID = "0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"


def _command_shims(tmp_path: Path, env: dict[str, str], *, debootstrap_rc: int = 0) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls"
    common = f'printf "%s %s\\n" "${{0##*/}}" "$*" >> {calls}\n'
    for name in ("sgdisk", "blockdev", "mkfs.vfat", "mkfs.ext4", "mount", "umount",
                 "chroot", "grub-install", "reboot"):
        _shim(bindir / name, common + "exit 0\n")
    _shim(bindir / "debootstrap", common + f"echo bootstrap-output\nexit {debootstrap_rc}\n")
    dev = Path(env["DEVROOT"])
    _shim(bindir / "blkid", f'''case "$*" in
*"PTTYPE"*"{dev}/sda") echo gpt ;;
*"TYPE"*"{dev}/sda1") echo vfat ;;
*"TYPE"*"{dev}/sda2") echo ext4 ;;
*"UUID"*"{dev}/sda1") echo ESP-UUID ;;
*"UUID"*"{dev}/sda2") echo ROOT-UUID ;;
*) exit 2 ;;
esac
''')
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return calls


@pytest.mark.parametrize("missing", KEYS)
@LINUX_ONLY
def test_each_missing_answer_is_named_and_no_disk_command_runs(tmp_path, missing):
    box = _machine(tmp_path)
    answers = _answers(tmp_path / "answers")
    lines = [line for line in answers.read_text(encoding="utf-8").splitlines() if not line.startswith(missing + "=")]
    answers.write_text("\n".join(lines) + "\n", newline="\n")
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, answers, "--dry-run")
    assert got.returncode == 2
    assert f"missing answer: {missing}" in got.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8") if calls.exists() else ""


@pytest.mark.parametrize(("changes", "code"), [
    ({"disk": "/dev/sda1"}, "invalid-disk"),
    ({"role": "primary"}, "invalid-role"),
    ({"primary_url": "https://main.example:8443"}, "invalid-primary-url"),
    ({"role": "secondary", "primary_url": "http://main:8443", "primary_fingerprint": FP}, "invalid-primary-url"),
    ({"role": "secondary", "primary_url": "main:9443", "primary_fingerprint": FP}, "invalid-primary-url"),
    ({"primary_fingerprint": FP}, "invalid-primary-url"),
    # #1310: a secondary whose primary never answered green is refused
    ({"role": "secondary", "primary_url": "192.168.68.250"}, "primary-unverified"),
    ({"role": "secondary", "primary_url": "192.168.68.250", "primary_fingerprint": "ab" * 31}, "primary-unverified"),
    ({"role": "secondary", "primary_url": "192.168.68.250", "primary_fingerprint": "AB" * 32}, "primary-unverified"),
    ({"hostname": "-bad"}, "invalid-hostname"),
    ({"servers_if": "ens99"}, "invalid-interface"),
    ({"servers_mac": "bad"}, "invalid-mac"),
    ({"servers_mac": "00:50:56:01:02:02"}, "mac-mismatch"),
    ({"servers_mode": "manual"}, "invalid-network-mode"),
    ({"servers_addr": "10.0.0.8"}, "invalid-network"),
    ({"servers_mode": "static", "servers_addr": "999.0.0.8",
      "servers_mask": "255.255.255.0", "servers_gw": "10.0.0.1",
      "servers_dns": "10.0.0.2"}, "invalid-address"),
    ({"servers_mode": "static", "servers_addr": "10.0.0.8",
      "servers_mask": "bad", "servers_gw": "10.0.0.1",
      "servers_dns": "10.0.0.2"}, "invalid-netmask"),
    ({"servers_mode": "static", "servers_addr": "10.0.0.8",
      "servers_mask": "255.255.255.0", "servers_gw": "bad",
      "servers_dns": "10.0.0.2"}, "invalid-gateway"),
    ({"servers_mode": "static", "servers_addr": "10.0.0.8",
      "servers_mask": "255.255.255.0", "servers_gw": "10.0.0.1",
      "servers_dns": "10.0.0.2,bad"}, "invalid-dns"),
    ({"admin_pass": ""}, "invalid-admin-password"),
    ({"root_pass": ""}, "invalid-root-password"),   # #1632: no default, no value from the ISO
    ({"admin_user": "Admin"}, "invalid-admin-user"),
    ({"admin_user": "-x"}, "invalid-admin-user"),
])
@LINUX_ONLY
def test_bad_answers_are_named_before_any_disk_command(tmp_path, changes, code):
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", **changes), "--dry-run")
    assert got.returncode == 2 and f"[{code}]" in got.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8") if calls.exists() else ""


@pytest.mark.parametrize("typed", ["192.168.68.250", "https://192.168.68.250:8443", "imagectl-ta:8443"])
@LINUX_ONLY
def test_a_secondary_with_a_verified_primary_passes_in_any_typed_form(tmp_path, typed):
    """#1310: the IP alone is enough -- and the fingerprint of a green check
    is what lets the secondary install at all."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", role="secondary", primary_url=typed,
                             primary_fingerprint=FP), "--dry-run")
    assert got.returncode == 0, got.stderr


@pytest.mark.parametrize(("typed", "stored"), [
    ("192.168.68.250", "https://192.168.68.250:8443"),
    ("  192.168.68.250 ", "https://192.168.68.250:8443"),
    ("192.168.68.250:8443", "https://192.168.68.250:8443"),
    ("https://192.168.68.250:8443/", "https://192.168.68.250:8443"),
    ("imagectl-ta.college.ac.il", "https://imagectl-ta.college.ac.il:8443"),
    ("http://192.168.68.250", None), ("192.168.68.250:9443", None), ("", None),
    ("https://x:8443/api", None), ("a b", None), ("-x", None),
])
def test_normalize_primary_url_has_one_stored_form(typed, stored):
    """One rule for the GUI bridge and the engine (installer/lib/answers.sh)."""
    got = subprocess.run([SH, "-c", '. "$1"; normalize_primary_url "$2"', "_",
                          str(INSTALLER / "lib" / "answers.sh"), typed],
                         text=True, capture_output=True, stdin=subprocess.DEVNULL)
    if stored is None:
        assert got.returncode != 0 and got.stdout == "", got.stdout
    else:
        assert got.returncode == 0 and got.stdout == stored


@LINUX_ONLY
def test_iso_device_is_refused_before_any_disk_command(tmp_path):
    box = _machine(tmp_path)
    box["mounts"].write_text("/dev/sda1 /cdrom iso9660 ro 0 0\n", newline="\n")
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert got.returncode == 2 and "[iso-target]" in got.stderr
    assert not calls.exists()


@LINUX_ONLY
def test_inventory_has_the_documented_line_from_sysfs_and_blkid(tmp_path):
    box = _machine(tmp_path)
    disk = box["root"] / "sys" / "block" / "sda"
    for part in ("sda1", "sda2"):
        (disk / part).mkdir()
        (disk / part / "partition").write_text("1\n", newline="\n")
    _command_shims(tmp_path, box["env"])
    got = _run(box, None, "--inventory")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == (
        "disk=/dev/sda|model=VMware Virtual disk|size=64424509440|bus=sata|"
        "removable=0|has=gpt:2 partitions (vfat, ext4)|iso=0"
    )


def _normal(text: str, box) -> list[str]:
    for key in ("target", "cdrom", "dev"):
        text = text.replace(str(box[key]), f"<{key}>")
    return [line for line in text.splitlines() if line.startswith("+")]


@LINUX_ONLY
def test_dry_run_prints_the_exact_command_sequence_and_redacts_secrets(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert got.returncode == 0, got.stderr
    lines = _normal(got.stdout, box)
    assert lines[:11] == [
        "+ sgdisk --zap-all <dev>/sda",
        "+ sgdisk --new=1:1MiB:+512MiB --typecode=1:EF00 --change-name=1:EFI --new=3:0:+1MiB --typecode=3:EF02 --change-name=3:bios_grub --new=2:0:0 --typecode=2:8300 --change-name=2:root <dev>/sda",
        "+ blockdev --rereadpt <dev>/sda",
        "+ mkfs.vfat -F 32 -n EFI <dev>/sda1",
        "+ mkfs.ext4 -F -L root <dev>/sda2",
        "+ mkdir -p <target>",
        "+ mount <dev>/sda2 <target>",
        "+ mkdir -p <target>/boot/efi",
        "+ mount <dev>/sda1 <target>/boot/efi",
        "+ debootstrap --arch amd64 --no-check-gpg trixie <target> file://<cdrom>",
        "+ mkdir -p <target>/dev <target>/proc <target>/sys <target>/cdrom",
    ]
    assert [line.split()[1] for line in lines] == [
        "sgdisk", "sgdisk", "blockdev", "mkfs.vfat", "mkfs.ext4",
        "mkdir", "mount", "mkdir", "mount", "debootstrap", "mkdir",
        "mount", "mount", "mount", "mount", "write-target", "rm",
        "chroot", "chroot", "cp", "cp", "chmod", "cp", "chroot", "chroot",
        "mkdir", "cp", "cp", "verify-package-pool", "write-target",
        "write-target", "write-target", "write-target", "cp", "printf",
        "chroot", "chroot", "write-target", "write-target", "chroot", "chroot", "ln", "chroot", "blkid", "blkid",
        "write-target", "write-target", "write-target", "chroot", "chroot", "write-target", "chroot", "write-target",
        "write-target-secret", "umount", "umount", "umount", "umount",
        "umount", "umount", "sync",
    ]
    assert "+ chroot <target> apt-get install -y --no-install-recommends busybox-static linux-image-amd64 grub-efi-amd64-signed shim-signed grub-pc-bin" in lines
    # --removable and --force-extra-removable are mutually exclusive in Debian's grub-install (ESXi, 21/09)
    assert "+ chroot <target> grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian --force-extra-removable" in lines
    assert "--removable --force-extra-removable" not in got.stdout
    assert "+ chroot <target> grub-install --target=i386-pc /dev/sda" in lines
    time_sync = "+ chroot <target> systemctl enable chrony.service"
    answers = "+ write-target mode=0600 <target>/etc/imagectl/answers"
    assert lines.index(time_sync) + 1 == lines.index(answers)
    grub_cfg = "+ write-target mode=0644 <target>/etc/default/grub.d/imagectl.cfg"
    recovery = "+ write-target mode=0755 <target>/etc/grub.d/41_imagectl_recovery"
    assert lines.index(grub_cfg) < lines.index(recovery) < lines.index("+ chroot <target> update-grub")
    # #2137: the by-uuid link update-grub reads is there before it runs
    link = "+ ln -sf '<root-device>' '<target>/dev/disk/by-uuid/<root-uuid>'"
    assert lines.index(recovery) < lines.index(link) < lines.index("+ chroot <target> update-grub")
    assert lines[-7:] == [
        "+ umount <target>/cdrom", "+ umount <target>/sys", "+ umount <target>/proc",
        "+ umount <target>/dev", "+ umount <target>/boot/efi", "+ umount <target>", "+ sync",
    ]
    assert "correct horse" not in got.stdout + got.stderr
    assert ROOT_PW not in got.stdout + got.stderr   # #1632
    assert "chage" not in got.stdout, "#1632: the operator chose root's password -- nothing to expire"
    assert re.search(r"(?m)^\+\s+reboot(?:\s|$)", got.stdout) is None
    for state in ("partitioning", "bootstrap", "packages", "bootloader", "finishing", "done"):
        assert f"state={state} " in got.stderr
    assert box["state"].read_text(encoding="utf-8").splitlines()[:3] == [
        "state=done", "pct=100", "title=Installation complete"]


def test_grub_menu_is_hidden_with_an_esc_window_written_before_update_grub(tmp_path):
    """Nadav, 23/09 (#1208): boot straight through like ESXi, Esc shows the
    menu. The dry run prints only the path, so install_bootloader runs here
    for real against stubbed helpers and the content is read back."""
    target = tmp_path / "target"
    target.mkdir()
    calls = tmp_path / "calls"
    harness = tmp_path / "harness.sh"
    harness.write_text(f"""set -u
DRY_RUN=0 TARGET='{target.as_posix()}' CDROM='{tmp_path.as_posix()}' ANSWER_DISK=/dev/sda ANSWER_ROOT_PASS='{ROOT_PW}'
SYSROOT='{tmp_path.as_posix()}'   # no efivars here: the NVRAM branch is another test's (#2093)
log() {{ printf '%s\\n' "$*" >> '{calls.as_posix()}'; }}
fail() {{ log "fail $1"; exit 2; }}
progress() {{ :; }}
must_run() {{ shift 2; "$@" || exit 2; }}
{_FAKE_GRUB}
chroot() {{ log "chroot $*"; fake_grub "$@"; }}
mount() {{ return 1; }}
write_target() {{
    log "write-target $1 $2"
    mkdir -p "$TARGET${{2%/*}}" && printf '%s' "$3" > "$TARGET$2"
}}
. '{(INSTALLER / "lib" / "finish.sh").as_posix()}'
install_bootloader
""", newline="\n")
    got = subprocess.run([SH, str(harness)], text=True, capture_output=True,
                         stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    log = calls.read_text(encoding="utf-8").splitlines()
    write = "write-target 0644 /etc/default/grub.d/imagectl.cfg"
    assert write in log, log
    assert log.index(write) < log.index(f"chroot {target.as_posix()} update-grub"), log
    cfg = (target / "etc" / "default" / "grub.d" / "imagectl.cfg").read_text(encoding="utf-8")
    settings = [line for line in cfg.splitlines() if line and not line.startswith("#")]
    # hidden with 0 would leave no window in which to press Esc
    assert settings == ["GRUB_TIMEOUT_STYLE=hidden", "GRUB_TIMEOUT=3"]

    recovery = target / "etc" / "grub.d" / "41_imagectl_recovery"
    text = recovery.read_text(encoding="utf-8")
    assert 'menuentry "ImageSrv recovery (text console)"' in text
    assert "root_uuid=$(grub-probe --target=fs_uuid /)" in text
    assert "root=UUID=$root_uuid ro nomodeset systemd.unit=multi-user.target" in text
    assert log.index("write-target 0755 /etc/grub.d/41_imagectl_recovery") < log.index(
        f"chroot {target.as_posix()} update-grub"
    )


# --- #2137: grub.cfg boots the root by UUID, not by the name of a disk --------

def _grub_root_harness(tmp_path: Path, **probe: str):
    """install_bootloader for real, chroot answering as `_FAKE_GRUB` (10_linux's
    rule). The target's /dev is a plain directory here, as the live
    initramfs's devtmpfs is: no udev, no /dev/disk at all (rc3, 24/10)."""
    target = tmp_path / "target"
    (target / "dev").mkdir(parents=True)
    calls = tmp_path / "calls"
    knobs = "".join(f"{key}='{value}'\nexport {key}\n" for key, value in probe.items())
    harness = tmp_path / "harness.sh"
    harness.write_text(f"""set -u
{knobs}DRY_RUN=0 TARGET='{target.as_posix()}' CDROM='{tmp_path.as_posix()}' ANSWER_DISK=/dev/sda ANSWER_ROOT_PASS='{ROOT_PW}'
SYSROOT='{tmp_path.as_posix()}'
log() {{ printf '%s\\n' "$*" >> '{calls.as_posix()}'; }}
fail() {{ log "fail $1 $*"; exit 2; }}
progress() {{ :; }}
must_run() {{ shift 2; "$@" || exit 2; }}
{_FAKE_GRUB}
chroot() {{ log "chroot $*"; fake_grub "$@"; }}
mount() {{ return 1; }}
write_target() {{ log "write-target $1 $2"; }}
. '{(INSTALLER / "lib" / "finish.sh").as_posix()}'
install_bootloader
log done
""", newline="\n")
    got = subprocess.run([SH, str(harness)], text=True, capture_output=True, stdin=subprocess.DEVNULL)
    log = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return got, log, target


def _kernel_roots(target: Path) -> list[str]:
    text = (target / "boot" / "grub" / "grub.cfg").read_text(encoding="utf-8")
    return re.findall(r"(?m)^\s*linux\s.*?\sroot=(\S+)", text)


@LINUX_ONLY
def test_a_fresh_install_boots_its_root_by_uuid_not_by_disk_name(tmp_path):
    """rc3 (Hyper-V, 24/10): `root=/dev/sda2`, and with a second disk the
    system disk was sdb -- "ALERT! /dev/sda2 does not exist". update-grub
    finds /dev/disk/by-uuid/<uuid> for the root it probed, so every kernel
    line boots root=UUID=."""
    got, log, target = _grub_root_harness(tmp_path)
    assert got.returncode == 0 and "done" in log, (got.stderr, log)
    assert _kernel_roots(target) == [f"UUID={ROOT_UUID}"] * 2
    link = target / "dev" / "disk" / "by-uuid" / ROOT_UUID
    assert os.readlink(link) == "/dev/sda2"
    t = target.as_posix()
    assert log.index(f"chroot {t} grub-probe --target=device /") < log.index(f"chroot {t} update-grub")


@LINUX_ONLY
def test_a_raid_root_links_the_md_array_grub_probe_names(tmp_path):
    """#1989: under RAID the root is the md array; the link is to what
    grub-probe names, not to a member partition."""
    got, log, target = _grub_root_harness(tmp_path, PROBE_DEV="/dev/md0")
    assert got.returncode == 0 and "done" in log, (got.stderr, log)
    assert os.readlink(target / "dev" / "disk" / "by-uuid" / ROOT_UUID) == "/dev/md0"
    assert _kernel_roots(target) == [f"UUID={ROOT_UUID}"] * 2


@LINUX_ONLY
def test_a_grub_cfg_that_still_boots_a_disk_name_fails_the_install_by_name(tmp_path):
    """Principle 5: the verdict is grub.cfg read back, not "update-grub said
    0". An update-grub that wrote the device anyway is a failed install."""
    got, log, target = _grub_root_harness(tmp_path, GRUB_IGNORES_UUID="1")
    assert got.returncode == 2, (got.stderr, log)
    assert "root=/dev/sda2" in (target / "boot" / "grub" / "grub.cfg").read_text(encoding="utf-8")
    assert any(line.startswith("fail grub-root-not-uuid") for line in log), log
    assert "done" not in log


@LINUX_ONLY
def test_a_root_uuid_grub_probe_cannot_read_stops_before_update_grub(tmp_path):
    got, log, target = _grub_root_harness(tmp_path, PROBE_FAIL="1")
    assert got.returncode == 2, (got.stderr, log)
    assert any(line.startswith("fail grub-root-uuid") for line in log), log
    assert not [c for c in log if c.endswith(" update-grub")], log


@LINUX_ONLY
def test_a_grub_cfg_that_was_not_written_is_unchecked_not_passed(tmp_path):
    """No grub.cfg to read back = "could not check", which fails (principle 5)."""
    got, log, target = _grub_root_harness(tmp_path, PROBE_DEV="/dev/sda2")
    assert got.returncode == 0
    (target / "boot" / "grub" / "grub.cfg").unlink()
    harness = (tmp_path / "harness.sh").read_text(encoding="utf-8").replace(
        "install_bootloader\n", "ROOT_FS_UUID=" + ROOT_UUID + "\ngrub_boots_root_by_uuid\n")
    (tmp_path / "harness.sh").write_text(harness, newline="\n")
    got = subprocess.run([SH, str(tmp_path / "harness.sh")], text=True, capture_output=True,
                         stdin=subprocess.DEVNULL)
    log = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    assert got.returncode == 2 and any(line.startswith("fail grub-root-unchecked") for line in log), log


@LINUX_ONLY
def test_failed_debootstrap_sets_failed_state_and_never_reaches_grub(tmp_path):
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"], debootstrap_rc=7)
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 2 and "[bootstrap-failed]" in got.stderr
    state = box["state"].read_text(encoding="utf-8")
    assert "state=failed" in state and "error=bootstrap-failed: debootstrap exited 7" in state
    called = calls.read_text(encoding="utf-8")
    assert "debootstrap --arch amd64" in called
    assert "grub-install" not in called


@LINUX_ONLY
def test_a_source_tree_without_git_fails_unless_the_manifest_says_lab_iso(tmp_path):
    """The update button works on .git (#1185): a public-clone ISO always has
    it, a lab ISO (build-iso --source) declares source_git=false and passes
    with the gap named in the progress log."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    for name in ("chown", "sync", "chmod"):  # the real run past debootstrap, without root
        _shim(tmp_path / "bin" / name, "exit 0\n")
    shutil.rmtree(Path(box["env"]["CDROM"]) / "imagectl-src" / ".git")
    (box["target"] / "opt").mkdir()  # debootstrap (shimmed here) would have made it
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 2 and "[source-git-missing]" in got.stderr
    shutil.rmtree(box["target"] / "opt"); (box["target"] / "opt").mkdir()
    (Path(box["env"]["CDROM"]) / "imagectl-iso.json").write_text(
        '{"tag":"test","source_git":false}\n', newline="\n")
    got = _run(box, _answers(tmp_path / "answers"))
    # past the check: the run goes on to the firstboot copy (which needs a
    # populated target this shimmed box does not have)
    assert "[source-git-missing]" not in got.stderr
    assert "the update button will not work" in got.stderr
    assert "[firstboot-copy]" in got.stderr


@LINUX_ONLY
def test_the_target_tree_is_world_readable_whatever_umask_the_engine_inherits(tmp_path):
    """The console process runs under umask 077 and the engine inherited it:
    apt's sandbox user could not read /var/lib/imagectl/apt-repo on the
    installed server (ESXi, 21/09). Secrets keep their explicit 0600."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    for name in ("chown", "sync", "chmod"):
        _shim(tmp_path / "bin" / name, "exit 0\n")
    (box["target"] / "opt").mkdir()
    engine = tmp_path / "engine.sh"
    engine.write_text(f"#!/bin/sh\numask 077\nexec {SH} {ENGINE} \"$@\"\n", newline="\n")
    engine.chmod(0o755)
    _run(box, _answers(tmp_path / "answers"), engine=engine)  # stops at firstboot-copy on this box
    # the directories the engine itself creates before that point stand in
    # for apt-repo (created later by the same mkdir under the same umask)
    for path in (box["target"] / "cdrom", box["target"] / "boot" / "efi", box["target"] / "sys"):
        assert path.is_dir(), path
        assert path.stat().st_mode & 0o055 == 0o055, f"{path} is not world-readable"
    answers = box["target"] / "etc" / "imagectl" / "answers"
    assert not answers.exists() or answers.stat().st_mode & 0o077 == 0


@LINUX_ONLY
def test_cleanup_unmounts_what_debootstrap_left_under_the_target(tmp_path):
    """debootstrap leaves its own proc mounted inside the target; the
    flag-tracked umount peeled only our bind and the root stayed busy
    (ESXi, 21/09). Leftovers under the target are unmounted by name."""
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    target = box["target"]
    box["mounts"].write_text(
        f"/dev/sr0 /cdrom iso9660 ro 0 0\nproc {target}/proc proc rw 0 0\n", newline="\n")
    got = _run(box, _answers(tmp_path / "answers"))  # fails at the source copy; cleanup still runs
    assert f"installer: unmounted {target}/proc" in got.stderr
    assert f"umount {target}/proc\n" in calls.read_text(encoding="utf-8")
    assert "unmount-failed" not in got.stderr


# --- #2093: the target is released by the kernel's table, not by flags ---------

_KERNEL_MOUNT = r"""
printf 'mount %s\n' "$*" >> "{calls}"
src=; dst=; kind=none; prev=
for arg in "$@"; do
    [ "$prev" = -t ] && kind=$arg
    case "$arg" in --bind) kind=bind ;; esac
    src=$dst; dst=$arg; prev=$arg
done
printf '%s %s %s rw 0 0\n' "$src" "$dst" "$kind" >> "{mounts}"
"""
# umount as the kernel answers it: busy while anything is mounted under the
# point, otherwise the newest mount on that point goes (stacked mounts are lines)
_KERNEL_UMOUNT = r"""
printf 'umount %s\n' "$*" >> "{calls}"
mp=$1
if [ "$mp" = "${{UMOUNT_STUCK:-}}" ] || awk -v m="$mp" 'index($2, m "/") == 1 {{ f = 1 }} END {{ exit !f }}' "{mounts}"; then
    echo "umount: $mp: target is busy." >&2; exit 32
fi
if ! awk -v m="$mp" '{{ l[NR] = $0; if ($2 == m) last = NR }} END {{ if (!last) exit 1; for (i = 1; i <= NR; i++) if (i != last) print l[i] }}' "{mounts}" > "{mounts}.next"; then
    echo "umount: $mp: not mounted." >&2; exit 32
fi
mv "{mounts}.next" "{mounts}"
if [ "$mp" = "${{UMOUNT_LOSES_TABLE:-}}" ]; then mv "{mounts}" "{mounts}.lost"; fi
"""


def _full_install_box(tmp_path):
    """The shimmed box, carried to the end of a real (not dry) run: the
    target tree the copies need, an apt index, and mount/umount that keep
    PROC_MOUNTS the way the kernel would. The leftovers of rc1 (24/10, read
    in the mount table at every step): debootstrap leaves its proc, and the
    package step leaves an efivarfs -- Debian's mdadm initramfs hook mounts
    one on an empty efivars when update-initramfs runs in the chroot. A late
    submount under /target/sys (after update-grub) stands for any other one
    the engine did not make: only the cleanup can see that one."""
    import gzip
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    target, mounts, bindir = box["target"], box["mounts"], tmp_path / "bin"
    (box["root"] / "sys" / "firmware" / "efi" / "efivars").mkdir(parents=True)   # a UEFI machine
    efivars = f"{target}/sys/firmware/efi/efivars"
    _shim(bindir / "mount", _KERNEL_MOUNT.format(calls=calls, mounts=mounts))
    _shim(bindir / "umount", _KERNEL_UMOUNT.format(calls=calls, mounts=mounts))
    _shim(bindir / "debootstrap", f'printf "proc {target}/proc proc rw 0 0\\n" >> {mounts}\nexit 0\n')
    _shim(bindir / "chroot", _FAKE_GRUB + f'printf "%s %s\\n" chroot "$*" >> {calls}\n'
          'fake_grub "$@" || exit 1\n'
          'case "$*" in\n'
          f'*"apt-get install"*) printf "efivarfs {efivars} efivarfs rw,nosuid,nodev,noexec 0 0\\n" >> {mounts} ;;\n'
          f'*"--target=x86_64-efi"*) printf "efivarfs-at-grub-efi %s\\n" "$(grep -c " {efivars} " {mounts})" >> {calls} ;;\n'
          f'*update-grub*) printf "fusectl {target}/sys/fs/fuse/connections fusectl rw 0 0\\n" >> {mounts} ;;\n'
          'esac\nexit 0\n')
    for name in ("chown", "sync"):
        _shim(bindir / name, f'printf "%s %s\\n" {name} "$*" >> {calls}\nexit 0\n')
    for rel in ("opt", "usr/local/sbin", "etc/systemd/system"):
        (target / rel).mkdir(parents=True)
    index = Path(box["env"]["CDROM"]) / "dists" / "trixie" / "main" / "binary-amd64" / "Packages.gz"
    index.parent.mkdir(parents=True)
    index.write_bytes(gzip.compress(b"Package: x\nFilename: pool/main/imagectl/x.deb\n\n"))
    return box, calls


def _under(mounts: Path, target: Path) -> list[str]:
    if not mounts.exists():
        return ["<table gone>"]
    points = [line.split()[1] for line in mounts.read_text(encoding="utf-8").splitlines()]
    return [p for p in points if p == str(target) or p.startswith(f"{target}/")]


@LINUX_ONLY
def test_a_target_released_through_leftovers_completes_the_install(tmp_path):
    """rc1 (24/10, ESXi + Hyper-V): `umount /target/sys` was busy (a submount
    the engine did not make under it), the leftover pass then unmounted
    everything -- and the flag kept saying "mounted": a fully released target
    failed at 88%. The verdict is what the kernel lists, read again: nothing
    -> done."""
    box, calls = _full_install_box(tmp_path)
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 0, got.stderr[-3000:]
    assert box["state"].read_text(encoding="utf-8").splitlines()[0] == "state=done"
    assert _under(box["mounts"], box["target"]) == []
    seen = calls.read_text(encoding="utf-8").splitlines()
    umounts = [line.split()[1] for line in seen if line.startswith("umount ")]
    target = str(box["target"])
    # every submount before the mount it sits on, all before the root
    assert umounts.index(f"{target}/sys/fs/fuse/connections") < umounts.index(f"{target}/sys")
    assert umounts.index(f"{target}/boot/efi") < umounts.index(target) == len(umounts) - 1
    assert "unmount-failed" not in got.stderr and "target is busy" not in got.stderr
    assert any(line.startswith("sync") for line in seen)


@LINUX_ONLY
def test_the_whole_engine_leaves_a_grub_cfg_that_boots_the_root_by_uuid(tmp_path):
    """#2137 through the engine itself (not dry): the shimmed chroot's
    update-grub decides root= as 10_linux does, against the target's /dev."""
    box, _calls = _full_install_box(tmp_path)
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 0, got.stderr[-3000:]
    assert _kernel_roots(box["target"]) == [f"UUID={ROOT_UUID}"] * 2


@LINUX_ONLY
def test_grub_writes_nvram_through_one_efivarfs_not_one_stacked_on_the_hooks(tmp_path):
    """The root of rc1 (mount table at each step, ESXi 24/10): before
    finish.sh mounted efivarfs, one was already there -- mounted during the
    package step by the mdadm initramfs hook (rw,nosuid,nodev,noexec), never
    unmounted. Ours stacked on it; the umount after grub-install peeled only
    ours. The leftover goes first, by name, and after grub-install none stays."""
    box, calls = _full_install_box(tmp_path)
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 0, got.stderr[-3000:]
    seen = calls.read_text(encoding="utf-8").splitlines()
    assert "efivarfs-at-grub-efi 1" in seen, [c for c in seen if "efivarfs" in c]
    assert "unmounted an efivarfs left on the target by update-initramfs" in got.stderr
    efivars = f"{box['target']}/sys/firmware/efi/efivars"
    grub_bios = next(i for i, c in enumerate(seen) if "--target=i386-pc" in c)
    assert f"umount {efivars}" in seen[:grub_bios]
    assert f"umount {efivars}" not in seen[grub_bios:], "nothing left for the cleanup to find"


@LINUX_ONLY
def test_a_mount_that_stays_fails_the_install_by_name_after_sync(tmp_path):
    """The other side (principle 5): something that really stays mounted is a
    failed install that names it -- and the data was synced before saying so."""
    box, calls = _full_install_box(tmp_path)
    box["env"]["UMOUNT_STUCK"] = f"{box['target']}/cdrom"
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 2
    assert f"[unmount-failed] still mounted under {box['target']}: {box['target']}/cdrom" in got.stderr
    state = box["state"].read_text(encoding="utf-8")
    assert "state=failed" in state and "unmount-failed" in state
    seen = calls.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("sync") for line in seen)


@LINUX_ONLY
def test_a_mount_table_that_cannot_be_read_is_not_a_released_target(tmp_path):
    """"Could not check" is not "nothing mounted" (principle 5): the table is
    gone after the umounts, and the install fails as unchecked, not done."""
    box, _calls = _full_install_box(tmp_path)
    box["env"]["UMOUNT_LOSES_TABLE"] = str(box["target"])
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 2
    assert "[unmount-unchecked]" in got.stderr
    assert "state=done" not in box["state"].read_text(encoding="utf-8")


@LINUX_ONLY
def test_static_network_with_no_gateway_and_no_dns_is_valid(tmp_path):
    """#2096: both say "(optional)" on the screen; empty blocked the install
    ("the DNS list is invalid"). A value is still checked (the cases above)."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", servers_mode="static", servers_addr="10.68.0.61",
                             servers_mask="255.255.255.0"), "--dry-run")
    assert got.returncode == 0, got.stderr
    assert "invalid-dns" not in got.stderr and "invalid-gateway" not in got.stderr


@LINUX_ONLY
def test_removing_the_iso_guard_makes_the_destructive_dry_run_reachable(tmp_path):
    """Negative control: one mutation, behavioural failure, not an import failure."""
    box = _machine(tmp_path)
    box["mounts"].write_text("/dev/sda1 /cdrom iso9660 ro 0 0\n", newline="\n")
    mutated = tmp_path / "installer"
    shutil.copytree(INSTALLER, mutated)
    answers_sh = mutated / "lib" / "answers.sh"
    source = answers_sh.read_text(encoding="utf-8")
    guard = '    disk_is_iso "$DISK_NAME" && fail iso-target "refusing the disk that contains /cdrom: $ANSWER_DISK"\n'
    assert source.count(guard) == 1
    answers_sh.write_text(source.replace(guard, ""), newline="\n")
    box["env"]["AGENT_LIB_DIR"] = str(REPO / "agent" / "lib")
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run", engine=mutated / "imagectl-install")
    assert got.returncode == 0, got.stderr
    assert "+ sgdisk --zap-all" in got.stdout


def _copy_repo(tmp_path: Path, packages: dict[str, str], on_cd: tuple[str, ...]):
    """‏copy_local_repo עצמו (לא dry-run) מול ‏/cdrom מדומה: ‏pool של ה-netinst
    (‏pool/main/o/...) לצד pool/main/imagectl, ו-dists/ שמאנדקס את שניהם — כמו
    ה-ISO אחרי `make-pool.sh --index-only` (‏#1371)."""
    import gzip
    cdrom, target = tmp_path / "cdrom", tmp_path / "target"
    for rel in on_cd:
        (cdrom / rel).parent.mkdir(parents=True, exist_ok=True)
        (cdrom / rel).write_bytes(b"deb")
    index = cdrom / "dists" / "trixie" / "main" / "binary-amd64" / "Packages.gz"
    index.parent.mkdir(parents=True)
    index.write_bytes(gzip.compress("".join(
        f"Package: {name}\nFilename: {rel}\n\n" for name, rel in packages.items()).encode()))
    target.mkdir()
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "DRY_RUN=0\n"
        'must_run() { _c=$1; _m=$2; shift 2; "$@" || fail "$_c" "$_m"; }\n'
        "fail() { printf 'FAIL [%s] %s\\n' \"$1\" \"$2\"; exit 2; }\n"
        f'. "{(INSTALLER / "lib" / "bootstrap.sh").as_posix()}"\n'
        "copy_local_repo\n", newline="\n")
    env = {**os.environ, "CDROM": cdrom.as_posix(), "TARGET": target.as_posix(),
           "BOOTSTRAP_LOG": (tmp_path / "debootstrap.log").as_posix()}
    got = subprocess.run([SH, str(harness)], text=True, capture_output=True, env=env,
                         cwd=tmp_path, stdin=subprocess.DEVNULL)
    return got, target / "var" / "lib" / "imagectl" / "apt-repo"


NETINST_AND_OURS = {
    "qemu-system-x86": "pool/main/imagectl/qemu-system-x86_1_amd64.deb",
    "fonts-ibm-plex": "pool/contrib/imagectl/fonts-ibm-plex_1_all.deb",
    "openssh-server": "pool/main/o/openssh/openssh-server_10.0p1-7+deb13u4_amd64.deb",
    "dbus-user-session": "pool/main/d/dbus/dbus-user-session_1.16.2-2_amd64.deb",
}


def test_the_local_repo_has_every_package_its_index_lists(tmp_path):
    """שרת ת"א 13/10: ‏495 מתוך 1134 החבילות באינדקס היו בלי קובץ (openssh-server,
    ‏dbus-user-session) — רק pool/*/imagectl הועתק, ו-dists/ כולו."""
    import gzip
    got, repo = _copy_repo(tmp_path, NETINST_AND_OURS, tuple(NETINST_AND_OURS.values()))
    assert got.returncode == 0, got.stdout + got.stderr
    text = gzip.decompress((repo / "dists/trixie/main/binary-amd64/Packages.gz").read_bytes()).decode()
    listed = [line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Filename: ")]
    assert len(listed) == len(NETINST_AND_OURS)
    missing = [rel for rel in listed if not (repo / rel).is_file()]
    assert missing == [], f"the local repo publishes packages it does not have: {missing}"


def test_an_index_entry_without_a_file_fails_the_install_by_name(tmp_path):
    """ראיה חיובית (עיקרון 5): אינדקס שמונה קובץ שאינו על הדיסק — ההתקנה נעצרת
    בשם, ולא שרת שנופל ב-"File not found" בעדכון הראשון."""
    on_cd = tuple(rel for name, rel in NETINST_AND_OURS.items() if name != "openssh-server")
    got, _repo = _copy_repo(tmp_path, NETINST_AND_OURS, on_cd)
    assert got.returncode == 2
    assert "FAIL [apt-repo-check] 1 of 4 indexed packages are not in the local repository" in got.stdout
    assert "openssh-server_10.0p1-7+deb13u4_amd64.deb" in got.stdout


def test_installed_system_reserves_tty1_before_enabling_firstboot():
    bootstrap = (INSTALLER / "lib" / "bootstrap.sh").read_text(encoding="utf-8")
    mask = 'systemctl mask getty@tty1.service'
    enable = 'systemctl enable imagectl-firstboot.service'
    assert bootstrap.count(mask) == 1, "the installed target does not reserve tty1 before first boot"
    assert bootstrap.index(mask) < bootstrap.index(enable)
    assert 'must_run firstboot-tty1 "could not reserve tty1 for firstboot progress"' in bootstrap
    assert "getty@tty2" not in bootstrap


@pytest.mark.parametrize("path", sizelimit.guarded_shell_files(REPO), ids=lambda p: p.name)
def test_initramfs_shell_files_stay_under_300_lines(path):
    sizelimit.assert_within_limit(path)


@pytest.mark.parametrize("path", [ENGINE, *(INSTALLER / "lib").glob("*.sh")], ids=lambda p: p.name)
def test_installer_is_posix_sh(path):
    got = subprocess.run([SH, "-n", str(path)], text=True, capture_output=True,
                         stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    text = path.read_text(encoding="utf-8")
    for bashism in ("[[", "]]", "declare -a", "declare -A", "${BASH_SOURCE", "<(" , "<<<"):
        assert bashism not in text, f"{path.name}: bashism {bashism!r}"


def test_installer_packages_and_binaries_are_conditional_and_on_the_iso():
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    packages = (REPO / "tools" / "iso" / "packages.txt").read_text(encoding="utf-8")
    assert "--installer)      WITH_INSTALLER=1" in build
    assert "TOOL_BINS+=(debootstrap dpkg-deb mkfs.vfat chroot mkfs.ext4 eject perl dash tar)" in build
    assert "[dash]=dash" in build and "[tar]=tar" in build
    # busybox applets must not shadow the packed tools (ESXi, 21/09)
    assert 'rm "$ROOT/bin/$_shadow"' in build
    assert "[debootstrap]=debootstrap" in build and "[mkfs.vfat]=dosfstools" in build
    assert "debootstrap" in packages and "dosfstools" in packages and "dpkg" in packages


# --- #1632 (Nadav 24/10): "root's password -- I set it, as part of the install" --------

OPENSSL = shutil.which("openssl")
DECOY_PW = "Build-time-1!"   # what an old ISO carried in imagectl/root-password.hash


def _root_harness(tmp_path: Path, root_pass: str) -> tuple[subprocess.CompletedProcess, dict[str, Path]]:
    """install_bootloader for real against stubbed helpers. chroot records its
    argv and, for chpasswd, the line it was fed; openssl is a wrapper that
    records its own argv and environment and runs the real one. The CD carries
    an old-style root-password.hash of a DIFFERENT password: the engine must
    not read it."""
    target = tmp_path / "target"
    target.mkdir()
    cdrom = tmp_path / "cdrom"
    (cdrom / "imagectl").mkdir(parents=True)
    decoy = subprocess.run([OPENSSL, "passwd", "-6", "-stdin"], input=DECOY_PW + "\n",
                           capture_output=True, text=True, check=True).stdout.strip()
    (cdrom / "imagectl" / "root-password.hash").write_text(decoy + "\n", newline="\n")
    files = {name: tmp_path / name for name in ("calls", "shadow", "openssl-argv", "openssl-env")}
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _shim(bindir / "openssl", f"""printf '%s\\n' "$*" >> '{files["openssl-argv"].as_posix()}'
env >> '{files["openssl-env"].as_posix()}'
exec '{OPENSSL}' "$@"
""")
    harness = tmp_path / "harness.sh"
    harness.write_text(f"""set -u
PATH='{bindir.as_posix()}':$PATH
DRY_RUN=0 TARGET='{target.as_posix()}' CDROM='{cdrom.as_posix()}' ANSWER_DISK=/dev/sda
ANSWER_ROOT_PASS='{root_pass}'
SYSROOT='{tmp_path.as_posix()}'   # no efivars here: the NVRAM branch is another test's (#2093)
log() {{ printf '%s\\n' "$*" >> '{files["calls"].as_posix()}'; }}
fail() {{ log "fail $1"; exit 2; }}
progress() {{ :; }}
must_run() {{ shift 2; "$@" || exit 2; }}
{_FAKE_GRUB}
chroot() {{ log "chroot $*"; case "$2" in chpasswd) cat > '{files["shadow"].as_posix()}' ;; *) fake_grub "$@" ;; esac; }}
mount() {{ return 1; }}
write_target() {{ log "write-target $1 $2"; }}
. '{(INSTALLER / "lib" / "finish.sh").as_posix()}'
install_bootloader
""", newline="\n")
    got = subprocess.run([SH, str(harness)], text=True, capture_output=True, stdin=subprocess.DEVNULL)
    return got, files


def _crypt(password: str, salt: str) -> str:
    return subprocess.run([OPENSSL, "passwd", "-6", "-salt", salt, "-stdin"], input=password + "\n",
                          capture_output=True, text=True, check=True).stdout.strip()


@LINUX_ONLY
@pytest.mark.skipif(OPENSSL is None, reason="openssl is in the live image; the test hashes with it")
def test_root_gets_the_hash_of_the_password_chosen_on_the_screen_not_the_isos(tmp_path):
    got, files = _root_harness(tmp_path, ROOT_PW)
    assert got.returncode == 0, got.stderr
    line = files["shadow"].read_text(encoding="utf-8").strip()
    assert line.startswith("root:$6$"), line
    hashed = line.split(":", 1)[1]
    salt = hashed.split("$")[2]
    assert hashed == _crypt(ROOT_PW, salt), "the hash is not of the password chosen on the screen"
    assert hashed != _crypt(DECOY_PW, salt), "the hash is of the ISO's build-time password"
    calls = files["calls"].read_text(encoding="utf-8").splitlines()
    assert f"chroot {(tmp_path / 'target').as_posix()} chpasswd -e" in calls
    assert not [c for c in calls if "chage" in c], "#1632: no forced change of a password the operator chose"
    # never in argv, never in the environment of the hashing process
    assert files["openssl-argv"].read_text(encoding="utf-8").splitlines() == ["passwd -6 -stdin"]
    assert ROOT_PW not in files["openssl-env"].read_text(encoding="utf-8")
    assert ROOT_PW not in got.stdout + got.stderr


@LINUX_ONLY
@pytest.mark.skipif(OPENSSL is None, reason="openssl is in the live image; the test hashes with it")
def test_no_root_password_from_the_screen_fails_by_name_even_with_an_old_iso_hash(tmp_path):
    got, files = _root_harness(tmp_path, "")
    assert got.returncode == 2, (got.stdout, got.stderr)
    calls = files["calls"].read_text(encoding="utf-8").splitlines()
    assert "fail root-password-missing" in calls, calls
    assert not files["shadow"].exists(), "root got a password nobody chose"
    assert not [c for c in calls if "chpasswd" in c or "grub-install" in c], calls


@LINUX_ONLY
def test_an_empty_root_password_stops_the_engine_before_any_disk_command(tmp_path):
    """The real engine, not dry-run: the named state is in the state file the
    GUI reads, and nothing touched the disk."""
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", root_pass=""))
    assert got.returncode == 2 and "[invalid-root-password]" in got.stderr, got.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "error=invalid-root-password:" in box["state"].read_text(encoding="utf-8")
