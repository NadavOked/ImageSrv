"""#3107 -- a pulled drawer's sentence carried "(מדיניות החשמל של SATA: host5=keep_firmware_set…)".

Metal 13/11 (cloner 2, chunk engine): Nadav pulled drawer 3 (ata3) on purpose. The right reason
showed -- "הכונן נעלם מהמערכת (נשלף או נותק)" -- and right after it a clause that belonged to
nothing: the machine's whole host list, English, a firmware term, overflowing the reason's field.
``drawer_lpm_hint`` (verdict.sh) printed ``$RUN_DIR/sata_lpm`` whole, on every gone drawer.

The rules now (decided in #3107):
* only the drawer's OWN host (its ata port -> the scsi host beside it in sysfs);
* only a policy that saves power -- ``keep_firmware_settings`` and ``unreadable`` are "unknown",
  not a suspect (the file and the hello still carry them: that is where they belong);
* never beside a pull sign (``drawer_port_gone``: DevExch, or link down as the last word):
  a pull wins over a slumber -- the hint was a sentence about a drive nobody pulled;
* the words are Hebrew, without a host name or a policy name.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, posix
from test_sata_lpm_1827 import drawer_on_host

pytestmark = requires_native("sh", posix=True, why="sysfs symlinks and a POSIX shell")

GONE = "הכונן נעלם מהמערכת (נשלף או נותק)"
HINT = " (חיסכון בחשמל פעיל בחריץ הזה — ייתכן שהכונן נרדם)"

#: the pull of 13/11 as ata3 printed it -- line noise (10B8B) beside DevExch, then link down
PULL = [
    "[  338.912345] ata3: exception Emask 0x10 SAct 0x1 SErr 0x4050002 action 0xe frozen",
    "[  338.912360] ata3: SError: { RecovComm PHYRdyChg 10B8B DevExch }",
    "[  339.700001] ata3: SATA link down (SStatus 0 SControl 300)",
]


def why(tmp_path: Path, recorded: str | None, dmesg: list[str] | None = None) -> str:
    """drawer_dead_mark on sda (ata3, scsi host2) that left sysfs; returns DRAWER_WHY."""
    drawer_on_host(tmp_path, "sda", ata=3, host=2)
    run = tmp_path / "run"
    if recorded is not None:
        (run / "sata_lpm").write_text(recorded + "\n", encoding="utf-8", newline="\n")
    (tmp_path / "kmsg").write_text("\n".join(dmesg or []) + "\n", encoding="utf-8", newline="\n")
    script = (
        f"RUN_DIR={posix(run)!r}; SYSROOT={posix(tmp_path / 'root')!r}; LOG_FILE={posix(run / 'log')!r}; "
        f". {posix(AGENT / 'lib/restore/verdict.sh')!r}; log() {{ :; }}; event_beep() {{ :; }}; "
        f"drawer_dead_mark sda 1 {posix(tmp_path / 'kmsg')!r} && printf '%s' \"$DRAWER_WHY\""
    )
    proc = subprocess.run(["sh", "-c", script], text=True, capture_output=True, check=False,
                          stdin=subprocess.DEVNULL, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_its_own_host_on_a_power_saving_policy_is_said_in_hebrew(tmp_path):
    out = why(tmp_path, "host2=med_power_with_dipm")
    assert out == GONE + HINT
    assert not re.search("[A-Za-z]", HINT)          # no host name, no policy name


def test_the_machine_list_is_not_printed_only_the_drawers_host_counts(tmp_path):
    """The field report: host5 is in the record, the pulled drawer is on host2."""
    assert why(tmp_path, "host5=med_power_with_dipm") == GONE
    both = why(tmp_path, "host5=min_power host2=medium_power")
    assert both == GONE + HINT and "host5" not in both


@pytest.mark.parametrize("recorded", [
    "host2=keep_firmware_settings",     # the kernel leaves the firmware's setting: not a policy that slumbers
    "host2=unreadable",                 # unknown, not a suspect
    "host5=keep_firmware_settings",     # the report itself: another host, and not a suspect anyway
])
def test_an_unknown_policy_is_not_a_suspect(tmp_path, recorded):
    assert why(tmp_path, recorded) == GONE


def test_a_pull_sign_wins_over_the_slumber(tmp_path):
    """DevExch / link down on the drawer's ata port: the drive was pulled -- the hint is off even
    when its own host reads a power-saving policy."""
    assert why(tmp_path, "host2=med_power_with_dipm", PULL) == GONE
    assert why(tmp_path, "host2=min_power", [PULL[2]]) == GONE      # link down as the last word


def test_a_link_that_came_back_is_not_a_pull_sign(tmp_path):
    back = [PULL[2], "[  345.0] ata3: SATA link up 6.0 Gbps (SStatus 133 SControl 300)"]
    assert why(tmp_path, "host2=med_power_with_dipm", back) == GONE + HINT


def test_another_ports_pull_is_not_this_drawers(tmp_path):
    other = [line.replace("ata3", "ata4") for line in PULL]
    assert why(tmp_path, "host2=med_power_with_dipm", other) == GONE + HINT


@pytest.mark.parametrize("recorded", ["max_performance", "none", None])
def test_nothing_recorded_or_all_max_performance_says_nothing(tmp_path, recorded):
    assert why(tmp_path, recorded) == GONE
