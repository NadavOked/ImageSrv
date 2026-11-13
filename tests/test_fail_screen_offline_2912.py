"""#2912: מסך הכשל של מחשב הבנייה לא נעלם כשהשרת לא עונה.

מסך הכשל (#1569/#2068) הוא **מקומי**: הרשומות שלו (`fail.state`), הכפתורים
שלו, ו-`fail.action` ש-`hold_action` קורא כל שנייה -- שום דבר מזה לא צריך
את השרת. אבל `gui_state` מוסיף את `fail.state` לקובץ המצב רק אחרי שהצליח
לשאול את השרת. כשהשרת נפל (או שהרשת נותקה) באמצע קליטה, ה-snapshot החלופי
של `gui_state_loop` נכתב עם `message=` בלבד, הגואי עבר ל"לא ניתן לאמת את
מצב התחנה", והכפתורים "אתחל"/"כבה" נעלמו -- דווקא כשאתחול הוא מה שצריך.

התיקון: ה-snapshot החלופי נושא גם את רשומות הכשל (כמו `gui_shrink_state`
ו-`gui_ask_state`, שגם הם מקומיים), את גיל ה-hello ותשובתו (שורת המצב אומרת
"אין קשר עם השרת"), ושורה אחת שאומרת למה שאר המצב חסר. לא "הכול תקין"
(עיקרון 5): `message=` נשאר.

בקרה שלילית (bakara-shlilit): עם `agent/lib/gui/guistate.sh` ב-origin/main,
`test_the_failure_screen_stays_when_the_server_does_not_answer` נכשל על
התנהגות -- ב-state אין `fail=` ואין `fail_btn=`.
"""

from __future__ import annotations

import shlex

import pytest

from test_agent import BASH, posix
from test_failure_screen_1569 import records, run_agent, values

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")

#: המשפט ש-gui_fail_local מוסיף: למה שאר המצב חסר
UNVERIFIED = "מצב התחנה לא אומת"


def _outage(tmp_path, *, failure: bool, hello: str = "30|") -> dict:
    """הסוכן האמיתי: מסך כשל (אם failure), שרת שלא עונה (gui_state נכשל),
    ואז איטרציה אחת של gui_state_loop. מחזיר את קובץ המצב שהגואי היה קורא.
    hello = גיל ה-hello (שניות אחורה) ואחריו `|` וקוד ה-HTTP (ריק = אין תשובה)."""
    gui = tmp_path / "run" / "gui"
    age, rc = hello.split("|")
    script = (
        'mkdir -p "$GUI_DIR"; '
        + ('fail_screen hold "capture did not complete"; ' if failure else '')
        + f'printf "%s|%s\\n" "$(( $(date +%s) - {int(age)} ))" {shlex.quote(rc)} > "$RUN_DIR/hello.state"; '
        + 'gui_ask_state() { :; }; gui_state() { return 1; }; '
        + '( sleep() { exit 0; }; gui_state_loop )'
    )
    r = run_agent(tmp_path, script, env=f"D_ROLE=build GUI_DIR={posix(gui)}",
                  targets={"sda": ("failed", "write: short write")})
    r["state"] = records(gui / "state")
    assert r["state"], r["out"]
    return r


def test_the_failure_screen_stays_when_the_server_does_not_answer(tmp_path):
    r = _outage(tmp_path, failure=True)
    st, local = r["state"], r["recs"]
    assert values(local, "fail"), ("fail_screen did not write its records", r["out"])
    # the screen itself: the same block the hold wrote, buttons included
    assert values(st, "fail") == values(local, "fail"), st
    buttons = values(st, "fail_btn")
    assert buttons and buttons == values(local, "fail_btn"), st
    assert {b.split("|", 1)[0] for b in buttons} & {"off", "reboot", "retry"}, buttons
    # not "all well": the unverified message stays, and a step says so
    assert any("לא ניתן לאמת" in m for m in values(st, "message")), st
    notes = [s for s in values(st, "fail_step") if UNVERIFIED in s]
    assert len(notes) == 1, st
    # the note comes after fail= (fail= empties the step list), and after the screen's own steps
    keys = [k for k, _ in st]
    assert keys.index("fail") < keys.index("fail_step"), keys
    assert values(st, "fail_step")[-1] == notes[0], values(st, "fail_step")
    # the status line's evidence: a hello that is 30 s old and had no answer
    assert values(st, "hello_age") == ["30"], st
    assert values(st, "hello_rc") == [""], st


def test_no_failure_means_no_failure_screen_when_the_server_does_not_answer(tmp_path):
    """השומר ההפוך: בלי fail.state ה-snapshot החלופי נשאר מה שהיה -- הודעה בלבד.
    לא ממציאים מסך כשל, ולא מוסיפים את ההערה על הכפתורים."""
    st = _outage(tmp_path, failure=False)["state"]
    assert any("לא ניתן לאמת" in m for m in values(st, "message")), st
    assert not [k for k, _ in st if k.startswith("fail")], st


def test_a_hold_that_ended_takes_the_failure_screen_off_the_next_snapshot(tmp_path):
    """hold.sh מוחק את fail.state כשהמסך מסיים את תפקידו; ה-snapshot הבא לא נושא אותו
    (אחרת מסך כשל ישן היה נשאר מעל השאלה הבאה -- #2743/#2783)."""
    gui = tmp_path / "run" / "gui"
    r = run_agent(
        tmp_path,
        'mkdir -p "$GUI_DIR"; fail_screen hold "capture did not complete"; rm -f "$RUN_DIR/fail.state"; '
        'gui_ask_state() { :; }; gui_state() { return 1; }; ( sleep() { exit 0; }; gui_state_loop )',
        env=f"D_ROLE=build GUI_DIR={posix(gui)}", targets={"sda": ("failed", "write: short write")})
    st = records(gui / "state")
    assert st and not [k for k, _ in st if k.startswith("fail")], (st, r["out"])
