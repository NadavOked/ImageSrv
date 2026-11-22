"""#2056, #2059 — הסוכן מחיל את זמני ההמתנה והכיבוי שהשרת שולח, ואומר מה החיל.

החוזה (docs/architecture/interfaces.md): בתשובת ה-hello של מכונה רשומה מגיעים
`lost_wait_seconds` (180–1800), `idle_poweroff_seconds` (60–3600)
ו-`end_poweroff_seconds` (10–300), ומ-18/11 גם `drawer_disk_wait_seconds` ו-`drawer_net_wait_seconds`
(30–900, ברירת מחדל 120, #3658). הסוכן מחיל כל ערך תקין, ומחזיר ב-hello הבא
`applied_timing` — **רק** מה שבאמת הוחל (עיקרון 5). ערך חסר/פגום = הקבוע של
הסוכן ושורת יומן, לעולם לא 0 ולא "אינסוף".

עד כאן צד השרת עבד (מדידת VM 03/11: `lost_wait_seconds=240` → `client_lost` אחרי
238ש') וצד הסוכן לא היה קיים: `WAIT_STREAM_STALL_S=300` קבוע — הגדרה מעל 300
והמשכפל מוותר לפני השרת — והכיבויים היו הקבועים של idle.sh.

הטסטים עוברים במסלול האמיתי: `read_answer` נחתך מ-`imagesrv-agent`, הספריות
נטענות בסדר של הסוכן (`agent_sources`), וה-hello נבנה ב-`build_hello` על מכונה
מזויפת. `timing.sh` נטען **רק אם הוא קיים** — כך שבקרה שלילית (החזרת כל קובצי
הייצור) נופלת על ההתנהגות ולא על קובץ חסר.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401
from agentlib import agent_order, agent_sources

pytestmark = requires_native(("bash", BASH))

TIMING = AGENT / "lib" / "core" / "timing.sh"
MAIN = AGENT / "imagesrv-agent"
LIBS = ("common", "timing", "waits", "jsonq", "sysinfo", "diskinfo", "restore", "idle")
GOOD = {"lost_wait_seconds": 240, "idle_poweroff_seconds": 600, "end_poweroff_seconds": 45,
        "drawer_disk_wait_seconds": 90, "drawer_net_wait_seconds": 200}
DEFAULTS = {"lost_wait_seconds": 180, "idle_poweroff_seconds": 300, "end_poweroff_seconds": 20,
            "drawer_disk_wait_seconds": 120, "drawer_net_wait_seconds": 120}
#: the agent's own constants (waits.sh, idle.sh, timing.sh) -- what an unapplied value leaves in force.
#: disk/chunk = the two environment variables the disk ceiling travels in, in ms (fanout, chunkcast)
CONST = {"stall": 120, "udp": 120, "idle": 300, "end": 20, "disk": 120000, "chunk": 120000}


UNSET = ("unset WAIT_STREAM_STALL_S STREAM_STALL_TIMEOUT IDLE_POWEROFF_S END_POWEROFF_S "
         "DISK_STALL_S FANOUT_STALL_EVICT_MS CHUNKCAST_WRITE_STALL_MS DRAWER_HOLD_S; ")

#: jq for a station without it (Windows): the narrow test shim, only when no real one
JQ_STUB = '#!/bin/sh\nexec python "' + posix(Path(__file__).with_name("jq_shim.py")) + '" "$@"\n'


def _path(run: Path) -> str:
    if shutil.which("jq") is not None:
        return ""
    bin_dir = run.parent / "jqbin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "jq").write_text(JQ_STUB, encoding="utf-8", newline="\n")
    (bin_dir / "jq").chmod(0o755)
    s = posix(bin_dir)   # /c/... -- a C: inside PATH is cut at its colon
    return f'export PATH={"/" + s[0].lower() + s[2:] if s[1:2] == ":" else s!r}:"$PATH"; '


def _cut(name: str) -> str:
    source = MAIN.read_text(encoding="utf-8")
    start = source.index(f"\n{name}() {{\n")
    return source[start + 1:source.index("\n}\n", start) + 3]


def run_answers(machine: dict, answers: list[dict], env: str = "") -> tuple[dict, dict, str]:
    """Each answer through read_answer, as the main loop does, then one build_hello.
    Returns (the hello body, the ceilings in force, the log)."""
    run = machine["run"]
    names = [n for n in LIBS if n != "timing" or TIMING.exists()]
    feeds = ""
    for i, answer in enumerate(answers):
        path = run / f"answer{i}.json"
        path.write_text(json.dumps(answer), encoding="utf-8")
        feeds += f'cp {posix(path)!r} "$RESP"; read_answer; '
    out = sh(
        _path(run) + UNSET + (
            f'export SYSROOT={posix(machine["sysroot"])!r} DEVROOT={posix(machine["dev"])!r} '
            f'RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/agent.log IFACE=eth0 IP=10.44.12.187; ')
        + env + agent_sources(*names)
        + "min_free_take() { :; }\n" + _cut("read_answer")
        + f'RESP={posix(run)!r}/response.json; ' + feeds
        + 'build_hello > "$RUN_DIR/hello.json"; '
        'echo "ceilings stall=$WAIT_STREAM_STALL_S udp=$STREAM_STALL_TIMEOUT '
        'idle=$IDLE_POWEROFF_S end=$END_POWEROFF_S disk=$FANOUT_STALL_EVICT_MS chunk=$CHUNKCAST_WRITE_STALL_MS"')
    line = [ln for ln in out.splitlines() if ln.startswith("ceilings ")][-1]
    ceilings = {k: int(v) for k, v in (kv.split("=") for kv in line.split()[1:])}
    hello = json.loads((run / "hello.json").read_text(encoding="utf-8"))
    log = (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""
    return hello, ceilings, log


def answer(**timing) -> dict:
    return {"schema": 1, "known": True, "role": "cloner", "task": None, "session": None, **timing}


# --- hello with the values -> applied in force, and said in the next hello ----


def test_the_servers_values_are_applied_and_said_in_the_next_hello(fake_machine):
    hello, ceilings, _ = run_answers(fake_machine, [answer(**GOOD)])
    assert hello["applied_timing"] == GOOD
    # #3658 (Nadav 18/11): the two drawer waits are the settings themselves, in seconds and (the disk) in ms
    assert ceilings == {"stall": 200, "udp": 200, "idle": 600, "end": 45, "disk": 90000, "chunk": 90000}


def test_the_defaults_change_nothing_and_are_still_reported(fake_machine):
    """180/300/20/120/120 are the agent's own numbers too: nothing moves, and the console
    can count the machine as applied (agent_timing_applied compares the value)."""
    hello, ceilings, _ = run_answers(fake_machine, [answer(**DEFAULTS)])
    assert hello["applied_timing"] == DEFAULTS
    assert ceilings == CONST


@pytest.mark.parametrize("lost", [180, 181, 300, 479, 1000, 1800])
def test_lost_wait_no_longer_moves_the_stream_ceiling(fake_machine, lost):
    """#3658 (Nadav 18/11): the network wait is its own setting. Until then the stream ceiling was
    lost_wait + 120 and never under 300, which made "2 minutes and a drawer gives up" impossible."""
    hello, ceilings, _ = run_answers(fake_machine, [answer(lost_wait_seconds=lost)])
    assert hello["applied_timing"] == {"lost_wait_seconds": lost}
    assert ceilings == CONST


# --- a value that is missing or bad -> the constant, logged, absent from applied


@pytest.mark.parametrize("field,bad", [
    ("lost_wait_seconds", 179), ("lost_wait_seconds", 1801), ("lost_wait_seconds", 0),
    ("lost_wait_seconds", "0240"), ("lost_wait_seconds", 240.5), ("lost_wait_seconds", True),
    ("lost_wait_seconds", -240), ("lost_wait_seconds", "x"),
    ("drawer_disk_wait_seconds", 29), ("drawer_disk_wait_seconds", 901), ("drawer_disk_wait_seconds", "090"),
    ("drawer_net_wait_seconds", 29), ("drawer_net_wait_seconds", 901), ("drawer_net_wait_seconds", True),
    ("drawer_net_wait_seconds", 0), ("drawer_net_wait_seconds", "x"),
    ("idle_poweroff_seconds", 59), ("idle_poweroff_seconds", 3601), ("idle_poweroff_seconds", 0),
    ("end_poweroff_seconds", 9), ("end_poweroff_seconds", 301), ("end_poweroff_seconds", 0),
])
def test_a_bad_value_is_the_constant_logged_and_not_reported(fake_machine, field, bad):
    hello, ceilings, log = run_answers(fake_machine, [answer(**{**GOOD, field: bad})])
    want = {k: v for k, v in GOOD.items() if k != field}
    assert hello["applied_timing"] == want
    mine = {"idle_poweroff_seconds": ("idle",), "end_poweroff_seconds": ("end",),
            "drawer_net_wait_seconds": ("stall", "udp"), "drawer_disk_wait_seconds": ("disk", "chunk")}.get(field, ())
    for key in mine:
        assert ceilings[key] == CONST[key], key
    assert f"{field}: the agent's own" in log


def test_an_answer_without_the_fields_is_the_constants_and_an_empty_applied(fake_machine):
    """An older server, or an unregistered machine (the server sends nothing):
    applied_timing is {} -- present, so the server overwrites an earlier record."""
    hello, ceilings, log = run_answers(fake_machine, [answer()])
    assert hello["applied_timing"] == {}
    assert ceilings == CONST
    assert "lost_wait_seconds: the agent's own" in log and "not in the answer" in log


def test_a_value_that_stops_coming_goes_back_to_the_constant(fake_machine):
    hello, ceilings, _ = run_answers(fake_machine, [answer(**GOOD), answer()])
    assert hello["applied_timing"] == {}
    assert ceilings == CONST


def test_the_log_line_is_once_per_change_not_once_per_hello(fake_machine):
    _, _, log = run_answers(fake_machine, [answer(**GOOD)] * 3 + [answer()] * 2)
    lines = [ln for ln in log.splitlines() if "timing:" in ln]
    assert len(lines) == 2, lines


# --- the tests' own short ceilings stay theirs ------------------------------


def test_an_environment_override_wins_and_is_not_reported(fake_machine):
    """WAIT_STREAM_STALL_S=20 / END_POWEROFF_S=2 / FANOUT_STALL_EVICT_MS=3000 are the tests' (and a lab's)
    override: the server's value is not applied over them, so it is not said."""
    hello, ceilings, _ = run_answers(
        fake_machine, [answer(**GOOD)],
        env="export WAIT_STREAM_STALL_S=20 END_POWEROFF_S=2 FANOUT_STALL_EVICT_MS=3000; ")
    assert hello["applied_timing"] == {"lost_wait_seconds": 240, "idle_poweroff_seconds": 600}
    # chunkcast's own variable was not set: it keeps the agent's 120, the server's 90 is not applied over the disk override either
    assert ceilings == {"stall": 20, "udp": 20, "idle": 600, "end": 2, "disk": 3000, "chunk": 120000}


def test_timing_is_loaded_before_the_libraries_that_default_the_ceilings():
    """The override is read when timing.sh loads -- before waits.sh, restore.sh and
    idle.sh fill the variables in. After them every value would look overridden."""
    order = agent_order()
    assert "timing" in order, "imagesrv-agent does not source timing.sh"
    for lib in ("waits", "restore", "idle"):
        assert order.index("timing") < order.index(lib), f"timing.sh after {lib}.sh"


# --- what the values drive --------------------------------------------------


def _with_answer(machine: dict, timing: dict, script: str) -> str:
    run = machine["run"]
    (run / "answer.json").write_text(json.dumps(answer(**timing)), encoding="utf-8")
    names = [n for n in LIBS if n != "timing" or TIMING.exists()]
    return sh(
        _path(run) + UNSET + f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/agent.log IDLE_WARN_S=1; '
        + agent_sources(*names)
        + "min_free_take() { :; }\n" + _cut("read_answer")
        + f'RESP={posix(run)!r}/response.json; cp {posix(run)!r}/answer.json "$RESP"; read_answer; '
        "log() { printf '%s\\n' \"$*\"; }; sync() { :; }; "
        "safe_poweroff() { echo \"POWEROFF $1\"; }; " + script)


def test_the_idle_power_off_follows_the_server(fake_machine):
    """100 s without activity: off with the server's 60, on with the agent's 300."""
    script = ('echo "100000.5 0" > "$RUN_DIR/uptime"; export UPTIME_FILE="$RUN_DIR/uptime"; '   # #3681: idle.since is uptime
              'printf "%s\\n" "$(( $(idle_up) - 100 ))" > "$RUN_DIR/idle.since"; '
              'D_SESSION_STATE=none D_TASK=null; idle_check; echo rc=$?')
    out = _with_answer(fake_machine, {"idle_poweroff_seconds": 60}, script)
    assert "POWEROFF idle: no activity for 60s" in out
    out = _with_answer(fake_machine, {}, script)
    assert "POWEROFF" not in out and "rc=0" in out


def test_the_end_of_task_count_follows_the_server(fake_machine):
    script = ('end_problems() { return 1; }; finish_and_stop() { echo STOPPED; }; '
              '(sleep 1; touch "$RUN_DIR/end.now") & end_countdown drawers "drawers complete" 1 1 sda; wait')
    out = _with_answer(fake_machine, {"end_poweroff_seconds": 10}, script)
    assert "powering off in 10s" in out and "STOPPED" in out


# --- one rule, two implementations ------------------------------------------


def test_the_ranges_are_the_servers():
    from server.agent import timing   # lazy: the agent side is what this file checks
    text = TIMING.read_text(encoding="utf-8")
    for key, wire in timing.WIRE.items():
        _default, low, high = timing.RANGES[key]
        m = re.search(rf"_timing_pick {wire} \"\$1\" (\d+) (\d+)", text)
        assert m, f"timing.sh does not read {wire}"
        assert (int(m.group(1)), int(m.group(2))) == (low, high), wire


def test_the_servers_defaults_are_the_agents_constants():
    """A machine that applies the server's default runs exactly as one that got
    nothing: the default changes nothing on either side."""
    from server.agent import timing
    waits = (AGENT / "lib" / "core" / "waits.sh").read_text(encoding="utf-8")
    idle = (AGENT / "lib" / "core" / "idle.sh").read_text(encoding="utf-8")
    stall = int(re.search(r'WAIT_STREAM_STALL_S="\$\{WAIT_STREAM_STALL_S:-(\d+)\}"', waits).group(1))
    disk = int(re.search(r'DISK_STALL_S="\$\{DISK_STALL_S:-(\d+)\}"', TIMING.read_text(encoding="utf-8")).group(1))
    assert timing.RANGES[timing.NET_WAIT][0] == stall == 120   # Nadav 18/11
    assert timing.RANGES[timing.DISK_WAIT][0] == disk == 120
    assert f'IDLE_POWEROFF_S="${{IDLE_POWEROFF_S:-{timing.RANGES[timing.IDLE][0]}}}"' in idle
    assert f'END_POWEROFF_S="${{END_POWEROFF_S:-{timing.RANGES[timing.END][0]}}}"' in idle


def test_the_c_defaults_of_the_disk_ceiling_are_the_same_120():
    """fanout and chunkcast read the ceiling from their environment; run without it (a lab, a bench)
    they must give up at the same 120 s -- two numbers for one wait is what #3035 was."""
    for source, name in (("fanout.c", "STALL_EVICT_MS"), ("chunkcast.c", "CC_WRITE_STALL_MS")):
        text = (AGENT / source).read_text(encoding="utf-8")
        assert re.search(rf"^#define {name} 120000\b", text, re.M), (source, name)
