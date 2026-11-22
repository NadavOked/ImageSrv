"""#2062 -- one stick clones and installs a server: "התקן שרת ImageSrv על המחשב הזה".

Nadav 23/10 and 12/11: a button in the stick's settings, and the packages on a partition
of the stick -- no internet. The stick built with --server-iso carries the server's ISO as
its third partition (IMAGESRV_INSTALL, iso9660) and the live installer in its initramfs;
the button closes the clone screen and execs that installer (agent/lib/usb/usbinstall.sh).

What these hold, all through the settings screen's own keys (usbset_key) and the stick's
main loop (usbclone_main) -- the two ways in that exist on the stick:

* the button erases nothing and asks for the installer only on the red button, with the
  focus starting on "ביטול" and no typed word (#1270);
* the medium is the stick's own partition, never a drawer that carries the same label:
  the engine protects the disk that holds /cdrom, so a medium taken from a drawer would
  offer the stick as a target (#2431: a label is not the stick);
* every piece of evidence is positive (principle 5): one partition, iso9660, mounted where
  /proc/mounts says so, imagesrv-iso.json with a version, the installer in the image --
  a check that could not be made opens nothing and says why;
* the agent checks again before it closes the clone screen, and a refusal there leaves the
  clone screen up with the reason on it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentlib import agent_order, agent_sources
from msys_guard import run_script_file
from native import requires_native
from shell_steps import run_steps
from test_cloner_gui import BASH, posix
from test_usb_clone import KIOSK, LIB, LIBS, REPO, frame_records

pytestmark = [requires_native(("bash", BASH)), requires_native(("jq", shutil.which("jq")))]

#: The stick's libraries in the agent's own order, with usbinstall where the agent sources
#: it. On a tree from before #2062 it is not there: these tests then fail on what the screen
#: says and on what was asked for -- not on a library that did not load.
STICK_LIBS = tuple(dict.fromkeys(n for n in (*LIBS, "usbinstall") if n in agent_order()))

TITLE = "התקן שרת ImageSrv על המחשב הזה"
ASK = "להתקין שרת ImageSrv על המחשב הזה?"
REFUSED = "אי אפשר להתקין שרת מהדיסק-און-קי הזה"
OPENING = "פותח את מתקין השרת…"
GO = "המשך להתקנת שרת"
TO_INSTALL = ["KEY DOWN"] * 7                      # the eighth item, after "החזר לברירת המחדל"
FSI, PDI = "\u2068", "\u2069"


def run(tmp_path: Path, script: str, *, steps: list[str] | None = None, timeout: int = 60,
        **env: str) -> subprocess.CompletedProcess:
    run_dir = tmp_path / "run"; run_dir.mkdir(exist_ok=True)
    pre = f'export RUN_DIR={posix(run_dir)!r} IMAGESRV_TEST=1 SMART_KB={posix(LIB)!r}/data; '
    pre += "".join(f'export {k}={v!r}; ' for k, v in env.items())
    pre += agent_sources(*STICK_LIBS)
    pre += 'sync() { :; }; '
    if steps is not None:
        return run_steps([BASH], ['export PATH="/usr/bin:$PATH"; ' + pre + script, *steps],
                         tmp_path, timeout=timeout, cwd=str(REPO))
    # from a file, not `bash -c`: the script is longer than Git for Windows' bash keeps whole (#893)
    return run_script_file(BASH, 'export PATH="/usr/bin:$PATH"; ' + pre + script, tmp_path / "agent-run.sh",
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=str(REPO))


class Stick:
    """A machine as the stick's kiosk sees it: /sys/block, blkid, mount and /proc/mounts.

    ``parts``: device -> (label, filesystem type). ``media``: device -> the files a
    read-only mount of it shows. The stick is sdb unless ``stick_disks`` says otherwise.
    """

    def __init__(self, tmp_path: Path, parts: dict[str, tuple[str, str]] | None = None, *,
                 media: dict[str, dict[str, str]] | None = None, stick_disks: str | None = "sdb\n",
                 internal: tuple[str, ...] = (), installer: bool = True, mount_rc: int = 0,
                 mount_records: bool = True, mounted: str | None = None) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "sysroot"
        self.parts = parts if parts is not None else {
            "sda1": ("Windows", "ntfs"), "sdb1": ("IMGSRV_USB", "vfat"), "sdb2": ("IMGSRV_DATA", "vfat"),
            "sdb3": ("IMAGESRV_INSTALL", "iso9660")}
        media = media if media is not None else {"sdb3": {"imagesrv-iso.json": '{"imagesrv_tag": "v0.93.0"}\n'}}
        for dev in self.parts:
            disk = dev.rstrip("0123456789")
            d = self.root / "sys" / "block" / disk
            (d / dev).mkdir(parents=True, exist_ok=True)
            (d / dev / "partition").write_text(dev[len(disk):] + "\n")
            (d / "removable").write_text("0\n" if disk in internal else "1\n")
            if disk in internal:
                (d / "device").mkdir(exist_ok=True)     # a device path on no USB bus (disk_internal)
        (self.root / "proc").mkdir(parents=True, exist_ok=True)
        self.mounts = self.root / "proc" / "mounts"
        self.mnt = tmp_path / "cdrom"
        self.mounts.write_text(f"{mounted} {posix(self.mnt)} iso9660 ro 0 0\n" if mounted else "")
        self.media = tmp_path / "media"
        for dev, files in media.items():
            for name, text in files.items():
                (self.media / dev).mkdir(parents=True, exist_ok=True)
                (self.media / dev / name).write_text(text, encoding="utf-8")
        run_dir = tmp_path / "run"; run_dir.mkdir(exist_ok=True)
        if stick_disks is not None:
            (run_dir / "usb-media.disks").write_text(stick_disks)
        self.bin = tmp_path / "imagesrv-installer"
        if installer:
            self.bin.write_text("#!/bin/sh\necho INSTALLER-RAN\n", newline="\n")
            self.bin.chmod(0o755)
        self.calls = run_dir / "mount.calls"
        self.usb = tmp_path / "usbdata"; self.usb.mkdir(exist_ok=True)
        self.mount_rc, self.mount_records = mount_rc, mount_records

    def stubs(self) -> str:
        cases = "".join(f'LABEL:{dev}) echo {lab} ;; TYPE:{dev}) echo {fs} ;; '
                        for dev, (lab, fs) in self.parts.items())
        record = (f'printf "%s %s iso9660 ro 0 0\\n" "$_src" "$_dst" >> {posix(self.mounts)!r}; '
                  if self.mount_records else "")
        return (
            f'blkid() {{ _q=$2; eval "_b=\\${{$#}}"; case "$_q:${{_b##*/}}" in {cases}*) return 2 ;; esac; }}; '
            'modprobe() { :; }; '
            # mount: the last two words are the device and the mount point; a mount that works
            # shows the medium's files and its line in /proc/mounts, as the kernel does
            f'mount() {{ echo "mount $*" >> {posix(self.calls)!r}; [ {self.mount_rc} = 0 ] || return {self.mount_rc}; '
            'eval "_dst=\\${$#}"; _n=$(($# - 1)); eval "_src=\\${$_n}"; '
            f'{record}'
            f'[ ! -d {posix(self.media)!r}/"${{_src##*/}}" ] || cp -r {posix(self.media)!r}/"${{_src##*/}}"/. "$_dst"/; }}; '
            f'umount() {{ echo "umount $*" >> {posix(self.calls)!r}; '
            f'[ "$1" = {posix(self.mnt)!r} ] || return 0; rm -rf {posix(self.mnt)!r}; : > {posix(self.mounts)!r}; }}; '
            f'USB_INSTALL_MNT={posix(self.mnt)!r}; USB_INSTALLER_BIN={posix(self.bin)!r}; '
            f'echo mounted > "$RUN_DIR/usb.data"; USB_DATA_DIR={posix(self.usb)!r}; '
        )

    def keys(self, keys: list[str]) -> subprocess.CompletedProcess:
        """usbset_open, the keys, the frame -- as the kiosk does on the settings screen."""
        steps = [*(f'usbset_key {k!r};' for k in keys), 'cat "$GUI_DIR/usbset.frame"']
        return run(self.tmp, KIOSK + self.stubs() + 'usbset_open; ', steps=steps,
                   IMAGESRV_MODE="usbclone", SYSROOT=posix(self.root))

    def asked(self) -> str | None:
        """The partition the kiosk asked the agent to install from, or None."""
        go = self.tmp / "run" / "lc" / "usbinstall.go"
        return go.read_text().strip() if go.exists() else None

    def mount_calls(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def mounted_now(self) -> str:
        return self.mounts.read_text()


def records(out: subprocess.CompletedProcess) -> list[list[str]]:
    return frame_records(out.stdout)


def dialog(out: subprocess.CompletedProcess) -> list[str] | None:
    return next((r for r in records(out) if r[0] == "dlg"), None)


def buttons(out: subprocess.CompletedProcess) -> list[list[str]]:
    return [r for r in records(out) if r[0] == "d.btn"]


# --- the item -------------------------------------------------------------------------------------

def test_the_item_is_the_last_of_the_settings_and_opens_nothing_by_itself(tmp_path):
    s = Stick(tmp_path)
    out = s.keys(TO_INSTALL + ["KEY DOWN"])                       # one more: there is no tenth
    items = [r for r in records(out) if r[0] == "item"]
    assert [r[2] for r in items][-2:] == ["החזר לברירת המחדל", TITLE], out.stdout + out.stderr
    assert len(items) == 8 and [r[4] for r in items] == ["0"] * 7 + ["1"], items
    assert next(r for r in records(out) if r[0] == "pane")[1] == TITLE
    assert ["prow", "חבילות ההתקנה", "", "על הדיסק-און-קי"] == next(r for r in records(out) if r[0] == "prow")[:4]
    assert dialog(out) is None and s.asked() is None
    assert s.mount_calls() == [], "looking at the item mounted something"


def test_enter_shows_the_version_read_from_the_stick_with_the_focus_on_cancel(tmp_path):
    s = Stick(tmp_path)
    out = s.keys(TO_INSTALL + ["KEY ENTER"])
    assert dialog(out)[:4] == ["dlg", "danger", "640", ASK], out.stdout + out.stderr
    assert ["d.kv", "גרסת השרת", f"{FSI}v0.93.0{PDI}", "0"] in records(out)
    assert buttons(out) == [["d.btn", "ביטול", "Esc", "plain", "1"], ["d.btn", GO, "", "danger", "0"]]
    assert not any(r[0] == "d.field" for r in records(out))        # no typed word (#1270)
    assert s.mount_calls() == [f"mount -t iso9660 -o ro {posix(s.root)}/dev/sdb3 {posix(s.mnt)}"]
    assert s.asked() is None                                       # opening the dialog asks for nothing


@pytest.mark.parametrize("keys", [["KEY ENTER"], ["KEY ESC"], ["KEY TAB", "KEY ESC"],
                                  ["KEY TAB", "KEY TAB", "KEY ENTER"], ["KEY RIGHT", "KEY LEFT", "KEY ENTER"]])
def test_anything_but_the_red_button_closes_the_dialog_and_asks_for_nothing(tmp_path, keys):
    s = Stick(tmp_path)
    out = s.keys(TO_INSTALL + ["KEY ENTER"] + keys)
    assert dialog(out) is None, out.stdout + out.stderr
    assert s.asked() is None and not (tmp_path / "run" / "lc" / "end.req").exists()
    assert s.mounted_now() == "", "the read-only mount was left behind"


def test_only_the_red_button_asks_the_agent_for_the_installer(tmp_path):
    s = Stick(tmp_path)
    out = s.keys(TO_INSTALL + ["KEY ENTER", "KEY TAB"])
    assert buttons(out)[1] == ["d.btn", GO, "", "danger", "1"] and s.asked() is None
    out = s.keys(TO_INSTALL + ["KEY ENTER", "KEY TAB", "KEY ENTER", "KEY ESC", "KEY ENTER"])
    assert s.asked() == "sdb3", out.stdout + out.stderr
    assert (tmp_path / "run" / "lc" / "end.req").read_text().strip() == "back"   # the local screen ends: the agent takes over
    assert dialog(out)[3] == OPENING and buttons(out) == [], out.stdout   # asked: no key undoes or repeats it
    assert s.mounted_now().split()[:2] == [f"{posix(s.root)}/dev/sdb3", posix(s.mnt)]   # kept for the agent, read-only


# --- what is not the stick's medium, and what could not be checked ------------------------------------

def refused(tmp_path: Path, **kw) -> tuple[Stick, subprocess.CompletedProcess]:
    s = Stick(tmp_path, **kw)
    out = s.keys(TO_INSTALL + ["KEY ENTER", "KEY TAB", "KEY ENTER"])   # the keys that would have installed
    return s, out


def assert_refused(s: Stick, out: subprocess.CompletedProcess, why: str) -> None:
    first = s.keys(TO_INSTALL + ["KEY ENTER"])
    assert dialog(first)[:4] == ["dlg", "plain", "640", REFUSED], first.stdout + first.stderr
    assert why in dialog(first)[4], dialog(first)
    assert [b[1] for b in buttons(first)] == ["סגירה"], buttons(first)   # nothing to go on with
    assert s.asked() is None and not (s.tmp / "run" / "lc" / "end.req").exists(), out.stdout + out.stderr


def test_a_drawer_that_carries_the_install_label_is_not_the_sticks_medium(tmp_path):
    # the stick has no install partition; a drawer has a whole server ISO written on it
    s, out = refused(tmp_path, parts={"sda1": ("IMAGESRV_INSTALL", "iso9660"), "sdb1": ("IMGSRV_USB", "vfat"),
                                      "sdb2": ("IMGSRV_DATA", "vfat")},
                     media={"sda1": {"imagesrv-iso.json": '{"imagesrv_tag": "v0.93.0"}\n'}})
    assert_refused(s, out, "נבנה בלי חבילות ההתקנה")
    assert s.mount_calls() == [], "a drawer was mounted as the install medium"


def test_a_stick_that_is_an_internal_disk_is_not_a_stick(tmp_path):
    s, out = refused(tmp_path, internal=("sdb",))
    assert_refused(s, out, "נבנה בלי חבילות ההתקנה")
    assert s.mount_calls() == []


def test_two_install_partitions_on_the_stick_are_not_one(tmp_path):
    parts = {"sdb1": ("IMGSRV_USB", "vfat"), "sdb2": ("IMGSRV_DATA", "vfat"),
             "sdb3": ("IMAGESRV_INSTALL", "iso9660"), "sdb4": ("IMAGESRV_INSTALL", "iso9660")}
    s, out = refused(tmp_path, parts=parts)
    assert_refused(s, out, "יותר ממחיצת התקנה אחת (sdb3 sdb4)")
    assert s.mount_calls() == []


def test_a_partition_that_is_not_iso9660_is_not_the_medium(tmp_path):
    s, out = refused(tmp_path, parts={"sdb1": ("IMGSRV_USB", "vfat"), "sdb2": ("IMGSRV_DATA", "vfat"),
                                      "sdb3": ("IMAGESRV_INSTALL", "vfat")})
    assert_refused(s, out, "אינה iso9660 (vfat)")
    assert s.mount_calls() == []


def test_the_stick_that_was_never_identified_installs_nothing(tmp_path):
    s, out = refused(tmp_path, stick_disks=None)
    assert_refused(s, out, "לא זוהה")


def test_a_mount_that_fails_is_said(tmp_path):
    s, out = refused(tmp_path, mount_rc=32)
    assert_refused(s, out, "לא נקראה מהדיסק-און-קי")


def test_a_mount_that_reports_success_and_is_not_in_proc_mounts_is_not_a_mount(tmp_path):
    s, out = refused(tmp_path, mount_records=False)      # exit code 0, and the kernel knows of nothing
    assert_refused(s, out, "מעוגן שום דבר ולא מחיצת ההתקנה של הדיסק-און-קי (sdb3)")


def test_another_device_on_the_mount_point_is_not_the_sticks_medium(tmp_path):
    s, out = refused(tmp_path, mounted="/dev/sr0")
    assert_refused(s, out, "מעוגן /dev/sr0 ולא מחיצת ההתקנה")
    assert s.mount_calls() == [], "mounted over, or unmounted, a medium that is not ours"
    assert s.mounted_now().startswith("/dev/sr0 ")


def test_a_partition_without_the_manifest_is_not_an_imagesrv_medium(tmp_path):
    s, out = refused(tmp_path, media={"sdb3": {"README": "a Debian netinst\n"}})
    assert_refused(s, out, "אין imagesrv-iso.json")


@pytest.mark.parametrize("manifest", ['{"netinst": "debian-13.7.0"}\n', '{"imagesrv_tag": "v1 && reboot"}\n', "not json\n"])
def test_a_version_that_cannot_be_read_is_not_a_version(tmp_path, manifest):
    s, out = refused(tmp_path, media={"sdb3": {"imagesrv-iso.json": manifest}})
    assert_refused(s, out, "גרסת השרת לא נקראה")


def test_a_stick_whose_initramfs_has_no_installer_says_so(tmp_path):
    s, out = refused(tmp_path, installer=False)
    assert_refused(s, out, "מתקין השרת אינו חלק מהדיסק-און-קי הזה")
    assert s.mount_calls() == []


def test_without_the_partition_the_item_says_it_cannot_install(tmp_path):
    s = Stick(tmp_path, parts={"sdb1": ("IMGSRV_USB", "vfat"), "sdb2": ("IMGSRV_DATA", "vfat")})
    out = s.keys(TO_INSTALL)
    row = next(r for r in records(out) if r[0] == "prow")
    assert row[1:4] == ["חבילות ההתקנה", "", "לא נמצאו"] and row[6:8] == ["warn", "אי אפשר להתקין"], row
    assert any("נבנה בלי חבילות ההתקנה" in r[1] for r in records(out) if r[0] == "pnote")


# --- the agent: from the clone screen to the installer ----------------------------------------------

#: usbclone_main with everything before the local screen stubbed; the screen "ends" as the
#: kiosk's request leaves it. The stubs print the order things happen in.
MAIN = ('usb_find_media() { :; }; usb_data_mount() { :; }; usb_sync_load() { :; }; usb_ctrl_mode() { :; }; '
        'usb_diag() { :; }; usb_survey_detect() { :; }; usb_survey() { :; }; usb_settings_load() { :; }; '
        'usb_answer() { :; }; min_free_take() { :; }; usb_fb_wait() { :; }; gui_cloner_parent() { :; }; '
        'lc_gui_alive() { return 0; }; power_watch_start() { :; }; fail_fatal() { echo FAILSCREEN; }; '
        'installer_dhcp_all() { echo DHCP; }; gui_early_stop() { echo "GUI-STOPPED $1"; }; '
        'LC="$RUN_DIR/lc"; INSTALL_MEDIA_FILE="$RUN_DIR/install-media"; ')


def agent(s: Stick, request: str | None, **env: str) -> subprocess.CompletedProcess:
    go = "" if request is None else f'echo {request} > "$LC/usbinstall.go"; '
    script = s.stubs() + MAIN + f'lc_session() {{ echo SESSION; {go}LC_END=back; }}; usbclone_main'
    return run(s.tmp, script, IMAGESRV_MODE="usbclone", SYSROOT=posix(s.root), IMAGESRV_TEST_ONCE="1", **env)


def test_the_agent_checks_again_closes_the_clone_screen_and_starts_the_installer(tmp_path):
    s = Stick(tmp_path)
    out = agent(s, "sdb3")
    assert out.returncode == 0, out.stdout + out.stderr
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith(("SESSION", "DHCP", "GUI-STOPPED", "TEST-INSTALLER"))]
    assert lines[:1] == ["SESSION"] and lines[-2:] == ["GUI-STOPPED server install", "TEST-INSTALLER: sdb3 v0.93.0"], out.stdout
    assert (tmp_path / "run" / "install-media").read_text().strip() == "/dev/sdb3"   # what the installer ejects
    assert f"umount {posix(s.usb)}" in s.mount_calls()                      # settings and reports: off before the installer
    assert s.mounted_now().split()[:2] == [f"{posix(s.root)}/dev/sdb3", posix(s.mnt)]
    assert not (tmp_path / "run" / "lc" / "usbinstall.go").exists()        # the request is used once


def test_the_dhcp_runs_under_the_dialog_and_its_words_never_reach_the_screen(tmp_path):
    s = Stick(tmp_path)
    out = agent(s, "sdb3")
    assert "DHCP" not in out.stdout, "the DHCP step wrote to the console under the GUI"
    log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8", errors="replace")
    assert "DHCP" in log and out.stdout.index("GUI-STOPPED") < out.stdout.index("TEST-INSTALLER")


@pytest.mark.parametrize("request_dev,kw,why", [
    ("sdb3", {"mount_rc": 32}, "לא נקראה מהדיסק-און-קי"),
    ("sdb4", {}, "השתנתה מאז האישור (sdb4 ← sdb3)"),
    ("sdb3", {"installer": False}, "מתקין השרת אינו חלק מהדיסק-און-קי הזה"),
    ("sdb3", {"media": {"sdb3": {"README": "x\n"}}}, "אין imagesrv-iso.json"),
])
def test_a_check_that_fails_in_the_agent_leaves_the_clone_screen_up_with_the_reason(tmp_path, request_dev, kw, why):
    s = Stick(tmp_path, **kw)
    out = agent(s, request_dev)
    assert out.returncode == 0, out.stdout + out.stderr                     # the loop went on (IMAGESRV_TEST_ONCE ends it)
    assert "TEST-INSTALLER" not in out.stdout and "GUI-STOPPED" not in out.stdout and "DHCP" not in out.stdout, out.stdout
    notice = (tmp_path / "run" / "usb.notice").read_text(encoding="utf-8")
    assert notice.startswith("מתקין השרת לא נפתח: ") and why in notice, notice
    assert f"umount {posix(s.usb)}" not in s.mount_calls()                  # the settings stay writable: still the clone stick
    assert not (tmp_path / "run" / "install-media").exists()


def test_without_a_request_the_loop_is_the_clone_screens(tmp_path):
    s = Stick(tmp_path)
    out = agent(s, None)
    assert out.returncode == 0 and "SESSION" in out.stdout, out.stdout + out.stderr
    assert "TEST-INSTALLER" not in out.stdout and "GUI-STOPPED" not in out.stdout
    assert s.mount_calls() == []


# --- the wiring ---------------------------------------------------------------------------------------

def test_the_agent_and_the_kiosk_both_load_the_library():
    assert "usbinstall" in agent_order(), "imagesrv-agent does not source usbinstall.sh: the handover is not there"
    bridge = (REPO / "agent" / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    libs = bridge[bridge.index("gui_libs() {"):bridge.index("gui_main() {")]
    assert '. "$LIB_DIR/usbinstall.sh"' in libs, "the kiosk does not load usbinstall.sh: the item's keys are not found"


def test_the_stick_still_boots_straight_to_the_clone_screen():
    """One kernel line, usbclone, no second entry: the installer is reached from the settings."""
    for name in ("grub.cfg.in", "syslinux.cfg.in"):
        text = (REPO / "tools" / "usb" / name).read_text(encoding="utf-8")
        assert "installer" not in text, name
