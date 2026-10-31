"""Dead-man tests use a fake monotonic clock, fake reboot and regular pet file.

No watchdog device, server, or reboot is ever invoked.
"""
import subprocess

import pytest

from test_agent import AGENT, BASH
from native import requires_native

pytestmark = requires_native(("bash", BASH))


def run(tmp_path, script):
    prefix = f'''
        RUN_DIR={tmp_path.as_posix()!r}
        WATCHDOG_UPTIME="$RUN_DIR/uptime"
        . {AGENT.as_posix()!r}/lib/core/watchdog.sh
        reboot() {{ echo "REBOOT $*"; exit 0; }}
    '''
    return subprocess.run([BASH, "--posix", "-c", prefix + script],
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=10)


def test_heartbeat_expires_at_full_ten_minutes(tmp_path):
    out = run(tmp_path, '''
        echo '100.25 0' > "$WATCHDOG_UPTIME"
        WATCHDOG_ACTIVE=1
        watchdog_beat || exit 8
        _wd_last=100
        watchdog_expired 699 && exit 9
        watchdog_expired 700 || exit 10
        echo expired
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "expired"
    assert (tmp_path / "watchdog.beat").read_text().strip() == "100"


@pytest.mark.parametrize("contents", ["", "garbage", "999999"])
def test_bad_or_future_heartbeat_cannot_renew_lease(tmp_path, contents):
    (tmp_path / "watchdog.beat").write_text(contents + "\n")
    out = run(tmp_path, '_wd_last=100; watchdog_expired 700; echo "rc=$?"')
    assert out.stdout.strip() == "rc=0", out.stderr


def test_missing_heartbeat_keeps_last_observation(tmp_path):
    out = run(tmp_path, '_wd_last=100; watchdog_expired 700; echo "rc=$?"')
    assert out.stdout.strip() == "rc=0"
    assert "cannot read heartbeat" in out.stderr


def test_hardware_pets_only_until_expiry(tmp_path):
    out = run(tmp_path, '''
        echo '599.0 0' > "$WATCHDOG_UPTIME"
        echo 0 > "$RUN_DIR/watchdog.beat"
        _wd_last=0
        sleep() { echo '600.0 0' > "$WATCHDOG_UPTIME"; }
        watchdog_loop hardware 3> "$RUN_DIR/pets"
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "REBOOT -f"
    assert (tmp_path / "pets").read_text() == "."
    assert "no agent progress for 600s" in out.stderr


def test_no_hardware_uses_software_deadman(tmp_path):
    out = run(tmp_path, '''
        echo '0.0 0' > "$WATCHDOG_UPTIME"
        echo 0 > "$RUN_DIR/watchdog.beat"
        WATCHDOG_DEVICE="$RUN_DIR/no-device"
        sleep() { echo '600.0 0' > "$WATCHDOG_UPTIME"; }
        watchdog_supervise
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "REBOOT -f"
    assert "software recovery armed" in out.stderr


def test_healthy_idle_can_wait_for_hours(tmp_path):
    out = run(tmp_path, '''
        WATCHDOG_ACTIVE=1
        _wd_last=0
        for tick in 0 500 1000 1500 2000 2500 3000 3500 4000; do
            echo "$tick.0 0" > "$WATCHDOG_UPTIME"
            watchdog_beat || exit 8
            watchdog_expired "$tick" && exit 9
        done
        echo healthy
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "healthy"


@pytest.mark.parametrize("moving", [True, False])
def test_transfer_wait_renews_lease_but_stalled_wait_finishes(tmp_path, moving):
    out = run(tmp_path, f'''
        . {AGENT.as_posix()!r}/lib/core/waits.sh
        WATCHDOG_ACTIVE=1; WAIT_POLL_S=100; t=0; _wd_last=0
        echo '0.0 0' > "$WATCHDOG_UPTIME"
        : > "$RUN_DIR/counter"
        log() {{ echo "$*" >&2; }}
        kill() {{ [ "$1" != -0 ] || [ "$t" -lt 1200 ]; }}
        wait() {{ return 0; }}
        sleep() {{
            t=$((t+100))
            echo "$t.0 0" > "$WATCHDOG_UPTIME"
            if {str(moving).lower()}; then echo "$t" >> "$RUN_DIR/counter"; fi
            watchdog_expired "$t" && exit 9
            return 0
        }}
        poll_tick() {{ sleep; _tk=$((WAIT_POLL_S * 10)); }}   # #1705: one fake tick = one poll
        wait_progress 999 "$RUN_DIR/counter" 600 120 stream
        echo "result=$? t=$t"
        # Once the controlling wait stops, a surviving reporter must not
        # renew the lease; the last real heartbeat eventually expires.
        watchdog_expired "$((t+600))" || exit 10
    ''')
    assert out.returncode == 0, out.stderr
    expected = "result=0 t=1200" if moving else "result=1 t=600"
    assert out.stdout.strip() == expected


def test_attended_hello_loop_renews_the_watchdog_lease(tmp_path):
    """#1130 negative control: attended used to send hello without a watchdog beat."""
    out = run(tmp_path, f'''
        . {AGENT.as_posix()!r}/lib/core/attended.sh
        attended_hello() {{ echo hello >> "$RUN_DIR/hellos"; }}
        watchdog_beat() {{ echo beat >> "$RUN_DIR/beats"; }}
        sleep() {{
            n=$(wc -l < "$RUN_DIR/beats")
            [ "$n" -lt 3 ] || exit 0
        }}
        attended_start "choose"
        wait "$ATTENDED_PID"
        echo "beats=$(wc -l < "$RUN_DIR/beats") hellos=$(wc -l < "$RUN_DIR/hellos")"
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "beats=3 hellos=3"


def test_hardware_pet_error_does_not_kill_supervisor(tmp_path):
    out = run(tmp_path, '''
        echo '599.0 0' > "$WATCHDOG_UPTIME"
        echo 0 > "$RUN_DIR/watchdog.beat"
        _wd_last=0
        sleep() { echo '600.0 0' > "$WATCHDOG_UPTIME"; }
        watchdog_loop hardware 3>&-
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "REBOOT -f"
    assert "hardware keepalive failed" in out.stderr


def test_transient_clock_error_recovers_without_reboot(tmp_path):
    out = run(tmp_path, '''
        echo broken > "$WATCHDOG_UPTIME"
        echo 100 > "$RUN_DIR/watchdog.beat"
        _wd_last=100; n=0
        sleep() {
            n=$((n+1))
            if [ "$n" = 1 ]; then
                echo '101.0 0' > "$WATCHDOG_UPTIME"
            else
                echo recovered
                exit 0
            fi
        }
        watchdog_loop hardware 3> "$RUN_DIR/pets"
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "recovered"
    assert (tmp_path / "pets").read_text() == "."
    assert "monotonic clock unreadable" in out.stderr


@pytest.mark.parametrize("role,mode", [("classroom", "normal"),
                                       ("build", "normal"),
                                       ("cloner", "recovery")])
def test_interactive_and_classroom_modes_do_not_arm(tmp_path, role, mode):
    out = run(tmp_path, f'''
        D_ROLE={role}; D_MODE={mode}; D_SCHEMA=1; D_KNOWN=true
        setsid() {{ echo unexpected; }}
        watchdog_start
        echo "active=${{WATCHDOG_ACTIVE:-0}}"
    ''')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "active=0"


