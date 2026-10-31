"""#1913 (S7b, 20/10): a local clone's report that the server did not take --
the server was off at the end of the copy -- was sent once and never again.
The clone stood, the screen said "not reported", and the journal had a hole.

What the fix promises, each one tested here through the real lc_report and
lc_report_resend (agent/lib/localclone/localclone.sh, lcwait.sh), with curl stubbed:

1. No answer (000) or a server failure (5xx): the report is kept.
2. After a hello the server answered, it goes again, marked late (late_s),
   and the result screen's state turns to "sent".
3. A 4xx is an answer: not kept, not resent.
4. A server that keeps failing it: given up after LC_REPORT_TRIES, aloud.
5. The server's journal line says the report is late.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH
from test_local_clone_agent import run

from server.agent import local_clone_report

pytestmark = requires_native(("bash", BASH))

#: a finished run: two targets done, the clock of the run in started/ended
RUN = ('mkdir -p "$LC/run"; printf "sda\\nsdb\\n" > "$LC/run/targets"; _lr_src=sdc; _lr_r="$LC/run"; '
       'echo 1000 > "$LC/run/started"; echo 1500 > "$LC/run/ended"; '
       'target_init sda 100; target_init sdb 100; echo done > "$RUN_DIR/targets/sda/state"; '
       'echo done > "$RUN_DIR/targets/sdb/state"; disk_port() { echo 1; }; disk_serial() { echo S1; }; ')
#: curl answers what $RUN_DIR/code says, and keeps every body it was given
CURL = ('curl() { for a; do case "$a" in @*) n=$(ls "$RUN_DIR"/sent.* 2>/dev/null | wc -l); '
        'cp "${a#@}" "$RUN_DIR/sent.$n";; esac; done; cat "$RUN_DIR/code"; }; ')


def _run(tmp_path: Path, script: str, **env):
    out = run(tmp_path, RUN + CURL + script, **env)
    assert out.returncode == 0, out.stdout + out.stderr
    return out


def _sent(tmp_path: Path) -> list[dict]:
    files = sorted((tmp_path / "run").glob("sent.*"), key=lambda p: int(p.suffix[1:]))
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


def _pending(tmp_path: Path) -> list[str]:
    d = tmp_path / "run" / "lc" / "pending"
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def test_a_report_the_server_missed_is_sent_again_when_it_answers_marked_late(tmp_path):
    out = _run(tmp_path, 'echo 000 > "$RUN_DIR/code"; lc_report; echo "first=$(cat "$LC/run/report")"; '
                         'echo 200 > "$RUN_DIR/code"; lc_report_resend; echo "after=$(cat "$LC/run/report")"')
    assert "first=failed|000" in out.stdout, out.stdout
    assert "after=sent" in out.stdout, out.stdout                    # the result screen: reported
    first, again = _sent(tmp_path)
    assert "late_s" not in first
    assert again["late_s"] > 0 and again["targets"] == first["targets"] and again["mac"] == first["mac"]
    assert _pending(tmp_path) == []                                  # sent once, gone
    assert "reached the server journal (late)" in out.stdout


def test_a_server_failure_is_kept_too_and_a_later_run_does_not_take_its_sent(tmp_path):
    out = _run(tmp_path, 'echo 503 > "$RUN_DIR/code"; lc_report; echo 2000 > "$LC/run/started"; '
                         'echo 200 > "$RUN_DIR/code"; lc_report_resend; echo "after=$(cat "$LC/run/report")"')
    assert len(_sent(tmp_path)) == 2 and _pending(tmp_path) == []
    assert "after=failed|503" in out.stdout, out.stdout              # another run is on the screen now


def test_a_refusal_is_an_answer_and_is_not_kept(tmp_path):
    _run(tmp_path, 'echo 400 > "$RUN_DIR/code"; lc_report; lc_report_resend')
    assert _pending(tmp_path) == [] and len(_sent(tmp_path)) == 1


def test_nothing_is_sent_again_while_the_server_still_does_not_take_it_and_then_given_up(tmp_path):
    out = _run(tmp_path, 'echo 000 > "$RUN_DIR/code"; lc_report; lc_report_resend; echo "kept=$(ls "$LC/pending")"; '
                         'lc_report_resend; lc_report_resend; echo "end=$(ls "$LC/pending")"', LC_REPORT_TRIES="3")
    assert "kept=1000_1500.json" in out.stdout, out.stdout
    assert "end=" in out.stdout and _pending(tmp_path) == []
    assert "refused 3 times (http 000) -- given up" in out.stdout
    assert len(_sent(tmp_path)) == 4                                 # the first + exactly 3 tries


def test_the_main_loop_and_the_beat_both_resend_after_a_hello_the_server_answered():
    agent = (Path(__file__).resolve().parent.parent / "agent")
    main = (agent / "imagesrv-agent").read_text(encoding="utf-8")
    send_hello = main[main.index("send_hello() {"):main.index("read_answer() {")]
    assert send_hello.index("hello_post") < send_hello.index("lc_report_resend")
    beat = (agent / "lib" / "core" / "attended.sh").read_text(encoding="utf-8")
    hello = beat[beat.index("attended_hello() {"):beat.index("attended_keep() {")]
    assert hello.index("http_post_json") < hello.index("lc_report_resend")


def test_the_journal_says_the_report_is_late():
    body = {"source": {"dev": "sdc", "port": 3}, "targets": [{"dev": "sda", "port": 1, "state": "done"}],
            "bytes": 44_000_000_000, "elapsed_s": 900}
    assert "מאוחר" not in local_clone_report.journal_line(body)
    line = local_clone_report.journal_line({**body, "late_s": 349})
    assert "דוח מאוחר" in line and "5:49" in line
    assert "מאוחר" not in local_clone_report.journal_line({**body, "late_s": "x"})
