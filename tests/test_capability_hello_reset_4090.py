"""#4090 -- a hello that reports a different link speed throws away the learned capability of a cloner, at once -- not at the end of the next wave.

Metal 24/11 (cloner 2): `ethtool -s eth0 speed 100` taught the brain "the machine held the wave back at 90Mb/s". The card then went back
to 1000Mb/s, and the next two waves still warned "link 100Mb" and predicted 43 minutes (they took 8 and 19): the reset
(`capability_reset`, LR24/RESET) came only at the end of the SECOND wave. The rule (Zeus, a technical rule of the brain, not a policy):
when a hello reports a link speed different from the one the capability was kept against, it resets IMMEDIATELY -- before the round's
precheck and before the next wave's ETA. The end-of-wave reset stays, as a safety net. A hello with no speed (an old agent, link down,
unreadable sysfs) resets nothing: "not read" is not "changed" (principle 5).

The numbers are numbers for the test (a 90Mb/s capability learned on a 100Mb/s link), not measurements.

**Negative control:** with `server/agent/api.py` and `server/brain/capability.py` back at the base of this PR (`reset_on_hello` gone and
the hello not calling it), the (neg) tests fail on behaviour: through the real `hello` the learned capability is still 90Mb/s after a
hello that read 1000Mb/s, no `RESET` row is written, and the precheck still says `nic_mbps == 100`. The tests that call `reset_on_hello`
directly fail there on the attribute -- that is NOT a negative control, and they are not counted.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server import db  # noqa: E402
from server.brain import capability as C, memory, veto  # noqa: E402
from server.rounds import nic_live, nic_speed  # noqa: E402
from test_nic_live_3277 import BOOT_A, _hello  # noqa: E402
from test_server_room import CLONER1, CLONER2, room, room_body, room_server  # noqa: E402,F401 -- room_server is a fixture

LEARNED_BPS = 90 * 10 ** 6          # "the machine held it back at 90Mb/s", learned while the link was 100Mb/s


@pytest.fixture(autouse=True)
def _forget():
    nic_live.forget()
    veto.forget()
    yield
    nic_live.forget()
    veto.forget()


def _taught_on_100(conn, mac=CLONER1, link=100):
    """What `capability.learn` keeps after a wave on a 100Mb/s link: a capability of 90Mb/s, the link it was learned against."""
    assert memory.remember(conn, "machine", mac, "capability", {"limited_bps": float(LEARNED_BPS), "link_mbps": float(link)},
                           {"iface": "eth0"})
    assert C.learned(conn, mac) == LEARNED_BPS


def _resets(conn, mac=CLONER1):
    return [d for d in memory.decisions(conn, kind="machine", key=mac, rule="LR24", limit=50)
            if d["action"] == "MACHINE_CAPABILITY" and d["verdict"] == "RESET"]


def _nic_precheck(deploy):
    return [w for w in (room(deploy)["round"]["precheck"] or []) if w.get("nic_mbps") is not None]


def _open_round(room_server):
    deploy = room_server["deploy"]
    assert deploy.post("/api/console/room", json=room_body(room_server, 3, {CLONER1: ["S1", "S2"], CLONER2: ["T1"]})).status_code == 200
    return deploy


# --- the server: the hello carries the speed, the reset happens before anything reads it -------------------------------------


def test_a_hello_that_reads_1000_after_a_capability_learned_on_100_resets_it_before_the_precheck_and_the_eta(room_server):
    """(neg) Metal 24/11: the card is back on 1000Mb/s. The very next hello drops the 90Mb/s capability -- the precheck of the round that is
    being opened has no `nic=100` warning, the wave rate no longer comes from 90Mb/s, and the brain says why."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    deploy = _open_round(room_server)
    _taught_on_100(ctx.conn)
    _hello(anon, ["S1", "S2"], nic_link={"iface": "eth0", "speed_mbps": 100}, boot_id=BOOT_A)     # still on 100: what it learned is true
    assert C.learned(ctx.conn, CLONER1) == LEARNED_BPS and _resets(ctx.conn) == []
    assert [w["nic_mbps"] for w in _nic_precheck(deploy)] == [100, 100], "the slow link IS what the operator should see now"

    _hello(anon, ["S1", "S2"], nic_link={"iface": "eth0", "speed_mbps": 1000}, boot_id=BOOT_A)    # the cable was fixed

    assert C.learned(ctx.conn, CLONER1) is None, "the capability learned on the 100Mb/s link is gone"
    assert _nic_precheck(deploy) == [], room(deploy)["round"]["precheck"]
    [reset] = _resets(ctx.conn)
    assert "100 -> 1000Mb/s" in reset["reason"] and reset["mode"] == "shadow", reset
    assert f"was_bps={LEARNED_BPS}" in reset["evidence"] and "link_mbps=1000" in reset["evidence"] and "seen_at=hello" in reset["evidence"]
    [line] = ctx.conn.execute("SELECT detail, machine FROM journal WHERE event = 'capability_reset'").fetchall()
    assert line["machine"] == CLONER1 and "100 -> 1000Mb/s" in line["detail"]
    # the wave ETA reads the same capability: nothing to predict from, the wave goes out at the card's speed
    assert C.wave_rate(ctx.conn, "ses_next", 900 * 10 ** 6, [CLONER1]) is None


def test_the_reset_keeps_the_new_link_so_the_end_of_the_wave_does_not_reset_a_second_time(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    _taught_on_100(ctx.conn)
    _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 1000}, boot_id=BOOT_A)
    assert memory.summary(ctx.conn, "machine", CLONER1)["capability"]["num"]["link_mbps"]["last"] == 1000
    assert memory.summary(ctx.conn, "machine", CLONER1)["capability"]["text"]["iface"]["value"] == "eth0"
    for _ in range(3):                                      # a hello is the poll: the same speed again and again is not a change
        _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 1000}, boot_id=BOOT_A)
    assert len(_resets(ctx.conn)) == 1


@pytest.mark.parametrize("link", [None, "fast", {"iface": "eth0"}, {"iface": "eth0", "speed_mbps": None},
                                  {"iface": "eth0", "speed_mbps": -1}, {"iface": "eth0", "speed_mbps": "1000"}])
def test_a_hello_that_did_not_read_the_speed_resets_nothing(room_server, link):
    """(neg) "Not read" (an old agent with no `nic_link`, link down = -1, unreadable sysfs) is not "changed": the capability stays and no row is
    written. The positive half is in the test above; this one proves the guard is not "reset on every hello"."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    _taught_on_100(ctx.conn)
    _hello(anon, ["S1"], **({} if link is None else {"nic_link": link}), boot_id=BOOT_A)
    assert C.learned(ctx.conn, CLONER1) == LEARNED_BPS
    assert _resets(ctx.conn) == []
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'capability_reset'").fetchone()[0] == 0


def test_a_hello_does_not_judge_a_speed_the_operator_chose_by_hand(room_server):
    """A manual speed (`nic_speed.manual`) is the operator's, and the capability is kept against it: the hello must not fight it every few seconds."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    _taught_on_100(ctx.conn, link=1000)
    nic_speed.set_manual(ctx.conn, CLONER1, 1000)
    _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 100}, boot_id=BOOT_A)
    assert C.learned(ctx.conn, CLONER1) == LEARNED_BPS and _resets(ctx.conn) == []


def test_with_the_brain_off_a_hello_resets_nothing_and_writes_nothing(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    _taught_on_100(ctx.conn)
    db.set_setting(ctx.conn, "brain_veto_mode", "off")
    veto.forget()
    _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 1000}, boot_id=BOOT_A)
    assert C.learned(ctx.conn, CLONER1) == LEARNED_BPS and _resets(ctx.conn) == []


def test_a_machine_that_learned_nothing_gets_no_row_from_a_hello(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 1000}, boot_id=BOOT_A)
    assert memory.summary(ctx.conn, "machine", CLONER1).get("capability") is None and _resets(ctx.conn) == []


# --- the function itself (not counted as a control: it does not exist at the base) ---------------------------------------------


def test_the_end_of_the_wave_reset_is_still_there_as_a_safety_net_after_a_hello_that_was_missed(tmp_path):
    """The hello never carried the new speed (an old agent that reads the speed only in the probe): the old end-of-wave check still resets."""
    from test_brain_capability_3041 import MAC, _cap, _rows, _wave
    conn = db.connect(tmp_path / "imagesrv.db")
    nic_speed.set_manual(conn, MAC, 1000)
    _wave(conn, "ses_1", bps=LEARNED_BPS, rcvbuf=9)
    assert C.learned(conn, MAC) == LEARNED_BPS and _cap(conn)["num"]["link_mbps"]["last"] == 1000
    nic_speed.set_manual(conn, MAC, 2500)
    _wave(conn, "ses_2", bps=LEARNED_BPS, rcvbuf=9)
    [reset] = [r for r in _rows(conn) if r["verdict"] == "RESET"]
    assert "1000 -> 2500Mb/s" in reset["reason"] and "seen_at=wave_end" in reset["evidence"], reset


def test_reset_on_hello_never_raises_and_ignores_a_speed_that_is_not_a_number(tmp_path):
    conn = db.connect(tmp_path / "imagesrv.db")
    _taught_on_100(conn)
    for bad in (None, 0, -1):
        C.reset_on_hello(conn, CLONER1, bad)
    conn.close()
    C.reset_on_hello(conn, CLONER1, 1000)                   # a closed connection: logged, not raised
