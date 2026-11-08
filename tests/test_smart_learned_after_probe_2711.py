"""#2711 -- "rose since the previous round" (#1859) is compared with what THIS round read.

A cloner woken into a round (the room's usual path: it powers off after every round) sends its
one hello before any probe -- the agent's SMART cache is per boot, so `smart_attrs: null` --
and the server's `learned_for` skips a disk with nothing read now (`smart_learned: []`).
`smart_preflight` then read that answer and probed. The comparison of the reading of now with
the serial's previous snapshot was done nowhere the agent reads: a disk whose 188 rose was
written as clean. That is "looks fine when it was not checked" (principle 5).

The fix (agent only, no new field): the gate probes first, then sends one NON-joining hello
(`attended_hello` -- the heartbeat a running task already sends; its answer never touches
response.json) and reads `smart_learned` from that answer. The server code is the one it is.

The test runs the real gate (`smart_preflight`) over a fake smartctl, and the stubbed POST runs
the server's real `smart_history.learned_for` on the body the agent really built, against a
database holding the previous round's snapshot of the same serial.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

import fixtures_smart as F
from test_smart import posix, preflight

# The agent reads the answer with jq (jsonq.sh). Without jq the expression would not run at all --
# skipped by name, never "passed" (the testrunner and the public CI have it).
pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq אינו מותקן — הביטוי לא היה רץ בכלל")

REPO = Path(__file__).resolve().parent.parent
SERIAL = "S-sda"   # the harness's disk_serial (test_smart.fake_memory)
MAC = "aa:bb:cc:dd:ee:ff"

HELPER = '''
import json, sys
sys.path.insert(0, {repo!r})
from server import db
from server.disks import smart_history
conn = db.connect({db!r})
body = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({{"schema": 1, "smart_learned": smart_history.learned_for(conn, body.get("disks"))}}))
'''


def disk(timeouts: int) -> str:
    """An 860 EVO with 188 Command_Timeout -- `info` in the knowledge table: never orange by itself."""
    return F.evo860() + F.row(188, "Command_Timeout", "0x0032", 100, 100, 0, "Old_age", str(timeouts))


def setup(tmp_path: Path, before: int) -> str:
    """The server's side: last round's snapshot of this serial, 188 raw = `before`. Returns the stub."""
    from server import db
    conn = db.connect(tmp_path / "srv.db")
    attrs = {"ata": [[5, 100, 100, 10, 0, "Reallocated_Sector_Ct"], [188, 100, 100, 0, before, "Command_Timeout"]],
             "nvme": None}
    conn.execute("INSERT INTO smart_history (serial, session_id, mac, at, attrs_json) VALUES (?,?,?,?,?)",
                 (SERIAL, "ses_prev", MAC, "2026-11-08T05:11:09+00:00", json.dumps(attrs)))
    conn.commit()
    helper = tmp_path / "server_hello.py"
    helper.write_text(HELPER.format(repo=str(REPO), db=str(tmp_path / "srv.db")), encoding="utf-8")
    proc = tmp_path / "sysro" / "proc"   # build_hello's memory_bytes
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "meminfo").write_text("MemTotal:        8040580 kB\n", newline="\n")
    nic = tmp_path / "sysro" / "sys" / "class" / "net" / "eth0"   # build_hello's identity: IFACE's address
    nic.mkdir(parents=True, exist_ok=True)
    (nic / "address").write_text(MAC + "\n", newline="\n")
    # The boot hello's answer, from the same server code: the disk had no reading yet -> [].
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    boot = tmp_path / "boot.json"
    boot.write_text(json.dumps({"disks": [{"dev": "sda", "serial": SERIAL, "smart_attrs": None}]}), encoding="utf-8")
    py = posix(Path(sys.executable))
    # every POST: kept (what the agent sent), answered by the server's function
    # the real json_get_join (jsonq.sh) -- the harness's fake_memory answers "ok" for any file
    return ('export IFACE=eth0 IP=10.44.0.5; json_get_join() { jq -r "$2 | .[]?" "$1" 2>/dev/null; }; '
            f'http_post_json() {{ _n=$(ls "$RUN_DIR"/posted.*.json 2>/dev/null | wc -l); '
            f'cp "$2" "$RUN_DIR/posted.$_n.json"; "{py}" {posix(helper)!r} "$2"; }}; '
            f'"{py}" {posix(helper)!r} {posix(boot)!r} > "$RUN_DIR/response.json"; ')


def test_a_rise_read_in_this_round_is_orange_on_a_woken_cloner(tmp_path):
    """The bug: 188 rose 0 -> 7 since the previous round; the boot hello had no reading."""
    stub = setup(tmp_path, before=0)
    written, run, err = preflight(tmp_path, disk(7), extra=stub)
    assert written == "sda", err
    note = run / "targets/sda/smart_note"
    assert note.exists(), "the disk was written as clean -- what rose was never compared (#2711)\n" + err
    assert "פקודות שנתקעו (188) עלו מאז הסבב הקודם" in note.read_text(encoding="utf-8")
    ev = json.loads((run / "disk_event.json").read_text())
    assert (ev["smart"]["verdict"], ev["smart"]["reason"]) == ("warn", "timeout_rose"), ev["smart"]


def test_the_comparison_hello_carries_this_rounds_reading_and_does_not_join(tmp_path):
    stub = setup(tmp_path, before=0)
    _, run, err = preflight(tmp_path, disk(7), extra=stub)
    hellos = [json.loads(p.read_text()) for p in sorted(run.glob("posted.*.json"))
              if '"all_macs"' in p.read_text()]
    assert hellos, "no hello after the probe\n" + err
    h = hellos[0]
    assert h["joining"] is False, "a joining hello mid-round marks the member lost (#1728)"
    (d,) = [d for d in h["disks"] if d["dev"] == "sda"]
    assert [188, 100, 100, 0, 7] in d["smart_attrs"]["ata"], d["smart_attrs"]
    assert (run / "response.json").read_text() == json.dumps({"schema": 1, "smart_learned": []}) + "\n", \
        "the main loop's answer is not touched under a running task"


def test_nothing_rose_is_not_orange(tmp_path):
    stub = setup(tmp_path, before=7)
    written, run, err = preflight(tmp_path, disk(7), extra=stub)
    assert written == "sda" and not (run / "targets/sda/smart_note").exists(), err


def test_a_hello_that_does_not_go_through_is_said_and_never_blocks(tmp_path):
    """Principle 5: "could not compare" is logged as such; the boot answer's (empty) word stands,
    and the gate goes on -- this is an orange note, not a gate that can stop a round."""
    stub = setup(tmp_path, before=0)
    stub += 'attended_hello() { return 1; }; '
    written, run, err = preflight(tmp_path, disk(7), extra=stub)
    assert written == "sda", err
    assert "not compared with the previous round" in err, err
