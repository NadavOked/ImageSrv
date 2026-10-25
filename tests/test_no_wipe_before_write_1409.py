"""#1409 -- a target refused before anything was written keeps its partition table.

Since #1388 a failed target's GPT is wiped (``fail_written_target`` ->
``wipe_written_gpt``). The local clone (#1322) fails targets *before* writing --
"the target is not what was checked", target == source, "does not fit", a source
that changed -- and before b8e308db every one of those ran ``sgdisk --zap-all``
on the disk the agent had just declared it would not touch, including, by a name
swap, the source itself.

The Issue's definition of done, driven through the real ``lc_run`` (the harness of
``test_red_disk_never_target_1732``): serial mismatch, target == source, ``short``,
and ``_fail_alive`` of a source failure -> **zero** sgdisk calls; a target whose
write had started -> exactly one ``--zap-all``, on that disk only.

Negative control (mutation): ``wipe_written_gpt`` without its evidence line (the
``write_started`` serial check) -- the unconditional wipe of #1388 as it was.

Not covered here, and still open (see the Issue): a drawer whose plan is refused by
``validate_restore_plan`` *after* the write check (writecheck.sh) already wrote it.
"""

from __future__ import annotations

from native import requires_native
from test_local_clone_agent import BASH, run

pytestmark = requires_native(("bash", BASH))

ZAP_HE = "היעד אינו מה שנבדק — לא נכתב"

#: sda, sdb, sdc in the inventory with serials S-<dev>; the source is sdc. A
#: target listed in $SWAPPED answers another serial now (the drawer was replaced).
#: lc_source_parts is where the run stops: with $WRITE_FIRST it first records that
#: the write of that disk began -- what apply_gpt / the write check do.
HARNESS = (
    'LC="$RUN_DIR/lc"; mkdir -p "$LC/inv" "$LC/measure"; '
    'for d in sda sdb sdc; do echo "x|$d" > "$LC/inv/$d.line"; done; echo sdc > "$LC/source"; : > "$LC/measure/sdc"; '
    'disk_serial() { case " ${SWAPPED:-} " in *" $1 "*) printf NEW ;; *) printf "S-%s" "$1" ;; esac; }; '
    'lc_inv_field() { case "$2" in 4) printf "S-%s" "$1" ;; 12) echo "${SRC_OK:-1}" ;; *) echo ;; esac; }; '
    'lc_verdict_for() { echo "${VERDICT:-fits}||"; }; lc_measure() { :; }; smart_probe() { echo ok; }; '
    'sgdisk() { echo "$*" >> "$RUN_DIR/sgdisk.calls"; }; blockdev() { :; }; ata_capture() { :; }; '
    'lc_source_parts() { [ -z "${WRITE_FIRST:-}" ] || target_write_started "$WRITE_FIRST"; '
    'LC_ERROR=stopped-here; return 1; }; '
    'lc_end() { kill "$_lr_beat" 2>/dev/null; }; '
)


def lc(tmp_path, targets: str, **env):
    out = run(tmp_path, HARNESS + f'lc_run sdc "{targets}" 0; '
              'for d in sda sdb sdc; do [ -f "$RUN_DIR/targets/$d/state" ] && '
              'echo "$d=$(cat "$RUN_DIR/targets/$d/state")|$(cat "$RUN_DIR/targets/$d/error" 2>/dev/null)"; done; :', **env)
    assert out.returncode == 0, out.stderr
    calls_file = tmp_path / "run" / "sgdisk.calls"
    calls = calls_file.read_text(encoding="utf-8").splitlines() if calls_file.exists() else []
    states = dict(line.split("=", 1) for line in out.stdout.splitlines() if line[:3] in ("sda", "sdb", "sdc"))
    return states, calls, out


def test_a_target_whose_serial_changed_is_failed_and_its_table_kept(tmp_path):
    states, calls, out = lc(tmp_path, "sdb", SWAPPED="sdb")
    assert states["sdb"] == f"failed|{ZAP_HE}", (states, out.stderr)
    assert calls == [], calls


def test_the_source_named_as_a_target_is_failed_and_never_wiped(tmp_path):
    states, calls, out = lc(tmp_path, "sdc")
    assert states["sdc"] == f"failed|{ZAP_HE}", (states, out.stderr)
    assert calls == [], calls


def test_a_target_that_does_not_fit_is_failed_and_its_table_kept(tmp_path):
    states, calls, out = lc(tmp_path, "sdb", VERDICT="short")
    assert states["sdb"].startswith("failed|") and states["sdb"].endswith("— לא נכתב"), (states, out.stderr)
    assert calls == [], calls


def test_a_source_that_changed_fails_every_target_and_wipes_none(tmp_path):
    states, calls, out = lc(tmp_path, "sda sdb", SRC_OK="0")
    for d in ("sda", "sdb"):
        assert states[d].startswith("failed|דיסק המקור אינו מה שנבדק"), (states, out.stderr)
    assert calls == [], calls


def test_a_target_whose_write_started_is_wiped_once_and_only_it(tmp_path):
    """#1388 stays: a partly written disk must look broken. sda began writing,
    sdb did not; the same failure reaches both."""
    states, calls, out = lc(tmp_path, "sda sdb", WRITE_FIRST="sda")
    assert states["sda"] == "failed|stopped-here — לא נכתב דבר", (states, out.stderr)
    zaps = [c for c in calls if c.startswith("--zap-all ")]
    assert len(zaps) == 1 and zaps[0].endswith("/sda"), calls
