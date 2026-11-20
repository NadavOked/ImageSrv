"""#3498: the sender's own count of what held it back reaches K8 -- `chunkcast send` says `paced <wire_ms> <disk_ms>` once a
second (how long it slept for `--kbps` and for `--write-kbps`), `chunklog.paced_of` reads the last one, the engine sums
it over the first passes (`rate_outcome["paced_ms"]`), and `chunk_rate.sample_of` keeps it for `server_release`.

Not measured is None everywhere (principle 5): a log that is missing, a log with no such line, a partition whose log
was not read. Zero is a sender that never slept.

**Negative control:** `server/rounds/chunklog.py`, `server/rounds/chunksend.py` and `server/rounds/chunk_rate.py` back to
the base -- `paced_of` does not exist, and `rate_outcome` has no `paced_ms`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.rounds import chunklog, chunk_rate

from test_chunk_nic_gate_2533 import _sending                      # noqa: F401 -- fixture dependency of the rig
from test_chunksend_2533 import ALL, SID, Runner, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_server_room import CLONER1, room_server                 # noqa: F401 -- shared fixture


def test_the_last_paced_line_is_the_count_and_a_missing_one_is_not_measured(tmp_path):
    log = tmp_path / "send.log"
    log.write_text("sent 1 10\npaced 100 200\nsent 2 20\npaced 1500 2500\nnoise\npaced 7\n", encoding="utf-8")
    assert chunklog.paced_of(log) == (1500, 2500)
    log.write_text("sent 1 10\nchunkcast: send: stopped\n", encoding="utf-8")
    assert chunklog.paced_of(log) is None                                  # no line: not measured
    assert chunklog.paced_of(tmp_path / "missing.log") is None             # no log: not measured
    log.write_text("paced 0 0\n", encoding="utf-8")
    assert chunklog.paced_of(log) == (0, 0)                                # a sender that never slept


def test_sample_of_keeps_a_measured_sleep_and_drops_a_malformed_one():
    base = {"chunks": 10, "lost_chunks": 0, "first_pass_s": 100, "bytes": 10, "stream_zbytes": 5, "ended": "send_done"}
    assert chunk_rate.sample_of(1000, {**base, "paced_ms": {"wire": 5, "disk": 0}})["paced_ms"] == {"wire": 5, "disk": 0}
    for bad in (None, {"wire": 5}, {"wire": -1, "disk": 0}, {"wire": True, "disk": 0}, "5"):
        assert chunk_rate.sample_of(1000, {**base, "paced_ms": bad})["paced_ms"] is None
    assert chunk_rate.sample_of(1000, base)["paced_ms"] is None


class _LoggingRunner(Runner):
    """A fake `chunkcast send` that leaves the log a real one leaves: the file of `--file`, ending in a `paced` line."""

    def __init__(self, paced: str | None):
        super().__init__()
        self.paced = paced

    def __call__(self, cmd):
        process = super().__call__(cmd)
        if self.paced is not None:
            chunklog.log_path(cmd[cmd.index("--file") + 1]).write_text(f"sent 10 20\n{self.paced}\ndone 10 20 5\n")
        return process


def _first_pass(rig):
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": ALL}, skipped={"C1": 0})["action"] == "done"


def test_the_engine_reports_what_the_sender_slept_over_the_first_pass(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    rig.runner = _LoggingRunner("paced 1200 3400")
    engine = rig.make()
    _first_pass(rig)
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine.rate_outcome(SID)["paced_ms"] == {"wire": 1200, "disk": 3400}


def test_a_sender_that_left_no_log_is_not_measured_not_zero(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    rig.runner = _LoggingRunner(None)
    engine = rig.make()
    _first_pass(rig)
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine.rate_outcome(SID)["paced_ms"] is None
