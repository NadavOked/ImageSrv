"""#1935 -- ``done`` reaches the server only after the name, the drivers and the
incomplete-write mark -- never before them.

The server keeps a member that reported a terminal state (``done``/``partial``/
``failed``) as it is (#771): what is reported after it is dropped. Until #1935
``run_restore`` (and the room's ``run_restore_drawers``), ``name_this_machine``
and ``stage_drivers`` each wrote ``done`` into ``$RUN_DIR/state`` -- which the
reporter sends every 2 s -- while the hostname, the drivers and the mark were
still to come. Now the helpers leave a non-terminal word (``verifying``/
``naming``/``staging``) and the callers write the terminal one right before the
final report.

Here: the real ``do_restore`` and ``do_restore_drawers`` (test_final_report's
harness and its fake server), with the post-write steps replaced by spies that
record the state the reporter would be sending while each of them runs. The
helpers themselves are run for real in test_drawer_progress.py
(``run_restore_drawers``), test_hostname_failure_visible.py
(``name_this_machine``) and test_postdeploy.py (``stage_drivers``).

**Negative control** (imagesrv-agent, drawers.sh, hostname.sh, postdeploy.sh,
restore.sh, single_restore.sh of 9001fc91; Testrunner 22/10): both fail -- the
final report is ``naming`` / ``verifying``, never ``done``: with the spies the
old callers never wrote it, the old helpers did, early. The helpers' own tests
fail there too (27 failed, 66 passed in the six files).
"""

from __future__ import annotations

from server.agent.reports import TERMINAL, VALID_STATES

from test_final_report import (  # noqa: F401 -- `reports` is a fixture
    IMAGE, SESSION, native_tools, reports, run_path, url_of,
)

#: The new contract of the write helpers -- a non-terminal word -- and a spy on
#: every step after the write. `AT <step> <state>` = what the reporter sends meanwhile.
SPIES = (
    'run_restore() { target_set "$2" done; echo verifying > "$RUN_DIR/state"; return 0; }; '
    'run_restore_drawers() { for _t in sdb sdc; do target_set "$_t" done; done; '
    'echo verifying > "$RUN_DIR/state"; return 0; }; '
    '_at() { echo "AT $1 $(cat "$RUN_DIR/state")" >> "$RUN_DIR/trace"; }; '
    'name_this_machine() { _at name; echo naming > "$RUN_DIR/state"; }; '
    'stage_drivers() { _at drivers; }; '
    'stage_drawer_drivers() { _at drivers; }; '
    'writemark_release_done() { _at mark; }; '
)


def _steps(result: dict) -> list[tuple[str, str]]:
    return [tuple(line.split()[1:3]) for line in result["trace"] if line.startswith("AT ")]


def _assert_done_last(result: dict, reports, steps: list[str]) -> None:
    seen = _steps(result)
    assert [s for s, _ in seen] == steps, result["trace"]
    for step, state in seen:
        assert state in VALID_STATES and state not in TERMINAL, \
            f"'{state}' was reported while '{step}' still ran -- the server keeps it (#1935)"
    assert reports.received, result["out"]
    assert reports.received[-1]["state"] == "done", [r["state"] for r in reports.received]


@native_tools
def test_a_station_reports_done_only_after_the_name_the_drivers_and_the_mark(tmp_path, reports):
    result = run_path(tmp_path, url_of(reports), "do_restore", stubs=SPIES)
    _assert_done_last(result, reports, ["name", "drivers", "mark"])


@native_tools
def test_a_cloner_reports_done_only_after_the_drivers_and_the_mark(tmp_path, reports):
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}", stubs=SPIES)
    _assert_done_last(result, reports, ["drivers", "mark"])
