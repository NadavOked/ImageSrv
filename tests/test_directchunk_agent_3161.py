"""#3161 -- the build machine as the source of a chunk-engine wave: `agent/lib/capture/directchunk.sh`.

`direct_send_run` (directsend.sh) is run as the agent runs it, with the libraries of the real
`$LIB_DIR` and stand-ins for the tools (test_directsend.py's box). What is checked is the
contract with the server's engine:

- the server asked for indexes (`task.direct.engine`): every index -- and a partclone partition's
  head, cut from what read 1 kept -- is PUT before the manifest;
- the wave runs and the hello carries an order: ONE more read of the partition goes through
  `chunkcast send --stdin` with the order's own numbers, and the verdict is told (`chunk-sent`);
- a pass is "sent" only when the sha256 of what was read is the manifest's -- a disk that changed
  between the reads fails at the source, by name, and so does a sender that refused the stream;
- the source is done only when the SERVER says so (`chunk.done`), and stops when the wave failed;
- with the switch off nothing changes: udp-sender, and no index leaves the machine.

**Negative control:** with `directchunk.sh` gone and `directsend.sh` / `capturesink.sh` back at the
base commit, the source sends through udp-sender whatever the hello says: every test here except
`test_with_the_switch_off...` fails on its own assertion (no chunkcast, no index PUT).
"""

from __future__ import annotations

import hashlib
import json
import os

from agentlib import flat_lib
from native import requires_native
from test_agent import BASH, posix
from test_directsend import _answer, _progress, direct_run
from test_timeouts import make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

KEY = "0123456789abcdef"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"       # the round's token, in the hello (`chunk.serve_token`)
ORDER = {"seq": 1, "part": 1, "pass": 1, "kbps": 700000, "write_kbps": 400000,
         "group": "239.83.67.1", "port": 9002, "key": KEY, "chunks": False}

#: chunkcast's stand-in: records its argv, takes the whole stream, and says "done" as the real one does.
CHUNKCAST = (
    '#!/bin/sh\n'
    # #3209: a frame server -- its argv, and whether it was stopped by its pid
    'if [ "$1" = serve ]; then printf "%s\\n" "$@" > "$CC_ARGV.serve"\n'
    # the token file as it is when the server starts (it is gone once the round is over): what it holds, and its mode
    '  while [ $# -gt 0 ]; do [ "$1" != --token-file ] || { cat "$2" > "$CC_ARGV.serve.token"; stat -c %a "$2" > "$CC_ARGV.serve.mode"; }; shift; done\n'
    '  trap \'echo stopped > "$CC_ARGV.serve.term"; exit 0\' TERM\n'
    '  echo serving; sleep 300 & wait "$!"; exit 0; fi\n'
    'printf "%s\\n" "$@" >> "$CC_ARGV"; echo "---" >> "$CC_ARGV"\n'
    'case " $* " in *" --device "*) [ -z "${CC_DEVICE_RC:-}" ] || exit "$CC_DEVICE_RC" ;; esac\n'
    'trap \'echo stopped > "$CC_ARGV.term"; exit 6\' TERM\n'
    'cat > /dev/null\n'
    '[ -z "${CC_HANG:-}" ] || { sleep 60 & wait "$!"; }\n'       # still "sending" long after the stream was read
    '[ "${CC_RC:-0}" != 0 ] || echo "done 1 4096 5"\n'
    'exit "${CC_RC:-0}"\n'
)

#: curl's: records its argv; a repair's chunk list is "5-9"; a chunk-sent body is kept and answered.
CURL = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$@" >> "$CURL_ARGV"; echo "---" >> "$CURL_ARGV"\n'
    'sent=; body=\n'
    'for a; do case "$a" in */chunks/*) echo "5-9" ;; */chunk-sent) sent=1 ;; @*) body=${a#@} ;; esac; done\n'
    '[ -z "$sent" ] || { cat "$body" >> "$CURL_SENT"; [ -z "${CURL_SENT_RC:-}" ] || exit "$CURL_SENT_RC"; echo \'{"ok":true}\'; }\n'
    'exit 0\n'
)

#: chunkcut's, for a partition with a bit map: the stream as it is, and an index that says its first 6 bytes are the head.
CHUNKCUT_PARTCLONE = (
    '#!/bin/sh\n'
    'while [ $# -gt 0 ]; do case "$1" in -i) i=$2; shift ;; esac; shift; done\n'
    'cat\n'
    "printf 'IMAGESRV-IDX 2 partclone 4194304 200 6 0123456789abcdef 0 0 6\\n' > \"$i\"; printf '0123456789abcdef\\n' > \"$i.xxh64\"\n"
)


def _hello(*, state="running", engine=True, order=None, done=False, error=None, wave=True, token=TOKEN):
    answer = _answer(state)
    if engine:
        answer["task"]["direct"]["engine"] = "chunk"
        if wave:
            answer["task"]["direct"]["chunk"] = {"serve_port": 9003, "order": order, "done": done, "error": error,
                                                 **({"serve_token": token} if token is not None else {})}
    return answer


def _run(tmp_path, hellos, *, env=None, extra_stubs=None):
    """`direct_send_run` with directchunk.sh loaded, and one hello answer a call: hellos[0] is the
    first, the last one is repeated."""
    box = tmp_path / "box"
    box.mkdir(parents=True)
    for n, answer in enumerate(hellos, start=1):
        (box / f"hello.{n}.json").write_text(json.dumps(answer), encoding="utf-8")
    (box / "hello.last.json").write_text(json.dumps(hellos[-1]), encoding="utf-8")
    pre = (f". {posix(flat_lib())}/directchunk.sh; "
           'send_hello() { _hn=$(cat "$HELLO_DIR/n" 2>/dev/null || echo 0); _hn=$((_hn + 1)); echo "$_hn" > "$HELLO_DIR/n"; '
           '_hf="$HELLO_DIR/hello.$_hn.json"; [ -f "$_hf" ] || _hf="$HELLO_DIR/hello.last.json"; cp "$_hf" "$RESP"; }; ')
    pre += f". {posix(flat_lib())}/directserve.sh; "
    box, run, out = direct_run(
        tmp_path, env={"HELLO_DIR": posix(box), "CC_ARGV": posix(box / "cc.argv"), "CURL_SENT": posix(box / "sent.jsonl"),
                       "DIRECT_CHUNK_BEAT_S": "1", "SERVER": "http://10.55.0.1:8080", **(env or {})},
        extra_stubs={"chunkcast": CHUNKCAST, "curl": CURL, **(extra_stubs or {})}, pre=pre, timeout=90)
    return box, run, out


def _flags(box, block: int = 0, name: str = "cc.argv") -> dict:
    words = (box / name).read_text("utf-8").split("---")[block].split("\n")
    words = [w for w in words if w]
    out, i = {"_": words[:1]}, 1
    while i < len(words):
        if i + 1 < len(words) and not words[i + 1].startswith("--"):
            out[words[i]] = words[i + 1]
            i += 2
        else:
            out[words[i]] = None
            i += 1
    return out


def _sent(box) -> list[dict]:
    """The verdicts the source told the server (`chunk-sent`), without the "still sending" beats between them."""
    path = box / "sent.jsonl"
    told = [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []
    return [t for t in told if t["state"] != "sending"]


def _beats(box) -> list[dict]:
    path = box / "sent.jsonl"
    told = [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []
    return [t for t in told if t["state"] == "sending"]


def _log(run) -> str:
    return (run / "agent.log").read_text("utf-8", "replace") if (run / "agent.log").exists() else ""


#: read 1 and the offer, the wave runs, one order, every partition whole -- and then the round closes
WAVE = [_hello(state="open", wave=False), _hello(), _hello(order=ORDER), _hello(done=True),
        _hello(state="closed", done=True)]


def test_the_indexes_go_before_the_manifest_and_the_pass_goes_through_chunkcast(tmp_path):
    box, run, out = _run(tmp_path, WAVE)
    assert out.strip().endswith("rc=0"), out + _log(run)
    curl = (box / "curl.argv").read_text("utf-8")
    assert "/api/v1/direct/tsk_715/index/1" in curl
    assert curl.index("/direct/tsk_715/index/1") < curl.index("/direct/tsk_715/manifest")    # the manifest signs it
    assert "/head/" not in curl                             # a raw partition has no head
    flags = _flags(box)
    assert flags["_"] == ["send"] and "--stdin" in flags and "--file" not in flags
    assert flags["--to"] == "239.83.67.1:9002" and flags["--session"] == KEY
    assert flags["--part"] == "1" and flags["--pass"] == "1"
    assert flags["--kbps"] == "700000" and flags["--write-kbps"] == "400000"
    assert flags["--index"] == f"{posix(run)}/idx.1"         # read 1's index, the one the receivers hold
    assert flags["--iface"] == "eth0" and "--chunks-file" not in flags
    assert not (box / "udp.argv").exists()                  # udp-sender never ran
    assert (box / "pcl.count").read_text().strip() == "2"   # read 1, and one read for the one pass
    assert _sent(box) == [{"seq": 1, "state": "done", "rc": 0, "error": ""}]
    assert _beats(box) and all(b["seq"] == 1 for b in _beats(box))     # the server heard it while it ran
    report = _progress(box)
    assert report["state"] == "sending" and report["targets"][0]["state"] != "failed"


def test_the_source_is_done_only_when_the_server_says_every_partition_is_whole(tmp_path):
    """After its pass the server may still be repairing with the receivers: no order and no `done`
    is "wait", not "finished". Here the third answer after the pass says done."""
    waiting = _hello()
    box, run, out = _run(tmp_path, [*WAVE[:3], waiting, waiting, *WAVE[3:]])
    assert out.strip().endswith("rc=0"), out + _log(run)
    assert int((box / "n").read_text()) >= 7                # it asked again until the server said so
    assert (box / "cc.argv").read_text("utf-8").count("---") == 1   # and the order was carried out once


def test_a_disk_that_changed_between_the_reads_fails_at_the_source_by_name(tmp_path):
    box, run, out = _run(tmp_path, WAVE, env={"PCL_SECOND": "B"})
    assert out.strip().endswith("rc=1"), out + _log(run)
    assert "הבייטים שנקראו שונים מהמניפסט" in _progress(box)["targets"][0]["error"]
    told = _sent(box)[-1]
    assert told["state"] == "failed" and told["seq"] == 1 and "שונים מהמניפסט" in told["error"]
    want = hashlib.sha256(b"A" * 4096).hexdigest()
    assert (run / "dchunk.1.sha").read_text().split()[0] != want     # the evidence the verdict was read from


def test_a_sender_that_refuses_the_stream_fails_the_pass_with_its_code(tmp_path):
    box, run, out = _run(tmp_path, WAVE, env={"CC_RC": "9"})
    assert out.strip().endswith("rc=1"), out + _log(run)
    assert "הדיסק השתנה בין הקריאות" in _progress(box)["targets"][0]["error"]
    told = _sent(box)[-1]
    assert told["state"] == "failed" and told["rc"] == 9


def test_a_sender_that_exits_zero_without_saying_done_is_not_a_sent_pass(tmp_path):
    quiet = '#!/bin/sh\nprintf "%s\\n" "$@" >> "$CC_ARGV"; echo "---" >> "$CC_ARGV"\ncat > /dev/null\nexit 0\n'
    box, run, out = _run(tmp_path, WAVE, extra_stubs={"chunkcast": quiet})
    assert out.strip().endswith("rc=1"), out + _log(run)
    assert "אין ראיה שנשלח" in _progress(box)["targets"][0]["error"]
    assert _sent(box)[-1]["state"] == "failed"


REPAIR = [WAVE[0], WAVE[1], _hello(order={**ORDER, "pass": 2, "chunks": True}), *WAVE[3:]]


def test_a_repair_order_reads_only_the_servers_chunks_and_not_the_partition_again(tmp_path):
    """#3209: a repair used to be one more read of the whole partition, for whatever was missing."""
    box, run, out = _run(tmp_path, REPAIR)
    assert out.strip().endswith("rc=0"), out + _log(run)
    flags = _flags(box)
    assert flags["--pass"] == "2" and flags["--chunks-file"] == f"{posix(run)}/dchunk.list"
    assert (run / "dchunk.list").read_text().strip() == "5-9"
    assert "/api/v1/direct/tsk_715/chunks/1" in (box / "curl.argv").read_text("utf-8")
    assert flags["--device"].endswith("/sda1") and flags["--level"] == "3" and "--stdin" not in flags
    assert flags["--index"] == f"{posix(run)}/idx.1" and flags["--to"] == "239.83.67.1:9002"
    assert (box / "pcl.count").read_text().strip() == "1"   # read 1 only: the disk was not read through again
    assert (box / "cc.argv").read_text("utf-8").count("---") == 1
    assert _sent(box) == [{"seq": 1, "state": "done", "rc": 0, "error": ""}]


def test_a_targeted_repair_that_does_not_go_out_is_said_and_the_whole_partition_is_read_instead(tmp_path):
    box, run, out = _run(tmp_path, REPAIR, env={"CC_DEVICE_RC": "9"})
    assert out.strip().endswith("rc=0"), out + _log(run)
    assert "--device" in _flags(box, 0) and "--stdin" in _flags(box, 1)     # the attempt, and then the read
    assert _flags(box, 1)["--chunks-file"] == f"{posix(run)}/dchunk.list"
    assert (box / "pcl.count").read_text().strip() == "2"
    assert "the targeted repair did not go out (chunkcast rc=9)" in _log(run)
    assert _sent(box) == [{"seq": 1, "state": "done", "rc": 0, "error": ""}]


def test_the_frame_servers_run_for_the_wave_answer_the_server_only_and_are_stopped_with_it(tmp_path):
    """#3209: a drawer pushed back in, or a machine that reported late, fetches single chunks -- and the
    image server relays that to this machine. One server a partition, until the round closes."""
    box, run, out = _run(tmp_path, WAVE)
    assert out.strip().endswith("rc=0"), out + _log(run)
    serve = _flags(box, 0, "cc.argv.serve")
    assert serve["_"] == ["serve"] and serve["--listen"] == "0.0.0.0:9004"   # the port base + partition 1
    assert serve["--allow"] == "10.55.0.1"                  # the image server's address: one more layer, not the only one
    assert serve["--index"] == f"{posix(run)}/idx.1" and serve["--device"].endswith("/sda1")
    assert serve["--level"] == "3"
    assert (box / "cc.argv.serve.term").read_text().strip() == "stopped"     # by the pid it was started with
    assert not (run / "dserve.pids").exists() and not (run / "dserve.token").exists()


def test_the_frame_servers_get_the_rounds_token_in_a_file_and_never_in_argv_or_the_log(tmp_path):
    """The review of #3216: what lets a request through is the round's token, which the server put in the hello.
    `ps` shows argv, and the log is read by whoever debugs: the token is in neither."""
    box, run, out = _run(tmp_path, WAVE)
    assert out.strip().endswith("rc=0"), out + _log(run)
    serve = _flags(box, 0, "cc.argv.serve")
    assert serve["--token-file"] == f"{posix(run)}/dserve.token"
    assert (box / "cc.argv.serve.token").read_text().strip() == TOKEN        # what the server put in the hello, as it is
    if os.name == "posix":
        assert (box / "cc.argv.serve.mode").read_text().strip() == "600"     # nobody else on the machine reads it
    for where in (box / "cc.argv.serve", box / "cc.argv"):
        assert TOKEN not in where.read_text("utf-8"), where
    assert TOKEN not in _log(run) and TOKEN not in out


def test_a_server_known_by_a_name_still_gets_frame_servers_that_ask_for_the_token(tmp_path):
    """The review of #3216: with a name for an address, the frame servers answered ANY peer (--allow was left out). Now
    the token is what is asked, whatever the address is -- so the name no longer leaves the build machine open."""
    box, run, out = _run(tmp_path, WAVE, env={"SERVER": "http://imagesrv.lab:8080"})
    assert out.strip().endswith("rc=0"), out + _log(run)
    serve = _flags(box, 0, "cc.argv.serve")
    assert "--allow" not in serve and serve["--token-file"] == f"{posix(run)}/dserve.token"
    assert (box / "cc.argv.serve.token").read_text().strip() == TOKEN
    assert "answer any peer" not in _log(run)


def test_no_token_in_the_hello_means_no_frame_server_and_it_is_said(tmp_path):
    """Fail closed (principle 5): a server that sent no token (an older one) leaves nothing listening on the disk; the
    completions that need it then fail at the server, by name -- the wave itself is not touched."""
    untokened = [_hello(state="open", wave=False), _hello(token=None), _hello(order=ORDER, token=None),
                 _hello(done=True, token=None), _hello(state="closed", done=True, token=None)]
    box, run, out = _run(tmp_path, untokened)
    assert out.strip().endswith("rc=0"), out + _log(run)
    assert not (box / "cc.argv.serve").exists()             # `chunkcast serve` never ran
    assert "sent no token for the frame servers" in _log(run)
    assert not (run / "dserve.token").exists()


def test_the_source_stays_until_the_round_closes_and_not_only_until_its_last_pass(tmp_path):
    whole = _hello(done=True)
    box, run, out = _run(tmp_path, [*WAVE[:3], whole, whole, whole, _hello(state="closed", done=True)])
    assert out.strip().endswith("rc=0"), out + _log(run)
    assert int((box / "n").read_text()) >= 7                # it went on asking while the round ran
    assert "served until the round closes" in _log(run) and "the round closed" in _log(run)


def test_a_wave_the_server_failed_stops_the_source_with_the_servers_reason(tmp_path):
    failed = _hello(state="failed", error="מחשב בגל מריץ סוכן ישן")
    box, run, out = _run(tmp_path, [WAVE[0], WAVE[1], failed])
    assert out.strip().endswith("rc=1"), out + _log(run)
    assert "השרת עצר את ההפצה הישירה: מחשב בגל מריץ סוכן ישן" in _progress(box)["targets"][0]["error"]
    assert not (box / "cc.argv").exists()                   # nothing was read for nobody


def test_a_round_that_closed_in_the_middle_of_a_pass_stops_the_sender_at_once(tmp_path):
    """The operator stopped the round while the source was sending: the server refuses the pass's beat
    (404 -- the task is closed), and the sender is stopped by its own pid. Without that it would go on
    multicasting the rest of the partition to nobody -- on the network the room shares."""
    box, run, out = _run(tmp_path, WAVE, env={"CC_HANG": "1", "CURL_SENT_RC": "22"})
    assert out.strip().endswith("rc=1"), out + _log(run)
    assert "השרת כבר אינו מחכה למעבר הזה" in _progress(box)["targets"][0]["error"]
    assert (box / "cc.argv.term").read_text().strip() == "stopped"       # SIGTERM reached chunkcast itself


def test_a_wave_refused_before_it_started_does_not_leave_the_source_waiting(tmp_path):
    """The switch is on and the server refused the wave (`failed`, before `running`): the source
    used to wait for `running` or `closed` only -- for ever."""
    box, run, out = _run(tmp_path, [WAVE[0], _hello(state="failed", wave=False)])
    assert out.strip().endswith("rc=1"), out + _log(run)
    assert "הגל לא התחיל" in _progress(box)["targets"][0]["error"]
    assert not (box / "cc.argv").exists() and not (box / "udp.argv").exists()


def test_with_the_switch_off_the_source_is_udpcast_and_no_index_leaves_the_machine(tmp_path):
    off = _hello(engine=False)
    box, run, out = _run(tmp_path, [off])
    assert out.strip().endswith("rc=0"), out + _log(run)
    assert (box / "udp.argv").exists() and not (box / "cc.argv").exists()
    curl = (box / "curl.argv").read_text("utf-8")
    assert "/index/" not in curl and "/head/" not in curl and "/manifest" in curl


def test_the_head_of_a_partclone_partition_is_cut_from_what_read_1_kept(tmp_path):
    box, run, out = _run(tmp_path, WAVE, extra_stubs={"chunkcut": CHUNKCUT_PARTCLONE})
    assert out.strip().endswith("rc=0"), out + _log(run)
    curl = (box / "curl.argv").read_text("utf-8")
    assert "/api/v1/direct/tsk_715/head/1" in curl, out + _log(run)
    assert curl.index("/direct/tsk_715/index/1") < curl.index("/direct/tsk_715/head/1") < curl.index("/direct/tsk_715/manifest")
    assert not list(run.glob("dhead.*"))                    # what was kept in RAM is gone once it was offered


def test_a_head_that_read_1_did_not_keep_whole_refuses_the_send_before_the_manifest(tmp_path):
    box, run, out = _run(tmp_path, WAVE, env={"DIRECT_HEAD_KEEP": "3"}, extra_stubs={"chunkcut": CHUNKCUT_PARTCLONE})
    assert out.strip().endswith("rc=1"), out + _log(run)
    assert "לא נשמרה כולה בקריאה הראשונה" in _progress(box)["targets"][0]["error"]
    curl = (box / "curl.argv").read_text("utf-8")
    assert "/head/" not in curl and "/manifest" not in curl
    assert not (box / "cc.argv").exists() and not (box / "udp.argv").exists()


def test_read_1_keeps_the_beginning_of_the_stream_and_drains_the_rest(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    out = run_sh(
        make_stubs(tmp_path / "stubs", {})
        + f"export RUN_DIR={posix(run)!r} DIRECT_HEAD_KEEP=5; . {posix(flat_lib())}/directchunk.sh; "
        f"mkfifo {posix(run)}/f; ( printf 'headANDtherestofthestream' > {posix(run)}/f ) & "
        f"direct_head_keep {posix(run)}/f p1.esp.pcl.zst; echo rc=$?; wait")
    assert out.strip().endswith("rc=0"), out
    assert (run / "dhead.p1.esp.pcl.zst").read_bytes() == b"headA"
