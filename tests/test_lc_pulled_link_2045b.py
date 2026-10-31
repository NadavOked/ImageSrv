"""#2045, continued -- a pulled drawer whose WRITE failed before it left sysfs (metal 23/10).

The first fix (PR #2065) catches a pulled drawer by its node leaving
``/sys/block``. On metal (23/10 16:32, cloner 2, drawer 1 pulled at 21%) the
order was the other way round: ``ata1: exception … DevExch`` at 338.9 s, ``SATA
link down`` at 339.7/345.2/350.6, and only then ``I/O error, dev sdb … (WRITE)``
-- with ``/sys/block/sdb`` still there. ``drawer_dead_reason`` took the WRITE
line, and the operator read "מחיצה 3: הקרנל דחה כתיבה לכונן: I/O error, dev sdb,
sector 77047488 op 0x1:(WRITE)…": English, and not "pulled". The server's journal
said "sdb נכשל": the local clone's report read the port from sysfs at the end,
when the disk had none.

Here, through the real verdict.sh / localclone.sh:

* a WRITE error on a drawer whose ata port says the disk left (DevExch, or the
  link down as its last word) is "pulled", in Hebrew, and marked ``pulled``;
* a WRITE error with no such word, a link that came back, a media error, another
  port's link, or a link that went down before this drawer's run -- keeps the
  kernel's reason (the guards: not every write error is a pull);
* the report names the drawer by the port kept at ``target_init``: "דיסק 1".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server.agent import local_clone_report
from test_local_clone_agent import posix, run

pytestmark = requires_native("bash", posix=True, why="sysfs symlinks and a POSIX shell")

GONE = "הכונן נעלם מהמערכת (נשלף או נותק)"

#: the metal sequence of 23/10 16:32, ata1 = drawer 1 = sdb (timestamps from Nadav's dmesg)
METAL = [
    "[  338.912345] ata1: exception Emask 0x10 SAct 0x1 SErr 0x4050002 action 0xe frozen",
    "[  338.912360] ata1: SError: { RecovComm PHYRdyChg CommWake DevExch }",
    "[  338.912371] ata1.00: failed command: WRITE FPDMA QUEUED",
    "[  339.700001] ata1: SATA link down (SStatus 0 SControl 300)",
    "[  345.200001] ata1: SATA link down (SStatus 0 SControl 300)",
    "[  350.600001] ata1: SATA link down (SStatus 0 SControl 300)",
    "[  350.600200] I/O error, dev sdb, sector 77047488 op 0x1:(WRITE) flags 0x4000 phys_seg 32 prio class 2",
]
WRITE_ERR = METAL[-1]


def box(tmp_path: Path) -> Path:
    """sysfs as on the cloner: /sys/block/sd?/device -> .../ataN/host…/…, uptime 100."""
    b = tmp_path / "box"
    (b / "proc").mkdir(parents=True)
    (b / "proc" / "uptime").write_text("100.00 50.00\n")
    for disk, ata, serial in (("sdb", 1, "WD-PULLED"), ("sdc", 2, "SG-HEALTHY")):
        real = b / "sys" / "devices" / "pci0000:00" / "0000:00:1f.2" / f"ata{ata}" / f"host{ata - 1}" / \
            f"target{ata - 1}:0:0" / f"{ata - 1}:0:0:0"
        real.mkdir(parents=True)
        (real / "state").write_text("running\n")
        (real / "serial").write_text(serial + "\n")
        blk = b / "sys" / "block" / disk
        blk.mkdir(parents=True)
        os.symlink(real, blk / "device")
    return b


def reason(tmp_path: Path, kmsg: list[str], disk: str = "sdb") -> tuple[str, str]:
    """target_init (the drawer's run begins at uptime 100), then drawer_dead_reason on kmsg.
    Returns (rc, the reason)."""
    b = box(tmp_path)
    (b / "kmsg").write_text("\n".join(kmsg) + "\n")
    out = run(tmp_path, f'target_init {disk} 100; drawer_dead_reason {disk} 1 {posix(b / "kmsg")!r}; echo "rc=$?"',
              SYSROOT=posix(b))
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    return lines[-1], "\n".join(lines[:-1])


def test_the_metal_pull_is_pulled_in_hebrew(tmp_path):
    rc, why = reason(tmp_path, METAL)
    assert rc == "rc=0" and why == GONE, why
    assert not re.search("[A-Za-z]", why), why


def test_link_down_as_the_last_word_is_a_pull_even_without_devexch(tmp_path):
    rc, why = reason(tmp_path, [l for l in METAL if "DevExch" not in l])
    assert rc == "rc=0" and why == GONE, why


def test_line_noise_of_a_yanked_cable_does_not_hide_the_pull(tmp_path):
    rc, why = reason(tmp_path, [METAL[0], "[  338.912360] ata1: SError: { PHYRdyChg 10B8B DevExch }",
                                "[  338.912380] ata1.00: res 40/00:00:00:00:00/00:00:00:00:00/40 Emask 0x10 (ATA bus error)",
                                METAL[3], WRITE_ERR])
    assert rc == "rc=0" and why == GONE, why


@pytest.mark.parametrize("kmsg", [
    pytest.param([WRITE_ERR], id="no-word-from-the-port"),
    pytest.param(["[  339.700001] ata1: SATA link down (SStatus 0 SControl 300)",
                  "[  341.000001] ata1: SATA link up 3.0 Gbps (SStatus 123 SControl 300)", WRITE_ERR],
                 id="the-link-came-back"),
    pytest.param(["[  339.000001] ata1.00: error: { UNC }",
                  "[  339.700001] ata1: SATA link down (SStatus 0 SControl 300)", WRITE_ERR],
                 id="the-disk-reported-a-media-error"),
    pytest.param(["[  339.700001] ata2: SATA link down (SStatus 0 SControl 300)",
                  "[  339.700002] ata2: SError: { DevExch }", WRITE_ERR],
                 id="another-port"),
    pytest.param(["[  12.000001] ata1: SATA link down (SStatus 0 SControl 300)", WRITE_ERR],
                 id="before-this-run"),
])
def test_a_write_error_without_the_port_saying_so_keeps_the_kernel_reason(tmp_path, kmsg):
    rc, why = reason(tmp_path, kmsg)
    assert rc == "rc=0", why
    assert why.startswith("הקרנל דחה כתיבה לכונן: I/O error, dev sdb"), why


def test_no_write_error_is_no_death_even_with_the_link_down(tmp_path):
    """Unchanged by #2045b: the port's word alone does not kill a drawer (early detection is not built)."""
    rc, why = reason(tmp_path, METAL[:-1])
    assert rc == "rc=1" and why == "", why


def test_the_watcher_marks_the_metal_pull_pulled_and_fails_it_in_hebrew(tmp_path):
    b = box(tmp_path)
    (b / "kmsg").write_text("\n".join(METAL) + "\n")
    stubs = tmp_path / "stubs"; stubs.mkdir()
    out = run(tmp_path,
              f'mkdir -p {posix(stubs)!r}; printf "#!/bin/sh\\ncat {posix(b / "kmsg")}\\n" > {posix(stubs)}/dmesg; '
              f'chmod 0755 {posix(stubs)}/dmesg; export PATH="{posix(stubs)}:$PATH"; '
              'target_init sdb 100; target_set sdb writing; sleep 4 & s=$!; '
              'drawer_watch "$s" 3 sdb; wait "$s"; '
              'echo "state=$(cat "$RUN_DIR/targets/sdb/state")"; echo "error=$(cat "$RUN_DIR/targets/sdb/error")"; '
              'echo "pulled=$([ -e "$RUN_DIR/targets/sdb/pulled" ] && echo 1 || echo 0)"',
              timeout=60, SYSROOT=posix(b), WAIT_HELPER_S="2")
    assert out.returncode == 0, out.stderr
    got = dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)
    assert got["state"] == "failed", out.stdout
    assert got["error"] == f"מחיצה 3: {GONE}", got["error"]
    assert got["pulled"] == "1", out.stdout


def test_the_report_names_a_pulled_drawer_by_the_port_it_had(tmp_path):
    """journal 3109 (23/10): "sdb נכשל" -- the port was read from sysfs after the disk left."""
    b = box(tmp_path)
    out = run(tmp_path,
              'curl() { for a; do case "$a" in @*) cp "${a#@}" "$RUN_DIR/sent.json";; esac; done; echo 200; }; '
              'mkdir -p "$LC/run"; printf "sdb\\nsdc\\n" > "$LC/run/targets"; _lr_src=sda; '
              'echo 1000 > "$LC/run/started"; echo 1500 > "$LC/run/ended"; '
              'target_init sdb 100; target_init sdc 100; '
              f'rm -rf {posix(b / "sys" / "block" / "sdb")!r}; '   # pulled: out of sysfs
              f'echo failed > "$RUN_DIR/targets/sdb/state"; echo "מחיצה 3: {GONE}" > "$RUN_DIR/targets/sdb/error"; '
              'echo done > "$RUN_DIR/targets/sdc/state"; lc_report',
              SYSROOT=posix(b))
    assert out.returncode == 0, out.stderr
    body = json.loads((tmp_path / "run" / "sent.json").read_text(encoding="utf-8"))
    sdb, sdc = body["targets"]
    assert (sdb["port"], sdb["serial"]) == (1, "WD-PULLED"), sdb
    assert (sdc["port"], sdc["serial"]) == (2, "SG-HEALTHY"), sdc
    line = local_clone_report.journal_line(body)
    assert "דיסק 1 נכשל" in line and "sdb" not in line, line
