"""#3475 (K3, קבוצה ד'): שלושת שערי השרת לפני פתיחת סבב, בצל -- RAID (B49), מקום פנוי (B60), בריאות המערכת (B51).

כל שער נבדק על **קלט אמיתי של המקור שלו**: טקסט `/proc/mdstat` ו-`/etc/imagesrv/raid` שנקראים מקבצים (אותם נתיבים ש-`raid_status`
קורא, מוזזים לתיקיית הטסט), שורות `storage_usage` שנכתבות דרך `storage_history.sample`, כשל `SQLITE_FULL` שנרשם דרך `db.note_disk_full`
ומתנקה דרך `db.journal`, ושורות בריאות שנבנו מהפונקציות של השרת (`server_disks.smart_verdict` על פלט smartctl, `health.collect` עם hooks
מוזרקים). לכל פסק (OPEN/WARN/BLOCK/UNDECIDED) יש טסט שמגיע אליו, ו"לא נבדק" נפרד מ"נבדק ותקין".

**הכרעות שנקבעו כאן** (בלי לשאול; הנימוק בדוח ה-PR): `bad` בבריאות המערכת = WARN ולא BLOCK; מערך RAID שלא נקרא = UNDECIDED ולא BLOCK;
קצב מילוי שעוד לא נמדד = UNDECIDED ולא OPEN.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import shutil
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import gate_core, gate_host, gates, memory, veto
from server.brain.gate_core import BLOCK, OPEN, UNDECIDED, WARN
from server.storage import history as storage_history, locations as storage_locations
from server.system import health, raid_status, server_disks
from server.system.health_rows import check
from test_server_health import health_server  # noqa: F401 -- הפיקסצ׳ר

pytestmark = pytest.mark.system_reserve        # הרזרבה של `disk_reserve` נשארת 10GiB (ב-conftest היא 0): B60 קורא אותה
#: ה-fixture האוטומטי של conftest (`_no_slow_health_gate`) מחליף את `gate_core.health_rows` ב-`None`; כאן בודקים את המקור האמיתי
REAL_HEALTH_GATE = True
RID = "room_test3475"
GB = 10 ** 9
Usage = namedtuple("Usage", "total used free")


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    db._last_full.clear()                      # מצב של התהליך כולו: סימן SQLITE_FULL של טסט אחר
    c = db.connect(tmp_path / "gates.db")
    yield c
    db._last_full.clear()
    veto.forget()


def only(read):
    return read(None, (), RID)


def one(readings):
    [reading] = readings
    assert reading.mac is None
    return reading


# --- B49: RAID ----------------------------------------------------------------------------------------------

# טקסט mdstat אמיתי (הדוגמאות של `test_raid_health_1989.py`, מהמעבדה)
HEALTHY = """Personalities : [raid1] [raid6] [raid5] [raid4]
md0 : active raid1 sdb2[1] sda2[0]
      976630464 blocks super 1.2 [2/2] [UU]
      bitmap: 1/8 pages [4KB], 65536KB chunk

unused devices: <none>
"""
DEGRADED = """Personalities : [raid1]
md0 : active raid1 sda2[0] sdb2[1](F)
      976630464 blocks super 1.2 [2/1] [U_]

unused devices: <none>
"""
REBUILDING = """Personalities : [raid6] [raid5] [raid4]
md0 : active raid5 sdd2[3] sdc2[1] sda2[0]
      1953260544 blocks super 1.2 level 5, 512k chunk, algorithm 2 [3/2] [UU_]
      [=>...................]  recovery =  8.5% (83034112/976630272) finish=79.3min speed=187776K/sec

unused devices: <none>
"""
INACTIVE = """Personalities : [raid10]
md0 : inactive sdb2[1](S) sda2[0](S)
      1953260544 blocks super 1.2

"""
NO_COUNT = """Personalities : [raid1]
md0 : active raid1 sdb2[1] sda2[0]
      976630464 blocks super 1.2

unused devices: <none>
"""
NO_ARRAYS = "Personalities : \nunused devices: <none>\n"
DECLARED = "level=raid1\ndisks=/dev/sda,/dev/sdb\nspares=0\narray=/dev/md0\n"
MOUNTED = "/dev/md0 / ext4 rw 0 0\n/dev/sda1 /boot/efi vfat rw 0 0\n"
NOT_MOUNTED = "/dev/md0 / ext4 rw 0 0\n"
ABSENT, UNREADABLE = "absent", "unreadable"


def raid_files(monkeypatch, tmp_path, *, mdstat=ABSENT, declared=ABSENT, mounts=MOUNTED):
    """שלושת הקבצים שבריאות ה-RAID קוראת, מוזזים לתיקיית הטסט. ``UNREADABLE`` = תיקייה: קריאה שנכשלת ב-OSError, לא קובץ חסר."""
    for attr, name, text in (("MDSTAT_PATH", "mdstat", mdstat), ("RAID_FACT_PATH", "raid", declared), ("MOUNTS_PATH", "mounts", mounts)):
        path = tmp_path / name
        if text == UNREADABLE:
            path.mkdir()
        elif text != ABSENT:
            path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(raid_status, attr, path)


def test_a_healthy_mirror_with_its_esp_is_open_and_names_the_rows(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=HEALTHY, declared=DECLARED)
    got = one(only(gate_host.raid))
    assert got.verdict == OPEN
    assert "מערך RAID md0: RAID1 · 2/2 כוננים" in got.reason and "מחיצת ESP (/boot/efi): מעוגנת" in got.reason
    assert {"raid_md0=ok", "raid_esp=ok"} <= set(got.evidence)


@pytest.mark.parametrize(("mdstat", "words"), [
    pytest.param(DEGRADED, ["נפל: sdb2", "להחליף"], id="failed-member"),
    pytest.param(REBUILDING, ["בבנייה מחדש 8.5%"], id="rebuilding"),
])
def test_an_active_array_that_is_hurt_or_rebuilding_is_warn_not_block(conn, monkeypatch, tmp_path, mdstat, words):
    # מערך פגוע אבל פעיל: הנתונים נגישים, והסבב עדיין סביר (הכרעת #3475)
    raid_files(monkeypatch, tmp_path, mdstat=mdstat, declared=DECLARED.replace("raid1", "raid5") if "raid5" in mdstat else DECLARED)
    got = one(only(gate_host.raid))
    assert got.verdict == WARN, got
    for word in words:
        assert word in got.reason, got.reason


def test_an_inactive_array_blocks_because_the_data_is_unreachable(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=INACTIVE, declared=DECLARED.replace("raid1", "raid10"))
    got = one(only(gate_host.raid))
    assert got.verdict == BLOCK
    assert "המערך אינו פעיל" in got.reason and "raid_md0=bad" in got.evidence


def test_a_declared_array_that_is_not_in_mdstat_blocks(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=NO_ARRAYS, declared=DECLARED)
    got = one(only(gate_host.raid))
    assert got.verdict == BLOCK and "אבל המערך אינו מופיע ב-/proc/mdstat" in got.reason, got


def test_a_declared_array_with_no_mdstat_file_at_all_blocks_too(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=ABSENT, declared=DECLARED)
    got = one(only(gate_host.raid))
    assert got.verdict == BLOCK and "mdstat=absent" in got.evidence, got


def test_the_worst_row_decides_and_the_reason_names_only_it(conn, monkeypatch, tmp_path):
    two = DEGRADED + INACTIVE.replace("md0", "md1")
    raid_files(monkeypatch, tmp_path, mdstat=two, declared=DECLARED)
    got = one(only(gate_host.raid))
    assert got.verdict == BLOCK and "md1" in got.reason and "נפל: sdb2" not in got.reason
    assert {"raid_md0=bad", "raid_md1=bad"} <= set(got.evidence)


def test_an_esp_that_is_not_mounted_is_warn(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=HEALTHY, declared=DECLARED, mounts=NOT_MOUNTED)
    got = one(only(gate_host.raid))
    assert got.verdict == WARN and "לא מעוגנת" in got.reason, got


def test_an_mdstat_that_could_not_be_read_is_undecided_not_block(conn, monkeypatch, tmp_path):
    # "לא נקרא" אינו "המערך נעלם": ההצהרה אומרת RAID, ו-/proc/mdstat לא נקרא -- השורה בבריאות אדומה, והשער אינו מכריז חסימה
    raid_files(monkeypatch, tmp_path, mdstat=UNREADABLE, declared=DECLARED)
    got = one(only(gate_host.raid))
    assert got.verdict == UNDECIDED and "/proc/mdstat לא נקרא" in got.reason and "mdstat=error" in got.evidence, got


def test_a_declaration_that_could_not_be_read_is_undecided(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=HEALTHY, declared=UNREADABLE)
    got = one(only(gate_host.raid))
    assert got.verdict == UNDECIDED and "לא ניתן לקרוא" in got.reason, got


def test_an_array_whose_drive_state_was_not_read_is_undecided(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=NO_COUNT, declared=DECLARED)
    got = one(only(gate_host.raid))
    assert got.verdict == UNDECIDED and "לא נקרא מצב הכוננים" in got.reason, got


@pytest.mark.parametrize("declared", [ABSENT, "level=none\n"], ids=["no-declaration-file", "declared-none"])
def test_a_server_without_raid_is_open_only_on_positive_evidence(conn, monkeypatch, tmp_path, declared):
    raid_files(monkeypatch, tmp_path, mdstat=NO_ARRAYS, declared=declared)
    got = one(only(gate_host.raid))
    assert got.verdict == OPEN and "arrays=0" in got.evidence
    assert f"declared={'absent' if declared == ABSENT else 'text'}" in got.evidence and "mdstat=text" in got.evidence


def test_a_server_with_no_md_at_all_and_no_declaration_is_open(conn, monkeypatch, tmp_path):
    # ווינדוס, VM בלי md: שני הקבצים לא קיימים, בלי שגיאה
    raid_files(monkeypatch, tmp_path, mdstat=ABSENT, declared=ABSENT)
    got = one(only(gate_host.raid))
    assert got.verdict == OPEN and {"mdstat=absent", "declared=absent"} <= set(got.evidence), got


def test_a_declaration_file_with_no_level_is_not_evidence_of_no_raid(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=NO_ARRAYS, declared="# empty\n")
    got = one(only(gate_host.raid))
    assert got.verdict == UNDECIDED and "אין בו level" in got.reason, got


def test_an_array_on_a_server_with_no_declaration_is_still_read(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=DEGRADED, declared=ABSENT)
    assert one(only(gate_host.raid)).verdict == WARN


# --- B60: מקום פנוי -----------------------------------------------------------------------------------------


def samples(conn, loc, points):
    """(ימים אחורה, פנוי בבייטים), נכתבות דרך `storage_history.sample` -- אותה כתיבה כמו הסיבוב היומי ופתיחת סבב."""
    now = datetime.now(timezone.utc)
    for days, free in sorted(points, reverse=True):
        stamp = (now - timedelta(days=days)).isoformat(timespec="seconds")
        assert storage_history.sample(conn, loc, "/x", "daily", usage=lambda p: Usage(1000 * GB, 0, free))
        conn.execute("UPDATE storage_usage SET at = ? WHERE rowid = (SELECT MAX(rowid) FROM storage_usage)", (stamp,))
    conn.commit()


def db_free(monkeypatch, free):
    """הפנוי במחיצת ה-DB, כמו ש-`shutil.disk_usage` מחזיר אותו (`health._db_free`)."""
    def usage(path):
        if isinstance(free, Exception):
            raise free
        return Usage(1000 * GB, 0, free)
    monkeypatch.setattr(shutil, "disk_usage", usage)


@pytest.fixture()
def library(conn, tmp_path):
    storage_locations.ensure_local(conn, tmp_path / "images")
    return conn


def test_a_steady_library_and_a_roomy_db_partition_are_open(library, monkeypatch):
    db_free(monkeypatch, 100 * GB)
    samples(library, "loc_local", [(10, 300 * GB), (0, 299 * GB)])
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == OPEN, got
    assert "מקום פנוי ל-DB: פנויים 100.0 GB" in got.reason and "מקום פנוי לאורך זמן (loc_local)" in got.reason
    assert f"loc_local=ok free={299 * GB}" in got.evidence


def test_a_write_that_already_failed_on_a_full_disk_blocks(library, monkeypatch):
    db_free(monkeypatch, 100 * GB)
    samples(library, "loc_local", [(10, 300 * GB), (0, 299 * GB)])
    db.note_disk_full("journal room_open")
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == BLOCK
    assert "כתיבה ל-DB נכשלה כי המחיצה מלאה (journal room_open" in got.reason


def test_a_successful_write_afterwards_clears_the_block(library, monkeypatch):
    # הראיה החיובית של #1892: שורת יומן שנכתבה אחרי הכשל מנקה אותו -- וזה מה ש-`room_open` עושה לפני השערים
    db_free(monkeypatch, 100 * GB)
    samples(library, "loc_local", [(10, 300 * GB), (0, 299 * GB)])
    db.note_disk_full("journal room_open")
    assert one(gate_host.library_space(library, (), RID)).verdict == BLOCK
    assert db.journal(library, "room_open", "x", round_id=RID) is not None
    assert one(gate_host.library_space(library, (), RID)).verdict == OPEN


@pytest.mark.parametrize(("free", "words"), [
    pytest.param(500 * 10 ** 6, ["נותרו 0.5 GB", "ה-DB עומד להתמלא"], id="under-1GiB"),
    pytest.param(5 * GB, ["נותרו 5.0 GB", "פחות מרזרבת המערכת (10.7 GB)"], id="under-the-reserve"),
])
def test_a_db_partition_that_is_nearly_full_is_warn_before_any_write_failed(library, monkeypatch, free, words):
    db_free(monkeypatch, free)
    samples(library, "loc_local", [(10, 300 * GB), (0, 299 * GB)])
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == WARN, got
    for word in words:
        assert word in got.reason, got.reason


def test_a_library_that_fills_within_a_month_is_warn_with_the_health_line(library, monkeypatch):
    db_free(monkeypatch, 100 * GB)
    samples(library, "loc_local", [(10, 300 * GB), (0, 100 * GB)])      # 20GB ליום: 5 ימים
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == WARN
    assert "מקום פנוי לאורך זמן (loc_local): " in got.reason and "מתמלא בעוד כ-5 ימים" in got.reason
    assert any(e.startswith("loc_local=filling days_left=5") for e in got.evidence), got.evidence


def test_a_location_under_the_system_reserve_is_warn_even_with_one_sample(library, monkeypatch):
    db_free(monkeypatch, 100 * GB)
    samples(library, "loc_local", [(0, 2 * GB)])
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == WARN and "נותרו 2.0 GB, פחות מרזרבת המערכת (10.7 GB)" in got.reason, got


def test_a_fill_rate_that_was_not_measured_yet_is_undecided_not_open(library, monkeypatch):
    # דגימה אחת, פחות מיום: "אינו מתמלא" עוד לא נמדד. הפנוי תקין, אבל השורה אומרת "לא נבדק" ולכן גם השער
    db_free(monkeypatch, 100 * GB)
    samples(library, "loc_local", [(0.2, 300 * GB)])
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == UNDECIDED and "לא נבדק — צריך דגימות ביותר מיום אחד" in got.reason, got
    assert any(e.startswith("loc_local=no_rate") for e in got.evidence)


def test_a_connected_location_that_was_never_sampled_is_undecided(library, monkeypatch):
    db_free(monkeypatch, 100 * GB)
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == UNDECIDED and "לא נדגם" in got.reason, got


def test_a_db_partition_that_could_not_be_measured_is_undecided_not_open(library, monkeypatch):
    db_free(monkeypatch, OSError("stale handle"))
    samples(library, "loc_local", [(10, 300 * GB), (0, 299 * GB)])
    got = one(gate_host.library_space(library, (), RID))
    assert got.verdict == UNDECIDED and "לא ניתן למדוד את המחיצה — לא נבדק" in got.reason and "free=unread" in got.evidence[0], got


def test_no_connected_location_is_undecided_and_a_disconnected_one_is_not_measured(conn, monkeypatch, tmp_path):
    db_free(monkeypatch, 100 * GB)
    assert "אין מיקום אחסון מחובר" in one(gate_host.library_space(conn, (), RID)).reason
    storage_locations.ensure_local(conn, tmp_path / "images")
    samples(conn, "loc_local", [(10, 300 * GB), (0, 100 * GB)])
    conn.execute("UPDATE storage_locations SET state = 'unreachable'")
    conn.commit()
    got = one(gate_host.library_space(conn, (), RID))
    assert got.verdict == UNDECIDED and "5 ימים" not in got.reason and "locations=0" in got.evidence, got


# --- B51: בריאות המערכת -------------------------------------------------------------------------------------

FAILED_SMART = {"model_name": "SAMSUNG MZ7TY256HDHP-000L7", "smart_status": {"passed": False},
                "ata_smart_attributes": {"table": [{"id": 173, "name": "Wear_Leveling_Count", "when_failed": "now"}]}}
PASSED_SMART = {"model_name": "Samsung SSD 860 EVO 500GB", "smart_status": {"passed": True},
                "ata_smart_attributes": {"table": [{"id": 199, "name": "UDMA_CRC_Error_Count", "when_failed": ""}]}}


def smart_row(data, rc=0, disk="sda"):
    """שורת SMART של דיסק שרת, מ-`smart_verdict` על פלט smartctl אמיתי (כמו `disk_rows`)."""
    import json
    state, detail = server_disks.smart_verdict(rc, json.dumps(data), "", disk)
    return check(f"server_disk:{disk}", f"דיסק השרת {disk} (מערכת)", state, detail)


GOOD = [check("dnsmasq", "שירות dnsmasq", "ok", "רץ"), check("time_sync", "שעון", "info", "ידני"),
        check("identity", "זהות מכונה", "not_applicable", "עוד לא הוגדר")]


def wire(conn, rows):
    gate_core.attach_health(conn, lambda: list(rows))


def test_all_rows_ok_is_open_and_neutral_rows_are_neither_a_problem_nor_evidence(conn):
    wire(conn, [*GOOD, smart_row(PASSED_SMART, 0)])
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == OPEN and got.reason == "נבדקו 2 שורות בריאות, כולן תקינות"
    assert {"ok=2", "neutral=2"} <= set(got.evidence) and any(e.startswith("health_ms=") for e in got.evidence)


def test_a_failing_smart_row_of_a_server_disk_is_warn_and_names_the_disk(conn):
    wire(conn, [*GOOD, smart_row(FAILED_SMART, 8)])
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == WARN, got
    assert "דיסק השרת sda (מערכת): SMART: FAILED · נכשל עכשיו: 173 Wear_Leveling_Count" in got.reason
    assert "server_disk:sda=bad" in got.evidence and "שירות dnsmasq" not in got.reason


def test_the_same_disk_passing_does_not_warn(conn):
    wire(conn, [*GOOD, smart_row(PASSED_SMART, 0)])
    assert one(gate_host.system_health(conn, (), RID)).verdict == OPEN


def test_any_other_bad_row_is_at_least_warn_and_names_label_and_detail(conn):
    wire(conn, [*GOOD, check("dnsmasq", "שירות dnsmasq", "bad", "dnsmasq down: unknown interface eth9")])
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == WARN and "שירות dnsmasq: dnsmasq down: unknown interface eth9" in got.reason, got


def test_a_health_row_alone_never_blocks(conn):
    # `BLOCK` שמור למה שהוכח (B49, B60): שורה אדומה היא "נראה רע"
    wire(conn, [check(i, i, "bad", "x") for i in ("a", "b", "c")])
    assert one(gate_host.system_health(conn, (), RID)).verdict == WARN


def test_warn_beats_unknown_and_the_reason_names_only_the_deciding_rows(conn):
    wire(conn, [*GOOD, check("firewall", "חומת אש", "unknown", "לא הצלחנו לבדוק"), check("backup", "גיבוי", "warn", "עוד לא רץ גיבוי")])
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == WARN and got.reason == "גיבוי: עוד לא רץ גיבוי"
    assert {"firewall=unknown", "backup=warn"} <= set(got.evidence)


@pytest.mark.parametrize("state", ["unknown", "off", "surprise"])
def test_an_unknown_or_off_or_foreign_state_is_undecided_not_open(conn, state):
    wire(conn, [*GOOD, check("firewall", "חומת אש", state, "לא הצלחנו לבדוק")])
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == UNDECIDED and "חומת אש: לא הצלחנו לבדוק" in got.reason, got


def test_a_smart_that_could_not_be_read_is_undecided_for_that_disk(conn):
    wire(conn, [*GOOD, smart_row(PASSED_SMART, 2)])      # כלי בדיקת SMART לא הצליח לפתוח את הדיסק
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == UNDECIDED and "כלי בדיקת SMART לא הצליח לפתוח" in got.reason, got


def test_a_health_source_that_was_not_wired_is_undecided(conn):
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == UNDECIDED and "health=unwired" in got.evidence, got


def test_a_collect_that_raised_is_undecided_by_name_not_open(conn):
    def collect():
        raise ZeroDivisionError("boom")
    gate_core.attach_health(conn, collect)
    got = one(gate_host.system_health(conn, (), RID))
    assert got.verdict == UNDECIDED and "ZeroDivisionError" in got.reason, got


@pytest.mark.parametrize("rows", [[], [check("identity", "זהות מכונה", "not_applicable", "x"), check("time_sync", "שעון", "info", "y")]],
                         ids=["no-rows", "only-neutral-rows"])
def test_no_row_that_says_ok_is_not_evidence(conn, rows):
    wire(conn, rows)
    assert one(gate_host.system_health(conn, (), RID)).verdict == UNDECIDED


def test_the_real_collect_with_a_failing_server_disk_is_warn(health_server_ctx):
    """אותו `collect` של `GET /health`, עם hooks מוזרקים (כמו `test_server_disks_smart_3007`) ושורת SMART שנכשלה."""
    ctx, hooks = health_server_ctx
    row = smart_row(FAILED_SMART, 8)
    gate_core.attach_health(ctx.conn, lambda: health.collect(ctx, {**hooks, "server_disks": lambda: [row]}, "http://x"))
    got = one(gate_host.system_health(ctx.conn, (), RID))
    assert got.verdict == WARN and "SMART: FAILED" in got.reason and "server_disk:sda=bad" in got.evidence, got


def test_the_real_collect_with_a_dead_dnsmasq_is_warn_by_name(health_server_ctx):
    ctx, hooks = health_server_ctx
    down = {**hooks, "unit_active": lambda name: "failed", "dnsmasq_journal": lambda: "dnsmasq: unknown interface eth9\n"}
    gate_core.attach_health(ctx.conn, lambda: health.collect(ctx, down, "http://x"))
    got = one(gate_host.system_health(ctx.conn, (), RID))
    assert got.verdict == WARN and "כרטיס הרשת eth9 אינו מוכר" in got.reason, got


@pytest.fixture()
def health_server_ctx(health_server):
    from test_server_health import SS_DNSMASQ                                   # noqa: PLC0415

    hooks = {**health.default_hooks(), "ss": lambda: SS_DNSMASQ, "unit_active": lambda n: "active",
             "http_get": lambda u: 200, "http_size": lambda u: (200, 31_000_000),
             "interfaces": lambda: [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
             "tftp_root": lambda: health_server["tftp"], "udp_sender_pids": lambda: []}
    return health_server["admin"].app.state.ctx, hooks





def test_the_gates_are_registered_with_these_functions_and_record_in_shadow(conn, monkeypatch, tmp_path):
    raid_files(monkeypatch, tmp_path, mdstat=INACTIVE, declared=DECLARED.replace("raid1", "raid10"))
    wire(conn, [*GOOD, smart_row(FAILED_SMART, 8)])
    db_free(monkeypatch, 100 * GB)
    by_id = {g.id: g for g in gates.GATES}
    assert [by_id[i].read for i in ("B49", "B60", "B51")] == [gate_host.raid, gate_host.library_space, gate_host.system_health]
    monkeypatch.setattr(gates, "GATES", tuple(by_id[i] for i in ("B49", "B51")))
    got = gates.run(conn, RID, [])
    assert [(g.id, r.verdict) for g, r in got] == [("B49", BLOCK), ("B51", WARN)]
    rows = {d["rule"]: d for d in memory.decisions(conn, round_id=RID, limit=10)}
    assert (rows["B49"]["verdict"], rows["B49"]["mode"], rows["B49"]["entity_kind"]) == ("BLOCK", "shadow", "round")
    assert "SMART: FAILED" in rows["B51"]["reason"]
