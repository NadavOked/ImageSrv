"""#2046 (Nadav 24/10, mockup 11) -- the check after the write is SEEN: the bar of each
drawer stays blue (the write is whole) and a green fills it from the start side by how
much of the disk was read back, with a percent; a check that stopped leaves a red tick
where it stopped; "אומת" is said only for a disk whose every chunk was read back.

Four layers, each judged by what the layer below says -- no layer vouches for itself:

* ``fanout --readback -p <file> <base> <total>`` (C): the counter. The file ends on the
  disk's whole when the check passed, on the byte it reached when it failed, and a file
  that cannot be written never changes what the check says (it is a bar, not a verdict).
* readback.sh / readbacklc.sh / rbprog.sh (shell): each partition is given the bytes of the
  ones before it as its base and the disk's whole as its total, and a disk with no list
  is given no bar at all (no invented progress).
* the kiosk (clonergui.sh / lcgui.sh): ``drawer_rbp=`` / ``lc_rbp=`` -- dev|done|total|
  verdict|rate -- and nothing at all from a file that is not two numbers.
* the screens (C, drawn for real by a driver): the pixels of the blue and the green, and
  the words.

The C and the screens run on the lab (``requires_native``); the shell layers run with bash.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import pcl_image as P
import test_readback_agent_1805 as A
from native import requires_native
from test_cloner_gui import BASH, GUI, PORTS, PRELUDE, _pkgconfig, env, posix, sh, target

REPO = Path(__file__).resolve().parent.parent
FANOUT_C = REPO / "agent" / "fanout.c"
CHUNK = P.CHUNK_BYTES

needs_gcc = requires_native("gcc", posix=True, why="fanout --readback is C, read with O_DIRECT")


# --- fanout: the counter ------------------------------------------------------------


@pytest.fixture(scope="module")
def fanout(tmp_path_factory):
    binary = tmp_path_factory.mktemp("rbprog") / "fanout"
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(binary), str(FANOUT_C)],
                   check=True, stdin=subprocess.DEVNULL)
    return binary


def _env(path: Path) -> dict:
    e = dict(os.environ)
    try:
        os.close(os.open(path, os.O_RDONLY | os.O_DIRECT))
    except OSError as exc:
        if exc.errno != errno.EINVAL:
            raise
        e["FANOUT_READBACK_CACHED"] = "1"      # a tmpfs refuses O_DIRECT; the agent never sets this
    return e


def _run(fanout: Path, dev: Path, stdin: bytes, *args: str):
    feed = dev.parent / f"{dev.name}.stdin"
    feed.write_bytes(stdin)
    with feed.open("rb") as fh:
        res = subprocess.run([str(fanout), "--readback", *args], stdin=fh, capture_output=True,
                             timeout=120, env=_env(dev))
    return res.returncode, res.stdout.decode(), res.stderr.decode()


def _disk(tmp_path: Path, chunks: int = 20, bs: int = 4096):
    per = CHUNK // bs
    n = chunks * per
    used = list(range(n))
    data = P.stream(used, bs)
    dev = tmp_path / "sdb3"
    dev.write_bytes(P.device(n, used, bs))
    return dev, P.image(n, used, bs), len(data)


@needs_gcc
@pytest.mark.parametrize("sample", [False, True])
def test_the_file_ends_on_the_disks_whole_with_the_partitions_before_it_counted(fanout, tmp_path, sample):
    """base 1000 + this partition's bytes = the total, in a full read and in a sample (a chunk nobody
    drew is passed, not read -- the bar still reaches the end)."""
    dev, img, nbytes = _disk(tmp_path)
    prog = tmp_path / "rb_prog"
    flags = ["-x", "-s", "1805", "50"] if sample else ["-x"]
    rc, out, err = _run(fanout, dev, img, "-p", str(prog), "1000", str(1000 + nbytes), *flags, str(dev))
    assert rc == 0, err
    assert prog.read_text() == f"{1000 + nbytes} {1000 + nbytes}\n"


@needs_gcc
def test_a_check_that_stops_leaves_the_file_on_the_byte_it_reached(fanout, tmp_path):
    """Negative: the device ends after 12 of 20 chunks. The check fails (rc 4) and the file says 12 of 20 -- the
    tick the screen draws; not 0 (the open), not 20 (the plan). The last write is forced: it is not rate-limited."""
    dev, img, nbytes = _disk(tmp_path)
    dev.write_bytes(dev.read_bytes()[:12 * CHUNK])
    prog = tmp_path / "rb_prog"
    rc, out, err = _run(fanout, dev, img, "-p", str(prog), "0", str(nbytes), "-x", str(dev))
    assert rc == 4, err
    assert prog.read_text() == f"{12 * CHUNK} {nbytes}\n"


@needs_gcc
def test_a_partition_read_whole_counts_in_bytes_too(fanout, tmp_path):
    raw = 3 * CHUNK + 12345
    dev = tmp_path / "sdb2"
    dev.write_bytes(os.urandom(raw))
    prog = tmp_path / "rb_prog"
    rc, out, err = _run(fanout, dev, b"", "-p", str(prog), "0", str(raw), "-x", str(dev), str(raw))
    assert rc == 0, err
    assert prog.read_text() == f"{raw} {raw}\n"


@needs_gcc
def test_a_file_that_cannot_be_written_changes_nothing_in_the_verdict(fanout, tmp_path):
    """A bar is not worth a disk's verdict: the same list on stdout, rc 0, and the reason on stderr."""
    dev, img, nbytes = _disk(tmp_path, chunks=3)
    plain = _run(fanout, dev, img, "-x", str(dev))
    bar = _run(fanout, dev, img, "-p", str(tmp_path / "no-such-dir" / "rb_prog"), "0", str(nbytes), "-x", str(dev))
    assert plain[0] == bar[0] == 0
    assert plain[1] == bar[1]
    assert "progress file" in bar[2], bar[2]


@needs_gcc
@pytest.mark.parametrize("base, total", [("0", "0"), ("x", "10"), ("0", "-5"), ("0", "")])
def test_an_unreadable_bar_is_a_usage_error_not_a_guess(fanout, tmp_path, base, total):
    dev, img, nbytes = _disk(tmp_path, chunks=2)
    rc, out, err = _run(fanout, dev, img, "-p", str(tmp_path / "p"), base, total, "-x", str(dev))
    assert rc == 2 and "bad progress" in err, (rc, err)
    assert out == ""


# --- the shell: what each partition is given ----------------------------------------


pytestmark_shell = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")


def _progs(box: Path, dev: str, parts=(1, 2, 3)) -> list[tuple[str, int, int]]:
    got = []
    for i in parts:
        f = box / f"progs.{dev}{i}"
        assert f.exists(), (dev, i, sorted(p.name for p in box.glob("progs.*")))
        path, base, total = f.read_text().split()
        got.append((path, int(base), int(total)))
    return got


@pytestmark_shell
@pytest.mark.parametrize("mode", ["full", "sample"])
def test_a_round_gives_each_partition_its_base_and_the_disks_whole(tmp_path, mode):
    box = A.box_for(tmp_path)
    res = A.drawers(box, A.manifest(mode))
    sizes = [A.SHAPE[i][1] for i in (1, 2, 3)]
    for dev in ("sdb", "sdc"):
        path = posix(box / "run" / "targets" / dev / "rb_prog")
        assert _progs(box, dev) == [(path, 0, sum(sizes)), (path, sizes[0], sum(sizes)),
                                    (path, sizes[0] + sizes[1], sum(sizes))], res["raw"]
    assert res["t"]["sdb"]["readback"] in ("verified", "sampled")       # the verdict is not the bar's


@pytestmark_shell
def test_a_disk_with_no_list_is_given_no_bar(tmp_path):
    """Negative for the invented progress: an image without lists is written and says "not verified" -- fanout is
    never asked for a counter whose total is 0."""
    box = A.box_for(tmp_path)
    res = A.drawers(box, A.manifest("full", kinds={}))
    assert list(box.glob("progs.*")) == [], res["raw"]
    assert res["t"]["sdb"]["readback"] == "unverified"


@pytestmark_shell
def test_a_local_clone_bar_continues_from_what_the_last_partition_really_passed(tmp_path):
    """The total is the estimate (size when read whole, used_bytes otherwise); the base is what the last
    partition's own "n <chunks> <bytes>" line said it passed."""
    box = A.box_for(tmp_path, extra=("sda",))
    res = A.local(box, "full")
    est = (100 << 20) + (16 << 20) + (50 << 30)                          # manifest(): no used_bytes -> size_bytes
    got = _progs(box, "sdb")
    assert [g[1] for g in got] == [0, A.SHAPE[1][1], A.SHAPE[1][1] + A.SHAPE[2][1]], res["raw"]
    assert {g[2] for g in got} == {est}


@pytestmark_shell
def test_the_agent_loads_rbprog_sh_where_it_loads_readback_sh():
    text = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    assert 'rbprog.sh' in text and text.index("readback.sh") < text.index("rbprog.sh") < text.index("readbacklc.sh")


@pytestmark_shell
def test_a_new_round_forgets_the_last_one_bar():
    """target_init clears rb_prog with the rest of the round: a stale 100% must not greet the next check."""
    text = (REPO / "agent" / "lib" / "core" / "progress.sh").read_text(encoding="utf-8")
    assert '"$_t/rb_prog"' in text


# --- the kiosk: the records ----------------------------------------------------------


def _kiosk(tmp_path: Path, state: str, rb_prog: str | None, verdict: str = "", total: int = 1000, again: bool = False):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    gui = tmp_path / "gui"; gui.mkdir(exist_ok=True)
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"readback_mode":"full"}', newline="\n")
    target(run, "sdb", state, total, total, "0")
    if rb_prog is not None:
        (run / "targets" / "sdb" / "rb_prog").write_text(rb_prog, newline="\n")
    if verdict:
        (run / "targets" / "sdb" / "readback").write_text(verdict + "\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state" + ("; cloner_gui_state" if again else ""))
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text().splitlines(), gui


@requires_native("jq", why="json_get reads the hello for the mode")
def test_a_disk_being_checked_carries_its_bar_and_the_mode(tmp_path):
    lines, _ = _kiosk(tmp_path, "verifying", "350 1000\n")
    assert "drawer_rbp=sdb|350|1000||-1" in lines, lines
    assert "rb_mode=full" in lines


@requires_native("jq", why="json_get reads the hello for the mode")
def test_a_check_that_stopped_has_its_bar_and_no_verdict(tmp_path):
    lines, _ = _kiosk(tmp_path, "failed", "620 1000\n")
    assert "drawer_rbp=sdb|620|1000||-1" in lines, lines


@requires_native("jq", why="json_get reads the hello for the mode")
@pytest.mark.parametrize("verdict", ["verified", "sampled", "unverified"])
def test_a_finished_check_carries_its_verdict(tmp_path, verdict):
    lines, _ = _kiosk(tmp_path, "done", "1000 1000\n", verdict)
    assert f"drawer_rbp=sdb|1000|1000|{verdict}|-1" in lines, lines


@requires_native("jq", why="json_get reads the hello for the mode")
@pytest.mark.parametrize("rb_prog", ["", "garbage\n", "10\n", "5 0\n", "2000 1000\n", "-1 100\n", "10 20 30\n", "1e3 100\n"])
def test_a_file_that_is_not_two_numbers_draws_no_bar(tmp_path, rb_prog):
    """Negative: the bar is drawn only from two numbers with done <= total and total > 0 -- never from a half line."""
    lines, _ = _kiosk(tmp_path, "verifying", rb_prog)
    assert not any(l.startswith("drawer_rbp=") for l in lines), lines


@requires_native("jq", why="json_get reads the hello for the mode")
def test_a_verdict_that_is_not_one_of_the_three_is_not_carried(tmp_path):
    lines, _ = _kiosk(tmp_path, "done", None, "great")
    assert not any(l.startswith("drawer_rbp=") for l in lines), lines


@requires_native("jq", why="json_get reads the hello for the mode")
def test_the_rate_of_the_check_is_its_own_not_the_writes(tmp_path):
    """The write's counters are full by now. The check's rate is the average over the bar itself: the anchor
    is moved back 10 s and the bar moved by 10 MB -> 1 MB/s, and the drawer's rate/eta fields say so."""
    lines, gui = _kiosk(tmp_path, "verifying", "100000 100000000\n")
    assert "drawer_rbp=sdb|100000|100000000||-1" in lines                       # first sample: not measured
    t0, b0 = (gui / "pace.rb.sdb").read_text().split()
    (gui / "pace.rb.sdb").write_text(f"{int(t0) - 100} 100000\n", newline="\n")
    (gui / "moved.rb.sdb").write_text(f"{int(t0)} 100000\n", newline="\n")      # the bytes moved just now
    again, _ = _kiosk(tmp_path, "verifying", "10100000 100000000\n")
    rec = next(l for l in again if l.startswith("drawer_rbp="))
    head, rate = rec.rsplit("|", 1)
    assert head == "drawer_rbp=sdb|10100000|100000000|", again
    assert 95000 <= int(rate) <= 100000, again                                  # 10 MB over 100 s (+ the second the test took)
    drawer = next(l for l in again if l.startswith("drawer=")).split("|")
    assert drawer[6] == rate and 880 <= int(drawer[7]) <= 920, drawer           # the drawer's own rate/eta: the check's, not the write's


@requires_native("jq", why="json_get reads the hello for the mode")
def test_a_check_that_stands_is_not_measured_never_waiting_for_the_stream(tmp_path):
    lines, gui = _kiosk(tmp_path, "verifying", "100000 100000000\n")
    old = int(time.time()) - 60
    (gui / "pace.rb.sdb").write_text(f"{old} 50000\n", newline="\n")
    (gui / "moved.rb.sdb").write_text(f"{old} 100000\n", newline="\n")           # no byte for a minute
    again, _ = _kiosk(tmp_path, "verifying", "100000 100000000\n")
    assert "drawer_rbp=sdb|100000|100000000||-1" in again, again
    drawer = next(l for l in again if l.startswith("drawer="))
    assert "|-1|-1|" in drawer, drawer                                         # not "0": that is "waiting for the stream"


# --- the screens: drawn for real ------------------------------------------------------


pytestmark_gui = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static int near(unsigned v, double c) { int d = (int)v - (int)(c * 255 + .5); return d > -6 && d < 6; }
static long count(cairo_surface_t *s, Rgb c) {
    unsigned char *p = cairo_image_surface_get_data(s); int st = cairo_image_surface_get_stride(s); long n = 0;
    for (int y = 0; y < 800; y++) for (int x = 0; x < 1280; x++) {
        unsigned v = *(unsigned *)(p + y * st + x * 4);
        if (near((v >> 16) & 255, c.r) && near((v >> 8) & 255, c.g) && near(v & 255, c.b)) n++; }
    return n;
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_flush(s);
    printf("blue=%ld\ngreen=%ld\nred=%ld\n", count(s, a.theme->led_write), count(s, a.theme->led_ok), count(s, a.theme->danger));
    cairo_surface_destroy(s);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("rbscreen")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def show(driver: Path, tmp_path: Path, state: str, name: str = "s") -> tuple[dict, list[str]]:
    """Draws the real screen; returns the pixel counts and every string it laid out."""
    p = tmp_path / f"{name}.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    log = tmp_path / f"{name}.log"
    e = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGESRV_GUI_TEXTLOG=str(log))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=e, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    px = {k: int(v) for k, v in (l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)}
    return px, log.read_text(encoding="utf-8").splitlines() if log.exists() else []


GB = 19_500_000_000


def cloner(rows: list[tuple[str, str, str]], extra: str = "") -> str:
    """rows = (state, rbp fields 'done|total|verdict|rate' or '', error)."""
    out = "cloner=office365 v4\ndrawer_count=4\nhello_age=2\nhello_rc=200\n" + extra
    for i, (st, rb, err) in enumerate(rows, 1):
        dev = f"sd{'bcde'[i - 1]}"
        out += f"drawer={i}|{dev}|{st}|{GB}|{GB}|{err}|-1|-1|{i}|SATA {i - 1}\n"
        if rb:
            out += f"drawer_rbp={dev}|{rb}\n"
    return out


@pytestmark_gui
def test_the_blue_stays_whole_while_the_green_grows_over_it(driver, tmp_path):
    """One disk at 0 / 20 / 80 %: blue + green is one bar's worth at every step (the write is whole), and the green
    grows in proportion to the percent. Without the fix there is no green at all and the bar sits at its write share."""
    got = {pct: show(driver, tmp_path, cloner([("verifying", f"{pct}|100||-1", "")]), f"p{pct}")[0] for pct in (0, 20, 80)}
    whole = [got[p]["blue"] + got[p]["green"] for p in (0, 20, 80)]
    assert max(whole) - min(whole) < 0.03 * max(whole), got
    d20 = got[20]["green"] - got[0]["green"]
    d80 = got[80]["green"] - got[0]["green"]
    assert d20 > 400, got                                           # the green is there
    assert 3.4 < d80 / d20 < 4.6, got                               # and it is 4x as long at 4x the percent


@pytestmark_gui
def test_the_title_the_percent_and_the_count_are_the_checks(driver, tmp_path):
    rows = [("verifying", "350|1000||-1", ""), ("verifying", "800|1000||-1", ""),
            ("done", "1000|1000|sampled|-1", ""), ("verifying", "0|1000||-1", "")]
    _, said = show(driver, tmp_path, cloner(rows), "sample")
    assert "בודק את הדיסקים — 1 מתוך 4 נבדקו" in said, said
    assert "35%" in said and "80%" in said and "0.0%" in said, said
    assert "3 מתוך 4 בבדיקה" in said, said
    assert "בודק" in said, said


@pytestmark_gui
def test_only_a_full_read_is_said_to_be_verified(driver, tmp_path):
    rows = [("verifying", "350|1000||-1", ""), ("done", "1000|1000|verified|-1", ""),
            ("done", "1000|1000|verified|-1", ""), ("done", "1000|1000|verified|-1", "")]
    _, full = show(driver, tmp_path, cloner(rows, "rb_mode=full\n"), "full")
    assert "בודק את הדיסקים — 3 מתוך 4 אומתו" in full, full
    assert "אומת" in full and "אומת — זהה לאימג׳" in full, full
    rows = [("verifying", "350|1000||-1", ""), ("done", "1000|1000|sampled|-1", ""),
            ("done", "1000|1000|sampled|-1", ""), ("done", "1000|1000|sampled|-1", "")]
    _, sampled = show(driver, tmp_path, cloner(rows), "sampled")
    assert "בודק את הדיסקים — 3 מתוך 4 נבדקו" in sampled, sampled
    assert not any("אומת" in s for s in sampled), sampled          # a sample is never "verified"


@pytestmark_gui
def test_a_check_that_stopped_has_a_tick_and_says_where_and_why(driver, tmp_path):
    reason = "האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב"
    with_bar, said = show(driver, tmp_path, cloner([("failed", "620|1000||-1", reason)]), "failed")
    assert f"הבדיקה נכשלה ב-62%: {reason}" in said, said
    plain, said2 = show(driver, tmp_path, cloner([("failed", "", reason)]), "plain")
    assert f"נכשל: {reason}" in said2 and not any("הבדיקה נכשלה" in s for s in said2), said2
    early, _ = show(driver, tmp_path, cloner([("failed", "310|1000||-1", reason)]), "early")
    grew = with_bar["green"] - early["green"]
    assert grew > 600, (with_bar, early)                           # the green stops where the check stopped: 62% is longer than 31%
    assert abs((early["blue"] - with_bar["blue"]) - grew) < 0.15 * grew, (with_bar, early)   # and the blue is what it did not reach
    assert plain["red"] - with_bar["red"] > 3000, (plain, with_bar)  # no red fill over the bar: only the tick


@pytestmark_gui
def test_a_disk_with_no_count_keeps_its_old_row(driver, tmp_path):
    """No invented progress: verifying without a drawer_rbp is the row it was (a whole blue bar, the old words)."""
    px, said = show(driver, tmp_path, cloner([("verifying", "", "")]), "nobar")
    assert "בודק את מה שנכתב…" in said, said
    assert px["green"] < 400, px                                    # only the LEDs/dots, no bar


@pytestmark_gui
def test_an_old_agent_that_sends_no_record_is_the_old_screen(driver, tmp_path):
    """The check is on (every disk is verifying) but nobody counted it: the screen is the one it was -- the old
    words, no check title, no green -- never a made-up 0% or 100%."""
    px, said = show(driver, tmp_path, cloner([("verifying", "", ""), ("verifying", "", "")]), "old")
    assert "בודק את מה שנכתב…" in said, said
    assert not [s for s in said if "בודק את הדיסקים" in s or "בבדיקה" in s or " נבדקו" in s], said
    assert px["green"] < 400, px


def local_state(rows: list[tuple[str, str, str]], phase: str = "verifying", extra: str = "") -> str:
    """rows = (state, lc_rbp fields 'done|total|verdict|rate' or '', error)."""
    from test_native_local_clone import DISKS, FITS
    out = DISKS.replace("lc_phase=idle", f"lc_phase={phase}") + FITS + extra
    out += "lc_run=sdc|148600000000|148600000000|1065|0||5|\n"
    for dev, (st, rb, err) in zip(("sda", "sdb"), rows):
        out += f"lc_tgt={dev}|{st}|148600000000|4|4||{err}|148600000000|0|\n"
        if rb:
            out += f"lc_rbp={dev}|{rb}\n"
    return out


@pytestmark_gui
def test_the_local_clone_shows_the_same_check(driver, tmp_path):
    state = local_state([("verifying", "300|1000||-1", ""), ("verifying", "900|1000||-1", "")])
    px0, said = show(driver, tmp_path, local_state([("verifying", "0|1000||-1", ""), ("verifying", "0|1000||-1", "")]), "l0")
    px, said = show(driver, tmp_path, state, "l1")
    assert "בודק את הדיסקים — 0 מתוך 2 נבדקו" in said, said
    assert "30%" in said and "90%" in said and "60%" in said, said           # the rows and the mean (the big number)
    assert "2 מתוך 2 בבדיקה" in said, said
    assert px["green"] - px0["green"] > 800, (px, px0)
    assert abs((px["blue"] + px["green"]) - (px0["blue"] + px0["green"])) < 0.04 * (px0["blue"] + px0["green"]), (px, px0)


@pytestmark_gui
def test_the_local_clone_says_verified_only_for_a_full_read_and_shows_the_stop(driver, tmp_path):
    reason = "מחיצה 2: האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב"
    state = local_state([("done", "1000|1000|verified|-1", ""), ("failed", "180|1000||-1", reason)], extra="rb_mode=full\n")
    _, said = show(driver, tmp_path, state, "lmix")
    assert "אומת — זהה למקור" in said, said
    assert f"הבדיקה נכשלה ב-18%: {reason}" in said, said
