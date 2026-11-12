"""#2910 -- a cloner whose drawers were ALL skipped before writing reports `failed`, not `waiting`.

`job_reset` writes `waiting` to `$RUN_DIR/state`; every writer of `failed` is on a writing path.
When the SMART gate (or the drawer gates) skipped every drawer, `do_restore_drawers` went
straight to `end_failed_round`, and `report_final` sent the word that was left: `waiting`.
`waiting` is not terminal on the server (`reports.TERMINAL`), so the member stayed `waiting`
for 180 s and was marked `lost` -- with the wrong reason ("stopped reporting before a result"),
though it had reported, and both drawers were red.

Here the path is cut out of `imagesrv-agent` with its neighbours stubbed (as
test_writemark_postdeploy_1877.py): what the state is at the moment the failed round is
reported. The decision "red = skip without asking" (#2044) is not touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_final_report import cut_function

pytestmark = requires_native(("bash", BASH))

STUBS = r'''
log() { echo "LOG $*" >> "$RUN_DIR/agent.log"; }
json_get() { case "$2" in .session.image_id) echo img_1 ;; .session.id) echo ses_1 ;; *) echo 0 ;; esac; }
http_get() { echo '{}'; }
identify_watch_stop() { :; }; smart_selftest_cancel() { :; }; session_stream() { return 0; }
die_local() { echo "DIE $*" >> "$RUN_DIR/trace"; exit 1; }
job_reset() { echo waiting > "$RUN_DIR/state"; }
target_init() { mkdir -p "$RUN_DIR/targets/$1"; echo waiting > "$RUN_DIR/targets/$1/state"; }
target_set() { echo "$2" > "$RUN_DIR/targets/$1/state"; }
progress_loop() { :; }
resolve_target_drawers() { _disks="sdb sdc"; }
drawers_bound() { echo "$@"; }
session_open_gate() { :; }
round_beep() { :; }
smart_gate() {
    case "${GATE:-}" in
        all) shift; for _t in "$@"; do target_set "$_t" skipped; done; return 1 ;;      # every drawer red: skipped
        one) shift; target_set "$1" skipped; shift; echo "$@"; return 0 ;;                # one red, the rest written
        *) shift; echo "$@"; return 0 ;;
    esac
}
drawer_gates() { case "${GATE:-}" in drawers) for _t in "$@"; do target_set "$_t" skipped; done ;; *) echo "$@" ;; esac; }
run_restore_drawers() { echo "DRAWERS $*" >> "$RUN_DIR/trace"; shift 4; for _t in "$@"; do target_set "$_t" done; done; echo done > "$RUN_DIR/state"; return 0; }
crc_delta_measure() { :; }; smart_note_apply() { :; }; stage_drawer_drivers() { :; }
writemark_release_done() { return 0; }
report_final() { echo "REPORT $(cat "$RUN_DIR/state")" >> "$RUN_DIR/trace"; return 0; }
agent_log_send() { :; }; round_beep_result() { :; }
end_tally() { _et_ok=1; _et_n=1; }; end_countdown() { return 0; }
end_failed_round() { echo "ENDFAILED $1 state=$(cat "$RUN_DIR/state")" >> "$RUN_DIR/trace"; }
round_closed() { return 1; }
'''


def _run(tmp_path: Path, gate: str) -> list[str]:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    funcs = tmp_path / "funcs.sh"
    funcs.write_text(cut_function("do_restore_drawers") + "\n", encoding="utf-8", newline="\n")
    script = (f'export RUN_DIR={posix(run)!r} RESP=x SERVER=http://127.0.0.1:9 MAC=m D_ROLE=cloner '
              f'IMAGESRV_TEST=1 GATE={gate}; . {posix(AGENT)}/lib/restore/verdict.sh; '
              + STUBS + f'. {posix(funcs)}; do_restore_drawers img_1 ses_1; echo "RETURNED rc=$?"')
    out = tmp_path / "out.txt"
    with out.open("w", encoding="utf-8") as sink:
        subprocess.run([BASH, "-c", script], stdout=sink, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, timeout=120)
    trace = run / "trace"
    return trace.read_text(encoding="utf-8").splitlines() if trace.exists() else [out.read_text()]


def test_every_drawer_skipped_by_the_smart_gate_is_reported_failed(tmp_path):
    trace = _run(tmp_path, "all")
    assert trace == ["ENDFAILED all drawers skipped state=failed"], trace


def test_every_drawer_skipped_by_the_drawer_gates_is_reported_failed(tmp_path):
    trace = _run(tmp_path, "drawers")
    assert trace == ["ENDFAILED all drawers skipped state=failed"], trace


def test_a_round_with_a_drawer_left_to_write_is_not_touched(tmp_path):
    """Both states: one red drawer skipped, the other written -- the machine reports what the
    writing path says (`done` here), never the all-skipped `failed`."""
    trace = _run(tmp_path, "one")
    assert len(trace) == 2 and trace[0].startswith("DRAWERS ") and trace[0].endswith(" sdc"), trace
    assert trace[1] == "REPORT done", trace
