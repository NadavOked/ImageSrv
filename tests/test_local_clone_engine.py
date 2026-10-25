"""#1322 — the local clone engine on real block devices: sparse image files on
loop devices (``localclone-fixture.sh``), root through ``sudo -n``. The
Testrunner only; elsewhere the module is a declared skip (``native.py``).

The source is a Windows-like disk with a Linux root last: ESP (FAT32), MSR
(no filesystem -- copied whole), Windows (NTFS, shrinkable), Recovery (NTFS,
attributes), root (ext4, the one that grows). The checks read the targets
back independently of the engine -- ``sgdisk`` and the files' own sha256 --
so a wrong engine cannot vouch for itself:

* identical: the disk GUID, every partition's type, unique GUID, name,
  attributes, start and size; the files; the raw MSR bytes;
* a larger target: the last system partition grows to the end, the rest is
  identical;
* a smaller target: refused when the data does not fit; measured with no
  server (#1478) and refused when the confirm did not present the shrink; when
  it did, it fits through a shrunk RAM view of the source (#1466), and every
  byte of the source is the same afterwards;
* a target whose writes fail fails alone, visibly, and the others finish;
* a stop request ends the copy: the targets fail, the source is untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agent" / "lib"
FIXTURE = REPO / "tests" / "localclone-fixture.sh"
SBIN = "/usr/sbin:/sbin"
#: #1594: the RAM view loads `brd`, a module of the whole machine: a second suite
#: doing the same finds it "already loaded, not by the clone" and refuses (as it
#: must). The Testrunner runs several suites at once, so the engine runs take turns.
ENGINE_LOCK = "/tmp/imagectl-lc-engine.lock"


def _sudo_ok() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def _tool(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name, path=SBIN)


pytestmark = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, _tool(t)) for t in ("losetup", "sgdisk", "partclone.ntfs", "partclone.fat", "partclone.ext4",
                              "partclone.dd", "ntfsresize", "mkntfs", "mkfs.vfat", "mkfs.ext4", "ntfs-3g",
                              "pv", "gcc", "jq", "curl", "flock")],
    posix=True,
    why="loop devices and partclone as root: the Testrunner",
)

LIBS = ("common", "jsonq", "waits", "sysinfo", "progress", "restore", "diskfit", "expand", "grow", "ntfsunhib", "expandshare", "hostname", "hostname_linux", "writemark", "drawers",
        "verdict", "failmark", "ntfsboot", "manifest", "hibernation", "shrinkplan", "shrink", "shrinkmem", "smart",
        "lcinv", "lcsize", "lcplan", "lcsnap", "lctune", "lccopy", "lcraw", "localclone", "askgui", "readback", "readbacklc")

DRIVER = r'''
set -u
F=$1; TARGETS=$2; SHRINK=$3
export RUN_DIR=$F/run LIB_DIR=%(lib)s IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff
rm -rf "$RUN_DIR"; mkdir -p "$RUN_DIR"
# #1594: a RAM view needs `brd` free. A suite that does not take the engine lock (an
# older branch) holds it for a minute or two at a time; the engine refuses a brd it did
# not load, rightly, so the copy that needs a view waits for it to go -- up to 6 minutes,
# after which what the engine says is the true state of the machine.
[ "$SHRINK" != 1 ] || { i=0; while [ -e /sys/module/brd ] && [ $i -lt 180 ]; do sleep 2; i=$((i + 1)); done; }
# #1354: the fixture's disks are MiB-sized, so no target could keep 20 GB free.
# 0 = the console's "rule off" -- the copy itself is what this file checks;
# the rule has its own tests (test_min_free_1354.py, lc_verdict_for).
echo 0 > "$RUN_DIR/min_free_gb"
# DEVROOT is the test's own directory (test_tests_never_touch_host_dev): links
# to the fixture's loop devices and their partitions, and nothing else -- the
# engine cannot reach a disk of the host.
export DEVROOT=$F/dev; mkdir -p "$DEVROOT"
for d in $(awk '{ print $2 }' "$F/devs"); do n=${d#/dev/}; ln -sf "$d" "$DEVROOT/$n"
  for p in 1 2 3 4 5 6 7 8 9; do ln -sf "${d}p$p" "$DEVROOT/${n}p$p"; done; done
# #1934: the initramfs has no udev (lcsnap.sh); a test host does, and its watch
# (60-block.rules, loop*) re-reads a disk's partition table whenever a writer closes
# the whole disk -- readback's `dd conv=fsync` -- so the partition nodes vanish for a
# moment under `blockdev --flushbufs` and the target fails "ריקון המטמון ... נכשל".
# Measured 21/10 on the testrunner: 23 and 11 of 200 failed without a lock, 0 of 200
# with one. udev re-reads only under flock(LOCK_EX|LOCK_NB): a shared lock on every
# fixture disk is its own "leave it alone" -- the host is the initramfs again.
# The locks live in a small process of their own, never in this shell: fds opened
# here are inherited by everything the engine starts, and a child that outlives a
# reboot-cut run (`kill -9 0` takes one process group) held the "full" disk open
# after teardown (a `sleep`, measured 21/10). It leaves when the driver does; its
# own sleeps get the lock fds closed.
_drv=$$; rm -f "$F/locked"
( fd=3; for d in $(awk '{ print $2 }' "$F/devs"); do
    [ $fd -le 9 ] || { echo "more fixture disks than fds 3-9" >&2; exit 3; }
    eval "exec $fd<\"\$d\"" && flock -s $fd || { echo "no shared lock on $d" >&2; exit 3; }; fd=$((fd + 1)); done
  : > "$F/locked"
  while kill -0 "$_drv" 2>/dev/null; do sleep 1 3<&- 4<&- 5<&- 6<&- 7<&- 8<&- 9<&- >/dev/null 2>&1; done ) </dev/null >/dev/null &
_locker=$!; trap 'kill "$_locker" 2>/dev/null' EXIT
i=0; until [ -e "$F/locked" ]; do
  kill -0 "$_locker" 2>/dev/null && [ $i -lt 100 ] || { echo "the fixture disks were not locked"; exit 3; }
  i=$((i + 1)); sleep 0.1; done
# #1466: the RAM view's own nodes (device-mapper and brd) -- RAM, not a disk of the host
ln -sfn /dev/mapper "$DEVROOT/mapper"; for i in 0 1 2 3; do ln -sf "/dev/ram$i" "$DEVROOT/ram$i"; done
%(sources)s
dev() { awk -v n="$1" '$1 == n { sub("/dev/", "", $2); print $2 }' "$F/devs"; }
SRC=$(dev src)
disk_serial() { echo "SER-$1"; }                 # loop devices have none; the shrink record needs one
list_drawers() { echo "$SRC"; for n in same big small tiny full; do dev "$n"; done; }
# #2017: a test's own setup after the links and the libraries (a bad partition, a GUI's answer)
[ -z "${HOOK:-}" ] || . "$HOOK"
mkdir -p "$LC"
lc_inventory
# #1798: what the inventory says of each disk -- the line and the incomplete-write mark
for n in $(awk '{ print $1 }' "$F/devs"); do d=$(dev "$n"); [ -f "$LC/inv/$d.line" ] || continue
  echo "inv $n|$(cat "$LC/inv/$d.line")|$(cat "$LC/inv/$d.mark" 2>/dev/null)"; done
[ -z "${INV_ONLY:-}" ] || exit 0
lc_measure "$SRC"
for n in $TARGETS; do echo "verdict $n $(lc_verdict_for "$SRC" "$(dev "$n")")"; done
T=""; for n in $TARGETS; do T="$T $(dev "$n")"; done
# המשגיח: תקרה (600 x 0.2s) ונהרג אחרי lc_run — lc_run שנכשל לפני "copy" השאיר
# אותו חי, מחזיק את הצינור, והטסט נתקע עד timeout=1500 (15/10, אחרי #1433)
_stopper=""
[ -z "${STOP_AT:-}" ] || { ( i=0; while [ $i -lt 600 ]; do [ "$(cat "$LC/run/cur" 2>/dev/null)" = "$STOP_AT|copy" ] && { touch "$LC/stop.req"; exit 0; }; i=$((i + 1)); sleep 0.2; done ) & _stopper=$!; }
# #1798: `reboot -f` -- the whole process group (setsid: the driver's own) dies with
# no trap, no cleanup and no report, once partition KILL_AT's copy has begun.
# Only as a session leader: `kill 0` must never reach the process that runs the test.
[ -z "${KILL_AT:-}" ] || [ "$(awk '{ print $6 }' /proc/$$/stat)" = "$$" ] || { echo "KILL_AT without setsid"; exit 3; }
[ -z "${KILL_AT:-}" ] || ( i=0; while [ $i -lt 600 ]; do c=$(cat "$LC/run/cur" 2>/dev/null); [ "${c%%%%|*}" -ge "$KILL_AT" ] 2>/dev/null && kill -9 0; i=$((i + 1)); sleep 0.2; done ) &
lc_run "$SRC" "$T" "$SHRINK"
[ -z "$_stopper" ] || kill "$_stopper" 2>/dev/null || :
echo "phase $(cat "$LC/phase")"
for n in $TARGETS; do d=$(dev "$n"); t=$RUN_DIR/targets/$d
  echo "target $n|$(cat "$t/state")|$(cat "$t/error" 2>/dev/null | tr '\n' ' ')|$(cat "$t/lc_ok" 2>/dev/null | tr '\n' ' ')|$(cat "$t/lc_table" 2>/dev/null)"
  echo "note $n|$(cat "$t/ntfs_note" 2>/dev/null)"; done   # #2009
echo "srcntfs $(tr '\n' ' ' < "$LC/run/src.ntfs" 2>/dev/null)"
echo "hiberlog $(awk '/source [^ ]+ partition 3: Windows is hibernated \(Fast Startup\)/ { n++ } END { print n + 0 }' "$LOG_FILE" 2>/dev/null)"
echo "restore $(cat "$LC/run/source_restore" 2>/dev/null)"
echo "report $(cat "$LC/run/report" 2>/dev/null)"
# #1594: what the machine holds of a RAM view once this copy is over. The engine tears its own
# down (#1466); a suite without the lock may still hold its own, which goes away -- ours would not.
[ "$SHRINK" != 1 ] || { i=0; while { [ -e /sys/module/brd ] || dmsetup ls 2>/dev/null | grep -q '^lc-src-'; } && [ $i -lt 150 ]; do sleep 2; i=$((i + 1)); done; }
echo "dmleft $(dmsetup ls 2>/dev/null | awk '$1 ~ /^lc-/ { printf "%%s,", $1 }')"
echo "brd $([ -e /sys/module/brd ] && echo loaded || echo none)"
'''


class _Server:
    """The server's agent routes the local clone touches: state (it answers),
    shrink-open/close (#926) and local-clone (the report)."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        posts = self.posts

        class H(BaseHTTPRequestHandler):
            def _send(self, body: dict, code: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._send({"known": True})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                posts.append((self.path.rsplit("/", 1)[-1], body))
                self._send({"ok": True, "id": 7})

            def log_message(self, *_a):
                pass

        self.http = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.http.shutdown()


def _root(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo", "-n", "env", f"PATH={os.environ.get('PATH', '')}:{SBIN}", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def fanout_bin(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lcbin")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-o", str(d / "fanout"), str(REPO / "agent" / "fanout.c")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return d


@pytest.fixture
def disks(tmp_path):
    fx = tmp_path / "fx"
    up = _root("sh", str(FIXTURE), "setup", str(fx), timeout=300)
    try:
        assert up.returncode == 0, up.stdout + up.stderr
        devs = dict(line.split() for line in (fx / "devs").read_text().splitlines())
        yield fx, devs
    finally:
        # #1934: a teardown that left the tmpfs mounted fails here, not unseen on the host
        down = _root("sh", str(FIXTURE), "teardown", str(fx), timeout=120)
        assert down.returncode == 0, down.stdout + down.stderr


def _engine(tmp_path, fx: Path, fanout: Path, targets: str, shrink: int, server: str = "", stop_at: str = "",
            env: tuple[str, ...] = (), kill_at: str = "") -> dict:
    sources = "\n".join(f'. "$LIB_DIR/{n}.sh"' for n in LIBS)
    script = tmp_path / "drive.sh"
    script.write_text(DRIVER % {"lib": LIB, "sources": sources}, newline="\n")
    # #1798: a killed run is its own session, so `kill -9 0` takes the engine and nothing else
    session = ("setsid", "-w") if kill_at else ()
    run = _root(f"PATH={fanout}:{os.environ.get('PATH', '')}:{SBIN}", f"SERVER={server}", f"STOP_AT={stop_at}",
                f"KILL_AT={kill_at}", "LC_STALL_S=120", *env, "flock", ENGINE_LOCK, *session, "sh", str(script),
                str(fx), targets, str(shrink), timeout=1500)
    out = {"raw": run.stdout + run.stderr, "verdict": {}, "target": {}, "inv": {}, "note": {}, "rc": run.returncode}
    for line in run.stdout.splitlines():
        if line.startswith("inv "):
            name, rest = line[4:].split("|", 1); out["inv"][name] = rest.split("|")
        elif line.startswith("verdict "):
            _v, name, rest = line.split(" ", 2); out["verdict"][name] = rest.split("|")[0]
        elif line.startswith("target "):
            name, state, error, ok, table = line[7:].split("|", 4)
            out["target"][name] = {"state": state, "error": error, "ok": ok.split(), "table": table}
        elif line.startswith("note "):
            name, _, note = line[5:].partition("|"); out["note"][name] = note
        elif line.startswith(("phase ", "restore ", "report ", "dmleft ", "brd ", "srcntfs ", "hiberlog ")):
            k, _, v = line.partition(" "); out[k] = v
    if kill_at:
        assert run.returncode in (137, -9) and "phase " not in run.stdout, (run.returncode, out["raw"][-4000:])   # SIGKILL, mid-copy
    else:
        assert run.returncode == 0, out["raw"][-4000:]
    return out


def _table(dev: str) -> dict:
    """The table read back with sgdisk, independently of the engine."""
    p = _root("sgdisk", "-p", dev).stdout
    disk_guid = next(l.split(": ")[1].strip() for l in p.splitlines() if l.startswith("Disk identifier"))
    parts = {}
    for idx in [l.split()[0] for l in p.splitlines() if l.strip()[:1].isdigit() and len(l.split()) > 5]:
        info = dict(l.split(": ", 1) for l in _root("sgdisk", "-i", idx, dev).stdout.splitlines() if ": " in l)
        parts[int(idx)] = {k: info[k].split(" (")[0].strip() for k in
                           ("Partition GUID code", "Partition unique GUID", "First sector", "Partition size",
                            "Attribute flags", "Partition name")}
    return {"guid": disk_guid, "parts": parts}


def _files(dev: str, fx: Path) -> list[str]:
    out = _root("sh", str(FIXTURE), "files", dev, str(fx))
    lines = sorted(out.stdout.splitlines())
    assert lines and not any("mount-failed" in l for l in lines), out.stdout + out.stderr
    return lines


def _no_table(dev: str) -> bool:
    head = _root("dd", f"if={dev}", "bs=512", "skip=1", "count=1", "status=none")
    return "EFI PART" not in head.stdout


def _first_last_md5(part: str) -> tuple[str, str]:
    """md5 of a partition's first and last 512 bytes, read with dd -- not by the engine."""
    out = _root("sh", "-c", f'n=$(blockdev --getsz {part}) && dd if={part} bs=512 count=1 status=none | md5sum'
                            f' && dd if={part} bs=512 skip=$((n - 1)) count=1 status=none | md5sum').stdout.split()
    assert len(out) == 4, out
    return out[0], out[2]


def _size(part: dict) -> int:
    return int(part["Partition size"].split()[0])


def _hidden(part: str) -> int:
    """Boot sector bytes 0x1C..0x1F ('hidden sectors', #2015), read with dd -- not by the engine."""
    out = _root("sh", "-c", f"dd if={part} bs=1 skip=28 count=4 status=none | od -An -tu4").stdout.split()
    assert len(out) == 1, out
    return int(out[0])


def test_identical_and_larger_copies_a_failing_target_alone_and_refusals(tmp_path, disks, fanout_bin):
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    res = _engine(tmp_path, fx, fanout_bin, "same big small tiny full", 0)
    v, t = res["verdict"], res["target"]
    # #1478: no server here, and the smaller target is still measured -- "shrink", not "unchecked"
    assert v == {"same": "fits", "big": "fits", "small": "shrink", "tiny": "short", "full": "fits"}, res["raw"][-3000:]
    assert t["same"]["state"] == "done" and t["big"]["state"] == "done", res["raw"][-3000:]
    assert t["same"]["ok"] == ["1", "2", "3", "4", "5"] and t["big"]["ok"] == ["1", "2", "3", "4", "5"]
    # identical, read back by sgdisk: GUIDs, types, names, attributes, places and sizes
    same = _table(devs["same"])
    assert same == src_table
    big = _table(devs["big"])
    assert big["guid"] == src_table["guid"]
    for i in (1, 2, 3, 4):
        assert big["parts"][i] == src_table["parts"][i], i
    grown, was = big["parts"][5], src_table["parts"][5]
    assert {k: grown[k] for k in grown if k != "Partition size"} == {k: was[k] for k in was if k != "Partition size"}
    assert _size(grown) > _size(was) + 2_000_000, (grown, was)            # +1.5 GB to the end
    assert t["big"]["table"].startswith("5|")
    # the content: every file, and the raw bytes of the partition with no filesystem
    assert _files(devs["same"], fx) == src_files and _files(devs["big"], fx) == src_files
    for d in ("same", "big"):
        assert _root("cmp", "-n", "16777216", devs["src"] + "p2", devs[d] + "p2").returncode == 0, d
    # #1487: the NTFS backup boot sector (the partition's last sector; mkntfs
    # fills the partition) -- partclone -r does not write it. The metal check.
    for d in ("src", "same", "big"):
        for p in ("p3", "p4"):
            first, last = _first_last_md5(devs[d] + p)
            assert first == last, (d, p, first, last)
    # refused before a byte: a shrink the confirm did not present (shrink=0 -- this
    # run checks identical copies, and an accepted shrink compacts every target's
    # layout; the accepted path is the RAM view test's), and too small
    assert t["small"]["state"] == "failed" and "חלון האישור" in t["small"]["error"] and _no_table(devs["small"])
    assert t["tiny"]["state"] == "failed" and "חסרים" in t["tiny"]["error"] and _no_table(devs["tiny"])
    # the target whose writes fail fails alone, with the partition and the cause
    assert t["full"]["state"] == "failed" and "מחיצה" in t["full"]["error"], t["full"]
    # the source was only read
    assert _table(devs["src"]) == src_table and _files(devs["src"], fx) == src_files
    assert res["phase"] == "done" and res["report"].startswith("failed|")   # no server: said, not hidden
    # #2009: a clean source, read by the real ntfs-3g.probe -- the done targets stay green
    assert res["srcntfs"].split() == ["3|clean", "4|clean"] and res["note"]["same"] == res["note"]["big"] == "", res["raw"][-3000:]


def test_no_ram_for_a_view_refuses_the_smaller_target_and_writes_nothing(tmp_path, disks, fanout_bin):
    """#1466 option B (owner, 16/10): no RAM left for a view once the reserve is
    taken -> the smaller target is refused by name, before a byte; the source
    is never shrunk in place, and the target that fits is copied as usual."""
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    src_sha = _root("sha256sum", devs["src"]).stdout.split()[0]
    srv = _Server()
    try:
        res = _engine(tmp_path, fx, fanout_bin, "small same", 1, server=srv.url,
                      env=("LC_SNAP_RESERVE_MB=100000000",))
    finally:
        srv.close()
    v, t = res["verdict"], res["target"]
    assert v == {"small": "shrink", "same": "fits"}, res["raw"][-3000:]
    assert t["small"]["state"] == "failed" and "שמורים להעתקה עצמה" in t["small"]["error"], res["raw"][-3000:]
    assert _no_table(devs["small"])
    assert t["same"]["state"] == "done" and _files(devs["same"], fx) == src_files
    assert _root("sha256sum", devs["src"]).stdout.split()[0] == src_sha            # not one byte written
    assert _table(devs["src"]) == src_table
    assert "shrink-open" not in [k for k, _b in srv.posts]
    report = dict(srv.posts)["local-clone"]
    assert report["shrink_path"] == "refused" and report["shrunk"] is False, report


@pytest.mark.skipif(_tool("dmsetup") is None, reason="dmsetup is not installed: the RAM view (#1466) cannot be built here")
def test_a_smaller_target_through_a_ram_view_leaves_the_source_bytes_identical(tmp_path, disks, fanout_bin):
    """#1466 (owner, 16/10): dm-snapshot over the source partition, the store on brd --
    the source disk is only read: every byte of it is the same after the copy."""
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    src_sha = _root("sha256sum", devs["src"]).stdout.split()[0]
    srv = _Server()
    try:
        res = _engine(tmp_path, fx, fanout_bin, "small same", 1, server=srv.url)
    finally:
        srv.close()
    v, t = res["verdict"], res["target"]
    assert v["small"] == "shrink", res["raw"][-3000:]
    assert t["small"]["state"] == "done" and t["same"]["state"] == "done", res["raw"][-3000:]
    small = _table(devs["small"])
    assert _size(small["parts"][3]) < _size(src_table["parts"][3])   # the compacted layout
    # #2015: the Recovery (NTFS) after the shrunk Windows moved back -- partclone wrote
    # the source's boot sector, and every NTFS/FAT boot sector says where it starts now.
    assert int(small["parts"][4]["First sector"]) < int(src_table["parts"][4]["First sector"])
    for d, table in (("small", small), ("same", _table(devs["same"]))):
        for i in (1, 3, 4):
            assert _hidden(f"{devs[d]}p{i}") == int(table["parts"][i]["First sector"]), (d, i)
        first, last = _first_last_md5(devs[d] + "p4")   # #1487's backup follows sector 0
        assert first == last, (d, first, last)
    assert _files(devs["small"], fx) == src_files and _files(devs["same"], fx) == src_files
    assert _root("sha256sum", devs["src"]).stdout.split()[0] == src_sha            # not one byte written
    assert _table(devs["src"]) == src_table
    report = dict(srv.posts)["local-clone"]
    assert report["shrink_path"] == "snapshot" and report["snapshot_teardown"] == "ok", report
    assert report["shrunk"] is False and isinstance(report["relocations_mb"], int), report
    assert "shrink-open" not in [k for k, _b in srv.posts]                         # nothing to put back
    assert res["dmleft"] == "" and res["brd"] == "none", (res["dmleft"], res["brd"])   # measured under the lock, #1594


def test_without_the_confirmed_shrink_the_small_target_is_not_written(tmp_path, disks, fanout_bin):
    fx, devs = disks
    src_table = _table(devs["src"])
    srv = _Server()
    try:
        res = _engine(tmp_path, fx, fanout_bin, "small same", 0, server=srv.url)
    finally:
        srv.close()
    t = res["target"]
    assert res["verdict"]["small"] == "shrink"
    assert t["small"]["state"] == "failed" and "חלון האישור" in t["small"]["error"] and _no_table(devs["small"])
    assert t["same"]["state"] == "done"
    assert "shrink-open" not in [k for k, _b in srv.posts]                # the source was never touched
    assert _table(devs["src"]) == src_table


def test_a_stop_ends_the_copy_and_leaves_the_source(tmp_path, disks, fanout_bin):
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    res = _engine(tmp_path, fx, fanout_bin, "same", 0, stop_at="3")
    t = res["target"]["same"]
    assert res["phase"] == "stopped", res["raw"][-3000:]
    assert t["state"] == "failed" and "נעצר" in t["error"], t
    assert _table(devs["src"]) == src_table and _files(devs["src"], fx) == src_files


def test_a_copy_cut_by_a_reboot_leaves_the_target_marked_never_a_source_still_a_target(tmp_path, disks, fanout_bin):
    """#1798 (metal, 19/10): `reboot -f` in the middle of a local clone left the target
    with a full GPT, "Windows · 4 מחיצות", and it could be picked as the SOURCE. Here
    the engine is SIGKILLed once partition 3's copy began -- no trap, no cleanup, no
    report -- and a fresh inventory (a new RUN_DIR: the boot after) reads the disks."""
    fx, devs = disks
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    for d in ("cut", "boot", "again"):
        (tmp_path / d).mkdir()
    _engine(tmp_path / "cut", fx, fanout_bin, "same", 0, kill_at="3")
    cut = _table(devs["same"])
    assert cut["guid"] == src_table["guid"] and sorted(cut["parts"]) == [1, 2, 3, 4, 5], cut   # a full GPT...
    assert {p["Partition name"] for p in cut["parts"].values()} == {"'imagectl-incomplete'"}, cut   # ...that says so
    after = _engine(tmp_path / "boot", fx, fanout_bin, "", 0, env=("INV_ONLY=1",))
    same, src = after["inv"]["same"], after["inv"]["src"]
    # usable | why | the mark (lc_disk field 16)
    assert same[11:14] == ["0", "כתיבה לא הושלמה — לא שמיש", "incomplete"], same
    assert (src[11], src[13]) == ("1", "clean"), src
    assert _table(devs["src"]) == src_table and _files(devs["src"], fx) == src_files   # the source: only read
    # allowed as a target: a whole copy onto it ends done, with the source's names
    res = _engine(tmp_path / "again", fx, fanout_bin, "same", 0)
    assert res["target"]["same"]["state"] == "done", res["raw"][-3000:]
    assert _table(devs["same"]) == src_table and _files(devs["same"], fx) == src_files


UNHIB = "Windows במקור לא כובה עד הסוף (Fast Startup). בעותק בוטלה ההשהיה (עלייה מלאה)"
SAME = ("Windows במקור לא כובה עד הסוף (Fast Startup), וביטול ההשהיה בעותק נכשל — הועתק בלי הגדלה, זהה למקור. "
        "כדי להגדיל: לכבות את המקור עם Shift+כיבוי ולשכפל שוב")


def _hibernate_src_p3(tmp_path, devs) -> None:
    """Windows left in Fast Startup: a hiberfil.sys whose header is ``hibr`` --
    what the real ``ntfs-3g.probe`` reads (rc 14)."""
    m = tmp_path / "hm"; m.mkdir()
    hib = tmp_path / "hiberfil.sys"; hib.write_bytes(b"hibr" + bytes(8188))
    put = _root("sh", "-c", 'mount -t ntfs-3g "$1" "$2" && cp "$3" "$2/hiberfil.sys" && sync && umount "$2"',
                "_", devs["src"] + "p3", str(m), str(hib))
    assert put.returncode == 0, put.stdout + put.stderr
    probe = _root("ntfs-3g.probe", "--readwrite", devs["src"] + "p3")
    assert probe.returncode == 14, (probe.returncode, probe.stdout + probe.stderr)   # the fixture really is "hibernated"


def _src_p3_unchanged(devs, before: str) -> None:
    after = _root("sha256sum", devs["src"] + "p3").stdout.split()[0]
    assert after == before                                                        # the source: not a byte written
    assert _root("ntfs-3g.probe", "--readwrite", devs["src"] + "p3").returncode == 14


def test_a_hibernated_source_is_unhibernated_on_the_copy_only_and_grown(tmp_path, disks, fanout_bin):
    """#2009 (field 22/10: a hibernated copy, grown, did not boot; Nadav: never
    worse than dd). The real ntfs-3g drops the hibernation on each TARGET's
    Windows partition -- read back clean, hiberfil.sys gone -- and the target
    grows as always (the fixture's Linux root, last). The source is not touched."""
    fx, devs = disks
    _hibernate_src_p3(tmp_path, devs)
    before = _root("sha256sum", devs["src"] + "p3").stdout.split()[0]
    src_table = _table(devs["src"])
    res = _engine(tmp_path, fx, fanout_bin, "same big", 0)
    t = res["target"]
    assert t["same"]["state"] == "done" and t["big"]["state"] == "done", res["raw"][-3000:]
    assert res["srcntfs"].split() == ["3|hibernated", "4|clean"], res["raw"][-3000:]
    assert res["hiberlog"] == "1", res["raw"][-3000:]
    assert res["note"]["same"] == UNHIB and res["note"]["big"] == UNHIB, res["note"]
    for d in ("same", "big"):
        assert _root("ntfs-3g.probe", "--readwrite", devs[d] + "p3").returncode == 0, d   # a full boot, not a resume
    assert "hibernation dropped on the copy -- probe clean, hiberfil.sys gone or blank" in res["raw"]
    big = _table(devs["big"])
    assert _size(big["parts"][5]) > _size(src_table["parts"][5]) + 2_000_000   # grown as always
    _src_p3_unchanged(devs, before)


def test_when_the_drop_fails_the_copy_is_the_sources_own_like_dd(tmp_path, disks, fanout_bin):
    """ntfs-3g refuses remove_hiberfile (a wrapper in front of the real one):
    no grow -- the bigger target gets the source's table back, every file is
    the source's, the Windows partition still resumes (rc 14). Orange, said."""
    fx, devs = disks
    _hibernate_src_p3(tmp_path, devs)
    before = _root("sha256sum", devs["src"] + "p3").stdout.split()[0]
    src_table, src_files = _table(devs["src"]), _files(devs["src"], fx)
    wrap = tmp_path / "wrap"; wrap.mkdir()
    real = _tool("ntfs-3g")
    (wrap / "ntfs-3g").write_text('#!/bin/sh\ncase "$*" in *remove_hiberfile*) echo refused >&2; exit 1 ;; esac\n'
                                  f'exec {real} "$@"\n', newline="\n")
    os.chmod(wrap / "ntfs-3g", 0o755)
    res = _engine(tmp_path, fx, fanout_bin, "big", 0,
                  env=(f"PATH={wrap}:{fanout_bin}:{os.environ.get('PATH', '')}:{SBIN}",))
    t = res["target"]
    assert t["big"]["state"] == "done", res["raw"][-3000:]
    assert res["note"]["big"] == SAME, res["note"]
    assert _table(devs["big"])["parts"] == src_table["parts"]                    # not grown, nothing moved
    assert _files(devs["big"], fx) == src_files
    assert _root("ntfs-3g.probe", "--readwrite", devs["big"] + "p3").returncode == 14   # resumes on the volume it left
    _src_p3_unchanged(devs, before)
