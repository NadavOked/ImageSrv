"""#3277 -- the distribution card's link speed is read LIVE from every hello, and a sample that is old or from an earlier
boot is never "reported".

17/11 (argus 1d, cloner 2): `ethtool -s eth0 speed 100` after the first hello. The round opened two seconds later: the
journal said `1000Mb/s reported`, there was no precheck warning and no K3 LINK_SLOW; the round took 745 s against 425 s
predicted. The speed came from `probe.nic[].speed_mbps` -- cached by the agent for `PROBE_TTL` = 600 s, and kept by the
server at any age and from any boot (`probe.latest`).

Now the agent sends `nic_link` = `{"iface", "speed_mbps"}` in every hello (one sysfs read, outside the cache); the server
keeps it in memory (`server/rounds/nic_live.py`) and `nic_speed` prefers it. A missing field (an old agent) or a null
speed is "unknown", never 1000; a probe sample counts only inside `PROBE_MAX_AGE_S` and not from before the current boot.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from test_agent import AGENT, posix, sh  # noqa: E402 -- the helpers of the agent tests
from server import db  # noqa: E402
from server.agent import probe  # noqa: E402
from server.rounds import nic_live, nic_speed, room as room_mod  # noqa: E402
from test_server_room import (  # noqa: E402,F401 -- room_server is a fixture
    CLONER1, CLONER2, cloner_hello, room, room_body, room_server,
)

MAC = "aa:bb:cc:00:00:41"
BOOT_A = "11111111-2222-4333-8444-555555555555"
BOOT_B = "66666666-7777-4888-9999-000000000000"


@pytest.fixture(autouse=True)
def _forget_live_speeds():
    nic_live.forget()
    yield
    nic_live.forget()


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _probe(conn, mbps, mac=MAC):
    probe.record(conn, mac, probe.well_formed({"nic": [{"name": "eth0", "speed_mbps": mbps, "ip_conflict": None}]}))


# --- the in-memory registry ------------------------------------------------------------------------------

def test_the_live_speed_is_what_the_last_hello_said():
    nic_live.note(MAC, {"iface": "eth0", "speed_mbps": 1000}, BOOT_A, now=1000)
    assert nic_live.live(MAC, now=1001) == 1000
    nic_live.note(MAC, {"iface": "eth0", "speed_mbps": 100}, BOOT_A, now=1010)
    assert nic_live.live(MAC, now=1011) == 100


@pytest.mark.parametrize("field", [None, {}, "eth0", [], {"iface": "eth0"}, {"speed_mbps": None}, {"speed_mbps": -1},
                                   {"speed_mbps": 0}, {"speed_mbps": True}, {"speed_mbps": "1000"}, {"speed_mbps": 1.5e9}])
def test_a_missing_or_broken_field_is_unknown_and_not_the_last_good_value(field):
    nic_live.note(MAC, {"iface": "eth0", "speed_mbps": 1000}, BOOT_A, now=1000)
    nic_live.note(MAC, field, BOOT_A, now=1010)
    assert nic_live.live(MAC, now=1011) is None


def test_a_live_value_older_than_the_limit_is_not_counted():
    nic_live.note(MAC, {"iface": "eth0", "speed_mbps": 100}, BOOT_A, now=1000)
    assert nic_live.live(MAC, now=1000 + nic_live.LIVE_MAX_AGE_S) == 100
    assert nic_live.live(MAC, now=1000 + nic_live.LIVE_MAX_AGE_S + 1) is None


def test_a_new_boot_id_marks_when_the_boot_began_and_the_first_one_seen_does_not():
    nic_live.note(MAC, None, BOOT_A, now=1000)
    assert nic_live.boot_since(MAC) is None, "the first boot seen: the server does not know when it began"
    nic_live.note(MAC, None, BOOT_A, now=1010)
    assert nic_live.boot_since(MAC) is None
    nic_live.note(MAC, None, BOOT_B, now=1020)
    assert nic_live.boot_since(MAC) == 1020
    nic_live.note(MAC, None, "not-a-boot-id", now=1030)
    assert nic_live.boot_since(MAC) == 1020, "an unreadable id is not 'another boot'"


def test_the_registry_never_grows_without_a_limit_and_never_throws():
    for i in range(nic_live.MAX_MACHINES + 50):
        nic_live.note(f"aa:{i:010d}", {"speed_mbps": 100}, None, now=1000 + i)
    assert len(nic_live._machines) <= nic_live.MAX_MACHINES
    nic_live.note(MAC, object(), object(), now="x")     # garbage in every argument: a hello is never stopped


# --- nic_speed ---------------------------------------------------------------------------------------------

def test_the_link_that_dropped_after_the_first_hello_is_seen_by_the_next_one(conn):
    """The 17/11 case: the probe says 1000, the link is 100 now."""
    _probe(conn, 1000)
    assert nic_speed.view(conn, MAC) == {"speed_mbps": 1000, "source": "reported", "why": None}
    nic_live.note(MAC, {"iface": "eth0", "speed_mbps": 100}, BOOT_A)
    assert nic_speed.view(conn, MAC) == {"speed_mbps": 100, "source": "reported", "why": None}
    assert nic_speed.measured(conn, MAC) == (100, None)


def test_an_old_agent_with_a_fresh_probe_is_as_before(conn):
    _probe(conn, 2500)
    assert nic_speed.view(conn, MAC) == {"speed_mbps": 2500, "source": "reported", "why": None}


def test_a_probe_sample_that_is_too_old_is_assumed_and_not_reported(conn):
    _probe(conn, 100)
    later = time.time() + nic_speed.PROBE_MAX_AGE_S + 5
    assert nic_speed.view(conn, MAC, now=later) == {"speed_mbps": 1000, "source": "assumed", "why": "stale"}
    assert nic_speed.measured(conn, MAC, now=later) == (None, "stale")


def test_a_probe_sample_from_before_this_boot_is_assumed_and_not_reported(conn):
    _probe(conn, 1000)                                        # taken in the previous boot
    nic_live.note(MAC, None, BOOT_A, now=time.time() - 30)
    nic_live.note(MAC, None, BOOT_B, now=time.time() + 5)     # the machine came up again, after the sample
    assert nic_speed.view(conn, MAC) == {"speed_mbps": 1000, "source": "assumed", "why": "older_boot"}


def test_a_sample_without_a_readable_time_is_not_trusted(conn):
    sample = {"nic": [{"name": "eth0", "speed_mbps": 100, "ip_conflict": None}]}
    for bad in (None, "", "yesterday"):
        assert nic_speed.view(conn, MAC, sample, sampled=True, sampled_at=bad)["source"] == "assumed", bad
    assert nic_speed.view(conn, MAC, sample, sampled=True, sampled_at=db.now_iso())["source"] == "reported"


def test_nothing_known_is_assumed_and_never_called_reported(conn):
    assert nic_speed.view(conn, MAC) == {"speed_mbps": 1000, "source": "assumed", "why": "no_probe"}


def test_manual_still_wins_over_the_live_speed(conn):
    nic_live.note(MAC, {"iface": "eth0", "speed_mbps": 100}, BOOT_A)
    nic_speed.set_manual(conn, MAC, 10000)
    assert nic_speed.view(conn, MAC)["source"] == "manual"
    assert nic_speed.measured(conn, MAC) == (100, None), "the console's measured link is not hidden by a manual setting"


# --- the server, end to end: the hello carries it -------------------------------------------------------------

def _hello(anon, serials, **extra):
    from conftest import hello_body
    body = hello_body(CLONER1)
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "serial": s, "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1}
                     for i, s in enumerate(serials)]
    body.update(extra)
    r = anon.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_hello_with_a_live_100_after_a_probe_of_1000_warns_before_the_start(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _probe(ctx.conn, 1000, CLONER1)                            # what the cache said at the first hello
    # three drawers wanted, two hello: the wave stays open and the precheck is what the operator sees before "start"
    assert deploy.post("/api/console/room", json=room_body(room_server, 3, {CLONER1: ["S1", "S2"], CLONER2: ["T1"]})).status_code == 200
    _hello(anon, ["S1", "S2"], nic_link={"iface": "eth0", "speed_mbps": 100}, boot_id=BOOT_A)
    pre = room(deploy)["round"]["precheck"]
    assert pre and all(w.get("nic_mbps") == 100 and "100Mb/s" in w["text_he"] for w in pre), pre


def test_a_hello_without_the_field_changes_nothing_and_a_broken_one_does_not_fail(room_server):
    anon = room_server["anon"]
    _hello(anon, ["S1"])                                       # an old agent
    assert nic_live.live(CLONER1) is None
    for bad in ("fast", [1], {"speed_mbps": "x"}, {"speed_mbps": -1, "iface": "x" * 100}):
        _hello(anon, ["S1"], nic_link=bad)                     # 200, not 422/500
    assert nic_live.live(CLONER1) is None


# --- the agent: the field, from sysfs, outside the cache ----------------------------------------------------

def _agent_field(tmp_path: Path, speed_file: str | None, iface: str = "eth0") -> str:
    root = tmp_path / "root"
    (root / "sys/class/net" / "eth0").mkdir(parents=True, exist_ok=True)
    if speed_file is not None:
        (root / "sys/class/net/eth0/speed").write_text(speed_file, newline="\n")
    return sh(f'export SYSROOT={posix(root)!r} IFACE={iface!r}; . {posix(AGENT)}/lib/hw/sysinfo.sh; nic_link_json')


@pytest.mark.parametrize("speed_file, want", [
    ("1000\n", '"speed_mbps":1000'), ("100\n", '"speed_mbps":100'), ("2500", '"speed_mbps":2500'),
    ("-1\n", '"speed_mbps":null'), ("0\n", '"speed_mbps":null'), ("", '"speed_mbps":null'), ("abc\n", '"speed_mbps":null'),
    (None, '"speed_mbps":null'),
])
def test_the_agent_says_the_speed_it_reads_now_and_null_when_it_cannot(tmp_path, speed_file, want):
    out = _agent_field(tmp_path, speed_file)
    assert out.startswith(',"nic_link":{"iface":"eth0",') and want in out, out


def test_the_agent_sends_no_field_without_a_safe_interface(tmp_path):
    assert _agent_field(tmp_path, "100\n", iface="") == ""
    assert _agent_field(tmp_path, "100\n", iface="a b;c") == ""
