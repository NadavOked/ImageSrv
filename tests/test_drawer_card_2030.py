"""#2030 -- the drawer card says the connection, the size and what is on the disk.

The card showed the drawer number twice ("1 מגירה" big, "דיסק 1" next to it) and, in
a round, the device name under it ("sda") -- the size was missing there altogether
(a drawer record has no size; only the idle inventory had it). Nadav 22/10: the big
number stays (what he writes on the drawer); the name is the connection ("SATA 0",
"NVMe 03:00.0"); then the size; then how much is taken -- before the write what it
will erase, after it what was written, "לא נמדד" when nobody measured (principle 5,
never 0); "sda" leaves the screen.

Two halves:

* **the agent** (`diskused.sh`): the measurement and its cache, under bash with the
  leaf commands (blkid, mount, df, umount) stood in -- the numbers, and every way it
  can fail to measure saying -1, never 0; nothing mounted while a round owns the disks;
  a disk a round wrote is stale; the bridge loads the library.
* **the screens** (C, the Testrunner only): the real GUI renders the round, the local
  clone and the stick; IMAGECTL_GUI_TEXTLOG records every string the screen lays out,
  and the assertions are on those words -- not on pixels.

**Negative control:** with the production files of the fix put back as they were on
lab/int-1022 (everything but the test hook in text.c), the screen tests fail on the
words below and the agent tests fail on "diskused.sh: No such file".
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import AGENT, BASH, GUI, _build_gui, _pkgconfig, posix

REPO = Path(__file__).resolve().parent.parent
pytestmark = requires_native(("bash", BASH))
GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui is built on Linux only",
)

# ---------------------------------------------------------------- the agent

STUBS = r'''
# the leaf commands: what the disk says is a file next to its node
blkid() { [ -f "$DEVROOT/$(basename "$5").fs" ] || return 4; cat "$DEVROOT/$(basename "$5").fs"; }
mount() { _node="${@: -2:1}"; echo "$_node" > "$CUR"; echo x >> "$MOUNTS"; [ ! -f "$DEVROOT/$(basename "$_node").nomount" ]; }
ntfs-3g() { echo "$3" > "$CUR"; echo x >> "$MOUNTS"; [ ! -f "$DEVROOT/$(basename "$3").nomount" ]; }
df() { printf 'Filesystem 1K-blocks Used Available Use%% Mounted
x 999999 %s 9 1%% /m
' "$(cat "$DEVROOT/$(basename "$(cat "$CUR")").used")"; }
umount() { [ ! -f "$DEVROOT/umountfail" ]; }
disk_scheme() { cat "$DEVROOT/$(basename "$1").scheme" 2>/dev/null || echo unknown; }
'''


def part(root: Path, dev: Path, disk: str, idx: int, sectors: int, fs: str | None = None,
         used_kb: int | None = None, nomount: bool = False) -> None:
    d = root / "sys/block" / disk / f"{disk}{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "partition").write_text(f"{idx}\n"); (d / "size").write_text(f"{sectors}\n")
    if fs is not None:
        (dev / f"{disk}{idx}.fs").write_text(fs + "\n")
    if used_kb is not None:
        (dev / f"{disk}{idx}.used").write_text(f"{used_kb}\n")
    if nomount:
        (dev / f"{disk}{idx}.nomount").write_text("")


def disk(root: Path, dev: Path, name: str, sectors: int, scheme: str = "gpt") -> None:
    (root / "sys/block" / name).mkdir(parents=True, exist_ok=True)
    (root / "sys/block" / name / "size").write_text(f"{sectors}\n")
    (dev / f"{name}.scheme").write_text(scheme + "\n")


class Box:
    def __init__(self, tmp: Path):
        self.run, self.gui, self.root, self.dev = tmp / "run", tmp / "gui", tmp / "root", tmp / "dev"
        for p in (self.run, self.gui, self.root, self.dev):
            p.mkdir(parents=True, exist_ok=True)
        self.mounts = tmp / "mounts"; self.cur = tmp / "cur"; self.log = tmp / "log"
        self.mounts.write_text("")

    def sh(self, script: str) -> subprocess.CompletedProcess:
        libs = "".join(f". {posix(AGENT)}/lib/{n}.sh; " for n in ("common", "sysinfo", "manifest", "restore", "diskused"))
        pre = (f'export PATH="/usr/bin:$PATH" RUN_DIR={posix(self.run)} GUI_DIR={posix(self.gui)} SYSROOT={posix(self.root)} '
               f'DEVROOT={posix(self.dev)} LOG_FILE={posix(self.log)} MOUNTS={posix(self.mounts)} CUR={posix(self.cur)} '
               f'MNT={posix(self.gui)}/used/mnt IMAGECTL_TEST=1; {libs}{STUBS}')
        out = subprocess.run([BASH, "-c", pre + script], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        assert out.returncode == 0, out.stderr
        return out

    def mount_count(self) -> int:
        return len(self.mounts.read_text().split())


GB = 1_000_000_000


def rec(disks: str) -> str:
    """disk_used_records with the background scan stood in: it only notes that it was asked."""
    return '( disk_used_scan() { echo spawn >> "$GUI_DIR/spawns"; }; disk_used_records "%s" )' % disks


def box_with_disk(tmp_path: Path) -> Box:
    b = Box(tmp_path)
    disk(b.root, b.dev, "sda", 1_953_525_168)                  # 1 TB
    part(b.root, b.dev, "sda", 1, 204800, "vfat", used_kb=1000)
    part(b.root, b.dev, "sda", 2, 1000, "swap")
    part(b.root, b.dev, "sda", 3, 2048, "unknown")             # a filesystem partclone does not know: the whole partition
    part(b.root, b.dev, "sda", 4, 976_000_000, "ntfs", used_kb=200_000_000)
    return b


def test_the_bytes_in_use_are_what_df_says_per_partition_swap_nothing_unknown_all(tmp_path):
    b = box_with_disk(tmp_path)
    out = b.sh("disk_used_measure sda").stdout.strip()
    assert out == str(1000 * 1024 + 0 + 2048 * 512 + 200_000_000 * 1024), out


@pytest.mark.parametrize("what,setup", [
    ("a filesystem blkid cannot read", lambda b: (b.dev / "sda1.fs").unlink()),
    ("a partition that will not mount (dirty NTFS)", lambda b: (b.dev / "sda4.nomount").write_text("")),
    ("a umount that failed", lambda b: (b.dev / "umountfail").write_text("")),
    ("a unreadable partition size", lambda b: (b.root / "sys/block/sda/sda1/size").write_text("x\n")),
])
def test_everything_that_cannot_be_measured_says_minus_one_never_zero_never_a_partial_sum(tmp_path, what, setup):
    b = box_with_disk(tmp_path); setup(b)
    assert b.sh("disk_used_measure sda").stdout.strip() == "-1", what


def test_a_disk_with_no_partitions_is_zero_only_when_the_table_says_none(tmp_path):
    b = Box(tmp_path)
    disk(b.root, b.dev, "sdb", 1_000_000, "none"); disk(b.root, b.dev, "sdc", 1_000_000, "unknown")
    assert b.sh("disk_used_measure sdb; disk_used_measure sdc").stdout.split() == ["0", "-1"]


def test_nothing_is_mounted_while_a_round_owns_the_disks(tmp_path):
    b = box_with_disk(tmp_path)
    (b.run / "targets/sdb").mkdir(parents=True)               # any target directory: a round is open
    assert b.sh("disk_used_measure sda").stdout.strip() == "-1"
    assert b.mount_count() == 0


def test_a_round_that_arrives_while_a_disk_is_measured_drops_the_value(tmp_path):
    """The scan checks before every partition and again after: a number taken while the
    disk was being taken over is not written to the cache."""
    b = box_with_disk(tmp_path)
    b.sh(f'ntfs-3g() {{ mkdir -p "$RUN_DIR/targets/sdb"; return 1; }}; DU_DIR={posix(b.gui)}/used; mkdir -p "$DU_DIR"; disk_used_scan')
    assert not (b.gui / "used/sda").exists()


def test_the_record_is_the_size_and_minus_one_until_the_scan_has_a_value(tmp_path):
    b = box_with_disk(tmp_path)
    first = b.sh(rec("sda") + " | head -n 1").stdout.strip()
    assert first == f"cused=sda|{1_953_525_168 * 512}|-1", first
    b.sh('disk_used_scan')                                    # what the background job does, in the foreground
    want = 1000 * 1024 + 2048 * 512 + 200_000_000 * 1024
    assert b.sh(rec("sda")).stdout.strip() == f"cused=sda|{1_953_525_168 * 512}|{want}"


def test_a_measured_minus_one_is_cached_and_not_retried_every_second(tmp_path):
    b = box_with_disk(tmp_path); (b.dev / "sda4.nomount").write_text("")
    b.sh('disk_used_scan'); before = b.mount_count()
    b.sh('disk_used_scan; disk_used_scan')
    assert b.mount_count() == before, "a disk that cannot be measured was mounted again"
    assert b.sh(rec("sda")).stdout.strip().endswith("|-1")


def test_a_cache_for_another_size_is_not_a_value(tmp_path):
    b = box_with_disk(tmp_path); b.sh('disk_used_scan')
    (b.root / "sys/block/sda/size").write_text("1000\n")      # another disk in the same slot
    assert b.sh(rec("sda") + " | head -n 1").stdout.strip() == f"cused=sda|{1000 * 512}|-1"


def test_a_disk_the_round_wrote_is_stale_and_measured_again_at_the_next_idle(tmp_path):
    b = box_with_disk(tmp_path); b.sh('disk_used_scan')
    (b.run / "targets/sda").mkdir(parents=True); (b.run / "targets/sda/state").write_text("writing\n")
    assert b.sh(rec("sda")).stdout.strip().endswith("|-1")      # never the old number
    assert (b.gui / "used/sda.dirty").exists()
    shutil.rmtree(b.run / "targets")                          # idle again
    b.sh('disk_used_scan')
    assert not b.sh(rec("sda")).stdout.strip().endswith("|-1")


def test_a_waiting_or_skipped_drawer_keeps_its_number_it_was_not_written(tmp_path):
    b = box_with_disk(tmp_path); b.sh('disk_used_scan')
    for state in ("waiting", "skipped", "asking"):
        (b.run / "targets/sda").mkdir(parents=True, exist_ok=True); (b.run / "targets/sda/state").write_text(state + "\n")
        assert not b.sh(rec("sda")).stdout.strip().endswith("|-1"), state


def test_the_scan_is_started_only_for_an_unmeasured_disk_with_no_round_and_no_scan_running(tmp_path):
    b = box_with_disk(tmp_path); spawns = b.gui / "spawns"
    b.sh(rec("sda")); assert spawns.read_text().split() == ["spawn"]                # unmeasured, idle: start it
    b.sh("echo $$ > \"$GUI_DIR/used/scan.pid\"; sleep 30 & echo $! > \"$GUI_DIR/used/scan.pid\"; " + rec("sda") + "; kill $!")
    assert spawns.read_text().split() == ["spawn"], "a second scan was started on top of a running one"
    (b.gui / "used/scan.pid").unlink()
    b.sh("disk_used_scan"); b.sh(rec("sda")); assert spawns.read_text().split() == ["spawn"], "a measured disk was scanned again"
    (b.run / "targets/sdz").mkdir(parents=True); (b.gui / "used/sda").unlink()
    b.sh(rec("sda")); assert spawns.read_text().split() == ["spawn"], "a scan was started while a round is open"


def test_the_bridge_loads_the_library_and_the_state_writer_asks_it():
    assert "diskused.sh" in (AGENT / "lib/guibridge.sh").read_text(encoding="utf-8")
    assert 'disk_used_records "$(list_disks)"' in (AGENT / "lib/clonergui.sh").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the screens

SIZE = 500_107_862_016
ROUND = f"""cloner=Win11 Lab 2026
drawer_count=3
cdrawer_slot=1|SATA 0|
cdrawer_slot=2|SATA 1|
cdrawer_slot=3|NVMe 03:00.0|
drawer=1|sda|done|85899345920|85899345920||-1|-1|1|SATA 0
drawer=2|sdb|writing|40000000000|85899345920||98000000|300|2|SATA 1
drawer=3|nvme0n1|waiting|0|85899345920||-1|-1|3|NVMe 03:00.0
cused=sda|{SIZE}|214748364800
cused=sdb|{SIZE}|-1
cused=nvme0n1|256060514304|61234567890
updated=1792249500
message=x|y
"""


@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    return _build_gui(tmp_path_factory.mktemp("gui2030"))


def words(binary: Path, tmp: Path, state: str | None, card: str, theme: str = "light") -> list[str]:
    """Every string the screen laid out for <card>, marks stripped (bidi isolates)."""
    log = tmp / f"text-{card}.log"; log.write_text("")
    cmd = [str(binary), "--png", posix(tmp / "p"), "--size", "1280x800", "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"]
    if state is not None:
        sf = tmp / "state.txt"; sf.write_text(state, encoding="utf-8", newline="\n"); cmd += ["--state", posix(sf)]
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGECTL_GUI_TEXTLOG=posix(log))
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    out, on = [], False
    for l in log.read_text(encoding="utf-8").splitlines():   # the run draws every card: take this one
        if l.startswith("@@ "):
            on = l == f"@@ {card}-{theme}"
        elif on:
            out.append(re.sub("[‎‏⁦-⁩]", "", l))
    assert out, f"no text logged for {card}-{theme}"
    return out


@GUI_BUILD
def test_a_round_card_says_the_connection_the_size_and_what_is_taken_and_never_sda(binary, tmp_path):
    w = words(binary, tmp_path, ROUND, "cloner")
    for conn in ("SATA 0", "SATA 1", "NVMe 03:00.0"):
        assert conn in w, (conn, w)
    assert not [l for l in w if l in ("sda", "sdb", "nvme0n1", "sdc")], "a device name on the screen"
    assert not [l for l in w if re.fullmatch(r"דיסק [123]", l)], "the drawer number said a second time as the name"
    assert "500 GB" in w and "256 GB" in w, w                                   # the size, in a round: it was missing
    assert "תפוס עכשיו: 61.2 GB" in w, w                                       # a waiting drawer: what it will erase
    assert "נכתב: 85.9 GB" in w, w                                             # a done drawer: what it wrote
    assert "נכתב עד כה: 40.0 GB" in w, w                                       # a writing one: what it has so far


IDLE = f"""message=x|y
disk_probe=drives
drawer_count=3
cdrawer_slot=1|SATA 0|
cdrawer_slot=2|SATA 1|
cdrawer_slot=3|SATA 2|
cdisk=1|sda|Samsung SSD 870 EVO|{SIZE}|passed||6.0|6.0||1|SATA 0
cdisk=2|sdb|WDC WD5000AAKX|{SIZE}|passed||6.0|6.0||2|SATA 1
cused=sda|{SIZE}|214748364800
cused=sdb|{SIZE}|-1
"""


@GUI_BUILD
def test_the_waiting_cloner_says_what_is_on_each_disk_before_a_round_and_never_sda(binary, tmp_path):
    """Before a round the cloner is on the standby screen: this is where "what will be erased" lives."""
    w = words(binary, tmp_path, IDLE, "standby")
    assert "SATA 0" in w and "SATA 1" in w and "SATA 2" in w, w                # the connection is the name, empty drawer too
    assert "תפוס עכשיו: 215 GB" in w and "תפוס עכשיו: לא נמדד" in w, w
    assert not [l for l in w if l in ("sda", "sdb")], "a device name on the screen"
    assert not [l for l in w if re.fullmatch(r"דיסק [123]", l)], "the drawer number said again as the name"
    assert "6.0 Gb/s" in w, w                                                  # the link kept its place, under the name


@GUI_BUILD
def test_an_operators_slot_name_still_wins_over_the_connection(binary, tmp_path):
    w = words(binary, tmp_path, IDLE.replace("cdrawer_slot=1|SATA 0|", "cdrawer_slot=1|SATA 0|למעלה"), "standby")
    assert "למעלה" in w and "דיסק 1 · SATA 0 · 6.0 Gb/s" in w, w


@GUI_BUILD
def test_nothing_measured_is_said_never_zero(binary, tmp_path):
    state = ROUND.replace("cused=nvme0n1|256060514304|61234567890", "cused=nvme0n1|256060514304|-1")
    w = words(binary, tmp_path, state, "cloner")
    assert "תפוס עכשיו: לא נמדד" in w, w
    assert not [l for l in w if l.startswith("תפוס עכשיו") and re.search(r"\b0 (MB|GB)", l)], w
    # the state of an agent without diskused.sh: no record at all -- the size is unknown, not "0 GB"
    old = "\n".join(l for l in ROUND.splitlines() if not l.startswith("cused=")) + "\n"
    w = words(binary, tmp_path, old, "cloner")
    assert "גודל הדיסק לא נקרא" in w and "תפוס עכשיו: לא נמדד" in w, w


@GUI_BUILD
@pytest.mark.parametrize("card", ["lcsource", "lcsource-usb", "lcprogress", "lcprogress-usb"])
def test_the_local_clone_and_the_stick_say_the_connection_and_no_device_name(binary, tmp_path, card):
    w = words(binary, tmp_path, None, card)
    assert "SATA 0" in w and "SATA 1" in w, w
    assert not [l for l in w if l in ("sda", "sdb", "sdc")], "a device name on the screen"
    assert not [l for l in w if re.fullmatch(r"דיסק [123]", l)], w
    assert any(l.startswith("500 GB") for l in w), w                           # the size (and the model after it)
    if card.startswith("lcsource"):
        assert any(l.startswith("עליו עכשיו:") and "212 GB בשימוש" in l for l in w), w   # what the write will erase
    else:
        assert "נכתב עד כה: 55.1 GB" in w, w                                  # what it has written
