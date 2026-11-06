"""#2487 + #2492: what an agent route may write, and under which disk's name.

#2487: a machine that is not registered but holds a DHCP lease passed the
identity gate (`identity_gate`: the MAC matches its lease) and then wrote rows
with no limit -- `disk-event`, `shrink-open`, `local-clone` and an unknown
`hello`. `open_pull` already refused it with 403 `unknown_mac`; the three
writers now do the same. The unknown hello is part of the flow (a new machine
has to appear in the console to be registered) and is NOT refused: it keeps
one inventory/probe row per MAC and one `unknown_mac` journal line per window.

#2492: `disk-event`, the progress report and `shrink-open` stored the serial
from the request body as it came -- any machine could mark another machine's
disk read-only (its hello then hands the mark back, and its agent skips the
disk until an admin clears it). The serial is now kept only when it is what
this MAC reported for this `dev` -- the gate `smart_history.record` already had.

Every refusal here has a positive twin: a registered machine reporting its own
disk is stored exactly as before. Breaking that is worse than the weakness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from test_server_api import open_session
from test_shrink_records import SERIAL, layout

STRANGER = "02:00:5e:10:00:99"      # holds a lease (no identity hook here), not registered
VICTIM_SERIAL = "S-VICTIM-0001"


def _hello(server, mac: str, disks: list[dict] | None = None, **extra) -> dict:
    body = hello_body(mac)
    if disks is not None:
        body["disks"] = disks
    body.update(extra)
    response = server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _disk(dev: str, serial: str, port: int = 1) -> dict:
    return {"dev": dev, "size_bytes": 256060514304, "model": "Test SSD", "serial": serial,
            "removable": False, "scheme": "gpt", "has_data": True, "port": port}


def _readonly_event(session: str, mac: str, disk: str, serial: str) -> dict:
    return {"session_id": session, "mac": mac, "disk": disk, "port": 1, "serial": serial,
            "smart": {"verdict": "fail", "reason": "readonly"},
            "write_state": "skipped", "decision": "skip"}


def _count(server, table: str) -> int:
    return server["ctx"].conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _events(server, event: str) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


# --- #2487: an unregistered MAC writes nothing through the three routes -------------


def test_a_disk_event_from_an_unregistered_mac_is_403_and_writes_nothing(server):
    open_session(server)
    r = server["anon"].post("/api/v1/agent/disk-event",
                            json=_readonly_event("ses_x", STRANGER, "sda", "S1"))
    assert r.status_code == 403 and r.json()["code"] == "unknown_mac", r.text
    assert _count(server, "disk_events") == 0
    assert _count(server, "disk_failures") == 0


def test_a_local_clone_report_from_an_unregistered_mac_is_403_and_not_journaled(server):
    from test_local_clone_report import _report
    r = server["anon"].post("/api/v1/agent/local-clone", json=_report(mac=STRANGER))
    assert r.status_code == 403 and r.json()["code"] == "unknown_mac", r.text
    assert _events(server, "local_clone") == []


def test_a_local_clone_report_from_a_registered_machine_is_journaled(server):
    from test_local_clone_report import _report
    ids = open_session(server)
    r = server["anon"].post("/api/v1/agent/local-clone", json=_report(mac=ids["mac1"]))
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text
    assert len(_events(server, "local_clone")) == 1


def test_a_shrink_open_from_an_unregistered_mac_is_403_and_writes_nothing(server):
    open_session(server)
    _hello(server, STRANGER, [_disk("sda", SERIAL)])      # even with its own disk reported
    r = server["anon"].post("/api/v1/agent/shrink-open", json=layout(mac=STRANGER))
    assert r.status_code == 403 and r.json()["code"] == "unknown_mac", r.text
    assert _count(server, "shrink_records") == 0


# --- #2487: an unknown hello is answered, and its rows are bounded -----------------


def test_an_unknown_hello_is_answered_and_seen_but_its_rows_are_bounded(server):
    """The flow stays: known:false, and the MAC is in the device list to be
    registered. What a script could grow without limit no longer grows: the
    inventory and the probe keep ONE row per unknown MAC (the latest), and the
    `unknown_mac` journal line is written once per window, then counted."""
    for i in range(6):
        answer = _hello(server, STRANGER,
                        inventory={"dmi": {"product_name": f"Model {i}"}, "pci": []},
                        probe={"cpu": {"model": f"CPU {i}"}})
        assert answer["known"] is False
    conn = server["ctx"].conn
    assert conn.execute("SELECT 1 FROM net_devices WHERE mac = ?", (STRANGER,)).fetchone()
    inv = conn.execute("SELECT inventory_json FROM machine_inventory WHERE mac = ?", (STRANGER,)).fetchall()
    probes = conn.execute("SELECT probe_json FROM machine_probe WHERE mac = ?", (STRANGER,)).fetchall()
    assert len(inv) == 1 and "Model 5" in inv[0]["inventory_json"]     # the latest, in place
    assert len(probes) == 1 and "CPU 5" in probes[0]["probe_json"]
    assert len(_events(server, "unknown_mac")) == 1


def test_a_registered_machine_keeps_every_inventory_version(server):
    """The positive twin: a registered machine's versions are its history (#720)."""
    ids = open_session(server)
    for i in range(3):
        _hello(server, ids["mac1"], inventory={"dmi": {"product_name": f"Model {i}"}, "pci": []},
               probe={"cpu": {"model": f"CPU {i}"}})
    conn = server["ctx"].conn
    assert conn.execute("SELECT COUNT(*) AS n FROM machine_inventory WHERE mac = ?",
                        (ids["mac1"],)).fetchone()["n"] == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM machine_probe WHERE mac = ?",
                        (ids["mac1"],)).fetchone()["n"] == 3


def test_the_unknown_mac_line_comes_back_after_the_window_with_the_count(server, monkeypatch):
    from server.agent import api as agent_api
    now = [1000.0]
    monkeypatch.setattr(agent_api, "_clock", lambda: now[0])
    for _ in range(4):
        _hello(server, STRANGER)
    now[0] += agent_api.IDENTITY_JOURNAL_WINDOW_S + 1
    _hello(server, STRANGER)
    lines = _events(server, "unknown_mac")
    assert len(lines) == 2, lines
    assert lines[0].startswith(STRANGER) and "[+" not in lines[0]
    assert "[+3 identical" in lines[1], lines


# --- #2492: the serial is bound to the MAC that reports it -------------------------


def test_a_readonly_mark_on_another_machines_serial_does_not_reach_its_hello(server):
    """The issue's own test: machine B reports `readonly` with machine A's serial.
    A's hello must come back without the mark, and nothing names that serial."""
    ids = open_session(server)
    a, b = ids["mac1"], ids["mac2"]
    _hello(server, a, [_disk("sda", VICTIM_SERIAL)])
    _hello(server, b, [_disk("sda", "S-ATTACKER")])
    r = server["anon"].post("/api/v1/agent/disk-event",
                            json=_readonly_event(ids["session"], b, "sda", VICTIM_SERIAL))
    assert r.status_code == 200, r.text      # best effort: the event itself is kept, unnamed
    assert _hello(server, a, [_disk("sda", VICTIM_SERIAL)])["disk_failures"] == []
    conn = server["ctx"].conn
    assert conn.execute("SELECT COUNT(*) AS n FROM disk_failures").fetchone()["n"] == 0
    (row,) = conn.execute("SELECT mac, serial FROM disk_events").fetchall()
    assert row["mac"] == b and row["serial"] is None


def test_a_machine_that_claims_the_serial_in_its_own_hello_still_cannot_mark_it(server):
    """Review of the branch (coordinator, 06/11): the hello is the writer's own
    word -- B can list A's serial in its hello and pass the binding. So the
    reading side decides: a `readonly` mark is handed back only to the MAC that
    wrote it. A's hello comes back without B's mark."""
    ids = open_session(server)
    a, b = ids["mac1"], ids["mac2"]
    _hello(server, a, [_disk("sda", VICTIM_SERIAL)])
    _hello(server, b, [_disk("sda", VICTIM_SERIAL)])          # the claim
    r = server["anon"].post("/api/v1/agent/disk-event",
                            json=_readonly_event(ids["session"], b, "sda", VICTIM_SERIAL))
    assert r.status_code == 200, r.text
    assert _hello(server, a, [_disk("sda", VICTIM_SERIAL)])["disk_failures"] == []
    # B's own mark stays B's: what it wrote reaches only its own hello
    marks = _hello(server, b, [_disk("sda", VICTIM_SERIAL)])["disk_failures"]
    assert [(m["serial"], m["cause"]) for m in marks] == [(VICTIM_SERIAL, "readonly")]


def test_a_shrink_record_is_offered_only_to_the_machine_that_opened_it(server):
    """The same claim on `shrink-open`: B lists A's serial and opens a record.
    A's hello must not offer to "restore" a layout B wrote; B's own hello does."""
    ids = open_session(server)
    a, b = ids["mac1"], ids["mac2"]
    _hello(server, a, [_disk("sda", SERIAL)])
    _hello(server, b, [_disk("sda", SERIAL)])                 # the claim
    r = server["anon"].post("/api/v1/agent/shrink-open", json=layout(mac=b))
    assert r.status_code == 200, r.text
    assert _hello(server, a, [_disk("sda", SERIAL)])["shrink_open"] == []
    assert [rec["mac"] for rec in _hello(server, b, [_disk("sda", SERIAL)])["shrink_open"]] == [b]


def test_another_machines_mark_first_does_not_swallow_the_owners_own(server):
    """Review (coordinator, 06/11): the dedupe must match the reading side. B
    marks A's serial first; A then marks its own disk. A's mark is created and
    comes back in A's hello -- B's earlier mark does not count as "already
    there". The console shows both: a conflict is visible, not hidden."""
    ids = open_session(server)
    a, b = ids["mac1"], ids["mac2"]
    _hello(server, b, [_disk("sda", VICTIM_SERIAL)])          # B claims first
    assert server["anon"].post("/api/v1/agent/disk-event", json=_readonly_event(
        ids["session"], b, "sda", VICTIM_SERIAL)).status_code == 200
    _hello(server, a, [_disk("sda", VICTIM_SERIAL)])
    assert server["anon"].post("/api/v1/agent/disk-event", json=_readonly_event(
        ids["session"], a, "sda", VICTIM_SERIAL)).status_code == 200
    marks = _hello(server, a, [_disk("sda", VICTIM_SERIAL)])["disk_failures"]
    assert [(m["serial"], m["cause"]) for m in marks] == [(VICTIM_SERIAL, "readonly")]
    rows = server["admin"].get("/api/console/disk-failures").json()
    assert sorted(r["mac"] for r in rows if r["serial"] == VICTIM_SERIAL) == sorted([a, b])


def test_another_machines_shrink_record_first_does_not_block_the_owners(server):
    """The same on `shrink-open`: B opens on A's serial first; A's own record is
    opened (not 409), offered in A's hello, and closed by A. Both reach the console."""
    ids = open_session(server)
    a, b = ids["mac1"], ids["mac2"]
    _hello(server, b, [_disk("sda", SERIAL)])
    assert server["anon"].post("/api/v1/agent/shrink-open", json=layout(mac=b)).status_code == 200
    _hello(server, a, [_disk("sda", SERIAL)])
    r = server["anon"].post("/api/v1/agent/shrink-open", json=layout(mac=a))
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    rid = r.json()["id"]
    offered = _hello(server, a, [_disk("sda", SERIAL)])["shrink_open"]
    assert [(rec["id"], rec["mac"]) for rec in offered] == [(rid, a)]
    assert sorted(rec["mac"] for rec in server["admin"].get("/api/console/shrink-records").json()) == sorted([a, b])
    # the owner closes its own, without an id (an agent whose shrink_id file was lost)
    closed = server["anon"].post("/api/v1/agent/shrink-close", json={"mac": a, "serial": SERIAL})
    assert closed.status_code == 200 and closed.json() == {"ok": True}, closed.text
    assert [rec["mac"] for rec in server["admin"].get("/api/console/shrink-records").json()] == [b]
    # and the owner of the other record is still the only one who closes it
    assert server["anon"].post("/api/v1/agent/shrink-close", json={"mac": a, "serial": SERIAL}).status_code == 403


def test_a_readonly_mark_on_the_machines_own_disk_is_remembered(server):
    """The positive twin: the machine that reported the disk marks it, and its
    next hello (setro gone after the reboot) hands the mark back."""
    ids = open_session(server)
    a = ids["mac1"]
    _hello(server, a, [_disk("sda", VICTIM_SERIAL)])
    r = server["anon"].post("/api/v1/agent/disk-event",
                            json=_readonly_event(ids["session"], a, "sda", VICTIM_SERIAL))
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text
    marks = _hello(server, a, [_disk("sda", VICTIM_SERIAL)])["disk_failures"]
    assert [(m["serial"], m["cause"]) for m in marks] == [(VICTIM_SERIAL, "readonly")]
    (row,) = server["ctx"].conn.execute("SELECT serial FROM disk_events").fetchall()
    assert row["serial"] == VICTIM_SERIAL


def test_a_disk_event_from_a_second_card_in_the_round_is_still_stored(server):
    """#524: a machine that boots from a card that is not the registered one is
    known by `all_macs`, joins the round under the card it booted from, and
    sends its disk events from it. A member of the round is not a stranger."""
    ids = open_session(server)
    card = "02:00:5e:10:00:42"
    answer = _hello(server, card, [_disk("sda", VICTIM_SERIAL)], all_macs=[card, ids["mac1"]])
    assert answer["known"] is True and answer["session"] is not None
    r = server["anon"].post("/api/v1/agent/disk-event",
                            json=_readonly_event(ids["session"], card, "sda", VICTIM_SERIAL))
    assert r.status_code == 200 and r.json() == {"ok": True}, r.text
    assert server["ctx"].conn.execute(
        "SELECT serial FROM disk_failures").fetchone()["serial"] == VICTIM_SERIAL


def _failed(dev: str, serial: str) -> dict:
    from test_disk_failures import failed_target
    return failed_target(dev=dev, serial=serial)


def test_a_failed_target_with_another_machines_serial_is_kept_without_it(server):
    """Progress (#874): a member of the round reports a failed write with a
    serial it never reported. The failure stays on its own slot; the serial --
    which would follow the disk to any machine -- is not stored."""
    ids = open_session(server)
    a, b = ids["mac1"], ids["mac2"]
    _hello(server, a, [_disk("sda", VICTIM_SERIAL)])
    _hello(server, b, [_disk("sda", "S-ATTACKER")])
    report = {"session_id": ids["session"], "mac": b, "state": "failed",
              "targets": [_failed("sda", VICTIM_SERIAL)]}
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    (row,) = server["ctx"].conn.execute("SELECT mac, serial, port FROM disk_failures").fetchall()
    assert (row["mac"], row["serial"], row["port"]) == (b, None, 1)
    assert _hello(server, a, [_disk("sda", VICTIM_SERIAL)])["disk_failures"] == []


def test_a_failed_target_on_the_machines_own_disk_keeps_its_serial(server):
    ids = open_session(server)
    a = ids["mac1"]
    _hello(server, a, [_disk("sda", VICTIM_SERIAL)])
    report = {"session_id": ids["session"], "mac": a, "state": "failed",
              "targets": [_failed("sda", VICTIM_SERIAL)]}
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    (row,) = server["ctx"].conn.execute("SELECT serial FROM disk_failures").fetchall()
    assert row["serial"] == VICTIM_SERIAL


def test_a_disk_that_fell_off_the_bus_keeps_its_serial_from_the_round(server):
    """The legitimate case the hello alone cannot prove: the disk failed and left
    sysfs, and the hello after it no longer lists it. Its disk_event of THIS
    round (sent before the write, while it was there, and bound then) is the
    evidence that the serial is this machine's."""
    ids = open_session(server)
    a = ids["mac1"]
    _hello(server, a, [_disk("sda", VICTIM_SERIAL)])
    ok = {**_readonly_event(ids["session"], a, "sda", VICTIM_SERIAL),
          "smart": {"verdict": "ok", "reason": ""}, "write_state": "pending", "decision": None}
    assert server["anon"].post("/api/v1/agent/disk-event", json=ok).status_code == 200
    _hello(server, a, [])                                  # gone from the bus
    report = {"session_id": ids["session"], "mac": a, "state": "failed",
              "targets": [_failed("sda", VICTIM_SERIAL)]}
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    (row,) = server["ctx"].conn.execute("SELECT serial FROM disk_failures").fetchall()
    assert row["serial"] == VICTIM_SERIAL


def test_a_shrink_open_on_a_serial_the_machine_did_not_report_is_refused(server):
    ids = open_session(server)
    _hello(server, ids["mac1"], [_disk("sda", "S-OTHER")])
    r = server["anon"].post("/api/v1/agent/shrink-open", json=layout(mac=ids["mac1"]))
    assert r.status_code == 403 and r.json()["code"] == "serial_not_reported", r.text
    assert _count(server, "shrink_records") == 0


def test_a_shrink_open_on_the_machines_own_disk_is_opened(server):
    ids = open_session(server)
    _hello(server, ids["mac1"], [_disk("sda", SERIAL)])
    r = server["anon"].post("/api/v1/agent/shrink-open", json=layout(mac=ids["mac1"]))
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert _count(server, "shrink_records") == 1


# --- #2487: closed shrink records leave with the rest of the history ----------------


def test_housekeeping_drops_closed_shrink_records_after_90_days_never_open_ones(tmp_path):
    from server import db
    from server.disks import shrink_records
    from server.system import housekeeping
    conn = db.connect(tmp_path / "t.db")
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    old = (now - timedelta(days=housekeeping.RETENTION_DAYS + 1)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    for serial in ("S-OLD-CLOSED", "S-NEW-CLOSED", "S-OLD-OPEN"):
        shrink_records.open_record(conn, layout(serial=serial))
    conn.execute("UPDATE shrink_records SET closed_at = ?, closed_by = 'agent' WHERE serial = ?",
                 (old, "S-OLD-CLOSED"))
    conn.execute("UPDATE shrink_records SET closed_at = ?, closed_by = 'agent' WHERE serial = ?",
                 (recent, "S-NEW-CLOSED"))
    conn.execute("UPDATE shrink_records SET opened_at = ? WHERE serial = ?", (old, "S-OLD-OPEN"))
    conn.commit()
    result = housekeeping.purge_tables(conn, now)
    assert result["deleted"].get("shrink_records") == 1, result
    left = {r["serial"] for r in conn.execute("SELECT serial FROM shrink_records")}
    assert left == {"S-NEW-CLOSED", "S-OLD-OPEN"}       # an open record is the only memory (#926)
