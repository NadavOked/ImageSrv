"""#1561: the GUI shows "שחזור לדיסק הזה" from the handoff to the end.

Nadav saw it on metal on 17/10, on the build machine. The bridge released the
display for the write, and the whole restore was English on a black tty:
``imagesrv:`` lines, curl errors, and Hebrew drawn as squares. Owner, 17/10:
"no screen that is not graphic in our design". The checks:

* **During the write**, ``single_restore_run`` shows ``--screen progress``
  (``#st-progress``) -- the GUI the handoff brought up, or one of its own --
  and writes its records from the target's own files: the image name, the
  disk by the mapping, "מחיצה N מתוך M", pct, bytes, rate and time left. One
  GUI process, and its state is renamed into place.
* **At the end**, a success card ("השחזור הושלם"), then the count to the
  power-off (``sr_end_poweroff``): "המחשב יכבה בעוד N שניות", 20 s, or at
  once on "כבה עכשיו" (``end-poweroff`` on the GUI's stdout) -- never a
  reboot (Nadav 17/10). A power-off that did not take is said on the card.
  A failure is #1545's message on the SAME GUI (``message=`` routes over
  ``--screen progress``).
* **Before the write** (manifest unreadable, no disk), the refusal is in
  Hebrew on the GUI, then the reboot -- never an English FATAL line alone.
  (The refusals in guiparent.sh: test_gui_restore_authorization.py.)
* **Native** (lab only, needs gcc): the records the agent writes route to
  ``SCREEN_PROGRESS`` and, with ``message=``, to ``SCREEN_MESSAGE`` under
  ``--screen progress``, and the cards are drawn with them (PNG).

log() and the tty while the GUI lives are #1569's (fix/gui-always-no-black):
here the restore's GUI only says which process owns the display (gui.pid).

Negative control (the lab, from the repo root, on the branch):
  git checkout origin/main -- agent/
  python3 -m pytest tests/test_single_restore_progress_gui.py \\
      tests/test_gui_restore_authorization.py tests/test_unicast_pull.py -q   # the #1561 tests fail
  git checkout HEAD -- agent/
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_unicast_pull import AWAIT, IMAGE, MAC, RUN_TIMEOUT_S

REPO = Path(__file__).resolve().parent.parent
RLM = "‏"
TOTAL = 40_000_000_000

pytestmark = requires_native(("bash", BASH), "jq")

MANIFEST = {"id": IMAGE, "name": "Windows 11 Lab", "total_compressed_bytes": TOTAL,
            "partitions": [{"index": 1}, {"index": 2}, {"index": 3}]}

#: The GUI: it records how it was started and every state it read (when the
#: file changed), and leaves on a last card -- the way a real one would be
#: cut by the power-off or stay on a message. GUI_MODE=dies: exits at once.
#: GUI_PRESS=1: "כבה עכשיו" -- end-poweroff on stdout once the count shows.
#: GUI_CANCEL=1: "ביטול השחזור" confirmed -- restore-cancel once the card
#: offers it (cancel=1) mid-stream. GUI_MSG_EXITS=0 / GUI_OFF_EXITS=0: a
#: message / "מכבה" card does not end it (the real GUI stays until the power goes).
GUI_STUB = r'''#!/bin/sh
printf '%s\n' "$*" >> "$RUN_DIR/gui.calls"
st=; for a in "$@"; do [ "$prev" = --state ] && st=$a; prev=$a; done
[ "${GUI_MODE:-live}" = dies ] && exit 0
n=0; pressed=; cancelled=
while [ "$n" -lt 600 ]; do
    if [ -f "$st" ] && cat "$st" > "$RUN_DIR/gui.now" && ! cmp -s "$RUN_DIR/gui.now" "$RUN_DIR/gui.last"; then
        { cat "$RUN_DIR/gui.now"; echo "--"; } >> "$RUN_DIR/gui.seen"; cp "$RUN_DIR/gui.now" "$RUN_DIR/gui.last"
        [ "${GUI_MSG_EXITS:-1}" = 1 ] && LC_ALL=C grep -q '^message=' "$RUN_DIR/gui.now" && exit 0
        [ "${GUI_OFF_EXITS:-1}" = 1 ] && LC_ALL=C grep -qF 'מכבה את המחשב' "$RUN_DIR/gui.now" && exit 0
        if [ "${GUI_CANCEL:-0}" = 1 ] && [ -z "$cancelled" ] && LC_ALL=C grep -qx 'cancel=1' "$RUN_DIR/gui.now" \
                && LC_ALL=C grep -qF 'מחיצה 2' "$RUN_DIR/gui.now"; then
            printf 'restore-cancel\n\n'; cancelled=1
        fi
        if [ "${GUI_PRESS:-0}" = 1 ] && [ -z "$pressed" ] && LC_ALL=C grep -qF 'המחשב יכבה בעוד' "$RUN_DIR/gui.now"; then
            printf 'end-poweroff\n\n'; pressed=1
        fi
        [ "${GUI_UNTIL_DONE:-0}" = 1 ] && LC_ALL=C grep -qF 'השחזור הושלם' "$RUN_DIR/gui.now" && exit 0
    fi
    sleep 0.05; n=$((n + 1))
done
'''

def progress_stub(watch: bool = True) -> str:
    """run_restore as the real one leaves the files: the target "writing", the
    partition the loop is on (restore.sh), the stream counter (bytes.raw).
    With ``watch`` each step waits until the GUI has read it -- positive
    evidence, with a ceiling (AWAIT), not a sleep that hopes."""
    seen = (lambda text: f'await_line "$RUN_DIR/gui.seen" "{text}" || return 1; ') if watch else (lambda text: "")
    return (
        'run_restore() { echo writing > "$RUN_DIR/state"; target_set "$2" writing; '
        + seen("מכין את הדיסק") +
        'echo 2 > "$RUN_DIR/targets/$2/partition"; echo 1000000000 >> "$RUN_DIR/targets/$2/bytes.raw"; '
        + seen("מחיצה 2 מתוך 3") +
        'sleep 1.2; echo 1200000000 >> "$RUN_DIR/targets/$2/bytes.raw"; '   # a rate needs two seconds
        + seen("MB/s") +
        'echo done > "$RUN_DIR/state"; target_set "$2" done; return 0; }; '
    )


PROGRESS = progress_stub()
REASON = "sgdisk דחה את תוכנית השחזור — הדיסק לא שונה"
FAILS = (
    'run_restore() { echo writing > "$RUN_DIR/state"; target_set "$2" writing; '
    'echo 1 > "$RUN_DIR/targets/$2/partition"; '
    'await_line "$RUN_DIR/gui.seen" "מחיצה 1 מתוך 3" || return 1; '
    f'echo failed > "$RUN_DIR/state"; target_set "$2" failed {shlex.quote(REASON)}; return 1; }}; '
)
#: #1561: run_restore as the real one meets "ביטול השחזור": the identity is
#: recorded before the write (write_check / apply_gpt), partition 2 streams
#: through the real http_get_stream -- the fake curl above -- and when the
#: stream ends the write fails by itself, and fail_written_target would take
#: ATA evidence while write_started is there (ATA-CAPTURE: the drive blamed).
CANCELLED = (
    'run_restore() { echo writing > "$RUN_DIR/state"; target_set "$2" writing; '
    'echo SERIAL > "$RUN_DIR/targets/$2/write_started"; '
    'echo 2 > "$RUN_DIR/targets/$2/partition"; echo 1000000000 >> "$RUN_DIR/targets/$2/bytes.raw"; '
    'http_get_stream "$3/api/v1/images/$4/files/p2.zst" > /dev/null; echo "STREAM rc=$?" >> "$RUN_DIR/trace"; '
    '[ ! -f "$RUN_DIR/targets/$2/write_started" ] || echo ATA-CAPTURE >> "$RUN_DIR/trace"; '
    'echo failed > "$RUN_DIR/state"; target_set "$2" failed "נכתבו 1 מתוך 3 מחיצות"; return 1; }; '
)
#: what the GUI restore ends with in guiparent.sh, after single_restore_run
END = 'sr_end_poweroff; echo "END=$?"; '
#: guiparent.sh's own words: 1 (failed) is held, 0 and 3 (cancelled) count to the power-off
AFTER = '[ "$_rc" = 1 ] || { sr_end_poweroff; echo "END=$?"; }; '


def restore(tmp_path: Path, run_restore: str, *, gui: str | None = "live", manifest: bool = True,
            then: str = "", env: str = "") -> dict:
    """The real single_restore_run with the system's edges stubbed: the
    network (http_get, pull_post, http_post_json), the disk
    (pick_internal_disk, disk_port), the write (run_restore) and the power
    (arm_wol; IMAGESRV_TEST makes safe_poweroff say TEST-POWEROFF)."""
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True, exist_ok=True)
    (run / "targets").mkdir(exist_ok=True)
    (run / "manifest.src.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (run / "response.json").write_text(json.dumps({"name": "BUILD-01", "server_name": "srv1"}), encoding="utf-8")
    fake = tmp_path / "bin"   # #1561: a curl that streams for ever, until it is stopped
    fake.mkdir(exist_ok=True)
    (fake / "curl").write_text('#!/bin/sh\nn=0; while [ "$n" -lt 300 ]; do sleep 0.1; n=$((n + 1)); done\n',
                               encoding="utf-8", newline="\n")
    (fake / "curl").chmod(0o755)
    gui_bin = tmp_path / "station-gui"
    if gui:
        gui_bin.write_text(GUI_STUB, encoding="utf-8", newline="\n")
        gui_bin.chmod(0o755)
    get = 'cat "$RUN_DIR/manifest.src.json"' if manifest else "return 22"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} SERVER=http://127.0.0.1:1 "
        f"SR_GUI_BIN={shlex.quote(posix(gui_bin))} GUI_MODE={gui or 'none'} SR_GUI_TICK_S=0.2 "
        "AFTER_TASK_FILE=/nonexistent/after-task D_ROLE=build "
        f"IMAGESRV_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=1 {env}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/pull.sh; "
        f". {posix(AGENT)}/lib/gui/ui.sh; . {posix(AGENT)}/lib/gui/guibridge.sh; "
        f". {posix(AGENT)}/lib/gui/clonergui.sh; . {posix(AGENT)}/lib/restore/writemark.sh; . {posix(AGENT)}/lib/hw/slotnames.sh; "
        f". {posix(AGENT)}/lib/restore/single_restore.sh; . {posix(AGENT)}/lib/gui/srgui.sh; "
        f". {posix(AGENT)}/lib/gui/guistate.sh; "   # gui_header_state: the header's machine and server
        "disk_port() { echo 1; }; pick_internal_disk() { echo sda; }; stage_drivers() { :; }; "
        "sr_share_gate() { return 0; }; "   # #1898: the split question -- its own tests (test_single_restore_share_1898.py)
        f"http_get() {{ {get}; }}; "
        "pull_post() { printf '{\"id\":\"ses_1\"}' > \"$3\"; echo 200; }; "
        "http_post_json() { :; }; progress_loop() { :; }; "
        'arm_wol() { return 0; }; watchdog_beat() { echo beat >> "$RUN_DIR/beats"; }; '
        'sgdisk() { echo "SGDISK $*" >> "$RUN_DIR/trace"; return "${SGDISK_RC:-0}"; }; '
        'blockdev() { echo "BLOCKDEV $*" >> "$RUN_DIR/trace"; }; '
        + AWAIT + run_restore
        + f'single_restore_run {IMAGE}; _rc=$?; echo "RC=$_rc"; ' + then + 'wait'
    )
    out_file = tmp_path / "out.txt"
    with out_file.open("w", encoding="utf-8") as sink:
        proc = subprocess.run([BASH, "-c", f'export PATH="{posix(fake)}:/usr/bin:$PATH"; ' + script],
                              stdout=sink, stderr=subprocess.STDOUT, cwd=str(REPO),
                              stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S)
    out = out_file.read_text(encoding="utf-8")
    assert "AWAIT-TIMEOUT" not in out, out
    read = lambda name: (run / name).read_text(encoding="utf-8") if (run / name).exists() else ""  # noqa: E731
    return {"out": out, "rc": proc.returncode, "log": read("agent.log"), "seen": read("gui.seen"),
            "calls": read("gui.calls").splitlines(), "beats": read("beats").count("beat"), "run": run,
            "trace": read("trace"), "error": read("targets/sda/error")}


def cards(seen: str) -> list[str]:
    return [c for c in seen.split("--\n") if c.strip()]


#: the header's records (gui_header_state) ride on every card; checked once, in the first test
HEADER = ("machine_name=", "server_name=", "server_version=", "tz=", "server_addr=", "agent_version=")


def body(card: str) -> str:
    return "".join(line for line in card.splitlines(True) if not line.startswith(HEADER))


def test_the_restore_shows_its_progress_on_one_gui(tmp_path):
    """The heart of #1561. Negative control: on main no GUI starts -- gui.calls is empty."""
    r = restore(tmp_path, PROGRESS, env="GUI_UNTIL_DONE=1")
    assert "RC=0" in r["out"], r["out"]
    assert len(r["calls"]) == 1, r["calls"]
    call = r["calls"][0]
    assert "--screen progress" in call and f"--state {posix(r['run'])}/gui/restore.state" in call, call
    assert "--backend " in call
    seen = r["seen"]
    assert f"title={RLM}שחזור: Windows 11 Lab\n" in seen, seen
    assert f"sub={RLM}דיסק 1 · מחיצה 2 מתוך 3 · " in seen
    assert "pct=2\n" in seen and "bytes=1000000000\n" in seen and "moving=1\n" in seen
    # "partition=" would make the card say "(בלוקים)", the capture's block axis
    assert "partition=" not in seen
    # measured only after two samples -- before that it says so, never 0
    assert "מודד את הקצב…" in seen
    assert re.search(r"מחיצה 2 מתוך 3 · \d+\.\d MB/s · (כ-\d+ דק׳ נותרו|פחות מ)", seen), seen
    # the GUI that owns the display now, for log() (#1569 reads gui.pid)
    assert (r["run"] / "gui.pid").read_text().strip().isdigit()
    # the header says which machine and server -- not "לא ידוע" for twenty minutes
    assert all("machine_name=BUILD-01\n" in c and "server_name=srv1\n" in c for c in cards(seen)), seen


def test_before_the_first_partition_it_says_the_disk_is_being_prepared(tmp_path):
    r = restore(tmp_path, PROGRESS, env="GUI_UNTIL_DONE=1")
    first = next(c for c in cards(r["seen"]) if "מכין את הדיסק" in c)
    assert "pct=0\n" in first and "moving=0\n" in first, first


def test_the_restore_ends_on_a_success_card_then_powers_off_in_20_seconds(tmp_path):
    """Nadav 17/10: "השחזור הושלם", then "המחשב יכבה בעוד 20 שניות" -- and
    off, not a reboot. The count is the agent's (one beat a second); the card
    counts down with it. Negative control: on main there is no sr_end_poweroff."""
    r = restore(tmp_path, PROGRESS, then=END)
    assert "RC=0" in r["out"] and "END=0" in r["out"], r["out"]
    assert "TEST-POWEROFF" in r["out"] and "TEST-REBOOT" not in r["out"]
    assert r["beats"] == 20, r["beats"]                      # SR_POWEROFF_S: 20 by default
    done = [c for c in cards(r["seen"]) if "השחזור הושלם" in c]
    assert done, r["seen"]
    assert f"title={RLM}השחזור הושלם: Windows 11 Lab\n" in done[0] and "pct=100\n" in done[0], done[0]
    left = [int(n) for n in re.findall(r"המחשב יכבה בעוד (\d+) שניות", r["seen"])]
    assert left and left[0] in (19, 20) and left == sorted(left, reverse=True) and min(left) < 19, left
    assert f"sub={RLM}דיסק 1 · מכבה את המחשב…\n" in done[-1], done[-1]
    # #1560's dialog draws the count and "כבה עכשיו" from this record; its button says end-poweroff
    counting = [c for c in done if "המחשב יכבה בעוד" in c]
    assert counting and all(re.search(r"^end_poweroff=\d+\|restore$", c, re.M) for c in counting), counting
    assert "end_poweroff=" not in done[-1]
    assert len(r["calls"]) == 1, r["calls"]
    assert "restore: ended -- powering off in 20s" in r["log"]


def test_the_power_off_button_powers_off_at_once(tmp_path):
    """"כבה עכשיו": the GUI says end-poweroff on its stdout (#1560's dialog
    draws the button over this card); the count stops there."""
    r = restore(tmp_path, PROGRESS, then=END, env="GUI_PRESS=1")
    assert "END=0" in r["out"] and "TEST-POWEROFF" in r["out"], r["out"]
    assert r["beats"] < 5, r["beats"]
    assert "restore: 'כבה עכשיו' pressed" in r["log"]


def test_a_power_off_that_did_not_take_is_said_on_the_card(tmp_path):
    """safe_poweroff refused (no Wake-on-LAN): the machine stays on, and the
    screen says so -- not a frozen countdown (principle 5)."""
    r = restore(tmp_path, PROGRESS, then='arm_wol() { return 1; }; SR_POWEROFF_S=1; ' + END, env="GUI_OFF_EXITS=0")
    assert "END=1" in r["out"] and "TEST-POWEROFF" not in r["out"], r["out"]
    last = body(cards(r["seen"])[-1])
    assert last.startswith(f"message=השחזור הושלם — המחשב לא כובה|{RLM}Wake-on-LAN לא נדרך"), last
    assert len(last.split("|")[1].rstrip("\n").encode()) < 200   # msg_sub[200] in ui.h
    assert "the machine stayed on (wol)" in r["log"]


def test_a_failed_write_shows_its_reason_on_the_same_gui(tmp_path):
    """#1545's message, now on the progress GUI -- no second GUI takes the display."""
    r = restore(tmp_path, FAILS)
    last = body(cards(r["seen"])[-1])
    assert last == f"message=השחזור נכשל — המחשב נשאר דלוק ומדווח לשרת|{RLM}{REASON}\n", last
    assert len(r["calls"]) == 1, r["calls"]
    assert "TEST-HOLD" in r["out"] and "RC=1" in r["out"]


def test_without_a_gui_the_restore_goes_on(tmp_path):
    r = restore(tmp_path, progress_stub(watch=False), gui=None, then=END)
    assert "RC=0" in r["out"] and "TEST-POWEROFF" in r["out"], r["out"]
    assert "no GUI for the progress" in r["log"]
    assert "imagesrv: unicast pull registered as ses_1" in r["out"], r["out"]


def test_a_refusal_before_the_write_is_in_hebrew_on_the_gui(tmp_path):
    """Negative control: on main it is `die_local` alone -- no GUI."""
    r = restore(tmp_path, PROGRESS, manifest=False)
    last = body(cards(r["seen"])[-1]) if r["seen"] else ""
    assert last == (f"message=השחזור לא התחיל — לא נכתב דבר|{RLM}"
                    "לא ניתן לקרוא מהשרת את פרטי האימג׳ · המחשב יופעל מחדש\n"), (last, r["out"])
    assert "TEST-REBOOT: restore: manifest fetch failed" in r["out"]
    assert r["rc"] == 86


# --- native: the records route and draw (lab only) ----------------------------

GUI = REPO / "native-gui"


def _pkgconfig(*pkgs: str) -> bool:
    return bool(shutil.which("pkg-config")) and subprocess.run(
        ["pkg-config", "--exists", *pkgs], stdin=subprocess.DEVNULL).returncode == 0


native = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH), "jq",
    why="native-gui נבנה על המעבדה בלבד",
)

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_progress = 1;   /* --screen progress */
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    state_parse(&a.st, f); fclose(f); app_route(&a);
    if (argc > 3 && !strcmp(argv[3], "confirm")) a.confirm = CONFIRM_RESTORE_CANCEL;   /* #1561: the click asked */
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_write_to_png(s, argv[2]); cairo_surface_destroy(s);
    int cancel_hit = 0, confirm_ok = 0;
    for (int i = 0; i < a.nhits; i++) { cancel_hit |= a.hits[i].id == HIT_RESTORE_CANCEL; confirm_ok |= a.hits[i].id == HIT_CONFIRM_OK; }
    printf("screen=%d progress=%d message=%d title=%s\n", a.screen, SCREEN_PROGRESS, SCREEN_MESSAGE, a.st.title);
    printf("cancel_hit=%d confirm_ok=%d confirm=%d\n", cancel_hit, confirm_ok, a.confirm == CONFIRM_RESTORE_CANCEL);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("srdrv")
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


#: what the agent's files hold at each stage (sr_gui_start / sr_gui_done / sr_end_poweroff)
STAGES = {
    "preparing": "",
    "writing": 'printf "sda\nשחזור: Windows 11 Lab\nדיסק 1\n3\nWindows 11 Lab\n" > "$RUN_DIR/gui/restore.head"; ',
}
STAGES["stopping"] = STAGES["writing"] + ': > "$RUN_DIR/gui/restore.cancel"; '
STAGES["done"] = STAGES["writing"] + ': > "$RUN_DIR/gui/restore.done"; echo $(( $(date +%s) + 20 )) > "$RUN_DIR/gui/restore.end"; '


def agent_state(tmp_path: Path, stage: str = "writing") -> Path:
    """The records the agent's own writer (sr_gui_state) produces at a stage."""
    run = tmp_path / f"run-{stage}"
    (run / "gui").mkdir(parents=True, exist_ok=True)
    out = tmp_path / f"{stage}.state"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/progress.sh; "
        f". {posix(AGENT)}/lib/gui/clonergui.sh; . {posix(AGENT)}/lib/restore/writemark.sh; . {posix(AGENT)}/lib/gui/srgui.sh; "
        f'target_init sda {TOTAL}; target_set sda writing; echo writing > "$RUN_DIR/state"; '
        'echo 2 > "$RUN_DIR/targets/sda/partition"; echo 18000000000 >> "$RUN_DIR/targets/sda/bytes.raw"; '
        + STAGES[stage] +
        f'sr_gui_state > {shlex.quote(posix(out))}'
    )
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script], capture_output=True,
                          text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return out


def drive(driver: Path, state: Path, png: Path, *extra: str) -> str:
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(driver), posix(state), posix(png), *extra], capture_output=True, text=True,
                         encoding="utf-8", env=env, timeout=120, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run.stdout


@native
def test_the_agents_records_route_to_the_progress_card_and_draw(driver, tmp_path):
    state = agent_state(tmp_path)
    out = drive(driver, state, tmp_path / "restore.png")
    screen, progress = (int(v) for v in re.findall(r"screen=(\d+) progress=(\d+)", out)[0])
    assert screen == progress, out
    assert "שחזור: Windows 11 Lab" in out
    # the texts are drawn: the same card without them is a different picture
    bare = tmp_path / "bare.state"
    bare.write_text("".join(l for l in state.read_text(encoding="utf-8").splitlines(True)
                            if not l.startswith(("title=", "sub="))), encoding="utf-8", newline="\n")
    drive(driver, bare, tmp_path / "bare.png")
    assert (tmp_path / "restore.png").read_bytes() != (tmp_path / "bare.png").read_bytes()


@native
@pytest.mark.parametrize("stage", ["preparing", "done"])
def test_the_first_and_last_cards_route_to_the_progress_card(driver, tmp_path, stage):
    """The card before the checks, and "השחזור הושלם" with the count."""
    state = agent_state(tmp_path, stage)
    text = state.read_text(encoding="utf-8")
    assert ("בודק את האימג׳" if stage == "preparing" else "המחשב יכבה בעוד") in text, text
    out = drive(driver, state, tmp_path / f"{stage}.png")
    screen, progress = (int(v) for v in re.findall(r"screen=(\d+) progress=(\d+)", out)[0])
    assert screen == progress, out


@native
def test_the_failure_message_routes_over_the_progress_screen(driver, tmp_path):
    msg = tmp_path / "failed.state"
    msg.write_text(f"message=השחזור נכשל — המחשב נשאר דלוק ומדווח לשרת|{REASON}\n", encoding="utf-8", newline="\n")
    out = drive(driver, msg, tmp_path / "failed.png")
    screen, message = (int(v) for v in re.findall(r"screen=(\d+) progress=\d+ message=(\d+)", out)[0])
    assert screen == message, out


@native
def test_the_real_binary_renders_the_restore_progress_png(tmp_path):
    from test_cloner_gui import _build_gui
    binary = _build_gui(tmp_path)
    state = agent_state(tmp_path)
    # --png renders every card; "message" routes only with a message= record (png_setup clears it elsewhere)
    with state.open("a", encoding="utf-8", newline="\n") as f:
        f.write("message=x|y\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "p"), "--size", "1280x800", "--mac", MAC,
                          "--state", posix(state)], capture_output=True, text=True, timeout=300, env=env,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr   # render_png checks each card's route (want[])
    for theme in ("light", "dark"):
        assert (tmp_path / f"p-progress-{theme}.png").exists(), theme


# --- #1561: "ביטול השחזור" (Nadav 17/10) ---------------------------------------


def test_cancel_stops_the_stream_wipes_the_table_and_powers_off(tmp_path):
    """The button (after its red confirmation) says restore-cancel; the stream
    stops, the primary and backup GPT go -- an empty disk, not a half one --
    "השחזור בוטל — הדיסק ריק", and the same 20 s to the power-off. No ATA
    evidence: a cancel is not a disk fault. Negative control: on main the
    stream runs to its end (no cancel), nothing is wiped, no power-off."""
    r = restore(tmp_path, CANCELLED, then=AFTER, env="GUI_CANCEL=1 GUI_MSG_EXITS=0")
    assert "RC=3" in r["out"] and "END=0" in r["out"] and "TEST-POWEROFF" in r["out"], r["out"]
    assert "STREAM rc=" in r["trace"] and "STREAM rc=0" not in r["trace"], r["trace"]   # stopped, not ended
    assert "restore: stopping the stream (curl " in r["log"], r["log"]
    # the conftest's DEVROOT points nowhere real: never the host's disks
    assert re.search(r"^SGDISK --zap-all \S*/sda$", r["trace"], re.M), r["trace"]
    assert re.search(r"^BLOCKDEV --rereadpt \S*/sda$", r["trace"], re.M), r["trace"]
    assert "ATA-CAPTURE" not in r["trace"], r["trace"]
    assert "בוטל" in r["error"] and "הדיסק ריק" in r["error"], r["error"]
    seen = [body(c) for c in cards(r["seen"])]
    offered = [c for c in seen if "cancel=1\n" in c]
    assert offered and all("מחיצה 2" in c or "מכין את הדיסק" in c for c in offered), offered
    # once asked, never offered again (the "מבטל…" card itself may be too short to be seen here)
    asked = next(i for i, c in enumerate(seen) if "מבטל: עוצר את הכתיבה" in c or c.startswith("message="))
    assert all("cancel=1" not in c for c in seen[asked:]), seen
    gone = [c for c in seen if c.startswith("message=השחזור בוטל — הדיסק ריק|")]
    assert gone and any(re.search(r"המחשב יכבה בעוד \d+ שניות", c) for c in gone), seen
    assert gone[-1] == f"message=השחזור בוטל — הדיסק ריק|{RLM}דיסק 1 · מכבה את המחשב…\n", gone[-1]
    assert all("end_poweroff=" not in c for c in gone)   # #1560's dialog would say "הושלם"
    assert r["beats"] == 20, r["beats"]
    assert len(r["calls"]) == 1, r["calls"]


def test_a_cancel_whose_wipe_failed_is_held_and_said(tmp_path):
    """sgdisk did not wipe (twice): the disk is half written and may look whole --
    said on the screen, the machine held for a person, never a power-off."""
    r = restore(tmp_path, CANCELLED, then=AFTER, env="GUI_CANCEL=1 SGDISK_RC=2")
    assert "RC=1" in r["out"] and "TEST-HOLD" in r["out"] and "TEST-POWEROFF" not in r["out"], r["out"]
    assert len(re.findall(r"^SGDISK --zap-all \S*/sda$", r["trace"], re.M)) == 2, r["trace"]
    last = body(cards(r["seen"])[-1])
    assert last.startswith(f"message=השחזור בוטל — מחיקת הטבלה נכשלה|{RLM}הדיסק כתוב בחלקו"), last
    assert "נכשלה" in r["error"], r["error"]


def test_no_cancel_is_offered_once_the_write_ended(tmp_path):
    r = restore(tmp_path, PROGRESS, then=END)
    seen = [body(c) for c in cards(r["seen"])]
    assert any("cancel=1\n" in c for c in seen if "מחיצה 2" in c), seen
    assert all("cancel=1" not in c for c in seen if "השחזור הושלם" in c or "בודק את האימג" in c), seen


@native
def test_the_cancel_button_is_drawn_only_when_offered_and_asks_first(driver, tmp_path):
    """#1561: cancel=1 draws "ביטול השחזור" (its hit); the click opens the red
    confirmation (#1270) -- its OK is what says restore-cancel (main.c
    confirm_do). Without cancel=1 there is no button."""
    state = agent_state(tmp_path)
    text = state.read_text(encoding="utf-8")
    assert "cancel=1\n" in text, text
    out = drive(driver, state, tmp_path / "cancel.png")
    assert "cancel_hit=1" in out, out
    asked = drive(driver, state, tmp_path / "cancel-confirm.png", "confirm")
    assert "confirm_ok=1 confirm=1" in asked and "cancel_hit=0" in asked, asked   # modal: only its buttons
    bare = tmp_path / "nocancel.state"
    bare.write_text(text.replace("cancel=1\n", ""), encoding="utf-8", newline="\n")
    assert "cancel_hit=0" in drive(driver, bare, tmp_path / "nocancel.png")


def test_a_cancel_being_carried_out_says_so_and_offers_no_second(tmp_path):
    """Between the press and the empty disk: "מבטל…", and no cancel=1."""
    card = agent_state(tmp_path, "stopping").read_text(encoding="utf-8")
    assert f"sub={RLM}דיסק 1 · מבטל: עוצר את הכתיבה ומוחק את טבלת המחיצות…\n" in card, card
    assert "cancel=1" not in card
    assert "cancel=1\n" in agent_state(tmp_path, "writing").read_text(encoding="utf-8")
