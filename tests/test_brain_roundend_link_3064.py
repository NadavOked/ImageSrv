"""#3064, שני כללים במוח, בצל: K10 סוף סבב וכיבוי (#3276), ו-K3 אזהרת קישור ו-ETA לפני ההתחלה (#3277).

**K10.** משכפל "דלוק ומחכה ל'כבה'" (#2031) אינו עבודה פתוחה: כשכל מי שנשאר לגל מוחזק, המוח רושם ``ROUND_END/CLOSE``
בשם המשכפל, והתוצאה היא מה שהקוד הקיים עשה (הסבב נכשל אחרי 7 הערות WoL). ה-WoL של פתיחת גל שנשלח בתוך ספירת הכיבוי
(#1721) נרשם ``WAKE/DEFER``, והתוצאה היא מתי המשכפל הצטרף. **צל:** שום WoL לא נדחה והסבב לא נסגר.

**K3, קישור.** משכפל שכרטיס ההפצה שלו נמדד ב-100Mb/s (מכללה 11/11) מקבל אזהרה עם ETA חזוי ב-`precheck` של החדר; כשהגל
יוצא נרשמת ``LINK_SLOW`` (``EXCLUDE`` בצל כשיש משכפל מהיר יותר), ובסוף הגל ה-ETA בפועל מול החזוי.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server.agent import probe  # noqa: E402
from server.brain import memory, veto  # noqa: E402
from server.db import set_setting  # noqa: E402
from server.rounds import room as room_mod, room_precheck  # noqa: E402
from test_held_console_2031 import held_report  # noqa: E402
from test_room_rewake_1721 import CAP, STEP, _round, _wave1_partial  # noqa: E402
from test_server_room import (  # noqa: E402,F401 -- room_server הוא fixture
    CLONER1, CLONER2, cloner_hello, report, room, room_body, room_server,
)

try:                                     # בקרה שלילית: בבסיס אין אף אחד מהשניים
    from server.brain import linkwarn, roundend  # noqa: E402
except ImportError:                      # pragma: no cover
    linkwarn = roundend = None

#: הטסטים הטהורים של המודולים החדשים. בבקרה השלילית הם מדולגים (אין מה לייבא), והטסטים בשרת אמיתי הם שנכשלים.
pure = pytest.mark.skipif(roundend is None or linkwarn is None, reason="negative control: the new brain modules are absent")


@pytest.fixture(autouse=True)
def _fresh_brain():
    if roundend is not None:
        roundend.forget()
    veto.forget()
    yield
    if roundend is not None:
        roundend.forget()
    veto.forget()


def k(conn, rule: str, action: str | None = None) -> list[dict]:
    rows = memory.decisions(conn, rule=rule, limit=100)
    return [d for d in rows if action is None or d["action"] == action]


def one(conn, rule: str, action: str) -> dict:
    rows = k(conn, rule, action)
    assert len(rows) == 1, f"{rule}/{action}: {len(rows)} החלטות במקום אחת -- {rows}"
    return rows[0]


def C(mac="aa", name="משכפל 1", fresh=True, held=False, joined=False, gap=None):
    return roundend.Cloner(mac=mac, name=name, fresh=fresh, held=held, joined=joined, done_gap_s=gap)


# --- K10, טהור -----------------------------------------------------------------------------------------------------


@pure
def test_k10_only_held_cloners_left_means_no_open_work_and_names_them():
    found = roundend.judge([C(held=True), C(mac="bb", name="משכפל 2", fresh=False)], end_s=20)
    assert [(f.action, f.verdict) for f in found] == [("ROUND_END", "CLOSE")]
    assert "משכפל 1" in found[0].reason and "'כבה'" in found[0].reason


@pure
@pytest.mark.parametrize("specs", [      # (kwargs לכל משכפל) -- נבנים בטסט, לא באיסוף (בבקרה השלילית אין `roundend`)
    [{"held": True}, {"mac": "bb", "name": "משכפל 2"}],                  # משכפל אחר עוד יכול להצטרף ולכתוב
    [{"held": True}, {"mac": "bb", "name": "משכפל 2", "joined": True}],  # ומשכפל שכבר הצטרף עם מגירה לכתוב
    [{"held": True, "fresh": False}],                                     # למוחזק אין מה לכתוב: לא שלו לסגור
], ids=["another-may-join", "another-joined", "held-has-nothing"])
def test_k10_open_work_keeps_the_round(specs):
    cloners = [C(**spec) for spec in specs]
    assert not [f for f in roundend.judge(cloners, end_s=20) if f.action == "ROUND_END"]


@pure
@pytest.mark.parametrize("gap, defer", [(0.0, True), (19.0, True), (20.0, False), (-3.0, False), (None, False)])
def test_k10_a_wol_inside_the_countdown_is_deferred(gap, defer):
    found = roundend.judge([C(gap=gap)], end_s=20)
    assert [(f.action, f.verdict, f.target) for f in found] == ([("WAKE", "DEFER", "aa")] if defer else [])
    if defer:
        assert "משכפל 1" in found[0].reason


@pure
def test_k10_says_nothing_about_a_cloner_that_joined_or_holds():
    assert roundend.judge([C(gap=1.0, joined=True), C(mac="bb", gap=1.0, held=True, fresh=False)], end_s=20) == []


# --- K3 קישור, טהור ------------------------------------------------------------------------------------------------


def nic(mbps, source="reported"):
    return {"speed_mbps": mbps, "source": source, "why": None}


@pure
def test_link_the_college_link_warns_with_an_eta_of_about_three_hours():
    """82.6GB ב-100Mb/s: 3:10 שעות בפועל (מכללה 11/11, #2841). ה-ETA החזוי -- בתוך 5 דקות מזה."""
    [f] = linkwarn.judge({"aa": nic(100)}, {"aa": "משכפל 1"}, 82_600_000_000)
    assert (f.verdict, f.mbps) == ("WARN", 100)
    assert abs(f.eta_s - (3 * 3600 + 10 * 60)) < 5 * 60, f.eta_s
    assert "100Mb/s" in linkwarn.text_he(f) and "ETA" in linkwarn.text_he(f)


@pure
def test_link_exclusion_is_proposed_only_when_a_faster_cloner_is_measured():
    found = linkwarn.judge({"aa": nic(100), "bb": nic(1000), "cc": nic(1000, "assumed")},
                           {"aa": "משכפל 1", "bb": "משכפל 2", "cc": "משכפל 3"}, 10**9)
    assert [(f.mac, f.verdict, f.faster) for f in found] == [("aa", "EXCLUDE", ("משכפל 2",))]


@pure
@pytest.mark.parametrize("view", [nic(1000), nic(2500, "manual"), nic(100, "assumed"), {"speed_mbps": None}],
                         ids=["gigabit", "manual", "assumed-is-not-measured", "unread"])
def test_link_no_warning_without_a_measured_slow_link(view):
    assert linkwarn.judge({"aa": view}, {}, 10**9) == []


@pure
def test_link_unknown_image_size_is_an_unknown_eta_not_zero():
    [f] = linkwarn.judge({"aa": nic(10)}, {}, None)
    assert f.eta_s is None and "לא ידוע" in linkwarn.text_he(f)


def test_the_precheck_note_carries_the_link_warning_on_every_drawer_of_the_cloner():
    machine = {"mac": "aa", "name": "shich-1", "joined": True, "slot_names": {},
               "nic_warn": {"mbps": 100, "eta_s": 600, "text_he": "כרטיס הרשת של המשכפל מקושר ב-100Mb/s"},
               "drawer_list": [{"fresh": True, "selected": True, "port": p, "serial": f"S{p}", "link_state": "ok",
                                "link_gbps": 6.0, "link_max_gbps": 6.0, "crc_rise": None} for p in (1, 2)]}
    out = room_precheck.warnings([machine], selection=True)
    assert [(w["port"], w.get("nic_mbps"), w.get("eta_s")) for w in out] == [(1, 100, 600), (2, 100, 600)]
    assert all("100Mb/s" in w["text_he"] for w in out)
    del machine["nic_warn"]
    assert room_precheck.warnings([machine], selection=True) == []


# --- K10 בשרת אמיתי (#1721, #2031) --------------------------------------------------------------------------------


def test_k10_records_the_swallowed_wol_of_the_new_wave_and_when_the_cloner_joined(room_server, monkeypatch):
    """#1721: גל 2 נפתח באותה שנייה שבה משכפל 1 דיווח partial ונכנס לספירה -- ה-WoL שלו נבלע. בצל: נשלח כרגיל."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    clock, wave2 = _wave1_partial(room_server, monkeypatch)
    room_mod.sweep(ctx.conn, ctx.store)
    room_mod.sweep(ctx.conn, ctx.store)
    d = one(ctx.conn, "K10", "WAKE")
    assert (d["verdict"], d["entity_key"], d["round_id"], d["mode"]) == ("DEFER", CLONER1, wave2, "shadow")
    assert "shich-1" in d["reason"] and d["outcome_state"] == "open"
    assert any(e.startswith("done_gap_s=") for e in d["evidence"])

    cloner_hello(anon, CLONER1, ["S1", "S2"])                        # חזר והצטרף לגל 2
    room_mod.sweep(ctx.conn, ctx.store)
    d = one(ctx.conn, "K10", "WAKE")
    assert d["outcome_state"] == "closed" and "הצטרף" in d["outcome"], d


def test_k10_a_held_cloner_is_no_open_work_and_the_outcome_is_what_the_old_code_did(room_server, monkeypatch):
    """#2031: משכפל 1 מחזיק את התוצאות. המוח: "היה סוגר עכשיו, בשמו". הקוד הקיים: 7 הערות WoL, ואז נכשל."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    clock, wave2 = _wave1_partial(room_server, monkeypatch)
    held_report(anon)
    room_mod.sweep(ctx.conn, ctx.store)
    d = one(ctx.conn, "K10", "ROUND_END")
    assert (d["verdict"], d["round_id"], d["entity_kind"]) == ("CLOSE", wave2, "round")
    assert "shich-1" in d["reason"] and "'כבה'" in d["reason"] and d["outcome_state"] == "open"
    assert _round(ctx)["state"] == "active", "צל: המוח סגר את הסבב"

    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        held_report(anon)
        room_mod.sweep(ctx.conn, ctx.store)
    assert _round(ctx)["state"] == "failed"
    d = one(ctx.conn, "K10", "ROUND_END")
    assert d["outcome_state"] == "closed" and "נכשל" in d["outcome"], d
    assert len(k(ctx.conn, "K10", "ROUND_END")) == 1, "החלטה אחת לגל, לא אחת לכל דופק"


def test_k10_off_records_nothing(room_server, monkeypatch):
    ctx, anon = room_server["ctx"], room_server["anon"]
    set_setting(ctx.conn, veto.SETTING, "off")
    _wave1_partial(room_server, monkeypatch)
    held_report(anon)
    room_mod.sweep(ctx.conn, ctx.store)
    assert k(ctx.conn, "K10") == []


@pure
def test_k10_a_brain_that_throws_does_not_stop_the_round(room_server, monkeypatch):
    ctx = room_server["ctx"]
    monkeypatch.setattr(roundend, "judge", lambda *a, **kw: 1 / 0)
    clock, _ = _wave1_partial(room_server, monkeypatch)
    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        room_mod.sweep(ctx.conn, ctx.store)
    assert _round(ctx)["state"] == "failed", "כשל במוח עצר את הדופק"


# --- K3 קישור בשרת אמיתי -----------------------------------------------------------------------------------------


def _slow_link(conn, mac: str, mbps: int = 100) -> None:
    probe.record(conn, mac, {"nic": [{"name": "enp1s0", "speed_mbps": mbps, "ip_conflict": False}]})


def test_link_the_room_warns_before_start_and_the_brain_compares_the_eta_at_the_end(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _slow_link(ctx.conn, CLONER1)
    _slow_link(ctx.conn, CLONER2, 1000)
    opened = deploy.post("/api/console/room", json=room_body(room_server, 3, {CLONER1: ["S1", "S2"], CLONER2: ["T1"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]

    pre = room(deploy)["round"]["precheck"]
    mine = [w for w in pre if w["mac"] == CLONER1]
    assert len(mine) == 2 and all(w.get("nic_mbps") == 100 and w.get("eta_s") for w in mine), pre
    assert all("100Mb/s" in w["text_he"] and "ETA" in w["text_he"] for w in mine)
    assert not [w for w in pre if w["mac"] == CLONER2], "גיגה אינו קישור איטי"

    cloner_hello(anon, CLONER2, ["T1"])
    room_mod.sweep(ctx.conn, ctx.store)                              # הגל יוצא לבד: שלוש מגירות מוכנות
    assert room(deploy)["round"]["wave_state"] == "running"
    d = one(ctx.conn, "K3", "LINK_SLOW")
    assert (d["verdict"], d["entity_key"], d["round_id"], d["mode"]) == ("EXCLUDE", CLONER1, wave, "shadow")
    assert "צל" in d["reason"] and "shich-2" in d["reason"]
    assert any(e.startswith("eta_s=") and e != "eta_s=None" for e in d["evidence"]), d["evidence"]
    warned = ctx.conn.execute("SELECT detail FROM journal WHERE event = 'room_precheck_warned'").fetchone()
    assert warned is not None and "nic=100" in warned["detail"], "התחלה עם האזהרה לא נרשמה ביומן"
    members = {m["mac"] for m in ctx.store.members(wave)}
    assert members == {CLONER1, CLONER2}, "צל: המשכפל האיטי הוצא מהשידור"

    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})
    report(anon, wave, CLONER2, {"sda": "done"})
    room_mod.sweep(ctx.conn, ctx.store)
    d = one(ctx.conn, "K3", "LINK_SLOW")
    assert d["outcome_state"] == "closed" and "ETA חזוי" in d["outcome"] and "בפועל" in d["outcome"], d


def test_link_off_shows_and_records_nothing(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    set_setting(ctx.conn, veto.SETTING, "off")
    _slow_link(ctx.conn, CLONER1)
    deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert not [w for w in room(deploy)["round"]["precheck"] if w.get("nic_mbps")]
    room_mod.sweep(ctx.conn, ctx.store)
    assert k(ctx.conn, "K3") == []


@pure
def test_link_finished_without_a_start_time_says_not_measured(tmp_path):
    from server import db
    conn = db.connect(tmp_path / "x.db")
    memory.record(conn, mode="shadow", rule="K3", action="LINK_SLOW", verdict="WARN", reason="r",
                  evidence=("eta_s=600",), round_id="ses_1", entity=("network", "aa"))
    linkwarn.finished(conn, "ses_1", None, now=datetime.now(timezone.utc))
    [d] = memory.decisions(conn, rule="K3")
    assert "לא נמדד" in d["outcome"] and "10 דק'" in d["outcome"], d["outcome"]
