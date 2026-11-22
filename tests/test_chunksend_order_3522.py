"""#3522: ``--order`` של השולח עובר מהשרת כמו שהוא, ושורות הניסוי של השולח אינן סיבת כישלון.

``IMAGESRV_CHUNK_ORDER`` (``chunksend.CHUNK_ORDER_ENV``) עובר **כמו שהוא**, גם ערך שגוי -- השולח הוא שמסרב (יציאה 2) והגל נכשל
בשם הערך, ולא נפילה שקטה ל-``seq``. מי שלא ביקש סדר (``_command`` בלי ``order``) משאיר לשולח את שלו. ברירת המחדל של השרת היא
``mixed`` (ההגדרה ``chunk_order``, ``test_chunk_order_default_3522.py``). השורות ``knobs ...`` ו-``sendstats ...`` של
``chunkcast send`` הן התקדמות כמו ``paced``: כשהשולח נכשל, הסיבה שהקונסולה מראה היא משפט של השולח, ולא שורת ניסוי
(``chunklog._PROGRESS``).

**בקרה שלילית:** ``server/rounds/chunksend.py`` ו-``server/rounds/chunklog.py`` חוזרים לבסיס: אין ``chunk_order``
והפקודה אינה נושאת ``--order`` שהתבקש (הטסטים נופלים על ה-assert), ו-``log_tail`` מחזיר את שורת ה-``knobs`` או ה-``sendstats``
במקום המשפט של השולח.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from server.rounds import chunklog, chunksend
from server.rounds.chunksend import ChunkEngine

#: השם המתועד (interfaces.md). נכתב כאן ולא מיובא: בבסיס אין קבוע, והטסט חייב ליפול בהקשחה ולא ב-ImportError.
CHUNK_ORDER_ENV = "IMAGESRV_CHUNK_ORDER"

KNOBS = "knobs ahead=4 readahead=legacy order=seq fb_low=2 fb_high=6 qcap=8"
STATS = ("sendstats read_ms=12 lag_forgiven_wire=0 lag_forgiven_disk=3 forgiven_ms_wire=0 forgiven_ms_disk=41 "
         "enobufs=0 eagain=1 eintr=0 refused=0")


def _cmd(chunks=None, order=None):
    engine = SimpleNamespace(port=7713, interface=None, feedback=True, write_floor=lambda: 20)   # פורט-הפצה-מכוון
    wave = SimpleNamespace(key="0123456789abcdef", round=1)
    return ChunkEngine._command(engine, wave, {"index": 3}, Path("image.zst"), 100000, 400000, chunks, None, None, order)


def test_the_command_has_no_order_when_the_caller_does_not_ask(monkeypatch):
    monkeypatch.delenv(CHUNK_ORDER_ENV, raising=False)
    assert "--order" not in _cmd()


@pytest.mark.parametrize("value", ["mixed", "seq", "mixd", ""])
def test_the_value_goes_to_the_sender_as_it_is_and_the_sender_decides(monkeypatch, value):
    monkeypatch.setenv(CHUNK_ORDER_ENV, value)
    assert chunksend.chunk_order() == value
    cmd = _cmd(order=chunksend.chunk_order())
    assert "--order" in cmd, cmd
    assert cmd[cmd.index("--order") + 1] == value
    assert cmd.count("--order") == 1


def test_the_environment_variable_is_the_documented_name():
    assert getattr(chunksend, "CHUNK_ORDER_ENV", None) == CHUNK_ORDER_ENV


@pytest.mark.parametrize("line", [KNOBS, STATS, KNOBS.replace("legacy", "on").replace("seq", "mixed")])
def test_the_experiment_lines_are_progress_and_not_what_the_sender_said(tmp_path, line):
    path = tmp_path / "send.log"
    path.write_text(f"{line}\nsent 3 900\npaced 10 20\nchunkcast: send: the file ended inside chunk 4\n{STATS}\n"
                    f"paced 12 25\n", encoding="utf-8")
    assert chunklog.log_tail(path) == "chunkcast: send: the file ended inside chunk 4"


def test_a_sender_that_only_said_its_knobs_failed_with_nothing_to_show(tmp_path):
    path = tmp_path / "send.log"
    path.write_text(f"{KNOBS}\n", encoding="utf-8")
    assert chunklog.log_tail(path) == ""


def test_a_damaged_experiment_line_is_still_a_sentence_of_the_sender(tmp_path):
    """The two lines are progress only when whole: a half line is the one thing the sender may have said last."""
    path = tmp_path / "send.log"
    path.write_text(f"{KNOBS}\nknobs ahead=4 readahead=legacy order=bogus fb_low=2 fb_high=6 qcap=8\n", encoding="utf-8")
    assert chunklog.log_tail(path).startswith("knobs ahead=4 readahead=legacy order=bogus")
