"""אין מסך שחור בלי גואי (הכרעת נדב, 17/10).

1. גואי שמת מופעל מחדש — ‏`GUI_RESPAWN_MAX` פעמים באתחול אחד
   (‏guiparent.sh). קודם: מת פעם אחת, ‏`_gui_disabled=1` עד האתחול הבא.
   מאז #1726 מפעיל אותו מחדש `gui_keep`, ולא הקריאה הבאה ל-`gui_parent`
   (‏test_gui_keeper_1726.py).
2. כשגם ההפעלה מחדש נכשלת — מסך סטטי בעיצוב שלנו נצבע ישר על
   ‏`/dev/fb0` (‏splash.sh), מבלוק raw שהגואי עצמו מרנדר בזמן הבנייה
   (‏`--splash-raw`). לא טקסט ולא שחור.
3. כשהגואי חי (או המסך הסטטי), ‏`log()` אינו כותב ל-tty0 — היומן והשרת
   כרגיל. בלי גואי: כמו היום.
4. ‏11 קריאות ה-log עם עברית קשיחה (‏non-gui-screens-audit.md, ‏17/10) הן
   קריאות `log` — ולכן סעיף 3 מוריד אותן מהמסך בלי לגעת בהן.
"""

from __future__ import annotations

import os
import shlex
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix
from agentlib import flat_lib

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

LIB = flat_lib()
GUI = REPO / "native-gui"


def _sh(tmp_path: Path, body: str, shell: list[str] | None = None,
        env_path: str | None = None) -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    script = (
        f"RUN_DIR={shlex.quote(posix(run))}; export RUN_DIR; IMAGESRV_TEST=1; "
        f"SYSROOT={shlex.quote(posix(tmp_path / 'sysroot'))}; "
        f". {posix(LIB)}/common.sh; . {posix(LIB)}/waits.sh; . {posix(LIB)}/ui.sh; "
        f". {posix(LIB)}/localboot.sh; . {posix(LIB)}/guiparent.sh; . {posix(LIB)}/splash.sh; "
        'sleep() { :; }; build_hello() { printf %s "{}"; }; http_post_json() { :; }; '
        + body
    )
    path = env_path or "/usr/bin:/bin"
    return subprocess.run(
        (shell or [BASH, "-c"]) + [f'PATH="{path}:$PATH"; export PATH; ' + script],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        stdin=subprocess.DEVNULL, timeout=120)


def _log(tmp_path: Path) -> str:
    f = tmp_path / "run" / "agent.log"
    return f.read_text(encoding="utf-8") if f.exists() else ""


# --- a fake framebuffer -------------------------------------------------------


def _fb(tmp_path: Path, w: int, h: int, stride: int, bpp: int = 32) -> Path:
    gfx = tmp_path / "sysroot" / "sys" / "class" / "graphics" / "fb0"
    gfx.mkdir(parents=True, exist_ok=True)
    (gfx / "virtual_size").write_text(f"{w},{h}\n", newline="\n")
    (gfx / "stride").write_text(f"{stride}\n", newline="\n")
    (gfx / "bits_per_pixel").write_text(f"{bpp}\n", newline="\n")
    for n, name, bind in ((0, "(S) dummy device", "1"), (1, "(M) frame buffer device", "1")):
        vc = tmp_path / "sysroot" / "sys" / "class" / "vtconsole" / f"vtcon{n}"
        vc.mkdir(parents=True, exist_ok=True)
        (vc / "name").write_text(name + "\n", newline="\n")
        (vc / "bind").write_text(bind + "\n", newline="\n")
    fb = tmp_path / "fb0"
    fb.write_bytes(b"\x00" * (stride * h))         # black, like a screen nobody drew on
    return fb


def _block(tmp_path: Path, bw: int, bh: int) -> tuple[Path, bytes]:
    """A raw block whose corner is the background and whose every other
    pixel is distinct -- a row placed one pixel off cannot pass."""
    d = tmp_path / "share"
    d.mkdir(exist_ok=True)
    px = bytearray()
    for y in range(bh):
        for x in range(bw):
            px += b"\x10\x20\x30\x00" if (x, y) == (0, 0) else struct.pack("<I", 0x00A00000 + y * 256 + x)
    (d / f"splash-{bw}x{bh}.raw").write_bytes(bytes(px))
    return d, bytes(px)


def _expected(raw: bytes, bw: int, bh: int, w: int, h: int, stride: int) -> bytes:
    frame = bytearray(raw[:4] * (stride * h // 4))
    x0, y0 = (w - bw) // 2, (h - bh) // 2
    for r in range(bh):
        off = (y0 + r) * stride + x0 * 4
        frame[off:off + bw * 4] = raw[r * bw * 4:(r + 1) * bw * 4]
    return bytes(frame)


def _env(tmp_path: Path, fb: Path, share: Path) -> str:
    return (f"FB_DEV={shlex.quote(posix(fb))}; SPLASH_DIR={shlex.quote(posix(share))}; ")


def _busybox_path(tmp_path: Path) -> str:
    """A PATH where dd/head/cat/wc/... are busybox's own -- the tools the
    initramfs runs (busybox-static, applet links in /bin)."""
    bb = shutil.which("busybox")
    d = tmp_path / "bb"
    d.mkdir(exist_ok=True)
    for applet in ("dd", "head", "cat", "wc", "rm", "grep", "mv", "date", "sleep"):
        (d / applet).symlink_to(bb)
    return posix(d)


SHELLS = [
    pytest.param("bash", id="bash+coreutils"),
    pytest.param("busybox", id="busybox-ash",
                 marks=requires_native(("busybox", shutil.which("busybox")), posix=True,
                                       why="the initramfs shell and dd")),
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("w,h,stride", [(16, 8, 64), (17, 9, 80)], ids=["exact", "padded-stride"])
def test_the_static_screen_is_the_block_centred_on_its_background(tmp_path, shell, w, h, stride):
    fb = _fb(tmp_path, w, h, stride)
    share, raw = _block(tmp_path, 6, 3)
    sh_cmd = [shutil.which("busybox"), "sh", "-c"] if shell == "busybox" else None
    env_path = _busybox_path(tmp_path) if shell == "busybox" else None
    p = _sh(tmp_path, _env(tmp_path, fb, share) + 'splash_draw; echo "rc=$?"', sh_cmd, env_path)
    assert "rc=0" in p.stdout, (p.stdout, p.stderr, _log(tmp_path))
    assert fb.read_bytes() == _expected(raw, 6, 3, w, h, stride)
    # The text console no longer draws over it; the dummy console is untouched.
    vt = tmp_path / "sysroot" / "sys" / "class" / "vtconsole"
    assert (vt / "vtcon1" / "bind").read_text().strip() == "0"
    assert (vt / "vtcon0" / "bind").read_text().strip() == "1"
    assert (tmp_path / "run" / "splash.on").exists()
    assert not (tmp_path / "run" / "splash.frame").exists()
    assert "imagesrv:" not in p.stdout                 # its own log line stays off the screen


@pytest.mark.parametrize("case", ["no-raw", "16bpp", "wrong-size-raw", "no-fb"])
def test_the_static_screen_refuses_what_it_cannot_draw_right(tmp_path, case):
    """Positive evidence or nothing: a wrong picture is not drawn and the
    refusal says why (principle 5) -- and the marker that would silence
    log() is not left behind."""
    fb = _fb(tmp_path, 16, 8, 64, bpp=16 if case == "16bpp" else 32)
    share, _raw = _block(tmp_path, 6, 3)
    if case == "no-raw":
        for f in share.iterdir():
            f.unlink()
    if case == "wrong-size-raw":
        (share / "splash-6x3.raw").write_bytes(b"\x01" * 10)
    before = fb.read_bytes()
    target = tmp_path / "nofb" if case == "no-fb" else fb
    p = _sh(tmp_path, _env(tmp_path, target, share) + 'splash_draw; echo "rc=$?"')
    assert "rc=1" in p.stdout, p.stdout
    assert fb.read_bytes() == before
    assert not (tmp_path / "run" / "splash.on").exists()
    assert not (tmp_path / "nofb").exists()
    assert "splash:" in _log(tmp_path)


# --- 1+2: restart, then the static screen ------------------------------------


def _fake_gui(tmp_path: Path, station_rc: int = 0) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    kiosk = bindir / "kiosk"
    kiosk.write_text(f'#!/bin/sh\necho started >> "{posix(tmp_path / "kiosk.runs")}"\nexit 0\n',
                     newline="\n")
    station = bindir / "station"
    station.write_text(f"#!/bin/sh\nexit {station_rc}\n", newline="\n")
    for f in (kiosk, station):
        f.chmod(0o755)
    return (f"GUI_KIOSK={shlex.quote(posix(kiosk))}; GUI_STATION={shlex.quote(posix(station))}; "
            "WAIT_HELPER_S=5; D_ROLE=build; ")


def _runs(tmp_path: Path) -> int:
    f = tmp_path / "kiosk.runs"
    return len(f.read_text().splitlines()) if f.exists() else 0


@requires_native(posix=True, why="chmod +x and #! scripts")
def test_a_gui_that_dies_is_started_again_then_the_static_screen(tmp_path):
    fb = _fb(tmp_path, 16, 8, 64)
    share, raw = _block(tmp_path, 6, 3)
    # #1726: the keeper (gui_keep) restarts it by itself, not the next gui_parent;
    # gui_parent hears that it gave up once the keeper has ended.
    p = _sh(tmp_path, _fake_gui(tmp_path) + _env(tmp_path, fb, share) + "GUI_RESPAWN_MAX=2; "
            'for i in 1 2 3; do gui_parent; echo "call$i=$?"; '
            '[ -z "$_gui_pid" ] || wait "$_gui_pid"; done')
    out, log = p.stdout, _log(tmp_path)
    # started, died, started again twice -- three kiosks -- then given up.
    assert ["call1=0", "call2=1", "call3=1"] == \
        [ln for ln in out.splitlines() if ln.startswith("call")], (out, p.stderr, log)
    assert _runs(tmp_path) == 3
    assert "starting it again (1 of 2)" in log and "starting it again (2 of 2)" in log
    assert "after 2 restarts -- the static screen instead" in log
    # the double failure: our screen on the framebuffer, not black, not text
    assert fb.read_bytes() == _expected(raw, 6, 3, 16, 8, 64)
    assert not (tmp_path / "run" / "gui.pid").exists()


@requires_native(posix=True, why="chmod +x and #! scripts")
def test_a_gui_whose_libraries_do_not_load_gets_the_static_screen_at_once(tmp_path):
    fb = _fb(tmp_path, 16, 8, 64)
    share, raw = _block(tmp_path, 6, 3)
    p = _sh(tmp_path, _fake_gui(tmp_path, station_rc=127) + _env(tmp_path, fb, share)
            + 'gui_parent; echo "rc=$?"')
    assert "rc=1" in p.stdout
    assert _runs(tmp_path) == 0
    assert fb.read_bytes() == _expected(raw, 6, 3, 16, 8, 64)
    assert "native GUI libraries unavailable" in _log(tmp_path)


@requires_native(posix=True, why="chmod +x and #! scripts")
def test_the_build_screen_with_the_static_screen_up_writes_no_text(tmp_path):
    """gui_unavailable_local after a double failure: the static screen stays,
    no "The graphical interface did not start." over it; still the reboot
    to the local disk (principle 1)."""
    fb = _fb(tmp_path, 16, 8, 64)
    share, _raw = _block(tmp_path, 6, 3)
    p = _sh(tmp_path, _fake_gui(tmp_path, station_rc=127) + _env(tmp_path, fb, share)
            + "gui_parent || gui_unavailable_local build")
    assert p.returncode == 86, (p.stdout, p.stderr)
    assert "TEST-REBOOT: native GUI unavailable (build screen)" in p.stdout
    assert "The graphical interface did not start." not in p.stdout
    assert "imagesrv: FATAL" not in p.stdout          # the reboot line stays off our screen
    assert "FATAL: native GUI unavailable" in _log(tmp_path)


# --- 3: log() and the screen -------------------------------------------------


@pytest.mark.parametrize("owner,on_screen", [
    ("none", True), ("dead-gui", True), ("live-gui", False), ("splash", False)])
def test_log_writes_the_tty_only_when_no_screen_of_ours_owns_it(tmp_path, owner, on_screen):
    setup = {
        "none": "",
        "dead-gui": '(exit 0) & _d=$!; wait $_d; echo "$_d" > "$RUN_DIR/gui.pid"; ',
        "live-gui": 'sleep_real() { command sleep "$@"; }; sleep_real 30 & _l=$!; '
                    'echo "$_l" > "$RUN_DIR/gui.pid"; ',
        "splash": ': > "$RUN_DIR/splash.on"; ',
    }[owner]
    tail = 'kill "$_l"' if owner == "live-gui" else ":"
    p = _sh(tmp_path, setup + 'log "פג הזמן: המגירה sda לא הסתיים"; '
            '( log "from a subshell" ); ' + tail)
    assert ("imagesrv: פג הזמן" in p.stdout) is on_screen, p.stdout
    assert ("imagesrv: from a subshell" in p.stdout) is on_screen, p.stdout
    log = _log(tmp_path)
    assert "פג הזמן: המגירה sda לא הסתיים" in log and "from a subshell" in log


#: ‏non-gui-screens-audit.md (17/10), סעיף 1: עברית קשיחה בקריאות log.
#: (קובץ, קטע מהשורה) — לא מספר שורה, שזז.
HEBREW_LOGS = [
    ("bootca.sh", '"boot_ca: $_p נקרא"'),
    ("bootca.sh", '"boot_ca: $_p -- לא הצלחנו לקרוא את החתימה"'),
    ("grow.sh", '"$2: e2fsck לא החזיר קוד יציאה"'),
    ("grow.sh", '"$2: e2fsck נכשל (rc=$_ck)"'),
    ("grow.sh", '"$2: resize2fs נכשל (rc=${_rc:-לא נרשם})"'),
    ("grow.sh", '"$2: xfs_growfs נכשל (rc=${_rc:-לא נרשם})"'),
    ("grow.sh", '"אין כלי הרחבה ל-$1'),
    ("restore.sh", '"partition $5 ($7): ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם"'),
    ("waits.sh", '"פג הזמן: $1 לא ענה תוך $_cr_max שניות'),
    ("waits.sh", '"פג הזמן: $_w_what לא הסתיים תוך $_w_max שניות -- נכשל"'),
    ("waits.sh", '"פג הזמן: $_p_what -- $_p_max שניות בלי התקדמות'),
]


@pytest.mark.parametrize("name,fragment", HEBREW_LOGS)
def test_the_hard_coded_hebrew_lines_reach_the_screen_only_through_log(name, fragment):
    lines = [ln for ln in (LIB / name).read_text(encoding="utf-8").splitlines() if fragment in ln]
    assert len(lines) == 1, (name, fragment, lines)
    # log, and only log, writes the line -- so log() decides whether the tty sees it.
    assert "log " + fragment in lines[0], lines[0]


# --- the picture itself (C) and the build -------------------------------------


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs], stdin=subprocess.DEVNULL).returncode == 0


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui is built on the lab",
)
def test_the_gui_renders_the_static_screen_in_our_design(tmp_path):
    binary = tmp_path / "gui-bin"
    build = subprocess.run(
        [BASH, "-c",
         'set -e; cd "$1"; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE '
         '$(pkg-config --cflags pangocairo cairo libdrm) -o "$2" $(make -s -f Makefile print-gui-src) '
         '$(pkg-config --libs pangocairo cairo libdrm) -lm',
         "_", posix(GUI), posix(binary)],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    out = tmp_path / "splash-640x240.raw"
    run = subprocess.run([str(binary), "--splash-raw", str(out), "--size", "640x240"],
                         capture_output=True, text=True, timeout=60, env=env,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    raw = out.read_bytes()
    assert len(raw) == 640 * 240 * 4
    px = [raw[i:i + 3] for i in range(0, len(raw), 4)]     # B G R of XRGB8888
    porcelain, blue, ink = bytes.fromhex("F5F1EE"), bytes.fromhex("B87900"), bytes.fromhex("313131")
    for corner in (0, 639, 640 * 239, 640 * 240 - 1):     # splash.sh fills the screen with this
        assert px[corner] == porcelain
    assert px.count(blue) > 500, "the ImageSrv symbol is not drawn"
    assert px.count(ink) > 200, "the line under the symbol is not drawn"
    assert px.count(porcelain) < len(px) - 1000


def test_the_build_renders_the_static_screen_into_the_gui_image():
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    gui = builder[builder.index('if [ "$WITH_GUI" -eq 1 ]; then'):]
    gui = gui[:gui.index("# --- kernel modules and firmware")]
    assert '"$GUI_BIN" --splash-raw "$SPLASH_RAW" --size "${SPLASH_W}x$SPLASH_H"' in gui
    assert 'SPLASH_RAW="$ROOT/usr/share/imagesrv/splash-${SPLASH_W}x$SPLASH_H.raw"' in gui
    splash = (LIB / "splash.sh").read_text(encoding="utf-8")
    assert 'SPLASH_DIR="${SPLASH_DIR:-/usr/share/imagesrv}"' in splash
    assert '"$SPLASH_DIR"/splash-*x*.raw' in splash
