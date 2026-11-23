"""#4029: tools/iso/esxi-install-test.py -- the ISO's install on ESXi, to /health/live.

The tool is a script around govc, so govc is a fake here: it keeps a VM and a GUI (nine rail steps,
Enter moves on, the screenshots are real PNGs with the rail's circles) and records every call.
What is checked is the decision the tool makes, not ESXi:

* exit 0 only when /health/live was READ and its version equals the ISO's -- a version that is
  different, null, an answer that never came, or a body that is not the JSON, is exit 1;
* it deletes the VM it created and nothing else, and never touches a VM it did not create;
* a screen that did not advance is a failure after three presses, not a wait for the timeout;
* only the GOVC_* lines of esxi.env are read.
"""

import argparse
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("esxi_install_test", REPO / "tools" / "iso" / "esxi-install-test.py")
esxi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(esxi)

MAC = "00:0c:29:aa:bb:cc"
OK_BODY = '{"ok": true, "version": "v0.99.0", "version_error": null}'


# --- fakes -----------------------------------------------------------------------------------

def png(rail, salt=0):
    """A 1280x768 RGB PNG whose rail circles read as `rail` (done = green, current = blue, pending = dark)."""
    w, h = 1280, 768
    colour = {"blue": esxi.RAIL_BLUE, "green": (42, 115, 0), "dark": (37, 51, 61)}
    raw = bytearray()
    for y in range(h):
        row = bytearray(b"\xff" * (w * 3))
        for i in range(esxi.RAIL_STEPS):
            if y == esxi.RAIL_Y0 + esxi.RAIL_DY * i:
                kind = "blue" if rail == i + 1 else ("green" if rail and i + 1 < rail else "dark")
                row[esxi.RAIL_X * 3:esxi.RAIL_X * 3 + 3] = bytes(colour[kind])
        if y == 0:
            row[0] = salt
        raw += b"\x00" + row

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 1)) + chunk(b"IEND", b""))


# the GUI the fake plays: (name, rail step, salt). Enter moves to the next entry.
SCREENS = [("boot", None, 0), ("disk", 1, 0), ("role", 2, 0), ("network", 3, 0), ("hostname", 4, 0),
           ("time", 5, 0), ("backup", 6, 0), ("admin", 7, 0), ("summary", 8, 0), ("progress", 8, 1), ("done", 9, 0)]


class FakeGovc:
    def __init__(self, exists=False, stuck=(), destroy_fails=False, iso_size=0, boot_polls=2):
        self.calls, self.keys, self.vm, self.exists = [], [], None, exists
        self.stuck, self.destroy_fails, self.iso_size, self.boot_polls = set(stuck), destroy_fails, iso_size, boot_polls
        self.screen, self.enters = 0, {}

    def names(self, verb):
        return [c[-1] for c in self.calls if c[0] == verb]

    def present(self, name):
        return self.vm == name or (self.exists and name == "eit-x")

    def run(self, *args, timeout=300):
        self.calls.append(args)
        verb = args[0]
        if verb == "about":
            return 0, "FullName: VMware ESXi 8.0.3"
        if verb == "vm.info":
            return 0, f"Name:           {args[1]}\n  Power state: poweredOn\n" if self.present(args[1]) else ""
        if verb == "datastore.ls":
            return 0, json.dumps([{"file": [{"path": "x.iso", "fileSize": self.iso_size}]}]) if self.iso_size else (1, "not found")
        if verb == "datastore.upload":
            self.iso_size = Path(args[3]).stat().st_size
            return 0, ""
        if verb == "vm.create":
            self.vm = args[-1]
            return 0, ""
        if verb == "device.info":
            return 0, f"Name: ethernet-0\n  MAC Address:      {MAC}\n"
        if verb == "vm.keystrokes":
            code = args[-1]
            self.keys.append(code)
            name = SCREENS[self.screen][0]
            if code == "KEY_ENTER":
                self.enters[name] = self.enters.get(name, 0) + 1
                if name not in self.stuck and self.screen < len(SCREENS) - 1:
                    self.screen += 1
            return 0, ""
        if verb == "vm.console":
            if self.screen == 0 and self.boot_polls > 0:
                self.boot_polls -= 1                       # the GUI is not up yet: no blue circle
            elif self.screen == 0:
                self.screen = 1
            _name, rail, salt = SCREENS[self.screen]
            Path(args[2]).write_bytes(png(rail, salt))
            return 0, ""
        if verb == "vm.destroy":
            if not self.destroy_fails:
                self.vm = None
            return 0, ""
        return 0, ""                                        # device.boot, vm.power


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


def world_for(govc, body=OK_BODY, bodies=None):
    w = esxi.World(govc, subnet="192.168.68.0/24")
    clock = Clock()
    w.sleep, w.now, w.touch = clock.sleep, clock.now, lambda cidr: None
    w.neighbors = lambda: f"  192.168.68.55    {MAC.replace(':', '-')}   dynamic\n"
    seen = iter(bodies) if bodies is not None else None
    w.fetch = (lambda ip: next(seen, None)) if seen is not None else (lambda ip: body)
    return w


def args_for(tmp_path, **kw):
    base = {"iso": "[store] pub-v0.99.0/imagesrv-v0.99.0-amd64.iso", "out": str(tmp_path / "out"), "vm_name": "eit-x",
            "expect_version": None, "datastore": "store", "vm_datastore": "vms", "network": "VM Network",
            "subnet": None, "ip": None, "timeout": 600, "gui_timeout": 60, "mem_mb": 8192, "cpus": 4, "disk_gb": 40}
    base.update(kw)
    return argparse.Namespace(**base)


def run_it(tmp_path, govc=None, body=OK_BODY, bodies=None, **kw):
    govc = govc or FakeGovc(iso_size=1633288192)
    return esxi.run(args_for(tmp_path, **kw), world_for(govc, body, bodies)), govc


# --- the exit code: what counts as a pass --------------------------------------------------

@pytest.mark.parametrize("body,code", [
    (OK_BODY, 0),
    ('{"ok": true, "version": "0.99.0"}', 0),                                              # v prefix is not a difference
    ('{"ok": true, "version": "v0.98.1", "version_error": null}', 1),                     # another release
    ('{"ok": true, "version": null, "version_error": "no-git"}', 1),                       # not read is not a pass
    ('{"ok": false, "version": "v0.99.0"}', 1),
    ('{"ok": true}', 1),
    ('{"ok": true, "version": ""}', 1),
    ('{"ok": true, "version": 99}', 1),
    ("<html>502</html>", 1),
    ("", 1),
    (None, 1),                                                                             # it never answered
])
def test_only_a_version_that_was_read_and_matches_passes(body, code):
    got, message = esxi.verdict("0.99.0", body)
    assert got == code, message
    assert message.startswith("PASS") == (code == 0)


def test_the_version_comes_from_the_iso_name_or_the_run_is_not_run():
    assert esxi.expected_version("/x/imagesrv-v0.99.0-amd64.iso") == "0.99.0"
    assert esxi.expected_version("[store] pub/imagesrv-v0.100.2-rc1-amd64.iso") == "0.100.2-rc1"
    assert esxi.expected_version("whatever.iso", "v1.2.3") == "1.2.3"
    with pytest.raises(esxi.NotRun):
        esxi.expected_version("debian-13-netinst.iso")


def test_esxi_env_gives_only_the_govc_lines(tmp_path):
    env = tmp_path / "esxi.env"
    env.write_text("# lab\nGOVC_URL=https://192.0.2.9\r\nGOVC_USERNAME=root\nGOVC_PASSWORD=\"pw1\"\nGOVC_INSECURE=1\n"
                   "SERVER_TA_PASS=topsecret\nSERVER_TA_ROOT=alsosecret\n", encoding="utf-8")
    got = esxi.load_govc_env(str(env))
    assert got == {"GOVC_URL": "https://192.0.2.9", "GOVC_USERNAME": "root", "GOVC_PASSWORD": "pw1", "GOVC_INSECURE": "1"}
    assert "topsecret" not in json.dumps(got)
    env.write_text("GOVC_URL=https://192.0.2.9\n", encoding="utf-8")
    with pytest.raises(esxi.NotRun):
        esxi.load_govc_env(str(env))
    with pytest.raises(esxi.NotRun):
        esxi.load_govc_env(str(tmp_path / "missing.env"))


def test_the_rail_step_is_read_from_the_blue_circle():
    for step in range(1, 10):
        assert esxi.rail_step(png(step)) == step
    assert esxi.rail_step(png(None)) is None                 # no circle is blue: not the GUI
    assert esxi.rail_step(b"not a png") is None
    assert esxi.rail_step(png(3)[:-40]) is None              # a truncated file is "not read", not a step


def test_the_neighbour_tables_of_windows_and_linux_both_give_the_address():
    win = "  192.168.68.55         00-0c-29-aa-bb-cc     dynamic\n  192.168.68.1          11-22-33-44-55-66     dynamic\n"
    nix = "192.168.68.77 dev eth0 lladdr 00:0C:29:AA:BB:CC REACHABLE\n? (192.168.68.9) at 00:0c:29:aa:bb:cc [ether] on eth0\n"
    assert esxi.ips_from_neighbors(win, MAC) == ["192.168.68.55"]
    assert esxi.ips_from_neighbors(nix, MAC) == ["192.168.68.77", "192.168.68.9"]
    assert esxi.ips_from_neighbors(win, "00:50:56:00:00:01") == []


def test_a_password_the_keyboard_cannot_type_is_refused_before_a_key_is_pressed():
    ans = {"role": "standalone", "servers_mode": "dhcp", "timezone": "Asia/Jerusalem", "hostname": "isotest",
           "admin_user": "admin", "admin_pass": "Has-Capital", "root_pass": "ok-1"}
    with pytest.raises(esxi.NotRun):
        esxi.gui_script(ans)
    ans.update(admin_pass="fine-1", role="secondary")
    with pytest.raises(esxi.NotRun):                          # install-test's answers drifted from the GUI's defaults
        esxi.gui_script(ans)


# --- the run, against the fake govc --------------------------------------------------------

def test_a_matching_version_exits_0_quotes_health_and_deletes_only_its_vm(tmp_path, capsys):
    code, govc = run_it(tmp_path)
    out = capsys.readouterr().out
    assert code == 0, out
    assert OK_BODY in out and "PASS" in out                   # the quote of /health/live
    assert (tmp_path / "out" / "health-live.json").read_text(encoding="utf-8") == OK_BODY
    assert govc.names("vm.destroy") == ["eit-x"] and govc.vm is None
    # no VM name other than ours appears in any call that acts on a VM
    acting = [c for c in govc.calls if c[0] in ("vm.create", "vm.power", "vm.destroy", "vm.console", "device.boot", "device.info")]
    assert acting and all("eit-x" in c for c in acting)
    assert govc.calls.index(("vm.destroy", "eit-x")) == len(govc.calls) - 2   # the last act, then the read-back
    assert govc.calls[-1][0] == "vm.info"
    assert any(f.name.endswith("final.png") for f in (tmp_path / "out").iterdir())


def test_the_gui_is_driven_through_every_screen_with_the_saved_answers(tmp_path):
    code, govc = run_it(tmp_path)
    assert code == 0
    answers = dict(line.split("=", 1) for line in (tmp_path / "out" / "answers").read_text(encoding="utf-8").splitlines())
    assert answers["disk"] == "/dev/sda" and answers["servers_mac"] == MAC and answers["hostname"] == "isotest"
    typed = "".join({"KEY_MINUS": "-", **{f"KEY_{d}": str(d) for d in range(10)}}.get(k, k[-1].lower())
                    for k in govc.keys if k not in ("KEY_ENTER", "KEY_TAB", "KEY_SPACE"))
    assert typed == (answers["hostname"] + answers["admin_user"] + answers["admin_pass"] * 2 + answers["root_pass"] * 2)
    assert govc.enters == {"disk": 1, "role": 1, "network": 1, "hostname": 1, "time": 1, "backup": 1, "admin": 1, "summary": 1}
    assert all(c[:4] == ("vm.keystrokes", "-vm", "eit-x", "-c") and "," not in c[4] for c in govc.calls if c[0] == "vm.keystrokes")


def test_the_vm_boots_the_disk_first_so_the_reboot_lands_on_the_installed_system(tmp_path):
    _code, govc = run_it(tmp_path)
    create = next(c for c in govc.calls if c[0] == "vm.create")
    assert "-firmware" in create and create[create.index("-firmware") + 1] == "efi"
    assert create[create.index("-iso") + 1] == "pub-v0.99.0/imagesrv-v0.99.0-amd64.iso" and "-on=false" in create
    boot = next(c for c in govc.calls if c[0] == "device.boot")
    assert boot[boot.index("-order") + 1] == "disk,cdrom"
    verbs = [c[0] for c in govc.calls]
    assert verbs.index("vm.create") < verbs.index("device.boot") < verbs.index("vm.power") < verbs.index("vm.keystrokes")


@pytest.mark.parametrize("body", ['{"ok": true, "version": "v0.98.0", "version_error": null}',
                                  '{"ok": true, "version": null, "version_error": "no-git"}', "garbage"])
def test_a_wrong_or_unread_version_exits_1_and_still_deletes_the_vm(tmp_path, body, capsys):
    code, govc = run_it(tmp_path, body=body)
    assert code == 1, capsys.readouterr().out
    assert govc.names("vm.destroy") == ["eit-x"]


def test_health_that_never_answers_exits_1_at_the_ceiling_and_deletes_the_vm(tmp_path, capsys):
    code, govc = run_it(tmp_path, bodies=[], timeout=300)
    out = capsys.readouterr().out
    assert code == 1 and "never answered" in out
    assert govc.names("vm.destroy") == ["eit-x"]


def test_an_answer_after_some_silence_still_passes(tmp_path):
    code, _govc = run_it(tmp_path, bodies=[None, None, OK_BODY])
    assert code == 0


def test_a_screen_that_does_not_advance_fails_after_three_presses_and_deletes_the_vm(tmp_path, capsys):
    govc = FakeGovc(iso_size=1633288192, stuck={"hostname"})
    code, govc = run_it(tmp_path, govc=govc)
    out = capsys.readouterr().out
    assert code == 1 and "hostname" in out and "did not advance" in out
    assert govc.enters["hostname"] == 3 and "time" not in govc.enters      # it did not carry on into the next screen
    assert govc.names("vm.destroy") == ["eit-x"]
    assert any(f.name.endswith("failure.png") for f in (tmp_path / "out").iterdir())


def test_a_gui_that_never_shows_its_first_screen_fails_without_pressing_a_key(tmp_path, capsys):
    govc = FakeGovc(iso_size=1633288192, boot_polls=10 ** 6)
    code, govc = run_it(tmp_path, govc=govc, gui_timeout=30)
    assert code == 1 and "never showed step 1" in capsys.readouterr().out
    assert govc.keys == [] and govc.names("vm.destroy") == ["eit-x"]


def test_a_vm_that_already_exists_is_never_touched(tmp_path):
    govc = FakeGovc(exists=True, iso_size=1633288192)
    with pytest.raises(esxi.NotRun):
        run_it(tmp_path, govc=govc)
    assert [c[0] for c in govc.calls if c[0] in ("vm.create", "vm.power", "vm.destroy", "vm.keystrokes", "device.boot")] == []


def test_a_name_without_the_eit_prefix_is_refused_before_govc_is_asked_anything(tmp_path):
    govc = FakeGovc(iso_size=1633288192)
    with pytest.raises(esxi.NotRun):
        run_it(tmp_path, govc=govc, vm_name="ImageCtl-testrunner")
    assert govc.calls == []


def test_an_iso_that_is_not_on_the_datastore_is_not_run_and_creates_nothing(tmp_path):
    govc = FakeGovc(iso_size=0)
    with pytest.raises(esxi.NotRun):
        run_it(tmp_path, govc=govc)
    assert govc.names("vm.create") == [] and govc.names("vm.destroy") == []


def test_a_local_iso_is_uploaded_once_and_not_again_when_the_size_matches(tmp_path):
    iso = tmp_path / "imagesrv-v0.99.0-amd64.iso"
    iso.write_bytes(b"x" * 4096)
    code, govc = run_it(tmp_path, govc=FakeGovc(iso_size=0), iso=str(iso))
    assert code == 0 and [c[3] for c in govc.calls if c[0] == "datastore.upload"] == [str(iso)]
    code, govc = run_it(tmp_path, govc=FakeGovc(iso_size=4096), iso=str(iso))
    assert code == 0 and not [c for c in govc.calls if c[0] == "datastore.upload"]
    code, govc = run_it(tmp_path, govc=FakeGovc(iso_size=1234), iso=str(iso))       # a half-uploaded copy is not "there"
    assert code == 0 and [c for c in govc.calls if c[0] == "datastore.upload"]


def test_a_vm_left_behind_is_said_loudly_and_the_verdict_stands(tmp_path, capsys):
    code, govc = run_it(tmp_path, govc=FakeGovc(iso_size=1633288192, destroy_fails=True))
    out = capsys.readouterr().out
    assert code == 0 and "CLEANUP FAILED" in out and "eit-x" in out
    assert govc.vm == "eit-x"
