"""#3629: the grow of a restored xfs mounts it `nouuid` -- two xfs of one image carry the same UUID.

Every copy of a captured xfs has the same UUID, and the kernel refuses to mount a second one while the first is mounted
("Filesystem has duplicate UUID ... - can't mount") unless it is mounted `nouuid`. `grow.sh` mounted the volume it grows with a
bare `-t xfs`, so the grow of a drawer failed (`GROW_ERROR`, "לא הצלחנו לעגן את מערכת הקבצים xfs") whenever another xfs of the
same image was mounted at that moment: an earlier drawer left mounted by a failed umount, or the source of a local clone.

The real thing, as root on loop devices (the Testrunner): two restored copies of one real xfs, the first MOUNTED PLAIN, then
`finish_grow` (the real one, `xfs_growfs` and all) on the second, and after that on the first once it is released. Both end clean,
with the one UUID, grown past the captured size.

Negative control: grow.sh back to lab/vm-1114f -- the grow of the second copy fails on the mount (rc 0 with a deferred grow, and an
error that names the mount), and the size stays what was captured. The test_stage_attribution test that pins the mount line
changes with it.
"""

from __future__ import annotations

import os
import subprocess

from test_xfs_3415 import (AGENT, BIG, BUSYBOX, REAL, SBIN_PATH, SIZE, _capture, _clean, _fs_bytes, _garbage, _make_xfs,
                           _restore, _sb, needs_root)

#: One root script. `grow_one NAME LOOP` is finish_grow as drawers.sh calls it, on a loop device; only the identity check (#2434),
#: the partition-name mark (#1798) and the drawer's state file are stubbed. RESULT: "<rc of finish_grow>|<target state>|<error>".
SCRIPT = r'''set -u
la=""; lb=""
cleanup() { umount "$W/hold" 2>/dev/null; for l in $la $lb; do losetup -d "$l" 2>/dev/null; done; chown -R "$OWNER" "$W"; }
trap cleanup EXIT
la=$(losetup -f --show "$A") || exit 91
lb=$(losetup -f --show "$B") || exit 92
mkdir -p "$W/hold" || exit 93
grow_one() {   # $1 = name, $2 = loop
    mkdir -p "$W/$1/run/targets/sda" || exit 94
    echo "2|xfs" > "$W/$1/run/targets/sda/expanded"
    RUN_DIR="$W/$1/run" LOOP="$2" "$BB" sh -c '
        . "$AGENT/lib/core/common.sh"; . "$AGENT/lib/restore/grow.sh"
        partition_node() { echo "$LOOP"; }
        assert_target() { :; }; writemark_release() { :; }
        target_set() { printf "%s|%s\n" "$2" "$3" > "$RUN_DIR/target_state"; }
        finish_grow sda "" ""' > "$W/$1/grow.out" 2>&1
    echo "$?" > "$W/$1/rc"
}
# the first copy is mounted PLAIN: its UUID is taken
mount -t xfs "$la" "$W/hold" || exit 95
grow_one second "$lb"
umount "$W/hold" || exit 96
grow_one first "$la"
'''


@REAL
@needs_root
def test_two_xfs_of_one_image_are_both_grown_even_while_the_first_is_mounted(tmp_path):
    src = _make_xfs(tmp_path)
    packed = _capture(src, tmp_path)
    a, b = _garbage(tmp_path / "a.disk", BIG), _garbage(tmp_path / "b.disk", BIG)
    _restore(packed, a, tmp_path)
    _restore(packed, b, tmp_path)
    assert _sb(a)["uuid"] == _sb(b)["uuid"], "the two copies must carry the same xfs UUID"
    assert _fs_bytes(a) <= SIZE and _fs_bytes(b) <= SIZE      # as captured: both have a grow to do

    w = tmp_path / "w"
    w.mkdir()
    env = {"W": str(w), "A": str(a), "B": str(b), "AGENT": str(AGENT), "BB": BUSYBOX,
           "OWNER": f"{os.getuid()}:{os.getgid()}", "PATH": SBIN_PATH}
    proc = subprocess.run(["sudo", "-n", "env", *[f"{k}={v}" for k, v in env.items()], "sh", "-c", SCRIPT],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)       # the harness itself (the plain mount of the first)

    def outcome(name: str) -> str:
        run = w / name
        state = (run / "run" / "target_state").read_text(errors="replace").strip() if (run / "run" / "target_state").exists() else "?"
        log = (run / "run" / "agent.log").read_text(errors="replace") if (run / "run" / "agent.log").exists() else ""
        return f"{name}: rc={(run / 'rc').read_text().strip()} state={state}\n{(run / 'grow.out').read_text(errors='replace')}{log}"

    for name, image in (("second", b), ("first", a)):
        assert (w / name / "rc").read_text().strip() == "0", outcome(name)
        assert (w / name / "run" / "target_state").read_text() == "done|\n", outcome(name)   # done, and no "the grow was deferred"
        assert _fs_bytes(image) > SIZE, outcome(name)                                          # grown past the captured size
        _clean(image)
    assert _sb(a)["uuid"] == _sb(b)["uuid"]
