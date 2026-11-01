"""#1709: the GUI comes up before DHCP and the first hello, not after them.

Nadav, 18/10: "if it saves even one second -- build it", and no black screen.
Until #1709 the native GUI was started only from the agent's main loop, after
the first hello and `decide` (imagesrv-agent: build_console_screen, wait_poll):
the module load, the link, DHCP and hello's own probe all went by on a blank
or text screen. Now agent/init starts it (lib/guiearly.sh) the moment the GPU
driver's framebuffer exists, while the rest of the modules load.

The order is proven on the real agent/init, run end to end with its outside
world stubbed: modprobe/ip/udhcpc/curl record into one trace, the GPU driver's
modprobe makes a framebuffer appear, a storage module's modprobe HOLDS until a
GUI has started (up to 5 s), and the agent is a stub that records its hello.
Before #1709 there is no GUI line in the trace at all.

Then the hand-over: the agent adopts that GUI (no second one), passes the role
the hello named to the same bridge (no restart), and a path that draws no GUI
-- an unknown machine boots from its disk, principle 1 -- closes it cleanly.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix
from test_cloner_gui import GUI, _pkgconfig
from agentlib import flat_lib

LIB = flat_lib()   # #2239: $LIB_DIR is the flat layout the initramfs ships
POSIX = requires_native(("bash", BASH), posix=True, why="kill, process groups, a char device for the framebuffer")

E1000E = "pci:v00008086d00001502sv00001028sd00000494bc02sc00i00"
I915 = "pci:v00008086d00000046sv0000103Csd0000304Bbc03sc00i00"
#: ahci is loaded by the third phase of mods_load_all, after the GPU's modalias
STORAGE = "ahci"
#: how long the storage modprobe holds, waiting for a GUI (a ceiling, not a sleep)
HOLD_S = 5


def _write(path: Path, text: str, mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(mode)
    return path


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"


def _await(fn, ceiling: float):
    end = time.monotonic() + ceiling
    while True:
        got = fn()
        if got or time.monotonic() >= end:
            return got
        time.sleep(0.05)


def _killpg(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=10)


# --- 1. agent/init end to end -------------------------------------------------

#: The bridge as far as init sees it: one line in the trace, then it lives.
KIOSK_TRACE = '#!/bin/sh\necho "gui screen=$GUI_SCREEN" >> "$TRACE"\nexec sleep 300\n'
STATION_OK = "#!/bin/sh\nexit 0\n"   # the `--help` load check


def _init(tmp: Path, *, fb_name: str = "i915drmfb", cmdline: str = "imagesrv.server=127.0.0.1:9"):
    """Runs the real agent/init. Returns (trace lines, init's output, run dir)."""
    sysd, sysroot, run, mod = tmp / "sys", tmp / "sysroot", tmp / "run", tmp / "etc"
    trace, fb = tmp / "trace", tmp / "dev" / "fb0"
    (sysd / "class" / "net" / "lo").mkdir(parents=True)
    fb.parent.mkdir()
    run.mkdir()
    for slot, cls, alias in (("dev19", "0x020000", E1000E), ("dev02", "0x030000", I915)):
        _write(sysd / "bus" / "pci" / "devices" / slot / "class", cls + "\n", 0o644)
        _write(sysd / "bus" / "pci" / "devices" / slot / "modalias", alias + "\n", 0o644)
    lists = {"modules": [STORAGE, "sd_mod", "ext4", "e1000e", "i915"], "modules.required": ["ext4"],
             "modules.phy": [], "modules.nic": ["e1000e"], "modules.gpu": ["i915"]}
    for name, items in lists.items():
        _write(mod / name, "".join(f"{i}\n" for i in items), 0o644)
    _write(tmp / "uptime", "3.21 9.00\n", 0o644)
    _write(tmp / "cmdline", cmdline + "\n", 0o644)
    agent = _write(tmp / "imagesrv-agent", '#!/bin/sh\necho "hello gui.pid=$(cat "$RUN/gui.pid" 2>/dev/null)" >> "$TRACE"\n')
    kiosk = _write(tmp / "kiosk", KIOSK_TRACE)
    station = _write(tmp / "station", STATION_OK)

    text = (AGENT / "init").read_text(encoding="utf-8")
    for old, new in (("/proc/uptime", posix(tmp / "uptime")), ("/run/imagesrv", posix(run)),
                     ("/usr/lib/imagesrv", posix(LIB)), ("/etc/imagesrv", posix(mod)),
                     ("/sys/", posix(sysd) + "/"), ("/usr/bin/imagesrv-agent", posix(agent)),
                     ('fail "agent exited"', "exit 0")):
        assert old in text, old
        text = text.replace(old, new)
    stubs = f"""
TRACE={posix(trace)!r}; RUN={posix(run)!r}; SYSD={posix(sysd)!r}; SYSROOT={posix(sysroot)!r}
export TRACE RUN SYSROOT
export CMDLINE_FILE={posix(tmp / 'cmdline')!r} UPTIME_FILE={posix(tmp / 'uptime')!r} SYS_DIR="$SYSD"
export GUI_KIOSK={posix(kiosk)!r} GUI_STATION={posix(station)!r} FB_DEV={posix(fb)!r}
mount() {{ :; }}
curl() {{ echo "curl $*" >> "$TRACE"; return 0; }}
ip() {{ case "$*" in "-4 addr show"*) echo "    inet 10.0.0.9/24 brd 10.0.0.255" ;; esac; }}
udhcpc() {{   # #2040: the -q probe records the server-id, like udhcpc.probe
    echo "dhcp $2" >> "$TRACE"
    case " $* " in *" -q "*) echo serverid=127.0.0.1 > "$RUN/dhcp-probe.$2" ;; esac
    return 0
}}
modprobe() {{
    case "$1" in
        {STORAGE!r}) _h=0; until grep -q '^gui ' "$TRACE" 2>/dev/null || [ "$_h" -ge {HOLD_S * 10} ]; do sleep 0.1; _h=$((_h + 1)); done ;;
    esac
    echo "mod $1" >> "$TRACE"   # before its effects: the trace is in the order things happened
    case "$1" in
        {E1000E!r}) mkdir -p "$SYSD/class/net/eth0"; : > "$SYSD/class/net/eth0/device"
                    echo 1 > "$SYSD/class/net/eth0/carrier"; printf 'aa:bb:cc:dd:ee:01\\n' > "$SYSD/class/net/eth0/address" ;;
        {I915!r}) mkdir -p "$SYSD/class/drm/card0" "$SYSROOT/sys/class/graphics/fb0"
                  echo {fb_name!r} > "$SYSROOT/sys/class/graphics/fb0/name"; ln -s /dev/null {posix(fb)!r} ;;
        pci:*) return 1 ;;
    esac
    return 0
}}
"""
    out = tmp / "out.txt"
    with out.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(["sh", "-c", stubs + text], stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            proc.wait(timeout=60)
        finally:
            _killpg(proc)
    return _lines(trace), out.read_text(encoding="utf-8"), run


@POSIX
def test_init_starts_the_gui_before_dhcp_and_before_the_first_hello(tmp_path):
    trace, out, _ = _init(tmp_path)
    guis = [i for i, line in enumerate(trace) if line.startswith("gui ")]
    assert guis, f"no GUI was started from init\n{trace}\n{out}"
    dhcp = next(i for i, line in enumerate(trace) if line.startswith("dhcp "))
    hello = next(i for i, line in enumerate(trace) if line.startswith("hello "))
    assert guis[0] < dhcp < hello, trace
    assert trace[guis[0]] == "gui screen=boot", trace
    assert len(guis) == 1, trace                        # one GUI; the agent adopts it (below)


@POSIX
def test_the_gui_starts_while_the_modules_are_still_loading(tmp_path):
    # the storage modprobe holds until a GUI exists: a GUI that waited for
    # the module load to end would come after it, HOLD_S seconds later
    trace, out, _ = _init(tmp_path)
    assert f"mod {I915}" in trace and f"mod {STORAGE}" in trace, trace
    gui = next((i for i, line in enumerate(trace) if line.startswith("gui ")), None)
    assert gui is not None, f"no GUI\n{trace}\n{out}"
    assert trace.index(f"mod {I915}") < gui < trace.index(f"mod {STORAGE}"), trace


@POSIX
def test_the_gui_start_rides_on_the_boot_marks(tmp_path):
    trace, out, run = _init(tmp_path)
    net = next((line for line in trace if "s=agent-net" in line), "")
    assert ",gui:3.21" in net, f"{net}\n{trace}\n{out}"
    assert (run / "gui.pid").read_text().strip() in next(l for l in trace if l.startswith("hello "))


@POSIX
def test_a_firmware_framebuffer_starts_no_early_gui(tmp_path):
    # simpledrm/efifb is torn down when the GPU driver loads: a GUI on it is a
    # black screen. With no GPU framebuffer the agent starts it after the hello.
    started = time.monotonic()
    trace, out, run = _init(tmp_path, fb_name="simpledrmdrmfb")
    assert not [line for line in trace if line.startswith("gui ")], trace
    assert "hello gui.pid=" in trace, trace
    assert not (run / "gui.early.wait").exists()
    assert time.monotonic() - started < HOLD_S + 10


@POSIX
def test_the_installer_iso_starts_no_early_gui(tmp_path):
    box = tmp_path / "box"
    kiosk = _write(box / "kiosk", KIOSK_TRACE)
    station = _write(box / "station", STATION_OK)
    fb = box / "fb0"
    _write(box / "sysroot" / "sys" / "class" / "graphics" / "fb0" / "name", "i915drmfb\n", 0o644)
    _write(box / "cmdline", "imagesrv.server=127.0.0.1:9 imagesrv.mode=installer\n", 0o644)
    (box / "run").mkdir()
    script = (f"export RUN_DIR={posix(box / 'run')!r} LIB_DIR={posix(LIB)!r} TRACE={posix(box / 'trace')!r} "
              f"CMDLINE_FILE={posix(box / 'cmdline')!r} GUI_KIOSK={posix(kiosk)!r} GUI_STATION={posix(station)!r} "
              f"FB_DEV={posix(fb)!r} SYSROOT={posix(box / 'sysroot')!r}; ln -s /dev/null \"$FB_DEV\"; "
              f". {posix(LIB)}/guiearly.sh; gui_early_boot; echo rc=$?")
    done = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert "rc=0" in done.stdout, done.stdout + done.stderr
    assert not (box / "trace").exists() and not (box / "run" / "gui.pid").exists()


# --- 2. the agent adopts it, hands the role over, or closes it ---------------

#: The bridge as the real one behaves for the keeper (test_gui_keeper_1726):
#: a GUI process and a state writer, both pids where the keeper reads them.
KIOSK_BRIDGE = r'''#!/bin/sh
echo "run screen=$GUI_SCREEN" >> "$RUN_DIR/kiosk.runs"
mkdir -p "$RUN_DIR/gui"
sleep 300 & s=$!
( while :; do sleep 1; done ) & w=$!
echo "$s $w" > "$RUN_DIR/gui/station.pid"
echo "$s $w" >> "$RUN_DIR/stations"
wait "$s"
'''


def _agent(tmp: Path, body: str, bridge: str = KIOSK_BRIDGE) -> subprocess.Popen:
    """init's early start (gui_early_boot), then `body` as the agent."""
    run = tmp / "run"
    run.mkdir()
    kiosk = _write(tmp / "kiosk", bridge)
    station = _write(tmp / "station", STATION_OK)
    fb = tmp / "fb0"
    _write(tmp / "sysroot" / "sys" / "class" / "graphics" / "fb0" / "name", "i915drmfb\n", 0o644)
    _write(tmp / "cmdline", "imagesrv.server=127.0.0.1:9\n", 0o644)
    # #2275: the real stat, each call recorded -- the keeper's looks at the GUI's beat (ui.sh gui_beat_fresh)
    _write(tmp / "bin" / "stat", f'#!/bin/sh\necho "$*" >> "$RUN_DIR/stat.calls"\nexec {shutil.which("stat")} "$@"\n')
    script = (
        f"export PATH={shlex.quote(posix(tmp / 'bin'))}:\"$PATH\"; "
        f"export RUN_DIR={shlex.quote(posix(run))} LIB_DIR={shlex.quote(posix(LIB))} IMAGESRV_TEST=1 WAIT_HELPER_S=5 "
        f"CMDLINE_FILE={shlex.quote(posix(tmp / 'cmdline'))} GUI_KIOSK={shlex.quote(posix(kiosk))} "
        f"GUI_STATION={shlex.quote(posix(station))} FB_DEV={shlex.quote(posix(fb))} "
        f"SYSROOT={shlex.quote(posix(tmp / 'sysroot'))}; ln -s /dev/null \"$FB_DEV\"; "
        f"( . {posix(LIB)}/guiearly.sh; gui_early_boot ); "                       # init's part
        f"for l in common jsonq waits ui localboot splash guiparent guiearly; do . {posix(LIB)}/$l.sh; done; "
        "MAC=aa:bb:cc:dd:ee:01 IP=10.0.0.9; "
        + body
    )
    out = (tmp / "out.txt").open("w", encoding="utf-8")
    return subprocess.Popen([BASH, "-c", 'export PATH="/usr/bin:/bin:$PATH"; ' + script], stdout=out,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(REPO), start_new_session=True)


def _looks(tmp: Path) -> int:
    """How many times the keeper has read the GUI's heartbeat (gui_beat_fresh's stat)."""
    return sum("gui/beat" in line for line in _lines(tmp / "run" / "stat.calls"))


def _report(tmp: Path) -> str:
    return "\n".join(f"--- {n}\n" + (p.read_text(encoding="utf-8") if p.exists() else "(none)")
                     for n, p in (("out", tmp / "out.txt"), ("log", tmp / "run" / "agent.log"),
                                  ("kiosk.runs", tmp / "run" / "kiosk.runs")))


@POSIX
def test_the_agent_adopts_the_early_gui_and_hands_it_the_role_without_a_restart(tmp_path):
    proc = _agent(tmp_path, "gui_early_adopt; D_ROLE=cloner; gui_parent; echo rc1=$?; "
                            "gui_parent; echo rc2=$?; sleep 300")
    try:
        role = tmp_path / "run" / "gui.screen"
        assert _await(role.exists, 10), _report(tmp_path)
        assert role.read_text().splitlines() == ["cloner", "aa:bb:cc:dd:ee:01", "10.0.0.9"], _report(tmp_path)
        out = tmp_path / "out.txt"
        assert _await(lambda: "rc2=" in out.read_text(), 10), _report(tmp_path)
        assert "rc1=0" in out.read_text() and "rc2=0" in out.read_text(), _report(tmp_path)
        # #2275: the keeper's own looks, not a clock. It restarts a GUI only after a look
        # (gui_keep: a stale beat, then gone), and a new bridge names itself in kiosk.runs
        # before the next look -- so three looks after the hand-over with one run are
        # three restarts that did not happen. A wall-clock 1.5 s was one tick under load.
        runs = tmp_path / "run" / "kiosk.runs"
        seen = _looks(tmp_path)
        _await(lambda: _looks(tmp_path) >= seen + 3 or len(_lines(runs)) > 1, 30)
        assert _lines(runs) == ["run screen=boot"], _report(tmp_path)
        assert _looks(tmp_path) >= seen + 3, _report(tmp_path)
    finally:
        _killpg(proc)


@POSIX
@pytest.mark.parametrize("decision,role,closed", [
    ("unknown", "null", True), ("local", "build", True), ("restore", "classroom_station", True),
    ("restore", "cloner", False), ("build_console", "build", False), ("wait_poll", "cloner", False),
])
def test_a_path_that_draws_no_gui_closes_the_early_one_cleanly(tmp_path, decision, role, closed):
    proc = _agent(tmp_path, f"gui_early_adopt; D_ROLE={role}; gui_early_settle {decision}; "
                            'echo "settled pid=${_gui_pid:-none}"; sleep 300')
    try:
        out = tmp_path / "out.txt"
        assert _await(lambda: "settled" in out.read_text(), 20), _report(tmp_path)
        # an open path does not wait for the GUI: its bridge may still be starting
        assert _await(lambda: _lines(tmp_path / "run" / "stations"), 10), _report(tmp_path)
        s, w = (int(p) for p in _lines(tmp_path / "run" / "stations")[0].split())
        if closed:
            # the GUI process got SIGTERM (it gives the display back), its writer too,
            # and the keeper is gone: nothing starts a second GUI
            assert not _alive(s) and not _alive(w), _report(tmp_path)
            assert "settled pid=none" in out.read_text()
            assert not (tmp_path / "run" / "gui.pid").exists()
            time.sleep(2.5)
            assert len(_lines(tmp_path / "run" / "kiosk.runs")) == 1, _report(tmp_path)
            assert f"the '{decision}' path draws none" in (tmp_path / "run" / "agent.log").read_text()
        else:
            assert _alive(s) and "settled pid=none" not in out.read_text(), _report(tmp_path)
    finally:
        _killpg(proc)


#: The bridge still starting when the path is decided: a second before its GUI is up.
KIOSK_SLOW = KIOSK_BRIDGE.replace("mkdir -p", "sleep 1\nmkdir -p", 1)


@POSIX
def test_a_gui_still_starting_when_the_path_draws_none_is_closed_too(tmp_path):
    """Round 20 (31/10): settled before the bridge named its GUI, the close killed
    the keeper only -- and the bridge the keeper had already started brought the
    GUI up a moment later, over the path that draws none. The full gate's load
    opened that window; here the bridge holds it open for a second."""
    proc = _agent(tmp_path, "gui_early_adopt; D_ROLE=null; gui_early_settle unknown; "
                            'echo "settled pid=${_gui_pid:-none}"; sleep 300', KIOSK_SLOW)
    try:
        out = tmp_path / "out.txt"
        assert _await(lambda: "settled" in out.read_text(), 20), _report(tmp_path)
        assert _await(lambda: _lines(tmp_path / "run" / "stations"), 5), _report(tmp_path)
        s, w = (int(p) for p in _lines(tmp_path / "run" / "stations")[0].split())
        assert _await(lambda: not _alive(s) and not _alive(w), 3), _report(tmp_path)
        assert "settled pid=none" in out.read_text(), _report(tmp_path)
    finally:
        _killpg(proc)


# --- 3. the bridge: the boot screen, then the role's own writer ---------------

def _bridge(tmp: Path, body: str) -> subprocess.CompletedProcess:
    run = tmp / "run"
    (run / "gui").mkdir(parents=True)
    script = (f"export RUN_DIR={posix(run)!r} GUI_DIR={posix(run / 'gui')!r} SERVER=http://10.0.0.1:8080 "
              f"GUI_SCREEN=boot MAC= IP=; "
              f"for l in common waits jsonq guistate guiearly; do . {posix(LIB)}/$l.sh; done; "
              'cloner_gui_state_loop() { echo "cloner-loop $GUI_SCREEN $MAC $IP"; }; '
              'gui_state_loop() { echo "build-loop [$GUI_SCREEN] $MAC $IP"; }; ' + body)
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          timeout=60, stdin=subprocess.DEVNULL)


@POSIX
def test_the_boot_screen_shows_its_stages(tmp_path):
    run = tmp_path / "run"
    done = _bridge(tmp_path, 'gui_early_state; echo ===; echo IFACE=eth0 > "$RUN_DIR/net.conf"; gui_early_state; echo ===; '
                             'echo "fail=x" > "$RUN_DIR/fail.state"; gui_early_state')
    first, second, third = done.stdout.split("===\n")
    assert "message=מתחבר לשרת…|" in first and "שלב 1 מתוך 3: רשת" in first, done.stdout
    assert "רשת ← שרת ← משימה" in first and "screen=boot" in first, done.stdout
    assert "server_addr=10.0.0.1" in first, done.stdout
    assert "שלב 2 מתוך 3: שרת" in second, done.stdout
    assert "fail=x" in third, done.stdout                # die_local's failure screen, while booting
    assert run.exists()


@POSIX
@pytest.mark.parametrize("role,loop,mode", [
    ("cloner", "cloner-loop cloner aa:bb:cc:dd:ee:01 10.0.0.9", "menu"),
    ("menu", "build-loop [] aa:bb:cc:dd:ee:01 10.0.0.9", "menu"),
    ("class", "build-loop [class] aa:bb:cc:dd:ee:01 10.0.0.9", "classes"),
])
def test_the_bridge_switches_to_the_role_writer_in_the_same_process(tmp_path, role, loop, mode):
    run = tmp_path / "run"
    body = ("printf 'menu\\n' > \"$GUI_DIR/mode\"; "
            f"( sleep 0.6; : > \"$RUN_DIR/net.conf\"; sleep 0.6; "
            f"printf '%s\\n' {role} aa:bb:cc:dd:ee:01 10.0.0.9 > \"$RUN_DIR/gui.screen\" ) & "
            "gui_early_loop")
    done = _bridge(tmp_path, body)
    assert done.stdout.strip().splitlines()[-1] == loop, done.stdout + done.stderr
    assert (run / "gui" / "mode").read_text(encoding="utf-8").strip() == mode
    assert "שלב 3 מתוך 3: משימה" in (run / "gui" / "state").read_text(encoding="utf-8")


@POSIX
def test_the_state_writers_carry_the_role_and_the_address(tmp_path):
    done = _bridge(tmp_path, "GUI_SCREEN=cloner MAC=aa:bb:cc:dd:ee:01 IP=10.0.0.9 gui_hello_state")
    lines = done.stdout.splitlines()
    assert {"screen=cloner", "ip=10.0.0.9", "mac=aa:bb:cc:dd:ee:01"} <= set(lines), done.stdout + done.stderr


# --- 4. the GUI process takes the role from the state file, once ---------------

DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "ui.h"
#include "state.h"
/* weak: the driver builds against the code before #1709 too -- the negative
 * control fails on behaviour, not on a missing symbol */
void app_take_screen(App *a) __attribute__((weak));
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static const char *name(Screen s) {
    return s == SCREEN_MESSAGE ? "message" : s == SCREEN_STANDBY ? "standby" : s == SCREEN_CLONER ? "cloner"
         : s == SCREEN_LOGIN ? "login" : s == SCREEN_CLASS ? "class" : "other";
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    for (int i = 1; i < argc; i++) {                    /* one App: the same process, no restart */
        FILE *f = fopen(argv[i], "r"); if (!f) return 2;
        memset(&a.st, 0, sizeof a.st);
        state_parse(&a.st, f); fclose(f);
        if (app_take_screen) app_take_screen(&a);
        app_route(&a);
        printf("%d screen=%s cloner=%d mac=%s ip=%s\n", i, name(a.screen), a.force_cloner, a.mac, a.ip);
    }
    return 0;
}
'''

needs_gui = pytest.mark.skipif(
    not ((shutil.which("cc") or shutil.which("gcc")) and _pkgconfig("pangocairo", "cairo", "libdrm")),
    reason="native-gui נבנה על המעבדה בלבד")


@needs_gui
def test_the_gui_process_goes_from_the_boot_screen_to_the_cloner_screen(tmp_path):
    (tmp_path / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(tmp_path / "drv"), posix(tmp_path / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    states = ["message=מתחבר לשרת…|רשת ← שרת ← משימה\nscreen=boot\nserver_addr=10.0.0.1\n",
              "hello_age=1\nhello_rc=200\nscreen=cloner\nip=10.0.0.9\nmac=aa:bb:cc:dd:ee:01\n",
              "message=לא ניתן לאמת את מצב התחנה|x\n"]       # gui_state_loop's error path: no screen= -- it stays
    paths = []
    for i, body in enumerate(states):
        p = tmp_path / f"s{i}.txt"
        p.write_text(body, encoding="utf-8", newline="\n")
        paths.append(posix(p))
    r = subprocess.run([str(tmp_path / "drv"), *paths], capture_output=True, text=True, encoding="utf-8",
                       timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    got = r.stdout.splitlines()
    assert got[0].startswith("1 screen=message cloner=0"), got
    assert got[1] == "2 screen=standby cloner=1 mac=aa:bb:cc:dd:ee:01 ip=10.0.0.9", got
    assert got[2] == "3 screen=standby cloner=1 mac=aa:bb:cc:dd:ee:01 ip=10.0.0.9", got
