#!/usr/bin/env python3
"""tools/iso/install-test.py -- a full install from the ISO in QEMU, to state=done.

The release check of the ISO (`/work/qemu-iso.sh` on the testrunner) reached
the first installer screen and stopped there. Every ISO from v0.82.0 on had
a pool that could not be installed (apt 3.0.4 next to the netinst's
apt-utils 3.0.3: `apt-install` failed at 45%), and the first-screen check
was green on all of them. This tool runs the installer engine itself, from
the ISO's own kernel and initramfs, against an empty disk, until the engine
writes state=done -- or names how it failed.

  python3 -I tools/iso/install-test.py --iso imagesrv-v0.84.1-amd64.iso --out DIR

How (the way the 06/11 VM test ran it by hand, vm-2490):
  * /live/vmlinuz + /live/initrd.img are taken out of the ISO (xorriso) and
    booted directly, with the ISO's kernel argument `imagesrv.mode=installer`
    plus `console=ttyS0,115200 INSTALLER_BIN=/bin/sh`: init does everything
    it does for a person (temporary DHCP, mounts the medium at /cdrom) and
    then hands the serial console a shell instead of the GUI.
  * the ISO as a SCSI CD (virtio-scsi), an empty 40GB virtio disk, OVMF,
    one e1000 NIC on QEMU user networking with restrict=on -- the install
    is offline, as on a real server.
  * answers are written as the GUI bridge writes them (standalone, DHCP,
    throwaway passwords in DIR/answers, mode 0600), and the engine runs as
    the bridge runs it: imagesrv-install <answers> --state <file>.
  * the state file is read every 30 s. state=done = pass (exit 0).
    state=failed, a dead QEMU, or the ceiling = fail (exit 1), with the
    state's error= line and the engine log in DIR. Missing tools or an
    unreadable ISO = not run (exit 2) -- never a pass.

--medium fat32-usb (#2761) is the other way a person writes the stick: Rufus in its default
"copy files" mode. The ISO's files are copied onto the one FAT32 partition of a GPT stick (label cut
to 11 characters, symlinks as small text files, as Rufus writes them), only the serial console is
added to the grub.cfg copy, and QEMU boots the stick through OVMF -> shim -> grub -> the kernel, so
the boot itself is proved and not only the mount. Needs mtools, sfdisk, xorriso.

Not covered: Secure Boot (direct kernel boot skips shim; OVMF here has it off), BIOS/CSM, the GUI screens,
and the first boot of the installed disk (tools/iso/test-iso.sh stage 2).
Without KVM (TCG) an install is about an hour; give --timeout accordingly.
"""

import argparse
import base64
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time

ENGINE = "/usr/lib/imagesrv/installer/imagesrv-install"
MAC = "52:54:00:15:0a:01"
OVMF_CODE = "/usr/share/OVMF/OVMF_CODE_4M.fd"
OVMF_VARS = "/usr/share/OVMF/OVMF_VARS_4M.fd"


def say(msg):
    print(f"install-test: {msg}", flush=True)


class NotRun(Exception):
    """The test could not run -- exit 2, never a pass."""


def shell_line(cmd, tag):
    """What Serial.run types: the command, then its exit code between markers."""
    return f"{cmd}; echo RC$?{tag}END"


class Serial:
    """A shell on the guest's ttyS0: run a command, get its output and exit code."""

    def __init__(self, path, log):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(path)
        self.sock.settimeout(0.5)
        self.log = log

    def _read(self, seconds, until=None):
        buf, end = b"", time.time() + seconds
        while time.time() < end:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                data = b""
            if data:
                buf += data
                self.log.write(data)
                self.log.flush()
            if until is not None and until.search(buf):
                break
        return buf

    def wait_shell(self, seconds):
        """True once the guest shell expands $((...)) -- the input echo alone never matches."""
        end = time.time() + seconds
        while time.time() < end:
            self.sock.sendall(b"echo SH$((40+2))OK\r")
            if re.search(rb"SH42OK", self._read(10, re.compile(rb"SH42OK"))):
                return True
        return False

    def run(self, cmd, seconds=60):
        tag = secrets.token_hex(4)
        self.sock.sendall(f"{shell_line(cmd, tag)}\r".encode())
        pat = re.compile(rb"RC(\d+)" + tag.encode() + rb"END")
        buf = self._read(seconds, pat)
        m = pat.search(buf)
        if not m:
            return None, buf.decode("utf-8", "replace")
        # The terminal echoes the command line too: callers match whole output
        # lines (^key=value$) that the echoed command can never form.
        return int(m.group(1)), buf[: m.start()].decode("utf-8", "replace")


def need(tool):
    path = shutil.which(tool)
    if not path:
        raise NotRun(f"missing {tool}")
    return path


def extract(iso, out):
    kernel, initrd = os.path.join(out, "vmlinuz"), os.path.join(out, "initrd.img")
    for f in (kernel, initrd):
        if os.path.exists(f):
            os.remove(f)
    proc = subprocess.run([need("xorriso"), "-osirrox", "on", "-indev", iso,
                           "-extract", "/live/vmlinuz", kernel, "-extract", "/live/initrd.img", initrd],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    sizes = [os.path.getsize(f) if os.path.exists(f) else 0 for f in (kernel, initrd)]
    if proc.returncode != 0 or min(sizes) < 1024 * 1024:
        raise NotRun(f"could not take /live/vmlinuz and /live/initrd.img out of {iso}: {proc.stderr.strip()[-300:]}")
    return kernel, initrd


FAT_LABEL = "IMAGESRV_IN"   # Rufus: upper case, cut to the 11 characters a FAT label holds
PART_OFFSET = 1024 * 1024


def tool(name):
    """A tool by name; sfdisk lives in /usr/sbin, which a plain user's PATH may not have."""
    path = shutil.which(name, path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")
    if not path:
        raise NotRun(f"missing {name}")
    return path


def sh(cmd, what, stdin_text=None):
    proc = subprocess.run(cmd, input=stdin_text, stdin=None if stdin_text else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise NotRun(f"{what} failed ({proc.returncode}): {proc.stdout.strip()[-300:]}")
    return proc.stdout


def build_fat32_stick(iso, out):
    """The ISO's files on a GPT stick with one FAT32 partition, the way Rufus's copy mode leaves it.

    Symlinks: Rufus does not recreate them on FAT32; it writes the link's target as a small text file
    (iso.c, read 09/11). Done the same here. No root: everything is a file and mtools works on offsets.
    Returns (image path, the symlinks turned into text files)."""
    tree, img = os.path.join(out, "fat-tree"), os.path.join(out, "usb.img")
    if os.path.isdir(tree):
        sh(["chmod", "-R", "u+w", tree], "chmod")
        shutil.rmtree(tree)
    sh([need("xorriso"), "-osirrox", "on:auto_chmod_on", "-indev", iso, "-extract", "/", tree], "xorriso -extract")
    links = []
    for root, dirs, files in os.walk(tree):
        for name in dirs + files:
            full = os.path.join(root, name)
            if os.path.islink(full):
                links.append(os.path.relpath(full, tree))
                target = os.readlink(full)
                os.unlink(full)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(target)
    # the only change to the content: a serial console and the shell, as the direct-kernel mode has them
    cfg = os.path.join(tree, "boot", "grub", "grub.cfg")
    with open(cfg, encoding="utf-8") as f:
        text = f.read()
    if " --- quiet" not in text:
        raise NotRun("boot/grub/grub.cfg has no ' --- quiet' to put the serial console before")
    os.chmod(cfg, 0o644)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(text.replace(" --- quiet", " console=ttyS0,115200 INSTALLER_BIN=/bin/sh --- quiet", 1))
    total = sum(os.path.getsize(os.path.join(r, n)) for r, _d, fs in os.walk(tree) for n in fs)
    mib = (total * 11 // 10) // (1024 * 1024) + 512
    if os.path.exists(img):
        os.remove(img)
    with open(img, "wb") as f:
        f.truncate(mib * 1024 * 1024)
    sectors = (mib * 1024 * 1024 - 2 * PART_OFFSET) // 512
    sh([tool("sfdisk"), "-q", img], "sfdisk", f"label: gpt\nstart=2048, size={sectors}, type=U\n")
    part = f"{img}@@{PART_OFFSET}"
    sh([tool("mformat"), "-i", part, "-F", "-T", str(sectors), "-v", FAT_LABEL, "::"], "mformat")
    sh([tool("mlabel"), "-i", part, f"::{FAT_LABEL}"], "mlabel")
    sh([tool("mcopy"), "-i", part, "-s", "-n", *[os.path.join(tree, n) for n in sorted(os.listdir(tree))], "::/"], "mcopy")
    shown = sh([tool("mdir"), "-i", part, "::/imagesrv-iso.json"], "mdir imagesrv-iso.json")
    if "imagesrv" not in shown.lower():
        raise NotRun("imagesrv-iso.json did not reach the FAT32 partition")
    shutil.rmtree(tree)
    return img, links


def answers_text(nic):
    pw = lambda: secrets.token_urlsafe(15)  # noqa: E731 -- throwaway VM passwords
    return "".join(f"{k}={v}\n" for k, v in (
        ("disk", "/dev/vda"), ("role", "standalone"), ("primary_url", ""), ("primary_fingerprint", ""),
        ("servers_if", nic), ("servers_mac", MAC), ("servers_mode", "dhcp"), ("servers_addr", ""),
        ("servers_mask", ""), ("servers_gw", ""), ("servers_dns", ""), ("hostname", "isotest"),
        ("timezone", "Asia/Jerusalem"), ("admin_user", "admin"), ("admin_pass", pw()), ("root_pass", pw())))


def read_state(text):
    """The state file's own lines (state=, pct=, title=, log=, error=); the echoed command has none."""
    return dict(line.rstrip("\r").split("=", 1) for line in text.splitlines()
                if re.match(r"^(state|pct|title|log|error)=", line))


def run(args):
    for tool in ("qemu-system-x86_64", "qemu-img"):
        need(tool)
    for f in (OVMF_CODE, OVMF_VARS):
        if not os.path.isfile(f):
            raise NotRun(f"missing {f} (apt-get install ovmf)")
    if not os.path.isfile(args.iso):
        raise NotRun(f"no ISO: {args.iso}")
    os.makedirs(args.out, exist_ok=True)
    fat = args.medium == "fat32-usb"
    if not fat:
        kernel, initrd = extract(args.iso, args.out)
    disk, vars_fd, sock = (os.path.join(args.out, n) for n in ("disk.qcow2", "vars.fd", "serial.sock"))
    for f in (disk, sock):
        if os.path.exists(f):
            os.remove(f)
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", disk, f"{args.disk_gb}G"],
                   check=True, stdin=subprocess.DEVNULL)
    shutil.copyfile(OVMF_VARS, vars_fd)
    accel = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg,thread=multi"
    cmd = ["qemu-system-x86_64", "-name", "imagesrv-install-test", "-m", str(args.mem_mb), "-smp", str(args.smp),
           "-accel", accel,
           "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
           "-drive", f"if=pflash,format=raw,file={vars_fd}"]
    if fat:
        stick, links = build_fat32_stick(args.iso, args.out)
        say(f"FAT32 stick {stick} (label {FAT_LABEL}); {len(links)} symlinks as text files: {' '.join(links[:6])}")
        cmd += ["-device", "qemu-xhci,id=xhci", "-drive", f"if=none,id=usb0,format=raw,file={stick}",
                "-device", "usb-storage,bus=xhci.0,drive=usb0,bootindex=0"]
    else:
        cmd += ["-kernel", kernel, "-initrd", initrd,
                "-append", "imagesrv.mode=installer console=ttyS0,115200 INSTALLER_BIN=/bin/sh",
                "-device", "virtio-scsi-pci,id=scsi0",
                "-drive", f"if=none,id=cd0,media=cdrom,readonly=on,file={args.iso}",
                "-device", "scsi-cd,bus=scsi0.0,drive=cd0"]
    cmd += ["-drive", f"file={disk},if=virtio,format=qcow2",
            "-netdev", "user,id=n0,restrict=on", "-device", f"e1000,netdev=n0,mac={MAC}",
            "-display", "none", "-serial", f"unix:{sock},server,nowait", "-monitor", "none"]
    say(f"QEMU ({accel}): {args.iso}")
    with open(os.path.join(args.out, "qemu.log"), "wb") as qlog:
        qemu = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=qlog, stderr=subprocess.STDOUT)
    try:
        return drive(args, qemu, sock)
    finally:
        if qemu.poll() is None:
            qemu.kill()
        qemu.wait()


def drive(args, qemu, sock):
    for _ in range(60):
        if os.path.exists(sock):
            break
        time.sleep(1)
    with open(os.path.join(args.out, "serial.log"), "ab") as log:
        ser = Serial(sock, log)
        if not ser.wait_shell(args.boot_timeout):
            return fail(args, f"no shell on the serial console within {args.boot_timeout}s (serial.log)")
        rc, out = ser.run("cat /run/imagesrv/install-media && test -f /cdrom/imagesrv-iso.json")
        if rc != 0:
            _rc, seen = ser.run("blkid; ls /dev/sd* /dev/sr* 2>&1; grep -E 'vfat|iso9660' /proc/filesystems", 30)
            with open(os.path.join(args.out, "media-evidence.txt"), "w", encoding="utf-8") as f:
                f.write(seen or "")
            return fail(args, f"the live image did not mount the install medium at /cdrom: {out.strip()[-200:]} "
                              "(what the guest sees: media-evidence.txt)")
        rc, out = ser.run(f"for d in /sys/class/net/*; do [ \"$(cat $d/address)\" = {MAC} ] && echo NIC=${{d##*/}}; done")
        nic = re.search(r"^NIC=([A-Za-z0-9_.-]+)\r?$", out or "", re.M)
        if rc is None or not nic:
            return fail(args, f"no interface with MAC {MAC} in the live image: {(out or '').strip()[-200:]}")
        text = answers_text(nic.group(1))
        path = os.path.join(args.out, "answers")
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(text)
        b64 = base64.b64encode(text.encode()).decode()
        rc, _ = ser.run(f"umask 077; echo {b64} | base64 -d > /tmp/answers")
        if rc != 0:
            return fail(args, "could not write the answers file in the guest")
        rc, out = ser.run(f"{ENGINE} /tmp/answers --dry-run --state /tmp/dry.state > /tmp/dry.log 2>&1; "
                          "rc=$?; tail -n 3 /tmp/dry.log; (exit $rc)", 120)
        if rc != 0:
            return fail(args, f"the engine refused the answers (--dry-run): {(out or '').strip()[-300:]}")
        # `cmd &` closed by run()'s `; echo` is `&;` -- a syntax error in ash (the first QEMU run,
        # 06/11). `& echo PID=$!` is one list, and the pid is the evidence that it started.
        rc, out = ser.run(f"{ENGINE} /tmp/answers --state /tmp/install.state > /tmp/engine.log 2>&1 & echo PID=$!")
        pid = re.search(r"^PID=(\d+)\r?$", out or "", re.M)
        if rc != 0 or not pid:
            return fail(args, f"could not start the engine: {(out or '').strip()[-200:]}")
        pid = pid.group(1)
        say(f"engine {pid} started on {nic.group(1)}; reading the state every 30s, ceiling {args.timeout}s")
        end, last, state, gone = time.time() + args.timeout, None, {}, False
        with open(os.path.join(args.out, "progress.log"), "a", encoding="utf-8") as prog:
            while time.time() < end:
                if qemu.poll() is not None:
                    return fail(args, f"QEMU exited ({qemu.returncode}) during the install")
                rc, out = ser.run(f"cat /tmp/install.state; kill -0 {pid} 2>/dev/null && echo ENGINE=up", 30)
                state = read_state(out or "") or state
                up = re.search(r"^ENGINE=up\r?$", out or "", re.M)
                line = " ".join(f"{k}={state[k]}" for k in ("state", "pct", "title", "error") if k in state)
                if line and line != last:
                    say(line)
                    prog.write(time.strftime("%H:%M:%S ") + line + "\n")
                    prog.flush()
                    last = line
                if state.get("state") in ("done", "failed"):
                    break
                if rc is not None and not up:
                    # gone: one more read (it may have written done/failed after our cat),
                    # otherwise a fail below -- not a 3h wait.
                    _rc, out = ser.run("cat /tmp/install.state", 30)
                    state, gone = read_state(out or "") or state, True
                    break
                time.sleep(30)
        _rc, log_text = ser.run("cat /tmp/engine.log", 600)
        with open(os.path.join(args.out, "engine.log"), "w", encoding="utf-8") as f:
            f.write(log_text or "")
        if state.get("state") == "done":
            say(f"PASS: state=done ({args.iso})")
            return 0
        if state.get("state") == "failed":
            return fail(args, f"state=failed error={state.get('error', '?')} (engine.log)")
        why = "the engine exited without state=done/failed" if gone else f"no state=done within {args.timeout}s"
        return fail(args, f"{why}; last: {last} (engine.log)")


def fail(args, msg):
    say(f"FAIL: {msg}")
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1])
    ap.add_argument("--iso", required=True)
    ap.add_argument("--out", required=True, help="work and evidence directory (disk, serial.log, engine.log)")
    ap.add_argument("--medium", choices=("cdrom", "fat32-usb"), default="cdrom",
                    help="cdrom: the ISO as a CD, direct kernel boot (default); fat32-usb: Rufus copy mode, via OVMF (#2761)")
    ap.add_argument("--timeout", type=int, default=3 * 3600, help="install ceiling in seconds (default 3h, TCG)")
    ap.add_argument("--boot-timeout", type=int, default=1800, help="ceiling for the live shell (default 30 min)")
    ap.add_argument("--mem-mb", type=int, default=4096)
    ap.add_argument("--smp", type=int, default=4)
    ap.add_argument("--disk-gb", type=int, default=40)
    args = ap.parse_args()
    try:
        return run(args)
    except NotRun as e:
        say(f"NOT RUN: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
