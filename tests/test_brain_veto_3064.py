"""#3064, שלב 1: הווטו (`server/brain/veto.py`) ונקודת ה-hook שלו ב-`room._mark_lost` (D21, מחיקת יומן הכתיבה).

- המתג `brain_veto_mode`: off / shadow (ברירת המחדל) / enforce, וערך לא מוכר = shadow.
- **(ב) ברמת ה-hook:** אין מחיקה כשיש ראיה חיה (enforce, I5/I7); ראיה של משכפל **אחר** אינה מחזיקה (I3);
  ובלי ראיה בכלל המחיקה מתבצעת גם ב-enforce.
- **(ד) off:** אין שום קריאה מעבר להגדרה, אין שורה ביומן, והמחיקה כמו היום.
- כל החלטה היא שורה מובנית אחת: זמן, כלל, נקודה, יעד, סיבה, ראיות, hash הצילום. התקרה משחררת החזקה.

**בקרה שלילית:** הסרת ה-hook מ-`_mark_lost` (קריאה ישירה ל-`ledger.expire`, כמו לפני #3064) מפילה כאן את
טסטי ה-hook: ב-enforce השורה נמחקת, וב-shadow אין שורת "הווטו היה חוסם".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import decide as brain, veto
from server.rounds import room

W = "W-1"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")     # session_members בלי sessions/groups: רק מה שהמוח קורא
    yield c
    veto.forget()


def ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def member(conn, mac: str, age: float, state: str = "writing") -> None:
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, ?, ?, ?)", (W, mac, state, ago(age),
                                             json.dumps([{"dev": "sda", "serial": f"{mac}-1", "state": state}])))
    conn.commit()


def write_ledger(conn, mac: str) -> None:
    conn.execute("INSERT INTO write_ledger (round_id, wave_id, mac, serial, size_bytes, boot_id, created_at)"
                 " VALUES ('R-1', ?, ?, ?, 1000, 'boot', ?)", (W, mac, f"{mac}-1", ago(900)))
    conn.commit()


def chunk_ledger(conn, mac: str, age: float) -> None:
    conn.execute("INSERT INTO chunk_ledger (serial, size_bytes, part, round_id, mac, image_id, index_xxh64,"
                 " chunks, ranges_json, updated_at) VALUES (?, 1000, 3, ?, ?, 'img', '0', 10, '[]', ?)",
                 (f"{mac}-1", W, mac, ago(age)))
    conn.commit()


def kept(conn, mac: str) -> bool:
    return conn.execute("SELECT COUNT(*) FROM write_ledger WHERE wave_id = ? AND mac = ?", (W, mac)).fetchone()[0] == 1


def brain_lines(caplog) -> list[dict]:
    return [json.loads(r.getMessage()[len("brain "):]) for r in caplog.records
            if r.name == "imagesrv.brain" and r.getMessage().startswith("brain ")]


class Store:
    def __init__(self, conn):
        self.conn = conn

    def members(self, wave_id):
        return self.conn.execute("SELECT * FROM session_members WHERE session_id = ?", (wave_id,)).fetchall()


def mark_lost(conn):
    wave = {"id": W, "started_at": ago(3600)}
    return room._mark_lost(conn, Store(conn), wave, Store(conn).members(W))


# --- the switch ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, mode", [(None, "shadow"), ("off", "off"), ("shadow", "shadow"),
                                       ("enforce", "enforce"), ("ENFORCE", "shadow"), ("", "shadow")])
def test_the_switch_defaults_to_shadow(conn, raw, mode):
    if raw is not None:
        db.set_setting(conn, veto.SETTING, raw)
    assert veto.mode(conn) == mode


def test_the_thresholds_are_the_codes_start_values_and_only_lost_is_a_setting(conn):
    """נדב 14/11 בערב: ספים טכניים אינם בהגדרות. רק LOST (`lost_wait_seconds`, זמני הסוכן) נקרא, והשאר ערכי ההתחלה בקוד."""
    assert veto.config(conn) == brain.Config(suspect_s=30, lost_s=180, stalled_s=180, ceiling_s=1800)
    for retired in ("brain_hold_ceiling_seconds", "brain_suspect_seconds", "brain_stalled_seconds",
                    "brain_memory_min_returns", "brain_memory_max_age_days"):
        db.set_setting(conn, retired, "600")                       # retired: never read
    db.set_setting(conn, "lost_wait_seconds", "300")              # LOST is the existing setting
    assert veto.config(conn) == brain.Config(suspect_s=30, lost_s=300, stalled_s=180, ceiling_s=1800)


# --- veto.check --------------------------------------------------------------------------------------------------


def test_shadow_says_it_would_block_and_lets_it_go(conn, caplog):
    member(conn, "aa", 2)
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    got = veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="test", part=4)
    assert got.proceed is True and got.would_block is True and got.mode == "shadow"
    k2 = [x for x in brain_lines(caplog) if x["rule"] == "K2"]
    assert len(k2) == 1 and k2[0]["say"] == "הווטו היה חוסם" and k2[0]["point"] == "D16"
    assert k2[0]["verdict"] == "HOLD" and k2[0]["target"] == "aa" and k2[0]["part"] == 4
    assert k2[0]["snap"] == got.snapshot and len(got.snapshot) == 12
    assert k2[0]["evidence"] and k2[0]["reason"].startswith("aa=ALIVE")
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'brain_veto'").fetchone()[0] == 0


def test_enforce_holds_says_so_once_and_the_ceiling_releases(conn, caplog):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    t = [1000.0]
    ask = lambda: veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="test",
                             mono=lambda: t[0])
    assert ask().proceed is False
    t[0] += 1799
    assert ask().proceed is False
    t[0] += 1
    released = ask()
    assert released.proceed is True and "התקרה" in released.reason
    said = [r[0] for r in conn.execute("SELECT detail FROM journal WHERE event = 'brain_veto' ORDER BY id")]
    assert len(said) == 2 and said[0].startswith("הווטו חסם D16") and said[1].startswith("הווטו שחרר D16")


def test_a_failure_with_evidence_of_its_own_goes_through_in_enforce(conn):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    got = veto.check(conn, action="FAIL_DISKS", rule="D14", round_id=W, targets=["aa"], reason="slow",
                     own_evidence="readback failed")
    assert got.proceed is True and got.would_block is False


def test_a_point_without_a_hold_is_logged_and_proceeds_in_enforce(conn):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    got = veto.check(conn, action="FAIL_WAVE", rule="D19", round_id=W, targets=["aa"], reason="x", can_hold=False)
    assert got.proceed is True and got.would_block is True


def test_a_brain_that_cannot_read_holds_in_enforce_and_never_raises(conn, monkeypatch):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    monkeypatch.setattr(veto.observe, "from_db", lambda *a, **k: 1 / 0)
    got = veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="x")
    assert got.proceed is False and "ZeroDivisionError" in got.reason
    db.set_setting(conn, veto.SETTING, "shadow")
    assert veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="x").proceed is True


# --- (ד) off: nothing but the switch is read -----------------------------------------------------------------------


def test_off_reads_only_the_switch_and_writes_nothing(conn, caplog, monkeypatch):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "off")
    caplog.set_level(logging.DEBUG, logger="imagesrv.brain")
    monkeypatch.setattr(veto.observe, "from_db", lambda *a, **k: pytest.fail("off must not observe"))
    got = veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="x")
    assert got == veto.Verdict(True, "off", False, "")
    assert brain_lines(caplog) == []


# --- (ב) the D21 hook in room._mark_lost ---------------------------------------------------------------------------


def test_D21_enforce_no_destruction_while_the_ledger_shows_work(conn):
    """I5/I7: הדיווח שתק 400 ש', אבל יומן החתיכות של המשכפל נכתב לפני 10 ש' -- זו עבודה, לא מוות."""
    member(conn, "aa", 400)
    chunk_ledger(conn, "aa", 10)
    write_ledger(conn, "aa")
    db.set_setting(conn, veto.SETTING, "enforce")
    mark_lost(conn)
    assert conn.execute("SELECT state FROM session_members WHERE mac = 'aa'").fetchone()[0] == "lost"
    assert kept(conn, "aa")
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'brain_veto'").fetchone()[0] == 1


def test_D21_shadow_deletes_as_today_and_says_it_would_have_blocked(conn, caplog):
    member(conn, "aa", 400)
    chunk_ledger(conn, "aa", 10)
    write_ledger(conn, "aa")
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    mark_lost(conn)
    assert not kept(conn, "aa")
    k2 = [x for x in brain_lines(caplog) if x["rule"] == "K2"]
    assert [(x["point"], x["action"], x["say"]) for x in k2] == [("D21", "EXPIRE_LEDGER", "הווטו היה חוסם")]


def test_D21_enforce_real_silence_is_deleted(conn):
    """I7: אין שום ראיה 400 ש' -- המחיקה מתבצעת גם ב-enforce. הווטו אינו "לא מוחקים לעולם"."""
    member(conn, "aa", 400)
    write_ledger(conn, "aa")
    db.set_setting(conn, veto.SETTING, "enforce")
    mark_lost(conn)
    assert not kept(conn, "aa")


def test_D21_I3_another_cloner_alive_does_not_hold_this_one(conn):
    """בידוד: bb חי וכותב, aa שותק באמת. המחיקה של aa אינה מוחזקת בגלל bb, ו-bb לא נוגעים בו."""
    member(conn, "aa", 400)
    member(conn, "bb", 2)
    chunk_ledger(conn, "bb", 1)
    write_ledger(conn, "aa")
    write_ledger(conn, "bb")
    db.set_setting(conn, veto.SETTING, "enforce")
    mark_lost(conn)
    assert not kept(conn, "aa") and kept(conn, "bb")
    assert conn.execute("SELECT state FROM session_members WHERE mac = 'bb'").fetchone()[0] == "writing"


def test_D21_off_is_exactly_as_before(conn, caplog):
    member(conn, "aa", 400)
    chunk_ledger(conn, "aa", 10)
    write_ledger(conn, "aa")
    db.set_setting(conn, veto.SETTING, "off")
    caplog.set_level(logging.DEBUG, logger="imagesrv.brain")
    mark_lost(conn)
    assert not kept(conn, "aa") and brain_lines(caplog) == []


# --- stage 2: the registry of holds ---------------------------------------------------------------------------------


def test_a_settled_hold_does_not_shorten_the_next_one(conn):
    """D19 can be held twice on one wave and partition (after a re-plan). Without `settle` the second hold would be
    counted from the first, and the ceiling would pass at once."""
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    t = [1000.0]
    ask = lambda: veto.check(conn, action="FAIL_WAVE", rule="D19", round_id=W, targets=["aa"], reason="x", part=3,
                             mono=lambda: t[0])
    assert ask().proceed is False
    t[0] += 1700
    veto.settle(conn, round_id=W, action="FAIL_WAVE", part=3)
    t[0] += 200                                   # 1900 s since the first hold, 200 since it ended
    assert ask().proceed is False                 # the second hold starts its own clock
    said = [r[0] for r in conn.execute("SELECT detail FROM journal WHERE event = 'brain_veto' ORDER BY id")]
    assert [d.split(" ")[:2] for d in said] == [["הווטו", "חסם"], ["הווטו", "שחרר"], ["הווטו", "חסם"]], said


def test_settle_touches_only_its_own_wave_action_and_partition(conn):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    for part in (3, 4):
        veto.check(conn, action="FAIL_WAVE", rule="D19", round_id=W, targets=["aa"], reason="x", part=part)
    veto.settle(conn, round_id=W, action="FAIL_WAVE", part=3)
    assert [k[:3] for k in veto._held] == [(W, "FAIL_WAVE", 4)]
    veto.settle(conn, round_id="other", action="FAIL_WAVE", part=4)
    veto.settle(conn, round_id=W, action="FAIL_DISKS", part=4)
    assert [k[:3] for k in veto._held] == [(W, "FAIL_WAVE", 4)]


def test_a_hold_nobody_asked_about_for_a_day_is_forgotten(conn):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    t = [1000.0]
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id="old-wave", targets=["aa"], reason="x", mono=lambda: t[0])
    t[0] += veto.STALE_HOLD_S + 1
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="x", mono=lambda: t[0])
    assert [k[0] for k in veto._held] == [W]
