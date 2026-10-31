"""‏#874 — אדום לפני הסבב בלי לכתוב על הדיסק: הזיכרון בשרת.

הכרעת נדב 16/09: הסימון על הדיסק (#845) נפסל. במקומו:

* **הסיווג** (`server/disks/ata_cause.py`) — הסוכן שולח את שורות ה-ATA של הקרנל
  כמות שהן, והשרת אומר כבל/חריץ, הדיסק, או "לא סווג" (עיקרון 5 — לא ניחוש).
  שני הדפוסים נבדקים בדיוק: השורות מ-`/root/dmesg-sda-20260916-070036.txt`
  (מחשב 2, דיסק 1) = `cable`; דוגמה ריאלית עם `media error` + `UNC` = `disk`.
* **הזיכרון** (`server/disks/disk_failures.py`, טבלת `disk_failures`) — נכתב
  מדיווח ההתקדמות של יעד שנכשל (ממשק 4), פעם אחת ליעד, לפי סידורי
  **וגם** לפי מכונה+חריץ (בראיה השנייה הפורט אשם והדיסקים חפים).
* **התשובה ל-hello** (ממשק 3, `disk_failures`) — רק רשומות פתוחות
  שתואמות לסידוריים שהמכונה שלחה או לחריץ באותה מכונה.
* **"נקה"** מהקונסולה — `cleared_at`; הזיכרון אינו גובר על המפעיל.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.disks import ata_cause, disk_failures
from server import db
from test_server_api import hello, open_session

pytest.importorskip("fastapi")

#: השורות כפי שצוטטו ב-#874 מהמדידה על דיסק 1 במחשב 2 (16/09, 00:16 ו-07:03):
#: שגיאות קידוד על הקו, הדיסק ענה DRDY. זה כבל/פורט, לא הדיסק.
CABLE_LINES = [
    "[  312.401172] ata1.00: exception Emask 0x50 SAct 0x1ff0 SErr 0x480900 action 0x6 frozen",
    "[  312.401180] ata1.00: irq_stat 0x08000000, interface fatal error",
    "[  312.401183] ata1: SError: { UnrecovData HostInt 10B8B Handshk }",
    "[  312.401188] ata1.00: failed command: WRITE FPDMA QUEUED",
    "[  312.401199] ata1.00: cmd 61/00:20:00:e8:5a/04:00:01:00:00/40 tag 4 ncq dma 524288 out",
    "[  312.401201]          res 40/00:00:00:00:00/00:00:00:00:00/00 Emask 0x50 (ATA bus error)",
    "[  312.401204] ata1.00: status: { DRDY }",
    "[  312.401231] ata1: hard resetting link",
    "[  318.101172] ata1: limiting SATA link speed to 1.5 Gbps",
    "[  323.401172] ata1.00: failed to IDENTIFY (I/O error, err_mask=0x100)",
]

#: דוגמה מומצאת-אך-ריאלית של דיסק שמת: סקטור לא-ניתן-לתיקון ושגיאת
#: מדיה, בלי אף סימן קו (SError ריק, אין ATA bus error).
DISK_LINES = [
    "[ 1200.120001] ata2.00: exception Emask 0x0 SAct 0x1 SErr 0x0 action 0x0",
    "[ 1200.120010] ata2.00: irq_stat 0x40000008",
    "[ 1200.120014] ata2.00: failed command: WRITE FPDMA QUEUED",
    "[ 1200.120020] ata2.00: cmd 61/08:00:00:10:00/00:00:00:00:00/40 tag 0 ncq dma 4096 out",
    "[ 1200.120022]          res 41/40:08:00:10:00/00:00:00:00:00/40 Emask 0x409 (media error) <F>",
    "[ 1200.120026] ata2.00: status: { DRDY ERR }",
    "[ 1200.120028] ata2.00: error: { UNC }",
    "[ 1200.130001] blk_update_request: I/O error, dev sdb, sector 4096 op 0x1:(WRITE)",
]


# --- הסיווג: פונקציה טהורה, שני הדפוסים בדיוק ----------------------------------

def test_the_lines_measured_on_cloner_2_disk_1_are_a_cable():
    assert ata_cause.classify(CABLE_LINES) == "cable"


def test_a_crc_counter_without_agent_reported_ata_link_errors_is_not_cable():
    assert ata_cause.classify(["UDMA_CRC_Error_Count 667 (unchanged)"]) == "unclassified"


def test_unc_and_media_error_without_a_line_error_is_the_disk():
    assert ata_cause.classify(DISK_LINES) == "disk"


def test_no_lines_is_unclassified_not_a_guess():
    assert ata_cause.classify([]) == "unclassified"
    assert ata_cause.classify(["[ 1.0] ata1: SATA link up 6.0 Gbps (SStatus 133 SControl 300)"]) == "unclassified"


def test_a_disk_mark_next_to_a_cable_mark_is_still_the_cable():
    """קו רעוע מייצר גם ABRT; הכבל הוא ההסבר הפשוט יותר, ורק בלעדיו
    ‏ABRT/UNC מצביעים על הדיסק."""
    both = DISK_LINES + ["[ 1201.0] ata2: SError: { ICRC }", "[ 1201.1] ata2.00: error: { ICRC ABRT }"]
    assert ata_cause.classify(both) == "cable"
    assert ata_cause.classify(["[ 1.0] ata2.00: error: { ABRT }"]) == "disk"


#: ‏dmesg אמיתי מכשל הכתיבה על פורט 1 במחשב 2 (16/09 07:03, #890) — כפי
#: שהסוכן מסנן אותו לפורט: ראש (exception, SError, 30× WRITE FPDMA QUEUED),
#: איפוס, רעש ACPI, ושוב. אין בו `ATA bus error` — שורת ה-`res` של הקרנל
#: נכתבת בלי קידומת ata1 — ולכן הסימן הוא `10B8B` / `Handshk` / `interface
#: fatal error`.
METAL_0703 = (Path(__file__).parent / "fixtures" / "ata1-cable-failure-0916.txt").read_text(
    encoding="utf-8").splitlines()


def metal_tail_as_stored_in_the_lab_db() -> list[str]:
    """הרשומה שנרשמה ב-DB של המעבדה ב-10:16 (#890): ‏`ata_capture` על main שמר
    את 40 השורות **האחרונות** — ‏`failed to set xfermode`, איפוס, 13× ACPI,
    ‏`Read log 0x00 page 0x00 failed` — ואף שורת SError. שתי השורות שאינן
    ב-fixture (xfermode, Read log) משוחזרות מהציטוט ב-Issue."""
    acpi = [l for l in METAL_0703 if "ACPI cmd" in l][:13]
    reset = next(l for l in METAL_0703 if "hard resetting link" in l)
    return (["[20472.500000] ata1.00: failed to set xfermode (err_mask=0x40)", reset] + acpi
            + ["[20473.100000] ata1.00: Read log 0x00 page 0x00 failed, Emask 0x40"])


def test_the_metal_failure_of_0703_with_its_head_is_a_cable():
    assert ata_cause.classify(ata_cause.clean_log(METAL_0703)) == "cable"


def test_read_log_failed_alone_after_a_reset_is_not_the_disk():
    """‏#890: ‏`Read log ... failed` מופיע אחרי **כל** איפוס קו — לבדו, בלי
    ‏UNC/ABRT/media error, הוא אינו ראיה על הדיסק אלא "לא סווג" (עיקרון 5).
    בקרה שלילית: על main הרשומה מהברזל מסווגת `disk` — ההיפך מהאמת."""
    tail = metal_tail_as_stored_in_the_lab_db()
    assert not any("SError" in l for l in tail)
    assert ata_cause.classify(tail) == "unclassified"
    assert ata_cause.classify(["[ 1.0] ata2.00: Read log 0x10 page 0x00 failed, Emask 0x1"]) == "unclassified"
    # עם סימן דיסק לצדו — הסימן מכריע, לא ה-Read log.
    assert ata_cause.classify(tail + ["[ 1.0] ata1.00: error: { UNC }"]) == "disk"


def test_the_log_is_bounded_and_only_strings_survive():
    lines = ata_cause.clean_log([1, None, "x" * 500] + ["ok"] * 60)
    assert len(lines) == ata_cause.MAX_LINES
    assert lines[0] == "x" * ata_cause.MAX_LINE_CHARS
    assert ata_cause.clean_log("not a list") == []


# --- הזיכרון: נכתב מדיווח ההתקדמות, פעם אחת ליעד -------------------------------

@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "t.db")


def failed_target(**over) -> dict:
    t = {"dev": "sda", "bytes_written": 15500000, "bytes_total": 57982058496,
         "state": "failed", "error": "partition 3: fsync errno=5",
         "serial": "S3TWNE0JB04745", "port": 1, "ata_port": 1, "ata_log": CABLE_LINES}
    t.update(over)
    return t


def test_record_keeps_serial_port_and_the_servers_classification(conn):
    row = disk_failures.record(conn, "ses1", "b4:2e:99:07:1a:c4", failed_target())
    assert row["cause"] == "cable" and row["serial"] == "S3TWNE0JB04745" and row["port"] == 1
    (open_row,) = disk_failures.list_open(conn)
    assert open_row["ata_log"] == CABLE_LINES
    assert open_row["disk_number"] == 1
    assert open_row["error"] == "partition 3: fsync errno=5"


def test_the_same_failure_reported_every_two_seconds_is_one_row(conn):
    assert disk_failures.record(conn, "ses1", "b4:2e:99:07:1a:c4", failed_target()) is not None
    assert disk_failures.record(conn, "ses1", "b4:2e:99:07:1a:c4", failed_target()) is None
    assert len(disk_failures.list_open(conn)) == 1


def test_an_old_agent_without_ata_log_is_not_remembered(conn):
    """סוכן ישן: אין `ata_log`, אין סידורי — אין למה להצמיד זיכרון."""
    t = failed_target(); del t["ata_log"]
    assert disk_failures.record(conn, "ses1", "b4:2e:99:07:1a:c4", t) is None
    assert disk_failures.list_open(conn) == []


def test_the_progress_report_of_a_failed_drawer_lands_in_the_memory(server):
    """המסלול המלא דרך ממשק 4: מכונה חברה בסבב מדווחת יעד שנכשל עם
    `ata_log` → רשומה פתוחה בקונסולה, ביומן `disk_failure`. בקרה שלילית: על
    main אין טבלה ואין נקודת קצה — `/api/console/disk-failures` הוא 404."""
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    report = {"session_id": ids["session"], "mac": ids["mac1"], "state": "writing",
              # ‏sdb עם ata_log אך לא failed: רק כשל נרשם.
              "targets": [failed_target(), failed_target(dev="sdb", state="writing", serial="S9")]}
    for _ in range(3):   # הדיווח חוזר; הזיכרון לא
        assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    rows = server["admin"].get("/api/console/disk-failures").json()   # #1073: קונסולה = admin
    assert len(rows) == 1
    assert rows[0]["mac"] == ids["mac1"] and rows[0]["cause"] == "cable"
    assert rows[0]["serial"] == "S3TWNE0JB04745" and rows[0]["port"] == 1
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert events.count("disk_failure") == 1


# --- hello: מה שהסוכן מקבל — לפי סידורי או לפי חריץ באותה מכונה -----------------

def test_hello_answers_the_failure_by_serial_on_any_machine(server):
    """הדיסק נדד למכונה אחרת: הרשומה מגיעה לפי הסידורי, ו-`port` הוא
    ‏null — פורט 1 של מכונה אחרת אינו פורט 1 כאן."""
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    disk_failures.record(server["ctx"].conn, ids["session"], ids["mac1"], failed_target())
    moved = hello(server, ids["mac2"])
    body_serial = "S1"   # hello_body מדווח serial S1 -- לא הסידורי שנכשל
    assert body_serial != "S3TWNE0JB04745"
    assert moved["disk_failures"] == []
    # עכשיו המכונה השנייה מדווחת את הדיסק שנכשל.
    from conftest import hello_body
    body = hello_body(ids["mac2"])
    body["disks"][0]["serial"] = "S3TWNE0JB04745"
    answer = server["anon"].post("/api/v1/agent/hello", json=body).json()
    (rec,) = answer["disk_failures"]
    assert rec["serial"] == "S3TWNE0JB04745" and rec["cause"] == "cable"
    assert rec["port"] is None
    assert rec["at"]


def test_hello_answers_the_failure_by_slot_on_the_same_machine(server):
    """הדיסק הוחלף אבל הכבל לא: אותה מכונה, סידורי אחר — הרשומה מגיעה
    לפי החריץ, עם `port` כדי שהסוכן יצבע את הדיסק שיושב בו עכשיו."""
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    disk_failures.record(server["ctx"].conn, ids["session"], ids["mac1"], failed_target())
    answer = hello(server, ids["mac1"])            # serial S1 -- דיסק אחר
    (rec,) = answer["disk_failures"]
    assert rec["port"] == 1 and rec["cause"] == "cable" and rec["serial"] == "S3TWNE0JB04745"


def test_a_cleared_failure_no_longer_paints_and_cannot_be_cleared_twice(server):
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    row = disk_failures.record(server["ctx"].conn, ids["session"], ids["mac1"], failed_target())
    assert server["deploy"].post(f"/api/console/disk-failures/{row['id']}/clear").status_code == 403
    assert server["admin"].post(f"/api/console/disk-failures/{row['id']}/clear").json() == {"ok": True}
    assert hello(server, ids["mac1"])["disk_failures"] == []
    assert server["admin"].get("/api/console/disk-failures").json() == []
    assert server["admin"].post(f"/api/console/disk-failures/{row['id']}/clear").status_code == 404
    assert server["admin"].post("/api/console/disk-failures/999/clear").status_code == 404
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert events.count("disk_failure_cleared") == 1


def test_a_failure_without_a_port_on_this_machine_needs_a_serial_to_match(conn):
    """רשומה בלי פורט ובלי סידורי אין למה להצמיד — היא נשמרת (ראיה) אך
    אינה עונה ל-hello של אף אחד."""
    disk_failures.record(conn, "ses1", "b4:2e:99:07:1a:c4", failed_target(serial=None, port=None))
    assert disk_failures.open_for(conn, "b4:2e:99:07:1a:c4", [{"serial": "S1"}]) == []
    assert len(disk_failures.list_open(conn)) == 1


# --- מגירה שנשלפה אינה אשמת הדיסק (נדב 17/10) ----------------------------------
# המקרה מהמעבדה: ‏`disk_failures` id=1241 (17/10 12:24 UTC), ‏`cause=unclassified`,
# המגירה "נשלפה" ב-`echo 1 > /sys/block/sdb/device/delete`. הכונן לא החזיר שגיאה
# משלו — הקרנל דחה כתיבה להתקן שכבר אינו שם — והדיסק נצבע כתום (failed_last).
# הכלל: ירוק בהפצה הבאה, כמו "זרם שנקטע". רק על ראיה חיובית (עיקרון 5).

PULLED_ERROR = ("מחיצה 3: הקרנל דחה כתיבה לכונן: device offline error, dev sdb, sector 5963736"
                " op 0x1:(WRITE) flags 0x100000 phys_seg 1 prio class 2")
PULLED_LINES = ["[  200.000001] sd 2:0:0:0: [sdb] tag#3 device offline error, dev sdb,"
                " sector 5963736 op 0x1:(WRITE) flags 0x100000 phys_seg 1 prio class 2"]

#: ‏hot-unplug אמיתי של SATA: ה-AHCI מדווח שנוכחות ההתקן השתנתה (`DevExch`).
HOTPLUG_LINES = [
    "[  900.100001] ata3: exception Emask 0x10 SAct 0x0 SErr 0x4010000 action 0xe frozen",
    "[  900.100005] ata3: irq_stat 0x00400040, connection status changed",
    "[  900.100008] ata3: SError: { PHYRdyChg DevExch }",
    "[  900.100011] ata3: hard resetting link",
    "[  905.200001] ata3: SATA link down (SStatus 0 SControl 300)",
]


@pytest.mark.parametrize("lines, error", [
    (PULLED_LINES, PULLED_ERROR),       # 1241: ה-ata_log מ-#1602 והשגיאה של הסוכן
    ([], PULLED_ERROR),                 # סוכן לפני #1602: הראיה רק ב-error
    ([], "מחיצה 2: הכונן נעלם מהמערכת (נשלף או נותק)"),   # ‏/sys/block/<d> נעלם
    (HOTPLUG_LINES, "מחיצה 1: הכתיבה נכשלה (partclone rc=1)"),
])
def test_a_pulled_drawer_is_classified_removed(conn, lines, error):
    """דרך `record` — המסלול של דיווח ההתקדמות, עם `error` של הסוכן."""
    row = disk_failures.record(conn, "ses1", "b4:2e:99:07:1a:c4", failed_target(
        dev="sdb", error=error, ata_log=lines))
    assert row["cause"] == "removed"


@pytest.mark.parametrize("lines, error, cause", [
    # שגיאת כתיבה אמיתית: ‏errno 5 עם ata exception ו-UnrecovData — כמו id=1 במעבדה.
    (CABLE_LINES, "partition 3: fsync errno=5", "cable"),
    (CABLE_LINES, PULLED_ERROR, "cable"),          # סימן קו גובר גם על offline
    (DISK_LINES + PULLED_LINES, PULLED_ERROR, "disk"),
    # הכונן עבר טיפול שגיאות של ATA ואחריו הקרנל הוציא אותו offline — זה דיסק
    # שגווע, לא מגירה ששלפו. בלי סימן קו/דיסק: לא סווג, לא ניחוש.
    (["[ 50.0] ata2.00: exception Emask 0x0 SAct 0x0 SErr 0x0 action 0x6 frozen"] + PULLED_LINES,
     PULLED_ERROR, "unclassified"),
    ([], "מחיצה 3: הכתיבה נכשלה (partclone rc=1)", "unclassified"),
    ([], "", "unclassified"),
])
def test_without_evidence_of_a_pull_the_cause_is_unchanged(conn, lines, error, cause):
    row = disk_failures.record(conn, "ses1", "b4:2e:99:07:1a:c4", failed_target(
        dev="sdb", error=error, ata_log=lines))
    assert row["cause"] == cause


def test_a_pulled_drawer_is_recorded_removed_and_does_not_colour_the_disk(conn):
    """הרשומה נשמרת (ראיה, ביומן), אבל hello אינו מחזיר אותה והקונסולה אינה
    מציגה אותה — הדיסק ירוק בהפצה הבאה. ‏**בקרה שלילית:** על הקוד הישן היא
    ‏`unclassified` וחוזרת בשניהם — כתום."""
    mac = "b4:2e:99:07:1a:c4"
    row = disk_failures.record(conn, "ses1", mac, failed_target(
        dev="sdb", serial="S-PULLED", port=2, error=PULLED_ERROR, ata_log=PULLED_LINES))
    assert row["cause"] == "removed"
    assert disk_failures.open_for(conn, mac, [{"serial": "S-PULLED"}]) == []
    assert disk_failures.list_open(conn) == []


def test_a_row_recorded_before_the_fix_as_unclassified_is_green_too(conn):
    """הרשומה 1241 כבר ב-DB כ-`unclassified`: היא נקראת שוב לפי הראיה שבה,
    ולא נשארת כתומה עד "נקה"."""
    mac = "78:ac:c0:9b:11:c2"
    conn.execute(
        "INSERT INTO disk_failures (session_id, mac, dev, serial, port, ata_port, at, cause,"
        " error, ata_log) VALUES ('ses_1241', ?, 'sdb', 'S-1241', 2, 3,"
        " '2026-10-17T12:24:00+00:00', 'unclassified', ?, '[]')", (mac, PULLED_ERROR))
    conn.commit()
    assert disk_failures.open_for(conn, mac, [{"serial": "S-1241"}]) == []
    assert disk_failures.list_open(conn) == []


def test_a_real_write_error_still_colours_the_disk_orange(conn):
    mac = "b4:2e:99:07:1a:c4"
    disk_failures.record(conn, "ses1", mac, failed_target())       # CABLE_LINES, errno=5
    (rec,) = disk_failures.open_for(conn, mac, [{"serial": "S3TWNE0JB04745"}])
    assert rec["cause"] == "cable"


def test_the_pull_phrases_are_what_the_agent_and_the_kernel_write():
    """הביטוי של הסוכן חייב להיות המילים שהסוכן כותב, אחרת הסיווג "עובד" בטסט
    ולא על ברזל."""
    agent = Path(__file__).resolve().parent.parent / "agent"
    source = "\n".join(p.read_text(encoding="utf-8") for p in agent.rglob("*.sh"))
    for phrase in ata_cause.REMOVED_AGENT_PHRASES:
        assert phrase in source, phrase
