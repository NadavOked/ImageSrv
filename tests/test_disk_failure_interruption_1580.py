"""#1580 — עצירה באמצע אינה צובעת את הדיסק (`server/disks/disk_failures.py`).

הטסטים האלה ישבו ב-test_smart_ask.py, שהוסר ב-#2044 עם שאלת ה-SMART של
המשכפל. הם אינם קשורים לשאלה — הם על זיכרון הכשלים — ולכן עברו לכאן כמות שהם.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

CLONER = "aa:bb:cc:00:00:31"


@pytest.fixture()
def server(tmp_path: Path, images_root):
    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=lambda _packet: None)
    ctx = app.state.ctx
    yield {"ctx": ctx}
    ctx.sender.stop()


def _failure(ctx, mac, serial, cause, error, session="ses_x"):
    ctx.conn.execute(
        "INSERT INTO disk_failures (session_id, mac, dev, serial, port, ata_port, at, cause, error,"
        " ata_log) VALUES (?, ?, 'sda', ?, 1, 1, '2026-10-17T08:16:10+00:00', ?, ?, '[]')",
        (session, mac, serial, cause, error))
    ctx.conn.commit()


@pytest.mark.parametrize("error", [
    "מחיצה 1: פג הזמן -- הזרם לא הגיע",                 # שליפת המגירה 17/10: sda התקין
    "מחיצה 2: הזרם נקטע לפני סופו (מקור rc=1)",
    "מחיצה 1: השרת סגר את הסבב -- הכתיבה הופסקה",
    "המחשב כובה מהקונסולה באמצע שיכפול מקומי — הדיסק שבור ואינו שמיש",
    "השיכפול נעצר לפני מחיצה 3 — היעד אינו שמיש",
])
def test_an_interruption_does_not_colour_the_disk(server, error):
    """#1580 (נדב 17/10): כשל שהוא עצירה באמצע (הסבב נעצר/נסגר, המכונה כובתה,
    הזרם לא הגיע) אינו שגיאת דיסק — hello אינו מחזיר אותו (הסוכן לא צובע),
    והקונסולה אינה מציגה אותו. **בקרה שלילית:** על הקוד הישן הוא חוזר בשניהם."""
    from server.disks import disk_failures
    ctx = server["ctx"]
    _failure(ctx, CLONER, "S-OK", "unclassified", error)
    assert disk_failures.open_for(ctx.conn, CLONER, [{"serial": "S-OK"}]) == []
    assert disk_failures.list_open(ctx.conn) == []


@pytest.mark.parametrize("cause, error", [
    ("cable", "מחיצה 1: פג הזמן -- הזרם לא הגיע"),   # הקרנל ראה שגיאת קו — היא ההסבר
    ("disk", "מחיצה 1: השרת סגר את הסבב -- הכתיבה הופסקה"),
    ("unclassified", "מחיצה 3: הכתיבה נכשלה (partclone rc=1)"),   # לא עצירה
    ("unclassified", None),                                       # אין error — אין ראיה
])
def test_a_real_failure_still_colours_the_disk(server, cause, error):
    """הצד השני: שגיאת קו/דיסק מה-ATA, או כשל כתיבה שאינו עצירה — נשארים."""
    from server.disks import disk_failures
    ctx = server["ctx"]
    _failure(ctx, CLONER, "S-BAD", cause, error)
    assert [f["serial"] for f in disk_failures.open_for(ctx.conn, CLONER, [{"serial": "S-BAD"}])] == ["S-BAD"]
    assert [f["serial"] for f in disk_failures.list_open(ctx.conn)] == ["S-BAD"]


def test_the_interruption_phrases_are_what_the_agent_writes():
    """הביטויים בשרת חייבים להיות המילים שהסוכן כותב — אחרת הסיווג "עובד"
    בטסט ולא על ברזל."""
    from server.disks import disk_failures
    agent = Path(__file__).resolve().parent.parent / "agent"
    source = "\n".join(p.read_text(encoding="utf-8") for p in agent.rglob("*.sh")) + \
        (agent / "imagesrv-agent").read_text(encoding="utf-8")
    for phrase in disk_failures.INTERRUPTION_PHRASES:
        assert phrase in source, phrase
