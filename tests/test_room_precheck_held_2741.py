"""#2741 -- the "held" text of `missing` names only the disks that failed.

Metal 08/11, cloner 2: ports 1 and 2 in the round, port 1 (sdd) failed, port 2 was written.
The text said "(דיסק 1, דיסק 2)"; `held.problems` had one disk. The parentheses now list the
problem disks from `held.problems`, mapped by `dev` to their drawer, else the dev name.
"""

from server.rounds import room_precheck

SLOTS = [{"mac": "m1", "ports": [1, 2]}]


def _machine(problems, drawers):
    return {"mac": "m1", "name": "shich-2", "awake": True, "joined": False,
            "held": {"at": "x", "problems": problems} if problems is not None else {"at": "x"},
            "drawer_list": drawers}


def _text(m):
    got = room_precheck.missing([m], 2, SLOTS, 0)
    assert [x["reason"] for x in got] == ["held"], got
    return got[0]["text_he"]


DRAWERS = [{"dev": "sdd", "port": 1, "fresh": False}, {"dev": "sdc", "port": 2, "fresh": False}]


def test_only_the_failed_disk_of_two_drawers_is_named():
    text = _text(_machine([{"disk": "sdd", "level": "r", "reason": "איטי — 6 MB/s"}], DRAWERS))
    assert "(דיסק 1: איטי — 6 MB/s)" in text, text
    assert "דיסק 2" not in text, text


def test_a_dev_that_maps_to_no_drawer_is_shown_by_its_dev_name():
    text = _text(_machine([{"disk": "sdz", "level": "r", "reason": ""}], DRAWERS))
    assert text.endswith("(sdz)"), text


def test_no_problems_means_no_parentheses():
    text = _text(_machine([], DRAWERS))
    assert "(" not in text, text


def test_other_reasons_still_list_the_round_drawers():
    m = _machine([{"disk": "sdd", "level": "r", "reason": "x"}], DRAWERS)
    m["held"] = None
    m["awake"] = False
    got = room_precheck.missing([m], 2, SLOTS, 0)
    assert got[0]["reason"] == "off" and "(דיסק 1, דיסק 2)" in got[0]["text_he"], got
