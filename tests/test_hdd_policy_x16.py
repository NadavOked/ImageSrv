"""X16 (Nadav 14/11) -- the "HDD support" switch, on the server.

Policy, not a threshold. Off (the default) = an HDD is a source only; as a
target it is refused with a visible reason. On = an HDD meets its own
expectation, learned from the measured waves of the same serial and model,
not the SSD floor. Not an HDD = exactly as before. ``rotational`` not read =
not measured: the non-rotational policy, with a log line (principle 5).

Negative control: ``server/disks/hdd_policy.py`` removed and
``server/rounds/repair.py``, ``server/app.py``, ``server/agent/api.py``,
``server/console/api.py``, ``server/console/journal_he.py``, ``server/db.py``
back to the base -> the import fails, the setting is refused as unknown, and the
manifest has no ``hdd_floor_mbps``.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import floorrule
from server.disks import hdd_policy as hdd
from server.rounds import repair

H = {"serial": "WD-H1", "model": "WDC WD10EZEX", "rotational": 1}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    c.close()


def _on(conn, value: str = "true") -> None:
    db.set_setting(conn, hdd.SETTING, value)


def _waves(conn, serial, model, mbps_list) -> None:
    for i, mbps in enumerate(mbps_list):
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?, ?, ?, 0, 0, ?, 10, '2026-11-15T00:00:00Z')",
                     (serial, model, f"s{serial}{model}{i}", mbps * 10_000_000))
    conn.commit()


def _inventory(conn, mac, disks) -> None:
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)", (mac, json.dumps(disks)))
    conn.commit()


def test_the_default_is_off(conn):
    assert db.get_setting(conn, hdd.SETTING) == "false"
    assert hdd.enabled(conn) is False
    assert hdd.decision(conn, H) == (hdd.REFUSED, None)


@pytest.mark.parametrize("support", [False, True])
@pytest.mark.parametrize("rotational", [0, 1, None, "1", True])
@pytest.mark.parametrize("role", ["source", "target"])
def test_the_matrix(conn, caplog, support, rotational, role):
    """Only a target HDD (rotational exactly 1) with the switch off is refused;
    anything not read is the SSD policy, and says so in the log."""
    _on(conn, "true" if support else "false")
    reason, floor = hdd.decision(conn, {"serial": "x", "rotational": rotational}, role=role)
    hdd_target = role == "target" and rotational == 1 and not isinstance(rotational, bool)
    if hdd_target and not support:
        assert (reason, floor) == (hdd.REFUSED, None)
    elif hdd_target:
        assert (reason, floor) == (None, floorrule.INITIAL_MBPS)     # no wave measured for "x": the estimate
    else:
        assert (reason, floor) == (None, 50)                   # the SSD floor (writecheck_min_mbps), unchanged
    unread = rotational not in (0, 1) or isinstance(rotational, bool)
    assert ("not read -- not measured" in caplog.text) == (unread and role == "target")


def test_the_ssd_floor_still_follows_its_setting(conn):
    db.set_setting(conn, "writecheck_min_mbps", "80")
    assert hdd.decision(conn, {"serial": "s", "rotational": 0}) == (None, 80)


def test_learning_is_per_serial_and_model_and_half_the_median(conn, caplog):
    _on(conn)
    _waves(conn, "WD-H1", "WDC WD10EZEX", [100, 120, 80])     # median 100 -> 50
    _waves(conn, "WD-H1", "OTHER", [400])                     # another model: not this disk's history
    _waves(conn, "WD-H2", "WDC WD10EZEX", [10])               # another serial: not either
    assert hdd.decision(conn, H) == (None, 50)
    assert hdd.expectation(conn, "WD-H1", "OTHER") == 200
    assert hdd.expectation(conn, "WD-H9", "WDC WD10EZEX") is None   # never measured = None, not 0
    assert hdd.decision(conn, {**H, "serial": "WD-H9"}) == (None, floorrule.INITIAL_MBPS)
    assert "no measured wave -- the starting estimate" in caplog.text


def test_only_the_last_window_of_waves_counts(conn):
    _on(conn)
    _waves(conn, "WD-H1", "WDC WD10EZEX", [400] * 5 + [60] * floorrule.WINDOW)   # the old fast ones fall out
    assert hdd.expectation(conn, "WD-H1", "WDC WD10EZEX") == 30


def test_the_manifest_carries_only_learned_hdds_and_the_estimate(conn):
    assert hdd.manifest(conn) == {"hdd_floor_mbps": 20, "hdd_floors": {}}
    _on(conn)
    _inventory(conn, "aa:bb:cc:00:00:01", [H, {"serial": "SSD1", "model": "S", "rotational": 0},
                                           {"serial": "NEW", "model": "N", "rotational": 1}])
    _waves(conn, "WD-H1", "WDC WD10EZEX", [90])
    _waves(conn, "SSD1", "S", [500])
    assert hdd.manifest(conn)["hdd_floors"] == {"WD-H1": {"WDC WD10EZEX": 45}}


def test_the_manifest_the_agent_pulls(server):
    anon, admin = server["anon"], server["admin"]
    served = anon.get("/api/v1/images/img_7f3a91/manifest").json()
    assert served["hdd_floor_mbps"] == 20 and served["hdd_floors"] == {}, served
    assert "hdd_support" not in served, "the switch travels in the hello answer (#3268), not twice"


def test_the_setting_is_a_boolean_and_admin_only(server):
    admin, deploy = server["admin"], server["deploy"]
    assert admin.get("/api/console/settings").json()["hdd_support"] == "false"
    assert admin.post("/api/console/settings", json={"hdd_support": "true"}).status_code == 200
    assert admin.get("/api/console/settings").json()["hdd_support"] == "true"
    for bad in ("yes", 1, "20"):
        assert admin.post("/api/console/settings", json={"hdd_support": bad}).status_code == 400
    assert deploy.post("/api/console/settings", json={"hdd_support": "false"}).status_code == 403
    assert admin.get("/api/console/settings").json()["hdd_support"] == "true"


# --- the chunk engine's repair: repair.plan through RepairFloor -----------------

def _plan(conn):
    """30 of 100 chunks durable, 70 skipped, asked 100 MB/s: each disk wrote ~30 MB/s."""
    floor = hdd.RepairFloor(conn)
    assert int(floor) == 50 and floor * 8000 == 400_000     # the sender's numeric contract
    return repair.plan(100, {"aa:bb:cc:00:00:01": {"WD-H1": [(0, 29)], "SSD1": [(0, 29)]}},
                       repairs_done=0, skipped={"aa:bb:cc:00:00:01": {"WD-H1": 70, "SSD1": 70}},
                       asked_mbps=100, floor_mbps=floor)


def test_repair_off_refuses_the_hdd_and_the_ssd_is_as_before(conn):
    _inventory(conn, "aa:bb:cc:00:00:01", [H, {"serial": "SSD1", "model": "S", "rotational": 0}])
    p = _plan(conn)
    assert p.failed[("aa:bb:cc:00:00:01", "WD-H1")] == hdd.REFUSED
    # #3170: a slow disk is not failed -- its gap is completed, and the slowness is recorded in Plan.slow
    assert ("aa:bb:cc:00:00:01", "SSD1") not in p.failed
    assert "(50MB/s)" in p.slow[("aa:bb:cc:00:00:01", "SSD1")]


def test_repair_on_judges_the_hdd_by_its_own_expectation(conn):
    _on(conn)
    _inventory(conn, "aa:bb:cc:00:00:01", [H, {"serial": "SSD1", "model": "S", "rotational": 0}])
    p = _plan(conn)                                           # no history: 20, and 30 >= 20
    assert ("aa:bb:cc:00:00:01", "WD-H1") not in p.failed
    assert ("aa:bb:cc:00:00:01", "SSD1") in p.slow            # 30 < 50: unchanged (#3170: slow, not failed)
    assert ("aa:bb:cc:00:00:01", "SSD1") not in p.failed
    assert p.fetch or p.multicast                             # the HDD's gap is repaired
    _waves(conn, "WD-H1", "WDC WD10EZEX", [100])              # it once wrote 100: now 50 is expected of it
    slow_now = _plan(conn)
    assert "(50MB/s)" in slow_now.slow[("aa:bb:cc:00:00:01", "WD-H1")]
    assert ("aa:bb:cc:00:00:01", "WD-H1") not in slow_now.failed


def test_a_complete_hdd_is_not_failed_after_the_fact(conn):
    _inventory(conn, "aa:bb:cc:00:00:01", [H])
    p = repair.plan(100, {"aa:bb:cc:00:00:01": {"WD-H1": [(0, 99)]}}, repairs_done=0,
                    floor_mbps=hdd.RepairFloor(conn))
    assert p.failed == {} and p.done


def test_not_in_the_inventory_or_not_read_is_the_ssd_policy_and_logged(conn, caplog):
    _inventory(conn, "aa:bb:cc:00:00:01", [{"serial": "OLD", "model": "M"}])   # an agent before X16
    floor = hdd.RepairFloor(conn)
    assert floor.for_disk("aa:bb:cc:00:00:01", "OLD") == (None, 50)
    assert floor.for_disk("aa:bb:cc:00:00:02", "GONE") == (None, 50)
    assert "rotational None not read" in caplog.text and "not in the inventory" in caplog.text


def test_the_numeric_uses_do_not_read_the_inventory(conn, monkeypatch):
    """_write_kbps and _fail_slow_fetches call write_floor() often: building one
    must not read net_devices -- only plan's for_disk does, once per disk."""
    _inventory(conn, "aa:bb:cc:00:00:01", [H])
    reads = []
    real = hdd._inventory
    monkeypatch.setattr(hdd, "_inventory", lambda c, mac=None: reads.append(mac) or real(c, mac))
    floor = hdd.RepairFloor(conn)
    assert int(floor) == 50 and reads == []
    floor.for_disk("aa:bb:cc:00:00:01", "WD-H1")
    floor.for_disk("aa:bb:cc:00:00:01", "WD-H1")
    assert reads == ["aa:bb:cc:00:00:01"]
