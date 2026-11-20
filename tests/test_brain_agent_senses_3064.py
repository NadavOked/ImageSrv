"""#3064: חושי הסוכן במשכפל -- S41 S51 S52 S53 S54 S56 S57 S58 S59 S60 S65, בצד הסוכן.

כל חוש מדפיס ערך JSON, ו-``null`` כשלא נקרא ("לא נמדד", עיקרון 5): לעולם לא 0 ולעולם לא "תקין".
מה שנבדק לכל חוש: הערך כשנקרא, ו-``null`` כשאין מקור. הצד של השרת בקובץ `test_brain_agent_senses_server_3064.py`.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
pytestmark = requires_native(("bash", BASH), why="the agent's shell")

HEAD = r'''
export RUN_DIR="$1/run" LOG_FILE="$1/run/agent.log" SYSROOT="$1/root" DEVROOT="$1/dev" IFACE=eth0
mkdir -p "$RUN_DIR/targets" "$RUN_DIR/smart" "$SYSROOT/proc" "$SYSROOT/sys" "$DEVROOT"
. agent/lib/core/common.sh; . agent/lib/core/waits.sh
. agent/lib/hw/netprobe.sh; . agent/lib/hw/sysinfo.sh; . agent/lib/hw/diskinfo.sh; . agent/lib/hw/fwimage.sh
. agent/lib/smart/smartattrs.sh; . agent/lib/smart/hwissues.sh
. agent/lib/hw/sensecore.sh; . agent/lib/hw/hostsense.sh; . agent/lib/hw/linksense.sh; . agent/lib/hw/disksense.sh
. agent/lib/restore/rxsense.sh; . agent/lib/restore/chunkfetch.sh   # chunkfetch: _chunk_taken, the count rx_http_note reads
'''


def sh(tmp_path: Path, script: str) -> str:
    (tmp_path / "t.sh").write_text(HEAD + script, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, posix(tmp_path / "t.sh"), posix(tmp_path)], capture_output=True, text=True, encoding="utf-8",
                          cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def lines(out: str) -> dict:
    """`name=value` lines the script printed (the agent's own `log` echoes `imagesrv: ...` -- ignored)."""
    return dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln and not ln.startswith("imagesrv:"))


def stub(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return posix(path)


def sata_disk(root: Path, disk: str, ata: int) -> None:
    """/sys/block/<disk>/device -> .../ataN/hostH/target/lun, the path `_hw_ata` and `disk_port` read."""
    real = root / "sys/devices/pci0000:00/0000:00:1f.2" / f"ata{ata}/host{ata - 1}/target{ata - 1}:0:0/{ata - 1}:0:0:0"
    real.mkdir(parents=True)
    (root / "sys/block" / disk).mkdir(parents=True)
    try:
        os.symlink(real, root / "sys/block" / disk / "device")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")


# --- S51: carrier_changes --------------------------------------------------------------------------------------------


def _nic(root: Path, changes: str | None) -> None:
    nic = root / "sys/class/net/eth0"
    nic.mkdir(parents=True, exist_ok=True)
    (nic / "carrier").write_text("1\n")
    if changes is not None:
        (nic / "carrier_changes").write_text(changes + "\n")


def test_carrier_changes_are_told_now_and_since_the_rounds_first_tick(tmp_path):
    _nic(tmp_path / "root", "3")
    out = sh(tmp_path, r'''
D=$(ss_dir ses_1); echo "a=$(ss_carrier_json "$D")"
echo 5 > "$SYSROOT/sys/class/net/eth0/carrier_changes"; echo "b=$(ss_carrier_json "$D")"
D=$(ss_dir ses_2); echo "c=$(ss_carrier_json "$D")"
''')
    got = lines(out)
    assert json.loads(got["a"]) == {"iface": "eth0", "changes": 3, "delta": 0}
    assert json.loads(got["b"]) == {"iface": "eth0", "changes": 5, "delta": 2}
    assert json.loads(got["c"]) == {"iface": "eth0", "changes": 5, "delta": 0}, "a new round starts its own baseline"


def test_carrier_changes_not_read_is_null_never_zero(tmp_path):
    _nic(tmp_path / "root", None)
    got = lines(sh(tmp_path, r'D=$(ss_dir ses_1); echo "a=$(ss_carrier_json "$D")"'))
    assert json.loads(got["a"]) is None


# --- S60: PSI ---------------------------------------------------------------------------------------------------------


def _psi(root: Path, cpu: str, io_some: str, io_full: str, mem_some: str, mem_full: str) -> None:
    d = root / "proc/pressure"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpu").write_text(f"some avg10={cpu} avg60=0.00 avg300=0.00 total=1\n")
    (d / "io").write_text(f"some avg10={io_some} avg60=0.00 avg300=0.00 total=1\nfull avg10={io_full} avg60=0.00 avg300=0.00 total=1\n")
    (d / "memory").write_text(f"some avg10={mem_some} avg60=0.00 avg300=0.00 total=1\nfull avg10={mem_full} avg60=0.00 avg300=0.00 total=1\n")


def test_psi_keeps_the_highest_of_the_round_and_counts_the_samples(tmp_path):
    root = tmp_path / "root"
    _psi(root, "1.50", "2.00", "0.50", "0.00", "0.00")
    got = lines(sh(tmp_path, r'''
D=$(ss_dir ses_1); echo "a=$(ss_psi_json "$D")"
'''))
    assert json.loads(got["a"]) == {"samples": 1, "peak": {"cpu_some": 1.5, "io_some": 2.0, "io_full": 0.5, "mem_some": 0.0, "mem_full": 0.0}}
    _psi(root, "0.20", "9.00", "0.10", "3.25", "1.00")
    got = lines(sh(tmp_path, r'D=$(ss_dir ses_1); echo "b=$(ss_psi_json "$D")"'))
    assert json.loads(got["b"]) == {"samples": 2, "peak": {"cpu_some": 1.5, "io_some": 9.0, "io_full": 0.5, "mem_some": 3.25, "mem_full": 1.0}}


def test_psi_without_the_kernels_pressure_files_is_null(tmp_path):
    got = lines(sh(tmp_path, r'D=$(ss_dir ses_1); echo "a=$(ss_psi_json "$D")"'))
    assert json.loads(got["a"]) is None


# --- S65: the controller's driver -------------------------------------------------------------------------------------


def _pci(root: Path, addr: str, cls: str, driver: str | None) -> None:
    dev = root / "sys/bus/pci/devices" / addr
    dev.mkdir(parents=True)
    (dev / "class").write_text(cls + "\n")
    if driver:
        target = root / "sys/bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(target, dev / "driver")
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")


def test_the_controller_driver_is_what_is_bound_and_none_when_nothing_is(tmp_path):
    root = tmp_path / "root"
    _pci(root, "0000:00:1f.2", "0x010601", "ahci")
    _pci(root, "0000:03:00.0", "0x010400", None)            # a RAID class with no driver in the initramfs
    _pci(root, "0000:00:02.0", "0x030000", "i915")          # not storage
    got = lines(sh(tmp_path, r'echo "a=$(ss_ctrl_json)"; echo "b=$(ss_sata_driver ahci)"; echo "c=$(ss_sata_driver raid)"; echo "d=$(ss_sata_driver ide)"'))
    assert json.loads(got["a"]) == [{"mode": "ahci", "driver": "ahci"}, {"mode": "raid", "driver": "none"}]
    assert (got["b"], got["c"], got["d"]) == ("ahci", "none", ""), "a mode that is not there has no driver -- nothing, not a guess"


def test_ide_mode_names_its_driver_in_the_hello_fragment(tmp_path):
    root = tmp_path / "root"
    _pci(root, "0000:00:1f.2", "0x010185", "ata_piix")
    out = sh(tmp_path, r'firmware_json; echo')
    assert json.loads("{" + out.strip().lstrip(",") + "}") == {"sata_mode": "ide", "sata_driver": "ata_piix"}


def test_no_pci_bus_is_null_not_an_empty_list(tmp_path):
    got = lines(sh(tmp_path, r'echo "a=$(ss_ctrl_json)"'))
    assert json.loads(got["a"]) is None


# --- S52: the phy event counters ---------------------------------------------------------------------------------------

PHY = """SATA Phy Event Counters (GP Log 0x11)
ID      Size     Value  Description
0x0001  2            {icrc}  Command failed due to ICRC error
0x0009  2           {rdy}  Transition from drive PhyRdy to drive PhyNRdy
0x000a  2            {rst}  Device-to-host register FISes sent due to a COMRESET
"""


def _phy_run(tmp_path: Path, before: tuple, after: str | None) -> dict:
    run = tmp_path / "run"
    (run / "smart").mkdir(parents=True, exist_ok=True)
    (run / "targets/sda").mkdir(parents=True, exist_ok=True)
    (run / "smart/sda.out").write_text("smartctl\n\n" + PHY.format(icrc=before[0], rdy=before[1], rst=before[2]))
    body = "exit 4" if after is None else f"cat <<'EOF'\n{after}\nEOF\nexit 0"
    smartctl = stub(tmp_path / "smartctl", body)
    out = sh(tmp_path, f'SMARTCTL={smartctl}; ls_phy_after sda; echo "j=$(ls_phy_json sda)"')
    return json.loads(lines(out)["j"])


def test_phy_events_are_the_delta_of_the_round_by_counter(tmp_path):
    got = _phy_run(tmp_path, (10, 4, 3), PHY.format(icrc=10, rdy=9, rst=5))
    assert got == {"total": 7, "ids": {"0x0009": 5, "0x000a": 2}}, "since power-on counters: only what rose in the round"


def test_phy_events_that_did_not_rise_are_a_measured_zero(tmp_path):
    assert _phy_run(tmp_path, (1, 2, 3), PHY.format(icrc=1, rdy=2, rst=3)) == {"total": 0, "ids": {}}


@pytest.mark.parametrize("after", [None, "no table here", PHY.format(icrc=0, rdy=0, rst=0)], ids=["no-answer", "no-table", "counter-fell"])
def test_phy_events_that_could_not_be_compared_are_null(tmp_path, after):
    assert _phy_run(tmp_path, (10, 4, 3), after) is None


# --- S53 + S58: link resets and the connection's name -------------------------------------------------------------------


def _dmesg(tmp_path: Path, lines_: list[str]) -> str:
    (tmp_path / "dmesg.txt").write_text("\n".join(lines_) + "\n")
    return stub(tmp_path / "dmesg", f"cat {posix(tmp_path / 'dmesg.txt')}")


RESET1 = "[  100.000000] ata1: hard resetting link"
RESET2 = "[  200.000000] ata1.00: hard resetting link"


def test_link_resets_count_from_the_first_tick_of_the_write_and_name_the_boot_count(tmp_path):
    sata_disk(tmp_path / "root", "sda", 1)
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    dm = _dmesg(tmp_path, ["[  1.0] ata1: SATA link up 6.0 Gbps", RESET1, "[  120.0] ata2: hard resetting link"])
    out = sh(tmp_path, f'DMESG={dm}; SENSE_RESETS_EVERY_S=0; mkdir -p "$RUN_DIR/senses"\n'
                       'ls_resets_refresh; echo "a=$(ls_resets_json sda writing)"\n'
                       f'printf "%s\\n" "{RESET2}" >> {posix(tmp_path / "dmesg.txt")}; ls_resets_refresh; echo "b=$(ls_resets_json sda writing)"')
    got = lines(out)
    assert json.loads(got["a"]) == {"boot": 1, "delta": 0}
    assert json.loads(got["b"]) == {"boot": 2, "delta": 1}, "ata1.00 is ata1's link: counted on its disk, not on ata2's"


def test_a_drawer_first_seen_after_the_write_has_no_baseline_so_no_delta(tmp_path):
    sata_disk(tmp_path / "root", "sda", 1)
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    dm = _dmesg(tmp_path, ["[  1.0] ata1: SATA link up 6.0 Gbps", RESET1])
    got = lines(sh(tmp_path, f'DMESG={dm}; SENSE_RESETS_EVERY_S=0; mkdir -p "$RUN_DIR/senses"; ls_resets_refresh; echo "a=$(ls_resets_json sda done)"'))
    assert json.loads(got["a"]) == {"boot": 1, "delta": None}


def test_a_kernel_log_that_did_not_read_is_null_not_no_resets(tmp_path):
    sata_disk(tmp_path / "root", "sda", 1)
    dm = stub(tmp_path / "dmesg", "exit 1")
    got = lines(sh(tmp_path, f'DMESG={dm}; SENSE_RESETS_EVERY_S=0; mkdir -p "$RUN_DIR/senses"; ls_resets_refresh; echo "a=$(ls_resets_json sda writing)"'))
    assert json.loads(got["a"]) is None


def test_the_connection_name_is_ata_n_the_console_s_sata_n_minus_one_and_the_drawer(tmp_path):
    sata_disk(tmp_path / "root", "sda", 2)
    sata_disk(tmp_path / "root", "sdb", 3)
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "run/drawer_slots").write_text("ok\n1|sata:2\n")
    got = lines(sh(tmp_path, 'echo "a=$(ls_conn_json sda)"; echo "b=$(ls_conn_json sdb)"; echo "c=$(ls_conn_json nvme0n1)"'))
    assert json.loads(got["a"]) == {"ata": 2, "sata": 1, "drawer": 1}
    assert json.loads(got["b"]) == {"ata": 3, "sata": 2, "drawer": "unmapped"}, "the map was read and does not have it"
    assert json.loads(got["c"]) is None


def test_the_drawer_of_a_map_that_was_not_read_is_null(tmp_path):
    sata_disk(tmp_path / "root", "sda", 1)
    got = lines(sh(tmp_path, 'echo "a=$(ls_conn_json sda)"'))
    assert json.loads(got["a"]) == {"ata": 1, "sata": 0, "drawer": None}


# --- S54: temperature --------------------------------------------------------------------------------------------------

ATTRS = "ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE\n"
TEMP = "194 Temperature_Celsius     0x0022   066   045   000    Old_age   Always       -       {c} (Min/Max 20/45)\n"


def test_the_temperature_is_before_during_and_after_and_a_sample_is_one_drawer_per_interval(tmp_path):
    run = tmp_path / "run"
    for d in ("sda", "sdb"):
        (run / f"targets/{d}").mkdir(parents=True)
        (run / f"targets/{d}/state").write_text("writing\n")
    (run / "smart").mkdir(parents=True)
    (run / "smart/sda.out").write_text(ATTRS + TEMP.format(c=31))
    smartctl = stub(tmp_path / "smartctl", f"printf '%s' '{ATTRS}{TEMP.format(c=44)}'; exit 0")
    (run / "targets/sda/temp_after").write_text("38\n")
    out = sh(tmp_path, f'SMARTCTL={smartctl}; mkdir -p "$RUN_DIR/senses"; SENSE_TEMP_EVERY_S=0\n'
                       'ds_temp_tick; ds_temp_tick; ds_temp_tick\n'
                       'echo "a=$(ds_temp_json sda)"; echo "b=$(ds_temp_json sdb)"; echo "n=$(cat "$RUN_DIR/targets/sdb/ss.temp.n")"')
    got = lines(out)
    assert json.loads(got["a"]) == {"before_c": 31, "peak_during_c": 44, "after_c": 38, "samples": 2}
    assert json.loads(got["b"]) == {"before_c": None, "peak_during_c": 44, "after_c": None, "samples": 1}
    assert got["n"] == "1", "three ticks took the first, the second and the first again: round robin, one drawer each"


def test_a_drive_that_did_not_answer_leaves_the_temperature_null(tmp_path):
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    (tmp_path / "run/targets/sda/state").write_text("writing\n")
    smartctl = stub(tmp_path / "smartctl", "exit 2")
    out = sh(tmp_path, f'SMARTCTL={smartctl}; mkdir -p "$RUN_DIR/senses"; SENSE_TEMP_EVERY_S=0; ds_temp_tick; echo "a=$(ds_temp_json sda)"')
    assert json.loads(lines(out)["a"]) is None


# --- S56 + S57: insertion to ready, and present at boot -----------------------------------------------------------------


def test_ready_is_the_seconds_from_the_kernels_attach_to_the_first_stage_of_the_write(tmp_path):
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    (tmp_path / "run/targets/sdb").mkdir(parents=True)
    (tmp_path / "run/targets/sda/stages").write_text("gpt 40.5\np1 50.0\n")
    dm = _dmesg(tmp_path, ["[    5.1] sd 0:0:0:0: [sda] Attached SCSI disk", "[   12.5] sd 1:0:0:0: [sda] Attached SCSI disk",
                           "[   13.0] sd 2:0:0:0: [sdc] Attached SCSI disk"])
    out = sh(tmp_path, f'DMESG={dm}; echo "a=$(ds_ready_json sda)"; echo "b=$(ds_ready_json sdb)"; echo "c=$(ds_ready_json sda)"')
    got = lines(out)
    assert json.loads(got["a"]) == 28, "the LAST attach of the name: 40.5 - 12.5"
    assert json.loads(got["b"]) is None, "the write has not begun"
    assert json.loads(got["c"]) == 28


def test_ready_is_null_when_the_kernel_log_does_not_name_the_drive(tmp_path):
    (tmp_path / "run/targets/nvme0n1").mkdir(parents=True)
    (tmp_path / "run/targets/nvme0n1/stages").write_text("gpt 40.5\n")
    dm = _dmesg(tmp_path, ["[    5.1] sd 0:0:0:0: [sda] Attached SCSI disk"])
    assert json.loads(lines(sh(tmp_path, f'DMESG={dm}; echo "a=$(ds_ready_json nvme0n1)"'))["a"]) is None


def test_a_disk_that_was_here_at_the_first_look_is_at_boot_and_one_that_came_later_is_not(tmp_path):
    root = tmp_path / "root"
    (root / "proc").mkdir(parents=True)
    (root / "proc/uptime").write_text("12.34 40.00\n")
    for d in ("sda", "sdb"):
        (root / "sys/block" / d / "device").mkdir(parents=True)
        (root / "sys/block" / d / "size").write_text("2048\n")
        (root / "sys/block" / d / "device/serial").write_text(f"SER{d}\n")
    out = sh(tmp_path, 'echo "none=$(ds_boot_json sda)"; ss_post_mark; echo "a=$(ds_boot_json sda)"; echo "c=$(ds_boot_json sdc)"; '
                       'echo "marked=$(head -n 1 "$RUN_DIR/senses/post.disks")"; ss_post_mark; echo "again=$(wc -l < "$RUN_DIR/senses/post.disks" | tr -d " ")"')
    got = lines(out)
    assert got["none"] == "null", "no first look recorded: unknown, not 'came later'"
    assert got["a"] == "true" and got["c"] == "false" and got["marked"] == "t0 12.34"
    assert int(got["again"]) >= 2, "the mark is made once"


# --- S41: the receiver's counters ---------------------------------------------------------------------------------------

RX = "rxstats pkts=1000 foreign=2 stray={stray} bad_pkt=0 dup=5 evicted=7 busy=0 bad_chunk=1 good=900 skipped=3 conflict=0 version=0\n"


def test_rx_counters_are_summed_over_the_passes_that_said_them_and_the_server_sees_how_many_did(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run/p1.out").write_text("listening\n" + RX.format(stray=4) + "pass complete\n")
    (tmp_path / "run/p2.out").write_text("listening\n" + RX.format(stray=6) + "pass complete\n")
    (tmp_path / "run/p3.out").write_text("listening\npass complete\n")          # an older chunkcast: no counters
    out = sh(tmp_path, 'export CHUNK_SESSION=ses_1; D=$(ss_dir ses_1)\n'
                       'rx_sense_pass "$RUN_DIR/p1.out"; rx_sense_pass "$RUN_DIR/p2.out"; echo "a=$(ss_rx_json "$D")"\n'
                       'rx_sense_pass "$RUN_DIR/p3.out"; echo "b=$(ss_rx_json "$D")"; echo "n=$(ss_rx_json "$RUN_DIR/senses/none")"')
    got = lines(out)
    a, b = json.loads(got["a"]), json.loads(got["b"])
    assert (a["passes"], a["read"], a["pkts"], a["evicted"], a["stray"], a["bad_chunk"]) == (2, 2, 2000, 14, 10, 2)
    assert (b["passes"], b["read"], b["pkts"]) == (3, 2, 2000), "the third pass said nothing: counted as a pass, not as zero"
    assert json.loads(got["n"]) is None


def test_a_counter_chunkcast_could_not_know_makes_its_sum_null(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run/p1.out").write_text(RX.format(stray="-"))
    (tmp_path / "run/p2.out").write_text(RX.format(stray=3))
    out = sh(tmp_path, 'export CHUNK_SESSION=ses_1; D=$(ss_dir ses_1); rx_sense_pass "$RUN_DIR/p1.out"; rx_sense_pass "$RUN_DIR/p2.out"; echo "a=$(ss_rx_json "$D")"')
    a = json.loads(lines(out)["a"])
    assert a["stray"] is None and a["pkts"] == 2000, "stray without --from is not 0: the sum of it is not a number"


def test_a_late_pass_of_an_earlier_round_does_not_join_the_next_one(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run/p1.out").write_text(RX.format(stray=1))
    out = sh(tmp_path, 'export CHUNK_SESSION=ses_1; rx_sense_pass "$RUN_DIR/p1.out"; D=$(ss_dir ses_2); echo "a=$(ss_rx_json "$D")"; ls "$RUN_DIR/senses" | tr "\\n" " " | sed "s/^/dirs=/"; echo')
    got = lines(out)
    assert json.loads(got["a"]) is None and got["dirs"].strip() == "ses_2"


# --- S59: the HTTP fetches ---------------------------------------------------------------------------------------------


def test_http_fetches_add_up_their_ranges_bytes_chunks_seconds_and_curl_failures(tmp_path):
    run = tmp_path / "run"
    (run / "chunk").mkdir(parents=True)
    (run / "chunk/fetch.ranges").write_text("0-9,20-29,40-40\n")
    (run / "chunk/fetch.curl").write_text('url = "u"\nrange = "0-99"\nfail\nnext\nurl = "u"\nrange = "200-399"\n')
    (tmp_path / "root/proc").mkdir(parents=True)
    (tmp_path / "root/proc/uptime").write_text("100.0 1\n")
    out = sh(tmp_path, 'export CHUNK_SESSION=ses_1 CHUNK_DIR="$RUN_DIR/chunk" CHUNK_STATE="$RUN_DIR/chunk/state"; D=$(ss_dir ses_1)\n'
                       'echo 7 > "$CHUNK_STATE.taken"; echo 0 > "$CHUNK_DIR/fetch.rc"\n'
                       'rx_http_begin; echo "130.0 1" > "$SYSROOT/proc/uptime"; rx_http_note 0; echo "a=$(ss_http_json "$D")"\n'
                       'echo 28 > "$CHUNK_DIR/fetch.rc"; rx_http_begin; echo "135.0 1" > "$SYSROOT/proc/uptime"; rx_http_note 1; echo "b=$(ss_http_json "$D")"')
    got = lines(out)
    assert json.loads(got["a"]) == {"fetches": 1, "ranges": 3, "req_bytes": 300, "taken": 7, "secs": 30, "curl_rc": 0, "curl_failed": 0}
    assert json.loads(got["b"]) == {"fetches": 2, "ranges": 6, "req_bytes": 600, "taken": 14, "secs": 35, "curl_rc": 28, "curl_failed": 1}


def test_a_fetch_whose_taken_count_was_not_read_makes_that_sum_null(tmp_path):
    run = tmp_path / "run"
    (run / "chunk").mkdir(parents=True)
    (run / "chunk/fetch.ranges").write_text("0-9\n")
    (run / "chunk/fetch.curl").write_text('range = "0-99"\n')
    out = sh(tmp_path, 'export CHUNK_SESSION=ses_1 CHUNK_DIR="$RUN_DIR/chunk" CHUNK_STATE="$RUN_DIR/chunk/state"; D=$(ss_dir ses_1)\n'
                       'echo 0 > "$CHUNK_DIR/fetch.rc"; rx_http_begin; rx_http_note 0; echo "a=$(ss_http_json "$D")"')
    a = json.loads(lines(out)["a"])
    assert a["taken"] is None and a["secs"] is None and a["ranges"] == 1, a


# --- the whole: both extra fragments of the progress report ----------------------------------------------------------------


def test_the_machine_and_target_fragments_are_valid_json_and_only_in_a_round(tmp_path):
    root, run = tmp_path / "root", tmp_path / "run"
    _nic(root, "2")
    sata_disk(root, "sda", 1)
    (run / "targets/sda").mkdir(parents=True)
    (run / "targets/sda/state").write_text("writing\n")
    out = sh(tmp_path, '. agent/lib/core/datareport.sh; DMESG=true; mkdir -p "$RUN_DIR/senses"; date +%s > "$RUN_DIR/senses/temp.at"; SENSE_TEMP_EVERY_S=9999\n'
                       'echo "task=$(machine_extra_json ses_1 task_1)"; echo "idle=$(machine_extra_json ses_1 "")"\n'
                       'echo writing > "$RUN_DIR/state"; echo "m=$(machine_extra_json ses_1 "")"; echo "t=$(target_extra_json sda)"\n'
                       'echo waiting > "$RUN_DIR/targets/sda/state"; echo "w=$(target_extra_json sda)"')
    got = lines(out)
    assert got["task"] == "" and got["idle"] == "", "a capture and a machine that is not writing carry no senses"
    machine = json.loads("{" + got["m"].lstrip(",") + "}")["senses"]
    assert set(machine) == {"carrier", "psi", "ctrl", "rx", "http", "tune"}
    assert machine["carrier"] == {"iface": "eth0", "changes": 2, "delta": 0} and machine["psi"] is None and machine["rx"] is None
    target = json.loads("{" + got["t"].lstrip(",") + "}")["senses"]
    assert set(target) == {"phy", "resets", "conn", "temp", "ready_s", "at_boot"}
    assert target["phy"] is None and target["conn"]["ata"] == 1 and target["at_boot"] is None
    assert got["w"] == "", "a drawer that has not begun carries nothing"


def test_every_new_library_is_sourced_by_the_agent_and_stays_under_the_wall():
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    for name in ("sensecore", "hostsense", "linksense", "disksense", "rxsense"):
        assert f'. "$LIB_DIR/{name}.sh"' in main, name
        path = next((AGENT / "lib").rglob(f"{name}.sh"))
        assert len(path.read_text(encoding="utf-8").splitlines()) < 280, name
    assert len(main.splitlines()) <= 300
