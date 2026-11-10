"""Drive the graphical server screen (#1203).

``imagesrv-station-gui --screen dcui`` only paints and reads the keyboard.
This process stays root, keeps the one state machine (:mod:`app`: password
gating, the 3-failure lockout, the 90 s auto-logout, the 60 s network
rollback) and runs every action through :mod:`actions`, exactly as the text
screen does.  The two talk over the child's pipes:

* to the GUI   -- one frame of records per change (:func:`view.render`);
* from the GUI -- one token per line: ``READY WxH`` once the display is open,
  ``KEY <name>``, ``CHAR <hex code point>`` and ``CAPS 0|1``.

When the GUI cannot run -- no binary, no DRM/fbdev, it exits before READY, or
it stops later -- :func:`run` returns the reason and the caller falls back to
the 80x25 text screen, which shows that reason (principle 5: the fallback is
said, not silent).
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from server.net import netcfg_rollback

from . import app, view

GUI_CANDIDATES = (
    Path("/opt/imagesrv/native-gui/imagesrv-station-gui"),   # built by build_initramfs.sh
    Path("/usr/bin/imagesrv-station-gui"),
)
#: The GUI must have its display open (READY) within this long.
READY_SECONDS = 15.0
POLL_SECONDS = 0.2
#: #2768: what the text screen watches to hand the screen back to the GUI --
#: on a kernel uevent of the display (drm/graphics), and on this slow fallback
#: for a change that comes with no event (owner 09/11: no look every 2 s).
DEV = Path("/dev")
SYS_DRM = Path("/sys/class/drm")
FALLBACK_SECONDS = 30.0
NETLINK_KOBJECT_UEVENT = 15
DISPLAY_SUBSYSTEMS = (b"SUBSYSTEM=drm", b"SUBSYSTEM=graphics")

#: curses' own numbers, so app._handle_key sees the same keys from both screens.
KEYS = SimpleNamespace(KEY_DOWN=258, KEY_UP=259, KEY_LEFT=260, KEY_RIGHT=261,
                       KEY_BACKSPACE=263, KEY_F2=266, KEY_F4=268, KEY_F5=269,
                       KEY_F9=273, KEY_F12=276, KEY_ENTER=343, KEY_BTAB=353)
_NAMED = {
    "F2": KEYS.KEY_F2, "F4": KEYS.KEY_F4, "F5": KEYS.KEY_F5, "F9": KEYS.KEY_F9,
    "F12": KEYS.KEY_F12,
    "ENTER": "\n", "ESC": "\x1b", "TAB": "\t", "BACKTAB": KEYS.KEY_BTAB,
    "BACKSPACE": KEYS.KEY_BACKSPACE, "UP": KEYS.KEY_UP, "DOWN": KEYS.KEY_DOWN,
    "LEFT": KEYS.KEY_LEFT, "RIGHT": KEYS.KEY_RIGHT,
}


def parse_token(line: str) -> tuple[str, object] | None:
    """One line from the GUI -> ("key", key) | ("caps", bool) | ("ready", "WxH");
    None for anything else (an unknown key is ignored, not guessed)."""
    parts = line.strip().split()
    if len(parts) != 2:
        return None
    kind, value = parts
    if kind == "KEY" and value in _NAMED:
        return "key", _NAMED[value]
    if kind == "CHAR":
        try:
            point = int(value, 16)
        except ValueError:
            return None
        return ("key", chr(point)) if 0x20 <= point <= 0x10FFFF else None
    if kind == "CAPS" and value in ("0", "1"):
        return "caps", value == "1"
    if kind == "READY":
        return "ready", value
    return None


def find_binary(candidates=GUI_CANDIDATES) -> Path | None:
    return next((path for path in candidates
                 if path.is_file() and os.access(path, os.X_OK)), None)


def display_problem(dev: Path = Path("/dev")) -> str:
    """'' when a screen device exists.  The GUI is never started on its
    memory backend here: a server screen drawn into a file is a blank tty1."""
    if any((dev / "dri").glob("card*")) or (dev / "fb0").exists():
        return ""
    return f"no {dev}/dri/card* or {dev}/fb0 (no framebuffer)"


def display_state(dev: Path | None = None, sys_drm: Path | None = None) -> tuple:
    """#2768: the display hardware as the kernel shows it -- the KMS cards,
    /dev/fb0, and every connector's status. A status that cannot be read is
    part of the state by name, not dropped."""
    dev = DEV if dev is None else dev
    sys_drm = SYS_DRM if sys_drm is None else sys_drm
    cards = tuple(sorted(path.name for path in (dev / "dri").glob("card*")))
    connectors = []
    for status in sorted(sys_drm.glob("card*-*/status")):
        try:
            connectors.append((status.parent.name, status.read_text(encoding="ascii").strip()))
        except OSError as exc:
            connectors.append((status.parent.name, f"unreadable: {exc.strerror}"))
    return cards, (dev / "fb0").exists(), tuple(connectors)


def open_uevents():
    """#2768: the kernel's own uevents (netlink group 1, no udev needed),
    non-blocking; None -- and said in the journal -- when there are none."""
    try:
        sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
    except (AttributeError, OSError) as exc:           # AttributeError: no AF_NETLINK here
        print(f"imagesrv-dcui: no uevents ({exc}) -- the display is looked at every "
              f"{FALLBACK_SECONDS:.0f} s", file=sys.stderr, flush=True)
        return None
    try:
        sock.bind((0, 1))
        sock.setblocking(False)
    except OSError as exc:
        sock.close()
        print(f"imagesrv-dcui: no uevents ({exc}) -- the display is looked at every "
              f"{FALLBACK_SECONDS:.0f} s", file=sys.stderr, flush=True)
        return None
    return sock


def display_events(sock) -> bool:
    """Drain the socket; True when an event was of the display (a card, a
    connector, fb0) -- or events were lost (ENOBUFS) and one may have been."""
    hit = False
    while True:
        try:
            message = sock.recv(8192)
        except BlockingIOError:
            return hit
        except OSError:                                 # ENOBUFS: some were dropped
            hit = True
            continue
        if not message:
            return hit
        fields = message.split(b"\0")
        hit = hit or any(field in fields for field in DISPLAY_SUBSYSTEMS)


class DisplayWatch:
    """#2768: the text screen's way back to the GUI -- a fallback only. The
    state is taken before the GUI is tried; once it differs (a display was
    connected, a card or fb0 appeared) the GUI is tried again. It is read on
    a display uevent, once at the start, and every FALLBACK_SECONDS -- not in
    a busy loop. An unchanged state is no reason to try again: a GUI that
    cannot run is not restarted in a loop, and the text screen keeps saying why."""

    def __init__(self):
        self.state = display_state()
        self.events = open_uevents()
        self.next_check = 0.0
        self.fired = False

    def changed(self, now: float) -> bool:
        if self.fired:
            return True
        look = now >= self.next_check
        if self.events is not None and display_events(self.events):
            look = True
        if not look:
            return False
        self.next_check = now + FALLBACK_SECONDS
        if display_state() != self.state:
            self.fired = True
            if self.events is not None:
                self.events.close()
                self.events = None
        return self.fired


class Session:
    """The GUI child: frames in, tokens out (read on a thread, so the loop
    waits on a queue with a timeout instead of select on a pipe)."""

    def __init__(self, argv: list[str], stderr=None):
        # stderr=None: the child shares the service's stderr, so its own
        # complaints reach the journal.  A test passes a file (#2539).
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=stderr, bufsize=0)
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.said: list[str] = []      # round/1108j: what take() held when it met the end
        self.ended = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for raw in self.proc.stdout:
            self.lines.put(raw.decode("utf-8", "replace"))
        self.lines.put(None)                          # EOF: the GUI is gone

    def wait_ready(self, seconds: float = READY_SECONDS) -> str:
        """'' once READY arrived; else why not."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                return f"imagesrv-station-gui exited before opening the screen ({self.exit_code()})"
            if line.startswith("FAIL "):
                # #1362: the GUI could not show its first frame, or could not
                # confirm it is on the screen, and says why -- that reason is
                # what the text screen shows, not a bare exit code.
                self.stop()
                return line[5:].strip() or "imagesrv-station-gui failed without a reason"
            token = parse_token(line)
            if token and token[0] == "ready":
                return ""
        self.stop()
        return f"imagesrv-station-gui did not open the screen within {seconds:.0f} s"

    def send(self, frame: str) -> bool:
        try:
            self.proc.stdin.write(frame.encode("utf-8"))
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def take(self, timeout: float) -> list[str] | None:
        """The lines that arrived within <timeout>; None once the GUI is gone."""
        try:
            first = self.lines.get(timeout=timeout)
        except queue.Empty:
            return []
        got = [first]
        while True:
            try:
                got.append(self.lines.get_nowait())
            except queue.Empty:
                break
        if None in got:
            self.said, self.ended = got[:got.index(None)], True
            return None
        return got

    def last_words(self, seconds: float = 2.0) -> list[str]:
        """round/1108j: what the GUI said before it went -- the lines take() held
        with the end, then the pipe until EOF (the reader thread may lag behind
        the exit that made a send fail). Bounded like exit_code()."""
        got, self.said = self.said, []
        deadline = time.monotonic() + seconds
        while not self.ended:
            try:
                line = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if line is None:
                self.ended = True
            else:
                got.append(line)
        return got

    def exit_code(self) -> str:
        try:
            code = self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return "still running"
        return f"exit {code}"

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _rollback_left(runtime) -> int:
    if runtime.screen != "network_confirm":
        return 0
    marker = netcfg_rollback.read_pending(runtime.data_dir / "netcfg")
    return max(0, int(marker.deadline - time.time())) if marker else 0


def local_now() -> datetime:
    """#1447: the zone is read again for every frame. The console changes it
    (timedatectl) under a DCUI that keeps running, and glibc's localtime_r
    keeps the zone it read first until tzset() -- the screen stayed UTC."""
    if hasattr(time, "tzset"):
        time.tzset()
    return datetime.now()


def frame_now(runtime) -> str:
    now = local_now()
    return view.render(runtime, time.monotonic(), time.time(), now.strftime("%H:%M"), now,
                       _rollback_left(runtime))


def _gone(session: Session) -> str:
    """The GUI is gone. A FAIL line it wrote before exiting is the reason (#2768):
    queued with the end of the pipe, or not yet read when the next frame's send
    hit the closed pipe, it was lost and the text screen said "stopped (exit 1)"
    (round/1108j, full run 2)."""
    for line in session.last_words():
        if line.startswith("FAIL "):
            return line[5:].strip() or "imagesrv-station-gui failed without a reason"
    return f"graphic screen stopped ({session.exit_code()})"


def serve(runtime, session: Session, max_loops: int | None = None) -> str:
    """The loop: tick, send a frame when it changed, apply the GUI's keys.
    Returns why it stopped."""
    last_frame = ""
    seen_refresh = None
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        app.tick(runtime, time.monotonic())
        if runtime.last_refresh != seen_refresh:
            seen_refresh = runtime.last_refresh
            runtime.extras = view.collect_extras(runtime.conn)
        frame = frame_now(runtime)
        if frame != last_frame:
            if not session.send(frame):
                return _gone(session)
            last_frame = frame
        lines = session.take(POLL_SECONDS)
        if lines is None:
            return _gone(session)
        for line in lines:
            if line.startswith("FAIL "):
                # #2768: a display connected after READY that could not be
                # shown -- the GUI says why, and that is what the text screen says.
                return line[5:].strip() or "imagesrv-station-gui failed without a reason"
            token = parse_token(line)
            if token is None:
                continue
            kind, value = token
            if kind == "key":
                app._handle_key(runtime, value, KEYS)
            elif kind == "caps":
                runtime.caps = bool(value)
    return "loop limit reached"


def run(data_dir: Path, candidates=GUI_CANDIDATES, dev: Path = Path("/dev"),
        theme: str = "light") -> str:
    """Show the graphical screen until it cannot be shown; return why."""
    binary = find_binary(candidates)
    if binary is None:
        return "imagesrv-station-gui not found: " + ", ".join(str(p) for p in candidates)
    problem = display_problem(dev)
    if problem:
        return problem
    session = Session([str(binary), "--screen", "dcui", "--theme", theme])
    problem = session.wait_ready()
    if problem:
        return problem
    try:
        return serve(app.new_runtime(data_dir), session)
    finally:
        session.stop()
