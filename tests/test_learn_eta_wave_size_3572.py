"""#3572 (K16): the history of a wave counts only when it had the same number of cloners as the wave forecast."""

from __future__ import annotations

import pytest

from server import db
from server.learn import eta as learn_eta

from test_learn_eta_1843 import GB, MAC, _drives, _wave

OTHERS = [f"aa:bb:cc:00:00:{k:02d}" for k in range(1, 6)]


def _delivered(rate_mbs: int, macs: list[str]) -> dict:
    return {m: {"drawers": 1, "done": 1, "written_bytes": 20 * GB} for m in macs}


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def test_a_one_cloner_forecast_ignores_six_cloner_waves(conn):
    # two 6-cloner waves, slow (20GB / 1000s = 20MB/s), then two 1-cloner waves, fast (20GB / 400s = 50MB/s)
    for _ in range(2):
        _wave(conn, _delivered(20, [MAC, *OTHERS]), wall=1000, total=1100)
    for _ in range(2):
        _wave(conn, _delivered(50, [MAC]), wall=400, total=500)
    got = learn_eta.wave(conn, _drives(MAC, 1), 18 * GB)
    assert got["why"] is None
    (machine,) = got["machines"]
    assert machine["waves"] == 2 and machine["goodput_bps"] == 50_000_000, got
    assert got["seconds"] == 100 + 360


def test_a_six_cloner_forecast_rests_on_the_six_cloner_waves(conn):
    _wave(conn, _delivered(20, [MAC, *OTHERS]), wall=1000, total=1100)
    _wave(conn, _delivered(50, [MAC]), wall=400, total=500)
    drives = [d for m in [MAC, *OTHERS] for d in _drives(m, 1)]
    got = learn_eta.wave(conn, drives, 18 * GB)
    assert got["why"] is None
    assert {m["goodput_bps"] for m in got["machines"]} == {20_000_000}, got


def test_only_other_sized_waves_is_no_history_not_a_number(conn):
    _wave(conn, _delivered(20, [MAC, *OTHERS]), wall=1000, total=1100)
    got = learn_eta.wave(conn, _drives(MAC, 1), 18 * GB)
    assert got["seconds"] is None and got["why"] == "no_history" and got["missing"] == [MAC]
