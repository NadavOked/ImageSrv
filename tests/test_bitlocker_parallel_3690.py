"""#3690 -- `bitlocker_state` of two drawers, side by side, must not share a temp file.

Since #3646 `idle_wc_scan` starts one subshell per drawer: `( idle_wc_disk "$d" ) &`. `$$` inside a subshell is the PARENT's pid, so
`bitlocker_state` named its two temp files the same for every drawer (`bitlocker.raw.$$`, `bitlocker.text.$$`), and the first drawer's
`rm -f` took the file out from under the next one's `tr`. Cloner 2, 18/11, cold boot 07:50:43Z: two Samsung drives with a plain NTFS
header came out "unchecked: BitLocker (tr rc=1)" in the same second, and sat without a speed check.

Here two subshells run side by side over two nodes -- one with a plain NTFS header, one with the `-FVE-FS-` signature -- with `tr`
held for a second (a shim) so both `dd` reads are done before either `tr` starts: the collision is the order of events, not luck.
A shared file makes the clear drawer read the encrypted drawer's header (or lose its own). The fix gives every call its own file.

**Negative control:** `agent/lib/capture/hibernation.sh` back at the base (`9ab7a1a4a`) -- the clear drawer comes out "encrypted"
(or "not checked"), and the no-temp-file case says "clear".
"""

from __future__ import annotations

from pathlib import Path

from test_agent import AGENT, posix, sh
from test_timeouts import make_stubs

NTFS_HEADER = b"\xeb\x52\x90NTFS    " + b"\x00" * 3 + b"\x02" + b"MSDOS5.0" + b"\x00" * 490   # one sector: plain, no BitLocker
FVE_HEADER = b"\xeb\x58\x90-FVE-FS-" + b"\x00" * 3 + b"\x02" + b"\x00" * 490                    # one sector: BitLocker's signature

#: `tr` that waits first: both dd reads are over before either tr starts
SLOW_TR = '#!/bin/sh\nsleep 1\nexec "$REAL_TR" "$@"\n'


def _two_drawers(tmp_path: Path, nodes: list[bytes], stubs: dict | None = None) -> tuple[list[str], list[str]]:
    """Each node's `bitlocker_state` in a subshell, all started together. Returns (their answers, what is left in RUN_DIR)."""
    run = tmp_path / "run"; run.mkdir(parents=True)
    calls = []
    for i, body in enumerate(nodes):
        (tmp_path / f"n{i}").write_bytes(body)
        calls.append(f'( bitlocker_state {i + 1} ntfs {posix(tmp_path)}/n{i} > {posix(tmp_path)}/out{i} ) &')
    script = (
        'export REAL_TR=$(command -v tr)\n'
        + make_stubs(tmp_path / "stubs", {"tr": SLOW_TR, **(stubs or {})})
        + f'RUN_DIR={posix(run)!r}; LOG_FILE={posix(run / "agent.log")!r}; touch "$LOG_FILE"; '
        + f'. {posix(AGENT)}/lib/capture/hibernation.sh\n'
        + "\n".join(calls) + "\nwait\n"
        + "; ".join(f'cat {posix(tmp_path)}/out{i}' for i in range(len(nodes)))
    )
    out = sh(script)
    left = sorted(p.name for p in run.iterdir() if p.name != "agent.log")
    return out.split("\n")[:len(nodes)], left


def test_two_drawers_side_by_side_each_get_their_own_header(tmp_path):
    """The positive evidence is per drawer: the plain NTFS drawer says clear and the BitLocker one says encrypted, in the SAME run --
    with one shared file the first is told about the second's header."""
    answers, left = _two_drawers(tmp_path, [NTFS_HEADER, FVE_HEADER])
    assert answers == ["clear", "encrypted"], answers
    assert left == [], f"temp files left behind: {left}"


def test_three_plain_drawers_side_by_side_are_all_clear(tmp_path):
    """The hardware's case: three drawers, none with BitLocker (the issue's DoD 2). Nothing is "not checked"."""
    answers, left = _two_drawers(tmp_path, [NTFS_HEADER, NTFS_HEADER, NTFS_HEADER])
    assert answers == ["clear", "clear", "clear"], answers
    assert left == [], left


def test_a_temp_file_that_cannot_be_made_is_not_clear(tmp_path):
    """Principle 5: when the check could not even start, the answer is a reason, never `clear`."""
    answers, _ = _two_drawers(tmp_path, [NTFS_HEADER], stubs={"mktemp": "#!/bin/sh\nexit 1\n"})
    assert answers[0].startswith("לא הצלחנו לבדוק BitLocker"), answers
    assert answers[0] != "clear"
