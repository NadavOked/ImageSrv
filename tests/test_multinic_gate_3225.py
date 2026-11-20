"""#3225: a machine registered by another card (#524) passes the deploy gate and `source_gate`.

The machine's boot card A is the one the DHCP leased; the operator registered its other card B.
`hello` finds it by `all_macs` (#524) -- but the gate (`deploy_gate`) and `source_gate` read only
the lease holder's MAC (A), which is not in `machines`: 403 `deploy_unregistered` /
`identity_refused` on every path except hello and /boot.

The fix is evidence, not trust in a list the caller sends (#2439): a hello that passed
`identity_gate` (A is leased to the caller's address) AND `verify_extra` (B is not somewhere
else) records "A is a card of B, from this address". The gate accepts A only from that address,
for `ALIAS_SECONDS` after the last such hello, and only while B is still registered.

Negative control: see the PR body -- every production file the change touched was reverted.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import hello_body
from server.agent import deploy_gate, identity, registry
from server.app import create_agent_app, create_runtime
from server.db import get_setting, set_setting, update_one, writing
from test_machine_identity import write_leases

VLAN = "10.44.12.10"
A, A_IP = "b4:2e:99:07:1a:c4", "10.44.12.21"        # the boot card: leased, NOT registered
B = "b4:2e:99:07:1a:c5"                             # the registered card: no lease anywhere
STRANGER, STRANGER_IP = "b4:2e:99:07:1a:d0", "10.44.12.30"
OTHER_A_IP = "10.44.12.55"
CLONERS = "grp_CLONERS"
ALIAS_KEY = "nic_alias:"                            # the setting the evidence is kept in
ALIAS_TTL = 6 * 3600                                # how long a hello is evidence (identity.ALIAS_SECONDS)
ACTIVE = "/api/v1/agent/sessions/active"            # a `source_gate` route with no image (#2489)


@pytest.fixture()
def env(tmp_path: Path, images_root: Path):
    from test_sender import Recorder                       # noqa: PLC0415

    leases = write_leases(tmp_path / "dnsmasq.leases", {A: A_IP, STRANGER: STRANGER_IP})
    rt = create_runtime(tmp_path / "data", images_root, f"http://{VLAN}:8080",
                        sender_runner=Recorder(block=True), wol_send=lambda _p: None,
                        identity_hooks={"leases": identity.LeaseFile(leases)})
    registry.add_machine(rt.conn, B, "cloner-1", CLONERS, "test")
    agent_app = create_agent_app(rt)

    def client(ip: str) -> TestClient:
        return TestClient(agent_app, base_url=f"http://{VLAN}:8080", client=(ip, 40000),
                          raise_server_exceptions=False)

    yield {"rt": rt, "conn": rt.conn, "client": client, "leases": leases}
    rt.ctx.sender.stop()


def _hello(client: TestClient, mac: str, all_macs: list[str]):
    return client.post("/api/v1/agent/hello", json={**hello_body(mac), "all_macs": all_macs})


def _alias(conn, mac: str) -> dict | None:
    raw = get_setting(conn, ALIAS_KEY + mac)
    return json.loads(raw) if raw else None


def _age(conn, mac: str, seconds: int) -> None:
    doc = _alias(conn, mac)
    doc["at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    set_setting(conn, ALIAS_KEY + mac, json.dumps(doc))


def test_before_any_hello_the_boot_card_is_not_registered_and_the_gate_is_closed(env):
    r = env["client"](A_IP).get(ACTIVE)
    assert r.status_code == 403 and r.json()["code"] == deploy_gate.EVENT


def test_a_hello_that_identifies_the_machine_by_its_other_card_opens_the_gate_and_source_gate(env):
    c = env["client"](A_IP)
    hello = _hello(c, A, [A, B])
    assert hello.status_code == 200 and hello.json()["known"] is True, hello.text
    assert hello.json()["name"] == "cloner-1"
    # both layers read the same lease: the deploy gate first, then `source_gate` inside the route
    r = c.get(ACTIVE)
    assert r.status_code == 200, r.text
    assert _alias(env["conn"], A)["machine"] == B


def test_the_manifest_path_is_not_refused_as_identity_refused_any_more(env):
    """#2657 still wants an open round with the image (`image_fetch_refused`); what must be gone is
    the 403 `identity_refused` / `deploy_unregistered` for a lease holder that is not registered."""
    c = env["client"](A_IP)
    _hello(c, A, [A, B])
    r = c.get("/api/v1/images/img_1/manifest")
    assert r.status_code == 403 and r.json()["code"] == "image_fetch_refused", r.text


def test_all_macs_in_a_list_nobody_verified_is_not_evidence(env):
    """The stranger never sent a hello at all: naming B in a later request body changes nothing."""
    r = env["client"](STRANGER_IP).get(ACTIVE)
    assert r.status_code == 403 and r.json()["code"] == deploy_gate.EVENT


def test_a_card_that_names_no_registered_card_is_not_a_card_of_anything(env):
    c = env["client"](STRANGER_IP)
    hello = _hello(c, STRANGER, [STRANGER, "b4:2e:99:07:1a:ee"])
    assert hello.json()["known"] is False
    assert _alias(env["conn"], STRANGER) is None
    assert c.get(ACTIVE).status_code == 403


def test_a_registered_card_that_is_somewhere_else_now_is_dropped_by_verify_extra_and_learns_nothing(env):
    """#2439 stays: B leased to another address is the real machine -- the caller is not it."""
    write_leases(env["leases"], {A: A_IP, STRANGER: STRANGER_IP, B: "10.44.12.99"})
    c = env["client"](STRANGER_IP)
    assert _hello(c, STRANGER, [STRANGER, B]).json()["known"] is False
    assert _alias(env["conn"], STRANGER) is None
    assert c.get(ACTIVE).status_code == 403


def test_the_evidence_is_for_the_address_it_was_given_from(env):
    c = env["client"](A_IP)
    _hello(c, A, [A, B])
    assert c.get(ACTIVE).status_code == 200
    # the DHCP gives the same card another address: the old hello says nothing about it
    write_leases(env["leases"], {A: OTHER_A_IP})
    moved = env["client"](OTHER_A_IP)
    assert moved.get(ACTIVE).status_code == 403
    assert _hello(moved, A, [A, B]).json()["known"] is True
    assert moved.get(ACTIVE).status_code == 200


def test_the_evidence_expires_and_a_new_hello_renews_it(env):
    c = env["client"](A_IP)
    _hello(c, A, [A, B])
    _age(env["conn"], A, ALIAS_TTL + 60)
    assert c.get(ACTIVE).status_code == 403
    _hello(c, A, [A, B])
    assert c.get(ACTIVE).status_code == 200


def test_a_machine_that_was_deleted_is_closed_at_once_even_with_fresh_evidence(env):
    c = env["client"](A_IP)
    _hello(c, A, [A, B])
    assert c.get(ACTIVE).status_code == 200
    with writing(env["conn"]):
        assert update_one(env["conn"], "DELETE FROM machines WHERE mac = ?", (B,))
    assert c.get(ACTIVE).status_code == 403


@pytest.mark.parametrize("raw", ["not json", "[]", '{"machine": 5, "ip": "x", "at": "x"}',
                                 '{"machine": "%s", "ip": "%s", "at": "2026-11-14T10:00:00"}' % (B, A_IP)],
                         ids=["not-json", "not-a-dict", "wrong-types", "naive-timestamp"])
def test_a_damaged_record_is_not_registered(env, raw):
    """Principle 5: a record that cannot be read is no evidence, whatever else it says."""
    set_setting(env["conn"], ALIAS_KEY + A, raw)
    assert env["client"](A_IP).get(ACTIVE).status_code == 403


def test_a_machine_registered_by_its_boot_card_writes_no_record_and_the_journal_stays_quiet(env):
    registry.add_machine(env["conn"], STRANGER, "cloner-2", CLONERS, "test")
    c = env["client"](STRANGER_IP)
    assert _hello(c, STRANGER, [STRANGER, B]).json()["known"] is True
    assert _alias(env["conn"], STRANGER) is None


def test_hellos_in_a_row_write_once_and_say_it_once(env):
    c = env["client"](A_IP)
    for _ in range(5):
        _hello(c, A, [A, B])
    lines = env["conn"].execute("SELECT detail FROM journal WHERE event = 'nic_alias_learned'").fetchall()
    assert len(lines) == 1 and A in lines[0]["detail"] and B in lines[0]["detail"]
    first = _alias(env["conn"], A)["at"]
    _hello(c, A, [A, B])
    assert _alias(env["conn"], A)["at"] == first          # inside the refresh window: not rewritten


def test_with_the_identity_switch_off_nothing_is_learned(env):
    """Without a lease check there is no evidence about the address -- and no gate to open either."""
    set_setting(env["conn"], "identity_check", "false")
    _hello(env["client"](A_IP), A, [A, B])
    assert _alias(env["conn"], A) is None


def test_the_journal_event_has_a_hebrew_label():
    from server.console import journal_he                                 # noqa: PLC0415

    assert journal_he.EVENTS_HE["nic_alias_learned"]
