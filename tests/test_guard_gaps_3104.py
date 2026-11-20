"""#3104: ענפי שומרי עיקרון 5 ו-6 ש-mutmut מצא ואף טסט לא נעל.

כל טסט כאן נקרא על הפונקציה הטהורה, ובודק **ערך** (הודעת הכשל בשמה או
ה-`blocked`), לא רק "משהו נכשל". כל אחד מהם נכשל כשמחזירים את הקוד
שמתחתיו למוטנט שתואר ב-#3104.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from server.agent.bootguard import ATTEMPT_LIMIT, repeats
from server.library.capture import manifest_problem

ESP_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
SWAP_GUID = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
WINDOWS_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
DISK_GUID = "047B3400-0000-0000-0000-0000003AEE00"
BODY = b"x" * 4096


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _esp(**over):
    part = {"index": 1, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
            "start_sector": 2048, "size_bytes": 104857600, "used_bytes": 1000,
            "file": "p1.esp.pcl.zst", "sha256": _sha(BODY), "index_xxh64": "1" * 16,
            "expandable": False}
    part.update(over)
    return part


def _swap():
    return {"index": 2, "type_guid": SWAP_GUID, "role": "swap", "fs": "swap",
            "start_sector": 300000, "size_bytes": 1073741824, "used_bytes": 0,
            "file": None, "sha256": None, "expandable": False}


def _windows(**over):
    part = {"index": 3, "type_guid": WINDOWS_GUID, "role": "windows", "fs": "ntfs",
            "start_sector": 3000000, "size_bytes": 250000000000, "used_bytes": 1000,
            "file": "p3.windows.pcl.zst", "sha256": _sha(BODY), "index_xxh64": "3" * 16,
            "expandable": False}
    part.update(over)
    return part


def _manifest(parts, **over):
    manifest = {"schema": 1, "image_format": 2, "family": 256,
                "source_disk_bytes": 256060514304, "min_target_bytes": 256060514304,
                "scheme": "gpt", "sector_size": 512, "disk_guid": DISK_GUID,
                "partitions": parts, "total_compressed_bytes": len(BODY) * 2,
                "compression": "zstd-9"}
    manifest.update(over)
    return manifest


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "p1.esp.pcl.zst").write_bytes(BODY)
    return tmp_path


def test_the_baseline_manifest_passes(folder):
    """הבקרה החיובית: בלי זה כל הטסטים הבאים עוברים גם על שומר שדוחה הכול."""
    (folder / "p3.windows.pcl.zst").write_bytes(BODY)
    assert manifest_problem(_manifest([_esp(), _swap(), _windows()]), folder) is None


# --- manifest_problem ----------------------------------------------------------


def test_a_swap_partition_does_not_stop_the_checks_of_the_partitions_after_it(folder):
    """swap שאינה אחרונה: מחיצה אחריה ללא קובץ חייבת להיתפס בשמה.

    המוטנט (`continue` -> `break`) דילג על כל המחיצות שאחרי ה-swap, ואימג'
    בלי קובץ של מחיצת Windows נכנס לספרייה."""
    problem = manifest_problem(_manifest([_esp(), _swap(), _windows()]), folder)
    assert problem == "partition file was never uploaded: p3.windows.pcl.zst"


def test_an_unsafe_partition_file_name_is_refused_by_name(folder):
    problem = manifest_problem(_manifest([_esp(file="../x")]), folder)
    # #3104: ה-`_validate` של הספרייה תופס את השם לפני הבדיקה הנפרדת של
    # `manifest_problem`; חשוב שהשם נדחה בשמו ולא נזרקת חריגה.
    assert problem == "partition 1 has an unsafe file name"


@pytest.mark.parametrize("parts", ["abc", {"a": 1}, 5, [], None])
def test_partitions_that_are_not_a_nonempty_list_are_refused_with_a_message(folder, parts):
    problem = manifest_problem(_manifest(parts), folder)
    assert isinstance(problem, str) and problem


@pytest.mark.parametrize("family, refused", [(256, False), (500, False), (501, True),
                                            (255, True), (None, True)])
def test_only_family_256_and_500_are_accepted(folder, family, refused):
    (folder / "p3.windows.pcl.zst").write_bytes(BODY)
    problem = manifest_problem(_manifest([_esp(), _windows()], family=family), folder)
    if refused:
        assert problem is not None and problem.startswith("family must be 256 or 500")
    else:
        assert problem is None


def test_two_expandable_partitions_are_refused_but_one_is_accepted(folder):
    (folder / "p3.windows.pcl.zst").write_bytes(BODY)
    one = manifest_problem(_manifest([_esp(), _windows(expandable=True)]), folder)
    two = manifest_problem(
        _manifest([_esp(expandable=True), _windows(expandable=True)]), folder)
    assert one is None
    assert two == "at most one expandable partition"


# --- bootguard.repeats -----------------------------------------------------------


def _conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE boot_attempts (mac TEXT PRIMARY KEY, context TEXT NOT NULL,"
                 " attempts INTEGER NOT NULL, first_at TEXT, last_at TEXT)")
    for mac, context, attempts in rows:
        conn.execute("INSERT INTO boot_attempts VALUES (?, ?, ?, 'a', 'b')",
                     (mac, context, attempts))
    return conn


def test_blocked_flips_exactly_one_past_the_budget():
    """התקציב של #75: ATTEMPT_LIMIT ניסיונות מותרים, והבא אחריהם חסום."""
    conn = _conn([("aa:00:00:00:00:01", "ses_1", ATTEMPT_LIMIT),
                  ("aa:00:00:00:00:02", "ses_1", ATTEMPT_LIMIT + 1)])
    got = repeats(conn, "ses_1")
    assert got["aa:00:00:00:00:01"] == {"attempts": ATTEMPT_LIMIT, "blocked": False}
    assert got["aa:00:00:00:00:02"] == {"attempts": ATTEMPT_LIMIT + 1, "blocked": True}


def test_repeats_lists_the_second_boot_and_not_the_first():
    conn = _conn([("aa:00:00:00:00:01", "ses_1", 1), ("aa:00:00:00:00:02", "ses_1", 2),
                  ("aa:00:00:00:00:03", "ses_2", 5)])
    assert list(repeats(conn, "ses_1")) == ["aa:00:00:00:00:02"]
