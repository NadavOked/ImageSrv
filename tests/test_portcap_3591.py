"""#3591 ב'1 (Nadav 17/11: "וזה המוח צריך לדעת ולהכיר") -- the brain learns what each SATA port can do, per machine model and per
the link seen at the port over time -- not by the disk's ability.

A port that is always 3.0 is a property of the board (Q77: SATA 0/1 are 6.0, SATA 2/3 are 3.0). A cable is suspected only when a
port that SAW a higher speed drops from it (or a CRC rises): the one evidence of a cable that the speed gives.

Layers: ``portcap.capability`` / ``drop`` / ``ceiling_mbps`` (pure over ``port_history``), the drawer's fields and the precheck
warning (``room_precheck``), the journal line (``drop=6>3``) and the console's kind (``precheckSigns``).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from console_src import console_js
from native import requires_native
from server import db
from server.agent import inventory
from server.console import journal_he
from server.learn import portcap
from server.rounds import room as room_mod, room_precheck

MAC, MATE, OTHER_MODEL = "aa:bb:cc:00:00:51", "aa:bb:cc:00:00:52", "aa:bb:cc:00:00:53"
NODE = shutil.which("node")
_n = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    for mac, model in ((MAC, "HP Compaq Elite 8300 CMT"), (MATE, "HP Compaq Elite 8300 CMT"), (OTHER_MODEL, "Dell OptiPlex 790")):
        inventory.record(c, mac, inventory.well_formed({"dmi": {"product_name": model}, "pci": []}))
    return c


def seen(conn, mac, port, link, *, waves=1, crc=None):
    """`waves` rows of port_history: the link the machine's hello said at the end of that many waves."""
    for _ in range(waves):
        conn.execute("INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps, link_max_gbps, cable_fail, at)"
                     " VALUES (?,?,?,?,?,?,?,?,?)", (f"ses_{next(_n):05d}", mac, port, f"S{next(_n)}", crc, link, 6.0, 0, db.now_iso()))
    conn.commit()


# --- the learning ----------------------------------------------------------------------------------------------

def test_a_port_never_measured_is_unknown_not_slow_and_not_fine(conn):
    got = portcap.capability(conn, MAC, 3)
    assert got["gbps"] is None and got["known"] is False and got["basis"] is None, got
    assert portcap.drop(got, 3.0, 6.0) is None, "no capability: cannot judge a drop either way"


@pytest.mark.parametrize("port", [None, True, -1, "3", 2.5])
def test_a_port_that_is_not_a_port_number_is_unknown(conn, port):
    assert portcap.capability(conn, MAC, port)["gbps"] is None


def test_one_read_of_six_proves_six(conn):
    seen(conn, MAC, 1, 6.0)
    got = portcap.capability(conn, MAC, 1)
    assert (got["gbps"], got["basis"], got["known"], got["waves"]) == (6.0, "machine", True, 1), got


def test_a_port_that_always_read_three_is_three_only_after_enough_waves(conn):
    seen(conn, MAC, 3, 3.0)
    one = portcap.capability(conn, MAC, 3)
    assert (one["gbps"], one["known"]) == (3.0, False), "once at 3.0 can be a cable: a lower bound, not the board's limit"
    seen(conn, MAC, 3, 3.0, waves=portcap.MIN_WAVES - 1)
    got = portcap.capability(conn, MAC, 3)
    assert (got["gbps"], got["known"], got["waves"]) == (3.0, True, portcap.MIN_WAVES), got


def test_waves_that_dropped_from_the_top_are_no_evidence_of_the_capability(conn):
    seen(conn, MAC, 3, 3.0)
    seen(conn, MAC, 3, 1.5, waves=5)
    got = portcap.capability(conn, MAC, 3)
    assert (got["gbps"], got["known"]) == (3.0, False), got


def test_the_highest_link_seen_is_the_capability_whatever_came_later(conn):
    seen(conn, MAC, 2, 6.0)
    seen(conn, MAC, 2, 3.0, waves=4)
    assert portcap.capability(conn, MAC, 2)["gbps"] == 6.0


def test_a_machine_of_the_same_model_that_saw_more_teaches_this_one(conn):
    seen(conn, MAC, 4, 3.0, waves=2)
    seen(conn, MATE, 4, 6.0)
    got = portcap.capability(conn, MAC, 4)
    assert (got["gbps"], got["basis"], got["known"], got["machines"]) == (6.0, "model", True, 2), got
    assert got["model"] == "HP Compaq Elite 8300 CMT"


def test_a_machine_of_another_model_teaches_nothing(conn):
    seen(conn, OTHER_MODEL, 4, 6.0)
    seen(conn, MAC, 4, 3.0)
    assert portcap.capability(conn, MAC, 4)["gbps"] == 3.0


def test_a_machine_with_no_model_learns_only_from_itself(tmp_path):
    c = db.connect(tmp_path / "x.db")              # no inventory: no model
    seen(c, MAC, 4, 3.0)
    seen(c, MATE, 4, 6.0)
    got = portcap.capability(c, MAC, 4)
    assert (got["gbps"], got["model"]) == (3.0, None), got


def test_the_disks_own_ceiling_is_not_read_here(conn):
    # port_history keeps link_max_gbps (the disk's and controller's ceiling); the port's capability is what it DELIVERED
    conn.execute("INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps, link_max_gbps, cable_fail, at)"
                 " VALUES ('s1', ?, 5, 'S', NULL, 3.0, 6.0, 0, ?)", (MAC, db.now_iso()))
    conn.commit()
    assert portcap.capability(conn, MAC, 5)["gbps"] == 3.0


# --- the drop ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cap, now, disk_max, want", [
    (6.0, 3.0, 6.0, True),        # a port that saw 6.0 now at 3.0: the cable
    (6.0, 1.5, None, True),
    (6.0, 6.0, 6.0, False),
    (3.0, 3.0, 6.0, False),       # always 3.0: the board's port, not a fault
    (3.0, 1.5, 6.0, True),        # a 3.0 port that dropped
    (6.0, 3.0, 3.0, False),       # a SATA II disk (max 3.0) on a 6.0 port: its normal
    (None, 3.0, 6.0, None),       # no capability: not judged
    (6.0, None, 6.0, None),       # the link was not read now: not judged
    (6.0, True, 6.0, None),
    (6.0, 0, 6.0, None),
])
def test_a_drop_is_a_link_below_what_the_port_saw_and_below_what_the_disk_can(cap, now, disk_max, want):
    assert portcap.drop({"gbps": cap}, now, disk_max) is want


@pytest.mark.parametrize("gbps, mbps", [(1.5, 140), (3.0, 280), (6.0, 550), (3, 280), (4.0, None), (None, None), (True, None), ("3.0", None)])
def test_the_ceilings(gbps, mbps):
    assert portcap.ceiling_mbps(gbps) == mbps


def test_one_dot_five_is_marked_an_estimate():
    assert "1.5" in portcap.__doc__ and "הערכה" in portcap.__doc__


# --- the drawer and the precheck ---------------------------------------------------------------------------------

def _disk(port, link, top=6.0, serial="S1"):
    return {"serial": serial, "port": port, "link_gbps": link, "link_max_gbps": top, "crc": None}


def test_the_drawer_says_a_port_that_saw_six_and_reads_three_has_dropped(conn):
    seen(conn, MAC, 2, 6.0)
    got = room_precheck.drawer_fields(conn, _disk(2, 3.0), MAC)
    assert (got["port_cap_gbps"], got["port_cap_known"], got["port_cap_basis"], got["port_drop"]) == (6.0, True, "machine", True), got


def test_a_port_that_is_always_three_is_not_a_drop_and_not_a_warning(conn):
    seen(conn, MAC, 4, 3.0, waves=5)
    got = room_precheck.drawer_fields(conn, _disk(4, 3.0), MAC)
    assert got["port_cap_gbps"] == 3.0 and got["port_drop"] is False, got
    machine = {"mac": MAC, "name": "C1", "joined": True,
               "drawer_list": [{"port": 4, "fresh": True, "serial": "S1", "link_gbps": 3.0, "link_max_gbps": 6.0, "crc_rise": 0, **got}]}
    assert room_precheck.warnings([machine], False) == []


def test_a_drawer_with_no_history_or_no_machine_is_not_judged(conn):
    assert room_precheck.drawer_fields(conn, _disk(2, 3.0), MAC)["port_drop"] is None
    assert room_precheck.drawer_fields(conn, _disk(2, 3.0), None)["port_drop"] is None
    assert room_precheck.drawer_fields(conn, {"serial": "S", "link_gbps": 3.0, "link_max_gbps": 6.0}, MAC)["port_drop"] is None  # no port


def test_the_precheck_warns_on_a_drop_in_words_and_says_to_check_the_cable(conn):
    seen(conn, MAC, 2, 6.0)
    got = room_precheck.drawer_fields(conn, _disk(2, 3.0), MAC)
    machine = {"mac": MAC, "name": "C1", "joined": True,
               "drawer_list": [{"port": 2, "fresh": True, "serial": "S1", "link_gbps": 3.0, "link_max_gbps": 6.0, "crc_rise": 0, **got}]}
    (w,) = room_precheck.warnings([machine], False)
    assert "הקישור ירד ל-3.0 Gb/s (היציאה ראתה 6.0)" in w["text_he"] and "לבדוק כבל" in w["text_he"], w
    assert w["port_drop"] is True and w["port_cap_gbps"] == 6.0


# --- the journal and the console ---------------------------------------------------------------------------------

def test_the_journal_line_carries_the_drop_and_says_it_is_a_link_warning(conn):
    row = {"mac": MAC, "port": 2, "serial": "S1", "link_gbps": 3.0, "link_max_gbps": 6.0, "crc_rise": 0, "crc": None,
           "port_drop": True, "port_cap_gbps": 6.0}
    room_mod.journal_precheck(conn, {"id": "room_1", "wave_number": 1}, [row], "")
    (detail,) = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'room_precheck_warned'")]
    assert f"{MAC}:2 drop=6>3" in detail, detail
    kind, reasons = journal_he.precheck_he(detail)
    assert kind == "עם קישור חשוד" and "הקישור ירד ל-3 Gb/s (היציאה ראתה 6)" in reasons, (kind, reasons)


@requires_native(("node", NODE))
def test_the_console_counts_a_drop_as_a_link_sign_and_a_steady_three_as_nothing(tmp_path):
    js = console_js()
    pick = [re.search(r"^const PRECHECK_LINK_ROSE = .*$", js, re.M), re.search(r"^const PRECHECK_DISK_ROSE = .*$", js, re.M),
            re.search(r"^function precheckSigns\(x\) \{.*?^\}", js, re.M | re.S)]
    assert all(pick), "precheckSigns is gone from console.js"
    probe = tmp_path / "probe.cjs"
    probe.write_text("\n".join(m.group(0) for m in pick) + """
console.log(JSON.stringify({ drop: precheckSigns({ port_drop: true }), steady: precheckSigns({ port_drop: false, link_gbps: 3, link_max_gbps: 6 }),
  unknown: precheckSigns({ port_drop: null }) }));
""", encoding="utf-8", newline="\n")
    run = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"drop": ["link"], "steady": [], "unknown": []}


# --- a connection that cannot answer the question (the fakes of test_serialless_drawer / test_writemark_1798) --------

class _NoAnswer:
    """`execute` returns a row of another shape for every query (a stub): the learning cannot be read."""

    def execute(self, *_a, **_k):
        class _R:
            def fetchone(self):
                return {"disks_json": "[]"}

            def fetchall(self):
                return []

            def __iter__(self):
                return iter(())
        return _R()


def test_a_connection_that_cannot_answer_leaves_the_drawer_not_judged_and_does_not_raise():
    got = room_precheck._portcap(_NoAnswer(), {"port": 2}, MAC, 3.0, 6.0)
    assert got == {"port_cap_gbps": None, "port_cap_known": None, "port_cap_basis": None, "port_drop": None}
