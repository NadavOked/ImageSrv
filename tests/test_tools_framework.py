"""‏#649 תחום 1 — המסגרת של ארגז הכלים (agent/lib/tools/tools.sh), בהזרקת מעטפת.

החוזה (התגובה האחרונה ב-#649): כל `tools_<domain>.sh` מגדיר
`tools_<domain>_list` (שורה לכלי `id|domain|title|risk|args`) ו-
`tools_<domain>_run <id> [arg]`. המסגרת מאחדת את הרשימה, **חוסמת
‏rw/destroy לפני שהמודול נקרא** כשההקלדה אינה שם המכונה (עיקרון 7),
וכותבת את הפלט ל-`$RUN_DIR/tool-<id>.out`.

הבקרות השליליות המובנות: מודול מזויף מסמן קובץ כשהוא נקרא — ‏rc 3
בלי הקובץ הוא ההוכחה שהשומר יושב במסגרת ולא במודול; ‏rc 2 מהמודול
יוצא החוצה כ-2 ולא מקופל ל-0/1 (עיקרון 5).

‏#1050: הבחירה שנשמרה בקונסולה היא רשימת-היתר. ‏`setup` כותב קובץ
בחירה עם כל המזהים המזויפים (ומצביע עליו ב-`TOOLS_SELECTION`), כי
בלעדיו המסגרת מציעה **כלום** — וזה נבדק בנפרד למטה.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"


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


def msys(p: Path) -> str:
    """‏`/c/...` — הצורה היחידה ש-PATH של bash-של-Git מחפש בה (‏`C:/` עובד
    כארגומנט, לא כרכיב PATH)."""
    s = posix(p)
    return f"/{s[0].lower()}{s[2:]}" if len(s) > 1 and s[1] == ":" else s


def last(out: subprocess.CompletedProcess) -> str:
    """השורה האחרונה של stdout — ‏`echo rc=$?` של הטסט. לא `in`: ‏`log`
    מדפיס `tool x finished rc=2` לאותו stdout, ו-`"rc=2" in out.stdout` היה
    עובר גם כשהמסגרת קיפלה את 2 ל-0 (נמדד בבקרה השלילית, מוטציה B)."""
    lines = out.stdout.splitlines()
    return lines[-1] if lines else ""


def sh(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO),
        stdin=subprocess.DEVNULL, timeout=timeout,
    )


DISK_MODULE = """\
tools_disk_list() {
    printf 'wipe|disk|מחיקת דיסק|destroy|disk:הדיסק למחיקה\\n'
    printf 'fsck|disk|בדיקת מערכת קבצים|rw|\\n'
    printf 'lsblk|disk|רשימת דיסקים|ro|\\n'
}
tools_disk_run() {
    printf 'disk %s arg=%s\\n' "$1" "$2" >> "$RUN_DIR/called"
    case "$1" in
        lsblk) echo "sda 256G"; return 0 ;;
        fsck) echo "clean"; return 0 ;;
        wipe) echo "wiped $2"; return 0 ;;
    esac
    return 4
}
"""

NET_MODULE = """\
tools_net_list() {
    printf 'ping|net|בדיקת קישוריות|ro|host\\n'
    printf 'broken|net|כלי שלא מצליח לבדוק|ro|\\n'
    printf '|net|שורה בלי מזהה|ro|\\n'
}
tools_net_run() {
    printf 'net %s arg=%s\\n' "$1" "$2" >> "$RUN_DIR/called"
    case "$1" in
        ping) echo "reply from $2"; return 0 ;;
        broken) echo "could not reach the tool"; return 2 ;;
    esac
    return 4
}
"""


#: כל מזהה שהמודולים המזויפים והמובנים מכירים — ברירת המחדל של קובץ הבחירה.
ALL_FAKE_IDS = ["sysinfo", "smart-health", "wipe", "fsck", "lsblk", "odd", "ping", "broken"]


def write_selection(tmp_path: Path, build: list[str]) -> Path:
    """‏tools-selection.json בצורה שהשרת כותב (interfaces.md §23)."""
    sel = tmp_path / "tools-selection.json"
    sel.write_text(json.dumps({"schema": 1, "build": build, "student": []}, indent=1),
                   encoding="utf-8", newline="\n")
    return sel


def setup(tmp_path: Path, modules: dict[str, str] | None = None,
          fakes: dict[str, str] | None = None,
          selection: list[str] | None = None,
          tools: bool = True) -> tuple[Path, Path, Path, str]:
    """LIB_DIR מזויף עם המודולים, RUN_DIR, GUI_DIR, ו-PATH עם כלים מזויפים.

    ‏PATH מתחיל בתיקייה **ריקה** של כלים מזויפים כדי ש-`dmidecode`/`smartctl`
    של התחנה לא יזלגו פנימה: המובנים מופיעים רק כשהבינארי שלהם ארוז.
    ‏`selection` — מזהי ה-build בקובץ הבחירה; ברירת המחדל כולם (#1050).
    ‏`tools` — היכולת ב-hello (‏`tools_on`, ‏#1303); ברירת המחדל דלוקה (v1.1)."""
    lib = tmp_path / "lib"; lib.mkdir()
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    fake = tmp_path / "bin"; fake.mkdir()
    shutil.copy(AGENT / "lib" / "tools" / "toolbins.sh", lib / "toolbins.sh")
    sel = write_selection(tmp_path, ALL_FAKE_IDS if selection is None else selection)
    (run / "response.json").write_text(json.dumps({"role": "build", "tools": tools}),
                                       encoding="utf-8", newline="\n")
    for name, body in (modules or {}).items():
        (lib / f"tools_{name}.sh").write_text(body, encoding="utf-8", newline="\n")
    for name, body in (fakes or {}).items():
        f = fake / name
        f.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        f.chmod(0o755)
    # ‏PATH סגור: הכלים המזויפים, ואחריהם רק /usr/bin ו-/bin של bash — כך
    # ‏dmidecode/smartctl של התחנה (אם יש) אינם מופיעים, והמובנים נרשמים
    # רק כשהבינארי שלהם "ארוז" (מזויף כאן). ‏jq (לצד הקיוסק) חייב להיות
    # אחד משני אלה — ‏requires_native("jq") על הטסטים שצריכים אותו.
    jq_dir = Path(shutil.which("jq")).parent if shutil.which("jq") else None
    path = ":".join([msys(fake), *([msys(jq_dir)] if jq_dir else []), "/usr/bin", "/bin"])
    prelude = (
        f"export PATH={path!r} LIB_DIR={posix(lib)!r} RUN_DIR={posix(run)!r} "
        f"GUI_DIR={posix(gui)!r} TOOLS_SELECTION={posix(sel)!r} IMAGESRV_TEST=1; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f". {posix(AGENT)}/lib/gui/buildmenuitems.sh; . {posix(AGENT)}/lib/tools/tools.sh; "
    )
    return lib, run, gui, prelude


# --- הרשימה המאוחדת ------------------------------------------------------------


def test_the_list_unifies_every_module(tmp_path):
    _, _, _, pre = setup(tmp_path, {"disk": DISK_MODULE, "net": NET_MODULE})
    out = sh(pre + "tools_list")
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert "wipe|disk|מחיקת דיסק|destroy|disk:הדיסק למחיקה" in lines
    assert "ping|net|בדיקת קישוריות|ro|host" in lines
    assert len(lines) == 5, lines


def test_a_line_without_an_id_is_dropped_and_said(tmp_path):
    _, _, _, pre = setup(tmp_path, {"net": NET_MODULE})
    out = sh(pre + "tools_list")
    assert not any(line.startswith("|") for line in out.stdout.splitlines())
    assert "malformed line dropped" in out.stderr


def test_zero_modules_and_no_packed_binaries_is_an_empty_list(tmp_path):
    """המצב הריק: המסגרת רצה, מחזירה 0, ולא מדפיסה כלום — המסך אומר
    "לא נבחרו כלים". המובנים אינם מזויפים: `command -v` לא מוצא אותם."""
    _, _, _, pre = setup(tmp_path)
    out = sh(pre + "tools_list")
    assert out.returncode == 0, out.stderr
    assert out.stdout == ""


# --- הבחירה מהשרת היא רשימת-היתר (#1050) ----------------------------------------------


def test_an_unselected_tool_is_not_listed(tmp_path):
    _, _, _, pre = setup(tmp_path, {"disk": DISK_MODULE, "net": NET_MODULE},
                         selection=["wipe", "ping"])
    out = sh(pre + "tools_list")
    assert out.returncode == 0, out.stderr
    ids = [l.split("|")[0] for l in out.stdout.splitlines()]
    assert ids == ["wipe", "ping"], ids


def test_an_unselected_tool_does_not_run_even_with_the_machine_name(tmp_path):
    """הסינון אינו רק ברשימה: ‏tool-run עם מזהה שהמודול מכיר אך השרת לא
    בחר הוא rc 4 — והמודול אינו נקרא. אחרת המסך היה מסתיר והפקודה מריצה."""
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE}, selection=["lsblk"])
    out = sh(pre + "export MACHINE_NAME=B; tools_run wipe /dev/sda B; echo rc=$?")
    assert last(out) == "rc=4", out.stdout + out.stderr
    assert not (run / "called").exists()
    assert not (run / "tool-wipe.out").exists()


def test_a_built_in_needs_the_selection_too(tmp_path):
    """‏sysinfo/smart-health אינם בקטלוג של נדב: הבינארי ארוז, אבל בלי
    המזהה בבחירה הם לא מוצעים ולא רצים."""
    _, run, _, pre = setup(tmp_path, fakes={"dmidecode": DMIDECODE_OK}, selection=["lsblk"])
    out = sh(pre + "tools_list; tools_run sysinfo; echo rc=$?")
    assert "sysinfo|" not in out.stdout
    assert last(out) == "rc=4"
    assert not (run / "tool-sysinfo.out").exists()


def test_no_selection_file_offers_nothing_and_says_so(tmp_path):
    """אין קובץ = אין כלים (fail closed), וזה נאמר ב-stderr — "לא נבחר" ו"הקובץ
    חסר" הם שני מצבים, גם כששניהם רשימה ריקה (עיקרון 5)."""
    _, _, _, pre = setup(tmp_path, {"disk": DISK_MODULE})
    (tmp_path / "tools-selection.json").unlink()
    out = sh(pre + "tools_list; echo rc=$?")
    assert last(out) == "rc=0"
    assert [l for l in out.stdout.splitlines() if "|" in l] == []
    assert "no selection file" in out.stderr


def test_an_empty_build_list_offers_nothing_without_the_missing_file_message(tmp_path):
    _, _, _, pre = setup(tmp_path, {"disk": DISK_MODULE}, selection=[])
    out = sh(pre + "tools_list; echo rc=$?")
    assert last(out) == "rc=0"
    assert [l for l in out.stdout.splitlines() if "|" in l] == []
    assert "no selection file" not in out.stderr


def test_the_confirm_reaches_the_module(tmp_path):
    """‏#1050: המודולים האמיתיים שומרים בעצמם עם ההקלדה (‏$3); מסגרת שמעבירה
    רק שני ארגומנטים הייתה מכשילה כל destroy ב-rc 3 גם עם השם הנכון."""
    module = """\
tools_x_list() { printf 'echo3|x|t|destroy|\\n'; }
tools_x_run() { printf 'confirm=%s\\n' "$3"; }
"""
    _, run, _, pre = setup(tmp_path, {"x": module}, selection=["echo3"])
    out = sh(pre + "export MACHINE_NAME=B; tools_run echo3 '' B; echo rc=$?")
    assert last(out) == "rc=0", out.stdout + out.stderr
    assert (run / "tool-echo3.out").read_text(encoding="utf-8") == "confirm=B\n"


# --- השומר במסגרת --------------------------------------------------------------


def test_destroy_without_the_machine_name_is_rc_3_and_the_module_is_never_called(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE})
    out = sh(pre + "export MACHINE_NAME=BUILD-01; tools_run wipe /dev/sda; echo rc=$?")
    assert last(out) == "rc=3", out.stdout + out.stderr
    assert not (run / "called").exists(), "המודול נקרא למרות שהאישור לא תאם"
    assert not (run / "tool-wipe.out").exists()


def test_destroy_with_a_wrong_name_is_rc_3(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE})
    out = sh(pre + "export MACHINE_NAME=BUILD-01; tools_run wipe /dev/sda BUILD-02; echo rc=$?")
    assert last(out) == "rc=3"
    assert not (run / "called").exists()


def test_rw_is_guarded_like_destroy(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE})
    out = sh(pre + "export MACHINE_NAME=BUILD-01; tools_run fsck /dev/sda; echo rc=$?")
    assert last(out) == "rc=3"
    assert not (run / "called").exists()


def test_destroy_with_the_machine_name_runs_the_module(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE})
    out = sh(pre + "export MACHINE_NAME=BUILD-01; tools_run wipe /dev/sda BUILD-01; echo rc=$?")
    assert last(out) == "rc=0", out.stdout + out.stderr
    assert (run / "called").read_text(encoding="utf-8") == "disk wipe arg=/dev/sda\n"
    assert (run / "tool-wipe.out").read_text(encoding="utf-8") == "wiped /dev/sda\n"


def test_no_machine_name_at_all_refuses_rw(tmp_path):
    """שם ריק אינו "הכול תואם": בלי שם — אין מה להקליד, ולכן rc 3."""
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE})
    out = sh(pre + "unset MACHINE_NAME; hostname() { :; }; tools_run fsck /dev/sda ''; echo rc=$?")
    assert last(out) == "rc=3"
    assert not (run / "called").exists()


def test_ro_runs_without_any_confirmation(tmp_path):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE, "net": NET_MODULE})
    out = sh(pre + "tools_run ping 10.0.0.1; echo rc=$?")
    assert last(out) == "rc=0", out.stderr
    assert (run / "tool-ping.out").read_text() == "reply from 10.0.0.1\n"
    assert (run / "called").read_text(encoding="utf-8") == "net ping arg=10.0.0.1\n"


# --- קודי חזרה ----------------------------------------------------------------------


def test_rc_2_from_the_module_reaches_the_caller_unchanged(tmp_path):
    """"לא הצלחנו לבדוק" אינו "בדקנו ותקין" ואינו "נכשל" (עיקרון 5)."""
    _, run, _, pre = setup(tmp_path, {"net": NET_MODULE})
    out = sh(pre + "tools_run broken; echo rc=$?")
    assert last(out) == "rc=2"
    assert (run / "tool-broken.out").read_text() == "could not reach the tool\n"


@pytest.mark.parametrize("bad", ["nosuch", "../etc", "a b", ""])
def test_an_unknown_or_unsafe_id_is_rc_4_with_no_file(tmp_path, bad):
    _, run, _, pre = setup(tmp_path, {"disk": DISK_MODULE})
    out = sh(pre + f"tools_run {bad!r}; echo rc=$?")
    assert last(out) == "rc=4"
    assert not (run / "called").exists()
    assert sorted(p.name for p in run.iterdir()) == ["agent.log"] or not list(run.glob("tool-*"))


def test_an_unknown_risk_word_is_treated_as_destroy(tmp_path):
    module = """\
tools_x_list() { printf 'odd|x|כלי עם סיכון לא מוכר|maybe|\\n'; }
tools_x_run() { touch "$RUN_DIR/called"; echo ran; }
"""
    _, run, _, pre = setup(tmp_path, {"x": module})
    out = sh(pre + "export MACHINE_NAME=B; tools_run odd; echo rc=$?; tools_run odd '' B; echo rc=$?")
    rcs = [l for l in out.stdout.splitlines() if l.startswith("rc=")]
    assert rcs == ["rc=3", "rc=0"], out.stdout + out.stderr
    assert (run / "called").exists()


# --- המובנים -------------------------------------------------------------------------


DMIDECODE_OK = """\
case "$2" in
    system-manufacturer) echo "LENOVO" ;;
    system-product-name) echo "10SQ0016IV" ;;
    system-serial-number) echo "PC1ABCD" ;;
    baseboard-product-name) echo "To be filled by O.E.M." ;;
    bios-vendor) echo "LENOVO" ;;
    bios-version) echo "M1UKT66A" ;;
    bios-release-date) echo "05/12/2023" ;;
esac
"""


def test_sysinfo_reads_dmidecode_into_hebrew_lines(tmp_path):
    _, run, _, pre = setup(tmp_path, fakes={"dmidecode": DMIDECODE_OK})
    out = sh(pre + "tools_list; tools_run sysinfo; echo rc=$?")
    assert "sysinfo|hw|" in out.stdout
    assert last(out) == "rc=0", out.stderr
    text = (run / "tool-sysinfo.out").read_text(encoding="utf-8")
    assert "יצרן: LENOVO" in text
    assert "מספר סידורי: PC1ABCD" in text
    assert "לוח אם: לא דווח על ידי הקושחה" in text
    assert "גרסת BIOS: M1UKT66A" in text


def test_sysinfo_with_a_failing_dmidecode_is_rc_2_not_a_clean_answer(tmp_path):
    _, run, _, pre = setup(tmp_path, fakes={"dmidecode": "exit 1\n"})
    out = sh(pre + "tools_run sysinfo; echo rc=$?")
    assert last(out) == "rc=2"
    assert "לא הצלחנו לבדוק" in (run / "tool-sysinfo.out").read_text(encoding="utf-8")


def smart_setup(tmp_path: Path, disks: dict[str, int]) -> tuple[Path, str]:
    """‏/sys/block מזויף (SYSROOT) ו-smartctl שמחזיר קוד יציאה לפי הדיסק."""
    sysroot = tmp_path / "sysroot"
    for name in disks:
        (sysroot / "sys" / "block" / name / "device").mkdir(parents=True)
        (sysroot / "sys" / "block" / name / "device" / "model").write_text(f"Model-{name}\n")
    cases = " ".join(f"/dev/{d}) exit {rc} ;;" for d, rc in disks.items())
    fake = f'for a in "$@"; do last="$a"; done\ncase "$last" in {cases} esac\nexit 2\n'
    _, run, _, pre = setup(tmp_path, fakes={"smartctl": fake})
    return run, pre + f"export SYSROOT={posix(sysroot)!r}; "


def test_smart_health_lists_every_disk_with_its_verdict(tmp_path):
    run, pre = smart_setup(tmp_path, {"sda": 0, "sdb": 8})
    out = sh(pre + "tools_run smart-health; echo rc=$?")
    assert last(out) == "rc=1", out.stdout + out.stderr
    text = (run / "tool-smart-health.out").read_text(encoding="utf-8")
    assert "/dev/sda (Model-sda): תקין" in text
    assert "/dev/sdb (Model-sdb): נכשל" in text


def test_a_disk_smartctl_could_not_read_is_unchecked_rc_2(tmp_path):
    run, pre = smart_setup(tmp_path, {"sda": 0, "nvme0n1": 2})
    out = sh(pre + "tools_run smart-health; echo rc=$?")
    assert last(out) == "rc=2"
    text = (run / "tool-smart-health.out").read_text(encoding="utf-8")
    assert "/dev/nvme0n1 (Model-nvme0n1): לא הצלחנו לבדוק" in text
    assert "/dev/sda (Model-sda): תקין" in text


def test_all_clean_is_rc_0(tmp_path):
    _, pre = smart_setup(tmp_path, {"sda": 0, "sdb": 0})
    out = sh(pre + "tools_run smart-health; echo rc=$?")
    assert last(out) == "rc=0"


# --- הצד של הקיוסק (guibridge -> tools_gui_*) -----------------------------------------


@requires_native("jq", why="tools_gui_run קורא את שם המכונה מ-station.json עם jq")
def test_tool_list_writes_the_gui_file_with_the_machine_name_first(tmp_path):
    _, _, gui, pre = setup(tmp_path, {"disk": DISK_MODULE})
    (gui / "station.json").write_text('{"name":"BUILD-01"}')
    out = sh(pre + "export MACHINE_NAME=BUILD-01; tools_gui_list; echo rc=$?")
    assert last(out) == "rc=0", out.stderr
    lines = (gui / "tools").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "machine=BUILD-01"
    assert "lsblk|disk|רשימת דיסקים|ro|" in lines


@requires_native("jq", why="tools_gui_run קורא את שם המכונה מ-station.json עם jq")
def test_tool_run_token_writes_the_result_record_and_the_output(tmp_path):
    _, run, gui, pre = setup(tmp_path, {"disk": DISK_MODULE})
    (gui / "station.json").write_text('{"name":"BUILD-01"}')
    out = sh(pre + "tools_gui_run 'tool-run|wipe|/dev/sda|BUILD-01'; echo rc=$?")
    assert last(out) == "rc=0", out.stderr
    rec = (gui / "tool-result").read_text().strip().split("|")
    assert rec[:2] == ["wipe", "0"]
    assert Path(rec[2]).read_text() == "wiped /dev/sda\n"
    assert rec[3] == "1"


@requires_native("jq", why="tools_gui_run קורא את שם המכונה מ-station.json עם jq")
def test_the_bridge_takes_the_name_from_station_json_not_from_the_gui(tmp_path):
    """‏#649/עיקרון 7: ה-GUI מקליד, השרת קובע את השם. ‏station.json הוא העותק
    הטרי של /agent/state; שם אחר בהקלדה = rc 3 ברשומה, והמודול לא נקרא."""
    _, run, gui, pre = setup(tmp_path, {"disk": DISK_MODULE})
    (gui / "station.json").write_text('{"name":"BUILD-01"}')
    out = sh(pre + "export MACHINE_NAME=WRONG; tools_gui_run 'tool-run|wipe|/dev/sda|WRONG'; echo rc=$?")
    assert last(out) == "rc=0", out.stderr          # a written result is a handled action
    assert (gui / "tool-result").read_text().split("|")[1] == "3"
    assert not (run / "called").exists()


@requires_native("jq", why="tools_on קורא את היכולת מה-hello עם jq")
def test_a_second_run_gets_a_new_sequence_number(tmp_path):
    _, _, gui, pre = setup(tmp_path, {"disk": DISK_MODULE})
    sh(pre + "tools_gui_run 'tool-run|lsblk||'; tools_gui_run 'tool-run|lsblk||'")
    assert (gui / "tool-result").read_text().strip().endswith("|2")


def test_tool_run_is_refused_while_the_toolbox_is_off(tmp_path):
    """‏#1303: הגואי מסתיר את הכפתור לפי `menu_tools`, אבל רשומת `tool-run`
    שמגיעה בכל זאת (גואי ישן, כתיבה ידנית ל-fifo) הריצה את הכלי — גם
    במהדורה בלי ארגז הכלים. הגשר עצמו הוא השער: יכולת כבויה, או כזו שלא
    נקראה, = אין ריצה ואין רשומת תוצאה. ‏lsblk הוא ro, כדי שרק השער יעצור."""
    _, run, gui, pre = setup(tmp_path, {"disk": DISK_MODULE}, tools=False)
    out = sh(pre + "tools_gui_run 'tool-run|lsblk||'; echo rc=$?")
    assert last(out) == "rc=1", out.stdout
    assert not (gui / "tool-result").exists()
    assert not (run / "called").exists()


@pytest.mark.parametrize("token", ["tool-run|lsblk|a b|", "tool-run|../x||", "tool-run|lsblk|$(id)|"])
def test_a_hostile_token_is_refused_before_anything_runs(tmp_path, token):
    _, run, gui, pre = setup(tmp_path, {"disk": DISK_MODULE})
    out = sh(pre + f"tools_gui_run {token!r}; echo rc=$?")
    assert last(out) == "rc=1"
    assert not (gui / "tool-result").exists()
    assert not (run / "called").exists()
