"""#3817 (F2, F3; אפיק #3653): תקרת הכנת האימג' (`indexprep`) והמתנה למחשב הבנייה (`directchunk.RELAY_TIMEOUT_S`) הן החלטות של המוח, בצל.

1. **רישום F2.** גל על אימג' שנקלט זה עתה פותח הכנה: שורה אחת `B3817a` עם הגודל, הבסיס, הקצב, התקרה שהמוח היה קובע, התקרה שהקוד החיל
   (`agree=1`) והמקור (הערכה, לא מספר בלי מקור). הגל יוצא בדיוק כמו קודם, והיומן (`room_index_wait`) נושא את אותה תקרה.
2. **צל לא משנה.** תקרה שנחצית מכשילה את הסבב בשמו, כמו ב-#3137, והשורה אומרת `agree=1` על אותה תקרה.
3. **רישום F3.** פתיחת סבב ישיר רושמת שורה אחת `B3817b` עם 30 ש' ומקור, והערך שנרשם הוא הקבוע של הקוד (אין עותק מקביל).
4. **ברירות מחדל בטוחות:** `off` = לא נקרא ולא נרשם; מוח שנפל אינו מפיל את ההכנה ואינו מפיל פתיחת סבב.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import memory, veto
from server.db import connect, set_setting
from test_direct_server import _setup as open_direct
from test_server_room import room, room_server   # noqa: F401 -- room_server הוא fixture
from test_wave_after_capture_3137 import (   # noqa: F401 -- derive הוא fixture
    _events, _fresh_capture, _hello, _open, _until_failed, derive, wait_for)

PREP_RULE, RELAY_RULE = "B3817a", "B3817b"


def brain():
    """מודול המוח של התקרות. חסר = כשל בשמו, לא ImportError שמסתיר שהטסט לא רץ."""
    try:
        from server.brain import ceilings
    except ImportError:
        pytest.fail("server/brain/ceilings.py אינו קיים: אין תקרות הכנת אימג' והעברת חתיכות במוח")
    return ceilings


def rows(conn, rule):
    return memory.decisions(conn, rule=rule, limit=50)


def evidence(row) -> dict[str, str]:
    return dict(item.split("=", 1) for item in row["evidence"])


def test_a_wave_on_a_fresh_capture_records_the_prep_ceiling_and_leaves_as_before(room_server, derive):
    brain()
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    wave = _open(room_server)
    _hello(room_server)
    assert wait_for(lambda: _hello(room_server, joining=False)["session"]["state"] == "running")
    assert wait_for(lambda: (ctx.chunk.status() or {}).get("state") == "sending"), ctx.chunk.status()
    [row] = rows(ctx.conn, PREP_RULE)
    assert (row["mode"], row["action"], row["point"], row["verdict"]) == ("shadow", "IMAGE_PREP_CEILING", "index_prep", "WAIT")
    got = evidence(row)
    assert got["key"] == wave and got["base_s"] == "300" and got["floor_bps"] == "10000000", got
    assert got["ceiling_s"] == got["applied_s"] and got["agree"] == "1", got
    assert got["source"].startswith("estimate:") and "#3137" in got["source"], got
    [detail] = _events(ctx, "room_index_wait")
    assert f"bytes={got['bytes']} ceiling={got['ceiling_s']}s" in detail, (detail, got)
    ctx.chunk.stop(wave)


def test_a_ceiling_that_passes_still_fails_the_round_by_name_and_the_row_says_the_same_ceiling(room_server, derive, monkeypatch):
    from server.rounds import indexprep
    brain()
    monkeypatch.setattr(indexprep, "CEILING_BASE_S", 0.5)
    ctx = room_server["ctx"]
    _fresh_capture(ctx)
    derive.hold.clear()
    _open(room_server)
    _hello(room_server)
    assert _until_failed(room_server)
    assert room(room_server["deploy"])["round"]["failed_reason"].startswith("הכנת האימג' לשידור לא הסתיימה תוך")
    [row] = rows(ctx.conn, PREP_RULE)
    got = evidence(row)
    assert got["base_s"] == "0.5" and got["agree"] == "1", got      # the brain takes the code's value as it is
    derive.hold.set()


def test_the_open_direct_round_records_the_relay_timeout_from_the_code_constant(room_server):
    from server.rounds import directchunk
    brain()
    opened = open_direct(room_server)
    [row] = rows(room_server["ctx"].conn, RELAY_RULE)
    assert (row["mode"], row["action"], row["point"], row["verdict"]) == ("shadow", "DIRECT_RELAY_TIMEOUT", "direct_open", "WAIT")
    got = evidence(row)
    assert got["task"] == opened["task_id"] and got["timeout_s"] == "30" and got["source"].startswith("estimate:"), got
    assert row["round_id"] == opened["id"]
    assert directchunk.RELAY_TIMEOUT_S == 30                         # shadow: the value did not move


def test_the_relay_row_carries_the_codes_constant_not_a_copy(room_server, monkeypatch):
    from server.rounds import directchunk
    brain()
    monkeypatch.setattr(directchunk, "RELAY_TIMEOUT_S", 45)
    opened = open_direct(room_server)                                 # one open per test: _setup registers the build machine
    assert any(evidence(r)["timeout_s"] == "45" for r in rows(room_server["ctx"].conn, RELAY_RULE) if r["round_id"] == opened["id"])


def test_off_records_nothing_and_a_fallen_brain_does_not_throw(tmp_path, monkeypatch):
    ceilings = brain()
    conn = connect(tmp_path / "off.db")
    set_setting(conn, veto.SETTING, "off")
    ceilings.prep_shadow(conn, "w", "img_x", 10**9, 310.0, base_s=300.0, floor_bps=10_000_000)
    ceilings.relay_shadow(conn, "room_off", "tsk_off", 30)
    assert memory.decisions(conn, limit=50) == []
    set_setting(conn, veto.SETTING, "shadow")

    def boom(*a, **k):
        raise RuntimeError("the brain fell")
    monkeypatch.setattr(memory, "record", boom)
    ceilings.prep_shadow(conn, "w", "img_x", 10**9, 310.0, base_s=300.0, floor_bps=10_000_000)
    ceilings.relay_shadow(conn, "room_x", "tsk_x", 30)


@pytest.mark.parametrize("size", [0, 1_700_000_000, 18_000_000_000])
def test_the_brain_ceiling_is_the_one_the_code_has_today(size):
    from server.rounds import indexprep
    assert brain().image_prep_s(size, indexprep.CEILING_BASE_S, indexprep.CEILING_FLOOR_BPS) == indexprep.ceiling_s(size)
