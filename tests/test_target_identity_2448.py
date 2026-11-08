"""#2448 -- the two gaps left after #2434: a factory-twin serial, and a slot that is not a disk.

1. **The same serial from the factory.** #2434 binds serial|sectors|slot at
   ``target_init`` and asks it again before every write (``assert_target``). Two
   cheap drives of one model and size with one serial (``0000000000``) are the same
   identity without the slot: drawer 2 moved into slot 1 answers to drawer 1's
   ``bound``, and was written. Now a disk with a twin here -- now, or at
   ``target_init`` (``twin``) -- fails by name and is not written; each twin that is
   a target fails under its own name. An empty serial is an identity too: one disk
   without a serial (a build/student station, a VM) has no twin and is written.
2. **The room chooses a slot, not a disk.** ``target_init`` runs after the hello,
   the manifest and ``session_stream``; a disk pushed into the chosen slot in that
   window was bound and written. The server now sends ``session.target_disks`` --
   the serial and size that this hello's inventory (what the console shows) has in
   each slot -- and ``drawers_bound`` compares what ``target_init`` bound with it.
   Another disk: "המגירה הוחלפה אחרי שנבחרה", not written. No field (a server from
   before): the slot alone, as before.

The identity is what sysfs gives (``device/serial``, ``size``: real files in the box);
only ``disk_port`` is a stub reading ``port.<dev>`` (Windows cannot build the
symlinked ``ataN`` path). The jq half (``resolve_target_drawers`` reading the field)
is at the bottom, and runs where jq is (the testrunner).

Negative control: every production file this fix touches (failmark.sh,
drawerselect.sh, room.py, hello.py) back to the base together (#439) -- see the
commit message for the output.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, REPO, posix

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "jsonq", "waits", "sysinfo", "diskinfo", "progress", "restore", "expand", "grow", "ntfsunhib",
        "expandshare", "writemark", "hostname", "hostname_linux", "writeback", "drawers", "verdict",
        "failmark", "ntfsboot", "postdeploy", "writecheck", "discard", "drawergate", "drawerselect", "cancel")

SECTORS = 976773168
TWIN_HE = "אותו מספר סידורי ואותו גודל"
SWAPPED_HE = "המגירה הוחלפה אחרי שנבחרה"


def run(tmp_path: Path, disks: dict[str, tuple[str | None, int, int]], bind: list[str],
        script: str, *, shown: list[str] | None = None, resp: dict | None = None) -> tuple[str, list[str], Path]:
    """``disks``: dev -> (serial or None, slot, sectors) in a fake sysfs; each of
    ``bind`` through ``target_init`` (as do_restore_drawers does); ``shown``: the
    lines of ``drawer_shown`` (port|size_bytes|serial), None = no file (an old
    server). Every destructive tool only records its call."""
    box = tmp_path / "box"
    run_dir = box / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    for dev, (serial, slot, sectors) in disks.items():
        d = box / "sys/block" / dev
        (d / "device").mkdir(parents=True, exist_ok=True)
        (d / "queue").mkdir(parents=True, exist_ok=True)
        (d / "size").write_text(f"{sectors}\n", newline="\n")
        (d / "queue/rotational").write_text("0\n", newline="\n")
        (d / "queue/discard_max_bytes").write_text("2147450880\n", newline="\n")
        if serial is not None:
            (d / "device/serial").write_text(serial + "\n", newline="\n")
        (box / f"port.{dev}").write_text(str(slot), newline="\n")
    if shown is not None:
        (run_dir / "drawer_shown").write_text("".join(f"{line}\n" for line in shown), newline="\n")
    if resp is not None:
        (box / "response.json").write_text(json.dumps(resp), encoding="utf-8")
    calls = box / "calls"
    pre = (f'export RUN_DIR={posix(run_dir)!r} SYSROOT={posix(box)!r} DEVROOT={posix(box)}/dev '
           f'RESP={posix(box)}/response.json LOG_FILE={posix(box)}/agent.log CALLS={posix(calls)!r} '
           'IMAGESRV_TEST=1; mkdir -p "$DEVROOT"; '
           + agent_sources(*LIBS)
           + 'disk_port() { cat "$SYSROOT/port.$1" 2>/dev/null; }; '
           + 'die_local() { echo "DIE: $*"; exit 0; }; '
           + 'for t in blkdiscard sgdisk dd mkswap partclone.ntfs; do eval "$t() { echo \\"$t \\$*\\" >> \\"\\$CALLS\\"; }"; done; '
           + 'ata_capture() { :; }; stage_at() { :; }; '
           + "".join(f"target_init {d} 1000; " for d in bind))
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + pre + script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, cwd=str(REPO), timeout=60)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return proc.stdout, lines, run_dir


def state(run_dir: Path, dev: str) -> tuple[str, str]:
    t = run_dir / "targets" / dev
    err = t / "error"
    return ((t / "state").read_text(encoding="utf-8").strip(),
            err.read_text(encoding="utf-8").strip() if err.exists() else "")


TWINS = {"sdb": ("0000000000", 1, SECTORS), "sdc": ("0000000000", 2, SECTORS)}


# --- 1. the same serial from the factory ------------------------------------------


def test_two_drawers_with_one_serial_are_each_refused_by_name(tmp_path):
    """Both chosen: neither is written, and each fails under its own name."""
    out, _calls, run_dir = run(tmp_path, TWINS, ["sdb", "sdc"],
                               'DRAWER_MAP=" sdb:1 sdc:2"; echo "out=$(drawers_bound sdb sdc)"')
    assert out.strip().endswith("out="), out
    for dev, other in (("sdb", "sdc"), ("sdc", "sdb")):
        st, err = state(run_dir, dev)
        assert st == "failed" and TWIN_HE in err, (dev, st, err)
        assert f"לדיסק {dev} ולדיסק {other}" in err, (dev, err)
        assert (run_dir / "targets" / dev / "foreign").exists(), dev


def test_drawer_2_moved_into_slot_1_is_not_written(tmp_path):
    """The Issue's scenario: slot 1 chosen (sdb); its twin sat in slot 2 at target_init.
    Drawer 1 is pulled and drawer 2 put in slot 1 -- under sdb, serial|sectors|slot is
    exactly what was bound. Before #2448 the TRIM reached blkdiscard."""
    out, calls, run_dir = run(tmp_path, TWINS, ["sdb"],
                              'rm -rf "$SYSROOT/sys/block/sdc"; '      # drawer 2 left slot 2 ...
                              'discard_before_restore sdb; echo "rc=$?"')   # ... and now answers to sdb in slot 1
    assert calls == [], (calls, out)
    st, err = state(run_dir, "sdb")
    assert st == "failed" and TWIN_HE in err, (st, err)


def test_a_twin_that_appears_after_the_choice_stops_the_next_write(tmp_path):
    """No twin at target_init, one now: the next destructive step asks again."""
    out, _calls, run_dir = run(tmp_path, {"sdb": TWINS["sdb"]}, ["sdb"],
                              'mkdir -p "$SYSROOT/sys/block/sdc/device"; echo 0000000000 > "$SYSROOT/sys/block/sdc/device/serial"; '
                              f'echo {SECTORS} > "$SYSROOT/sys/block/sdc/size"; echo 2 > "$SYSROOT/port.sdc"; '
                              'target_write_started sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert state(run_dir, "sdb")[0] == "failed"
    assert not (run_dir / "targets/sdb/write_started").exists()


def test_the_station_refuses_a_disk_with_a_twin_too(tmp_path):
    """assert_target is every path's (station, local clone): the write check fails."""
    out, calls, _run_dir = run(tmp_path, TWINS, ["sdb"], 'discard_before_restore sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=2") and calls == [], (out, calls)


def test_two_disks_without_a_serial_of_one_size_are_twins(tmp_path):
    """An empty serial is an identity too: two of them, one size -- indistinguishable."""
    out, calls, _ = run(tmp_path, {"sdb": (None, 1, SECTORS), "sdc": (None, 2, SECTORS)}, ["sdb"],
                        'discard_before_restore sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=2") and calls == [], (out, calls)


@pytest.mark.parametrize("disks", [
    {"sdb": ("S1", 1, SECTORS), "sdc": ("S2", 2, SECTORS)},              # different serials
    {"sdb": ("0000000000", 1, SECTORS), "sdc": ("0000000000", 2, SECTORS * 2)},   # one serial, two sizes
    {"sdb": (None, 1, SECTORS)},                                       # one disk, no serial (build/student, VM)
    {"sdb": (None, 1, SECTORS), "sdc": (None, 2, SECTORS * 2)},        # no serial, but not one size
], ids=["serials", "sizes", "single-no-serial", "no-serial-sizes"])
def test_a_disk_without_a_twin_is_written_as_before(tmp_path, disks):
    """Positive control: the twin check refuses nothing else."""
    out, calls, run_dir = run(tmp_path, disks, ["sdb"], 'discard_before_restore sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=0") and calls and calls[0].startswith("blkdiscard "), (out, calls)
    assert state(run_dir, "sdb")[0] != "failed"
    assert not (run_dir / "targets/sdb/twin").exists()


def test_the_vm_bridges_loop_has_no_twin_among_the_hosts_disks(tmp_path):
    """server/vm/vmdisk.py binds a loop device on the server: a file this job made, not a
    drawer. A host disk without a serial of the same size is not its twin."""
    out, calls, run_dir = run(tmp_path, {"loop0": (None, 0, SECTORS), "sda": (None, 1, SECTORS)}, ["loop0"],
                              'discard_before_restore loop0; echo "rc=$?"')
    assert out.strip().endswith("rc=0") and calls and calls[0].startswith("blkdiscard "), (out, calls)
    assert not (run_dir / "targets/loop0/twin").exists()


def test_drawers_with_different_serials_are_both_written(tmp_path):
    disks = {"sdb": ("S1", 1, SECTORS), "sdc": ("S2", 2, SECTORS)}
    out, _calls, _ = run(tmp_path, disks, ["sdb", "sdc"],
                         'DRAWER_MAP=" sdb:1 sdc:2"; echo "out=$(drawers_bound sdb sdc | tr "\\n" " ")"')
    assert out.strip().endswith("out=sdb sdc"), out


# --- 2. the slot the room chose holds the disk the console showed ------------------


DRAWER = {"sdb": ("S2", 2, SECTORS)}
BOUND = 'DRAWER_MAP=" sdb:2"; echo "out=$(drawers_bound sdb)"'


@pytest.mark.parametrize("shown", [
    [f"2|{SECTORS * 512}|S9"],               # another serial in the slot
    [f"2|{SECTORS * 2 * 512}|S2"],           # the same serial, another size
    [f"1|{SECTORS * 512}|S2"],               # nothing sent for this slot
    [f"2|{SECTORS * 512}|"],                 # the console showed no serial there (null)
    ["2||S2"],                              # nor a size
    [],                                      # the field, empty
], ids=["serial", "size", "no-entry", "null-serial", "null-size", "empty"])
def test_a_disk_not_the_one_the_console_showed_is_not_written(tmp_path, shown):
    out, _calls, run_dir = run(tmp_path, DRAWER, ["sdb"], BOUND, shown=shown)
    assert out.strip().endswith("out="), out
    st, err = state(run_dir, "sdb")
    assert st == "failed" and SWAPPED_HE in err, (st, err)
    assert (run_dir / "targets/sdb/foreign").exists()


def test_the_disk_the_console_showed_is_written(tmp_path):
    out, _calls, run_dir = run(tmp_path, DRAWER, ["sdb"], BOUND,
                               shown=[f"1|{SECTORS * 512}|S1", f"2|{SECTORS * 512}|S2"])
    assert out.strip().endswith("out=sdb"), out
    assert state(run_dir, "sdb")[0] == "waiting"


def test_an_old_server_without_the_field_writes_by_the_slot_as_before(tmp_path):
    out, _calls, _run_dir = run(tmp_path, DRAWER, ["sdb"], BOUND, shown=None)
    assert out.strip().endswith("out=sdb"), out


def test_a_serial_with_a_bar_is_compared_whole(tmp_path):
    """The serial is the last field of a line, and may hold '|'."""
    disks = {"sdb": ("A|B", 2, SECTORS)}
    out, _c, _r = run(tmp_path, disks, ["sdb"], BOUND, shown=[f"2|{SECTORS * 512}|A|B"])
    assert out.strip().endswith("out=sdb"), out
    out, _c, _r = run(tmp_path / "other", disks, ["sdb"], BOUND, shown=[f"2|{SECTORS * 512}|A"])
    assert out.strip().endswith("out="), out


# --- the field on the wire (jq: the testrunner) ------------------------------------


jq = requires_native(("bash", BASH), "jq")


def _resolve(tmp_path, session: dict, disks=None, script: str = "") -> str:
    session = {"id": "ses_1", "target_ports": [2], **session}
    out, _calls, _run_dir = run(tmp_path, disks or DRAWER, [],
                                'list_drawers() { echo sdb; }; resolve_target_drawers; '
                                'echo "DISKS:$(echo $_disks)"; '
                                'if [ -f "$RUN_DIR/drawer_shown" ]; then echo SHOWN; cat "$RUN_DIR/drawer_shown"; else echo NOSHOWN; fi; '
                                + script,
                                resp={"schema": 1, "known": True, "session": session})
    return out


@jq
@pytest.mark.parametrize("session", [{}, {"target_disks": None}], ids=["absent", "null"])
def test_no_target_disks_reads_as_an_old_server(tmp_path, session):
    out = _resolve(tmp_path, session)
    assert "DIE" not in out and "DISKS:sdb" in out and "NOSHOWN" in out, out


@jq
def test_target_disks_become_drawer_shown(tmp_path):
    out = _resolve(tmp_path, {"target_disks": [
        {"port": 2, "serial": "S2", "size_bytes": SECTORS * 512},
        {"port": 3, "serial": None, "size_bytes": None},
        {"port": "4", "serial": "X", "size_bytes": 1},          # not a slot number: dropped
        "junk"]})
    assert "SHOWN" in out and "DIE" not in out, out
    lines = out.split("SHOWN", 1)[1].split()
    assert lines == [f"2|{SECTORS * 512}|S2", "3||"], out


@jq
@pytest.mark.parametrize("value", [{"port": 2}, "S2", 7, True], ids=["object", "string", "number", "bool"])
def test_malformed_target_disks_stop_before_writing(tmp_path, value):
    out = _resolve(tmp_path, {"target_disks": value})
    assert "DIE: room target disks are malformed" in out, out


@jq
@pytest.mark.parametrize("serial,written", [("S2", True), ("S9", False)])
def test_the_whole_path_hello_to_drawers_bound(tmp_path, serial, written):
    """resolve_target_drawers -> target_init -> drawers_bound, as do_restore_drawers runs
    them: the console showed S2 in slot 2; by target_init the slot holds `serial`."""
    out = _resolve(tmp_path, {"target_disks": [{"port": 2, "serial": "S2", "size_bytes": SECTORS * 512}]},
                   disks={"sdb": (serial, 2, SECTORS)},
                   script='target_init sdb 1000; echo "out=$(drawers_bound $_disks)"')
    assert out.strip().endswith("out=sdb" if written else "out="), out
    if not written:
        assert SWAPPED_HE in (tmp_path / "box/run/targets/sdb/error").read_text(encoding="utf-8")


def test_the_agent_reads_the_field_where_the_drawers_are_chosen():
    """Structural: the field is read in drawerselect.sh, and do_restore_drawers still
    calls drawers_bound between target_init and the SMART gate."""
    text = (AGENT / "lib/restore/drawerselect.sh").read_text(encoding="utf-8")
    assert ".session.target_disks" in text
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert main.index('target_init "$_d" "$_total"') < main.index("drawers_bound $_disks") < main.index("smart_gate")
