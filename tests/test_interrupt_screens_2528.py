"""#2528 (שלב 1ב) — השאלה על משכפל שכבה באמצע סבב, במסך המשכפל ובמסך מחשב הבנייה.

הכרעות נדב 12/11:

- **במחשב הבנייה שפתח את הסבב** עונה כל מי שמחובר לקיוסק (מנהל או משתמש הפצה): דיאלוג עם
  שתי התשובות של הקונסולה, והתשובה נשלחת עם המכונות שהדיאלוג הציג. Esc = "לא עכשיו".
- **במסך המשכפל עצמו** אין משתמש מחובר: הוא **מודיע** ("הבנתי"), ואינו עונה.
- בלי תשובה לא נכתב דבר — הסוכן אינו מכריע שום דבר בעצמו.

שלוש שכבות: השרת (השדה `interrupt` בתשובת ה-hello של המשכפל), הסוכן (askgui.sh /
interrupt.sh / roomflow.sh, מול גואי מזויף), והגואי הנייטיבי (state + PNG, על שרת הטסטים בלבד).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_cloner_gui import sh
from test_interrupt_question_2528 import (   # noqa: F401 — room_server הוא fixture
    BOOT_A, BOOT_B, CLONER1, CLONER2, beat, hello, interrupted, post_answer, progress, question,
    room_server, start,
)

pytest.importorskip("fastapi")

# --- השרת: השדה `interrupt` בתשובת ה-hello של המשכפל ---------------------------------------


def test_the_interrupted_cloner_is_told_in_its_hello_answer(room_server):
    anon = room_server["anon"]
    interrupted(room_server, done=("sda",))
    told = hello(anon, CLONER1, ["S1", "S2"], BOOT_B, joining=False).get("interrupt")
    assert told is not None, "המשכפל שעליו השאלה אינו מקבל אותה ב-hello"
    assert told["round_id"].startswith("room_")
    assert told["disks"] == [{"port": 2, "was_port": 2}], told


def test_the_hello_answer_says_where_the_disk_sits_now(room_server):
    anon = room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "done", "sdb": "writing"})
    hello(anon, CLONER1, ["S2", "S1"], BOOT_B)                       # המגירות הוחלפו בזמן הכיבוי
    told = hello(anon, CLONER1, ["S2", "S1"], BOOT_B, joining=False).get("interrupt")
    assert told is not None and told["disks"] == [{"port": 1, "was_port": 2}], told
    gone = hello(anon, CLONER1, ["S1"], BOOT_B, joining=False).get("interrupt")   # S2 נשלף
    assert gone is not None and gone["disks"] == [{"port": None, "was_port": 2}], gone


def test_another_cloner_and_an_answered_question_are_not_told(room_server):
    anon, deploy = room_server["anon"], room_server["deploy"]
    interrupted(room_server)
    assert "interrupt" not in hello(anon, CLONER2, ["T1"], BOOT_A, joining=False)
    beat(room_server)
    assert post_answer(deploy, "rewrite").status_code == 200
    assert "interrupt" not in hello(anon, CLONER1, ["S1", "S2"], BOOT_B, joining=False)


def test_a_cloner_with_no_question_gets_no_field(room_server):
    start(room_server)
    assert "interrupt" not in hello(room_server["anon"], CLONER1, ["S1", "S2"], BOOT_A, joining=False)


# --- דיסק שלא הושלם והוחזר לחריץ שלא נבחר לסבב ---------------------------------------------


def _three_drawers_two_selected(room_server) -> str:
    """משכפל עם שלוש מגירות; הסבב נבחר על חריצים 1 ו-2 בלבד. S1 הושלם, S2 לא, והמשכפל כבה."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    hello(anon, CLONER1, ["S1", "S2", "S3"], BOOT_A, joining=False)
    opened = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": [{"mac": CLONER1, "ports": [1, 2]}]})
    assert opened.status_code == 200, opened.text
    for _ in range(3):
        session = hello(anon, CLONER1, ["S1", "S2", "S3"], BOOT_A)["session"]
        if session is not None and session["state"] == "running":
            break
    assert session["target_ports"] == [1, 2], session
    progress(anon, session["id"], CLONER1, {"sda": "done", "sdb": "writing"})
    return session["id"]


def test_rewrite_follows_the_incomplete_disk_to_a_slot_that_was_not_chosen(room_server):
    """S2 (לא הושלם, חריץ 2) הוחזר לחריץ 3 שלא נבחר; S3 הזר עבר לחריץ 2. "כתוב מחדש" כותב
    את S2 איפה שהוא יושב — ולא את S3 שבחריץ הישן, ולא את S1 שהושלם."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _three_drawers_two_selected(room_server)
    now = ["S1", "S3", "S2"]
    assert hello(anon, CLONER1, now, BOOT_B)["session"] is None
    disks = question(deploy)["machines"][0]["disks"]
    assert [(d["serial"], d["was_port"], d["port"]) for d in disks] == [("S2", 2, 3)], disks
    beat(room_server)
    assert post_answer(deploy, "rewrite").status_code == 200

    session = hello(anon, CLONER1, now, BOOT_B)["session"]
    assert session is not None, "הדיסק שלא הושלם לא נכתב כי הוא בחריץ שלא נבחר"
    assert session["target_ports"] == [3], f"נכתב לפי החריץ ולא לפי הדיסק: {session['target_ports']}"
    assert session["target_disks"] == [{"port": 3, "serial": "S2", "size_bytes": 256060514304}]
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "round_disk_followed" in events


def test_a_disk_that_stayed_in_its_slot_changes_nothing_in_the_selection(room_server):
    """שומר: בלי תזוזה הבחירה של הסבב נשארת כמו שהיא."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _three_drawers_two_selected(room_server)
    now = ["S1", "S2", "S3"]
    assert hello(anon, CLONER1, now, BOOT_B)["session"] is None
    beat(room_server)
    assert post_answer(deploy, "rewrite").status_code == 200
    assert hello(anon, CLONER1, now, BOOT_B)["session"]["target_ports"] == [2]
    slots = json.loads(ctx.conn.execute("SELECT target_slots_json FROM room_rounds").fetchone()[0])
    assert slots[0]["ports"] == [1, 2]


# --- הסוכן: הבקשה לגואי, התשובה, ומי רשאי לענות איפה ---------------------------------------

shell = requires_native(("bash", BASH), ("jq", __import__("shutil").which("jq")),
                        why="הסוכן קורא את תשובת השרת עם jq")

LIBS = ("common", "waits", "jsonq", "attended", "askgui", "roomflow", "interrupt")


def box(tmp_path: Path, role: str = "cloner") -> tuple[str, Path]:
    run = tmp_path / "run"
    gui = run / "gui"
    gui.mkdir(parents=True, exist_ok=True)
    pre = (f"export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} LOG_FILE={posix(run / 'log')!r} "
           f"IMAGESRV_TEST=1 D_ROLE={role} RESP={posix(run / 'response.json')!r}; "
           + "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
           + 'attended() { shift; "$@"; }; attended_power_check() { :; }; '
           + "sleep 30 & _gui_pid=$!; trap 'kill $_gui_pid 2>/dev/null' EXIT; ")
    return pre, run


def clicker(answer: str | None) -> str:
    """גואי מזויף: מחכה לבקשה, שומר אותה, ועונה (או לא עונה)."""
    script = ('( for _ in $(seq 1 100); do [ -s "$GUI_DIR/ask-request" ] && break; sleep 0.1; done; '
              'cp "$GUI_DIR/ask-request" "$RUN_DIR/request.seen"; '
              'N=$(IFS="|" read -r n _ < "$GUI_DIR/ask-request"; echo "$n"); ')
    if answer is not None:
        script += f'printf "%s|%s\\n" "$N" {answer} > "$GUI_DIR/ask-decision"; '
    return script + ") & "


ROWS = {"interrupt": {"round_id": "room_ab12cd34",
                      "disks": [{"port": 2, "was_port": 2}, {"port": None, "was_port": 3}]}}


@shell
def test_the_rows_are_the_port_now_and_the_port_it_was_written_in(tmp_path):
    pre, run = box(tmp_path)
    (run / "response.json").write_text(json.dumps(ROWS))
    out = sh(pre + 'interrupt_rows "$RESP" .interrupt.disks; echo "rc=$?"')
    assert out.stdout.split() == ["2|2", "0|3", "rc=0"], out.stdout + out.stderr


@shell
@pytest.mark.parametrize("disks", [[], "x", [{"port": 1}], [{"port": 1, "was_port": 0}],
                                   [{"port": 1, "was_port": 100}], [{"port": 1, "was_port": "2"}]])
def test_rows_that_cannot_be_read_whole_are_not_rows(tmp_path, disks):
    pre, run = box(tmp_path)
    (run / "response.json").write_text(json.dumps({"interrupt": {"round_id": "room_ab12cd34", "disks": disks}}))
    out = sh(pre + 'interrupt_rows "$RESP" .interrupt.disks; echo "rc=$?"')
    assert out.stdout.strip().splitlines()[-1] != "rc=0", out.stdout


@shell
def test_the_cloner_is_told_once_and_answers_nothing(tmp_path):
    """מסך המשכפל: בקשה אחת עם subject=self, "הבנתי" — ושום דבר לא נשלח לשרת."""
    pre, run = box(tmp_path)
    (run / "response.json").write_text(json.dumps(ROWS))
    posted = "http_post_json() { echo posted >> \"$RUN_DIR/posted\"; }; console_post() { echo posted >> \"$RUN_DIR/posted\"; echo 200; }; "
    out = sh(pre + posted + clicker("ok") + 'interrupt_told; echo "rc=$?"; interrupt_told; cat "$RUN_DIR/interrupt.told"', timeout=60)
    assert "rc=0" in out.stdout and out.stdout.strip().endswith("room_ab12cd34"), out.stdout + out.stderr
    assert (run / "request.seen").read_text().splitlines() == ["1-0|interrupt|2|self", "2|2", "0|3"]
    assert not (run / "posted").exists(), "המשכפל שלח תשובה לשרת"
    assert not (run / "gui/ask-request").exists(), "השאלה הוצגה שוב אחרי 'הבנתי'"


@shell
def test_a_cloner_whose_answer_names_no_question_shows_nothing(tmp_path):
    pre, run = box(tmp_path)
    (run / "response.json").write_text(json.dumps({"schema": 1, "known": True}))
    (run / "interrupt.told").write_text("room_old\n")
    out = sh(pre + 'interrupt_told; echo "rc=$?"; ls "$RUN_DIR"')
    assert "rc=0" in out.stdout and "interrupt.told" not in out.stdout.split("rc=0")[1], out.stdout
    assert not (run / "gui/ask-request").exists()


@shell
def test_the_cloners_notice_closes_when_the_question_was_answered_elsewhere(tmp_path):
    """דופק ה-hello בזמן ההמתנה כבר אינו נושא את השאלה → `interrupt.gone` → הדיאלוג נסגר לבד."""
    pre, run = box(tmp_path)
    (run / "response.json").write_text(json.dumps(ROWS))
    (run / "beat.json").write_text(json.dumps({"schema": 1, "known": True}))
    (run / "unread.json").write_text("not json")
    closer = ('( for _ in $(seq 1 100); do [ -s "$GUI_DIR/ask-request" ] && break; sleep 0.1; done; '
              'interrupt_keep "$RUN_DIR/unread.json"; [ ! -e "$RUN_DIR/interrupt.gone" ] && echo kept > "$RUN_DIR/kept"; '
              'interrupt_keep "$RUN_DIR/beat.json" ) & ')
    out = sh(pre + closer + 'INTERRUPT_ASK_S=20; interrupt_told; echo "rc=$?"', timeout=30)
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    assert (run / "kept").exists(), "תשובה שלא נקראה סגרה את ההודעה"
    assert (run / "interrupt.gone").exists() and not (run / "gui/ask-request").exists()


ROOM = {"round": {"id": "room_ab12cd34", "state": "active", "interrupt": {
    "answer": None, "machines": [
        {"mac": "aa:bb:cc:00:00:21", "name": "c1", "disks": [{"serial": "S2", "port": 2, "was_port": 2}]},
        {"mac": "aa:bb:cc:00:00:22", "name": "c2", "disks": [{"serial": "T1", "port": None, "was_port": 1}]}]}}}


def _build(tmp_path, answer, code="200", room=ROOM):
    pre, run = box(tmp_path, role="build")
    (run / "room.json").write_text(json.dumps(room))
    post = ('console_post() { cp "$2" "$RUN_DIR/posted.json"; echo "$1" > "$RUN_DIR/posted.path"; echo ' + code + '; }; ')
    out = sh(pre + post + 'interrupt_room_watch "$RUN_DIR/room.json"; ' + clicker(answer)
             + 'INTERRUPT_ASK_S=3; interrupt_ask_build; echo "rc=$?"', timeout=60)
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    return run


@shell
@pytest.mark.parametrize("answer", ["rewrite", "close"])
def test_the_build_machine_posts_the_answer_with_the_machines_it_showed(tmp_path, answer):
    run = _build(tmp_path, answer)
    assert (run / "request.seen").read_text().splitlines() == ["1-0|interrupt|2|cloner", "2|2", "0|1"]
    assert (run / "posted.path").read_text().strip() == "room/interrupt"
    assert json.loads((run / "posted.json").read_text()) == {
        "answer": answer, "machines": ["aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22"]}


@shell
@pytest.mark.parametrize("answer", ["later", None, "ok"])
def test_not_now_or_no_answer_posts_nothing_and_is_not_asked_again(tmp_path, answer):
    run = _build(tmp_path, answer)
    assert not (run / "posted.json").exists(), f"{answer!r} נשלח לשרת כתשובה"
    assert (run / "interrupt.asked").exists()


@shell
def test_an_answer_that_did_not_reach_the_server_is_asked_again(tmp_path):
    run = _build(tmp_path, "rewrite", code="503")
    assert not (run / "interrupt.asked").exists(), "תשובה שלא הגיעה לשרת נחשבה כאילו נענתה"


@shell
def test_a_room_without_an_open_question_asks_nothing_and_closes_a_dialog(tmp_path):
    pre, run = box(tmp_path, role="build")
    (run / "room.json").write_text(json.dumps(ROOM))
    answered = json.loads(json.dumps(ROOM))
    answered["round"]["interrupt"]["answer"] = "rewrite"
    (run / "answered.json").write_text(json.dumps(answered))
    out = sh(pre + 'interrupt_room_watch "$RUN_DIR/room.json"; [ -s "$RUN_DIR/room.interrupt" ] && echo open; '
             'interrupt_room_watch "$RUN_DIR/answered.json"; [ -e "$RUN_DIR/room.interrupt" ] || echo closed; '
             '[ -e "$RUN_DIR/interrupt.gone" ] && echo gone; interrupt_ask_build; echo "rc=$?"')
    assert out.stdout.split() == ["open", "closed", "gone", "rc=0"], out.stdout + out.stderr
    assert not (run / "gui/ask-request").exists()


def _dispatch(tmp_path, request: str, token: str, screen: str) -> bool:
    gui = tmp_path / "gui"
    gui.mkdir(parents=True)
    (gui / "ask-request").write_text(request, newline="\n")
    sh(f"export GUI_DIR={posix(gui)!r}; . {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/gui/askgui.sh; "
       f"GUI_SCREEN={screen}; token='{token}'; gui_ask_dispatch")
    return (gui / "ask-decision").exists()


@requires_native(("bash", BASH))
def test_the_question_is_answered_on_both_screens_and_only_with_its_own_words(tmp_path):
    ask = "8-0|interrupt|1|cloner\n2|2\n"
    assert _dispatch(tmp_path / "a", ask, "ask-rewrite|8-0", "")           # מחשב הבנייה
    assert _dispatch(tmp_path / "b", ask, "ask-close|8-0", "")
    assert _dispatch(tmp_path / "c", ask, "ask-later|8-0", "")
    assert _dispatch(tmp_path / "d", "8-0|interrupt|1|self\n2|2\n", "ask-ok|8-0", "cloner")   # המשכפל
    assert not _dispatch(tmp_path / "e", ask, "ask-wipe|8-0", "")           # לא תשובה של השאלה הזו
    assert not _dispatch(tmp_path / "f", ask, "ask-rewrite|7-0", "")        # לא הבקשה שעל המסך


@shell
@pytest.mark.parametrize("name,subject,rows", [
    ("subject", "sda", "2|2\n"), ("field", "cloner", "2\n"), ("was", "cloner", "2|0\n"),
    ("port", "cloner", "100|2\n"), ("word", "self", "a|2\n"), ("none", "self", "")])
def test_a_malformed_question_is_never_published(tmp_path, name, subject, rows):
    pre, run = box(tmp_path)
    (run / "rows").write_text(rows, newline="\n")
    out = sh(pre + f'gui_ask_choice interrupt {subject} "$RUN_DIR/rows" 2; echo "rc=$?"', timeout=30)
    assert out.stdout.strip() == "rc=1", (name, out.stdout)
    assert not (run / "gui/ask-request").exists(), name


# --- הגואי הנייטיבי (שרת הטסטים: cc + pango/cairo) ------------------------------------------

from test_cloner_gui import _build_gui, _count_color, _png_rgb, _token  # noqa: E402
from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render  # noqa: E402

BUILD_ASK = "ask_prompt=8-0|interrupt|2|cloner\nask_row=2|2\nask_row=1|3\n"
CLONER_TOLD = "ask_prompt=8-0|interrupt|2|self\nask_row=2|2\nask_row=0|3\n"


@GUI_TOOLS
def test_the_build_machine_draws_the_question_with_two_answers(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, tmp_path / "r", BASE_STATE + BUILD_ASK)
    _w, _h, ch, plain = _png_rgb(tmp_path / "r" / "card-menu-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "r" / "card-build-interrupt-light.png")
    warn, primary = _token("--clr-warning"), _token("--clr-action")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500, "אין מסגרת שאלה"
    assert _count_color(asked, ch2, primary, tol=12) - _count_color(plain, ch, primary, tol=12) > 1500, "אין כפתור ראשי"
    for theme in ("light", "dark"):
        assert (tmp_path / "r" / f"card-build-interrupt-{theme}.png").exists()


@GUI_TOOLS
def test_the_cloner_draws_a_notice_with_one_plain_button(tmp_path):
    """מסך המשכפל: מודיע, לא שואל — אין כפתור ראשי (כחול), ויש מסגרת כתומה."""
    binary = _build_gui(tmp_path)
    _render(binary, tmp_path / "r", BASE_STATE + CLONER_TOLD)
    _w, _h, ch, plain = _png_rgb(tmp_path / "r" / "card-cloner-light.png")
    _w, _h, ch2, told = _png_rgb(tmp_path / "r" / "card-cloner-interrupt-light.png")
    warn, primary = _token("--clr-warning"), _token("--clr-action")
    assert _count_color(told, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    assert _count_color(told, ch2, primary, tol=12) - _count_color(plain, ch, primary, tol=12) < 500, \
        "במסך המשכפל יש כפתור תשובה ראשי"


@GUI_TOOLS
@pytest.mark.parametrize("name,text", [
    ("subject", "ask_prompt=8-0|interrupt|1|sda\nask_row=2|2\n"),
    ("count", "ask_prompt=8-0|interrupt|2|self\nask_row=2|2\n"),
    ("norows", "ask_prompt=8-0|interrupt|0|self\n"),
    ("was", "ask_prompt=8-0|interrupt|1|cloner\nask_row=2|0\n"),
    ("field", "ask_prompt=8-0|interrupt|1|cloner\nask_row=2\n")])
def test_a_broken_question_is_not_drawn(tmp_path, name, text):
    binary = _build_gui(tmp_path)
    run = _render(binary, tmp_path / name, BASE_STATE + text)
    assert "question incomplete or malformed" in run.stderr, name
