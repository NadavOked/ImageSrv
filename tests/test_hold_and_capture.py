"""שני דיווחים שנבלעו באותו קובץ — ‏issues #109 ו-#127.

שניהם אותה צורה בדיוק, בשתי שכבות שונות: משהו נשלח לשרת, ומה שחזר ממנו
נמחק לפני שמישהו הסתכל בו.

**‏#109 — הדופק של מכונה שנעצרה.** ‏`ui_error_hold` הפעילה את הדופק
כ-`"$2" > /dev/null 2>&1 || true`: ‏stdout, ‏stderr וקוד היציאה, שלושתם
בשורה אחת. הדופק אינו דיווח שייחזור בעוד שתי שניות (שם ה-`|| true` של
‏`progress_loop` נכון ונשאר) — הוא **הראיה היחידה** שהמכונה חיה:
‏`last_seen` נכתב ב-hello ובו בלבד, ואחרי `AWAKE_SECONDS` הקונסולה
מציירת את המכונה כ**כבויה**. זו התשובה הגרועה ביותר שאפשר לתת לטכנאי —
היא שולחת אותו לחפש כבל חשמל בזמן שהמכונה דלוקה עם הודעת השגיאה שהוא
בא לקרוא. זו חזרה של #64, דרך התיקון של #64 עצמו.

**‏#127 — דיווח הסיום של קליטה.** ‏`echo done > state; sleep 4; kill`,
פעמיים: אותו הימור ש-#101 הוציא מסבב הכיתה וממסלול המגירות. כאן הוא
החמור מכולם — הצעד הבא הוא `poweroff -f` והדיסק נשלף — ומשימה שנשארה
פתוחה בשרת מול מחשב כבוי היא מצב שהמפעיל אינו יכול לפענח.

הבקרה השלילית בשני המקרים היא על **התנהגות**: כל טסט כאן מריץ את
הפונקציות האמיתיות, וטוען את `hold.sh` רק אם היא קיימת — כך אותו טסט
בדיוק רץ מול הקוד שלפני התיקון, ונופל שם על מה שהמכונה עשתה (נכבתה בלי
אישור, שתקה בלי לומר) ולא על קובץ חסר.

הפלט של כל הרצה הולך לקובץ ולא ל-PIPE: תהליך רקע ששרד מחזיק עותק של
הצינור, וההמתנה עליו נראית כמו באג בקוד שנבדק (גוטצ'ה ב-`CLAUDE.md`).
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from test_agent import AGENT, BASH, posix
from test_final_report import (  # noqa: F401 — ‏`reports` הוא fixture
    REPO,
    RUN_TIMEOUT_S,
    MAC,
    cut_function,
    journal,
    native_tools,
    reports,
    run_path,
    sourced_libs,
    url_of,
)

TASK = "tsk_5c20a1"
#: האסימון שה-hello מוסר למכונה (#530) — ‏48 תווים כמו `tasks.new_token`.
TOKEN = "5c" * 24

#: תשובת השרת למחשב בנייה עם משימת קליטה (ממשק 3). ‏`do_task` קורא
#: מכאן `.task.*` ולא `.session.*` — ולכן גם הדיווח שלו נושא `task_id`.
TASK_ANSWER = {
    "schema": 1, "known": True, "role": "build",
    "task": {"id": TASK, "type": "capture", "disk": "sdb",
             "name": "Windows 11 Lab", "token": TOKEN},
}

#: הקליטה עצמה אינה מה שנבדק כאן — היא דורשת דיסק — ולכן היא מזויפת
#: על גבול המערכת: מה שהיא כותבת ל-`targets/` ול-`state` הוא בדיוק מה
#: שהאמיתית כותבת, וקוד היציאה נשלט מבחוץ.
CAPTURE_STUBS = (
    'capture_disk() { echo "CAPTURE $1" >> "$RUN_DIR/trace"; '
    'target_init "$2" 4096; echo capturing > "$RUN_DIR/state"; '
    'if [ "${CAPTURE_RC:-0}" = "0" ]; then target_set "$2" done; '
    'else target_set "$2" failed "the drive is not GPT"; fi; '
    'return "${CAPTURE_RC:-0}"; }; '
    'upload_manifest() { echo "UPLOAD $1" >> "$RUN_DIR/trace"; '
    'return "${UPLOAD_RC:-0}"; }; '
)


def run_capture(tmp_path: Path, server: str, *, tries: int = 2,
                capture_rc: int = 0, upload_rc: int = 0) -> dict:
    """מריץ את מסלול הקליטה על התשתית של #101, בלי לבנות שנייה."""
    return run_path(
        tmp_path, server, "do_task", tries=tries, answer=TASK_ANSWER,
        stubs=CAPTURE_STUBS, env=f"CAPTURE_RC={capture_rc} UPLOAD_RC={upload_rc}",
    )


# --- ‏#127: שני מסלולי הקליטה קוראים את התשובה --------------------------------


@native_tools
def test_a_capture_does_not_power_off_when_the_final_report_was_refused(
        tmp_path, reports):
    """**לב #127.** השרת אינו מאשר את דיווח הסיום.

    הקוד שלפני התיקון ישן ארבע שניות, הרג את הדווח, וכיבה. אחרי
    הכיבוי אין למי לחזור: המשימה נשארת `running` בשרת מול מחשב שכבוי
    ושהדיסק כבר נשלף ממנו, והמפעיל אינו יכול לדעת אם האימג' נכנס
    לספרייה. לכן המכונה נעצרת, נקובה בשם, ואינה נכבית.
    """
    reports.refuse_first = -1

    result = run_capture(tmp_path, url_of(reports))

    assert "TEST-HOLD" in result["out"], "המכונה נכבתה בלי אישור מהשרת"
    assert "Capture complete" not in result["out"]
    assert "RETURNED rc=1" in result["out"]
    # הדיווח נשלח באמת, ולא פעם אחת — התקרה היא ניסיונות, לא שניות.
    assert len(reports.received) == 2
    assert reports.received[-1]["task_id"] == TASK
    assert reports.received[-1]["state"] == "done"
    assert "session_id" not in reports.received[-1]
    assert "did not acknowledge the final report" in result["log"]


@native_tools
def test_a_capture_powers_off_once_the_server_took_the_report(tmp_path, reports):
    """אישור הוא אישור: ‏200 → המכונה אומרת שהאימג' בספרייה ונכבית."""
    result = run_capture(tmp_path, url_of(reports))

    assert "TEST-HOLD" not in result["out"]
    # The text screen that said so (#409's question) was removed on 11/10
    # (GUI only); the journal line is the evidence now.
    assert "capture complete -- powering off" in result["log"]
    assert "RETURNED" not in result["out"], "המסלול לא הגיע לכיבוי"
    assert len(reports.received) == 1
    report = reports.received[0]
    assert report["task_id"] == TASK and report["state"] == "done"
    assert report["targets"][0]["dev"] == "sdb"


@native_tools
def test_the_capture_report_is_retried_until_the_server_comes_back(
        tmp_path, reports):
    """שרת שהופעל מחדש בזמן הקליטה: הדיווח האחרון מגיע בניסיון השלישי,
    והמכונה ממשיכה כרגיל."""
    reports.refuse_first = 2

    result = run_capture(tmp_path, url_of(reports), tries=4)

    assert "TEST-HOLD" not in result["out"]
    assert len(reports.received) == 3
    assert "got through on try 3" in result["log"]


@native_tools
def test_a_failed_capture_reports_its_failure_and_reads_the_answer(
        tmp_path, reports):
    """המסלול השני, שהיה שבור באותה צורה בדיוק.

    ‏"נכשל" חייב להגיע לשרת לא פחות מ"הסתיים": בלעדיו המשימה נשארת
    ‏`running`, והמפעיל מחפש קליטה שכבר מתה. הקוד שלפני התיקון הרג
    כאן את הדווח אחרי `sleep 4` — כלומר גם הימר וגם השתיק.
    """
    result = run_capture(tmp_path, url_of(reports), capture_rc=1)

    assert "FAILED: capture did not complete" in result["out"]
    assert len(reports.received) == 1, "דיווח הכישלון לא נשלח סינכרונית"
    assert reports.received[0]["task_id"] == TASK
    assert reports.received[0]["state"] == "failed"
    assert reports.received[0]["targets"][0]["error"] == "the drive is not GPT"
    assert "RETURNED rc=1" in result["out"]


@native_tools
def test_a_failed_capture_that_was_not_heard_says_both_things(tmp_path, reports):
    """שני הכשלים אינם אותו כשל, והמסך אומר את שניהם: הקליטה נכשלה,
    **וגם** השרת לא שמע על כך. טכנאי שקורא רק את הראשון מניח שהקונסולה
    כבר מראה אותו."""
    reports.refuse_first = -1

    result = run_capture(tmp_path, url_of(reports), capture_rc=1)

    assert "capture did not complete, and the server was not told" in result["out"]
    assert len(reports.received) == 2
    assert reports.received[-1]["state"] == "failed"
    # הדווח מורם בחזרה עם ה-task, כדי ש-`failed` ינחת אם השרת יחזור.
    assert result["trace"].count(f"LOOP {TASK}") == 2


def code_only(text: str) -> str:
    """שורות הקוד בלבד. בדיקה סטטית שקוראת גם את ההערות בודקת את מה
    שכתוב על הבאג ולא את מה שהקוד עושה — וההערות כאן **מצטטות** את
    הצורה הישנה בכוונה, כדי שמי שיקרא אותן יידע למה היא הוחלפה."""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


@native_tools
def test_the_capture_path_no_longer_bets_on_the_clock():
    """‏`sleep 4` היה ההימור, ושני המסלולים עוברים עכשיו דרך
    ‏`report_final` — מנגנון אחד לארבעת דיווחי הסיום, לא ארבעה."""
    raw = cut_function("do_task")
    assert raw is not None
    body = code_only(raw)
    assert "sleep 4" not in body, "מסלול הקליטה עדיין מהמר על שעון"
    assert body.count("report_final") == 2, "אחד משני המסלולים אינו קורא תשובה"
    # ⚠️ ‏`finish_and_stop` ולא `poweroff -f`. המחרוזת עברה
    # ל-`common.sh` כשנוסף מתג `after-task`, והשומר הזה נשאר טוען
    # עליה — כלומר **נכשל על קוד תקין**. הוא `@native_tools`, ולכן
    # מדולג בווינדוס ולא רץ מעולם; נתפס בהרצה על מכונת הלינוקס
    # (#573/#588), בדיוק כמו התאום שלו ב-`test_final_report.py` (#587).
    # ‏#1560: סוף הקליטה עבר ל-build_end_ask (task.sh) — השאלה ואחריה finish_and_stop.
    assert body.index("report_final") < body.index("build_end_ask")
    ask = cut_function("build_end_ask")
    assert ask is not None and "finish_and_stop" in code_only(ask)


@native_tools
def test_a_held_capture_keeps_a_heartbeat():
    """עצירה אינה שתיקה (#64). המסלול הזה החזיק עצירה **בלי** דופק —
    ‏`ui_error_hold "capture did not complete"` בלי ארגומנט שני — וגם
    הרג את הדווח, כך שהמכונה נעלמה מהקונסולה פעמיים."""
    body = cut_function("do_task")
    assert 'ui_error_hold "capture did not complete" hold_beat' in body


# --- ‏#109: כישלון הדופק מגיע למקום שמישהו רואה -------------------------------
#
# ‏`ui_error_hold` אינה חוזרת, ולכן היא נבדקת מול דופק שסופר את עצמו
# ויוצא מהמעטפת בקריאה ה-N. זו יציאה ולא `kill`: בלי job control הריגת
# תת-מעטפת משאירה בנים חיים, ומדידת "כמה פעימות היו" בשעון היא בדיוק
# ההימור שהאיסיו הזה עוסק בו.

BEAT_COUNTER = (
    '_bump() { _n=$(cat "$RUN_DIR/n" 2>/dev/null || echo 0); _n=$((_n + 1)); '
    'echo "$_n" > "$RUN_DIR/n"; echo "BEAT $_n" >> "$RUN_DIR/trace"; }; '
)

#: דופק שנכשל בלי הפסקה, עם הסיבה על stderr — בדיוק כמו curl.
DEAD_BEAT = BEAT_COUNTER + (
    'beat() { _bump; [ "$_n" -ge "$BEATS" ] && exit 0; '
    'echo "curl: (7) Failed to connect to 10.10.10.8 port 8080" >&2; '
    'return 1; }; '
)

#: דופק שנכשל פעמיים ואז השרת חוזר — המעבר לשני הכיוונים.
FLAPPING_BEAT = BEAT_COUNTER + (
    'beat() { _bump; if [ "$_n" -le 2 ]; then '
    'echo "curl: (7) Connection refused" >&2; return 1; fi; '
    '[ "$_n" -ge "$BEATS" ] && exit 0; return 0; }; '
)


def hold_probe(tmp_path: Path, beat: str, *, beats: int = 5) -> dict:
    """מריץ עצירה אמיתית — בלי `IMAGECTL_TEST=1`, שקוצר אותה לפני הלולאה.

    ‏`HOLD_BEAT_S=0` כדי שהלולאה לא תמתין: מה שנמדד הוא מספר הפעימות
    שקרו, לא כמה זמן הן לקחו.
    """
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "hold.txt"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"IMAGECTL_TEST=0 HOLD_BEAT_S=0 BEATS={beats}; "
        + sourced_libs()
        + beat
        + 'ui_error_hold "restore did not complete" beat'
    )
    with out_file.open("w", encoding="utf-8") as sink:
        try:
            subprocess.run(
                [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                stdout=sink, stderr=subprocess.STDOUT, text=True, cwd=str(REPO),
                stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"העצירה לא הסתיימה תוך {RUN_TIMEOUT_S}s — הדופק לא נספר. "
                "הפלט עד כה:\n" + out_file.read_text(encoding="utf-8")
            ) from None
    return {
        "out": out_file.read_text(encoding="utf-8"),
        "log": journal(run),
        "trace": (run / "trace").read_text(encoding="utf-8").splitlines()
        if (run / "trace").exists() else [],
    }


@native_tools
def test_a_heartbeat_that_fails_reaches_the_journal(tmp_path):
    """**לב #109.** מכונה שנעצרה על שגיאה ואיבדה את השרת: קוד היציאה
    של הדופק אינו נמחק, והיומן אומר שהקשר אבד."""
    result = hold_probe(tmp_path, DEAD_BEAT)

    assert "the heartbeat is not getting through" in result["log"], \
        "כישלון הדופק נבלע — אין לו זכר ביומן"
    assert "the console will show this machine as off" in result["log"]


@native_tools
def test_a_heartbeat_that_fails_reaches_the_screen(tmp_path):
    """ובצד המכונה: טכנאי שעומד מולה רואה `FAILED` — וצריך לראות גם
    שהקונסולה אינה רואה אותה, אחרת הוא מחפש כבל חשמל."""
    result = hold_probe(tmp_path, DEAD_BEAT)

    assert "FAILED: restore did not complete" in result["out"]
    assert "no contact with the server since" in result["out"], \
        "אין שום סימן על המסך שהקשר אבד"
    assert "check the network, not" in result["out"]


@native_tools
def test_the_reason_curl_gave_is_not_thrown_away(tmp_path):
    """‏`2>&1 > /dev/null` מחק בדיוק את המשפט שאומר מה קרה. "לא הצליח
    להתחבר" ו"לא הצליח לפענח שם" שולחים טכנאי לשני מקומות שונים."""
    result = hold_probe(tmp_path, DEAD_BEAT)

    assert "Failed to connect to 10.10.10.8 port 8080" in result["log"]


@native_tools
def test_the_hold_keeps_beating_after_a_failure(tmp_path):
    """מה שאסור לשבור: לולאה שנעצרת על פעימה כושלת משאירה מכונה שגם
    אינה פועמת וגם אינה נראית. ארבע פעימות כושלות, והחמישית עדיין
    נקראת."""
    result = hold_probe(tmp_path, DEAD_BEAT, beats=5)

    assert result["trace"] == [f"BEAT {n}" for n in range(1, 6)]


@native_tools
def test_the_journal_says_it_once_per_transition_and_not_once_per_beat(tmp_path):
    """יומן שמקבל שורה כל עשר שניות הוא יומן שאיש לא קורא, ומסך
    שנגלל מאבד את שורת ה-`FAILED` שהטכנאי בא בשבילה."""
    result = hold_probe(tmp_path, DEAD_BEAT, beats=8)

    assert result["log"].count("the heartbeat is not getting through") == 1
    assert result["out"].count("no contact with the server since") == 1
    # גם הסיבה שהכלי נתן: שבע פעימות כושלות הן ניתוק אחד, לא שבעה.
    # היומן יושב ב-tmpfs, ועצירה נמשכת שעות.
    assert result["log"].count("Failed to connect") == 1


@native_tools
def test_the_machine_says_when_the_server_comes_back(tmp_path):
    """הגדרת "גמור" של האיסיו: שורה שנייה כשהקשר חוזר. אחרת המסך נשאר
    תקוע על "אין קשר" גם כשהמכונה כבר גלויה בקונסולה."""
    result = hold_probe(tmp_path, FLAPPING_BEAT, beats=6)

    assert "no contact with the server since" in result["out"]
    assert "The server can see this machine again." in result["out"]
    assert "the server is answering again (after 2 missed heartbeats)" \
        in result["log"]
    assert result["out"].index("no contact") \
        < result["out"].index("The server can see")


@native_tools
def test_a_failed_heartbeat_never_becomes_an_action():
    """מה שאסור: המכונה בעצירה על שגיאה ואינה מבצעת יותר כלום (#64).
    כישלון דופק הוא סיבה **לומר**, לא לאתחל, לנסות שוב או לכבות."""
    hold = (AGENT / "lib" / "hold.sh").read_text(encoding="utf-8")
    start = hold.index("hold_watch() {")
    watch = code_only(hold[start:hold.index("\n}\n", start)])   # #2068: hold_action (a person's button) is its own function
    for verb in ("reboot", "poweroff", "die_local", "run_restore"):
        assert verb not in watch, f"‏hold_watch עושה {verb} על כישלון דופק"
    # #2068: the only reboot / power-off in the hold is a press on the failure screen (fail.action)
    action = code_only(hold[hold.index("hold_action() {"):])
    assert 'fail.action' in action.split("case")[0], "hold_action פועל בלי לחיצה"


@native_tools
def test_the_hold_no_longer_swallows_the_beat():
    """הבליעה המשולשת עצמה — ‏stdout, ‏stderr ו-`|| true` בשורה אחת."""
    ui = (AGENT / "lib" / "ui.sh").read_text(encoding="utf-8")
    hold = ui[ui.index("ui_error_hold() {"):]
    hold = code_only(hold[:hold.index("\n}\n")])
    assert '> /dev/null 2>&1 || true' not in hold
    assert "hold_watch" in hold


# --- #133: עצירה בלי דופק, כי הדווח מעולם לא הופעל ---------------------------


def error_hold_call_sites() -> list[tuple[str, int, str, int]]:
    r"""כל קריאה ל-`ui_error_hold` בסוכן: קובץ, שורה, הטקסט, ומספר הארגומנטים.

    שורות המשך (`\` בסוף) מאוחדות קודם — ב-`hold.sh` הדופק יושב בשורה
    שאחרי הקריאה, וסריקה שורה-שורה הייתה מדווחת עליו כחסר.
    ההגדרה עצמה אינה קריאה.
    """
    import shlex

    found = []
    for path in sorted(AGENT.rglob("*")):
        if not path.is_file() or path.suffix not in ("", ".sh"):
            continue
        raw = path.read_text(encoding="utf-8").splitlines()
        joined, buf, start = [], "", 0
        for n, line in enumerate(raw, 1):
            if not buf:
                start = n
            buf += line.rstrip()
            if buf.endswith("\\"):
                buf = buf[:-1] + " "
                continue
            joined.append((start, buf.strip()))
            buf = ""
        for n, line in joined:
            if not line.startswith("ui_error_hold") or line.startswith("ui_error_hold()"):
                continue
            try:
                argc = len(shlex.split(line)) - 1
            except ValueError:                       # ציטוט שאינו נסגר בשורה
                argc = -1
            found.append((path.name, n, line, argc))
    return found


def test_every_error_hold_in_the_agent_carries_a_heartbeat():
    """‏#133, ובאותה נשימה כל מקרה עתידי מאותה מחלקה.

    ‏`ui_error_hold` בלי ארגומנט שני היא עצירה **בלי דופק**: ‏`last_seen`
    נכתב ב-hello ובו בלבד, ואחרי `AWAKE_SECONDS` הקונסולה מציירת את
    המכונה ככבויה — בזמן שהיא עומדת דלוקה עם הודעת השגיאה שהטכנאי בא
    לקרוא. זה #64, שנסגר, וחזר.

    במסלול המשיכה ההערה בקוד טענה שהדווח "מושאר רץ בכוונה" — **וזה נכון
    רק כש-`pull_open` הצליחה.** כשהיא נכשלה `_ppid` ריק, ‏`progress_loop`
    מעולם לא הופעל, ואין מה להשאיר. מכונה שנכשלה גם בפתיחה וגם בשחזור
    הייתה שקופה לשרת לחלוטין — עם דיסק במצב לא ידוע.

    הבדיקה היא על **כל** אתרי הקריאה ולא על אחד, כי זו הפעם השלישית
    שהמחלקה הזאת חוזרת: ‏#64 (בלי דופק), ‏#109 (דופק שנבלע), וזה.
    """
    sites = error_hold_call_sites()
    assert sites, "לא נמצאה אף קריאה — הבדיקה מודדת את עצמה ולא את הסוכן"
    without = [s for s in sites if s[3] < 2]
    assert not without, (
        "עצירת שגיאה בלי דופק:\n"
        + "\n".join(f"  {f}:{n}  {line}" for f, n, line in without)
    )


# --- ‏#855: הדיווח על משימה נושא את אסימון המשימה --------------------------


@native_tools
def test_the_capture_report_carries_the_task_token(tmp_path, reports):
    """השרת דורש מ-#855 את `X-Imagectl-Task-Token` גם על `/progress` של
    משימה — אותה כותרת שההעלאה כבר שולחת (`capture.sh`). בלי זה השרת
    עונה 403, ‏`report_final` רואה כישלון, ומחשב הבנייה נעצר על
    "השרת לא אישר" אחרי קליטה שהצליחה. הבקרה השלילית: על `progress.sh`
    שלפני התיקון הכותרת חסרה והטסט נופל כאן."""
    run_capture(tmp_path, url_of(reports))

    assert reports.received and reports.received[-1]["task_id"] == TASK
    sent = reports.headers[-1].get("x-imagectl-task-token")
    assert sent == TOKEN, f"הדיווח נשלח בלי אסימון המשימה: {reports.headers[-1]}"
