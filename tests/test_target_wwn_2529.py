"""#2529 -- the WWN as one more proof that the disk written is the disk chosen (owner 15/11, option B).

#2434 binds serial|sectors|slot at ``target_init`` (``bound``) and asks it again before every
destructive step (``assert_target``). Now ``target_bind`` also keeps the disk's WWN
(``targets/<dev>/wwn``) and writes ONE JSON line of what was approved (``target_bound {...}``
in the agent log), and ``assert_target`` compares it:

* another WWN under the same serial|sectors|slot -- the disk fails by name and is not written;
* the same WWN -- written, as before;
* no WWN on a side (at the choice, or now) -- neither stricter nor laxer: written on serial,
  size and slot, and the log says the WWN was not compared (principle 5), once per disk and side.

Twins (one serial, one size) stay refused even when their WWNs differ: the WWN never tells
them apart (#2448, the owner's decision).

The identity is what sysfs gives (``device/serial``, ``size``, ``wwid``: real files in the
box); only ``disk_port`` is a stub reading ``port.<dev>`` (as test_target_identity_2448.py).
The destructive step is ``discard_before_restore`` (blkdiscard, recorded, never run).

Negative control: every production file this change touches (failmark.sh, imagesrv-agent,
guibridge.sh, and targetwwn.sh removed) back to the base together -- see the PR body.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agentlib import agent_order, agent_sources
from native import requires_native
from test_agent import AGENT, BASH, REPO, posix

pytestmark = requires_native(("bash", BASH))

# targetwwn is not named: it comes with failmark where imagesrv-agent sources it (agentlib.agent_libs).
LIBS = ("common", "waits", "sysinfo", "diskinfo", "progress", "failmark", "discard")

SECTORS = 976773168
WWN_A = "naa.5002538e40a1b2c3"
WWN_B = "naa.5002538e40ffffff"
WWN_HE = "ה-WWN של הדיסק שבשם"
TWIN_HE = "אותו מספר סידורי ואותו גודל"


def run(tmp_path: Path, disks: dict[str, tuple[str, int, str | None]], bind: list[str],
        script: str) -> tuple[str, list[str], Path, str]:
    """``disks``: dev -> (serial, slot, device/wwid or None) in a fake sysfs; each of ``bind``
    through ``target_init``, then ``script``. Returns stdout, blkdiscard calls, RUN_DIR, agent.log."""
    box = tmp_path / "box"
    run_dir = box / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    for dev, (serial, slot, wwn) in disks.items():
        d = box / "sys/block" / dev
        (d / "device").mkdir(parents=True, exist_ok=True)
        (d / "queue").mkdir(parents=True, exist_ok=True)
        (d / "size").write_text(f"{SECTORS}\n", newline="\n")
        (d / "queue/rotational").write_text("0\n", newline="\n")
        (d / "queue/discard_max_bytes").write_text("2147450880\n", newline="\n")
        (d / "device/serial").write_text(serial + "\n", newline="\n")
        if wwn is not None:
            (d / "device/wwid").write_text(wwn + "\n", newline="\n")
        (box / f"port.{dev}").write_text(str(slot), newline="\n")
    calls = box / "calls"
    pre = (f'export RUN_DIR={posix(run_dir)!r} SYSROOT={posix(box)!r} DEVROOT={posix(box)}/dev '
           f'CALLS={posix(calls)!r} IMAGESRV_TEST=1 WAIT_HELPER_S=10; mkdir -p "$DEVROOT"; '
           + agent_sources(*LIBS)
           + 'disk_port() { cat "$SYSROOT/port.$1" 2>/dev/null; }; '
           + 'blkdiscard() { echo "blkdiscard $*" >> "$CALLS"; }; '
           + 'ata_capture() { :; }; stage_at() { :; }; '
           + "".join(f"target_init {d} 1000; " for d in bind))
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + pre + script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, cwd=str(REPO), timeout=60)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    log = run_dir / "agent.log"   # common.sh's LOG_FILE
    return proc.stdout, lines, run_dir, log.read_text(encoding="utf-8") if log.exists() else ""


def state(run_dir: Path, dev: str) -> tuple[str, str]:
    t = run_dir / "targets" / dev
    err = t / "error"
    return ((t / "state").read_text(encoding="utf-8").strip(),
            err.read_text(encoding="utf-8").strip() if err.exists() else "")


SET_WWID = 'echo {w} > "$SYSROOT/sys/block/sdb/device/wwid"; '
TRIM = 'discard_before_restore sdb; echo "rc=$?"'


def test_another_wwn_under_the_same_serial_size_and_slot_is_not_written(tmp_path):
    """The disk in slot 1 answers with the serial, size and slot bound -- and another WWN.
    Before #2529 serial|sectors|slot was the whole proof, and the TRIM reached blkdiscard."""
    out, calls, run_dir, log = run(tmp_path, {"sdb": ("S1", 1, WWN_A)}, ["sdb"],
                                   SET_WWID.format(w=WWN_B) + TRIM)
    assert out.strip().endswith("rc=2"), out
    assert calls == [], calls
    st, err = state(run_dir, "sdb")
    assert st == "failed" and WWN_HE in err and WWN_A in err and WWN_B in err, (st, err)
    assert (run_dir / "targets/sdb/foreign").exists()


def test_the_same_wwn_is_written(tmp_path):
    """The guard against an over-broad check: the disk chosen, WWN and all, is trimmed."""
    out, calls, run_dir, _log = run(tmp_path, {"sdb": ("S1", 1, WWN_A)}, ["sdb"], TRIM)
    assert out.strip().endswith("rc=0"), out
    assert calls == ["blkdiscard " + posix(tmp_path / "box") + "/dev/sdb"], calls
    assert state(run_dir, "sdb")[0] != "failed"


def test_no_wwn_at_the_choice_is_written_and_said_once(tmp_path):
    """No WWN when it was chosen, one now: not stricter (written), not silent (one log line,
    even when asked twice)."""
    out, calls, run_dir, log = run(tmp_path, {"sdb": ("S1", 1, None)}, ["sdb"],
                                   SET_WWID.format(w=WWN_B) + "assert_target sdb; " + TRIM)
    assert out.strip().endswith("rc=0"), out
    assert len(calls) == 1, calls
    assert state(run_dir, "sdb")[0] != "failed"
    said = [ln for ln in log.splitlines() if "sdb: WWN not compared -- none at the choice;" in ln]
    assert len(said) == 1, log


def test_no_wwn_now_is_written_and_said(tmp_path):
    """A WWN when it was chosen, none now (the other side): written on serial, size and slot, and said."""
    out, calls, run_dir, log = run(tmp_path, {"sdb": ("S1", 1, WWN_A)}, ["sdb"],
                                   'rm "$SYSROOT/sys/block/sdb/device/wwid"; ' + TRIM)
    assert out.strip().endswith("rc=0"), out
    assert len(calls) == 1, calls
    assert "sdb: WWN not compared -- none now;" in log, log


def test_the_bind_writes_one_json_line_of_what_was_approved(tmp_path):
    """One `target_bound {...}` line per bind, with the identity the writes will be held to."""
    _out, _calls, run_dir, log = run(tmp_path, {"sdb": ("S1", 1, WWN_A), "sdc": ("S2", 2, None)},
                                     ["sdb", "sdc"], "true")
    bound = [json.loads(ln.split("target_bound ", 1)[1]) for ln in log.splitlines() if "target_bound {" in ln]
    assert bound == [
        {"disk": "sdb", "serial": "S1", "sectors": SECTORS, "port": 1, "wwn": WWN_A, "twin": None},
        {"disk": "sdc", "serial": "S2", "sectors": SECTORS, "port": 2, "wwn": None, "twin": None},
    ], log
    assert (run_dir / "targets/sdb/wwn").read_text(encoding="utf-8").strip() == WWN_A
    assert not (run_dir / "targets/sdc/wwn").exists()


def test_twins_stay_refused_even_with_two_wwns(tmp_path):
    """Option B: the WWN only makes the check stricter. Two drawers with one serial and one
    size are refused as before (#2448), though their WWNs tell them apart."""
    out, calls, run_dir, _log = run(tmp_path, {"sdb": ("0000000000", 1, WWN_A), "sdc": ("0000000000", 2, WWN_B)},
                                    ["sdb"], TRIM)
    assert out.strip().endswith("rc=2"), out
    assert calls == [], calls
    st, err = state(run_dir, "sdb")
    assert st == "failed" and TWIN_HE in err, (st, err)


def test_the_agent_and_the_kiosk_load_it_after_failmark():
    """Both loaders of failmark.sh load targetwwn.sh after it -- without it assert_target only
    says the WWN was not compared."""
    order = agent_order()
    assert "targetwwn" in order and order.index("targetwwn") > order.index("failmark"), order
    bridge = (AGENT / "lib/gui/guibridge.sh").read_text(encoding="utf-8")
    assert 'failmark.sh"; . "$LIB_DIR/' + 'targetwwn.sh";' in bridge   # not a literal `. <lib>.sh` line: test_agent_lib_order_2272 reads those as a room
