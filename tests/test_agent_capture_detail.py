"""#1505 — הצד של הסוכן בפירוט הקליטה: `capturedetail.sh` + `build_progress`.

הטבלה נבנית מ-`parts.txt` (כמו שהקליטה בונה אותה), מתעדכנת במהלך הזרימה,
ונישאת **רק** בדיווח של משימה. ‏`used_bytes` שלא נמדד הוא `null`, לא אפס;
וקריאת-עזר שחסרה (טסט שטוען רק את capture.sh) אינה מפילה קליטה.
רץ תחת bash, כמו `test_agent_capture_stage.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

LIB = AGENT / "lib"
ESP, WIN, SWAP = ("C12A7328-F81F-11D2-BA4B-00A0C93EC93B", "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
                  "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F")
#: idx|guid|uguid|start|size_sectors -- the shape capture.sh writes
PARTS = f"1|{ESP}|u1|2048|204800\n3|{WIN}|u3|206848|1000000\n4|{SWAP}|u4|1206848|2048\n"
STUBS = ('_fs_of() { case "$1" in *1) echo vfat ;; *3) echo ntfs ;; *4) echo swap ;; *) echo unknown ;; esac; }\n'
         'partition_node() { echo "/dev/$1$2"; }\n')


def run(tmp_path: Path, body: str, who: str = "task", parts: str = PARTS, setup: str = "") -> str:
    run_dir = tmp_path / "run"
    (run_dir / "targets").mkdir(parents=True, exist_ok=True)
    (run_dir / "parts.txt").write_text(parts, encoding="utf-8", newline="\n")
    ids = '"" b4:2e:99:07:1a:c4 tsk_4e10' if who == "task" else "ses_1 b4:2e:99:07:1a:c4"
    return sh(
        f'export RUN_DIR={posix(run_dir)!r} CAPTURE_LEVEL=3\n'
        f'. {posix(LIB)}/common.sh; . {posix(LIB)}/manifest.sh; . {posix(LIB)}/progress.sh; '
        f'. {posix(LIB)}/capturedetail.sh\n{STUBS}target_init sda 0; {setup}\n{body}\n'
        f'build_progress {ids}')


def target(tmp_path: Path, body: str = "cd_init sda \"$RUN_DIR/parts.txt\" 512", **kw) -> dict:
    return json.loads(run(tmp_path, body, **kw))["targets"][0]


def test_cd_init_describes_every_partition_of_the_table(tmp_path):
    t = target(tmp_path)
    assert t["compression"] == "zstd-3"
    assert t["partitions"] == [
        {"index": 1, "role": "esp", "fs": "vfat", "size_bytes": 204800 * 512, "used_bytes": None, "state": "waiting"},
        {"index": 3, "role": "windows", "fs": "ntfs", "size_bytes": 1000000 * 512, "used_bytes": None, "state": "waiting"},
        {"index": 4, "role": "swap", "fs": "swap", "size_bytes": 2048 * 512, "used_bytes": 0, "state": "waiting"},
    ]


def test_an_unmeasured_used_is_null_and_a_measured_one_survives_a_rebuild(tmp_path):
    body = ('cd_init sda "$RUN_DIR/parts.txt" 512; cd_used 3 5368709120; cd_used 1 null; cd_used 1 ""; '
            'cd_init sda "$RUN_DIR/parts.txt" 512')        # אחרי הכיווץ הטבלה נבנית מחדש
    used = {p["index"]: p["used_bytes"] for p in target(tmp_path, body)["partitions"]}
    assert used == {1: None, 3: 5368709120, 4: 0}, "used לא נמדד הפך ל-0, או שנמדד אבד בבנייה מחדש"


def test_the_states_walk_through_shrinking_reading_done(tmp_path):
    body = ('cd_init sda "$RUN_DIR/parts.txt" 512; cd_state 3 shrinking; cd_part 1 esp vfat reading; '
            'cd_part 4 swap swap done')
    states = {p["index"]: p["state"] for p in target(tmp_path, body)["partitions"]}
    assert states == {1: "reading", 3: "shrinking", 4: "done"}


def test_the_role_and_fs_found_while_reading_replace_the_guess(tmp_path):
    """תפקיד מה-GUID הוא ניחוש עד שנבדקה מחיצת המערכת (#1369): data ולא windows."""
    body = 'cd_init sda "$RUN_DIR/parts.txt" 512; cd_part 3 data ntfs reading'
    part = next(p for p in target(tmp_path, body)["partitions"] if p["index"] == 3)
    assert (part["role"], part["state"]) == ("data", "reading")


def test_no_table_means_no_field_not_an_empty_list(tmp_path):
    t = target(tmp_path, ":")
    assert "partitions" not in t and "compression" not in t


def test_a_round_report_never_carries_the_capture_table(tmp_path):
    assert "partitions" not in target(tmp_path, who="session")


def test_editing_a_table_that_was_never_built_is_a_no_op(tmp_path):
    t = target(tmp_path, "cd_state 3 shrinking; cd_part 1 esp vfat done; cd_used 3 5")
    assert "partitions" not in t


def test_hostile_characters_cannot_break_the_json(tmp_path):
    body = 'cd_init sda "$RUN_DIR/parts.txt" 512; cd_part 3 \'wi"n\' \'nt|fs\' reading'
    part = next(p for p in target(tmp_path, body)["partitions"] if p["index"] == 3)
    assert (part["role"], part["fs"], part["state"]) == ("win", "ntfs", "reading"), "תו | הזיז עמודות"
    assert part["used_bytes"] is None and part["size_bytes"] == 1000000 * 512


def test_the_capture_flow_marks_the_table_at_every_step():
    """הזרימה עצמה דורשת דיסק; כאן נבדק שכל נקודה מסומנת במקומה, שהקריאות לא
    מפילות קליטה כשהעזר חסר, ושהסוכן טוען את הקובץ לפני capture.sh."""
    capture = (LIB / "capture.sh").read_text(encoding="utf-8")
    sink = (LIB / "capturesink.sh").read_text(encoding="utf-8")
    shrink = (LIB / "shrink.sh").read_text(encoding="utf-8")
    calls = re.findall(r"\bcd_(init|part|state|used)\b[^\n]*", capture + sink + shrink)
    assert sorted(calls) == sorted(["init", "init", "part", "used", "part", "part", "used", "state", "state"]), calls
    for source in (capture, sink, shrink):
        for line in re.findall(r"^.*\bcd_(?:init|part|state|used) .*$", source, re.M):
            assert "2>/dev/null || :" in line, f"קריאה שיכולה להפיל קליטה: {line.strip()}"
    order = capture.index('capture_stage shrink; cd_init'), capture.index("capture_space_preflight"), \
        capture.index("capture_prepare_views"), capture.index('capture_stage stream; cd_init')
    assert list(order) == sorted(order), "הטבלה נבנית לפני ה-preflight ומחדש אחרי הכיווץ"
    agent = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert agent.index('"$LIB_DIR/capturedetail.sh"') < agent.index('"$LIB_DIR/capture.sh"')


def test_a_non_number_never_lands_in_the_used_column(tmp_path):
    """הקובץ עצמו, לא רק ה-JSON: ‏`-` = לא נמדד, ו-`null`/טקסט/ריק אינם נכתבים אליו."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "parts.txt").write_text(PARTS, encoding="utf-8", newline="\n")
    out = sh(
        f'export RUN_DIR={posix(run_dir)!r}\n'
        f'. {posix(LIB)}/common.sh; . {posix(LIB)}/manifest.sh; . {posix(LIB)}/capturedetail.sh\n{STUBS}'
        'cd_init sda "$RUN_DIR/parts.txt" 512; cd_used 1 null; cd_used 1 12abc; cd_used 1 ""; cd_used 3 42\n'
        "awk -F'|' '{ print $1 \"=\" $5 }' \"$RUN_DIR/cdetail.txt\"")
    assert out.split() == ["1=-", "3=42", "4=0"], out
