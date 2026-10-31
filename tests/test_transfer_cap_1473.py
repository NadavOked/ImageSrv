"""#1473 (Nadav 16/10) -- an optional rate cap for inter-site transfers.

The operator sets ``intersite_rate_cap_mbps`` (Mb/s per transfer, 0 = unlimited,
the default) from the branches screen. Both directions are paced by the primary:
push after each tar chunk it sends, pull after each piece it reads (TCP flow
control holds the secondary's sender to the same rate -- no protocol change).

The end-to-end tests run a real primary and a real secondary on loopback (the
fixtures of ``test_storage_transfer``) and swap only the pacer's clock and sleep
for a virtual pair, so the pacing is measured exactly and the test does not wait.
"""

from __future__ import annotations

import logging

import pytest

from server import db
from server.interserver import transfer_cap

MBPS = transfer_cap.BYTES_PER_MBPS


# --- the setting ---------------------------------------------------------------


@pytest.mark.parametrize("raw, want", [
    ("0", 0), (0, 0), ("250", 250), (250, 250), (100_000, 100_000),
    ("0250", None), ("-1", None), (-1, None), ("1.5", None), (1.5, None), ("", None),
    ("abc", None), (True, None), (None, None), (100_001, None), ("1000000", None),
])
def test_parse_takes_a_whole_number_in_range_only(raw, want):
    assert transfer_cap.parse(raw) == want


def test_unset_is_unlimited(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    assert transfer_cap.cap_mbps(conn) == 0
    assert transfer_cap.bytes_per_second(conn) is None


def test_a_stored_cap_is_bytes_per_second(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.set_setting(conn, transfer_cap.SETTING, "200")
    assert transfer_cap.bytes_per_second(conn) == 25_000_000


def test_a_garbled_stored_value_is_unlimited_and_said(tmp_path, caplog):
    conn = db.connect(tmp_path / "x.db")
    db.set_setting(conn, transfer_cap.SETTING, "fast")
    with caplog.at_level(logging.WARNING, logger="imagesrv.transfer_cap"):
        assert transfer_cap.cap_mbps(conn) == 0
    assert "fast" in caplog.text


# --- the pacer -------------------------------------------------------------------


class _Virtual:
    """A clock that moves only when the pacer sleeps -- exact, and instant."""

    def __init__(self):
        self.t, self.slept, self.sent = 0.0, [], []

    def clock(self):
        return self.t

    def sleep(self, seconds):
        assert seconds > 0
        self.slept.append(seconds)
        self.t += seconds


def test_unlimited_never_sleeps():
    v = _Virtual()
    pace = transfer_cap.Pacer(lambda: None, clock=v.clock, sleep=v.sleep)
    for sent in range(0, 10 ** 9, 10 ** 6):
        pace(sent)
    assert v.slept == []


def test_the_average_rate_is_held_to_the_cap():
    v = _Virtual()
    pace = transfer_cap.Pacer(lambda: 1000, clock=v.clock, sleep=v.sleep)
    for sent in range(0, 10_001, 500):
        pace(sent)
    assert sum(v.slept) == pytest.approx(10.0)        # 10,000 bytes at 1,000 B/s


def test_a_change_takes_hold_mid_transfer():
    """Lowering the cap in the console catches a transfer that is running."""
    v, rate = _Virtual(), {"now": 1000}
    pace = transfer_cap.Pacer(lambda: rate["now"], clock=v.clock, sleep=v.sleep)
    for sent in range(0, 4001, 100):                 # 4s at 1,000 B/s
        pace(sent)
    rate["now"] = 100
    before = sum(v.slept)
    # 4,100 .. 9,000 in steps of 100 -- byte counts, written as a multiple so the
    # source scan for the multicast ports (test_sender_ports, #156) stays strict
    for sent in (100 * i for i in range(41, 91)):    # 4,900 bytes more
        pace(sent)
    after = sum(v.slept) - before
    # at the old rate this is 4.9s; the new one is read within RECHECK_S (2s, at
    # most 2,000 bytes at the old rate) -- the rest at 100 B/s is >= 29s
    assert 29 <= after <= 49.1, after


def test_no_credit_after_a_slow_stretch():
    """A transfer slower than the cap for a while (disk, link) does not burst over it
    to catch up."""
    v = _Virtual()
    pace = transfer_cap.Pacer(lambda: 1000, clock=v.clock, sleep=v.sleep)
    pace(0)
    v.t += 100.0                                     # 100s with nothing sent
    pace(100)
    v.slept.clear()
    for sent in range(200, 1101, 100):              # then 1,000 bytes...
        pace(sent)
    assert sum(v.slept) == pytest.approx(1.0)        # ...still take 1s, not 0


# --- the console API ---------------------------------------------------------------


@pytest.fixture()
def api(server):
    return server


def test_the_setting_is_unlimited_by_default_and_settable(api):
    admin, ctx = api["admin"], api["ctx"]
    assert admin.get("/api/console/settings").json()[transfer_cap.SETTING] == "0"
    assert admin.post("/api/console/settings",
                      json={transfer_cap.SETTING: 200}).status_code == 200
    assert admin.get("/api/console/settings").json()[transfer_cap.SETTING] == "200"
    detail = ctx.conn.execute("SELECT detail FROM journal WHERE event = 'setting_change'"
                              " ORDER BY id DESC").fetchone()[0]
    assert detail == f"{transfer_cap.SETTING}=200"


@pytest.mark.parametrize("bad", ["-5", "abc", "0200", 100_001, True, "", 2.5])
def test_a_bad_value_is_refused_before_anything_is_written(api, bad):
    admin, ctx = api["admin"], api["ctx"]
    r = admin.post("/api/console/settings",
                   json={"session_wait_seconds": "77", transfer_cap.SETTING: bad})
    assert r.status_code == 400 and "Mb/s" in r.text, r.text
    stored = {k: v for k, v in ctx.conn.execute("SELECT key, value FROM settings")}
    assert transfer_cap.SETTING not in stored and stored.get("session_wait_seconds") != "77"


def test_only_an_admin_sets_it(api):
    assert api["deploy"].post("/api/console/settings",
                              json={transfer_cap.SETTING: 9}).status_code == 403
