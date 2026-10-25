"""#2067 -- the five tunes of the cloner and the build machine, behaviourally.

Every check decodes the bytes written to a fake "PC Speaker" evdev node
(struct input_event, 24 bytes: EV_SND 0x12 / SND_TONE 0x02 / Hz, then 0) and
runs under **busybox ash** -- the shell the agent runs under -- not bash.
Never a real device or disk.

Written by Codex (gpt-6-astra high); verified and corrected by Claude 24/10:
the lock expires (an owner killed with -9 left it forever), the stream tick
forks nothing per second, no "network lost" before the first byte or outside
the stream phase, and the build machine beeps too (Nadav 24/10).
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

import pytest

import hygiene
from native import requires_native
from test_agent import AGENT, REPO, posix

BUSYBOX = os.environ.get("IMAGECTL_BUSYBOX") or shutil.which("busybox")
pytestmark = requires_native(
    ("busybox", BUSYBOX), posix=True,
    why="the agent runs under busybox ash; bash would hide a bashism",
)

# Independent expectations (mockup 08-beep-2067, BEEPS): Hz, on, silence after.
TUNES = {
    "success": [(523, ".1", ".02"), (659, ".1", ".02"),
                (784, ".1", ".02"), (1047, ".28", "0")],
    "failure": [(880, ".3", "0"), (587, ".3", "0")] * 3,
    "netlost": [(880, ".22", ".04"), (660, ".22", ".04"), (494, ".45", "0")],
    "netback": [(900, ".15", ".06"), (1400, ".15", "0")],
    "pulled": [(440, ".08", ".12")] * 3 + [(440, ".08", "0")],
}


def run_ash(script: str, timeout: int = 45) -> str:
    """test_timeouts.run_sh, under `busybox ash`: output to a file, the group killed."""
    out_file = Path(tempfile.mkdtemp(prefix="imagectl-beep-")) / "out"
    script_file = out_file.with_name("script.sh")
    script_file.write_text(f"{{\n{script}\n}} > {posix(out_file)!r} 2>&1\n",
                           encoding="utf-8", newline="\n")
    try:
        hygiene.run_group([BUSYBOX, "ash", posix(script_file)], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                          cwd=str(REPO), timeout=timeout)
    except subprocess.TimeoutExpired:
        shutil.rmtree(out_file.parent, ignore_errors=True)
        pytest.fail(f"stuck for more than {timeout} s")
    try:
        return out_file.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(out_file.parent, ignore_errors=True)


def prelude(tmp_path: Path, *, speaker=True, enabled=True, role="cloner") -> str:
    device = tmp_path / "sys/event5/device"
    device.mkdir(parents=True)
    (device / "name").write_text("PC Speaker\n" if speaker else "Power Button\n",
                                 encoding="utf-8", newline="\n")
    (tmp_path / "dev/input").mkdir(parents=True)
    (tmp_path / "response.json").write_text(
        json.dumps({"role": role, "ui": {"cloner_beep": enabled},
                    "session": {"image_id": "img", "id": "ses"}}), encoding="utf-8")
    return f"""
        export RUN_DIR={posix(tmp_path)!r} SYSFS_INPUT={posix(tmp_path / 'sys')!r}
        export DEVROOT={posix(tmp_path / 'dev')!r} D_ROLE={role!r}
        RESP="$RUN_DIR/response.json"; LOG_FILE="$RUN_DIR/agent.log"
        LIB_DIR={posix(AGENT / 'lib')!r}; GUI_DIR="$RUN_DIR/gui"
        . "$LIB_DIR/jsonq.sh"; . "$LIB_DIR/roundbeep.sh"
        log() {{ printf '%s\\n' "$*" >> "$LOG_FILE"; }}
        sleep() {{ printf '%s\\n' "$1" >> "$RUN_DIR/sleeps"; }}
    """


def frequencies(tmp_path: Path) -> list[int]:
    node = tmp_path / "dev/input/event5"
    data = node.read_bytes() if node.is_file() else b""
    assert len(data) % 24 == 0
    events = list(struct.iter_unpack("<qqHHi", data))
    assert all((sec, usec, kind, code) == (0, 0, 0x12, 0x02)
               for sec, usec, kind, code, value in events)
    return [value for sec, usec, kind, code, value in events]


def expected(kind):
    return [value for hz, on, off in TUNES[kind] for value in (hz, 0)]


def log_lines(tmp_path: Path) -> list[str]:
    log = tmp_path / "agent.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _function(path: Path, name: str, nxt: str) -> str:
    """The real text of one shell function, up to the line that opens the next one."""
    text = path.read_text(encoding="utf-8")
    return f"{name}() {{" + text.split(f"\n{name}() {{", 1)[1].split(f"\n{nxt}", 1)[0] + "\n"


@pytest.mark.parametrize("kind", TUNES)
def test_exact_tones_and_on_and_off_durations(tmp_path, kind):
    run_ash(prelude(tmp_path) + f"event_beep {kind}; wait")
    assert frequencies(tmp_path) == expected(kind)
    assert (tmp_path / "sleeps").read_text().splitlines() == [
        duration for hz, on, off in TUNES[kind] for duration in (on, off) if duration != "0"]
    assert not (tmp_path / "beep.lock").exists()


def test_the_build_machine_beeps_too(tmp_path):
    run_ash(prelude(tmp_path, role="build") + "event_beep pulled; wait")
    assert frequencies(tmp_path) == expected("pulled")


def test_the_kiosk_takes_the_role_from_the_hello(tmp_path):
    # The kiosk is its own process: no D_ROLE there; response.json says build.
    run_ash(prelude(tmp_path, role="build") + "unset D_ROLE; event_beep netback; wait")
    assert frequencies(tmp_path) == expected("netback")


@pytest.mark.parametrize("options,reason", [
    ({"speaker": False}, "no PC speaker"),
    ({"enabled": False}, "off in the console settings"),
    ({"role": "classroom"}, "not a cloner or a build machine"),
])
def test_unheard_is_one_line_and_never_a_failure(tmp_path, options, reason):
    output = run_ash(prelude(tmp_path, **options) + "event_beep failure; echo rc=$?; wait")
    assert "rc=0" in output
    assert frequencies(tmp_path) == []
    lines = log_lines(tmp_path)
    assert len(lines) == 1 and reason in lines[0] and lines[0].endswith(" -- not heard")


@pytest.mark.parametrize("fail_on", ["on", "off"])
def test_failed_device_write_is_one_log_line(tmp_path, fail_on):
    script = prelude(tmp_path)
    # A directory cannot accept an evdev write, even when tests run as root.
    script += 'mkdir "$DEVROOT/input/event5"\n' if fail_on == "on" else """
        sleep() { rm -f "$DEVROOT/input/event5"; mkdir "$DEVROOT/input/event5"; }
    """
    output = run_ash(script + "event_beep failure; echo rc=$?; wait")
    assert "rc=0" in output
    lines = log_lines(tmp_path)
    assert len(lines) == 1 and "writing to" in lines[0] and " -- not heard" in lines[0]


def test_stream_outage_then_recovery_once(tmp_path):
    run_ash(prelude(tmp_path) + """
        t=1000; date() { echo "$t"; }   # minutes apart: the 5 s repeat rule is not what is tested
        for age in 29 30 45 0 0 31 31; do
            t=$((t + 60)); stream_beep_tick "$age"; wait
            if [ -f "$DEVROOT/input/event5" ]; then wc -c < "$DEVROOT/input/event5"
            else echo 0; fi >> "$RUN_DIR/sizes"
        done
    """)
    # 29: nothing; 30: netlost (6 events = 144 B); 45: nothing more; 0: netback
    # (+4 = 240 B); 0: nothing; 31: a new outage = netlost again; 31: nothing.
    assert (tmp_path / "sizes").read_text().split() == ["0", "144", "144", "240", "240", "384", "384"]
    assert frequencies(tmp_path) == expected("netlost") + expected("netback") + expected("netlost")


def test_stream_threshold_override_and_unheard_is_logged(tmp_path):
    run_ash(prelude(tmp_path, speaker=False) + """
        NETLOST_AFTER_S=7
        stream_beep_tick 6; wait
        stream_beep_tick 7; wait
        stream_beep_tick 9; wait
        stream_beep_tick 0; wait
    """)
    assert frequencies(tmp_path) == []
    lines = log_lines(tmp_path)
    assert [line.split(":")[0] for line in lines] == ["beep netlost", "beep netback"]
    assert all(line.endswith("no PC speaker (pcspkr) -- not heard") for line in lines)


@pytest.mark.parametrize("machine", ["waiting", "writing"])
def test_the_stream_tick_forks_nothing_while_no_drawer_streams(tmp_path, machine):
    # The kiosk calls it once a second (clonergui.sh). Every command the tick
    # could fork is a function here that leaves a mark, and `( ... )` would
    # leave a background job behind for `jobs` to list.
    run_ash(prelude(tmp_path) + f"""
        command mkdir -p "$RUN_DIR/targets/sda" "$GUI_DIR"
        echo writing > "$RUN_DIR/targets/sda/state"
        echo {machine} > "$RUN_DIR/state"
        for c in date cat rm json_get mkdir sed awk; do eval "$c() {{ echo $c >> \\"\\$RUN_DIR/forks\\"; }}"; done
        stream_beep_tick; stream_beep_tick; stream_beep_tick
        jobs > "$RUN_DIR/jobs"
    """)
    assert not (tmp_path / "forks").exists()
    assert (tmp_path / "jobs").read_text() == ""


def test_returns_before_playback_finishes(tmp_path):
    # Handshake, not a wall-clock race: playback cannot finish until the caller
    # returns and releases it. A synchronous implementation times out.
    run_ash(prelude(tmp_path) + """
        sleep() {
            : > "$RUN_DIR/playing"
            while [ ! -f "$RUN_DIR/release" ]; do command sleep .01; done
        }
        event_beep failure
        while [ ! -f "$RUN_DIR/playing" ]; do command sleep .01; done
        wc -c < "$DEVROOT/input/event5" > "$RUN_DIR/before-release"
        : > "$RUN_DIR/release"
        wait
    """, timeout=10)
    assert (tmp_path / "before-release").read_text().strip() == "24"
    assert frequencies(tmp_path) == expected("failure")


def test_serialization_never_skips_a_failure(tmp_path):
    run_ash(prelude(tmp_path) + """
        sleep() {
            if [ "$1" = .05 ]; then command sleep .01; return; fi
            : > "$RUN_DIR/playing"
            while [ ! -f "$RUN_DIR/release" ]; do command sleep .01; done
        }
        event_beep success
        while [ ! -f "$RUN_DIR/playing" ]; do command sleep .01; done
        event_beep failure
        event_beep failure
        : > "$RUN_DIR/release"
        wait
    """, timeout=10)
    assert frequencies(tmp_path) == expected("success") + expected("failure") * 2
    assert not (tmp_path / "beep.lock").exists()


HOLD = """
        sleep() {
            [ "$1" != .05 ] || return 0
            : > "$RUN_DIR/playing"; while :; do command sleep .01; done
        }
"""
OWNER = """
        while [ ! -f "$RUN_DIR/beep.lock/pid" ] || [ ! -f "$RUN_DIR/playing" ]; do command sleep .01; done
        read -r owner < "$RUN_DIR/beep.lock/pid"
"""


def test_a_lock_whose_owner_was_killed_is_broken_at_once(tmp_path):
    # kill -9 runs no trap: the directory stays. The next tune must not wait on it.
    run_ash(prelude(tmp_path) + HOLD + "event_beep success\n" + OWNER + """
        kill -9 "$owner"; wait "$owner"
        sleep() { :; }
        event_beep failure; wait
    """, timeout=15)
    # The killed tune's first tone (still on: -9 runs nothing), then the failure in full.
    assert frequencies(tmp_path) == [523] + expected("failure")
    assert any("outlived its owner" in line for line in log_lines(tmp_path))
    assert not (tmp_path / "beep.lock").exists()


def test_a_waiter_expires_on_a_lock_it_cannot_judge(tmp_path):
    # No owner pid to check (no /proc): the ceiling breaks it -- never a waiter forever.
    run_ash(prelude(tmp_path) + """
        mkdir "$RUN_DIR/beep.lock"
        BEEP_LOCK_WAIT_S=1; sleep() { command sleep .001; }
        event_beep failure; wait
    """, timeout=15)
    assert frequencies(tmp_path) == expected("failure")
    assert any("stayed locked 1 s -- lock broken" in line for line in log_lines(tmp_path))


def test_a_tune_stopped_by_term_turns_the_tone_off(tmp_path):
    run_ash(prelude(tmp_path) + HOLD + "event_beep failure\n" + OWNER + """
        kill -TERM "$owner"; wait
    """, timeout=15)
    assert frequencies(tmp_path) == [880, 0]
    assert not (tmp_path / "beep.lock").exists()


@pytest.mark.parametrize("kind", ["netlost", "netback", "pulled"])
def test_repeat_suppressed_for_five_seconds_only(tmp_path, kind):
    run_ash(prelude(tmp_path) + f"""
        date() {{ cat "$RUN_DIR/clock"; }}
        echo 100 > "$RUN_DIR/clock"; event_beep {kind}; wait
        echo 104 > "$RUN_DIR/clock"; event_beep {kind}; wait
        echo 105 > "$RUN_DIR/clock"; event_beep {kind}; wait
    """)
    assert frequencies(tmp_path) == expected(kind) * 2
    assert "\n".join(log_lines(tmp_path)).count("repeat within 5 s -- not heard") == 1


@pytest.mark.parametrize("state,kind", [("done", "success"), ("failed", "failure"),
                                        ("skipped", "failure")])
def test_real_round_end_uses_all_drawers(tmp_path, state, kind):
    script = prelude(tmp_path) + _function(AGENT / "imagectl-agent", "do_restore_drawers", "do_restore() {")
    script += f"""
        . "$LIB_DIR/idle.sh"
        for fn in identify_watch_stop smart_selftest_cancel session_stream job_reset crc_delta_measure smart_note_apply stage_drawer_drivers writemark_release_done report_final agent_log_send; do eval "$fn() {{ :; }}"; done
        resolve_target_drawers() {{ _disks='sda sdb'; }}
        target_init() {{ mkdir -p "$RUN_DIR/targets/$1"; echo writing > "$RUN_DIR/targets/$1/state"; }}
        http_get() {{ echo '{{}}'; }}
        progress_loop() {{ :; }}
        smart_gate() {{ echo 'sda sdb'; }}
        drawer_gates() {{ echo 'sda sdb'; }}
        run_restore_drawers() {{
            echo done > "$RUN_DIR/targets/sda/state"
            echo {state} > "$RUN_DIR/targets/sdb/state"
        }}
        _machine_state() {{ echo done; }}
        end_countdown() {{ :; }}
        SERVER=unused; MAC=unused; IMAGECTL_TEST=0
        do_restore_drawers image session
        wait
    """
    run_ash(script)
    assert frequencies(tmp_path) == expected(kind)


def test_a_round_whose_drawers_were_all_skipped_ends_in_failure(tmp_path):
    script = prelude(tmp_path) + _function(AGENT / "imagectl-agent", "do_restore_drawers", "do_restore() {")
    script += """
        for fn in identify_watch_stop smart_selftest_cancel session_stream job_reset target_init progress_loop end_failed_round; do eval "$fn() { :; }"; done
        resolve_target_drawers() { _disks='sda sdb'; }
        http_get() { echo '{}'; }
        smart_gate() { return 1; }
        SERVER=unused; MAC=unused
        do_restore_drawers image session
        wait
    """
    run_ash(script)
    assert frequencies(tmp_path) == expected("failure")


@pytest.mark.parametrize("restored,kind", [(True, "success"), (False, "failure")])
def test_build_machine_restore_end(tmp_path, restored, kind):
    # Nadav 24/10: the build machine beeps at the end of a restore too. On
    # success the tune is over before the reboot that would cut it.
    script = prelude(tmp_path, role="build") + _function(AGENT / "imagectl-agent", "do_restore", "# --- main loop")
    script += f"""
        . "$LIB_DIR/waits.sh"   # wait_pid: the bounded wait before the reboot
        for fn in job_reset target_init smart_note_apply name_this_machine stage_drivers ui_clear ui_header session_stream report_final writemark_release_done sync; do eval "$fn() {{ :; }}"; done
        crc_delta_after() {{ _r=$?; return "$_r"; }}
        pick_internal_disk() {{ echo sda; }}
        http_get() {{ echo '{{}}'; }}
        progress_loop() {{ :; }}
        smart_gate() {{ echo sda; }}
        run_restore() {{ {'return 0' if restored else 'return 1'}; }}
        ui_error_hold() {{ echo "held: $1" >> "$RUN_DIR/held"; }}
        reboot() {{ wc -c < "$DEVROOT/input/event5" > "$RUN_DIR/at-reboot"; }}
        sleep() {{ command sleep .01; }}
        SERVER=unused; MAC=unused; IMAGECTL_TEST=0
        do_restore
        wait
    """
    run_ash(script, timeout=20)
    assert frequencies(tmp_path) == expected(kind)
    if restored:
        assert (tmp_path / "at-reboot").read_text().strip() == str(24 * len(expected(kind)))
    else:
        assert "restore did not complete" in (tmp_path / "held").read_text()


@pytest.mark.parametrize("states,stopped,kinds", [
    (("done", "done"), False, ["success"]),
    (("done", "failed"), False, ["failure"]),    # partial = failure
    (("failed", "failed"), False, ["failure"]),  # the source failed: all of them
    (("done", "failed"), True, []),              # the operator stopped it: no tune
])
def test_local_clone_end(tmp_path, states, stopped, kinds):
    script = prelude(tmp_path) + '. "$LIB_DIR/idle.sh"\n' + _function(
        AGENT / "lib/localclone.sh", "lc_end", "lc_gb_short() {")
    script += f"""
        _lr_r="$RUN_DIR/lcrun"; LC="$RUN_DIR/lc"; mkdir -p "$_lr_r" "$LC"
        _lr_all="sda sdb "; _lr_beat=999999
        mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb"
        echo {states[0]} > "$RUN_DIR/targets/sda/state"; echo {states[1]} > "$RUN_DIR/targets/sdb/state"
        {'echo 1 > "$_lr_r/stopped"' if stopped else ':'}
        lc_report() {{ :; }}; wait_pid() {{ :; }}
        lc_end; wait
    """
    run_ash(script)
    assert frequencies(tmp_path) == [v for k in kinds for v in expected(k)]


@pytest.mark.parametrize("role", ["cloner", "build"])
@pytest.mark.parametrize("pulled,kind", [(True, "pulled"), (False, "failure")])
def test_drawer_watch_real_dead_paths(tmp_path, role, pulled, kind):
    # drawer_watch serves the round (drawers.sh) and the local clone (lccopy.sh)
    # alike; the real event_beep plays, in both roles.
    script = prelude(tmp_path, role=role)
    script += f"""
        . "$LIB_DIR/verdict.sh"
        mkdir -p "$RUN_DIR/targets/sda"
        echo writing > "$RUN_DIR/targets/sda/state"
        SYSROOT="$RUN_DIR/sysroot"; turns=0
        kill() {{
            [ "$1" = -0 ] || return 0
            turns=$((turns + 1)); [ "$turns" = 1 ]
        }}
        _nap() {{ :; }}; dmesg() {{ :; }}
        drawer_dead_reason() {{ {'echo "$DRAWER_GONE_WHY"' if pulled else 'echo media-error'}; }}
        fail_written_target() {{ echo failed > "$RUN_DIR/targets/$1/state"; }}
        drawer_watch 123 1 sda
        wait
    """
    run_ash(script)
    assert frequencies(tmp_path) == expected(kind)
    assert (tmp_path / "targets/sda/dead").is_file()
    assert (tmp_path / "targets/sda/pulled").exists() == pulled


KIOSK = """
        date() { cat "$RUN_DIR/clock"; }
        mkdir -p "$GUI_DIR" "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb"
        echo writing > "$RUN_DIR/state"
        echo writing > "$RUN_DIR/targets/sda/state"
        echo writing > "$RUN_DIR/targets/sdb/state"
        echo 4096 > "$RUN_DIR/bytes.raw"   # fanout's -P (drawers.sh): a partition mid-receive
"""


def test_kiosk_uses_latest_moving_drawer_and_resets_when_idle(tmp_path):
    run_ash(prelude(tmp_path) + """
        unset D_ROLE RESP
        . "$LIB_DIR/guibridge.sh"; gui_libs
        log() { printf '%s\\n' "$*" >> "$LOG_FILE"; }
    """ + KIOSK + """
        echo '60 100' > "$GUI_DIR/moved.sda"
        echo '100 100' > "$GUI_DIR/moved.sdb"
        echo 100 > "$RUN_DIR/clock"; stream_beep_tick; wait
        echo 130 > "$RUN_DIR/clock"; stream_beep_tick; wait
        echo '131 200' > "$GUI_DIR/moved.sdb"
        echo 131 > "$RUN_DIR/clock"; stream_beep_tick; wait
        echo done > "$RUN_DIR/targets/sda/state"
        echo done > "$RUN_DIR/targets/sdb/state"
        stream_beep_tick; wait
    """)
    assert frequencies(tmp_path) == expected("netlost") + expected("netback")
    assert not (tmp_path / "beep.net").exists()


def test_no_outage_before_the_first_byte(tmp_path):
    # The write check, TRIM, the tables and the server's start wait all run
    # with the drawer "writing" and no byte yet: not a network outage.
    run_ash(prelude(tmp_path) + KIOSK + """
        echo 100 > "$RUN_DIR/clock"; stream_beep_tick; wait
        echo 200 > "$RUN_DIR/clock"; stream_beep_tick; wait
    """)
    assert frequencies(tmp_path) == []


@pytest.mark.parametrize("machine", ["verifying", "done", "failed"])
def test_no_outage_outside_the_stream_phase(tmp_path, machine):
    # After the last partition (readback, grow, drivers) the bytes stand still by design.
    run_ash(prelude(tmp_path) + KIOSK + f"""
        echo {machine} > "$RUN_DIR/state"
        echo '10 100' > "$GUI_DIR/moved.sda"
        echo 100 > "$RUN_DIR/clock"; stream_beep_tick; wait
    """)
    assert frequencies(tmp_path) == []


def test_real_gui_state_feeds_the_stream_beeper(tmp_path):
    run_ash(prelude(tmp_path) + """
        . "$LIB_DIR/clonergui.sh"
        for fn in gui_header_state gui_hello_state slot_names_refresh drawer_slots_refresh slot_names_records drawer_slots_records gui_ask_state; do eval "$fn() { :; }"; done
        disk_port() { echo 1; }; disk_drawer() { echo 1; }
        target_bytes() { cat "$RUN_DIR/targets/$1/bytes"; }
        date() { cat "$RUN_DIR/clock"; }
        mkdir -p "$GUI_DIR" "$RUN_DIR/targets/sda"
        echo writing > "$RUN_DIR/state"
        echo writing > "$RUN_DIR/targets/sda/state"
        echo 1000 > "$RUN_DIR/targets/sda/total"
        echo 100 > "$RUN_DIR/targets/sda/bytes"; echo 100 > "$RUN_DIR/bytes.raw"
        echo '70 100' > "$GUI_DIR/moved.sda"
        echo 100 > "$RUN_DIR/clock"; cloner_gui_state; wait
        echo 145 > "$RUN_DIR/clock"; cloner_gui_state; wait
        echo 200 > "$RUN_DIR/targets/sda/bytes"
        echo 146 > "$RUN_DIR/clock"; cloner_gui_state; wait
    """)
    assert frequencies(tmp_path) == expected("netlost") + expected("netback")


def test_local_copy_is_not_reported_as_a_server_outage(tmp_path):
    run_ash(prelude(tmp_path) + KIOSK + """
        mkdir -p "$RUN_DIR/lc"
        echo '1 100' > "$GUI_DIR/moved.sda"
        echo copying > "$RUN_DIR/lc/phase"
        echo 100 > "$RUN_DIR/clock"; stream_beep_tick; wait
    """)
    assert frequencies(tmp_path) == []


ROOM = """
        . "$LIB_DIR/clonergui.sh"; . "$LIB_DIR/progress.sh"   # the real target_bytes and cloner_gui_pace
        for fn in gui_header_state gui_hello_state slot_names_refresh drawer_slots_refresh slot_names_records drawer_slots_records gui_ask_state; do eval "$fn() { :; }"; done
        disk_port() { echo 1; }; disk_drawer() { echo 1; }
        date() { cat "$RUN_DIR/clock"; }
        tick() { for t in "$@"; do echo "$t" > "$RUN_DIR/clock"; cloner_gui_state; wait; done; }
        T="$RUN_DIR/targets/sda"; mkdir -p "$GUI_DIR" "$T"
        echo writing > "$RUN_DIR/state"; echo writing > "$T/state"; echo 100000 > "$T/total"
        echo 0 > "$T/counter.index"
        # Partition 1 mid-receive: fanout's -P counter (drawers.sh) and the drawer's bytes.0.
        echo 4000 > "$RUN_DIR/bytes.raw"; echo 4000 > "$T/bytes.0"
        tick 100
        # Partition 1 reached EOF (source.rc); the drawer drains its buffer to the disk.
        echo 5000 >> "$RUN_DIR/bytes.raw"; echo 5000 > "$T/bytes.0"; echo 0 > "$RUN_DIR/source.rc"
        tick 101 120 141
        # The partition is done (target_partition_done): the next counter, empty.
        : > "$T/bytes.1"; echo 1 > "$T/counter.index"
        tick 150 180 190
        # Partition 2 (drawers.sh: rm source.rc bytes.raw; fanout opens its counter, no byte):
        # the sender waits for a slow drawer in the room (WAIT_STREAM_START_LATER_S).
        rm -f "$RUN_DIR/source.rc"; : > "$RUN_DIR/bytes.raw"
        tick 191 230 251
        # Its first bytes: the machine's counter moves a tick before the drawer's.
        echo 64 > "$RUN_DIR/bytes.raw"; tick 252
        echo 64 > "$T/bytes.1"; tick 253
"""


def test_no_outage_between_partitions_or_while_draining(tmp_path):
    # #2067 (Grok's review): the receive of partition 1 ended; the drawer
    # drains for 40 s, then the next partition waits 60 s for the sender.
    # The cable is in all along: no "network lost", and so no "back".
    run_ash(prelude(tmp_path) + ROOM)
    assert frequencies(tmp_path) == []


def test_an_outage_mid_partition_after_a_wait_is_still_heard(tmp_path):
    # The same room, then partition 2's bytes stand 30 s mid-receive: lost; they move: back.
    run_ash(prelude(tmp_path) + ROOM + """
        tick 270 283
        echo 900 > "$T/bytes.1"; echo 900 >> "$RUN_DIR/bytes.raw"; tick 284
    """)
    assert frequencies(tmp_path) == expected("netlost") + expected("netback")
