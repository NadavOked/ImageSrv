"""#3036: "automatic wave rate" switched off is the fixed ceiling, as the console says -- not the start rate.

The setting `learned_rate_enabled` promises in the console (`server/static/console.js`, its `more`): "כבוי = תמיד
התקרה הקבועה (IMAGESRV_MAX_BITRATE)". `drive_rates.choose` gave the START rate instead: 600m with no
`IMAGESRV_MAX_BITRATE` (not 900m), and a learned start (`learned_start_mbps`, #2028) when one was kept. Nadav, 12/11
on the Issue: the code does what is written -- off is a fixed rate at `IMAGESRV_MAX_BITRATE` or the ceiling, and the
learned rate has no effect.

**Negative control:** with `server/rounds/drive_rates.py` (and the comment in `server/db.py`) back at the base, the
tests marked (neg) fail on behaviour: 600m / 650m where the ceiling was promised. The guards (switch on, a configured
ceiling under 600m) pass in both states.
"""

from __future__ import annotations

import pytest

from console_src import console_js
from server import db
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    db.set_setting(c, drive_rates.SETTING, "false")
    return c


def _drive(serial="S1", mac="aa:bb:cc:00:30:36", port=1):
    return {"serial": serial, "model": "EVO 870", "mac": mac, "port": port}


def _slow(conn, serial, rate_bps):
    """A drive that held two waves back -- what would lower a wave with the switch on (`MIN_HELD_SAMPLES`)."""
    for n in range(drive_rates.MIN_HELD_SAMPLES):
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?,?,?,?,?,?,?,?)", (serial, "EVO 870", f"ses_slow{n}", rate_bps, 25 * MB,
                                                   rate_bps * 1000, 1000, "2026-11-12T10:00:00+00:00"))
    conn.commit()


def test_off_with_no_configured_ceiling_is_900m_not_the_start_rate(conn):
    """(neg) No `IMAGESRV_MAX_BITRATE`: the ceiling is `TOP_BITRATE` (900m), and that is the rate."""
    got = drive_rates.choose(conn, [_drive()], None)
    assert got["basis"] == "disabled"
    assert got["max_bitrate"] == got["ceiling"] == "900m", got


def test_off_is_the_configured_ceiling(conn):
    """(neg) `IMAGESRV_MAX_BITRATE=800m`: 800m, not min(600m, 800m)."""
    got = drive_rates.choose(conn, [_drive()], "800m")
    assert got["basis"] == "disabled" and got["max_bitrate"] == "800m", got


def test_off_a_learned_start_has_no_effect(conn):
    """(neg) A start proved by a learning round (#2028, 650m) under a 700m ceiling: still the ceiling."""
    db.set_setting(conn, drive_rates.START_SETTING, "650")
    got = drive_rates.choose(conn, [_drive()], "700m")
    assert got["basis"] == "disabled" and got["max_bitrate"] == "700m", got


def test_off_a_slow_drive_memory_does_not_lower_it(conn):
    """(neg) Off is a FIXED rate: a drive known to be slow does not lower it (the memory is still written)."""
    _slow(conn, "S1", 10 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], None)
    assert got["basis"] == "disabled" and got["max_bitrate"] == "900m" and got["by"] is None, got


def test_off_a_chunk_wave_goes_out_at_the_ceiling_and_the_learning_leaves_it(conn):
    """(neg) The chunk engine takes its rate from the same `decide` (`chunk_rate.adjust` keeps a `disabled` answer)."""
    rate = drive_rates.decide(conn, "ses_c1", lambda: [_drive()], None,
                              adjust=lambda chosen, wave: chunk_rate.adjust(conn, chosen, wave))
    assert rate == "900m"
    assert drive_rates.decision(conn, "ses_c1")["basis"] == "disabled"
    (line,) = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'")]
    assert line.startswith("ses_c1 900m basis=disabled"), line


def test_a_configured_ceiling_under_the_start_rate_is_still_that_ceiling(conn):
    """Guard, both states: 200m (the lab's `IMAGESRV_MAX_BITRATE`) was 200m before and is 200m now."""
    got = drive_rates.choose(conn, [_drive()], "200m")
    assert got["basis"] == "disabled" and got["max_bitrate"] == "200m", got


def test_switched_on_the_first_wave_still_opens_at_the_start_rate(conn):
    """Guard, both states: the fix is the switched-off branch only -- on, a room with no history opens at 600m."""
    db.set_setting(conn, drive_rates.SETTING, "true")
    got = drive_rates.choose(conn, [_drive()], None)
    assert got["basis"] == "start" and got["max_bitrate"] == "600m" and got["ceiling"] == "900m", got


def test_the_console_still_promises_the_fixed_ceiling():
    """The promise the code now keeps: if the wording changes, the code here must change with it (#3036)."""
    assert "כבוי = תמיד התקרה הקבועה (IMAGESRV_MAX_BITRATE)" in console_js()
