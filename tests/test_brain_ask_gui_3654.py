"""#3654 (mockup approved by Nadav 18/11, lab/design-ask-1118/native-ask.html): the brain's question on the
machine's own screen -- the native GUI, and the agent between it and the server.

The server asks and the server's clock ends the question; the screen decides nothing:

- **The agent** keeps the hello's ``asks`` (ask_keep) with the moment it read them (``asks.at``), and copies them
  into the GUI's state on every screen (``ask_gui_records`` through ``gui_hello_state``): ``bask=`` / ``bask_opt=``,
  the deadline on this machine's clock. What the person chose goes out as ``brain-ask|<id>|<option>``; the bridge
  posts it only for a question the list names, with one of its own options (``ask_gui_answer``). A 409 (answered in
  the console, or expired) is not a failure: the next hello says what happened. A 200 replaces the question in
  asks.json with the server's own word on it, so the screen says "registered" from what the server said.
- **The GUI** draws the mockup's frames: the open question with its count, the default chosen, the red verb for a
  destructive option (n1-n3, n7, n8); after it closed, its result -- answered here, in the console, expired (n4-n6)
  -- for 5 s or until Enter, and then never again. A question that does not arrive whole is not drawn, and the
  state line says one arrived broken (no answer = the server's safe default, never destructive).

The GUI is read through its text log (IMAGESRV_GUI_TEXTLOG -- what the screen SAYS), its pixels, and a driver that
feeds the real key handling (``bask_modal``) and prints what it emitted. Lab only (cc + pango/cairo, bash, jq, curl).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_cloner_gui import AGENT, BASH, GUI, _count_color, _png_rgb, posix, sh
from test_shrink_gui import GUI_TOOLS

JQ = shutil.which("jq")
SHELL = requires_native(("bash", BASH), ("jq", JQ), why="the agent reads the server's answer with jq")
CURL = requires_native(("bash", BASH), ("jq", JQ), ("curl", shutil.which("curl")), why="a real POST through curl")

FSI, PDI = "\u2068", "\u2069"
WIPE = {"id": "wipe", "label_he": "למחוק את המידע הישן ולכתוב את הדיסק", "destructive": True, "verb_he": "מחיקת המידע",
        "consequence_he": "המידע הישן יימחק ולא יהיה אפשר לשחזר אותו", "outcome_he": ""}
SKIP = {"id": "skip", "label_he": "לדלג על הדיסק", "destructive": False, "verb_he": "", "consequence_he": "",
        "outcome_he": "הדיסק לא ייכתב"}
TITLE = "נמצא על הדיסק מידע של מערך RAID ישן"
DETAIL = "הדיסק היה פעם חלק ממערך RAID של Intel. כתיבה תמחק את המידע הזה, והדיסק ייכתב כדיסק רגיל."


def ask(**over) -> dict:
    """One question as the hello carries it (server/brain/ask.py view + effective/by_user)."""
    q = {"id": 41, "point": "A11", "title_he": TITLE, "detail_he": DETAIL, "options": [SKIP, WIPE], "columns": 1,
         "ttl_s": 1800, "safe_default": "skip", "safe_default_he": "לדלג על הדיסק", "who": ["admin", "machine"],
         "entity": {"kind": "machine", "key": "aa:bb:cc:00:00:01"}, "round_id": None, "mode": "ask", "state": "open",
         "state_he": "פתוחה", "created_at": "2026-11-18T12:00:00+00:00", "expires_at": "2026-11-18T12:30:00+00:00",
         "expires_in_s": 1476, "answer": None, "answer_he": None, "answered_by": None, "answered_role": None,
         "closed_at": None, "closed_note_he": None, "effective": None, "by_user": False}
    q.update(over)
    return q


# --- the agent: what the GUI is told, and what goes back ----------------------------------------------------------

def agent(tmp_path: Path, script: str, *, libs=("common", "ask"), stubs: str = "") -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True, exist_ok=True)
    head = (f"export LC_ALL=C RUN_DIR={posix(run)!r} GUI_DIR={posix(run / 'gui')!r} LOG_FILE={posix(run / 'log')!r} "
            "MAC=aa:bb:cc:00:00:01 SERVER=http://127.0.0.1:9 HTTP_TIMEOUT=5 HTTP_RETRIES=0 IMAGESRV_TEST=1; "
            + "".join(f". {posix(flat_lib())}/{n}.sh; " for n in libs))
    return sh(head + stubs + script)


def keep(tmp_path: Path, asks) -> Path:
    """The hello's answer through the real ask_keep: asks.json and the moment it was read (asks.at)."""
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "hello.json").write_text(json.dumps({"schema": 1, "asks": asks}, ensure_ascii=False), encoding="utf-8")
    out = agent(tmp_path, f'ask_keep {posix(run / "hello.json")!r}')
    assert out.returncode == 0, out.stderr
    return run


def records(tmp_path: Path) -> list[str]:
    out = agent(tmp_path, "ask_gui_records; echo rc=$?")
    assert out.stdout.splitlines()[-1] == "rc=0", out.stderr
    return out.stdout.splitlines()[:-1]


@SHELL
def test_the_list_is_kept_with_the_moment_it_was_read_on_this_machines_clock(tmp_path):
    before = int(time.time())
    run = keep(tmp_path, [ask()])
    assert (run / "asks.at").exists(), "the moment the list was read is not kept"
    at = int((run / "asks.at").read_text().strip())
    assert before <= at <= int(time.time()) + 1
    assert json.loads((run / "asks.json").read_text(encoding="utf-8"))[0]["id"] == 41


@SHELL
def test_an_open_question_reaches_the_gui_whole_with_a_deadline_on_this_machines_clock(tmp_path):
    run = keep(tmp_path, [ask()])
    assert (run / "asks.at").exists(), "the moment the list was read is not kept"
    at = int((run / "asks.at").read_text().strip())
    got = records(tmp_path)
    assert got == [f"bask=41|open|2|1|1800|{at + 1476}|skip|||||1|{TITLE}|{DETAIL}",
                   "bask_opt=41|skip|0|לדלג על הדיסק|||הדיסק לא ייכתב",
                   "bask_opt=41|wipe|1|למחוק את המידע הישן ולכתוב את הדיסק|מחיקת המידע|"
                   "המידע הישן יימחק ולא יהיה אפשר לשחזר אותו|"], got


@SHELL
def test_a_closed_question_carries_who_answered_and_when_and_open_ones_come_first(tmp_path):
    answered = ask(id=40, state="answered", answer="skip", answered_by="noc", answered_role="admin",
                   closed_at="2026-11-18T12:31:07+00:00", expires_in_s=None, who=["machine"])
    keep(tmp_path, [answered, ask(id=42)])
    got = [r for r in records(tmp_path) if r.startswith("bask=")]
    assert [r.split("|")[0] for r in got] == ["bask=42", "bask=40"], got           # the open one first
    f = got[1].split("|")
    assert f[1:12] == ["answered", "2", "1", "1800", "", "skip", "skip", "admin", "noc", "1795005067", "0"], f


@SHELL
def test_what_the_record_format_cannot_hold_is_cleaned_and_the_gui_gets_eight_at_most(tmp_path):
    many = [ask(id=i, title_he="שורה|אחת\nושתיים") for i in range(1, 11)]
    keep(tmp_path, many)
    heads = [r for r in records(tmp_path) if r.startswith("bask=")]
    assert len(heads) == 8, heads
    assert all("|שורה אחת ושתיים|" in h for h in heads), heads[0]


@SHELL
@pytest.mark.parametrize("broken", ["at", "json"])
def test_no_list_or_no_moment_for_it_is_no_question_on_the_screen(tmp_path, broken):
    run = keep(tmp_path, [ask()])
    if broken == "at":
        (run / "asks.at").unlink(missing_ok=True)
    else:
        (run / "asks.json").write_text("not json")
    assert records(tmp_path) == []


@SHELL
def test_every_screen_carries_the_question_through_gui_hello_state(tmp_path):
    keep(tmp_path, [ask()])
    out = agent(tmp_path, "GUI_SCREEN=cloner; gui_hello_state; echo rc=$?", libs=("common", "ask", "guistate"))
    lines = out.stdout.splitlines()
    assert lines[-1] == "rc=0", out.stderr
    assert any(l.startswith("bask=41|open|") for l in lines), out.stdout
    assert sum(l.startswith("bask_opt=41|") for l in lines) == 2, out.stdout


POST = ('http_post_json() { printf "%s\\n" "$1" > "$RUN_DIR/posted.url"; cp "$2" "$RUN_DIR/posted.body"; '
        'echo \'{"ok":true}\'; }; ')


def dispatch(tmp_path: Path, token: str, stubs: str = POST) -> tuple[int, Path]:
    run = tmp_path / "run"
    out = agent(tmp_path, f"token='{token}'; GUI_SCREEN=cloner; gui_dispatch; echo rc=$?",
                libs=("common", "ask", "guibridge"), stubs=stubs)
    return int(out.stdout.strip().splitlines()[-1].split("=")[1]), run


@SHELL
@pytest.mark.parametrize("option", ["skip", "wipe"])
def test_the_bridge_posts_the_persons_choice_with_no_sign_in(tmp_path, option):
    keep(tmp_path, [ask()])
    rc, run = dispatch(tmp_path, f"brain-ask|41|{option}")
    assert rc == 0
    assert (run / "posted.url").read_text().strip() == "http://127.0.0.1:9/api/v1/agent/ask-answer"
    assert json.loads((run / "posted.body").read_text()) == {"mac": "aa:bb:cc:00:00:01", "ask_id": 41, "option": option}


@SHELL
@pytest.mark.parametrize("token", ["brain-ask|41|format", "brain-ask|41|Skip", "brain-ask|43|skip",
                                   "brain-ask|41|skip|wipe", "brain-ask|x|skip", "brain-ask|41|"])
def test_an_option_the_question_does_not_list_is_never_posted(tmp_path, token):
    keep(tmp_path, [ask()])
    rc, run = dispatch(tmp_path, token)
    assert rc != 0 and not (run / "posted.url").exists()


@SHELL
def test_a_question_the_list_says_closed_is_not_posted_and_is_not_an_error(tmp_path):
    keep(tmp_path, [ask(state="expired", closed_at="2026-11-18T12:30:00+00:00", expires_in_s=None)])
    rc, run = dispatch(tmp_path, "brain-ask|41|wipe")
    assert rc == 0 and not (run / "posted.url").exists()
    assert not (run / "gui" / "error").exists()


class _Server:
    """A real HTTP endpoint for http_post_json (curl): one status, one body."""

    def __init__(self, status: int, body: dict):
        outer = self
        self.bodies: list[dict] = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                outer.bodies.append(json.loads(self.rfile.read(n)))
                data = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def answer_through_curl(tmp_path: Path, status: int, body: dict) -> tuple[str, Path, list]:
    keep(tmp_path, [ask()])
    srv = _Server(status, body)
    try:
        out = agent(tmp_path, f"SERVER={srv.url}; ask_answer 41 skip; echo rc=$?")
    finally:
        srv.close()
    return out.stdout.strip().splitlines()[-1], tmp_path / "run", srv.bodies


@CURL
def test_a_409_through_the_real_curl_is_closed_elsewhere_not_a_failure(tmp_path):
    rc, run, bodies = answer_through_curl(tmp_path, 409, {"detail": "closed"})
    assert bodies == [{"mac": "aa:bb:cc:00:00:01", "ask_id": 41, "option": "skip"}]
    assert rc == "rc=2", rc
    assert "already closed (409)" in (run / "agent.log").read_text(encoding="utf-8")   # common.sh: $RUN_DIR/agent.log


@CURL
def test_an_answer_the_server_took_is_its_own_word_on_the_screen_at_once(tmp_path):
    taken = ask(state="answered", answer="skip", answered_by="aa:bb:cc:00:00:01", answered_role="machine",
                closed_at="2026-11-18T12:31:07+00:00", expires_in_s=None)
    taken.pop("effective"); taken.pop("by_user")
    rc, run, _ = answer_through_curl(tmp_path, 200, {"ok": True, "ask": taken})
    assert rc == "rc=0", rc
    now = json.loads((run / "asks.json").read_text(encoding="utf-8"))[0]
    assert (now["state"], now["answered_role"], now["effective"], now["by_user"]) == ("answered", "machine", "skip", True)


@CURL
def test_a_server_error_is_a_failure_and_the_question_stays_open(tmp_path):
    rc, run, _ = answer_through_curl(tmp_path, 503, {"detail": "busy"})
    assert rc == "rc=1", rc
    assert json.loads((run / "asks.json").read_text(encoding="utf-8"))[0]["state"] == "open"


def test_the_kiosk_loads_ask_sh():
    """The kiosk is its own process (gui_libs): without ask.sh the state writer and the bridge do not have it."""
    body = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8").split("gui_libs() {", 1)[1].split("\n}", 1)[0]
    assert '. "$LIB_DIR/ask.sh"' in body


# --- the native GUI ------------------------------------------------------------------------------------------------

UPDATED = 1795523000   # the state's own clock: --png counts from it (renders do not depend on when they ran)
BASE = ("message=x|y\ncloner=office365\ndrawer_count=3\n"
        "drawer=1|sda|writing|41|100|\ndrawer=2|sdb|asking|0|100|\ndrawer=3|sdc|writing|38|100|\n"
        "machine_name=CLONE-02\nserver_name=imagesrv-ta\nserver_addr=10.44.0.1\ntz=UTC\n"
        f"updated={UPDATED}\n")


def bask(state="open", left=1476, answer="", role="", by="", closed="", opts=None, cols=1, safe="skip", ttl=1800,
         admin=1, detail=DETAIL, qid=41) -> str:
    opts = opts if opts is not None else [SKIP, WIPE]
    deadline = "" if left is None else str(UPDATED + left)
    out = f"bask={qid}|{state}|{len(opts)}|{cols}|{ttl}|{deadline}|{safe}|{answer}|{role}|{by}|{closed}|{admin}|{TITLE}|{detail}\n"
    for o in opts:
        out += (f"bask_opt={qid}|{o['id']}|{1 if o['destructive'] else 0}|{o['label_he']}|{o['verb_he']}|"
                f"{o['consequence_he']}|{o['outcome_he']}\n")
    return out


@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    from test_cloner_gui import _build_gui
    return _build_gui(tmp_path_factory.mktemp("gui"))


def render(binary: Path, out: Path, state: str, size: str = "1024x768") -> tuple[subprocess.CompletedProcess, list[str]]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "state.txt").write_text(state, encoding="utf-8", newline="\n")
    log = out / "text.log"
    env = dict(os.environ, IMAGESRV_GUI_TEXTLOG=str(log))
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "--png", posix(out / "c"), "--size", size, "--mac", "3C:52:82:A1:00:22",
                          "--ip", "10.44.0.31", "--state", posix(out / "state.txt")],
                         capture_output=True, text=True, timeout=180, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr[-2000:]
    return run, log.read_text(encoding="utf-8").splitlines()


def said(lines: list[str], card: str, theme: str = "light") -> list[str]:
    mark = f"@@ {card}-{theme}"
    assert mark in lines, f"no card {card}"
    start = lines.index(mark)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("@@ ")), len(lines))
    return lines[start + 1:end]


def has(words: list[str], text: str) -> bool:
    return any(text in w for w in words)


@GUI_TOOLS
def test_n1_the_open_question_over_the_round_with_its_count_and_the_default_chosen(binary, tmp_path):
    _, log = render(binary, tmp_path / "r", BASE + bask())
    words = said(log, "cloner")     # an existing card: the question is drawn over whatever screen is up
    for text in ("שאלה מהמוח", TITLE, DETAIL, "24:36", "עד ברירת המחדל", "לדלג על הדיסק", "ברירת המחדל כשאין תשובה",
                 "למחוק את המידע הישן ולכתוב את הדיסק", "פעולה הרסנית — לא תתבצע בלי התשובה הזו", "אישור", "Enter",
                 "ממתין להכרעה · שאלה אחת", "בחירה", "מספר"):
        assert has(words, text), (text, words)
    assert any("בלי תשובה תבוצע ברירת המחדל" in w and "«לדלג על הדיסק»" in w and "אפשר לענות גם מהקונסולה" in w
               for w in words), words
    assert has(words, f"{FSI}CLONE-02{PDI}"), words                 # the machine, isolated -- not the mockup's drawer
    assert not has(words, "מחיקת המידע"), words                      # the verb only when its option is chosen


@GUI_TOOLS
def test_n1_pixels_the_amber_frame_and_one_blue_button_over_the_screen(binary, tmp_path):
    render(binary, tmp_path / "plain", BASE)
    render(binary, tmp_path / "q", BASE + bask())
    for theme, warn, blue in (("light", (0xC2, 0x54, 0x00), (0x00, 0x79, 0xB8)), ("dark", (0xF0, 0x95, 0x4A), None)):
        w, h, ch, plain = _png_rgb(tmp_path / "plain" / f"c-cloner-{theme}.png")
        _w, _h, ch2, asked = _png_rgb(tmp_path / "q" / f"c-cloner-{theme}.png")

        def edge(px, chn):   # the amber frame's left edge: (1024 - 760) / 2 = 132, 2px inside the box -- rows of column 132
            return sum(_count_color(px[(y * w + 132) * chn:(y * w + 133) * chn], chn, warn, tol=10) for y in range(h))
        assert edge(asked, ch2) > 300 and edge(plain, ch) < 50, (theme, edge(asked, ch2), edge(plain, ch))
        if blue:
            assert _count_color(asked, ch2, blue, tol=6) > 25000                        # "אישור": 700 x 44


@GUI_TOOLS
def test_n2_a_destructive_option_chosen_turns_the_button_red_with_its_verb_and_says_its_cost(binary, tmp_path):
    _, log = render(binary, tmp_path / "r", BASE + bask())
    picked = said(log, "brain-ask-pick")
    assert has(picked, "מחיקת המידע") and has(picked, "המידע הישן יימחק ולא יהיה אפשר לשחזר אותו"), picked
    assert not has(picked, "פעולה הרסנית — לא תתבצע"), picked
    red = (0xE1, 0x22, 0x00)
    _w, _h, ch, px = _png_rgb(tmp_path / "r" / "c-brain-ask-pick-light.png")
    _w, _h, ch0, px0 = _png_rgb(tmp_path / "r" / "c-brain-ask-light.png")
    assert _count_color(px, ch, red, tol=6) > 25000 and _count_color(px0, ch0, red, tol=6) < 500


@GUI_TOOLS
def test_n3_the_last_minute_is_counted_in_seconds_in_the_warning_colour(binary, tmp_path):
    _, log = render(binary, tmp_path / "r", BASE + bask(left=12))
    words = said(log, "cloner")
    assert has(words, "00:12") and any("בעוד 12 שניות" in w and "תבוצע ברירת המחדל" in w for w in words), words
    _w, _h, ch, px = _png_rgb(tmp_path / "r" / "c-cloner-light.png")
    assert _count_color(px, ch, (0xA8, 0x47, 0x00), tol=8) > 150                       # --clr-warning-fg


@GUI_TOOLS
@pytest.mark.parametrize("frame,state,want", [
    ("n4", bask(state="answered", answer="skip", role="machine", by="3c:52:82:a1:00:22", closed="1795530667", left=None),
     ["התשובה נרשמה: «לדלג על הדיסק»", f"נרשמה ב-{FSI}14:31:07{PDI}. הדיסק לא ייכתב.", "נסגרה", "השאלה נסגרה"]),
    ("n5", bask(state="answered", answer="skip", role="admin", by="noc", closed="1795530667", left=None),
     [f"נענתה בקונסולה על ידי {FSI}noc{PDI}: «לדלג על הדיסק»",
      f"נענתה ב-{FSI}14:31:07{PDI}. הדיסק לא ייכתב. המסך ממשיך לפי התשובה.", "נסגרה"]),
    ("n6", bask(state="expired", closed="1795532190", left=None),
     ["לא נענתה בזמן: בוצעה ברירת המחדל: «לדלג על הדיסק»",
      f"עברו {FSI}30:00{PDI} בלי תשובה ({FSI}14:56:30{PDI}). הדיסק לא ייכתב. לא בוצעה שום פעולה שמוחקת.", "פקעה",
      "השאלה נסגרה · ברירת המחדל"]),
], ids=["n4", "n5", "n6"])
def test_n4_to_n6_after_it_closed_the_screen_says_what_the_server_said(binary, tmp_path, frame, state, want):
    _, log = render(binary, tmp_path / frame, BASE + state)
    words = said(log, "brain-ask")
    for text in want + ["סגירה", "החלון נסגר לבד בעוד 5 שניות, והמסך חוזר לסבב", TITLE]:
        assert has(words, text), (frame, text, words)
    assert not has(words, "אישור") and not has(words, "עד ברירת המחדל"), words


EIGHT = [{"id": i, "label_he": l, "destructive": False, "verb_he": "", "consequence_he": "", "outcome_he": ""}
         for i, l in (("all", "להוסיף את כל המקום לכונן C"), ("ratio", "לחלק לפי היחס שהיה במקור"),
                      ("free", "להשאיר את המקום ללא הקצאה"), ("dd", "להוסיף את המקום לכונן D"),
                      ("even", "לחלק שווה בין כל המחיצות"), ("orig", "לשכפל בגודל המקור בלי למתוח"),
                      ("skipd", "לדלג על הדיסק"))] + [
    {"id": "recov", "label_he": "למחוק את מחיצת השחזור", "destructive": True, "verb_he": "מחיקת מחיצת השחזור",
     "consequence_he": "המחיצה תימחק, אי אפשר לשחזר", "outcome_he": ""}]


@GUI_TOOLS
@pytest.mark.parametrize("name,state", [
    ("safe-destructive", bask(safe="wipe")),
    ("an-option-missing", bask().replace("|2|1|1800|", "|3|1|1800|")),
    ("bad-option-id", bask().replace("bask_opt=41|wipe|", "bask_opt=41|Wipe|")),
    ("no-verb", bask().replace("|מחיקת המידע|", "||")),
    ("no-deadline", bask(left=None)),
    ("answered-no-role", bask(state="answered", answer="skip", closed="1795530667", left=None)),
    ("answer-not-listed", bask(state="answered", answer="format", role="admin", closed="1795530667", left=None)),
    ("overlong-label", bask(opts=[SKIP, dict(WIPE, label_he="מ" * 90)])),
    ("orphan-option", "bask_opt=99|skip|0|x|||\n" + bask()),
], ids=lambda v: v if "|" not in v else "")
def test_a_question_that_did_not_arrive_whole_is_not_drawn_and_the_screen_says_one_arrived_broken(binary, tmp_path,
                                                                                                    name, state):
    run, log = render(binary, tmp_path / name, BASE + state)
    words = said(log, "cloner")
    if name != "orphan-option":   # the orphan's question itself is whole and drawn; the orphan is the broken one
        assert "שאלה מהמוח" not in words, (name, words)                 # the pill: no question on the screen
        assert "brain question 41 incomplete or malformed; not shown" in run.stderr, run.stderr[-800:]
    assert has(words, "שאלה מהמוח הגיעה פגומה ולא מוצגת — בלי תשובה תבוצע ברירת המחדל הבטוחה"), (name, words)


DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "state.h"
#if __has_include("brain_ask.h")
#include "brain_ask.h"
#define HAVE 1
#else
#define HAVE 0
#endif
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
#if HAVE
static int W = 1024, H = 768;
static void draw(App *a) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    cairo_t *cr = cairo_create(s); app_draw(a, cr, W, H); cairo_destroy(cr); cairo_surface_destroy(s);
}
static void load(App *a, const char *path) {
    FILE *f = fopen(path, "r"); if (!f) { perror(path); exit(2); }
    state_parse(&a->st, f); fclose(f); bask_after_reload(a); app_route(a); draw(a);
}
static int click(App *a, int id) {
    for (int h = a->nhits - 1; h >= 0; h--) if (a->hits[h].id == id) {
        Event e; memset(&e, 0, sizeof e); e.type = UIEV_CLICK; double s = a->scale > 0 ? a->scale : 1;
        e.x = (a->hits[h].r.x + a->hits[h].r.w / 2) * s; e.y = (a->hits[h].r.y + a->hits[h].r.h / 2) * s;
        return bask_modal(a, &e); }
    return -1;
}
#endif
int main(int argc, char **argv) {
#if !HAVE
    (void)argc; (void)argv; printf("@ no brain question in this GUI\n"); return 0;
#else
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    if (getenv("SIZE")) sscanf(getenv("SIZE"), "%dx%d", &W, &H);
    for (int i = 1; i < argc; i++) {
        const char *v = argv[i]; Event e; memset(&e, 0, sizeof e); int took = 0;
        if (!strncmp(v, "load:", 5)) { load(&a, v + 5); }
        else if (!strncmp(v, "key:", 4)) {
            e.type = UIEV_KEY; const char *k = v + 4;
            e.key = !strcmp(k, "ENTER") ? KEYSYM_ENTER : !strcmp(k, "UP") ? KEYSYM_UP : !strcmp(k, "DOWN") ? KEYSYM_DOWN
                  : !strcmp(k, "LEFT") ? KEYSYM_LEFT : !strcmp(k, "RIGHT") ? KEYSYM_RIGHT : KEYSYM_ESC;
            app_route(&a); took = bask_modal(&a, &e);
        } else if (!strncmp(v, "char:", 5)) { e.type = UIEV_CHAR; e.ch = v[5]; app_route(&a); took = bask_modal(&a, &e); }
        else if (!strcmp(v, "click:go")) took = click(&a, HIT_BASK_GO);
        else if (!strcmp(v, "click:close")) took = click(&a, HIT_BASK_CLOSE);
        else if (!strncmp(v, "click:opt", 9)) took = click(&a, HIT_BASK_OPT_BASE + atoi(v + 9));
        else if (!strcmp(v, "later")) { a.bask_sent_at -= BASK_RESEND_S + 1; a.bask_closed_at -= BASK_RESULT_S + 1; took = bask_tick(&a); }
        app_route(&a); draw(&a);
        const Bask *b = bask_current(&a);
        printf("@ %s took=%d id=%d state=%d sel=%d visible=%d\n", v, took, b ? b->id : 0, b ? b->state : 0,
               a.bask_sel, bask_visible(&a));
        if (getenv("GEOM") && b) {
            double s = a.scale > 0 ? a.scale : 1;
            for (int h = 0; h < a.nhits; h++) if (a.hits[h].id >= HIT_BASK_OPT_BASE || a.hits[h].id == HIT_BASK_GO)
                printf("@ hit %d %.0f %.0f %.0f %.0f %.0f\n", a.hits[h].id, a.hits[h].r.x, a.hits[h].r.y,
                       a.hits[h].r.w, a.hits[h].r.h, H / s);
        }
        fflush(stdout);
    }
    return 0;
#endif
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("baskdrv")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def drive(driver: Path, tmp_path: Path, states: dict[str, str], *events: str, size: str = "1024x768",
          geom: bool = False) -> list[str]:
    for name, text in states.items():
        (tmp_path / f"{name}.txt").write_text(BASE + text, encoding="utf-8", newline="\n")
    args = [e if not e.startswith("load:") else "load:" + posix(tmp_path / f"{e[5:]}.txt") for e in events]
    env = dict(os.environ, SIZE=size, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    if geom:
        env["GEOM"] = "1"
    run = subprocess.run([str(driver), *args], capture_output=True, text=True, encoding="utf-8", timeout=120,
                         stdin=subprocess.DEVNULL, env=env)
    assert run.returncode == 0, run.stderr[-1500:]
    return [l for l in run.stdout.splitlines() if l]


def sel_after(lines: list[str]) -> list[int]:
    return [int(l.split("sel=")[1].split()[0]) for l in lines if l.startswith("@ ") and " sel=" in l]


@GUI_TOOLS
def test_the_keys_choose_and_enter_sends_the_choice_once_until_the_server_says(driver, tmp_path):
    out = drive(driver, tmp_path, {"q": bask()}, "load:q", "key:DOWN", "key:ENTER", "key:ENTER", "char:1", "later",
                "key:ENTER")
    assert sel_after(out) == [0, 1, 1, 1, 0, 0, 0], out              # the default first; DOWN; 1 = the first
    sent = [l for l in out if l.startswith("brain-ask|")]
    assert sent == ["brain-ask|41|wipe", "brain-ask|41|skip"], out   # once, and again only after the resend window


@GUI_TOOLS
def test_in_two_columns_the_arrows_walk_the_grid_right_to_left_and_the_digits_pick(driver, tmp_path):
    out = drive(driver, tmp_path, {"q": bask(opts=EIGHT, cols=2, safe="ratio", ttl=600, left=540, detail="")},
                "load:q", "key:RIGHT", "key:LEFT", "key:DOWN", "key:UP", "char:8", "key:DOWN", "char:9", "click:opt4",
                "click:go")
    assert sel_after(out) == [1, 0, 1, 3, 1, 7, 7, 7, 4, 4], out
    assert [l for l in out if l.startswith("brain-ask|")] == ["brain-ask|41|even"], out


@GUI_TOOLS
@pytest.mark.parametrize("size", ["640x480", "1024x768", "1920x1080"])
def test_n8_eight_options_fit_the_screen_with_no_scrolling(driver, tmp_path, size):
    out = drive(driver, tmp_path, {"q": bask(opts=EIGHT, cols=2, safe="ratio", ttl=600, left=540, detail="")},
                "load:q", size=size, geom=True)
    hits = [list(map(float, l.split()[2:])) for l in out if l.startswith("@ hit ")]
    assert len(hits) == 9, out                                      # eight options and the button
    for hid, x, y, w, h, screen_h in hits:
        assert y >= 0 and y + h <= screen_h - 36, (size, hid, y, h)  # above the status bar, all of it


@GUI_TOOLS
def test_a_result_closes_on_enter_or_after_five_seconds_and_never_comes_back(driver, tmp_path):
    states = {"open": bask(), "done": bask(state="answered", answer="skip", role="admin", by="noc",
                                           closed="1795530667", left=None)}
    out = drive(driver, tmp_path, states, "load:open", "load:done", "key:ENTER", "load:done")
    ids = [(int(l.split("id=")[1].split()[0]), int(l.split("state=")[1].split()[0]))
           for l in out if l.startswith("@ ") and " id=" in l]
    assert ids == [(41, 1), (41, 2), (0, 0), (0, 0)], out           # open; its result; closed here; not again
    out = drive(driver, tmp_path, states, "load:open", "load:done", "later")
    ids = [int(l.split("id=")[1].split()[0]) for l in out if l.startswith("@ ") and " id=" in l]
    assert ids == [41, 41, 0], out                                  # by itself after 5 s


@GUI_TOOLS
def test_a_closed_question_that_was_never_on_the_screen_does_not_pop_up_as_a_result(driver, tmp_path):
    out = drive(driver, tmp_path, {"done": bask(state="expired", closed="1795532190", left=None)}, "load:done")
    assert out == ["@ load:" + posix(tmp_path / "done.txt") + " took=0 id=0 state=0 sel=0 visible=0"], out


@GUI_TOOLS
def test_a_cancelled_question_leaves_the_screen(driver, tmp_path):
    states = {"open": bask(), "gone": bask(state="cancelled", left=None)}
    out = drive(driver, tmp_path, states, "load:open", "load:gone")
    assert [int(l.split("id=")[1].split()[0]) for l in out if l.startswith("@ ") and " id=" in l] == [41, 0], out
