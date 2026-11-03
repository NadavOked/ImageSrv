"""#1366 (Nadav 13/10) -- the write speed of the pre-wave check is a verdict.

A disk that wrote below WRITECHECK_MIN_MBPS (50 MB/s: under half the wave's
900 Mbit/s ceiling), or below half the median of the *other* measured drawers
on the machine **of the same kind** (two at least; #2037: SSD against SSD,
HDD against HDD, every median taken before any disk is failed), is red: it
holds the whole wave back, and red skips when nobody answers -- failed by name, before its table. Not measured =
no verdict at all (principle 5b). The measuring itself (dd's own summary on a
real disk) is in test_writecheck_1366.py; this is the rule over the numbers.
"""

from __future__ import annotations

import json

import pytest
from test_smart import AGENT, BASH, posix, sh  # noqa: F401 -- the same bash, declared skip without it

from native import requires_native
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "waits", "sysinfo", "progress", "failmark", "writecheck")


def verdict(tmp_path, disks: dict[str, int | str | None], states: dict[str, str] | None = None,
            env: str = "", rot: dict[str, str | None] | None = None,
            manifest: dict | str | None = None) -> dict:
    """disks: name -> MB/s measured (None = no number; "x" = junk in the file).
    rot: name -> /sys/block/<d>/queue/rotational ("0" SSD, "1" HDD, None = no
    file); a disk not named there is an SSD. manifest (#2338): the one the
    verdict takes its floor from -- a dict as JSON, a str as raw text, None =
    ``{}`` (no floor in it: the agent's own)."""
    run = tmp_path / "run"
    man = tmp_path / "manifest.json"
    man.write_text(manifest if isinstance(manifest, str) else json.dumps(manifest or {}), encoding="utf-8")
    agent_log = run / "agent.log"   # common.sh: LOG_FILE="$RUN_DIR/agent.log"
    for name, mbps in disks.items():
        r = (rot or {}).get(name, "0")
        if r is not None:
            q = tmp_path / "sysroot" / "sys" / "block" / name / "queue"
            q.mkdir(parents=True)
            (q / "rotational").write_text(r + "\n", newline="\n")
        t = run / "targets" / name
        t.mkdir(parents=True)
        (t / "state").write_text((states or {}).get(name, "writing") + "\n")
        (t / "write_check_state").write_text("ok")
        if mbps is not None:
            (t / "write_check_mbps").write_text(str(mbps))
    prelude = "".join(f"[ ! -f {posix(flat_lib())}/{n}.sh ] || . {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(tmp_path / 'sysroot')!r} {env}; {prelude}"
             f"write_speed_verdict {posix(man)!r} {' '.join(disks)}; echo \"RC $?\"")
    assert out.returncode == 0, out.stderr
    res = {"rc": int(out.stdout.split("RC ")[-1].split()[0]),
           "log": agent_log.read_text(encoding="utf-8") if agent_log.exists() else ""}
    for name in disks:
        t = run / "targets" / name
        res[name] = ((t / "state").read_text().strip(), (t / "write_check_state").read_text(),
                     (t / "error").read_text().strip() if (t / "error").exists() else "")
    return res


def test_under_the_floor_is_red_by_name(tmp_path):
    r = verdict(tmp_path, {"sda": 31, "sdb": 400})
    assert r["sda"] == ("failed", "slow", "איטי — 31 MB/s, מתחת לסף 50 MB/s"), r
    assert r["sdb"] == ("writing", "ok", "") and r["rc"] == 1


def test_half_the_median_of_the_others_is_red(tmp_path):
    r = verdict(tmp_path, {"sda": 120, "sdb": 400, "sdc": 380})
    assert r["sda"][0:2] == ("failed", "slow"), r
    assert r["sda"][2] == "איטי — 120 MB/s, פחות מחצי מ-390 MB/s של שאר המגירות מאותו סוג (מוצק)"
    assert r["sdb"][0] == r["sdc"][0] == "writing"


def test_one_other_disk_is_no_median(tmp_path):
    """120 against a single 400: a median of one disk is that disk's mood."""
    r = verdict(tmp_path, {"sda": 120, "sdb": 400})
    assert r["sda"][0] == r["sdb"][0] == "writing" and r["rc"] == 0, r


@pytest.mark.parametrize("junk", [None, "x", ""])
def test_not_measured_is_no_verdict_and_not_counted(tmp_path, junk):
    """No number: never slow, and never a 0 that drags the others' median down."""
    r = verdict(tmp_path, {"sda": junk, "sdb": 400, "sdc": 390, "sdd": 120})
    assert r["sda"][0] == "writing", r
    assert r["sdd"][0] == "failed"            # 120 < 395/2 -- the unmeasured one was not a 0


def test_a_failed_disk_is_neither_judged_nor_counted(tmp_path):
    r = verdict(tmp_path, {"sda": 10, "sdb": 400, "sdc": 380, "sdd": 400}, states={"sda": "failed"})
    assert r["sda"] == ("failed", "ok", ""), r          # its own failure stays its own
    assert r["sdb"][0] == r["sdc"][0] == r["sdd"][0] == "writing"


def test_the_floor_is_a_setting(tmp_path):
    r = verdict(tmp_path, {"sda": 80}, env="WRITECHECK_MIN_MBPS=100")
    assert r["sda"][0:2] == ("failed", "slow") and "מתחת לסף 100 MB/s" in r["sda"][2], r


# --- #2037: the same kind only, and every median before any verdict --------

HDD, SSD = "1", "0"


def _states(r: dict, disks) -> dict:
    return {d: r[d][0] for d in disks}


def test_room_8c6bf26c_two_hdds_beside_an_ssd_both_pass(tmp_path):
    """Lab 23/10, cloner 2, ports 1/2/5. Before: sda (57) failed against the
    others' 131 and sdd (54) passed only because sda was already out."""
    disks = {"sda": 57, "sdc": 209, "sdd": 54}
    r = verdict(tmp_path, disks, rot={"sda": HDD, "sdc": SSD, "sdd": HDD})
    assert _states(r, disks) == {"sda": "writing", "sdc": "writing", "sdd": "writing"}, r
    assert r["rc"] == 0


def test_room_3d81439d_the_same_disks_reversed_both_pass(tmp_path):
    """The next round, 23/10 05:20Z: sdb (52) failed against 119, sda (59) passed."""
    disks = {"sda": 59, "sdb": 52, "sdd": 179}
    r = verdict(tmp_path, disks, rot={"sda": HDD, "sdb": HDD, "sdd": SSD})
    assert _states(r, disks) == {"sda": "writing", "sdb": "writing", "sdd": "writing"}, r
    assert r["rc"] == 0


@pytest.mark.parametrize("order", [("sda", "sdb", "sdc", "sdd"), ("sdb", "sda", "sdd", "sdc")])
def test_the_verdict_does_not_depend_on_the_drawer_order(tmp_path, order):
    """100 against {118, 230, 250} -> 230: red. 118 against {100, 230, 250} ->
    230: 236 >= 230, passes -- in either order. Judged one by one with the
    failed one dropped from the next comparison, 118 met {230, 250} -> 240 and
    went red too, but only when it came second."""
    mbps = {"sda": 100, "sdb": 118, "sdc": 230, "sdd": 250}
    disks = {d: mbps[d] for d in order}
    r = verdict(tmp_path, disks)
    assert _states(r, order) == {"sda": "failed", "sdb": "writing", "sdc": "writing", "sdd": "writing"}, r
    assert r["sda"][1:] == ("slow", "איטי — 100 MB/s, פחות מחצי מ-230 MB/s של שאר המגירות מאותו סוג (מוצק)"), r


def test_a_lone_hdd_beside_ssds_meets_only_the_floor(tmp_path):
    """60 against SSDs at 400/380 was 'under half of 390'; alone of its kind it
    meets the fixed floor only, and 60 >= 50."""
    disks = {"sda": 60, "sdb": 400, "sdc": 380}
    r = verdict(tmp_path, disks, rot={"sda": HDD})
    assert _states(r, disks) == {"sda": "writing", "sdb": "writing", "sdc": "writing"}, r


def test_a_lone_hdd_under_the_floor_is_still_red(tmp_path):
    r = verdict(tmp_path, {"sda": 40, "sdb": 400, "sdc": 380}, rot={"sda": HDD})
    assert r["sda"] == ("failed", "slow", "איטי — 40 MB/s, מתחת לסף 50 MB/s"), r


def test_hdds_are_compared_among_themselves(tmp_path):
    """Three HDDs and an SSD: the slow HDD is judged by the HDDs' median (111),
    not one the SSD pulled up, and the message says which kind."""
    disks = {"sda": 52, "sdb": 110, "sdc": 112, "sdd": 450}
    r = verdict(tmp_path, disks, rot={"sda": HDD, "sdb": HDD, "sdc": HDD, "sdd": SSD})
    assert r["sda"] == ("failed", "slow", "איטי — 52 MB/s, פחות מחצי מ-111 MB/s של שאר המגירות מאותו סוג (מכני)"), r
    assert _states(r, ("sdb", "sdc", "sdd")) == {"sdb": "writing", "sdc": "writing", "sdd": "writing"}, r


def test_an_unreadable_kind_meets_only_the_floor_and_is_not_counted(tmp_path):
    """No rotational file: its kind is not known, so there is nothing of the
    same kind to compare to -- the floor only -- and it is in nobody's median."""
    disks = {"sda": 120, "sdb": 400, "sdc": 380, "sdd": 30}
    r = verdict(tmp_path, disks, rot={"sda": None, "sdd": None})
    assert r["sda"][0] == "writing", r
    assert r["sdd"][2] == "איטי — 30 MB/s, מתחת לסף 50 MB/s", r
    assert r["sdb"][0] == r["sdc"][0] == "writing"
