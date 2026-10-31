"""השאלות שעברו מהטקסט לגואי — ‏askgui.sh (נדב, 11/10: "הכול גואי").

ההחזרה באתחול של דיסק שנשאר מכווץ (#926/#929) ו"כבה / חזרה לתפריט" בסוף
קליטה (#409) היו שאלות על קונסולת הטקסט. עכשיו הן דיאלוג בסגנון שאלת
הכיווץ (#928), באותה לחיצת-יד: הסוכן מפרסם ``ask-request`` (nonce|kind|
count|subject + שורות), ‏guistate מעתיק ל-``ask_prompt=``/``ask_row=``, הגואי
פולט ``ask-<answer>|<nonce>`` והגשר כותב ``ask-decision``.

* בקשה שלמה בלבד; תשובה נקשרת ל-nonce ולתשובות שהסוג מתיר.
* גואי מת, בקשה פגומה או בלי תשובה עד התקרה — non-zero, והקורא שומר על
  ברירת המחדל הבטוחה (השאר / כבה).
* ‏shrink_restore_ask שואל את הגואי ואינו קורא אף מקש.

בקרה שלילית: על 8eda4eff ‏askgui.sh אינו קיים, ו-shrink_restore_ask קורא
את ה-"1" שמחכה ב-stdin ועונה restore בלי גואי.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, posix, sh

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

pytestmark = requires_native(("bash", BASH))

ROWS = "3|146000000000|499000000000|table\n4|700000000|1000000000|fs\n"


def setup(tmp_path: Path, extra: str = "") -> tuple[str, Path]:
    run = tmp_path / "run"; run.mkdir(parents=True)
    gui = run / "gui"; gui.mkdir()
    (run / "rows").write_text(ROWS, newline="\n")
    pre = (f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} IMAGESRV_TEST=1; '
           f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; '
           f'. {posix(AGENT)}/lib/gui/askgui.sh; {extra}')
    return pre, gui


ANSWER = """
for _ in $(seq 1 50); do [ -s "$GUI_DIR/ask-request" ] && break; sleep 0.2; done
cp "$GUI_DIR/ask-request" "$RUN_DIR/request.seen"
N=$(IFS='|' read -r n _ < "$GUI_DIR/ask-request"; echo "$n")
"""


def test_the_growback_request_carries_every_row_and_a_bound_answer_releases_it(tmp_path):
    pre, gui = setup(tmp_path)
    out = sh(pre + f"""
sleep 30 & _gui_pid=$!
( gui_ask_choice growback sda "$RUN_DIR/rows" 30 > "$RUN_DIR/out"; echo "rc=$?" > "$RUN_DIR/rc" ) &
CH=$!
{ANSWER}
printf '%s|restore\\n' "$N" > "$GUI_DIR/ask-decision"
wait $CH; kill $_gui_pid
cat "$RUN_DIR/out" "$RUN_DIR/rc"; ls "$GUI_DIR"
""")
    assert out.returncode == 0, out.stderr
    seen = (tmp_path / "run" / "request.seen").read_text().splitlines()
    assert seen == ["1-0|growback|2|sda", "3|146000000000|499000000000|table",
                    "4|700000000|1000000000|fs"]
    assert out.stdout.split()[:2] == ["restore", "rc=0"], out.stdout
    assert "ask-request" not in out.stdout and "ask-decision" not in out.stdout


def test_the_capture_end_request_has_no_rows_and_the_seconds_as_subject(tmp_path):
    pre, gui = setup(tmp_path)
    out = sh(pre + f"""
sleep 30 & _gui_pid=$!
( gui_ask_choice capture-end 20 "" 30 > "$RUN_DIR/out" ) &
CH=$!
{ANSWER}
printf '%s|menu\\n' "$N" > "$GUI_DIR/ask-decision"
wait $CH; kill $_gui_pid; cat "$RUN_DIR/out"
""")
    assert (tmp_path / "run" / "request.seen").read_text().splitlines() == ["1-0|capture-end|0|20"]
    assert out.stdout.strip() == "menu", out.stdout + out.stderr


def test_a_stale_nonce_or_an_answer_of_another_kind_does_not_release(tmp_path):
    pre, gui = setup(tmp_path)
    out = sh(pre + f"""
sleep 30 & _gui_pid=$!
( gui_ask_choice growback sda "$RUN_DIR/rows" 30 > "$RUN_DIR/out"; echo done > "$RUN_DIR/rc" ) &
CH=$!
{ANSWER}
printf '9-0|restore\\n' > "$GUI_DIR/ask-decision"; sleep 2
[ -f "$RUN_DIR/rc" ] && echo RELEASED-ON-STALE
printf '%s|menu\\n' "$N" > "$GUI_DIR/ask-decision"; sleep 2
[ -f "$RUN_DIR/rc" ] && echo RELEASED-ON-OTHER-KIND
printf '%s|leave\\n' "$N" > "$GUI_DIR/ask-decision"
wait $CH; kill $_gui_pid; cat "$RUN_DIR/out"
""")
    assert "RELEASED-ON-STALE" not in out.stdout
    assert "RELEASED-ON-OTHER-KIND" not in out.stdout
    assert out.stdout.strip().endswith("leave"), out.stdout


def test_a_dead_gui_answers_nothing(tmp_path):
    pre, gui = setup(tmp_path)
    out = sh(pre + """
sh -c 'exit 0' & _gui_pid=$!; wait $_gui_pid
gui_ask_choice growback sda "$RUN_DIR/rows" 30 > "$RUN_DIR/out"; echo "rc=$?"
""")
    assert "rc=1" in out.stdout.splitlines(), out.stdout + out.stderr
    assert not (gui / "ask-request").exists()


def test_no_answer_within_the_ceiling_fails_and_clears_the_question(tmp_path):
    pre, gui = setup(tmp_path)
    started = time.monotonic()
    out = sh(pre + """
sleep 25 & _gui_pid=$!
gui_ask_choice capture-end 2 "" 2 > "$RUN_DIR/out"; echo "rc=$?"
kill $_gui_pid
""", timeout=60)
    assert "rc=1" in out.stdout.splitlines(), out.stdout + out.stderr
    assert "no answer to the capture-end question in 2s" in out.stderr
    assert not (gui / "ask-request").exists()
    assert time.monotonic() - started < 20


def test_a_malformed_request_is_never_published(tmp_path):
    for name, rows, kind, subject in (
        ("grows", "3|500|100|table\n", "growback", "sda"),
        ("kind", "3|100|500|both\n", "growback", "sda"),
        ("dup", "3|100|500|table\n3|100|500|fs\n", "growback", "sda"),
        ("empty", "", "growback", "sda"),
        ("subject", "3|100|500|table\n", "growback", "sd a;x"),
        ("unknown", "", "reboot", "x"),
    ):
        sub = tmp_path / name; sub.mkdir()
        pre, gui = setup(sub)
        (sub / "run" / "rows").write_text(rows, newline="\n")
        out = sh(pre + f"""
sleep 6 & _gui_pid=$!   # short: a kill racing the fork on a loaded Windows host must not hold the pipe 30 s
gui_ask_choice {kind} '{subject}' "$RUN_DIR/rows" 5; echo "rc=$?"; kill $_gui_pid
""", timeout=30)
        assert out.stdout.strip() == "rc=1", (name, out.stdout)
        assert not (gui / "ask-request").exists(), name


# --- the bridge side ------------------------------------------------------------


def dispatch(tmp_path: Path, request: str, token: str, screen: str = "") -> Path:
    pre, gui = setup(tmp_path)
    (gui / "ask-request").write_text(request, newline="\n")
    sh(pre + f"GUI_SCREEN={screen}; token='{token}'; gui_ask_dispatch; echo rc=$?")
    return gui


def test_the_bridge_accepts_only_the_live_nonce_and_a_valid_answer(tmp_path):
    req = "4-0|growback|1|sda\n3|100|500|table\n"
    assert (dispatch(tmp_path / "ok", req, "ask-restore|4-0") / "ask-decision").read_text() == "4-0|restore\n"
    for name, token, screen in (("stale", "ask-restore|3-0", ""), ("kind", "ask-menu|4-0", ""),
                                ("cloner", "ask-restore|4-0", "cloner"), ("nonce", "ask-restore|4;0", "")):
        gui = dispatch(tmp_path / name, req, token, screen)
        assert not (gui / "ask-decision").exists(), name


def test_the_state_carries_the_question(tmp_path):
    pre, gui = setup(tmp_path)
    (gui / "ask-request").write_text("4-0|growback|1|sda\n3|100|500|table\n", newline="\n")
    out = sh(pre + "gui_ask_state")
    assert out.stdout.splitlines() == ["ask_prompt=4-0|growback|1|sda", "ask_row=3|100|500|table"]
    (gui / "ask-request").unlink()
    assert sh(pre + "gui_ask_state; echo rc=$?").stdout.strip() == "rc=0"


# --- shrink_restore_ask: the GUI is asked, the keyboard never ---------------------


SHRUNK_ASK = "3|table|499000000000|146000000000\n4|fs|1000000000|700000000\n"


def restore_ask(tmp_path: Path, body: str, stdin: str = "1\n") -> subprocess.CompletedProcess:
    pre, gui = setup(tmp_path, f'. {posix(AGENT)}/lib/shrink/shrinkoffer.sh; ')
    (tmp_path / "run" / "targets" / "sda").mkdir(parents=True)
    (tmp_path / "run" / "shrunk.ask").write_text(SHRUNK_ASK, newline="\n")
    run = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + pre + body],
                         input=stdin.encode(), capture_output=True, cwd=str(REPO), timeout=90)
    return subprocess.CompletedProcess(run.args, run.returncode,
                                       run.stdout.decode("utf-8", "replace"),
                                       run.stderr.decode("utf-8", "replace"))


def test_the_grow_back_question_goes_to_the_gui_with_now_and_original(tmp_path):
    out = restore_ask(tmp_path, f"""
sleep 30 & _gui_pid=$!
( {ANSWER} printf '%s|restore\\n' "$N" > "$GUI_DIR/ask-decision" ) &
shrink_restore_ask sda "$RUN_DIR/shrunk.ask"; kill $_gui_pid
""")
    assert out.stdout.strip() == "restore", out.stdout + out.stderr
    seen = (tmp_path / "run" / "request.seen").read_text().splitlines()
    assert seen == ["1-0|growback|2|sda", "3|146000000000|499000000000|table",
                    "4|700000000|1000000000|fs"]


def test_without_a_gui_the_disk_is_left_and_no_key_is_read(tmp_path):
    """"1" ב-stdin היה restore בתפריט הטקסט שנמחק."""
    out = restore_ask(tmp_path, """
sh -c 'exit 0' & _gui_pid=$!; wait $_gui_pid
shrink_restore_ask sda "$RUN_DIR/shrunk.ask"
IFS= read -r _left && echo "STDIN-UNTOUCHED $_left"
""")
    assert out.stdout.splitlines()[0] == "leave", out.stdout + out.stderr
    assert "STDIN-UNTOUCHED 1" in out.stdout
    assert "left as it is" in out.stderr


# --- the native dialog (renders on the Testrunner; cc + pango/cairo there) -------

from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render  # noqa: E402
from test_cloner_gui import _build_gui, _count_color, _png_rgb, _token  # noqa: E402

GROWBACK = ("ask_prompt=4-0|growback|2|sda\n"
            "ask_row=3|146000000000|499000000000|table\n"
            "ask_row=4|700000000|1000000000|fs\n")
CAPTURE_END = "ask_prompt=5-0|capture-end|0|20\n"


@GUI_TOOLS
def test_the_growback_dialog_is_drawn_over_the_menu_in_the_shrink_style(tmp_path):
    """The approved pattern (#928): the amber frame and exactly one button
    filled with the primary colour, over the screen behind it."""
    binary = _build_gui(tmp_path)
    _render(binary, tmp_path / "g", BASE_STATE + GROWBACK)
    _w, _h, ch, plain = _png_rgb(tmp_path / "g" / "card-menu-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "g" / "card-growback-light.png")
    warn = _token("--clr-warning")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    for theme in ("light", "dark"):
        assert (tmp_path / "g" / f"card-growback-{theme}.png").exists()


@GUI_TOOLS
def test_the_capture_end_dialog_is_drawn_over_the_capture(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, tmp_path / "c", BASE_STATE + CAPTURE_END)
    _w, _h, ch, plain = _png_rgb(tmp_path / "c" / "card-progress-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "c" / "card-capture-end-light.png")
    warn = _token("--clr-warning")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500


@GUI_TOOLS
def test_an_incomplete_question_is_not_drawn(tmp_path):
    binary = _build_gui(tmp_path)
    for name, text in (
        ("short", GROWBACK.replace("4-0|growback|2|", "4-0|growback|3|")),
        ("grow", "ask_prompt=4-0|growback|1|sda\nask_row=3|500|100|table\n"),
        ("kind", "ask_prompt=4-0|reboot|0|sda\n"),
        ("nonce", GROWBACK.replace("4-0|", "4;rm|")),
    ):
        run = _render(binary, tmp_path / name, BASE_STATE + text)
        assert "question incomplete or malformed" in run.stderr, name
        a = (tmp_path / name / "card-growback-light.png").read_bytes()
        b = (tmp_path / name / "card-menu-light.png").read_bytes()
        assert a == b, f"{name}: a dialog was drawn for a broken question"
