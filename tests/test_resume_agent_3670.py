"""#3670 (A32), צד הסוכן (`agent/lib/restore/resume.sh`): התקרה של הקריאה החוזרת היא מספר של המוח, לא של הסוכן.

עד #3670 ישב בסוכן `RESUME_MIN_MBPS=10` ו-`+ 60`: תקרה של שמונה שעות או של דקה נקבעה שם, ושום כלל במוח לא ידע עליה. עכשיו
השרת אומר אותה בתשובת `/agent/resume` (`readback`: `min_mbps`, `slack_s`), והסוכן רק מחשב ממנה את מספר השניות
(`bytes / (min_mbps * 1000000) + slack_s`). **ברירת המחדל הבטוחה כשאין החלטה:** תשובה בלי המספרים האלה, או עם מספרים שאינם
שלמים וחיוביים = המגירה נכתבת מההתחלה, ולא "נשתמש במה שהיה".

**בקרה שלילית:** החזרת `resume.sh` לבסיס מפילה את הטסטים כאן בכשל התנהגותי: `_rs_ceiling` אינו קיים (אין תקרה מהמוח),
והתקרה הישנה (62 שניות לחמש החתיכות) נשארת גם כשהמוח אומר שנייה אחת. השומר היחיד שעובר בשני המצבים בכוונה:
`test_a_partition_is_read_back_when_the_ceiling_leaves_room`.
"""

from __future__ import annotations

import json

import pytest

from native import requires_native
from test_agent import BASH
from test_resume_agent_2528 import _answer, _run

pytestmark = requires_native(("bash", BASH), "jq", why="התשובה של השרת נקראת ב-jq, כמו בסוכן")

FIVE_CHUNKS = 5 * 4194304


def _with(readback) -> dict:
    """The server's answer for sda, with `readback` as it carries it (None = the key is not there at all)."""
    answer = _answer()
    if readback is None:
        del answer["targets"][0]["readback"]
    else:
        answer["targets"][0]["readback"] = readback
    return answer


# --- the ceiling is computed from the brain's numbers -------------------------------------------------------------------


@pytest.mark.parametrize("readback, want", [
    ({"min_mbps": 10, "slack_s": 60}, 62),            # the numbers the agent kept until #3670: 20971520 / 10e6 = 2, and a minute
    ({"min_mbps": 20, "slack_s": 5}, 6),              # the brain's, not the agent's: 1 + 5
    ({"min_mbps": 1, "slack_s": 1}, 21),              # 20971520 / 1e6 = 20, and 1
    ({"min_mbps": 20971520, "slack_s": 1}, 1),        # the ceiling the next test waits out
])
def test_the_ceiling_is_the_bytes_over_the_brains_rate_plus_the_brains_slack(tmp_path, readback, want):
    got = _run(tmp_path, f"ceiling {FIVE_CHUNKS}", _with(readback))
    assert f"ceiling={want}" in got["trace"], got


@pytest.mark.parametrize("readback", [
    None, {}, {"min_mbps": 10}, {"slack_s": 60}, {"min_mbps": "10", "slack_s": 60}, {"min_mbps": 10, "slack_s": "60"},
    {"min_mbps": 0, "slack_s": 60}, {"min_mbps": 10, "slack_s": 0}, {"min_mbps": -10, "slack_s": 60},
    {"min_mbps": 10.5, "slack_s": 60}, {"min_mbps": None, "slack_s": 60}, {"min_mbps": True, "slack_s": 60}, [10, 60], "10 60",
], ids=lambda r: json.dumps(r))
def test_no_ceiling_by_the_brain_is_no_ceiling_not_a_default_of_the_agents(tmp_path, readback):
    got = _run(tmp_path, f"ceiling {FIVE_CHUNKS}", _with(readback))
    assert got["trace"][-1] == "ceiling=", got["trace"]        # the helper ran and printed nothing
    assert "command not found" not in got["out"], "the helper is not there at all: that is not 'no ceiling', that is the base"


def test_a_ceiling_of_another_disk_is_not_this_disks(tmp_path):
    answer = _answer(serial="SER-sdb")
    got = _run(tmp_path, f"ceiling {FIVE_CHUNKS}", answer)
    assert got["trace"][-1] == "ceiling=" and "command not found" not in got["out"], got


# --- what the drawer does with it ------------------------------------------------------------------------------------------


def test_a_read_back_that_outlasts_the_brains_ceiling_writes_the_partition_from_the_start(tmp_path):
    """המוח אומר שנייה אחת. הקריאה החוזרת לוקחת שנייה אחת של זמן אמת, וה-`sleep` של הרתמה הוא 0.05: התקרה הישנה (62
    "שניות") הייתה מחכה לה עד הסוף ומאמתת אותה; התקרה של המוח חותכת אותה, והמחיצה נכתבת מההתחלה."""
    got = _run(tmp_path, "seed", _with({"min_mbps": 20971520, "slack_s": 1}), env="RB_SLOW=1")
    assert not (tmp_path / "chunk" / "p3.state").exists(), got
    assert "לא הסתיימה תוך 1 שניות" in got["log"], got["log"]


def test_a_partition_is_read_back_when_the_ceiling_leaves_room(tmp_path):
    """השומר: אותה קריאה איטית, ותקרה של דקה -- נקראת בחזרה ונכנסת למצב (עובר גם על הבסיס, וזה בכוונה: הוא מראה שהכשל
    של הטסט שלמעלה הוא התקרה ולא הקריאה האיטית)."""
    got = _run(tmp_path, "seed", _with({"min_mbps": 10, "slack_s": 60}), env="RB_SLOW=1")
    assert (tmp_path / "chunk" / "p3.state").exists(), got
    assert "אומתו 3 מתוך 5" in got["log"], got["log"]


def test_a_seed_without_the_brains_ceiling_reads_nothing_back_and_writes_the_partition_from_the_start(tmp_path):
    got = _run(tmp_path, "seed", _with(None))
    assert not any(t.startswith("readback") for t in got["trace"]), got["trace"]
    assert not (tmp_path / "chunk" / "p3.state").exists(), got
    assert "המוח לא נתן ל-sda תקרה לקריאה החוזרת" in got["log"], got["log"]


def test_a_drawer_the_brain_said_resume_without_a_ceiling_is_written_from_the_start(tmp_path):
    got = _run(tmp_path, "round", _with(None), env="MARKED=sda")
    assert "POST /api/v1/agent/resume" in got["trace"], got
    assert "HELD sda" not in got["trace"] and "REST=sda sdb" in got["trace"], got["trace"]
    assert "fits sda" not in got["trace"], "it does not even go on to the gates that write nothing"
    assert "המוח לא נתן תקרה לקריאה החוזרת" in got["log"], got["log"]


def test_the_question_carries_the_raw_measurement_and_no_computed_time(tmp_path):
    """הסוכן שולח מה שקרא (`read_mbps`, `read_bytes`); כמה זמן זה ייקח -- המוח מחשב."""
    got = _run(tmp_path, "round", _answer(), env="MARKED=sda")
    [target] = got["asked"]["targets"]
    assert set(target) == {"dev", "serial", "size_bytes", "read_mbps", "read_bytes"}, target
    assert target["read_mbps"] == 134 and target["read_bytes"] == FIVE_CHUNKS
