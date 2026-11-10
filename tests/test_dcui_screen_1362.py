"""#1362: the graphical server screen was black on an ESXi VM and kept tty2 away.

Two faults, one symptom ("the screen is dead and nothing gets me out"):

* **Black.** ESXi's SVGA (vmwgfx) does not scan a KMS dumb buffer out: the
  host copies it to the screen only for the damage it is told about with
  ``drmModeDirtyFB``. ``backend_present`` never said, so the screen kept the
  black buffer of ``drmModeSetCrtc`` while every frame sat in memory.
  Principle 5: the GUI now also reads the CRTC back before it says READY and
  answers ``FAIL <why>`` instead -- the text screen shows that reason.
* **No way out.** The keyboard is grabbed, so the kernel never saw Alt+F2;
  and as DRM master the GUI would have kept the CRTC on tty2 anyway. Alt+Fn
  is now a VT switch the GUI asks for, and while another VT is in front the
  keyboards are let go and whatever arrives is dropped (not typed into the
  password field of a screen nobody sees).

The DRM half runs ``backend.c`` against a stub libdrm (no GPU in a test): the
stub records ``drmModeDirtyFB`` and answers ``drmModeGetCrtc`` as told. The
VT switch itself -- signals, master, fbcon -- is proven on the ESXi VM, not
here (the PR has the screenshots).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from native import requires_native
from server.dcui import gui

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
    posix=True, why="backend.c/input.c are built on Linux",
)
CFLAGS = "-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE"


def _build(tmp_path: Path, name: str, sources: str, libs: str = "", wrap: str = "") -> Path:
    binary = tmp_path / name
    command = (f'set -e; cd "$1"; cc {CFLAGS} -Isrc $(pkg-config --cflags cairo libdrm) {wrap} '
               f'-o "$2" {sources} $(pkg-config --libs cairo) {libs} -lm')
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI_DIR), str(binary)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return binary


def _run(binary: Path, **env: str) -> list[str]:
    proc = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL, env={**os.environ, **env})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.splitlines()


# ---- DRM: a stub libdrm with one 64x32 display on card0 ---------------------------

STUB = r'''
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

int dirty_calls = 0;
unsigned dirty_fb = 0;
static int card = -1;

/* only /dev/dri/card0 exists; no tty, no fb0 -- nothing real is opened */
int __real_open(const char *path, int flags, ...);
int __wrap_open(const char *path, int flags, ...) {
    if (!strcmp(path, "/dev/dri/card0")) return card = __real_open("/dev/null", O_RDWR);
    if (!strncmp(path, "/dev/", 5)) { errno = ENOENT; return -1; }
    va_list ap; va_start(ap, flags); int mode = va_arg(ap, int); va_end(ap);
    return __real_open(path, flags, mode);
}
void *__real_mmap(void *a, size_t n, int prot, int fl, int fd, off_t off);
void *__wrap_mmap(void *a, size_t n, int prot, int fl, int fd, off_t off) {
    if (fd == card) return __real_mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return __real_mmap(a, n, prot, fl, fd, off);
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
    drmModeConnectorPtr c = calloc(1, sizeof *c);
    c->connector_id = id; c->connection = DRM_MODE_CONNECTED; c->encoder_id = 50;
    c->count_modes = 1; c->modes = calloc(1, sizeof *c->modes);
    c->modes[0].hdisplay = 64; c->modes[0].vdisplay = 32;
    return c;
}
/* #2768: backend_display_absent reads the status without a probe */
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
    const char *shown = getenv("FAKE_SCANOUT");      /* the fb the CRTC reads back */
    c->crtc_id = id; c->mode_valid = 1; c->buffer_id = shown ? (uint32_t)atoi(shown) : 52;
    return c;
}
void drmModeFreeCrtc(drmModeCrtcPtr c) { free(c); }
int drmModeSetCrtc(int fd, uint32_t crtc, uint32_t fb, uint32_t x, uint32_t y, uint32_t *conn,
                   int count, drmModeModeInfoPtr mode) {
    (void)fd; (void)crtc; (void)fb; (void)x; (void)y; (void)conn; (void)count; (void)mode;
    return 0;
}
int drmModeDirtyFB(int fd, uint32_t fb, drmModeClipPtr clips, uint32_t n) {
    (void)fd; (void)clips; (void)n;
    dirty_calls++; dirty_fb = fb;
    const char *rc = getenv("FAKE_DIRTY_RC");      /* -errno, as libdrm returns it */
    return rc ? atoi(rc) : 0;
}
int drmSetMaster(int fd) { (void)fd; return 0; }
int drmDropMaster(int fd) { (void)fd; return 0; }
'''

PRESENT = r'''
#include <stdio.h>
#include "backend.h"
extern int dirty_calls; extern unsigned dirty_fb;
int main(void) {
    char err[512];
    Backend *b = backend_open("drm", NULL, err, sizeof err);
    if (!b) { printf("open: %s\n", err); return 1; }
    backend_present(b);
    printf("dirty=%d fb=%u\n", dirty_calls, dirty_fb);
    backend_present(b);
    printf("dirty=%d fb=%u\n", dirty_calls, dirty_fb);
    backend_close(b);
    return 0;
}
'''

VERIFY = r'''
#include <stdio.h>
#include "backend.h"
int main(void) {
    char err[512] = "";
    Backend *b = backend_open("drm", NULL, err, sizeof err);
    if (!b) { printf("open: %s\n", err); return 1; }
    backend_present(b);
    int rc = backend_verify(b, err, sizeof err);
    printf("verify=%d %s\n", rc, rc ? err : "");
    backend_close(b);
    return 0;
}
'''

WRAP = "-Wl,--wrap=open -Wl,--wrap=mmap"


def _drm_driver(tmp_path: Path, name: str, main: str) -> Path:
    (tmp_path / "stub.c").write_text(STUB, encoding="utf-8")
    (tmp_path / f"{name}.c").write_text(main, encoding="utf-8")
    return _build(tmp_path, name, f"{tmp_path / name}.c {tmp_path / 'stub.c'} src/backend.c", wrap=WRAP)


@NATIVE
def test_every_drm_frame_tells_the_driver_what_changed(tmp_path):
    """The root cause: without DirtyFB a virtual GPU never shows the frame."""
    binary = _drm_driver(tmp_path, "present", PRESENT)
    assert _run(binary) == ["dirty=1 fb=52", "dirty=2 fb=52"]


@NATIVE
def test_the_screen_is_up_only_when_the_crtc_reads_back_our_framebuffer(tmp_path):
    binary = _drm_driver(tmp_path, "verify", VERIFY)
    assert _run(binary) == ["verify=0 "]
    # another framebuffer on the CRTC: not shown, and said so
    assert _run(binary, FAKE_SCANOUT="0") == ["verify=-1 drm: CRTC 40 shows fb 0, not ours (52)"]
    # a driver without a dirty hook scans the buffer out itself: -ENOSYS is fine
    assert _run(binary, FAKE_DIRTY_RC=str(-38)) == ["verify=0 "]
    # a refused damage report is a frame that may never reach the screen
    out = _run(binary, FAKE_DIRTY_RC=str(-13))
    assert out[0].startswith("verify=-1 drm: DirtyFB refused: "), out


# ---- input: Alt+Fn is a VT switch, and another VT's keys are not ours -------------

KEYS = r'''
#include "input.c"
#include <stdio.h>
static void drain(Input *in) {
    Event e;
    while (input_next(in, &e)) printf("%s\n", e.key == KEYSYM_F2 ? "F2" : "other");
}
int main(void) {
    Input *in = calloc(1, sizeof *in);
    Dev d = { .fd = -1, .kinds = DEV_KEYBOARD };
    key_event(in, &d, KEY_LEFTALT, 1);
    key_event(in, &d, KEY_F2, 1);
    key_event(in, &d, KEY_F2, 2);               /* the chord held: autorepeat */
    key_event(in, &d, KEY_F2, 0);
    key_event(in, &d, KEY_LEFTALT, 0);
    key_event(in, &d, KEY_F2, 1);               /* plain F2: the screen's settings key */
    drain(in);
    return 0;
}
'''

VT_NUMBERS = r'''
#include "input.c"
#include <stdio.h>
int main(void) {
    Input *in = calloc(1, sizeof *in);
    Dev d = { .fd = -1, .kinds = DEV_KEYBOARD };
    int codes[] = { KEY_F1, KEY_F2, KEY_F6, KEY_F11, KEY_F12 };
    key_event(in, &d, KEY_LEFTCTRL, 1);         /* Ctrl+Alt+Fn too */
    key_event(in, &d, KEY_RIGHTALT, 1);
    for (int i = 0; i < 5; i++) { key_event(in, &d, codes[i], 1); key_event(in, &d, codes[i], 0); }
    Event e;
    while (input_next(in, &e)) printf("%d\n", e.key == KEYSYM_VT ? e.vt : -1);
    return 0;
}
'''

SUSPEND = r'''
#include "input.c"
#include <stdio.h>
/* one keyboard on a pipe: a key written to it, read with the GUI's pump */
static int press(Input *in, int wfd) {
    struct input_event ev[2];
    memset(ev, 0, sizeof ev);
    ev[0].type = EV_KEY; ev[0].code = KEY_A; ev[0].value = 1;
    ev[1].type = EV_SYN;
    if (write(wfd, ev, sizeof ev) != sizeof ev) return -1;
    struct pollfd fds[1];
    int n = input_fill_pollfds(in, fds, 1);
    poll(fds, n, 1000);
    input_pump(in, fds, n);
    int got = 0; Event e;
    while (input_next(in, &e)) got += e.type == UIEV_CHAR && e.ch == 'a';
    return got;
}
int main(void) {
    int p[2];
    if (pipe(p)) return 2;
    fcntl(p[0], F_SETFL, O_NONBLOCK);
    Input *in = calloc(1, sizeof *in);
    in->dev[0] = (Dev){ .fd = p[0], .kinds = DEV_KEYBOARD };
    snprintf(in->dev[0].path, sizeof in->dev[0].path, "pipe");
    in->ndev = 1;
    printf("front=%d\n", press(in, p[1]));
    input_suspend(in, 1);
    printf("away=%d\n", press(in, p[1]));
    input_suspend(in, 0);
    printf("back=%d\n", press(in, p[1]));
    return 0;
}
'''


def _input_driver(tmp_path: Path, name: str, source: str) -> Path:
    (tmp_path / f"{name}.c").write_text(source, encoding="utf-8")
    return _build(tmp_path, name, f"{tmp_path / name}.c")


@NATIVE
def test_alt_f2_asks_for_a_vt_switch_and_is_not_the_settings_key(tmp_path):
    """Before: Alt+F2 reached the screen as F2 (the settings dialog), twice
    with autorepeat, and the kernel -- behind the grab -- never switched."""
    assert _run(_input_driver(tmp_path, "keys", KEYS)) == ["other", "F2"]


@NATIVE
def test_alt_and_ctrl_alt_fn_name_the_vt(tmp_path):
    assert _run(_input_driver(tmp_path, "vtn", VT_NUMBERS)) == ["1", "2", "6", "11", "12"]


@NATIVE
def test_keys_typed_on_another_vt_never_reach_the_server_screen(tmp_path):
    """The password typed into tty2's login is not a key for the screen behind it."""
    assert _run(_input_driver(tmp_path, "suspend", SUSPEND)) == ["front=1", "away=0", "back=1"]


# ---- Python: the reason reaches the text screen -----------------------------------

FAILING_GUI = textwrap.dedent('''
    import sys
    print("FAIL drm: CRTC 40 shows fb 0, not ours (52)", flush=True)
    sys.exit(1)
''')


def test_a_gui_that_cannot_show_its_frame_hands_its_reason_to_the_text_screen(tmp_path):
    script = tmp_path / "failing_gui.py"
    script.write_text(FAILING_GUI, encoding="utf-8")
    with (tmp_path / "child.stderr").open("wb") as child_stderr:      # #2539
        session = gui.Session([sys.executable, str(script)], stderr=child_stderr)
        why = session.wait_ready(10)
    assert why == "drm: CRTC 40 shows fb 0, not ours (52)"
