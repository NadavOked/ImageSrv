"""נדב 17/10: "אין מסך שחור ואין מסך שאינו גואי" — מסך האתחול של השרת.

מ-GRUB ועד ה-DCUI השרת הציג tty1 שחור עם סמן (מיפוי המסכים, S1), ובאתחול
הראשון גם את שורות ההתקדמות של firstboot באנגלית (S2) ואת יומן הבקשות של
האשף (S4). ‏server/system/bootsplash.py מצייר את אותה תמונה סטטית שהסוכן מצייר
(agent/lib/gui/splash.sh) על ה-framebuffer, ושגיאות נשארות גלויות: ה-console
אינו מנותק מה-framebuffer, ו-stderr של firstboot והאשף עדיין מגיע אליו.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from server.system import bootsplash

REPO = Path(__file__).resolve().parent.parent
UNIT = REPO / "deploy" / "imagesrv-boot-splash.service"
SETUP = REPO / "deploy" / "setup-boot-server.sh"
BOOTSTRAP = REPO / "installer" / "lib" / "bootstrap.sh"
FIRSTBOOT = REPO / "tools" / "iso" / "firstboot.sh"
FIRSTBOOT_UNIT = REPO / "tools" / "iso" / "imagesrv-firstboot.service"
WIZARD_UNIT = REPO / "deploy" / "imagesrv-wizard.service"
SH = shutil.which("sh") or shutil.which("bash")

BG, INK = b"\x10\x20\x30\x00", b"\xaa\xbb\xcc\x00"


def _block(bw: int, bh: int) -> bytes:
    """A block whose corner is the background and whose middle is ink."""
    rows = []
    for y in range(bh):
        rows.append(b"".join(INK if 0 < x < bw - 1 and 0 < y < bh - 1 else BG for x in range(bw)))
    return b"".join(rows)


def _machine(tmp_path: Path, monkeypatch, *, w=8, h=6, stride=None, bpp=32, block=(4, 2),
             fb=True, name="simpledrmdrmfb"):
    stride = stride or w * 4
    splash = tmp_path / "share"
    splash.mkdir()
    (splash / f"splash-{block[0]}x{block[1]}.raw").write_bytes(_block(*block))
    sysfs = tmp_path / "sys/class/graphics/fb0"
    if fb:
        sysfs.mkdir(parents=True)
        (sysfs / "virtual_size").write_text(f"{w},{h}\n")
        (sysfs / "stride").write_text(f"{stride}\n")
        (sysfs / "bits_per_pixel").write_text(f"{bpp}\n")
        (sysfs / "name").write_text(f"{name}\n")
        (tmp_path / "sys/class/graphics/fbcon").mkdir()
    dev = tmp_path / "fb0"
    dev.write_bytes(b"\0" * (stride * h))
    monkeypatch.setattr(bootsplash, "SPLASH_DIR", splash)
    monkeypatch.setattr(bootsplash, "SYSROOT", tmp_path)
    monkeypatch.setattr(bootsplash, "FB_DEV", dev)
    monkeypatch.setattr(bootsplash, "TTY", tmp_path / "tty1")
    monkeypatch.setattr(bootsplash, "MARKER", tmp_path / "splash.on")
    monkeypatch.setattr(bootsplash, "FB_WAIT_S", 1.0)
    monkeypatch.setattr(bootsplash, "WATCH_S", 0.0)
    return dev, sysfs


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_the_frame_is_the_background_with_the_block_centred_on_the_stride():
    block = _block(4, 2)
    frame = bootsplash.build_frame(block, 4, 2, 8, 6, 40)   # 8 px + 8 bytes of padding
    assert len(frame) == 40 * 6
    rows = [frame[r * 40:(r + 1) * 40] for r in range(6)]
    assert rows[0] == BG * 10 and rows[5] == BG * 10
    # rows 2-3 hold the block, two pixels in from the left
    assert rows[2][8:24] == block[:16] and rows[3][8:24] == block[16:]
    assert rows[2][:8] == BG * 2 and rows[2][24:] == BG * 4


def test_a_block_larger_than_the_screen_is_refused_by_name():
    with pytest.raises(bootsplash.SplashError, match="does not fit"):
        bootsplash.build_frame(_block(4, 2), 4, 2, 3, 6, 12)


def test_it_paints_hides_the_cursor_and_writes_the_marker(tmp_path, monkeypatch):
    dev, _ = _machine(tmp_path, monkeypatch)
    monkeypatch.setattr(bootsplash, "tty_is_graphics", lambda tty: False)
    clock = Clock()
    assert bootsplash.run(clock, clock.sleep) == 0
    assert dev.read_bytes() == bootsplash.build_frame(_block(4, 2), 4, 2, 8, 6, 32)
    assert (tmp_path / "splash.on").exists(), "firstboot reads this to keep its lines off tty1"
    assert (tmp_path / "sys/class/graphics/fbcon/cursor_blink").read_bytes() == b"0"
    assert (tmp_path / "tty1").read_bytes() == b"\033[?25l"


def test_no_framebuffer_is_said_and_nothing_is_painted(tmp_path, monkeypatch, capsys):
    _machine(tmp_path, monkeypatch, fb=False)
    clock = Clock()
    assert bootsplash.run(clock, clock.sleep) == 0
    assert "no framebuffer" in capsys.readouterr().out
    assert not (tmp_path / "splash.on").exists()


def test_a_missing_picture_fails_by_name(tmp_path, monkeypatch, capsys):
    _machine(tmp_path, monkeypatch)
    for raw in (tmp_path / "share").iterdir():
        raw.unlink()
    assert bootsplash.main() == 1
    assert "FAILED: no prebuilt screen" in capsys.readouterr().out


def test_a_16bpp_framebuffer_is_refused_not_drawn_wrong(tmp_path, monkeypatch, capsys):
    dev, _ = _machine(tmp_path, monkeypatch, bpp=16)
    monkeypatch.setattr(bootsplash, "tty_is_graphics", lambda tty: False)
    before = dev.read_bytes()
    assert bootsplash.main() == 1
    assert "16bpp" in capsys.readouterr().out
    assert dev.read_bytes() == before


def test_a_replaced_framebuffer_is_painted_again_until_the_dcui_has_tty1(tmp_path, monkeypatch):
    dev, sysfs = _machine(tmp_path, monkeypatch)
    monkeypatch.setattr(bootsplash, "WATCH_S", 60.0)
    owner = iter([False, False, False, True])   # first paint, two watch ticks, then the DCUI
    swapped = []

    def tty(_):
        state = next(owner)
        if not swapped and dev.read_bytes() != b"\0" * (32 * 6):
            swapped.append(1)
            # the DRM driver took over: a new, larger mode and a blank screen
            (sysfs / "virtual_size").write_text("10,6\n")
            (sysfs / "stride").write_text("40\n")
            (sysfs / "name").write_text("vmwgfxdrmfb\n")
            dev.write_bytes(b"\0" * (40 * 6))
        return state

    monkeypatch.setattr(bootsplash, "tty_is_graphics", tty)
    clock = Clock()
    assert bootsplash.run(clock, clock.sleep) == 0
    assert dev.read_bytes() == bootsplash.build_frame(_block(4, 2), 4, 2, 10, 6, 40)


def test_an_unknown_tty_mode_stops_the_watch_instead_of_painting_over_the_dcui(tmp_path, monkeypatch, capsys):
    dev, sysfs = _machine(tmp_path, monkeypatch)
    monkeypatch.setattr(bootsplash, "WATCH_S", 60.0)
    answers = iter([False, None])
    monkeypatch.setattr(bootsplash, "tty_is_graphics", lambda tty: next(answers))
    clock = Clock()
    assert bootsplash.run(clock, clock.sleep) == 0
    assert "the watch stops" in capsys.readouterr().out


def test_the_dcui_already_on_tty1_is_never_painted_over(tmp_path, monkeypatch):
    dev, _ = _machine(tmp_path, monkeypatch)
    monkeypatch.setattr(bootsplash, "tty_is_graphics", lambda tty: True)
    clock = Clock()
    assert bootsplash.run(clock, clock.sleep) == 0
    assert dev.read_bytes() == b"\0" * (32 * 6)


def test_the_text_console_is_not_unbound_so_errors_stay_visible():
    code = (REPO / "server" / "system" / "bootsplash.py").read_text(encoding="utf-8")
    assert "vtconsole" not in code and "KDSETMODE" not in code, \
        "a [FAILED] line or a crit kernel line must still be drawn over the picture"


def test_the_unit_runs_early_before_the_screens_and_starts_nothing():
    unit = UNIT.read_text(encoding="utf-8")
    assert "DefaultDependencies=no" in unit
    before = re.search(r"^Before=(.*)$", unit, re.M)[1].split()
    for name in ("sysinit.target", "imagesrv-dcui.service", "imagesrv-firstboot.service",
                 "imagesrv-installer-gui.service"):
        assert name in before
    assert "WantedBy=sysinit.target" in unit
    assert "ExecStart=/usr/bin/python3 -I /usr/local/sbin/imagesrv-boot-splash" in unit
    # a silent skip would be a black screen nobody can explain (principle 5)
    assert "ConditionPath" not in unit
    assert "Requires=" not in unit and "Wants=" not in unit


def test_setup_installs_renders_and_enables_the_boot_screen():
    setup = SETUP.read_text(encoding="utf-8")
    assert 'run install -D -m 0755 "$APP_DIR/server/system/bootsplash.py" /usr/local/sbin/imagesrv-boot-splash' in setup
    assert '"$APP_DIR/deploy/imagesrv-boot-splash.service"' in setup
    assert 'run "$SPLASH_GUI" --splash-raw "$SPLASH_RAW" --size 640x240' in setup
    assert setup.count("run systemctl enable imagesrv-boot-splash.service") == 1
    render = setup[setup.index('if [[ -x "$SPLASH_GUI" ]]'):setup.index("# ‏#1195")]
    assert render.count("warn ") == 2, "both ways of having no picture are said"


def _bootstrap_harness(tmp_path: Path, *, picture: bool):
    cdrom, target, live = tmp_path / "cdrom", tmp_path / "target", tmp_path / "live"
    for rel in ("server/system/bootsplash.py", "deploy/imagesrv-boot-splash.service"):
        (cdrom / "imagesrv-src" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, cdrom / "imagesrv-src" / rel)
    live.mkdir()
    if picture:
        (live / "splash-640x240.raw").write_bytes(b"\0" * 16)
    target.mkdir()
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "DRY_RUN=0\n"
        'must_run() { _c=$1; _m=$2; shift 2; "$@" || fail "$_c" "$_m"; }\n'
        "fail() { printf 'FAIL [%s] %s\\n' \"$1\" \"$2\"; exit 2; }\n"
        "progress() { printf 'PROGRESS %s\\n' \"$4\"; }\n"
        "chroot() { printf 'CHROOT %s\\n' \"$*\"; }\n"
        f'. "{BOOTSTRAP.as_posix()}"\n'
        "install_boot_splash\n", newline="\n")
    env = {**os.environ, "CDROM": cdrom.as_posix(), "TARGET": target.as_posix(),
           "SPLASH_SRC_DIR": live.as_posix()}
    got = subprocess.run([SH, str(harness)], text=True, capture_output=True, env=env,
                         cwd=tmp_path, stdin=subprocess.DEVNULL)
    return got, target


@pytest.mark.skipif(SH is None, reason="no POSIX shell")
def test_the_live_installer_puts_the_boot_screen_on_the_target(tmp_path):
    got, target = _bootstrap_harness(tmp_path, picture=True)
    assert got.returncode == 0, got.stdout + got.stderr
    assert (target / "usr/local/sbin/imagesrv-boot-splash").is_file()
    assert (target / "etc/systemd/system/imagesrv-boot-splash.service").is_file()
    assert (target / "usr/share/imagesrv/splash-640x240.raw").stat().st_size == 16
    assert f"CHROOT {target.as_posix()} systemctl enable imagesrv-boot-splash.service" in got.stdout


@pytest.mark.skipif(SH is None, reason="no POSIX shell")
def test_an_installer_image_without_the_picture_says_so_and_goes_on(tmp_path):
    got, target = _bootstrap_harness(tmp_path, picture=False)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "PROGRESS no boot screen picture in the installer image" in got.stdout
    assert not (target / "usr/share/imagesrv").exists()


def test_bootstrap_installs_the_boot_screen_before_the_first_boot():
    code = BOOTSTRAP.read_text(encoding="utf-8")
    assert code.index("systemctl enable imagesrv-firstboot.service") < code.index("    install_boot_splash\n")


def test_firstboot_keeps_progress_off_the_picture_and_failures_on_tty1():
    code = FIRSTBOOT.read_text(encoding="utf-8")
    assert "SPLASH_MARKER=${IMAGESRV_SPLASH_MARKER:-/run/imagesrv-boot-splash.on}" in code
    assert 'if [[ -e "$SPLASH_MARKER" ]]; then TTY_PROGRESS=0; fi' in code
    assert code.index("TTY_PROGRESS=0; fi") < code.index("log() {")
    failure = code[code.index("fail() {"):code.index("unexpected_failure() {")]
    assert "TTY_PROGRESS" not in failure and '> "$FIRSTBOOT_TTY"' in failure, \
        "a failure is printed on tty1 whether the picture is up or not"
    unit = FIRSTBOOT_UNIT.read_text(encoding="utf-8")
    assert "StandardOutput=journal\n" in unit and "StandardError=journal+console" in unit


def test_the_wizard_request_log_stays_off_the_console_and_its_errors_do_not():
    unit = WIZARD_UNIT.read_text(encoding="utf-8")
    assert "StandardOutput=journal\n" in unit
    assert "StandardError=journal+console" in unit
