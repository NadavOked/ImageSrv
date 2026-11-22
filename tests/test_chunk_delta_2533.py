"""#2533 -- the `durable` report as a delta (the coordinator's decision, 13/11).

The cumulative ledger, sent whole in every report, grows with the damage: a 200GB image
(47,500 chunks) with every other chunk missing on two disks is 47,500 ranges -- over the
1MiB a request body may be -- and it was sent again every two seconds. Now:

- the agent sends only what became durable since the last report the server ACKNOWLEDGED
  (`durable_seq`), a piece of at most 30,000 ranges a request;
- the server answers the next `durable_seq`, or `durable_full` when it cannot place the
  report (a reply that was lost, a server that restarted) -- and then everything is sent again;
- the repair stage reads the union the server holds, as before.

Three layers: the server's side alone (runs anywhere), the agent's shell alone, and the two
together -- the agent's real shell builds every request and reads every answer, the real
routes answer them (through the app's body cap), and this file only carries the bytes.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256
from native import requires_native
from server import db
from server.agent import body as agent_body
from server.rounds import chunkledger, repair
from server.rounds.durableseq import DurableSeq
from agentlib import agent_sources
from fakesend import wait_for
from test_agent import AGENT, BASH, posix
from test_chunksend_2533 import _index_the_image, _room_wave
from test_server_room import CLONER1, room_server   # noqa: F401 -- a fixture

REPO = AGENT.parent
SIZE = 256060514304
PART = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
needs_agent = requires_native(("bash", BASH), "jq", posix=True,
                              why="the agent's own shell builds the reports, and reads the answers with jq")
JSON = {"Content-Type": "application/json"}
NL = chr(10)


# --- the server alone ------------------------------------------------------------------


def test_a_report_that_does_not_number_its_ledger_is_neither_counted_nor_checked():
    seq = DurableSeq()
    assert seq.admit("ses_1", "m", "pass", {}) is None and seq.answer("ses_1", "m", "pass", None) == {}
    assert seq.admit("ses_1", "m", "pass", {"durable_seq": True}) is None        # a bool is not a number
    assert seq.admit(None, "m", "pass", {"durable_seq": 0}) is None


def test_the_number_continues_only_what_this_server_answered_last():
    seq = DurableSeq()
    assert seq.admit("ses_1", "m", "pass", {"durable_seq": 0}) is True
    assert seq.answer("ses_1", "m", "pass", True) == {"durable_seq": 1}
    assert seq.admit("ses_1", "m", "pass", {"durable_seq": 0}) is False          # the reply to it never arrived
    assert seq.answer("ses_1", "m", "pass", False) == {"durable_seq": 1, "durable_full": True}
    assert seq.admit("ses_1", "m", "pass", {"durable_seq": 1}) is True
    # a channel, a machine and a round each count alone
    assert seq.admit("ses_1", "m", "progress", {"durable_seq": 0}) is True
    assert seq.admit("ses_1", "other", "pass", {"durable_seq": 0}) is True
    assert seq.admit("ses_2", "m", "pass", {"durable_seq": 1}) is False
    # a server that restarted knows nothing: whoever is past 0 is asked for everything
    assert DurableSeq().admit("ses_1", "m", "pass", {"durable_seq": 1}) is False


def _wave(room_server, chunks: int = 10) -> str:
    """A chunk wave of one cloner with two drawers. The fake sender holds its pass open (5s): what
    is tested here is the ledger, and the engine's verdict on a pass waits for the pass to end."""
    ctx = room_server["ctx"]
    room_server["recorder"].block = True
    _index_the_image(ctx, chunks=chunks)
    wave, _hello = _room_wave(room_server)
    return wave


def _pass(room_server, wave: str, seq: object, ranges: dict, state: str = "ended", **more):
    body = {"mac": CLONER1, "session_id": wave, "part": PART, "pass": 1, "state": state, "targets": [
        {"serial": serial, "write_mbps": 150, "failed": False, "durable": [{"part": PART, "ranges": r}]}
        for serial, r in ranges.items()], **more}
    if seq is not None:
        body["durable_seq"] = seq
    return room_server["anon"].post("/api/v1/agent/chunk-pass", json=body)


def _progress(room_server, wave: str, seq: object, ranges: object) -> dict:
    target = {"dev": "sda", "bytes_written": 10, "bytes_total": 100, "state": "writing"}
    if ranges is not None:
        target["durable"] = [{"part": PART, "ranges": ranges}]
    body = {"session_id": wave, "mac": CLONER1, "state": "writing", "targets": [target]}
    if seq is not None:
        body["durable_seq"] = seq
    answer = room_server["anon"].post("/api/v1/agent/progress", json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _locked_once(monkeypatch) -> list:
    """`chunkledger.record` fails once, as a database that is locked does, and then works."""
    real, failed = chunkledger.record, []

    def record(*args, **kwargs):
        if not failed:
            failed.append(1)
            raise sqlite3.OperationalError("database is locked")
        return real(*args, **kwargs)
    monkeypatch.setattr(chunkledger, "record", record)
    return failed


def _ledger(ctx, serial: str):
    return chunkledger.of_disk(ctx.conn, serial, SIZE).get(PART, {}).get("ranges")


def test_chunk_pass_takes_a_delta_that_continues_and_asks_for_everything_when_it_does_not(room_server):
    ctx = room_server["ctx"]
    wave = _wave(room_server)
    first = _pass(room_server, wave, 0, {"S1": [], "S2": []}, state="listening").json()
    assert first["action"] == "receive" and first["durable_seq"] == 1
    assert room_server["recorder"].spawned.wait(timeout=5)

    got = _pass(room_server, wave, 1, {"S1": [[0, 3]]}).json()
    assert got["durable_seq"] == 2 and "durable_full" not in got and _ledger(ctx, "S1") == [(0, 3)]
    got = _pass(room_server, wave, 2, {"S1": [[6, 7]]}).json()                    # only what is new: a union
    assert got["durable_seq"] == 3 and _ledger(ctx, "S1") == [(0, 3), (6, 7)]

    # the agent never heard that answer: it sends from 2 again, and more. The server cannot know what
    # else it missed -- nothing of this report is recorded, nothing is decided, everything is asked for
    gap = _pass(room_server, wave, 2, {"S1": [[6, 9]]}).json()
    assert gap == {"ok": True, "action": "wait", "durable_seq": 3, "durable_full": True}
    assert _ledger(ctx, "S1") == [(0, 3), (6, 7)]
    whole = _pass(room_server, wave, 3, {"S1": [[0, 3], [6, 9]]}).json()
    assert whole["durable_seq"] == 4 and _ledger(ctx, "S1") == [(0, 3), (6, 9)]

    # a server that restarted (the numbers are in memory, on purpose): the same answer
    ctx.durable_seq = DurableSeq()
    again = _pass(room_server, wave, 4, {"S1": []}).json()
    assert again["durable_full"] is True and again["durable_seq"] == 0 and again["action"] == "wait"
    # an agent from before the delta sends the whole list with no number: taken as before
    plain = _pass(room_server, wave, None, {"S1": [[0, 9]]}).json()
    assert "durable_seq" not in plain and _ledger(ctx, "S1") == [(0, 9)]
    ctx.chunk.stop(wave)


def test_a_piece_of_a_long_report_is_recorded_and_decides_nothing(room_server):
    """`durable_more`: more pieces follow. The ranges are the server's; the machine has not reported yet."""
    ctx = room_server["ctx"]
    wave = _wave(room_server)
    assert _pass(room_server, wave, 0, {"S1": [], "S2": []}, state="listening").json()["action"] == "receive"
    assert room_server["recorder"].spawned.wait(timeout=5)
    piece = _pass(room_server, wave, 1, {"S1": [[0, 9]]}, durable_more=True).json()
    assert piece == {"ok": True, "action": "wait", "durable_seq": 2} and _ledger(ctx, "S1") == [(0, 9)]
    mark = ctx.chunk._wave.marks[(PART, CLONER1)]                                 # noqa: SLF001
    assert mark.state == "listening", "a piece of a report was taken for the machine's report"
    last = _pass(room_server, wave, 2, {"S1": [], "S2": [[0, 9]]}).json()
    assert last["durable_seq"] == 3 and ctx.chunk._wave.marks[(PART, CLONER1)].state == "ended"   # noqa: SLF001
    assert _ledger(ctx, "S2") == [(0, 9)]
    ctx.chunk.stop(wave)


def test_the_progress_report_is_numbered_the_same_way_on_a_channel_of_its_own(room_server):
    ctx = room_server["ctx"]
    wave = _wave(room_server)

    def progress(seq, ranges):
        return _progress(room_server, wave, seq, ranges)
    assert "durable_seq" not in progress(None, None)                              # the usual report: no ledger, no number
    assert progress(0, [[0, 3]])["durable_seq"] == 1 and _ledger(ctx, "S1") == [(0, 3)]
    assert progress(1, [[4, 5]])["durable_seq"] == 2 and _ledger(ctx, "S1") == [(0, 5)]
    gap = progress(1, [[8, 9]])                                                   # its last answer was lost
    assert gap["durable_full"] is True and gap["durable_seq"] == 2 and gap["ok"] is True
    assert progress(2, [[0, 5], [8, 9]])["durable_seq"] == 3 and _ledger(ctx, "S1") == [(0, 5), (8, 9)]
    # the pass channel counts alone: the same machine, the same round, its own 0
    assert _pass(room_server, wave, 0, {"S1": [], "S2": []}, state="listening").json()["durable_seq"] == 1
    ctx.chunk.stop(wave)


def test_the_number_moves_only_on_a_report_the_ledger_took(room_server, monkeypatch):
    """The seam stage 2 found (13/11). A cumulative list repaired itself in the next report; a delta
    does not: if the number moved on a report the ledger did not write, those ranges would never be
    sent again and the disk would look emptier than it is. So a write that FAILED is simply not
    acknowledged -- the agent sends again what it has since its last acknowledgement -- and a refusal
    that sending again cannot change is, so that nothing is repeated every two seconds for nothing."""
    ctx = room_server["ctx"]
    wave = _wave(room_server)
    assert _progress(room_server, wave, 0, [[0, 3]])["durable_seq"] == 1

    failed = _locked_once(monkeypatch)
    lost = _progress(room_server, wave, 1, [[6, 7]])
    assert lost["ok"] is True, "a ledger that could not be written failed the progress report itself"
    # no word about the ledger: nothing is acknowledged, and nobody is asked for everything either
    assert "durable_seq" not in lost and "durable_full" not in lost and _ledger(ctx, "S1") == [(0, 3)]
    assert _progress(room_server, wave, 1, [[6, 7]])["durable_seq"] == 2      # what the agent sends again: since its last ack
    assert _ledger(ctx, "S1") == [(0, 3), (6, 7)]

    refused = _progress(room_server, wave, 2, [[0, 5000]])                      # outside the partition: for good
    assert refused["durable_seq"] == 3 and "durable_full" not in refused and _ledger(ctx, "S1") == [(0, 3), (6, 7)]

    # the control message: a write that failed there is no answer at all, and so nothing is acknowledged
    failed.clear()
    with pytest.raises(sqlite3.OperationalError):
        _pass(room_server, wave, 0, {"S1": [[0, 9]]}, state="listening")
    again = _pass(room_server, wave, 0, {"S1": [[0, 9]]}, state="listening").json()
    assert again["durable_seq"] == 1 and _ledger(ctx, "S1") == [(0, 9)]
    ctx.chunk.stop(wave)


# --- the agent's shell alone -------------------------------------------------------------


SHELL = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log"; MAC="$2"; CHUNK_SESSION="$3"; SERVER=http://srv; HTTP_TIMEOUT=5
. agent/lib/core/common.sh; . agent/lib/core/jsonq.sh; . agent/lib/restore/chunkledger.sh; . agent/lib/restore/chunkrecv.sh
disk_serial() { case "$1" in sda) echo S1 ;; sdb) echo S2 ;; esac; }; _wc_mbps() { echo 150; }
'''


class Agent:
    """The agent's ledger functions, run by its shell, a step a process -- the state is its files."""

    def __init__(self, tmp_path, session: str, env: str = ""):
        self.run, self.session, self.env = tmp_path / "run", session, env
        for dev in ("sda", "sdb"):
            (self.run / "targets" / dev / "ledger").mkdir(parents=True, exist_ok=True)
            (self.run / "targets" / dev / "state").write_text("writing\n")
        (tmp_path / "shell.sh").write_text(SHELL, encoding="utf-8", newline="\n")
        self.shell = tmp_path / "shell.sh"

    def sh(self, script: str) -> str:
        done = subprocess.run(
            [BASH, "-c", f'{self.env} . {posix(self.shell)!r} {posix(self.run)!r} {CLONER1} {self.session}; {script}'],
            capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
        assert done.stderr == "", done.stderr
        return done.stdout

    def durable(self, dev: str, lines: list[str], part: int = PART) -> None:
        (self.run / "targets" / dev / "ledger" / f"durable.{part}").write_text(
            "".join(line + "\n" for line in lines), encoding="ascii", newline="\n")

    def acked(self, dev: str, channel: str = "pass", part: int = PART) -> str:
        path = self.run / "targets" / dev / "ledger" / f"acked.{channel}.{part}"
        return path.read_text() if path.exists() else ""

    def body(self, state: str = "ended") -> bytes:
        return self.sh(f"_chunk_body {state} 1 {PART} sda sdb").encode("utf-8")

    def progress_body(self) -> bytes:
        """The periodic report as progress.sh builds it, cut down to the target and its ledger."""
        return self.sh('LEDGER_SESSION="$CHUNK_SESSION"; '
                       'printf \'{"session_id":"%s","mac":"%s","state":"writing","targets":[\' "$CHUNK_SESSION" "$MAC"; '
                       'printf \'{"dev":"sda","bytes_written":10,"bytes_total":100,"state":"writing"%s}\' "$(ledger_json sda)"; '
                       'printf \']%s}\' "$(ledger_seq_json progress)"').encode("utf-8")

    def ack(self, reply: dict | None, channel: str = "pass") -> int:
        """What the agent does with the answer; ``None`` = the send failed (no reply at all)."""
        (self.run / "reply.json").write_text(json.dumps(reply or {}))
        return int(self.sh(f'ledger_ack {channel} "$RUN_DIR/reply.json" "$CHUNK_SESSION" {0 if reply is not None else 7}; echo $?'))


def _ranges_of_progress(body: bytes) -> list | None:
    return (json.loads(body)["targets"][0].get("durable") or [{}])[0].get("ranges")


def _ranges(body: bytes) -> dict:
    return {t["serial"]: (t.get("durable") or [{}])[0].get("ranges") for t in json.loads(body)["targets"]}


@needs_agent
def test_the_agent_sends_what_is_new_and_calls_it_the_servers_only_on_its_word(tmp_path):
    agent = Agent(tmp_path, "ses_1")
    agent.durable("sda", ["0-41", "44-50"])
    first = agent.body()
    assert json.loads(first)["durable_seq"] == 0 and _ranges(first) == {"S1": [[0, 41], [44, 50]], "S2": None}
    assert agent.ack(None) == 1 and _ranges(agent.body()) == _ranges(first)       # no reply: sent again, whole
    assert agent.ack({"ok": True, "action": "wait"}) == 1                         # an answer that says nothing of the ledger
    assert _ranges(agent.body()) == _ranges(first)
    assert agent.ack({"ok": True, "durable_seq": 1}) == 0 and agent.acked("sda") == "0-41\n44-50\n"
    quiet = agent.body()
    assert json.loads(quiet)["durable_seq"] == 1 and _ranges(quiet) == {"S1": None, "S2": None}   # nothing new: no ranges
    agent.ack({"ok": True, "durable_seq": 2})

    agent.durable("sda", ["0-99"])                                                # the runs grew together
    agent.durable("sdb", ["7-7"])
    assert _ranges(agent.body()) == {"S1": [[42, 43], [51, 99]], "S2": [[7, 7]]}
    assert agent.ack({"ok": True, "durable_seq": 3}) == 0 and agent.acked("sda") == "0-99\n"

    # the server lost its place: what was acknowledged is forgotten, its number is taken, everything goes again
    assert agent.ack({"ok": True, "action": "wait", "durable_seq": 9, "durable_full": True}) == 2
    assert agent.acked("sda") == ""
    whole = agent.body()
    assert json.loads(whole)["durable_seq"] == 9 and _ranges(whole) == {"S1": [[0, 99]], "S2": [[7, 7]]}
    # another round on the same boot starts the channel again
    other = Agent(tmp_path, "ses_2")
    assert json.loads(other.body())["durable_seq"] == 0 and _ranges(other.body())["S1"] == [[0, 99]]


@needs_agent
def test_a_long_ledger_goes_in_pieces_and_nothing_is_cut_quietly(tmp_path):
    agent = Agent(tmp_path, "ses_1", env="export LEDGER_PIECE_RANGES=3;")
    agent.durable("sda", ["0-0", "2-2", "4-4", "6-6", "8-8"])
    agent.durable("sdb", ["1-1", "3-3", "5-5"])
    seen, seq = {"S1": [], "S2": []}, 0
    for piece in range(1, 4):
        body = json.loads(agent.body())
        carried = sum(len(r) for r in _ranges(json.dumps(body).encode()).values() if r)
        assert carried <= 3 and body.get("durable_more", False) is (piece < 3), body
        for serial, ranges in _ranges(json.dumps(body).encode()).items():
            seen[serial] += ranges or []
        seq += 1
        assert agent.ack({"ok": True, "action": "wait", "durable_seq": seq}) == (3 if piece < 3 else 0)
    assert seen == {"S1": [[0, 0], [2, 2], [4, 4], [6, 6], [8, 8]], "S2": [[1, 1], [3, 3], [5, 5]]}
    assert agent.acked("sda") == "0-0\n2-2\n4-4\n6-6\n8-8\n" and agent.acked("sdb") == "1-1\n3-3\n5-5\n"
    assert _ranges(agent.body()) == {"S1": None, "S2": None}


PERIODIC = r"""
http_post_json() {   # the n-th report gets the n-th canned reply; none = the send failed (curl's 22)
    _n=$(cat "$RUN_DIR/posts" 2>/dev/null || echo 0); _n=$((_n + 1)); echo "$_n" > "$RUN_DIR/posts"
    cp "$2" "$RUN_DIR/sent.$_n.json"
    [ -f "$RUN_DIR/reply.$_n.json" ] || return 22
    cat "$RUN_DIR/reply.$_n.json"
}
L="$RUN_DIR/targets/sda/ledger"; codes=""
printf '0-41\n' > "$L/durable.3"
progress_send ses_1 "$MAC" http://srv; codes="$codes $?"
progress_send ses_1 "$MAC" http://srv; codes="$codes $?"
printf '0-60\n' > "$L/durable.3"
progress_send ses_1 "$MAC" http://srv; codes="$codes $?"
progress_send ses_1 "$MAC" http://srv; codes="$codes $?"
progress_send ses_1 "$MAC" http://srv; codes="$codes $?"
progress_send ses_1 "$MAC" http://srv; codes="$codes $?"
echo "codes:$codes"
"""


@needs_agent
def test_the_periodic_report_carries_the_ledger_once_and_then_only_what_is_new(tmp_path):
    """`progress_send` itself (progress.sh), every two seconds in the agent: the report that used to
    carry the whole ledger each time. What the server answered decides what the next one carries, and
    the send's own exit code is still the send's."""
    run = tmp_path / "run"
    (run / "targets" / "sda" / "ledger").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing")
    (run / "targets" / "sda" / "bytes.0").write_text("1000")
    (run / "targets" / "sda" / "bytes.1").write_text("500\n")
    (run / "targets" / "sda" / "total").write_text("57982058496")
    (run / "state").write_text("writing")
    replies = {1: {"ok": True, "durable_seq": 1}, 2: {"ok": True},
               # 3: no reply at all -- the report was lost
               4: {"ok": True, "durable_seq": 5, "durable_full": True},
               5: {"ok": True, "durable_seq": 6}, 6: {"ok": True}}
    for n, reply in replies.items():
        (run / f"reply.{n}.json").write_text(json.dumps(reply))
    (tmp_path / "periodic.sh").write_text(PERIODIC, encoding="utf-8", newline="\n")
    done = subprocess.run(
        [BASH, "-c", f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "agent.log")!r}; MAC={CLONER1}; HTTP_TIMEOUT=5; '
                     f'. {posix(AGENT)}/lib/core/common.sh; '
                     + agent_sources("jsonq", "sysinfo", "diskinfo", "progress", "failmark", "datareport", "chunkledger")
                     + f'. {posix(tmp_path / "periodic.sh")!r}'],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert done.stdout.strip().splitlines()[-1] == "codes: 0 0 22 0 0 0", done.stdout + done.stderr
    sent = [json.loads((run / f"sent.{n}.json").read_text(encoding="utf-8")) for n in range(1, 7)]
    carried = [(r.get("durable_seq"), r["targets"][0].get("durable")) for r in sent]
    assert carried == [
        (0, [{"part": 3, "ranges": [[0, 41]]}]),        # the first report: everything, and it is acknowledged
        (None, None),                                   # nothing new: no ranges and no number
        (1, [{"part": 3, "ranges": [[42, 60]]}]),       # only what is new -- and this report is lost
        (1, [{"part": 3, "ranges": [[42, 60]]}]),       # so it goes again; the server answers "everything"
        (5, [{"part": 3, "ranges": [[0, 60]]}]),        # everything, from the server's number
        (None, None)]
    assert all(r["session_id"] == "ses_1" and r["targets"][0]["dev"] == "sda" for r in sent)


# --- the two together ------------------------------------------------------------------


class Link:
    """Carries the agent's requests to the real route and the answers back -- and can lose either."""

    def __init__(self, room_server, agent: Agent):
        self.anon, self.agent, self.sizes, self.answers = room_server["anon"], agent, [], []

    def tell(self, state: str = "ended", lose: str = "") -> int:
        """One request. ``lose``: "request" (it never arrives) or "reply" (the server answered into the void)."""
        body = self.agent.body(state)
        if lose == "request":
            return self.agent.ack(None)
        self.sizes.append(len(body))
        answer = self.anon.post("/api/v1/agent/chunk-pass", content=body, headers=JSON)
        assert answer.status_code == 200, (answer.status_code, answer.text[:300])
        self.answers.append(answer.json())
        return self.agent.ack(None if lose == "reply" else answer.json())

    def until_told(self, state: str = "ended") -> int:
        """As `_chunk_tell`: pieces, and everything again when the server asks, until an answer stands."""
        for n in range(1, 50):
            if self.tell(state) not in (2, 3):
                return n
        raise AssertionError("the report never ended")


@needs_agent
def test_200gb_with_every_other_chunk_missing_on_two_disks_fits_and_adds_up_to_the_whole_report(room_server, tmp_path):
    """The case that was refused whole (413). 47,500 chunks; each disk holds every other one: 23,750
    ranges a disk. No request passes the body cap -- none passes 512KB -- and the union the server ends
    with is the one a single whole report gives."""
    chunks = 47_500
    ctx = room_server["ctx"]
    wave = _wave(room_server, chunks=chunks)
    agent = Agent(tmp_path, wave)
    link = Link(room_server, agent)
    assert link.tell("listening") == 0 and link.answers[-1]["action"] == "receive"
    assert room_server["recorder"].spawned.wait(timeout=5)

    even = [(k, k) for k in range(0, chunks, 2)]
    odd = [(k, k) for k in range(1, chunks, 2)]
    agent.durable("sda", [f"{lo}-{hi}" for lo, hi in even])
    agent.durable("sdb", [f"{lo}-{hi}" for lo, hi in odd])
    requests = link.until_told()
    assert requests >= 2, "47,500 ranges went in one request"
    assert max(link.sizes) < 512 * 1024 < agent_body.MAX_BYTES, link.sizes
    assert all(a.get("action") == "wait" and "durable_full" not in a for a in link.answers[1:-1])   # the pieces
    assert _ledger(ctx, "S1") == even and _ledger(ctx, "S2") == odd

    # the same lists as ONE whole report, straight into a ledger of their own: the same rows
    other = db.connect(tmp_path / "whole.db")
    manifest = ctx.library.get("img_7f3a91")
    for serial, ranges in (("S1", even), ("S2", odd)):
        chunkledger.record(other, round_id=wave, mac=CLONER1, serial=serial, size_bytes=SIZE, image_id="img_7f3a91",
                           manifest=manifest, durable=[{"part": PART, "ranges": [list(r) for r in ranges]}])
        assert chunkledger.of_disk(other, serial, SIZE)[PART]["ranges"] == _ledger(ctx, serial)

    # and once it is all acknowledged, the next question carries no ranges at all
    before = len(link.sizes)
    assert link.tell() == 0 and link.sizes[before] < 1024
    ctx.chunk.stop(wave)


@needs_agent
def test_a_delta_that_was_lost_is_found_out_and_made_good(room_server, tmp_path):
    """Three ways to lose one: the request never arrives, the reply never arrives, the server restarts.
    In each the server ends with everything the disk holds, and the agent knows it."""
    ctx = room_server["ctx"]
    wave = _wave(room_server, chunks=100)
    agent = Agent(tmp_path, wave)
    link = Link(room_server, agent)
    assert link.tell("listening") == 0
    assert room_server["recorder"].spawned.wait(timeout=5)

    agent.durable("sdb", ["0-99"])                                                 # this drawer is whole throughout
    agent.durable("sda", ["0-59"])
    assert link.tell(lose="request") == 1 and _ledger(ctx, "S1") is None           # it never left: nothing anywhere
    assert link.tell() == 0 and _ledger(ctx, "S1") == [(0, 59)] and agent.acked("sda") == "0-59\n"
    assert _ledger(ctx, "S2") == [(0, 99)]

    agent.durable("sda", ["0-59", "70-79"])
    assert link.tell(lose="reply") == 1                                            # the server has it; the agent does not know
    assert _ledger(ctx, "S1") == [(0, 59), (70, 79)] and agent.acked("sda") == "0-59\n"
    agent.durable("sda", ["0-59", "70-89"])
    assert link.tell() == 2 and link.answers[-1]["durable_full"] is True           # found out: the numbers do not meet
    assert link.answers[-1]["action"] == "wait" and _ledger(ctx, "S1") == [(0, 59), (70, 79)]   # nothing decided on it
    assert link.tell() == 0 and _ranges(agent.body()) == {"S1": None, "S2": None}  # everything again, and acknowledged
    assert _ledger(ctx, "S1") == [(0, 59), (70, 89)] and agent.acked("sda") == "0-59\n70-89\n"

    ctx.durable_seq = DurableSeq()                                                 # the server restarted
    agent.durable("sda", ["0-89"])
    assert link.until_told() == 2 and link.answers[-2]["durable_full"] is True
    assert _ledger(ctx, "S1") == [(0, 89)] and agent.acked("sda") == "0-89\n" and agent.acked("sdb") == "0-99\n"
    ctx.chunk.stop(wave)


@needs_agent
def test_a_ledger_write_that_failed_once_mid_round_does_not_shrink_what_the_repair_sees(room_server, tmp_path, monkeypatch):
    """The coordinator's test (13/11): `note_report` fails once in the middle of a round. At the end the
    server holds exactly what the disk holds -- and what the repair stage would send again is exactly
    what the disk lacks, not a chunk more."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = _wave(room_server, chunks=100)
    agent = Agent(tmp_path, wave)

    def report():
        answer = anon.post("/api/v1/agent/progress", content=agent.progress_body(), headers=JSON)
        assert answer.status_code == 200, answer.text
        return answer.json(), agent.ack(answer.json(), "progress")
    agent.durable("sda", ["0-9"])
    assert report()[1] == 0 and _ledger(ctx, "S1") == [(0, 9)]

    _locked_once(monkeypatch)
    agent.durable("sda", ["0-9", "20-29"])
    reply, rc = report()
    assert reply["ok"] is True and "durable_seq" not in reply and rc == 1         # not written: not acknowledged
    assert _ledger(ctx, "S1") == [(0, 9)] and agent.acked("sda", "progress") == "0-9" + NL
    agent.durable("sda", ["0-9", "20-39"])
    assert _ranges_of_progress(agent.progress_body()) == [[20, 39]]               # everything since the last ack
    assert report()[1] == 0

    assert _ledger(ctx, "S1") == [(0, 9), (20, 39)], "the server holds less than the disk does"
    assert agent.acked("sda", "progress") == "0-9" + NL + "20-39" + NL
    assert repair.missing(100, ctx.chunk.ledger.durable(wave, CLONER1, "S1", PART)) == [(10, 19), (40, 99)]
    ctx.chunk.stop(wave)


def _wave_of_an_image_not_verified_yet(room_server, monkeypatch) -> str:
    """A running wave whose image the ledger does not hold as verified: the manifest has no `readback_chunks`.
    The ledger refuses its `durable` for now ("no verified chunk index yet") -- and the manifest it keeps is
    read again every time here, so the test does not wait out its quarter of a minute.

    #3173: this was a udpcast wave, the one engine that went out on an image with no index. The chunk engine
    does not start a wave without a verified index (`ChunkEngine._send`), so the wave goes out on the verified
    fixture image, and the verification is taken from the manifest only once the engine has read it and is
    waiting for the cloner -- what the ledger is asked here does not depend on which engine sends."""
    monkeypatch.setattr(chunkledger, "MANIFEST_TTL_S", 0.0)
    chunkledger._SEEN.clear()                                                     # noqa: SLF001
    ctx = room_server["ctx"]
    wave, _hello = _room_wave(room_server)
    assert wait_for(lambda: (ctx.chunk.status() or {}).get("state") == "sending"), ctx.chunk.status()
    manifest_file = ctx.library.file_path("img_7f3a91", next(
        p["file"] for p in MANIFEST_256["partitions"] if p.get("file"))).parent / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    for part in manifest["partitions"]:
        part.pop("readback_chunks", None)                                         # derived, not yet verified
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return wave


def test_three_reports_the_ledger_cannot_take_yet_and_then_one_it_can(room_server, monkeypatch):
    """The coordinator's test (13/11). `RETRY_LATER` three cycles running: the number does not move and
    nothing is said about the ledger -- no acknowledgement, and no `durable_full` every cycle. The agent
    keeps sending what it has since its last acknowledgement; once the index is verified it is taken,
    and the server holds the truth."""
    ctx = room_server["ctx"]
    wave = _wave_of_an_image_not_verified_yet(room_server, monkeypatch)
    for held in ([[0, 9]], [[0, 19]], [[0, 29]]):                                 # the disk goes on writing
        waiting = _progress(room_server, wave, 0, held)
        assert waiting["ok"] is True and "durable_seq" not in waiting and "durable_full" not in waiting
        assert _ledger(ctx, "S1") is None
    _index_the_image(ctx, chunks=100)                                             # the derivation finished
    assert _progress(room_server, wave, 0, [[0, 39]])["durable_seq"] == 1
    assert _ledger(ctx, "S1") == [(0, 39)]
    assert _progress(room_server, wave, 1, [[40, 49]])["durable_seq"] == 2 and _ledger(ctx, "S1") == [(0, 49)]


@needs_agent
def test_the_agent_keeps_sending_since_its_last_ack_until_the_ledger_takes_it(room_server, tmp_path, monkeypatch):
    """The same three cycles, with the agent's own shell on the other end: each report carries everything
    since the last acknowledgement (there is none yet), none of them is acknowledged, and after the
    fourth the server's union is exactly what the disk holds."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = _wave_of_an_image_not_verified_yet(room_server, monkeypatch)
    agent = Agent(tmp_path, wave)

    def report():
        body = agent.progress_body()
        answer = anon.post("/api/v1/agent/progress", content=body, headers=JSON)
        assert answer.status_code == 200, answer.text
        return json.loads(body), answer.json(), agent.ack(answer.json(), "progress")
    for lines, ranges in ((["0-9"], [[0, 9]]), (["0-9", "12-19"], [[0, 9], [12, 19]]),
                          (["0-9", "12-29"], [[0, 9], [12, 29]])):
        agent.durable("sda", lines)
        sent, reply, rc = report()
        assert sent["durable_seq"] == 0 and sent["targets"][0]["durable"] == [{"part": PART, "ranges": ranges}]
        assert "durable_seq" not in reply and "durable_full" not in reply and rc == 1
        assert _ledger(ctx, "S1") is None and agent.acked("sda", "progress") == ""
    _index_the_image(ctx, chunks=100)
    agent.durable("sda", ["0-9", "12-39"])
    sent, reply, rc = report()
    assert sent["targets"][0]["durable"] == [{"part": PART, "ranges": [[0, 9], [12, 39]]}] and rc == 0
    assert _ledger(ctx, "S1") == [(0, 9), (12, 39)] and agent.acked("sda", "progress") == "0-9" + NL + "12-39" + NL
    assert repair.missing(100, _ledger(ctx, "S1")) == [(10, 11), (40, 99)]

