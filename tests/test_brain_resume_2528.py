"""#2528 שלב 4 (K11), צד השרת: המוח מחליט לכל דיסק של משכפל שחזר -- להמשיך או להתחיל מחדש -- ורושם ב-``brain_decisions``.

הכרעת נדב 14/11: "צריך שהמוח יבין כמה חסר, ולפי זה יחליט אם להתחיל מחדש או לא." שני הקריטריונים: כמה חסר (ה-ledger
של השרת), וקצב הקריאה החוזרת מול קצב הכתיבה. כאן:

1. הכלל (`decide`) על המספרים שנמדדו על ברזל ב-14/11, ועל כל קלט שלא נמדד -- "להתחיל מחדש", בשמו.
2. ה-ledger נשמר ברגע "כתוב מחדש" (`snapshot`), ולכן הוא עדיין שם אחרי שהדיווח הראשון של הגל החדש מוחק אותו.
3. צל (ברירת המחדל) = נרשם ולא נאכף; enforce = נאכף; off = לא נרשם. שורה אחת לגל ולדיסק.
4. המעבר הראשון של המחיצה משדר רק את מה שחסר כשכל מי שהתייצב נושא ledger מאומת (`chunksend._first_pass`).

**בקרה שלילית:** החזרת `chunksend.py`, `interrupt.py`, `api.py` ו-`journal_he.py` לבסיס ומחיקת `server/brain/resume.py`
מפילה את כל הטסטים כאן: `resume` אינו קיים, והמעבר הראשון משדר הכול (``lists[0] is None``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server import db
from server.rounds import chunkledger as L

from test_chunk_ledger_2533 import MANIFEST, SIZE, rec
from test_chunksend_2533 import rig, wait_for          # noqa: F401 -- rig is a fixture

from server.brain import memory, resume   # resume's absence is a failure, not a skip (the negative control)

MAC = "aa:bb:cc:00:00:21"
CHUNK = 4 << 20


@pytest.fixture()
def conn(tmp_path):
    resume._candidates.clear()
    resume._decided.clear()
    return db.connect(tmp_path / "imagesrv.db")


def _interrupted(conn, *, written3=50, took_s=1050):
    """ses_1 כתב את img_aaaaaa על S1, והמשכפל כבה: ה-ledger עומד על `written3` חתיכות של מחיצה 3 ועל שתי חתיכות
    של מחיצה 1, `took_s` שניות אחרי שהגל התחיל."""
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'room', 'cloner')")   # sessions.group_id is a foreign key
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at, started_at) VALUES ('ses_1', 'g', 'img_aaaaaa', 'ROOM', 1, 1,"
                 " 'running', 'x', 'now', 0, '2026-11-14T10:00:00+00:00')")
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)",
                 (MAC, f'[{{"dev": "sda", "serial": "S1", "size_bytes": {SIZE}}}]'))
    conn.commit()
    rec(conn, [{"part": 3, "ranges": [[0, written3 - 1]]}, {"part": 1, "ranges": [[0, 1]]}])
    conn.execute("UPDATE chunk_ledger SET updated_at = ?",
                 (f"2026-11-14T10:{took_s // 60:02d}:{took_s % 60:02d}+00:00",))
    conn.commit()


def _ask(conn, readback_s=300, serial="S1", session="ses_2", image="img_aaaaaa"):
    """What the agent sends is the raw measurement (#3670): 1 MB/s over (readback_s - 1) MB reads back in `readback_s`
    seconds, the brain's own formula (`resume.readback_seconds`). None = the disk's read was not measured (null)."""
    raw = {"read_mbps": None, "read_bytes": 76562087936} if readback_s is None         else {"read_mbps": 1, "read_bytes": (readback_s - 1) * 1_000_000} if readback_s else {"read_mbps": 0, "read_bytes": 1}
    return resume.answer(conn, session_id=session, image_id=image, manifest=MANIFEST,
                         targets=[{"dev": "sda", "serial": serial, "size_bytes": SIZE, **raw}])


def _rows(conn):
    """ההחלטות של K11, הישנה ראשונה -- דרך נקודת הקריאה היחידה של הזיכרון (`memory.decisions`), כמו הקונסולה."""
    return list(reversed(memory.decisions(conn, rule="K11")))


# --- 1. הכלל ---------------------------------------------------------------------------------------------------


def test_the_metal_numbers_of_14_11_say_resume():
    """משכפל 2, 14/11: כבה ב-9,172 מתוך 18,254 חתיכות; כתיבה מלאה 35 דקות; קריאה עם XXH64 ב-210MiB/s."""
    pace = 18254 * CHUNK / (35 * 60)
    readback = 18254 * CHUNK / (210 * 1048576)
    d = resume.decide(total_chunks=18254, written_chunks=9172, readback_s=readback, pace_bps=pace)
    assert d.verdict == resume.RESUME, d
    assert d.reason.startswith("להמשיך") and "חסרות 9082 מתוך 18254" in d.reason
    assert "written=9172/18254" in d.evidence


@pytest.mark.parametrize("kw, why", [
    (dict(written_chunks=None), "אין לדיסק הזה ledger"),
    (dict(written_chunks=0), "ה-ledger ריק"),
    (dict(readback_s=None), "זמן הקריאה החוזרת לא נמדד"),
    (dict(readback_s=0), "זמן הקריאה החוזרת לא נמדד"),
    (dict(pace_bps=None), "קצב הכתיבה של הדיסק בגל שנקטע לא נמדד"),
])
def test_what_was_not_measured_is_restart_by_name_never_resume(kw, why):
    args = {**dict(total_chunks=100, written_chunks=90, readback_s=10.0, pace_bps=1e6), **kw}   # kw replaces one of them
    d = resume.decide(**args)
    assert d.verdict == resume.RESTART and why in d.reason, d


def test_a_resume_that_saves_less_than_the_margin_is_a_restart():
    """נדב 12/11: "אם הקריאה וההשלמה לוקחות בדיוק כמו כתיבה מחדש, לא הרווחנו כלום"."""
    pace = 100 * CHUNK / 1000                       # a whole write: 1000 s
    assert resume.decide(total_chunks=100, written_chunks=50, readback_s=300, pace_bps=pace).verdict == resume.RESUME
    d = resume.decide(total_chunks=100, written_chunks=50, readback_s=450, pace_bps=pace)   # 950 of 1000
    assert d.verdict == resume.RESTART and "החיסכון קטן מ-10%" in d.reason


# --- 2-3. ה-ledger שנשמר, וההחלטה הרשומה ---------------------------------------------------------------------------


def test_the_ledger_kept_at_rewrite_outlives_the_new_waves_first_report_and_shadow_only_records(conn):
    _interrupted(conn)
    assert resume.snapshot(conn, {"id": "room_1", "image_id": "img_aaaaaa"}, [MAC]) == 1
    L.forget_others(conn, [("S1", SIZE)], "ses_2")                    # the new wave's first progress report
    assert L.of_disk(conn, "S1", SIZE) == {}

    got = _ask(conn)
    assert got == [{"serial": "S1", "verdict": "resume", "enforced": False, "reason": got[0]["reason"],
                    "ledger": {"3": 50, "1": 2}, "readback": {"min_mbps": 10, "slack_s": 60}}], got
    assert "חסרות 50 מתוך 102" in got[0]["reason"]
    rows = _rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert (row["mode"], row["verdict"], row["entity_kind"], row["entity_key"], row["round_id"]) == \
        ("shadow", "RESUME", "disk", "S1", "ses_2")
    assert row["outcome"] == resume.SHADOW_HE
    assert _ask(conn) == got and len(_rows(conn)) == 1, "the same wave and disk: the same answer, one row"


def test_enforce_marks_the_answer_enforced_and_off_neither_decides_nor_records(conn):
    _interrupted(conn)
    resume.snapshot(conn, {"id": "room_1", "image_id": "img_aaaaaa"}, [MAC])
    db.set_setting(conn, resume.SETTING, "enforce")
    got = _ask(conn)[0]
    assert (got["verdict"], got["enforced"]) == ("resume", True)
    assert _rows(conn)[0]["mode"] == "enforce" and _rows(conn)[0]["outcome"].endswith("resume")

    db.set_setting(conn, resume.SETTING, "off")
    off = _ask(conn, session="ses_3")[0]
    assert (off["verdict"], off["enforced"]) == ("restart", False) and len(_rows(conn)) == 1


def test_a_slow_read_back_against_this_disks_own_pace_is_a_restart_with_its_numbers(conn):
    _interrupted(conn)
    resume.snapshot(conn, {"id": "room_1", "image_id": "img_aaaaaa"}, [MAC])
    got = _ask(conn, readback_s=900)[0]
    assert got["verdict"] == "restart" and "להתחיל מחדש" in got["reason"], got
    assert _rows(conn)[0]["verdict"] == "RESTART"


@pytest.mark.parametrize("ask, why", [
    (dict(image="img_bbbbbb"), "אין לדיסק הזה ledger"),           # the ledger is of another image: not the same write
    (dict(readback_s=None), "זמן הקריאה החוזרת לא נמדד"),          # the agent could not measure its disk
])
def test_no_evidence_is_restart(conn, ask, why):
    _interrupted(conn)
    resume.snapshot(conn, {"id": "room_1", "image_id": "img_aaaaaa"}, [MAC])
    got = _ask(conn, **ask)[0]
    assert got["verdict"] == "restart" and why in got["reason"], got


def test_no_snapshot_no_ledger_restart(conn):
    """שום "כתוב מחדש" לא נענה על המשכפל הזה (או שהשרת הופעל מחדש בינתיים): אין ממה להמשיך."""
    _interrupted(conn)
    got = _ask(conn)[0]
    assert got["verdict"] == "restart" and got["ledger"] == {}


# --- 4. המעבר הראשון -----------------------------------------------------------------------------------------------


def _opened(rig):
    """The engine reads the partitions' indexes in its own thread, after `start` returns: before that a `listening`
    that carries ranges is refused as `bad_durable` ("durable ranges for a partition the server is not sending",
    `_read_targets`) -- and the first tell of these tests would not be the case they name."""
    assert wait_for(lambda: rig.engine.status()["state"] == "sending"), rig.engine.status()


def test_the_first_pass_sends_only_what_every_listener_lacks(rig):
    rig.members = ["a"]
    rig.make()
    _opened(rig)
    assert rig.tell("a", 1, "listening", {"A1": [[0, 5]]})["action"] == "receive"     # read back: 0-5 are on it
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.runner.lists[0] == "6-9", rig.runner.lists
    assert rig.named("chunk_first_pass_from_ledger")


def test_a_listener_with_nothing_read_back_gets_the_whole_pass_as_today(rig):
    rig.members = ["a", "b"]
    rig.make()
    _opened(rig)
    rig.tell("a", 1, "listening", {"A1": [[0, 5]]})
    rig.tell("b", 1, "listening", {"B1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.runner.lists[0] is None


def test_a_partition_whole_on_every_listener_is_not_sent_at_all(rig):
    rig.members = ["a"]
    rig.make()
    _opened(rig)
    rig.tell("a", 1, "listening", {"A1": [[0, 9]]})
    assert rig.ask("a", 1, {"A1": [[0, 9]]})["action"] == "done"
    assert rig.runner.commands == []
