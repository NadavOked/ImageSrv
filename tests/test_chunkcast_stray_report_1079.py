"""#1079 / #2485: מה שהסוכן (`chunkrecv.sh`, `chunkgate.sh`) עושה עם הכתובת של השרת ועם המונה של החבילות הזרות.

`chunkcast` עצמו -- שהוא מסנן, סופר ואומר `stray <N>` -- נבדק מול התהליך האמיתי ב-test_chunkcast_from_filter_1079.py.
כאן ה-shell האמיתי מול `chunkcast` מזויף (הרתמה של test_chunkrecv_2533.py): הכתובת שה-hello נשא מגיעה ל-`--from`,
והמונה שנאמר מגיע לשרת ב-`ended` של אותו מעבר, ורק שם.

**בקרה שלילית:** החזרת `chunkgate.sh` ו-`chunkrecv.sh` לבסיס מפילה את כל הטסטים כאן.
"""

from __future__ import annotations

from native import requires_native
from test_agent import BASH
from test_chunkrecv_2533 import DONE, FETCH, RECEIVE1, _run

pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

SERVER = "10.55.0.5"
SLOW = "REAL_SLEEP=1 TEST_START_CEILING=30"


def _recvs(trace):
    return [t for t in trace if t.startswith("chunkcast recv")]


def _strays(got):
    return [(b["state"], b.get("stray")) for b in got["bodies"]]


def test_the_receiver_is_given_the_address_the_server_named(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"CHUNK_SOURCE={SERVER}")
    assert got["trace"][-1] == "rc=0", got
    (recv,) = _recvs(got["trace"])
    assert f"--from {SERVER} " in recv, recv


def test_without_an_address_the_receiver_is_started_as_it_was_before(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE])
    (recv,) = _recvs(got["trace"])
    assert "--from" not in recv, recv


def test_the_count_of_stray_packets_is_told_to_the_server_in_the_ended_of_that_pass_and_only_there(tmp_path):
    """chunkcast אומר `stray` כשהוא מתחיל להאזין, כל פעם שהמספר גדל, ובסוף: האחרון הוא של המעבר, ושדה אחד בלבד
    (JSON עם מפתח כפול הוא שני ערכים לפי הקורא)."""
    got = _run(tmp_path, [RECEIVE1, FETCH, DONE],
               env=f"CHUNK_SOURCE={SERVER} RECV_STRAY='0 4 11' RECV_COUNT=2 RECV_RANGES=0-0,2-2 {SLOW}")
    assert got["trace"][-1] == "rc=0", got
    assert _strays(got) == [("listening", None), ("ended", 11), ("ended", None)], _strays(got)   # not the fetch's: it receives nothing


def test_zero_strays_is_said_as_zero(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"CHUNK_SOURCE={SERVER} RECV_STRAY='0 0'")
    assert _strays(got) == [("listening", None), ("ended", 0)], _strays(got)


def test_a_receiver_that_said_no_count_is_not_reported_as_zero(tmp_path):
    """chunkcast ישן, או שאין `--from`: "לא נקרא" אינו 0 (עיקרון 5). השדה חסר."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"CHUNK_SOURCE={SERVER}")
    assert _strays(got) == [("listening", None), ("ended", None)], _strays(got)
