"""#3269, metal 21/11 (LabRoot `892e891fc`, WAVE_START 15554): the write check replaced a network rate the AIMD had learned.

`wave_rate ... 892m basis=probe was=350m(chunk_up) why=drawers,probe probe=191MB/s`. Cloner 1 (an e1000 on a PCI bus) had
taught a clean wave at 250m (R1-77, sent 240); the next wave climbed one step to 350m, its drawers had changed, and the write
check of the drives (191MB/s) took the wave to the ceiling, 892m. Cloner 1 lost 431 permille (7949 chunks over HTTP, four
repair rounds), and 591 permille in the wave after it (`lab/metal-1121c/truth.md`, "השלמה").

The write check measures the drives, not the network: it is a ceiling of the drives, and the wave goes out at the lower of
the two (`chunk_rate._raise_cap`). A start the AIMD learned from a wave whose loss was measured is not raised by it; any other
start is raised by at most `PROBE_RAISE_MBPS` (the AIMD's own step). The check still lowers a start.

Negative control: remove the final clamp to raise_cap in start() to reproduce the 350m -> 892m jump.
"""

from __future__ import annotations


import pytest

from server import db
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6
C1, C2 = "aa:bb:cc:00:21:01", "aa:bb:cc:00:21:02"     # C1: the cloner with the PCI card
MODEL = "SAMSUNG MZ7LN256"
CHUNK = 4 * 1024 * 1024
CHUNKS = 5000


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drive(serial: str, mac: str, port: int) -> dict:
    return {"serial": serial, "model": MODEL, "mac": mac, "port": port}


def _wave(conn, sid, drives, ceiling=None) -> int:
    return drive_rates.parse_bitrate(drive_rates.decide(
        conn, sid, lambda: drives, ceiling, adjust=lambda chosen, wave: chunk_rate.adjust(conn, chosen, wave)))


def _taught_at_250m(conn):
    """R1-77: a clean chunk wave of both cloners at 250m -- the network rate the brain learned and measured (cloner 1 sent 240)."""
    rate = _wave(conn, "w1", [_drive("D1", C1, 1), _drive("D2", C1, 2), _drive("D3", C2, 3)], "250m")
    assert rate == 250 * MB
    size = CHUNKS * CHUNK
    chunk_rate.record(conn, "w1", {"chunks": CHUNKS, "bytes": size, "first_pass_s": size * 8 / rate,
                                   "stream_zbytes": size * 240 // 250,       # sent 240 of the 250m asked
                                   "lost_chunks": 0, "away": 0, "cloners": 2, "repair_rounds": 0,
                                   "ended": "send_done"})
    # what the drives of that wave wrote (the reference of `_evidence` for a chunk basis): far from today's 191MB/s
    conn.executemany("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES (?,?,?,?,?,?)",
                     [("w1", s, MODEL, m, 60, "2026-11-21T14:00:00") for s, m in (("D1", C1), ("D2", C1), ("D3", C2))])
    conn.commit()


def _now(conn) -> int:
    """SA (room_a08a36d4): cloner 1 ports 1+2, cloner 2 ports 3+5 -- other drawers than the wave that taught."""
    return _wave(conn, "ses_now", [_drive("D1", C1, 1), _drive("D2", C1, 2), _drive("D3", C2, 3), _drive("D5", C2, 5)])


PROBES_191 = {(C1, "D1"): 191, (C1, "D2"): 191, (C2, "D3"): 191, (C2, "D5"): 191}


def _k8(conn):
    return conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K8' AND action = 'WAVE_START'").fetchall()


def test_the_metal_of_21_11_the_write_check_does_not_lift_a_learned_network_rate(conn):
    _taught_at_250m(conn)
    bits = _now(conn)
    decision = drive_rates.decision(conn, "ses_now")
    assert decision["basis"] == "chunk_up" and bits == 350 * MB               # one step up from the clean 250m
    cap = chunk_rate.cap_of(conn, decision)[0]
    assert cap == 892 * MB and 191 * 8 * MB > cap                             # before the fix: the wave went out at the cap
    got = chunk_rate.start(conn, "ses_now", bits, PROBES_191)
    assert got == 350 * MB
    after = drive_rates.decision(conn, "ses_now")
    assert after["basis"] == "chunk_up" and after["max_bitrate"] == "350m"
    check = after["start_check"]
    assert "net_learned" in check["why"] and check["raise_by"] == "net_learned" and check["raise_cap"] == "350m"
    assert check["probe_mbps"] == 191
    (row,) = _k8(conn)
    assert row["verdict"] == "KEEP" and "net_learned" in row["reason"]
    assert not conn.execute("SELECT 1 FROM journal WHERE event = 'wave_rate' AND detail LIKE '%basis=probe%'").fetchall()


def test_a_write_check_under_the_network_rate_still_lowers_it(conn):
    """The lower of the two: drives that write 30MB/s (240m) hold a 350m network rate down to what they write."""
    _taught_at_250m(conn)
    bits = _now(conn)
    got = chunk_rate.start(conn, "ses_now", bits, {k: 30 for k in PROBES_191})
    assert got == 240 * MB < bits
    after = drive_rates.decision(conn, "ses_now")
    assert after["basis"] == "probe" and "raise_by" not in after["start_check"]
    assert [r["verdict"] for r in _k8(conn)] == ["PROBE"]


def test_the_raise_cap_of_a_measured_network_rate_is_the_rate_itself():
    cfg = chunk_rate.Config()
    learned = {"basis": "chunk_up", "previous": {"session_id": "w1", "loss_permille": 0}}
    assert chunk_rate._raise_cap(learned, 350 * MB, cfg) == (350 * MB, "net_learned")


@pytest.mark.parametrize("decision", [
    {"basis": "learned", "previous": None},                                   # a drive's memory
    {"basis": "climb", "previous": {"session_id": "old"}},                    # the removed sender's base
    {"basis": "chunk_unsafe", "previous": {"session_id": None}},              # no wave taught: loss not measured
    {"basis": "chunk_up", "previous": {"session_id": "w1", "loss_permille": None}},
])
def test_a_start_not_measured_on_the_network_is_raised_one_step(decision):
    cfg = chunk_rate.Config()
    assert chunk_rate._raise_cap(decision, 350 * MB, cfg) == (450 * MB, "raise_step")


def test_the_step_is_the_aimds_own_step():
    """`PROBE_RAISE_MBPS` is not a new number: it is the AIMD's step (`STEP_MBPS`), and the brain's config carries it."""
    assert chunk_rate.PROBE_RAISE_MBPS == chunk_rate.STEP_MBPS
    assert chunk_rate.config(None).probe_raise_bps == chunk_rate.STEP_MBPS * MB


def test_the_catalog_has_the_threshold():
    import tomllib
    from pathlib import Path
    with (Path(__file__).resolve().parents[1] / "server" / "brain" / "catalog.toml").open("rb") as f:
        items = {i["id"]: i for i in tomllib.load(f)["item"]}
    item = items["T-PROBE-RAISE"]
    assert item["symbol"] == "PROBE_RAISE_MBPS" and item["source"] == "estimate" and item.get("recommendation")
    assert "T-PROBE-RAISE" in items["K8"]["uses"]


def test_reported_100m_receiver_caps_the_probe(conn):
    from server.agent import probe

    _taught_at_250m(conn)
    probe.record(conn, C1, probe.well_formed({"nic": [{"name": "eth0", "speed_mbps": 100}]}))
    bits = _now(conn)
    decision = drive_rates.decision(conn, "ses_now")
    assert decision["cloner"]["source"] == "reported"
    assert decision["cap_by"] == "cloner"
    assert chunk_rate.start(conn, "ses_now", bits, PROBES_191) == bits == 90 * MB
