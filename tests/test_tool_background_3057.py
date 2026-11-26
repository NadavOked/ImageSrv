"""#3057: a toolbox tool runs in the background -- the kiosk's record loop goes on, and a running tool keeps the idle clock off.

Before: `tool-run` was dispatched as `tools_gui_run`, which returns only when the tool exits. A tool of hours (ddrescue, nwipe) held
the loop, so no `touch` record was read, and idle_check (the agent) powered the machine off in the middle of it.

The tool here is a fake that waits for a `go` file (bounded: 20 s is the end of the negative control, not a pass). Every assertion is on
what the loop or the clock sees WHILE it waits.
"""

from __future__ import annotations

from native import requires_native
from test_agent import AGENT, posix
from test_tools_framework import last, setup, sh

SLOW_MODULE = """\
tools_slow_list() { printf 'slow|disk|a long tool|ro|\\n'; }
tools_slow_run() {
    _n=0
    while [ ! -e "$RUN_DIR/go" ] && [ "$_n" -lt 200 ]; do sleep 0.1; _n=$((_n + 1)); done
    echo "slow finished"
}
"""

PRE = f". {posix(AGENT)}/lib/core/bgfds.sh; . {posix(AGENT)}/lib/core/idle.sh; "
#: poll for a file, 10 s at most
WAIT = 'w() { _w=0; while [ ! -e "$1" ] && [ "$_w" -lt 100 ]; do sleep 0.1; _w=$((_w + 1)); done; }; '

pytestmark = requires_native("jq", why="tools_gui_prep reads the machine name with jq")


def test_the_loop_gets_control_back_while_the_tool_runs_and_the_clock_stays_off(tmp_path):
    _, run, gui, pre = setup(tmp_path, {"slow": SLOW_MODULE}, selection=["slow"])   # the module is the provider of the tool's functions (tools_slow_*), and the id must be in the server's selection (#1050)
    (gui / "station.json").write_text('{"name":"BUILD-01"}')
    out = sh(pre + PRE + WAIT + """
export IDLE_POWEROFF_S=300
tools_gui_bg 'tool-run|slow||'; echo "bg_rc=$?"
[ -e "$GUI_DIR/tool-result" ] && echo "result_early=yes" || echo "result_early=no"
idle_exempt && echo "exempt_while_running=yes" || echo "exempt_while_running=no"
tools_gui_bg 'tool-run|slow||'; echo "second_rc=$?"
: > "$RUN_DIR/go"
w "$GUI_DIR/tool-result"; cat "$GUI_DIR/tool-result"
g() { _g=0; while [ -e "$RUN_DIR/tool.running" ] && [ "$_g" -lt 100 ]; do sleep 0.1; _g=$((_g + 1)); done; }; g
idle_exempt && echo "exempt_after=yes" || echo "exempt_after=no"
echo done
""")
    lines = out.stdout.splitlines()
    assert "bg_rc=0" in lines, out.stdout + out.stderr
    assert "result_early=no" in lines, "the tool finished before the loop got control back: it ran in the foreground\n" + out.stdout
    assert "exempt_while_running=yes" in lines, "a running tool is not activity: the machine may power off mid-run\n" + out.stdout
    assert "second_rc=1" in lines, "a second tool started while the first still runs\n" + out.stdout
    assert any(x.startswith("slow|0|") and x.endswith("|1") for x in lines), out.stdout
    assert "exempt_after=no" in lines, "the clock stays off after the tool ended\n" + out.stdout
    assert (run / "tool-slow.out").read_text(encoding="utf-8").strip() == "slow finished"
    assert not (run / "tool.running").exists()


def test_a_dead_job_is_no_running_tool(tmp_path):
    """A killed kiosk leaves tool.running behind with a dead pid: the clock must run again (principle 5: no evidence of work)."""
    _, run, gui, pre = setup(tmp_path, {"disk": SLOW_MODULE})
    out = sh(pre + PRE + """
export IDLE_POWEROFF_S=300
sleep 0 & p=$!; wait "$p"; printf '%s\\n' "$p" > "$RUN_DIR/tool.running"
idle_exempt && echo "exempt=yes" || echo "exempt=no"
tools_running && echo "running=yes" || echo "running=no"
""")
    assert "exempt=no" in out.stdout.splitlines(), out.stdout + out.stderr
    assert "running=no" in out.stdout.splitlines(), out.stdout + out.stderr
    assert last(out) == "running=no"
