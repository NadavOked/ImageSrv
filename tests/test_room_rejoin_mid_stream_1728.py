"""#1728 — משכפל שאותחל באמצע הזרם אינו מצטרף שוב לאותו גל.

ברזל rc5, 18/10 (journal 707–719): `reboot -f` במשכפל באמצע מחיצה 3. הוא
עלה תוך 27 שניות, שלח hello, וקיבל את **אותו גל `running`** — כי הוא כבר
היה חבר בו. הוא ניסה לכתוב את מחיצה 1 מזרם שכבר היה במחיצה 3, fanout החזיר
`empty stream`, שני הדיסקים התקינים נרשמו `disk_failure`, והגל נגמר בלי
שנכתב דבר — `room_failed written=0/2` (#1672), אף שהמשכפל חי ומוכן.

מה שנדרש:

1. hello **מצטרף** של חבר שכבר נכנס לזרם (מצבו אינו `waiting`) אינו מקבל
   את הגל הרץ. החבר מסומן `lost` בגל הזה, והיומן אומר `room_rejoin`.
2. כשהגל נגמר בלי שנכתב בו דבר, וחבר שסומן כך **חי** (hello טרי) — הסבב
   אינו נכשל: נפתח גל 2, והמשכפל מצטרף אליו.

שומרי תיקון-יתר (עוברים לפני התיקון ואחריו):

- ה-hello הראשון אחרי שהגל יצא, מחבר שעוד לא דיווח (`waiting`), עדיין מקבל
  את הגל — זו הדרך היחידה שבה הסוכן לומד שהגל יצא.
- דופק לא-מצטרף (`joining: false`) מחבר שכותב אינו מוציא אותו מהגל.
- #1672 נשמר: חבר שנעלם ולא חזר → הסבב `failed`
  (`test_room_dead_wave_fails_round.py`).
"""

from __future__ import annotations

import pytest

from test_server_room import (           # noqa: F401 — room_server הוא fixture
    CLONER1, cloner_hello, heartbeat, room, room_body, room_server,
)

pytest.importorskip("fastapi")


def _start(room_server) -> str:
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"
    return wave


def _writing(client, wave: str) -> None:
    """המשכפל בתוך הזרם: דיווח `writing` לא-סופי על שתי המגירות."""
    resp = client.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [{"dev": d, "bytes_written": 40, "bytes_total": 100,
                     "state": "writing"} for d in ("sda", "sdb")],
    })
    assert resp.status_code == 200, resp.text


def _member_state(ctx, wave: str) -> str:
    return ctx.conn.execute(
        "SELECT state FROM session_members WHERE session_id = ? AND mac = ?",
        (wave, CLONER1),
    ).fetchone()["state"]


def _events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]


def test_a_cloner_rebooted_mid_stream_is_not_handed_the_running_wave(room_server):
    """המקרה מהברזל, צעד 3: ה-hello שאחרי האתחול אינו מקבל את הגל הרץ."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = _start(room_server)
    _writing(anon, wave)

    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])      # reboot -f → hello

    session = answer["session"]
    assert session is None or session["id"] != wave, (
        f"משכפל שאותחל באמצע הזרם קיבל שוב את הגל הרץ {wave}: {session}")
    assert _member_state(ctx, wave) == "lost", "החבר שאותחל לא יצא מהגל"
    assert "room_rejoin" in _events(ctx), "האתחול באמצע הזרם לא נרשם ביומן (עיקרון 5)"


def test_the_round_goes_on_to_wave_2_when_the_rebooted_cloner_is_alive(room_server):
    """צעדים 5–6: הגל נגמר בלי שנכתב בו דבר — אבל המשכפל חי, ולכן גל 2."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    wave = _start(room_server)
    _writing(anon, wave)
    cloner_hello(anon, CLONER1, ["S1", "S2"])              # האתחול
    heartbeat(room_server)                                  # הדופק הבא מסיים את הגל

    row = ctx.conn.execute("SELECT state, wave_number FROM room_rounds").fetchone()
    assert row["state"] == "active", (
        f"הסבב {row['state']} אף שהמשכפל היחיד חי ויכול לכתוב בגל הבא")
    assert row["wave_number"] == 2
    assert "room_failed" not in _events(ctx)
    assert room(deploy)["round"]["wave_state"] == "open"

    # #1703: ה-hello שמצרף אותו מכסה את היעד — גל 2 יוצא באותה תשובה.
    joined = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert joined is not None and joined["id"] != wave and joined["state"] == "running", joined


def test_the_first_hello_after_the_wave_left_still_gets_it(room_server):
    """שומר: כך הסוכן לומד שהגל יצא. חבר `waiting` מקבל את הגל הרץ."""
    wave = _start(room_server)
    session = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]
    assert session is not None and session["id"] == wave and session["state"] == "running"


def test_a_heartbeat_of_a_writing_cloner_does_not_take_it_out(room_server):
    """שומר: הדופק של הגואי בזמן הכתיבה אינו מצטרף, ואינו אתחול."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = _start(room_server)
    _writing(anon, wave)
    cloner_hello(anon, CLONER1, ["S1", "S2"], joining=False)
    assert _member_state(ctx, wave) == "writing"
    assert "room_rejoin" not in _events(ctx)
