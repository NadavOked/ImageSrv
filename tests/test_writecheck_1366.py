"""#1366 — the write check before the stream, on real block devices.

Loop devices and device-mapper targets, root through ``sudo -n``: the
Testrunner only; elsewhere the module is a declared skip (``native.py``).
The agent runs under ``busybox sh``, so ``dd``/``cmp`` are the busybox applets
the initramfs ships (busybox-static 1.37), not the host's coreutils.

The disks:
  good  -- a 256M sparse file on a loop device: takes the write;
  full  -- its backing file sits on a 6M tmpfs: the start fits, the middle fails;
  err   -- device-mapper: linear, an ``error`` segment over the middle, linear;
  zero  -- device-mapper ``zero``: takes every write and reads back zeros
           (the disk that lies about having written);
  unsel -- random bytes on a loop device, never selected: never written (#1275).

The checks read the disks back independently of the agent -- the backing
files' bytes and sha256 -- so the code under test cannot vouch for itself.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agent" / "lib"
SBIN = "/usr/sbin:/sbin"
MIB = 1048576
HEAD = "הדיסק לא מקבל כתיבה — "


def _sudo_ok() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def _tool(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name, path=SBIN)


pytestmark = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, _tool(t)) for t in ("losetup", "dmsetup", "busybox", "blockdev", "jq")],
    posix=True,
    why="loop and device-mapper devices as root: the Testrunner",
)

# expand: diskfit calls _expand_candidate since #1354 (2b195230), as imagectl-agent sources it.
LIBS = ("common", "jsonq", "waits", "sysinfo", "progress", "failmark", "verdict", "ntfsboot", "expand", "diskfit",
        "bootcadb", "restore", "drawers", "drawerfeed", "clonergui", "writecheck")

SETUP = r'''
set -eu
D=$1; ID=$2
truncate -s 256M "$D/good.img"; truncate -s 64M "$D/back.img"
head -c 67108864 /dev/urandom > "$D/unsel.img"
mkdir -p "$D/full"; mount -t tmpfs -o size=6m "wc$ID" "$D/full"; truncate -s 64M "$D/full/full.img"
for n in good unsel back; do echo "$n $(losetup -f --show "$D/$n.img")" >> "$D/devs"; done
echo "full $(losetup -f --show "$D/full/full.img")" >> "$D/devs"
bk=$(awk '$1 == "back" { print $2 }' "$D/devs")
printf '0 65536 linear %s 0\n65536 32768 error\n98304 32768 linear %s 98304\n' "$bk" "$bk" \
    | dmsetup create "wcerr$ID"
echo "err /dev/mapper/wcerr$ID" >> "$D/devs"
echo '0 131072 zero' | dmsetup create "wczero$ID"
echo "zero /dev/mapper/wczero$ID" >> "$D/devs"
# #1805 (CLR-02): 96MiB reported, 32MiB real -- three segments onto one backing
truncate -s 32M "$D/wrapback.img"; echo "wrapback $(losetup -f --show "$D/wrapback.img")" >> "$D/devs"
wb=$(awk '$1 == "wrapback" { print $2 }' "$D/devs")
printf '0 65536 linear %s 0\n65536 65536 linear %s 0\n131072 65536 linear %s 0\n' "$wb" "$wb" "$wb" \
    | dmsetup create "wcwrap$ID"
echo "wrap /dev/mapper/wcwrap$ID" >> "$D/devs"
'''

TEARDOWN = r'''
D=$1; ID=$2
dmsetup remove "wcerr$ID" 2>/dev/null; dmsetup remove "wczero$ID" 2>/dev/null; dmsetup remove "wcwrap$ID" 2>/dev/null
[ -f "$D/devs" ] && while read -r n d; do case "$d" in /dev/loop*) losetup -d "$d" ;; esac; done < "$D/devs"
umount "$D/full" 2>/dev/null
# #1534: the cleanup is proven, not assumed -- read back that nothing this fixture
# created is left: its two dm names (exact field, wcerr1x1 is a prefix of wcerr1x10),
# a loop backed by a file under $D, the tmpfs. A listing that could not run is a leftover.
for _try in 1 2 3 4 5 6 7 8 9 10; do
    left=""
    dl=$(dmsetup ls) || left="$left dmsetup-ls-failed"
    hit=$(printf '%s\n' "$dl" | awk -v a="wcerr$ID" -v b="wczero$ID" -v c="wcwrap$ID" '$1 == a || $1 == b || $1 == c { printf " dm:%s", $1 }')
    left="$left$hit"
    lo=$(losetup -a) || left="$left losetup-a-failed"
    case "$lo" in *"$D/"*) left="$left loop:$(printf '%s\n' "$lo" | grep -F "$D/" | cut -d: -f1 | tr '\n' ' ')" ;; esac
    grep -qF " $D/full " /proc/mounts; rc=$?
    [ "$rc" -eq 1 ] || left="$left mount:$D/full(rc=$rc)"
    [ -n "$left" ] || break
    sleep 0.5
    umount "$D/full" 2>/dev/null   # its loop may have been detached lazily, after the first try
done
rm -rf "$D/run" "$D/dev" "$D"/*.img
[ -z "$left" ] || { echo "teardown left behind:$left" >&2; exit 1; }
'''

DRIVER = r'''
set -u
F=$1; MODE=$2; shift 2
export RUN_DIR=$F/run LIB_DIR=%(lib)s IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff EFIVARS_ROOT=$F/no-efivars
rm -rf "$RUN_DIR"; mkdir -p "$RUN_DIR/targets"
# DEVROOT is the test's own directory (test_tests_never_touch_host_dev): links to
# the fixture's devices and nothing else, by their kernel names (loop7, dm-3).
export DEVROOT=$F/dev; rm -rf "$DEVROOT"; mkdir -p "$DEVROOT"; : > "$F/names"
while read -r n d; do k=$(basename "$(readlink -f "$d")"); ln -s "$d" "$DEVROOT/$k"; echo "$n $k" >> "$F/names"; done < "$F/devs"
ln -s "$F/devs" "$DEVROOT/plain"; echo "plain plain" >> "$F/names"   # a regular file, not a block device
%(sources)s
k() { awk -v n="$1" '$1 == n { print $2 }' "$F/names"; }
disk_serial() { echo "SER-$1"; }
disk_port() { awk -v k="$1" '$2 == k { print NR }' "$F/names"; }   # the port is the line number
case "${STUB:-}" in
    nodirect) dd() { if [ "${1:-}" = --help ]; then echo "Usage: dd [if=FILE] [of=FILE]"; return 0; fi; command dd "$@"; } ;;
    cmp2) cmp() { return 2; } ;;
    slow) dd() { if [ "${1:-}" = --help ]; then command dd --help; return; fi; sleep 4; } ;;
esac
show() { for n in "$@"; do t=$RUN_DIR/targets/$(k "$n")
    echo "T $n|$(cat "$t/state" 2>/dev/null)|$(cat "$t/error" 2>/dev/null)|$(cat "$t/write_check_state" 2>/dev/null)|$(cat "$t/write_check_reason" 2>/dev/null)"
done; }
case "$MODE" in
check)
    for n in "$@"; do target_init "$(k "$n")" 0; write_check "$(k "$n")"; echo "RC $n $?"; done
    show "$@"; build_progress ses_x "$MAC" > "$F/progress.json"
    ;;
room)   # $@ = the selected ports (line numbers); every fixture disk is a drawer
    printf '{"session":{"target_ports":[%%s]}}' "$(echo "$@" | tr ' ' ',')" > "$F/resp.json"; RESP=$F/resp.json
    list_drawers() { awk '$1 != "plain" { print $2 }' "$F/names"; }
    resolve_target_drawers; echo "SEL$_disks"
    for d in $_disks; do target_init "$d" 0; done
    apply_gpt() { echo "gpt $1" >> "$F/calls"; }
    expand_last() { return 0; }
    manifest_plan() { echo '1|guid|data|ntfs|2048|2048|p1.zst|sha|0|||'; }
    restore_partition_drawers() { shift 8; for d in "$@"; do
        [ "$(cat "$RUN_DIR/targets/$d/state")" = failed ] || { echo "fed $d" >> "$F/calls"; target_partition_done "$d"; }; done; }
    finish_grow() { target_set "$1" done; }
    readback_drawers() { :; }   # #1805: the read-back has its own tests (test_readback_agent_1805.py)
    : > "$F/calls"
    # shellcheck disable=SC2086
    run_restore_drawers multicast http://127.0.0.1:9 img "$F/m.json" $_disks; echo "RR $?"
    show good full err zero unsel
    ;;
station)
    # the real apply_gpt always sets PLAN_ERROR first (expand.sh); run_restore reads it (77557596)
    apply_gpt() { PLAN_ERROR=""; echo "gpt $1" >> "$F/calls"; return 1; }
    : > "$F/calls"; target_init "$(k "$1")" 0
    run_restore multicast "$(k "$1")" http://127.0.0.1:9 img "$F/m.json"; echo "RS $?"
    show "$1"
    ;;
esac
'''

MANIFEST = {"sector_size": 512, "partitions": [{"index": 1, "start_sector": 2048, "size_bytes": MIB}],
            "min_target_bytes": 4 * MIB}
_ids = itertools.count()


def _root(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo", "-n", "env", f"PATH={os.environ.get('PATH', '')}:{SBIN}", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL)


@pytest.fixture
def fx(tmp_path):
    d = tmp_path / "wc"
    d.mkdir()
    (d / "m.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    ident = f"{os.getpid()}x{next(_ids)}"
    (tmp_path / "setup.sh").write_text(SETUP, newline="\n")
    (tmp_path / "teardown.sh").write_text(TEARDOWN, newline="\n")
    try:
        up = _root("sh", str(tmp_path / "setup.sh"), str(d), ident)
        assert up.returncode == 0, up.stdout + up.stderr
        yield d
    finally:
        down = _root("sh", str(tmp_path / "teardown.sh"), str(d), ident, timeout=120)
        assert down.returncode == 0, down.stdout + down.stderr   # #1534: proven, not assumed


def _agent(fx: Path, mode: str, *args: str, env: tuple[str, ...] = ()) -> dict:
    """The driver under busybox sh, its output to a file: an orphan of a
    killed check must not hold a pipe this test waits on (CLAUDE.md)."""
    # Only the files that exist: the negative control takes writecheck.sh away, and
    # the tests must then fail on the behaviour, not on a `.` of a missing file.
    sources = "\n".join(f'[ ! -f "$LIB_DIR/{n}.sh" ] || . "$LIB_DIR/{n}.sh"' for n in LIBS)
    script = fx / "drive.sh"
    script.write_text(DRIVER % {"lib": LIB, "sources": sources}, newline="\n")
    out = fx / "out.txt"
    with out.open("w", encoding="utf-8") as fh:
        run = subprocess.run(["sudo", "-n", "env", f"PATH={os.environ.get('PATH', '')}:{SBIN}", *env,
                              "busybox", "sh", str(script), str(fx), mode, *args],
                             stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=300)
    raw = out.read_text(encoding="utf-8", errors="replace")
    assert run.returncode == 0, raw[-4000:]
    res: dict = {"raw": raw, "t": {}, "rc": {}}
    for line in raw.splitlines():
        if line.startswith("T "):
            name, state, error, wstate, wreason = line[2:].split("|", 4)
            res["t"][name] = {"state": state, "error": error, "wstate": wstate, "wreason": wreason}
        elif line.startswith("RC "):
            _r, name, rc = line.split(); res["rc"][name] = int(rc)
        elif line.startswith(("RR ", "RS ")):
            res[line[:2]] = int(line[3:])
        elif line.startswith("SEL"):
            res["sel"] = line[3:].split()
    return res


def _region(img: Path, mib: int, n: int = 4) -> bytes:
    with img.open("rb") as f:
        f.seek(mib * MIB)
        return f.read(n * MIB)


def _sha(img: Path) -> str:
    return hashlib.sha256(img.read_bytes()).hexdigest()


def test_a_good_disk_is_written_in_three_places_and_read_back(fx):
    # 16MiB a place: 48MiB with its fsync takes long enough for the 10ms clock (writecheck.sh)
    res = _agent(fx, "check", "good", env=("WRITECHECK_MIB=16",))
    assert res["rc"]["good"] == 0, res["raw"][-3000:]
    g = res["t"]["good"]
    assert g["wstate"] == "ok" and g["state"] == "waiting" and g["error"] == "", g
    # read back independently: the start, the middle and the end now hold the
    # patterns, three different ones, and nothing between them was touched.
    img = fx / "good.img"
    start, mid, end = _region(img, 0, 16), _region(img, 128, 16), _region(img, 240, 16)
    assert start.count(0) < MIB and mid.count(0) < MIB and end.count(0) < MIB
    assert len({start, mid, end}) == 3
    # #1805 (CLR-02): and a 4KiB random marker at every power-of-two MiB -- between
    # the places that is 16, 32 and 64 MiB; nothing else between them was touched.
    between = bytearray(_region(img, 16, 112))
    for mib in (16, 32, 64):
        off = (mib - 16) * MIB
        assert between[off:off + 4096].count(0) < 4096, mib
        between[off:off + 4096] = bytes(4096)
    assert bytes(between) == bytes(112 * MIB) and _region(img, 144, 96) == bytes(96 * MIB)
    entry = next(t for t in json.loads((fx / "progress.json").read_text(encoding="utf-8"))["targets"])
    assert entry["write_preflight"]["state"] == "ok", entry
    # the speed, from dd's own summary: a number, measured (5b) -- never a made-up 0
    assert isinstance(entry["write_preflight"]["mbps"], int) and entry["write_preflight"]["mbps"] > 0, entry


@pytest.mark.parametrize("name, place, detail", [
    ("err", "אמצע (32 MiB)", "dd:"),            # device-mapper error over the middle
    ("full", "אמצע (32 MiB)", "dd:"),           # the backing tmpfs fills after the start
    ("zero", "התחלה (0 MiB)", "מה שנקרא בחזרה שונה ממה שנכתב"),   # writes vanish
])
def test_a_disk_that_does_not_take_the_write_fails_by_name(fx, name, place, detail):
    res = _agent(fx, "check", name)
    t = res["t"][name]
    assert res["rc"][name] == 1, res["raw"][-3000:]
    assert t["state"] == "failed" and t["wstate"] == "failed", t
    assert t["error"].startswith(HEAD + place) and detail in t["error"], t
    entry = json.loads((fx / "progress.json").read_text(encoding="utf-8"))["targets"][0]
    assert entry["state"] == "failed" and entry["write_preflight"]["state"] == "failed"
    assert entry["error"] == t["error"]                       # the existing field the screens read
    assert entry["write_preflight"]["mbps"] is None           # no speed for a disk that did not take the write


def test_a_disk_that_wraps_its_addresses_fails_before_its_table(fx):
    """CLR-02 (#1805): 96MiB reported, 32MiB real -- every write lands at its
    address modulo 32MiB. The three places (0, 48, 92 MiB -> 0, 16, 28 MiB of
    what it has) do not overlap, so they alone read back fine -- the negative
    control: before #1805 this disk was `ok`. The marker at 32 MiB (and 64)
    lands on the one at 0, and that is read back as someone else's bytes."""
    res = _agent(fx, "check", "wrap")
    t = res["t"]["wrap"]
    assert res["rc"]["wrap"] == 1, res["raw"][-3000:]
    assert t["state"] == "failed" and t["wstate"] == "failed", t
    assert t["error"] == HEAD + "סמן ב-0 MiB נקרא בחזרה שונה — הדיסק מחזיר תוכן של מקום אחר (קיבולת מזויפת?)", t


def test_the_room_writes_only_the_selected_drawers(fx):
    """#1275, safety-critical: the unselected drawer is not written, not even
    the check's 4MB -- its bytes are the same before and after the round."""
    before = _sha(fx / "unsel.img")
    names = dict(line.split() for line in (fx / "devs").read_text().splitlines())
    order = list(names)                 # port = line number in the driver
    ports = [str(order.index(n) + 1) for n in ("good", "err")]
    # #1534: the speed floor is not what this test is about (test_writecheck_speed_1366.py
    # is). Its 50 MB/s, measured on a sparse file on a CI runner's disk, read 26 MB/s
    # once (run 38961297623, 3.12): good went red "slow" and RR was 1 -- the selection
    # was right. The verdict still runs here; only its floor is the test's own.
    res = _agent(fx, "room", *ports, env=("WRITECHECK_MIN_MBPS=1",))
    t = res["t"]
    # the selection is exactly the two drawers the fixture made, by their kernel names
    kernel = dict(line.split() for line in (fx / "names").read_text().splitlines())
    assert res["RR"] == 0 and res["sel"] == [kernel["good"], kernel["err"]], res["raw"][-3000:]
    assert t["good"]["state"] == "done" and t["good"]["wstate"] == "ok", t["good"]
    assert t["err"]["state"] == "failed" and t["err"]["error"].startswith(HEAD + "אמצע"), t["err"]
    calls = (fx / "calls").read_text().split("\n")
    assert [c.split()[0] for c in calls if c] == ["gpt", "fed"], calls      # only good: err failed before its table
    assert t["unsel"]["state"] == "" and t["unsel"]["wstate"] == "", t["unsel"]   # no target at all
    assert _sha(fx / "unsel.img") == before
    assert res["raw"].count("write check:") == 2


def test_the_station_fails_before_the_table(fx):
    floor = ("WRITECHECK_MIN_MBPS=1",)   # #1534: the runner's disk speed is not the subject
    bad = _agent(fx, "station", "err", env=floor)
    assert bad["RS"] == 1 and (fx / "calls").read_text() == "", bad["raw"][-3000:]
    assert bad["t"]["err"]["error"].startswith(HEAD + "אמצע")
    good = _agent(fx, "station", "good", env=floor)
    assert good["RS"] == 1 and (fx / "calls").read_text().startswith("gpt "), good["raw"][-3000:]
    assert good["t"]["good"]["wstate"] == "ok"
    assert not good["t"]["good"]["error"].startswith(HEAD)    # it failed later, at the (stubbed) table


@pytest.mark.parametrize("stub, disk, why", [
    ("nodirect", "good", "ה-dd כאן אינו מכיר direct"),
    ("cmp2", "good", "ההשוואה לא רצה (cmp rc=2)"),
    ("", "plain", "אינו התקן בלוק"),
])
def test_a_check_that_could_not_run_is_not_checked_and_the_round_goes_on(fx, stub, disk, why):
    before = _sha(fx / "good.img") if stub == "nodirect" else None
    res = _agent(fx, "check", disk, env=(f"STUB={stub}",))
    t = res["t"][disk]
    assert res["rc"][disk] == 0, res["raw"][-3000:]
    assert t["wstate"] == "unchecked" and t["state"] == "waiting" and t["error"] == "", t
    assert t["wreason"].startswith("לא נבדק") and why in t["wreason"], t
    if before is not None:
        assert _sha(fx / "good.img") == before        # nothing was written without the flags


def test_a_check_that_does_not_finish_in_time_fails(fx):
    res = _agent(fx, "check", "good", env=("STUB=slow", "WAIT_WRITECHECK_S=2"))
    t = res["t"]["good"]
    assert res["rc"]["good"] == 1 and t["state"] == "failed", res["raw"][-3000:]
    assert t["error"] == HEAD + "לא סיים לכתוב ולקרוא בחזרה תוך 2 שניות", t
