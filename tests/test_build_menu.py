"""מסך מחשב הבנייה — מאז 11/10 גואי בלבד (‏#135, ‏#1073, הכרעת נדב 11/10).

עד 11/10 הקובץ הזה בדק את **תפריט הטקסט** של מחשב הבנייה: כניסה בטקסט,
ארבע-חמש אפשרויות ממוספרות לפי התפקיד, בחירת תיקייה, "Type this computer's
name" ו-standby. נדב הכריע: אין מסך טקסט חלופי במכונות — רק הגואי — והקוד
נמחק. הבדיקות של התפריט נמחקו איתו, והשומר שהוא לא חוזר הוא
‏`tests/test_gui_only_no_text_fallback.py`.

מה שנשאר כאן הוא מה שהגואי עצמו עובר דרכו, מול שרת HTTP אמיתי בתהליכון
(כמו `test_final_report.py`):

1. ‏`build_console_screen` בלי גואי — הודעה ואתחול מהדיסק המקומי, **בלי**
   כניסה, בלי בקשה אחת לקונסולה ובלי לקרוא את המקשים שחיכו ב-stdin.
   בקרה שלילית: על הקוד הישן היא נכנסת לכניסה בטקסט ושולחת
   ‏`POST /api/v1/agent/login`.
2. הקליטה מהגואי (‏`gui_capture`, ‏`guibridge.sh`) מגיעה ל-
   ‏`POST /api/console/tasks/capture` עם הגוף של הקונסולה, ה-cookie,
   והתיקייה — קיימת, או חדשה שנוצרת קודם. שם בעברית אינו מגיע לשרת.
3. ‏`gui_role` קורא את התפקיד מתשובת הכניסה (#947).

הפלט של כל הרצה הולך לקובץ ולא ל-PIPE (‏`capture_output` ממתין לסגירת
הצינור, לא ליציאת התהליך).
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_final_report import cut_function
from agentlib import flat_lib, lib_path

REPO = Path(__file__).resolve().parent.parent

#: ‏curl הוא המנגנון שנבדק (קוד התשובה וה-cookie הם הראיה החיובית), ו-jq
#: קורא את מצב המכונה. בלעדיהם אין מה לבדוק, ובמקום שבו הם אמורים להיות
#: זה כישלון ולא דילוג (#52).
native_tools = requires_native(("bash", BASH), "curl", "jq")

MAC = "b4:2e:99:07:1a:c4"
ANSWER = {"schema": 1, "known": True, "role": "build", "task": None,
          "session": None, "allowed_images": [], "ui": {"require_login": True}}

FOLDERS = [
    {"name": "Lab", "description": "", "images": 2},
    {"name": "Classrooms", "description": "", "images": 5},
]

#: מצב המכונה מ-`/api/v1/agent/state` — ‏`gui_capture` בודק מולו את הדיסק.
STATE = {"mac": MAC, "known": True, "role": "build", "name": "BUILD-01",
         "disks": [{"dev": "/dev/sda", "removable": False}],
         "allowed_images": [], "task": None}

RUN_TIMEOUT_S = 90


# --- שרת קונסולה מזויף, שאפשר להגיד לו מה להחזיר -----------------------------


class Console(HTTPServer):
    """מחזיר את מה שהוגדר לו, וזוכר כל בקשה שהגיעה."""

    role = "admin"
    folders: list[dict] = []
    requests: list[dict] = []
    state: dict = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _send(self, status: int, payload, cookie: str = "") -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=None) -> None:
        self.server.requests.append({
            "method": self.command, "path": self.path, "body": body,
            "cookie": self.headers.get("Cookie", ""),
        })

    def do_GET(self) -> None:                        # noqa: N802 — BaseHTTP
        self._record()
        if self.path == "/api/console/folders":
            self._send(200, self.server.folders)
        elif self.path.startswith("/api/v1/agent/state?mac="):
            self._send(200, self.server.state)
        else:
            self._send(404, {"detail": "no such path"})

    def do_POST(self) -> None:                       # noqa: N802 — BaseHTTP
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = {"unparsable": raw.decode("utf-8", "replace")}
        self._record(body)

        if self.path == "/api/v1/agent/hello":
            self._send(200, ANSWER)
        elif self.path == "/api/v1/agent/login":
            self._send(200, {"ok": True, "role": self.server.role})
        elif self.path == "/api/console/login":
            token = f"u|{self.server.role}|9999999999|deadbeef"
            self._send(200, {"username": "u", "role": self.server.role,
                             "idle_seconds": 300},
                       cookie=f"imagesrv_session={token}; Path=/; HttpOnly")
        elif self.path == "/api/console/folders":
            self._send(200, {"ok": True})
        elif self.path == "/api/console/tasks/capture":
            self._send(200, {"id": "tsk_01", "image_id": "img_01"})
        else:
            self._send(404, {"detail": "no such path"})

    def log_message(self, *_args) -> None:
        """בלי רעש ל-stderr של הריצה."""


@pytest.fixture
def console():
    httpd = Console(("127.0.0.1", 0), Handler)
    httpd.requests = []
    httpd.folders = [dict(f) for f in FOLDERS]
    httpd.role = "admin"
    httpd.state = json.loads(json.dumps(STATE))
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


def posted(console: Console, path: str) -> list[dict]:
    return [r["body"] for r in console.requests
            if r["method"] == "POST" and r["path"] == path]


#: הספריות שהמסך והגשר טוענים (‏gui_libs ב-guibridge.sh, והסוכן).
LIBS = ("common.sh", "jsonq.sh", "waits.sh", "ui.sh", "hold.sh", "attended.sh",
        "buildmenuitems.sh", "buildmenu.sh", "buildcapture.sh", "roomflow.sh",
        "localboot.sh", "guiparent.sh", "guibridge.sh")


def run_shell(tmp_path: Path, server: str, body: str, stdin_text: str = "") -> dict:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(json.dumps(ANSWER), encoding="utf-8")
    keys = tmp_path / "keys.txt"
    keys.write_text(stdin_text, encoding="utf-8", newline="\n")
    out_file = tmp_path / "out.txt"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"SERVER={shlex.quote(server)} "
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f'LOG_FILE={shlex.quote(posix(run / "agent.log"))} '
        "IMAGESRV_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=4; "
        # The Windows shell would rewrite "/dev/sda" into a Windows path on
        # the way to jq.exe; a development-station artifact, not the agent's.
        "export MSYS2_ARG_CONV_EXCL='*'; "
        + "".join(f". {posix(flat_lib())}/{n}; " for n in LIBS)
        + 'sleep() { :; }; '
        + body
    )
    with out_file.open("w", encoding="utf-8") as sink, keys.open("rb") as feed:
        try:
            subprocess.run(
                [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                stdout=sink, stderr=subprocess.STDOUT, text=True, cwd=str(REPO),
                stdin=feed, timeout=RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"לא הסתיים תוך {RUN_TIMEOUT_S}s. הפלט עד כה:\n"
                + out_file.read_text(encoding="utf-8")
            ) from None
    log = run / "agent.log"
    return {
        "out": out_file.open(encoding="utf-8", newline="").read().replace("\r", ""),
        "log": log.read_text(encoding="utf-8") if log.exists() else "",
    }


# --- 1: בלי גואי — דיסק מקומי, בלי כניסה ובלי תפריט ----------------------------


@native_tools
def test_the_build_screen_without_a_gui_boots_local_and_asks_nothing(tmp_path, console):
    if Path("/usr/bin/imagesrv-kiosk").exists():
        pytest.fail("/usr/bin/imagesrv-kiosk קיים על המכונה המריצה — הטסט מניח גואי חסר")
    screen = cut_function("build_console_screen")
    assert screen is not None, "‏build_console_screen אינה מוגדרת ב-imagesrv-agent"
    result = run_shell(
        tmp_path, url_of(console),
        # 11/10: no efivars here -- BootNext "skipped", the host untouched.
        f"export SYSROOT={shlex.quote(posix(tmp_path / 'sysroot'))}; "
        'build_hello() { printf %s "{\\"mac\\":\\"$MAC\\",\\"joining\\":$1}"; }; '
        "shrink_offer_pending() { :; }; " + screen
        + '\nbuild_console_screen; echo "RETURNED rc=$?"; '
        'IFS= read -r _left && echo "STDIN-UNTOUCHED $_left"',
        stdin_text="admin\npw\n1\n")

    out = result["out"]
    assert "TEST-REBOOT: native GUI unavailable (build screen)" in out, out
    assert "The graphical interface did not start." in out
    assert "RETURNED" not in out
    assert "Sign in" not in out and "Username" not in out
    # 11/10: the one request is the hello that says the GUI did not come up
    # -- no sign-in, nothing from the console API.
    assert [(r["method"], r["path"]) for r in console.requests] == [("POST", "/api/v1/agent/hello")], console.requests
    assert console.requests[0]["body"]["gui_failed"]["screen"] == "build"
    assert console.requests[0]["body"]["joining"] is False


# --- 2: הקליטה מהגואי מגיעה ל-POST עם התיקייה הנכונה ------------------------


def gui_capture_call(name: str, folder: str, folder_new: str, desc: str = "") -> str:
    """הפעולה `capture-start` כפי שהגשר מריץ אותה: כניסה, תפקיד, ואז
    ‏`gui_capture` עם שדות הרשומה."""
    return (
        "RECOVERY_USER=admin RECOVERY_PASS=pw; console_signin || exit 11; "
        "gui_role || exit 12; "
        f"dev=/dev/sda name={shlex.quote(name)} folder={shlex.quote(folder)} "
        f"folder_new={folder_new} desc={shlex.quote(desc)}; "
        'gui_capture; echo "CAPTURE rc=$?"'
    )


@native_tools
def test_a_gui_capture_into_an_existing_folder_carries_that_folder(tmp_path, console):
    result = run_shell(tmp_path, url_of(console),
                       gui_capture_call("Win11 lab", "Classrooms", "no"))
    assert "CAPTURE rc=0" in result["out"], result["out"]
    captures = posted(console, "/api/console/tasks/capture")
    assert captures == [{"mac": MAC, "name": "Win11 lab", "disk": "sda",
                         "folder": "Classrooms", "description": ""}]
    assert posted(console, "/api/console/folders") == []


@native_tools
def test_a_gui_capture_into_a_new_folder_creates_it_first(tmp_path, console):
    result = run_shell(tmp_path, url_of(console),
                       gui_capture_call("Win11 lab", "New Course", "yes", "desc"))
    assert "CAPTURE rc=0" in result["out"], result["out"]
    assert posted(console, "/api/console/folders") == [{"name": "New Course"}]
    captures = posted(console, "/api/console/tasks/capture")
    assert len(captures) == 1
    assert captures[0]["folder"] == "New Course"
    assert captures[0]["description"] == "desc"
    order = [r["path"] for r in console.requests if r["method"] == "POST"]
    assert order.index("/api/console/folders") < order.index("/api/console/tasks/capture")


@native_tools
def test_a_hebrew_folder_name_never_reaches_the_server(tmp_path, console):
    """‏(הכרעת נדב, 30/08: שמות תיקיות ואימג'ים באנגלית או מספרים.)"""
    result = run_shell(tmp_path, url_of(console),
                       gui_capture_call("Win11 lab", "מעבדה", "yes"))
    assert "CAPTURE rc=1" in result["out"], result["out"]
    assert posted(console, "/api/console/folders") == []
    assert posted(console, "/api/console/tasks/capture") == []


@native_tools
def test_the_capture_request_carries_the_console_session_cookie(tmp_path, console):
    """הקליטה היא `admin_only` — היא חייבת לנסוע עם ה-cookie של הקונסולה."""
    run_shell(tmp_path, url_of(console), gui_capture_call("Base", "Lab", "no"))
    sent = [r for r in console.requests
            if r["path"] == "/api/console/tasks/capture"]
    assert sent and "imagesrv_session=" in sent[0]["cookie"]


@native_tools
def test_a_deploy_session_cannot_capture(tmp_path, console):
    """‏`gui_capture` הוא admin בלבד — גם כשהשרת היה מסרב ממילא."""
    console.role = "deploy"
    result = run_shell(tmp_path, url_of(console), gui_capture_call("Base", "Lab", "no"))
    assert "CAPTURE rc=1" in result["out"], result["out"]
    assert posted(console, "/api/console/tasks/capture") == []


# --- הכלל הסטטי: אין עברית על המסך של הסוכן ---------------------------------


#: ‏`gui_error "<למסך>" "<ללוג>"` — קבוצה 1 היא הארגומנט של המסך; אחריה
#: חייב לבוא ארגומנט שני לא-ריק, וממנו והלאה אין עברית.
SCREEN_ONLY = re.compile(r'^\s*gui_error\s+("[^"]*")\s+"[^"]+"')


@pytest.mark.parametrize("name", ("buildmenuitems.sh", "buildmenu.sh",
                                  "buildcapture.sh", "roomflow.sh", "guiparent.sh"))
def test_the_agent_libs_print_no_hebrew(name):
    """לקונסולת Linux אין פונט עברי ואין RTL — ‏0 שורות עברית, כמו ui.sh.

    חריג אחד, צר (#1503): ‏`gui_error "<עברית>" "<אנגלית>"`. הארגומנט
    הראשון נכתב רק לקובץ ה-error של הגואי הנייטיב (שהוא עברית RTL),
    והשני הוא מה שהולך ל-stderr/ללוג. בלי ארגומנט שני `gui_error` כותב
    ללוג את הראשון — ולכן שורה כזו אינה חריג והיא נופלת כאן."""
    path = lib_path(name)
    assert path.exists(), f"‏{name} חסר"
    hebrew = [line for line in path.read_text(encoding="utf-8").splitlines()
              if any("֐" <= ch <= "ת" for ch in line)]
    hebrew = [line for line in hebrew
              if not ((m := SCREEN_ONLY.match(line))
                      and not any("֐" <= ch <= "ת" for ch in line[m.end(1):]))]
    assert hebrew == []


@native_tools
def test_gui_role_comes_from_the_login_answer_not_from_me(tmp_path):
    """‏17/09 (#947): הכניסה במסך הבנייה נדחתה גם אחרי login=200 — `gui_role`
    קרא `/api/console/me`, שאינו ב-allowlist הקיוסק (רשימת אסטרה) ולכן 404
    על פורט הסוכן. התשובה של login כבר נושאת את התפקיד; ממנה קוראים.
    בקרה שלילית: על הקוד הישן הטסט נופל — אין `console_get`/רשת כאן, ורק
    `console_login.json` קיים."""
    lib = posix(REPO / "agent" / "lib" / "gui" / "guibridge.sh")
    run = tmp_path / "run"; run.mkdir()
    runp = posix(run)
    (run / "console_login.json").write_text('{"username":"nadav","role":"admin","idle_seconds":300}', encoding="utf-8")
    script = f"""
        RUN_DIR={runp}; SERVER=http://127.0.0.1:9; HTTP_TIMEOUT=1; HTTP_RETRIES=0
        console_get() {{ echo 'console_get must not be called' >&2; exit 97; }}
        . {lib}
        gui_role || exit 1
        printf '%s
' "$GUI_ROLE"
        rm -f {runp}/console_login.json
        gui_role && exit 2
        printf '{{"username":"x","role":"viewer"}}' > {runp}/console_login.json
        gui_role && exit 3
        echo ok
    """
    sf = tmp_path / "t.sh"; sf.write_text(script, encoding="utf-8")
    r = subprocess.run([BASH, str(sf)], capture_output=True, text=True, cwd=str(REPO),
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout
    assert r.stdout.split() == ["admin", "ok"], r.stdout
