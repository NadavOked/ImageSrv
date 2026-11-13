"""#1581 בהפצה ישירה — המקור אינו סופר את ההמתנה לאדם כ"אף מחשב לא הצטרף".

אחרי #2044 (PR #2356) שאלת ה-SMART של המשכפל אינה קיימת, אבל שאלות המגירה
(RAID #1808, חלוקת המקום #1898) נשארו, ומחשב השיכפול ממתין עליהן לאדם
לפני הבייט הראשון (drawergate.sh → attended → `prompt` ב-hello). השולח
**של השרת** מחזיק את המחיצה הראשונה בזמן הזה (#2202). בהפצה ישירה (#715)
השולח הוא udp-sender **במחשב הבנייה**, עם `--start-timeout 180` משלו, והוא
לא שאל על השאלה כלל: שלוש דקות על שאלה = "אף מחשב משכפל לא הצטרף לזרם".

השרת אומר עכשיו כמה חברים בגל ממתינים (`task.direct.operator_wait`,
test_direct_operator_wait_1581.py), והמקור:
* אינו מפעיל את udp-sender של המחיצה הראשונה כל עוד המספר אינו 0;
* עוצר אותו אם השאלה עולה **אחרי** שהופעל ולפני הבייט הראשון, ומפעיל אותו
  שוב אחרי התשובה עם `--start-timeout` מלא;
* ומכונה שבאמת לא הגיעה — בלי שאלה — עדיין נכשלת בשמה (עיקרון 5).

**בקרה שלילית:** עם `agent/lib/capture/directsend.sh` ו-`agent/lib/restore/directrecv.sh`
של `origin/main` (שניהם יחד), שני הטסטים הראשונים נופלים; השלישי והרביעי
עוברים בשני המצבים, וזו הנקודה שלהם.
"""

from __future__ import annotations

import json
import subprocess

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_directsend import _answer, _progress, direct_run

pytestmark = requires_native(("bash", BASH))


def _asking_answer(path, waiting: int = 1) -> None:
    answer = _answer()
    answer["task"]["direct"]["operator_wait"] = waiting
    path.write_text(json.dumps(answer), encoding="utf-8")


def _starts(box) -> list[list[str]]:
    argv = (box / "udp.argv").read_text("utf-8").splitlines()
    marks = [i for i, w in enumerate(argv) if w == "--interface"] + [len(argv)]
    return [argv[a:b] for a, b in zip(marks, marks[1:])]


def _flag(run: list[str], name: str) -> str:
    return run[run.index(name) + 1]


#: udp-sender שבהפעלה הראשונה **אינו** מקבל איש: השאלה של המשכפל עולה רגע
#: אחרי שהופעל ($ASKING), והוא יוצא 0 בלי לשדר אחרי "start-timeout" קצר —
#: כמו האמיתי (#438). מההפעלה השנייה — המשכפל ענה והצטרף: הוא משדר.
LATE_QUESTION_SENDER = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$@" >> "$UDP_ARGV"\n'
    'n=$(cat "$UDP_COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$UDP_COUNT"\n'
    'if [ "$n" -eq 1 ]; then\n'
    '  : > "$ASKING"\n'
    '  i=0; while [ "$i" -lt "$UDP_WAIT" ]; do sleep 1; i=$((i + 1)); done\n'
    '  : > "$UDP_COUNT.timed-out"\n'
    '  exit 0\n'
    'fi\n'
    'cat > /dev/null\n'
    'echo "Starting transfer: 0000000"\n'
    'exit 0\n'
)


def test_a_question_after_the_source_started_stops_partition_one_and_sends_it_again(tmp_path):
    """ברזל 25/10 (ses_ca1fb8e9), בצורת ההפצה הישירה: השאלה עלתה שתי שניות
    אחרי שהשולח הופעל. כאן ה-start-timeout של הזיוף הוא 12 שניות במקום 180,
    והמשכפל עונה אחרי שלוש תשובות hello עם השאלה."""
    asking, answered = tmp_path / "asking.flag", tmp_path / "asked.count"
    _asking_answer(tmp_path / "asking.json")
    hello = (
        f'answer_to() {{ if [ -e {posix(asking)!r} ]; then '
        f'n=$(cat {posix(answered)!r} 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > {posix(answered)!r}; '
        f'[ "$n" -lt 3 ] || rm -f {posix(asking)!r}; cp {posix(tmp_path / "asking.json")!r} "$1"; '
        f'else cp "$BOX/answer.json" "$1"; fi; }}; '
        'send_hello() { answer_to "$RESP"; }; '
        'direct_watch_hello() { answer_to "$RUN_DIR/dwatch.json"; }; '
    )
    box, run, out = direct_run(
        tmp_path, udp_sender=LATE_QUESTION_SENDER, timeout=120, pre=hello,
        env={"ASKING": posix(asking), "UDP_COUNT": posix(tmp_path / "udp.count"),
             "UDP_WAIT": "12", "DIRECT_POLL_S": "1", "DIRECT_ASK_POLL_S": "1",
             "BOX": posix(tmp_path / "box")})
    log = (run / "agent.log").read_text("utf-8", "replace") if (run / "agent.log").exists() else ""
    assert out.strip().endswith("rc=0"), out + log
    starts = _starts(box)
    assert len(starts) == 2, starts
    # הראשון נעצר לפני ה-12 שלו; השני קיבל שעון מלא של מחיצה **ראשונה**, לא של "הבאות"
    assert [_flag(s, "--start-timeout") for s in starts] == ["180", "180"]
    assert "stopping partition 1" in log
    assert not (tmp_path / "udp.count.timed-out").exists(), "udp-sender הראשון לא נעצר — רץ עד ה-timeout שלו"
    assert "was not found to stop" not in log
    report = _progress(box)
    assert report["targets"][0]["state"] != "failed", report


#: udp-sender שמשדר מיד ורושם כמה hello היו לפניו.
COUNTING_SENDER = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$@" >> "$UDP_ARGV"\n'
    'cat "$HELLOS" >> "$UDP_SEEN" 2>/dev/null || echo none >> "$UDP_SEEN"\n'
    'cat > /dev/null\n'
    'echo "Starting transfer: 0000000"\n'
    'exit 0\n'
)


def test_the_first_partition_waits_while_a_cloner_is_on_a_question(tmp_path):
    """הגל `running`, ושתי תשובות ה-hello הראשונות אומרות שמשכפל ממתין לאדם —
    udp-sender אינו מופעל לפני התשובה השלישית."""
    hellos, seen = tmp_path / "hellos", tmp_path / "seen"
    _asking_answer(tmp_path / "asking.json")
    hello = (
        f'send_hello() {{ n=$(cat {posix(hellos)!r} 2>/dev/null || echo 0); n=$((n + 1)); '
        f'echo "$n" > {posix(hellos)!r}; '
        f'if [ "$n" -le 2 ]; then cp {posix(tmp_path / "asking.json")!r} "$RESP"; '
        f'else cp "$BOX/answer.json" "$RESP"; fi; }}; '
        'direct_watch_hello() { cp "$BOX/answer.json" "$RUN_DIR/dwatch.json"; }; '
    )
    box, run, out = direct_run(
        tmp_path, udp_sender=COUNTING_SENDER, timeout=120, pre=hello,
        env={"HELLOS": posix(hellos), "UDP_SEEN": posix(seen),
             "DIRECT_POLL_S": "1", "BOX": posix(tmp_path / "box")})
    assert out.strip().endswith("rc=0"), out
    first = int(seen.read_text("utf-8").split()[0])
    assert first >= 3, f"udp-sender הופעל אחרי {first} hello — בזמן שהמשכפל עוד ממתין לאדם"
    assert len(_starts(box)) == 1


def test_a_machine_that_never_came_still_fails_by_name(tmp_path):
    """עיקרון 5: בלי שאלה פתוחה (`operator_wait: 0`) — מה שהיה: udp-sender שיצא
    0 בלי לשדר הוא "אף מחשב משכפל לא הצטרף", ולא ניסיון נוסף."""
    answer = _answer()
    answer["task"]["direct"]["operator_wait"] = 0
    box, run, out = direct_run(tmp_path, answer=answer, udp_silent=True)
    assert out.strip().endswith("rc=1"), out
    assert "אף מחשב משכפל לא הצטרף לזרם" in _progress(box)["targets"][0]["error"]
    assert len(_starts(box)) == 1


def test_stop_finds_only_our_udp_sender(tmp_path):
    """`direct_stop_sender` הורג את udp-sender של ה-portbase שלנו בלבד —
    לא תהליך אחר שמזכיר את המילה, ולא udp-sender על portbase אחר."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    sender = stubs / "udp-sender"
    script = (
        f"cat > {posix(sender)!r} <<'STUB'\n#!/bin/sh\nwhile :; do sleep 1; done\nSTUB\n"
        f"chmod 0755 {posix(sender)!r}\n"
        f"export RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path / 'agent.log')!r}\n"
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/directrecv.sh\n"
        f"{posix(sender)!r} --interface eth0 --portbase 31200 > /dev/null 2>&1 & ours=$!\n"
        f"{posix(sender)!r} --interface eth0 --portbase 31202 > /dev/null 2>&1 & other=$!\n"   # another round's pair, never a production port (#156)
        "sh -c 'while :; do sleep 1; done # udp-sender --portbase 31200' > /dev/null 2>&1 & mention=$!\n"
        "sleep 1; direct_stop_sender 31200; sleep 1\n"
        'kill -0 "$ours" 2>/dev/null && echo ours=alive || echo ours=gone\n'
        'kill -0 "$other" 2>/dev/null && echo other=alive || echo other=gone\n'
        'kill -0 "$mention" 2>/dev/null && echo mention=alive || echo mention=gone\n'
        'kill "$ours" "$other" "$mention" 2>/dev/null; exit 0\n'
    )
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL).stdout
    assert "ours=gone" in out, out
    assert "other=alive" in out and "mention=alive" in out, out
