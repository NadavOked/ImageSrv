"""מסך הכישלון האחיד של הגואי הנייטיבי (#1569; המוקאפ שנדב אישר ב-PR #1550).

הכרעת נדב 17/10: אין מסך לא-גרפי ואין מסך שחור. עד כאן כל עצירה הייתה
‏`ui_error_hold` — אנגלית על tty, מוסתרת מאחורי גואי חי או ריבועים
בלעדיו — ו-`die_local` היה שורת FATAL ושלוש שניות. עכשיו שתי הכניסות
האלה קוראות ל-`fail_screen` (‏`agent/lib/gui/failscreen.sh`), שהופכת את
שורת האנגלית שכבר יש להן לרשומות `fail*` של מסך עברי אחד
(‏`native-gui/src/screens_failure.c`): כותרת, "מה קרה" עם הסיבה של היעד,
"מה עושים עכשיו", ופרטים טכניים מתקפלים.

שלוש שכבות של בדיקות:

* **התנהגות הסוכן** — ‏bash על הפונקציות האמיתיות (‏`ui_error_hold`,
  ‏`die_local`, ‏`gui_hello_state`), מה שנכתב ל-`$RUN_DIR/fail.state` ומה
  שגואי מזויף קיבל. ‏`failscreen.sh` נטען **רק אם הוא קיים**, כדי שבקרה
  שלילית תיפול על ההתנהגות (אין רשומות) ולא על קובץ חסר.
* **חוזי מקור** — הניתוב, הפרסר, רשימת הכרטיסים.
* **רינדור** — ‏`requires_native`: נבנה ומצויר על מעבדת ה-VM בלבד.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix
from test_cloner_gui import _build_gui, _count_color, _pkgconfig, _png, _png_rgb, _token
from agentlib import flat_lib, lib_path

SRC = REPO / "native-gui" / "src"
MAC = "3C:52:82:A1:00:1F"

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")

GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

#: הגואי המזויף: רושם מה קיבל ומה היה בקובץ המצב ברגע ההפעלה, ויוצא.
GUI_STUB = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$*" > "$RUN_DIR/gui.args"\n'
    'cat "$RUN_DIR/gui/failed.state" > "$RUN_DIR/gui.seen"\n'
)


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def _lib(name: str) -> str:
    return (lib_path(name)).read_text(encoding="utf-8")


def records(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out.append((k, v))
    return out


def values(recs, key: str) -> list[str]:
    return [v for k, v in recs if k == key]


def run_agent(tmp_path: Path, script: str, *, targets: dict[str, tuple[str, str]] | None = None,
              env: str = "", gui: bool = False, disks: dict[str, int] | None = None) -> dict:
    """מריץ קטע סוכן אמיתי עם RUN_DIR משלו. ‏targets: dev -> (state, error);
    ‏disks: dev -> sectors, ב-SYSROOT מזויף (תא "דיסק מקור · פנימי · NGB")."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    sysroot = tmp_path / "sys"
    for dev, sectors in (disks or {}).items():
        d = sysroot / "sys" / "block" / dev
        d.mkdir(parents=True, exist_ok=True)
        (d / "size").write_text(f"{sectors}" + chr(10), encoding="utf-8")
        (d / "removable").write_text("0" + chr(10), encoding="utf-8")
        (d / "device").mkdir(exist_ok=True)   # #1909: an internal disk hangs somewhere (disk_internal)
    for dev, (state, error) in (targets or {}).items():
        d = run / "targets" / dev
        d.mkdir(exist_ok=True)
        (d / "state").write_text(state + "\n", encoding="utf-8")
        if error:
            (d / "error").write_text(error + "\n", encoding="utf-8")
    gui_env = ""
    if gui:
        stub = tmp_path / "gui-stub"
        stub.write_text(GUI_STUB, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
        gui_env = f"FAIL_GUI_BIN={shlex.quote(posix(stub))} "
    libs = "".join(f". {posix(flat_lib())}/{n}; "
                   for n in ("common.sh", "sysinfo.sh", "jsonq.sh", "progress.sh", "guistate.sh", "hold.sh", "ui.sh", "failscreen.sh")
                   if (lib_path(n)).exists())
    full = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC} IP=10.44.0.82 IMAGESRV_TEST=1 "
        f"SYSROOT={shlex.quote(posix(sysroot))} AGENT_VERSION_FILE=/nonexistent FAIL_GUI_BIN=/nonexistent LOG_FILE= {gui_env}{env}; "
        + libs
        + 'LOG_FILE="$RUN_DIR/agent.log"; '
        # בלי jq בתחנה: השם נופל ל-MAC. המיפוי של המגירות -- כמו slotnames.sh היה עונה.
        + 'json_get() { echo null; }; gui_backend_args() { echo --backend mem; }; disk_port() { echo 0; }; '
        + 'disk_drawer() { case "$1" in sda) echo "1|SATA 0";; sdb) echo "2|SATA 1";; sdc) echo "3|SATA 2";; *) echo "0|";; esac; }; '
        + script + '; echo "RETURNED rc=$?"; wait'
    )
    out_file = tmp_path / "out.txt"
    with out_file.open("w", encoding="utf-8") as sink:
        subprocess.run([BASH, "-c", full], stdout=sink, stderr=subprocess.STDOUT, text=True,
                       stdin=subprocess.DEVNULL, cwd=str(REPO), timeout=60)
    return {"out": out_file.read_text(encoding="utf-8"), "recs": records(run / "fail.state"),
            "run": run, "seen": records(run / "gui" / "failed.state")}


# --- הסוכן: מה נכתב ----------------------------------------------------------------


def test_a_failed_capture_hold_becomes_a_hebrew_screen_with_the_reason(tmp_path):
    """הכניסה האמיתית: `ui_error_hold "capture did not complete" hold_beat` (task.sh:73).
    הכותרת בעברית, הסיבה של היעד בתוך "מה קרה", שלושה צעדים, ושורת
    האנגלית המקורית נשמרת -- בתת-הכותרת ובפרטים הטכניים."""
    r = run_agent(tmp_path, 'echo uploading > "$RUN_DIR/state"; echo 4096 > "$RUN_DIR/targets/sdb/total"; '
                            # #1657: what was read is the bytes.<N> counters target_bytes sums, not `base`
                            'echo 3900 > "$RUN_DIR/targets/sdb/bytes.0"; ui_error_hold "capture did not complete" hold_beat',
                  targets={"sdb": ("failed", "upload rc=22 (HTTP 503) -> manifest upload failed")},
                  env="D_ROLE=build", disks={"sdb": 976773168})
    assert "TEST-HOLD" in r["out"], r["out"]                       # ה-hold שמתחת לא השתנה
    assert values(r["recs"], "fail") == ["bad|1|הקליטה נכשלה|קליטת אימג׳ חדש|build"], r["recs"]
    # owner 17/10: no English on the screen -- the raw line lives in the technical lines only
    assert values(r["recs"], "fail_why") == ["הדיסק נקרא, אבל ההעלאה לשרת לא הסתיימה, ולכן האימג׳ לא נכנס לספרייה."]
    for k, v in r["recs"]:
        if k != "fail_code":
            assert "upload rc" not in v and "manifest" not in v, (k, v)
    assert values(r["recs"], "fail_row") == [], "קליטה מדיסק אחד: אין טבלת מגירות (המוקאפ)"
    assert ("fail_ctx", "שלב|העלאה לשרת · 95%") in r["recs"], r["recs"]
    assert ("fail_ctx", "דיסק מקור|פנימי · 500GB") in r["recs"], r["recs"]
    assert values(r["recs"], "fail_safe") == ["הדיסק של המחשב הזה לא שונה — קליטה רק קוראת ממנו. אפשר להתחיל מחדש."]
    assert len(values(r["recs"], "fail_step")) == 1   # #2068: one sentence ...
    assert values(r["recs"], "fail_btn") == ["retry|נסה שוב", "off|כבה את המחשב"]   # ... and the buttons
    assert "[agent] ui_error_hold: capture did not complete" in values(r["recs"], "fail_code")
    assert "targets/sdb/error upload rc=22 (HTTP 503) -> manifest upload failed" in values(r["recs"], "fail_code")
    assert ("fail_ctx", f"מכונה|{MAC}") in r["recs"]              # בלי jq: ה-MAC ולא שם מומצא
    assert "failure screen: capture" in (r["run"] / "agent.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("english,title,tone", [
    ("capture did not complete, and the server was not told", "הקליטה נכשלה — והשרת לא יודע", "warn"),
    ("direct deployment did not complete, and the server was not told", "השליחה הישירה נכשלה — והשרת לא יודע", "warn"),
    ("the work finished but the server never confirmed it", "המשימה הסתיימה — והשרת לא אישר", "warn"),
    ("direct deployment did not complete", "השליחה הישירה נכשלה", "bad"),
    ("restore did not complete", "השחזור נכשל", "bad"),
    ("the disk was skipped -- nothing written", "הדיסק דולג — לא נכתב דבר", "bad"),
    ("something nobody wrote a case for", "המשימה נכשלה", "bad"),
])
def test_every_hold_line_has_a_hebrew_title_and_the_unheard_ones_are_orange(tmp_path, english, title, tone):
    """מקרה 6ב׳ במוקאפ: "השרת לא יודע" הוא כתום, לא אדום -- "לא הצלחנו לדווח"
    אינו "דיווחנו ונכשל" (עיקרון 5). שורה שאין לה מקרה מקבלת כותרת כללית
    ואת האנגלית בפרטים -- לא מסך שחור."""
    r = run_agent(tmp_path, f'ui_error_hold {shlex.quote(english)} hold_beat',
                  targets={"sda": ("failed", "x")})
    fail = values(r["recs"], "fail")
    assert fail and fail[0].startswith(f"{tone}|1|{title}|"), (fail, r["out"])
    assert english not in fail[0] and "[agent] ui_error_hold: " + english in values(r["recs"], "fail_code")
    if tone == "warn" and "never confirmed" not in english:
        assert "גם הדיווח על כך לא הגיע לשרת" in values(r["recs"], "fail_why")[0]


def test_a_cloner_gets_a_row_per_drawer_by_the_mapping(tmp_path):
    """מקרים 1–2 במוקאפ: שורה לכל מגירה, לפי המיפוי ("מגירה N · דיסק N"),
    עם הסיבה של אותו יעד והפיל שלו -- "דולג" כתום, "נכשל" אדום."""
    r = run_agent(tmp_path, 'ui_error_hold "all drawers skipped" hold_beat', env="D_ROLE=cloner D_SESSION_ID=ses_7f3a",
                  targets={"sda": ("skipped", "המפעיל דילג (FAILED: סקטורים מוחלפים 212)"),
                           "sdb": ("skipped", "המפעיל דילג (אזהרה: CRC 812)"), "sdc": ("failed", "write: short write")})
    assert values(r["recs"], "fail") == ["bad|1|כל המגירות דולגו — לא נכתב דבר|סבב · מחשב שיכפול|cloner"]
    rows = values(r["recs"], "fail_row")
    # a Hebrew reason is kept as it is; an English one is said in Hebrew (the raw line: fail_code)
    assert rows == ["מגירה 1 · דיסק 1|המפעיל דילג (FAILED: סקטורים מוחלפים 212)|wn|דולג",
                    "מגירה 2 · דיסק 2|המפעיל דילג (אזהרה: CRC 812)|wn|דולג",
                    "מגירה 3 · דיסק 3|הדיסק איבד בייטים בזמן הכתיבה|bad|נכשל"], rows
    assert "targets/sdc/error write: short write" in values(r["recs"], "fail_code")
    assert ("fail_ctx", "מגירות|3 מתוך 3 נכשלו או דולגו") in r["recs"]
    assert "session ses_7f3a" in values(r["recs"], "fail_code")


def test_a_pipe_or_newline_in_a_reason_cannot_break_a_record(tmp_path):
    r = run_agent(tmp_path, 'ui_error_hold "restore did not complete" hold_beat',
                  targets={"sda": ("failed", "a|b\nsecond line")})
    for k, v in r["recs"]:
        assert v.count("|") == (4 if k == "fail" else 3 if k == "fail_row" else 1 if k in ("fail_ctx", "fail_btn") else 0), (k, v)
    assert "targets/sda/error ab" in values(r["recs"], "fail_code")   # הקו נמחק (כמו #1548), לא שובר שדה


def test_die_local_shows_the_hebrew_reason_with_a_reboot_count_then_reboots(tmp_path):
    """מקרה 5 בדוח: סירוב אחרי ה-handoff ולפני הכתיבה. היה `FATAL` באנגלית
    ושלוש שניות; עכשיו המסך עם הסיבה בעברית ו"לא נכתב דבר", ‏`fail_at`
    (הפיל סופר), ורק אז האתחול -- שבמצב טסט הוא `TEST-REBOOT`."""
    r = run_agent(tmp_path, 'die_local "restore: not authorized -- nothing was written"', env="D_ROLE=build")
    assert "TEST-REBOOT: restore: not authorized -- nothing was written" in r["out"]
    assert values(r["recs"], "fail") == ["bad|0|המחשב נעצר — מאתחל|השרת מחליט מאיפה האתחול הבא|build"]
    assert values(r["recs"], "fail_why") == ["השחזור לא אושר מול השרת — כניסת הקונסולה של המסך הזה כבר אינה בתוקף. לא נכתב דבר."]
    assert values(r["recs"], "fail_safe") == ["לא נכתב דבר לדיסק."]
    at = values(r["recs"], "fail_at")
    assert len(at) == 1 and at[0].isdigit() and int(at[0]) > 1_700_000_000, at
    assert "[agent] die_local: restore: not authorized -- nothing was written" in values(r["recs"], "fail_code")


@pytest.mark.parametrize("english,he", [
    ("restore: image is no longer allowed for this machine", "האימג׳ שנבחר כבר אינו מותר למחשב הזה"),
    ("restore: malformed image id from the GUI", "מזהה האימג׳ שהגיע מהמסך פגום"),
    ("restore: manifest fetch failed", "לא ניתן היה לקרוא מהשרת את פרטי האימג׳"),
    ("no internal disk found", "לא נמצא דיסק פנימי"),
    ("server unreachable", "השרת לא ענה"),
    # #1861: an unmapped line is said without its English -- the line itself is in fail_code
    ("a line nobody translated", "המחשב נעצר בשלב שאין לו הסבר כאן"),   # #2068: no details box to point at
])
def test_every_known_fatal_line_has_hebrew_and_an_unknown_one_still_gets_a_screen(tmp_path, english, he):
    r = run_agent(tmp_path, f'die_local {shlex.quote(english)}')
    assert "TEST-REBOOT" in r["out"]
    assert he in values(r["recs"], "fail_why")[0], r["recs"]


# --- הסוכן: איך זה מגיע למסך ---------------------------------------------------------


def test_a_live_kiosk_gets_the_block_through_its_own_state_writer(tmp_path):
    """קיוסק חי (`_gui_pid`): לא מרימים גואי שני על אותו framebuffer. כותב
    המצב של הקיוסק -- ‏`gui_hello_state`, שכל מסך קורא -- מצרף את הבלוק."""
    r = run_agent(tmp_path, '_gui_pid=$$; ui_error_hold "capture did not complete" hold_beat; '
                            'gui_hello_state > "$RUN_DIR/hello.out"', targets={"sda": ("failed", "x")}, gui=True)
    assert not (r["run"] / "gui.args").exists(), "גואי שני הופעל ליד קיוסק חי"
    hello = (r["run"] / "hello.out").read_text(encoding="utf-8")
    assert "fail=bad|1|הקליטה נכשלה|קליטת אימג׳ חדש|" in hello
    assert "hello_age=" in hello and "fail_code=[agent] ui_error_hold: capture did not complete" in hello


def test_without_a_kiosk_a_display_only_gui_is_started_on_the_1548_file(tmp_path):
    """בלי קיוסק: גואי לתצוגה בלבד על `$RUN_DIR/gui/failed.state` -- הקובץ
    של #1548 -- עם הכותרת (מכונה/שרת/גרסאות), ה-hello והבלוק; מחשב שיכפול
    מקבל `--screen cloner` כדי שהכותרת תאמר "מחשב שיכפול"."""
    r = run_agent(tmp_path, 'ui_error_hold "no drawer completed" hold_beat', env="D_ROLE=cloner",
                  targets={"sda": ("failed", "write: I/O error")}, gui=True)
    args = (r["run"] / "gui.args").read_text(encoding="utf-8")
    assert f"--state {posix(r['run'])}/gui/failed.state" in args and "--backend mem" in args, args
    assert f"--mac {MAC}" in args and "--screen cloner" in args, args
    seen = records(r["run"] / "gui.seen")
    assert values(seen, "fail") == ["bad|1|אף מגירה לא הסתיימה בהצלחה|סבב · מחשב שיכפול|cloner"]
    assert ("agent_version", "unknown") in seen and "hello_age" in [k for k, _ in seen]
    assert len(values(seen, "fail")) == 1, "הבלוק נכתב פעמיים"


def test_a_build_machine_gui_is_started_without_the_cloner_screen(tmp_path):
    r = run_agent(tmp_path, 'ui_error_hold "capture did not complete" hold_beat', env="D_ROLE=build",
                  targets={"sda": ("failed", "x")}, gui=True)
    args = (r["run"] / "gui.args").read_text(encoding="utf-8")
    assert "--screen" not in args, args


def test_the_1548_display_gui_is_reused_not_doubled(tmp_path):
    """‏`single_restore_failed_screen` (#1548) כבר העלה גואי על `failed.state`
    (‏`_sf_pid`); המסך האחיד נכתב לאותו קובץ והגואי ההוא מחליף תמונה."""
    r = run_agent(tmp_path, 'sleep 20 & _sf_pid=$!; ui_error_hold "restore did not complete" hold_beat; sleep 1; '
                            'cat "$RUN_DIR/gui/failed.state" > "$RUN_DIR/after.state"; kill $_sf_pid',
                  targets={"sda": ("failed", "sgdisk דחה את תוכנית השחזור — הדיסק לא שונה")}, gui=True)
    assert not (r["run"] / "gui.args").exists(), "גואי שני הופעל ליד הגואי של #1548"
    after = records(r["run"] / "after.state")
    assert values(after, "fail") == ["bad|1|השחזור נכשל|שחזור אימג׳ למחשב הזה|"]
    assert "sgdisk דחה את תוכנית השחזור — הדיסק לא שונה" in values(after, "fail_why")[0]   # already Hebrew: kept


def test_no_gui_binary_means_the_records_are_still_written_and_the_hold_goes_on(tmp_path):
    r = run_agent(tmp_path, 'ui_error_hold "capture did not complete" hold_beat', targets={"sda": ("failed", "x")})
    assert "TEST-HOLD" in r["out"] and values(r["recs"], "fail")


# --- חוזי מקור -------------------------------------------------------------------------


def test_the_two_entry_points_call_the_screen_and_the_agent_loads_it():
    ui = _lib("ui.sh")
    hold = ui[ui.index("ui_error_hold() {"):]
    assert 'fail_screen hold "$1"' in hold.split("ui_clear")[0], "ui_error_hold לא מצייר לפני הטקסט"
    common = _lib("common.sh")
    die = common[common.index("die_local() {"):common.index("\n}\n", common.index("die_local() {"))]
    assert 'fail_fatal "$1"' in die and die.index("fail_fatal") < die.index("sleep 3")
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/failscreen.sh"' in agent
    assert 'cat "$RUN_DIR/fail.state"' in _lib("guistate.sh")


def test_the_unknown_machine_is_not_a_failure_and_draws_its_own_records():
    """‏#1550 השאיר את "מכונה לא מוכרת" פתוחה; ‏#1802 (נדב 19/10) הכריע: מקרה 8
    במוקאפ, ‏tone ‏inf. היא אינה כישלון ולכן אינה עוברת ב-`fail_screen` (שם
    הכותרת אדומה) -- ‏`ui_unknown` כותב את הרשומות ומציג אותן ב-`fail_show`."""
    assert "TODO (#1550" not in _lib("failscreen.sh")
    ui = _lib("ui.sh")
    unknown = ui[ui.index("ui_unknown() {"):ui.index("ui_error_hold() {")]
    assert "fail_screen" not in unknown and "fail_show" in unknown


def test_the_native_side_routes_parses_and_renders_the_block():
    screens = _src("screens.c")
    route = screens[screens.index("void app_route(App *a){"):]
    route = route[:route.index("\n}\n")]
    assert route.index("SCREEN_FAILURE") < route.index("SCREEN_MESSAGE"), "הכישלון אינו לפני message="
    assert "case SCREEN_FAILURE:  screen_failure(" in screens
    assert "case SCREEN_FAILURE:return failure_status_word(a,dot);" in screens
    state = _src("state.c")
    block = state[state.index('KEY("fail")'):state.index('KEY("fail_why")')]
    assert "s->nfail_ctx = s->nfail_rows = s->nfail_steps = s->nfail_code = s->nfail_btns = 0;" in block, "fail= אינו מאפס את הרשימות"
    for key in ("fail_why", "fail_safe", "fail_at", "fail_ctx", "fail_row", "fail_step", "fail_code", "fail_btn"):
        assert f'KEY("{key}")' in state, key
    main = _src("main.c")
    assert '"failure",' in main and '"failure-open"' not in main   # #2068: the details box went
    assert 'power_drop(a, "fail.action", a->st.fail_btns[b].action)' in main   # the buttons: the agent acts (hold.sh)
    assert "src/screens_failure.c" in (REPO / "native-gui" / "Makefile").read_text(encoding="utf-8")
    failure = _src("screens_failure.c")
    for text in ("מה קרה", "מה אפשר לעשות", "המחשב נשאר דלוק — לא ייכבה לבד", "אין קשר עם השרת"):
        assert text in failure, text
    assert "פרטים טכניים" not in failure and "טכנאי" not in failure   # #2068


def test_the_records_are_documented_where_the_state_file_is():
    for doc in (REPO / "docs" / "interfaces.md", REPO / "native-gui" / "README.md"):
        text = doc.read_text(encoding="utf-8")
        for key in ("fail=", "fail_why=", "fail_row=", "fail_code=", "fail_at="):
            assert key in text, (doc.name, key)


# --- רינדור (מעבדה) --------------------------------------------------------------------


def _state_for_png(tmp_path: Path, english: str, hello_rc: str = "200") -> Path:
    r = run_agent(tmp_path, f'ui_error_hold {shlex.quote(english)} hold_beat',
                  targets={"sda": ("failed", "write: I/O error, dev sda, sector 2152448")}, env="D_ROLE=build")
    assert values(r["recs"], "fail"), r["out"]
    state = tmp_path / "state.txt"
    state.write_text((r["run"] / "fail.state").read_text(encoding="utf-8")
                     + f"message=x|y\nhello_age=4\nhello_rc={hello_rc}\n", encoding="utf-8", newline="\n")
    return state


@GUI_BUILD
@pytest.mark.parametrize("size", ["1920x1080", "1024x768"])
def test_the_failure_screen_renders_in_both_themes_with_the_badge(tmp_path, size):
    """הרשומות שהסוכן האמיתי כתב, מצוירות: התג האדום (60×60 ב---clr-danger-soft),
    שתי הערכות. ‏#2068: אין עוד כרטיס "פתוח" -- הפרטים הטכניים ירדו."""
    binary = _build_gui(tmp_path)
    state = _state_for_png(tmp_path, "capture did not complete")
    _png(binary, tmp_path / "card", state, size=size)
    for th in ("light", "dark"):
        assert (tmp_path / f"card-failure-{th}.png").exists() and not (tmp_path / f"card-failure-open-{th}.png").exists()
        w, h, ch, px = _png_rgb(tmp_path / f"card-failure-{th}.png")
        # tol=4: --clr-danger-soft (#FCEDEA) is 3/18/21 from white -- the default tolerance counts the whole surface
        assert _count_color(px, ch, _token("--clr-danger-soft", th), tol=4) > 1500, f"אין תג אדום ({th}, {size})"


@GUI_BUILD
def test_the_unheard_variant_renders_an_orange_badge(tmp_path):
    binary = _build_gui(tmp_path)
    state = _state_for_png(tmp_path, "capture did not complete, and the server was not told", hello_rc="0")
    _png(binary, tmp_path / "card", state)
    w, h, ch, px = _png_rgb(tmp_path / "card-failure-light.png")
    assert _count_color(px, ch, _token("--clr-warning-soft"), tol=4) > 1500
    # the red is only the rows' "נכשל" pills now, not the 60x60 badge
    assert _count_color(px, ch, _token("--clr-danger-soft"), tol=4) < 1500
