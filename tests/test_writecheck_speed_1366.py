"""#1366 (Nadav 13/10) -- the write speed of the pre-wave check is a verdict.

A disk that wrote below WRITECHECK_MIN_MBPS (50 MB/s: under half the wave's
900 Mbit/s ceiling), or below half the median of the *other* measured drawers
on the machine (two at least), is red: it holds the whole wave back, and red
skips when nobody answers -- failed by name, before its table. Not measured =
no verdict at all (principle 5b). The measuring itself (dd's own summary on a
real disk) is in test_writecheck_1366.py; this is the rule over the numbers.
"""

from __future__ import annotations

import pytest
from test_smart import AGENT, BASH, posix, sh  # noqa: F401 -- the same bash, declared skip without it

from native import requires_native
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "waits", "progress", "failmark", "writecheck")


def verdict(tmp_path, disks: dict[str, int | str | None], states: dict[str, str] | None = None,
            env: str = "") -> dict:
    """disks: name -> MB/s measured (None = no number; "x" = junk in the file)."""
    run = tmp_path / "run"
    for name, mbps in disks.items():
        t = run / "targets" / name
        t.mkdir(parents=True)
        (t / "state").write_text((states or {}).get(name, "writing") + "\n")
        (t / "write_check_state").write_text("ok")
        if mbps is not None:
            (t / "write_check_mbps").write_text(str(mbps))
    prelude = "".join(f"[ ! -f {posix(flat_lib())}/{n}.sh ] || . {posix(flat_lib())}/{n}.sh; " for n in LIBS)
    out = sh(f"export RUN_DIR={posix(run)!r} {env}; {prelude}"
             f"write_speed_verdict {' '.join(disks)}; echo \"RC $?\"")
    assert out.returncode == 0, out.stderr
    res = {"rc": int(out.stdout.split("RC ")[-1].split()[0])}
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
    assert r["sda"][2] == "איטי — 120 MB/s, פחות מחצי מ-390 MB/s של שאר המגירות"
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
