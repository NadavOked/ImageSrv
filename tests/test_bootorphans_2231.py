"""#2231 -- dead UEFI Boot#### entries after a restore or a clone (`agent/lib/postdeploy/bootorphans.sh`).

A restored disk brings its own PARTUUIDs; the board's NVRAM still holds entries
for the disk that used to be there. ``bootorphans_scan`` only LISTS the dead ones
(``bootorphans.json``); ``bootorphans_clean`` deletes exactly the numbers it is
given, and reads the table back.

Runs on the real sh with a fake ``efibootmgr`` whose "NVRAM" is a file and a fake
``blkid`` that answers per partition node. What is asserted is the evidence, not the
exit code of the write (principle 5): the entry must be gone from the table, BootOrder
must have only lost it (PXE stays first), no other entry may have moved -- and a scan
that could not read something says "unchecked", never "everything is dead".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_captured_stdout import stub

pytestmark = requires_native(("bash", BASH))

ESP = "3a7f1c2e-9b4d-4e6f-8a10-2b3c4d5e6f70"
OLD = "11111111-2222-3333-4444-555555555555"
OLD2 = "66666666-7777-8888-9999-aaaaaaaaaaaa"

#: The tab is the real efibootmgr -v separator between the label and the device path.
WIN = f"Boot0000* Windows Boot Manager\tHD(1,GPT,{ESP.upper()},0x800,0x32000)/File(\\EFI\\MICROSOFT\\BOOT\\BOOTMGFW.EFI)"
PXE = "Boot0001* UEFI PXEv4 (MAC:B42E99071AC4)\tPciRoot(0x0)/Pci(0x1,0x0)/MAC(b42e99071ac4,1)/IPv4(0.0.0.00.0.0.0,0,0)"
UBUNTU = f"Boot0003* ubuntu\tHD(1,GPT,{OLD},0x800,0x100000)/File(\\EFI\\ubuntu\\shimx64.efi)"
DEAD2 = f"Boot0004* Windows Boot Manager\tHD(2,GPT,{OLD2.upper()},0x800,0x32000)/File(\\EFI\\MICROSOFT\\BOOT\\BOOTMGFW.EFI)"
USB = "Boot0005* USB stick\tPciRoot(0x0)/Pci(0x14,0x0)/USB(2,0)"
MBR = "Boot0006* old BIOS disk\tHD(1,MBR,0xdeadbeef,0x800,0x100000)"
HEADER = ["BootCurrent: 0001", "Timeout: 2 seconds"]

EFIBOOTMGR = r'''
echo "$*" >> "$FAKE_NVRAM.calls"
[ "${FAKE_V:-ok}" = fail ] && [ "$1" = -v ] && { echo "EFI variables are not supported" >&2; exit 2; }
case "$1" in
  -v) cat "$FAKE_NVRAM"; exit 0 ;;
  "") sed 's/\t.*//' "$FAKE_NVRAM"; exit 0 ;;
  -B)
    [ "$2" = -b ] || exit 64
    n=$3
    [ "${FAKE_B:-ok}" = fail ] && { echo "Could not delete Boot$n" >&2; exit 5; }
    [ "${FAKE_B:-ok}" = drop ] && exit 0
    grep -v "^Boot$n" "$FAKE_NVRAM" > "$FAKE_NVRAM.tmp"
    # the real tool takes the entry out of BootOrder as well
    awk -v n="$n" -v mode="${FAKE_B:-ok}" '
      /^BootOrder:/ { sub(/^BootOrder: */, ""); m = split($0, a, ","); out = ""
        for (i = 1; i <= m; i++) if (a[i] != n) out = out (out == "" ? "" : ",") a[i]
        if (mode == "reorder") { k = split(out, b, ","); out = ""; for (i = k; i >= 1; i--) out = out (out == "" ? "" : ",") b[i] }
        print "BootOrder: " out; next }
      mode == "eat" && /^Boot0000/ { next }
      { print }' "$FAKE_NVRAM.tmp" > "$FAKE_NVRAM"
    exit 0 ;;
  *) echo "unexpected efibootmgr $*" >&2; exit 64 ;;
esac
'''

#: blkid -s PARTUUID -o value <node>: the uuid of that node from $FAKE_UUIDS ("node uuid");
#: no line = the partition has none (rc 2); "ERR" = blkid could not read it (rc 4).
BLKID = r'''
node=${5##*/}
line=$(grep "^$node " "$FAKE_UUIDS") || exit 2
u=${line#* }
[ "$u" = ERR ] && exit 4
echo "$u" | tr 'a-f' 'A-F'
'''


class Box:
    def __init__(self, tmp_path: Path, *, nvram: tuple[str, ...] = (WIN, PXE, UBUNTU),
                 order: str = "0001,0000,0003", uuids: tuple[str, ...] | None = None,
                 efivars: bool = True, env: str = ""):
        self.tmp = tmp_path
        self.run = tmp_path / "run"
        self.run.mkdir()
        self.sys = tmp_path / "sys"
        (self.sys / "sys/class/block").mkdir(parents=True)
        if efivars:
            d = self.sys / "sys/firmware/efi/efivars"
            d.mkdir(parents=True)
            (d / "BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(b"\x07\x00\x00\x00\x01\x00")
        rows = uuids if uuids is not None else (f"sda1 {ESP}", "sda2 9a9a9a9a-0000-0000-0000-000000000002")
        for row in rows:
            node = row.split()[0]
            (self.sys / "sys/class/block" / node).mkdir()
            (self.sys / "sys/class/block" / node / "partition").write_text("1\n")
        self.uuids = tmp_path / "uuids"
        self.uuids.write_text("".join(r + "\n" for r in rows), newline="\n")
        self.nvram = tmp_path / "nvram"
        lines = list(HEADER) + ([f"BootOrder: {order}"] if order else []) + list(nvram)
        self.nvram.write_text("".join(l + "\n" for l in lines), newline="\n")
        stubs = tmp_path / "stubs"
        stubs.mkdir()
        self.script = (stub(stubs / "efibootmgr", EFIBOOTMGR) + stub(stubs / "blkid", BLKID)
                       + f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
                       f'export RUN_DIR={posix(self.run)!r} DEVROOT={posix(self.run)!r}/dev '
                       f'LOG_FILE={posix(self.run / "agent.log")!r} SYSROOT={posix(self.sys)!r} '
                       f'FAKE_NVRAM={posix(self.nvram)!r} FAKE_UUIDS={posix(self.uuids)!r} {env}; '
                       f'. {posix(AGENT)}/lib/core/common.sh; '
                       f'. {posix(AGENT)}/lib/postdeploy/bootorphans.sh; ')

    def call(self, command: str) -> int:
        out = subprocess.run(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + self.script + command + '; echo "rc=$?"'],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(AGENT.parent), stdin=subprocess.DEVNULL,
        )
        assert out.returncode == 0, out.stderr
        return int(out.stdout.strip().splitlines()[-1].removeprefix("rc="))

    def scan(self) -> dict:
        assert self.call("bootorphans_scan") == 0       # a scan never fails
        return json.loads((self.run / "bootorphans.json").read_text(encoding="utf-8"))

    def clean(self, *numbers: str) -> tuple[int, dict]:
        rc = self.call("bootorphans_clean " + " ".join(f"'{n}'" for n in numbers))
        return rc, json.loads((self.run / "bootorphans-clean.json").read_text(encoding="utf-8"))

    def table(self) -> list[str]:
        return self.nvram.read_text().splitlines()

    def calls(self) -> list[str]:
        p = Path(str(self.nvram) + ".calls")
        return p.read_text().splitlines() if p.exists() else []

    def deletes(self) -> list[str]:
        return [c for c in self.calls() if c.startswith("-B")]


# --- scan: only positive evidence makes an entry dead -------------------------


def test_an_entry_whose_partuuid_is_on_no_disk_is_listed_and_nothing_is_touched(tmp_path):
    box = Box(tmp_path)
    before = box.table()
    assert box.scan() == {"state": "found", "entries": [{"num": "0003", "partuuid": OLD, "label": "ubuntu"}]}
    assert box.table() == before and box.deletes() == []     # read only


def test_the_partuuid_is_compared_case_insensitively(tmp_path):
    # Windows writes its own entry in capitals; blkid prints what is stored. Neither is "dead".
    box = Box(tmp_path, nvram=(WIN, PXE))
    assert box.scan() == {"state": "none"}


def test_pxe_usb_mbr_and_fileless_entries_are_never_candidates(tmp_path):
    box = Box(tmp_path, nvram=(WIN, PXE, USB, MBR), order="0001,0000,0005,0006")
    assert box.scan() == {"state": "none"}


def test_two_dead_entries_are_both_listed_with_their_label(tmp_path):
    box = Box(tmp_path, nvram=(WIN, PXE, UBUNTU, DEAD2), order="0001,0000,0003,0004")
    nums = [e["num"] for e in box.scan()["entries"]]
    assert nums == ["0003", "0004"]


def test_a_label_with_a_quote_does_not_break_the_json(tmp_path):
    weird = f'Boot0003* my "disk" | x\tHD(1,GPT,{OLD},0x800,0x1)/File(\\a)'
    box = Box(tmp_path, nvram=(WIN, PXE, weird))
    entry = box.scan()["entries"][0]                         # json.loads would have raised on a broken line
    assert entry["num"] == "0003" and entry["label"] == 'my "disk"   x'    # the "|" of the label became a space


# --- scan: "could not look" is not "all dead" (principle 5) -------------------


def test_a_partition_blkid_cannot_read_makes_the_scan_unchecked_not_all_dead(tmp_path):
    box = Box(tmp_path, uuids=(f"sda1 {ESP}", "sdb1 ERR"))
    result = box.scan()
    assert result["state"] == "unchecked" and "PARTUUID" in result["reason"]
    assert "entries" not in result


def test_no_partition_at_all_is_unchecked(tmp_path):
    box = Box(tmp_path, uuids=())
    assert box.scan()["state"] == "unchecked"


def test_a_failed_efibootmgr_is_unchecked_with_its_rc(tmp_path):
    box = Box(tmp_path, env="FAKE_V=fail")
    result = box.scan()
    assert result["state"] == "unchecked" and "rc=2" in result["reason"]


def test_no_efivars_is_skipped_by_name(tmp_path):
    box = Box(tmp_path, efivars=False)
    assert box.scan() == {"state": "skipped", "reason": "no_efivars"}
    assert box.calls() == []


# --- clean: the approval is the argument, and the table is read back ------------


def test_without_an_approval_nothing_is_deleted(tmp_path):
    box = Box(tmp_path)
    rc = box.call("bootorphans_clean")
    assert rc == 3 and box.deletes() == []
    assert json.loads((box.run / "bootorphans-clean.json").read_text()) == {"state": "refused", "reason": "no_approval"}


def test_an_approved_dead_entry_is_deleted_boot_order_only_loses_it(tmp_path):
    box = Box(tmp_path)
    rc, result = box.clean("0003")
    assert rc == 0 and result == {"state": "cleaned", "deleted": ["0003"]}
    table = box.table()
    assert not any(l.startswith("Boot0003") for l in table)
    assert "BootOrder: 0001,0000" in table                   # PXE (0001) is still first
    assert WIN in table and PXE in table                     # nothing else moved


def test_only_the_approved_one_of_two_dead_entries_is_deleted(tmp_path):
    box = Box(tmp_path, nvram=(WIN, PXE, UBUNTU, DEAD2), order="0001,0000,0003,0004")
    rc, result = box.clean("0004")
    assert rc == 0 and result["deleted"] == ["0004"]
    assert any(l.startswith("Boot0003") for l in box.table())
    assert box.deletes() == ["-B -b 0004"]


def test_a_lower_case_number_is_accepted_and_a_duplicate_is_deleted_once(tmp_path):
    nvram = (WIN, PXE, f"Boot00AB* x\tHD(1,GPT,{OLD},0x800,0x1)/File(\\a)")
    box = Box(tmp_path, nvram=nvram, order="0001,0000,00AB")
    rc, result = box.clean("00ab", "00AB")
    assert rc == 0 and result["deleted"] == ["00AB"]
    assert box.deletes() == ["-B -b 00AB"]


@pytest.mark.parametrize("approved", ["0000", "0001", "0005"])
def test_a_live_pxe_or_unknown_entry_is_refused_even_when_approved(tmp_path, approved):
    # 0000 = Windows on a disk that is here, 0001 = PXE, 0005 = no such entry
    box = Box(tmp_path, nvram=(WIN, PXE, UBUNTU))
    rc, result = box.clean(approved)
    assert rc == 3 and result["state"] == "refused" and "not a dead entry" in result["reason"]
    assert box.deletes() == []


def test_one_bad_number_in_the_list_deletes_nothing_at_all(tmp_path):
    box = Box(tmp_path)
    rc, result = box.clean("0003", "0000")
    assert rc == 3 and box.deletes() == []
    assert any(l.startswith("Boot0003") for l in box.table())


@pytest.mark.parametrize("bad", ["3", "00003", "0003;touch x", "zzzz", "../0003"])
def test_a_malformed_number_is_refused_before_anything_runs(tmp_path, bad):
    box = Box(tmp_path)
    rc, result = box.clean(bad)
    assert rc == 3 and result["state"] == "refused" and result["reason"].startswith("bad_number")
    assert box.deletes() == []


def test_a_scan_that_could_not_look_refuses_the_delete(tmp_path):
    box = Box(tmp_path, uuids=(f"sda1 {ESP}", "sdb1 ERR"))
    rc, result = box.clean("0003")
    assert rc == 3 and "not checked" in result["reason"] and box.deletes() == []


# --- clean: the read-back is the evidence ---------------------------------------


def test_a_delete_that_exits_zero_but_leaves_the_entry_is_a_failure(tmp_path):
    box = Box(tmp_path, env="FAKE_B=drop")
    rc, result = box.clean("0003")
    assert rc == 1 and result["state"] == "failed" and "still in the table" in result["error"]


def test_a_delete_that_fails_is_a_failure_with_the_rc(tmp_path):
    box = Box(tmp_path, env="FAKE_B=fail")
    rc, result = box.clean("0003")
    assert rc == 1 and result["state"] == "failed" and "rc=5" in result["error"]


def test_a_firmware_that_reorders_boot_order_is_a_failure_naming_it(tmp_path):
    # PXE must stay first: a delete that also reorders is not "cleaned"
    box = Box(tmp_path, env="FAKE_B=reorder")
    rc, result = box.clean("0003")
    assert rc == 1 and "BootOrder" in result["error"]


def test_a_delete_that_took_another_entry_with_it_is_a_failure(tmp_path):
    box = Box(tmp_path, env="FAKE_B=eat")
    rc, result = box.clean("0003")
    assert rc == 1 and "another Boot####" in result["error"]


# --- wiring ----------------------------------------------------------------------


def test_the_restore_hook_only_scans_and_the_agent_loads_the_library():
    post = (AGENT / "lib" / "postdeploy" / "postdeploy.sh").read_text(encoding="utf-8")
    body = post[post.index("stage_drivers() {") : post.index("_stage_drivers() {")]
    assert body.index('ensure_boot_entry "$@"') < body.index("bootorphans_scan")
    assert "bootorphans_clean" not in post                   # deleting is never part of the restore
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert main.index('"$LIB_DIR/bootorphans.sh"') < main.index('"$LIB_DIR/postdeploy.sh"')


def test_the_library_never_touches_boot_order_or_boot_next():
    text = (AGENT / "lib" / "postdeploy" / "bootorphans.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.lstrip().startswith("efibootmgr"):
            for flag in (" -o ", " -O", " -n ", " -N", " -c ", " -C "):
                assert flag not in line, line
    assert "efibootmgr -B -b" in text                        # the only write
