"""#2533, stages 2 + 3 together, end to end, without metal.

A real server (`python -m server.main`, its own process, its real identity gate), the
real `chunkcast` on both sides, and the agent's real `chunkrecv.sh` and `chunkgate.sh`:

1. An image_format 2 image is CAPTURED -- uploaded through the capture API as a build
   machine does it -- and the server derives and verifies its chunk index (stage 2).
2. With the switch on, a cloning-room round distributes it: the server's own
   `chunkcast send`, the cloners' `chunkcast recv` run by `chunk_partition_drawers`.
3. Packets are lost on purpose in the first pass. What is missing is repaired -- over
   HTTP Range when one machine lacks it, by a second multicast pass when two do --
   from what the ledger (`chunk_ledger`) says, not from the engine's memory.
4. Every target's SHA-256 is the source partition's.

What stands in for hardware, and only that: a "disk" is a file (CHUNKCAST_ALLOW_FILE,
a test-only switch), so the agent's hardware helpers (`disk_serial`, `partition_node`,
`assert_target`, `ntfs_boot_target`...) are stubs, and the partition table, the write
check and the read-back that surround this function on a machine are not run. The
stream goes sender -> a relay on loopback (where packets are lost) -> the real
multicast group with TTL 0, so nothing leaves the host.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import warnings
from pathlib import Path

import pytest

import chunkcast_rig as C
import hygiene
from native import requires_native
from test_agent import BASH, posix

from tools.e2e import harness as H      # the simulation's client, hello and constants

REPO = Path(__file__).resolve().parent.parent

pytestmark = requires_native("gcc", "zstd", "jq", "curl", ("bash", BASH), posix=True,
                             why="a real server, the real chunkcast and the agent's real shell")

BUILD, CLONER_A, CLONER_B = "aa:bb:cc:00:e2:10", "aa:bb:cc:00:e2:21", "aa:bb:cc:00:e2:22"
GROUP = "239.84.82.1"
LOST = {1, 2}                       # chunks of partition 3 that lose a packet in the first pass


def _tcp_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: #3184: uvicorn's access line is written for exceptions only (4xx/5xx, writes, slow), so a successful range GET leaves no
#: line. This file's evidence IS that line (which connection asked, how many times), so the server it starts logs every request,
#: as it did before #3184. The same `server.main`, run in-process; only the filter's verdict is replaced.
LOG_EVERY_REQUEST = ("from server.system import accesslog; accesslog.AccessFilter.filter = lambda self, record: True; "
                     "import runpy; runpy.run_module('server.main', run_name='__main__', alter_sys=True)")


class Stack:
    """The server, the relay and the image -- one per test."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        (tmp / "bin").mkdir()
        self.bin = C.build_binaries(tmp / "bin")
        self.esp = C.build(tmp / "esp", self.bin / "fanout", chunks=0.3, noisy=(0,), name="p1.esp.pcl.zst")
        self.win = C.build(tmp / "win", self.bin / "fanout", chunks=3.5, noisy=(1,), name="p3.windows.pcl.zst", salt=7)
        self.port, self.console_port, self.portbase = _tcp_port(), _tcp_port(), C.free_port()
        # #3580: the agent API is TLS-only; the console stays plain on loopback
        self.base, self.console = f"https://127.0.0.1:{self.port}", f"http://127.0.0.1:{self.console_port}"
        self.passes: dict[int, int] = {}            # partition -> the pass the sender last announced
        self.agents: list[subprocess.Popen] = []    # every agent's shell, a process group each

        #: #3522: (partition, pass) -> the chunks in the order the sender put them on the wire (the first packet of each)
        self.wire: dict[tuple[int, int | None], list[int]] = {}

        def rule(receiver, kind, chunk, seq, packet):
            part = int.from_bytes(packet[6:8], "big")
            if kind == C.ANNOUNCE:
                self.passes[part] = int.from_bytes(packet[16:20], "big")
            if kind == C.DATA and seq == 0:
                self.wire.setdefault((part, self.passes.get(part)), []).append(chunk)
            lose = kind == C.DATA and part == 3 and self.passes.get(part) == 1 and chunk in LOST and seq == 0
            return [] if lose else [packet]

        self.relay = C.Relay(1, rule)
        self.relay.host, self.relay.ports = GROUP, [self.portbase + 2]
        self.relay.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)   # nothing leaves this host
        self.relay.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.relay.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))

        shim = tmp / "shim"
        shim.mkdir()
        (shim / "chunkcast").write_text(
            "#!/bin/sh\n"
            "# The server's own `chunkcast send`, as the engine wrote it -- sent to the test's relay\n"
            "# instead of straight to the group (the last --to wins), with a short lead and tail, and at\n"
            "# 10MB/s on the wire (the last --kbps wins): the relay is a Python thread, and what it cannot\n"
            "# keep up with is lost for every receiver at once -- a loss of this rig, not of the engine.\n"
            f'if [ "$1" = send ]; then echo "$*" >> "{tmp}/sends.log"; '      # #3522: what the engine asked, a line a pass
            f'exec "{self.bin}/chunkcast" "$@" --to "$CHUNK_E2E_RELAY" --lead-ms 300 --tail-ms 600'
            ' --kbps 80000; fi\n'
            f'exec "{self.bin}/chunkcast" "$@"\n', encoding="ascii", newline="\n")
        (shim / "chunkcast").chmod(0o755)

        leases = tmp / "dnsmasq.leases"
        leases.write_text("".join(f"1789000000 {mac} 127.0.0.1 e2e-{i} 01:{mac}\n"
                                  for i, mac in enumerate((BUILD, CLONER_A, CLONER_B))), encoding="ascii")
        self.data, self.images = tmp / "data", tmp / "images"
        self.images.mkdir()
        # the identity the server will serve (it reuses it); the client and the agent's shell trust exactly it
        from server.agent import tls
        identity, _ = tls.ensure_identity(self.data)
        H.trust_agent(self.base, identity)
        (tmp / "agent-name").write_text(identity.name + "\n", encoding="ascii", newline="\n")
        self.trust_env = {"SERVER_CA_FILE": identity.ca_path.as_posix(),
                          "AGENT_NAME_FILE": (tmp / "agent-name").as_posix(), "NO_PROXY": "*", "no_proxy": "*"}
        self.log = (tmp / "server.log").open("wb")
        self.server = subprocess.Popen(
            [sys.executable, "-c", LOG_EVERY_REQUEST, "--server-url", self.base, "--data-dir", str(self.data),
             "--images", str(self.images), "--host", "127.0.0.1", "--port", str(self.port),
             "--console-port", str(self.console_port), "--console-tls", "off", "--boot-port", str(_tcp_port()),
             "--sender-portbase", str(self.portbase), "--dhcp-leases", str(leases)],
            cwd=str(REPO), stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env={**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
                 "CHUNK_E2E_RELAY": self.relay.addr})
        self._wait(lambda: self._up(), "the server answers")
        from server.console import users
        from server.db import connect
        conn = connect(self.data / "imagesrv.db")
        users.create(conn, H.ADMIN["username"], H.ADMIN["password"], "admin", by="e2e")
        conn.close()
        self.admin = H.Client(self.base, self.console)
        assert self.admin.json("POST", "/api/console/login", H.ADMIN)[0] == 200
        self.agent = H.Client(self.base, self.console)

    def _up(self) -> bool:
        try:
            H._open(self.base + "/api/v1/health", timeout=2).read()
        except urllib.error.HTTPError:
            return True                             # it answered: any HTTP status is a server
        except OSError:
            return False
        return True

    def _wait(self, check, what: str, seconds: float = 60):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            assert self.server.poll() is None, f"the server exited ({self.server.returncode}) -- {self.tail()}"
            got = check()
            if got:
                return got
            time.sleep(0.2)
        raise AssertionError(f"timed out: {what}\n{self.tail()}")

    def tail(self, lines: int = 40) -> str:
        self.log.flush()
        return "\n".join((self.tmp / "server.log").read_text(errors="replace").splitlines()[-lines:])

    def machine(self, mac: str, name: str, group: str) -> None:
        status, body = self.admin.json("POST", "/api/console/machines", {"mac": mac, "name": name, "group_id": group})
        assert status == 200, body

    def hello(self, mac: str, disks: list[dict], **more) -> dict:
        status, body = self.agent.json("POST", "/api/v1/agent/hello", {
            "schema": 1, "mac": mac, "all_macs": [mac], "ip": "127.0.0.1", "hostname_current": None,
            "uuid": "E2E", "firmware": "uefi", "secure_boot": True, "agent_version": "0.1.0",
            "memory_bytes": 8 << 30, "disks": disks, **more})
        assert status == 200, body
        return body

    def capture(self) -> str:
        """The image enters the library the way a build machine sends it: task, token, files, manifest."""
        self.machine(BUILD, "BUILD-E2E", "grp_BUILD")
        self.hello(BUILD, [H.disk("sda", H.GB256, "BLD-E2E")])       # the build machine is up, with its disk
        status, task = self.admin.json("POST", "/api/console/tasks/capture", {
            "mac": BUILD, "disk": "sda", "name": "Chunk E2E", "description": "format 2", "folder": "E2E"})
        assert status == 200, task
        answer = self.hello(BUILD, [H.disk("sda", H.GB256, "BLD-E2E")])
        token = (answer.get("task") or {}).get("token")
        assert token and answer["task"]["id"] == task["id"], answer
        up = H.Client(self.base, self.console)
        up.headers["X-Imagesrv-Task-Token"] = token
        files = {img.file.name: img.file.read_bytes() for img in (self.esp, self.win)}
        for name, payload in files.items():
            status, body = up.request("PUT", f"/api/v1/capture/{task['id']}/files/{name}", body=payload,
                                      ctype="application/octet-stream", raw=True)
            assert status == 200, (name, status, body[:300])
        manifest = {
            "schema": 1, "image_format": 2, "family": 256, "source_disk_bytes": H.GB256,
            "min_target_bytes": H.GB256, "scheme": "gpt", "sector_size": 512,
            "disk_guid": "B8A3C1D2-4E5F-6A7B-8C9D-0E1F2A3B4C5D",
            "partitions": [
                {"index": 1, "type_guid": H.ESP_GUID, "role": "esp", "fs": "vfat", "start_sector": 2048,
                 "size_bytes": 104857600, "used_bytes": len(self.esp.data), "file": self.esp.file.name,
                 "sha256": _sha(files[self.esp.file.name]), "index_xxh64": self.esp.index_xxh, "expandable": False},
                {"index": 3, "type_guid": H.WINDOWS_GUID, "role": "windows", "fs": "ntfs", "start_sector": 1085440,
                 "size_bytes": H.GB256 - 1085440 * 512, "used_bytes": len(self.win.data), "file": self.win.file.name,
                 "sha256": _sha(files[self.win.file.name]), "index_xxh64": self.win.index_xxh, "expandable": True}],
            "total_compressed_bytes": sum(map(len, files.values())), "compression": "zstd-3"}
        status, body = up.json("PUT", f"/api/v1/capture/{task['id']}/manifest", manifest)
        assert status == 202, body

        def state():
            rows = self.admin.json("GET", "/api/console/tasks")[1]
            return next(t for t in rows if t["id"] == task["id"])["state"]
        self._wait(lambda: state() != "verifying", "the capture's verification ended")
        assert state() == "done", (state(), self.tail())
        return task["image_id"]

    def index(self, image_id: str, part: int) -> str | None:
        status, body = self.agent.request("GET", f"/api/v1/images/{image_id}/index/{part}")
        return body.decode("ascii") if status == 200 else None

    def close(self) -> None:
        for agent in self.agents:                   # a test that failed half way: no receiver stays behind (#1736)
            hygiene.kill_group(agent.pid)
            agent.wait(10)
        self.server.terminate()
        try:
            self.server.wait(10)
        except subprocess.TimeoutExpired:
            self.server.kill()
        self.relay.close()
        self.log.close()


@pytest.fixture()
def stack(tmp_path):
    s = Stack(tmp_path)
    try:
        yield s
    finally:
        s.close()


AGENT = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log" CHUNKCAST_ALLOW_FILE=1
PATH="$BIN:$PATH"; IMAGESRV_SERVER="$2"; IMAGE="$3"; MAC="$4"; DISKS="$5"
IFACE=lo            # this host's card: the relay sends the group on loopback (TTL 0), and a join without a card takes the default route's
. agent/lib/core/common.sh; . agent/lib/core/jsonq.sh; . agent/lib/core/waits.sh; . agent/lib/restore/verdict.sh
. agent/lib/core/tls.sh; tls_config || { echo "tls_config: $TLS_WHY" >&2; exit 5; }   # #3580: $SERVER is the certificate name, curl trusts the site CA
. agent/lib/restore/chunkgate.sh; . agent/lib/restore/chunkledger.sh; . agent/lib/restore/chunkrecv.sh
. agent/lib/restore/chunkfetch.sh; . agent/lib/restore/chunkwatch.sh
[ ! -f agent/lib/restore/chunkb3.sh ] || . agent/lib/restore/chunkb3.sh   # #3332: the partition's BLAKE3 list
[ ! -f agent/lib/restore/chunkrc.sh ] || . agent/lib/restore/chunkrc.sh   # #3259: what the receiver's exit codes mean
[ ! -f agent/lib/restore/chunkloss.sh ] || . agent/lib/restore/chunkloss.sh   # #3064 G6/G7: what the receiver lost
WAIT_STREAM_STALL_S=30; WAIT_DRAWER_S=30; STREAM_START_CEILING=90; CHUNK_ASK_POLL_S=1
trace() { echo "$*" >> "$RUN_DIR/trace"; }
# --- the hardware this host does not have; nothing else is replaced ---
disk_serial() { cat "$RUN_DIR/disks/$1.serial"; }
partition_node() { echo "$RUN_DIR/disks/$1.p$2"; }
export SYSROOT="$RUN_DIR/sysroot"; dmesg() { :; }   # the pass's watch (chunkwatch.sh) asks this host's kernel about ITS disks otherwise
event_beep() { trace "BEEP $1"; }
node_is_block() { [ -f "$1" ]; }
assert_target() { return 0; }
ntfs_boot_target() { return 0; }
_wc_mbps() { echo 100; }
target_counter() { :; }; next_stream_ceiling() { :; }; cancel_pending() { :; }
round_closed() { return 1; }; round_closed_why() { echo "the round was closed"; }
fail_written_target() { echo failed > "$RUN_DIR/targets/$1/state"; trace "FAIL $1 $2"; }
target_partition_done() { trace "DONE $1 p$CHUNK_E2E_PART"; }
for d in $DISKS; do mkdir -p "$RUN_DIR/targets/$d"; echo writing > "$RUN_DIR/targets/$d/state"; done
chunk_gate "$RUN_DIR/resp.json" "$RUN_DIR/manifest.json" || { trace "GATE $CHUNK_REFUSAL"; exit 3; }
trace "MODE $STREAM_MODE"
jq -r '.partitions[] | select(.file != null) | "\(.index) \(.fs) \(.file)"' "$RUN_DIR/manifest.json" > "$RUN_DIR/plan"
while read -r idx fs file <&5; do
    CHUNK_E2E_PART=$idx
    # shellcheck disable=SC2086
    chunk_partition_drawers "$SERVER" "$IMAGE" "$idx" "$fs" "$file" "$RUN_DIR/manifest.json" $DISKS \
        || { trace "PARTITION $idx failed"; exit 4; }
done 5< "$RUN_DIR/plan"
trace OK
'''


def _open(stack: Stack, cloners: dict[str, list[str]]):
    """Captures and opens a room round on the chunk engine, up to a wave that runs for every cloner.
    Returns (image, wave, disks, the hello answers, the manifest as the agent fetched it)."""
    image = stack.capture()
    # The server derives the index from the uploaded file, in the background, and says so in the manifest.
    # The index route itself is for members of an open round of this image only (#2657): it is read below,
    # once the wave runs, from the address the receivers fetch it from.
    def derived():
        status, manifest = stack.admin.json("GET", f"/api/console/images/{image}")
        assert status == 200, manifest
        parts = {p["index"]: p for p in manifest["partitions"]}
        assert not any(p.get("index_error") for p in parts.values()), parts
        return all(isinstance(parts[n].get("readback_chunks"), int) for n in (1, 3))
    stack._wait(derived, "the server derived the index of both partitions")
    assert stack.index(image, 3) is None, "the index was served to a machine that is in no round of this image"
    disks = {}
    for n, (mac, serials) in enumerate(cloners.items(), start=1):
        stack.machine(mac, f"{n:02d}", "grp_CLONERS")
        disks[mac] = [H.disk(f"sd{chr(97 + i)}", H.GB256, serial, port=i + 1) for i, serial in enumerate(serials)]
        stack.hello(mac, disks[mac], engines=["udpcast", "chunk"])
    status, opened = stack.admin.json("POST", "/api/console/room", {
        "image_id": image, "target_drives": sum(map(len, cloners.values())),
        "target_slots": [{"mac": mac, "ports": [d["port"] for d in disks[mac]]} for mac in cloners]})
    assert status == 200, opened

    def running():
        answers = {mac: stack.hello(mac, disks[mac], engines=["udpcast", "chunk"]) for mac in cloners}
        ok = all((a.get("session") or {}).get("state") == "running" for a in answers.values())
        return answers if ok else None
    answers = stack._wait(running, "the wave is running for every cloner")
    wave = answers[next(iter(cloners))]["session"]["id"]
    assert all(a["session"]["engine"] == "chunk" and a["session"]["chunk"]["port"] == stack.portbase + 2
               for a in answers.values()), answers
    manifest = stack.agent.request("GET", f"/api/v1/images/{image}/manifest")[1]
    for img, part in ((stack.esp, 1), (stack.win, 3)):
        assert stack.index(image, part) == img.idx.read_text(encoding="ascii"),             f"the index the server serves for partition {part} is not the one the capture cut"

    return image, wave, disks, answers, manifest


def _round(stack: Stack, cloners: dict[str, list[str]]):
    """`_open`, and then the agents run. Returns (image, wave, runs)."""
    image, wave, disks, answers, manifest = _open(stack, cloners)

    runs = {}
    for mac in cloners:
        run = stack.tmp / f"agent-{mac[-2:]}"
        (run / "disks").mkdir(parents=True)
        (run / "resp.json").write_text(json.dumps(answers[mac]), encoding="utf-8")
        (run / "manifest.json").write_bytes(manifest)
        for d in disks[mac]:
            (run / "disks" / f"{d['dev']}.serial").write_text(d["serial"], encoding="ascii")
            stack.esp.blank(run / "disks" / f"{d['dev']}.p1")
            stack.win.blank(run / "disks" / f"{d['dev']}.p3")
        (run / "agent.sh").write_text(AGENT, encoding="utf-8", newline="\n")
        with (run / "out").open("wb") as out:
            runs[mac] = (run, subprocess.Popen(
                [BASH, posix(run / "agent.sh"), posix(run), stack.base, image, mac,
                 " ".join(d["dev"] for d in disks[mac])],
                cwd=str(REPO), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True, env={**os.environ, "BIN": str(stack.bin), **stack.trust_env}))
            stack.agents.append(runs[mac][1])
    for mac, (run, process) in runs.items():
        try:
            rc = process.wait(240)
        except subprocess.TimeoutExpired:
            process.kill()
            rc = "timeout"
        trace = (run / "trace").read_text() if (run / "trace").exists() else ""
        assert rc == 0, (f"{mac}: rc={rc}\ntrace:\n{trace}\nagent log:\n"
                         + (run / "agent.log").read_text(errors="replace")[-3000:] + "\nserver:\n" + stack.tail())
        assert trace.splitlines()[0] == "MODE chunk" and trace.splitlines()[-1] == "OK", trace
    return image, wave, {mac: (run, disks[mac]) for mac, (run, _p) in runs.items()}


def _after(stack: Stack, wave: str, runs: dict) -> list[str]:
    """Every target is the source, the ledger says so, and the journal says how it was repaired.

    #3332, the whole chain: the server derived each partition's BLAKE3 list (its `chunkcast b3list`), the manifest
    names it, the agent fetched it from `/b3/<n>`, and every writer of the machine checked against it."""
    for mac, (run, disks) in runs.items():
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        named = {p["index"]: p.get("chunk_b3") for p in manifest["partitions"] if p["index"] in (1, 3)}
        assert all(isinstance(h, str) and len(h) == 64 for h in named.values()), named
        log = (run / "agent.log").read_text(errors="replace")
        for part, img in ((1, stack.esp), (3, stack.win)):
            assert f"partition {part}: BLAKE3 list fetched ({img.n} chunks)" in log, log[-3000:]
            assert (run / "chunk" / f"p{part}.b3").stat().st_size == 32 * img.n
        for d in disks:
            for img, part in ((stack.esp, 1), (stack.win, 3)):
                target = (run / "disks" / f"{d['dev']}.p{part}").read_bytes()
                assert _sha(target) == _sha(img.expected()), f"{mac} {d['dev']} p{part} is not the source"
            assert (run / "targets" / d["dev"] / "ledger" / "durable.3").read_text() == f"0-{stack.win.n - 1}\n"
    stack.server.terminate()
    stack.server.wait(15)
    conn = sqlite3.connect(stack.data / "imagesrv.db")
    conn.row_factory = sqlite3.Row
    for _mac, (_run, disks) in runs.items():
        for d in disks:
            rows = {r["part"]: r for r in conn.execute(
                "SELECT * FROM chunk_ledger WHERE serial = ? AND size_bytes = ?", (d["serial"], d["size_bytes"]))}
            assert set(rows) == {1, 3}, (d["serial"], list(rows))
            assert rows[3]["round_id"] == wave and json.loads(rows[3]["ranges_json"]) == [[0, stack.win.n - 1]]
            assert json.loads(rows[1]["ranges_json"]) == [[0, stack.esp.n - 1]]
    journal = [f"{r['event']} {r['detail']}" for r in conn.execute(
        "SELECT event, detail FROM journal WHERE event IN ('chunk_repair', 'chunk_disk_failed', 'send_failed',"
        " 'send_done', 'send_start') ORDER BY rowid")]
    conn.close()
    assert not [j for j in journal if j.startswith(("send_failed", "chunk_disk_failed"))], journal
    assert any(j.startswith("send_start") and "engine=chunk" in j for j in journal), journal
    return [j for j in journal if j.startswith("chunk_repair")]


def test_one_cloner_two_drawers_what_it_lost_is_fetched_over_http(stack):
    """A single machine lacks the chunks: no second multicast pass -- it fetches them itself, by Range."""
    _image, wave, runs = _round(stack, {CLONER_A: ["E2E-A1", "E2E-A2"]})
    repairs = _after(stack, wave, runs)
    assert len(repairs) == 1 and "multicast=0 " in repairs[0] and f"'{CLONER_A}': {len(LOST)}" in repairs[0], repairs
    assert stack.passes == {1: 1, 3: 1}                     # the sender ran one pass a partition
    log = (runs[CLONER_A][0] / "agent.log").read_text(errors="replace")
    assert "chunk fetch: 1 ranges: curl rc=0, apply rc=0" in log, log[-2000:]


def test_two_cloners_what_both_lost_is_sent_again_once(stack):
    """Two machines lack the same chunks: one more multicast pass of exactly those repairs both."""
    _image, wave, runs = _round(stack, {CLONER_A: ["E2E-A1"], CLONER_B: ["E2E-B1"]})
    repairs = _after(stack, wave, runs)
    # The first repair is the multicast pass of exactly what both lacked. A packet this loaded host really
    # loses in that pass is repaired by a later round (seen on the queue, 12/11, at the wave's full rate);
    # the deliberate loss never needs one.
    assert repairs and f"multicast={len(LOST)} to 2 fetch={{}}" in repairs[0], repairs
    assert stack.passes[1] == 1 and stack.passes[3] >= 2    # partition 3 had its repair pass


# --- the adversarial review of #3063 (13/11): how long a scattered fetch takes ---------------


FETCHER = r"""
export RUN_DIR="$1" LOG_FILE="$1/agent.log"; IMAGESRV_SERVER="$2"; IMAGE="$3"; FILE="$4"; MAC=aa:bb:cc:00:e2:21
. agent/lib/core/common.sh; . agent/lib/core/jsonq.sh; . agent/lib/core/waits.sh; . agent/lib/restore/verdict.sh
. agent/lib/core/tls.sh; tls_config || { echo "tls_config: $TLS_WHY" >&2; exit 5; }   # #3580
. agent/lib/restore/chunkledger.sh; . agent/lib/restore/chunkrecv.sh; . agent/lib/restore/chunkfetch.sh
. agent/lib/restore/chunkwatch.sh
[ ! -f agent/lib/restore/chunkrc.sh ] || . agent/lib/restore/chunkrc.sh   # #3259: what the receiver's exit codes mean
WAIT_STREAM_STALL_S=60; WAIT_DRAWER_S=60; STREAM_START_CEILING=600
CHUNK_DIR="$RUN_DIR/chunk"; CHUNK_STATE="$CHUNK_DIR/p3.state"; CHUNK_IDX="$CHUNK_DIR/p3.idx"; CHUNK_IMAGE_ARGS=""; CHUNK_SESSION=none
round_closed() { return 1; }
export SYSROOT="$RUN_DIR/sysroot"; dmesg() { :; }; partition_node() { echo "/dev/$1$2"; }   # the fetch is watched (chunkwatch.sh): not this host's sda
# everything is the agent's own but the last link: what apply would read is kept, byte for byte
chunkcast() { cat > "$RUN_DIR/got.bin"; echo "pass applied"; }
_chunk_fetch "$SERVER" "$IMAGE" "$FILE" 3 2 "sda" /dev/null
echo "rc=$?"
"""


def test_twenty_thousand_scattered_ranges_are_one_curl_on_one_connection(stack):
    """ממצא 3, נמדד מול השרת האמיתי. 20,000 טווחים של חתיכה אחת -- חוסר מפוזר של אימג' גדול -- דרך
    `_chunk_fetch` של הסוכן כמו שהוא: מעבר אחד על אינדקס של 40,000 חתיכות, `curl` אחד, חיבור אחד, ושער
    הזהות של השרת בכל בקשה. הבתים שמגיעים הם בדיוק הטווחים שהתבקשו, בסדר. הזמן נמדד ונאמר.

    מה שאינו כאן: הכתיבה עצמה. 20,000 חתיכות הן 80GB של נתונים, ו-`chunkcast apply` האמיתי על רשימה
    נמדד בנפרד (test_chunkcast_2533.py); כאן הוא מוחלף ב-`cat`, והאינדקס סינתטי (פריים של 50 בתים)."""
    image, wave, _disks, _answers, _manifest = _open(stack, {CLONER_A: ["E2E-A1"]})
    # the wave now waits for this machine's report, not for anyone to line up: time for the measurement
    status, told = stack.agent.json("POST", "/api/v1/agent/chunk-pass", {
        "mac": CLONER_A, "session_id": wave, "part": 3, "pass": 1, "state": "listening",
        "targets": [{"serial": "E2E-A1"}]})
    assert status == 200 and told["action"] == "receive", told

    served, name = stack.win.file.read_bytes(), stack.win.file.name
    chunks, frame = 40_000, 50
    assert len(served) >= chunks * frame, len(served)
    run = stack.tmp / "fetcher"
    (run / "chunk").mkdir(parents=True)
    (run / "chunk" / "p3.idx").write_text(
        f"IMAGESRV-IDX 2 raw 4194304 0 0 - {chunks * 4194304} {chunks} {chunks * frame}\n"
        + "".join(f"{k * frame} {frame} {k:016x}\n" for k in range(chunks)), encoding="ascii", newline="\n")
    wanted = list(range(1, chunks, 2))                                      # every other chunk: 20,000 ranges
    (run / "chunk" / "answer.json").write_text(json.dumps({"ranges": [[k, k] for k in wanted]}))
    (run / "fetcher.sh").write_text(FETCHER, encoding="utf-8", newline="\n")

    began = time.monotonic()
    done = subprocess.run([BASH, posix(run / "fetcher.sh"), posix(run), stack.base, image, name],
                          cwd=str(REPO), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600,
                          env={**os.environ, **stack.trust_env})
    took = time.monotonic() - began
    log = (run / "agent.log").read_text(errors="replace") if (run / "agent.log").exists() else ""
    assert done.stdout.strip().splitlines()[-1] == "rc=0", done.stdout + done.stderr + log[-2000:]
    assert "chunk fetch: 20000 ranges: curl rc=0, apply rc=0" in done.stdout + log

    got = (run / "got.bin").read_bytes()
    assert len(got) == len(wanted) * frame, (len(got), stack.tail())
    assert got == b"".join(served[k * frame:(k + 1) * frame] for k in wanted), "the ranges are not the bytes asked for, in order"

    stack.log.flush()
    asked = re.findall(r'127\.0\.0\.1:(\d+) - "GET /api/v1/images/%s/files/%s HTTP/1\.1" 206' % (image, re.escape(name)),
                       (stack.tmp / "server.log").read_text(errors="replace"))
    assert len(asked) == len(wanted), len(asked)
    assert len(set(asked)) == 1, f"{len(set(asked))} connections for one fetch"
    warnings.warn(UserWarning(
        f"#2533 measured: a fetch of {len(wanted)} scattered ranges (index of {chunks} chunks, one curl, one "
        f"connection, the identity gate on every request) took {took:.1f}s -- {took / len(wanted) * 1000:.2f}ms a range"))
    assert took < 300, f"{took:.0f}s for 20,000 ranges"

