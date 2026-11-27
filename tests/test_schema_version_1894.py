"""#1894 — גרסת סכמה ב-DB, סירוב לעלות על DB חדש מהקוד, והחזרת ה-DB ב-rollback.

`PRAGMA user_version` נכתב בכל `_initialize`. קוד שפוגש DB מגרסה גבוהה משלו
(חזרה לגרסה קודמת אחרי שהחדשה כבר מיגרה) מסרב בשם — **לפני** שהוא מריץ עליו
את המיגרציות שלו. `server-upgrade.sh` מעתיק את ה-DB לפני ה-checkout, ומחזיר
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
#: `db.SCHEMA_VERSION` **ועדכן כאן** — אחרת קוד ישן לא יזהה DB שמיגרת.
#: getattr: בבקרה השלילית (קוד בלי הקבוע) הטסטים נכשלים על ההתנהגות, לא על ייבוא.
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
                7: "47896c6739283e20dfc43b9458dc2d6f039de4717af91caa89b20004e3387a1c",
                # 8: email_queue (#1751, PR #1757, 31/10)
                8: "d43d9f74c5c05f8448f2a0c9333c82875fc77fd608874320707e34a9f2a9db00",
                # 9: login_account_failures + login_known_ips (#2443, 05/11)
                9: "aaa90d0571c5fd7abde1e92ce3e24db9c60b7e96b1002e120f3f25dd1852b8d7",
                # 10: login_account_failures.surface (#2443 Fable, 05/11); login_known_ips
                # removed before any release (Nadav 05/11: no exemption, a 15-minute lock)
                10: "cda49192f1a686cf062355ae0c78a27066a3c623d03842873c1845f808bab24f",
                # 11: journal.machine/round_id/class_id (#1296, 06/11)
                11: "8b749052277b032873a241b39a3c21096f268af57f184fbb2d4f5d2c28ac644a",
                # 12: learning_runs/waves/changes/machines (#2028, 07/11)
                12: "0eb4413b6d7acca2507afe380f3fb5a0dc6c1616280836364b247f4e7f44a409",
                # 13: usb_records + usb_syncs (#1920, 08/11)
                13: "df5e4febd90716a0a52dc1f5343c4e74a21650381803105cb20fd35301fcf653",
                # 14: disk_f1_marks (#1858, 08/11; 13 in its PR -- #1920 took 13 first in round/1107c)
                14: "9030cc575a61b535ed7b5fc2e5f25efa6e88dd730191998856556fc7f3e9dadd",
                # 15: net_devices.monitor_on (#2844; bumped in release/v0.91.11, Refs #2850)
                15: "e85bb3510829eaed4560191fd9521eb991f48c78fda25e105309101906780264",
                # 16: room_rounds.hostnames_json (#2786, 09/11; 15 in its PR -- net_devices.monitor_on took 15 first)
                16: "4a1651485542b31598a47bb8a738aa505d8b1a19e0b11785c22620cf7995a1bb",
                # 17: boot_marks (#2830, 12/11)
                17: "7a065e2cea6d15bb789f8021c871cbbc3511a985acfafeb9db3c1e9573158448",
                # 18: users.view (#3025, 12/11; 17 in its PR -- boot_marks took 17 first in release/v0.94.0)
                18: "e34ce6efdab706618544e310c0723f57dd2e8a2003018d61ed59ec9ce96316d5",
                # The lab branches (lab/vm-1113*, lab/vm-1114a/b) numbered their own 18-20 beside main:
                # 18 = 16 + #2528 stage 1 (write_ledger, round_interrupts, net_devices.boot_id)
                #      283e45f59afd3a680b27644a66ac4e084dfa568888c0ab7aaf83ca4ee9717564 -- not main's 18
                # 19 = + chunk_ledger (#2533)  6c5df60dd78453849bca3e676ac11828c973a06338d18039600e7d382fa01730
                # 20 = + brain_decisions/brain_memory (#3185)  4ef71c360217f8360a47d2f551ecb728d1a0189190fd3bfd5492c1c1e2a1b689
                # 21: lab/vm-1114c -- main's 18 and the lab's 20 together, one number above both (14/11)
                21: "4a6a46314e66fb132ff0fbd8fd02ad797968b706fb2f39db46ca0a03f1f3b4df",
                # 22: prodline_rounds + prodline_disks (#3181, 14/11)
                22: "8f14fb53090c91726d70a2d622818222306e70adbee45538e593d90a5e326a03",
                # 23: wave_senses (#3041/#3125, S46-S50, 15/11)
                23: "8989657669a1df210816eea605df5241b5cf77b3558740066c2f892075ce02f8",
                # 24: chunk_pass_rates + chunk_pass_counters, brain_decisions.predicted/actual/unit/matched, port_history.failed
                #     (#3125, 15/11; on top of #3324's 23)
                24: "233c4a712ab97c6fe7f5e81d33902cd9bdc5c42b0bd151dae2bfe85ecfa0b0c1",
                # 25: session_members.beeps_json (#3125, S62, 15/11); on top of 23 (#3324) and 24 (#3347). Recomputed in the merged tree (argus 15/11)
                25: "f19602c51ccd8c665425eb8a2533611f5459dfe46503f00978b7c3ce5dc56322",
                # 26: brain_asks (#3654, 18/11): the brain's one channel to the user
                26: "e8a745590108e25d0e8bb161877ae551e689fe9abfc393320576989697af521b",
                # 27: brain_asks.rows_json (#3673, 20/11): the detail table of a question (`.ba-rows`). Recomputed in the merged tree on top of main's 26
                27: "3b1534f26ebf2387117f4939ee46b8894ed1875efe3510b0578b409f82c84e1e"}


def _fingerprint(conn) -> str:
    rows = sorted(r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def test_a_fresh_database_carries_the_schema_version(tmp_path):
    conn = connect(tmp_path / "imagesrv.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION


def test_a_database_from_before_versions_is_stamped(tmp_path):
    path = tmp_path / "imagesrv.db"
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
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE model_slot_maps")
    if found == 3:
        raw.execute("ALTER TABLE tasks DROP COLUMN disk_serial")
    raw.execute(f"PRAGMA user_version = {found}")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'model_slot_maps'").fetchone()
    assert "disk_serial" in [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]


@pytest.mark.parametrize("found", [12, 13])
def test_both_round_1107c_migrations_run_from_12_and_from_13(tmp_path, found):
    """round/1107c: #1920 (usb_records/usb_syncs) is 13 and #1858 (disk_f1_marks) is 14 --
    both PRs said 13. A server on 12 (before both) or on 13 (#1920 only) reaches 14 with
    all three tables in place, stamped 14."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE disk_f1_marks")
    if found == 12:
        raw.execute("DROP TABLE usb_syncs")
        raw.execute("DROP TABLE usb_records")
    raw.execute(f"PRAGMA user_version = {found}")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"usb_records", "usb_syncs", "disk_f1_marks"} <= names


def test_a_database_on_5_gains_the_held_columns_and_is_stamped_6(tmp_path):
    """#2031 (PR #2036) added net_devices.held_at/held_json without a bump. A server
    already on 5 runs ADDED_COLUMNS, gains both, and is stamped 6."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE net_devices DROP COLUMN held_json")
    raw.execute("ALTER TABLE net_devices DROP COLUMN held_at")
    raw.execute("PRAGMA user_version = 5")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    columns = [r[1] for r in conn.execute("PRAGMA table_info(net_devices)")]
    assert "held_at" in columns and "held_json" in columns


def test_a_database_on_14_gains_monitor_on_and_is_stamped_15(tmp_path):
    """#2844 (PR #2850) added net_devices.monitor_on without a bump; release/v0.91.11
    bumped it to 15. A server on 14 (the work server, TA) gains the column and is
    stamped 15."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE net_devices DROP COLUMN monitor_on")
    raw.execute("PRAGMA user_version = 14")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    columns = [r[1] for r in conn.execute("PRAGMA table_info(net_devices)")]
    assert "monitor_on" in columns


def test_a_database_on_16_gains_users_view_and_keeps_every_user_on_the_default(tmp_path):
    """#3025: users.view is a new column. A server on 16 gains it and is stamped 17,
    and no existing user gets a written value: NULL = the default by is_builtin."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE users DROP COLUMN view")
    raw.execute("INSERT INTO users (username, pw_hash, role, created_at)"
                " VALUES ('old', 'x', 'admin', '2026-01-01')")
    raw.execute("PRAGMA user_version = 16")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    assert conn.execute("SELECT view FROM users WHERE username = 'old'").fetchone()[0] is None


def test_a_database_on_7_gains_the_email_queue_and_is_stamped_8(tmp_path):
    """#1751: the email-alert queue is a new table. A server already on 7 gains it
    (with its one-pending-per-combination index) and is stamped 8."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE email_queue")
    raw.execute("PRAGMA user_version = 7")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"email_queue", "email_queue_one_pending"} <= names


def test_a_database_newer_than_the_code_is_refused_and_left_untouched(tmp_path):
    path = tmp_path / "imagesrv.db"
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
    conn = connect(tmp_path / "imagesrv.db")
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
    # "השרת החדש" — כותב ל-DB (ואולי מיגר) ואינו עונה ב-/health/live.
    (repo / "deploy" / "console-live.sh").write_text(
        "#!/usr/bin/env bash\n" + live_script + "\nexit 1\n", encoding="utf-8", newline="\n")
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    raw = sqlite3.connect(data / "imagesrv.db")
    return proc, raw, _status(data), calls(tmp_path), data


WRITE = ('python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); '
         "c.execute(\\\"INSERT INTO journal (ts,user,event,detail) VALUES ('t','','after_update','')\\\")"
         '{bump}; c.commit()" "$IMAGESRV_DATA_DIR/imagesrv.db"')


def test_rollback_after_a_migration_restores_the_database_and_says_so(tmp_path):
    bump = f"; c.execute('PRAGMA user_version = {VERSION + 1}')"
    proc, raw, status, seen, data = _upgrade_with_live(tmp_path, WRITE.format(bump=bump))
    assert proc.returncode != 0
    assert raw.execute("PRAGMA user_version").fetchone()[0] == VERSION
    assert raw.execute("SELECT 1 FROM journal WHERE event = 'after_update'").fetchone() is None
    assert "systemctl stop imagesrv-server" in seen
    assert status["state"] == "failed" and "הוחזר לעותק שלפני העדכון" in status["error"], status
    assert (data / "imagesrv.db.pre-upgrade").is_file()


def test_rollback_without_a_migration_keeps_what_was_written(tmp_path):
    proc, raw, status, seen, _ = _upgrade_with_live(tmp_path, WRITE.format(bump=""))
    assert proc.returncode != 0
    assert raw.execute("SELECT 1 FROM journal WHERE event = 'after_update'").fetchone()
    assert "systemctl stop imagesrv-server" not in seen
    assert "הוחזר לעותק" not in status["error"]


def test_a_database_on_8_gains_the_account_lockout_table_and_is_stamped_9(tmp_path):
    """#2443: the per-account failure cap adds a table. A server already on 8
    gains it (with the cap's index) and is stamped 9."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE login_account_failures")
    raw.execute("PRAGMA user_version = 8")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"login_account_failures", "idx_login_account_failures"} <= names


def test_a_database_on_9_gains_the_cap_surface_and_old_rows_are_the_console(tmp_path):
    """#2443 (Fable): the per-account cap is counted per surface. A server on 9 has
    the table without `surface`; it gains the column, and the rows it already had
    (all written by the console-or-agent shared cap) count as the console."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE login_account_failures")
    raw.execute("CREATE TABLE login_account_failures (username TEXT NOT NULL, at REAL NOT NULL)")
    raw.execute("INSERT INTO login_account_failures (username, at) VALUES ('noc', 1.0)")
    raw.execute("PRAGMA user_version = 9")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    assert conn.execute("SELECT surface FROM login_account_failures").fetchone()[0] == "console"


def test_a_database_on_16_gains_the_write_ledger_and_is_stamped_21(tmp_path):
    """#2528 (stage 1): a server on 16 gains write_ledger, round_interrupts and
    net_devices.boot_id, and is stamped with the current version (21 since lab/vm-1114c)."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE IF EXISTS write_ledger")
    raw.execute("DROP TABLE IF EXISTS round_interrupts")
    if "boot_id" in [r[1] for r in raw.execute("PRAGMA table_info(net_devices)")]:
        raw.execute("ALTER TABLE net_devices DROP COLUMN boot_id")
    raw.execute("PRAGMA user_version = 16")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"write_ledger", "round_interrupts"} <= tables
    assert "boot_id" in [r[1] for r in conn.execute("PRAGMA table_info(net_devices)")]


def test_a_database_on_16_gains_boot_marks_and_is_stamped_17(tmp_path):
    """#2830: the `up=` boot marks are a new table. A server already on 16 gains it
    (with its index), empty, and is stamped 17."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE boot_marks")
    raw.execute("PRAGMA user_version = 16")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"boot_marks", "boot_marks_mac"} <= names
    assert conn.execute("SELECT COUNT(*) FROM boot_marks").fetchone()[0] == 0


#: what main had at 18 and the lab did not, and what the lab had at 20 and main did not
MAIN_ONLY_TABLES = {"boot_marks"}
LAB_ONLY_TABLES = {"write_ledger", "round_interrupts", "chunk_ledger", "brain_decisions", "brain_memory"}


def _drop(path, tables, columns, version):
    raw = sqlite3.connect(path)
    for t in tables:
        raw.execute(f"DROP TABLE {t}")
    for t, c in columns:
        raw.execute(f"ALTER TABLE {t} DROP COLUMN {c}")
    raw.execute(f"PRAGMA user_version = {version}")
    raw.commit()
    raw.close()


def _shape(conn) -> dict:
    """Every table with its columns, and every index -- not the SQL text: a column that
    ADD COLUMN brought back is written at the end of its CREATE, not where SCHEMA has it."""
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    shape = {t: sorted(r[1] for r in conn.execute(f"PRAGMA table_info({t})")) for t in names}
    shape["(indexes)"] = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"))
    return shape


@pytest.mark.parametrize("found, tables, columns", [
    (18, LAB_ONLY_TABLES, [("net_devices", "boot_id")]),           # main v0.96 (17 boot_marks, 18 users.view)
    (20, MAIN_ONLY_TABLES, [("users", "view")]),                   # the lab's 18-20 (#2528, #2533, #3185)
], ids=["main-18", "lab-20"])
def test_main_18_and_lab_20_both_reach_21_with_every_table(tmp_path, found, tables, columns):
    """lab/vm-1114c (14/11): main and the lab each used 17-20 for different tables. 21 is
    above both; a server on either side gains what the other side added -- every table,
    column and index of a fresh 21 -- and a second start changes nothing (idempotent)."""
    fresh = _shape(connect(tmp_path / "fresh.db"))
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    _drop(path, tables, columns, found)
    before = _shape(sqlite3.connect(path))
    assert before != fresh                                  # the drop really took something away
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    assert _shape(conn) == fresh
    conn.close()
    again = connect(path)
    assert again.execute("PRAGMA user_version").fetchone()[0] == VERSION
    assert _shape(again) == fresh


def test_a_database_on_21_gains_the_production_line_tables_and_is_stamped_22(tmp_path):
    """#3181: prodline_rounds/prodline_disks are new tables. A server on 21 gains both and is stamped 22."""
    path = tmp_path / "imagesrv.db"
    connect(path).close()
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE prodline_disks")
    raw.execute("DROP TABLE prodline_rounds")
    raw.execute("PRAGMA user_version = 21")
    raw.commit()
    raw.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == VERSION == 27
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"prodline_rounds", "prodline_disks"} <= names
