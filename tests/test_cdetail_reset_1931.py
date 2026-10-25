"""#1931 (found porting #1916, 21/10): `$RUN_DIR/cdetail.txt` -- the
per-partition table a capture reports (capturedetail.sh) -- outlived its
capture. job_reset did not remove it, so a second capture in the same boot:

* between target_init (capture.sh) and cd_init, reported the LAST capture's
  partitions, as if they were its own;
* in cd_init, carried the last capture's used_bytes over as if measured now
  (principle 5b: a number with no real source).

The test runs the real job_reset, target_init, build_progress and cd_init.
"""

from __future__ import annotations

import json
import subprocess

from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH))

#: the first capture's table, left behind: partition 3 measured at 777 bytes used
OLD = "3|windows|ntfs|1000|777|done\n"


def _second_capture(tmp_path) -> tuple[dict, str]:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    (run / "cdetail.txt").write_text(OLD, newline="\n")
    (run / "parts.txt").write_text("3|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|U|2048|4\n", newline="\n")
    script = (
        f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/capturedetail.sh; '
        '_fs_of() { echo ntfs; }; partition_node() { echo "/dev/$1$2"; }; _partition_role() { echo windows; }; '
        'job_reset; target_init sda 0; build_progress "" aa:bb:cc:dd:ee:ff tsk_f0fe > "$RUN_DIR/first.json"; '
        'cd_init sda "$RUN_DIR/parts.txt" 512; cat "$RUN_DIR/cdetail.txt"')
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=30,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    return json.loads((run / "first.json").read_text(encoding="utf-8")), out.stdout


def test_the_first_report_of_a_second_capture_has_no_partitions_of_the_last(tmp_path):
    first, _ = _second_capture(tmp_path)
    (target,) = first["targets"]
    assert "partitions" not in target, target


def test_used_bytes_of_the_last_capture_are_not_carried_as_measured(tmp_path):
    _, table = _second_capture(tmp_path)
    assert table.strip() == "3|windows|ntfs|2048|-|waiting", table   # "-" = not measured, never 777
