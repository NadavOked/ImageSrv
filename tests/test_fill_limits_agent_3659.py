"""#3659 (אפיק #3653, נקודה A27) -- הצד של הסוכן: הקבועים של ההשלמה הם רק "אין החלטה", והסוכן אומר ביומן כשהשתמש בהם.

המוח אומר (`server/brain/fill.py`, `fill.limits`; הצד שלו ב-`test_brain_fill_limits_3659.py`):

- `fill_word` (`fillword.sh`): `bad_max` -> `$RUN_DIR/neighborfill.bad`, `word_s` -> `$RUN_DIR/fillword.word_s` (למדידה הבאה);
- `neighborfill.sh`: התקרה (`neighborfill.cap` / `NF_CAP_MIBS`) מחליפה את `NF_CAP_MAX_MIBS`, לא נחתכת בו; `bad_max` מחליף `NF_BAD_MAX`;
- `peerfill.sh`: `speed_kibs` ו-`speed_s` בהוראה מחליפים את `PF_SPEED_BPS` ו-`PF_SPEED_S`.

מילה שאינה מספר שלם בטווח אינה מילה: הקבוע של הסוכן, ונאמר ביומן. בהוראה לשכן-משכפל שדה כזה דוחה את ההוראה כולה, כמו כל שדה שלה.

כאן רצים הסקריפטים האמיתיים (`fillword.sh`, `neighborfill.sh`, `peerfill.sh`), `chunkcast` האמיתי ו-curl האמיתי, כמו ב-
`test_neighborfill_3181.py` ו-`test_peerfill_b29.py`, שמהם נלקחות הרתמות.

**בקרה שלילית:** `fillword.sh`, `neighborfill.sh` ו-`peerfill.sh` חוזרים לבסיס: הקבועים מחליטים לבדם, `NF_CAP_MIBS=500` נחתך ל-100,
`bad_max` ו-`speed_*` נשכחים, ואין שורת "no word from the brain" ביומן. הטסטים כאן נופלים ב-AssertionError.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix
from test_neighborfill_3181 import cc, chunkcut, cut, damaged, pcl  # noqa: F401
from test_neighborfill_3181 import fill as nf_fill
from test_peerfill_b29 import ID, Neighbour, needy

pytestmark = requires_native("gcc", "zstd", "curl", "jq", ("bash", BASH), posix=True,
                             why="chunkcast needs gcc and libzstd; the frames go over curl; the plan is read with jq")

MIB = 1024 * 1024
NO_WORD = "no word from the brain for:"


# --- fillword.sh: the plan's words are kept, or there are none ---------------------------------------------------------


def fw(tmp: Path, body: str, *, reply: dict | None = None, files: dict | None = None, env: dict | None = None) -> dict:
    """`fill_word sda 3 30` of the real fillword.sh. The server's answer is a file (`_pl_heard` reads it: stubbed, it is prodline's)."""
    run = tmp / "run"
    for name in ("sda", "sdb"):
        (run / "targets" / name).mkdir(parents=True, exist_ok=True)
    if reply is not None:
        (run / "reply.json").write_text(json.dumps(reply))
    for rel, text in (files or {}).items():
        (run / rel).write_text(text)
    script = (f"export RUN_DIR={posix(run)!r} DEVROOT={posix(tmp / 'dev')!r}; " + agent_sources("common", "waits", "restore", "fillword")
              + '_pl_heard() { PL_REPLY=$(cat "$RUN_DIR/reply.json" 2>/dev/null) || return 1; [ -n "$PL_REPLY" ]; }; '
                'target_bound_serial() { echo A1; }; ' + body)
    began = time.monotonic()
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=60,
                          stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})})
    read = lambda name: (run / name).read_text().strip() if (run / name).exists() else None   # noqa: E731
    return {"out": proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stdout + proc.stderr,
            "log": read("agent.log") or "", "bad": read("neighborfill.bad"), "word": read("fillword.word_s"),
            "cap": read("neighborfill.cap"), "took": time.monotonic() - began}


CALL = 'fill_word sda 3 30; echo "rc=$? only=$FILL_ONLY"'
PLAN = {"verdict": "FEW", "source": "NEIGHBOR", "neighbors": ["sdb"], "per_neighbor_bps": 50 * MIB, "budget_bps": 50 * MIB,
        "bad_max": 2, "word_s": 4}


def test_the_words_of_a_plan_are_kept_for_the_fill_and_the_next_measure(tmp_path):
    got = fw(tmp_path, CALL, reply={"ok": True, "fill": {"A1": PLAN}})
    assert got["out"] == "rc=0 only=sdb", got
    assert (got["bad"], got["word"], got["cap"]) == ("2", "4", "50"), got
    assert "אין החלטה" not in got["log"], got["log"]


def test_a_plan_without_words_leaves_none_and_removes_the_old_ones(tmp_path):
    """מקור `PASS` (מעבר שידור) = אין החלטה: הקבצים מהתוכנית הקודמת נמחקים, ונאמר ביומן. הקבוע של הסוכן הוא שמחליט."""
    plan = {"verdict": "REPASS", "source": "PASS", "neighbors": [], "per_neighbor_bps": 0, "budget_bps": 50 * MIB}
    got = fw(tmp_path, CALL, reply={"ok": True, "fill": {"A1": plan}}, files={"neighborfill.bad": "2\n", "fillword.word_s": "9\n"})
    assert got["out"] == "rc=0 only=", got
    assert (got["bad"], got["word"]) == (None, None), got
    assert "אינו הוראת השלמה: אין החלטה, שכנות קודם ואז HTTP" in got["log"], got["log"]


@pytest.mark.parametrize("value", [0, -1, 2.5, "3", 101, 99999999, None, True], ids=repr)
def test_a_word_that_is_not_a_whole_number_in_range_is_not_a_word(tmp_path, value):
    got = fw(tmp_path, CALL, reply={"ok": True, "fill": {"A1": {**PLAN, "bad_max": value, "word_s": value}}},
             files={"neighborfill.bad": "2\n"})
    assert (got["bad"], got["word"]) == (None, None), got
    assert "bad_max בתוכנית אינו מספר שלם בטווח 1-100 -- הקבוע של הסוכן" in got["log"], got["log"]
    assert "word_s בתוכנית אינו מספר שלם בטווח 1-60 -- הקבוע של הסוכן" in got["log"], got["log"]
    assert got["out"] == "rc=0 only=sdb", "the plan's source still stands: only the words that were not words are the agent's own"


def test_a_shadow_answer_has_no_plan_for_the_drawer_and_the_log_says_it_is_no_decision(tmp_path):
    got = fw(tmp_path, CALL, reply={"ok": True}, files={"neighborfill.bad": "2\n", "fillword.word_s": "9\n"})
    assert got["out"] == "rc=0 only=", got
    assert got["bad"] is None, "the last plan's word does not outlive the plan"
    assert got["word"] == "9", "the wait is what the brain last said until it says otherwise"
    assert "בלי תוכנית למגירה הזו" in got["log"] and "אין החלטה: שכנות קודם, ואז HTTP" in got["log"], got["log"]


def test_http_is_the_brains_word_and_is_returned(tmp_path):
    plan = {**PLAN, "source": "HTTP", "neighbors": []}
    got = fw(tmp_path, CALL, reply={"ok": True, "fill": {"A1": plan}})
    assert got["out"] == "rc=1 only=", got
    assert "השלמה ב-HTTP, לא קוראים משכנות" in got["log"], got["log"]


def test_no_answer_within_the_default_wait_is_no_decision_and_says_which_wait_was_used(tmp_path):
    got = fw(tmp_path, CALL, env={"FILL_WORD_S": "1"})
    assert got["out"] == "rc=0 only=" and 1 <= got["took"] < 20, got
    assert "תוך 1 ש' (ברירת המחדל של הסוכן, המוח עוד לא אמר)" in got["log"], got["log"]
    assert "אין החלטה: שכנות קודם, ואז HTTP למה שנשאר" in got["log"], got["log"]


def test_the_wait_the_brain_said_replaces_the_default(tmp_path):
    """`FILL_WORD_S=0` היה חוזר מיד. 2 הם של המוח, והמדידה מחכה להם -- ואומרת שהמוח אמר."""
    got = fw(tmp_path, CALL, env={"FILL_WORD_S": "0"}, files={"fillword.word_s": "2\n"})
    assert got["took"] >= 2, got
    assert "תוך 2 ש' (המוח אמר)" in got["log"], got["log"]


# --- neighborfill.sh: the ceiling and bad_max ------------------------------------------------------------------------


def test_no_word_means_the_agents_defaults_and_the_log_says_so(tmp_path, cc, pcl):
    got = nf_fill(tmp_path, cc, pcl, {"sdb": {"ledger": ["0-4"]}}, env={"NF_BATCH": "1"})
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert f"{NO_WORD} bad_max=3 ceiling=100MiB/s -- the agent's own defaults (A27)" in got["log"], got["log"]
    assert "1 neighbours read, 100MiB/s together at most" in got["log"]


def test_the_brains_ceiling_replaces_the_default_and_is_not_clipped_by_it(tmp_path, cc, pcl):
    """בלי הקבוע של 100 כקיר: מילה של 300 היא 300. (ה-100 המדוד, M1/M2, הוא `fill.CEILING_BPS` של המוח; הסוכן מבצע.)"""
    got = nf_fill(tmp_path, cc, pcl, {"sdb": {"ledger": ["0-4"]}}, env={"NF_BATCH": "1"},
                  files={"neighborfill.cap": "300\n", "neighborfill.bad": "2\n"})
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert NO_WORD not in got["log"], got["log"]
    assert "1 neighbours read, 300MiB/s together at most" in got["log"], got["log"]


def test_a_ceiling_word_alone_leaves_only_bad_max_to_the_default(tmp_path, cc, pcl):
    got = nf_fill(tmp_path, cc, pcl, {"sdb": {"ledger": ["0-4"]}}, env={"NF_BATCH": "1", "NF_CAP_MIBS": "500"})
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert f"{NO_WORD} bad_max=3 -- the agent's own defaults (A27)" in got["log"], got["log"]
    assert "500MiB/s together at most" in got["log"], got["log"]


@pytest.mark.parametrize("word", ["abc", "0", "00", "-1"])
def test_a_bad_max_word_that_is_not_a_whole_number_is_no_word(tmp_path, cc, pcl, word):
    got = nf_fill(tmp_path, cc, pcl, {"sdb": {"ledger": ["0-4"]}}, env={"NF_BATCH": "1"}, files={"neighborfill.bad": word + "\n"})
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert f"{NO_WORD} bad_max=3 ceiling=100MiB/s" in got["log"], got["log"]


def test_bad_max_of_the_brain_stops_a_neighbour_that_fails_sooner_than_the_default(tmp_path, cc, cut):
    """חתיכה 2 אצל sdb שבורה. ברירת המחדל (3 חתיכות שבורות): 3-4 בכל זאת נקראות והנותר הוא 2 בלבד. `bad_max` = 1: sdb נופלת אחרי
    החתיכה הראשונה שלא הגיעה שלמה, ו-3-4 נשארות ל-HTTP. שני המצבים באותו עץ ובאותה רתמה: משתנה אחד."""
    img, disk = cut
    neighbour = {"sdb": {"ledger": ["0-4"], "bytes": damaged(img, disk, 2)}}
    default = nf_fill(tmp_path / "default", cc, cut, neighbour, env={"NF_BATCH": "1"})
    assert default["left"] == ["2-2"], default["out"] + default["log"]
    told = nf_fill(tmp_path / "told", cc, cut, neighbour, env={"NF_BATCH": "1"}, files={"neighborfill.bad": "1\n"})
    assert told["left"] == ["2-4"], told["out"] + told["log"]
    assert "sdb failed 1 pieces -- it is not asked again" in told["log"], told["log"]
    assert told["target"] == img.expected({0, 1}), "0-1 were written before it failed; not a byte of 2, and 3-4 were not asked"


# --- peerfill.sh: the floor is in the instruction -----------------------------------------------------------------------


def test_an_instruction_without_a_floor_uses_the_agents_default_and_says_so(tmp_path, cc, pcl):
    peer = Neighbour(pcl[0], tmp_path)
    try:
        got = needy(tmp_path, cc, pcl, peer.port)
    finally:
        peer.close()
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert f"{NO_WORD} speed_kibs=1024 speed_s=30 -- the agent's own defaults (A27)" in got["log"], got["log"]
    assert "below 1048576B/s for 30s = the neighbour failed" in got["log"], got["log"]


def test_the_floor_in_the_instruction_is_the_brains_word(tmp_path, cc, pcl):
    peer = Neighbour(pcl[0], tmp_path)
    try:
        got = needy(tmp_path, cc, pcl, peer.port, instruction={"speed_kibs": 2048, "speed_s": 5})
    finally:
        peer.close()
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert "below 2097152B/s for 5s = the neighbour failed" in got["log"], got["log"]
    assert NO_WORD not in got["log"], got["log"]


def test_one_field_of_the_floor_missing_leaves_only_it_to_the_default(tmp_path, cc, pcl):
    peer = Neighbour(pcl[0], tmp_path)
    try:
        got = needy(tmp_path, cc, pcl, peer.port, instruction={"speed_s": 7})
    finally:
        peer.close()
    assert got["rc"] == "rc=0", got["out"] + got["log"]
    assert f"{NO_WORD} speed_kibs=1024 -- the agent's own defaults (A27)" in got["log"], got["log"]
    assert "below 1048576B/s for 7s" in got["log"], got["log"]


def test_the_seconds_in_the_instruction_decide_when_a_stuck_neighbour_failed_not_the_agents_default(tmp_path, cc, pcl):
    """שכן שעונה 206 ושותק. הקבוע של הסוכן (`PF_SPEED_S`) מוגדר כאן ל-600 ש': אילו הוא היה מחליט, הבדיקה הייתה תקועה.
    ההוראה אומרת 2 -- והשכן נכשל תוך שניות, `bad`."""
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(4)
    held = []

    def hang():
        while True:
            try:
                c, _ = ls.accept()
            except OSError:
                return
            held.append(c)
            c.recv(4096)
            c.sendall(b"HTTP/1.1 206 Partial Content\r\nContent-Length: 99999999\r\n\r\n")

    threading.Thread(target=hang, daemon=True).start()
    try:
        got = needy(tmp_path, cc, pcl, ls.getsockname()[1], instruction={"speed_kibs": 1024, "speed_s": 2},
                    env={"PF_SPEED_S": "600"})
    finally:
        ls.close()
        for c in held:
            c.close()
    assert got["rc"] == "rc=1" and got["left"] == ["0-4"], got["out"] + got["log"]
    assert got["result"].startswith(f"{ID} bad"), got["result"]
    assert got["took"] < 60, got["took"]


@pytest.mark.parametrize("bad", [{"speed_kibs": 0}, {"speed_kibs": "1024; reboot"}, {"speed_kibs": 100000}, {"speed_kibs": "fast"},
                                 {"speed_s": 0}, {"speed_s": 3601}, {"speed_s": "30 -o /tmp/x"}])
def test_a_floor_that_is_there_and_is_not_a_whole_number_refuses_the_instruction(tmp_path, cc, pcl, bad):
    peer = Neighbour(pcl[0], tmp_path)
    try:
        got = needy(tmp_path, cc, pcl, peer.port, instruction=bad)
    finally:
        peer.close()
    assert got["rc"] == "rc=2", got["out"] + got["log"]
    assert got["left"] == ["0-4"] and peer.asked == []
    assert "the instruction is refused -- the speed floor" in got["log"], got["log"]
