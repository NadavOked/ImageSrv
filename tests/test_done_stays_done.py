"""קליטה שהצליחה נשארת `done` גם כשה-WoL לא נדרך (#1222).

אחרי דיווח ה-`done` הסוכן קורא ל-`finish_and_stop`. כשאף כרטיס רשת לא
נדרך ל-Wake-on-LAN, הפונקציה **חוזרת** — במכוון: מכונה שאי אפשר להעיר
נשארת דולקת (#587). הקוד שלפני התיקון המשיך משם ישר לנתיב הכישלון:
‏`manifest upload failed`, ‏`failed > state`, ודיווח שני — השרת קיבל
‏`['done', 'failed']`, והמסך אמר "capture did not complete" על אימג' שכבר
בספרייה. עיקרון 5 מהצד ההפוך: הצלחה שמדווחת ככישלון.

מה נבדק, במסלולים שבנויים כך — קליטה (`do_task`), הפצה ישירה
(`direct_send_task`, ‏#715), חדר השיכפולים (`do_restore_drawers`: סבב
שהושלם, ו"החלף דיסק" בשער ה-SMART, ‏#1222), ותחנת הכיתה (`do_restore`:
"החלף דיסק" בשער ה-SMART כש-`after-task=poweroff` מפורש, ‏#1224):
‏`finish_and_stop` **האמיתי** רץ (‏`D_ROLE=build`/`cloner`/`classroom` →
ענף הכיבוי), ‏`arm_wol` מזויף ככישלון, ו-`poweroff` מזויף כ-`exit 0`
כדי שכיבוי שלא היה צריך לקרות ייראה בעקבה. השרת האמיתי בתהליכון סופר
את הדיווחים.

‏`IMAGECTL_TEST=0`: ה-`exit 0` של מצב הטסט יושב לפני `finish_and_stop`,
ובלי לעבור אותו אין מה לבדוק. ‏stdin הוא ‏`DEVNULL` — אין מסך, ושאלת סוף
הקליטה (#409) נגמרת ב"אין תשובה" = כיבוי, כלומר בדיוק ב-`finish_and_stop`.
"""

from __future__ import annotations

from pathlib import Path

from test_agent import AGENT, posix
from test_final_report import (  # noqa: F401 — ‏`reports` הוא fixture
    IMAGE,
    PRESS_OFF,
    SESSION,
    native_tools,
    reports,
    run_path,
    url_of,
)
from test_hold_and_capture import CAPTURE_STUBS, TASK, TASK_ANSWER

#: ‏`ui_error_hold` מזויף: האמיתית, מחוץ למצב טסט, נכנסת ל-`hold_watch`
#: ולא חוזרת — בבקרה השלילית זה היה נראה כתלייה ולא ככישלון בשמו.
WOL_UNARMED = (
    'arm_wol() { echo "WOL-FAILED" >> "$RUN_DIR/trace"; return 1; }; '
    'poweroff() { echo "POWEROFF" >> "$RUN_DIR/trace"; exit 0; }; '
    'reboot() { echo "REBOOT" >> "$RUN_DIR/trace"; exit 0; }; '
    'sleep() { :; }; '
    'shrink_gui_release() { :; }; '
    'ui_error_hold() { echo "HOLD $1" >> "$RUN_DIR/trace"; echo "  FAILED: $1"; }; '
)

ENV = "IMAGECTL_TEST=0 D_ROLE=build AFTER_TASK_FILE=/nonexistent/after-task"

#: ‏`_build_standby=0` — מצב התפריט; אחרי התיקון המסך נשאר (‏=1) ולא נמחק.
CALL = '_build_standby=0; do_task; echo "RETURNED rc=$? standby=$_build_standby"'

DIRECT_ANSWER = {
    "schema": 1, "known": True, "role": "build",
    "task": {"id": TASK, "type": "direct_send", "disk": "sda",
             "token": "t" * 48},
}

#: ההפצה עצמה אינה מה שנבדק כאן (‏test_directsend.py בודק אותה) — היא
#: מזויפת כהצלחה, ומה שנבדק הוא מה שקורה אחריה.
DIRECT_STUBS = (
    f". {posix(AGENT)}/lib/directsend.sh; "
    'direct_send_run() { echo "SENT $1" >> "$RUN_DIR/trace"; return 0; }; '
)


def assert_done_stays_done(result: dict, reports) -> None:
    trace = result["trace"]
    # ‏finish_and_stop האמיתי הגיע לענף הכיבוי וה-WoL לא נדרך — אחרת
    # הבדיקה לא בדקה את מה שהיא אומרת שהיא בודקת.
    assert "WOL-FAILED" in trace, f"‏arm_wol לא נקרא: {trace}"
    assert "POWEROFF" not in trace, "המכונה כבתה בלי WoL דרוך (#587)"
    states = [r["state"] for r in reports.received]
    assert states == ["done"], f"הדיווחים לשרת: {states}"
    assert not any(t.startswith("HOLD ") for t in trace), f"מסך כישלון: {trace}"
    assert "did not complete" not in result["out"]
    assert "Wake-on-LAN could not be armed" in result["out"]
    assert "staying powered on" in result["log"]
    # חוזר ללולאה, והמסך נשאר (‏_build_standby) — לא נמחק בתפריט.
    assert "RETURNED rc=0 standby=1" in result["out"], result["out"]


@native_tools
def test_a_capture_that_could_not_arm_wol_stays_done(tmp_path, reports):
    """**לב #1222.** קליטה מוצלחת, אין תשובה בסופה (כיבוי), וה-WoL לא
    נדרך: דיווח אחד — ‏`done` — והמכונה נשארת דולקת עם הסבר."""
    result = run_path(
        tmp_path, url_of(reports), CALL,
        answer=TASK_ANSWER, stubs=CAPTURE_STUBS + WOL_UNARMED, env=ENV,
    )
    assert_done_stays_done(result, reports)


#: #1483: Wake-on-LAN armed, and the power-off came back -- busybox poweroff
#: without -f against a shell PID 1 (HP-BUILD, 16/10). Still done, and said
#: as what it was, not as Wake-on-LAN.
POWEROFF_RETURNS = (
    'arm_wol() { echo "WOL-ARMED" >> "$RUN_DIR/trace"; return 0; }; '
    'poweroff() { echo "POWEROFF $*" >> "$RUN_DIR/trace"; return 0; }; '
    'reboot() { echo "REBOOT" >> "$RUN_DIR/trace"; exit 0; }; '
    'sleep() { :; }; sync() { :; }; '
    'shrink_gui_release() { :; }; '
    'ui_error_hold() { echo "HOLD $1" >> "$RUN_DIR/trace"; echo "  FAILED: $1"; }; '
)


@native_tools
def test_a_capture_whose_poweroff_came_back_stays_done_and_says_why(tmp_path, reports):
    result = run_path(
        tmp_path, url_of(reports), CALL,
        answer=TASK_ANSWER, stubs=CAPTURE_STUBS + POWEROFF_RETURNS, env=ENV,
    )
    assert "POWEROFF -f" in result["trace"], result["trace"]
    assert [r["state"] for r in reports.received] == ["done"]
    assert "wol not armed" not in result["log"], result["log"]
    assert "the power-off did not take" in result["log"], result["log"]
    assert "still on" in result["out"] and "RETURNED rc=0 standby=1" in result["out"], result["out"]


@native_tools
def test_a_direct_send_that_could_not_arm_wol_stays_done(tmp_path, reports):
    """אותה צורה ב-`direct_send_task` (#715): ההפצה הושלמה, ה-WoL לא
    נדרך — ‏`done` בלבד, בלי "direct deployment did not complete"."""
    result = run_path(
        tmp_path, url_of(reports), CALL,
        answer=DIRECT_ANSWER, stubs=DIRECT_STUBS + WOL_UNARMED, env=ENV,
    )
    assert "SENT " + TASK in result["trace"], result["trace"]
    assert_done_stays_done(result, reports)


# --- חדר השיכפולים: ‏`do_restore_drawers` ------------------------------------

ROOM_ENV = "IMAGECTL_TEST=0 D_ROLE=cloner AFTER_TASK_FILE=/nonexistent/after-task"


@native_tools
def test_a_cloning_round_that_could_not_arm_wol_stays_done(tmp_path, reports):
    """המגירות נכתבו והסבב דווח `done`; ה-WoL לא נדרך. הקוד הישן נפל אל
    ‏`ui_error_hold "no drawer completed"`: מסך FAILED אחרי סבב שהצליח."""
    result = run_path(
        tmp_path, url_of(reports),
        f'_build_standby=0; do_restore_drawers {IMAGE} {SESSION}; '
        'echo "RETURNED rc=$? standby=$_build_standby"',
        stubs=WOL_UNARMED + PRESS_OFF, env=ROOM_ENV,
    )
    assert "DRAWERS" in result["trace"], result["trace"]
    assert "PRESSED" in result["trace"], "the round was not held (#2024): " + str(result["trace"])
    assert "no drawer completed" not in result["out"]
    assert_done_stays_done(result, reports)


#: ‏smart_gate מזויף כ"המפעיל בחר החלף" (rc=2) — מה שהאמיתי כותב ל-
#: ‏awaiting_replace ומחזיר. ‏hold_watch האמיתית אינה חוזרת לעולם; המזויפת
#: רושמת עם איזו שורה היא נקראה ויוצאת, כמו שהאמיתית "לא חוזרת".
REPLACE_STUBS = (
    f". {posix(AGENT)}/lib/smartgate.sh; "
    'smart_gate() { shift; mkdir -p "$RUN_DIR/smart"; '
    'echo sdb > "$RUN_DIR/smart/awaiting_replace"; echo "$@"; return 2; }; '
    'hold_watch() { echo "WATCH $1 [$HOLD_PROMPT]" >> "$RUN_DIR/trace"; exit 0; }; '
)


@native_tools
def test_a_replace_that_could_not_arm_wol_holds_without_a_false_reason(
        tmp_path, reports):
    """המפעיל בחר "החלף דיסק" בשער ה-SMART, וה-WoL לא נדרך. הקוד הישן
    המשיך לשורה הבאה והציג "FAILED: all drawers skipped" — סיבה שגויה.
    עכשיו: מסך ההחלפה נשאר, עם הוראה לכבות ביד, והדופק ממשיך (#64). לא
    נכתב בייט, ואין דיווח סיום — הסבב לא הסתיים."""
    result = run_path(
        tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
        stubs=WOL_UNARMED + REPLACE_STUBS, env=ROOM_ENV,
    )
    trace = result["trace"]
    assert "WOL-FAILED" in trace, f"‏arm_wol לא נקרא: {trace}"
    assert "POWEROFF" not in trace
    assert "DRAWERS" not in trace, "נכתב למגירות למרות בחירת 'החלף'"
    assert not any(t.startswith("HOLD ") for t in trace), f"מסך כישלון: {trace}"
    assert "FAILED" not in result["out"]
    assert "Replace the flagged disk" in result["out"]
    assert "power off by hand for the swap" in result["out"]
    assert "WATCH hold_beat [Replace the flagged disk: power off by hand]" in trace
    assert "staying powered on" in result["log"]
    assert reports.received == [], f"דיווח סיום על סבב שלא הסתיים: {reports.received}"


# --- תחנת הכיתה: ‏`do_restore` (#1224) ---------------------------------------
#
# ‏`finish_and_stop` שם: בלי `after-task` מפורש, ‏D_ROLE=classroom/student
# גוזר `reboot` — ומעולם לא חוזר. הבאג הזה קיים רק כשכלי הטכנאי כתב
# ‏`after-task=poweroff` במפורש (המעבדה, לא תחנת כיתה בכיתה עצמה): אז
# ‏finish_and_stop עובר לענף הכיבוי, וה-WoL שלא נדרך גורם לו לחזור.

#: אותו זיוף כמו ‏`REPLACE_STUBS` בחדר השיכפולים, על הדיסק היחיד של
#: התחנה (‏`pick_internal_disk` מזויף כ-`sda` ב-STUBS של test_final_report).
STATION_REPLACE_STUBS = (
    f". {posix(AGENT)}/lib/smartgate.sh; "
    'smart_gate() { shift; mkdir -p "$RUN_DIR/smart"; '
    'echo sda > "$RUN_DIR/smart/awaiting_replace"; echo "$@"; return 2; }; '
    'hold_watch() { echo "WATCH $1 [$HOLD_PROMPT]" >> "$RUN_DIR/trace"; exit 0; }; '
)


@native_tools
def test_a_station_replace_with_after_task_poweroff_and_no_wol_holds_without_a_false_reason(
        tmp_path, reports):
    """תחנת כיתה, `/etc/imagectl/after-task` מכיל `poweroff` במפורש
    (כלי הטכנאי כותב את זה לצי המעבדה), המפעיל בחר "החלף דיסק" בשער
    ה-SMART, וה-WoL לא נדרך. הקוד הישן המשיך לשורה הבאה והציג
    "FAILED: the disk was skipped -- nothing written" — סיבה שגויה
    (הדיסק לא דולג, הוא ממתין להחלפה). עכשיו: מסך ההחלפה נשאר, עם
    הוראה לכבות ביד, והדופק ממשיך (#64). לא נכתב בייט, ואין דיווח
    סיום — הסבב לא הסתיים למכונה הזאת."""
    after_task = tmp_path / "after-task"
    after_task.write_text("poweroff\n", encoding="utf-8")
    env = f"IMAGECTL_TEST=0 D_ROLE=classroom AFTER_TASK_FILE={posix(after_task)}"

    result = run_path(
        tmp_path, url_of(reports), "do_restore",
        stubs=WOL_UNARMED + STATION_REPLACE_STUBS, env=env,
    )
    trace = result["trace"]
    assert "WOL-FAILED" in trace, f"‏arm_wol לא נקרא: {trace}"
    assert "POWEROFF" not in trace
    assert "REBOOT" not in trace, "התחנה אתחלה למרות after-task=poweroff"
    assert "RESTORE" not in trace, "נכתב לדיסק למרות בחירת 'החלף'"
    assert not any(t.startswith("HOLD ") for t in trace), f"מסך כישלון: {trace}"
    assert "FAILED" not in result["out"]
    assert "Replace the flagged disk" in result["out"]
    assert "power off by hand for the swap" in result["out"]
    assert "WATCH hold_beat [Replace the flagged disk: power off by hand]" in trace
    assert "staying powered on" in result["log"]
    assert reports.received == [], f"דיווח סיום על סבב שלא הסתיים: {reports.received}"
