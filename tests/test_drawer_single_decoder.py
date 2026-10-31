"""One zstd for the cloner, not one per drawer (drawerfeed.sh).

Metal, 18/10 (HP 8100, i5-650, 2 cores + HT): one drawer ran at 450m, two at
264-370m, and the CPU was the wall -- each drawer decoded the very same stream
with its own `zstd -dc` (0.68 of a logical CPU each at 75MB/s, 17/10,
cloner-pipeline-perf.md 1.1). Now the stream is decoded once and the
*decompressed* bytes are fanned out to every drawer's partclone.

What must not move with it, and is pinned here:

* **Structure.** Exactly one decoder, and one partclone per drawer.
* **Every byte, on every drawer** (principle 4): each drawer's partition is the
  whole decompressed stream, and the stream's sha256 still decides.
* **Units.** A drawer's counter is read against the image's *compressed*
  total (console bar, server drive rates). After the decoder the second fanout
  only sees decompressed bytes, so `fanout -U` keeps the counter in compressed
  bytes -- exactly the compressed size at the end.
* **A decoder failure is the stream's, for every drawer** -- never `done`
  (#527), and never recorded against a healthy disk (#440, #1728).
* **The lab switch** ($RUN_DIR/tune) brings the old shape back for a metal
  A/B, and refuses a value it does not know -- out loud.

Runs the real `restore_partition_drawers` with the real `fanout` (compiled
from agent/fanout.c); zstd is gzip under that name, so "compressed" and
"decompressed" really differ in length. Once under bash and once under
busybox ash -- the agent's own shell.
"""

from __future__ import annotations

import gzip
import hashlib
import random
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import hygiene
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import log_of, make_stubs
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent
BUSYBOX = shutil.which("busybox")
pytestmark = [
    requires_native("gcc", posix=True, why="fanout האמיתי צריך gcc ו-fifo"),
    # #2118: WAIT_STREAM_STALL (20s, ערך של המוצר) נחצה תחת -n auto; סדרתי עובר ב-3s.
    pytest.mark.serial,
]

_rng = random.Random(1018)
#: ~6MB that compress about 3:1 -- more than the 2MB rings, so the rings wrap.
RAW = b"".join(bytes(_rng.randrange(16) for _ in range(64)) * 3 for _ in range(32 * 1024))
PACKED = gzip.compress(RAW, compresslevel=6, mtime=0)
SHA = hashlib.sha256(PACKED).hexdigest()
DISKS = ("sda", "sdb", "sdc")

STUBS = {
    # The decoder: every start is counted, with the niceness it got.
    "zstd": '#!/bin/sh\necho "$$ $(nice)" >> "$BOX/zstd.calls"\nexec gzip -dc\n',
    "partclone.dd": ('#!/bin/sh\necho "$*" >> "$BOX/pcl.args"\nnode=\n'
                     'while [ $# -gt 0 ]; do case "$1" in -O) node=$2; shift 2 ;; *) shift ;; esac; done\n'
                     'echo "$node $(nice)" >> "$BOX/pcl.calls"\n'
                     'exec cat > "$node"\n'),
    "dmesg": "#!/bin/sh\n:\n",
    "sgdisk": '#!/bin/sh\necho "$*" >> "$BOX/sgdisk.calls"\n',
    "blockdev": "#!/bin/sh\nexit 0\n",
    "fanout": None,   # the real one
}

SHELLS = [pytest.param(BASH, id="bash"),
          pytest.param(BUSYBOX, id="busybox-ash",
                       marks=requires_native("busybox", why="the agent's own shell"))]


@pytest.fixture(scope="module")
def fanout_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("fanout-bin")
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out / "fanout"),
                    str(REPO / "agent" / "fanout.c")], check=True, stdin=subprocess.DEVNULL)
    # Integration tests exercise decoding/bytes, not fanout's five-second
    # reader-start deadline. Rendezvous with the real FIFO readers first.
    # Keep only WRITE descriptors: an absent/dead reader still causes EPIPE.
    # exec closes these at fanout exit, so readers still receive EOF.
    ready = out / "ready"
    ready.mkdir()
    wrapper = ready / "fanout"
    wrapper.write_text(
        '#!/bin/sh\n'
        'rendezvous() {\n'
        '  while :; do case "$1" in -S|-P|-U|-H) shift 2 ;; *) break ;; esac; done\n'
        '  shift\n'
        '  n=3\n'
        '  for fifo; do\n'
        '    [ -p "$fifo" ] || continue\n'
        '    case "$n" in\n'
        '      3) exec 3>"$fifo" ;;\n'
        '      4) exec 4>"$fifo" ;;\n'
        '      5) exec 5>"$fifo" ;;\n'
        '      *) echo "unexpected fourth test drawer" >&2; exit 2 ;;\n'
        '    esac\n'
        '    n=$((n + 1))\n'
        '  done\n'
        '}\n'
        'rendezvous "$@"\n'
        f'exec {shlex.quote(str(out / "fanout"))} "$@"\n',
        encoding="utf-8", newline="\n")
    wrapper.chmod(0o755)
    return out


def run_in(shell: str, script: str, timeout: int = 60) -> str:
    """Like test_timeouts.run_sh, in the shell asked for: output to a file,
    DEVNULL for the pipes (an orphan must not hold the test)."""
    out_file = Path(tempfile.mkdtemp(prefix="imagesrv-feed-")) / "out"
    script_file = out_file.with_name("script.sh")
    script_file.write_text('export PATH="/usr/bin:$PATH"\n'
                           f"{{\n{script}\n}} > {posix(out_file)!r} 2>&1\n",
                           encoding="utf-8", newline="\n")
    argv = [shell, "sh", posix(script_file)] if shell == BUSYBOX else [shell, posix(script_file)]
    hygiene.run_group(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   stdin=subprocess.DEVNULL, cwd=str(REPO), timeout=timeout)
    return out_file.read_text(encoding="utf-8", errors="replace")


def room(tmp_path: Path, fanout_dir: Path, shell: str = BASH, payload: bytes = PACKED,
         sha: str = SHA, tune: str = "", env: str = "",
         disks: tuple[str, ...] = DISKS) -> tuple[Path, Path, str]:
    box = tmp_path / "box"
    run, dev = box / "run", box / "dev"
    (run / "targets").mkdir(parents=True)
    dev.mkdir()
    (box / "proc").mkdir()
    (box / "proc" / "uptime").write_text("100.00 50.00\n")
    for d in DISKS:
        sysd = box / "sys" / "block" / d / "device"
        sysd.mkdir(parents=True)
        (sysd / "state").write_text("running\n")
        (sysd / "serial").write_text(f"SER-{d}\n")
    (box / "part.gz").write_bytes(payload)
    if tune:
        (run / "tune").write_text(tune, encoding="utf-8")
    libs = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in (
        "common", "sysinfo", "waits", "progress", "restore", "drawers", "drawerfeed",
        "verdict", "failmark", "ntfsboot"))
    out = run_in(shell,
        make_stubs(box / "stubs", STUBS)
        + f'export PATH="{posix(fanout_dir / "ready")}:$PATH"\n'
        + f"export BOX={posix(box)!r} SYSROOT={posix(box)!r} RUN_DIR={posix(run)!r} "
        f"DEVROOT={posix(dev)!r} TMPDIR={posix(tmp_path)!r} FANOUT_BUFFER=2097152 "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=10 WAIT_HELPER_S=5 WAIT_STREAM_START_S=20 "
        f"WAIT_STREAM_STALL_S=20 {env}; "
        + libs
        + "".join(f"target_init {d} {len(payload)}; echo SER-{d} > \"$RUN_DIR/targets/{d}/write_started\"; "
                  for d in disks)
        + f'stream_source() {{ nice > "$BOX/source.nice"; cat {posix(box / "part.gz")!r}; }}; '
        + f"restore_partition_drawers unicast http://s img 3 dd part.zst {sha} '' {' '.join(disks)}"
        " > /dev/null 2>&1; " 'echo "rc=$?"; '
        + "".join(f'echo "{d} $(target_bytes {d})"; ' for d in disks)
        + f"pkill -9 -f {posix(box)!r} 2>/dev/null; :")
    return box, run, out


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def state(run: Path, dev: str, name: str = "state") -> str:
    path = run / "targets" / dev / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


@pytest.mark.parametrize("shell", SHELLS)
def test_one_decoder_feeds_every_drawer_every_byte(tmp_path, fanout_dir, shell):
    box, run, out = room(tmp_path, fanout_dir, shell)
    log = log_of(run)
    assert "rc=0" in out, out + log
    assert len(lines(box / "zstd.calls")) == 1, (lines(box / "zstd.calls"), log)
    assert sorted(line.split()[0].rsplit("/", 1)[1] for line in lines(box / "pcl.calls")) == \
        ["sda3", "sdb3", "sdc3"]
    assert "decode=shared drawers=3" in log
    assert "--write-direct-io" not in "".join(lines(box / "pcl.args")), "O_DIRECT is opt-in"
    for d in DISKS:
        assert state(run, d) != "failed", (d, state(run, d, "error"), log)
        assert (box / "dev" / f"{d}3").read_bytes() == RAW, f"{d}: not every byte"
    # The counters stay in compressed bytes: exactly the stream, per drawer.
    for d in DISKS:
        assert f"{d} {len(PACKED)}" in out, (out, len(PACKED), len(RAW))


def test_the_lab_switch_brings_back_a_decoder_per_drawer(tmp_path, fanout_dir):
    box, run, out = room(tmp_path, fanout_dir, tune="IMAGESRV_DRAWER_DECODE=each\n")
    log = log_of(run)
    assert "rc=0" in out, out + log
    assert len(lines(box / "zstd.calls")) == 3, lines(box / "zstd.calls")
    assert "decode=each" in log
    for d in DISKS:
        assert (box / "dev" / f"{d}3").read_bytes() == RAW
        assert f"{d} {len(PACKED)}" in out, out


def test_one_drawer_keeps_its_own_decoder(tmp_path, fanout_dir):
    """auto: with one live drawer a second fanout is pure cost (the bench in
    drawerfeed.sh) -- the old shape, and still every byte."""
    box, run, out = room(tmp_path, fanout_dir, disks=("sda",))
    log = log_of(run)
    assert "rc=0" in out, out + log
    assert "decode=each drawers=1" in log, log
    assert not (run / "mid").exists()
    assert (box / "dev" / "sda3").read_bytes() == RAW
    assert f"sda {len(PACKED)}" in out, out


def test_direct_io_is_handed_to_partclone_when_asked(tmp_path, fanout_dir):
    box, run, out = room(tmp_path, fanout_dir, tune="IMAGESRV_DRAWER_DIRECTIO=1\n")
    assert "rc=0" in out, out + log_of(run)
    args = lines(box / "pcl.args")
    assert len(args) == 3 and all("--write-direct-io" in a for a in args), args


def test_a_switch_value_it_does_not_know_is_refused_out_loud(tmp_path, fanout_dir):
    box, run, out = room(tmp_path, fanout_dir,
                         tune="IMAGESRV_DRAWER_DECODE=fast\nIMAGESRV_DRAWER_NICE=-5\n"
                              "IMAGESRV_DRAWER_DIRECTIO=yes\n")
    log = log_of(run)
    assert "rc=0" in out, out + log
    assert "IMAGESRV_DRAWER_DECODE=fast is not auto|shared|each" in log
    assert "IMAGESRV_DRAWER_NICE=-5 is not 0-19" in log
    assert "IMAGESRV_DRAWER_DIRECTIO=yes is not 0|1" in log
    assert len(lines(box / "zstd.calls")) == 1


def test_the_writers_are_niced_and_the_receiver_is_not(tmp_path, fanout_dir):
    box, run, out = room(tmp_path, fanout_dir, tune="IMAGESRV_DRAWER_NICE=7\n")
    assert "rc=0" in out, out + log_of(run)
    assert [line.split()[1] for line in lines(box / "zstd.calls")] == ["7"]
    assert [line.split()[1] for line in lines(box / "pcl.calls")] == ["7", "7", "7"]
    assert (box / "source.nice").read_text().strip() == "0", "the receiver must not lose the CPU"


def test_a_decoder_that_fails_fails_every_drawer_as_the_stream(tmp_path, fanout_dir):
    """The bytes arrived exactly as sent -- the sha matches -- and the decoder
    still failed (here: a damaged frame). Every drawer it fed failed, by the
    stream's name: no drawer is done on a short write, no disk is blamed."""
    bad = bytearray(PACKED)
    bad[len(bad) // 2:len(bad) // 2 + 64] = b"\xff" * 64
    bad = bytes(bad)
    box, run, out = room(tmp_path, fanout_dir, payload=bad, sha=hashlib.sha256(bad).hexdigest())
    log = log_of(run)
    assert "rc=1" in out, out + log
    for d in DISKS:
        assert state(run, d) == "failed", (d, log)
        assert "פענוח הזרם נכשל" in state(run, d, "error"), state(run, d, "error")
        assert not (run / "targets" / d / "ata_log.json").exists(), (d, "a healthy disk was blamed")


def test_a_hung_drawer_is_evicted_alone_and_the_machine_carries_on(tmp_path, fanout_dir):
    """Two fanouts now wait on the same hung drawer: the drawers' one (it is
    its to evict) and, through the decoder, the stream's one. With equal
    ceilings they race, and the stream's could drop the decoder -- the whole
    machine -- for one drawer. The stream's waits twice as long."""
    hang = ("#!/bin/sh\nnode=\n"
            'while [ $# -gt 0 ]; do case "$1" in -O) node=$2; shift 2 ;; *) shift ;; esac; done\n'
            'case "$node" in */sdb3) head -c 1048576 > /dev/null; exec sleep 40 ;; esac\n'
            'exec cat > "$node"\n')
    old = STUBS["partclone.dd"]
    STUBS["partclone.dd"] = hang
    try:
        box, run, out = room(tmp_path, fanout_dir, env="FANOUT_STALL_EVICT_MS=3000")
    finally:
        STUBS["partclone.dd"] = old
    log = log_of(run)
    assert "rc=0" in out, out + log
    assert state(run, "sdb") == "failed", log
    for d in ("sda", "sdc"):
        assert state(run, d) != "failed", (d, state(run, d, "error"), log)
        assert (box / "dev" / f"{d}3").read_bytes() == RAW


# --- fanout -U, on its own ---------------------------------------------------


def _fanout_u(fanout_dir: Path, tmp_path: Path, units: str | None, data: bytes,
              outs: list[Path]) -> subprocess.CompletedProcess:
    upath = tmp_path / "mid.bytes"
    for out in outs:   # fanout opens its targets, it does not create them (fifos)
        if not out.exists():
            out.write_bytes(b"")
    if units is not None:
        upath.write_text(units)
    return subprocess.run([str(fanout_dir / "fanout"), "-U", str(upath), "2097152",
                           *map(str, outs)], input=data, capture_output=True, timeout=30)


def test_units_counter_ends_on_the_upstream_total(tmp_path, fanout_dir):
    out = tmp_path / "out"
    r = _fanout_u(fanout_dir, tmp_path, "100\n250\n", b"x" * 4000, [out])
    assert r.returncode == 0, r.stderr
    assert out.read_bytes() == b"x" * 4000
    assert (tmp_path / "out.bytes").read_text().splitlines()[-1] == "250"


def test_units_counter_reads_only_whole_lines_and_never_invents_a_number(tmp_path, fanout_dir):
    out = tmp_path / "out"
    r = _fanout_u(fanout_dir, tmp_path, "100\n25", b"x" * 4000, [out])
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out.bytes").read_text().splitlines()[-1] == "100"


def test_units_counter_says_nothing_until_the_upstream_counter_exists(tmp_path, fanout_dir):
    """Not read is not zero (principle 5): no number at all, not a made-up 0."""
    out = tmp_path / "out"
    r = _fanout_u(fanout_dir, tmp_path, None, b"y" * 4000, [out])
    assert r.returncode == 0, r.stderr
    assert out.read_bytes() == b"y" * 4000
    assert (tmp_path / "out.bytes").read_text() == ""


def test_units_counter_of_a_drawer_that_died_is_frozen_below_the_total(tmp_path, fanout_dir):
    ok, dead = tmp_path / "ok", tmp_path / "dead"
    subprocess.run(["mkfifo", str(dead)], check=True, stdin=subprocess.DEVNULL)
    reader = subprocess.Popen(["sh", "-c", f"head -c 65536 {posix(dead)} > /dev/null"],
                              stdin=subprocess.DEVNULL)
    r = _fanout_u(fanout_dir, tmp_path, "1000000\n", b"z" * (8 * 1024 * 1024), [ok, dead])
    reader.wait(timeout=10)
    assert r.returncode == 1, r.stderr
    assert f"{dead} failed" in r.stdout.decode()
    assert (tmp_path / "ok.bytes").read_text().splitlines()[-1] == "1000000"
    frozen = int((tmp_path / "dead.bytes").read_text().splitlines()[-1])
    assert 0 <= frozen < 1000000, frozen
