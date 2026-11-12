"""#2704 -- the journal line of a wave that went out with a precheck warning says which warning.

Metal, 08/11, v0.90.0 (journal row 667): "הגל יצא לדרך למרות אזהרת קישור/CRC על מגירה —
room_7f331ae9 wave=1 2 · מחשבי שיכפול:5 learned=slow_write". The link was 3.0/3.0 Gb/s and
CRC 0; the only reason was a slow write check (46 MB/s under 50, #447). The label was written
when the precheck knew only the link and CRC (#432); #1844/#1845/#447 added reasons to the
detail (`room.journal_precheck`) and the label stayed. The operator goes to the cable when the
disk is slow -- principle 5א.

The label follows the kind of the warnings with the room page's rule and words (#2670,
console.js `precheckKind`), and every reason in the detail is said in Hebrew beside the
raw tokens, which stay: they are the evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from console_src import console_js
from server.console import journal_he
from server.console.journal_he import JournalTranslator
from server.rounds import room

pytest.importorskip("fastapi")

from conftest import setup_classroom  # noqa: E402

MAC = "78:ac:c0:9b:11:c2"
ROUND = {"id": "room_7f331ae9", "wave_number": 1}


def warning(**over) -> dict:
    """The metal row (05-447-precheck-before-join.json): link 3/3, CRC 0, a slow write. #2848 (Nadav 11/11):
    a past round's slow write (#447's ``write_slow``) is no longer warned -- the slow kind is ``model_slow``."""
    w = {"mac": MAC, "port": 5, "serial": "naa.5000039fd2c449b0", "link_gbps": 3, "link_max_gbps": 3,
         "crc_rise": 0, "crc": 0, "crc_suspect": False, "port_suspect": False, "disk_suspect": False,
         "model_slow": True}
    w.update(over)
    return w


def line(server, *warned) -> tuple[str, str]:
    conn = server["ctx"].conn
    room.journal_precheck(conn, ROUND, list(warned), "")
    (detail,) = [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_precheck_warned' ORDER BY id DESC LIMIT 1")]
    return JournalTranslator(conn, server["ctx"].library).translate("room_precheck_warned", detail)


def test_a_slow_write_alone_is_not_called_a_link_or_crc_warning(server):
    """The metal line: no link, no CRC -- the label says slow write, and the detail keeps its token."""
    setup_classroom(server)
    label, text = line(server, warning())
    assert "קישור" not in label and "CRC" not in label, label
    assert "כתיבה איטית" in label, label
    assert "learned=slow_model" in text, "the raw token is the evidence -- it stays"
    assert "איטי מהרגיל לדגם" in text, text


def test_a_slow_link_is_a_link_warning(server):
    setup_classroom(server)
    label, text = line(server, warning(link_gbps=1.5, link_max_gbps=6.0, model_slow=False))
    assert "קישור חשוד" in label and "כתיבה איטית" not in label, label
    assert "link=1.5/6.0" in text and "קישור 1.5 מתוך 6.0 Gb/s" in text, text


def test_a_crc_rise_is_a_link_warning_and_an_absolute_count_is_not_journaled(server):
    """#2846: a stale ``crc_suspect`` on the warning (an old caller) writes no ``crc=``."""
    setup_classroom(server)
    label, text = line(server, warning(crc_rise=12, crc_suspect=True, crc=812, model_slow=False))
    assert "קישור חשוד" in label, label
    assert "מונה CRC עלה ב-12 מאז הסבב הקודם" in text and "812 שגיאות" not in text, text


def test_a_slow_write_on_a_slow_link_is_both(server):
    """Like the room page: one drawer carrying a link sign is a link warning too (#2670)."""
    setup_classroom(server)
    label, _ = line(server, warning(link_gbps=1.5, link_max_gbps=6.0))
    assert "קישור חשוד או כתיבה איטית" in label, label


def test_two_drawers_of_two_kinds_are_both(server):
    setup_classroom(server)
    label, text = line(server, warning(), warning(port=3, link_gbps=1.5, link_max_gbps=6.0, model_slow=False))
    assert "קישור חשוד או כתיבה איטית" in label, label
    assert "קישור 1.5 מתוך 6.0 Gb/s" in text and "איטי מהרגיל לדגם" in text, text


@pytest.mark.parametrize("flag,token,he", [
    ("port_suspect", "learned=port", "כנראה כבל/מחבר, לא הדיסק"),
    ("disk_suspect", "learned=disk", "הדיסק עצמו"),
    ("model_slow", "learned=slow_model", "איטי מהרגיל לדגם"),
])
def test_every_learned_reason_is_said_in_hebrew(server, flag, token, he):
    setup_classroom(server)
    _, text = line(server, warning(**{"model_slow": False, flag: True}))
    assert token in text and he in text, text


def test_a_detail_it_cannot_read_keeps_the_general_label(server):
    """An old or broken row: never a guessed kind -- the general label and the raw detail."""
    setup_classroom(server)
    tr = JournalTranslator(server["ctx"].conn, server["ctx"].library)
    label, text = tr.translate("room_precheck_warned", "something else entirely")
    assert label == journal_he.EVENTS_HE["room_precheck_warned"]
    assert "something else entirely" in text


def test_the_general_label_names_no_cause():
    """The filter list and the fallback: "a warning", not "link/CRC" (#2704)."""
    label = journal_he.EVENTS_HE["room_precheck_warned"]
    assert "קישור" not in label and "CRC" not in label and "אזהרה" in label, label


def test_the_kinds_are_the_room_pages_words():
    """One rule, two places: the room note (#2670) and the journal say the same kind."""
    js = console_js()
    body = re.search(r"const PRECHECK_NOUN_HE = \{([^}]*)\}", js).group(1)   # #2707: one table of nouns
    for words in journal_he.PRECHECK_NOUN_HE.values():
        assert f'"{words}"' in body, words


def test_the_journal_endpoint_carries_the_label(server):
    """End to end: GET /api/console/journal, as the operator reads it."""
    setup_classroom(server)
    room.journal_precheck(server["ctx"].conn, ROUND, [warning()], "")
    rows = server["admin"].get("/api/console/journal").json()
    (row,) = [r for r in rows if r["event"] == "room_precheck_warned"]
    assert "כתיבה איטית" in row["label"] and "קישור" not in row["label"], row["label"]


def test_the_reasons_are_the_screens_sentences():
    """The journal's Hebrew is the head of the sentence the room page already says -- not new words."""
    from server.disks import crcwarn
    from server.learn import models, ports
    he = journal_he.PRECHECK_REASON_HE
    assert ports.PORT_HE.startswith(he["learned=port"]) and ports.DISK_HE.startswith(he["learned=disk"])
    assert models.SLOW_HE.startswith(he["learned=slow_model"])
    # #2848: learned=slow_write is no longer written; a journal line from before still reads in Hebrew
    assert journal_he.precheck_he(f"r wave=1 {MAC}:5 learned=slow_write")[1] == ["בבדיקת הכתיבה הקודמת נמדד איטי"]
    assert journal_he.precheck_he(f"r wave=1 {MAC}:5 crc=812")[1] == [crcwarn.text_he(812)]
