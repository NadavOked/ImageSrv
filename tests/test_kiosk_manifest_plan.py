"""Metal, cloner 2, 17/10 (main 9eba8e7d): during the whole local clone the agent
log filled once a second with

    /usr/bin/imagesrv-kiosk: line 249: manifest_plan: not found
    /usr/bin/imagesrv-kiosk: line 260: manifest_plan: not found

Same family as #1484, the next function down the same call chain: the kiosk is
its own process, ``gui_libs`` is its whole load chain, and #1484 added diskfit.sh
to it. ``min_free_check`` (diskfit.sh) calls ``_expand_candidate`` (expand.sh),
which calls ``manifest_plan`` -- defined in restore.sh, which ``gui_libs`` did
not load. The two line numbers are the two ``manifest_plan`` calls in
``_expand_candidate``.

What broke: no candidate partition -> ``system_free_after`` counted no growth ->
the free space left in C: after the clone was computed as if the partition
stayed at the source's size -> a target that DOES fit was drawn "short".
(The failing direction is the conservative one; nothing was wrongly "ok".)

Loading restore.sh in the kiosk makes ``manifest_plan`` run beside the agent's
own call, and it wrote ``$RUN_DIR/plan.txt`` -- the agent's file. Two processes,
one scratch file: the agent's count check could pass on its own plan and its
``cat`` read the kiosk's. So the file is per process (``plan.$$.txt``).

The kiosk chain here is the real one (``gui_libs``), not a test prelude.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_cloner_gui import AGENT, BASH, posix, sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH), "jq")

G = 1_000_000_000
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
MS_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"


def _manifest(path: Path, system_size: int, system_used: int) -> None:
    def part(i, guid, role, fs, start, size, used):
        return {"index": i, "type_guid": guid, "role": role, "fs": fs, "start_sector": start,
                "size_bytes": size, "used_bytes": used, "file": f"p{i}.zst", "sha256": "0" * 64,
                "expandable": False}
    parts = [part(1, ESP, "esp", "vfat", 2048, 100 * 1024 * 1024, 1_000_000),
             part(2, MS_DATA, "windows", "ntfs", 206848, system_size, system_used)]
    path.write_text(json.dumps({"sector_size": 512, "partitions": parts}) + "\n", encoding="utf-8", newline="\n")


def _kiosk_min_free(tmp_path: Path, system_size: int, system_used: int, disk: int, need: int):
    """min_free_check as the kiosk calls it, in the kiosk's own load chain."""
    run = tmp_path / "run"; run.mkdir()
    (run / "min_free_gb").write_text("20\n", newline="\n")
    (run / "plan.txt").write_text("the agent's scratch\n", newline="\n")      # not the kiosk's to touch
    m = tmp_path / "m.json"; _manifest(m, system_size, system_used)
    sysroot = tmp_path / "sys"; sysroot.mkdir()
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(tmp_path / "gui")!r} IMAGESRV_TEST=1 '
             f'SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; '
             f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; '
             f'min_free_check {posix(m)!r} {disk} {need}')
    assert out.returncode == 0, out.stderr
    return out.stdout.strip(), out.stderr, run


def test_the_kiosk_chain_knows_where_manifest_plan_is(tmp_path):
    out = sh(f'export RUN_DIR={posix(tmp_path)!r} LIB_DIR={posix(flat_lib())!r}; '
             f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; type manifest_plan')
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_target_that_fits_only_after_the_system_partition_grows_is_not_drawn_short(tmp_path):
    # C: is 400 GB with 390 used: 10 GB free as the image has it, below the 20 GB rule.
    # The disk is 500 GB and the layout needs 410, so expand_last stretches C: by 90 GB.
    verdict, err, _ = _kiosk_min_free(tmp_path, 400 * G, 390 * G, 500 * G, 410 * G)
    assert "not found" not in err, err
    assert verdict == "ok", verdict


def test_the_free_space_rule_still_says_short_when_the_growth_is_not_enough(tmp_path):
    # a guard that holds in both states: no room to grow -> 10 GB free -> short, not ok
    verdict, _, _ = _kiosk_min_free(tmp_path, 400 * G, 390 * G, 410 * G, 410 * G)
    assert verdict.startswith("short|"), verdict


def test_a_roomy_system_partition_is_ok_with_or_without_growth(tmp_path):
    verdict, _, _ = _kiosk_min_free(tmp_path, 400 * G, 100 * G, 410 * G, 410 * G)
    assert verdict == "ok", verdict


def test_the_kiosk_never_writes_the_agents_plan_scratch_file(tmp_path):
    _, _, run = _kiosk_min_free(tmp_path, 400 * G, 390 * G, 500 * G, 410 * G)
    assert (run / "plan.txt").read_text(encoding="utf-8") == "the agent's scratch\n"
