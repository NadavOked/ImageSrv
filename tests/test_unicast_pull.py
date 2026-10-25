"""הצד השני של #60: הסוכן פותח את זרם היוניקאסט — ‏issue #63.

‏#60 בנה בשרת את `POST /api/v1/agent/pulls`, וכל מה שנתלה עליו — דיווחי
התקדמות, שורת יומן, שורה במבט-העל. ‏`single_station_flow` פשוט לא קרא
לו: תחנה מושכת אימג' עשרים דקות, ומפעיל שמסתכל בקונסולה רואה שרת פנוי.

שלוש שאלות שהבדיקות כאן שואלות, ולא אחת:

* **האם נפתח זרם, ולפני הכתיבה** — משיכה שמוכרזת בסוף היא משיכה שאיש
  לא יכול היה לצפות בה.
* **מה קורה כשהפתיחה נכשלת** — עיקרון 1 אומר שהשחזור ממשיך; מה שאסור
  הוא לוותר בשקט. ולכן חצי מהקובץ הזה עוסק ב*הבחנה* בין סוגי הכישלון:
  ‏`curl -f` מקפל 404 ("השרת הזה ישן מהendpoint") ו-503 ("השרת שממנו
  אנחנו עומדים למשוך 40 ג'יגה נופל") לאותו exit 22, ושני אלה שולחים
  טכנאי לשני מקומות שונים.
* **האם הזרם נסגר** — לא "ישנו וקיווינו" אלא דיווח סיום שנשלח, ושכשל
  בשליחתו הוא שורה ביומן.

חלק א' רץ מול **שרת HTTP אמיתי** בתהליכון, עם ה-curl וה-jq האמיתיים:
זה מה שנבדק — קוד סטטוס אמיתי שעובר דרך `-w '%{http_code}'`, ולא זיוף
של הפונקציה שאמורה לקרוא אותו. חלק ב' מריץ את האשף עצמו עם תפרים
מזויפים (‏`pull_post`, ‏`run_restore`, ‏`http_get`) ובודק את החיווט.
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

REPO = Path(__file__).resolve().parent.parent

#: ‏curl ו-jq הם המנגנון שנבדק כאן, לא נוחות: ההבחנה בין 404 ל-503 היא
#: קוד הסטטוס שחוזר מ-curl, וההבחנה בין "404 שלנו" ל-"404 של הנתב" היא
#: השדה `code` שנקרא ב-jq. בלעדיהם אין מה לבדוק — ובמעבדה זה כישלון (#52).
pytestmark = requires_native(("bash", BASH), "curl", "jq")

MAC = "b4:2e:99:07:1a:c4"
IMAGE = "img_7f3a91"

MANIFEST = {
    "id": IMAGE,
    "name": "Windows 11 Lab",
    "family": 256,
    "total_compressed_bytes": 40960,
}


# --- שרת מזויף אמיתי ---------------------------------------------------------


class Recorder(HTTPServer):
    """שומר את מה שהתקבל, ומחזיר את מה שהוגדר לו."""

    status = 200
    body = b'{"id":"ses_pull01","kind":"unicast","image_id":"img_7f3a91"}'
    received: list[bytes] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:                       # noqa: N802 — שם של BaseHTTP
        length = int(self.headers.get("Content-Length") or 0)
        self.server.received.append(self.rfile.read(length))
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, *_args) -> None:
        """בלי רעש ל-stderr של הריצה."""


@pytest.fixture
def fake_server():
    """שרת אמיתי על פורט אקראי, בתהליכון. נסגר בסוף הבדיקה."""
    httpd = Recorder(("127.0.0.1", 0), Handler)
    httpd.received = []
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


# --- חלק א': ‏pull_open מול תשובות אמיתיות -----------------------------------


def journal(run: Path) -> str:
    """היומן המקומי של הסוכן — ריק כשעוד לא נכתבה בו שורה. ‏\"\" ולא
    חריגה, כדי שבקרה שלילית תיפול על מה שהיא בודקת ולא על קובץ חסר."""
    path = run / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def open_pull(tmp_path: Path, server: str, *, user: str = "labtech",
              password: str = "pass") -> tuple[str, str]:
    """מריץ את `pull_open` האמיתי. מחזיר (פלט, היומן המקומי)."""
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    # ‏shlex.quote ולא repr: לסיסמה מותר להכיל לוכסן אחורי, ו-repr של
    # פייתון היה מכפיל אותו לתוך המרכאות הבודדות של sh.
    args = " ".join(shlex.quote(a) for a in (server, MAC, IMAGE, user, password))
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} HTTP_RETRIES=0 "
        f"HTTP_TIMEOUT=4 IMAGECTL_TEST=1; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/pull.sh; "
        f"if pull_open {args}; "
        f'then echo "OPENED=[$PULL_SESSION]"; '
        f'else echo "NOT-OPENED=[$PULL_SESSION]"; fi'
    )
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, cwd=str(REPO),
        stdin=subprocess.DEVNULL, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout, journal(run)


def test_a_pull_that_opened_hands_back_the_session_id(tmp_path, fake_server):
    """ראיה חיובית אחת: ‏200 שנשא `id`. זה — ורק זה — זרם פתוח."""
    out, log = open_pull(tmp_path, url_of(fake_server))

    assert "OPENED=[ses_pull01]" in out
    assert "unicast pull registered as ses_pull01" in log


def test_an_answer_without_an_id_is_not_an_open_stream(tmp_path, fake_server):
    """‏200 ריק אינו "לא הייתה שגיאה, אז הכל טוב": דיווחי ההתקדמות
    היו נשלחים ל-session שאינו קיים, והמסך היה נשאר ריק בדיוק כמו
    לפני התיקון — רק שהפעם בשקט."""
    fake_server.body = b"{}"

    out, log = open_pull(tmp_path, url_of(fake_server))

    assert "NOT-OPENED=[]" in out
    assert "200 without a session id" in log


def test_an_old_server_is_named_as_an_old_server(tmp_path, fake_server):
    """‏404 בלי מעטפת השגיאה שלנו = הנתב אומר "אין כזה נתיב". סוכן חדש
    מול שרת שלפני #60 — ומהמצב הזה יוצאים בשחזור רגיל, לא בתעלומה."""
    fake_server.status = 404
    fake_server.body = b'{"detail":"Not Found"}'

    out, log = open_pull(tmp_path, url_of(fake_server))

    assert "NOT-OPENED=[]" in out
    assert "no /api/v1/agent/pulls" in log and "older" in log
    assert "restoring unwatched" in log


def test_a_server_that_fell_over_is_not_an_old_server(tmp_path, fake_server):
    """הגוטצ'ה של #63, ועיקרון 5 עליה: ‏`curl -f` נותן ל-404 ול-503
    את אותו exit 22. ‏404 פירושו "המשך, השרת פשוט לא יודע לרשום" —
    ‏503 פירושו שהשרת שממנו התחנה עומדת למשוך את האימג' חולה. טכנאי
    שנשלח לשדרג שרת בזמן שהשרת נופל מפסיד אחר צהריים."""
    fake_server.status = 503
    fake_server.body = b"service unavailable"

    _out, log = open_pull(tmp_path, url_of(fake_server))

    assert "http 503" in log
    assert "older" not in log and "no /api/v1/agent/pulls" not in log


def test_a_refusal_the_server_did_make_keeps_its_reason(tmp_path, fake_server):
    """‏404 *עם* `code` הוא השרת מכיר את הנתיב וסירב — אימג' שאינו
    קיים. אותו קוד HTTP, סיפור אחר לגמרי."""
    fake_server.status = 404
    fake_server.body = b'{"ok":false,"error":"unknown image","code":"no_image"}'

    _out, log = open_pull(tmp_path, url_of(fake_server))

    assert "no_image" in log
    assert "older" not in log


def test_a_server_that_never_answered_has_its_own_line(tmp_path, fake_server):
    """‏000: אין תשובה בכלל. לא שרת ישן ולא סירוב — ולכן לא אותה שורה."""
    dead = url_of(fake_server)
    fake_server.shutdown()
    fake_server.server_close()

    _out, log = open_pull(tmp_path, dead)

    assert "no answer from the server" in log
    assert "older" not in log and "http 000" not in log


def test_the_body_is_what_the_endpoint_documents(tmp_path, fake_server):
    """הגוף נבנה ביד (אין jq בצד הכותב), ולכן סיסמה עוינת חייבת לצאת
    JSON תקין — אחרת פתיחת הזרם נכשלת דווקא אצל מי שבחר סיסמה טובה."""
    open_pull(tmp_path, url_of(fake_server), user="labtech",
              password='pa"ss\\word')

    assert len(fake_server.received) == 1
    assert json.loads(fake_server.received[0]) == {
        "mac": MAC, "image_id": IMAGE,
        "username": "labtech", "password": 'pa"ss\\word',
    }


def test_a_station_without_a_login_sends_empty_credentials(tmp_path, fake_server):
    """בוילן ההפצה עם סבב פתוח אין מסך כניסה (#42) ואין למה להמציא
    אישורים — השרת מוותר שם על הבדיקה בעצמו."""
    open_pull(tmp_path, url_of(fake_server), user="", password="")

    body = json.loads(fake_server.received[0])
    assert body["username"] == "" and body["password"] == ""


# --- חלק ב': החיווט באשף השחזור ---------------------------------------------

#: ‏`progress_loop` מזויף בכל הבדיקות **חוץ** מזו שבודקת את הסגירה.
#: הלולאה האמיתית היא אינסופית, ובמסלול הכישלון היא נשארת לרוץ בכוונה —
#: תהליך רקע כזה ששורד את הריצה מחזיק את הצינור של הבדיקה (הגוטצ'ה של
#: `capture_output`) ומזהם את הריצה הבאה. איפה שנבדקת הסגירה, הלולאה
#: האמיתית רצה — ושם היא גם נהרגת, כי זה בדיוק מה שנבדק.
LOOP_STUB = 'progress_loop() { echo "LOOP $1" >> "$RUN_DIR/trace"; }; '

#: ‏`ui.sh` מפעילה את הדווח **ברקע** (`progress_loop ... &`), ולכן כל מה
#: שהוא כותב נכתב אחרי שהמעטפת הראשית כבר המשיכה הלאה. ‏`wait` חוסם עד
#: ש**כל** תהליך רקע של המעטפת באמת יצא — כלומר עד שהכתיבה שלו הושלמה —
#: ולכן פייתון לעולם אינו קורא קובץ שעוד נכתב. הוא אינו `sleep`: הוא
#: אינו מהמר על חלון זמן אלא ממתין לאירוע.
#:
#: מה שהוא מונע בפועל הוא שורת JSON חתוכה ב-`posts.jsonl` — דיווח אחרון
#: של לולאה שנהרגה שנוחת בזמן שפייתון כבר קורא — כשל שהיה נראה כמו באג
#: בבניית הדיווח. הוא **אינו** מה שתיקן את ה-flake עצמו; ראו RESTORE_STUB.
#: ‏11/10: אשף הטקסט (`single_station_flow`) הוסר — GUI בלבד. מה שנשאר הוא
#: הכתיבה שמאחורי מסך השחזור הגרפי, `single_restore_run`, והיא שנבדקת.
FLOW = f'single_restore_run {IMAGE}; echo "RC=$?"; wait'

#: תקרה על הריצה כולה — רשת ביטחון ולא כוונון תזמון: הדרך היחידה להגיע
#: אליה היא תהליך רקע שלא נסגר, וזה באג שצריך להיראות בשמו ולא כתלייה.
RUN_TIMEOUT_S = 60


def run_flow(script: str, keys: tuple[str, ...], out_file: Path) -> None:
    """מריץ את המעטפת, וחוזר רק אחרי שכל תהליכי הרקע שלה יצאו.

    הפלט הולך לקובץ ולא ל-PIPE: תהליך רקע ששרד את הריצה מחזיק צינור
    פתוח, וההמתנה עליו נראית כמו באג בקוד שנבדק (גוטצ'ה מוכרת).
    """
    with out_file.open("w", encoding="utf-8") as sink:
        try:
            # ‏input בבייטים ולא ב-text (#491): ‏`single_restore_run` אינו קורא
            # stdin, ומקשים שמחכים שם חייבים להישאר שם (GUI בלבד, 11/10).
            subprocess.run(
                [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                stdout=sink, stderr=subprocess.STDOUT, cwd=str(REPO),
                input="".join(f"{k}\n" for k in keys).encode("utf-8"),
                timeout=RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"האשף לא סיים תוך {RUN_TIMEOUT_S}s: ה-`wait` תלוי על תהליך "
                f"רקע שלא נסגר. הפלט עד כה:\n"
                + out_file.read_text(encoding="utf-8")
            ) from None

#: המתנה על ראיה חיובית עם תקרה — הדפוס של `agent/lib/waits.sh`: חוזרת
#: ברגע שהשורה נמצאת, ואומרת בקול כשהיא לא הגיעה. ‏500 × 0.02 = תקרה של
#: 10 שניות, שאין להגיע אליה לעולם; הגעה אליה היא כשל ולא איטיות.
AWAIT = (
    'await_line() { _n=0; '
    'while [ "$_n" -lt 500 ]; do '
    'if [ -f "$1" ] && grep -qF "$2" "$1" 2>/dev/null; then return 0; fi; '
    'sleep 0.02; _n=$((_n + 1)); done; '
    'echo "AWAIT-TIMEOUT: \'$2\' never reached $1"; return 1; }; '
)

#: ‏run_restore מזויף — הצינור עצמו נבדק במקום אחר (והוא דורש חומרה).
#: מה שכן משוחזר כאן הוא מה שהאמיתי כותב: המצב שממנו נבנה הדיווח.
#:
#: והוא **ממתין לדווח שיעלה**, וזה לא קישוט אלא תיקון ה-flake.
#:
#: שחזור אמיתי נמשך דקות, ולכן `progress_loop` תמיד הספיק להירשם הרבה
#: לפני ש-`pull_close` הורג אותו. הזיוף שחזר מיד כיווץ את עשרים הדקות
#: האלה לאפס, וה-`kill` של `pull_close` ניצח את תהליכון הרקע לפני
#: שהספיק לכתוב שורה אחת. כלומר הכשל לא היה בדיקה שקראה מוקדם מדי אלא
#: **קוד המוצר שהרג את הזיוף בזמן** — ולכן `wait` לבדו לא עזר: אין למה
#: לחכות כשהתהליך מת לפני שכתב. ההמתנה כאן מחזירה את סדר הזמנים האמיתי,
#: על ראיה חיובית עם תקרה ולא על `sleep`.
#:
#: מדוד: לפני התיקון 5 מתוך 20 ריצות רצופות נפלו (וכל 5 האחרונות
#: ברציפות, כשהמכונה הייתה עמוסה יותר); אחריו 20 מתוך 20 ירוקות.
RESTORE_STUB = (
    'run_restore() { '
    # ‏#1561: הגואי של ההתקדמות (סטאב שיוצא מיד) מת לפני הכשל -- כדי שמסך
    # הכישלון כאן ייפתח בגואי משלו, דטרמיניסטית. הגואי החי: test_single_restore_progress_gui.py.
    '_g=0; while sr_gui_alive && [ "$_g" -lt 500 ]; do sleep 0.02; _g=$((_g + 1)); done; '
    '[ -n "${AWAIT_TEXT:-}" ] && { await_line "$AWAIT_FILE" "$AWAIT_TEXT" '
    '|| return 1; }; '
    'echo "RESTORE $1" >> "$RUN_DIR/trace"; '
    'if [ "${RESTORE_RESULT:-ok}" = "ok" ]; then '
    'echo done > "$RUN_DIR/state"; target_set "$2" "done"; return 0; fi; '
    'echo failed > "$RUN_DIR/state"; target_set "$2" "failed" "${RESTORE_REASON:-no}"; return 1; }; '
    'writemark_release_done() { :; }; '   # #1877: the mark is not what is tested here (test_writemark_postdeploy_1877.py)
)

#: ‏#1545: הגואי שמסך הכישלון מעלה — רושם מה קיבל ומה היה בקובץ המצב, ויוצא.
#: ‏#1561: כל גואי של השחזור עולה כ-`--screen progress` על `restore.state`.
#: הגואי של ההתקדמות (בלי `message=`) יוצא מיד, ומסך הכישלון נרשם.
GUI_STUB = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$*" > "$RUN_DIR/gui.args"\n'
    'grep -q "^message=" "$RUN_DIR/gui/restore.state" && cat "$RUN_DIR/gui/restore.state" > "$RUN_DIR/gui.seen"\n'
    'exit 0\n'
)


def station(tmp_path, *, pull_code: str = "200",
            pull_answer: str = '{"id":"ses_pull01","kind":"unicast"}',
            restore: str = "ok", real_loop: bool = False,
            keys: tuple[str, ...] = ("1", "ERASE", ""),
            gui: bool = False, reason: str = "no") -> dict:
    """מריץ את `single_restore_run` האמיתי מקצה לקצה עם תפרים מזויפים.
    ‏`keys` הם מקשים שמחכים ב-stdin — הכתיבה אינה קוראת אותם (GUI בלבד).

    התפרים הם בדיוק הגבולות של המערכת: הרשת (`pull_post`,
    `http_post_json`, `http_get`), הדיסק (`pick_internal_disk`) והכתיבה
    (`run_restore`). כל מה שביניהם — הסדר, ההחלטה, הסגירה — הוא הקוד
    האמיתי.
    """
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(
        json.dumps({"allowed_images": [IMAGE]}), encoding="utf-8")
    (run / "manifest.src.json").write_text(
        json.dumps(MANIFEST), encoding="utf-8")
    (run / "pull_answer.json").write_text(pull_answer, encoding="utf-8")
    out_file = tmp_path / "out.txt"

    # על מה `run_restore` ממתין לפני שהוא "כותב". רק כשבאמת אמור לעלות
    # דווח: משיכה שנדחתה לא מפעילה אותו, והמתנה לו שם הייתה תלייה.
    # הראיה שונה בין המסלולים כי המנגנון שונה — הזיוף כותב לעקבה,
    # הלולאה האמיתית שולחת דיווח.
    if pull_code != "200":
        await_file, await_text = "", ""
    elif real_loop:
        await_file, await_text = posix(run / "posts.jsonl"), "session_id"
    else:
        await_file, await_text = posix(run / "trace"), "LOOP "

    # ‏#1545: בלי gui=True הנתיב לא קיים — גם על מכונה שיש בה גואי מותקן
    gui_bin = tmp_path / "station-gui"
    if gui:
        gui_bin.write_text(GUI_STUB, encoding="utf-8", newline="\n")
        gui_bin.chmod(0o755)

    script = (
        f"export SR_GUI_BIN={shlex.quote(posix(gui_bin))} "
        f"RESTORE_REASON={shlex.quote(reason)}; "
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f'SERVER="http://127.0.0.1:1" '
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f"IMAGECTL_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=1 PROGRESS_INTERVAL_S=0.2 "
        f"RECOVERY_USER=labtech RECOVERY_PASS=pass RESTORE_RESULT={restore!r} "
        f"AWAIT_FILE={shlex.quote(await_file)} "
        f"AWAIT_TEXT={shlex.quote(await_text)}; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/pull.sh; "
        f". {posix(AGENT)}/lib/ui.sh; . {posix(AGENT)}/lib/guibridge.sh; "   # #1545: gui_backend_args
        f". {posix(AGENT)}/lib/single_restore.sh; "   # #706: the GUI restore's write
        f". {posix(AGENT)}/lib/srgui.sh; . {posix(AGENT)}/lib/clonergui.sh; . {posix(AGENT)}/lib/writemark.sh; "   # #1561: its progress on the GUI
        f". {posix(AGENT)}/lib/slotnames.sh; disk_port() {{ echo 1; }}; "
        'pick_internal_disk() { echo sda; }; '
        'sr_share_gate() { return 0; }; '   # #1898: the split question -- its own tests (test_single_restore_share_1898.py)
        'http_get() { cat "$RUN_DIR/manifest.src.json"; }; '
        'pull_post() { cp "$2" "$RUN_DIR/sent.json"; '
        'cp "$RUN_DIR/pull_answer.json" "$3"; '
        f'echo "PULL" >> "$RUN_DIR/trace"; echo {pull_code!r}; }}; '
        'http_post_json() { printf \'%s\\n\' "$(cat "$2")" '
        '>> "$RUN_DIR/posts.jsonl"; }; '
        + AWAIT
        + RESTORE_STUB
        + ("" if real_loop else LOOP_STUB)
        + FLOW
    )
    run_flow(script, keys, out_file)
    out = out_file.read_text(encoding="utf-8")
    # התקרה של `await_line` נכשלת בשמה, ולא כ"הטענה לא התקיימה": אם
    # הדווח לא עלה תוך 10 שניות זו תקלה בפני עצמה, ולא איטיות שעוברת.
    assert "AWAIT-TIMEOUT" not in out, out
    posts = [json.loads(line) for line
             in (run / "posts.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()] if (run / "posts.jsonl").exists() else []
    return {
        "out": out,
        "log": journal(run),
        "trace": (run / "trace").read_text(encoding="utf-8").splitlines()
        if (run / "trace").exists() else [],
        "posts": posts,
        "sent": json.loads((run / "sent.json").read_text(encoding="utf-8"))
        if (run / "sent.json").exists() else None,
    }


def test_the_wizard_opens_the_stream_before_it_writes(tmp_path):
    """לב האיסיו. הקוד שלפני התיקון מגיע לכאן עם עקבה שיש בה `RESTORE`
    בלבד — עשרים דקות של משיכה שהשרת לא ידע עליהן."""
    result = station(tmp_path)

    # ‏`PULL` הוא הראשון, ו-`RESTORE` אחריו. הדווח (`LOOP`) נכנס לעקבה
    # מתהליכון רקע, ולכן מיקומו ביחס ל-`RESTORE` אינו מובטח ואינו נבדק.
    assert result["trace"][0] == "PULL"
    assert result["trace"].index("PULL") < result["trace"].index("RESTORE unicast")
    assert result["sent"]["image_id"] == IMAGE
    assert result["sent"]["mac"] == MAC


def test_the_reporter_gets_the_session_the_server_returned(tmp_path):
    """לא מזהה שהסוכן המציא ולא ריק: מה שחזר בגוף התשובה."""
    result = station(tmp_path)

    assert "LOOP ses_pull01" in result["trace"]
    assert "unicast pull registered as ses_pull01" in result["log"]


def test_the_gui_restore_opens_the_pull_without_credentials(tmp_path):
    """‏11/10 (GUI בלבד): הכניסה בטקסט שסיפקה את האישורים הוסרה, והסיסמה
    שהוקלדה בגואי אינה יוצאת מתהליך הכניסה של הקיוסק. המשיכה נפתחת עם
    אישורים ריקים — גם כש-RECOVERY_* מוגדרים בסביבה (שאריות, לא כניסה).
    בקרה שלילית: הקוד הישן שולח labtech/pass."""
    result = station(tmp_path)

    assert result["sent"]["username"] == ""
    assert result["sent"]["password"] == ""


def test_an_old_server_does_not_cancel_the_restore(tmp_path):
    """עיקרון 1: הבייטים מוגשים ממילא, והדיסק כבר נמחק. מה שאסור הוא
    לוותר בשקט — ולכן יש שורה ביומן, וגם אין דיווחים לשום מקום."""
    result = station(tmp_path, pull_code="404",
                     pull_answer='{"detail":"Not Found"}')

    assert result["trace"] == ["PULL", "RESTORE unicast"]
    assert "RC=0" in result["out"]
    assert "no /api/v1/agent/pulls" in result["log"]
    assert result["posts"] == []


def test_a_server_that_fell_over_does_not_cancel_the_restore_either(tmp_path):
    """אותה החלטה, שורת יומן אחרת. ההבחנה היא כל העניין."""
    result = station(tmp_path, pull_code="503", pull_answer="down")

    assert result["trace"] == ["PULL", "RESTORE unicast"]
    assert "RC=0" in result["out"]
    assert "http 503" in result["log"] and "older" not in result["log"]


def test_the_stream_is_closed_when_the_restore_finishes(tmp_path):
    """הלולאה האמיתית רצה כאן, ונהרגת כאן. הדיווח האחרון הוא הסגירה:
    השרת סוגר משיכה על ראיה חיובית — `done` — ולא על שתיקה."""
    result = station(tmp_path, real_loop=True)

    assert result["posts"], "לא נשלח אף דיווח — הזרם לא נצפה בכלל"
    last = result["posts"][-1]
    assert last["session_id"] == "ses_pull01"
    assert last["state"] == "done"
    assert last["targets"][0] == {"dev": "sda", "bytes_written": 0,
                                  "bytes_total": 40960, "state": "done"}
    assert "unicast pull ses_pull01 closed" in result["log"]


def test_every_report_of_the_pull_carries_its_session(tmp_path):
    """הלולאה האמיתית מדווחת לאותו זרם לכל אורכה — לא רק בסוף."""
    result = station(tmp_path, real_loop=True)

    assert {p["session_id"] for p in result["posts"]} == {"ses_pull01"}
    assert all("task_id" not in p for p in result["posts"])


def test_a_failed_restore_leaves_the_stream_on_the_screen(tmp_path):
    """"נכשל" ו"הסתיים" אינם אותו מצב (#60). הדווח ממשיך לרוץ, ואף
    אחד לא שולח `done` — המשיכה נשארת גלויה עד שמפעיל סוגר אותה."""
    result = station(tmp_path, restore="fail", keys=("1", "ERASE"))

    assert sorted(result["trace"]) == ["LOOP ses_pull01", "PULL", "RESTORE unicast"]
    assert "TEST-HOLD" in result["out"]
    assert "FAILED: restore did not complete" in result["out"]
    assert "closed" not in result["log"]


PLAN_REFUSED = "sgdisk דחה את תוכנית השחזור — הדיסק לא שונה"


def test_a_failed_restore_brings_the_gui_back_in_hebrew_with_the_reason(tmp_path):
    """‏#1545 (ברזל 17/10): הגשר שחרר את התצוגה לכתיבה, והכישלון היה טקסט
    באנגלית על tty שחור בלי הסיבה. הגואי חוזר כמסך הודעה: כותרת בעברית,
    והסיבה של היעד עצמו. ה-hold שמתחתיו (פעימה, TEST-HOLD) לא השתנה.
    בקרה שלילית: לפני התיקון הגואי לא מופעל כלל (`gui.seen` לא נוצר)."""
    result = station(tmp_path, restore="fail", keys=("1", "ERASE"), gui=True, reason=PLAN_REFUSED)
    run = tmp_path / "run"
    seen = (run / "gui.seen").read_text(encoding="utf-8") if (run / "gui.seen").exists() else ""
    assert seen == f"message=השחזור נכשל — המחשב נשאר דלוק ומדווח לשרת|\u200f{PLAN_REFUSED}\n", (seen, result["out"])
    title, sub = seen[len("message="):].rstrip("\n").split("|")
    assert len(title.encode()) < 96 and len(sub.encode()) < 200   # msg_title[96], msg_sub[200] ב-ui.h
    args = (run / "gui.args").read_text(encoding="utf-8")
    assert f"--state {posix(run)}/gui/restore.state" in args and "--backend " in args, args
    assert "TEST-HOLD" in result["out"]          # הפעימה וה-hold נשארו
    assert "closed" not in result["log"]


def test_a_reason_too_long_for_the_gui_is_replaced_not_cut_mid_letter(tmp_path):
    result = station(tmp_path, restore="fail", keys=("1", "ERASE"), gui=True, reason="א" * 120)
    seen = (tmp_path / "run" / "gui.seen").read_text(encoding="utf-8")
    assert seen.endswith("|\u200fהסיבה המלאה ביומן הסוכן ובקונסולה\n"), (seen, result["out"])


def test_without_a_gui_binary_the_failure_stays_on_the_console(tmp_path):
    result = station(tmp_path, restore="fail", keys=("1", "ERASE"), reason=PLAN_REFUSED)
    assert not (tmp_path / "run" / "gui.seen").exists()
    assert "FAILED: restore did not complete" in result["out"]
    assert "no GUI for the failure screen" in result["log"]


def test_the_closing_report_that_did_not_arrive_is_not_silence(tmp_path):
    """עיקרון 5 על הסגירה עצמה: אם דיווח הסיום לא עבר, המשיכה תישאר
    "רצה" על מסך הקונסולה — וזו שורה ביומן, לא כלום."""
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} IMAGECTL_TEST=1; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/pull.sh; "
        'http_post_json() { return 7; }; '
        'sleep 30 > /dev/null 2>&1 & '
        'pull_close "$!" ses_pull01 aa:bb "http://x"'
    )
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, cwd=str(REPO),
        stdin=subprocess.DEVNULL, timeout=60,
    )

    assert proc.returncode == 1, proc.stderr
    log = journal(run)
    assert "WARNING" in log and "did not get through" in log
    assert "closed" not in log
