"""#3844 (F3, VM 19/11, צעד 3b) -- ניתוק רשת של 4 דקות במשכפל נגמר ב-`disk_failure cause=unclassified`.

הסוכן ויתר אחרי תקרת ההמתנה של המקלט (#3658, נדב 18/11: "אחרי 2 דקות לא חוזר, להוריד אותו") וכתב
"מחיצה 1: השרת לא ענה במשך 124 שניות", בלי שורות ATA (בניתוק רשת אין כאלה). השרת סיווג `unclassified`.
ההכרעה על הזמן נשארת של נדב ושל המוח; כאן רק הסיבה: **רשת**, לא "לא סווג", ולא אשמת הדיסק.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server import db
from server.brain import cause as k9
from server.disks import ata_cause, disk_failures

ROOT = Path(__file__).resolve().parent.parent
#: מה שהסוכן כתב בריצה 19/11 (`step3b`, journal.txt)
AGENT_SAID = "מחיצה 1: השרת לא ענה במשך 124 שניות"
MAC = "78:ac:c0:9b:11:c2"


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "net3844.db")


def failed_target(**over) -> dict:
    t = {"dev": "sda", "state": "failed", "error": AGENT_SAID, "serial": "naa.5000c29aaa441c55",
         "port": 3, "ata_port": None, "ata_log": []}
    t.update(over)
    return t


def test_a_silent_server_with_no_kernel_lines_is_network_not_unclassified():
    assert ata_cause.classify([], AGENT_SAID) == "network"


@pytest.mark.parametrize("seconds", [121, 124, 600])
def test_no_threshold_lives_in_the_server_whatever_the_seconds_are(seconds):
    assert ata_cause.classify([], f"מחיצה 1: השרת לא ענה במשך {seconds} שניות") == "network"


def test_kernel_evidence_still_wins_over_the_agents_words():
    """לא מקפלים: אם הקרנל ראה שגיאת קו, הכבל הוא ההסבר ולא הרשת."""
    cable = ["[ 1.0] ata1: SError: { 10B8B Handshk }"]
    assert ata_cause.classify(cable, AGENT_SAID) == "cable"


def test_other_failures_stay_unclassified():
    assert ata_cause.classify([], "מחיצה 1: משהו אחר נכשל") == "unclassified"


def test_the_server_reads_the_words_the_agent_writes():
    """הביטוי שהשרת מחפש הוא הביטוי ש-chunkrecv.sh כותב; שינוי ניסוח בסוכן מפיל את הטסט, לא את הסיווג בשקט."""
    sh = (ROOT / "agent/lib/restore/chunkrecv.sh").read_text(encoding="utf-8")
    written = re.search(r'_cp_why="([^"$]*)\$', sh)
    assert written, "chunkrecv.sh no longer sets _cp_why to 'text $seconds'"
    assert all(p in written.group(1) for p in ata_cause.NETWORK_PHRASES)


def test_the_failure_is_recorded_as_network_and_does_not_paint_the_disk(conn):
    row = disk_failures.record(conn, "ses1", MAC, failed_target())
    assert row is not None and row["cause"] == "network"
    journal = [r[0] for r in conn.execute("SELECT detail FROM journal WHERE event = 'disk_failure'")]
    assert any("cause=network" in m and "unclassified" not in m for m in journal), journal
    # the disk is not at fault: next wave it is not orange, and the console list does not show it
    assert disk_failures.open_for(conn, MAC, [{"serial": "naa.5000c29aaa441c55"}]) == []
    assert disk_failures.list_open(conn) == []


def test_k9_says_network_and_not_against_the_disk():
    v = k9.classify(k9.Evidence(kernel=(), error=AGENT_SAID))
    assert v.cause == k9.NETWORK and not v.against_disk
    assert k9.classify(k9.Evidence(error=AGENT_SAID)).cause == k9.NETWORK   # kernel lines unread: the agent's words are still evidence
    assert k9.classify(k9.Evidence(kernel=(), error="מחיצה 1: משהו אחר")).cause == k9.UNCLASSIFIED


def test_k9_network_with_unread_kernel_says_so_in_the_evidence():
    """עיקרון 5: שורות קרנל שלא נקראו אינן "בדקנו ואין" -- ה-evidence נושא `kernel=unread`, וה-reason אומר זאת."""
    unread = k9.classify(k9.Evidence(error=AGENT_SAID))
    assert unread.cause == k9.NETWORK and "kernel=unread" in unread.evidence
    assert "לא נקראו" in unread.reason
    read = k9.classify(k9.Evidence(kernel=(), error=AGENT_SAID))
    assert "kernel=unread" not in read.evidence and "kernel=network (0 lines)" in read.evidence
    assert "לא נקראו" not in read.reason
