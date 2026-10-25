"""#1714 -- every sgdisk that *writes* sleeps a whole second inside gdisk.

``DiskIO::DiskSync()`` (gptfdisk, ``diskio-unix.cc``) runs ``sleep(1)`` before
``BLKRRPART`` on Linux. Before #1714 a Windows image with a recovery partition
in the tail cost ~10 such calls per disk: ``--zap-all``, one ``-n`` call per
partition, ``-U``, ``-e``; then the expansion: ``-d`` for the tail, ``-n`` for
the tail, ``-d/-n`` for the candidate. Every drawer, every pull, every station
and every local clone paid them before the first byte.

Here: a counting sgdisk stub (``test_restore_evidence.build_box``). The table
is written by ``--zap-all`` + ONE call, the expansion by ONE call, the local
clone's names by ONE call (since #1798 that call is writemark_clear's). Negative control: on the code before #1714 the
counts are 6 / 3 / 3 and every test here fails.

That the one call writes the SAME table, byte for byte, as the separate calls
did: real sgdisk on sparse files, test_sgdisk_one_call_real_1714.py.
"""

from __future__ import annotations

import pytest

from test_agent import AGENT, BASH, posix, sh
from test_restore_evidence import PLAN, build_box, log_of, rc_of

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

DISK_GUID = "047B3400-0000-0000-0000-0000003AEE00"
ATTRS = "8000000000000001"


def _writes(box) -> list[str]:
    """The sgdisk calls that write: not a read (-p/-i), not the --pretend check."""
    path = box / "sgdisk.calls"
    calls = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [c for c in calls
            if not c.startswith(("-p ", "-i ")) and " --pretend " not in f" {c} "]


def test_apply_gpt_writes_the_whole_table_in_one_call_after_the_zap(tmp_path):
    plan = [line + f"|{ATTRS}" for line in PLAN]   # PLAN ends with the empty uuid field
    box, run, prelude = build_box(tmp_path, plan=plan, disk_guid=DISK_GUID)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", (out, log_of(run))
    writes = _writes(box)
    assert len(writes) == 2, writes   # before #1714: zap + 3 x -n + -U + -e = 6
    assert writes[0].startswith("--zap-all "), writes
    one = writes[1]
    # the order of the separate calls, which is the order gptcl.cc applies them in
    assert one.startswith("-a 1 -n 1:2048:206847 -t 1:C12A7328-F81F-11D2-BA4B-00A0C93EC93B "
                          "-u 1:11111111-1111-1111-1111-111111111111 "
                          f"-A 1:=:0x{ATTRS} -n 2:206848:"), one
    assert one.endswith(f" -U {DISK_GUID} -e {posix(box)}/dev/sda"), one
    for index in ("1", "2", "3"):
        assert f" -n {index}:" in one and f" -u {index}:" in one and f" -A {index}:=:0x{ATTRS}" in one
    # the evidence is still read back from the disk, not inferred from the exit code
    log = log_of(run)
    assert "all 3 partitions are live block devices" in log
    assert f"disk GUID {DISK_GUID} came back from the disk" in log


def test_the_expansion_is_one_call(tmp_path):
    box, run, prelude = build_box(tmp_path, disk_guid=DISK_GUID)
    (box / "sys" / "block" / "sda").mkdir(parents=True)
    (box / "sys" / "block" / "sda" / "size").write_text("976773168\n")
    out = sh(prelude + 'apply_gpt sda m.json || { echo "rc=apply"; exit; }; '
             + ': > "$RUN_DIR/../sgdisk.calls"; expand_last sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", (out, log_of(run))
    writes = _writes(box)
    assert len(writes) == 1, writes   # before #1714: -d tail, -n tail, -d/-n candidate = 3
    # the tail (recovery, 3) leaves first and comes back at the end of the disk;
    # then the candidate (windows, 2) is rebuilt from its own start to the tail
    assert writes[0].startswith("-a 1 -d 3 -n 3:"), writes
    assert " -d 2 -n 2:206848:0 -t 2:EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 " in writes[0], writes
    assert (run / "targets" / "sda" / "expanded").read_text().strip() == "2|ntfs"


def test_a_refused_expansion_call_fails_by_name(tmp_path):
    """One call that sgdisk refused is the expansion that did not happen -- named
    in the log, no marker (principle 5). The caller falls back to apply_gpt."""
    box, run, prelude = build_box(tmp_path, sgdisk_fail=["-a 1 -d *"])
    (box / "sys" / "block" / "sda").mkdir(parents=True)
    (box / "sys" / "block" / "sda" / "size").write_text("976773168\n")
    out = sh(prelude + 'expand_last sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "sgdisk refused the expanded table (one call): -a 1 -d 3 " in log_of(run)
    assert not (run / "targets" / "sda" / "expanded").exists()


ENTRIES = ["1|EFI system partition|8000000000000000",
           "2|Microsoft reserved partition|0",
           "3||0",
           "4|Basic data partition|0"]


def _names(tmp_path, entries, fail=False, marked=(1, 2, 3, 4)):
    """#1798: the local clone's names come back through writemark_clear -- it
    reads which partitions carry the mark (-p), writes them in ONE call, reads
    back with -i, one partition at a time. The stub's -p lists the marked rows
    until a write happened; -i says the name its -c wrote."""
    lc = tmp_path / "lc"
    (lc / "run").mkdir(parents=True)
    (lc / "run" / "entries").write_text("\n".join(entries) + "\n", encoding="utf-8", newline="\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    calls, done = tmp_path / "calls", tmp_path / "written"
    rows = "".join(f"echo '   {i}   2048   4095   1.0 MiB   0700  imagectl-incomplete'\n" for i in marked)
    (stubs / "sgdisk").write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = -p ]; then [ -f {posix(done)} ] || {{\n{rows}:\n}}; exit 0; fi\n"
        f"if [ \"$1\" = -i ]; then echo \"Partition name: '$(cat {posix(tmp_path)}/name.$2)'\"; exit 0; fi\n"
        f"p=; for a in \"$@\"; do [ \"$p\" = -c ] && printf '%s\\n' \"${{a#*:}}\" > {posix(tmp_path)}/name.${{a%%:*}}; p=$a; done\n"
        f"for a in \"$@\"; do printf '[%s]' \"$a\"; done >> {posix(calls)}\n"
        f"echo >> {posix(calls)}\n"
        f": > {posix(done)}\n"
        f"exit {1 if fail else 0}\n", encoding="utf-8", newline="\n")
    out = sh(f'chmod 0755 {posix(stubs)}/sgdisk; export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
             f'LC={posix(lc)!r} DEVROOT=/nodev LOG_FILE={posix(tmp_path / "log")!r}; log() {{ :; }}; '
             f'. {posix(AGENT)}/lib/writemark.sh; writemark_clear sdb "$LC/run/entries"; echo "rc=$?"')
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return rc_of(out), lines


def test_the_local_clone_names_are_one_call_and_keep_their_spaces(tmp_path):
    rc, lines = _names(tmp_path, ENTRIES)
    assert rc == "rc=0"
    # #1798: an empty source name is written too -- it replaces the mark
    assert lines == ["[-c][1:EFI system partition][-c][2:Microsoft reserved partition]"
                     "[-c][3:][-c][4:Basic data partition][/nodev/sdb]"], lines   # before #1714: 3 calls


def test_a_refused_names_call_is_a_failure_and_no_mark_is_no_call(tmp_path):
    rc, _lines = _names(tmp_path, ENTRIES, fail=True)
    assert rc == "rc=1", "sgdisk -c refused, and writemark_clear said 0"
    (tmp_path / "none").mkdir()
    rc, lines = _names(tmp_path / "none", ["1||0", "2||0"], marked=())
    assert (rc, lines) == ("rc=0", []), "a table without the mark is not written"
