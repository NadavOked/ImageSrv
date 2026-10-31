"""הדיווח האחרון של עבודה נשלח ונקרא, לא נשלח ונשכח — ‏issue #101.

‏`progress.sh` הצהיר על הכלל הנכון בהערה שלו, ויישם אותו במקום אחד
מתוך שלושה: ‏`pull_close` שלח את דיווח הסיום סינכרונית ובדק את קוד
היציאה, בעוד **מסלול סבב הכיתה ומסלול המגירות** הוציאו אותו דרך לולאת
ה"שלח ושכח" והמתינו ב-`sleep 6`. שש שניות אינן ראיה: עם
‏`--max-time 10 --retry 3` הן לא מחזיקות בהכרח אפילו ניסיון אחד שהושלם.

המחיר של הניחוש הזה הוא לא שורה חסרה במסך. מכונה שסיימה שחזור ושה-`done`
שלה לא הגיע נשארת `session_members.done = 0`, הסבב נשאר `running`,
וה-hello הבא שלה נענה באותו שחזור — **לולאת שחזור**, בדיוק תרחיש ה-QA
הקריטי שב-`CLAUDE.md`.

מה שנבדק כאן:

* **ראיה חיובית** — ‏`report_final` מחזיר 0 רק כשהשרת ענה 200 (`curl -sfS`
  הופך 400 לקוד יציאה), ועוצר את לולאת הדיווח לפני הדיווח האחרון כדי
  שיהיה כותב אחד ל-`progress.json`.
* **תקרת ניסיונות ולא תקרת שניות** — כל ניסיון הוא curl שלם עם ה-retry
  שלו, ולכן ספירת שניות כאן הייתה ניחוש שני.
* **הבקרה השלילית** — שני המסלולים **אינם ממשיכים** לאתחל/לכבות בלי
  תשובה. על הקוד שלפני התיקון הבדיקות האלה נופלות: הוא ישן שש שניות
  והמשיך.
* **למה זה חשוב** — הצד השרתי, שכבר עובד: מי שדיווח `done` לא מקבל את
  הסבב שוב, ומי שלא דיווח — כן.

הפלט של כל הרצה הולך לקובץ ולא ל-PIPE, ולולאת הדיווח מזויפת בכל מסלול
שנעצר: תהליך רקע ששורד את הריצה מחזיק את הצינור, ואז ההמתנה עליו נראית
כמו באג בקוד שנבדק (גוטצ'ה מוכרת ב-`CLAUDE.md`).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from agentlib import flat_lib, lib_path

REPO = Path(__file__).resolve().parent.parent

#: ‏curl הוא המנגנון שנבדק — קוד היציאה שלו *הוא* הראיה החיובית — ו-jq
#: קורא את תשובת השרת שממנה נגזרים מזהה הסבב והאימג'. בלעדיהם אין מה
#: לבדוק, ובמקום שבו הם אמורים להיות זה כישלון ולא דילוג (#52).
native_tools = requires_native(("bash", BASH), "curl", "jq")

MAC = "b4:2e:99:07:1a:c4"
SESSION = "ses_a91f"
IMAGE = "img_7f3a91"

MANIFEST = {"id": IMAGE, "name": "Windows 11 Lab", "family": 256,
            "total_compressed_bytes": 40960}
ANSWER = {"schema": 1, "known": True, "role": "classroom",
          "session": {"id": SESSION, "state": "running", "image_id": IMAGE,
                      "prefix": "LAB1",
                      # ‏#1410: סבב חדר בלי בחירת מגירות מפורשת נעצר ב-die_local
                      "target_ports": [1, 2]},
          "group": {"suffix": "05"}}

#: תקרה על הריצה כולה — רשת ביטחון ולא כוונון תזמון. הדרך היחידה להגיע
#: אליה היא תהליך שלא נסגר, וזה באג שצריך להיראות בשמו ולא כתלייה.
RUN_TIMEOUT_S = 90


# --- שרת אמיתי בתהליכון, שאפשר להגיד לו לסרב --------------------------------


class Reports(HTTPServer):
    """מקבל דיווחים ומחזיר את מה שהוגדר לו.

    ‏`refuse_first` הוא ההשהיה של האיסיו בצורתה המדידה: השרת לא עונה
    כמו שצריך על N הדיווחים הראשונים ואז חוזר לעצמו. ‏`refuse_first`
    שלילי = לא עונה כמו שצריך אף פעם.
    """

    refuse_first = 0
    received: list[dict] = []
    #: כותרות כל בקשה, באותו סדר כמו `received` (#855: האסימון על הדיווח).
    headers: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:                       # noqa: N802 — שם של BaseHTTP
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.server.headers.append({k.lower(): v for k, v in self.headers.items()})
        try:
            self.server.received.append(json.loads(raw))
        except ValueError:
            self.server.received.append({"unparsable": raw.decode("utf-8", "replace")})
        seen = len(self.server.received)
        refuse = self.server.refuse_first < 0 or seen <= self.server.refuse_first
        body = b'{"ok":false,"code":"nope"}' if refuse else b'{"ok":true}'
        self.send_response(503 if refuse else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """בלי רעש ל-stderr של הריצה."""


@pytest.fixture
def reports():
    httpd = Reports(("127.0.0.1", 0), Handler)
    httpd.received = []
    httpd.headers = []
    httpd.refuse_first = 0
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def url_of(httpd: HTTPServer) -> str:
    host, port = httpd.server_address[:2]
    return f"http://{host}:{port}"


# --- הרצת הפונקציות של הסוכן הראשי ------------------------------------------
#
# ‏`imagesrv-agent` הוא סקריפט ולא ספרייה: ה-source שלו מריץ את הלולאה
# הראשית. הפונקציות נחתכות ממנו לפי הגבול שהקובץ עצמו מגדיר — הגדרה
# בעמודה 0 ו-`}` בעמודה 0 — ונטענות לבד. החיתוך נכשל בקול אם השם השתנה:
# בדיקה שלא מצאה את מה שהיא בודקת אינה בדיקה שעברה.


def cut_function(name: str) -> str | None:
    # ‏#1217: ‏`do_task` עבר ל-`lib/task.sh` (קיר 300). נחתך משם באותו גבול;
    # שהקובץ נטען בסוכן — ‏test_every_lib_file_is_loaded_and_packed.
    task = AGENT / "lib" / "core" / "task.sh"
    source = (AGENT / "imagesrv-agent").read_text(encoding="utf-8") \
        + (task.read_text(encoding="utf-8") if task.exists() else "")
    marker = f"\n{name}() {{\n"
    start = source.find(marker)
    if start == -1:
        return None
    end = source.find("\n}\n", start + len(marker))
    assert end != -1, f"‏{name} אינה נסגרת ב-}} בעמודה 0"
    return source[start + 1:end + 3]


def agent_functions(*names: str) -> str:
    """החיתוך שהבדיקות הסטטיות עומדות עליו: שם שאינו שם הוא כישלון."""
    chunks = []
    for name in names:
        body = cut_function(name)
        assert body is not None, f"‏{name} אינה מוגדרת בעמודה 0 ב-imagesrv-agent"
        chunks.append(body)
    return "\n".join(chunks)


def loadable_functions() -> str:
    """מה שנטען להרצה. ‏`hold_unheard` נלקחת רק אם היא קיימת — לא כדי
    לסלוח על היעדרה אלא ההפך: כך בקרה שלילית מול הקוד שלפני התיקון
    מריצה את **המסלול האמיתי** שלו ונופלת על ההתנהגות (המכונה אתחלה
    בלי אישור), ולא על קובץ שלא נחתך.

    מ-#109 ואילך `hold_unheard` ו-`hold_beat` יושבות ב-`agent/lib/core/hold.sh`,
    ולכן החיתוך מ-`imagesrv-agent` מחזיר `None` — ‏`sourced_libs` מביאה
    אותן משם. שתי הדרכים חיות זו לצד זו בכוונה: זה מה שמאפשר להריץ את
    אותו טסט בדיוק מול הקוד שלפני התיקון ומול זה שאחריו."""
    # capture_end_ask (#409) was removed on 11/10 -- GUI only.
    wanted = ("hold_unheard", "do_task",                       # task.sh
              "task_done_stays_on",                            # #1222: task.sh
              "build_end_ask",                                 # #1560: task.sh
              "do_restore_drawers", "do_restore")
    return "\n".join(body for body in (cut_function(n) for n in wanted)
                     if body is not None)


def sourced_libs() -> str:
    """ספריות הסוכן שהמסלולים צריכים. ‏`hold.sh` נטענת **רק אם היא
    קיימת**: בקרה שלילית ש-`git stash` הוריד בה את קוד הסוכן חייבת
    ליפול על ההתנהגות ולא על קובץ חסר."""
    names = ["common.sh", "waits.sh", "jsonq.sh", "progress.sh", "pull.sh",   # waits: capped_run (#1367)
             "streamid.sh",   # #1826: session_stream, between the manifest fetch and the write
             "ui.sh", "hold.sh", "clonergui.sh",   # #708: resolve_target_drawers (#701) lives here
             "smart.sh", "crcdelta.sh"]   # #872: crc_delta_after sits on the write line of do_restore
    names += ["attended.sh", "shrinkplan.sh"]   # attended beats; the capture's shrink plan
    names += ["captureview.sh", "hibernation.sh"]   # #2213: direct-send completion preserves the warning
    names += ["idle.sh"]   # #1560: end_countdown / end_failed_round -- the end of a cloner round
    names += ["verdict.sh"]   # #1935: _machine_state -- do_restore_drawers writes the final word itself
    return "".join(f". {posix(flat_lib())}/{n}; "
                   for n in names if (lib_path(n)).exists())


def journal(run: Path) -> str:
    path = run / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


#: הלולאה מזויפת בכל בדיקה של המסלולים: מה שנבדק שם הוא הדיווח האחרון,
#: וכל POST אמיתי שמגיע לשרת המזויף הוא שלו ושל אף אחד אחר. הזיוף גם
#: מונע לולאת רקע אינסופית ששורדת את הריצה — `hold_unheard` מרימה אחת.
#: ‏`$1$4` ולא `$1 $4`: מסלול הסבב מזוהה ב-session ומסלול הקליטה
#: ב-task, ותמיד בדיוק אחד מהם מלא (ממשק 4) — כך העקבה נושאת את המזהה
#: של שני המסלולים בלי רווח תלוי-מסלול בסוף השורה.
LOOP_STUB = 'progress_loop() { echo "LOOP $1$4" >> "$RUN_DIR/trace"; }; '

#: #2077: סוף משימה עם D_ROLE=classroom (`finish_and_stop` אחרי 'כבה')
#: קורא ל-`reboot -f` בלי שומר IMAGESRV_TEST. זיוף ב-PATH נכשל ורושם;
#: פקודה אמיתית אינה נמצאת לפניו. בלי קובץ היומן הזיוף נכשל גם הוא.
_POWER_STUB = r"""#!/bin/sh
if [ -z "${IMAGESRV_POWER_LOG:-}" ]; then
    echo "IMAGESRV_POWER_LOG is unset" >&2
    exit 126
fi
printf 'STUB %s %s\n' "$(basename "$0")" "$*" >> "$IMAGESRV_POWER_LOG" || exit 126
exit 127
"""

POWER_COMMANDS = ("reboot", "poweroff", "safe_poweroff")


def install_power_stubs(directory: Path) -> None:
    """שלוש פקודות הכיבוי, כסקריפטים כושלים בראש ה-PATH של `run_path`."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in POWER_COMMANDS:
        (directory / name).write_text(_POWER_STUB, encoding="utf-8", newline="\n")


def _power_stub_prefix(tmp_path: Path) -> str:
    """PATH שהזיוף ראשון בו, והנתיב ש-`type -P` יחזיר — מ-`pwd` של bash."""
    stub_dir = tmp_path / "power-stubs"
    install_power_stubs(stub_dir)
    return (
        f'_ps=$(cd {shlex.quote(posix(stub_dir))} && pwd) || '
        '{ echo "POWER-STUB-DIR-UNREADABLE"; exit 96; }; '
        f'_pl=$(cd {shlex.quote(posix(tmp_path))} && pwd) || '
        '{ echo "POWER-LOG-DIR-UNREADABLE"; exit 96; }; '
        'chmod 0755 "$_ps"/reboot "$_ps"/poweroff "$_ps"/safe_poweroff || '
        '{ echo "POWER-STUB-CHMOD-FAILED"; exit 96; }; '
        'export IMAGESRV_POWER_STUB_DIR="$_ps" IMAGESRV_POWER_LOG="$_pl/power.calls" '
        'PATH="$_ps:/usr/bin:$PATH"; '
    )

STUBS = (
    # ‏#652: הסוכן האמיתי טוען את smartgate.sh (imagesrv-agent), ולכן `smart_gate`
    # מוגדר שם. כאן, כמו `run_restore` ו-`progress_loop`, הוא מזויף כדי
    # לבודד את מסלול הדיווח מבדיקת ה-SMART: מעביר את כל הדיסקים הלאה
    # (בריאים) ומחזיר 0. ה-SMART עצמו נבדק ב-test_smart.py.
    'smart_gate() { shift; echo "$@"; }; '
    # #1808/#1898: the drawer questions after it (drawergate.sh) -- the same
    # isolation; their own tests are test_raid_drawer_1808 / test_head_share_1898.
    'drawer_gates() { echo "$@"; }; '
    'pick_internal_disk() { echo sda; }; '
    'list_drawers() { echo "sdb sdc"; }; '
    # ‏#1410: resolve_target_drawers ממפה כל פורט שנבחר לדיסק אחד בדיוק
    'disk_port() { case "$1" in sdb) echo 1 ;; sdc) echo 2 ;; esac; }; '
    'http_get() { cat "$RUN_DIR/manifest.src.json"; }; '
    'name_this_machine() { echo "NAMED" >> "$RUN_DIR/trace"; '
    'echo done > "$RUN_DIR/state"; }; '
    'hold_beat() { echo "BEAT" >> "$RUN_DIR/trace"; }; '
    'writemark_release_done() { :; }; '   # #1877: the mark is not what is tested here (test_writemark_postdeploy_1877.py)
    '_end() { [ "${RESTORE_RC:-0}" = "0" ] && echo done || echo failed; }; '
    'run_restore() { echo "RESTORE" >> "$RUN_DIR/trace"; '
    'target_set "$2" "$(_end)"; _end > "$RUN_DIR/state"; '
    'return "${RESTORE_RC:-0}"; }; '
    'run_restore_drawers() { echo "DRAWERS" >> "$RUN_DIR/trace"; '
    'for _t in sdb sdc; do target_set "$_t" "$(_end)"; done; '
    '_end > "$RUN_DIR/state"; return "${RESTORE_RC:-0}"; }; '
)

#: #2024: a round with a problem -- a failed round, or a done drawer whose read-back
#: is not recorded (the STUBS above write none) -- holds its results until "כבה",
#: no count and no ceiling. The hold's first tick starts the held beat (#2031,
#: hold.sh); standing in for it, "כבה" is pressed (end.now) and the hold ends at the
#: next tick, into finish_and_stop -- what a person at the machine does.
PRESS_OFF = 'held_beat_start() { echo "PRESSED" >> "$RUN_DIR/trace"; : > "$RUN_DIR/end.now"; }; '


def run_path(tmp_path: Path, server: str, call: str, *, tries: int = 2,
             restore_rc: int = 0, answer: dict | None = None,
             stubs: str = "", env: str = "") -> dict:
    """מריץ מסלול אחד של הסוכן ומחזיר מה שיצא ממנו.

    ‏`call` הוא הקריאה עצמה — ‏`do_restore`, ‏`do_restore_drawers ...`
    או `do_task`. ‏`answer` מחליף את תשובת השרת (מסלול הקליטה קורא
    ‏`.task.*` ולא `.session.*`), ‏`stubs` ו-`env` מוסיפים למסלול הזה
    בלבד. הפלט הולך לקובץ: תהליך רקע ששרד מחזיק צינור פתוח, וההמתנה
    עליו נראית כמו באג בקוד שנבדק ולא בכלי הבדיקה.

    ‏#2077: ‏`reboot`, ‏`poweroff` ו-`safe_poweroff` בראש ה-PATH הם זיוף
    שנכשל (127) ורושם ל-`power.calls`. סוף משימה של כיתה קורא ל-`reboot -f`.
    """
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(json.dumps(answer or ANSWER), encoding="utf-8")
    (run / "manifest.src.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    funcs = tmp_path / "agentfuncs.sh"
    funcs.write_text(loadable_functions(), encoding="utf-8")
    out_file = tmp_path / "out.txt"

    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"SERVER={shlex.quote(server)} "
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f"IMAGESRV_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=4 PROGRESS_INTERVAL_S=0.2 "
        f"FINAL_REPORT_TRIES={tries} FINAL_REPORT_GAP_S=0 D_ROLE=classroom "
        f"RESTORE_RC={restore_rc} {env}; "
        + sourced_libs()
        + STUBS
        + stubs
        + LOOP_STUB
        + f". {posix(funcs)}; "
        # ‏`wait` ולא `sleep`: ‏`hold_unheard` מרימה את הדווח ברקע, ופייתון
        # קורא את העקבה רק אחרי שכל תהליכי הרקע של המעטפת באמת יצאו.
        # במסלול המוצלח הוא לא נחוץ ולא נקרא — שם המעטפת יוצאת ב-exit 0.
        + f'{call}; echo "RETURNED rc=$?"; wait'
    )
    with out_file.open("w", encoding="utf-8") as sink:
        try:
            subprocess.run(
                [BASH, "-c", _power_stub_prefix(tmp_path) + script],
                stdout=sink, stderr=subprocess.STDOUT, text=True, cwd=str(REPO),
                stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"המסלול לא סיים תוך {RUN_TIMEOUT_S}s. הפלט עד כה:\n"
                + out_file.read_text(encoding="utf-8")
            ) from None
    return {
        "out": out_file.read_text(encoding="utf-8"),
        "log": journal(run),
        "trace": (run / "trace").read_text(encoding="utf-8").splitlines()
        if (run / "trace").exists() else [],
    }


HOLD = "FAILED: the work finished but the server never confirmed it"


# --- הבקרה השלילית: המסלולים אינם ממשיכים בלי תשובה --------------------------


@native_tools
def test_a_class_round_does_not_reboot_when_the_final_report_was_refused(
        tmp_path, reports):
    """**לב האיסיו, מסלול סבב הכיתה.**

    השרת אינו מאשר את דיווח הסיום. הקוד שלפני התיקון ישן שש שניות,
    הרג את הדווח ואתחל — והמכונה הזאת, שהשרת עדיין סופר אותה כמי שלא
    סיימה, הייתה מקבלת את אותו הסבב באתחול הבא ומשחזרת שוב.
    """
    reports.refuse_first = -1

    result = run_path(tmp_path, url_of(reports), "do_restore")

    assert "TEST-HOLD" in result["out"], "המכונה המשיכה בלי אישור מהשרת"
    assert HOLD in result["out"]
    assert "RETURNED rc=1" in result["out"]
    # הדיווח באמת נשלח, ולא פעם אחת בלבד — ראיה שהתקרה היא ניסיונות.
    assert len(reports.received) == 2
    assert reports.received[-1]["state"] == "done"
    assert "did not acknowledge the final report" in result["log"]


@native_tools
def test_the_cloning_room_powers_off_even_when_the_final_report_was_refused(
        tmp_path, reports):
    """נדב 17/10: מחשב שיכפול נכבה בכל מצב — גם כשה-`done` לא אושר.
    לפני כן המסלול נעצר כאן על מסך ההמתנה (‏hold_unheard), והמחשב נשאר
    דלוק גם אחרי שהשרת חזר לענות (ברזל, room_72308383). הדיסקים כתובים;
    הדיווח ממשיך ברקע עד הכיבוי, ואם השרת לא שמע — גל שלא נספר בו דבר
    נסגר אצלו כנכשל (#1672) ולא חוסם את הסבב הבא."""
    reports.refuse_first = -1

    result = run_path(tmp_path, url_of(reports),
                      f"do_restore_drawers {IMAGE} {SESSION}")

    assert "TEST-HOLD" not in result["out"], "המחשב נעצר על מסך ההמתנה"
    assert HOLD not in result["out"]
    assert "RETURNED rc=1" not in result["out"]
    assert "the done report was not confirmed" in result["log"]
    assert "drawers complete" in result["log"], "המסלול לא הגיע לכיבוי"
    assert reports.received[-1]["session_id"] == SESSION
    assert reports.received[-1]["state"] == "done"


@native_tools
def test_the_held_machine_stays_visible_instead_of_going_quiet(tmp_path, reports):
    """עצירה אינה שתיקה (#64): המסך נקוב בשם, הדופק ממשיך, והדווח
    מורם בחזרה כדי שה-`done` ינחת ברגע שהשרת יחזור לענות."""
    reports.refuse_first = -1

    result = run_path(tmp_path, url_of(reports), "do_restore")

    assert "Leave the computer on and contact IT." in result["out"]
    # ‏`hold_unheard` מרימה את הדווח מחדש — הזיוף רושם את הקריאה השנייה.
    assert result["trace"].count(f"LOOP {SESSION}") == 2


# --- הצד החיובי: עם אישור, וכשהוא מאחר -------------------------------------


@native_tools
def test_a_class_round_reboots_once_the_server_took_the_report(tmp_path, reports):
    """אישור הוא אישור: ‏200 → המכונה מסיימת את דרכה כרגיל."""
    result = run_path(tmp_path, url_of(reports), "do_restore")

    assert "TEST-HOLD" not in result["out"]
    assert "RETURNED" not in result["out"], "המסלול לא הגיע לאתחול"
    assert len(reports.received) == 1
    report = reports.received[0]
    assert report["session_id"] == SESSION and report["state"] == "done"
    assert report["targets"][0]["dev"] == "sda"
    assert report["targets"][0]["state"] == "done"


@native_tools
def test_the_final_report_is_retried_until_the_server_comes_back(tmp_path, reports):
    """הגדרת "גמור" של האיסיו: שרת שאינו עונה כשהשחזור נגמר, וחוזר
    אחר כך — הדיווח האחרון מגיע, והמכונה ממשיכה כרגיל."""
    reports.refuse_first = 2

    result = run_path(tmp_path, url_of(reports), "do_restore", tries=4)

    assert "TEST-HOLD" not in result["out"]
    assert "RETURNED" not in result["out"]
    assert len(reports.received) == 3
    assert "got through on try 3" in result["log"]


@native_tools
def test_a_restore_that_failed_still_holds_on_its_own_message(tmp_path, reports):
    """שחזור שנכשל לא נגע בשינוי הזה: המסך שלו הוא "restore did not
    complete", והדווח נשאר לרוץ כדי שהקונסולה תראה `failed`."""
    result = run_path(tmp_path, url_of(reports), "do_restore", restore_rc=1)

    assert "FAILED: restore did not complete" in result["out"]
    assert HOLD not in result["out"]


# --- ‏report_final עצמו: עוצר את הלולאה, וקורא את התשובה ---------------------


def report_final_probe(tmp_path: Path, sender: str, *, tries: int = 2) -> dict:
    """מריץ את `report_final` מול תהליך רקע אמיתי שאפשר להרוג."""
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "probe.txt"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} IMAGESRV_TEST=1 "
        f"FINAL_REPORT_TRIES={tries} FINAL_REPORT_GAP_S=0; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; "
        + sender
        + 'sleep 45 > /dev/null 2>&1 & _pid=$!; '
        f'if report_final "$_pid" {SESSION} {MAC} "http://x"; '
        'then echo "SENT"; else echo "NOT-SENT"; fi; '
        'if kill -0 "$_pid" 2>/dev/null; then echo "LOOP-ALIVE"; kill "$_pid"; '
        'else echo "LOOP-STOPPED"; fi'
    )
    with out_file.open("w", encoding="utf-8") as sink:
        subprocess.run(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
            stdout=sink, stderr=subprocess.STDOUT, text=True, cwd=str(REPO),
            stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S,
        )
    return {"out": out_file.read_text(encoding="utf-8"), "log": journal(run)}


@native_tools
def test_the_reporter_is_stopped_before_the_closing_report_is_sent(tmp_path):
    """כותב אחד ל-`progress.json`. זה מה ש-`pull_close` עשה מהיום
    הראשון, וזה מה שהמסלולים האחרים לא עשו."""
    result = report_final_probe(
        tmp_path, 'http_post_json() { echo "POST" >> "$RUN_DIR/trace"; }; ')

    assert "SENT" in result["out"]
    assert "LOOP-STOPPED" in result["out"]


@native_tools
def test_a_report_the_server_refused_is_not_a_report_that_arrived(tmp_path):
    """עיקרון 5 בצורתו הצרופה: קוד היציאה הוא הראיה. ‏`curl -sfS` נותן
    ל-400 של השרת קוד יציאה, ולכן "לא הייתה שגיאה" אינו "התקבל"."""
    result = report_final_probe(tmp_path, "http_post_json() { return 22; }; ")

    assert "NOT-SENT" in result["out"]
    assert "LOOP-STOPPED" in result["out"]
    assert "WARNING" in result["log"]
    assert "did not acknowledge the final report after 2 tries" in result["log"]


@native_tools
def test_a_final_report_for_an_already_closed_task_is_sent_once(tmp_path):
    sender = ('http_post_json() { echo POST >> "$RUN_DIR/trace"; '
              'echo \'{"code":"not_open"}\' > "$RUN_DIR/progress.reply"; return 22; }; ')
    result = report_final_probe(tmp_path, sender, tries=8)

    assert "SENT" in result["out"]
    assert (tmp_path / "run/trace").read_text().splitlines() == ["POST"]
    assert "server says the task is closed" in result["log"]


@native_tools
def test_the_ceiling_is_tries_and_the_journal_says_which_one(tmp_path):
    """כל ניסיון הוא curl שלם עם ה-retry שלו — ולכן התקרה נספרת
    בניסיונות. היומן אומר איזה מהם עבר, כדי ששרת איטי ייראה כשרת
    איטי ולא כשרת תקין."""
    counter = (
        'http_post_json() { _n=$(cat "$RUN_DIR/n" 2>/dev/null || echo 0); '
        '_n=$((_n + 1)); echo "$_n" > "$RUN_DIR/n"; [ "$_n" -ge 3 ]; }; '
    )
    result = report_final_probe(tmp_path, counter, tries=5)

    assert "SENT" in result["out"]
    assert "the final report was not accepted (try 1 of 5)" in result["log"]
    assert "the final report was not accepted (try 2 of 5)" in result["log"]
    assert "got through on try 3" in result["log"]


# --- הבקרה הסטטית: השעון אינו הראיה, וזה מנגנון אחד ולא שלושה ---------------


@native_tools
def test_no_restore_path_sleeps_instead_of_reading_the_answer():
    """‏`sleep 6` לא חוזר, בשום צורה ובשום מסלול — הוא היה ההימור."""
    for name in ("do_restore", "do_restore_drawers"):
        body = agent_functions(name)
        assert "sleep 6" not in body, f"‏{name} עדיין מהמרת על שעון"
        assert "report_final" in body, f"‏{name} אינה קוראת את התשובה"


def test_the_answer_is_read_before_the_machine_leaves():
    """הסדר הוא כל העניין: קודם אישור, ורק אחריו אתחול או כיבוי."""
    station = agent_functions("do_restore")
    assert station.index("report_final") < station.index("reboot -f")
    room = agent_functions("do_restore_drawers")
    # ‏rindex ולא index: שער ה-SMART (#652) מוסיף `finish_and_stop` *מוקדם*
    # במסלול "החלף דיסק" — כיבוי לפני שנכתב בייט, שאין עליו דיווח סיום.
    # הכלל של #101 הוא על הכיבוי שאחרי שחזור שהושלם — האחרון. ‏#1560: הכיבוי
    # הזה הוא ספירה לאחור (idle.sh end_countdown -> finish_and_stop).
    assert room.index("report_final") < room.rindex("end_countdown")


@native_tools
def test_all_three_closing_reports_go_through_one_mechanism():
    """הכלל הנכון היה מיושם במקום אחד מתוך שלושה, וכך הוא נשחק.
    ‏`pull_close` עובר עכשיו דרך אותה פונקציה בדיוק."""
    pull = (AGENT / "lib" / "restore" / "pull.sh").read_text(encoding="utf-8")
    close = pull[pull.index("pull_close() {"):]
    assert "report_final" in close
    agent = agent_functions("do_restore", "do_restore_drawers")
    assert agent.count("report_final") == 2


@native_tools
def test_the_periodic_loop_keeps_its_forgiveness():
    """מה שאסור לתקן: ‏`|| true` בלולאה התקופתית נכון ומתועד — דיווח
    שייחזור בעוד שתי שניות אינו חייב תשובה. הבעיה מעולם לא הייתה
    הלולאה אלא שהדיווח *האחרון* עבר דרכה."""
    progress = (AGENT / "lib" / "core" / "progress.sh").read_text(encoding="utf-8")
    loop = progress[progress.index("progress_loop() {"):]
    loop = loop[:loop.index("\n}\n")]
    assert "|| true" in loop


# --- למה זה חשוב: הצד השרתי, שכבר עובד --------------------------------------


def test_a_machine_that_reported_done_is_not_sent_the_round_again(server):
    """תרחיש ה-QA הקריטי מ-`CLAUDE.md`, ושתי הזרועות שלו יחד.

    השרת בסדר: מי שדיווח `done` מקבל תשובה בלי סבב. מה שמסוכן הוא
    הזרוע השנייה — מי ש-`done` שלו לא הגיע מקבל את אותו הסבב שוב,
    ומשחזר שוב. זו הסיבה שהסוכן אינו מאתחל בלי אישור.
    """
    from conftest import hello_body, setup_classroom            # noqa: PLC0415

    ids = setup_classroom(server)
    opened = server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": IMAGE,
        "prefix": "LAB1", "expected_clients": 1,
    })
    assert opened.status_code == 200
    session_id = opened.json()["id"]

    first = server["anon"].post("/api/v1/agent/hello",
                                json=hello_body(ids["mac1"])).json()
    assert first["session"]["state"] in ("open", "running")

    def hello_again() -> dict:
        return server["anon"].post("/api/v1/agent/hello",
                                   json=hello_body(ids["mac1"])).json()

    # הזרוע המסוכנת: המכונה סיימה, ה-`done` לא הגיע — ‏hello מחזיר לה
    # את אותו הסבב, והיא תשחזר שוב. זה מה שהשינוי בסוכן מונע.
    assert hello_again()["session"] is not None

    report = {"session_id": session_id, "mac": ids["mac1"], "state": "done",
              "targets": [{"dev": "sda", "bytes_written": 40960,
                           "bytes_total": 40960, "state": "done"}]}
    assert server["anon"].post("/api/v1/agent/progress",
                               json=report).status_code == 200

    assert hello_again()["session"] is None
