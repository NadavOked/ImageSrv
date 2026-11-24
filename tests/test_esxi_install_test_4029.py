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


# --- the failure paths: every one of them is exit 1 (ran, did not work) or 2 (not run), and the VM it made is gone ----

class FailingGovc(FakeGovc):
    """The fake GUI, but a verb can fail: fail={verb: (rc, output, after)} -- `after` calls of it still go through."""

    def __init__(self, fail, **kw):
        super().__init__(iso_size=kw.pop("iso_size", 1633288192), **kw)
        self.fail, self.seen = fail, {}

    def run(self, *args, timeout=300):
        verb = args[0]
        self.seen[verb] = self.seen.get(verb, 0) + 1
        if verb in self.fail and self.seen[verb] > self.fail[verb][2]:
            self.calls.append(args)
            return self.fail[verb][0], self.fail[verb][1]
        return super().run(*args, timeout=timeout)


def fails(verb, rc=1, out="boom", after=0):
    return {verb: (rc, out, after)}


@pytest.mark.parametrize("verb,text,acted_before", [
    ("vm.create", "vm.create failed", ()),                       # it may have half-made the VM: the name is still cleaned up
    ("device.boot", "device.boot failed", ("vm.create",)),
    ("vm.power", "power on failed", ("vm.create", "device.boot")),
])
def test_a_govc_step_that_fails_is_exit_1_and_the_vm_is_still_deleted(tmp_path, capsys, verb, text, acted_before):
    code, govc = run_it(tmp_path, govc=FailingGovc(fails(verb)))
    out = capsys.readouterr().out
    assert code == 1 and text in out and "boom" in out, out
    assert govc.names("vm.destroy") == ["eit-x"]                  # created=True is set before vm.create's answer is read
    assert govc.keys == []                                        # no key was pressed into a VM that did not get this far
    assert all(a in [c[0] for c in govc.calls] for a in acted_before)


def test_a_nic_without_a_mac_is_exit_1_after_ten_tries_not_a_hang(tmp_path, capsys):
    code, govc = run_it(tmp_path, govc=FailingGovc(fails("device.info", rc=0, out="Name: ethernet-0\n")))
    assert code == 1 and "no MAC address" in capsys.readouterr().out
    assert govc.names("device.info") == ["ethernet-0"] * 10 and govc.keys == []
    assert govc.names("vm.destroy") == ["eit-x"]


def test_a_key_that_govc_refuses_is_exit_1_and_is_not_retried_silently(tmp_path, capsys):
    code, govc = run_it(tmp_path, govc=FailingGovc(fails("vm.keystrokes", out="no such key")))
    assert code == 1 and "keystroke failed (1): no such key" in capsys.readouterr().out
    assert len(govc.names("vm.keystrokes")) == 1 and govc.names("vm.destroy") == ["eit-x"]


@pytest.mark.parametrize("rc,wrote", [(1, True), (0, False)])
def test_no_screenshot_is_a_failure_not_a_blank_screen_read_as_a_step(tmp_path, capsys, rc, wrote):
    """govc exiting 0 without writing the file is not a screenshot either; and the `failure` shot that follows
    fails too, which must not hide the verdict or skip the deletion."""
    class Blind(FailingGovc):
        def run(self, *args, timeout=300):
            if args[0] == "vm.console":
                self.calls.append(args)
                if wrote:
                    Path(args[2]).write_bytes(png(1))
                return rc, "console is gone"
            return super().run(*args, timeout=timeout)
    code, govc = run_it(tmp_path, govc=Blind({}))
    out = capsys.readouterr().out
    assert code == 1 and "no console screenshot" in out, out
    assert govc.names("vm.destroy") == ["eit-x"]


@pytest.mark.parametrize("rc,expected_polls,error", [
    (0, [0, 5], None),
    (0, [0, 5, 10], "never showed step 1 within 10s"),
    (1, [0], r"no console screenshot \(1\)"),
])
def test_wait_step_with_a_1x1_screenshot(tmp_path, rc, expected_polls, error):
    tiny = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
        "0000000c49444154789c63606060000000040001f61738550000000049454e44ae426082"
    )
    clock = Clock()
    polls = []

    def capture(*args, timeout):
        polls.append(clock.now())
        Path(args[2]).write_bytes(png(1) if error is None and len(polls) > 1 else tiny)
        return rc, ""

    w = esxi.World(FakeGovc())
    w.govc.run = capture
    w.sleep, w.now = clock.sleep, clock.now
    lab = esxi.Lab(w, args_for(tmp_path), tmp_path)
    if error is None:
        lab.wait_step(1, "boot", 10)
    else:
        with pytest.raises(esxi.Failed, match=error):
            lab.wait_step(1, "boot", 10)
    assert polls == expected_polls


def test_a_final_screenshot_that_fails_does_not_change_the_verdict(tmp_path, capsys):
    govc = FailingGovc({})
    base_run = govc.run

    def run(*args, timeout=300):
        if args[0] == "vm.console" and getattr(govc, "health_read", False):
            govc.calls.append(args)
            return 1, "gone"
        return base_run(*args, timeout=timeout)
    govc.run = run
    w = world_for(govc)

    def fetch(ip):
        govc.health_read = True
        return OK_BODY
    w.fetch = fetch
    code = esxi.run(args_for(tmp_path), w)
    out = capsys.readouterr().out
    assert code == 0 and "PASS" in out and "no final screenshot" in out
    assert govc.names("vm.destroy") == ["eit-x"]


def test_a_vm_that_cannot_be_read_back_after_the_delete_is_reported_as_left_behind(tmp_path, capsys):
    # the first vm.info (is the name free?) answers; the read-back after vm.destroy does not: "not known" is not "gone"
    code, govc = run_it(tmp_path, govc=FailingGovc(fails("vm.info", out="session expired", after=1)))
    out = capsys.readouterr().out
    assert code == 0 and "CLEANUP FAILED" in out and "eit-x" in out and "eit-x deleted" not in out


def test_an_esxi_host_that_does_not_answer_is_not_run_and_nothing_is_created(tmp_path):
    govc = FailingGovc(fails("about", rc=124, out="govc about timed out after 60s"))
    with pytest.raises(esxi.NotRun, match="cannot reach the ESXi host"):
        run_it(tmp_path, govc=govc)
    assert govc.names("vm.create") == [] and govc.names("vm.destroy") == []


def test_a_vm_name_that_cannot_be_looked_up_is_not_run_rather_than_assumed_free(tmp_path):
    govc = FailingGovc(fails("vm.info", out="permission denied"))
    with pytest.raises(esxi.NotRun, match="vm.info eit-x failed"):
        run_it(tmp_path, govc=govc)
    assert govc.names("vm.create") == []


def test_a_local_iso_that_is_not_there_is_not_run(tmp_path):
    govc = FakeGovc(iso_size=1)
    with pytest.raises(esxi.NotRun, match="no ISO"):
        run_it(tmp_path, govc=govc, iso=str(tmp_path / "imagesrv-v0.99.0-amd64.iso"))
    assert govc.names("vm.create") == [] and govc.names("datastore.upload") == []


@pytest.mark.parametrize("upload", [(1, "disk full"), (0, "")])      # govc failed / govc says ok but the size is not there
def test_an_upload_that_did_not_arrive_whole_is_not_run_and_creates_nothing(tmp_path, upload):
    iso = tmp_path / "imagesrv-v0.99.0-amd64.iso"
    iso.write_bytes(b"x" * 4096)
    govc = FailingGovc({"datastore.upload": (*upload, 0)}, iso_size=0)
    with pytest.raises(esxi.NotRun, match="upload to .* failed or came back a different size"):
        run_it(tmp_path, govc=govc, iso=str(iso))
    assert govc.names("vm.create") == [] and govc.names("vm.destroy") == []


class Scripted:
    def __init__(self, rc, out):
        self.rc, self.out = rc, out

    def run(self, *args, timeout=300):
        return self.rc, self.out


@pytest.mark.parametrize("rc,out,size", [
    (0, json.dumps([{"file": [{"fileSize": "1633288192"}]}]), 1633288192),
    (1, "File [store] iso/x.iso was not found", 0),                        # govc says no: size 0, not an exception
    (0, "not json", 0), (0, "[]", 0), (0, '[{"file": []}]', 0), (0, '[{"file": [{"path": "x"}]}]', 0), (0, "null", 0),
])
def test_the_remote_size_is_read_or_it_is_zero_never_a_guess(tmp_path, rc, out, size):
    lab = esxi.Lab(esxi.World(Scripted(rc, out)), args_for(tmp_path), tmp_path)
    assert lab.remote_size("store", "iso/x.iso") == size


# --- the pieces that touch the machine: tested without the machine ---------------------------------

def test_govc_runs_with_the_lab_env_without_echoing_args_and_a_hung_one_is_rc_124(monkeypatch):
    import sys
    g = esxi.Govc(sys.executable, {"GOVC_URL": "https://esxi.example"})
    rc, out = g.run("-c", "import os,sys;print(os.environ['GOVC_URL']);sys.stdin.read();sys.exit(3)")
    assert (rc, out.strip()) == (3, "https://esxi.example")          # stdin is closed: a prompt cannot hang the tool

    def hung(*a, **kw):
        raise esxi.subprocess.TimeoutExpired(a[0], kw["timeout"])
    monkeypatch.setattr(esxi.subprocess, "run", hung)
    rc, out = g.run("vm.keystrokes", "-c", "KEY_SECRET", timeout=7)
    assert rc == 124 and "vm.keystrokes timed out after 7s" in out and "KEY_SECRET" not in out


def test_the_health_body_is_returned_only_for_a_200_and_a_failure_is_none(monkeypatch):
    seen = {}

    class Resp:
        def __init__(self, status, body=b'{"ok": true}'):
            self.status, self.body = status, body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    def make(answer):
        def urlopen(url, timeout, context):
            seen.update(url=url, ctx=context, timeout=timeout)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return urlopen
    monkeypatch.setattr(esxi.urllib.request, "urlopen", make(Resp(200)))
    assert esxi.fetch_health("10.1.2.3") == '{"ok": true}'
    assert seen["url"] == "https://10.1.2.3/api/console/health/live" and seen["ctx"].check_hostname is False   # self-signed
    monkeypatch.setattr(esxi.urllib.request, "urlopen", make(Resp(503, b"<html>")))
    assert esxi.fetch_health("10.1.2.3") is None                      # a body that came with a 503 is not an answer
    for err in (ConnectionRefusedError(), TimeoutError(), OSError("no route"), ValueError("bad url")):
        monkeypatch.setattr(esxi.urllib.request, "urlopen", make(err))
        assert esxi.fetch_health("10.1.2.3") is None


def test_the_neighbour_table_is_asked_with_the_command_the_os_has(monkeypatch):
    from types import SimpleNamespace
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return SimpleNamespace(stdout=f"table of {' '.join(cmd)}")
    monkeypatch.setattr(esxi.subprocess, "run", fake_run)
    monkeypatch.setattr(esxi, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(esxi, "shutil", SimpleNamespace(which=lambda c: c))
    assert esxi.read_neighbors() == "table of arp -a" and ran == [["arp", "-a"]]
    ran.clear()
    monkeypatch.setattr(esxi, "os", SimpleNamespace(name="posix"))
    assert esxi.read_neighbors() == "table of ip neigh"                # `ip` first
    monkeypatch.setattr(esxi, "shutil", SimpleNamespace(which=lambda c: c if c == "arp" else None))
    assert esxi.read_neighbors() == "table of arp -an"                 # a box without iproute2
    monkeypatch.setattr(esxi, "shutil", SimpleNamespace(which=lambda c: None))
    ran.clear()
    assert esxi.read_neighbors() == "" and ran == []                   # no tool: an empty table, which finds no address


def test_every_host_of_the_subnet_is_touched_on_443_and_a_dead_one_is_not_an_error(monkeypatch):
    touched, closed = [], []

    class Sock:
        def __init__(self, host):
            self.host = host

        def close(self):
            closed.append(self.host)

    def connect(addr, timeout):
        touched.append((addr, timeout))
        if addr[0].endswith(".2"):
            raise ConnectionRefusedError()                             # an OSError: nobody there, or a closed port
        return Sock(addr[0])
    monkeypatch.setattr(esxi.socket, "create_connection", connect)
    esxi.touch_subnet("10.9.8.0/29")
    assert sorted(a for (a, _p), _t in touched) == [f"10.9.8.{i}" for i in range(1, 7)]
    assert all(p == 443 and t < 1 for (_a, p), t in touched)
    assert sorted(closed) == [f"10.9.8.{i}" for i in (1, 3, 4, 5, 6)]


def test_the_subnet_to_search_is_the_slash_24_of_the_address_on_the_way_to_the_host(monkeypatch):
    class FakeSocket:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, addr):
            self.addr = addr

        def getsockname(self):
            return ("192.168.68.123", 54321)
    monkeypatch.setattr(esxi.socket, "socket", FakeSocket)
    assert esxi.local_subnet("192.168.68.4") == "192.168.68.0/24"


def test_find_ips_touches_the_subnet_first_but_not_when_there_is_none():
    govc, touched = FakeGovc(), []
    w = world_for(govc)
    w.touch = touched.append
    assert w.find_ips(MAC) == ["192.168.68.55"] and touched == ["192.168.68.0/24"]
    w.subnet = None
    assert w.find_ips(MAC) == ["192.168.68.55"] and touched == ["192.168.68.0/24"]


# --- the PNG reader: govc's capture may use any of the five row filters ------------------------------

def _filtered_png(rows, kinds):
    """An 8-bit RGB PNG of `rows` (bytes of equal length) where row y is stored with filter kinds[y % len(kinds)]."""
    stride, prev, raw = len(rows[0]), bytes(len(rows[0])), bytearray()
    assert stride % 3 == 0
    for y, row in enumerate(rows):
        ft = kinds[y % len(kinds)]
        raw.append(ft)
        for i, x in enumerate(row):
            a = row[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            guess = (0, a, b, (a + b) >> 1, a if pa <= pb and pa <= pc else (b if pb <= pc else c))[ft]
            raw.append((x - guess) & 255)
        prev = row

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", stride // 3, len(rows), 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


@pytest.mark.parametrize("kinds", [[0], [1], [2], [3], [4], [4, 3, 2, 1, 0]])
def test_the_png_reader_undoes_each_row_filter_to_the_exact_pixels(kinds):
    import random
    rnd = random.Random(4029)
    rows = [bytes(rnd.randrange(256) for _ in range(7 * 3)) for _ in range(10)]
    width, got = esxi._png_rows(_filtered_png(rows, kinds), 9)
    assert width == 7 and [bytes(r) for r in got] == rows


def test_a_png_that_is_not_8_bit_rgb_is_refused_and_a_narrow_one_is_not_read_as_a_step():
    rows = [bytes(21)] * 3
    palette = bytearray(_filtered_png(rows, [0]))
    palette[8 + 8 + 9] = 3                                        # IHDR colour type 2 -> 3 (palette): not what govc writes
    with pytest.raises(ValueError, match="unsupported PNG header"):
        esxi._png_rows(bytes(palette), 2)
    assert esxi.rail_step(bytes(palette)) is None
    tall = esxi.RAIL_Y0 + esxi.RAIL_DY * (esxi.RAIL_STEPS - 1) + 1
    assert esxi.rail_step(_filtered_png([bytes(300)] * tall, [0])) is None    # 100 px wide: the rail is not in this picture


# --- main(): the command line, and what it does before any run -------------------------------------

def _cli(monkeypatch, tmp_path, *extra, env_file=True, govc=True):
    import sys
    envf = tmp_path / "esxi.env"
    envf.write_text("GOVC_URL=https://esxi.example:443\nGOVC_USERNAME=root\nGOVC_PASSWORD=pw\nOTHER=x\n", encoding="utf-8")
    argv = ["esxi-install-test.py", "--iso", "[store] pub/imagesrv-v0.99.0-amd64.iso", "--out", str(tmp_path / "o")]
    argv += ["--govc", sys.executable if govc else str(tmp_path / "no-govc")]
    if env_file:
        argv += ["--env", str(envf)]
    monkeypatch.setattr(sys, "argv", argv + list(extra))
    return envf


def test_main_without_a_govc_binary_is_not_run_exit_2(monkeypatch, tmp_path, capsys):
    _cli(monkeypatch, tmp_path, govc=False)
    assert esxi.main() == 2
    assert "NOT RUN: no govc" in capsys.readouterr().out


def test_main_without_an_env_file_is_not_run_exit_2_and_never_reaches_run(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ESXI_ENV", raising=False)
    _cli(monkeypatch, tmp_path, env_file=False)
    monkeypatch.setattr(esxi, "run", lambda *a: pytest.fail("run() must not be reached without an env"))
    assert esxi.main() == 2
    assert "NOT RUN: no esxi.env" in capsys.readouterr().out


def test_main_finds_the_subnet_toward_the_esxi_host_and_hands_run_the_govc_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ESXI_ENV", raising=False)
    _cli(monkeypatch, tmp_path)
    got = {}
    monkeypatch.setattr(esxi, "local_subnet", lambda host: got.setdefault("host", host) and "10.1.2.0/24")
    monkeypatch.setattr(esxi, "run", lambda args, world: got.update(args=args, world=world) or 0)
    assert esxi.main() == 0
    assert got["host"] == "esxi.example" and got["world"].subnet == "10.1.2.0/24"
    assert got["world"].govc.env["GOVC_URL"] == "https://esxi.example:443" and "OTHER" not in got["world"].govc.env
    assert got["args"].iso.startswith("[store]") and got["args"].vm_name.startswith("eit-") and got["args"].timeout == 3000


def test_main_reads_the_env_from_ESXI_ENV_and_a_url_without_a_scheme_still_has_a_host(monkeypatch, tmp_path):
    envf = _cli(monkeypatch, tmp_path, env_file=False)
    envf.write_text("GOVC_URL=192.0.2.9\nGOVC_USERNAME=root\nGOVC_PASSWORD=pw\n", encoding="utf-8")
    monkeypatch.setenv("ESXI_ENV", str(envf))
    hosts = []
    monkeypatch.setattr(esxi, "local_subnet", lambda host: hosts.append(host) or "192.0.2.0/24")
    monkeypatch.setattr(esxi, "run", lambda args, world: 0)
    assert esxi.main() == 0 and hosts == ["192.0.2.9"]


@pytest.mark.parametrize("extra,subnet", [(("--ip", "10.0.0.5"), None), (("--subnet", "10.7.7.0/24"), "10.7.7.0/24")])
def test_main_does_not_search_for_a_subnet_when_it_was_given_one_or_the_address(monkeypatch, tmp_path, extra, subnet):
    monkeypatch.delenv("ESXI_ENV", raising=False)
    _cli(monkeypatch, tmp_path, *extra)
    monkeypatch.setattr(esxi, "local_subnet", lambda host: pytest.fail("the subnet was asked for although it was given"))
    got = {}
    monkeypatch.setattr(esxi, "run", lambda args, world: got.update(world=world) or 0)
    assert esxi.main() == 0 and got["world"].subnet == subnet


def test_the_script_run_as_a_program_exits_with_mains_code(monkeypatch, tmp_path):
    import runpy
    _cli(monkeypatch, tmp_path, govc=False)
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(REPO / "tools" / "iso" / "esxi-install-test.py"), run_name="__main__")
    assert stop.value.code == 2
