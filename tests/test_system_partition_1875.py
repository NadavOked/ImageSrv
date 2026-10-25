"""#1875 — which partition is "the system partition", decided once.

The owner's real image **Vm** (img_5790e4, captured before #1369) has two
``role: windows`` partitions: p1, a 3GB OEM/tools partition at sector 2048,
and p2, the real C: (223GB). ``system_free_after`` (agent) and
``minfree.free_after`` (server) judged the FIRST windows partition on the
platter -- p1, 2.0GB free -- so the image was refused on every disk, a 500GB
one included ("יישארו רק 2.0 GB פנויים ב-C:"), while ``_expand_candidate``
stretched the LAST one (p2). Two answers to one question.

The rule now (one jq def in ``agent/lib/diskfit.sh``, one function in
``server/minfree.py``):

1. **Evidence.** Capture already mounts every Basic Data NTFS partition
   read-only to look for ``Windows\\System32`` (#1369); in the same mount it
   now looks for ``Windows/System32/config/SYSTEM`` and records
   ``"system": true`` on that partition. A marked partition is the system.
2. **An old manifest without the mark:** the LARGEST windows partition --
   an OEM/tools partition is small, C: holds the system (and in Vm it is also
   the one expand stretches). Two windows partitions of the same top size, or
   one without a size, is real doubt: ``unchecked``, never a wrong ``short``
   (principle 5).
3. No windows partition: the first linux one, as before.

``hostname.sh`` mounts the system partition first and still checks for
``Windows\\System32`` before writing (the evidence at restore time); before
this change it mounted p1 first (``ntfs-3g -o remove_hiberfile``, read-write)
and moved on to p2.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from native import requires_native
from server.images import ImageLibrary, required_bytes
from test_agent import AGENT, BASH, _stub, posix, sh

needs_shell = requires_native(("bash", BASH), ("jq", shutil.which("jq")))

GIB = 1 << 30
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
REC = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"

#: The manifest of Vm (img_5790e4) as it sits on the server: geometry, roles and
#: used bytes exactly as recorded. ``expandable`` on p2 is what _mark_expandable
#: (#58) writes on the last windows partition; a variant below clears it.
VM = {
    "schema": 1, "id": "img_5790e4", "name": "Vm", "family": 500,
    "scheme": "gpt", "sector_size": 512, "os": "windows",
    "disk_guid": "4C7B1E00-0000-4000-8000-000000001875",
    "source_disk_bytes": 500107862016, "min_target_bytes": 227457368064,
    "partitions": [
        {"index": 1, "type_guid": WIN, "role": "windows", "fs": "ntfs",
         "start_sector": 2048, "size_bytes": 3028287488, "used_bytes": 880222208,
         "file": "p1.windows.pcl.zst", "sha256": "a1" * 32, "expandable": False},
        {"index": 2, "type_guid": WIN, "role": "windows", "fs": "ntfs",
         "start_sector": 5919226, "size_bytes": 223392075264, "used_bytes": 203075014656,
         "file": "p2.windows.pcl.zst", "sha256": "a2" * 32, "expandable": True},
        {"index": 3, "type_guid": REC, "role": "recovery", "fs": "ntfs",
         "start_sector": 442232832, "size_bytes": 927989760, "used_bytes": 717639680,
         "file": "p3.recovery.pcl.zst", "sha256": "a3" * 32, "expandable": False},
        {"index": 4, "type_guid": ESP, "role": "esp", "fs": "vfat",
         "start_sector": 444047360, "size_bytes": 104857600, "used_bytes": 75114496,
         "file": "p4.esp.pcl.zst", "sha256": "a4" * 32, "expandable": False},
    ],
}
NEED = 227457368064
DISK_500 = 500107862016
DISK_256 = 256060514304
C_FREE = 223392075264 - 203075014656          # C: as captured: 18.9 GiB free


def vm(changes: dict) -> dict:
    """A copy of Vm; `changes` = {index: {field: value}}."""
    m = copy.deepcopy(VM)
    for p in m["partitions"]:
        p.update(changes.get(p["index"], {}))
    return m


def fits(tmp_path: Path, image: dict, disk: int, gb: str = "20") -> tuple[str, str]:
    """disk_fits on drawer sda of `disk` bytes -- the agent's own diskfit.sh,
    expand.sh and restore.sh, the real jq."""
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (run / "min_free_gb").write_text(gb + "\n", encoding="utf-8")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(image), encoding="utf-8")
    out = sh(_stub(stubs / "blockdev", f"echo {disk}")
             + f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
             f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run)!r}/log; "
             + "".join(f". {posix(AGENT)}/lib/{lib}.sh; " for lib in
                       ("common", "jsonq", "progress", "restore", "expand", "diskfit"))
             + "raid_member_reason() { :; }; "   # #1808: the RAID gate has its own tests (test_capture_gates.py); these disks are files
             + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
    err = run / "targets" / "sda" / "error"
    return out.strip().splitlines()[-1], err.read_text(encoding="utf-8").strip() if err.exists() else ""


def system_index(tmp_path: Path, image: dict) -> str:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(image), encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    return sh(f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/log; "
              f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/diskfit.sh; "
              f"system_partition {posix(path)!r}").strip()


# --- the bug: Vm is refused on every disk -----------------------------------


@needs_shell
def test_vm_fits_a_500gb_drawer(tmp_path):
    assert fits(tmp_path, VM, DISK_500) == ("rc=0", "")


@needs_shell
def test_vm_fits_a_256gb_drawer(tmp_path):
    assert fits(tmp_path, VM, DISK_256) == ("rc=0", "")


@needs_shell
def test_vm_is_judged_on_c_and_not_on_the_3gb_partition(tmp_path):
    """On a disk exactly the layout nothing grows: C: keeps its 18.9 GiB free --
    that is short of 20, and the number is C:'s, not p1's 2.0."""
    rc, err = fits(tmp_path, VM, NEED)
    assert rc == "rc=1"
    assert err == "לא נכנס — יישארו רק 18.9 GB פנויים ב-C:, המינימום 20GB"


@needs_shell
def test_vm_without_an_expandable_mark_is_judged_the_same(tmp_path):
    """No mark = expand's automatic pick (the last windows partition, p2)."""
    image = vm({2: {"expandable": False}})
    assert fits(tmp_path, image, DISK_500) == ("rc=0", "")


def test_the_server_lets_vm_onto_a_500gb_and_a_256gb_disk():
    from server import minfree   # lazy: the negative control swaps the module
    assert minfree.refusal(VM, DISK_500, 20) is None
    assert minfree.refusal(VM, DISK_256, 20) is None
    assert minfree.unchecked(VM, 20) is None
    assert minfree.free_after(VM, NEED) == (C_FREE, "windows")


@needs_shell
def test_the_smallest_disk_the_console_shows_for_vm_is_the_agents_boundary(tmp_path):
    """Short by ~1.1 GiB at its own layout, and C: is the partition that grows:
    the console's "fits a disk from X" exists and the agent agrees on it."""
    from server import minfree
    smallest = minfree.min_disk_bytes(VM, 20)
    assert smallest == NEED + 20 * GIB - C_FREE
    assert fits(tmp_path / "a", VM, smallest)[0] == "rc=0"
    assert fits(tmp_path / "b", VM, smallest - 1)[0] == "rc=1"


# --- the rule: evidence first, then the largest, doubt is unchecked ---------


@needs_shell
def test_the_agent_and_the_server_name_the_same_system_partition(tmp_path):
    from server import minfree
    assert minfree.system_partition(VM)["index"] == 2
    assert system_index(tmp_path, VM) == "2"


@needs_shell
def test_the_system_mark_from_capture_beats_size(tmp_path):
    """Evidence over the heuristic: a third windows partition -- neither the
    first on the platter nor the largest -- carries the mark, and it is the one
    judged (0.2 GB free; it does not grow, expand stretches p2)."""
    from server import minfree
    image = vm({3: {"role": "windows", "system": True}})
    rc, err = fits(tmp_path, image, DISK_500)
    assert (rc, err) == ("rc=1", "לא נכנס — יישארו רק 0.2 GB פנויים ב-C:, המינימום 20GB")
    assert err == minfree.refusal(image, DISK_500, 20)
    assert minfree.system_partition(image)["index"] == 3
    assert system_index(tmp_path, image) == "3"


AMBIGUOUS = "לא נבדק אם יישארו 20GB פנויים ב-C: — לא ידוע איזו מחיצת windows היא C: (אין סימון מהקליטה, ואין אחת גדולה מכולן)"


@needs_shell
def test_two_windows_partitions_of_the_same_size_are_unchecked_not_short(tmp_path):
    from server import minfree
    image = vm({1: {"size_bytes": 223392075264, "used_bytes": 1 << 20}})
    rc, err = fits(tmp_path, image, 10**13)
    assert (rc, err) == ("rc=1", AMBIGUOUS)
    assert minfree.refusal(image, 10**13, 20) == AMBIGUOUS
    assert minfree.unchecked(image, 20) == AMBIGUOUS
    assert minfree.min_disk_bytes(image, 20) is None
    assert system_index(tmp_path, image) == "ambiguous"


@needs_shell
def test_a_windows_partition_without_a_size_among_several_is_unchecked(tmp_path):
    from server import minfree
    image = vm({1: {"size_bytes": None}})
    assert minfree.refusal(image, DISK_500, 20) == AMBIGUOUS
    assert fits(tmp_path, image, DISK_500) == ("rc=1", AMBIGUOUS)
    assert system_index(tmp_path, image) == "ambiguous"


@needs_shell
def test_the_rule_off_ignores_the_doubt(tmp_path):
    image = vm({1: {"size_bytes": 223392075264}})
    assert fits(tmp_path, image, 10**13, gb="0") == ("rc=0", "")


def test_the_server_refuses_a_system_mark_that_is_not_a_boolean():
    assert ImageLibrary._validate(VM) is None
    assert ImageLibrary._validate(vm({2: {"system": True}})) is None
    assert ImageLibrary._validate(vm({2: {"system": "yes"}})) == "partition 2 has malformed system flag"


# --- hostname: the name lands on C:, and p1 is never mounted ----------------

#: ntfs-3g that "mounts": records the node, exposes a system on *2 only.
NTFS3G = ('echo "$*" >> "$RUN_DIR/ntfs-3g.calls"\n'
          'eval "mnt=\\${$#}"; eval "node=\\${$(($# - 1))}"\n'
          'echo "$node" > "$RUN_DIR/mounted"\n'
          'case "$node" in *2) mkdir -p "$mnt/Windows/System32/config"\n'
          '                    echo hive > "$mnt/Windows/System32/config/SYSTEM" ;; esac\n'
          'exit 0')
#: hivewrite: a write records which partition was mounted; reads answer as a
#: hive that took the name (Select\\Current = 1).
HIVEWRITE = ('if [ "$1" = "-g" ]; then\n'
             '  case "$*" in *Current*) echo 1;; *) echo LAB1-05;; esac; exit 0\nfi\n'
             'cat "$RUN_DIR/mounted" >> "$RUN_DIR/hive.writes"; exit 0')
UMOUNT = 'rm -rf "$1/Windows"; exit 0'


@needs_shell
def test_vm_is_named_on_c_and_the_oem_partition_is_never_mounted(tmp_path):
    box = tmp_path / "box"
    stubs = box / "stubs"
    run = box / "run"
    stubs.mkdir(parents=True)
    run.mkdir(parents=True)
    manifest = box / "manifest.json"
    manifest.write_text(json.dumps(VM), encoding="utf-8")
    out = sh(_stub(stubs / "ntfs-3g", NTFS3G) + _stub(stubs / "hivewrite", HIVEWRITE)
             + _stub(stubs / "umount", UMOUNT)
             + f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
             f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box / 'dev')!r} LOG_FILE={posix(run / 'log')!r}; "
             + "".join(f". {posix(AGENT)}/lib/{lib}.sh; " for lib in
                       ("common", "jsonq", "restore", "diskfit", "hostname", "unattend"))
             + f"write_hostname sda {posix(manifest)!r} LAB1-05 || true")
    result = json.loads(out.strip().splitlines()[-1])
    assert result == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"}
    c_node = f"{posix(box / 'dev')}/sda2"
    assert (run / "hive.writes").read_text().split() == [c_node] * 2
    mounts = (run / "ntfs-3g.calls").read_text().splitlines()
    assert [m.split()[-2] for m in mounts] == [c_node], mounts


# --- capture records the evidence ---------------------------------------------


@needs_shell
def test_capture_marks_the_partition_with_the_system_hive(tmp_path):
    from test_capture_measure_1368 import _captured
    parts = {p["index"]: p for p in _captured(tmp_path)["partitions"]}
    assert parts[2]["role"] == "windows" and parts[2]["system"] is True
    assert "system" not in parts[1]                       # data: no System32


@needs_shell
def test_a_system32_without_the_system_hive_is_windows_but_not_marked(tmp_path):
    """The role keeps #1369's rule (System32); the mark needs the hive itself."""
    from test_capture_measure_1368 import _captured
    no_hive = ('#!/bin/sh\n'
               'eval "mnt=\\${$#}"; eval "node=\\${$(($# - 1))}"\n'
               'case "$node" in *2) mkdir -p "$mnt/Windows/System32" ;; esac\n'
               'exit 0\n')
    parts = {p["index"]: p for p in _captured(tmp_path, **{"ntfs-3g": no_hive})["partitions"]}
    assert parts[2]["role"] == "windows"
    assert "system" not in parts[2]
