"""#3850 (J1, 17/11): lines written to agent.log before the clock is set from
the server carry the RTC time with nothing to say so, and read as if they
happened mid-round (it cost a whole investigation in #3594 part b).

The fix: ``log()`` (common.sh) stamps ``[HH:MM:SS rtc]`` until the server has
vouched for the clock (``$RUN_DIR/clock.ok``, written by ``clock_from_server``
in hello.sh), and the ``clock: set from the server`` line says how many lines
before it are stamped rtc. Nothing is rewritten: the log stays append-only.

These run the real ``log`` and the real ``hello_post`` with a fake ``date``.

**Negative control:** with ``log()`` and hello.sh as before the fix, no line
carries ``rtc``.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_cloner_gui import AGENT, BASH, posix, sh

SERVER_NOW = 1_760_600_000
SHELL = requires_native(("bash", BASH), why="the agent is POSIX sh")
FAKE_JSON_GET = (
    "json_get() { _v=$(sed -n 's/.*\"now\": *\\([^,}]*\\).*/\\1/p' \"$1\" | tr -d '\"'); "
    "printf '%s\n' \"${_v:-null}\"; }; ")


def _run(tmp_path: Path, local: int, *, hello: bool = True) -> list[str]:
    run = tmp_path / "run"; run.mkdir()
    clock = tmp_path / "clock"; clock.write_text(f"{local}\n", newline="\n")
    (tmp_path / "reply.json").write_text(json.dumps({"now": SERVER_NOW}), newline="\n")
    (tmp_path / "req.json").write_text("{}", newline="\n")
    script = (
        f"export RUN_DIR={posix(run)!r} HTTP_TIMEOUT=5 HTTP_RETRIES=0; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/hello.sh; "
        + FAKE_JSON_GET
        + f"date() {{ case \"$1\" in +%s) cat {posix(clock)!r} ;; "
        f"-s) printf '%s\n' \"${{2#@}}\" > {posix(clock)!r} ;; *) echo 17:29:45 ;; esac; }}; "
        f"curl() {{ while [ $# -gt 0 ]; do [ \"$1\" = -o ] && _o=$2; shift; done; "
        f"cp {posix(tmp_path / 'reply.json')!r} \"$_o\"; printf 200; }}; "
        "log 'dhcp bound' >/dev/null; log 'gap: hardware probe start' >/dev/null; "
        + (f"hello_post http://s/h {posix(tmp_path / 'req.json')!r} {posix(tmp_path / 'resp.json')!r}; " if hello else "")
        + "log 'after' >/dev/null")
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return (run / "agent.log").read_text().splitlines()


@SHELL
def test_lines_before_the_clock_is_set_are_marked_and_the_set_line_counts_them(tmp_path):
    log = _run(tmp_path, SERVER_NOW + 835)
    assert log[0] == "[17:29:45 rtc] dhcp bound", log
    assert log[1] == "[17:29:45 rtc] gap: hardware probe start", log
    assert log[2].startswith("[17:29:45] clock: set from the server, was 835s off"), log
    assert "the 2 lines before this are stamped rtc" in log[2], log
    assert log[3] == "[17:29:45] after", log          # once synced: a plain stamp
    assert log[0:2] == [l for l in log if "rtc]" in l], log   # and nothing else is marked


@SHELL
def test_a_clock_within_the_slack_is_vouched_for_too(tmp_path):
    log = _run(tmp_path, SERVER_NOW + 1)
    assert log[-1] == "[17:29:45] after", log


@SHELL
def test_without_a_hello_every_line_stays_marked(tmp_path):
    log = _run(tmp_path, SERVER_NOW + 835, hello=False)
    assert log[-1] == "[17:29:45 rtc] after", log
