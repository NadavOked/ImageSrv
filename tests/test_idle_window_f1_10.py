"""Mockup 10 (#1858, Nadav 24/10): the cloner's idle power-off window, and F1 only when known.

"מאושר רק אם אנחנו יודעים שיש F1 לדיסק." The window that says the machine powers off in 30 s
(#434, after 5 minutes with nobody) warns about the BIOS's F1 stop only when a drawer holds a
disk known to cause it: SMART red because its health failed or could not be read (#2078). A red
disk for another reason (read-only) is not an F1 -- no warning. The window has two buttons:
"השאר דלוק" (Esc -- any key keeps it on, as before) and "כבה עכשיו" (Enter -- off now:
idle-poweroff -> idle.now -> idle.sh).
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH
from test_agent_idle import _since, run_idle
from test_cloner_gui import _build_gui, _count_color, _png, _png_rgb, _token
from test_failure_screen_1569 import GUI_BUILD

pytestmark = requires_native(("bash", BASH))


def test_the_warning_says_how_long_the_machine_waited(tmp_path):
    out, _ = run_idle(tmp_path, _since(tmp_path, 10) + "; touch() { :; }",
                      '(sleep 0.3; cp "$RUN_DIR/idle.warn" "$RUN_DIR/seen") & idle_check; wait')
    at, idle_s = (tmp_path / "run" / "seen").read_text(encoding="utf-8").strip().split("|")
    assert at.isdigit() and idle_s == "2"   # IDLE_POWEROFF_S of the harness


def test_power_off_now_ends_the_count_at_once(tmp_path):
    import time
    t0 = time.monotonic()
    out, calls = run_idle(tmp_path, _since(tmp_path, 10) + "; export IDLE_WARN_S=20",
                          '(sleep 1; : > "$RUN_DIR/idle.now") & idle_check; wait')
    assert "'כבה עכשיו' pressed" in out, out
    assert calls.read_text(encoding="utf-8") == "POWEROFF -f\n"
    assert time.monotonic() - t0 < 12, "the 20 s count ran out instead"


def test_a_stale_press_from_before_the_window_powers_nothing_off(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10) + '; : > "$RUN_DIR/idle.now"',
                          '(sleep 0.3; touch "$RUN_DIR/idle.touch") & idle_check; wait')
    assert "cancelled by activity" in out and not calls.exists(), out


def test_the_bridge_takes_the_press_only_while_the_window_is_up():
    bridge = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    assert 'idle-poweroff) [ -s "$RUN_DIR/idle.warn" ] && : > "$RUN_DIR/idle.now"' in bridge


STATE = (
    "cdisk=0|sda|WDC WD5000AAKX|500107862016|ok||3.0|3.0|0|1|SATA 0||\n"
    "cdisk=1|sdb|Samsung SSD 870 EVO|500107862016|fail|{cause}|3.0|3.0|0|2|SATA 1||\n"
    "idle_poweroff_at=1792900000|300\nhello_age=4\nhello_rc=200\nmessage=x|y\n"
)


@GUI_BUILD
def test_the_f1_warning_is_drawn_only_for_a_disk_known_to_stop_the_bios(tmp_path):
    binary = _build_gui(tmp_path)
    counts = {}
    for cause in ("health_failed", "health_unread", "readonly"):
        st = tmp_path / f"{cause}.txt"
        st.write_text(STATE.format(cause=cause), encoding="utf-8", newline="\n")
        _png(binary, tmp_path / cause, st, size="1280x800")
        w, h, ch, px = _png_rgb(tmp_path / f"{cause}-standby-light.png")
        counts[cause] = _count_color(px, ch, _token("--clr-warning-soft"), tol=4)
    # the warning box: an orange-soft area, on the window, for the two F1 causes only
    assert counts["health_failed"] - counts["readonly"] > 4000, counts
    assert counts["health_unread"] - counts["readonly"] > 4000, counts


def test_enter_in_the_window_is_power_off_now_and_not_a_touch():
    main = (AGENT.parent / "native-gui" / "src" / "main.c").read_text(encoding="utf-8")
    handle = main[main.index("static int handle(App *a"):]
    assert handle.index("idle_modal(a, e, demo)") < handle.index("emit_touch(a, demo)"), "the touch would cancel first"
    modal = main[main.index("static int idle_modal("):main.index("static int handle(App *a")]
    assert 'emit1("idle-poweroff")' in modal and "KEYSYM_ENTER" in modal and "HIT_IDLE_OFF" in modal
