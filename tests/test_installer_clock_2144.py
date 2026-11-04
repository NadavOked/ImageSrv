"""#2144 (mockup 2144-time, Nadav 04/11): the server clock in the installer.

The chain, each link tested here:

* ``installer/lib/clock.sh`` -- the answer ``ntp_servers`` (empty = automatic,
  else up to four IPv4 addresses or names): a rule no looser than the
  console's (``timesync.validate``), the chrony file byte for byte what the
  server writes (``timesync._write_client``), and the check at the end of
  the installation -- synced / unsynced / **unchecked**, each by positive
  evidence, never failing the install;
* the engine -- a refused value is named before any disk command; the file
  and the check run inside the target before it is released;
* ``gui-bridge.sh`` -- ``ntp_servers=`` in the answers file, the engine's
  refusal on that field, and ``finish`` reporting the clock (no record =
  ``unchecked``);
* firstboot -> ``setup-boot-server.sh --ntp-servers`` -> the console's
  setting ``ntp_servers``, which the server's start writes to chrony again.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from server.db import connect, get_setting
from server.system import timesync
from test_installer_engine import (LINUX_ONLY, SH, _answers, _command_shims, _full_install_box,
                                   _machine, _normal, _run)
from test_installer_gui_bridge import FORM, bridge
from test_installer_gui_bridge import pytestmark as BRIDGE_ONLY   # sh + jq, not Windows
from test_installer_gui_bridge import _machine as _bridge_machine

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "installer" / "lib"
SH_ONLY = pytest.mark.skipif(SH is None, reason="a POSIX sh is required")


def _sh(script: str, *args: str) -> subprocess.CompletedProcess:
    """clock.sh's functions with answers.sh's valid_ipv4 -- no engine, no disk."""
    prelude = (f'fail() {{ printf "FAIL %s\\n" "$1"; exit 2; }}; '
               f'. "{(LIB / "answers.sh").as_posix()}"; . "{(LIB / "clock.sh").as_posix()}"; ')
    return subprocess.run([SH, "-c", prelude + script, "_", *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)


def _with_ntp(answers: Path, value: str) -> Path:
    answers.write_text(answers.read_text() + f"ntp_servers={value}\n", newline="\n")
    return answers


# --- the rule: no looser than the console's ------------------------------------

ACCEPTED = ["", "10.44.0.250", "  10.44.0.250   ntp.college.ac.il ", "a b c d", "ntp1.", "999.1.1.1",
            "x" * 63 + ".il"]
REFUSED = ["a b c d e", "bad_host", "-ntp", "ntp-", "a..b", ".ntp", "ntp/1", "10.0.0.1:123",
           "fe80::1", "x" * 64 + ".il", "ntp;reboot"]


@SH_ONLY
@pytest.mark.parametrize("value", ACCEPTED)
def test_what_the_installer_accepts_the_console_accepts_in_the_same_form(value):
    got = _sh('normalize_ntp_servers "$1"', value)
    assert got.returncode == 0, got.stdout + got.stderr
    assert got.stdout == timesync.validate(value)


@SH_ONLY
@pytest.mark.parametrize("value", REFUSED)
def test_what_the_installer_refuses_is_refused_by_name(value):
    got = _sh('ANSWER_NTP_SERVERS=$1; validate_ntp_servers; echo "ok $ANSWER_NTP_SERVERS"', value)
    assert got.stdout.strip() == "FAIL invalid-ntp-servers", got.stdout + got.stderr


@SH_ONLY
@pytest.mark.parametrize("value", ["", "10.44.0.250", "10.44.0.250 ntp.college.ac.il"])
def test_the_chrony_file_is_byte_for_byte_what_the_server_writes(tmp_path, monkeypatch, value):
    monkeypatch.setattr(timesync, "CONF_DIR", tmp_path)
    monkeypatch.setattr(timesync, "CLIENT_CONF", tmp_path / "imagesrv.conf")
    timesync._write_client(value)
    got = subprocess.run([SH, "-c", f'. "{(LIB / "clock.sh").as_posix()}"; chrony_source_text "$1"', "_", value],
                         capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
    assert got.returncode == 0, got.stderr
    # Path.write_text writes CRLF on Windows; the server runs on Debian
    assert got.stdout == (tmp_path / "imagesrv.conf").read_bytes().replace(b"\r\n", b"\n")


# --- the verdict: positive evidence for each of the three ----------------------

@SH_ONLY
@pytest.mark.parametrize("rc,out,state,offset", [
    (0, "2026-11-04T12:32:01Z chronyd version 4.6 starting\n"
        "2026-11-04T12:32:03Z System clock wrong by -0.002712 seconds (ignored)\n"
        "2026-11-04T12:32:03Z chronyd exiting", "synced", "3"),
    (0, "System clock wrong by 10800.123456 seconds (ignored)", "synced", "10800123"),
    (1, "2026-11-04T12:32:01Z chronyd version 4.6 starting\n2026-11-04T12:32:21Z Timeout reached\n"
        "2026-11-04T12:32:21Z chronyd exiting", "unsynced", ""),
    (1, "Fatal error : Could not open configuration file", "unchecked", ""),
    (127, "chroot: failed to run command 'timeout': No such file or directory", "unchecked", ""),
    (124, "", "unchecked", ""),
    (0, "chronyd exiting", "unchecked", ""),
    (0, "Timeout reached", "unchecked", ""),   # 0 with no offset line is not "did not answer"
])
def test_the_clock_verdict(rc, out, state, offset):
    got = _sh('clock_verdict "$1" "$2"; printf "%s|%s" "$CLOCK_STATE" "$CLOCK_OFFSET_MS"', str(rc), out)
    assert got.stdout == f"{state}|{offset}", got.stderr


# --- the engine -----------------------------------------------------------------

@LINUX_ONLY
@pytest.mark.parametrize("value", ["fe80::1", "a b c d e", "bad_host"])
def test_a_refused_time_source_is_named_before_any_disk_command(tmp_path, value):
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _with_ntp(_answers(tmp_path / "answers"), value), "--dry-run")
    assert got.returncode == 2 and "[invalid-ntp-servers]" in got.stderr, got.stderr
    assert not calls.exists()


@LINUX_ONLY
def test_the_dry_run_checks_the_chosen_server_inside_the_target(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _with_ntp(_answers(tmp_path / "answers"), "10.44.0.250  ntp.college"), "--dry-run")
    assert got.returncode == 0, got.stderr
    lines = _normal(got.stdout, box)
    check = ("+ chroot <target> timeout 30 chronyd -Q -t 20 'server 10.44.0.250 iburst' "
             "'server ntp.college iburst'")
    assert check in lines
    assert lines.index("+ write-target mode=0644 <target>/etc/chrony/conf.d/imagesrv.conf") < lines.index(check)
    assert lines.index(check) < lines.index("+ umount <target>/dev"), "chroot needs /dev and /proc bound"


@LINUX_ONLY
def test_automatic_checks_the_debian_pools_and_old_answers_are_automatic(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")   # no ntp_servers= line at all
    assert got.returncode == 0, got.stderr
    assert ("+ chroot <target> timeout 30 chronyd -Q -t 20 'pool 0.debian.pool.ntp.org iburst' "
            "'pool 1.debian.pool.ntp.org iburst' 'pool 2.debian.pool.ntp.org iburst' "
            "'pool 3.debian.pool.ntp.org iburst'") in _normal(got.stdout, box)


def _clock_box(tmp_path: Path, chronyd: str):
    """The full (not dry) shimmed install, its chroot answering `chronyd` as told."""
    box, calls = _full_install_box(tmp_path)
    shim = tmp_path / "bin" / "chroot"
    head, rest = shim.read_text().split("\n", 1)
    shim.write_text(f'{head}\ncase "$*" in *chronyd*) {chronyd} ;; esac\n{rest}', newline="\n")
    return box, calls


@LINUX_ONLY
def test_the_choice_lands_in_the_targets_chrony_and_answers_and_a_reply_is_synced(tmp_path):
    box, _ = _clock_box(tmp_path, "echo 'System clock wrong by 0.003127 seconds (ignored)' >&2; exit 0")
    got = _run(box, _with_ntp(_answers(tmp_path / "answers"), "10.44.0.250"))
    assert got.returncode == 0, got.stderr[-3000:]
    target = box["target"]
    assert (target / "etc/chrony/conf.d/imagesrv.conf").read_text() == (
        "# Managed by ImageSrv — time source (#1773)\nmakestep 1.0 -1\nserver 10.44.0.250 iburst\n")
    assert "\nntp_servers=10.44.0.250\n" in (target / "etc/imagesrv/answers").read_text()
    record = Path(box["state"]).parent / "clock-check"
    assert record.read_text() == "clock=synced\nclock_server=10.44.0.250\nclock_offset_ms=3\n"
    assert box["state"].read_text(encoding="utf-8").splitlines()[0] == "state=done"


@LINUX_ONLY
@pytest.mark.parametrize("chronyd,state", [
    ("echo 'Timeout reached' >&2; exit 1", "unsynced"),
    ("echo 'Fatal error : Could not open command socket' >&2; exit 1", "unchecked"),
])
def test_no_reply_or_no_answer_is_said_and_the_install_still_completes(tmp_path, chronyd, state):
    box, _ = _clock_box(tmp_path, chronyd)
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 0, got.stderr[-3000:]
    assert box["state"].read_text(encoding="utf-8").splitlines()[0] == "state=done"
    record = (Path(box["state"]).parent / "clock-check").read_text()
    assert record == f"clock={state}\nclock_server=debian.pool.ntp.org\n"
    assert f"installer: clock {state} (debian.pool.ntp.org)" in got.stderr


@LINUX_ONLY
def test_a_stale_record_is_never_read_as_this_installations(tmp_path):
    box, _ = _clock_box(tmp_path, "exit 1")
    stale = Path(box["state"]).parent / "clock-check"
    stale.write_text("clock=synced\nclock_server=10.44.0.250\nclock_offset_ms=1\n")
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 0, got.stderr[-3000:]
    assert stale.read_text().startswith("clock=unchecked\n")


def test_the_engine_sources_the_module_and_checks_before_releasing_the_target():
    engine = (REPO / "installer" / "imagesrv-install").read_text(encoding="utf-8")
    assert '. "$INSTALLER_DIR/lib/clock.sh"' in engine
    assert engine.index("write_host_configuration\n") < engine.index("check_clock") < engine.index("cleanup_mounts\n")
    answers = (LIB / "answers.sh").read_text(encoding="utf-8")
    assert "ntp_servers) ANSWER_NTP_SERVERS=$_as_value ;;" in answers and "validate_ntp_servers" in answers
    finish = (LIB / "finish.sh").read_text(encoding="utf-8")
    assert "\nntp_servers=$ANSWER_NTP_SERVERS\n" in finish and "    write_chrony_source\n" in finish


# --- the bridge -----------------------------------------------------------------

@BRIDGE_ONLY
def test_apply_writes_the_chosen_source_and_automatic_is_empty(tmp_path):
    m = _bridge_machine(tmp_path)
    rc, _, err = bridge(m, "apply", {**FORM, "ntp_servers": "10.44.0.250"})
    assert rc == 0, err
    assert "ntp_servers=10.44.0.250\n" in (m["run"] / "answers").read_text()
    (tmp_path / "auto").mkdir()
    m = _bridge_machine(tmp_path / "auto")
    rc, _, err = bridge(m, "apply", {**FORM})
    assert rc == 0, err
    assert "\nntp_servers=\n" in (m["run"] / "answers").read_text()


@BRIDGE_ONLY
def test_the_engines_refusal_lands_on_the_ntp_field(tmp_path):
    m = _bridge_machine(tmp_path)
    engine = tmp_path / "ntp-engine"
    engine.write_text("#!/bin/sh\n"
                      "grep -q '^ntp_servers=bad_host$' \"$1\" && "
                      "{ echo 'installer: [invalid-ntp-servers] ntp_servers must be up to four' >&2; exit 2; }\n"
                      "exit 0\n")
    engine.chmod(0o755)
    rc, out, err = bridge(m, "validate", {**FORM, "ntp_servers": "bad_host", "step": "6"}, ENGINE=str(engine))
    assert rc == 0, err
    assert out["errors.ntp_servers"] == ["כתובת שרת הזמן אינה תקינה — עד ארבע כתובות IPv4 או שמות, מופרדות ברווח"]
    assert "errors.disk" not in out and "errors.password" not in out, "step 6 is not the passwords' step"


@BRIDGE_ONLY
@pytest.mark.parametrize("record,expected", [
    ("clock=synced\nclock_server=10.44.0.250\nclock_offset_ms=3\n",
     {"clock": ["synced"], "clock_server": ["10.44.0.250"], "clock_offset_ms": ["3"]}),
    ("clock=unsynced\nclock_server=10.44.0.250\n", {"clock": ["unsynced"], "clock_server": ["10.44.0.250"]}),
    ("clock=maybe\n", {"clock": ["unchecked"]}),
    (None, {"clock": ["unchecked"]}),
])
def test_finish_reports_the_clock_and_no_record_is_unchecked(tmp_path, record, expected):
    m = _bridge_machine(tmp_path)
    if record is not None:
        (m["run"] / "clock-check").write_text(record)
    rc, out, err = bridge(m, "finish")
    assert rc == 0, err
    for key, value in expected.items():
        assert out[key] == value
    if expected["clock"] != ["synced"]:
        assert "clock_offset_ms" not in out


def test_the_bridge_hands_its_record_path_to_the_engine_and_forgets_the_last_one():
    text = (REPO / "installer" / "gui-bridge.sh").read_text(encoding="utf-8")
    assert 'CLOCK_FILE="${CLOCK_FILE:-$RUN_DIR/clock-check}"' in text
    assert 'rm -f "$CLOCK_FILE"' in text
    assert '( CLOCK_FILE="$CLOCK_FILE" "$ENGINE" "$ANSWERS" --state "$STATE_FILE"' in text
    assert 'emit ntp_servers "$(field ntp_servers)"' in text


# --- firstboot -> the console's setting -> chrony again --------------------------

def test_firstboot_hands_a_chosen_source_to_setup():
    firstboot = (REPO / "tools" / "iso" / "firstboot-answers.sh").read_text(encoding="utf-8")
    assert ('if [[ -n "$(answers_get ntp_servers)" ]]; then argv+=(--ntp-servers '
            '"$(answers_get ntp_servers)"); fi') in firstboot
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert '--ntp-servers) NTP_SERVERS="${2:?}"; shift 2 ;;' in setup


def _setup_snippet(tmp_path: Path) -> str:
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    block = re.search(r'NTP_SERVERS="\$NTP_SERVERS" python3 - <<PYEOF\n(.*?)\nPYEOF\n', setup, re.S)
    assert block, "the setting is written by the heredoc after --timezone's"
    return block.group(1).replace("$APP_DIR", REPO.as_posix()).replace("$DATA_DIR", tmp_path.as_posix())


def test_setup_writes_the_consoles_setting_and_the_servers_start_writes_chrony(tmp_path):
    connect(tmp_path / "imagesrv.db").close()
    got = subprocess.run([sys.executable, "-c", _setup_snippet(tmp_path)], capture_output=True, text=True,
                         env={**os.environ, "NTP_SERVERS": " 10.44.0.250   ntp.college "},
                         stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode == 0, got.stderr
    conn = connect(tmp_path / "imagesrv.db")
    assert get_setting(conn, "ntp_servers") == "10.44.0.250 ntp.college"
    written = []
    hooks = {"ensure_confdir": lambda: None, "write": written.append, "restart": lambda: None,
             "tracking": lambda: "Leap status : Normal\n", "sources": lambda: "^* 10.44.0.250 2 6 377\n",
             "now": lambda: ""}
    timesync.apply_on_start(conn, hooks)
    assert written == ["10.44.0.250 ntp.college"], "the server's start keeps the installer's choice"


def test_setup_refuses_what_the_console_would_refuse(tmp_path):
    connect(tmp_path / "imagesrv.db").close()
    got = subprocess.run([sys.executable, "-c", _setup_snippet(tmp_path)], capture_output=True, text=True,
                         env={**os.environ, "NTP_SERVERS": "bad_host!", "PYTHONIOENCODING": "utf-8"},
                         encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode != 0 and "כתובת שרת זמן לא תקינה" in got.stderr
    assert get_setting(connect(tmp_path / "imagesrv.db"), "ntp_servers") is None
