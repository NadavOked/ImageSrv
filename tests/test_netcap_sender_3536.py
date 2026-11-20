"""#3536: `WAVE_NETCAP` לא לומד תקרת רשת מהקצב האיטי של השולח עצמו.

הבאג: `netcap._waves` קיבל כל גל נקי ש-`server_io.basis != server_disk` והוכיח ממנו `min(asked, sent)`. בברזל (16/11,
`lab/metal-1116-r12`) השולח ישן 63-66% מהמעבר הראשון ושלח 349-372Mb/s מתוך 669m, ו-`basis` ריק (לא נמדד), ולכן "התקרה"
(371m) הייתה הקצב האיטי של השולח. הרשת העבירה 669m באבדן 0.2-0.5 אלפית.

הכלל: גל מוכיח קצב רק אם יש ראיה חיובית שהשולח והדיסק לא היו הגבול -- `basis == ok`, השולח נמדד ולא ישן מעל `SENDER_ASLEEP_MAX`,
והוא שלח לפחות `KEEP_UP` ממה שהתבקש. בלי הוכחה: `None` ו-`UNPROVEN`, לא `WOULD_CAP`.

**הנתונים:** `REAL` הם שישה גלי החתיכות של r12 כפי שנרשמו (`runs/*-chunk/db-extra.json`, `wave_rates.stream.chunk`; הכרטיס `ens34`,
משכפל אחד). **לא היה בברזל גל חתיכות עם אבדן מעל הסף** ולא גל שהשולח לא ישן בו, ולכן הטסטים של "נלמד" ושל "אבדן ליד התקרה" בנויים מאותו
גל אמיתי עם שדות שהוחלפו -- וכתוב בכל אחד אילו.

**בקרה שלילית:** `server/brain/netcap.py` חוזר לבסיס (`_pushed` חסר) -- הטסטים שדורשים `None`/`UNPROVEN` נופלים.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from server import db
from server.brain import memory, netcap
from server.learn import server_io
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6
MAC = "78:ac:c0:9b:11:c2"
CARD = "ens34"
LOSS = chunk_rate.LOSS_PERMILLE
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

#: (session_id, sent_bps, loss_permille, first_pass_s, paced wire ms, paced disk ms); asked_bps = 669m בכולם
REAL = [
    ("ses_cbd80730", 371755171, 0.21690797679084647, 399.0894286609837, 107776, 142731),      # a1
    ("ses_1f59a6d9", 363033888, 0.5422699419771162, 408.67688546999125, 107753, 153441),      # a2
    ("ses_cc15f42c", 368796499, 0.43381595358169295, 402.29112571899896, 107739, 146408),     # a3
    ("ses_d8135989", 371358134, 0.37958895938398135, 399.51611464002053, 107352, 142827),     # b1
    ("ses_c54806ff", 349096035, 0.4880429477794046, 424.99353750701994, 107674, 172024),      # b2
    ("ses_9d75fc5c", 363462778, 0.4880429477794046, 408.1946427320072, 107683, 154131),       # b3
]
ASKED = 669 * MB


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _put(conn, sid, sample, basis="ok", interface=CARD, macs=(MAC,)) -> None:
    sample = {"teaches": True, "skip": None, "ended": "send_done", **sample}
    decision = {"max_bitrate": f"{sample['asked_bps'] // MB}m", "basis": "chunk_hold", "link": {"interface": interface},
                "roster": [{"mac": m, "serial": f"S{n}", "port": n} for n, m in enumerate(macs)]}
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?,?,?,?)",
                 (sid, json.dumps(decision), NOW.isoformat(timespec="seconds"), json.dumps({"chunk": sample})))
    if basis is not None:
        conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, basis) VALUES ('wave', ?, ?, '{}', ?)",
                     (sid, NOW.isoformat(), basis))
    conn.commit()


def _real(conn, row, basis=None) -> None:
    sid, sent, loss, seconds, wire, disk = row
    _put(conn, sid, {"asked_bps": ASKED, "sent_bps": sent, "loss_permille": loss, "first_pass_s": seconds,
                     "paced_ms": {"wire": wire, "disk": disk}}, basis=basis)


def _learn(conn):
    return netcap.learn(conn, CARD, (MAC,), LOSS)


def _bps(caps) -> dict:
    return {path: c["bps"] for path, c in caps.items()}


def _shadow_rows(conn):
    return [r for r in memory.decisions(conn, rule="K8", limit=50) if r["action"] == netcap.ACTION]


# --- the wave of r12: the sender's own slow rate is not a network ceiling -------------------------------------------


def test_the_real_data_is_what_the_issue_says():
    """The inputs of the tests below: every real wave had the sender asleep more than SENDER_ASLEEP_MAX, and put under KEEP_UP."""
    for _sid, sent, _loss, seconds, wire, disk in REAL:
        assert (wire + disk) / (seconds * 1000) > server_io.SENDER_ASLEEP_MAX
        assert sent < drive_rates.KEEP_UP * ASKED


def test_a2_wave_of_r12_proves_no_network_ceiling(conn):
    """The wave a2 as the brain read it: asked 669m, sent 363m, asleep 64%, `server_io` not measured, loss 0.54 per mille."""
    _real(conn, REAL[1])
    assert _bps(_learn(conn)) == {f"card:{CARD}": None, f"cloner:{MAC}": None}
    got = _learn(conn)[f"card:{CARD}"]
    assert got["waves"] == 0 and got["session_id"] is None, "not even counted as a proof"


@pytest.mark.parametrize("basis", [None, "ok"])
def test_all_six_waves_of_r12_prove_nothing_with_or_without_a_measured_disk(conn, basis):
    """`ok` is the best case for the wave (the disk was measured and was not the limit): still the sender was asleep AND slow."""
    for row in REAL:
        _real(conn, row, basis=basis)
    assert _bps(_learn(conn)) == {f"card:{CARD}": None, f"cloner:{MAC}": None}


def test_the_sleep_alone_is_not_a_proof_even_when_the_engine_kept_its_pace(conn):
    """a2 with `sent` raised to the ask: the sender still slept 64% of the first pass, so the time went on its pacing and not on
    the wire -- no evidence the network was what held it."""
    sid, _sent, loss, seconds, wire, disk = REAL[1]
    _put(conn, sid, {"asked_bps": ASKED, "sent_bps": ASKED, "loss_permille": loss, "first_pass_s": seconds,
                     "paced_ms": {"wire": wire, "disk": disk}}, basis="ok")
    assert _learn(conn)[f"card:{CARD}"]["bps"] is None


def test_a_slow_sender_that_did_not_sleep_is_not_a_proof_either(conn):
    """a2 with the sleep removed: awake, and still 54% of the ask -- the sender (disk read, CPU) was the limit, not the wire."""
    sid, sent, loss, seconds, _wire, _disk = REAL[1]
    _put(conn, sid, {"asked_bps": ASKED, "sent_bps": sent, "loss_permille": loss, "first_pass_s": seconds,
                     "paced_ms": {"wire": 0, "disk": 0}}, basis="ok")
    assert _learn(conn)[f"card:{CARD}"]["bps"] is None


def test_an_awake_sender_with_no_server_io_row_is_not_a_proof(conn):
    """a2 with the sender fixed and `server_io` missing (r12: `server_io=unmeasured`): "not measured" is not "not the disk"."""
    sid, _sent, loss, seconds, _wire, _disk = REAL[1]
    _put(conn, sid, {"asked_bps": ASKED, "sent_bps": int(ASKED * 0.97), "loss_permille": loss, "first_pass_s": seconds,
                     "paced_ms": {"wire": 0, "disk": 0}}, basis=None)
    assert _learn(conn)[f"card:{CARD}"]["bps"] is None


@pytest.mark.parametrize("what,sample", [
    ("no paced_ms", {"first_pass_s": 400.0}),
    ("no first_pass_s", {"paced_ms": {"wire": 0, "disk": 0}}),
    ("paced_ms without disk", {"first_pass_s": 400.0, "paced_ms": {"wire": 0}}),
    ("a first pass of zero seconds", {"first_pass_s": 0, "paced_ms": {"wire": 0, "disk": 0}}),
])
def test_a_sender_that_was_not_measured_is_not_an_awake_sender(conn, what, sample):
    _put(conn, "w1", {"asked_bps": ASKED, "sent_bps": ASKED, "loss_permille": 0.1, **sample}, basis="ok")
    assert _learn(conn)[f"card:{CARD}"]["bps"] is None, what


# --- the same wave, with the evidence there: it is learned ---------------------------------------------------------


def test_a_clean_wave_with_an_awake_sender_that_kept_its_pace_and_a_measured_disk_is_learned(conn):
    """a2 with the sender fixed (derived: paced 0, sent 97% of the ask, `basis = ok`): the rate it put is the ceiling."""
    sid, _sent, loss, seconds, _wire, _disk = REAL[1]
    sent = int(ASKED * 0.97)
    _put(conn, sid, {"asked_bps": ASKED, "sent_bps": sent, "loss_permille": loss, "first_pass_s": seconds,
                     "paced_ms": {"wire": 0, "disk": 0}}, basis="ok")
    caps = _learn(conn)
    assert _bps(caps) == {f"card:{CARD}": sent, f"cloner:{MAC}": sent}
    assert caps[f"card:{CARD}"]["waves"] == 1 and caps[f"card:{CARD}"]["session_id"] == sid
    assert caps[f"card:{CARD}"]["disk_unmeasured"] is False


def test_the_threshold_of_the_sleep_and_of_the_pace_are_inclusive_where_the_code_around_them_is(conn):
    """Exactly `SENDER_ASLEEP_MAX` asleep and exactly `KEEP_UP` of the ask: proof (`sender_asleep` and `next_rate` use `>`, `<`)."""
    asked = 500 * MB                                         # a round ask: `KEEP_UP * asked` is exact in a float
    sent = int(asked * drive_rates.KEEP_UP)
    assert sent == drive_rates.KEEP_UP * asked
    _put(conn, "w1", {"asked_bps": asked, "sent_bps": sent, "loss_permille": 0.1, "first_pass_s": 100.0,
                      "paced_ms": {"wire": 25000, "disk": 0}}, basis="ok")
    assert _learn(conn)[f"card:{CARD}"]["bps"] == sent
    _put(conn, "w2", {"asked_bps": ASKED, "sent_bps": ASKED, "loss_permille": 0.1, "first_pass_s": 100.0,
                      "paced_ms": {"wire": 25001, "disk": 0}}, basis="ok")
    assert _learn(conn)[f"card:{CARD}"]["session_id"] == "w1", "one ms over the limit proves nothing"


def test_a_slow_clean_wave_does_not_raise_a_cap_that_a_proven_wave_set(conn):
    """The proven wave (awake, 300m) is the cap; a newer clean wave of the r12 shape (363m, asleep) must not lift it to 363m."""
    _put(conn, "w_proof", {"asked_bps": 300 * MB, "sent_bps": 300 * MB, "loss_permille": 0.1, "first_pass_s": 100.0,
                           "paced_ms": {"wire": 0, "disk": 0}}, basis="ok")
    _real(conn, REAL[1])
    got = _learn(conn)[f"card:{CARD}"]
    assert got["bps"] == 300 * MB and got["session_id"] == "w_proof" and got["waves"] == 1


def test_loss_near_the_cap_still_cancels_a_proof_at_that_rate_and_above(conn):
    """The ceiling is where loss starts: a proven 250m below a lossy 304m stays; a proven 304m is cancelled by it (derived from
    r12: the lossy wave's own evidence line said `lossy_at=304m`; the loss value 5 per mille is made up, above `LOSS_PERMILLE`)."""
    awake = {"first_pass_s": 100.0, "paced_ms": {"wire": 0, "disk": 0}}
    _put(conn, "w_clean", {"asked_bps": 250 * MB, "sent_bps": 250 * MB, "loss_permille": 0.1, **awake}, basis="ok")
    _put(conn, "w_equal", {"asked_bps": 304 * MB, "sent_bps": 304 * MB, "loss_permille": 0.1, **awake}, basis="ok")
    _put(conn, "w_lossy", {"asked_bps": 304 * MB, "sent_bps": 304 * MB, "loss_permille": 5.0, **awake}, basis="ok")
    got = _learn(conn)[f"card:{CARD}"]
    assert got["bps"] == 250 * MB and got["lossy_bps"] == 304 * MB and got["session_id"] == "w_clean"


# --- no proof, no cap: the shadow row ------------------------------------------------------------------------------


def test_the_shadow_of_a_wave_after_r12_says_unproven_and_not_would_cap(conn):
    """The row of the issue: before, `card:ens34=371m` and `WOULD_CAP 669m -> 371m`. Now no path is proven: `UNPROVEN`, `would` = asked."""
    for row in REAL:
        _real(conn, row)
    decision = {"link": {"interface": CARD}, "roster": [{"mac": MAC}]}
    netcap.shadow(conn, "ses_next", decision, ASKED, LOSS)
    (row,) = _shadow_rows(conn)
    assert row["verdict"] == netcap.UNPROVEN and row["mode"] == "shadow"
    assert f"would={netcap.fmt(ASKED)}" in row["evidence"] and f"asked={netcap.fmt(ASKED)}" in row["evidence"]
    assert all("371m" not in e and "WOULD_CAP" not in e for e in row["evidence"])
    assert netcap.bound(ASKED, _learn(conn)) == (ASKED, None), "no cap -- the rate is the rate"


def test_the_shadow_of_a_proven_wave_still_says_would_cap(conn):
    """The other side: a wave with the evidence still gives a cap, and the row names it."""
    _put(conn, "w1", {"asked_bps": 300 * MB, "sent_bps": 300 * MB, "loss_permille": 0.1, "first_pass_s": 100.0,
                      "paced_ms": {"wire": 0, "disk": 0}}, basis="ok")
    netcap.shadow(conn, "ses_next", {"link": {"interface": CARD}, "roster": [{"mac": MAC}]}, ASKED, LOSS)
    (row,) = _shadow_rows(conn)
    assert row["verdict"] == netcap.WOULD_CAP and f"would={netcap.fmt(300 * MB)}" in row["evidence"]
