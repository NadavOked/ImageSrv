"""#2705 -- a hello with no SMART reading does not erase what the round read.

Metal, 08/11, v0.90.0, cloner 2: its screen showed drawer 3's full SMART table, and after
the second round `GET /api/console/machines/{mac}/smart` said `read: false, attributes: []`
-- "לא נבדק" on a disk that was read.

The cause (not the hello "racing" the read): the agent's SMART cache is per boot, and the
hello never probes (smartattrs.sh -- `smart_attrs: null` = not read in this boot). A cloner
woken into a round sends its one hello first, reads SMART in the round's gate (the
`disk_event` -> `smart_history`), and powers off. That hello replaced `disks_json` whole
(db.net_seen), and `describe` read the attributes only from there -- the round's snapshot
of the same serial, already in `smart_history`, was never looked at.

"לא נבדק" only when nothing was read: not in the last hello, and never in a round.
"""

from __future__ import annotations

import pytest

from server.agent import registry
from server.disks import disk_events, smart_history

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom  # noqa: E402


def attrs(**raw) -> dict:
    rows = {5: 0, 9: 12871, 199: 0}
    rows.update({int(k[1:]): v for k, v in raw.items()})
    names = {5: "Reallocated_Sector_Ct", 9: "Power_On_Hours", 199: "CRC_Error_Count"}
    return {"ata": [[i, 100, 100, 10 if i == 5 else 0, v, names[i]] for i, v in sorted(rows.items())],
            "nvme": None}


def event(mac: str, session: str, a: dict) -> dict:
    return {"session_id": session, "mac": mac, "disk": "sda", "port": 3, "serial": "S1",
            "write_state": "pending", "decision": None,
            "smart": {"verdict": "ok", "reason": "ok", "realloc": 0, "pending": 0,
                      "uncorrectable": 0, "crc": 0, "attrs": a}}


def hello(server, mac: str, smart_attrs) -> None:
    body = hello_body(mac)
    if smart_attrs is not ...:
        body["disks"][0]["smart_attrs"] = smart_attrs
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def view(server, mac: str) -> dict:
    (disk,) = server["admin"].get(f"/api/console/machines/{mac}/smart").json()["disks"]
    return disk


def at(conn, session: str, when: str) -> None:
    conn.execute("UPDATE smart_history SET at = ? WHERE session_id = ?", (when, session))
    conn.commit()


def test_the_boot_hello_of_the_next_round_does_not_erase_the_rounds_reading(server):
    """The metal sequence: an idle hello with the table, the round's disk_event, then the boot hello
    of the next round with `smart_attrs: null` -- and the console still shows what the round read."""
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], attrs(r5=0))                       # boot 1: idle, probed
    disk_events.ingest(server["ctx"].conn, event(mac, "ses1", attrs(r5=0)))   # round 1's gate
    hello(server, ids["mac1"], None)                              # boot 2: woken into round 2, no probe yet
    disk = view(server, ids["mac1"])
    assert disk["read"] is True, "the round read this disk -- 'לא נבדק' is false"
    assert {a["id"] for a in disk["attributes"]} == {5, 9, 199}
    assert disk.get("read_from") == "round"
    row = server["ctx"].conn.execute("SELECT at FROM smart_history WHERE session_id = 'ses1'").fetchone()
    assert disk.get("read_at") == row["at"], "said with when it was read -- it is not this boot's reading"


def test_an_old_agent_that_sends_no_smart_attrs_also_falls_back(server):
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], ...)
    disk_events.ingest(server["ctx"].conn, event(mac, "ses1", attrs()))
    disk = view(server, ids["mac1"])
    assert disk["read"] is True and disk.get("read_from") == "round"


def test_the_fallback_is_compared_with_the_round_before_it(server):
    """The shown reading is round 2's; "rose since the previous round" is against round 1, never
    against itself (which would always say "nothing rose")."""
    ids = setup_classroom(server)
    conn = server["ctx"].conn
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], attrs())
    disk_events.ingest(conn, event(mac, "ses1", attrs(r5=0)))
    at(conn, "ses1", "2026-11-08T05:11:09+00:00")
    disk_events.ingest(conn, event(mac, "ses2", attrs(r5=2)))
    at(conn, "ses2", "2026-11-08T05:14:43+00:00")
    hello(server, ids["mac1"], None)
    disk = view(server, ids["mac1"])
    assert disk["read"] is True and {a["id"]: a["raw"] for a in disk["attributes"]}[5] == 2, "round 2's reading"
    assert disk.get("read_at") == "2026-11-08T05:14:43+00:00"
    assert disk["compared"] is True and disk["previous_at"] == "2026-11-08T05:11:09+00:00"
    assert {a["id"]: a["rose_by"] for a in disk["attributes"]}[5] == 2
    assert disk["learned"] == "critical_rose"


def test_a_single_round_and_no_reading_since_is_no_comparison(server):
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], attrs())
    disk_events.ingest(server["ctx"].conn, event(mac, "ses1", attrs(r5=4)))
    hello(server, ids["mac1"], None)
    disk = view(server, ids["mac1"])
    assert disk["read"] is True and disk["compared"] is False and disk["previous_at"] is None
    assert all(a["rose_by"] is None for a in disk["attributes"])


def test_a_disk_never_read_anywhere_is_not_checked(server):
    """Principle 5 the other way: no hello reading and no round -- "לא נבדק", no invented table."""
    ids = setup_classroom(server)
    hello(server, ids["mac1"], None)
    disk = view(server, ids["mac1"])
    assert disk["read"] is False and disk["attributes"] == [] and disk.get("read_from") is None
    assert disk.get("read_at") is None


def test_a_reading_in_the_last_hello_is_the_one_shown(server):
    """Unchanged: the hello's own reading is the newest -- the round's snapshot is its comparison."""
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], attrs())
    disk_events.ingest(server["ctx"].conn, event(mac, "ses1", attrs(r5=0)))
    hello(server, ids["mac1"], attrs(r5=3))
    disk = view(server, ids["mac1"])
    assert disk.get("read_from") == "hello" and disk.get("read_at") is None
    assert {a["id"]: a["raw"] for a in disk["attributes"]}[5] == 3
    assert disk["compared"] is True


def test_a_hello_that_read_an_empty_table_is_not_replaced_by_an_older_round(server):
    """`smart_attrs: {}`-shaped = smartctl read the disk now and it gave no attributes. That is this
    boot's answer; only `null` (not read in this boot) falls back."""
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], attrs())
    disk_events.ingest(server["ctx"].conn, event(mac, "ses1", attrs()))
    hello(server, ids["mac1"], {"ata": [], "nvme": None})
    disk = view(server, ids["mac1"])
    assert disk["read"] is False and disk.get("read_from") is None


def test_describe_falls_back_by_serial_not_by_dev(server):
    """The dev name moves between boots (port 3 was sdd in one boot, sdb in the next -- 08/11)."""
    ids = setup_classroom(server)
    conn = server["ctx"].conn
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], attrs())
    disk_events.ingest(conn, event(mac, "ses1", attrs()))
    got = smart_history.describe(conn, {"serial": "S1", "dev": "sdd", "smart_attrs": None})
    assert got["read"] is True and got["dev"] == "sdd" and got.get("read_from") == "round"
