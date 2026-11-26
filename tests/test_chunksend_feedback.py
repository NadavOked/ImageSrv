"""#3512: the command-line plumbing only; the server invents no new rate policy."""
from pathlib import Path
from types import SimpleNamespace

from server.rounds.chunksend import ChunkEngine


def _cmd(engine, kbps, write_kbps):
    wave = SimpleNamespace(key="0123456789abcdef", round=1)
    return ChunkEngine._command(engine, wave, {"index": 3}, Path("image.zst"), kbps, write_kbps, None)


def _engine(**kw):
    return SimpleNamespace(port=7713, interface=None, source=None, feedback=True, write_floor=lambda: 20, **kw)   # פורט-הפצה-מכוון


def test_max_is_twice_the_write_rate_and_min_is_the_drive_floor():
    cmd = _cmd(_engine(), 100000, 400000)
    for key, value in (("--rate-min", "160000"), ("--rate-max", "800000"), ("--feedback", "on"),
                       ("--control-port", "7714"), ("--write-kbps", "400000"), ("--kbps", "100000")):   # פורט-הפצה-מכוון
        assert cmd[cmd.index(key) + 1] == value


def test_floor_above_the_start_never_exceeds_it_and_zero_floor_is_not_zero():
    assert _cmd(_engine(), 100000, 100000)[_cmd(_engine(), 100000, 100000).index("--rate-min") + 1] == "100000"
    zero = SimpleNamespace(port=7713, interface=None, source=None, feedback=True, write_floor=lambda: 0)   # פורט-הפצה-מכוון
    cmd = _cmd(zero, 100000, 400000)
    assert cmd[cmd.index("--rate-min") + 1] == "1"
    assert cmd[cmd.index("--rate-max") + 1] == "800000"      # #3512: the feedback may rise above the start; --kbps stays the wire cap


def test_wire_only_start_is_the_network_budget_and_the_switch_is_forwarded():
    cmd = _cmd(_engine(), 100000, 0)
    assert cmd[cmd.index("--rate-min") + 1] == cmd[cmd.index("--rate-max") + 1] == "100000"
    off = SimpleNamespace(port=7713, interface=None, source=None, feedback=False, write_floor=lambda: 20)   # פורט-הפצה-מכוון
    assert _cmd(off, 100000, 400000)[_cmd(off, 100000, 400000).index("--feedback") + 1] == "off"


def test_feedback_lines_are_progress_not_the_failure_reason(tmp_path):
    from server.rounds import chunklog
    log = tmp_path / "send.log"
    log.write_text("chunkcast: send: the socket refused a packet: boom\n"
                   "sent 3 100\npaced 1 2\nrate 395000 decrease\nfeedback malformed=2\n"
                   "left-behind 0000000000000002 10.0.0.7 1\nrate 400000 increase\n")
    assert chunklog.log_tail(log) == "chunkcast: send: the socket refused a packet: boom"
