#!/usr/bin/env python3
"""tools/iso/esxi-install-test.py -- a full install from the ISO on ESXi, to /health/live.

Step 11 of the release chain, in one command (#4029). tools/iso/install-test.py proves the
installer ENGINE in QEMU; this one proves the ISO as a person meets it on the lab's ESXi: UEFI
boot, GRUB, the live GUI, the nine screens, the reboot into the installed disk, and the server
answering with the version the ISO was built as.

  python tools/iso/esxi-install-test.py --iso "[store] pub-v0.99.0/imagesrv-v0.99.0-amd64.iso" \\
      --out DIR --govc C:/Projects/ImageSrv/lab/esxi/govc.exe --env C:/Projects/ImageSrv/lab/esxi/esxi.env

  --iso     a local file (uploaded to [store] iso/<name> unless a file of the same size is there)
            or a datastore path "[store] dir/file.iso" that is already on the datastore.
  --env     the lab's esxi.env, or $ESXI_ENV. Only its GOVC_* lines are read: the file also holds a
            server's admin password, and this tool never touches it. Never put it in the repo.

How (govc and the lab's esxi.env only -- no SSH to ESXi, no known_hosts):
  * a VM named eit-<...> (a name without the prefix is refused, and so is a name that already
    exists): EFI, pvscsi disk, vmxnet3 NIC on "VM Network", the ISO as a CD, boot order disk then
    CD -- so the reboot at the end of the install lands on the installed disk.
  * the install is the GUI's own nine screens, typed with `govc vm.keystrokes`, one key per call
    (a whole string in one call loses most of its keys: measured 22/11, 6 of 16 arrived). The
    answers are install-test.py's own (standalone, DHCP, hostname, admin) with passwords the GUI
    accepts and a keyboard can type; they are written to DIR/answers, mode 0600.
  * every screen is PROVED, not assumed: the screenshot is read for the rail's highlighted step
    (the blue circle) and a key that did not move the screen is pressed again, then fails.
  * the server's address is found by the VM's MAC (a TCP touch of the subnet fills the ARP table
    -- --subnet, default the /24 of this machine; or --ip). GET https://<ip>/api/console/health/live
    is the evidence: its `version` must equal the ISO's (from the file name, or --expect-version).
  * the VM it created is powered off and deleted at the end, pass or fail. Nothing else is touched.

Exit: 0 only when /health/live was read and its version matches (an answer that never came, or a
version that was not read, is not a pass -- principle 5) · 1 failed (the evidence is in DIR) ·
2 not run (a missing tool, env, ISO or name -- never a pass).
"""

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
VM_PREFIX = "eit-"
HEALTH_PATH = "/api/console/health/live"
RAIL_X, RAIL_Y0, RAIL_DY, RAIL_STEPS = 1235, 124 - 11, 65, 9   # the 1280x768 GUI's step circles, sampled inside the ring
RAIL_BLUE = (0, 121, 184)                                      # the current step; done = green (42, 115, 0)
TYPABLE = re.compile(r"^[a-z0-9-]+$")                          # what _key() can press without a modifier


def say(msg):
    print(f"esxi-install-test: {msg}", flush=True)


class NotRun(Exception):
    """The test could not run -- exit 2, never a pass."""


class Failed(Exception):
    """The test ran and the ISO did not do what a person needs -- exit 1."""


def load_install_test():
    spec = importlib.util.spec_from_file_location("install_test", HERE / "install-test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- pure logic: what is read and decided --------------------------------------------------

def load_govc_env(path):
    """GOVC_* from the lab's esxi.env, and nothing else (it also holds a server's admin password)."""
    if not path or not os.path.isfile(path):
        raise NotRun(f"no esxi.env ({path!r}): pass --env or set ESXI_ENV")
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^(GOVC_[A-Z_]+)=(.*)$", line.strip().replace("\r", ""))
            if m:
                env[m.group(1)] = m.group(2).strip().strip("\"'")
    missing = [k for k in ("GOVC_URL", "GOVC_USERNAME", "GOVC_PASSWORD") if not env.get(k)]
    if missing:
        raise NotRun(f"{path} has no {', '.join(missing)}")
    return env


def expected_version(iso, override=None):
    """`imagesrv-v0.99.0-amd64.iso` -> `0.99.0`. Without one there is nothing to compare: not run."""
    if override:
        return override.lstrip("v")
    m = re.search(r"-v(\d+\.\d+\.\d+[0-9A-Za-z.+~-]*?)-amd64\.iso$", os.path.basename(iso))
    if not m:
        raise NotRun(f"cannot tell the expected version from {os.path.basename(iso)!r}: pass --expect-version")
    return m.group(1)


def parse_health(text):
    """(version or None, why not). `version: null` comes with `version_error` -- "not read" is not "no version"."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return None, "not JSON"
    if not isinstance(doc, dict) or doc.get("ok") is not True:
        return None, "ok is not true"
    ver = doc.get("version")
    if not isinstance(ver, str) or not ver.strip():
        return None, f"version not read ({doc.get('version_error')!r})"
    return ver.strip(), ""


def verdict(expected, health_text):
    """(exit code, message). 0 only for a version that was read and equals the expected one."""
    if health_text is None:
        return 1, "FAIL: /health/live never answered"
    got, why = parse_health(health_text)
    if got is None:
        return 1, f"FAIL: /health/live answered but the version was not read: {why}"
    if got.lstrip("v") != expected.lstrip("v"):
        return 1, f"FAIL: /health/live says version {got!r}, the ISO is {expected!r}"
    return 0, f"PASS: /health/live version {got} == the ISO's {expected}"


def ips_from_neighbors(text, mac):
    """Every IPv4 on an ARP/neighbour line that carries `mac` (arp -a, arp -an and ip neigh all work).
    More than one is normal: the live installer's address and the installed server's can differ."""
    want, found = mac.lower().replace("-", ":"), []
    for line in (text or "").splitlines():
        if want in line.lower().replace("-", ":"):
            m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
            if m and m.group(1) not in found:
                found.append(m.group(1))
    return found


def _png_rows(data, last_row):
    """Unfiltered rows 0..last_row of an 8-bit RGB, non-interlaced PNG (what govc's console capture is)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, head = 8, bytearray(), None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    if head is None or head[2:] != (8, 2, 0, 0, 0):
        raise ValueError(f"unsupported PNG header {head}")
    width, raw, stride, prev, rows = head[0], zlib.decompress(bytes(idat)), head[0] * 3, bytearray(head[0] * 3), []
    for y in range(last_row + 1):
        ft, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride if ft else 0):     # filter 0 (None) leaves the row as it is
            a = line[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        rows.append(line)
        prev = line
    return width, rows


def rail_step(png):
    """Which of the installer's nine steps is highlighted in this screenshot (1..9), or None.

    Read from the step circles on the left rail: the current one is the GUI's blue, done is green,
    pending is dark. Exactly one blue circle = positive evidence; anything else = None."""
    try:
        width, rows = _png_rows(png, RAIL_Y0 + RAIL_DY * (RAIL_STEPS - 1))
    except (ValueError, zlib.error, struct.error, IndexError):
        return None
    if width <= RAIL_X:
        return None
    blue = [i + 1 for i in range(RAIL_STEPS)
            if tuple(rows[RAIL_Y0 + RAIL_DY * i][RAIL_X * 3:RAIL_X * 3 + 3]) == RAIL_BLUE]
    return blue[0] if len(blue) == 1 else None


_KEYS = {"-": "KEY_MINUS", **{str(d): f"KEY_{d}" for d in range(10)}, **{chr(c): f"KEY_{chr(c).upper()}" for c in range(97, 123)}}


def _typed(text):
    if not TYPABLE.match(text or ""):
        raise NotRun(f"cannot type {text!r} (the keyboard script presses a-z, 0-9 and - only)")
    return [_KEYS[c] for c in text]


def gui_script(ans):
    """(name, keys, the rail step after Enter) for each screen. The role, network, time and backup
    screens take their defaults (primary server, DHCP, automatic time, 5 GB): install-test's answers."""
    if (ans["role"], ans["servers_mode"], ans["timezone"]) != ("standalone", "dhcp", "Asia/Jerusalem"):
        raise NotRun("install-test.py's answers are no longer the GUI's defaults (role, DHCP, time zone): "
                     "teach gui_script the screens first")
    pw, rpw = _typed(ans["admin_pass"]), _typed(ans["root_pass"])
    tab = ["KEY_TAB"]
    return [
        ("disk", ["KEY_SPACE"], 2),                 # "I understand"; the one disk is already chosen
        ("role", [], 3), ("network", [], 4),
        ("hostname", _typed(ans["hostname"]), 5),
        ("time", [], 6), ("backup", [], 7),
        ("admin", _typed(ans["admin_user"]) + tab + pw + tab * 2 + pw + tab * 2 + rpw + tab * 2 + rpw, 8),
        ("summary", [], 8),                         # "החל התקנה": the progress screen, rail step 8 still
    ]


def esxi_answers(mac, make_answers):
    """install-test.py's answers, changed only where ESXi differs: the disk, the MAC, and passwords
    the GUI accepts (8+, a letter, a digit, a symbol) that a one-key-at-a-time keyboard can type."""
    ans = dict(line.split("=", 1) for line in make_answers("eth0").splitlines())
    ans.update(disk="/dev/sda", servers_mac=mac,
               admin_pass="eit-" + secrets.token_hex(6), root_pass="eit-" + secrets.token_hex(6))
    return ans


# --- the world: govc, the network, the clock -----------------------------------------------

class Govc:
    def __init__(self, exe, env):
        self.exe, self.env = exe, {**os.environ, **env}

    def run(self, *args, timeout=300):
        """(rc, output). A hung govc is rc 124, never a wait without end; args are never echoed (keys)."""
        try:
            proc = subprocess.run([self.exe, *args], env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return 124, f"govc {args[0]} timed out after {timeout}s"
        return proc.returncode, proc.stdout.decode("utf-8", "replace")


def fetch_health(ip, timeout=10):
    """The body of GET /api/console/health/live, or None. TLS is self-signed: nothing secret is sent."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(f"https://{ip}{HEALTH_PATH}", timeout=timeout, context=ctx) as r:
            return r.read().decode("utf-8", "replace") if r.status == 200 else None
    except (OSError, ValueError):
        return None


def read_neighbors():
    cmds = [["arp", "-a"]] if os.name == "nt" else [["ip", "neigh"], ["arp", "-an"]]
    for cmd in cmds:
        if shutil.which(cmd[0]):
            return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, check=False).stdout
    return ""


def touch_subnet(cidr):
    """One TCP SYN to every address: whoever is there answers ARP, which is all the ARP table needs."""
    def poke(ip):
        try:
            socket.create_connection((str(ip), 443), timeout=0.4).close()
        except OSError:
            pass
    with ThreadPoolExecutor(64) as pool:
        list(pool.map(poke, ipaddress.ip_network(cidr).hosts()))


def local_subnet(toward):
    """The /24 of this machine's address on the way to `toward` (the ESXi host)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((toward, 443))
        return str(ipaddress.ip_network(s.getsockname()[0] + "/24", strict=False))


class World:
    """What touches the outside; a test swaps the pieces."""

    def __init__(self, govc, subnet=None):
        self.govc, self.subnet = govc, subnet
        self.fetch, self.neighbors, self.touch = fetch_health, read_neighbors, touch_subnet
        self.sleep, self.now = time.sleep, time.monotonic

    def find_ips(self, mac):
        if self.subnet:
            self.touch(self.subnet)
        return ips_from_neighbors(self.neighbors(), mac)


# --- the test ----------------------------------------------------------------------------------

class Lab:
    def __init__(self, world, args, out):
        self.w, self.g, self.args, self.out, self.shots = world, world.govc, args, Path(out), 0
        self.name, self.created = args.vm_name, False

    def govc(self, *a, timeout=300):
        return self.g.run(*a, timeout=timeout)

    def vm_exists(self):
        rc, out = self.govc("vm.info", self.name, timeout=60)
        if rc != 0:
            raise NotRun(f"govc vm.info {self.name} failed ({rc}): {out.strip()[-200:]}")
        return re.search(r"^Name:\s+\S", out, re.MULTILINE) is not None

    def iso_on_datastore(self):
        """(datastore, path) of the ISO, uploading a local file when the datastore has no same-size copy."""
        iso = self.args.iso
        m = re.match(r"^\[([^\]]+)\]\s*(.+)$", iso)
        ds, path = (m.group(1), m.group(2)) if m else (self.args.datastore, f"iso/{os.path.basename(iso)}")
        size = self.remote_size(ds, path)
        if m:
            if not size:
                raise NotRun(f"[{ds}] {path} is not on the datastore")
            return ds, path
        if not os.path.isfile(iso):
            raise NotRun(f"no ISO: {iso}")
        if size == os.path.getsize(iso):
            say(f"[{ds}] {path} is already there ({size} bytes)")
            return ds, path
        say(f"uploading {iso} to [{ds}] {path}")
        rc, out = self.govc("datastore.upload", "-ds", ds, iso, path, timeout=3600)
        if rc != 0 or self.remote_size(ds, path) != os.path.getsize(iso):
            raise NotRun(f"upload to [{ds}] {path} failed or came back a different size: {out.strip()[-200:]}")
        return ds, path

    def remote_size(self, ds, path):
        rc, out = self.govc("datastore.ls", "-ds", ds, "-l", "-json", path, timeout=120)
        if rc != 0:
            return 0
        try:
            return int(json.loads(out)[0]["file"][0]["fileSize"])
        except (ValueError, KeyError, IndexError, TypeError):
            return 0

    def shot(self, label, numbered=True):
        """A console screenshot: NN-label.png, or label.png (overwritten) for the polling ones. Returns its bytes."""
        if numbered:
            self.shots += 1
        path = self.out / (f"{self.shots:02d}-{label}.png" if numbered else f"{label}.png")
        rc, out = self.govc("vm.console", "-capture", str(path), self.name, timeout=120)
        if rc != 0 or not path.is_file() or path.stat().st_size < 100:
            raise Failed(f"no console screenshot ({rc}): {out.strip()[-200:]}")
        return path.read_bytes()

    def key(self, code):
        rc, out = self.govc("vm.keystrokes", "-vm", self.name, "-c", code, timeout=60)
        if rc != 0:
            raise Failed(f"keystroke failed ({rc}): {out.strip()[-120:]}")
        self.w.sleep(0.25)

    def wait_step(self, want, label, seconds):
        """Screenshots until the rail shows step `want`; the last screenshot is kept either way."""
        end = self.w.now() + seconds
        while True:
            seen = rail_step(self.shot(label, numbered=False))
            if seen == want:
                return
            if self.w.now() >= end:
                raise Failed(f"the GUI never showed step {want} within {seconds}s (last seen: {seen}); see {label}")
            self.w.sleep(5)

    def create_vm(self, ds, iso_path):
        a = self.args
        say(f"creating {self.name} (EFI, {a.mem_mb} MB, {a.cpus} vCPU, {a.disk_gb} GB, \"{a.network}\")")
        rc, out = self.govc("vm.create", "-on=false", "-g", "debian12_64Guest", "-c", str(a.cpus), "-m", str(a.mem_mb),
                            "-firmware", "efi", "-disk", f"{a.disk_gb}GB", "-disk.controller", "pvscsi",
                            "-net", a.network, "-net.adapter", "vmxnet3", "-iso", iso_path, "-iso-datastore", ds,
                            "-ds", a.vm_datastore, self.name)
        self.created = True     # from here on the name is ours: whatever exists under it was made by this run
        if rc != 0:
            raise Failed(f"vm.create failed ({rc}): {out.strip()[-300:]}")
        rc, out = self.govc("device.boot", "-vm", self.name, "-order", "disk,cdrom")
        if rc != 0:
            raise Failed(f"device.boot failed ({rc}): {out.strip()[-200:]}")

    def power_on_and_mac(self):
        rc, out = self.govc("vm.power", "-on", self.name)
        if rc != 0:
            raise Failed(f"power on failed ({rc}): {out.strip()[-200:]}")
        for _ in range(10):
            rc, out = self.govc("device.info", "-vm", self.name, "ethernet-0", timeout=60)
            m = re.search(r"MAC Address:\s*([0-9a-fA-F:]{17})", out)
            if rc == 0 and m:
                return m.group(1).lower()
            self.w.sleep(2)
        raise Failed("ESXi gave the NIC no MAC address")

    def drive_gui(self, script):
        say(f"waiting for the installer's first screen (up to {self.args.gui_timeout}s)")
        self.wait_step(1, "boot", self.args.gui_timeout)
        for name, keys, after in script:
            for k in keys:
                self.key(k)
            before = self.shot(f"screen-{name}")
            for _attempt in range(3):
                self.key("KEY_ENTER")
                self.w.sleep(3)
                now = self.shot(f"after-{name}")
                # the summary's next screen has the same rail step: there only a changed screen is the proof
                if rail_step(now) == after and (name != "summary" or now != before):
                    break
            else:
                raise Failed(f"the {name} screen did not advance after 3 Enter presses (rail step "
                             f"{rail_step(now)}, wanted {after}); see the screenshots")
            say(f"{name}: ok (rail step {after})")

    def wait_health(self, mac, seconds):
        """Poll until /health/live answers; a progress screenshot is kept every two minutes."""
        say(f"install running; waiting up to {seconds}s for /health/live (VM MAC {mac})")
        end, next_shot, last_rail = self.w.now() + seconds, 0, None
        while self.w.now() < end:
            if self.w.now() >= next_shot:
                rail = rail_step(self.shot("progress", numbered=False))
                if rail != last_rail:
                    say(f"rail step {rail}")
                    last_rail = rail
                next_shot = self.w.now() + 120
            for ip in [self.args.ip] if self.args.ip else self.w.find_ips(mac):
                body = self.w.fetch(ip)
                if body is not None:
                    say(f"/health/live at {ip}: {body.strip()}")
                    (self.out / "health-live.json").write_text(body, encoding="utf-8")
                    return body
            self.w.sleep(20)
        return None

    def cleanup(self):
        """Power off and delete the VM this run created -- only that one -- and read back that it is gone."""
        if not self.created:
            return True
        self.govc("vm.power", "-off", "-force", self.name)
        self.govc("vm.destroy", self.name)
        try:
            gone = not self.vm_exists()
        except NotRun:
            gone = False
        say(f"{self.name} deleted" if gone else f"CLEANUP FAILED: {self.name} is still on the ESXi host -- delete it by hand")
        return gone


def run(args, world):
    if not args.vm_name.startswith(VM_PREFIX):
        raise NotRun(f"the VM name must start with {VM_PREFIX!r} (this tool deletes the VM it made): {args.vm_name!r}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    expect = expected_version(args.iso, args.expect_version)
    lab = Lab(world, args, out)
    rc, text = lab.govc("about", timeout=60)
    if rc != 0:
        raise NotRun(f"govc cannot reach the ESXi host ({rc}): {text.strip()[-200:]}")
    if lab.vm_exists():
        raise NotRun(f"a VM named {args.vm_name} already exists -- not touching it")
    install_test = load_install_test()
    code = 1
    try:
        ds, iso_path = lab.iso_on_datastore()
        lab.create_vm(ds, iso_path)
        mac = lab.power_on_and_mac()
        ans = esxi_answers(mac, install_test.answers_text)
        script = gui_script(ans)
        with open(os.open(out / "answers", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write("".join(f"{k}={v}\n" for k, v in ans.items()))
        lab.drive_gui(script)
        body = lab.wait_health(mac, args.timeout)
        code, msg = verdict(expect, body)
        say(msg)
        try:
            lab.shot("final")
        except Failed as e:
            say(f"no final screenshot: {e}")
    except Failed as e:
        say(f"FAIL: {e}")
        try:
            lab.shot("failure")
        except Failed:
            pass
    finally:
        if not lab.cleanup():
            say("the verdict above stands, but the VM was left behind")
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1])
    ap.add_argument("--iso", required=True, help='a local ISO, or "[store] dir/file.iso" already on the datastore')
    ap.add_argument("--out", required=True, help="evidence directory (screenshots, answers, health-live.json)")
    ap.add_argument("--env", default=os.environ.get("ESXI_ENV"), help="esxi.env (only GOVC_* is read) or $ESXI_ENV")
    ap.add_argument("--govc", default=shutil.which("govc"), help="the govc binary (default: on PATH)")
    ap.add_argument("--vm-name", default=time.strftime(f"{VM_PREFIX}%m%d-%H%M"))
    ap.add_argument("--expect-version", help="default: from the ISO's file name")
    ap.add_argument("--datastore", default="store", help="where a local ISO is uploaded (default store)")
    ap.add_argument("--vm-datastore", default="vms", help="where the VM's disk lives (default vms)")
    ap.add_argument("--network", default="VM Network")
    ap.add_argument("--subnet", help="the subnet to find the server in (default: this machine's /24)")
    ap.add_argument("--ip", help="the server's address, when it is known (skips the search)")
    ap.add_argument("--timeout", type=int, default=3000, help="seconds from 'start install' to /health/live (default 50 min)")
    ap.add_argument("--gui-timeout", type=int, default=300, help="seconds for the installer's first screen")
    ap.add_argument("--mem-mb", type=int, default=8192)
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--disk-gb", type=int, default=40)
    args = ap.parse_args()
    try:
        if not args.govc or not os.path.exists(args.govc):
            raise NotRun(f"no govc ({args.govc!r}): pass --govc")
        env = load_govc_env(args.env)
        world = World(Govc(args.govc, env), args.subnet)
        if not args.ip and not args.subnet:
            url = env["GOVC_URL"]
            world.subnet = local_subnet(urllib.parse.urlparse(url if "://" in url else "https://" + url).hostname)
        return run(args, world)
    except NotRun as e:
        say(f"NOT RUN: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
