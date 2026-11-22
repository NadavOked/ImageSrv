"""#3173: udpcast is gone, and with it the only road a classroom station had to its round.

The chunk engine writes drawers only (chunkrecv.sh); a station's round over the network is v2. Until
then a station (any machine that is not a cloner) handed a running round fails BY NAME, before a byte
of its disk is touched -- principle 1: the local disk stays whole -- and the server is told `failed`
with the reason, the way every other failure there is told. The same for a drawers' restore asked in
any mode but the chunk engine's (a wave without the chunk engine is refused earlier, by chunk_gate --
test_chunk_gate_2533.py).

**Negative control** (all of `agent/` back at fa33a4068, WSL Debian 21/11): the three fail on behaviour --
the station goes on to the write (trace `['LOOP ses_a91f', 'WROTE smart_gate', 'WROTE run_restore', 'NAMED']`),
and the drawers' restore runs its resume, write check, TRIM and table in multicast mode
(`['IO resume_round', 'IO write_check_round', 'IO discard_before_restore', ...]`).
"""

from __future__ import annotations

import subprocess

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_final_report import reports, run_path, url_of  # noqa: F401 -- `reports` is a fixture

REPO = AGENT.parent
pytestmark = requires_native(("bash", BASH), "jq", why="the server's answers are read with jq, as in the agent")

WHY = "a station restore over the network arrives in version 2 -- nothing written"

#: every step of the old station road that touches the disk, or leads to it, leaves its name in the trace
WRITES = ("smart_gate", "write_check", "discard_before_restore", "apply_gpt", "run_restore")
RECORD = "".join(f'{name}() {{ echo "WROTE {name}" >> "$RUN_DIR/trace"; return 0; }}; ' for name in WRITES)


def test_a_station_handed_a_round_fails_by_name_before_its_disk_and_the_server_is_told(tmp_path, reports):
    result = run_path(tmp_path, url_of(reports), "do_restore", stubs=RECORD)
    assert not [t for t in result["trace"] if t.startswith(("WROTE", "RESTORE"))], result["trace"]
    assert "RETURNED rc=1" in result["out"], result["out"]
    assert "TEST-HOLD" in result["out"] and WHY in result["out"], result["out"]
    assert f"restore: {WHY}" in result["log"], result["log"]
    final = reports.received[-1]
    assert final["state"] == "failed", final
    assert [(t["dev"], t["state"], t.get("error"), t["bytes_written"]) for t in final["targets"]] == [("sda", "failed", WHY, 0)], final


def test_a_station_whose_report_is_not_confirmed_holds_and_does_not_reboot(tmp_path, reports):
    """The failure is the server's to see: an unconfirmed report holds the machine (hold_unheard), as everywhere."""
    reports.refuse_first = -1
    result = run_path(tmp_path, url_of(reports), "do_restore", stubs=RECORD)
    assert "FAILED: the work finished but the server never confirmed it" in result["out"], result["out"]
    assert not [t for t in result["trace"] if t.startswith(("WROTE", "RESTORE"))], result["trace"]


def test_a_drawers_restore_in_another_mode_fails_every_drawer_before_any_disk_io(tmp_path):
    """run_restore_drawers itself refuses a mode that is not the chunk engine -- no TRIM, no table, no stream."""
    run = tmp_path / "run"
    for d in ("sdb", "sdc"):
        (run / "targets" / d).mkdir(parents=True)
        (run / "targets" / d / "state").write_text("waiting\n")
    script = (f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / 'agent.log')!r}; "
              + agent_sources("common", "waits", "restore", "writeback", "drawers", "verdict")
              + 'trace() { echo "$*" >> "$RUN_DIR/trace"; }; '
              + 'fail_written_target() { echo failed > "$RUN_DIR/targets/$1/state"; trace "FAIL $1 $2"; }; '
              + "".join(f'{n}() {{ trace "IO {n}"; return 0; }}; ' for n in (
                  "write_check_round", "resume_round", "discard_before_restore", "apply_gpt", "expand_last",
                  "manifest_plan", "chunk_partition_drawers", "stage_at"))
              + 'run_restore_drawers multicast http://s img_1 /dev/null sdb sdc; echo "rc=$? state=$(cat "$RUN_DIR/state")"')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          cwd=str(REPO), stdin=subprocess.DEVNULL)
    out = proc.stdout + proc.stderr
    trace = (run / "trace").read_text(encoding="utf-8").splitlines() if (run / "trace").exists() else []
    assert "rc=1 state=failed" in out, out
    assert not [t for t in trace if t.startswith("IO ")], trace
    assert [t for t in trace if t.startswith("FAIL ")] == [
        f"FAIL {d} מנוע הזרם 'multicast' אינו מנוע החתיכות -- הדיסק לא שונה" for d in ("sdb", "sdc")], trace
