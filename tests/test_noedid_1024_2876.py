"""#2876: a machine behind a KVM switch gets no EDID and the GUI came up at
800x600 or 640x480 -- under the 1024x768 the screens are built for.

The agent's GUI draws on /dev/fb0 (fbdev, #641), whose size the kernel picks.
With no EDID the connector lists only the kernel's fallback modes
(``drm_add_modes_noedid``: up to 1024x768, none preferred; on DP 640x480 is
marked preferred). Now, when fb0 comes up under 1024x768 and the connector
has no EDID mode of 1024x768 or more, the GUI sets 1024x768 itself over KMS.
A display that said its size keeps exactly what it had -- fbdev, and not even
a DRM probe when fb0 is already 1024x768 or more.

Run here as ``backend.c`` over a stub: /dev/fb0 answers FBIOGET_*SCREENINFO
with FAKE_FB, and a stub libdrm has one connector whose modes are FAKE_MODES
(``*`` = the EDID's preferred mode). Nothing touches the boot path: GRUB and
the kernel command line are unchanged (#1663).

The display on the metal (i915, a real KVM, BIOS and UEFI) is proven there,
not here -- the PR lists what to look at.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
GUI_DIR = REPO / "native-gui"


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs], stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("bash", shutil.which("bash")),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("cairo/libdrm", _pkgconfig("cairo", "libdrm")),
    posix=True, why="backend.c is built on Linux",
)
CFLAGS = "-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE"
WRAP = "-Wl,--wrap=open -Wl,--wrap=mmap -Wl,--wrap=ioctl"

STUB = r'''
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <linux/fb.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

static int card = -1, fb = -1;

/* /dev/fb0 and /dev/dri/card0 exist (unless FAKE_NO_KMS); no tty */
int __real_open(const char *path, int flags, ...);
int __wrap_open(const char *path, int flags, ...) {
    if (!strcmp(path, "/dev/fb0")) return fb = __real_open("/dev/null", O_RDWR);
    if (!strcmp(path, "/dev/dri/card0") && !getenv("FAKE_NO_KMS")) return card = __real_open("/dev/null", O_RDWR);
    if (!strncmp(path, "/dev/", 5)) { errno = ENOENT; return -1; }
    va_list ap; va_start(ap, flags); int mode = va_arg(ap, int); va_end(ap);
    return __real_open(path, flags, mode);
}
void *__real_mmap(void *a, size_t n, int prot, int fl, int fd, off_t off);
void *__wrap_mmap(void *a, size_t n, int prot, int fl, int fd, off_t off) {
    if (fd >= 0 && (fd == card || fd == fb))
        return __real_mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return __real_mmap(a, n, prot, fl, fd, off);
}
int __real_ioctl(int fd, unsigned long req, ...);
int __wrap_ioctl(int fd, unsigned long req, ...) {
    va_list ap; va_start(ap, req); void *arg = va_arg(ap, void *); va_end(ap);
    if (fd >= 0 && fd == fb) {
        unsigned w = 0, h = 0;
        sscanf(getenv("FAKE_FB"), "%ux%u", &w, &h);
        if (req == FBIOGET_VSCREENINFO) {
            struct fb_var_screeninfo *v = arg; memset(v, 0, sizeof *v);
            v->xres = v->xres_virtual = w; v->yres = v->yres_virtual = h; v->bits_per_pixel = 32;
            v->red.offset = 16; v->green.offset = 8; v->blue.offset = 0;
            return 0;
        }
        if (req == FBIOGET_FSCREENINFO) {
            struct fb_fix_screeninfo *f = arg; memset(f, 0, sizeof *f);
            f->line_length = w * 4; f->smem_len = w * 4 * h;
            return 0;
        }
        return 0;
    }
    return __real_ioctl(fd, req, arg);
}

/* FAKE_MODES: "*1920x1080@60,1024x768@60" -- '*' marks the EDID's preferred mode */
static int parse_modes(drmModeModeInfo **out) {
    const char *s = getenv("FAKE_MODES");
    int n = 0; drmModeModeInfo *m = calloc(16, sizeof *m);
    while (s && *s && n < 16) {
        int pref = *s == '*'; if (pref) s++;
        unsigned w, h, r; int used = 0;
        if (sscanf(s, "%ux%u@%u%n", &w, &h, &r, &used) != 3) break;
        m[n].hdisplay = w; m[n].vdisplay = h; m[n].vrefresh = r;
        m[n].type = DRM_MODE_TYPE_DRIVER | (pref ? DRM_MODE_TYPE_PREFERRED : 0);
        n++; s += used; if (*s == ',') s++;
    }
    *out = m; return n;
}
static uint32_t conns[1] = { 30 }, crtcs[1] = { 40 };
drmModeResPtr drmModeGetResources(int fd) {
    (void)fd;
    drmModeResPtr r = calloc(1, sizeof *r);
    r->count_connectors = 1; r->connectors = conns; r->count_crtcs = 1; r->crtcs = crtcs;
    return r;
}
void drmModeFreeResources(drmModeResPtr r) { free(r); }
drmModeConnectorPtr drmModeGetConnector(int fd, uint32_t id) {
    (void)fd;
    printf("probe conn=%u\n", id);
    drmModeConnectorPtr c = calloc(1, sizeof *c);
    c->connector_id = id; c->connection = DRM_MODE_CONNECTED; c->encoder_id = 50;
    c->count_modes = parse_modes(&c->modes);
    return c;
}
drmModeConnectorPtr drmModeGetConnectorCurrent(int fd, uint32_t id) { return drmModeGetConnector(fd, id); }
void drmModeFreeConnector(drmModeConnectorPtr c) { free(c->modes); free(c); }
drmModeEncoderPtr drmModeGetEncoder(int fd, uint32_t id) {
    (void)fd;
    drmModeEncoderPtr e = calloc(1, sizeof *e);
    e->encoder_id = id; e->crtc_id = 40; e->possible_crtcs = 1;
    return e;
}
void drmModeFreeEncoder(drmModeEncoderPtr e) { free(e); }
int drmIoctl(int fd, unsigned long req, void *arg) {
    (void)fd;
    if (req == DRM_IOCTL_MODE_CREATE_DUMB) {
        struct drm_mode_create_dumb *c = arg;
        c->pitch = c->width * 4; c->size = (uint64_t)c->pitch * c->height; c->handle = 7;
    } else if (req == DRM_IOCTL_MODE_MAP_DUMB) {
        ((struct drm_mode_map_dumb *)arg)->offset = 0;
    }
    return 0;
}
int drmModeAddFB(int fd, uint32_t w, uint32_t h, uint8_t depth, uint8_t bpp, uint32_t pitch,
                 uint32_t bo, uint32_t *id) {
    (void)fd; (void)w; (void)h; (void)depth; (void)bpp; (void)pitch; (void)bo;
    *id = 52;
    return 0;
}
int drmModeRmFB(int fd, uint32_t id) { (void)fd; (void)id; return 0; }
drmModeCrtcPtr drmModeGetCrtc(int fd, uint32_t id) {
    (void)fd;
    drmModeCrtcPtr c = calloc(1, sizeof *c);
    c->crtc_id = id; c->mode_valid = 1; c->buffer_id = 99;    /* fbdev's own fb, restored on close */
    return c;
}
void drmModeFreeCrtc(drmModeCrtcPtr c) { free(c); }
int drmModeSetCrtc(int fd, uint32_t crtc, uint32_t fbid, uint32_t x, uint32_t y, uint32_t *conn,
                   int count, drmModeModeInfoPtr mode) {
    (void)fd; (void)crtc; (void)x; (void)y; (void)conn; (void)count;
    if (fbid == 52 && getenv("FAKE_SETCRTC_BUSY")) { errno = EACCES; return -1; }
    if (fbid == 52) printf("setcrtc %ux%u@%u\n", mode->hdisplay, mode->vdisplay, mode->vrefresh);
    return 0;
}
int drmModeDirtyFB(int fd, uint32_t f, drmModeClipPtr clips, uint32_t n) { (void)fd; (void)f; (void)clips; (void)n; return 0; }
int drmSetMaster(int fd) { (void)fd; return 0; }
int drmDropMaster(int fd) { (void)fd; return 0; }
'''

OPEN = r'''
#include <stdio.h>
#include "backend.h"
int main(void) {
    char err[512] = "";
    Backend *b = backend_open("fbdev", NULL, err, sizeof err);
    if (!b) { printf("open: %s\n", err); return 1; }
    backend_present(b);
    printf("display %dx%d\n", backend_width(b), backend_height(b));
    backend_close(b);
    return 0;
}
'''

NOEDID = "1024x768@75,1024x768@60,1024x768@70,800x600@60,640x480@60"   # drm_add_modes_noedid, sorted


def _binary(tmp_path: Path) -> Path:
    (tmp_path / "stub.c").write_text(STUB, encoding="utf-8")
    (tmp_path / "open.c").write_text(OPEN, encoding="utf-8")
    binary = tmp_path / "open"
    command = (f'set -e; cd "$1"; cc {CFLAGS} -Isrc $(pkg-config --cflags cairo libdrm) {WRAP} '
               f'-o "$2" "$3" "$4" src/backend.c $(pkg-config --libs cairo) -lm')
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI_DIR), str(binary),
                           str(tmp_path / "open.c"), str(tmp_path / "stub.c")],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return binary


def _open(binary: Path, fb: str, modes: str, **env: str) -> tuple[list[str], str]:
    proc = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                          env={**os.environ, "FAKE_FB": fb, "FAKE_MODES": modes, **env})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.splitlines(), proc.stderr


@NATIVE
def test_no_edid_at_800x600_comes_up_at_1024x768(tmp_path):
    """The KVM case: fb0 800x600, the kernel's fallback modes only -> KMS 1024x768 at 60 Hz, and the log says why."""
    out, log = _open(_binary(tmp_path), "800x600", NOEDID)
    assert out == ["probe conn=30", "setcrtc 1024x768@60", "display 1024x768"]
    assert "no EDID mode of 1024x768 or more -- KMS 1024x768@60 instead (#2876)" in log
    assert "fb0 800x600" in log


@NATIVE
def test_dp_without_edid_640x480_preferred_comes_up_at_1024x768(tmp_path):
    """DP with no EDID: the kernel marks 640x480 preferred (DP CTS) -- under 1024x768, so raised too."""
    out, _ = _open(_binary(tmp_path), "640x480", "1024x768@60,800x600@60,*640x480@60")
    assert out == ["probe conn=30", "setcrtc 1024x768@60", "display 1024x768"]


@NATIVE
def test_edid_1920x1080_is_left_alone_without_a_probe(tmp_path):
    """A monitor that sent its EDID: fbdev at its own size, and no DRM probe at all."""
    out, log = _open(_binary(tmp_path), "1920x1080", "*1920x1080@60,1280x1024@60,1024x768@60")
    assert out == ["display 1920x1080"]
    assert "#2876" not in log


@NATIVE
def test_edid_1920x1080_with_a_small_fb0_is_still_left_alone(tmp_path):
    """Even if fb0 were small, an EDID mode of 1024x768 or more means the display said its size: no modeset."""
    out, log = _open(_binary(tmp_path), "800x600", "*1920x1080@60,1024x768@60,800x600@60")
    assert out == ["probe conn=30", "display 800x600"]
    assert "fb0 800x600 kept -- connector 30: EDID mode 1920x1080, the display's own size (#2876)" in log


@NATIVE
def test_edid_800x600_only_is_left_alone(tmp_path):
    """An EDID that offers only 800x600: no 1024x768 to choose, so fb0 stays as the kernel set it."""
    out, log = _open(_binary(tmp_path), "800x600", "*800x600@60,640x480@60")
    assert out == ["probe conn=30", "display 800x600"]
    assert "fb0 800x600 kept -- connector 30: EDID mode 800x600, no 1024x768 to raise to (#2876)" in log


@NATIVE
def test_a_refused_modeset_keeps_fb0(tmp_path):
    """Another DRM master (an earlier GUI still exiting): the modeset is refused, fb0 stays and is drawn on."""
    out, log = _open(_binary(tmp_path), "800x600", NOEDID, FAKE_SETCRTC_BUSY="1")
    assert out[-1] == "display 800x600"
    assert "fb0 800x600 kept -- no KMS to raise it" in log


@NATIVE
def test_no_kms_keeps_fb0(tmp_path):
    """No DRM device (vesafb/efifb only): fb0 stays, and the log names why."""
    out, log = _open(_binary(tmp_path), "800x600", NOEDID, FAKE_NO_KMS="1")
    assert out == ["display 800x600"]
    assert "fb0 800x600 kept -- no KMS to raise it" in log
