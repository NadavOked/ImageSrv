"""#2904 — סבב חדר שנשאר `closing` כי השרת מת באמצע "עצור סבב".

`close_round` כותב `closing` ועושה commit **לפני** שהוא סוגר את הגל ועוצר את
המשדר. שרת שמת בחלון הזה השאיר את הסבב `closing` לנצח: `tick` טוען רק `active`,
"עצור סבב" שני מסורב ("כבר בתהליך סגירה"), ו-`reconcile_on_start` לא נגע
ב-`room_rounds`. `busy_db.reason` ראה "יש סבב חדר פתוח או רץ" — והגיבוי היומי,
האתחול המתוזמן ו"החל עדכון" נעצרו.

כמו #411/#2373: עלייה מחדש = שרת **שני** על אותו `--data-dir`. ההבחנה שנבדקת:
הסבב נסגר (`closed` — המפעיל ביקש לעצור; לא חוזר ל-`active`), יש לו יומן, הגל
שלו אינו נשאר פתוח, והשרת אינו "עסוק". סבב `active` אינו נוגע (שם הטסט הקיים
`test_the_room_round_survives_and_the_next_pulse_opens_a_fresh_wave`).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_reconcile_on_start import _boot, _events, _running_wave  # noqa: E402
from test_server_room import CLONER1, CLONER2, cloner_hello  # noqa: E402

from server.system import busy_db  # noqa: E402


def _cut_stop(ctx, wave_state: str) -> str:
    """"עצור סבב" שנקטע: ה-CAS ל-`closing` נשמר, והשרת מת לפני שהגל הגיע
    ל-`wave_state` הסופי. מחזיר את מזהה הסבב."""
    conn = ctx.conn
    row = conn.execute("SELECT id, wave_session_id FROM room_rounds").fetchone()
    conn.execute("UPDATE room_rounds SET state = 'closing' WHERE id = ?", (row["id"],))
    conn.execute("UPDATE sessions SET state = ? WHERE id = ?", (wave_state, row["wave_session_id"]))
    conn.commit()
    assert busy_db.reason(conn) is not None      # לפני העלייה: השרת "עסוק"
    ctx.sender.stop()
    return row["id"]


def _round(ctx, round_id: str):
    return ctx.conn.execute(
        "SELECT state, closed_at, wave_session_id FROM room_rounds WHERE id = ?",
        (round_id,)).fetchone()


@pytest.mark.parametrize("wave_state", ["closed", "running"])
def test_a_round_cut_while_closing_is_closed_on_start_and_the_server_is_not_busy(
        tmp_path, monkeypatch, wave_state):
    """`closed`: השרת מת בתוך `sender.stop` (החלון הארוך, שניות). `running`: מת
    בין ה-CAS ל-`store.close` — הגל נסגר ככישלון בשם (#411), והסבב נסגר איתו."""
    first = _boot(tmp_path, monkeypatch, seed=True)
    _running_wave(first)
    round_id = _cut_stop(first["ctx"], wave_state)

    second = _boot(tmp_path, monkeypatch, seed=False)
    ctx = second["ctx"]
    row = _round(ctx, round_id)
    assert row["state"] == "closed", dict(row)
    assert row["closed_at"]
    wave = ctx.conn.execute("SELECT state FROM sessions WHERE id = ?",
                            (row["wave_session_id"],)).fetchone()
    assert wave["state"] == "closed"
    assert "room_close" in _events(ctx)
    assert busy_db.reason(ctx.conn) is None
    # tick (hello) לא פותח את הסבב מחדש: המפעיל ביקש לעצור.
    cloner_hello(second["anon"], CLONER1, ["S1", "S2"], joining=False)
    assert _round(ctx, round_id)["state"] == "closed"
    second["ctx"].sender.stop()


def test_a_round_cut_while_closing_with_its_wave_still_open_closes_the_wave_too(
        tmp_path, monkeypatch):
    """גל `open` (ממתין למצטרפים) — #411 אינו נוגע בו בכוונה, ולכן בלי הסגירה
    כאן הוא היה נשאר פתוח תחת סבב סגור, ו-`busy_db` היה אומר "סבב הפצה פתוח"."""
    first = _boot(tmp_path, monkeypatch, seed=True)
    admin, anon = first["admin"], first["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert admin.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 4,
        "target_slots": [{"mac": CLONER1, "ports": [1, 2]},
                         {"mac": CLONER2, "ports": [1, 2]}]}).status_code == 200
    round_id = _cut_stop(first["ctx"], "open")

    second = _boot(tmp_path, monkeypatch, seed=False)
    ctx = second["ctx"]
    row = _round(ctx, round_id)
    assert row["state"] == "closed", dict(row)
    assert ctx.conn.execute("SELECT state FROM sessions WHERE id = ?",
                            (row["wave_session_id"],)).fetchone()["state"] == "closed"
    assert busy_db.reason(ctx.conn) is None
    second["ctx"].sender.stop()


def test_reconcile_reports_the_rounds_it_closed_and_leaves_the_others(tmp_path):
    from server.db import connect
    from server.rounds.reconcile import reconcile_on_start

    conn = connect(tmp_path / "x.db")
    for rid, state in (("r_c", "closing"), ("r_a", "active"), ("r_f", "failed"), ("r_d", "closed")):
        conn.execute(
            "INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
            " VALUES (?, 'img', 4, ?, 'noc', '2026-11-11T00:00:00+00:00')", (rid, state))
    conn.commit()
    out = reconcile_on_start(conn, tmp_path / "images")
    assert out.get("rounds_closed") == ["r_c"], out
    states = dict(conn.execute("SELECT id, state FROM room_rounds").fetchall())
    assert states == {"r_c": "closed", "r_a": "active", "r_f": "failed", "r_d": "closed"}
    assert reconcile_on_start(conn, tmp_path / "images").get("rounds_closed") == []
