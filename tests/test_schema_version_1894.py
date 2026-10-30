"""‏#1894 — גרסת סכמה ב-DB, סירוב לעלות על DB חדש מהקוד, והחזרת ה-DB ב-rollback.

‏`PRAGMA user_version` נכתב בכל `_initialize`. קוד שפוגש DB מגרסה גבוהה משלו
(חזרה לגרסה קודמת אחרי שהחדשה כבר מיגרה) מסרב בשם — **לפני** שהוא מריץ עליו
את המיגרציות שלו. ‏`server-upgrade.sh` מעתיק את ה-DB לפני ה-checkout, ומחזיר
את העותק רק אם הגרסה החדשה באמת מיגרה.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from server import db
from server.db import SchemaError, connect

#: טביעת הסכמה של DB טרי ב-SCHEMA_VERSION הנוכחי. שינית סכמה? העלה את
#: ‏`db.SCHEMA_VERSION` **ועדכן כאן** — אחרת קוד ישן לא יזהה DB שמיגרת.
#: ‏getattr: בבקרה השלילית (קוד בלי הקבוע) הטסטים נכשלים על ההתנהגות, לא על ייבוא.
VERSION = getattr(db, "SCHEMA_VERSION", 1)

FINGERPRINTS = {1: "f35aff9de1e2da2378ec9825cedb3248626acaa849d6e23d2209ace73279eb5a",
                2: "6555b21e8e125ee271529f88e22355d64af5e632141cf8f31f356b9eaa77bcb5",
                # 3: room_rounds.cancelled_serials (#1936, 21/10)
                3: "141bd99f9a4446a44295e36d2b56eca1a2accab612d6d34dccbf509740329337",
                # 4: tasks.disk_serial (#1901, 22/10)
                4: "ecc5ede60757e7f40b7032a9f73e33affbc12da8507e89fb1ae58719ec3d61bc",
                # 5: model_slot_maps (#2001, 22/10; 4 in its PR -- #1901 took 4 first in lab/int-1022)
                5: "f79a1deed0176e55c865a0fb33b907acdd5a1b008dc904ac230067faaaa62790",
                # 6: net_devices.held_at + held_json (#2031, PR #2036, 22/10; it did not bump)
                6: "745aae931b0522af00e91d463f7a8ccb84894c6eb4f133ca80160d92923ae02c",
                # 7: applied_timing (#2056/#2059), disk sets + inventory (#1857), firmware images (#2080) -- mockups 05/04/06, 24/10
                7: "47896c6739283e20dfc43b9458dc2d6f039de4717af91caa89b20004e3387a1c"}


def _fingerprint(conn) -> str:
    rows = sorted(r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def test_a_fresh_database_carries_the_schema_version(tmp_path):
    conn = connect(tmp_path / "imagectl.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION


def test_a_database_from_before_versions_is_stamped(tmp_path):
    path = tmp_path / "imagectl.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION


@pytest.mark.parametrize("found", [3, 4])
def test_both_lab_int_1022_migrations_run_from_3_and_from_4(tmp_path, found):
    """lab/int-1022: #1901 (tasks.disk_serial) is 4 and #2001 (model_slot_maps) is 5 --
    both PRs said 4. A server on 3 (before both) or on 4 (#1901 only) reaches 5 with
    both in place, stamped 5."""
    path = tmp_path / "imagectl.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE model_slot_maps")
    if found == 3:
        raw.execute("ALTER TABLE tasks DROP COLUMN disk_serial")
    raw.execute(f"PRAGMA user_version = {found}")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 7
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'model_slot_maps'").fetchone()
    assert "disk_serial" in [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]


def test_a_database_on_5_gains_the_held_columns_and_is_stamped_6(tmp_path):
    """#2031 (PR #2036) added net_devices.held_at/held_json without a bump. A server
    already on 5 runs ADDED_COLUMNS, gains both, and is stamped 6."""
    path = tmp_path / "imagectl.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE net_devices DROP COLUMN held_json")
    raw.execute("ALTER TABLE net_devices DROP COLUMN held_at")
    raw.execute("PRAGMA user_version = 5")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 7
    columns = [r[1] for r in conn.execute("PRAGMA table_info(net_devices)")]
    assert "held_at" in columns and "held_json" in columns


def test_a_database_newer_than_the_code_is_refused_and_left_untouched(tmp_path):
    path = tmp_path / "imagectl.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE future_only (x)")
    raw.execute(f"PRAGMA user_version = {VERSION + 1}")
    raw.commit()
    raw.close()
    with pytest.raises(SchemaError, match="#1894"):
        connect(path)
    raw = sqlite3.connect(path)
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"future_only"}                       # אף מיגרציה לא רצה עליו
    assert raw.execute("PRAGMA user_version").fetchone()[0] == VERSION + 1


def test_a_schema_change_must_bump_the_version(tmp_path):
    conn = connect(tmp_path / "imagectl.db")
    assert VERSION in FINGERPRINTS, "העלית את SCHEMA_VERSION — רשום כאן את הטביעה"
    assert _fingerprint(conn) == FINGERPRINTS[VERSION], (
        "הסכמה השתנתה ו-db.SCHEMA_VERSION לא עלה (#1894)")


# --- server-upgrade.sh: עותק לפני, והחזרה רק אחרי מיגרציה ----------------------

def _upgrade_with_live(tmp_path, live_script: str):
    from test_installer_1131 import (BASH, _fake_repo, _run_upgrade, _seed_db, _status,
                                     _upgrade_fakes, calls)
    if not BASH:
        pytest.skip("server-upgrade.sh is bash")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=False)
    # ‏"השרת החדש" — כותב ל-DB (ואולי מיגר) ואינו עונה ב-/health/live.
    (repo / "install" / "console-live.sh").write_text(
        "#!/usr/bin/env bash\n" + live_script + "\nexit 1\n", encoding="utf-8", newline="\n")
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    raw = sqlite3.connect(data / "imagectl.db")
    return proc, raw, _status(data), calls(tmp_path), data


WRITE = ('python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); '
         "c.execute(\\\"INSERT INTO journal (ts,user,event,detail) VALUES ('t','','after_update','')\\\")"
         '{bump}; c.commit()" "$IMAGECTL_DATA_DIR/imagectl.db"')


def test_rollback_after_a_migration_restores_the_database_and_says_so(tmp_path):
    bump = f"; c.execute('PRAGMA user_version = {VERSION + 1}')"
    proc, raw, status, seen, data = _upgrade_with_live(tmp_path, WRITE.format(bump=bump))
    assert proc.returncode != 0
    assert raw.execute("PRAGMA user_version").fetchone()[0] == VERSION
    assert raw.execute("SELECT 1 FROM journal WHERE event = 'after_update'").fetchone() is None
    assert "systemctl stop imagectl-server" in seen
    assert status["state"] == "failed" and "הוחזר לעותק שלפני העדכון" in status["error"], status
    assert (data / "imagectl.db.pre-upgrade").is_file()


def test_rollback_without_a_migration_keeps_what_was_written(tmp_path):
    proc, raw, status, seen, _ = _upgrade_with_live(tmp_path, WRITE.format(bump=""))
    assert proc.returncode != 0
    assert raw.execute("SELECT 1 FROM journal WHERE event = 'after_update'").fetchone()
    assert "systemctl stop imagectl-server" not in seen
    assert "הוחזר לעותק" not in status["error"]
