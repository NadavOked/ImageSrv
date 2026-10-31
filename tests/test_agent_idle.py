"""‏#434: מכונה שממתינה לאיש נכבית אחרי חמש דקות — ולעולם לא כשמוצג כשל.

שלושה מצבים שנראו זהים מבחוץ (מחשב בנייה על מסך כניסה 35 דקות, מחשב
שיכפול `failed` שעות, תחנה שהפסיקה hello): רק הראשון הוא "ממתין". הכלל:
המתנה מעבר לסף → אזהרה של IDLE_WARN_S שניות → כיבוי דרך arm_wol; פעילות
(מקש בגואי, תשובת hello שהשתנתה, סבב פתוח, `stay_on`) מאפסת; מסך כשל
אינו מגיע ל-`poll_sleep` בכלל, ולכן אינו מגיע לשעון. ‏`poweroff` מזויף."""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native

from test_agent import AGENT, BASH, posix, sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

LIB = flat_lib()
def run_idle(tmp_path: Path, setup: str, script: str) -> str:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    return sh(
        f'export RUN_DIR={posix(run)!r} IDLE_POWEROFF_S=2 IDLE_WARN_S=1; '
        f'. {posix(LIB)}/common.sh; . {posix(LIB)}/idle.sh; '
        f'log() {{ printf "%s\\n" "$*"; }}; sync() {{ :; }}; '
        # #1483: poweroff -f does not return -- one that returns is a failed power-off
        f'poweroff() {{ printf "POWEROFF %s\\n" "$*" >> {posix(calls)!r}; exit 0; }}; '
        f'reboot() {{ printf "REBOOT %s\\n" "$*" >> {posix(calls)!r}; }}; '
        'arm_wol() { return 0; }; '
        + setup + '; ' + script
    ), calls


def _since(tmp_path: Path, ago: int) -> str:
    return f'printf "%s\\n" "$(( $(date +%s) - {ago} ))" > "$RUN_DIR/idle.since"'


def test_idle_past_the_threshold_warns_then_powers_off(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10), "idle_check")
    assert "power-off in 1s unless a key is pressed" in out
    assert "no activity for 2s: powering off with wake-on-LAN armed" in out
    assert calls.read_text(encoding="utf-8") == "POWEROFF -f\n"


def test_a_key_press_during_the_countdown_cancels_it(tmp_path):
    # the kiosk touches idle.touch (the GUI's "touch" token); newer than the
    # clock = activity -- simulated by a touch after the warning began
    out, calls = run_idle(tmp_path, _since(tmp_path, 10),
                          '(sleep 0.3; touch "$RUN_DIR/idle.touch") & idle_check; wait')
    assert "cancelled by activity" in out
    assert not calls.exists()
    assert not (tmp_path / "run" / "idle.warn").exists()


@pytest.mark.parametrize("exempt", [
    'touch "$RUN_DIR/stay_on"',
    'export D_SESSION_STATE=open',
    'export D_TASK="{\\"id\\":\\"t1\\"}"',
    'export IDLE_POWEROFF_S=0',
    'touch "$RUN_DIR/idle.touch"; sleep 1',   # touched after the clock started
])
def test_exempt_states_never_power_off(tmp_path, exempt):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10) + "; " + exempt, "idle_check; echo rc=$?")
    assert "rc=0" in out and "powering off" not in out
    assert not calls.exists()


def test_a_changed_hello_answer_is_activity(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10), "POLL_MARK_CHANGED=1 idle_check; cat \"$RUN_DIR/idle.since\"")
    assert not calls.exists()
    since = int(out.strip().splitlines()[-1])
    assert since > 0


def test_without_wol_the_machine_stays_on_and_says_so(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10) + "; arm_wol() { return 1; }", "idle_check; echo rc=$?")
    assert "not all supported wired interfaces were armed -- not powering off" in out and "rc=1" in out
    assert not calls.exists()


def test_a_poweroff_that_returns_is_logged_with_its_rc_not_taken_as_off(tmp_path):
    """#1483, metal 16/10 (cloner 2): "powering off with wake-on-LAN armed"
    every ~45 s and the machine stayed up -- poweroff came back and nothing
    said so. A return is a failure, logged with its rc; the clock restarts."""
    out, calls = run_idle(tmp_path, _since(tmp_path, 10) + '; poweroff() { echo "POWEROFF $*"; return 3; }',
                          "idle_check; echo rc=$?")
    assert "POWEROFF -f" in out, out
    assert "poweroff -f returned rc=3 -- the machine is still on" in out and "rc=1" in out, out


def test_the_clock_beats_only_in_poll_sleep_and_the_failure_hold_never_reaches_it():
    poll = (LIB / "poll.sh").read_text(encoding="utf-8")
    assert "command -v idle_check >/dev/null 2>&1 && idle_check" in poll
    assert "POLL_MARK_CHANGED=1" in poll
    ui = (LIB / "ui.sh").read_text(encoding="utf-8") + (LIB / "hold.sh").read_text(encoding="utf-8")
    assert "poll_sleep" not in ui, "מסך כשל שמגיע ל-poll_sleep היה מגיע לשעון הכיבוי"
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/idle.sh"' in agent
    bridge = (LIB / "guibridge.sh").read_text(encoding="utf-8")
    assert 'touch "$RUN_DIR/idle.touch" || return 1' in bridge, "מקש בגואי חייב להגיע לשעון"
    gui = (AGENT.parent / "native-gui" / "src" / "main.c").read_text(encoding="utf-8")
    assert 'emit("touch"); emit_end();' in gui
    assert 'if (a->st.idle_poweroff_at <= 0 && now - last < 10) return;' in gui, "בזמן ספירה כל מקש נשלח, אחרת עד אחד ל-10 שניות"


# --- ‏#980: "כיבוי כולם" מהקונסולה נוסע ב-hello (`power_action`) ---------------
# ‏json_get מזויף: jq אינו מותקן בתחנת הווינדוס, והמסלול הנבדק הוא ההחלטה
# ב-idle.sh. שם השדה נעוץ משני הצדדים: כאן בטקסט של idle.sh, ובשרת
# ב-`test_room_console.py` (‏`power_action` בתשובת ה-hello).
_PA = ('json_get() { [ "$2" = ".power_action" ] && [ -s "$RUN_DIR/pa" ] '
       '&& { cat "$RUN_DIR/pa"; return 0; }; echo null; }; RESP=/dev/null')


def test_a_console_poweroff_in_the_answer_powers_off_a_busy_waiting_machine(tmp_path):
    # השעון טרי (הייתה פעילות לפני רגע) — ובכל זאת נכבית: זו בקשה, לא המתנה.
    out, calls = run_idle(tmp_path, _since(tmp_path, 0) + "; " + _PA
                          + '; echo poweroff > "$RUN_DIR/pa"', "idle_check")
    assert "the console asked to power off: powering off with wake-on-LAN armed" in out
    assert calls.read_text(encoding="utf-8") == "POWEROFF -f\n"


def test_a_console_poweroff_without_wol_stays_on_and_says_so(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 0) + "; " + _PA
                          + '; echo poweroff > "$RUN_DIR/pa"; arm_wol() { return 1; }',
                          "idle_check; echo rc=$?")
    assert "not all supported wired interfaces were armed -- not powering off" in out and "rc=1" in out
    assert not calls.exists()


# ‏#977 §4: "אתחול מרחוק" ממגירת המחשב — אותו ערוץ, ערך אחר. בלי arm_wol:
# מכונה שמאותחלת קמה בעצמה, ואין מה להעיר אחר כך.
def test_a_console_reboot_in_the_answer_reboots_without_arming_wol(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 0) + "; " + _PA
                          + '; echo reboot > "$RUN_DIR/pa"; arm_wol() { echo ARMED; return 1; }',
                          "idle_check")
    assert "the console asked to reboot -- rebooting" in out
    assert "ARMED" not in out
    assert calls.read_text(encoding="utf-8") == "REBOOT -f\n"


@pytest.mark.parametrize("value", ["", "null", "REBOOT", "POWEROFF", "reboot now"])
def test_anything_but_poweroff_or_reboot_in_the_answer_is_ignored(tmp_path, value):
    out, calls = run_idle(tmp_path, _since(tmp_path, 0) + "; " + _PA
                          + f'; printf %s {value!r} > "$RUN_DIR/pa"', "idle_check; echo rc=$?")
    assert "rc=0" in out and "powering off" not in out
    assert not calls.exists()


def test_the_power_check_reads_the_hello_answer_field():
    idle = (LIB / "idle.sh").read_text(encoding="utf-8")
    assert 'json_get "$RESP" ".power_action"' in idle
