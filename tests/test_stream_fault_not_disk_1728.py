"""‏#1728 — זרם ריק או חלקי הוא כשל של הזרם, לא של הדיסק.

ברזל rc5, ‏18/10: משכפל שאותחל באמצע הזרם קיבל את הגל הרץ שוב, ניסה לכתוב
את מחיצה 1 מזרם שכבר היה במחיצה 3, ו-fanout דיווח `empty stream` על שתי
המגירות. ‏`_read_fanout_report` קרא את השורה כ"הכתיבה לדיסק נכשלה" — בלי
`stream` — ולכן `ata_capture` רץ, `ata_log` ריק נשלח, והשרת רשם
`disk_failure` עם `cause=unclassified` על **שני דיסקים תקינים**, שנצבעו כתום
בסבב הבא.

שני הצדדים:

- **הסוכן** (`verdict.sh`): פסק האורך של fanout (`empty`/`short`/`long
  stream`) הוא של הזרם — כל המגירות קיבלו אותם בייטים. ‏`fail_written_target`
  מקבל `stream`, ואין `ata_log`.
- **השרת** (`disk_failures.record`): סוכן ישן (rc5 על הברזל) עדיין שולח
  `ata_log` על שורה כזו. בלי ראיה מהקרנל (כבל/דיסק) — לא נרשם, ורשומה
  שכבר נרשמה כך אינה צובעת.

שומרי תיקון-יתר: ‏`write error` של fanout בלי ראיית קרנל עדיין נרשם (#440:
דיסק מת אינו "הרשת"), וראיית כבל ליד זרם ריק עדיין נרשמת כבל.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from server import db
from server.disks import disk_failures
from test_agent import BASH
from test_stream_honesty import drawer_run, errors_of

REPO = Path(__file__).resolve().parent.parent
MAC = "b4:2e:99:07:1a:c4"

#: הנוסח המדויק שהגיע מהברזל — ‏`_write_fail` של verdict.sh לפני התיקון.
METAL_ERROR = "partition 1: הכתיבה לדיסק נכשלה (fanout: empty stream; partclone rc=1)"

CABLE_LINES = [
    "[ 2950.112233] ata1.00: exception Emask 0x10 SAct 0x0 SErr 0x280100 action 0x6 frozen",
    "[ 2950.112240] ata1: SError: { UnrecovData 10B8B BadCRC }",
    "[ 2950.112250] ata1: limiting SATA link speed to 1.5 Gbps",
]


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "t.db")


def _target(error: str, ata_log: list[str]) -> dict:
    return {"dev": "sda", "bytes_written": 0, "bytes_total": 57982058496,
            "state": "failed", "error": error, "serial": "S-1728", "port": 1,
            "ata_port": 1, "ata_log": ata_log}


# --- השרת ---------------------------------------------------------------------


def test_an_empty_stream_with_no_kernel_evidence_is_not_a_disk_failure(conn):
    """המקרה מהברזל, כפי שסוכן rc5 שולח אותו."""
    assert disk_failures.record(conn, "ses_1728", MAC, _target(METAL_ERROR, [])) is None
    assert disk_failures.list_open(conn) == []
    assert disk_failures.open_for(conn, MAC, [{"serial": "S-1728"}]) == []
    events = [r["event"] for r in conn.execute("SELECT event FROM journal")]
    assert "disk_failure" not in events


@pytest.mark.parametrize("reason", ["short stream (got 10 of 20)", "long stream (got 30 of 20)"])
def test_a_short_or_long_stream_is_not_a_disk_failure_either(conn, reason):
    error = f"partition 2: הכתיבה לדיסק נכשלה (fanout: {reason}; partclone rc=1)"
    assert disk_failures.record(conn, "ses_1728", MAC, _target(error, [])) is None


def test_a_row_already_recorded_from_the_metal_does_not_colour_the_disk(conn):
    """הרשומות של 18/10 כבר ב-DB של המעבדה — נקראות שוב לפי הראיה שבהן."""
    conn.execute(
        "INSERT INTO disk_failures (session_id, mac, dev, serial, port, ata_port, at, cause,"
        " error, ata_log) VALUES ('ses_m2', ?, 'sda', 'S-1728', 1, 1,"
        " '2026-10-18T01:57:13+00:00', 'unclassified', ?, '[]')", (MAC, METAL_ERROR))
    conn.commit()
    assert disk_failures.open_for(conn, MAC, [{"serial": "S-1728"}]) == []
    assert disk_failures.list_open(conn) == []


def test_a_dead_disk_without_kernel_lines_is_still_recorded(conn):
    """שומר #440: ‏`write error` של fanout הוא המגירה שמתה — לא הזרם."""
    error = "partition 3: הכתיבה לדיסק נכשלה (fanout: write error; partclone rc=1)"
    row = disk_failures.record(conn, "ses_1728", MAC, _target(error, []))
    assert row is not None and row["cause"] == "unclassified"


def test_kernel_evidence_next_to_an_empty_stream_still_wins(conn):
    """שומר: אם הקרנל ראה שגיאת קו, היא ההסבר — כמו בכל רשומה אחרת."""
    row = disk_failures.record(conn, "ses_1728", MAC, _target(METAL_ERROR, CABLE_LINES))
    assert row is not None and row["cause"] == "cable"


def test_the_stream_phrases_are_what_fanout_and_the_agent_write():
    """הביטוי חייב להיות המילים שנכתבות בפועל, אחרת הסיווג "עובד" רק בטסט."""
    fanout = (REPO / "agent" / "fanout.c").read_text(encoding="utf-8")
    verdict = (REPO / "agent" / "lib" / "restore" / "verdict.sh").read_text(encoding="utf-8")
    assert "(fanout: " in verdict
    # getattr: בבקרה השלילית הטסט נופל על ה-assert, לא על סמל חסר.
    assert getattr(disk_failures, "STREAM_PHRASES", ()) == (
        "fanout: empty stream", "fanout: short stream", "fanout: long stream")
    # fanout.c: ‏"empty stream", ו-"%s stream (got …)" עם "short"/"long".
    assert '"empty stream"' in fanout
    assert '"%s stream (got %lld of %lld)"' in fanout
    assert '"short" : "long"' in fanout


# --- הסוכן --------------------------------------------------------------------

#: ‏fanout כמו הבינארי על stdin ריק: פותח וסוגר כל fifo (המגירה מקבלת EOF,
#: ‏partclone יוצא), כותב את ה-sha של אפס בייטים (`fanout_eof` — ‏reached_eof),
#: ומדווח `failed empty stream` על כל מגירה, ‏rc=1. ‏fanout שלא היה פותח את
#: ה-fifo היה מכשיל את המגירה על "פג הזמן" — ענף אחר, והטסט לא היה נוגע בבאג.
EMPTY_FANOUT = (
    "#!/bin/sh\n"
    "shift\n"
    'tmp="$(mktemp)"\n'
    'cat > "$tmp"\n'
    'fanout_eof "$tmp"\n'
    'for out; do : > "$out" & done\n'
    "wait\n"
    'for out; do echo "$out failed empty stream"; done\n'
    'rm -f "$tmp"\n'
    "exit 1\n"
)


@requires_native(("bash", BASH))
def test_an_empty_stream_fails_the_drawers_without_disk_evidence(tmp_path):
    run, out = drawer_run(
        tmp_path,
        # הכתיבה כבר החלה (טבלת המחיצות נכתבה) — בלי זה ata_capture לא רץ
        # ממילא (#1429), והטסט לא היה מבחין בין לפני לאחרי.
        ": > \"$RUN_DIR/targets/sda/write_started\"; : > \"$RUN_DIR/targets/sdb/write_started\"; "
        "restore_partition_drawers unicast http://s img 1 dd part.zst "
        f"{'0' * 64} '' sda sdb > /dev/null 2>&1; " 'echo "rc=$?"',
        {"fanout": EMPTY_FANOUT},
        source="stream_source() { :; }; ",
    )
    assert out.strip().endswith("rc=1"), out
    errors = errors_of(run)
    for dev in ("sda", "sdb"):
        target = run / "targets" / dev
        assert (target / "state").read_text(encoding="utf-8").strip() == "failed"
        assert "empty stream" in errors[dev], errors[dev]
        assert not (target / "ata_log.json").exists(), (
            f"{dev}: זרם ריק נאסף כראיית דיסק — השרת ירשום disk_failure על דיסק תקין"
            f" (error={errors[dev]!r})")
