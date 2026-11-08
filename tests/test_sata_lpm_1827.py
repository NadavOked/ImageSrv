"""A cloner's SATA links are held at max_performance, read back (#1827).

Debian's default LPM policy (med_power_with_dipm) lets an idle drawer slumber,
and some drives drop off the bus from it. The agent sets every scsi_host's
policy once the hello answer says "cloner" -- never on another role -- and the
evidence is the value read back, not the write. The result rides in the hello
and, when it is not max_performance, in a pulled drawer's sentence.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, fake_machine, posix, sh  # noqa: F401

pytestmark = requires_native("sh", why="the production helper is POSIX sh")

CORE = AGENT / "lib" / "core"
POLICY = "link_power_management_policy"


def _run(tmp_path: Path, role: str, body: str = "sata_lpm_cloner") -> subprocess.CompletedProcess[str]:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    script = (
        f"SYSROOT={posix(tmp_path / 'root')!r}; RUN_DIR={posix(run)!r}; D_ROLE={role!r}; "
        f"LOG_FILE={posix(run / 'agent.log')!r}; export SYSROOT RUN_DIR D_ROLE LOG_FILE; "
        f". {posix(CORE / 'common.sh')!r}; . {posix(CORE / 'satalpm.sh')!r}; {body}"
    )
    return subprocess.run(
        ["sh", "-c", script], text=True, capture_output=True, check=False,
        stdin=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"},
    )


def _host(tmp_path: Path, number: int, policy: str = "med_power_with_dipm") -> Path:
    host = tmp_path / "root/sys/class/scsi_host" / f"host{number}"
    host.mkdir(parents=True)
    (host / POLICY).write_text(policy + "\n", encoding="utf-8", newline="\n")
    return host / POLICY


def _result(tmp_path: Path) -> str:
    return (tmp_path / "run/sata_lpm").read_text(encoding="utf-8")


def test_a_cloner_sets_every_host_to_max_performance_and_records_the_read_back(tmp_path):
    host0, host1 = _host(tmp_path, 0), _host(tmp_path, 1, "min_power")
    proc = _run(tmp_path, "cloner")
    assert proc.returncode == 0, proc.stderr
    assert host0.read_text(encoding="utf-8") == "max_performance\n"
    assert host1.read_text(encoding="utf-8") == "max_performance\n"
    assert _result(tmp_path) == "max_performance\n"
    assert "sata lpm: max_performance (2 hosts)" in (tmp_path / "run/agent.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("role", ["classroom", "build", "unknown", ""])
def test_no_other_role_touches_the_policy(tmp_path, role):
    host0 = _host(tmp_path, 0)
    proc = _run(tmp_path, role)
    assert proc.returncode == 0, proc.stderr
    assert host0.read_text(encoding="utf-8") == "med_power_with_dipm\n"
    assert not (tmp_path / "run/sata_lpm").exists()


def test_a_host_that_does_not_read_back_is_named_not_counted_as_set(tmp_path):
    _host(tmp_path, 0)
    # host3's knob cannot be written or read: no read back is not "max_performance".
    (tmp_path / "root/sys/class/scsi_host/host3" / POLICY).mkdir(parents=True)
    proc = _run(tmp_path, "cloner")
    assert proc.returncode == 0, proc.stderr
    assert _result(tmp_path) == "host3=unreadable\n"


def test_no_host_with_the_knob_is_none_not_max_performance(tmp_path):
    (tmp_path / "root/sys/class/scsi_host/host0").mkdir(parents=True)   # e.g. a USB host
    proc = _run(tmp_path, "cloner")
    assert proc.returncode == 0, proc.stderr
    assert _result(tmp_path) == "none\n"


def test_it_runs_once_per_boot(tmp_path):
    host0 = _host(tmp_path, 0)
    _run(tmp_path, "cloner")
    host0.write_text("med_power_with_dipm\n", encoding="utf-8", newline="\n")
    _run(tmp_path, "cloner")
    assert host0.read_text(encoding="utf-8") == "med_power_with_dipm\n"


def test_the_hello_fragment_is_absent_until_it_ran(tmp_path):
    assert _run(tmp_path, "cloner", "sata_lpm_json").stdout == ""
    (tmp_path / "run/sata_lpm").write_text("host2=med_power_with_dipm\n", encoding="utf-8", newline="\n")
    out = _run(tmp_path, "cloner", "sata_lpm_json").stdout
    assert json.loads("{" + out.lstrip(",") + "}") == {"sata_lpm": "host2=med_power_with_dipm"}


def test_build_hello_carries_the_policy(fake_machine):  # noqa: F811
    env = (f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
           f'DEVROOT={posix(fake_machine["dev"])!r} '
           f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
           f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; . {posix(AGENT)}/lib/hw/diskinfo.sh; '
           f'. {posix(CORE)}/satalpm.sh; build_hello')
    assert "sata_lpm" not in json.loads(sh(env))
    (fake_machine["run"] / "sata_lpm").write_text("max_performance\n", encoding="utf-8", newline="\n")
    assert json.loads(sh(env))["sata_lpm"] == "max_performance"


def test_the_agent_sets_it_once_the_role_is_known():
    """The call sits in the main loop after read_answer (D_ROLE), before decide."""
    text = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    loop = text[text.index("    read_answer\n"):text.index("_dec=$(decide)")]
    assert "sata_lpm_cloner" in loop


def _watch(tmp_path: Path, recorded: str | None) -> str:
    """drawer_watch over one pulled drawer; returns the sentence it failed it with."""
    run = tmp_path / "run"
    (run / "targets/sda").mkdir(parents=True)
    (run / "targets/sda/state").write_text("writing\n", encoding="utf-8", newline="\n")
    if recorded is not None:
        (run / "sata_lpm").write_text(recorded + "\n", encoding="utf-8", newline="\n")
    script = (
        f"RUN_DIR={posix(run)!r}; SYSROOT={posix(tmp_path / 'root')!r}; LOG_FILE={posix(run / 'log')!r}; "
        f". {posix(AGENT / 'lib/restore/verdict.sh')!r}; "
        "log() { :; }; event_beep() { :; }; _nap() { :; }; dmesg() { :; }; turns=0; "
        'kill() { [ "$1" = -0 ] || return 0; turns=$((turns + 1)); [ "$turns" = 1 ]; }; '
        'drawer_dead_reason() { echo "$DRAWER_GONE_WHY"; }; '
        'fail_written_target() { printf "%s" "$2" > "$RUN_DIR/why"; }; '
        "drawer_watch 123 1 sda"
    )
    proc = subprocess.run(["sh", "-c", script], text=True, capture_output=True, check=False,
                          stdin=subprocess.DEVNULL, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    return (run / "why").read_text(encoding="utf-8")


def test_a_pulled_drawer_names_a_policy_that_was_not_max_performance(tmp_path):
    why = _watch(tmp_path, "host1=med_power_with_dipm")
    assert why.startswith("מחיצה 1: הכונן נעלם מהמערכת")   # server/disks/ata_cause.py: still "removed"
    assert "host1=med_power_with_dipm" in why


@pytest.mark.parametrize("recorded", ["max_performance", "none", None])
def test_a_pulled_drawer_says_nothing_of_lpm_otherwise(tmp_path, recorded):
    assert "SATA" not in _watch(tmp_path, recorded)
