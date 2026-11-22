"""#3593 -- `stream_timeout` says the stream stopped; a helper command that timed out is not that.

J1 (17/11, `room_b2ddf7cd`): one line, `פג הזמן: smartctl לא ענה תוך 5 שניות -- נעזב, לא נבדק`
(`capped_run`, waits.sh), made `classify` report "הזרם נעצר" on a round that ended written=4/4.
The signature now needs a stream-progress phrase next to "פג הזמן"; the emitters' own wording is
pinned against the agent source so the two cannot drift apart silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.agent import logs as agent_logs

AGENT = Path(__file__).resolve().parent.parent / "agent" / "lib"

#: (the line the agent writes, the file that writes it, the fragment of its source that must stay)
STREAM_STOPS = [
    ("[17:40:35] פג הזמן: restore stream -- 300 שניות בלי התקדמות (עצר על 123456 בייטים) -- נכשל",
     "core/waits.sh", "שניות בלי התקדמות"),
    ("[17:41:02] פג הזמן: chunkcast p3 לא הסתיים ולא התקדם -- נכשל", "restore/chunkrecv.sh", "לא הסתיים ולא התקדם"),
    ("[17:41:09] פג הזמן -- הזרם לא הגיע", None, None),   # #3173: its emitter (drawers.sh, udpcast) is gone
    ("[17:41:10] udp-receiver: receive-timeout", None, None),
]

#: timeouts that are NOT the stream: a helper that did not answer, a generic wait, the send side's finish.
NOT_THE_STREAM = [
    ("[17:40:35] פג הזמן: smartctl לא ענה תוך 5 שניות -- נעזב, לא נבדק", "core/waits.sh", "לא ענה תוך"),
    ("[17:40:36] פג הזמן: sgdisk לא הסתיים תוך 30 שניות -- נכשל", "core/waits.sh", "לא הסתיים תוך"),
    ("[17:40:37] פג הזמן: chunkcast send of partition 3 לא הסתיים תוך 120 שניות אחרי סוף הזרם -- נכשל",
     "capture/directchunk.sh", "לא הסתיים תוך"),
]


def _keys(text: str) -> list[str]:
    return [hit["key"] for hit in agent_logs.classify(text)]


@pytest.mark.parametrize("line", [line for line, _f, _s in STREAM_STOPS])
def test_a_real_stream_stop_is_still_a_stream_timeout(line):
    assert "stream_timeout" in _keys(f"[17:00:00] round started\n{line}\n[17:42:00] done\n"), line


@pytest.mark.parametrize("line", [line for line, _f, _s in NOT_THE_STREAM])
def test_a_timeout_of_a_helper_or_a_generic_wait_is_not_a_stream_timeout(line):
    assert "stream_timeout" not in _keys(line + "\n"), line


def test_seven_smartctl_timeouts_in_a_round_that_finished_say_nothing_about_the_stream():
    """The J1 log: seven such lines (17:27:24 .. 17:40:35) and nothing else that matches."""
    lines = "".join(f"[17:{27 + i}:24] פג הזמן: smartctl לא ענה תוך 5 שניות -- נעזב, לא נבדק\n" for i in range(7))
    assert _keys(lines + "[17:41:00] room: written=4/4\n") == []


def test_one_real_stream_stop_among_smartctl_timeouts_is_the_line_shown():
    smart = "[17:40:35] פג הזמן: smartctl לא ענה תוך 5 שניות -- נעזב, לא נבדק\n"
    real = "[17:40:50] פג הזמן -- הזרם לא הגיע\n"
    got = [h for h in agent_logs.classify(real + smart) if h["key"] == "stream_timeout"]
    assert len(got) == 1 and "הזרם לא הגיע" in got[0]["line"], got


@pytest.mark.parametrize("line,source,fragment", [t for t in STREAM_STOPS + NOT_THE_STREAM if t[1]])
def test_the_agents_wording_is_still_what_the_signature_was_written_against(line, source, fragment):
    assert fragment in (AGENT / source).read_text(encoding="utf-8"), (source, fragment)
