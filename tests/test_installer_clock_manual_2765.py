"""#2765 (Nadav 09/11): "ידני — בלי שרת זמן" in the installer.

The chain, each link tested here:

* ``installer/lib/clock.sh`` -- the answer ``clock_manual`` ("YYYY-MM-DD HH:MM[:SS]" in ``timezone``;
  a calendar day that exists, 2000..2099) and its exclusion of ``ntp_servers``; the chrony file byte
  for byte what the server writes; the clock set **in the target's chroot** and the running clock
  read back, then the hardware clock -- and a failure of either **fails the installation by name**
  (the operator asked for this very time: a server that comes up with another one is the dangerous
  result, unlike an unanswered time server);
* the engine -- a refused value is named before any disk command; the moment the time falls on is
  written to the target's answers (``ntp_manual_at``) for the first boot;
* ``gui-bridge.sh`` -- ``clock_manual=`` in the answers file, the engine's refusal on that field,
  and ``finish`` reporting ``clock=manual``;
* firstboot -> ``setup-boot-server.sh --ntp-manual-at`` -> the console's two settings (the setup
  side is in ``test_manual_clock_2765.py``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from server.system import timesync
from test_installer_clock_2144 import _clock_box, _sh, SH_ONLY
from test_installer_engine import LINUX_ONLY, SH, _answers, _command_shims, _machine, _normal, _run
from test_installer_gui_bridge import FORM, bridge
from test_installer_gui_bridge import pytestmark as BRIDGE_ONLY   # sh + jq, not Windows
from test_installer_gui_bridge import _machine as _bridge_machine

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "installer" / "lib"
MANUAL = "2026-11-09 14:32"


def _with(answers: Path, **extra: str) -> Path:
    answers.write_text(answers.read_text() + "".join(f"{k}={v}\n" for k, v in extra.items()), newline="\n")
    return answers


# --- the rule ---------------------------------------------------------------------

ACCEPTED = ["", "2026-11-09 14:32", "2026-11-09 14:32:59", "2000-01-01 00:00", "2099-12-31 23:59",
            "2028-02-29 12:00", "2026-04-30 09:05", "2026-10-08 08:08"]   # leap day; leading zeros are not octal
REFUSED = ["soon", "2026-11-09", "14:32", "2026-11-09T14:32", "2026-11-09 14:32 ", "2026-11-09  14:32",
           "2026-02-29 12:00", "2026-02-31 10:00", "2026-04-31 10:00", "2026-13-01 10:00", "2026-00-10 10:00",
           "2026-11-00 10:00", "2026-11-09 24:00", "2026-11-09 14:60", "1999-12-31 23:59", "2100-01-01 00:00",
           "2026-11-09 14:32:60", "2026-11-09 14:32x", "$(reboot)", "2026-11-09 14:32;reboot", "09/11/2026 14:32"]


@SH_ONLY
@pytest.mark.parametrize("value", ACCEPTED)
def test_a_real_date_and_time_is_accepted(value):
    got = _sh('ANSWER_NTP_SERVERS=; ANSWER_CLOCK_MANUAL=$1; validate_clock_manual; echo ok', value)
    assert got.stdout.strip() == "ok", got.stdout + got.stderr


@SH_ONLY
@pytest.mark.parametrize("value", REFUSED)
def test_anything_else_is_refused_by_name(value):
    got = _sh('ANSWER_NTP_SERVERS=; ANSWER_CLOCK_MANUAL=$1; validate_clock_manual; echo ok', value)
    assert got.stdout.strip() == "FAIL invalid-clock-manual", got.stdout + got.stderr


@SH_ONLY
def test_a_time_server_and_a_manual_time_are_alternatives():
    got = _sh('ANSWER_NTP_SERVERS=$1; ANSWER_CLOCK_MANUAL=$2; validate_clock_manual; echo ok', "10.44.0.250", MANUAL)
    assert got.stdout.strip() == "FAIL invalid-clock-manual", got.stdout + got.stderr


@SH_ONLY
def test_the_chrony_file_is_byte_for_byte_what_the_server_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "CONF_DIR", tmp_path)
    monkeypatch.setattr(timesync, "CLIENT_CONF", tmp_path / "imagesrv.conf")
    timesync._write_manual()
    got = subprocess.run([SH, "-c", f'. "{(LIB / "clock.sh").as_posix()}"; chrony_manual_text'],
                         capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    assert got.returncode == 0, got.stderr
    assert got.stdout == (tmp_path / "imagesrv.conf").read_bytes().replace(b"\r\n", b"\n")


# --- the engine -------------------------------------------------------------------

@LINUX_ONLY
@pytest.mark.parametrize("extra", [{"clock_manual": "2026-02-31 10:00"}, {"clock_manual": "soon"},
                                   {"ntp_servers": "10.44.0.250", "clock_manual": MANUAL}])
def test_a_refused_manual_time_is_named_before_any_disk_command(tmp_path, extra):
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _with(_answers(tmp_path / "answers"), **extra), "--dry-run")
    assert got.returncode == 2 and "[invalid-clock-manual]" in got.stderr, got.stderr
    assert not calls.exists()


@LINUX_ONLY
def test_the_dry_run_converts_in_the_target_sets_the_clock_there_and_asks_no_time_server(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _with(_answers(tmp_path / "answers"), clock_manual=MANUAL), "--dry-run")
    assert got.returncode == 0, got.stderr
    lines = _normal(got.stdout, box)
    convert = f"+ chroot <target> env TZ=Asia/Jerusalem date -d '{MANUAL}' '+%s'"
    assert convert in lines, "the target has tzdata and GNU date; the live image's busybox is not relied on"
    assert "+ chroot <target> date -u -s '@<epoch>'" in lines and "+ chroot <target> hwclock --systohc --utc" in lines
    assert not [line for line in lines if "chronyd" in line], "no time server is asked"
    chrony = "+ write-target mode=0644 <target>/etc/chrony/conf.d/imagesrv.conf"
    assert chrony in lines and lines.index(chrony) < lines.index(convert) < lines.index("+ chroot <target> hwclock --systohc --utc")
    assert lines.index("+ chroot <target> hwclock --systohc --utc") < lines.index("+ umount <target>/dev"), "chroot needs /dev for the RTC"


@LINUX_ONLY
def test_without_a_manual_time_nothing_of_this_runs_and_the_time_server_is_asked_as_before(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert got.returncode == 0, got.stderr
    lines = _normal(got.stdout, box)
    assert not [line for line in lines if "date -u -s" in line or "date -d" in line]
    assert [line for line in lines if "chronyd -Q" in line]
    # #2400: the one hwclock here is the synced source's -- right after its step (chronyd -q), not the manual time's
    rtc = [i for i, line in enumerate(lines) if "hwclock" in line]
    assert len(rtc) == 1 and "chronyd -q" in lines[rtc[0] - 1], lines


def _manual_box(tmp_path: Path, chroot_extra: str = ""):
    """The full shimmed install whose chroot answers the clock commands. `date -d` answers the moment the
    install started (the first chroot call), as the GUI's shifted time would: so the epoch the engine
    sets is the real now, and its read-back is honest."""
    box, calls = _clock_box(tmp_path, "exit 0")
    shim = tmp_path / "bin" / "chroot"
    head, rest = shim.read_text().split("\n", 1)
    t0 = tmp_path / "t0"
    shim.write_text(
        f'{head}\n[ -e {t0} ] || date +%s > {t0}\n'
        f'case "$*" in\n*"date -u -d"*) echo 2026-11-09T12:32:00+00:00; exit 0 ;;\n*"date -d"*) cat {t0}; exit 0 ;;\n'
        f'{chroot_extra}esac\n{rest}', newline="\n")
    return box, calls


@LINUX_ONLY
def test_the_clock_is_set_read_back_and_written_and_the_moment_goes_to_the_servers_answers(tmp_path):
    box, calls = _manual_box(tmp_path)
    got = _run(box, _with(_answers(tmp_path / "answers"), clock_manual=MANUAL))
    assert got.returncode == 0, got.stderr[-3000:]
    assert box["state"].read_text(encoding="utf-8").splitlines()[0] == "state=done"
    target = box["target"]
    assert (target / "etc/chrony/conf.d/imagesrv.conf").read_bytes() == (
        "# Managed by ImageSrv — time source (#2765): manual, no time server\n").encode("utf-8")
    answers = (target / "etc/imagesrv/answers").read_text()
    assert "\nntp_servers=\n" in answers and "\nntp_manual_at=2026-11-09T12:32:00+00:00\n" in answers
    record = (Path(box["state"]).parent / "clock-check").read_text()
    assert record == "clock=manual\nclock_server=\nclock_manual_at=2026-11-09T12:32:00+00:00\n"
    seen = calls.read_text(encoding="utf-8").splitlines()
    sets = [line for line in seen if line.startswith("chroot ") and "date -u -s @" in line]
    assert len(sets) == 1, seen
    assert [line for line in seen if "hwclock --systohc --utc" in line]
    assert not [line for line in seen if "chronyd" in line], "no time server is asked"
    assert seen.index(sets[0]) < [i for i, line in enumerate(seen) if "hwclock" in line][0], "the running clock, then the hardware clock"
    assert "installer: clock manual (Asia/Jerusalem) set to 2026-11-09 14:32" in got.stderr


@LINUX_ONLY
def test_a_time_server_install_writes_no_manual_moment(tmp_path):
    box, _ = _clock_box(tmp_path, "echo 'System clock wrong by 0.003127 seconds (ignored)' >&2; exit 0")
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 0, got.stderr[-3000:]
    assert "\nntp_manual_at=\n" in (box["target"] / "etc/imagesrv/answers").read_text()


@LINUX_ONLY
def test_a_clock_that_would_not_be_set_fails_the_installation_by_name(tmp_path):
    box, _ = _manual_box(tmp_path, '*"date -u -s"*) exit 1 ;;\n')
    got = _run(box, _with(_answers(tmp_path / "answers"), clock_manual=MANUAL))
    assert got.returncode != 0 and "[clock-manual-failed]" in got.stderr, got.stderr[-2000:]
    assert box["state"].read_text(encoding="utf-8").splitlines()[0] == "state=failed"


@LINUX_ONLY
def test_a_date_that_exits_zero_and_changes_nothing_is_not_a_set_clock(tmp_path):
    """Principle 5: the clock is read back. A target whose `date -u -s` is a no-op while the set time is
    an hour away from the running clock fails, instead of reporting "set"."""
    box, _ = _manual_box(tmp_path)
    shim = tmp_path / "bin" / "chroot"
    text = shim.read_text()
    needle = f"cat {tmp_path}/t0; exit 0"
    assert needle in text
    shim.write_text(text.replace(needle, f"echo $(( $(cat {tmp_path}/t0) + 3600 )); exit 0"), newline="\n")
    got = _run(box, _with(_answers(tmp_path / "answers"), clock_manual=MANUAL))
    assert got.returncode != 0 and "[clock-manual-failed]" in got.stderr and "reads" in got.stderr, got.stderr[-2000:]


@LINUX_ONLY
def test_a_hardware_clock_that_would_not_be_written_fails_by_name_too(tmp_path):
    box, _ = _manual_box(tmp_path, '*hwclock*) exit 1 ;;\n')
    got = _run(box, _with(_answers(tmp_path / "answers"), clock_manual=MANUAL))
    assert got.returncode != 0 and "[clock-manual-rtc]" in got.stderr, got.stderr[-2000:]
    assert box["state"].read_text(encoding="utf-8").splitlines()[0] == "state=failed"


def test_the_engine_marks_its_start_and_resolves_the_moment_before_writing_the_answers():
    engine = (REPO / "installer" / "imagesrv-install").read_text(encoding="utf-8")
    assert engine.index("validate_answers\nclock_mark_start") < engine.index("partition_disk")
    answers = (LIB / "answers.sh").read_text(encoding="utf-8")
    assert "clock_manual) ANSWER_CLOCK_MANUAL=$_as_value ;;" in answers and "validate_clock_manual" in answers
    finish = (LIB / "finish.sh").read_text(encoding="utf-8")
    assert finish.index("    write_chrony_source\n    resolve_manual_clock ") < finish.index("ntp_manual_at=$CLOCK_MANUAL_AT")


# --- the bridge -------------------------------------------------------------------

@BRIDGE_ONLY
def test_apply_writes_the_manual_time_and_no_time_server(tmp_path):
    m = _bridge_machine(tmp_path)
    rc, _, err = bridge(m, "apply", {**FORM, "clock_manual": "2026-11-09 14:32:07"})
    assert rc == 0, err
    text = (m["run"] / "answers").read_text()
    assert "\nclock_manual=2026-11-09 14:32:07\n" in text and "\nntp_servers=\n" in text


@BRIDGE_ONLY
def test_the_engines_refusal_lands_on_the_manual_time_field(tmp_path):
    m = _bridge_machine(tmp_path)
    engine = tmp_path / "cm-engine"
    engine.write_text("#!/bin/sh\n"
                      "grep -q '^clock_manual=2026-02-31 10:00$' \"$1\" && "
                      "{ echo 'installer: [invalid-clock-manual] clock_manual is not a date that exists' >&2; exit 2; }\n"
                      "exit 0\n")
    engine.chmod(0o755)
    rc, out, err = bridge(m, "validate", {**FORM, "clock_manual": "2026-02-31 10:00", "step": "6"}, ENGINE=str(engine))
    assert rc == 0, err
    assert out["errors.clock_manual"] == ["התאריך והשעה אינם תקינים — בצורה 2026-11-09 14:32, שנה 2000 עד 2099"]
    assert "errors.disk" not in out and "errors.ntp_servers" not in out


@BRIDGE_ONLY
def test_finish_reports_a_manual_clock(tmp_path):
    m = _bridge_machine(tmp_path)
    (m["run"] / "clock-check").write_text("clock=manual\nclock_server=\nclock_manual_at=2026-11-09T12:32:00+00:00\n")
    rc, out, err = bridge(m, "finish")
    assert rc == 0, err
    assert out["clock"] == ["manual"] and "clock_offset_ms" not in out


def test_the_bridge_and_the_field_tables_know_the_new_code():
    assert 'emit clock_manual "$(field clock_manual)"' in (REPO / "installer" / "gui-bridge.sh").read_text(encoding="utf-8")
    fields = (LIB / "fielderrors.sh").read_text(encoding="utf-8")
    assert "invalid-clock-manual) printf clock_manual ;;" in fields
    assert os.path.exists(LIB / "clock.sh")
