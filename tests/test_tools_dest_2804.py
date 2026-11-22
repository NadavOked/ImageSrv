"""#2804: the toolbox contract the GUI reads, and the destination rules.

* `$GUI_DIR/tools`: `id|domain|title|risk|args|what` -- domain from a closed
  vocabulary, args `disk:<label>` / `part:<label>` / a text label / empty,
  `what` the catalog's own sentence (server/tools_catalog.json).
* `$GUI_DIR/tool-parts`: `dev|disk|size_bytes|fstype|label` per partition,
  never the clone stick; no file = not read.
* Nadav 31/10: a tool that saves files is missing -- not grey -- on a machine
  that has nowhere to save them (today: every machine with a server).
* The toolbox is off while a disk of this machine is written (mockup 14), and
  on the stick it is on only when the packed selection has a tool to show.
* The cloner and the stick sign nobody in: tool-list / tool-run are handled
  there before the sign-in check, and only there.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from native import requires_native
from test_tools_carve_2804 import LIB, ROOT, SIX, fake, room, sh

CATALOG = json.loads((ROOT / "server/tools_catalog.json").read_text(encoding="utf-8"))
WHAT = {t["id"]: t["what"] for t in CATALOG["tools"]}
CATALOG_IDS = list(WHAT)
DOMAINS = {"disk", "wipe", "net", "windows", "boot", "hw", "recover", "sec", "other", "combo"}
RUNNABLE_NOW = sorted(set(CATALOG_IDS) - {"testdisk", "photorec"})


def listing(case: Path, **kw):
    out = sh(case, "tools_list; echo rc=$?", **kw)
    rows = [l for l in out.stdout.splitlines() if "|" in l]
    return out, rows


# --- missing, not grey ---------------------------------------------------------------------

def test_with_a_server_the_six_are_not_listed_and_are_named_once(tmp_path):
    case = room(tmp_path, CATALOG_IDS)
    out, rows = listing(case, mode="normal")
    ids = [r.split("|")[0] for r in rows]
    assert not set(ids) & set(SIX), ids
    assert sorted(ids) == sorted(set(RUNNABLE_NOW) - set(SIX))
    named = [l for l in out.stderr.splitlines() if l.startswith("tools: not offered")]
    assert len(named) == 1, out.stderr
    assert "אין עדיין ערוץ שמירה לשרת" in named[0] and all(f" {t}" in named[0] for t in SIX)


def test_on_the_stick_with_its_data_partition_all_runnable_ids_are_listed(tmp_path):
    case = room(tmp_path, CATALOG_IDS)
    out, rows = listing(case)
    assert sorted(r.split("|")[0] for r in rows) == RUNNABLE_NOW, out.stderr
    assert "not offered" not in out.stderr


def test_on_the_stick_without_its_data_partition_the_six_are_missing(tmp_path):
    case = room(tmp_path, CATALOG_IDS)
    (case / "run/usb.data").write_text("failed\n")
    out, rows = listing(case)
    assert not {r.split("|")[0] for r in rows} & set(SIX)
    assert "מחיצת נתונים" in out.stderr


# --- the row contract ------------------------------------------------------------------------

def test_every_row_keeps_the_contract_the_gui_reads(tmp_path):
    case = room(tmp_path, CATALOG_IDS)
    _, rows = listing(case)
    assert rows
    for row in rows:
        f = row.split("|")
        assert len(f) == 6, row
        tid, dom, title, risk, args, what = f
        assert dom in DOMAINS, row
        assert risk in {"ro", "rw", "destroy"}, row
        assert not args.startswith("/dev/"), row           # a picker, not a device name to type
        if args.startswith(("disk:", "part:")):
            assert args.split(":", 1)[1].strip(), row      # the picker's label
        assert what == WHAT[tid], (tid, what)
        assert risk == next(t["risk"] for t in CATALOG["tools"] if t["id"] == tid), row


@pytest.mark.parametrize("tid,domain,kind", [
    ("disk-blkdiscard", "wipe", "disk"), ("disk-dd-zero", "wipe", "disk"), ("nwipe", "wipe", "disk"),
    ("nvme-sanitize", "wipe", "disk"), ("nvme-sanitize-status", "wipe", "disk"),
    ("zap-partition-table", "wipe", "disk"), ("tpm-clear", "wipe", ""),
    ("ntfs-recover-scan", "recover", "part"), ("unsquashfs", "recover", "text"), ("bsdtar", "recover", "text"),
    ("scalpel", "recover", "disk"), ("foremost", "recover", "disk"), ("extundelete", "recover", "part"),
    ("magicrescue", "recover", "disk"), ("ddrescue-image", "disk", "disk"), ("safecopy", "disk", "disk"),
    ("esp-fsck-repair", "boot", "part"), ("windows-ntfs-cat", "windows", "text"), ("win-users", "windows", "text"),
])
def test_domain_and_picker_per_tool(tmp_path, tid, domain, kind):
    """Mockup 14 (Nadav 31/10): erasers under "מחיקת דיסקים", ddrescue/safecopy
    under "דיסק" (03-01), esp-fsck-repair under "אתחול" (03-05)."""
    case = room(tmp_path, [tid])
    _, rows = listing(case)
    [row] = rows
    _, dom, _, _, args, _ = row.split("|")
    assert dom == domain
    got = "" if not args else args.split(":", 1)[0] if args.startswith(("disk:", "part:")) else "text"
    assert got == kind, row


def test_an_arg_the_gui_sends_for_a_partition_passes_the_runs_filter():
    """tools_gui_run lets only A-Za-z0-9_./:@- through; /dev/<name> is inside it."""
    src = (LIB / "tools.sh").read_text(encoding="utf-8")
    assert "*[!A-Za-z0-9_./:@-]*) return 1" in src


# --- the partition picker ---------------------------------------------------------------------

BLKID = r'''
dev=""; for a in "$@"; do dev=$a; done
key=""; prev=""; for a in "$@"; do [ "$prev" = -s ] && key=$a; prev=$a; done
f="$CASE/blkid.${dev##*/}.$key"
[ -f "$f.rc" ] && exit "$(cat "$f.rc")"
cat "$f" 2>/dev/null || exit 2
'''


def test_tool_parts_lists_each_partition_but_never_the_stick(tmp_path):
    case = room(tmp_path)
    fake(case / "bin", "blkid", BLKID)
    (case / "blkid.sda1.TYPE").write_text("ntfs")
    (case / "blkid.sda1.LABEL").write_text("Win|dows")
    (case / "blkid.sdb2.TYPE").write_text("vfat")
    (case / "gui").mkdir()
    out = sh(case, f'GUI_DIR={shlex.quote((case / "gui").as_posix())}; tools_gui_parts; echo rc=$?')
    assert out.stdout.splitlines()[-1] == "rc=0", out.stderr
    lines = (case / "gui/tool-parts").read_text().splitlines()
    assert lines == [f"/dev/sda1|/dev/sda|{4 * 512}|ntfs|Win dows"], lines


@pytest.mark.parametrize("rc,shown", [("2", ""), ("4", "?")])
def test_tool_parts_tells_no_filesystem_from_could_not_read(tmp_path, rc, shown):
    case = room(tmp_path)
    fake(case / "bin", "blkid", BLKID)
    (case / "blkid.sda1.TYPE.rc").write_text(rc)
    (case / "gui").mkdir()
    sh(case, f'GUI_DIR={shlex.quote((case / "gui").as_posix())}; tools_gui_parts')
    [line] = (case / "gui/tool-parts").read_text().splitlines()
    assert line.split("|")[3] == shown, line


def test_tool_parts_unreadable_is_no_file_not_an_empty_one(tmp_path):
    case = room(tmp_path)
    (case / "gui").mkdir()
    (case / "gui/tool-parts").write_text("stale\n")
    out = sh(case, f'GUI_DIR={shlex.quote((case / "gui").as_posix())}; SYSROOT="$CASE/nowhere"; tools_gui_parts; echo rc=$?')
    assert out.stdout.splitlines()[-1] == "rc=1"
    assert not (case / "gui/tool-parts").exists()


def test_tool_list_writes_both_files(tmp_path):
    case = room(tmp_path, ["scalpel"])
    fake(case / "bin", "blkid", BLKID)
    (case / "gui").mkdir()
    out = sh(case, f'GUI_DIR={shlex.quote((case / "gui").as_posix())}; tools_gui_list; echo rc=$?')
    assert out.stdout.splitlines()[-1] == "rc=0", out.stderr
    tools = (case / "gui/tools").read_text(encoding="utf-8").splitlines()
    assert tools[0] == "machine=lab-pc" and tools[1].startswith("scalpel|recover|")
    assert (case / "gui/tool-parts").read_text().startswith("/dev/sda1|/dev/sda|")


# --- off while writing ---------------------------------------------------------------------------

@pytest.mark.parametrize("where,value", [("lc/phase", "copying"), ("lc/phase", "verifying"),
                                         ("targets/sdc/state", "writing"), ("targets/sdc/state", "waiting")])
def test_nothing_runs_while_a_disk_here_is_written(tmp_path, where, value):
    case = room(tmp_path)
    f = case / "run" / where
    f.parent.mkdir(parents=True)
    f.write_text(value + "\n")
    out = sh(case, "tools_run scalpel /dev/sda; echo rc=$?")
    assert out.stdout.splitlines()[-1] == "rc=2", out.stdout
    assert (case / "called").read_text() == ""
    assert "המחשב כותב עכשיו לדיסק" in (case / "run/tool-scalpel.out").read_text(encoding="utf-8")


@pytest.mark.parametrize("where,value", [("lc/phase", "idle"), ("lc/phase", "done"),
                                         ("targets/sdc/state", "done"), ("targets/sdc/state", "failed")])
def test_a_finished_write_does_not_hold_the_toolbox(tmp_path, where, value):
    case = room(tmp_path)
    f = case / "run" / where
    f.parent.mkdir(parents=True)
    f.write_text(value + "\n")
    out = sh(case, "tools_run scalpel /dev/sda; echo rc=$?")
    assert out.stdout.splitlines()[-1] == "rc=0", (case / "run/tool-scalpel.out").read_text(encoding="utf-8")


def test_a_drawer_state_that_cannot_be_read_counts_as_writing(tmp_path):
    case = room(tmp_path)
    (case / "run/targets/sdc/state").mkdir(parents=True)   # cat fails on a folder
    out = sh(case, "tools_busy; echo rc=$?")
    assert out.stdout.splitlines()[-1] == "rc=0"


# --- the button on the stick, the cloner's records, and the dispatch -------------------------------

ITEMS = f'. "$LIB_DIR/jsonq.sh"; . "$LIB_DIR/buildmenuitems.sh"; '


@pytest.mark.parametrize("selection,data,on", [
    (["scalpel", "disk-blkdiscard"], "mounted", "1"),
    (["scalpel"], "mounted", "1"),
    (["scalpel"], "none", "0"),                 # its only tool needs the data partition
    (["testdisk"], "mounted", "0"),             # no module runs it
    ([], "mounted", "0"),
])
def test_the_button_on_the_stick_follows_what_the_packed_selection_can_show(tmp_path, selection, data, on):
    case = room(tmp_path, selection)
    (case / "run/usb.data").write_text(data + "\n")
    out = sh(case, ITEMS + "tools_menu_record")
    assert out.stdout.strip().splitlines()[-1] == f"menu_tools={on}", out.stderr


def test_the_button_on_the_stick_without_a_selection_file_is_off(tmp_path):
    case = room(tmp_path)
    (case / "selection.json").unlink()
    out = sh(case, ITEMS + "tools_menu_record")
    assert out.stdout.strip().splitlines()[-1] == "menu_tools=0"


@requires_native("jq")
@pytest.mark.parametrize("hello,on", [({"tools": True}, "1"), ({"tools": False}, "0"), ({}, "0")])
def test_with_a_server_the_button_still_follows_the_hello(tmp_path, hello, on):
    case = room(tmp_path)
    (case / "run/response.json").write_text(json.dumps(hello))
    out = sh(case, ITEMS + "tools_menu_record", mode="normal")
    assert out.stdout.strip().splitlines()[-1] == f"menu_tools={on}", out.stderr


@pytest.mark.parametrize("name", ["clonergui.sh", "lcgui.sh"])
def test_the_cloner_and_local_clone_screens_write_the_record(name):
    src = (LIB / name).read_text(encoding="utf-8")
    assert "tools_menu_record >>" in src


DISPATCH = '''. "$LIB_DIR/jsonq.sh"; . "$LIB_DIR/buildmenuitems.sh"; . "$LIB_DIR/guibridge.sh"
GUI_DIR="$CASE/gui"; mkdir -p "$GUI_DIR"; token=tool-list
'''


def test_the_cloner_screen_lists_tools_without_a_sign_in(tmp_path):
    case = room(tmp_path, ["scalpel"])
    out = sh(case, DISPATCH + "GUI_SCREEN=cloner gui_dispatch; echo rc=$?")
    assert out.stdout.splitlines()[-1] == "rc=0", out.stderr
    assert (case / "gui/tools").read_text(encoding="utf-8").splitlines()[1].startswith("scalpel|")


def test_the_build_machine_still_needs_its_sign_in(tmp_path):
    case = room(tmp_path, ["scalpel"])
    out = sh(case, DISPATCH + "GUI_SCREEN= gui_dispatch; echo rc=$?")
    assert out.stdout.splitlines()[-1] == "rc=1"
    assert not (case / "gui/tools").exists()
