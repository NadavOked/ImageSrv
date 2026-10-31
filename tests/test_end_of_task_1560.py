"""סוף משימה לפי סוג מכונה — הכרעת נדב 17/10 (#1560).

**מחשב בנייה:** אחרי קליטה, שליחה ישירה וסבב שנפתח מהגואי שלו — **שאלה**
("כבה" / "חזרה לתפריט", הדיאלוג של askgui.sh). "שחזור לדיסק הזה" אינו כאן —
הוא בענף fix/single-restore-progress-gui (#1561).
**משכפל:** אחרי שיכפול מקומי, סבב מהשרת ו"שחזור אימג׳ מהשרת" (מקש 3, שהוא
סבב של מכונה אחת ועובר באותו `do_restore_drawers`, וכמוהו שליחה ישירה
שהמשכפל מקבל) — **גם כשנכשלו** —
ספירה של 20 שניות לכיבוי עם כפתור "כבה עכשיו".
**ברזל 17/10:** משכפל על המסך הראשי יוצא ממנו לסבב שנפתח עבורו; ומחשב
בנייה אינו נכבה מ-idle בזמן סבב שהוא פתח או מציג.

מה נבדק:

* ``end_countdown`` (idle.sh) — סופר, נכבה דרך ``finish_and_stop``; "כבה
  עכשיו" מהקיוסק (``end.now``); לחיצה ישנה אינה נספרת; כיבוי שסורב משאיר
  סיבה על המסך.
* ``idle_round_shown`` (idle.sh) — סבב שנפתח כאן, או סבב/שיעור פעיל שהקיוסק
  מצייר עכשיו, אינו idle; בלי סבב (או קיוסק שמת) — הכיבוי כמו היום.
* הצנרת: ``gui_hello_state`` נושא ``end_poweroff=``, הגשר כותב ``end.now``
  רק כשיש ספירה, וצופה הסבב (roomflow.sh) מוסר ``room.ended`` רק כשהסבב
  שנפתח כאן **נעלם** — לא כשנכשל, לא כשנסגר כאן, ולא כשהקריאה נכשלה.
* כל אתר קריאה: ההפצה הישירה שואלת (בלי `sleep 20`), הסבב שואל מתפריט
  הבנייה, ‏`do_restore_drawers` סופר במקום לכבות מיד, ושיכפול מקומי סופר
  — שהצליח וגם שנכשל (ושנעצר ביד — לא).
* הגואי (מעבדה בלבד: cc + pango): הספירה והשאלות החדשות מצוירות; רשומה
  פגומה אינה מצוירת.

בקרה שלילית: ראו גוף ה-PR — על origin/main 9eba8e7d אתרי הקריאה כבים מיד /
אינם שואלים / נשארים בתפריט / נכבים באמצע סבב.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix
from test_capture_end import END_STUBS
from test_done_stays_done import DIRECT_ANSWER, DIRECT_STUBS
from test_final_report import (  # noqa: F401 — ‏`reports` הוא fixture
    IMAGE,
    SESSION,
    cut_function,
    native_tools,
    reports,
    run_path,
    url_of,
)

pytestmark = requires_native(("bash", BASH))
with_jq = requires_native(("bash", BASH), "jq")


def q(p: Path) -> str:
    return shlex.quote(posix(p))


def bash(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        stdin=subprocess.DEVNULL, timeout=timeout,
    )


def asked_dialog(trace: list[str], kind: str) -> bool:
    """``DIALOG <kind> <epoch>  20`` — השאלה; בלי תשובה כיבוי ב-20 ש׳ (הכרעת
    נדב 17/10), והנושא הוא **רגע הכיבוי** — הגואי סופר אליו. עד ברזל 17/10:
    ``20  20`` — "20" כטקסט קבוע שלא זז."""
    now = int(time.time())
    for line in trace:
        parts = line.split(" ")
        if parts[:2] == ["DIALOG", kind] and len(parts) == 5 and parts[3] == "" and parts[4] == "20":
            return parts[2].isdigit() and now + 10 <= int(parts[2]) <= now + 21
    return False


def trace_of(run: Path) -> list[str]:
    f = run / "trace"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


# --- end_countdown (idle.sh) ---------------------------------------------------

#: ‏finish_and_stop מזויף: רושם, ומחזיר את PO_RC. ‏POWEROFF_WHY=wol כש-1 —
#: מה ש-safe_poweroff האמיתי קובע כשה-WoL לא נדרך (#587).
POWER_STUB = (
    'finish_and_stop() { echo "POWEROFF" >> "$RUN_DIR/trace"; '
    '[ "${PO_RC:-0}" = 0 ] && return 0; POWEROFF_WHY=wol; return 1; }; '
)


def countdown(tmp_path: Path, body: str, env: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True, exist_ok=True)
    # #2024: the count is for a clean end -- one target done and read back (the
    # calls below name it, sdb); a problem holds instead (test_end_hold_2024.py)
    (run / "targets" / "sdb").mkdir(parents=True, exist_ok=True)
    (run / "targets" / "sdb" / "state").write_text("done\n", newline="\n")
    (run / "targets" / "sdb" / "readback").write_text("sampled\n", newline="\n")
    script = (
        f"export RUN_DIR={q(run)} LOG_FILE={q(run / 'agent.log')} MAC=aa:bb:cc:dd:ee:ff "
        f"IMAGESRV_TEST=0 END_POWEROFF_S=20 {env}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/idle.sh; "
        + POWER_STUB + body
    )
    return bash(script), run


def test_the_count_runs_its_seconds_on_the_screen_and_then_powers_off(tmp_path):
    """‏20 שניות, ובזמן הספירה ``end.poweroff`` = ‏<epoch>|<kind> — מה שכל
    מסך מצייר. בסוף: ‏finish_and_stop (‏safe_poweroff, ‏WoL דרוך), והקובץ נמחק."""
    before = int(time.time())
    out, run = countdown(tmp_path, (
        'sleep() { [ -f "$RUN_DIR/seen" ] || cp "$RUN_DIR/end.poweroff" "$RUN_DIR/seen"; '
        'echo SLEEP >> "$RUN_DIR/trace"; }; '
        'end_countdown drawers "drawers complete" "" "" sdb; echo "rc=$?"'))
    trace = trace_of(run)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert trace.count("SLEEP") == 20 and trace[-1] == "POWEROFF", trace
    at, kind, done, total, held = (run / "seen").read_text().strip().split("|")
    assert (kind, done, total, held) == ("drawers", "", "", "0")
    assert before + 20 <= int(at) <= int(time.time()) + 20
    assert not (run / "end.poweroff").exists()
    assert "drawers complete -- powering off in 20s" in (run / "agent.log").read_text(encoding="utf-8")


def test_the_button_from_the_kiosk_powers_off_at_once_and_an_old_press_does_not_count(tmp_path):
    """‏``end.now`` (הגשר כותב אותו על end-poweroff) — כיבוי בשנייה שאחרי. לחיצה
    שנשארה מספירה קודמת נמחקת בתחילת הספירה: אחרת 0 שניות במקום 1."""
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    (run / "end.now").write_text("")
    out, run = countdown(tmp_path, (
        'sleep() { echo SLEEP >> "$RUN_DIR/trace"; : > "$RUN_DIR/end.now"; }; '
        'end_countdown localclone "local clone ended" "" "" sdb; echo "rc=$?"'))
    assert trace_of(run) == ["SLEEP", "POWEROFF"], trace_of(run)
    assert "'כבה עכשיו' pressed" in (run / "agent.log").read_text(encoding="utf-8")
    assert not (run / "end.now").exists()


def test_a_refused_power_off_returns_and_leaves_the_reason_on_every_screen(tmp_path):
    """‏WoL לא נדרך: לא כבה (#587), חוזר עם 1 — והסיבה ב-power.fail, שכל מסך
    מצייר (gui_hello_state). הספירה עצמה ירדה מהמסך."""
    out, run = countdown(tmp_path, 'sleep() { :; }; end_countdown drawers "drawers complete" "" "" sdb; echo "rc=$?"',
                         env="PO_RC=1")
    assert out.stdout.strip().endswith("rc=1"), out.stdout + out.stderr
    assert "Wake-on-LAN" in (run / "power.fail").read_text(encoding="utf-8")
    assert not (run / "end.poweroff").exists()
    assert "the machine stayed on (wol)" in (run / "agent.log").read_text(encoding="utf-8")


# --- the kiosk's plumbing --------------------------------------------------------


def test_every_screen_carries_the_count(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    pre = (f"export RUN_DIR={q(run)}; . {posix(AGENT)}/lib/core/common.sh; "
           f". {posix(AGENT)}/lib/gui/guistate.sh; ")
    assert "end_poweroff" not in bash(pre + "gui_hello_state").stdout
    (run / "end.poweroff").write_text("1760000000|partial|2|3|0\n", newline="\n")
    lines = bash(pre + "gui_hello_state").stdout.splitlines()
    assert "end_poweroff=1760000000|partial|2|3|0" in lines, lines


def bridge_token(tmp_path: Path, token: str, counting: bool, kind: str = "drawers") -> Path:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    if counting:
        (run / "end.poweroff").write_text(f"1760000000|{kind}|1|2|0\n", newline="\n")
    bash(f"export RUN_DIR={q(run)} GUI_DIR={q(run / 'gui')}; . {posix(AGENT)}/lib/core/common.sh; "
         f". {posix(AGENT)}/lib/gui/guibridge.sh; gui_role() {{ return 1; }}; "
         f"token={token}; gui_dispatch; echo rc=$?")
    return run


def test_the_bridge_takes_the_button_without_a_session_only_while_counting(tmp_path):
    assert (bridge_token(tmp_path / "on", "end-poweroff", True) / "end.now").exists()
    assert not (bridge_token(tmp_path / "off", "end-poweroff", False) / "end.now").exists()


@pytest.mark.parametrize("kind, held", [("partial", True), ("failed", True),
                                        ("drawers", False), ("localclone", False)])
def test_the_count_can_be_stopped_only_when_something_failed(tmp_path, kind, held):
    """**הכרעת נדב 17/10 (3).** נכשל חלק — אפשר לעצור את הספירה כדי לקרוא
    את התוצאות. הכול הצליח — אין מה לקרוא, ואין עצירה."""
    assert (bridge_token(tmp_path, "end-hold", True, kind) / "end.hold").exists() is held
    assert not (bridge_token(tmp_path / "off", "end-hold", False) / "end.hold").exists()


def test_a_stopped_count_holds_the_results_until_the_button(tmp_path):
    """עצירה: הרשומה עוברת ל-held=1, והכיבוי — בכפתור. **#2024 (נדב 22/10)
    החליף את התקרה של END_HOLD_S (300 ש')**: עצירה היא ההחזקה בלי תקרה, כמו
    יעד עם בעיה. עדיין יש פעולה על המסך — הכפתור (ברזל 17/10, 09:19Z: 27
    דקות על מסך "נכשל" בלי כפתור)."""
    out, run = countdown(tmp_path, (
        'sleep() { n=$((${n:-0} + 1)); [ "$n" != 2 ] || : > "$RUN_DIR/end.hold"; '
        '[ "$n" != 4 ] || cp "$RUN_DIR/end.poweroff" "$RUN_DIR/seen"; '
        '[ "$n" != 400 ] || : > "$RUN_DIR/end.now"; echo SLEEP >> "$RUN_DIR/trace"; }; '
        'end_countdown drawers "drawers complete (2 of 2)" 2 2 sdb; echo "rc=$?"'))
    trace = trace_of(run)
    # 2 seconds of the count, then held past the old 300 s ceiling -- off only on the button
    assert trace.count("SLEEP") == 400 and trace[-1] == "POWEROFF", trace
    at, kind, done, total, held = (run / "seen").read_text().strip().split("|")
    assert (at, kind, done, total, held) == ("0", "drawers", "2", "2", "1")
    assert "the count was stopped" in (run / "agent.log").read_text(encoding="utf-8")


def watch(tmp_path: Path, room: object, *, fetch_ok: bool = True, seen_ago: int = 0,
          frozen: bool = False) -> tuple[subprocess.CompletedProcess, Path]:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    now = int(time.time())
    (run / "room.watch").write_text(f"room_ab12cd34|{now - seen_ago}\n", newline="\n")
    (run / "room.src.json").write_text(json.dumps(room), encoding="utf-8")
    get = ('gui_get() { cp "$RUN_DIR/room.src.json" "$2"; }; ' if fetch_ok
           else 'gui_get() { return 1; }; ')
    # ‏frozen: ‏`date +%s` של הסוכן מחזיר את השנייה שנכתבה — גבול של שנייה אחת
    # ‏(599 מול 600) אינו תלוי בכמה זמן עלה ה-shell (Testrunner תחת עומס, 21/10).
    if frozen:
        get += f"date() {{ echo {now}; }}; "
    out = bash(f"export RUN_DIR={q(run)} GUI_DIR={q(run / 'gui')} LOG_FILE={q(run / 'agent.log')}; "
               f". {posix(AGENT)}/lib/core/common.sh; "
               f". {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/gui/roomflow.sh; "
               + get + "room_end_watch; echo rc=$?")
    return out, run


@with_jq
@pytest.mark.parametrize("room, watched, ended, rc", [
    ({"round": {"id": "room_ab12cd34", "state": "active"}}, True, False, 0),   # still running
    ({"round": None}, False, True, 0),                                            # finished: closed
    ({"round": {"id": "room_ffff0000", "state": "active"}}, False, True, 0),    # ours is gone
    ({"round": {"id": "room_ab12cd34", "state": "failed"}}, False, False, 0),   # failed: not "finished"
    ({"round": {"id": "room_ab12cd34", "state": "closing"}}, False, False, 0),  # a person closes it
    ({"machines": []}, True, False, 1),                                           # no round key: unread
])
def test_the_round_watch_hands_over_only_a_round_that_ended(tmp_path, room, watched, ended, rc):
    out, run = watch(tmp_path, room)
    assert out.stdout.strip() == f"rc={rc}", out.stdout + out.stderr
    assert (run / "room.watch").exists() is watched
    assert (run / "room.ended").exists() is ended


@with_jq
def test_a_room_that_could_not_be_read_is_not_an_end(tmp_path):
    out, run = watch(tmp_path, {"round": None}, fetch_ok=False, seen_ago=599, frozen=True)
    assert out.stdout.strip() == "rc=1"
    assert (run / "room.watch").exists() and not (run / "room.ended").exists()


@with_jq
@pytest.mark.parametrize("fetch_ok, room", [(False, {"round": None}), (True, {"machines": []})],
                         ids=["no-answer", "unreadable"])
def test_a_watch_not_confirmed_for_ten_minutes_is_dropped_aloud(tmp_path, fetch_ok, room):
    """**סקירת אבטחה 17/10 (LOW):** טוקן שפג או שרת שלא עונה השאירו את
    ‏room.watch לנצח — ומחשב הבנייה לא נכבה מ-idle לעולם. 600 ש׳ בלי "active"
    — הצפייה נמחקת, בשורה ביומן, ושעון ה-idle חוזר לספור. בלי שאלה."""
    out, run = watch(tmp_path, room, fetch_ok=fetch_ok, seen_ago=601)
    assert out.stdout.strip().splitlines()[-1] == "rc=1", out.stdout + out.stderr
    assert not (run / "room.watch").exists() and not (run / "room.ended").exists()
    assert "not confirmed for 600s" in (run / "agent.log").read_text(encoding="utf-8")


@with_jq
def test_an_active_answer_renews_the_watch(tmp_path):
    out, run = watch(tmp_path, {"round": {"id": "room_ab12cd34", "state": "active"}}, seen_ago=590)
    rid, seen = (run / "room.watch").read_text().strip().split("|")
    assert rid == "room_ab12cd34" and int(seen) >= int(time.time()) - 5, (rid, seen)


@with_jq
def test_opening_a_round_here_starts_the_watch_and_closing_it_here_ends_it(tmp_path):
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    stubs = ('console_post() { printf \'%s\' "$RESP_BODY" > "$3"; echo 200; }; '
             'console_say() { :; }; gui_role() { return 0; }; ')
    pre = (f"export RUN_DIR={q(run)} GUI_DIR={q(run / 'gui')} LOG_FILE={q(run / 'agent.log')}; "
           f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
           f". {posix(AGENT)}/lib/gui/roomflow.sh; . {posix(AGENT)}/lib/gui/guibridge.sh; " + stubs)
    out = bash(pre + "RESP_BODY='{\"id\":\"room_ab12cd34\",\"wave_session_id\":\"s\"}' room_open img_1 2")
    rid, seen = (run / "room.watch").read_text().strip().split("|")
    assert rid == "room_ab12cd34" and abs(int(seen) - time.time()) < 30, out.stderr
    out = bash(pre + "RESP_BODY='{\"ok\":true}'; token=room-close; confirm=x; gui_dispatch; echo rc=$?")
    assert out.stdout.strip() == "rc=0", out.stdout + out.stderr
    assert not (run / "room.watch").exists(), "נסגר כאן — אין מה לשאול"
    # an answer with no usable id: opened, said so, nothing watched
    out = bash(pre + "RESP_BODY='{\"id\":\"x;rm\"}' room_open img_1 2")
    assert not (run / "room.watch").exists()
    assert "its end will not be asked" in (run / "agent.log").read_text(encoding="utf-8")


# --- the call sites --------------------------------------------------------------

DIRECT_ENV = "IMAGESRV_TEST=0 D_ROLE=build AFTER_TASK_FILE=/nonexistent/after-task"
STANDBY = '_build_standby=1; do_task; _r=$?; echo "STANDBY $_build_standby rc=$_r"'


@native_tools
def test_a_direct_send_asks_and_menu_keeps_the_machine_on(tmp_path, reports):
    """**לב ההכרעה לשליחה ישירה.** עד 17/10: ‏`sleep 20` וכיבוי בלי שאלה."""
    result = run_path(tmp_path, url_of(reports), STANDBY, answer=DIRECT_ANSWER,
                      stubs=DIRECT_STUBS + END_STUBS, env=DIRECT_ENV + " CE_ANSWER=menu")
    trace = result["trace"]
    assert asked_dialog(trace, "send-end"), trace
    assert "ASKED ההפצה הישירה הושלמה: לכבות או לחזור לתפריט" in trace
    assert "POWEROFF" not in trace and "SLEEP 20" not in trace, trace
    assert "STANDBY 0 rc=0" in result["out"], result["out"]
    assert [r["state"] for r in reports.received] == ["done"]


@native_tools
def test_a_direct_send_with_no_answer_powers_off_without_waiting_twice(tmp_path, reports):
    result = run_path(tmp_path, url_of(reports), STANDBY, answer=DIRECT_ANSWER,
                      stubs=DIRECT_STUBS + END_STUBS, env=DIRECT_ENV)
    trace = result["trace"]
    assert asked_dialog(trace, "send-end") and "POWEROFF" in trace, trace
    assert "SLEEP 20" not in trace, "עוד 20 שניות אחרי 20 השניות של השאלה"


def build_menu(tmp_path: Path, ended: bool, answer: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    run = tmp_path / "run"
    run.mkdir(parents=True)
    if ended:
        (run / "room.ended").write_text("room_ab12cd34\n", newline="\n")
    body = cut_function("build_console_screen")
    script = (f"export RUN_DIR={q(run)} LOG_FILE={q(run / 'agent.log')} IMAGESRV_TEST=0 "
              f"CE_ANSWER={answer!r}; . {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/task.sh; "
              + END_STUBS + 'gui_parent() { return 0; }; shrink_offer_pending() { :; }; '
              + body + '\n_build_standby=1; build_console_screen; echo "STANDBY $_build_standby"')
    return bash(script), run


def test_the_end_of_a_round_opened_here_is_asked_from_the_build_menu(tmp_path):
    out, run = build_menu(tmp_path, ended=True, answer="menu")
    trace = trace_of(run)
    assert asked_dialog(trace, "round-end"), (trace, out.stderr)
    assert "ASKED הסבב הסתיים: לכבות או לחזור לתפריט" in trace
    assert "POWEROFF" not in trace and "STANDBY 0" in out.stdout
    assert not (run / "room.ended").exists(), "נשאל פעם אחת"


def test_no_round_end_no_question_and_no_answer_powers_off(tmp_path):
    out, run = build_menu(tmp_path / "none", ended=False)
    assert trace_of(run) == [], out.stderr
    out, run = build_menu(tmp_path / "quiet", ended=True)
    assert asked_dialog(trace_of(run), "round-end") and "POWEROFF" in trace_of(run)


COUNT_STUB = ('end_countdown() { echo "COUNTDOWN $1 $3/$4" >> "$RUN_DIR/trace"; exit 0; }; '
              'finish_and_stop() { echo "POWEROFF-AT-ONCE" >> "$RUN_DIR/trace"; exit 0; }; ')


@native_tools
def test_a_cloner_round_counts_down_instead_of_powering_off_at_once(tmp_path, reports):
    """סבב מהשרת — וגם "שחזור אימג׳ מהשרת" (מקש 3), שהוא סבב של מכונה אחת
    ועובר באותו `do_restore_drawers` (lcpull.sh). עד 17/10: כיבוי מיד."""
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                      stubs=COUNT_STUB, env="IMAGESRV_TEST=0 D_ROLE=cloner")
    trace = result["trace"]
    assert "COUNTDOWN drawers 2/2" in trace and "POWEROFF-AT-ONCE" not in trace, trace
    assert [r["state"] for r in reports.received] == ["done"]


HOLD_STUB = 'ui_error_hold() { echo "HOLD $1" >> "$RUN_DIR/trace"; }; '


@native_tools
@pytest.mark.parametrize("stays_on", [False, True])
def test_a_failed_cloner_round_is_reported_and_counts_down_too(tmp_path, reports, stays_on):
    """**ברזל 17/10.** משכפל 2 נשאר דלוק אחרי "שחזור מהשרת" שנכשל, והסבב
    הבא נפתח בלעדיו: פעימת ההמתנה אינה מצטרפת לגל, ו-WoL לא מעיר מכונה
    דלוקה. עכשיו: ‏'failed' מדווח (הקונסולה שומרת את הסיבה), ספירה, כיבוי.
    כיבוי שסורב (WoL) — מסך הכישלון כמו קודם."""
    count = ('end_countdown() { echo "COUNTDOWN $1" >> "$RUN_DIR/trace"; return 1; }; '
             if stays_on else COUNT_STUB)
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                      restore_rc=1, stubs=count + HOLD_STUB, env="IMAGESRV_TEST=0 D_ROLE=cloner")
    trace = result["trace"]
    assert "COUNTDOWN failed 0/2" in trace or (stays_on and "COUNTDOWN failed" in trace), trace
    assert [r["state"] for r in reports.received][-1:] == ["failed"], reports.received
    assert ("HOLD no drawer completed" in trace) is stays_on, trace


@native_tools
def test_a_round_where_every_drawer_was_skipped_counts_down_too(tmp_path, reports):
    """**ברזל 17/10, 09:19Z:** משכפל 2 אחרי שליחה ישירה שנכשלה (‏sha256 של
    מחיצה 1) הציג 27 דקות את מסך הסבב — "נכשל", ‏0%, "אין פעולה במסך הזה" —
    בלי כפתור ובלי כיבוי. שליחה ישירה מגיעה למשכפל כסבב, באותו
    `do_restore_drawers`: כשל כתיבה הוא המקרה שלמעלה; וכאן הכשל האחר של
    אותו מסלול — כל המגירות דולגו (שער SMART). עד 17/10: ‏ui_error_hold."""
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                      stubs=COUNT_STUB + HOLD_STUB + 'smart_gate() { return 1; }; ',
                      env="IMAGESRV_TEST=0 D_ROLE=cloner")
    trace = result["trace"]
    # #2024: the skipped drawers are the end's targets too (_all) -- "0 of 2", each with its reason
    assert "COUNTDOWN failed 0/2" in trace and "HOLD all drawers skipped" not in trace, trace
    assert reports.received, "the failure is reported before the count"


@native_tools
def test_a_round_where_some_drawers_failed_shows_the_results_and_counts(tmp_path, reports):
    """**הכרעת נדב 17/10 (3).** סבב / שחזור מהשרת שבו חלק מהמגירות נכשלו:
    ‏'partial' עם "1 מתוך 2" — מסך תוצאות שאפשר לעצור עליו את הספירה — ואז
    כיבוי. עד 17/10: כמו הצלחה מלאה, בלי לומר מה נכשל."""
    partial = ('run_restore_drawers() { echo "DRAWERS" >> "$RUN_DIR/trace"; target_set sdb done; '
               'target_set sdc failed; echo done > "$RUN_DIR/state"; return 0; }; ')
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                      stubs=COUNT_STUB + partial, env="IMAGESRV_TEST=0 D_ROLE=cloner")
    assert "COUNTDOWN partial 1/2" in result["trace"], result["trace"]


def local_clone(tmp_path: Path, target_state: str, phase: str = "done") -> list[str]:
    run = tmp_path / "run"
    (run / "lc").mkdir(parents=True)
    stubs = (
        'lc_gui_alive() { return 0; }; lc_power_check() { :; }; attended_start() { :; }; '
        'lc_measure() { :; }; '
        'lc_inventory() { [ -f "$RUN_DIR/once" ] || { : > "$RUN_DIR/once"; echo "sda|sdb|0" > "$LC/start.req"; }; }; '
        'lc_run() { mkdir -p "$LC/run" "$RUN_DIR/targets/sdb"; echo sdb > "$LC/run/targets"; '
        f'echo {target_state} > "$RUN_DIR/targets/sdb/state"; echo {phase} > "$LC/phase"; }}; '
        'sleep() { n=$((${n:-0} + 1)); [ "$n" -lt 4 ] || { echo NOCOUNT >> "$RUN_DIR/trace"; exit 0; }; }; '
        'end_countdown() { echo "COUNTDOWN $1" >> "$RUN_DIR/trace"; exit 0; }; '
    )
    out = bash(f"export RUN_DIR={q(run)} LC={q(run / 'lc')} LOG_FILE={q(run / 'agent.log')}; "
               f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/idle.sh; . {posix(AGENT)}/lib/localclone/lcwait.sh; "
               f". {posix(AGENT)}/lib/localclone/localclone.sh; " + stubs + "lc_session")
    assert out.returncode == 0, out.stderr
    return trace_of(run)


def test_a_local_clone_that_wrote_every_disk_counts_down_to_power_off(tmp_path):
    """עד 17/10: מסך התוצאה חיכה לכפתור עד 600 ש׳, ואז "מהשרת" — דלוק."""
    assert local_clone(tmp_path, "done") == ["COUNTDOWN localclone"]


def test_a_local_clone_with_a_failed_disk_counts_down_too(tmp_path):
    """**נדב 17/10: "גם כשנכשלו".** הכישלון על הספירה ("השיכפול נכשל"), והסיבה
    בדיווח (lc_report). עד 17/10: מסך התוצאה חיכה לכפתור, והמשכפל נשאר דלוק."""
    assert local_clone(tmp_path, "failed") == ["COUNTDOWN failed"]


def test_a_local_clone_stopped_by_hand_goes_back_to_choosing_without_a_count(tmp_path):
    """"עצור" הוא אדם ליד המכונה שבחר — חוזרים לבחירה (המוקאפ), בלי ספירה."""
    assert local_clone(tmp_path, "failed", phase="stopped") == ["NOCOUNT"]


# --- a cloner on a menu joins a round opened for it (metal 17/10) -----------------

LC_WORLD = (
    'lc_gui_alive() { return 0; }; lc_power_check() { :; }; lc_measure() { :; }; '
    'lc_pull_images() { :; }; lc_pull_nonce() { :; }; '
    # the beat's answer: what attended_keep writes from .session.state
    'attended_start() { printf "%s\\n" "$ROUND" > "$RUN_DIR/attended.round"; }; attended_stop() { :; }; '
    'sleep() { n=$((${n:-0} + 1)); [ "$n" -lt 4 ] || { echo "STAYED" >> "$RUN_DIR/trace"; exit 0; }; }; '
)


def cloner_menu(tmp_path: Path, round_: str, call: str, extra: str = "") -> tuple[list[str], str]:
    run = tmp_path / "run"
    (run / "lc").mkdir(parents=True)
    out = bash(f"export RUN_DIR={q(run)} LC={q(run / 'lc')} ROUND={round_} LC_BOOT_S=60; "
               f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/localclone/lcwait.sh; "
               f". {posix(AGENT)}/lib/localclone/lcgui.sh; . {posix(AGENT)}/lib/localclone/localclone.sh; "
               f". {posix(AGENT)}/lib/localclone/lcpull.sh; " + LC_WORLD + extra + call)
    assert out.returncode == 0, out.stderr
    log = run / "agent.log"
    return trace_of(run), log.read_text(encoding="utf-8") if log.exists() else ""


@pytest.mark.parametrize("round_", ["open", "running"])
def test_the_boot_menu_leaves_for_a_round_opened_for_this_machine(tmp_path, round_):
    """המסך הראשי (מהשרת / שיכפול מקומי / שחזור), בלי ספירה (נגעו בו): סבב
    שנפתח עבור המכונה — יוצאים ממנו, והלולאה הראשית מצטרפת ב-hello."""
    trace, log = cloner_menu(tmp_path, round_, (
        'echo boot > "$LC/screen"; LC_PAUSED=1; lc_boot_choice; echo "LEFT $LC_CHOICE" >> "$RUN_DIR/trace"'))
    assert trace == ["LEFT server"], trace
    assert "a round is open for this machine" in log


def test_the_whole_offer_goes_on_from_the_server_when_a_round_calls(tmp_path):
    trace, log = cloner_menu(tmp_path, "open", (
        'echo boot > "$LC/screen"; local_clone_offer; echo OFFER-DONE >> "$RUN_DIR/trace"'))
    assert trace == ["OFFER-DONE"], trace
    assert "going on from the server" in log


def test_no_round_keeps_the_menu(tmp_path):
    trace, _ = cloner_menu(tmp_path, "none", 'echo boot > "$LC/screen"; LC_PAUSED=1; lc_boot_choice')
    assert trace == ["STAYED"], trace


def test_the_idle_local_screen_and_the_restore_screen_leave_for_the_round(tmp_path):
    trace, _ = cloner_menu(tmp_path / "local", "open",
                           'lc_inventory() { :; }; lc_session; echo "END $LC_END" >> "$RUN_DIR/trace"')
    assert trace == ["END round"], trace
    trace, _ = cloner_menu(tmp_path / "pull", "open",
                           'lc_pull_session; echo "END $LC_END" >> "$RUN_DIR/trace"')
    assert trace == ["END round"], trace


def test_local_work_on_the_screen_is_not_left_for_the_round(tmp_path):
    """באמצע פעולה מקומית (מדידה/שיכפול/משיכה רצים) אין הצטרפות: הפעולה
    חוסמת את הלולאה, ואחריה — שלב שאינו idle (כאן: copying)."""
    trace, _ = cloner_menu(tmp_path, "open",
                           'lc_inventory() { echo copying > "$LC/phase"; }; lc_session')
    assert trace == ["STAYED"], trace


# --- no idle power-off during a round (metal 17/10, 08:15 and 08:30 UTC) ---------

def idle(tmp_path: Path, setup: dict[str, tuple[str, int]]) -> list[str]:
    """``idle_check`` אחרי 400 ש׳ בלי מקש (התקרה 300). ‏setup: קובץ -> (תוכן,
    גיל בשניות) — הגיל קובע את ה-mtime, שהוא מה ש-``find -newer`` קורא."""
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    now = int(time.time())
    files = {"idle.since": (f"{now - 400}\n", 400), **setup}
    for name, (text, age) in files.items():
        f = run / name
        f.write_text(text, encoding="utf-8", newline="\n")
        os.utime(f, (now - age, now - age))
    out = bash(f"export RUN_DIR={q(run)} GUI_DIR={q(run / 'gui')} LOG_FILE={q(run / 'agent.log')} "
               f"IDLE_POWEROFF_S=300 IDLE_WARN_S=3 D_SESSION_STATE=none D_TASK=null; "
               f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/idle.sh; "
               'power_check() { return 0; }; sleep() { :; }; '
               'safe_poweroff() { echo "POWEROFF" >> "$RUN_DIR/trace"; return 0; }; '
               "idle_check; echo rc=$?")
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    return trace_of(run)


ACTIVE_ROOM = json.dumps({"round": {"id": "room_ab12cd34", "state": "active"}, "machines": []})


@with_jq
@pytest.mark.parametrize("setup", [
    # a round opened from this GUI, still active (roomflow.sh room.watch)
    {"room.watch": ("room_ab12cd34|1760000000\n", 400)},
    # the room screen draws an active round (opened from the console): 91%, the wave sending
    {"gui/mode": ("room\n", 400), "gui/room.json": (ACTIVE_ROOM, 1)},
    {"gui/mode": ("direct\n", 400), "gui/room.json": (ACTIVE_ROOM, 1)},
    # the classes screen draws a class session that is running
    {"gui/mode": ("classes\n", 400),
     "gui/session-shown.json": (json.dumps({"session": {"state": "running"}}), 1)},
], ids=["opened-here", "room-shown", "direct-shown", "class-shown"])
def test_no_idle_power_off_while_a_round_is_active(tmp_path, setup):
    """**הבאג.** מחשב הבנייה פתח סבב, הציג 91% וגל משדר — ונכבה: ‏"אין
    פעילות" ספר רק מקלדת ועכבר. סבב פעיל בלי קלט — אין כיבוי, והשעון מתאפס."""
    assert idle(tmp_path, setup) == []


@with_jq
@pytest.mark.parametrize("setup", [
    {},                                                                     # nothing: as today
    {"gui/mode": ("menu\n", 400), "gui/room.json": (ACTIVE_ROOM, 1)},      # not on the room screen
    {"gui/mode": ("room\n", 400), "gui/room.json": (json.dumps({"round": None, "machines": []}), 1)},
    {"gui/mode": ("room\n", 400),
     "gui/room.json": (json.dumps({"round": {"id": "r", "state": "failed"}}), 1)},   # failed: not active
    {"gui/mode": ("room\n", 400), "gui/room.json": (ACTIVE_ROOM, 500)},    # the kiosk stopped reading
    {"gui/mode": ("room\n", 400), "gui/room.json": ("{not json", 1)},       # unread is no round
], ids=["none", "menu", "no-round", "failed-round", "stale", "unreadable"])
def test_without_an_active_round_the_idle_power_off_stays_as_it_was(tmp_path, setup):
    assert idle(tmp_path, setup) == ["POWEROFF"]


# --- the native GUI (renders on the Testrunner; cc + pango/cairo there) -----------

from test_cloner_gui import _build_gui, _count_color, _png_rgb, _token  # noqa: E402
from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render  # noqa: E402


@GUI_TOOLS
def test_the_count_is_drawn_over_the_screen_in_the_dialog_style(tmp_path):
    binary = _build_gui(tmp_path)
    run = _render(binary, tmp_path / "e", BASE_STATE + f"end_poweroff={int(time.time()) + 20}|drawers|||0\n")
    assert "end_poweroff malformed" not in run.stderr
    _w, _h, ch, plain = _png_rgb(tmp_path / "e" / "card-menu-light.png")
    _w, _h, ch2, counted = _png_rgb(tmp_path / "e" / "card-end-countdown-light.png")
    warn = _token("--clr-warning")
    assert _count_color(counted, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    assert (tmp_path / "e" / "card-end-countdown-dark.png").exists()


@GUI_TOOLS
@pytest.mark.parametrize("kind", ["send-end", "round-end"])
def test_the_new_end_questions_are_drawn(tmp_path, kind):
    binary = _build_gui(tmp_path)
    run = _render(binary, tmp_path / kind, BASE_STATE + f"ask_prompt=7-0|{kind}|0|{int(time.time()) + 300}\n")
    assert "question incomplete or malformed" not in run.stderr, run.stderr
    _w, _h, ch, plain = _png_rgb(tmp_path / kind / "card-menu-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / kind / f"card-{kind}-light.png")
    warn = _token("--clr-warning")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500


@GUI_TOOLS
def test_a_broken_count_or_question_is_refused_aloud(tmp_path):
    binary = _build_gui(tmp_path)
    for name, text in (("kind", "end_poweroff=1760000000|reboot|||0\n"),
                       ("restore", "end_poweroff=1760000000|restore|||0\n"),   # not this branch (#1561)
                       ("epoch", "end_poweroff=soon|drawers|||0\n"),
                       ("two", "end_poweroff=1760000000|drawers\n"),            # the old two fields
                       ("count", "end_poweroff=1760000000|partial|3|2|0\n"),    # done > targets
                       ("held", "end_poweroff=1760000000|partial|1|2|2\n"),
                       ("extra", "end_poweroff=1760000000|drawers|||0|x\n")):
        run = _render(binary, tmp_path / name, BASE_STATE + text)
        assert "end_poweroff malformed" in run.stderr, name
    run = _render(binary, tmp_path / "rows", BASE_STATE + "ask_prompt=7-0|send-end|1|20\nask_row=3|1|2|table\n")
    assert "question incomplete or malformed" in run.stderr


# --- every count on the screen counts: 20, 19, 18 (metal 17/10) -------------------

FB_W, FB_H, FB_HEADER = 1280, 800, 4096


def dialog_frames(binary: Path, tmp_path: Path, state_text: str, seconds: float = 4.6) -> int:
    """הגואי האמיתי ב-``--backend mem``, קובץ מצב **שאינו משתנה**: כמה
    תמונות שונות של אזור הדיאלוג (מרכז המסך) נראו ב-``seconds`` שניות.
    מספר שמתעדכן כל שנייה = 4–5; כל 2 ש׳ (הלולאה עד 17/10) = לכל היותר 3;
    טקסט קבוע = 1."""
    import hashlib
    import struct
    state = tmp_path / "state.txt"
    state.write_text(state_text, newline="\n")
    fb = tmp_path / "fb.mem"
    env = dict(os.environ)
    gui_dir = REPO / "native-gui"
    if (gui_dir / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(gui_dir / "fonts.conf")
    seen: set[str] = set()
    with (tmp_path / "gui.err").open("wb") as errf:
        proc = subprocess.Popen(
            [str(binary), "--backend", "mem", "--fb-file", str(fb), "--no-input",
             "--state", str(state), "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
            stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL, env=env)
        try:
            ready = time.monotonic() + 20
            while time.monotonic() < ready and not (fb.exists() and fb.stat().st_size > FB_HEADER):
                time.sleep(0.1)
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                data = fb.read_bytes()
                seq = struct.unpack("<I", data[24:28])[0]
                if seq % 2 == 0 and len(data) >= FB_HEADER + FB_W * FB_H * 4:
                    rows = b"".join(data[FB_HEADER + y * FB_W * 4 + 200 * 4: FB_HEADER + y * FB_W * 4 + 1080 * 4]
                                    for y in range(150, 760))
                    seen.add(hashlib.sha256(rows).hexdigest())
                time.sleep(0.15)
            assert proc.poll() is None, (tmp_path / "gui.err").read_text(errors="replace")
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    return len(seen)


@GUI_TOOLS
@pytest.mark.parametrize("record", [
    "ask_prompt=7-0|capture-end|0|{at20}",    # the end of a capture: 20, 19, 18
    "ask_prompt=7-0|send-end|0|{at20}",
    "end_poweroff={at20}|drawers|||0",         # a cloner
    "end_poweroff={at20}|partial|1|2|0",       # some drawers failed: the dialog with two buttons
    "end_poweroff={at300}|partial|1|2|1",      # the count stopped: the bar over the results
    "idle_poweroff_at={at20}",                 # the idle warning (#434)
], ids=["capture-end", "send-end", "cloner-end", "partial", "held", "idle"])
def test_every_count_on_the_screen_goes_down_each_second(tmp_path, record):
    """**ברזל 17/10, 09:06Z:** אחרי קליטה הוצג "המחשב יכבה בעוד 20 שניות" —
    20 כטקסט קבוע — והמחשב נכבה לבד. עכשיו הנושא הוא רגע הכיבוי, והמספר
    יורד בכל שנייה; הלולאה מתעוררת בכל שנייה כל עוד ספירה על המסך."""
    binary = _build_gui(tmp_path)
    now = int(time.time())
    text = record.format(at300=now + 300, at20=now + 20)
    # no updated=: its "N s ago" would change the screen by itself
    frames = dialog_frames(binary, tmp_path, "task=none\n" + text + "\n")
    assert frames >= 4, f"{frames} distinct frames of the dialog in 4.6 s"


@GUI_TOOLS
def test_partial_results_are_said_and_the_held_count_is_a_bar_over_the_screen(tmp_path):
    """הדיאלוג של 'partial' שונה מזה של הצלחה (שני כפתורים, "1 מתוך 2"); ואחרי
    עצירה — אין שכבה כהה על המסך: התוצאות שמאחור נראות, והפס עם הכפתור."""
    binary = _build_gui(tmp_path)
    now = int(time.time())
    shots = {}
    for name, rec in (("ok", f"{now + 20}|drawers|2|2|0"), ("part", f"{now + 20}|partial|1|2|0"),
                      ("held", f"{now + 300}|partial|1|2|1")):
        run = _render(binary, tmp_path / name, BASE_STATE + f"end_poweroff={rec}\n")
        assert "end_poweroff malformed" not in run.stderr, (name, run.stderr)
        shots[name] = _png_rgb(tmp_path / name / "card-end-countdown-light.png")
    _w, _h, ch, plain = _png_rgb(tmp_path / "ok" / "card-menu-light.png")
    assert shots["ok"][3] != shots["part"][3]
    # the dim layer: a held count leaves the screen behind it as it was, but for the bar
    dark = lambda px, c: sum(1 for i in range(0, len(px), c * 97) if sum(px[i:i + 3]) < 120)  # noqa: E731
    assert dark(shots["held"][3], shots["held"][2]) < dark(shots["part"][3], shots["part"][2]) / 2


@native_tools
def test_a_failed_cloner_round_the_server_did_not_confirm_still_counts_down(tmp_path, reports):
    """**ברזל 17/10, 18:05–18:12Z (`room_72308383`).** הרשת של משכפל 2 ירדה
    באמצע הזרם; שתי המגירות נכשלו בגלוי, הדיווח הסופי לא אושר (השרת לא
    נגיש) — והמשכפל נתקע על "המשימה הסתיימה — והשרת לא אישר" בלי ספירה:
    ‏`hold_unheard` אינו חוזר לעולם, גם אחרי שהשרת ענה שוב ("session closed").
    הכרעת נדב: משכפל נכבה אחרי 20 ש' **בכל מצב**, כולל כשל — בספירה גלויה.
    הדיווח ממשיך ברקע עד הכיבוי; השרת סוגר את הסבב בעצמו (גל מת → `failed`)."""
    reports.refuse_first = -1
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                      restore_rc=1, stubs=COUNT_STUB + HOLD_STUB,
                      env="IMAGESRV_TEST=0 D_ROLE=cloner")
    trace = result["trace"]
    assert "COUNTDOWN failed 0/2" in trace, trace
    assert not [t for t in trace if t.startswith("HOLD")], trace
    assert reports.received and reports.received[-1]["state"] == "failed", reports.received
    assert f"LOOP {SESSION}" in trace, "הדיווח לא הורם מחדש לזמן הספירה"
