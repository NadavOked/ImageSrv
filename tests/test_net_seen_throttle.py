"""חניקת הכתיבה של `net_seen` — ‏issue #136.

‏hello הוא גם ההצטרפות וגם הסקירה, וכל hello היה **טרנזאקציית כתיבה
מלאה**. סוכן שסוקר כל שתי שניות = 30 כתיבות בדקה לכל מכונה, 600 לכיתה
של 20 — ודווקא בדקה שבה השרת פותח סבב. זה בדיוק המזון של #54:
‏`database is locked` שנראה כמו עומס ואינו.

הבדיקה כאן היא על **כתיבות בפועל** ולא על "נראה תקין": ‏`writes_to`
סופר את משפטי הכתיבה ש-sqlite באמת ביצע על `net_devices`, ולכן הוא
ראיה חיובית שהכתיבה נחסכה — ולא היעדר שגיאה. שלושת הדברים שאסור
לשבור נבדקים באותה מידת רצינות:

* ‏`last_seen` נשאר הראיה שהמכונה חיה (‏#64, ‏#109). חלון החניקה חייב
  להישאר קצר מ-`room.AWAKE_SECONDS`, אחרת "חסכנו כתיבות" הפך מכונה
  דלוקה לכבויה על המסך — התשובה הגרועה ביותר לטכנאי.
* ‏`agent_loops` סופר **הגעות** hello ולא כתיבות. החניקה לא נוגעת בו,
  וזה נבדק מול השרת המלא ולא מונח.
* ‏`disks_json` שורד דילוג — מסך מחשב הבנייה קורא ממנו.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server.db import NET_SEEN_MIN_INTERVAL_SECONDS, connect, net_seen

MAC = "b4:2e:99:07:1a:c4"
IP = "10.44.12.187"


@contextlib.contextmanager
def writes_to(conn, table: str = "net_devices"):
    """סופר משפטי כתיבה לטבלה אחת, דרך ה-trace של sqlite.

    ‏`total_changes` סופר שורות מכל הטבלאות, וכאן צריך בדיוק את
    ‏`net_devices` — גם כשמונה אחר (‏`agent_loops`) כותב לצידו.
    """
    seen: list[str] = []

    def trace(statement: str) -> None:
        if statement.lstrip()[:6].upper() in ("INSERT", "UPDATE", "DELETE") \
                and table in statement:
            seen.append(statement)

    conn.set_trace_callback(trace)
    try:
        yield seen
    finally:
        conn.set_trace_callback(None)


def row_of(db, mac: str = MAC):
    return db.execute("SELECT * FROM net_devices WHERE mac = ?", (mac,)).fetchone()


def age_the_row(db, seconds: int, mac: str = MAC) -> str:
    """מזקין את השורה ביד. הסף הוא הפרש זמנים ולא מונה, ולכן הדרך
    לחצות אותו בלי לישון היא להזיז את החותמת, לא את השעון."""
    stamp = (datetime.now(timezone.utc)
             - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    db.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ?", (stamp, mac))
    db.commit()
    return stamp


@pytest.fixture()
def db(tmp_path: Path):
    return connect(tmp_path / "throttle.db")


# --- הבקרה השלילית: כתיבות בפועל --------------------------------------------


def test_a_dense_hello_stream_writes_once_and_not_thirty_times(db):
    """דקה שלמה של סקירה כל שתי שניות — כתיבה אחת, לא 30.

    זו הבקרה השלילית של #136: על הקוד שלפני התיקון המונה כאן הוא 30,
    כי כל `INSERT ... ON CONFLICT DO UPDATE` שינה שורה ובוצע commit.
    """
    net_seen(db, MAC, IP)                       # המגע הראשון — כתיבה אמיתית

    with writes_to(db.connection) as writes:
        for _ in range(30):                     # 60 שניות של sleep 2
            net_seen(db, MAC, IP)

    assert writes == [], (
        f"‏hello חוזר בתוך חלון החניקה עדיין כותב — {len(writes)} כתיבות"
    )
    # והשורה בכל זאת שם, עם מה שנכתב במגע הראשון.
    assert row_of(db)["ip"] == IP


def test_the_first_contact_is_always_a_write(db):
    """אין שורה — אין מה לחנוק. מכונה חדשה נרשמת מיד."""
    with writes_to(db.connection) as writes:
        net_seen(db, MAC, IP)
    assert len(writes) == 1
    assert row_of(db)["first_seen"]


def test_two_machines_do_not_throttle_each_other(db):
    """החניקה היא לכל MAC בנפרד. כיתה שלמה שנדלקת יחד נרשמת כולה."""
    with writes_to(db.connection) as writes:
        for n in range(20):
            net_seen(db, f"b4:2e:99:07:1a:{n:02x}", IP)
    assert len(writes) == 20


# --- מה שאסור לשבור 1: `last_seen` נשאר ראיה שהמכונה חיה ---------------------


def test_last_seen_moves_again_the_moment_the_window_passes(db):
    """דילוג הוא דחייה, לא ויתור: ה-hello הבא אחרי החלון נכתב."""
    net_seen(db, MAC, IP)
    aged = age_the_row(db, NET_SEEN_MIN_INTERVAL_SECONDS + 1)

    net_seen(db, MAC, IP)
    assert row_of(db)["last_seen"] > aged


def test_a_hello_right_on_the_edge_of_the_window_is_written(db):
    """הגבול עצמו שייך לכתיבה. ‏`<` ולא `<=`: סוכן שסוקר בדיוק בקצב
    התקרה (15 שניות) חייב לכתוב בכל סקירה, אחרת הפער נפתח לכפול."""
    net_seen(db, MAC, IP)
    aged = age_the_row(db, NET_SEEN_MIN_INTERVAL_SECONDS)

    net_seen(db, MAC, IP)
    assert row_of(db)["last_seen"] > aged


def test_the_window_stays_well_inside_the_awake_threshold():
    """השומר על המספר עצמו. הסף חייב להישאר קטן מ-`AWAKE_SECONDS`,
    אחרת מכונה חיה מצטיירת ככבויה — ‏#64 ו-#109 שוב, דרך התיקון."""
    from server.rounds.room import AWAKE_SECONDS                   # noqa: PLC0415

    assert NET_SEEN_MIN_INTERVAL_SECONDS * 2 <= AWAKE_SECONDS


def test_a_timestamp_that_cannot_be_read_is_written_over(db):
    """עיקרון 5. חותמת פגומה, חסרה, או בלי אזור זמן אינה "טרייה" —
    היא "לא ידוע", ולא ידוע מסתיים בכתיבה."""
    net_seen(db, MAC, IP)
    for broken in ("", "not-a-timestamp", "2026-08-30T14:00:00"):
        db.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ?",
                   (broken, MAC))
        db.commit()
        with writes_to(db.connection) as writes:
            net_seen(db, MAC, IP)
        assert len(writes) == 1, f"‏{broken!r} נחשב טרי"


def test_a_clock_that_jumped_backwards_is_written_over(db):
    """חותמת מהעתיד אינה ראיה לטריות — היא ראיה לשעון שקפץ."""
    net_seen(db, MAC, IP)
    age_the_row(db, -3600)
    with writes_to(db.connection) as writes:
        net_seen(db, MAC, IP)
    assert len(writes) == 1


# --- מה שאסור לשבור 3: שינוי אמיתי לא נבלע -----------------------------------


def test_a_new_address_is_written_at_once(db):
    """המכונה קיבלה כתובת אחרת — זה מידע חדש, לא ביקור חוזר."""
    net_seen(db, MAC, IP)
    with writes_to(db.connection) as writes:
        net_seen(db, MAC, "10.44.12.200")
    assert len(writes) == 1
    assert row_of(db)["ip"] == "10.44.12.200"


def test_new_disks_are_written_at_once(db):
    """מסך מחשב הבנייה מציג "מה מותקן עכשיו". כונן שהוחלף חייב להופיע
    בסקירה הבאה, לא בעוד רבע דקה."""
    first = json.dumps([{"dev": "sda", "size_bytes": 256060514304}])
    second = json.dumps([{"dev": "sdb", "size_bytes": 500107862016}])
    net_seen(db, MAC, IP, disks_json=first)
    with writes_to(db.connection) as writes:
        net_seen(db, MAC, IP, disks_json=second)
    assert len(writes) == 1
    assert row_of(db)["disks_json"] == second


def test_a_skipped_write_keeps_the_disks_it_already_had(db):
    """‏hello של תפריט האתחול מגיע בלי דיסקים. הדילוג חייב לשמור על
    הערך הקיים — בדיוק כמו ה-COALESCE שהיה שם קודם."""
    disks = json.dumps([{"dev": "sda", "size_bytes": 256060514304}])
    net_seen(db, MAC, IP, disks_json=disks)
    net_seen(db, MAC, IP)                       # נחנק
    assert row_of(db)["disks_json"] == disks

    age_the_row(db, NET_SEEN_MIN_INTERVAL_SECONDS + 1)
    net_seen(db, MAC, IP)                       # נכתב
    assert row_of(db)["disks_json"] == disks


def test_the_free_text_description_survives_the_throttle(db):
    """התיאור שהמפעיל הקליד נשמר בין עדכונים — גם כשאין עדכון."""
    net_seen(db, MAC, IP)
    db.execute("UPDATE net_devices SET description = ? WHERE mac = ?",
               ("מחשב הבנייה", MAC))
    db.commit()
    net_seen(db, MAC, IP)
    age_the_row(db, NET_SEEN_MIN_INTERVAL_SECONDS + 1)
    net_seen(db, MAC, IP)
    assert row_of(db)["description"] == "מחשב הבנייה"


# --- מה שאסור לשבור 2: גלאי הלולאות סופר הגעות, לא כתיבות --------------------


def test_the_loop_counter_climbs_while_the_writes_stop(db):
    """שני המונים על חיבור אחד, זה מול זה: ‏`agent_loops` עולה בכל
    הגעה, ו-`net_devices` לא נכתבת אף פעם."""
    from server.agent import agent_loops                          # noqa: PLC0415

    net_seen(db, MAC, IP)
    beats = 12
    with writes_to(db.connection) as writes:
        for n in range(beats):
            net_seen(db, MAC, IP)
            stamp = (datetime.now(timezone.utc)
                     + timedelta(seconds=n)).isoformat(timespec="seconds")
            agent_loops._count(db, MAC, stamp)

    hits = db.execute(
        "SELECT hits FROM agent_loops WHERE mac = ?", (MAC,)
    ).fetchone()["hits"]
    assert hits == beats, "הגעות hello לא נספרו במלואן"
    assert writes == [], f"‏net_devices נכתבה {len(writes)} פעמים מיותרות"


def test_the_loop_counter_counts_hellos_and_not_writes(server):
    """אותו משפט מול השרת המלא, דרך ה-endpoint האמיתי.

    ‏`agent_loops` הוא הראיה ל"דיסק שלא עולה" (#112). הוא סופר את
    **הגעת** ה-hello, והחניקה של `net_seen` אינה אמורה לגעת בו — לא
    בהנחה, אלא בעשר בקשות אמיתיות.
    """
    from conftest import hello_body, setup_classroom        # noqa: PLC0415

    ids = setup_classroom(server)
    mac = ids["mac1"]

    beats = 10
    for _ in range(beats):
        assert server["anon"].post(
            "/api/v1/agent/hello", json=hello_body(mac)
        ).status_code == 200

    row = server["ctx"].conn.execute(
        "SELECT hits FROM agent_loops WHERE mac = ?", (mac,)
    ).fetchone()
    assert row is not None and row["hits"] == beats, \
        "החניקה של net_seen שינתה את מונה הלולאות"
