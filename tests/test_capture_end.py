"""אחרי קליטה — "כבה" (ברירת המחדל) או "חזרה לתפריט", בדיאלוג של הגואי (#409).

נדב, 05/09 (#409): "בסוף, כשהוא מסיים, לחזור לתפריט הראשי — או לכבות."
זה נבנה כשאלה על **מסך הטקסט**; ב-11/10 מסכי הטקסט נמחקו, ונדב הכריע
"הכול גואי": השאלה היא דיאלוג קטן בגואי (askgui.sh, ‏kind capture-end).
"כבה" הוא ברירת המחדל: בלי גואי, או בלי תשובה עד `CAPTURE_END_ASK_S` —
כיבוי, ההגנה שהייתה כאן לפני #409 (מי שקלט ויצא מהחדר מוצא מכונה כבויה).

מה נבדק, על הפונקציות האמיתיות מול שרת דיווח אמיתי בתהליכון. הגואי מזויף
בגבול שלו — `gui_ask_choice` מחזיר את התשובה שהוגדרה (`CE_ANSWER`), ריק =
אין תשובה:

* **"חזרה לתפריט"** — ‏`do_task` חוזר ללולאה ו-`_build_standby` מתאפס.
* **"כבה"** — כיבוי מיד, בלי המתנה.
* **בלי תשובה** — כיבוי; ומקש "1" שמחכה ב-stdin (בטקסט הישן "תפריט") אינו
  נקרא.
* **השאלה עוברת במנגנון הקיים** — ‏`attended` (‏hello עם `prompt`, #906).
* **הגואי מופעל לפני הקריאה** — שאלת הכיווץ היא דיאלוג גרפי בלבד (#928).

הכיבוי מזויף כ-`exit 0` (כמו `poweroff -f`: אינו חוזר), ו-`sleep` מזויף
כרישום. ‏`IMAGESRV_TEST=0`: ה-`exit 0` של מצב הטסט יושב לפני הכיבוי.

הפלט הולך לקובץ ולא ל-PIPE (גוטצ'ה ב-`CLAUDE.md`); ה-stdin הוא צינור
משלנו ולא ירושה (‏#14).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from test_agent import BASH, posix
from test_final_report import (  # noqa: F401 — ‏`reports` הוא fixture
    LOOP_STUB,
    REPO,
    RUN_TIMEOUT_S,
    MAC,
    STUBS,
    journal,
    loadable_functions,
    native_tools,
    reports,
    sourced_libs,
    url_of,
)
from test_hold_and_capture import CAPTURE_STUBS, TASK_ANSWER

END_STUBS = (
    'attended() { echo "ASKED $1" >> "$RUN_DIR/trace"; shift; "$@"; }; '
    'gui_parent() { echo "GUI-START" >> "$RUN_DIR/trace"; return 1; }; '
    'gui_ask_choice() { echo "DIALOG $*" >> "$RUN_DIR/trace"; '
    '[ -n "${CE_ANSWER:-}" ] || return 1; echo "$CE_ANSWER"; }; '
    'finish_and_stop() { echo "POWEROFF" >> "$RUN_DIR/trace"; exit 0; }; '
    'sleep() { echo "SLEEP $1" >> "$RUN_DIR/trace"; }; '
)


def run_end(tmp_path: Path, server: str, *, answer: str | None,
            keep_open: bool = False, gui: str = "") -> dict:
    """מריץ קליטה מוצלחת עד סופה. ‏`answer` נכתב ל-stdin ואז הוא נסגר;
    ‏`keep_open` משאיר אותו פתוח ושקט עד שהמעטפת יוצאת — מסך בלי אדם."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(json.dumps(TASK_ANSWER), encoding="utf-8")
    funcs = tmp_path / "agentfuncs.sh"
    funcs.write_text(loadable_functions(), encoding="utf-8")
    out_file = tmp_path / "out.txt"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"SERVER={shlex.quote(server)} "
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f"IMAGESRV_TEST=0 HTTP_RETRIES=0 HTTP_TIMEOUT=4 PROGRESS_INTERVAL_S=0.2 "
        f"FINAL_REPORT_TRIES=2 FINAL_REPORT_GAP_S=0 D_ROLE=build CE_ANSWER={gui!r}; "
        + sourced_libs() + STUBS + CAPTURE_STUBS + END_STUBS + LOOP_STUB
        + f". {posix(funcs)}; "
        + '_build_standby=1; do_task; '
        + 'echo "RETURNED rc=$? standby=$_build_standby"'
    )
    with out_file.open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
            stdin=subprocess.PIPE, stdout=sink, stderr=subprocess.STDOUT,
            cwd=str(REPO),
        )
        assert proc.stdin is not None
        try:
            if answer is not None:
                proc.stdin.write(answer.encode())
                proc.stdin.flush()
            if not keep_open:
                proc.stdin.close()
            proc.wait(timeout=RUN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            raise AssertionError(
                f"הקליטה לא הסתיימה תוך {RUN_TIMEOUT_S}s. הפלט עד כה:\n"
                + out_file.read_text(encoding="utf-8")) from None
        finally:
            if not proc.stdin.closed:
                proc.stdin.close()
    return {
        "out": out_file.read_text(encoding="utf-8"),
        "log": journal(run),
        "trace": (run / "trace").read_text(encoding="utf-8").splitlines()
        if (run / "trace").exists() else [],
    }


def waited(trace: list[str]) -> list[str]:
    """כל `sleep` שאינו אפס — ההמתנה שהמפעיל היה רואה."""
    return [t for t in trace if t.startswith("SLEEP ") and t != "SLEEP 0"]


# --- אין שאלה: כיבוי ---------------------------------------------------------


@native_tools
def test_menu_on_the_dialog_returns_to_the_menu(tmp_path, reports):
    """**לב #409.** "חזרה לתפריט" — המכונה נשארת דלוקה וחוזרת ללולאה."""
    result = run_end(tmp_path, url_of(reports), answer=None, gui="menu")
    assert "POWEROFF" not in result["trace"], "המכונה כבתה למרות שהמפעיל בחר תפריט"
    assert "RETURNED rc=0 standby=0" in result["out"], result["out"]
    assert "back to the menu" in result["log"]
    assert len(reports.received) == 1 and reports.received[0]["state"] == "done"


@native_tools
def test_poweroff_on_the_dialog_powers_off_at_once(tmp_path, reports):
    result = run_end(tmp_path, url_of(reports), answer=None, gui="poweroff")
    assert "POWEROFF" in result["trace"]
    assert waited(result["trace"]) == []
    assert "RETURNED" not in result["out"]


@native_tools
def test_no_answer_powers_off_and_no_key_is_read(tmp_path, reports):
    """בלי גואי/בלי תשובה — כיבוי. "1" ב-stdin היה "תפריט" בטקסט הישן."""
    result = run_end(tmp_path, url_of(reports), answer="1\n")
    assert "POWEROFF" in result["trace"], result["out"]
    assert "RETURNED" not in result["out"]
    assert "Back to the menu" not in result["out"]
    assert "capture complete -- powering off" in result["log"]


@native_tools
def test_the_question_is_the_gui_dialog_through_attended(tmp_path, reports):
    """‏`attended` נושא את השאלה לקונסולה (#906); השאלה עצמה היא הדיאלוג
    capture-end. ‏#1560 (ברזל 17/10): התקרה 20 ש׳ (CAPTURE_END_ASK_S, נדב), והנושא
    הוא רגע הכיבוי — הגואי סופר אליו; עד אז "20" כטקסט קבוע שלא זז."""
    result = run_end(tmp_path, url_of(reports), answer=None, gui="poweroff")
    asked = [t for t in result["trace"] if t.startswith("ASKED ")]
    assert asked == ["ASKED הקליטה הושלמה: לכבות או לחזור לתפריט"]
    from test_end_of_task_1560 import asked_dialog
    assert asked_dialog(result["trace"], "capture-end"), result["trace"]


@native_tools
def test_no_wait_before_the_power_off(tmp_path, reports):
    """אין 20 השניות של השאלה."""
    result = run_end(tmp_path, url_of(reports), answer=None)

    assert "POWEROFF" in result["trace"]
    assert waited(result["trace"]) == [], f"המתנה לפני הכיבוי: {waited(result['trace'])}"


@native_tools
def test_the_report_is_sent_before_the_power_off(tmp_path, reports):
    result = run_end(tmp_path, url_of(reports), answer=None)

    assert "POWEROFF" in result["trace"]
    assert len(reports.received) == 1 and reports.received[0]["state"] == "done"


@native_tools
def test_the_gui_is_started_before_the_capture_reads(tmp_path, reports):
    """שאלת הכיווץ היא דיאלוג גרפי בלבד (#928, 11/10). קליטה שהוזמנה
    מהקונסולה מגיעה לפעמים לפני שהגואי עלה — ‏`do_task` מפעיל אותו לפני
    הקריאה, ולא נופל למסך טקסט."""
    result = run_end(tmp_path, url_of(reports), answer=None)

    trace = result["trace"]
    capture = next((t for t in trace if t.startswith("CAPTURE ")), None)
    assert "GUI-START" in trace and capture, trace
    assert trace.index("GUI-START") < trace.index(capture)
    assert "a shrink question will cancel the capture" in result["log"]
