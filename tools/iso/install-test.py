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

Not covered: Secure Boot (direct kernel boot skips shim), the GUI screens,
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
           "-drive", f"if=pflash,format=raw,file={vars_fd}",
           "-kernel", kernel, "-initrd", initrd,
           "-append", "imagesrv.mode=installer console=ttyS0,115200 INSTALLER_BIN=/bin/sh",
           "-drive", f"file={disk},if=virtio,format=qcow2",
           "-device", "virtio-scsi-pci,id=scsi0",
           "-drive", f"if=none,id=cd0,media=cdrom,readonly=on,file={args.iso}",
           "-device", "scsi-cd,bus=scsi0.0,drive=cd0",
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
            return fail(args, f"the live image did not mount the ISO at /cdrom: {out.strip()[-200:]}")
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
