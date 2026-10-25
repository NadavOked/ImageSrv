"""#1662 -- the local clone's total while it copies (``lc_run`` total, ``lc_tgt``
total, the percent and the time left, ``lc_part`` bytes) is the number the
source screen said before the start (#1443: "203.1 GB יועתקו"), not df's.

Before: ``lc_manifest`` read ``used_bytes`` from ``$LC/inv/<src>.used`` --
``df`` on a read-only mount -- and ``lc_run_total`` summed it. The screen said
partclone's "X יועתקו", and a minute later "Y מתוך Z" with another Z, for the
same copy. The counter (``bytes.raw``) counts what partclone reads, so the
denominator is partclone's: ``$LC/measure/<src>.copy.parts`` (lcsize.sh).

Never one of each (the issue): partclone's numbers for every partition, or --
not measured, another disk, another table (a shrunk layout) -- df's for every
partition, as before, and the log says which and why ("approximate"). An
identity check that cannot run is a "no", never a reuse (principle 5). A valid
measurement with a hole in it keeps the hole: null, never filled from df.

**Negative control:** with ``agent/lib/lcplan.sh`` and ``agent/lib/lcsize.sh``
back at feat/1443-exact-size (a4e1a19a), ``test_the_run_total_is_the_number_shown_before_the_start``
fails: the total is df's sum (0.5 GB + 212 GB), not partclone's (203.1 GB),
and the log has no line saying which number the run counts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_local_clone_agent import run

G = 1_000_000_000
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
#: ESP, MSR (no file system: copied whole), Windows, swap
PARTS = ("1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|U1|2048|204800\n"
         "2|E3C9E316-0B5C-4DB8-817D-F92DF00215AE|U2|206848|32768\n"
         f"3|{WIN}|U3|239616|998244352\n"
         "4|0657FD6D-A4AB-43C4-84E5-0933C84B4F4F|U4|998483968|2048000\n")
#: df on a read-only mount (the inventory)
DF = "1|500000000\n2|16777216\n3|212000000000\n4|0\n"
DF_TOTAL = 500000000 + 16777216 + 212000000000
#: partclone's used blocks (the copy) -- the issue's 203.1 GB on partition 3
PCL = {"vfat": 33554432, "ntfs": 49580892 * 4096}
PCL_TOTAL = 33554432 + 32768 * 512 + 49580892 * 4096        # MSR whole, swap 0

SETUP = (
    'mkdir -p "$LC/inv" "$LC/measure" "$LC/run" "$SYSROOT/sys/block/sdc"; echo 1000000000 > "$SYSROOT/sys/block/sdc/size"; '
    f"printf '%s' '{PARTS}' > \"$LC/inv/sdc.parts\"; printf '%s' '{DF}' > \"$LC/inv/sdc.used\"; "
    'cp "$LC/inv/sdc.parts" "$LC/run/parts.txt"; '
    "printf '1|EFI|0\\n2|MSR|0\\n3|Basic data|0\\n4|swap|0\\n' > \"$LC/run/entries\"; "
    '_fs_of() { case "$1" in *1) echo vfat ;; *2) echo unknown ;; *3) echo ntfs ;; *4) echo swap ;; esac; }; '
    '_uuid_of() { :; }; lc_role() { case "$2" in 3) echo windows ;; *) echo other ;; esac; }; '
    'sgdisk() { echo "Disk identifier (GUID): 11111111-2222-3333-4444-555555555555"; }; '
    'disk_serial() { echo "${SERIAL-S3}"; }; '
    f'_lc_pcl_measure() {{ case "$2" in vfat) echo {PCL["vfat"]} ;; ntfs) [ -z "${{PCL_FAIL-}}" ] || {{ echo "partclone failed"; return 1; }}; echo {PCL["ntfs"]} ;; esac; }}; '
    'LOG_FILE="$RUN_DIR/agent.log"; : > "$LOG_FILE"; log() { echo "$*" >> "$LOG_FILE"; }; '
)
#: before the start (the source screen's "X יועתקו"), then the run's total
SHOW = ('SERIAL=S3 lc_copy_size sdc; echo "shown=$(cut -d"|" -f1 "$LC/measure/sdc.copy")"; '
        'lc_manifest sdc "$LC/run/parts.txt"; echo "rc=$?"; '
        'echo "total=$(lc_run_total "$LC/run/manifest.json")"; '
        'jq -r \'.partitions[] | "part=\\(.index)|\\(.used_bytes)"\' "$LC/run/manifest.json"; '
        'grep "#1662" "$LOG_FILE" | sed "s/^/log=/"')


def _run(tmp_path: Path, between: str = "", **env: str) -> tuple[dict[str, str], list[str], list[str]]:
    out = run(tmp_path, 'export SYSROOT="$RUN_DIR/sys" DEVROOT="$RUN_DIR/dev" LC="$RUN_DIR/lc"; '
              + SETUP + SHOW.replace("lc_manifest", between + "lc_manifest", 1), **env)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    got = dict(l.split("=", 1) for l in lines if l.split("=", 1)[0] in ("shown", "rc", "total"))
    return got, [l[5:] for l in lines if l.startswith("part=")], [l[4:] for l in lines if l.startswith("log=")]


def test_the_run_total_is_the_number_shown_before_the_start(tmp_path):
    got, parts, logs = _run(tmp_path)
    assert got["shown"] == str(PCL_TOTAL), got
    assert got["rc"] == "0", got
    assert got["total"] == got["shown"], f"'{got['shown']} יועתקו' before the start, {got['total']} while it copies"
    # every partition from partclone -- lc_part's bytes too, the counter's own unit
    assert parts == [f"1|{PCL['vfat']}", f"2|{32768 * 512}", f"3|{PCL['ntfs']}", "4|0"], parts
    assert any("partclone" in l for l in logs), logs


@pytest.mark.parametrize("between,env,why", [
    # not measured: -1 (#1440) -- df's, as before, never a 0 or a partial sum
    ("", {"PCL_FAIL": "1"}, "not measured"),
    # no measurement file at all (never measured, or lc_measure removed it)
    ('rm "$LC/measure/sdc.copy"; ', {}, "not measured"),
    # what it was measured on cannot be read: not "the same", never a reuse
    ('rm "$LC/measure/sdc.copy.serial"; ', {}, "another disk"),
    ('rm "$LC/measure/sdc.copy.table"; ', {}, "another table"),
    # the comparison itself breaks (rc 2): a check that cannot run is a "no"
    ("cmp() { return 2; }; ", {}, "another table"),
    # another table: the shrunk layout the run copies is not the one measured
    ("sed -i 's/|998244352$/|500000000/' \"$LC/run/parts.txt\"; ", {}, "another table"),
    # another disk in the same port: the serial measured is not the serial now
    ("SERIAL=S9; ", {}, "another disk"),
    # a serial that cannot be read cannot say "the same disk"
    ("SERIAL=; ", {}, "another disk"),
])
def test_no_valid_measurement_keeps_df_for_every_partition_aloud(tmp_path, between, env, why):
    got, parts, logs = _run(tmp_path, between, **env)
    assert got["rc"] == "0", got
    assert got["total"] == str(DF_TOTAL), got
    # never one of each: df's for all four
    assert [p.split("|")[1] for p in parts] == ["500000000", "16777216", "212000000000", "0"], parts
    assert any("df" in l and why in l and "approximate" in l for l in logs), logs


@pytest.mark.parametrize("between", [
    # a row missing, a -1, a word, the whole per-partition file gone -- while
    # the source, the serial and the table are the ones measured
    "sed -i '/^3|/d' \"$LC/measure/sdc.copy.parts\"; ",
    "sed -i 's/^3|.*/3|-1/' \"$LC/measure/sdc.copy.parts\"; ",
    "sed -i 's/^3|.*/3|x/' \"$LC/measure/sdc.copy.parts\"; ",
    'rm "$LC/measure/sdc.copy.parts"; ',
])
def test_a_hole_in_the_measurement_stays_unknown_not_filled_from_df(tmp_path, between):
    got, parts, logs = _run(tmp_path, between)
    assert got["rc"] == "0", got
    # #1440: not measured is null -- never 0, never the size, never df's 212 GB
    assert got["total"] == "null", got
    assert "3|null" in parts, parts
    assert not any("212000000000" in p for p in parts), parts
    assert not any("df" in l for l in logs), logs
