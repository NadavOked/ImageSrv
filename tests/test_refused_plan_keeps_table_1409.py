"""#1409 -- a refused plan never ends in a wiped partition table.

The Issue's last open case (comments of 15/10, 23/10 and 30/10): in a round,
``write_check_round`` ran the write check first, and ``_wc_body`` records
``write_started`` before its ``dd``. Then ``apply_gpt`` refused the plan
(``validate_restore_plan``), and ``fail_written_target`` -- seeing a write that had
started on the same serial -- collected ATA evidence against a healthy drive and
ran ``sgdisk --zap-all``, under the message "הדיסק לא שונה". The station did the
write check and the TRIM on the same refused plan.

Closed by 3dca834c (#2412): ``plan_gate`` runs the whole plan check before the
RAID wipe, the write check and the TRIM, and fails the target with
``target_set`` -- no evidence, no wipe. These tests guard that, through the real
``run_restore_drawers`` / ``run_restore``: the real ``write_check_round``,
``plan_gate``, ``_wc_body``, ``_wc_finish``, ``discard_before_restore``,
``apply_gpt``, ``fail_written_target`` and ``wipe_written_gpt``.

What is not real, and why:
  * sgdisk / blockdev / jq -- the stubs of test_restore_evidence (sgdisk records
    every call); blkdiscard and the ``dd`` onto the disk record into
    ``$RUN_DIR/destroy``; a ``dd`` onto the disk keeps its bytes in the box, so the
    write check reads them back and passes, as on a good drive.
  * ``write_check``'s outer probe -- ``[ -b ]``, ``dd --help``, the disk size -- is
    replaced by a wrapper that calls the real ``_wc_body`` and ``_wc_finish``: a
    block device cannot be made without root, and on Windows ``/dev/sda`` is the
    host's own disk. The probe only reads; every write of the check is real code.
  * the stream (``restore_partition_drawers`` / ``restore_partition``) -- the
    good-plan controls stop there, after the table.

Negative control: the production diff of 3dca834c reversed on this branch
(expand.sh, restore.sh, writecheck.sh) -- the commit message has the output.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_plan_before_destroy_2412 import BAD_GUID
from test_restore_evidence import PLAN, build_box, rc_of, restore_run

pytestmark = requires_native(("bash", BASH))

UNTOUCHED_HE = "הדיסק לא שונה"
SERIAL = "S-SDA-1409"

#: blkdiscard and the dd onto the disk record what they would have destroyed. A
#: dd from the disk returns what the dd onto it left; every other dd is real
#: (validate_restore_plan makes its sparse scratch file with one).
TOOLS = (
    'blkdiscard() { echo "blkdiscard $*" >> "$RUN_DIR/destroy"; }; '
    'dd() { local a i="" o="" b="" se=0 sk=0; for a in "$@"; do case "$a" in '
    'if=*) i=${a#if=} ;; of=*) o=${a#of=} ;; bs=*) b=${a#bs=} ;; seek=*) se=${a#seek=} ;; skip=*) sk=${a#skip=} ;; esac; done; '
    'case "$o" in "$DEVROOT"/sd*) echo "dd ${o##*/}" >> "$RUN_DIR/destroy"; cp "$i" "$RUN_DIR/disk.$b.$se"; return ;; esac; '
    'case "$i" in "$DEVROOT"/sd*) cp "$RUN_DIR/disk.$b.$sk" "$o"; return ;; esac; '
    'command dd "$@"; }; '
    # write_check without its read-only probe (see the module docstring): 1GiB, the real body
    'write_check() { local w="$RUN_DIR/wcw/$1" r; mkdir -p "$w"; _wc_mib=1024; '
    'r=$(_wc_body "$1" "$DEVROOT/$1" "$w" 0 512 1020); _wc_finish "$1" "${r%%|*}" "${r#*|}"; }; '
    'stage_at() { :; }; '   # the stage timeline (datareport.sh) has its own tests
)


def drawer_libs() -> str:
    lib = posix(AGENT) + "/lib/restore"
    return "".join(f". {lib}/{n}.sh; " for n in ("verdict", "discard", "writeback", "drawers"))


def box(tmp_path: Path, plan: list[str]) -> tuple[Path, Path, str]:
    """build_box, plus sda in sysfs: a serial (so a wipe is possible at all, #1427),
    an SSD that takes a TRIM, and the size -- written before the prelude binds it."""
    b, run, prelude = build_box(tmp_path, plan=plan)
    sda = b / "sys" / "block" / "sda"
    (sda / "device").mkdir(parents=True)
    (sda / "queue").mkdir()
    (sda / "device" / "serial").write_text(SERIAL + "\n", newline="\n")
    (sda / "size").write_text("976562500\n", newline="\n")
    (sda / "queue" / "rotational").write_text("0\n", newline="\n")
    (sda / "queue" / "discard_max_bytes").write_text("2147450880\n", newline="\n")
    return b, run, prelude + drawer_libs() + TOOLS


def destroyed(run: Path) -> list[str]:
    f = run / "destroy"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def zaps(b: Path) -> list[str]:
    f = b / "sgdisk.calls"
    calls = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    return [c for c in calls if c.startswith("--zap-all ")]


def target(run: Path, name: str) -> str:
    f = run / "targets" / "sda" / name
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def round_run(prelude: str) -> str:
    # The stream is not what is tested: a good plan stops at the first partition.
    return sh(prelude + 'restore_partition_drawers() { return 1; }; '
              'run_restore_drawers multicast http://s img m.json sda; echo "rc=$?"')


def test_a_round_whose_plan_is_refused_never_writes_trims_or_wipes_the_drawer(tmp_path):
    """The Issue's 15/10 run (VCLONE-1, sda): "malformed restore plan -- disk
    untouched", then "wiped primary and backup GPT after restore failure"."""
    b, run, prelude = box(tmp_path, [BAD_GUID, *PLAN[1:]])
    out = round_run(prelude)
    assert rc_of(out) == "rc=1", out
    assert zaps(b) == [], zaps(b)
    assert destroyed(run) == [], destroyed(run)
    assert target(run, "state") == "failed"
    assert UNTOUCHED_HE in target(run, "error"), target(run, "error")
    assert not (run / "targets/sda/write_started").exists()
    # no kernel evidence against a healthy drive: the server records no disk_failure
    assert not (run / "targets/sda/ata_log.json").exists()


def test_a_round_with_a_good_plan_reaches_every_destructive_step(tmp_path):
    """Positive control: the box is not too weak to reach the disk. The write check
    writes it, the TRIM runs, apply_gpt zaps it -- and a failure after the write
    wipes it (#1388) and collects the evidence, on this disk only."""
    b, run, prelude = box(tmp_path, PLAN)
    out = round_run(prelude)
    assert rc_of(out) == "rc=1", out
    d = destroyed(run)
    assert "dd sda" in d and "blkdiscard " + posix(b) + "/dev/sda" in d, d
    assert d.index("dd sda") < d.index("blkdiscard " + posix(b) + "/dev/sda"), d
    z = zaps(b)
    assert len(z) == 2 and all(c.endswith("/dev/sda") for c in z), z   # the table's, then the wipe's
    assert target(run, "write_started") == SERIAL
    assert (run / "targets/sda/ata_log.json").exists()


def test_a_station_whose_plan_is_refused_is_neither_written_nor_trimmed(tmp_path):
    """The same on the single station (restore.sh): the Issue's 30/10 comment."""
    b, run, prelude = box(tmp_path, [BAD_GUID, *PLAN[1:]])
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=1", out
    assert zaps(b) == [], zaps(b)
    assert destroyed(run) == [], destroyed(run)
    assert target(run, "state") == "failed"
    assert UNTOUCHED_HE in target(run, "error"), target(run, "error")
    assert not (run / "targets/sda/ata_log.json").exists()


def test_a_station_with_a_good_plan_still_checks_trims_and_writes_the_table(tmp_path):
    """Positive control for the station."""
    b, run, prelude = box(tmp_path, PLAN)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=0", out
    d = destroyed(run)
    assert d[0] == "dd sda" and d[-1] == "blkdiscard " + posix(b) + "/dev/sda", d
    assert [c.endswith("/dev/sda") for c in zaps(b)] == [True], zaps(b)
