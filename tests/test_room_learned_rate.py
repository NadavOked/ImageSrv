"""#447 קצה-לקצה: הכוננים שבגל קובעים את קצב הגל, והמפעיל רואה למה.

הבקרה השלילית של ה-Issue, מול ה-API האמיתי: כונן עם היסטוריה של 15MB/s
מצטרף לגל. **היום** הגל יוצא בתקרה הגלובלית (200m) והמגירה הזו לא עומדת
בה. **אחרי** — `chunkcast send` מקבל 108m (`--kbps`), ומסך החדר אומר "נקבע לפי" אותו כונן.
והצד השני: גל בלי היסטוריה יוצא בדיוק בתקרה של היום — שום דבר לא זז.

#3173: עד המחיקה של udpcast הקצב נקרא מ-`--max-bitrate` של udp-sender, וכאן היו גם
הטענות של הלמידה ממה ש-udp-sender ספר בסוף הגל. הן הלכו איתו; הלמידה של גל החתיכות
נבדקת ב-`test_chunk_rate_engine_3163` וב-`test_drive_rate_stream_engine_3296`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256  # noqa: E402
from fakesend import Cloners, arg, wait_for  # noqa: E402
from test_server_room import (  # noqa: E402,F401
    CLONER1, CLONER2, cloner_hello, room, room_body, room_server,
)

MB = 1_000_000
#: the drawers `_open_and_join` puts in the wave
DRAWERS = {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}
FIRST_PART = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))


def _history(ctx, serial, rate_bps, cap_bps=25 * MB, model="Drawer SSD"):
    """שני גלים קודמים שבהם הכונן הגביל — `MIN_HELD_SAMPLES`."""
    for n in range(2):
        ctx.conn.execute(
            "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
            " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
            (serial, model, f"ses_old{n}_{serial}", rate_bps, cap_bps, rate_bps * 1000,
             1000, "2026-10-01T10:00:00+00:00"))
    ctx.conn.commit()


def _open_and_join(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    # יעד 5 מול ארבע מגירות: הגל נשאר `open` (מוצג `projected`) עד ההתחלה
    # הידנית. #1703: hello שמכסה את היעד מוציא את הגל בעצמו.
    assert admin.post("/api/console/room", json=room_body(room_server, 5, DRAWERS)).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    return wave


def _sent_bitrate(room_server, write_mbps: int | None = 150):
    """הקצב שהגל יצא בו: `--kbps` של `chunkcast send` הראשון, בצורה של `drive_rates` ("108m").

    מנוע החתיכות אינו משדר עד שכל משכפל בגל אמר `listening` (`fakesend.Cloners`); `write_mbps`
    הוא בדיקת הכתיבה שהמגירות מדווחות איתו (None = לא דיווחו)."""
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    assert wait_for(lambda: ctx.chunk.status() is not None), "מנוע החתיכות לא קיבל את הגל"
    wave = ctx.chunk.status()["session_id"]
    Cloners(room_server["anon"], wave, DRAWERS, write_mbps=write_mbps).listen(FIRST_PART)
    assert wait_for(lambda: recorder.commands), "chunkcast send לא הופעל"
    kbps = int(arg(recorder.commands[0], "--kbps"))
    return f"{kbps // 1000}m"


def test_a_known_slow_drive_sets_the_waves_bitrate_and_is_named(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.chunk.max_bitrate = "200m"
    _history(ctx, "S3", 15 * MB)
    _open_and_join(room_server)

    projected = room(admin)["round"]["wave_rate"]
    assert projected["projected"] is True
    assert projected["max_bitrate"] == "108m"
    assert projected["by"]["serial"] == "S3"

    assert admin.post("/api/console/room/start").status_code == 200
    # #3269: בדיקת כתיבה של הגל מחליפה זיכרון ישן (`chunk_rate.start`; נבדק ב-
    # `test_chunk_rate_engine_3163::test_a_stale_learned_network_rate_is_not_lifted_by_the_write_check_of_the_wave`).
    # כאן המגירות אינן מדווחות בדיקה, והזיכרון של הכונן הוא שקובע את הקצב.
    assert _sent_bitrate(room_server, write_mbps=None) == "108m"

    actual = room(admin)["round"]["wave_rate"]
    assert actual["projected"] is False
    assert actual["basis"] == "learned" and actual["max_bitrate"] == "108m"
    assert actual["by"]["serial"] == "S3" and actual["by"]["mac"] == CLONER2
    assert actual["unknown_drives"] == 3
    assert "roster" not in actual, "רשימת הגל היא לזיכרון בשרת, לא למסך"


def test_a_wave_of_unknown_drives_leaves_todays_cap_untouched(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.chunk.max_bitrate = "200m"
    _open_and_join(room_server)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "200m"
    shown = room(admin)["round"]["wave_rate"]
    # בלי גל קודם עם אותם כוננים — קצב הפתיחה, שכאן הוא התקרה של היום
    assert shown["basis"] == "start" and shown["by"] is None
    assert shown["unknown_drives"] == 4


def test_with_learning_off_the_slow_drive_does_not_matter(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.chunk.max_bitrate = "200m"
    _history(ctx, "S3", 15 * MB)
    assert admin.post("/api/console/settings",
                      json={"learned_rate_enabled": False}).status_code == 200
    _open_and_join(room_server)
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "200m"
    assert room(admin)["round"]["wave_rate"]["basis"] == "disabled"


# --- קצב אוטומטי (הכרעת נדב 11/10): "השרת לבד מחליט" ----------------------------


def test_without_a_configured_cap_the_wave_goes_out_at_600m(room_server):
    """שרת מה-ISO אינו מגדיר `IMAGESRV_MAX_BITRATE`. עד ההכרעה udp-sender
    יצא בלי `--max-bitrate` כלל; מעכשיו — בקצב הפתיחה."""
    admin = room_server["admin"]
    assert room_server["ctx"].chunk.max_bitrate is None
    _open_and_join(room_server)
    assert room(admin)["round"]["wave_rate"]["max_bitrate"] == "600m"
    assert admin.post("/api/console/room/start").status_code == 200
    assert _sent_bitrate(room_server) == "600m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["basis"] == "start" and shown["ceiling"] == "900m"
