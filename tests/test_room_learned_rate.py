"""‏#447 קצה-לקצה: הכוננים שבגל קובעים את `--max-bitrate`, והמפעיל רואה למה.

הבקרה השלילית של ה-Issue, מול ה-API האמיתי: כונן עם היסטוריה של 15MB/s
מצטרף לגל. **היום** הגל יוצא בתקרה הגלובלית (200m) והמגירה הזו לא עומדת
בה. **אחרי** — ‏udp-sender מקבל 108m, ומסך החדר אומר "נקבע לפי" אותו כונן.
והצד השני: גל בלי היסטוריה יוצא בדיוק בתקרה של היום — שום דבר לא זז.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from test_sender import wait_for  # noqa: E402
from test_server_room import (  # noqa: E402,F401
    CLONER1, CLONER2, cloner_hello, heartbeat, room, room_body, room_server,
)

MB = 1_000_000


def _history(ctx, serial, rate_bps, cap_bps=25 * MB, model="Drawer SSD"):
    """שני גלים קודמים שבהם הכונן הגביל — `MIN_HELD_SAMPLES`."""
    for n in range(2):
        ctx.conn.execute(
            "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
            " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
            (serial, model, f"ses_old{n}_{serial}", rate_bps, cap_bps, rate_bps * 1000,
             1000, "2026-10-01T10:00:00+00:00"))
    ctx.conn.commit()


def _clean_receivers() -> str:
    """‏`wave_rates.drops` של גל ששני המקלטים דיווחו בו אפס זריקות (17/10)."""
    return json.dumps({mac: {"rcvbuf": 0, "inerrors": 0} for mac in (CLONER1, CLONER2)})


def _open_and_join(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    # יעד 5 מול ארבע מגירות: הגל נשאר `open` (מוצג `projected`) עד ההתחלה
    # הידנית. ‏#1703: hello שמכסה את היעד מוציא את הגל בעצמו.
    assert admin.post("/api/console/room", json=room_body(room_server, 5, {
        CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]})).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    return wave


def _sent_bitrate(room_server):
    recorder = room_server["recorder"]
    assert wait_for(lambda: recorder.commands), "udp-sender לא הופעל"
    cmd = recorder.commands[0]
    return cmd[cmd.index("--max-bitrate") + 1] if "--max-bitrate" in cmd else None


def test_a_known_slow_drive_sets_the_waves_bitrate_and_is_named(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.sender.max_bitrate = "200m"
    _history(ctx, "S3", 15 * MB)
    _open_and_join(room_server)

    projected = room(admin)["round"]["wave_rate"]
    assert projected["projected"] is True
    assert projected["max_bitrate"] == "108m"
    assert projected["by"]["serial"] == "S3"

    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "108m"

    actual = room(admin)["round"]["wave_rate"]
    assert actual["projected"] is False
    assert actual["basis"] == "learned" and actual["max_bitrate"] == "108m"
    assert actual["by"]["serial"] == "S3" and actual["by"]["mac"] == CLONER2
    assert actual["unknown_drives"] == 3
    assert "roster" not in actual, "רשימת הגל היא לזיכרון בשרת, לא למסך"


def test_a_wave_of_unknown_drives_leaves_todays_cap_untouched(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.sender.max_bitrate = "200m"
    _open_and_join(room_server)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "200m"
    shown = room(admin)["round"]["wave_rate"]
    # בלי גל קודם עם אותם כוננים — קצב הפתיחה, שכאן הוא התקרה של היום
    assert shown["basis"] == "start" and shown["by"] is None
    assert shown["unknown_drives"] == 4


def test_with_learning_off_the_slow_drive_does_not_matter(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.sender.max_bitrate = "200m"
    _history(ctx, "S3", 15 * MB)
    assert admin.post("/api/console/settings",
                      json={"learned_rate_enabled": False}).status_code == 200
    _open_and_join(room_server)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "200m"
    assert room(admin)["round"]["wave_rate"]["basis"] == "disabled"


def test_a_finished_wave_teaches_the_next_one(room_server):
    """הזיכרון נכתב מדיווחי ההתקדמות של גל אמיתי: 15GB ב-1000 שניות תחת
    200m (25MB/s) — הכונן הגביל, והקצב שלו נשמר לפי דגם וסידורי."""
    ctx, admin, anon = room_server["ctx"], room_server["admin"], room_server["anon"]
    ctx.sender.max_bitrate = "200m"
    wave = _open_and_join(room_server)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "200m"

    def report(mac, state, written):
        assert anon.post("/api/v1/agent/progress", json={
            "session_id": wave, "mac": mac, "state": state,
            "targets": [{"dev": dev, "bytes_written": written, "bytes_total": 15_000 * MB,
                         "state": "done" if state == "done" else "writing"}
                        for dev in ("sda", "sdb")],
        }).status_code == 200

    for mac in (CLONER1, CLONER2):
        report(mac, "writing", 1000)
    ago = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat(timespec="seconds")
    for row in ctx.conn.execute("SELECT mac, targets_json FROM session_members"
                                " WHERE session_id = ?", (wave,)).fetchall():
        targets = [{**t, "first_moved_at": ago} for t in json.loads(row["targets_json"])]
        ctx.conn.execute("UPDATE session_members SET targets_json = ? WHERE session_id = ?"
                         " AND mac = ?", (json.dumps(targets), wave, row["mac"]))
    ctx.conn.commit()
    for mac in (CLONER1, CLONER2):
        report(mac, "done", 15_000 * MB)
    heartbeat(room_server)

    rows = ctx.conn.execute("SELECT serial, model, rate_bps, cap_bps FROM drive_rates"
                            " ORDER BY serial").fetchall()
    assert [r["serial"] for r in rows] == ["S1", "S2", "S3", "S4"]
    assert all(r["model"] == "Drawer SSD" and r["cap_bps"] == 25 * MB for r in rows)
    assert all(14 * MB <= r["rate_bps"] <= 15 * MB for r in rows), [r["rate_bps"] for r in rows]


# --- קצב אוטומטי (הכרעת נדב 11/10): "השרת לבד מחליט" ----------------------------


def test_without_a_configured_cap_the_wave_goes_out_at_600m(room_server):
    """שרת מה-ISO אינו מגדיר `IMAGESRV_MAX_BITRATE`. עד ההכרעה udp-sender
    יצא בלי `--max-bitrate` כלל; מעכשיו — בקצב הפתיחה."""
    admin = room_server["admin"]
    assert room_server["ctx"].sender.max_bitrate is None
    _open_and_join(room_server)
    assert room(admin)["round"]["wave_rate"]["max_bitrate"] == "600m"
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "600m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["basis"] == "start" and shown["ceiling"] == "900m"


def test_the_end_of_the_stream_is_remembered_for_the_next_wave(room_server):
    """מה ש-udp-sender ספר בסוף הגל נשמר ליד הקצב שנבחר לו — מזה הגל הבא
    יודע אם לעלות. בלי זה אין "גל נקי", והקצב לעולם אינו עולה."""
    from test_sender_stream_stats import line                  # noqa: PLC0415
    ctx, admin, recorder = room_server["ctx"], room_server["admin"], room_server["recorder"]
    recorder.log_text = "Starting transfer: f\n" + line(64_000, 0)
    wave = _open_and_join(room_server)
    assert admin.post("/api/console/room/start").status_code == 200
    assert wait_for(lambda: ctx.sender.status()["state"] == "done")

    def stream():
        row = ctx.conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?",
                               (wave,)).fetchone()
        return json.loads(row["stream"]) if row and row["stream"] else None

    assert wait_for(lambda: stream() is not None)
    # ‏udp-sender המזויף מסיים מיד — המונה לא זז בין שתי דגימות, ולכן קצב
    # הזרם "לא נמדד" (None), לא אפס.
    # ‏#1758: ‏`ended` — איך השולח סיים; ‏`wall_s` — משך הזרם בשעון, וכאן
    # ‏udp-sender המזויף מסיים לפני דגימה שזזה, ולכן גם הוא "לא נמדד".
    # ‏#1761: יומן השולח נקרא ואין בו `Dropping client` / ‏`Timeout notAnswered`
    # — אפס שנקרא, לא None.
    got = stream()
    assert got == {"sent_bytes": 64_000 * len(recorder.commands), "loss_blocks": 0,
                   "stream_bps": None, "stream_s": None, "ended": "send_done",
                   "wall_s": got["wall_s"], "dropped_clients": 0, "timeouts": 0}
    assert got["wall_s"] is None or got["wall_s"] < 5


def test_a_clean_wave_with_the_same_drives_raises_the_next_one(room_server):
    """הגל הקודם עם אותם ארבעה כוננים יצא ב-600m, udp-sender ספר אפס שידורים
    חוזרים וכל כונן עמד בקצב — הגל הזה יוצא ב-700m, ומסך החדר אומר למה."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    roster = [{"mac": mac, "serial": s, "model": "Drawer SSD"}
              for mac, s in ((CLONER1, "S1"), (CLONER1, "S2"), (CLONER2, "S3"),
                             (CLONER2, "S4"))]
    ctx.conn.execute(
        "INSERT INTO wave_rates (session_id, decision, at, stream, drops)"
        " VALUES (?,?,?,?,?)",
        ("ses_before", json.dumps({"max_bitrate": "600m", "roster": roster}),
         "2026-10-11T09:00:00+00:00",
         json.dumps({"sent_bytes": 10 ** 10, "loss_blocks": 0,
                     "stream_bps": 71 * MB, "stream_s": 140.8}),   # ‏95% מ-600m
         _clean_receivers()))
    for d in roster:
        ctx.conn.execute(
            "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
            " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
            (d["serial"], "Drawer SSD", "ses_before", 75 * MB, 75 * MB, 75 * MB * 1000,
             1000, "2026-10-11T09:20:00+00:00"))
    ctx.conn.commit()
    _open_and_join(room_server)
    assert room(admin)["round"]["wave_rate"]["max_bitrate"] == "700m"
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "700m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["basis"] == "up"
    assert shown["previous"]["session_id"] == "ses_before"


def test_new_drawers_in_the_same_cloners_raise_the_next_wave(room_server):
    """#1471 (הכרעת נדב 16/10) קצה-לקצה: הגל הקודם יצא ב-600m לאותם שני מחשבי
    שיכפול עם **מגירות אחרות** (O1–O4), נקי. הזיכרון הוא של המכונות — הגל הזה
    (S1–S4) יוצא ב-700m. עד היום: מגירה שלא הייתה בגל הקודם = 600m."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    roster = [{"mac": mac, "serial": s, "model": "Drawer SSD"}
              for mac, s in ((CLONER1, "O1"), (CLONER1, "O2"), (CLONER2, "O3"),
                             (CLONER2, "O4"))]
    ctx.conn.execute(
        "INSERT INTO wave_rates (session_id, decision, at, stream, drops)"
        " VALUES (?,?,?,?,?)",
        ("ses_before", json.dumps({"max_bitrate": "600m", "roster": roster}),
         "2026-10-16T09:00:00+00:00",
         json.dumps({"sent_bytes": 10 ** 10, "loss_blocks": 0,
                     "stream_bps": 71 * MB, "stream_s": 140.8}),
         _clean_receivers()))
    for d in roster:
        ctx.conn.execute(
            "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
            " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
            (d["serial"], "Drawer SSD", "ses_before", 75 * MB, 75 * MB, 75 * MB * 1000,
             1000, "2026-10-16T09:20:00+00:00"))
    ctx.conn.commit()
    _open_and_join(room_server)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "700m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["basis"] == "up"
    assert shown["previous"]["session_id"] == "ses_before"
    assert shown["previous"]["mac"] in (CLONER1, CLONER2)
