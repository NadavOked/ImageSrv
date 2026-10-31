"""#1171 -- the direct send takes the tail too, on a RAM view (#1466), once a
person said yes.

Measured on metal 20/09, round ``room_ef6afa0f``: a direct send (#715) from a
NVMe of 256,062,259,200 bytes to a Samsung MZ7LN256 of 256,060,514,304 bytes
-- 1,744,896 bytes short -- and the drawer was refused. The capture and the
local clone already end the layout ``SHRINK_TAIL_BYTES`` (64MiB) before the end
of the source (#1202), on a copy-on-write view of the partition (#1466); the
direct send skipped every shrink (``CAPTURE_SINK=discard``), because its second
read must hash to the first, and a view built twice is not promised to be the
same bytes.

Now the direct send takes the tail only -- not the regular 10%/2GiB margin --
and only after a person said yes (Nadav 17/10: never silently). The question is
the capture's own dialog (shrinkgui.sh, screens_shrink.c) in the direct send's
words: "כווץ ושלח" or "שלח בלי כיווץ". The second button, cancel (3/Esc), and NO
answer (no GUI, or the dialog's ceiling ran out) all send the full layout:
nothing is shrunk without a yes, as in the capture and the local clone, but the
send itself goes on, and a smaller drawer is refused by name and by how much
(``disk_fits``). ONE view is made, both reads read it, and it is released
after the second (``direct_send_task``). No room for the tail, or no view: the
full layout too, said in the log.

The first part runs the real ``capture_disk`` with the stubs and the 20/09
numbers of ``test_tail_shift_1202.py``; then the question (agent side and the
drawn dialog); then the real ``direct_send_task`` with the harness of
``test_directsend.py``.

**Negative control:** the production files of this change set back to
origin/main -- agent/lib shrink.sh, shrinkplan.sh, shrinkgui.sh, capture.sh,
directsend.sh and native-gui/src state.c, screens_shrink.c, screens.c, ui.h.
Testrunner 17/10: 11 of 13 fail there (the two guards pass, as their
docstrings say); against the first commit of #1171 (the tail taken silently),
the confirmed test, the three not-confirmed ones, the question and the dialog fail.
"""

from __future__ import annotations

import json
import re

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_capture_refusals import capture_run
from test_cloner_gui import sh as gui_sh   # a CompletedProcess, as test_shrink_gui.py uses it
from test_directsend import _answer, direct_run
from test_shrink_gui import (BASE_STATE, GUI_TOOLS, QUESTION, _bands, _build_gui, _count_color,
                             _png_rgb, _render, _token, setup)
from test_shrink_on_capture import BLOCKDEV_OK, FS_MAP, manifest, order, part, source_writes, stubs, view_sys
from test_tail_shift_1202 import (
    DISK_SECTORS,
    NEARLY_FULL_MIN,
    NTFSRESIZE,
    PACKED_MIN,
    REC_SECTORS,
    REC_START,
    SGDISK,
    TAIL,
    TARGET_BYTES,
    WIN_BYTES,
    WIN_SECTORS,
    WIN_START,
)

pytestmark = requires_native(("bash", BASH))


def _answer_is(word: str) -> str:
    """shrink_ask as the dialog would answer it, recorded where the steps are."""
    return f'shrink_ask() {{ echo asked >> "$RUN_DIR/order.log"; echo {word}; }}; '


YES, PLAIN, CANCEL = _answer_is("continue"), _answer_is("plain"), _answer_is("cancel")
#: No override: the real shrink_ask, and no GUI on this machine -- no answer.
NO_ANSWER = ""


def _capture(tmp_path, ntfs_min: int, answer: str = YES, env: dict | None = None):
    """capture_disk as the direct send's first read (CAPTURE_SINK=discard),
    the 20/09 layout of test_tail_shift_1202.py, and the given answer."""
    size = f'echo {DISK_SECTORS} > "$SYSROOT/sys/block/sda/size"; '
    sysfs = view_sys({3: WIN_SECTORS, 4: REC_SECTORS})
    return capture_run(tmp_path, stubs=stubs(sgdisk=SGDISK, ntfsresize=NTFSRESIZE, blockdev=BLOCKDEV_OK),
                       shell_pre=FS_MAP + sysfs + size + answer,
                       env={"NTFS_MIN": str(ntfs_min), "CAPTURE_SINK": "discard", **(env or {})})


def _ok(box, run, out):
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")


def _agent_log(run) -> str:
    return (run / "agent.log").read_text(encoding="utf-8", errors="replace")


def _pcl_reads(run) -> list[str]:
    """The node every partclone read (-s), in order."""
    reads = []
    for step in order(run):
        if step.startswith("partclone "):
            words = step.split()
            reads.append(words[words.index("-s") + 1])
    return reads


# --- confirmed: "כווץ ושלח" ------------------------------------------------------------

def test_confirmed_the_tail_is_taken_on_a_view_and_fits_the_smaller_drive(tmp_path):
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN)
    _ok(box, run, out)
    steps, dev = order(run), posix(box / "dev")
    first_dm = next(i for i, s in enumerate(steps) if s.startswith(("modprobe", "dmsetup")))
    assert "asked" in steps and steps.index("asked") < first_dm, "a view was made before the yes"
    assert f"ntfsresize -s {WIN_BYTES - TAIL} --no-progress-bar {dev}/mapper/lc-src-3" in steps, steps
    assert source_writes(steps, box) == [], source_writes(steps, box)
    assert f"{dev}/mapper/lc-cut-3" in _pcl_reads(run), _pcl_reads(run)
    m = manifest(run)
    win, rec = part(m, 3), part(m, 4)
    assert win["size_bytes"] == WIN_BYTES - TAIL and win["shrunk_from_bytes"] == WIN_BYTES
    assert win["start_sector"] == WIN_START
    assert rec["start_sector"] == REC_START - TAIL // 512 and rec["source_start_sector"] == REC_START
    from server.library.images import required_bytes
    assert required_bytes(m) == m["min_target_bytes"] <= TARGET_BYTES, (m["min_target_bytes"], TARGET_BYTES)


def test_the_view_outlives_the_first_read_for_the_second(tmp_path):
    """The second read must hash to the first: the view is not released at
    the end of capture_disk -- direct_send_task releases it after the send."""
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN)
    _ok(box, run, out)
    assert not [s for s in order(run) if s.startswith(("dmsetup remove", "rmmod"))], order(run)
    node = run / "capsnap" / "snap" / "3.node"
    assert node.read_text(encoding="utf-8").strip() == f"{posix(box / 'dev')}/mapper/lc-cut-3"


def test_a_direct_send_does_not_take_the_regular_margin(tmp_path):
    """C: with room to spare: a capture shrinks it to its minimum + 10%/2GiB;
    a direct send takes exactly the tail, nothing more."""
    box, run, out = _capture(tmp_path, WIN_BYTES // 2)
    _ok(box, run, out)
    shrinks = [s for s in order(run) if s.startswith("ntfsresize -s ")]
    assert shrinks == [f"ntfsresize -s {WIN_BYTES - TAIL} --no-progress-bar {posix(box / 'dev')}/mapper/lc-src-3"], shrinks
    assert part(manifest(run), 3)["size_bytes"] == WIN_BYTES - TAIL


# --- not confirmed: "שלח בלי כיווץ", cancel, no answer ----------------------------------

@pytest.mark.parametrize("answer,word", [(PLAIN, "plain"), (CANCEL, "cancel"), (NO_ANSWER, "cancel")],
                         ids=["send-without-shrinking", "cancel", "no-answer"])
def test_not_confirmed_nothing_is_shrunk_and_the_full_layout_is_sent(tmp_path, answer, word):
    """Nadav 17/10: never silently. Whatever is not "כווץ ושלח" -- the second
    button, cancel (3/Esc), or no answer at all (the real shrink_ask: no GUI
    here, as when the dialog's ceiling ran out) -- makes no view and shrinks
    nothing; the send goes on with the layout of before, and the log says why.
    All three fail on the first commit of #1171 (it shrank without asking)."""
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN, answer=answer)
    _ok(box, run, out)
    steps = order(run)
    assert not [s for s in steps if s.startswith(("ntfsresize -s", "dmsetup", "modprobe"))], steps
    m = manifest(run)
    assert part(m, 3)["size_bytes"] == WIN_BYTES and "shrunk_from_bytes" not in part(m, 3)
    assert part(m, 4)["start_sector"] == REC_START
    assert f"the shrink was not confirmed ({word};" in _agent_log(run), _agent_log(run)[-1500:]
    assert ("asked" in steps) == bool(answer), steps


def test_a_packed_c_is_sent_at_full_size_and_nobody_is_asked(tmp_path):
    """No room for the tail: no question, the full layout -- the direct send
    of before. A guard: it passes on origin/main too."""
    box, run, out = _capture(tmp_path, PACKED_MIN)
    _ok(box, run, out)
    steps = order(run)
    assert "asked" not in steps and not [s for s in steps if s.startswith(("ntfsresize -s", "dmsetup"))], steps
    m = manifest(run)
    assert part(m, 3)["size_bytes"] == WIN_BYTES and "shrunk_from_bytes" not in part(m, 3)
    assert part(m, 4)["start_sector"] == REC_START and part(m, 4)["size_bytes"] == REC_SECTORS * 512


def test_no_view_sends_the_full_layout_and_says_so(tmp_path):
    """Confirmed, but dmsetup refuses: no view, nothing left behind, the full
    layout is read from the partition itself -- and the log names the reason."""
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN, env={"FAIL_DM_CREATE": "1"})
    _ok(box, run, out)
    m = manifest(run)
    assert part(m, 3)["size_bytes"] == WIN_BYTES and "shrunk_from_bytes" not in part(m, 3)
    assert f"{posix(box / 'dev')}/sda3" in _pcl_reads(run), _pcl_reads(run)
    log = _agent_log(run)
    assert "direct send: no tail" in log and "dmsetup create lc-src-3" in log, log[-2000:]
    assert not (run / "capsnap" / "snap").exists(), "a half-made view is removed"


# --- the question: the console's line, the dialog's request, the drawn dialog -----------

def test_the_question_is_the_direct_sends_and_in_hebrew(tmp_path):
    plan = tmp_path / "plan"
    plan.write_text(f"3|g|u|1|2|{WIN_BYTES}|{WIN_BYTES - TAIL}\n", newline="\n")
    q = sh(f"export CAPTURE_SINK=discard; . {posix(AGENT)}/lib/shrink/shrinkplan.sh; "
           f"shrink_question {posix(plan)!r}")
    assert q.startswith("ההפצה הישירה תכווץ בעותק בזיכרון") and "כווץ ושלח" in q and "הקליטה" not in q, q
    assert not re.search(r"[A-Za-z]", q.replace(" GB", "")), q   # GB: the unit every screen shows
    pre, _gui = setup(tmp_path, "export CAPTURE_SINK=discard; ")   # its plan has two rows
    out = gui_sh(pre + '''
sleep 30 & _gui_pid=$!
( gui_shrink_choice "$RUN_DIR/shrink.plan" > /dev/null 2>&1 ) &
for _ in $(seq 1 50); do [ -s "$GUI_DIR/shrink-request" ] && break; sleep 0.2; done
head -n 1 "$GUI_DIR/shrink-request"; kill $_gui_pid
''')
    assert out.stdout.splitlines()[0].endswith("|2|direct"), out.stdout


@GUI_TOOLS
def test_the_dialog_in_direct_words_has_two_answers(tmp_path):
    """The capture's dialog with |direct: one primary button ("כווץ ושלח"),
    ONE bordered secondary ("שלח בלי כיווץ") -- no third. An unknown third
    field is a broken question, and is not drawn."""
    binary = _build_gui(tmp_path)
    _render(binary, tmp_path / "q", BASE_STATE + QUESTION.replace("7-0|2", "7-0|2|direct"))
    w, h, ch, px = _png_rgb(tmp_path / "q" / "card-shrink-light.png")
    x0, x1 = w // 2 - 300, w // 2 + 300

    def row(rgb, y, tol=12):
        start = (y * w + x0) * ch
        return _count_color(px[start:start + (x1 - x0) * ch], ch, rgb, tol=tol)

    frame = [y for y in range(h) if row(_token("--clr-warning"), y) > 400]
    assert frame, "no dialog frame"
    top, bottom = min(frame), max(frame)
    filled = _bands([y for y in range(top, bottom) if row(_token("--clr-action"), y) > 400])
    assert len(filled) == 1, filled
    dw = min(w - 64, 760)
    bx = (w - dw) // 2 + 28
    border = _token("--clr-border")
    col = [y for y in range(filled[0][1] + 1, bottom)
           if all(abs(px[(y * w + bx) * ch + k] - border[k]) <= 12 for k in range(3))]
    boxes = [b for b in _bands(col) if 30 <= b[1] - b[0] + 1 <= 48]
    assert len(boxes) == 1, f"{len(boxes)} bordered secondary buttons {boxes}"
    run = _render(binary, tmp_path / "bad", BASE_STATE + QUESTION.replace("7-0|2", "7-0|2|other"))
    assert "shrink question incomplete or malformed" in run.stderr
    assert (tmp_path / "bad" / "card-shrink-light.png").read_bytes() == \
        (tmp_path / "bad" / "card-progress-light.png").read_bytes()


# --- the second read and the release, through direct_send_task -------------------------

#: A view made by the first read (as shrink_before_capture would), and a release
#: recorded where the partclone reads are -- the order is the evidence.
VIEW = ('shrink_before_capture() { mkdir -p "$RUN_DIR/capsnap/snap"; head -c 512 /dev/zero > "$DEVROOT/view1"; '
        'echo "$DEVROOT/view1" > "$RUN_DIR/capsnap/snap/1.node"; LC_SNAP_RUN="$RUN_DIR/capsnap"; }; '
        'lc_snap_teardown() { echo released >> "$PCL_ARGV"; rm -rf "$RUN_DIR/capsnap/snap"; }; ')
#: What direct_send_task calls around the send, quiet.
TASK_QUIET = ('progress_loop() { :; }; report_final() { return 0; }; hold_unheard() { :; }; '
              'ui_error_hold() { :; }; finish_and_stop() { :; }; task_done_stays_on() { :; }; ')


def _task(tmp_path, answer=None):
    box = tmp_path / "box"
    pre = VIEW + TASK_QUIET + f'cp {posix(box / "answer.json")!r} "$RESP"; '
    box, run, out = direct_run(tmp_path, answer=answer, pre=pre, call="direct_send_task",
                               env={"PCL_ARGV": posix(box / "pcl.argv")})
    return box, (box / "pcl.argv").read_text(encoding="utf-8").splitlines(), out


def test_both_reads_read_the_view_and_it_is_released_after_the_send(tmp_path):
    box, steps, out = _task(tmp_path)
    view = f"{posix(box / 'dev')}/view1"
    reads = [s.split()[s.split().index("-s") + 1] for s in steps if s.startswith("partclone ")]
    assert reads == [view, view], (steps, out)
    assert steps[-1] == "released" and steps.count("released") == 1, steps
    assert json.loads((box / "run" / "new-manifest.json").read_text("utf-8"))["partitions"][0]["sha256"]


def test_a_closed_wave_still_releases_the_view(tmp_path):
    """A guard: the view was released on this road before, and must still be."""
    box, steps, out = _task(tmp_path, answer=_answer("closed"))
    assert "released" in steps, (steps, out)
    assert not (box / "run" / "capsnap" / "snap").exists()
