"""#1447 (metal 16/10): cloner 2's agent log said 07:38 while the server's
journal said 07:25, both UTC -- the machine's clock ran 13 minutes ahead. The
agent has no NTP and never set its clock; it lived on the RTC, and the RTC of
an old machine drifts or has a dead battery (cloner 1).

The fix, two links:

* ``POST /api/v1/agent/hello`` answers with ``now`` -- the server's epoch,
  read last (``server/agent/api.py``). Only in the real hello, not the GRUB menu.
* ``hello_post`` (``agent/lib/core/hello.sh``) sets the clock with ``date -s @now``
  when it is more than ``CLOCK_SLACK_S`` (2 s) off, logs the correction, and
  only then stamps ``hello.state``. No ``now`` (an older server) = untouched;
  a ``now`` that is not a number or a ``date -s`` that fails is logged and the
  hello is not failed for it. The RTC is never written.

The shell tests fake ``json_get`` -- jsonq.sh is "the single place that
touches jq", so tests fake one function -- and one more runs the real jq.

**Negative control:** without the ``now`` field and ``clock_from_server``,
``date -s`` is never called, no correction is logged, ``hello.state`` keeps
the drifted time and the reply has no ``now``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import AGENT, BASH, posix, sh

SERVER_NOW = 1_760_600_000
SHELL = requires_native(("bash", BASH), why="the agent is POSIX sh")

#: ‏jq stand-in for `.now` only -- what jsonq.sh's json_get prints: the raw
#: value, and "null" for a missing key or JSON null.
FAKE_JSON_GET = (
    "json_get() { _v=$(sed -n 's/.*\"now\": *\\([^,}]*\\).*/\\1/p' \"$1\" | tr -d '\"'); "
    "printf '%s\\n' \"${_v:-null}\"; }; ")


def _run(tmp_path: Path, reply: dict | None, local: int, *, curl_rc: int = 0,
         date_set_rc: int = 0, real_jq: bool = False) -> dict:
    run = tmp_path / "run"; run.mkdir()
    clock = tmp_path / "clock"; clock.write_text(f"{local}\n", newline="\n")
    reply_file = tmp_path / "reply.json"
    reply_file.write_text(json.dumps(reply if reply is not None else {}), newline="\n")
    (tmp_path / "req.json").write_text("{}", newline="\n")
    script = (
        f"export RUN_DIR={posix(run)!r} HTTP_TIMEOUT=5 HTTP_RETRIES=0; "
        f". {posix(AGENT)}/lib/core/hello.sh; "
        + (f". {posix(AGENT)}/lib/core/jsonq.sh; " if real_jq else FAKE_JSON_GET)
        + f"log() {{ printf '%s\\n' \"$*\" >> {posix(tmp_path / 'log')!r}; }}; "
        # date: +%s reads the fake clock; -s @N records the call and moves it.
        f"date() {{ case \"$1\" in +%s) cat {posix(clock)!r} ;; "
        f"-s) printf '%s\\n' \"$2\" >> {posix(tmp_path / 'set')!r}; "
        f"[ {date_set_rc} -eq 0 ] || return {date_set_rc}; "
        f"printf '%s\\n' \"${{2#@}}\" > {posix(clock)!r} ;; esac; }}; "
        # curl: writes the reply to -o and prints the HTTP code, or fails.
        f"curl() {{ while [ $# -gt 0 ]; do [ \"$1\" = -o ] && _o=$2; shift; done; "
        f"[ {curl_rc} -eq 0 ] || return {curl_rc}; "
        f"cp {posix(reply_file)!r} \"$_o\"; printf 200; }}; "
        f"hello_post http://server/api/v1/agent/hello {posix(tmp_path / 'req.json')!r} "
        f"{posix(tmp_path / 'resp.json')!r}; echo \"rc=$?\""
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    read = lambda name: ((tmp_path / name).read_text().splitlines()  # noqa: E731
                         if (tmp_path / name).exists() else [])
    return {"rc": out.stdout.strip().splitlines()[-1], "set": read("set"),
            "log": read("log"), "state": read("run/hello.state"), "stderr": out.stderr}


@SHELL
def test_a_clock_13_minutes_ahead_is_set_back_and_logged(tmp_path):
    got = _run(tmp_path, {"known": True, "now": SERVER_NOW}, SERVER_NOW + 780)
    assert got["rc"] == "rc=0", got
    assert got["set"] == [f"@{SERVER_NOW}"], got
    assert len(got["log"]) == 1 and "was 780s off" in got["log"][0], got
    # hello.state is stamped after the correction, not with the drifted time
    assert got["state"] == [f"{SERVER_NOW}|200"], got


@SHELL
def test_a_clock_behind_is_set_forward(tmp_path):
    got = _run(tmp_path, {"now": SERVER_NOW}, SERVER_NOW - 600)
    assert got["set"] == [f"@{SERVER_NOW}"], got
    assert "was -600s off" in got["log"][0], got


@SHELL
@pytest.mark.parametrize("off", [-2, 0, 1, 2])
def test_within_the_slack_the_clock_is_left_alone(tmp_path, off):
    got = _run(tmp_path, {"now": SERVER_NOW}, SERVER_NOW + off)
    assert got["set"] == [] and got["log"] == [], got


@SHELL
def test_three_seconds_off_is_already_corrected(tmp_path):
    got = _run(tmp_path, {"now": SERVER_NOW}, SERVER_NOW + 3)
    assert got["set"] == [f"@{SERVER_NOW}"], got


@SHELL
@pytest.mark.parametrize("reply", [{"known": True}, {"known": True, "now": None}])
def test_an_older_server_without_now_leaves_the_clock(tmp_path, reply):
    got = _run(tmp_path, reply, SERVER_NOW + 780)
    assert got["set"] == [] and got["log"] == [], got
    assert got["state"] == [f"{SERVER_NOW + 780}|200"], got


@SHELL
@pytest.mark.parametrize("now", ["soon", -5, 1.5])
def test_a_now_that_is_not_an_epoch_is_logged_not_applied(tmp_path, now):
    got = _run(tmp_path, {"now": now}, SERVER_NOW + 780)
    assert got["rc"] == "rc=0", got
    assert got["set"] == [], got
    assert len(got["log"]) == 1 and "not a number" in got["log"][0], got


@SHELL
def test_a_failed_hello_does_not_touch_the_clock(tmp_path):
    got = _run(tmp_path, {"now": SERVER_NOW}, SERVER_NOW + 780, curl_rc=7)
    assert got["rc"] == "rc=7", got
    assert got["set"] == [] and got["log"] == [], got


@SHELL
def test_a_date_that_cannot_be_set_is_logged_and_the_hello_goes_on(tmp_path):
    got = _run(tmp_path, {"now": SERVER_NOW}, SERVER_NOW + 780, date_set_rc=1)
    assert got["rc"] == "rc=0", got
    assert got["set"] == [f"@{SERVER_NOW}"], got
    assert len(got["log"]) == 1 and "date -s failed" in got["log"][0], got
    assert "780s off" in got["log"][0], got


@requires_native(("bash", BASH), "jq", why="the real json_get runs jq")
def test_the_real_json_get_reads_now(tmp_path):
    got = _run(tmp_path, {"known": True, "session": {"now": 5}, "now": SERVER_NOW},
               SERVER_NOW + 780, real_jq=True)
    assert got["set"] == [f"@{SERVER_NOW}"], got


def test_the_hello_reply_carries_the_server_time(server):
    pytest.importorskip("fastapi")
    from conftest import hello_body                     # noqa: PLC0415
    before = int(time.time())
    reply = server["anon"].post("/api/v1/agent/hello",
                                json=hello_body("b4:2e:99:07:1a:fb")).json()
    after = int(time.time())
    assert isinstance(reply["now"], int), reply
    assert before <= reply["now"] <= after, reply


def test_the_agent_never_writes_the_rtc():
    """The RTC of a Windows build machine keeps local time; the battery of a
    cloner may be dead. The correction is the system clock only."""
    text = (AGENT / "lib" / "core" / "hello.sh").read_text(encoding="utf-8")
    assert "hwclock" not in text
