#!/usr/bin/python3
"""The server's boot screen: from early boot until the DCUI owns tty1.

Owner, 17/10: "no black screen and no screen that is not the GUI". Between
GRUB and the DCUI the server showed a black tty1 with a cursor (non-gui
audit S1) -- the wait for network-online and imagesrv-server alone. This
paints the same static screen the agent's last resort paints
(agent/lib/gui/splash.sh): the ImageSrv symbol and "ImageSrv — מתאתחל…",
rendered once by the GUI binary (``--splash-raw``) into a raw XRGB8888 block,
centred here on a frame of the framebuffer's own size and stride, the rest
filled with the block's corner pixel (the design's background).

Errors stay visible: the kernel console is NOT unbound and tty1 stays in
text mode, so a ``[FAILED]`` line from systemd (``quiet`` = show_status
"error") or a crit kernel line is drawn over the picture, as it should be.
Only the cursor is hidden.

The screen is watched, not just painted once: a DRM driver that loads late
(vmwgfx, hyperv_drm) replaces the firmware framebuffer and the picture with
it, so a changed mode is painted again. The watch ends when tty1 is in
graphics mode (the DCUI's GUI has it -- never paint over it) or after
``WATCH_S``.

Standalone on purpose (no ``server`` imports): it runs before /opt/imagesrv
exists on an ISO's first boot. Exit codes: 0 = painted, or nothing to paint
on (no framebuffer: the DCUI's own text fallback says so); 1 = it should
have painted and did not -- the reason in the journal.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

SPLASH_DIR = Path(os.environ.get("IMAGESRV_SPLASH_DIR", "/usr/share/imagesrv"))
SYSROOT = Path(os.environ.get("IMAGESRV_SYSROOT", "/"))
FB_DEV = Path(os.environ.get("IMAGESRV_FB_DEV", "/dev/fb0"))
TTY = Path(os.environ.get("IMAGESRV_SPLASH_TTY", "/dev/tty1"))
#: How long the framebuffer may take to appear (a DRM module loaded by udev).
FB_WAIT_S = float(os.environ.get("IMAGESRV_SPLASH_FB_WAIT_S", "20"))
#: How long a replaced framebuffer is painted again. network-online alone can
#: take two minutes on a server without a cable.
WATCH_S = float(os.environ.get("IMAGESRV_SPLASH_WATCH_S", "300"))
POLL_S = 0.5
#: Written once the picture is up: firstboot then keeps its progress lines off
#: tty1 (they would draw over it); its failure is still printed there.
MARKER = Path(os.environ.get("IMAGESRV_SPLASH_MARKER", "/run/imagesrv-boot-splash.on"))

KDGETMODE = 0x4B3B
KD_GRAPHICS = 1
HIDE_CURSOR = b"\033[?25l"


class SplashError(Exception):
    """It should have painted and did not; the message says why."""


def log(message: str) -> None:
    print(f"imagesrv-boot-splash: {message}", flush=True)


def find_block(splash_dir: Path) -> tuple[Path, int, int]:
    blocks = sorted(splash_dir.glob("splash-*x*.raw"))
    if not blocks:
        raise SplashError(f"no prebuilt screen in {splash_dir}")
    match = re.fullmatch(r"splash-(\d+)x(\d+)\.raw", blocks[0].name)
    if not match:
        raise SplashError(f"unreadable block name {blocks[0].name}")
    w, h = int(match[1]), int(match[2])
    size = blocks[0].stat().st_size
    if w <= 0 or h <= 0 or size != w * h * 4:
        raise SplashError(f"{blocks[0]} is {size} bytes, not {w}x{h} XRGB8888")
    return blocks[0], w, h


def fb_mode(fb: Path, sysroot: Path) -> tuple[int, int, int, int, str] | None:
    """(width, height, stride, bpp, name) of the framebuffer; None = there is none."""
    sysdir = sysroot / "sys/class/graphics" / fb.name
    if not sysdir.is_dir():
        return None
    try:
        w, h = (int(v) for v in (sysdir / "virtual_size").read_text().strip().split(","))
        stride = int((sysdir / "stride").read_text().strip())
        bpp = int((sysdir / "bits_per_pixel").read_text().strip())
        name = (sysdir / "name").read_text().strip() if (sysdir / "name").exists() else ""
    except (OSError, ValueError) as exc:
        raise SplashError(f"cannot read the mode of {fb}: {exc}") from exc
    return w, h, stride, bpp, name


def build_frame(block: bytes, bw: int, bh: int, w: int, h: int, stride: int) -> bytes:
    """The whole framebuffer: the corner pixel everywhere, the block centred."""
    if bw > w or bh > h or stride < w * 4:
        raise SplashError(f"{bw}x{bh} does not fit {w}x{h} (stride {stride})")
    frame = bytearray(block[:4] * (stride // 4) + block[:stride % 4]) * h
    x, y = (w - bw) // 2 * 4, (h - bh) // 2
    row = bw * 4
    for r in range(bh):
        at = (y + r) * stride + x
        frame[at:at + row] = block[r * row:(r + 1) * row]
    return bytes(frame)


def paint(fb: Path, mode: tuple[int, int, int, int, str], block_path: Path, bw: int, bh: int) -> None:
    w, h, stride, bpp, _ = mode
    # The GUI draws XRGB8888 only; another depth would be a wrong picture.
    if bpp != 32:
        raise SplashError(f"{bpp}bpp framebuffer, only 32 is drawn")
    frame = build_frame(block_path.read_bytes(), bw, bh, w, h, stride)
    try:
        with open(fb, "r+b", buffering=0) as out:
            written = out.write(frame)
    except OSError as exc:
        raise SplashError(f"writing {fb} failed: {exc}") from exc
    if written != len(frame):
        raise SplashError(f"wrote {written} of {len(frame)} bytes to {fb}")


def tty_is_graphics(tty: Path) -> bool | None:
    """True = someone (the DCUI's GUI) has tty1 in KD_GRAPHICS: hands off.
    None = could not ask -- not folded into "text mode" (principle 5)."""
    try:
        fd = os.open(tty, os.O_RDONLY | os.O_NOCTTY)
    except OSError as exc:
        log(f"cannot open {tty} to read its mode: {exc}")
        return None
    try:
        import fcntl   # POSIX only; here so the tests import the module on Windows too
        mode = bytearray(4)
        fcntl.ioctl(fd, KDGETMODE, mode)
        return int.from_bytes(mode, sys.byteorder) == KD_GRAPHICS
    except (OSError, ImportError) as exc:
        log(f"cannot read the mode of {tty}: {exc}")
        return None
    finally:
        os.close(fd)


def hide_cursor(tty: Path, sysroot: Path) -> None:
    # A failure here is a blinking cursor on the picture, not a lost screen:
    # said in the journal, not fatal.
    blink = sysroot / "sys/class/graphics/fbcon/cursor_blink"
    for path, data in ((blink, b"0"), (tty, HIDE_CURSOR)):
        try:
            with open(path, "wb", buffering=0) as out:
                out.write(data)
        except OSError as exc:
            log(f"the cursor may stay visible: {path}: {exc}")


def run(clock=time.monotonic, sleep=time.sleep) -> int:
    started = clock()
    block_path, bw, bh = find_block(SPLASH_DIR)
    mode = fb_mode(FB_DEV, SYSROOT)
    while mode is None and clock() - started < FB_WAIT_S:
        sleep(POLL_S)
        mode = fb_mode(FB_DEV, SYSROOT)
    if mode is None:
        log(f"no framebuffer at {FB_DEV} after {FB_WAIT_S:.0f}s -- the text console stays (nothing to paint on)")
        return 0
    # Unknown (None) here is still painted: this runs long before the DCUI
    # (Before= in the unit), so nobody else can be on the screen yet.
    if tty_is_graphics(TTY):
        log("tty1 is already in graphics mode -- the DCUI has it, nothing painted")
        return 0
    hide_cursor(TTY, SYSROOT)
    paint(FB_DEV, mode, block_path, bw, bh)
    try:
        MARKER.write_text("")
    except OSError as exc:
        log(f"cannot write {MARKER} -- firstboot will print over the picture: {exc}")
    log(f"the boot screen is up ({mode[0]}x{mode[1]}, {mode[4] or FB_DEV.name})")
    while clock() - started < WATCH_S:
        sleep(POLL_S)
        owner = tty_is_graphics(TTY)
        if owner is None:
            log("tty1's mode is unknown -- the watch stops rather than paint over the DCUI")
            return 0
        if owner:
            log("the DCUI has tty1 -- done")
            return 0
        now = fb_mode(FB_DEV, SYSROOT)
        if now is not None and now != mode:
            mode = now
            hide_cursor(TTY, SYSROOT)
            paint(FB_DEV, mode, block_path, bw, bh)
            log(f"the framebuffer changed -- painted again ({mode[0]}x{mode[1]}, {mode[4] or FB_DEV.name})")
    return 0


def main() -> int:
    try:
        return run()
    except SplashError as exc:
        log(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
