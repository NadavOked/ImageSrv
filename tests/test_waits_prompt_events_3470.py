"""#3470: ההמתנה למפעיל נמדדת מהאירועים שנכתבים היום, ו"אין אירוע" אינו 0.

`learn/waits.py` קרא רק `send_held_smart`/`send_hold_released` (נעלמו ב-#2044), ומנוע החתיכות החזיק גל על שאלה בלי
לכתוב דבר. כאן: (1) מנוע החתיכות כותב `send_held_prompt` בתחילת ההחזקה ו-`send_prompt_released` עם השניות בסופה, באותו פורמט
של השולח; (2) `round_waits`/`week` קוראים את שני הזוגות; (3) סבב בלי אירוע שחרור הוא `None`, ושורת הבריאות אינה ירוקה.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server import db
from server.learn import waits as learn_waits
from server.rounds import chunksend

from test_chunksend_2533 import SID, rig, wait_for  # noqa: F401 -- `rig` is a fixture

T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)


def iso(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _round(conn, waves=(("ses_a", 0, 2),)):
    conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at, closed_at)"
                 " VALUES ('room_1', 'img', 4, 'closed', 't', ?, ?)", (iso(0), iso(60)))
    for sid, created, started in waves:
        conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                     " state, opened_by, created_at, last_join_at, started_at, closed_at, kind)"
                     " VALUES (?, 'grp_CLONERS', 'img', 'p', 1, 300, 'closed', 't', ?, 0, ?, ?, 'multicast')",
                     (sid, iso(created), iso(started), iso(created + 30)))


def _journal(conn, minutes, event, detail):
    conn.execute("INSERT INTO journal (ts, event, detail) VALUES (?,?,?)", (iso(minutes), event, detail))


# --- מנוע החתיכות כותב את ההחזקה ------------------------------------------------------------------------------------


def test_the_chunk_engine_journals_a_hold_for_a_question_and_its_release(rig, monkeypatch):
    monkeypatch.setattr(chunksend, "HELD_FRESH_S", 0.3, raising=False)
    asking = {"open": True}
    rig.make(pending_prompts=lambda sid: ["c"] if asking["open"] else [])
    assert wait_for(lambda: rig.tell("a", 1, "listening", {"A1": []}).get("held") is True)
    assert rig.named("send_held_prompt") == [f"{SID} c"], rig.events
    assert rig.named("send_prompt_released") == [], "עוד מחזיקים: אין שחרור"
    asking["open"] = False
    rig.tell("c", 1, "listening", {"C1": []})
    assert wait_for(lambda: len(rig.named("send_prompt_released")) == 1), rig.events
    [released] = rig.named("send_prompt_released")
    assert released.startswith(f"{SID} ") and released.endswith("s"), released
    held_s = learn_waits._RELEASED.match(released)
    assert held_s is not None, "השחרור חייב להתאים לפורמט ש-`learn/waits.py` קורא"


def test_the_chunk_engine_without_a_question_writes_no_hold_events(rig):
    rig.make(pending_prompts=lambda sid: [])
    for mac in ("a", "b", "c"):
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.named("send_held_prompt") == [] and rig.named("send_prompt_released") == []


# --- הסיכום קורא את האירועים של היום --------------------------------------------------------------------------------


def test_a_round_counts_the_prompt_hold_and_both_event_pairs(conn):
    _round(conn, (("ses_a", 0, 2), ("ses_b", 20, 22)))
    _journal(conn, 2, "send_held_prompt", "ses_a 78:ac:c0:9b:11:c2")
    _journal(conn, 12, "send_prompt_released", "ses_a 600s")
    _journal(conn, 22, "send_held_smart", "ses_b 78:ac:c0:9b:11:c2")       # הזוג הישן: היסטוריה
    _journal(conn, 25, "send_hold_released", "ses_b 120s")
    conn.commit()
    got = learn_waits.round_waits(conn, iso(0), iso(60))
    assert (got["smart_hold_s"], got["smart_holds"], got["smart_unreleased"]) == (720, 2, 0)


def test_a_prompt_hold_that_was_never_released_is_counted_but_has_no_seconds(conn):
    _round(conn)
    _journal(conn, 2, "send_held_prompt", "ses_a 78:ac:c0:9b:11:c2")
    conn.commit()
    got = learn_waits.round_waits(conn, iso(0), iso(60))
    assert (got["smart_hold_s"], got["smart_holds"], got["smart_unreleased"]) == (None, 0, 1)


def test_no_event_is_not_measured_and_not_zero(conn):
    """RED על הבסיס: גל שיצא בלי אף אירוע החזיר 0."""
    _round(conn)
    conn.commit()
    got = learn_waits.round_waits(conn, iso(0), iso(60))
    assert got["smart_hold_s"] is None and got["waves"] == 1 and got["open_wait_s"] == 120


def test_the_week_line_is_not_green_when_nothing_was_measured(conn):
    _round(conn)
    conn.commit()
    [line] = learn_waits.health_checks(conn)
    assert line["state"] != "ok" and "לא נמדד" in line["detail"], line


def test_the_week_line_is_green_when_a_short_hold_was_measured(conn):
    _round(conn)
    _journal(conn, 2, "send_held_prompt", "ses_a m")
    _journal(conn, 3, "send_prompt_released", "ses_a 40s")
    conn.commit()
    [line] = learn_waits.health_checks(conn)
    assert line["state"] == "ok" and "פחות מדקה" in line["detail"], line
