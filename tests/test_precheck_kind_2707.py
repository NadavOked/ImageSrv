"""#2707 -- the kind of a precheck warning is what the warning is, in the room and in the journal.

`precheckKind` (#2670) called every warning that is not a slow write "suspect link", and the
journal (#2704) took the same rule on purpose. So a drawer whose only warning is "הדיסק עצמו —
בעייתי ב-N חיבורים" (`disk_suspect`, #1844) or "איטי מהרגיל לדגם" (`model_slow`, #1845) was
titled a link -- the operator goes to the cable when the server learned it is the disk (5א).

The signs, one table in both places:
* link -- a slow link, a CRC rise, CRC over the threshold, `port_suspect` (#1844: "כנראה כבל/מחבר"),
  `smart_learned` downshift_rose / timeout_rose (183/188 -- "likely the cable", smart_history.ROSE_LINK);
* slow write -- `model_slow` (#1845) (#2848: a past round's `write_slow`, #447, is no longer warned);
* suspect disk -- `disk_suspect` (#1844), `smart_learned` critical_rose / hot (#1859);
* none of them -- "עם אזהרה", never a guessed kind.
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
    w = {"mac": MAC, "port": 5, "serial": "S5", "link_gbps": 3, "link_max_gbps": 3, "crc_rise": 0, "crc": 0,
         "crc_suspect": False, "smart_learned": None, "port_suspect": False, "disk_suspect": False,
         "model_slow": False}
    w.update(over)
    return w


def label(server, *warned) -> str:
    conn = server["ctx"].conn
    room.journal_precheck(conn, ROUND, list(warned), "")
    (detail,) = [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_precheck_warned' ORDER BY id DESC LIMIT 1")]
    return JournalTranslator(conn, server["ctx"].library).translate("room_precheck_warned", detail)[0]


@pytest.mark.parametrize("over,kind", [
    ({"disk_suspect": True}, "עם דיסק חשוד"),
    ({"model_slow": True}, "עם כתיבה איטית"),
    ({"port_suspect": True}, "עם קישור חשוד"),
    ({"smart_learned": "critical_rose"}, "עם דיסק חשוד"),
    ({"smart_learned": "hot"}, "עם דיסק חשוד"),
    ({"smart_learned": "downshift_rose"}, "עם קישור חשוד"),
    ({"smart_learned": "timeout_rose"}, "עם קישור חשוד"),
    ({"crc_rise": 12}, "עם קישור חשוד"),
])
def test_each_sign_is_its_own_kind(server, over, kind):
    setup_classroom(server)
    got = label(server, warning(**over))
    assert got.endswith(kind), got


def test_the_disk_itself_is_not_called_a_link(server):
    """The finding: learned=disk alone."""
    setup_classroom(server)
    assert "קישור" not in label(server, warning(disk_suspect=True))


def test_kinds_join_in_one_order(server):
    setup_classroom(server)
    got = label(server, warning(disk_suspect=True), warning(port=3, link_gbps=1.5, link_max_gbps=6.0),
                warning(port=4, model_slow=True))
    assert got.endswith("עם קישור חשוד או כתיבה איטית או דיסק חשוד"), got


def test_a_warning_with_no_sign_it_knows_is_a_warning_not_a_guess():
    assert journal_he.precheck_he(f"r wave=1 {MAC}:5")[0] == "עם אזהרה"


def test_the_learned_smart_word_is_in_the_journal_and_in_hebrew(server):
    """#1859's word reached the room note but not the journal line -- without it the journal cannot say the kind."""
    from server.disks import smart_history
    setup_classroom(server)
    conn = server["ctx"].conn
    room.journal_precheck(conn, ROUND, [warning(smart_learned="critical_rose")], "")
    (detail,) = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'room_precheck_warned'")]
    assert "smart=critical_rose" in detail
    _, text = JournalTranslator(conn, server["ctx"].library).translate("room_precheck_warned", detail)
    assert smart_history.LEARNED_HE["critical_rose"] in text


def test_one_table_in_both_places():
    """console.js precheckKind and journal_he.precheck_he: the same nouns and the same smart words per kind."""
    js = console_js()
    nouns = dict(re.findall(r'(\w+): "([^"]+)"', re.search(r"const PRECHECK_NOUN_HE = \{([^}]*)\}", js).group(1)))
    assert nouns == journal_he.PRECHECK_NOUN_HE
    for kind, words in journal_he.PRECHECK_SMART_KIND.items():
        js_words = re.search(rf"const PRECHECK_{kind.upper()}_ROSE = \[([^\]]*)\]", js).group(1)
        assert sorted(re.findall(r'"(\w+)"', js_words)) == sorted(words), kind
    assert '"עם אזהרה"' in js
