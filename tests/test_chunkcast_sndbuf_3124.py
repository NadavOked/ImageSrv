"""#3124 (B-NET-18): the sender reads back the send buffer the kernel gave, and says it -- the other half of the receiver's `rcvbuf`.

`chunkcast send` asked for `SO_SNDBUF` (4 MiB) and never read what the kernel granted (it caps the ask at `wmem_max`), so a
sender whose buffer was cut said nothing. It now prints `sndbuf <asked> <granted>` once at the start (`-1` = not read, never the
ask). The line is evidence: `chunklog.log_tail` must not take it for the reason a send failed.

Behavioural: the real binary sends over real UDP; the line is read from its output.
"""
from __future__ import annotations

import re

from test_chunkcast_knobs_3522 import capture, cc, img   # noqa: F401  (fixtures)
from server.rounds import chunklog


def test_the_sender_says_the_send_buffer_it_asked_and_the_one_the_kernel_gave(cc, img, tmp_path):   # noqa: F811
    run, _ = capture(cc, img, tmp_path / "snd")
    lines = [s for s in run.out.read_text().splitlines() if s.startswith("sndbuf ")]
    assert len(lines) == 1, run.log()
    m = re.fullmatch(r"sndbuf (\d+) (-?\d+)", lines[0])
    assert m, lines
    assert int(m.group(1)) == 4 << 20, "the ask"
    assert int(m.group(2)) > 0, "the granted size was not read back"


def test_the_sndbuf_line_is_evidence_and_not_the_reason_a_send_failed(tmp_path):
    log = tmp_path / "send.log"
    log.write_text("sndbuf 4194304 425984\nknobs ahead=4 readahead=legacy order=seq fb_low=2 fb_high=6 qcap=8\nsent 3 100\n",
                   encoding="utf-8")
    assert chunklog.log_tail(log) == ""
    log.write_text("sndbuf 4194304 -1\nchunkcast: send: cannot send to 1.2.3.4: Network is unreachable\n", encoding="utf-8")
    assert chunklog.log_tail(log) == "chunkcast: send: cannot send to 1.2.3.4: Network is unreachable"
