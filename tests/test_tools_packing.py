"""‏#1050 — הבחירה שנשמרה בקונסולה מגיעה ל-initrd ולתפריט התחנה.

שני צדדים, טבלה אחת (`agent/lib/tools/toolbins.sh`): הבנאי (`build_initramfs.sh
--tools-selection`) אורז את הבינארים של מזהי ה-`build` שבקובץ, והתחנה
מציעה רק אותם. כאן נבדק **התכנון** (`tools_bins_plan`) ולא initrd אמיתי —
זה דביאן בלבד, אצל המתאם במעבדה — ומול הקובץ בצורה שהשרת כותב.

הטענות: לכל מזהה בקטלוג יש שורה בטבלה ורק לו; בחירה → רשימת הבינארים
של המזהים שבה; ריק → כלום; מזהה זר → הבנאי נעצר **בשמו** (עיקרון 5);
התחנה מסננת את `tools_list` לפי הקובץ; ומגבלת 300 על כל קובץ שנגע.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from agentlib import flat_lib, lib_files

import pytest

import sizelimit
from native import requires_native

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
LIB = flat_lib()
TOOLBINS = LIB / "toolbins.sh"
BUILDER = REPO / "tools" / "build_initramfs.sh"
CATALOG = json.loads((REPO / "server" / "tools_catalog.json").read_text(encoding="utf-8"))
CATALOG_IDS = [t["id"] for t in CATALOG["tools"]]


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
pytestmark = requires_native(("bash", BASH))


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def sh(script: str, *, strict: bool = False) -> subprocess.CompletedProcess:
    """‏`strict` = הדגלים של הבנאי (`set -euo pipefail`): מה שעובר בטסט חייב
    לעבור גם שם, ו-grep שמחזיר 1 על רשימה ריקה היה מפיל את הבנייה."""
    head = "set -euo pipefail; " if strict else ""
    return subprocess.run(
        [BASH, "-c", f'export PATH="/usr/bin:$PATH"; {head}. {posix(TOOLBINS)!r}; ' + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=60,
    )


def selection(tmp_path: Path, build: list[str], student: list[str] | None = None,
              name: str = "tools-selection.json") -> Path:
    """הקובץ כפי ש-`PUT /api/console/tools/selection` כותב אותו (§23)."""
    sel = tmp_path / name
    sel.write_text(json.dumps({"schema": 1, "build": build, "student": student or []}, indent=1),
                   encoding="utf-8", newline="\n")
    return sel


def bins_of(tool_id: str) -> list[str]:
    out = sh(f"tools_bins {tool_id!r}")
    assert out.returncode == 0, f"{tool_id}: rc {out.returncode} {out.stderr}"
    return out.stdout.split()


# --- the table -----------------------------------------------------------------


def table_ids() -> set[str]:
    """המזהים שבטבלת `tools_bins` עצמה — מהמקור, כדי לתפוס שורה שאין לה קטלוג."""
    src = TOOLBINS.read_text(encoding="utf-8")
    body = src[src.index("tools_bins() {"):src.index("_tools_selection_list()")]
    return set(re.findall(r"^\s+([a-z0-9-]+)\)\s+printf", body, flags=re.M))


def test_every_catalog_id_has_a_row_and_nothing_else_does():
    assert table_ids() == set(CATALOG_IDS), table_ids() ^ set(CATALOG_IDS)
    assert len(CATALOG_IDS) == 25


@pytest.mark.parametrize("tool_id", CATALOG_IDS)
def test_every_catalog_id_maps_to_real_file_names(tool_id):
    """שמות קבצים, לא טקסט תצוגה: בלי רווחים, בלי `-Z`/`--ses`, בלי `/`."""
    for b in bins_of(tool_id):
        assert re.fullmatch(r"[A-Za-z0-9_.+-]+", b), (tool_id, b)
    # and the table agrees with the catalog's first word (a drift check, not
    # the source of truth: `dd` is busybox, so it maps to nothing)
    display = next(t["binary"] for t in CATALOG["tools"] if t["id"] == tool_id)
    assert display.split()[0] in {"dd", *bins_of(tool_id)}, (tool_id, display, bins_of(tool_id))


def test_an_unknown_id_is_rc_1_with_no_output():
    out = sh("tools_bins nope-tool; echo rc=$?")
    assert out.stdout.strip() == "rc=1"


def test_dd_zero_needs_nothing_beyond_busybox():
    assert bins_of("disk-dd-zero") == []


# --- the plan (what the builder packs) ----------------------------------------------


def test_the_plan_is_the_union_of_the_selected_ids_binaries(tmp_path):
    chosen = ["disk-blkdiscard", "win-users", "win-blank-password", "magicrescue",
              "disk-dd-zero", "nvme-sanitize", "disk-nvme-format"]
    sel = selection(tmp_path, chosen, student=["photorec"])
    out = sh(f"tools_bins_plan {posix(sel)!r}", strict=True)
    assert out.returncode == 0, out.stderr
    expected = []
    for tool_id in chosen:
        for b in bins_of(tool_id):
            if b not in expected:
                expected.append(b)
    assert out.stdout.split() == expected == \
        ["blkdiscard", "chntpw", "magicrescue", "recoverjpeg", "nvme"]
    assert "7 build ids, 1 student ids (student ignored, v2)" in out.stderr


def test_the_whole_catalog_plans_every_binary_once(tmp_path):
    sel = selection(tmp_path, CATALOG_IDS)
    out = sh(f"tools_bins_plan {posix(sel)!r}", strict=True)
    assert out.returncode == 0, out.stderr
    planned = out.stdout.split()
    assert len(planned) == len(set(planned))
    assert set(planned) == {b for t in CATALOG_IDS for b in bins_of(t)}


def test_an_empty_build_list_plans_nothing_and_is_not_an_error(tmp_path):
    sel = selection(tmp_path, [], student=["photorec", "testdisk"])
    out = sh(f"tools_bins_plan {posix(sel)!r}; echo rc=$?", strict=True)
    assert out.stdout.strip() == "rc=0", out.stdout + out.stderr
    assert "0 build ids, 2 student ids" in out.stderr


def test_a_missing_selection_file_is_rc_1_by_path(tmp_path):
    out = sh(f"tools_bins_plan {posix(tmp_path / 'none.json')!r}; echo rc=$?")
    assert out.stdout.strip() == "rc=1"
    assert "none.json" in out.stderr


def test_an_unknown_id_stops_the_plan_loudly_and_packs_nothing(tmp_path):
    """עיקרון 5: מזהה שהטבלה לא מכירה אינו "כלי בלי בינארים" — הוא עצירה,
    בשם, וגם הבינארים של המזהים התקינים באותו קובץ אינם מודפסים."""
    sel = selection(tmp_path, ["disk-blkdiscard", "nope-tool", "also-nope"])
    out = sh(f"tools_bins_plan {posix(sel)!r}; echo rc=$?")
    assert out.stdout.strip() == "rc=1", out.stdout
    assert "nope-tool" in out.stderr and "also-nope" in out.stderr


def test_the_file_the_server_writes_is_parsed_with_its_real_whitespace(tmp_path):
    """‏json.dump עם indent, ‏\\r\\n של ווינדוס, ורווחים — כולם אותו קובץ."""
    sel = tmp_path / "sel.json"
    sel.write_bytes(b'{\r\n "schema": 1,\r\n "build": [\r\n  "testdisk",\r\n  "photorec"\r\n ],\r\n "student": []\r\n}\r\n')
    out = sh(f"tools_bins_plan {posix(sel)!r}", strict=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["testdisk", "photorec"]


# --- the builder text ---------------------------------------------------------------


def test_the_builder_has_the_flag_and_uses_the_same_table():
    text = BUILDER.read_text(encoding="utf-8")
    assert "--tools-selection) TOOLS_SELECTION_FILE=" in text
    assert '. "$AGENT_DIR/lib/tools/toolbins.sh"' in text
    assert 'tools_bins_plan "$TOOLS_SELECTION_FILE"' in text
    assert '"$ROOT/etc/imagesrv/tools-selection.json"' in text
    # a plan that failed stops the build before apt
    assert "the selection is not packable" in text
    assert text.index("tools_bins_plan") < text.index("apt-get install")


def test_the_builder_knows_the_apt_package_of_every_binary_the_table_can_name():
    """‏TOOL_PKG בבנאי ו-`tools_bins` בטבלה חייבים לנוע יחד: בינארי בלי
    חבילה נופל ב-copy_bin עם הודעה גרועה, וחבילה בלי בינארי מותקנת לשווא."""
    text = BUILDER.read_text(encoding="utf-8")
    block = text[text.index("declare -A TOOL_PKG=("):]
    block = block[:block.index("\n)")]
    packaged = set(re.findall(r"\[([A-Za-z0-9_.+-]+)\]=", block))
    needed = {b for t in CATALOG_IDS for b in bins_of(t)}
    assert packaged == needed, packaged ^ needed


def test_without_the_flag_no_toolbox_binary_is_in_the_base_list():
    """בלי `--tools-selection` נארז רק הבסיס: אף בינארי שהטבלה יכולה לתת
    אינו ב-BINARIES — חוץ מאלה שהסוכן עצמו צריך בלי קשר לארגז (hdparm,
    sgdisk לשחזור/קליטה)."""
    text = BUILDER.read_text(encoding="utf-8")
    base = set(re.search(r"BINARIES=\((.*?)\)", text, flags=re.DOTALL).group(1).split())
    toolbox = {b for t in CATALOG_IDS for b in bins_of(t)}
    assert base & toolbox == {"hdparm", "sgdisk"}, base & toolbox
    assert "REQUIRED_DISK_TOOLS" not in text and "REQUIRED_HW_TOOL_BINARIES" not in text


# --- the station filter (the real modules, a fake selection) ------------------------


def station(tmp_path: Path, build: list[str], with_file: bool = True) -> subprocess.CompletedProcess:
    sel = selection(tmp_path, build) if with_file else tmp_path / "none.json"
    run = tmp_path / "run"; run.mkdir()
    script = (
        f'export PATH="/usr/bin:/bin" LIB_DIR={posix(LIB)!r} RUN_DIR={posix(run)!r} '
        f"TOOLS_SELECTION={posix(sel)!r}; "
        f". {posix(LIB)}/common.sh; . {posix(LIB)}/tools.sh; tools_list; echo rc=$?"
    )
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(REPO),
                          stdin=subprocess.DEVNULL, timeout=60)


def listed(out: subprocess.CompletedProcess) -> list[str]:
    return [l.split("|")[0] for l in out.stdout.splitlines() if "|" in l]


#: מזהי הקטלוג שיש להם מימוש `tools_<domain>_run` היום (#649 d2/d4/d5/d6 לאחר
#: הגיזום ל-#1050). השאר נארזים ואינם מוצעים בתחנה — פער גלוי, לא שקט.
RUNNABLE = ["disk-blkdiscard", "disk-hdparm-erase", "disk-nvme-format", "disk-dd-zero",
            "disk-wipefs-all", "win-users", "win-blank-password", "esp-fsck-repair", "tpm-clear"]
PACKED_ONLY = sorted(set(CATALOG_IDS) - set(RUNNABLE))


def test_the_station_lists_only_the_selected_ids(tmp_path):
    out = station(tmp_path, ["tpm-clear", "disk-blkdiscard", "esp-fsck-repair", "win-users"])
    assert out.stdout.splitlines()[-1] == "rc=0", out.stderr
    assert sorted(listed(out)) == ["disk-blkdiscard", "esp-fsck-repair", "tpm-clear", "win-users"]


def test_the_station_with_the_whole_catalog_lists_exactly_the_runnable_ids(tmp_path):
    """מה שיש לו מודול מופיע; מה שאין לו — לא. הרשימה RUNNABLE היא הצהרה
    שנופלת כשמישהו מוסיף או מוחק מימוש בלי לעדכן אותה."""
    out = station(tmp_path, CATALOG_IDS)
    assert sorted(listed(out)) == sorted(RUNNABLE), listed(out)
    assert len(PACKED_ONLY) == 16


def test_the_station_without_a_selection_file_lists_nothing(tmp_path):
    out = station(tmp_path, [], with_file=False)
    assert listed(out) == []
    assert "no selection file" in out.stderr


def test_the_station_with_an_empty_selection_lists_nothing(tmp_path):
    out = station(tmp_path, [])
    assert listed(out) == []


# --- the 300-line wall on every agent file this work touched --------------------------


@pytest.mark.parametrize("name", ["tools.sh", "toolbins.sh", "tools_disk.sh",
                                  "tools_windows.sh", "tools_boot.sh", "tools_hw.sh"])
def test_touched_lib_files_stay_within_the_limit(name):
    sizelimit.assert_within_limit(LIB / name)


def test_the_agent_stays_within_300_lines_and_loads_the_table():
    src = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert sizelimit.count_lines(AGENT / "imagesrv-agent") <= 300
    assert '. "$LIB_DIR/toolbins.sh"' in src


def test_no_tools_bins_module_shadows_the_glob():
    """‏tools_load מגלגל `tools_*.sh` כמודולים; קובץ `tools_bins.sh` היה נטען
    כתחום "bins" בלי `tools_bins_list` — לכן הטבלה היא `toolbins.sh`."""
    assert not (LIB / "tools_bins.sh").exists()
    modules = sorted(p.name for p in lib_files(LIB) if p.match("tools_*.sh"))
    assert modules == ["tools_boot.sh", "tools_disk.sh", "tools_hw.sh", "tools_windows.sh"]
