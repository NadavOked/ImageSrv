"""X16 (Nadav 14/11) -- the "HDD support" switch, on the receiver. Fake sysfs,
no real block-device writes.

The switch is #3268's (``hdd_support`` in the hello answer, read from ``$RUN_DIR/response.json``);
X16 adds the policy. The hello carries ``rotational`` (0, 1, or null = not measured). A target HDD
with the switch off is refused, red, **before any write** (raid_wipe, the
write probe); with it on it meets its own learned expectation instead of the
SSD floor. A non-HDD is judged exactly as before; rotational not read = the
non-rotational policy, with a log line.

Negative control: ``agent/lib/hw/disklink.sh``, ``agent/lib/restore/writecheck.sh``
and ``agent/lib/restore/restore.sh`` back to the base -> no ``rotational`` in
the hello, an HDD target is written and judged against the 50 MB/s floor.
"""

from __future__ import annotations

import json
import shutil

import pytest
from agentlib import flat_lib, lib_path
from native import requires_native
from test_smart import BASH, posix, sh
from test_writecheck_speed_1366 import LIBS, verdict

pytestmark = requires_native(("bash", BASH), ("jq", shutil.which("jq")))

REFUSED = "HDD — תמיכה ב-HDD כבויה בהגדרות"


@pytest.mark.parametrize("value,expected", [("1", 1), ("0", 0), (None, None), ("bad", None), ("", None)])
def test_the_hello_reports_rotational_or_null(tmp_path, value, expected):
    queue = tmp_path / "sys/block/sda/queue"
    queue.mkdir(parents=True)
    if value is not None:
        (queue / "rotational").write_text(value + "\n", newline="\n")
    lib = posix(lib_path("disklink.sh"))
    r = sh(f'SYSROOT={posix(tmp_path)!r}; RUN_DIR={posix(tmp_path)!r}; . {lib!r}; '
           'printf "%s|" "$(disk_rotational sda)"; disk_link_json sda')
    assert r.returncode == 0, r.stderr
    alone, fragment = r.stdout.split("|", 1)
    assert alone == json.dumps(expected)
    assert json.loads('{"dev":"sda"' + fragment + '}')["rotational"] == expected


@pytest.mark.parametrize("support", [False, True])
@pytest.mark.parametrize("rotational", ["0", "1", None])
def test_the_receiver_matrix(tmp_path, support, rotational):
    """33 MB/s: under the SSD floor (50), over the HDD estimate (20)."""
    r = verdict(tmp_path, {"sda": 33}, rot={"sda": rotational},
                manifest={"hdd_floor_mbps": 20}, hello={"hdd_support": support})
    if rotational == "1" and not support:
        assert r["sda"] == ("failed", "slow", REFUSED), r
    elif rotational == "1":
        assert r["sda"] == ("writing", "ok", ""), r
        assert "no measured waves for HDD" in r["log"]
    else:                                                     # exactly as before X16
        assert r["sda"] == ("failed", "slow", "איטי — 33 MB/s, מתחת לסף 50 MB/s"), r
    assert ("rotational not read (no file) -- not measured" in r["log"]) == (rotational is None)


@pytest.mark.parametrize("hello", [None, "{}", '{"hdd_support": "yes"}', "{not json"],
                         ids=["no file", "no key", "not a boolean", "not json"])
def test_a_switch_that_did_not_read_is_off_with_a_log_line(tmp_path, hello):
    r = verdict(tmp_path, {"sda": None}, rot={"sda": "1"}, hello=hello)
    assert r["sda"][1:] == ("slow", REFUSED), r              # refused even with no speed measured
    assert "hdd_support unread in the hello answer -- off, the default" in r["log"]


def test_the_learned_floor_is_this_disks_serial_and_model(tmp_path):
    device = tmp_path / "sysroot/sys/block/sda/device"
    device.mkdir(parents=True)
    (device / "serial").write_text("WD-H1\n", newline="\n")
    (device / "model").write_text("WDC WD10EZEX\n", newline="\n")
    floors = {"WD-H1": {"WDC WD10EZEX": 40, "OTHER": 200}, "WD-H2": {"WDC WD10EZEX": 300}}
    r = verdict(tmp_path, {"sda": 33}, rot={"sda": "1"},
                manifest={"hdd_floor_mbps": 20, "hdd_floors": floors}, hello={"hdd_support": True})
    assert r["sda"] == ("failed", "slow", "איטי — 33 MB/s, מתחת לסף 40 MB/s (הציפייה שנלמדה ל-HDD הזה)"), r


def test_an_hdd_without_history_uses_the_estimate_and_says_so(tmp_path):
    r = verdict(tmp_path, {"sda": 15}, rot={"sda": "1"}, hello={"hdd_support": True})
    assert r["sda"] == ("failed", "slow", "איטי — 15 MB/s, מתחת לסף 20 MB/s (הערכת פתיחה ל-HDD, לא נמדדה)"), r
    assert "the starting estimate 20 MB/s" in r["log"]


def _round(tmp_path, support: bool) -> str:
    """The real write_check_round, every write in it a recorder."""
    (tmp_path / "sys/block/sda/queue").mkdir(parents=True)
    (tmp_path / "sys/block/sda/queue/rotational").write_text("1\n", newline="\n")
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    (tmp_path / "run/targets/sda/raid_wipe").write_text("md0\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    (tmp_path / "run/response.json").write_text(json.dumps({"hdd_support": support}))   # the hello answer: the switch
    prelude = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    r = sh(f'export SYSROOT={posix(tmp_path)!r} RUN_DIR={posix(tmp_path / "run")!r} '
           f'DEVROOT={posix(tmp_path / "dev")!r}; {prelude}'
           'disk_fits() { echo REACHED disk_fits; }; plan_gate() { echo REACHED plan_gate; }; '
           'raid_wipe() { echo REACHED raid_wipe; }; '
           f'write_check_round {posix(manifest)!r} sda; cat "$RUN_DIR/targets/sda/state"')
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_off_refuses_before_raid_wipe_and_the_write_probe(tmp_path):
    out = _round(tmp_path, support=False)
    assert "REACHED" not in out and out.strip().endswith("failed"), out


def test_on_the_same_disk_reaches_them(tmp_path):
    """The positive control of the one above: the gate, not the harness, stops it."""
    out = _round(tmp_path, support=True)
    assert "REACHED raid_wipe" in out, out


def test_the_single_restore_write_check_refuses_before_writing(tmp_path):
    """run_restore hands write_check its manifest ($5); the gate is its first step."""
    (tmp_path / "sysroot/sys/block/sda/queue").mkdir(parents=True)
    (tmp_path / "sysroot/sys/block/sda/queue/rotational").write_text("1\n", newline="\n")
    (tmp_path / "run/targets/sda").mkdir(parents=True)
    manifest = tmp_path / "m.json"
    manifest.write_text("{}")
    (tmp_path / "run/response.json").write_text('{"hdd_support": false}')
    prelude = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    r = sh(f'export SYSROOT={posix(tmp_path / "sysroot")!r} RUN_DIR={posix(tmp_path / "run")!r} '
           f'DEVROOT={posix(tmp_path / "dev")!r}; {prelude}'
           f'write_check sda {posix(manifest)!r}; echo "rc=$?"; cat "$RUN_DIR/targets/sda/write_check_state"')
    assert r.returncode == 0, r.stderr
    assert "rc=1" in r.stdout and r.stdout.strip().endswith("slow"), r.stdout   # not "unchecked: not a block device"
    rs = lib_path("restore.sh").read_text(encoding="utf-8")
    assert 'write_check "$2" "$5"' in rs
