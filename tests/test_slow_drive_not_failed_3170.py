"""#3170 מול #3295 (נדב 15/11, אפשרות ב'): "ההפצה בקצב של המהירים. האיטי מפספס חתיכות ומשלים אותן אחר כך."

מה שהפייסטוס מדד על הברזל, בגל של 700m: ה-APS כתב בכ-66MB/s, מתחת ל-74 (המהירות המינימלית לכונן), והשרת הכשיל אותו
"לא עמד בקצב". הדיסק לא השלים; אחר כך **החדר פתח גל 2 לבד והעיר את המשכפל עד 6 פעמים** -- בלי שום כשל אמיתי.

השרשרת: כשל של הדיסק (`fail` בתשובה של מנוע החתיכות) -> הסוכן מדווח `failed` -> הסבב נשאר עם דיסק טרי -> גל 2 ו-WoL.
הטסטים כאן חותכים אותה בשני מקומות: **המנוע** אינו מכשיל דיסק איטי (`test_chunksend_2533.py` ו-`test_chunk_repair_2533.py` בודקים
את הפרטים), והחדר, כשהדיסק האיטי השלים והסוכן מדווח `done`, אינו פותח גל 2 ואינו מעיר. השומר ההפוך: דיסק שנכשל באמת
(כתיבה שנכשלה) עדיין פותח גל 2, כדי שהתיקון לא יבלע כשל.

**בקרה שלילית:** החזרת `repair.plan` ו-`chunksend.py` לבסיס (דיסק איטי נכשל) מפילה את שני הטסטים של המנוע כאן.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.rounds import repair
from test_cancel_drawer_1386_server import _progress
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1, cloner_hello, heartbeat, room, room_body, room_server,
)


def _events(ctx, name: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (name,))]


def test_the_engine_does_not_fail_the_hephaestus_drive():
    """700m (~87.5MB/s), the APS at ~66MB/s under 74: no failure for the room to act on."""
    plan = repair.plan(8000, {"aps": {"S1": [(0, 6059)]}, "other": {"S2": [(0, 7999)]}}, repairs_done=0,
                       skipped={"aps": {"S1": 1940}}, asked_mbps=87.5, floor_mbps=74)
    assert plan.failed == {} and ("aps", "S1") in plan.slow and plan.fetch == {"aps": [(6060, 7999)]}


def _two_drawers(room_server, second: str) -> tuple[str, str, int]:
    """A round of two drives; S1 done, S2 reported `second`. Returns (round id, wave id, WoL sent so far)."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    if room(deploy)["round"]["wave_state"] == "open":
        assert deploy.post("/api/console/room/start").status_code == 200
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    woken = len(room_server["woken"])
    _progress(anon, wave, CLONER1, {"sda": "done", "sdb": second}, top="done" if second == "done" else "partial")
    heartbeat(room_server)
    return opened.json()["id"], wave, woken


def test_a_slow_drive_that_completed_opens_no_second_wave_and_wakes_nobody(room_server):
    """After the fix the APS completes (from its neighbour or over HTTP) and the agent reports it `done`: the round is
    done on wave 1 -- no `room_wave wave=2`, no WoL, no `room_failed`."""
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    round_id, _wave, woken = _two_drawers(room_server, "done")
    assert room(deploy)["round"] is None                                       # closed: nothing left to write
    row = ctx.conn.execute("SELECT state, written_drives, failed_reason FROM room_rounds WHERE id = ?",
                           (round_id,)).fetchone()
    assert (row["state"], row["written_drives"], row["failed_reason"]) == ("closed", 2, None)
    assert _events(ctx, "room_done") == [f"{round_id} written=2/2"]
    assert _events(ctx, "room_wave") == [] and _events(ctx, "room_failed") == []
    assert len(room_server["woken"]) == woken, "the cloner was woken for a wave nobody needed"


def test_the_guard_a_drive_that_really_failed_still_opens_wave_two(room_server):
    """The fix must not swallow a real failure: a drawer the agent reports `failed` (a write error) is still wave 2."""
    deploy = room_server["deploy"]
    _round_id, _wave, _woken = _two_drawers(room_server, "failed")
    view = room(deploy)["round"]
    assert (view["written_drives"], view["remaining_drives"], view["wave_number"]) == (1, 1, 2), view
