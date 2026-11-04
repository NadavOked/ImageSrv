"""#2181: the power trap on the harness itself.

A test that reaches `reboot`/`poweroff`/`halt`/`shutdown` used to reach the real
binary on the testrunner (`/usr/sbin/reboot`, #2077), and only not being root
kept the machine up. These tests run the real shells against the trap the
conftest installs, and pin the three ways a test's own stub must keep winning.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

import powertrap
from native import requires_native

BASH = shutil.which("bash")
BUSYBOX = os.environ.get("IMAGESRV_BUSYBOX") or shutil.which("busybox")

pytestmark = requires_native(("bash", BASH), why="the trap is reached from a shell")
pytest_plugins = ("pytester",)


def sh(script: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=60, **kwargs)


@pytest.fixture()
def expected_calls():
    """The calls this test makes on purpose -- acknowledged, so `power_guard`
    (the guard under test) does not fail the test that measures it."""
    yield
    powertrap.acknowledge()


#: The four, as a shell inside a test resolves them: the file it would run, and
#: whether that file is the trap (it carries the marker) -- or MISSING.
RESOLVE = ("for c in reboot poweroff halt shutdown; do "
           "p=$(type -P $c) || { echo $c=MISSING; continue; }; "
           "if grep -q '#2181: power trap' \"$p\"; then echo $c=trap; else echo $c=REAL:$p; fi; done")


def test_no_test_can_reach_a_real_power_command():
    """**The Issue itself, without the module's internals.** On the testrunner
    without the trap this prints `reboot=REAL:/usr/sbin/reboot` (#2077); on
    Windows, `MISSING`. With it, every one of the four is the trap."""
    done = sh(RESOLVE)
    assert done.stdout.split() == [f"{c}=trap" for c in powertrap.POWER_COMMANDS], done
    if os.name != "nt":
        # A PATH built from nothing, the shape test_iso_lab_gate_1412 uses.
        done = sh(RESOLVE, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        assert done.stdout.split() == [f"{c}=trap" for c in powertrap.POWER_COMMANDS], done


def test_the_conftest_installed_the_trap_first_on_path():
    assert os.environ["PATH"].split(os.pathsep)[0] == str(powertrap.trap_dir())


def test_power_guard_fails_a_test_that_reached_the_trap(pytester, expected_calls):
    """The autouse fixture end to end: an inner test that runs `reboot -f`
    and asserts nothing is an ERROR in its teardown, naming the command."""
    pytester.makepyfile(f"""
        import subprocess
        from powertrap import power_guard  # noqa: F401
        def test_reaches_reboot():
            subprocess.run([{BASH!r}, "-c", "reboot -f"], stdin=subprocess.DEVNULL,
                           capture_output=True)
        def test_reaches_nothing():
            pass
    """)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.re_match_lines([r".*#2181.*reboot -f.*"])


@pytest.mark.parametrize("command", ["reboot -f", "poweroff -f", "halt", "shutdown -h now"])
def test_every_power_command_lands_in_the_trap_and_fails(command, expected_calls):
    before = len(powertrap.calls())
    done = sh(f"{command}; echo rc=$?")
    assert "rc=1" in done.stdout, done
    assert powertrap.calls()[before:] == [command], powertrap.calls()
    assert "#2181" in done.stderr


def test_a_power_command_is_what_the_guard_fails_the_test_on():
    """The guard's verdict, without pytester: a call nobody acknowledged is
    what `power_guard` reports at teardown; an acknowledged one is not."""
    start = len(powertrap.calls())
    sh("poweroff -f")
    reported = powertrap.escaped(start)
    powertrap.acknowledge()
    assert reported == ["poweroff -f"]
    assert powertrap.escaped(start) == []


def test_a_test_stub_before_the_trap_still_wins(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "reboot").write_text("#!/bin/sh\necho STUB \"$@\"\n", encoding="utf-8", newline="\n")
    (stubs / "reboot").chmod(0o755)
    stub_dir = stubs.as_posix()
    if os.name == "nt":
        stub_dir = "/" + stub_dir[0].lower() + stub_dir[2:]
    before = len(powertrap.calls())
    done = sh(f'PATH="{stub_dir}:$PATH"; reboot -f')
    assert done.stdout.strip() == "STUB -f", done
    done = sh('reboot() { echo FUNC "$@"; }; reboot -f')
    assert done.stdout.strip() == "FUNC -f", done
    assert powertrap.calls()[before:] == []


def test_an_explicit_env_path_gets_the_trap_before_the_system_dirs(tmp_path):
    """`env={"PATH": f"{stubs}:/usr/sbin:/usr/bin"}` built from nothing: on
    POSIX the guard puts the trap right before `/usr/sbin`, after the stubs."""
    trap, stubs = "/run/trap", "/tmp/pytest-of-x/test0/stubs"
    path = f"{stubs}:/usr/sbin:/usr/bin:/sbin:/bin"
    assert powertrap.with_trap(path, trap) == f"{stubs}:{trap}:/usr/sbin:/usr/bin:/sbin:/bin"
    assert powertrap.with_trap(powertrap.with_trap(path, trap), trap) == powertrap.with_trap(path, trap)
    assert powertrap.with_trap(stubs, trap) == f"{stubs}:{trap}"
    assert powertrap.with_trap(f"{stubs}:/bin/", trap) == f"{stubs}:{trap}:/bin/"
    if os.name != "nt":
        done = sh("command -v reboot", env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        assert done.stdout.strip() == f"{powertrap.trap_dir()}/reboot", done


def test_busybox_c_scripts_get_trap_functions_that_a_test_function_overrides():
    script = "reboot -f"
    args = powertrap._busybox_trapped(["/usr/bin/busybox", "ash", "-c", script, "x"])
    assert args[3] == powertrap.trap_functions() + script
    assert "\n" not in powertrap.trap_functions()
    assert powertrap._busybox_trapped(["/usr/bin/busybox", "ash", "file.sh"])[2] == "file.sh"
    assert powertrap._busybox_trapped([BASH, "-c", script])[2] == script


@requires_native(("busybox", BUSYBOX), why="busybox runs its own reboot applet before PATH")
def test_busybox_reboot_lands_in_the_trap_not_the_applet(expected_calls):
    before = len(powertrap.calls())
    done = subprocess.run([BUSYBOX, "ash", "-c", "reboot -f; echo rc=$?"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert "rc=1" in done.stdout, done
    assert powertrap.calls()[before:] == ["reboot -f"]
    done = subprocess.run([BUSYBOX, "ash", "-c", 'reboot() { echo FUNC; }; reboot -f'],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "FUNC", done
