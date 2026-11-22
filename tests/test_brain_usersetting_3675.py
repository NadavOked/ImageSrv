"""#3675, part 1: a change of a setting the user decides (`hdd_support`, `tail_shrink_enabled`, `min_free_gb`, the agent timings) is a
row in `brain_decisions`: the value, who changed it, when. Rule `B3675`, action `SETTING_CHANGE`, entity `settings` / the key.

**Negative control (bakara-shlilit):** with `server/console/api.py` back at the base (no `brain_usersetting.record` call), the (neg) tests
fail on behaviour: the setting is stored and the history has no row. The (guard) tests assert what the base also does -- the value is
stored, and a key that is not one of the five records nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import memory

URL = "/api/console/settings"


def _rows(server, key=None):
    rows = [d for d in memory.decisions(server["ctx"].conn, rule="B3675", limit=50) if d["action"] == "SETTING_CHANGE"]
    return [d for d in rows if key is None or d["entity_key"] == key]


def _ev(row):
    return dict(e.split("=", 1) for e in row["evidence"] if "=" in e)


def test_a_changed_user_setting_is_a_decision_with_old_new_and_who(server):
    assert server["admin"].post(URL, json={"hdd_support": "true"}).status_code == 200
    [row] = _rows(server, "hdd_support")
    assert row["verdict"] == "SET" and row["entity_kind"] == "settings", row
    ev = _ev(row)
    assert (ev["key"], ev["old"], ev["new"]) == ("hdd_support", "false", "true") and ev["by"], ev   # old = the stored default row
    assert row["at"], row


def test_every_one_of_the_five_settings_is_recorded(server):
    body = {"hdd_support": "true", "tail_shrink_enabled": "true", "min_free_gb": "35", "lost_wait_seconds": 600,
            "gui_idle_poweroff_seconds": 120, "gui_end_poweroff_seconds": 30, "drawer_disk_wait_seconds": 200,
            "drawer_net_wait_seconds": 200}
    assert server["admin"].post(URL, json=body).status_code == 200
    assert sorted(r["entity_key"] for r in _rows(server)) == sorted(body)


def test_a_write_that_leaves_the_value_as_it_was_records_nothing(server):
    assert server["admin"].post(URL, json={"min_free_gb": "35"}).status_code == 200
    assert len(_rows(server, "min_free_gb")) == 1
    assert server["admin"].post(URL, json={"min_free_gb": 35}).status_code == 200      # same value, as a number
    assert len(_rows(server, "min_free_gb")) == 1
    assert server["admin"].post(URL, json={"min_free_gb": "40"}).status_code == 200
    rows = _rows(server, "min_free_gb")
    assert len(rows) == 2 and {(_ev(r)["old"], _ev(r)["new"]) for r in rows} == {("default", "35"), ("35", "40")}


def test_a_refused_write_records_nothing_and_stores_nothing(server):
    assert server["admin"].post(URL, json={"hdd_support": "true", "min_free_gb": "501"}).status_code == 400
    assert _rows(server) == []                                                             # (guard) validated before any write
    assert server["admin"].get(URL).json()["hdd_support"] == "false"


def test_a_setting_that_is_not_the_users_decision_of_this_issue_records_nothing(server):
    assert server["admin"].post(URL, json={"cloner_beep": "false"}).status_code == 200    # (guard)
    assert _rows(server) == []
