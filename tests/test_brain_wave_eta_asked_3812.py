"""#3812 (K3, `server/brain/waveeta.py`): `achieved_before` counts only a wave on the same drives that asked for at least as much as
the wave being predicted (Zeus 22/11: a lower wave proves nothing about a higher rate).

19/11 of Jev (`jev-brain-1119b`): ratio 0.31 -- the wave before asked 892Mb/s and got 90, the wave predicted asked 400 (predicted 1642s,
took 505s); ratio 1.96 -- the wave before asked 400 and got 303, the wave predicted asked 600 (predicted 489s, took 957s).

**Negative control (bakara-shlilit):** with `server/brain/waveeta.py` at the base, the (neg) tests fail on behaviour: the forecast is
taken from the other wave's rate. The (guard) tests pass in both states: they keep the fix from rejecting every earlier wave.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import memory
from server.brain import waveeta as W

IMAGE = 18_545_476_822
CHUNKS = 18_441
MAC = "78:ac:c0:9b:11:c2"
SERIALS = ("naa.50025388a07a5842", "naa.5002538d00a68927")


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


def _wave(conn, sid, *, asked, got, drop_asked=False):
    chunk = {"achieved_bps": got, "first_pass_s": 300.0, "ended": "send_done", "stream_zbytes": IMAGE,
             "drives": {MAC: {s: IMAGE // 2 for s in SERIALS}}}
    if not drop_asked:
        chunk["asked_bps"] = asked
    conn.execute("INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
                 " created_at, last_join_at) VALUES (?, 'grp_CLONERS', 'img_x', 'ROOM', 1, 1, 'closed', 'x', 'now', 0)", (sid,))
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?, '{}', ?, ?)",
                 (sid, db.now_iso(), json.dumps({"chunk": chunk})))
    conn.commit()


def _predict(conn, asked):
    conn.execute("INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
                 " created_at, last_join_at) VALUES ('ses_now', 'grp_CLONERS', 'img_x', 'ROOM', 1, 1, 'running', 'x', 'now', 0)")
    conn.commit()
    eta = W.WaveEta(conn, library=Lib(), chunk=Chunk(), mode=lambda: "shadow", clock=lambda: 0.0)
    eta.started("ses_now", asked, {(MAC, s): 300 for s in SERIALS})
    rows = [d for d in memory.decisions(conn, rule="K3", limit=20) if d["action"] == "WAVE_ETA" and d["round_id"] == "ses_now"]
    assert len(rows) == 1 and rows[0]["verdict"] == "PREDICT", rows
    ev = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in rows[0]["evidence"] if "=" in e}
    return int(ev["eta_s"]), ev


def _eta(bps):
    return round(IMAGE * 8 / bps)


def test_a_wave_that_asked_for_less_is_not_the_base(conn):
    """(neg) 1.96: the wave before asked 400 and got 303; this one asks 600. 303 says nothing of 600: the forecast is the asked rate."""
    _wave(conn, "ses_low", asked=400_000_000, got=303_000_000)
    eta_s, ev = _predict(conn, 600_000_000)
    assert eta_s == _eta(600_000_000), (eta_s, ev)
    assert ev["achieved_before_match"] == "none" and "achieved_before_wave" not in ev, ev


def test_a_wave_that_asked_for_more_and_got_little_is_the_base(conn):
    """(guard) 0.31: the wave before asked 892 and got 90; this one asks 400. The drives could not give 400: 90 is the base."""
    _wave(conn, "ses_high", asked=892_000_000, got=90_000_000)
    eta_s, ev = _predict(conn, 400_000_000)
    assert eta_s == _eta(90_000_000), (eta_s, ev)
    assert ev["achieved_before_wave"] == "ses_high", ev


def test_a_wave_that_asked_for_the_same_is_the_base(conn):
    """(guard) equal asks count (`at least as much`)."""
    _wave(conn, "ses_same", asked=400_000_000, got=303_000_000)
    eta_s, ev = _predict(conn, 400_000_000)
    assert eta_s == _eta(303_000_000) and ev["achieved_before_wave"] == "ses_same", (eta_s, ev)


def test_a_lower_newer_wave_is_skipped_for_an_older_higher_one(conn):
    """(neg) the newest wave on these drives asked less and is skipped, not a blocker: the older one that asked enough is the base."""
    _wave(conn, "ses_old", asked=700_000_000, got=250_000_000)
    _wave(conn, "ses_new", asked=300_000_000, got=290_000_000)
    eta_s, ev = _predict(conn, 600_000_000)
    assert eta_s == _eta(250_000_000) and ev["achieved_before_wave"] == "ses_old", (eta_s, ev)


def test_a_wave_whose_ask_was_not_recorded_cannot_be_compared(conn):
    """(neg) principle 5: no `asked_bps` is "could not check", not "asked enough"."""
    _wave(conn, "ses_blind", asked=0, got=90_000_000, drop_asked=True)
    eta_s, ev = _predict(conn, 400_000_000)
    assert eta_s == _eta(400_000_000) and ev["achieved_before_match"] == "none", (eta_s, ev)
