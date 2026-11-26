"""#4144: Scheduled-Cast that could not evaluate is "not checked" (principle 5), not "the rule approved the start".

When the evaluation in `scheduled_cast.hold` raises, the wave starts exactly as it does with the switch off (the code from
before the rule), AND an UNDECIDED row is written to ``brain_decisions`` with the reason -- a log line alone is not a record.
"""

import json

import pytest

from server.brain import scheduled_cast
from server.db import set_setting
import test_server_room
from test_server_room import CLONER1, cloner_hello
from test_scheduled_cast_3569 import open_wave

room_server = test_server_room.room_server     # the fixture, re-exported (an import would shadow it as F811)


def rows(conn, wave):
    return conn.execute("SELECT * FROM brain_decisions WHERE rule='SCHEDULED_CAST' AND entity_key=?", (wave,)).fetchall()


def test_a_failed_evaluation_starts_like_switch_off_and_records_undecided(room_server, monkeypatch):
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", "enforce")
    set_setting(conn, "brain_scheduled_cast", "true")

    def broken(*_args, **_kwargs):
        raise RuntimeError("clock unreadable")

    monkeypatch.setattr(scheduled_cast, "judge", broken)
    wave = open_wave(room_server, {CLONER1: ["S1", "S2"]}, 2)
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    # as with the switch off: the old code started the wave on the first pulse
    assert answer["session"]["state"] == "running"
    found = rows(conn, wave)
    assert [(r["verdict"], r["point"], r["mode"]) for r in found] == [("UNDECIDED", "hold", "shadow")]
    assert "לא נבדק" in found[0]["reason"]
    assert any("RuntimeError" in str(item) for item in json.loads(found[0]["evidence_json"]))
    assert found[0]["outcome"].startswith("n/a: ")        # closed on purpose, not left open as a bug


@pytest.mark.parametrize("switch", ["true", "false"])
def test_switch_off_and_a_working_rule_write_no_undecided(room_server, switch):
    """Guard: only a failed evaluation writes UNDECIDED. A working rule holds the wave; switch off never evaluates."""
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", "enforce")
    set_setting(conn, "brain_scheduled_cast", switch)
    wave = open_wave(room_server, {CLONER1: ["S1", "S2"]}, 2)
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    assert answer["session"]["state"] == ("open" if switch == "true" else "running")
    assert [r["verdict"] for r in rows(conn, wave)] == (["WAIT"] if switch == "false" else [])


def test_the_rule_has_a_decision_item_in_the_catalog():
    """#4144: the rule writes SCHEDULED_CAST rows and can hold a wave, so the catalog needs a decision item (not only the two thresholds)."""
    import tomllib
    from pathlib import Path
    items = tomllib.loads((Path(__file__).resolve().parents[1] / "server" / "brain" / "catalog.toml").read_text(encoding="utf-8"))["item"]
    found = [i for i in items if i["layer"] == "decisions" and i.get("file") == "server/brain/scheduled_cast.py" and i.get("symbol") == "hold"]
    assert [(i.get("mode"), i.get("brain_state")) for i in found] == [("shadow", "shadow")]
