#!/usr/bin/env python3
"""tools/usb/stick-install-test.py -- #2062: a full server install from the clone stick in QEMU, to state=done.

  python3 -I tools/usb/stick-install-test.py --stick imagesrv-usb-<v>.img --out DIR

The release check of the combined stick (build_usb_clone.sh --server-iso), next to
tools/iso/install-test.py for the ISO. Exit 0 = installed and the stick untouched; 1 = failed, with
the reason; 2 = not run (a missing tool, a stick without the third partition) -- never a pass.
First run: testrunner, 12/11/2026, TCG, 20 minutes from the engine's start to state=done.

What it proves, and how:
  * the stick's own kernel and initramfs (taken out of partition 1 with mtools) are booted with the
    stick's kernel word, imagesrv.mode=usbclone, plus `console=ttyS0,115200 USB_INSTALLER_BIN=/bin/sh`:
    the agent does everything it does for a person, and where it would exec the installer's console
    process it execs a shell on the serial console instead (as tools/iso/install-test.py does);
  * the stick is a usb-storage disk (a COPY of the image: the copy is compared afterwards), the target
    an empty virtio disk, the keyboard a USB keyboard;
  * the settings' button is pressed as a person presses it: F4, eight times Down, Enter, Tab, Enter --
    monitor sendkey, with a screendump after each stage;
  * from the shell on, it is install-test.py's own drive(): the medium is on /cdrom, the engine runs
    with answers as the GUI bridge writes them, and the state file is read until state=done.

Then: partitions 1 and 3 of the stick copy are byte-identical to the image (the install wrote nothing
to them); partition 2 (settings, reports) is reported, not compared -- the stick writes its boot
report there on every boot.

Not covered: the boot chain (BIOS/syslinux, UEFI/shim/GRUB -- the stick's own boot is checked
separately), Secure Boot, the installer's GUI screens, the first boot of the installed disk.
"""

import argparse
import hashlib
import importlib.util
import os
import shutil
import socket
import struct
import subprocess
import sys
import time


def say(msg):
    print(f"stick-install-test: {msg}", flush=True)


def load_install_test(repo):
    spec = importlib.util.spec_from_file_location("install_test", os.path.join(repo, "tools", "iso", "install-test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def partitions(img):
    with open(img, "rb") as f:
        mbr = f.read(512)
    return [struct.unpack_from("<II", mbr, 446 + 16 * i + 8) for i in range(4)]


def part_sha(img, start, sectors):
    h = hashlib.sha256()
    with open(img, "rb") as f:
        f.seek(start * 512)
        left = sectors * 512
        while left:
            block = f.read(min(left, 1 << 20))
            if not block:
                break
            h.update(block)
            left -= len(block)
    return h.hexdigest()


class Monitor:
    def __init__(self, path):
        self.path = path

    def cmd(self, line):
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.path)
        s.settimeout(5)
        try:
            s.recv(4096)
        except socket.timeout:
            pass
        s.sendall((line + "\n").encode())
        time.sleep(0.5)
        try:
            s.recv(65536)
        except socket.timeout:
            pass
        s.close()

    def wait_bright(self, out, seconds):
        end, ppm = time.time() + seconds, os.path.join(out, "probe.ppm")
        while time.time() < end:
            self.cmd(f"screendump {ppm}")
            time.sleep(2)
            if os.path.exists(ppm) and ppm_mean(ppm) > 150:
                os.remove(ppm)
                return True
            time.sleep(8)
        return False

    def shot(self, out, name):
        ppm = os.path.join(out, name + ".ppm")
        self.cmd(f"screendump {ppm}")
        time.sleep(2)
        if os.path.exists(ppm) and shutil.which("pnmtopng"):
            with open(os.path.join(out, name + ".png"), "wb") as f:
                subprocess.run(["pnmtopng", ppm], stdout=f, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            os.remove(ppm)
        say(f"screendump {name}")


def ppm_mean(path):
    """The mean of a sample of the picture's bytes: a text console is near 0, our light screens above 150."""
    with open(path, "rb") as f:
        data = f.read()
    body = data.split(b"\n", 3)[-1] if data[:2] == b"P6" else b""
    sample = body[::997]
    return sum(sample) / len(sample) if sample else 0.0


def wait_log(path, needle, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with open(path, "rb") as f:
                if needle.encode() in f.read():
                    return True
        except OSError:
            pass
        time.sleep(5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stick", required=True)
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."),
                    help="the tree whose tools/iso/install-test.py drives the engine (default: this one)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=3 * 3600)
    ap.add_argument("--boot-timeout", type=int, default=1800)
    ap.add_argument("--gui-wait", type=int, default=900, help="ceiling for the clone screen (TCG)")
    ap.add_argument("--key-gap", type=float, default=6.0, help="seconds between keys (TCG: the kiosk redraws per key)")
    ap.add_argument("--mem-mb", type=int, default=4096)
    ap.add_argument("--smp", type=int, default=4)
    ap.add_argument("--disk-gb", type=int, default=40)
    args = ap.parse_args()
    it = load_install_test(args.repo)
    os.makedirs(args.out, exist_ok=True)
    out = os.path.abspath(args.out)
    args.iso = args.stick   # drive()'s messages name it
    try:
        for t in ("qemu-system-x86_64", "qemu-img", "mtype"):
            it.need(t)
        parts = partitions(args.stick)
        if not parts[2][1]:
            raise it.NotRun("the stick has no third partition (built without --server-iso)")
        p1 = parts[0][0] * 512
        kernel, initrd = os.path.join(out, "vmlinuz"), os.path.join(out, "initrd.img")
        for name, dst in (("vmlinuz", kernel), ("initrd.img", initrd)):
            with open(dst, "wb") as f:
                r = subprocess.run(["mtype", "-i", f"{args.stick}@@{p1}", f"::/imagesrv/{name}"], stdout=f,
                                   stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
            if r.returncode != 0 or os.path.getsize(dst) < 1 << 20:
                raise it.NotRun(f"could not take /imagesrv/{name} out of the stick: {r.stderr.decode()[-200:]}")
        stick = os.path.join(out, "stick.img")
        subprocess.run(["cp", "--sparse=always", args.stick, stick], check=True, stdin=subprocess.DEVNULL)
        before = {i: part_sha(stick, *parts[i]) for i in (0, 1, 2)}
        disk, vars_fd, sock, mon = (os.path.join(out, n) for n in ("disk.qcow2", "vars.fd", "serial.sock", "mon.sock"))
        for f in (disk, sock, mon):
            if os.path.exists(f):
                os.remove(f)
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", disk, f"{args.disk_gb}G"], check=True,
                       stdin=subprocess.DEVNULL)
        shutil.copyfile(it.OVMF_VARS, vars_fd)
    except it.NotRun as e:
        say(f"NOT RUN: {e}")
        return 2
    accel = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg,thread=multi"
    cmd = ["qemu-system-x86_64", "-name", "imagesrv-stick-install-test", "-m", str(args.mem_mb), "-smp", str(args.smp),
           "-accel", accel,
           "-drive", f"if=pflash,format=raw,readonly=on,file={it.OVMF_CODE}",
           "-drive", f"if=pflash,format=raw,file={vars_fd}",
           "-kernel", kernel, "-initrd", initrd,
           "-append", "imagesrv.mode=usbclone console=ttyS0,115200 USB_INSTALLER_BIN=/bin/sh",
           "-device", "qemu-xhci,id=xhci",
           "-drive", f"if=none,id=usb0,format=raw,file={stick}",
           "-device", "usb-storage,bus=xhci.0,drive=usb0",
           "-device", "usb-kbd,bus=xhci.0",
           "-drive", f"file={disk},if=virtio,format=qcow2",
           "-netdev", "user,id=n0,restrict=on", "-device", f"e1000,netdev=n0,mac={it.MAC}",
           "-display", "none", "-vga", "std",
           "-serial", f"unix:{sock},server,nowait", "-monitor", f"unix:{mon},server,nowait"]
    say(f"QEMU ({accel}): {args.stick}")
    with open(os.path.join(out, "qemu.log"), "wb") as qlog:
        qemu = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=qlog, stderr=subprocess.STDOUT)
    rc = 1
    try:
        for _ in range(60):
            if os.path.exists(sock) and os.path.exists(mon):
                break
            time.sleep(1)
        # the serial console's lines, kept by a reader of our own until the shell is expected
        serial_log = os.path.join(out, "serial.log")
        s = socket.socket(socket.AF_UNIX); s.connect(sock); s.settimeout(1)
        m = Monitor(mon)
        end, seen = time.time() + args.gui_wait, b""
        with open(serial_log, "ab") as log:
            while time.time() < end and b"usbclone: boot media on" not in seen:
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    data = b""
                seen += data; log.write(data); log.flush()
                if qemu.poll() is not None:
                    say(f"FAIL: QEMU exited ({qemu.returncode}) before the clone screen"); return 1
            if b"usbclone: boot media on" not in seen:
                say("FAIL: the agent did not find the stick within the ceiling (serial.log)"); return 1
            say("the agent found the stick; waiting for the clone screen")
        s.close()
        if not m.wait_bright(out, args.gui_wait):
            m.shot(out, "0-no-clone-screen")
            say("FAIL: the clone screen (a light frame) did not come up within the ceiling"); return 1
        time.sleep(15)
        m.shot(out, "1-clone-screen")
        m.cmd("sendkey f4"); time.sleep(args.key_gap * 2)
        m.shot(out, "2-settings")
        for _ in range(8):
            m.cmd("sendkey down"); time.sleep(args.key_gap)
        m.shot(out, "3-item-selected")
        m.cmd("sendkey ret"); time.sleep(args.key_gap * 4)
        m.shot(out, "4-dialog")
        m.cmd("sendkey tab"); time.sleep(args.key_gap)
        m.shot(out, "5-red-focused")
        m.cmd("sendkey ret"); time.sleep(args.key_gap * 2)
        m.shot(out, "6-opening")
        rc = it.drive(args, qemu, sock)
    finally:
        if qemu.poll() is None:
            qemu.kill()
        qemu.wait()
    after = {i: part_sha(stick, *parts[i]) for i in (0, 1, 2)}
    for i, name in ((0, "IMGSRV_USB"), (2, "IMAGESRV_INSTALL")):
        same = before[i] == after[i]
        say(f"partition {i + 1} {name}: {'unchanged' if same else 'CHANGED'} ({after[i][:16]})")
        if not same:
            rc = 1
    say(f"partition 2 IMGSRV_DATA: {'unchanged' if before[1] == after[1] else 'written by the stick itself (its boot report)'}")
    say("PASS" if rc == 0 else f"exit {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
