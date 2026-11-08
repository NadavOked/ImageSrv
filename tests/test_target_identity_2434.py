"""#2434 / #2429 -- the disk a job writes is the disk that was chosen, not its name.

A drawer is chosen by its slot (drawerselect.sh), a local-clone target by the
inventory line the person saw; from then on every stage wrote to ``sdX``. A drawer
pulled during the RAID question (up to 1800 s) and a disk pushed into another slot
get the same name -- and before this fix every stage wrote to it: the RAID wipe,
the write check, the TRIM, the table, each partition, the grow, the drivers, the
mark, and the GPT wipe after a failure (``target_write_started`` only *recorded*
whoever sat there, so the wipe followed the foreign disk too).

Now ``target_init`` binds serial|sectors|slot (``bound``, failmark.sh) and
``assert_target`` asks it again before each destructive step. A disk that is not
the one chosen fails by name and is locked (``foreign``): nothing after it --
``done``, evidence, a wipe -- changes it.

The identity is what sysfs gives: ``device/serial`` and ``size`` are real files in
the box; only ``disk_port`` (the ``ataN`` in a symlinked sysfs path, which Windows
cannot build) is a stub that reads ``port.<dev>`` from the box.

Negative control: every test here was run against the agent of a000bec7 (main,
v0.82.0) with these tests in place, and the write tests fail there -- the stub
tool is called on the swapped disk (the commit message has the output).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, REPO, posix

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "jsonq", "waits", "sysinfo", "diskinfo", "progress", "restore", "expand", "grow", "ntfsunhib",
        "expandshare", "writemark", "hostname", "hostname_linux", "writeback", "drawers", "verdict",
        "failmark", "ntfsboot", "postdeploy", "writecheck", "discard", "drawergate", "drawerselect", "cancel")

GONE_HE = "אינו הדיסק שנבחר לכתיבה"


def run(tmp_path: Path, script: str, *, sysroot_disks=("sdb",)) -> tuple[str, list[str], Path]:
    """Runs ``script`` with sdb bound as serial S2 in slot 2 (target_init), and
    ``swap <dev> <serial> <port>`` to put another disk under the same name. Every
    destructive tool is a function that only records its call."""
    box = tmp_path / "box"
    run_dir = box / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    for d in sysroot_disks:
        (box / "sys/block" / d / "device").mkdir(parents=True, exist_ok=True)
        (box / "sys/block" / d / "queue").mkdir(parents=True, exist_ok=True)
        (box / "sys/block" / d / "size").write_text("976773168\n", newline="\n")
        (box / "sys/block" / d / "queue/rotational").write_text("0\n", newline="\n")
        (box / "sys/block" / d / "queue/discard_max_bytes").write_text("2147450880\n", newline="\n")
    calls = box / "calls"
    pre = (f'export RUN_DIR={posix(run_dir)!r} SYSROOT={posix(box)!r} DEVROOT={posix(box)}/dev '
           f'LOG_FILE={posix(box)}/agent.log CALLS={posix(calls)!r} IMAGESRV_TEST=1; mkdir -p "$DEVROOT"; '
           + agent_sources(*LIBS)
           + 'disk_port() { cat "$SYSROOT/port.$1" 2>/dev/null; }; '
           + 'swap() { printf "%s\\n" "$2" > "$SYSROOT/sys/block/$1/device/serial"; '
             'printf "%s" "$3" > "$SYSROOT/port.$1"; [ -z "${4:-}" ] || echo "$4" > "$SYSROOT/sys/block/$1/size"; }; '
           + 'unserial() { rm -f "$SYSROOT/sys/block/$1/device/serial"; }; '
           # the destructive tools: each call is the evidence that the disk was touched
           + 'for t in blkdiscard sgdisk dd mkswap partclone.ntfs; do eval "$t() { echo \\"$t \\$*\\" >> \\"\\$CALLS\\"; }"; done; '
           + 'ata_capture() { :; }; stage_at() { :; }; '
           + 'swap sdb S2 2; target_init sdb 1000; '
           )
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + pre + script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, cwd=str(REPO), timeout=60)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return proc.stdout, lines, run_dir


def state(run_dir: Path, dev: str = "sdb") -> tuple[str, str]:
    t = run_dir / "targets" / dev
    err = t / "error"
    return ((t / "state").read_text(encoding="utf-8").strip(),
            err.read_text(encoding="utf-8").strip() if err.exists() else "")


#: Each destructive step of a restore / a round / a local clone, called on sdb
#: after the disk under that name changed. ``setup`` prepares what the step needs
#: to reach its write; the step must not call any tool, and must say it failed.
SITES = {
    "trim": ("", "discard_before_restore sdb"),
    "raid_wipe": ('echo md > "$RUN_DIR/targets/sdb/raid_wipe"; raid_member_probe() { echo md; }; ', "raid_wipe sdb"),
    "write_check": ('mkdir -p "$RUN_DIR/wc"; ', 'r=$(_wc_body sdb "$DEVROOT/sdb" "$RUN_DIR/wc" 0); echo "$r"; case "$r" in failed*) false ;; esac'),
    "table": ("", "target_write_started sdb"),
    "partition": ("node_is_block() { return 0; }; ", "restore_partition multicast s img sdb 2 swap '' '' ''"),
    "drawer_swap": ("cancel_pending() { :; }; ", "restore_partition_drawers multicast s img 3 swap '' '' '' sdb"),
    "ntfs_boot": ("ntfs_backup_boot() { echo \"ntfs_backup_boot $*\" >> \"$CALLS\"; }; ", "ntfs_boot_target sdb 2 ntfs"),
    "grow": ("boot_hidden_disk() { return 0; }; grow_expanded() { echo grow >> \"$CALLS\"; }; ", 'finish_grow sdb "" m.json'),
    "mark": ("_writemark_marked() { echo 1; }; ", "writemark_clear sdb"),
    "drivers": ("_drivers_fail() { :; }; manifest_plan() { echo \"manifest_plan $*\" >> \"$CALLS\"; }; ", "_stage_drivers sdb m.json"),
    "boot_entry": ("_stage_drivers() { :; }; ensure_boot_entry() { echo \"ensure_boot_entry $*\" >> \"$CALLS\"; }; ", "stage_drivers sdb m.json"),
    "hostname": ("json_get() { echo p; }; write_hostname() { echo \"write_hostname $*\" >> \"$CALLS\"; }; ", "name_this_machine sdb ses"),
}


@pytest.mark.parametrize("site", sorted(SITES))
def test_a_disk_swapped_under_the_chosen_name_is_not_written(tmp_path, site):
    """Scenario A of #2434: drawer 2 pulled, a disk in slot 3 (not chosen) got sdb."""
    setup, call = SITES[site]
    out, calls, run_dir = run(tmp_path, setup + "swap sdb S3 3; " + call + '; echo "rc=$?"')
    assert calls == [], (site, calls, out)
    st, err = state(run_dir)
    assert st == "failed" and GONE_HE in err, (site, st, err, out)
    assert (run_dir / "targets/sdb/foreign").exists(), site


def test_the_same_slot_with_another_disk_is_not_written_either(tmp_path):
    """Scenario B: the disk in the chosen slot was replaced between partitions."""
    out, calls, run_dir = run(tmp_path, "node_is_block() { return 0; }; swap sdb S9 2; "
                              "restore_partition multicast s img sdb 2 swap '' '' ''; echo \"rc=$?\"")
    assert out.strip().endswith("rc=1") and calls == [], (out, calls)


def test_a_pulled_disk_is_not_the_chosen_one(tmp_path):
    """Pulled: sysfs has no serial and no size under the name -- not "the same"."""
    out, calls, run_dir = run(tmp_path, 'rm -rf "$SYSROOT/sys/block/sdb"; '
                              'target_write_started sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert not (run_dir / "targets/sdb/write_started").exists()


@pytest.mark.parametrize("site", ["trim", "mark", "table"])
def test_the_disk_chosen_is_written_as_before(tmp_path, site):
    """Positive control: no swap, the same steps reach their tool."""
    setup, call = SITES[site]
    out, calls, run_dir = run(tmp_path, setup + call + '; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), (site, out)
    assert state(run_dir)[0] != "failed", (site, state(run_dir))
    if site == "table":
        assert (run_dir / "targets/sdb/write_started").read_text().strip() == "S2"
    else:
        assert calls and calls[0].split()[0] in ("blkdiscard", "sgdisk"), (site, calls)


def test_a_single_disk_without_a_serial_is_still_written(tmp_path):
    """A build/student station has one disk (VMs often without a serial): there is
    no other disk to mistake it for, so its size and slot are its identity."""
    out, calls, run_dir = run(tmp_path / "same", 'unserial sdb; target_init sdb 1000; '
                              'discard_before_restore sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=0") and len(calls) == 1 and calls[0].startswith("blkdiscard "), (out, calls)
    out, calls, _ = run(tmp_path / "bigger", 'unserial sdb; target_init sdb 1000; swap sdb "" 2 1953525168; unserial sdb; '
                        'discard_before_restore sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=2") and calls == [], (out, calls)


def test_a_target_never_chosen_in_this_job_is_not_written(tmp_path):
    """No `bound` = no target_init = never chosen: fail closed (principle 5)."""
    out, calls, run_dir = run(tmp_path, 'mkdir -p "$RUN_DIR/targets/sdc"; echo writing > "$RUN_DIR/targets/sdc/state"; '
                              'cp -r "$SYSROOT/sys/block/sdb" "$SYSROOT/sys/block/sdc"; '
                              'discard_before_restore sdc; echo "rc=$?"', sysroot_disks=("sdb",))
    assert out.strip().endswith("rc=2") and calls == [], (out, calls)
    assert state(run_dir, "sdc")[0] == "failed"


def test_a_foreign_target_stays_failed_by_its_reason(tmp_path):
    """Nothing after the refusal -- a stream verdict, the drivers' note -- turns it done."""
    _out, calls, run_dir = run(tmp_path, 'swap sdb S3 3; discard_before_restore sdb; target_set sdb done "הושלם"; '
                               'target_set sdb writing; :')
    st, err = state(run_dir)
    assert calls == [] and st == "failed" and GONE_HE in err, (calls, st, err)


def test_the_wipe_after_a_failure_never_follows_the_foreign_disk(tmp_path):
    """#2434: target_write_started recorded whoever sat there, and the GPT wipe of
    #1388 matched that serial -- the foreign disk's table was zapped."""
    out, calls, run_dir = run(tmp_path, 'swap sdb S3 3; target_write_started sdb; '
                              'fail_written_target sdb "partition 2 failed"; :')
    assert not [c for c in calls if "--zap-all" in c], (calls, out)
    assert (run_dir / "targets/sdb/write_started").exists() is False


def test_the_wipe_of_the_chosen_disk_still_happens(tmp_path):
    """#1388 stays: the disk chosen, partly written, is wiped once."""
    _out, calls, _ = run(tmp_path, 'target_write_started sdb; fail_written_target sdb "partition 2 failed"; :')
    assert [c for c in calls if c.startswith("sgdisk --zap-all")], calls


def test_writemark_release_done_counts_a_foreign_target_as_failed(tmp_path):
    """The station prints done after writemark_release_done (#1877: the mark comes
    off after the name and the drivers): a disk swapped by then is not cleared,
    and the call fails -- the station must not print done."""
    out, calls, run_dir = run(tmp_path, '_writemark_marked() { echo 1; }; target_set sdb done; swap sdb S3 3; '
                              'writemark_release_done sdb; echo "rc=$?"')
    assert out.strip().endswith("rc=1") and calls == [], (out, calls)
    assert state(run_dir)[0] == "failed"


# --- drawer selection (drawerselect.sh) ------------------------------------------

def test_a_drawer_without_a_serial_is_not_written(tmp_path):
    out, _calls, run_dir = run(tmp_path, 'unserial sdb; target_init sdb 1000; DRAWER_MAP=" sdb:2"; '
                               'echo "out=$(drawers_bound sdb)"', sysroot_disks=("sdb",))
    assert "out=\n" in out + "\n" or out.strip().endswith("out="), out
    st, err = state(run_dir)
    assert st == "failed" and "אין מספר סידורי" in err, (st, err)


def test_a_drawer_bound_in_another_slot_than_the_room_chose_is_not_written(tmp_path):
    """resolve_target_drawers mapped slot 2 to sdb; by target_init a disk in slot 3
    answered to sdb."""
    out, _calls, run_dir = run(tmp_path, 'swap sdb S3 3; target_init sdb 1000; DRAWER_MAP=" sdb:2"; '
                               'echo "out=$(drawers_bound sdb)"')
    assert out.strip().endswith("out="), out
    st, err = state(run_dir)
    assert st == "failed" and "בחריץ 3, לא בחריץ שנבחר (2)" in err, (st, err)


def test_a_drawer_with_no_identity_recorded_says_so(tmp_path):
    """No `bound` at all is not "no serial": the report says what is missing."""
    out, _calls, run_dir = run(tmp_path, 'rm -f "$RUN_DIR/targets/sdb/bound"; DRAWER_MAP=" sdb:2"; '
                               'echo "out=$(drawers_bound sdb)"')
    assert out.strip().endswith("out="), out
    st, err = state(run_dir)
    assert st == "failed" and "לא נרשמה בבחירה" in err, (st, err)


def test_a_drawer_in_its_slot_with_a_serial_is_written(tmp_path):
    out, _calls, run_dir = run(tmp_path, 'DRAWER_MAP=" sdb:2"; echo "out=$(drawers_bound sdb)"')
    assert out.strip().endswith("out=sdb"), out
    assert state(run_dir)[0] == "waiting"


def test_every_write_site_asks_assert_target():
    """Structural: the sites the behaviour tests above cannot reach without jq or a
    real fifo (expand_last's second table write, a drawer's partition fifo, the
    local clone's partitions and its copy's own writes) keep their check."""
    lib = REPO / "agent" / "lib"
    want = {
        "restore/expand.sh": ['assert_target "$1" || return 1   # #2434: the disk chosen, again'],
        "restore/drawers.sh": ['assert_target "$_d" || continue   # #1386; #2434: before its fifo',
                               'if assert_target "$_d" && make_swap'],
        "localclone/lccopy.sh": ['assert_target "$_ls_t" || continue', 'assert_target "$_lk_t" || continue',
                                 'assert_target "$_lf_t" || continue'],
    }
    for rel, needles in want.items():
        text = (lib / rel).read_text(encoding="utf-8")
        for n in needles:
            assert n in text, (rel, n)
