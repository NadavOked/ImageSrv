"""#3522: `mixed` הוא סדר השידור שהשרת מבקש מ-`chunkcast send` בכל מעבר, בלי שמישהו הגדיר דבר.

על הברזל (r14, 16/11): סדר הקובץ 464/469 שניות, `mixed` 388/390, udpcast 429. עד כאן השרת העביר `--order` רק כשמשתנה הסביבה
`IMAGESRV_CHUNK_ORDER` היה קיים, ולכן כל גל רגיל יצא בסדר הקובץ. עכשיו: משתנה הסביבה (כמו שהוא, גם שגוי -- השולח מסרב)
> ההגדרה `chunk_order` (`mixed` או `seq`, אינה במסך ההגדרות: ספים טכניים לא נכנסים למסך, הכרעת נדב 14/11) > `mixed`.

מה שנבדק כאן: הערכים, סדר הקדימות, המעבר הראשון וההשלמה, והחיווט דרך האפליקציה. מה שרץ על `chunkcast` האמיתי -- מגירה
שמצטרפת באמצע, השלמה ב-HTTP, שכנה, `peerfill`, גל שלם עם שרת אמיתי -- ב-`test_chunk_order_mixed_native_3522.py`.

**בקרה שלילית:** ``server/rounds/chunksend.py`` ו-``server/app.py`` חוזרים לבסיס: אין `chunk_order` ואין `order` במנוע,
והפקודה אינה נושאת `--order` ללא משתנה הסביבה (כל הטסטים כאן נופלים; ``test_a_caller_that_does_not_ask...`` נופל גם הוא,
כי ב-`_command` של הבסיס הפרמטר אינו קיים).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server.db import connect, set_setting
from server.rounds import chunksend
from server.rounds.chunksend import ChunkEngine
from server.rounds.directchunk import DirectChunks

from test_chunk_say_3532 import _first_command          # noqa: F401 -- the app's first command, as a sender gets it
from test_chunksend_2533 import ALL, rig, wait_for       # noqa: F401 -- rig הוא fixture
from test_server_room import room_server                 # noqa: F401 -- room_server הוא fixture

#: השמות המתועדים (interfaces.md). נכתבים כאן ולא מיובאים: בבסיס אין קבועים, והטסט חייב ליפול על ה-assert ולא ב-ImportError.
ENV, KEY = "IMAGESRV_CHUNK_ORDER", "chunk_order"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "order.db")
    yield conn
    conn.close()


def order_of(cmd):
    assert cmd.count("--order") == 1, cmd
    return cmd[cmd.index("--order") + 1]


def command(chunks=None, order=None):
    """The command, and the list file of a completion pass removed (the engine removes it after the pass)."""
    engine = SimpleNamespace(port=9002, interface=None, feedback=True, write_floor=lambda: 20)
    wave = SimpleNamespace(key="0123456789abcdef", round=1)
    cmd = ChunkEngine._command(engine, wave, {"index": 3}, Path("image.zst"), 100000, 400000, chunks, None, None, order)
    if "--chunks-file" in cmd:
        os.unlink(cmd[cmd.index("--chunks-file") + 1])
    return cmd


# --- הפקודה ----------------------------------------------------------------------------------------------------------

def test_the_command_carries_the_order_in_a_first_pass_and_in_a_completion_pass():
    assert order_of(command(order="mixed")) == "mixed"
    assert order_of(command(chunks=[(5, 6)], order="mixed")) == "mixed"      # --chunks-file: the completion pass
    assert order_of(command(order="seq")) == "seq"


def test_a_caller_that_does_not_ask_leaves_the_senders_own_order():
    assert "--order" not in command()


# --- הערכים ----------------------------------------------------------------------------------------------------------

def test_the_default_is_mixed():
    assert chunksend.chunk_order() == "mixed"
    assert chunksend.DEFAULT_CHUNK_ORDER == "mixed" and chunksend.CHUNK_ORDERS == ("mixed", "seq")


def test_a_database_without_the_setting_is_mixed(db):
    assert chunksend.chunk_order(db) == "mixed"


def test_the_setting_chooses_seq_or_mixed(db):
    set_setting(db, KEY, "seq")
    assert chunksend.chunk_order(db) == "seq"
    set_setting(db, KEY, "mixed")
    assert chunksend.chunk_order(db) == "mixed"


@pytest.mark.parametrize("bad", ["", "abc", "Mixed", "mixed ", "random", "0", "seq,mixed"])
def test_a_damaged_setting_is_the_default_and_a_line_in_the_log(db, caplog, bad):
    set_setting(db, KEY, bad)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        assert chunksend.chunk_order(db) == "mixed"
    assert KEY in caplog.text, caplog.text


def test_the_environment_wins_over_the_setting_and_goes_as_it_is(db, monkeypatch):
    set_setting(db, KEY, "mixed")
    monkeypatch.setenv(ENV, "seq")
    assert chunksend.chunk_order(db) == "seq"
    for value in ("mixd", "", "MIXED"):         # not made right: the sender refuses it (exit 2) and the wave fails by the value
        monkeypatch.setenv(ENV, value)
        assert chunksend.chunk_order(db) == value


def test_a_database_that_cannot_be_read_is_the_default_and_not_a_failed_wave(caplog):
    class Broken:
        def execute(self, *args):
            raise RuntimeError("database is locked")
    with caplog.at_level(logging.ERROR, logger="imagesrv.chunksend"):
        assert chunksend.chunk_order(Broken()) == "mixed"
    assert "database is locked" in caplog.text


def test_the_environment_variable_and_the_setting_are_the_documented_names():
    assert (chunksend.CHUNK_ORDER_ENV, chunksend.CHUNK_ORDER_SETTING) == (ENV, KEY)


# --- המנוע: כל מעבר, גם ההשלמה ---------------------------------------------------------------------------------------

def test_every_pass_of_a_wave_carries_the_order(rig):
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
    assert order_of(first) == "mixed" and order_of(second) == "mixed"
    assert engine.order() == "mixed"


def test_the_engine_asks_for_the_order_at_every_pass(rig):
    """A value changed between the passes of a wave is the value of the next one: nothing is remembered."""
    values = iter(["seq", "mixed", "mixed"])
    engine = rig.make()
    engine.order = lambda: next(values)
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
    assert [order_of(c) for c in rig.runner.commands] == ["seq", "mixed"]


# --- מקצה לקצה דרך האפליקציה: ההגדרה בבסיס הנתונים היא מה שהשולח מקבל -----------------------------------------------

def test_through_the_app_nothing_set_is_mixed(room_server):
    assert order_of(_first_command(room_server)) == "mixed"


def test_through_the_app_a_setting_in_the_database_reaches_the_sender(room_server):
    set_setting(room_server["ctx"].conn, KEY, "seq")
    assert order_of(_first_command(room_server)) == "seq"


def test_through_the_app_the_environment_wins_over_the_setting(room_server, monkeypatch):
    set_setting(room_server["ctx"].conn, KEY, "seq")
    monkeypatch.setenv(ENV, "mixed")
    assert order_of(_first_command(room_server)) == "mixed"


# --- הפצה ישירה: מחשב הבנייה בונה את הפקודה בעצמו ------------------------------------------------------------------------

def test_a_direct_order_is_made_from_the_flags_it_needs_and_the_order_flag_is_not_one_of_them():
    """A direct wave (a build machine sends) goes through the same engine, whose command now carries `--order mixed`. The
    server turns it into an order for the source (`_issue`), which reads seven flags and no more: the order is not passed on."""
    engine = SimpleNamespace(port=9002, interface=None, feedback=True, write_floor=lambda: 20)
    wave = SimpleNamespace(key="0123456789abcdef", round=1)
    cmd = ChunkEngine._command(engine, wave, {"index": 3}, Path("image.zst"), 100000, 400000, [(5, 6)], None, ("400", "300"),
                               "mixed")
    listed = cmd[cmd.index("--chunks-file") + 1]
    owner = SimpleNamespace(_lock=threading.Lock(), _seq=0, _order=None, clock=lambda: 0.0)
    try:
        order = DirectChunks._issue(owner, "image", cmd)
    finally:
        os.unlink(listed)
    assert "--order" in cmd and order.chunks == "5-6"
    assert set(order.public()) == {"seq", "part", "pass", "kbps", "write_kbps", "group", "port", "key", "chunks"}


@pytest.mark.parametrize("script", ["directchunk.sh", "directserve.sh"])
def test_the_build_machines_own_commands_do_not_carry_order(script):
    """The main pass of a build machine reads its stream from stdin: `--order mixed` is refused there (exit 2, the stream is read
    in order), and a repair reads from the disk by `--device` and asks for the sender's own order. Changing either is a separate
    change (and `--device` with `mixed` is not shown to work); this guards that this one did not."""
    text = (REPO / "agent" / "lib" / "capture" / script).read_text(encoding="utf-8")
    assert "chunkcast send" in text and "--order" not in text
