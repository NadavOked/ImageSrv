"""#1989: RAID arrays on the health page -- /proc/mdstat is the evidence.

תקין / פגוע / בבנייה מחדש (עם אחוז); an array the installer created and that
is not in mdstat is red, and an unreadable mdstat is never "no arrays".
"""
from __future__ import annotations

import pytest

from server.system import raid_status
from test_server_health import health_server  # noqa: F401 -- the fixture

HEALTHY = """Personalities : [raid1] [raid6] [raid5] [raid4]
md0 : active raid1 sdb2[1] sda2[0]
      976630464 blocks super 1.2 [2/2] [UU]
      bitmap: 1/8 pages [4KB], 65536KB chunk

unused devices: <none>
"""

DEGRADED = """Personalities : [raid1]
md0 : active raid1 sda2[0] sdb2[1](F)
      976630464 blocks super 1.2 [2/1] [U_]

unused devices: <none>
"""

MISSING_NO_FLAG = """Personalities : [raid5]
md0 : active raid5 sdc2[2] sda2[0]
      1953260544 blocks super 1.2 level 5, 512k chunk, algorithm 2 [3/2] [U_U]

unused devices: <none>
"""

REBUILDING = """Personalities : [raid6] [raid5] [raid4]
md0 : active raid5 sdd2[3] sdc2[1] sda2[0]
      1953260544 blocks super 1.2 level 5, 512k chunk, algorithm 2 [3/2] [UU_]
      [=>...................]  recovery =  8.5% (83034112/976630272) finish=79.3min speed=187776K/sec

unused devices: <none>
"""

SPARE_TOOK_OVER = """Personalities : [raid1]
md0 : active raid1 sdc2[2] sdb2[1](F) sda2[0]
      976630464 blocks super 1.2 [2/2] [UU]

unused devices: <none>
"""

WITH_SPARE = """Personalities : [raid1]
md0 : active raid1 sdc2[2](S) sdb2[1] sda2[0]
      976630464 blocks super 1.2 [2/2] [UU]

unused devices: <none>
"""

INACTIVE = """Personalities : [raid10]
md0 : inactive sdb2[1](S) sda2[0](S)
      1953260544 blocks super 1.2

unused devices: <none>
"""

NO_ARRAYS = "Personalities : \nunused devices: <none>\n"
DECLARED = "level=raid1\ndisks=/dev/sda,/dev/sdb\nspares=0\narray=/dev/md0\n"


def _rows(mdstat, declared):
    def reader(value):
        def read():
            if isinstance(value, Exception):
                raise value
            return value
        return read
    return raid_status.health_checks(reader(mdstat), reader(declared))


def _one(mdstat, declared=DECLARED):
    rows = _rows(mdstat, declared)
    assert len(rows) == 1, rows
    return rows[0]


def test_a_healthy_mirror_is_green():
    row = _one(HEALTHY)
    assert row["id"] == "raid_md0" and row["state"] == "ok"
    assert "RAID1" in row["detail"] and "2/2" in row["detail"] and "תקין" in row["detail"]


@pytest.mark.parametrize("text", [DEGRADED, MISSING_NO_FLAG, SPARE_TOOK_OVER, INACTIVE])
def test_a_degraded_or_failed_array_is_red(text):
    assert _one(text)["state"] == "bad"


def test_a_failed_member_is_named_for_replacement():
    row = _one(DEGRADED)
    assert "sdb2" in row["detail"] and "להחליף" in row["detail"]
    # a spare that took over leaves a full array -- the failed disk is still red
    row = _one(SPARE_TOOK_OVER)
    assert row["state"] == "bad" and "sdb2" in row["detail"]


def test_a_rebuild_is_orange_with_its_percent():
    row = _one(REBUILDING)
    assert row["state"] == "warn" and "בבנייה מחדש 8.5%" in row["detail"]


def test_a_waiting_spare_is_green_and_named():
    row = _one(WITH_SPARE)
    assert row["state"] == "ok" and "רזרבי: sdc2" in row["detail"]


def test_an_array_the_installer_made_but_mdstat_lacks_is_red():
    rows = _rows(NO_ARRAYS, DECLARED)
    assert [r["state"] for r in rows] == ["bad"] and "md0" in rows[0]["detail"]
    rows = _rows(None, DECLARED)   # no /proc/mdstat at all: md never loaded
    assert [r["state"] for r in rows] == ["bad"]


def test_an_unreadable_mdstat_is_never_no_arrays():
    rows = _rows(PermissionError("denied"), DECLARED)
    assert [r["state"] for r in rows] == ["bad"]
    rows = _rows(PermissionError("denied"), None)
    assert [r["state"] for r in rows] == ["unknown"]


def test_no_raid_declared_and_no_arrays_is_no_row():
    assert _rows(None, None) == []
    assert _rows(NO_ARRAYS, None) == []
    assert _rows(NO_ARRAYS, "level=none\n") == []


def test_an_array_made_by_hand_is_shown_without_a_declaration():
    assert _one(HEALTHY, None)["state"] == "ok"


def test_hooks_not_injected_add_no_row():
    assert raid_status.health_checks(None, None) == []


def test_the_health_page_carries_the_raid_row_from_the_hooks(health_server):
    """health.collect, the page's own list -- not only the module."""
    from server.system import health
    from test_server_health import SS_DNSMASQ
    base = {**health.default_hooks(),
            "ss": lambda: SS_DNSMASQ, "unit_active": lambda n: "active",
            "http_get": lambda u: 200, "http_size": lambda u: (200, 31_000_000),
            "interfaces": lambda: [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
            "tftp_root": lambda: health_server["tftp"], "udp_sender_pids": lambda: []}
    ctx = health_server["admin"].app.state.ctx
    rows = {r["id"]: r for r in health.collect(ctx, {**base, "mdstat": lambda: DEGRADED,
                                                      "raid_declared": lambda: DECLARED}, "http://x")}
    assert rows["raid_md0"]["state"] == "bad"
    rows = {r["id"]: r for r in health.collect(ctx, {**base, "mdstat": lambda: None,
                                                      "raid_declared": lambda: None}, "http://x")}
    assert not any(k.startswith("raid") for k in rows)
