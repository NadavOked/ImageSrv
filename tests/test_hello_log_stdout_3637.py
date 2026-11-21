"""#3637 -- `log` inside the hello's `$( )` put a line of text into the JSON body.

`log` (common.sh) prints `imagesrv: <message>` to STDOUT while no GUI owns the screen. `probe_json` runs
inside the `$( ... )` of `build_hello` (sysinfo.sh), so its two `log` calls -- an expired probe marker, a
probe job that could not start -- became part of the body: `{"a":1imagesrv: stale hardware-probe marker
expired; restarting}`, and the server answered 400 bad_json. (Not the cause of #3632: that was a tail
`]}}` inside netprobe.json.) netcache.sh already writes its parent-side `log` lines to stderr.

* behaviour (busybox ash): the body is valid JSON with a stale marker, and with a job that cannot start;
  the message still reaches stderr (not silenced -- it is the only sign that the marker expired);
* static: no `*_json` function that build_hello calls has a `log` without `>&2` in its own body.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, posix
from test_beeps_2067 import BUSYBOX, run_ash
from test_gap_subshell_fd_3524 import prelude

ASH = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")


def hello_fragments() -> list[str]:
    """The `*_json` functions build_hello puts into the body, from sysinfo.sh itself."""
    text = (AGENT / "lib/hw/sysinfo.sh").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"command -v (\w+_json) ", text)))


def body_of(name: str) -> tuple[str, list[str]]:
    for path in sorted((AGENT / "lib").rglob("*.sh")):
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if line.startswith(f"{name}() {{"):
                end = next(j for j in range(i, len(lines)) if lines[j].startswith("}"))
                return path.name, lines[i:end + 1]
    raise AssertionError(f"{name} is not defined under agent/lib")


def test_the_scan_sees_the_fragments_build_hello_calls():
    names = hello_fragments()
    assert {"probe_json", "netprobe_json", "inventory_json"} <= set(names), names


@pytest.mark.parametrize("name", hello_fragments())
def test_no_hello_fragment_logs_to_stdout(name):
    """Every `log` in a fragment's own body is `log ... >&2`: the fragment is captured by `$( )`."""
    where, lines = body_of(name)
    bare = [f"{where}: {line.strip()}" for line in lines
            if re.search(r'(^|[\s;&|(])log "', line.split(" #")[0]) and ">&2" not in line.split(" #")[0]]
    assert not bare, f"{name} writes to stdout inside the hello's $( ):\n" + "\n".join(bare)


def run(tmp_path: Path, body: str) -> str:
    return run_ash(prelude(tmp_path)
                   + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; '
                   + f'. {posix(AGENT)}/lib/hw/probe.sh; . {posix(AGENT)}/lib/core/bgfds.sh; . {posix(AGENT)}/lib/hw/probe_bg.sh; '
                   + r'''_probe_fresh() { printf ',"probe":{"x":1}'; }
''' + body, timeout=30)


def body_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("BODY="))[len("BODY="):]


@ASH
def test_an_expired_probe_marker_does_not_enter_the_body(tmp_path):
    out = run(tmp_path, r'''
echo 1 > "$RUN_DIR/probe.json.running"
b="{\"a\":1$(probe_json 2>"$RUN_DIR/err")}"; echo "BODY=$b"
echo "ERR=$(cat "$RUN_DIR/err")"
''')
    assert json.loads(body_line(out)) == {"a": 1}, out
    assert "ERR=" + "imagesrv: stale hardware-probe marker expired; restarting" in out, "the message must still be said, on stderr\n" + out


@ASH
def test_a_probe_job_that_cannot_start_does_not_enter_the_body(tmp_path):
    out = run(tmp_path, r'''
_probe_background_start() { return 1; }
b="{\"a\":1$(probe_json 2>"$RUN_DIR/err")}"; echo "BODY=$b"
echo "ERR=$(cat "$RUN_DIR/err")"
''')
    assert json.loads(body_line(out)) == {"a": 1}, out
    assert "ERR=imagesrv: hardware probe could not start" in out, out
