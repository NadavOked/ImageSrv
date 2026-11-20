"""#3268 (B62, Nadav 18/11) -- the agent half: the waiting-screen write check goes to the server, and what the screen shows is the brain's decision.

Nadav, on #3650: "the brain decides if it fell behind last time AND if the check showed it falls behind now". The server half is
`test_brain_slowdisk_b62.py`. Here:

- up: `idle_wc_report_json` -- the hello's `write_check`: serial, state, MB/s of the last half, of the first half, and the reason when slow.
  Only a finished, numbered ok / slow check; a number JSON does not accept is no report (a hello that does not parse is a machine
  nobody hears, #3632); the whole hello stays valid JSON.
- down: `idle_wc_decisions_refresh` + `idle_wc_decision` -- the answer's `write_decisions`, shown as the brain said it, but only for exactly
  the result it decided on. RED = slow|the reason, INFO = history|a neutral line (no colour).
- **no decision = the check's own word**, as before the rule. A missing decision is not "ok": a disk the check says slow stays red.
- the whole loop through the real server: the agent's own report, the server's decision, the agent's field.
- the screen: red for `slow` only; `history` is grey.
- **18/11 (after #3718): a check that passed can still be red** -- when the server's decision on it is RED (the last wave it fell behind in, it wrote
  under the floor all through): `slow|` + the server's words. The floor goes up with the report (`floor_mbps`), the one the check used for that drawer.

**Negative control:** the production files of this PR back at the base (`609f2d416`, #3650) -- the agent has no report and no decision, so
the tests below fail on behaviour (an empty report, the check's own word where the brain's was expected).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from test_cloner_gui import _token
from test_idlecheck_3268 import DRIVER, LIBS, _make, _seed
from test_idlelong_3268 import DISK, _long
from test_server_room import CLONER1, room_server  # noqa: F401 -- room_server is a fixture
from test_native_standby_1304 import _GUI, _count, _render as _standby
from test_smart import BASH, posix  # noqa: F401 -- the same bash, declared skip without it
from test_write_rate_3295 import _earlier

from agentlib import flat_lib
from native import requires_native
from server import db

AGENT = requires_native(("bash", BASH), ("jq", shutil.which("jq")), ("sha256sum", shutil.which("sha256sum")),
                        ("awk", shutil.which("awk")))

APS = "t10.ATA     APS-SL3N-512                            SICSD40302WL"   # with its spaces (#3574)
SAMSUNG = "naa.5002538d42a58a2c"
SLOW59 = "איטי — 59 MB/s, מתחת לסף 74 MB/s"
BRAIN_RED = SLOW59 + "; וגם בגל הקודם פיגר (55MB/s, 17/11)"
BRAIN_INFO = "פיגר בגל הקודם (55MB/s, 17/11); הבדיקה עכשיו תקינה"
BRAIN_HISTORY = "בגל הקודם כתב 43MB/s לאורך כל הגל (17/11), מתחת לסף 74 MB/s — הבדיקה הקצרה לא מספיק ארוכה כדי לראות את זה"


def _agent(tmp_path: Path, script: str, extra: str = "", long_mib: int | None = None) -> subprocess.CompletedProcess:
    driver = DRIVER % {"libs": " ".join((*LIBS, "idlelong", "idlebrain")), "lib": posix(flat_lib())}
    if long_mib is not None:
        extra += f"export WRITECHECK_LONG_MIB={long_mib} IDLE_WC_LONG_WAIT_S=60 IDLE_WC_LONG_NET_S=30\n"
    driver = driver.replace("set +e\n", extra + "set +e\n")
    return subprocess.run([BASH, "-c", driver, "x", posix(tmp_path), script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=240)


def _result(tmp_path: Path, mbps: str, first: str = "", state: str = "ok", name: str = "sdx") -> None:
    """A finished check of a drawer, as `_ic_res` leaves it (`key` is whatever: the report does not read it)."""
    _seed(tmp_path, name, state, mbps=mbps, key="x|131072")
    (tmp_path / "run" / "idlecheck" / f"res.{name}" / "first").write_text(first)


def _disk(tmp_path: Path, serial: str = APS) -> None:
    _make(tmp_path, serial=serial)


def _answer(tmp_path: Path, decisions: list[dict] | None, raw: str | None = None) -> None:
    body: dict = {"writecheck_min_mbps": 74}
    if decisions is not None:
        body["write_decisions"] = decisions
    (tmp_path / "run" / "response.json").write_text(raw if raw is not None else json.dumps(body, ensure_ascii=False))


def _dec(verdict: str, state: str, mbps: int, first: int | None, reason: str, serial: str = APS) -> dict:
    return {"serial": serial, "verdict": verdict, "state": state, "mbps": mbps, "first_mbps": first, "reason": reason}


def _field(tmp_path: Path) -> str:
    return _agent(tmp_path, "idle_wc_decisions_refresh; idle_wc_field sdx").stdout.strip()


def _report(tmp_path: Path) -> list[dict]:
    out = _agent(tmp_path, "idle_wc_report_json")
    assert out.returncode == 0, out.stderr
    if not out.stdout:
        return []
    assert out.stdout.startswith(',"write_check":['), out.stdout            # a fragment with a leading comma, like its siblings
    return json.loads("{" + out.stdout[1:] + "}")["write_check"]           # and valid JSON by itself


# --- up: the report ---------------------------------------------------------------------------------------------------

@AGENT
def test_a_finished_check_is_reported_with_both_halves_and_the_reason_when_slow(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    assert _report(tmp_path) == [{"serial": APS, "state": "ok", "mbps": 120, "first_mbps": 200, "floor_mbps": 74, "reason": ""}]
    _result(tmp_path, "59", first="61")
    assert _report(tmp_path) == [{"serial": APS, "state": "slow", "mbps": 59, "first_mbps": 61, "floor_mbps": 74, "reason": SLOW59}]


@AGENT
def test_the_report_is_what_the_server_reads(tmp_path):
    """The two halves of the wire agree: what the agent sends is what `slowdisk.well_formed` takes."""
    from server.brain import slowdisk   # noqa: PLC0415

    _disk(tmp_path)
    _result(tmp_path, "59", first="61")
    (now,) = slowdisk.well_formed(_report(tmp_path), [{"serial": APS}])
    assert (now.serial, now.state, now.mbps, now.first_mbps, now.reason, now.floor_mbps) == (APS, "slow", 59, 61, SLOW59, 74)


@AGENT
def test_no_first_half_is_null_not_zero_and_a_short_check_is_still_a_report(tmp_path):
    _disk(tmp_path)
    for first in ("", "x", "007", "-5", "6.5"):
        _result(tmp_path, "120", first=first)
        assert _report(tmp_path) == [{"serial": APS, "state": "ok", "mbps": 120, "first_mbps": None, "floor_mbps": 74, "reason": ""}], first


@AGENT
@pytest.mark.parametrize("state, mbps, why", [
    ("failed", "120", "the disk refused the write"), ("unchecked", "120", "no copy on the server"),
    ("ok", "", "a result with no number is not a measurement"), ("ok", "x", "not a number"), ("ok", "012", "a number JSON refuses"),
    ("ok", "5.5", "not a whole number"),
])
def test_only_a_finished_numbered_ok_or_slow_check_is_reported(tmp_path, state, mbps, why):
    _disk(tmp_path)
    _result(tmp_path, mbps, first="200", state=state)
    assert _report(tmp_path) == [], why


@AGENT
def test_a_disk_still_being_checked_a_disk_with_no_serial_and_no_result_are_not_reported(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    (tmp_path / "run" / "idlecheck" / "busy.sdx").write_text("")                     # the check runs again on it
    assert _report(tmp_path) == []
    (tmp_path / "run" / "idlecheck" / "busy.sdx").unlink()
    (tmp_path / "sys" / "block" / "sdx" / "device" / "serial").write_text("")
    assert _report(tmp_path) == [], "no serial: nothing the server could match"
    shutil.rmtree(tmp_path / "run" / "idlecheck")
    assert _report(tmp_path) == []


@AGENT
def test_the_whole_hello_stays_valid_json_with_the_report_even_for_a_serial_that_needs_escaping(tmp_path):
    odd = 'SER"1\\x'
    _disk(tmp_path, serial=odd)
    _result(tmp_path, "59", first="61")
    net = tmp_path / "sys" / "class" / "net" / "eth0"
    net.mkdir(parents=True)
    (net / "address").write_text("b4:2e:99:07:1a:c4\n")
    (tmp_path / "proc" / "meminfo").write_text("MemTotal:        8388608 kB\n")
    out = _agent(tmp_path, "build_hello true", extra="export IFACE=eth0 IP=10.44.12.187 AGENT_VERSION=t\n")
    assert out.returncode == 0, out.stderr
    hello = json.loads(out.stdout)                                                       # a hello that does not parse is a machine nobody hears (#3632)
    assert hello["write_check"] == [{"serial": odd, "state": "slow", "mbps": 59, "first_mbps": 61, "floor_mbps": 74, "reason": SLOW59}]
    assert [d["serial"] for d in hello["disks"]] == [odd], "the report names the same string the disk entry does"
    assert hello["mac"] == "b4:2e:99:07:1a:c4" and hello["schema"] == 2


@AGENT
def test_two_drawers_two_reports_and_only_the_slow_one_says_why(tmp_path):
    _make(tmp_path, serial=SAMSUNG)
    (tmp_path / "dev" / "sdy").write_bytes(b"\0" * 1048576)
    for d in ("sys/block/sdy/device", "sys/block/sdy/queue"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for name, text in (("size", "2048"), ("removable", "0"), ("device/serial", APS), ("queue/rotational", "1")):
        (tmp_path / "sys/block/sdy" / name).write_text(text)
    _result(tmp_path, "120", first="200", name="sdx")
    _result(tmp_path, "40", first="45", name="sdy")
    got = {r["serial"]: r for r in _report(tmp_path)}
    assert set(got) == {SAMSUNG, APS}, got
    assert (got[SAMSUNG]["state"], got[SAMSUNG]["reason"]) == ("ok", "")
    assert (got[APS]["state"], got[APS]["mbps"]) == ("slow", 40) and got[APS]["reason"].startswith("איטי — 40 MB/s"), got


# --- the floor the check used goes with the report (Nadav 18/11): the brain compares the last wave's sustained rate to it ---------

def _hdd(tmp_path: Path, rotational: str, **answer: object) -> None:
    (tmp_path / "sys" / "block" / "sdx" / "queue" / "rotational").write_text(rotational)
    (tmp_path / "run" / "response.json").write_text(json.dumps({"writecheck_min_mbps": 74, **answer}))


@AGENT
@pytest.mark.parametrize("rotational, answer, floor", [
    ("0", {}, 74), ("0", {"hdd_support": True}, 74), ("0", {"hdd_support": False}, 74),   # a solid disk: the console's floor
    ("1", {}, 74),                                                                          # an older server that does not say: the check uses the floor too (_wc_slow_why)
    ("1", {"hdd_support": True}, None), ("1", {"hdd_support": False}, None),               # a mechanical disk with the HDD switch set: no SSD floor
    ("?", {}, 74),                                                                          # rotational not read: as a solid disk
])
def test_the_report_carries_the_floor_the_check_used_for_that_drawer(tmp_path, rotational, answer, floor):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    _hdd(tmp_path, rotational, **answer)
    (entry,) = _report(tmp_path)
    assert entry["floor_mbps"] == floor, entry
    if floor is None:
        assert "null" in _agent(tmp_path, "idle_wc_report_json").stdout


@AGENT
def test_with_no_answer_the_floor_is_the_agents_own_and_never_none(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    (tmp_path / "run" / "response.json").unlink()
    (entry,) = _report(tmp_path)
    assert entry["floor_mbps"] == 50, "the check's own floor when the server's answer is not there: the same number the check judged by"


# --- the sustained check keeps both halves ---------------------------------------------------------------------------

@AGENT
def test_the_sustained_check_reports_what_it_measured_in_each_half(tmp_path):
    # 52MiB at 200 MB/s, then 40 MB/s: the first half is the cache, the last half is the drive (#3268)
    _long(tmp_path, model="52 200 40", size_mib=DISK)
    out = _agent(tmp_path, "idle_wc_disk sdx", long_mib=64)
    assert out.returncode == 0, out.stderr
    res = tmp_path / "run" / "idlecheck" / "res.sdx"
    assert (res / "state").read_text() == "ok", (res / "reason").read_text()
    last, first = int((res / "mbps").read_text()), int((res / "first").read_text())
    said = re.search(r"(\d+) MB/s בחצי האחרון \((\d+) בחצי הראשון\)", (res / "reason").read_text())
    assert said and (int(said.group(1)), int(said.group(2))) == (last, first), "the report and the words of the check are one measurement"
    assert 35 <= last <= 45 and first >= 2 * last, (first, last)   # the first half still has some of the cache in it: 3x, not 5x
    (entry,) = _report(tmp_path)
    assert (entry["state"], entry["mbps"], entry["first_mbps"]) == ("slow", last, first), entry
    assert entry["reason"].startswith(f"איטי — {last} MB/s, מתחת לסף 74 MB/s")


@AGENT
def test_a_check_with_no_sustained_part_reports_its_rate_and_no_first_half(tmp_path):
    _long(tmp_path, model="52 200 40", size_mib=DISK)
    out = _agent(tmp_path, "idle_wc_disk sdx", long_mib=0)
    assert out.returncode == 0, out.stderr
    (entry,) = _report(tmp_path)
    assert entry["first_mbps"] is None and entry["mbps"] > 0, entry
    assert (tmp_path / "run" / "idlecheck" / "res.sdx" / "first").read_text() == ""


# --- down: the decision -----------------------------------------------------------------------------------------------

@AGENT
def test_a_red_decision_is_shown_as_the_brain_said_it(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "59", first="61")
    _answer(tmp_path, [_dec("RED", "slow", 59, 61, BRAIN_RED)])
    assert _field(tmp_path) == "slow|" + BRAIN_RED, "the screen shows the brain's words, not a line the agent put together"


@AGENT
def test_an_information_decision_is_a_neutral_line_and_not_a_colour(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    _answer(tmp_path, [_dec("INFO", "ok", 120, 200, BRAIN_INFO)])
    assert _field(tmp_path) == "history|" + BRAIN_INFO


@AGENT
def test_no_decision_is_the_checks_own_word_and_a_slow_disk_stays_red(tmp_path):
    """Principle 5: 'no decision' is not 'ok' and not 'slow'. It is what the check said, as before the rule."""
    _disk(tmp_path)
    for answer in (None, [], [_dec("NONE", "ok", 120, 200, "x")]):
        _result(tmp_path, "120", first="200")
        _answer(tmp_path, answer)
        assert _field(tmp_path) == "ok|", answer                                    # not 'history', and nothing the brain did not say
        _result(tmp_path, "59", first="61")
        assert _field(tmp_path) == "slow|" + SLOW59, answer                         # red comes from the check of now, with no decision at all
    _result(tmp_path, "40", first="45")
    (tmp_path / "run" / "response.json").unlink()                                    # no answer at all: the floor is the agent's own (50)
    assert _field(tmp_path) == "slow|איטי — 40 MB/s, מתחת לסף 50 MB/s"


@AGENT
@pytest.mark.parametrize("name, decision", [
    ("another disk", _dec("INFO", "ok", 120, 200, BRAIN_INFO, SAMSUNG)),
    ("the serial with its spaces squeezed", _dec("INFO", "ok", 120, 200, BRAIN_INFO, APS.replace("     ", " "))),
    ("a prefix of the serial", _dec("INFO", "ok", 120, 200, BRAIN_INFO, APS[:20])),
    ("the check was measured again (other rate)", _dec("INFO", "ok", 121, 200, BRAIN_INFO)),
    ("the check was measured again (other first half)", _dec("INFO", "ok", 120, 201, BRAIN_INFO)),
    ("no first half where the check has one", _dec("INFO", "ok", 120, None, BRAIN_INFO)),
    ("a state that is not the local one", _dec("RED", "slow", 120, 200, BRAIN_RED)),
    ("INFO about a result that says slow", _dec("INFO", "slow", 120, 200, BRAIN_INFO)),
    ("a word that is not RED or INFO", _dec("OK", "ok", 120, 200, BRAIN_INFO)),
    ("a decision with no words", _dec("INFO", "ok", 120, 200, "")),
])
def test_a_decision_on_another_result_is_not_applied(tmp_path, name, decision):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    _answer(tmp_path, [decision])
    assert _field(tmp_path) == "ok|", name


@AGENT
def test_the_verdict_must_fit_the_word_of_the_check(tmp_path):
    """Even a decision that names the same numbers is not drawn when its verdict does not fit: RED only for slow, INFO only for ok."""
    _disk(tmp_path)
    _result(tmp_path, "59", first="61")                                              # the check says slow
    _answer(tmp_path, [_dec("INFO", "slow", 59, 61, BRAIN_INFO)])
    assert _field(tmp_path) == "slow|" + SLOW59, "an information line must not replace a red one"
    _result(tmp_path, "120", first="200")                                            # the check says ok
    _answer(tmp_path, [_dec("RED", "slow", 120, 200, BRAIN_RED)])
    assert _field(tmp_path) == "ok|", "a decision that says the check was slow is not about this result"


@AGENT
def test_a_disk_that_passed_but_wrote_under_the_floor_all_wave_is_red_as_the_brain_said_it(tmp_path):
    """Nadav 18/11: the short check passes (213 MB/s of the cache) and the history is the reason for red: 'slow|' + the server's words."""
    _disk(tmp_path)
    _result(tmp_path, "216", first="213")
    _answer(tmp_path, [_dec("RED", "ok", 216, 213, BRAIN_HISTORY)])
    assert _field(tmp_path) == "slow|" + BRAIN_HISTORY
    _result(tmp_path, "217", first="213")                                            # checked again: another result, the decision is not about it
    assert _field(tmp_path) == "ok|"
    _result(tmp_path, "216", first="213")
    _answer(tmp_path, [_dec("RED", "ok", 216, 213, "")])                             # a decision with no words is not drawn
    assert _field(tmp_path) == "ok|"
    _answer(tmp_path, [_dec("RED", "ok", 216, 213, BRAIN_HISTORY, SAMSUNG)])         # of another disk
    assert _field(tmp_path) == "ok|"


@AGENT
def test_a_decision_on_a_result_that_changed_is_not_applied_and_the_check_alone_speaks(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "59", first="61")
    _answer(tmp_path, [_dec("RED", "slow", 59, 61, BRAIN_RED)])
    assert _field(tmp_path) == "slow|" + BRAIN_RED
    _result(tmp_path, "90", first="95")                                              # checked again: fine now
    assert _field(tmp_path) == "ok|"


@AGENT
def test_failed_and_unchecked_are_said_as_they_were_and_the_round_is_not_blocked(tmp_path):
    _disk(tmp_path)
    _answer(tmp_path, [_dec("INFO", "ok", 120, 200, BRAIN_INFO)])
    _seed(tmp_path, "sdx", "failed", reason="הדיסק לא מקבל כתיבה — x", key="x|131072")
    assert _field(tmp_path) == "failed|הדיסק לא מקבל כתיבה — x"
    _seed(tmp_path, "sdx", "unchecked", reason="אין מספר סידורי", key="x|131072")
    assert _field(tmp_path) == "unchecked|אין מספר סידורי"
    _seed(tmp_path, "sdx", "ok", mbps="120", key=re.sub(r"[^A-Za-z0-9._-]", "_", APS) + "|131072")   # _ic_ident: the serial as a file name
    out = _agent(tmp_path, "idle_wc_decisions_refresh; idle_wc_adopt sdx; echo RC=$?")
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)   # a decision must not stop the round from taking a passed check


@AGENT
def test_the_separator_and_the_newline_cannot_split_a_decision(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    _answer(tmp_path, [_dec("INFO", "ok", 120, 200, "a|b\nc\rd")])
    assert _field(tmp_path) == "history|a b c d"


@AGENT
def test_an_answer_that_was_not_read_keeps_the_previous_decision_and_one_that_has_none_drops_it(tmp_path):
    _disk(tmp_path)
    _result(tmp_path, "120", first="200")
    _answer(tmp_path, [_dec("INFO", "ok", 120, 200, BRAIN_INFO)])
    assert _field(tmp_path) == "history|" + BRAIN_INFO
    _answer(tmp_path, None, raw='{"write_decisions": [{"serial": ')                  # a hello answer mid-write
    out = _agent(tmp_path, "idle_wc_decisions_refresh; echo RC=$?; idle_wc_field sdx")
    assert "RC=1" in out.stdout and "history|" + BRAIN_INFO in out.stdout, out.stdout
    _answer(tmp_path, None)                                                          # read whole, and the brain decided nothing now
    out = _agent(tmp_path, "idle_wc_decisions_refresh; echo RC=$?; idle_wc_field sdx")
    assert "RC=0" in out.stdout and out.stdout.strip().endswith("ok|"), out.stdout


# --- the whole loop, through the real server -------------------------------------------------------------------------

@AGENT
def test_the_agents_own_report_goes_to_the_server_and_its_decision_is_what_the_screen_shows(room_server, tmp_path):
    from test_brain_slowdisk_b62 import BEHIND   # noqa: PLC0415
    from conftest import hello_body   # noqa: PLC0415

    conn = room_server["ctx"].conn
    db.set_setting(conn, "learned_rate_enabled", "true")
    db.set_setting(conn, "writecheck_min_mbps", "74")                            # the floor the server's answer carries: the one of the other tests (default 50)
    _earlier(conn, "ses_earlier", BEHIND)                                        # the APS fell behind in a wave
    _disk(tmp_path)
    (tmp_path / "sys" / "block" / "sdx" / "queue" / "rotational").write_text("0")   # the server's answer says hdd_support=false: a spinning disk is "a source only", slow in itself

    def loop(mbps: str, first: str) -> str:
        _result(tmp_path, mbps, first=first)
        body = hello_body(CLONER1)
        body["disks"] = [{"dev": "sda", "size_bytes": 256060514304, "model": "Drawer SSD", "serial": APS, "removable": False,
                          "scheme": "gpt", "has_data": False, "port": 1}]
        body["write_check"] = _report(tmp_path)                                    # what the agent itself would put in its hello
        assert body["write_check"], "the agent reported nothing"
        answer = room_server["anon"].post("/api/v1/agent/hello", json=body).json()
        (tmp_path / "run" / "response.json").write_text(json.dumps(answer, ensure_ascii=False))
        return _field(tmp_path)

    red = loop("59", "61")
    assert red.startswith("slow|" + SLOW59 + "; וגם בגל הקודם פיגר ("), red          # the check says slow now; the brain adds the history
    exposed = loop("216", "213")                                                     # passes now (the cache), but the wave it fell behind in wrote 61 < the floor 74
    assert exposed.startswith("slow|בגל הקודם כתב 61MB/s לאורך כל הגל (") and exposed.endswith("הבדיקה הקצרה לא מספיק ארוכה כדי לראות את זה"), exposed
    from test_brain_slowdisk_b62 import _wrote   # noqa: PLC0415
    _earlier(conn, "ses_newer", _wrote(248))                                         # the newest wave it fell behind in: 248, over the floor
    info = loop("120", "200")
    assert info.startswith("history|פיגר בגל הקודם (248MB/s, ") and info.endswith("); הבדיקה עכשיו תקינה"), info
    db.set_setting(conn, "learned_rate_enabled", "false")                        # the server cannot say if it fell behind: no decision to show
    assert loop("121", "201") == "ok|", "no decision: the check's own word"
    assert loop("58", "60") == "slow|איטי — 58 MB/s, מתחת לסף 74 MB/s", "red stays red without any history"


# --- the screen -------------------------------------------------------------------------------------------------------

def _cdisk(wstate: str, wtext: str) -> str:
    return "cdisk=" + "|".join(["1", "sda", "Healthy Model", "500107862016", "passed", "", "3.0", "3.0", "0", "1", "SATA 0",
                               "clean", "", "", "", "", "", wstate, wtext]) + "\n"


@_GUI
def test_the_screen_is_red_only_for_a_disk_slow_now_and_the_information_is_a_grey_line(tmp_path):
    red = {}
    for name, state, text in (("ok", "ok", ""),
                              ("history", "history", BRAIN_INFO),
                              ("slow", "slow", BRAIN_RED),
                              ("unknown", "bogus", "x")):
        img = _standby(tmp_path, name, _cdisk(state, text))["light"]
        red[name] = _count(img, (0, 0, img[0], img[1]), _token("--clr-danger"), tol=14)
    assert red["slow"] > red["ok"] + 100, f"a disk slow now is not red: {red}"
    assert red["history"] <= red["ok"] + 20, f"the information alone painted red: {red}"
    assert red["unknown"] <= red["ok"] + 20, f"an unknown word must never be drawn as a verdict: {red}"
