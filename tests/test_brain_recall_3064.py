"""#3064 שלב 5: **המוח מחליט לפי מה שזכר** -- הכלל M1, "משכפל שחוזר משתיקה" (`decide.remembered`, `server/brain/recall.py`).

התכנון: ראו #3064. מה נבדק:
- **הכלל:** משכפל שחזר משתיקה ב-`brain_memory_min_returns` סבבים בתוך `brain_memory_max_age_days` מקבל החזקה עד השתיקה
  הארוכה שממנה חזר, ולא מעל התקרה. אחרי החלון -- PROCEED כמו היום.
- **הזיכרון רק מאריך:** לא הופך HOLD ל-PROCEED, ולא נוגע בכשל עם ראיה משלו.
- **ריק, ישן, סותר, לא נקרא, מעט מדי:** ההחלטה של היום. ריק = אותה החלטה בדיוק, גם בסיבה.
- **התצפית:** חבר שסומן `lost` וחזר באותו סבב (progress או hello מצטרף) נרשם פעם אחת, רק אחרי החלטת LOST של המוח.
- **shadow לא משנה כלום;** enforce מחזיק. כל החלטה שנשענה על הזיכרון נרשמת עם הרשומות שלה (``memory#N``).
- **הלולאה כולה:** שלושה סבבים בצל שבהם המשכפל חזר, ובסבב הרביעי ב-enforce יומן הכתיבה שלו נשמר.

**בלי sleep (הלקח של #3176):** כל זמן מוזרק -- `now` של הצילום, של `veto.check` ושל `recall.returned`, ו-`at` של
השורות. שום טסט אינו מחכה לשעון.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.agent import reports
from server.brain import decide as brain, memory, observe, recall, veto
from server.rounds import room

MAC, OTHER = "aa:00:00:00:00:01", "bb:00:00:00:00:02"
W = "W-now"
NOW = 1_800_000_000.0
DAY = 86400.0
CFG = brain.Config()


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    veto.forget()
    monkeypatch.setattr(memory, "_orphans_closed", True)   # an open hold seeded in the past is not an orphan here
    c = db.connect(tmp_path / "recall.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()


def iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat(timespec="seconds")


def lost(conn, mac, round_id, at, *, verdict="PROCEED", outcome="הפעולה בוצעה", point="D21"):
    """החלטת K2 שקבעה את `mac` LOST -- בדיוק בצורה ש-`veto._remember` כותב."""
    return memory.record(conn, mode="shadow", rule="K2", point=point, action="EXPIRE_LEDGER", verdict=verdict,
                         reason="אין ראיה חיה", evidence=(f"{mac}=LOST",), round_id=round_id,
                         entity=("machine", mac), outcome=outcome, at=iso(at))


def came_back(conn, mac, round_id, at, silence):
    return memory.record(conn, mode="shadow", rule=recall.RULE, point=recall.ACTION, action="LIVENESS",
                         verdict=recall.RETURNED, reason="חזר", evidence=("after=#0", f"silence_s={silence}"),
                         round_id=round_id, entity=("machine", mac), outcome=recall.RETURNED_HE, at=iso(at))


def history(conn, mac, silences, *, days_ago=(3, 2, 1), now=NOW):
    """סבב לכל שתיקה: LOST ואז חזרה."""
    ids = []
    for n, (silence, d) in enumerate(zip(silences, days_ago)):
        at = now - d * DAY
        lost(conn, mac, f"R{n}", at)
        ids.append(came_back(conn, mac, f"R{n}", at + 60, silence))
    return ids


def snap(*, memory_=(), evidence_age=None, held_s=0.0, own=None, now=NOW, targets=(MAC,)):
    """צילום של פעולה הרסנית אחת על `targets`. `evidence_age` = לפני כמה זמן נראתה ראיה אחרונה. None = אין ראיה:
    חבר שסומן `lost`, ש-`updated_at` שלו אינו דיווח שלו (`observe.build`)."""
    members = [{"mac": m, "state": "writing" if evidence_age is not None else observe.LOST_STATE,
                "updated_at": iso(now - (evidence_age if evidence_age is not None else 0)), "targets_json": "[]"}
               for m in targets]
    p = observe.Proposal("FAIL_WAVE", "D16", tuple(targets), "test", (), own, held_s)
    return observe.build(now=now, round_id=W, members=members, proposals=(p,), memory=memory_)


def k2(s, cfg=CFG):
    return brain.decide(s, cfg)[-1]


# --- 1. הכלל, כפונקציה טהורה ------------------------------------------------------------------------------------


def recall_of(conn, mac=MAC, now=NOW, cfg=CFG):
    return recall.one(conn, mac, now=now, max_age_s=cfg.max_age_s)


def test_three_returns_hold_until_the_longest_silence_it_came_back_from(conn):
    ids = history(conn, MAC, [240, 400, 300])
    r = recall_of(conn)
    held = k2(snap(memory_=(r,), evidence_age=390))
    assert held.verdict == brain.HOLD and held.reason.startswith("זיכרון:"), held
    assert "400s" in held.reason and set(held.memory) == set(ids)
    past = k2(snap(memory_=(r,), evidence_age=401))
    assert past.verdict == brain.PROCEED and "400s" in past.reason, "after the window it is today's PROCEED"


def test_without_evidence_the_silence_is_the_lower_bound_lost_plus_the_hold(conn):
    history(conn, MAC, [400, 400, 400])
    r = recall_of(conn)
    assert k2(snap(memory_=(r,), held_s=0)).verdict == brain.HOLD           # 180 < 400
    assert k2(snap(memory_=(r,), held_s=219)).verdict == brain.HOLD         # 399 < 400
    assert k2(snap(memory_=(r,), held_s=220)).verdict == brain.PROCEED      # 400: the window passed


def test_the_window_never_passes_the_ceiling(conn):
    history(conn, MAC, [7200, 7200, 7200])
    r = recall_of(conn)
    cfg = brain.Config(ceiling_s=600)
    assert k2(snap(memory_=(r,), evidence_age=599), cfg).verdict == brain.HOLD
    assert k2(snap(memory_=(r,), evidence_age=601), cfg).verdict == brain.PROCEED
    over = k2(snap(memory_=(r,), held_s=600), cfg)
    assert over.verdict == brain.PROCEED and "התקרה" in over.reason, "the ceiling still ends every hold"


@pytest.mark.parametrize("returns, needed, verdict", [(2, 3, brain.PROCEED), (3, 3, brain.HOLD), (1, 1, brain.HOLD)])
def test_the_minimum_of_returns_is_counted_in_rounds(conn, returns, needed, verdict):
    history(conn, MAC, [400] * returns, days_ago=(5, 4, 3)[:returns])
    cfg = brain.Config(min_returns=needed)
    got = k2(snap(memory_=(recall_of(conn, cfg=cfg),), evidence_age=300), cfg)
    assert got.verdict == verdict, got
    if verdict == brain.PROCEED:
        assert f"ב-{returns} סבבים, ונדרשים {needed}" in got.reason


def test_two_returns_in_one_round_are_one_round(conn):
    for n in range(3):                        # three silences and three returns, all in R0
        lost(conn, MAC, "R0", NOW - DAY + n * 1000)
        came_back(conn, MAC, "R0", NOW - DAY + n * 1000 + 60, 400)
    got = k2(snap(memory_=(recall_of(conn),), evidence_age=300))
    assert got.verdict == brain.PROCEED and "ב-1 סבבים" in got.reason


# --- 2. ריק, ישן, סותר, לא נקרא ------------------------------------------------------------------------------------


def test_empty_memory_is_todays_decision_word_for_word(conn):
    r = recall_of(conn)
    assert r == observe.Recall(MAC)
    today = k2(snap(evidence_age=300))
    assert k2(snap(memory_=(r,), evidence_age=300)) == today
    assert today.verdict == brain.PROCEED and today.memory == () and "זיכרון" not in today.reason


def test_stale_memory_is_todays_decision(conn):
    history(conn, MAC, [400, 400, 400], days_ago=(100, 95, 91))
    stale = observe.Recall(MAC, returns=((1, NOW - 91 * DAY, 400.0, "R0"),) * 3)
    got = k2(snap(memory_=(stale,), evidence_age=300))
    assert got.verdict == brain.PROCEED and "הזיכרון ישן" in got.reason
    assert recall_of(conn) == observe.Recall(MAC), "the window of the read is the setting: older rows are not read"


def test_contradicting_memory_a_round_it_did_not_come_back_from_is_todays_decision(conn):
    history(conn, MAC, [400, 400, 400])
    gone = lost(conn, MAC, "R9", NOW - 0.5 * DAY)          # judged LOST, never came back in R9
    r = recall_of(conn)
    assert [x[0] for x in r.losses] == [gone]
    got = k2(snap(memory_=(r,), evidence_age=300))
    assert got.verdict == brain.PROCEED and "הזיכרון סותר" in got.reason and gone in got.memory


def test_a_contradiction_older_than_the_window_no_longer_counts(conn):
    history(conn, MAC, [400, 400, 400])
    lost(conn, MAC, "R9", NOW - 200 * DAY)
    cfg = brain.Config(max_age_s=300 * DAY)
    assert k2(snap(memory_=(recall_of(conn, cfg=cfg),), evidence_age=300), cfg).verdict == brain.PROCEED
    assert k2(snap(memory_=(recall_of(conn),), evidence_age=300)).verdict == brain.HOLD, "90 days: it aged out"


def test_the_current_round_is_never_a_contradiction(conn):
    history(conn, MAC, [400, 400, 400])
    lost(conn, MAC, W, NOW - 120)                            # this very silence, still open
    assert k2(snap(memory_=(recall_of(conn),), evidence_age=300)).verdict == brain.HOLD


def test_unread_memory_is_todays_decision_and_says_so(conn, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("disk")
    monkeypatch.setattr(memory, "decisions", boom)
    r = recall_of(conn)
    assert r.unread is True
    got = k2(snap(memory_=(r,), evidence_age=300))
    assert got.verdict == brain.PROCEED and "הזיכרון לא נקרא" in got.reason


# --- 3. הזיכרון רק מאריך ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("age, own", [(5, None), (60, None), (None, "readback failed")])
def test_memory_never_turns_a_decision_of_today_into_a_shorter_one(conn, age, own):
    """ראיה חיה -> HOLD עם או בלי זיכרון; כשל עם ראיה משלו -> PROCEED עם או בלי זיכרון. גם זיכרון סותר."""
    history(conn, MAC, [400, 400, 400])
    strong = observe.Recall(MAC, returns=tuple((n, NOW - DAY, 9999.0, f"R{n}") for n in range(3)))
    lost(conn, MAC, "R9", NOW - DAY)
    for r in (strong, recall_of(conn)):                      # a memory that would hold, and a contradicting one
        assert k2(snap(memory_=(r,), evidence_age=age, own=own)).verdict == \
            k2(snap(evidence_age=age, own=own)).verdict


def test_I3_the_memory_of_another_cloner_does_not_hold_this_one(conn):
    history(conn, OTHER, [400, 400, 400])
    got = k2(snap(memory_=(recall_of(conn, mac=OTHER),), evidence_age=300))
    assert got.verdict == brain.PROCEED and got.memory == ()


def test_the_memory_is_part_of_the_snapshot_hash(conn):
    history(conn, MAC, [400, 400, 400])
    assert snap(memory_=(recall_of(conn),)).digest() != snap().digest()
    assert snap(memory_=(recall_of(conn),)).digest() == snap(memory_=(recall_of(conn),)).digest()


# --- 4. התצפית: "חזר" ---------------------------------------------------------------------------------------------


def test_a_return_is_recorded_once_after_a_lost_of_the_brain_with_a_lower_bound(conn):
    first = lost(conn, MAC, W, NOW - 100)
    lost(conn, MAC, W, NOW - 40, point="D19")                # a second LOST in the same silence
    got = recall.returned(conn, MAC, W, now=NOW)
    row = memory.decisions(conn, kind="machine", key=MAC, limit=1)[0]
    assert row["id"] == got and row["verdict"] == "RETURNED" and row["outcome"] == recall.RETURNED_HE
    assert f"after=#{first}" in row["evidence"] and "silence_s=280" in row["evidence"], row   # 100 + lost 180
    assert recall.returned(conn, MAC, W, now=NOW + 5) is None, "a second hello of the same return"


def test_no_lost_of_the_brain_no_return(conn):
    lost(conn, MAC, "other-round", NOW - 100)
    assert recall.returned(conn, MAC, W, now=NOW) is None
    assert memory.decisions(conn, kind="machine", key=MAC, rule=recall.RULE) == []


def test_off_records_no_return(conn):
    db.set_setting(conn, veto.SETTING, "off")
    lost(conn, MAC, W, NOW - 100)
    assert recall.returned(conn, MAC, W, now=NOW) is None


def test_a_memory_hold_that_came_back_is_closed_as_right(conn):
    held = lost(conn, MAC, W, NOW - 100, verdict="HOLD", outcome=None)
    recall.returned(conn, MAC, W, now=NOW)
    row = next(r for r in memory.decisions(conn, kind="machine", key=MAC) if r["id"] == held)
    assert row["outcome"] == recall.RETURNED_HE


def test_the_return_never_raises(conn, monkeypatch):
    lost(conn, MAC, W, NOW - 100)
    monkeypatch.setattr(memory, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("full")))
    assert recall.returned(conn, MAC, W, now=NOW) is None


def _session(conn, state="running"):
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES (?, 'g', 'img', 'ROOM', 1, 60, ?, 't', ?, 0)",
                 (W, state, iso(time.time())))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, 'lost', ?, '[]')", (W, MAC, iso(time.time() - 30)))
    conn.commit()


def test_a_lost_member_that_reports_again_is_a_return(conn):
    _session(conn)
    lost(conn, MAC, W, time.time() - 30)
    got = reports.ingest(conn, {"session_id": W, "mac": MAC, "state": "writing",
                                "targets": [{"dev": "sda", "state": "writing"}]})
    assert got.get("ok") is not False, got
    rows = memory.decisions(conn, kind="machine", key=MAC, rule=recall.RULE)
    assert [r["verdict"] for r in rows] == ["RETURNED"]


def test_a_lost_member_that_rejoins_after_a_reboot_is_a_return_once(conn):
    _session(conn)
    lost(conn, MAC, W, time.time() - 30)
    for _hello in range(3):
        assert room.rejoined_mid_stream(conn, {"id": W}, MAC) is True
    rows = memory.decisions(conn, kind="machine", key=MAC, rule=recall.RULE)
    assert [r["verdict"] for r in rows] == ["RETURNED"]


# --- 5. דרך הווטו: shadow לא משנה כלום, enforce מחזיק -----------------------------------------------------------------


def _member_silent(conn, seconds):
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json) VALUES"
                 " (?, ?, 'writing', ?, '[]')", (W, MAC, iso(time.time() - seconds)))
    conn.execute("INSERT INTO write_ledger (round_id, wave_id, mac, serial, size_bytes, boot_id, created_at)"
                 " VALUES ('R-1', ?, ?, 'S1', 1000, 'boot', ?)", (W, MAC, iso(time.time() - 900)))
    conn.commit()


def _kept(conn):
    return conn.execute("SELECT COUNT(*) FROM write_ledger WHERE wave_id = ? AND mac = ?", (W, MAC)).fetchone()[0] == 1


def _mark_lost(conn):
    store = type("S", (), {"members": lambda self, wid: conn.execute(
        "SELECT * FROM session_members WHERE session_id = ?", (wid,)).fetchall()})()
    room._mark_lost(conn, store, {"id": W, "started_at": iso(time.time() - 3600)}, store.members(W))


def _k2_rows(conn, round_id=W):
    return [r for r in memory.decisions(conn, kind="machine", key=MAC, rule="K2") if r["round_id"] == round_id]


def test_shadow_records_the_memory_hold_and_changes_nothing(conn):
    ids = history(conn, MAC, [400, 400, 400], now=time.time())
    _member_silent(conn, 200)
    _mark_lost(conn)
    assert not _kept(conn), "shadow: the ledger is deleted exactly as today"
    row = _k2_rows(conn)[0]
    assert row["verdict"] == "HOLD" and row["reason"].startswith("זיכרון:") and row["mode"] == "shadow"
    assert {f"memory#{i}" for i in ids} <= set(row["evidence"])
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'brain_veto'").fetchone()[0] == 0


def test_shadow_check_says_it_would_block_and_proceeds(conn):
    history(conn, MAC, [400, 400, 400], now=time.time())
    _member_silent(conn, 200)
    got = veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=[MAC], reason="t")
    assert got.proceed is True and got.would_block is True and got.reason.startswith("זיכרון:")


def test_enforce_keeps_the_ledger_of_a_cloner_that_comes_back(conn):
    history(conn, MAC, [400, 400, 400], now=time.time())
    _member_silent(conn, 200)
    db.set_setting(conn, veto.SETTING, "enforce")
    _mark_lost(conn)
    assert _kept(conn)
    assert "זיכרון" in conn.execute("SELECT detail FROM journal WHERE event = 'brain_veto'").fetchone()[0]


@pytest.mark.parametrize("memory_state", ["empty", "stale", "contradicting", "too_few"])
def test_enforce_without_a_basis_in_memory_deletes_as_today(conn, memory_state):
    now = time.time()
    if memory_state == "stale":
        history(conn, MAC, [400, 400, 400], days_ago=(120, 110, 100), now=now)
    elif memory_state == "contradicting":
        history(conn, MAC, [400, 400, 400], now=now)
        lost(conn, MAC, "R9", now - 3600)
    elif memory_state == "too_few":
        history(conn, MAC, [400, 400], days_ago=(2, 1), now=now)
    _member_silent(conn, 200)
    db.set_setting(conn, veto.SETTING, "enforce")
    _mark_lost(conn)
    assert not _kept(conn)
    assert _k2_rows(conn)[0]["verdict"] == "PROCEED"


def test_the_memory_thresholds_are_the_codes_and_not_settings(conn):
    """נדב 14/11 בערב: `min_returns` ו-`max_age_s` הם ערכי התחלה בקוד. שני סבבים אינם מספיקים (3), והגדרה ישנה של 2 אינה נקראת."""
    history(conn, MAC, [400, 400], days_ago=(2, 1), now=time.time())
    _member_silent(conn, 200)
    db.set_setting(conn, veto.SETTING, "enforce")
    db.set_setting(conn, "brain_memory_min_returns", "2")           # retired: never read
    assert veto.config(conn).min_returns == 3
    _mark_lost(conn)
    assert not _kept(conn), "two returns are not enough: the setting that said 2 is not read"


def test_two_returns_are_enough_when_the_brains_own_value_is_two(conn, monkeypatch):
    history(conn, MAC, [400, 400], days_ago=(2, 1), now=time.time())
    _member_silent(conn, 200)
    db.set_setting(conn, veto.SETTING, "enforce")
    real = veto.config
    monkeypatch.setattr(veto, "config", lambda c: dataclasses.replace(real(c), min_returns=2))
    _mark_lost(conn)
    assert _kept(conn)


def test_the_memory_defaults_and_a_stale_stored_value_is_not_read(conn):
    assert (veto.config(conn).min_returns, veto.config(conn).max_age_s) == (3, 90 * DAY)
    db.set_setting(conn, "brain_memory_max_age_days", "30")
    db.set_setting(conn, "brain_memory_min_returns", "abc")
    assert (veto.config(conn).min_returns, veto.config(conn).max_age_s) == (3, 90 * DAY)


# --- 6. הלולאה כולה: נזכר, ואז מחליט ------------------------------------------------------------------------------------


def test_three_shadow_rounds_it_came_back_from_then_enforce_keeps_the_fourth(conn):
    t0 = time.time() - 4 * DAY
    for n in range(3):
        round_id = f"R{n}"
        at = t0 + n * DAY
        conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json) VALUES"
                     " (?, ?, 'writing', ?, '[]')", (round_id, MAC, iso(at - 200)))
        conn.commit()
        got = veto.check(conn, action="EXPIRE_LEDGER", rule="D21", round_id=round_id, targets=[MAC],
                         reason="no progress", now=at)
        assert got.proceed is True and got.would_block is False, "shadow, no memory yet: today's PROCEED"
        conn.execute("UPDATE brain_decisions SET at = ? WHERE round_id = ?", (iso(at), round_id))
        conn.commit()
        assert recall.returned(conn, MAC, round_id, now=at + 300) is not None   # came back after 200+300+...
    db.set_setting(conn, veto.SETTING, "enforce")
    _member_silent(conn, 200)
    _mark_lost(conn)
    assert _kept(conn), "the fourth time the brain remembers, and the ledger stays"
    row = _k2_rows(conn)[0]
    assert row["verdict"] == "HOLD" and row["reason"].startswith("זיכרון:") and "ב-3 סבבים" in row["reason"]
    assert json.dumps(row["evidence"]).count("memory#") == 3
