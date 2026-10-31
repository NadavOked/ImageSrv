"""‏#1703 — ה-hello שמשלים את הגל מוציא אותו לדרך **באותה תשובה**.

ברזל 17/10 (יומן השרת TA): משכפל שהוער ב-WoL לתוך גל פתוח — מה-hello הראשון
עד `GET …/manifest`: ‏22 ש' (ses_fbd8f9ca, 08:24:11→08:24:33) ו-23 ש'
(ses_81f31ff4, 09:41:45→09:42:08). משכפל שכבר היה דלוק: 0.

השורש בשרת: ‏`hello.py` מריץ את הדופק (`room.pulse` → `tick`) **לפני**
‏`record_hello`. בדופק הזה המכונה עוד אינה חברה, ‏`ready_drives` אינו סופר
את המגירות שלה, והגל נשאר `open` — גם כשהיא הנבחרת האחרונה (או היחידה).
התשובה אומרת `open`, והגל יוצא רק בדופק **הבא**: שעון הרקע (‏5 ש') או
ה-hello הבא. הסוכן לומד שהגל יצא רק מה-hello הבא של הלולאה הראשית, וזה
מגיע אחרי `smart_probe_idle`, הרמת הגואי, מסך הבחירה (#1322, יוצא על
`open` לפי #1560) ו-`poll_sleep`.

השעון מזויף (`now_fn=Clock()` של השרת ו-`room._utcnow`) ואינו זז; ‏TestClient
בלי `with` אינו מריץ את שעון הרקע; ואין כאן `room.sweep` ולא hello נוסף.
כלומר אם הגל רץ — ה-hello עצמו הוציא אותו.

שומרי תיקון-יתר (עוברים לפני התיקון ואחריו):

- חבר שעוד בדרך אינו מוחמץ: שני נבחרים, הראשון מקבל `open`.
- דופק לא-מצטרף (`joining: false`, ‏attended.sh) אינו מצרף ואינו מוציא.
- ‏#1728: החבר שהצטרף ראשון מקבל את הגל ב-hello הבא שלו ואינו נחשב
  אתחול באמצע הזרם.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.rounds import room as room_mod

from test_server_room import (           # noqa: F401 — ‏room_server הוא fixture
    CLONER1, CLONER2, cloner_hello, room_body, room_server,
)

pytest.importorskip("fastapi")


def _freeze(monkeypatch) -> None:
    frozen = datetime.now(timezone.utc)
    monkeypatch.setattr(room_mod, "_utcnow", lambda: frozen)


def _starts(ctx) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'session_start_auto'")]


def _member_state(ctx, wave: str, mac: str) -> str | None:
    row = ctx.conn.execute(
        "SELECT state FROM session_members WHERE session_id = ? AND mac = ?",
        (wave, mac)).fetchone()
    return row["state"] if row else None


def test_the_hello_of_the_only_selected_cloner_starts_the_wave_in_its_own_answer(
        room_server, monkeypatch):
    """המקרה מהברזל: מחשב שיכפול יחיד שנבחר, עולה מ-WoL, hello ראשון."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _freeze(monkeypatch)
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text

    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])

    session = answer["session"]
    assert session is not None, answer
    assert session["state"] == "running", (
        "כל הנבחרים כבר בגל ומגירותיהם מכסות את היעד, והתשובה עדיין "
        f"'{session['state']}' — הגל ממתין לדופק הבא (#1703)")
    assert _starts(ctx), "הגל לא יצא לדרך ביומן"
    assert _member_state(ctx, session["id"], CLONER1) == "waiting"


def test_two_selected_the_first_waits_and_the_second_starts_the_wave(room_server, monkeypatch):
    """חבר שעוד בדרך אינו מוחמץ; וה-hello של האחרון מוציא את הגל מיד."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _freeze(monkeypatch)
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    assert opened.status_code == 200, opened.text

    first = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert first["session"]["state"] == "open", (
        "הגל יצא בלי הנבחר השני — חבר בדרך הוחמץ")
    assert not _starts(ctx)

    second = cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert second["session"]["state"] == "running", (
        f"הנבחר האחרון הצטרף והגל '{second['session']['state']}' (#1703)")
    wave = second["session"]["id"]
    assert {m["mac"] for m in ctx.store.members(wave)} == {CLONER1, CLONER2}

    # ‏#1728: הראשון לומד שהגל יצא מה-hello הבא שלו — חבר `waiting`, לא אתחול.
    again = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert again["session"] is not None and again["session"]["id"] == wave
    assert again["session"]["state"] == "running"
    assert _member_state(ctx, wave, CLONER1) == "waiting"
    assert not ctx.conn.execute(
        "SELECT 1 FROM journal WHERE event = 'room_rejoin'").fetchone()


def test_a_non_joining_beat_neither_joins_nor_starts(room_server, monkeypatch):
    """הדופק של מסך שממתין לאדם (`joining: false`) אינו מצטרף — ולכן גם אינו
    מוציא גל. ההחלטה להצטרף נשארת של הלולאה הראשית."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _freeze(monkeypatch)
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text

    beat = cloner_hello(anon, CLONER1, ["S1", "S2"], joining=False)

    assert beat["session"]["state"] == "open"
    wave = beat["session"]["id"]
    assert not ctx.store.members(wave)
    assert not _starts(ctx)


# --- the agent: the boot choice leaves on the main loop's own "open" ---------

def test_the_boot_choice_leaves_on_the_main_answer_without_waiting_for_the_beat(tmp_path):
    """מחשב שיכפול שה-hello הראשי שלו ענה `open` (גל ממתין למשכפל נוסף):
    מסך הבחירה (#1322) יוצא מיד — לא אחרי `sleep 1` והלוך-חזור של הדופק
    (attended.sh) שיחזור ויגיד את אותו `open`. כאן הדופק עוד לא ענה
    (`attended.round` ריק), וה-`sleep` של העולם המדומה סופר: ‏STAYED = נשאר."""
    from test_end_of_task_1560 import cloner_menu        # noqa: PLC0415

    trace, log = cloner_menu(tmp_path, '""', (
        'D_SESSION_STATE=open; echo boot > "$LC/screen"; lc_boot_choice; '
        'echo "LEFT $LC_CHOICE" >> "$RUN_DIR/trace"'))
    assert trace == ["LEFT server"], trace
    assert "a round is open for this machine" in log


def test_no_round_in_either_answer_keeps_the_boot_choice(tmp_path):
    """שומר: בלי סבב לא בתשובה הראשית ולא בדופק — המסך נשאר (הספירה של
    `LC_BOOT_S`, החלטת נדב, אינה מתקצרת)."""
    from test_end_of_task_1560 import cloner_menu        # noqa: PLC0415

    trace, _ = cloner_menu(tmp_path, '""', (
        'D_SESSION_STATE=none; echo boot > "$LC/screen"; lc_boot_choice'))
    assert trace == ["STAYED"], trace
