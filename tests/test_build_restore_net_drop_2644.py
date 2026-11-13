"""#2644 -- a build machine that loses the network mid-restore behaves like a cloner that was cut.

Nadav, 07/11: "the behaviour must not be different" -- the same wait, the same ceiling, the
same screen. On metal the night before, a cloner's NIC was down 75 s and its stream went on
from 30% to 32%. The cloner's code is the reference, and the build machine now runs it:

* **the wait and its ceiling.** The cloner's receiver (udp-receiver) holds a cut for
  ``--receive-timeout $UDPCAST_STALL_TIMEOUT`` (300 s, or the console's lost wait + 120 --
  timing.sh). The build machine's restore streams over HTTP, and its curl gave up after
  ``HTTP_STALL_TIMEOUT`` (120 s, a constant the console never moved). Now that curl waits
  out the cloner's ceiling: the TCP connection survives the cut and the stream goes on from
  the byte it stopped at -- the same request, nothing written twice or skipped (the sha and
  the bytes on the "disk" say so). Past the ceiling: a visible failure, as on the cloner.
* **the screen and the tunes.** The cloner's kiosk samples the stream once a second
  (roundbeep.sh ``stream_beep_tick``: the tunes "network lost"/"back") and writes the red and
  green bars (``stream_net_record``, mockup 08). The single restore now keeps its stream's
  counter and the source's rc where the cloner's stream keeps them ($RUN_DIR/bytes.raw,
  $RUN_DIR/source.rc), its card's writer (srgui.sh) calls those same two functions, and the
  progress card draws the cloner's own bar (``net_bar``).

The cut is a pause in a real HTTP server's body (no test can pull a cable); the curl, the
fanout (compiled) and the shell (busybox ash) are the real ones.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_beeps_2067 import BUSYBOX, expected, frequencies, run_ash
from test_timeouts import make_stubs

GCC = shutil.which("gcc") or shutil.which("cc")
_AGENT = requires_native(("busybox", BUSYBOX), ("gcc", GCC), "curl", "jq", posix=True,
                         why="the agent's shell, the compiled fanout, the real curl; jq reads the role")

CHUNK = 256 * 1024
HEAD, TAIL = 4, 6                       # pieces before and after the cut
PAYLOAD = b"".join(hashlib.sha256(i.to_bytes(4, "big")).digest() for i in range((HEAD + TAIL) * CHUNK // 32))
SHA = hashlib.sha256(PAYLOAD).hexdigest()

#: partclone.dd as the disk: the bytes it was given, in order, into its -O node
PARTCLONE = '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do [ "$1" = -O ] && o=$2; shift; done\ncat > "$o"\n'


class _Server(ThreadingHTTPServer):
    daemon_threads = True               # a handler asleep in its cut does not hold the shutdown
    pause = 0.0
    gets: list[str]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:           # noqa: N802 -- BaseHTTP's name
        self.server.gets.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        try:
            for i in range(HEAD + TAIL):
                if i == HEAD:
                    time.sleep(self.server.pause)   # the cut: not a byte, the connection kept
                self.wfile.write(PAYLOAD[i * CHUNK:(i + 1) * CHUNK]); self.wfile.flush()
                time.sleep(0.3 if i < HEAD else 0.4)
        except (BrokenPipeError, ConnectionResetError):
            pass                        # the client gave up -- what the old ceiling did

    def log_message(self, *_args) -> None:
        """No noise on the run's stderr."""


@pytest.fixture
def server():
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.gets = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)


@pytest.fixture(scope="module")
def stubs(tmp_path_factory) -> Path:
    """The compiled fanout -- its -P counter moves while bytes come, as on the machine."""
    d = tmp_path_factory.mktemp("stubs2644")
    subprocess.run([GCC, "-O2", "-o", str(d / "fanout"), str(AGENT / "fanout.c")],
                   check=True, stdin=subprocess.DEVNULL, timeout=120)
    return d


def _restore(box: Path, stubs: Path, server: _Server, *, ceiling: int, http: int,
             sample: bool = False) -> dict:
    """The real restore_partition of the build machine (unicast) under busybox ash. ``ceiling``
    is the cloner's (UDPCAST_STALL_TIMEOUT = WAIT_STREAM_STALL_S, as timing.sh keeps them),
    ``http`` the old HTTP_STALL_TIMEOUT. ``sample``: the card's writer samples every 0.3 s."""
    run, dev = box / "run", box / "dev"
    (box / "sys/event5/device").mkdir(parents=True)
    (box / "sys/event5/device/name").write_text("PC Speaker\n", newline="\n")
    (dev / "input").mkdir(parents=True); run.mkdir(parents=True)
    (run / "response.json").write_text(json.dumps({"role": "build", "ui": {"cloner_beep": True}}), encoding="utf-8")
    url = "http://%s:%d" % server.server_address[:2]
    sampler = (
        'mkdir -p "$RUN_DIR/gui"; printf "sda\\nשחזור: Lab\\nדיסק 1\\n1\\nLab\\n" > "$RUN_DIR/gui/restore.head"\n'
        '( while [ ! -e "$RUN_DIR/stop" ]; do sr_gui_state > "$RUN_DIR/s.next" 2>> "$RUN_DIR/s.err"\n'
        '  { cat "$RUN_DIR/s.next"; echo --; } >> "$RUN_DIR/samples"; sleep 0.3; done; wait ) & sp=$!\n'
    ) if sample else "sp=\n"
    script = (
        make_stubs(stubs, {"fanout": None, "partclone.dd": PARTCLONE})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} SYSFS_INPUT={posix(box / 'sys')!r}\n"
        + "export D_ROLE=build WAIT_POLL_S=1 WAIT_STREAM_START_S=30 WAIT_HELPER_S=10 WAIT_DRAWER_S=30 "
        + f"WAIT_STREAM_STALL_S={ceiling} UDPCAST_STALL_TIMEOUT={ceiling} HTTP_STALL_TIMEOUT={http} "
        + "HTTP_TIMEOUT=4 NETLOST_AFTER_S=2\n"
        + agent_sources("common", "sysinfo", "diskinfo", "waits", "jsonq", "progress", "restore", "writeback", "drawers",
                        "drawerfeed", "verdict", "failmark", "ntfsboot", "roundbeep", "srgui", "clonergui") + "\n"
        + "node_is_block() { true; }\n"   # a regular file stands for the partition (test_timeouts.py)
        + f'target_init sda {len(PAYLOAD)}; target_set sda writing; echo writing > "$RUN_DIR/state"\n'
        + 'echo 1 > "$RUN_DIR/targets/sda/partition"\n'
        + sampler
        + "s=$(date +%s)\n"
        + f"restore_partition unicast {url} img sda 1 dd p1.zst {SHA} '' > {posix(box)}/pipe.out 2>&1\n"
        + 'echo "rc=$? elapsed=$(( $(date +%s) - s ))"\n'
        + '[ -z "$sp" ] || { sleep 1; : > "$RUN_DIR/stop"; wait "$sp"; }\n'
        + "wait\n"
    )
    out = run_ash(script, timeout=90)
    fields = dict(f.split("=", 1) for f in out.split() if re.fullmatch(r"(rc|elapsed)=\d+", f))
    assert "rc" in fields, out
    read = lambda p: p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""  # noqa: E731
    return {"rc": int(fields["rc"]), "elapsed": int(fields["elapsed"]), "out": out,
            "disk": (dev / "sda1").read_bytes() if (dev / "sda1").exists() else b"",
            "log": read(run / "agent.log"), "samples": read(run / "samples"), "err": read(run / "s.err")}


# --- the wait and its ceiling --------------------------------------------------------


@_AGENT
def test_a_cut_shorter_than_the_cloners_ceiling_goes_on_from_the_same_byte(tmp_path, stubs, server):
    """10 s without a byte; the cloner's ceiling is 20, HTTP's own was 2 (as 120 is to 300).
    curl judges its speed over a moving window of ~5 s, so a speed-time of 2 gives up ~7 s
    into a cut (measured: a 5 s cut passed on main) -- hence 10.
    Negative control: on main curl gives up mid-cut and the partition fails."""
    server.pause = 10
    r = _restore(tmp_path, stubs, server, ceiling=20, http=2)
    assert r["rc"] == 0, (r["out"], r["log"])
    assert r["disk"] == PAYLOAD, f"{len(r['disk'])} of {len(PAYLOAD)} bytes, in order"   # none twice, none skipped
    assert len(server.gets) == 1, server.gets   # the same request went on -- not a restart
    assert r["elapsed"] >= 10, r["elapsed"]      # it did wait the cut out


@_AGENT
def test_a_cut_past_the_ceiling_fails_visibly(tmp_path, stubs, server):
    """The guard: past the cloner's ceiling (3 s) the partition fails -- in seconds, not at
    the end of the cut, and the disk does not hold the image. Passes on main too (its
    wait_progress had the same 300); what it pins is that waiting longer did not become
    waiting for ever."""
    server.pause = 12
    r = _restore(tmp_path, stubs, server, ceiling=3, http=60)
    assert r["rc"] == 1, (r["out"], r["log"])
    assert r["elapsed"] < 12, r["elapsed"]
    assert r["disk"] != PAYLOAD
    assert "partition 1 (p1.zst)" in r["log"], r["log"]


def _argv(tmp_path: Path, body: str) -> list[str]:
    """stream_source unicast under ash with a curl that only records its argv."""
    run = tmp_path / "run"; run.mkdir(parents=True)
    curl = tmp_path / "bin" / "curl"; curl.parent.mkdir()
    curl.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RUN_DIR/curl.argv"\n', newline="\n"); curl.chmod(0o755)
    run_ash(f'export PATH={posix(curl.parent)!r}:"$PATH" RUN_DIR={posix(run)!r}\n'
            + agent_sources("common", "timing", "waits", "jsonq", "sysinfo", "diskinfo", "progress", "failmark", "restore")
            + "\n" + body)
    return (run / "curl.argv").read_text(encoding="utf-8").split("\n")


def _flag(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@requires_native(("busybox", BUSYBOX), "jq", posix=True, why="timing_take reads the answer with jq")
@pytest.mark.parametrize("answer, ceiling", [
    ("{}", "300"),                              # the agent's own: the cloner's 300, not HTTP's 120
    ('{"lost_wait_seconds": 600}', "720"),      # the console's lost wait + 120, as udp-receiver's
])
def test_the_restore_streams_under_the_cloners_ceiling(tmp_path, answer, ceiling):
    """The one number: udp-receiver's --receive-timeout is curl's --speed-time, and the console
    moves both (timing.sh). Negative control: on main --speed-time is 120 in both rows."""
    (tmp_path / "answer.json").write_text(answer, encoding="utf-8")
    argv = _argv(tmp_path, f"timing_take {posix(tmp_path / 'answer.json')!r} > /dev/null; "
                           "stream_source unicast http://s img p2.zst 600")
    assert _flag(argv, "--speed-time") == ceiling, argv
    assert argv[-2] == "http://s/api/v1/images/img/files/p2.zst", argv


@requires_native(("busybox", BUSYBOX), posix=True, why="the agent's shell")
def test_other_downloads_keep_their_own_http_ceiling(tmp_path):
    """The guard: only the restore's stream takes the cloner's ceiling -- a driver package
    (postdeploy.sh) still gives up after HTTP_STALL_TIMEOUT."""
    argv = _argv(tmp_path, "http_get_stream http://s/api/v1/agent/drivers/x/files/y")
    assert _flag(argv, "--speed-time") == "120", argv


# --- the tunes and the bars ----------------------------------------------------------


@_AGENT
def test_the_cut_is_the_cloners_red_then_green_bar_and_its_two_tunes(tmp_path, stubs, server):
    """The card's writer (sr_gui_state, once a tick) during a 6 s cut, with the cloner's 30 s
    as 2: "net=lost|<last byte>|<ceiling>" while it lasts, "net=back|<s>|<at>" when bytes
    move, and the speaker plays netlost then netback -- the cloner's own records and tunes.
    Negative control: on main no record is written and no tune plays (a 6 s cut itself is
    survived there -- the ceiling is the test above)."""
    server.pause = 6
    r = _restore(tmp_path, stubs, server, ceiling=12, http=2, sample=True)
    assert r["rc"] == 0 and r["disk"] == PAYLOAD, (r["out"], r["log"])
    recs = [l for l in r["samples"].splitlines() if l.startswith("net=")]
    lost = [i for i, l in enumerate(recs) if re.fullmatch(r"net=lost\|\d+\|12", l)]
    back = [i for i, l in enumerate(recs) if re.fullmatch(r"net=back\|\d+\|\d+", l)]
    assert lost and back and lost[-1] < back[0], recs
    assert not [l for l in recs if not re.fullmatch(r"net=(lost|back)\|\d+\|\d+", l)], recs
    away = int(recs[back[0]].split("|")[1])
    assert 4 <= away <= 9, recs[back[0]]                  # the cut, give or take a sample
    assert frequencies(tmp_path) == expected("netlost") + expected("netback")
    assert not r["err"].strip(), r["err"]                 # nothing of the tick is a "not found"


@requires_native(("bash", BASH))
def test_the_restore_card_carries_the_record(tmp_path):
    """The wiring alone, as test_net_bars_2067 has it for the cloner's state."""
    run = tmp_path / "run"; (run / "gui").mkdir(parents=True)
    (run / "beep.net").write_text("lost\n", newline="\n")
    (run / "beep.netsince").write_text("1955\n", newline="\n")
    script = (f"export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/core/common.sh; "
              + agent_sources("sysinfo", "diskinfo", "progress", "failmark", "roundbeep", "clonergui", "srgui")
              + 'stream_beep_tick() { :; }; target_init sda 100; target_set sda writing; '
              'printf "sda\\nשחזור\\nדיסק 1\\n1\\nx\\n" > "$RUN_DIR/gui/restore.head"; sr_gui_state')
    out = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script], capture_output=True,
                         text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "net=lost|1955|300" in out.stdout.splitlines(), out.stdout


# --- the screen: the real parser and the progress card, every text it made -------------

GUI = AGENT.parent / "native-gui"


def _pkgconfig(*pkgs: str) -> bool:
    return bool(shutil.which("pkg-config")) and subprocess.run(
        ["pkg-config", "--exists", *pkgs], stdin=subprocess.DEVNULL).returncode == 0


_GUI = requires_native(("cc", GCC), ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
                       ("bash", BASH), why="native-gui נבנה על המעבדה בלבד")

DRIVER = r'''
#include <stdio.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_progress = 1;   /* --screen progress */
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f);
    app_route(&a);
    printf("screen=%d progress=%d\n", a.screen, SCREEN_PROGRESS);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_destroy(s);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("drv2644")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _card(driver: Path, tmp_path: Path, net: str | None) -> list[str]:
    now = int(time.time())
    state = ("title=\u200fשחזור: Windows 11 Lab\nsub=\u200fדיסק 1 · מחיצה 2 מתוך 3 · ממתין לזרם מהשרת · 0 MB/s\n"
             "pct=31\nbytes=12400000000\nmoving=1\ncancel=1\n"
             + (re.sub(r"NOW-(\d+)", lambda m: str(now - int(m.group(1))), net) + "\n" if net else "")
             + f"updated={now}\n")
    p = tmp_path / "state.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    screen, progress = re.findall(r"screen=(\d+) progress=(\d+)", r.stdout)[0]
    assert screen == progress, r.stdout                    # the build machine's restore card
    got = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
    assert any("שחזור: Windows 11 Lab" in t for t in got), got   # the card itself, under the bar
    return got


def _net(got: list[str]) -> list[str]:
    return [t for t in got if t.startswith(("הרשת ", "הזרם מהשרת עומד", "הזרם ממשיך"))]


@_GUI
def test_the_restore_card_draws_the_cloners_red_bar(driver, tmp_path):
    """Negative control: on main the progress card draws no bar -- these texts are absent."""
    got = _card(driver, tmp_path, "net=lost|NOW-40|300")
    assert any(re.fullmatch(r"הרשת נותקה לפני 4[0-9] שניות", t) for t in got), got
    assert any(t.startswith("הזרם מהשרת עומד.") and "תוך 5 דקות" in t for t in got), got


@_GUI
def test_the_restore_card_draws_the_cloners_green_bar_for_five_seconds(driver, tmp_path):
    assert "הרשת חזרה אחרי 52 שניות" in _card(driver, tmp_path, "net=back|52|NOW-1")
    assert _net(_card(driver, tmp_path, "net=back|52|NOW-6")) == []


@_GUI
def test_no_record_no_bar_on_the_restore_card(driver, tmp_path):
    """The guard: a restore without a cut looks as before."""
    assert _net(_card(driver, tmp_path, None)) == []
