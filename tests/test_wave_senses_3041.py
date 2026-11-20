"""#3041 / #3125: the cloner's own senses during a chunk wave reach the server (S46-S50).

Nadav decided on 15/11 (#3041) that the brain learns each cloner's ceiling by itself, from every wave. The numbers it
needs were read by hand over ssh in the #3034 measurement, and the brain's catalog marked them `missing`:

- S48: per core, busy% of the window and the highest window of the wave (/proc/stat)
- S49: per core, irq% and softirq%, and NET_RX softirqs a second (/proc/stat, /proc/softirqs)
- S50: the card's rx_missed (sysfs) and rx_no_buffer (`ethtool -S`) since the wave began, and its RX ring (`ethtool -g`)
- S46/S47: per drawer, util% (io_ticks), in-flight, and ms a write (/proc/diskstats)

The agent (`agent/lib/restore/wavesense.sh`) closes a window every WAVE_SENSE_S seconds of a receiving pass, from the
hooks chunkloss.sh already runs (chunk_loss_mark / chunk_loss_tell), and the progress report carries the wave's
summary as `wave_senses`. The server keeps it per (wave, machine) in `wave_senses` and folds it into the brain's
long-term memory (`brain_memory`, kind machine) instead of deleting it.

**Principle 5 is the half most of these tests are about:** a sense that was not read is null, never 0 -- and "no
ethtool on the machine" is `"ethtool": false`, said in so many words, with what only ethtool reads null.

**Negative control:** see the PR body.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from native import requires_native
from server import db
from server.agent import reports
from server.brain import memory
from server.system import housekeeping
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
MAC = "78:ac:c0:9b:18:85"
needs_bash = requires_native(("bash", BASH), why="the agent's shell")

UNIT = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log" SYSROOT="$1/sysroot"; IFACE=eth0; CHUNK_SESSION=ses_1; WAVE_SENSE_S=5
export ETHTOOL="${NO_ETHTOOL:+$RUN_DIR/no-such-ethtool}"; ETHTOOL="${ETHTOOL:-$RUN_DIR/ethtool}"
. agent/lib/core/common.sh; . agent/lib/core/waits.sh; . agent/lib/core/datareport.sh; . agent/lib/restore/chunkloss.sh
# as the agent sources it. Not there = the tree before #3041 (the negative control): nothing is measured
[ ! -f agent/lib/restore/wavesense.sh ] || . agent/lib/restore/wavesense.sh
mkdir -p "$SYSROOT/proc" "$SYSROOT/sys/class/net/eth0/statistics" "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb"
: > "$RUN_DIR/recv.out"
printf '#!/bin/sh\ncat "${0%%/*}/eth$1" 2>/dev/null\n' > "$RUN_DIR/ethtool"; chmod +x "$RUN_DIR/ethtool"
up() { echo "$1 0.00" > "$SYSROOT/proc/uptime"; }
# $1, $2 = core 0 and core 1: "user nice system idle iowait irq softirq steal"
cpus() { printf 'cpu  0 0 0 0 0 0 0 0 0 0\ncpu0 %s 0 0\ncpu1 %s 0 0\nintr 1\n' "$1" "$2" > "$SYSROOT/proc/stat"; }
netrx() { printf '                    CPU0       CPU1\n          HI:          0          0\n      NET_RX:  %s  %s\n' "$1" "$2" > "$SYSROOT/proc/softirqs"; }
nodisks() { : > "$SYSROOT/proc/diskstats"; }
# $1 = name, $2 = writes completed, $3 = ms writing, $4 = in flight, $5 = io_ticks
disk() { printf '   8       0 %s 0 0 0 0 %s 0 0 %s %s %s 0 0 0 0 0\n' "$1" "$2" "$3" "$4" "$5" >> "$SYSROOT/proc/diskstats"; }
missed() { echo "$1" > "$SYSROOT/sys/class/net/eth0/statistics/rx_missed_errors"; }
nobuf() { printf 'NIC statistics:\n     rx_packets: 100\n     rx_no_buffer_count: %s\n' "$1" > "$RUN_DIR/eth-S"; }
ring() { printf 'Ring parameters for eth0:\nPre-set maximums:\nRX:\t\t%s\nRX Mini:\tn/a\nTX:\t\t4096\nCurrent hardware settings:\nRX:\t\t%s\nTX:\t\t256\n' "$2" "$1" > "$RUN_DIR/eth-g"; }
show() { echo "$1 $(cat "$RUN_DIR/wsense/senses.json" 2>/dev/null || echo none)"; }
pass() { chunk_loss_mark; }        # a receiving pass begins (chunkrecv.sh)
tick() { chunk_loss_tell "$RUN_DIR/recv.out"; }   # once a second while it runs
'''


def _unit(tmp_path, script: str, env: dict | None = None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.sh").write_text(UNIT + script, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, posix(tmp_path / "t.sh"), posix(tmp_path)], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL,
                          env=None if env is None else {**os.environ, **env})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = {}
    for line in proc.stdout.splitlines():
        name, _, text = line.partition(" ")
        out[name] = None if text == "none" else (json.loads(text) if text.startswith("{") else text)
    return out


def _core(senses, n):
    return next(c for c in senses["cpu"] if c["core"] == n)


# --- the agent ------------------------------------------------------------------------------------------------------


LOADED = r'''
up 100; cpus "100 0 50 800 50 0 0 0" "100 0 0 900 0 0 0 0"; netrx 1000 2000
nodisks; disk sda 100 1000 0 5000; disk sdb 10 10 0 100; missed 7; nobuf 3; ring 256 4096
pass; show start
up 103; tick; show early
up 105.5; cpus "160 0 70 810 55 2 3 0" "100 0 0 1000 0 0 0 0"; netrx 6500 2000
nodisks; disk sda 200 1300 12 9400; missed 9; nobuf 4
tick; show w1
'''


@needs_bash
def test_a_window_says_each_core_each_drawer_and_the_card(tmp_path):
    got = _unit(tmp_path, LOADED)
    start = got["start"]
    assert start is not None, "nothing was measured at the start of the pass"
    assert start["windows"] == 0 and start["cpu"] is None and start["window_s"] == 5
    assert start["nic"] == {"iface": "eth0", "ethtool": True, "rx_missed": 0, "rx_no_buffer": 0,
                            "ring_rx": 256, "ring_rx_max": 4096}
    assert got["early"]["windows"] == 0, "a window closed before WAVE_SENSE_S passed"
    w1 = got["w1"]
    assert w1["windows"] == 1
    # core 0: 100 jiffies in the window -- idle 10, iowait 5, irq 2, softirq 3 -> busy 85%
    assert _core(w1, 0) == {"core": 0, "busy": 85, "busy_max": 85, "irq": 2, "softirq": 3, "softirq_max": 3,
                            "net_rx_ps": 1000, "net_rx_ps_max": 1000}
    assert _core(w1, 1)["busy"] == 0 and _core(w1, 1)["net_rx_ps"] == 0
    # sda: 4400 ms of io in a 5.5 s window = 80%; 300 ms for 100 writes = 3 ms a write; 12 in flight now
    assert w1["disks"]["sda"] == {"util": 80, "util_max": 80, "inflight": 12, "inflight_max": 12,
                                  "w_ms": 3.0, "w_ms_max": 3.0}
    # sdb left /proc/diskstats in the window: not measured -- not an idle drawer
    assert w1["disks"]["sdb"] is None
    assert w1["nic"]["rx_missed"] == 2 and w1["nic"]["rx_no_buffer"] == 1


@needs_bash
def test_the_highest_window_of_the_wave_stays_beside_the_latest(tmp_path):
    got = _unit(tmp_path, LOADED + r'''
up 111; cpus "178 0 80 880 55 2 5 0" "100 0 0 1100 0 0 0 0"; netrx 7000 2000
nodisks; disk sda 210 1310 1 9500; disk sdb 20 20 0 200
tick; show w2
''')
    w2 = got["w2"]
    assert w2["windows"] == 2
    # 100 jiffies: idle 70 -> busy 30, softirq 2; the peak of the wave is still the first window's
    assert _core(w2, 0)["busy"] == 30 and _core(w2, 0)["busy_max"] == 85
    assert _core(w2, 0)["softirq_max"] == 3 and _core(w2, 0)["net_rx_ps_max"] == 1000
    assert w2["disks"]["sda"]["inflight"] == 1 and w2["disks"]["sda"]["inflight_max"] == 12
    assert w2["disks"]["sda"]["util_max"] == 80 and w2["disks"]["sda"]["w_ms"] == 1.0
    # sdb is back in diskstats, but one reading is not a window: still not measured, not "idle"
    assert w2["disks"]["sdb"] is None


@needs_bash
def test_what_could_not_be_read_is_null_never_zero(tmp_path):
    """No /proc/stat, no softirqs, no diskstats line for a drawer, no rx_missed in sysfs: each is null."""
    got = _unit(tmp_path, r'''
up 100; nodisks; nobuf 3; ring 256 4096
pass
up 106; tick; show w1
''')
    w1 = got["w1"]
    assert w1 is not None and w1["windows"] == 1
    assert w1["cpu"] is None
    assert w1["disks"] == {"sda": None, "sdb": None}
    assert w1["nic"]["rx_missed"] is None, "an rx_missed that sysfs does not have was reported as a number"


@needs_bash
def test_no_ethtool_is_said_as_not_available_and_not_as_zero(tmp_path):
    got = _unit(tmp_path, r'''
up 100; cpus "1 0 0 1 0 0 0 0" "1 0 0 1 0 0 0 0"; nodisks; missed 7
pass
up 106; cpus "2 0 0 2 0 0 0 0" "2 0 0 2 0 0 0 0"; missed 8; tick; show w1
''', env={"NO_ETHTOOL": "1"})
    nic = got["w1"]["nic"]
    assert nic == {"iface": "eth0", "ethtool": False, "rx_missed": 1, "rx_no_buffer": None,
                   "ring_rx": None, "ring_rx_max": None}


@needs_bash
def test_a_driver_without_the_counter_and_a_counter_that_went_down_are_not_read(tmp_path):
    """ethtool is there, but the driver has no rx_no_buffer_count (not igb/e1000e); and the card was reset in the
    wave, so rx_missed went down: neither is "no drops"."""
    got = _unit(tmp_path, r'''
up 100; nodisks; missed 9; ring 512 4096
printf 'NIC statistics:\n     rx_packets: 100\n' > "$RUN_DIR/eth-S"
pass
up 106; missed 2; tick; show w1
''')
    nic = got["w1"]["nic"]
    assert nic["ethtool"] is True and nic["ring_rx"] == 512
    assert nic["rx_no_buffer"] is None and nic["rx_missed"] is None


@needs_bash
def test_a_new_wave_starts_from_nothing_and_a_report_carries_only_its_own_wave(tmp_path):
    got = _unit(tmp_path, LOADED + r'''
CHUNK_SESSION=ses_2
pass; show next
echo "mine $(machine_extra_json ses_2 '' | grep -c '^,"wave_senses":{"window_s":5,"windows":0,')"
echo "other $(machine_extra_json ses_1 '' | grep -c wave_senses)"
echo "task $(machine_extra_json ses_2 task_9 | grep -c wave_senses)"
''')
    assert got["next"]["windows"] == 0 and got["next"]["cpu"] is None, "the last wave's windows leaked into this one"
    assert got["mine"] == "1", "the report of the wave did not carry its senses"
    assert got["other"] == "0", "the report of another wave carried this wave's senses"
    assert got["task"] == "0", "a task report (a capture) carried a wave's senses"


@needs_bash
def test_the_progress_report_carries_the_summary(tmp_path):
    got = _unit(tmp_path, LOADED + r'''
echo "extra $(machine_extra_json ses_1 '' | sed 's/^,"wave_senses"://')"
''')
    assert got["extra"] == got["w1"]


# --- the server -----------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at, started_at) VALUES ('ses_w', 'grp_CLONERS', 'img', 'p', 1, 300,"
              " 'running', 't', ?, 0, ?)", (db.now_iso(), db.now_iso()))
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('ses_w', ?, 'writing', ?)",
              (MAC, db.now_iso()))
    c.commit()
    return c


SENSES = {"window_s": 5, "windows": 3,
          "cpu": [{"core": 0, "busy": 91, "busy_max": 92, "irq": 1, "softirq": 30, "softirq_max": 34,
                   "net_rx_ps": 21000, "net_rx_ps_max": 23000},
                  {"core": 1, "busy": 88, "busy_max": 90, "irq": 0, "softirq": 2, "softirq_max": 3,
                   "net_rx_ps": 0, "net_rx_ps_max": 10}],
          "nic": {"iface": "eth0", "ethtool": True, "rx_missed": 4, "rx_no_buffer": 0, "ring_rx": 256,
                  "ring_rx_max": 4096},
          "disks": {"sda": {"util": 60, "util_max": 97, "inflight": 3, "inflight_max": 31, "w_ms": 2.5,
                            "w_ms_max": 9.1}, "sdb": None}}


def _report(conn, senses, state="writing"):
    body = {"session_id": "ses_w", "mac": MAC, "state": state, "targets": []}
    if senses is not ...:
        body["wave_senses"] = senses
    assert reports.ingest(conn, body)["ok"]


def _kept(conn):
    rows = memory.knowledge(conn, "machine", MAC)["recent"].get("wave_senses")
    return None if not rows else [json.loads(r["senses_json"]) for r in rows]


def test_the_wave_s_summary_is_kept_per_wave_and_machine_as_sent(conn):
    _report(conn, SENSES)
    assert _kept(conn) == [SENSES]
    later = json.loads(json.dumps(SENSES)); later["windows"] = 4
    _report(conn, later)
    assert _kept(conn) == [later], "each report replaces the wave's summary -- one row per (wave, machine)"
    _report(conn, ...)                       # a report without it (a quiet one) leaves the last
    assert _kept(conn) == [later]


def test_a_broken_value_is_null_alone_and_never_a_zero(conn):
    bad = json.loads(json.dumps(SENSES))
    bad["cpu"][0]["busy"] = 140             # a percent above 100 is a broken read
    bad["cpu"][1]["softirq"] = "high"
    bad["nic"]["rx_missed"] = -3
    bad["disks"]["sda"]["w_ms"] = True
    _report(conn, bad)
    [kept] = _kept(conn)
    assert kept["cpu"][0]["busy"] is None and kept["cpu"][0]["busy_max"] == 92
    assert kept["cpu"][1]["softirq"] is None and kept["cpu"][1]["busy"] == 88
    assert kept["nic"]["rx_missed"] is None and kept["nic"]["ring_rx"] == 256
    assert kept["disks"]["sda"]["w_ms"] is None and kept["disks"]["sda"]["util_max"] == 97
    assert kept["disks"]["sdb"] is None


def test_without_ethtool_the_server_keeps_not_available(conn):
    sent = json.loads(json.dumps(SENSES))
    sent["nic"] = {"iface": "eth0", "ethtool": False, "rx_missed": 1, "rx_no_buffer": 0, "ring_rx": 0,
                   "ring_rx_max": 0}
    _report(conn, sent)
    [kept] = _kept(conn)
    # an agent that says "no ethtool" and sends 0 beside it: the 0 cannot be a measurement
    assert kept["nic"] == {"iface": "eth0", "ethtool": False, "rx_missed": 1, "rx_no_buffer": None,
                           "ring_rx": None, "ring_rx_max": None}


def test_a_summary_that_is_not_an_object_keeps_the_last_good_one(conn):
    _report(conn, SENSES)
    _report(conn, "broken")
    assert _kept(conn) == [SENSES]


def test_an_old_wave_is_folded_into_the_machine_s_memory_not_deleted(conn):
    _report(conn, SENSES)
    assert _kept(conn), "the senses were not kept at all"
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")
    conn.execute("UPDATE wave_senses SET at = ?", (old,))
    conn.commit()
    housekeeping.purge_tables(conn)
    assert _kept(conn) is None, "a wave older than the window was neither folded nor deleted"
    got = memory.summary(conn, "machine", MAC).get("wave_senses")
    assert got is not None and got["rows"] == 1
    num = got["num"]
    assert num["cpu_busy_max"]["max"] == 92 and num["cpu_busy_min_core"]["last"] == 88
    assert num["rx_missed"]["last"] == 4 and num["disk_inflight_max"]["max"] == 31
    assert got["text"]["session_id"]["value"] == "ses_w"


def test_the_fold_does_not_count_what_was_not_read_as_zero(conn):
    sent = {"window_s": 5, "windows": 1, "cpu": None,
            "nic": {"iface": "eth0", "ethtool": False, "rx_missed": None, "rx_no_buffer": None, "ring_rx": None,
                    "ring_rx_max": None}, "disks": {"sda": None}}
    _report(conn, sent)
    assert _kept(conn), "the senses were not kept at all"
    conn.execute("UPDATE wave_senses SET at = ?",
                 ((datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds"),))
    conn.commit()
    housekeeping.purge_tables(conn)
    got = memory.summary(conn, "machine", MAC).get("wave_senses")
    assert got is not None and got["rows"] == 1
    assert got["num"] == {}, f"a sense that was not read was summed as a number: {got['num']}"
