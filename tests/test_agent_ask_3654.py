"""#3654 (K14): הסוכן והשאלות של המוח (`agent/lib/core/ask.sh`).

הסוכן **אינו מחליט דבר**: הוא שומר מה שהשרת אמר (`asks` בתשובת ה-hello), מדווח לשרת מה שאדם ענה ליד המסך, וממתין.
מה שנבדק:

- **"לא נקרא" אינו "אין":** תשובה בלי מערך `asks` קריא (`null`, חסר, פגום, שרת ישן) משאירה את הרשימה האחרונה כמו שהיא;
  מערך ריק -- שנקרא -- מחליף אותה.
- **מה קרה לשאלה:** `open` / `answered X` / `expired X` / `cancelled X` / `gone` / `unknown`. ``unknown`` ו-``gone`` אינם
  החלטה: הקורא נשאר בברירת המחדל שהייתה לו קודם.
- **תשובה:** רק מזהה מספרי ואפשרות `[a-z][a-z0-9_]*`; גוף ה-JSON המדויק; ``0`` רק כשהשרת קיבל.
- **המתנה:** מחכה לשאלה שהוכרעה, ועוצרת בתקרה בלי להמציא תשובה.
- שאלה שאינה מכונה זו לא נקראת: הסוכן קורא רק את מה שהשרת שלח אליו (הסינון בשרת, `ask.for_machine`).

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix, sh

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
JQ = shutil.which("jq")

pytestmark = requires_native(("bash", BASH), ("jq", JQ))

OPEN = '{"id":7,"state":"open","effective":null,"by_user":false}'
ANSWERED = '{"id":8,"state":"answered","effective":"wipe","by_user":true}'
EXPIRED = '{"id":9,"state":"expired","effective":"skip","by_user":false}'
CANCELLED = '{"id":10,"state":"cancelled","effective":"skip","by_user":false}'


def run(tmp_path: Path, script: str, *, stubs: str = ""):
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    head = (f"export LC_ALL=C RUN_DIR={posix(run_dir)!r} MAC=aa:bb:cc:00:00:01 SERVER=http://srv HTTP_TIMEOUT=2; "
            'log() { echo "$*" >&2; }; '
            f'. {posix(AGENT)}/lib/core/ask.sh; ')
    return sh(head + stubs + script)


def hello(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8", newline="\n")
    return posix(path)


def state(tmp_path: Path, answer_body: str, ask_id: int) -> str:
    out = run(tmp_path, f'ask_keep {hello(tmp_path, "h.json", answer_body)}; ask_state {ask_id}')
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# --- מה שנשמר: לא נקרא אינו "אין" --------------------------------------------------------------------------------------


@pytest.mark.parametrize("ask_id, want", [(7, "open"), (8, "answered wipe"), (9, "expired skip"),
                                          (10, "cancelled skip"), (11, "gone")])
def test_the_state_of_each_question_is_read_from_the_last_hello(tmp_path, ask_id, want):
    body = f'{{"schema":1,"asks":[{OPEN},{ANSWERED},{EXPIRED},{CANCELLED}]}}'
    assert state(tmp_path, body, ask_id) == want


def test_before_any_list_was_read_a_question_is_unknown_not_gone(tmp_path):
    out = run(tmp_path, "ask_state 7")
    assert out.returncode == 0 and out.stdout.strip() == "unknown", out.stderr


@pytest.mark.parametrize("unreadable", [
    '{"schema":1,"asks":null}',       # השרת לא הצליח לקרוא
    '{"schema":1}',                   # שרת ישן
    '{"schema":1,"asks":"x"}', '{"schema":1,"asks":{"id":7}}', "not json", "[]", ""])
def test_an_answer_with_no_readable_list_keeps_the_last_one(tmp_path, unreadable):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "asks.json").write_text(f"[{OPEN}]", newline="\n")
    out = run(tmp_path, f'ask_keep {hello(tmp_path, "h.json", unreadable)}; ask_state 7')
    assert out.returncode == 0 and out.stdout.strip() == "open", (out.stdout, out.stderr)
    assert not (run_dir / "asks.json.next").exists()


def test_an_empty_list_that_was_read_replaces_the_old_one_and_the_question_is_gone(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "asks.json").write_text(f"[{OPEN}]", newline="\n")
    out = run(tmp_path, f'ask_keep {hello(tmp_path, "h.json", '{"asks":[]}')}; ask_state 7')
    assert out.stdout.strip() == "gone", (out.stdout, out.stderr)


@pytest.mark.parametrize("bad", ["", "x", "1; echo hi", "-1", "1.5"])
def test_a_question_number_that_is_not_a_number_is_unknown(tmp_path, bad):
    out = run(tmp_path, f"ask_state '{bad}'")
    assert out.returncode == 0 and out.stdout.strip() == "unknown", out.stderr


# --- התשובה ליד המסך ---------------------------------------------------------------------------------------------------

POSTED = ('http_post_json() { printf "%s\\n" "$1" > "$RUN_DIR/posted.url"; cp "$2" "$RUN_DIR/posted.body"; '
          '[ "${STUB_POST:-ok}" = ok ] && echo \'{"ok":true}\'; }; ')


def test_an_answer_is_posted_with_this_machine_the_question_and_the_option(tmp_path):
    out = run(tmp_path, "ask_answer 7 skip; echo rc=$?", stubs=POSTED)
    assert out.stdout.strip() == "rc=0", out.stderr
    run_dir = tmp_path / "run"
    assert (run_dir / "posted.url").read_text().strip() == "http://srv/api/v1/agent/ask-answer"
    assert (run_dir / "posted.body").read_text() == '{"mac":"aa:bb:cc:00:00:01","ask_id":7,"option":"skip"}'


@pytest.mark.parametrize("ask_id, option", [("", "skip"), ("x", "skip"), ("7", ""), ("7", "Skip"), ("7", "1skip"),
                                            ("7", 'sk"ip'), ("7", "skip;x"), ("7 8", "skip"), ("7", "a b")])
def test_an_answer_that_is_not_a_question_number_and_a_listed_looking_option_is_never_posted(tmp_path, ask_id, option):
    out = run(tmp_path, f"ask_answer '{ask_id}' '{option}'; echo rc=$?", stubs=POSTED)
    assert out.stdout.strip() == "rc=1", (out.stdout, out.stderr)
    assert not (tmp_path / "run" / "posted.url").exists()


def test_an_answer_the_server_did_not_take_is_not_reported_as_given(tmp_path):
    out = run(tmp_path, "ask_answer 7 skip; echo rc=$?", stubs='http_post_json() { return 22; }; ')
    assert out.stdout.strip() == "rc=1" and "stays open" in out.stderr, (out.stdout, out.stderr)


# --- ההמתנה -----------------------------------------------------------------------------------------------------------


def test_waiting_returns_the_decision_when_the_server_reports_one(tmp_path):
    # ה-beat השני כבר מביא תשובה של מי שענה בקונסולה
    stubs = ('attended_hello() { n=$(cat "$RUN_DIR/beats" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$RUN_DIR/beats"; '
             f'if [ "$n" -ge 2 ]; then echo \'[{ANSWERED.replace("8", "7")}]\' > "$RUN_DIR/asks.json"; '
             f'else echo \'[{OPEN}]\' > "$RUN_DIR/asks.json"; fi; }}; ATTENDED_BEAT_S=0; ')
    out = run(tmp_path, 'ask_wait 7 30 "שאלה"; echo rc=$?', stubs=stubs)
    assert out.stdout.splitlines() == ["answered wipe", "rc=0"], (out.stdout, out.stderr)
    assert (tmp_path / "run" / "beats").read_text().strip() == "2"


def test_waiting_says_what_happened_when_the_question_expired(tmp_path):
    stubs = f'attended_hello() {{ echo \'[{EXPIRED.replace("9", "7")}]\' > "$RUN_DIR/asks.json"; }}; '
    out = run(tmp_path, "ask_wait 7 30; echo rc=$?", stubs=stubs)
    assert out.stdout.splitlines() == ["expired skip", "rc=0"], (out.stdout, out.stderr)


def test_waiting_gives_up_at_the_ceiling_without_inventing_an_answer(tmp_path):
    stubs = f'attended_hello() {{ echo \'[{OPEN}]\' > "$RUN_DIR/asks.json"; }}; ATTENDED_BEAT_S=1; '
    out = run(tmp_path, "ask_wait 7 0; echo rc=$?", stubs=stubs)
    assert out.stdout.splitlines() == ["rc=1"], (out.stdout, out.stderr)


def test_waiting_for_a_question_the_server_never_named_is_not_a_decision(tmp_path):
    stubs = 'attended_hello() { :; }; ATTENDED_BEAT_S=1; '
    out = run(tmp_path, "ask_wait 7 0; echo rc=$?", stubs=stubs)
    assert out.stdout.splitlines() == ["rc=1"], (out.stdout, out.stderr)


# --- החיווט: טעינה, ושמירה בכל hello -----------------------------------------------------------------------------------


def test_the_agent_loads_ask_sh_and_keeps_the_list_on_every_answer_it_reads():
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/ask.sh"' in main
    body = main.split("read_answer() {", 1)[1].split("\n}", 1)[0]
    assert 'ask_keep "$RESP"' in body, "the main loop's hello answer must reach ask_keep"
    attended = (AGENT / "lib" / "core" / "attended.sh").read_text(encoding="utf-8")
    assert 'ask_keep "$1"' in attended.split("attended_keep() {", 1)[1].split("\n}", 1)[0], \
        "the beat during a wait must reach ask_keep too"
