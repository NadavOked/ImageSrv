"""#1956 (mockup 1956-installer-backup, Nadav 06/11): the hour and the reserved space, from the
installer's answers to the server's settings.

* ``installer/lib/backup.sh`` -- ``backup_time`` and ``backup_reserve_gb``: empty or absent = the
  server's defaults; otherwise the console's own rules, and 20% of the chosen disk;
* ``gui-bridge.sh`` -- both in the answers file; the engine's refusal lands on the right field;
* ``finish.sh`` and firstboot -- the target's answers, ``setup-boot-server --backup-time/-reserve-gb``,
  and there the settings ``POST /backup-policy`` writes.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from server.db import connect, get_setting
from server.system import backup_job
from test_installer_clock_2144 import _clock_box
from test_installer_engine import LINUX_ONLY, SH, _answers, _command_shims, _machine, _run
from test_installer_gui_bridge import FORM, bridge
from test_installer_gui_bridge import pytestmark as BRIDGE_ONLY   # sh + jq, not Windows
from test_installer_gui_bridge import _machine as _bridge_machine

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "installer" / "lib"
SH_ONLY = pytest.mark.skipif(SH is None, reason="a POSIX sh is required")
GIB_500 = 500 * 10 ** 9 // 512       # sectors of a 500 GB disk: the cap is 100 GB


def _sh(script: str, sysroot: Path, *args: str) -> subprocess.CompletedProcess:
    prelude = (f'fail() {{ printf "FAIL %s\\n" "$1" >&2; exit 2; }}; SYSROOT="{sysroot.as_posix()}"; DISK_NAME=sda; '
               f'. "{(LIB / "backup.sh").as_posix()}"; ')
    return subprocess.run([SH, "-c", prelude + script, "_", *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)


@pytest.fixture()
def disk(tmp_path) -> Path:
    block = tmp_path / "sys" / "block" / "sda"
    block.mkdir(parents=True)
    (block / "size").write_text(f"{GIB_500}\n", newline="\n")
    return tmp_path


def _validate(sysroot: Path, at: str, gb: str) -> subprocess.CompletedProcess:
    return _sh('ANSWER_BACKUP_TIME=$1; ANSWER_BACKUP_RESERVE_GB=$2; validate_backup; echo ok', sysroot, at, gb)


# --- the rules: the console's -----------------------------------------------------------

TIMES = ["00:00", "02:00", "09:59", "19:30", "23:59", "24:00", "2:00", "02:60", "02:0", "0200", "aa:bb", "02:00 "]
SIZES = ["1", "5", "20", "100", "999999", "0", "00", "05", "-1", "1.5", "abc", "1000000", " 5"]


@SH_ONLY
@pytest.mark.parametrize("value", TIMES)
def test_the_engine_and_the_server_agree_on_the_hour(value, disk):
    assert (_validate(disk, value, "").stdout.strip() == "ok") == (backup_job.parse_time(value) is not None), value


@SH_ONLY
@pytest.mark.parametrize("value", SIZES)
def test_the_engine_is_no_looser_than_the_server_on_the_size(value, disk):
    """The server reads "05" as 5; the engine refuses it. The other way round would be a value the
    installer accepts and firstboot then refuses, after the disk was erased."""
    accepted = _sh('valid_backup_reserve "$1" && echo ok', disk, value).stdout.strip() == "ok"
    if accepted:
        assert backup_job.parse_reserve(value) is not None, value


@SH_ONLY
def test_empty_is_the_servers_defaults_and_is_not_held_against_the_disk(tmp_path):
    got = _validate(tmp_path, "", "")                      # no sys/block at all: nothing to read
    assert got.stdout.strip() == "ok", got.stderr


@SH_ONLY
@pytest.mark.parametrize("gb, ok", [("5", True), ("100", True), ("101", False), ("999999", False)])
def test_the_cap_is_twenty_percent_of_the_chosen_disk(disk, gb, ok):
    got = _validate(disk, "02:00", gb)
    assert (got.stdout.strip() == "ok") is ok, got.stderr
    if not ok:
        assert "invalid-backup-reserve" in got.stderr


@SH_ONLY
def test_a_disk_size_that_cannot_be_read_is_a_named_failure_not_no_cap(tmp_path):
    got = _validate(tmp_path, "02:00", "5")
    assert got.stdout.strip() != "ok" and "backup-disk-size" in got.stderr, got.stderr


# --- the engine ---------------------------------------------------------------------------

def _with(answers: Path, **values: str) -> Path:
    answers.write_text(answers.read_text() + "".join(f"{k}={v}\n" for k, v in values.items()), newline="\n")
    return answers


@LINUX_ONLY
def test_a_refused_hour_stops_the_install_before_any_disk_command(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _with(_answers(tmp_path / "answers"), backup_time="25:00"), "--dry-run")
    assert got.returncode != 0 and "[invalid-backup-time]" in got.stderr, got.stderr
    assert "sgdisk" not in got.stdout


@LINUX_ONLY
def test_old_answers_without_the_keys_still_install(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert got.returncode == 0, got.stderr


@LINUX_ONLY
def test_the_answers_reach_the_target(tmp_path):
    box, _ = _clock_box(tmp_path, "exit 0")
    got = _run(box, _with(_answers(tmp_path / "answers"), backup_time="23:15", backup_reserve_gb="1"))
    assert got.returncode == 0, got.stderr[-3000:]
    text = (box["target"] / "etc/imagesrv/answers").read_text()
    assert "\nbackup_time=23:15\nbackup_reserve_gb=1\n" in text


def test_the_wiring():
    engine = (REPO / "installer" / "imagesrv-install").read_text(encoding="utf-8")
    assert '. "$INSTALLER_DIR/lib/backup.sh"' in engine
    answers = (LIB / "answers.sh").read_text(encoding="utf-8")
    assert "    validate_backup " in answers
    firstboot = (REPO / "tools" / "iso" / "firstboot-answers.sh").read_text(encoding="utf-8")
    assert 'argv+=(--backup-time "$(answers_get backup_time)")' in firstboot
    assert 'argv+=(--backup-reserve-gb "$(answers_get backup_reserve_gb)")' in firstboot


# --- the bridge ---------------------------------------------------------------------------

@BRIDGE_ONLY
def test_apply_writes_the_choice_and_a_form_without_one_is_empty(tmp_path):
    m = _bridge_machine(tmp_path)
    rc, _, err = bridge(m, "apply", {**FORM, "backup_time": "23:15", "backup_reserve_gb": "40"})
    assert rc == 0, err
    text = (m["run"] / "answers").read_text()
    assert "backup_time=23:15\n" in text and "backup_reserve_gb=40\n" in text
    (tmp_path / "plain").mkdir()
    m = _bridge_machine(tmp_path / "plain")
    rc, _, err = bridge(m, "apply", {**FORM})
    assert rc == 0, err
    assert "\nbackup_time=\nbackup_reserve_gb=\n" in (m["run"] / "answers").read_text()


@BRIDGE_ONLY
@pytest.mark.parametrize("code, key, line", [
    ("invalid-backup-time", "backup_time", "שעת הגיבוי אינה תקינה"),
    ("invalid-backup-reserve", "backup_reserve_gb", "המקום השמור אינו תקין"),
    ("backup-disk-size", "backup_reserve_gb", "לא הצלחנו לקרוא את גודל הדיסק"),
])
def test_the_engines_refusal_lands_on_its_field(tmp_path, code, key, line):
    m = _bridge_machine(tmp_path)
    engine = tmp_path / "engine"
    engine.write_text(f"#!/bin/sh\necho 'installer: [{code}] no' >&2; exit 2\n")
    engine.chmod(0o755)
    rc, out, err = bridge(m, "validate", {**FORM, "step": "7"}, ENGINE=str(engine))
    assert rc == 0, err
    assert line in out[f"errors.{key}"][0], out
    assert "errors.disk" not in out and "errors.password" not in out


# --- setup-boot-server ----------------------------------------------------------------------

def _setup_snippet(tmp_path: Path) -> str:
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    block = re.search(r'BACKUP_RESERVE_GB="\$BACKUP_RESERVE_GB" python3 - <<PYEOF\n(.*?)\nPYEOF\n', setup, re.S)
    assert block, "the settings are written by the heredoc after --ntp-servers'"
    return block.group(1).replace("$APP_DIR", REPO.as_posix()).replace("$DATA_DIR", tmp_path.as_posix())


def _setup(tmp_path: Path, at: str, gb: str) -> subprocess.CompletedProcess:
    connect(tmp_path / "imagesrv.db").close()
    return subprocess.run([sys.executable, "-c", _setup_snippet(tmp_path)], capture_output=True, text=True,
                          env={**os.environ, "BACKUP_TIME": at, "BACKUP_RESERVE_GB": gb, "PYTHONIOENCODING": "utf-8"},
                          encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)


def test_setup_writes_the_consoles_settings(tmp_path):
    got = _setup(tmp_path, "23:15", "40")
    assert got.returncode == 0, got.stderr
    conn = connect(tmp_path / "imagesrv.db")
    assert get_setting(conn, "backup_time") == "23:15" and get_setting(conn, "backup_reserve_gb") == "40"


def test_setup_leaves_an_unset_value_unset(tmp_path):
    assert _setup(tmp_path, "", "40").returncode == 0
    conn = connect(tmp_path / "imagesrv.db")
    assert get_setting(conn, "backup_time") is None and get_setting(conn, "backup_reserve_gb") == "40"


def test_setup_refuses_what_the_console_would_refuse(tmp_path):
    got = _setup(tmp_path, "25:00", "5")
    assert got.returncode != 0 and "backup_time" in got.stderr
    assert get_setting(connect(tmp_path / "imagesrv.db"), "backup_time") is None
