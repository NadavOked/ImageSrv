"""#2405 -- an expansion that failed no longer falls back quietly to the manifest's table.

Until #1897 (Nadav, 04/11) the way back from a failed ``expand_last`` was fine:
C: kept its size in the image and the system booted. After it, the same way back
writes D: and E: SHRUNK (about 2 GB free) with one WARNING line in the agent log.
Nadav's rule from #1897: "never shrink D: and hide it from the operator".

``expand_fallback`` (expandshare.sh), called by the three callers (run_restore,
run_restore_drawers, lc_prepare_target), decides:
  * the image owes a return (a partition after the candidate that capture shrank)
    -> the target FAILS by name (rc 1, PLAN_ERROR names the partitions), and the
    table of the manifest is not written;
  * it owes none -> the way back stays (apply_gpt, its rc), as before.

The box: real jq and the real libraries; an sgdisk that refuses every table
change (the expansion's own call among them), and ``apply_gpt`` as a recorder.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from agentlib import flat_lib
from test_agent import AGENT, BASH, _stub, posix, sh
from test_expand_share_1897 import DISK_500, c_d_e

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

LIBS = ("common", "waits", "jsonq", "sysinfo", "diskinfo", "progress", "failmark", "restore", "expand", "grow",
        "ntfsunhib", "expandshare", "writemark", "diskfit")


def after_failed_expansion(tmp_path: Path, manifest: dict, share_ok: bool = False,
                           apply_rc: int = 0) -> tuple[str, Path, Path]:
    """expand_last (sgdisk refuses) and then what the callers do: expand_fallback.
    Returns (the rc line with PLAN_ERROR, the run dir, the box); ``apply_gpt`` records its calls in run/apply."""
    box = tmp_path / "box"; stubs = box / "stubs"; run = box / "run"
    stubs.mkdir(parents=True); (run / "targets/sda").mkdir(parents=True)
    (box / "sys/block/sda").mkdir(parents=True)
    (box / "sys/block/sda/size").write_text(f"{DISK_500}\n")
    if share_ok:
        (run / "targets/sda/share.ok").write_text("")
    (box / "m.json").write_text(json.dumps(manifest), encoding="utf-8")
    out = sh(_stub(stubs / "sgdisk", f'echo "$*" >> {posix(box)}/sgdisk.calls; exit 4')
             + _stub(stubs / "blockdev", "exit 0") + _stub(stubs / "sleep", "exit 0")
             + f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
             f"export SYSROOT={posix(box)!r} RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
             + f'apply_gpt() {{ echo "$1" >> {posix(run)!r}/apply; return {apply_rc}; }}; '
             + "node_is_block() { true; }; verify_unique_guids() { true; }; target_bind sda; "
             + f"expand_last sda {posix(box / 'm.json')!r} > /dev/null 2>&1; echo \"expand_rc=$?\"; "
             + f"expand_fallback sda {posix(box / 'm.json')!r} > /dev/null; echo \"rc=$? err=$PLAN_ERROR\"")
    lines = out.strip().splitlines()
    assert lines[-2] == "expand_rc=1", out   # the premise: the expansion did fail
    return lines[-1], run, box


@needs_shell
def test_an_image_that_owes_a_return_fails_the_target_by_name_and_writes_nothing(tmp_path):
    line, run, _ = after_failed_expansion(tmp_path, c_d_e())
    assert line.startswith("rc=1 err="), line
    err = line.split("err=", 1)[1]
    # D: and E: (partitions 4 and 5) would arrive shrunk -- named, with the size they should have
    assert "מחיצה 4 (107.3 GB)" in err and "מחיצה 5 (16.1 GB)" in err, err
    assert "מכווצ" in err and "היעד נכשל" in err, err
    assert not (run / "apply").exists(), "the manifest's table was written under a failed expansion"
    assert "would arrive shrunk" in (run / "agent.log").read_text(encoding="utf-8")


@needs_shell
def test_an_image_that_owes_nothing_keeps_the_way_back(tmp_path):
    m = c_d_e()
    for p in m["partitions"]:
        p.pop("shrunk_from_bytes", None)
    line, run, _ = after_failed_expansion(tmp_path, m)
    assert line == "rc=0 err=", line
    assert (run / "apply").read_text().split() == ["sda"]
    assert "WARNING: expansion failed on sda" in (run / "agent.log").read_text(encoding="utf-8")


@needs_shell
def test_the_way_back_returns_the_rc_of_the_table_it_writes(tmp_path):
    m = c_d_e()
    for p in m["partitions"]:
        p.pop("shrunk_from_bytes", None)
    line, _, _ = after_failed_expansion(tmp_path, m, apply_rc=1)
    assert line.startswith("rc=1"), line


@needs_shell
def test_a_manifest_jq_cannot_read_is_a_failed_target_not_a_quiet_fallback(tmp_path):
    box = tmp_path / "box"; stubs = box / "stubs"; run = box / "run"
    stubs.mkdir(parents=True); (run / "targets/sda").mkdir(parents=True)
    (box / "m.json").write_text("{not json", encoding="utf-8")
    out = sh(f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/log; "
             + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in LIBS)
             + f'apply_gpt() {{ echo called >> {posix(run)!r}/apply; }}; '
             + "_expand_candidate() { echo '2|x'; }; "   # the candidate known; the manifest itself unreadable
             + f"expand_fallback sda {posix(box / 'm.json')!r} > /dev/null; echo \"rc=$? err=$PLAN_ERROR\"")
    assert out.strip().splitlines()[-1].startswith("rc=1 err="), out
    assert not (run / "apply").exists()


def test_all_three_callers_use_it_and_none_keeps_the_quiet_way_back():
    callers = {
        "restore/restore.sh": "expand_fallback",
        "restore/drawers.sh": "expand_fallback",
        "localclone/lccopy.sh": "expand_fallback",
    }
    for rel, symbol in callers.items():
        text = (AGENT / "lib" / rel).read_text(encoding="utf-8")
        assert symbol in text, rel
        assert not re.search(r"writing at the image's own size|writing at the source's own size|restoring at the image's own size", text), rel
