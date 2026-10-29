"""#2207 -- a local clone to a smaller target shrinks the source's RAM view only to
what the smallest target holds, not to ntfsresize's minimum + margin.

Metal, cloner 2, 26/10 (``C:\\ImageCtl-Lab\\metal-1026-shrink\\reloc-curve.txt``):
a 500GB source with 75GB used, a 256GB target. Shrunk to 82.66GB (the plan of
the day) the view had to move 28,352 MB -- 31.4 GB of RAM by the budget, refused
on a 12GB machine; shrunk to 157GB or more it moves 0 MB.

The capture (shrink.sh) keeps its minimum on purpose -- a small image -- and
does not go through ``lc_shrink_source``; ``test_the_capture_plan_is_still_the_minimum``
holds that line.

**Negative control** (Testrunner, 26/10): with ``agent/lib/lcplan.sh`` and
``agent/lib/localclone.sh`` at the base commit, 5 fail -- the 256GB case (refused:
28352 MB to move, 31443 MB of RAM, 11264 MB there), the smallest of several, the
unread size (no log line), the measure plan, and lc_run's targets (none passed).
4 pass in both states, the guards: never below the minimum, the minimum's refusal
by name, no targets, and the capture's plan.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, posix
from test_lc_readonly_source import NEW_BYTES, PARTS, PLAN, SHOW, setup, source_writes
from test_local_clone_agent import run

pytestmark = requires_native(("bash", BASH))

MIB = 1048576
GPT_TAIL = 262144
MARGIN = 1 << 30
#: what the source's other partitions take on a target: p1 at 2048 + 204800, the
#: gap to p3 (1085440), and p4 (2048000 sectors) moved back behind p3
P3_START = 1085440 * 512
P4 = 2048000 * 512
CUR = 998244352 * 512
#: Samsung MZ7LN256, the metal target (500118192 sectors)
T256 = 500118192 * 512
T300 = 585937500 * 512

#: ntfsresize -n as metal answered it on the 870 (reloc-curve.txt): nothing to move at
#: 157 GB and above, 28,352 MB at the plan's own 82.66 GB -- here, under 150 GB
RELOC_BY_SIZE = r'''
ntfsresize() {
    ord "ntfsresize $*"
    case "$*" in
        "-n "*) if [ "$3" -ge 150000000000 ]; then echo "Needed relocations : 0 (0 MB)"
                else echo "Needed relocations : 6921875 (28352 MB)"; fi ;;
        "-s "*) cat > /dev/null; return 0 ;;
    esac
}
'''


def targets(**sizes: int) -> str:
    """Each target disk as the kernel lists it: /sys/block/<dev>/size, in sectors."""
    return "".join(f'mkdir -p "$SYSROOT/sys/block/{d}"; echo {b // 512} > "$SYSROOT/sys/block/{d}/size"; '
                   for d, b in sizes.items())


def shrink_for(tmp_path: Path, devs: str, pre: str = "", **env) -> tuple[dict[str, str], list[str], Path]:
    out = run(tmp_path, setup() + RELOC_BY_SIZE + pre + f'lc_shrink_source sdc "{devs}"; _rc=$?; ' + SHOW
              + 'echo "plan=$(cat "$LC/run/shrink.plan")"; ', **env)
    assert out.returncode == 0, out.stderr
    got = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line and not line.startswith("imagectl:"))
    log = tmp_path / "run" / "order.log"
    return got, (log.read_text(encoding="utf-8").splitlines() if log.exists() else []), tmp_path / "run"


def new_size(got: dict[str, str]) -> int:
    (line,) = got["plan"].splitlines()
    return int(line.split("|")[6])


def expected(target: int) -> int:
    """The largest size, whole MiB below the current one, whose compacted layout ends
    GPT tail + margin before the target."""
    room = target - GPT_TAIL - MARGIN - P3_START - P4
    give = (CUR - room + MIB - 1) // MIB * MIB
    return CUR - give


def test_a_256gb_target_takes_the_source_shrunk_to_its_own_size_with_nothing_moved(tmp_path):
    """The metal case: 12 GB of RAM, a 256GB target. The minimum needed 28,352 MB moved
    (refused); the target's own size needs none, and the copy goes on."""
    got, order, run_dir = shrink_for(tmp_path, "sda", targets(sda=T256), MEM_KIB=str(12 * 1024 * 1024))
    assert got["rc"] == "0" and got["path"] == "snapshot", got
    n = new_size(got)
    assert n == expected(T256), (n, expected(T256))
    assert T256 - 2 * MARGIN < n + P3_START + P4 < T256 - MARGIN, n      # close to the target, inside it
    assert (CUR - n) % MIB == 0, n                                      # the tail moves aligned
    assert got["relocations_mb"] == "0", got                            # no RAM for moving data
    view = f"{posix(run_dir / 'devroot')}/mapper/lc-src-3"
    assert f"ntfsresize -n -s {n} --no-progress-bar {view}" in order, order
    assert source_writes(order, posix(run_dir / "devroot")) == []
    # the compacted layout the targets get ends inside the smallest one, GPT backup included
    ends = [(int(f[3]) + int(f[4])) * 512 for f in (l.split("|") for l in (run_dir / "lc" / "run" / "parts.txt").read_text().splitlines())]
    assert max(ends) + GPT_TAIL <= T256 - MARGIN, ends
    assert "smallest target" in (run_dir / "agent.log").read_text(encoding="utf-8")


def test_several_targets_shrink_for_the_smallest(tmp_path):
    got, _, _ = shrink_for(tmp_path, "sdb sda", targets(sda=T256, sdb=T300))
    assert got["rc"] == "0", got
    assert new_size(got) == expected(T256), (new_size(got), expected(T256), expected(T300))


def test_never_below_the_measured_minimum(tmp_path):
    """A target that takes the source only at its minimum: the plan as measured, not
    less -- the verdict (lc_run) already refused anything smaller, as before."""
    tight = NEW_BYTES + P3_START + P4 + GPT_TAIL + MARGIN // 2
    got, _, _ = shrink_for(tmp_path, "sda", targets(sda=tight), MEM_KIB=str(64 * 1024 * 1024))
    assert got["rc"] == "0", got
    assert new_size(got) == NEW_BYTES, new_size(got)


def test_with_the_minimum_a_too_small_memory_is_still_refused_by_name(tmp_path):
    """Where only the minimum fits, its relocations are the budget's -- refused by name, as today."""
    tight = NEW_BYTES + P3_START + P4 + GPT_TAIL + MARGIN // 2
    got, _, _ = shrink_for(tmp_path, "sda", targets(sda=tight), MEM_KIB=str(12 * 1024 * 1024))
    assert got["rc"] == "1" and got["path"] == "refused", got
    assert "להזזת 28352 MB נדרשים" in got["error"] and "המקור לא נכתב" in got["error"], got


def test_a_target_whose_size_does_not_read_keeps_the_measured_plan(tmp_path):
    """Principle 5: no size -- no guess. The plan as measured (today's), and the log says why."""
    got, _, run_dir = shrink_for(tmp_path, "sda sdz", targets(sda=T256), MEM_KIB=str(64 * 1024 * 1024))
    assert got["rc"] == "0", got
    assert got["plan"] == PLAN.strip(), got["plan"]
    assert "no target size" in (run_dir / "agent.log").read_text(encoding="utf-8")


def test_no_targets_keeps_the_measured_plan(tmp_path):
    got, _, _ = shrink_for(tmp_path, "", MEM_KIB=str(64 * 1024 * 1024))
    assert got["rc"] == "0" and got["plan"] == PLAN.strip(), got


def test_the_measure_screen_keeps_the_minimum_for_the_verdict(tmp_path):
    """lc_measure's plan -- what decides fits/shrink/short -- is not rewritten by the run."""
    got, _, run_dir = shrink_for(tmp_path, "sda", targets(sda=T256), MEM_KIB=str(12 * 1024 * 1024))
    assert got["rc"] == "0", got
    assert (run_dir / "lc" / "measure" / "sdc.plan").read_text() == PLAN


def test_lc_run_passes_the_targets_that_need_the_shrink(tmp_path):
    """lc_run hands lc_shrink_source the smaller targets -- not the ones that fit as they are."""
    stubs = ('mkdir -p "$LC/inv" "$LC/measure"; for d in sda sdb sdd; do : > "$LC/inv/$d.line"; done; '
             'echo "done|1|" > "$LC/measure/sdc"; echo sdc > "$LC/source"; '
             'lc_inv_field() { case "$2" in 12) echo 1 ;; 4) printf "SER-%s" "$1" ;; esac; }; '
             'disk_serial() { printf "SER-%s" "$1"; }; smart_red() { return 1; }; '
             'lc_verdict_for() { case "$2" in sdd) echo "fits|1|" ;; *) echo "shrink|1|" ;; esac; }; '
             'lc_source_parts() { return 0; }; lc_manifest() { return 1; }; lc_end() { :; }; '
             'lc_shrink_source() { echo "ARGS=$1|$(echo $2)"; LC_ERROR=stop; return 1; }; ')
    out = run(tmp_path, stubs + 'lc_run sdc "sda sdb sdd" 1; ')
    (line,) = [l for l in out.stdout.splitlines() if l.startswith("ARGS=")]
    assert line == "ARGS=sdc|sda sdb", (line, out.stderr)


def test_the_capture_plan_is_still_the_minimum(tmp_path):
    """shrink.sh's plan (shrink_plan) is untouched: minimum + margin, a small image."""
    out = run(tmp_path, setup() + 'mkdir -p "$SYSROOT/sys/block/sdc"; '
              'shrink_plan sdc "$LC/run/parts.txt" 512; echo "rc=$?"; cat "$RUN_DIR/shrink.plan"')
    assert out.returncode == 0, out.stderr
    (line,) = [l for l in out.stdout.splitlines() if l.startswith("3|")]
    # --info says 100,000,000,000; + 10% margin, MiB up; what is cut stays whole MiB
    want_min = (int(100_000_000_000 * 1.1) + MIB - 1) // MIB * MIB
    assert int(line.split("|")[6]) == CUR - (CUR - want_min) // MIB * MIB, line
