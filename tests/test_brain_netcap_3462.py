"""#3462: תקרת רשת נלמדת לכל נתיב (כרטיס ההפצה של השרת, וכל משכפל) -- `server/brain/netcap.py`.

התקרה = הקצב הגבוה ביותר שהוכח בלי אבדן מעל הסף, מראיה חיובית בלבד, מ-`wave_rates` (אין טבלה חדשה):

- גל נכנס רק אם לימד (`teaches`), האבדן שלו נמדד, ודיסק הספרייה לא קבע אותו (`server_io.basis != server_disk`);
- הקצב שהוכח הוא `min(asked, sent)` -- מה שהמנוע דחף, ו`sent` שלא ידוע אינו הוכחה;
- #3536: וגל מוכיח רק כשהשולח לא היה הגבול -- דיסק הספרייה נמדד (`basis == ok`), השולח לא ישן, והוא עמד בקצב (`tests/test_netcap_sender_3536.py`);
- אבדן מעל הסף מבטל הוכחות ישנות מקצב שווה או גבוה ממנו (באותו נתיב), לא מקצב נמוך יותר;
- אין הוכחה = אין תקרה (`None`), לא מספר שהומצא;
- השימוש בצל בלבד: שורת `K8`/`WAVE_NETCAP` ב-`brain_decisions`, והקצב של הגל אינו משתנה.

**בקרה שלילית:** `server/brain/netcap.py` (חסר, ו-`chunk_rate.py` ו-`say.py` חוזרים לבסיס) -- כל הטסטים כאן נופלים
(`ImportError`/`AttributeError`), והטסט ש-`start` רושם שורת צל נופל גם כשהמודול קיים אבל החיבור ב-`chunk_rate.start` חסר.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from server import db
from server.brain import memory, netcap, say
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6
A, B, C = "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02", "aa:bb:cc:00:00:03"
LOSS = chunk_rate.LOSS_PERMILLE
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _wave(conn, sid, *, interface="eth0", macs=(A, B), asked=300 * MB, sent="same", loss=0.0, teaches=True,
          skip=None, disk="ok", asleep=0.0) -> None:
    """גל חתיכות שנסגר. `sent="same"` = `asked`; `None` = לא נמדד. `loss` באלפית; `None` = לא נמדד. `disk` = ה-basis של server_io
    (`None` = אין שורה, לא נמדד). `asleep` = שבר מהמעבר הראשון שהשולח ישן לקצב שלו (#3536); `None` = `paced_ms` לא נמדד."""
    sample = {"asked_bps": asked, "sent_bps": asked if sent == "same" else sent, "loss_permille": loss,
              "teaches": teaches, "skip": skip}
    if asleep is not None:
        sample.update(first_pass_s=100.0, paced_ms={"wire": round(asleep * 100000), "disk": 0})
    decision = {"max_bitrate": f"{asked // MB}m", "basis": "chunk_hold", "link": {"interface": interface},
                "roster": [{"mac": m, "serial": f"S{n}", "port": n} for n, m in enumerate(macs)]}
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?,?,?,?)",
                 (sid, json.dumps(decision), NOW.isoformat(timespec="seconds"),
                  json.dumps({"chunk": sample, "ended": "send_done"})))
    if disk is not None:
        conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, basis) VALUES ('wave', ?, ?, '{}', ?)",
                     (sid, NOW.isoformat(), disk))
    conn.commit()


def _caps(conn, interface="eth0", macs=(A, B)):
    return netcap.learn(conn, interface, macs, LOSS)


def _bps(caps) -> dict:
    return {path: c["bps"] for path, c in caps.items()}


# --- the cap is what was proven -------------------------------------------------------------------------------------


def test_a_clean_wave_proves_its_rate_for_the_card_and_for_every_cloner_in_it(conn):
    _wave(conn, "w1", asked=300 * MB)
    assert _bps(_caps(conn)) == {"card:eth0": 300 * MB, f"cloner:{A}": 300 * MB, f"cloner:{B}": 300 * MB}


def test_the_proof_is_what_the_engine_pushed_not_what_was_asked(conn):
    _wave(conn, "w1", asked=300 * MB, sent=270 * MB)         # kept its pace (>= KEEP_UP of the ask): the proof is the 270, not 300
    assert _caps(conn)["card:eth0"]["bps"] == 270 * MB


def test_the_highest_clean_rate_wins(conn):
    for sid, rate in (("w1", 300), ("w2", 500), ("w3", 400)):
        _wave(conn, sid, asked=rate * MB)
    got = _caps(conn)["card:eth0"]
    assert got["bps"] == 500 * MB and got["waves"] == 3 and got["session_id"] == "w2"


def test_a_cloner_that_was_not_in_the_wave_has_no_proof_and_neither_has_another_card(conn):
    _wave(conn, "w1", interface="eth0", macs=(A,))
    got = _caps(conn, interface="eth1", macs=(A, C))
    assert got["card:eth1"]["bps"] is None and got[f"cloner:{C}"]["bps"] is None
    assert got[f"cloner:{A}"]["bps"] == 300 * MB, "the cloner is proven on any card it was in"


def test_nothing_proven_is_no_cap_not_zero_and_not_a_guess(conn):
    got = _caps(conn)
    assert set(got) == {"card:eth0", f"cloner:{A}", f"cloner:{B}"}
    assert all(c["bps"] is None and c["waves"] == 0 and c["session_id"] is None for c in got.values())
    assert netcap.bound(500 * MB, got) == (500 * MB, None)


def test_a_card_that_was_not_read_has_no_card_path(conn):
    _wave(conn, "w1")
    assert set(netcap.learn(conn, None, (A,), LOSS)) == {f"cloner:{A}"}


# --- positive evidence only ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("what,kwargs", [
    ("server_disk basis", {"disk": "server_disk"}),
    ("a wave that taught nothing", {"teaches": False, "skip": "chunk_net_gap"}),
    ("a wave that taught nothing, by the server", {"teaches": False, "skip": "server_disk"}),
    ("a skip on a wave marked teaches", {"skip": "too_short"}),
    ("loss not measured", {"loss": None}),
    ("the rate the engine pushed not known", {"sent": None}),
    ("#3536 the server disk was not measured", {"disk": None}),
    ("#3536 the sender slept for its own pace", {"asleep": 0.64}),
    ("#3536 the sender's sleep not measured", {"asleep": None}),
    ("#3536 the engine did not keep its pace (15/11: asked 892m, put 263Mb/s)", {"asked": 892 * MB, "sent": 263 * MB}),
    ("#3536 nothing was sent", {"sent": 0}),
])
def test_only_a_wave_with_positive_evidence_proves_a_rate(conn, what, kwargs):
    _wave(conn, "w1", **kwargs)
    assert all(c["bps"] is None for c in _caps(conn).values()), what


def test_a_wave_whose_server_disk_was_measured_fine_proves_and_one_not_measured_does_not(conn):
    _wave(conn, "w1", asked=300 * MB, disk="ok")
    _wave(conn, "w2", asked=500 * MB, disk=None)             # #3536: "not measured" is not "the network was the limit"
    got = _caps(conn)["card:eth0"]
    assert got["bps"] == 300 * MB and got["disk_unmeasured"] is False and got["waves"] == 1
    assert _caps(conn)["cloner:" + A]["session_id"] == "w1"


def test_a_server_disk_wave_does_not_cancel_a_clean_proof_either(conn):
    _wave(conn, "w1", asked=600 * MB)
    _wave(conn, "w2", asked=500 * MB, loss=40.0, disk="server_disk")    # lossy, but the server's disk did it
    assert _caps(conn)["card:eth0"]["bps"] == 600 * MB


# --- loss cancels what it contradicts --------------------------------------------------------------------------------


def test_loss_exactly_at_the_threshold_is_clean_and_above_it_is_not(conn):
    _wave(conn, "w1", asked=300 * MB, loss=LOSS)
    assert _caps(conn)["card:eth0"]["bps"] == 300 * MB
    _wave(conn, "w2", asked=300 * MB, loss=LOSS + 0.01)
    assert _caps(conn)["card:eth0"]["bps"] is None and _caps(conn)["card:eth0"]["lossy_bps"] == 300 * MB


def test_a_newer_lossy_wave_cancels_proofs_of_the_same_or_a_higher_rate_only(conn):
    _wave(conn, "w1", asked=300 * MB)
    _wave(conn, "w2", asked=600 * MB)
    _wave(conn, "w3", asked=500 * MB, loss=5.0)
    got = _caps(conn)["card:eth0"]
    assert got["bps"] == 300 * MB and got["session_id"] == "w1" and got["lossy_bps"] == 500 * MB


def test_a_lossy_wave_with_nothing_clean_below_it_leaves_no_cap(conn):
    _wave(conn, "w1", asked=600 * MB)
    _wave(conn, "w2", asked=500 * MB, loss=5.0)
    got = _caps(conn)["card:eth0"]
    assert got["bps"] is None and got["lossy_bps"] == 500 * MB


def test_a_clean_wave_after_the_loss_proves_again(conn):
    _wave(conn, "w1", asked=500 * MB, loss=5.0)
    _wave(conn, "w2", asked=600 * MB)                        # the network got better (a cable was fixed)
    assert _caps(conn)["card:eth0"]["bps"] == 600 * MB


def test_loss_is_every_cloners_in_the_wave_because_the_worst_machine_is_not_known(conn):
    _wave(conn, "w1", macs=(A, B), asked=300 * MB)
    _wave(conn, "w2", macs=(A, B), asked=300 * MB, loss=5.0)
    assert [c["bps"] for p, c in _caps(conn).items() if p.startswith("cloner:")] == [None, None]
    assert _caps(conn, macs=(C,))[f"cloner:{C}"]["bps"] is None, "a cloner without history is simply unproven"


# --- the min ----------------------------------------------------------------------------------------------------------


def test_bound_takes_the_lowest_proven_cap_and_names_its_path():
    caps = {"card:eth0": {"bps": 800 * MB}, f"cloner:{A}": {"bps": 300 * MB}, f"cloner:{B}": {"bps": None}}
    assert netcap.bound(500 * MB, caps) == (300 * MB, f"cloner:{A}")


def test_bound_leaves_a_rate_that_is_below_every_cap(conn):
    assert netcap.bound(200 * MB, {"card:eth0": {"bps": 300 * MB}}) == (200 * MB, None)
    assert netcap.bound(300 * MB, {"card:eth0": {"bps": 300 * MB}}) == (300 * MB, None)


def test_bound_accepts_caps_from_other_writers_the_same_way():
    caps = {"card:eth0": {"bps": 800 * MB}, "server": {"bps": 250 * MB}}       # the server cap plugs in here
    assert netcap.bound(500 * MB, caps) == (250 * MB, "server")


def test_a_broken_row_is_skipped_and_never_raises(conn):
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES ('bad', 'not json', 'x', '{{')")
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES ('bad2', '[]', 'x', '{}')")
    _wave(conn, "w1")
    assert _caps(conn)["card:eth0"]["bps"] == 300 * MB


# --- the shadow, through chunk_rate.start -----------------------------------------------------------------------------


def _now_wave(conn, sid="ses_now"):
    drives = [{"serial": "S0", "model": "SAMSUNG MZ7LN256", "mac": A, "port": 1}]
    bits = drive_rates.parse_bitrate(drive_rates.decide(conn, sid, lambda: drives, None))
    decision = drive_rates.decision(conn, sid)
    decision["link"] = {"interface": "eth0"}
    conn.execute("UPDATE wave_rates SET decision = ? WHERE session_id = ?", (json.dumps(decision), sid))
    conn.commit()
    return bits


def _rows(conn):
    return [r for r in memory.decisions(conn, rule="K8", limit=50) if r["action"] == netcap.ACTION]


def test_start_records_what_the_cap_would_do_and_does_not_change_the_rate(conn):
    bits = _now_wave(conn)                                   # first: `decide` reads the history, and this is about the cap
    _wave(conn, "w1", asked=200 * MB)
    got = chunk_rate.start(conn, "ses_now", bits, {}, now=NOW.timestamp())
    assert got == bits, "shadow: the rate of the wave is the rate it would be without the cap"
    assert drive_rates.parse_bitrate(drive_rates.decision(conn, "ses_now")["max_bitrate"]) == bits
    (row,) = _rows(conn)
    assert row["mode"] == "shadow" and row["verdict"] == netcap.WOULD_CAP and row["round_id"] == "ses_now"
    assert "card:eth0" in row["reason"] or f"cloner:{A}" in row["reason"]
    assert any(e == f"would={netcap.fmt(200 * MB)}" for e in row["evidence"]) and bits > 200 * MB
    assert row["outcome"] == netcap.SHADOW_OUTCOME_HE and "(צל: רק נרשם)" in row["line_he"]


def test_start_without_a_proof_says_unproven_and_a_rate_below_the_cap_says_within(conn):
    bits = _now_wave(conn)
    chunk_rate.start(conn, "ses_now", bits, {}, now=NOW.timestamp())
    assert [r["verdict"] for r in _rows(conn)] == [netcap.UNPROVEN]
    _now_wave(conn, "ses_two")
    _wave(conn, "w1", asked=bits + 100 * MB)
    chunk_rate.start(conn, "ses_two", bits, {}, now=NOW.timestamp())
    assert [r["verdict"] for r in _rows(conn) if r["round_id"] == "ses_two"] == [netcap.WITHIN]


def test_one_wave_is_one_row_even_when_start_runs_twice(conn):
    bits = _now_wave(conn)
    for _ in range(2):
        chunk_rate.start(conn, "ses_now", bits, {}, now=NOW.timestamp())
    assert len(_rows(conn)) == 1


def test_shadow_never_raises_even_with_a_broken_decision(conn):
    netcap.shadow(conn, "ses_x", {"link": 5, "roster": "no"}, 300 * MB, LOSS)         # must not raise
    conn.close()
    netcap.shadow(conn, "ses_x", {}, 300 * MB, LOSS)                                    # a closed connection: not raise


def test_the_labels_exist_in_hebrew_for_the_consoles_line():
    assert netcap.ACTION in say.ACTION_HE
    assert {netcap.WOULD_CAP, netcap.WITHIN, netcap.UNPROVEN} <= set(say.VERDICT_HE)


def test_the_stream_key_is_the_one_chunk_rate_writes():
    assert netcap.STREAM_KEY == chunk_rate.STREAM_KEY


# --- #3464 meets #3462: the server's read ceiling is one more path of `bound` ---------------------------------------


def _server_io(conn, ref, read, device="sdb"):
    """A measured wave the library disk limited (`basis = server_disk`): what `server_cap.learn` reads."""
    result = {"device": device, "read_mbps": read, "basis": "server_disk", "util": 0.95}     # #3525: a ceiling is learned from a saturated disk
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis)"
                 " VALUES ('wave', ?, ?, '{}', ?, ?, 'server_disk')",
                 (ref, NOW.isoformat(), NOW.isoformat(), json.dumps(result)))
    conn.commit()


def test_the_learned_server_read_ceiling_is_a_path_of_the_bound_and_is_named_in_the_row(conn):
    """(neg) Two waves the library disk limited at 20MB/s = 160Mb/s: a wave asking 300m would be held at 160m by the server
    path, and the row says which path set it. Without `_server_path` in `shadow` the row says UNPROVEN, no cap."""
    for ref in ("io_a", "io_b"):
        _server_io(conn, ref, 20.0)
    assert netcap._server_path(conn) == {"server:sdb": {"path": "server:sdb", "bps": 160 * MB, "waves": 2,
                                                         "session_id": "io_b", "lossy_bps": None, "disk_unmeasured": False}}   # the newest wave
    netcap.shadow(conn, "ses_srv", {"link": {"interface": "eth0"}, "roster": []}, 300 * MB, LOSS)
    (row,) = _rows(conn)
    assert row["verdict"] == netcap.WOULD_CAP and "server:sdb" in row["reason"]
    assert f"would={netcap.fmt(160 * MB)}" in row["evidence"]


def test_a_server_ceiling_that_was_not_learned_is_no_path_not_a_zero(conn):
    assert netcap._server_path(conn) == {}
    _server_io(conn, "io_a", 20.0)                           # one wave is not enough (`MIN_WAVES`)
    assert netcap._server_path(conn) == {}
