"""‏#1808 — מגירה של משכפל עם מטא-דאטה של RAID ישן (הכרעת נדב, 22/10).

עד כאן `disk_fits` סירב לכל דיסק שנמצא עליו מידע של מערך RAID — גם למגירה
של משכפל, שמחזיקה דיסק שהיה פעם חבר במערך. עכשיו:

* **לא סירוב קשיח.** המגירה כתומה (`asking`) עם מה שנמצא, והמפעיל נשאל
  בדיאלוג השאלות של הגואי (askgui.sh, ‏`raid`) — כמו SMART.
* **אישור מפורש** ("כתוב ומחק") — והכתיבה מוחקת את המטא-דאטה (`raid_wipe`)
  לפני בדיקת הכתיבה, וקוראת שוב: רק `none` אחרי המחיקה הוא דיסק נקי.
* בלי תשובה, "דלג", או גואי מת — **לא נכתב**, בגלוי.
* בקליטה ממקור שהוא חבר במערך — הסירוב נשאר (`test_capture_gates.py`).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, PORTS, PRELUDE, env, posix, sh, target
from test_capture_gates import ISW_SIGNATURE, _with_isw_anchor
from test_timeouts import make_stubs

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "waits", "jsonq", "sysinfo", "progress", "restore", "expand", "grow", "ntfsunhib",
        "expandshare", "diskfit", "askgui", "smartgate", "drawergate")

#: עונה לשאלה הראשונה שעל המסך, ושומר את הבקשה ואת מצב המגירה בזמן ההמתנה.
ANSWER = """
for _ in $(seq 1 100); do [ -s "$GUI_DIR/ask-request" ] && break; sleep 0.1; done
cp "$GUI_DIR/ask-request" "$RUN_DIR/request.seen"
cp "$RUN_DIR/targets/sda/state" "$RUN_DIR/state.seen"; cp "$RUN_DIR/targets/sda/error" "$RUN_DIR/error.seen"
N=$(IFS='|' read -r n _ < "$GUI_DIR/ask-request"; echo "$n")
"""


def box(tmp_path: Path, images: dict[str, bytes], stubs: dict[str, str] | None = None) -> tuple[str, Path]:
    run = tmp_path / "run"; dev = tmp_path / "dev"; gui = run / "gui"
    for d in (run, dev, gui):
        d.mkdir(parents=True, exist_ok=True)
    for name, raw in images.items():
        (dev / name).write_bytes(raw)
        (tmp_path / "sys/block" / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / "sys/block" / name / "size").write_text(f"{len(raw) // 512}\n", newline="\n")
        (run / "targets" / name).mkdir(parents=True, exist_ok=True)
        (run / "targets" / name / "state").write_text("waiting\n", newline="\n")
    all_stubs = {"blkid": "#!/bin/sh\nexit 2\n", **(stubs or {})}
    pre = (make_stubs(tmp_path / "stubs", all_stubs)
           + f"export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} DEVROOT={posix(dev)!r} "
           f"SYSROOT={posix(tmp_path)!r} LOG_FILE={posix(run / 'log')!r} IMAGECTL_TEST=1 D_ROLE=cloner; "
           + "".join(f". {posix(AGENT)}/lib/{n}.sh; " for n in LIBS)
           + "attended() { shift; \"$@\"; }; attended_power_check() { :; }; "
           + 'disk_port() { case "$1" in sda) echo 1;; sdb) echo 2;; esac; }; ')
    return pre, run


def gate(tmp_path, answer: str | None, images=None, gui_alive=True):
    pre, run = box(tmp_path, images or {"sda": _with_isw_anchor(), "sdb": bytes(16384)})
    live = "sleep 30 & _gui_pid=$!; " if gui_alive else "sh -c 'exit 0' & _gui_pid=$!; wait $_gui_pid; "
    reply = (f"( {ANSWER} printf '%s|{answer}\\n' \"$N\" > \"$GUI_DIR/ask-decision\" ) & "
             if answer else "")
    out = sh(pre + live + reply + 'WAIT_SMART_GUI_S=4 drawer_raid_gate sda sdb > "$RUN_DIR/out"; echo "rc=$?"; '
             '[ -z "${_gui_pid:-}" ] || kill $_gui_pid 2>/dev/null; wait', timeout=60)
    return out, run


def test_wipe_writes_the_drawer_and_records_what_to_wipe(tmp_path):
    out, run = gate(tmp_path, "wipe")
    assert (run / "out").read_text().split() == ["sda", "sdb"], out.stdout + out.stderr
    seen = (run / "request.seen").read_text().splitlines()
    assert seen == ["1-0|raid|1|sda", "1|isw_raid_member"], seen
    assert (run / "state.seen").read_text().strip() == "asking"   # orange while it waits
    assert "RAID" in (run / "error.seen").read_text() and "isw_raid_member" in (run / "error.seen").read_text()
    assert (run / "targets/sda/raid_wipe").read_text().strip() == "isw_raid_member"
    assert (run / "targets/sda/state").read_text().strip() == "waiting"
    assert not (run / "targets/sda/error").exists()


def test_skip_leaves_the_drawer_unwritten_by_name(tmp_path):
    out, run = gate(tmp_path, "skip")
    assert (run / "out").read_text().split() == ["sdb"], out.stdout + out.stderr
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert "RAID" in (run / "targets/sda/error").read_text()
    assert not (run / "targets/sda/raid_wipe").exists()


def test_no_answer_is_not_an_approval(tmp_path):
    for name, alive in (("dead", False), ("silent", True)):
        out, run = gate(tmp_path / name, None, gui_alive=alive)
        assert (run / "out").read_text().split() == ["sdb"], (name, out.stdout + out.stderr)
        assert (run / "targets/sda/state").read_text().strip() == "skipped", name
        assert not (run / "targets/sda/raid_wipe").exists(), name


def test_a_drawer_without_metadata_is_not_asked(tmp_path):
    out, run = gate(tmp_path, None, images={"sda": bytes(16384), "sdb": bytes(16384)})
    assert (run / "out").read_text().split() == ["sda", "sdb"]
    assert not (run / "request.seen").exists()


# --- disk_fits: only an approved drawer passes the RAID check -------------------


def _fits(tmp_path, approved: bool) -> str:
    pre, run = box(tmp_path, {"sda": _with_isw_anchor()})
    if approved:
        (run / "targets/sda/raid_wipe").write_text("isw_raid_member\n")
    (run / "m.json").write_text('{"partitions":[]}', newline="\n")
    sh(pre + 'disk_fits sda "$RUN_DIR/m.json"; echo "rc=$?"')
    return (run / "targets/sda/error").read_text()


def test_disk_fits_refuses_a_member_unless_the_operator_approved_it(tmp_path):
    assert "RAID של Intel" in _fits(tmp_path / "no", False)
    reason = _fits(tmp_path / "yes", True)
    assert "RAID" not in reason, reason   # the next check speaks, not the RAID one


# --- raid_wipe: zeroed, and read again -------------------------------------------

MIB = 1 << 20


def _wipe(tmp_path, approved="isw_raid_member", stubs=None):
    raw = bytearray(4 * MIB)
    raw[: len(b"MD12")] = b"MD12"
    raw[2 * MIB: 2 * MIB + 4] = b"KEEP"
    raw[-1024: -1024 + len(ISW_SIGNATURE)] = ISW_SIGNATURE
    pre, run = box(tmp_path, {"sda": bytes(raw)}, stubs)
    (run / "targets/sda/raid_wipe").write_text(approved + "\n")
    out = sh(pre + 'raid_wipe sda; echo "rc=$?"')
    return out, run, (tmp_path / "dev/sda").read_bytes()


def test_the_wipe_zeroes_both_ends_and_the_disk_reads_clean(tmp_path):
    out, run, after = _wipe(tmp_path)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert ISW_SIGNATURE not in after and b"MD12" not in after
    assert after[2 * MIB: 2 * MIB + 4] == b"KEEP", "only the first and the last MiB"
    assert len(after) == 4 * MIB


def test_metadata_that_changed_since_the_approval_is_not_wiped(tmp_path):
    out, run, after = _wipe(tmp_path, approved="ddf_raid_member")
    assert out.stdout.strip().endswith("rc=1")
    assert ISW_SIGNATURE in after
    assert "השתנה" in (run / "targets/sda/error").read_text()


def test_a_wipe_that_did_not_take_fails_the_drawer(tmp_path):
    """‏dd שיוצא 0 בלי לכתוב — הקריאה החוזרת היא הראיה, לא קוד היציאה."""
    real = shutil.which("dd") or "/bin/dd"
    lazy = f'#!/bin/sh\ncase "$*" in *of=*) exit 0 ;; esac\nexec {real} "$@"\n'
    out, run, after = _wipe(tmp_path, stubs={"dd": lazy})
    assert out.stdout.strip().endswith("rc=1"), out.stdout + out.stderr
    assert "נשאר על הדיסק" in (run / "targets/sda/error").read_text()


# --- the question on the cloner screen --------------------------------------------


def _dispatch(tmp_path, request: str, token: str, screen: str) -> bool:
    gui = tmp_path / "gui"; gui.mkdir(parents=True)
    (gui / "ask-request").write_text(request, newline="\n")
    sh(f"export GUI_DIR={posix(gui)!r}; . {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/askgui.sh; "
       f"GUI_SCREEN={screen}; token='{token}'; gui_ask_dispatch")
    return (gui / "ask-decision").exists()


def test_the_cloner_answers_a_drawer_question_and_only_that(tmp_path):
    raid = "6-0|raid|1|sda\n1|isw_raid_member\n"
    assert _dispatch(tmp_path / "a", raid, "ask-wipe|6-0", "cloner")
    assert not _dispatch(tmp_path / "b", raid, "ask-wipe|6-0", "")          # not the build screen's
    assert not _dispatch(tmp_path / "c", raid, "ask-restore|6-0", "cloner")  # not an answer of raid
    share = "7-0|share|1|sda\n1|3|100|200\n"
    assert _dispatch(tmp_path / "d", share, "ask-split|7-0", "cloner")


def test_a_malformed_drawer_question_is_never_published(tmp_path):
    pre, run = box(tmp_path, {"sda": bytes(16384)})
    for name, kind, rows in (("two", "raid", "1|isw_raid_member\n2|isw_raid_member\n"),
                             ("word", "raid", "1|isw raid\n"),
                             ("grow", "share", "1|3|200|100\n"),
                             ("field", "share", "1|3|100\n")):
        (run / f"{name}.rows").write_text(rows, newline="\n")
        out = sh(pre + f'sleep 6 & _gui_pid=$!; gui_ask_choice {kind} sda "$RUN_DIR/{name}.rows" 2; echo "rc=$?"; kill $_gui_pid',
                 timeout=30)
        assert out.stdout.strip() == "rc=1", (name, out.stdout)
        assert not (run / "gui/ask-request").exists(), name


@requires_native("jq", why="cloner_gui_state reads the image name with jq")
def test_the_cloner_state_carries_the_drawer_question(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", "asking", 100, 0, "")
    (gui / "ask-request").write_text("6-0|raid|1|sda\n1|isw_raid_member\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text().splitlines()
    assert "ask_prompt=6-0|raid|1|sda" in lines and "ask_row=1|isw_raid_member" in lines
    assert any(line.startswith("drawer=1|sda|asking|") for line in lines), lines


# --- the native dialog and the orange drawer (Testrunner: cc + pango/cairo) ------

from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render  # noqa: E402
from test_cloner_gui import _build_gui, _count_color, _png_rgb, _token  # noqa: E402


@GUI_TOOLS
def test_the_raid_question_is_drawn_over_the_cloner_with_a_red_button(tmp_path):
    binary = _build_gui(tmp_path)
    state = BASE_STATE + "ask_prompt=6-0|raid|1|sdb\nask_row=2|isw_raid_member\n"
    _render(binary, tmp_path / "r", state)
    _w, _h, ch, plain = _png_rgb(tmp_path / "r" / "card-cloner-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "r" / "card-cloner-raid-light.png")
    warn, danger = _token("--clr-warning"), _token("--clr-danger")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    assert _count_color(asked, ch2, danger, tol=12) - _count_color(plain, ch, danger, tol=12) > 1500
    for theme in ("light", "dark"):
        assert (tmp_path / "r" / f"card-cloner-raid-{theme}.png").exists()


@GUI_TOOLS
def test_a_broken_raid_question_is_not_drawn(tmp_path):
    binary = _build_gui(tmp_path)
    for name, text in (("rows", "ask_prompt=6-0|raid|2|sdb\nask_row=2|isw_raid_member\nask_row=1|x\n"),
                       ("word", "ask_prompt=6-0|raid|1|sdb\nask_row=2|isw raid\n")):
        run = _render(binary, tmp_path / name, BASE_STATE + text)
        assert "question incomplete or malformed" in run.stderr, name
