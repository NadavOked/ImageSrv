"""#3064 (N4, N6, N8): `agent/lib/hw/netwatch.sh` -- המשכפל מאזין לרשת ההפצה ברקע וה-hello נושא את התוצאה.

רץ על ה-sh האמיתי. `imagesrv-netwatch` מזויף בנתיב מפורש (`NETWATCH=`): הבינארי האמיתי נבדק ב-`test_netwatch_c_3064.py`.

מה נבדק:
- שלושת המצבים ב-hello (עיקרון 5): נמדד (עם `age_s`) · `{"pending":true}` · `{"error"}` -- ושום דבר כשהבינארי חסר או כשהמכונה אינה משכפל.
- **פלט ריק מהכלי אינו "נמדד"** וקוד יציאה לא צפוי אינו "נמדד": שניהם `{"error"}`.
- ההאזנה מתחילה **רק** כשהמשכפל ממתין (בלי סבב פתוח ובלי משימה), פעם אחת, ובלי להתחיל מחדש בתוך `NETWATCH_GAP`.
- `netwatch_stop` הורג דגימה באמצע וזורק אותה -- ושומר את הדגימה הקודמת. דגימה שחפפה עבודה אינה ראיה.
- **ההמתנה לכלי חסומה בתקרה** (`wait_pid`, חלון הכלי + `NETWATCH_GRACE`): כלי שנתקע אינו משאיר את הדגימה "ממתינה" לנצח --
  הוא נהרג, והדגימה היא `{"error"}` בשמה.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import sizelimit
from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401
from test_netprobe import _stub

pytestmark = requires_native(("bash", BASH))

SAMPLE = ('{"v":1,"iface":"eth0","window_s":140,"promisc":true,"igmp":{"queries":2,"general":2,"group":0,'
          '"reports":0,"leaves":0,"versions":[2],"sources":["10.0.0.1"],"last_query_age_s":30},"passed":10,'
          '"kernel_drops":0,"sampled":10,"classes":{"arp":1},"groups":[],"groups_more":0,"macs":[],"macs_more":0,'
          '"joined":["224.0.0.1"]}')


def _env(fake_machine, tool: Path | str, extra: str = "") -> str:
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    return (f"export SYSROOT={posix(sysroot)!r} RUN_DIR={posix(run)!r} IFACE=eth0 NETWATCH={str(tool).replace(chr(92), '/')!r} "
            f"D_ROLE=cloner D_SESSION_STATE=none D_TASK=null NETWATCH_S=3 {extra}; "
            f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; . {posix(AGENT)}/lib/hw/netprobe.sh; "
            f". {posix(AGENT)}/lib/hw/netwatch.sh; ")


def _fragment(fake_machine, tool, extra: str = "") -> object:
    out = sh(_env(fake_machine, tool, extra) + 'printf "{\\"x\\":0%s}" "$(netwatch_json)"')
    return json.loads(out).get("netwatch", "absent")


def _wait(cond, seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_the_wall_and_the_wiring():
    assert sizelimit.count_lines(AGENT / "lib" / "hw" / "netwatch.sh") <= 100
    assert sizelimit.count_lines(AGENT / "imagesrv-agent") <= 300
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/netwatch.sh"' in agent and "netwatch_idle_start" in agent
    # כל מצב שבו המשכפל יוצא מהמתנה עוצר את הדגימה
    assert agent.count("netwatch_stop") >= 3
    assert "netwatch_json" in (AGENT / "lib" / "hw" / "sysinfo.sh").read_text(encoding="utf-8")


def test_binary_absent_or_not_a_cloner_says_nothing(fake_machine):
    """סוכן בלי הבינארי, ומחשב בנייה: שום `netwatch` ב-hello -- השרת שומר "לא דווח", ולא "ממתין" נצחי."""
    assert _fragment(fake_machine, "imagesrv-netwatch-not-here") == "absent"
    assert _fragment(fake_machine, "imagesrv-netwatch-not-here", "D_ROLE=build") == "absent"


def test_a_cloner_with_the_binary_and_no_sample_is_pending(fake_machine, tmp_path):
    tool = _stub(tmp_path, "nw", "exit 0")
    assert _fragment(fake_machine, tool) == {"pending": True}
    assert _fragment(fake_machine, tool, "D_ROLE=build") == "absent"


def test_a_cached_sample_is_served_with_its_age(fake_machine, tmp_path):
    tool = _stub(tmp_path, "nw", "exit 0")
    run = fake_machine["run"]
    (run / "netwatch.json").write_text(SAMPLE + "\n")
    (run / "netwatch.json.at").write_text(f"{int(time.time()) - 42}\n")
    got = _fragment(fake_machine, tool)
    assert got["window_s"] == 140 and got["igmp"]["queries"] == 2 and got["joined"] == ["224.0.0.1"]
    assert 40 <= got["age_s"] <= 60, got["age_s"]


def test_an_idle_cloner_samples_in_the_background_and_the_hello_serves_it(fake_machine, tmp_path):
    log = tmp_path / "calls.log"
    tool = _stub(tmp_path, "nw", f'echo "$@" >> {posix(log)!r}; printf \'%s\\n\' \'{SAMPLE}\'; exit 0')
    run = fake_machine["run"]
    sh(_env(fake_machine, tool) + "netwatch_idle_start; echo started")
    assert _wait(lambda: (run / "netwatch.json").exists() and (run / "netwatch.json.at").exists()), "no sample was cached"
    assert log.read_text().split() == ["eth0", "3", "--promisc"]       # the NIC of the distribution net, the window, promiscuous
    assert _wait(lambda: not (run / "netwatch.pid").exists())
    got = _fragment(fake_machine, tool)
    assert got["igmp"]["queries"] == 2 and got["age_s"] >= 0


@pytest.mark.parametrize("script, expect", [
    ("exit 0", "לא הדפיס"),                                # a tool that said nothing did not measure
    ("printf '{\"error\":\"Operation not permitted\"}\\n'; exit 3", "Operation not permitted"),
    ("echo garbage; exit 1", "rc 1"),
], ids=["empty", "listen-error", "crash"])
def test_a_tool_that_did_not_measure_is_an_error_never_a_sample(fake_machine, tmp_path, script, expect):
    tool = _stub(tmp_path, "nw", script)
    run = fake_machine["run"]
    sh(_env(fake_machine, tool) + "netwatch_idle_start")
    assert _wait(lambda: (run / "netwatch.json").exists())
    got = _fragment(fake_machine, tool)
    assert set(got) == {"age_s", "error"} and expect in got["error"], got


def test_a_tool_that_hangs_is_killed_at_its_ceiling_and_the_sample_is_an_error(fake_machine, tmp_path):
    """הכלי נתקע אחרי החלון שלו (`NETWATCH_S`): בלי תקרה הדגימה נשארה `pending` לנצח וההאזנה הבאה לא התחילה."""
    started, tool_pid = tmp_path / "started", tmp_path / "tool.pid"
    tool = _stub(tmp_path, "nw", f'echo $$ > {posix(tool_pid)!r}; touch {posix(started)!r}; exec sleep 120')
    run = fake_machine["run"]
    begin = time.monotonic()
    sh(_env(fake_machine, tool, "NETWATCH_S=1 NETWATCH_GRACE=1") + "netwatch_idle_start")
    assert _wait(lambda: started.exists()), "the sample did not start"
    assert _wait(lambda: (run / "netwatch.json").exists(), seconds=20), "a hung tool left the sample pending for good"
    assert time.monotonic() - begin < 20
    got = _fragment(fake_machine, tool)
    assert set(got) == {"age_s", "error"} and "לא הסתיים תוך 2 שניות" in got["error"], got
    assert _wait(lambda: not (run / "netwatch.pid").exists()), "the pid file of a dead sample was left behind"
    alive = sh(f"kill -0 $(cat {posix(tool_pid)!r}) 2>/dev/null && echo alive || echo gone").strip()
    assert alive == "gone", "the hung tool outlived its ceiling"


@pytest.mark.parametrize("gate", ["D_SESSION_STATE=open", "D_TASK=local_clone", "D_ROLE=build", "D_ROLE=''"])
def test_it_never_starts_unless_the_cloner_idles(fake_machine, tmp_path, gate):
    """כל שער סגור, ואחריו קריאה אחת פתוחה: רק הקריאה הפתוחה רצה (הכלי רושם את התג של מי שהפעיל אותו)."""
    log = tmp_path / "calls.log"
    tool = _stub(tmp_path, "nw", f'echo "$NW_TAG" >> {posix(log)!r}; printf \'%s\\n\' \'{SAMPLE}\'; exit 0')
    run = fake_machine["run"]
    sh(_env(fake_machine, tool, f"NW_TAG=closed {gate}") + "netwatch_idle_start; "
       + "D_ROLE=cloner; D_SESSION_STATE=none; D_TASK=null; NW_TAG=open; export NW_TAG; netwatch_idle_start")
    assert _wait(lambda: (run / "netwatch.json").exists())
    assert _wait(lambda: not (run / "netwatch.pid").exists())
    assert log.read_text().split() == ["open"], log.read_text()


def test_a_second_sample_waits_for_the_gap(fake_machine, tmp_path):
    log = tmp_path / "calls.log"
    tool = _stub(tmp_path, "nw", f'echo x >> {posix(log)!r}; printf \'%s\\n\' \'{SAMPLE}\'; exit 0')
    run = fake_machine["run"]
    env = _env(fake_machine, tool, "NETWATCH_GAP=3600")
    sh(env + "netwatch_idle_start")
    assert _wait(lambda: (run / "netwatch.json.at").exists() and not (run / "netwatch.pid").exists())
    sh(env + "netwatch_idle_start")
    # הקריאה השנייה כבר ציינה ש-GAP לא עבר: אין הרצה שנייה (היינו רואים שורה שנייה ביומן)
    assert not _wait(lambda: log.read_text().count("x") > 1, seconds=1.0)
    (run / "netwatch.json.at").write_text("1\n")                        # the cache is old now
    sh(env + "netwatch_idle_start")
    assert _wait(lambda: log.read_text().count("x") == 2), "an old sample was not refreshed"


def test_stop_kills_a_half_finished_sample_and_keeps_the_last_good_one(fake_machine, tmp_path):
    started = tmp_path / "started"
    tool = _stub(tmp_path, "nw", f'touch {posix(started)!r}; exec sleep 60')
    run = fake_machine["run"]
    (run / "netwatch.json").write_text('{"v":1,"window_s":99,"igmp":{"queries":0}}\n')
    (run / "netwatch.json.at").write_text("1\n")
    sh(_env(fake_machine, tool) + "netwatch_idle_start")
    assert _wait(lambda: started.exists() and (run / "netwatch.pid").exists()), "the sample did not start"
    sh(_env(fake_machine, tool) + "netwatch_stop")
    assert _wait(lambda: not (run / "netwatch.pid").exists()), "the sample was not stopped"
    assert _wait(lambda: not (run / "netwatch.work").exists())
    assert json.loads((run / "netwatch.json").read_text())["window_s"] == 99, "a killed sample replaced the last good one"


def test_the_hello_carries_it_end_to_end(fake_machine, tmp_path):
    """`build_hello` נושא `netwatch` כשדה אח של `probe`/`netprobe`: אותה צורה ש-`server/agent/api.py` מצפה לה."""
    tool = _stub(tmp_path, "nw", "exit 0")
    run = fake_machine["run"]
    (run / "netwatch.json").write_text(SAMPLE + "\n")
    (run / "netwatch.json.at").write_text(f"{int(time.time())}\n")
    hello = json.loads(sh(
        _env(fake_machine, tool, f"DEVROOT={posix(fake_machine['dev'])!r} IP=10.44.12.187")
        + f". {posix(AGENT)}/lib/hw/sysinfo.sh; . {posix(AGENT)}/lib/hw/diskinfo.sh; build_hello"))
    assert hello["schema"] == 2 and hello["netwatch"]["igmp"]["sources"] == ["10.0.0.1"]
