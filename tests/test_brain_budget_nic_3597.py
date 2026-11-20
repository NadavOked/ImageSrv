"""#3597 -- K7 (`server/brain/budget.py`) לא קרא את כרטיס המקלטים (`rx_missed`, `rx_no_buffer`) ש-B59 ו-K8 סופרים, וקרא
`RcvbufErrors` בשם `rx_dropped`. הנימוק "נמדדו נקיים" נאמר על "מקלטים" כולל, בגלים שבהם הכרטיס זרק (17/11, J1:
`rx_missed=885, rx_no_buffer=189`).

מה נבדק:

- **הקריאה:** `netsense.receivers_card` סוכם את `wave_senses.nic` של כל מכונות הגל, ומכונה אחת שלא דיווחה (או בלי ethtool)
  היא "לא נקרא" לכולם -- לא "הכרטיס לא זרק".
- **הראיות:** `rcvbuf` (השם הנכון), `rx_missed`, `rx_no_buffer` -- כמו שנקראו, או `unmeasured`. אין עוד `rx_dropped=`.
- **הנימוק:** ה"שקט" נאמר רק על מקורות בשמם. כרטיס שזרק או שלא נקרא אינו "נקי".
- **ההכרעה לא זזה:** כרטיס המקלטים אינו מקצץ (אין ספי כיול: 21-885 בגל שלם הם רעש) -- הכרעת הנימוק בלבד. ו-B59/K8 לא נגעו.
"""

from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import budget as B, netsense
from server.brain.served import Served

MBPS = 1_000_000
CFG = B.Config(ceiling_bps=900 * MBPS)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "nic.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


class _Chunk:
    interface = "eth1"

    def sending_bps(self):
        return 300 * MBPS


class _Sender:
    interface = None

    def status(self):
        return None

    def link(self):
        return {"speed_mbps": 1000}


def _wave(conn, members):
    """גל חתיכות רץ. ``members`` = {mac: nic | None}: ``None`` = אין שורת חושים למכונה (סוכן ישן, או עוד לא דיווחה)."""
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES ('W-1', 'g', 'img', 'ROOM', 1, 1, 'running', 'x', 'now', 0)")
    for mac, nic in members.items():
        conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, udp_drops_json)"
                     " VALUES ('W-1', ?, 'writing', 'now', ?)", (mac, json.dumps({"rcvbuf": 0, "inerrors": 0})))
        if nic is not None:
            _senses(conn, mac, nic)
    conn.commit()


def _senses(conn, mac, nic):
    conn.execute("INSERT INTO wave_senses (session_id, mac, at, senses_json) VALUES ('W-1', ?, 'now', ?)"
                 " ON CONFLICT (session_id, mac) DO UPDATE SET senses_json = excluded.senses_json",
                 (mac, json.dumps({"window_s": 10, "windows": 3, "cpu": [], "disks": {}, "nic": nic})))
    conn.commit()


def _nic(missed, no_buffer, ethtool=True):
    return {"iface": "eth0", "ethtool": ethtool, "rx_missed": missed, "rx_no_buffer": no_buffer, "ring_rx": None,
            "ring_rx_max": None}


def _budget(conn, tmp_path):
    return B.production(conn, served=Served(), chunk=_Chunk(), sender=_Sender(), sys_net=tmp_path, proc=tmp_path / "proc")


def _clean_part(reason: str) -> str:
    """מה ש"שקט (...)" אומר שנמדד נקי -- לפני ההערות על הכרטיס."""
    return reason.split(" נמדדו נקיים")[0]


# --- הקריאה: כרטיס המקלטים, אותו כלל של `receivers_rcvbuf` -------------------------------------------------------------


def test_the_receivers_card_is_summed_over_the_machines_of_the_running_wave(conn):
    assert netsense.receivers_card(conn, "rx_missed") is None, "no running wave: nothing to report"
    _wave(conn, {"aa": _nic(21, 13), "bb": _nic(885, 189)})
    assert netsense.receivers_card(conn, "rx_missed") == 906
    assert netsense.receivers_card(conn, "rx_no_buffer") == 202


def test_a_machine_without_a_senses_row_makes_the_whole_wave_unread_not_a_card_that_did_not_drop(conn):
    _wave(conn, {"aa": _nic(0, 0), "bb": None})
    assert netsense.receivers_card(conn, "rx_missed") is None
    assert netsense.receivers_card(conn, "rx_no_buffer") is None


def test_a_machine_without_ethtool_leaves_only_rx_no_buffer_unread(conn):
    """`wavesense._nic`: בלי ethtool `rx_no_buffer` הוא null. `rx_missed` נקרא, והשדה השני לא נחשב נקי."""
    _wave(conn, {"aa": _nic(4, 0), "bb": _nic(6, None, ethtool=False)})
    assert netsense.receivers_card(conn, "rx_missed") == 10
    assert netsense.receivers_card(conn, "rx_no_buffer") is None


@pytest.mark.parametrize("nic", [None, {}, {"rx_missed": -1}, {"rx_missed": True}, {"rx_missed": "7"}, "x", []])
def test_a_malformed_nic_summary_is_unread(conn, nic):
    _wave(conn, {"aa": _nic(1, 1), "bb": nic})
    assert netsense.receivers_card(conn, "rx_missed") is None


def test_the_card_counters_are_differences_and_a_counter_that_went_back_is_unmeasured():
    a = B.Reading(1000.0, 0, 0, rx_missed=10, rx_no_buffer=5)
    b = B.Reading(1010.0, 0, 0, rx_missed=910, rx_no_buffer=5)
    s = B.derive(a, b)
    assert (s.rx_missed, s.rx_no_buffer) == (900, 0)
    back = B.derive(b, B.Reading(1020.0, 0, 0, rx_missed=3, rx_no_buffer=None))
    assert (back.rx_missed, back.rx_no_buffer) == (None, None), "a wave ended / a machine left: not 0, not negative"


# --- הראיות והנימוק ---------------------------------------------------------------------------------------------------


def test_the_production_budget_records_the_card_the_receivers_reported_and_does_not_call_it_clean(conn, tmp_path):
    """הבאג של J1: הכרטיס זרק, ו-K7 כתב "נמדדו נקיים" בלי לקרוא אותו."""
    _wave(conn, {"aa": _nic(0, 0), "bb": _nic(0, 0)})
    budget = _budget(conn, tmp_path)
    budget.tick()                                                   # the first cycle: no deltas
    time.sleep(0.05)
    conn.execute("UPDATE session_members SET udp_drops_json = ? WHERE mac = 'bb'", (json.dumps({"rcvbuf": 25, "inerrors": 0}),))
    conn.commit()
    assert budget.tick().action == B.CUT_                           # the budget is under its limit now: the next quiet cycle raises
    time.sleep(0.05)
    _senses(conn, "bb", _nic(885, 189))
    d = budget.tick()
    assert d.action == B.RAISE, d
    assert "rx_missed=885" in d.evidence and "rx_no_buffer=189" in d.evidence, d.evidence
    assert "rcvbuf=0" in d.evidence and not any(e.startswith("rx_dropped=") for e in d.evidence), \
        "RcvbufErrors is `rcvbuf`: `rx_dropped` is the card's counter elsewhere (gate_machines, net_history)"
    assert "rx_missed" not in _clean_part(d.reason) and "rx_no_buffer" not in _clean_part(d.reason), d.reason
    assert "כרטיס המקלטים זרק" in d.reason and "rx_missed=885" in d.reason, d.reason


def test_the_card_that_dropped_does_not_cut_the_budget_without_calibration():
    """21-885 בגל שלם הם מספרים קטנים: החיתוך היה רעש. ההכרעה כמו קודם, הנימוק בלבד השתנה."""
    s = B.Sample(300 * MBPS, 0.0, 0, 0, 0.0, abandoned=0, rx_missed=885, rx_no_buffer=189)
    d = B.step(100 * MBPS, CFG, s)
    assert (d.action, d.rate_bps) == (B.RAISE, 100 * MBPS + B.STEP_BPS), d


def test_a_card_that_was_not_read_is_unmeasured_in_the_evidence_and_in_the_reason():
    d = B.step(100 * MBPS, CFG, B.Sample(300 * MBPS, 0.0, 0, 0, 0.0, abandoned=0))
    assert "rx_missed=unmeasured" in d.evidence and "rx_no_buffer=unmeasured" in d.evidence
    assert "rx_missed" not in _clean_part(d.reason) and "כרטיס המקלטים לא נקרא" in d.reason, d.reason


def test_a_card_that_was_read_clean_says_so_by_name_in_the_evidence_only():
    d = B.step(100 * MBPS, CFG, B.Sample(300 * MBPS, 0.0, 0, 0, 0.0, abandoned=0, rx_missed=0, rx_no_buffer=0))
    assert "rx_missed=0" in d.evidence and "rx_no_buffer=0" in d.evidence
    assert "כרטיס המקלטים" not in d.reason and "rcvbuf" in _clean_part(d.reason), d.reason


def test_the_quiet_is_said_by_the_name_of_each_source_never_as_the_receivers_in_general():
    d = B.step(100 * MBPS, CFG, B.Sample(300 * MBPS, 0.0, 0, 0, 0.0, abandoned=None))
    clean = _clean_part(d.reason)
    assert "rcvbuf" in clean and "tx_dropped" in clean and "נטישות" not in clean, d.reason
    assert "מקלטים" not in clean, "'receivers' covers sources that were not read"


def test_loss_in_the_kernel_still_cuts_and_the_card_is_still_in_its_evidence():
    s = B.Sample(300 * MBPS, 0.0, 9, 0, 0.0, abandoned=0, rx_missed=5, rx_no_buffer=None)
    d = B.step(200 * MBPS, CFG, s)
    assert d.action == B.CUT_ and d.rate_bps == 100 * MBPS and "9" in d.reason
    assert "rcvbuf=9" in d.evidence and "rx_missed=5" in d.evidence and "rx_no_buffer=unmeasured" in d.evidence


# --- B29 קורא את הראיות של K7 וסופר "unmeasured" -----------------------------------------------------------------------------


def test_the_two_card_fields_do_not_turn_a_partly_measured_cycle_into_nothing_measured_for_the_peer_choice():
    """`peer.load_of`: HOLD עם ארבעה `=unmeasured` = "K7 לא מדד". שני שדות הכרטיס שנוספו ל-K7 אינם נספרים: אחרת מחזור שבו
    rcvbuf נמדד (נקי) היה מגיע ל-4 ונקרא "לא מדד אובדן או עומס"."""
    from server.brain import peer
    partly = B.Decision(50 * MBPS, B.HOLD, "r", 400 * MBPS, 500 * MBPS,
                        ("rcvbuf=0", "abandoned=unmeasured", "tx_dropped=unmeasured", "io_full=unmeasured",
                         "rx_missed=unmeasured", "rx_no_buffer=unmeasured"))
    assert peer.load_of(partly).busy is False, "rcvbuf was measured: not 'K7 did not measure'"
    blind = B.Decision(50 * MBPS, B.HOLD, "r", 400 * MBPS, 500 * MBPS,
                       ("rcvbuf=unmeasured", "abandoned=unmeasured", "tx_dropped=unmeasured", "io_full=unmeasured",
                        "rx_missed=0", "rx_no_buffer=0"))
    assert peer.load_of(blind).busy is None, "the four senses of before are still all unmeasured"
