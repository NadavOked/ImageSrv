"""#3094 -- `systemctl restart` in the middle of a chunk wave is named for what it is.

The VM lab, 13/11 11:43 UTC: systemd sends SIGTERM to the whole cgroup, `chunkcast send` (a child of
the server) dies on it with 6 -- "stopped by a signal" -- and the sender thread read a child that had
failed, BEFORE the lifespan's `close()` set `_stop`. `room_rounds.failed_reason` said "chunkcast send
נכשל על p1.linux.pcl.zst (קוד 6, מעבר 1)", and the cloner's screen repeated it: an operator looks for a
fault in the sender or the image when the server was restarted. (`kill -9` of the main PID, with no
orderly stop, got the right reason from `reconcile_on_start`.)

The evidence that the server is going down is the signal that reached the SERVER itself (`ports.Listeners.
_on_signal`, the process's only SIGTERM/SIGINT handler), not the child's exit code. So:

* code 6 AND the server's own signal (even one that arrives a moment after the child died: systemd
  signals both at once) = the round fails in the name of the restart, in the wording of `session_orphaned`;
* code 6 with no such signal = a real failure, "chunkcast send נכשל" (someone stopped the child alone);
* a code that is not 6, signal or no signal, is a real failure -- never swallowed into "restarted".

A graceful stop is still a failure and is not silent or "closed" (#2907): the round is failed, by name.
"""

from __future__ import annotations

import signal
import threading

import pytest

pytest.importorskip("fastapi")

from server.core import shutdown
from server.net import ports
from server.rounds import chunksend
from test_chunksend_2533 import Runner, rig, wait_for   # noqa: F401 -- `rig` is a fixture

STOPPED = 6


def _wave_fails(rig, **kw) -> str:
    engine = rig.make(**kw)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    (why,) = rig.named("send_failed")
    return why


def test_a_child_stopped_by_the_signal_the_server_got_too_is_a_restart_not_a_send_failure(rig):
    rig.runner = Runner(code=STOPPED)
    down = threading.Event()
    down.set()
    why = _wave_fails(rig, going_down=down)
    assert "השרת הופעל מחדש או נעצר" in why and "p3.windows.pcl.zst" in why, why
    assert "נכשל על" not in why and "קוד" not in why, why          # the symptom is not the reason


def test_the_signal_that_reaches_the_server_a_moment_after_the_child_died_still_counts(rig):
    """systemd signals the server and its child together; the child may be reaped first. A grace, not a guess."""
    rig.runner = Runner(code=STOPPED)
    down = threading.Event()
    threading.Timer(0.3, down.set).start()
    why = _wave_fails(rig, going_down=down, shutdown_grace=5)
    assert "השרת הופעל מחדש או נעצר" in why, why


def test_a_child_stopped_alone_is_still_a_send_failure(rig):
    """Guard against swallowing a real failure: code 6 and NO signal at the server (`kill -TERM` of the
    child alone, an operator's mistake, an OOM that is not ours) is "chunkcast send נכשל"."""
    rig.runner = Runner(code=STOPPED)
    why = _wave_fails(rig, going_down=threading.Event(), shutdown_grace=0.2)
    assert "chunkcast send נכשל על p3.windows.pcl.zst (קוד 6, מעבר 1)" in why, why
    assert "הופעל מחדש" not in why


@pytest.mark.parametrize("code", [1, 2, 7, -9])
def test_any_other_code_is_a_send_failure_even_while_the_server_is_going_down(rig, code):
    rig.runner = Runner(code=code)
    down = threading.Event()
    down.set()
    why = _wave_fails(rig, going_down=down)
    assert f"נכשל על p3.windows.pcl.zst (קוד {code}, מעבר 1)" in why and "הופעל מחדש" not in why, why


def test_the_signal_handler_of_the_process_says_so_before_anything_else(monkeypatch):
    """`ports.Listeners._on_signal` is the process's SIGTERM/SIGINT handler (serve() installs it, every
    open/close installs it again): it sets the flag the engine reads."""
    monkeypatch.setattr(shutdown, "GOING_DOWN", threading.Event())
    assert not shutdown.GOING_DOWN.is_set()
    ports.Listeners()._on_signal(signal.SIGTERM, None)
    assert shutdown.GOING_DOWN.is_set()


def test_the_engine_reads_the_process_flag_unless_it_is_given_one(rig, monkeypatch):
    """Production builds the engine without `going_down`: it is the module's flag, read when the child dies."""
    flag = threading.Event()
    flag.set()
    monkeypatch.setattr(shutdown, "GOING_DOWN", flag)
    rig.runner = Runner(code=STOPPED)
    assert "השרת הופעל מחדש או נעצר" in _wave_fails(rig)
    assert chunksend.STOPPED_BY_SIGNAL == STOPPED
