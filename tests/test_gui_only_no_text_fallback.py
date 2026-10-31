"""GUI בלבד — אין מסך טקסט חלופי במכונות (הכרעת נדב, 11/10).

עד כאן, כשהממשק הגרפי (‏imagesrv-kiosk / imagesrv-station-gui) לא עלה,
הסוכן נפל לזרימת טקסט על הקונסולה: אשף השחזור עם "Type ERASE", "Type this
computer's name", תפריט הבנייה הממוספר, מסך החדר, פתיחת סבב כיתה, השאלה
בסוף הקליטה ותפריט ה-SMART. נדב: "שלא יהיה כמו אתמול עם הפורט שגילינו
שקיים" — קוד שנשאר במקום מגיע אליו מישהו מתישהו. לכן הקוד **נמחק**, והטסט
הזה הוא השומר שהוא לא חוזר:

1. הקבצים שהיו טקסט בלבד אינם קיימים, ואינם נארזים.
2. אף פונקציה של הזרימות שהוסרו אינה מוגדרת ואינה נקראת ב-`agent/`.
3. אין `read` מהקונסולה ב-`agent/` מחוץ לערוץ הגשר הגרפי (‏`gui_auth`
   קורא את הסיסמה שהגואי מעביר בצינור, ‏`gui_records` את ה-fifo של
   הפעולות). בלי חריגים: שאלת ההחזרה של כיווץ שנשאר (#926) הייתה חריג
   עד שנדב הכריע "הכול גואי" (11/10) — היא דיאלוג גרפי עכשיו (askgui.sh).
4. אין מחרוזות-הנחיה של מסך טקסט (‏"Type ERASE", ‏"Choose [", ‏stty).
5. התנהגות: מסך שצריך אדם ולא קיבל גואי — הודעה ואתחול מהדיסק המקומי
   (עיקרון 1), בלי לקרוא שורה אחת מהמקלדת; ‏SMART ושאלת הכיווץ בלי גואי —
   ברירת המחדל של "אין תשובה", לא הקשה שחיכתה ב-stdin.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import pytest

from test_agent import AGENT, BASH, REPO, posix
from test_final_report import cut_function

import sizelimit
from agentlib import flat_lib

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

#: הקבצים שהיו זרימת טקסט בלבד ונמחקו.
DELETED_LIBS = ("recovery.sh", "selfrestore.sh", "classround.sh",
                "directflow.sh", "roomdraw.sh")

#: הפונקציות של הזרימות שהוסרו. שם שחוזר כאן — כהגדרה או כקריאה — הוא
#: זרימת טקסט שחזרה.
DELETED_FUNCTIONS = (
    "recovery_flow", "recovery_menu", "recovery_gate", "recovery_login",
    "recovery_login_ask", "single_station_flow", "login_failed", "login_body",
    "login_post", "self_restore_flow", "self_restore_state",
    "build_menu", "build_menu_flow", "build_menu_gate", "build_standby",
    "build_ask_name", "build_pick_folder", "build_capture_flow",
    "build_menu_options", "build_menu_label",
    "room_flow", "room_pick_image", "room_status_get", "room_draw",
    "direct_flow", "direct_open", "direct_pick_targets",
    "class_round_flow", "class_menu", "image_menu", "machine_menu",
    "open_round_body", "open_round_post", "capture_end_ask",
    "shrink_gui_release",   # stopped the GUI to ask on the text screen
)

#: הנחיות של מסך טקסט. כל אחת מהן הייתה בקוד עד 11/10.
PROMPT_STRINGS = ("Type ERASE", "Type this computer", "Choose [", "or 0 to",
                  "[y/N]", "Username: ", "Password: ", "Press Enter")

#: קריאות stdin מותרות: ערוץ הגשר הגרפי, והחריג היחיד שממתין להכרעה.
#: (קובץ, פונקציה) — לא שורה, כדי שתוספת קריאה בפונקציה אחרת תיתפס.
ALLOWED_STDIN_READS = {
    ("guibridge.sh", "gui_auth"),       # הסיסמה מהגואי, בצינור (--auth-cmd)
    ("guibridge.sh", "gui_records"),    # ה-fifo של הפעולות מהגואי
}

SHELL_FILES = sizelimit.guarded_shell_files(REPO)
AGENT_FILES = [p for p in SHELL_FILES if AGENT in p.parents]


def _code_lines(path: Path):
    """(מספר שורה, פונקציה נוכחית, שורה בלי הערה) לכל שורה בקובץ."""
    func = ""
    for no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", raw)
        if m:
            func = m.group(1)
        stripped = raw.lstrip()
        if stripped.startswith("#"):
            continue
        # הערה בסוף שורה: ' # ' מחוץ למירכאות (קירוב שמספיק לסוכן הזה).
        code = raw
        for m2 in re.finditer(r"\s#\s", raw):
            before = raw[:m2.start()]
            if before.count("'") % 2 == 0 and before.count('"') % 2 == 0:
                code = before
                break
        yield no, func, code


#: ‏`read` במקום של פקודה: תחילת שורה, אחרי ; && || | { ( ! או מילת
#: מפתח, ואחרי השמות-משתנה (‏IFS=) — לא המילה "read" בתוך מחרוזת.
READ_RE = re.compile(
    r"(?:^|[;&|{(!]|\b(?:do|then|else|while|until|if)\b)\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*read\s")


def _stdin_reads(path: Path) -> list[tuple[int, str, str]]:
    """קריאות `read` שהקלט שלהן אינו קובץ, heredoc או צינור — כלומר
    הקונסולה."""
    lines = list(_code_lines(path))
    hits = []
    for i, (no, func, code) in enumerate(lines):
        m = READ_RE.search(code)
        if not m:
            continue
        before = code[:m.start()]
        if before.count('"') % 2 or before.count("'") % 2:
            continue                           # the word "read" inside a string
        # A `\` continuation: the redirect is on the next line(s).
        j = i
        while code.rstrip().endswith("\\") and j + 1 < len(lines):
            j += 1
            code = code.rstrip()[:-1] + " " + lines[j][2]
        after = code[m.start():]
        if "<" in after:                       # < file, <<EOF, <&3
            continue
        if "|" in code[:m.start()]:            # ... | while read / | read
            continue
        if re.search(r"\bwhile\b", code[:m.end()]):
            indent = len(code) - len(code.lstrip())
            # Piped into the loop, or into a `{ ...; while read` group that
            # opened a few lines up (same function).
            back = [c for _, f, c in lines[max(0, i - 5):i] if f == func and c.strip()]
            if any(c.rstrip().endswith(("|", "| {")) for c in back) \
                    or code.lstrip().startswith("|"):
                continue
            same = re.search(r"\bdone\b(.*)$", after)
            if same:                               # while ...; do ...; done[ < f]
                if "<" in same.group(1):
                    continue
                hits.append((no, func, code.strip()))
                continue
            done = next((c for _, _, c in lines[i + 1:]
                         if c.lstrip().startswith("done")
                         and len(c) - len(c.lstrip()) <= indent), "")
            if "<" in done:                    # done < file / done 3< file
                continue
        hits.append((no, func, code.strip()))
    return hits


def test_the_text_only_libs_are_gone_and_not_packed():
    # #2239: agent/lib gets topic subfolders -- look for the names anywhere under it.
    present = [n for n in DELETED_LIBS if any((AGENT / "lib").rglob(n))]
    assert present == [], f"קבצי זרימת טקסט חזרו ל-agent/lib: {present}"
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    named = [n for n in DELETED_LIBS if n in builder]
    assert named == [], f"build_initramfs.sh מזכיר קובץ שנמחק: {named}"
    # הבנאי אורז את agent/lib כולו דרך agent-lib-flatten.sh — מה שאינו שם אינו נארז.
    assert 'agent-lib-flatten.sh" "$AGENT_DIR/lib"' in builder


def test_no_removed_text_flow_function_is_defined_or_called():
    offenders = []
    for path in AGENT_FILES:
        for no, _func, code in _code_lines(path):
            for name in DELETED_FUNCTIONS:
                if re.search(rf"(?<![A-Za-z0-9_]){name}(?![A-Za-z0-9_])", code):
                    offenders.append(f"{path.name}:{no}: {name}")
    assert offenders == [], "זרימת טקסט שהוסרה חזרה:\n" + "\n".join(offenders)


def test_nothing_in_the_agent_reads_the_console_outside_the_gui_bridge():
    offenders = []
    allowed_seen = set()
    for path in AGENT_FILES:
        for no, func, code in _stdin_reads(path):
            if (path.name, func) in ALLOWED_STDIN_READS:
                allowed_seen.add((path.name, func))
                continue
            offenders.append(f"{path.name}:{no} ({func}): {code}")
    assert offenders == [], \
        "קריאה מהקונסולה מחוץ לגשר הגרפי — מסך טקסט:\n" + "\n".join(offenders)
    # החריג אינו רשימה שמתייבשת בשקט: חריג שכבר אינו קורא — למחוק מכאן.
    stale = ALLOWED_STDIN_READS - allowed_seen
    assert stale == set(), f"חריג שאינו קורא עוד מ-stdin — להסיר מהרשימה: {stale}"


def test_no_text_screen_prompt_is_left_in_the_agent():
    offenders = []
    for path in AGENT_FILES:
        for no, func, code in _code_lines(path):
            if (path.name, func) in ALLOWED_STDIN_READS:
                continue
            if re.search(r"(?<![A-Za-z0-9_])stty(?![A-Za-z0-9_])", code):
                offenders.append(f"{path.name}:{no}: stty")
            for s in PROMPT_STRINGS:
                if s in code:
                    offenders.append(f"{path.name}:{no}: {s!r}")
    assert offenders == [], "הנחיית מסך טקסט נשארה:\n" + "\n".join(offenders)


def test_the_scanner_catches_a_console_read(tmp_path):
    """הסורק עצמו נבדק — סורק שאינו תופס דבר הוא ירוק מזויף (עיקרון 5)."""
    f = tmp_path / "x.sh"
    f.write_text(
        'ask() {\n'
        '    printf "Choose: "\n'
        '    read -r _c\n'
        '    while read -r _l; do echo "$_l"; done\n'
        '    IFS= read -r _ok < "$f"\n'
        '    printf "a\\n" | while read -r _p; do :; done\n'
        '    while IFS= read -r _q; do\n'
        '        :\n'
        '    done < "$f"\n'
        '}\n', encoding="utf-8")
    hits = [(no, func) for no, func, _ in _stdin_reads(f)]
    assert hits == [(3, "ask"), (4, "ask")]


# --- התנהגות ------------------------------------------------------------------


def _run(tmp_path: Path, body: str, stdin_text: str = "1\n1\n1\nERASE\n") -> tuple[str, int, str]:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    feed = tmp_path / "keys.txt"
    feed.write_text(stdin_text, encoding="utf-8", newline="\n")
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} LOG_FILE={shlex.quote(posix(run / 'agent.log'))} "
        "IMAGESRV_TEST=1 LIB_DIR=" + shlex.quote(posix(flat_lib())) + "; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/gui/ui.sh; . {posix(AGENT)}/lib/gui/localboot.sh; "
        f". {posix(AGENT)}/lib/gui/guiparent.sh; "
        # 11/10: no efivars under SYSROOT -- BootNext is "skipped" and the
        # machine running the test is never touched; the report hello is
        # recorded, not sent.
        f"export SYSROOT={shlex.quote(posix(tmp_path / 'sysroot'))}; "
        'sleep() { :; }; '
        'build_hello() { printf %s "{\\"joining\\":$1}"; }; '
        'http_post_json() { cat "$2" >> "$RUN_DIR/hellos"; echo >> "$RUN_DIR/hellos"; }; '
        + body
    )
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        stdin=feed.open("rb"), capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), timeout=60)
    log = (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""
    return proc.stdout + proc.stderr, proc.returncode, log


def _kiosk_absent():
    # gui_parent בודק את הנתיבים האלה בדיוק; בעמדת פיתוח/מכונת טסטים אינם קיימים.
    if Path("/usr/bin/imagesrv-kiosk").exists():
        pytest.fail("/usr/bin/imagesrv-kiosk קיים על המכונה המריצה — הטסט מניח גואי חסר")


@pytest.mark.parametrize("dead_gui", [False, True], ids=["never-started", "crashed"])
def test_the_build_screen_without_a_gui_boots_the_local_disk(tmp_path, dead_gui):
    """מחשב בנייה שהגואי שלו לא עלה (או קרס): הודעה, יומן, ‏die_local —
    בלי תפריט ובלי לקרוא את המקשים שחיכו ב-stdin. לפני 11/10: כניסה בטקסט
    ותפריט ממוספר."""
    _kiosk_absent()
    screen = cut_function("build_console_screen")
    assert screen is not None
    dead = "(exit 0) & _gui_pid=$!; wait $_gui_pid 2>/dev/null; " if dead_gui else ""
    out, rc, log = _run(
        tmp_path,
        "shrink_offer_pending() { :; }; " + dead + screen
        + '\nbuild_console_screen; echo "NOT-REACHED rc=$?"; '
        'IFS= read -r _left && echo "STDIN-UNTOUCHED $_left"')
    assert rc == 86, out
    assert "The graphical interface did not start." in out
    assert "TEST-REBOOT: native GUI unavailable (build screen)" in out
    assert '"gui_failed":{"screen":"build"' in (tmp_path / "run" / "hellos").read_text()
    assert "NOT-REACHED" not in out
    assert "no text fallback" in log
    assert "Sign in" not in out and "Choose" not in out


def test_the_recovery_branch_without_a_gui_boots_the_local_disk():
    """הענף של מצב recovery בלולאה הראשית: בלי גואי — gui_unavailable_local,
    לא אשף טקסט."""
    src = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    branch = src[src.index("        recovery)"):src.index("        wait_open)")]
    assert "gui_unavailable_local recovery" in branch
    assert "recovery_flow" not in branch


def test_the_gui_unavailable_message_is_ascii(tmp_path):
    """הקונסולה הלינוקסית אינה מרנדרת עברית/RTL (ui.sh) ואין באימג' גופן
    קונסולה — עברית הייתה מגיעה כריבועים."""
    out, rc, _log = _run(tmp_path, "gui_unavailable_local recovery")
    assert rc == 86
    assert out.isascii(), out


def test_smart_without_a_gui_takes_the_no_answer_default(tmp_path):
    """תפריט ה-SMART בטקסט הוסר. בלי גואי — "המשך" של הצבע (נדב 16/09):
    כתום כותב (rescue), אדום מדלג (skip). ה-"1" שמחכה ב-stdin היה בוחר
    "החלף" בתפריט הישן."""
    lib = posix(flat_lib())
    out, rc, _log = _run(
        tmp_path,
        f". {lib}/smartgate.sh; D_ROLE=cloner; "
        'gui_smart_choice() { return 1; }; '
        'echo "orange=$(smart_ask "Disk 1" "SMART warn" sda warn "realloc" rescue)"; '
        'echo "red=$(smart_ask "Disk 2" "failed" sdb failed_last "cable" skip)"; '
        'IFS= read -r _left && echo "STDIN-UNTOUCHED $_left"')
    assert rc == 0, out
    assert "orange=rescue" in out
    assert "red=skip" in out
    assert "STDIN-UNTOUCHED 1" in out


def test_the_shrink_question_without_a_gui_cancels(tmp_path):
    """שאלת הכיווץ בטקסט הוסרה (#928 היא דיאלוג). בלי גואי — cancel: שום
    דבר לא מכווץ בלי אדם שאמר כן. ה-"1" ב-stdin היה "כווץ וקלוט" בישן."""
    lib = posix(flat_lib())
    plan = tmp_path / "plan"
    plan.write_text("3|g|u|2048|1000|60000000000|40000000000\n", encoding="utf-8")
    out, rc, _log = _run(
        tmp_path,
        f". {lib}/shrinkplan.sh; . {lib}/shrinkgui.sh; _gui_pid=; "
        f'echo "answer=$(shrink_ask {shlex.quote(posix(plan))})"; '
        'IFS= read -r _left && echo "STDIN-UNTOUCHED $_left"')
    assert rc == 0, out
    assert "answer=cancel" in out
    assert "STDIN-UNTOUCHED 1" in out
