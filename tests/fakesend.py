"""The fake sender of the server's tests, and the fake cloner that listens to it (#3173).

The chunk engine (`server/rounds/chunksend.py`) is the only stream engine. A test's server gets its
`chunkcast send` here: `Recorder` is the `sender_runner` that `create_app` takes -- it records every
command the engine would have run, hands back a `FakeProcess` the test controls, and writes the log a
real sender leaves beside the partition (`chunklog.log_path`), so a later pass never reads the
evidence of an earlier test.

A chunk wave is a conversation, not one process: the engine sends a partition only after every live
cloner said `listening` (`POST /api/v1/agent/chunk-pass`), and closes it only after every one said
`ended` and holds every chunk. `Cloners` is that side of the conversation, as `chunkrecv.sh` holds
it -- nothing in it reaches into the engine: it speaks HTTP only.

Nothing here imports a test module: this file is a helper, and the tests of the sender import it,
not the other way round.
"""

from __future__ import annotations

import subprocess
import threading
import time

from server.rounds import chunklog

try:
    from conftest import HANG_S
except ImportError:                      # pragma: no cover -- imported outside pytest
    HANG_S = 60.0

#: What `chunkcast send` prints for a pass that went out whole (agent/chunkcast.c): the knobs it ran
#: with, a progress line, its counters, how long it slept for its own pacing, and `done`. A Recorder
#: writes it by default, so a test that expects a clean pass does not fail for want of the evidence.
DONE_LOG = (
    "knobs ahead=0 readahead=legacy order=mixed fb_low=1 fb_high=4 qcap=64"
    " pace=smooth batch=1 reader=thread readq=8\n"
    "sent 1 26\n"
    "sendstats read_ms=0 lag_forgiven_wire=0 lag_forgiven_disk=0 forgiven_ms_wire=0"
    " forgiven_ms_disk=0 enobufs=0 eagain=0 eintr=0 refused=0 over_ms=0 readq_empty_ms=0 readq_full_ms=0\n"
    "paced 0 0\n"
    "done 1 26 0\n"
)


#: the exit code of a blocking fake that nobody released within `HANG_S` (`timeout`'s own code)
HUNG = 124


class FakeProcess:
    """A fake `chunkcast send`: blocked until `release`, exactly like a sender mid-pass."""

    def __init__(self, cmd, code=0, block=False, delay=0.0,
                 ignore_term=False, ignore_kill=False):
        self.cmd = cmd
        self.code = code
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.delay = delay          # how long `wait` really takes (#341)
        self.ignore_term = ignore_term
        self.ignore_kill = ignore_kill
        # a PID that is not in /proc: after poll() said dead, the PID check never meets a real process
        self.pid = 10_000_000
        self._gate = threading.Event()
        if not block:
            self._gate.set()

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.delay:
            threading.Event().wait(self.delay)
        if timeout is not None:
            if not self._gate.wait(timeout=timeout):
                raise subprocess.TimeoutExpired(self.cmd, timeout)
            return self.code
        if not self._gate.wait(timeout=HANG_S):
            # a leaked, never-released fake must not outlive the run -- and must not end as a pass that went out
            # whole: nobody released it, so it ends as a sender that failed (`HUNG`), and stays ended
            if self.code == 0:
                self.code = HUNG
            self._gate.set()
        return self.code

    def poll(self):
        return self.code if self._gate.is_set() else None

    def terminate(self):
        self.terminated = True
        if not self.ignore_term:
            self.code = -15
            self._gate.set()

    def kill(self):
        self.killed = True
        if not self.ignore_kill:
            self.code = -9
            self._gate.set()

    def release(self):
        self._gate.set()


class Recorder:
    """What the chunk engine would have run: every `chunkcast send` command, in order.

    `lists[n]` is the chunk list of pass n (`--chunks-file`), read while the file exists -- the engine
    removes it after the pass -- or None for a full pass.
    """

    def __init__(self, code=0, block=False, delay=0.0, log_text=DONE_LOG,
                 ignore_term=False, ignore_kill=False):
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self.lists: list[str | None] = []
        self.code = code
        self.block = block
        self.delay = delay
        self.log_text = log_text
        self.ignore_term = ignore_term
        self.ignore_kill = ignore_kill
        self.spawned = threading.Event()

    def __call__(self, cmd):
        if "--file" in cmd:
            # a log for each partition, like the real `chunklog.run_process`: derived from --file, and opened anew
            # for every run -- `log_text=None` is a sender that printed nothing, never the log of an earlier test
            chunklog.log_path(cmd[cmd.index("--file") + 1]).write_text(self.log_text or "", errors="replace")
        self.lists.append(open(cmd[cmd.index("--chunks-file") + 1], encoding="ascii").read().strip()
                          if "--chunks-file" in cmd else None)
        process = FakeProcess(
            cmd, self.code, self.block, self.delay,
            self.ignore_term, self.ignore_kill,
        )
        # processes before commands: a test that waits for len(commands) and reads processes[n] at once
        # fell on IndexError under load (round gates 14 and 15, 30/10) -- the process was not recorded yet
        self.processes.append(process)
        self.commands.append(cmd)
        self.spawned.set()
        return process

    def sent_parts(self) -> list[int]:
        """The partition of every command, in order (`--part`)."""
        return [int(cmd[cmd.index("--part") + 1]) for cmd in self.commands]


def wait_for(predicate, timeout=HANG_S):
    """A hang detector, never a speed assertion: returns the moment `predicate` holds. The ceiling is wall time
    (a predicate that takes long -- one that speaks HTTP -- does not stretch it)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def arg(cmd: list[str], flag: str) -> str:
    """The value of `flag` in a recorded command."""
    return cmd[cmd.index(flag) + 1]


# --- the cloners' side of a chunk wave ---------------------------------------------------------


class Cloners:
    """The cloners of one wave, speaking the chunk protocol to the server over HTTP.

    `members` = {mac: [serial, ...]} -- the drawers each cloner writes. `client` is any TestClient of the
    app (an agent is not logged in). `parts` = [(partition index, chunks), ...] in the order the engine
    sends them; without it, the partitions of the image every server fixture carries.
    """

    def __init__(self, client, wave_id: str, members: dict[str, list[str]], *,
                 parts: list[tuple[int, int]] | None = None, write_mbps: int = 150):
        self.client, self.wave_id, self.members, self.write_mbps = client, wave_id, members, write_mbps
        if parts is None:                    # the image every server fixture carries (`conftest.write_image`)
            from conftest import MANIFEST_256, INDEX_CHUNKS
            parts = [(p["index"], INDEX_CHUNKS) for p in MANIFEST_256["partitions"] if p.get("file")]
        self.parts = parts

    def tell(self, mac: str, part: int, state: str, *, pass_: int = 1, durable: list | None = None,
             serials: list[str] | None = None):
        """One `chunk-pass` report. `durable` = the closed ranges [[first, last], ...] every drawer holds
        of `part` (None: the report carries no ledger, as a `listening` before the first byte)."""
        targets = [{"serial": serial, "write_mbps": self.write_mbps,
                    **({} if durable is None else {"durable": [{"part": part, "ranges": durable}]})}
                   for serial in (self.members[mac] if serials is None else serials)]
        return self.client.post("/api/v1/agent/chunk-pass", json={
            "mac": mac, "session_id": self.wave_id, "part": part, "pass": pass_, "state": state,
            "targets": targets})

    def listen(self, part: int, *, pass_: int = 1, macs: list[str] | None = None) -> dict[str, dict]:
        """Every cloner says `listening` for `part`; the answers, by mac. Each must be told to receive."""
        answers = {}
        for mac in (self.members if macs is None else macs):
            got = self.tell(mac, part, "listening", pass_=pass_)
            assert got.status_code == 200, got.text
            answers[mac] = got.json()
            assert answers[mac].get("action") == "receive", (mac, answers[mac])
        return answers

    def ended(self, part: int, chunks: int, *, pass_: int = 1, macs: list[str] | None = None) -> dict[str, dict]:
        """Every cloner says `ended` holding every chunk of `part`; the last answer of each, by mac --
        asked again while the server says `wait` (it answers once every live cloner has reported)."""
        whole = [[0, chunks - 1]] if chunks else []
        answers = {}

        def all_answered():
            for mac in (self.members if macs is None else macs):
                got = self.tell(mac, part, "ended", pass_=pass_, durable=whole)
                assert got.status_code == 200, got.text
                answers[mac] = got.json()
            return all(a.get("action") != "wait" for a in answers.values())

        assert wait_for(all_answered), f"the server never closed pass {pass_} of partition {part}: {answers}"
        return answers

    def receive(self, recorder: Recorder, *, release: bool = True) -> None:
        """The whole wave, with no loss: for each partition every cloner listens, the engine sends once
        (`recorder` sees the command; a blocking fake is released), and every cloner ends with every
        chunk. The reports of the drawers themselves (`/agent/progress`) are the test's own."""
        for part, chunks in self.parts:
            before = len(recorder.commands)
            self.listen(part)
            assert wait_for(lambda: len(recorder.commands) > before), (
                f"the engine never sent partition {part}: {recorder.commands}")
            assert arg(recorder.commands[before], "--part") == str(part), recorder.commands[before]
            if release:
                recorder.processes[before].release()
            answers = self.ended(part, chunks)
            # `done` alone is not "written": a drawer the server failed by name is out of the count, and the
            # answer is still `done` -- with `fail` beside it (principle 5)
            assert all(a.get("action") == "done" and "fail" not in a for a in answers.values()), answers
