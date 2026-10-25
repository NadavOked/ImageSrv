"""#1442, #1619 -- the local clone's "copied" counter at a partition change.

The screen reads the counter in the middle of the agent's writes. Two ways it
misread, one per issue:

* #1442: the base the live counter was added to was written with ``echo >``
  (truncate, then write): a read between the two saw base 0, the counter
  dropped for a frame;
* #1619: the base was written first and the live counter emptied after: a read
  between the two saw the new base *and* the old counter, the finished
  partition counted twice, the counter jumped.

A race is not asserted by hoping to hit it. This stops the real ``lc_stream``
(stubbed ``partclone``/``fanout``, as in ``test_local_clone_agent.py``) before
every command of the shell that runs it -- bash ``DEBUG`` trap with
``functrace`` -- and reads the counter the way the screen's state does
(``lc_gui_run``) at each stop. Every interleaving of a reader with the writer,
at the writer's own command granularity, is then in the samples.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, posix
from test_local_clone_agent import REPO, STREAM_STUBS, run

pytestmark = requires_native(("bash", BASH))

FIRST, SECOND = 32768, 12288          # the two partitions' bytes


def _drive(tmp_path: Path) -> list[tuple[str, int]]:
    """Two partitions streamed back to back; (cur, copied) at every command."""
    box = tmp_path / "box"; stubs = box / "stubs"; dev = box / "dev"; dev.mkdir(parents=True)
    (dev / "sdc1").write_bytes(b"x" * FIRST)
    (dev / "sdc2").write_bytes(b"y" * SECOND)
    samples = box / "samples"
    make = [f"mkdir -p {posix(stubs)!r}"]
    for name, body in STREAM_STUBS.items():
        make += [f"cat > {posix(stubs)}/{name} <<'STUB'\n{body}STUB", f"chmod 0755 {posix(stubs)}/{name}"]
    t = "\n".join(make) + "\n"
    t += f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH" STUB_BOX={posix(box)!r} DEVROOT={posix(dev)!r}; '
    t += 'mkdir -p "$LC/run"; ata_capture() { :; }; target_init sda 100; target_init sdb 100; '
    t += f'SAMPLES={posix(samples)!r}; _smp_busy=; _smp_main=$BASHPID; '
    t += '[ -n "$_smp_main" ] || { echo "no BASHPID: not bash" >&2; exit 97; }; '
    # what the screen's state loop reads, at this very command; the guard keeps the
    # reader's own commands (and the subshells it forks) from sampling themselves.
    # #1748: the sampler's own names only. It runs inside the agent's functions and
    # sh has no locals: a `_c` here was `_counter_of`'s `_c` (progress.sh) -- a count
    # landed where a counter path is read, and `ln -s` made a link named 32768 in
    # the cwd, the repository root.
    # #2118/#2121: the main shell only. `set -T` hands the trap to every subshell
    # (the background reader/fanout wrappers, drawer_watch), and two samplers at
    # once append in the order they reach `>>`, not the order they read: a 0 read
    # 19-24ms BEFORE a 32768 landed AFTER it -- a "drop" the counter never made
    # (2/56 under -n 14). The counter's shell writes (`: > bytes.N`, `cur`) are
    # lc_stream's, in the main shell, so every stop at them is still sampled.
    t += ('_sample() { [ "$BASHPID" = "$_smp_main" ] || return 0; '
          '[ -z "$_smp_busy" ] || return 0; _smp_busy=1; '
          '_smp_c=$(lc_gui_run 2>/dev/null | sed -n "s/^lc_run=//p" | cut -d"|" -f3); '
          'printf "%s|%s\\n" "$(cat "$LC/run/cur" 2>/dev/null)" "$_smp_c" >> "$SAMPLES"; _smp_busy=; }; ')
    t += 'set -T; trap _sample DEBUG; '
    t += 'lc_stream 1 dd sdc sda sdb; echo "rc1=$?"; lc_stream 2 dd sdc sda sdb; echo "rc2=$?"; trap - DEBUG; set +T'
    repo_entries = set(REPO.iterdir())       # run()'s cwd, where the stray links landed (#1748)
    out = run(tmp_path, t, timeout=300, WAIT_HELPER_S="20", WAIT_DRAWER_S="20")
    assert out.returncode == 0 and "rc1=0" in out.stdout and "rc2=0" in out.stdout, out.stdout + out.stderr
    assert set(REPO.iterdir()) == repo_entries, sorted(map(str, set(REPO.iterdir()) - repo_entries))
    rows = []
    for line in samples.read_text().splitlines():
        cur, _, copied = line.rpartition("|")
        assert copied.isdigit(), line                    # a counter that could not be read is not a sample
        rows.append((cur, int(copied)))
    return rows


def test_the_counter_only_grows_and_never_shows_a_partition_twice(tmp_path):
    rows = _drive(tmp_path)
    counts = [c for _, c in rows]
    assert len(rows) > 50, len(rows)                     # the trap really stopped at the writer's commands
    # the whole copy is in the samples: nothing, the first partition, both
    assert 0 in counts and FIRST in counts and FIRST + SECOND in counts, sorted(set(counts))
    # never down: #1442's base truncated before it was written
    drops = [(a, b) for a, b in zip(counts, counts[1:]) if b < a]
    assert not drops, drops
    # never more than has been read: #1619's finished partition counted twice.
    # Until the second partition begins (cur 2|copy) at most the first is read.
    first_only = [(cur, c) for cur, c in rows if not cur.startswith("2|")]
    assert first_only and all(c <= FIRST for _, c in first_only), [r for r in first_only if r[1] > FIRST]
    assert all(c <= FIRST + SECOND for _, c in rows), sorted(set(counts))


def test_copied_is_the_finished_partitions_plus_the_live_one(tmp_path):
    r = tmp_path / "run" / "lc" / "run"; r.mkdir(parents=True)
    (r / "bytes.1").write_text("1000\n", newline="\n")           # finished
    (r / "bytes.2").write_text("12\n250\n", newline="\n")        # live: pv -n prints a number per line
    (r / "cur").write_text("2|copy\n", newline="\n")
    out = run(tmp_path, 'lc_gui_run')
    assert out.returncode == 0, out.stderr
    assert next(l for l in out.stdout.splitlines() if l.startswith("lc_run=")).split("|")[2] == "1250", out.stdout
