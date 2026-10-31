"""#928: the capture's shrink question as a dialog of the native GUI.

Until #928 the kiosk was stopped before the question (it was stopped by a helper since removed)
and the operator answered on the text console. Now the agent publishes the
plan through the same handshake as the cloner's SMART choice:

* `gui_shrink_choice` (`agent/lib/shrink/shrinkgui.sh`) validates the whole plan,
  writes `$GUI_DIR/shrink-request` atomically (``nonce|count`` + one
  ``idx|old|new`` row per partition) and waits for a decision bound to that
  nonce. A dead GUI, a malformed plan, a stale/garbage decision or no answer
  within ``WAIT_SHRINK_GUI_S`` never becomes "continue" (principle 5).
* `shrink_ask` (`shrinkplan.sh`) asks there when a GUI owns the screen and
  turns every failure into ``cancel``; without a GUI the text menu stays.
* `gui_shrink_state` (`guistate.sh`) copies the request into the state file
  -- in the failure snapshot as well -- and `gui_shrink_dispatch` accepts
  ``shrink-<answer>|<nonce>`` on the build screen, without a session.
* The C side parses the records all-or-nothing and draws the modal (--png).

Negative control (bakara-shlilit): with every production file of this change
back at origin/main, the shell tests fail on behaviour (no request is ever
written, the kiosk is killed, the text menu answers "continue" from stdin)
and the --png card does not exist.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from native import requires_native
from test_cloner_gui import (BASH, GUI, _build_gui, _count_color, _pkgconfig,
                             _png_rgb, _token, posix, sh)

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
SRC = GUI / "src"

pytestmark = requires_native(("bash", BASH))

#: Two NTFS partitions, in shrink_plan's format: idx|guid|uguid|start|size|cur|new.
PLAN = ("3|EBD0A0A2|1111|2048|974608384|499000000000|146000000000\n"
        "4|DE94BBA4|2222|974610432|1953125|1000000000|700000000\n")


def setup(tmp_path: Path, extra: str = "") -> tuple[str, Path]:
    run = tmp_path / "run"; run.mkdir()
    gui = run / "gui"; gui.mkdir()
    (run / "shrink.plan").write_text(PLAN, newline="\n")
    pre = (f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} IMAGESRV_TEST=1; '
           f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; '
           f'. {posix(AGENT)}/lib/shrink/shrinkgui.sh; {extra}')
    return pre, gui


#: Wait for the request, then answer with its nonce: what the bridge does.
ANSWER = """
for _ in $(seq 1 50); do [ -s "$GUI_DIR/shrink-request" ] && break; sleep 0.2; done
cp "$GUI_DIR/shrink-request" "$RUN_DIR/request.seen"
N=$(IFS='|' read -r n _ < "$GUI_DIR/shrink-request"; echo "$n")
"""


# --- gui_shrink_choice: the agent's side of the handshake -----------------------


def test_the_request_carries_every_row_and_a_bound_answer_releases_it(tmp_path):
    pre, gui = setup(tmp_path)
    out = sh(pre + f"""
sleep 30 & _gui_pid=$!
( gui_shrink_choice "$RUN_DIR/shrink.plan" > "$RUN_DIR/out"; echo "rc=$?" > "$RUN_DIR/rc" ) &
CH=$!
{ANSWER}
printf '%s|plain\\n' "$N" > "$GUI_DIR/shrink-decision"
wait $CH; kill $_gui_pid
cat "$RUN_DIR/out" "$RUN_DIR/rc"
ls "$GUI_DIR"
""")
    assert out.returncode == 0, out.stderr
    seen_file = tmp_path / "run" / "request.seen"
    assert seen_file.exists(), "no request was published"
    seen = seen_file.read_text().splitlines()
    assert seen == ["1-0|2", "3|499000000000|146000000000", "4|1000000000|700000000"]
    assert out.stdout.split()[:2] == ["plain", "rc=0"], out.stdout
    assert "shrink-request" not in out.stdout and "shrink-decision" not in out.stdout


def test_a_stale_nonce_or_a_bad_answer_does_not_release_the_question(tmp_path):
    pre, gui = setup(tmp_path)
    out = sh(pre + f"""
sleep 30 & _gui_pid=$!
( gui_shrink_choice "$RUN_DIR/shrink.plan" > "$RUN_DIR/out"; echo done > "$RUN_DIR/rc" ) &
CH=$!
{ANSWER}
printf '9-0|continue\\n' > "$GUI_DIR/shrink-decision"; sleep 2
[ -f "$RUN_DIR/rc" ] && echo RELEASED-ON-STALE
printf '%s|yes\\n' "$N" > "$GUI_DIR/shrink-decision"; sleep 2
[ -f "$RUN_DIR/rc" ] && echo RELEASED-ON-GARBAGE
printf '%s|continue\\n' "$N" > "$GUI_DIR/shrink-decision"
wait $CH; kill $_gui_pid
cat "$RUN_DIR/out"
""")
    assert out.returncode == 0, out.stderr
    assert "RELEASED-ON-STALE" not in out.stdout
    assert "RELEASED-ON-GARBAGE" not in out.stdout
    assert out.stdout.strip().endswith("continue"), out.stdout


def test_a_dead_gui_answers_nothing(tmp_path):
    pre, gui = setup(tmp_path)
    out = sh(pre + """
sh -c 'exit 0' & _gui_pid=$!; wait $_gui_pid
gui_shrink_choice "$RUN_DIR/shrink.plan" > "$RUN_DIR/out"; echo "rc=$?"
""")
    assert "rc=1" in out.stdout.splitlines(), out.stdout + out.stderr
    assert (tmp_path / "run" / "out").read_text() == ""
    assert not (gui / "shrink-request").exists()


def test_no_answer_within_the_ceiling_fails_and_clears_the_question(tmp_path):
    pre, gui = setup(tmp_path, "WAIT_SHRINK_GUI_S=2; ")
    started = time.monotonic()
    # The GUI outlives the ceiling but not the test: without a ceiling the
    # wait ends only at the GUI's death -- late, and without the message.
    out = sh(pre + """
sleep 25 & _gui_pid=$!
gui_shrink_choice "$RUN_DIR/shrink.plan" > "$RUN_DIR/out"; echo "rc=$?"
kill $_gui_pid
""", timeout=60)
    elapsed = time.monotonic() - started
    assert "rc=1" in out.stdout.splitlines(), out.stdout + out.stderr
    assert (tmp_path / "run" / "out").read_text() == "", "a timeout must not answer"
    assert "no shrink answer in 2s" in out.stderr
    assert not (gui / "shrink-request").exists(), "the GUI would keep asking"
    assert elapsed < 20, elapsed


def test_an_unset_ceiling_refuses_instead_of_waiting_for_ever(tmp_path):
    pre, gui = setup(tmp_path, "WAIT_SHRINK_GUI_S=; ")
    out = sh(pre + """
sleep 30 & _gui_pid=$!
gui_shrink_choice "$RUN_DIR/shrink.plan"; echo "rc=$?"; kill $_gui_pid
""", timeout=20)
    assert out.stdout.strip() == "rc=1", out.stdout


def test_a_malformed_plan_is_never_published(tmp_path):
    """A row that does not shrink (new >= old), a duplicate partition and a
    plan longer than the GUI draws are refused before anything is shown."""
    for name, plan in (
        ("grow", "3|g|u|2048|100|1000|2000\n"),
        ("dup", "3|g|u|2048|100|1000|500\n3|g|u|4096|100|1000|500\n"),
        ("long", "".join(f"{i}|g|u|{i}|1|1000|500\n" for i in range(1, 18))),
    ):
        sub = tmp_path / name; sub.mkdir()
        pre, gui = setup(sub)
        (sub / "run" / "shrink.plan").write_text(plan, newline="\n")
        out = sh(pre + """
sleep 30 & _gui_pid=$!
gui_shrink_choice "$RUN_DIR/shrink.plan"; echo "rc=$?"; kill $_gui_pid
""", timeout=20)
        assert out.stdout.strip() == "rc=1", (name, out.stdout)
        assert not (gui / "shrink-request").exists(), name


# --- shrink_ask / shrink_before_capture: the GUI is asked, never the hidden TTY --


def ask_setup(tmp_path: Path) -> tuple[str, Path]:
    return setup(tmp_path, f'. {posix(AGENT)}/lib/shrink/shrinkplan.sh; '
                           'ui_clear() { :; }; ui_header() { :; }; ')


def run_with_stdin(script: str, stdin: str) -> subprocess.CompletedProcess:
    """Bytes in, so Windows' text mode cannot turn "1\\n" into "1\\r\\n" --
    the text menu would not take that as "1", and a test of "the hidden
    menu was not read" would pass for the wrong reason."""
    run = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                         input=stdin.encode(), capture_output=True,
                         cwd=str(REPO), timeout=60)
    return subprocess.CompletedProcess(run.args, run.returncode,
                                       run.stdout.decode("utf-8", "replace"),
                                       run.stderr.decode("utf-8", "replace"))


def test_shrink_ask_on_the_gui_returns_the_operators_answer(tmp_path):
    pre, gui = ask_setup(tmp_path)
    out = sh(pre + f"""
sleep 30 & _gui_pid=$!
( {ANSWER} printf '%s|continue\\n' "$N" > "$GUI_DIR/shrink-decision" ) &
shrink_ask "$RUN_DIR/shrink.plan"
kill $_gui_pid
""")
    assert out.stdout.strip() == "continue", out.stdout + out.stderr


def test_a_failed_bridge_cancels_and_never_reads_the_hidden_text_screen(tmp_path):
    """The GUI owned the screen and is gone. "1" waiting on stdin is exactly
    what the text menu would take as "shrink" -- it must not be read."""
    pre, gui = ask_setup(tmp_path)
    out = run_with_stdin(pre + """
sh -c 'exit 0' & _gui_pid=$!; wait $_gui_pid
shrink_ask "$RUN_DIR/shrink.plan"
""", "1\n")
    assert out.stdout.strip() == "cancel", out.stdout + out.stderr
    assert "capture cancelled" in out.stderr


def test_without_a_gui_the_capture_is_cancelled_and_no_key_is_read(tmp_path):
    """11/10 (GUI only): the text menu that asked here was removed. With no
    GUI the question cannot be asked, and nothing shrinks without a yes --
    "cancel", even with "2" ("plain" on the old menu) waiting on stdin.
    Negative control: the old code reads the "2" and answers "plain"."""
    for key in ("1", "2", "3"):
        sub = tmp_path / key; sub.mkdir()
        pre, gui = ask_setup(sub)
        out = run_with_stdin(pre + 'shrink_ask "$RUN_DIR/shrink.plan"', key + "\n")
        assert out.stdout.strip() == "cancel", (key, out.stdout + out.stderr)
        assert "Shrink and capture" not in out.stderr
        assert "capture cancelled" in out.stderr
        assert not (gui / "shrink-request").exists()


def test_the_capture_keeps_the_gui_and_asks_it(tmp_path):
    """`shrink_before_capture` no longer stops the kiosk: the question goes
    to the GUI (a request is published), the kiosk is still running when the
    answer is in, and "cancel" stops the capture with nothing shrunk."""
    pre, gui = setup(tmp_path, (
        f'. {posix(AGENT)}/lib/shrink/shrinkplan.sh; . {posix(AGENT)}/lib/shrink/shrink.sh; '
        'ui_clear() { :; }; ui_header() { :; }; '
        'shrink_plan() { :; }; shrink_records_refresh() { :; }; '
        'shrink_pending_for() { return 1; }; attended() { shift; "$@"; }; '
        'ntfsresize() { echo NTFSRESIZE-RAN; }; '))
    out = sh(pre + f"""
sleep 30 & _gui_pid=$!; G=$_gui_pid
( {ANSWER} printf '%s|cancel\\n' "$N" > "$GUI_DIR/shrink-decision" ) &
shrink_before_capture sda "$RUN_DIR/parts.txt" 512 < /dev/null; echo "rc=$?"
kill -0 $G 2>/dev/null && echo GUI-ALIVE
[ -n "$SHRINK_ERROR" ] && echo CAPTURE-STOPPED-WITH-A-REASON
kill $G 2>/dev/null; true
""")
    assert "rc=1" in out.stdout.splitlines(), out.stdout + out.stderr
    seen = tmp_path / "run" / "request.seen"
    assert seen.exists() and seen.read_text().startswith("1-0|2"), \
        "the question never reached the GUI"
    assert "GUI-ALIVE" in out.stdout, "the kiosk was stopped for the question"
    assert "NTFSRESIZE-RAN" not in out.stdout
    assert "CAPTURE-STOPPED-WITH-A-REASON" in out.stdout


# --- guistate.sh: the request becomes state records ------------------------------


STATE_PRE = (f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
             f'. {posix(AGENT)}/lib/gui/buildmenuitems.sh; . {posix(AGENT)}/lib/gui/guistate.sh; '
             f'. {posix(AGENT)}/lib/gui/askgui.sh; '
             'disk_slot_records() { :; }; ')   # #1495: disk numbering is test_drawer_map_gui_1495's


def test_gui_shrink_state_copies_the_request(tmp_path):
    gui = tmp_path / "gui"; gui.mkdir()
    env = f'GUI_DIR={posix(gui)!r}; '
    assert sh(env + STATE_PRE + "gui_shrink_state").stdout == "", "no request, no record"
    (gui / "shrink-request").write_text("4-0|1\n3|1000|500\n", newline="\n")
    out = sh(env + STATE_PRE + "gui_shrink_state")
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["shrink_prompt=4-0|1", "shrink_partition=3|1000|500"]


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_the_state_file_carries_the_question(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "station.json").write_text(
        '{"known": true, "role": "build", "disks": [], "task": null, "allowed_images": []}',
        newline="\n")
    (gui / "mode").write_text("menu\n", newline="\n")
    (gui / "shrink-request").write_text("4-0|1\n3|1000|500\n", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
             'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
             + STATE_PRE + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; gui_state')
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text().splitlines()
    assert "shrink_prompt=4-0|1" in lines and "shrink_partition=3|1000|500" in lines, lines


def test_the_failure_snapshot_keeps_the_question(tmp_path):
    """The question is local: a state that could not be fetched from the
    server must not take the dialog off the screen."""
    gui = tmp_path / "gui"; gui.mkdir()
    (gui / "shrink-request").write_text("4-0|1\n3|1000|500\n", newline="\n")
    out = sh(f'GUI_DIR={posix(gui)!r}; ' + STATE_PRE +
             '( gui_state() { return 1; }; sleep() { exit 0; }; gui_state_loop )')
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "message=לא ניתן לאמת את מצב התחנה|בדקו את החיבור לשרת. הפרטים ביומן הסוכן", lines
    assert "shrink_prompt=4-0|1" in lines, (lines, out.stderr)


# --- guibridge.sh: the click is bound to the question on screen ------------------


def bridge(tmp_path: Path, screen: str) -> tuple[str, Path]:
    gui = tmp_path / "gui"; gui.mkdir()
    run = tmp_path / "run"; run.mkdir()
    pre = (f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
           f'GUI_SCREEN={screen} MAC=aa:bb:cc:dd:ee:ff SERVER=http://x; '
           f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; '
           f'. {posix(AGENT)}/lib/shrink/shrinkgui.sh; . {posix(AGENT)}/lib/gui/guibridge.sh; '
           'gui_role() { echo GUI_ROLE_CALLED >&2; return 1; }; ')
    return pre, gui


def test_the_bridge_writes_the_answer_for_the_active_nonce(tmp_path):
    pre, gui = bridge(tmp_path, "")
    (gui / "shrink-request").write_text("5-0|1\n3|1000|500\n", newline="\n")
    out = sh(pre + 'token="shrink-continue|5-0"; gui_dispatch; echo "rc=$?"')
    assert "rc=0" in out.stdout, out.stderr
    assert (gui / "shrink-decision").read_text() == "5-0|continue\n"
    assert "GUI_ROLE_CALLED" not in out.stderr, "a pending question needs no session"


def test_the_bridge_rejects_what_is_not_the_question_on_screen(tmp_path):
    for name, screen, request, token in (
        ("stale", "", "6-0|1\n3|1000|500\n", "shrink-continue|5-0"),
        ("none", "", None, "shrink-continue|5-0"),
        ("no-nonce", "", "5-0|1\n3|1000|500\n", "shrink-continue"),
        ("bad-answer", "", "5-0|1\n3|1000|500\n", "shrink-yes|5-0"),
        ("cloner", "cloner", "5-0|1\n3|1000|500\n", "shrink-continue|5-0"),
    ):
        sub = tmp_path / name; sub.mkdir()
        pre, gui = bridge(sub, screen)
        if request:
            (gui / "shrink-request").write_text(request, newline="\n")
        out = sh(pre + f'token="{token}"; gui_dispatch; echo "rc=$?"')
        assert "rc=0" not in out.stdout, name
        assert not (gui / "shrink-decision").exists(), name


# --- the C side --------------------------------------------------------------------


def _body(text: str, name: str) -> str:
    m = re.search(r"\b" + re.escape(name) + r"\([^)]*\)\s*\{(.*?)\n\}", text, re.S)
    assert m, name
    return m.group(1)


def test_the_dialog_keys_and_buttons_map_to_the_three_answers():
    """No compiler on the Windows station: the event mapping is read from the
    source. Enter is not an answer -- "shrink" is never the default."""
    modal = _body((SRC / "main.c").read_text(encoding="utf-8"), "shrink_modal")
    assert re.findall(r"e->ch == '(\d)'\) emit_shrink\(a, \"(shrink-\w+)\"\)", modal) == [
        ("1", "shrink-continue"), ("2", "shrink-plain"), ("3", "shrink-cancel")]
    assert re.search(r"KEYSYM_ESC\) emit_shrink\(a, \"shrink-cancel\"\)", modal)
    assert "KEYSYM_ENTER" not in modal
    assert re.findall(r"case (HIT_SHRINK_\w+):\s+emit_shrink\(a, \"(shrink-\w+)\"\)", modal) == [
        ("HIT_SHRINK_CONTINUE", "shrink-continue"), ("HIT_SHRINK_PLAIN", "shrink-plain"),
        ("HIT_SHRINK_CANCEL", "shrink-cancel")]


#: Nadav, 10/10: when "capture without shrinking" is the right answer.
PLAIN_HINT = ("רק אם הכיווץ נכשל, או שכל דיסקי היעד גדולים מהמקור — "
              "האימג' לא ייכנס לדיסקים קטנים יותר")


def test_shrink_is_the_one_primary_button_and_plain_is_a_hinted_secondary():
    """Nadav, 10/10: "shrink and capture" is the primary button and comes
    first; "capture without shrinking" is a secondary button -- the same box
    as "cancel", never filled -- with a grey hint right under it (the owner,
    10/10: the underlined link looked out of place); "cancel" is the same
    secondary button, last. Read from the source: there is no compiler on the
    Windows station (the --png test below checks the pixels on the lab)."""
    src = (SRC / "screens_shrink.c").read_text(encoding="utf-8")
    label = dict(re.findall(r'static const char (SHRINK_\w+)\[\] =\s*"([^"]+)";', src))
    assert label.get("SHRINK_CONTINUE_LABEL") == "כווץ וקלוט", label
    assert label.get("SHRINK_PLAIN_LABEL") == "קלוט בלי כיווץ", label
    assert label.get("SHRINK_CANCEL_LABEL") == "בטל קליטה", label
    assert label.get("SHRINK_PLAIN_HINT") == PLAIN_HINT, label
    body = _body(src, "draw_shrink_dialog")
    answers = [(m.group(1), m.group(2) or "", m.group(3)) for m in re.finditer(
        r"(draw_btn|hit_add)\(a, [^;]*?(?:(BTN_\w+), )?(HIT_SHRINK_\w+)\);", body)]
    assert answers == [("draw_btn", "BTN_PRIMARY", "HIT_SHRINK_CONTINUE"),
                       ("draw_btn", "BTN_PLAIN", "HIT_SHRINK_PLAIN"),
                       ("draw_btn", "BTN_PLAIN", "HIT_SHRINK_CANCEL")], answers
    assert body.count("BTN_PRIMARY") == 1, "one primary answer only"
    assert "cairo_rectangle(cr, lx" not in body, "the underline of the old link is back"
    assert re.search(r"text_draw\(cr, &hint, [^;]*t->muted\)", body), "the hint is not grey"
    assert body.index("HIT_SHRINK_PLAIN") < body.index("&hint, ") < body.index("HIT_SHRINK_CANCEL"), \
        "the hint is not under the secondary answer"


GUI_TOOLS = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _render(binary: Path, out: Path, state_text: str) -> subprocess.CompletedProcess:
    out.mkdir(parents=True, exist_ok=True)
    state = out / "state.txt"
    state.write_text(state_text, newline="\n")
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run(
        [str(binary), "--png", posix(out / "card"), "--size", "1280x800",
         "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run


#: --png renders every card and checks its route; "message" needs a message=.
BASE_STATE = "task=running\ntask_name=Win11\ntask_disk=/dev/sda\npct=-1\nmessage=x|y\n"
QUESTION = "shrink_prompt=7-0|2\nshrink_partition=3|499000000000|146000000000\n" \
           "shrink_partition=4|1000000000|700000000\n"


@GUI_TOOLS
def test_the_dialog_is_drawn_over_the_capture(tmp_path):
    """With a complete question the "shrink" card differs from the plain
    capture card by the dialog: its amber frame and the primary button."""
    binary = _build_gui(tmp_path)
    _render(binary, tmp_path / "q", BASE_STATE + QUESTION)
    _w, _h, ch, plain = _png_rgb(tmp_path / "q" / "card-progress-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "q" / "card-shrink-light.png")
    warn = _token("--clr-warning")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    for theme in ("light", "dark"):
        assert (tmp_path / "q" / f"card-shrink-{theme}.png").exists()


@GUI_TOOLS
def test_an_incomplete_question_is_not_drawn(tmp_path):
    """Two rows for a declared three, or a row that grows: no dialog at all
    (the shrink card renders exactly as the capture card), a warning on
    stderr -- the agent cancels on its ceiling."""
    binary = _build_gui(tmp_path)
    for name, text in (
        ("short", QUESTION.replace("7-0|2", "7-0|3")),
        ("grow", "shrink_prompt=7-0|1\nshrink_partition=3|100|200\n"),
        ("nonce", QUESTION.replace("7-0|2", "7;rm|2")),
    ):
        run = _render(binary, tmp_path / name, BASE_STATE + text)
        assert "shrink question incomplete or malformed" in run.stderr, name
        a = (tmp_path / name / "card-shrink-light.png").read_bytes()
        b = (tmp_path / name / "card-progress-light.png").read_bytes()
        assert a == b, f"{name}: a dialog was drawn for a broken question"


def _bands(rows: list[int]) -> list[tuple[int, int]]:
    """Consecutive row numbers grouped into (first, last) bands."""
    out: list[tuple[int, int]] = []
    for y in rows:
        if out and y == out[-1][1] + 1:
            out[-1] = (out[-1][0], y)
        else:
            out.append((y, y))
    return out


@GUI_TOOLS
def test_the_drawn_dialog_has_one_primary_button_and_a_grey_hint(tmp_path):
    """Nadav, 10/10, in pixels: inside the dialog exactly one button is filled
    with the primary colour (--clr-action) -- "shrink and capture" -- then two
    bordered secondary buttons, and between them grey (--clr-muted) text in
    rows with no ink text: the hint under "capture without shrinking"."""
    binary = _build_gui(tmp_path)
    _render(binary, tmp_path / "q", BASE_STATE + QUESTION)
    for theme in ("light", "dark"):
        w, h, ch, px = _png_rgb(tmp_path / "q" / f"card-shrink-{theme}.png")
        x0, x1 = w // 2 - 300, w // 2 + 300

        def row(rgb, y, tol=12):
            start = (y * w + x0) * ch
            return _count_color(px[start:start + (x1 - x0) * ch], ch, rgb, tol=tol)

        frame = [y for y in range(h) if row(_token("--clr-warning", theme), y) > 400]
        assert frame, f"{theme}: no dialog frame"
        top, bottom = min(frame), max(frame)
        action = _token("--clr-action", theme)
        filled = _bands([y for y in range(top, bottom) if row(action, y) > 400])
        assert len(filled) == 1, f"{theme}: {len(filled)} primary-coloured buttons {filled}"
        first, last = filled[0]
        assert 40 <= last - first + 1 <= 48, f"{theme}: primary button is {last - first + 1}px tall"
        # two secondary buttons below it: their left border is one crisp column
        # (x = dialog left + 28, an integer at 1280), a run of ~44 rows each
        dw = min(w - 64, 760); bx = (w - dw) // 2 + 28
        border = _token("--clr-border", theme)
        col = [y for y in range(last + 1, bottom)
               if all(abs(px[(y * w + bx) * ch + k] - border[k]) <= 12 for k in range(3))]
        boxes = [b for b in _bands(col) if 30 <= b[1] - b[0] + 1 <= 48]
        assert len(boxes) == 2, f"{theme}: {len(boxes)} bordered secondary buttons {boxes}"
        muted, ink = _token("--clr-muted", theme), _token("--clr-text", theme)
        hint = [y for y in range(boxes[0][1] + 1, boxes[1][0])
                if row(muted, y) >= 20 and row(ink, y, tol=26) == 0]
        assert len(hint) >= 5, f"{theme}: no grey hint under 'capture without shrinking' ({hint})"
