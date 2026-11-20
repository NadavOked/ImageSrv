"""#3591 (Nadav 17/11) -- no SATA link SPEED (1.5, 3.0 or 6.0) is red, blocks a write, or says "check the cable".

17/11, cloner 1 (HP Compaq Elite 8300, Intel Q77): only SATA 0/1 are 6 Gb/s; SATA 2/3 are 3 Gb/s in hardware. A good
SSD on SATA 3 read ``link_gbps=3.0, link_max_gbps=6.0`` -- and the product drew it red "קישור SATA איטי", refused to
write it, and told the technician to check the cable. Nadav: "שיכפול מקומי אין מה לעשות, זה לא מחשב חדש והוא צריך לתמוך
גם וגם", and then: "ברשת של 1 גיגה לא. ברשת של 10 לא" -- 1.5, 3.0 and 6.0 behave the same.

The speed stays in the hello and is shown as information ("קישור 3.0 מתוך 6.0 Gb/s"). It becomes a *reason for slowness*
only when it is the bottleneck against the measured network (the brain, #3591 part B). What still says "cable": a SATA
problem (``sata_issues``: a link reset / a drawer that went) and a CRC that rises during the write (#2846).

Layers here: the server's judge + precheck + learned port history (Python), the console (``linkText`` / ``precheckSigns``
/ ``slotClass``), and the cloner's rule (C, the header). The agent's hardware words are in ``test_smart_tables_1921.py``
and the cloner's pixels in ``test_room_precheck_native_432_65.py``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from console_src import console_js
from native import requires_native
from server.disks import disk_rule
from server.learn import ports as learn_ports
from server.rounds import room_precheck

from test_disk_rule_server_2848 import _hello, room_body, room, CLONER1, room_server  # noqa: F401
from test_learn_ports_1844 import MAC, _obs, conn  # noqa: F401 -- the fixture

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

CHECK_CABLE = "לבדוק כבל"
SPEEDS = [(1.5, 6.0), (3.0, 6.0), (6.0, 6.0), (1.5, 1.5)]


# --- the rule ------------------------------------------------------------------------------------------

def test_the_rule_has_no_link_speed_in_it():
    assert "link_slow" not in disk_rule.RED and "link_slow" not in disk_rule.REASON_HE
    assert set(disk_rule.REASON_HE) == set(disk_rule.RED) | set(disk_rule.YELLOW)
    assert "link_slow" not in disk_rule.facts_of({"link_gbps": 1.5, "link_max_gbps": 6.0, "link_state": "slow"})


@pytest.mark.parametrize("gbps, top", SPEEDS)
@pytest.mark.parametrize("state", [None, "ok", "slow"])    # "slow": a word of an older server -- not read
def test_the_judge_never_makes_a_disk_red_for_its_link_speed(gbps, top, state):
    disk = {"link_gbps": gbps, "link_max_gbps": top, "smart": "ok", **({"link_state": state} if state else {})}
    got = disk_rule.judge_disk(disk)
    assert (got["color"], got["source"], got["target"], got["target_reason"]) == ("green", True, True, None), got


def test_a_sata_problem_is_still_red_the_guard():
    got = disk_rule.judge_disk({"link_gbps": 6.0, "link_max_gbps": 6.0, "smart": "ok", "sata_issues": ["link_reset"]})
    assert (got["color"], got["target"], got["target_reason"]) == ("red", False, "sata_issue"), got


def test_the_server_state_of_a_link_is_ok_or_unknown_and_never_slow():
    for gbps, top in SPEEDS:
        assert room_precheck.link_state(gbps, top) == "ok", (gbps, top)
    assert room_precheck.link_state(None, 6.0) == "unknown" and room_precheck.link_state(3.0, None) == "unknown"


# --- the precheck: a CRC that rose warns, a speed does not ----------------------------------------------

def _machine(link: float, crc_rise: int = 0) -> dict:
    return {"mac": CLONER1, "name": "C1", "joined": True,
            "drawer_list": [{"port": 1, "fresh": True, "serial": "S", "link_gbps": link, "link_max_gbps": 6.0,
                             "link_state": room_precheck.link_state(link, 6.0), "crc_rise": crc_rise}]}


@pytest.mark.parametrize("gbps", [1.5, 3.0, 6.0])
def test_a_link_speed_is_not_a_precheck_warning(gbps):
    assert room_precheck.warnings([_machine(gbps)], False) == []


def test_a_crc_rise_still_warns_and_still_says_to_check_the_cable():
    (w,) = room_precheck.warnings([_machine(1.5, crc_rise=12)], False)
    assert "מונה CRC עלה ב-12" in w["text_he"] and CHECK_CABLE in w["text_he"], w
    assert "מתוך" not in w["text_he"], "the speed is not part of the warning"


# --- the server, end to end ----------------------------------------------------------------------------

def _open(room_server, ports=(1, 2)):
    return room_server["deploy"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": len(ports),
        "target_slots": [{"mac": CLONER1, "ports": list(ports)}]})


@pytest.mark.parametrize("gbps", [1.5, 3.0])
def test_a_round_opens_on_drawers_at_a_low_link_speed_with_nothing_to_check(room_server, gbps):
    room_body(room_server, 2, {CLONER1: ["X", "Y"]})
    drawers = [{"serial": s, "link_gbps": gbps, "link_max_gbps": 6.0} for s in ("X", "Y")]
    _hello(room_server, CLONER1, drawers)
    r = _open(room_server)
    assert r.status_code == 200, r.text                      # before #3591: 400 "קישור SATA איטי -- לא כותבים"
    _hello(room_server, CLONER1, drawers)                      # the hello after the open is what the precheck reads
    view = room(room_server["deploy"])
    got = [(d["link_gbps"], d["link_max_gbps"], d["link_state"], d["rule"]["color"], d["rule"]["target"])
           for d in view["machines"][0]["drawer_list"]]
    assert got == [(gbps, 6.0, "ok", "green", True)] * 2, got
    assert view["round"]["precheck"] == [], view["round"]["precheck"]


def test_a_drawer_with_a_sata_problem_is_still_refused(room_server):
    room_body(room_server, 1, {CLONER1: ["X"]})
    _hello(room_server, CLONER1, [{"serial": "X", "sata_issues": ["drawer_gone"]}])
    r = _open(room_server, ports=(1,))
    assert r.status_code == 400 and "בעיית SATA" in r.json()["detail"], r.text


# --- what the server learns about a connection (#1844) -----------------------------------------------

@pytest.mark.parametrize("link", [(1.5, 6.0), (3.0, 6.0)])
def test_two_different_disks_at_a_low_link_speed_on_one_connection_are_not_the_cable(conn, link):
    _obs(conn, MAC, 1, "S1", link=link)
    _obs(conn, MAC, 1, "S2", link=link)
    got = learn_ports.verdict(conn, MAC, 1, "S9")
    assert got["port_suspect"] is False and got["text_he"] is None, got


def test_two_different_disks_with_a_crc_on_one_connection_still_are(conn):
    _obs(conn, MAC, 1, "S1", crc=4)
    _obs(conn, MAC, 1, "S2", crc=3)
    got = learn_ports.verdict(conn, MAC, 1, "S9")
    assert got["port_suspect"] is True and got["port_disks"] == 2, got


def test_a_crc_that_rises_is_still_a_problem_on_any_link_speed(conn):
    # #2846: only a rise during the write -- untouched by #3591
    assert learn_ports._problem(5, 3.0, 6.0, 0) is True and learn_ports._problem(5, 1.5, 6.0, 0) is True
    assert learn_ports._problem(0, 3.0, 6.0, 0) is False and learn_ports._problem(0, 1.5, 6.0, 0) is False
    assert learn_ports._problem(0, 6.0, 6.0, 1) is True        # a failure classified as the cable


# --- the console ---------------------------------------------------------------------------------------

@requires_native(("node", NODE))
def test_the_console_shows_a_link_speed_as_information_only(tmp_path):
    js = console_js()
    assert "function linkSlow" not in js and "link_slow" not in js, "a slow-link decision is back in console.js"
    pick = [re.search(r"^const SMART_REASON_HE = .*$", js, re.M),
            re.search(r"^function smartF1Text\(reason\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^const FAILURE_CAUSE_HE = .*$", js, re.M),
            re.search(r"^const PRECHECK_LINK_ROSE = .*$", js, re.M),
            re.search(r"^const PRECHECK_DISK_ROSE = .*$", js, re.M),
            re.search(r"^function linkText\(d\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^function precheckSigns\(x\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^const SATA_ISSUE_HE = .*$", js, re.M),
            re.search(r"^function sataIssues\(d\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^const NOT_CHECKED_RULE = \{.*?\};$", js, re.M | re.S),   # #3666: no rule from the brain = not checked
            re.search(r"^function slotRule\(s\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^function slotClass\(s\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^const PULLED_HE = .*$", js, re.M),                      # slotClass asks drawerPulled first (#3209)
            re.search(r"^function drawerPulled\(d\) \{.*?^\}", js, re.M | re.S)]
    assert all(pick), "a function this test reads is gone from console.js"
    probe = tmp_path / "probe.cjs"
    probe.write_text("\n".join(m.group(0) for m in pick) + """
const GREEN = { color: "green", source: true, target: true };   // what the brain sent for these disks (see the server half below)
const out = {};
for (const [g, t] of [[1.5, 6], [3, 6], [6, 6]]) {
  const d = { link_gbps: g, link_max_gbps: t, smart: "ok" };
  out["text" + g] = linkText(d);
  out["signs" + g] = precheckSigns(d);
  out["class" + g] = slotClass({ disk: { ...d, link_state: "slow", rule: GREEN } });
}
out.rise = precheckSigns({ link_gbps: 6, link_max_gbps: 6, crc_rise: 12 });
out.problem = slotClass({ disk: { smart: "ok", sata_issues: ["link_reset"], rule: { color: "red", source: true, target: false } } });
console.log(JSON.stringify(out));
""", encoding="utf-8", newline="\n")
    run = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    # #3666: the decision is the server's -- no link speed is a reason (1.5, 3.0, 6.0), a SATA problem still is
    from server.disks import disk_rule
    assert disk_rule.RED == ("smart_fail", "sata_issue", "write_slow"), disk_rule.RED
    for g in (1.5, 3.0, 6.0):
        assert disk_rule.judge_disk({"serial": "S1", "smart": "ok", "link_gbps": g, "link_max_gbps": 6.0,
                                     "link_state": "slow"})["color"] == "green", g
    assert disk_rule.judge_disk({"serial": "S1", "smart": "ok", "sata_issues": ["link_reset"]})["color"] == "red"
    for g in ("1.5", "3", "6"):
        assert out[f"signs{g}"] == [] and out[f"class{g}"] == "ok", (g, out)
    assert out["text3"] == "קישור 3.0 מתוך 6.0 Gb/s" and out["text1.5"] == "קישור 1.5 מתוך 6.0 Gb/s", out
    assert CHECK_CABLE not in out["text1.5"] + out["text3"], out
    assert out["rise"] == ["link"], "a CRC rise is still a link sign"
    assert out["problem"] == "err", "a SATA problem is still red"


# --- the cloner (C) ----------------------------------------------------------------------------------------

def test_the_cloners_rule_has_no_link_speed_in_it():
    for name in ("disk_rule.h", "disk_rule.c"):
        assert "link_slow" not in (ROOT / "native-gui/src" / name).read_text(encoding="utf-8"), name
    standby = (ROOT / "native-gui/src/screens_standby.c").read_text(encoding="utf-8")
    assert "static int link_slow(" not in standby and "SLOW_LINK_BELOW_GBPS" not in standby
