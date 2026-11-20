"""#3163, #3269 -- Nadav 14/11: the thresholds of the chunk wave's network rate are NOT settings. Technical thresholds
are the brain's: they are opening constants of `chunk_rate.py` (`LOSS_PERMILLE`, `STEP_MBPS`, `CUT_PERCENT`,
`STALE_HOURS`, `PROBE_DIFF_PERCENT`; `SERVER_PSI_PERCENT` went in #3367), which the brain may calibrate from its memory later. The
console neither lists nor accepts them, and a value an older server stored is not read.

**Negative control:** the base (`lab/vm-1114d`, where the first three were settings) fails these tests: the GET lists
them and the POST accepts them.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.db import set_setting
from server.rounds import chunk_rate

KEYS = ("chunk_rate_loss_permille", "chunk_rate_step_mbps", "chunk_rate_cut_percent",
        "chunk_rate_stale_hours", "chunk_rate_probe_diff_percent", "chunk_rate_server_psi_percent")


def test_the_console_does_not_list_them(server):
    got = server["admin"].get("/api/console/settings").json()
    assert not [k for k in KEYS if k in got]


@pytest.mark.parametrize("key", KEYS)
def test_the_console_does_not_accept_them(server, key):
    r = server["admin"].post("/api/console/settings", json={key: "40"})
    assert r.status_code == 400, r.status_code
    assert key not in server["admin"].get("/api/console/settings").json()


def test_a_stored_value_is_not_read(server):
    conn = server["ctx"].conn
    for key in KEYS:
        set_setting(conn, key, "40")
    assert chunk_rate.config(conn) == chunk_rate.Config()
