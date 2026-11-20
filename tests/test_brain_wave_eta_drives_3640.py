"""#3640 (K3, `server/brain/waveeta.py`): the rate an earlier wave achieved is the rate of ITS drives, so K3 takes it only from a
chunk wave that ran on the same drives as the wave it predicts.

J3b (LabRoot 18/11, `ses_9b54c930`, one cloner, two Samsungs `naa.50025388a07a5842` + `naa.5002538d00a68927`): the wave before it
(`ses_266728f8`, 17/11) ran on `naa.5002538d42a58a2c` + an APS (`t10.ATA ... SICSD40302WL`) and the APS held it to 165Mb/s. K3 took
`min(asked, 165)` and predicted 897s; J3b took 346s (ratio 0.39). The next wave, on the same two Samsungs as J3b, predicted 314 and took
345 (1.10). The image is the J3b one: 18,545,476,822 compressed bytes.

**Negative control (bakara-shlilit):** with `server/brain/waveeta.py` back at the base (`bfd628f02`), every test marked (neg) fails on
behaviour -- the prediction is the slow wave's rate (897s), or the evidence has no `achieved_before_match`. The tests marked (guard) assert
only what the base also says (the forecast, `achieved_before_wave`) and pass in both states: they are what keeps the fix from rejecting
every earlier wave.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import memory
from server.brain import waveeta as W

IMAGE = 18_545_476_822                 # J3b `total_compressed_bytes`
CHUNKS = 18_441
ASKED = 500_000_000                    # the wave asks more than the APS wave managed; J3b's 314s t1 means >= 472Mb/s
MAC = "78:ac:c0:9b:11:c2"
FAST_A, FAST_B = "naa.50025388a07a5842", "naa.5002538d00a68927"            # J3b: the wave and the one after it
FAST_C = "naa.5002538d42a58a2c"                                             # `ses_266728f8`: a Samsung ...
APS = "t10.ATA     APS-SL3N-512                            SICSD40302WL"    # ... and the slow APS (spaces, as in the engine's line)
APS_BPS = 165_000_000                  # `achieved_before_mbps=165` of decision 6387
FAST_BPS = 472_000_000                 # `achieved_before_mbps=472` of decision 6436


class Lib:
    def get(self, image_id):
        return {"total_compressed_bytes": IMAGE, "partitions": [{"index": 3, "file": "p3.pcl.zst", "readback_chunks": CHUNKS}]}


class Chunk:
    no_file = None

    def rate_outcome(self, session_id):
        return {}


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def _sample(achieved: int | None, serials, *, ended: str = "send_done", key: str = "drives") -> dict:
    """`wave_rates.stream` of a chunk wave (`chunk_rate.sample_of`): the drives as `{mac: {serial: bytes}}`, or only `write_serials`
    (`key="write_serials"`), or neither (`key=None`)."""
    chunk = {"asked_bps": ASKED, "achieved_bps": achieved, "first_pass_s": 300.0, "ended": ended, "stream_zbytes": IMAGE}
    if key == "drives":
        chunk["drives"] = {MAC: {s: IMAGE // 2 for s in serials}}
    elif key == "write_serials":
        chunk["write_serials"] = sorted(serials)
    return {"chunk": chunk}


def _wave(conn, sid: str, stream: dict) -> None:
    conn.execute("INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
                 " created_at, last_join_at) VALUES (?, 'grp_CLONERS', 'img_x', 'ROOM', 1, 1, 'closed', 'x', 'now', 0)", (sid,))
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?, '{}', ?, ?)",
                 (sid, db.now_iso(), json.dumps(stream)))
    conn.commit()


def _predict(conn, serials=(FAST_A, FAST_B), *, behind=()):
    """K3's `WAVE_ETA` of the wave `ses_now` on `serials` (the write-checked drives) -- plus the ones the engine journaled as behind.
    Returns (`eta_s`, the evidence as a dict, the reason)."""
    conn.execute("INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
                 " created_at, last_join_at) VALUES ('ses_now', 'grp_CLONERS', 'img_x', 'ROOM', 1, 1, 'running', 'x', 'now', 0)")
    conn.commit()
    eta = W.WaveEta(conn, library=Lib(), chunk=Chunk(), mode=lambda: "shadow", clock=lambda: 0.0)
    for serial in behind:
        eta.on_event("chunk_write_behind", f'ses_now {json.dumps(serial)} check=60MB/s wrote=40MB/s in=http left_out', "ses_now")
    assert eta.started("ses_now", ASKED, {(MAC, s): 300 for s in serials}) == ASKED          # the rate itself is never changed
    rows = [d for d in memory.decisions(conn, rule="K3", limit=20) if d["action"] == "WAVE_ETA" and d["round_id"] == "ses_now"]
    assert len(rows) == 1 and rows[0]["verdict"] == "PREDICT", rows
    ev = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in rows[0]["evidence"] if "=" in e}
    return int(ev["eta_s"]), ev, rows[0]["reason"]


ASKED_ETA = round(IMAGE * 8 / ASKED)            # 297s: the forecast at the rate the wave asks
SLOW_ETA = round(IMAGE * 8 / APS_BPS)            # 899s: the forecast at the APS wave's rate (J3b: 897)


# --- J3b ------------------------------------------------------------------------------------------------------------------


def test_j3b_a_slow_wave_on_other_drives_is_not_the_base(conn):
    """(neg) J3b: the wave before ran on a Samsung and an APS (165Mb/s); this one runs on two other Samsungs. Before: 897-899s against
    346s. After: the asked rate, and the evidence says no wave on the same drives was found."""
    _wave(conn, "ses_266728f8", _sample(APS_BPS, [FAST_C, APS]))
    eta_s, ev, reason = _predict(conn)
    assert SLOW_ETA in range(895, 905)
    assert eta_s == ASKED_ETA, (eta_s, ev)
    assert ev["achieved_before_mbps"] == "unmeasured" and ev["achieved_before_match"] == "none", ev
    assert "achieved_before_wave" not in ev and ev["stream_mbps"] == "500", ev
    assert "ביטחון נמוך" in reason, reason                  # not a forecast like any other: it says it has no wave to stand on


def test_j3b_the_next_wave_takes_the_rate_of_the_wave_on_its_own_drives(conn):
    """(guard) The wave after J3b ran on the same two Samsungs as J3b: its rate (472Mb/s) is the base, as it was (314 predicted,
    345 actual)."""
    _wave(conn, "ses_266728f8", _sample(APS_BPS, [FAST_C, APS]))
    _wave(conn, "ses_9b54c930", _sample(FAST_BPS, [FAST_A, FAST_B]))
    eta_s, ev, reason = _predict(conn)
    assert eta_s == round(IMAGE * 8 / FAST_BPS), ev
    assert ev["achieved_before_wave"] == "ses_9b54c930" and ev["achieved_before_mbps"] == "472", ev
    assert "ביטחון נמוך" not in reason and "472Mb/s" in reason, reason


def test_the_rate_of_an_older_wave_on_the_same_drives_beats_a_newer_slow_wave_on_others(conn):
    """(neg) The newest chunk wave is the APS one; the wave before that ran on these Samsungs. The newest is skipped, not a blocker."""
    _wave(conn, "ses_same", _sample(FAST_BPS, [FAST_A, FAST_B]))
    _wave(conn, "ses_aps", _sample(APS_BPS, [FAST_C, APS]))
    eta_s, ev, _reason = _predict(conn)
    assert eta_s == round(IMAGE * 8 / FAST_BPS), (eta_s, ev)
    assert ev["achieved_before_wave"] == "ses_same" and ev["achieved_before_match"] == "drives", ev


# --- what "the same drives" means -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("earlier, why", [
    ([FAST_A], "one of this wave's two drives only"),
    ([FAST_A, FAST_B, APS], "these two plus a slow one: the slow one held that wave"),
    ([FAST_A, FAST_C], "one drive in common"),
])
def test_only_the_same_set_of_drives_counts(conn, earlier, why):
    """(neg) Equal sets, not overlap: a wave with an extra drive was held by it, a wave with fewer drives says nothing of the one that
    is new here."""
    _wave(conn, "ses_prev", _sample(APS_BPS, earlier))
    eta_s, ev, _reason = _predict(conn)
    assert eta_s == ASKED_ETA and ev["achieved_before_match"] == "none", (why, eta_s, ev)


def test_the_same_drives_in_another_order_or_machine_are_the_same_drives(conn):
    """(guard) The serial is the drive; the order of the set and which cloner it sat in do not matter."""
    _wave(conn, "ses_prev", {"chunk": {**_sample(FAST_BPS, [])["chunk"], "drives": {"aa:bb": {FAST_B: 1}, "cc:dd": {FAST_A: 1}}}})
    eta_s, ev, _reason = _predict(conn)
    assert eta_s == round(IMAGE * 8 / FAST_BPS) and ev["achieved_before_wave"] == "ses_prev", ev


def test_a_wave_that_did_not_record_its_drives_is_not_the_base(conn):
    """(neg) "Could not check" is not "same drives" (principle 5): the rate of a wave whose drives are unknown is not used."""
    _wave(conn, "ses_prev", _sample(APS_BPS, [], key=None))
    eta_s, ev, _reason = _predict(conn)
    assert eta_s == ASKED_ETA and ev["achieved_before_match"] == "none", ev


def test_the_write_serials_name_the_drives_when_the_wave_has_no_drive_bytes(conn):
    """(guard) `chunk.drives` is None when the sizes were not known; `chunk.write_serials` names the drives then: the same set is the base."""
    _wave(conn, "ses_prev", _sample(FAST_BPS, [FAST_A, FAST_B], key="write_serials"))
    assert _predict(conn)[1]["achieved_before_wave"] == "ses_prev"


def test_write_serials_of_other_drives_are_not_the_base(conn):
    """(neg) The same fallback, another set: not the base."""
    _wave(conn, "ses_prev", _sample(APS_BPS, [FAST_C, APS], key="write_serials"))
    eta_s, ev, _reason = _predict(conn)
    assert eta_s == ASKED_ETA and ev["achieved_before_match"] == "none", ev


def test_a_drive_the_engine_left_out_as_behind_is_still_one_of_the_wave_s_drives(conn):
    """(guard) The write check leaves a drive that was behind in an earlier wave out of `probes` (`chunksend._start_rate`), and the engine
    journals it as `chunk_write_behind`: K3 counts it, so a wave on all three drives is a wave on these drives."""
    _wave(conn, "ses_prev", _sample(FAST_BPS, [FAST_A, FAST_B, APS]))
    _eta_s, ev, _reason = _predict(conn, [FAST_A, FAST_B], behind=[APS])
    assert ev["achieved_before_wave"] == "ses_prev" and ev["stream_mbps"] == "472", ev          # (the APS adds its own completion to the ETA)


# --- what #3550 had right stays right -------------------------------------------------------------------------------------


@pytest.mark.parametrize("ended", ["send_failed", "send_stopped"])
def test_a_failed_wave_on_these_drives_does_not_borrow_an_older_one(conn, ended):
    """(guard) #3550: the last wave on these drives decides; if it gave no rate the asked rate stays, not an older wave's."""
    _wave(conn, "ses_old", _sample(FAST_BPS, [FAST_A, FAST_B]))
    _wave(conn, "ses_last", _sample(100_000_000, [FAST_A, FAST_B], ended=ended))
    eta_s, ev, _reason = _predict(conn)
    assert eta_s == ASKED_ETA and "achieved_before_wave" not in ev, ev


def test_a_udpcast_wave_in_between_is_still_skipped(conn):
    """(guard) A udpcast wave has no `stream.chunk`."""
    _wave(conn, "ses_chunk", _sample(FAST_BPS, [FAST_A, FAST_B]))
    _wave(conn, "ses_udp", {"wall_s": 374.6, "ended": "send_done", "sent_bytes": IMAGE, "loss_blocks": 0})
    assert _predict(conn)[1]["achieved_before_wave"] == "ses_chunk"


def test_the_asked_rate_is_still_the_ceiling_of_the_rate_taken(conn):
    """(guard) #3550: a wave on these drives that managed more than this wave asks does not speed the forecast past what is asked."""
    _wave(conn, "ses_prev", _sample(800_000_000, [FAST_A, FAST_B]))
    eta_s, ev, _reason = _predict(conn)
    assert eta_s == ASKED_ETA and ev["stream_mbps"] == "500" and ev["achieved_before_wave"] == "ses_prev", ev


def test_a_wave_with_no_drives_to_compare_has_no_base(conn):
    """(neg) No write-checked drives at all (a caller that gave none): nothing to compare with, so no base -- not "any wave"."""
    _wave(conn, "ses_prev", _sample(APS_BPS, [FAST_A, FAST_B]))
    eta_s, ev, _reason = _predict(conn, serials=())
    assert eta_s == ASKED_ETA and ev["achieved_before_match"] == "none", ev
