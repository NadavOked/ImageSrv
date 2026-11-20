"""#3260: ממתינים לשרת בין שאלה לשאלה אחרי `ended` -- הראשונות מהר, אחר כך כמו קודם.

אחרי `ended` השרת עונה `wait` עד שהתוכנית של המעבר הבא (או `done`) מוכנה, והסוכן ישן `CHUNK_ASK_POLL_S` (2 ש') לפני
השאלה הבאה: זמן מת של עד שתי שניות בכל מעבר בין מחיצות (לפי הקוד; מדידה על ברזל טרם נעשתה). עכשיו
`CHUNK_ASK_FAST_N` השאלות הראשונות באות כל `CHUNK_ASK_FAST_S` (0.3 ש'), ומשם `CHUNK_ASK_POLL_S`.

מודדים: הזמן מהודעת `ended` ועד ההודעה הבאה (השרת ענה `wait` פעם אחת ואז `done`), עם `sleep` אמיתי וה-shell האמיתי.
**בקרה שלילית:** `agent/lib/restore/chunkrecv.sh` חזר לבסיס -- הזמן הזה הוא ה-`CHUNK_ASK_POLL_S` שלם (כאן 2 ש').
"""

from __future__ import annotations

import test_chunkrecv_2533 as recv

WAIT = {"ok": True, "action": "wait", "poll": 2}
POST_LINE = 'trace "POST $(jq -r ' + "'" + r'"\(.state) \(.part)"' + "'" + ' "$2")"'    # the harness's own line, to which the time is added
HARNESS = (recv.HARNESS
           .replace("CHUNK_ASK_POLL_S=1;", "CHUNK_ASK_POLL_S=${TEST_ASK_POLL_S:-1};")
           .replace(POST_LINE, POST_LINE + '; date +%s%N > "$RUN_DIR/t.$_n"')
           .replace("WAIT_STREAM_STALL_S=1; WAIT_DRAWER_S=1;", "WAIT_STREAM_STALL_S=${TEST_CEILING:-1}; WAIT_DRAWER_S=${TEST_CEILING:-1};"))
ENV = "REAL_SLEEP=1 TEST_START_CEILING=30"


def _gaps_ms(tmp_path, answers: list[dict], env: str = "") -> list[int]:
    """The time between one control message and the next, in ms (message 1 = `listening`, 2 = the first `ended`)."""
    assert HARNESS != recv.HARNESS and "t.$_n" in HARNESS and "TEST_ASK_POLL_S" in HARNESS    # the three hooks took
    old, recv.HARNESS = recv.HARNESS, HARNESS
    try:
        got = recv._run(tmp_path, answers, env=f"{ENV} {env}")
    finally:
        recv.HARNESS = old
    stamps = [int((tmp_path / f"t.{n}").read_text()) for n in range(1, len(answers) + 1)]
    assert got["trace"][-1].startswith("rc=0"), got
    return [(b - a) // 1_000_000 for a, b in zip(stamps, stamps[1:])]


def test_the_question_after_ended_is_asked_again_within_a_fraction_of_a_second(tmp_path):
    """(neg) `ended` -> wait -> the next question -> `done`: with the ask interval at 2 s the second question is 2 s later, not 0.3."""
    gaps = _gaps_ms(tmp_path, [recv.RECEIVE1, WAIT, recv.DONE], env="TEST_ASK_POLL_S=2")
    assert gaps[1] < 500, gaps                 # gaps[0] is the pass itself (listening -> ended)


def test_after_the_quick_questions_the_interval_is_the_old_one(tmp_path):
    """Guard, both states: a server that keeps saying `wait` (the others are not done) is not asked at 0.3 s for ever --
    the first five are quick, the sixth wait is the full interval (here 1 s)."""
    gaps = _gaps_ms(tmp_path, [recv.RECEIVE1] + [WAIT] * 6 + [recv.DONE], env="TEST_CEILING=30")
    quick, slow = gaps[1:6], gaps[6]
    assert all(g < 700 for g in quick), gaps   # (neg) in the base every one of these is >= 1000
    assert slow >= 900, gaps
