"""#1443 -- the local clone says how many bytes it will copy BEFORE it starts,
measured the way the copy counts them (Nadav, 17/10: "לפני העתקה אני רוצה
מספר מדויק, לפני שמתחילים").

Until now the source screen said "X להעתקה" from the inventory's df number on
a read-only mount (``_used_bytes``); partclone's own "Space in use" was read
only once the copy ran (progress.sh). ``lc_copy_size`` (lcsize.sh) runs the
copy's own partclone command with its stream into /dev/null, reads "Space in
use" x "Block size" from its log as soon as they are there, and kills it --
its own PID. A partition with no known file system is copied whole (dd), a
swap partition is not copied at all.

Four parts:

* the parser, on a real partclone log (v0.3.27, the lab -- the fixture of
  test_capture_source_progress.py) and on the issue's line from cloner 2;
* the measurement under busybox ash with stand-in partclone executables:
  the number, the source never a target (no -o/-O, stdout /dev/null), the
  partclone PID gone, a ceiling on the wait, and a failure said with why;
* the wiring: lc_measure writes it on every path, and the bridge carries it;
* the screen (cc + pango, the Testrunner): "203.1 GB יועתקו", or "לא נמדד —
  why" -- never the df number under a copy word.

**Negative control:** on main (712ebf70) there is no lcsize.sh -- the parser
and measurement tests fail on "lc_pcl_bytes: not found"/"lc_copy_size: not
found", lc_measure writes no ``.copy``, the bridge prints ``lc_src=sdc|done``,
and the screen draws "212 GB להעתקה" (the df number) where the test expects
"203.1 GB יועתקו".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_json_escape import BUSYBOX, busybox_env
from test_local_clone_agent import run
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent
LIB = flat_lib()
needs_busybox = requires_native(("busybox", BUSYBOX), posix=True,
                                why="the agent runs under busybox ash: its awk, its kill/wait")

#: a real partclone log (v0.3.27, the lab) -- test_capture_source_progress.PCL_LOG
PCL_LOG = """Partclone v0.3.27 http://partclone.org
Starting to clone device (/dev/sda3) to image (-)
Reading Super Block
File system:  NTFS
Device size:  53.7 GB = 13107200 Blocks
Space in use:   6.8 GB = 1665979 Blocks
Free Space:   46.9 GB = 11441221 Blocks
Block size:   4096 Byte
Elapsed: 00:00:03, Remaining: 00:00:22, Completed:  11.80%, Rate: 2.10GB/min
"""
PCL_BYTES = 1665979 * 4096

#: the issue's lines, cloner 2, 15/10, partclone.ntfs
CLONER2 = "Space in use: 203.1 GB = 49580892 Blocks\nBlock size:   4096 Byte\n"
CLONER2_BYTES = 49580892 * 4096                    # 203 083 333 632

FAT_LOG = ("Partclone v0.3.27 http://partclone.org\nStarting to clone device (/dev/sdc1) to image (-)\n"
           "File system:  FAT32\nDevice size:  104.9 MB = 25600 Blocks\nSpace in use:  33.6 MB = 8192 Blocks\n"
           "Free Space:   71.3 MB = 17408 Blocks\nBlock size:   4096 Byte\n")
FAT_BYTES = 8192 * 4096

WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
PARTS = ("1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|U1|2048|204800\n"
         "2|E3C9E316-0B5C-4DB8-817D-F92DF00215AE|U2|206848|32768\n"
         f"3|{WIN}|U3|239616|998244352\n"
         "4|0657FD6D-A4AB-43C4-84E5-0933C84B4F4F|U4|998483968|2048000\n")
MSR_BYTES = 32768 * 512                            # no file system: copied whole


# --- the parser ---------------------------------------------------------------------

def _ash(tmp_path: Path, body: str, stubs: dict[str, str] | None = None, **env: str) -> tuple[subprocess.CompletedProcess, float]:
    """``body`` under busybox ash with the agent's libs, stand-ins first on PATH."""
    run_dir = tmp_path / "run"; run_dir.mkdir(exist_ok=True)
    sdir = tmp_path / "stubs"; sdir.mkdir(exist_ok=True)
    for name, text in (stubs or {}).items():
        p = sdir / name; p.write_text(text, encoding="utf-8", newline="\n"); p.chmod(0o755)
    e = busybox_env(tmp_path)
    # no real partclone may answer for a missing stand-in: the stubs, busybox, the base system only
    e["PATH"] = os.pathsep.join([str(sdir), e["PATH"].split(os.pathsep)[0], "/usr/bin", "/bin"])
    e.update(RUN_DIR=str(run_dir), STUB_DIR=str(sdir), DEVROOT=str(tmp_path / "dev"), IMAGESRV_TEST="1", **env)
    (tmp_path / "dev").mkdir(exist_ok=True)
    pre = "".join(f'. "{LIB}/{n}.sh"\n' for n in ("common", "waits", "restore", "manifest", "lcinv", "lcsize"))
    script = tmp_path / "t.sh"
    script.write_text(pre + 'LC="$RUN_DIR/lc"; mkdir -p "$LC/inv" "$LC/measure"\n' + body, encoding="utf-8", newline="\n")
    t0 = time.monotonic()
    out = subprocess.run([BUSYBOX, "ash", str(script)], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, stdin=subprocess.DEVNULL, env=e, cwd=str(REPO))
    return out, time.monotonic() - t0


@needs_busybox
@pytest.mark.parametrize("log,want", [
    (PCL_LOG, PCL_BYTES),                                            # a real log, the lab
    (CLONER2, CLONER2_BYTES),                                        # the issue's numbers, cloner 2
    (PCL_LOG.replace("\n", "\r"), PCL_BYTES),                        # stderr's \r form
    ("Space in use:    0.0 GB = 0 Blocks\nBlock size:   4096 Byte\n", 0),   # measured 0 is 0
])
def test_space_in_use_times_block_size(tmp_path, log, want):
    (tmp_path / "pcl.log").write_text(log, encoding="utf-8", newline="")
    out, _ = _ash(tmp_path, f'lc_pcl_bytes "{posix(tmp_path / "pcl.log")}"; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [str(want), "rc=0"], out.stdout


@needs_busybox
@pytest.mark.parametrize("log", [
    PCL_LOG.replace("Block size:   4096 Byte\n", ""),               # no block size: the product is not known
    PCL_LOG.replace("Space in use:   6.8 GB = 1665979 Blocks\n", ""),   # the device size is NOT the used size
    "",
])
def test_a_missing_line_is_not_measured_never_zero(tmp_path, log):
    (tmp_path / "pcl.log").write_text(log, encoding="utf-8", newline="")
    out, _ = _ash(tmp_path, f'lc_pcl_bytes "{posix(tmp_path / "pcl.log")}"; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["rc=1"], out.stdout


# --- the measurement ----------------------------------------------------------------

def _stub(lines: str = "", then: str = "exec sleep 30") -> str:
    """A partclone stand-in: records its PID, its arguments and where its
    stdin/stdout go, writes ``lines`` to its -L log, then ``then`` -- by
    default it goes on "reading" for 30 s, as the real one goes on copying."""
    return ('#!/bin/sh\n'
            'n=$(basename "$0"); echo "$$" > "$STUB_DIR/pid.$n"; echo "$n $*" >> "$STUB_DIR/calls.log"\n'
            'echo "$n 0=$(readlink /proc/$$/fd/0) 1=$(readlink /proc/$$/fd/1)" >> "$STUB_DIR/fds.log"\n'
            'log=""; prev=""; for a in "$@"; do [ "$prev" = -L ] && log=$a; prev=$a; done\n'
            f"printf '%s' '{lines}' > \"$log\"\n" + then + "\n")


FS_OF = '_fs_of() { case "$1" in *1) echo vfat ;; *2) echo unknown ;; *3) echo ntfs ;; *4) echo swap ;; esac; }\n'
SHOW = ('lc_copy_size sdc; echo "rc=$?"; echo "copy=$(cat "$LC/measure/sdc.copy")"; '
        'echo "parts=$(tr "\\n" " " < "$LC/measure/sdc.copy.parts")"\n')


def _measure(tmp_path, ntfs: str | None, fat: str | None = None, **env):
    (tmp_path / "parts").write_text(PARTS, encoding="utf-8", newline="\n")
    stubs = {}
    if ntfs is not None: stubs["partclone.ntfs"] = ntfs
    if fat is not None: stubs["partclone.fat"] = fat
    body = FS_OF + f'cp "{posix(tmp_path / "parts")}" "$LC/inv/sdc.parts"\n' + SHOW
    out, took = _ash(tmp_path, body, stubs, **env)
    assert out.returncode == 0, out.stderr
    got = dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l and not l.startswith("imagesrv"))
    calls = (tmp_path / "stubs" / "calls.log")
    return got, (calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []), took


def _alive(tmp_path: Path, name: str) -> bool:
    pid = (tmp_path / "stubs" / f"pid.{name}").read_text().strip()
    return Path(f"/proc/{pid}").exists()


@needs_busybox
def test_the_copy_size_is_partclones_used_blocks_and_the_reader_is_killed(tmp_path):
    got, calls, took = _measure(tmp_path, ntfs=_stub(PCL_LOG), fat=_stub(FAT_LOG, "exit 0"))
    assert got["copy"] == f"{FAT_BYTES + MSR_BYTES + PCL_BYTES}|", got
    assert got["parts"].split() == [f"1|{FAT_BYTES}", f"2|{MSR_BYTES}", f"3|{PCL_BYTES}", "4|0"], got
    assert took < 15, f"waited for the 30 s 'copy' instead of killing it at the info lines: {took:.1f}s"
    assert not _alive(tmp_path, "partclone.ntfs"), "partclone was left running"
    # MSR (no file system) and swap never reach partclone
    assert [c.split()[0] for c in calls] == ["partclone.fat", "partclone.ntfs"], calls


@needs_busybox
def test_the_source_is_only_ever_read(tmp_path):
    """The copy's own command, with no target: clone mode, the partition after
    -s only, no -o/-O, stdout /dev/null, stdin /dev/null (not the table it loops on)."""
    dev = tmp_path / "dev"; dev.mkdir()
    for i in (1, 2, 3, 4):
        (dev / f"sdc{i}").write_bytes(bytes([i]) * 4096)
    before = {p.name: p.read_bytes() for p in dev.iterdir()}
    got, calls, _ = _measure(tmp_path, ntfs=_stub(PCL_LOG), fat=_stub(FAT_LOG, "exit 0"))
    assert got["rc"] == "0", got
    ntfs = next(c.split()[1:] for c in calls if c.startswith("partclone.ntfs "))
    assert ntfs[:6] == ["-c", "-I", "-a0", "-z", "524288", "-s"], ntfs           # lc_stream's flags
    assert ntfs[6] == f"{posix(dev)}/sdc3" and ntfs[7] == "-L", ntfs
    assert ntfs[8].startswith(posix(tmp_path / "run")), ntfs                     # the log: RAM, not a disk
    for c in calls:
        assert not {"-o", "-O", "-r", "-D", "--overwrite", "--output"} & set(c.split()), c
    fds = (tmp_path / "stubs" / "fds.log").read_text(encoding="utf-8").splitlines()
    assert fds and all(l.endswith("0=/dev/null 1=/dev/null") for l in fds), fds
    assert {p.name: p.read_bytes() for p in dev.iterdir()} == before


@needs_busybox
def test_a_partclone_that_says_nothing_is_cut_at_the_ceiling_and_said(tmp_path):
    got, _, took = _measure(tmp_path, ntfs=_stub(""), fat=_stub(FAT_LOG, "exit 0"), LC_SIZE_MAX_S="1")
    assert got["copy"] == "-1|מחיצה 3: partclone לא דיווח תוך 1 שניות", got
    assert got["parts"].split()[2] == "3|-1" and got["parts"].split()[0] == f"1|{FAT_BYTES}", got
    assert took < 15, f"the ceiling did not hold: {took:.1f}s"
    assert not _alive(tmp_path, "partclone.ntfs"), "partclone was left running past its ceiling"


@needs_busybox
@pytest.mark.parametrize("ntfs,why", [
    (_stub("", 'echo "ntfs_mount failed" >&2; exit 1'), "מחיצה 3: partclone הסתיים בלי לדווח כמה בשימוש (rc=1)"),
    (None, "מחיצה 3: partclone.ntfs חסר"),
])
def test_a_failure_is_not_measured_with_why(tmp_path, ntfs, why):
    if ntfs is None and shutil.which("partclone.ntfs", path="/usr/bin:/bin"):
        pytest.skip("a real partclone.ntfs in /usr/bin -- 'missing' cannot be staged here")
    got, _, _ = _measure(tmp_path, ntfs=ntfs, fat=_stub(FAT_LOG, "exit 0"))
    assert got["copy"] == f"-1|{why}", got


@needs_busybox
def test_no_table_is_not_measured(tmp_path):
    out, _ = _ash(tmp_path, 'lc_copy_size sdc; cat "$LC/measure/sdc.copy"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines()[-1] == "-1|לא הצלחנו לקרוא את טבלת המחיצות של המקור", out.stdout


# --- the wiring -----------------------------------------------------------------------

WIRE = (FS_OF.replace("*1) echo vfat", "*1) echo unknown") +
        'lc_inv_field() { case "$2" in 11) echo 100000000 ;; 5) echo 500000000000 ;; esac; }; list_drawers() { echo sda; echo sdc; }; '
        'mkdir -p "$LC/inv" "$LC/measure"; printf "1|G|U1|2048|204800\\n4|G|U4|206848|2048\\n" > "$LC/inv/sdc.parts"; ')


def test_lc_measure_writes_the_copy_size_on_every_path(tmp_path):
    """No drawer is smaller ("none" returns early) and the fit failing (rc 1)
    -- the copy's size is measured either way, and a stale one is gone while
    it is measured."""
    out = run(tmp_path, WIRE + 'echo "999|" > "$LC/measure/sdc.copy"; '
              'lc_copy_size() { echo "seen=$(cat "$LC/measure/sdc.copy" 2>/dev/null)"; echo "1|" > "$LC/measure/sdc.copy"; }; '
              'lc_measure sdc; echo "rc=$?"; echo "m=$(cat "$LC/measure/sdc")"; echo "c=$(cat "$LC/measure/sdc.copy")"; '
              '_lc_measure_fit() { return 1; }; lc_measure sdc; echo "rc=$?"; echo "c=$(cat "$LC/measure/sdc.copy")"')
    assert out.returncode == 0, out.stderr
    lines = [l for l in out.stdout.splitlines() if not l.startswith("imagesrv")]
    assert lines == ["seen=", "rc=0", "m=none||", "c=1|", "seen=", "rc=1", "c=1|"], lines


def test_lc_measure_runs_the_real_measurement(tmp_path):
    out = run(tmp_path, WIRE + 'lc_measure sdc; echo "c=$(cat "$LC/measure/sdc.copy")"')
    assert out.returncode == 0, out.stderr
    assert f"c={204800 * 512}|" in out.stdout, out.stdout          # unknown fs: whole; swap: 0


@pytest.mark.parametrize("copy,want", [(f"{CLONER2_BYTES}|", f"lc_src=sdc|done|{CLONER2_BYTES}|"),
                                       ("-1|מחיצה 3: partclone חסר", "lc_src=sdc|done|-1|מחיצה 3: partclone חסר"),
                                       (None, "lc_src=sdc|done||")])
def test_the_bridge_carries_the_copy_size(tmp_path, copy, want):
    put = f'printf "%s\\n" {copy!r} > "$LC/measure/sdc.copy"; ' if copy else ""
    out = run(tmp_path, 'export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC/inv" "$LC/measure"; echo 0 > "$RUN_DIR/min_free_gb"; '
              'echo local > "$LC/screen"; echo done > "$LC/inv.state"; echo idle > "$LC/phase"; '
              'echo "3|sdc|C|s3|480000000000|ok|ok|Windows|4|212000000000|480000000000|1|" > "$LC/inv/sdc.line"; '
              'echo sdc > "$LC/source"; echo "done|200000000000|" > "$LC/measure/sdc"; ' + put +
              'lc_gui_state; cat "$GUI_DIR/state"')
    assert out.returncode == 0, out.stderr
    assert want in out.stdout.splitlines(), out.stdout


# --- the screen -----------------------------------------------------------------------

from test_lc_progress_1437_1440 import driver, needs_cc, screens  # noqa: E402,F401 -- the text driver (a fixture)

DISKS = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
         "lc_disk=1|sda|Samsung|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
         "lc_disk=3|sdc|Crucial|S3|480103981056|ok|ok|Windows|4|212400000000|480103981056|1|\n"
         "lc_fit=sda|fits|480103981056|\n")
FSI, PDI, RLM = "⁨", "⁩", "‏"


@needs_cc
def test_the_source_screen_says_the_measured_number(driver, tmp_path):  # noqa: F811
    ok, failed, waiting, old = screens(
        driver, tmp_path,
        DISKS + f"lc_src=sdc|none|{CLONER2_BYTES}|\n",
        DISKS + "lc_src=sdc|none|-1|מחיצה 3: partclone לא דיווח תוך 120 שניות\n",
        DISKS + "lc_src=sdc|none||\n",
        DISKS + "lc_src=sdc|none\n")                                    # an agent without #1443
    assert f"{RLM}{FSI}203.1 GB{PDI} יועתקו" in ok, "\n".join(ok)
    assert "כמה יועתק: לא נמדד — מחיצה 3: partclone לא דיווח תוך 120 שניות" in failed, "\n".join(failed)
    assert "מודד כמה יועתק…" in waiting and "מודד כמה יועתק…" in old, (waiting, old)
    for drawn in (ok, failed, waiting, old):                           # the df number never says "to copy"
        assert not any("להעתקה" in s for s in drawn), drawn
        assert not any("212" in s and "יועתקו" in s for s in drawn), drawn
