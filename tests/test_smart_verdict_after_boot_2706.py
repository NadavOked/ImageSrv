"""#2706 -- the drawer's SMART word after a round, when the machine has not read SMART since it booted.

Same root as #2705: a cloner woken into a round sends one hello with `smart: "unchecked"` (its
cache is per boot; the hello never probes), reads SMART in the round's gate (`disk_event`) and
powers off. The hello replaced `disks_json` whole, so the drawer's tooltip said "SMART לא נבדק"
on a disk the round had read (metal 08/11, 14-1857-console-tooltips.txt, 05:16Z).

The view carries the last round's verdict beside the hello's: `smart_round`
`{verdict, reason, at}` from `disk_events`, by serial. **The hello's `smart` is not changed**:
the red gate (`room.open`, ‏#1732) and the precheck read it, and a verdict from an earlier boot
is not evidence about the disk now (principle 5, the rule of #2705).

The precheck (`room_precheck._learned`) decided, explicitly: it stays on the hello. With
`smart_attrs: null` it says `smart_learned: None, smart_compared: None` -- "not read now" --
even when `smart_history` holds two snapshots with a rise; the round snapshot is never
presented to the wave's warning as a current reading.
"""

from __future__ import annotations

import pytest

from server.agent import registry
from server.disks import disk_events, smart_history
from server.rounds import room, room_precheck

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom  # noqa: E402


def hello(server, mac: str, smart: str = "unchecked", smart_attrs=None) -> None:
    body = hello_body(mac)
    body["disks"][0].update({"port": 3, "smart": smart, "smart_attrs": smart_attrs})
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def event(mac: str, session: str, verdict: str = "ok", reason: str = "ok", serial: str = "S1",
          attrs: dict | None = None) -> dict:
    return {"session_id": session, "mac": mac, "disk": "sda", "port": 3, "serial": serial,
            "write_state": "pending", "decision": None,
            "smart": {"verdict": verdict, "reason": reason, "realloc": 0, "pending": 0,
                      "uncorrectable": 0, "crc": 0, "attrs": attrs}}


def attrs(r5: int) -> dict:
    return {"ata": [[5, 100, 100, 10, r5, "Reallocated_Sector_Ct"], [9, 100, 100, 0, 100, "Power_On_Hours"]],
            "nvme": None}


def machine_disk(server, mac: str) -> dict:
    (m,) = [m for m in server["admin"].get("/api/console/machines").json()
            if m["mac"] == registry.normalize_mac(mac)]
    (d,) = m["disks"]
    return d


def test_the_machines_view_carries_the_rounds_verdict_beside_the_boot_hellos(server):
    """The metal sequence: round 2's gate read the disk (ok), then nothing -- the tooltip's data."""
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"])                                   # boot: woken into the round
    disk_events.ingest(server["ctx"].conn, event(mac, "ses2"))   # the round's gate
    d = machine_disk(server, ids["mac1"])
    assert d["smart"] == "unchecked", "the hello's own word is not rewritten"
    got = d.get("smart_round") or {}
    assert got.get("verdict") == "ok" and got.get("reason") == "ok" and got.get("at"), got


def test_the_latest_round_wins(server):
    ids = setup_classroom(server)
    conn = server["ctx"].conn
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"])
    disk_events.ingest(conn, event(mac, "ses1", "ok", "ok"))
    disk_events.ingest(conn, event(mac, "ses2", "warn", "reallocated"))
    conn.execute("UPDATE disk_events SET updated_at = '2026-11-08T05:11:09+00:00' WHERE session_id = 'ses1'")
    conn.execute("UPDATE disk_events SET updated_at = '2026-11-08T05:14:43+00:00' WHERE session_id = 'ses2'")
    conn.commit()
    got = machine_disk(server, ids["mac1"]).get("smart_round") or {}
    assert (got.get("verdict"), got.get("reason"), got.get("at")) == \
        ("warn", "reallocated", "2026-11-08T05:14:43+00:00")


def test_a_disk_no_round_read_has_no_round_verdict(server):
    ids = setup_classroom(server)
    hello(server, ids["mac1"])
    assert machine_disk(server, ids["mac1"]).get("smart_round", "missing") is None


def test_another_machines_event_under_this_serial_is_not_this_disks_verdict(server):
    """disk_events keeps the serial only when that MAC reported it (#2492) -- a forged event is no verdict."""
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"])
    disk_events.ingest(server["ctx"].conn, event("aa:aa:aa:aa:aa:01", "ses9", "fail", "health_failed"))
    disk_events.ingest(server["ctx"].conn, event(mac, "ses2"))
    assert (machine_disk(server, ids["mac1"]).get("smart_round") or {}).get("verdict") == "ok"


def test_the_room_drawer_list_carries_it_too(server):
    """During the round the card reads drawer_list -- the gate's reading of this round is the newest."""
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"])
    disk_events.ingest(server["ctx"].conn, event(mac, "ses2", "warn", "ssd_worn"))
    (d,) = room.drawer_list(server["ctx"].conn, mac, set())
    assert d["smart"] == "unchecked"
    assert (d.get("smart_round") or {}).get("verdict") == "warn"


def test_the_red_gate_still_reads_the_hello_only(server):
    """The round said fail; the boot hello said unchecked. The server's red refusal (#1732) is the
    hello's -- the agent refuses the red disk itself in its gate (smartgate.sh), on a reading of now."""
    ids = setup_classroom(server)
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"])
    disk_events.ingest(server["ctx"].conn, event(mac, "ses2", "fail", "health_failed"))
    (d,) = room.drawer_list(server["ctx"].conn, mac, set())
    assert d["smart"] == "unchecked" and (d.get("smart_round") or {}).get("verdict") == "fail"


def test_the_precheck_does_not_take_a_round_snapshot_as_a_reading_of_now(server):
    """Decision (#2706): two snapshots with a rise and a boot hello with no reading -- the precheck says
    "not read now" (None, None), not the rise and not "no comparison yet"."""
    ids = setup_classroom(server)
    conn = server["ctx"].conn
    mac = registry.normalize_mac(ids["mac1"])
    hello(server, ids["mac1"], smart_attrs=attrs(0))
    disk_events.ingest(conn, event(mac, "ses1", attrs=attrs(0)))
    conn.execute("UPDATE smart_history SET at = '2026-11-08T05:11:09+00:00' WHERE session_id = 'ses1'")
    disk_events.ingest(conn, event(mac, "ses2", attrs=attrs(4)))
    conn.commit()
    assert smart_history.describe(conn, {"serial": "S1", "smart_attrs": None})["learned"] == "critical_rose", \
        "the console's view (#2705) shows the rise between the two rounds, with the date"
    hello(server, ids["mac1"])
    got = room_precheck.drawer_fields(conn, {"serial": "S1", "smart_attrs": None}, mac)
    assert (got["smart_learned"], got["smart_compared"]) == (None, None)
