"""‏#2916 §3 — "בטל דיסק זה" שהסוכן סירב לו נסגר בשמו, לא נשאר "מבטלת…".

**התרחיש:** השרת מקבל ביטול לפי מצב המגירה בדיווח **האחרון** (`writing`), והסוכן כבר
עבר ל-`verifying` — הדיווח נשלח כל ~2 ש'. הסוכן מקבל ביטול רק על מגירה ש-`writing`
(`cancel_request`, ‏cancel.sh) ומסרב בשקט: בלי קובץ ובלי לוג. ‏`drawer_cancels.done_at`
נשאר NULL, ‏`pending` שולח את הבקשה בכל תשובה, והמסכים מציגים "מבטלת…" עד סוף הסבב.

**התיקון (בשרת — הסוכן לא משנה):** דיווח שאומר שהמגירה כבר לא כותבת (`verifying`,
`done`, `failed`) סוגר את הבקשה: היא נמחקת, ביומן `room_drawer_cancel_refused` עם
"הכתיבה כבר הסתיימה — באימות", ואינה נשלחת שוב. בלי "בוטל" — המגירה לא בוטלה.
"""

from __future__ import annotations

import pytest

from test_cancel_drawer_1386_server import (CANCEL, CLONER1, _drawer, _journal, _progress,  # noqa: F401
                                            _running)
from test_cancel_drawer_1386_server import room, room_server  # noqa: F401 -- fixtures
from test_kiosk_app import kiosk_env  # noqa: F401 -- fixture


def _ask(room_server, dev="sdb"):
    deploy, anon = room_server["deploy"], room_server["anon"]
    wave = _running(room_server)
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert deploy.post(CANCEL, json={"mac": CLONER1, "dev": dev}).status_code == 200
    return wave


@pytest.mark.parametrize("state, why", [
    ("verifying", "הכתיבה כבר הסתיימה — באימות"),
    ("done", "הכתיבה כבר הסתיימה"),
    ("failed", "הדיסק כבר נכשל בכתיבה"),
])
def test_a_cancel_the_agent_can_no_longer_carry_out_is_closed_in_its_name(room_server, state, why):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _ask(room_server)
    assert _drawer(room(deploy), CLONER1, "sdb")["cancel"]["done"] is False   # "מבטלת…"

    # the agent had already moved on: the very next report says so, and the request is closed
    reply = _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": state})
    assert "cancel" not in reply, "the request is still sent to an agent that refuses it"
    refused = _journal(ctx, "room_drawer_cancel_refused")
    assert len(refused) == 1 and why in refused[0][0] and "sdb" in refused[0][0], refused
    assert refused[0][1] == "labtech"
    assert ctx.conn.execute("SELECT COUNT(*) FROM drawer_cancels").fetchone()[0] == 0
    view = _drawer(room(deploy), CLONER1, "sdb")
    assert view["cancel"] is None, "'מבטלת…' stays on a drawer that is no longer writing"
    assert view["state"] == state
    # not a cancel: no "בוטל" line, and nothing was recorded as cancelled
    assert _journal(ctx, "room_drawer_cancelled") == []

    # once, however many reports follow
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": state})
    assert len(_journal(ctx, "room_drawer_cancel_refused")) == 1


def test_a_cancel_on_a_drawer_that_still_writes_is_kept_and_sent(room_server):
    """השומר מפני תיקון-יתר: הדיווח עוד אומר `writing` (הסוכן לא הספיק לקבל) — הבקשה נשארת."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _ask(room_server)
    reply = _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert [c["dev"] for c in reply["cancel"]] == ["sdb"]
    assert _journal(ctx, "room_drawer_cancel_refused") == []
    assert _drawer(room(deploy), CLONER1, "sdb")["cancel"]["done"] is False


def test_a_confirmed_cancel_is_still_a_cancel_and_not_a_refusal(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _ask(room_server)
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "cancelled"})
    assert len(_journal(ctx, "room_drawer_cancelled")) == 1
    assert _journal(ctx, "room_drawer_cancel_refused") == []
    assert _drawer(room(deploy), CLONER1, "sdb")["cancel"]["done"] is True


def test_a_drawer_that_was_never_asked_to_cancel_is_untouched(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _running(room_server)
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    _progress(anon, wave, CLONER1, {"sda": "verifying", "sdb": "verifying"})
    assert _journal(ctx, "room_drawer_cancel_refused") == []
    assert _drawer(room(deploy), CLONER1, "sdb")["cancel"] is None
