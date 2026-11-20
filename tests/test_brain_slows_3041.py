"""#3041 (K3): "the cloner will slow the round to ~X MB/s" -- before the wave, from what the brain learned.

Zeus 15/11 (Nadav's #3041): the warning is shown for every cloner whose LEARNED capacity is below the others' in the wave,
**only when by `waveeta` it adds at least a minute to the round**. No percentage: the minute comes from the prediction.
Shadow like the rest of K3: a warning, nothing is excluded or changed.

The numbers are numbers for the test, not measurements: a 10GB compressed image, 1Gb/s cards (a 900Mb/s ceiling,
`LINK_CEILING_PER_MBPS`), a learned capacity of 400Mb/s (the prediction: 200s against 89s, 111s added).

**Negative control:** with `server/brain/linkwarn.py` back at the base of this PR, the tests fail on behaviour: the
learned-slower cloner gets no warning. They import nothing new, so they are all behaviour tests.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import linkwarn, memory, veto

SIZE = 10 * 10 ** 9
NAMES = {"aa": "משכפל 1", "bb": "משכפל 2", "cc": "משכפל 3"}


def _nic(mbps=1000, source="reported"):
    return {"speed_mbps": mbps, "source": source, "why": None}


def _links(*macs):
    return {m: _nic() for m in macs}


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    veto.forget()


def test_a_cloner_learned_slower_than_the_others_is_warned_with_what_it_adds():
    [f] = linkwarn.judge(_links("aa", "bb"), NAMES, SIZE, {"aa": 400_000_000})
    assert f.mac == "aa" and f.verdict == linkwarn.WARN and f.learned and f.rate_bps == 400_000_000
    assert f.adds_s == pytest.approx(8e10 / 4e8 - 8e10 / 9e8, abs=1) and f.faster == ("משכפל 2",)
    text = linkwarn.text_he(f)
    assert "יאט את הסבב ל-~50MB/s" in text and "דק'" in text


def test_the_minute_is_the_line_not_a_percentage():
    below = linkwarn.judge(_links("aa", "bb"), NAMES, SIZE, {"aa": 540_000_000})      # adds 59s
    above = linkwarn.judge(_links("aa", "bb"), NAMES, SIZE, {"aa": 530_000_000})      # adds 62s
    assert below == [] and len(above) == 1 and above[0].adds_s >= linkwarn.MIN_ADDED_S


def test_a_small_image_does_not_make_the_same_difference_worth_a_warning():
    assert linkwarn.judge(_links("aa", "bb"), NAMES, SIZE // 10, {"aa": 400_000_000}) == []


def test_no_warning_when_nothing_is_slower_or_nothing_is_known():
    assert linkwarn.judge(_links("aa", "bb"), NAMES, SIZE, {"aa": 400_000_000, "bb": 400_000_000}) == []
    assert linkwarn.judge(_links("aa"), NAMES, SIZE, {"aa": 400_000_000}) == [], "alone in the wave: nobody to slow"
    assert linkwarn.judge(_links("aa", "bb"), NAMES, SIZE, {}) == [], "no learned capacity: the card, as before"
    assert linkwarn.judge(_links("aa", "bb"), NAMES, None, {"aa": 400_000_000}) == [], "unknown size: no prediction"
    assert linkwarn.judge(_links("aa", "bb"), NAMES, SIZE, {"aa": 5_000_000_000}) == [], "above the card: the card"


def test_an_assumed_card_counts_for_the_others_as_the_wave_counts_it():
    links = {"aa": _nic(), "bb": _nic(1000, "assumed")}
    [f] = linkwarn.judge(links, NAMES, SIZE, {"aa": 400_000_000})
    assert f.faster == ("משכפל 2",)


def test_a_cloner_already_warned_for_its_slow_link_is_not_warned_twice():
    found = linkwarn.judge({"aa": _nic(100), "bb": _nic()}, NAMES, SIZE, {"aa": 50_000_000})
    assert [f.mac for f in found] == ["aa"] and found[0].adds_s is None


def test_the_decision_is_recorded_in_shadow_with_what_it_adds(conn):
    memory.remember(conn, "machine", "aa", "capability", {"limited_bps": 410e6, "clean_bps": 400e6})
    linkwarn.started(conn, "ses_w", ["aa", "bb"], NAMES, SIZE)
    [row] = [d for d in memory.decisions(conn, rule="K3", round_id="ses_w", limit=10) if d["action"] == "LINK_SLOW"]
    assert row["verdict"] == "WARN" and row["mode"] == "shadow" and row["entity_key"] == "aa", row
    assert "יאט את הסבב" in row["reason"] and any(e.startswith("adds_s=11") for e in row["evidence"]), row
    assert "אזהרה בלבד" in row["reason"]
