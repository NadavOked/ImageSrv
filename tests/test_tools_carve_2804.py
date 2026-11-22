"""#2804: the six catalog ids that save files -- scalpel, foremost, extundelete,
magicrescue (+recoverjpeg), ddrescue-image, safecopy -- run through the real
framework (tools_run) with fake binaries on PATH, on a fake machine booted
from the USB stick: the source is /dev/sda (a disk with a partition), the
destination the stick's data partition (sdb2, mounted at $CASE/usbdata).

What is proved: the exact non-interactive command with an explicit source and
an explicit output folder (never RECOVERED_FILES/ or the current folder),
stdin closed, the verdict from what landed at the destination (files counted
there, ddrescue's mapfile, safecopy's image size) and not from the tool's
word, a resumed run in the same per-serial folder, and every refusal
(no tool, the stick as source, a partition where a disk is needed, a full
or unknown destination) as rc 2 with nothing run. No real disk is touched.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native

ROOT = Path(__file__).resolve().parents[1]
LIB = flat_lib()
BASH = shutil.which("bash")
pytestmark = requires_native(("bash", BASH))

SIX = ["scalpel", "foremost", "extundelete", "magicrescue", "ddrescue-image", "safecopy"]
SRC_SECTORS = 8           # /dev/sda: 4096 bytes -- small enough to write as a fake image
SRC_BYTES = SRC_SECTORS * 512

# A fake carver: records its arguments, says whether stdin was open, writes
# $CASE/nfiles files (default 3) into the folder it was given, exits rc.<name>.
CARVER = r'''
printf '%s %s\n' "$(basename "$0")" "$*" >> "$CASE/called"
if read -r _l; then echo "STDIN-OPEN $(basename "$0")" >> "$CASE/called"; fi
out=""; prev=""
for a in "$@"; do
    case "$prev" in -o|-d) out=$a ;; esac
    prev=$a
done
[ -n "$out" ] || { echo "no output folder" >&2; exit 64; }
mkdir -p "$out/sub" || exit 65
n=$(cat "$CASE/nfiles" 2>/dev/null || echo 3)
i=0; while [ "$i" -lt "$n" ]; do echo data > "$out/sub/f$i.jpg"; i=$((i + 1)); done
echo audit > "$out/audit.txt"
: > "$CASE/ran"
exit "$(cat "$CASE/rc.$(basename "$0")" 2>/dev/null || echo 0)"
'''

DF = r'''
kb=$(cat "$CASE/df.kb" 2>/dev/null || echo 99999999)
if [ -f "$CASE/ran" ] && [ -f "$CASE/df.after" ]; then kb=$(cat "$CASE/df.after"); fi
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfake 1 1 %s 1%% /x\n' "$kb"
'''

# ddrescue: the mapfile it would leave, from $CASE/map (a template), and an image.
DDRESCUE = r'''
printf 'ddrescue %s\n' "$*" >> "$CASE/called"
if read -r _l; then echo "STDIN-OPEN ddrescue" >> "$CASE/called"; fi
img=$3; map=$4
[ -f "$map" ] && echo "RESUMED" >> "$CASE/called"
printf 'x' > "$img"
cp "$CASE/map" "$map"
exit "$(cat "$CASE/rc.ddrescue" 2>/dev/null || echo 0)"
'''

# safecopy: one stage per call, by its --stageN; writes the image (source size)
# and stageN.badblocks in the current folder, as the real presets do.
SAFECOPY = r'''
printf 'safecopy %s (in %s)\n' "$*" "$(pwd)" >> "$CASE/called"
if read -r _l; then echo "STDIN-OPEN safecopy" >> "$CASE/called"; fi
stage=${1#--stage}
case "$stage" in 2) [ -f stage1.badblocks ] || exit 70 ;; 3) [ -f stage2.badblocks ] || exit 70 ;; esac
head -c "$(cat "$CASE/img.bytes")" /dev/zero > "$3"
cat "$CASE/bad.$stage" 2>/dev/null > "stage$stage.badblocks"
exit "$(cat "$CASE/rc.safecopy.$stage" 2>/dev/null || echo 0)"
'''


def fake(box: Path, name: str, body: str) -> None:
    target = box / name
    target.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    subprocess.run([BASH, "-c", f"chmod +x {shlex.quote(target.as_posix())}"], check=True,
                   stdin=subprocess.DEVNULL)   # #14: WinError 50 in a multi-file run


def room(tmp_path: Path, selection: list[str] | None = None) -> Path:
    for name in ("bin", "run", "usbdata", "sysroot/proc"):
        (tmp_path / name).mkdir(parents=True)
    blk = tmp_path / "sysroot/sys/block"
    for disk, part, size in (("sda", "sda1", SRC_SECTORS), ("sdb", "sdb2", 65536)):
        (blk / disk / "device").mkdir(parents=True)
        (blk / disk / "size").write_text(f"{size}\n")
        (blk / disk / "device/model").write_text(f"MODEL-{disk}\n")
        (blk / disk / "device/serial").write_text(f"SER-{disk}\n")
        (blk / disk / part).mkdir()
        (blk / disk / part / "partition").write_text("1\n")
        (blk / disk / part / "size").write_text(f"{size // 2}\n")
    (tmp_path / "run/usb-media.disks").write_text("sdb\n")
    (tmp_path / "run/usb.data").write_text("mounted\n")
    (tmp_path / "called").write_text("")
    (tmp_path / "img.bytes").write_text(f"{SRC_BYTES}\n")
    (tmp_path / "selection.json").write_text(json.dumps(
        {"schema": 1, "build": SIX if selection is None else selection, "student": []}))
    for name in ("scalpel", "foremost", "extundelete", "recoverjpeg", "magicrescue"):
        fake(tmp_path / "bin", name, CARVER)
    fake(tmp_path / "bin", "jpegtran", "exit 0")
    fake(tmp_path / "bin", "df", DF)
    fake(tmp_path / "bin", "ddrescue", DDRESCUE)
    fake(tmp_path / "bin", "safecopy", SAFECOPY)
    recipes = tmp_path / "recipes"; recipes.mkdir()
    for r in ("jpeg-jfif", "jpeg-exif"):
        (recipes / r).write_text("extension jpg\n")
    full_map(tmp_path)
    return tmp_path


def full_map(case: Path, blocks: list[tuple[int, int, str]] | None = None) -> None:
    """A ddrescue mapfile (its own format): comments, the status line, then blocks."""
    blocks = blocks if blocks is not None else [(0, SRC_BYTES, "+")]
    lines = ["# Mapfile. Created by GNU ddrescue version 1.29", "# current_pos  current_status  current_pass",
             "0x00000000     +               1", "#      pos        size  status"]
    lines += [f"0x{p:08X}  0x{s:08X}  {st}" for p, s, st in blocks]
    (case / "map").write_text("\n".join(lines) + "\n", newline="\n")   # ddrescue's own: LF


def prelude(case: Path, mounts: str | None = None, mode: str = "usbclone") -> str:
    mounts = mounts if mounts is not None else '"$SYSROOT/dev/sdb2" "$CASE/usbdata" vfat'
    return f'''
export CASE=$(cd {shlex.quote(case.as_posix())} && pwd)
export PATH="$CASE/bin:/usr/bin:/bin" RUN_DIR="$CASE/run" SYSROOT="$CASE/sysroot" LC="$CASE/run/lc"
export MACHINE_NAME=lab-pc LIB_DIR={shlex.quote(LIB.as_posix())} TOOLS_SELECTION="$CASE/selection.json"
export IMAGESRV_MODE={mode} USB_DATA_DIR="$CASE/usbdata" TOOLS_MR_RECIPES="$CASE/recipes"
printf '%s %s %s rw 0 0\\n' {mounts} > "$SYSROOT/proc/mounts"
printf '/dev/root / ext4 rw 0 0\\n' >> "$SYSROOT/proc/mounts"
. "$LIB_DIR/common.sh"
. "$LIB_DIR/tools.sh"
tools_load
'''


def sh(case: Path, body: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", prelude(case, **kw) + body], text=True, encoding="utf-8",
                          errors="replace", capture_output=True, stdin=subprocess.DEVNULL,
                          timeout=60, cwd=ROOT)


def run(case: Path, tool: str, arg: str = "/dev/sda", confirm: str = "lab-pc", **kw):
    """tools_run with a pipe on stdin: a tool that did not close it would read it."""
    out = sh(case, f"printf 'y\\ny\\n' | tools_run {tool} {shlex.quote(arg)} {confirm}; echo rc=$?", **kw)
    return out, out.stdout.splitlines()[-1] if out.stdout else ""


def output(case: Path, tool: str) -> str:
    return (case / "run" / f"tool-{tool}.out").read_text(encoding="utf-8")


def calls(case: Path) -> str:
    return (case / "called").read_text()


def dests(case: Path, tool: str) -> list[Path]:
    return sorted((case / "usbdata/recovered" / tool).glob("*"))


# --- the carvers ----------------------------------------------------------------------

CARVE = {"scalpel": "/dev/sda", "foremost": "/dev/sda", "extundelete": "/dev/sda1", "magicrescue": "/dev/sda"}


@pytest.mark.parametrize("tool", list(CARVE))
def test_a_carver_runs_its_exact_command_into_a_fresh_folder_and_counts_what_landed(tmp_path, tool):
    case = room(tmp_path)
    out, rc = run(case, tool, CARVE[tool])
    text = output(case, tool)
    assert rc == "rc=0", out.stderr + text
    [stamp] = dests(case, tool)
    dest = stamp / "out"
    c = calls(case)
    out_dir = rf"\S*/usbdata/recovered/{tool}/{stamp.name}/out"
    expected = {
        "scalpel": rf"^scalpel -c \S*/run/tool-scalpel\.conf -o {out_dir} /dev/sda$",
        "foremost": rf"^foremost -t all -i /dev/sda -o {out_dir}$",
        "extundelete": rf"^extundelete --restore-all -o {out_dir} /dev/sda1$",
        "magicrescue": rf"^magicrescue -d {out_dir}/magicrescue -r \S*/recipes/jpeg-jfif -r \S*/recipes/jpeg-exif /dev/sda$",
    }[tool]
    assert re.search(expected, c, flags=re.M), (expected, c)
    if tool == "magicrescue":
        assert re.search(rf"^recoverjpeg -o {out_dir}/recoverjpeg /dev/sda$", c, flags=re.M), c
    assert "STDIN-OPEN" not in c, c                       # </dev/null on every call
    assert "RECOVERED_FILES" not in c and "RECOVERED_FILES" not in text
    if tool != "foremost":
        assert c.strip().splitlines()[-1].endswith(CARVE[tool]), c   # the explicit source, last
    n = 6 if tool == "magicrescue" else 3                 # recoverjpeg + magicrescue
    assert f"נספרו ביעד: {n} קבצים" in text, text
    assert text.splitlines()[0].startswith("יעד השמירה: בדיסק-און-קי"), text
    assert str(dest).replace("\\", "/").split("/usbdata/")[1] in text
    assert len(list(dest.rglob("*.jpg"))) == n
    assert "מה זה אומר" in text


def test_scalpel_gets_a_config_with_types_because_debians_is_all_comments(tmp_path):
    case = room(tmp_path)
    run(case, "scalpel")
    conf = (case / "run/tool-scalpel.conf").read_text()
    rows = [l.split() for l in conf.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert {r[0] for r in rows} == {"jpg", "pdf", "zip"}, rows


@pytest.mark.parametrize("tool", list(CARVE))
def test_nothing_found_is_not_green(tmp_path, tool):
    case = room(tmp_path)
    (case / "nfiles").write_text("0")
    _, rc = run(case, tool, CARVE[tool])
    assert rc == "rc=1", output(case, tool)
    assert "לא חולץ אף קובץ" in output(case, tool)


@pytest.mark.parametrize("tool", list(CARVE))
def test_a_tool_that_failed_is_partial_even_with_files_saved(tmp_path, tool):
    case = room(tmp_path)
    binary = "recoverjpeg" if tool == "magicrescue" else tool
    (case / f"rc.{binary}").write_text("3")
    _, rc = run(case, tool, CARVE[tool])
    assert rc == "rc=2", output(case, tool)
    assert "חלקי" in output(case, tool)


@pytest.mark.parametrize("tool", list(CARVE))
def test_a_destination_that_filled_up_is_partial(tmp_path, tool):
    case = room(tmp_path)
    (case / "df.after").write_text("100")
    _, rc = run(case, tool, CARVE[tool])
    assert rc == "rc=2", output(case, tool)
    assert "היעד התמלא" in output(case, tool)


def test_magicrescue_without_its_recipes_says_so_and_is_partial(tmp_path):
    case = room(tmp_path)
    for r in (case / "recipes").iterdir():
        r.unlink()
    _, rc = run(case, "magicrescue")
    text = output(case, "magicrescue")
    assert rc == "rc=2", text
    assert "magicrescue לא הורץ" in text and "recoverjpeg -o " in calls(case)
    assert "magicrescue -d" not in calls(case)


def test_extundelete_reads_a_partition_and_refuses_a_whole_disk(tmp_path):
    case = room(tmp_path)
    _, rc = run(case, "extundelete", "/dev/sda")
    assert rc == "rc=2" and "קורא מחיצה" in output(case, "extundelete")
    assert calls(case) == ""


@pytest.mark.parametrize("tool", ["scalpel", "ddrescue-image", "safecopy"])
def test_a_partition_is_refused_where_a_disk_is_read(tmp_path, tool):
    case = room(tmp_path)
    _, rc = run(case, tool, "/dev/sda1")
    assert rc == "rc=2" and "דיסק שלם" in output(case, tool)
    assert calls(case) == ""


# --- refusals before anything runs (shared by all six) ---------------------------------

ARG = {**CARVE, "ddrescue-image": "/dev/sda", "safecopy": "/dev/sda"}


@pytest.mark.parametrize("tool", SIX)
@pytest.mark.parametrize("arg", ["", "sda", "/dev/../sda", "/dev/loop0", "/dev/sdz", "/dev/sdb", "/dev/sdb2"])
def test_no_explicit_readable_source_or_the_stick_runs_nothing(tmp_path, tool, arg):
    case = room(tmp_path)
    _, rc = run(case, tool, arg)
    assert rc == "rc=2", output(case, tool)
    assert calls(case) == "", calls(case)
    assert not (case / "usbdata/recovered").exists()


@pytest.mark.parametrize("tool", SIX)
def test_a_destination_on_the_source_disk_is_refused(tmp_path, tool):
    case = room(tmp_path)
    out, rc = run(case, tool, ARG[tool], mounts='"$SYSROOT/dev/sda1" "$CASE/usbdata" vfat')
    assert rc == "rc=2", output(case, tool)
    assert "הדיסק שממנו קוראים" in output(case, tool)
    assert calls(case) == ""


@pytest.mark.parametrize("tool", SIX)
def test_a_destination_not_on_a_named_disk_is_refused(tmp_path, tool):
    case = room(tmp_path)
    _, rc = run(case, tool, ARG[tool], mounts='tmpfs "$CASE/usbdata" tmpfs')
    assert rc == "rc=2" and "לא ניתן לקבוע על איזה דיסק" in output(case, tool)
    assert calls(case) == ""


@pytest.mark.parametrize("tool", SIX)
def test_not_enough_space_is_refused_before_the_run(tmp_path, tool):
    case = room(tmp_path)
    (case / "df.kb").write_text("2")
    _, rc = run(case, tool, ARG[tool])
    assert rc == "rc=2" and "אין מספיק מקום" in output(case, tool), output(case, tool)
    assert calls(case) == ""


@pytest.mark.parametrize("tool", SIX)
def test_free_space_that_cannot_be_read_is_a_refusal_not_room(tmp_path, tool):
    case = room(tmp_path)
    fake(case / "bin", "df", "exit 1")
    _, rc = run(case, tool, ARG[tool])
    assert rc == "rc=2" and "כמה מקום פנוי" in output(case, tool)
    assert calls(case) == ""


@pytest.mark.parametrize("tool", SIX)
def test_a_missing_binary_is_not_checked(tmp_path, tool):
    case = room(tmp_path)
    binary = {"ddrescue-image": "ddrescue", "magicrescue": "recoverjpeg"}.get(tool, tool)
    (case / "bin" / binary).unlink()
    _, rc = run(case, tool, ARG[tool])
    assert rc == "rc=2" and f"הכלי {binary} חסר" in output(case, tool)


@pytest.mark.parametrize("tool", SIX)
def test_without_a_data_partition_on_the_stick_nothing_runs(tmp_path, tool):
    case = room(tmp_path)
    (case / "run/usb.data").write_text("none\n")
    _, rc = run(case, tool, ARG[tool])
    assert rc == "rc=2" and "מחיצת נתונים" in output(case, tool)
    assert calls(case) == ""


@pytest.mark.parametrize("tool", SIX)
def test_with_a_server_and_no_channel_nothing_runs(tmp_path, tool):
    case = room(tmp_path)
    _, rc = run(case, tool, ARG[tool], mode="normal")
    assert rc == "rc=2" and "אין עדיין ערוץ שמירה לשרת" in output(case, tool)
    assert calls(case) == ""


# --- ddrescue-image ------------------------------------------------------------------------

def test_ddrescue_whole_disk_rescued_is_green_and_lives_in_the_serial_folder(tmp_path):
    case = room(tmp_path)
    _, rc = run(case, "ddrescue-image")
    text = output(case, "ddrescue-image")
    assert rc == "rc=0", text
    folder = case / "usbdata/recovered/rescue/SER-sda"
    assert (folder / "image.map").is_file() and (folder / "image.img").is_file()
    assert (folder / "source.txt").read_text().strip() == f"{SRC_BYTES}|MODEL-sda"
    assert re.search(r"^ddrescue -r3 /dev/sda \S*/rescue/SER-sda/image\.img \S*/rescue/SER-sda/image\.map$",
                     calls(case), flags=re.M), calls(case)
    assert "STDIN-OPEN" not in calls(case)
    assert f"הועתקו {SRC_BYTES} בתים, 0 בתים פגומים, 0 בתים עוד לא נוסו" in text


def test_ddrescue_bad_sectors_are_red(tmp_path):
    case = room(tmp_path)
    full_map(case, [(0, 3584, "+"), (3584, 512, "-")])
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=1", output(case, "ddrescue-image")
    assert "512 בתים לא נקראו" in output(case, "ddrescue-image")


@pytest.mark.parametrize("blocks,why", [
    ([(0, 2048, "+"), (2048, 2048, "?")], "לא הסתיימה"),
    ([(0, 2048, "+")], "ולא את כל הדיסק"),
    ([], "ולא את כל הדיסק"),
])
def test_ddrescue_a_map_that_is_not_complete_is_orange(tmp_path, blocks, why):
    case = room(tmp_path)
    full_map(case, blocks)
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=2", output(case, "ddrescue-image")
    assert why in output(case, "ddrescue-image")


def test_ddrescue_nonzero_exit_with_a_full_map_is_still_not_green(tmp_path):
    case = room(tmp_path)
    (case / "rc.ddrescue").write_text("1")
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=2", output(case, "ddrescue-image")


def test_ddrescue_without_a_map_has_no_evidence(tmp_path):
    case = room(tmp_path)
    (case / "map").write_text("")
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=2" and "אין ראיה" in output(case, "ddrescue-image")


def test_ddrescue_a_second_run_continues_the_first(tmp_path):
    case = room(tmp_path)
    full_map(case, [(0, 2048, "+"), (2048, 2048, "?")])
    run(case, "ddrescue-image")
    full_map(case)
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=0", output(case, "ddrescue-image")
    assert calls(case).count("RESUMED") == 1
    assert "ממשיך ריצה קודמת" in output(case, "ddrescue-image")
    assert [p.name for p in (case / "usbdata/recovered/rescue").iterdir()] == ["SER-sda"]


def test_ddrescue_never_continues_a_folder_of_another_disk(tmp_path):
    case = room(tmp_path)
    run(case, "ddrescue-image")
    (case / "sysroot/sys/block/sda/size").write_text(f"{SRC_SECTORS * 2}\n")   # same serial, other size
    before = calls(case)
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=2" and "דיסק בגודל או דגם אחר" in output(case, "ddrescue-image")
    assert calls(case) == before


def test_ddrescue_a_disk_without_a_serial_gets_its_own_folder(tmp_path):
    case = room(tmp_path)
    (case / "sysroot/sys/block/sda/device/serial").write_text("\n")
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=0", output(case, "ddrescue-image")
    assert "אין מספר סידורי" in output(case, "ddrescue-image")
    [d] = (case / "usbdata/recovered/rescue").iterdir()
    assert d.name.startswith("no-serial-")


def test_ddrescue_an_image_of_4gib_or_more_does_not_go_to_fat(tmp_path):
    case = room(tmp_path)
    (case / "sysroot/sys/block/sda/size").write_text(f"{8 * 1024 * 1024}\n")   # 4 GiB
    _, rc = run(case, "ddrescue-image")
    assert rc == "rc=2" and "התקרה 4GB" in output(case, "ddrescue-image")
    assert calls(case) == ""


# --- safecopy -------------------------------------------------------------------------------

def test_safecopy_runs_the_three_stages_in_the_serial_folder(tmp_path):
    case = room(tmp_path)
    _, rc = run(case, "safecopy")
    text = output(case, "safecopy")
    assert rc == "rc=0", text
    stages = [l for l in calls(case).splitlines() if l.startswith("safecopy ")]
    assert [l.split()[1] for l in stages] == ["--stage1", "--stage2", "--stage3"]
    assert all(re.search(r" /dev/sda \S*/rescue/SER-sda/safecopy\.img \(in \S*/usbdata/recovered/rescue/SER-sda\)$", l)
               for l in stages), stages
    assert "STDIN-OPEN" not in calls(case)
    assert f"אימג׳ של {SRC_BYTES} בתים מתוך {SRC_BYTES}" in text


def test_safecopy_blocks_left_after_stage_three_are_red(tmp_path):
    case = room(tmp_path)
    (case / "bad.3").write_text("5\n6\n")
    (case / "rc.safecopy.3").write_text("1")
    _, rc = run(case, "safecopy")
    assert rc == "rc=1", output(case, "safecopy")
    assert "2 בלוקים לא נקראו" in output(case, "safecopy")


def test_safecopy_an_abort_stops_and_a_rerun_continues_from_that_stage(tmp_path):
    case = room(tmp_path)
    (case / "rc.safecopy.2").write_text("2")
    _, rc = run(case, "safecopy")
    assert rc == "rc=2" and "נעצר באמצע" in output(case, "safecopy")
    (case / "rc.safecopy.2").unlink()
    (case / "called").write_text("")
    _, rc = run(case, "safecopy")
    assert rc == "rc=0", output(case, "safecopy")
    assert [l.split()[1] for l in calls(case).splitlines()] == ["--stage2", "--stage3"]


def test_safecopy_an_image_that_is_not_the_disk_size_is_not_trusted(tmp_path):
    case = room(tmp_path)
    (case / "img.bytes").write_text("100")
    _, rc = run(case, "safecopy")
    assert rc == "rc=2" and "אינו בגודל הדיסק" in output(case, "safecopy")
