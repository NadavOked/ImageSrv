"""#3533: what the writers ask of the disks, said by the receiver, and --sync-ms reachable from the agent.

The issue is a hardware experiment (`--sync-ms 2000 -> 8000`, one variable). Nothing here changes a default: the receiver says one
more stdout line (`wio`), and the agent passes `CHUNK_SYNC_MS` on as `--sync-ms` -- and shows a stall after three flushes of it, as it
shows one after three of 2 s (6) -- only when that variable is set.
"""
from __future__ import annotations

import subprocess

import pytest

import chunkcast_rig as C
from native import requires_native
from test_agent import AGENT, BASH
from test_chunkrecv_2533 import DONE, RECEIVE1, _run

REPO = AGENT.parent
native_c = requires_native("gcc", "zstd", posix=True, why="real chunkcast on loopback")
shell = requires_native(("bash", BASH), "jq", why="the agent's own shell")


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("wio-bin"))


def one_pass(cc, image, tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    session, port = C.new_session(), C.free_port()
    target = image.blank(tmp_path / "target.disk")
    r = C.recv(cc, image, [target], f"127.0.0.1:{port}", session, tmp_path / "state", tag=tmp_path / "recv")
    s = C.send(cc, image, f"127.0.0.1:{port}", session, tag=tmp_path / "send")
    try:
        assert s.wait() == 0, s.log()
        assert r.wait() == 0, r.log()
    finally:
        for run in (r, s):
            if run.process.poll() is None:
                run.process.kill()
                run.process.wait()
    assert target.read_bytes() == image.expected(), r.log()
    found = [x for x in r.out.read_text().splitlines() if x.startswith("wio ")]
    assert len(found) == 1, r.log()
    return {k: int(v) for k, v in (pair.split("=") for pair in found[0].split()[1:])}


@native_c
def test_a_raw_partition_is_one_write_for_each_chunk(cc, tmp_path):
    raw = C.build(tmp_path / "img", cc / "fanout", kind="raw", noisy=(), chunks=2.25, name="p2.msr.pcl.zst")
    wio = one_pass(cc, raw, tmp_path / "run")
    assert wio["runs"] == raw.n == 3 and wio["bytes"] == len(raw.data), wio
    assert wio["sync_ms"] == 100 and wio["flushes"] >= 1, wio      # the rig's own --sync-ms


@native_c
def test_a_partition_with_free_blocks_between_the_used_ones_is_one_write_for_each_run_of_a_chunk(cc, tmp_path):
    """The expectation is the image's own layout (a run is the used blocks that touch, cut at a chunk's end), not the receiver's."""
    img = C.build(tmp_path / "img", cc / "fanout", noisy=(), chunks=4.5)
    per = C.CH // img.bs
    expected = 0
    for k in range(img.n):
        blocks = img.used[k * per:(k + 1) * per]
        expected += 1 + sum(1 for a, b in zip(blocks, blocks[1:]) if b != a + 1)
    wio = one_pass(cc, img, tmp_path / "run")
    assert expected > 4 * img.n, "the image has to have runs to count"
    assert wio["runs"] == expected and wio["bytes"] == len(img.data), (wio, expected)


def sent_sync(tmp_path, env=""):
    got = _run(tmp_path, [RECEIVE1, DONE], env=env)
    assert got["trace"][-1] == "rc=0", got
    line = next(x for x in got["trace"] if x.startswith("chunkcast recv "))
    return line.split("--sync-ms ")[1].split()[0] if "--sync-ms" in line else None


@shell
def test_the_agent_hands_chunk_sync_ms_to_the_receiver_and_only_then(tmp_path):
    assert sent_sync(tmp_path / "a") is None
    assert sent_sync(tmp_path / "b", env="CHUNK_SYNC_MS=8000") == "8000"


@shell
@pytest.mark.parametrize("env,shown", [("", "6"), ("CHUNK_SYNC_MS=2000", "6"), ("CHUNK_SYNC_MS=8000", "24"),
                                       ("CHUNK_SYNC_MS=8000 CHUNK_STALL_SHOW_S=2", "2"), ("CHUNK_SYNC_MS=abc", "6")])
def test_a_stall_is_shown_after_three_flushes(env, shown):
    """6 s at the default 2 s. Not a number: chunkcast refuses it at its start, and the display stays at the default.
    The assignments are separate statements: `VAR=x . file` is temporary in bash, so a CHUNK_STALL_SHOW_S given that way is gone after the `.`."""
    proc = subprocess.run([BASH, "-c", f'{env or ":"}; . agent/lib/restore/chunkstall.sh; echo "$CHUNK_STALL_SHOW_S"'],
                          capture_output=True, text=True, cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert proc.stdout.strip() == shown, proc
