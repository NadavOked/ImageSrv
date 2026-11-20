"""#3314 (sister of #3313): the toolbox modules never tell the operator to type the machine name.

Nadav 10/10 (#1270): no typed word anywhere; the confirm window with the red button is the confirmation. The
check of `confirm == machine name` stays -- it is the API guard the kiosk's confirm window fills in (a wrong
name is still refused, rc 3, nothing touched). Only the text changes: the refusal of the disk, windows and hw
tools says "not confirmed in the confirm window", and the comments / docs say the same.

`esp-fsck-repair` (tools_boot.sh) is #3313 / PR #3318.

Negative control: `tools_disk.sh`, `tools_windows.sh`, `tools_hw.sh` and `tools.sh` back at the base of this PR --
the three refusal tests and the source test fail; the control (a wrong name is still refused with nothing run)
passes on both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_tools_disk import DESTROY_TOOLS, run_disk, out_text, BASH
from test_tools_hw import fake_tools, run_tool, SH as HW_SH
from test_tools_windows import SH as WIN_SH, fake, fixture as win_fixture, run as win_run
from native import requires_native

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "agent" / "lib" / "tools"
WINDOW = "חלון האישור"
TYPING = re.compile(r"הקלד|להקליד|must type|typed machine|type the machine")


@requires_native(("bash", BASH))
@pytest.mark.parametrize("tool_id,arg", DESTROY_TOOLS)
def test_a_refused_disk_tool_points_at_the_confirm_window_and_asks_for_no_typing(tmp_path: Path, tool_id, arg):
    log = tmp_path / "touched.log"
    log.write_text("", encoding="utf-8")
    r = run_disk(tmp_path, cmd=f"tools_disk_run {tool_id} {arg!r} wrong-name")
    body = out_text(tmp_path, tool_id)
    assert r.returncode == 3, (r.stdout, r.stderr, body)              # the guard stays
    assert log.read_text(encoding="utf-8").strip() == ""              # and nothing was touched
    assert not TYPING.search(body), body
    assert WINDOW in body and "סורב" in body


@pytest.mark.skipif(not WIN_SH, reason="POSIX sh is required")
def test_a_refused_windows_tool_points_at_the_confirm_window_and_asks_for_no_typing(tmp_path: Path):
    base, env = win_fixture(tmp_path)
    marker = base / "chntpw-ran"
    fake(base / "bin", "chntpw", f'touch "{marker.as_posix()}"; exit 0')
    done = win_run(base, "win-blank-password", "SAM:alice", confirm="wrong", env=env)
    output = (base / "run/tool-win-blank-password.out").read_text(encoding="utf-8")
    assert done.returncode == 3 and not marker.exists()
    assert not TYPING.search(output), output
    assert WINDOW in output and "דבר לא נמח" in output


@pytest.mark.skipif(not HW_SH, reason="POSIX sh is unavailable")
def test_a_refused_tpm_clear_points_at_the_confirm_window_and_asks_for_no_typing(tmp_path: Path):
    bindir = fake_tools(tmp_path)
    marker = tmp_path / "called"
    tool = bindir / "tpm2_clear"
    tool.write_text(f"#!/bin/sh\ntouch '{marker.as_posix()}'\n", encoding="utf-8", newline="\n")
    tool.chmod(0o755)
    done = run_tool(tmp_path, "tpm-clear", path=bindir, confirm="wrong-name")
    out = (tmp_path / "run/tool-tpm-clear.out").read_text(encoding="utf-8")
    assert done.returncode == 3 and not marker.exists()
    assert not TYPING.search(out), out
    assert WINDOW in out and "דבר לא נמחק" in out


def test_no_module_text_or_comment_tells_the_operator_to_type():
    """Every source of the framework and of the disk / windows / hw modules, comments included (a comment that says
    'the name the operator must type' is the next author's brief). tools_boot.sh is #3313."""
    for name in ("tools.sh", "tools_disk.sh", "tools_windows.sh", "tools_hw.sh"):
        text = (TOOLS / name).read_text(encoding="utf-8")
        hits = [line.strip() for line in text.splitlines() if TYPING.search(line) or "machine-name confirmation" in line]
        assert not hits, (name, hits)
