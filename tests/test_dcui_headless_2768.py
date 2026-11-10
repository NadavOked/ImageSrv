"""#2768: a server booted with no display attached stayed on the text screen
after a display was connected -- "/dev/fb0: No such file or directory".

Reproduced in QEMU on the Testrunner (09/11, the v0.91.7 install disk,
virtio-gpu with ``video=Virtual-1:d``): ``/dev/dri/card0`` exists, its one
connector is disconnected, the kernel says "Cannot find any crtc or sizes"
and makes no ``/dev/fb0`` (the fbdev setup waits for a connector -- the same
generic DRM path i915 takes on the owner's machine). The GUI found no
connected display on DRM, then no fb0, said only the fbdev half, and the
text screen never looked again.

Three halves, one test each:

* **The GUI waits for a display instead of failing** (``dcui.c``): with a KMS
  device and nothing connected it draws in memory, says ``READY``, and lights
  the first display that comes up -- no restart, no text screen. It looks on
  a DRM hotplug uevent and on a 30 s fallback, never in a busy loop, and
  reads the status the kernel holds (no probe, no EDID read) until a
  connector says it is connected (owner, 09/11: "every 2 s loads the
  server"). Run here as the whole binary over a stub libdrm whose connector
  is "connected" once a file exists, and whose kernel uevent socket is a
  datagram socket the test writes to.
* **The reason names DRM too**: when there is nothing to wait for (no KMS),
  ``FAIL`` carries DRM's reason and fbdev's, not fbdev's alone.
* **The text screen is a fallback that looks again** (``app.run``): when the
  display hardware changes under it (a card, fb0, a connector status), it
  hands the screen back to the GUI. Nothing changed = it stays, and says why.
"""

from __future__ import annotations

import os
import select
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

from native import requires_native
from server.dcui import app as dcui_app
from server.dcui import gui

REPO = Path(__file__).resolve().parent.parent
GUI_DIR = REPO / "native-gui"


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("bash", shutil.which("bash")),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("make", shutil.which("make")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="the native GUI is built and run on Linux",
)

# ---- the GUI, whole, over a stub libdrm -------------------------------------------

# card0 has one connector; it is connected (640x480) only while the file
# $FAKE_CONNECTED exists. /dev/tty1 is a VT that is always in front; every
# other /dev path is absent -- no fb0, and no real keyboard is grabbed.
STUB = r'''
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stddef.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <linux/kd.h>
#include <linux/vt.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

static char kind[1024];                         /* per fd: 'c' card, 't' tty1 */

int __real_open(const char *path, int flags, ...);
int __wrap_open(const char *path, int flags, ...) {
    int mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, int); va_end(ap); }
    char k = !strcmp(path, "/dev/dri/card0") ? 'c' : !strcmp(path, "/dev/tty1") ? 't' : 0;
    if (k) {
        int fd = __real_open("/dev/null", O_RDWR | O_CLOEXEC);
        if (fd >= 0 && fd < (int)sizeof kind) kind[fd] = k;
        return fd;
    }
    if (!strncmp(path, "/dev/", 5)) { errno = ENOENT; return -1; }
    return __real_open(path, flags, mode);
}
int __real_close(int fd);
int __wrap_close(int fd) {
    if (fd >= 0 && fd < (int)sizeof kind) kind[fd] = 0;
    return __real_close(fd);
}
void *__real_mmap(void *a, size_t n, int prot, int fl, int fd, off_t off);
void *__wrap_mmap(void *a, size_t n, int prot, int fl, int fd, off_t off) {
    if (fd >= 0 && fd < (int)sizeof kind && kind[fd] == 'c')
        return __real_mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return __real_mmap(a, n, prot, fl, fd, off);
}
int __real_ioctl(int fd, unsigned long req, ...);
int __wrap_ioctl(int fd, unsigned long req, ...) {
    va_list ap; va_start(ap, req); void *arg = va_arg(ap, void *); va_end(ap);
    if (fd >= 0 && fd < (int)sizeof kind && kind[fd] == 't') {
        if (req == VT_GETSTATE) ((struct vt_stat *)arg)->v_active = 1;
        if (req == KDGETMODE) *(int *)arg = KD_TEXT;
        return 0;
    }
    return __real_ioctl(fd, req, arg);
}

/* The kernel's uevent socket is an abstract AF_UNIX datagram socket named
 * $FAKE_UEVENT_SOCK; without it the GUI gets no netlink at all. */
static int fake_uev = -1;
int __real_socket(int domain, int type, int proto);
int __real_bind(int fd, const struct sockaddr *a, socklen_t n);
int __wrap_socket(int domain, int type, int proto) {
    if (domain != AF_NETLINK) return __real_socket(domain, type, proto);
    const char *name = getenv("FAKE_UEVENT_SOCK");
    if (!name) { errno = EAFNOSUPPORT; return -1; }
    int fd = __real_socket(AF_UNIX, SOCK_DGRAM | (type & (SOCK_NONBLOCK | SOCK_CLOEXEC)), 0);
    struct sockaddr_un a = { .sun_family = AF_UNIX };
    size_t len = strlen(name);
    memcpy(a.sun_path + 1, name, len);
    if (fd >= 0 && __real_bind(fd, (struct sockaddr *)&a, (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + len)) == 0)
        fake_uev = fd;
    return fd;
}
int __wrap_bind(int fd, const struct sockaddr *a, socklen_t n) {
    if (fd >= 0 && fd == fake_uev) return 0;
    return __real_bind(fd, a, n);
}

static int connected(void) {
    const char *flag = getenv("FAKE_CONNECTED");
    return flag && access(flag, F_OK) == 0;
}
static uint32_t conns[1] = { 30 }, crtcs[1] = { 40 };
drmModeResPtr drmModeGetResources(int fd) {
    (void)fd;
    if (getenv("FAKE_NO_KMS")) { errno = EOPNOTSUPP; return NULL; }
    drmModeResPtr r = calloc(1, sizeof *r);
    r->count_connectors = 1; r->connectors = conns; r->count_crtcs = 1; r->crtcs = crtcs;
    return r;
}
void drmModeFreeResources(drmModeResPtr r) { free(r); }
/* GetConnector probes (EDID/DDC on a real card); Current only reads the
 * status the kernel holds. Each says so, so a test can count the probes. */
static drmModeConnectorPtr connector(uint32_t id) {
    drmModeConnectorPtr c = calloc(1, sizeof *c);
    c->connector_id = id; c->encoder_id = 50;
    c->connection = connected() ? DRM_MODE_CONNECTED : DRM_MODE_DISCONNECTED;
    if (c->connection == DRM_MODE_CONNECTED) {
        c->count_modes = 1; c->modes = calloc(1, sizeof *c->modes);
        c->modes[0].hdisplay = 640; c->modes[0].vdisplay = 480;
    }
    return c;
}
drmModeConnectorPtr drmModeGetConnector(int fd, uint32_t id) {
    (void)fd;
    fprintf(stderr, "stub: probe conn=%u\n", id);
    return connector(id);
}
drmModeConnectorPtr drmModeGetConnectorCurrent(int fd, uint32_t id) {
    (void)fd;
    fprintf(stderr, "stub: current conn=%u\n", id);
    return connector(id);
}
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
    c->crtc_id = id; c->mode_valid = 1; c->buffer_id = 52;
    return c;
}
void drmModeFreeCrtc(drmModeCrtcPtr c) { free(c); }
int drmModeSetCrtc(int fd, uint32_t crtc, uint32_t fb, uint32_t x, uint32_t y, uint32_t *conn,
                   int count, drmModeModeInfoPtr mode) {
    (void)fd; (void)x; (void)y; (void)count;
    if (fb && getenv("FAKE_SETCRTC_BUSY")) { errno = EBUSY; return -1; }
    if (fb) fprintf(stderr, "stub: setcrtc crtc=%u fb=%u conn=%u %ux%u\n", crtc, fb, *conn,
                    mode ? mode->hdisplay : 0, mode ? mode->vdisplay : 0);
    return 0;
}
int drmModeDirtyFB(int fd, uint32_t fb, drmModeClipPtr clips, uint32_t n) {
    (void)fd; (void)clips; (void)n;
    fprintf(stderr, "stub: dirty fb=%u\n", fb);
    return 0;
}
int drmSetMaster(int fd) { (void)fd; return 0; }
int drmDropMaster(int fd) { (void)fd; return 0; }
'''

CFLAGS = "-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE"
WRAP = ("-Wl,--wrap=open -Wl,--wrap=close -Wl,--wrap=mmap -Wl,--wrap=ioctl "
        "-Wl,--wrap=socket -Wl,--wrap=bind")
HOTPLUG = b"change@/devices/pci0000:00/0000:00:02.0/drm/card0\0ACTION=change\0SUBSYSTEM=drm\0HOTPLUG=1\0"
USB = b"add@/devices/pci0000:00/usb1/1-1\0ACTION=add\0SUBSYSTEM=usb\0"


@pytest.fixture(scope="module")
def stub_gui(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dcui-2768")
    (tmp / "stub.c").write_text(STUB, encoding="utf-8")
    binary = tmp / "gui"
    command = (f'set -e; cd "$1"; cc {CFLAGS} $(pkg-config --cflags pangocairo cairo libdrm) {WRAP} '
               f'-o "$2" $(make -s -f Makefile print-gui-src) "$3" '
               f'$(pkg-config --libs pangocairo cairo) -lm')
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI_DIR), str(binary), str(tmp / "stub.c")],
                          capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return binary


class _Gui:
    """The GUI child as the DCUI runs it: --screen dcui, frames on stdin."""

    def __init__(self, binary: Path, tmp: Path, uevents: bool = True, **env: str):
        self.uevent_name = f"imagesrv-2768-{os.getpid()}-{time.monotonic_ns()}"
        if uevents:
            env["FAKE_UEVENT_SOCK"] = self.uevent_name
        self.err_path = tmp / "gui.stderr"
        self.err = self.err_path.open("wb")
        self.proc = subprocess.Popen([str(binary), "--screen", "dcui", "--theme", "light"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
                                     env={**os.environ, **env})

    def line(self, seconds: float = 20.0) -> str:
        ready, _, _ = select.select([self.proc.stdout], [], [], seconds)
        return self.proc.stdout.readline().decode("utf-8", "replace").strip() if ready else "<nothing>"

    def stderr(self) -> str:
        if not self.err.closed:
            self.err.flush()
        return self.err_path.read_text(encoding="utf-8", errors="replace")

    def uevent(self, message: bytes) -> None:
        """What the kernel multicasts on a hotplug: one datagram."""
        sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sender.sendto(message, "\0" + self.uevent_name)
        except (ConnectionRefusedError, FileNotFoundError):
            pass                        # a multicast nobody listens to is not an error
        finally:
            sender.close()

    def count(self, text: str) -> int:
        return self.stderr().count(text)

    def wait_count(self, text: str, want: int, seconds: float) -> int:
        deadline = time.monotonic() + seconds
        while self.count(text) < want and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.count(text)

    def wait_for(self, text: str, seconds: float = 15.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if text in self.stderr():
                return True
            time.sleep(0.1)
        return False

    def close(self) -> int | None:
        try:
            self.proc.stdin.close()
            return self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            return None
        finally:
            self.err.close()


@NATIVE
def test_a_server_with_no_display_gets_the_gui_on_the_first_display_connected(stub_gui, tmp_path):
    flag = tmp_path / "connected"
    child = _Gui(stub_gui, tmp_path, FAKE_CONNECTED=str(flag))
    try:
        first = child.line()
        # before #2768: "FAIL no screen: /dev/fb0: No such file or directory"
        assert first.startswith("READY "), (first, child.stderr())
        assert "stub: setcrtc" not in child.stderr()        # nothing to light yet
        time.sleep(1)
        flag.write_text("")                                 # the display is connected...
        sent = time.monotonic()
        child.uevent(HOTPLUG)                               # ...and the kernel says so
        assert child.wait_for("stub: setcrtc crtc=40 fb=52 conn=30 640x480", 3.0), child.stderr()
        assert time.monotonic() - sent < 3.0                # the event, not the 30 s fallback
        assert child.wait_for("stub: dirty fb=52"), child.stderr()   # a frame is on it
        assert child.proc.poll() is None, child.stderr()    # the same GUI, not a restart
    finally:
        rc = child.close()
    assert rc == 0, child.stderr()


@NATIVE
def test_a_display_that_comes_but_cannot_be_used_is_said_not_waited_for(stub_gui, tmp_path):
    """Principle 5: once a display is there, "still waiting" would be a lie --
    the GUI stops with why, and the text screen shows it."""
    flag = tmp_path / "connected"
    child = _Gui(stub_gui, tmp_path, FAKE_CONNECTED=str(flag), FAKE_SETCRTC_BUSY="1")
    try:
        first = child.line()
        assert first.startswith("READY "), (first, child.stderr())
        flag.write_text("")
        child.uevent(HOTPLUG)
        second = child.line(15)
        while second.startswith("CAPS "):                  # READY is followed by CAPS
            second = child.line(15)
    finally:
        rc = child.close()
    assert second.startswith("FAIL display connected but not usable: drm: "), (second, child.stderr())
    assert "drmModeSetCrtc: Device or resource busy" in second and "fbdev: /dev/fb0" in second, second
    assert rc == 1


@NATIVE
def test_a_display_connected_at_boot_is_lit_straight_away(stub_gui, tmp_path):
    """The guard: the ordinary boot (Hyper-V, ESXi, a monitor on) is unchanged."""
    flag = tmp_path / "connected"
    flag.write_text("")
    child = _Gui(stub_gui, tmp_path, FAKE_CONNECTED=str(flag))
    try:
        assert child.line() == "READY 640x480", child.stderr()
        assert "stub: setcrtc crtc=40 fb=52 conn=30 640x480" in child.stderr()
    finally:
        rc = child.close()
    assert rc == 0, child.stderr()


@NATIVE
def test_waiting_for_a_display_reads_no_connector_until_an_event(stub_gui, tmp_path):
    """Owner 09/11: no look every 2 s. While nothing is connected the GUI
    sleeps on the uevent socket: no probe and no status read, and an event
    of another subsystem wakes nothing."""
    child = _Gui(stub_gui, tmp_path, FAKE_CONNECTED=str(tmp_path / "connected"))
    try:
        assert child.line().startswith("READY "), child.stderr()
        time.sleep(1)
        probes, reads = child.count("stub: probe"), child.count("stub: current")
        child.uevent(USB)                                   # a USB stick: not ours
        time.sleep(5)                                       # two and a half old 2 s looks
        assert child.count("stub: probe") == probes, child.stderr()
        assert child.count("stub: current") == reads, child.stderr()
        child.uevent(HOTPLUG)                               # a hotplug, still disconnected
        assert child.wait_count("stub: current", reads + 1, 3.0) == reads + 1, child.stderr()
        time.sleep(0.5)
        assert child.count("stub: probe") == probes, child.stderr()   # disconnected: no probe
        assert child.proc.poll() is None, child.stderr()
    finally:
        rc = child.close()
    assert rc == 0, child.stderr()


@NATIVE
def test_without_hotplug_events_the_display_is_found_on_the_slow_fallback(stub_gui, tmp_path):
    """A driver that changes the status without an event (virtio-gpu,
    measured in QEMU), or no netlink socket at all: found within 30 s."""
    flag = tmp_path / "connected"
    child = _Gui(stub_gui, tmp_path, uevents=False, FAKE_CONNECTED=str(flag))
    try:
        assert child.line().startswith("READY "), child.stderr()
        flag.write_text("")
        sent = time.monotonic()
        assert child.wait_for("stub: setcrtc crtc=40 fb=52 conn=30 640x480", 40.0), child.stderr()
        assert time.monotonic() - sent <= 32.0, time.monotonic() - sent
        # principle 5: the missing events are said, not silently polled around
        assert "no hotplug events (netlink socket: " in child.stderr(), child.stderr()
    finally:
        rc = child.close()
    assert rc == 0, child.stderr()


@NATIVE
def test_with_nothing_to_wait_for_the_reason_names_drm_and_fbdev(stub_gui, tmp_path):
    child = _Gui(stub_gui, tmp_path, FAKE_CONNECTED=str(tmp_path / "connected"), FAKE_NO_KMS="1")
    try:
        first = child.line()
    finally:
        rc = child.close()
    assert first.startswith("FAIL no screen: "), first
    assert "drm: " in first and "no KMS resources" in first, first
    assert "fbdev: /dev/fb0: No such file or directory" in first, first
    assert rc == 1


# ---- Python: a FAIL after READY is the reason, not "exit 1" -----------------------

LATE_FAIL = textwrap.dedent('''
    import sys
    print("READY 1024x768", flush=True)
    sys.stdin.readline()
    print("FAIL display connected but not shown: drm: CRTC 40 shows fb 0, not ours (52)", flush=True)
    sys.exit(1)
''')


def test_a_gui_that_fails_after_ready_hands_its_reason_to_the_text_screen(tmp_path):
    script = tmp_path / "late_fail.py"
    script.write_text(LATE_FAIL, encoding="utf-8")
    with (tmp_path / "child.stderr").open("wb") as child_stderr:
        session = gui.Session([sys.executable, str(script)], stderr=child_stderr)
        try:
            assert session.wait_ready(10) == ""
            runtime = dcui_app.new_runtime(tmp_path)
            runtime.last_refresh = time.monotonic() + 3600     # no host refresh in a test
            why = gui.serve(runtime, session, max_loops=200)
        finally:
            session.stop()
    assert why == "display connected but not shown: drm: CRTC 40 shows fb 0, not ours (52)"


#: round/1108j: says why and is gone at once -- by the time serve() runs, the FAIL line
#: and the end of the pipe are both queued, and the pipe to it is closed.
GONE_FAIL = textwrap.dedent('''
    import sys
    print("READY 1024x768", flush=True)
    print("FAIL display connected but not shown: drm: CRTC 40 shows fb 0, not ours (52)", flush=True)
    sys.exit(1)
''')


@pytest.mark.parametrize("path", ["send", "take"])
def test_a_reason_said_just_before_the_gui_exits_is_not_lost(tmp_path, path):
    """round/1108j (full run 2 of the round, 09/11): the late-fail test above got
    "graphic screen stopped (exit 1)" -- the GUI's FAIL line was in the queue and
    serve() never read it. Two roads lose it: the next frame's send hits the closed
    pipe ("send"), or take() gets the line and the end together and returns None
    ("take", send made to succeed). Here the race is decided before serve() starts."""
    script = tmp_path / "gone_fail.py"
    script.write_text(GONE_FAIL, encoding="utf-8")
    with (tmp_path / "child.stderr").open("wb") as child_stderr:
        session = gui.Session([sys.executable, str(script)], stderr=child_stderr)
        try:
            assert session.wait_ready(10) == ""
            session.proc.wait(timeout=10)
            deadline = time.monotonic() + 10
            while session.lines.qsize() < 2 and time.monotonic() < deadline:   # the FAIL line, then None
                time.sleep(0.01)
            assert session.lines.qsize() == 2, "the reader did not queue the line and the end"
            if path == "take":
                session.send = lambda frame: True
            runtime = dcui_app.new_runtime(tmp_path)
            runtime.last_refresh = time.monotonic() + 3600     # no host refresh in a test
            why = gui.serve(runtime, session, max_loops=20)
        finally:
            session.stop()
    assert why == "display connected but not shown: drm: CRTC 40 shows fb 0, not ours (52)"


# ---- Python: the text screen looks again when the display hardware changes --------

class _StillText(Exception):
    pass


class _Screen:
    """A stand-in stdscr: 80x25, no keys. `change` runs on the first read
    (the display is connected while the text screen is up); after `reads`
    more reads the screen is declared stuck.

    round/1108j: counted in reads, not in wall-clock seconds. curses_main
    builds its runtime (new_runtime: the host's facts) after this object is
    made and before the first read, and on the testrunner that took 0.1 to
    25 s (five runs, 09/11) -- a 3 s deadline started here ran out before the
    screen read a single key: 6 of 20 alone, load 1.8. Every read sleeps
    0.02 s, so `reads` is also a floor in time: the 0.05 s fallback is due
    long before 150 reads."""

    def __init__(self, change, reads: int):
        self.change, self.left = change, reads

    def keypad(self, flag): pass
    def timeout(self, ms): pass
    def erase(self): pass
    def refresh(self): pass
    def getmaxyx(self): return 25, 80
    def addstr(self, *args): pass

    def get_wch(self):
        if self.change:
            self.change()
            self.change = None
        self.left -= 1
        if self.left < 0:
            raise _StillText
        time.sleep(0.02)
        raise _CursesError


class _CursesError(Exception):
    pass


class _Events:
    """The kernel's uevent socket, non-blocking: datagrams the test queues."""

    def __init__(self):
        self.queue: list[bytes] = []

    def recv(self, size: int) -> bytes:
        if not self.queue:
            raise BlockingIOError
        return self.queue.pop(0)

    def close(self):
        pass


@pytest.fixture()
def boot(tmp_path, monkeypatch):
    """app.run on a normal boot, with /dev and /sys/class/drm under tmp_path:
    the GUI attempt fails, the text screen comes up, and `change` is what
    happens to the display hardware meanwhile."""
    dev, sys_drm = tmp_path / "dev", tmp_path / "sys-drm"
    (dev / "dri").mkdir(parents=True)
    (sys_drm / "card0-HDMI-A-1").mkdir(parents=True)
    (sys_drm / "card0-HDMI-A-1" / "status").write_text("disconnected\n")
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("BOOT_IMAGE=/vmlinuz root=UUID=1 ro quiet\n", encoding="ascii")
    monkeypatch.setattr(dcui_app, "CMDLINE", cmdline)
    monkeypatch.setattr(gui, "DEV", dev, raising=False)
    monkeypatch.setattr(gui, "SYS_DRM", sys_drm, raising=False)
    monkeypatch.setattr(gui, "FALLBACK_SECONDS", 0.05, raising=False)
    events = _Events()
    monkeypatch.setattr(gui, "open_uevents", lambda: events, raising=False)
    seen: dict = {"gui": 0, "why": [], "stuck": 0}

    def fake_gui(data_dir):
        seen["gui"] += 1
        return "no /dev/dri/card* or /dev/fb0 (no framebuffer)" if seen["gui"] == 1 else "gui stopped"

    def start(change, reads: int = 150):
        def wrapper(fn, data_dir, why):
            seen["why"].append(why)
            if seen["gui"] > 1:                     # the second text screen ends the test
                return
            try:
                fn(_Screen(change, reads), data_dir, why)
            except _StillText:
                seen["stuck"] += 1

        curses = types.ModuleType("curses")
        curses.wrapper = wrapper
        curses.error = _CursesError
        curses.A_NORMAL = 0
        curses.curs_set = lambda n: None
        curses.has_colors = lambda: False
        for name, value in vars(gui.KEYS).items():   # the key numbers _handle_key reads
            setattr(curses, name, value)
        monkeypatch.setitem(sys.modules, "curses", curses)
        monkeypatch.setattr(gui, "run", fake_gui)
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        dcui_app.run(data)
        return seen

    start.dev, start.sys_drm, start.events = dev, sys_drm, events
    start.monkeypatch = monkeypatch
    return start


@pytest.mark.parametrize("what", ["fb0", "card", "connector"])
def test_the_text_screen_hands_back_to_the_gui_once_a_display_appears(boot, what):
    def plug():
        if what == "fb0":
            (boot.dev / "fb0").write_text("")
        elif what == "card":
            (boot.dev / "dri" / "card0").write_text("")
        else:
            (boot.sys_drm / "card0-HDMI-A-1" / "status").write_text("connected\n")

    seen = boot(plug)
    # before #2768: one GUI attempt, and the text screen stayed for good
    assert seen["stuck"] == 0 and seen["gui"] == 2, seen
    assert seen["why"] == ["no /dev/dri/card* or /dev/fb0 (no framebuffer)", "gui stopped"]


def test_a_display_uevent_hands_back_at_once_without_waiting_for_the_fallback(boot):
    boot.monkeypatch.setattr(gui, "FALLBACK_SECONDS", 3600.0)

    def plug():
        (boot.sys_drm / "card0-HDMI-A-1" / "status").write_text("connected\n")
        boot.events.queue.append(b"change@/devices/drm/card0\0ACTION=change\0SUBSYSTEM=drm\0HOTPLUG=1\0")

    seen = boot(plug)
    assert seen["stuck"] == 0 and seen["gui"] == 2, seen


def test_between_events_the_text_screen_does_not_look(boot):
    """Owner 09/11: no look every 2 s. A change with no event and no fallback
    due is not seen within the 150 reads (3 s and more) this text screen lives."""
    boot.monkeypatch.setattr(gui, "FALLBACK_SECONDS", 3600.0)

    def plug():
        (boot.sys_drm / "card0-HDMI-A-1" / "status").write_text("connected\n")
        boot.events.queue.append(b"add@/devices/usb1/1-1\0ACTION=add\0SUBSYSTEM=usb\0")

    seen = boot(plug)
    assert seen["stuck"] == 1 and seen["gui"] == 1, seen


def test_nothing_changed_keeps_the_text_screen_and_its_reason(boot):
    """The guard: no change, no new attempt -- no restart loop of a GUI that cannot run."""
    seen = boot(None, reads=50)
    assert seen["stuck"] == 1 and seen["gui"] == 1, seen
    assert seen["why"] == ["no /dev/dri/card* or /dev/fb0 (no framebuffer)"]
