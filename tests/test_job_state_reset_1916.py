"""משימה מדווחת על עצמה בלבד — ‏#1916.

**מה קרה על ברזל (20/10, ‏HP-BUILD-2):** קליטה ראשונה נכשלה. באותו אתחול
נשלחה קליטה שנייה, והיא נסגרה בשרת כ-`failed` תוך 3 שניות, בלי שגיאה
ובלי שבלוק אחד נקרא. ה-`source_progress` שלה נשא את המספרים של הקליטה
הקודמת.

**השורש, בקוד:** ‏`do_task` מרים את `progress_loop` **לפני** ש-`capture_disk`
כותב משהו משלו. הדיווח הראשון נבנה (‏`build_progress`) מתוך `$RUN_DIR/state`
— ושם עדיין `failed` של המשימה הקודמת — ומתוך `targets/<dev>/`, שבו
‏`partclone.log` של המשימה הקודמת (‏`target_init` אינו מוחק אותו). השרת
(‏`reports._ingest_task`) רואה `state == "failed"` וסוגר את המשימה החדשה.

**אותה משפחה בשלושה מסלולים נוספים**, וכולם נבדקים כאן:

* סבב מגירות אחרי סבב שנכשל — הדיווח הראשון של הסבב החדש נושא `failed`,
  והחבר נרשם טרמינלי (‏`TERMINAL` ב-`reports.py`) בסבב שלא כתב בו בייט.
* שחזור תחנה אחרי שחזור שנכשל — אותו `failed` עליון.
* הפצה ישירה אחרי הפצה שהושלמה — `done` עליון, והשרת **סוגר משימת
  `direct_send` כ-`done`** על סמך הצהרת הסוכן (‏#715). זו הצורה החמורה:
  הצלחה בלי ראיה, עיקרון 5.

**איך נבדק:** הזיוף של `progress_loop` מצלם את הדיווח שהלולאה האמיתית
הייתה שולחת ברגע שהיא עולה (‏`build_progress` האמיתי), והעבודה המזויפת
(קליטה/שחזור) **ממתינה לצילום** לפני שהיא נוגעת במשהו. כך הצילום הוא
בדיוק הדיווח הראשון של המשימה — בלי מרוץ, ובלי לסמוך על תזמון.

‏`json_get` מוחלף בפייתון כדי שהקובץ ירוץ גם בלי jq (תחנת הפיתוח);
‏`jsonq.sh` עצמו קיים כדי שיהיה מקום אחד לזייף (ראו ראשו).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_final_report import (  # noqa: F401 — ‏`reports` הוא fixture
    IMAGE,
    PRESS_OFF,
    REPO,
    RUN_TIMEOUT_S,
    reports,
    run_path,
    url_of,
)

pytestmark = requires_native(("bash", BASH), "curl")

TASK_1 = "tsk_310a"
TASK_2 = "tsk_f0fe"
TOKEN = "f0" * 24

#: ‏`json_get` בלי jq: נתיב נקודות פשוט, ופלט כמו `jq -r` (מחרוזת גולמית,
#: ‏`null` על חסר). מספיק לכל מה שהמסלולים כאן קוראים.
JGET = r'''
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        v = json.load(f)
    for k in sys.argv[2].strip(".").split("."):
        if k:
            v = v.get(k) if isinstance(v, dict) else None
except Exception:
    v = None
if v is None:
    print("null")
elif isinstance(v, bool):
    print("true" if v else "false")
elif isinstance(v, (dict, list)):
    print(json.dumps(v))
else:
    print(v)
'''

#: הצילום: מה שהלולאה האמיתית הייתה שולחת ברגע שעלתה, שורה לכל משימה.
#: ‏`snap.ready` הוא האות לעבודה המזויפת שהצילום קיים.
SNAP = (
    'json_get() { "$PYBIN" "$JGET" "$1" "$2"; }; '
    '_await_snap() { _n=0; while [ ! -f "$RUN_DIR/snap.ready" ] && [ "$_n" -lt 200 ]; '
    'do sleep 0.05; _n=$((_n + 1)); done; rm -f "$RUN_DIR/snap.ready"; }; '
)

#: מוגדר בתוך הקריאה עצמה: ‏`run_path` מוסיף את הזיוף שלו (‏`LOOP_STUB`)
#: אחרי ה-stubs, והאחרון שהוגדר הוא שרץ.
SNAP_LOOP = (
    'progress_loop() { _s=$(build_progress "$1" "$2" "$4"); '
    'printf "%s\\n" "$_s" >> "$RUN_DIR/snaps.jsonl"; : > "$RUN_DIR/snap.ready"; }; '
)

#: קליטה מזויפת שמשאירה בדיוק את מה שהאמיתית השאירה ב-#1916: שלב, יומן
#: partclone עם 4% ממחיצה 3, ויעד `failed` עם סיבה.
CAPTURE_FAILS = (
    'capture_disk() { _await_snap; '
    'capture_stage() { printf "%s\\n" "$1" > "$RUN_DIR/stage"; }; capture_stage stream; '
    'target_init "$2" 0; echo capturing > "$RUN_DIR/state"; '
    'printf "Starting to clone device (/dev/%s3) to image\\nSpace in use: 87690000 Blocks\\n'
    'Completed: 4.00%%\\n" "$2" > "$RUN_DIR/targets/$2/partclone.log"; '
    'target_set "$2" failed "מחיצה 3: הקליטה נכשלה (שלב=zstd rc=137)"; '
    'echo failed > "$RUN_DIR/state"; return 1; }; '
    'upload_manifest() { return 1; }; '
)


def answer(task_id: str, kind: str = "capture") -> dict:
    return {"schema": 1, "known": True, "role": "build",
            "task": {"id": task_id, "type": kind, "disk": "sda",
                     "name": "Windows 11 Lab", "token": TOKEN}}


def run_jobs(tmp_path: Path, server: str, call: str, *, stubs: str = "",
             first_answer: dict | None = None, env: str = "") -> dict:
    """‏`run_path` של #101, עם הצילום ובלי jq."""
    jget = tmp_path / "jget.py"
    jget.write_text(JGET, encoding="utf-8")
    env = (f"PYBIN={shlex.quote(posix(Path(sys.executable)))} "
           f"JGET={shlex.quote(posix(jget))} {env}")
    result = run_path(tmp_path, server, SNAP_LOOP + call, answer=first_answer,
                      stubs=SNAP + stubs, env=env)
    snaps = tmp_path / "run" / "snaps.jsonl"
    result["snaps"] = [json.loads(line) for line in
                       snaps.read_text(encoding="utf-8").splitlines()] \
        if snaps.exists() else []
    return result


def assert_reports_only_itself(snap: dict, out: str) -> None:
    """הדיווח הראשון של משימה: אין בו דבר מהמשימה הקודמת."""
    assert snap["state"] not in ("failed", "done"), (
        f"הדיווח הראשון נושא את מצב המשימה הקודמת: {snap}\n{out}")
    for target in snap["targets"]:
        assert target["state"] not in ("failed", "done"), (
            f"יעד שעוד לא נגעו בו מדווח כמסוים: {target}\n{out}")
        assert "error" not in target, f"שגיאה של המשימה הקודמת: {target}"
        assert "source_progress" not in target, (
            f"התקדמות קריאה של המשימה הקודמת: {target}")
    assert "stage" not in snap, f"שלב של המשימה הקודמת: {snap}"
    # ‏rc7 (17/10): ‏`udp_drops.json` עליון נכנס לכל דיווח, ומתאפס רק בתוך
    # ‏`run_restore_drawers` — אחרי שהדווח של הסבב החדש כבר עלה.
    assert "udp_drops" not in snap, f"ספירת ה-drops של הסבב הקודם: {snap}"


# --- ‏#1916 עצמו: קליטה אחרי קליטה שנכשלה ---------------------------------


def test_a_second_capture_reports_only_itself(tmp_path, reports):
    """**לב #1916.** הקליטה הראשונה נכשלת באמת (‏`do_task` האמיתי), ואז
    באותו `RUN_DIR` מגיעה קליטה שנייה. הדיווח הראשון שלה — מה שסגר את
    ‏tsk_f0fe בשרת — אינו נושא `failed`, שגיאה, שלב או `source_progress`."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    (run / "resp2.json").write_text(json.dumps(answer(TASK_2)), encoding="utf-8")

    result = run_jobs(
        tmp_path, url_of(reports),
        f'do_task; cp {shlex.quote(posix(run / "resp2.json"))} "$RESP"; do_task',
        stubs=CAPTURE_FAILS, first_answer=answer(TASK_1))

    assert len(result["snaps"]) == 2, result["out"]
    first, second = result["snaps"]
    assert first["task_id"] == TASK_1 and second["task_id"] == TASK_2
    assert_reports_only_itself(second, result["out"])
    # ‏capture_disk עוד לא קרא ל-target_init: יעד של המשימה הקודמת אינו יעד שלה.
    assert second["targets"] == [], second


def test_a_second_capture_after_a_held_failure_reports_only_itself(
        tmp_path, reports):
    """**המסלול שנמצא על ברזל, כפי שהוא ב-rc7.** הקליטה הראשונה נכשלת,
    והמכונה עומדת ב-`ui_error_hold` האמיתי (‏`hold_watch`, לא ה-`TEST-HOLD`
    המקוצר). הפעימה הראשונה עונה באותה משימה — ההמתנה נמשכת; השנייה
    עונה במשימה חדשה, ו-`hold_new_task` (#1575) משחרר. ה-hello הבא של
    הלולאה הראשית מביא את tsk_f0fe ל-`do_task` באותו `RUN_DIR` — וזה
    הדיווח שסגר אותה בשרת תוך 3 שניות."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    resp1, resp2 = run / "resp1.json", run / "resp2.json"
    resp1.write_text(json.dumps(answer(TASK_1)), encoding="utf-8")
    resp2.write_text(json.dumps(answer(TASK_2)), encoding="utf-8")

    beat = (
        # ‏hold_watch קורא לפעימה בלי תת-מעטפת, ולכן המונה נשמר בין פעימות.
        'hold_beat() { _hb=$((${_hb:-0} + 1)); echo "BEAT $_hb" >> "$RUN_DIR/trace"; '
        '[ "$_hb" -le 20 ] || { echo "BEAT-CEILING"; exit 99; }; '
        f'if [ "$_hb" -lt 2 ]; then cp {shlex.quote(posix(resp1))} "$RUN_DIR/attended.resp"; '
        f'else cp {shlex.quote(posix(resp2))} "$RUN_DIR/attended.resp"; fi; }}; '
        # ‏IMAGECTL_TEST=0 בהמתנה בלבד: שום פקודה אמיתית לא יוצאת מכאן.
        'reboot() { echo "REAL-REBOOT"; exit 98; }; '
        'poweroff() { echo "REAL-POWEROFF"; exit 98; }; '
    )
    result = run_jobs(
        tmp_path, url_of(reports),
        'IMAGECTL_TEST=0; do_task; echo "HOLD-RC=$?"; IMAGECTL_TEST=1; '
        'cp "$RUN_DIR/attended.resp" "$RESP"; do_task',
        stubs=CAPTURE_FAILS + beat, first_answer=answer(TASK_1),
        env="HOLD_BEAT_S=0")

    out = result["out"]
    assert "BEAT-CEILING" not in out and "REAL-" not in out, out
    # ההמתנה האמיתית רצה ונגמרה דרך hold_new_task — לא דרך TEST-HOLD.
    assert "HOLD-RC=1" in out, out
    assert result["trace"].count("BEAT 1") == 1, result["trace"]
    assert "BEAT 2" in result["trace"], result["trace"]
    assert (f"hold ended: the server gave a new task ({TASK_2}) after {TASK_1} failed"
            in result["log"]), result["log"]

    assert len(result["snaps"]) == 2, out
    first, second = result["snaps"]
    assert first["task_id"] == TASK_1 and second["task_id"] == TASK_2
    assert_reports_only_itself(second, out)
    assert second["targets"] == [], second


def test_shrink_state_survives_the_reset(tmp_path, reports):
    """הניקוי אינו מוחק את מה שחייב לשרוד בין משימות: דיסק שנשאר מכווץ
    (‏`shrunk_pending`, ‏`shrinkoffer.sh`) מוצע בחזרה פעם אחת באתחול —
    ואם הסימון נמחק, המפעיל לא ישמע עליו לעולם."""
    target = tmp_path / "run" / "targets" / "sda"
    target.mkdir(parents=True)
    (target / "shrunk_pending").write_text("7|3|p\n", encoding="utf-8")
    (target / "shrink_id").write_text("7\n", encoding="utf-8")

    result = run_jobs(tmp_path, url_of(reports), "do_task",
                      stubs=CAPTURE_FAILS, first_answer=answer(TASK_1))

    assert (target / "shrunk_pending").read_text(encoding="utf-8") == "7|3|p\n", result["out"]
    assert (target / "shrink_id").exists(), result["out"]


# --- המשפחה: אותו מצב ישן בשאר המסלולים -------------------------------------


def test_a_second_drawer_round_after_a_failed_one_reports_only_itself(
        tmp_path, reports):
    """משכפל: סבב שנכשל, ואז סבב חדש באותו אתחול. בלי הניקוי הדיווח
    הראשון של הסבב השני נושא `failed`, והשרת רושם את החבר כטרמינלי
    בסבב שבו לא נכתב בייט."""
    result = run_jobs(
        tmp_path, url_of(reports),
        f"do_restore_drawers {IMAGE} ses_1; do_restore_drawers {IMAGE} ses_2",
        stubs='resolve_target_drawers() { _disks="sdb sdc"; }; '
              'smart_gate() { _await_snap; shift; echo "$@"; }; '
              # ‏rc7: הסבב הראשון משאיר את ספירת ה-drops שלו (waits.sh, udp_drops_add)
              'run_restore_drawers() { '
              'echo \'{"rcvbuf":3,"inerrors":0}\' > "$RUN_DIR/udp_drops.json"; '
              'echo "3 0" > "$RUN_DIR/udp_drops.sum"; '
              'for _t in sdb sdc; do target_set "$_t" failed; done; '
              'echo failed > "$RUN_DIR/state"; return 1; }; '
              + PRESS_OFF,   # #2024: the failed round holds until "כבה"
        env="RESTORE_RC=1")

    assert len(result["snaps"]) == 2, result["out"]
    assert result["snaps"][1]["session_id"] == "ses_2"
    # הזיוף באמת כותב את הספירה — אחרת "אין udp_drops" לא היה אומר דבר.
    assert (tmp_path / "run" / "udp_drops.json").exists(), result["out"]
    assert_reports_only_itself(result["snaps"][1], result["out"])


def test_a_second_station_restore_after_a_failed_one_reports_only_itself(
        tmp_path, reports):
    """תחנה: שחזור שנכשל, ושחזור נוסף באותו `RUN_DIR`."""
    result = run_jobs(
        tmp_path, url_of(reports), "do_restore; do_restore",
        stubs='smart_gate() { _await_snap; shift; echo "$@"; }; ',
        env="RESTORE_RC=1")

    assert len(result["snaps"]) == 2, result["out"]
    assert_reports_only_itself(result["snaps"][1], result["out"])


def test_a_direct_send_after_a_completed_one_does_not_report_done(
        tmp_path, reports):
    """הצורה החמורה: הפצה ישירה שהושלמה משאירה `done`, והשרת סוגר
    משימת `direct_send` כ-`done` לפי הדיווח (#715). דיווח ראשון שנושא
    ‏`done` ישן היה סוגר הפצה חדשה כמוצלחת לפני שבייט אחד שודר."""
    run = tmp_path / "run"
    target = run / "targets" / "sda"
    target.mkdir(parents=True)
    (run / "state").write_text("done\n", encoding="utf-8")
    (target / "state").write_text("done\n", encoding="utf-8")

    result = run_jobs(
        tmp_path, url_of(reports), "do_task",
        stubs=f". {posix(AGENT)}/lib/directsend.sh; "
              'direct_send_run() { _await_snap; target_init "$2" 1; '
              'target_set "$2" failed "stub"; echo failed > "$RUN_DIR/state"; return 1; }; ',
        first_answer=answer(TASK_2, kind="direct_send"))

    assert len(result["snaps"]) == 1, result["out"]
    assert_reports_only_itself(result["snaps"][0], result["out"])


# --- ‏404 על בדיקת המקום: "המשימה נסגרה", לא "המקום לא נבדק" ---------------


class _Fixed(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:                       # noqa: N802 — שם של BaseHTTP
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        code, body = self.server.reply
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """בלי רעש."""


@pytest.fixture
def fixed_server():
    httpd = HTTPServer(("127.0.0.1", 0), _Fixed)
    httpd.reply = (200, b"{}")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def preflight(tmp_path: Path, server: str) -> str:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "parts.txt").write_text("1|guid|uguid|2048|1000\n", encoding="utf-8", newline="\n")
    jget = tmp_path / "jget.py"
    jget.write_text(JGET, encoding="utf-8")
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} SERVER={shlex.quote(server)} "
        f"HTTP_TIMEOUT=4 HTTP_RETRIES=0 TASK_TOKEN={TOKEN}; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/capturesink.sh; "
        f'json_get() {{ {shlex.quote(posix(Path(sys.executable)))} {shlex.quote(posix(jget))} "$1" "$2"; }}; '
        "partition_node() { echo /dev/sda1; }; _fs_of() { echo ntfs; }; "
        "_partition_role() { echo data; }; partclone_for_fs() { echo partclone.ntfs; }; "
        "_used_bytes() { echo 4096; }; cd_used() { :; }; target_set() { :; }; "
        f'capture_space_preflight {TASK_2} sda "$RUN_DIR/parts.txt" 512; '
        'echo "rc=$? why=$CAPTURE_SPACE_ERROR"'
    )
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
        stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S)
    return proc.stdout + proc.stderr


def test_a_404_on_the_space_check_says_the_task_was_closed(tmp_path, fixed_server):
    """‏#1916, הבעיה השנייה: ‏404 הוא "המשימה אינה פתוחה בשרת"
    (‏`capture.task_for`), ולא "המקום הפנוי לא נבדק". הסיבה השקרית שלחה
    את המפעיל לבדוק דיסק של שרת שהיו בו 564GB פנויים."""
    fixed_server.reply = (404, b'{"detail":"no such open task"}')
    out = preflight(tmp_path, url_of(fixed_server))
    assert "rc=1" in out, out
    assert "המשימה נסגרה בשרת" in out, out
    assert "מקום פנוי" not in out, out


def test_a_server_error_on_the_space_check_still_says_unchecked(tmp_path, fixed_server):
    """השומר מפני תיקון-יתר: כל כשל אחר נשאר "לא נבדק" — רק 404 הוא
    ראיה חיובית שהמשימה נסגרה (עיקרון 5)."""
    fixed_server.reply = (503, b'{"detail":"busy"}')
    out = preflight(tmp_path, url_of(fixed_server))
    assert "rc=1" in out, out
    assert "לא נבדק: מקום פנוי בשרת" in out, out


def test_a_checked_space_still_passes(tmp_path, fixed_server):
    fixed_server.reply = (200, b'{"status":"ok","reason":"space checked"}')
    out = preflight(tmp_path, url_of(fixed_server))
    assert "rc=0" in out, out
