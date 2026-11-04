"""‏#1635 — שלושה ערכים מתשובת השרת שהסוכן הכניס לנתיב או לקובץ המצב בלי רשימת היתר.

המקור בכולם הוא השרת בלבד, ולכן זו הקשחה ולא חולשה — אבל הסוכן רץ כ-root
על הדיסק המשוחזר, ושם תשובה פגומה היא מחיקה ולא תצוגה שגויה:

1. **שמות חבילות דרייברים** (`postdeploy.sh`): כל שם הוא תיקייה תחת
   ‏`ImageSrv/Drivers/` שעוברת `rm -rf` — גם חבילה בלי קבצים, שאותה
   ‏`_drivers_fetch` (שבודק `_pkg`) לא רואה כלל. ‏`../../Windows` מחק את
   מחיצת Windows. עכשיו: התווים של `NAME_RE` ב-`drivers.py`, לפני הורדה ועיגון.
2. **‏`.task.disk`** (`task.sh`, ‏`directsend.sh`): נכנס ל-`$RUN_DIR/targets/<disk>`
   ול-`/dev/<disk>`. עכשיו ‏`json_disk` (jsonq.sh) — שם קרנל או כלום, והמשימה
   נכשלת בגלוי (דיווח `failed`) בלי ש-`capture_disk`/`direct_send_run` נקראו.
3. **שם האימג' במסך המשכפל** (`clonergui.sh`): ‏`\\n` בשם הוסיף רשומת state.
   עכשיו ‏`tr '\\r\\n|'` כמו בשאר הרשומות.

הכול רץ על ה-sh האמיתי עם `json_get` מזויף — אין jq בווינדוס, והפילטרים
עצמם אינם מה שנבדק.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_cloner_gui import PORTS, PRELUDE, env, sh, target
from test_postdeploy import FILES, Box, answer_for

pytestmark = requires_native(("bash", BASH))


# --- 1. שמות חבילות דרייברים ---------------------------------------------------


@pytest.mark.parametrize("evil", ["../../Windows", "..", "a/b", "a b", ".hidden", "-rf"])
def test_a_package_name_outside_the_allowlist_fails_before_the_disk_is_touched(tmp_path, evil):
    """חבילה בלי קבצים ושם מחוץ לרשימה — לצד חבילה תקינה. ‏`Drivers/` כבר קיים
    בדיסק (אימג' שנקלט ממכונה שקיבלה דרייברים), ולכן `../../Windows` אמיתי:
    בלי התיקון ה-`rm -rf` מוחק את `Windows/` ואיתו את הכוורת."""
    answer = answer_for(FILES)
    answer["packages"].append({"name": evil, "by": "pci", "files": []})
    box = Box(tmp_path, answer=answer)
    (box.win / "ImageSrv/Drivers").mkdir(parents=True)
    _, result = box.stage()
    assert box.hive.exists(), "Windows/System32/config/SOFTWARE נמחק מהדיסק המשוחזר"
    assert result["state"] == "failed" and "שם חבילה לא תקין" in result["error"], result
    assert result["packages"] == []
    assert box.staged_files() == {}, "בייט אחד לא הועתק לדיסק"
    assert box.calls() == [], "המחיצה לא עוגנה כלל"


def test_a_name_with_the_allowed_punctuation_is_staged(tmp_path):
    """הצד השני: נקודה, מקף וקו תחתון מותרים ב-`NAME_RE` — אינם נדחים כאן."""
    packages = {"Realtek_8168.v2-x64": {"rt640x64.inf": b"[Version] rt\n"}}
    box = Box(tmp_path, packages=packages, answer=answer_for(packages))
    _, result = box.stage()
    assert result["state"] == "staged" and result["packages"] == ["Realtek_8168.v2-x64"], result
    assert box.staged_files() == {"Realtek_8168.v2-x64/rt640x64.inf": b"[Version] rt\n"}


# --- 2. ‏.task.disk -------------------------------------------------------------

#: מה שמסביב ל-`do_task`/`direct_send_task` — מסך, דיווח, החזקה — מזויף, ושני
#: הצרכנים של שם הדיסק רושמים עם מה נקראו. ‏`report_final` רושם את המצב שנשלח.
SURROUND = (
    'log() { printf "%s\\n" "$*" >> "$RUN_DIR/agent.log"; }; '
    'ui_clear() { :; }; ui_header() { :; }; progress_loop() { :; }; gui_parent() { :; }; '
    'hold_unheard() { :; }; ui_error_hold() { :; }; hold_beat() { :; }; build_end_ask() { :; }; '
    'shrink_restore_source() { :; }; capture_unhib_note() { :; }; upload_manifest() { :; }; '
    'job_reset() { echo waiting > "$RUN_DIR/state"; }; '
    'report_final() { echo "REPORT $(cat "$RUN_DIR/state")" >> "$RUN_DIR/trace"; }; '
    'capture_disk() { echo "CAPTURE <$2>" >> "$RUN_DIR/trace"; return 1; }; '
    'direct_send_run() { echo "SEND <$2>" >> "$RUN_DIR/trace"; return 1; }; '
    'json_get() { case "$2" in .task.type) echo "$T_TYPE" ;; .task.id) echo tsk_1635 ;; '
    '.task.disk) printf "%s\\n" "$T_DISK" ;; *) echo null ;; esac; }; '
)


def run_task(tmp_path: Path, kind: str, disk: str) -> tuple[list[str], str]:
    run = tmp_path / "run"
    run.mkdir()
    script = (
        f"export RUN_DIR={posix(run)!r} RESP=/dev/null MAC=aa:bb:cc:dd:ee:ff "
        f"SERVER=http://s IMAGESRV_TEST=1 T_TYPE={kind!r} T_DISK={disk!r}; "
        f". {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/core/task.sh; "
        f". {posix(AGENT)}/lib/capture/directsend.sh; "
        + SURROUND
        + 'do_task; echo "rc=$?"'
    )
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.stdout.strip().endswith("rc=1"), out.stdout + out.stderr
    trace = (run / "trace").read_text(encoding="utf-8").splitlines()
    log = (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""
    return trace, log


BAD_DISKS = ["../x", "../../tmp/x", "sda/../sdb", "sda;reboot", "SDA", "null", ""]


@pytest.mark.parametrize("kind,consumer", [("capture", "CAPTURE"), ("direct_send", "SEND")])
@pytest.mark.parametrize("disk", BAD_DISKS)
def test_a_disk_name_that_is_not_a_kernel_name_fails_the_task_before_it_is_used(tmp_path, kind, consumer, disk):
    trace, log = run_task(tmp_path, kind, disk)
    assert not any(t.startswith(consumer) for t in trace), f"שם הדיסק הגיע לנתיב: {trace}"
    assert trace == ["REPORT failed"], f"עיקרון 5: המשימה נכשלת בגלוי, ולשרת נאמר: {trace}"
    assert "refused (#1635)" in log or "refused -- not a kernel disk name (#1635)" in log, log


@pytest.mark.parametrize("kind,consumer", [("capture", "CAPTURE"), ("direct_send", "SEND")])
@pytest.mark.parametrize("disk", ["sda", "sdb", "nvme0n1", "vda", "mmcblk0"])
def test_a_kernel_disk_name_reaches_the_capture_unchanged(tmp_path, kind, consumer, disk):
    """הצד השני: שם קרנל עובר כפי שהוא — הרשימה אינה דוחה הכול."""
    trace, _ = run_task(tmp_path, kind, disk)
    assert trace == [f"{consumer} <{disk}>", "REPORT failed"], trace


# --- 3. שם האימג' במסך המשכפל ---------------------------------------------------


def test_a_newline_in_the_image_name_cannot_add_a_state_record(tmp_path):
    """‏`\\n` בשם מהמניפסט היה כותב שורה שנייה — כאן `drawer_count=8`, רשומה
    שהמסך קורא כמספר המגירות. אחרי התיקון: רשומת `cloner=` אחת, מקופלת לרווחים."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text("{}", newline="\n")
    target(run, "sda", "writing", 100, 40, "5")   # בלי response.json: gui_header_state קורא אותו ב-jq
    fake = ('json_get() { case "$1:$2" in *manifest.json:.name) '
            "printf 'Office\\r\\ndrawer_count=8|x' ;; *) echo null ;; esac; }; ")
    out = sh(env(run, gui) + PRELUDE + PORTS + fake + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    assert [ln for ln in lines if ln.startswith("cloner=")] == ["cloner=Office  drawer_count=8 x"], lines
    assert not any(ln.startswith("drawer_count=") for ln in lines), lines
