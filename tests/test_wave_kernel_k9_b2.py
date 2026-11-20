"""#3064 (K9): the agent sends each drawer's kernel lines in the wave's report (``wave_senses.kernel``,
`agent/lib/restore/wavesense.sh`, `_ws_kernel`) -- so the server's cause of a disk it fails mid-wave has them THEN.

Hardware, 15/11: the agent's own capture ("sda: 0 kernel lines kept for the report (ata2)", failmark.sh `ata_capture`)
ran at 10:26:25, after the server had closed the round (10:26:24). `ata_capture` runs only once a write failed.

**Principle 5:** a list, even an empty one, is "dmesg was read and has nothing about this port"; ``null`` is "not read" --
no dmesg, an empty dmesg, or a drawer whose ATA port is unknown. The two are never the same.

**Negative control:** with `wavesense.sh` back at the base there is no ``kernel`` key, and every test here fails on it.
"""

from __future__ import annotations

import pytest

from test_wave_senses_3041 import _unit, needs_bash

LINES = [
    "[   10.000001] ata2.00: exception Emask 0x0 SAct 0x0 SErr 0x0 action 0x0",          # before the target began (since=50)
    "[   60.100000] ata2: SATA link up 6.0 Gbps (SStatus 133 SControl 300)",              # noise after a reset
    "[  120.000001] ata2.00: exception Emask 0x10 SAct 0x0 SErr 0x4050000 action 0xe frozen",
    "[  120.000002] ata2: SError: { PHYRdyChg CommWake 10B8B DevExch }",
    "[  120.000003] ata3.00: exception Emask 0x0 SAct 0x0 SErr 0x0 action 0x0",           # another drawer's port
    "[  120.000004] ata2: hard resetting link",
]

#: sda sits on ata2 and began at 50 s of uptime; sdb has no ATA port (as an NVMe or USB drive)
SETUP = r'''
up 100; cpus "1 0 0 1 0 0 0 0" "1 0 0 1 0 0 0 0"; nodisks
ata_port_of() { case "$1" in sda) echo 2 ;; esac; }
echo 50 > "$RUN_DIR/targets/sda/since"
DMESG=fakedmesg; fakedmesg() { cat "$RUN_DIR/dmesg.txt"; }
'''


def _run(tmp_path, dmesg: list[str] | None, extra: str = "") -> dict:
    body = SETUP + extra
    if dmesg is not None:
        (tmp_path / "dmesg.txt").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "dmesg.txt").write_text("\n".join(dmesg) + ("\n" if dmesg else ""), encoding="utf-8", newline="\n")
    got = _unit(tmp_path, body + r'''
pass
up 106; cpus "2 0 0 2 0 0 0 0" "2 0 0 2 0 0 0 0"; tick; show w1
''')
    return got["w1"]


@needs_bash
def test_a_drawer_gets_the_ata_lines_of_its_port_since_its_target_began(tmp_path):
    """(neg) the lines of ata2 from the first exception, without the noise, the other port, or what came before."""
    w1 = _run(tmp_path, LINES)
    assert w1["windows"] == 1
    assert w1["kernel"]["sda"] == [LINES[2], LINES[3], LINES[5]], w1["kernel"]
    assert w1["kernel"]["sdb"] is None, "a drawer with no ATA port was given a kernel"


@needs_bash
def test_a_dmesg_that_was_read_and_has_nothing_is_an_empty_list_and_not_null(tmp_path):
    w1 = _run(tmp_path, ["[    1.000000] Linux version 6.1", "[  120.000003] ata3.00: exception Emask 0x0 SAct 0x0"])
    assert w1["kernel"] == {"sda": [], "sdb": None}, w1["kernel"]


@needs_bash
def test_a_dmesg_that_could_not_be_read_is_null_for_every_drawer(tmp_path):
    """(neg) the command fails, or says nothing at all: nothing is known -- not 'a clean kernel'."""
    failing = _run(tmp_path, None, r'''fakedmesg() { return 1; }''')
    assert failing["kernel"] == {"sda": None, "sdb": None}, failing["kernel"]
    empty = _run(tmp_path / "empty", [])
    assert empty["kernel"] == {"sda": None, "sdb": None}, empty["kernel"]


@needs_bash
def test_an_agent_without_the_port_helper_says_null_and_does_not_stop_the_window(tmp_path):
    w1 = _run(tmp_path, LINES, r'''unset -f ata_port_of''')
    assert w1["kernel"] == {"sda": None, "sdb": None}
    assert w1["windows"] == 1 and w1["nic"] is not None


@needs_bash
def test_a_drawer_that_left_sysfs_keeps_the_port_it_was_seen_on(tmp_path):
    """failmark.sh keeps `serial|port|ata` in targets/<d>/seen (#1602): a disk that is gone has no sysfs path."""
    w1 = _run(tmp_path, LINES, r'''ata_port_of() { :; }; echo "SER|3|2" > "$RUN_DIR/targets/sda/seen"''')
    assert w1["kernel"]["sda"] == [LINES[2], LINES[3], LINES[5]], w1["kernel"]


@pytest.mark.parametrize("n", [30])
@needs_bash
def test_the_lines_are_capped_at_twenty(tmp_path, n):
    many = [f"[  {200 + i}.000000] ata2: SError: {{ 10B8B }} {i}" for i in range(n)]
    w1 = _run(tmp_path, many)
    assert len(w1["kernel"]["sda"]) == 20 and w1["kernel"]["sda"][0] == many[0]


@needs_bash
def test_a_line_with_quotes_and_backslashes_stays_valid_json(tmp_path):
    w1 = _run(tmp_path, ['[  120.000001] ata2.00: exception Emask 0x10 "quoted" and a \\ backslash'])
    assert w1["kernel"]["sda"] == ['[  120.000001] ata2.00: exception Emask 0x10 "quoted" and a \\ backslash']
