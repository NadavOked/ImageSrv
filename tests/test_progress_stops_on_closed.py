"""‏#557, צד הסוכן — הלולאה נעצרת על `not_open`, ורק עליו.

שרת שמסרב לסוכן שאינו מקשיב הוא אותה לולאה עם קוד אחר. שני הצדדים
נדרשים, ולכן הקובץ הזה קיים לצד `test_closed_session_report.py`.

⚠️ **וההבחנה שהכול תלוי בה:** ‏`not_open` פירושו *"נענינו בלא"*.
שגיאת רשת, ‏500, או תשובה ריקה פירושן *"לא הצלחנו לשאול"* — ולולאה
שנעצרת **עליהן** היא מכונה שמפסיקה לדווח כי השרת אותחל לשנייה.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
BASH = shutil.which("bash")
JQ = shutil.which("jq")
requires_bash = pytest.mark.skipif(BASH is None, reason="bash לא זמין")

#: ⚠️ **בלי `jq`, ‏`json_get` מחזיר `null` על כל קלט** — והטסטים היו
#: עוברים חמישה מתוך שישה **מהסיבה הלא נכונה**: כל תשובה נראית
#: כ"אין קוד", ולכן `CONTINUE`. ירוק שאינו מעיד על דבר.
#:
#: לכן `jq` אינו מדולג בשקט אלא **מוצהר**, ומי שאין לו מקבל דילוג
#: בשם ולא חמישה ירוקים ריקים.
requires_jq = pytest.mark.skipif(
    JQ is None, reason="jq אינו מותקן — json_get היה מחזיר null על כל קלט")


def run_case(tmp_path: Path, reply: str | None) -> subprocess.CompletedProcess:
    """מריץ את `progress_refused_for_good` מול תשובה נתונה.

    ‏**הפונקציה האמיתית נטענת מהקובץ** ולא משוכפלת כאן: טסט שמשכפל
    את הלוגיקה עובר גם כשהמימוש נסחף.

    ⚠️ ‏`common.sh` מגדיר `log` בעצמו, ולכן הוא נטען **לפני**
    הדריסה. הפוך — והטסט רואה קוד יציאה בלי שום הסבר.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    if reply is not None:
        (run_dir / "progress.reply").write_text(reply, encoding="utf-8")
    script = textwrap.dedent(f"""
        RUN_DIR={run_dir.as_posix()!r}
        . {AGENT.as_posix()}/lib/core/jsonq.sh
        . {AGENT.as_posix()}/lib/core/progress.sh
        log() {{ printf '%s\\n' "$*" >&2; }}
        if progress_refused_for_good; then echo STOP; else echo CONTINUE; fi
    """)
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


# --- מה שעוצר --------------------------------------------------------------


@requires_bash
@requires_jq
def test_not_open_stops_the_loop(tmp_path: Path):
    """הסירוב היחיד שאומר "אין טעם לנסות שוב"."""
    out = run_case(tmp_path, '{"ok":false,"error":"closed","code":"not_open"}')
    assert out.stdout.strip() == "STOP", out


# --- ומה שאסור לו לעצור ----------------------------------------------------


@requires_bash
@requires_jq
def test_a_missing_reply_keeps_the_loop_running(tmp_path: Path):
    """אין קובץ תשובה = הבקשה לא הגיעה. **"לא ידענו" אינו "לא".**"""
    assert run_case(tmp_path, None).stdout.strip() == "CONTINUE"


@requires_bash
@requires_jq
def test_an_empty_reply_keeps_the_loop_running(tmp_path: Path):
    """שרת שנפל באמצע מחזיר גוף ריק. זו לא הכרעה."""
    assert run_case(tmp_path, "").stdout.strip() == "CONTINUE"


@requires_bash
@requires_jq
def test_a_success_reply_keeps_the_loop_running(tmp_path: Path):
    """המסלול התקין — רדיוס הפגיעה של התיקון."""
    assert run_case(tmp_path, '{"ok":true}').stdout.strip() == "CONTINUE"


@requires_bash
@requires_jq
def test_a_different_refusal_keeps_the_loop_running(tmp_path: Path):
    """‏`not_member` הוא סירוב — **ואינו** סיבה להפסיק לדווח.

    מכונה שאינה ברשימה עדיין עשויה להצטרף בגל הבא. עצירה עליה
    הייתה מרחיבה את התיקון למקום שלא נמדד בו כשל.
    """
    out = run_case(tmp_path, '{"ok":false,"code":"not_member"}')
    assert out.stdout.strip() == "CONTINUE", out


@requires_bash
@requires_jq
def test_garbage_that_is_not_json_keeps_the_loop_running(tmp_path: Path):
    """‏proxy שהחזיר HTML במקום JSON אינו אומר שהסבב נסגר."""
    assert run_case(tmp_path, "<html>502</html>").stdout.strip() == "CONTINUE"


@requires_bash
@requires_jq
def test_actual_progress_loop_returns_after_one_not_open_reply(tmp_path):
    """Execute the loop, not just its predicate; no network/server involved."""
    script = f'''
        RUN_DIR={tmp_path.as_posix()!r}
        . {AGENT.as_posix()}/lib/core/jsonq.sh
        . {AGENT.as_posix()}/lib/core/progress.sh
        log() {{ echo "$*" >&2; }}
        http_post_json() {{
            echo post >> "$RUN_DIR/posts"
            printf '%s' '{{"ok":false,"code":"not_open"}}'
        }}
        sleep() {{ echo unexpected-sleep; exit 9; }}
        progress_loop closed aa http://unused
        echo returned
    '''
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=10)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "returned"
    assert (tmp_path / "posts").read_text().splitlines() == ["post"]
