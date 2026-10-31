"""#1657: target progress cannot dip or count a partition twice.

The real ``target_partition_done`` is stopped before each command with bash's
DEBUG trap.  ``target_bytes`` is sampled at every stop, including after an
outer ``> base`` has truncated the file and before its command substitution,
and between writing that base and emptying the old live counter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, posix

pytestmark = requires_native(("bash", BASH))

FIRST, SECOND = 32768, 12288


def test_target_bytes_only_grows_and_never_counts_a_partition_twice(tmp_path: Path):
    progress = Path(__file__).parents[1] / "agent" / "lib" / "core" / "progress.sh"
    samples = tmp_path / "samples"
    script = f"""
set -eu
RUN_DIR={posix(tmp_path / 'run')!r}; SYSROOT={posix(tmp_path / 'sys')!r}
mkdir -p "$RUN_DIR/targets" "$SYSROOT/proc"
printf '1 0\n' > "$SYSROOT/proc/uptime"
. {posix(progress)!r}
target_ident_keep() {{ :; }}   # #1602's identity (failmark.sh, sysfs): not what is counted here
target_init sda {FIRST + SECOND}
SAMPLES={posix(samples)!r}; _busy=
_sample() {{
    [ -z "$_busy" ] || return 0
    _busy=1
    printf '%s\n' "$(target_bytes sda)" >> "$SAMPLES"
    _busy=
}}
printf '%s\n' {FIRST} > "$(_counter_of sda)"
set -T; trap _sample DEBUG
target_partition_done sda
printf '%s\n' {SECOND} > "$(_counter_of sda)"
target_partition_done sda
trap - DEBUG; set +T
printf 'final=%s\n' "$(target_bytes sda)"
"""
    out = subprocess.run(
        [BASH, "-c", script], text=True, capture_output=True, timeout=30,
        stdin=subprocess.DEVNULL,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    counts = [int(line) for line in samples.read_text().splitlines()]
    assert len(counts) > 10, counts
    assert FIRST in counts and FIRST + SECOND in counts, sorted(set(counts))
    drops = [(a, b) for a, b in zip(counts, counts[1:]) if b < a]
    assert not drops, drops
    assert all(value <= FIRST + SECOND for value in counts), sorted(set(counts))
    assert f"final={FIRST + SECOND}" in out.stdout, out.stdout
