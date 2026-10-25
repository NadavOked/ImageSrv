"""#1914 -- the clone stick: imagectl.mode=usbclone, no server, the stick never a disk.

* the kernel line of the stick (tools/usb/*.cfg.in) carries exactly one imagectl.*
  word, imagectl.mode=usbclone -- principle 2 -- and parse_cmdline reads it;
* the stick is found by its partition labels, not by a device name, and is never in
  the local clone's inventory; in usbclone mode a list that was not written is every
  disk (fail closed);
* usbclone mode sends nothing: the agent's whole USB path runs with a curl that
  records every call, and the record stays empty; lc_report keeps the report on the
  stick and says "usb";
* the defaults (Nadav 20/10) and settings.json: a value that is not valid is refused
  by name, never used.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import hygiene
from native import requires_native
from test_cloner_gui import BASH, posix

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agent" / "lib"
USB = REPO / "tools" / "usb"

pytestmark = requires_native(("bash", BASH))
needs_jq = requires_native(("jq", shutil.which("jq")))

LIBS = ("common", "jsonq", "waits", "sysinfo", "progress", "restore", "diskfit", "expand", "grow", "ntfsunhib", "expandshare", "hostname", "hostname_linux",
        "drawers", "verdict", "failmark", "ntfsboot", "manifest", "hibernation", "shrinkplan", "shrink",
        "shrinkmem", "smart", "smartjudge", "hwissues", "guistate", "attended", "lcinv", "lcsize", "lcplan", "lcsnap", "lctune",
        "lccopy", "lcraw", "localclone", "lcwait", "idle", "lcgui", "usbclone", "usbdiag", "usbsurvey", "usbset",
        "disklink", "slotnames", "usbmap", "usbmapwin")   # #1962: the drawer mapping by model; #2001 its window


def run(tmp_path: Path, script: str, timeout: int = 60, **env) -> subprocess.CompletedProcess:
    run_dir = tmp_path / "run"; run_dir.mkdir(exist_ok=True)
    pre = f'export RUN_DIR={posix(run_dir)!r} IMAGECTL_TEST=1 SMART_KB={posix(LIB)!r}/data; '
    pre += "".join(f'export {k}={v!r}; ' for k, v in env.items())
    pre += "".join(f'. {posix(LIB)}/{name}.sh; ' for name in LIBS)
    return hygiene.run_group([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + pre + script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, stdin=subprocess.DEVNULL, cwd=str(REPO))


def sysroot(tmp_path: Path, layout: dict[str, list[str]]) -> Path:
    """sys/block/<disk>/<partition>/ the way the kernel lays it out."""
    root = tmp_path / "sysroot"
    for disk, parts in layout.items():
        d = root / "sys" / "block" / disk
        d.mkdir(parents=True)
        (d / "removable").write_text("1\n")
        for p in parts:
            (d / p).mkdir()
            (d / p / "partition").write_text(p[-1] + "\n")
    return root


def blkid_stub(labels: dict[str, str]) -> str:
    """blkid -s LABEL -o value <dev>: the label of the last word's basename."""
    cases = "".join(f'{dev}) echo {lab} ;; ' for dev, lab in labels.items())
    return f'blkid() {{ eval "_b=\\${{$#}}"; case "${{_b##*/}}" in {cases}*) return 2 ;; esac; }}; '


# --- the kernel line --------------------------------------------------------------------------

def kernel_args(text: str) -> list[list[str]]:
    lines = [ln.split() for ln in text.splitlines()]
    return [ln[2:] for ln in lines if ln[:1] == ["linux"]] + [ln[1:] for ln in lines if ln[:1] == ["APPEND"]]


@pytest.mark.parametrize("name", ["grub.cfg.in", "syslinux.cfg.in"])
def test_the_sticks_kernel_line_is_one_imagectl_word(name):
    args = kernel_args((USB / name).read_text(encoding="utf-8"))
    assert len(args) == 1, args
    ours = [a for a in args[0] if a.startswith("imagectl.")]
    assert ours == ["imagectl.mode=usbclone"], ours
    assert not any(a.startswith("ip=") for a in args[0]), args   # no DHCP asked of the kernel either


def test_parse_cmdline_reads_usbclone_and_no_server(tmp_path):
    line = " ".join(kernel_args((USB / "grub.cfg.in").read_text(encoding="utf-8"))[0])
    (tmp_path / "cmdline").write_text(line + "\n")
    out = run(tmp_path, 'parse_cmdline; printf "%s|%s\\n" "$IMAGECTL_MODE" "$IMAGECTL_SERVER"',
              CMDLINE_FILE=posix(tmp_path / "cmdline"))
    assert out.stdout.strip().splitlines()[-1] == "usbclone|", out.stdout + out.stderr


def test_init_hands_over_before_the_dhcp_loop():
    init = (REPO / "agent" / "init").read_text(encoding="utf-8")
    hand = init.index('if [ "$IMAGECTL_MODE" = usbclone ]')
    assert hand < init.index("udhcpc -i"), "usbclone must exec the agent before any DHCP"
    assert "exec /usr/bin/imagectl-agent" in init[hand:hand + 300]
    agent = (REPO / "agent" / "imagectl-agent").read_text(encoding="utf-8")
    assert agent.index("usbclone_main") < agent.index("send_hello()")   # before the first hello can exist


# --- the stick is never a source or a target -------------------------------------------------

@pytest.mark.parametrize("stick", ["sdb", "sda"])   # by label: the name it got does not matter
def test_the_stick_is_found_by_its_label(tmp_path, stick):
    disks = {"sda": ["sda1", "sda2"], "sdb": ["sdb1", "sdb2"], "sdc": ["sdc1"]}
    root = sysroot(tmp_path, disks)
    labels = {f"{stick}1": "IMGSRV_USB", f"{stick}2": "IMGSRV_DATA",
              **{f"{d}1": "Windows" for d in disks if d != stick}}
    out = run(tmp_path, blkid_stub(labels) + 'usb_media_disks', SYSROOT=posix(root))
    assert out.stdout.split() == [stick], out.stdout + out.stderr


def test_no_label_found_stops_with_nothing_offered(tmp_path):
    root = sysroot(tmp_path, {"sda": ["sda1"], "sdb": ["sdb1"]})
    out = run(tmp_path, blkid_stub({"sda1": "Windows"}) + 'usb_find_media; echo "rc=$?"',
              SYSROOT=posix(root), USB_FIND_S="0")
    assert out.stdout.strip().endswith("rc=1"), out.stdout + out.stderr
    assert not (tmp_path / "run" / "usb-media.disks").exists()


INV = ('list_drawers() { echo sda; echo sdb; echo sdc; }; '
       'lc_inv_disk() { echo "0|$1|m|s|1|ok||||||1|"; }; LC="$RUN_DIR/lc"; lc_inventory; ls "$LC/inv"')


def test_the_boot_stick_is_never_in_the_inventory(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    out = run(tmp_path, INV, IMAGECTL_MODE="usbclone")
    assert out.stdout.split() == ["sda.line", "sdc.line"], out.stdout + out.stderr


@needs_jq
def test_no_screen_is_said_in_words_on_the_text_console(tmp_path):
    # VCLONE-2 (ESXi, BIOS, VMware SVGA, 20/10): no framebuffer, the GUI never came up --
    # the console says so in English (no Hebrew font there), never a black screen
    root = sysroot(tmp_path, {"sdb": ["sdb1"]})
    out = run(tmp_path, blkid_stub({"sdb1": "IMGSRV_USB"}) + 'mount() { return 32; }; gui_cloner_parent() { :; }; '
              'fail_fatal() { echo FAILSCREEN; }; usbclone_main',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone", USB_FIND_S="0", USB_FB_WAIT_S="0")
    assert out.returncode == 87, out.stdout + out.stderr
    assert "The clone screen could not start" in out.stdout and "no /dev/fb0" in out.stdout, out.stdout
    assert "FAILSCREEN" not in out.stdout   # the GUI is what failed: no second try at drawing it


def test_without_a_framebuffer_the_words_come_instead_of_a_gui_drawn_into_ram(tmp_path):
    # #1937 (HP 6005, Radeon HD 4200, 21/10): no fb0, yet the GUI "came up" -- into RAM (the
    # mem backend, #835) -- and the screen kept the boot log. Now: no fb0, no GUI, the words.
    root = sysroot(tmp_path, {"sdb": ["sdb1"]})
    # usbclone_main with everything before the screen stubbed (no jq here): the GUI "comes up"
    # -- as the mem backend did -- and the session would run once
    pre = ('usb_find_media() { :; }; usb_data_mount() { :; }; usb_ctrl_mode() { :; }; usb_diag() { :; }; '
           'usb_survey_detect() { :; }; usb_survey() { :; }; usb_settings_load() { :; }; usb_answer() { :; }; '
           'min_free_take() { :; }; gui_cloner_parent() { echo GUI-STARTED; }; lc_gui_alive() { return 0; }; '
           'power_watch_start() { :; }; lc_session() { echo SESSION; LC_END=back; }; fail_fatal() { echo FAILSCREEN; }; ')
    out = run(tmp_path, pre + 'usbclone_main', SYSROOT=posix(root), IMAGECTL_MODE="usbclone",
              IMAGECTL_TEST_ONCE="1", USB_FB_WAIT_S="0")
    assert out.returncode == 87, out.stdout + out.stderr   # usb_stop
    assert "no /dev/fb0" in out.stdout and "GUI-STARTED" not in out.stdout and "SESSION" not in out.stdout, out.stdout
    late = root / "sys" / "class" / "graphics" / "fb0"   # a driver that comes a little late is waited for
    out = run(tmp_path, pre + f'( sleep 1; mkdir -p {posix(late)!r} ) & usbclone_main', SYSROOT=posix(root),
              IMAGECTL_MODE="usbclone", IMAGECTL_TEST_ONCE="1", USB_FB_WAIT_S="5")
    assert out.returncode == 0 and "GUI-STARTED" in out.stdout and "SESSION" in out.stdout, out.stdout + out.stderr


def test_usbclone_without_the_list_offers_no_disk(tmp_path):
    out = run(tmp_path, INV, IMAGECTL_MODE="usbclone")
    assert out.stdout.split() == [], out.stdout + out.stderr


def test_other_modes_ignore_the_list(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    out = run(tmp_path, INV, IMAGECTL_MODE="normal")
    assert out.stdout.split() == ["sda.line", "sdb.line", "sdc.line"], out.stdout + out.stderr


# --- nothing reaches a server --------------------------------------------------------------------

def curl_recorder(tmp_path: Path) -> tuple[str, Path]:
    """A curl that writes down every call and fails at once: a function for this
    shell, and a file first on PATH for anything it starts."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    log = tmp_path / "curl.calls"
    (bin_dir / "curl").write_text(f'#!/bin/sh\necho "$*" >> {posix(log)!r}\nexit 7\n', newline="\n")
    (bin_dir / "curl").chmod(0o755)
    prefix = (f'export PATH={posix(bin_dir)!r}:"$PATH"; '
              f'curl() {{ echo "$*" >> {posix(log)!r}; return 7; }}; ')
    return prefix, log


def test_the_offline_functions_send_nothing(tmp_path):
    bin_dir, log = curl_recorder(tmp_path)
    out = run(tmp_path, bin_dir + 'SERVER=http://10.0.0.1:8080; usb_offline; '
                        'http_get "$SERVER/api/v1/agent/state"; echo "g=$?"; '
                        'http_post_json "$SERVER/api/v1/agent/hello" /dev/null; echo "p=$?"; '
                        'send_hello; echo "h=$?";'
                        'echo open > "$RUN_DIR/attended.round"; lc_round_calls; echo "r=$?"')
    assert "g=1" in out.stdout and "p=1" in out.stdout and "h=1" in out.stdout and "r=1" in out.stdout, \
        out.stdout + out.stderr
    assert not log.exists(), log.read_text()


def test_no_beat_is_started(tmp_path):
    # the attended beat is a hello every 10 s while a person decides: none on the stick
    out = run(tmp_path, 'usb_offline; attended_start q; echo "pid=${ATTENDED_PID:-none}"')
    assert out.stdout.strip().splitlines()[-1] == "pid=none", out.stdout + out.stderr


@needs_jq   # the stick's copy is rebuilt without serials by jq (Nadav 20/10)
def test_the_local_report_stays_local_and_keeps_no_serial(tmp_path):
    bin_dir, log = curl_recorder(tmp_path)
    r = tmp_path / "run" / "lc" / "run"; r.mkdir(parents=True)
    (r / "targets").write_text("sdc\n"); (r / "started").write_text("100\n"); (r / "ended").write_text("160\n")
    data = tmp_path / "usbdata"; data.mkdir()
    out = run(tmp_path, bin_dir + 'LC="$RUN_DIR/lc"; _lr_src=sda; MAC=aa:bb; '
                        'disk_port() { echo 1; }; disk_serial() { echo S1XYZ987; }; list_disks() { echo sda; echo sdc; }; '
                        f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; lc_report; cat "$LC/run/report"',
              IMAGECTL_MODE="usbclone")
    assert out.stdout.strip().splitlines()[-1] == "usb", out.stdout + out.stderr
    assert not log.exists(), log.read_text()
    kept = list((data / "reports").glob("clone-*.json"))
    assert len(kept) == 1, out.stdout + out.stderr
    text = kept[0].read_text(encoding="utf-8")
    report = json.loads(text)
    assert report["source"]["dev"] == "sda" and "machine" in report
    assert "S1XYZ987" not in text and '"serial"' not in text, text   # Nadav 20/10: nothing about the disks


@needs_jq
def test_usbclone_main_sends_nothing_and_reaches_the_local_screen(tmp_path):
    bin_dir, log = curl_recorder(tmp_path)
    root = sysroot(tmp_path, {"sda": ["sda1"], "sdb": ["sdb1", "sdb2"]})
    (root / "sys" / "class" / "net" / "eth0").mkdir(parents=True)
    (root / "sys" / "class" / "net" / "eth0" / "address").write_text("aa:bb:cc:dd:ee:01\n")
    (root / "sys" / "class" / "graphics" / "fb0").mkdir(parents=True)   # #1937: a screen to draw on
    script = (bin_dir
              + blkid_stub({"sdb1": "IMGSRV_USB", "sdb2": "IMGSRV_DATA", "sda1": "Windows"})
              + 'mount() { return 32; }; '
              'gui_cloner_parent() { sleep 30 & _gui_pid=$!; }; '
              'lc_session() { echo "SESSION drawers=$(cat "$RUN_DIR/usb-media.disks")"; LC_END=back; }; '
              'usbclone_main')
    out = run(tmp_path, script, SYSROOT=posix(root), IMAGECTL_MODE="usbclone", IMAGECTL_TEST_ONCE="1",
              USB_FIND_S="0")
    assert "SESSION drawers=sdb" in out.stdout, out.stdout + out.stderr
    assert out.returncode == 0, out.stdout + out.stderr
    assert not log.exists(), log.read_text()
    answer = json.loads((tmp_path / "run" / "response.json").read_text(encoding="utf-8"))
    assert (answer["role"], answer["min_free_gb"], answer["readback_mode"], answer["crc_warn_threshold"]) == \
        ("cloner", 20, "sample", 500)
    assert answer["usb"]["temp_warn_c"] == 60 and answer["usb"]["data"] == "failed"
    assert "לסשן הזה בלבד" in (tmp_path / "run" / "usb.notice").read_text(encoding="utf-8")
    assert (tmp_path / "run" / "min_free_gb").read_text().strip() == "20"


# --- the settings file --------------------------------------------------------------------------

def settings(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    data = tmp_path / "usbdata"; data.mkdir(exist_ok=True)
    (data / "settings.json").write_text(body, encoding="utf-8")
    return run(tmp_path, f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_settings_load; '
                         'printf "%s|%s|%s|%s\\n%s\\n" "$USB_MIN_FREE_GB" "$USB_READBACK" "$USB_CRC_WARN" '
                         '"$USB_TEMP_WARN_C" "$USB_NOTICE"')


@needs_jq
def test_valid_settings_are_used(tmp_path):
    out = settings(tmp_path, '{"min_free_gb": 0, "readback_mode": "full", "crc_warn_threshold": 0, "temp_warn_c": 55}')
    assert out.stdout.splitlines()[:2] == ["0|full|0|55", ""], out.stdout + out.stderr


@needs_jq
@pytest.mark.parametrize("body,bad", [
    ('{"min_free_gb": 5000}', "min_free_gb=5000"),
    ('{"min_free_gb": "020"}', "min_free_gb=020"),
    ('{"readback_mode": "fast"}', "readback_mode=fast"),
    ('{"temp_warn_c": 5}', "temp_warn_c=5"),
])
def test_a_value_that_is_not_valid_is_refused_aloud(tmp_path, body, bad):
    out = settings(tmp_path, body)
    first, notice = out.stdout.splitlines()[:2]
    assert first == "20|sample|500|60", out.stdout + out.stderr     # the defaults stay
    assert "נדחו" in notice and bad in notice, notice


@needs_jq
def test_a_settings_file_that_is_not_json_is_refused_aloud(tmp_path):
    out = settings(tmp_path, "{not json")
    first, notice = out.stdout.splitlines()[:2]
    assert first == "20|sample|500|60" and "אינו JSON תקין" in notice, out.stdout


def test_the_defaults_are_nadavs():
    text = (LIB / "usbclone.sh").read_text(encoding="utf-8")
    got = dict(re.findall(r"^USB_DEF_(\w+)=(\S+)", text, re.M))
    assert got == {"MIN_FREE_GB": "20", "READBACK": "sample", "CRC_WARN": "500", "TEMP_WARN_C": "60"}


# --- the settings: the server's settings screen, the stick's data (Nadav 20/10) ------------------

FRAMES = REPO / "docs" / "design" / "native" / "usb-clone" / "frames"
KIOSK = 'LC="$RUN_DIR/lc"; GUI_DIR="$RUN_DIR/gui"; mkdir -p "$LC" "$GUI_DIR"; AGENT_VERSION=v0.77.3; '


def frame_records(text: str) -> list[list[str]]:
    """The records, the clock (hdr field 6) left out: it is the minute it was drawn."""
    out = []
    for line in text.splitlines():
        if not line:
            continue
        rec = line.split("\t")
        while len(rec) > 1 and rec[-1] == "":   # a trailing empty field is the same record to dcui_feed
            rec.pop()
        if rec[0] == "hdr":
            rec[5] = "HH:MM"
        out.append(rec)
    return out


def kiosk(tmp_path: Path, keys: list[str], data: str = "mounted") -> tuple[subprocess.CompletedProcess, Path]:
    usb = tmp_path / "usbdata"; usb.mkdir(exist_ok=True)
    script = (KIOSK + f'echo {data} > "$RUN_DIR/usb.data"; USB_DATA_DIR={posix(usb)!r}; usbset_open; '
              + "".join(f'usbset_key {k!r}; ' for k in keys) + 'cat "$GUI_DIR/usbset.frame"')
    return run(tmp_path, script, IMAGECTL_MODE="usbclone"), usb


@pytest.mark.parametrize("name,keys", [
    ("usb-1-settings", []),
    ("usb-2-edit-refused", ["KEY ENTER", "KEY BACKSPACE", "KEY BACKSPACE",
                            "CHAR 35", "CHAR 30", "CHAR 30", "CHAR 30", "KEY ENTER"]),
    ("usb-3-readback", ["KEY DOWN", "KEY ENTER"]),
])
def test_the_settings_are_the_approved_frames(tmp_path, name, keys):
    out, _usb = kiosk(tmp_path, keys)
    want = frame_records((FRAMES / f"{name}.frame").read_text(encoding="utf-8"))
    assert frame_records(out.stdout) == want, out.stdout + out.stderr


@pytest.mark.parametrize("key", ["CHAR 20", "KEY RIGHT"])
def test_space_switches_the_readback_choice_as_the_hint_says(tmp_path, key):
    """#1963: the dialog's hint is "Space החלפה", and Space reaches usbset_key as "CHAR 20"
    (dcui_token: every printable key is a CHAR). It was swallowed; only the arrows switched."""
    out, _usb = kiosk(tmp_path, ["KEY DOWN", "KEY ENTER", key])
    opts = {r[1]: r[3] for r in frame_records(out.stdout) if r[0] == "d.opt"}
    assert opts == {"מדגם": "0", "מלא": "1"}, out.stdout + out.stderr


def test_a_refused_value_is_not_saved(tmp_path):
    out, usb = kiosk(tmp_path, ["KEY ENTER", "KEY BACKSPACE", "KEY BACKSPACE",
                                "CHAR 39", "CHAR 39", "CHAR 39", "KEY ENTER"])
    assert "999 אינו בטווח 0–500 — לא נשמר." in out.stdout, out.stdout + out.stderr
    assert not (usb / "settings.json").exists()
    assert not (tmp_path / "run" / "usb-settings.json").exists()


def test_a_letter_is_refused_in_a_number(tmp_path):
    out, _usb = kiosk(tmp_path, ["KEY ENTER", "CHAR 61"])
    assert "d.error\tספרות בלבד" in out.stdout, out.stdout + out.stderr


def test_without_a_data_partition_the_screen_says_session_only(tmp_path):
    out, _usb = kiosk(tmp_path, [], data="none")
    assert "לסשן הזה בלבד" in out.stdout and "\tלסשן\t" in out.stdout, out.stdout + out.stderr


@needs_jq
def test_a_valid_value_is_saved_on_the_stick_and_in_force(tmp_path):
    out, usb = kiosk(tmp_path, ["KEY ENTER", "KEY BACKSPACE", "KEY BACKSPACE", "CHAR 33", "CHAR 30", "KEY ENTER",
                                "KEY DOWN", "KEY ENTER", "KEY RIGHT", "KEY ENTER"])
    saved = json.loads((usb / "settings.json").read_text(encoding="utf-8"))
    assert saved == {"min_free_gb": 30, "readback_mode": "full"}, out.stdout + out.stderr
    answer = json.loads((tmp_path / "run" / "response.json").read_text(encoding="utf-8"))
    assert (answer["min_free_gb"], answer["readback_mode"]) == (30, "full")
    assert (tmp_path / "run" / "min_free_gb").read_text().strip() == "30"


def test_esc_goes_back_to_the_clone_screen(tmp_path):
    out = run(tmp_path, KIOSK + 'usbset_open; cat "$LC/screen"; usbset_key "KEY ESC"; cat "$LC/screen"',
              IMAGECTL_MODE="usbclone")
    assert out.stdout.split() == ["usbset", "local"], out.stdout + out.stderr


@pytest.mark.parametrize("mode,phase,want", [("usbclone", "idle", "usbset"), ("normal", "idle", "local"),
                                             ("usbclone", "copying", "local")])
def test_the_settings_open_only_on_the_stick_and_not_during_a_copy(tmp_path, mode, phase, want):
    out = run(tmp_path, KIOSK + f'echo local > "$LC/screen"; echo {phase} > "$LC/phase"; '
                                'token=lc-usbset; lc_gui_dispatch; cat "$LC/screen"', IMAGECTL_MODE=mode)
    assert out.stdout.split()[-1] == want, out.stdout + out.stderr


# --- the shrink: the cloner's, unchanged (Nadav 20/10: "ב-USB אמור להיות כיווץ גם") -----------------

#: the local clone's shrink path: the measure, the plan, the RAM view (#1466), the copy, the read-back
SHRINK_PATH = ("lcinv.sh", "lcsize.sh", "lcplan.sh", "lcsnap.sh", "lctune.sh", "lccopy.sh", "lcraw.sh",
               "shrinkplan.sh", "shrink.sh", "hibernation.sh", "readbacklc.sh")


@pytest.mark.parametrize("name", SHRINK_PATH)
def test_the_shrink_path_asks_no_server(name):
    text = (LIB / name).read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for word in ("$SERVER", "http_get", "http_post_json", "curl "):
        assert word not in code, f"{name}: {word}"


def test_the_stick_carries_what_the_shrink_needs():
    """The stick packs the cloner's own GUI initramfs (build_usb_clone.sh -> build_initramfs.sh
    --with-gui); that build stops when a declared module is missing (#78), so these are there."""
    build = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    binaries = build[build.index("BINARIES=("):build.index(")", build.index("BINARIES=("))]
    for tool in ("ntfsresize", "dmsetup", "partclone.ntfs", "partclone.dd", "blockdev"):
        assert tool in binaries, tool
    assert re.search(r"dm-mod dm-snapshot dm-bufio brd", build)
    usb = (REPO / "tools" / "build_usb_clone.sh").read_text(encoding="utf-8")
    assert "build_initramfs.sh" in usb and "--with-gui" in usb


def test_a_smaller_target_is_offered_the_shrink_on_the_stick_with_no_server_call(tmp_path):
    # the screen's records in usbclone mode: the 250 GB drawer "fits after a temporary shrink"
    # of the 480 GB source, the same lc_fit as the cloner's -- and curl never ran
    rec, log = curl_recorder(tmp_path)
    out = run(tmp_path, rec + 'usb_offline; LC="$RUN_DIR/lc"; export GUI_DIR=$RUN_DIR/gui; '
              'mkdir -p "$GUI_DIR" "$LC/inv" "$LC/measure"; echo 0 > "$RUN_DIR/min_free_gb"; '
              'echo local > "$LC/screen"; echo done > "$LC/inv.state"; echo idle > "$LC/phase"; '
              'echo "1|sda|A|s1|250000000000|ok|ok||0|0|-1|0|" > "$LC/inv/sda.line"; '
              'echo "3|sdc|C|s3|480000000000|ok|ok|Windows|4|1|480000000000|1|" > "$LC/inv/sdc.line"; '
              'echo sdc > "$LC/source"; echo "done|200000000000|" > "$LC/measure/sdc"; '
              'lc_gui_state; cat "$GUI_DIR/state"', IMAGECTL_MODE="usbclone")
    lines = out.stdout.splitlines()
    assert "lc_fit=sda|shrink|200000000000|" in lines and "lc_usb=1" in lines, out.stdout + out.stderr
    assert not log.exists(), log.read_text()


# --- the diagnostic report (college 20/10: "no disks" on an old HP, nothing to send back) ------------

def test_every_boot_keeps_a_diagnostic_report_on_the_stick(tmp_path):
    root = sysroot(tmp_path, {"sdb": ["sdb1", "sdb2"]})
    ide = root / "sys" / "bus" / "pci" / "devices" / "0000_00_1f.2"; ide.mkdir(parents=True)
    (ide / "class").write_text("0x01018a\n", newline="\n")
    (ide / "vendor").write_text("0x8086\n", newline="\n"); (ide / "device").write_text("0x3a20\n", newline="\n")
    (ide / "modalias").write_text("pci:v00008086d00003A20sv0000103Csd00003048bc01sc01i8a\n", newline="\n")
    (root / "proc").mkdir(); (root / "proc" / "partitions").write_text("major minor  #blocks  name\n   8  16  7812500 sdb\n")
    (root / "sys" / "block" / "sdb" / "device").mkdir()
    (root / "sys" / "block" / "sdb" / "device" / "serial").write_text("WD-WCC4E1234567\n")   # Nadav 20/10: never kept
    (tmp_path / "run").mkdir(); (tmp_path / "run" / "agent.log").write_text("[10:00:00] smart: sdb WD-WCC4E1234567 ok\n")
    data = tmp_path / "usbdata"; data.mkdir()
    (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    out = run(tmp_path, f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_diag no-disks; echo "rc=$?"; '
                        'LC="$RUN_DIR/lc"; GUI_DIR="$RUN_DIR/gui"; mkdir -p "$LC" "$GUI_DIR"; echo local > "$LC/screen"; '
                        'lc_gui_state; cat "$GUI_DIR/state"', SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    kept = list(data.glob("diag-*.txt"))
    assert len(kept) == 1, kept
    text = kept[0].read_text(encoding="utf-8")
    assert "(the machine's clock, not verified)" in text
    assert "class=0x01018a id=0x8086 0x3a20 driver=(none)" in text, text   # an IDE-mode ICH10 with no driver, said
    assert "sdb" in text and "boot media (never a source or a target): sdb" in text
    assert "WD-WCC4E1234567" not in text and "smart: sdb <serial> ok" in text, text   # scrubbed, the log line too
    assert f"lc_diag={kept[0].name}" in out.stdout.splitlines(), out.stdout


def wwid_disk(tmp_path: Path, vpd: bool, pad: bool = False) -> tuple[subprocess.CompletedProcess, Path]:
    """sda's disk_serial is a naa. wwid (a number, not the serial); its bare serial
    ZA1B2C3D lives in VPD page 0x80 (vpd=True) or nowhere this side can read (False)
    -- and dmesg and agent.log both carry it."""
    root = sysroot(tmp_path, {"sda": ["sda1"], "sdb": ["sdb1"]})
    dev = root / "sys" / "block" / "sda" / "device"; dev.mkdir()
    (dev / "wwid").write_text("naa.5000c500a1b2c3d4\n")
    if vpd:
        body = b"      ZA1B2C3D      " if pad else b"ZA1B2C3D"   # ATA pads the page with spaces
        (dev / "vpd_pg80").write_bytes(b"\x00\x80\x00" + bytes([len(body)]) + body)
    stick = root / "sys" / "block" / "sdb" / "device"; stick.mkdir()
    (stick / "serial").write_text("STICK0001\n")
    data = tmp_path / "usbdata"; data.mkdir()
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    (tmp_path / "run" / "agent.log").write_text("[10:00:00] local clone: sda ZA1B2C3D 500 GB\n")
    out = run(tmp_path, 'dmesg() { echo "ata1.00: ATA-9: ST500DM002, KC45, max UDMA/133 sn ZA1B2C3D"; '
                        'echo "sd 0:0:0:0: [sda] 976773168 512-byte logical blocks"; }; '
                        f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_diag boot; echo "rc=$?"',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    return out, data


def stick_files(data: Path) -> str:
    return "".join(p.read_text(encoding="utf-8", errors="replace") for p in data.rglob("*") if p.is_file())


def test_a_serial_behind_a_wwid_never_reaches_the_stick(tmp_path):
    # review of d7166eb1: disk_serial gave the naa. wwid, and the bare serial in dmesg/log leaked
    out, data = wwid_disk(tmp_path, vpd=True)
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    text = stick_files(data)
    assert "ZA1B2C3D" not in text and "STICK0001" not in text, text
    assert "[sda] 976773168 512-byte logical blocks" in text   # the diagnosis stays


def test_a_space_padded_vpd_serial_is_still_scrubbed(tmp_path):
    # review of 1e79ace0: the candidate kept its padding, and index() never found the bare serial
    out, data = wwid_disk(tmp_path, vpd=True, pad=True)
    text = stick_files(data)
    assert "rc=0" in out.stdout and "ZA1B2C3D" not in text, text
    assert "[sda] 976773168 512-byte logical blocks" in text   # known, so dmesg is kept -- scrubbed


@needs_jq
def test_an_unknown_serial_keeps_the_reports_free_text_off_the_stick(tmp_path):
    # review of 1e79ace0: a target's error carried a serial-like string, and the scrub knew none
    rec, log = curl_recorder(tmp_path)
    r = tmp_path / "run" / "lc" / "run"; r.mkdir(parents=True)
    (r / "targets").write_text("sdc\n"); (r / "started").write_text("100\n"); (r / "ended").write_text("160\n")
    (r / "source_restore").write_text("view of ZQ9X8W7V released\n")
    t = tmp_path / "run" / "targets" / "sdc"; t.mkdir(parents=True)
    (t / "state").write_text("failed\n"); (t / "error").write_text("write failed on ZQ9X8W7V\n")
    data = tmp_path / "usbdata"; data.mkdir()
    out = run(tmp_path, rec + 'LC="$RUN_DIR/lc"; _lr_src=sda; MAC=aa:bb; disk_port() { echo 1; }; '
              'disk_serial() { echo naa.5000c500a1b2c3d4; }; list_disks() { echo sda; echo sdc; }; '
              f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; lc_report', IMAGECTL_MODE="usbclone")
    kept = list((data / "reports").glob("clone-*.json"))
    assert len(kept) == 1, out.stdout + out.stderr
    report = json.loads(kept[0].read_text(encoding="utf-8"))
    text = kept[0].read_text(encoding="utf-8")
    assert "ZQ9X8W7V" not in text and "naa.5000c500a1b2c3d4" not in text, text
    assert report["targets"][0]["state"] == "failed" and report["elapsed_s"] == 60   # the rest stays
    assert report["targets"][0]["error"] == report["source_restore"] == "הוסר — לא ניתן לוודא שאין בו serial"


@needs_jq
def test_a_target_gone_since_the_clone_keeps_the_reports_free_text_off_the_stick(tmp_path):
    # review of 06329d00: sdc was pulled after the copy -- list_disks no longer has it,
    # but the report's text does; its serial is unknown, so the text goes
    rec, log = curl_recorder(tmp_path)
    root = sysroot(tmp_path, {"sda": []})
    (root / "sys" / "block" / "sda" / "device").mkdir()
    (root / "sys" / "block" / "sda" / "device" / "serial").write_text("S3Z1NB0K1\n")   # the source: known
    r = tmp_path / "run" / "lc" / "run"; r.mkdir(parents=True)
    (r / "src").write_text("sda\n"); (r / "targets").write_text("sdc\n")
    (r / "started").write_text("100\n"); (r / "ended").write_text("160\n")
    t = tmp_path / "run" / "targets" / "sdc"; t.mkdir(parents=True)
    (t / "state").write_text("failed\n"); (t / "error").write_text("the drawer went away: WX41A1234567\n")
    data = tmp_path / "usbdata"; data.mkdir()
    out = run(tmp_path, rec + 'LC="$RUN_DIR/lc"; _lr_src=sda; MAC=aa:bb; disk_port() { echo 1; }; '
              f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; lc_report',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    kept = list((data / "reports").glob("clone-*.json"))
    assert len(kept) == 1, out.stdout + out.stderr
    report = json.loads(kept[0].read_text(encoding="utf-8"))
    assert "WX41A1234567" not in kept[0].read_text(encoding="utf-8")
    assert report["targets"][0]["error"] == "הוסר — לא ניתן לוודא שאין בו serial", report


def test_a_drawer_swapped_under_the_same_name_does_not_leak_the_first_serial(tmp_path):
    # review of c5ed7ff3: sdc was ORIGSER01 when the clone started, NEWSER002 when the files
    # were written -- the scrub must know the first one, from the snapshot in RAM
    root = sysroot(tmp_path, {"sdc": []})
    dev = root / "sys" / "block" / "sdc" / "device"; dev.mkdir()
    (dev / "serial").write_text("ORIGSER01\n")
    lc = tmp_path / "run" / "lc" / "run"; lc.mkdir(parents=True)
    (lc / "src").write_text("sdc\n"); (lc / "targets").write_text("")
    (tmp_path / "run" / "agent.log").write_text("[10:00:00] local clone: sdc ORIGSER01 -> sdd\n")
    data = tmp_path / "usbdata"; data.mkdir()
    out = run(tmp_path, f'usb_serials_snap sdc; echo NEWSER002 > {posix(dev / "serial")!r}; dmesg() {{ :; }}; '
                        f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_diag clone; echo "rc=$?"',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    text = stick_files(data)
    assert "ORIGSER01" not in text and "NEWSER002" not in text, text
    assert "local clone: sdc <serial> -> sdd" in text, text   # known: the log is kept, scrubbed
    assert not list(data.rglob("*snap*"))                       # the snapshot is RAM, never the stick


def test_a_disk_the_report_names_but_gone_is_not_known(tmp_path):
    # the same rule without jq: usb_serials over list_disks + the report's own disks
    root = sysroot(tmp_path, {"sda": []})
    (root / "sys" / "block" / "sda" / "device").mkdir()
    (root / "sys" / "block" / "sda" / "device" / "serial").write_text("S3Z1NB0K1\n")
    out = run(tmp_path, 'usb_serials "$RUN_DIR/s"; echo "present=$?"; usb_serials "$RUN_DIR/s" sda sdc; echo "named=$?"',
              SYSROOT=posix(root))
    assert "present=0" in out.stdout and "named=1" in out.stdout, out.stdout + out.stderr


def test_a_serial_found_nowhere_leaves_the_free_text_out(tmp_path):
    out, data = wwid_disk(tmp_path, vpd=False)
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    text = stick_files(data)
    assert "ZA1B2C3D" not in text, text
    assert text.count("לא ניתן לוודא שאין בו serial") == 2, text   # dmesg and agent.log, said in their place
    assert "== storage controllers" in text                     # the rest of the report is there


def stick_only(tmp_path: Path, serial: str | None) -> tuple[subprocess.CompletedProcess, str]:
    """#1937 (the college, 21/10): a boot with only the stick (sdb) connected. Its serial
    lives in the USB device (2-1/serial) -- not in sys/block -- or nowhere (serial=None)."""
    root = sysroot(tmp_path, {"sdb": ["sdb1", "sdb2"]})
    usb = root / "sys" / "bus" / "usb" / "devices" / "2-1"; usb.mkdir(parents=True)
    (usb / "idVendor").write_text("0781\n", newline="\n")
    if serial:
        (usb / "serial").write_text(serial + "\n", newline="\n")
    data = tmp_path / "usbdata"; data.mkdir()
    (tmp_path / "run").mkdir(exist_ok=True); (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    (tmp_path / "run" / "agent.log").write_text(f"[10:00:00] usbclone: the stick {serial or 'sdb'} found by its label\n")
    out = run(tmp_path, 'usb_disk_syspath() { case "$1" in '
              'sdb) echo /sys/devices/pci0000:00/0000:00:1d.7/usb2/2-1/2-1:1.0/host6/target6:0:0/6:0:0:0/block/sdb ;; esac; }; '
              f'dmesg() {{ echo "sd 6:0:0:0: [sdb] 123699200 512-byte logical blocks"; echo "scsi 6:0:0:0: Direct-Access SanDisk {serial or ""}"; }}; '
              f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_diag boot; echo "rc=$?"',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    return out, stick_files(data)


def test_a_boot_with_only_the_stick_keeps_dmesg_and_the_log_scrubbed(tmp_path):
    out, text = stick_only(tmp_path, "4C530001230928114583")
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    assert "לא ניתן לוודא שאין בו serial" not in text, text   # the stick is not "unknown"
    assert "[sdb] 123699200 512-byte logical blocks" in text and "the stick <serial> found" in text, text
    assert "4C530001230928114583" not in text, text


def test_a_stick_whose_usb_device_has_no_serial_is_not_unknown(tmp_path):
    out, text = stick_only(tmp_path, None)
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    assert "[sdb] 123699200 512-byte logical blocks" in text and "לא ניתן לוודא שאין בו serial" not in text, text


def test_the_diag_says_the_controller_mode_without_drawers_and_has_no_lspci(tmp_path):
    # #1937 (every field diag, 21/10): "controller mode (drawers): |" with only the stick
    # connected, and "lspci: not found" twice -- lspci is not on the stick
    root = sysroot(tmp_path, {"sdb": ["sdb1"]})
    ide = root / "sys" / "bus" / "pci" / "devices" / "0000_00_1f.2"; ide.mkdir(parents=True)
    (ide / "class").write_text("0x01018a\n", newline="\n")
    data = tmp_path / "usbdata"; data.mkdir()
    (tmp_path / "run").mkdir(exist_ok=True); (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    out = run(tmp_path, f'dmesg() {{ :; }}; usb_ctrl_mode; USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_diag boot; echo "rc=$?"',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    text = stick_files(data)
    line = next(ln for ln in text.splitlines() if ln.startswith("controller mode (drawers): "))
    assert line == "controller mode (drawers): לא נקרא (no drawer with a disk) -- by controller: 0000_00_1f.2=(none):IDE", line
    assert "lspci" not in text, text


def test_the_diag_surveys_the_machine_and_says_what_it_could_not_read(tmp_path):
    # Nadav 20/10: a round over several models tomorrow -- every field read here, none blank
    root = sysroot(tmp_path, {"sda": [], "sdb": ["sdb1"]})
    w = lambda rel, text: ((root / rel).parent.mkdir(parents=True, exist_ok=True), (root / rel).write_text(text))
    w("sys/class/dmi/id/sys_vendor", "Hewlett-Packard\n"); w("sys/class/dmi/id/product_name", "HP Compaq 8100 Elite SFF PC\n")
    w("sys/class/dmi/id/bios_version", "786H1 v01.05\n")   # bios_date left out: said, not blank
    w("proc/cpuinfo", "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i5 CPU 650 @ 3.20GHz\ncpu cores\t: 2\n"
                      "flags\t\t: fpu sse4_2 aes popcnt\nprocessor\t: 1\n")
    w("proc/meminfo", "MemTotal:        3987456 kB\n")
    w("proc/uptime", "41.27 80.00\n")
    w("sys/class/ata_link/link1/sata_spd", "3.0 Gbps\n")
    w("sys/block/sda/device/model", "ST500DM002-1BD142\n"); w("sys/block/sda/size", "976773168\n")
    w("sys/block/sda/device/serial", "Z3TABCDE\n")
    w("sys/bus/usb/devices/2-1/speed", "480\n")
    w("sys/bus/pci/devices/0000_00_1f.2/class", "0x01018a\n")
    w("sys/bus/pci/devices/0000_00_1f.2/vendor", "0x8086\n"); w("sys/bus/pci/devices/0000_00_1f.2/device", "0x3a20\n")
    data = tmp_path / "usbdata"; data.mkdir()
    (tmp_path / "run").mkdir(exist_ok=True); (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    (tmp_path / "run" / "usb.gui_up").write_text("12.40\n")
    out = run(tmp_path, 'usb_disk_syspath() { case "$1" in '
              'sda) echo /sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0/block/sda ;; '
              'sdb) echo /sys/devices/pci0000:00/0000:00:1d.0/usb2/2-1/2-1:1.0/host6/target6:0:0/6:0:0:0/block/sdb ;; esac; }; '
              'usb_pci_driver() { case "$1" in 0000:00:1d.0) printf ehci-pci ;; esac; }; dmesg() { :; }; '
              f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_diag boot; echo "rc=$?"',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    text = stick_files(data)
    for want in ("machine: Hewlett-Packard HP Compaq 8100 Elite SFF PC", "BIOS: 786H1 v01.05, לא נקרא",
                 "boot: BIOS (Secure Boot: n/a)",
                 "CPU: Intel(R) Core(TM) i5 CPU 650 @ 3.20GHz · cores 2 · threads 2 · aes yes · sha no",
                 "RAM: 3.8 GB", "CPU temperature: לא נקרא", "port 1: 3.0 Gbps · sda ST500DM002-1BD142 465 GB",
                 "driver=לא נקרא mode=IDE", "fb0: לא נקרא", "USB boot: sdb on 0000:00:1d.0 (ehci-pci), 480 Mb/s (USB 2.0)",
                 "kernel to GUI: 12.40 s"):
        assert want in text, (want, text)
    assert "Z3TABCDE" not in text   # the disk: port, model, size -- never its serial


def test_no_data_partition_no_report_and_said(tmp_path):
    out = run(tmp_path, 'USB_DATA_STATE=failed; usb_diag boot; echo "rc=$?"; cat "$RUN_DIR/agent.log"')
    assert "rc=1" in out.stdout and "the diagnostic report is not kept" in out.stdout, out.stdout + out.stderr


# --- the controller in IDE mode (Nadav 20/10: 60-80 MB/s there, 134 in AHCI) ---------------------

def ctrl(tmp_path: Path, drivers: dict[str, str]) -> tuple[str, str]:
    """usb_ctrl_mode with the disks behind 0000:00:1f.2 (sda, sdb) and 0000:00:1d.0 (the stick, sdc)."""
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "run" / "usb-media.disks").write_text("sdc\n")
    paths = {"sda": "/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0/block/sda",
             "sdb": "/sys/devices/pci0000:00/0000:00:1f.2/ata2/host1/target1:0:0/1:0:0:0/block/sdb",
             "sdc": "/sys/devices/pci0000:00/0000:00:1d.0/usb2/2-1/2-1:1.0/host6/block/sdc"}
    path_case = "".join(f'{d}) echo {p} ;; ' for d, p in paths.items())
    drv_case = "".join(f'{b}) printf %s {d} ;; ' for b, d in drivers.items())
    out = run(tmp_path, 'list_drawers() { echo sda; echo sdb; echo sdc; }; '
              f'usb_disk_syspath() {{ case "$1" in {path_case} esac; }}; '
              f'usb_pci_driver() {{ case "$1" in {drv_case} esac; }}; '
              'usb_ctrl_mode; cat "$RUN_DIR/usb.ctrl"; LC="$RUN_DIR/lc"; GUI_DIR="$RUN_DIR/gui"; mkdir -p "$LC" "$GUI_DIR"; '
              'echo local > "$LC/screen"; lc_gui_state; grep "^lc_ide=" "$GUI_DIR/state"', IMAGECTL_MODE="usbclone")
    lines = out.stdout.splitlines()
    return lines[0], lines[-1]


def test_drawers_behind_ata_piix_are_said_to_be_in_ide_mode(tmp_path):
    rec, gui = ctrl(tmp_path, {"0000:00:1f.2": "ata_piix", "0000:00:1d.0": "ehci-pci"})
    assert rec == "ide|sda=ata_piix sdb=ata_piix" and gui == "lc_ide=ide", (rec, gui)   # the stick is not counted


def test_ahci_is_not_ide(tmp_path):
    rec, gui = ctrl(tmp_path, {"0000:00:1f.2": "ahci"})
    assert rec == "other|sda=ahci sdb=ahci" and gui == "lc_ide=other", (rec, gui)


def test_a_driver_that_cannot_be_read_says_nothing(tmp_path):
    rec, gui = ctrl(tmp_path, {})
    assert rec == "|" and gui == "lc_ide=", (rec, gui)


# --- nothing from what is ON a disk (Nadav 20/10: no GUID, label, UUID, hostname) ------------------

DISK_GUID = "9C3B1A2E-1111-4A2B-8C3D-0123456789AB"
PART_GUID = "c0ffee00-2222-4b3c-9d4e-abcdefabcdef"
SWAP_UUID = "3f0a1b2c-1d2e-4f50-a1b2-c3d4e5f60718"


def test_the_diag_keeps_no_identifier_from_the_disks(tmp_path):
    # expand.sh logs the source's disk GUID ("came back from the disk"), mkswap its UUID= and
    # LABEL=, ext4 in dmesg its filesystem UUID -- all in the agent.log tail / dmesg of the diag
    root = sysroot(tmp_path, {"sda": ["sda1"], "sdb": ["sdb1"]})
    (root / "sys" / "block" / "sda" / "device").mkdir()
    (root / "sys" / "block" / "sda" / "device" / "serial").write_text("ZA1B2C3D\n")
    (root / "sys" / "block" / "sdb" / "device").mkdir()
    (root / "sys" / "block" / "sdb" / "device" / "serial").write_text("STICK0001\n")
    data = tmp_path / "usbdata"; data.mkdir()
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "run" / "usb-media.disks").write_text("sdb\n")
    (tmp_path / "run" / "agent.log").write_text(
        f"[10:00:00] sdc: disk GUID {DISK_GUID} came back from the disk\n"
        f"[10:00:01] Setting up swapspace version 1, size = 2 GiB, LABEL=OferSwap, UUID={SWAP_UUID}\n"
        '[10:00:02] blkid: LABEL="Ofer PC" UUID="1234-ABCD" TYPE="ntfs"\n'
        "[10:00:03] fat label: LABEL=Ofer Home Disk\n"                    # spaces, no quotes
        "[10:00:04] swap UUID=7d1e2f30-aaaa-4bbb-8ccc-0123456789ab,x\n"   # unquoted
        "[10:00:05] vfat volume ABCD-1234 on sda1\n"                      # a FAT volume serial, bare
        "[10:00:06] ntfs 01D9A2B3C4D5E6F7 on sda2\n"                      # an NTFS volume serial, bare
        "[10:00:07] local clone: sda -> sdc\n", encoding="utf-8", newline="\n")
    out = run(tmp_path, f'dmesg() {{ echo "EXT4-fs (sda2): mounted filesystem {PART_GUID} ro"; '
                        'echo "sd 0:0:0:0: [sda] 976773168 512-byte logical blocks"; }; '
                        f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_diag boot; echo "rc=$?"',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    text = stick_files(data)
    for leak in (DISK_GUID, PART_GUID, SWAP_UUID, "OferSwap", "Ofer PC", "1234-ABCD", "Ofer Home Disk",
                 "7d1e2f30-aaaa-4bbb-8ccc-0123456789ab", "ABCD-1234", "01D9A2B3C4D5E6F7"):
        assert leak not in text, (leak, text)
    assert text.count("[שורה הוסרה — מזהה דיסק]") == 8, text   # each line said, not dropped in silence
    assert "local clone: sda -> sdc" in text and "[sda] 976773168 512-byte logical blocks" in text, text


@needs_jq
def test_the_report_keeps_no_identifier_and_stays_json(tmp_path):
    # _lc_gpt_why puts sgdisk's refused line -- every unique GUID, and -U the disk GUID -- in a
    # target's error, and that text goes into the report kept on the stick
    rec, log = curl_recorder(tmp_path)
    root = sysroot(tmp_path, {"sda": [], "sdc": []})
    for d, s in (("sda", "S3Z1NB0K1"), ("sdc", "WX41A1234567")):
        (root / "sys" / "block" / d / "device").mkdir()
        (root / "sys" / "block" / d / "device" / "serial").write_text(s + "\n")
    r = tmp_path / "run" / "lc" / "run"; r.mkdir(parents=True)
    (r / "src").write_text("sda\n"); (r / "targets").write_text("sdc\n")
    (r / "started").write_text("100\n"); (r / "ended").write_text("160\n")
    t = tmp_path / "run" / "targets" / "sdc"; t.mkdir(parents=True)
    (t / "state").write_text("failed\n")
    (t / "error").write_text(f'טבלת המחיצות לא נכתבה: sgdisk refused: -u 3:{PART_GUID} -U {DISK_GUID} -e '
                             'blkid LABEL="Ofer PC"\n', encoding="utf-8", newline="\n")
    data = tmp_path / "usbdata"; data.mkdir()
    out = run(tmp_path, rec + 'LC="$RUN_DIR/lc"; _lr_src=sda; MAC=aa:bb; disk_port() { echo 1; }; '
              'usb_serials_snap sda sdc; '
              f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; lc_report',
              SYSROOT=posix(root), IMAGECTL_MODE="usbclone")
    kept = list((data / "reports").glob("clone-*.json"))
    assert len(kept) == 1, out.stdout + out.stderr
    text = kept[0].read_text(encoding="utf-8")
    report = json.loads(text)   # the scrub must not break the JSON
    for leak in (DISK_GUID, PART_GUID, "Ofer PC"):
        assert leak not in text, (leak, text)
    assert report["targets"][0]["error"] == "[שורה הוסרה — מזהה דיסק]", report
    assert report["targets"][0]["state"] == "failed" and report["elapsed_s"] == 60   # the rest stays
