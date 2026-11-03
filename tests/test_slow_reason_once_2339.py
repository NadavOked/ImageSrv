"""#2339 -- a disk failed as slow says why **once**.

The root, in the code: ``write_speed_verdict`` (writecheck.sh) writes the one
sentence to two files -- ``write_check_reason`` and, through ``target_set
failed``, ``error``. ``end_problem`` (idle.sh) reads both: the failed state
adds ``error``, the ``slow`` check adds ``write_check_reason``, joined by
`` · ``. ``end.problems`` is the one source of the console's
``held.problems[].reason`` (hold.sh) and of the drawer's line on the native
screen (``end_problem=`` -> ``end_problem_of``), so both showed it twice
(03/11, cloner 2: "איטי — 33 MB/s, מתחת לסף 50 MB/s · איטי — 33 MB/s, …").

The chain here is the real one: ``write_speed_verdict`` writes the files,
``end_problems`` reads them. A second reason that is really different stays.

**Negative control** (in the PR): with the base's idle.sh the first test fails
with the sentence twice.
"""

from __future__ import annotations

from pathlib import Path

from agentlib import agent_sources
from native import requires_native
from test_smart import BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

SLOW = "איטי — 33 MB/s, מתחת לסף 50 MB/s"


def chain(tmp_path: Path, extra: dict[str, str] | None = None, after: str = "") -> dict[str, tuple[str, str]]:
    """sdc wrote 33 MB/s, sdb 400; the verdict, sdb written, ``after``, then the end's problems."""
    run = tmp_path / "run"
    for disk, mbps in (("sdb", 400), ("sdc", 33)):
        t = run / "targets" / disk
        t.mkdir(parents=True)
        (t / "state").write_text("writing\n", newline="\n")
        (t / "write_check_state").write_text("ok", newline="\n")
        (t / "write_check_mbps").write_text(str(mbps), newline="\n")
        (t / "readback").write_text("sampled\n", newline="\n")
        q = tmp_path / "sysroot" / "sys" / "block" / disk / "queue"
        q.mkdir(parents=True)
        (q / "rotational").write_text("0\n", newline="\n")
    for name, text in (extra or {}).items():
        (run / "targets" / "sdc" / name).write_text(text + "\n", encoding="utf-8", newline="\n")
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(tmp_path / 'sysroot')!r} LOG_FILE={posix(run / 'agent.log')!r}; "
             f"{agent_sources('common', 'waits', 'sysinfo', 'progress', 'failmark', 'writecheck', 'idle')}"
             f"write_speed_verdict sdb sdc; target_set sdb done; {after} end_problems sdb sdc; echo \"RC $?\"")
    assert out.stdout.strip().endswith("RC 0"), out.stdout + out.stderr   # 0 = a problem was found
    rows = (run / "end.problems").read_text(encoding="utf-8").splitlines()
    return {r.split("|")[0]: tuple(r.split("|", 2)[1:]) for r in rows}


def test_a_disk_failed_as_slow_says_why_once(tmp_path):
    got = chain(tmp_path)
    assert got == {"sdc": ("r", SLOW)}, got


def test_written_anyway_after_red_still_says_it_once_and_stays_red(tmp_path):
    # the operator let the red disk write; it ended done, its error file still the verdict
    got = chain(tmp_path, after='target_set sdc done;')
    assert got == {"sdc": ("r", SLOW)}, got


def test_a_different_second_reason_is_kept(tmp_path):
    got = chain(tmp_path, after='target_set sdc skipped "SMART אדום";')
    assert got == {"sdc": ("r", f"SMART אדום · {SLOW}")}, got
