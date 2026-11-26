"""#3687 (S32; אפיק #3653): תקרות השתיקה של מחשב הבנייה בהפצה ישירה הן החלטה של המוח, **בצל**.

שתי התקרות הן אותה שתיקה, ולכן שורה אחת: מעבר שנקטע (`directchunk.SILENCE_GRACE_S` מעל `lost_wait_seconds`), ומשימת המקור של סבב שנסגר
(`direct_settle.SOURCE_SILENT_S`). הערכים נשארים בקוד כנקודת התחלה והם הקלט של המוח: אין עותק מקביל שיכול להיסחף, וההתנהגות זהה להיום.

1. **רישום.** פתיחת סבב ישיר רושמת שורה אחת `B3687` (`shadow`, `WAIT`) עם `lost_s`, `grace_s`, `ceiling_s`, `settle_s` ומקור.
2. **אין עותק מקביל.** הערכים שנרשמו הם הקבועים של הקוד: משנים קבוע והשורה אומרת אותו שינוי; ו-`lost_wait_seconds` נקרא מההגדרה.
3. **צל לא משנה.** הקבועים של הקוד נשארים 150 ו-120, ו-`directchunk.wait` ו-`direct_settle` לא נגעו.
4. **ברירות מחדל בטוחות.** `off` = לא נקרא ולא נרשם; מוח שנפל אינו מפיל פתיחת סבב.

**בקרה שלילית:** `server/brain/ceilings.py` ו-`server/rounds/direct.py` חוזרים לבסיס (ו-`catalog.toml`): `source_silence_shadow` נעלם, ופתיחת הסבב אינה
רושמת, ולכן הטסטים ההתנהגותיים נופלים ב-assert על `[row]` ריק, והטסט של הקטלוג נופל על `B3687`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.agent import timing as agent_timing
from server.brain import memory, veto
from server.db import connect, set_setting
from test_direct_server import _setup as open_direct
from test_server_room import room, room_server   # noqa: F401 -- room_server הוא fixture

RULE = "B3687"
REPO = Path(__file__).resolve().parent.parent


def brain():
    """מודול התקרות של המוח. חסר הפונקציה = כשל בשמה, לא ImportError שמסתיר שהטסט לא רץ."""
    from server.brain import ceilings
    if not hasattr(ceilings, "source_silence_shadow"):
        pytest.fail("server/brain/ceilings.py אינו מכיר את שתיקת המקור: אין תקרת שתיקה של מחשב הבנייה במוח")
    return ceilings


def rows(conn):
    return memory.decisions(conn, rule=RULE, limit=50)


def evidence(row) -> dict[str, str]:
    return dict(item.split("=", 1) for item in row["evidence"])


def test_the_open_direct_round_records_both_silence_ceilings_from_the_codes_constants(room_server):
    from server.rounds import direct_settle, directchunk
    brain()
    ctx = room_server["ctx"]
    opened = open_direct(room_server)
    [row] = rows(ctx.conn)
    assert (row["mode"], row["action"], row["point"], row["verdict"]) == ("shadow", "DIRECT_SOURCE_SILENCE", "direct_open", "WAIT")
    assert row["round_id"] == opened["id"]
    got = evidence(row)
    lost = agent_timing.lost_seconds(ctx.conn)
    assert got["task"] == opened["task_id"]
    assert got["lost_s"] == f"{lost:g}" and got["grace_s"] == f"{directchunk.SILENCE_GRACE_S:g}", got
    assert got["ceiling_s"] == f"{lost + directchunk.SILENCE_GRACE_S:g}", got
    assert got["settle_s"] == f"{direct_settle.SOURCE_SILENT_S:g}", got
    assert got["source"].startswith("code:") and "unmeasured" in got["source"], got
    assert (directchunk.SILENCE_GRACE_S, direct_settle.SOURCE_SILENT_S) == (150, 120)     # shadow: the values did not move


def test_the_row_carries_the_codes_constants_not_a_copy(room_server, monkeypatch):
    from server.rounds import direct_settle, directchunk
    brain()
    monkeypatch.setattr(directchunk, "SILENCE_GRACE_S", 200)
    monkeypatch.setattr(direct_settle, "SOURCE_SILENT_S", 90)
    opened = open_direct(room_server)
    [row] = [r for r in rows(room_server["ctx"].conn) if r["round_id"] == opened["id"]]
    got = evidence(row)
    assert got["grace_s"] == "200" and got["settle_s"] == "90", got


def test_the_ceiling_follows_lost_wait_seconds_from_the_setting(room_server):
    brain()
    ctx = room_server["ctx"]
    set_setting(ctx.conn, agent_timing.LOST, "300")
    open_direct(room_server)
    [row] = rows(ctx.conn)
    got = evidence(row)
    assert got["lost_s"] == "300" and got["ceiling_s"] == "450", got


def test_off_records_nothing_and_a_fallen_brain_does_not_throw(tmp_path, monkeypatch):
    ceilings = brain()
    conn = connect(tmp_path / "off.db")
    set_setting(conn, veto.SETTING, "off")
    ceilings.source_silence_shadow(conn, "room_off", "tsk_off", 180, 150, 120)
    assert memory.decisions(conn, limit=50) == []
    set_setting(conn, veto.SETTING, "shadow")

    def boom(*a, **k):
        raise RuntimeError("the brain fell")
    monkeypatch.setattr(memory, "record", boom)
    ceilings.source_silence_shadow(conn, "room_x", "tsk_x", 180, 150, 120)


def test_the_rule_is_in_the_catalog_and_points_at_the_code():
    with (REPO / "server" / "brain" / "catalog.toml").open("rb") as f:
        items = {it["id"]: it for it in tomllib.load(f)["item"]}
    item = items.get(RULE)
    assert item is not None, "B3687 אינו בקטלוג"
    assert item["mode"] == "shadow" and item["symbol"] == "source_silence_shadow"
    covered = {(a["file"], a["symbol"]) for a in item["also"]}
    assert ("server/rounds/directchunk.py", "SILENCE_GRACE_S") in covered
    assert ("server/rounds/direct_settle.py", "SOURCE_SILENT_S") in covered
