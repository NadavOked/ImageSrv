"""#2023 (נדב 15/11), PR 2 -- הצד של הסוכן: `hold` ו-`fail` בתשובת השרת ל-`fetching` (`agent/lib/restore/chunkhold.sh`).

כש-`brain_drawer_hold` דלוק השרת עונה לדיווח `fetching` של מי שמוריד `{"hold": [serial]}`: המגירה יוצאת מהמחיצה ומשלימה ברקע,
במנגנון של מגירה שנשלפה וחזרה (`drawerback.sh`: `p<n>.kept`, `catchup`, `_db_start`), והמשכפל ממשיך בלעדיה. `fail` -- המגירה
נכשלת בשמה. כאן ה-shell האמיתי של `chunk_partition_drawers`, `_chunk_fetch` ו-`drawerback.sh` מול ה-harness של
test_chunkrecv_2533.py (`chunkcast`, השרת והדיסקים מזויפים): נבדק מה שהמשכפל עושה, מה שהשרת שומע, ומה שנרשם על הדיסקים.

המגירות: sda חסר לה (שתי חתיכות מתוך חמש), sdb שלמה. השרת עונה `receive`, אחר כך `fetch` (הטווחים החסרים), ואחרי ההורדה `done`.

**בקרה שלילית:** בלי `chunkhold.sh` ו-`chunkfetch.sh` של השינוי, הטסטים המסומנים (neg) נופלים על התנהגות: ההורדה לא נעצרת, אין השלמה
ברקע, והמשכפל מחכה לסיומה. הטסטים הלא מסומנים הם שומרים: תשובה בלי `hold`/`fail` אינה משנה דבר, ואחרי הבקרה הם עוברים.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from native import requires_native
from test_agent import BASH, posix
from test_chunkrecv_2533 import B3_HASH, B3_LIST, DONE, FETCH, HARNESS, INDEX, RECEIVE1, REPO

pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

# the harness of test_chunkrecv_2533, with three additions: the new library (not there = the tree before the change, the negative
# control), the server's answer to a `fetching` report (a file the test writes; none = the plain `wait`), and a drawer that is whole
_SOURCE = "[ ! -f agent/lib/restore/chunkgate.sh ] || . agent/lib/restore/chunkgate.sh\n"
_FETCHING = """cp "$2" "$RUN_DIR/fetching.$_f.json"; echo TOLD >> "$RUN_DIR/order"; echo '{"ok": true, "action": "wait"}'; return 0"""
_RECV_STATE = """_fake_state "${RECV_COUNT:-5}" "${RECV_RANGES:-0-4}" "$@"\n"""
_PLAN = """[ -s "$RUN_DIR/restore.plan" ] || echo "3|g|windows|ntfs|2048|20971520|p3.windows.pcl.zst|sha|0|ug|uuid|" > "$RUN_DIR/restore.plan"
"""
assert HARNESS.count(_SOURCE) == 1 and HARNESS.count(_FETCHING) == 1 and HARNESS.count(_RECV_STATE) == 1 and HARNESS.count(_PLAN) == 1
HARNESS_HOLD = (HARNESS
                .replace(_SOURCE, _SOURCE + "[ ! -f agent/lib/restore/chunkhold.sh ] || . agent/lib/restore/chunkhold.sh\n")
                .replace(_FETCHING, """cp "$2" "$RUN_DIR/fetching.$_f.json"; echo TOLD >> "$RUN_DIR/order"
        cat "$RUN_DIR/fetching.answer.json" 2>/dev/null || echo '{"ok": true, "action": "wait"}'; return 0""")
                # NO_PLAN_ROW: the round's plan has no row for this partition (the agent cannot place a completion)
                .replace(_PLAN, _PLAN + """[ -z "${NO_PLAN_ROW:-}" ] || echo "9|g|data|ntfs|2048|20971520|p9.pcl.zst|sha|0|ug|uuid|" > "$RUN_DIR/restore.plan"
""")
                .replace(_RECV_STATE, _RECV_STATE + """            # SDB_WHOLE: the second drawer holds every chunk when the pass ends -- only sda lacks (the harness gives every node the same)
            [ -z "${SDB_WHOLE:-}" ] || sed -i "s|^/dev/sdb3 ok .*|/dev/sdb3 ok 5 0-4|" "$CHUNK_STATE"\n"""))
# APPLY_TAKEN=3: `apply` takes a chunk every 0.7 s, so the first `fetching` report comes about a second in, long before its first flush (2.9 s)
ENV = "SDB_WHOLE=1 RECV_COUNT=2 RECV_RANGES=0-0,2-2 REAL_SLEEP=1 TEST_START_CEILING=30 APPLY_TAKEN=3"
HOLD_SDA = {"ok": True, "action": "wait", "hold": ["SER-sda"]}


def _run_hold(tmp_path, fetching_answer: dict | None, env: str = ENV, answers: list[dict] | None = None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "idx").write_text(INDEX, encoding="ascii", newline="\n")
    (tmp_path / "b3").write_bytes(B3_LIST)   # #3332: the partition's BLAKE3 list is required before a chunk is written
    (tmp_path / "manifest.json").write_text(json.dumps({"partitions": [{"index": 3, "index_xxh64": "abc", "chunk_b3": B3_HASH}]}))
    answers = answers or [RECEIVE1, FETCH, DONE]
    for n, answer in enumerate(answers, start=1):
        (tmp_path / f"answer.{n}.json").write_text(json.dumps(answer))
    (tmp_path / "answer.last.json").write_text(json.dumps(answers[-1]))
    if fetching_answer is not None:
        (tmp_path / "fetching.answer.json").write_text(json.dumps(fetching_answer))
    (tmp_path / "harness.sh").write_text(HARNESS_HOLD, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, "-c", f'B3_HASH={B3_HASH} {env} . {posix(tmp_path / "harness.sh")!r} {posix(tmp_path)!r}'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=120)
    trace = (tmp_path / "trace").read_text(encoding="utf-8").splitlines() if (tmp_path / "trace").exists() else []
    bodies = [json.loads(p.read_text()) for p in sorted(tmp_path.glob("body.*.json"), key=lambda p: int(p.name.split(".")[1]))]
    return {"trace": trace, "bodies": bodies, "out": proc.stdout + proc.stderr,
            "log": (tmp_path / "agent.log").read_text(encoding="utf-8") if (tmp_path / "agent.log").exists() else ""}


def _applies(trace: list[str]) -> list[str]:
    return [t for t in trace if t.startswith("chunkcast apply")]


def test_without_a_hold_the_fetch_runs_as_it_did_and_both_drawers_finish_in_it(tmp_path):
    """The guard (the setting off, or a brain that did not decide): the plain `wait` -- one `apply` for both drawers, nothing kept,
    nothing in the background, and the machine ends whole."""
    got = _run_hold(tmp_path, None)
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace and "DONE sdb" in trace, got
    assert len(_applies(trace)) == 1 and "/dev/sda3" in _applies(trace)[0] and "/dev/sdb3" in _applies(trace)[0], trace
    assert not (tmp_path / "chunk" / "p3.kept").exists() and not (tmp_path / "targets" / "sda" / "catchup").exists()
    assert [b["state"] for b in got["bodies"]] == ["listening", "ended", "ended"]
    assert [t["serial"] for t in got["bodies"][2]["targets"]] == ["SER-sda", "SER-sdb"]


def test_a_hold_takes_the_drawer_out_of_the_fetch_and_completes_it_in_the_background_while_the_machine_goes_on(tmp_path):
    """(neg) The server answers the first `fetching` report with `hold: [SER-sda]`. The fetch (one `apply` for both drawers) is stopped
    -- before it finished anything -- and what chunkcast made durable on sda is kept (`p3.kept`); sda completes its two missing
    ranges in the BACKGROUND (a second `apply`, on /dev/sda3 alone, the agent's own ranges) and is signed done at the end; the
    machine's own question (`ended`) does not carry sda, and sdb -- whole -- is signed done by the round."""
    got = _run_hold(tmp_path, HOLD_SDA)
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace and "DONE sdb" in trace and not any(t.startswith("FAIL") for t in trace), got
    first, background = _applies(trace)[0], _applies(trace)[1:]
    assert "/dev/sda3" in first and "/dev/sdb3" in first and "--chunks-file" in first, first            # the fetch, for both drawers
    assert len(background) == 1 and "/dev/sda3" in background[0] and "/dev/sdb3" not in background[0], background
    assert trace.count("APPLY-END") == 1, "the fetch was not stopped: both applies ran to their end"       # only the background's did
    assert "/dev/sda3 ok 2 0-0,2-2" in (tmp_path / "chunk" / "p3.kept").read_text(), "what was durable on sda was not kept"
    assert "the server holds sda (SER-sda) -- it completes in the background" in log, log
    assert "השלמה: מחיצה 3 שלמה על sda" in log, log
    assert [b["state"] for b in got["bodies"]] == ["listening", "ended", "ended"]
    after = got["bodies"][2]
    assert [t["serial"] for t in after["targets"]] == ["SER-sdb"], after                                   # sda is not the partition's any more
    assert len(list(tmp_path.glob("fetching.[0-9]*.json"))) == 1       # it was told once, and then it was not a fetch (`fetching.answer.json` is the stub's answer, not a report)


def test_a_held_drawer_is_not_failed_by_the_round_and_the_machine_does_not_leave_the_partition_without_it_whole(tmp_path):
    """(neg) The completion in the background ends WHOLE (the evidence is the drawer's line in its own state, not an exit code), and
    the round's end waits for it (`drawer_back all`): by the time the harness ends, sda is signed after sdb -- it never failed."""
    got = _run_hold(tmp_path, HOLD_SDA)
    trace = got["trace"]
    assert "DONE sda" in trace and trace.index("DONE sdb") < trace.index("DONE sda"), trace              # sdb first, sda when its completion ends
    assert not (tmp_path / "targets" / "sda" / "back_failed").exists()
    assert (tmp_path / "targets" / "sda" / "state").read_text().strip() == "writing"


def test_a_fail_stops_the_fetch_and_fails_the_drawer_by_name_while_the_other_one_finishes(tmp_path):
    """(neg) `fail: {SER-sda: why}` in the answer to a `fetching` report (the brain said DROP twice): the fetch stops, sda fails by
    the server's sentence -- it is written again in the next wave -- and there is no completion in the background. sdb is whole."""
    why = "ההשלמה איטית מכתיבה מחדש — הדיסק ייכתב בגל הבא"
    got = _run_hold(tmp_path, {"ok": True, "action": "wait", "fail": {"SER-sda": why}})
    trace = got["trace"]
    assert f"FAIL sda מחיצה 3: {why}" in trace, trace
    assert "DONE sdb" in trace and "DONE sda" not in trace and trace[-1] == "rc=0", got
    assert len(_applies(trace)) == 1 and trace.count("APPLY-END") == 0, "the fetch ran on after the drawer failed"
    assert not (tmp_path / "targets" / "sda" / "catchup").exists() and not (tmp_path / "chunk" / "p3.kept").exists()


def test_a_serial_that_is_not_a_live_drawer_here_is_not_held_and_the_fetch_goes_on(tmp_path):
    """The guard: a hold for a serial this machine does not have is nothing -- the fetch is not stopped and nothing is kept."""
    got = _run_hold(tmp_path, {"ok": True, "action": "wait", "hold": ["SER-zzz"], "fail": {"SER-yyy": "x"}})
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace and "DONE sdb" in trace and not any(t.startswith("FAIL") for t in trace), got
    assert len(_applies(trace)) == 1 and trace.count("APPLY-END") == 1
    assert not (tmp_path / "chunk" / "p3.kept").exists()


@pytest.mark.parametrize("bad", [{"hold": "SER-sda"}, {"hold": {"SER-sda": 1}}, {"fail": ["SER-sda"]}, {"fail": "x"}, {"hold": []}, {}])
def test_an_answer_that_is_not_the_shape_of_the_contract_does_nothing(tmp_path, bad):
    """The guard (principle 5): `hold` that is not a list, `fail` that is not an object, or nothing -- the fetch runs as it did."""
    got = _run_hold(tmp_path, {"ok": True, "action": "wait", **bad})
    assert got["trace"][-1] == "rc=0" and len(_applies(got["trace"])) == 1 and got["trace"].count("APPLY-END") == 1, got
    assert "DONE sda" in got["trace"] and not (tmp_path / "chunk" / "p3.kept").exists()


def test_a_hold_that_cannot_be_completed_in_the_background_fails_the_drawer_by_name_and_does_not_leave_it_half_held(tmp_path):
    """(neg) No place for the partition in the plan: the drawer would be neither fetching nor completing -- it fails by name (principle 5),
    and the fetch is stopped. sdb finishes."""
    got = _run_hold(tmp_path, HOLD_SDA, env=ENV + " NO_PLAN_ROW=1")
    trace, log = got["trace"], got["log"]
    assert any(t.startswith("FAIL sda") and "ההשלמה ברקע אינה אפשרית" in t for t in trace) and "DONE sdb" in trace and "DONE sda" not in trace, got
    assert "cannot be completed in the background here -- it fails by name" in log, log
    assert not (tmp_path / "targets" / "sda" / "catchup").exists()
