"""#3816 (K17): what `drawer_tune` chose reaches the brain, in shadow -- no new threshold, no change of behaviour.

Agent: `drawer_tune` leaves its EFFECTIVE choice in `$RUN_DIR/drawer_tune.json`; `ss_tune_json` (sensecore.sh) hands it to the
progress report as `senses.tune`, `null` when `drawer_tune` did not run. An unreadable CPU count is `null`, never 0.
Server: a terminal report writes ONE `K17/DRAWER_TUNE` row, `mode=shadow`, `outcome='n/a: shadow'`: `OBSERVED` with the
choice in the evidence, `NOT_READ` when the agent said nothing (never "the default"); the switch `off` writes nothing; a
corrupt `tune` does not raise and is `NOT_READ`.

**Negative control:** with `agent/lib/restore/drawerfeed.sh`, `agent/lib/hw/sensecore.sh`, `server/agent/reports.py` and
`server/brain/drawertune.py` back at the base commit, the tests marked (neg) fail on BEHAVIOUR -- no `drawer_tune.json`
(senses carry no `tune` key: the assertion on the VALUE fails, never a missing function), no `K17` row. The tests never import `drawertune`.

#3173: `drawer_tune` (drawerfeed.sh) left with the old receiver's pipeline, and with it the three tests of its choice. What
stays on the agent's side is the sense itself: without it `senses.tune` is `null` -- today, always.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server import db
from server.agent import reports
from server.brain import memory, veto
from test_agent import BASH, posix

REPO = Path(__file__).resolve().parent.parent
SENSE = REPO / "agent" / "lib" / "hw" / "sensecore.sh"
MAC = "78:ac:c0:9b:18:85"


def _sh(tmp_path: Path, body: str) -> str:
    """Runs `body` in bash with sensecore.sh loaded; output to a file, DEVNULL for the pipes."""
    script, out = tmp_path / "s.sh", tmp_path / "out"
    stubs = "".join(f"{f}() {{ printf null; }}\n" for f in ("ss_carrier_json", "ss_psi_json", "ss_ctrl_json", "ss_rx_json", "ss_http_json"))
    script.write_text(f'log() {{ :; }}\nds_temp_tick() {{ :; }}\n{stubs}. {posix(SENSE)}\nRUN_DIR={posix(tmp_path / "run")}\n{body}\n',
                      encoding="utf-8", newline="\n")
    with out.open("wb") as fh:
        rc = subprocess.run([BASH, posix(script)], stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                            timeout=60).returncode
    assert rc == 0, out.read_text(encoding="utf-8", errors="replace")
    return out.read_text(encoding="utf-8").strip()


def _room(tmp_path: Path, states: dict[str, str], tune: str = "", cpus: int | None = 2) -> None:
    for dev, st in states.items():
        d = tmp_path / "run" / "targets" / dev
        d.mkdir(parents=True)
        (d / "state").write_text(st + "\n")
    (tmp_path / "run" / "tune").write_text(tune)
    if cpus is not None:
        (tmp_path / "proc").mkdir()
        (tmp_path / "proc" / "cpuinfo").write_text("".join(f"processor\t: {i}\nmodel name\t: x\n\n" for i in range(cpus)))


def _senses(tmp_path: Path, partition: int | None = 3) -> dict:
    """`senses` of the machine's progress report (the public `senses_machine_json`), after `drawer_tune` when asked."""
    disks = " ".join(d.name for d in sorted((tmp_path / "run" / "targets").iterdir()))
    run = f"drawer_tune {partition} {disks}\n" if partition is not None else ""
    body = f'SYSROOT={posix(tmp_path)}\necho writing > "$RUN_DIR/state"\n{run}echo "{{$(senses_machine_json ses_1 "" | cut -c2-)}}"'
    raw = _sh(tmp_path, body)
    try:
        return json.loads(raw)["senses"]
    except ValueError:
        raise AssertionError(f"not JSON: {raw!r}") from None


# --- the agent --------------------------------------------------------------------------------------------------------


def test_without_drawer_tune_the_sense_is_null(tmp_path):
    """(neg) no drawer_tune ran (#3173: none is left): `null` = not read, not a default."""
    _room(tmp_path, {"sda": "writing"})
    senses = _senses(tmp_path, None)
    assert "tune" in senses and senses["tune"] is None, senses


# --- the server -------------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at, started_at) VALUES ('ses_w', 'grp_CLONERS', 'img', 'p', 1, 300,"
              " 'running', 't', ?, 0, ?)", (db.now_iso(), db.now_iso()))
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('ses_w', ?, 'writing', ?)",
              (MAC, db.now_iso()))
    c.commit()
    yield c
    veto.forget()


def _report(conn, state="done", **extra):
    assert reports.ingest(conn, {"session_id": "ses_w", "mac": MAC, "state": state, "targets": [], **extra})["ok"]


def _rows(conn):
    return [d for d in memory.decisions(conn, rule="K17", limit=100) if d["action"] == "DRAWER_TUNE"]


TUNE = {"decode": "shared", "nice": 0, "direct": 0, "drawers": 3, "cpus": 2, "partition": 1}


def test_a_terminal_report_records_what_the_agent_chose(conn):
    """(neg) the choice is in the evidence as the agent sent it, in shadow, and says nothing was decided."""
    _report(conn, senses={"tune": TUNE})
    [row] = _rows(conn)
    assert row["verdict"] == "OBSERVED" and row["mode"] == "shadow" and row["outcome"] == "n/a: shadow", row
    for part in ("decode=shared", "nice=0", "direct=0", "drawers=3", "cpus=2", "partition=1"):
        assert part in row["evidence"], (part, row["evidence"])
    assert "auto_shared_from=2(measured,Testrunner,2cpu,18/10)" in row["evidence"], row["evidence"]
    assert row["entity_kind"] == "machine" and row["entity_key"] == MAC and row["round_id"] == "ses_w"


def test_no_tune_is_not_read_never_the_default(conn):
    """(neg) senses without `tune` (chunk engine, old agent): a NOT_READ row, with no value invented."""
    _report(conn, senses={"carrier": None})
    [row] = _rows(conn)
    assert row["verdict"] == "NOT_READ" and "tune=null" in row["evidence"] and "decode=shared" not in row["evidence"], row


@pytest.mark.parametrize("bad", ["shared", {**TUNE, "decode": "fast"}, {**TUNE, "drawers": None}])
def test_a_corrupt_tune_is_not_read_and_does_not_raise(conn, bad):
    """(neg) a string where the object belongs, an unknown decode: no value survives, the report is ingested."""
    _report(conn, senses={"tune": bad})
    assert [r["verdict"] for r in _rows(conn)] == ["NOT_READ"]


def test_a_progress_report_and_the_switch_off_record_nothing(conn):
    _report(conn, state="writing", senses={"tune": TUNE})
    db.set_setting(conn, veto.SETTING, "off")
    _report(conn, senses={"tune": TUNE})
    assert _rows(conn) == []


def test_one_bad_field_is_null_alone_and_never_a_number(conn):
    """(neg) nice 99 is out of 0-19: that field is `null` in the evidence, the rest of the choice is still recorded."""
    _report(conn, senses={"tune": {**TUNE, "nice": 99}})
    [row] = _rows(conn)
    assert row["verdict"] == "OBSERVED" and "nice=null" in row["evidence"] and "nice=99" not in row["evidence"], row
