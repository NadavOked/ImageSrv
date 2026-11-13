"""#1472: הקליטה מריצה partclone עם `-a0 -z 524288`, כמו השיכפול המקומי.

הכרעת נדב 16/10. `-a0` מוריד את ה-checksum של partclone עצמו, ו-`-z` הוא
חוצץ של 512KB — נמדד על מחשב שיכפול 2 (i5-650, 15/10, `lccopy.sh:116-118`):
108 → 157MB/s ו-160 → 170MB/s. בטוח כי `fanout -S` מגבב את כל הזרם הדחוס
והשרת מאמת את ה-sha256 הזה לפני שהאימג' נכנס לספרייה (עיקרון 6).

`partclone.dd` אינו מקבל אף אחד מהם — אותו כלל כמו ב-`lccopy.sh:119`: הוא
מעתיק בייט-בייט ואינו מכיר את הדגלים של כלי מערכות הקבצים.

הבדיקה רצה על `capture_disk` האמיתי (ההרנס של test_capture_refusals),
ו-partclone מזויף רושם את שורת הפקודה שקיבל.
"""

from __future__ import annotations

from native import requires_native
from test_agent import BASH
from test_capture_refusals import CURL_SINK, ONE_PARTITION, capture_run

pytestmark = requires_native(("bash", BASH))

#: partclone מזויף: רושם את הארגומנטים ומזרים 4KB — מספיק לזרם שלם.
RECORDING_PARTCLONE = (
    '#!/bin/sh\n'
    'printf "%s\\n" "$*" >> "$RUN_DIR/pcl.args"\n'
    'head -c 4096 /dev/zero\n'
    'exit 0\n'
)


def _args(tmp_path, fs: str, tool: str) -> list[str]:
    box, run, out = capture_run(
        tmp_path,
        stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK, tool: RECORDING_PARTCLONE},
        shell_pre=f"_fs_of() {{ echo {fs}; }}; ",
    )
    assert out.strip().endswith("rc=0"), \
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    # ראיה חיובית שהכלי הזה הוא שרץ — קובץ חסר אינו "בלי דגלים".
    recorded = (run / "pcl.args").read_text(encoding="utf-8").splitlines()
    assert len(recorded) == 1, recorded
    return recorded[0].split()


def test_a_filesystem_partclone_captures_without_its_checksum_and_with_a_512k_buffer(tmp_path):
    args = _args(tmp_path, "vfat", "partclone.fat")
    assert "-c" in args, args
    assert "-a0" in args, args
    at = args.index("-z")
    assert args[at + 1] == "524288", args


def test_partclone_dd_gets_neither_flag(tmp_path):
    args = _args(tmp_path, "unknown", "partclone.dd")
    assert "-a0" not in args and "-z" not in args, args
