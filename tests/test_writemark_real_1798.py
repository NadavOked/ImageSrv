"""#1798 on real sgdisk: the incomplete-write mark is a partition NAME, and the
original names come back exactly.

Real sgdisk, under busybox ash like the agent, on a sparse file the size of a
120GiB disk (the layout of test_sgdisk_one_call_real_1714.py: ESP, MSR,
Windows, Recovery in the tail). What a stub cannot prove:

* ``apply_gpt``'s one call names every partition ``imagesrv-incomplete`` --
  read back with ``sgdisk -i`` (``Partition name: '...'``), not from our args;
* a NEW process reads the same file and says ``incomplete`` (the reboot);
* ``expand_last`` rebuilds the candidate and the tail and they carry it again;
* ``writemark_clear`` with the source's names puts them back byte for byte --
  spaces, and the EMPTY name (``-c 3:``) that real sgdisk must accept;
* without a names file (a library image) every name is empty.

Linux only (sgdisk + busybox): the Testrunner.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_json_escape import BUSYBOX, bb, source_line
from test_sgdisk_one_call_real_1714 import DISK_GUID, SECTORS, _blank, _rows

SGDISK = shutil.which("sgdisk")

pytestmark = requires_native(
    ("busybox", BUSYBOX), ("sgdisk", SGDISK), posix=True,
    why="the mark is a GPT partition name -- only real sgdisk says what it wrote")

MARK = "imagesrv-incomplete"


def _name(img: Path, idx: int) -> str:
    out = subprocess.run([SGDISK, "-i", str(idx), str(img)], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL).stdout
    m = re.search(r"^Partition name: '(.*)'$", out, re.M)
    assert m, out
    return m.group(1)


def _run(tmp_path: Path, commands: str, run_dir: str = "run") -> str:
    plan = tmp_path / "plan.txt"
    plan.write_text("\n".join(_rows()) + "\n", encoding="utf-8")
    sysblock = tmp_path / "sys" / "block" / "sda"
    sysblock.mkdir(parents=True, exist_ok=True)
    (sysblock / "size").write_text(f"{SECTORS}\n")
    (tmp_path / run_dir).mkdir(exist_ok=True)
    body = (
        f'RUN_DIR="{(tmp_path / run_dir).as_posix()}"; LOG_FILE="{(tmp_path / "log").as_posix()}"\n'
        f'DEVROOT="{(tmp_path / "dev").as_posix()}"; SYSROOT="{tmp_path.as_posix()}"\n'
        'log() { echo "LOG $*" >> "$LOG_FILE"; }\n'
        + source_line("waits.sh", "restore.sh", "expand.sh", "expandshare.sh", "writemark.sh")
        + f'manifest_plan() {{ cat "{plan.as_posix()}"; }}\n'
        f'json_get() {{ case "$2" in .sector_size) echo 512 ;; .scheme) echo gpt ;; '
        f'.disk_guid) echo {DISK_GUID} ;; *) echo null ;; esac; }}\n'
        f'blockdev() {{ case "$1" in --getss) echo 512 ;; --getsz) echo {SECTORS} ;; *) return 0 ;; esac; }}\n'
        'target_write_started() { :; }\n'
        'verify_table() { :; }\n'
        'share_tail() { printf "%s\\n" "$4"; }   # #1897: no shrunk tail here; test_expand_share_1897.py\n'
        + commands
    )
    return bb(tmp_path, body).strip()


def test_the_mark_is_on_every_partition_and_survives_the_reboot(tmp_path):
    img = _blank(tmp_path / "dev" / "sda")
    out = _run(tmp_path, 'apply_gpt sda m.json && echo OK; writemark_state sda\n')
    assert out.splitlines()[-2:] == ["OK", "incomplete"], (tmp_path / "log").read_text()
    assert [_name(img, i) for i in (1, 2, 3, 4)] == [MARK] * 4
    # the reboot: another process, an empty RUN_DIR -- the disk itself says it
    assert _run(tmp_path, "writemark_state sda\n", run_dir="after-reboot") == "incomplete"


def test_the_expansion_carries_the_mark_on_what_it_rebuilt(tmp_path):
    img = _blank(tmp_path / "dev" / "sda")
    out = _run(tmp_path, 'apply_gpt sda m.json && expand_last sda m.json && echo OK\n')
    assert out.splitlines()[-1] == "OK", (tmp_path / "log").read_text()
    assert (tmp_path / "run" / "targets" / "sda" / "expanded").read_text().strip() == "3|ntfs"
    assert [_name(img, i) for i in (1, 2, 3, 4)] == [MARK] * 4


def test_the_sources_names_come_back_exactly_and_an_empty_one_too(tmp_path):
    img = _blank(tmp_path / "dev" / "sda")
    names = tmp_path / "entries"
    names.write_text("1|EFI system partition|0\n2|Microsoft reserved partition|0\n3||0\n"
                     "4|Basic data partition|8000000000000001\n", encoding="utf-8")
    out = _run(tmp_path, f'apply_gpt sda m.json && writemark_clear sda "{names.as_posix()}" && echo OK; '
                         'writemark_state sda\n')
    assert out.splitlines()[-2:] == ["OK", "clean"], (tmp_path / "log").read_text()
    assert [_name(img, i) for i in (1, 2, 3, 4)] == [
        "EFI system partition", "Microsoft reserved partition", "", "Basic data partition"]


def test_a_library_image_gets_empty_names_like_its_manifest(tmp_path):
    img = _blank(tmp_path / "dev" / "sda")
    out = _run(tmp_path, 'apply_gpt sda m.json && writemark_clear sda && echo OK; writemark_state sda\n')
    assert out.splitlines()[-2:] == ["OK", "clean"], (tmp_path / "log").read_text()
    assert [_name(img, i) for i in (1, 2, 3, 4)] == ["", "", "", ""]
