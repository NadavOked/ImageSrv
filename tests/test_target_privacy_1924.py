"""#1924 (Nadav 20/10): in direct cloning and in distribution the server
learns only WHAT was written to WHICH disk -- the image, the target's serial
and drawer, the result and the times. Nothing from the target's content.

1. hello: a drawer (or any machine that is not a build machine) does not say
   `scheme` or `has_data` -- the server never used them (Nadav's check, and
   grep: only station.py passes them through, to the build machine's capture
   screen, which keeps them). `hostname_current` was always null and never
   read: gone.
2. the local clone's report carries exactly the agreed fields -- a new one
   must be thought about here, not slip in.
3. a target's error, which travels to the journal, carries no GUID and no
   partition name read from the disk (the values stay in the agent's log).
   Distribution: test_partition_guid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401 (fixture)
from test_lc_report_resend_1913 import CURL, RUN
from test_local_clone_agent import run

pytestmark = requires_native(("bash", BASH))


def _hello(fake, role: str) -> dict:
    out = sh(f'export SYSROOT={posix(fake["sysroot"])!r} DEVROOT={posix(fake["dev"])!r} '
             f'RUN_DIR={posix(fake["run"])!r} IFACE=eth0 IP=10.44.12.187 D_ROLE={role}; '
             f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; build_hello')
    return json.loads(out)


def test_a_drawer_does_not_say_what_is_on_it(fake_machine):  # noqa: F811
    for role in ("cloner", "classroom", ""):
        hello = _hello(fake_machine, role)
        (disk,) = hello["disks"]
        assert "scheme" not in disk and "has_data" not in disk, (role, disk)
        assert "hostname_current" not in hello
        # what stays: hardware
        assert {"dev", "size_bytes", "model", "serial", "removable", "port", "smart"} <= set(disk)


def test_the_build_machine_still_tells_its_capture_screen(fake_machine):  # noqa: F811
    (disk,) = _hello(fake_machine, "build")["disks"]
    assert disk["scheme"] == "gpt" and disk["has_data"] is True


#: the local clone report's fields (interfaces.md section 34), hardware,
#: numbers and the result -- nothing read from a disk's content
REPORT_KEYS = {"mac", "source", "targets", "shrunk", "source_restore", "stopped", "bytes", "elapsed_s",
               "shrink_path", "snapshot_teardown", "info_s", "resize_s", "relocations_mb", "copy_s",
               "verify_s", "restore_s"}


def test_the_local_clone_report_carries_only_the_agreed_fields(tmp_path):
    out = run(tmp_path, RUN + CURL + 'echo 200 > "$RUN_DIR/code"; lc_report')
    assert out.returncode == 0, out.stderr
    report = json.loads((tmp_path / "run" / "lc" / "run" / "report.json").read_text(encoding="utf-8"))
    assert set(report) == REPORT_KEYS
    assert set(report["source"]) == {"dev", "port", "serial"}
    # #2009: ntfs_note -- one of five fixed sentences (hibernation.sh LC_NTFS_*), no value of the disk in it
    assert {k for t in report["targets"] for k in t} == {"dev", "port", "serial", "state", "error", "ntfs_note"}


GUID = re.compile(r"\$_tc_(dg|want|gu|u|gn|wn)\b")


def test_no_target_error_names_a_guid_or_a_partition_name():
    """lc_table_check (lcplan.sh): every LC_ERROR is said without the
    values; the values go to the agent's log."""
    src = (Path(AGENT) / "lib" / "lcplan.sh").read_text(encoding="utf-8")
    errors = re.findall(r'LC_ERROR="([^"]*)"', src)
    assert errors
    leaking = [e for e in errors if GUID.search(e)]
    assert leaking == [], leaking
