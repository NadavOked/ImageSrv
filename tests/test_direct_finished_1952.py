"""‏#1952 — צד מחשב הבנייה: מי קיבל את המחיצה הקודמת עד סופה.

בהפצה ישירה udp-sender רץ במחשב הבנייה, ולשרת אין את הלוג שלו (במסלול
הספרייה #1950 קורא אותו בשרת). לכן ה-hello שלפני כל מחיצה, מהשנייה, נושא
‏`direct_finished`: הכתובות שהתחברו ולא נזרקו ולא התנתקו לפני `Transfer
complete` (‏`agent/lib/restore/directrecv.sh`). השרת סופר לפיהן חבר `lost` שעדיין
מקבל (‏`tests/test_direct_lost_receiver_1952.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401
from test_directsend import _answer, direct_run

pytestmark = requires_native(("bash", BASH))

LIVE, LOST = "10.44.0.59", "10.44.0.161"


def _log(*lines: str) -> str:
    """לוג של מחיצה בצורה שנמדדה מול udpcast 20120424 (#1950, testrunner 22/10)."""
    return "\n".join(["Udp-sender 20120424", "Using mcast address 234.44.0.1", *lines]) + "\n"


BOTH_FINISHED = _log(f"New connection from {LIVE}  (#0) 00000009",
                     f"New connection from {LOST}  (#1) 00000009",
                     "Starting transfer: 00000009", "Transfer complete.",
                     f"Disconnecting #0 ({LIVE})", f"Disconnecting #1 ({LOST})")
LOST_DROPPED = _log(f"New connection from {LIVE}  (#0) 00000009",
                    f"New connection from {LOST}  (#1) 00000009",
                    "Starting transfer: 00000009", "Dropping client #1 because of timeout",
                    "Transfer complete.", f"Disconnecting #0 ({LIVE})")
#: ‏udpcast ממחזר את `#N` אחרי ניתוק — השורות נקראות לפי הסדר.
SLOT_REUSED = _log(f"New connection from {LOST}  (#0) 00000009", f"Disconnecting #0 ({LOST})",
                   f"New connection from {LIVE}  (#0) 00000009",
                   "Starting transfer: 00000009", "Transfer complete.", f"Disconnecting #0 ({LIVE})")


def _finished(tmp_path: Path, text: str | None) -> tuple[str, str]:
    """(rc, הפלט) של `direct_finished_ips` על לוג (None = אין קובץ)."""
    log = tmp_path / "dsend.1.log"
    if text is not None:
        log.write_text(text, encoding="utf-8", newline="\n")
    out = sh(f". {posix(AGENT)}/lib/restore/directrecv.sh; "
             f'out=$(direct_finished_ips {posix(log)!r}); echo "rc=$?"; printf "%s" "$out"')
    rc, _, body = out.partition("\n")
    return rc, body


def test_both_receivers_took_the_partition_to_the_end(tmp_path):
    rc, body = _finished(tmp_path, BOTH_FINISHED)
    assert rc == "rc=0" and sorted(json.loads(body)) == sorted([LIVE, LOST]), body


def test_a_dropped_receiver_did_not(tmp_path):
    rc, body = _finished(tmp_path, LOST_DROPPED)
    assert rc == "rc=0" and json.loads(body) == [LIVE], body


def test_a_slot_number_reused_after_a_disconnect_is_the_new_address(tmp_path):
    rc, body = _finished(tmp_path, SLOT_REUSED)
    assert rc == "rc=0" and json.loads(body) == [LIVE], body


def test_a_missing_log_is_not_an_empty_list(tmp_path):
    rc, body = _finished(tmp_path, None)
    assert rc != "rc=0" and body == "", body


def _direct_hello(tmp_path: Path, prev: str, log: str | None, hello_rc: int = 0) -> str:
    run = tmp_path / "run"
    run.mkdir()
    if log is not None:
        (run / f"dsend.{prev}.log").write_text(log, encoding="utf-8", newline="\n")
    return sh(
        f"export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/restore/directrecv.sh; "
        'send_hello() { cat "$RUN_DIR/direct.finished" 2>/dev/null || printf NONE; '
        f'return {hello_rc}; }}; '
        f'direct_hello {prev!r}; echo " rc=$?"; [ -e "$RUN_DIR/direct.finished" ] && echo LEFT || :')


def test_the_hello_carries_who_finished_and_then_the_fragment_is_gone(tmp_path):
    assert _direct_hello(tmp_path, "1", LOST_DROPPED) == f',"direct_finished":["{LIVE}"] rc=0\n'


def test_an_unreadable_log_is_reported_as_not_checked(tmp_path):
    """"לא נבדק" — ‏null, שהשרת סופר כ"עדיין מקבל" (עיקרון 5) — ולא רשימה ריקה."""
    assert _direct_hello(tmp_path, "1", None) == ',"direct_finished":null rc=0\n'


def test_before_the_first_partition_the_field_is_absent(tmp_path):
    assert _direct_hello(tmp_path, "", None) == "NONE rc=0\n"


def test_a_failed_hello_is_a_failed_hello(tmp_path):
    assert _direct_hello(tmp_path, "1", LOST_DROPPED, hello_rc=1).endswith(" rc=1\n")


def test_build_hello_carries_the_fragment_and_only_while_it_exists(fake_machine):  # noqa: F811
    env = (f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
           f'DEVROOT={posix(fake_machine["dev"])!r} '
           f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
           f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; build_hello')
    assert "direct_finished" not in json.loads(sh(env))
    (fake_machine["run"] / "direct.finished").write_text(
        f',"direct_finished":["{LIVE}"]', encoding="utf-8", newline="\n")
    assert json.loads(sh(env))["direct_finished"] == [LIVE]


#: ‏udp-sender מזויף שכותב את שורות המקלטים כמו האמיתי: ‏LOST נזרק.
UDP_SENDER = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$@" >> "$UDP_ARGV"\n'
    'cat > /dev/null\n'
    f'echo "New connection from {LIVE}  (#0) 00000009"\n'
    f'echo "New connection from {LOST}  (#1) 00000009"\n'
    'echo "Starting transfer: 0000000"\n'
    'echo "Dropping client #1 because of timeout"\n'
    'echo "Transfer complete."\n'
    f'echo "Disconnecting #0 ({LIVE})"\n'
    'exit 0\n'
)


def test_the_hello_before_partition_two_says_who_took_partition_one(tmp_path):
    """הצינור כולו: ה-hello שלפני מחיצה 1 בלי השדה, זה שלפני מחיצה 2 עם מי
    שקיבל את מחיצה 1 עד סופה — מהלוג של udp-sender של מחיצה 1."""
    box = tmp_path / "box"
    pre = ('send_hello() { { cat "$RUN_DIR/direct.finished" 2>/dev/null || printf NONE; echo; } '
           f'>> "$RUN_DIR/hellos"; cp {posix(box / "answer.json")!r} "$RESP"; }}; ')
    box, run, out = direct_run(tmp_path, answer=_answer(), partitions=2, timeout=120,
                               udp_sender=UDP_SENDER, pre=pre)
    assert out.strip().endswith("rc=0"), out + (run / "agent.log").read_text("utf-8", "replace")
    # ‏direct_wait_running, מחיצה 1, מחיצה 2
    assert (run / "hellos").read_text("utf-8").splitlines() == [
        "NONE", "NONE", f',"direct_finished":["{LIVE}"]']
    assert not (run / "direct.finished").exists()
