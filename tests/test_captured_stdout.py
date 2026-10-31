"""‏#876: פונקציה שהפלט שלה נלכד ב-`$( )` — ה-stdout שלה הוא הערך בלבד.

‏`log()` (common.sh) כותב גם ליומן וגם ל-stdout. פונקציה שנלכדת ב-`$( )`
וקוראת ל-`log` מכניסה את שורת היומן לתוך הערך שהקורא לוכד: ‏`hostname.json`
קיבל `imagesrv: writing hostname ...` **לפני** ה-JSON — בהצלחה, לא רק
בכשל. התיקון מקומי (`log ... >&2` בפונקציה), לא ב-`log()` עצמו.

הרשימה כאן **מפורשת** — כל פונקציה שנמצאה נלכדת ב-`$( )` בסריקת
`agent/lib` (16/09) — ולא היוריסטיקה שסורקת קוד: כל פונקציה מורצת בפועל עם
זיופים של הכלים שהיא קוראת (mount/ntfs-3g/hivewrite/umount/jq/od), וה-stdout
המלא שלה מושווה לערך. ‏`.splitlines()[-1]` — הצורה שבה הטסטים הישנים קראו
את התוצאה — היא בדיוק מה שהסתיר את הזיהום, ולכן כאן קוראים את **כל** הפלט.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from native import requires_native
from test_smart import AGENT, BASH, posix
from agentlib import lib_path

pytestmark = requires_native(("bash", BASH))

#: הפונקציות הנלכדות ב-`$( )` שקוראות ל-`log` (ישירות או דרך עוזרות) —
#: (קובץ, שם). הסריקה: `grep -n '=\$(' agent/lib/*.sh agent/imagesrv-agent`
#: + קריאת כל פונקציה ומה שהיא קוראת. ‏smart_send_event (#875) אינה כאן —
#: תוקנה ב-PR #879 ונבדקת ב-test_smart.
CAPTURED_LOGGERS = [
    ("hostname.sh", "_mount_windows"),
    ("hostname_linux.sh", "_mount_linux"),
    ("hostname.sh", "_umount_checked"),
    ("hostname_linux.sh", "_write_hostname_linux"),
    ("hostname.sh", "_control_set"),
    ("hostname.sh", "write_hostname"),
    ("hostname.sh", "_write_hostname_windows"),  # ‏#107: נלכד ב-dual-boot
    ("restore.sh", "manifest_plan"),
    ("monitor.sh", "monitor_secret"),
]


def sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(AGENT.parent), stdin=subprocess.DEVNULL,
    )


def stub(path: Path, body: str) -> str:
    # ‏chmod דרך bash: ‏os.chmod של פייתון על ווינדוס אינו קובע את סיבית
    # ה-exec ש-Git Bash קורא (כמו ב-test_agent._stub).
    return (f"cat > {posix(path)} <<'STUB'\n#!/bin/sh\n{body}\nSTUB\n"
            f"chmod 0755 {posix(path)}\n")


WIN_PLAN = "1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|4096|1|p1|y|false|u"
LINUX_PLAN = "2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|4096|1|p2|y|false|u"

#: ‏hivewrite מזויף: קריאת Select\Current מחזירה 1, כל קריאה אחרת את השם
#: שנכתב (האימות עובר); כתיבה יוצאת 0.
HIVEWRITE_OK = (
    'if [ "$1" = "-g" ]; then\n'
    '  case "$*" in *Current*) echo 1;; *) echo LAB1-05;; esac\n'
    '  exit 0\nfi\nexit 0')


def box(tmp_path: Path, stubs: dict[str, str], plan: str = WIN_PLAN) -> tuple[str, Path]:
    """‏RUN_DIR + PATH עם הזיופים; manifest_plan ו-partition_node נדרסים אחרי
    ה-source (כמו בטסטי hostname הקיימים) כדי לא לדרוש jq."""
    stub_dir = tmp_path / "stubs"; stub_dir.mkdir()
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    script = "".join(stub(stub_dir / name, body) for name, body in stubs.items())
    script += (
        f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
        f'LOG_FILE={posix(run / "agent.log")!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/restore.sh; '
        f'. {posix(AGENT)}/lib/postdeploy/hostname.sh; . {posix(AGENT)}/lib/postdeploy/unattend.sh; . {posix(AGENT)}/lib/postdeploy/hostname_linux.sh; '
        f'manifest_plan() {{ printf \'%s\\n\' {plan!r}; }}; '
        f'partition_node() {{ echo /dev/fake; }}; '
    )
    return script, run


def windows_box(tmp_path: Path, **override: str) -> tuple[str, Path]:
    run = tmp_path / "run"
    hive = run / "win" / "Windows" / "System32" / "config"
    hive.mkdir(parents=True)
    (hive / "SYSTEM").write_text("hive", encoding="utf-8")  # מה ש-ntfs-3g היה חושף
    stubs = {"ntfs-3g": "exit 0", "hivewrite": HIVEWRITE_OK, "umount": "exit 0"}
    stubs.update(override)
    return box(tmp_path, stubs)


def linux_box(tmp_path: Path, **override: str) -> tuple[str, Path]:
    run = tmp_path / "run"
    (run / "linux" / "etc").mkdir(parents=True)  # מה ש-mount היה יוצר
    stubs = {"mount": "exit 0", "umount": "exit 0"}
    stubs.update(override)
    return box(tmp_path, stubs, plan=LINUX_PLAN)


def only_json(out: subprocess.CompletedProcess) -> dict:
    """ה-stdout כולו הוא אובייקט JSON אחד — לא שורת יומן לפניו."""
    assert not out.stdout.startswith("imagesrv:"), out.stdout
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 1, out.stdout
    return json.loads(lines[0])


# --- write_hostname: ההצלחה עצמה הייתה מזוהמת ---------------------------------

def test_windows_hostname_success_prints_only_the_result(tmp_path):
    """הבאג של #876: `log "writing hostname ..."` נכנס ל-stdout לפני ה-JSON."""
    script, run = windows_box(tmp_path)
    out = sh(script + 'write_hostname sda /dev/null LAB1-05')
    assert out.returncode == 0, out.stderr
    result = only_json(out)
    assert result == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"}
    # השורה עדיין נרשמת — ביומן, ולא בערך.
    assert "writing hostname LAB1-05 into ControlSet001" in (run / "agent.log").read_text()


def test_linux_hostname_success_prints_only_the_result(tmp_path):
    script, run = linux_box(tmp_path)
    out = sh(script + 'write_hostname sda /dev/null LAB1-05')
    assert out.returncode == 0, out.stderr
    assert only_json(out)["ok"] is True
    assert (run / "linux/etc/hostname").read_text().strip() == "LAB1-05"


def test_hostname_json_is_valid_json_after_a_successful_name(tmp_path):
    """‏name_this_machine כותב את מה ש-write_hostname הדפיס ל-hostname.json;
    ‏#856 מציע לשלוח אותו לשרת — הוא חייב להיות JSON תקין. ‏json_get נדרס
    (אין jq בבדיקה); השם עצמו מורכב ע"י compose_hostname האמיתית."""
    script, run = windows_box(tmp_path)
    (run / "response.json").write_text("{}", encoding="utf-8")
    out = sh(script
             + f'RESP={posix(run / "response.json")!r}; '
             'json_get() { case "$2" in *prefix) echo lab1;; *suffix) echo 05;; esac; }; '
             'name_this_machine sda sess1')
    assert out.returncode == 0, out.stderr
    text = (run / "hostname.json").read_text(encoding="utf-8")
    assert not text.startswith("imagesrv:"), text
    assert json.loads(text) == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"}
    # #1935: name_this_machine leaves 'naming'; done is the caller's, after the drivers and the mark
    assert (run / "state").read_text().strip() == "naming"


def test_a_failed_verify_prints_only_the_error_object(tmp_path):
    # read-back מחזיר שם אחר → `log "hostname verify failed ..."` + JSON של כשל.
    wrong = HIVEWRITE_OK.replace("echo LAB1-05", "echo OTHER")
    script, _ = windows_box(tmp_path, hivewrite=wrong)
    out = sh(script + 'write_hostname sda /dev/null LAB1-05')
    assert out.returncode == 1
    assert only_json(out)["code"] == "hive_write_failed"


def test_a_failed_umount_prints_only_the_error_object(tmp_path):
    # ‏_umount_checked רושמת `log "umount of ... failed"` בתוך הפונקציה הנלכדת.
    for make in (windows_box, linux_box):
        script, _ = make(tmp_path / make.__name__, umount="exit 1")
        out = sh(script + 'write_hostname sda /dev/null LAB1-05')
        assert out.returncode == 1
        assert only_json(out)["code"] == "umount_failed"


# --- העוזרות: בכשל stdout ריק, הקוד אומר הכול -----------------------------------

def test_mount_helpers_print_nothing_on_failure(tmp_path):
    cases = {
        "win-mount": (windows_box, {"ntfs-3g": "exit 1"}, "_mount_windows sda /dev/null"),
        "lin-mount": (linux_box, {"mount": "exit 1"}, "_mount_linux sda /dev/null"),
        "lin-noetc": (linux_box, {}, "rm -r \"$RUN_DIR/linux/etc\"; _mount_linux sda /dev/null"),
        "win-noplan": (windows_box, {}, 'manifest_plan() { :; }; _mount_windows sda /dev/null'),
        "lin-noplan": (linux_box, {}, 'manifest_plan() { :; }; _mount_linux sda /dev/null'),
    }
    for name, (make, over, call) in cases.items():
        script, _ = make(tmp_path / name, **over)
        out = sh(script + call)
        # ‏#89: ‏_mount_linux מחזיר קוד שאומר *למה* — 2 = עוגן אבל אין /etc.
        assert out.returncode == (2 if name == "lin-noetc" else 1), (name, out.stdout, out.stderr)
        assert out.stdout == "", (name, out.stdout)


def test_control_set_prints_nothing_when_select_current_is_unreadable(tmp_path):
    unreadable = HIVEWRITE_OK.replace("echo 1;;", "exit 3;;")
    script, run = windows_box(tmp_path, hivewrite=unreadable)
    out = sh(script + '_control_set "$RUN_DIR/win/Windows/System32/config/SYSTEM"')
    assert out.returncode == 1
    assert out.stdout == "", out.stdout
    assert "could not read Select" in (run / "agent.log").read_text()


def test_manifest_plan_prints_nothing_on_a_partial_plan(tmp_path):
    """‏#51: jq רינדר 1 מתוך 3 מחיצות → manifest_plan נכשל בקול. השורה
    ‏`plan: 1 of 3 partitions rendered` הולכת ליומן, לא לתוכנית שהקורא לוכד
    (הקוראים מסננים ב-awk, ולכן זה לא נראה — אותו דפוס בדיוק)."""
    run = tmp_path / "run"; run.mkdir()
    stub_dir = tmp_path / "stubs"; stub_dir.mkdir()
    jq = 'case "$*" in *length*) echo 3;; *) echo "1|g|esp|vfat|2048|1|p1|x|false|u|";; esac'
    out = sh(stub(stub_dir / "jq", jq)
             + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
             f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "agent.log")!r}; '
             f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/restore.sh; '
             'manifest_plan /dev/null')
    assert out.returncode == 1, out.stderr
    assert out.stdout == "", out.stdout
    assert "plan: 1 of 3 partitions rendered" in (run / "agent.log").read_text()


def test_monitor_secret_prints_nothing_when_no_secret_can_be_drawn(tmp_path):
    # ‏od מזויף שמדפיס כלום = אין סוד; sysinfo לוכד `_ms=$(monitor_secret)`.
    run = tmp_path / "run"; run.mkdir()
    stub_dir = tmp_path / "stubs"; stub_dir.mkdir()
    out = sh(stub(stub_dir / "od", "exit 0")
             + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
             f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "agent.log")!r}; '
             f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/monitor.sh; '
             'monitor_secret')
    assert out.returncode == 1, out.stderr
    assert out.stdout == "", out.stdout


# --- שומר סטטי על הרשימה: כל `log` בפונקציה נלכדת מופנה ל-stderr --------------

def function_body(lib: str, name: str) -> str:
    text = (lib_path(lib)).read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}", text, re.S | re.M)
    assert m, f"{name} not found in {lib}"
    return m.group(1)


def test_every_captured_function_sends_log_to_stderr():
    """מסלולים שהטסטים למעלה אינם מריצים (btrfs subvol, jq שנפל) נתפסים כאן:
    בגוף כל פונקציה מהרשימה, כל קריאה ל-`log` מסתיימת ב-`>&2`."""
    bare = []
    for lib, name in CAPTURED_LOGGERS:
        for line in function_body(lib, name).splitlines():
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            if re.search(r"(^|[;{]|\|\||&&)\s*log\s", code) and ">&2" not in code:
                bare.append(f"{lib}:{name}: {line.strip()}")
    assert bare == [], "\n".join(bare)
