"""#3532: השרת מעביר ל-`chunkcast send` את `--lead-ms` ו-`--tail-ms`, בכל מעבר, ובערכים מוגדרים.

עד כאן `_command` לא העביר אותם, ולכן כל מעבר ישן 1.2 שניות לפני הנתונים ו-2.1 שניות אחריהם (ברירות המחדל של
הבינארי, 1000/2000): 3.3 שניות. ברירות המחדל של השרת: 400/300 (`chunksend.DEFAULT_LEAD_MS`, `DEFAULT_TAIL_MS`) --
ההנמקה במספרים בהערה שלהן, והמחיר/הבטיחות מול הבינארי האמיתי ב-`test_chunkcast_say_3532.py`.

סדר הקדימות: משתנה הסביבה (כמו שהוא, גם שגוי -- השולח מסרב) > ההגדרה `chunk_lead_ms` / `chunk_tail_ms` > הקבועים.

**בקרה שלילית:** ``server/rounds/chunksend.py`` ו-``server/app.py`` חוזרים לבסיס: אין `say_ms`, ואין `--lead-ms` בפקודה
(כל הטסטים כאן נופלים).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server.db import connect, set_setting
from server.rounds import chunksend
from server.rounds.chunksend import ChunkEngine

from test_chunksend_2533 import (        # noqa: F401 -- rig הוא fixture
    ALL, _index_the_image, _room_wave, rig, wait_for,
)
from test_server_room import CLONER1, room_server  # noqa: F401 -- room_server הוא fixture
from conftest import MANIFEST_256

#: השמות המתועדים (interfaces.md). נכתבים כאן ולא מיובאים: בבסיס אין קבועים, והטסט חייב ליפול על ה-assert ולא ב-ImportError.
LEAD_ENV, TAIL_ENV = "IMAGESRV_CHUNK_LEAD_MS", "IMAGESRV_CHUNK_TAIL_MS"
LEAD_KEY, TAIL_KEY = "chunk_lead_ms", "chunk_tail_ms"


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv(LEAD_ENV, raising=False)
    monkeypatch.delenv(TAIL_ENV, raising=False)


def flags(cmd):
    assert "--lead-ms" in cmd and "--tail-ms" in cmd, cmd
    assert cmd.count("--lead-ms") == 1 and cmd.count("--tail-ms") == 1, cmd
    return cmd[cmd.index("--lead-ms") + 1], cmd[cmd.index("--tail-ms") + 1]


def command(chunks=None, say=None):
    """The command, and the list file of a completion pass removed (the engine removes it after the pass)."""
    engine = SimpleNamespace(port=7713, interface=None, feedback=True, write_floor=lambda: 20)   # פורט-הפצה-מכוון
    wave = SimpleNamespace(key="0123456789abcdef", round=1)
    cmd = ChunkEngine._command(engine, wave, {"index": 3}, Path("image.zst"), 100000, 400000, chunks, None, say)
    if "--chunks-file" in cmd:
        os.unlink(cmd[cmd.index("--chunks-file") + 1])
    return cmd


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "say.db")
    yield conn
    conn.close()


# --- הפקודה ----------------------------------------------------------------------------------------------------------

def test_the_command_carries_the_flags_in_a_first_pass_and_in_a_completion_pass():
    assert flags(command(say=("400", "300"))) == ("400", "300")
    assert flags(command(chunks=[(5, 6)], say=("400", "300"))) == ("400", "300")      # --chunks-file: the completion pass


def test_a_caller_that_does_not_ask_leaves_the_senders_own_values():
    assert "--lead-ms" not in command() and "--tail-ms" not in command()


# --- הערכים ----------------------------------------------------------------------------------------------------------

def test_the_defaults_are_the_documented_numbers():
    assert (chunksend.DEFAULT_LEAD_MS, chunksend.DEFAULT_TAIL_MS) == (400, 300)
    assert chunksend.say_ms() == ("400", "300")


def test_the_defaults_cost_one_second_a_pass_against_three_and_a_third():
    """The sender sleeps its gap (200ms before the data, 100ms after) after EVERY send, the last one too."""
    def cost(lead, tail):
        return (lead // 200 + 1) * 200 + (tail // 100 + 1) * 100
    assert cost(1000, 2000) == 3300
    assert cost(chunksend.DEFAULT_LEAD_MS, chunksend.DEFAULT_TAIL_MS) == 1000


def test_a_setting_changes_the_value(db):
    set_setting(db, LEAD_KEY, "250")
    assert chunksend.say_ms(db) == ("250", "300")
    set_setting(db, TAIL_KEY, "0")
    assert chunksend.say_ms(db) == ("250", "0")


@pytest.mark.parametrize("bad", ["", "abc", "-1", "007", "1.5", " 5", "10001", "٣", "1e3"])
def test_a_damaged_setting_is_the_default_and_a_line_in_the_log(db, caplog, bad):
    set_setting(db, TAIL_KEY, bad)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        assert chunksend.say_ms(db) == ("400", "300")
    assert TAIL_KEY in caplog.text, caplog.text


def test_the_environment_wins_over_the_setting_and_goes_as_it_is(db, monkeypatch):
    set_setting(db, LEAD_KEY, "250")
    monkeypatch.setenv(LEAD_ENV, "200")
    assert chunksend.say_ms(db) == ("200", "300")
    for value in ("", "abc", "0200"):           # not made right: the sender refuses it (exit 2) and the wave fails by the value
        monkeypatch.setenv(TAIL_ENV, value)
        assert chunksend.say_ms(db)[1] == value


def test_a_database_that_cannot_be_read_is_the_defaults_and_not_a_failed_wave(caplog):
    class Broken:
        def execute(self, *args):
            raise RuntimeError("database is locked")
    with caplog.at_level(logging.ERROR, logger="imagesrv.chunksend"):
        assert chunksend.say_ms(Broken()) == ("400", "300")
    assert "database is locked" in caplog.text


# --- המנוע: כל מעבר, גם ההשלמה ---------------------------------------------------------------------------------------

def test_every_pass_of_a_wave_carries_the_flags(rig):
    engine = rig.make()
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.tell("a", 1, "ended", {"A1": ALL})["action"] == "wait"
    assert rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})["action"] == "wait"
    assert rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]})["action"] == "receive"
    assert rig.ask("b", 1, {"B1": [[0, 4], [7, 9]]})["action"] == "receive"
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    first, second = rig.runner.commands
    assert rig.runner.lists == [None, "5-6"]                                # the second is the completion pass
    assert flags(first) == ("400", "300") and flags(second) == ("400", "300")
    assert engine.say() == ("400", "300")


def test_the_engine_asks_for_the_values_at_every_pass(rig):
    """A value changed between the passes of a wave is the value of the next one: nothing is remembered."""
    values = iter([("250", "120"), ("300", "200"), ("300", "200")])
    engine = rig.make()
    engine.say = lambda: next(values)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})
    rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]})
    rig.ask("b", 1, {"B1": [[0, 4], [7, 9]]})
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    assert [flags(c) for c in rig.runner.commands] == [("250", "120"), ("300", "200")]


# --- מקצה לקצה דרך האפליקציה: ההגדרה בבסיס הנתונים היא מה שהשולח מקבל -----------------------------------------------

def _first_command(room_server):
    ctx, recorder, anon = room_server["ctx"], room_server["recorder"], room_server["anon"]
    _index_the_image(ctx)
    wave, _hello = _room_wave(room_server)
    first = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    answer = anon.post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": first, "pass": 1, "state": "listening",
        "targets": [{"serial": "S1", "write_mbps": 150}, {"serial": "S2", "write_mbps": 150}]})
    assert answer.status_code == 200, answer.text
    assert recorder.spawned.wait(timeout=5)
    ctx.chunk.stop(wave)
    return recorder.commands[0]


def test_through_the_app_the_defaults_reach_the_sender(room_server):
    assert flags(_first_command(room_server)) == ("400", "300")


def test_through_the_app_a_setting_in_the_database_reaches_the_sender(room_server):
    set_setting(room_server["ctx"].conn, LEAD_KEY, "250")
    set_setting(room_server["ctx"].conn, TAIL_KEY, "120")
    assert flags(_first_command(room_server)) == ("250", "120")
