"""‏#989 ס׳ 2 — הצד של הסוכן בתשובת SMART מהקונסולה (`attended.sh`, `clonergui.sh`).

* השאלה שעל המסך הגרפי (`smart-request`) נוסעת ב-hello כ-`ask`; בלי בקשה
  חיה (תפריט טקסט) אין `ask` — שום קורא לא היה מקבל את התשובה.
* תשובת הקונסולה (`ask_answer`) נכתבת ל-`smart-decision` כאילו נלחץ כפתור,
  **רק** אם ה-nonce הוא של השאלה הנוכחית וההחלטה חוקית; החלטה מקומית שכבר
  שם אינה נדרסת.
* החלטה מקומית עוברת דרך השרת (`attended_claim`): מה שהוא מחזיר — המנצחת —
  הוא מה שנעשה. שרת שלא ענה: ההחלטה המקומית נשארת, ונאמר על כך.

אין `jq` בעמדת הפיתוח בווינדוס: שם נכנס תחליף צר שמבין **רק** את המסנן של
`smart_ask_answer` (כל מסנן אחר — קוד 5, לא פלט ריק). על המעבדה רץ jq אמיתי.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, PORTS, posix, sh, smart_choice_env  # noqa: F401  (BASH: pytestmark)

pytestmark = requires_native(("bash", BASH))

REPO = Path(__file__).resolve().parent.parent
ATTENDED = posix(REPO / "agent" / "lib" / "core" / "attended.sh")

JQ_STUB = r'''
import json, sys
args = sys.argv[1:]
nonce = args[args.index("--arg") + 2]
doc = json.load(open(args[-1], encoding="utf-8"))
if "ask_answer" not in " ".join(args):
    sys.exit(5)
ans = doc.get("ask_answer")
if isinstance(ans, dict) and ans.get("nonce") == nonce and ans.get("answer") in ("replace", "rescue", "skip"):
    print(ans["answer"]); sys.exit(0)
sys.exit(4)
'''


@pytest.fixture()
def box(tmp_path):
    (tmp_path / "run").mkdir(); (tmp_path / "gui").mkdir(); (tmp_path / "bin").mkdir()
    if shutil.which("jq") is None:
        (tmp_path / "jqstub.py").write_text(JQ_STUB, encoding="utf-8", newline="\n")
        (tmp_path / "bin" / "jq").write_text(f'#!/bin/sh\nexec python "{posix(tmp_path / "jqstub.py")}" "$@"\n',
                                             newline="\n")
    return tmp_path


def prelude(box: Path, reply: str | None, rc: int = 0) -> str:
    """http_post_json מזויף: שומר כל גוף שנשלח, ומחזיר את התשובה המוכנה (או נכשל)."""
    (box / "reply.json").write_text(reply or "{}", encoding="utf-8", newline="\n")
    return f'''export PATH="$(cd "{posix(box / "bin")}" && pwd):$PATH" RUN_DIR={posix(box / "run")} GUI_DIR={posix(box / "gui")} SERVER=http://s
. {ATTENDED}
build_hello() {{ printf '{{"mac":"aa:bb:cc:dd:ee:ff","joining":false}}'; }}
json_escape() {{ printf '%s' "$1"; }}   # test data has no quote or backslash (Windows argv mangles them)
log() {{ echo "$*" >&2; }}
N=0
http_post_json() {{ N=$((N+1)); cp "$2" "$RUN_DIR/req$N.json"; [ {rc} = 0 ] || return 22; cat "{posix(box / "reply.json")}"; }}
'''


def request(box: Path, n: int = 1) -> dict:
    return json.loads((box / "run" / f"req{n}.json").read_text(encoding="utf-8"))


def ask_file(box: Path, line="3-2|2|warn|SMART warn (crc 4)") -> None:
    (box / "gui" / "smart-request").write_text(line + "\n", newline="\n")


def decision(box: Path) -> str | None:
    f = box / "gui" / "smart-decision"
    return f.read_text().strip() if f.exists() else None


# --- ask: רק כשהמסך הגרפי שואל --------------------------------------------------


def test_a_gui_question_is_offered_to_the_console_as_ask(box):
    ask_file(box)
    out = sh(prelude(box, None) + 'attended_hello "Disk 2: SMART warn"')
    assert out.returncode == 0, out.stderr
    body = request(box)
    assert body["ask"] == {"nonce": "3-2", "port": 2, "verdict": "warn", "reason": "SMART warn (crc 4)"}
    assert body["prompt"] == "Disk 2: SMART warn" and body["waiting_for"] == "operator"


def test_no_gui_request_means_no_ask(box):
    """תפריט טקסט: אין מי שיקרא את התשובה, ולכן אין הצעה לקונסולה."""
    out = sh(prelude(box, None) + 'attended_hello "Disk 2: SMART warn"')
    assert out.returncode == 0, out.stderr
    assert "ask" not in request(box)


@pytest.mark.parametrize("line", ["x-2|2|warn|r", "3-2|two|warn|r", "|2|warn|r"])
def test_a_garbled_request_is_not_offered(box, line):
    ask_file(box, line)
    sh(prelude(box, None) + 'attended_hello "q"')
    assert "ask" not in request(box)


# --- ask_answer → smart-decision ------------------------------------------------


def answered(nonce: str, answer: str) -> str:
    return json.dumps({"known": True, "ask_answer": {"nonce": nonce, "answer": answer}})


def test_the_console_answer_becomes_the_decision_the_gui_wait_reads(box):
    ask_file(box)
    out = sh(prelude(box, answered("3-2", "skip")) + 'attended_hello "q"')
    assert out.returncode == 0, out.stderr
    assert decision(box) == "3-2|skip"


@pytest.mark.parametrize("reply", [
    json.dumps({"ask_answer": {"nonce": "2-2", "answer": "skip"}}),      # שאלה קודמת
    json.dumps({"ask_answer": {"nonce": "3-2", "answer": "format"}}),    # לא חוקי
    json.dumps({}), "not json",
])
def test_a_stale_or_invalid_answer_decides_nothing(box, reply):
    ask_file(box)
    sh(prelude(box, reply) + 'attended_hello "q"')
    assert decision(box) is None


def test_a_local_click_already_there_is_not_overwritten(box):
    ask_file(box)
    (box / "gui" / "smart-decision").write_text("3-2|replace\n", newline="\n")
    sh(prelude(box, answered("3-2", "skip")) + 'attended_hello "q"')
    assert decision(box) == "3-2|replace"


def test_an_answer_without_a_live_request_is_ignored(box):
    sh(prelude(box, answered("3-2", "skip")) + 'attended_hello "q"')
    assert decision(box) is None


# --- attended_claim: השרת מכריע ------------------------------------------------


def test_the_claim_returns_the_winning_answer_not_the_local_one(box):
    out = sh(prelude(box, answered("3-2", "replace")) + 'attended_claim 3-2 skip')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "replace", "the console answered first: that is what is done"
    assert request(box)["ask_answered"] == {"nonce": "3-2", "answer": "skip"}


def test_the_claim_confirms_the_local_answer_when_it_won(box):
    out = sh(prelude(box, answered("3-2", "skip")) + 'attended_claim 3-2 skip')
    assert out.stdout.strip() == "skip" and out.stderr.strip() == ""


def test_the_claim_without_a_server_keeps_the_local_choice_and_says_so(box):
    out = sh(prelude(box, None, rc=1) + 'attended_claim 3-2 rescue')
    assert out.stdout.strip() == "rescue"
    assert "did not confirm" in out.stderr


def test_the_claim_with_an_answer_for_another_question_keeps_the_local_choice(box):
    out = sh(prelude(box, answered("9-9", "replace")) + 'attended_claim 3-2 skip')
    assert out.stdout.strip() == "skip" and "did not confirm" in out.stderr


# --- gui_smart_choice: ההחלטה המקומית עוברת דרך ה-claim ---------------------------


def gui_choice(box: Path, claim_def: str) -> str:
    g = box.parent / (box.name + "-g"); g.mkdir()
    setup, gui = smart_choice_env(g)
    script = f"""{setup}{claim_def}
sleep 20 & _gui_pid=$!
( gui_smart_choice sda fail pending > {posix(gui)}/out ) &
CH=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -f {posix(gui)}/smart-request ] && break; sleep 0.3; done
N=$(IFS='|' read -r n _ < {posix(gui)}/smart-request; echo "$n")
printf '%s|replace\\n' "$N" > {posix(gui)}/smart-decision
wait $CH
kill $_gui_pid 2>/dev/null
cat {posix(gui)}/out
"""
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_a_local_click_goes_through_the_server_and_the_winner_is_returned(box):
    # הקונסולה ענתה "דלג" באותו רגע שהמסך לחץ "החלף": מה שנעשה הוא מה שהשרת מכריע.
    assert gui_choice(box, 'attended_claim() { echo skip; }; ') == "skip"


def test_without_the_claim_function_the_local_click_stands(box):
    # הסוכן הישן / חבילת טסטים בלי attended.sh — ההתנהגות כמו לפני #989.
    assert gui_choice(box, '') == "replace"
