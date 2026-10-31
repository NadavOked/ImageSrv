"""‏#989 (1): קצב ואיבוד של הגל — ממה ש-`udp-sender` עצמו מדפיס.

הצורה של שורת ההתקדמות נלקחה מקוד המקור של udpcast (‏`statistics.c`
‏`displaySenderStats`, ‏`log.c` ‏`printLongNum`; עותק ב-github
‏elisescu/udpcast, נקרא 24/09):

    bytes=<printLongNum> re-xmits=%07llu (%3u.%01u%%) slice=%04d - %3d\\r

ושורה כזו נכתבת לכל היותר פעם ב-`DEFLT_STAT_PERIOD` (‏500ms) לתוך
הלוג הפר-מחיצתי (‏`run_process` מפנה אליו את ה-stderr). ⚠️ **לא נמדד
על ברזל** — אין בריפו לוג אמיתי של udp-sender בזמן גל. הצורה כאן היא
של הקוד, לא של מדידה; מה שלא מתאים לה הוא `None`, לא אפס.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.rounds import sender as sender_module
from server.library.images import ImageLibrary
from server.rounds.sender import SenderEngine

from conftest import MANIFEST_256, write_image
from test_sender import Recorder, wait_for
from test_server_room import CLONER1, cloner_hello, room, room_body, room_server  # noqa: F401


def long_num(x: int) -> str:
    """‏`printLongNum` של udpcast (‏log.c), שורה-בשורה — כדי שהטסט יבנה
    את השורה כפי ש-udp-sender כותב אותה, כולל הרווחים והסיומת."""
    if x > 1_000_000_000_000:
        min_div, suffix = 1_048_576, "M"
    elif x >= 1_000_000_000:
        min_div, suffix = 1024, "K"
    else:
        min_div, suffix = 1, " "
    divisor, nonzero, out = min_div * 1_000_000, False, ""
    while divisor >= min_div:
        digits = (x // divisor) % 1000
        if digits or nonzero:
            out += f"{digits:03d}" if nonzero else f"{digits:3d}"
        else:
            out += "    "
        if digits:
            nonzero = True
        divisor //= 1000
        out += " " if divisor >= min_div else suffix
    return out


def line(total: int, rexmits: int, clients: int = 2) -> str:
    return f"bytes={long_num(total)} re-xmits={rexmits:07d} (  0.0%) slice=0112 - {clients:3d}\r"


def _counts(outcome: dict) -> dict:
    """שני המונים מתוך `wave_outcome` (קצב הזרם נבדק בטסטים משלו)."""
    return {k: outcome[k] for k in ("sent_bytes", "loss_blocks")}


def write_log(path: Path, text: str, mtime: float | None = None) -> None:
    path.write_text(text, newline="")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


# --- הפענוח -----------------------------------------------------------------


@pytest.mark.parametrize("total", [0, 14, 50_954_014, 999_999_999,
                                   10_737_418_240, 67_000_000_000,
                                   2_000_000_000_000])
def test_the_progress_line_is_read_back_as_printed(tmp_path, total):
    log = tmp_path / "s.log"
    write_log(log, "Starting transfer: file[x]\n" + line(total, 12))
    got_bytes, got_rexmits = sender_module.sender_progress(log)
    # מעל 10⁹ udp-sender מדפיס ביחידות K (ומעל 10¹² ב-M) — הערך חוזר
    # מעוגל למטה ליחידה, לא ממוצא.
    unit = 1_048_576 if total > 1_000_000_000_000 else 1024 if total >= 1_000_000_000 else 1
    assert got_bytes == total // unit * unit
    assert got_rexmits == 12


def test_the_last_line_wins_and_a_half_written_one_is_ignored(tmp_path):
    log = tmp_path / "s.log"
    write_log(log, "Starting transfer: f\n" + line(1000, 1) + line(5000, 3) + "bytes=  12 3")
    assert sender_module.sender_progress(log) == (5000, 3)


@pytest.mark.parametrize("text", ["", "Starting transfer: f\n",
                                  "bytes=12 xmits=4\r"])
def test_no_progress_line_is_none_not_zero(tmp_path, text):
    log = tmp_path / "s.log"
    write_log(log, text)
    assert sender_module.sender_progress(log) is None


def test_an_unreadable_log_is_none(tmp_path):
    assert sender_module.sender_progress(tmp_path / "missing.log") is None


# --- המנוע ------------------------------------------------------------------


@pytest.fixture()
def engine_env(tmp_path, monkeypatch):
    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    monkeypatch.setattr(sender_module, "SENDER_LOG", tmp_path / "logs" / "sender.log")
    (tmp_path / "logs").mkdir()
    write_image(tmp_path / "images", MANIFEST_256)
    recorder = Recorder(block=True)
    engine = SenderEngine(ImageLibrary(tmp_path / "images"), runner=recorder)
    yield engine, recorder
    engine.stop()


def current_log(recorder: Recorder, index: int = -1) -> Path:
    cmd = recorder.commands[index]
    return sender_module.partition_log(cmd[cmd.index("--file") + 1])


def test_nothing_is_measured_for_a_wave_the_engine_is_not_sending(engine_env):
    engine, _ = engine_env
    assert engine.stream_stats("ses_1") == {"throughput_bps": None, "loss_blocks": None}
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "sending")
    assert engine.stream_stats("ses_other") == {"throughput_bps": None, "loss_blocks": None}


def test_the_rate_is_bytes_between_two_printed_lines_over_their_time(engine_env):
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "sending")
    log, now = current_log(recorder), time.time()
    write_log(log, "Starting transfer: f\n" + line(100_000_000, 4), mtime=now - 2)
    first = engine.stream_stats("ses_1")
    assert first == {"throughput_bps": None, "loss_blocks": 4}, "דגימה אחת אינה קצב"
    write_log(log, "Starting transfer: f\n" + line(100_000_000, 4) + line(340_000_000, 9),
              mtime=now)
    assert engine.stream_stats("ses_1") == {"throughput_bps": 120_000_000, "loss_blocks": 9}
    # בלי שורה חדשה (אותו mtime) הקצב האחרון נשאר — הדגימה לא נמדדה מחדש
    assert engine.stream_stats("ses_1")["throughput_bps"] == 120_000_000


def test_a_log_that_stopped_printing_has_no_rate(engine_env):
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "sending")
    log, old = current_log(recorder), time.time() - 60
    write_log(log, "Starting transfer: f\n" + line(1_000, 0), mtime=old - 2)
    engine.stream_stats("ses_1")
    write_log(log, "Starting transfer: f\n" + line(1_000, 0) + line(9_000, 0), mtime=old)
    assert engine.stream_stats("ses_1")["throughput_bps"] is None, \
        "דקה בלי שורה — לא 'כך הזרם עכשיו'"


def test_loss_is_summed_over_the_partitions_of_the_wave(engine_env):
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(5_000, 7))
    recorder.processes[0].release()
    assert wait_for(lambda: len(recorder.processes) == 2)
    # המחיצה השנייה עוד ממתינה למקבלים: קראנו אותה ואין בה שידור — אפס שם
    write_log(current_log(recorder), "Udp-sender 20120424\n")
    assert engine.stream_stats("ses_1")["loss_blocks"] == 7
    write_log(current_log(recorder), "Starting transfer: f\n" + line(9_000, 5))
    assert engine.stream_stats("ses_1")["loss_blocks"] == 12


def test_a_started_partition_without_a_readable_line_makes_loss_unknown(engine_env):
    """שידור שהתחיל ואין בו שורה שאנחנו יודעים לקרוא — ‏udpcast אחר, או
    לוג שנמחק — אינו "אפס שידורים חוזרים"."""
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "sending")
    write_log(current_log(recorder), "Starting transfer: f\nsomething else\r")
    assert engine.stream_stats("ses_1")["loss_blocks"] is None
    current_log(recorder).unlink()
    assert engine.stream_stats("ses_1")["loss_blocks"] is None


def test_a_simulated_sender_measures_nothing(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    engine = SenderEngine(ImageLibrary(tmp_path), simulate=True)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    try:
        assert wait_for(lambda: engine.status()["state"] == "sending")
        assert engine.stream_stats("ses_1") == {"throughput_bps": None, "loss_blocks": None}
        assert _counts(engine.wave_outcome("ses_1")) == {"sent_bytes": None, "loss_blocks": None}
    finally:
        engine.stop()


# --- סוף הגל: מה שהקצב האוטומטי לומד ממנו ---------------------------------------


def test_the_wave_outcome_sums_bytes_and_resends_over_the_partitions(engine_env):
    """הקצב האוטומטי (הכרעת נדב 11/10) עולה רק על אפס שידורים חוזרים —
    ולכן צריך את שני המונים של **כל** הגל, לא של המחיצה האחרונה."""
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(5_000, 7))
    recorder.processes[0].release()
    assert wait_for(lambda: len(recorder.processes) == 2)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(9_000, 0))
    assert _counts(engine.wave_outcome("ses_1")) == {"sent_bytes": 14_000, "loss_blocks": 7}
    assert _counts(engine.wave_outcome("ses_other")) == {"sent_bytes": None, "loss_blocks": None}


def test_the_stream_rate_is_the_bytes_moved_over_the_time_they_moved(engine_env, monkeypatch):
    """התנאי השני לעלייה (המתאם, 11/10): הקצב בקו, מהמונה של udp-sender עצמו.
    השולח דוגם אותו בזמן שהוא ממתין לתהליך — ההמתנה למקבלים (מונה 0)
    אינה זרם, ולכן הספירה מתחילה בדגימה הראשונה שמעל אפס."""
    monkeypatch.setattr(sender_module, "PACE_POLL", 0.05)
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(0, 0))
    time.sleep(0.3)                                  # ממתין למקבלים — לא נספר
    write_log(current_log(recorder), "Starting transfer: f\n" + line(1_000_000, 0))
    time.sleep(0.5)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(6_000_000, 0))
    time.sleep(0.2)
    recorder.processes[0].release()
    assert wait_for(lambda: len(recorder.processes) == 2)
    got = engine.wave_outcome("ses_1")
    assert got["stream_s"] is not None and 0.3 <= got["stream_s"] <= 1.5, got
    # ‏5MB זזו (מ-1MB ל-6MB); ‏`stream_s` מעוגל לעשירית, ולכן בקירוב
    assert abs(got["stream_bps"] * got["stream_s"] - 5_000_000) < 1_000_000, got


def test_a_partition_that_never_moved_between_samples_has_no_stream_rate(engine_env):
    """מונה שנראה פעם אחת בלבד — אין שני זמנים, אין קצב. ‏None, לא אפס."""
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(5_000, 0))
    recorder.processes[0].release()
    assert wait_for(lambda: len(recorder.processes) == 2)
    got = engine.wave_outcome("ses_1")
    assert got["stream_bps"] is None and got["stream_s"] is None


def test_a_started_partition_without_a_line_makes_the_outcome_unknown(engine_env):
    """עיקרון 5: מחיצה ששודרה ואין לה מונה — כל הגל "לא נמדד", לא אפס."""
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "sending")
    write_log(current_log(recorder), "Starting transfer: f\nsomething else\r")
    assert _counts(engine.wave_outcome("ses_1")) == {"sent_bytes": None, "loss_blocks": None}


# --- ‏GET /api/console/room ----------------------------------------------------


def test_the_room_round_carries_the_stream_numbers(room_server, tmp_path, monkeypatch):
    monkeypatch.setattr(sender_module, "SENDER_LOG", tmp_path / "logs" / "sender.log")
    (tmp_path / "logs").mkdir()
    admin, anon, recorder = room_server["admin"], room_server["anon"], room_server["recorder"]
    recorder.block = True
    assert admin.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    rnd = room(admin)["round"]
    assert rnd.get("throughput_bps") is None and rnd.get("loss_blocks") is None, "הגל פתוח — לא משדר"
    # #1703: the hello that covers the target starts the wave in its own answer
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["state"] == "running"
    assert wait_for(lambda: len(recorder.commands) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(64_000, 3))
    rnd = room(admin)["round"]
    assert rnd.get("loss_blocks") == 3
    assert rnd.get("throughput_bps") is None, "דגימה ראשונה"
