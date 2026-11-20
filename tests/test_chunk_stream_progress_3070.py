"""#3070 -- the bar and the rate of a drawer in the chunk engine count what the disk writes.

Metal 13/11, cloner 2, run h, image img_3fa17f: two healthy drawers stood 92 s at 84% and
117 s at 92% (rate 0-9 MB/s) while they wrote -- they ended `17814 of 18254 durable, 0 skipped`.

The root, read from the image's own index on the lab server (partition 3, 18,254 chunks of
4 MiB, 17,814,151,642 compressed bytes): chunks 9227..13554 (4,327 of them) hold 0.1% of the
compressed bytes, and chunks 13554..18254 (4,701) hold none -- a zeroed region. The drive
writes 4 MiB a chunk whatever those 4 MiB cost compressed, so the compressed counter
(`_chunk_counters`, the sum of `zbytes` of what is durable) stands while the disk writes.
Its position in time matches: 4,327 chunks at the 141 MB/s the pass ran at are 129 s, 4,701
are 140 s. The other explanation the Issue kept open (a flush that never returned) is not
what the index shows.

The fix is the screen's: a second pair, in the bytes of the data stream (chunkprog.sh), that
the drawer record, its bar and its rate are made of. The report to the server stays compressed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentlib import agent_sources
from native import requires_native
from test_cloner_gui import AGENT, BASH, PORTS, PRELUDE, env, posix, sh, target

pytestmark = requires_native("jq", ("bash", BASH), why="the manifest is read with jq, as in the agent")

CB = 4194304


def _index(path: Path, sizes: list[int], data_bytes: int | None = None) -> None:
    """An index of `len(sizes)` chunks whose frames are `sizes` compressed bytes each."""
    data = len(sizes) * CB if data_bytes is None else data_bytes
    head = 100
    rows, off = [], head
    for k, z in enumerate(sizes):
        rows.append(f"{off} {z} {k:016x}")
        off += z
    path.write_text(f"IMAGESRV-IDX 2 partclone {CB} 4096 {head} 54a7280ff8d37ac3 {data} {len(sizes)} {off}\n"
                    + "\n".join(rows) + "\n", newline="\n")


def _state(path: Path, node: str, ranges: str, count: int, word: str = "ok") -> None:
    path.write_text(f"chunkcast-state 1 0123456789abcdef 3 99\n{node} {word} {count} {ranges}\n", newline="\n")


LIBS = (f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
        f'{agent_sources("sysinfo", "diskinfo", "progress", "failmark")}. {posix(AGENT)}/lib/restore/chunkrecv.sh; '   # the agent's own order (#2272)
        # not there = the tree before the fix (the negative control): the counters run as they did, and the tests fail on what they do
        f'[ ! -f {posix(AGENT)}/lib/restore/chunkprog.sh ] || . {posix(AGENT)}/lib/restore/chunkprog.sh; '
        'partition_node() { echo "/dev/$1$2"; }; disk_serial() { echo "SER-$1"; }; ')


def _run(tmp: Path, body: str):
    run = tmp / "run"
    (run / "targets" / "sda").mkdir(parents=True, exist_ok=True)
    (run / "targets" / "sda" / "state").write_text("writing\n", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)}/log; ' + LIBS + body)
    assert out.returncode == 0, out.stderr
    return out


def _number(path: Path) -> int | None:
    """The last number of a counter file, or None -- 'not written' is its own answer."""
    if not path.exists():
        return None
    nums = [int(l) for l in path.read_text().split() if l.isdigit()]
    return nums[-1] if nums else None


def _counters(tmp: Path, sizes: list[int], ranges: str, count: int, data_bytes: int | None = None,
              word: str = "ok") -> None:
    chunk = tmp / "run" / "chunk"
    chunk.mkdir(parents=True, exist_ok=True)
    _index(chunk / "p3.idx", sizes, data_bytes)
    _state(chunk / "p3.state", "/dev/sda3", ranges, count, word)
    _run(tmp, f'CHUNK_DIR={posix(chunk)}; CHUNK_STATE={posix(chunk)}/p3.state; CHUNK_IDX={posix(chunk)}/p3.idx; '
              f'echo {4096 * 99 * CB} > "$RUN_DIR/targets/sda/stream_total"; _chunk_counters 3 sda')


def test_a_stretch_that_compresses_to_nothing_moves_the_stream_counter_and_not_the_compressed_one(tmp_path):
    """The metal case in small: 10 dense chunks, then 10 that compress to 40 bytes. Both counters read the
    state after the first ten, then after all twenty. The compressed one moves 400 bytes (a bar that stands);
    the stream one moves ten chunks of the disk's write."""
    sizes = [3_000_000] * 10 + [40] * 10
    _counters(tmp_path, sizes, "0-9", 10)
    t = tmp_path / "run" / "targets" / "sda"
    z1, s1 = _number(t / "feed.bytes"), _number(t / "stream.3")
    _counters(tmp_path, sizes, "0-19", 20)
    z2, s2 = _number(t / "feed.bytes"), _number(t / "stream.3")
    assert z1 is not None and z2 is not None and z2 - z1 == 400, (z1, z2)     # what the bar did: nothing
    assert s1 == 10 * CB and s2 == 20 * CB, (s1, s2)                           # what the disk did


def test_the_last_chunk_is_short_and_the_stream_ends_where_the_data_does(tmp_path):
    """Chunks are 4 MiB but the last: a drawer that holds every chunk has written exactly the data, not
    a chunk more -- 100% means the bytes are all there."""
    data = 3 * CB - 1000
    _counters(tmp_path, [500] * 3, "0-1", 2, data_bytes=data)
    assert _number(tmp_path / "run" / "targets" / "sda" / "stream.3") == 2 * CB     # the last one is not durable
    _counters(tmp_path, [500] * 3, "0-2", 3, data_bytes=data)
    assert _number(tmp_path / "run" / "targets" / "sda" / "stream.3") == data


def test_holes_count_only_what_is_there(tmp_path):
    """17,814 of 18,254 on the metal: a drawer with missing chunks is behind by exactly those."""
    _counters(tmp_path, [500] * 10, "0-3,6-9", 8)
    assert _number(tmp_path / "run" / "targets" / "sda" / "stream.3") == 8 * CB


def test_a_failed_drawer_leaves_its_last_number(tmp_path):
    """A target chunkcast failed says no count: nothing is written over what was last measured."""
    sizes = [500] * 10
    _counters(tmp_path, sizes, "0-4", 5)
    _counters(tmp_path, sizes, "-", 0, word="failed")
    assert _number(tmp_path / "run" / "targets" / "sda" / "stream.3") == 5 * CB


def test_no_total_no_stream_file(tmp_path):
    """A drawer the wave gave no total (another engine) writes no stream counter: there is no pair to make."""
    chunk = tmp_path / "run" / "chunk"
    chunk.mkdir(parents=True)
    _index(chunk / "p3.idx", [500] * 4)
    _state(chunk / "p3.state", "/dev/sda3", "0-3", 4)
    _run(tmp_path, f'CHUNK_DIR={posix(chunk)}; CHUNK_STATE={posix(chunk)}/p3.state; CHUNK_IDX={posix(chunk)}/p3.idx; '
                   '_chunk_counters 3 sda')
    assert not list((tmp_path / "run" / "targets" / "sda").glob("stream*"))


def _manifest(path: Path, parts: list[dict]) -> None:
    path.write_text(json.dumps({"image_format": 2, "partitions": parts}), newline="\n")


def _init(tmp: Path, parts: list[dict]) -> str | None:
    _manifest(tmp / "manifest.json", parts)
    (tmp / "run" / "targets" / "sda").mkdir(parents=True, exist_ok=True)
    out = _run(tmp, f'if command -v chunk_stream_init >/dev/null; then chunk_stream_init {posix(tmp)}/manifest.json sda || :; '
                    'else echo NOFUNC; fi')   # rc 1 is its answer "this drawer has no total", not a failure of the room
    assert "NOFUNC" not in out.stdout, "chunk_stream_init does not exist"
    f = tmp / "run" / "targets" / "sda" / "stream_total"
    return f.read_text().strip() if f.exists() else None


def test_the_total_is_the_data_stream_of_every_partition_that_has_a_file(tmp_path):
    got = _init(tmp_path, [{"index": 1, "file": "p1.pcl.zst", "readback_bytes": 40239104},
                           {"index": 3, "file": "p3.pcl.zst", "readback_bytes": 76562337792},
                           {"index": 5, "file": None, "readback_bytes": None, "fs": "swap"}])
    assert got == str(40239104 + 76562337792)


def test_a_partition_that_does_not_say_its_stream_leaves_no_total_and_removes_an_old_one(tmp_path):
    (tmp_path / "run" / "targets" / "sda").mkdir(parents=True)
    (tmp_path / "run" / "targets" / "sda" / "stream_total").write_text("123\n")     # a total of a wave before
    got = _init(tmp_path, [{"index": 1, "file": "p1.pcl.zst", "readback_bytes": 40239104},
                           {"index": 3, "file": "p3.pcl.zst"}])
    assert got is None


def _pair(tmp: Path) -> str:
    return _run(tmp, 'if chunk_stream_pair sda; then echo "$_cpg_bytes $_cpg_total"; else echo NONE; fi').stdout.strip()


def test_the_pair_is_the_sum_of_the_partitions_over_the_total(tmp_path):
    t = tmp_path / "run" / "targets" / "sda"
    t.mkdir(parents=True)
    (t / "stream_total").write_text("1000\n")
    (t / "stream.1").write_text("100\n")
    (t / "stream.3").write_text("5\n7\n")      # a counter file may hold lines; the last is the number
    assert _pair(tmp_path) == "107 1000"


def test_no_total_or_more_than_the_whole_is_no_pair(tmp_path):
    t = tmp_path / "run" / "targets" / "sda"
    t.mkdir(parents=True)
    (t / "stream.1").write_text("100\n")
    assert _pair(tmp_path) == "NONE"            # nothing to be a pair of
    (t / "stream_total").write_text("50\n")
    assert _pair(tmp_path) == "NONE"            # more written than the whole: no measurement, never a bar over 100%


# --- the drawer's record: what the screen is made of ------------------------------------------

G = 1_000_000_000
STATE_SH = PRELUDE + (f'[ ! -f {posix(AGENT)}/lib/restore/chunkprog.sh ] || . {posix(AGENT)}/lib/restore/chunkprog.sh; ') + PORTS


def _record(tmp: Path, moved_ago: int, compressed: str, stream: int, total: int) -> list[str]:
    run, gui = tmp / "run", tmp / "gui"
    run.mkdir(exist_ok=True); gui.mkdir(exist_ok=True)
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", "writing", 18 * G, 0, compressed)
    (run / "targets" / "sda" / "stream_total").write_text(f"{total}\n", newline="\n")
    (run / "targets" / "sda" / "stream.3").write_text(f"{stream}\n", newline="\n")
    now = int(time.time())
    # the screen's last sight of the compressed counter: the same number, `moved_ago` seconds ago -- the standing bar
    (gui / "moved.sda").write_text(f"{now - moved_ago} {compressed}\n", newline="\n")
    (gui / "pace.sda").write_text(f"{now - 700} 1000\n", newline="\n")
    out = sh(env(run, gui) + STATE_SH + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    return next(l for l in lines if l.startswith("drawer=1|sda|")).split("|")


def test_a_drawer_that_writes_through_a_stretch_of_nothing_has_a_rate_and_a_stream_bar(tmp_path):
    """The metal 92%: the compressed counter has not moved for 3 minutes (the old screen's 'waiting for the
    stream, 0 MB/s'), and the durable stream bytes stand at 70 GB of 76.5 GB -- written, and moving."""
    f = _record(tmp_path, 180, "16370000000", 70 * G, 76_562_337_792)
    assert int(f[3]) == 70 * G and int(f[4]) == 76_562_337_792, f      # the bar's numbers: the stream's
    assert int(f[6]) > 0, f                                            # a rate, not "waiting": the bytes moved from what the screen last saw
    assert int(f[7]) > 0, f                                            # and a time left


def test_a_stream_that_really_stands_is_still_rate_zero(tmp_path):
    """No progress is 0, measured -- the rule of #2055 is kept: only a counter that moves is a rate. Here the
    screen last saw exactly these stream bytes 3 minutes ago."""
    run, gui = tmp_path / "run", tmp_path / "gui"
    f = _record(tmp_path, 0, "16370000000", 70 * G, 76_562_337_792)
    now = int(time.time())
    (gui / "moved.sda").write_text(f"{now - 180} {70 * G}\n", newline="\n")
    out = sh(env(run, gui) + STATE_SH + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    f = next(l for l in lines if l.startswith("drawer=1|sda|")).split("|")
    assert int(f[6]) == 0 and int(f[7]) > 0, f      # #3270: it stands = rate 0, measured; the wave's average end stays


def test_a_drawer_without_a_stream_total_keeps_the_compressed_pair(tmp_path):
    """The other engine, or a manifest that did not say: the bar is as it was."""
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", "writing", 18 * G, 0, "9000000000")
    out = sh(env(run, gui) + STATE_SH + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    f = next(l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("drawer=1|sda|")).split("|")
    assert int(f[3]) == 9 * G and int(f[4]) == 18 * G, f


def test_both_the_agent_and_the_screens_process_load_the_library():
    """A function the screen's kiosk calls and does not have is "not found" once a second on the metal (#834)."""
    for path in ("agent/imagesrv-agent", "agent/lib/gui/guibridge.sh"):
        assert "chunkprog.sh" in (AGENT.parent / path).read_text(encoding="utf-8"), path
