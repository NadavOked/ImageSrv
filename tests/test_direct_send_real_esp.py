"""#1574 -- direct send on a real FAT32 ESP: pass 2 sends the bytes pass 1 promised.

Found on metal 17/10 (HP build machine, Samsung 870, ESP first): every direct
send failed on partition 1 with "the bytes sent differ from the manifest", and
every drawer then failed with "sha256 mismatch in p1.esp.pcl.zst".

The suspects were three, and this file measures each one on a real loop disk,
with the real partclone.fat, ``zstd -T0`` and fanout -- only udp-sender, curl
and the server's answer are stubs:

* the bytes of the ESP change between the passes (boot_ca mounts it after
  pass 1) -- ``test_boot_ca_mount_does_not_change_the_esp``;
* the pipeline is not deterministic (``zstd -T0``) -- after the fix both passes
  are the same pipeline, and ``test_direct_send_from_a_real_esp_sends_what_the_manifest_says``
  is that pipeline run twice on 8 cores;
* the two passes are not the same pipeline -- the root (#1472 gave pass 1
  ``-a0 -z 524288`` and pass 2 kept partclone's defaults).

And the check is not folded: a real write to the ESP between the passes still
fails the send, by name (``test_a_real_write_between_the_passes_still_fails``).

Loop devices and mount are root: the Testrunner's ``sudo -n``; elsewhere a
declared native skip (``native.py``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_directsend import TASK, _answer
from test_local_clone_engine import FIXTURE, LIB, REPO, _root, _sudo_ok, _tool

pytestmark = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, _tool(t)) for t in ("losetup", "sgdisk", "partclone.fat", "mkfs.vfat", "zstd", "openssl",
                              "gcc", "jq")],
    posix=True,
    why="a loop disk, mount and partclone as root: the Testrunner",
)

LIBS = ("common", "sysinfo", "waits", "jsonq", "progress", "restore", "manifest", "bootca",
        "hibernation", "shrinkplan", "shrink", "lcsnap", "writemark", "diskfit", "capturegate", "capture", "capturesink", "directsend", "directrecv", "ui")   # #1798: capture_disk reads the mark; #1901/#1808: the source gates

#: udp-sender: says it started (as the real one does once receivers joined)
#: and swallows the stream. What it read is what fanout hashed.
UDP_SENDER = '#!/bin/sh\necho "Starting transfer: 00000000"\ncat > /dev/null\nexit 0\n'

#: curl: the manifest PUT is the only call. CURL_HOOK runs there -- after the
#: manifest exists and before pass 2 -- to change the disk on purpose.
CURL = '#!/bin/sh\nprintf "%s\\n" "$@" >> "$F/curl.argv"\n[ -z "${CURL_HOOK:-}" ] || sh -c "$CURL_HOOK"\nexit 0\n'

DRIVER = r'''
set -u
F=$1; MODE=$2; T=$3
export F RUN_DIR=$F/run LIB_DIR=%(lib)s IMAGECTL_TEST=1 MAC=aa:bb:cc:00:00:10 SERVER=http://s
export IFACE=lo TASK_TOKEN=tttttttttttttttttttttttttttttttttttttttttttttttt RESP=$F/resp.json DIRECT_POLL_S=0
export PATH="$T/stubs:$PATH"
rm -rf "$RUN_DIR"; mkdir -p "$RUN_DIR"
# DEVROOT/SYSROOT are the test's own (test_tests_never_touch_host_dev): sda is the loop disk.
SRC=$(awk '$1 == "src" { print $2 }' "$F/devs"); L=${SRC#/dev/}
export DEVROOT=$F/dev SYSROOT=$F/sysroot
mkdir -p "$DEVROOT" "$SYSROOT/sys/block/sda/queue"
ln -sf "$SRC" "$DEVROOT/sda"; ln -sf "${SRC}p1" "$DEVROOT/sda1"
cat "/sys/block/$L/size" > "$SYSROOT/sys/block/sda/size"
cat "/sys/block/$L/queue/logical_block_size" > "$SYSROOT/sys/block/sda/queue/logical_block_size"
%(sources)s
send_hello() { cp "$T/answer.json" "$RESP"; }
echo "before $(sha256sum < "${SRC}p1" | cut -c1-64)"
case "$MODE" in
send)  direct_send_run %(task)s sda > "$F/direct.out" 2>&1; echo "rc=$?" ;;
probe) boot_ca_probe "$DEVROOT/sda1" "$RUN_DIR/bootca.out" > "$F/direct.out" 2>&1; echo "rc=$?" ;;
esac
echo "after $(sha256sum < "${SRC}p1" | cut -c1-64)"
'''


@pytest.fixture(scope="module")
def stubs(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("dsbin")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-o", str(d / "fanout"), str(REPO / "agent" / "fanout.c")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    for name, body in (("udp-sender", UDP_SENDER), ("curl", CURL)):
        (d / name).write_text(body, newline="\n")
        (d / name).chmod(0o755)
    return d


@pytest.fixture
def esp(tmp_path):
    fx = tmp_path / "fx"
    up = _root("sh", str(FIXTURE), "setup-esp", str(fx), timeout=300)
    try:
        assert up.returncode == 0, up.stdout + up.stderr
        yield fx
    finally:
        _root("sh", str(FIXTURE), "teardown", str(fx), timeout=120)


def _drive(fx: Path, stubs: Path, mode: str, env: tuple[str, ...] = ()) -> dict:
    # fx is root's (the fixture); the driver, the stubs and the answer are ours.
    t = fx.parent / f"t-{mode}"
    t.mkdir(exist_ok=True)
    (t / "stubs").symlink_to(stubs, target_is_directory=True)
    (t / "answer.json").write_text(json.dumps(_answer(receivers=1)), encoding="utf-8")
    script = t / "drive.sh"
    sources = "\n".join(f'. "$LIB_DIR/{n}.sh"' for n in LIBS)
    script.write_text(DRIVER % {"lib": LIB, "sources": sources, "task": TASK}, newline="\n")
    done = _root(*env, "sh", str(script), str(fx), mode, str(t), timeout=600)
    words = dict(line.split(None, 1) for line in done.stdout.splitlines() if " " in line)
    log = _root("cat", str(fx / "run" / "agent.log")).stdout
    out = _root("cat", str(fx / "direct.out")).stdout
    rc = next((ln for ln in done.stdout.splitlines() if ln.startswith("rc=")), "rc=?")
    return {"rc": rc, "before": words.get("before"), "after": words.get("after"),
            "log": log, "out": out, "raw": done.stdout + done.stderr}


def _error(fx: Path) -> str:
    return _root("cat", str(fx / "run" / "targets" / "sda" / "error")).stdout


def _sha_file(fx: Path, name: str) -> str:
    return _root("cat", str(fx / "run" / name)).stdout.split()[0]


def test_boot_ca_mount_does_not_change_the_esp(esp, stubs):
    """boot_ca's own mount (``-t vfat -o ro``) on a real FAT32: the raw bytes of
    the partition are the same before and after. Evidence that the mount
    happened: the boot loader on it was found and opened."""
    r = _drive(esp, stubs, "probe")
    assert "boot_ca: /EFI/BOOT/bootx64.efi" in r["log"], r["raw"] + r["log"]
    assert r["before"] and len(r["before"]) == 64, r["raw"]
    assert r["before"] == r["after"], r["raw"]


def test_direct_send_from_a_real_esp_sends_what_the_manifest_says(esp, stubs):
    """The metal failure of 17/10, on a loop disk: pass 1 (capture_disk,
    discard sink), boot_ca's mount, pass 2 (the stream) -- and the wire hash
    equals the manifest. Before the fix: rc=1, "הבייטים שנשלחו שונים מהמניפסט"."""
    r = _drive(esp, stubs, "send")
    assert r["rc"] == "rc=0", r["raw"] + r["out"] + r["log"] + _error(esp)
    manifest = json.loads(_root("cat", str(esp / "run" / "new-manifest.json")).stdout)
    assert manifest["partitions"][0]["role"] == "esp"
    assert _sha_file(esp, "dsha.1") == manifest["partitions"][0]["sha256"]
    # boot_ca ran between the passes, on a real mount (not a skipped probe)
    assert "boot_ca: /EFI/BOOT/bootx64.efi" in r["log"], r["log"]
    # and the source is the same disk it was: nothing in the session wrote it
    assert r["before"] == r["after"], r["raw"]


def test_a_real_write_between_the_passes_still_fails(esp, stubs):
    """The check is not folded: a file written to the ESP after the manifest
    and before the stream fails the send at the source, by name."""
    hook = ('mkdir -p "$F/w" && mount -t vfat "$DEVROOT/sda1" "$F/w" '
            '&& echo changed > "$F/w/changed.txt" && sync && umount "$F/w"')
    r = _drive(esp, stubs, "send", env=(f"CURL_HOOK={hook}",))
    assert r["rc"] == "rc=1", r["raw"] + r["out"] + r["log"]
    assert r["before"] != r["after"], "the hook did not change the disk -- no evidence: " + r["raw"]
    assert "הבייטים שנשלחו שונים מהמניפסט" in _error(esp), _error(esp) + r["log"]
