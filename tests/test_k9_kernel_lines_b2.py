"""#3064 (K9): the kernel's lines reach the cause of a disk the engine fails mid-wave -- server side.

Hardware, 15/11 (LabRoot, three rounds, one APS): K9 said ``UNCLASSIFIED`` on every failure -- "no evidence from the kernel
(not read: the kernel lines)". `chunksend._tell_cause` never passed any, `disk_failures` carries ``ata_log`` only when the
agent fails a written target, and the agent's own capture ("sda: 0 kernel lines kept", agentlog-b1, 10:26:25) ran after
the server had closed the round. The lines now come in the wave's report (``wave_senses.kernel``,
`agent/lib/restore/wavesense.sh`) and `wavesense.kernel_lines` hands them to `cause.judge` at D11, D12, D14 and D15.

**Principle 5 is what most of this is about:** a list (even an empty one) is "dmesg was read"; ``null`` is "not read", and
stays not read -- it is never a clean kernel.

**Negative control:** with `server/brain/wavesense.py`, `server/brain/cause.py`, `server/rounds/chunksend.py` and
`server/app.py` back at the base, ``kernel_lines``/``kernel_of`` do not exist, and the tests marked (neg) fail on behaviour.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import cause as K, memory, veto, wavesense, repairrule
from server.rounds import repair

from test_chunksend_2533 import ALL, rig, wait_for   # noqa: F401 — rig is a fixture
from test_disk_failures import CABLE_LINES, DISK_LINES

MAC = "78:ac:c0:9b:11:c2"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "k9k.db")
    yield c
    veto.forget()


def k9(conn) -> list[dict]:
    return memory.decisions(conn, rule="K9", limit=100)


def _wave(conn, kernel, *, session="ses_w", serials=None):
    """A wave the machine reported senses for, with a hello that names the serial of each device."""
    serials = serials or {"sda": "SER-A", "sdb": "SER-B"}
    conn.execute("INSERT OR REPLACE INTO net_devices (mac, first_seen, last_seen, disks_json) VALUES (?,?,?,?)",
                 (MAC, db.now_iso(), db.now_iso(), json.dumps([{"dev": d, "serial": s} for d, s in serials.items()])))
    senses = {"window_s": 5, "windows": 3, "cpu": None, "nic": None, "disks": {}, "kernel": kernel}
    conn.execute("INSERT OR REPLACE INTO wave_senses (session_id, mac, at, senses_json) VALUES (?,?,?,?)",
                 (session, MAC, db.now_iso(), json.dumps(senses)))
    conn.commit()


# --- the server keeps what the agent sent ---------------------------------------------------------------------------


def test_the_kernel_lines_are_kept_per_drawer_and_not_read_stays_not_read():
    """(neg) a list is kept, an empty list is "read and clean", and anything else is None -- never an empty list."""
    got = wavesense.well_formed({"kernel": {"sda": list(CABLE_LINES), "sdb": [], "sdc": None, "sdd": "x",
                                            "sde": ["ok", 5]}})
    assert got["kernel"]["sda"] == CABLE_LINES
    assert got["kernel"]["sdb"] == [], "a clean kernel was lost"
    assert got["kernel"]["sdc"] is None and got["kernel"]["sdd"] is None and got["kernel"]["sde"] is None
    assert "kernel" not in wavesense.well_formed({"windows": 1}), "an agent that sends none was given a kernel"
    assert wavesense.well_formed({"kernel": ["sda"]})["kernel"] is None


def test_the_lines_are_capped_so_a_report_cannot_grow_the_row():
    got = wavesense.well_formed({"kernel": {"sda": ["x" * 1000] * 50}})["kernel"]["sda"]
    assert len(got) == wavesense.MAX_KERNEL_LINES and all(len(line) == wavesense.MAX_KERNEL_CHARS for line in got)


# --- finding the drawer by its serial -------------------------------------------------------------------------------


def test_the_lines_of_a_drawer_are_found_by_its_serial_not_by_its_name(conn):
    """(neg) sdb is SER-B on this machine; the lines of sda are not its lines."""
    _wave(conn, {"sda": ["a-line"], "sdb": list(DISK_LINES)})
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "SER-B") == tuple(DISK_LINES)
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "SER-A") == ("a-line",)


def test_a_clean_kernel_is_an_empty_tuple_and_not_known_is_none(conn):
    _wave(conn, {"sda": [], "sdb": None})
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "SER-A") == ()
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "SER-B") is None, "a drawer dmesg did not cover was 'clean'"
    assert wavesense.kernel_lines(conn, "ses_other", MAC, "SER-A") is None, "no row for the wave"
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "NOT-HERE") is None, "a serial that no drawer reported"
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "") is None


def test_an_agent_that_does_not_send_kernel_and_a_serial_on_two_drawers_are_not_known(conn):
    _wave(conn, None)
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "SER-A") is None
    _wave(conn, {"sda": ["x"], "sdb": ["y"]}, serials={"sda": "SAME", "sdb": "SAME"})
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "SAME") is None, "two drawers with one serial: not an identity"


def test_a_drawer_that_left_the_hello_is_bound_by_the_disk_event_of_the_wave(conn):
    """The hello after the failure no longer lists a dead disk (`reports._bound_serial`): the wave's own event does."""
    _wave(conn, {"sdc": list(CABLE_LINES)}, serials={"sda": "SER-A"})
    conn.execute("INSERT INTO disk_events (session_id, mac, disk, serial, verdict, updated_at) VALUES (?,?,?,?,?,?)",
                 ("ses_w", MAC, "sdc", "SER-C", "ok", db.now_iso()))
    conn.commit()
    assert wavesense.kernel_lines(conn, "ses_w", MAC, "SER-C") == tuple(CABLE_LINES)


def test_a_database_that_fails_is_not_known_and_never_raises():
    assert wavesense.kernel_lines(object(), "ses_w", MAC, "SER-A") is None


# --- the cause -------------------------------------------------------------------------------------------------------


def test_a_slow_drive_with_a_cable_in_the_kernel_is_the_link_and_not_unclassified(conn):
    """(neg) D11 sees skipped; the kernel lines now say the cable. Before: UNCLASSIFIED, 'kernel not read'."""
    v = K.judge(conn, point="D11", round_id="ses_w", mac=MAC, serial="SER-A", existing="נכשל", skipped=11390,
                kernel=tuple(CABLE_LINES))
    assert v.cause == K.LINK and not v.against_disk, v
    (d,) = k9(conn)
    assert d["verdict"] == "LINK" and any(e.startswith("kernel=cable") for e in d["evidence"]), d


def test_a_slow_drive_with_a_clean_kernel_stays_unclassified_and_says_the_kernel_was_read(conn):
    """The design stands (cause.py head): a slow drive with a clean kernel is not DRIVE. What changes is what the row says:
    the kernel WAS read and has nothing -- not 'not read'."""
    v = K.judge(conn, point="D11", round_id="ses_w", mac=MAC, serial="SER-A", existing="נכשל", skipped=11390,
                kernel=())
    assert v.cause == K.UNCLASSIFIED and not v.against_disk
    (d,) = k9(conn)
    assert "נקרא" in d["reason"] and "אין ראיה לכבל" in d["reason"] and "לא נקראו: שורות הקרנל" not in d["reason"], d
    assert any(e.endswith("(0 lines)") and e.startswith("kernel=") and e != "kernel=None (0 lines)"
               for e in d["evidence"]), d["evidence"]


def test_a_slow_drive_whose_kernel_was_not_read_is_still_said_not_read(conn):
    v = K.judge(conn, point="D11", round_id="ses_w", mac=MAC, serial="SER-A", existing="נכשל", skipped=11390)
    assert v.cause == K.UNCLASSIFIED
    (d,) = k9(conn)
    assert "אי אפשר לשלול כבל" in d["reason"] and "לא נקראו: שורות הקרנל" in d["reason"], d


# --- the engine: the lines reach K9 at the point that fails the disk --------------------------------------------------


def _laggard_that_never_completes(rig) -> dict:
    """#3170/#3373: a disk that lags in the first pass is not failed -- the first answer is `fetch`, with no `fail`. It is
    failed only when it still lacks the same piece after `MAX_REPAIRS` repair rounds (D12): B1 reports it every time, and
    the answer that comes back is the last one (`done`, with `fail`)."""
    first = rig.ask("b", 1, {"B1": [[0, 0]], "B2": ALL}, skipped={"B1": 9, "B2": 0})
    assert first["action"] == "fetch" and "fail" not in first, first
    for n in range(2, repairrule.MAX_ROUNDS + 3):
        last = rig.ask("b", n, {"B1": [[0, 0]], "B2": ALL})
        if last["action"] != "fetch":
            return last
    raise AssertionError("B1 was never failed, however many repair rounds went by")


def _slow_wave(rig, conn, lines):
    """A drive that lagged and never completes: the engine fails it by name after the repairs (D12), and K9 is asked."""
    engine = rig.make()
    engine.cause = lambda **kw: K.judge(conn, **kw)
    if lines != "unwired":
        engine.kernel_of = lines
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("c", 1, "ended", {"C1": ALL})
    b = _laggard_that_never_completes(rig)
    assert b["action"] == "done" and "לא הצליח לקבל את כל הנתונים" in b["fail"]["B1"], b   # what the engine does did not change
    assert wait_for(lambda: k9(conn))
    return engine, k9(conn)


def test_the_engine_passes_the_kernel_lines_of_the_drawer_it_fails_to_k9(rig, conn):
    """(neg) D12: the hook is asked for (wave, mac, serial) and its lines decide the cause."""
    asked = []

    def kernel_of(session_id, mac, serial):
        asked.append((mac, serial))
        return tuple(CABLE_LINES)

    engine, rows = _slow_wave(rig, conn, kernel_of)
    (d,) = rows
    assert (d["point"], d["entity_key"], d["verdict"]) == ("D12", "B1", K.LINK), d
    assert asked == [("b", "B1")], asked
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_without_the_hook_the_engine_asks_nothing_and_k9_is_as_before(rig, conn):
    engine, rows = _slow_wave(rig, conn, "unwired")
    assert rows[0]["verdict"] == K.UNCLASSIFIED and "אי אפשר לשלול כבל" in rows[0]["reason"]
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_a_hook_that_throws_costs_the_lines_and_not_the_cause_or_the_wave(rig, conn):
    def boom(*_a):
        raise RuntimeError("no row")

    engine, rows = _slow_wave(rig, conn, boom)
    assert rows[0]["verdict"] == K.UNCLASSIFIED, rows
    assert wait_for(lambda: engine.status()["state"] == "done")
